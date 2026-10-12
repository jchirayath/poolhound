"""Commands that wait for a time, and the ones that switch off what was left on.

WHAT THIS IS

"Spa ready at seven" and "the pool light off at eleven, every night" -- and,
the half that matters more, "and switch it off again afterwards". A schedule is
a DEFERRED COMMAND: somebody with `operate` decides now what the pool will be
told later. Everything below follows from taking that sentence literally.

WHERE IT RUNS: ON THE PI (docs/LOCAL-AND-SYNC.md)

The same code runs on both machines, and only one of them fires. The PI does,
through pirunner: so a schedule fires when the internet or the server is down,
poolhound local can make and change them, and nothing can fire twice. It
issues each command through the agent's own path -- commands.validate() and
the Guard, with its 300 s heater cooldown -- and judges it by what the panel
reports afterwards. It keeps this file in /var/lib/poolhound, written only
when something changed (tick() writes nothing on a quiet look), because the
card is the thing that has died twice.

The SERVER keeps a copy, kept in step by sync.py, and fires nothing once the
Pi has synced (look(), pi_runs()). Its thread withdraws a schedule whose author
lost the right, which the Pi cannot check, and mails the alerts the Pi
records, which the Pi cannot send. Until the Pi has synced -- an older agent --
the server fires them as it always did, through server.command(), the function
a button press reaches.

WHY IT IS ON DISK AND THE QUEUE IS NOT

queue_.py is in memory on purpose -- a command older than five minutes is
refused on arrival, so persisting the queue only preserves things too old to
run. A schedule is the opposite: its whole value is in surviving the hours
before it is due, and `docker compose up --force-recreate` empties the process.
So it lives in `$POOLHOUND_DATA/schedules.json`, read-modify-written under
locking.exclusive("schedules") and replaced atomically.

WALL CLOCK, AND A STAMP FROM THE FUTURE IS NOT BELIEVED

The same rule ask-budget.json and the Pi's guard follow, for the same reason:
monotonic time restarts with the process, and the process restarting is the
event this has to read across. Which means a clock that jumped backwards can
leave stamps in the future. Each kind is handled by what it is FOR:

  * record-keeping stamps (`created`, `alive_at`, a history row) from the
    future are dropped -- believing them would hold a schedule's first run, or
    the watchdog's "the scheduler is alive", hostage to a clock that was wrong;
  * an auto-off whose `due` lies further ahead than any auto-off can be set
    for is treated as due NOW. Dropping it would leave a heater burning; the
    only safe direction for a corrupt off is sooner.

A recurring schedule remembers WHICH LOCAL DATE it last ran for, not the
instant, so a clock stepping back two minutes after 19:00 cannot run the 19:00
spa twice.

THE TWO DIRECTIONS ARE NOT SYMMETRICAL

This is the safety argument in one paragraph. An ON delivered late is a gas
heater lit for nobody: "spa at 19:00" arriving at 23:40 because the server was
down is exactly the failure commands.MAX_AGE_S exists to prevent, one layer up.
So an ON is run only within GRACE_S of its time; past that it is recorded as
MISSED, on the page and in an alert, and never run. An OFF that cannot be
delivered is the other way round: the heater that was supposed to stop and did
not is the case that matters most. An auto-off is RETRIED until the panel
reports the device off, and the first failure raises a serious alert.

THE TIME ZONE IS THE POOL'S

Times are wall-clock times in config.DEPLOY_TZ, whatever the host's zone is,
because "seven" means seven at the pool. Daylight saving has two edges and
both are decided here rather than left to whatever arithmetic falls out:

  * spring forward: 02:30 does not exist that night. It runs ONCE, at the
    instant 02:30 would have been on the old offset -- 03:30 on the new one --
    rather than being skipped (PEP 495, fold=0, which is zoneinfo's own rule).
  * fall back: 01:30 happens twice. It runs ONCE, at the first of them.

Both are selftest cases ticking minute by minute through the real 2026
transitions, because "it looks right" is not a test of a calendar.

THE SAFETY NET APPLIES TO EVERY PRESS, NOT ONLY TO OURS

"Off after N hours" only covers somebody who remembered to choose it. The
household-wide max-on (`[schedule.max_on]`, the spa heater at 3 hours unless
configured) is driven from what the PANEL REPORTS, not from what poolhound
sent -- so it covers a press at the wall panel, through AqualinkD's own page,
or from poolhound local, none of which this server ever sees. It reads the
newest samples, finds how long the device has been reported running without a
break, and past the limit queues that device's ordinary off command. Once per
run: a person who turns it straight back on has made a decision, and a net
that fights them every tick would be a different, worse product.

WHO RUNS IT, AND HOW YOU KNOW IT IS RUNNING

A daemon thread in the serving process (start(), called from server.main()).
Cron would be a fresh interpreter every minute on a box whose CIFS share
already makes start-up slow, and a minute's granularity for "spa at 19:00" is
fine but a minute's granularity for an off that failed is a minute of gas. The
thread ticks every TICK_S and can never die of an exception: each tick is
caught, counted and kept as `last_error`.

But nothing inside a process can report that the process is gone. So the
thread's liveness is said three ways: /api/health carries health() from the
process that owns the thread; the Control tab draws that live; and the tick
stamps `alive_at` into the file every few minutes, which bin/watch -- a
different process, on cron -- reads, and alerts on when it goes stale.
"""
import csv, datetime as dt, json, os, sys, threading, time, traceback, uuid
from zoneinfo import ZoneInfo

from . import access, commands as C, config, locking, syncdefs as SD

FILE = "schedules.json"

# How often the thread looks. Twenty seconds is well inside the agent's
# five-minute expiry and far below anything a person would call late.
TICK_S = 20
# How late an ON may still run. Fifteen minutes covers a container restart
# or a deploy; it does not cover "the server was down all evening", which is
# the case that must NOT light a heater.
GRACE_S = 15 * 60
# An off that did not take is tried again, but not faster than this or than the
# device's own cooldown -- a retry the Guard is certain to refuse is an audit
# row that says nothing.
OFF_RETRY_S = 90
# After a send, how long the panel has to report the device off before the
# send counts as not having worked. commands.PENDING_GIVES_UP_S is the page's
# own patience for the same question, plus a sample's worth of slack.
OFF_CONFIRM_S = C.PENDING_GIVES_UP_S + 60
# And when to stop. Six attempts over ~half an hour is enough to say, loudly,
# that something other than a busy link is wrong; after that a person has it.
OFF_MAX_ATTEMPTS = 6
# How long the page and the watchdog wait before calling the thread dead.
ALIVE_EVERY_S = 300
ALIVE_STALE_S = 900
# A sample older than this says nothing about what is running NOW, so the
# safety net does not act on it -- the agent is probably down, and a command
# could not reach the panel anyway.
SAMPLE_FRESH_S = 40 * 60

HISTORY_KEEP = 200
SCHEDULES_MAX = 50

# Offered on the page; anything else is refused.
OFF_AFTER_CHOICES = C.OFF_AFTER_CHOICES
OFF_AFTER_MAX_H = max(OFF_AFTER_CHOICES)

REPEATS = {
    "once":     None,
    "daily":    (0, 1, 2, 3, 4, 5, 6),
    "weekdays": (0, 1, 2, 3, 4),
    "weekends": (5, 6),
}
REPEAT_WORDS = {"once": "once", "daily": "every day",
                "weekdays": "weekdays", "weekends": "weekends"}

