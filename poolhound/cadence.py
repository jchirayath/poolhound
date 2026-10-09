"""How often each lab is pulled, and whether it is due right now.

WHY THIS MODULE EXISTS

The cadence used to live in the crontab, and the crontab is on the HOST. The
container cannot edit it, and should not be able to: a web form that can rewrite
`/etc/cron.d` is a web form that can run anything as root. So for as long as
cron decided when a pull happened, "how often do you fetch WaterGuru" was a
question the product could state and not answer — Settings said the cadence and
then said to go and edit a file.

So cron no longer decides. It TICKS, hourly, and the collector asks this module
whether it is due. The cadence is a setting in `config.toml`, which the container
already owns and already writes — the same file the pool volume lives in, on the
share, surviving a `--force-recreate`. One fact, one owner, and the owner is now
the file a person can actually change.

WHAT A TICK COSTS, WHICH IS NOTHING. `due()` reads the run log and the config and
returns before any network call is made. A skipped tick spends no API call, logs
no attempt against the vendor, and records itself as `skipped` so the Collection
tab can tell "we chose not to" from "it broke".

THE FLOOR IS NOT A PREFERENCE. WaterGuru asks for no more than a call or two a
day and this product says in three places that it respects that. A dropdown that
offers "every hour" is a dropdown that breaks the promise on somebody's behalf,
so the floor is enforced HERE, in the due test that actually spends the call —
not only in the form. A config edited by hand on the share goes through the same
clamp, because the share is reachable by anybody who can reach the host and the
form is not the only way in.

UPGRADE IS A NO-OP. An install with no `[collection]` section keeps the cadence
its crontab already had: the defaults are read from `deploy/poolhound-cron`
itself, so the setting is opt-in and an existing deployment behaves exactly as it
did until somebody chooses otherwise. That is the same shape as `[access]`, and
for the same reason — a new setting must not change a running pool.
"""

import datetime as dt
import os
import re

from . import config

# The collectors whose cadence is a setting, and what bounds each one.
#
#   floor_hours  the shortest interval allowed, and why
#   default      the interval used when nobody has said
#
# watch and render are deliberately NOT here. They cost nothing, they are not
# somebody else's service, and their cadence is a property of the deployment
# rather than a choice about it — so they stay in the crontab, where a tick and
# a schedule are the same thing.
SOURCES = {
    "wg-collect": {
        "label": "WaterGuru",
        "setting": "waterguru",
        # TWO A DAY IS THE VENDOR'S NUMBER, not ours. The upstream project asks
        # for no more than one or two calls a day; twelve hours is the shortest
        # interval that honours the second of them, and nothing may go below it.
        "floor_hours": 12,
        "floor_why": "WaterGuru asks for no more than two calls a day",
        "default_hours": 24,
    },
    "leslies": {
        "label": "Leslie's",
        "setting": "leslies",
        # No published budget — it is a browser session, not an API. The floor
        # is politeness plus futility: one call returns the WHOLE history, so a
        # second one an hour later can only return the same rows again.
        "floor_hours": 6,
        "floor_why": "one call returns the whole history, so a second is a "
                     "re-read of rows we already have",
        "default_hours": 24,
    },
}

# What the form offers. Hours, because the interval is the fact; the label is
# how a person says it. Anything below a source's floor is dropped from ITS
# list rather than shown and refused — a control that offers a value the server
# will not accept is the defect ROLE_UI exists to catch, one layer down.
CHOICES = (
    (12, "twice a day"),
    (24, "once a day"),
    (48, "every other day"),
    (168, "once a week"),
)

# A daily-or-longer pull lands at a chosen hour; a shorter one cannot, because
# the interval already decides. Stated once, here, and the form says which case
# it is in rather than leaving a box that silently stops applying.
DAILY_HOURS = 24

# AND A CEILING, because the watchdog is not part of this choice. It alarms when
# free chlorine is 36 hours stale, and nothing in this module tells it not to —
# so a cadence longer than a week would put the product into permanent alarm
# about a state its owner had deliberately selected. A week is the longest
# interval offered and the longest accepted; anything beyond it is a decision to
# stop collecting, which is not a frequency.
CEILING_HOURS = 168

