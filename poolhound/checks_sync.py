"""poolhound local and poolhound, kept in step -- and every way that could go wrong.

Two copies of the same scenes and schedules, on two machines, edited by two
pages, and one of them runs them. So each case is one of: an older copy
overwriting a newer one, a deletion coming back, a schedule running twice or
from the wrong machine, the Pi's card written for nothing, a sync when nothing
changed, or somebody's address shown to the house network.
"""
import datetime as dt
import json
import os
import shutil
import tempfile

from .selftest import check


class _Machine:
    """A data directory standing in for one machine's disk. Entering it points
    POOLHOUND_DATA there, which is how scenes.py and schedule.py find their
    files on the server and on the Pi alike."""
    def __init__(self, name):
        from . import config
        self.dir = tempfile.mkdtemp(prefix=f"poolhound-{name}-")
        shutil.copy(os.path.join(config.root(), "config", "config.example.toml"),
                    os.path.join(self.dir, "config.toml"))
        self._prev = []

    def __enter__(self):
        self._prev.append(os.environ.get("POOLHOUND_DATA"))
        os.environ["POOLHOUND_DATA"] = self.dir
        return self

    def __exit__(self, *a):
        prev = self._prev.pop()
        if prev is None:
            os.environ.pop("POOLHOUND_DATA", None)
        else:
            os.environ["POOLHOUND_DATA"] = prev
        return False

    def gone(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class _Link:
    """The agent's Server, as far as a sync is concerned: each request is
    answered by the real server half, on the server's own disk."""
    def __init__(self, server_machine, cfg):
        self.srv, self.cfg, self.requests = server_machine, cfg, []

    def _req(self, path, body, method, timeout):
        from . import sync
        self.requests.append("full" if "defs" in body else "check")
        with self.srv:
            ans, err = sync.exchange(json.loads(json.dumps(body)), self.cfg)
        assert err is None, err
        raw = json.dumps(ans).encode()

        class R:
            def read(self_inner): return raw
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *a): return False
        return R()


def _runner(link=None, execute=None, panel=None, deaf=False):
    """A runner over a fake panel that does what it is told -- or, `deaf`,
    answers every press and changes nothing, as a sleeping PDA does."""
    from . import commands as C, pirunner
    calls = []
    state = dict(panel or {"pump": 1, "spa": 0, "spa_heat": 0, "pool_light": 0, "sheer": 0})

    def ex(cmd, guard):
        calls.append((cmd["action"], cmd["device"], cmd.get("value")))
        col = C.state_column(cmd["device"])
        if col and not deaf and cmd["action"] in ("set", "heat"):
            state[col] = 1 if cmd.get("value") else 0
        return {"ok": True, "detail": "ok"}
    r = pirunner.Runner(execute or ex, guard=None, read_sample=lambda: dict(state),
                        server=link, log=lambda *a: None, sleep=lambda s: None)
    return r, calls


def t_the_later_update_wins_and_a_deletion_stays_deleted():
    print("\n  sync — last update wins, per item, and a deletion travels like an edit")
    from . import syncdefs as SD
    a = {"scenes": [{"id": "s1", "name": "Old", "steps": [], "updated_at": "2026-10-11T10:00:00.000+00:00"}]}
    b = {"scenes": [{"id": "s1", "name": "New", "steps": [], "updated_at": "2026-10-11T10:05:00.000+00:00"}]}
    check("the later edit wins whichever side it is on",
          (SD.merge(a, b)["scenes"][0]["name"], SD.merge(b, a)["scenes"][0]["name"]), ("New", "New"))
    gone = {"deleted": [{"id": "s1", "kind": "scene", "updated_at": "2026-10-11T10:06:00.000+00:00"}]}
    check("a deletion after the edit removes it", SD.merge(b, gone)["scenes"], [])
    early = {"deleted": [{"id": "s1", "kind": "scene", "updated_at": "2026-10-11T09:00:00.000+00:00"}]}
    check("an edit after the deletion brings it back", len(SD.merge(early, b)["scenes"]), 1)
    x = {"schedules": [{"id": "abcdef", "updated_at": "2026-10-11T12:00:00.000+00:00", "last_key": "2026-10-10"}]}
    y = {"schedules": [{"id": "abcdef", "updated_at": "2026-10-11T08:00:00.000+00:00", "last_key": "2026-10-11"}]}
    check("the day a schedule last ran is the later one, whichever copy wins",
          SD.merge(x, y)["schedules"][0]["last_key"], "2026-10-11")


def t_a_change_on_either_page_reaches_the_other():
    print("\n  sync — made on poolhound local, seen on poolhound; deleted there, gone here")
    from . import config, scenes as SC, sync
    srv, pi = _Machine("server"), _Machine("pi")
    try:
        with srv:
            cfg = config.load()
        link = _Link(srv, cfg)
        with pi:
            r, _ = _runner(link)
            row, err = r.edit("scene/save", SC.templates()[3])
            check("saved at the pool", err, None)
            r.sync_once()
        with srv:
            check("and now on the server", [s["name"] for s in SC.load(cfg)],
                  ["Filter, sheer descent and lights"])
            SC.delete({"id": row["id"]}, "someone@example.com", cfg)
        with pi:
            r.sync_once()
            check("deleted on the server, gone at the pool", SC.load(None), [])
            link.requests.clear()
            r.sync_once()
            check("and with nothing changed, one small request and no more",
                  link.requests, ["check"])
    finally:
        srv.gone()
        pi.gone()


