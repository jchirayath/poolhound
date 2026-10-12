"""The pool's equipment schedules, both kinds, as the Pi last read them.

TWO KINDS, AND BOTH ARE KEPT

  the panel's own programs  stored IN the Jandy panel and run by it. They keep
                            the pump on its hours when the Pi, AqualinkD or
                            this server are down, which is why they exist.
                            Read through the PDA menus by AqualinkD with the
                            patch in deploy/aqualinkd -- stock AqualinkD has no
                            way to read them at all.
  AqualinkD's schedules     cron entries on the Pi that press a button through
                            AqualinkD's own API. More flexible, and gone with
                            the Pi.

Neither is the server's own scheduler (schedule.py), which presses buttons
through the agent and is a third thing again. This module only REPORTS the first
two: what each one says, read off the Pi. Changing them is not here.

WHERE IT RUNS

The agent imports this to shape what it read, so it is STDLIB ONLY like the
agent. The server imports it to validate what arrived, store it, and describe it
in words. One module, so the agent and the server cannot disagree about the
shape -- the reason commands.py is shared, applied here.

WHAT ARRIVES IS NOT TRUSTED

The body comes from the Pi over a bearer token, and every string in it was
typed by a person into a panel or a cron file. Everything is length-capped,
reduced to printable ASCII and rebuilt field by field; nothing is passed
through. The page puts it on screen with textContent.
"""
import json, os, re, time

FILE = "panel_schedules.json"
MAX_PROGRAMS = 48          # devices on the panel's PROGRAM list; this one has 11
MAX_SCHEDULES = 64         # AqualinkD writes one cron line per schedule
MAX_STR = 40
# A snapshot older than this is shown as stale. The agent pushes every
# PUSH_EVERY_S, so three missed pushes is a Pi that is not reporting.
PUSH_EVERY_S = 1800
STALE_S = 3 * PUSH_EVERY_S


def _s(v, n=MAX_STR):
    """A printable ASCII string of at most n characters, from anything."""
    if v is None:
        return ""
    return re.sub(r"[^\x20-\x7e]", "", str(v)).strip()[:n]


def _i(v, lo, hi):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


# ------------------------------------------------------------- the panel's
def programs_from_aqualinkd(raw):
    """AqualinkD's /api/programs answer -> (state, read_at, error, programs).

    `state` is AqualinkD's own word: never_read, reading, ok, failed,
    unsupported. A device with `of == 0` has no program entered; that is a
    reading, not a gap.
    """
    if not isinstance(raw, dict) or raw.get("type") != "programs":
        return "invalid", None, "not a programs answer", []
    state = _s(raw.get("state"), 16) or "invalid"
    read_at = _i(raw.get("read_at"), 0, 2**40) or None
    progs = []
    for p in (raw.get("programs") or [])[:MAX_PROGRAMS]:
        if not isinstance(p, dict) or not _s(p.get("device")):
            continue
        of = _i(p.get("of"), 0, 99)
        pgm = _i(p.get("pgm"), 0, 99)
        if of is None or pgm is None:
            continue
        progs.append({"device": _s(p.get("device")), "pgm": pgm, "of": of,
                      "on": _s(p.get("on"), 16) if of else "",
                      "off": _s(p.get("off"), 16) if of else "",
                      "days": _s(p.get("days"), 16) if of else ""})
    return state, read_at, _s(raw.get("error"), 80), progs


_SET_STATES = ("queued", "running", "ok", "failed")


def set_from_aqualinkd(raw):
    """The `set` object of /api/programs -- the last change AqualinkD made to a
    program and what the panel showed afterwards -- or None."""
    st = raw.get("set") if isinstance(raw, dict) else None
    if not isinstance(st, dict) or st.get("state") not in _SET_STATES:
        return None
    return {"state": st["state"], "device": _s(st.get("device"), 16),
            "want_on": _s(st.get("want_on"), 16), "want_off": _s(st.get("want_off"), 16),
            "now_on": _s(st.get("now_on"), 16), "now_off": _s(st.get("now_off"), 16),
            "requested_at": _i(st.get("requested_at"), 0, 2**40) or None,
            "done_at": _i(st.get("done_at"), 0, 2**40) or None,
            "error": _s(st.get("error"), 96)}


