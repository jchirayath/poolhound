"""AqualinkD's own schedules, saved from the site, and every way that could go wrong.

A save rewrites a file of lines cron runs as root, replacing the whole list.
So each case is one of: a typed string reaching that line, a heater lit by a
line nobody watches, somebody else's edit wiped without a word, entries the
page never showed deleted with it, or a save reported done that did not land.
"""
import json
import os
import shutil
import tempfile

from .selftest import check


def _refused(e):
    from . import commands as C
    try:
        C.cron_entry(e)
    except C.Refused as r:
        return str(r)
    return ""


def _one(dev="Aux_1", value="1", hour="19", minute="0", dayw="*"):
    return {"device": dev, "value": value, "hour": hour, "min": minute, "dayw": dayw}


def t_a_schedule_is_rebuilt_from_known_parts_never_passed_through():
    print("\n  AqualinkD schedules — nothing typed reaches a line cron runs as root")
    from . import commands as C
    check("a value with a command in it", _refused(_one(value="1; reboot")) != "", True)
    check("a url naming something that is not a switch",
          _refused({"url": "/api/x;id/set", "value": "1", "hour": "1", "min": "0",
                    "dayw": "*"}) != "", True)
    check("a time that is a cron expression", _refused(_one(hour="*/2")) != "", True)
    check("a day list with a day twice", _refused(_one(dayw="1,1")) != "", True)
    check("the salt cell's boost, whose id has a slash", _refused(_one(dev="SWG/Boost")) != "", True)
    check("a day of the month", _refused({**_one(), "daym": "1"}) != "", True)
    e = C.cron_entry(_one(dev="Filter_Pump", hour="07", minute="05", dayw="5,1,3"))
    check("what is kept is built here: the url from the device, the days in order",
          (e["url"], e["hour"], e["min"], e["dayw"]), ("/api/Filter_Pump/set", "7", "5", "1,3,5"))


def t_a_cron_line_may_switch_a_heater_off_but_never_on():
    print("\n  AqualinkD schedules — a gas heater is never lit by a line nobody watches")
    check("a heater on is refused, and says where to go instead",
          "poolhound's own schedule" in _refused(_one(dev="Spa_Heater", value="1")), True)
    check("a heater off is fine", _refused(_one(dev="Spa_Heater", value="0")), "")


def _cmd(expect, schedules):
    import datetime as dt
    return {"id": "a" * 16, "action": "cron", "issued": dt.datetime.now(dt.timezone.utc).isoformat(),
            "value": {"expect": expect, "schedules": schedules}}


class _Guard:
    def __init__(self):
        self.recorded = []

    def record(self, cmd):
        self.recorded.append(cmd["id"])


class _FakeAqualinkd:
    """Holds a list as AqualinkD does, answering /api/schedules with it and
    replacing it on /api/schedules/set -- or, with `mangle`, keeping
    something other than what it was sent."""
    def __init__(self, entries, mangle=False):
        self.entries, self.mangle, self.puts = list(entries), mangle, []

    def get(self, path):
        assert path == "/api/schedules"
        return {"type": "schedules", "schedules": [dict(e) for e in self.entries]}

    def put(self, path, body):
        self.puts.append((path, json.loads(json.dumps(body))))
        got = body["schedules"][:-1] if self.mangle else body["schedules"]
        self.entries = [dict(e) for e in got]
        return 200, '{"message":"Saved Schedules"}'


def _held(entries):
    """A list in the shape AqualinkD answers with, as the page saw it."""
    from . import commands as C
    return [{**C.cron_entry(e), "enabled": "1"} for e in entries]


def t_a_save_is_refused_when_the_list_changed_since_the_page_read_it():
    print("\n  AqualinkD schedules — somebody else's edit is never wiped without a word")
    from . import agent as A, commands as C, programs as P
    seen = _held([_one()])
    aq = _FakeAqualinkd(_held([_one(), _one(dev="Aux_2")]))   # changed meanwhile
    expect = C.cron_fingerprint(P.shape_schedules({"schedules": seen})[1])
    try:
        A.change_cron(C.validate(_cmd(expect, [])), _Guard(), get=aq.get, put=aq.put, report=False)
        why = ""
    except C.Refused as e:
        why = str(e)
    check("refused, saying why", "changed" in why, True)
    check("and nothing was sent to AqualinkD", aq.puts, [])


