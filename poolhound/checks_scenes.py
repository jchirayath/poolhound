"""Scenes: several controls in one tap, and every way that could go wrong.

A scene is presses made for somebody, so each case is a press made wrongly:
in an order that starts a heater before its pump, carrying on after a step
was refused, switching the pump off when a spa session ends, or saved in a
shape the agent would refuse at the moment it matters.
"""
import os
import shutil
import tempfile

from .selftest import check


def _box():
    from . import config, render
    d = tempfile.mkdtemp(prefix="poolhound-scenes-")
    prev = (os.environ.get("POOLHOUND_DATA"), render.DATA)
    os.environ["POOLHOUND_DATA"] = d
    render.DATA = d
    shutil.copy(os.path.join(config.root(), "config", "config.example.toml"),
                os.path.join(d, "config.toml"))
    return d, prev


def _unbox(d, prev):
    from . import render
    render.DATA = prev[1]
    if prev[0] is None:
        os.environ.pop("POOLHOUND_DATA", None)
    else:
        os.environ["POOLHOUND_DATA"] = prev[0]
    shutil.rmtree(d, ignore_errors=True)


def t_a_scene_runs_in_the_safe_order_whatever_order_it_was_saved_in():
    print("\n  scenes — pump first, temperature before heater, offs before ons")
    from . import scenes as SC
    scene, err = SC.clean({"name": "Spa", "steps": [
        {"action": "heat", "device": "Spa_Heater", "value": 1},
        {"action": "setpoint", "device": "Spa_Heater", "value": 102},
        {"action": "set", "device": "Spa", "value": 1},
        {"action": "set", "device": "Aux_1", "value": 0},
        {"action": "set", "device": "Filter_Pump", "value": 1}]})
    check("saved heater-first, it runs pump first",
          [(s["action"], s["device"]) for s in scene["steps"]],
          [("set", "Aux_1"), ("set", "Filter_Pump"), ("set", "Spa"),
           ("setpoint", "Spa_Heater"), ("heat", "Spa_Heater")])
    down, _ = SC.clean({"name": "Down", "steps": [
        {"action": "set", "device": "Filter_Pump", "value": 0},
        {"action": "heat", "device": "Pool_Heater", "value": 0},
        {"action": "set", "device": "Aux_2", "value": 0}]})
    check("switching off: the heater first, the pump last",
          [s["device"] for s in down["steps"]], ["Pool_Heater", "Aux_2", "Filter_Pump"])


def t_a_scene_is_refused_when_it_is_saved_not_when_it_runs():
    print("\n  scenes — what the agent would refuse is refused at the form")
    from . import scenes as SC
    def why(body):
        return SC.clean(body)[1] or ""
    check("a heater that is not one", "not a heater" in why(
        {"name": "x", "steps": [{"action": "heat", "device": "Spa", "value": 1}]}), True)
    check("a temperature out of range", "accepts" in why(
        {"name": "x", "steps": [{"action": "setpoint", "device": "Spa_Heater", "value": 140}]}), True)
    check("the same control twice", "twice" in why(
        {"name": "x", "steps": [{"action": "set", "device": "Spa", "value": 1},
                                {"action": "set", "device": "Spa", "value": 0}]}), True)
    check("a heater's temperature and its switch are not twice",
          why({"name": "x", "steps": [{"action": "setpoint", "device": "Spa_Heater", "value": 100},
                                      {"action": "heat", "device": "Spa_Heater", "value": 1}]}), "")
    check("an action that is not a press", "each step" in why(
        {"name": "x", "steps": [{"action": "all_off"}]}), True)
    check("no name", "name" in why({"name": " ", "steps": [{"action": "set", "device": "Spa", "value": 1}]}), True)
    check("more steps than a scene holds", "limit" in why(
        {"name": "x", "steps": [{"action": "set", "device": "Spa", "value": 1}] * (SC.STEPS_MAX + 1)}), True)


def t_then_off_after_leaves_the_pump_to_the_panel():
    print("\n  scenes — a spa session ending does not cut short the day's filtering")
    from . import scenes as SC
    spa = SC.templates()[0]
    check("the spa and its heater are switched off afterwards, the pump is not",
          SC.off_steps(SC.clean(spa)[0]["steps"]), ["Spa", "Spa_Heater"])
    check("so a scene of only the pump has nothing to switch off afterwards",
          "nothing to switch off" in (SC.clean({"name": "p", "off_after_h": 2, "steps": [
              {"action": "set", "device": "Filter_Pump", "value": 1}]})[1] or ""), True)


def t_a_scene_stops_at_the_first_step_refused():
    print("\n  scenes — a refused pump means the heater is never asked")
    from . import config, scenes as SC, server
    d, prev = _box()
    real = server.command
    pressed = []
    try:
        cfg = config.load()
        row, _ = SC.save(SC.templates()[0], "someone@example.com", cfg)

        def fake(data, cfg, who):
            if data.get("action") == "scene":
                return real(data, cfg, who)
            pressed.append(data["device"])
            if data["device"] == "Spa":
                return {"what": "Spa on", "result": {"ok": False, "refused": "cooldown"}}, None
            return {"what": data["device"], "result": {"ok": True}, "pending": False}, None
        server.command = fake
        r, err = server.run_scene({"action": "scene", "device": row["id"]}, cfg, "someone@example.com")
        check("it pressed the pump, then the spa, and stopped there", pressed, ["Filter_Pump", "Spa"])
        check("and says where, and why", (r["result"]["ok"], "cooldown" in r["result"]["error"]), (False, True))
        pressed.clear()
        server.command = lambda data, cfg, who: (real(data, cfg, who) if data.get("action") == "scene"
                                                else (pressed.append(data["device"]) or
                                                      ({"what": data["device"], "result": {"ok": True}}, None)))
        r, _ = server.run_scene({"action": "scene", "device": row["id"]}, cfg, "someone@example.com")
        check("all the way through when nothing is refused",
              pressed, ["Filter_Pump", "Spa", "Spa_Heater", "Spa_Heater"])
        check("an unknown scene is refused, not run",
              server.run_scene({"device": "nope"}, cfg, "x")[1] is not None, True)
    finally:
        server.command = real
        _unbox(d, prev)


def t_a_scene_can_be_scheduled_and_is_named_by_its_name():
    print("\n  scenes — scheduled like any command, and said in words")
    from . import config, scenes as SC, schedule as S
    d, prev = _box()
    try:
        cfg = config.load()
        row, _ = SC.save(SC.templates()[3], "someone@example.com", cfg)
        sch, err = S.create({"action": "scene", "device": row["id"], "repeat": "daily",
                             "time": "19:00"}, "someone@example.com", cfg, data=d)
        check("a daily scene is scheduled", (err, S._what(sch)),
              (None, "scene Filter, sheer descent and lights"))
        check("a scene carries its own off-after, so a schedule's is refused",
              S.create({"action": "scene", "device": row["id"], "repeat": "daily", "time": "19:00",
                        "off_after_h": 2}, "x", cfg, data=d)[1] is not None, True)
        SC.delete({"id": row["id"]}, "x", cfg)
        check("deleted, the schedule says so rather than guessing",
              S._what(sch), "a scene that has since been deleted")
    finally:
        _unbox(d, prev)
