#!/usr/bin/env python3
"""The agent that runs on the Raspberry Pi.

WHAT IT IS FOR
  the server holds the data and serves the pages; the panel is here. This bridges the
  two without anything reaching into the house:

    every 15 min   read AqualinkD on localhost, POST the sample up to the server
    continuously   hold one SSE connection open and wait to be told something

  Both directions are opened from here. Nothing is forwarded on the router and
  the server has no route in — it can only leave a note and wait for this to collect
  it.

WHY SSE AND NOT POLLING
  A command should arrive in under a second, and polling for that costs about
  140 MB a month at fifteen-second intervals. One held connection with a
  heartbeat costs around seven, and delivers immediately. The trade between
  frequency and cost only exists if you poll.

WHY IT WRITES NOTHING TO THE SD CARD
  Two cards have already died on this board through write wear, which is the
  entire reason data collection was on a Mac until now. So this keeps its state
  in /run, which is a tmpfs: executed command ids and cooldown timers live in
  RAM and are lost on reboot, which is correct — a command from before a reboot
  is far past its expiry anyway.

  SURVIVING THE PROCESS IS THE POINT, and for a long time this paragraph was
  describing something that did not happen. STATE_DIR was created at startup
  and never written to; Guard held the interlocks in process memory. So every
  `update-pi.sh` deploy restarted the service and cleared the gas heater's
  300s cooldown — the only thing between a double-press and short-cycling it —
  along with the record of which commands had already run. A file in /run is
  still RAM and still no write to the card; what it adds is that a restart
  reads it back.

WHY IT REFUSES THINGS the server HAS ALREADY APPROVED
  the server sits behind Entra, which authenticates people well and protects nothing
  if the host itself is taken. AqualinkD has no password at all. So every command
  is checked here against commands.py before the panel sees it, and the server's
  approval is necessary rather than sufficient.

STDLIB ONLY
  No pip install on a box whose storage is the fragile part.
"""
import argparse, datetime as dt, http.server, json, os, socket, ssl, sys, threading, time
import urllib.error, urllib.request

from . import commands as C

STATE_DIR = "/run/poolhound" if os.path.isdir("/run") else "/tmp/poolhound"
# The interlocks, across a RESTART but not across a reboot. See Guard.
GUARD_STATE = os.path.join(STATE_DIR, "guard.json")
AQUALINK = os.environ.get("POOLHOUND_AQUALINK", "http://127.0.0.1:8000")
SAMPLE_EVERY = 900
HEARTBEAT_TIMEOUT = 120        # no traffic for this long means the link is gone

# ------------------------------------------------- telling systemd it is well
# WHAT THE PING HAS TO MEAN, which is the whole of this design.
#
# MEASURED. After a reboot the Pi's DNS was not up, and this agent sat
# `active` for eight minutes logging "stream lost (URLError); retrying in 60s"
# while reaching nothing. systemctl said active, so poolhound-agent-ensure
# correctly did nothing: `is-active` answers "is there a process", and the
# process was fine. It was the only thing that was.
#
# So a watchdog ping sent on a timer would be worthless here — it would prove
# a thread is scheduled, which was never in doubt. The ping is withheld when
# the agent has had NO CONTACT WITH THE SERVER for LINK_STALE_S, and systemd
# then restarts it. A fresh process re-resolves DNS and opens a new socket,
# which is exactly what the stuck one could not do.
#
# TEN MINUTES, NOT TWO. A real server outage is not this agent's fault and
# restarting cannot fix it, so the threshold is well past any ordinary blip:
# the server sends stream heartbeats inside HEARTBEAT_TIMEOUT, and reconnect
# backoff tops out at 60s, so a healthy agent is never quiet for ten minutes.
# During a long outage this does churn one restart every few minutes. That is
# accepted deliberately, and it is only affordable because the command
# interlocks now survive a restart in /run — before that, this would have
# cleared the gas heater's cooldown every time it fired.
LINK_STALE_S = 600
WATCHDOG_PING_S = 30

_LINK = {"at": 0.0, "warned": False}


def link_alive():
    """The server said something to us just now."""
    _LINK["at"] = time.time()
    _LINK["warned"] = False


def link_age():
    """Seconds since the server last said anything, or None before the start."""
    return None if not _LINK["at"] else time.time() - _LINK["at"]


def sd_notify(state):
    """Send one datagram to systemd, if systemd is listening.

    Stdlib only, like the rest of this file -- no pip install on a board whose
    storage is the fragile part. A no-op without NOTIFY_SOCKET, which is the
    case when the agent is run by hand, so nothing here depends on systemd
    being the thing that started it.
    """
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    # systemd uses the abstract namespace when the path begins with @, which
    # in Python is a leading NUL rather than the character itself.
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(state.encode())
        return True
    except OSError:
        # Never fatal. A watchdog that can take the agent down by failing to
        # report is worse than no watchdog.
        return False


def systemd_watchdog(stop):
    """Ping while the link is alive; go quiet when it is not."""
    if not os.environ.get("NOTIFY_SOCKET"):
        return
    while not stop.is_set():
        age = link_age()
        if age is not None and age < LINK_STALE_S:
            sd_notify("WATCHDOG=1")
        elif not _LINK["warned"]:
            # ONCE PER EPISODE, not every thirty seconds: this writes to the
            # journal, the journal is on the card, and two cards have already
            # died here.
            _LINK["warned"] = True
            log(f"no contact with the server for {age:.0f}s — withholding the "
                f"systemd watchdog ping, so it will restart this agent")
        stop.wait(WATCHDOG_PING_S)

