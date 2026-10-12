"""The Pi runs the scenes and the schedules, and keeps them in step with the server.

docs/LOCAL-AND-SYNC.md is the design. In short: the Pi is the ONE runner, so a
schedule fires when the internet or the server is down and can never fire
twice; poolhound local edits the same scenes and schedules the web app does;
and the two copies are merged, last update wins, only when one of them
changed.

THE SAME CODE AS THE SERVER. Nothing here re-implements a rule. scenes.py
validates and orders a scene, schedule.py decides when a schedule is due, what
is missed, what an automatic off is and when to give up on one; this module
gives them what they need on the Pi:

  issue     a command, run here: validated, through the Guard (cooldowns,
            rate limit, ids), to AqualinkD -- the path a command from the
            server takes -- then schedule.note_command() for its automatic off
  samples   the panel as it is NOW, read every tick, in the server's sample
            shape, plus when each watched device came on (kept on the card,
            written only when that changes), for the household max-on
  storage   /var/lib/poolhound (POOLHOUND_DATA): scenes.json, schedules.json,
            the settings the server sent. Written only when something changed.

WHAT IT DOES NOT DO. It has no access list: the server withdraws a schedule
whose author lost the right, and that arrives here as a deletion. It cannot
send mail: what needs telling is in the history, which the server mails and
poolhound local shows.

STDLIB ONLY, like the agent that imports it.
"""
import datetime as dt
import json
import os
import threading
import time

from . import commands as C, scenes as SC, schedule as S, sync as SY, syncdefs as SD

SETTINGS = "pi_settings.json"
ON_SINCE = "pi_on_since.json"
LOCAL_DOOR = "door:pool.html"
SYNC_RETRY_S = (30, 60, 120, 300, 600)

# WAKE, PRESS, READ BACK -- for what the Pi runs on its own. With the panel's
# sleep mode on, the FIRST command after a sleep is routinely lost while
# AqualinkD still answers 200 (measured from poolhound local; the daemon logs
# "didn't find a requested menu"). A press from the app has a person and a
# "pending" on the page to notice; a schedule at 19:00 has nobody. So a read
# wakes the panel first, and the result is what the panel REPORTS afterwards,
# never the 200. Not retried: the Guard's cooldowns stand between a retry and
# a short-cycled heater, and a failure here is reported, not hidden. The
# timings are poolhound local's, measured on this panel.
WAKE_S = 5
SETTLE_S = 14
SETTLE_SLOW_S = 45
SETPOINT_SETTLE_S = 20
SLOW = ("Spa", "Spa_Heater", "Pool_Heater")


def _data():
    return os.environ.get("POOLHOUND_DATA") or "/var/lib/poolhound"


def _read_json(name, default):
    try:
        with open(os.path.join(_data(), name), encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, type(default)) else default
    except (OSError, ValueError):
        return default


def _write_json(name, obj):
    """Atomic, and only called when the content changed."""
    from . import locking
    locking.replace_atomically(os.path.join(_data(), name), lambda f: json.dump(obj, f))


_EMAIL = __import__("re").compile(r"[^\s@,;()]+@[^\s@,;()]+")


def _door(by):
    """Which way a change came, never who made it."""
    by = str(by or "")
    if by in ("schedule", "auto-off"):
        return by
    if by.startswith("door:"):
        return "poolhound local"
    return "poolhound app" if by else ""


def _source(by):
    return {"poolhound local": "poolhound local", "schedule": "a schedule",
            "auto-off": "an automatic off"}.get(_door(by), "poolhound app")