# ------------------------------------------------------------ AqualinkD's
_CRON = re.compile(r"^[0-9*,/-]{1,20}$")
_URL = re.compile(r"^/api/[A-Za-z0-9_]{1,32}/set$")


def schedules_from_aqualinkd(raw):
    """AqualinkD's /api/schedules answer -> (error, schedules).

    Each is a cron line it wrote: five time fields, the API path it calls and
    the value it sends. Anything not in that shape is dropped and counted.
    """
    err, out, _ = shape_schedules(raw)
    return err, out


def shape_schedules(raw):
    """schedules_from_aqualinkd, with how many entries were dropped: a list
    with entries this code cannot show is a list it must not rewrite, because
    a save replaces the whole file and would delete them."""
    if not isinstance(raw, dict):
        return "not a schedules answer", [], 0
    if raw.get("message") and not raw.get("schedules"):
        return _s(raw.get("message"), 80), [], 0     # "Error Schedules disabled"
    allof = raw.get("schedules") or []
    allof = allof if isinstance(allof, list) else []
    out, dropped = [], max(0, len(allof) - MAX_SCHEDULES)
    for e in allof[:MAX_SCHEDULES]:
        if not isinstance(e, dict):
            dropped += 1
            continue
        f = {k: _s(e.get(k), 20) for k in ("min", "hour", "daym", "month", "dayw")}
        url, value = _s(e.get("url"), 48), _s(e.get("value"), 8)
        if not all(_CRON.match(v) for v in f.values()) or not _URL.match(url) \
                or not re.fullmatch(r"-?[0-9.]{1,8}", value):
            dropped += 1
            continue
        out.append({**f, "enabled": str(e.get("enabled")) in ("1", "true", "True"),
                    "url": url, "value": value})
    return (f"{dropped} entry(s) not in the expected shape were left out"
            if dropped else ""), out, dropped


_DAYW = {"*": "every day", "1-5": "on weekdays", "0,6": "at weekends",
         "6,0": "at weekends"}
_DAYNAME = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def describe_cron(s):
    """One cron entry, in words: 'every day at 10:00'. Falls back to the raw
    fields rather than guessing at anything unusual."""
    m, h = s["min"], s["hour"]
    if m.isdigit() and h.isdigit() and int(h) < 24 and int(m) < 60:
        from .commands import clock_words
        when = f"at {clock_words(int(h) * 60 + int(m))}"     # as the panel writes it
    else:
        when = f"at minute {m} of hour {h}"
    d = s["dayw"]
    if d in _DAYW:
        days = _DAYW[d]
    elif re.fullmatch(r"[0-7](,[0-7])*", d):
        days = "on " + ", ".join(_DAYNAME[int(x)] for x in d.split(","))
    else:
        days = f"on weekdays {d}"
    if s["daym"] != "*" or s["month"] != "*":
        days += f", day {s['daym']} of month {s['month']}"
    return f"{days} {when}"


def device_of(s):
    """'/api/Filter_Pump/set' -> 'Filter_Pump'."""
    return s["url"].split("/")[2]


def action_of(s):
    v = s["value"]
    return "on" if v == "1" else "off" if v == "0" else f"set to {v}"