# WATCHING THE PANEL, WHICH IS A DIFFERENT JOB FROM SAMPLING IT.
#
# The heartbeat above pushes every fifteen minutes whatever is happening. That
# records the pool but not the EVENTS in it: on 2026-09-30 the pump ran a
# second block from 21:00 past midnight, and because the WaterGuru pod measured
# before that evening and not again until the next, a gradual overnight rise in
# free chlorine arrived on the page as a single unexplained step. The runtime
# was in the data; the moment it changed was not.
#
# AND THERE ARE SIX DOORS TO THIS PANEL. AqualinkD's own UI, poolhound local
# (the optional page on the Pi), aquapda_sim, aqmanager, the Jandy wall remote, the panel's own keypad,
# and its internal schedules. Only commands poolhound ITSELF issued appear in
# its audit log, so five of those doors were invisible as actions -- which is
# exactly the question that could not be answered about that evening.
#
# Watching the panel covers all of them at once, because the panel is the
# authority and every door ends at it. Nothing here listens and nothing here is
# exposed: this is a GET to AqualinkD on loopback, and the agent stays the
# component that only ever connects outward.
# WHERE A DOOR'S CLAIM ARRIVES, AND WHY THIS LISTENS AT ALL.
#
# The agent's whole design is that it connects outward and waits: the panel has
# no password of its own, so nothing may reach toward it. This endpoint does not
# weaken that, and the reason is what it CANNOT do. It accepts one thing -- "a
# change you are about to see came through me" -- and it has no path to the
# panel, no path to the server's write API, and no credential. The worst a
# caller achieves is a false `door:` on a row that was going to be written
# anyway, by something already running on this LAN.
#
# 127.0.0.1 ONLY, AND NOT NEGOTIABLE. POOLHOUND_BIND on the server is allowed
# 0.0.0.0 because the container never publishes its port; there is no such
# second fact here, so there is no such second option. A browser cannot reach
# this directly -- a phone's 127.0.0.1 is the phone -- so the page reaches it
# through a reverse-proxy hop on the Pi's own web server, which is already how
# that page reaches AqualinkD.
CLAIM_HOST = "127.0.0.1"
# 8791, AND THE DEFAULT IS A GUESS WHEREVER IT LANDS. The first one picked was
# 8788, which on the Pi this runs on was already held by another of the
# household's own services --
# with netinv on 8787 next to it. A Raspberry Pi doing the household's odd jobs
# has a crowded loopback, and nothing here can know what else is on it, so the
# override below matters more than the number does.
CLAIM_PORT = int(os.environ.get("POOLHOUND_CLAIM_PORT", "8791"))
# How long a claim stands. A door claims just BEFORE it acts, and the watcher
# sees the result within WATCH_EVERY plus WATCH_SETTLE; much longer than that
# and an unrelated later change would inherit somebody else's door.
CLAIM_TTL = 60

WATCH_EVERY = 5          # how often the panel's state is compared
WATCH_SETTLE = 4         # quiet seconds before a burst is pushed as one row
WATCH_MIN_GAP = 45       # floor between change-pushes; coalesces, never drops


def watched_columns():
    """The sample columns that are panel STATE rather than a measurement.

    DERIVED FROM commands.py, which is the shared vocabulary both ends already
    validate against, so a control added there is watched here without anybody
    remembering to. A hand-written list is how salt-cell boost came to be
    missing from the one page that claims to enumerate every control.

    Temperatures, salt and wattage are deliberately NOT state: they drift
    continuously, and a watcher that treated them as changes would push a row
    every five seconds and record the weather as an event.
    """
    cols = {sw["column"] for sw in C.SWITCHES.values()}
    for sp in C.SETPOINTS.values():
        cols.add(sp["column"])
        cols.add(sp["value_column"])
    # None MEANS "THIS CONTROL HAS NO COLUMN", and three of them do not:
    # Aux_4, Aux_5 and SWG/Boost. A boost that is switched on is therefore not
    # observable in a sample at all -- not a gap this watcher can close, and
    # worth knowing rather than silently dropping.
    cols.discard(None)
    # AND ONE STATE COLUMN IS NOT A COMMAND. The solar valve is actuated by the
    # panel's own logic, which is why commands.py leaves Extra_Aux out on
    # purpose -- but it is still panel state, and it moving is still an event
    # in this pool. Added here deliberately rather than by accident, and the
    # inventory below is what stops a NEW column being classified as neither.
    cols.add("solar_valve")
    return frozenset(cols)


# What is a measurement rather than state: it drifts on its own, continuously,
# and a watcher that called these changes would push a row every five seconds
# and record the weather as an event.
MEASURED_COLUMNS = frozenset({
    "pump_rpm", "pump_watts", "salt_ppm",
    "pool_temp", "air_temp", "spa_temp",
})


def state_of(sample):
    """One sample's switch-and-setpoint state, as something comparable.

    Sorted, so two dicts that agree cannot compare unequal on ordering, and
    stringified, so 1 and "1" are the same state -- read_sample() yields ints
    for switches and floats for setpoints, and a round trip through the server
    makes them strings.
    """
    return tuple(sorted((k, str(sample.get(k, "")))
                        for k in watched_columns()))


class ChangeWatch:
    """Decides when a panel change has settled enough to be worth a row.

    ITS OWN CLASS BECAUSE THE DECISION IS THE HARD PART, and a decision buried
    in a `while True` is a decision no test can reach. Three things have to be
    true at once and each one is a defect if it is missing:

      SETTLE   this is a PDA panel -- AqualinkD drives an on-screen menu, and a
               single press can be reported as a short burst of intermediate
               states. Pushing each one would write the menu walk into the
               pool's history as four events.
      MIN_GAP  a flapping circuit, or a heater cycling on its own thermostat,
               must not be able to write hundreds of rows an hour.
      COALESCE and the floor must DELAY rather than DROP. A floor that discards
               loses the change it was meant to record, which is worse than
               the noise it was protecting against -- so a change seen inside
               the floor is held and pushed when the floor expires, carrying
               whatever the state is by then.

    The first fingerprint is never a change. Without that an agent restart
    pushes a spurious event every time, and this service restarts on every
    deploy.
    """

    def __init__(self, settle=WATCH_SETTLE, min_gap=WATCH_MIN_GAP):
        self.settle, self.min_gap = settle, min_gap
        self.known = None          # the state last pushed, or first observed
        self._pending = None       # (fingerprint, when it was first seen)
        self._last_push = None

    def saw(self, fingerprint, now):
        """True when this observation should be pushed as a sample."""
        if self.known is None:
            self.known = fingerprint
            return False
        if fingerprint == self.known:
            # Back to where it started inside the settle window -- a press and
            # an un-press, or a menu walk that returned. Nothing happened.
            self._pending = None
            return False
        if self._pending is None or self._pending[0] != fingerprint:
            self._pending = (fingerprint, now)      # still moving; restart it
            return False
        if now - self._pending[1] < self.settle:
            return False
        if self._last_push is not None and now - self._last_push < self.min_gap:
            return False                              # held, not dropped
        self.known = fingerprint
        self._pending = None
        self._last_push = now
        return True