# CRON IS A TICK, AND THIS IS THE FILE THAT SAYS SO. Still parsed, for two
# reasons: watch and render really are scheduled there, and the pulls' DEFAULT
# hour comes from it so that an install which never opens Settings keeps the
# hour it already had.
CRON_FILE = ("deploy", "poolhound-cron")


def _section(cfg=None):
    cfg = cfg if cfg is not None else config.load()
    return cfg.get("collection") or {}


def cron_fields(path=None):
    """{tool: (minute, hour)} exactly as the crontab spells them.

    The raw fields, not an interpretation of them: what this answers is "what
    does the file say", and every reading of it is done by the callers below.
    """
    p = path or os.path.join(config.root(), *CRON_FILE)
    out = {}
    if not os.path.exists(p):
        return out
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^(\S+)\s+(\S+)\s+\S+\s+\S+\s+\S+\s+\S+\s+"
                         r".*collect\.sh\s+([a-z-]+)", line)
            if m:
                minute, hour, tool = m.groups()
                out[tool] = (minute, hour)
    return out


def configured(cfg=None):
    """Has anybody set a cadence, or is this install still on its defaults?

    Worth being able to say out loud. "Once a day because that is the default"
    and "once a day because somebody chose it" are different facts, and a page
    that cannot tell them apart invites the reader to assume the second.
    """
    sec = _section(cfg)
    return any(f"{s['setting']}_every_hours" in sec or
               f"{s['setting']}_hour_utc" in sec
               for s in SOURCES.values())


def every_hours(tool, cfg=None):
    """The interval between pulls, in hours, clamped to the source's floor.

    CLAMPED AND NOT REFUSED, because this is the reading path: a config that
    somehow holds 1 must still produce a usable schedule rather than an
    exception on the page that would report it. The WRITING path refuses —
    server.SETTABLE will not store a value below the floor — so a clamp here
    only ever fires on a file edited by hand, which is exactly the case the
    form cannot police.
    """
    src = SOURCES.get(tool)
    if src is None:
        return None
    raw = _section(cfg).get(f"{src['setting']}_every_hours")
    try:
        v = int(float(raw))
    except (TypeError, ValueError):
        v = src["default_hours"]
    return min(max(v, src["floor_hours"]), CEILING_HOURS)


def hour_utc(tool, cfg=None, cron=None):
    """Which hour of the day a daily-or-longer pull lands in, UTC.

    None for a sub-daily cadence: with two pulls a day there is no single hour
    to name, and inventing one would put a figure on the page that nothing
    honours. The caller says "not applicable" rather than printing a time the
    code ignores.

    THE DEFAULT IS THE CRONTAB'S OWN HOUR. That is what makes this setting
    opt-in: an install that never touches it keeps the hour it has been running
    at, rather than being moved to whatever constant this file might have
    chosen. The crontab's hour is a tick now — `0 * * * *` — and a `*` there
    means the file no longer names one, so the last resort is the hour the
    daily pulls were on when they were cron's to decide.
    """
    src = SOURCES.get(tool)
    if src is None or every_hours(tool, cfg) < DAILY_HOURS:
        return None
    raw = _section(cfg).get(f"{src['setting']}_hour_utc")
    try:
        v = int(float(raw))
        if 0 <= v <= 23:
            return v
    except (TypeError, ValueError):
        pass
    fields = cron_fields() if cron is None else cron
    spelled = (fields.get(tool) or ("", ""))[1]
    if spelled.isdigit() and 0 <= int(spelled) <= 23:
        return int(spelled)
    # The hour the pulls ran at when cron owned them, and the reason it is this
    # one: the WaterGuru pod measures between 19:37 and 21:15 UTC, so a pull
    # before that fetches the previous day's reading every time. It did, for
    # five days, and that is the whole argument for not defaulting to midnight.
    return 23


