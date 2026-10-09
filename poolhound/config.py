"""Site configuration.

Kept out of the code so the repository can be published without carrying one
household's addresses, pool volume or credentials. Resolution order:

  1. $POOLHOUND_CONFIG
  2. ./config/config.toml          (gitignored)
  3. ~/.config/poolhound/config.toml

Secrets are never stored here — the WaterGuru password lives in its own file,
referenced by path, so config.toml stays safe to read and diff.
"""
import os, shutil, sys

# tomllib arrived in Python 3.11. The system python on macOS is 3.9, and a
# launchd job that resolves `env python3` gets whatever PATH it inherited at
# bootstrap — which is not necessarily the python that was used to install
# anything. Failing here with the reason beats failing later with an import
# traceback in a log nobody is reading.
try:
    import tomllib
except ModuleNotFoundError:
    sys.exit(f"poolhound needs Python 3.11 or newer for tomllib; this is "
             f"{sys.version.split()[0]} at {sys.executable}. Point the launchd "
             f"job at the project's .venv/bin/python.")

DEFAULTS = {
    "waterguru": {"credentials": "~/.waterguru"},
    "paths": {"data": "data", "site": "site"},
}

# THE POOL'S TIMEZONE, AND IT IS LOAD-BEARING RATHER THAN COSMETIC.
#
# Two of this product's rules are answered in LOCAL time and cannot be answered
# any other way:
#
#   * best_lab() compares a WaterGuru UTC instant against a bare Leslie's
#     local date. 02:00Z is the previous evening here, so which lab is "most
#     recent" depends on the offset — and getting it wrong means the page
#     quotes a stale alkalinity, which is the exact defect behind "say what the
#     data says, not what a chart says".
#   * edit_chemical() stores the canonical dose timestamp as
#     `when.astimezone().strftime(...)`, so the single spelling every dose is
#     indexed by is the HOST's offset.
#
# The container therefore pins TZ, in deploy/Dockerfile and in
# compose.poolhound.yml. This is the one place the zone is NAMED, and
# checks_gates.t_the_deployment_pins_the_pools_timezone() asserts both of those
# files still agree with it — they cannot import this, so the copies are
# checked rather than removed.
#
# It is NOT read from config.toml. A zone that could differ per install would
# be a zone that silently differs from the one the container runs in, and the
# failure mode is a wrong lab quoted with no error anywhere. An install
# elsewhere changes this constant and the two deploy files together, and the
# check holds them to each other.
#
# MEASURED: the first CI run — on a UTC runner — failed four cases that pass on
# a Pacific workstation. The product was right both times; the suite was
# assuming the host's zone was the pool's.
DEPLOY_TZ = "America/Los_Angeles"

def _deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _deep_merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out

def _candidates():
    env = os.environ.get("POOLHOUND_CONFIG")
    if env: yield os.path.expanduser(env)
    # Alongside the data. On the server that is the Azure Files share, which means
    # the config survives a container rebuild and a VM rebuild for the same
    # reason the CSVs do — it is the one copy, not a copy.
    d = os.environ.get("POOLHOUND_DATA")
    if d: yield os.path.join(os.path.expanduser(d), "config.toml")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    yield os.path.join(here, "config", "config.toml")
    yield os.path.expanduser("~/.config/poolhound/config.toml")


# FILES THE PRODUCT READS AT RUN TIME, as against files a check reads.
#
# MEASURED: every alert email since the move to the server went out with no
# logo. mail.send() reads docs/poolhound-mark-email.png and, not finding it,
# sends the rest rather than failing -- which is the right behaviour and is why
# nothing ever said so. The Dockerfile copies docs/screenshots/cards/ and not
# that file, so it was never in the image at all.
#
# AND A CHECK DID REPORT IT, in the one wording that reads as harmless. The
# mail case calls skipped("...", "docs/poolhound-mark-email.png is not in this
# tree"), which is honest and correct for a stripped checkout. In the IMAGE
# build the same sentence means "this product cannot send a logo here", which
# is a defect -- so "not in this tree" was doing duty for two different facts
# and the louder one was invisible. The distinction this table draws is the
# fix: a README the image does not carry is absent on purpose, and one of these
# is a fault wherever it is missing.
#
# Keyed by repo-relative path, valued by what reads it, so a check can name the
# consequence rather than just the file.
RUNTIME_ASSETS = {
    "docs/poolhound-mark-email.png":
        "mail.py attaches it by Content-ID; without it every alert email "
        "sends with no logo, and sends successfully",
    "docs/screenshots/cards":
        "render.py copies these into the site directory for the landing "
        "page; the container has no browser and cannot retake them",
    "config/config.example.toml":
        "config.seed_config() copies it on a first run",
    "deploy/poolhound-cron":
        "cadence.py reads the housekeeping schedule and the pulls' default "
        "hour from it",
}