_REV = None

def revision():
    """Which commit this agent is running, from the REVISION file the deploy
    writes beside it. Read once: it cannot change without a restart, and the Pi
    must not be asked to stat a file on its SD card every fifteen minutes."""
    global _REV
    if _REV is None:
        try:
            here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            with open(os.path.join(here, "REVISION")) as f:
                _REV = f.read().strip()[:60] or "unknown"
        except OSError:
            _REV = "unknown"
    return _REV


def log(msg):
    sys.stdout.write(f"{dt.datetime.now():%H:%M:%S} {msg}\n")
    sys.stdout.flush()

def _longest_cooldown():
    """The longest any device can be held off, so pruning cannot drop a live one.

    Derived from commands.py rather than written down here: a second copy of
    the longest cooldown is a second copy that drifts the day somebody raises
    the heater's 300s, and the whole of the cooldown vocabulary lives there.
    """
    specs = list(C.SWITCHES.values()) + list(C.SETPOINTS.values())
    return max([s.get("cooldown_s", 0) for s in specs] + [60])


class Guard:
    """Idempotency, cooldowns and rate limiting, kept in /run.

    Separate from validation because these are about HISTORY rather than about
    the command itself: the same request can be perfectly legal twice and still
    be wrong the second time.

    WHY THIS IS ON DISK AND THE CLAIMS ARE NOT

    These three dicts used to be process memory only, while this module's
    docstring said the agent "keeps its state in /run" and Claims said "the
    agent's other state lives in /run for the same reason". Neither was true:
    STATE_DIR was created at startup and nothing was ever written into it.

    The consequence is specific. `update-pi.sh` restarts the service on every
    deploy, and a restart built a Guard with empty dicts — so the gas heater's
    300s cooldown, which is the only interlock standing between a double-press
    and short-cycling a pool heater, was cleared by deploying. An already-run
    command id was forgotten with it, so a resend that arrived across the
    restart would execute a second time rather than being refused.

    /run is a tmpfs, so this is still RAM and still no write to the card that
    two boards have already died on. What it survives is the process; what it
    does NOT survive is a reboot, which is correct and unchanged — MAX_AGE_S is
    300s, so every command from before a reboot is expired on arrival anyway,
    and a cooldown is a statement about the last few minutes.

    Wall clock on purpose: time.monotonic() restarts with the process, which is
    exactly the event this has to read across.
    """
    def __init__(self, path=GUARD_STATE):
        self.path = path
        self.done = {}        # command id -> when it ran
        self.last = {}        # device  -> when it last changed
        self.recent = []      # timestamps, for the rate limit
        self._load()

    def _load(self):
        """Read the interlocks back, and never fail to start over them.

        A corrupt or unreadable file must not stop the agent: it would take the
        pool's only sampler down with it. It is logged and the state starts
        empty, which is the behaviour that shipped — no worse than before, and
        loud about it.
        """
        try:
            with open(self.path, encoding="utf-8") as f:
                saved = json.load(f)
        except FileNotFoundError:
            return
        except Exception as e:                            # noqa: BLE001
            log(f"guard state unreadable ({type(e).__name__}), "
                f"starting with no cooldowns held: {self.path}")
            return
        try:
            now = time.time()
            self.done = {str(k): float(v) for k, v in (saved.get("done") or {}).items()}
            self.last = {str(k): float(v) for k, v in (saved.get("last") or {}).items()}
            self.recent = [float(t) for t in (saved.get("recent") or [])]
            # A clock that went BACKWARDS would leave a timestamp in the
            # future, and `now - future` is negative, which reads as "changed
            # ages ago" and waves the cooldown through. Drop those.
            self.done = {k: v for k, v in self.done.items() if v <= now}
            self.last = {k: v for k, v in self.last.items() if v <= now}
            self.recent = [t for t in self.recent if t <= now]
            self._prune(now)
            held = sum(1 for v in self.last.values()
                       if now - v < _longest_cooldown())
            if held:
                log(f"restored {held} cooldown(s) still in force from {self.path}")
        except Exception as e:                            # noqa: BLE001
            log(f"guard state malformed ({type(e).__name__}), starting empty")
            self.done, self.last, self.recent = {}, {}, []

    def _prune(self, now):
        """Keep only what can still refuse something."""
        self.done = {k: v for k, v in self.done.items()
                     if v > now - (C.MAX_AGE_S * 4)}
        self.last = {k: v for k, v in self.last.items()
                     if v > now - _longest_cooldown()}
        self.recent = [t for t in self.recent if now - t < C.RATE_WINDOW_S]

    def _save(self):
        """Write the interlocks where a restart will find them.

        Atomic, because a half-written file read by the next start is the one
        way this could make things worse than holding nothing. Never raises:
        the command has already run, and a bookkeeping failure must not be
        reported as a failed command — but it IS logged, because an agent
        silently not holding its cooldowns is the defect this replaced.
        """
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = f"{self.path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"done": self.done, "last": self.last,
                           "recent": self.recent}, f)
            os.replace(tmp, self.path)
        except Exception as e:                            # noqa: BLE001
            log(f"could not persist guard state ({type(e).__name__}): a restart "
                f"will not hold these cooldowns")

    def check(self, cmd):
        now = time.time()
        if cmd["id"] in self.done:
            raise C.Refused("already executed — this is a resend, and running it "
                            "again would double the action")
        self.recent = [t for t in self.recent if now - t < C.RATE_WINDOW_S]
        if len(self.recent) >= C.RATE_LIMIT:
            raise C.Refused(f"rate limit: more than {C.RATE_LIMIT} commands in "
                            f"{C.RATE_WINDOW_S}s")
        d = cmd.get("device")
        if d:
            cool = C.cooldown_for(cmd)
            since = now - self.last.get(d, 0)
            if since < cool:
                spec = C.SWITCHES.get(d) or C.SETPOINTS.get(d) or {}
                why = spec.get("why") or ""
                raise C.Refused(f"{spec.get('label', d)} changed {since:.0f}s ago and "
                                f"has a {cool}s cooldown. {why}".strip())

    def record(self, cmd):
        now = time.time()
        self.done[cmd["id"]] = now
        self.recent.append(now)
        if cmd.get("device"):
            self.last[cmd["device"]] = now
        # Ids are only needed until they can no longer be replayed; cooldowns
        # until they can no longer refuse anything.
        self._prune(now)
        self._save()