def human(tool, cfg=None, cron=None):
    """The cadence as a sentence, which is what every page states."""
    n = every_hours(tool, cfg)
    if n is None:
        return None
    word = dict(CHOICES).get(n)
    h = hour_utc(tool, cfg, cron)
    if h is None:
        return word or f"every {n} hours"
    # FROM, not AT. The hour is a floor, not an appointment: cron ticks and the
    # first tick at or after that hour is the one that pulls. "at 23:00" would
    # be a promise about a minute, and Leslie's tick is at :05.
    at = f"{h:02d}:00 UTC"
    return f"{word} from {at}" if word else f"every {n} hours, from {at}"


def clock(tool, cfg=None, cron=None):
    """The same fact at label width — what fits beside an arrow in a diagram."""
    h = hour_utc(tool, cfg, cron)
    if h is not None:
        return f"{h:02d}:00"
    n = every_hours(tool, cfg)
    return None if n is None else f"every {n}h"


def _last_success(tool, cfg=None):
    """When this tool last COMPLETED, or None.

    Completed, not attempted. A pull that raised must be retried on the next
    tick rather than counted as this period's pull — otherwise a vendor outage
    at the due hour costs the whole day's reading, and the product would report
    itself as having collected.
    """
    from . import render, runlog
    for r in runlog.rows(cfg):
        if r.get("tool") != tool:
            continue
        if (r.get("outcome") or "").strip() != "ok":
            continue
        d = render.when(r.get("at", ""))
        # A future timestamp is not a recent run. The clock on a machine that
        # wrote this row is not necessarily this one's, and is_future() exists
        # because a skewed writer once made a reading look newer than now — a
        # row accepted here would suppress every pull until the clock caught up.
        if d and not render.is_future(r.get("at", "")):
            return d
    return None


# HOW EARLY A TICK MAY COUNT. Cron fires on the hour; a pull that succeeded at
# 23:04 would put the next 24-hour boundary at 23:04 tomorrow, which the 23:00
# tick misses by four minutes — so it would run at 00:00 and again an hour later
# the day after, walking the daily pull right around the clock. Half an hour of
# slack holds the hour still without ever letting two pulls into one interval,
# because no interval offered here is under twelve hours.
TICK_SLACK = dt.timedelta(minutes=30)


def due(tool, cfg=None, now=None, cron=None):
    """(is it due, why) for one source, before anything is spent.

    Called by the collector at the top, ahead of any network call: a tick that
    is not due must cost nothing at the vendor, which is the entire reason the
    crontab may tick hourly against a budget of two calls a day.

    A tool with no configured cadence is always due — that is how `--dry-run`, a
    manual run and a source outside SOURCES all keep working. Saying no to a
    tool this module has never heard of would silently stop a collector by
    omission, which is how salt-cell boost went missing from Help.
    """
    if tool not in SOURCES:
        return True, "not a scheduled pull — running because it was asked for"
    # AWARE OR NAIVE, BECAUSE THE TWO COMPARISONS BELOW WANT DIFFERENT THINGS.
    #
    # Elapsed time is measured against render.when(), which returns naive local
    # by design so every timestamp on the page sits on the pool's clock. The
    # hour gate is a UTC hour, because that is what the setting names and what
    # the crontab's own comment spends a paragraph on.
    #
    # Taking only naive-local made this function machine-dependent to CALL: a
    # caller passing 23:00 meant 23:00 UTC on the server, where the zone is
    # Etc/UTC, and 06:00 UTC on a workstation in Pacific. Its own test was
    # written the wrong way round and that is how this was found. Accepting
    # both, and deriving each comparison's clock explicitly, is what makes a
    # case about the hour gate mean the same thing everywhere.
    now = now or dt.datetime.now(dt.timezone.utc)
    now_local = (now.astimezone().replace(tzinfo=None) if now.tzinfo else now)
    last = _last_success(tool, cfg)
    n = every_hours(tool, cfg)
    if last is None:
        return True, "nothing has been collected yet"
    # NAIVE-LOCAL ON BOTH SIDES. render.when() returns naive local by design, so
    # every timestamp on the page sits on the pool's clock; now() is the same,
    # and an elapsed time is the one comparison that does not care which zone
    # they share as long as they share one.
    if (now_local - last) < dt.timedelta(hours=n) - TICK_SLACK:
        from .render import ago
        return False, (f"last pull was {ago(last.isoformat())} and the cadence "
                       f"is {human(tool, cfg, cron)}")
    h = hour_utc(tool, cfg, cron)
    # AND THE HOUR GATE IS EXPLICITLY UTC, which the elapsed check above is
    # deliberately not. `now.hour` would be the LOCAL hour, and comparing it to
    # a UTC hour is right only on a host whose zone is UTC — the server's is,
    # which is exactly how this would have been wrong everywhere else and
    # correct in production, the hardest kind of bug to see. The crontab
    # already carries a paragraph about a timezone assumption that held on one
    # machine and not the other.
    if h is not None and _utc_hour(now) < h:
        return False, (f"due today, but not before {h:02d}:00 UTC — "
                       f"the cadence is {human(tool, cfg, cron)}")
    return True, f"due — the cadence is {human(tool, cfg, cron)}"