def runtime_asset(rel):
    """The absolute path of one declared runtime asset."""
    return os.path.join(root(), *rel.split("/"))


def missing_runtime_assets(root_dir=None):
    """Declared assets this installation does not actually have.

    Not a skip. These are not documentation: each one is something the product
    reads while it is running, so an absent one is a fault wherever it happens
    -- which is the whole distinction the comment above draws.
    """
    base = root_dir or root()
    return sorted(rel for rel in RUNTIME_ASSETS
                  if not os.path.exists(os.path.join(base, *rel.split("/"))))


def on_deployment():
    """Is this the host the collectors actually run on?

    THE SAME MARKER refuse_off_deployment() turns on, and for the same reason:
    POOLHOUND_DATA is set by the image and not by a checkout, so it is already
    what distinguishes a durable deployment from a scratch directory
    everywhere else in this product.

    It is asked here because "is this job installed" is only a question the
    host running the job can answer. Settings used to branch on
    vault.keyvault_name() instead and then check the filesystem regardless, so
    a workstation -- where no collector may run at all, by design -- was told
    in a red warning that "nothing is collecting on that schedule". True, and
    not a fault: the server is collecting, and this host cannot see it.
    """
    return bool(os.environ.get("POOLHOUND_DATA"))


def refuse_off_deployment(tool):
    """Stop a collector that is being run somewhere it must not collect.

    THE COLLECTION ALREADY RUNS ON THE WEB SERVER. Running one here as well is
    never useful and is occasionally destructive: the readings live on an Azure
    Files share mounted only on the server, so a pull started from a laptop appends
    to a data directory nothing serves and nothing reconciles, and the two sets
    quietly stop agreeing. The vendor pulls also cost something real -- the
    WaterGuru project asks for no more than a call or two a day and this would
    spend one of them to write a file nobody reads.

    It is not hypothetical. Two fabricated doses were written into this
    household's chemicals.csv from a workstation during one review, and a
    shadow data directory accumulated here for eight days after the move to
    the server before anybody noticed it existed.

    POOLHOUND_DATA IS THE MARKER, and not only because it is convenient. It is
    already the thing that distinguishes a durable deployment from a scratch
    checkout everywhere else in this product: save_settings() refuses to write
    anywhere but $POOLHOUND_DATA/config.toml when it is set, because anything
    else is inside a container and dies on the next --force-recreate. The image
    sets it. A workstation does not. So a host where it is unset is a host with
    no durable place to put a reading, which is the whole argument in one fact.

    The override exists because a refusal with no way past it gets removed by
    the next person who has a real reason. Developing a parser does NOT need
    it: `--dry-run` and `--from-file` are the documented way and are what the
    saved responses in the share's raw/ directory are for.
    """
    import os
    if os.environ.get("POOLHOUND_DATA") or os.environ.get("POOLHOUND_COLLECT_ANYWAY"):
        return
    sys.exit(
        f"  {tool} is refusing to run here.\n"
        f"\n"
        f"  Collection runs on the web server, from /etc/cron.d/poolhound, and\n"
        f"  the readings live on a share that is mounted there and nowhere else.\n"
        f"  This host has no POOLHOUND_DATA, so anything written would go into a\n"
        f"  second copy of the pool's history that nothing serves.\n"
        f"\n"
        f"  To see what it would have done, without writing or spending a call:\n"
        f"      bin/{tool} --dry-run\n"
        f"  To run it here anyway, deliberately:\n"
        f"      POOLHOUND_COLLECT_ANYWAY=1 bin/{tool}\n")


def load():
    for p in _candidates():
        if p and os.path.exists(p):
            with open(p, "rb") as f:
                cfg = _deep_merge(DEFAULTS, tomllib.load(f))
            cfg["_source"] = p
            return cfg
    # NAME THE DURABLE PATH FIRST. This said "copy config/config.example.toml to
    # config/config.toml", which is relative to WORKDIR -- inside the container
    # on the server. An operator who followed it got a working site whose every saved
    # setting (volume, targets, notification settings, credential paths) was
    # written back into the container layer and destroyed by the next
    # --force-recreate, while the page reported each save as succeeding.
    #
    # _candidates() already PREFERS $POOLHOUND_DATA/config.toml, which on the server
    # is the Azure Files share. The message just never agreed with it.
    tried = [p for p in _candidates() if p]
    lines = ["no config found. poolhound looked, in order:"]
    lines += [f"    {p}" for p in tried]
    d = os.environ.get("POOLHOUND_DATA")
    if d:
        durable = os.path.join(os.path.expanduser(d), "config.toml")
        lines.append("")
        lines.append(f"  Put it at {durable} — that is the one that survives a")
        lines.append("  container rebuild, because it lives beside the readings rather")
        lines.append("  than inside the image. Start from config/config.example.toml.")
    else:
        lines.append("")
        lines.append("  Copy config/config.example.toml to config/config.toml.")
    sys.exit("\n".join(lines))