def aq_get(path, timeout=15):
    with urllib.request.urlopen(f"{AQUALINK}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode())

def aq_set(device, value, timeout=20):
    """AqualinkD wants PUT with a form body.

    Worth stating because the obvious-looking GET /api/<id>/set/<value> answers
    200 and does nothing — the daemon compares the circuit against itself and
    logs "not changing state, is same". That cost a day once.
    """
    req = urllib.request.Request(
        f"{AQUALINK}/api/{device}/set", data=f"value={value}".encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="PUT")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode()[:200]

def execute(cmd, guard):
    C.validate(cmd)
    guard.check(cmd)
    a = cmd["action"]
    if a == "all_off":
        # Every circuit is attempted even after one fails — a half-off pool is
        # better than stopping at the first error — but the outcome of each is
        # KEPT, not flattened into a string. This used to return ok:True
        # unconditionally, so "everything off" reported done in green with the
        # pump still running. It is the one command in the product that must
        # never overstate, and it was the only one that could not understate.
        results, failed = [], []
        def off(dev):
            try:
                status = aq_set(dev, 0)[0]
                results.append((dev, status))
                if not 200 <= status < 300:
                    failed.append(dev)
            except Exception as e:
                results.append((dev, str(e)[:40]))
                failed.append(dev)
        for dev in ("Spa", "Aux_1", "Aux_2", "Aux_3", "Aux_4", "Aux_5"):
            off(dev)
        # The pump goes last: turning it off first strands anything still running.
        off("Filter_Pump")
        guard.record(cmd)
        return {"ok": not failed, "detail": results,
                "failed": failed,
                "error": ("could not switch off: " + ", ".join(failed)) if failed else None}
    if a == "resync":
        # aq_get raises on a failure, so reporting ok unconditionally after it
        # returned is sound — but say so, rather than leaving the next reader to
        # work out that this literal is not the same mistake as the one above.
        aq_get("/api/devices")          # raises if the panel is unreachable
        guard.record(cmd)
        return {"ok": True, "detail": "re-read the panel"}
    status, body = aq_set(cmd["device"], cmd["value"])
    guard.record(cmd)
    return {"ok": 200 <= status < 300, "detail": f"{status} {body}"}

class Server:
    """Everything that talks to the server, in one place so the token cannot leak into
    a URL, a log line or an exception by accident."""
    def __init__(self, base, token, verify=True):
        self.base = base.rstrip("/")
        self._token = token
        self.ctx = ssl.create_default_context()
        if not verify:
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE

    def _req(self, path, data=None, method="GET", timeout=30):
        r = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(data).encode() if data is not None else None,
            method=method,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self._token}",
                     # Which code is actually running on the Pi. the server stamps
                     # REVISION into its image and --check compares it to the
                     # served page's footer; the Pi had no equivalent, and
                     # update-pi.sh --check printed an agent.py MTIME -- which
                     # rsync -a preserves from the source checkout, so it
                     # reflects when the repo was cloned, not what is deployed.
                     # That gap is the reason update-pi.sh exists: the agent sat
                     # three weeks behind with nothing saying so.
                     "X-Poolhound-Revision": revision(),
                     "Accept": "application/json"})
        return urllib.request.urlopen(r, timeout=timeout, context=self.ctx)

    def push_note(self, changed, door):
        """Record WHAT changed and through which door, if a door said so.

        A separate route from the sample on purpose: the sample is the
        measurement and this is the story about it. Keeping them apart is what
        let this be added with no change to aqualink.COLS -- and samples.csv is
        1,935 rows of irreplaceable history whose header is written once, so a
        column added to it rewrites the whole file.
        """
        body = {"changed": list(changed), "door": door or ""}
        with self._req("/api/agent/note", body, "POST", 20):
            pass

    def push_sample(self, sample):
        with self._req("/api/agent/sample", sample, "POST", 30) as r:
            return r.status

    def ack(self, cmd_id, result):
        try:
            with self._req("/api/agent/ack", {"id": cmd_id, **result}, "POST", 20):
                return True
        except Exception as e:
            log(f"  ack for {cmd_id[:8]} failed: {type(e).__name__}")
            return False

    def stream(self):
        """Open the command stream and yield commands as they arrive."""
        r = urllib.request.Request(
            f"{self.base}/api/agent/commands",
            headers={"Authorization": f"Bearer {self._token}",
                     "X-Poolhound-Revision": revision(),
                     "Accept": "text/event-stream",
                     "Cache-Control": "no-store"})
        conn = urllib.request.urlopen(r, timeout=HEARTBEAT_TIMEOUT, context=self.ctx)
        # THE STREAM OPENED, so the name resolved, the socket connected and the
        # token was accepted. That is the strongest single statement of health
        # this agent can make, and it is what the systemd watchdog ping means.
        link_alive()
        buf = []
        for raw in conn:
            # EVERY LINE COUNTS, the heartbeats included. A `:` comment is the
            # server saying "still here" on an idle pool, and an idle pool is
            # the normal case — judging liveness on commands alone would call a
            # perfectly healthy quiet night a dead link.
            link_alive()
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith(":"):
                continue                       # heartbeat
            if line == "":
                if buf:
                    try:
                        yield json.loads("".join(buf))
                    except ValueError:
                        log("  stream sent something that was not JSON; ignored")
                    buf = []
                continue
            if line.startswith("data:"):
                buf.append(line[5:].lstrip())

