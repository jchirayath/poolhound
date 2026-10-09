#!/usr/bin/env python3
"""A loopback-only server so the Chemicals and Settings tabs can write.

WHY THERE IS A SERVER AT ALL
  The rest of poolhound is generated files. Reading needs no process. But
  logging a dose and changing the pool volume are writes, and the honest choice
  was a form that saves or no form at all — a Save button that quietly discards
  input is worse than a documented command line.

  So this exists, and it is kept as small as the job allows: three POSTs, a
  health check, and static files. No framework, no database, no dependency.

WHY LOOPBACK IS NOT, BY ITSELF, A DEFENCE
  It is tempting to reason that a socket on 127.0.0.1 is unreachable and
  therefore safe. That is true of the network and false of the browser. Any page
  open in any tab can issue requests to 127.0.0.1, and they leave from inside
  this machine — so an unprotected loopback API is writable by every website
  visited while it is running. Proved before it was fixed: a POST carrying
  `Origin: https://evil.example.com` wrote a fabricated dose into the record.

  Three checks close it, and they are layered because each covers what the
  others miss:

  HOST. The Host header must name a loopback address. Without this, an attacker
  who controls a domain can point it at 127.0.0.1 (DNS rebinding) and reach this
  server as a same-origin peer, at which point the origin check has nothing to
  say.

  ORIGIN. A cross-site request that carries an Origin at all must carry one of
  ours. Requests with no Origin — curl, a script, the collectors — are allowed,
  because they were never a browser to begin with.

  TOKEN. Writes require a header carrying a secret minted at startup and handed
  out only by /api/health. A cross-origin page cannot read that response (no
  CORS headers are sent, so the browser withholds the body) and cannot set a
  custom header on a simple form post without a preflight this server refuses.
  This is the check that survives a browser sending no Origin at all.

  The bind address is still not configurable, because the only reason to change
  it would be to expose a write API to the LAN.

  Accept a field it does not recognise. Settings writes touch a fixed list of
  keys in a fixed list of sections; anything else is refused rather than merged.
  A config file is a thing a running system reads, not a scratch pad for
  whatever the browser posts.

WHY IT EDITS config.toml RATHER THAN REWRITING IT
  That file is mostly comments explaining why each value is what it is. There is
  no TOML writer in the standard library, and generating one from a dict would
  throw the comments away — turning a documented file into a bare list of values
  the first time anyone saved from the browser. So a save rewrites the value on
  a matching key line in the right section, in place, and leaves everything else
  exactly as it was.
"""
import collections, csv, datetime as dt, hmac, http.server, json, math, os, posixpath, re, secrets
import socketserver, sys, threading, time, tomllib, traceback, urllib.parse

from . import chemicals as CH
from . import config
from . import pool_shape
from . import vault
from . import access
from . import commands as CMD
from .queue_ import QUEUE
from . import locking
from . import audit
from . import aqualink
from . import headers as HDR
from . import switchlog

# Loopback by default, and the only way to change it is to say so explicitly.
#
# In a container "loopback" would mean "reachable by nothing", including the
# reverse proxy, which is a separate container on a bridge network. So the bind
# address is openable — but ONLY to 0.0.0.0 inside a container whose port is
# `expose`d rather than published, which puts it on the docker bridge and
# nowhere else. That is the same exposure as loopback on a single host: the
# packet still has to come through Caddy.
#
# It is an environment variable and not a config key so that it is visible in
# `docker inspect` next to the port list, where the two facts that have to agree
# — "binds 0.0.0.0" and "is not published" — can be read together.
HOST = os.environ.get("POOLHOUND_BIND", "127.0.0.1").strip() or "127.0.0.1"
if HOST not in ("127.0.0.1", "::1", "0.0.0.0"):
    sys.exit(f"POOLHOUND_BIND={HOST!r} refused: loopback or 0.0.0.0 only")
PORT = int(os.environ.get("POOLHOUND_PORT", "8787"))

# Minted per process. Not persisted: a token that outlives the process would
# have to be stored somewhere, and there is nothing here worth the risk of
# leaving a credential on disk.
TOKEN = secrets.token_urlsafe(32)
TOKEN_HEADER = "X-Poolhound-Token"

ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"[::1]:{PORT}",
                 "127.0.0.1", "localhost"}
ALLOWED_ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}",
                   f"http://[::1]:{PORT}"}

# On the server the process still binds loopback; a reverse proxy in front terminates
# TLS and enforces Entra. Adding the public name here lets the Host and Origin
# checks keep working through that proxy without ever binding publicly — the
# rule that this never listens on a routable address is not relaxed, only the
# names it will answer to.
# Comma-separated, because the site answers to more than one name: an alias that
# is not listed here fails the Host check and every write from it is refused,
# which presents as "the site works but nothing saves" — a confusing way to
# discover a missing entry.
PUBLIC_HOSTS = [h.strip() for h in
                os.environ.get("POOLHOUND_PUBLIC_HOST", "").split(",") if h.strip()]
PUBLIC_HOST = PUBLIC_HOSTS[0] if PUBLIC_HOSTS else ""
for _h in PUBLIC_HOSTS:
    ALLOWED_HOSTS |= {_h, f"{_h}:443", f"{_h}:80"}
    ALLOWED_ORIGINS |= {f"https://{_h}", f"http://{_h}"}

# Headers a reverse proxy sets once it has authenticated somebody. Read in this
# order; the first that is present wins.
#
# WHAT ACTUALLY MAKES THESE TRUSTWORTHY, stated plainly because the old sentence
# here ("nothing reaches this process except through that proxy — which is
# exactly why the bind address stays on loopback") stopped describing the
# deployment and nobody noticed. On the server this process binds 0.0.0.0 inside a
# container on a shared docker bridge, merged into the server's compose file. The
# port is `expose`d and never published, so nothing off the host reaches it —
# but every OTHER container on that bridge can open poolhound:8787 directly, and
# the `request_header -X-Forwarded-Email` lines that delete a client-supplied
# identity live on the poolhound.example.com vhost in Caddy, which a sibling
# container does not go through.
#
# So the honest statement is: these headers are trusted because of DOCKER
# NETWORK MEMBERSHIP plus Caddy stripping them on the one path from outside.
# Anything that can already open a socket to this container can claim to be
# anybody. That is a smaller blast radius than it sounds — it needs code
# execution in a sibling container on the same host — but it is not the same
# claim as "loopback", and writing the weaker claim down is the only way the
# next person can decide whether it is still good enough.
# ORDER IS PRECEDENCE: whoami() takes the first of these that has a value, so
# the email-shaped headers come first and the username is the fallback.
#
# X-Auth-Request-User WAS COPIED BY THE PROXY AND READ BY NOBODY. The Caddy
# vhost strips it on the way in (so the client cannot forge it) and then
# re-adds it from oauth2-proxy in both forward_auth blocks -- and it was not in
# this tuple, so it crossed the wire on every authenticated request and was
# dropped on the floor. Harmless here, because X-Auth-Request-Email is copied
# alongside it and is read; not harmless for the installs the README invites,
# since GitHub and some Authelia configurations issue no email claim at all and
# the only identity on offer is the username. Those installs saw "anonymous"
# with every header present.
#
# Last in the list, so nothing changes for an install that has an email.
# PROVING THE REQUEST CAME THROUGH THE PROXY, rather than inferring it from
# network position.
#
# The paragraph above states the weakness honestly and then leaves it standing:
# identity is trusted because of docker network membership, and every sibling
# container on that bridge can open poolhound:8787 and claim to be anybody.
# Measured on the author's stack: eleven siblings, and neither compose file
# declares a `networks:` key, so they all share the default one.
#
# A shared secret turns "you are on the right network" into "you hold the thing
# only Caddy holds". Caddy sets this header AFTER the strip block (so a client
# cannot supply it) and poolhound compares it before reading any identity.
#
# OPT-IN, AND FOR THE SAME REASON access.py's policy is: an upgrade that
# started refusing every header would lock the owner out of their own pool.
# Unset, behaviour is exactly what it was and /api/health reports the boundary
# as unproven so the gap is visible rather than assumed closed. Set it, and a
# sibling container gets 'anonymous'.
PROXY_AUTH_HEADER = "X-Poolhound-Proxy-Auth"
PROXY_SECRET = os.environ.get("POOLHOUND_PROXY_SECRET", "").strip()

IDENTITY_HEADERS = ("X-MS-CLIENT-PRINCIPAL-NAME",   # Azure App Service Easy Auth
                    "X-Forwarded-Email",            # oauth2-proxy
                    "X-Forwarded-User",
                    "X-Auth-Request-Email",
                    "X-Auth-Request-User")          # no email claim (GitHub, some Authelia)

# AGENT_TOKEN_FILE USED TO BE DECLARED HERE, and the lookup with it — see
# vault.TOKEN_FILE. It was an absolute path POOLHOUND_VAULT does not move,
# consulted BEFORE vault.agent_token(), so it was a route to the household's
# shared secret that sat outside the isolation boundary while every assertion
# about that boundary passed. One function owns every route to the token now.

# Routes that anybody may POST to, signed in or not.
#
# The volume calculator is useful to any pool owner and knows nothing about this
# one: it takes an outline and returns a number. Both of these are PURE — they
# read nothing, write nothing and remember nothing, so there is no state for an
# anonymous caller to reach. /api/pool-shape (no suffix) is deliberately NOT
# here: that one saves the answer as this pool's volume, which is a write and
# stays behind the gate.
#
# They still cost CPU and bandwidth — a 60 MB upload and an image decode — so
# they carry their own tighter rate limit rather than sharing the write budget.
PUBLIC_POST = {"/api/pool-shape/compute", "/api/photo"}

# How many uploaded photos may be DECODING at once. Two, because each one holds
# its bytes plus a decoded bitmap, and the container has 512 MB for everything.
# The public rate limit below bounds requests per minute and bounds nothing
# about concurrency: it would happily admit thirty simultaneous decodes.
_PHOTO_SLOTS = threading.Semaphore(2)
PUBLIC_RATE, PUBLIC_WINDOW_S = 30, 60
_public_hits = collections.deque()

# Both limiters read a deque, decide, and then append. In a threaded server that
# gap lets roughly one extra request per concurrent thread through -- not
# exploitable into anything worse, since the buckets are small and the work
# behind them is bounded, but a limit that is documented as 30 and is actually
# "30 plus however many threads happen to be in here" is a limit that does not
# mean what it says. One lock covers both; they are microseconds apart.
_rate_lock = threading.Lock()

def public_rate_ok():
    with _rate_lock:
        now = time.time()
        while _public_hits and now - _public_hits[0] > PUBLIC_WINDOW_S:
            _public_hits.popleft()
        if len(_public_hits) >= PUBLIC_RATE:
            return False
        _public_hits.append(now)
        return True


# The last reason the token could not be looked up, for the operator. Not a
# cache of the token and never the token itself: a bare 403 on the agent routes
# is indistinguishable from "the Pi is unplugged", and the reason lives in the
# one place nobody watching the page can see — the server's stderr.
_agent_token_error = [None]

# WHEN THE AGENT LAST AUTHENTICATED WITH THE OUTGOING TOKEN. 0.0 means it is on
# the current one. A rotation is a sequence of steps a person takes across two
# machines, and the step people do not do is the last one — so the state "the
# server has a new token and the Pi is still presenting the old" is reported by
# /api/health rather than left to be remembered. Process-local on purpose: it
# is a claim about the connection this process is serving.
_on_previous_token = [0.0]

def agent_token():
    """The shared secret the Pi presents, or None.

    Every route to it — file, Key Vault, keychain — is inside
    vault.agent_token() now, in that order, so the isolation guard covers all
    three. This layer exists only to turn "could not ask" into something a
    person can read, rather than the silence it used to be: the `except
    Exception: return None` here was what let a Key Vault outage read as an
    unconfigured server, close every agent route and take the Pi off the air
    with nothing anywhere saying why.
    """
    try:
        tok = vault.agent_token(create=False)
    except vault.VaultError as e:
        if _agent_token_error[0] != str(e):
            print(f"server: the agent token could not be looked up: {e}",
                  file=sys.stderr, flush=True)
        _agent_token_error[0] = str(e)
        return None
    _agent_token_error[0] = None
    return tok

# Writes are cheap but not free — each one re-renders the page. A stuck client
# retrying in a loop should be slowed down rather than allowed to spin the CPU.
HEARTBEAT_S = 20
# DETECTION COSTS TWO HEARTBEATS, NOT ONE, and the interval is set from that.
#
# A socket whose peer has gone accepts the first write into the kernel buffer
# and raises only once the RST has come back — which is after the next write.
# So a dead agent is noticed on the SECOND heartbeat, and at 45 seconds that
# measured 90 seconds exactly. For all of it /api/health reported the Pi
# connected and /api/control accepted commands, painting them amber and waiting
# for a queue nobody was draining.
#
# Writing the heartbeat twice in one cycle does NOT help: both land in the
# buffer before the reset arrives. Only separation in time does. 20 seconds
# gives a worst case near 40, costs seven bytes a cycle, and is what the
# docstring below can honestly call "seconds".

RATE_LIMIT, RATE_WINDOW = 40, 60.0
_writes = collections.deque()

def rate_ok():
    with _rate_lock:
        now = time.monotonic()
        while _writes and now - _writes[0] > RATE_WINDOW:
            _writes.popleft()
        if len(_writes) >= RATE_LIMIT:
            return False
        _writes.append(now)
        return True

def _s(v):
    """Whatever arrived, as text. JSON is not a form.

    `(data.get("chemical") or "").strip()` reads as careful and is not: it
    handles None and handles nothing else, because JSON carries numbers,
    booleans, lists and objects where a form carries only strings. A body of
    {"chemical": 5} reached `.strip()` on an int and raised AttributeError out
    of the handler — measured across a sweep of type-confusion probes, 11 of 14
    closed the connection with no HTTP status at all.

    So every untrusted field is read through this, and a wrong TYPE becomes the
    ordinary wrong-VALUE refusal the route already knows how to give: 5 becomes
    "5" and comes back as "unknown chemical '5'", which is both true and
    actionable. Lists and objects stringify into something no validator accepts,
    which is the correct outcome for them too.
    """
    return "" if v is None else str(v)

def _num(v):
    """A JSON number, or None. `true` is not 1 here.

    The sibling of _s() and the same argument: float(True) is 1.0, so a body
    carrying a boolean where an amount belongs logged one fluid ounce of acid
    rather than being refused. A measurement or a dose is a number somebody
    wrote down; a boolean never was one.
    """
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _bool(v):
    """Checkboxes arrive as true/false, "true"/"false", or "on"/absent."""
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")

def _addr(v, allow_blank=False):
    v = (v or "").strip()
    if not v:
        return "" if allow_blank else None
    # Not a full RFC check — just enough that a typo does not become a config
    # value that fails silently at the moment an alarm needs to go out.
    return v if re.fullmatch(r"[^@\s]{1,64}@[A-Za-z0-9._-]{1,253}\.[A-Za-z]{2,24}", v) else None

def _addrs(v):
    """One or more addresses, comma- or space-separated.

    Every address must be valid or the whole field is rejected. Dropping the bad
    one and keeping the rest would be friendlier right up to the moment an alarm
    goes to two people instead of three and nobody knows why — a typo in a
    notification list has to be visible at the moment it is typed, because its
    consequence only shows up when something has already gone wrong.
    """
    v = (v or "").strip()
    if not v:
        return ""
    parts = [p for p in re.split(r"[,;\s]+", v) if p]
    out, seen = [], set()
    for p in parts:
        if not _addr(p):
            return None
        if p.lower() not in seen:
            seen.add(p.lower()); out.append(p)
    return ", ".join(out)

# Every key a save is allowed to touch: (section, key, coercion).
def _cadence_hours(tool, v):
    """An interval in hours, refused outside the bounds its source declares.

    REFUSED, NOT CLAMPED. volume_gallons was clamped once -- max(100, ...) --
    so a typed 0 was written as 100 and reported as saved, which is a save that
    returns a different number from the one submitted. A cadence is the same
    shape of value: somebody would set "every hour", be told it saved, and
    believe the product was doing it.
    """
    from . import cadence
    src = cadence.SOURCES.get(tool)
    if src is None:
        return None
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    return n if src["floor_hours"] <= n <= cadence.CEILING_HOURS else None


def _cadence_hour(v):
    """An hour of the day, 0-23, in UTC. Blank clears it back to the default."""
    if str(v).strip() == "":
        return CLEAR
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= 23 else None