def volume(cfg=None):
    """This pool's volume in gallons, or None if nobody has said what it is.

    The distinction matters more here than anywhere else in the product. Every
    dose figure multiplies through this number, and it was defaulted to 15000 in
    nine separate places — so an install that had been told nothing about any
    pool presented "estimated effect on 15,000 gal: -8 ppm alkalinity, -0.21 pH"
    as though it were about the reader's water, and the Pool chemistry tab said
    the dose reference was "computed from 15,000 gallons, not copied from a
    generic chart" when it was exactly a generic chart for a notional pool.

    Returning None makes "not set" a state a caller has to handle, which is what
    the two fields beside it in Settings already do correctly: they render empty
    with "not measured yet" rather than inventing a figure.
    """
    cfg = cfg if cfg is not None else load()
    raw = (cfg.get("pool") or {}).get("volume_gallons")
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None

def pump_watts(cfg=None):
    """The pump's RATED draw in watts, or None if nobody has said.

    Deliberately separate from the measured `pump_watts` column in samples.csv.
    A rating is a number off a label; a measurement is what the pump actually
    drew. An energy figure built from the rating is an estimate and has to say
    so, which is the same distinction config.volume() draws and for the same
    reason -- see its docstring.
    """
    cfg = cfg if cfg is not None else load()
    raw = (cfg.get("pool") or {}).get("pump_watts")
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if 0 < v <= 10000 else None

def kwh_cost(cfg=None):
    """Cost of a kilowatt-hour in the local currency, or None if unset.

    No default. A made-up tariff turns an honest energy figure into a fabricated
    money figure, and money is the number people act on hardest.
    """
    cfg = cfg if cfg is not None else load()
    raw = (cfg.get("power") or {}).get("kwh_cost")
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if 0 < v < 100 else None

# What this household pays for each chemical, keyed by the catalogue's name,
# in the unit the upkeep table quotes it in. The setting's own name says the
# unit, so a price typed per bag cannot be mistaken for a price per pound.
PRICE_SETTINGS = {"acid": "acid_per_gal", "chlorine": "chlorine_per_gal",
                  "shock": "shock_per_lb", "bicarb": "bicarb_per_lb",
                  "soda_ash": "soda_ash_per_lb", "calcium": "calcium_per_lb",
                  "cya": "cya_per_lb", "cya_liquid": "cya_liquid_per_gal",
                  "nophos": "nophos_per_gal", "salt": "salt_per_lb"}


def chemical_price(chem, cfg=None):
    """What this household pays per unit of `chem`, or None if nobody has said.

    None, not a default, for the reason kwh_cost() gives. The reference prices
    in chemicals.py are a separate, labelled thing the caller may fall back
    to; this answers only what was entered.
    """
    cfg = cfg if cfg is not None else load()
    raw = (cfg.get("prices") or {}).get(PRICE_SETTINGS.get(chem, ""))
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if 0 < v < 1000 else None


def root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def data_dir(cfg=None):
    # The environment wins over the config file so the container can be pointed
    # at a mounted share without a config edit — the image then carries no
    # deployment-specific path and is the same artefact everywhere.
    env = os.environ.get("POOLHOUND_DATA")
    if env:
        d = os.path.expanduser(env)
        os.makedirs(d, exist_ok=True)
        return d
    cfg = cfg or load()
    d = os.path.expanduser(cfg["paths"]["data"])
    if not os.path.isabs(d): d = os.path.join(root(), d)
    os.makedirs(d, exist_ok=True)
    return d

def site_dir(cfg=None):
    env = os.environ.get("POOLHOUND_SITE")
    if env:
        d = os.path.expanduser(env)
        os.makedirs(d, exist_ok=True)
        return d
    cfg = cfg or load()
    d = os.path.expanduser(cfg["paths"]["site"])
    if not os.path.isabs(d): d = os.path.join(root(), d)
    os.makedirs(d, exist_ok=True)
    return d


def caddy_vhost():
    """The Caddy vhost template in this checkout, or None if there is none.

    FOUND BY GLOB, NOT BY NAME. Three call sites each spelled the filename
    out, and the filename carried the deployment's own domain — so the one
    fact that had to change for this repository to be publishable was written
    in four places, one of which was the file itself. The image carries no
    deploy/ directory at all, which is why None is an answer rather than an
    error.
    """
    import glob
    hits = sorted(glob.glob(os.path.join(root(), "deploy", "*.caddy")))
    return hits[0] if hits else None