def read_sample():
    """One reading of the panel, in the shape the server stores."""
    dev = {d["id"]: d for d in aq_get("/api/devices").get("devices", [])}
    def on(i):  return 1 if (dev.get(i, {}).get("state") == "on") else 0
    def pump_field(k, hi):
        """A numeric field hanging off the Filter_Pump device itself.

        -999 is AqualinkD's "no reading"; 0 is a real reading (the pump is off)
        and must be kept, because a day of zeroes is what proves the pump did
        not run rather than that nobody looked.
        """
        try:
            v = float(dev.get("Filter_Pump", {}).get(k))
        except (TypeError, ValueError):
            return ""
        return "" if v <= -100 or v > hi else v

    def sp_value(i):
        """The SETPOINT of a device, from its own spvalue field."""
        try:
            v = float(dev.get(i, {}).get("spvalue"))
        except (TypeError, ValueError):
            return ""
        return "" if v <= -100 or v > 120 else v

    def val(i):
        v = dev.get(i, {}).get("value")
        try:
            v = float(v)
        except (TypeError, ValueError):
            return ""
        # The sentinel belongs to commands.py, which both ends validate against.
        return "" if v <= C.NO_READING else v
    row = {
        "ts": dt.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
        "pump": on("Filter_Pump"), "spa": on("Spa"), "sheer": on("Aux_2"),
        "pool_light": on("Aux_1"), "spa_light": on("Aux_3"),
        "pool_heat": on("Pool_Heater"), "spa_heat": on("Spa_Heater"),
        "solar_valve": on("Extra_Aux"),
        "swg_pct": val("SWG/Percent"), "salt_ppm": val("SWG/PPM"),
        "pool_temp": val("Temperature/Pool"), "air_temp": val("Temperature/Air"),
        "spa_temp": val("Temperature/Spa"),
        # Read, not hardcoded empty. These two were written as "" on every
        # sample this agent has ever pushed -- 476 rows, not one value -- so the
        # column looked like a measurement that was always missing rather than
        # one nothing ever tried to take. They are not devices with a `value`:
        # AqualinkD reports them as fields ON the Filter_Pump object, which is
        # why they need their own accessor. A pump that does not report them
        # (single-speed, or a panel that does not expose them) still yields ""
        # and nothing downstream may claim otherwise.
        "pump_rpm": pump_field("Pump_RPM", 5000),
        "pump_watts": pump_field("Pump_Watts", 10000),
        # spvalue, NOT value. On these devices `value` is the CURRENT reading
        # and `spvalue` is the setpoint. Freeze_Protect.value tracks the air
        # temperature exactly -- 56 when Temperature/Air is 56 -- and its real
        # setpoint is 34; Pool_Heater.value is -999 whenever the pump is off
        # while its setpoint is 104 either way. Reading `value` recorded the
        # weather into a column named for a setpoint, and the control card
        # printed it as "now 56": a fabricated number in the shape of a real one.
        "freeze": on("Freeze_Protect"),
        "pool_set": sp_value("Pool_Heater"), "spa_set": sp_value("Spa_Heater"),
        "freeze_set": sp_value("Freeze_Protect"),
    }
    # val() drops AqualinkD's -999, which is the whole story for the two water
    # probes and only half of it for the salt cell: SWG/PPM latches instead,
    # answering the last measured figure for as long as the pump stays off. That
    # is not a reading, and it was being stored as one. See commands.py.
    return C.drop_stale_flow_readings(row)

# Which sample column reports each switch, so a command can be checked against
# the panel's own answer rather than assumed to have worked.
# From commands.py — the vocabulary this agent already validates every command
# against. Keeping a private copy here meant the Pi, which is released
# separately from the server, could disagree with the server about which column
# confirms which switch.
CONFIRM_COL = {d: c for d, c in C.STATE_COLUMN.items() if c}

def confirm_and_push(server, cmd, tries=(1.0, 3.0, 8.0, 20.0)):
    """Read the panel back after a command and push what it says.

    WHY THIS EXISTS
    The ack only ever meant "I sent it". The state the page SHOWS comes from a
    sample, and samples arrive every fifteen minutes — so a switch that was just
    turned on sat reading "waiting" for up to a quarter of an hour even though
    the panel had obeyed within a second.

    Pushing a reading straight after the command closes that gap to about a
    second. It is a real reading, not an assumption: if the panel has not
    actually changed, what gets pushed is the old state and the page keeps
    saying "waiting", which is the truth.

    RETRIED, BECAUSE THE PANEL IS NOT INSTANT
    This is a PDA panel: AqualinkD drives an on-screen menu and the reported
    state can lag the command by a few seconds. Reading once would frequently
    capture the old value and push a sample that says the opposite of what just
    happened — worse than saying nothing. So it reads a few times, stops as soon
    as the panel agrees with what was asked, and gives up quietly if it never
    does. Giving up is not a failure to report: the last reading still went up,
    and it still says "waiting", because it is.
    """
    dev = cmd.get("device")
    col = CONFIRM_COL.get(dev)
    want = None
    if cmd.get("action") == "set" and col:
        want = 1 if cmd.get("value") in (1, "1", True) else 0

    for n, delay in enumerate(tries):
        time.sleep(delay)
        try:
            s = read_sample()
        except Exception as e:
            log(f"  confirm read failed: {type(e).__name__}")
            return
        try:
            server.push_sample(s)
        except Exception as e:
            log(f"  confirm push failed: {type(e).__name__}")
            return
        if want is None:
            return                      # all_off, resync, setpoints: one push is enough
        if str(s.get(col, "")) == str(want):
            log(f"  {dev} confirmed {'on' if want else 'off'} after "
                f"{sum(tries[:n+1]):.0f}s")
            return
    log(f"  {dev} had not changed on the panel after "
        f"{sum(tries):.0f}s; the page will keep saying waiting")