def t_the_pi_writes_its_card_only_when_something_changed():
    print("\n  sync — a quiet tick writes nothing to the SD card")
    from . import schedule as S
    pi = _Machine("pi")
    try:
        with pi:
            r, calls = _runner()
            now = S.utcnow()
            S.create({"action": "set", "device": "Aux_1", "value": 1, "repeat": "daily",
                      "time": (now + dt.timedelta(hours=3)).astimezone(S.tz()).strftime("%H:%M")},
                     "door:pool.html", {})
            path = S.path()
            r.tick(now)
            before = os.stat(path).st_mtime_ns
            r.tick(now + dt.timedelta(seconds=20))
            r.tick(now + dt.timedelta(seconds=40))
            check("two quiet ticks later the schedule file is untouched",
                  os.stat(path).st_mtime_ns, before)
            check("and nothing was pressed", calls, [])
    finally:
        pi.gone()


def t_the_pi_runs_a_schedule_once_and_records_its_automatic_off():
    print("\n  sync — the Pi fires a due schedule once, and keeps its off-after")
    from . import schedule as S
    pi = _Machine("pi")
    try:
        with pi:
            r, calls = _runner()
            t0 = S.utcnow().replace(second=0, microsecond=0)
            created = t0 - dt.timedelta(days=1)
            S.create({"action": "set", "device": "Spa", "value": 1, "repeat": "daily",
                      "time": t0.astimezone(S.tz()).strftime("%H:%M"), "off_after_h": 2},
                     "door:pool.html", {}, now=created)
            r.tick(t0 + dt.timedelta(seconds=30))
            r.tick(t0 + dt.timedelta(seconds=50))
            check("pressed once, not on every tick", calls, [("set", "Spa", 1)])
            offs = S.listing({})["autooffs"]
            check("and the Pi holds the automatic off it asked for",
                  [(o["device"], o["cause"]) for o in offs], [("Spa", "off-after")])
    finally:
        pi.gone()


def t_a_press_the_panel_ignored_is_not_reported_as_done():
    print("\n  sync — a scheduled press is judged by the panel, not by AqualinkD's 200")
    from . import scenes as SC
    pi = _Machine("pi")
    try:
        with pi:
            r, calls = _runner(deaf=True)
            row, _ = SC.save(SC.templates()[3], "door:pool.html", {})
            out = r.run_scene(row["id"], "door:pool.html")
            # The pump was already on, so its step is honestly done; the sheer
            # descent is the first the sleeping panel ignores.
            check("the panel did not change, so the scene says so and stops there",
                  (out["ok"], "did not change" in out.get("error", ""), [c[1] for c in calls]),
                  (False, True, ["Filter_Pump", "Aux_2"]))
    finally:
        pi.gone()


def t_once_the_pi_runs_them_the_server_never_fires_one():
    print("\n  sync — one runner: after the hand-over the server fires nothing")
    from . import config, schedule as S, scenes as SC
    srv, pi = _Machine("server"), _Machine("pi")
    try:
        with srv:
            cfg = config.load()
            t0 = S.utcnow().replace(second=0, microsecond=0)
            S.create({"action": "set", "device": "Aux_1", "value": 1, "repeat": "daily",
                      "time": (t0 + dt.timedelta(hours=1)).astimezone(S.tz()).strftime("%H:%M")},
                     "local", cfg, now=t0 - dt.timedelta(days=1))
            check("before the Pi has synced, the server is still the runner", S.pi_runs(), False)
            early = []
            S.look(cfg, now=t0 + dt.timedelta(hours=1, seconds=30),
                   issue=lambda cmd, by, cfg, off_after_h=None: (
                       early.append(cmd["device"]) or ({"result": {"ok": True}}, None)),
                   notify=lambda **k: True)
            check("and fires a due schedule itself", early, ["Aux_1"])
        with pi:
            r, _ = _runner(_Link(srv, cfg))
            r.sync_once()
            check("the server's schedule is on the Pi", len(S.listing({})["schedules"]), 1)
        with srv:
            check("after it, the Pi is", S.pi_runs(), True)
            pressed = []

            def issue(cmd, by, cfg, off_after_h=None):
                pressed.append(cmd["device"])
                return {"result": {"ok": True}}, None
            # A second schedule, due after the hand-over: the first already ran
            # today, so it could not tell a server that still fires from one
            # that does not.
            S.create({"action": "set", "device": "Aux_2", "value": 1, "repeat": "daily",
                      "time": (t0 + dt.timedelta(hours=2)).astimezone(S.tz()).strftime("%H:%M")},
                     "local", cfg, now=t0 - dt.timedelta(days=1))
            S.look(cfg, now=t0 + dt.timedelta(hours=2, seconds=30), issue=issue,
                   notify=lambda **k: True, nudge=lambda: None)
            check("and at the schedule's time the server's thread presses nothing",
                  pressed, [])
    finally:
        srv.gone()
        pi.gone()