# Which devices may carry "off after", and the hours the page pre-selects.
# Configurable under [schedule.auto_off] in config.toml; 0 removes a device.
# The heaters and the spa are on by default because they are the ones that cost
# money when forgotten. A light is cheap to leave on and is the owner's call.
DEFAULT_AUTO_OFF_H = {"Spa": 2, "Spa_Heater": 2, "Pool_Heater": 6}
# The household-wide net, [schedule.max_on]. The spa heater by default: a spa
# reaches temperature in under an hour, a long soak is two, and three hours of
# gas with nobody in it is the bill this exists to stop. 0 turns it off --
# which is a decision somebody makes in a file, not a button.
DEFAULT_MAX_ON_H = {"Spa_Heater": 3}

SEVERE = "serious"

# The settings this process's describing and scene lookups use. The server
# has its config.toml; the Pi has what the server last sent it, or nothing,
# and must never call config.load() (which exits when there is no file).
_CFG = {"cfg": None}

# --------------------------------------------------------------- the clock
_TZ = None


def tz():
    global _TZ
    if _TZ is None:
        _TZ = ZoneInfo(config.DEPLOY_TZ)
    return _TZ


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def _iso(t):
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def _parse(s):
    """An aware datetime from one of OUR stamps, or None."""
    try:
        t = dt.datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _parse_sample_ts(s):
    """The agent's `2026-10-08T07:28:00-0700`, aware, or None."""
    try:
        return dt.datetime.strptime(str(s).strip(), "%Y-%m-%dT%H:%M:%S%z")
    except (TypeError, ValueError):
        return None


def _hhmm(s):
    """(h, m) from "HH:MM", or None."""
    try:
        h, m = str(s).strip().split(":")
        h, m = int(h), int(m)
    except (TypeError, ValueError):
        return None
    return (h, m) if 0 <= h < 24 and 0 <= m < 60 else None


def occurrence(sched, day, zone=None):
    """When a recurring schedule falls on local date `day`, as a UTC instant.

    fold=0 is the whole of the DST policy, and it is zoneinfo's: a wall time
    that does not exist (spring forward) resolves on the OLD offset, so it lands
    the length of the gap later on the new one; a wall time that happens twice
    (fall back) resolves to the FIRST. Each local date has exactly one instant,
    and the date is what a run is keyed by -- so neither edge can double-fire
    and neither can skip.
    """
    hm = _hhmm(sched.get("time"))
    if not hm:
        return None
    local = dt.datetime(day.year, day.month, day.day, hm[0], hm[1],
                        tzinfo=zone or tz(), fold=0)
    return local.astimezone(dt.timezone.utc)


def _days(sched):
    return REPEATS.get(sched.get("repeat")) or ()


def latest_due(sched, now, zone=None):
    """(instant, key) of the most recent occurrence at or before `now` that
    has not been handled, or None.

    `key` is what marks it handled: the local date for a recurring schedule,
    "once" for a one-off. Only occurrences after the schedule was created
    count, so making a daily 07:00 at 09:00 does not report this morning's as
    missed.
    """
    zone = zone or tz()
    if sched.get("repeat") == "once":
        at = _parse(sched.get("at"))
        if at and at <= now and sched.get("last_key") != "once":
            return at, "once"
        return None
    created = _parse(sched.get("created")) or now
    today = now.astimezone(zone).date()
    for back in range(0, 8):
        day = today - dt.timedelta(days=back)
        if day.weekday() not in _days(sched):
            continue
        occ = occurrence(sched, day, zone)
        if occ is None or occ > now:
            continue
        if occ <= created or sched.get("last_key") == day.isoformat():
            return None
        return occ, day.isoformat()
    return None


def next_due(sched, now, zone=None):
    """The next instant this schedule will run, or None."""
    zone = zone or tz()
    if sched.get("repeat") == "once":
        at = _parse(sched.get("at"))
        return at if at and sched.get("last_key") != "once" else None
    today = now.astimezone(zone).date()
    for ahead in range(0, 9):
        day = today + dt.timedelta(days=ahead)
        if day.weekday() not in _days(sched):
            continue
        occ = occurrence(sched, day, zone)
        if occ and occ > now and sched.get("last_key") != day.isoformat():
            return occ
    return None


# ------------------------------------------------------------ configuration
def auto_off_hours(cfg=None):
    """{device: default hours} for the devices that may carry "off after"."""
    raw = ((cfg or {}).get("schedule") or {}).get("auto_off")
    out = dict(DEFAULT_AUTO_OFF_H)
    if isinstance(raw, dict):
        for dev, h in raw.items():
            try:
                h = float(h)
            except (TypeError, ValueError):
                continue
            if C.off_command(dev) is None:
                continue                    # nothing to switch off: ignored
            if h <= 0:
                out.pop(dev, None)
            else:
                out[dev] = min(h, OFF_AFTER_MAX_H)
    return out


def max_on_hours(cfg=None):
    """{device: hours} for the household-wide net. Only devices the panel
    reports a state for can be on it -- a net needs something to watch."""
    raw = ((cfg or {}).get("schedule") or {}).get("max_on")
    out = dict(DEFAULT_MAX_ON_H)
    if isinstance(raw, dict):
        for dev, h in raw.items():
            try:
                h = float(h)
            except (TypeError, ValueError):
                continue
            if C.off_command(dev) is None or not C.state_column(dev):
                continue
            if h <= 0:
                out.pop(dev, None)
            else:
                out[dev] = h
    return out


def on_direction(cmd):
    """Is this a command a later "off" would undo?"""
    return (cmd.get("action") in ("set", "setpoint", "heat")
            and not C.is_off(cmd) and C.off_command(cmd.get("device")) is not None)


def check_off_after(data, cfg=None):
    """(hours or None, error or None) for an `off_after_h` riding on a command.

    Checked BEFORE the command is issued, so a bad value refuses the whole
    press rather than switching something on with no off to follow.
    """
    raw = data.get("off_after_h")
    if raw in (None, "", 0, "0"):
        return None, None
    try:
        h = float(raw)
    except (TypeError, ValueError):
        return None, "off_after_h must be a number of hours"
    if h not in OFF_AFTER_CHOICES:
        return None, ("off after must be one of "
                      + ", ".join(f"{c:g}" for c in OFF_AFTER_CHOICES) + " hours")
    if not on_direction(data):
        return None, "only something being switched ON can be switched off afterwards"
    if data.get("device") not in auto_off_hours(cfg):
        return None, (f"{C.label_for(data.get('device'))} does not offer an automatic "
                      f"off — [schedule.auto_off] in config.toml lists the ones that do")
    return h, None


# ------------------------------------------------------------- the file
def path(data=None):
    return os.path.join(data or config.data_dir(), FILE)


def _empty():
    # `deleted`: markers for schedules cancelled, withdrawn or run out, so a
    # sync cannot bring one back (syncdefs). `pi_at`: on the server, when the
    # Pi last sent its runner's state; once set, the Pi runs the schedules.
    return {"version": 1, "schedules": [], "autooffs": [], "net": {},
            "history": [], "alive_at": None, "deleted": [], "pi_at": None}