def sampler(server, stop):
    """Push a sample every SAMPLE_EVERY seconds, forever, on its own thread.

    THIS IS A THREAD BECAUSE THE STREAM BLOCKS.

    It used to be a check at the top of the main loop, guarded by a comment
    saying "the sample loop runs whether or not the stream is healthy". It did
    not. `for cmd in server.stream()` blocks inside the generator until a COMMAND
    arrives, and heartbeats are swallowed with `continue` rather than yielded —
    so on a healthy, idle connection the loop never came back round and no
    sample was ever pushed. Samples only appeared when the stream DROPPED, which
    made the failure invisible during testing: every deploy restarted the
    container, dropped the stream, and produced a sample that looked like proof
    it was working. It recorded nothing for the 75 minutes it was left alone.

    A separate thread is the honest expression of the intent: the two jobs are
    independent, and neither should be able to stall the other.
    """
    while not stop.is_set():
        try:
            server.push_sample(read_sample())
        except Exception as e:
            # Never fatal. A panel that is briefly unreachable, or a server that
            # is restarting, must not end the sampler for the rest of the day.
            log(f"sample push failed: {type(e).__name__}: {e}")
        stop.wait(SAMPLE_EVERY)


class Claims:
    """Doors that have said they are about to change something.

    IN MEMORY AND NOWHERE ELSE. Nothing writes to this Pi's card -- it has
    already destroyed two -- and a claim is worth seconds, so a file would be
    wear for no benefit. The agent's other state lives in /run for the same
    reason.
    """

    def __init__(self, ttl=CLAIM_TTL):
        self.ttl = ttl
        self._claims = []              # (expires_at, door)
        self._lock = threading.Lock()

    def add(self, door, now):
        with self._lock:
            self._claims.append((now + self.ttl, door))

    def take(self, now):
        """The most recent unexpired door, and forget it. None if nobody claimed.

        TAKEN, NOT READ. A claim explains ONE change; leaving it in place would
        let the next unrelated change -- a schedule firing an hour later --
        inherit a door it never came through, which is a false record of
        exactly the kind this is supposed to replace.
        """
        with self._lock:
            live = [(e, d) for e, d in self._claims if e > now]
            self._claims = []
            return live[-1][1] if live else None


ACTIVITY_STATE = os.path.join(STATE_DIR, "activity.json")
ACTIVITY_KEEP = 200
# A change seen this soon after poolhound ran a command on the same device is
# that command arriving, not a second event. The panel takes a few seconds and
# the watcher settles for WATCH_SETTLE plus WATCH_EVERY on top.
APP_WINDOW_S = 90

# Which watched column a setpoint's VALUE is reported in, for describing a
# change in it. From commands.py, the vocabulary both ends validate against.
_VALUE_DEVICE = {sp["value_column"]: d for d, sp in C.SETPOINTS.items()
                 if sp.get("value_column")}


def describe_change(col, value):
    """'Filter pump on', 'Pool heater set to 88', for one watched column.

    None for a column that went blank: a probe dropping out (the pump stopped,
    so the heater setpoint reads -999) is the panel losing a reading, not
    somebody doing something, and an activity list that said "Pool heater set
    to nothing" would be reporting the weather as an action.
    """
    v = str(value)
    if v == "":
        return None
    if col in _VALUE_DEVICE:
        try:
            v = f"{float(v):g}"
        except ValueError:
            pass
        return f"{C.label_for(_VALUE_DEVICE[col])} set to {v}"
    if col == "solar_valve":
        label = "Solar valve"
    else:
        label = C.label_for(C.COLUMN_DEVICE.get(col, col))
    word = {"1": "on", "0": "off"}.get(v, v)
    return f"{label} {word}"


class Activity:
    """What happened at the panel lately, for poolhound local's activity list.

    WHY THE AGENT HOLDS THIS. poolhound local's log was the browser's memory:
    what that one tab had done, forty lines, gone on reload. It could never say
    that the poolhound app had switched the pump, because nothing on the Pi
    recorded it in a place the page could read. The agent is the one process
    that sees both halves -- the commands it executes for the app and every
    change it watches arrive at the panel -- so it keeps the list.

    WHAT IT HOLDS IS SAFE TO SHOW THE HOUSE LAN, and that is deliberate. It is
    served unauthenticated through the Pi's web server, to a network that can
    already drive the panel with no sign-in. So it carries what changed and
    which way it came, and never WHO: a command's `by` is a signed-in identity
    on the server, and it stays there. "poolhound app" is the most it says.

    In /run like Guard, so it survives the agent restarting (every deploy) and
    not a reboot, and costs the SD card nothing. Wall clock, for the reason
    Guard gives: the restart is the event this has to read across.
    """

    def __init__(self, path=ACTIVITY_STATE, keep=ACTIVITY_KEEP):
        self.path, self.keep = path, keep
        self._lock = threading.Lock()
        self.events = []        # oldest first: {at, t, what, how, ok, device}
        self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                saved = json.load(f)
            now = time.time()
            # A stamp from the future is dropped, as Guard drops one: it would
            # sort above everything that really happened afterwards.
            self.events = [e for e in saved.get("events", [])
                           if isinstance(e, dict) and float(e.get("t", 0)) <= now
                           ][-self.keep:]
        except FileNotFoundError:
            return
        except Exception as e:                            # noqa: BLE001
            # Never stops the agent: this is a convenience for a page, and the
            # agent is the pool's only sampler.
            log(f"activity unreadable ({type(e).__name__}), starting empty")
            self.events = []

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = f"{self.path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"events": self.events}, f)
            os.replace(tmp, self.path)
        except Exception as e:                            # noqa: BLE001
            log(f"could not persist activity ({type(e).__name__})")

    def add(self, what, how, ok=True, device=None, now=None):
        now = time.time() if now is None else now
        e = {"at": dt.datetime.fromtimestamp(now).astimezone()
                     .strftime("%Y-%m-%dT%H:%M:%S%z"),
             "t": now, "what": str(what)[:120], "how": str(how)[:60],
             "ok": bool(ok), "device": device}
        with self._lock:
            self.events.append(e)
            self.events = self.events[-self.keep:]
            self._save()
        return e

    def app_ran(self, device, now=None):
        """Did poolhound execute a command on `device` within APP_WINDOW_S?"""
        now = time.time() if now is None else now
        with self._lock:
            return any(e.get("how") == "poolhound app" and e.get("ok")
                       and (e.get("device") == device or e.get("device") == "*")
                       and 0 <= now - float(e["t"]) <= APP_WINDOW_S
                       for e in self.events)

    def recent(self, n=100):
        """Newest first, without the internal fields a page has no use for."""
        with self._lock:
            return [{k: e[k] for k in ("at", "what", "how", "ok")}
                    for e in reversed(self.events[-n:])]


