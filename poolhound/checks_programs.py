"""The panel's own programs and AqualinkD's schedules: what the Pi reports.

Each case is a way this could be wrong while looking right. A stock AqualinkD
answering 404 must not take the cron schedules down with it. A device with no
program is a reading, not a gap. A cron line that is not a cron line must not
be described as one. And a snapshot that did not come from the agent's own
shaping is held to the same shape regardless.

The fixtures are AqualinkD's real response shapes. The programs one is this
panel as read on 2026-10-09; the schedules one is invented, because the Pi
had none.
"""
import json
import os
import shutil
import tempfile
import threading
import urllib.error

from .selftest import check

_FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _fix(name):
    with open(os.path.join(_FIX, name), encoding="utf-8") as f:
        return json.load(f)["response"]


def t_a_programs_answer_keeps_devices_with_none():
    print("\n  programs — a device with no program is a reading, not a gap")
    from . import programs as P
    st, read_at, err, progs = P.programs_from_aqualinkd(_fix("aqualinkd-programs.json"))
    check("the state is AqualinkD's own word", st, "ok")
    check("every device on the list is kept", len(progs), 10)
    check("the filter pump's program is read",
          progs[0], {"device": "FILTER PUMP", "pgm": 1, "of": 1, "on": "10:00 AM",
                     "off": "4:00 PM", "days": "ALL DAYS"})
    spa = [p for p in progs if p["device"] == "SPA"][0]
    check("and SPA says it has none", (spa["of"], spa["on"], spa["off"]), (0, "", ""))
    st, *_ = P.programs_from_aqualinkd({"type": "schedules"})
    check("an answer that is not a programs list is called invalid", st, "invalid")


def t_a_stock_aqualinkd_still_reports_its_schedules():
    print("\n  programs — a 404 on one route does not cost the other")
    from . import agent as A

    def get(route):
        if route == "/api/programs":
            raise urllib.error.HTTPError(route, 400, "unknown", {}, None)
        return _fix("aqualinkd-schedules.json")
    body = A.read_schedules(get)
    check("the panel half says AqualinkD could not read it",
          (body["panel"]["state"], "400" in body["panel"]["error"]), ("unreachable", True))
    check("and the cron half is still there", len(body["aqualinkd"]["schedules"]), 3)

    def down(route):
        raise OSError("connection refused")
    body = A.read_schedules(down)
    check("an AqualinkD that answers nothing is reported, not raised",
          (body["panel"]["state"], body["aqualinkd"]["schedules"]), ("unreachable", []))
    check("and names the route", "/api/schedules" in body["aqualinkd"]["error"], True)


def t_a_cron_line_is_described_or_refused():
    print("\n  programs — a cron line in words, and one that is not a cron line")
    from . import programs as P
    err, sch = P.schedules_from_aqualinkd(_fix("aqualinkd-schedules.json"))
    words = [(P.describe_cron(s), P.device_of(s), P.action_of(s), s["enabled"]) for s in sch]
    check("every day at 10:00, pump on",
          words[0], ("every day at 10:00 AM", "Filter_Pump", "on", True))
    check("weekdays", words[1], ("on weekdays at 4:30 PM", "Filter_Pump", "off", True))
    check("weekends, and a commented-out line is kept as disabled",
          words[2], ("at weekends at 7:00 PM", "Aux_1", "on", False))
    # A url that would be a different request if anything ever built one from it.
    bad = {"schedules": [
        {"enabled": "1", "min": "0", "hour": "10", "daym": "*", "month": "*", "dayw": "*",
         "url": "/api/Filter_Pump/set; rm -rf /", "value": "1"},
        {"enabled": "1", "min": "$(id)", "hour": "10", "daym": "*", "month": "*",
         "dayw": "*", "url": "/api/Spa/set", "value": "1"}]}
    err, sch = P.schedules_from_aqualinkd(bad)
    check("lines not in cron's shape are dropped", sch, [])
    check("and counted, not silently", "2 entry" in err, True)
    err, sch = P.schedules_from_aqualinkd({"message": "Error Schedules disabled"})
    check("a disabled scheduler says so", err, "Error Schedules disabled")