def _load(data, now):
    """The state, with every stamp from the future dealt with as the module
    docstring says. NEVER raises on a bad file: a corrupt schedule must not
    take the server's other duties down, and it is reported, loudly."""
    try:
        with open(path(data), encoding="utf-8") as f:
            st = json.load(f)
        if not isinstance(st, dict):
            raise ValueError("not an object")
    except FileNotFoundError:
        return _empty()
    except Exception as e:                                   # noqa: BLE001
        print(f"schedule: {path(data)} is unreadable ({type(e).__name__}: {e}); "
              f"starting from nothing. The file is left in place for a person "
              f"to look at.", file=sys.stderr, flush=True)
        st = _empty()
        st["_unreadable"] = True
        return st
    base = _empty()
    base.update({k: st.get(k, base[k]) for k in base})
    slack = now + dt.timedelta(seconds=60)
    # Record-keeping stamps from the future: dropped.
    a = _parse(base.get("alive_at"))
    if a is None or a > slack:
        base["alive_at"] = None
    for s in base["schedules"]:
        c = _parse(s.get("created"))
        if c is None or c > slack:
            s["created"] = _iso(now)
    base["history"] = [h for h in base["history"]
                       if isinstance(h, dict) and (_parse(h.get("at")) or slack) <= slack]
    # Safety stamps from the future: sooner, never never.
    horizon = now + dt.timedelta(hours=OFF_AFTER_MAX_H, seconds=60)
    for o in base["autooffs"]:
        d = _parse(o.get("due"))
        if d is None or d > horizon:
            o["due"] = _iso(now)
            o["clock_note"] = "its due time was in the future beyond any auto-off; run now"
    return base


def _load_w(data, now):
    """_load, for a caller about to WRITE. An unreadable file is refused
    rather than replaced: saving over it would turn "a person must look at
    this" into "every schedule silently gone". ValueError, which the write
    API answers as a 409 carrying this sentence."""
    st = _load(data, now)
    if st.get("_unreadable"):
        raise ValueError(f"{path(data)} could not be read, so nothing is being "
                         f"added to it or taken from it until a person repairs "
                         f"or removes it")
    return st


def _save(data, st):
    st.pop("_unreadable", None)
    locking.replace_atomically(path(data), lambda f: json.dump(st, f, indent=1))


def _remove(st, row, now, kind="schedule"):
    """Take a schedule (or an automatic off) out, and leave the marker that
    tells the other machine it went."""
    key = "schedules" if kind == "schedule" else "autooffs"
    if row in st[key]:
        st[key].remove(row)
    st["deleted"] = SD.prune(st.get("deleted", []) + [SD.tombstone(kind, row.get("id"), now)], now)


def _locked(data):
    return locking.exclusive(data or config.data_dir(), "schedules")


def _history(st, now, kind, what, outcome, detail="", by="", severity="",
             device="", ref=""):
    """One line of what happened. `severity` set means somebody must be told."""
    st["history"].insert(0, {
        "id": uuid.uuid4().hex[:12], "at": _iso(now), "kind": kind,
        "what": what, "outcome": outcome, "detail": str(detail)[:300],
        "by": str(by or "")[:120], "device": device, "ref": ref,
        "severity": severity, "alerted": False if severity else None})
    del st["history"][HISTORY_KEEP:]


# ------------------------------------------------------------ the API side
def describe_when(s):
    rep = s.get("repeat")
    if rep == "once":
        at = _parse(s.get("at"))
        return at.astimezone(tz()).strftime("%a %-d %b, %H:%M") if at else "?"
    return f"{REPEAT_WORDS.get(rep, rep)} at {s.get('time')}"


def _what(cmd):
    # A scheduled SCENE is named by its name, looked up now: renamed since it
    # was scheduled, the list says what it is called today.
    if cmd.get("action") == "scene":
        from . import scenes as SC
        sc = SC.get(_CFG.get("cfg"), str(cmd.get("device") or ""))
        return f"scene {sc['name']}" if sc else "a scene that has since been deleted"
    return C.describe(cmd)


def create(body, who, cfg=None, data=None, now=None):
    """Add one schedule. (row, None) or (None, reason).

    The command is validated by commands.validate() NOW, with a fresh stamp,
    so a schedule for a device or value the agent would refuse is refused while
    the person is looking at the form rather than at 03:00 to nobody.
    """
    now = now or utcnow()
    cmd = {"action": body.get("action"), "device": body.get("device"),
           "value": body.get("value")}
    if cmd["action"] == "scene":
        # A scene: its steps were validated when it was saved, and are again,
        # one by one, when it fires. It carries its own "then off after".
        from . import scenes as SC
        sc = SC.get(cfg if cfg is not None else config.load(), str(cmd["device"] or ""))
        if sc is None:
            return None, "no such scene — it may have been deleted"
        cmd["value"] = None
        if body.get("off_after_h") not in (None, "", 0, "0"):
            return None, ("a scene switches itself off by its own \u201cthen off after\u201d; "
                          "set it on the scene")
    elif cmd["action"] not in ("set", "setpoint", "heat"):
        return None, "a schedule switches one circuit, sets one setpoint, or runs a scene"
    else:
        try:
            v = C.validate({"id": "0" * 16, "issued": _iso(now), **cmd}, now=now)
        except C.Refused as e:
            return None, str(e)
        cmd["value"] = v["value"]
    rep = str(body.get("repeat") or "once")
    if rep not in REPEATS:
        return None, "repeat must be one of " + ", ".join(REPEATS)
    row = {"id": uuid.uuid4().hex[:12], **cmd, "repeat": rep,
           "by": str(who or "")[:120], "created": _iso(now), "last_key": None,
           "off_after_h": None, "updated_at": SD.stamp(now)}
    if rep == "once":
        raw = str(body.get("at") or "").strip()
        try:
            local = dt.datetime.strptime(raw[:16], "%Y-%m-%dT%H:%M")
        except ValueError:
            return None, "a one-off schedule needs a date and a time"
        at = local.replace(tzinfo=tz(), fold=0).astimezone(dt.timezone.utc)
        if at <= now:
            return None, "that time has already passed"
        if at > now + dt.timedelta(days=366):
            return None, "a schedule more than a year out is a note, not a schedule"
        row["at"] = _iso(at)
    else:
        hm = _hhmm(body.get("time"))
        if not hm:
            return None, "a repeating schedule needs a time of day, HH:MM"
        row["time"] = f"{hm[0]:02d}:{hm[1]:02d}"
    if body.get("off_after_h") not in (None, "", 0, "0"):
        h, err = check_off_after({**cmd, "off_after_h": body.get("off_after_h")}, cfg)
        if err:
            return None, err
        row["off_after_h"] = h
    with _locked(data):
        st = _load_w(data, now)
        if len(st["schedules"]) >= SCHEDULES_MAX:
            return None, f"{SCHEDULES_MAX} schedules is the limit; cancel one first"
        st["schedules"].append(row)
        _history(st, now, "created", _what(cmd), "scheduled",
                 describe_when(row) + (f", off after {row['off_after_h']:g} h"
                                       if row["off_after_h"] else ""),
                 by=who, device=cmd["device"], ref=row["id"])
        _save(data, st)
    return row, None