def record_command(activity, cmd, result):
    """One activity row for a command the server sent and this agent handled."""
    if activity is None:
        return
    what = C.describe(cmd)
    if result.get("refused"):
        what += " — refused by the Pi"
    elif not result.get("ok"):
        what += " — failed"
    device = "*" if cmd.get("action") == "all_off" else cmd.get("device")
    activity.add(what, "poolhound app", ok=bool(result.get("ok")), device=device)


def record_change(activity, changes, door):
    """Activity rows for a panel change the watcher saw.

    `changes` is [(column, new value)]. Each gets its own row, named for the
    way it came: the door that claimed it, else poolhound if the app just
    commanded that device (its own row is already there, so this one is
    skipped), else the panel or AqualinkD, which look alike from here.
    """
    if activity is None:
        return
    now = time.time()
    for col, v in changes:
        what = describe_change(col, v)
        if what is None:
            continue
        dev = C.COLUMN_DEVICE.get(col) or _VALUE_DEVICE.get(col)
        if door:
            how = door
        elif dev and activity.app_ran(dev, now):
            continue                 # the app's own command, already listed
        else:
            how = "panel or AqualinkD"
        activity.add(what, how, device=dev, now=now)


def claim_server(claims, clock=None, activity=None):
    """The loopback endpoint. Records a door's claim, and reads back activity.

    Returns the HTTPServer so a caller can shut it down; serve_forever() is the
    caller's to run on a thread.

    GET /activity is the one read, and what it returns is Activity.recent():
    what changed and which way it came, nothing about who. See Activity for why
    that is safe on a LAN that can already drive the panel.
    """
    clock = clock or time.monotonic

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):     # its own log line, not stderr noise
            pass

        def do_GET(self):
            if self.path.split("?")[0] != "/activity":
                return self._reply(404, {"error": "no such path"})
            events = activity.recent() if activity else []
            return self._reply(200, {
                "events": events,
                "kept": ACTIVITY_KEEP,
                "note": "kept in memory on the Pi: survives the agent "
                        "restarting, not the Pi rebooting"})

        def do_POST(self):
            if self.path.split("?")[0] != "/claim":
                return self._reply(404, {"error": "no such path"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > 4096:
                    return self._reply(413, {"error": "too large"})
                body = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("not an object")
            except Exception:
                return self._reply(400, {"error": "expected a JSON object"})
            # ALLOWLISTED IN commands.py, which both ends validate against, and
            # written as `door:<name>` so it can never read as an identity.
            door = C.door_label(body.get("door"))
            if door is None:
                return self._reply(400, {"error": "unknown door"})
            claims.add(door, clock())
            log(f"claim from {door}")
            return self._reply(200, {"noted": door})

        def _reply(self, code, obj):
            raw = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    # HTTPServer, not ThreadingHTTPServer: this handles a handful of requests a
    # day and a thread per connection is a way for a LAN caller to make the
    # agent allocate.
    return http.server.HTTPServer((CLAIM_HOST, CLAIM_PORT), Handler)


def watcher(server, stop, read=None, clock=None, claims=None, activity=None):
    """Push a sample the moment the panel's state changes, on its own thread.

    A SECOND THREAD, for the reason sampler() has a docstring about: the two
    jobs are independent and neither may be able to stall the other. The
    heartbeat records the pool every fifteen minutes; this records the moment
    something in it moved, and a pool where nothing moves for a day produces
    nothing here at all.

    THE HEARTBEAT IS NOT REPLACED. It is what makes silence detectable -- a
    panel that has gone unreachable pushes no changes either, and "no changes"
    looks exactly like a quiet afternoon. Losing that would trade an event log
    for a blind spot.

    IT PUSHES A WHOLE SAMPLE, not an event record, and that is what lets this
    be additive: by_day() accumulates the interval each sample REPRESENTS,
    capped at SEG_MAX, rather than counting rows -- so an extra row at an
    irregular time shortens the span of the one before it and makes runtime
    more accurate, not wrong. A row here needs no new file, no new column list,
    no new loader and no new route.

    `read` and `clock` are injectable so the loop is testable without a panel.
    """
    read = read or read_sample
    clock = clock or time.monotonic
    watch = ChangeWatch()
    while not stop.is_set():
        try:
            sample = read()
            before = watch.known
            now = clock()
            if watch.saw(state_of(sample), now):
                server.push_sample(sample)
                # THE SAMPLE FIRST, THE NOTE SECOND, and the note is allowed to
                # fail on its own. The row is the thing that cannot be
                # recovered later; a missing audit line is a gap in the story
                # of a change that is itself safely recorded.
                changed = [k for k, v in state_of(sample)
                           if dict(before or ()).get(k) != v] if before else []
                door = claims.take(now) if claims else None
                try:
                    server.push_note(changed, door)
                except Exception as e:
                    log(f"change note failed: {type(e).__name__}: {e}")
                # AND POOLHOUND LOCAL'S LIST, which is local and cannot fail the
                # watch: Activity swallows its own errors.
                record_change(activity, [(k, v) for k, v in state_of(sample)
                                         if k in changed], door)
                log("panel changed — pushed a sample"
                    + (f" ({door})" if door else ""))
        except Exception as e:
            # Never fatal, exactly as the sampler is not. A panel that is
            # briefly unreachable must not end the watch for the rest of the
            # day, and this thread holds no state that an exception corrupts:
            # ChangeWatch only ever advances on a successful push.
            log(f"watch failed: {type(e).__name__}: {e}")
        stop.wait(WATCH_EVERY)


def main():
    ap = argparse.ArgumentParser(description="poolhound agent (runs on the Pi)")
    # NO DEFAULT HOST. This defaulted to one household's server, compiled into
    # every copy of a repository meant to be opened -- so somebody else's Pi,
    # started without --server, would have pointed its agent at our machine and
    # presented its bearer token there. It fails closed now: the address is
    # required, and the error says where to put it.
    # ONE SPELLING NOW, AND THE MIGRATION IS WHY IT TOOK A WHILE. This carried a
    # fallback to an older variable named after the server itself — which was
    # both a compatibility shim and the last place a tracked file named this
    # deployment's host. The variable is set in /etc/poolhound/agent.env ON THE
    # PI, a hand-maintained file no deploy touches: update-pi.sh rsyncs the code
    # and restarts the unit, and ships neither the unit file nor this one. So
    # dropping the fallback before the Pi was changed would have left a
    # correctly-deployed agent unable to find the server at its next restart,
    # with update-pi.sh --check reporting the revision as current — the exact
    # shape of failure this project keeps writing checks about.
    #
    # The order was therefore: rename the key on the Pi (the deployed agent
    # already read POOLHOUND_SERVER first), restart, confirm a sample landed at
    # the restart timestamp, and only then delete this. Done 2026-10-07.
    ap.add_argument("--server", default=os.environ.get("POOLHOUND_SERVER"))
    ap.add_argument("--token-file", default="/etc/poolhound/agent.token")
    ap.add_argument("--once", action="store_true", help="push one sample and exit")
    ap.add_argument("--insecure", action="store_true", help="skip TLS verification")
    a = ap.parse_args()
    if not a.server:
        ap.error("no server address. Pass --server https://<host>, or set "
                 "POOLHOUND_SERVER in /etc/poolhound/agent.env — the unit file "
                 "reads it from there.")

    try:
        with open(a.token_file) as f:
            token = f.read().strip()
    except OSError:
        sys.exit(f"no agent token at {a.token_file} — create it, chmod 600, and it "
                 f"must match the one the server was given")
    if os.stat(a.token_file).st_mode & 0o077:
        sys.exit(f"{a.token_file} is readable by others — chmod 600 it")

    os.makedirs(STATE_DIR, exist_ok=True)
    server = Server(a.server, token, verify=not a.insecure)

    if a.once:
        s = read_sample()
        log(f"sample: pump={s['pump']} swg={s['swg_pct']} pool={s['pool_temp']}")
        log(f"pushed, status {server.push_sample(s)}")
        return 0

    guard = Guard()
    backoff = 1.0
    log(f"agent up; panel {AQUALINK}, server {a.server}")

    # Sampling is independent of the command stream and runs on its own thread,
    # so a healthy-but-idle stream cannot stall it and a dead stream cannot stop
    # the pool being recorded. Daemon so a KeyboardInterrupt still exits.
    stop = threading.Event()
    threading.Thread(target=sampler, args=(server, stop), daemon=True,
                     name="poolhound-sampler").start()
    # The heartbeat and the watch are separate threads on purpose; see both
    # docstrings. One answers "what is the pool doing", the other "when did it
    # change", and a pool that is quiet must still produce the first.
    claims = Claims()
    # What poolhound local's activity list reads. See Activity.
    activity = Activity()
    # RECORD-ONLY, ON LOOPBACK. See CLAIM_HOST for what this can and cannot do.
    # A failure to bind is not fatal: the watch is the floor and still captures
    # every change from every door, with no door named.
    try:
        claim_srv = claim_server(claims, activity=activity)
        threading.Thread(target=claim_srv.serve_forever, daemon=True,
                         name="poolhound-claims").start()
        log(f"claims on http://{CLAIM_HOST}:{CLAIM_PORT}/claim, "
            f"activity at /activity")
    except OSError as e:
        # NAMES THE FIX, because the first time this fired it said only that
        # the address was in use -- true, and silent about the one-line remedy.
        log(f"claim endpoint not listening on {CLAIM_HOST}:{CLAIM_PORT} "
            f"({type(e).__name__}: {e})")
        log(f"  set POOLHOUND_CLAIM_PORT to a free port in "
            f"/etc/poolhound/agent.env if something else holds it")
        log(f"  changes are still captured from every door; none will be named")
    threading.Thread(target=watcher, args=(server, stop),
                     kwargs={"claims": claims, "activity": activity},
                     daemon=True, name="poolhound-watcher").start()

    # THE CLOCK STARTS AT LAUNCH, not at first contact. Otherwise link_age()
    # is None until the stream opens and the watchdog would ping forever on a
    # process that never connected at all -- which is the failure it is for.
    link_alive()
    if os.environ.get("NOTIFY_SOCKET"):
        threading.Thread(target=systemd_watchdog, args=(stop,), daemon=True,
                         name="poolhound-watchdog").start()
        log(f"systemd watchdog: pinging while the link is under "
            f"{LINK_STALE_S}s old")

    while True:
        try:
            for cmd in server.stream():
                backoff = 1.0
                cid = str(cmd.get("id", ""))[:8]
                try:
                    result = execute(cmd, guard)
                    log(f"ran {cid} {C.describe(cmd)} -> {result['detail']}")
                except C.Refused as e:
                    result = {"ok": False, "refused": str(e)}
                    log(f"refused {cid}: {e}")
                except Exception as e:
                    result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                    log(f"failed {cid}: {e}")
                # BEFORE the ack, which is a network call of up to 20s: the
                # watcher sees the panel move within about ten, and finding no
                # app row yet it would credit the change to the panel.
                record_command(activity, cmd, result)
                server.ack(cmd.get("id"), result)

                # Read the panel back and push it, on its own thread so the
                # command stream is not blocked while this waits for the panel
                # to catch up. Only for commands that actually did something —
                # a refusal has nothing to confirm.
                if result.get("ok") and cmd.get("action") in ("set", "setpoint", "all_off"):
                    threading.Thread(target=confirm_and_push, args=(server, cmd),
                                     daemon=True,
                                     name="poolhound-confirm").start()

            log("stream closed by the other end")
        except KeyboardInterrupt:
            log("stopping")
            stop.set()
            return 0
        except (urllib.error.URLError, socket.timeout, ssl.SSLError, OSError) as e:
            log(f"stream lost ({type(e).__name__}); retrying in {backoff:.0f}s")
        except Exception as e:
            log(f"unexpected {type(e).__name__}: {e}; retrying in {backoff:.0f}s")
        # This link drops: the wifi here runs around -63 dBm and has before.
        # Backoff keeps a server outage from becoming a connection storm.
        time.sleep(backoff)
        backoff = min(backoff * 2, 60.0)

if __name__ == "__main__":
    sys.exit(main())