def t_a_pushed_snapshot_is_rebuilt_not_trusted():
    print("\n  programs — the server rebuilds what arrives, field by field")
    from . import programs as P
    good = P.snapshot(_fix("aqualinkd-programs.json"), "",
                      _fix("aqualinkd-schedules.json"), "", now=1791588500)
    clean, err = P.validate(json.loads(json.dumps(good)))
    check("the agent's own snapshot passes unchanged", (err, clean), (None, good))
    check("a snapshot with no version is refused",
          bool(P.validate({**good, "version": None})[1]), True)
    evil = json.loads(json.dumps(good))
    evil["panel"]["programs"][0]["device"] = "FILTER\x00PUMP<script>" + "x" * 200
    evil["panel"]["state"] = "pwned"
    evil["panel"]["extra"] = "kept?"
    clean, err = P.validate(evil)
    dev = clean["panel"]["programs"][0]["device"]
    check("control characters are stripped and length capped",
          ("\x00" in dev, len(dev) <= P.MAX_STR), (False, True))
    check("a state that is not one of the words becomes invalid",
          clean["panel"]["state"], "invalid")
    check("and an unknown key does not survive", "extra" in clean["panel"], False)


def t_the_snapshot_round_trips_through_the_data_directory():
    print("\n  programs — stored, listed in words, and stale when the Pi goes quiet")
    from . import config, programs as P, render
    d = tempfile.mkdtemp(prefix="poolhound-programs-")
    prev_env, prev_data = os.environ.get("POOLHOUND_DATA"), render.DATA
    try:
        os.environ["POOLHOUND_DATA"] = d
        render.DATA = d
        shutil.copy(os.path.join(config.root(), "config", "config.example.toml"),
                    os.path.join(d, "config.toml"))
        cfg = config.load()
        check("before any push, there is nothing to show", P.listing(cfg), {"have": False})
        body = P.snapshot(_fix("aqualinkd-programs.json"), "",
                          _fix("aqualinkd-schedules.json"), "", now=1791588500)
        r, e = P.ingest(body, cfg, now=1791588500)
        check("a push is stored", (e, r and r["programs"], r and r["schedules"]), (None, 10, 3))
        got = P.listing(cfg, now=1791588500 + 60)
        check("and listed as fresh", (got["have"], got["stale"], got["age_s"]), (True, False, 60))
        check("with the cron line in words and the device by its label",
              (got["aqualinkd"]["schedules"][0]["when"], got["aqualinkd"]["schedules"][0]["label"]),
              ("every day at 10:00 AM", "Filter pump"))
        got = P.listing(cfg, now=1791588500 + P.STALE_S + 1)
        check("three missed pushes reads as stale", got["stale"], True)
        with open(P.path(cfg), "w") as f:
            f.write("{not json")
        check("an unreadable file is reported, not raised",
              P.listing(cfg), {"have": False, "unreadable": True})
    finally:
        render.DATA = prev_data
        if prev_env is None:
            os.environ.pop("POOLHOUND_DATA", None)
        else:
            os.environ["POOLHOUND_DATA"] = prev_env
        shutil.rmtree(d, ignore_errors=True)


def t_the_reporter_refreshes_late_and_pushes_anyway():
    print("\n  programs — the panel walk waits, the push does not, and neither is fatal")
    from . import agent as A
    now = [1000.0]
    calls, pushed = [], []
    stop = threading.Event()

    class Srv:
        def _req(self, path, body, method, timeout):
            pushed.append((path, body["read_at"] is not None))
            if len(pushed) >= 3:
                stop.set()
            class Ctx:
                def __enter__(self): return self
                def __exit__(self, *a): return False
            return Ctx()

    def get(route):
        calls.append(route)
        return _fix("aqualinkd-programs.json") if route == "/api/programs" \
            else _fix("aqualinkd-schedules.json")

    def poke(route):
        calls.append(route)
        raise OSError("stock AqualinkD")       # a refresh that fails is logged, not fatal

    real_wait = stop.wait
    def wait(s):
        now[0] += s
        return real_wait(0)
    stop.wait = wait
    A.schedules_reporter(Srv(), stop, get=get, poke=poke,
                         clock=lambda: now[0], first_refresh=300)
    check("the first push does not wait for the panel walk",
          calls[:2], ["/api/programs", "/api/schedules"])
    check("the walk is asked for once its delay has passed",
          "/api/programs/refresh" in calls, True)
    check("and a failed refresh does not stop the pushes",
          [p for p, _ in pushed], ["/api/agent/schedules"] * 3)


