#!/usr/bin/env python3
"""Render the collected data into a single static dashboard page.

WHY A STATIC FILE AND NOT A SERVER
  Everything this page shows is already on disk in four CSVs written by cron.
  A server would add a process to supervise, a port to firewall and a runtime
  dependency to install, in exchange for nothing — the data changes four times
  an hour at most. `render` writes site/index.html; open it, or point any web
  server at site/. It also means the page survives the repo being copied to a
  machine with no Python at all.

WHAT THE PAGE IS TRYING TO SHOW
  A pool reading on its own is a number without a cause. The WaterGuru says pH
  7.8 on Tuesday; the useful question is what the pool DID between Monday's
  reading and Tuesday's — how long the pump ran, how long the spa spilled over,
  whether the sheer descent was on, how hard the cell was driving. That is why
  the centre of this page is not the chart but the DAY STRIP: one 24-hour lane
  per day showing every piece of equipment that ran, with that day's chemistry
  printed at the end of the row. Cause on the left, effect on the right, one row
  per day, so the eye does the correlation before the regression ever runs.

COLOUR
  Three equipment series only, in fixed order, from a palette checked with the
  dataviz validator for CVD separation and contrast on both surfaces:
     pump #2a78d6   spa #eb6834   sheer #1baf7a
  The sheer green sits at 2.74 contrast on the light surface, which is legal
  only with a non-colour fallback — hence the always-present legend, the direct
  hour labels, and the full table view at the bottom of the page.
"""
import contextlib, csv, datetime as dt, html, json, math, os, re, sys
from . import config, style, locking
from . import access as _access
from . import brand as _brand
from .icons import icon, icon_for
from . import help as helpdoc

CFG  = config.load()
DATA = config.data_dir(CFG)
SITE = config.site_dir(CFG)
POOL = CFG.get("pool", {})

def reload():
    """Re-read the configuration into this module's globals.

    Everything below reads CFG/POOL/DATA/SITE as module globals, bound once when
    the module was imported. That is right for a one-shot `bin/render` and wrong
    for the server, which renders in its OWN process after every write: a
    Settings save reached config.toml and the page handed straight back to the
    browser was still drawn from whatever this process happened to start with.
    It presented as "saving the pool volume does nothing", and worse — the dose
    catalogue shipped to the browser carried the stale figure while
    /api/chemical, which reloads per request, logged against the new one.

    Called at the top of build(), which is the only place that matters: every
    render goes through it, and nothing else caches a derived value past it.
    """
    global CFG, DATA, SITE, POOL
    CFG  = config.load()
    DATA = config.data_dir(CFG)
    SITE = config.site_dir(CFG)
    POOL = CFG.get("pool", {})

def _config_dir():
    """The directory holding config.toml — where the "config" lock is taken.

    server.edit_toml() locks on `dirname(config.toml)`, so anything that wants
    to read the file without a write landing halfway has to name the same place.
    config.load() records where it found the file; the fallback is the data
    directory, which is where a deployed install keeps it anyway.
    """
    src = CFG.get("_source")
    return os.path.dirname(os.path.abspath(src)) if src else config.data_dir(CFG)

# Definitions for the terms that carry weight here and are opaque to anyone who
# has not read the chemistry tab. Kept as Python and serialised to JSON rather
# than written as a JavaScript object literal: an apostrophe in "the water's
# resistance" silently ended the string and took the whole script with it, which
# is a bug this shape cannot have.
GLOSSARY = {
    "cya": "Cyanuric acid — chlorine stabiliser. It shields chlorine from UV, and it "
           "sets the free-chlorine target: aim for about 7.5% of the CYA reading.",
    "free chlorine": "Chlorine still available to sanitise, as opposed to chlorine "
                     "already spent on something. Its correct level is a fraction of "
                     "cyanuric acid, not a fixed number.",
    "alkalinity": "Dissolved carbonates — the water's resistance to pH change, and the "
                  "reservoir that feeds pH back up after acid. A salt pool wants less "
                  "of it than a chlorine pool, because the cell pushes pH up all day.",
    "saturation index": "Whether the water will deposit calcium or dissolve it. Below "
                        "about -0.3 it takes calcium out of plaster and grout; above "
                        "+0.3 it leaves scale.",
    "swg": "Salt water generator — the cell that makes chlorine from dissolved salt "
           "while the pump runs.",
    "hocl": "Hypochlorous acid — the form of free chlorine that actually kills things. "
            "How much of your chlorine is in this form is decided by pH.",
}

# --- series palette (validated; do not recolour without re-running the checker)
# From style.SERIES, which is the one place it is written down. These used to be
# re-typed here, so the CSS and the SVG charts this module draws could disagree
# and nothing would say so — the legend and the line are the same decision.
C_PUMP, C_SPA, C_SHEER = style.SERIES["pump"], style.SERIES["spa"], style.SERIES["sheer"]

# --------------------------------------------------------------------- loading
# Which CSVs a correction can speak about, by the source name corrections.csv
# uses. Anything not here (samples, chemicals) is read untouched.
CORRECTABLE = {"readings.csv": "readings", "lab.csv": "lab",
               "leslies.csv": "leslies", "manual.csv": "manual"}

def rows(name):
    """Read a CSV, with any corrections already applied.

    Corrections used to be applied in build() instead, which was described as
    "once, at the point everything else reads from". It was not: Pool chemistry,
    two Settings panels and — worst — the notifier all called rows() directly and
    got the raw file, so a reading the owner had explicitly marked as fouled went
    on driving the public chemistry narrative and the alerts after Home had
    stopped showing it. Applying them HERE is what that sentence always meant;
    there is now no raw path left to take by accident.
    """
    p = os.path.join(DATA, name)
    if not os.path.exists(p): return []
    with open(p, newline="") as f:
        out = [r for r in csv.DictReader(f)]
    src = CORRECTABLE.get(name)
    if src:
        from . import corrections as CORR
        out = CORR.apply(out, src)
    return _dedup_measurements(out, name)


# One measurement recorded twice is still one measurement.
#
# Both labs return their WHOLE history on every pull and the collectors dedup on
# the measurement timestamp — but the dedup used to READ the file, decide, and
# only then take the lock, so two runs could both find a timestamp absent and
# both write it. That race is fixed; the row it already produced is still on
# disk, and this household's readings.csv carries the 2026-09-08 reading twice.
#
# HISTORY IS NOT REWRITTEN — the same rule drop_stale_flow_readings() follows,
# and the reason corrections.csv exists rather than a delete. So the duplicate
# is collapsed on the way IN, once, in the one function everything reads
# through. Not a correction either: a correction records a judgement that a
# reading was wrong, and this reading is fine. It was just filed twice.
#
# dose_response_block() had been carrying its own copy of this, inline, because
# pairing a dose against a doubled reading is visibly wrong. Every other reader
# — the averages, the charts, the notifier — had no such workaround and counted
# the measurement twice.
DEDUP_ON = {
    "readings.csv": "measured",
    "lab.csv": "measured",
    "leslies.csv": "measured",
    "manual.csv": "measured",
}

def _dedup_measurements(out, name):
    """Collapse rows that describe the same measurement of the same file.

    Keyed to the minute, because that is the resolution the collectors dedup at
    and a re-pull can differ in seconds. The FIRST occurrence is kept: it is the
    one every earlier render already showed, so this changes what is counted
    without changing what is displayed.
    """
    key = DEDUP_ON.get(name)
    if not key:
        return out
    seen, keep = set(), []
    for r in out:
        k = (r.get(key) or "")[:16]
        if not k:
            keep.append(r)          # nothing to dedup on; never silently dropped
            continue
        if k in seen:
            continue
        seen.add(k)
        keep.append(r)
    return keep

def newest(rs, field="measured"):
    """The row with the latest parsed timestamp, not the last one in the file.

    File order is append order, and append order is not measurement order the
    moment anything backfills — a re-pull, a manual `--from-file` run, two
    collectors finishing out of sequence. Rows whose timestamp will not parse
    sort last among themselves rather than winning outright, so a malformed cell
    cannot promote itself to "current".
    """
    if not rs:
        return {}
    dated = [(when(r.get(field)), i, r) for i, r in enumerate(rs)]
    good = [d for d in dated if d[0] is not None]
    if not good:
        return rs[-1]
    return max(good, key=lambda d: (d[0], d[1]))[2]

def current_ta(default=90.0):
    """The newest alkalinity either lab reports, reconciled the usual way.

    A loader, and here rather than in a panel, because more than one place needs
    it and they must not disagree: the Pool chemistry dose tables and the dose
    catalogue shipped to the browser both scale acid by it. Acid moves pH
    further in a less buffered pool, so quoting a dose against the wrong
    alkalinity is wrong by the ratio of the two — 23% at this pool's measured 73
    against the 90 default, in the overdose direction.
    """
    try:
        cur = best_lab((newest(rows("lab.csv")), "WaterGuru lab"),
                       (newest(rows("leslies.csv")), "Leslie's"),
                       (newest(rows("manual.csv")), "By hand"))
        entry = (cur or {}).get("ta") or {}
        got = num(entry.get("value") if isinstance(entry, dict) else entry)
        return got if got else default
    except Exception:
        return default


def ta_is_measured():
    """Has anybody actually measured this pool's alkalinity?

    current_ta() falls back to 90 when nothing has, and every acid dose scales
    by it -- 23% in the OVERDOSE direction against this pool's measured 73, by
    that function's own arithmetic. The volume has three separate disclosures
    for exactly this situation and alkalinity had none, so an install with no
    lab result quoted acid doses against an invented buffer as confidently as
    against a measured one.

    Same shape as config.volume() returning None: the caller has to ask, so it
    cannot forget.
    """
    try:
        cur = best_lab((newest(rows("lab.csv")), "WaterGuru lab"),
                       (newest(rows("leslies.csv")), "Leslie's"),
                       (newest(rows("manual.csv")), "By hand"))
        entry = (cur or {}).get("ta") or {}
        return num(entry.get("value") if isinstance(entry, dict) else entry) is not None
    except Exception:
        return False

def num(v):
    """CSV cells are strings and often empty. Anything unparseable is absent,
    not zero — a missing SWG reading must not average in as 0%.

    AND THE PANEL'S SENTINEL IS ABSENT TOO. AqualinkD answers commands.NO_READING
    for a measurement it cannot take, and float() turns that into a perfectly
    ordinary number. drop_stale_flow_readings() blanks those at WRITE time and
    CLAUDE.md is explicit that history is never rewritten — so any sentinel that
    reached the file before that rule existed, or by a path that bypasses it, is
    still on disk and was still being averaged as a measurement.

    Measured: 23 honest readings of 3315 ppm salt plus ONE sentinel row put the
    published figure at 3135.2 — a 180 ppm error on the public page, from a
    value the panel used to say "I cannot measure that right now".

    The docstring above already makes this argument for the empty string. The
    sentinel is the same claim in a different spelling, and was not held to it.
    """
    try:
        if v is None or v == "": return None
        f = float(v)
    except (TypeError, ValueError):
        return None
    # commands.py owns the sentinel; <= because the panel has answered -1000
    # and worse on a cold bus, and none of those is a reading either.
    from . import commands as _c
    return None if f <= _c.NO_READING else f

def when(v):
    """Parse either flavour we store: local-with-offset from our own writers,
    UTC 'Z' from the WaterGuru payload. Returned naive-local so every timestamp
    on the page sits on the same clock the pool actually runs on."""
    if not v: return None
    try:
        t = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.astimezone().replace(tzinfo=None) if t.tzinfo else t

# ------------------------------------------------------------- lab reconciliation
# Measures that come from a lab rather than the pod.
LAB_MEASURES = ["ta", "ch", "cya", "salt", "phosphates", "copper", "iron",
                "saturation_index", "tds", "total_hardness", "borate"]

# The three the pod reads daily -- and which used to be reconciled NOWHERE.
#
# They were excluded from LAB_MEASURES on the reasoning that "the pod reads them
# every day, and for those two the recency of a daily reading beats the
# precision of a monthly one". That is right whenever there IS a pod. It also
# meant pH and free chlorine could enter this product through exactly one door:
# readings.csv, the WaterGuru pod. The hand-entry form accepts them, validates
# them, stores them in manual.csv and audits them -- and every consumer read
# `latest`, so on an install with a test kit and a Leslie's history and no pod
# subscription the two measures that drive nearly every decision this product
# exists to support both read "unknown", and "What to do next" offered no acid
# dose and no chlorine dose at all.
#
# Reconciling them the same most-recent-wins way, WITH the pod as one of the
# sources, keeps the original reasoning intact: a pod reading daily wins on
# recency every time, automatically, and when there is no pod the test kit is
# used instead of nothing.
DAILY_MEASURES = ["ph", "free_cl", "total_cl"]

def best_lab(*sources, measures=None):
    """Reconcile the lab sources into one current value per measure.

    Both sources are laboratories: WaterGuru analyses a mailed-in sample,
    Leslie's runs a bench photometer on one carried into the store. Neither is
    the junior partner, so the rule is not "prefer the better instrument" — it is
    simply MOST RECENT WINS, per measure. Calcium moves slowly and cyanuric acid
    barely at all, but alkalinity can shift 20 ppm in a week on one dose of acid,
    so a three-week-old number is a historical fact rather than a current one.

    What is NOT done is averaging. Where two labs disagree, the mean is a number
    neither of them measured, and it hides the one question worth asking: did the
    pool change, or did the labs?
    """
    out = {}
    for m in (measures or LAB_MEASURES):
        best = None
        for src, name in sources:
            v = num(src.get(m)) if src else None
            if v is None: continue
            d = src.get("measured", "")
            # Compared as TIMES, not as strings. The two sources do not store the
            # same shape: WaterGuru writes a UTC instant ("2026-08-14T20:52:32.662Z")
            # and Leslie's writes a bare local date ("2026-05-21"). Sorting those
            # lexicographically is only accidentally right -- a WaterGuru sample
            # at 02:00Z is the PREVIOUS evening locally, so against a Leslie's
            # test dated that next day the string compare picks WaterGuru and the
            # clock says Leslie's. "Most recent wins" then silently loses by up to
            # a day, on the reconciliation the whole two-labs rule rests on.
            # when() already normalises both flavours to local; an unparseable
            # date sorts last rather than winning.
            t = when(d)
            if best is None or (t is not None and
                                (best["_t"] is None or t > best["_t"])):
                best = {"value": v, "source": name, "measured": d, "_t": t}
        if best:
            best.pop("_t", None)
            out[m] = best
    return out

# The smallest gap in each measure that would actually change what you do about
# it. A percentage alone is not enough to call an instrument unreliable: copper
# reading 0.1 against 0.2 is a 50% gap and also one significant digit on a number
# where both answers mean the same thing — "traces, nothing to act on". Without
# this floor the table would report rounding as a change in the pool.
MATTERS = {"ta": 10, "ch": 25, "cya": 10, "salt": 200, "phosphates": 100,
           "copper": 0.3, "iron": 0.3, "saturation_index": 0.3, "tds": 300,
           "total_hardness": 25, "borate": 10,
           # Also used to compare TARGETS, where the daily measures matter too.
           "ph": 0.15, "free_cl": 0.5}

def disagreements(older, newer):
    """Every measure both labs report, with the change between them.

    Deliberately NOT called "error". Two labs a week apart differ for two reasons
    that this table cannot tell apart: the labs measure differently, or the pool
    actually moved. Alkalinity falling 95 to 73 over seven days is exactly what
    one dose of muriatic acid does to this pool — so the chemical log, not this
    table, is what settles it.
    """
    out = []
    for m in LAB_MEASURES:
        a, b = num((older or {}).get(m)), num((newer or {}).get(m))
        if a is None or b is None: continue
        scale = max(abs(a), abs(b), 1e-9)
        rel = abs(b - a) / scale
        out.append({"m": m, "a": a, "b": b, "diff": b - a, "rel": rel,
                    "material": rel > 0.2 and abs(b - a) >= MATTERS.get(m, 0)})
    out.sort(key=lambda d: (-d["material"], -d["rel"]))
    return out

def lab_history(*sources):
    """Every lab measurement from every source, as one time series per measure.

    Merged for plotting but tagged by source, so a reader can see whether a jump
    is one lab disagreeing with another or the same lab watching the pool change.
    """
    series = {}
    for rowset, name in sources:
        for r in rowset:
            d = r.get("measured", "")
            if not d: continue
            for m in LAB_MEASURES:
                v = num(r.get(m))
                if v is None: continue
                series.setdefault(m, []).append({"d": d, "v": v, "src": name})
    for m in series:
        series[m].sort(key=lambda p: p["d"])
    return series

# How far ahead of us a timestamp may sit before it is a fault rather than a
# clock. The Pi, the server and two vendor APIs each keep their own time; five
# minutes is the tolerance save_reading() and the dose log already use, and this
# is that same decision in the one place every reader can see it.
CLOCK_SKEW = dt.timedelta(minutes=5)

def is_future(iso):
    """Is this timestamp implausibly ahead of now? Absent/unparseable is not."""
    d = when(iso)
    return bool(d) and d > dt.datetime.now() + CLOCK_SKEW

def ago(iso):
    """A gap in words. Resolves below a day, because the collectors are judged
    on hours: a sampler that last ran nine hours ago is a fault, and calling
    that "today" reads as though nothing is wrong.

    A GAP IN THE FUTURE IS NOT FRESHNESS. `secs` is negative for a timestamp
    ahead of now, and every branch below tested an upper bound only — so a row
    dated next year, or next century, read "just now". That is not cosmetic:
    collector_health() and the watchdog judge staleness on this same arithmetic,
    so ONE future-dated row made a dead collector look current, and bin/watch
    sent a RECOVERED all-clear for a sampler that had not returned data in a
    day. It then stayed quiet for as long as the bad timestamp stayed ahead.

    The "is this in the future" fact existed in exactly one place — the hand
    entry form, which refuses it — and in none of the three that share this
    arithmetic and are described as unable to disagree. They agreed on the
    wrong answer.
    """
    d = when(iso)
    if not d: return ""
    secs = (dt.datetime.now() - d).total_seconds()
    if secs < -CLOCK_SKEW.total_seconds():
        return "dated in the future"
    if secs < 90:            return "just now"
    if secs < 3600:          return f"{secs/60:.0f} min ago"
    if secs < 86400:
        h = secs / 3600
        return "1 hour ago" if h < 1.5 else f"{h:.0f} hours ago"
    days = secs / 86400
    return "1 day ago" if days < 2 else f"{days:.0f} days ago"

# EVERY MEASURE, not only the ones a lab chart draws. The three the pod reads
# daily were absent, so anything that looked a name up for them got the column
# key back -- "free_cl" where the rest of the page says "Free chlorine". The
# page itself never noticed because it writes those three as literals in the
# six places it needs them, which is the same fact in seven spellings.
MEASURE_NAMES = {"ph": "pH", "free_cl": "Free chlorine", "total_cl": "Total chlorine",
                 "ta": "Alkalinity", "ch": "Calcium", "cya": "Cyanuric acid",
                 "salt": "Salt", "phosphates": "Phosphates", "copper": "Copper",
                 "iron": "Iron", "saturation_index": "Saturation index",
                 "tds": "TDS", "total_hardness": "Total hardness", "borate": "Borate"}

# ------------------------------------------------------------ collector health
# How stale each source may get before silence is more likely than quiet.
# One set of thresholds, used by both the dashboard warning and the notifier,
# so the two can never disagree about whether a source has stopped.
#
# The pod measures once a day and the pull runs at 14:00, so 36 hours means one
# missed day raises it and a pull that merely runs late does not. The sampler
# runs every 15 minutes, so two hours is eight consecutive misses. Leslie's only
# updates when a sample is carried into a store, which is why its threshold is
# long enough to be effectively informational.
STALE_AFTER = {"sample": dt.timedelta(hours=2),
               "waterguru": dt.timedelta(hours=36),
               "leslies": dt.timedelta(days=75)}

# Which collectors need a credential before they can run at all, and the vault
# service each one asks for. The Pi's agent needs none — it is pushed to, not
# pulled from — so it is absent here on purpose and `_source_configured` says
# "cannot tell" for it rather than guessing.
NEEDS_CREDENTIAL = {"waterguru": "waterguru", "leslies": "leslies"}


def _source_configured(key):
    """True / False / None — has this collector been given what it needs?

    None means the question does not apply or could not be answered, and the
    caller must then say nothing about configuration rather than assume either
    way. A vault that cannot be read is exactly that case: reporting "not set
    up" because the store is corrupt would send somebody to re-enter
    credentials that are already there.
    """
    service = NEEDS_CREDENTIAL.get(key)
    if not service:
        return None
    try:
        from . import vault
        st = vault.status()
        if isinstance(st, dict) and st.get("error"):
            return None                      # unreadable, not unconfigured
        entry = (st.get("services") or {}).get(service) if isinstance(st, dict) else None
        if entry is None:
            return None
        return bool(entry.get("stored") or entry.get("legacy_file"))
    except Exception:                        # noqa: BLE001
        return None


def collector_health(samples, readings, leslies):
    # Is this the deployed host or a workstation? The same test panels.py uses
    # to disable the controller address on Settings.
    from . import vault as _v
    _KV = bool(_v.keyvault_name())

    """Say when a source has stopped, because stale data looks exactly like
    current data on a dashboard.

    Every number on this page is timestamped, but a reader scanning tiles sees
    values, not dates — so a collector that died three weeks ago presents as a
    calm, healthy pool. That failure is silent by construction and has to be
    surfaced deliberately.
    """
    now = dt.datetime.now()
    out = []
    for key, rowset, field, label, fix in (
        ("sample",    samples,  "ts",       "Pool controller",
         # The fix text has to name something reachable on THIS machine. On
         # the server the AqualinkD address on Settings is disabled and labelled
         # "Not used on this server", and ~/.waterguru does not exist -- the
         # credential is in Key Vault or in the store the Settings form writes.
         # So the one screen whose job is to say what to do sent the owner to a
         # field the same product calls inert, and to a file path that is
         # neither in use nor settable anywhere in the UI.
         ("Check the Pi agent is connected — see Pool control."
          if _KV else "Check the Pi is up and the address on Settings is right.")),
        ("waterguru", readings, "measured", "WaterGuru pod",
         ("Check the pod has battery and signal, and that the WaterGuru login "
          "on Settings is current."
          if _KV else
          "Check the pod has battery and signal, and that ~/.waterguru is valid.")),
        ("leslies",   leslies,  "measured", "Leslie's lab",
         "Nothing is broken — this only updates when a sample is taken in."),
    ):
        # A ROW DATED IN THE FUTURE CANNOT BE THE LAST THING WE RECEIVED.
        #
        # `last` was max() over every parsed timestamp, so one future-dated row
        # became the newest — and `now - last` is then NEGATIVE, which is never
        # greater than STALE_AFTER, so the source silently dropped out of this
        # list entirely. Measured: a dead sampler warned "last returned data
        # 1 day ago"; append one row dated a year ahead and bin/watch printed
        # RECOVERED for the same dead sampler, then stayed quiet for as long as
        # the bad timestamp stayed ahead of now.
        #
        # Excluded from the freshness question, and reported as its own fault,
        # because a collector writing timestamps we cannot have received is a
        # real problem — a clock wrong on the Pi, or a vendor returning
        # nonsense — and silently dropping the row would hide it the way the
        # negative gap did.
        ahead = [d for r in rowset
                 if (d := when(r.get(field))) and d > now + CLOCK_SKEW]
        ds = [d for r in rowset
              if (d := when(r.get(field))) and d <= now + CLOCK_SKEW]
        last = max(ds) if ds else None
        if ahead:
            out.append({"key": key + "-future", "label": label, "state": "warn",
                        "text": f"has {len(ahead)} reading(s) dated in the future "
                                f"(newest {max(ahead):%Y-%m-%d %H:%M}) — a clock is "
                                f"wrong somewhere, and they are not counted as fresh",
                        "fix": "Check the clock on the machine that wrote them."})
        if last is None:
            # "BROKEN" AND "NEVER SET UP" ARE DIFFERENT PROBLEMS.
            #
            # This decided a source was broken purely from "no rows in its
            # CSV", so an install with no WaterGuru account was told to check a
            # pod's battery and signal, and a credential file it was never
            # asked to create. On a drop-kit install three of the four
            # top-priority items were about services the owner does not have
            # and never will — and the very same page, in Settings, correctly
            # reported "Missing — no credentials — this source cannot run".
            # Two panels, one fact, opposite answers.
            configured = _source_configured(key)
            if configured is False:
                out.append({"key": key, "label": label, "state": "info",
                            "text": "is not set up",
                            "fix": f"Add the {label} login on Settings if you "
                                   f"have one. If you do not, nothing here is "
                                   f"wrong and this is the only place it will "
                                   f"be mentioned."})
            else:
                out.append({"key": key, "label": label, "state": "missing",
                            "text": "has never returned anything", "fix": fix})
        elif now - last > STALE_AFTER[key]:
            out.append({"key": key, "label": label,
                        "state": "warn" if key != "leslies" else "info",
                        "text": f"last returned data {ago(last.isoformat())}",
                        "fix": fix})
    return out

def trajectory(readings, samples, measure="free_cl", min_points=4):
    """Where a measure is heading, and when it gets there.

    A pool does not respond to a change in cell output the way a dimmer responds
    to a switch. Production is constant and loss is proportional to how much is
    there, so after any adjustment the level decays towards a new equilibrium:

        d(FC)/dt = P - k*FC     ->     settles at P/k

    Fitting P and k from consecutive readings turns "it is still falling" into a
    number and a date, which is the difference between waiting on purpose and
    waiting because nobody knows.

    The reason this matters more than it sounds: the natural move four days into
    a change is to make another one. Doing that stacks a second adjustment on an
    unconverged first, so neither can be attributed afterwards and the usual
    result is overshoot in the other direction.
    """
    rows_, seen = [], set()
    for r in sorted(readings, key=lambda r: r.get("measured", "")):
        k = (r.get("measured") or "")[:16]
        v = num(r.get(measure))
        d = when(r.get("measured"))
        if k and k not in seen and v is not None and d:
            seen.add(k); rows_.append((d, v))
    if len(rows_) < min_points:
        return None

    # Consecutive daily deltas against the midpoint value they apply to.
    pts = []
    for i in range(len(rows_) - 1):
        dt_days = (rows_[i + 1][0] - rows_[i][0]).total_seconds() / 86400.0
        if not (0.5 <= dt_days <= 2.5):
            continue
        pts.append(((rows_[i][1] + rows_[i + 1][1]) / 2,
                    (rows_[i + 1][1] - rows_[i][1]) / dt_days))
    # The first step after an adjustment is a transient, not part of the decay.
    pts = pts[1:]
    if len(pts) < 2:
        return None

    n = len(pts)
    sx = sum(a for a, _ in pts); sy = sum(b for _, b in pts)
    sxx = sum(a * a for a, _ in pts); sxy = sum(a * b for a, b in pts)
    den = n * sxx - sx * sx
    if abs(den) < 1e-9:
        return None
    slope = (n * sxy - sx * sy) / den
    k_loss = -slope
    prod = (sy - slope * sx) / n
    if k_loss <= 0.01:
        return None                      # not decaying towards anything

    # Production scales with pump runtime, and this pool's has been moving. The
    # fit averages whatever the runtime was across its window, so projecting
    # forward on that average is wrong whenever the schedule has changed —
    # which is exactly when someone is most likely to be asking.
    hours = {}
    for s in samples:
        d = (s.get("ts") or "")[:10]
        if d and s.get("pump") == "1":
            hours[d] = hours.get(d, 0) + 0.25
    # THE NEWEST SAMPLED DAY HAS NOT FINISHED, and counting its hours-so-far as
    # a day's runtime is how this told the page the opposite of the truth. Cron
    # renders every six hours, so most renders land mid-afternoon: at 14:10 the
    # day held 4.75 h against a 7.29 h average, the ratio came out 0.65, and
    # equilibrium_adjusted read 2.86 ppm -- under a 2.9 target -- while the same
    # pool at its last COMPLETE day (6.50 h) was settling towards 3.66. The
    # Power card then offered to give back runtime that was never spare, and an
    # owner reading it would have left a cell that is over-producing alone.
    #
    # Prorating the partial day by elapsed clock is worse, not better: the pump
    # runs in a block rather than uniformly, so 4.75 h seen by 14:10 scales up
    # to 8 h and overstates it in the other direction. The data itself says
    # which days are finished -- any day before the newest one sampled -- so the
    # partial day is dropped from BOTH statistics rather than estimated. It
    # rejoins the window tomorrow, whole.
    partial = max(((s.get("ts") or "")[:10] for s in samples), default="")
    window = [(d.strftime("%Y-%m-%d"), hours.get(d.strftime("%Y-%m-%d"), 0.0))
              for d, _ in rows_]
    window = [h for day, h in window if h > 0 and day != partial]
    ratio, recent, avg = 1.0, None, None
    if len(window) >= 2:
        avg = sum(window) / len(window)
        recent = window[-1]
        if avg > 0:
            ratio = recent / avg

    cur = rows_[-1][1]
    eq = prod / k_loss
    eq_adj = (prod * ratio) / k_loss
    out = {"current": cur, "k": k_loss, "production": prod,
           "equilibrium": eq, "equilibrium_adjusted": eq_adj,
           "pump_avg_h": avg, "pump_recent_h": recent, "pump_ratio": ratio,
           "project": []}
    for d in (2, 4, 7, 14):
        out["project"].append((d, eq_adj + (cur - eq_adj) * math.exp(-k_loss * d)))
    return out

# ---------------------------------------------------------------------- actions
def actions(LAB, latest, TG, chems, health, gallons, acid_pct, traj=None,
            DAILY=None, public=False):
    """Turn the state of the water into a ranked list of things to do.

    A dashboard that only reports leaves the reader to do the chemistry, which
    is the work this project exists to remove. Each item therefore carries the
    dose, computed for this pool, not just the observation.

    Ordering is by consequence, not by how far a number sits outside its band.
    Aggressive water dissolves plaster continuously and permanently; a high pH
    is a loss of sanitiser effectiveness that reverses the moment it is
    corrected. Sorting by percentage-out-of-range would put them the wrong way
    round.
    """
    from . import chemicals as CH
    out = []

    def dose(chem, target_effect, key):
        """How much of `chem` produces `target_effect` on `key`, in whole units
        someone can actually pour.

        Through chemicals.amount_for, and WITH the measured alkalinity. These
        cards carry the most prominent dose figure in the product — "Bring pH
        down: 1.5 qt" is what somebody acts on — and they were computing it at
        the 90 default like the copy in chemistry.py was.
        """
        # No volume, no dose. Every figure here multiplies through `gallons`,
        # so an install that has not been told how big the pool is must get no
        # number rather than a number about a notional 15,000-gallon pool. The
        # caller turns a None into an absent dose line, not into the string
        # "None".
        if gallons is None:
            return None
        c = CH.CHEMICALS[chem]
        base = CH.amount_for(chem, key, target_effect, gallons,
                             c["default_pct"], ta_now)
        if base is None:
            return None
        if c["phase"] == "liquid":
            return f"{base/128:.2f} gal" if base >= 128 else (
                   f"{base/32:.1f} qt" if base >= 32 else f"{base:.0f} fl oz")
        return f"{base:.1f} lb" if base >= 1 else f"{base*16:.0f} oz"

    def dose_line(chem, target_effect, key, of):
        """The whole "1.4 qt of 31.45% muriatic acid" line, or nothing at all.

        Every call site used to interpolate dose() into an f-string, so a dose
        that could not be computed rendered as the literal text "None of 77%
        calcium chloride" on the most prominent card in the product. An absent
        dose is an empty string, which the cards already handle — two of them
        pass dose="" deliberately.
        """
        amt = dose(chem, target_effect, key)
        return f"{amt} of {of}" if amt else ""

    ch  = LAB.get("ch", {}).get("value")
    ta  = LAB.get("ta", {}).get("value")
    ta_now = ta if ta else 90.0          # what the acid figures scale against
    cya = LAB.get("cya", {}).get("value")
    salt = LAB.get("salt", {}).get("value")
    # From the reconciliation, not from the pod alone. Reading `latest` here is
    # what left "What to do next" with no acid dose and no chlorine dose on an
    # install whose owner had typed both numbers in by hand that morning.
    DAILY = DAILY or {}
    ph  = (DAILY.get("ph") or {}).get("value")
    fc  = (DAILY.get("free_cl") or {}).get("value")
    if ph is None: ph = num(latest.get("ph"))
    if fc is None: fc = num(latest.get("free_cl"))

    def prov(*keys):
        """Which reading an item rests on, and how old it is.

        render.py:211's own docstring says "a reader scanning tiles sees values,
        not dates" — the tiles were given provenance for exactly that reason and
        the action cards, which are the things somebody actually acts on, were
        not. The top card can prescribe 19 lb of calcium chloride off a lab
        result three weeks old, and STALE_AFTER for Leslie's is 75 days, so no
        warning fires for another seven weeks.
        """
        bits = []
        for k in keys:
            e = LAB.get(k) or {}
            if e.get("measured"):
                age = ago(e["measured"])          # ago() takes the ISO string
                bits.append(f"{e.get('source', 'lab')} {age}".strip())
        return " · ".join(dict.fromkeys(bits))

    def prov_pod(*also):
        """Provenance for an item resting on the pod reading (pH, free chlorine),
        plus any lab measures it is judged against."""
        # Name the source that actually supplied the number. This said
        # "WaterGuru pod" unconditionally, so an action resting on a test-kit
        # reading was attributed to a pod the install may not even have.
        e = (DAILY.get("free_cl") or DAILY.get("ph") or {})
        raw = e.get("measured") or latest.get("measured") or latest.get("fetched") or ""
        src = e.get("source") or "WaterGuru pod"
        age = ago(raw)
        bits = [f"{src} {age}".strip()] if age else []
        extra = prov(*also)
        if extra:
            bits.append(extra)
        return " · ".join(bits)

    # 1. Calcium. Low calcium is the only item here that does permanent damage.
    if ch is not None and ch < TG["ch"][0]:
        need = TG["ch"][1] - ch
        out.append(dict(pri="do", title="Raise calcium hardness",
            detail=f"{ch:.0f} ppm against a target of {TG['ch'][0]:.0f}–{TG['ch'][2]:.0f}. "
                   f"Water short of calcium takes it from the plaster and the grout, and "
                   f"that damage does not reverse when the number is fixed.",
            dose=dose_line('calcium', need, 'ch', '77% calcium chloride'),
            src=prov("ch"),
            note="Dissolve in a bucket first — it gets hot. Re-test in a week."))

    # 2. pH. Costs sanitiser effectiveness every hour it sits high.
    if ph is not None and ph > TG["ph"][2]:
        need = ph - TG["ph"][1]
        floz = (None if gallons is None else
                CH.amount_for("acid", "ph", -need, gallons, acid_pct, ta_now))
        if floz is None:
            amt = ""
        else:
            floz = abs(floz)
            amt = f"{floz/32:.1f} qt" if floz >= 32 else f"{floz:.0f} fl oz"
        out.append(dict(pri="do", title="Bring pH down",
            src=prov_pod(),
            detail=f"{ph:.2f} against a target of {TG['ph'][1]:.1f}. At this pH only about "
                   f"{hocl_fraction(ph):.0f}% of the free chlorine is in the active form, "
                   f"against {hocl_fraction(7.4):.0f}% at 7.4 — the same chlorine, mostly "
                   f"switched off.",
            dose=f"{amt} of {acid_pct:g}% muriatic acid" if amt else "",
            note="Pour slowly over a return with the pump running, never into the skimmer."
                 + ("" if public else " Log it on the Chemicals tab.")))
    elif ph is not None and ph < TG["ph"][0]:
        out.append(dict(pri="watch", title="pH is below target",
            src=prov_pod(),
            detail=f"{ph:.2f} against {TG['ph'][0]:.1f}–{TG['ph'][2]:.1f}. Low pH with this "
                   f"pool's calcium makes the water aggressive to plaster.",
            dose="", note="Aerate rather than dose — the spillover raises pH on its own."))

    # 3. Free chlorine, judged against CYA rather than a fixed band.
    if fc is not None and fc < TG["free_cl"][0]:
        need = TG["free_cl"][1] - fc
        out.append(dict(pri="do", title="Free chlorine is low",
            src=prov_pod("cya"),
            detail=f"{fc:.1f} ppm against {TG['free_cl'][0]:.1f}–{TG['free_cl'][2]:.1f}, "
                   f"which is set by cyanuric acid at {cya:.0f} ppm." if cya else
                   f"{fc:.1f} ppm against {TG['free_cl'][0]:.1f}–{TG['free_cl'][2]:.1f}.",
            dose=dose_line('chlorine', need, 'free_cl', '12.5% liquid chlorine'),
            note="Or raise the cell output — the lasting fix is the dial, not the jug."))
    elif fc is not None and fc > TG["free_cl"][2]:
        st, _ = verdict(fc, TG["free_cl"], "free_cl")
        if st == "high":
            out.append(dict(pri="do", title="Free chlorine is too high",
            src=prov_pod("cya"),
                detail=f"{fc:.1f} ppm, far above the {TG['free_cl'][2]:.1f} this pool needs "
                       f"at cyanuric acid {cya:.0f}. At this level it bleaches swimwear and "
                       f"liners." if cya else f"{fc:.1f} ppm.",
                dose="", note="Turn the cell right down and let it fall before swimming."))
        else:
            detail = (f"{fc:.1f} ppm against a target ceiling of {TG['free_cl'][2]:.1f}. "
                      f"Nothing is going wrong — this is surplus, not excess, and the only "
                      f"cost is a cell working harder than it has to.")
            note = "Turn the cell down a step and re-read in two days."
            if traj and traj["equilibrium_adjusted"] < fc - 0.15:
                soon = next((v for d, v in traj["project"] if d == 4), None)
                inband = next((d for d, v in traj["project"]
                               if v <= TG["free_cl"][2]), None)
                detail += (f" It is already falling towards about "
                           f"<b>{traj['equilibrium_adjusted']:.1f} ppm</b> at the current "
                           f"runtime, losing {traj['k']*100:.0f}% of the gap each day"
                           + (f", so roughly {soon:.1f} in four days" if soon else "")
                           + (f" and inside the band within about {inband} days"
                              if inband else "") + ".")
                note = ("Wait rather than adjust again. A second change now lands on top of "
                        "one that has not finished, and then neither can be credited with "
                        "what happened — the usual result is overshooting the other way.")
                if traj.get("pump_ratio") and abs(traj["pump_ratio"] - 1) > 0.2:
                    note += (f" Note the pump has been running "
                             f"{traj['pump_recent_h']:.1f} h against an average of "
                             f"{traj['pump_avg_h']:.1f} h over these readings; runtime moves "
                             f"chlorine production more than the dial does, so settle that "
                             f"first.")
            out.append(dict(pri="watch", title="More chlorine than the pool needs",
            src=prov_pod("cya"),
                detail=detail, dose="", note=note))

    # 4. Alkalinity and salt, both slow and both cheap to correct.
    if ta is not None and ta < TG["ta"][0]:
        out.append(dict(pri="watch", title="Alkalinity is low",
            src=prov("ta"),
            detail=f"{ta:.0f} ppm against {TG['ta'][0]:.0f}–{TG['ta'][2]:.0f}. Low alkalinity "
                   f"does not slow the pH climb so much as make it erratic.",
            dose=dose_line('bicarb', TG['ta'][1] - ta, 'ta', 'sodium bicarbonate'),
            note=""))
    if salt is not None and salt < TG["salt"][0]:
        out.append(dict(pri="watch", title="Salt is low",
            src=prov("salt"),
            detail=f"{salt:.0f} ppm against {TG['salt'][0]:.0f}–{TG['salt'][2]:.0f}. Below "
                   f"range the cell throttles back or stops.",
            dose=dose_line('salt', TG['salt'][1] - salt, 'salt', 'pool salt'),
            note="Brush it off the floor and run the pump a day before re-testing."))

    # 5. WHAT THE POD IS COMPLAINING ABOUT, IN THIS PAGE'S OWN VOICE.
    #
    # The vendor's alert string was printed raw as the first thing on Home: 450
    # characters in which eight of ten clauses were the same complaint restated
    # per analyte ("Total Alkalinity measurement outdated", "Calcium Hardness
    # measurement outdated", …), two were things this page says far better
    # twenty lines below with the actual numbers, and exactly one — "Replace
    # Cassette soon" — was a dated, money-costing action with a consumable
    # behind it. That one never reached "What to do next" at all, so the only
    # item on the page with a part to buy was the one the action list ignored.
    #
    # Undifferentiated vendor text placed above the thing that replaces it is
    # the product arguing against itself. Parsed here: the consumable becomes an
    # action, the N "outdated" clauses collapse into one action naming them, and
    # a clause that merely restates a measure this page already judges is
    # dropped, because the tile beside it carries the number and the target.
    # The raw string still ships, below the actions, in the pod's own words.
    # ESCAPED AT THE BOUNDARY. `detail` is interpolated into the page WITHOUT
    # escaping, on purpose -- the author-written details carry <b> and friends --
    # so every clause of this string is untrusted text arriving at a sink that
    # trusts its input. It comes off a vendor API into readings.csv and then
    # straight onto the PUBLIC page: an alerts value of
    # `Replace cassette <img src=x onerror=...>` rendered as live markup, which
    # was demonstrated before this line existed. Escaping here rather than at
    # the sink keeps the deliberate markup in the hand-written details working.
    alert_clauses = [html.escape(a.strip()) for a in
                     str(latest.get("alerts") or "").split(";") if a.strip()]
    stale_measures, consumables, other_alerts = [], [], []
    # The measures this page states with a number, a target and a verdict.
    JUDGED = ("free chlorine", "ph", "total alkalinity", "alkalinity",
              "calcium hardness", "cyanuric acid", "salt", "saturation index",
              "phosphates", "copper", "iron")
    for a in alert_clauses:
        low = a.lower()
        if "outdated" in low:
            m = re.match(r"(.*?)\s+measurement outdated", a, re.I)
            stale_measures.append((m.group(1) if m else a).strip())
        elif "cassette" in low or "cartridge" in low or "replace" in low:
            consumables.append(a)
        elif any(low.startswith(j) for j in JUDGED):
            continue                    # the tiles say this with the number
        else:
            other_alerts.append(a)

    if consumables:
        out.append(dict(pri="do", title="The pod wants a new cassette",
            detail="The WaterGuru pod says: " + "; ".join(consumables) +
                   ". The cassette is what does the measuring, so when it runs "
                   "out the daily free-chlorine and pH readings on this page "
                   "stop — silently, looking exactly like a pool nobody tested.",
            dose="", src=prov_pod(),
            note="Order it before it expires; there is no local way to refill one."))

    if stale_measures:
        n = len(stale_measures)
        out.append(dict(pri="watch",
            title=f"{n} lab measure{'' if n == 1 else 's'} the pod calls out of date",
            detail=f"The pod reports {', '.join(stale_measures).lower()} as measured "
                   f"too long ago to rely on. These are the ones no daily sensor "
                   f"covers — they come from a posted sample or a carried-in one — "
                   f"so they go stale unless somebody sends water somewhere.",
            dose="", src=prov_pod(),
            note="Post a WaterGuru sample, or carry one to Leslie's; either updates "
                 "all of them at once."))

    for a in other_alerts:
        out.append(dict(pri="watch", title="The pod reports something else",
            detail=f"WaterGuru says: {a}. It is carried through rather than "
                   f"interpreted, because this page has no reading behind it.",
            dose="", src=prov_pod(), note=""))

    # THE DIAGNOSIS IS PUBLIC; THE REMEDY IS NOT.
    #
    # h["fix"] is operator guidance — "Check the pod has battery and signal, and
    # that ~/.waterguru is valid", "the address on Settings is right". It was
    # rendered into the PUBLIC build, where the reader has no Settings tab, no
    # pod and no shell. Six such instructions shipped on a cold install, and one
    # of them printed a credential FILE PATH into the build whose whole premise
    # is that token paths do not survive into it.
    #
    # The build's existing dead-link assertion only catches `data-tab-link=` in
    # markup, so prose naming a private tab in plain text sailed past it. The
    # leak list now carries the credential paths too, so the specific half
    # cannot recur silently.
    for h in health:
        if h["state"] in ("warn", "missing"):
            out.append(dict(pri="fix", title=f"{h['label']} {h['text']}",
                detail="Stale data looks exactly like current data on a dashboard, so this "
                       "is worth fixing before reading anything above as today's pool.",
                dose="", note="" if public else h["fix"]))

    if gallons is None:
        out.insert(0, dict(pri="fix", title="Nobody has said how big this pool is",
            detail="Every dose figure multiplies through the volume, so the items below "
                   "report what the water is doing but cannot say how much of anything to "
                   "add. This is the one number no sensor can supply.",
            dose="", src="",
            note=("Work it out on the Pool Volume Calculator tab."
                  if public else
                  "Set it in Settings, or work it out on the Pool Volume "
                  "Calculator tab.")))

    if not chems:
        out.append(dict(pri="watch", title="No chemical additions logged yet",
            detail="Every dose recorded is a change the model can attribute; every dose "
                   "missing is noise it has to absorb. This is the one input no sensor "
                   "can supply.",
            dose="", note=("" if public else
                           "Log one on the Chemicals tab, or with bin/chem.")))
    return out

def hocl_fraction(ph):
    """Percentage of free chlorine present as hypochlorous acid — the form that
    actually sanitises. The reason pH matters more than the chlorine number."""
    table = {7.0: 75, 7.2: 63, 7.4: 52, 7.5: 50, 7.6: 40, 7.8: 30, 8.0: 22}
    ks = sorted(table)
    p = min(max(ph, ks[0]), ks[-1])
    for a, b in zip(ks, ks[1:]):
        if a <= p <= b:
            return table[a] + (table[b] - table[a]) * (p - a) / (b - a)
    return table[ks[-1]]

# ---------------------------------------------------------------------- targets
def targets(lab, sanitiser=None, sanitiser_note=""):
    """Free-chlorine target is not a constant — it is a fraction of cyanuric
    acid. CYA absorbs UV for the chlorine, so a pool at CYA 80 needs roughly
    twice the FC of one at CYA 40 to hold the same sanitising power. The
    industry rule for a salt pool is 5% of CYA as the floor and 7.5% as the
    aim. Printing a flat '1-3 ppm' band would call this pool's 6.2 ppm wildly
    high when what matters is 6.2 against a CYA-38 target of 2.9."""
    cya = None
    if lab:
        c = lab.get("cya")
        cya = c["value"] if isinstance(c, dict) else num(c)
    if cya:
        fc = (round(cya * 0.05, 1), round(cya * 0.075, 1), round(cya * 0.10, 1))
    else:
        fc = (2.0, 3.0, 4.0)
    salt_pool = (sanitiser or "salt_cell") == "salt_cell"

    # Alkalinity is where the generic chart is actively wrong for this pool. A
    # chlorine pool wants TA 80-120; a salt pool wants it LOWER — around 60-90 —
    # because the cell is a permanent upward push on pH and TA is the reservoir
    # that feeds the rebound. Judging a salt pool against the chlorine-pool band
    # calls a correctly-run pool "low" and invites someone to add bicarbonate,
    # which is the opposite of what it needs.
    ta = (60, 75, 90) if salt_pool else (80, 100, 120)

    # Calcium likewise. The usual 200-400 is fine for a pool sitting at pH 7.6+;
    # a salt pool held down near 7.5 for chlorine effectiveness needs more
    # calcium to keep the saturation index out of the aggressive zone.
    ch = (250, 350, 450) if salt_pool else (200, 300, 400)

    # If the lab states its own salt range — Leslie's returns e.g. "Salt
    # 3000-4000" alongside the results — believe the instrument that produced
    # the number rather than a remembered figure.
    salt = (3000, 3200, 3600)
    m = re.search(r"(\d{3,5})\s*[-–]\s*(\d{3,5})", sanitiser_note or "")
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        salt = (lo, (lo + hi) / 2, hi)

    return {
        "free_cl": fc,
        "ph":      (7.4, 7.5, 7.6),
        "salt":    salt,
        "ta":      ta,
        "cya":     (30, 50, 80),
        "ch":      ch,
    }

# Above the target band, some measures are a problem and some are merely more
# than the pool needs. Where that distinction exists, this is where it lives:
# the value past which it stops being surplus and starts being a fault.
TOO_MUCH = {
    # Free chlorine is capped by the target at 10% of cyanuric acid, which is
    # what the pool NEEDS. The safety bound is much higher — around 40% of CYA —
    # but calling 12 ppm "surplus" stretches the word past usefulness, so the
    # line is drawn at twice the target ceiling. Between the two the only cost is
    # a cell working harder than it has to.
    "free_cl": lambda band: band[2] * 2.0,
    "salt":    lambda band: band[2] * 1.15,
    "ta":      lambda band: band[2] * 1.4,
    "ch":      lambda band: band[2] * 1.5,
    "cya":     lambda band: band[2] * 1.6,
}

def verdict(v, band, measure=None):
    """Four states, because 'above the band' is not one thing.

    Calling free chlorine at 4.6 ppm "high" next to calcium that is genuinely
    eating the plaster puts two different situations in the same voice, and the
    reader learns to discount both. WaterGuru's own range for this pool is
    1.6-5.4 and it had already stopped alerting, while this page was still
    saying high — which is the kind of disagreement that makes someone stop
    trusting the page rather than the number.

    'over' means more than the pool needs and nothing is going wrong. 'high'
    means act.
    """
    if v is None or not band: return "unknown", ""
    lo, _, hi = band
    if v < lo: return "low", "↓"
    if v > hi:
        ceiling = TOO_MUCH.get(measure)
        if ceiling and v <= ceiling(band):
            return "over", "↑"
        return "high", "↑"
    return "ok", ""

# ------------------------------------------------------------------ day rollup
SEG_MAX = dt.timedelta(minutes=20)   # a sample covers at most this much time

def by_day(samples, chems, readings):
    """Fold the 15-minute sample stream into one record per calendar day.

    Runtime is accumulated from the interval each sample REPRESENTS, capped at
    SEG_MAX: the sampler fires every 15 minutes, so a sample stands for the
    quarter-hour after it, but if the Mac was asleep or the Pi unreachable the
    next sample may be hours later and must not be counted as hours of pump
    runtime. Capping turns an outage into a visible gap instead of a fiction."""
    days = {}

    def slot(d):
        return days.setdefault(d, {
            # heat: gas heating is the most expensive thing this pool can do,
            # the notifier mails about every one of its edges, and there was
            # nowhere in the product to look up when it ran or for how long.
            "date": d, "segs": [],
            "hours": {"pump": 0.0, "spa": 0.0, "sheer": 0.0, "heat": 0.0},
            "swg": [], "temp": [], "salt": [], "spa_temp": [],
            "chem": [], "reading": None,
        })

    S = sorted([s for s in samples if when(s.get("ts"))], key=lambda s: when(s["ts"]))
    for i, s in enumerate(S):
        t = when(s["ts"])
        nxt = when(S[i + 1]["ts"]) if i + 1 < len(S) else None
        span = min(nxt - t, SEG_MAX) if nxt else SEG_MAX
        if span.total_seconds() <= 0: continue
        d = slot(t.date().isoformat())

        for key, col in (("pump", "pump"), ("spa", "spa"), ("sheer", "sheer"),
                         ("heat", "pool_heat")):
            if s.get(col) == "1":
                d["hours"][key] += span.total_seconds() / 3600.0
                # Store as fractional hours-of-day so the strip can place it.
                start = t.hour + t.minute / 60 + t.second / 3600
                d["segs"].append({"k": key, "a": start,
                                  "b": min(24.0, start + span.total_seconds() / 3600.0),
                                  "t": t.strftime("%H:%M")})
        for key, col in (("swg", "swg_pct"), ("temp", "pool_temp"),
                         ("salt", "salt_ppm"), ("spa_temp", "spa_temp")):
            v = num(s.get(col))
            if v is not None: d[key].append(v)

    for c in chems:
        t = when(c.get("ts"))
        if not t: continue
        d = slot(t.date().isoformat())
        d["chem"].append({"at": t.hour + t.minute / 60, "time": t.strftime("%H:%M"),
                          "what": c.get("chemical", ""), "amt": c.get("amount", ""),
                          "unit": c.get("unit", ""), "pct": c.get("pct", ""),
                          "note": c.get("note", "")})

    for r in readings:
        t = when(r.get("measured"))
        if not t: continue
        slot(t.date().isoformat())["reading"] = r

    for d in days.values():
        d["segs"].sort(key=lambda s: s["a"])
        for k in ("swg", "temp", "salt", "spa_temp"):
            d[k] = round(sum(d[k]) / len(d[k]), 1) if d[k] else None
    return [days[k] for k in sorted(days, reverse=True)]

def power_block(e, rated, cost, traj=None, fc_target=None):
    """The Power and runtime card.

    Says what it knows and, when it knows nothing, says why and what would fix
    it -- rather than rendering an empty chart or a zero. The advice at the end
    reuses the free-chlorine fit that already exists instead of introducing a
    second model of the same pool: the salt cell only makes chlorine while the
    pump runs, so production scales with runtime and the equilibrium the fit
    reports is the equilibrium AT THE RUNTIME IT OBSERVED. That assumption is
    stated on the page, because it is the whole basis of the suggestion.
    """
    hrs = e["hours_per_day"]
    out = []

    if not e["any"]:
        hrs = e["hours_per_day_window"]
        why = ("the pump has never reported its wattage to the controller"
               if not e["ever_reported_watts"] else
               "no wattage has been reported recently")
        out.append(
            '<div class="card"><p class="empty" style="margin:0">'
            + (f'The pump ran <b>{hrs:.1f} h a day</b> on average over these days. '
               if hrs else 'No pump runtime has been recorded yet. ')
            + f'There is no energy figure because {why}, and no pump rating has '
              'been entered. Put the rating from the pump\'s label into Settings '
              'and the hours above become kilowatt-hours \u2014 an estimate, and '
              'labelled as one.</p></div>')
        return "".join(out)

    lab = {"measured": "measured at the pump",
           "rated": "estimated from the rating you entered, not measured",
           "mixed": "part measured, part estimated from the rating"}[e["basis"]]
    kpd, ctot = e["kwh_per_day"], e["cost_total"]

    # The same tile object the water measures use — .tl/.tv/.tu, which is what
    # the stylesheet actually defines. The first spelling of this invented
    # .k/.v classes that exist nowhere, so the label and the value ran together
    # as "Pump runtime5.8 h" with no type hierarchy at all.
    def tile(label, value, unit, cls=""):
        return (f'<div class="tile{cls}"><div class="tl">{label}</div>'
                f'<div class="tv">{value}<span class="tu">{unit}</span></div></div>')

    tiles = [tile("Pump runtime", f"{hrs:.1f}", "h/day"),
             tile("Energy", f"{kpd:.1f}", "kWh/day")]
    if cost:
        # class="priv": runtime and kilowatt-hours are facts about the pool's
        # equipment, but what it COSTS is a fact about the household's bill, and
        # the public/private split in this product is drawn on exactly that line
        # — is this about the POOL or about the HOUSE. The build strips it from
        # index.html by class, along with the money in the advice below.
        tiles.append(tile("Cost", f"{kpd * cost:,.2f}", "/day", cls=" priv"))
    # Say what the figures cover. Without this the card averaged over the days
    # it had watts for and presented the result as though it described the whole
    # window -- and on this install the pump only started reporting wattage
    # recently, so every window for weeks is the partial case.
    kd, wd = e.get("known_days") or 0, e.get("window_days") or 0
    covers = (f'Both figures cover the <b>{kd} of {wd} days</b> the pump reported '
              f'its wattage, so they can be read against each other. '
              if kd and wd and kd != wd else
              f'Both figures cover all {wd} days shown. ' if wd else "")
    out.append('<div class="card"><div class="tiles">' + "".join(tiles) + "</div>"
               f'<p class="sub" style="margin:10px 0 0">Energy is {lab}. {covers}'
               f'Runtime is measured from the samples, with any gap in sampling '
               f'left as a gap rather than counted as running.</p></div>')

    # What the runtime could be, from the model that already exists.
    if traj and fc_target and traj.get("equilibrium_adjusted") and traj.get("pump_recent_h"):
        eq, now_h = traj["equilibrium_adjusted"], traj["pump_recent_h"]
        if eq > 0 and now_h > 0 and hrs:
            # TWO RUNTIMES, AND THE SAVING IS MEASURED AGAINST THE ONE ON SCREEN.
            #
            # `hrs` is the tile's figure: the mean over the days this card
            # covers, from by_day(), which caps each sample's interval so an
            # outage reads as a gap. `now_h` is the trajectory's: ONE day, the
            # day of the last reading, counted as a flat 0.25 h per sample with
            # the pump on. They are different quantities and the card printed
            # both, six lines apart, as though they were the same one — 5.1 h/day
            # in the tile and "the 6.50 h this pool has been running" below it.
            #
            # Worse, the saving was 6.50 - want_h: a difference against a SINGLE
            # DAY, presented as a per-day saving and priced as one, which
            # overstated the headline benefit of the section about six-fold. The
            # runtime that would hold the target is a schedule and stands on its
            # own; what changing to it SAVES has to be measured from the runtime
            # this pool actually averages, which is the number beside it.
            want_h = now_h * (fc_target / eq)
            delta = hrs - want_h
            saved = (e["kwh_per_day"] / hrs * delta)
            # Named, so the two never read as one number contradicting itself.
            basis = (f'The fit is built on the <b>{now_h:.2f} h</b> the pump ran on '
                     f'the most recent day with a reading; the saving above is '
                     f'measured from the <b>{hrs:.1f} h a day</b> it has averaged '
                     f'over the days this card covers. '
                     if abs(now_h - hrs) > 0.05 else "")
            if delta > 0.25:
                # Wrapped whole, em dash included, so stripping it from the
                # public build leaves a complete sentence rather than a dangling
                # "— about a day".
                money = (f'<span class="priv"> \u2014 about '
                         f'<b>{saved * cost:,.2f} a day</b></span>' if cost else "")
                out.append(
                    f'<div class="card" style="margin-top:12px"><p style="margin:0">'
                    f'<b>There is runtime to give back.</b> Free chlorine is settling '
                    f'towards <b>{eq:.1f} ppm</b> at the runtime this pool has been '
                    f'keeping, against a target of <b>{fc_target:.1f}</b>. The '
                    f'cell only makes chlorine while the pump runs, so if production '
                    f'scales with runtime, about <b>{want_h:.2f} h</b> would hold the '
                    f'target \u2014 roughly <b>{delta:.2f} h a day</b> less than the '
                    f'<b>{hrs:.1f} h</b> above, <b>{saved:.1f} kWh</b>{money}.</p>'
                    f'<p class="sub" style="margin:8px 0 0">{basis}Change one thing at a time '
                    f'and give it a week: the pool takes days to settle, and a second '
                    f'change landing on an unfinished one leaves neither creditable. '
                    f'Filtration and skimming also need runtime, and this figure knows '
                    f'only about chlorine.</p></div>')
            elif delta < -0.25:
                out.append(
                    f'<div class="card" style="margin-top:12px"><p style="margin:0">'
                    f'<b>Runtime is short for the chlorine this pool needs.</b> Free '
                    f'chlorine settles towards <b>{eq:.1f} ppm</b> at the runtime this '
                    f'pool has been keeping, below the <b>{fc_target:.1f}</b> target. On '
                    f'the same assumption about <b>{want_h:.2f} h</b> would hold it '
                    f'\u2014 <b>{-delta:.2f} h a day</b> more than the <b>{hrs:.1f} h</b> '
                    f'above \u2014 or raise the cell output instead, which costs less '
                    f'than the extra pumping.</p>'
                    + (f'<p class="sub" style="margin:8px 0 0">{basis}</p>' if basis else "")
                    + '</div>')

    return "".join(out)

def energy(days, samples, rated=None, cost=None):
    """What the pump cost to run, per day, and what that figure rests on.

    THE FIGURE HAS TO NAME ITS BASIS

    There are two very different numbers here and conflating them would be the
    same defect as quoting a dose against a volume nobody set. A MEASURED figure
    multiplies real runtime by watts the panel actually reported. An ESTIMATED
    one multiplies real runtime by a rating somebody typed off the pump's label,
    which is its draw at full speed and is wrong by however much a variable
    speed pump is throttled. Both are useful; presenting the second as the first
    is not.

    So every day carries its own basis, and a day with no watts and no rating
    gets hours and no energy at all rather than a plausible-looking zero.

    Runtime comes from by_day(), which caps each sample's interval so an outage
    reads as a gap instead of hours the pump did not run.
    """
    # Mean draw while the pump was actually running, per day. Samples taken with
    # the pump off are excluded rather than averaged in: they are real readings
    # of a stopped pump and would drag the mean toward zero, understating the
    # energy of the hours it did run.
    watts = {}
    for smp in samples:
        t = when(smp.get("ts"))
        if not t or str(smp.get("pump", "")).strip() not in ("1", "1.0"):
            continue
        w = num(smp.get("pump_watts"))
        if w is None or w <= 0:
            continue
        watts.setdefault(t.date().isoformat(), []).append(w)

    out, total_kwh, measured_days, rated_days = [], 0.0, 0, 0
    for d in days:
        hrs = d["hours"]["pump"]
        ws = watts.get(d["date"]) or []
        if ws:
            mean_w, basis = sum(ws) / len(ws), "measured"
            # A day the pump never ran contributes no energy whichever number it
            # would have used, so it must not colour the basis: one idle day
            # falling through to the rating made a fully measured week report
            # itself as "part estimated".
            if hrs > 0: measured_days += 1
        elif rated:
            mean_w, basis = float(rated), "rated"
            if hrs > 0: rated_days += 1
        else:
            out.append({"date": d["date"], "hours": hrs, "kwh": None,
                        "watts": None, "basis": None, "cost": None, "n": 0})
            continue
        kwh = mean_w / 1000.0 * hrs
        total_kwh += kwh
        out.append({"date": d["date"], "hours": hrs, "kwh": kwh, "watts": mean_w,
                    "basis": basis, "n": len(ws),
                    "cost": (kwh * cost) if cost else None})

    known = [e for e in out if e["kwh"] is not None]
    # THE TWO TILES MUST SHARE A DENOMINATOR.
    #
    # kwh_per_day averaged over the days that HAVE a figure; hours_per_day
    # averaged over EVERY day in the window. Printed side by side with the same
    # "/day" unit, over a window where the pump only recently began reporting
    # watts, that is two answers to two different questions wearing one label:
    # with five of six days unmeasured the card asserted a pool pump averaging
    # 55 W and called it "measured at the pump". The savings advice divides one
    # by the other, so the money figure inherited the mismatch.
    #
    # A day with no energy figure is UNKNOWN, not zero, so it cannot simply join
    # the average. Both figures are therefore taken over the days that have one,
    # and the card says how many days that is. The full-window runtime is kept
    # separately for the no-energy case, which is the only honest use for it.
    hrs_known = ([e["hours"] for e in known] or None)
    return {
        "days": out,
        "any": bool(known),
        "known_days": len(known),
        "window_days": len(out),
        # "measured" only when every day that has a figure got it from a
        # reading. One rated day in the set makes the total an estimate, and the
        # page says so rather than averaging the two kinds of number silently.
        "basis": ("measured" if measured_days and not rated_days else
                  "rated" if rated_days and not measured_days else
                  "mixed" if measured_days and rated_days else None),
        "measured_days": measured_days, "rated_days": rated_days,
        "kwh_total": total_kwh if known else None,
        "kwh_per_day": (total_kwh / len(known)) if known else None,
        "cost_total": (total_kwh * cost) if (known and cost) else None,
        # Over the SAME days the energy figure covers, so kwh/hrs is a real
        # mean draw and the two tiles can be read against each other.
        "hours_per_day": (sum(hrs_known) / len(hrs_known)) if hrs_known else
                         ((sum(e["hours"] for e in out) / len(out)) if out else None),
        # Over the whole window. Only for the case where there is no energy
        # figure at all and runtime is the only thing that can be said.
        "hours_per_day_window": (sum(e["hours"] for e in out) / len(out)) if out else None,
        "ever_reported_watts": bool(watts),
    }

# ----------------------------------------------------------------------- charts
_SVG_SEQ = [0]

def name_svg(title, desc):
    """The two elements that give one `svg role="img"` a name, and the tie to them.

    A CHART WAS ANNOUNCED AS ITS OWN AXIS TICKS. With no aria-labelledby the
    accessible name of an svg falls back to its text content, which for these
    charts is every tick, every point tooltip and every last-value label run
    together: measured in a browser, the free-chlorine trend computed a name
    beginning "target 2.90.84.17.3" and continuing through both labs' dated
    readings, and the thirty day strips all computed the SAME 468-character
    string of "Pump — 08:00Pump — 08:15…" with nothing in any of them naming
    its date. Of the twenty charts on the public page, the three named ones
    were the Help diagrams — which already do this correctly — so the pattern
    did not have to be invented, only applied.

    `<desc>` is the sentence a reader gets instead of the picture, so it states
    the SHAPE: how many readings, over what span, between what values. Derived
    from the series each chart was handed rather than written beside it, because
    a hand-written "rising steadily" is a claim that goes stale the next time
    cron renders.

    Returns (attrs, elements). The elements must be the svg's FIRST children:
    an `<svg><title>` that is not first is still announced by some browsers as a
    tooltip for whatever precedes it.
    """
    _SVG_SEQ[0] += 1
    n = _SVG_SEQ[0]
    t, d = f"cx{n}t", f"cx{n}d"
    return (f' aria-labelledby="{t} {d}"',
            f'<title id="{t}">{html.escape(title)}</title>'
            f'<desc id="{d}">{html.escape(desc)}</desc>')


def _span_words(pts):
    """How many readings and over what span, in words — with the honest singular.

    Said once here rather than in each of the three chart builders, so a strip
    and a trend cannot describe the same history two different ways.
    """
    if not pts:
        return "no readings"
    n = len(pts)
    a, b = pts[0][0], pts[-1][0]
    if n == 1 or a.date() == b.date():
        return f"one reading on {a.strftime('%-d %b')}" if n == 1 else \
               f"{n} readings on {a.strftime('%-d %b')}"
    return f"{n} readings from {a.strftime('%-d %b')} to {b.strftime('%-d %b')}"


# The mark each secondary source is drawn with, in order. SHAPE, NOT HUE — the
# reasoning is below where they are drawn, and it is why adding a third source
# here does not touch the palette. Each is a closed path centred on (0,0),
# scaled by r, so one loop draws any of them.
_REF_MARKS = {
    # Leslie's: the hollow diamond this chart has always used.
    "s-les": "M0,-{r} L{r},0 L0,{r} L-{r},0 Z",
    # By hand: a hollow square. Distinguishable from the diamond at 12px even
    # when the two sit on the same pixel, which they do whenever somebody
    # carries a sample in and tests it at home the same day.
    "s-man": "M-{a},-{a} L{a},-{a} L{a},{a} L-{a},{a} Z",
}


# The windows the trend charts offer, newest-first, and how far back each
# reaches. `None` is everything there is.
#
# NO "DAY". The pod reports once a day, so a one-day window is a chart with a
# single point on it and no line — a control that produces an empty frame
# teaches a reader the control is broken. The shortest window that draws a
# shape is a week.
CHART_RANGES = [("week", "Week", dt.timedelta(days=7)),
                ("month", "Month", dt.timedelta(days=31)),
                ("year", "Year", dt.timedelta(days=365)),
                ("all", "All", None)]


def range_bar(default="all", label="Time range"):
    """The control itself, once per section rather than once per chart.

    The two trend charts are, in line_chart()'s own words, "two charts, same x,
    stacked" — so they must share a window. A per-chart control let somebody
    put chlorine on a week and pH on a year, which silently breaks the one
    property that makes reading them together mean anything.
    """
    return (f'<div class="rangebar" role="group" aria-label="{html.escape(label)}">'
            + "".join(
                f'<button type="button" class="rbtn" data-range="{k}" '
                f'aria-pressed="{"true" if k == default else "false"}">'
                f'{html.escape(t)}</button>' for k, t, _ in CHART_RANGES)
            + "</div>")


def ranged_chart(build, default="all"):
    """The same chart at several spans. See range_bar() for the control.

    RENDERED ONCE PER SPAN, SERVER-SIDE, rather than redrawn in the browser.
    line_chart() computes its own scale, ticks, target band and accessible
    description from whatever points it is handed, so a narrower window is a
    filtered call and nothing else — every variant is as correct as the one
    that was there before, including its <desc>, which a client-side rescale
    would have had to reproduce and keep in step.

    The cost is a few kilobytes of duplicated SVG. The alternative was putting
    the series into the page as data and writing a drawing engine in the page
    script, which is a great deal of machinery to own for a control that picks
    between four numbers.

    `build(since)` renders the chart for one window; `since` is a datetime or
    None. Empty windows are dropped rather than offered, so the control never
    presents a span this pool has no readings in.
    """
    now = dt.datetime.now()
    out = []
    for key, text, delta in CHART_RANGES:
        svg = build(now - delta if delta else None)
        # A chart with nothing in it says "No readings collected yet", which is
        # true of a chart and false of a WINDOW: there are readings, just not
        # these. Detected here rather than left to each caller, because the
        # caller that forgets produces a page telling somebody their pool has
        # never been measured.
        if svg is not None and 'class="empty"' in svg:
            svg = None
        if svg is None:
            # EVERY WINDOW IS OFFERED, even an empty one, and it says which
            # window it is. Dropping the button instead would leave the two
            # stacked charts offering different spans, and "No readings
            # collected yet" — what the chart says when it has nothing — is
            # false here: there are readings, just not in the last week.
            svg = (f'<p class="empty">No readings in the last '
                   f'{html.escape(text.lower())}.</p>')
        out.append(f'<div class="rv" data-range="{key}"'
                   f'{"" if key == default else " hidden"}>{svg}</div>')
    return f'<div class="ranged" data-default="{default}">' + "".join(out) + "</div>"


def line_chart(series, band, fmt="{:.1f}", unit="", refs=None, ref_label="",
               title="Trend", extra=()):
    """One measure, one axis. Never two scales in one frame — a pH line and an
    FC line share nothing but a clock, and drawing them together invents
    crossings that mean nothing. Two charts, same x, stacked.

    `extra` is any number of further sources, each `(series, label, class)`.
    It used to be exactly one, named `refs`, which is why by-hand results —
    accepted by the entry form, validated, stored and audited — appeared on the
    lab-history charts and on no trend chart at all. Somebody with a test kit
    and no pod could enter a chlorine reading and watch it not be drawn.

    Every drawn element carries its source class, so the page can show and hide
    a source without redrawing anything.
    """
    sources = list(extra)
    if refs:
        sources.insert(0, (refs, ref_label or "second lab", "s-les"))
    W, H = 720, 168
    L, R, T, B = 44, 14, 14, 26
    pts  = [(when(m), v) for m, v in series if when(m) and v is not None]
    # Each secondary source, cleaned the same way and kept apart.
    secs = [(sorted((when(m), v) for m, v in (ser or [])
                    if when(m) and v is not None), lbl, cls)
            for ser, lbl, cls in sources]
    secs = [(p_, lbl, cls) for p_, lbl, cls in secs if p_]
    rpts = [pt for p_, _, _ in secs for pt in p_]
    # The raw measured string each point came from, so a dot can find its OWN
    # row in the tables below rather than opening a dialog that repeats the
    # tooltip and then tells the reader to go and look it up. Kept beside pts
    # rather than inside it: the tuples are unpacked as pairs in six places.
    keys = {when(m): str(m) for m, v in series if when(m) and v is not None}
    if not pts and not rpts:
        return '<p class="empty">No readings collected yet.</p>'
    pts.sort(); rpts.sort()

    ys = [v for _, v in pts] + [v for _, v in rpts] + [band[0], band[2]]
    lo, hi = min(ys), max(ys)
    pad = (hi - lo) * 0.25 or max(abs(hi) * 0.05, 0.5)
    lo, hi = lo - pad, hi + pad

    # The x-range must span BOTH sources, or the photometer points — which are
    # usually older than the pod's — would be drawn outside the frame.
    alltimes = [d.timestamp() for d, _ in pts + rpts]
    t0, t1 = min(alltimes), max(alltimes)
    if t1 - t0 < 3600:
        t0, t1 = t0 - 43200, t1 + 43200

    def X(t): return L + (t.timestamp() - t0) / max(t1 - t0, 1) * (W - L - R)
    def Y(v): return T + (hi - v) / max(hi - lo, 1e-9) * (H - T - B)

    desc = (f"Line chart. {_span_words(pts)}, "
            f"between {fmt.format(min(v for _, v in pts))}{unit} and "
            f"{fmt.format(max(v for _, v in pts))}{unit}, "
            f"against a target band of {fmt.format(band[0])}{unit} to "
            f"{fmt.format(band[2])}{unit}."
            if pts else
            f"Line chart with no readings from the pod; "
            f"the target band is {fmt.format(band[0])}{unit} to {fmt.format(band[2])}{unit}.")
    for p_, lbl, cls in secs:
        shape = "squares" if cls == "s-man" else "diamonds"
        desc += (f" {len(p_)} {lbl} result{'s' if len(p_) != 1 else ''} "
                 f"{'are' if len(p_) != 1 else 'is'} drawn as hollow {shape} on the "
                 f"same axis, never joined to the line.")
    if pts:
        desc += f" The latest is {fmt.format(pts[-1][1])}{unit} on {pts[-1][0].strftime('%-d %b')}."
    lab, names = name_svg(title, desc)
    o = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img"{lab}>', names]
    # Target band first, so data always draws on top of context.
    o.append(f'<rect x="{L}" y="{Y(band[2]):.1f}" width="{W-L-R}" '
             f'height="{max(Y(band[0])-Y(band[2]),1):.1f}" class="band"/>')
    o.append(f'<line x1="{L}" y1="{Y(band[1]):.1f}" x2="{W-R}" y2="{Y(band[1]):.1f}" class="aim"/>')
    o.append(f'<text x="{W-R-2}" y="{Y(band[1])-5:.1f}" class="aim-lbl" text-anchor="end">'
             f'target {fmt.format(band[1])}</text>')

    for v in (lo + (hi - lo) * f for f in (0, 0.5, 1)):
        o.append(f'<text x="{L-8}" y="{Y(v)+4:.1f}" class="ytick" text-anchor="end">{fmt.format(v)}</text>')

    # Every other source, same measure, same axis. Distinguished by MARK SHAPE
    # rather than hue: hollow marks against the pod's filled circle. Colour is
    # already carrying equipment identity elsewhere on this page, and reusing
    # the spa orange here to mean "Leslie's" would make one hue mean two things.
    # That reasoning is why a THIRD source costs a shape and not a colour.
    for p_, lbl, cls in secs:
        tmpl = _REF_MARKS.get(cls, _REF_MARKS["s-les"])
        for t_, v in p_:
            x, y = X(t_), Y(v)
            d = tmpl.format(r=6, a=4.6)
            o.append(f'<path transform="translate({x:.1f},{y:.1f})" d="{d}" '
                     f'class="refmark {cls}">'
                     f'<title>{html.escape(lbl)} {html.escape(t_.strftime("%-d %b"))} '
                     f'— {html.escape(fmt.format(v))}{html.escape(unit)}</title></path>')

    if len(pts) > 1:
        d = " ".join(f'{"M" if i == 0 else "L"}{X(t):.1f},{Y(v):.1f}' for i, (t, v) in enumerate(pts))
        o.append(f'<path d="{d}" class="line s-pod"/>')
    for t, v in pts:
        o.append(f'<circle cx="{X(t):.1f}" cy="{Y(v):.1f}" r="5" class="dot s-pod" '
                 f'data-x="{X(t):.1f}" data-y="{Y(v):.1f}" '
                 f'data-key="{html.escape(keys.get(t, ""))}" '
                 f'data-label="{html.escape(t.strftime("%a %-d %b %H:%M"))}" '
                 f'data-value="{html.escape(fmt.format(v) + unit)}"><title>'
                 f'{html.escape(t.strftime("%a %-d %b %H:%M"))} — {html.escape(fmt.format(v))}{html.escape(unit)}'
                 f'</title></circle>')

    if not pts:
        o.append("</svg>"); return "".join(o)
    # The newest point is the one being acted on, so it gets the only label.
    lt, lv = pts[-1]
    anchor = "end" if X(lt) > W - 90 else "start"
    dx = -10 if anchor == "end" else 10
    o.append(f'<text x="{X(lt)+dx:.1f}" y="{Y(lv)-10:.1f}" class="lastlbl" '
             f'text-anchor="{anchor}">{fmt.format(lv)}{unit}</text>')
    span = sorted(pts + rpts)
    o.append(f'<text x="{L}" y="{H-6}" class="xtick">{span[0][0].strftime("%-d %b")}</text>')
    if len(span) > 1:
        o.append(f'<text x="{W-R}" y="{H-6}" class="xtick" text-anchor="end">{span[-1][0].strftime("%-d %b")}</text>')
    o.append("</svg>")
    return "".join(o)

# Fixed order and fixed style per lab, so a source keeps its identity across
# every panel and never changes appearance because a filter changed the mix.
# WHICH WATERGURU. The vendor sells two instruments and this product reads
# both: the SENSE pod floating in the water, which reports pH and free chlorine
# daily into readings.csv, and the mailed-in sample it analyses in a lab, which
# reports the eight-measure panel into lab.csv. They share a brand and nothing
# else — no measure appears in both files.
#
# The label here was "WaterGuru", and it meant the lab. The Trend charts above
# call the other one "WaterGuru pod, daily". So the page named one vendor two
# ways and left a reader to work out that the WaterGuru on the alkalinity chart
# and the WaterGuru on the chlorine chart were different machines sampling on
# different schedules — which is exactly the distinction those charts exist to
# make visible.
SOURCE_ORDER = ["WaterGuru lab", "Leslie's", "By hand"]
SOURCE_STYLE = {"WaterGuru lab": "s-wg", "Leslie's": "s-les", "By hand": "s-man"}

def lab_chart(measure, points, band, fmt="{:.0f}"):
    """One measure's lab history, small.

    These are small multiples rather than five series on one axis: alkalinity
    near 80, calcium near 250 and salt near 3500 share no scale, and forcing them
    together would either flatten four of them into a line at the bottom or need
    a second axis — which is never the answer.

    Each panel therefore gets its own y-range, and the target band is drawn
    behind the data so the shape of the series is read against where it should
    be rather than against an arbitrary zero.
    """
    W, H, L, R, T, B = 300, 126, 40, 12, 12, 22
    pts = [(when(p["d"]), p["v"], p["src"]) for p in points if when(p["d"])]
    # As in line_chart: the raw measured string, so a point can find its row.
    keys = {when(p["d"]): str(p["d"]) for p in points if when(p["d"])}
    if not pts:
        return (f'<div class="lab-mini"><h4>{html.escape(MEASURE_NAMES.get(measure, measure))}</h4>'
                f'<p class="empty">no readings</p></div>')
    pts.sort()

    ys = [v for _, v, _ in pts] + ([band[0], band[2]] if band else [])
    lo, hi = min(ys), max(ys)
    pad = (hi - lo) * 0.2 or max(abs(hi) * 0.06, 0.5)
    lo, hi = lo - pad, hi + pad
    # A concentration cannot be negative. Padding the range below zero draws an
    # axis label like "-44 ppb", which is not a quantity that exists and makes
    # the reader distrust the panel. The saturation index is the one measure here
    # that genuinely goes negative.
    if measure != "saturation_index":
        lo = max(lo, 0.0)

    t0 = pts[0][0].timestamp(); t1 = pts[-1][0].timestamp()
    if t1 - t0 < 3600: t0, t1 = t0 - 86400, t1 + 86400

    def X(d): return L + (d.timestamp() - t0) / (t1 - t0) * (W - L - R)
    def Y(v): return T + (hi - v) / max(hi - lo, 1e-9) * (H - T - B)

    # The six lab-history panels announced as bare number strings — "5496" then
    # every dated tooltip — which never said WHICH measure they were. The <h4>
    # beside each one carries the name; nothing tied it to the picture. It is
    # repeated into the title here rather than pointed at with aria-labelledby,
    # because the heading is a sibling of the svg and outside it, and a name
    # that depends on an id defined elsewhere in the document is the tie that
    # breaks when a panel is moved.
    mname = MEASURE_NAMES.get(measure, measure)
    srcs = [s for s in SOURCE_ORDER if any(x[2] == s for x in pts)]
    ldesc = (f"Small line chart of {mname.lower()}. {_span_words(pts)}, "
             f"between {fmt.format(min(v for _, v, _ in pts))} and "
             f"{fmt.format(max(v for _, v, _ in pts))}.")
    if band:
        ldesc += f" The target band is {fmt.format(band[0])} to {fmt.format(band[2])}."
    ldesc += (f" One line per source ({', '.join(srcs)}), never one line through both, "
              f"so where they sit apart that gap is the labs disagreeing."
              if len(srcs) > 1 else f" All of it from {srcs[0]}." if srcs else "")
    lab, names = name_svg(f"{mname} — lab history", ldesc)
    o = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img"{lab}>', names]
    if band:
        o.append(f'<rect x="{L}" y="{Y(band[2]):.1f}" width="{W-L-R}" '
                 f'height="{max(Y(band[0])-Y(band[2]),1):.1f}" class="band"/>')
    for v in (lo, hi):
        o.append(f'<text x="{L-6}" y="{Y(v)+3.5:.1f}" class="ytick" text-anchor="end">{fmt.format(v)}</text>')

    # ONE LINE PER LAB, never a line through both.
    #
    # A single line joining every point implies one instrument watching the pool
    # move. It is two instruments sampling on different days, so a step between
    # consecutive points from different labs may be the pool changing or may be
    # the labs disagreeing — and drawing it as one continuous series silently
    # asserts the first. Each lab therefore gets its own line; where the lines
    # sit apart at the same moment, that separation is the disagreement, drawn
    # rather than hidden.
    for src in SOURCE_ORDER:
        sp = [(a, b) for a, b, s in pts if s == src]
        if not sp: continue
        cls = SOURCE_STYLE[src]
        if len(sp) > 1:
            d = " ".join(f'{"M" if i == 0 else "L"}{X(a):.1f},{Y(b):.1f}'
                         for i, (a, b) in enumerate(sp))
            o.append(f'<path d="{d}" class="labline {cls}"/>')
        for a, b in sp:
            x, y = X(a), Y(b)
            tip = (f'<title>{html.escape(src)} {html.escape(a.strftime("%-d %b"))}'
                   f' — {html.escape(fmt.format(b))}</title>')
            # The shared tip layer keys on .dot and reads these two attributes.
            # They were missing here, so six of the lab-history points rendered
            # the literal string "undefinedundefined" — and they carried
            # tabindex=0, so a keyboard user reached it too. Leslie's points get
            # them as well: on the chart whose entire purpose is comparing two
            # labs, only one lab's points were inspectable.
            dat = (f'data-label="{html.escape(src)} {html.escape(a.strftime("%-d %b"))}" '
                   f'data-key="{html.escape(keys.get(a, ""))}" '
                   f'data-value="{html.escape(fmt.format(b))}"')
            if cls == "s-wg":
                o.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" class="dot" '
                         f'{dat}>{tip}</circle>')
            else:
                o.append(f'<path d="M{x:.1f},{y-5:.1f} L{x+5:.1f},{y:.1f} L{x:.1f},{y+5:.1f} '
                         f'L{x-5:.1f},{y:.1f} Z" class="refmark dot" {dat}>{tip}</path>')
        # Each lab labels its own latest value, so a panel where they disagree
        # shows both numbers rather than only whichever happens to be newer.
        #
        # The two labs sample within days of each other, so their last points sit
        # close together and their labels collide. Rather than detect the overlap,
        # give each source a fixed side — WaterGuru above its point, Leslie's
        # below — which cannot collide however near the values fall, and stays
        # stable as data arrives instead of jumping when a collision appears.
        la, lv = sp[-1]
        anchor = "end" if X(la) > W - 52 else "start"
        dy = -9 if cls == "s-wg" else 15
        o.append(f'<text x="{X(la) + (-9 if anchor=="end" else 9):.1f}" y="{Y(lv)+dy:.1f}" '
                 f'class="lastlbl sm {cls}" text-anchor="{anchor}">{fmt.format(lv)}</text>')

    o.append(f'<text x="{L}" y="{H-5}" class="xtick">{pts[0][0].strftime("%-d %b")}</text>')
    if len(pts) > 1:
        o.append(f'<text x="{W-R}" y="{H-5}" class="xtick" text-anchor="end">{pts[-1][0].strftime("%-d %b")}</text>')
    o.append("</svg>")

    # No target band means no verdict to give. An "unknown" pill on every
    # untargeted measure is noise pretending to be information. The verdict
    # follows the most recent reading from either lab, matching the tiles.
    pill = ""
    if band:
        st, arrow = verdict(pts[-1][1], band)
        pill = f'<span class="pill p-{st}">{arrow} {st}</span>'
    return (f'<div class="lab-mini"><div class="lm-h">'
            f'<h4>{html.escape(MEASURE_NAMES.get(measure, measure))}</h4>{pill}</div>'
            + "".join(o) + '</div>')

LANES = [("pump", C_PUMP, "Pump"), ("spa", C_SPA, "Spa"), ("sheer", C_SHEER, "Sheer descent")]

def strip(day):
    """A day as 24 hours of equipment, three lanes, 2px apart.

    The 2px gap is not decoration: abutting fills of different hue read as one
    shape, and the whole point of the strip is telling apart 'the pump ran and
    the spa spilled the whole time' from 'the pump ran and the spa did not'."""
    W, LH, GAP, PAD = 640, 9, 2, 2
    H = PAD * 2 + len(LANES) * LH + (len(LANES) - 1) * GAP
    def X(h): return h / 24 * W

    # THIRTY OF THESE SHARED ONE NAME AND NONE OF THEM CARRIED ITS DATE.
    #
    # Measured: the day strips computed accessible names of 468 to 596
    # characters, every one of them the same run of "Pump — 08:00Pump — 08:15…"
    # point tooltips, so a reader working down the list of days heard the same
    # paragraph thirty times and could not tell which day any of it was. The
    # runtimes are already printed as text beside each strip — that is one of
    # the three non-colour fallbacks bin/contrast checks for — so the desc says
    # the same figures rather than inventing a second account of the day.
    try:
        _d = dt.date.fromisoformat(day["date"])
        when_ = _d.strftime("%A %-d %B")
    except (KeyError, TypeError, ValueError):
        when_ = str(day.get("date", "an unnamed day"))
    h_ = day.get("hours") or {}
    ran = ", ".join(f"{name.lower()} {h_[key]:.1f}h" for key, _c, name in LANES
                    if h_.get(key))
    ndoses = len(day.get("chem") or [])
    sdesc = (f"A 24-hour timeline, one lane each for "
             f"{', '.join(n.lower() for _k, _c, n in LANES)}. "
             + (f"That day: {ran}." if ran else "None of the three ran that day.")
             + (f" {ndoses} dose{'s' if ndoses != 1 else ''} logged, "
                f"marked above the lanes." if ndoses else ""))
    lab, names = name_svg(f"{when_} — what the equipment did", sdesc)
    o = [f'<svg viewBox="0 0 {W} {H}" class="strip" preserveAspectRatio="none" '
         f'role="img"{lab}>', names]
    for h in range(3, 24, 3):
        o.append(f'<line x1="{X(h):.1f}" y1="0" x2="{X(h):.1f}" y2="{H}" class="hgrid"/>')
    for i, (key, colour, name) in enumerate(LANES):
        y = PAD + i * (LH + GAP)
        o.append(f'<rect x="0" y="{y}" width="{W}" height="{LH}" class="lane"/>')
        for s in day["segs"]:
            if s["k"] != key: continue
            x, w = X(s["a"]), max(X(s["b"]) - X(s["a"]), 1.5)
            o.append(f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{LH}" rx="2" '
                     f'fill="{colour}"><title>{name} — {s["t"]}</title></rect>')
    for c in day["chem"]:
        x = X(c["at"])
        o.append(f'<path d="M{x-4:.1f},0 L{x+4:.1f},0 L{x:.1f},6 Z" class="chemmark">'
                 f'<title>{html.escape(c["time"])} — {html.escape(c["amt"])} {html.escape(c["unit"])} '
                 f'{html.escape(c["what"])} @ {html.escape(str(c["pct"]))}%</title></path>')
    o.append("</svg>")
    return "".join(o)

# ------------------------------------------------------------------------ page
def tile(label, value, unit, band, note="", fmt="{:.1f}", src="", help="", measure=None):
    v = value["value"] if isinstance(value, dict) else num(value)
    if isinstance(value, dict) and not src:
        src = f"{value['source']} · {ago(value['measured'])}"
    state, arrow = verdict(v, band, measure)
    shown = fmt.format(v) if v is not None else "—"
    tgt = f"{fmt.format(band[0])}–{fmt.format(band[2])}" if band else ""
    # A term the reader may not know gets its definition attached in place,
    # from GLOSSARY, rather than sending them to another tab to find out what
    # the tile is even measuring.
    lbl = (f'<span data-help="{html.escape(help)}">{html.escape(label)}</span>'
           if help else html.escape(label))
    return (f'<div class="tile s-{state}">'
            f'<div class="tl">{lbl}</div>'
            f'<div class="tv">{shown}<span class="tu">{html.escape(unit)}</span></div>'
            f'<div class="tb"><span class="pill p-{state}">{arrow} {state}</span>'
            f'<span class="tt">target {tgt}</span></div>'
            + (f'<div class="tn">{html.escape(note)}</div>' if note else "")
            + (f'<div class="tsrc">{html.escape(src)}</div>' if src else "")
            + '</div>')

def version_string():
    """Version, plus the commit if this is a checkout.

    A generated page outlives the code that made it — it sits in site/ until the
    next collector run — so "which build produced this?" is a real question when
    something on it looks wrong. `git describe` answers it when the repository is
    present and is simply skipped when it is not, because a released copy should
    not need git to render its own footer.
    """
    from . import __version__
    v = f"v{__version__}"

    # GIT FIRST, WHERE THERE IS A GIT. This read REVISION first, and the deploy
    # writes REVISION into the SOURCE TREE and used to leave it there -- so every
    # page rendered on the developer's machine afterwards was stamped with the
    # commit of the last deploy, for ever. The footer is documented as the only
    # way a reader can tell a fresh page from a month-old one, and on the machine
    # that writes the code it was wrong by thirty-four commits.
    #
    # The deploy no longer leaves the file behind, and the order here means that
    # even if something does, a checkout describes itself correctly. REVISION
    # remains the answer for a deployed copy, which has no .git -- the rsync
    # excludes it deliberately -- and is the only place the question can be
    # answered there.
    # exists(), not isdir(): in a git WORKTREE `.git` is a FILE holding a
    # gitdir: pointer, so isdir() called every worktree "not a checkout" and
    # stamped its pages v0.1.0 with no commit at all — the one thing this
    # function exists to put there, missing exactly where several branches are
    # being rendered side by side and telling them apart matters most.
    if os.path.exists(os.path.join(config.root(), ".git")):
        try:
            import subprocess
            r = subprocess.run(
                ["git", "-C", config.root(), "describe", "--always", "--dirty", "--tags"],
                capture_output=True, text=True, timeout=3)
            if r.returncode == 0 and r.stdout.strip():
                return v + f" · {r.stdout.strip()}"
        except (OSError, subprocess.SubprocessError):
            # Only "git is not here" is expected and ignorable. A broader except
            # hid a NameError in this function for exactly as long as it existed:
            # the footer silently lost its commit id and nothing said why.
            pass

    rev_file = os.path.join(config.root(), "REVISION")
    try:
        if os.path.exists(rev_file):
            with open(rev_file) as f:
                rev = f.read().strip()[:60]
            if rev:
                return v + f" · {rev}"
    except OSError:
        pass
    return v

def check_public_is_usable(page):
    """The public build must not ship a feature the public cannot operate.

    The Pool Volume Calculator is one of the four public tabs and the headline
    reason to visit the site. Every one of its buttons was inside a `.factions`
    row, and `body.no-api .factions .btn { display:none }` hid the lot whenever
    the page had no write token -- which on the public deployment is every
    visitor. So the calculator rendered as a form with no way to submit it,
    beside a note reading "Start it with bin/serve": an instruction addressed to
    a developer and shown to the public. It was invisible to every check the
    project had, because the markup was present and correct; only a browser on
    the real site could see it, and .click() on a display:none button reports
    success.

    So the rule is asserted here instead: a button that drives a route this
    product deliberately leaves public must carry `pure`, and `pure` must be
    exempt from the no-api rule.
    """
    from . import style
    css = style.CSS
    if "body.no-api .factions .btn:not(.pure)" not in css:
        raise ValueError(
            "the no-api rule no longer exempts .pure buttons, so the public "
            "volume calculator is hidden from the public again")
    for bid in ("design-calc", "shape-calc", "spa-calc"):
        m = re.search(r'<button[^>]*id="' + bid + r'"[^>]*>', page)
        if not m:
            continue                      # not in this build, which is fine
        if "pure" not in m.group(0).split("id=")[0]:
            raise ValueError(
                f"{bid} drives a public, pure route but is not marked `pure`, so "
                f"body.no-api hides it from every anonymous visitor")


def _site(key, default=""):
    return str((CFG.get("site") or {}).get(key) or default).strip()


def _signout_url():
    """Where "Sign out" goes, from config rather than compiled in.

    This was one household's oauth2-proxy and one household's domain, hardcoded
    into a repository meant to be opened -- so every install rendered a sign-out
    link pointing at somebody else's sign-in service. [site] proxy_host and host
    say it instead; with neither set the menu entry is dropped rather than
    guessed at, because a Sign out that goes somewhere arbitrary is worse than
    no Sign out.
    """
    import urllib.parse as _u
    proxy, host = _site("proxy_host"), _site("host")
    if not proxy or not host:
        return ""
    back = _u.quote(f"https://{host}/?signedout=1", safe="")
    return f"https://{proxy}/oauth2/sign_out?rd={back}"


def _signout_domain():
    """What to CALL the set of sites a sign-out covers, for the notice."""
    host = _site("host")
    return ".".join(host.split(".")[-2:]) if host.count(".") >= 1 else host


def _collection_badge():
    """A count on the tab when a source needs attention, or nothing.

    Same rule as Home's badge: it counts only what is actionable now, because a
    badge that is always lit is furniture. A number here means a collector has
    failed or is overdue — not that an alarm fired, which is the pool's problem
    rather than the plumbing's.
    """
    try:
        from . import collection as _c, runlog
        bad = [x for x in _c.summary(runlog.rows())
               if x["state"] in _c.NEEDS_ATTENTION]
    except Exception:                                   # noqa: BLE001
        return ""
    if not bad:
        return ""
    return (f'<span class="badge" aria-label="{len(bad)} collection source(s) '
            f'need attention">{len(bad)}</span>')


def section_icon_coverage(page):
    """Which rendered `<h2>`s got an icon chosen for them, and which took the default.

    `icons.SECTION_ICONS` is a hand-maintained list of heading substrings, and
    `icon_for()` answers "list" for anything it does not recognise — so a
    heading that matches nothing and a heading deliberately given the list glyph
    are indistinguishable from the outside, and the miss is silent. Measured
    against the pages this build writes: 23 of 45 headings on the authenticated
    page and 14 of 30 on the public one match no key at all, which is not a
    fallback rate anybody chose.

    The table is READ here, never copied: a second list of substrings beside
    the first is the exact defect this project keeps finding. This returns the
    measurement so a check can pin it and say the number out loud, instead of
    the rate being a thing nobody has ever looked at.

    Returns (matched, uncovered_titles).
    """
    from .icons import SECTION_ICONS
    titles = [re.sub(r"<[^>]+>", "", m.group(1)).strip()
              for m in re.finditer(r"<h2>(.*?)</h2>", page, re.S)]
    uncovered = [t for t in titles
                 if not any(k in t.lower() for k, _ in SECTION_ICONS)]
    return len(titles) - len(uncovered), uncovered


def a11y_wiring(page):
    """Two ties that assistive technology needs and a reader never sees.

    DERIVED FROM THE RENDERED PAGE, not maintained beside it. Both of these are
    the shape of defect this project keeps finding — one fact spelled in two
    places — so neither is a list. The tabs are found by the role they already
    carry and matched to panels by the id convention the page already uses, and
    the status regions are found by the class the page already gives them.
    Applied after the public build has cut its private panels out, so nothing
    points at an element that is no longer in the file.

    1. STATUS MESSAGES WERE WATCHED BY NOTHING. Measured in a browser on both
       builds: `[aria-live], [role=status], [role=alert], output` matched ZERO
       elements. Every form on the page reports through a `.fmsg` span — the
       text that says "logged — reloading", "saved as the pool volume", or why a
       write was refused — and each one was written into the DOM with nothing to
       announce it, so a screen-reader user pressed a button that starts a gas
       heater or records a dose and got silence either way. A .fmsg that never
       changes announces nothing, which is why this can be applied to all of
       them rather than to a chosen few.

    2. THE TABS NAMED NO PANEL. role=tablist/tab/tabpanel were all present and
       the arrow keys worked, but no tab carried aria-controls and no panel
       carried aria-labelledby, so the relationship a screen reader uses to move
       from a tab to its content — and to say which section the content belongs
       to — was absent on all seven.

    Returns (page, statuses, tabs_tied) so the build can assert it did something.
    """
    n_status = 0
    def _status(m):
        nonlocal n_status
        el = m.group(0)
        if re.search(r'\brole\s*=\s*"', el):
            return el
        n_status += 1
        return el[: len(m.group(1)) + 1] + ' role="status"' + el[len(m.group(1)) + 1:]
    page = re.sub(r'<(span|div|p)\b(?=[^>]*\bclass="[^"]*\bfmsg\b[^"]*")[^>]*>',
                  _status, page)

    # The tab buttons, however their attributes are ordered — the same lesson
    # declared_tabs() records: attribute order is not something an author thinks
    # about, so it must not be something a rule depends on.
    tied = []
    def _tab(m):
        el = m.group(0)
        if not re.search(r'\brole\s*=\s*"tab"', el):
            return el
        t = re.search(r'\bdata-tab\s*=\s*"([a-z0-9_-]+)"', el)
        if not t or f'id="tab-{t.group(1)}"' not in page:
            return el          # a tab whose panel this build cut out
        tied.append(t.group(1))
        return el[:7] + f' id="tabbtn-{t.group(1)}" aria-controls="tab-{t.group(1)}"' + el[7:]
    page = re.sub(r'<button\b[^>]*>', _tab, page)
    for t in tied:
        page = re.sub(rf'(<div class="panel" id="tab-{t}")',
                      rf'\1 aria-labelledby="tabbtn-{t}"', page, count=1)
    return page, n_status, len(tied)


def check_svg_styles(page):
    """A tag-like token inside an SVG <style> discards every rule after it.

    An SVG <style> is FOREIGN CONTENT, not raw text like an HTML one: the parser
    ends the element at the first `<`, so a stray angle bracket -- in a comment,
    even -- silently throws away the rest of the stylesheet. It happened here:
    the word `<text>` inside a CSS comment took every .lbl/.ttl/.sm/.bx rule with
    it and the whole wiring diagram fell back to black on white, while the page
    rendered and looked entirely plausible. Exactly the class of failure this
    build step exists to catch, and it was invisible to every check we had.
    """
    for m in re.finditer(r"<svg\b.*?</svg>", page, re.S):
        for st in re.finditer(r"<style>(.*?)</style>", m.group(0), re.S):
            bad = re.search(r"<[a-zA-Z/!]", st.group(1))
            if bad:
                near = st.group(1)[max(0, bad.start() - 40): bad.start() + 40]
                raise ValueError(
                    "a tag-like token inside an SVG <style> ends the style "
                    "element and discards every rule after it: "
                    f"...{near.strip()}...")


# Every tab the page declares, however the attributes are written.
#
# THE GATE WAS DEFEATED BY ATTRIBUTE ORDER. Both the build assertion and the
# selftest matched `<button role="tab" data-tab="...">` — one regex requiring
# the two attributes adjacent and in that order. A tab written
# `<button data-tab="secrets" role="tab">` is invisible to it, is therefore not
# "unclassified", is not in PRIVATE_TABS so the public build does not strip it,
# and ships to anonymous visitors. Demonstrated by adding exactly that button
# plus a panel carrying a house address: all three gates exited 0 and the
# address was in index.html.
#
# This is the same shape as the sentinel regex that required whoami() adjacent
# to its comparison and so matched none of the five call sites it was written
# for. Attribute order is not something an author thinks about, so it must not
# be something a gate depends on.
# The landing page's screenshots, and where they come from.
#
# They are committed under docs/ because they cannot be regenerated where they
# are needed: bin/screenshots drives a real browser, and the container that
# renders in production has no browser in it and never will. So they are a
# build input, like the icons, rather than something the deploy produces.
#
# Copied into the site directory rather than referenced out of docs/, because
# the site directory is what the HTTP handler is rooted at -- it serves exactly
# one tree and that is the point of it. Nothing else in this product writes a
# non-HTML file there, which is why the copy says out loud what it is doing.
CARDS_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "docs", "screenshots", "cards")
CARDS_DIR = "screenshots"          # under the site directory, and in the markup


def copy_cards():
    """Put the landing page's screenshots where the server can serve them.

    Returns the number copied. Never raises: a render that fails because an
    ornamental PNG could not be copied is a page nobody can read over a picture
    nobody needed, and this runs on every pushed sample. A missing card leaves
    the img's alt text, which says what the picture was of.

    Copies only what has changed. `bin/render` runs after every sample, every
    write and every six hours; re-writing two hundred kilobytes onto a CIFS
    share each time to no effect is the kind of cost that is invisible until
    somebody reads an Azure bill.
    """
    import shutil
    n = 0
    try:
        dest = os.path.join(SITE, CARDS_DIR)
        os.makedirs(dest, exist_ok=True)
        for name in sorted(os.listdir(CARDS_SRC)):
            if not name.endswith(".png"):
                continue
            src, dst = os.path.join(CARDS_SRC, name), os.path.join(dest, name)
            if (os.path.exists(dst)
                    and os.path.getsize(dst) == os.path.getsize(src)
                    and os.path.getmtime(dst) >= os.path.getmtime(src)):
                continue
            shutil.copy2(src, dst)
            n += 1
    except OSError:
        return n
    return n


def _favicon_href():
    """brand.favicon_svg() as a data: URL for <link rel=icon>.

    Percent-encoded by hand rather than base64: an SVG favicon that a person
    can read in view-source is worth the few characters, and quote() with a
    generous safe-list leaves it legible. The `#` of a colour literal MUST be
    encoded -- unencoded it ends the URL and the icon silently becomes a blank
    square, which is the classic way this breaks.

    THREE CHARACTERS CANNOT BE LEFT ALONE, and the first version left all three.
    The SVG's own attribute quotes ended the href on the very first one, so the
    rest of the drawing fell out of the tag and `">` was printed in the corner
    of every page -- visible in a browser within a minute, which is the only
    reason it did not ship. They are turned into single quotes rather than
    encoded, because the mark stays readable that way and an SVG attribute is
    happy with either. And `<`/`>` ARE encoded: legal inside an attribute value,
    but this build reads its own output with regexes that match `<[^>]+>`, and a
    stray `>` in an attribute is a tag boundary as far as every one of them is
    concerned.
    """
    from urllib.parse import quote
    return ("data:image/svg+xml,"
            + quote(_brand.favicon_svg().replace('"', "'"), safe="/:=' "))


def order_tabs(page, order):
    """Lay the nav out in `order`, and refuse if the two disagree.

    THE NAV ORDER IS DATA, NOT MARKUP. The buttons used to sit in the template
    in one fixed sequence and the public build simply deleted some of them,
    which meant the public order was whatever was left over -- Poolhound info
    third, on the page whose entire job is to be the first thing read.

    Refuses on a mismatch in either direction rather than silently dropping or
    appending: a button the order does not name would vanish from the page
    while remaining in the file, and an order naming a button that is not there
    is a rule protecting nothing. Both are the failure this project keeps
    finding, and both are cheap to detect here because the set is tiny.
    """
    m = re.search(r'(<nav class="tabs"[^>]*>)(.*?)(</nav>)', page, re.S)
    if not m:
        raise ValueError("no tab nav in the page to order")
    buttons = {}
    for b in re.finditer(r'<button role="tab" data-tab="(?P<t>[a-z]+)".*?</button>',
                         m.group(2), re.S):
        buttons[b.group("t")] = b.group(0)
    if set(buttons) != set(order):
        raise ValueError(
            f"the nav has tab button(s) {sorted(set(buttons) - set(order))} that "
            f"the order does not name, and the order names "
            f"{sorted(set(order) - set(buttons))} that the nav does not have. "
            f"A button missing from the order would be dropped from the page "
            f"without being removed from the build.")
    # THE DIVIDER, DRAWN FROM THE ORDER RATHER THAN WRITTEN INTO THE MARKUP.
    # The nav has two groups — the screens that operate the pool, then the ones
    # that explain it — and which button starts the second depends entirely on
    # this tuple. A class hand-placed in the template would be a second copy of
    # that decision, and it would be wrong on the public build, where the
    # group-start button is the FIRST button and a divider before it would be a
    # line hanging off the left edge of the bar.
    #
    # aria-hidden and not a tab: a tablist whose children are not all tabs is a
    # tablist an assistive technology may report wrongly, and removing it from
    # the accessibility tree entirely is what keeps that true.
    out = []
    for i, t in enumerate(order):
        if t == TAB_GROUP_START and i:
            out.append('<span class="tabsep" aria-hidden="true"></span>')
        out.append(buttons[t])
    return page[:m.start(2)] + "\n  " + "\n  ".join(out) + "\n" + page[m.end(2):]


def set_default_tab(page, tab):
    """Open the page on `tab`: one selected button, one visible panel, matched.

    WRITTEN, NOT ASSUMED. The template can only carry one default, and there
    are two builds that want different ones -- so the public build used to
    inherit whatever the authenticated markup said and was only correct by
    coincidence. Worse, the coincidence was about to break: the tab the markup
    selected is now one the public build cuts out, which would have left a page
    with no selected tab and every panel hidden. Blank, valid, and serving.

    Every button and every panel is rewritten from this one argument, so the
    two cannot disagree, and the result is asserted below rather than trusted.
    """
    def button(m):
        want = "true" if m.group("t") == tab else "false"
        return re.sub(r'aria-selected="(?:true|false)"',
                      f'aria-selected="{want}"', m.group(0))

    page = re.sub(r'<button role="tab" data-tab="(?P<t>[a-z]+)".*?</button>',
                  button, page, flags=re.S)

    def panel(m):
        t, attrs = m.group("t"), re.sub(r"\s+hidden\b", "", m.group("attrs"))
        return f'<div class="panel" id="tab-{t}"{attrs}{"" if t == tab else " hidden"}>'

    page = re.sub(r'<div class="panel" id="tab-(?P<t>[a-z]+)"(?P<attrs>[^>]*?)>',
                  panel, page)

    selected = re.findall(
        r'<button role="tab" data-tab="([a-z]+)"[^>]*aria-selected="true"', page)
    shown = re.findall(r'<div class="panel" id="tab-([a-z]+)"(?![^>]*\shidden)', page)
    if selected != [tab] or shown != [tab]:
        raise ValueError(
            f"default tab {tab!r}: the page ended up with selected={selected} "
            f"and visible={shown}. Exactly one of each, and the same one, or "
            f"the page opens blank or opens on two things at once.")
    return page


def declared_tabs(html):
    """The data-tab value of every element carrying role="tab"."""
    out = set()
    for m in re.finditer(r'<button\b[^>]*>', html):
        el = m.group(0)
        if not re.search(r'\brole\s*=\s*"tab"', el):
            continue
        t = re.search(r'\bdata-tab\s*=\s*"([a-z0-9_-]+)"', el)
        if t:
            out.add(t.group(1))
    return out


def check_page(page):
    """Refuse to write a page whose scripts or embedded data are broken.

    Both halves of this have already failed in practice. An apostrophe in "the
    water's resistance" ended a JavaScript string literal and took the entire
    script with it — tabs, forms, theme, everything — while the page still
    rendered and still looked correct in a screenshot. The failure is invisible
    unless the console is open, which is exactly the kind of thing a build step
    should catch instead of a person.

    JSON is checked always, because that needs nothing but the standard library.
    JavaScript is checked when node is installed — and the server's image now installs
    it, because for as long as it did not, the syntax check this docstring
    describes had never once run on the machine that renders the page after every
    sample, every write and every six hours from cron. It was exercised only on a
    laptop. A missing node is still not a hard error, because a released copy
    must be able to render without a JavaScript toolchain, but it is no longer
    SILENT: rendering without the check is a fact worth one line on stderr.
    """
    # ONE PASS OVER EVERY SCRIPT ELEMENT, BRANCHING ON ITS PARSED ATTRIBUTES.
    #
    # Both halves of this used to spell the JSON block as `id` before `type`,
    # including the "blocks not validated" guard that existed to catch a block
    # the pattern missed — so a block written `type="application/json" id="x"`
    # was invisible to the JSON check, invisible to the guard, and excluded from
    # the JavaScript check as well, which is every half of this function at once.
    # Attribute ORDER is not something an author thinks about, so it must not be
    # something the gate depends on. Nothing here matches a fixed spelling any
    # more: the attributes are read, and the branch is taken from what they say.
    #
    # Ids may contain hyphens — saved-shape, pool-templates, spa-templates — and
    # an earlier \w+ matched none of those, so the validator covered exactly the
    # two blocks that cannot vary and skipped the one built from the owner's own
    # data.
    ids, scripts, unchecked, odd = [], [], [], []
    for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", page, re.S):
        attrs, body = m.group(1), m.group(2)
        mt = re.search(r'\btype\s*=\s*"([^"]*)"', attrs)
        mi = re.search(r'\bid\s*=\s*"([^"]*)"', attrs)
        typ = (mt.group(1).strip().lower() if mt else "")
        sid = mi.group(1) if mi else "(no id)"
        if "src=" in attrs:
            unchecked.append(attrs.strip())
        elif typ == "application/json":
            ids.append(sid)
            try:
                json.loads(body)
            except json.JSONDecodeError as e:
                raise ValueError(f"embedded JSON '{sid}' is not valid: {e}") from None
        elif typ in ("", "text/javascript", "module"):
            scripts.append(body)
        else:
            # Not "fine" — unrecognised. A type this function does not know how
            # to check is a block nothing checks, which is the failure the whole
            # function exists to prevent.
            odd.append(f'{sid} (type="{typ}")')
    # Opening tags too, not only complete elements: a src= script that never
    # closes matches no element and would slip past the loop above.
    unchecked += [a.strip() for a in re.findall(r"<script\b([^>]*)>", page)
                  if "src=" in a and a.strip() not in unchecked]
    if odd:
        raise ValueError(
            f"script block(s) {odd} carry a type this gate does not know how to "
            f"validate, so nothing checked them. Add a branch for it here, or "
            f"use application/json.")
    if unchecked:
        raise ValueError(
            f"script tag(s) loading external source were not syntax checked: "
            f"{unchecked}. This page ships its own JavaScript; a src= here is "
            f"either a mistake or a dependency nothing validates.")

    try:
        import shutil, subprocess, tempfile
        if not shutil.which("node"):
            print("  note: node not installed — inline JavaScript was NOT syntax "
                  "checked", file=sys.stderr)
            return
        for i, src in enumerate(scripts):
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
                f.write(src); path = f.name
            try:
                r = subprocess.run(["node", "--check", path],
                                   capture_output=True, text=True, timeout=15)
                if r.returncode != 0:
                    # The WHOLE message. node --check puts only `path:line` on
                    # its first line and the caret and the actual "SyntaxError:
                    # Unexpected identifier" on the next few, so keeping line one
                    # threw away the half that says what is wrong. The temp path
                    # is meaningless to a reader, so it is stripped out.
                    # Both spellings: macOS resolves /tmp to /private/tmp, so
                    # node reports a path that is not the string we opened, and
                    # a plain replace left "/private<inline script 0>".
                    hide = f"<inline script {i}>"
                    detail = r.stderr.strip()
                    for form in {path, os.path.realpath(path)}:
                        detail = detail.replace(form, hide)
                    detail = detail.strip()
                    raise ValueError(
                        f"inline script {i} has a syntax error:\n{detail}")
            finally:
                os.unlink(path)
    except subprocess.TimeoutExpired:
        # TimeoutExpired IS a SubprocessError, so the clause below swallowed it
        # and the build reported success having checked nothing. A node that
        # hangs is not the same fact as a node that is absent.
        print("  note: node --check timed out — inline JavaScript was NOT fully "
              "syntax checked", file=sys.stderr)
    except (OSError, subprocess.SubprocessError) as e:
        # Only "node could not be run" is expected here. Say which.
        print(f"  note: could not run node --check ({type(e).__name__}) — inline "
              f"JavaScript was NOT syntax checked", file=sys.stderr)

# ---------------------------------------------------------------- data tables
# One component for every table that grows. The old one truncated to the last
# 40 rows with no way to reach the rest — 394 samples on file, 40 reachable, and
# nothing on the page admitting it. Silent truncation is the failure mode here,
# not scrolling: a reader cannot ask for what they cannot see is missing.
#
# WHAT THIS GIVES EVERY TABLE
#   a sticky header, so scrolling a long table does not lose the column names
#   a filter box, which is the only practical way through a few hundred rows
#   click-to-sort on any column
#   an HONEST count — "showing 40 of 394" — and a control to show them all
#   a bounded height, so one big table cannot make the page unnavigable
#
# The row cap is about page weight, not privacy: these tables are on the public
# panel and always have been. A year of samples is ~35,000 rows and nobody wants
# that inlined, so a bounded slice is rendered and the page says what it is.

DEFAULT_SHOW = 40          # rows visible before "show all"
MAX_RENDER   = 400         # rows put into the page at all

# What is running at this moment, in the shared state vocabulary.
#
# On Home because that is where somebody looks first, and "is the pump on?" is
# the question asked most often and answered least directly — until now it took
# reading a day strip and working out where the right-hand edge was.
#
# Equipment only, not chemistry: the tiles above already carry the chemistry and
# repeating it here in a different visual language would make two ways of saying
# the same thing.
NOW_ITEMS = [
    ("pump",        "Filter pump"),
    ("spa",         "Spa"),
    ("sheer",       "Sheer descent"),
    ("pool_light",  "Pool light"),
    ("spa_light",   "Spa light"),
    ("pool_heat",   "Pool heater"),
    ("spa_heat",    "Spa heater"),
    ("solar_valve", "Solar valve"),
    # The agent has written this column all along and watch.py mails the owner
    # when it changes. It was missing here, so the one place to look after
    # getting that mail -- "is freeze protection still running?" -- did not
    # answer it.
    ("freeze",      "Freeze protection"),
]

def now_strip(samples):
    """The current state of each circuit, from the newest sample.

    Deliberately says how old the reading is. A strip of green that turns out to
    be four hours stale is worse than no strip, because it answers the question
    confidently and wrongly — so the age rides alongside and goes amber when the
    sampler has clearly stopped.
    """
    if not samples:
        return ('<p class="empty">No equipment samples yet — the agent on the Pi '
                'writes one every fifteen minutes.</p>')
    # newest(), not the last row appended. That was defect 10 of the acceptance
    # review and it was fixed on Home and nowhere else -- both labs return their
    # whole history on every pull and the agent re-pushes after a reconnect, so
    # a row arriving out of order is normal rather than exotic.
    last = newest(samples, "ts")
    age = ago(last.get("ts"))
    d = when(last.get("ts"))
    stale = bool(d and (dt.datetime.now() - d).total_seconds() > 2400)

    chips = []
    for col, label in NOW_ITEMS:
        raw = str(last.get(col, "")).strip()
        if raw == "":
            state, word = "unknown", "no reading"
        elif raw == "1":
            state, word = "on", "on"
        else:
            state, word = "off", "off"
        chips.append(f'<span class="st st-{state}" data-col="{col}" '
                     f'data-device="{STATE_DEVICE.get(col, "")}">'
                     f"<b>{html.escape(label)}</b><i>{word}</i></span>")

    swg = last.get("swg_pct", "")
    if swg != "":
        chips.append(f'<span class="st st-{"on" if float(swg or 0) > 0 else "off"}">'
                     f'<b>Cell output</b><i>{html.escape(str(swg))}%</i></span>')

    agecls = "st-pending" if stale else "st-off"
    return (f'<div class="nowstrip">{"".join(chips)}</div>'
            f'<div class="st-legend"><span class="st {agecls}">'
            f'<b>reported</b><i>{html.escape(age)}</i></span>'
            f'<span class="st st-on"><b>on</b></span>'
            f'<span class="st st-off"><b>off</b></span>'
            f'<span class="st st-pending"><b>waiting for the next reading</b></span>'
            f'</div>')

# Which control each sample column belongs to, so a pending command can find its
# chip. The control tab owns the same mapping in the other direction; this is the
# only place the two meet, and it is one line each.
# The inverse of commands.STATE_COLUMN. The comment that used to sit here said
# "this is the only place the two meet, and it is one line each" — it was one of
# five places, across two machines.
from . import commands as _CMD
STATE_DEVICE = dict(_CMD.COLUMN_DEVICE)

# Which build is being rendered. data_table() needs it -- the reach control
# calls a gated route, so offering it on the public page would be a button that
# always 401s -- and threading a parameter through every call site is a lot of
# churn for one boolean. Set once at the top of _build(), the same way reload()
# sets the config every helper reads.
_PUBLIC = False


def data_table(name, data, cols, note="", show=DEFAULT_SHOW, cap=MAX_RENDER,
               newest_first=True, source=None):
    """One table, with an honest count. `note` is PLAIN TEXT, never markup.

    It used to be neither: nothing said which it was, it was inserted into the
    page unescaped, and the one caller that wanted emphasis wrote `&lt;b&gt;`
    — so the main page carried the literal characters `<b>What the pool did</b>`,
    angle brackets and all, in the middle of a sentence. It validated, it passed
    node --check, and only a browser could see it.

    Text, and escaped here, is the half of that choice that cannot come back: a
    note is a sentence, and a table that one day takes its note from a CSV
    column must not be able to inject a tag by doing so. Emphasis inside a note
    is spelled with quotation marks.
    """
    if not data:
        return (f'<div class="dtable-empty"><b>{html.escape(name)}</b>'
                f'<span>nothing recorded yet</span></div>')

    total = len(data)
    rows = list(reversed(data)) if newest_first else list(data)
    rendered = rows[:cap]

    head = "".join(
        # data-col is the sort INDEX; data-field is the CSV column name. The
        # reach control fills this table from /api/rows, whose rows are keyed
        # by name, and reading the visible header text instead would break the
        # moment a column is renamed for display.
        f'<th tabindex="0" role="columnheader button" data-col="{i}" '
        f'data-field="{html.escape(c)}" '
        f'title="Sort by {html.escape(c)}">{html.escape(c)}</th>'
        for i, c in enumerate(cols))

    body = []
    for n, r in enumerate(rendered):
        cells = "".join(f'<td>{html.escape(str(r.get(c, "")))}</td>' for c in cols)
        body.append(f'<tr{"" if n < show else " hidden"}>{cells}</tr>')

    # Said plainly, and only where it is true. "Showing 40 of 40" is noise.
    if total > len(rendered):
        truth = (f'showing the most recent <b class="dt-shown">{show}</b> of '
                 f'{len(rendered)} rendered &middot; {total:,} on file')
    elif len(rendered) > show:
        truth = f'showing <b class="dt-shown">{show}</b> of {total:,}'
    else:
        truth = f'{total:,} row{"" if total == 1 else "s"}'

    more = ('<button type="button" class="btn dt-more">Show all</button>'
            if len(rendered) > show else '')
    # The whole file, not the window. A table that honestly says "40 of 9,103"
    # is still a dead end if the other 9,063 are only reachable by ssh-ing to
    # the server. Rendered only on the authenticated build, because it is this
    # household's data; the link 404s for anyone else regardless.
    grab = (f'<a class="dt-get" href="/api/export/{source}" download '
            f'title="Download every row as CSV">&darr; CSV</a>'
            if source else '')

    # REACHING PAST THE WINDOW, not just admitting it. The count says
    # "showing 40 of 387" and the filter says how many rows it could not
    # search -- both added because silent truncation is the worse failure --
    # and neither was a way to SEE the rows they name. The controller writes 96
    # samples a day here, so the page's reach ran out within a fortnight.
    #
    # Only when rows are genuinely out of view, and only on the authenticated
    # build: /api/rows needs `view`, and a control the public page cannot use
    # reads as broken rather than as absent.
    reach = ('<span class="dt-reach">'
             '<label>from <input type="date" class="dt-from"></label>'
             '<label>to <input type="date" class="dt-to"></label>'
             '<button type="button" class="btn dt-fetch">Fetch</button>'
             '<span class="dt-fetched"></span></span>'
             if (source and not _PUBLIC and total > len(rendered)) else '')

    return f'''<div class="dtable" data-show="{show}" data-total="{total}"
     data-rendered="{len(rendered)}"{f' data-csv="{source}"' if source else ""}>
  <div class="dt-bar">
    <b class="dt-name">{html.escape(name)}</b>
    <input type="search" class="dt-filter" placeholder="Filter&hellip;"
           aria-label="Filter {html.escape(name)}">
    <span class="dt-count"><span class="dt-truth">{truth}</span
      ><span class="dt-search" hidden></span></span>
    {reach}{more}{grab}
  </div>
  {f'<p class="dt-note">{html.escape(note)}</p>' if note else ''}
  <div class="dt-scroll">
    <table class="raw">
      <thead><tr>{head}</tr></thead>
      <tbody>{"".join(body)}</tbody>
    </table>
  </div>
  <p class="dt-none" hidden></p>
</div>'''

def strip_elements(page, opening_re, tag="div"):
    """Remove an element and everything inside it, counting nesting.

    A regex cannot do this, and the one that used to try got it wrong in the
    worst way: `<div class="refresh-wrap">.*?</div>\\s*</div>` stops at the
    SECOND closing tag, but that element nests three deep, so one `</div>` was
    left behind on every public build. A stray close tag does not look like an
    error — it silently reparents everything after it, which is how the header
    ended up with its controls dumped onto a line of their own below the page.

    Counting opens and closes is the only way to be right, and being right here
    matters more than usual: this is the function that decides what an anonymous
    visitor is allowed to see.
    """
    out, pos = [], 0
    for m in re.finditer(opening_re, page):
        if m.start() < pos:
            continue                       # inside something already removed
        out.append(page[pos:m.start()])
        depth, i = 1, m.end()
        open_tag, close_tag = f"<{tag}", f"</{tag}>"
        while depth and i < len(page):
            no = page.find(open_tag, i)
            nc = page.find(close_tag, i)
            if nc == -1:
                i = len(page); break       # unbalanced input: drop the rest
            if no != -1 and no < nc:
                depth += 1; i = no + len(open_tag)
            else:
                depth -= 1; i = nc + len(close_tag)
        pos = i
    out.append(page[pos:])
    return "".join(out)


def with_subnav(panel_html, panel_id):
    """Build a jump-list for a panel from the sections it actually contains.

    Derived rather than declared. A hand-written list of links is a second copy
    of the page structure, and the failure mode is silent: someone renames a
    section, the nav keeps pointing at the old anchor, and nothing complains.
    Reading the headings out of the rendered panel means the nav cannot describe
    a page that does not exist.

    Sections without an id get one from their heading, because an anchor nobody
    remembered to add is the commonest reason a jump-list has a dead entry.
    """
    out, items = [], []
    pos = 0
    for m in re.finditer(r'<section(?P<attrs>[^>]*)>\s*<h2>(?P<title>.*?)</h2>',
                         panel_html, re.S):
        attrs, title = m.group("attrs"), re.sub(r"<[^>]+>", "", m.group("title")).strip()
        sid = re.search(r'id="([^"]+)"', attrs)
        if sid:
            sid, new_attrs = sid.group(1), attrs
        else:
            sid = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
            sid = f"{panel_id}-{sid}"
            new_attrs = f' id="{sid}"' + attrs
        out.append(panel_html[pos:m.start()])
        out.append(f'<section{new_attrs}><h2>{icon(icon_for(title), 17, "h-ico")}'
                   f'<span>{title}</span></h2>')
        pos = m.end()
        items.append((sid, title))
    out.append(panel_html[pos:])
    body = "".join(out)
    if len(items) < 2:
        return body, ""
    links = "".join(
        f'<a href="#{sid}" data-jump="{sid}">{icon(icon_for(title), 14)}'
        f'<span data-label="{html.escape(title, quote=True)}">'
        f'{html.escape(title)}</span></a>' for sid, title in items)
    return body, f'<nav class="subnav" aria-label="Sections">{links}</nav>'

def fill(template, **values):
    """Substitute @@name@@ tokens.

    str.format cannot be used on this template any more: it now carries the
    page's JavaScript and a JSON catalogue, both full of braces, and every one
    would have to be doubled to survive formatting. Doubling braces in code that
    is read and edited as code is a standing invitation to a syntax error that
    only appears at render time, so the placeholders were made unambiguous
    instead. A missing key raises rather than leaving @@name@@ visible on the
    page.
    """
    out = template
    for k, v in values.items():
        out = out.replace("@@" + k + "@@", str(v))
    # [a-z_] only would have let @@homeTab@@ or @@tab2@@ through unfilled, to be
    # rendered as literal text on the page. CLAUDE.md sells this seam on exactly
    # one property -- "render.fill() raises on an unfilled one" -- so the pattern
    # has to match every token spelling the template can hold, not the subset
    # that happened to be in use.
    missing = re.findall(r"@@([A-Za-z0-9_]+)@@", out)
    if missing:
        raise KeyError(f"unfilled template tokens: {sorted(set(missing))}")
    return out

# Which tabs may be served without a login. The division is not "read versus
# write" -- it is whether the content is about the POOL or about the HOUSE.
# Chemistry is impersonal and mildly interesting; the rest names an address, an
# email, an internal IP, and can switch a gas heater on.
#
# HOME MOVED ACROSS. It was public, and nothing on it was a secret -- it is
# derived from the same measurements the chemistry reference is. But it is a
# DASHBOARD, and a dashboard answers "how is it doing" to somebody who has not
# been told what "it" is, which is what every anonymous visitor to this address
# is. It also carried this household's current readings and runtime to no
# purpose: the argument for publishing chemistry is that it is useful to
# somebody else's pool, and that argument does not reach our chlorine.
#
# THE TWO TUPLES ARE DECLARED ONCE, HERE, and help.py reads them from this
# module. There used to be a second copy of PUBLIC_TABS in help.py, and it was
# the LIVE one: the copy here was referenced by nothing, so a reader editing the
# tuple that sits beside the excision -- the obvious place -- changed a constant
# with no readers and Help went on linking to whatever it linked to before.
#
# PUBLIC_TABS IS ALSO THE PUBLIC NAV ORDER. See order_tabs().
PUBLIC_TABS = ("info", "volume", "chemistry", "help", "policies")
PRIVATE_TABS = ("control", "home", "chemicals", "collection", "settings", "ask")

# The nav order on the AUTHENTICATED build, which is a different question from
# which tabs exist. Somebody who has signed in came to operate the pool, so the
# screens that do that come first and the reference material goes last; a
# stranger came to find out what this is, so the public build leads with the
# page that says.
#
# Derived nowhere else: one ordered tuple per build, and order_tabs() lays the
# nav out from it. The alternative -- one order in the markup and a mental note
# that the public build "just drops some" -- is what put Poolhound info third on
# the page whose whole job is to be read first.
#
# THE TAIL OF THIS TUPLE IS PUBLIC_TABS, IN ITS ORDER. That is what the divider
# means: everything after it is the page a stranger gets, sitting in the same
# sequence they would see it in. Chemistry moved across to make it true —
# it is a reference, not an instrument, and it was the one public tab stranded
# among the screens that operate the pool.
TAB_ORDER = ("home", "control", "settings", "chemicals", "ask", "collection",
             "info", "volume", "chemistry", "help", "policies")

# Where the reference group starts, for the divider the nav draws before it.
# Named rather than counted, so reordering the tuple above moves the rule.
TAB_GROUP_START = "info"

# Which tab each build opens on. It is the FIRST tab in each build's order, and
# that is the whole rule: a nav whose first item is not the page you land on
# reads as a bug in the nav. The authenticated page therefore opens on Home,
# which is where the water is now, what is running and what to do next; the
# public page opens on the explanation, because somebody who has not signed in
# came to find out what this is.
#
# It opened on Pool control until the nav was reordered to put Home first, on
# the reasoning that somebody who signed in came to operate the pool. Still
# true, and the controls are one tab away — but the dashboard answers "is
# anything wrong" before you have asked anything, which is the question you
# actually arrive with.
PRIVATE_DEFAULT_TAB = "home"
PUBLIC_DEFAULT_TAB = "info"

# What the PUBLIC page must still have once the private half has been cut out of
# it. The excision is done with regexes over the rendered page, and a pattern
# that reaches one element too far fails silently in the direction nothing was
# watching: the leak list proves no control endpoint survived, which is equally
# true of a blank page. These are the four public tabs, the script that drives
# them, and the two pure routes the calculator and the photo tracer call.
PUBLIC_MUST_SURVIVE = (
    r'id="tab-info"', r'id="tab-volume"', r'id="tab-chemistry"', r'id="tab-help"',
    r'id="tab-policies"',
    r'data-tab="info"', r'data-tab="volume"', r'data-tab="chemistry"', r'data-tab="help"',
    r'data-tab="policies"',
    r"/api/pool-shape/compute", r"/api/photo", r"/api/health",
    r'id="design-calc"', r'id="shape-detect"',
    r"<script", r"</script>",
)

# AND WHAT MUST STILL BE IN THOSE TABS. The list above is ids and endpoints, and
# an excision that empties the home and chemistry panels while leaving their
# wrapper divs passes every one of it: 37% of the page gone, four tab ids
# present, build exits 0. The check stopped one element short of the mistake it
# was written to catch — "the page does not contain a control endpoint" is
# equally true of a page with nothing in it, and so is "the page has a div
# called tab-home".
#
# These are contents rather than containers, and they are counted, because one
# surviving tile does not make a panel. The numbers are floors for an install
# with NO DATA AT ALL, which is the leanest the page ever legitimately gets:
# seven tiles and fifteen tables on an empty install, against four and six here.
# RETUNED WHEN HOME WENT PRIVATE. Two of the four entries here counted things
# that only ever existed on Home -- the reading tiles, and data_table()'s row
# counter -- so once Home moved behind the sign-in they were floors of 4 and 1
# against an actual of 0, and they failed the build, correctly, the first time
# it ran. They have not been deleted: they moved to PRIVATE_MUST_SURVIVE_COUNTS
# below, which is the build they now describe. A floor quietly dropped because
# it started failing is a guarantee quietly withdrawn.
PUBLIC_MUST_SURVIVE_COUNTS = (
    (r"<table", 10),            # the tables that are the non-colour route
    (r"<svg", 20),              # diagrams, the calculator, icons
    (r'class="card', 5),        # the panels have contents, not just wrappers
    # The landing page's screenshots and Help's figures. A public build with no
    # figure in it has lost the tab this address exists to serve.
    (r"<figure", 5),
)

# The same idea for the authenticated build, which had no such check at all --
# every count here was aimed at the public page, on the assumption that the
# private one could not be hollowed out because nothing cuts it. Nothing cuts
# it deliberately; a panel that renders empty empties it just as well, and the
# two components that most need saying are exactly the ones that moved.
PRIVATE_MUST_SURVIVE_COUNTS = (
    (r'class="tile', 4),        # the current-reading tiles
    # data_table() in either of its two forms: the honest count on a table that
    # has rows, or the "nothing recorded yet" card on one that does not. A page
    # carrying neither has lost the component this project's truncation rule
    # lives in.
    (r'class="dt-count"|class="dtable-empty"', 1),
)

# THIS USED TO BE A RATIO, AND THE RATIO STOPPED MEANING ANYTHING.
#
# It was PUBLIC_MIN_FRACTION = 0.70: the public page divided by the page it was
# cut from. The argument for it was that the ratio is stable because "the tables
# that grow are in both" -- 0.79 empty, 0.87 with a year of samples -- so a
# sudden drop meant an over-greedy excision.
#
# That argument died with this change. Every table that grows with history is
# now on Home, Chemicals or Collection, which are all private, and the public
# page is made of material that does not grow at all: an explanation, a
# reference, a calculator and a help document. Measured right now, the same
# install gives 0.742 with an empty data directory and 0.574 with this pool's
# history in it -- a floor that both sides of that pass has to sit under 0.57,
# by which point it is under HALF the page and catches nothing. And it would
# keep falling. A check that has to be lowered every year is a check that is
# eventually lowered past the thing it was watching for.
#
# So the invariant is restated in the terms that are now true: the public page
# does not grow, therefore it has a SIZE, and the size is 386 KB empty against
# 390 KB full. A floor in bytes says what the ratio used to say, does not age,
# and is not fooled by the private half getting bigger.
#
# The over-greedy excision this replaced is now caught where it happens rather
# than by its shadow: every private panel is cut at its own end marker and the
# build refuses if the marker is not found exactly once. See the strip loop.
PUBLIC_MIN_BYTES = 300_000

def build(public=False, out_name=None):
    """Render the page, one render at a time. See _build() for what it renders.

    ONE RENDER AT A TIME, BECAUSE THE RENDER READS SHARED GLOBALS.

    _build() begins by calling reload(), which REBINDS this module's CFG/DATA/
    SITE/POOL (and panels' and chemistry's copies of the same figures). The
    server renders from ThreadingTCPServer daemon threads, after every write and
    three times per agent sample, so two renders overlap routinely — and when
    they do, thread B's reload() swaps the volume out from under thread A
    halfway down its page. The result was ONE served admin.html quoting the
    Chemicals dose arithmetic against 31,337 gallons and the Settings and Pool
    chemistry panels against 47,521: "two tabs must not be able to disagree
    about what the current alkalinity is", broken inside a single file.
    replace_atomically() does not help — it makes the file SWAP atomic and says
    nothing about the content that was assembled into it.

    The cheapest correct fix is the one the CSV writers already use: rendering
    is a read-modify-write of process-wide state, so it takes the same kind of
    exclusive lock. `render` is its own lock name, so it never contends with a
    dose being appended; the critical section is a whole render rather than a
    few milliseconds, hence the longer timeout.
    """
    with locking.exclusive(config.data_dir(), "render", timeout=60.0):
        return _build(public=public, out_name=out_name)

def _build(public=False, out_name=None):
    """Render the page. Two variants rather than one page with things hidden.

    Client-side hiding is not a security boundary: the markup is still in the
    file, and "the tab is not shown" is one view-source away from "here is the
    internal address of the pool controller". So the public build never contains
    the private panels at all, and the proxy protects the path that serves the
    other one. A misconfigured rule then fails by showing too little rather than
    too much.
    """
    # Set before anything renders: data_table() reads it to decide whether to
    # offer a control that calls a route the public page may not call.
    global _PUBLIC
    _PUBLIC = public

    # Pick up a configuration that changed since this process started. The server
    # renders in-process straight after writing config.toml, so without this the
    # page it hands back is drawn from the values it booted with — the save
    # succeeds and the screen denies it. panels and chemistry hold their own
    # copies of the same figures and are reloaded alongside; all three are
    # imported by then, and reload() is cheap next to the render itself.
    #
    # ALL THREE FROM ONE CONFIG. They are three separate config.load() calls,
    # and a Settings save landing between them gave panels one volume and
    # chemistry another inside a single page — the Chemicals tab estimating a
    # dose against 31,337 gallons while Pool chemistry computed against 47,521.
    # The render lock in build() stops two RENDERS interleaving; it says nothing
    # about a WRITE landing mid-fan-out, so the fan-out is done under the same
    # "config" lock server.edit_toml() takes to write the file. It is held for
    # three file reads, not for the render.
    from . import chemistry as _chem, panels as _panels
    with contextlib.ExitStack() as held:
        try:
            held.enter_context(locking.exclusive(_config_dir(), "config", timeout=30.0))
        except RuntimeError:
            # Already held by this thread — which is the guarantee we came for,
            # given. Taking it twice would block on ourselves until the timeout.
            pass
        reload()
        _panels.reload()
        _chem.reload()

    samples  = rows("samples.csv")
    # Corrections come from rows() itself now, so a dropped reading is gone from
    # the tiles, the charts, the targets, the tables, Pool chemistry, Settings
    # AND the notifier alike. Applying them per-caller guaranteed that one of
    # them eventually forgot, and four of the five had.
    readings = rows("readings.csv")
    lab      = rows("lab.csv")          # WaterGuru — mailed-in sample
    leslies  = rows("leslies.csv")      # Leslie's — bench photometer
    manual   = rows("manual.csv")       # entered by hand: a kit, strips, another store
    chems    = rows("chemicals.csv")

    # Newest MEASURED, not last appended. These were [-1], which is file order —
    # so any out-of-order append reordered reality, including the
    # `bin/wg-collect --from-file` workflow CLAUDE.md itself prescribes for
    # development. It made Home announce "last reading 4 days ago" and show a
    # stale "Free Chlorine high" over the current, correct reading.
    latest    = newest(readings)
    wg_lab    = newest(lab)
    photo     = newest(leslies)
    byhand    = newest(manual)
    LAB       = best_lab((wg_lab, "WaterGuru lab"), (photo, "Leslie's"),
                         (byhand, "By hand"))
    # pH and free chlorine, reconciled across every source that reports them --
    # the pod included, so a pod reading daily still wins on recency. Without
    # this the only door for these two was readings.csv, and an install without
    # a WaterGuru subscription got "unknown" on the two tiles that drive most of
    # what this product is for.
    DAILY     = best_lab((latest, "WaterGuru pod"), (wg_lab, "WaterGuru lab"),
                         (photo, "Leslie's"), (byhand, "By hand"),
                         measures=DAILY_MEASURES)
    HIST      = lab_history((lab, "WaterGuru lab"), (leslies, "Leslie's"),
                            (manual, "By hand"))
    # Order the pair by date so the comparison reads older -> newer, which is
    # what makes "did the pool change?" the natural question instead of "which
    # one is wrong?".
    pair = sorted([(wg_lab, "WaterGuru lab"), (photo, "Leslie's")],
                  key=lambda s: s[0].get("measured", ""))
    DISAGREE  = disagreements(pair[0][0], pair[1][0])
    TG        = targets(LAB, POOL.get("sanitiser"), photo.get("sanitizer", ""))
    labcharts = "".join(
        lab_chart(m, HIST[m], TG.get(m), "{:.2f}" if m in ("copper", "iron", "saturation_index") else "{:.0f}")
        for m in ("ta", "ch", "cya", "salt", "phosphates", "copper")
        if HIST.get(m))
    days     = by_day(samples, chems, readings)

    fc_state, _ = verdict((DAILY.get("free_cl") or {}).get("value"), TG["free_cl"])
    ph_state, _ = verdict((DAILY.get("ph") or {}).get("value"), TG["ph"])
    overall = (latest.get("status") or "unknown").lower()
    # WaterGuru reports GREEN / YELLOW / RED. Printing that verbatim put a
    # COLOUR NAME where a status word belongs: a badge reading "yellow" in
    # amber says the same thing twice and means neither. Worse, it is the one
    # element on the page that is genuinely colour-alone — the word adds no
    # information a colourblind reader could use.
    OVERALL_WORD = {"green": "all good", "yellow": "needs attention",
                    "red": "act now", "unknown": "no reading"}
    overall_word = OVERALL_WORD.get(overall, overall)

    # -- header ---------------------------------------------------------------
    # "LAST READING" HAD ONE NUMBER AND TWO SOURCES BEHIND IT, AND IT NAMED
    # NEITHER.
    #
    # This read the newest WaterGuru POD measurement and printed it, unqualified,
    # at the top of every authenticated page. The pod is pulled once a day, so a
    # perfectly healthy pool says "last reading 19 h ago" — while the Pi's agent
    # had delivered a panel sample two minutes earlier.
    #
    # MEASURED: the live page said 19 hours against a newest sample of 07:58:23,
    # rendered 07:58:26. The owner read it as the agent having stopped, which is
    # the only sensible reading of an unqualified nineteen hours on a product
    # whose own empty state promises a sample "every fifteen minutes".
    #
    # So the line names both, because there are two streams and one figure
    # cannot answer for them: the panel every fifteen minutes, the chemistry
    # once a day. Printing the fresher of the two would be worse than the bug —
    # it would hide a dead agent behind a lab result.
    #
    # ago() rather than this function's own arithmetic. It had a second spelling
    # of "how old is this" (`f"{hrs:.0f} h ago"`) sitting four lines from the
    # loader that owns it, and the two disagreed in wording on every page:
    # "19 h ago" in the masthead against "19 hours ago" in the tile below it.
    panel_age = ago(newest(samples, "ts").get("ts")) if samples else ""
    pod_age = ago(latest.get("measured")) if latest.get("measured") else ""
    age = " · ".join(p for p in (f"panel {panel_age}" if panel_age else "",
                                 f"pod {pod_age}" if pod_age else "") if p)

    # -- tiles ----------------------------------------------------------------
    cya = LAB.get("cya", {}).get("value")
    cya_age = ago(LAB.get("cya", {}).get("measured", "")) if LAB.get("cya") else ""
    pod_src = f"WaterGuru pod · {ago(latest.get('measured'))}" if latest else ""

    # WaterGuru publishes its own acceptable range for free chlorine, and it is
    # wider than the one derived here. Where the reading falls between the two,
    # the page was saying "high" while the vendor had stopped alerting — which
    # reads as the page being broken rather than as two defensible opinions. So
    # the vendor's range is put on the tile whenever they disagree.
    fc_note = f"target scales with CYA {cya:.0f}" if cya else "no CYA on file"
    wg_t = rows("wg_targets.csv")
    fc_now = (DAILY.get("free_cl") or {}).get("value")
    if wg_t and fc_now is not None:
        try:
            import json as _json
            raw = sorted(os.listdir(os.path.join(DATA, "raw")))
            wgf = [f for f in raw if f.startswith("wg-")]
            if wgf:
                d = _json.load(open(os.path.join(DATA, "raw", wgf[-1])))
                for m in d["waterBodies"][0]["measurements"]:
                    if m.get("type") == "FREE_CL":
                        fr = m["cfg"]["floatRanges"]
                        lo, hi = fr["GREEN_MIN"], fr["GREEN_MAX"]
                        if fc_now > TG["free_cl"][2] and fc_now <= hi:
                            fc_note = (f"above the {TG['free_cl'][2]:.1f} this pool needs, "
                                       f"still inside WaterGuru's {lo:g}–{hi:g}")
                        break
        except Exception:
            pass
    tiles = [
        # No src= override: tile() derives the source and its age from the
        # reconciled entry, so a reading that came from a test kit says so
        # instead of being labelled as the pod's.
        tile("Free chlorine", DAILY.get("free_cl"), " ppm", TG["free_cl"], fc_note,
             help="free chlorine", measure="free_cl"),
        tile("pH", DAILY.get("ph"), "", TG["ph"],
             "acid lowers, cell and aeration raise", "{:.2f}"),
        tile("Salt", LAB.get("salt"), " ppm", TG["salt"], "the cell's raw material", "{:.0f}",
             measure="salt"),
        tile("Alkalinity", LAB.get("ta"), " ppm", TG["ta"], "buffers pH swing", "{:.0f}",
             help="alkalinity", measure="ta"),
        tile("Cyanuric acid", LAB.get("cya"), " ppm", TG["cya"],
             "UV shield for chlorine — it sets the chlorine target", "{:.0f}",
             help="cya"),
        tile("Calcium", LAB.get("ch"), " ppm", TG["ch"], "scale and plaster", "{:.0f}",
             measure="ch"),
    ]

    # -- charts ---------------------------------------------------------------
    # EVERY SOURCE THAT REPORTS THIS MEASURE, not just the two that used to fit.
    # The pod is the line; Leslie's and anything typed by hand are marks on the
    # same axis, never joined to it. by-hand readings were accepted, validated,
    # stored and audited, and drawn nowhere near these two.
    def _trend(measure, band, fmt_, unit_, title_):
        def build(since):
            def take(rowset):
                return [(r.get("measured"), num(r.get(measure))) for r in rowset
                        if not since or (when(r.get("measured"))
                                         and when(r.get("measured")) >= since)]
            pod = take(readings)
            ex = [(take(leslies), "Leslie's", "s-les"),
                  (take(manual), "By hand", "s-man")]
            if not any(v is not None for _, v in pod) and \
               not any(v is not None for ser, _, _ in ex for _, v in ser):
                return None          # nothing in this window; do not offer it
            return line_chart(pod, band, fmt_, unit_, title=title_, extra=ex)
        return ranged_chart(build)

    fc_chart = _trend("free_cl", TG["free_cl"], "{:.1f}", " ppm",
                      "Free chlorine over time")
    ph_chart = _trend("ph", TG["ph"], "{:.2f}", "", "pH over time")

    # -- day rows -------------------------------------------------------------
    # Bounded at 30, and it SAYS SO. This is the same silent truncation
    # data_table() exists to prevent — 92 days on file and 30 drawn, with
    # nothing on the page admitting the other 62 were there. A reader cannot ask
    # for what they cannot see is missing, and the rule was written for the
    # tables and never applied to the strip beside them.
    DAY_WINDOW = 30
    shown_days, total_days = days[:DAY_WINDOW], len(days)

    # WHICH DAYS THE DRILL-DOWN CAN ACTUALLY REACH.
    #
    # Every day row used to be a button labelled "Show the samples for 7
    # September", and clicking it filtered the samples table to that date. But
    # the samples table renders the newest MAX_RENDER rows, which at one sample
    # per fifteen minutes is about four days — so twenty-five of the thirty days
    # offered filtered the table to nothing and landed the reader on "Nothing
    # matches that filter". A control advertised on thirty days worked on five.
    #
    # Raising the cap to cover the window would mean 2,880 sample rows in the
    # page, which is the page-weight problem the cap exists for. So the promise
    # is made to match the reach instead: the days the table holds are buttons,
    # the rest are rows, and the strip says where the line falls. A row that
    # cannot deliver is not a button.
    #
    # Same slice data_table() will render: reversed(samples) capped, which is
    # the last MAX_RENDER rows of the file.
    drillable = {(s.get("ts") or "")[:10] for s in list(reversed(samples))[:MAX_RENDER]}
    drillable.discard("")

    rowhtml = []
    for d in shown_days:
        date = dt.date.fromisoformat(d["date"])
        r = d["reading"] or {}
        chips = []
        for key, lbl, f, band in (("free_cl", "FC", "{:.1f}", TG["free_cl"]),
                                  ("ph", "pH", "{:.2f}", TG["ph"])):
            v = num(r.get(key))
            st, _ = verdict(v, band)
            chips.append(f'<span class="chip c-{st}"><b>{lbl}</b>{f.format(v) if v is not None else "—"}</span>'
                         if v is not None else f'<span class="chip c-none"><b>{lbl}</b>—</span>')
        h = d["hours"]
        can_drill = d["date"] in drillable
        drill_attrs = (f' role="button" aria-label="Show the samples for {date:%-d %B}"'
                       if can_drill else
                       f' aria-label="{date:%-d %B} — its samples are older than '
                       f'the rows in the table below"')
        rowhtml.append(f'''<div class="drow{"" if can_drill else " drow-far"}" data-date="{d["date"]}"{drill_attrs}>
      <div class="dwhen"><span class="dd">{date.strftime("%a")}</span><span class="dm">{date.strftime("%-d %b")}</span></div>
      <div class="dstrip">{strip(d)}</div>
      <div class="dmeta">
        <span class="run" title="pump runtime"><i style="background:{C_PUMP}"></i>{h["pump"]:.1f}h</span>
        <span class="run" title="spa runtime"><i style="background:{C_SPA}"></i>{h["spa"]:.1f}h</span>
        <span class="run" title="sheer descent runtime"><i style="background:{C_SHEER}"></i>{h["sheer"]:.1f}h</span>
        <!-- No colour swatch. The series palette is validated as a SET -- three
             colours, checked for contrast on both surfaces and for separation
             under three kinds of colour vision deficiency -- and adding a
             fourth in passing would invalidate that check. Heater hours are
             text, which is also the non-colour channel the other three lanes
             are required to carry anyway. -->
        <span class="run" title="pool heater runtime">heater {h["heat"]:.1f}h</span>
        <!-- by_day() has computed a daily mean water temperature and a daily
             mean salt reading for every day in the history since it was
             written, and NOTHING displayed either. Meanwhile the Salt tile
             showed a lab figure from a different instrument while the cell had
             been measuring salt continuously the whole time. A computed value
             with no reader is how the next person concludes the feature
             exists. -->
        <span class="swg">{" · ".join(x for x in (
            f'cell {d["swg"]:.0f}%' if d["swg"] is not None else "",
            f'water {d["temp"]:.0f}\u00b0' if d["temp"] is not None else "",
            f'spa {d["spa_temp"]:.0f}\u00b0' if d["spa_temp"] is not None else "",
            f'salt {d["salt"]:.0f}' if d["salt"] is not None else "") if x)}</span>
      </div>
      <div class="dchips">{"".join(chips)}</div>
    </div>''')
    if not rowhtml:
        rowhtml = ['<p class="empty">No equipment samples yet — the sampler writes one every 15 minutes.</p>']
    else:
        say = []
        if total_days > len(shown_days):
            first = dt.date.fromisoformat(days[len(shown_days) - 1]["date"])
            oldest = dt.date.fromisoformat(days[-1]["date"])
            # "and in the tables below" was the second half of this sentence and
            # it was not true: the samples table holds the newest few hundred
            # rows, which is four days, not thirty. The CSV is where the rest is.
            say.append(
                f'Showing the most recent {len(shown_days)} of {total_days} days, '
                f'back to {first:%-d %b}. {total_days - len(shown_days)} earlier '
                f'days — to {oldest:%-d %b} — are on file, in samples.csv.')
        n_drill = sum(1 for d in shown_days if d["date"] in drillable)
        if n_drill and n_drill < len(shown_days):
            edge = dt.date.fromisoformat(
                min(d["date"] for d in shown_days if d["date"] in drillable))
            say.append(
                f'The {n_drill} day{"" if n_drill == 1 else "s"} back to '
                f'{edge:%-d %b} can be opened in the samples table below — that '
                f'is as far back as the {MAX_RENDER:,} rendered rows reach. '
                f'Earlier rows are in the CSV rather than in this page, so those '
                f'days are shown here but do not open.')
        if say:
            rowhtml.append(f'<p class="dt-note">{" ".join(say)}</p>')

    # -- what to do next ------------------------------------------------------
    HEALTH = collector_health(samples, readings, leslies)
    TRAJ = trajectory(readings, samples, "free_cl")
    ACTS = actions(LAB, latest, TG, chems, HEALTH,
                   config.volume(CFG),
                   float(POOL.get("acid_pct", 31.45)), traj=TRAJ, DAILY=DAILY,
                   public=public)
    # What keeping it balanced takes, by the week and the year. Private: it is
    # this household's consumption, read off this household's record.
    from . import upkeep as _upkeep
    UPKEEP = None if public else _upkeep.estimate(
        days, samples, chems, readings, config.volume(CFG), current_ta(),
        float(POOL.get("acid_pct", 31.45)), (TG.get("cya") or (None,) * 3)[1],
        dt.datetime.now(), when, num,
        prices={k: config.chemical_price(k, CFG) for k in config.PRICE_SETTINGS})
    PRI = {"do": ("Do this", "act-do"), "fix": ("Fix", "act-fix"),
           "watch": ("Keep an eye on", "act-watch")}
    # The badge counts only what is actionable now. Counting "keep an eye on"
    # items too would make the number permanent, and a badge that is always lit
    # is a badge nobody reads.
    n_now = len([a for a in ACTS if a["pri"] in ("do", "fix")])
    badge = (f'<span class="badge" aria-label="{n_now} things to do">{n_now}</span>'
             if n_now else "")
    if ACTS:
        # A RANKED LIST OF POURS IS A QUEUE, NOT A TO-DO LIST.
        #
        # The page states its own governing rule twice — one change at a time,
        # give it a week — and then offered two "Do this" cards, each with a
        # dose, with nothing between them saying which comes first or that the
        # second should wait. A reader who is not the author does both on
        # Saturday, and by this page's own argument neither can then be
        # credited with whatever happens. The chemistry tab has the right answer
        # ("Calcium first, then pH") and Home did not carry it.
        #
        # The ordering is already by consequence, so the first item that pours
        # something IS the one to do; every later pour is labelled as next and
        # says what it is waiting on. Items with no dose — a stale collector, a
        # cassette to order, a volume nobody has set — are not water changes and
        # are not queued behind one.
        poured = 0
        rows_ = []
        for a in ACTS:
            tag, cls = PRI[a["pri"]]
            queued = ""
            if a["dose"]:
                poured += 1
                if poured > 1:
                    tag = "Then, once that has settled"
                    queued = ('<p class="a-n">Wait for the change above to finish '
                              'before adding this. Two adjustments in flight at once '
                              'cannot be told apart afterwards, and the usual result '
                              'is overshooting in the other direction.</p>')
            rows_.append(
                f'<li class="{cls}">'
                f'<div class="a-h"><span class="a-tag">{html.escape(tag)}</span>'
                f'<h3>{html.escape(a["title"])}</h3></div>'
                f'<p class="a-d">{a["detail"]}</p>'
                + (f'<p class="a-dose"><b>{html.escape(a["dose"])}</b></p>' if a["dose"] else "")
                + queued
                + (f'<p class="a-n">{html.escape(a["note"])}</p>' if a["note"] else "")
                # Where the number came from and how old it is. Prose that states
                # a figure goes stale; a prescription that states one without
                # saying when it was measured goes stale silently.
                + (f'<p class="a-src">from {html.escape(a["src"])}</p>'
                   if a.get("src") else "")
                + '</li>')
        acts_block = f'<ol class="actions">{"".join(rows_)}</ol>'
    else:
        acts_block = ('<div class="card"><p class="empty">Nothing needs doing. Every '
                      'measure is inside its range and every source has reported '
                      'recently.</p></div>')

    # -- where the two instruments disagree -----------------------------------
    # Ordered by relative gap, because the ordering IS the finding: it says which
    # measures actually moved between the two samples and which held steady.
    if DISAGREE:
        cmp_rows = "".join(
            f'<tr><td>{html.escape(MEASURE_NAMES.get(d["m"], d["m"]))}</td>'
            f'<td class="n">{d["a"]:g}</td>'
            f'<td class="n">{d["b"]:g}</td>'
            f'<td class="n gap-{"wide" if d["material"] else "ok"}">'
            f'{d["diff"]:+g}<span class="rel">{d["rel"]*100:.0f}%</span></td>'
            f'<td>{"moved, or the labs read differently" if d["material"] else ("too small to read into" if d["rel"] > 0.2 else "steady")}</td>'
            f'</tr>' for d in DISAGREE)
        material = [d for d in DISAGREE if d["material"]]
        gap_days = ""
        d0, d1 = when(pair[0][0].get("measured")), when(pair[1][0].get("measured"))
        if d0 and d1: gap_days = f"{abs((d1 - d0).days)} days"
        if material:
            names = ", ".join(MEASURE_NAMES.get(d["m"], d["m"]).lower() for d in material)
            verdict_line = (f'Over the {gap_days} between them, <b>{html.escape(names)}</b> '
                            f'moved by more than that. Whether the pool changed or the labs '
                            f'differ is not decidable from this table — the chemical log is '
                            f'what settles it, since one dose of acid takes this pool\'s '
                            f'alkalinity down by about that much.')
        else:
            verdict_line = (f'Nothing moved materially in the {gap_days} between them, which '
                            f'is the strongest evidence available that both labs are '
                            f'measuring the same pool.')
        cmp_html = f'''<div class="card">
      <div class="scroll"><table>
        <thead><tr><th>Measure</th>
        <th>{html.escape(pair[0][1])}<br><span class="th2">{html.escape(pair[0][0].get("measured","")[:10])}</span></th>
        <th>{html.escape(pair[1][1])}<br><span class="th2">{html.escape(pair[1][0].get("measured","")[:10])}</span></th>
        <th>Change</th><th></th></tr></thead>
        <tbody>{cmp_rows}</tbody></table></div>
      <p class="sub" style="margin:14px 0 0">Neither column is averaged into the
      other — a mean would erase the only thing this table is for. A gap counts as
      real only when it is both proportionally large and big enough in absolute
      terms to change what you would do; copper at 0.1 against 0.2 is half the
      value and still the same conclusion. {verdict_line}</p>
    </div>'''
    else:
        cmp_html = ('<p class="empty">Only one lab source so far — nothing to '
                    'cross-check against yet.</p>')

    # -- model readiness ------------------------------------------------------
    # WHAT A PAIRED DAY IS, COUNTED ONCE.
    #
    # Three things were wrong in one sentence. It counted CSV ROWS, and both
    # labs return their whole history on every pull — nine rows for six
    # readings, a 50% overstatement, with collect_wg.py carrying a comment
    # saying exactly this. It counted READINGS while promising PAIRED DAYS, and
    # printed the day count beside them, so the binding constraint was on screen
    # and not in the arithmetic. And the countdown ran on the reading count, so
    # the page and the Pool chemistry tab stated two different numbers for the
    # same quantity.
    #
    # A paired day is a day that has BOTH a chemistry reading and equipment
    # runtime, which is what fitting a coefficient actually needs. Distinct
    # measurement timestamps, the same dedup key the collectors use.
    # [:16] is the key chemistry.what_the_data_supports() and trajectory()
    # already dedup on, so all three agree on how many readings there are.
    n_read = len({(r.get("measured") or "")[:16] for r in readings if r.get("measured")})
    n_days = len(days)
    paired = [d for d in days if d["reading"] and sum(d["hours"].values()) > 0]
    n_pair = len(paired)
    ready = n_pair >= 30
    model = (f'<p><b>{n_read}</b> chemistry reading{"s" if n_read != 1 else ""} against '
             f'<b>{n_days}</b> day{"s" if n_days != 1 else ""} of equipment runtime, '
             f'which pair up on <b>{n_pair}</b> day{"s" if n_pair != 1 else ""}. '
             + ("Enough to fit the coefficients — see THEORY.md."
                if ready else
                f"Fitting the pH and FC coefficients needs about 30 paired days; "
                f"{30 - n_pair} more to go. Until then this page is a "
                f"record, not a model.") + "</p>")

    # -- raw tables (the non-colour route to every number on this page) --------
    # Only the authenticated build carries download links. The route is gated
    # anyway, so a link in the public file would merely be a 401 the reader
    # cannot act on — and the public build's rule is that private things are NOT
    # IN THE FILE rather than hidden in it.
    def src(name):
        return None if public else name

    tables = (data_table("WaterGuru pod readings", readings,
                    ["measured", "free_cl", "ph", "water_temp", "skimmer_flow", "status", "alerts"],
                    source=src("readings.csv"))
              + data_table("WaterGuru lab (mailed-in sample)", lab,
                      ["measured", "ta", "ch", "cya", "salt", "phosphates",
                       "copper", "iron", "saturation_index"], source=src("lab.csv"))
              + data_table("Leslie's in-store tests", leslies,
                      ["measured", "score", "free_cl", "total_cl", "ph", "ta", "ch",
                       "cya", "iron", "copper", "phosphates", "salt", "issues"],
                      source=src("leslies.csv"))
              # EVERY column the form accepts. Five of the eleven measures it
              # stores -- total_cl, phosphates, copper, iron, saturation_index --
              # were absent here, and the row-detail dialog reads its field names
              # off this header, so it inherited the same blind spot. Somebody
              # who typed a saturation index had no way to confirm it was kept
              # short of the export. data_table already scrolls wide tables.
              + data_table("Entered by hand", manual,
                      ["measured", "source", "ph", "free_cl", "total_cl", "ta",
                       "ch", "cya", "salt", "phosphates", "copper", "iron",
                       "saturation_index", "note", "by"], source=src("manual.csv"),
                      # WHERE THEY WERE RECORDED IS ONLY SAYABLE WHERE THAT
                      # SCREEN EXISTS. This named the Settings tab
                      # unconditionally, and this table is in BOTH builds — so
                      # the public-prose assertion added a few commits ago
                      # refused the render outright the moment manual.csv held
                      # a single row. Recording one hand reading through the UI
                      # broke bin/render, and with it cron's six-hourly render.
                      # Latent only because this household has no manual.csv.
                      #
                      # The assertion was right and the prose was wrong, which
                      # is the direction to prefer — but a build that fails on
                      # ordinary data is still a defect, and this one was found
                      # by two review lanes hitting it while doing other work
                      # rather than by anything here.
                      note=("Test-kit and strip results"
                            + ("" if public else " recorded on the Settings tab")
                            + ". Reconciled with the pod and both labs the same "
                            "way they are with each other: most recent wins per "
                            "measure, never averaged. That now includes pH and "
                            "free chlorine, which used to reach the page only "
                            "from a WaterGuru pod."))
              # The five equipment columns that had no historical view anywhere:
              # not the day strip, not this table, and so not the row detail
              # either. The notifier mails about every heater edge and there was
              # nowhere in the product to look up when the heater ran.
              + data_table("AqualinkD controller samples", samples,
                      ["ts", "pump", "pump_rpm", "pump_watts", "swg_pct", "salt_ppm",
                       "pool_temp", "air_temp", "spa", "sheer",
                       "pool_light", "spa_light", "pool_heat", "spa_heat",
                       "solar_valve", "freeze", "pool_set", "spa_set", "freeze_set"],
                      source=src("samples.csv"),
                      note="One row every fifteen minutes, so this is the table that "
                           "grows. Filtering and sorting act on the rows in the page, "
                           "which is the most recent window rather than the whole file "
                           "\u2014 a date older than that is in the CSV, not here. "
                           "Click any row to see it in full; click a day in "
                           "\u201cWhat the pool did\u201d to jump to that day, if "
                           "that day is one of the ones this window reaches."))

    alerts = latest.get("alerts") or ""
    alert_html = (f'<div class="alerts" style="margin:14px 0 0">'
                  f'<b>WaterGuru, in its own words</b> {html.escape(alerts)}</div>'
                  if alerts else "")

    from . import chemistry, panels     # imported here: both read render's loaders
    from . import collection as _collection
    from . import info as _info
    from . import policies as _policies
    from . import assistant as _assistant

    # Shown to a reader whose role is `view`, on the three tabs that can act.
    # Rendered for everybody and revealed by the page script, because the page
    # is one file and cannot be built per person. Hidden means hidden, not
    # absent: without a sentence saying why, a screen of greyed-out controls
    # reads as a broken product rather than a deliberate permission.
    #
    # THE LEVEL IS WRITTEN BY THE PAGE SCRIPT, not baked in here. This sentence
    # used to read "You are signed in with view-only access" and was revealed to
    # everybody below admin -- so an `operate` reader saw it on a Pool control
    # tab whose every control was enabled for them. The empty span is filled in
    # once /api/health has said which level this actually is.
    ROLE_NOTE = (
        '<div class="v-warn role-note" hidden style="margin-bottom:14px">'
        + icon("alert", 15)
        + ' <b>You are signed in with <span class="role-note-what">limited '
        'access</span></b> The controls on this tab are shown so you can see '
        'what the pool can do, and the ones you may not use are disabled. '
        'Whoever administers this install can change that in Settings.</div>')

    def wrap(html_, pid, role_note=False):
        body, nav = with_subnav(html_, pid)
        return nav + (ROLE_NOTE if role_note else "") + body

    page = fill(TEMPLATE,
        css=style.CSS, extra_css=chemistry.EXTRA_CSS,
        chemistry_panel=wrap(chemistry.panel(public=public), "chem"),
        control_panel=wrap(panels.control_panel(), "ctl", role_note=True),
        help_panel=wrap(helpdoc.help_panel(public=public), "help"),
        volume_panel=wrap(panels.volume_panel(public=public), "vol"),
        # The public page had no way in at all: the control tab is simply absent,
        # so the only route to it was knowing to type /admin.html. Somebody who
        # owns the pool should not have to be told the URL of their own
        # dashboard. On the authenticated build this becomes the identity the
        # proxy asserted, which is also the quickest way to see that sign-in is
        # actually working rather than silently letting everyone through.
        signin=('<a class="signin" href="/admin.html" rel="nofollow">'
                + icon("plug", 14) + '<span>Sign in</span></a>') if public else
               ('<span class="whobox" id="whobox" hidden>'
                '<button type="button" class="whoami" id="whoami" aria-expanded="false" '
                'aria-haspopup="true" aria-controls="whomenu"></button>'
                '<span class="whomenu" id="whomenu" hidden role="menu">'
                # No URL means no entry. An anchor with href="" reloads the
                # current page, so a menu item reading "Sign out" that silently
                # does not is worse than the absence of one.
                + (f'<a class="signout" role="menuitem" rel="nofollow" '
                   f'href="{_signout_url()}">Sign out</a>' if _signout_url() else
                   '<span class="signout dim" role="menuitem">Sign out is not '
                   'configured \u2014 set [site] proxy_host and host</span>')
                + '</span></span>'),
        icon_volume=icon("shapes", 15),
        # Both filled from [site]; with nothing configured the notice says
        # "signed out of poolhound" and stops, rather than naming a domain the
        # reader has never heard of.
        signout_scope=(f" and the other {_signout_domain()} sites"
                       if _signout_domain() else ""),
        signout_return=(f"?post_logout_redirect_uri=https%3A%2F%2F{_site('host')}%2F"
                        if _site("host") else ""),
        # THE MASTHEAD SAID HOW THE POOL WAS DOING, ON THE PAGE THAT NO LONGER
        # DOES. Moving Home behind the sign-in was a decision about whose
        # readings these are -- and then the header above it went on telling
        # every anonymous visitor "NEEDS ATTENTION" and "last reading 8 days
        # ago", which is the headline of the dashboard that had just been taken
        # away, printed in larger type. Half a privacy change is a privacy
        # change that reads as one.
        #
        # Emitted as whole elements rather than as blanked-out contents, so the
        # public masthead has no empty pill sitting in it waiting to be noticed.
        # AND IT HAS TO READ AS A SENTENCE IN BOTH STATES. "last reading " was
        # a fixed prefix with the age appended, so an install with nothing in
        # it yet said "last reading no reading yet" — the empty case written as
        # if it were the ordinary one with a hole in it. An install with no
        # readings is the first thing a new deployment sees.
        # NO "last reading " PREFIX ANY MORE. The age now names its own source
        # — "panel 2 minutes ago · pod 19 hours ago" — so the prefix would read
        # "last reading panel 2 minutes ago", and the prefix was the half of
        # the sentence doing the misleading: one source, unqualified, standing
        # for the whole product. The empty case still has to read as a
        # sentence, which is why it is a whole alternative rather than this one
        # with a hole in it: an install with nothing in it yet once said "last
        # reading no reading yet".
        freshness=("" if public else
                   f'<span class="age">'
                   f'{html.escape(age) if age else "no readings yet"}'
                   f'</span>'),
        verdict=("" if public else
                 f'<span class="grade {html.escape(overall)}" '
                 f'title="WaterGuru reports {html.escape(overall.upper())}">'
                 f'{html.escape(overall_word)}</span>'),
        # What this is, and then the three things it does. The same block in
        # both builds — the public page and the signed-in page are the same
        # product.
        #
        # ASSEMBLED FROM info.PURPOSE, not typed here. It names the three
        # problems this exists to solve, and it was typed here once: the
        # original described only the measurement half, leaving the two things
        # a reader is most likely to have come for — reaching the controller,
        # and working out the volume — unmentioned on the page that offers
        # them. That was fixed, and then the landing page's own opening
        # section made the identical cut, because the three were a sentence in
        # this file rather than a fact anything could read. They are now in
        # one place and both readers take them from it.
        masthead=_info.masthead(public),
        page_title=html.escape(_info.page_title(), quote=True),
        description=html.escape(_info.WHAT_IT_IS, quote=True),
        icon_help=icon("book", 15),
        icon_policies=icon("shield", 15),
        policies_panel=wrap(_policies.panel(public=public), "policies"),
        policy_links=_policies.footer_links(),
        chemicals_panel=wrap(panels.chemicals_panel(UPKEEP), "dose", role_note=True),
        collection_panel=wrap(_collection.panel(public=public), "col"),
        info_panel=wrap(_info.panel(public=public), "info"),
        ask_panel=wrap(_assistant.panel(public=public), "ask"),
        # THE MARK, ON THE TAB THAT IS NAMED AFTER IT. One drawing, from
        # brand.py, at the size the other tab glyphs use -- so the landing tab
        # is recognisable as this product rather than as another line icon.
        mark_tab=_brand.mark(15),
        # AND THE FAVICON, WHICH WAS A THIRD DRAWING OF THE MARK. It was a
        # hand-typed drop on its own -- no pool, a colour literal instead of
        # the palette -- sitting in the template where brand.py could not see
        # it. seams.py guards the mark by its POOL path, so a copy carrying
        # only the drop was invisible to the one check that exists to stop
        # exactly this. It is the same drawing as the masthead now, and the
        # seam has a second pattern that would have caught it.
        favicon=_favicon_href(),
        collection_badge=_collection_badge(),
        settings_panel=wrap(panels.settings_panel(), "set", role_note=True),
        catalogue=panels.catalogue_json(),
        glossary=json.dumps(GLOSSARY),
        saved_shape=panels.saved_shape_json(),
        templates=panels.templates_json(),
        refill_units=panels.refill_units_json(),
        spa_templates=panels.spa_templates_json(),
        generated=dt.datetime.now().strftime("%a %-d %b %Y, %H:%M"),
        alert_html=alert_html, tiles="".join(tiles),
        fc_chart=fc_chart, ph_chart=ph_chart,
        fc_state=fc_state, ph_state=ph_state,
        rows="".join(rowhtml), model=model, tables=tables,
        power=power_block(
            energy(days, samples, rated=config.pump_watts(CFG),
                   cost=config.kwh_cost(CFG)),
            config.pump_watts(CFG), config.kwh_cost(CFG),
            traj=TRAJ,
            # The midpoint of this pool's own band, which knows about the salt
            # cell and the cyanuric acid, rather than a generic 3 ppm.
            fc_target=(TG.get("free_cl") or [None, None, None])[1]),
        nowstrip=now_strip(samples),
        cmp_html=cmp_html, trend_range=range_bar(),
        actions=acts_block, badge=badge,
        labcharts=labcharts or '<p class="empty">No lab results yet.</p>',
        c_pump=C_PUMP, c_spa=C_SPA, c_sheer=C_SHEER,
        nsamples=len(samples), source=html.escape(CFG.get("_source", "")),
        # ONE MARK, from brand.py. The masthead used to hand-inline the
        # paths, which is how the product came to have two drawings of
        # its own logo that could drift apart.
        mark=_brand.mark(26),
        version=html.escape(version_string()),
        # The role vocabulary and the controls each level may use, from
        # access.py, so the page and the server cannot disagree about either.
        # json.dumps gives a JS literal that cannot carry an unescaped quote
        # into the script -- the selectors are CSS, which is allowed apostrophes.
        pending_gives_up_ms=int(_CMD.PENDING_GIVES_UP_S * 1000),
        role_levels=json.dumps(list(_access.LEVELS)),
        role_operate=json.dumps(_access.ui_selector("operate")),
        role_admin=json.dumps(_access.ui_selector("admin")),
    )
    # Home's sections live in the template itself, so its nav is inserted after
    # the page is assembled rather than around a pre-built string.
    m = re.search(r'<div class="panel" id="tab-home" role="tabpanel"[^>]*>(.*?)</div><!-- /home -->',
                  page, re.S)
    if m:
        body, nav = with_subnav(m.group(1), "home")
        page = page[:m.start(1)] + nav + body + page[m.end(1):]

    # EVERY GATED ROUTE THE SCRIPT CALLS HAS A CONTROL, OR SAYS IT HAS NONE.
    #
    # The page dims what this reader may not use, from access.ROLE_UI. A route
    # that reaches the script without an entry there is a live button for
    # somebody who will be refused when they press it -- which is how the
    # Refresh control, the lab-correction form and the calculator's two Save
    # buttons each stayed enabled for a `view` reader. Checked against the
    # rendered page rather than against a list, so adding a fetch() is what
    # trips it.
    #
    # Both builds, and the check runs on the full page BEFORE the excision: the
    # public build has the private script cut out of it, so running this after
    # would pass by having nothing left to check.
    _called = set(re.findall(r"fetch\(\s*'(/api/[a-z0-9/-]+)'", page))
    _ungoverned = sorted(r for r in _called
                         if r in _access.NEEDS and r not in _access.ROLE_UI)
    if _ungoverned:
        raise ValueError(
            f"the page script calls {_ungoverned}, which access.NEEDS gates, and "
            f"access.ROLE_UI names no control for. A reader below that level "
            f"sees a live control and gets a 403 on press. Add a selector — or "
            f"an empty string, which says deliberately that there is nothing to "
            f"dim.")

    if public:
        # What the page weighed before anything was cut out of it, for the size
        # floor below. Measured here rather than guessed, so the invariant is
        # about THIS render rather than about a number somebody typed in 2026.
        size_before = len(page)
        # Remove the private panels and their tabs outright, then assert they are
        # gone. An assertion rather than a comment because this is the line
        # between "chemistry nerds can look" and "anybody can read my address and
        # turn on my heater", and it should fail loudly rather than drift.
        # Anything marked private is operator detail that happens to sit
        # outside a panel — the config path in the footer, the refresh control
        # that spends API calls. Stripped by class rather than by listing every
        # one, so a future addition is covered by marking it rather than by
        # remembering to update a regex here.
        page = strip_elements(page, r'<span[^>]*\sclass="[^"]*\bpriv\b[^"]*"[^>]*>', "span")
        page = strip_elements(page, r'<div[^>]*\sclass="[^"]*\bpriv\b[^"]*"[^>]*>', "div")
        page = strip_elements(page, r'<div class="refresh-wrap">', "div")
        # Script is stripped by the same principle as markup. A control handler
        # that survives into the public page is dead code rather than a hole —
        # it finds no buttons and returns — but it still reads as an oversight
        # to anybody who views source, and the next one might not be inert.
        page = re.sub(r"/\* priv:start \*/.*?/\* priv:end \*/", "", page, flags=re.S)

        # EVERY TAB IS CLASSIFIED, OR THE BUILD STOPS.
        #
        # The excision below is a DENYLIST: it removes the three tabs named in
        # PRIVATE_TABS and leaves everything else standing. So a tab added to
        # the template is public by default, and is public SILENTLY -- it is in
        # neither tuple, no check here mentions it, and the leak list only
        # catches it if its markup happens to contain a route or an address.
        # That is the wrong default for this project: the entire reason the
        # builds are two files rather than one page with things hidden is that
        # a mistake should "fail by showing too little". A denylist fails by
        # showing too much.
        #
        # Deriving the private set from the tabs that are actually present
        # instead -- everything not declared public -- would close it, but
        # silently in the other direction: a new private tab would vanish from
        # the public page with nobody told it had been classified. So the build
        # refuses to guess. A tab goes in one tuple or the other, deliberately.
        seen = declared_tabs(page)
        unclassified = sorted(seen - set(PUBLIC_TABS) - set(PRIVATE_TABS))
        if unclassified:
            raise ValueError(
                f"tab(s) {unclassified} are in the page and in neither PUBLIC_TABS "
                f"nor PRIVATE_TABS. The public build strips what PRIVATE_TABS "
                f"names and keeps the rest, so an unclassified tab ships to "
                f"anonymous visitors. Add it to one of the two tuples in "
                f"render.py -- whichever is right -- rather than letting the "
                f"default decide.")
        # And the other direction: a tuple naming a tab the page does not have
        # is a rule protecting nothing, which is how a renamed tab becomes an
        # exposure. Checked against the authenticated page, where all of them
        # are still present.
        declared = set(PUBLIC_TABS) | set(PRIVATE_TABS)
        if declared - seen:
            raise ValueError(
                f"PUBLIC_TABS/PRIVATE_TABS name tab(s) {sorted(declared - seen)} "
                f"that are not in the page. If one was renamed, the tuple still "
                f"guards the old spelling and the new one falls through to "
                f"public.")

        # EVERY PANEL IS CUT AT ITS OWN END MARKER, and nothing is cut any
        # other way. There used to be a fallback here that ran to "the next
        # panel or the end of <main>" for the four panels that carried no
        # terminator -- and a lazy match with a lookahead is exactly the
        # pattern that removes one element too many when the markup moves,
        # which is the failure the size check downstream was invented to
        # notice AFTER the fact. All nine panels now carry `<!-- /tab -->`,
        # the delimited form is the only form, and a panel whose marker is
        # missing stops the build instead of being swept up by its neighbour.
        for tab in PRIVATE_TABS:
            page, n = re.subn(
                rf'<div class="panel" id="tab-{tab}".*?</div><!-- /{tab} -->\n?',
                "", page, flags=re.S)
            if n != 1:
                raise ValueError(
                    f"the {tab} panel was matched {n} time(s) by its end marker "
                    f"`<!-- /{tab} -->`. A private panel that cannot be cut "
                    f"exactly is a private panel that ships, or a cut that runs "
                    f"into the next one.")
            page, n = re.subn(rf'<button role="tab" data-tab="{tab}".*?</button>\n?',
                              "", page, flags=re.S)
            if n != 1:
                raise ValueError(
                    f"the {tab} nav button was matched {n} time(s); its panel is "
                    f"gone and the button that reaches it is not.")
        # The generic patterns live here; the household-specific ones come from
        # config.toml, which is not in this repository.
        #
        # The street name used to be on this list, which meant the check that
        # existed to keep the address out of the public page was itself
        # publishing it to everybody who read the source. A guard that has to
        # name the secret cannot live in the open — hence never_publish.
        # Every endpoint that writes, reads a credential, or spends an API call.
        # The markup for these panels was already stripped, but the SCRIPT that
        # drives them was not, and the assertion did not name them — so nine
        # write endpoints sat in the public page, inert but legible to anyone
        # who viewed source. Listing them is what stops that recurring.
        #
        # DERIVED, NOT TYPED. This list was hand-written, and a hand-written
        # list of "every private route" is wrong the moment somebody adds one:
        # /api/export — admin-only, hands out the whole history — was never on
        # it, so a fetch of it in the public script passed the build. The routes
        # are already enumerated, once, in access.NEEDS, which is the file that
        # decides who may call them; taking the list from there means a new
        # route is covered by existing rather than by remembering. A route
        # missing from NEEDS already requires admin, so it fails there instead.
        #
        # A route that is a PREFIX of a public one is matched quoted, so
        # /api/pool-shape cannot match the /api/pool-shape/compute the
        # calculator needs and which stays.
        from . import server as _server
        _private = set(_access.NEEDS) - set(_server.PUBLIC_POST) - {"/api/health"}
        _routes = []
        for r in sorted(_private):
            if any(pub.startswith(r + "/") for pub in _server.PUBLIC_POST):
                _routes.append("'" + re.escape(r) + "'")
            else:
                _routes.append(re.escape(r))
        # The legacy credential FILE PATHS. One of these — ~/.waterguru — shipped
        # in the public build inside a "what to do next" instruction, in the
        # build whose premise is that no token path survives into it. The three
        # are enumerated in vault.SERVICES; taken from there so a fourth service
        # is covered by existing rather than by remembering.
        from . import vault as _vault
        _legacy = [re.escape(str(spec["legacy"]))
                   for spec in _vault.SERVICES.values() if spec.get("legacy")]
        patterns = [r"192\.168\.\d+\.\d+", r"/Users/", r"smtp_credentials",
                    r"agent\.token", r"ctl-circuits", r"ctl-hist",
                    r"priv:start"] + _legacy + _routes
        patterns += [re.escape(str(s)) for s in
                     (CFG.get("site", {}).get("never_publish") or [])]
        # And the other direction. The excision above removes markup and script
        # by pattern, and nothing checked that it stopped where it was meant to:
        # the leak list catches a private thing SURVIVING, and nothing caught
        # public machinery being DESTROYED. A regex that reached one element too
        # far would have taken the calculator with it and still passed every
        # check here, because "the page does not contain a control endpoint" is
        # equally true of a page with nothing on it.
        missing = [m for m in PUBLIC_MUST_SURVIVE if not re.search(m, page)]
        if missing:
            raise ValueError(f"the public build LOST {missing} — the excision took "
                             f"machinery the public page needs; refusing to write it")

        thin = [(pat, n, len(re.findall(pat, page)))
                for pat, n in PUBLIC_MUST_SURVIVE_COUNTS
                if len(re.findall(pat, page)) < n]
        if thin:
            raise ValueError(
                "the public build has been HOLLOWED OUT: " +
                "; ".join(f"{pat} appears {got} times, at least {n} expected"
                          for pat, n, got in thin) +
                ". The tabs are there and their contents are not, which every "
                "other check here reads as success.")

        if len(page) < PUBLIC_MIN_BYTES:
            raise ValueError(
                f"the public build is {len(page):,} bytes, cut from {size_before:,}, "
                f"and the floor is {PUBLIC_MIN_BYTES:,}. The public page is an "
                f"explanation, a chemistry reference, a calculator and a help "
                f"document — none of which grows or shrinks with the pool — so a "
                f"page this small is one the excision took a panel out of. "
                f"Refusing to write it.")

        # A link the public reader cannot follow. Help points at other tabs on
        # purpose -- that was the missing half of "if you do not understand this
        # tab, read Help" -- and _tablink() names a private tab in plain text
        # instead of linking it. That was verified once by hand and then
        # regressed the moment a sentence was added without the helper, which is
        # what an invariant checked by a person rather than by the build always
        # does. Dead tab links now fail the build.
        dead = sorted(set(re.findall(
            r'data-tab-link="(' + "|".join(PRIVATE_TABS) + r')"', page)))
        if dead:
            raise ValueError(
                f"the public build links to private tab(s) {dead} — a reader "
                f"following one lands nowhere. Use help._tablink(..., public), "
                f"which names the tab in plain text instead.")

        # AND PROSE THAT NAMES ONE. The assertion above catches a LINK; six
        # instructions shipped as plain text — "Log it on the Chemicals tab",
        # "Record one on Settings" — telling anonymous readers to use screens
        # that are not in their file. One of them named a credential path.
        # "<Tab> tab" is how an instruction reads, and it is specific enough not
        # to catch the ordinary words: the chemistry prose says "chemicals" and
        # "settings" constantly and means neither screen.
        #
        # Help is exempt: it points at private tabs ON PURPOSE and routes every
        # one through _tablink(), which renders them as plain text with "(needs
        # sign-in — not on this page)" beside them.
        _help_panel = re.search(r'<div class="panel" id="tab-help".*?</div><!-- /help -->',
                                page, re.S)
        _outside_help = (page.replace(_help_panel.group(0), "")
                         if _help_panel else page)
        _named = sorted({m for lbl in ("Pool control", "Chemicals", "Settings")
                         for m in re.findall(rf"\b{lbl} tab\b", _outside_help)})
        if _named:
            raise ValueError(
                f"the public build tells its reader to use {_named} — screens "
                f"that are not in this file. The diagnosis is public; the "
                f"remedy is not. Gate the sentence on `public`, or route it "
                f"through help._tablink().")

        leaked = [pat for pat in patterns if re.search(pat, page)]
        if leaked:
            raise ValueError(f"the public build still contains {leaked} — refusing to "
                             f"write a page that leaks the house")

        # AND THE ALLOWLIST, because a substring search over the page is only as
        # good as the substrings. `fetch('/api/con' + 'trol')` defeats every
        # pattern above at once, and so does a template literal or a variable —
        # the routes were checked by NAME, and a name is exactly the thing an
        # author can spell differently. So the public page's calls are checked
        # from the other side: every fetch() in it must name a literal string,
        # and that string must be one of the three routes an anonymous visitor
        # is allowed to reach. A computed target is refused whatever it computes
        # to, which is the only way to be sure without running the script.
        allowed = set(_server.PUBLIC_POST) | {"/api/health"}
        for call in re.findall(r"fetch\(\s*([^,)]*)", page):
            call = call.strip()
            lit = re.fullmatch(r"'([^']*)'", call) or re.fullmatch(r'"([^"]*)"', call)
            if not lit:
                raise ValueError(
                    f"the public build calls fetch({call!r}) — a target that is not "
                    f"a plain string literal cannot be checked against the public "
                    f"routes, so it is refused. Wrap the handler in priv:start / "
                    f"priv:end, or call one of {sorted(allowed)} directly.")
            if lit.group(1) not in allowed:
                raise ValueError(
                    f"the public build calls {lit.group(1)}, which is not one of the "
                    f"routes an anonymous visitor may reach ({sorted(allowed)}). "
                    f"Wrap the handler in priv:start / priv:end.")

    # THE NAV ORDER AND THE OPENING TAB, after the excision and before anything
    # measures the page. Both builds go through both calls: the authenticated
    # one is not "the template as written" any more than the public one is, and
    # having one build take the markup's word for it is how the two came to
    # differ by accident rather than by decision.
    page = order_tabs(page, PUBLIC_TABS if public else TAB_ORDER)
    page = set_default_tab(page, PUBLIC_DEFAULT_TAB if public else PRIVATE_DEFAULT_TAB)

    # After the public build's excisions, so no tab points at a panel this file
    # no longer has. Asserted rather than trusted: both of these are invisible
    # on screen, so a regex that silently stopped matching would be found by
    # somebody using a screen reader rather than by anybody rendering the page.
    page, n_status, n_tabs = a11y_wiring(page)
    if not n_status or not n_tabs:
        raise ValueError(
            f"a11y_wiring() tied {n_status} status region(s) and {n_tabs} tab(s) "
            f"to their panels, and both must be non-zero: every form on this page "
            f"reports through a .fmsg span and every panel is reached by a tab. "
            f"Zero means the markup moved and the wiring now matches nothing — "
            f"which shows up only in a screen reader.")
    if not public:
        thin = [(pat, n, len(re.findall(pat, page)))
                for pat, n in PRIVATE_MUST_SURVIVE_COUNTS
                if len(re.findall(pat, page)) < n]
        if thin:
            raise ValueError(
                "the authenticated build has been HOLLOWED OUT: " +
                "; ".join(f"{pat} appears {got} times, at least {n} expected"
                          for pat, n, got in thin) +
                ". Nothing cuts this build on purpose, but a panel that renders "
                "empty empties it just as well.")
    check_page(page)
    check_svg_styles(page)
    if public:
        # Only the public build: the authenticated one always has a token.
        check_public_is_usable(page)
    out = os.path.join(SITE, out_name or ("index.html" if public else "admin.html"))
    # Atomically. The site directory is served straight off disk by
    # SimpleHTTPRequestHandler, one open() per request, and a render fires on
    # every pushed sample and every write — so a plain open(out, "w") truncated
    # the live page and served whatever had been flushed so far to anybody who
    # asked during the window. A truncated page is worse than a blank one: it
    # renders, with the head and half the body, and looks like the pool.
    # replace_atomically() was already used for six data files; the page, which
    # is the thing people actually read, was the one writer still doing it in
    # place.
    locking.replace_atomically(out, lambda f: f.write(page))
    copy_cards()
    return out, len(page)

TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="@@favicon@@">
<!-- THE TAB SAYS WHAT THIS IS, not just what it is called. A title, a
     bookmark and a search result are all title-only; "poolhound" names the
     product to somebody who already knows it and nothing to anybody else.
     Both this and the description come from info.CATEGORY, because a product
     that types its category twice is a product that will shortly be calling
     itself two things in one document. -->
<title>@@page_title@@</title>
<meta name="description" content="@@description@@">
<style>
@@css@@
@@extra_css@@
</style>
</head><body>
<div class="wrap">

<a class="skip" href="#main">Skip to content</a>

<header>
  <h1>@@mark@@<span class="wordmark">poolhound</span></h1>
  @@freshness@@
  <!-- A visible way to the source. It was reachable before only as the words
       "open source" in the footer and a row in a Help table, which is findable
       rather than visible — and this repository is meant to be opened. -->
  <a class="srclink" href="https://github.com/jchirayath/poolhound" rel="noopener"
     title="poolhound on GitHub — MIT">
    <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z"/></svg><span>Source</span></a>
  <div class="refresh-wrap">
    <button type="button" id="refresh-btn" class="icon-btn refresh"
            aria-expanded="false" aria-controls="refresh-menu"
            title="Refresh the data sources"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 11a8 8 0 10-2 6M20 5v6h-6"/></svg><span>Refresh</span></button>
    <div class="refresh-menu" id="refresh-menu" hidden>
      <p>Both of these reach somebody else's server and are rationed, which is why
      they are separate buttons. The controller is not here: it pushes to us.</p>
      <button type="button" class="rsrc" data-src="waterguru">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3c0 0-6 6.5-6 10a6 6 0 1012 0c0-3.5-6-10-6-10z"/></svg><span><b>WaterGuru</b><i>spends one of the two daily calls upstream asks for</i></span></button>
      <button type="button" class="rsrc" data-src="leslies">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 3h12M9 3v7l-4 8a2 2 0 002 3h10a2 2 0 002-3l-4-8V3"/></svg><span><b>Leslie's</b><i>logs in as a browser; returns the whole history</i></span></button>
      <button type="button" class="rsrc all" data-src="waterguru,leslies">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 11a8 8 0 10-2 6M20 5v6h-6"/></svg><span><b>Everything</b><i>including the two that cost something</i></span></button>
      <div class="rmsg" id="refresh-msg"></div>
    </div>
  </div>
  <!-- THREE STATES, THREE ICONS, AND THE WORD FOR THE ONE YOU ARE IN.
       The toggle became three-state and the affordance did not follow: one
       fixed crescent served all three, so pressing it from "following your
       system" on a light machine moved to "light" — identical pixels, no
       feedback — and read as broken. The markup also still described two
       states and depended on the script to correct it, so with JavaScript off,
       or before it ran, the control said the wrong thing about itself.
       It now ships in its default state, named: the un-stamped "follow the
       system" one, which is what the page is in until the script says
       otherwise. Never colour or shape alone — the state is also a word. -->
  <button type="button" id="theme-toggle" class="icon-btn"
          aria-label="Theme: following your system — press to change"
          title="Theme: following your system">
    <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true"
         data-theme-icon="system">
      <circle cx="8" cy="8" r="6.4" fill="none" stroke="currentColor" stroke-width="1.4"/>
      <path d="M8 1.6a6.4 6.4 0 0 1 0 12.8z" fill="currentColor"/>
    </svg>
    <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true"
         data-theme-icon="light" hidden>
      <circle cx="8" cy="8" r="3.4" fill="currentColor"/>
      <g stroke="currentColor" stroke-width="1.4" stroke-linecap="round">
        <path d="M8 .8v2M8 13.2v2M.8 8h2M13.2 8h2M2.9 2.9l1.4 1.4M11.7 11.7l1.4 1.4M13.1 2.9l-1.4 1.4M4.3 11.7l-1.4 1.4"/>
      </g>
    </svg>
    <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true"
         data-theme-icon="dark" hidden>
      <path d="M8 1.6a6.4 6.4 0 1 0 6.4 6.4A5 5 0 0 1 8 1.6z" fill="currentColor"/>
    </svg>
    <!-- Inline, because style.CSS has no rule for this and .icon-btn is a bare
         inline-flex square; .icon-btn.refresh does the same job with a class. -->
    <span class="theme-word" style="font-size:11px;margin-left:5px">system</span>
  </button>
  @@signin@@
  @@verdict@@
</header>
@@masthead@@

<!-- THE LABELS ARE THE SHORT NAMES. "Pool control", "Pool chemistry" and
     "Pool Volume Calculator" needed 1,223px of nav in a 1,038px column, so
     Help sat off the right-hand edge of a 1,180px desktop with nothing saying
     so — a scroll container with no visible overflow reads as a menu that ends
     where it is cut. Every one of them is on a page headed "Pool", and each
     panel still carries its full name in its own heading and everywhere prose
     refers to it. -->
<nav class="tabs" role="tablist" aria-label="Sections">
  <button role="tab" data-tab="home" aria-selected="true"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 11l9-8 9 8M5 9v11h14V9"/></svg><span class="lbl">Home</span>@@badge@@</button>
  <button role="tab" data-tab="control" aria-selected="false"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 3v6M15 3v6M6 9h12v3a6 6 0 01-12 0zM12 18v3"/></svg><span class="lbl">Control</span></button>
  <button role="tab" data-tab="volume" aria-selected="false">@@icon_volume@@<span class="lbl">Volume</span></button>
  <button role="tab" data-tab="chemicals" aria-selected="false"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 3v6L4 20h16L15 9V3M9 3h6M8 14h8"/></svg><span class="lbl">Chemicals</span></button>
  <button role="tab" data-tab="chemistry" aria-selected="false"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 5a2 2 0 012-2h12v18H6a2 2 0 01-2-2z M8 7h8M8 11h6"/></svg><span class="lbl">Chemistry</span></button>
  <button role="tab" data-tab="collection" aria-selected="false"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 21a9 9 0 100-18 9 9 0 000 18zM12 7v5l3 2"/></svg><span class="lbl">Collection</span>@@collection_badge@@</button>
  <button role="tab" data-tab="settings" aria-selected="false"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h10M18 7h2M4 17h4M12 17h8M16 5v4M8 15v4"/></svg><span class="lbl">Settings</span></button>
  <button role="tab" data-tab="ask" aria-selected="false"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 11-3.2-6.9M12 17v.01M12 14c0-2 2.5-2.2 2.5-4A2.5 2.5 0 0010 9.6"/></svg><span class="lbl">Ask AI</span></button>
  <button role="tab" data-tab="info" aria-selected="false">@@mark_tab@@<span class="lbl">Poolhound info</span></button>
  <button role="tab" data-tab="help" aria-selected="false">@@icon_help@@<span class="lbl">Guide</span></button>
  <button role="tab" data-tab="policies" aria-selected="false">@@icon_policies@@<span class="lbl">Safety &amp; privacy</span></button>
</nav>

<main id="main">
<div class="panel" id="tab-home" role="tabpanel">

<section id="what-to-do">
  <h2>What to do next</h2>
  <p class="sub">Derived from the readings above and this pool's volume, ordered by
  consequence rather than by how far a number sits outside its band — aggressive
  water damages plaster permanently, while a high pH reverses the moment it is
  corrected. Doses are estimates to start from, not prescriptions; log what you
  actually add and the estimates improve.</p>
  @@actions@@
  <!-- BELOW the list, not above it. The pod's raw alert string used to be the
       first thing a new owner read on Home: the vendor's undifferentiated text
       placed above the thing that exists to replace it. Its clauses are parsed
       into the list above now; this is kept as the pod's own words, where a
       reader can check what was said against what was made of it. -->
  @@alert_html@@
</section>

<section>
  <h2>Where the water is now</h2>
  <p class="sub">Free chlorine and pH come from whichever source measured them
  most recently — usually the WaterGuru pod, which reads every day, but a lab
  result or a test you entered by hand wins if it is newer. The rest come from a
  laboratory — WaterGuru's, on a posted sample, or Leslie's, on one carried into
  the store. Each tile names its source and its age, and the most recent result
  wins whichever produced it.</p>
  <div class="tiles">@@tiles@@</div>
  <p class="sub" style="margin:12px 0 0">Every one of these is pushed by something —
  the cell, the spillover, the sun, the last thing poured in.
  <a href="#chemistry" data-tab-link="chemistry">What raises and lowers each factor →</a></p>
</section>

<section>
  <h2>Trend</h2>
  <p class="sub">Two measures, two charts, one shared clock. The shaded band is the
  acceptable range and the dashed line the aim.</p>
  <!-- THE LEGEND IS THE CONTROL. Two rows of the same information — a legend
       saying what a mark means and a switch turning it off — is one row too
       many, and the second would have to repeat the mark to be usable. So each
       button carries the shape the chart draws for that source. -->
  <div class="chartbar">
    @@trend_range@@
    <div class="srcbar" role="group" aria-label="Which sources are drawn">
      <button type="button" class="sbtn" data-src="s-pod" aria-pressed="true"><svg width="13" height="13" aria-hidden="true"><circle cx="6.5" cy="6.5" r="4.5" fill="var(--pump)"/></svg>WaterGuru pod</button>
      <button type="button" class="sbtn" data-src="s-les" aria-pressed="true"><svg width="15" height="13" aria-hidden="true"><path d="M7.5,1.5 L13,6.5 L7.5,11.5 L2,6.5 Z" fill="none" stroke="var(--ink2)" stroke-width="1.6"/></svg>Leslie's lab</button>
      <button type="button" class="sbtn" data-src="s-man" aria-pressed="true"><svg width="13" height="13" aria-hidden="true"><rect x="2" y="2" width="9" height="9" fill="none" stroke="var(--spa)" stroke-width="1.6"/></svg>By hand</button>
    </div>
    <span class="sub" style="margin:0">shaded band = target range</span>
  </div>
  <div class="two">
    <div class="card">
      <div class="ch-h"><h3>Free chlorine</h3><span class="pill p-@@fc_state@@">@@fc_state@@</span></div>
      @@fc_chart@@
    </div>
    <div class="card">
      <div class="ch-h"><h3>pH</h3><span class="pill p-@@ph_state@@">@@ph_state@@</span></div>
      @@ph_chart@@
    </div>
  </div>
</section>

<section id="lab-history">
  <h2>Lab history</h2>
  <p class="sub">Two laboratories measure this pool: WaterGuru analyses a sample
  posted to them, Leslie's runs a photometer on one carried into the store. Each
  lab is drawn as <b>its own line</b> — never joined into one — because a single
  line through both would assert that a step between consecutive points is the
  pool moving, when it may just as easily be the two labs disagreeing. Where the
  lines sit apart on the same date, that separation <em>is</em> the disagreement.
  Each panel keeps its own scale: alkalinity near 80 and salt near 3,500 share no
  axis.</p>
  <div class="card"><div class="labgrid">@@labcharts@@</div>
    <div class="chartbar" style="margin:14px 0 0">
      <div class="srcbar" role="group" aria-label="Which sources are drawn">
        <button type="button" class="sbtn" data-src="s-wg" aria-pressed="true"><svg width="30" height="13" aria-hidden="true"><line x1="0" y1="6.5" x2="30" y2="6.5" stroke="var(--pump)" stroke-width="1.8"/><circle cx="15" cy="6.5" r="4" fill="var(--pump)"/></svg>WaterGuru lab</button>
        <button type="button" class="sbtn" data-src="s-les" aria-pressed="true"><svg width="30" height="13" aria-hidden="true"><line x1="0" y1="6.5" x2="30" y2="6.5" stroke="var(--ink2)" stroke-width="1.8" stroke-dasharray="5 3"/><path d="M15,1.5 L20,6.5 L15,11.5 L10,6.5 Z" fill="var(--panel)" stroke="var(--ink2)" stroke-width="1.6"/></svg>Leslie's lab</button>
        <button type="button" class="sbtn" data-src="s-man" aria-pressed="true"><svg width="30" height="13" aria-hidden="true"><line x1="0" y1="6.5" x2="30" y2="6.5" stroke="var(--spa)" stroke-width="1.8" stroke-dasharray="2 3"/><rect x="10.5" y="2" width="9" height="9" fill="var(--panel)" stroke="var(--spa)" stroke-width="1.6"/></svg>By hand</button>
      </div>
      <span class="sub" style="margin:0">shaded band = target range</span>
    </div>
  </div>
</section>

<section id="two-instruments">
  <h2>What changed between the last two lab results</h2>
  <p class="sub">The two most recent lab results, side by side. A difference here
  has two possible causes and this table cannot separate them: the pool moved, or
  the labs read differently. That is the reason for logging every dose.</p>
  @@cmp_html@@
</section>

<section id="right-now">
  <h2>Running right now</h2>
  <p class="sub">The state the panel last reported. Amber means a command has
  gone through but the panel has not been read since — samples arrive every
  fifteen minutes, so a switch just turned on can sit like that for a few
  minutes before it confirms.</p>
  @@nowstrip@@
</section>

<section>
  <h2>What the pool did</h2>
  <p class="sub">One row per day, midnight to midnight. The salt cell only makes
  chlorine while the pump runs, and the spa spillover aerates the whole time it
  does — so pump hours drive chlorine up and pH up together. The sheer descent
  adds aeration on top. Chemical additions appear as a marker above the lanes.</p>
  <div class="card">
    <div class="legend">
      <span><i style="background:@@c_pump@@"></i>Pump</span>
      <span><i style="background:@@c_spa@@"></i>Spa</span>
      <span><i style="background:@@c_sheer@@"></i>Sheer descent</span>
      <span style="color:var(--ink3)">▼ chemical added</span>
    </div>
    <div class="hours"><div></div>
      <div><span>0</span><span>3</span><span>6</span><span>9</span><span>12</span>
           <span>15</span><span>18</span><span>21</span><span>24</span></div>
      <div></div></div>
    @@rows@@
  </div>
</section>

<section id="home-power">
  <h2>Power and runtime</h2>
  <p class="sub">What the pump costs to run, and whether it needs to run that
  long. Hours come from the samples above; the energy figure says on its face
  whether it was measured at the pump or estimated from a rating you entered.</p>
  @@power@@
</section>

<section>
  <h2>Model readiness</h2>
  <div class="card sub" style="margin:0">@@model@@</div>
</section>

<details>
  <summary>Every number on this page, as tables</summary>
  @@tables@@
</details>
</div><!-- /home -->

<div class="panel" id="tab-volume" role="tabpanel" hidden>@@volume_panel@@</div><!-- /volume -->
<div class="panel" id="tab-control" role="tabpanel" hidden>@@control_panel@@</div><!-- /control -->
<div class="panel" id="tab-chemicals" role="tabpanel" hidden>@@chemicals_panel@@</div><!-- /chemicals -->
<div class="panel" id="tab-chemistry" role="tabpanel" hidden>@@chemistry_panel@@</div><!-- /chemistry -->
<div class="panel" id="tab-collection" role="tabpanel" hidden>@@collection_panel@@</div><!-- /collection -->
<div class="panel" id="tab-settings"  role="tabpanel" hidden>@@settings_panel@@</div><!-- /settings -->
<div class="panel" id="tab-ask" role="tabpanel" hidden>@@ask_panel@@</div><!-- /ask -->
<div class="panel" id="tab-info" role="tabpanel" hidden>@@info_panel@@</div><!-- /info -->
<div class="panel" id="tab-help" role="tabpanel" hidden>@@help_panel@@</div><!-- /help -->
<div class="panel" id="tab-policies" role="tabpanel" hidden>@@policies_panel@@</div><!-- /policies -->
</main>

<footer>
  <span class="ver"><b>poolhound</b> @@version@@</span>
  <span>generated @@generated@@</span>
  <span>@@nsamples@@ equipment samples</span>
  <span title="@@source@@" class="priv">config loaded</span>
  <span id="api-state">read-only</span>
  <span>@@policy_links@@</span>
</footer>
<!-- On every tab, not buried in Help. None of the equipment data exists without
     AqualinkD and none of the water data without the two labs; a product that
     is mostly other people's work should say so where it is actually read. -->
<p class="thanks">Built on
  <a href="https://github.com/sfeakes/AqualinkD" rel="noopener">AqualinkD</a>,
  which speaks the panel's undocumented RS485 protocol. Water tests by
  <a href="https://waterguru.com/" rel="noopener">WaterGuru</a> and
  <a href="https://lesliespool.com/" rel="noopener">Leslie&rsquo;s</a>.
  poolhound is <a href="https://github.com/jchirayath/poolhound" rel="noopener">open
  source</a>.</p>
</div>

<div id="tip"></div>
<!-- Drill-down. A table row, a chart point and a day in the strip all resolve to
     ONE record, and this is where that record is shown in full. The samples
     table is twenty columns wide and scrolls sideways, so "what did this row
     actually say" was a question the page could not answer without a horizontal
     scrollbar and a good memory. -->
<dialog id="detail">
  <form method="dialog" class="d-head">
    <b id="detail-title">Reading</b>
    <button class="btn d-close" aria-label="Close">Close</button>
  </form>
  <div id="detail-body"></div>
</dialog>
<script id="catalogue" type="application/json">@@catalogue@@</script>
<script id="glossary" type="application/json">@@glossary@@</script>
<script id="saved-shape" type="application/json">@@saved_shape@@</script>
<script id="pool-templates" type="application/json">@@templates@@</script>
<script id="refill-units" type="application/json">@@refill_units@@</script>
<script id="spa-templates" type="application/json">@@spa_templates@@</script>
<script>
/* Hover layer. Charts are SVG, so the tooltip is a fixed div positioned from
   the dot's screen rect — no coordinate maths against the viewBox. */
(function () {
  var tip = document.getElementById('tip');
  document.querySelectorAll('.dot').forEach(function (d) {
    function show() {
      var r = d.getBoundingClientRect();
      /* Never render the word "undefined" at somebody: a point with no label
         is a bug, and the honest thing is to show nothing rather than to show
         a broken string that looks like data. */
      var lab = d.dataset.label || '', val = d.dataset.value || '';
      if (!lab && !val) return;
      /* DOM, not innerHTML — the same rule the drill-down dialog forty lines
         below is built on, and this layer was the exception. A dataset read
         DECODES the escaping the server applied, exactly as textContent does,
         so a server-escaped "&lt;img&gt;" in a dot's label comes back as a live
         tag and innerHTML re-parses it: the escaping is undone in transit, on a
         page whose script holds the token that can switch the pool. Both
         attributes feed only strftime output today, which makes this one commit
         — a series named after a lab string — from being live rather than safe. */
      tip.textContent = '';
      if (lab) {
        var b = document.createElement('b');
        b.textContent = lab;
        tip.appendChild(b);
      }
      tip.appendChild(document.createTextNode(val));
      tip.style.opacity = 1;
      var w = tip.offsetWidth;
      tip.style.left = Math.max(6, Math.min(window.innerWidth - w - 6, r.left + r.width / 2 - w / 2)) + 'px';
      tip.style.top = (r.top - tip.offsetHeight - 9) + 'px';
    }
    d.addEventListener('mouseenter', show);
    d.addEventListener('focus', show);
    d.addEventListener('mouseleave', function () { tip.style.opacity = 0; });
    d.addEventListener('blur', function () { tip.style.opacity = 0; });
    d.setAttribute('tabindex', '0');
  });
})();

/* ------------------------------------------------------------ drill-down ---
   Click a row, a chart point or a day and see THAT one thing, in full.
   Everything needed is already in the page; this only has to find it and lay
   it out, so there is no second source that could disagree with the table. */
(function () {
  var dlg = document.getElementById('detail');
  if (!dlg || !dlg.showModal) return;          /* no <dialog>: leave rows inert */
  var title = document.getElementById('detail-title');
  var body  = document.getElementById('detail-body');

  /* BUILT WITH DOM APIS, NEVER innerHTML.
     The values here are read back out of the table with textContent, and
     textContent DECODES the escaping the server applied — so "&lt;img&gt;" comes
     back as a real "<img>" tag. Putting that into innerHTML re-parses it and the
     server's escaping has been undone in transit. It is exploitable: a WaterGuru
     alert string, a Leslie's issues field, or a note somebody typed will execute
     when the row is clicked, on a page whose script holds the session token that
     can switch the pool. (script-src carries 'unsafe-inline' for the page's own
     script, so the CSP does not catch it either.)
     Demonstrated before this was written: clicking a row whose alerts field held
     <img src=x onerror=...> ran the handler.
     textContent for every value; elements for every tag. */
  function show(heading, pairs, foot) {
    title.textContent = heading;
    body.textContent = '';

    var dl = document.createElement('dl');
    dl.className = 'dl';
    pairs.forEach(function (p) {
      var k = document.createElement('dt');
      k.textContent = p[0];
      var v = document.createElement('dd');
      if (p[1] === '' || p[1] == null) v.textContent = '\u2014';
      else v.textContent = p[1];
      dl.appendChild(k);
      dl.appendChild(v);
    });
    body.appendChild(dl);

    /* The footer is a list of segments rather than a string of markup, so a
       filename or a label can never be read as a tag: {t: plain} | {b: bold}. */
    if (foot && foot.length) {
      var pEl = document.createElement('p');
      pEl.className = 'd-foot';
      foot.forEach(function (seg) {
        if (seg.b != null) {
          var b = document.createElement('b');
          b.textContent = seg.b;
          pEl.appendChild(b);
        } else {
          pEl.appendChild(document.createTextNode(seg.t == null ? '' : seg.t));
        }
      });
      body.appendChild(pEl);
    }
    dlg.showModal();
  }
  window.showDetail = show;

  /* Table rows. The header cells name the fields, so the dialog stays correct
     if a column is ever added or reordered — reading them beats a second copy
     of the column list here. */
  document.querySelectorAll('.dtable').forEach(function (dt) {
    var heads = [].slice.call(dt.querySelectorAll('thead th'))
                  .map(function (th) { return th.textContent.trim(); });
    var name = (dt.querySelector('.dt-name') || {}).textContent || 'Row';
    var tb = dt.querySelector('tbody');
    if (!tb) return;
    tb.classList.add('rows-clickable');
    tb.addEventListener('click', function (e) {
      var tr = e.target.closest('tr');
      if (!tr || tr.classList.contains('empty')) return;
      var cells = [].slice.call(tr.children).map(function (td) { return td.textContent.trim(); });
      var pairs = heads.map(function (h, i) { return [h, cells[i]]; })
                       .filter(function (p) { return p[0]; });
      show(name, pairs, dt.dataset.csv
           ? [{t: 'Every row of this table is in '}, {b: dt.dataset.csv}, {t: '.'}]
           : null);
    });
    /* Keyboard: a row you can click is a row you must be able to reach. */
    [].slice.call(tb.querySelectorAll('tr')).forEach(function (tr) {
      if (tr.classList.contains('empty')) return;
      tr.tabIndex = 0;
      tr.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); tr.click(); }
      });
    });
  });

  /* Chart points resolve to THE ROW, like everything else that drills.
     This used to open a dialog holding the two strings the tooltip had shown a
     second earlier, plus a sentence telling the reader to go and find the row
     themselves — a click and a dismissal to learn nothing. The dot carries the
     measured timestamp it was drawn from, which is the same string the tables
     print, so the row can be found by matching a cell exactly. No table
     identity is needed and none is assumed: the public build carries no CSV
     names, and this works there too. */
  function rowByKey(key) {
    var found = null;
    [].slice.call(document.querySelectorAll('.dtable')).some(function (dt) {
      var tb = dt.querySelector('tbody');
      if (!tb) return false;
      return [].slice.call(tb.rows).some(function (tr) {
        return [].slice.call(tr.cells).some(function (td) {
          if (td.textContent.trim() !== key) return false;
          found = {dt: dt, tr: tr};
          return true;
        });
      });
    });
    return found;
  }

  document.querySelectorAll('svg.chart .dot').forEach(function (d) {
    d.style.cursor = 'pointer';
    d.addEventListener('click', function () {
      var lab = d.dataset.label || '', val = d.dataset.value || '';
      if (!lab && !val) return;
      var hit = d.dataset.key ? rowByKey(d.dataset.key) : null;
      if (hit) {
        var heads = [].slice.call(hit.dt.querySelectorAll('thead th'))
                      .map(function (th) { return th.textContent.trim(); });
        var cells = [].slice.call(hit.tr.cells)
                      .map(function (td) { return td.textContent.trim(); });
        var name = (hit.dt.querySelector('.dt-name') || {}).textContent || 'Reading';
        show(name, heads.map(function (h, i) { return [h, cells[i]]; })
                        .filter(function (p) { return p[0]; }),
             hit.dt.dataset.csv
               ? [{t: 'Every row of this table is in '}, {b: hit.dt.dataset.csv}, {t: '.'}]
               : null);
        return;
      }
      /* Not in the page. Say that, rather than pointing at a table that does
         not hold it — a reading older than the rendered window is in the CSV,
         and the reader should not go hunting for a row that is not there. */
      show('Reading', [['When', lab], ['Value', val]],
           [{t: 'This reading is older than the rows rendered in the tables '},
            {t: 'below, so there is nothing further to open here.'}]);
    });
  });

  /* A day in the strip drills into the samples table: filter it to that date,
     open it, and scroll it into view. Real drill-down rather than a summary —
     it lands on the rows themselves. */
  var samples = [].slice.call(document.querySelectorAll('.dtable')).filter(function (t) {
    return (t.dataset.csv || '') === 'samples.csv';
  })[0];
  /* :not(.drow-far) — the day rows the rendered samples window cannot reach are
     not buttons and must not behave like them. The server decides which those
     are, because it is the half that knows how far the window goes. */
  document.querySelectorAll('.drow[data-date]:not(.drow-far)').forEach(function (row) {
    row.tabIndex = 0;
    row.classList.add('drow-click');
    function open() {
      if (!samples) return;
      var det = samples.closest('details');
      if (det) det.open = true;
      var f = samples.querySelector('.dt-filter');
      if (f) {
        f.value = row.dataset.date;
        f.dispatchEvent(new Event('input', {bubbles: true}));
      }
      /* Smooth only where motion is welcome. Every other animation on this
         page honours the preference; a scroll is no different, and this one is
         triggered by a click rather than chosen. */
      var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      samples.scrollIntoView({behavior: calm ? 'auto' : 'smooth', block: 'start'});
    }
    row.addEventListener('click', open);
    row.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); }
    });
  });
})();

/* ------------------------------------------------------------------ tabs ---
   The tab is in the URL hash, so a tab is linkable, the back button works, and
   a reload after saving returns to where the reader was rather than to Home. */
(function () {
  var tabs = [].slice.call(document.querySelectorAll('.tabs [data-tab]'));
  var names = tabs.map(function (b) { return b.dataset.tab; });
  /* WHICH TAB THIS PAGE OPENS ON, READ FROM THE PAGE.
     It used to be the name of the Home tab, spelled out as a literal, twice
     -- a second copy of a decision the build makes, since
     render.set_default_tab() writes exactly one selected button and exactly
     one visible panel, and then this overrode it on load. On the
     authenticated build it reopened that tab over the controls. On the public
     build, which does not contain that tab at all, it matched nothing: every
     panel was hidden and no tab was selected, so a page that had rendered
     correctly went blank a frame later and looked fine in any screenshot
     taken of the file rather than of the browser.

     NOTHING IN THIS COMMENT MAY SPELL A TAB NAME IN QUOTES. The check that
     keeps this honest reads the router for quoted tab names, and a comment
     naming the defect is indistinguishable from the defect. That has now
     happened five times in this project, always this way round.
     Read before anything calls show(), so it is the markup's answer. */
  var FIRST = (document.querySelector('.tabs [data-tab][aria-selected="true"]')
               || tabs[0]).dataset.tab;

  function show(name, push) {
    /* A hash that is not a tab may still be a SECTION inside one.
       The chemistry page links to #ph, #free_cl, #ta and so on, and those all
       fell through to the fallback and dumped the reader on Home — the tab
       router treating "not a tab" as "not anything". Resolving the id to its
       panel makes every in-page anchor work, and makes a shared link to
       #free_cl open the right tab at the right place instead of the front
       page. */
    var target = null;
    if (names.indexOf(name) < 0) {
      var el = name && document.getElementById(name);
      var panel = el && el.closest('.panel');
      if (panel && panel.id.indexOf('tab-') === 0) {
        target = el;
        name = panel.id.slice(4);
      } else {
        name = FIRST;
      }
    }
    tabs.forEach(function (b) {
      var on = b.dataset.tab === name;
      b.setAttribute('aria-selected', on ? 'true' : 'false');
      document.getElementById('tab-' + b.dataset.tab).hidden = !on;
    });
    if (push && location.hash.slice(1) !== name) history.pushState(null, '', '#' + name);
    /* Scrolling to the top of a section only makes sense once its panel is
       visible — before that it has no position. */
    if (target) {
      target.scrollIntoView({block: 'start'});
    } else {
      window.scrollTo(0, 0);
    }
  }
  tabs.forEach(function (b) {
    b.addEventListener('click', function () { show(b.dataset.tab, true); });
  });
  document.addEventListener('click', function (e) {
    var a = e.target.closest('[data-tab-link]');
    if (!a) return;
    e.preventDefault();
    show(a.dataset.tabLink, true);
  });
  /* role="tab" is a promise that arrow keys work. Without this the markup
     claims an interaction the page does not implement, which is worse for a
     screen-reader user than plain buttons would have been. */
  document.querySelector('.tabs').addEventListener('keydown', function (e) {
    var i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    var to = null;
    if (e.key === 'ArrowRight') to = (i + 1) % tabs.length;
    else if (e.key === 'ArrowLeft') to = (i - 1 + tabs.length) % tabs.length;
    else if (e.key === 'Home') to = 0;
    else if (e.key === 'End') to = tabs.length - 1;
    if (to === null) return;
    e.preventDefault();
    tabs[to].focus();
    show(tabs[to].dataset.tab, true);
  });
  tabs.forEach(function (b) { b.tabIndex = 0; });

  window.addEventListener('popstate', function () { show(location.hash.slice(1), false); });
  /* Plain <a href="#section"> anchors change the hash without firing popstate,
     so without this they moved the URL and nothing else — and the next
     navigation then resolved the stale hash to Home. */
  window.addEventListener('hashchange', function () {
    show(location.hash.slice(1), false);
  });
  show(location.hash.slice(1) || FIRST, false);
})();

/* ------------------------------------------------------- chart controls ---
   Two controls, one mechanism: show and hide what the server already drew.

   The time window picks between variants rendered at build time, so there is
   no drawing engine here and no series data in the page. The source toggles
   set a class on the enclosing section, which the stylesheet turns into
   display:none for every element of that source at once.

   Both are delegated from the document, because the charts are replaced
   wholesale by a re-render and a listener bound to a button would go with it. */
(function () {
  function pickRange(bar, key) {
    Array.prototype.forEach.call(bar.querySelectorAll('.rbtn'), function (b) {
      b.setAttribute('aria-pressed', b.dataset.range === key ? 'true' : 'false');
    });
    /* EVERY chart in the section, not the one nearest the button. The two
       trend charts are stacked on a shared x axis, so a window that applied to
       one of them would break the only reason they are drawn together. */
    var scope = bar.closest('section') || document;
    Array.prototype.forEach.call(scope.querySelectorAll('.ranged .rv'), function (v) {
      v.hidden = v.dataset.range !== key;
    });
  }

  document.addEventListener('click', function (e) {
    var r = e.target.closest && e.target.closest('.rbtn');
    if (r) { pickRange(r.parentNode, r.dataset.range); return; }


    var sbtn = e.target.closest && e.target.closest('.sbtn');
    if (!sbtn) return;
    /* The scope is the section, so toggling a source on the trend charts
       reaches both of them — they are two views of the same instruments and
       hiding a lab on one while it is drawn on the other is a page arguing
       with itself. */
    var scope = sbtn.closest('section') || document;
    var on = sbtn.getAttribute('aria-pressed') !== 'true';
    sbtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    scope.classList.toggle('hide-' + sbtn.dataset.src, !on);
  });
})();

/* ----------------------------------------------------------- canvas paint ---
   The tracing canvas was painted with literal colours: an #e8edf2 ground and
   #fff handles, plus a THIRD hand-typed copy of the series palette. In dark mode
   that is a near-white slab with white handles on it, and a re-picked palette
   would have left these behind — style.SERIES and the CSS are one source and
   this was a second one wearing the same numbers.

   Read from the live custom properties instead, so the canvas is whatever the
   theme currently says, and redraw when the theme changes. */
var PAINT = (function () {
  function tok(name, fallback) {
    try {
      var v = getComputedStyle(document.documentElement).getPropertyValue(name);
      return (v || '').trim() || fallback;
    } catch (e) { return fallback; }
  }
  return {
    ground: function () { return tok('--line2', '#e8edf2'); },
    handle: function () { return tok('--panel', '#ffffff'); },
    ink:    function () { return tok('--ink',   '#16202b'); },
    /* The fallbacks come from style.SERIES, not retyped here. They were the
       palette written a second time, in the one place bin/contrast cannot
       measure and a screenshot cannot show: re-picking a colour in style.py
       left the charts drawing the old one whenever the CSS token was missing,
       which is the state the series spent several commits in. Invisible to
       the seams gate until it stopped blanking render.TEMPLATE. */
    pump:   function () { return tok('--pump',  '@@c_pump@@'); },
    spa:    function () { return tok('--spa',   '@@c_spa@@'); },
    sheer:  function () { return tok('--sheer', '@@c_sheer@@'); }
  };
})();

/* ------------------------------------------------------------------ theme ---
   The CSS already supports an explicit choice in both directions; without a
   control, that half of it was unreachable. The preference is per-browser and
   never leaves it. */
(function () {
  var btn = document.getElementById('theme-toggle');
  if (!btn) return;
  var KEY = 'poolhound-theme';
  function apply(v) {
    if (v) document.documentElement.setAttribute('data-theme', v);
    else document.documentElement.removeAttribute('data-theme');
  }
  var saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) {}
  apply(saved);
  /* THREE states, because there are three. The stylesheet is built around the
     un-stamped "follow the system" state being the common case, and the toggle
     used to flip between light and dark only \u2014 so the first press was a
     one-way door out of the default and nothing could ever get back to it. A
     reader whose phone switches to dark at sunset lost that the moment they
     looked at the page in daylight and pressed once. */
  var ORDER = [null, 'light', 'dark'];
  function label(v) {
    return v === 'light' ? 'Theme: light' :
           v === 'dark'  ? 'Theme: dark'  : 'Theme: following your system';
  }
  function setLabel(v) {
    btn.setAttribute('aria-label', label(v) + ' \u2014 press to change');
    btn.setAttribute('title', label(v));
    /* The icon and the word follow the state too. A control whose only signal
       of which of three states it is in is a hover title is a control that
       reads as broken the first time pressing it changes nothing visible. */
    var want = v || 'system';
    btn.querySelectorAll('[data-theme-icon]').forEach(function (ic) {
      /* setAttribute, not .hidden: `hidden` is an HTMLElement property and
         these are SVG elements, so `ic.hidden = true` sets a JavaScript
         property nothing renders from and every icon stays visible. Found by
         asking the browser for the computed display rather than by reading
         this — which is the whole reason that rule exists. */
      if (ic.dataset.themeIcon === want) ic.removeAttribute('hidden');
      else ic.setAttribute('hidden', '');
    });
    var word = btn.querySelector('.theme-word');
    if (word) word.textContent = want;
  }
  setLabel(saved);
  btn.addEventListener('click', function () {
    var cur = null;
    try { cur = localStorage.getItem(KEY); } catch (e) {}
    var next = ORDER[(ORDER.indexOf(cur === 'light' || cur === 'dark' ? cur : null) + 1)
                     % ORDER.length];
    apply(next);
    setLabel(next);
    try {
      if (next) localStorage.setItem(KEY, next); else localStorage.removeItem(KEY);
    } catch (e) {}
  });
})();

/* ------------------------------------------------------------- inline help ---
   Terms like CYA, LSI and HOCl are load-bearing here and opaque to anyone who
   has not read the chemistry tab. Each is marked up once, explained in place,
   and still readable with JavaScript off because the definition is in the
   title attribute as well. */
(function () {
  var GLOSS = JSON.parse(document.getElementById('glossary').textContent);
  document.querySelectorAll('[data-help]').forEach(function (el) {
    var k = el.dataset.help.toLowerCase();
    if (GLOSS[k]) el.title = GLOSS[k];
  });
})();

/* priv:start */
/* ------------------------------------------------------------------ ask ---
   One question, one answer, nothing stored. Inside priv:start/priv:end
   because /api/ask is a gated route and the public build must not carry a
   call it would be refused for making — the build refuses to write a public
   page containing a fetch of anything outside the three public routes. */
(function () {
  var form = document.getElementById('ask-form');
  if (!form) return;
  var q = document.getElementById('ask-q');
  var msg = document.getElementById('ask-msg');
  var out = document.getElementById('ask-answer');
  var btn = form.querySelector('button[type=submit]');

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (!API) { msg.textContent = 'Needs the write API — start bin/serve.'; return; }
    var text = (q.value || '').trim();
    if (!text) { msg.textContent = 'Ask something first.'; return; }
    btn.disabled = true;
    msg.textContent = 'thinking…'; msg.className = 'fmsg';
    out.hidden = true;
    fetch('/api/ask', {method: 'POST', headers: postHeaders(),
                       body: JSON.stringify({question: text})})
      .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.j.error || 'refused');
        /* textContent, never innerHTML. This string came from a third party
           and lands in a page that can switch a gas heater on. */
        out.textContent = res.j.answer || '';
        out.hidden = false;
        msg.textContent = (res.j.left != null)
          ? res.j.left + ' question(s) left this hour' : '';
        msg.className = 'fmsg ok';
      })
      .catch(function (e) {
        msg.textContent = String(e.message || e); msg.className = 'fmsg bad';
      })
      .then(function () { btn.disabled = false; });
  });
})();
/* priv:end */

/* priv:start */   /* delete a dose */
/* ------------------------------------------------------- delete a dose --- */
document.addEventListener('click', function (e) {
  var b = e.target.closest('[data-del]');
  if (!b || !API) return;
  if (!confirm('Remove this entry from the log? The model will no longer attribute any change to it.')) return;
  b.disabled = true;
  fetch('/api/chemical/delete', {method: 'POST', headers: postHeaders(),
      /* Keyed by id where the row has one: two doses in one minute share a
         ts, and neither could be addressed by it -- both rows were
         permanently undeletable while this button went on being rendered. */
      body: JSON.stringify(b.dataset.delKind === 'id'
                           ? {id: b.dataset.del} : {ts: b.dataset.del})})
    .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
    .then(function (res) {
      if (!res.ok) throw new Error(res.j.error || 'refused');
      location.reload();
    })
    .catch(function (err) { b.disabled = false; alert('Could not remove it: ' + err.message); });
});
/* priv:end */

/* priv:start */   /* doses, by month */
/* ------------------------------------------------------ doses, by month ---
   Adds up what Python already worked out. Each dose arrives from
   upkeep.ledger() with its month, its amount in the unit it is sold by and its
   cost at the price it was priced at; nothing here converts a unit or knows a
   price. This picks the months, groups and sums -- and trims the period to the
   months the log covers, saying so, rather than drawing a year of empty rows
   in front of the first dose. */
(function () {
  var src = document.getElementById('dose-ledger');
  var table = document.getElementById('dm-table');
  if (!src || !table) return;
  var L = JSON.parse(src.textContent);
  var preset = document.getElementById('dm-preset'),
      from = document.getElementById('dm-from'),
      to = document.getElementById('dm-to'),
      note = document.getElementById('dm-note');
  var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep',
             'Oct', 'Nov', 'Dec'];
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function ym(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1); }
  function shift(m, n) {
    var k = (+m.slice(0, 4)) * 12 + (+m.slice(5, 7) - 1) + n;
    return Math.floor(k / 12) + '-' + pad(k % 12 + 1);
  }
  function name(m) { return MON[+m.slice(5, 7) - 1] + ' ' + m.slice(0, 4); }
  /* A month input that the browser does not support is a text box, so what
     comes back is checked for its shape rather than trusted. */
  function ok(m) { return /^[0-9]{4}-(0[1-9]|1[0-2])$/.test(m || ''); }
  var now = ym(new Date());
  function range(p) {
    var y = now.slice(0, 4);
    if (p === 'r12') return [shift(now, -11), now];
    if (p === 'ytd') return [y + '-01', now];
    if (p === 'prev') return [(y - 1) + '-01', (y - 1) + '-12'];
    if (p === 'all') return [L.first || now, now];
    return null;
  }
  /* The unit is in the column's heading, so a cell is the number alone. */
  function qty(v, u) {
    return u === 'gal' ? v.toFixed(2) : (v < 10 ? v.toFixed(1) : v.toFixed(0));
  }
  function money(v) {
    return '$' + v.toLocaleString('en-US', {minimumFractionDigits: 2,
                                            maximumFractionDigits: 2});
  }
  function el(tag, text, cls) {
    var c = document.createElement(tag);
    if (text != null) c.textContent = text;
    if (cls) c.className = cls;
    return c;
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function say(parts) { note.textContent = parts.join(' '); }

  function draw() {
    var a = from.value, b = to.value;
    var head = table.tHead, body = table.tBodies[0], foot = table.tFoot;
    clear(head); clear(body); clear(foot);
    if (!ok(a) || !ok(b)) { say(['Pick a month for both ends of the period, as YYYY-MM.']); return; }
    if (a > b) { var t = a; a = b; b = t; }
    if (!L.first) { say(['Nothing has been logged yet.']); return; }
    var lo = a < L.first ? L.first : a, hi = b > now ? now : b;
    var parts = [lo === hi ? 'Showing ' + name(lo) + '.'
                           : 'Showing ' + name(lo) + ' to ' + name(hi) + '.'];
    if (a < L.first) parts.push('The log begins in ' + name(L.first) + ', so nothing earlier can be shown.');
    if (b > now) parts.push('Months after this one have not happened yet.');
    if (lo > hi) { say(['Nothing was logged between ' + name(a) + ' and ' + name(b) + '.']); return; }

    var inr = L.doses.filter(function (d) { return d.ym >= lo && d.ym <= hi; });
    var keys = L.order.filter(function (k) {
      return inr.some(function (d) { return d.chem === k; });
    });
    var months = [];
    for (var m = lo; m <= hi; m = shift(m, 1)) months.push(m);

    var hr = el('tr');
    hr.appendChild(el('th', 'Month'));
    keys.forEach(function (k) {
      var th = el('th', L.chems[k].name + ' ');
      th.appendChild(el('i', L.chems[k].unit));
      hr.appendChild(th);
    });
    hr.appendChild(el('th', 'Cost'));
    head.appendChild(hr);

    var tq = {}, tc = {}, all = 0, unpriced = 0;
    keys.forEach(function (k) { tq[k] = 0; tc[k] = 0; });
    months.forEach(function (mo) {
      var tr = el('tr'), cost = 0, any = false;
      tr.appendChild(el('td', name(mo)));
      keys.forEach(function (k) {
        var q = 0, seen = false;
        inr.forEach(function (d) {
          if (d.ym !== mo || d.chem !== k) return;
          seen = true; q += d.qty;
          if (d.cost == null) unpriced += 1; else { cost += d.cost; tc[k] += d.cost; }
        });
        tq[k] += q; any = any || seen;
        tr.appendChild(el('td', seen ? qty(q, L.chems[k].unit) : '—', 'n'));
      });
      all += cost;
      tr.appendChild(el('td', any ? money(cost) : '—', 'n'));
      body.appendChild(tr);
    });

    var t1 = el('tr', null, 'dm-total'), t2 = el('tr', null, 'dm-total');
    t1.appendChild(el('th', 'Total'));
    t2.appendChild(el('th', 'Cost'));
    keys.forEach(function (k) {
      t1.appendChild(el('td', qty(tq[k], L.chems[k].unit), 'n'));
      t2.appendChild(el('td', money(tc[k]), 'n'));
    });
    t1.appendChild(el('td', money(all), 'n'));
    t2.appendChild(el('td', ''));
    foot.appendChild(t1); foot.appendChild(t2);

    if (!keys.length) parts.push('Nothing was logged in that period.');
    if (unpriced) parts.push(unpriced + (unpriced === 1 ? ' dose has' : ' doses have') +
                             ' no price and is left out of the cost; enter one in Settings.');
    if (L.skipped) parts.push(L.skipped + ' logged ' + (L.skipped === 1 ? 'entry' : 'entries') +
                              ' could not be read and ' + (L.skipped === 1 ? 'is' : 'are') + ' not counted.');
    say(parts);
  }

  function apply(p) {
    var r = range(p);
    if (r) { from.value = r[0]; to.value = r[1]; }
    draw();
  }
  preset.addEventListener('change', function () { apply(preset.value); });
  [from, to].forEach(function (i) {
    i.addEventListener('change', function () { preset.value = 'custom'; draw(); });
  });
  apply(preset.value);
})();
/* priv:end */


/* ---- the signed-out notice --------------------------------------------
   Shown only when arriving back from a sign-out. It exists because
   oauth2-proxy can clear its own cookie and nothing more: its `rd` parameter
   is whitelisted to the install's own domain, so the sign-out cannot chain
   onward to Microsoft's logout. The Entra session therefore survives, and
   pressing Sign in again lets you straight back in with no prompt.

   On your own machine that is convenient. On a borrowed one it is the
   opposite of what "sign out" implies, so the page says so plainly and
   offers the second step rather than letting somebody assume it happened. */
(function () {
  if (!/[?&]signedout=1/.test(location.search)) return;
  var el = document.createElement('div');
  el.className = 'signedout';
  /* The domain and the return address come from [site] in config.toml, filled
     in at render. They used to be one household's, compiled into every copy. */
  el.innerHTML = '<b>Signed out of poolhound</b>@@signout_scope@@ on ' +
    'this browser. <span>Microsoft still has you signed in, so choosing Sign in ' +
    'again will not prompt you. On a shared computer, ' +
    '<a href="https://login.microsoftonline.com/common/oauth2/v2.0/logout' +
    '@@signout_return@@" ' +
    'rel="nofollow">sign out of Microsoft too</a>.</span>';
  var h = document.querySelector('header');
  if (h && h.parentNode) h.parentNode.insertBefore(el, h.nextSibling);
  /* Take the marker out of the URL so a reload or a shared link does not
     re-announce a sign-out that is not happening again. */
  history.replaceState(null, '', location.pathname + location.hash);
})();


/* ---------------------------------------------------- status on the tabs ---
   A tab strip that looks identical whether or not something needs doing is a
   tab strip nobody checks. Each tab gets a dot when its own panel contains
   something wrong.

   DERIVED FROM THE RENDERED PANELS, not assigned by hand. The tiles already
   carry their verdict as a class, so reading those means the dot cannot
   disagree with the thing it is summarising — a hand-maintained list would
   drift the first time a measure was added.

   NEVER COLOUR ALONE. The dot carries a title, the tab gets an aria-label
   saying what is wrong, and the colour is the third signal rather than the
   only one. "over" is deliberately not counted: it means more than the pool
   needs with nothing going wrong, and colouring it would train people to
   ignore the dot. */
(function () {
  function scan() {
    document.querySelectorAll('[role="tab"]').forEach(function (tab) {
      var panel = document.getElementById('tab-' + tab.dataset.tab);
      if (!panel) return;

      var bad  = panel.querySelectorAll('.s-low, .s-high, .p-low, .p-high').length;
      var warn = panel.querySelectorAll('.v-warn:not([hidden])').length;
      var level = bad ? 'bad' : (warn ? 'warn' : '');

      /* The Home tab already carries a count badge, which says strictly more
         than a dot. Two indicators side by side is clutter that makes the
         reader work out whether they mean different things — they do not. */
      if (tab.querySelector('.badge')) return;

      var dot = tab.querySelector('.tdot');
      if (!level) { if (dot) dot.remove(); tab.removeAttribute('aria-label'); return; }
      if (!dot) {
        dot = document.createElement('span');
        dot.className = 'tdot';
        tab.appendChild(dot);
      }
      dot.className = 'tdot ' + level;
      var what = bad ? (bad + (bad === 1 ? ' reading is' : ' readings are') +
                        ' outside the target')
                     : 'something needs attention';
      dot.title = what;
      var name = (tab.querySelector('.lbl') || tab).textContent.trim();
      tab.setAttribute('aria-label', name + ' — ' + what);
    });
  }
  scan();
  /* The control tab's banners resolve after a fetch, so re-scan once they have. */
  setTimeout(scan, 1500);
})();

/* -------------------------------------------------------------- data tables ---
   Filter, sort and reveal, for every .dtable on the page.

   All of it client-side and all of it over rows already in the document. That
   is a deliberate ceiling: the server renders a bounded slice and says so, so
   this never has to paginate over data it does not have. The moment that stops
   being enough, the answer is an endpoint that queries the CSV — not a bigger
   page.

   Filtering hides rows rather than removing them, so sorting and clearing the
   filter are both reversible without re-reading anything. */
(function () {
  document.querySelectorAll('.dtable').forEach(function (dt) {
    var tbody  = dt.querySelector('tbody');
    if (!tbody) return;

/* priv:start */
    /* ---- reaching past the rendered window --------------------------------
       The count says "showing 40 of 9,463" and the filter says how many rows
       it could not search. Both are honest and neither was a way to SEE those
       rows: the controller writes 96 a day, so the page's reach ran out within
       a fortnight and the only route to last month was downloading the file.

       Rows come back from /api/rows, which is gated at `view` -- hence
       priv:start, since the public build must not carry a call it may not
       make, and the build asserts that. Built with textContent: these are the
       household's own CSV cells, but they are still values from a file and the
       row builder beside this one had to be rewritten once for exactly that. */
    var reach = dt.querySelector('.dt-reach');
    if (reach) {
      var fetchBtn = reach.querySelector('.dt-fetch');
      var saidEl   = reach.querySelector('.dt-fetched');
      var csvName  = dt.dataset.csv || '';
      fetchBtn.addEventListener('click', function () {
        var from = (reach.querySelector('.dt-from') || {}).value || '';
        var to   = (reach.querySelector('.dt-to')   || {}).value || '';
        if (!from && !to) {
          saidEl.textContent = 'pick a date first';
          return;
        }
        saidEl.textContent = 'fetching\u2026';
        fetchBtn.disabled = true;
        var qs = '?file=' + encodeURIComponent(csvName)
               + (from ? '&since=' + encodeURIComponent(from) : '')
               + (to   ? '&until=' + encodeURIComponent(to)   : '');
        fetch('/api/rows' + qs, {cache: 'no-store'})
          .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
          .then(function (j) {
            var cols = [].slice.call(dt.querySelectorAll('thead th'))
                         .map(function (th) { return th.dataset.field || ''; });
            tbody.textContent = '';
            j.rows.forEach(function (row) {
              var tr = document.createElement('tr');
              cols.forEach(function (c) {
                var td = document.createElement('td');
                td.textContent = row[c] == null ? '' : String(row[c]);
                tr.appendChild(td);
              });
              tbody.appendChild(tr);
            });
            rows = [].slice.call(tbody.rows);
            /* Say what came back, including when it was capped -- the server
               reports `truncated` rather than leaving the page to infer it
               from a count that happens to equal the limit. */
            saidEl.textContent = j.truncated
              ? ('showing ' + j.returned + ' of ' + j.matched.toLocaleString()
                 + ' in that range \u2014 narrow the dates to see the rest')
              : (j.matched.toLocaleString() + ' row'
                 + (j.matched === 1 ? '' : 's') + ' in that range');
            if (more) more.hidden = true;
          })
          .catch(function (e) {
            saidEl.textContent = (e === 401 || e === 403)
              ? 'this account may not read the history'
              : 'could not fetch those rows';
          })
          .then(function () { fetchBtn.disabled = false; });
      });
    }
/* priv:end */

    var rows   = [].slice.call(tbody.rows);
    var filter = dt.querySelector('.dt-filter');
    var more   = dt.querySelector('.dt-more');
    var none   = dt.querySelector('.dt-none');
    var shown  = dt.querySelector('.dt-shown');
    var truthEl  = dt.querySelector('.dt-truth');
    var searchEl = dt.querySelector('.dt-search');
    var limit  = parseInt(dt.dataset.show, 10) || 40;
    var all    = false;
    var sortCol = -1, sortDir = 1;

    /* Row text cached once. Recomputing textContent per keystroke across a few
       hundred rows is the difference between a filter that feels instant and
       one that stutters as you type. */
    rows.forEach(function (r) { r._t = r.textContent.toLowerCase(); });

    function apply() {
      var q = (filter ? filter.value : '').trim().toLowerCase();
      var terms = q ? q.split(/\\s+/) : [];
      var seen = 0;
      rows.forEach(function (r) {
        var hit = terms.every(function (t) { return r._t.indexOf(t) !== -1; });
        if (!hit) { r.hidden = true; return; }
        seen++;
        /* A filter shows everything it matches. Capping filtered results at the
           same limit would hide the very rows somebody just searched for. */
        r.hidden = (!all && !q && seen > limit);
      });
      var visible = rows.filter(function (r) { return !r.hidden; }).length;
      var total = parseInt(dt.dataset.total, 10) || rows.length;
      var drawn = parseInt(dt.dataset.rendered, 10) || rows.length;

      /* A SEARCH THAT FINDS SOMETHING MUST SAY WHAT IT SEARCHED.
         The honesty below was written for the empty result and fired nowhere
         else, so filtering 11,520 rows for "2026-09-1" returned five days of
         hits under a bar still reading "showing the most recent 400 of 400
         rendered · 11,520 on file" — a sentence about the UNFILTERED render.
         Zero results warn you; partial results look successful, which makes
         them the more dangerous case. While a filter is active the count
         describes the filter. */
      if (truthEl && searchEl) {
        if (q && visible > 0) {
          var smsg = visible.toLocaleString() +
                     (visible === 1 ? ' match' : ' matches');
          if (total > drawn) {
            smsg += ' in the ' + drawn.toLocaleString() + ' most recent rows — ' +
                    (total - drawn).toLocaleString() + ' older rows were not searched.';
            if (dt.dataset.csv) {
              smsg += ' Download the CSV to search all ' + total.toLocaleString() + '.';
            }
          } else {
            smsg += ' of ' + total.toLocaleString() + '.';
          }
          searchEl.textContent = smsg;
        } else if (q) {
          /* The whole explanation is in .dt-none directly below; saying it
             twice in two places is worse than saying it once. */
          searchEl.textContent = 'no matches';
        }
        truthEl.hidden = !!q;
        searchEl.hidden = !q;
      }

      if (none) {
        none.hidden = visible !== 0;
        /* "Nothing matches that filter." was a lie on the table that grows:
           filtering searches the rows IN THE PAGE, which is the most recent
           window, so a date older than it answered as though the pool had
           recorded nothing that day. Say which rows were actually searched,
           and point at the file that holds the rest. */
        if (visible === 0) {
          var msg = 'Nothing matches that filter';
          if (total > drawn) {
            msg += ' in the ' + drawn.toLocaleString() + ' most recent rows — ' +
                   'the other ' + (total - drawn).toLocaleString() + ' are on file ' +
                   'but not in this page.';
            if (dt.dataset.csv) {
              msg += ' Download the CSV to search all ' + total.toLocaleString() + '.';
            }
          } else {
            msg += '.';
          }
          none.textContent = msg;
        }
      }
      if (shown) shown.textContent = visible;
      if (more) more.hidden = !!q;
    }

    if (filter) filter.addEventListener('input', apply);

    if (more) more.addEventListener('click', function () {
      all = !all;
      more.textContent = all ? 'Show fewer' : 'Show all';
      apply();
    });

    dt.querySelectorAll('th[data-col]').forEach(function (th) {
      function sort() {
        var col = +th.dataset.col;
        sortDir = (col === sortCol) ? -sortDir : 1;
        sortCol = col;
        dt.querySelectorAll('th[data-col]').forEach(function (o) {
          o.removeAttribute('aria-sort');
        });
        th.setAttribute('aria-sort', sortDir === 1 ? 'ascending' : 'descending');

        rows.sort(function (a, b) {
          var x = a.cells[col].textContent.trim();
          var y = b.cells[col].textContent.trim();
          /* Blanks sink, whichever way the column is sorted. A missing reading
             is not the smallest reading, and letting empties win the ascending
             sort buries the actual minimum under rows that say nothing — which
             is exactly what somebody sorting a column is looking for. */
          if (x === '' && y === '') return 0;
          if (x === '') return 1;
          if (y === '') return -1;
          /* Numbers compare as numbers; everything else as text. A column of
             pump watts sorted lexically puts 9 after 1000, which looks like a
             broken table rather than a sorting choice. */
          var nx = parseFloat(x), ny = parseFloat(y);
          var numeric = !isNaN(nx) && !isNaN(ny) &&
                        /^-?[\\d.]+$/.test(x) && /^-?[\\d.]+$/.test(y);
          if (numeric) return (nx - ny) * sortDir;
          return x.localeCompare(y) * sortDir;
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
        apply();
      }
      th.addEventListener('click', sort);
      th.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); sort(); }
      });
    });

    apply();
  });
})();

/* ------------------------------------------------------- the running total ---
   Pool and spa are measured separately and by whichever method suits each: a
   spa is nearly always a manufactured shell with a diameter somebody can read
   off a tape, while the pool is the thing worth tracing. So the two can come
   from different tools, and on different photographs, and this is where they
   are added up.

   Kept in the page rather than on the server because nothing here is saved
   until somebody presses the button that saves it. A visitor working out a
   volume for a pool that is not this one gets the same arithmetic and leaves
   no trace. */
(function () {
  var card = document.getElementById('vol-total');
  if (!card) return;

  var state = {pool: null, spa: null};

  function fmt(n) { return Math.round(n).toLocaleString(); }

  /* ---- what it costs to put the water back ----
     The rate UNITS come from pool_shape.REFILL_RATE_UNITS through a JSON
     block, not from numbers typed here: a bill quotes per CCF, a CCF is a
     hundred cubic feet of water, and how many gallons that is already has an
     owner. No conversion is spelled out in this file -- a selftest asserts
     that, so the digits are deliberately absent rather than merely absent. */
  var rateEl = document.getElementById('vol-rate');
  var unitEl = document.getElementById('vol-rate-unit');
  var UNITS = [];
  try {
    var ru = document.getElementById('refill-units');
    if (ru) UNITS = JSON.parse(ru.textContent);
  } catch (e) { UNITS = []; }

  if (unitEl && UNITS.length && !unitEl.options.length) {
    UNITS.forEach(function (u) {
      var o = document.createElement('option');
      o.value = u.key;
      o.textContent = u.label;
      unitEl.appendChild(o);
    });
  }

  /* Remembered per browser, because somebody checking two pool designs should
     not retype their water rate. Wrapped because storage throws in a private
     window, and the page has to render the same either way. */
  function remember() {
    try {
      localStorage.setItem('ph.rate', rateEl.value);
      localStorage.setItem('ph.rateUnit', unitEl.value);
    } catch (e) { /* a forgotten rate is not a broken page */ }
  }
  try {
    var r0 = localStorage.getItem('ph.rate');
    var u0 = localStorage.getItem('ph.rateUnit');
    if (rateEl && r0) rateEl.value = r0;
    if (unitEl && u0) unitEl.value = u0;
  } catch (e) { /* ditto */ }

  function gallonsPer(key) {
    for (var i = 0; i < UNITS.length; i++) {
      if (UNITS[i].key === key) return UNITS[i].gallons;
    }
    return null;
  }

  function money(n) {
    /* Cents below a hundred dollars, whole dollars above it. A refill is
       rarely precise to the penny and "$1,247" reads faster than
       "$1,246.83" — but at $4.20 the pennies are the whole figure. */
    return '$' + (n < 100
      ? n.toFixed(2)
      : Math.round(n).toLocaleString());
  }

  function paintCost(gal) {
    var el = card.querySelector('[data-t=cost]');
    if (!el) return;
    var rate = rateEl ? parseFloat(rateEl.value) : NaN;
    var per = unitEl ? gallonsPer(unitEl.value) : null;
    /* A DASH, NOT $0.00, until there is something to say. A confident zero
       over an empty rate field is a figure somebody could believe. */
    if (!(gal > 0) || !(rate >= 0) || isNaN(rate) || !per) {
      el.textContent = '—';
      return;
    }
    el.textContent = money(gal / per * rate);
  }

  function paint() {
    var p = state.pool, s = state.spa;
    var elP = card.querySelector('[data-t=pool]');
    var elS = card.querySelector('[data-t=spa]');
    var elT = card.querySelector('[data-t=total]');
    var note = card.querySelector('.vt-note');

    elP.textContent = p === null ? '—' : fmt(p.gal);
    elS.textContent = s === null ? '—' : fmt(s.gal);

    if (p === null && s === null) {
      elT.textContent = '—';
      note.textContent = 'Work out the pool above, and the spa if there is one. ' +
                         'Each answer lands here.';
      /* ON THIS PATH TOO. An early return that skipped the cost would leave
         the last figure on screen after Clear, next to a total reading "—". */
      paintCost(0);
      return;
    }
    var totalGal = (p ? p.gal : 0) + (s ? s.gal : 0);
    elT.textContent = fmt(totalGal);
    /* The SAME total the row above shows, not a second sum of the same two
       numbers — the cost and the volume cannot disagree. */
    paintCost(totalGal);

    if (p === null) {
      note.textContent = 'Spa only so far — the pool figure is still missing, so ' +
                         'this is not a pool volume yet.';
    } else if (s === null) {
      note.textContent = 'Pool only. If there is a spa plumbed into the same water, ' +
                         'work it out above and it will be added here.';
    } else {
      note.textContent = 'A spillover spa shares one body of water with the pool, so ' +
                         'every dose is diluted by the total and this is the number ' +
                         'to use. A spa isolated behind a valve is a second body of ' +
                         'water — dose it on its own figure, not this one.';
    }
  }

  /* Called by whichever calculator just produced a figure. Last one wins per
     kind, deliberately: re-tracing an outline is how you correct it, and an
     average of your first attempt and your corrected one would be worse than
     either. */
  window.recordVolume = function (kind, gallons, method) {
    if (!(gallons > 0)) return;
    state[kind] = {gal: gallons, method: method || ''};
    paint();
    card.classList.add('vt-live');
  };

  var reset = card.querySelector('.vt-reset');
  if (reset) reset.addEventListener('click', function () {
    state.pool = state.spa = null;
    card.classList.remove('vt-live');
    paint();
  });

  /* Typing a rate repaints the cost without touching the volume. `input`
     rather than `change`, so the figure moves as the digits arrive. */
  if (rateEl) rateEl.addEventListener('input', function () {
    remember(); paint();
  });
  if (unitEl) unitEl.addEventListener('change', function () {
    remember(); paint();
  });

  paint();
})();

/* --------------------------------------------------------- the spa shell ---
   Scales a chosen shell to the measurements given and asks the server for the
   volume, the same way the pool tools do. The arithmetic is NOT repeated here:
   the browser sends an outline in feet and the answer comes back from
   pool_shape.py, so the figure on screen and the figure that could be saved
   cannot disagree.

   A spa is sent as `spa` rather than as the main outline, because the server
   models it differently — flat floor, perimeter bench — and feeding it through
   the pool path would apply a depth slope no spa has. */
(function () {
  /* The spa section is two tools now, mirroring the pool: a shell picker and a
     photograph. Scope to the section rather than one tool, or the picker's
     handlers disappear the moment the markup is split again. */
  var wrap = document.getElementById('vol-spa') || document.getElementById('spa-design-tool');
  if (!wrap) return;
  var SPA = JSON.parse((document.getElementById('spa-templates') || {}).textContent || '{}');

  var chosen = null;
  var note   = document.getElementById('spa-note');
  var calc   = document.getElementById('spa-calc');
  var save   = document.getElementById('spa-save');
  var msg    = document.getElementById('spa-msg');
  var out    = document.getElementById('spa-result');
  var ver    = document.getElementById('spa-verdict');

  function val(id) { return parseFloat((document.getElementById(id) || {}).value) || 0; }

  function record() {
    /* A traced rim beats a stock shell: somebody who went to the trouble of
       tracing meant it, and the shell is the fallback for when they did not. */
    var traced = window.__spaTrace && window.__spaTrace();
    if (traced) {
      return {points: [], scale_ft_per_px: 1, unit_feet: true,
              spa: {points: traced, unit_feet: true, depth_ft: val('s-depth'),
                    bench_width_ft: val('s-bench-w') / 12,
                    bench_depth_ft: val('s-bench-d') / 12}};
    }
    var t = SPA[chosen];
    var L = val('s-length'), W = val('s-width');
    /* Feet on both axes, so a round shell asks for the diameter twice rather
       than pretending one number describes an ellipse. */
    var pts = t.pts.map(function (p) { return [p[0] * L, p[1] * W]; });
    return {
      /* No pool outline: this is the spa on its own, and the server has a path
         for exactly that. Sending a token pool would make it compute a pool
         volume of nearly zero and add it to the answer. */
      points: [],
      scale_ft_per_px: 1, unit_feet: true,
      spa: {
        points: pts, unit_feet: true,
        depth_ft: val('s-depth'),
        bench_width_ft: val('s-bench-w') / 12,
        bench_depth_ft: val('s-bench-d') / 12
      }
    };
  }

  function spaNeed(m) {
    var n = document.getElementById('spa-need');
    if (n) { n.textContent = m || ''; n.hidden = !m; }
  }

  function preview() {
    if (window.__spaTrace && window.__spaTrace()) {
      out.innerHTML = '<span class="ph">Rim traced and scaled. Set the depth and the ' +
        'bench, then calculate.</span>';
      var okDepth = val('s-depth') > 0;
      calc.disabled = !okDepth;
      spaNeed(okDepth ? '' : 'Set the water depth.');
      return;
    }
    if (!chosen) {
      calc.disabled = true;
      spaNeed('Pick a shell above, or trace the rim on a photograph.');
      return;
    }
    var t = SPA[chosen];
    var L = val('s-length'), W = val('s-width'), d = val('s-depth');
    if (!(L > 0 && W > 0 && d > 0)) {
      out.innerHTML = '<span class="ph">Set the measurements and the depth.</span>';
      spaNeed('Give it a size across, the other way, and a water depth.');
      calc.disabled = true; return;
    }
    spaNeed('');
    /* A local estimate only, and labelled as one — the bench is not in it. It
       exists so the box is not blank while you type, not to be the answer. */
    var area = t.unit_area * L * W;
    var rough = area * d * 7.48052;
    out.innerHTML = '<div class="eff"><i>About</i><b>' +
      Math.round(rough).toLocaleString() + '</b>gallons before the bench</div>' +
      '<div class="eff"><b>' + Math.round(area) + '</b>sq ft of water surface</div>' +
      '<span class="ph">Calculate for the figure that accounts for the bench.</span>';
    calc.disabled = false;
  }

  wrap.querySelectorAll('.spa-design').forEach(function (b) {
    b.addEventListener('click', function () {
      chosen = b.dataset.spaDesign;
      wrap.querySelectorAll('.spa-design').forEach(function (o) {
        o.setAttribute('aria-pressed', o === b ? 'true' : 'false');
      });
      note.innerHTML = '<b>' + SPA[chosen].name + '.</b> ' + SPA[chosen].note;
      preview();
    });
  });

  ['s-length', 's-width', 's-depth', 's-bench-w', 's-bench-d'].forEach(function (id) {
    var e = document.getElementById(id);
    if (e) e.addEventListener('input', preview);
  });


  /* ------------------------------------------------ tracing the spa itself ---
     Its own canvas, because a spa can be nowhere near the pool. Forcing one
     photograph to hold both is how you end up measuring a spa eleven pixels
     across, and the scale that suits a 32 ft pool is the wrong scale for a 7 ft
     shell even when they ARE in the same frame.

     A small tracer rather than a second copy of the pool one: no detection, no
     camera geometry, no rectification, no depth model — a spa is one depth and
     the shape is a rim. Everything it does not need is everything that made the
     pool tool large. */
  var sCanvas = document.getElementById('spa-canvas');
  if (sCanvas) (function () {
    var ctx = sCanvas.getContext('2d');
    var img = null, pts = [], scalePts = [], mode = 'trace';
    var stage = document.getElementById('spa-stage');
    var pmsg = document.getElementById('spa-photo-msg');
    var hint = document.getElementById('spa-hint');
    var ftIn = document.getElementById('spa-scale-ft');

    function setMode(m) {
      mode = m;
      document.getElementById('spa-mode-trace').setAttribute('aria-pressed', m === 'trace');
      document.getElementById('spa-mode-scale').setAttribute('aria-pressed', m === 'scale');
      hint.textContent = m === 'trace'
        ? 'Click around the rim of the spa.'
        : 'Click two points you have measured, then type the distance.';
    }

    function draw() {
      ctx.clearRect(0, 0, sCanvas.width, sCanvas.height);
      if (img) ctx.drawImage(img, 0, 0, sCanvas.width, sCanvas.height);
      function poly(list, colour, close) {
        if (!list.length) return;
        ctx.lineWidth = 2; ctx.strokeStyle = colour; ctx.fillStyle = colour;
        ctx.beginPath();
        list.forEach(function (p, i) { i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]); });
        if (close && list.length > 2) ctx.closePath();
        ctx.stroke();
        list.forEach(function (p) {
          ctx.beginPath(); ctx.arc(p[0], p[1], 4, 0, 7); ctx.fill();
        });
      }
      /* PAINT, not literals. The pool tracer beside this one resolves all
         sixteen of its canvas colours through PAINT, so they follow the theme
         and stay tied to style.SERIES -- the single source the palette is
         validated as a set from. This canvas was added later and hardcoded
         two, so the spa outline and its scale line paint the light theme's hex
         on a dark page, and would silently keep the OLD colour if the series
         were re-picked. "Re-picking one in isolation means re-running
         bin/contrast" only works if nothing holds a private copy. */
      poly(pts, PAINT.spa(), true);
      poly(scalePts, PAINT.sheer(), false);
    }

    function load(src, label) {
      var i = new Image();
      i.onload = function () {
        img = i; stage.hidden = false;
        pts = []; scalePts = [];
        /* The canvas keeps the photograph's aspect so a rim traced on it is the
           rim, not a stretched one. */
        sCanvas.height = Math.round(900 * (i.naturalHeight / i.naturalWidth));
        draw(); setMode('trace');
        pmsg.textContent = label; pmsg.className = 'fmsg ok';
        preview();
      };
      i.onerror = function () { pmsg.textContent = 'could not read that image'; pmsg.className = 'fmsg bad'; };
      i.src = src;
    }

    document.getElementById('spa-use-pool-photo').addEventListener('click', function () {
      var pool = document.getElementById('shape-canvas');
      if (!pool || !window.__poolPhotoSrc) {
        pmsg.textContent = 'load a photograph in the pool section first';
        pmsg.className = 'fmsg bad'; return;
      }
      load(window.__poolPhotoSrc, 'using the pool photograph');
    });

    document.getElementById('spa-file').addEventListener('change', function (e) {
      var f = e.target.files && e.target.files[0];
      if (!f) return;
      pmsg.textContent = 'reading…'; pmsg.className = 'fmsg';
      var fr = new FileReader();
      fr.onload = function () { load(fr.result, 'using a separate photograph of the spa'); };
      fr.readAsDataURL(f);
    });

    sCanvas.addEventListener('click', function (e) {
      if (!img) return;
      var r = sCanvas.getBoundingClientRect();
      var p = [(e.clientX - r.left) * sCanvas.width / r.width,
               (e.clientY - r.top) * sCanvas.height / r.height];
      if (mode === 'trace') pts.push(p);
      else { if (scalePts.length >= 2) scalePts = []; scalePts.push(p); }
      draw(); preview();
    });

    document.getElementById('spa-mode-trace').addEventListener('click', function () { setMode('trace'); });
    document.getElementById('spa-mode-scale').addEventListener('click', function () { setMode('scale'); });
    document.getElementById('spa-undo').addEventListener('click', function () {
      (mode === 'trace' ? pts : scalePts).pop(); draw(); preview();
    });
    document.getElementById('spa-clear').addEventListener('click', function () {
      pts = []; scalePts = []; draw(); preview();
    });
    if (ftIn) ftIn.addEventListener('input', preview);

    /* Exposed so the shell picker and the tracer feed the same calculation:
       whichever produced an outline last is the one that counts. */
    window.__spaTrace = function () {
      if (pts.length < 3 || scalePts.length < 2) return null;
      var d = Math.hypot(scalePts[0][0] - scalePts[1][0], scalePts[0][1] - scalePts[1][1]);
      var ft = parseFloat(ftIn.value) || 0;
      if (!(d > 0 && ft > 0)) return null;
      var k = ft / d;
      return pts.map(function (p) { return [p[0] * k, p[1] * k]; });
    };
  })();

  calc.addEventListener('click', function () {
    if (!API) return;
    msg.textContent = 'calculating…'; msg.className = 'fmsg';
    fetch('/api/pool-shape/compute', {method: 'POST', headers: postHeaders(),
                                      body: JSON.stringify(record())})
      .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.j.error || 'refused');
        var s = res.j.spa;
        if (!s) throw new Error('the server did not return a spa figure');
        ver.hidden = false;
        ver.innerHTML =
          '<div class="v-head"><b>' + Math.round(s.gallons).toLocaleString() +
          ' gal</b><span>the spa on its own</span></div>' +
          '<div class="v-split"><span class="eff"><b>' + Math.round(s.area_sqft) +
          '</b>sq ft of water</span><span class="eff"><b>' +
          Math.round(s.bench_sqft) + '</b>sq ft of bench, ' +
          Math.round(s.bench_sqft / s.area_sqft * 100) + '% of it</span></div>' +
          '<p class="v-why">Treated as a plain box it would read ' +
          Math.round(s.as_box_gallons).toLocaleString() + ' gal — ' +
          Math.round((s.as_box_gallons / s.gallons - 1) * 100) +
          '% high. The bench is the difference, and it is why spa volumes are ' +
          'usually overstated.</p>';
        msg.textContent = save ? 'calculated — not saved yet' : 'calculated';
        msg.className = 'fmsg ok';
        if (save) save.disabled = false;
        if (window.recordVolume) window.recordVolume('spa', s.gallons, 'shell');
        ver.scrollIntoView({behavior: 'smooth', block: 'center'});
      })
      .catch(function (e) { msg.textContent = String(e.message || e); msg.className = 'fmsg bad'; });
  });
})();

/* ------------------------------------------------- pending control state ---
   A command can succeed and the page still show the old state, because what
   the page shows is the last SAMPLE and samples arrive every fifteen minutes.
   For up to a quarter of an hour a switch that was just turned on reads as
   off — which looks exactly like a command that failed, and is the single
   most misleading thing this interface could do.

   So anything asked for but not yet seen in a reading is marked waiting, in
   the same amber everywhere it appears: the control card, the pill on it, and
   the strip on Home. The server works out which devices those are by comparing
   the command log against the newest sample; the browser only paints it. */
(function () {
  /* Paints BOTH facts: what is still waiting, and what the panel currently
     says. It used to paint only "waiting" and lean on location.reload() to
     deliver the confirmed state — which made the whole mechanism depend on a
     page load landing and being fresh. A browser holding the pre-command
     render then showed "waiting" for ever with nothing wrong anywhere else.
     Painting the confirmed state directly removes that dependency, and keeps
     scroll position and anything half-typed. */
  function paint(pending, states, setpoints) {
    pending = pending || {};
    states  = states  || {};
    /* The setpoint VALUES, which are a different question from the on/off
       states beside them and used to be compared against them. */
    setpoints = setpoints || {};

    function set(el, state, label) {
      if (!el) return;
      el.className = 'st st-' + state;
      el.textContent = label;
    }

    /* WHAT THIS PAGE ASKED FOR OUTRANKS WHAT THE SERVER REMEMBERS.
       `marked` is what somebody pressed in THIS tab. A device stays waiting
       until the panel's own reported state matches what was asked — not until
       the server stops listing it as pending.

       Both symptoms came from trusting the server's list alone. Between
       pressing a control and the server registering the command there is a
       window where it is absent from `pending`, and the stale sample in
       `states` then overwrote "waiting" with the OLD value — a control that
       had just been switched on read as off. The same list is lost entirely
       when the container restarts, since it lives in memory. And in the other
       direction a reported state can run ahead of the fixture, which is how a
       light showed on before it visibly was.

       Matching on the value is the only rule that is right in all three
       cases. */
    function stateOf(dev) {
      var want = marked[dev];
      var got  = states[dev];
      if (want !== undefined) {
        if (got === undefined) {
          /* No column reports this device, so there is nothing to match
             against; defer to the server, which expires them. */
          return pending[dev] !== undefined ? ['pending', want] : null;
        }
        if (String(got) !== String(want)) return ['pending', want];
        return [got, got];              /* the panel agrees: confirmed */
      }
      if (pending[dev] !== undefined) return ['pending', pending[dev]];
      if (got) return [got, got];
      return null;
    }

    /* A setpoint is confirmed by the panel reporting THAT SETPOINT back, not by
       its on/off column. This compared the commanded '88' against the heater's
       state 'on', never matched, and left the card reading "set to 88, waiting"
       until the twenty-minute cap while the panel had agreed within a second. */
    function setpointOf(dev) {
      var want = markedSet[dev];
      var got  = setpoints[dev];
      if (want === undefined) return null;
      if (got === undefined || got === '') {
        return pending[dev] !== undefined ? ['pending', want] : null;
      }
      return Number(got) === Number(want) ? ['ok', got] : ['pending', want];
    }

    document.querySelectorAll('.ctl[data-device]').forEach(function (row) {
      var dev = row.dataset.device;
      var isSet = row.classList.contains('set');

      if (isSet) {
        /* The on/off badge keeps saying whether the heater is RUNNING. It used
           to be overwritten by setpoint progress, so for twenty minutes the one
           card that could answer "is the most expensive thing in the pool on"
           answered a different question instead. */
        var st = states[dev];
        if (st !== undefined) {
          row.dataset.state = st;
          set(row.querySelector('.st'), st, st === 'unknown' ? 'no reading' : st);
        }
        /* "now 88" was baked into the file by whichever process rendered it and
           never refreshed, so an open tab showed an arbitrarily old setpoint --
           the rule CLAUDE.md states, broken for exactly the four numbers added
           to make setpoints readable. */
        var cur = setpoints[dev];
        var note = row.querySelector('.c-note');
        if (note && cur !== undefined) {
          if (cur === '') { note.textContent = 'setpoint not reported'; note.className = 'c-note dim'; }
          else            { note.textContent = 'now ' + cur;            note.className = 'c-note'; }
          row.dataset.current = cur;
        }
        /* Refill the box only when nobody is typing in it and nothing is
           waiting -- overwriting a half-typed number would be worse than a
           stale one. */
        var inp = row.querySelector('.ctl-val');
        if (inp && document.activeElement !== inp && markedSet[dev] === undefined
            && cur !== undefined && cur !== '') inp.value = cur;

        var r = setpointOf(dev);
        var wait = row.querySelector('.sp-wait');
        if (wait) {
          if (!r)                  { wait.hidden = true; }
          else if (r[0] === 'ok')  { wait.hidden = false; wait.className = 'st st-on sp-wait';
                                     wait.textContent = 'set to ' + r[1]; }
          else                     { wait.hidden = false; wait.className = 'st st-pending sp-wait';
                                     wait.textContent = 'set to ' + r[1] + ', waiting'; }
        }
        return;
      }

      var r2 = stateOf(dev);
      if (!r2) return;
      row.dataset.state = r2[0];
      set(row.querySelector('.st'), r2[0],
          r2[0] === 'pending' ? 'set to ' + r2[1] + ', waiting'
        : r2[0] === 'unknown' ? 'no reading' : r2[1]);
    });

    /* The strip on Home carries the control name alongside the sample column,
       so this needs no second mapping table. */
    document.querySelectorAll('.nowstrip .st[data-device]').forEach(function (chip) {
      var r = stateOf(chip.dataset.device);
      if (!r) return;
      chip.className = 'st st-' + r[0];
      var i = chip.querySelector('i');
      if (i) i.textContent = r[0] === 'pending' ? r[1] + ', waiting'
                           : r[0] === 'unknown' ? 'no reading' : r[1];
    });
  }

  fetch('/api/health', {cache: 'no-store'})
    .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
    .then(function (j) {
      paint(j.pending, j.states, (j.states || {}).setpoints);
      /* Painted once and then abandoned, which is how a command issued from
         another tab, another device, or before this page loaded came to sit
         amber for ever. The poll below only ever started from markPending(),
         so "waiting" that this tab did not itself create was never watched by
         anything and never cleared. Adopt the server's list and watch it the
         same way. */
      adoptPending(j.pending);
    })
    .catch(function () {});

  /* Pressing a button marks its own card immediately rather than waiting for
     the next /api/health. The optimistic mark is safe because it only ever
     says "waiting", never "on" — the confirmed state still comes from a
     reading, and if the command silently did nothing this reverts to the real
     state on the next load rather than lying persistently. */
  /* Which devices this page has optimistically marked. A single boolean was
     wrong: it guarded ONE poll loop, so pressing a second control while the
     first was still confirming marked the second pending and started nothing
     to watch it — and when the first resolved the flag cleared, leaving the
     second painted "waiting" for ever with no poll left running. A set, and
     one loop that runs while the set is non-empty. */
  var marked = {};
  /* Setpoint marks are kept apart from switch marks because they are confirmed
     against a different field. Merging them is what let '88' be compared to
     'on'. */
  var markedSet = {};
  var looping = false;

  function loop(t0) {
    if (Object.keys(marked).length === 0 && Object.keys(markedSet).length === 0) {
      looping = false; return; }
    /* The server's number, shipped into the page rather than a second one
       typed here. This watch ran for 20 minutes against a server that gives up
       after 4, so the tab that sent a command claimed it was still waiting for
       sixteen minutes after every other view had stopped saying so. */
    if (Date.now() - t0 > PENDING_GIVES_UP_MS) {
      /* Giving up is a result too, and the sentence beside the badge has to
         hear about it -- otherwise it keeps promising a confirmation that is
         no longer coming. */
      Object.keys(marked).concat(Object.keys(markedSet)).forEach(function (d) {
        note(d, false);
      });
      marked = {}; markedSet = {}; looping = false; return; }

    /* Fast at first, then slow down. The Pi reads the panel back and pushes a
       reading within a second or two of obeying, so the answer is usually
       already there by the second check. A minute between checks would make a
       one-second confirmation feel like a one-minute one; checking fast for
       ever would hammer the server all night over a command that did not take. */
    var age = Date.now() - t0;
    var wait = age < 30000 ? 1500 : (age < 180000 ? 10000 : 60000);

    fetch('/api/health', {cache: 'no-store'})
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (j) {
        /* Paint on EVERY check, not only at the end: the confirmed state lands
           in the page the moment the panel reports it, with no reload, so
           nothing depends on a page load being fresh. */
        paint(j.pending, j.states, (j.states || {}).setpoints);
        /* Drop a device only when the PANEL agrees with what was asked. The
           server dropping it from `pending` is not enough — it does that on a
           restart too, and on the four-minute give-up. */
        var sps = (j.states || {}).setpoints || {};
        Object.keys(markedSet).forEach(function (d) {
          if (sps[d] !== undefined && sps[d] !== ''
              && Number(sps[d]) === Number(markedSet[d])) {
            delete markedSet[d]; note(d, true);
          }
        });
        var st = j.states || {};
        Object.keys(marked).forEach(function (d) {
          if (st[d] !== undefined && String(st[d]) === String(marked[d])) {
            delete marked[d]; note(d, true);
          } else if (st[d] === undefined &&
                     (!j.pending || j.pending[d] === undefined)) {
            /* No column reports this device, so the server dropping it is the
               only signal there is. That is not the panel agreeing, and the
               message must not claim it was. */
            delete marked[d]; note(d, null);
          }
        });
        setTimeout(function () { loop(t0); }, wait);
      })
      .catch(function () { setTimeout(function () { loop(t0); }, wait); });
  }

  /* ---------------------------------------- the command log, fetched ---
     Rendered empty on purpose. It used to be baked in from the rendering
     process's in-memory ring, and cron renders from a DIFFERENT process whose
     ring is empty -- so twice a day the page wiped the history and announced a
     restart that had not happened.

     Two sources, labelled. The session ring carries what the agent replied,
     which audit.csv does not; audit.csv survives the restart, which the ring
     does not. Showing only one of them is how the table came to promise a
     durable record it gave nobody a way to read. */
/* priv:start */
  /* The command log is an authenticated-build panel. paint() and the Home strip
     above it are deliberately shared -- the public page shows equipment state --
     but this fetches a gated endpoint for a section the public build does not
     contain, so it is stripped rather than left to 403 into a dead table. */
  /* THE PAGE CURSOR LIVES OUT HERE, and that is not a style choice. Declared
     inside loadHistory() it was reset to 1 by the very function the Older
     button calls, so the table redrew page one for ever and the button looked
     broken rather than being broken. */
  var page = 1;

  function loadHistory() {
    var tb = document.getElementById('ctl-hist');
    var note = document.getElementById('ctl-hist-note');
    if (!tb) return;

    /* Built as DOM nodes with textContent, not as an HTML string. Every value
       here came off the wire -- a command's `by` is an identity from a proxy
       header and a refusal is the agent's own words -- and there is no escaper
       in this script to reach for. textContent cannot be talked into markup. */
    /* A CELL IS A STRING OR {cls, text}, AND THE TEST WAS ON THE WRONG KEY.
       `if (c && c.cls)` asks whether the cell carries a CLASS, not whether it
       is an object -- so {cls: '', text: 'no reply recorded'} fell through to
       String(c) and the table printed "[object Object]". That is precisely the
       cell a command with no recorded acknowledgement produces, which is every
       command in audit.csv whose ack is outside the window. Measured in
       Chromium on the row a restart leaves behind. */
    function row(cells, cls) {
      var tr = document.createElement('tr');
      if (cls) tr.className = cls;
      cells.forEach(function (c) {
        var td = document.createElement('td');
        if (c && typeof c === 'object') {
          if (c.cls) td.className = c.cls;
          td.textContent = c.text == null ? '' : String(c.text);
        } else td.textContent = c == null ? '' : String(c);
        tr.appendChild(td);
      });
      return tr;
    }

    /* Retried once. This server speaks HTTP/1.0 -- it cannot speak 1.1 while
       the agent's SSE stream sends no Content-Length -- so every response closes
       its socket, and a browser reusing a connection for the burst of fetches
       this page fires at load loses one of them outright (ERR_EMPTY_RESPONSE).
       The pending poll never noticed because it retries on a timer; a one-shot
       load did, and reported "the command log could not be read" on nearly
       every visit while the endpoint was answering perfectly. One retry, then
       the honest message. */
    function ask(tries) {
      return fetch('/api/commands' + params(), {cache: 'no-store'})
        .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
        .catch(function (e) {
          if (tries > 0 && typeof e !== 'number') {
            return new Promise(function (res) { setTimeout(res, 250); }).then(function () {
              return ask(tries - 1);
            });
          }
          return Promise.reject(e);
        });
    }

    /* THE SERVER MERGES, FILTERS AND PAGINATES, and this draws what comes
       back. It used to merge three sources here and cap each one separately --
       which is fine until there is a filter, and then it is not: a filter
       applied to the slice the page happens to hold searches part of the
       record and reports a count for the whole of it. See switchlog.activity.

       The cells arrive composed, for the same reason the control banners are
       rendered in Python: one language per sentence. */
    function params() {
      var g = function (id) {
        var el = document.getElementById(id);
        return el ? el.value : '';
      };
      var q = ['page=' + page];
      if (g('ctl-f-device')) q.push('device=' + encodeURIComponent(g('ctl-f-device')));
      if (g('ctl-f-how')) q.push('how=' + encodeURIComponent(g('ctl-f-how')));
      /* "all" is the absence of a window, not a value to send. */
      if (g('ctl-f-days') && g('ctl-f-days') !== 'all') {
        q.push('days=' + encodeURIComponent(g('ctl-f-days')));
      }
      return '?' + q.join('&');
    }

    ask(1)
      .then(function (j) {
        var a = j.activity || {rows: [], matched: 0, total: 0, page: 1, pages: 1,
                               from: 0, to: 0};
        tb.textContent = '';
        a.rows.forEach(function (r) {
          tb.appendChild(row([r.when, r.what, r.how, r.by,
                              {cls: r.cls, text: r.result}], r.dim ? 'dim' : ''));
        });
        if (!a.rows.length) {
          var tr = document.createElement('tr');
          var td = document.createElement('td');
          td.colSpan = 5; td.className = 'empty';
          /* TWO EMPTY STATES, BECAUSE THEY MEAN DIFFERENT THINGS. "nothing
             matched" is a filter to widen; "nothing happened" is a claim about
             the pool. Rendering one sentence for both is how a filter comes to
             look like an outage. */
          td.textContent = a.total
            ? 'No activity matches these filters — ' + a.total
              + ' change(s) are in the record.'
            : 'No circuit has changed state in the record — nothing through '
              + 'poolhound, and nothing the fifteen-minute samples caught at '
              + 'the panel.';
          tr.appendChild(td); tb.appendChild(tr);
        }
        var pg = document.getElementById('ctl-page');
        if (pg) {
          pg.textContent = a.matched
            ? 'Showing ' + a.from + '–' + a.to + ' of ' + a.matched
              + (a.matched === a.total ? '' : ' matching, out of ' + a.total)
              + ' · page ' + a.page + ' of ' + a.pages
            : '';
        }
        var prev = document.getElementById('ctl-prev');
        var next = document.getElementById('ctl-next');
        /* DISABLED, not hidden: a control that vanishes at the end of the list
           moves the one beside it under the pointer. */
        if (prev) prev.disabled = a.page <= 1;
        if (next) next.disabled = a.page >= a.pages;
        page = a.page;
        if (note) {
          note.textContent =
            'The process log is held in memory and starts empty after a '
            + 'restart; audit.csv on the share is the record that survives, and '
            + 'changes made at the panel are read back out of samples.csv. '
            + (a.note || '');
        }
      })
      .catch(function (e) {
        tb.textContent = '';
        var tr = document.createElement('tr');
        var td = document.createElement('td');
        td.colSpan = 5; td.className = 'empty';
        td.textContent = 'The switch log could not be read'
          + (e === 403 ? ' — this account may not see it.' : '.');
        tr.appendChild(td); tb.appendChild(tr);
      });
  }

  /* WIRED ONCE, NOT ON EVERY LOAD. loadHistory() runs again on every filter
     change and after every command, so binding inside it would stack a
     listener per reload and fire the fetch 2, 4, 8 times. A flag on the
     element is the cheapest honest guard. */
  ['ctl-f-device', 'ctl-f-how', 'ctl-f-days'].forEach(function (id) {
    var el = document.getElementById(id);
    if (!el || el.dataset.wired) return;
    el.dataset.wired = '1';
    /* Back to the first page: page 7 of an unfiltered log is rarely a page of
       the filtered one, and landing past the end would read as "nothing
       matched" when the filter had matched plenty. */
    el.addEventListener('change', function () { page = 1; loadHistory(); });
  });
  [['ctl-prev', -1], ['ctl-next', 1]].forEach(function (pair) {
    var el = document.getElementById(pair[0]);
    if (!el || el.dataset.wired) return;
    el.dataset.wired = '1';
    el.addEventListener('click', function () {
      page = Math.max(1, page + pair[1]);
      loadHistory();
    });
  });

  loadHistory();
  window.reloadHistory = loadHistory;
/* priv:end */

  /* The one place that tells a control form how its command ended.
     `agreed` is true when the PANEL reported what was asked, false when the
     twenty-minute watch expired without that, and null when the device reports
     no state at all and there is nothing to confirm against. Three outcomes,
     three sentences -- rather than one sentence written at send time and never
     revisited. */
  function note(device, agreed) {
    if (window.notePanelResult) window.notePanelResult(device, agreed);
  }

  /* A command this tab did not send is still a command somebody is waiting on.
     Seeds the watch from the server's own pending list so the amber badge that
     /api/health painted on load is actually followed to its conclusion. */
  function adoptPending(pending) {
    if (!pending) return;
    var found = false;
    document.querySelectorAll('.ctl[data-device]').forEach(function (row) {
      var dev = row.dataset.device;
      if (pending[dev] === undefined) return;
      if (row.classList.contains('set')) {
        if (markedSet[dev] === undefined) { markedSet[dev] = String(pending[dev]); found = true; }
      } else if (marked[dev] === undefined) {
        marked[dev] = String(pending[dev]); found = true;
      }
    });
    if (found && !looping) { looping = true; loop(Date.now()); }
  }

  window.markPending = function (device, want, isSetpoint) {
    var o = {}; o[device] = want;
    if (isSetpoint) { markedSet[device] = want; paint(o, null, null); }
    else            { marked[device] = want;    paint(o, null, null); }
    if (looping) return;
    looping = true;
    loop(Date.now());
  };
})();


/* --------------------------------------------------- the identity menu ---
   Clicking your own name is where people look for the way out, so that is
   what it does. The menu closes on Escape, on a click elsewhere and on
   losing focus, because a popover that only closes by reopening it is the
   kind of thing that gets left hanging over the page. */
(function () {
  var btn = document.getElementById('whoami');
  var menu = document.getElementById('whomenu');
  if (!btn || !menu) return;

  function open(on) {
    menu.hidden = !on;
    btn.setAttribute('aria-expanded', on ? 'true' : 'false');
  }
  btn.addEventListener('click', function (e) {
    e.stopPropagation();
    open(menu.hidden);
  });
  document.addEventListener('click', function (e) {
    if (!menu.hidden && !menu.contains(e.target)) open(false);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !menu.hidden) { open(false); btn.focus(); }
  });
})();

/* priv:start */
/* ------------------------------------------------------ lab corrections ---
   Recording that a reading is wrong. The field row only appears for the one
   action that needs it, because three inputs that are irrelevant two thirds of
   the time is how a form teaches people to ignore it. */
(function () {
  var save = document.getElementById('corr-save');
  if (!save) return;
  var act = document.getElementById('corr-action');
  var setrow = document.getElementById('corr-setrow');
  var msg = document.getElementById('corr-msg');

  function sync() { setrow.hidden = act.value !== 'set'; }
  act.addEventListener('change', sync); sync();

  save.addEventListener('click', function () {
    if (!API) { msg.textContent = 'Needs the write API.'; msg.className = 'fmsg bad'; return; }
    var body = {
      source:   document.getElementById('corr-source').value,
      measured: document.getElementById('corr-measured').value.trim(),
      action:   act.value,
      field:    document.getElementById('corr-field').value.trim(),
      value:    document.getElementById('corr-value').value.trim(),
      note:     document.getElementById('corr-note').value.trim()
    };
    if (!body.measured) {
      msg.textContent = 'Which reading? Copy its date from the table.';
      msg.className = 'fmsg bad'; return;
    }
    /* A drop changes what every chart and every target is computed from, so it
       is worth one sentence of friction. */
    if (body.action === 'drop' && !confirm(
          'This reading will be left out of the tiles, the charts, the targets ' +
          'and the tables.\\n\\nThe original stays in the file and this can be ' +
          'undone.\\n\\nIgnore the reading from ' + body.measured + '?')) return;

    msg.textContent = 'recording…'; msg.className = 'fmsg';
    fetch('/api/lab-correction', {method: 'POST', headers: postHeaders(),
                                  body: JSON.stringify(body)})
      .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.j.error || 'refused');
        if (wroteOk(msg, res.j, 'recorded')) {
          setTimeout(function () { location.reload(); }, 900);
        }
      })
      .catch(function (e) { msg.textContent = String(e.message || e); msg.className = 'fmsg bad'; });
  });
})();

/* --------------------------------------------------------- editing a dose ---
   Unlike a lab result this row is OUR record of something we did, so it is
   edited in place: nothing will put a wrong amount back. Clicking Edit loads
   the row into the form above rather than opening a second one, so there is
   one set of inputs and one set of validation. */
(function () {
  var form = document.getElementById('chem-form');
  if (!form) return;

  document.querySelectorAll('.dose-edit').forEach(function (b) {
    b.addEventListener('click', function () {
      var r = b.closest('tr');
      var g = function (n) { return (r.querySelector('[data-f=' + n + ']') || {}).textContent || ''; };
      form.querySelector('[name=chemical]').value = b.dataset.chemical || '';
      form.querySelector('[name=amount]').value = b.dataset.amount || '';
      form.querySelector('[name=unit]').value = b.dataset.unit || '';
      var pct = form.querySelector('[name=pct]'); if (pct) pct.value = b.dataset.pct || '';
      var note = form.querySelector('[name=note]'); if (note) note.value = b.dataset.note || '';
      /* The field is name="ts" (panels.py). This looked for [name=when], matched
         nothing, and left the box showing the render-time "now" while the
         message beside it named the dose being edited — then dropped the typed
         time on submit and said "changed". */
      var when = form.querySelector('[name=ts]'); if (when) when.value = (b.dataset.ts || '').slice(0, 16);
      form.dataset.editing = b.dataset.ts;
      /* The row's own id, which is what the server matches on. Kept beside the
         timestamp rather than replacing it: the timestamp is what the message
         below shows a person, and what they may edit. */
      if (b.dataset.id) { form.dataset.editingId = b.dataset.id; }
      else { delete form.dataset.editingId; }
      var sub = form.querySelector('button[type=submit]');
      if (sub) sub.textContent = 'Save the change';
      var m = document.getElementById('chem-msg');
      if (m) { m.textContent = 'editing the dose from ' + (b.dataset.ts || '').slice(0, 16) +
                               ' — press Escape to start a new one instead';
               m.className = 'fmsg'; }
      form.scrollIntoView({behavior: 'smooth', block: 'center'});
      form.querySelector('[name=amount]').focus();
    });
  });

  /* Escape abandons the edit. Without it the only way out of edit mode is to
     save something, which is a trap when you opened it by mistake. */
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' || !form.dataset.editing) return;
    delete form.dataset.editing;
    delete form.dataset.editingId;
    var sub = form.querySelector('button[type=submit]');
    if (sub) sub.textContent = 'Log it';
    var m = document.getElementById('chem-msg');
    if (m) { m.textContent = 'new dose'; m.className = 'fmsg'; }
  });
})();
/* priv:end */

/* ------------------------------------------------- write API, if it is up ---
   The forms are useless without somewhere to write. Rather than show a Save
   button that silently does nothing when the page is opened off disk, ask once
   and let the CSS switch the forms into their command-line equivalent. */
/* Three different facts, kept apart because they used to be one.
   API      the server answered, so the calculator's public routes will work
   CANWRITE we hold a session token, so a gated write will be accepted
   PUBLIC   this is the public deployment rather than a workstation
   Conflating the first two told an anonymous visitor "saving enabled" while
   every write they attempted would be refused; separating them keeps the
   calculator working for everybody, which is the point of it being public. */
var API = false, TOKEN = null, CANWRITE = false, PUBLIC = false, ROLE = 'view';
/* The levels in order, from access.LEVELS, so the page and the server cannot
   disagree about which of them outranks which. RANK is set once the answer
   arrives; until then the page assumes the least, because a control enabled
   before we know is a control enabled for somebody who may not use it. */
var LEVELS = @@role_levels@@, RANK = 0;
/* How long the server will go on calling a command "waiting". commands.py owns
   it; this is the same number, not a second opinion. */
var PENDING_GIVES_UP_MS = @@pending_gives_up_ms@@;
fetch('/api/health', {cache: 'no-store'})
  .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
  .then(function (j) {
    API = true;
    /* The token is minted per server process and handed out only here. A
       cross-origin page cannot read this response — no CORS headers are sent —
       which is what makes requiring it on writes a real defence rather than a
       formality. */
    TOKEN = j.token;
    CANWRITE = !!j.token;
    PUBLIC = !!j.public;
    /* What this reader may DO, as opposed to whether they are signed in. The
       page is one file for everybody -- a render serves all of them -- so it
       cannot be built per person and has to ask. The server refuses regardless;
       this only stops somebody pressing a button that was always going to be
       refused, the same relationship the two builds have with the proxy. An
       older server sends no role, and 'admin' keeps it behaving as it did. */
    /* Clamped to the levels the server declares. This used to fall back to the
       HIGHEST level when the answer was not one it recognised, and the two
       tests below it named only the two levels it knew -- so any other value
       locked NOTHING and left every control on the page enabled. A fourth level
       added to the server, a typo in a config, a truncated response: each of
       them read as full access. The point of asking is to be more careful than
       the page can be on its own, and it was less.
       (The selftest asserts the old spelling is absent from this file, so it is
       described here rather than quoted -- a comment naming it would fail the
       very check that keeps it gone.) */
    ROLE = LEVELS.indexOf(j.role) >= 0 ? j.role : 'view';
    RANK = LEVELS.indexOf(ROLE);
    document.body.classList.add(CANWRITE ? 'has-api' : 'no-api');
    document.body.classList.add('role-' + ROLE);
    /* THREE LEVELS, NOT TWO. This tested only for 'view', so an `operate` user
       -- who may switch the spa and log a dose but not change settings or touch
       credentials -- was shown Settings, Notifications and the credential store
       fully enabled, with "saving enabled" in the status line, and then got
       `403 this needs admin access and you have operate` on submit. The server
       was right; the page was the half that lied.

       Disabled rather than hidden, deliberately: a control that is missing reads
       as a product that cannot do the thing, and somebody who may not change a
       setting should still see that the setting exists. The refusal is still the
       boundary -- this only stops a press that was always going to be refused. */
    /* DERIVED, NOT TYPED. These two strings were written by hand here, and a
       hand-written list of "every control that writes" is wrong the moment
       somebody adds one -- three already were: the Refresh control, which
       spends WaterGuru API calls; the lab-correction form, which changes what
       every chart and every target is computed from; and the calculator's two
       Save buttons, which set the volume every dose is figured against. Each
       was live for a `view` reader and answered 403 on press, which reads as a
       broken button rather than as a permission.
       They now come from access.ROLE_UI, keyed by route, and the build refuses
       to render if a gated route the script fetches has no entry there. */
    var OPERATE = @@role_operate@@;
    var ADMIN   = @@role_admin@@;
    /* A LATCH, NOT A ONE-SHOT.
       This ran once, when /api/health answered, and set .disabled = true. Six
       later handlers set .disabled = false on the same elements and each undid
       it for a reader who may not use them:
         - the agent-connection poll re-enables every control button the moment
           an agent connects, so a `view` reader's whole control panel came back
           to life a few seconds after it was dimmed -- the worst of the six, and
           the one that made the dimming look like it worked;
         - the calculator's two Save buttons ship disabled and are enabled once
           a volume has been computed, so a `view` reader who drew a shape got a
           live "Use it as the pool volume";
         - the refresh menu's per-source options, the credential "forget"
           button, and the dose-delete button on its error path.
       Ordering cannot fix this -- the re-enables are asynchronous and happen
       whenever the pool's state changes. So the element itself refuses: an own
       accessor that reports disabled and swallows writes, shadowing the
       prototype's. The other handlers go on doing what they do and it has no
       effect. The refusal at the server is still the boundary; this is the
       courtesy actually behaving like one. */
    function lockOut(sel) {
      document.querySelectorAll(sel).forEach(function (el) {
        el.disabled = true;
        try {
          Object.defineProperty(el, 'disabled', {
            configurable: true,
            get: function () { return true; },
            set: function () {}
          });
        } catch (e) { /* the plain disable above still stands */ }
      });
    }
    /* An anchor has no .disabled -- setting it is a silent no-op, which is how a
       view-only reader kept a working "download the whole history" link. The
       download links are hidden instead.

       Selected by CLASS, not by href. Naming the export route here put its
       literal path into the shared page script and the public build refused to
       be written -- correctly: that assertion exists so a gated endpoint cannot
       be spelled out in a file served to anonymous readers, and the right
       answer is to stop spelling it rather than to spell it more cleverly.
       (The first version of this comment named the route too, and was caught
       by the same check. The assertion does not care why the string is there.) */
    function hideDownloads() {
      document.querySelectorAll('a.dt-get').forEach(
        function (el) { el.hidden = true; });
    }
    /* Stated as what this reader MAY do, not as what each named role may not.
       The old spelling tested `=== 'view'` and `=== 'operate'` and so left an
       unknown role with everything enabled; asking whether we are at least
       `operate` locks the unknown out by default, which is the direction the
       server already fails in (a route missing from NEEDS requires admin). */
    if (RANK < LEVELS.indexOf('operate')) lockOut(OPERATE);
    if (RANK < LEVELS.indexOf('admin'))   { lockOut(ADMIN); hideDownloads(); }
    /* The note says which level, because it used to say "view-only access" to
       everybody below admin -- so an `operate` reader, looking at a Pool
       control tab that was fully enabled FOR THEM, was told they could not use
       it. A sentence contradicting the controls beside it is worse than no
       sentence: it teaches people the page is wrong about permissions, which
       is the one thing it has to be believed about. */
    if (RANK < LEVELS.indexOf('admin')) {
      document.querySelectorAll('.role-note').forEach(function (el) {
        var w = el.querySelector('.role-note-what');
        if (w) w.textContent = (ROLE === 'view')
          ? 'view-only access: you can read this tab and change nothing on it.'
          : 'operate access: you can act on the pool, and not change settings, '
          + 'credentials or the stored history.';
        el.hidden = false;
      });
    }
    /* "AM I SIGNED IN" IS ASKED BEFORE "WHAT MAY I DO". This tested ROLE first
       and fell through to the read-only cases last, which was survivable only
       while an unknown role defaulted to 'admin'. The moment that default was
       corrected to 'view' -- to stop an unrecognised role unlocking everything
       -- this line began telling every anonymous visitor to the PUBLIC site
       "signed in - view only". They are not signed in, and the page saying so
       is exactly the kind of thing that makes a reader distrust everything else
       on it. Two independent facts were being read off one variable.

       CANWRITE is the right first question: it means a session token was
       issued, which the server does only for somebody it will accept writes
       from. A workstation (`local`, not "signed in", but holding a token)
       therefore still reads "saving enabled", as it did before. */
    var s = document.getElementById('api-state');
    if (s) s.textContent =
          !CANWRITE            ? (PUBLIC ? 'read-only'
                                         : 'read-only (run bin/serve to enable saving)')
        : (ROLE === 'view')    ? 'signed in \u2014 view only'
        : (ROLE === 'operate') ? 'signed in \u2014 pool and doses, not settings'
        :                        'saving enabled';
    /* Show who the proxy says you are. Not decoration: if this is blank on a
       page reached over the internet, the Entra layer in front is not passing
       an identity and every write is about to be refused — far easier to
       understand here than as a 401 after filling in a form.

       THE SERVER SAYS WHETHER YOU ARE SIGNED IN; the page does not work it out
       from the name. This compared j.user against the loopback sentinel,
       which is a test the OTHER not-a-person answer also passes — so an
       anonymous visitor saw that word where a username goes and was handed
       a sign-out control, which the comment just below already called out
       as the thing not to do. Five Python call sites had the same bug; this
       was the sixth, and the only one on this side of the wire. */
    var w   = document.getElementById('whoami');
    var box = document.getElementById('whobox');
    if (w && j.signed_in && j.user) {
      w.textContent = j.user;
      /* The name IS the control. Revealed only once there is a session to end —
         a sign-out shown to somebody who is not signed in is a dead control
         that makes the page look broken. */
      if (box) box.hidden = false;
    } else if (w && j.public) {
      w.textContent = 'not signed in';
      w.className = 'whoami warn';
      w.disabled = true;
      if (box) box.hidden = false;
    }
  })
  .catch(function () {
    document.body.classList.add('no-api');
    var s = document.getElementById('api-state');
    /* Two different readers see this. On a workstation it means somebody has
       not started the write API; on the public site it means the visitor is
       not signed in, and telling THEM to run bin/serve is advice about a
       machine they do not have. The calculator is offered to anybody, so the
       public wording has to make sense to anybody. */
    if (s) s.textContent = PUBLIC ? 'read-only' : 'read-only (run bin/serve to enable saving)';
  });

function postHeaders() {
  return {'Content-Type': 'application/json', 'X-Poolhound-Token': TOKEN || ''};
}

/* A write can succeed while the re-render that follows it fails. The server
   reports that in render_error — and nothing on either page read the field, so
   the browser announced success and then reloaded onto a page that did not
   contain the change. Reloading is the part that must not happen: it presents
   stale content as confirmation, and the owner's natural response is to enter it
   again, which CLAUDE.md notes damages the fit worse than the missing row did.

   Returns true when it is safe to reload. */
function wroteOk(el, j, done) {
  if (j && j.render_error) {
    el.textContent = done + ', but the page could not be rebuilt — reload to see '
                   + 'the current state (' + j.render_error + ')';
    el.className = 'fmsg bad';
    return false;
  }
  el.textContent = done + ' — reloading';
  el.className = 'fmsg ok';
  return true;
}

/* priv:start */   /* chemicals form */
/* -------------------------------------------------------- chemicals form ---
   The effect preview multiplies factors exported by chemicals.py rather than
   reimplementing the arithmetic, so the browser and the CLI cannot disagree
   about what a dose does. Every effect in that catalogue is linear in strength,
   which is asserted on the Python side. */
(function () {
  var form = document.getElementById('chem-form');
  if (!form) return;
  var CAT = JSON.parse(document.getElementById('catalogue').textContent);
  var sel = document.getElementById('chem-select'),
      amt = document.getElementById('chem-amount'),
      unit = document.getElementById('chem-unit'),
      pct = document.getElementById('chem-pct'),
      plabel = document.getElementById('pct-label'),
      blurb = document.getElementById('chem-blurb'),
      out = document.getElementById('chem-preview'),
      cli = document.getElementById('chem-cli'),
      msg = document.getElementById('chem-msg');

  function chem() { return CAT.chemicals[sel.value]; }

  function fillUnits() {
    var c = chem();
    var keep = unit.value;
    unit.innerHTML = c.units.map(function (u) {
      return '<option value="' + u + '">' + u + '</option>';
    }).join('');
    /* "oz" means fluid ounces for a liquid and weight ounces for a solid, so it
       is offered for both — but a unit that does not apply is never offered. */
    unit.value = c.units.indexOf(keep) >= 0 ? keep
               : (c.phase === 'liquid' ? 'floz' : 'lb');
    pct.value = c.default_pct;
    plabel.textContent = c.pct_label.charAt(0).toUpperCase() + c.pct_label.slice(1);
    blurb.textContent = c.blurb;
  }

  function preview() {
    var c = chem();
    var table = c.phase === 'liquid' ? CAT.volume_units : CAT.weight_units;
    var base = parseFloat(amt.value) * (table[unit.value] || 0);
    var strength = parseFloat(pct.value);
    if (!isFinite(base) || base <= 0 || !isFinite(strength) || strength <= 0) {
      out.innerHTML = '<span class="ph">Enter an amount and a strength.</span>';
      return;
    }
    var scale = base * (strength / c.ref_pct);
    var parts = Object.keys(c.per_unit).map(function (k) {
      var lab = CAT.effect_labels[k] || {name: k, fmt: '{:+.2f}'};
      var v = c.per_unit[k] * scale;
      /* Given, not guessed. These used to be recovered from the Python format
         spec by substring search, which quietly rounds to the wrong place the
         first time a measure wants three decimals or a unit that is not ppm.
         The || branches keep an older catalogue working. */
      var dp = (lab.decimals !== undefined) ? lab.decimals
             : (lab.fmt.indexOf('.0f') > 0 ? 0 : (lab.fmt.indexOf('.1f') > 0 ? 1 : 2));
      var unitTxt = (lab.unit !== undefined) ? (lab.unit ? ' ' + lab.unit : '')
             : (lab.fmt.indexOf('ppm') > 0 ? ' ppm' : '');
      return '<span class="eff"><b>' + (v > 0 ? '+' : '') + v.toFixed(dp) + unitTxt +
             '</b>' + lab.name + '</span>';
    });
    /* AN EMPTY EFFECT MAP IS NOT AN EFFECT OF ZERO. A phosphate remover has no
       predicted change, because how much it removes depends on how much is
       there to bind. Rendering the empty list prints nothing, which reads as
       "this does nothing" — the opposite of what the product does. The
       catalogue carries the reason and it is shown instead.
       textContent, not innerHTML: this string comes from the catalogue, but so
       did every other string that has ever been interpolated into a page. */
    if (!parts.length && c.no_effect) {
      out.textContent = 'No estimate — ' + c.no_effect;
      return;
    }
    /* Never quote an effect "on 15,000 gal" to somebody who has not said their
       pool is 15,000 gallons. The figure every dose multiplies through is the
       one thing here that cannot be guessed on the reader's behalf. */
    if (CAT.gallons_known === false) {
      out.innerHTML = '<span class="pl">Set your pool volume in Settings before ' +
        'logging a dose — every figure here multiplies through it.</span>';
      cli.textContent = '';
      return;
    }
    out.innerHTML = '<span class="pl">Estimated effect on ' +
      CAT.gallons.toLocaleString() + ' gal</span>' + parts.join('');
    cli.textContent = 'bin/chem ' + sel.value + ' ' + amt.value + ' ' + unit.value +
      ' --pct ' + pct.value;
  }

  sel.addEventListener('change', function () { fillUnits(); preview(); });
  [amt, unit, pct].forEach(function (el) {
    el.addEventListener('input', preview);
    el.addEventListener('change', preview);
  });
  fillUnits(); preview();

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (!API) return;
    var data = {};
    new FormData(form).forEach(function (v, k) { data[k] = v; });
    /* One form, two destinations. The edit endpoint runs the same catalogue
       checks as logging, so an edit cannot write a unit the CLI would refuse. */
    var editing = form.dataset.editing;
    var url = editing ? '/api/chemical/edit' : '/api/chemical';
    /* Order matters: read what was typed BEFORE ts is overwritten with the row
       key. The server implements new_ts fully, duplicate refusal included — only
       the page could not reach it. */
    if (editing) {
      if (data.ts && data.ts !== editing) { data.new_ts = data.ts; }
      data.ts = editing;
      if (form.dataset.editingId) { data.id = form.dataset.editingId; }
    }
    msg.textContent = 'saving…'; msg.className = 'fmsg';
    fetch(url, {method: 'POST', headers: postHeaders(),
                body: JSON.stringify(data)})
      .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.j.error || 'refused');
        if (wroteOk(msg, res.j, editing ? 'changed' : 'logged')) {
          setTimeout(function () { location.reload(); }, 600);
        }
      })
      .catch(function (err) {
        msg.textContent = String(err.message || err); msg.className = 'fmsg bad';
      });
  });
})();
/* priv:end */



/* ------------------------------------------------- finding the water itself ---
   Pool water in a daylit photograph is the one large region that is decisively
   blue: decking is grey or tan, lawn is green, and shadow is dark but not blue.
   That is enough to segment on directly, with no model and nothing downloaded —
   threshold for blue, group the pixels that touch, and walk the boundary of each
   group.

   The spa falls out of the same pass rather than needing its own. It is water,
   so it is found; it is small and round, so it is told apart from the pool by
   size and compactness rather than by colour. Where a spillway joins the two
   they may arrive as one region, which is what the waist detection downstream is
   for.

   Everything it produces is an ordinary editable outline. The detector is a way
   to avoid forty clicks, not an authority — a reflection of sky in a window can
   look exactly like water, and the person looking at their own pool is the one
   who can say so. */
var Detect = (function () {

  function waterMask(d, w, h) {
    var m = new Uint8Array(w * h), n = 0;
    for (var i = 0, p = 0; i < m.length; i++, p += 4) {
      var r = d[p], g = d[p + 1], b = d[p + 2];
      var mx = Math.max(r, g, b), mn = Math.min(r, g, b);
      var sat = mx === 0 ? 0 : (mx - mn) / mx;
      /* Blue must actually dominate, not merely be present: grey decking has
         r≈g≈b and would otherwise pass on a cool white balance. */
      var blueLead = b - Math.max(r, g);
      var cyan = (b + g) / 2 - r;
      /* The saturation floor sat at 0.18 and a pale sunlit spa measured 0.20 on
         the test image — close enough that half its pixels fell out and the rest
         did not survive erosion. Small pale features are exactly what a spa is. */
      var ok = (blueLead > 12 || cyan > 26) && sat > 0.15 && mx > 40 && mx < 252;
      /* Sky is blue, large, and often the biggest blue thing in a photograph,
         so pale bright blue is refused. But the first version of this rule threw
         away a sunlit spa along with it: bright turquoise reads as high value
         and modest saturation, exactly like haze.
         What separates them is GREEN. Pool water is cyan — measured on a real
         satellite image, the spa ran rgb(185,228,232), a green lead of +43 over
         red — while pale sky keeps red and green close. So brightness alone no
         longer condemns a pixel; it has to be uncyan as well. */
      if (ok && mx > 205 && sat < 0.34 && (g - r) < 30) ok = false;

      /* Rooftop solar is the other blue thing in an overhead shot of a house,
         and it passes every colour test water does — rgb(84,107,146) on that
         same image. It is much darker than sunlit water, so a floor on
         brightness separates them without touching a pool in shade, which still
         reads far lighter than a panel. */
      if (ok && mx < 155 && (g - r) < 45) ok = false;
      if (ok) { m[i] = 1; n++; }
    }
    return {mask: m, count: n};
  }

  /* How rippled a region is. Water in daylight carries surface texture and
     specular glitter; sky and a painted wall do not. Measured as the mean
     absolute Laplacian inside the region, which needs no extra passes over the
     image and tells the two apart when colour alone cannot. */
  function texture(d, w, h, lab, id) {
    var sum = 0, n = 0;
    for (var y = 1; y < h - 1; y += 2) {
      for (var x = 1; x < w - 1; x += 2) {
        var i = y * w + x;
        if (lab[i] !== id) continue;
        var c = (d[i * 4] + d[i * 4 + 1] + d[i * 4 + 2]) / 3;
        var s = 0;
        s += (d[(i - 1) * 4] + d[(i - 1) * 4 + 1] + d[(i - 1) * 4 + 2]) / 3;
        s += (d[(i + 1) * 4] + d[(i + 1) * 4 + 1] + d[(i + 1) * 4 + 2]) / 3;
        s += (d[(i - w) * 4] + d[(i - w) * 4 + 1] + d[(i - w) * 4 + 2]) / 3;
        s += (d[(i + w) * 4] + d[(i + w) * 4 + 1] + d[(i + w) * 4 + 2]) / 3;
        sum += Math.abs(s - 4 * c); n++;
      }
    }
    return n ? sum / n / 4 : 0;
  }

  /* Remove single stray pixels and close pinholes, so a sun-glint on the water
     does not punch a hole in the outline and a speck of blue tile does not
     become its own "pool". */
  function morph(m, w, h, op, passes) {
    for (var k = 0; k < (passes || 1); k++) {
      var out = new Uint8Array(m.length);
      for (var y = 1; y < h - 1; y++) {
        for (var x = 1; x < w - 1; x++) {
          var i = y * w + x, c = 0;
          for (var dy = -1; dy <= 1; dy++)
            for (var dx = -1; dx <= 1; dx++)
              c += m[i + dy * w + dx];
          out[i] = op === 'erode' ? (c === 9 ? 1 : 0) : (c > 0 ? 1 : 0);
        }
      }
      m = out;
    }
    return m;
  }

  function components(m, w, h, minPx) {
    var lab = new Int32Array(m.length), next = 1, out = [];
    var stack = new Int32Array(m.length);
    for (var s = 0; s < m.length; s++) {
      if (!m[s] || lab[s]) continue;
      var top = 0, count = 0, id = next++;
      stack[top++] = s; lab[s] = id;
      var minx = w, maxx = 0, miny = h, maxy = 0, seed = s;
      while (top > 0) {
        var i = stack[--top]; count++;
        var x = i % w, y = (i - x) / w;
        if (x < minx) { minx = x; }
        if (x > maxx) maxx = x;
        if (y < miny) { miny = y; seed = i; }
        if (y > maxy) maxy = y;
        if (x > 0     && m[i - 1] && !lab[i - 1]) { lab[i - 1] = id; stack[top++] = i - 1; }
        if (x < w - 1 && m[i + 1] && !lab[i + 1]) { lab[i + 1] = id; stack[top++] = i + 1; }
        if (y > 0     && m[i - w] && !lab[i - w]) { lab[i - w] = id; stack[top++] = i - w; }
        if (y < h - 1 && m[i + w] && !lab[i + w]) { lab[i + w] = id; stack[top++] = i + w; }
      }
      if (count >= minPx) out.push({id: id, px: count, seed: seed,
                                    bbox: [minx, miny, maxx, maxy]});
    }
    return {labels: lab, blobs: out};
  }

  /* Moore-neighbour boundary tracing. Walks the outside edge of one region,
     which gives the points in order — a set of edge pixels would not. */
  function contour(lab, w, h, id, seed) {
    var D = [[1,0],[1,1],[0,1],[-1,1],[-1,0],[-1,-1],[0,-1],[1,-1]];
    var sx = seed % w, sy = (seed - sx) / w;
    var pts = [], cx = sx, cy = sy, dir = 6, guard = 0;
    do {
      pts.push([cx, cy]);
      var found = false;
      for (var k = 0; k < 8; k++) {
        var nd = (dir + 6 + k) % 8;
        var nx = cx + D[nd][0], ny = cy + D[nd][1];
        if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
        if (lab[ny * w + nx] === id) { cx = nx; cy = ny; dir = nd; found = true; break; }
      }
      if (!found) break;
    } while ((cx !== sx || cy !== sy) && ++guard < 400000);
    return pts;
  }

  /* Douglas-Peucker: keep the points that carry the shape, drop the ones that
     only carry pixel noise. A traced boundary is one point per pixel; a pool
     outline needs a few dozen. */
  function simplify(pts, eps) {
    if (pts.length < 3) return pts;
    var keep = new Uint8Array(pts.length); keep[0] = keep[pts.length - 1] = 1;
    var stack = [[0, pts.length - 1]];
    while (stack.length) {
      var seg = stack.pop(), a = seg[0], b = seg[1];
      var ax = pts[a][0], ay = pts[a][1], bx = pts[b][0], by = pts[b][1];
      var dx = bx - ax, dy = by - ay, len = Math.hypot(dx, dy) || 1;
      var worst = -1, wi = -1;
      for (var i = a + 1; i < b; i++) {
        var d = Math.abs(dy * pts[i][0] - dx * pts[i][1] + bx * ay - by * ax) / len;
        if (d > worst) { worst = d; wi = i; }
      }
      if (worst > eps && wi > 0) { keep[wi] = 1; stack.push([a, wi], [wi, b]); }
    }
    var out = [];
    for (var j = 0; j < pts.length; j++) if (keep[j]) out.push(pts[j]);
    return out;
  }

  function shoelaceAbs(p) {
    var s = 0;
    for (var i = 0; i < p.length; i++) {
      var a = p[i], b = p[(i + 1) % p.length];
      s += a[0] * b[1] - b[0] * a[1];
    }
    return Math.abs(s) / 2;
  }
  function periOf(p) {
    var s = 0;
    for (var i = 0; i < p.length; i++) {
      var a = p[i], b = p[(i + 1) % p.length];
      s += Math.hypot(b[0] - a[0], b[1] - a[1]);
    }
    return s;
  }

  function run(img) {
    /* Work small: segmentation gains nothing from twelve megapixels and the
       flood fill would crawl. Coordinates are scaled back at the end. */
    var scale = Math.min(1, 700 / Math.max(img.width, img.height));
    var w = Math.max(1, Math.round(img.width * scale));
    var h = Math.max(1, Math.round(img.height * scale));
    var c = document.createElement('canvas'); c.width = w; c.height = h;
    var x = c.getContext('2d', {willReadFrequently: true});
    x.drawImage(img, 0, 0, w, h);
    var d = x.getImageData(0, 0, w, h).data;

    var wm = waterMask(d, w, h);
    if (wm.count < w * h * 0.004) return {error: 'no water-coloured region found'};
    /* A balanced opening: erode then dilate by the same amount. The earlier
       1-then-2 was a net GROWTH, which bridged scattered bluish shadow pixels
       under a row of trees into one region large enough to outscore the pool
       itself. Removing specks must not also glue them together. */
    /* One pass each way. A balanced opening still removes specks, but two passes
       of erosion take four pixels off the diameter of every feature, and a spa is
       only about forty across at working scale — it was being destroyed to remove
       noise that one pass already handles. */
    var m = morph(morph(wm.mask, w, h, 'erode', 1), w, h, 'dilate', 1);

    var cc = components(m, w, h, Math.max(60, Math.round(w * h * 0.002)));
    if (!cc.blobs.length) return {error: 'water found, but no region large enough to be a pool'};
    cc.blobs.sort(function (a, b) { return b.px - a.px; });

    var shapes = cc.blobs.slice(0, 8).map(function (bl) {
      var raw = contour(cc.labels, w, h, bl.id, bl.seed);
      var pts = simplify(raw, Math.max(1.6, Math.sqrt(bl.px) * 0.035));
      var a = shoelaceAbs(pts), p = periOf(pts);
      var bb = bl.bbox;
      /* A pool is something the photographer framed; it sits inside the picture.
         A region running off the top of the frame is sky, and one running off any
         edge is usually background that happens to be blue. */
      var touchTop = bb[1] <= 1;
      var touchEdge = touchTop || bb[0] <= 1 || bb[2] >= w - 2 || bb[3] >= h - 2;
      var tex = texture(d, w, h, cc.labels, bl.id);
      var compact = p > 0 ? 4 * Math.PI * a / (p * p) : 0;

      /* Score rather than filter: any one of these can be wrong on a real photo,
         and a hard rule on the wrong one throws away the pool. */
      var score = a * (0.35 + compact);
      if (touchTop) score *= 0.05;
      else if (touchEdge) score *= 0.45;
      if (tex < 1.2) score *= 0.3;
      if (compact < 0.12) score *= 0.4;

      return {pts: pts.map(function (q) { return [q[0] / scale, q[1] / scale]; }),
              area: a, compact: compact, texture: tex,
              touchTop: touchTop, touchEdge: touchEdge, score: score};
    }).filter(function (s) { return s.pts.length >= 6 && s.area > 0; });

    if (!shapes.length) return {error: 'could not trace a boundary'};
    shapes.sort(function (a, b) { return b.score - a.score; });

    var pool = shapes[0], spa = null;

    function centroid(pts) {
      var x = 0, y = 0;
      pts.forEach(function (p) { x += p[0]; y += p[1]; });
      return [x / pts.length, y / pts.length];
    }

    /* Confidence, reported rather than enforced.
       Measured against sixteen photographs pulled off Wikimedia and hand
       labelled, this finds the pool in EVERY picture that contains one — seven
       of seven, none missed — and also outlines something blue in most pictures
       that do not: a roof, a television in a doorway, a slide in the snow.

       Those two facts are not equally important here. The tool is pointed at a
       photograph of a pool by someone who owns it, so recall is what matters and
       a false alarm on a hillside is a picture nobody will feed it. But it does
       mean this cannot be read as "yes, that is a pool" — the score below says
       how pool-LIKE the region is, and confirming it is the pool is the reader's
       job, which is why the outline is always shown and always editable. */
    var frac = pool.area / (w * h);
    var conf = 0;
    if (pool.compact > 0.25) conf += 2; else if (pool.compact > 0.15) conf += 1;
    if (pool.texture > 2.5) conf += 2; else if (pool.texture > 1.2) conf += 1;
    if (!pool.touchEdge) conf += 2; else if (!pool.touchTop) conf += 1;
    if (frac > 0.03) conf += 2; else if (frac > 0.008) conf += 1;
    pool.confidence = conf >= 6 ? 'high' : (conf >= 4 ? 'fair' : 'low');
    pool.confScore = conf;
    /* A spa is BESIDE the pool. That is the strongest signal available and it
       was not being used — the first pass picked a round shrub at the far corner
       of the garden simply because it was round and came first by area. Ranking
       candidates by roundness, water-likeness AND nearness, then taking the best
       rather than the first, is what makes the choice defensible. */
    var pc = centroid(pool.pts), poolR = Math.sqrt(pool.area / Math.PI);
    shapes.slice(1).forEach(function (s) {
      var c = centroid(s.pts);
      s.gap = Math.hypot(c[0] - pc[0], c[1] - pc[1]) / (poolR || 1);
    });
    /* A spa is water, smaller than the pool but not a puddle, and round enough
       to be a spa rather than a sliver of reflection along a wall. */
    for (var i = 1; i < shapes.length; i++) {
      var s = shapes[i], frac = s.area / pool.area;
      if (s.gap === undefined) continue;
      /* Texture is judged RELATIVE to the pool. The absolute threshold was tuned
         on phone photographs, where sunlit water glitters; satellite imagery is
         resampled and smooth, and a real pool measured 0.9 on one — its spa was
         being discarded for matching it. Whatever the imaging looks like, the
         spa is in the same picture as the pool and should read similarly. */
      var texFloor = Math.min(1.2, pool.texture * 0.6);
      if (frac > 0.015 && frac < 0.35 && s.compact > 0.45 && !s.touchTop &&
          s.texture >= texFloor && s.gap < 2.2) {
        s.spaScore = s.compact * (1 / (0.4 + s.gap));
        if (!spa || s.spaScore > spa.spaScore) spa = s;
      }
    }
    return {pool: pool, spa: spa, considered: shapes.length};
  }

  return {run: run};
})();

/* --------------------------------------------------- pool shape from a photo ---
   Points are stored in IMAGE pixel coordinates, never in canvas coordinates, so
   the geometry survives the canvas being resized or the window changing. The
   canvas is only ever a viewport onto the image. */
(function () {
  var stage = document.getElementById('shape-stage');
  if (!stage) return;
  var cv = document.getElementById('shape-canvas'), ctx = cv.getContext('2d');
  var img = null, scaleDisp = 1;
  var S = {points: [], spaPts: [], scalePts: [], rectPts: [], ft_per_px: 0,
           mode: 'trace', flip: false, exif: null, camTilt: null, camRoll: 0};

  var HINTS = {
    camera:  'No tape needed. The water is a plane, so the camera geometry places every pixel on it — ' +
             'the only thing missing is how high above the water you were standing.',
    trace:   'Click around the pool waterline. Ten to twenty points is plenty for a curve.',
    spa:     'Click around the spa waterline, the same as the pool. Eight to twelve points is plenty. It is measured on its own terms — flat floor, perimeter bench — and then added.',
    scale:   'Click the two ends of a distance you have measured, then type it below.',
    rectify: 'Click four corners of something you know is a rectangle on the water surface, clockwise from the near-left.'
  };

  function setMode(m) {
    S.mode = m;
    document.querySelectorAll('.modes .mode').forEach(function (b) {
      b.setAttribute('aria-pressed', b.dataset.mode === m ? 'true' : 'false');
    });
    document.getElementById('shape-hint').textContent = HINTS[m];
    document.getElementById('scale-row').hidden = m !== 'scale';
    document.getElementById('cam-row').hidden = m !== 'camera';
    document.getElementById('rect-row').hidden = m !== 'rectify';
    draw();
  }
  document.querySelectorAll('.modes .mode').forEach(function (b) {
    b.addEventListener('click', function () { setMode(b.dataset.mode); });
  });

  document.getElementById('shape-file').addEventListener('change', function (e) {
    var f = e.target.files && e.target.files[0];
    if (!f) return;
    var ex = document.getElementById('shape-exif');

    /* HEIC is what an iPhone writes by default, and Chrome and Firefox cannot
       decode it at all — the file simply never appears. When the local server is
       running it converts with sips, which macOS ships, and reads the EXIF with
       real tools rather than the hand-rolled MakerNote walker below. */
    if (API) {
      ex.textContent = 'reading the photo…';
      fetch('/api/photo', {method: 'POST',
                           headers: {'Content-Type': 'application/octet-stream',
                                     'X-Poolhound-Token': TOKEN || ''},
                           body: f})
        .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
        .then(function (res) {
          if (!res.ok) throw new Error(res.j.error || 'could not read that image');
          var j = res.j, i2 = new Image();
          i2.onload = function () { adopt(i2); };
          i2.src = j.data_url;
          /* Kept so the spa section can offer the same photograph without a
             second upload and a second round trip through the server. */
          window.__poolPhotoSrc = j.data_url;
          S.exif = {focal35: j.focal35, model: j.model};
          var bits = [];
          if (j.model) bits.push(j.model);
          if (j.focal35) bits.push(j.focal35 + 'mm equivalent');
          if (j.converted_from_heic) bits.push('HEIC converted');
          var cn = document.getElementById('cam-note');
          if (j.tilt_deg !== null && j.tilt_deg !== undefined) {
            S.camTilt = j.tilt_deg; S.camRoll = j.roll_deg || 0;
            var ti = document.getElementById('cam-tilt');
            if (ti) ti.value = j.tilt_deg.toFixed(0);
            bits.push('tilt ' + j.tilt_deg.toFixed(0) + '° from the accelerometer');
            if (cn) cn.textContent = 'Tilt read from the photo. Only the height is missing.';
          } else if (cn) {
            cn.textContent = j.focal35 ? 'No tilt in this file — set it by hand.'
                                       : 'No focal length in this file.';
          }
          ex.textContent = bits.join(' · ') || 'no camera information in this file';
        })
        .catch(function (err) {
          ex.textContent = String(err.message || err) + ' — trying the browser instead';
          localRead(f);
        });
      return;
    }
    localRead(f);
  });

  function adopt(i) {
    img = i;
    var maxW = 900, maxH = 620;
    scaleDisp = Math.min(maxW / i.width, maxH / i.height, 1);
    cv.width = Math.round(i.width * scaleDisp);
    cv.height = Math.round(i.height * scaleDisp);
    stage.hidden = false;
    setMode('trace');
    recompute();
  }

  function localRead(f) {
    var fr = new FileReader();
    fr.onload = function () {
      readExif(fr.result);
      var i = new Image();
      i.onerror = function () {
        document.getElementById('shape-exif').textContent = PUBLIC
          ? 'this browser cannot display that file. If it came from an iPhone it is '
            + 'probably HEIC — set Camera > Formats to Most Compatible, or export it '
            + 'as JPEG and try again.'
          : 'this browser cannot display that file — if it is a HEIC, start bin/serve '
            + 'and it will be converted, or export it as JPEG on the phone';
      };
      i.onload = function () {
        img = i;
        /* Fit the image into the canvas box; remember the factor so clicks can be
           mapped back to image pixels. */
        adopt(i);
      };
      i.src = URL.createObjectURL(f);
    };
    fr.readAsArrayBuffer(f);
  }

  /* A deliberately small EXIF reader: the phone name, so it is obvious the right
     photo was loaded, and the 35mm-equivalent focal length, which makes the
     known-height overhead method possible. Nothing else is read. */
  function readExif(buf) {
    var out = {}, dv = new DataView(buf);
    try {
      if (dv.getUint16(0) !== 0xFFD8) throw 0;
      var off = 2;
      while (off < dv.byteLength - 4) {
        var marker = dv.getUint16(off);
        var size = dv.getUint16(off + 2);
        if (marker === 0xFFE1) {
          var base = off + 10;
          var little = dv.getUint16(base) === 0x4949;
          var g16 = function (o) { return dv.getUint16(o, little); };
          var g32 = function (o) { return dv.getUint32(o, little); };
          var ifd = base + g32(base + 4);
          var want = {0x010F: 'make', 0x0110: 'model', 0x8769: 'exif'};
          var readDir = function (dir, map) {
            var n = g16(dir);
            for (var k = 0; k < n; k++) {
              var e = dir + 2 + k * 12, tag = g16(e);
              if (!map[tag]) continue;
              var type = g16(e + 2), count = g32(e + 4), val = g32(e + 8);
              if (type === 2) {
                var p = base + val, s = '';
                for (var c = 0; c < count - 1; c++) s += String.fromCharCode(dv.getUint8(p + c));
                out[map[tag]] = s.trim();
              } else if (type === 3) {
                out[map[tag]] = g16(e + 8);
              } else {
                out[map[tag]] = val;
              }
            }
          };
          readDir(ifd, want);
          if (out.exif) readDir(base + out.exif, {0xA405: 'focal35', 0x927C: 'maker'});
          /* Apple's MakerNote is itself an IFD, and tag 0x0008 in it is
             AccelerationVector — the gravity direction in device coordinates.
             The rear camera looks along the device's -Z, so a level phone reads
             (0,-1,0) and one aimed at the ground reads (0,0,-1): the downward
             tilt is asin(-gz). That removes the one input a photograph could not
             otherwise supply besides height. */
          if (out.maker) {
            try {
              var mk = base + out.maker;
              for (var off2 = mk; off2 < mk + 400; off2++) {
                if (dv.getUint8(off2) === 0x41 && dv.getUint8(off2+1) === 0x70 &&
                    dv.getUint8(off2+2) === 0x70 && dv.getUint8(off2+3) === 0x6C) {
                  var mbase = off2, mifd = mbase + 14, cnt = g16(mifd), g = null;
                  for (var q = 0; q < cnt; q++) {
                    var me = mifd + 2 + q * 12;
                    if (g16(me) === 0x0008 && g16(me + 2) === 10) {
                      var vp = mbase + g32(me + 8), vec = [];
                      for (var c2 = 0; c2 < 3; c2++) {
                        var nn = dv.getInt32(vp + c2 * 8, little);
                        var dd = dv.getInt32(vp + c2 * 8 + 4, little) || 1;
                        vec.push(nn / dd);
                      }
                      g = vec;
                    }
                  }
                  if (g) out.gravity = g;
                  break;
                }
              }
            } catch (e2) { /* MakerNote layouts vary; tilt stays manual */ }
          }
          break;
        }
        if ((marker & 0xFF00) !== 0xFF00) break;
        off += 2 + size;
      }
    } catch (err) { /* not a JPEG, or no EXIF; neither is a problem */ }
    var bits = [];
    if (out.make || out.model) bits.push(((out.make || '') + ' ' + (out.model || '')).trim());
    if (out.focal35) bits.push(out.focal35 + 'mm equivalent');
    S.exif = out;
    var cn = document.getElementById('cam-note');
    if (out.gravity) {
      var gv = out.gravity, n2 = Math.hypot(gv[0], gv[1], gv[2]) || 1;
      var tilt = Math.asin(Math.max(-1, Math.min(1, -gv[2] / n2))) * 180 / Math.PI;
      var roll = Math.atan2(gv[0] / n2, -gv[1] / n2) * 180 / Math.PI;
      S.camTilt = tilt; S.camRoll = roll;
      var ti = document.getElementById('cam-tilt');
      if (ti) ti.value = tilt.toFixed(0);
      bits.push('tilt ' + tilt.toFixed(0) + '° from the accelerometer');
      if (cn) cn.textContent = 'Tilt read from the photo. Only the height is missing.';
    } else if (cn) {
      cn.textContent = out.focal35 ? 'No tilt in this file — set it by hand.'
                                   : 'No focal length in this file; the camera method needs one.';
    }
    document.getElementById('shape-exif').textContent =
      bits.length ? bits.join(' · ') : 'no camera information in this file';
  }

  function toImage(ev) {
    var r = cv.getBoundingClientRect();
    return [(ev.clientX - r.left) * (cv.width / r.width) / scaleDisp,
            (ev.clientY - r.top) * (cv.height / r.height) / scaleDisp];
  }

  cv.addEventListener('click', function (ev) {
    if (!img) return;
    var p = toImage(ev);
    if (S.mode === 'trace') S.points.push(p);
    /* Traced point by point, the same as the pool. The two-click circle assumed
       every spa is round; plenty are octagonal, square, or a spillover lobe
       shaped to match the pool, and forcing those into a circle threw away real
       area. Tracing costs a few more clicks and describes whatever is there. */
    else if (S.mode === 'spa') S.spaPts.push(p);
    else if (S.mode === 'scale') { if (S.scalePts.length >= 2) S.scalePts = []; S.scalePts.push(p); }
    else { if (S.rectPts.length >= 4) S.rectPts = []; S.rectPts.push(p); }
    recompute();
  });

  document.getElementById('shape-undo').addEventListener('click', function () {
    var a = S.mode === 'trace' ? S.points : (S.mode === 'spa' ? S.spaPts :
            (S.mode === 'scale' ? S.scalePts : S.rectPts));
    a.pop(); recompute();
  });
  document.getElementById('shape-clear').addEventListener('click', function () {
    if (S.mode === 'trace') S.points = [];
    else if (S.mode === 'spa') S.spaPts = [];
    else if (S.mode === 'scale') S.scalePts = [];
    else S.rectPts = [];
    recompute();
  });

  /* Repaint when the theme changes, from either direction: the toggle stamps
     data-theme on <html>, and an un-stamped page follows the OS, which can flip
     under it at sunset. Without this the canvas keeps whichever palette it was
     drawn with until something else happens to redraw it. */
  try {
    new MutationObserver(function () { draw(); })
      .observe(document.documentElement, {attributes: true,
                                          attributeFilter: ['data-theme']});
    matchMedia('(prefers-color-scheme: dark)')
      .addEventListener('change', function () { draw(); });
  } catch (e) {}

  function draw() {
    if (!img) return;
    ctx.clearRect(0, 0, cv.width, cv.height);
    if (img.blank) {
      ctx.fillStyle = PAINT.ground(); ctx.fillRect(0, 0, cv.width, cv.height);
    } else {
      ctx.drawImage(img, 0, 0, cv.width, cv.height);
    }
    var d = function (p) { return [p[0] * scaleDisp, p[1] * scaleDisp]; };

    if (S.points.length) {
      ctx.beginPath();
      S.points.forEach(function (p, i) {
        var q = d(p);
        if (i === 0) ctx.moveTo(q[0], q[1]); else ctx.lineTo(q[0], q[1]);
      });
      if (S.points.length > 2) ctx.closePath();
      ctx.fillStyle = 'rgba(42,120,214,0.28)';
      ctx.strokeStyle = PAINT.pump(); ctx.lineWidth = 2;
      if (S.points.length > 2) ctx.fill();
      ctx.stroke();
      S.points.forEach(function (p) {
        var q = d(p);
        ctx.beginPath(); ctx.arc(q[0], q[1], 4, 0, 6.284);
        ctx.fillStyle = PAINT.handle(); ctx.fill();
        ctx.strokeStyle = PAINT.pump(); ctx.lineWidth = 2; ctx.stroke();
      });
    }
    if (S.spaPts.length) {
      ctx.beginPath();
      S.spaPts.forEach(function (p, i) {
        var q = d(p);
        if (i === 0) ctx.moveTo(q[0], q[1]); else ctx.lineTo(q[0], q[1]);
      });
      if (S.spaPts.length > 2) ctx.closePath();
      ctx.fillStyle = 'rgba(235,104,52,0.30)';
      ctx.strokeStyle = PAINT.spa(); ctx.lineWidth = 2;
      if (S.spaPts.length > 2) ctx.fill();
      ctx.stroke();
      S.spaPts.forEach(function (p) {
        var q = d(p);
        ctx.beginPath(); ctx.arc(q[0], q[1], 3.5, 0, 6.284);
        ctx.fillStyle = PAINT.handle(); ctx.fill();
        ctx.strokeStyle = PAINT.spa(); ctx.lineWidth = 2; ctx.stroke();
      });
    }
    drawEnds(d);
    if (S.scalePts.length) {
      ctx.beginPath();
      S.scalePts.forEach(function (p, i) {
        var q = d(p);
        if (i === 0) ctx.moveTo(q[0], q[1]); else ctx.lineTo(q[0], q[1]);
      });
      ctx.strokeStyle = PAINT.spa(); ctx.lineWidth = 3; ctx.stroke();
      S.scalePts.forEach(function (p) {
        var q = d(p);
        ctx.beginPath(); ctx.arc(q[0], q[1], 5, 0, 6.284);
        ctx.fillStyle = PAINT.spa(); ctx.fill();
      });
    }
    if (S.rectPts.length) {
      ctx.beginPath();
      S.rectPts.forEach(function (p, i) {
        var q = d(p);
        if (i === 0) ctx.moveTo(q[0], q[1]); else ctx.lineTo(q[0], q[1]);
      });
      if (S.rectPts.length === 4) ctx.closePath();
      ctx.strokeStyle = PAINT.sheer(); ctx.lineWidth = 2;
      ctx.setLineDash([6, 4]); ctx.stroke(); ctx.setLineDash([]);
      S.rectPts.forEach(function (p, i) {
        var q = d(p);
        ctx.beginPath(); ctx.arc(q[0], q[1], 6, 0, 6.284);
        ctx.fillStyle = PAINT.sheer(); ctx.fill();
        ctx.fillStyle = PAINT.handle(); ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
        ctx.fillText(String(i + 1), q[0], q[1] + 3.5);
      });
    }
  }

  /* Mark which end of the long axis the floor falls FROM. The axis direction is
     arbitrary — a mirrored outline can hand back the opposite one — so this is
     not something to infer. It is drawn on the photo and the reader says which
     is which. */
  function longAxisEnds(pts) {
    if (pts.length < 3) return null;
    var cx = 0, cy = 0;
    pts.forEach(function (p) { cx += p[0]; cy += p[1]; });
    cx /= pts.length; cy /= pts.length;
    var best = null;
    for (var i = 0; i < pts.length; i++)
      for (var j = i + 1; j < pts.length; j++) {
        var dd = Math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]);
        if (!best || dd > best.d) best = {d: dd, a: pts[i], b: pts[j]};
      }
    if (!best) return null;
    return S.flip ? {a: best.b, b: best.a} : {a: best.a, b: best.b};
  }

  function drawEnds(d) {
    var e = longAxisEnds(S.points);
    document.getElementById('ends-row').hidden = !e;
    if (!e) return;
    /* PAINT, not literals. The commit that moved this canvas onto the live
       custom properties missed these two markers: 'B' was filled with the LIGHT
       palette's --ink and its label drawn in --panel, which in dark mode is
       #161e27 on #16202b — 1.02:1, an invisible letter on its own disc. The
       proof it was an oversight is PAINT.ink(), defined for exactly this and
       called nowhere until now. ink-on-panel tracks the theme both ways. */
    [['A', e.a, PAINT.pump()], ['B', e.b, PAINT.ink()]].forEach(function (m) {
      var q = d(m[1]);
      ctx.beginPath(); ctx.arc(q[0], q[1], 11, 0, 6.284);
      ctx.fillStyle = m[2]; ctx.fill();
      ctx.strokeStyle = PAINT.handle(); ctx.lineWidth = 2; ctx.stroke();
      ctx.fillStyle = PAINT.handle(); ctx.font = 'bold 12px sans-serif';
      ctx.textAlign = 'center'; ctx.fillText(m[0], q[0], q[1] + 4);
    });
  }

  function shoelace(pts) {
    if (pts.length < 3) return 0;
    var s = 0;
    for (var i = 0; i < pts.length; i++) {
      var a = pts[i], b = pts[(i + 1) % pts.length];
      s += a[0] * b[1] - b[0] * a[1];
    }
    return Math.abs(s) / 2;
  }

  /* Guarded, like its twin further down. Unguarded, a missing element made
     this throw a TypeError and take the whole handler with it -- and ids DO go
     missing: four of them did when the spa moved to its own canvas. Two copies
     of one helper, one safe and one not, is how that became a crash instead of
     a zero. */
  function num(id) { var v = parseFloat((document.getElementById(id) || {}).value);
                     return isFinite(v) ? v : 0; }

  function currentModel() {
    var el = document.getElementById('depth-model');
    return el ? el.value : '';
  }

  function syncModelRows() {
    var m = currentModel();
    /* When left on "let the outline decide" the rows shown are the ones the
       analysis would use, so the inputs on screen are always the inputs that
       actually feed the answer. */
    var eff = m || (S.autoModel || 'linear');
    document.getElementById('ramp-row').hidden    = eff !== 'linear';
    document.getElementById('lobe-row').hidden    = eff !== 'lobes';
    document.getElementById('uniform-row').hidden = eff !== 'constant';
    var note = document.getElementById('model-note');
    if (!m && S.autoKind) {
      note.textContent = 'The outline reads as ' + S.autoKind + ', so: ' +
        ({linear: 'one steady slope', lobes: 'two basins', constant: 'a single depth'}[eff]);
    } else if (m && S.autoModel && m !== S.autoModel) {
      note.textContent = 'Overriding what the outline suggests (' + S.autoModel + ').';
    } else {
      note.textContent = '';
    }
  }

  function shapeRecord() {
    var rec = {
      points: S.points,
      model: currentModel() || undefined,
      shallow_ft: num('d-shallow'), deep_ft: num('d-deep'),
      lobe_near_ft: num('d-lobe-near'), lobe_far_ft: num('d-lobe-far'),
      uniform_ft: num('d-uniform'),
      flat_shallow: num('f-shallow') / 100, flat_deep: num('f-deep') / 100,
      ledge_sqft: num('ledge-sqft'), ledge_depth_ft: num('ledge-depth'),
      flip: S.flip
    };
    /* NO rec.spa HERE. The spa got its own canvas and its own inputs
       (t-spa-depth and friends); this arm kept reading spa-depth, spa-bench-w,
       spa-bench-d and spa-step, which exist in neither build. It was worse than
       dead: the four reads returned 0, so saving the POOL would have written a
       spa record of all zeros over the good one the spa's own tracer saved.
       The spa outline is still DRAWN from a reloaded shape below, which is why
       S.spaPts stays. */
    var rw = num('rect-w'), rl = num('rect-l');
    if (S.rectPts.length === 4 && rw > 0 && rl > 0) {
      rec.rectify = {src: S.rectPts, dst: [[0, 0], [rw, 0], [rw, rl], [0, rl]]};
    } else if (S.mode === 'camera' && S.exif && S.exif.focal35 && num('cam-height') > 0) {
      /* The server solves the ground plane; sending the parameters rather than
         a derived transform keeps one implementation of the geometry. */
      rec.camera = {img_w: img.width, img_h: img.height,
                    focal35: S.exif.focal35, height_ft: num('cam-height'),
                    tilt_deg: num('cam-tilt'), roll_deg: S.camRoll || 0};
    } else if (S.ft_per_px > 0) {
      rec.ft_per_px = S.ft_per_px;
    }
    return rec;
  }

  // Looked up once, here, so every reference below is in scope. Null in the
  // public build, which has the calculator but nothing of ours to save to.
  var save = document.getElementById('shape-save');
  /* Why the button is disabled, said AT the button.
     The reason was only ever in the result box, which on a traced photograph
     sits above a 900px canvas and well away from the control somebody is
     looking at — so "Find the water for me" would fill in the outline, leave
     Calculate greyed out, and give no reason anywhere near it. */
  function need(msg) {
    var n = document.getElementById('shape-need');
    if (n) { n.textContent = msg || ''; n.hidden = !msg; }
  }
  var lastPreview = 0;
  function recompute() {
    draw();
    var ft = num('scale-ft');
    if (S.scalePts.length === 2 && ft > 0) {
      var dx = S.scalePts[0][0] - S.scalePts[1][0], dy = S.scalePts[0][1] - S.scalePts[1][1];
      var px = Math.sqrt(dx * dx + dy * dy);
      S.ft_per_px = px > 0 ? ft / px : 0;
      document.getElementById('scale-note').textContent =
        px.toFixed(0) + ' px = ' + ft + ' ft  →  ' + (1 / S.ft_per_px).toFixed(1) + ' px per foot';
    }
    var rec = shapeRecord();
    var out = document.getElementById('shape-result');
    var rectified = !!rec.rectify;

    if (rec.points.length < 3) {
      out.innerHTML = '<span class="ph">Trace at least three points around the waterline.</span>';
      if (save) save.disabled = true;
      need('Click around the waterline first — three points at least.');
      document.getElementById('shape-calc').disabled = true; return;
    }
    if (rec.camera) {
      out.innerHTML = '<span class="ph">Scale will come from the camera geometry — ' +
        'height ' + rec.camera.height_ft + ' ft, tilt ' + rec.camera.tilt_deg +
        '°. Calculate to solve the ground plane.</span>';
      document.getElementById('shape-calc').disabled = false;
      need('');
      if (save) save.disabled = true; return;
    }
    if (!rectified && !rec.ft_per_px) {
      out.innerHTML = '<span class="ph">Now set the scale: click a distance you have ' +
        'measured, or switch to <b>Use the camera</b>.</span>';
      if (save) save.disabled = true;
      need('Set the scale first: press Set the scale, click two points a known ' +
           'distance apart, then type that distance.');
      document.getElementById('shape-calc').disabled = true; return;
    }
    need('');

    var area;
    if (rectified) {
      /* Preview only. The saved figure is recomputed on the Python side, which
         is authoritative — pool_shape.py computes it. */
      area = null;
    } else {
      area = shoelace(rec.points) * rec.ft_per_px * rec.ft_per_px;
    }

    if (area === null) {
      out.innerHTML = '<span class="ph">Four rectification points set. Calculate to solve ' +
                      'the perspective transform and measure the rectified area.</span>';
      document.getElementById('shape-calc').disabled = false;
      if (save) save.disabled = true; return;
    }

    var d1 = Math.min(rec.shallow_ft, rec.deep_ft), d2 = Math.max(rec.shallow_ft, rec.deep_ft);
    var fs = Math.max(0, Math.min(1, rec.flat_shallow)), fd = Math.max(0, Math.min(1, rec.flat_deep));
    if (fs + fd > 1) { var k = 1 / (fs + fd); fs *= k; fd *= k; }
    var avg = fs * d1 + fd * d2 + (1 - fs - fd) * (d1 + d2) / 2;
    var ledge = Math.max(0, Math.min(rec.ledge_sqft, area));
    var cubic = (area - ledge) * avg + ledge * rec.ledge_depth_ft;
    var poolGal = cubic * 7.480519;

    /* The spa, on its own terms: a flat floor less the bench people sit on.
       Mirrors spa_volume() on the Python side, which remains authoritative —
       the saved figure is recomputed there and any disagreement is reported. */
    var spaGal = 0, spaBench = 0, spaArea = 0;
    if (rec.spa && rec.spa.points.length >= 3 && rec.ft_per_px) {
      var sp = rec.spa.points.map(function (q) {
        return [q[0] * rec.ft_per_px, q[1] * rec.ft_per_px];
      });
      spaArea = shoelace(sp);
      var sPeri = 0;
      for (var i = 0; i < sp.length; i++) {
        var a1 = sp[i], b1 = sp[(i + 1) % sp.length];
        sPeri += Math.hypot(b1[0] - a1[0], b1[1] - a1[1]);
      }
      var bw = rec.spa.bench_width_ft, inr = sPeri > 0 ? 2 * spaArea / sPeri : 0;
      spaBench = bw <= 0 ? 0 : (bw >= inr ? spaArea
                 : Math.max(0, Math.min(spaArea, sPeri * bw - Math.PI * bw * bw)));
      var sStep = Math.max(0, Math.min(rec.spa.step_sqft, spaArea - spaBench));
      var sFloor = Math.max(0, spaArea - spaBench - sStep);
      spaGal = (sFloor * rec.spa.depth_ft
                + spaBench * Math.min(rec.spa.bench_depth_ft, rec.spa.depth_ft)
                + sStep * Math.min(rec.spa.step_depth_ft, rec.spa.depth_ft)) * 7.480519;
    }

    var gal = poolGal + spaGal;
    var pct = Math.sqrt(16 + 9 + (rectified ? 0.25 : 36) + 64 + (ledge > 0 ? 4 : 1));

    var html =
      '<span class="pl">Estimated volume</span>' +
      '<span class="eff"><b>' + Math.round(gal).toLocaleString() + '</b>gallons total</span>' +
      '<span class="eff"><b>±' + pct.toFixed(0) + '%</b>' +
        Math.round(gal * (1 - pct / 100)).toLocaleString() + '–' +
        Math.round(gal * (1 + pct / 100)).toLocaleString() + '</span>' +
      '<span class="eff"><b>' + Math.round(area).toLocaleString() + '</b>sq ft of pool</span>' +
      '<span class="eff"><b>' + avg.toFixed(1) + '</b>ft average depth</span>';
    if (spaGal > 0) {
      html += '<span class="eff"><b>' + Math.round(spaGal).toLocaleString() + '</b>in the spa</span>' +
              '<span class="eff"><b>' + Math.round(spaBench) + '</b>sq ft of bench</span>';
    }
    out.innerHTML = html;
    lastPreview = gal;
    /* Adopting is re-locked whenever the inputs move, so the saved number is
       always the one that was on screen when it was inspected. */
    document.getElementById('shape-calc').disabled = false;
    if (save) save.disabled = true;
  }

  /* spa-depth, spa-bench-w, spa-bench-d and spa-step are gone: they moved to
     the spa's own canvas as t-spa-*. The forEach guards on `el`, so they were
     harmless here -- but listing ids that cannot exist is how the next reader
     concludes the inputs are still somewhere. */
  ['d-shallow','d-deep','f-shallow','f-deep','ledge-sqft','ledge-depth','spa-gal',
   'scale-ft','rect-w','rect-l',
   'd-lobe-near','d-lobe-far','d-uniform'
  ].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener('input', recompute);
  });
  var msel = document.getElementById('depth-model');
  if (msel) msel.addEventListener('change', function () { syncModelRows(); recompute(); });

  /* Reload a stored outline. The photograph is not kept, so the points are drawn
     on a plain ground — enough to adjust depths and see the volume move without
     hunting for the original picture. */
  (function () {
    var btn = document.getElementById('shape-reload');
    if (!btn) return;
    btn.addEventListener('click', function () {
      var s = JSON.parse(document.getElementById('saved-shape').textContent || 'null');
      if (!s) return;
      S.points = s.points || [];
      S.spaPts = (s.spa && s.spa.points) || [];
      S.ft_per_px = s.ft_per_px || 0;
      S.flip = !!s.flip;
      var set = function (id, v) {
        var el = document.getElementById(id);
        if (el && v !== undefined && v !== null) el.value = v;
      };
      set('d-shallow', s.shallow_ft); set('d-deep', s.deep_ft);
      set('d-lobe-near', s.lobe_near_ft); set('d-lobe-far', s.lobe_far_ft);
      set('d-uniform', s.uniform_ft);
      set('ledge-sqft', s.ledge_sqft); set('ledge-depth', s.ledge_depth_ft);
      set('depth-model', s.model || '');
      /* The spa's depth fields live on its own canvas now and are restored
         there. set() guards on the element, so these four were silent no-ops
         rather than errors -- silent no-ops that read as working code. */
      var xs = S.points.map(function (p) { return p[0]; });
      var ys = S.points.map(function (p) { return p[1]; });
      img = {width: Math.max.apply(null, xs) * 1.15 + 40,
             height: Math.max.apply(null, ys) * 1.15 + 40, blank: true};
      scaleDisp = Math.min(900 / img.width, 620 / img.height, 1);
      cv.width = Math.round(img.width * scaleDisp);
      cv.height = Math.round(img.height * scaleDisp);
      stage.hidden = false;
      document.getElementById('shape-detected').hidden = false;
      document.getElementById('shape-detected').className = 'detected';
      document.getElementById('shape-detected').textContent =
        'Reloaded the saved outline. Choose the photograph again if you want to ' +
        're-trace it; the depths and the volume work without it.';
      setMode('trace');
      if (s.computed) { S.autoModel = s.computed.auto_model; S.autoKind = s.computed.shape_kind; }
      syncModelRows();
      recompute();
    });
  })();

  document.getElementById('shape-flip').addEventListener('click', function () {
    S.flip = !S.flip; recompute();
  });

  document.getElementById('shape-detect').addEventListener('click', function () {
    if (!img) return;
    var note = document.getElementById('shape-detected');
    note.hidden = false;
    note.textContent = 'looking for water…';
    setTimeout(function () {
      var r = Detect.run(img);
      if (r.error) {
        note.className = 'detected bad';
        note.textContent = r.error + ' — trace it by hand instead.';
        return;
      }
      S.points = r.pool.pts;
      S.spaPts = r.spa ? r.spa.pts : [];
      var c = r.pool.confidence;
      note.className = 'detected' + (c === 'low' ? ' bad' : (c === 'fair' ? ' warn' : ''));
      var body = r.spa
        ? 'Found <b>two</b> bodies of water: the larger taken as the pool, and a ' +
          'smaller compact one (' + Math.round(r.spa.area / r.pool.area * 100) +
          '% of its area) taken as the spa.'
        : 'Found <b>one</b> body of water and no spa beside it. A small pale spa often ' +
          'will not separate from wet deck by colour at this scale — if you have one, ' +
          'use <b>Spa: 2 clicks</b> rather than expecting it to be found.';
      var caveat = {
        high: ' It looks strongly like open water — rippled, compact and well inside the frame.',
        fair: ' It is a weaker match: clipped by the frame, unusually smooth, or small. ' +
              'Worth a closer look before you trust the number.',
        low:  ' It is a <b>poor</b> match for open water. If it has outlined sky, glass or ' +
              'paintwork, clear it and trace by hand.'
      }[c];
      caveat += ' <b>Check it is the right water before setting the scale</b> — this ' +
                'finds blue regions, it does not know what a pool is.';
      note.innerHTML = body + caveat;
      recompute();
    }, 30);
  });

  /* Everything the analysis worked out, on the page.
     It classifies the shape, decides what that means for the depth model, splits
     pool from spa, prices the bench, compares itself against length x width and
     says where its uncertainty comes from. For a while it computed all of that
     and displayed none of it, which is indistinguishable from not having done it. */
  function showVerdict(j) { showVerdictIn('shape-verdict', j); }
  /* Shared with the stock-design tool, which renders the same verdict into
     its own block. Hung on window rather than duplicated. */
  window.showVerdictIn = showVerdictIn;
  function showVerdictIn(id, j) {
    var el = document.getElementById(id);
    el.hidden = false;
    var pct = j.uncertainty_pct || 0;
    var rows = [];

    /* Pool, spa and total as three separate figures rather than one combined
       headline with the parts underneath. They are three different questions —
       how much water the pool holds, how much the spa holds, and how much a
       dose has to treat — and a single number answered none of them on its
       own. Where there is no spa there is one figure, because "total" for a
       single body of water is just the figure said twice. */
    var hasSpa = !!(j.spa && j.spa.gallons);
    var poolOnly = hasSpa ? j.pool_only_gallons : j.gallons;
    var fmt = function (n) { return Math.round(n).toLocaleString(); };

    if (hasSpa) {
      rows.push('<div class="v-parts">' +
        '<span class="vp"><i>Pool</i><b>' + fmt(poolOnly) + '</b>gal</span>' +
        '<span class="vp-op">+</span>' +
        '<span class="vp"><i>Spa</i><b>' + fmt(j.spa.gallons) + '</b>gal</span>' +
        '<span class="vp-op">=</span>' +
        '<span class="vp vp-total"><i>Total</i><b>' + fmt(j.gallons) + '</b>gal</span>' +
        '</div>' +
        '<p class="v-range">&plusmn;' + pct.toFixed(0) + '% on the total &middot; ' +
        fmt(j.gallons * (1 - pct / 100)) + '–' + fmt(j.gallons * (1 + pct / 100)) + '</p>');
    } else {
      rows.push('<div class="v-head"><b>' + fmt(j.gallons) +
        ' gal</b><span>&plusmn;' + pct.toFixed(0) + '% &middot; ' +
        fmt(j.gallons * (1 - pct / 100)) + '–' + fmt(j.gallons * (1 + pct / 100)) +
        '</span></div>');
    }
    /* Hand the figures to the running total at the foot of the tab, so whichever
       tool produced them — stock design or traced photograph, and one each if
       you like — the combined answer is in one place. */
    if (window.recordVolume) {
      window.recordVolume('pool', poolOnly, j.method || '');
      if (hasSpa) window.recordVolume('spa', j.spa.gallons, j.method || '');
    }

    if (j.shape_kind) {
      rows.push('<p class="v-why"><b>Read as ' + j.shape_kind + '.</b> ' +
        (j.shape_why || '') +
        (j.model_overridden ? ' <b>You overrode this</b> and asked for the ' +
          j.model + ' model instead of ' + j.auto_model + '.' : '') + '</p>');
    }

    if (j.spa) {
      var s = j.spa;
      rows.push('<div class="v-split">' +
        '<span class="eff"><b>' + Math.round(s.bench_sqft) + '</b>sq ft of bench, ' +
        Math.round(s.bench_sqft / s.area_sqft * 100) + '% of the spa surface</span></div>' +
        '<p class="v-why">Treated as a plain box the spa would read ' +
        Math.round(s.as_box_gallons).toLocaleString() + ' gal — ' +
        Math.round((s.as_box_gallons / s.gallons - 1) * 100) + '% high. The bench is the difference.</p>');
    }

    if (j.naive_gallons) {
      var d = (j.naive_gallons / j.gallons - 1) * 100;
      rows.push('<p class="v-why"><b>Against the usual method.</b> Length &times; width ' +
        '&times; average depth &times; 0.85 would give ' +
        Math.round(j.naive_gallons).toLocaleString() + ' gal, ' +
        (Math.abs(d) < 1 ? 'which happens to agree here'
         : (d > 0 ? Math.round(d) + '% high' : Math.round(-d) + '% low')) +
        '. That factor is a guess about a shape nobody measured; this outline is the shape.</p>');
    }

    if (j.terms) {
      var parts = Object.keys(j.terms).sort(function (a, b) {
        return j.terms[b].pct - j.terms[a].pct;
      }).map(function (k) {
        return '<li><b>&plusmn;' + j.terms[k].pct + '%</b> ' + k + ' — ' + j.terms[k].why + '</li>';
      });
      rows.push('<details class="v-terms"><summary>Where the &plusmn;' + pct.toFixed(0) +
        '% comes from</summary><ul>' + parts.join('') +
        '</ul><p class="v-why">Combined in quadrature, because these are independent ' +
        'and adding them outright would give a range so wide it says nothing. The ' +
        'floor dominates: it is modelled, not measured. Your logged acid doses will ' +
        'eventually measure the volume directly and replace all of this.</p></details>');
    }

    if (j.preview_disagreed_pct) {
      rows.push('<p class="v-warn">The live preview said something ' +
        j.preview_disagreed_pct + '% different from this. This figure is the one ' +
        'that was saved — it is recomputed server-side from the raw outline.</p>');
    }
    el.innerHTML = rows.join('');
    /* Feed the classification back into the selector, so "let the outline decide"
       can say what it decided instead of leaving the reader to guess. */
    if (j.auto_model && id === 'shape-verdict') {
      S.autoModel = j.auto_model; S.autoKind = j.shape_kind; syncModelRows();
    }
    el.scrollIntoView({block: 'nearest', behavior: 'smooth'});
    /* AND TAKE THE READER WITH IT. Scrolling moves the picture and leaves the
       caret where it was: measured after pressing Calculate, document
       activeElement was still <body> while the answer sat ~1180px down a panel
       that does not scroll itself, and nothing on the page was a live region to
       announce it. Focusing the block is what makes "the answer arrived" an
       event rather than a change of scenery. The element carries tabindex=-1 so
       this works without adding a tab stop. */
    try { el.focus({preventScroll: true}); } catch (e) { el.focus(); }
  }

  /* Calculating and adopting are separate acts. The figure every dose
     multiplies through should be inspectable before it is in use — look at the
     breakdown, disagree with a depth, try again, and only then commit. */
  document.getElementById('shape-calc').addEventListener('click', function () {
    if (!API) return;
    var msg = document.getElementById('shape-msg');
    msg.textContent = 'calculating…'; msg.className = 'fmsg';
    fetch('/api/pool-shape/compute', {method: 'POST', headers: postHeaders(),
                                      body: JSON.stringify(shapeRecord())})
      .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.j.error || 'refused');
        /* Always acknowledge the press. Blanking the message when there is no
           save button meant that on the public build Calculate changed nothing
           visible at the button — and the answer renders below the canvas,
           often off the bottom of the screen, so a correct result the reader
           never saw is indistinguishable from nothing happening. */
        msg.textContent = save ? 'calculated — not saved yet' : 'calculated';
        msg.className = 'fmsg ok';
        showVerdict(res.j);
        if (save) save.disabled = false;
        var _v = document.getElementById('shape-verdict');
        if (_v) _v.scrollIntoView({behavior: 'smooth', block: 'center'});
      })
      .catch(function (e) { msg.textContent = String(e.message || e); msg.className = 'fmsg bad'; });
  });

/* priv:start */   /* save a traced-photo volume */
  // Absent in the public build, where the calculator is offered but there is no
  // pool of ours to assign the answer to. An unguarded addEventListener here
  // throws and takes the REST OF THIS SCRIPT with it — including the calculator
  // the public build exists to provide.
  if (save) save.addEventListener('click', function () {
    if (!API) return;
    var msg = document.getElementById('shape-msg');
    msg.textContent = 'saving…'; msg.className = 'fmsg';
    fetch('/api/pool-shape', {method: 'POST', headers: postHeaders(),
                              body: JSON.stringify(Object.assign(shapeRecord(), {preview_gallons: lastPreview}))})
      .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.j.error || 'refused');
        msg.textContent = 'saved as the pool volume';
        msg.className = 'fmsg ok';
        /* Deliberately NOT reloading the page. A reload would throw away the
           photograph and the outline, so anyone wanting to adjust a depth would
           have to start over — and the whole verdict below is the reason to
           stay. */
        showVerdict(res.j);
      })
      .catch(function (e) { msg.textContent = String(e.message || e); msg.className = 'fmsg bad'; });
  });
/* priv:end */
})();


/* ------------------------------------------------ volume from a stock design ---
   The third route to a volume, for knowing the shape and the size without having
   a usable photograph. The designs are unit OUTLINES exported from templates.py,
   so scaling one here produces the same kind of polygon the tracing tool
   produces and the server measures it with the same code. There is no per-shape
   volume formula on either side. */
(function () {
  var root = document.getElementById('design-tool');
  if (!root) return;
  var TPL = JSON.parse(document.getElementById('pool-templates').textContent);
  var chosen = null;

  function num(id) { var v = parseFloat((document.getElementById(id) || {}).value);
                     return isFinite(v) ? v : 0; }

  root.querySelectorAll('[data-design]').forEach(function (b) {
    b.addEventListener('click', function () {
      chosen = b.dataset.design;
      root.querySelectorAll('[data-design]').forEach(function (o) {
        o.setAttribute('aria-pressed', o === b ? 'true' : 'false');
      });
      document.getElementById('design-note').textContent = TPL[chosen].note;
      recompute();
    });
  });

  function record() {
    var rec = {
      template: chosen,
      length_ft: num('t-length'), width_ft: num('t-width'),
      shallow_ft: num('t-shallow'), deep_ft: num('t-deep'),
      ledge_sqft: num('t-ledge'), ledge_depth_ft: num('t-ledge-d')
    };
    var dia = num('t-spa-dia');
    if (dia > 0) {
      /* A round spa needs no tracing — it is a circle of the given diameter,
         emitted as points so it travels the same path as a traced one. */
      var pts = [], r = dia / 2;
      for (var i = 0; i < 48; i++) {
        var a = 2 * Math.PI * i / 48;
        pts.push([r + r * Math.cos(a), r + r * Math.sin(a)]);
      }
      rec.spa = {points: pts, depth_ft: num('t-spa-depth'),
                 bench_width_ft: 16 / 12, bench_depth_ft: 16 / 12,
                 step_sqft: 3, step_depth_ft: 0.9, unit_feet: true};
    }
    return rec;
  }

  var save = document.getElementById('design-save');
  function recompute() {
    var out = document.getElementById('design-result');
    if (!chosen) {
      out.innerHTML = '<span class="ph">Pick a design and set the dimensions.</span>';
      if (save) save.disabled = true;
      document.getElementById('design-calc').disabled = true; return;
    }
    var L = num('t-length'), W = num('t-width');
    if (!(L > 0 && W > 0)) {
      out.innerHTML = '<span class="ph">Give it a length and a width.</span>';
      if (save) save.disabled = true; document.getElementById('design-calc').disabled = true; return;
    }
    var area = TPL[chosen].unit_area * L * W;
    var d1 = Math.min(num('t-shallow'), num('t-deep'));
    var d2 = Math.max(num('t-shallow'), num('t-deep'));
    /* A preview only: the midpoint here, while the server integrates depth over
       the real area distribution. On a lopsided design the two differ, and the
       saved figure is the server's — which is what the verdict below reports. */
    var avg = (d1 + d2) / 2;
    var ledge = Math.max(0, Math.min(num('t-ledge'), area));
    var gal = ((area - ledge) * avg + ledge * num('t-ledge-d')) * 7.480519;
    out.innerHTML =
      '<span class="pl">Approximately</span>' +
      '<span class="eff"><b>' + Math.round(gal).toLocaleString() + '</b>gallons</span>' +
      '<span class="eff"><b>' + Math.round(area).toLocaleString() + '</b>sq ft</span>' +
      '<span class="eff"><b>' + (TPL[chosen].unit_area * 100).toFixed(0) +
        '%</b>of a ' + L + '&times;' + W + ' box</span>' +
      '<span class="ph">calculate for the exact figure</span>';
    document.getElementById('design-calc').disabled = false;
    if (save) save.disabled = true;
  }

  ['t-length','t-width','t-shallow','t-deep','t-ledge','t-ledge-d',
   't-spa-dia','t-spa-depth'].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener('input', recompute);
  });

  document.getElementById('design-calc').addEventListener('click', function () {
    if (!API) return;
    var msg = document.getElementById('design-msg');
    msg.textContent = 'calculating…'; msg.className = 'fmsg';
    fetch('/api/pool-shape/compute', {method: 'POST', headers: postHeaders(),
                                      body: JSON.stringify(record())})
      .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.j.error || 'refused');
        msg.textContent = save ? 'calculated — not saved yet' : 'calculated';
        msg.className = 'fmsg ok';
        showVerdictIn('design-verdict', res.j);
        if (save) save.disabled = false;
        var _dv = document.getElementById('design-verdict');
        if (_dv) _dv.scrollIntoView({behavior: 'smooth', block: 'center'});
      })
      .catch(function (e) { msg.textContent = String(e.message || e); msg.className = 'fmsg bad'; });
  });

/* priv:start */   /* save a stock-design volume */
  if (save) save.addEventListener('click', function () {
    if (!API) return;
    var msg = document.getElementById('design-msg');
    msg.textContent = 'saving…'; msg.className = 'fmsg';
    fetch('/api/pool-shape', {method: 'POST', headers: postHeaders(),
                              body: JSON.stringify(record())})
      .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.j.error || 'refused');
        msg.textContent = 'saved as the pool volume'; msg.className = 'fmsg ok';
        showVerdictIn('design-verdict', res.j);
      })
      .catch(function (e) { msg.textContent = String(e.message || e); msg.className = 'fmsg bad'; });
  });
/* priv:end */
})();




/* priv:start */
/* ----------------------------------------------------------- pool controls ---
   Every button goes through one path so the confirmation rules, the disabled
   state and the result handling cannot diverge between them. */
(function () {
  var msgEl = document.getElementById('ctl-msg');
  if (!msgEl) return;

  /* Confirmation is not for everything — a pool light is cheap to get wrong and
     a dialog on it just teaches people to dismiss dialogs. It is reserved for
     the things that cost money or interrupt something.

     AND IT DEPENDS ON THE DIRECTION, which the first version of this did not.
     Keying the message off the device alone meant pressing On for the filter
     pump produced "Turning the pump off stops circulation, filtration and
     chlorine production. Switch it on?" — a warning about the opposite of what
     was being done, attached to a question about what was. A dialog that
     describes the wrong action is worse than no dialog: it is read once,
     found to be nonsense, and dismissed unread from then on.

     The rule is: warn about the consequence of THIS press, or do not warn. */
  function confirmFor(dev, kind, value, row) {
    var label = (row.querySelector('.ctl-h b') || {}).textContent || dev;

    if (kind === 'off') {
      if (dev === 'Filter_Pump') {
        return 'Turning the pump off stops circulation, filtration and chlorine ' +
               'production.\\n\\nTurn the pump off?';
      }
      return null;          /* turning a light or a feature off costs nothing */
    }

    if (kind === 'on') {
      if (dev === 'Spa') {
        return 'Spa mode diverts flow and runs the cell harder, and on this panel ' +
               'it is what the spa heater follows.\\n\\nSwitch the spa on?';
      }
      return null;          /* switching things on is cheap and reversible */
    }

    /* kind === 'set' */
    if (dev === 'Pool_Heater' || dev === 'Spa_Heater') {
      return 'Gas heating is the most expensive thing this pool does.\\n\\nSet ' +
             label.toLowerCase() + ' to ' + value + '\\u00B0F?';
    }
    if (dev === 'Freeze_Protect') {
      return null;          /* a safety setting; second-guessing it helps nobody */
    }
    if (dev === 'SWG/Percent') {
      var now = row.getAttribute('data-current');
      var from = (now !== null && now !== '') ? 'now ' + now + '%, ' : '';
      return 'Chlorine takes days to find its new level, so changing it again ' +
             'before it settles means neither change can be attributed ' +
             'afterwards.\\n\\nSet cell output to ' + value + '% (' + from +
             'and then wait)?';
    }
    return null;
  }

  function say(el, text, cls) {
    el.textContent = text;
    el.className = 'fmsg ' + (cls || '');
    /* Any new message supersedes a watch on the old one. */
    delete el.dataset.awaiting;
    delete el.dataset.awaitingWhat;
  }

  /* Called by the pending poll when a device's command reaches its end. The
     message written at send time said "waiting for the panel to report it" and
     NOTHING ever rewrote it, so it went on saying that after the panel had
     reported -- beside a badge that had already cleared. A page that states two
     different things about one command is the defect the pending badge exists
     to prevent, reappearing in the prose next to it. */
  window.notePanelResult = function (device, agreed) {
    document.querySelectorAll('.fmsg').forEach(function (el) {
      if (el.dataset.awaiting !== device) return;
      var what = el.dataset.awaitingWhat || 'the command';
      if (agreed === true)       say(el, what + ' — confirmed by the panel', 'ok');
      else if (agreed === null)  say(el, what + ' — sent; this device reports no '
                                             + 'state to confirm it against', 'ok');
      else                       say(el, what + ' — the panel has still not '
                                             + 'reported it', 'bad');
    });
  };

  function send(body, el, label) {
    if (!API) { say(el, 'Needs the write API.', 'bad'); return; }
    say(el, 'sending…');
    fetch('/api/control', {method: 'POST', headers: postHeaders(),
                           body: JSON.stringify(body)})
      .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.j.error || 'refused');
        var j = res.j;
        if (j.pending) {
          /* Sent but not yet acknowledged. Saying "done" here would be a lie the
             page could not take back. */
          say(el, j.what + ' — sent, waiting for the panel', '');
        } else if (j.result && j.result.ok) {
          say(el, j.what + ' — accepted, waiting for the panel to report it', 'ok');
          /* Tagged so the pending poll can come back and finish this sentence.
             Without the tag the line is a one-shot written at send time, which
             is exactly how it came to outlive the thing it described. */
          if (body.device) {
            el.dataset.awaiting = body.device;
            el.dataset.awaitingWhat = j.what;
          }
          if (window.markPending && body.device) {
            window.markPending(body.device,
              body.action === 'setpoint' ? String(body.value)
                                         : (body.value ? 'on' : 'off'),
              body.action === 'setpoint');
            /* The command that was just issued belongs in the log below it. */
            if (window.reloadHistory) window.reloadHistory();
          }
          /* all_off carries no single device, so the clause above skipped it and
             the one command that touches every circuit was the one with no
             reading-based confirmation behind it. The agent returns what it
             actually switched; mark exactly those, and nothing it could not. */
          if (window.markPending && body.action === 'all_off'
              && Array.isArray(j.result.detail)) {
            var bad = j.result.failed || [];
            j.result.detail.forEach(function (row) {
              var dev = row && row[0];
              if (dev && bad.indexOf(dev) === -1) window.markPending(dev, 'off');
            });
          }
        } else {
          var r2 = j.result || {};
          say(el, r2.refused || r2.error || 'the agent did not accept it', 'bad');
        }
      })
      .catch(function (e) { say(el, String(e.message || e), 'bad'); });
  }

  document.querySelectorAll('.ctl[data-device]').forEach(function (row) {
    var dev = row.dataset.device;
    var el = row.querySelector('.ctl-msg') || msgEl;

    row.querySelectorAll('.ctl-on, .ctl-off').forEach(function (b) {
      b.addEventListener('click', function () {
        var on = b.dataset.v === '1';
        var q = confirmFor(dev, on ? 'on' : 'off', on ? 1 : 0, row);
        if (q && !confirm(q)) return;
        send({action: 'set', device: dev, value: on ? 1 : 0}, el, dev);
      });
    });

    var setBtn = row.querySelector('.ctl-setpoint');
    if (setBtn) setBtn.addEventListener('click', function () {
      var input = row.querySelector('.ctl-val');
      var v = input.value.trim();
      /* An empty box is not a zero. The heaters have no reported setpoint to
         pre-fill, so sending "" as a number would silently become 0 and turn
         the heating off when somebody meant to read the current value. */
      if (v === '') {
        say(el, 'Type a value first — this panel does not report its current setpoint.', 'bad');
        input.focus();
        return;
      }
      var q = confirmFor(dev, 'set', v, row);
      if (q && !confirm(q)) return;
      send({action: 'setpoint', device: dev, value: Number(v)}, el, dev);
    });
  });

  var off = document.getElementById('ctl-alloff');
  if (off) off.addEventListener('click', function () {
    /* Named from the switches actually on the page rather than a sentence
       written once and left to drift. If a circuit is added to the catalogue
       it appears here automatically; a hand-written list would have gone on
       saying "all the lights" while quietly turning off something else. */
    var names = [];
    document.querySelectorAll('#tab-control .ctl[data-device]').forEach(function (r) {
      var d = r.dataset.device;
      if (d === 'Filter_Pump' || r.classList.contains('set')) return;
      var b = r.querySelector('.ctl-h b');
      if (b) names.push(b.textContent.trim().toLowerCase());
    });
    var list = names.length ? names.join(', ') : 'every circuit';
    if (!confirm('This turns off ' + list + ' — and then the filter pump last, ' +
                 'so nothing is left running without circulation.' +
                 '\\n\\nThe heaters and the cell setting are not changed.' +
                 '\\n\\nTurn everything off?')) return;
    send({action: 'all_off'}, msgEl, 'all off');
  });


  /* ---- live agent presence ----------------------------------------------
     The rendered page cannot know this. It is frequently written by a
     different process from the one serving it — cron's render, or a manual
     one — and each has its own in-memory queue holding no connections. So the
     page ships both banners hidden and asks the serving process here. */
  (function () {
    var checking = document.getElementById('ctl-checking');
    var offline  = document.getElementById('ctl-offline');
    if (!checking && !offline) return;

    function settle(connected) {
      if (checking) checking.hidden = true;
      if (offline) offline.hidden = !!connected;
      /* The live half of the staleness banner. The render states that the
         readings are old, which it knows; whether a command would reach the
         panel is a fact only this fetch has. With no agent connected the
         offline banner above has already said nothing can reach the panel, and
         the two used to contradict each other one line apart. */
      document.querySelectorAll('.ctl-stale-live').forEach(function (el) {
        el.textContent = connected
          ? ' A command will still be delivered, but check the result.'
          : '';
      });
      /* Disable the controls rather than let them be pressed into a void. The
         server would refuse anyway, but a button that looks live and answers
         "no agent is connected" teaches people to distrust the page. */
      document.querySelectorAll('#tab-control .ctl button, #ctl-alloff, #ctl-resync')
        .forEach(function (b) { b.disabled = !connected; });
    }

    /* A ONE-SHOT WAS THE WRONG SHAPE FOR A FACT THAT CHANGES.
       This fired once at load and never again, so when the Pi went away
       afterwards every circuit and setpoint button stayed enabled and the
       "no agent is connected" banner stayed hidden — indefinitely. Measured:
       health.agents went 1 -> 0 and the page went on showing live controls 22
       seconds later, and on a tab open for 688 seconds. Pressing one then
       produced exactly the refusal the comment above says the disabling exists
       to prevent.

       It was wrong in both directions: a tab loaded while the Pi was down kept
       the banner and stayed locked out after the Pi came back, stating
       something false on screen until somebody reloaded.

       The role lockOut() latch calls this poll "the worst of the six" handlers
       that used to undo it — and the lock it fights was itself a one-shot. The
       latch still wins where it applies: a `view` reader's controls stay
       disabled through every one of these, because the own accessor swallows
       the write. This only re-enables what the reader was allowed to use. */
    function checkAgent() {
      fetch('/api/health', {cache: 'no-store'})
        .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
        .then(function (j) { settle(Number(j.agents || 0) > 0); })
        .catch(function () { settle(false); });
    }
    checkAgent();
    /* Slow enough not to be chatty, fast enough that the page is not lying for
       long. Paused while the tab is hidden: a phone in a pocket polling all
       night is how a background tab becomes a battery complaint. */
    setInterval(function () {
      if (!document.hidden) checkAgent();
    }, 20000);
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) checkAgent();
    });
  })();

  var re = document.getElementById('ctl-resync');
  if (re) re.addEventListener('click', function () {
    send({action: 'resync'}, msgEl, 'resync');
  });
})();
/* priv:end */

/* priv:start */   /* refresh on demand */
/* ------------------------------------------------------ refresh on demand ---
   Split by source rather than one button, because they do not cost the same: the
   controller is a local HTTP call and can be polled all day, while WaterGuru asks
   for no more than a call or two daily. A single "refresh" would quietly spend
   those every time somebody wanted to see whether the pump had come on. */
(function () {
  var btn = document.getElementById('refresh-btn');
  if (!btn) return;
  var menu = document.getElementById('refresh-menu');
  var msg = document.getElementById('refresh-msg');

  function open(v) {
    menu.hidden = !v;
    btn.setAttribute('aria-expanded', v ? 'true' : 'false');
  }
  btn.addEventListener('click', function (e) {
    e.stopPropagation();
    if (!API) {
      msg.textContent = 'Needs the write API — start bin/serve.';
      open(true); return;
    }
    open(menu.hidden);
  });
  document.addEventListener('click', function (e) {
    if (!menu.hidden && !menu.contains(e.target)) open(false);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') open(false);
  });

  menu.querySelectorAll('.rsrc').forEach(function (b) {
    b.addEventListener('click', function () {
      if (!API) return;
      var srcs = b.dataset.src.split(',');
      menu.querySelectorAll('.rsrc').forEach(function (o) { o.disabled = true; });
      /* NO innerHTML ANYWHERE BELOW THIS LINE. What a collector prints is
         what a lab said to it, and this block is the one place third-party
         text reaches the page. The rule is the whole block rather than the
         two lines that interpolate today, because the next field somebody
         adds to a result would be the exception nobody noticed. */
      msg.textContent = '';
      msg.appendChild(document.createElement('span')).className = 'spin';
      msg.appendChild(document.createTextNode(
        'running ' + srcs.length +
        (srcs.length === 1 ? ' collector…' : ' collectors…')));
      fetch('/api/refresh', {method: 'POST', headers: postHeaders(),
                             body: JSON.stringify({sources: srcs})})
        .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
        .then(function (res) {
          if (!res.ok) throw new Error(res.j.error || 'refused');
          msg.textContent = '';
          res.j.results.forEach(function (x) {
            var d = document.createElement('div');
            d.className = 'rline ' + (x.ok ? 'ok' : 'bad');
            d.appendChild(document.createElement('b')).textContent = x.label;
            d.appendChild(document.createTextNode(' ' + x.message + ' '));
            d.appendChild(document.createElement('i')).textContent = x.seconds + 's';
            msg.appendChild(d);
          });
          var done = document.createElement('div');
          done.className = 'rline';
          done.textContent = 'Reloading…';
          msg.appendChild(done);
          /* The collectors re-render the page when they finish, so the file on
             disk is already newer than what is on screen. */
          setTimeout(function () { location.reload(); }, 1400);
        })
        .catch(function (e) {
          msg.textContent = '';
          var bad = document.createElement('div');
          bad.className = 'rline bad';
          bad.textContent = e.message || e;
          msg.appendChild(bad);
          menu.querySelectorAll('.rsrc').forEach(function (o) { o.disabled = false; });
        });
    });
  });
})();
/* priv:end */

/* ------------------------------------------------------- section jump-list ---
   Highlights whichever section is currently in view. Built from the page rather
   than declared, so it cannot point at a heading that no longer exists. */
(function () {
  var navs = [].slice.call(document.querySelectorAll('.subnav'));
  if (!navs.length) return;
  var links = [].slice.call(document.querySelectorAll('.subnav [data-jump]'));
  var byId = {};
  links.forEach(function (a) { byId[a.dataset.jump] = a; });

  links.forEach(function (a) {
    a.addEventListener('click', function (e) {
      e.preventDefault();
      var el = document.getElementById(a.dataset.jump);
      if (el) el.scrollIntoView({behavior: 'smooth', block: 'start'});
    });
  });

  if (!('IntersectionObserver' in window)) return;
  var seen = {};
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (en) { seen[en.target.id] = en.intersectionRatio; });
    var best = null, bestR = 0;
    Object.keys(seen).forEach(function (id) {
      if (seen[id] > bestR && byId[id] &&
          byId[id].closest('.panel') && !byId[id].closest('.panel').hidden) {
        best = id; bestR = seen[id];
      }
    });
    links.forEach(function (a) {
      a.setAttribute('aria-current', a.dataset.jump === best ? 'true' : 'false');
    });
  }, {rootMargin: '-70px 0px -55% 0px', threshold: [0, 0.15, 0.5, 1]});
  Object.keys(byId).forEach(function (id) {
    var el = document.getElementById(id);
    if (el) io.observe(el);
  });
})();


/* priv:start */   /* credentials, notification settings, manual reading, settings form */
/* ------------------------------------------------------------ credentials ---
   A password typed here goes straight into the encrypted vault and never comes
   back. The field is cleared on success and the page shows only whether
   something is stored and under which username — not a masked value, not a
   length, nothing that narrows a guess. */
(function () {
  document.querySelectorAll('.cred').forEach(function (row) {
    var svc = row.dataset.service;
    var user = row.querySelector('.c-user');
    var pass = row.querySelector('.c-pass');
    var msg = row.querySelector('.c-msg');

    function post(body, done) {
      if (!API) { msg.textContent = 'Needs the write API — start bin/serve.'; return; }
      msg.textContent = 'saving…'; msg.className = 'fmsg c-msg';
      fetch('/api/credential', {method: 'POST', headers: postHeaders(),
                                body: JSON.stringify(body)})
        .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
        .then(function (res) {
          if (!res.ok) throw new Error(res.j.error || 'refused');
          pass.value = '';
          done(res.j);
        })
        .catch(function (e) {
          msg.textContent = String(e.message || e); msg.className = 'fmsg c-msg bad';
        });
    }

    row.querySelector('.c-save').addEventListener('click', function () {
      var store = (row.querySelector('.c-store') || {}).value || 'vault';
      /* Say so out loud. Choosing the file is choosing a plaintext password on
         disk, and somebody who picked it from a dropdown without reading the
         note beside it should still be told before it is written. */
      if (store === 'file' && !confirm(
            'A plain file stores this password unencrypted.\\n\\n' +
            'Anything that can read your home directory can read it. The vault ' +
            'keeps its key where the operating system defends it.\\n\\n' +
            'Write it as a plain file anyway?')) return;

      post({service: svc, username: user.value, password: pass.value, store: store},
           function (j) {
        msg.textContent = j.store === 'file'
          ? 'written to ' + j.path + ', mode 600 — not encrypted'
          : 'stored, encrypted with the key in the ' +
            (j.key_location === 'keychain' ? 'keychain' : 'key file');
        msg.className = 'fmsg c-msg ' + (j.store === 'file' ? 'warn' : 'ok');
        pass.placeholder = 'unchanged';
        row.querySelector('.c-forget').disabled = false;
      });
    });

    row.querySelector('.c-forget').addEventListener('click', function () {
      if (!confirm('Remove the stored login for this service? The collector will stop ' +
                   'working until it is entered again.')) return;
      post({service: svc, forget: true}, function () {
        msg.textContent = 'forgotten'; msg.className = 'fmsg c-msg';
        user.value = ''; pass.placeholder = 'not set';
        row.querySelector('.c-forget').disabled = true;
      });
    });
  });

  var imp = document.getElementById('cred-import');
  if (imp) imp.addEventListener('click', function () {
    if (!API) return;
    var m = document.getElementById('cred-import-msg');
    m.textContent = 'importing…';
    fetch('/api/credential/import', {method: 'POST', headers: postHeaders(),
                                     body: JSON.stringify({remove: false})})
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var n = (j.imported || []).length;
        m.textContent = n ? (n + ' imported — the plaintext files are still there, ' +
                             'delete them once you have checked a collector still runs')
                          : 'nothing to import';
        m.className = 'fmsg ok';
        setTimeout(function () { location.reload(); }, 1600);
      })
      .catch(function (e) { m.textContent = String(e); m.className = 'fmsg bad'; });
  });
})();

/* ------------------------------------------------- notification settings ---
   Checkboxes are the trap here: an unchecked box is simply ABSENT from FormData,
   so a naive read saves "leave it alone" for every switch anyone turns off, and
   nothing can ever be disabled. Every checkbox is therefore read from the DOM
   rather than from the form data. */
(function () {
  var form = document.getElementById('notify-form');
  if (!form) return;
  var msg = document.getElementById('notify-msg');

  function body() {
    var d = {};
    new FormData(form).forEach(function (v, k) { d[k] = v; });
    form.querySelectorAll('input[type=checkbox]').forEach(function (c) {
      d[c.name] = c.checked;
    });
    return d;
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (!API) return;
    msg.textContent = 'saving…'; msg.className = 'fmsg';
    fetch('/api/settings', {method: 'POST', headers: postHeaders(),
                            body: JSON.stringify(body())})
      .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.j.error || 'refused');
        msg.textContent = 'saved'; msg.className = 'fmsg ok';
      })
      .catch(function (er) { msg.textContent = String(er.message || er); msg.className = 'fmsg bad'; });
  });

  function test(channel, label) {
    if (!API) return;
    msg.textContent = 'sending…'; msg.className = 'fmsg';
    /* Save first. Testing the settings on screen rather than the ones on disk is
       what makes a test worth running — otherwise it checks whatever was saved
       last time and says nothing about the address just typed in. */
    fetch('/api/settings', {method: 'POST', headers: postHeaders(),
                            body: JSON.stringify(body())})
      .then(function () {
        return fetch('/api/notify-test', {method: 'POST', headers: postHeaders(),
                                          body: JSON.stringify({channel: channel})});
      })
      .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.j.error || 'refused');
        msg.textContent = label + ' sent' + (res.j.to ? ' to ' + res.j.to : '') +
                          ' — check it arrived';
        msg.className = 'fmsg ok';
      })
      .catch(function (er) { msg.textContent = String(er.message || er); msg.className = 'fmsg bad'; });
  }
  document.getElementById('notify-test-email').addEventListener('click', function () {
    test('email', 'Email');
  });
  document.getElementById('notify-test-desktop').addEventListener('click', function () {
    test('desktop', 'Desktop notification');
  });
})();

/* ------------------------------------------------ record a test result ---
   A third source alongside the two collectors. Blank fields are omitted
   rather than sent as empty strings, so "I only tested pH" records pH and
   claims nothing about the rest. */
(function () {
  var form = document.getElementById('reading-form');
  if (!form) return;
  var msg = document.getElementById('reading-msg');
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (!API) { msg.textContent = 'Needs the write API.'; msg.className = 'fmsg bad'; return; }
    var data = {};
    new FormData(form).forEach(function (v, k) {
      if (String(v).trim() !== '') data[k] = v;
    });
    msg.textContent = 'recording…'; msg.className = 'fmsg';
    fetch('/api/reading', {method: 'POST', headers: postHeaders(),
                           body: JSON.stringify(data)})
      .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.j.error || 'refused');
        var n = res.j.measures, was = res.j.replaced;
        if (wroteOk(msg, res.j,
                    (was ? 'replaced the test from that time — ' : '') +
                    n + ' measure' + (n === 1 ? '' : 's') + ' recorded')) {
          setTimeout(function () { location.reload(); }, 700);
        }
      })
      .catch(function (err) {
        msg.textContent = String(err.message || err); msg.className = 'fmsg bad';
      });
  });
})();

/* --------------------------------------------------------- settings form --- */
/* EVERY form that posts settings, not one named form. Settings holds more than
   one now -- the pool, and how often each lab is pulled -- and they go to the
   same endpoint with the same all-or-nothing semantics, so a second copy of
   this handler would be a second place for the checkbox rule below to be got
   wrong. It already was got wrong once, for exactly one checkbox. Each form
   carries data-settings and its own .fmsg. */
(function () {
  document.querySelectorAll('form[data-settings]').forEach(function (form) {
  var msg = form.querySelector('.fmsg');
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (!API) return;
    var data = {};
    new FormData(form).forEach(function (v, k) { data[k] = v; });
    /* EVERY checkbox, read from the DOM. An unchecked box is simply absent from
       FormData, so a naive read saves "leave it alone" for anything anyone turns
       OFF -- which made "the spa shares water with the pool" a one-way switch:
       it could be turned on, the save reported success, and it could never be
       turned off again. volume_estimated was singled out here by hand and
       spa_shares_water was not, which is exactly the shape that recurs every
       time a checkbox is added. The notify form eleven lines down already did
       this correctly; this is the same loop. */
    form.querySelectorAll('input[type=checkbox][name]').forEach(function (b) {
      data[b.name] = b.checked;
    });
    msg.textContent = 'saving…'; msg.className = 'fmsg';
    fetch('/api/settings', {method: 'POST', headers: postHeaders(),
                            body: JSON.stringify(data)})
      .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.j.error || 'refused');
        if (wroteOk(msg, res.j, 'saved')) {
          setTimeout(function () { location.reload(); }, 600);
        }
      })
      .catch(function (err) {
        msg.textContent = String(err.message || err); msg.className = 'fmsg bad';
      });
  });
  });
})();
/* priv:end */
</script>
</body></html>
"""

if __name__ == "__main__":
    path, size = build()
    print(f"  wrote {path}  ({size/1024:.1f} KB)")