# ------------------------------------------------------------- the snapshot
def names_from_aqualinkd(raw):
    """AqualinkD device id -> the panel's own name for it, cleaned."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in list(raw.items())[:64]:
        if isinstance(k, str) and re.fullmatch(r"[A-Za-z0-9_]{1,32}", k) and _s(v, 16):
            out[k] = _s(v, 16)
    return out


def snapshot(programs_raw, programs_err, schedules_raw, schedules_err, now=None,
             names=None):
    """What the agent pushes: both kinds, each with its own error, so a Pi
    whose AqualinkD cannot read the panel still reports its cron schedules."""
    now = now if now is not None else time.time()
    body = {"version": 1, "read_at": int(now)}
    if programs_raw is not None:
        st, rd, err, progs = programs_from_aqualinkd(programs_raw)
        body["panel"] = {"state": st, "read_at": rd, "error": err, "programs": progs,
                         "set": set_from_aqualinkd(programs_raw)}
    else:
        body["panel"] = {"state": "unreachable", "read_at": None,
                         "error": _s(programs_err, 80), "programs": [], "set": None}
    if schedules_raw is not None:
        err, sch, dropped = shape_schedules(schedules_raw)
        body["aqualinkd"] = {"error": err, "schedules": sch, "dropped": dropped}
    else:
        body["aqualinkd"] = {"error": _s(schedules_err, 80), "schedules": [],
                             "dropped": 0}
    body["aqualinkd"]["names"] = names_from_aqualinkd(names)
    return body


def validate(body):
    """The server's half: rebuild a pushed snapshot from scratch, or refuse it.

    Re-runs the same shaping the agent did, on the agent's OUTPUT, so a
    snapshot that did not come from snapshot() -- an older agent, a bug, a
    stolen token -- is held to the same shape as one that did.
    """
    if not isinstance(body, dict) or body.get("version") != 1:
        return None, "a schedules snapshot needs version 1"
    read_at = _i(body.get("read_at"), 0, 2**40)
    if read_at is None:
        return None, "a schedules snapshot needs read_at"
    panel, aqd = body.get("panel"), body.get("aqualinkd")
    if not isinstance(panel, dict) or not isinstance(aqd, dict):
        return None, "a schedules snapshot needs panel and aqualinkd"
    st, rd, err, progs = programs_from_aqualinkd(
        {"type": "programs", **{k: panel.get(k) for k in
                                ("state", "read_at", "error", "programs")}})
    if st not in ("never_read", "reading", "ok", "failed", "unsupported",
                  "unreachable", "invalid"):
        st = "invalid"
    serr, sch, dropped = shape_schedules({"schedules": [
        {**s, "enabled": "1" if s.get("enabled") is True else "0"}
        for s in (aqd.get("schedules") or []) if isinstance(s, dict)]})
    return {"version": 1, "read_at": read_at,
            "panel": {"state": st, "read_at": rd, "error": err, "programs": progs,
                      "set": set_from_aqualinkd(panel)},
            "aqualinkd": {"error": _s(aqd.get("error"), 80) or serr,
                          "schedules": sch,
                          # What the Pi dropped, and what this dropped of
                          # the Pi's output: either means a save would lose
                          # entries nobody was shown.
                          "dropped": (_i(aqd.get("dropped"), 0, 999) or 0) + dropped,
                          "names": names_from_aqualinkd(aqd.get("names"))}}, None


# --------------------------------------------------------- changing one
def check_change(device, value, cfg):
    """The server's half of a program change: refuse, while the person is still
    looking at the form, anything AqualinkD would refuse a minute later.

    Against the Pi's last report, because that is what the page showed them.
    None when the change may go ahead; otherwise why not.
    """
    from . import commands as C
    t = C.program_times(value)
    if t is None:
        return "times are 24-hour HHMM-HHMM, two different times"
    rec = load(cfg)
    if not rec or rec.get("unreadable"):
        return "the Pi has not reported the panel's programs yet"
    panel = rec.get("panel") or {}
    if panel.get("state") != "ok":
        return "the panel's programs have not been read successfully yet"
    have = [p for p in panel.get("programs", []) if p.get("device") == device]
    if not have:
        return f"{device} is not on the panel's PROGRAM list"
    if have[0].get("of", 0) == 0:
        return f"{device} has no program to change; adding one is not supported yet"
    if have[0]["of"] > 1:
        return (f"{device} has {have[0]['of']} programs; only a single one can be "
                f"changed yet")
    st = panel.get("set") or {}
    if st.get("state") in ("queued", "running"):
        return "a change to the panel's programs is already in progress"
    return None


def check_cron(value, cfg):
    """The server's half of saving AqualinkD's schedules: refuse, while the
    person is still looking at the form, a save the agent would refuse.

    The page edited the list in the Pi's last report; `expect` is that list's
    fingerprint. If the report has moved on since, somebody else changed it,
    or the Pi read it differently -- either way the page is out of date. The
    agent checks the same thing against the Pi's file, which is the check
    that counts.
    """
    from . import commands as C
    rec = load(cfg)
    if not rec or rec.get("unreadable"):
        return "the Pi has not reported AqualinkD's schedules yet"
    aqd = rec.get("aqualinkd") or {}
    if aqd.get("dropped"):
        return ("AqualinkD has schedules this page cannot show; change them in "
                "AqualinkD's own page, or remove them there first")
    if aqd.get("error"):
        return f"AqualinkD's schedules could not be read: {aqd['error']}"
    have = aqd.get("schedules") or []
    for s in have:
        try:
            C.cron_entry(s)
        except C.Refused as e:
            return (f"a schedule already on the Pi is one this page will not "
                    f"write ({e}); change or remove it in AqualinkD's own page")
    if (value or {}).get("expect") != C.cron_fingerprint(have):
        return ("AqualinkD's schedules have changed since this page read them; "
                "reload it and make the change again")
    return None


# ------------------------------------------------------------------ storage
def path(cfg):
    from . import config
    return os.path.join(config.data_dir(cfg), FILE)


def save(clean, cfg, now=None):
    from . import config, locking
    rec = {**clean, "received_at": int(now if now is not None else time.time())}
    with locking.exclusive(config.data_dir(cfg), "panel_schedules"):
        locking.replace_atomically(path(cfg), lambda f: json.dump(rec, f, indent=1))
    return rec


def load(cfg):
    """The stored snapshot, or None. Never raises: an unreadable file is
    reported as unreadable by listing(), not as a crash in a page fetch."""
    try:
        with open(path(cfg)) as f:
            rec = json.load(f)
        return rec if isinstance(rec, dict) else {"unreadable": True}
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"unreadable": True}


def ingest(body, cfg, now=None):
    """/api/agent/schedules: validate, store, answer (result, error)."""
    clean, err = validate(body)
    if err:
        return None, err
    save(clean, cfg, now)
    return {"ok": True, "programs": len(clean["panel"]["programs"]),
            "schedules": len(clean["aqualinkd"]["schedules"])}, None


def listing(cfg, now=None):
    """What /api/panel-schedules returns: the snapshot, with every schedule
    described in words HERE, so the page has no cron arithmetic of its own."""
    from . import commands as C
    now = now if now is not None else time.time()
    rec = load(cfg)
    if rec is None:
        return {"have": False}
    if rec.get("unreadable"):
        return {"have": False, "unreadable": True}
    got = rec.get("received_at") or 0
    panel = rec.get("panel") or {}
    aqd = rec.get("aqualinkd") or {}
    names = aqd.get("names") or {}

    def panel_name(dev):
        # AqualinkD's own name for the device is the panel's; without it, the
        # label written in capitals is the panel's spelling often enough.
        return (names.get(dev) or C.label_for(dev, cfg)).upper()

    sch = [{"when": describe_cron(s), "device": device_of(s),
            "label": C.label_for(device_of(s), cfg), "action": action_of(s),
            "enabled": s["enabled"], "panel_name": panel_name(device_of(s)),
            # The fields themselves, for the editor: it sends the whole list
            # back, so it needs every entry as the Pi holds it.
            "entry": {k: s[k] for k in ("enabled", "min", "hour", "daym", "month",
                                        "dayw", "url", "value")}}
           for s in aqd.get("schedules", [])]
    # Devices a schedule may switch, labelled as the controls are; a heater
    # only off (commands.cron_entry says why).
    choices = [{"device": d, "label": C.label_for(d, cfg), "off_only": d in C.HEATERS,
                "panel_name": panel_name(d)}
               for d in C.CRON_DEVICES]
    return {"have": True, "received_at": got, "read_at": rec.get("read_at"),
            "age_s": max(0, int(now - got)), "stale": now - got > STALE_S,
            "panel": panel, "aqualinkd": {"error": aqd.get("error", ""),
                                          "schedules": sch,
                                          "fingerprint": C.cron_fingerprint(
                                              aqd.get("schedules", [])),
                                          "cannot_edit": check_cron(
                                              {"expect": C.cron_fingerprint(
                                                  aqd.get("schedules", []))}, cfg),
                                          "choices": choices},
            "overlaps": overlaps(panel.get("programs", []), sch)}


def overlaps(programs, schedules):
    """Equipment BOTH kinds of schedule switch. Neither knows about the
    other, so each switches it on its own timetable: a pump the panel stops at
    4 PM that AqualinkD starts again at 4:30. Only enabled cron lines count,
    and only devices that hold a panel program."""
    have = {p["device"].upper(): p for p in programs if p.get("of")}
    out = {}
    for s in schedules:
        p = have.get(s.get("panel_name", ""))
        if p and s.get("enabled"):
            o = out.setdefault(p["device"], {
                "device": p["device"], "panel": f"{p['on']} to {p['off']}, {p['days']}",
                "aqualinkd": []})
            o["aqualinkd"].append(f"{s['action']} {s['when']}")
    return list(out.values())