# ------------------------------------------------------- changing a program
def t_a_program_change_is_shaped_by_the_vocabulary():
    print("\n  programs — a change is a command, and the vocabulary decides its shape")
    from . import commands as C
    ok = C.validate(C.new_command("program", "SOLAR HEAT", "1040-1555", "someone"))
    check("a well-formed change passes", (ok["device"], ok["value"]), ("SOLAR HEAT", "1040-1555"))
    check("and reads as a sentence",
          C.describe(ok), "SOLAR HEAT program to on 10:40 AM, off 3:55 PM")
    check("the audit log's own spelling of it reads the same",
          C.describe(C.from_detail("SOLAR HEAT", "program=1040-1555")), C.describe(ok))
    for dev, val, why in (("SOLAR HEAT", "1040-1040", "the same time twice"),
                          ("SOLAR HEAT", "2460-1555", "an hour that does not exist"),
                          ("SOLAR HEAT", "10:40-15:55", "a separator in a time"),
                          ("solar heat", "1040-1555", "a name the panel does not write"),
                          ("SOLAR HEAT/../X", "1040-1555", "a path in the device"),
                          ("SOLAR HEAT", None, "no times at all")):
        try:
            C.validate(C.new_command("program", dev, val, "someone"))
            got = "accepted"
        except C.Refused:
            got = "refused"
        check(f"refused: {why}", got, "refused")
    check("a change waits a walk's length before another", C.cooldown_for(ok) >= 120, True)


def t_the_server_refuses_what_aqualinkd_would():
    print("\n  programs — refused while the person is looking, not a minute later")
    from . import config, programs as P, render
    d = tempfile.mkdtemp(prefix="poolhound-programs-")
    prev_env, prev_data = os.environ.get("POOLHOUND_DATA"), render.DATA
    try:
        os.environ["POOLHOUND_DATA"] = d
        render.DATA = d
        shutil.copy(os.path.join(config.root(), "config", "config.example.toml"),
                    os.path.join(d, "config.toml"))
        cfg = config.load()
        check("before the Pi has reported: refused",
              "not reported" in (P.check_change("SOLAR HEAT", "1040-1555", cfg) or ""), True)
        raw = _fix("aqualinkd-programs.json")
        raw["programs"].append({"device": "POOL LIGHT2", "pgm": 1, "of": 2,
                                "on": "7:00 PM", "off": "9:00 PM", "days": "ALL DAYS"})
        P.ingest(P.snapshot(raw, "", _fix("aqualinkd-schedules.json"), ""), cfg)
        check("a device with one program may be changed",
              P.check_change("SOLAR HEAT", "1040-1555", cfg), None)
        check("one with none may not (adding is unmapped)",
              "no program" in (P.check_change("SPA", "1040-1555", cfg) or ""), True)
        check("one with several may not (paging is unseen)",
              "2 programs" in (P.check_change("POOL LIGHT2", "1900-2100", cfg) or ""), True)
        check("one the panel does not list may not",
              "not on the panel" in (P.check_change("HOT TUB", "1040-1555", cfg) or ""), True)
        raw["set"] = {"state": "running", "device": "SOLAR HEAT", "want_on": "10:40 AM",
                      "want_off": "3:55 PM", "requested_at": 1, "done_at": 0, "error": "",
                      "now_on": "", "now_off": ""}
        P.ingest(P.snapshot(raw, "", None, "x"), cfg)
        check("nor anything while a change is running",
              "already in progress" in (P.check_change("SOLAR HEAT", "1040-1555", cfg) or ""), True)
        check("and the running change travels to the page",
              P.listing(cfg)["panel"]["set"]["state"], "running")
        raw["set"]["state"] = "pwned"
        P.ingest(P.snapshot(raw, "", None, "x"), cfg)
        check("a state that is not one of AqualinkD's words is dropped",
              P.listing(cfg)["panel"]["set"], None)
    finally:
        render.DATA = prev_data
        if prev_env is None:
            os.environ.pop("POOLHOUND_DATA", None)
        else:
            os.environ["POOLHOUND_DATA"] = prev_env
        shutil.rmtree(d, ignore_errors=True)