def t_a_save_sends_the_rebuilt_list_and_checks_what_landed():
    print("\n  AqualinkD schedules — saved means the Pi holds exactly that")
    from . import agent as A, commands as C, programs as P
    aq = _FakeAqualinkd(_held([_one()]))
    expect = C.cron_fingerprint(P.shape_schedules(aq.get("/api/schedules"))[1])
    new = [_one(), _one(dev="Filter_Pump", value="0", hour="16", minute="30", dayw="1-5")]
    r = A.change_cron(C.validate(_cmd(expect, new)), _Guard(), get=aq.get, put=aq.put, report=False)
    check("saved and confirmed", r["ok"], True)
    path, body = aq.puts[0]
    check("to AqualinkD's save route, every field a string as it reads them",
          (path, body["schedules"][1]),
          ("/api/schedules/set", {"enabled": "1", "min": "30", "hour": "16", "daym": "*",
                                  "month": "*", "dayw": "1-5", "url": "/api/Filter_Pump/set",
                                  "value": "0"}))
    bad = _FakeAqualinkd(_held([_one()]), mangle=True)
    r = A.change_cron(C.validate(_cmd(expect, new)), _Guard(), get=bad.get, put=bad.put, report=False)
    check("a save that landed differently is reported as not done",
          (r["ok"], "other than" in (r.get("error") or "")), (False, True))


def t_a_list_it_cannot_show_in_full_is_never_rewritten():
    print("\n  AqualinkD schedules — entries the page never showed are not deleted with a save")
    from . import programs as P
    many = _held([_one(minute=str(m % 60), hour=str(m // 60)) for m in range(P.MAX_SCHEDULES + 2)])
    err, out, dropped = P.shape_schedules({"schedules": many})
    check("entries past the cap are counted, not silently cut", (len(out), dropped),
          (P.MAX_SCHEDULES, 2))
    odd = P.shape_schedules({"schedules": [{"enabled": "1", "min": "0", "hour": "1", "daym": "*",
                                            "month": "*", "dayw": "*", "url": "/api/Aux_1/set/x",
                                            "value": "1"}]})
    check("an entry in a shape it does not know is counted too", odd[2], 1)


def t_the_server_refuses_a_stale_save_before_it_is_queued():
    print("\n  AqualinkD schedules — a page out of date is told so at once")
    from . import commands as C, config, programs as P, render, server
    d = tempfile.mkdtemp(prefix="poolhound-cron-")
    prev = (os.environ.get("POOLHOUND_DATA"), render.DATA)
    os.environ["POOLHOUND_DATA"] = d
    render.DATA = d
    try:
        shutil.copy(os.path.join(config.root(), "config", "config.example.toml"),
                    os.path.join(d, "config.toml"))
        cfg = config.load()
        held = _held([_one()])
        P.ingest(P.snapshot(None, "x", {"type": "schedules", "schedules": held}, ""), cfg)
        fp = P.listing(cfg)["aqualinkd"]["fingerprint"]
        check("the listing offers the list's fingerprint, and says it may be edited",
              (fp, P.listing(cfg)["aqualinkd"]["cannot_edit"]),
              (C.cron_fingerprint(P.shape_schedules({"schedules": held})[1]), None))
        r, err = server.issue_control({"action": "cron", "value": {"expect": "0" * 16,
                                                                   "schedules": []}}, cfg, "x")
        check("a save against an older list is refused by the server",
              "reload" in (err or ""), True)
        P.ingest(P.snapshot(None, "x", {"type": "schedules", "schedules": _held(
            [_one(dev="Spa_Heater", value="0")]) + [{**_held([_one()])[0],
                                                    "url": "/api/Spa_Heater/set"}]}, ""), cfg)
        check("a list holding a line this page would refuse cannot be edited here",
              "will not write" in (P.listing(cfg)["aqualinkd"]["cannot_edit"] or ""), True)
    finally:
        render.DATA = prev[1]
        if prev[0] is None:
            os.environ.pop("POOLHOUND_DATA", None)
        else:
            os.environ["POOLHOUND_DATA"] = prev[0]
        shutil.rmtree(d, ignore_errors=True)
