"""Scenes: a named set of ordinary commands, run in one go.

"Evening: the filter pump, the sheer descent and the pool light on." "Spa:
pump, spa, heater to 102, and all of it off again in two hours." A scene is
the buttons a person would press, in an order that is safe, pressed for them.

ONE COMMAND PATH, STILL
Running a scene is /api/control with action "scene", and server.command()
expands it into its steps and issues each one through itself -- the same
validation, rate limit, audit row and Pi Guard as a single press, recorded as
whoever ran it. The agent never sees a scene. Nothing here can make the
panel do anything a person at the Control tab could not.

ORDER IS DECIDED HERE, NOT BY WHOEVER WROTE THE SCENE
Steps are run in a fixed order whatever order they were saved in: heaters off,
then other things off, the pump off last; then the pump on first, other
things on, temperatures set, heaters on last. So a heater is never asked to
start before the pump that has to be circulating for it, and a pump is never
stopped under a burner. And a scene STOPS at the first step that is refused
or fails: the heater is not switched on after its pump was refused.

"THEN OFF AFTER" LEAVES THE PUMP TO THE PANEL
A scene may carry an off-after. It arranges an ordinary automatic off for
every step that switched something ON -- except the filter pump, whose hours
belong to the panel's own program: a spa session ending must not cut short
the day's filtering.

Stored in $POOLHOUND_DATA/scenes.json, replaced atomically under its lock. A
scene is a decision somebody made, so it lives where the schedules do.
"""
import json, os, re, time, uuid

from . import commands as C

FILE = "scenes.json"
SCENES_MAX = 20
STEPS_MAX = 10
NAME_MAX = 40
PUMP = "Filter_Pump"
ACTIONS = ("set", "heat", "setpoint")


def _s(v, n):
    return re.sub(r"[^\x20-\x7e]", "", str(v or "")).strip()[:n]


def _on(step):
    """Does this step start something? A temperature counts with the ons:
    it is set on the way to a heater being switched on."""
    return step["action"] == "setpoint" or step.get("value") in (1, "1", True)


def order_key(step):
    """Where a step runs. Offs before ons; within each, the safe order."""
    a, d = step["action"], step["device"]
    if not _on(step):
        return (0, 0 if a == "heat" else 2 if d == PUMP else 1)
    return (1, 0 if d == PUMP else 2 if a == "setpoint" else 3 if a == "heat" else 1)


def off_steps(steps):
    """The devices "then off after" switches off: every ON but the pump."""
    out = []
    for st in steps:
        if (_on(st) and st["action"] != "setpoint" and st["device"] != PUMP
                and C.off_command(st["device"]) is not None):
            out.append(st["device"])
    return out


def clean(body, now=None):
    """A scene from a person's form, validated and ordered, or (None, why)."""
    now = now or time.time()
    name = _s(body.get("name"), NAME_MAX)
    if not name:
        return None, "a scene needs a name"
    raw = body.get("steps")
    if not isinstance(raw, list) or not raw:
        return None, "a scene needs at least one step"
    if len(raw) > STEPS_MAX:
        return None, f"{STEPS_MAX} steps is the limit for one scene"
    steps, seen = [], set()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now))
    for st in raw:
        if not isinstance(st, dict) or st.get("action") not in ACTIONS:
            return None, "each step switches something, switches a heater, or sets a temperature"
        cmd = {"action": st["action"], "device": st.get("device"), "value": st.get("value")}
        try:
            v = C.validate({"id": "0" * 16, "issued": stamp, **cmd})
        except C.Refused as e:
            return None, f"{C.label_for(cmd['device'])}: {e}"
        key = (v["device"], "value" if v["action"] == "setpoint" else "state")
        if key in seen:
            return None, f"{C.label_for(v['device'])} appears twice"
        seen.add(key)
        steps.append({"action": v["action"], "device": v["device"], "value": v["value"]})
    steps.sort(key=order_key)
    off = body.get("off_after_h")
    if off in (None, "", 0, "0"):
        off = None
    else:
        from . import schedule as S
        try:
            off = float(off)
        except (TypeError, ValueError):
            return None, "off after must be a number of hours"
        if off not in S.OFF_AFTER_CHOICES:
            return None, ("off after must be one of "
                          + ", ".join(f"{c:g}" for c in S.OFF_AFTER_CHOICES) + " hours")
        if not off_steps(steps):
            return None, ("nothing in this scene is switched on apart from the pump, "
                          "so there is nothing to switch off afterwards")
    return {"name": name, "steps": steps, "off_after_h": off}, None