class Runner:
    def __init__(self, execute, guard, read_sample, server=None, log=print,
                 record=None, after_ok=None, sleep=time.sleep):
        """`record(cmd, result, source)` adds the activity row; `after_ok(cmd)`
        reads the panel back and pushes it. Both the agent's, injected."""
        self.execute, self.guard, self.read_sample = execute, guard, read_sample
        self.server, self.log = server, log
        self.record, self.after_ok, self.sleep = record, after_ok, sleep
        self.lock = threading.Lock()           # one command at a time, from anywhere
        self.wake = threading.Event()          # something changed: sync
        self.stop = threading.Event()
        self.health = {"started": None, "last_tick": None, "ticks": 0, "errors": 0,
                       "last_error": None, "last_sync": None, "sync_error": None}
        os.makedirs(_data(), exist_ok=True)
        os.environ.setdefault("POOLHOUND_DATA", _data())

    # ------------------------------------------------------------ settings
    def cfg(self):
        """The settings the server last sent (circuit names, auto-off and
        max-on hours), or {} -- the built-in defaults -- on a Pi without one."""
        return _read_json(SETTINGS, {})

    # ------------------------------------------------------------ commands
    def _took(self, cmd):
        """True/False when the panel now reports what `cmd` asked; None when
        it reports nothing for that device (Aux 4 and 5 have no state)."""
        try:
            row = {k: ("" if v is None else str(v)) for k, v in self.read_sample().items()}
        except Exception:                                     # noqa: BLE001
            return False
        return S.panel_agrees(cmd, [(S.utcnow(), row)])

    def run_command(self, cmd, by=None, verify=False):
        """One validated command, here and now. The path every press takes on
        the Pi: from the server's stream, from a scene, from a schedule.
        `verify` -- for what the Pi runs by itself -- wakes the panel first and
        judges by the panel's own report afterwards (WAKE_S)."""
        with self.lock:
            if verify:
                try:
                    self.read_sample()                    # wake the panel
                except Exception:                         # noqa: BLE001
                    pass
                self.sleep(WAKE_S)
            sent = False
            try:
                result = self.execute(cmd, self.guard)
                sent = bool(result.get("ok"))
                if verify and result.get("ok") and cmd.get("action") in ("set", "heat", "setpoint"):
                    self.sleep(SETPOINT_SETTLE_S if cmd["action"] == "setpoint" else
                               SETTLE_SLOW_S if cmd.get("device") in SLOW else SETTLE_S)
                    took = self._took(cmd)
                    if took is False:
                        result = {"ok": False, "detail": result.get("detail"),
                                  "error": "AqualinkD accepted it but the panel did not "
                                           "change; the panel may have been asleep"}
            except C.Refused as e:
                result = {"ok": False, "refused": str(e)}
            except Exception as e:                            # noqa: BLE001
                result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            # Before anything slow: the watcher must find this row when the
            # panel moves, or it credits the change to the panel.
            if self.record:
                self.record(cmd, result, _source(by or cmd.get("by")))
        self.log(f"ran {C.describe(cmd)} for {_door(by or cmd.get('by')) or 'nobody'}: "
                 f"{'ok' if result.get('ok') else result.get('refused') or result.get('error')}")
        if result.get("ok") and self.after_ok:
            self.after_ok(cmd)
        # THE AUTOMATIC OFF IS RECORDED FOR WHAT WAS SENT, not only for what the
        # read-back confirmed. A heater slower than the settle time, reported
        # "did not change", may still light a minute later -- and then nothing
        # would switch it off. An off for something already off is harmless:
        # the scheduler finds it off and sends nothing.
        if sent and cmd.get("action") in ("set", "setpoint", "heat"):
            try:
                S._CFG["cfg"] = self.cfg()
                S.note_command(cmd, by or cmd.get("by") or "", cmd.get("off_after_h"), self.cfg())
                self.wake.set()
            except Exception as e:                            # noqa: BLE001
                self.log(f"  automatic off not recorded: {type(e).__name__}: {e}")
                result["schedule_error"] = f"the automatic off could not be recorded: {e}"
        return result

    def run_scene(self, sid, by):
        """A scene's steps in the safe order, stopping at the first that does
        not take. "Then off after" rides on each ON step but the pump."""
        scene = SC.get(self.cfg(), str(sid or "")[:16])
        if scene is None:
            return {"ok": False, "error": "no such scene — it may have been deleted"}
        off = scene.get("off_after_h")
        offs = SC.off_steps(scene["steps"]) if off else []
        ran = []
        for st in sorted(scene["steps"], key=SC.order_key):
            extra = off if st["device"] in offs and st["action"] != "setpoint" else None
            cmd = C.new_command(st["action"], st["device"], st["value"], by, extra)
            r = self.run_command(cmd, by, verify=True)
            if not r.get("ok"):
                why = r.get("refused") or r.get("error") or "refused"
                return {"ok": False, "ran": ran, "scene": scene["name"],
                        "error": f"stopped at {C.describe(st, self.cfg())}: {why}"}
            ran.append(C.describe(st, self.cfg()))
        return {"ok": True, "ran": ran, "scene": scene["name"], "off_after_h": off}

    def issue(self, cmd, by, cfg, off_after_h=None):
        """schedule.tick()'s issue(): the shape server.command() answers in."""
        if cmd.get("action") == "scene":
            r = self.run_scene(cmd.get("device"), by)
        else:
            r = self.run_command(C.new_command(cmd["action"], cmd["device"], cmd["value"],
                                               by, off_after_h), by, verify=True)
        return {"what": S._what(cmd), "result": r, "pending": False}, None

    # ------------------------------------------------------------ the panel
    def samples(self, now):
        """[(time, row)], oldest first: when each watched device came on, then
        the panel now. What schedule.on_since() and panel_agrees() read."""
        row = {k: ("" if v is None else str(v)) for k, v in self.read_sample().items()}
        since = _read_json(ON_SINCE, {})
        changed = False
        for dev in list(S.max_on_hours(self.cfg())) + list(C.HEATERS) + ["Spa"]:
            col = C.state_column(dev)
            if not col:
                continue
            if row.get(col) == "1":
                if dev not in since:
                    since[dev] = S._iso(now)
                    changed = True
            elif row.get(col) == "0" and dev in since:
                del since[dev]
                changed = True
        if changed:
            _write_json(ON_SINCE, since)
        out = []
        for dev, t in since.items():
            col, at = C.state_column(dev), S._parse(t)
            if col and at and at < now:
                out.append((at, {col: "1"}))
        out.sort(key=lambda p: p[0])
        return out + [(now, row)]

    # ------------------------------------------------------------ the runner
    def tick(self, now=None):
        now = now or S.utcnow()
        cfg = self.cfg()
        S._CFG["cfg"] = cfg
        before = S.runtime_fp(S.export()[1])
        did = S.tick(cfg=cfg, now=now, issue=self.issue, samples=self.samples(now),
                     authority=lambda who: (True, "operate", "operate"),
                     keep_alive=False, notify=lambda **k: False)
        if did or S.runtime_fp(S.export()[1]) != before:
            self.wake.set()
        return did

    def loop(self):
        self.health["started"] = S._iso(S.utcnow())
        while not self.stop.is_set():
            try:
                for d in self.tick():
                    self.log(f"schedule: {d[0]} {d[1]}")
            except Exception as e:                            # noqa: BLE001
                self.health["errors"] += 1
                self.health["last_error"] = f"{type(e).__name__}: {e}"[:300]
                self.log(f"schedule tick failed: {self.health['last_error']}")
            finally:
                self.health["last_tick"] = S._iso(S.utcnow())
                self.health["ticks"] += 1
            self.stop.wait(S.TICK_S)

    # ------------------------------------------------------------ the sync
    def defs(self):
        return SY.local_defs(self.cfg())

    def sync_once(self):
        """One exchange. True if both sides now hold the same."""
        if self.server is None:
            return True
        defs = self.defs()
        _, runtime = S.export()
        ask = {"defs_fp": SY.defs_fp(defs), "runtime_fp": S.runtime_fp(runtime)}
        with self.server._req("/api/agent/sync", ask, "POST", 20) as r:
            ans = json.loads(r.read() or b"{}")
        if ans.get("same"):
            return True
        with self.server._req("/api/agent/sync", {"defs": defs, "runtime": runtime},
                              "POST", 30) as r:
            ans = json.loads(r.read() or b"{}")
        merged, _ = SY.clean_incoming(ans.get("defs"), self.cfg())
        merged = SD.merge(self.defs(), merged)
        settings = ans.get("settings") if isinstance(ans.get("settings"), dict) else None
        if settings is not None and settings != self.cfg():
            _write_json(SETTINGS, settings)
        changed = SY.install(merged, self.cfg())
        if S.adopt_autooffs(ans.get("handover")):
            changed = True
            self.log("took over the server's pending automatic offs")
        if changed:
            self.log("sync: brought the server's changes here")
        return True

    def sync_loop(self):
        """Sync at start, then whenever something changed here or the server
        says it changed there. A failure is retried with backoff while
        anything is left unsent."""
        tries = 0
        self.wake.set()
        while not self.stop.is_set():
            self.wake.wait()
            if self.stop.is_set():
                return
            self.wake.clear()
            try:
                self.sync_once()
                self.health["last_sync"], self.health["sync_error"] = S._iso(S.utcnow()), None
                tries = 0
            except Exception as e:                            # noqa: BLE001
                self.health["sync_error"] = f"{type(e).__name__}: {e}"[:200]
                delay = SYNC_RETRY_S[min(tries, len(SYNC_RETRY_S) - 1)]
                tries += 1
                self.log(f"sync failed ({self.health['sync_error']}); again in {delay}s")
                if self.stop.wait(delay):
                    return
                self.wake.set()

    def start(self):
        threading.Thread(target=self.loop, daemon=True, name="poolhound-runner").start()
        if self.server is not None:
            threading.Thread(target=self.sync_loop, daemon=True, name="poolhound-sync").start()

    # ------------------------------------------------------------ poolhound local
    def view(self):
        """Everything poolhound local shows, in one answer.

        NEVER WHO. This is served to the house network unauthenticated, like
        the activity list, and a schedule made on the server carries the
        signed-in address of whoever made it. Every `by` is reduced to which
        door it came through, and an address anywhere in a history line is
        taken out. The Pi keeps the real value: the server needs it to
        re-check that person's permission.
        """
        cfg = self.cfg()
        S._CFG["cfg"] = cfg
        sch = S.listing(cfg)
        sc = SC.listing(cfg)
        for row in sch["schedules"] + sch["autooffs"] + sch["history"] + sc["scenes"]:
            if "by" in row:
                row["by"] = _door(row.get("by"))
            if isinstance(row.get("detail"), str):
                row["detail"] = _EMAIL.sub("someone", row["detail"])
        sch.pop("scheduler", None)             # the server thread's; not running here
        return {"scenes": sc, "schedules": sch, "devices": C.catalogue(cfg),
                "health": dict(self.health), "connected": self.server is not None}

    def edit(self, what, body):
        """A change from poolhound local. (answer, None) or (None, why)."""
        cfg = self.cfg()
        S._CFG["cfg"] = cfg
        if what == "scene/save":
            r, err = SC.save(body, LOCAL_DOOR, cfg)
        elif what == "scene/delete":
            r, err = SC.delete(body, LOCAL_DOOR, cfg)
        elif what == "schedule/create":
            r, err = S.create(body, LOCAL_DOOR, cfg)
        elif what == "schedule/cancel":
            r, err = S.cancel(body, LOCAL_DOOR, cfg)
        elif what == "scene/run":
            r = self.run_scene(body.get("id"), LOCAL_DOOR)
            return r, None
        else:
            return None, "no such action"
        if not err:
            self.wake.set()
        return r, err