def clean_synced(row, cfg=None):
    """A schedule that arrived from the other machine, rebuilt field by field,
    or None. Held to what create() accepts, except that a one-off may already
    be in the past (it will be reported missed, not run) and a scene it names
    may arrive in the same exchange."""
    import re
    if not isinstance(row, dict):
        return None
    rid = str(row.get("id") or "")
    if not re.fullmatch(r"[0-9a-zA-Z]{6,40}", rid):
        return None
    cmd = {"action": row.get("action"), "device": row.get("device"), "value": row.get("value")}
    if cmd["action"] == "scene":
        if not re.fullmatch(r"[0-9a-zA-Z]{4,16}", str(cmd["device"] or "")):
            return None
        cmd["value"] = None
        off = None
    elif cmd["action"] in ("set", "setpoint", "heat"):
        try:
            v = C.validate({"id": "0" * 16, "issued": _iso(utcnow()), **cmd})
        except C.Refused:
            return None
        cmd["value"] = v["value"]
        off = row.get("off_after_h")
        if off in (None, "", 0, "0"):
            off = None
        else:
            try:
                off = float(off)
            except (TypeError, ValueError):
                return None
            if off not in OFF_AFTER_CHOICES:
                return None
    else:
        return None
    rep = row.get("repeat")
    if rep not in REPEATS:
        return None
    out = {"id": rid, **cmd, "repeat": rep, "off_after_h": off,
           "by": str(row.get("by") or "")[:120]}
    for k in ("created", "updated_at"):
        t = _parse(row.get(k))
        if t:
            out[k] = SD.stamp(t) if k == "updated_at" else _iso(t)
    if rep == "once":
        at = _parse(row.get("at"))
        if not at:
            return None
        out["at"] = _iso(at)
    else:
        hm = _hhmm(row.get("time"))
        if not hm:
            return None
        out["time"] = f"{hm[0]:02d}:{hm[1]:02d}"
    lk = row.get("last_key")
    out["last_key"] = lk if lk == "once" or (isinstance(lk, str) and re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", lk)) else None
    return out


def adopt_autooffs(offs, data=None, now=None):
    """The Pi's half of the hand-over: the automatic offs the server was
    holding, run from here now. Added by id; never duplicated."""
    now = now or utcnow()
    offs = [o for o in offs or () if isinstance(o, dict) and o.get("id") and
            isinstance(o.get("cmd"), dict)]
    if not offs:
        return False
    with _locked(data):
        st = _load_w(data, now)
        have = {o.get("id") for o in st["autooffs"]}
        new = [o for o in offs if o["id"] not in have]
        if not new:
            return False
        st["autooffs"] += new
        _save(data, st)
    return True


def cancel(body, who, cfg=None, data=None, now=None):
    """Cancel a schedule or a pending auto-off. (row, None) or (None, reason).

    A max-on auto-off cannot be cancelled here. It is the household's net, set
    in config.toml, and a cancel button beside it would make the net exactly as
    strong as the most impatient person holding `operate`.
    """
    now = now or utcnow()
    rid = str(body.get("id") or "")[:40]
    with _locked(data):
        st = _load_w(data, now)
        for key in ("schedules", "autooffs"):
            for row in st[key]:
                if row.get("id") != rid:
                    continue
                if key == "autooffs" and row.get("cause") == "max-on":
                    return None, ("this is the household safety net from "
                                  "[schedule.max_on] in config.toml, and it is "
                                  "not cancelled from the page")
                _remove(st, row, now, "schedule" if key == "schedules" else "autooff")
                what = _what(row.get("cmd") or row)
                _history(st, now, "cancelled", what, "cancelled",
                         describe_when(row) if key == "schedules"
                         else "the automatic off", by=who,
                         device=row.get("device", ""), ref=rid)
                _save(data, st)
                return {"id": rid, "kind": key[:-1]}, None
    return None, "no such schedule — it may already have run or been cancelled"


def note_command(data_in, who, off_after_h=None, cfg=None, data=None, now=None):
    """Called by server.command() after a command was issued successfully.

    Keeps the auto-offs honest about what a person has since done:

      * an OFF makes every pending off-after for that circuit moot -- it is off
        -- and leaving them would switch off whatever is turned on next;
      * an ON replaces any off-after for that device with the one chosen now,
        including "never". Otherwise "on, off after 2 h" followed an hour later
        by "on, off after 3 h" would still switch off at the first deadline.

    Max-on entries are left alone: the net is not something a press dismisses.
    And an auto-off's own command does not cancel its siblings here -- each
    finds the device off on its own turn and stands down without sending.
    """
    now = now or utcnow()
    cmd = {k: data_in.get(k) for k in ("action", "device", "value")}
    if who == access.AUTO_OFF or not cmd.get("device"):
        return
    off = C.is_off(cmd)
    on = on_direction(cmd)
    if not (off or on):
        return
    with _locked(data):
        st = _load_w(data, now)
        keep = []
        for o in st["autooffs"]:
            target = (o.get("cmd") or {}).get("device")
            moot = (o.get("cause") != "max-on"
                    and ((off and target == cmd["device"])
                         or (on and o.get("device") == cmd["device"])))
            if moot:
                _history(st, now, "superseded", _what(o.get("cmd") or {}),
                         "no longer needed",
                         f"{_what(cmd)} by {who}", by=who,
                         device=o.get("device", ""), ref=o.get("id", ""))
            else:
                keep.append(o)
        st["autooffs"] = keep
        if on and off_after_h:
            offc = C.off_command(cmd["device"])
            due = now + dt.timedelta(hours=float(off_after_h))
            st["autooffs"].append({
                "id": uuid.uuid4().hex[:12], "device": cmd["device"], "cmd": offc,
                "due": _iso(due), "cause": "off-after", "by": str(who)[:120],
                "created": _iso(now), "attempts": 0, "sent_at": None,
                "last_try": None, "alerted": False})
            _history(st, now, "auto-off set", _what(offc), "scheduled",
                     f"after {float(off_after_h):g} h, at "
                     f"{due.astimezone(tz()).strftime('%H:%M')}",
                     by=who, device=cmd["device"])
        _save(data, st)


def listing(cfg=None, data=None, now=None):
    """What /api/schedules returns. Pure reading; never writes."""
    now = now or utcnow()
    st = _load(data, now)
    sch = []
    for s in st["schedules"]:
        nd = next_due(s, now)
        offc = C.off_command(s.get("device"))
        sch.append({**s, "what": _what(s), "when": describe_when(s),
                    # What "off after" will actually send, in words: for the
                    # spa heater it is the SPA that goes off, and the list
                    # should say so rather than leave it to be discovered.
                    "then": _what(offc) if offc and s.get("off_after_h") else None,
                    "next": _iso(nd) if nd else None,
                    "label": C.label_for(s.get("device"), cfg)})
    sch.sort(key=lambda s: s["next"] or "9999")
    offs = []
    for o in st["autooffs"]:
        offs.append({**o, "what": _what(o.get("cmd") or {}),
                     "label": C.label_for(o.get("device"), cfg),
                     "cancellable": o.get("cause") != "max-on"})
    offs.sort(key=lambda o: o.get("due") or "")
    return {"schedules": sch, "autooffs": offs, "history": st["history"][:60],
            "history_total": len(st["history"]),
            "max_on": max_on_hours(cfg), "auto_off": auto_off_hours(cfg),
            "off_after_choices": list(OFF_AFTER_CHOICES), "tz": config.DEPLOY_TZ,
            "grace_s": GRACE_S, "unreadable": bool(st.get("_unreadable")),
            "scheduler": health(now), "runner": runner_view(cfg, st, now)}


def runner_view(cfg, st, now):
    """Who runs these, and how that runner is: for the page's liveness line.
    On the server once the Pi has taken over, from the Pi's last schedules
    report; stale when that report is, or when the Pi said its last look was
    long before it reported."""
    if not st.get("pi_at"):
        return {"on_pi": False}
    try:
        from . import programs
        rec = programs.load(cfg) or {}
    except Exception:                                        # noqa: BLE001
        rec = {}
    r = rec.get("runner") or None
    got = rec.get("received_at")
    rep_age = (now.timestamp() - got) if isinstance(got, (int, float)) else None
    # A runner that had just started when the report went out has not looked
    # yet; its start stands in, so a restart does not read as a dead runner
    # for the half hour until the next report.
    last = _parse((r or {}).get("last_tick")) or _parse((r or {}).get("started"))
    read = rec.get("read_at")
    lag = (read - last.timestamp()) if (last and isinstance(read, (int, float))) else None
    stale = (r is None or rep_age is None or rep_age > 3 * 1800
             or lag is None or lag > TICK_S * 6)
    return {"on_pi": True, "since": st.get("pi_at"), "pi": r,
            "report_age_s": int(rep_age) if rep_age is not None else None, "stale": stale}


# ------------------------------------------------------- reading the panel
def recent_samples(data=None, tail=256 * 1024):
    """The last few days of samples, oldest first, read from the file's TAIL.

    Not render.rows(): that reads the whole history, and this runs every
    twenty seconds against a CIFS share. A run of "on" longer than the tail
    holds is reported from the oldest sample the tail has -- which understates
    how long it has been on, the safe direction for a net that switches off.
    """
    p = os.path.join(data or config.data_dir(), "samples.csv")
    try:
        with open(p, "rb") as f:
            header = f.readline().decode("utf-8", "replace").strip()
            size = os.fstat(f.fileno()).st_size
            start = max(f.tell(), size - tail)
            f.seek(start)
            chunk = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    if start > len(header) + 1 and "\n" in chunk:
        chunk = chunk.split("\n", 1)[1]
    rows = list(csv.DictReader([header] + chunk.splitlines()))
    out = [(t, r) for r in rows if (t := _parse_sample_ts(r.get("ts")))]
    out.sort(key=lambda p: p[0])
    return out


def panel_agrees(cmd, samples, after=None):
    """True/False when the newest sample (newer than `after`) says whether the
    panel already does what `cmd` asks; None when it cannot say."""
    if not samples:
        return None
    t, last = samples[-1]
    if after is not None and t <= after:
        return None
    dev = cmd.get("device")
    # A heater's own switch is read exactly as a switch is: its state column.
    # Without `heat` here a heater's automatic off could never be confirmed --
    # re-sent every few minutes for ever, and still standing, ready to switch
    # the heater off again, after somebody had chosen to turn it back on.
    if cmd.get("action") in ("set", "heat"):
        col = C.state_column(dev)
        got = str(last.get(col, "") if col else "").strip()
        if got == "":
            return None
        return got == ("1" if cmd.get("value") in (1, "1", True) else "0")
    if cmd.get("action") == "setpoint":
        col = (C.SETPOINTS.get(dev) or {}).get("value_column")
        try:
            return abs(float(last.get(col, "")) - float(cmd.get("value"))) < 0.51
        except (TypeError, ValueError):
            return None
    return None


def on_since(device, samples, now):
    """When the panel started reporting `device` on without a break, or None.

    The first sample of the current unbroken run of "1" in its state column.
    None when the newest sample does not say it is on, or is too old to say
    anything about now.
    """
    col = C.state_column(device)
    if not col or not samples:
        return None
    t_last, last = samples[-1]
    if (now - t_last).total_seconds() > SAMPLE_FRESH_S:
        return None
    if str(last.get(col, "")).strip() != "1":
        return None
    since = t_last
    for t, r in reversed(samples):
        v = str(r.get(col, "")).strip()
        if v == "1":
            since = t
        elif v == "0":
            break
        # blank: says nothing either way; the run is not broken by silence
    return since


# ------------------------------------------------------------- the tick
def _issue_default(cmd, by, cfg, off_after_h=None):
    from . import server
    return server.command({**cmd, "off_after_h": off_after_h}, cfg, by)


def _sent(result):
    """Did the issue path hand the command to the agent without a refusal?"""
    res = (result or {}).get("result")
    return result is not None and (res is None or bool(res.get("ok")))


def _refusal(result, err):
    if err:
        return err
    res = (result or {}).get("result") or {}
    return res.get("refused") or res.get("error") or "the agent did not accept it"


def _fp(st):
    """What a tick may have changed, without alive_at: whether to write."""
    return SD.fingerprint({k: v for k, v in st.items() if k != "alive_at"})


def tick(cfg=None, now=None, data=None, issue=None, notify=None, samples=None,
         authority=None, keep_alive=True):
    """One look at the clock. Returns what it did, for the selftest and the log.

    TWO LOCKED PHASES WITH THE SENDING BETWEEN THEM, not one lock around the
    lot. Issuing waits up to three seconds for the agent per command, and
    server.command() takes this same lock itself (note_command) -- holding it
    across the send would deadlock on the first scheduled ON that carries an
    off-after. So: CLAIM under the lock (an ON's occurrence is marked handled
    BEFORE it is sent, so a crash between the two can lose an ON but never
    repeat one -- the right way round for a heater), SEND unlocked, SETTLE
    under the lock.
    """
    cfg = cfg if cfg is not None else config.load()
    _CFG["cfg"] = cfg
    now = now or utcnow()
    issue = issue or _issue_default
    # Who may still run a schedule. The server's [access]; the Pi has no
    # access list and passes its own (docs/LOCAL-AND-SYNC.md: the server
    # withdraws, and the withdrawal reaches the Pi as a deletion).
    authority = authority or (lambda who: access.may(who, "/api/schedule", cfg))
    if samples is None:
        samples = recent_samples(data)
    zone = tz()
    jobs, did = [], []

    # ---- 1. claim
    with _locked(data):
        st = _load(data, now)
        if st.get("_unreadable"):
            # Do not overwrite what a person needs to look at -- and do not
            # carry on as though nothing were scheduled. Raised, so the thread
            # counts it as a failed look and /api/health and the Control tab
            # carry the reason; alive_at() reports the file as long dead, so
            # bin/watch says nothing will run.
            raise RuntimeError(f"{path(data)} could not be read; nothing scheduled "
                               f"will run until a person repairs or removes it")
        before = _fp(st)
        for s in list(st["schedules"]):
            got = latest_due(s, now, zone)
            if not got:
                continue
            occ, key = got
            cmd = {k: s.get(k) for k in ("action", "device", "value")}
            # THE AUTHORITY IS RE-ASKED AT EVERY FIRING, not inherited from the
            # day the schedule was made. A recurring schedule is a standing
            # instruction, and the route that made it is gated at `operate`
            # only at creation -- so somebody dropped from [access] the next
            # morning would otherwise go on lighting the heater every evening,
            # sent as `schedule` with nothing in the audit naming them. The
            # schedule is withdrawn rather than skipped: skipped, it would
            # come back the day the policy changed again, and nobody could
            # say when it had been agreed to. Auto-offs are not asked: they
            # only ever stop something, and they are the system's, not a person's.
            ok, need, held = authority(s.get("by"))
            if not ok:
                _remove(st, s, now)
                _history(st, now, "withdrawn", _what(cmd), "withdrawn",
                         f"{describe_when(s)}, set by {s.get('by') or 'nobody named'}, "
                         f"who now holds {held} and it needs {need} — not sent, "
                         f"and removed",
                         by=access.SCHEDULER,
                         severity=SEVERE if C.is_off(cmd) else "info",
                         device=cmd["device"], ref=s["id"])
                did.append(("withdrawn", s["id"]))
                continue
            late = (now - occ).total_seconds()
            if s["repeat"] == "once":
                _remove(st, s, now)
            else:
                s["last_key"] = key
            if late > GRACE_S:
                # Past the window. Never run late -- see the docstring.
                sev = SEVERE if C.is_off(cmd) else "info"
                _history(st, now, "missed", _what(cmd), "missed",
                         f"due {occ.astimezone(zone).strftime('%a %-d %b %H:%M')}, "
                         f"and nothing was running to send it until "
                         f"{late / 60:.0f} minutes later — too late to run "
                         f"{'safely' if not C.is_off(cmd) else 'without overriding whoever has used the pool since'}",
                         by=access.SCHEDULER, severity=sev, device=cmd["device"],
                         ref=s["id"])
                did.append(("missed", s["id"]))
                continue
            jobs.append(("schedule", s, cmd, occ))

        # The net, from what the panel reports.
        nets = max_on_hours(cfg)
        for dev, hours in nets.items():
            since = on_since(dev, samples, now)
            if since is None:
                st["net"].pop(dev, None)
                continue
            if (now - since).total_seconds() < hours * 3600:
                continue
            if st["net"].get(dev) == _iso(since):
                continue                          # once per run
            st["net"][dev] = _iso(since)
            offc = C.off_command(dev)
            st["autooffs"].append({
                "id": uuid.uuid4().hex[:12], "device": dev, "cmd": offc,
                "due": _iso(now), "cause": "max-on", "by": access.AUTO_OFF,
                "created": _iso(now), "attempts": 0, "sent_at": None,
                "last_try": None, "alerted": False,
                "on_since": _iso(since)})
            _history(st, now, "max-on", _what(offc), "switching off",
                     f"{C.label_for(dev, cfg)} reported running since "
                     f"{since.astimezone(zone).strftime('%H:%M')}, past the "
                     f"{hours:g} h limit in [schedule.max_on]",
                     by=access.AUTO_OFF, device=dev)
            did.append(("max-on", dev))

        for o in st["autooffs"]:
            if (_parse(o.get("due")) or now) > now:
                continue
            last = _parse(o.get("last_try"))
            cool = max(OFF_RETRY_S, C.cooldown_for(o.get("cmd") or {}))
            if last and (now - last).total_seconds() < cool:
                continue
            sent_at = _parse(o.get("sent_at"))
            if sent_at and (now - sent_at).total_seconds() < OFF_CONFIRM_S:
                continue                          # give the panel its chance
            o["last_try"] = _iso(now)
            jobs.append(("autooff", dict(o), dict(o.get("cmd") or {}), None))

        # WRITTEN ONLY WHEN SOMETHING CHANGED -- on the Pi this file is on the
        # SD card, and a write every twenty seconds is 4,320 a day. alive_at is
        # the exception the server keeps (bin/watch reads it from the file);
        # the Pi says it is alive another way and passes keep_alive=False.
        alive = keep_alive and (not st["alive_at"] or
                                (now - _parse(st["alive_at"])).total_seconds() >= ALIVE_EVERY_S)
        if alive:
            st["alive_at"] = _iso(now)
        if alive or _fp(st) != before:
            _save(data, st)

    # ---- 2. send, outside the lock
    outcomes = []
    for kind, row, cmd, occ in jobs:
        if kind == "autooff":
            created = _parse(row.get("sent_at")) or _parse(row.get("created"))
            agrees = panel_agrees(cmd, samples, after=created)
            if agrees is True:
                outcomes.append((kind, row, cmd, "done", None,
                                 "the panel reports it off" if row.get("sent_at")
                                 else "already off — nothing sent", None))
                continue
            result, err = issue(cmd, access.AUTO_OFF, cfg)
            ok = _sent(result) and not err
            outcomes.append((kind, row, cmd, "sent" if ok else "failed", result,
                             None if ok else _refusal(result, err), None))
        else:
            result, err = issue(cmd, access.SCHEDULER, cfg,
                                off_after_h=row.get("off_after_h"))
            ok = _sent(result) and not err
            outcomes.append((kind, row, cmd, "sent" if ok else "failed", result,
                             None if ok else _refusal(result, err), occ))

    # ---- 3. settle
    alerts = _fresh_alerts(st, now)
    if outcomes:
        with _locked(data):
            st = _load_w(data, now)
            for kind, row, cmd, state, result, why, occ in outcomes:
                what = _what(cmd)
                if kind == "schedule":
                    if state == "sent":
                        res = (result or {}).get("result")
                        late = (now - occ).total_seconds()
                        _history(st, now, "fired", what,
                                 "done" if res else "sent, no answer yet",
                                 describe_when(row)
                                 + (f", {late / 60:.0f} min late" if late >= 90 else ""),
                                 by=access.SCHEDULER,
                                 device=cmd["device"], ref=row["id"])
                        did.append(("fired", row["id"]))
                    elif C.is_off(cmd):
                        # An off that could not be delivered becomes a retried
                        # auto-off: the heater that should have stopped is the
                        # case that matters most.
                        st["autooffs"].append({
                            "id": uuid.uuid4().hex[:12], "device": cmd["device"],
                            "cmd": cmd, "due": _iso(now), "cause": "schedule",
                            "by": access.SCHEDULER, "created": _iso(now),
                            "attempts": 1, "sent_at": None, "last_try": _iso(now),
                            "alerted": True})
                        _history(st, now, "missed", what, "not delivered — retrying",
                                 why, by=access.SCHEDULER, severity=SEVERE,
                                 device=cmd["device"], ref=row["id"])
                        did.append(("missed", row["id"]))
                    else:
                        _history(st, now, "missed", what, "not delivered", why,
                                 by=access.SCHEDULER, severity="info",
                                 device=cmd["device"], ref=row["id"])
                        did.append(("missed", row["id"]))
                    continue

                live = next((o for o in st["autooffs"] if o.get("id") == row["id"]), None)
                if live is None:
                    continue                      # cancelled or superseded meanwhile
                if state == "done":
                    st["autooffs"].remove(live)
                    late = ""
                    if live.get("alerted"):
                        late = " — after it had been reported as not working"
                    _history(st, now, "auto-off", what, "done", why + late,
                             by=access.AUTO_OFF, device=live.get("device", ""),
                             severity="info" if live.get("alerted") else "",
                             ref=live["id"])
                    did.append(("auto-off done", live["id"]))
                    continue
                live["attempts"] = int(live.get("attempts") or 0) + 1
                if state == "sent":
                    live["sent_at"] = _iso(now)
                    if not C.state_column(cmd.get("device")) and cmd.get("action") == "set":
                        # Nothing will ever confirm it; delivered is all there is.
                        st["autooffs"].remove(live)
                        _history(st, now, "auto-off", what, "sent",
                                 "this circuit reports no state to confirm it against",
                                 by=access.AUTO_OFF, device=live.get("device", ""),
                                 ref=live["id"])
                    elif live["attempts"] > 1:
                        _history(st, now, "auto-off", what, "sent again",
                                 f"attempt {live['attempts']}; the panel had not "
                                 f"reported the last one", by=access.AUTO_OFF,
                                 device=live.get("device", ""), ref=live["id"])
                    else:
                        _history(st, now, "auto-off", what, "sent",
                                 "waiting for the panel to report it off",
                                 by=access.AUTO_OFF, device=live.get("device", ""),
                                 ref=live["id"])
                    did.append(("auto-off sent", live["id"]))
                    continue
                # failed
                if live["attempts"] >= OFF_MAX_ATTEMPTS:
                    st["autooffs"].remove(live)
                    _history(st, now, "auto-off", what, "gave up",
                             f"{live['attempts']} attempts; the last said: {why}. "
                             f"It is probably still on — check the panel.",
                             by=access.AUTO_OFF, severity=SEVERE,
                             device=live.get("device", ""), ref=live["id"])
                elif not live.get("alerted"):
                    live["alerted"] = True
                    _history(st, now, "auto-off", what, "NOT delivered — retrying",
                             f"{why}. Trying again every few minutes until the "
                             f"panel reports it off.", by=access.AUTO_OFF,
                             severity=SEVERE, device=live.get("device", ""),
                             ref=live["id"])
                did.append(("auto-off failed", live["id"]))
            alerts = _fresh_alerts(st, now)
            _save(data, st)

    # ---- 4. say so, outside the lock (SMTP can take thirty seconds)
    if alerts:
        sent = send_alerts(alerts, cfg, notify)
        if sent:
            mark_alerted(sent, data=data, now=now)
    return did


def _fresh_alerts(st, now):
    """The rows THIS look created that need somebody told.

    Only this look's: an older row nobody could be told about is bin/watch's
    to retry, on its own cadence. Retrying every one of them every twenty
    seconds here would be an SMTP connection three times a minute to a relay
    that is already refusing.
    """
    stamp = _iso(now)
    return [h for h in st["history"]
            if h.get("severity") and h.get("alerted") is False and h.get("at") == stamp]


# --------------------------------------------------------------- alerting
def alert_text(h):
    """(title, message, severity, urgent) for one history row."""
    serious = h.get("severity") == SEVERE
    what = h.get("what") or "a scheduled command"
    if h.get("kind") == "auto-off" and h.get("outcome") == "done":
        return (f"Switched off: {what}", h.get("detail") or "", "info", False)
    if h.get("kind") == "withdrawn":
        # Serious only when the withdrawn command was an off: then something
        # somebody expected to stop will not, and they must hear it.
        return (f"Schedule withdrawn: {what}", h.get("detail") or "",
                h.get("severity") or "info", serious)
    if serious:
        return (f"Not switched off: {what}",
                f"{h.get('outcome')}. {h.get('detail') or ''}".strip(),
                SEVERE, True)
    return (f"Scheduled command missed: {what}",
            f"{h.get('outcome')}. {h.get('detail') or ''}".strip(), "info", False)


def send_alerts(rows, cfg, notify=None):
    """Notify about each row; return the ids that were actually delivered.

    Only a delivered alert is marked -- the watchdog's own rule, for the
    watchdog's own reason: recording a send that did not happen silences the
    retry, and the heater stays on with nobody told.
    """
    if notify is None:
        from . import watch
        notify = watch.notify
        url = watch.site_url(cfg, "#control")
    else:
        url = None
    out = []
    for h in rows:
        title, msg, sev, urgent = alert_text(h)
        try:
            ok = notify(title=title, message=msg, url=url, urgent=urgent,
                        cfg=cfg, severity=sev)
        except Exception as e:                               # noqa: BLE001
            print(f"schedule: alert failed: {type(e).__name__}: {e}",
                  file=sys.stderr, flush=True)
            ok = False
        if ok:
            out.append(h["id"])
    return out


def mark_alerted(ids, data=None, now=None):
    now = now or utcnow()
    with _locked(data):
        st = _load_w(data, now)
        for h in st["history"]:
            if h.get("id") in ids:
                h["alerted"] = True
        _save(data, st)


def unalerted(data=None, now=None, older_than_s=600):
    """For bin/watch: rows that needed telling somebody and did not get it.

    Older than ten minutes, so the watchdog does not race the scheduler's own
    attempt; the watchdog is the backstop, not the first sender.
    """
    now = now or utcnow()
    st = _load(data, now)
    return [h for h in st["history"]
            if h.get("severity") and h.get("alerted") is False
            and (now - (_parse(h.get("at")) or now)).total_seconds() >= older_than_s]


def alive_at(data=None, now=None):
    """When the thread last said it was alive, from the FILE -- the answer a
    different process can read."""
    now = now or utcnow()
    if not os.path.exists(path(data)):
        return None
    st = _load(data, now)
    if st.get("_unreadable"):
        # The thread is ticking and achieving nothing: as good as stopped.
        return dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    return _parse(st.get("alive_at"))


def net_lookup(data=None, now=None):
    """f(device, since) -> what the household limit did about `device` after
    `since` (aware), or None. For anomaly.py, which must not send a second
    "the spa heater is still on" about a run this net has already switched off.

    One read of the file, then a pure function over it: the anomaly detector
    asks once per long run, and a run is read off the same history the net
    writes. {"at": when it acted, "state": "off" | "trying" | "gave up"} --
    "off" only once an auto-off row says the panel reported it off, because
    "the net queued an off" is not "it is off".
    """
    now = now or utcnow()
    st = _load(data, now)
    hist = [(h, _parse(h.get("at"))) for h in st.get("history") or ()
            if isinstance(h, dict)]
    hist = [(h, t) for h, t in hist if t]

    def f(device, since):
        acted = [t for h, t in hist
                 if h.get("kind") == "max-on" and h.get("device") == device
                 and t >= since]
        if not acted:
            return None
        first = min(acted)
        after = sorted(((t, h) for h, t in hist
                        if h.get("kind") == "auto-off"
                        and h.get("device") == device and t >= first),
                       key=lambda p: p[0])
        state = "trying"
        if after:
            out = after[-1][1].get("outcome")
            state = "off" if out == "done" else "gave up" if out == "gave up" else "trying"
        return {"at": first, "state": state}
    return f


# ------------------------------------------------------------- the sync
# docs/LOCAL-AND-SYNC.md. The schedules people wrote ("defs") are merged with
# syncdefs, last update wins; the runner's own state ("runtime": pending
# automatic offs, the max-on net's memory, the history) belongs to the Pi and
# is only shown by the server. History is the exception that both sides add
# to -- a schedule created on either page is a row -- so it is a union by id.

def export(data=None, now=None):
    """(defs, runtime) as this side holds them."""
    now = now or utcnow()
    st = _load(data, now)
    defs = {"schedules": [{k: r.get(k) for k in SD.SCHEDULE_FIELDS + ("last_key",) if k in r}
                          for r in st["schedules"]],
            "deleted": [d for d in SD.prune(st.get("deleted"), now)
                        if d["kind"] in ("schedule", "autooff")]}
    runtime = {"autooffs": st["autooffs"], "net": st["net"], "history": st["history"]}
    return defs, runtime


def runtime_fp(runtime):
    """The runner's state, without the server's own `alerted` marks, which the
    Pi never changes and must not make every sync look different."""
    hist = [{k: v for k, v in h.items() if k != "alerted"} for h in runtime.get("history") or ()]
    return SD.fingerprint({**runtime, "history": hist})


def _union_history(a, b):
    by = {}
    for h in list(a or ()) + list(b or ()):
        if not isinstance(h, dict) or not h.get("id"):
            continue
        old = by.get(h["id"])
        if old is None:
            by[h["id"]] = dict(h)
        elif h.get("alerted") is True:
            old["alerted"] = True          # told once is told
    rows = sorted(by.values(), key=lambda h: str(h.get("at") or ""), reverse=True)
    return rows[:HISTORY_KEEP]


def apply_defs(merged, data=None, now=None):
    """Install a merged list of schedules and deletion markers. True if this
    changed anything; the file is written only then.

    A schedule this side runs keeps its own last_key unless the merged one is
    later (syncdefs already took the later). An automatic off named by an
    "autooff" marker is dropped: the other page cancelled it.
    """
    now = now or utcnow()
    with _locked(data):
        st = _load_w(data, now)
        before = _fp(st)
        mine = {r["id"]: r for r in st["schedules"]}
        rows = []
        for r in merged.get("schedules") or ():
            row = dict(mine.get(r["id"]) or {})
            row.update({k: r.get(k) for k in SD.SCHEDULE_FIELDS if k in r})
            row["last_key"] = r.get("last_key")
            rows.append(row)
        st["schedules"] = rows[:SCHEDULES_MAX]
        gone = [d for d in merged.get("deleted") or () if d.get("kind") in ("schedule", "autooff")]
        dropped = {d["id"] for d in gone if d["kind"] == "autooff"}
        st["autooffs"] = [o for o in st["autooffs"]
                          if o.get("id") not in dropped or o.get("cause") == "max-on"]
        st["deleted"] = SD.prune(gone, now)
        if _fp(st) == before:
            return False
        _save(data, st)
    return True


def mirror_runtime(runtime, data=None, now=None):
    """The server's half: show what the Pi's runner holds. Its automatic offs
    and net replace ours; history is merged. Marks that the Pi now runs the
    schedules, after which this server never fires one (pi_runs())."""
    now = now or utcnow()
    with _locked(data):
        st = _load_w(data, now)
        before = _fp(st)
        st["autooffs"] = [o for o in (runtime.get("autooffs") or []) if isinstance(o, dict)][:100]
        st["net"] = runtime.get("net") if isinstance(runtime.get("net"), dict) else {}
        st["history"] = _union_history(st["history"], runtime.get("history"))
        first = not st.get("pi_at")
        st["pi_at"] = st.get("pi_at") or _iso(now)
        if _fp(st) != before or first:
            _save(data, st)
            return True
    return False


def pi_runs(data=None, now=None):
    """Has the Pi taken over running the schedules? From then on, never fire
    here: one runner, so nothing can run twice."""
    return bool(_load(data, now or utcnow()).get("pi_at"))


def handover(data=None, now=None):
    """The automatic offs this server was holding when the Pi took over, for
    the Pi to run; cleared here. Called once, at the first sync."""
    now = now or utcnow()
    with _locked(data):
        st = _load_w(data, now)
        if st.get("pi_at"):
            return []
        offs = list(st["autooffs"])
        st["autooffs"] = []
        _save(data, st)
        return offs


# ------------------------------------------------------------ the thread
_TRIED = set()          # history rows this process has already tried to alert on
_STATUS = {"started": None, "last_tick": None, "ticks": 0, "errors": 0,
           "last_error": None, "last_error_at": None}
_THREAD = None
_STOP = threading.Event()


def health(now=None):
    """What /api/health says about the scheduler. From THIS process, which is
    the one that owns the thread; a render process has none and says so."""
    now = now or utcnow()
    last = _parse(_STATUS["last_tick"])
    running = bool(_THREAD and _THREAD.is_alive())
    stale = (not running or last is None
             or (now - last).total_seconds() > TICK_S * 3 + 30)
    return {"running": running, "stale": stale, "tick_s": TICK_S,
            "started": _STATUS["started"], "last_tick": _STATUS["last_tick"],
            "ticks": _STATUS["ticks"], "errors": _STATUS["errors"],
            "last_error": _STATUS["last_error"],
            "last_error_at": _STATUS["last_error_at"]}


def server_duties(cfg=None, now=None, data=None, notify=None, nudge=None):
    """What the server's thread does once the Pi runs the schedules.

      * WITHDRAW a schedule whose author no longer holds the level to make
        one -- the check tick() made at every firing, which the Pi cannot make
        because it has no access list. Withdrawn here, it is a deletion, and
        reaches the Pi at the next sync (nudge). A schedule made at poolhound
        local (`door:`) is the house network's, and is kept.
      * ALERT on what the Pi recorded -- a missed ON, an OFF that would not
        take -- because the Pi cannot send mail.
      * Stamp alive_at, so bin/watch still knows this thread is running.
    """
    cfg = cfg if cfg is not None else config.load()
    _CFG["cfg"] = cfg
    now = now or utcnow()
    did, changed = [], False
    with _locked(data):
        st = _load_w(data, now)
        for s in list(st["schedules"]):
            if str(s.get("by") or "").startswith("door:"):
                continue
            ok, need, held = access.may(s.get("by"), "/api/schedule", cfg)
            if ok:
                continue
            cmd = {k: s.get(k) for k in ("action", "device", "value")}
            _remove(st, s, now)
            _history(st, now, "withdrawn", _what(cmd), "withdrawn",
                     f"{describe_when(s)}, set by {s.get('by') or 'nobody named'}, "
                     f"who now holds {held} and it needs {need} — removed, here and "
                     f"on the Pi", by=access.SCHEDULER,
                     severity=SEVERE if C.is_off(cmd) else "info",
                     device=cmd["device"], ref=s["id"])
            did.append(("withdrawn", s["id"]))
            changed = True
        if changed or not st["alive_at"] or \
                (now - _parse(st["alive_at"])).total_seconds() >= ALIVE_EVERY_S:
            st["alive_at"] = _iso(now)
            _save(data, st)
        # One attempt each from here; bin/watch retries what is still
        # unalerted ten minutes on, as it does for the scheduler's own rows.
        pending = [h for h in st["history"]
                   if h.get("severity") and h.get("alerted") is False
                   and h.get("id") not in _TRIED]
        _TRIED.update(h.get("id") for h in pending)
    if changed and nudge:
        nudge()
    if pending:
        sent = send_alerts(pending, cfg, notify)
        if sent:
            mark_alerted(sent, data=data, now=now)
    return did


def look(cfg=None, now=None, data=None, issue=None, notify=None, nudge=None):
    """One look by the server's thread. ONE RUNNER: until the Pi has taken
    over, this server fires the schedules as it always did; from the first
    sync on, it never fires one again -- it mirrors, withdraws and alerts."""
    if pi_runs(data, now):
        if nudge is None:
            from .server import QUEUE
            nudge = QUEUE.nudge
        return server_duties(cfg, now, data, notify, nudge)
    return tick(cfg, now, data, issue, notify)


def _loop():
    while not _STOP.is_set():
        try:
            did = look()
            for d in did:
                print(f"  schedule: {d[0]} {d[1]}", flush=True)
        # SystemExit too: config.load() exits the process when there is no
        # config, which is right for a collector run by hand and would end
        # this thread silently -- the one death /api/health can only report
        # as "not running", with nothing saying why.
        except (Exception, SystemExit) as e:                 # noqa: BLE001
            _STATUS["errors"] += 1
            _STATUS["last_error"] = f"{type(e).__name__}: {e}"[:300]
            _STATUS["last_error_at"] = _iso(utcnow())
            traceback.print_exc()
        finally:
            _STATUS["last_tick"] = _iso(utcnow())
            _STATUS["ticks"] += 1
        _STOP.wait(TICK_S)


def start():
    """Start the thread once per process. Called by server.main(), and only
    there: a render, a selftest or a cron job must never become a second
    scheduler firing the same 19:00 spa."""
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return _THREAD
    _STOP.clear()
    _STATUS["started"] = _iso(utcnow())
    _THREAD = threading.Thread(target=_loop, name="poolhound-schedule", daemon=True)
    _THREAD.start()
    return _THREAD