def describe(scene, cfg=None):
    """The scene in words, in the order it runs."""
    return " → ".join(C.describe(st, cfg) for st in scene["steps"])


# ------------------------------------------------------------------ storage
def path(cfg):
    from . import config
    return os.path.join(config.data_dir(cfg), FILE)


def load(cfg):
    """Every scene, or [] -- never raises, so a bad file costs the scenes and
    not the Control tab."""
    try:
        with open(path(cfg)) as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return []
    sc = rec.get("scenes") if isinstance(rec, dict) else None
    return [s for s in (sc or []) if isinstance(s, dict) and s.get("id")
            and isinstance(s.get("steps"), list)]


def _save(cfg, scenes):
    from . import locking
    locking.replace_atomically(path(cfg), lambda f: json.dump(
        {"version": 1, "scenes": scenes}, f, indent=1))


def get(cfg, sid):
    return next((s for s in load(cfg) if s["id"] == sid), None)


def save(body, who, cfg, now=None):
    """Create, or replace by id. (scene, None) or (None, why)."""
    from . import config, locking
    scene, err = clean(body, now)
    if err:
        return None, err
    sid = _s(body.get("id"), 16)
    with locking.exclusive(config.data_dir(cfg), "scenes"):
        scenes = load(cfg)
        old = next((s for s in scenes if s["id"] == sid), None) if sid else None
        if sid and old is None:
            return None, "no such scene — it may have been deleted"
        if old is None and len(scenes) >= SCENES_MAX:
            return None, f"{SCENES_MAX} scenes is the limit; delete one first"
        if any(s["name"].lower() == scene["name"].lower() and s is not old for s in scenes):
            return None, f"there is already a scene called {scene['name']}"
        row = {**(old or {}), **scene, "id": sid or uuid.uuid4().hex[:10],
               "by": _s(who, 120), "saved": int(now or time.time())}
        scenes = [row if s is old else s for s in scenes] if old else scenes + [row]
        _save(cfg, scenes)
    return row, None


def delete(body, who, cfg):
    from . import config, locking
    sid = _s(body.get("id"), 16)
    with locking.exclusive(config.data_dir(cfg), "scenes"):
        scenes = load(cfg)
        left = [s for s in scenes if s["id"] != sid]
        if len(left) == len(scenes):
            return None, "no such scene — it may already have been deleted"
        _save(cfg, left)
    return {"id": sid}, None


def listing(cfg):
    """What /api/scenes returns: every scene in words, and the templates."""
    return {"scenes": [{**s, "what": describe(s, cfg),
                        "then_off": [C.label_for(d, cfg) for d in off_steps(s["steps"])]}
                       for s in load(cfg)],
            "templates": templates(), "steps_max": STEPS_MAX, "pump": PUMP}


def templates():
    """Starting points the form offers. Nothing is saved until a person does."""
    sp, pl = C.SETPOINTS["Spa_Heater"], C.SETPOINTS["Pool_Heater"]
    hot_spa = max(sp["min"], min(102, sp["max"]))
    warm_pool = max(pl["min"], min(84, pl["max"]))
    return [
        {"name": "Spa session", "off_after_h": 2, "steps": [
            {"action": "set", "device": PUMP, "value": 1},
            {"action": "set", "device": "Spa", "value": 1},
            {"action": "setpoint", "device": "Spa_Heater", "value": hot_spa},
            {"action": "heat", "device": "Spa_Heater", "value": 1}]},
        {"name": "Pool heat", "off_after_h": 4, "steps": [
            {"action": "set", "device": PUMP, "value": 1},
            {"action": "setpoint", "device": "Pool_Heater", "value": warm_pool},
            {"action": "heat", "device": "Pool_Heater", "value": 1}]},
        {"name": "Pool and lights, evening", "off_after_h": 3, "steps": [
            {"action": "set", "device": PUMP, "value": 1},
            {"action": "set", "device": "Aux_1", "value": 1}]},
        {"name": "Filter, sheer descent and lights", "off_after_h": None, "steps": [
            {"action": "set", "device": PUMP, "value": 1},
            {"action": "set", "device": "Aux_2", "value": 1},
            {"action": "set", "device": "Aux_1", "value": 1}]},
    ]