def t_poolhound_local_never_shows_who():
    print("\n  sync — the house network sees which page, never whose address")
    from . import schedule as S
    pi = _Machine("pi")
    try:
        with pi:
            r, _ = _runner()
            S.create({"action": "set", "device": "Aux_1", "value": 1, "repeat": "daily",
                      "time": "21:00"}, "alex@pool.example", {})
            v = json.dumps(r.view())
            check("no address anywhere in what poolhound local is served",
                  "alex@pool.example" in v, False)
            check("it says the app made it", '"by": "poolhound app"' in v, True)
    finally:
        pi.gone()


def t_a_page_rebound_to_the_pi_cannot_edit_its_schedules():
    print("\n  sync — poolhound local's edits answer only to the Pi's own names")
    from . import agent as A
    check("the Pi by address, by bare or .local name, and on loopback",
          [A.local_host_ok(h, "") for h in ("192.0.2.10", "pool-pi.local:80", "[::1]",
                                            "localhost")],
          [True, True, True, True])
    check("a name pointed at the Pi from outside is refused",
          [A.local_host_ok(h, "") for h in ("pool.evil.example", "evil.example:80", "a.b.local",
                                            "", "pool-pi", "pool-pi.local.")],
          [False, False, False, False, False, False])
    check("a name the household lists is allowed", A.local_host_ok("pool.home.arpa", "pool.home.arpa"), True)


def t_a_heater_sent_but_not_yet_seen_still_gets_its_automatic_off():
    print("\n  sync — sent but not confirmed: the automatic off is kept, not lost")
    from . import schedule as S
    pi = _Machine("pi")
    try:
        with pi:
            r, calls = _runner(deaf=True)
            import datetime as _dt
            from . import commands as C
            cmd = C.new_command("set", "Spa", 1, "door:pool.html", 2)
            out = r.run_command(cmd, "door:pool.html", verify=True)
            check("the panel had not changed when it was read back", out["ok"], False)
            check("and the automatic off is there anyway",
                  [o["device"] for o in S.listing({})["autooffs"]], ["Spa"])
    finally:
        pi.gone()


def t_a_runner_that_just_started_is_not_reported_dead():
    print("\n  sync — a Pi that restarted moments before reporting is running, not stale")
    from . import schedule as S
    now = S.utcnow()
    t = now.timestamp()
    import unittest.mock as M
    rec = {"received_at": t, "read_at": t,
           "runner": {"started": S._iso(now), "last_tick": None, "errors": 0}}
    with M.patch("poolhound.programs.load", return_value=rec):
        v = S.runner_view({}, {"pi_at": S._iso(now)}, now)
    check("not stale", v["stale"], False)
    rec["runner"]["started"] = S._iso(now - __import__("datetime").timedelta(hours=1))
    with M.patch("poolhound.programs.load", return_value=rec):
        v = S.runner_view({}, {"pi_at": S._iso(now)}, now)
    check("but one that started an hour before and never looked is", v["stale"], True)


def t_an_edit_on_the_website_tells_the_pi_at_once():
    print("\n  sync — a scene or schedule changed on the website nudges the Pi to sync now")
    from . import config, scenes as SC, server
    srv = _Machine("server")
    nudges = []
    real = server.QUEUE.nudge
    try:
        with srv:
            cfg = config.load()
            server.QUEUE.nudge = lambda: nudges.append(1)
            h = server.Handler.__new__(server.Handler)
            h.whoami = lambda: "alex@pool.example"
            h._json = lambda code, body: (code, body)
            code, row = h._dispatch_write("/api/scene", SC.templates()[3], cfg)
            check("saving a scene answers and nudges", (code, len(nudges)), (200, 1))
            h._dispatch_write("/api/scene/delete", {"id": row["id"]}, cfg)
            code, sch = h._dispatch_write("/api/schedule", {
                "action": "set", "device": "Aux_1", "value": 1, "repeat": "daily",
                "time": "21:30"}, cfg)
            h._dispatch_write("/api/schedule/delete", {"id": sch["id"]}, cfg)
            check("and so does deleting it, and making and cancelling a schedule",
                  len(nudges), 4)
    finally:
        server.QUEUE.nudge = real
        srv.gone()


def t_a_nudge_reaches_the_agent_as_a_sync_not_a_command():
    print("\n  sync — the nudge travels the command stream, and is not a command")
    from . import queue_
    q = queue_.CommandQueue()
    sub = q.subscribe()
    q.nudge()
    got = sub.get(timeout=1)
    check("a connected agent receives {type: sync}", got, {"type": "sync"})
    check("and nothing was issued, logged or counted against the rate limit",
          (len(q._log), len(q._recent)), (0, 0))