def record_panel_change(body, cfg):
    """One audit row for a panel change the agent SAW, rather than issued.

    WHY THIS ROUTE EXISTS. poolhound's audit log recorded only what poolhound
    itself did, and there are six doors to that panel -- AqualinkD's UI, the
    optional poolhound local page on the Pi, aquapda_sim, aqmanager, the wall remote, the keypad
    and the panel's own schedules. So on 2026-09-30 the pump ran a second block
    from 21:00 past midnight, free chlorine rose a full point, and the one
    place that should have explained it was empty.

    THE DOOR IS A CLAIM AND THE CHANGE IS NOT. The change was observed on the
    panel, which is the authority. The door comes from an unauthenticated page
    on the house LAN, so it is allowlisted in commands.DOORS and written as
    `door:<name>` -- never as an identity, because a name nothing verified in an
    append-only log is worse than no name at all.
    """
    changed = body.get("changed")
    if not isinstance(changed, list):
        return None, "changed must be a list of column names"
    # AGAINST THE COLUMNS THAT EXIST, so a caller cannot write arbitrary text
    # into a permanent record. Same shape as the ack that accepted any id and
    # wrote it to audit.csv forever.
    cols = [c for c in changed if isinstance(c, str) and c in aqualink.COLS]
    if not cols:
        return None, "no recognised column in `changed`"
    door = CMD.door_label(body.get("door")) if body.get("door") else None
    audit.record("panel.change", ",".join(sorted(cols)),
                 detail=("seen on the panel"
                         + (f", claimed by {door}" if door else
                            " — no door said it was theirs")),
                 by=door or "panel", cfg=cfg)
    return {"recorded": sorted(cols), "door": door or ""}, None


SETTABLE = {
    # Refused outside the range, not clamped into it. max(100.0, float(v)) meant
    # a typed 0 or a pasted -5000 was WRITTEN AS 100 and reported as saved: the
    # save returned a different number from the one submitted, for the single
    # value every dose figure multiplies through. Every other numeric key in
    # this table already refuses; this one rewrote the owner's input.
    # And blank clears it — "I do not know" has to be expressible for the value
    # whose whole docstring argues that not-set is a state.
    "volume_gallons":   ("pool", "volume_gallons", lambda v: _bounded(v, 100, 500000)),
    "volume_estimated": ("pool", "volume_estimated", lambda v: bool(v) and v != "false"),
    "sanitiser":        ("pool", "sanitiser",
                         lambda v: v if v in ("salt_cell", "liquid", "tablet") else None),
    "acid_pct":         ("pool", "acid_pct", lambda v: float(v) if 0 < float(v) <= 100 else None),
    "spa_shares_water": ("pool", "spa_shares_water", _bool),
    # The pump's RATED draw, off its label -- not a measurement. Blank clears it,
    # because "I do not know" has to be expressible: the alternative is that a
    # number typed once can never be withdrawn and every later energy figure
    # rests on it.
    # The assistant's endpoint. The KEY is not here and must never be: it goes
    # through /api/credential into the vault, like every other secret. These
    # two are an address and a model name, which are settings and not secrets.
    # THE ASSISTANT'S ENDPOINT IS WHERE THIS POOL'S READINGS AND THE API KEY
    # BOTH GO, so it is the one setting whose shape is worth insisting on. An
    # arbitrary endpoint is deliberate — "a model on your own network is a
    # base_url change and nothing leaves at all" — but an arbitrary STRING is
    # not the same freedom: it was accepting anything that survived .strip(),
    # including text with no scheme and nothing resembling a host.
    "ai_base_url":      ("ai", "base_url", lambda v: _url(v)),
    "ai_model":         ("ai", "model", lambda v: _token(v, 128)),
    "pump_watts":       ("pool", "pump_watts",
                         lambda v: CLEAR if str(v).strip() == "" else
                         (float(v) if 0 < float(v) <= 10000 else None)),
    "kwh_cost":         ("power", "kwh_cost",
                         lambda v: CLEAR if str(v).strip() == "" else
                         (float(v) if 0 < float(v) < 100 else None)),
    # What this household pays per gallon or pound, for the upkeep table's cost
    # column. Blank clears, and a cleared price falls back to the labelled
    # retail reference rather than to nothing -- the page says which it used.
    # The setting names are config.PRICE_SETTINGS's, so the reader and this
    # writer cannot spell them differently.
    **{name: ("prices", name,
              lambda v: CLEAR if str(v).strip() == "" else
              (float(v) if 0 < float(v) < 1000 else None))
       for name in config.PRICE_SETTINGS.values()},
    # BLANK CLEARS THESE. "This host does not poll the controller, the Pi pushes
    # to it" is the true statement on the server, and the only way to say it is to
    # empty the box -- which was refused as "invalid: aqualink_host" and, because
    # a save is all-or-nothing and the browser posts the whole form, took four
    # unrelated legitimate edits down with it.

    # HOW OFTEN EACH LAB IS PULLED. The only setting in this table that spends
    # somebody else's quota, so it is the only one whose bound is a promise
    # rather than a sanity check: WaterGuru asks for no more than two calls a
    # day, and twelve hours is the shortest interval that honours the second.
    #
    # REFUSED HERE AND CLAMPED THERE, on purpose. This is the writing path and
    # it says no; cadence.every_hours() is the reading path and it clamps,
    # because a config.toml edited by hand on the share never came through this
    # endpoint at all. The floor lives in cadence.SOURCES either way -- one
    # number, read by both, rather than a dropdown that agrees with a validator
    # until somebody edits one of them.
    "wg_every_hours":  ("collection", "waterguru_every_hours",
                        lambda v: _cadence_hours("wg-collect", v)),
    "wg_hour_utc":     ("collection", "waterguru_hour_utc", _cadence_hour),
    "le_every_hours":  ("collection", "leslies_every_hours",
                        lambda v: _cadence_hours("leslies", v)),
    "le_hour_utc":     ("collection", "leslies_hour_utc", _cadence_hour),

    # Notifications. Every one of these is a switch or an address; the SMTP
    # password is deliberately absent, because it lives in its own file and this
    # endpoint has no business accepting one.
    "n_desktop":     ("notify", "desktop",    _bool),
    "n_email":       ("notify", "email",      _bool),
    "n_collectors":  ("notify", "collectors", _bool),
    "n_chemistry":   ("notify", "chemistry",  _bool),
    "n_pool_heat":   ("notify", "pool_heat",  _bool),
    "n_spa_heat":    ("notify", "spa_heat",   _bool),
    "n_spa":         ("notify", "spa",        _bool),
    "n_freeze":      ("notify", "freeze",     _bool),
    "n_email_to":    ("notify", "email_to",   _addrs),
    "n_email_from":  ("notify", "email_from", lambda v: _addr(v, allow_blank=True)),
    # Blank clears, for the same reason: email is off by default, and an install
    # that has turned it off again has to be able to say so.
    "n_smtp_host":   ("notify", "smtp_host",  lambda v: _host(v)),
    "n_smtp_port":   ("notify", "smtp_port",  lambda v: _port(v)),
    "n_smtp_tls":    ("notify", "smtp_tls",   _bool),
    "n_smtp_credentials": ("notify", "smtp_credentials",
                           lambda v: CLEAR if str(v or "").strip() == "" else
                           (v if re.fullmatch(r"[~/][\w./~-]{0,255}", v) else None)),
    "n_email_min_severity": ("notify", "email_min_severity",
                             lambda v: v if v in ("info", "serious", "critical") else None),
}

def _host(v):
    """A hostname or IP, CLEAR for blank, None meaning refuse."""
    s = str(v or "").strip()
    if s == "":
        return CLEAR
    return s if re.fullmatch(r"[A-Za-z0-9._-]{1,253}", s) else None


def _port(v):
    """A TCP port, CLEAR for blank, None meaning refuse."""
    s = str(v or "").strip()
    if s == "":
        return CLEAR
    try:
        n = int(s)
    except ValueError:
        return None
    return n if 1 <= n <= 65535 else None


# WHAT WOULD HAVE BEEN ACCEPTED, in the voice the reading form already uses.
#
# Every Settings refusal was the literal string "invalid: " plus the field name.
# Two forms on the same tab held opposite standards: Record-a-reading refuses
# with "ta of 730 is outside 0-400, which is not a reading a pool test produces
# — check the decimal point", and Settings said "invalid: n_smtp_port" and
# stopped. The owner was told a field was wrong and nothing about what would be
# right — and because a save is all-or-nothing, the rest of their edits went
# with it, so they could not even find the offending one by elimination.
WHY = {
    "volume_gallons": "the pool volume must be between 100 and 500,000 gallons "
                      "(leave it empty if you do not know it)",
    "sanitiser": "the sanitiser must be one of salt_cell, liquid or tablet, and "
                 "cannot be left empty — the targets depend on it",
    "acid_pct": "the acid strength must be a percentage above 0 and at most 100 "
                "(muriatic ships at 31.45 and at 14.5), and cannot be left empty "
                "— every dose figure is computed through it",
    "pump_watts": "the pump's rated draw must be between 1 and 10,000 watts "
                  "(leave it empty if you do not know it)",
    "kwh_cost": "the electricity price must be above 0 and under 100 per kWh "
                "(leave it empty if you do not know it)",
    **{name: "a chemical price must be above 0 and under 1,000 per gallon or "
             "pound (leave it empty to use the typical retail price)"
       for name in config.PRICE_SETTINGS.values()},
    "n_email_to": "every address in the alert list must be a real address — one "
                  "typo is refused rather than silently dropped, because an "
                  "alarm going to two people instead of three shows up only "
                  "once something has already gone wrong",
    # "leave it empty" was only true WITH a stored credential. Without one there
    # is no username to borrow and the old code invented a From on this author's
    # domain; it now refuses, so the help has to say when the field is required.
    "n_email_from": "that is not an email address (it may be left empty only "
                    "when a sign-in is stored, whose username is then used)",
    "n_smtp_host": "the SMTP server must be a hostname or IP address "
                   "(leave it empty to switch outgoing mail off)",
    "n_smtp_port": "the SMTP port must be between 1 and 65535 "
                   "(leave it empty to switch outgoing mail off)",
    "n_smtp_credentials": "that must be a path beginning with ~ or / "
                          "(leave it empty to use the vault instead)",
    "n_email_min_severity": "the alert threshold must be info, serious or critical",
}


def _bounded(v, lo, hi):
    """A number inside [lo, hi], CLEAR for blank, or None meaning refuse."""
    s = str(v).strip()
    if s == "":
        return CLEAR
    f = float(s)                      # ValueError here is caught by save_settings
    if not math.isfinite(f) or not (lo <= f <= hi):
        return None
    return f


# A sentinel for "this key was not present", because None IS a value a config
# can legitimately hold and `dict.get(k)` cannot tell the two apart.
def _url(v):
    """An http(s) URL, or None — which save_settings reads as invalid.

    Blank is allowed and clears the setting; the assistant then reports itself
    unconfigured, which is the honest state of an install that has not chosen
    a provider.
    """
    u = _s(v).strip()
    if not u:
        return ""
    if not re.match(r"^https?://[A-Za-z0-9._~-]+(:\d{1,5})?(/[^\s\"\\]*)?$", u):
        return None
    return u


def _token(v, limit):
    """A model name: letters, digits and the punctuation providers actually use."""
    t = _s(v).strip()
    if not t:
        return ""
    return t if re.match(rf"^[A-Za-z0-9._:/-]{{1,{limit}}}$", t) else None


_MISSING = object()