# WHAT COUNTS AS "THIS DEPLOYMENT ALREADY HAS READINGS".
#
# config.toml is deliberately NOT in this list. It is the one file in the data
# directory that a deploy script writes by itself, so its presence says only
# that a deploy ran -- not that the share behind the directory is mounted. The
# guard in seed_config() asked about config.toml and was therefore disarmed by
# the very script it was protecting; it asks about these instead.
HISTORY_FILES = ("samples.csv", "readings.csv", "leslies.csv", "lab.csv",
                 "chemicals.csv", "manual.csv")

def has_history(data):
    """True when `data` holds any file that only a running pool produces."""
    return any(os.path.exists(os.path.join(data, f)) for f in HISTORY_FILES)


def seed_config(root=None):
    """Put config.toml beside the readings if it is not there yet.

    AND REFUSE TO DO IT ON A DEPLOYMENT THAT ALREADY HAS A HISTORY. This
    function nearly cost a pool its record. On the author's server /data is a
    bind mount of an Azure Files share, and the share silently failed to mount
    after a reboot -- so /data was an empty directory on local disk. The
    version of this without the guard below would have seeded a fresh config
    there, started cleanly, and written a second pool history to a disk nothing
    serves and nothing reconciles, which is the exact failure CLAUDE.md spends
    a paragraph on. It was only found because the OLD image was still deployed
    and crash-looped instead: the loud failure was the thing that saved it.

    POOLHOUND_REQUIRE_DATA=1 says "this deployment already has readings". Set
    it wherever /data is a mount of something, and an empty /data then stops
    the container with the cause named rather than being quietly populated.
    A first install does not set it, and seeding works as before.

    `os.path.ismount` cannot answer this: docker makes /data a mount point
    inside the container whether or not the HOST path it points at is mounted,
    so it is True in both the healthy and the broken case. The flag is the
    only honest signal available in here.
    """
    data = os.environ.get("POOLHOUND_DATA")
    if not data:
        return                      # not a durable deployment; config.py decides

    # THE GUARD ASKS ABOUT THE READINGS, AND IT ASKS FIRST.
    #
    # It used to sit BELOW `if os.path.exists(target): return` and test whether
    # config.toml was missing. config.toml is the one file in this directory a
    # DEPLOY SCRIPT writes by itself -- bootstrap-server.sh seeds it from the
    # host, onto the share, before the container starts -- so the one condition
    # that disarmed the guard was the condition the deploy creates. Run
    # bootstrap while the CIFS mount is down and the sequence was: the mount
    # fails silently (nofail), the script seeds config.toml onto the empty
    # local directory underneath, the container starts, this function returns
    # on its first line, and the collectors begin a SECOND pool history. That
    # is the precise failure the docstring above says this exists to prevent,
    # arriving through the door marked "the safe path".
    #
    # A config is not a history. The question is whether the READINGS are here.
    if os.environ.get("POOLHOUND_REQUIRE_DATA", "").strip() not in ("", "0") \
            and not has_history(data):
        try:
            listing = sorted(os.listdir(data))
        except OSError as e:
            listing = [f"<unreadable: {e}>"]
        sys.exit(
            f"REFUSING TO START. {data} holds none of {', '.join(HISTORY_FILES)},\n"
            f"and this deployment is marked as one that already has readings "
            f"(POOLHOUND_REQUIRE_DATA is set).\n\n"
            f"{data} currently holds: {listing or 'nothing at all'}\n\n"
            f"That almost certainly means the share or disk behind {data} is\n"
            f"NOT MOUNTED. Seeding a fresh config here would start a SECOND\n"
            f"pool history on whatever filesystem is underneath, which nothing\n"
            f"serves and nothing reconciles -- so this stops instead.\n\n"
            f"On the host:   mount | grep {data}\n"
            f"               sudo mount {data}\n"
            f"               systemctl status $(systemd-escape -p --suffix=mount {data})\n")

    target = os.path.join(data, "config.toml")
    if os.path.exists(target):
        return
    example = os.path.join(root or globals()["root"](), "config",
                           "config.example.toml")
    if not os.path.exists(example):
        return
    try:
        os.makedirs(data, exist_ok=True)
        # Written beside the target and renamed, so a container killed
        # mid-copy leaves no half-file for the next start to read as config.
        tmp = target + ".seeding"
        shutil.copyfile(example, tmp)
        os.replace(tmp, target)
    except OSError as e:
        print(f"could not seed {target}: {e}", file=sys.stderr)
        return
    print(f"first run: seeded {target} from config.example.toml. "
          f"Set your pool's volume on the Settings tab, or edit that file.",
          file=sys.stderr)