def _utc_hour(when):
    """The UTC hour of an aware OR naive datetime. A naive one is taken as this
    host's zone, which is what astimezone() does with one and what every other
    reader of these timestamps assumes."""
    return when.astimezone(dt.timezone.utc).hour


def tick_gate(tool, by, cfg=None, now=None):
    """(should this run go ahead, why) for one invocation of a collector.

    ONE PLACE, because both collectors need it and a gate spelled twice is a
    gate that will eventually disagree with itself — which is the defect a
    third of this project's review findings were.

    A CRON TICK IS GATED; A PERSON IS NOT. Cron fires hourly now and the
    cadence decides, so a tick that is not due must go no further. But the
    Refresh control exists precisely to spend a call on demand, and its own
    card says so — refusing a human who asked would be the product declining
    to do the one thing that button is for. `--dry-run` and `--from-file` come
    through here as manual too, which is what keeps parser development working
    with no override to remember.

    The caller exits 0 on a refusal. A tick that correctly declined to spend an
    API call has not failed, and reporting it as a failure would put the
    watchdog into alarm every hour on a pool that is fine.
    """
    if by == "cron":
        return due(tool, cfg, now=now)
    return True, f"started by hand ({by}) — the cadence does not gate this"


def report(cfg=None, now=None):
    """One line per settable source: tool, hours since its last SUCCESS, the
    configured interval, and whether it is overdue.

    FOR bootstrap-server.sh --check, and it exists because moving the cadence
    off the crontab broke that check in the direction that matters. It graded
    collection on the status file collect.sh writes, which now records the
    hourly TICK rather than a pull -- so "wg-collect ran 12m ago, exit 0" would
    have been true and printed green on a host that had not successfully pulled
    for a week. The status file still answers "is cron ticking"; this answers
    "is anything being collected", and they are different questions that looked
    like one for as long as a tick and a pull were the same event.

    Tab-separated because the caller is awk, and printed rather than returned
    because the caller is a shell on another machine.
    """
    now = now or dt.datetime.now()
    out = []
    for tool in sorted(SOURCES):
        n = every_hours(tool, cfg)
        last = _last_success(tool, cfg)
        if last is None:
            out.append(f"{tool}\t-1\t{n}\tnever")
            continue
        hours = (now - last).total_seconds() / 3600.0
        # TWO INTERVALS BEFORE IT IS OVERDUE, the same rule the Collection tab
        # applies to a late collector: one missed pull is a blip, two is a
        # pattern, and crying wolf is how a status line stops being read.
        out.append(f"{tool}\t{hours:.1f}\t{n}\t"
                   f"{'overdue' if hours > n * 2 else 'ok'}")
    return "\n".join(out)


if __name__ == "__main__":                        # pragma: no cover
    # DELEGATED, like selftest's. `python -m` runs this file as a SECOND module
    # object, so anything that reached module state here would be reading a
    # different copy from the one the package holds -- which is exactly how
    # twelve check modules' failures came to be invisible to the image build.
    # report() touches no module state, but the habit is the point.
    import poolhound.cadence as _canonical
    print(_canonical.report())