def t_the_agent_acks_at_once_and_follows_the_panel():
    print("\n  programs — the stream is not held for a minute, and a refusal says why")
    from . import agent as A, commands as C
    cmd = C.validate(C.new_command("program", "SOLAR HEAT", "1040-1555", "someone"))
    sent, followed = [], []

    class G:
        def record(self, c): sent.append(("recorded", c["id"]))
    r = A.change_program(cmd, G(), poke=lambda path: (sent.append(path) or (200, "Ok")),
                         follow=lambda c, get: followed.append(c["device"]))
    check("AqualinkD is asked for exactly this change",
          sent[0], "/api/programs/set/SOLAR HEAT/1040-1555")
    check("the ack says accepted, not done", (r["ok"], "being changed" in r["detail"]), (True, True))
    check("and a follower is started", followed, ["SOLAR HEAT"])
    followed.clear()
    r = A.change_program(cmd, G(), poke=lambda path: (400, "that device has several programs"),
                         follow=lambda c, get: followed.append(1))
    check("a refusal from AqualinkD is a refusal, with its reason",
          (r["ok"], "several programs" in r["error"]), (False, True))
    check("and nothing follows it", followed, [])


def t_a_change_is_followed_to_the_panels_answer():
    print("\n  programs — the follower waits for the panel's word, then reports")
    from . import agent as A, commands as C
    cmd = C.validate(C.new_command("program", "SOLAR HEAT", "1040-1555", "someone"))
    answers = iter([{"set": {"state": "running"}}, {"set": {"state": "running"}},
                    {"set": {"state": "ok"}}, {"set": {"state": "ok"}}])
    polled, reported, now = [], [], [0.0]

    def get(route):
        polled.append(route)
        return next(answers)
    prev = A._REPORT
    A._REPORT = lambda: reported.append(len(polled))
    try:
        def tick(s):
            now[0] += s
        t = A._follow_program(cmd, get, clock=lambda: now[0], wait=tick)
        t.join(5)
    finally:
        A._REPORT = prev
    check("it polls until the panel says ok", len(polled), 3)
    check("and then pushes a fresh report, once", reported, [3])


def t_equipment_scheduled_in_both_places_is_called_out():
    print("\n  programs — the same equipment in both schedules is said, by its panel name")
    from . import programs as P
    progs = _fix("aqualinkd-programs.json")
    sch = _fix("aqualinkd-schedules.json")     # Filter_Pump twice, Aux_1 disabled
    names = {"Filter_Pump": "FILTER PUMP", "Aux_1": "POOL LIGHT", "Extra_Aux": "SOLAR HEAT",
             "bad id!": "X", "Spa": "\x00SPA"}
    body = P.snapshot(progs, "", sch, "", names=names)
    check("AqualinkD's names travel, cleaned, and a bad id does not",
          body["aqualinkd"]["names"],
          {"Filter_Pump": "FILTER PUMP", "Aux_1": "POOL LIGHT", "Extra_Aux": "SOLAR HEAT", "Spa": "SPA"})
    clean, _ = P.validate(json.loads(json.dumps(body)))
    panel = clean["panel"]["programs"]
    listed = [{"when": P.describe_cron(s), "action": P.action_of(s), "enabled": s["enabled"],
               "panel_name": clean["aqualinkd"]["names"].get(P.device_of(s), "")}
              for s in clean["aqualinkd"]["schedules"]]
    ov = P.overlaps(panel, listed)
    check("the filter pump, programmed in the panel and in AqualinkD, is called out",
          [o["device"] for o in ov], ["FILTER PUMP"])
    check("with both AqualinkD lines, in words",
          ov[0]["aqualinkd"], ["on every day at 10:00 AM", "off on weekdays at 4:30 PM"])
    check("and the panel's own hours beside them",
          ov[0]["panel"], "10:00 AM to 4:00 PM, ALL DAYS")
    for s in listed:
        s["enabled"] = False
    check("a commented-out cron line is not a clash", P.overlaps(panel, listed), [])