def toml_value(v):
    if isinstance(v, bool):  return "true" if v else "false"
    if isinstance(v, (int,)): return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ValueError(f"refusing to write a non-finite value to config: {v!r}")
        # repr-grade, so a value round-trips. "%g" is six significant digits, so
        # 1234567 was silently stored as 1.23457e+06 and read back as 1234570
        # while the response echoed the exact number the owner typed.
        return repr(int(v)) if v.is_integer() else repr(v)
    # ESCAPED IN THE RIGHT ORDER, AND CONTROL CHARACTERS REFUSED.
    #
    # This escaped the quote and nothing else, which made it a TOML INJECTION:
    # a value ending in a backslash closes the string early, because the
    # backslash the writer emits pairs with the one it is escaping and the
    # following quote terminates. DEMONSTRATED, not theorised —
    #
    #   ai_model = "a\\"
    #   evil_injected = 1
    #   #"
    #
    # parses cleanly and lands `evil_injected` in the config. A newline in the
    # value starts a new line in the file, and a trailing `#` comments out the
    # closing quote this function appends.
    #
    # WHY THAT MATTERS MORE THAN "/api/settings NEEDS ADMIN". config.toml holds
    # things the product deliberately does not expose: `[access]` is "not
    # editable through the UI on purpose", and `[paths]` decides where the
    # history lives. A settings field that can write arbitrary keys is a way
    # around both of those, which is exactly the kind of boundary this file is
    # otherwise careful about.
    #
    # The backslash goes FIRST — escaping the quote first would then double the
    # backslash it just introduced.
    out = (str(v).replace("\\", "\\\\").replace('"', '\\"')
           .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
    # Anything still unprintable cannot be escaped into a basic string, and a
    # setting has no business carrying one.
    if re.search(r"[\x00-\x1f\x7f]", out):
        raise ValueError("refusing to write a control character to config")
    return '"' + out + '"'

# "I do not know" has to be expressible. Without this a number typed once could
# never be withdrawn: save_settings treats None as INVALID, so a blank field was
# an error rather than an erasure, and every later figure went on resting on a
# value its owner had tried to take back. Only the optional keys accept it.
CLEAR = object()

def edit_toml(path, updates):
    """Rewrite `key = value` in place, per section, preserving everything else.

    Serialised: this reads the whole file, edits lines and writes it back, so two
    overlapping saves — two browser tabs, or a save landing while another key is
    written — could lose an update or interleave into a config.toml that no
    longer parses. An unparseable config is not a small failure: config.load()
    runs per connection, so the site stops answering entirely, and the file is on
    the share where a container rebuild will not replace it.

    Sections are tracked as the file is walked so `host` under [aqualink] is not
    confused with a `host` that might one day exist elsewhere. A key that is not
    present in its section is appended to that section rather than to the end of
    the file, where it would silently land in whatever section came last.
    """
    with locking.exclusive(os.path.dirname(os.path.abspath(path)) or ".", "config"):
        return _edit_toml(path, updates)

DROP = object()          # marks a line to remove rather than rewrite

def _edit_toml(path, updates):
    lines = open(path).read().split("\n")
    section, seen = "", set()
    for i, line in enumerate(lines):
        m = re.match(r"\s*\[([^\]]+)\]", line)
        if m:
            section = m.group(1).strip()
            continue
        m = re.match(r"(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*)$", line)
        if not m:
            continue
        key = m.group(2)
        if (section, key) in updates:
            val = updates[(section, key)]
            # Keep any trailing comment: it is usually the reason for the value.
            comment = ""
            cm = re.search(r"(\s+#.*)$", m.group(4))
            if cm: comment = cm.group(1)
            lines[i] = (DROP if val is CLEAR else
                        f"{m.group(1)}{key}{m.group(3)}{toml_value(val)}{comment}")
            seen.add((section, key))

    for (sec, key), val in updates.items():
        # Clearing a key that is not there is already done.
        if (sec, key) in seen or val is CLEAR:
            continue
        try:
            at = next(i for i, l in enumerate(lines) if re.match(rf"\s*\[{re.escape(sec)}\]", l))
        except StopIteration:
            lines += [f"", f"[{sec}]", f"{key} = {toml_value(val)}"]
            continue
        end = at + 1
        while end < len(lines) and not re.match(r"\s*\[", lines[end]):
            end += 1
        lines.insert(end, f"{key} = {toml_value(val)}")

    # Written via a UNIQUELY named temporary file and renamed, so an interrupted
    # save cannot leave a half-written config that no collector can parse — and
    # two overlapping saves cannot interleave into one shared ".tmp" and rename
    # the result over the real file. That produced a config.toml that would not
    # parse, which takes the whole site down (config.load() runs per connection)
    # and survives a container rebuild, because the file is on the share.
    lines = [l for l in lines if l is not DROP]
    body = "\n".join(lines)

    # PARSE WHAT IS ABOUT TO BE WRITTEN, AND COMPARE IT TO WHAT WAS ASKED FOR.
    #
    # The escaping in toml_value() is the specific fix; this is the general one,
    # and it is the half that will still hold when somebody finds the next way
    # to smuggle a delimiter through. A settings save may change exactly the
    # keys it was given — a value that alters any OTHER key, or adds one, or
    # makes the file unparseable, is refused before it reaches the disk.
    #
    # config.toml holds `[access]`, which this product says is "not editable
    # through the UI on purpose", and `[paths]`, which decides where an
    # irreplaceable history lives. Neither is a thing a text field should be
    # able to reach, and until this check existed one could.
    try:
        after = tomllib.loads(body)
    except Exception as e:                                    # noqa: BLE001
        raise ValueError(
            f"refusing to write a config.toml that will not parse ({e}). "
            f"No setting is worth a file no collector can read.")
    before = {}
    try:
        with open(path, "rb") as f:
            before = tomllib.load(f)
    except Exception:                                         # noqa: BLE001
        before = {}

    def _flat(d, pre=()):
        out = {}
        for k, v in (d or {}).items():
            out.update(_flat(v, pre + (k,)) if isinstance(v, dict)
                       else {pre + (k,): v})
        return out

    asked = {(sec, key) for (sec, key) in updates}
    changed = set()
    fb, fa = _flat(before), _flat(after)
    for k in set(fb) | set(fa):
        if fb.get(k, _MISSING) != fa.get(k, _MISSING):
            changed.add(k[-2:] if len(k) >= 2 else k)
    stray = sorted(changed - asked)
    if stray:
        raise ValueError(
            f"refusing this save: it would also change "
            f"{', '.join('.'.join(k) for k in stray)}, which it was not asked "
            f"to touch. A setting that can rewrite another key is a way around "
            f"every boundary config.toml holds.")

    locking.replace_atomically(path, lambda f: f.write(body))

# Defined in chemicals.py, which bin/chem also imports. It was declared here and
# declared AGAIN, six columns long, in chem.py -- so whichever of the two
# writers created chemicals.csv decided its header and the other corrupted every
# row it appended. Two writers of one file cannot each hold their own opinion of
# its shape.
CHEM_COLS = CH.CHEM_COLS

def log_chemical(data, cfg, who="local"):
    """Serialised against the other chemicals.csv writers. See locking.py.

    Logging appends while editing and deleting rewrite the whole file, so an
    append landing inside a rewrite was dropped: the browser was shown "logged"
    and the estimated effect, and the row was not there afterwards. Nine
    concurrent logs, six rows. The lock covers the read AND the write, because
    it is the gap between them that loses the row.
    """
    with locking.exclusive(config.data_dir(cfg), "chemicals"):
        return _log_chemical(data, cfg, who)

def _log_chemical(data, cfg, who="local"):
    from . import render
    chem = _s(data.get("chemical")).strip()
    if chem not in CH.CHEMICALS:
        return None, f"unknown chemical {chem!r}"
    amount = _num(data.get("amount"))
    if amount is None:
        return None, "amount is not a number"
    if not (amount > 0) or amount != amount or amount in (float("inf"),):
        return None, "amount must be greater than zero"
    unit = _s(data.get("unit")).strip()
    if unit not in CH.units_for(chem):
        return None, f"{unit!r} is not a unit for a {CH.CHEMICALS[chem]['phase']}"
    # The same bound the EDIT route applies. It used to live only there, so this
    # route accepted 5,000 gal of acid and reported -202,511 ppm of alkalinity as
    # an estimate -- and then refused to let anybody edit the row it had just
    # written, because the bound on the way back in was stricter than the one on
    # the way out. chemicals.py owns dose arithmetic, so it owns this too.
    ok, why = CH.plausible_amount(chem, amount, unit)
    if not ok:
        return None, why
    pct = _num(data.get("pct"))
    if pct is None:
        return None, "strength is not a number"
    if not (0 < pct <= 100):
        return None, "strength must be between 0 and 100"

    ts = _s(data.get("ts")).strip()
    when = None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            when = dt.datetime.strptime(ts, fmt); break
        except ValueError:
            pass
    # An EMPTY field means "log this now", which is the ordinary case and right.
    # A field that was filled in and could not be parsed is a different thing
    # entirely, and it used to land here as `or now()`: the dose was written at
    # the wrong time and reported as a success, in the one log the whole fitting
    # premise rests on. Silently moving somebody's timestamp is worse than
    # refusing it, because nothing downstream can tell it happened.
    if when is None and ts:
        return None, (f"could not read the time {ts!r} — expected "
                      f"YYYY-MM-DDTHH:MM. Leave it empty to log it as now.")
    when = when or dt.datetime.now()
    # A dose cannot have been added in the future; accepting one would put an
    # effect before its cause in every join the model does.
    if when > dt.datetime.now() + render.CLOCK_SKEW:
        return None, "that time is in the future"

    row = {"ts": when.astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
           "chemical": chem, "amount": f"{amount:g}", "unit": unit,
           "pct": f"{pct:g}", "note": _s(data.get("note")).strip()[:200],
           "by": who, "id": CH.new_id()}

    # Migrate, then append -- log_chemical already holds the chemicals lock, so
    # this is append_LOCKED and not append_row. locking.exclusive() is NOT
    # re-entrant (flock is per open file description), and a comment here used
    # to claim it was; taking it again would have blocked for the full fifteen
    # seconds and then blamed a second writer that does not exist.
    # This used to be a bare append, so every install whose chemicals.csv
    # predates the `by` column got a 7-field row under a 6-field header on the
    # very next dose: the API answered 200 with the estimated effect, the page
    # showed the dose, and the attribution fell into an unnamed overflow column.
    out = os.path.join(config.data_dir(cfg), "chemicals.csv")
    locking.append_locked(out, CHEM_COLS, row)

    # A HISTORY THAT RECORDS DELETIONS AND NOT CREATIONS IS HALF A HISTORY.
    # dose.edit and dose.delete both wrote an audit row and dose.log did not, so
    # the trail could show a dose being removed with no record of it ever having
    # been added — and chemicals.csv's own `by` column is the only other source.
    # The id in the id COLUMN. dose.log passed none at all, so every dose row
    # ever written had it empty, while dose.edit and dose.delete smuggled it
    # into `object` as "ts [id]" — three spellings for one fact, and audit.py's
    # own comment says the column exists precisely so `object` is not
    # overloaded. Two doses logged in the same second were addressable in
    # chemicals.csv and byte-identical in the permanent record.
    audit.record("dose.log", row["ts"],
                 f"{row['amount']} {row['unit']} {row['chemical']} @ {row['pct']}%"
                 + (f" — {row['note']}" if row["note"] else ""),
                 by=who, cfg=cfg, id=row.get("id", ""))

    # Through config.volume(), which answers None when nobody has said how big
    # the pool is. This reached past it into the raw config and defaulted to
    # 15,000, so an unconfigured install was handed an estimated effect on a
    # notional pool and the page presented it as a fact about the reader's.
    gal = config.volume(cfg)
    return {"logged": row,
            # Returned so the caller that just logged it can address it without
            # a second lookup -- the one identity of this row, not its minute.
            "id": row["id"],
            "effects": CH.effects(chem, amount, unit, pct, gal) if gal else None,
            "gallons_known": gal is not None}, None


def find_dose(rows, data, cfg):
    """The one row an edit or a delete names, or (None, why).

    THE CALLER MUST HOLD THE CHEMICALS LOCK. ensure_ids() may write, and the
    read that chose the row and the write that changes it have to be one
    operation for the same reason logging does.

    `id` first and `ts` still accepted: the page addresses rows by timestamp
    today and an install is not re-rendered by this change. A ts that names one
    row is as good an answer as an id; a ts that names several is the defect
    this exists to end, and it now refuses with the ids rather than with
    nothing, so there is a way out of it from the same screen.

    Returns (row, None) or (None, message).
    """
    from . import render
    ident = _s(data.get("id")).strip()
    if ident:
        hit = [r for r in rows if (r.get("id") or "").strip() == ident]
        if not hit:
            return None, "no entry with that id"
        if len(hit) > 1:                      # ensure_ids makes this unreachable
            return None, f"{len(hit)} entries share that id"
        return hit[0], None
    ts = _s(data.get("ts")).strip()
    if not ts:
        return None, "no timestamp given"
    hit = [r for r in rows if r.get("ts") == ts]
    if not hit:
        return None, "no entry with that timestamp"
    if len(hit) > 1:
        return None, (
            f"{len(hit)} entries share that timestamp. The dose form records to "
            f"the minute, so this is two doses logged in the same one — say which "
            f"by its id: "
            + ", ".join(f"{(r.get('id') or '?')} ({r.get('amount','')} "
                        f"{r.get('unit','')} {r.get('chemical','')})" for r in hit))
    return hit[0], None

def delete_chemical(data, cfg, who="local"):
    """Serialised against the other chemicals.csv writers. See locking.py.

    Logging appends while editing and deleting rewrite the whole file, so an
    append landing inside a rewrite was dropped: the browser was shown "logged"
    and the estimated effect, and the row was not there afterwards. Nine
    concurrent logs, six rows. The lock covers the read AND the write, because
    it is the gap between them that loses the row.
    """
    with locking.exclusive(config.data_dir(cfg), "chemicals"):
        return _delete_chemical(data, cfg, who)

def _delete_chemical(data, cfg, who="local"):
    """Remove one logged dose, identified by its own id.

    A log you can only append to is a log that permanently carries its own
    typos, and a fabricated 24 fl oz of acid is worse for the fit than no row at
    all — the model would attribute a real pH change to a dose that never
    happened. So this exists, it matches on the row's id rather than on a row
    index (indices shift as rows are added) or on its timestamp (two doses in
    one minute share one), and it refuses to run if the match is not exactly one
    row.
    """
    out = os.path.join(config.data_dir(cfg), "chemicals.csv")
    if not os.path.exists(out):
        return None, "nothing logged yet"
    rows = list(csv.DictReader(open(out)))
    # Before the match, so the ids a refusal names are the ids on disk. A row
    # written before the column existed, or by bin/chem, has none until here.
    if CH.ensure_ids(rows):
        locking.rewrite_locked(out, CHEM_COLS, rows)
    gone, why = find_dose(rows, data, cfg)
    if why:
        return None, why
    ts = gone.get("ts", "")
    keep = [r for r in rows if r is not gone]
    # Migrate, then rewrite -- one call, so an unknown column on disk refuses
    # here exactly as it does when a dose is appended. This rebuilt the file
    # from CHEM_COLS alone and silently deleted anything else.
    locking.rewrite_locked(out, CHEM_COLS, keep)
    audit.record("dose.delete", ts,
                 f"{gone.get('amount','')} {gone.get('unit','')} "
                 f"{gone.get('chemical','')}".strip(),
                 by=who, cfg=cfg, id=gone.get("id", ""))
    return {"deleted": ts, "id": gone.get("id", ""),
            "remaining": len(keep)}, None

def compute_pool_shape(data, cfg):
    """Work out the volume and show the reasoning, without adopting it.

    Calculating and committing were the same action, which meant the only way to
    see how a number was arrived at was to already be using it. That is backwards
    for a figure every dose calculation multiplies through — you want to look at
    the breakdown, disagree with a depth, and try again before anything changes.
    """
    err = _validate_shape(data)
    if err:
        return None, err
    result, e2 = pool_shape.compute(data)
    if e2:
        return None, e2
    out = {k: v for k, v in result.items() if k != "shape"}
    out["shape_summary"] = result.get("shape")
    out["saved"] = False
    return out, None

def _validate_shape(data):
    """Store a traced outline and adopt the volume it implies.

    The browser previews the same figure live so it moves as the outline is
    dragged, but the number written here is recomputed from the raw inputs on
    this side, and that is the one that counts. A preview and a stored value
    that quietly disagree is the sort of bug found months later by someone
    wondering why the dashboard reports a figure the tool never showed them —
    so the two are compared and the difference is returned rather than hidden.

    The inputs are kept alongside the answer so the outline can be reopened and
    adjusted. Redoing the whole trace to move one point would guarantee it never
    gets refined.
    """
    # Two ways in: a traced outline, or a stock design plus dimensions. The
    # validation has to know that, or the design route is rejected for lacking
    # points it was never going to have.
    if data.get("template"):
        from . import templates as TPL
        if data["template"] not in TPL.TEMPLATES:
            return f"unknown design {data['template']!r}"
        for k in ("length_ft", "width_ft"):
            v = data.get(k)
            if not isinstance(v, (int, float)) or not (0 < v <= 200):
                return f"{k.replace('_ft','')} must be between 0 and 200 feet"
    else:
        pts = data.get("points")
        if not isinstance(pts, list) or len(pts) > 500:
            return "expected a list of at most 500 traced points"
        for p in pts:
            if (not isinstance(p, (list, tuple)) or len(p) != 2
                    or not all(isinstance(c, (int, float)) for c in p)):
                return "each point must be a pair of numbers"
    return None

def save_pool_shape(data, cfg, who="local"):
    err = _validate_shape(data)
    if err:
        return None, err
    result, err = pool_shape.compute(data)
    if err:
        return None, err

    record = dict(data)
    record["computed"] = result
    record["saved"] = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z")
    pool_shape.save(record, cfg)

    # Pool and spa are kept as separate figures, and whether the total includes
    # the spa is a plumbing question rather than a geometric one: a spa that
    # spills over continuously shares one body of water with the pool and every
    # dose is diluted by both, while one isolated behind a valve is a second
    # pool that happens to be nearby. Storing them apart means that decision can
    # be changed later without re-tracing anything.
    spa_gal = round((result.get("spa") or {}).get("gallons", 0))
    pool_gal = round(result.get("pool_only_gallons", result["gallons"]))
    shares = bool(cfg.get("pool", {}).get("spa_shares_water", True))
    gal = combined_volume(pool_gal, spa_gal, shares)
    # WHO RE-TRACED THE POOL. This route rewrites volume_gallons — the number
    # every dose calculation multiplies through — and recorded nothing at all,
    # so a figure that had moved could not be attributed to anybody, while a
    # settings save that changed the same key was audited. It took no `who`
    # either: the dispatch handed self.whoami() to chemical, control, reading,
    # lab-correction and settings, and to nothing else.
    was = num_or_none((cfg.get("pool") or {}).get("volume_gallons"))
    audit.record("pool_shape", "pool.volume_gallons",
                 f"{'' if was is None else f'{was:g} -> '}{gal:g} gal "
                 f"(pool {pool_gal:g} + spa {spa_gal:g}, "
                 f"{'shared' if shares else 'spa isolated'})",
                 by=who, cfg=cfg)
    edit_toml(cfg["_source"], {("pool", "volume_gallons"): gal,
                               ("pool", "pool_only_gallons"): pool_gal,
                               ("pool", "spa_gallons"): spa_gal,
                               # Traced from a photograph with one measured
                               # distance is still an estimate — a good one, but
                               # the pages should keep saying so until the acid
                               # response measures the volume directly.
                               ("pool", "volume_estimated"): True})
    # Return the whole analysis, not a summary of it. The page has a verdict
    # block built to explain the classification, the pool/spa split, the bench,
    # the comparison against length x width and where the uncertainty comes from
    # — and a trimmed response left it with nothing to say, which is a subtler
    # version of computing the thing and throwing it away.
    out = {k: v for k, v in result.items() if k != "shape"}
    out["shape_summary"] = result.get("shape")
    out["saved_volume_gallons"] = gal
    out["saved_pool_gallons"] = pool_gal
    out["saved_spa_gallons"] = spa_gal
    out["spa_shares_water"] = shares
    preview = data.get("preview_gallons")
    if isinstance(preview, (int, float)) and preview > 0:
        drift = abs(preview - result["gallons"]) / result["gallons"] * 100
        if drift > 1.0:
            out["preview_disagreed_pct"] = round(drift, 1)
    return out, None

def send_test_notification(data, cfg, who="local"):
    """Prove the channel works before it is needed.

    Discovering that mail was misconfigured at the moment the chlorine is gone is
    the worst possible time to discover it, and SMTP fails for a dozen mundane
    reasons — wrong port, an account password where an app password is required,
    two-factor. So the server's own rejection text is passed back rather than
    flattened into "failed", because that text is usually the fix.
    """
    from . import watch
    which = _s(data.get("channel") or "email").lower()
    # This sends mail from the household's SMTP account, to the household's
    # address list, and recorded nothing. "Who made this account send mail" is
    # exactly the question audit.py says it exists to answer months later.
    audit.record("notify.test", which, "requested", by=who, cfg=cfg)
    if which == "desktop":
        ok = watch._desktop("Test notification",
                            "If you can see this, desktop alerts are working.",
                            subtitle="poolhound")
        # Two-tuples on BOTH branches. The success branch returned a bare dict,
        # which the caller unpacked as `result, err = ...` -- yielding
        # result="sent" and err="channel". So the one button whose entire
        # purpose is to prove a channel works fired the notification and then
        # told the operator it had failed, with an error message that was the
        # second KEY of the dict it should have returned. The email branch below
        # is a correct tuple, which is why this survived.
        if ok:
            return {"sent": True, "channel": "desktop"}, None
        return None, "no desktop notifier available"
    n = cfg.get("notify", {})
    if not n.get("email"):
        return None, "email is switched off — turn it on and save first"
    # ONE copy of this message, in mail.py. The comment that used to sit here
    # explained that the other copy -- bin/watch --test-email -- described the
    # sender differently from the envelope. It did, for as long as there were
    # two copies to disagree.
    from poolhound import mail
    ok, err = watch.send_email(cfg, mail.test_alert(cfg))
    if ok:
        return {"sent": True, "channel": "email", "to": n.get("email_to")}, None
    hint = ""
    if "Application-specific password" in str(err) or "5.7.9" in str(err):
        hint = (" Gmail needs an app password rather than the account password: "
                "myaccount.google.com/apppasswords")
    return None, f"{err}{hint}"

# What a manual refresh can run, and what each one costs. The cost column is the
# whole reason this is not one button: WaterGuru asks for no more than a call or
# two daily, and Leslie's means logging in as a browser.
#
# THE CONTROLLER IS NOT HERE, AND CANNOT BE. It was the first and
# cheapest-looking entry -- "free, it is a local HTTP call" -- and on the server
# that call is one this host has no route to make, by design: the panel has no
# authentication of its own, so nothing reaches inward and the Pi pushes out
# instead. The button therefore had exactly one possible outcome, a paragraph
# explaining why it could not work, which had to be special-cased in the
# handler below. A control whose only behaviour is to explain itself is not a
# control. There is nothing to refresh: samples arrive on their own every
# fifteen minutes, and the Collection tab says when the last one did.
REFRESHABLE = {
    "waterguru": {"script": "wg-collect", "label": "WaterGuru",
                  "cost": "spends one of the two API calls a day upstream asks for",
                  "free": False},
    "leslies":   {"script": "leslies",    "label": "Leslie's",
                  "cost": "logs in as a browser; returns the whole history anyway",
                  "free": False},
}

def save_credential(data, cfg):
    """Store one service login, encrypted.

    The password arrives over loopback HTTP, which is acceptable only because
    that socket is unreachable from the network and every write already carries
    the session token and an origin check. It is never echoed back — not masked,
    not truncated, not its length — and never written to the log, which is why
    this endpoint returns a status rather than the record it just saved.
    """
    svc = _s(data.get("service")).strip()
    if svc not in vault.SERVICES:
        return None, f"unknown service {svc!r}"
    user = _s(data.get("username")).strip()
    pw = _s(data.get("password"))
    # Where it goes is the caller's choice, and both are real answers: the vault
    # for anything that can use it, a plain file for a deployment that cannot —
    # a collector running as another user, a host with neither keychain nor
    # managed identity. The page states the trade rather than hiding the option.
    store = _s(data.get("store") or "vault").strip()
    if store not in ("vault", "file"):
        return None, f"unknown store {store!r}"
    # INSIDE THE try, WHICH IS THE WHOLE OF ONE DEFECT. drop() reads the store
    # before it writes, so on a vault whose key no longer opens it this raised
    # VaultError from OUTSIDE the only handler: the socket closed with no HTTP
    # status, the browser's fetch() rejected with a bare network error, and the
    # page went on showing the message that told the reader to enter the
    # passwords again — the one thing this route existed to let them do.
    # Measured on a sandboxed server with one byte of vault.enc flipped: Forget
    # answered nothing at all (curl exit 52); it now answers 200.
    try:
        if data.get("forget"):
            vault.drop(svc)
            return {"forgotten": svc, "status": vault.status()}, None
        if not user:
            return None, "a username is needed"
        # A BLANK PASSWORD IS "LEAVE IT AS IT IS", which is what the field's own
        # placeholder promises. vault.put() keeps the stored one and refuses
        # only when there is nothing to keep. A plain FILE has no stored half to
        # keep — it is two lines, written whole — so that store still needs both.
        if not pw and store == "file":
            return None, ("a credential file is written whole, so it needs the "
                          "password as well — there is no stored half to keep. "
                          "Save to the vault to change only the username.")
        if len(user) > 320 or len(pw) > 1024:
            return None, "that is longer than any real credential"
        if store == "file":
            # THE KEY THE REST OF THE PRODUCT READS. This looked the path up
            # under cfg["smtp"]["credentials"], a section that exists in no
            # config.toml, no config.DEFAULTS and nowhere else in the
            # repository, so it was always None: every file-store save of the
            # mail password landed on the built-in ~/.poolhound_smtp while
            # watch.py went on reading notify.smtp_credentials — the key the
            # Settings form beside this one edits. One accessor answers "where
            # does this service's credential file live" now.
            written = vault.put_file(svc, user, pw,
                                     vault.credential_file(svc, cfg) or None)
            return {"saved": svc, "store": "file", "path": written,
                    "status": vault.status()}, None
        where = vault.put(svc, user, pw)
    except vault.VaultError as e:
        return None, str(e)
    # Drop any cached Key Vault copy of this service before reporting status.
    # The cache held a secret for the life of the process -- weeks, under
    # `restart: unless-stopped` -- so the page reporting whether a rotated
    # credential is in use was answering from a copy taken before the rotation.
    vault.forget_cache(svc)
    return {"saved": svc, "store": "vault", "key_location": where,
            "status": vault.status()}, None

def import_credentials(data, cfg, who="local"):
    """Move every plaintext credential found on disk into the vault.

    AUDITED, because its sibling is. /api/credential wrote an audit row for
    "set one password" and this — "move every password on this host into the
    store, and optionally delete the originals" — wrote none at all, so the more
    privileged of the two operations was the invisible one. The service NAMES
    are recorded; nothing else from the credential ever is.
    """
    moved = vault.import_legacy(remove=bool(data.get("remove")))
    audit.record("credential.import",
                 ",".join(sorted(str(m.get("service", "")) for m in moved)) or "none",
                 f"{len(moved)} imported"
                 + (", originals removed" if data.get("remove") else ""),
                 by=who, cfg=cfg)
    return {"imported": moved, "status": vault.status()}, None

def current_states():
    """What the panel last reported, keyed by control rather than by column.

    The page is rendered ahead of time and so is always a little behind; this is
    the same facts as of right now, in the shape the browser needs to paint them
    without re-rendering anything.
    """
    from .render import rows as _rows, newest as _newest
    try:
        samples = _rows("samples.csv")
    except Exception:
        return {}
    if not samples:
        return {}
    # newest(), not the last row appended. That was defect 10 of the acceptance
    # review and it was fixed on Home and nowhere else -- both labs return their
    # whole history on every pull and the agent re-pushes after a reconnect, so
    # a row arriving out of order is normal rather than exotic.
    last = _newest(samples, "ts")
    COL = {d: c for d, c in CMD.STATE_COLUMN.items() if c}
    out = {}
    for dev, col in COL.items():
        raw = str(last.get(col, "")).strip()
        out[dev] = "on" if raw == "1" else ("off" if raw != "" else "unknown")
    out["_ts"] = last.get("ts", "")
    # The SETPOINT VALUES, separately. "Is the heater running" and "what is it
    # set to" are different questions and only the first was answered here, so
    # the page compared a commanded setpoint of "88" against the heater's on/off
    # state "on", found them different, and showed "set to 88, waiting" until
    # the twenty-minute cap -- while the panel had confirmed within a second.
    # The amber pending badge REPLACED the on/off badge, so for twenty minutes
    # the one card that could say whether the most expensive thing in the pool
    # was running said nothing.
    #
    # They are also the four numbers most recently added to make setpoints
    # readable, and the "now 88" beside the card was baked into the render --
    # the exact thing CLAUDE.md says must be fetched, never baked.
    out["setpoints"] = {
        dev: str(last.get(spec.get("value_column") or "", "")).strip()
        for dev, spec in CMD.SETPOINTS.items()}
    return out


def pending_devices():
    """Devices commanded since the panel last reported, and what was asked of them.

    THE GAP THIS NAMES
    A command can succeed — the agent set it, the panel took it — and the page
    still show the old state, because what the page shows is the last SAMPLE and
    samples arrive every fifteen minutes. For up to a quarter of an hour a
    switch that was just turned on reads as off, which looks exactly like a
    command that failed.

    So the two facts are kept apart: what the panel last reported, and what has
    been asked of it since. A control in that gap is neither on nor off, it is
    waiting, and saying so is the difference between "the pool is ignoring me"
    and "the pool has not been asked yet".

    Only commands that the agent ACCEPTED count. One it refused is not pending,
    it is finished and failed.
    """
    # Imported here rather than at module scope: render imports panels imports
    # server, and hoisting this turns a working program into a circular import.
    from .render import rows as _rows, newest as _newest, when as _when
    try:
        samples = _rows("samples.csv")
    except Exception:
        return {}
    last = _newest(samples, "ts")
    last_at = _when(last.get("ts", ""))
    now = dt.datetime.now()

    # Which sample column reports each switch — from commands.py, which the server
    # and the agent both validate against, so the reading end and the confirming
    # end cannot drift apart. This was a local copy, and so were three others.
    COL = {d: c for d, c in CMD.STATE_COLUMN.items() if c}
    # And where each SETPOINT's value lands, so a commanded temperature can be
    # confirmed against what the panel reports rather than assumed.
    VALUE_COL = {d: v["value_column"] for d, v in CMD.SETPOINTS.items()
                 if v.get("value_column")}

    out = {}
    # ONLY THE NEWEST COMMAND PER DEVICE.
    #
    # history() is newest-first, so the first entry for a device is the one that
    # still stands and every later entry is a superseded instruction. Walking
    # all of them resurrected old ones: switch the pool light on, switch it off
    # again thirty seconds later, and the ON command was still within the
    # four-minute window with the panel now reporting off — so it was reported
    # as "waiting" all over again, minutes after it had been obeyed and
    # deliberately reversed. It surfaced on whatever the operator did next,
    # which made an unrelated command look like it had triggered the light.
    seen = set()
    for h in QUEUE.history(40):
        cmd = h.get("cmd") or {}
        dev = cmd.get("device")
        if not dev or cmd.get("action") not in ("set", "setpoint"):
            continue
        if dev in seen:
            continue                      # an older instruction for this device
        seen.add(dev)
        res = h.get("result") or {}
        if res and not res.get("ok"):
            continue                      # refused or failed: not waiting on anything
        at = _when(h.get("at", ""))
        if not at:
            continue

        # Pending means the PANEL DOES NOT YET AGREE — not merely that no sample
        # has arrived since. Those came apart the moment the agent started
        # pushing a reading straight after each command: a sample now lands
        # within a second, so "a sample has arrived" would clear the waiting
        # mark whether or not anything actually changed, and a command that
        # silently did nothing would present as confirmed.
        want = None
        col = COL.get(dev)
        vcol = VALUE_COL.get(dev)
        if cmd.get("action") == "set" and col:
            want = "1" if cmd.get("value") in (1, "1", True) else "0"
            got = str(last.get(col, "")).strip()
            if last_at and last_at >= at and got == want:
                continue                  # the panel has reported it; done
        elif cmd.get("action") == "setpoint" and vcol:
            # Setpoints ARE reported back now. This branch used to say they were
            # not and fall through to "one reading after the command is the only
            # confirmation available" -- which cleared the waiting mark whether
            # or not the panel had taken the number, the exact confusion the
            # switch branch above exists to avoid. The value column carries what
            # the panel is actually set to, so it can be compared like anything
            # else. Numerically: the panel answers 75.0 to a command of 75.
            got = str(last.get(vcol, "")).strip()
            try:
                agreed = got != "" and abs(float(got) - float(cmd.get("value"))) < 0.51
            except (TypeError, ValueError):
                agreed = False
            if last_at and last_at >= at and agreed:
                continue                  # the panel reports the number we asked for
        elif last_at and at <= last_at:
            # No column of any kind for this device, so one reading after the
            # command is the only confirmation available.
            continue

        # Give up after a few minutes. Past that the panel is not going to
        # change, and leaving it amber forever would turn a failed command into
        # permanent furniture. What was asked stays in the command log below.
        if (now - at).total_seconds() > CMD.PENDING_GIVES_UP_S:
            continue

        out[dev] = "on" if cmd.get("value") in (1, "1", True) else (
                   str(cmd.get("value")) if cmd.get("action") == "setpoint" else "off")
    return out


def ingest_sample(sample, cfg):
    """Store one sample pushed up by the agent.

    Out-of-order and duplicate arrivals are dropped on the timestamp, because a
    retry after a dropped connection is normal and must not double-count pump
    minutes.

    THE STALE-FLOW RULE IS APPLIED HERE, AT THE WRITE. It used to be applied by
    each collector before the row reached a file, and there were two of them —
    the Pi's agent, and the workstation poller. Deleting the poller left the
    rule living entirely on the far side of a network boundary: the server checked
    the COLUMNS of an incoming sample and took its VALUES on trust, so the one
    reading the panel latches rather than blanks — SWG/PPM, which repeats its
    last measured figure for as long as the pump is off — was refused only by a
    device the server does not control the version of. An agent that is older, or
    restored from an image, or reimplemented, would have had its latched salt
    stored and averaged.

    Applying it again here costs nothing and is idempotent: blanking a field
    that is already blank is a no-op, so the agent doing it first remains the
    right thing and this is the backstop. The rule itself is still the one
    function in commands.py — this adds a caller, not a copy.
    """
    ts = str(sample.get("ts") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4}", ts):
        return None, "sample needs an ISO timestamp with an offset"
    from . import aqualink, commands as _C
    sample = _C.drop_stale_flow_readings(dict(sample))
    cols = aqualink.COLS
    path = os.path.join(config.data_dir(cfg), "samples.csv")
    # Only the tail is scanned, not the whole file.
    #
    # This ran on every push — once every fifteen minutes — and read the entire
    # samples file to check one timestamp, which makes inserting N rows cost
    # O(N^2) over the life of the pool. It is cheap today at 400 rows and it is
    # a 10 MB read five years from now, forever, to answer a question the last
    # few hundred rows already settle.
    #
    # A duplicate only ever arrives from the agent retrying a push it is not
    # sure landed, which happens within minutes. 256 KB of tail is several days
    # of samples — orders of magnitude more history than a retry can span.
    #
    # This used to end "and the server is the only writer, so nothing else can slip a
    # row in behind us", which was the load-bearing half and was not true. One
    # HOST is not one writer: the server is threaded, and two retries arriving
    # together are two threads in this function at once. What makes the read and
    # the append safe is the lock the caller holds, not the deployment topology.
    # A comment that credits the wrong thing is worse than no comment, because
    # the next person removes the lock and keeps the reasoning.
    # Serialised, like the other writers of a file on the share. The dedup below
    # is a read followed by an append, and it is the GAP between them that loses
    # the race: two retries of the same push both find the timestamp absent and
    # both append it.
    #
    # NAMING ONLY WHAT IS TRUE. This used to say "chemicals.csv, config.toml,
    # manual.csv and leslies.csv all take this lock", and leslies.py imports no
    # locking at all — it reads the whole file, merges and rewrites it unlocked.
    # A comment that credits the wrong thing is the failure its own neighbouring
    # paragraph warns about. What DOES take a lock today: chemicals.csv (here
    # and in bin/chem), config.toml, manual.csv, audit.csv and samples.csv (here
    # and in aqualink.py). STILL UNLOCKED, and each one a read-modify-write or a
    # dedup-then-append: leslies.py (leslies.csv), collect_wg.py (readings.csv,
    # lab.csv, wg_targets.csv), corrections.py (corrections.csv).
    with locking.exclusive(config.data_dir(cfg), "samples"):
        out, err = _ingest_sample_locked(sample, ts, path, cols)
    if err or not (out or {}).get("stored"):
        return out, err

    # OUTSIDE THE LOCK. The critical section is the dedup read plus the append;
    # the render is neither, and it was held inside — 0.42s measured on a local
    # SSD with eight rows, and the server renders 549 rows from six CSVs on a CIFS
    # mount with actimeo=30 and writes 1.1 MB. locking.py sized its timeout for
    # "sub-millisecond critical sections"; a slow share turned that into a real
    # LockTimeout on the dose path, and a LockTimeout is a refused write.
    #
    # BOTH builds, and the caller no longer renders again: the handler rendered
    # index.html and admin.html immediately after this returned, so every stored
    # sample rendered admin.html twice.
    #
    # A failed re-render must not vanish. The row is stored either way — that is
    # the important half and it has already happened — but the page the
    # household reads then silently stays at the previous sample, which is the
    # same "green result about the wrong thing" this all exists to prevent.
    try:
        from . import render
        render.build(public=True)
        render.build()
    except Exception as e:
        why = f"{type(e).__name__}: {e}"
        out["rendered"] = False
        out["render_error"] = why
        print(f"ingest_sample: stored {ts} but the re-render FAILED: {why}",
              file=sys.stderr, flush=True)
    return out, None

# Moved to locking.py, which is the module every writer of these files can
# reach. It stayed here while aqualink.py -- which cannot import this module --
# was appending to samples.csv without it. Re-exported because that is the name
# the ingest path, the selftest and the deploy notes all use.
migrate_columns = locking.migrate_columns


def _ingest_sample_locked(sample, ts, path, cols):
    # Under the lock, before anything reads or appends.
    migrated = migrate_columns(path, cols)
    if migrated:
        added, n = migrated
        print(f"samples.csv: added column(s) {added} to {n} existing rows",
              file=sys.stderr, flush=True)

    TAIL = 256 * 1024
    seen = set()
    if os.path.exists(path):
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            f.seek(max(0, size - TAIL))
            chunk = f.read().decode("utf-8", "replace")
        if size > TAIL:
            # Reading from an offset lands mid-row; drop the partial first line
            # rather than parse half a timestamp as a whole one.
            chunk = chunk.split("\n", 1)[1] if "\n" in chunk else ""
        seen = {line.split(",", 1)[0] for line in chunk.splitlines() if line}
    if ts in seen:
        return {"stored": False, "reason": "already have that timestamp"}, None
    # SAY WHAT IS BEING DROPPED. extrasaction="ignore" is right -- an agent one
    # release ahead must not crash the ingest -- but it was silent, so the Pi
    # read the spa temperature off the panel every fifteen minutes for months,
    # the server discarded it at this line, and the POST still answered
    # {"stored": true}. A field collected on one machine and dropped on another
    # with nothing said is how a column stays missing without anyone noticing.
    extra = sorted(k for k in sample if k not in cols and k != "ts")
    if extra:
        print(f"ingest: the agent sent field(s) {extra} that samples.csv has no "
              f"column for; they were NOT stored. Add them to aqualink.COLS.",
              file=sys.stderr, flush=True)
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow({c: sample.get(c, "") for c in cols})
    # The render used to live here, INSIDE the lock. It is in ingest_sample now,
    # after the lock is released — see the reasoning there.
    return {"stored": True, "ts": ts}, None

def issue_control(data, cfg, who):
    """Queue one command for the pool, and wait a moment for the answer.

    Validated here so a mistake is refused while the person is still looking at
    the button — and again at the agent, which is the check that actually
    protects the panel.
    """
    # Shape first, connectivity second. A command for a device that is not in
    # the catalogue is wrong whether or not an agent happens to be listening,
    # and reporting it as "nothing is connected" sends the operator off to
    # debug the network over what is really a typo.
    try:
        CMD.validate({"id": "0" * 16, "action": data.get("action"),
                      "device": data.get("device"), "value": data.get("value"),
                      "issued": dt.datetime.now(dt.timezone.utc).isoformat()})
    except CMD.Refused as e:
        return None, str(e)
    if not QUEUE.agents_connected:
        return None, ("no agent is connected, so nothing can reach the panel. The "
                      "pool keeps running its own schedule meanwhile.")
    try:
        cmd, delivered = QUEUE.issue(data.get("action"), data.get("device"),
                                     data.get("value"), who)
    except CMD.Refused as e:
        return None, str(e)
    result = QUEUE.result_for(cmd["id"], wait=3.0)
    return {"id": cmd["id"], "what": CMD.describe(cmd), "by": who,
            "delivered_to": delivered, "result": result,
            "pending": result is None}, None

def refresh_sources(data, cfg, who="local"):
    """Run collectors on demand.

    Each is the same entry point launchd runs, invoked as a subprocess rather
    than imported. That is deliberate: a collector that hangs or dies takes a
    child process with it instead of this server, and whatever it prints is
    exactly what would have appeared in its log — so a manual refresh debugs the
    scheduled run rather than exercising a second code path that might behave
    differently.
    """
    import subprocess as sp
    want = data.get("sources") or []
    if not isinstance(want, list):
        return None, "sources must be a list"
    want = [w for w in want if w in REFRESHABLE]
    if not want:
        return None, "nothing to refresh"

    # WaterGuru allows one or two calls a day and this is the button that spends
    # them; Leslie's logs in as a browser. A budget that can be exhausted by
    # anybody the proxy admits should record who exhausted it, and this recorded
    # nothing.
    audit.record("refresh", ",".join(sorted(want)),
                 ", ".join(f"{k}: {REFRESHABLE[k]['cost']}" for k in sorted(want)
                           if not REFRESHABLE[k]["free"]) or "free sources only",
                 by=who, cfg=cfg)

    # The special case that used to sit here -- answering the "Pool controller"
    # button with a paragraph about why the server cannot poll the panel -- went
    # with the button. An unknown source is refused above; there is no longer a
    # known one that cannot run.
    results = []

    root = config.root()
    py = sys.executable
    for key in want:
        spec = REFRESHABLE[key]
        script = os.path.join(root, "bin", spec["script"])
        started = time.monotonic()
        try:
            r = sp.run([py, script], capture_output=True, text=True, timeout=180,
                       cwd=root)
            took = time.monotonic() - started
            out = (r.stdout or "").strip().splitlines()
            err = (r.stderr or "").strip().splitlines()
            results.append({
                "source": key, "label": spec["label"], "ok": r.returncode == 0,
                "code": r.returncode, "seconds": round(took, 1),
                # The last line is the summary each collector prints; the rest is
                # usually progress nobody needs on a page.
                "message": (out[-1].strip() if out else
                            (err[-1].strip() if err else "no output")),
            })
        except sp.TimeoutExpired:
            results.append({"source": key, "label": spec["label"], "ok": False,
                            "code": None, "seconds": 180,
                            "message": "timed out after 3 minutes"})
        except OSError as e:
            results.append({"source": key, "label": spec["label"], "ok": False,
                            "code": None, "seconds": 0, "message": str(e)})
    return {"results": results}, None

def edit_chemical(data, cfg, who="local"):
    """Serialised against the other chemicals.csv writers. See locking.py.

    Logging appends while editing and deleting rewrite the whole file, so an
    append landing inside a rewrite was dropped: the browser was shown "logged"
    and the estimated effect, and the row was not there afterwards. Nine
    concurrent logs, six rows. The lock covers the read AND the write, because
    it is the gap between them that loses the row.
    """
    with locking.exclusive(config.data_dir(cfg), "chemicals"):
        return _edit_chemical(data, cfg, who)

def _edit_chemical(data, cfg, who="local"):
    """Change one logged dose in place, identified by its own id.

    Editable rather than corrected-over, unlike a lab result: this row is OUR
    record of something we did, not somebody else's measurement being
    re-delivered on every pull. Nothing will put back a wrong amount, so there
    is nothing to layer over.

    The whole row is rewritten from validated fields rather than patched, so a
    partially-applied edit is not a state this can reach — and it runs through
    the same catalogue checks as logging a new one, because an edit that could
    write a unit the CLI would refuse is a second, weaker validator.
    """
    from . import render
    out = os.path.join(config.data_dir(cfg), "chemicals.csv")
    if not os.path.exists(out):
        return None, "nothing logged yet"

    rows = list(csv.DictReader(open(out)))
    if CH.ensure_ids(rows):
        locking.rewrite_locked(out, CHEM_COLS, rows)
    target, why = find_dose(rows, data, cfg)
    if why:
        return None, why
    ts = target.get("ts", "")

    row = dict(target)
    chem = _s(data.get("chemical") or row.get("chemical")).strip()
    if chem not in CH.CHEMICALS:
        return None, f"unknown chemical {chem!r}"
    unit = _s(data.get("unit") or row.get("unit")).strip()
    if unit not in CH.units_for(chem):
        return None, f"{unit!r} is not a unit for a {CH.CHEMICALS[chem]['phase']}"
    amount = _num(data.get("amount", row.get("amount")))
    pct = _num(data.get("pct", row.get("pct")))
    if amount is None or pct is None:
        return None, "amount and strength have to be numbers"
    ok, why = CH.plausible_amount(chem, amount, unit)
    if not ok:
        return None, why
    if not (0 < pct <= 100):
        return None, "strength is a percentage"

    # A COLLIDING TIMESTAMP IS NO LONGER A COLLIDING IDENTITY. The refusal
    # below used to be load-bearing -- moving one dose onto another's minute
    # made both unaddressable forever -- and it is now a courtesy: the rows keep
    # their own ids either way. Kept because two doses in one minute are almost
    # always the double-click rather than the intention, and saying so while it
    # can still be undone is worth more than quietly allowing it.
    # THE ONE FIELD THIS ROUTE DID NOT CHECK WAS THE ONE EVERYTHING INDEXES ON.
    #
    # chemical, unit, amount and pct are all validated above, and note is
    # capped — and new_ts went in unparsed, unbounded and uncapped. Measured:
    # "not-a-time" stored verbatim, 400 characters stored verbatim, an empty
    # string leaving a dose with no timestamp at all, and a date in 2030
    # accepted through the form.
    #
    # That last one is not cosmetic. dose_response pairs each dose with the
    # reading that followed it, so a dose moved into the future drops out of
    # the fit silently: this pool's MEASURED acid coefficient — the number the
    # whole product exists to produce — went from 120 fl oz per 0.1 pH to 80,
    # with the operator told only "edited".
    #
    # render.when() is the one parser and render.is_future() the one tolerance,
    # and the row is stored in the single canonical spelling so an ordinary
    # amount-only edit cannot leave two timestamp formats in one file.
    new_ts = _s(data.get("new_ts") or ts).strip()[:40]
    when_new = render.when(new_ts)
    if not when_new:
        return None, "that is not a date and time this understands"
    if render.is_future(new_ts):
        return None, "that time is in the future"
    new_ts = when_new.astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    if new_ts != ts and any(r.get("ts") == new_ts for r in rows):
        return None, "another entry already has that timestamp"

    row.update({"ts": new_ts, "chemical": chem, "amount": f"{amount:g}",
                "unit": unit, "pct": f"{pct:g}",
                "note": _s(data.get("note", row.get("note")))[:200],
                "by": who})

    # By identity, not by timestamp. `r.get("ts") == ts` rewrote EVERY row
    # sharing the minute with the edited one -- so on the pair of rows this
    # change exists to make addressable, one edit would have overwritten both
    # with the same values.
    locking.rewrite_locked(out, CHEM_COLS,
                           [row if r is target else r for r in rows])
    audit.record("dose.edit", ts,
                 (f"-> {new_ts} " if new_ts != ts else "") +
                 f"{amount:g} {unit} {chem} @ {pct:g}%",
                 by=who, cfg=cfg, id=row.get("id", ""))
    gal = config.volume(cfg)
    return {"edited": new_ts, "id": row.get("id", ""), "chemical": chem,
            "effects": CH.effects(chem, amount, unit, pct, gal) if gal else None,
            "gallons_known": gal is not None}, None


# ------------------------------------------------------------- manual readings
# A third source, alongside the two collectors.
#
# Until now readings could only ARRIVE: WaterGuru's pod and lab, and Leslie's
# bench photometer. An owner with a drop kit, test strips, or a different store
# could not record a single measurement — the product's central object was the
# one thing they could not author. It is its own file and its own source name
# rather than a row written into leslies.csv, for two reasons: that file is
# rewritten wholesale on every pull and would lose it, and claiming Leslie's
# measured something they did not is exactly the dishonesty the two-labs rule
# exists to prevent.
#
# It reconciles the same way everything else does — most recent wins per
# measure, never averaged — so a fresh kit test correctly overrides a
# three-week-old lab number, which is the entire point of being able to enter it.
MANUAL_FILE = "manual.csv"
MANUAL_COLS = ["measured", "recorded", "by", "source",
               "ph", "free_cl", "total_cl", "ta", "ch", "cya", "salt",
               "phosphates", "copper", "iron", "saturation_index", "note"]

# Plausible ranges. A test kit misread as 730 instead of 73 would otherwise
# become the newest value for alkalinity and drive every dose off it.
MANUAL_RANGE = {"ph": (5.0, 9.5), "free_cl": (0, 30), "total_cl": (0, 30),
                "ta": (0, 400), "ch": (0, 1500), "cya": (0, 300),
                "salt": (0, 10000), "phosphates": (0, 5000),
                "copper": (0, 5), "iron": (0, 5), "saturation_index": (-3, 3)}


def measure_value(field, raw):
    """One measurement as text, or (None, why). The same check wherever it enters.

    TWO DOORS INTO THE SAME NUMBER AND A BOUND ON ONLY ONE OF THEM. A pH of 99
    typed into the by-hand reading form was refused — "outside 5-9.5, which is
    not a reading a pool test produces" — and the identical figure sent as a lab
    CORRECTION was accepted, stored, and applied by the loaders to the row it
    names. Measured: a correction of 999999 on an alkalinity reading rendered a
    chart whose top tick read 1,199,987, and the page had no way to show it was
    wrong because as far as everything downstream is concerned that IS the
    reading now. A correction is not a lesser kind of measurement; it is the one
    that wins.

    Booleans are refused rather than coerced. JSON true walks through float() as
    1.0 and was stored as the string "True" — a value no chart can plot, in a
    column every dose calculation reads, from a request nothing refused.

    A field with no declared range is still required to be a number: the raw
    files carry columns this table does not describe (a Leslie's score, a TDS),
    and a text value in one of them would propagate into the arithmetic as a
    silent zero.
    """
    if isinstance(raw, bool):
        return None, f"{field} has to be a number; true and false are not readings"
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None, f"{field} has to be a number"
    if v != v or v in (float("inf"), float("-inf")):
        return None, f"{field} has to be a number that can be plotted"
    lo, hi = MANUAL_RANGE.get(field, (None, None))
    if lo is not None and not lo <= v <= hi:
        return None, (f"{field} of {v:g} is outside {lo:g}-{hi:g}, which is not a "
                      f"reading a pool test produces — check the decimal point")
    return f"{v:g}", None

def save_reading(data, cfg, who="local"):
    """Record one hand-entered test result."""
    from . import render
    label = _s(data.get("source")).strip()[:40] or "Test kit"
    raw_when = _s(data.get("measured")).strip()
    if not raw_when:
        return None, "when was it tested?"
    # render.when() AND render.is_future(), not a private copy of each.
    #
    # This called fromisoformat directly and compared the result against a
    # fresh timedelta(minutes=5). fromisoformat returns an AWARE datetime for
    # anything carrying an offset, and now() is naive, so the comparison raised
    # TypeError — a 500 — for every timestamp with a zone. Including the one
    # this very route hands back on success: {"recorded":
    # "2026-09-17T10:41:00-0700"}. The UI was shielded only by an accident,
    # datetime-local emitting no offset.
    #
    # So the future guard, which the suite records as having been consolidated
    # into ONE place, ran nowhere at all for an offset-bearing timestamp: it
    # raised before it could judge. render.when() normalises all three flavours
    # this product handles and render.is_future() owns the tolerance.
    measured = render.when(raw_when)
    if not measured:
        return None, "that is not a date and time this understands"
    if render.is_future(raw_when):
        return None, "that time is in the future"

    row = {c: "" for c in MANUAL_COLS}
    got = 0
    for key in MANUAL_RANGE:
        raw = data.get(key)
        if raw is None or (not isinstance(raw, bool) and str(raw).strip() == ""):
            continue
        text, err = measure_value(key, raw)
        if err:
            return None, err
        row[key] = text
        got += 1
    if not got:
        return None, "no measurements given — fill in at least one"

    row["measured"] = measured.astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    row["recorded"] = dt.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    row["by"], row["source"] = who, label
    row["note"] = _s(data.get("note")).strip()[:200]

    out = os.path.join(config.data_dir(cfg), MANUAL_FILE)
    with locking.exclusive(config.data_dir(cfg), "manual"):
        existing = list(csv.DictReader(open(out))) if os.path.exists(out) else []
        # Same key as the collectors dedup on, so re-submitting a test corrects
        # it rather than recording the same water twice.
        prior = [r for r in existing if r.get("measured") == row["measured"]]
        keep = [r for r in existing if r.get("measured") != row["measured"]]
        replaced = len(existing) - len(keep)
        # A CORRECTION IS NOT A DELETION OF EVERYTHING ELSE.
        #
        # `row` starts blank and only the measures in THIS submission are
        # filled, so re-submitting one corrected number dropped the other four
        # from the row — a five-measure test became a one-measure test, and the
        # response said "replaced" without saying what had gone. The form does
        # not prefill, so the ordinary way to fix a mistyped pH deleted the
        # alkalinity, calcium, CYA and chlorine beside it.
        #
        # The measures carried over are named in the response, so "replaced" is
        # no longer the whole story the caller gets.
        carried = []
        if prior:
            was = prior[-1]
            for key in MANUAL_RANGE:
                if not row.get(key) and was.get(key):
                    row[key] = was[key]
                    carried.append(key)
            # The note is the person's own words about that reading; an empty
            # one in a partial resubmit is not an instruction to erase it.
            if not row.get("note") and was.get("note"):
                row["note"] = was["note"]
        keep.append(row)
        keep.sort(key=lambda r: r.get("measured", ""))
        locking.rewrite_locked(out, MANUAL_COLS, keep)
    audit.record("reading.manual", row["measured"],
                 f"{label}: " + ", ".join(f"{k}={row[k]}" for k in MANUAL_RANGE if row[k]),
                 by=who, cfg=cfg)
    return {"recorded": row["measured"], "measures": got,
            "replaced": bool(replaced), "carried": sorted(carried),
            "source": label}, None

def save_correction(data, cfg, who):
    """Drop or override one lab reading.

    Not a delete. Both labs return their whole history on every pull and the
    collectors dedup on the measurement timestamp, so a row removed from the CSV
    is put back by the next cron run — a fix that appears to work and undoes
    itself overnight. This records the correction instead, and the loaders apply
    it every time they read.
    """
    from . import corrections as CORR
    src = _s(data.get("source")).strip()
    measured = _s(data.get("measured")).strip()
    action = _s(data.get("action")).strip()
    if src not in CORR.SOURCES:
        return None, f"unknown source {src!r}"
    if not measured:
        return None, "which reading? a measured timestamp is needed"
    if action not in ("drop", "set", "keep"):
        return None, "action must be drop, set or keep"

    field = _s(data.get("field")).strip()
    value = data.get("value", "")
    if action == "set":
        if not re.fullmatch(r"[a-z_]{1,32}", field):
            return None, "that is not a measurement name"
        # Stored as text because that is what the CSV holds, but it has to BE a
        # number, AND a number this measurement could actually take — the same
        # check the by-hand reading form applies, which is what this route did
        # not do. See measure_value().
        value, err = measure_value(field, value)
        if err:
            return None, err

    try:
        CORR.record(src, measured, action, field, value,
                    _s(data.get("note")).strip(), who, cfg)
    except ValueError as e:
        return None, str(e)
    return {"corrected": src, "measured": measured, "action": action,
            "field": field}, None


def combined_volume(pool_gal, spa_gal, shares):
    """Which body of water a dose is measured against.

    One expression, two callers. It lived only inside save_pool_shape, so the
    Settings checkbox -- whose own label promises "the figures stay separate
    either way, so this can change later without re-tracing anything" -- wrote
    spa_shares_water to config.toml and recomputed NOTHING. Unticking it left
    the combined pool+spa figure in place and every dose on Chemicals and Pool
    chemistry kept multiplying through hundreds of gallons the product had just
    been told were behind a valve, while the save reported success.
    """
    return (pool_gal or 0) + (spa_gal or 0) if shares else (pool_gal or 0)


def save_settings(data, cfg):
    # REFUSE A SAVE THAT WILL NOT SURVIVE. When POOLHOUND_DATA is set this is a
    # deployment with a durable data directory, and a config.toml anywhere else
    # is inside the container -- so every setting written to it (volume, targets,
    # notification settings) is destroyed by the next --force-recreate while the
    # page reports each save as succeeding. Better to refuse and say where the
    # file belongs than to accept and lose it silently.
    durable = os.environ.get("POOLHOUND_DATA")
    if durable:
        want = os.path.join(os.path.expanduser(durable), "config.toml")
        have = os.path.abspath(cfg.get("_source") or "")
        if have != os.path.abspath(want):
            return None, (f"this install keeps its data in {durable}, but the "
                          f"config it loaded is at {have} — which is not durable "
                          f"here, so a saved setting would be lost on the next "
                          f"rebuild. Move it to {want} and restart.")
    updates, bad = {}, []
    for field, raw in data.items():
        if field not in SETTABLE:
            continue                      # ignored, never merged blindly
        section, key, coerce = SETTABLE[field]
        try:
            val = coerce(raw)
        except (TypeError, ValueError):
            val = None
        if val is None:
            bad.append(field)
        else:
            updates[(section, key)] = val        # may be CLEAR
    if bad:
        # NAME THE FIELD AND SAY WHAT WOULD BE ACCEPTED. Still all-or-nothing:
        # the browser posts the whole form, and applying the half of a form that
        # parsed would leave the install in a state nobody typed — a volume from
        # this save beside a sanitiser from the last one. So the whole save is
        # refused, and the refusal has to carry enough for the person to fix it
        # in one go, because they cannot find the bad field by elimination.
        return None, "nothing was saved — " + "; ".join(
            f"{f}: {WHY.get(f, 'that value was not accepted')}"
            for f in sorted(bad))
    if not updates:
        return None, "nothing to save"

    # Changing which body of water a dose is diluted by changes the volume that
    # dose is measured against. Both figures are already on disk, side by side,
    # displayed disabled two fields to the left of the checkbox -- so the answer
    # is derivable and nothing derived it. Reported back as a derived value, so
    # the page can show what it became rather than implying nothing moved.
    derived = {}
    if ("pool", "spa_shares_water") in updates:
        pool_only = num_or_none((cfg.get("pool") or {}).get("pool_only_gallons"))
        spa_only = num_or_none((cfg.get("pool") or {}).get("spa_gallons"))
        if pool_only is not None:
            shares = bool(updates[("pool", "spa_shares_water")])
            gal = combined_volume(pool_only, spa_only or 0, shares)
            updates[("pool", "volume_gallons")] = float(gal)
            derived["pool.volume_gallons"] = float(gal)

    edit_toml(cfg["_source"], updates)
    out = {"saved": {f"{s}.{k}": ("(cleared)" if v is CLEAR else v)
                     for (s, k), v in updates.items()}}
    if derived:
        out["derived"] = derived
    return out, None


def num_or_none(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ---------------------------------------------------------------- export
# The owner's own history, in a form they can keep.
#
# There was no route at all: the record lives on an Azure Files share mounted on
# a VM they do not log into, behind a managed identity that belongs to the VM,
# and the site is behind Entra. Their only two routes to a multi-year history of
# their own pool were `az ssh` to the server or an Azure portal sign-in. Printing —
# the de facto export — prints forty rows of nine thousand.
#
# An allowlist of names, not a path the caller supplies: this reads files from
# the data directory and a caller-chosen filename is a directory traversal
# waiting to be written. Anything not named here cannot be fetched.
# What each file COSTS to read. Priced by the DATA, not by the route.
#
# /api/export needs admin because "a view account could download every reading,
# every dose, every settings change and audit.csv itself -- the record of who
# did what". /api/rows was then added at `view`, reading the same allowlist out
# of the same directory, and a view account could page the entire audit log 500
# rows at a time. Both tables agreed with themselves, so the authz selftest
# passed; nothing asked whether two routes over the same bytes cost the same.
#
# A file absent from here costs `admin`, so a new one is shut until somebody
# prices it -- the direction access.NEEDS already chose for routes.
FILE_NEEDS = {
    "samples.csv":     "view",   # equipment state, already on the public page
    "readings.csv":    "view",
    "lab.csv":         "view",
    "leslies.csv":     "view",
    "manual.csv":      "view",
    "wg_targets.csv":  "view",
    "corrections.csv": "view",
    "chemicals.csv":   "operate",  # what was added, and who added it
    "pool_shape.json": "operate",
    "audit.csv":       "admin",    # who changed what, including credentials
}

EXPORTABLE = {
    "samples.csv":     "AqualinkD equipment samples",
    "readings.csv":    "WaterGuru pod readings",
    "lab.csv":         "WaterGuru lab results",
    "leslies.csv":     "Leslie's lab results",
    "manual.csv":      "Readings entered by hand",
    "chemicals.csv":   "Doses logged",
    "corrections.csv": "Corrections applied to readings",
    "audit.csv":       "Who changed what",
    "wg_targets.csv":  "WaterGuru's own target ranges",
    # NOT a CSV, and the only artefact in here the owner made by hand rather
    # than collected. Twenty minutes of tracing an outline over a photograph,
    # with its scale point, depth model, ledge and bench -- on the tab whose
    # whole argument is that a traced outline beats a remembered 0.85 -- and it
    # could not be got out of the product. The photograph is deliberately never
    # stored, so this JSON is the entire record of that work.
    "pool_shape.json": "The traced pool and spa outline",
}

def export_one(name, cfg):
    """One CSV, as bytes. None when the name is not exportable or not there."""
    if name not in EXPORTABLE:
        return None
    p = os.path.join(config.data_dir(cfg), name)
    if not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        return f.read()

# How many rows one /api/rows call may return. A year of samples is ~35,000
# rows and nobody wants that in a fetch; the caller pages by narrowing the
# dates, and the response says when it truncated so the page can say so too.
ROWS_MAX = 500

def rows_slice(name, cfg, since="", until="", limit=ROWS_MAX):
    """A date-bounded slice of one CSV, newest first.

    WHY THIS EXISTS

    The data tables render at most MAX_RENDER rows and show DEFAULT_SHOW of
    them, and they say so -- "showing 40 of 387", "11,120 older rows were not
    searched". That honesty was added because silent truncation is the worse
    failure. But admitting a limit is not the same as lifting it: on this
    install the controller writes 96 rows a day, so by the time a reader wants
    last month the page cannot reach it at all and the only route to it was
    downloading the whole file.

    Allowlisted by name, like export_one, because this reads files from the
    data directory and a caller-supplied path is a directory traversal waiting
    to be written.
    """
    if name not in EXPORTABLE or not name.endswith(".csv"):
        return None
    p = os.path.join(config.data_dir(cfg), name)
    if not os.path.exists(p):
        return None
    # CORRECTIONS ARE APPLIED HERE TOO, because this slice REPLACES the table
    # that was built with them.
    #
    # This read the CSV raw while the table it overwrites comes from
    # render.rows(), which applies corrections.csv — so one click on Fetch
    # resurrected a reading the owner had explicitly dropped and reverted a
    # corrected value, in place, with no notice. Measured: render.rows("lab.csv")
    # gave ta 73 where /api/rows gave 95, and /api/rows returned the 2026-05-21
    # Leslie's row the owner had dropped.
    #
    # The comment that used to sit here said these are "the same names the
    # loaders use, so a slice cannot disagree with the table it fills". The
    # column names were the same and the READ was not. CLAUDE.md: "render.py
    # owns the loaders... Two tabs must not be able to disagree about what the
    # current alkalinity is" — and corrections exist precisely so that a fix
    # does not undo itself.
    from . import render as _render
    TS = ("ts", "measured", "at", "recorded")
    out, total = [], 0
    with open(p, newline="") as f:
        _all = list(csv.DictReader(f))
    _src = _render.CORRECTABLE.get(name)
    if _src:
        from . import corrections as _CORR
        _all = _CORR.apply(_all, _src)
    for r in _all:
        key = next((r.get(c) for c in TS if r.get(c)), "")
        if since and key < since:
            continue
        # `until` INCLUDES the day it names. These are string compares
        # against ISO timestamps, so a bare "2026-08-03" sorts before
        # "2026-08-03T00:13:00" and excluded the whole of the third -- an
        # off-by-one-day that reads as missing data rather than as a
        # boundary choice. A caller who wants an exclusive bound passes a
        # full timestamp.
        if until and key > (until + "\uffff" if len(until) <= 10 else until):
            continue
        total += 1
        out.append(r)
    out.sort(key=lambda r: next((r.get(c) for c in TS if r.get(c)), ""), reverse=True)
    return {"file": name, "rows": out[:limit], "matched": total,
            "returned": min(total, limit),
            # Say it, rather than leaving the caller to infer it from a count
            # that happens to equal the cap.
            "truncated": total > limit, "limit": limit}


def export_zip(cfg):
    """Everything, as a zip. Built in memory — the whole history is a few MB."""
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        readme = ["poolhound export", "=" * 16, ""]
        for name, what in EXPORTABLE.items():
            blob = export_one(name, cfg)
            if blob is None:
                continue
            z.writestr(f"poolhound/{name}", blob)
            # Line counts describe a CSV. For the JSON outline they would be
            # meaningless, so it gets its size instead of a number that looks
            # like a row count and is not one.
            if name.endswith(".csv"):
                readme.append(f"{name:18} {what}  ({blob.count(chr(10).encode()[0])} lines)")
            else:
                readme.append(f"{name:18} {what}  ({len(blob):,} bytes)")
        readme += ["", "These are the files poolhound stores, unmodified. Corrections",
                   "in corrections.csv are applied when the page is rendered and are",
                   "NOT folded into readings.csv — the raw response is kept as the",
                   "record of what the lab actually said.",
                   "",
                   # The README asserted completeness, so an owner who exported
                   # before a rebuild believed they had everything and then had
                   # to re-enter every setting. Say what is left out and why.
                   "What is NOT here: config.toml, which holds this install's",
                   "settings and the PATHS to its credentials. It is deliberately",
                   "left out so an export can be shared without thinking about it.",
                   "Copy it yourself from the data directory if you are moving",
                   "the install rather than taking your history elsewhere.",
                   "",
                   "pool_shape.json reloads on the Pool Volume tab. The photograph",
                   "it was traced over is never stored, by design, so choose the",
                   "picture again if you want to re-trace it — the depths and the",
                   "volume work without it."]
        z.writestr("poolhound/README.txt", "\n".join(readme) + "\n")
    return buf.getvalue()

class Handler(http.server.SimpleHTTPRequestHandler):
    def list_directory(self, path):
        """Never. A directory index is not a page this product has.

        With an un-rendered site volume the base class served an index of / --
        a 200 that advertised admin.html by name and contained nothing else.
        Failing closed here means a missing render reads as a missing render.
        """
        self.send_error(404, "no such page")
        return None

    # True while answering a HEAD. See do_HEAD.
    _head_only = False

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=config.site_dir(config.load()), **kw)

    def do_HEAD(self):
        """Exactly what GET would answer, minus the body.

        A BOUNDARY IS A PROPERTY OF THE REQUEST, NOT OF THE METHOD. Both guards
        that are supposed to be unconditional — the Host check against DNS
        rebinding and the /admin.html sign-in check — were attached to do_GET and
        do_POST, and SimpleHTTPRequestHandler's inherited do_HEAD walked past
        both. No body came back, but the Content-Length and Last-Modified of the
        AUTHENTICATED build did, to an anonymous caller, with any Host it liked:
        the Last-Modified is the timestamp of the last settings write, so a
        poller learned when the household touched the pool and roughly how much
        the private page had changed. `HEAD /admin.html` answered 200 while
        `GET /admin.html` answered 401, and `HEAD` with a forged Host answered
        200 while `GET` answered 421.

        So HEAD runs do_GET with the body suppressed, rather than a second copy
        of the gate list that can drift out of step with the first. The flag is
        read by _json, _download and the static branch — the three places that
        write a body — which is also what makes the response headers identical
        to the ones GET would have sent, as HEAD is defined to be.
        """
        self._head_only = True
        try:
            self.do_GET()
        finally:
            self._head_only = False

    def _serve_static(self):
        """The file handler, honouring _head_only."""
        if self._head_only:
            f = self.send_head()
            if f:
                f.close()
            return
        return super().do_GET()

    def log_message(self, fmt, *args):
        # The credential route is logged by name only. Bodies are never logged
        # anywhere, but naming the route in the same breath as a password is the
        # kind of habit that eventually puts one in a file.
        if "/api/" in (self.path or ""):
            sys.stderr.write("  %s %s\n" % (self.command, self.path))

    def _download(self, blob, filename, ctype):
        """Send bytes as a file. The filename is ours, never the caller's."""
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:80]
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Content-Disposition", f'attachment; filename="{safe}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not self._head_only:
            self.wfile.write(blob)

    def _json(self, code, body):
        # allow_nan=False. A volume of "1e400" coerced to float('inf') and this
        # emitted the bare token Infinity, which is not JSON: the browser's save
        # handler threw parsing the response to a write that had SUCCEEDED, and
        # took the rest of the page script with it — the failure mode this
        # codebase already has history with. A non-finite number now fails here,
        # loudly, instead of reaching a parser that cannot read it.
        try:
            raw = json.dumps(body, allow_nan=False).encode()
        except ValueError:
            raw = json.dumps({"error": "the server computed a value that is not a "
                                       "finite number; nothing was saved"}).encode()
            code = 500
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not self._head_only:
            self.wfile.write(raw)

    # ---------------------------------------------------------------- guards
    def decided_path(self):
        """The request path as the FILE HANDLER will resolve it.

        SimpleHTTPRequestHandler percent-decodes and normalises inside
        translate_path — after do_GET has already decided, from the raw
        self.path, whether this request needs an identity. So the gate below was
        deciding about a different string than the one that chose the file, and
        /./admin.html, //admin.html, /foo/../admin.html and /%2e/admin.html each
        served the full authenticated build to an anonymous caller while plain
        /admin.html was correctly refused.

        Production was never exposed, because Caddy normalises before proxying.
        That is exactly why this mattered: this check exists for the day the
        proxy rule stops matching, and its own comment promises it holds
        "whatever the proxy did with the path". Deciding on the resolved path is
        what makes that true.
        """
        p = urllib.parse.unquote(self.path.split("?")[0].split("#")[0])
        p = posixpath.normpath(p)
        # POSIX keeps a leading "//" as significant; a URL path does not.
        while p.startswith("//"):
            p = p[1:]
        return p

    def host_ok(self):
        """Reject a forged Host, which is what DNS rebinding looks like here."""
        return (self.headers.get("Host") or "").lower() in ALLOWED_HOSTS

    def origin_ok(self):
        """No Origin means it was not a browser. An Origin that is not ours is
        a cross-site request and is refused whatever it claims to be."""
        o = self.headers.get("Origin")
        return o is None or o in ALLOWED_ORIGINS

    def token_ok(self):
        return hmac.compare_digest(self.headers.get(TOKEN_HEADER, ""), TOKEN)

    def agent_ok(self):
        """Bearer auth for the agent, which cannot do an interactive login.

        Compared in constant time, and a missing token on this side means the
        route is closed rather than open — an unconfigured server must not
        accept an unauthenticated agent.

        Returns (ok, unavailable). `unavailable` separates "this server has no
        token configured, so the answer is no" from "this server could not find
        out", which used to be the same 403 and the same silence.
        """
        want = agent_token()
        if not want:
            return False, bool(_agent_token_error[0])
        got = (self.headers.get("Authorization") or "")
        if not got.startswith("Bearer "):
            return False, False
        presented = got[7:].strip()
        if hmac.compare_digest(presented, want):
            _on_previous_token[0] = 0.0
            return True, False
        # THE SECOND SLOT, AND ONLY INSIDE ITS WINDOW. With one token there was
        # no correct order to rotate in: whichever end you wrote first, the
        # other was refused until somebody reached it, and the samples inside
        # that gap are gone. vault.agent_token_previous() is None whenever no
        # rotation is in progress, which is nearly always, and it already
        # refuses a slot with no deadline, an unreadable deadline, a deadline
        # that has passed, and a value identical to the current token.
        prev = vault.agent_token_previous()
        if prev and hmac.compare_digest(presented, prev[0]):
            # RECORDED, because a rotation nobody finishes looks exactly like
            # one that is done. /api/health reports this, so the half-finished
            # state is on the page rather than in somebody's memory.
            _on_previous_token[0] = time.time()
            return True, False
        return False, False

    def signed_in(self):
        """Did a proxy vouch for a person?

        whoami() has TWO not-a-person answers -- "local" (loopback, no proxy)
        and "anonymous" (off-box, proxy sent no identity). Five call sites tested
        `!= "local"` and meant "signed in", so adding the second sentinel made
        every anonymous visitor look signed in: / redirected them to
        /admin.html, which Caddy gates, and the public site became unreachable.
        One predicate, so the two sentinels cannot drift apart again.
        """
        return self.whoami() not in ("local", "anonymous")

    def whoami(self):
        """Who the proxy says this is. 'local' when there is no proxy, which is
        the loopback case where being on the machine is the authentication."""
        # IS THIS REQUEST ACTUALLY FROM THE PROXY? Asked before any identity
        # header is read, because until it is answered they are just strings a
        # client chose. See PROXY_SECRET.
        if PROXY_SECRET and not hmac.compare_digest(
                (self.headers.get(PROXY_AUTH_HEADER) or "").strip(), PROXY_SECRET):
            # Fall through to the peer test rather than returning here: a
            # loopback caller is still 'local', which is what keeps a
            # workstation working with the secret set.
            return self._peer_identity()
        for h in IDENTITY_HEADERS:
            # .strip() BEFORE the truthiness test. A header whose value is only
            # whitespace — 0x0B and 0x0C reach here intact — used to return "",
            # which is neither an identity nor the "local" sentinel, and every
            # caller downstream had to guess which it meant.
            v = (self.headers.get(h) or "").strip()[:120]
            if v:
                return v
        # NO HEADER IS NOT THE SAME AS NO PROXY.
        #
        # "local" means "being on this machine IS the authentication", and
        # access.role() grants it admin unconditionally on that basis. True of a
        # loopback workstation; NOT true of a request that arrived from
        # somewhere else carrying no identity, which is what a misconfigured
        # proxy produces -- oauth2-proxy without --set-xauthrequest=true
        # forwards the request with no X-Auth-Request-* header at all, and every
        # such caller was landing on the admin sentinel.
        #
        # PUBLIC_HOST already refuses those at the write gate, so this was not
        # reachable on the deployed host. But it was one environment variable
        # away from reachable, and seeing that it was safe required reading two
        # distant facts together -- which is the reasoning this avoids by
        # answering differently.
        return self._peer_identity()

    def _peer_identity(self):
        """'local' for a loopback caller, 'anonymous' for anyone else."""
        peer = (self.client_address or ("",))[0]
        if peer in ("127.0.0.1", "::1", "::ffff:127.0.0.1", ""):
            return "local"
        return "anonymous"

    def end_headers(self):
        # The pages are regenerated after every write, so a cached copy is
        # always the stale one. This is a local tool; there is nothing to gain
        # from caching and a reload showing the previous state is a bug report.
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        # Deliberately no Access-Control-Allow-Origin: withholding it is what
        # stops a cross-origin page from reading the token out of /api/health.
        #
        # headers.py is the one statement of this set, NAMES INCLUDED, because
        # it used to be two that disagreed: this block sent four and the Caddy
        # vhost sent four, and the two fours were different ones.
        #
        # THE POLICY IS TAKEN FROM THE PAGE BEING SERVED, not written here.
        # The page loads nothing from anywhere and carries exactly one inline
        # script, so the policy names that script by digest rather than
        # allowing inline script wholesale — which is the one directive that
        # could not help with the XSS this project actually shipped.
        #
        # Derived per response rather than at render, because a render can
        # happen in a different process from the one serving; see headers.py.
        for name, value in HDR.for_response(self._csp()):
            self.send_header(name, value)
        super().end_headers()

    def _csp(self):
        """The policy for this response: the page's own, or the baseline.

        Anything that is not one of our HTML pages — JSON, a CSV export, a 404,
        a 421 — gets `script-src 'none'`, because nothing in those executes and
        a response that does not need a permission does not get one.
        """
        path = self.path.split("?")[0].split("#")[0]
        if not path.endswith(".html") and not path.endswith("/"):
            return HDR.BASELINE_CSP
        try:
            target = self.translate_path(self.path)
            # `/` IS A PAGE. translate_path answers the directory, and
            # SimpleHTTPRequestHandler then serves index.html out of it — so
            # asking the directory for a policy returns the baseline, which
            # refuses the landing page's own script. The public build is the
            # one served at `/`, which is to say the one every anonymous
            # visitor gets, so this is the case that must not be the one that
            # was overlooked.
            if os.path.isdir(target):
                target = os.path.join(target, "index.html")
            return HDR.csp_for_file(target)
        except Exception:
            # A policy is not worth a 500. The baseline refuses the page's own
            # script, which is a visible failure on a page nobody can use
            # anyway if this raised.
            return HDR.BASELINE_CSP

    def do_GET(self):
        if not self.host_ok():
            return self._json(421, {"error": "unrecognised Host"})
        if self.path.split("?")[0] == "/api/agent/commands":
            return self.stream_commands()
        # The authenticated page is never served without an identity, whatever
        # the proxy did with the path — including how it spelled it.
        #
        # CASE IS A SPELLING. This compared the path to two lowercase literals,
        # so /ADMIN.HTML matched neither and went straight to the file handler.
        # What was left holding the private build shut was the CASE SENSITIVITY
        # OF THE FILESYSTEM — ext4 in the container 404s it, APFS on a
        # workstation serves it — which is a mount option, not a boundary. The
        # compose file calls /site "a cache of what is in /data", one plausible
        # edit away from sitting on the CIFS share, which is case-insensitive.
        #
        # Entra held, because Caddy's `path` matcher lowercases before it
        # compares and so is case-insensitive where this was not. That is the
        # point: the two layers disagreed about which requests they were about,
        # and this one is the layer whose whole purpose is the day the proxy
        # rule stops matching. Refusing the uppercase spelling costs nothing
        # where the filesystem would have 404'd it — an anonymous caller gets
        # 401 rather than 404 for a file that is not there.
        if PUBLIC_HOST and self.decided_path().rstrip("/").lower() in ("/admin.html", "/admin"):
            if not self.signed_in():
                return self._json(401, {"error": "sign-in required"})
        # A SIGNED-IN VISITOR LANDING ON "/" GOT THE PUBLIC PAGE.
        #
        # The gate is on /admin.html, so the bare domain always served the
        # public build — which means anybody who signed in, then came back to
        # the site later, silently lost the refresh control, Pool control,
        # Chemicals and Settings, with nothing on the page explaining why. It
        # reads as features disappearing rather than as a different build.
        #
        # /index.html still serves the public page unconditionally, so the
        # public view remains reachable deliberately rather than only by
        # signing out.
        if (PUBLIC_HOST and self.decided_path().rstrip("/") in ("", "/")
                and self.signed_in()):
            self.send_response(302)
            self.send_header("Location", "/admin.html")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return

        route = self.decided_path()
        if route == "/api/export" or route.startswith("/api/export/"):
            # Somebody's whole pool history. Same gate as the authenticated
            # page: a signed-in person only, and never an anonymous caller on a
            # public host. The Caddy @private list carries these paths too, so
            # this is the second of two locks rather than the only one.
            if PUBLIC_HOST and not self.signed_in():
                return self._json(401, {"error": "sign-in required"})
            # AND the role. access.NEEDS says this route is admin, and it said so
            # while nothing consulted it: the authorization check lives in
            # do_POST and the export is a GET, so a `view` account could download
            # every reading, every dose, every settings change and audit.csv
            # itself — the record of who did what. A table that declares a
            # permission nothing enforces is worse than no table, because the
            # next person reads it and believes it.
            ok, need, have = access.may(self.whoami(), "/api/export", config.load())
            if not ok:
                return self._json(403, {"error": f"this needs {need} access and "
                                                 f"you have {have}",
                                        "need": need, "have": have})
            if route == "/api/export":
                blob = export_zip(config.load())
                stamp = dt.datetime.now().strftime("%Y%m%d")
                return self._download(blob, f"poolhound-{stamp}.zip",
                                      "application/zip")
            name = route[len("/api/export/"):]
            blob = export_one(name, config.load())
            if blob is None:
                return self._json(404, {"error": "no such export"})
            return self._download(blob, name, "text/csv; charset=utf-8")

        if self.path.split("?")[0] == "/api/health":
            if not self.origin_ok():
                return self._json(403, {"error": "cross-origin request refused"})
            from . import __version__ as ver
            who = self.whoami()
            # The session token is what unlocks writing, so a public visitor
            # does not get one. The page then renders read-only by itself
            # rather than offering buttons that would be refused.
            # signed_in IS SENT, so the page does not have to re-derive it.
            # This was the SIXTH call site of the sentinel bug and the only one
            # outside Python: the page tested `j.user !== 'local'`, which is
            # true of "anonymous", so every anonymous visitor to the public site
            # had their name rendered as the literal "anonymous" and was shown a
            # SIGN-OUT control -- the exact thing the comment beside that line
            # warns against, "a sign-out shown to somebody who is not signed in
            # is a dead control that makes the page look broken".
            #
            # The fix for the other five was one predicate; shipping its ANSWER
            # rather than its inputs is the same fix, applied across the wire.
            # A seventh sentinel would now change nothing on the page.
            # SAYS WHETHER THE TRUST BOUNDARY IS PROVEN, rather than leaving
            # it to be inferred. False means identity is being accepted on
            # docker network position alone -- see PROXY_SECRET. It is a
            # boolean about configuration, never the secret itself.
            body = {"ok": True, "api": 1, "version": ver, "started": STARTED,
                    "proxy_auth": bool(PROXY_SECRET),
                    "user": who, "public": bool(PUBLIC_HOST),
                    "signed_in": self.signed_in()}
            # THE SAME SENTINEL BUG AS THE REDIRECT, in the one response that
            # hands out the CSRF token. `who != "local"` meant "a person signed
            # in", and the moment whoami() gained a second not-a-person answer
            # every anonymous visitor to the public site was issued a session
            # token AND the private half of this body -- role, whether an agent
            # is connected, what is pending, and the panel's current equipment
            # states, which is operational detail about somebody's home.
            #
            # signed_in() covers both sentinels. Writes were still refused at the
            # proxy throughout, but the token is what makes the Origin/Host pair
            # meaningful and it is not something to hand to anybody who asks.
            if not PUBLIC_HOST or self.signed_in():
                body["token"] = TOKEN
                # Answered by the process that actually holds the SSE
                # connections, which is the only one that knows. Withheld from
                # anonymous callers: whether the house is currently reachable is
                # operational detail about somebody's home, not a public stat.
                # So the page can dim what this reader cannot use. The refusal
                # above is the boundary; this is courtesy, and the page is one
                # file for everybody so it has to be asked for at runtime.
                _cfg = config.load()
                body["role"] = access.role(who, _cfg)
                body["access_policy"] = access.configured(_cfg)
                body["agents"] = QUEUE.agents_connected
                body["agent_revision"] = getattr(QUEUE, "agent_revision", None)
                # Why the agent routes are closed, when they are closed for a
                # reason other than nobody having configured a token. Read from
                # the last attempt rather than by asking the credential store
                # again: a failing Key Vault lookup is not cached, and this
                # endpoint is polled every few seconds.
                body["agent_token_error"] = _agent_token_error[0]
                # WHETHER A ROTATION IS HALF DONE. The step people do not take
                # is the last one — retiring the outgoing token — and until
                # somebody takes it this install is accepting two credentials.
                # Both halves are reported because they are different facts: a
                # second slot exists at all, and the Pi is still presenting it.
                # Never the token or its fingerprint; a deadline and a boolean.
                _prev = vault.agent_token_previous()
                body["agent_token_rotating"] = (
                    _prev[1].isoformat() if _prev else None)
                body["agent_on_previous_token"] = bool(_on_previous_token[0])
                # What has been asked for but not yet seen in a reading...
                body["pending"] = pending_devices()
                # ...and what the panel currently says, so the page can correct
                # itself without a full reload. Relying on location.reload() to
                # deliver the confirmed state made the whole mechanism depend on
                # a page load landing and being fresh — and a browser holding
                # the pre-command render would show "waiting" indefinitely with
                # nothing wrong on the server at all.
                body["states"] = current_states()
            return self._json(200, body)
        if self.path.split("?")[0] == "/api/rows":
            # Gated like the export it slices: this is the household's own
            # history, and the whole point is that it reaches further back than
            # the page does.
            if PUBLIC_HOST and not self.signed_in():
                return self._json(401, {"error": "sign-in required"})
            _cfg = config.load()
            ok, need, have = access.may(self.whoami(), "/api/rows", _cfg)
            if not ok:
                return self._json(403, {
                    "error": f"this needs {need} access and you have {have}",
                    "needs": need, "have": have})
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            name = (q.get("file") or [""])[0]
            # The file's own price, on top of the route's. Without this a view
            # account reached audit.csv here that /api/export refuses them.
            need_file = FILE_NEEDS.get(name, "admin")
            if name in EXPORTABLE and not access.allows(have, need_file):
                return self._json(403, {
                    "error": f"{name} needs {need_file} access and you have {have}",
                    "needs": need_file, "have": have})
            # A PARAMETER THE RESPONSE REPORTS BACK IS ONE THE ROUTE ACCEPTS.
            #
            # `limit` was never read from the query and the response echoed
            # "limit": 500 as though it were the caller's, so ?limit=1 returned
            # 387 rows and said 500. And an unparseable date answered
            # {"matched": 0} — an authoritative "there are no rows" for a
            # question the route never understood, which is worse than an
            # error because the caller believes it.
            since = (q.get("since") or [""])[0][:32]
            until = (q.get("until") or [""])[0][:32]
            for label, v in (("since", since), ("until", until)):
                if v and not re.fullmatch(r"\d{4}-\d{2}(-\d{2}([T ].*)?)?", v):
                    return self._json(400, {
                        "error": f"{label}={v!r} is not a date. Use YYYY-MM-DD, "
                                 f"or a full timestamp."})
            raw_limit = (q.get("limit") or [""])[0]
            limit = ROWS_MAX
            if raw_limit:
                try:
                    limit = int(raw_limit)
                except ValueError:
                    return self._json(400, {
                        "error": f"limit={raw_limit!r} is not a whole number."})
                if limit < 1:
                    return self._json(400, {"error": "limit has to be at least 1."})
                limit = min(limit, ROWS_MAX)
            got = rows_slice(name, _cfg, since=since, until=until, limit=limit)
            if got is None:
                return self._json(404, {"error": f"no readable file named {name!r}",
                                        "available": sorted(n for n in EXPORTABLE
                                                            if n.endswith(".csv"))})
            return self._json(200, got)
        if self.path.split("?", 1)[0] == "/api/commands":
            # The control tab's history was BAKED INTO THE RENDER. Cron renders
            # every six hours through `docker compose exec`, which is a
            # different process with an empty queue -- so twice a day the page
            # erased the command history and asserted "Nothing issued yet this
            # session. ... it starts empty after a restart", a restart that had
            # not happened, while the serving process still held the commands.
            # That is the defect README records as fixed for the offline banner
            # and left unfixed for the table beside it.
            #
            # Two halves, because they answer different questions. `session` is
            # this process's ring: what was issued and what the agent said back.
            # `durable` is audit.csv on the share, which survives a restart --
            # and which the page has been pointing readers at while giving them
            # no way to read it. audit.load() had zero callers.
            # The same door /api/export uses: behind a proxy, an identity the
            # proxy vouched for. "local" reaching a PUBLIC_HOST process means no
            # proxy header arrived, which is an anonymous caller, not the owner
            # at the console.
            if PUBLIC_HOST and not self.signed_in():
                return self._json(401, {"error": "sign-in required"})
            _cfg = config.load()
            ok, need, have = access.may(self.whoami(), "/api/commands", _cfg)
            if not ok:
                return self._json(403, {
                    "error": f"this needs {need} access and you have {have}",
                    "needs": need, "have": have})
            try:
                durable = [r for r in audit.load(_cfg, limit=200)
                           if (r.get("action") or "").startswith("control")]
                # The same sentence the session half renders, rebuilt from what
                # the row kept, so the table reads identically before and after
                # a restart. Without it the durable half showed the DEVICE in
                # the command column and the value asked for in the result
                # column, which is not what either column is for.
                for r in durable:
                    if r.get("action") == "control" and not r.get("what"):
                        # CMD.from_detail, not a split here. This built the
                        # value as a STRING, and describe() asks whether it is
                        # truthy -- so "0" was true and every switch-OFF in
                        # this half of the table rendered as ON, which is the
                        # half that survives a restart.
                        r["what"] = CMD.describe(
                            CMD.from_detail(r.get("object", ""),
                                            r.get("detail") or ""), _cfg)
            except Exception:
                durable = []
            # AND WHAT WAS SWITCHED WITHOUT US, which is most of it. Derived
            # from the samples rather than from any log, because the panel
            # keeps none — see switchlog.py. Served here rather than rendered
            # for the same reason the two halves above are: a render can happen
            # in a different process from the one serving, and this table has
            # already been erased twice a day once.
            #
            # The whole audit is passed, not `durable`: that list is filtered to
            # 200 rows and sliced to 50 for display, and an attribution that
            # depended on a display limit would credit a person's command to the
            # panel as soon as the table got long enough.
            try:
                sp = os.path.join(config.data_dir(_cfg), "samples.csv")
                with open(sp, newline="", encoding="utf-8") as f:
                    _samples = list(csv.DictReader(f))
                # SUMMARISED OVER THE WHOLE RECORD, PAGINATED AFTERWARDS.
                # Built from the sliced list, the sentence read "120 change(s)
                # in the record" when the record held 272 -- the limit reported
                # as the total, which is the truncation that says nothing about
                # itself.
                _audit = audit.load(_cfg, limit=10 ** 6)
                # THE FILTERS ARE APPLIED TO THE WHOLE OF IT, in one place, by
                # a pure function the suite can give a record to. Filtering in
                # the page would have searched whatever slice the page happened
                # to hold and reported a count for the rest.
                q = urllib.parse.parse_qs(
                    urllib.parse.urlparse(self.path).query)
                act = switchlog.activity(
                    _samples, _audit, QUEUE.history(200), _cfg,
                    device=(q.get("device") or [""])[0],
                    how=(q.get("how") or [""])[0],
                    days=(q.get("days") or [""])[0] or None,
                    page=(q.get("page") or ["1"])[0],
                )
            except Exception:
                act = {"rows": [], "matched": 0, "total": 0, "page": 1,
                       "pages": 1, "per": switchlog.PER_PAGE, "note": "",
                       "from": 0, "to": 0}
            return self._json(200, {"session": QUEUE.history(20),
                                    "held": len(QUEUE.history(10 ** 9)),
                                    "durable": durable[:50],
                                    "durable_total": len(durable),
                                    "activity": act})
        if self.path.startswith("/api/"):
            return self._json(404, {"error": "no such endpoint"})
        return self._serve_static()

    def stream_commands(self):
        """Hold the connection open and write commands as they are issued.

        The heartbeat is not decoration. It keeps the NAT mapping alive on the
        house side and gives the agent something to time out against, so a link
        that has silently died is noticed in about 40 seconds rather than
        whenever the next command happens to be sent — which could be days.
        Two heartbeats, not one: see HEARTBEAT_S above for why, and for the
        measurement.
        """
        if self._head_only:
            # A HEAD on an endpoint whose whole answer is an open stream has
            # nothing to say, and starting one for a request that will read no
            # body leaves a subscriber nobody drains.
            return self._json(405, {"error": "not allowed"})
        if not self.agent_ok():
            return self._json(403, {"error": "agent authentication failed"})
        # What the Pi is running, so --check can compare it to what was shipped
        # instead of reading an mtime rsync copied from the source checkout.
        QUEUE.agent_revision = (self.headers.get("X-Poolhound-Revision")
                                or "unknown")[:60]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")   # nginx must not buffer this
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        q = QUEUE.subscribe()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    cmd = q.get(timeout=HEARTBEAT_S)
                    # Evicted at the subscriber cap. Close the stream so the
                    # agent reconnects: without this it held an open connection
                    # against a queue nobody would ever put a command into
                    # again, and reported itself healthy the whole time.
                    if cmd is QUEUE.CLOSED:
                        self.wfile.write(b": closing, evicted at the subscriber "
                                         b"cap; reconnect\n\n")
                        self.wfile.flush()
                        return
                    payload = json.dumps(cmd).encode()
                    self.wfile.write(b"data: " + payload + b"\n\n")
                except Exception as e:
                    if not isinstance(e, __import__("queue").Empty):
                        raise
                    self.wfile.write(b": hb\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass                                   # the agent went away; normal
        finally:
            QUEUE.unsubscribe(q)

    def do_OPTIONS(self):
        # No CORS preflight is ever granted. A cross-origin caller that needs
        # one is, by definition, trying to do something this API does not offer.
        self._json(405, {"error": "not allowed"})

    def do_POST(self):
        route = self.path.split("?")[0]
        if not self.host_ok():
            return self._json(421, {"error": "unrecognised Host"})

        # Agent routes authenticate with a bearer token instead of the session
        # token and origin pair, because a daemon has no page to load one from.
        if route.startswith("/api/agent/"):
            ok, unavailable = self.agent_ok()
            if not ok:
                # 503, not 403, when this server could not find out what the
                # token is. The agent retries a 503 and an operator reading the
                # Pi's log is told to look at the server rather than at the
                # token they just copied; a 403 said "your credential is wrong"
                # about a credential that was never checked.
                if unavailable:
                    return self._json(503, {
                        "error": "this server cannot check agent credentials right "
                                 "now — its own credential store did not answer. "
                                 "The reason is in the server log.",
                        "retry": True})
                return self._json(403, {"error": "agent authentication failed"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > 256_000:
                    return self._json(413, {"error": "too large"})
                body = json.loads(self.rfile.read(n) or b"{}")
                # THE GUARD NAMED THE CASE IT COULD NOT CATCH. This wrapped
                # json.loads alone, so a body that PARSES but is not an object —
                # `[1,2]`, `"hello"`, `42` — sailed past it and raised
                # AttributeError on the next `.get`, and the caller got no HTTP
                # status at all. To the Pi's agent that is indistinguishable
                # from the link dropping, and each one wrote a traceback into
                # the server's log. The sibling check on the ordinary write path has
                # had this isinstance test all along.
                if not isinstance(body, dict):
                    raise ValueError("not an object")
            except Exception:
                return self._json(400, {"error": "expected a JSON object"})
            if route == "/api/agent/sample":
                # ingest_sample re-renders both builds itself, outside the
                # samples lock, on a stored sample only. This used to render
                # here as well, so every stored sample rendered admin.html
                # twice — once inside the lock and once out here.
                r, e = ingest_sample(body, config.load())
            elif route == "/api/agent/note":
                r, e = record_panel_change(body, config.load())
            elif route == "/api/agent/ack":
                # AN ACK NAMES A COMMAND, OR IT IS NOT AN ACK.
                #
                # Nothing was validated: an absent id became "", a JSON null
                # became the literal string "None", and an id that was never
                # issued was accepted — each one writing a PERMANENT
                # control.ack row to audit.csv on the share, and each rendered
                # in the Pool control log as a command whose name is blank or
                # the word "None". The queue forgets on restart; audit.csv does
                # not.
                cmd_id = str(body.get("id") or "")[:64]
                if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", cmd_id):
                    return self._json(400, {"error": "an ack needs the id of the "
                                                     "command it answers"})
                outcome = {k: v for k, v in body.items() if k != "id"}
                # An id this process never issued is not refused — the server may
                # have restarted since, and the agent is right to report what it
                # did — but it must not be written to the permanent record as a
                # command, because nothing can ever pair it with one.
                known = QUEUE.knows(cmd_id)
                QUEUE.ack(cmd_id, outcome)
                # THE OTHER HALF OF A CONTROL ROW. "control" is recorded when a
                # command is issued and the answer was kept only in this
                # process's ring, which a restart or a second process empties --
                # so the durable trail said the spa heater had been ASKED for
                # and never whether the panel did it. One row per command, not
                # per sample: /api/agent/sample fires every fifteen minutes and
                # auditing that would be 35,000 rows a year of "a reading
                # arrived", which is what samples.csv already is.
                if known:
                    audit.record("control.ack", cmd_id,
                                 ", ".join(f"{k}={v}" for k, v in sorted(outcome.items()))[:400]
                                 or "no result reported",
                                 by="agent", cfg=config.load(), id=cmd_id)
                r, e = {"ok": True}, None
            else:
                return self._json(404, {"error": "no such endpoint"})
            return self._json(400, {"error": e}) if e else self._json(200, r)
        if not self.origin_ok():
            return self._json(403, {"error": "cross-origin request refused"})

        # The calculator's two pure routes skip the session token and the
        # identity check, because a public visitor is issued neither. The Origin
        # check above still applies: it is not protecting state here — there is
        # none — but it keeps another site from silently using this box as a
        # free image-processing endpoint on its own pages.
        public_route = route in PUBLIC_POST
        if public_route:
            if not public_rate_ok():
                return self._json(429, {"error": "the calculator is busy; try again in "
                                                 "a minute"})
        elif not self.token_ok():
            return self._json(403, {"error": "missing or bad token — reload the page"})

        # Every write, and every control, needs a person behind it once this is
        # reachable from outside. The proxy is supposed to guarantee that, and
        # this refuses anyway.
        #
        # Not redundancy for its own sake: the failure being defended against is
        # a proxy rule that stops matching — a path renamed, a location block
        # edited, an Easy Auth setting cleared during an unrelated change. That
        # failure is silent and it fails OPEN. Checking here means it fails
        # closed instead, and the cost is one header comparison.
        if PUBLIC_HOST and not public_route and not self.signed_in():
            return self._json(401, {"error": "this needs a signed-in user. If you are "
                                             "seeing this from outside, the "
                                             "authentication in front of poolhound is "
                                             "not passing an identity header."})
        # Authentication said WHO. This says WHAT THEY MAY DO, which nothing
        # asked before: every write route was open to anyone the proxy admitted,
        # so a guest on a shared sign-in had the heaters and the credential
        # store. An install with no policy is unchanged — see access.py — and a
        # route nobody has classified needs admin, so a new endpoint is shut
        # until somebody opens it rather than open until somebody remembers.
        if not public_route:
            ok, need, have = access.may(self.whoami(), route, config.load())
            if not ok:
                return self._json(403, {
                    "error": f"this needs {need} access and you have {have}",
                    "need": need, "have": have})

        if not public_route and not rate_ok():
            return self._json(429, {"error": "too many writes; slow down"})
        if not route.startswith("/api/"):
            return self._json(404, {"error": "no such endpoint"})
        # A photograph is megabytes; everything else is a form. One limit for
        # both would either reject photos or let a stray request allocate 60 MB.
        limit = 60 * 1024 * 1024 if route == "/api/photo" else 64_000
        if route == "/api/photo":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > limit:
                    return self._json(413, {"error": "image larger than 60 MB"})
                raw = self.rfile.read(n)
            except (OSError, ValueError):
                return self._json(400, {"error": "could not read the upload"})
            # BOUNDED, because the rate limiter counts arrivals and not work
            # in flight. PUBLIC_RATE allows 30 a minute from anyone on the
            # internet; thirty merely-large photos decoding at once is the same
            # OOM the pixel ceiling closes for one. A semaphore turns that into
            # a queue of two and a 503 for the rest, which is backpressure
            # rather than a dead container.
            if not _PHOTO_SLOTS.acquire(blocking=False):
                return self._json(503, {
                    "error": "the photo tracer is busy; try again in a moment",
                    "retry": True})
            try:
                result, err = pool_shape.prepare_photo(raw)
            finally:
                _PHOTO_SLOTS.release()
            if err:
                return self._json(400, {"error": err})
            return self._json(200, result)

        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                return self._json(413, {"error": "too large"})
            data = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(data, dict):
                raise ValueError
        except Exception:
            return self._json(400, {"error": "expected a JSON object"})

        cfg = config.load()
        # EVERY DIAGNOSTIC THESE WRITERS COMPOSE IS FOR A PERSON, and none of
        # them reached one. There was no handler here, so a LockTimeout or a
        # migrate refusal propagated out of do_POST, the socket closed with no
        # status line, and the browser's fetch() rejected with a bare network
        # error -- indistinguishable from the server going down. Measured: holding
        # the chemicals lock for 25s made a dose POST fail after 15s with
        # "Remote end closed connection without response", while the log carried
        # "another poolhound writer has held chemicals for more than 15s".
        #
        # 409, because both cases are "try again or fix the file", not "your
        # request was malformed" — and the exception's own text is the fix.
        try:
            return self._dispatch_write(route, data, cfg)
        except locking.LockTimeout as e:
            return self._json(409, {"error": str(e), "retry": True})
        except ValueError as e:
            return self._json(409, {"error": str(e), "retry": False})
        except Exception as e:
            # AND EVERYTHING ELSE, because the two above were the two that had
            # been THOUGHT OF. A write handler that raises anything else closed
            # the socket with no status line, and the comment above explains
            # exactly why that is the worst possible answer — the browser cannot
            # tell it from the server going down. Measured: a sweep of type-confusion
            # bodies ({"chemical": 5}, {"measured": 5}) left 11 of 14 probes with
            # no HTTP status. _s() turns those particular ones into ordinary
            # 400s now; this is here for the next class nobody has thought of.
            #
            # 500 with the exception's own text: these routes are behind the
            # session token and a signed-in identity, the reader is the operator,
            # and a message they can quote is worth more than a tidy one. The
            # traceback goes to the log, where it is of some use.
            traceback.print_exc()
            return self._json(500, {
                "error": f"the server failed while handling that: "
                         f"{type(e).__name__}: {e}. The traceback is in the log.",
                "retry": False})

    def _dispatch_write(self, route, data, cfg):
        if route == "/api/chemical":
            result, err = log_chemical(data, cfg, self.whoami())
        elif route == "/api/chemical/edit":
            result, err = edit_chemical(data, cfg, self.whoami())
        elif route == "/api/chemical/delete":
            result, err = delete_chemical(data, cfg, self.whoami())
        elif route == "/api/pool-shape":
            result, err = save_pool_shape(data, cfg, self.whoami())
        elif route == "/api/pool-shape/compute":
            result, err = compute_pool_shape(data, cfg)
        elif route == "/api/notify-test":
            result, err = send_test_notification(data, cfg, self.whoami())
        elif route == "/api/refresh":
            result, err = refresh_sources(data, cfg, self.whoami())
        elif route == "/api/credential":
            result, err = save_credential(data, cfg)
            if not err:
                audit.record("credential",
                             _s(data.get("service"))[:40],
                             "forgotten" if data.get("forget") else "set",
                             by=self.whoami(), cfg=cfg)
        elif route == "/api/credential/import":
            result, err = import_credentials(data, cfg, self.whoami())
        elif route == "/api/control":
            result, err = issue_control(data, cfg, self.whoami())
            # On the share, not in the queue. The queue is what is PENDING and
            # is right to be forgotten; this is what was ISSUED.
            if not err:
                audit.record("control", data.get("device") or data.get("action", ""),
                             f"{data.get('action','')}"
                             f"{'=' + str(data.get('value')) if data.get('value') is not None else ''}",
                             by=self.whoami(), cfg=cfg,
                             # The key its acknowledgement will carry, so the
                             # two halves of one event join on disk rather than
                             # being guessed at by timestamp. describe() is the
                             # same sentence the session log shows, so a
                             # restart changes what the table is READ FROM and
                             # not what it says.
                             id=(result or {}).get("id", ""))
        elif route == "/api/reading":
            result, err = save_reading(data, cfg, self.whoami())
        elif route == "/api/ask":
            # The provider call blocks for as long as the model takes, on a
            # threaded server whose other handlers are short. That is acceptable
            # here and nowhere else: this is one request, made by a person who
            # pressed a button and is watching a spinner, and assistant.ask()
            # carries its own timeout so the thread cannot be held for ever.
            from . import assistant
            answer, err = assistant.ask(_s(data.get("question")), cfg)
            result = None if err else {"answer": answer,
                                       "left": assistant.budget_left()}
        elif route == "/api/lab-correction":
            result, err = save_correction(data, cfg, self.whoami())
        elif route == "/api/settings":
            result, err = save_settings(data, cfg)
            if not err:
                # WITH THE VALUES. Every other audited action records what it
                # did; settings recorded only which keys were touched, so the
                # pool volume -- "the input every dose calculation multiplies
                # through" -- could go from 12,800 to 5,000 and the permanent
                # record said "volume_gallons". Six months later, when the doses
                # stopped working, the audit could not say what it had been. The
                # saved map holds no secrets by construction: a credential is
                # not a SETTABLE key.
                _saved = (result or {}).get("saved", {})
                audit.record("settings", ",".join(sorted(_saved)),
                             ", ".join(f"{k}={v}" for k, v in sorted(_saved.items())),
                             by=self.whoami(), cfg=cfg)
        else:
            return self._json(404, {"error": "no such endpoint"})

        if err:
            return self._json(400, {"error": err})

        # Re-render so the reload the browser is about to do shows the change.
        # BOTH builds: the public page is derived from the same CSVs, and
        # rendering only the authenticated one leaves anonymous visitors looking
        # at figures that silently stopped updating the day this deployment
        # gained a public build.
        # A failed render must not lose the write that already succeeded.
        try:
            from . import render
            render.build(public=True)
            render.build()
        except Exception as e:
            result["render_error"] = str(e)
        return self._json(200, result)

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

STARTED = dt.datetime.now().isoformat(timespec="seconds")

def main():
    cfg = config.load()
    site = config.site_dir(cfg)
    # BOTH builds, the way bin/render does it. This called render.build() with
    # no argument, which defaults to public=False and writes admin.html -- so a
    # server starting on an empty site volume tested for index.html, failed to
    # create it, and then SimpleHTTPRequestHandler fell back to a directory
    # listing: a 200 at / advertising admin.html and containing none of the
    # product. bootstrap --check's "public -> 200" passed on it.
    missing = [n for n in ("index.html", "admin.html")
               if not os.path.exists(os.path.join(site, n))]
    if missing:
        from . import render
        print(f"  rendering    {', '.join(missing)} (not in {site})")
        for public in (True, False):
            render.build(public=public)
    with Server((HOST, PORT), Handler) as httpd:
        print(f"  poolhound  http://{HOST}:{PORT}/")
        print(f"  serving    {site}")
        print(f"  writing    {cfg['_source']} and {config.data_dir(cfg)}/chemicals.csv")
        # Say which it actually is. "loopback only" printed by a process bound
        # to 0.0.0.0 is worse than no message: it is the line somebody will
        # quote when arguing that publishing the port is harmless.
        if HOST == "0.0.0.0":
            print("  bound 0.0.0.0 — SAFE ONLY IF the port is not published; a")
            print("             proxy must terminate TLS and enforce sign-in in front")
        else:
            print("  loopback only, Host+Origin checked, writes need the session token")
        print("  ctrl-c to stop")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  stopped")
    return 0

if __name__ == "__main__":
    sys.exit(main())
