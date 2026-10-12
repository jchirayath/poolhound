"""The exchange that keeps the Pi's and the server's scenes and schedules in step.

docs/LOCAL-AND-SYNC.md is the design; syncdefs.py decides how two copies
merge. This is the conversation: the agent asks with fingerprints, and only
if they differ does either side send its list.

    1. agent -> {"defs_fp", "runtime_fp"}
       server -> {"same": true} when both match what it holds and last saw,
                 else {"send": true}
    2. agent -> {"defs", "runtime"}
       server merges, stores, and answers {"defs", "settings", "handover"}

EVERYTHING THAT ARRIVES IS REBUILT. The agent is authenticated, but a scene
or schedule it sends was typed into a page on the house network, and it will
be shown here and fired there. Each scene goes through scenes.clean() and
each schedule through schedule.clean_synced(); what fails is dropped and
counted, not stored.

Both halves are here, so the shape of the exchange is decided once: the
server half (exchange) and the helpers the Pi's half calls (local_defs,
install).
"""
import datetime as dt
import re

from . import commands as C, scenes as SC, schedule as S, syncdefs as SD

STATE = "sync_state.json"        # on the server: the runtime fingerprint last seen


def local_defs(cfg=None):
    """This side's scenes and schedules, with their deletion markers."""
    sch, _ = S.export()
    return {"scenes": [{k: s.get(k) for k in SD.SCENE_FIELDS if k in s} for s in SC.load(cfg)],
            "schedules": sch["schedules"],
            "deleted": SD.prune(SC.deleted(cfg) + sch["deleted"])}


def defs_fp(defs):
    return SD.fingerprint(SD.defs_only(defs))


def settings(cfg):
    """What the Pi needs to describe and to limit: circuit names and the
    household's automatic-off and max-on hours. Nothing else of config.toml
    leaves the server."""
    cfg = cfg or {}
    sched = cfg.get("schedule") or {}
    return {"circuits": {k: str(v)[:40] for k, v in (cfg.get("circuits") or {}).items()},
            "schedule": {"auto_off": sched.get("auto_off") or {},
                         "max_on": sched.get("max_on") or {}}}


def clean_incoming(defs, cfg=None):
    """(defs, dropped): every scene and schedule rebuilt, or left out."""
    out, dropped = {"scenes": [], "schedules": [], "deleted": []}, 0
    for s in (defs or {}).get("scenes") or ():
        if not isinstance(s, dict) or not re.fullmatch(r"[0-9a-zA-Z]{4,16}", str(s.get("id") or "")):
            dropped += 1
            continue
        scene, err = SC.clean(s)
        if err:
            dropped += 1
            continue
        row = {**scene, "id": s["id"], "by": SC._s(s.get("by"), 120)}
        if isinstance(s.get("saved"), (int, float)):
            row["saved"] = int(s["saved"])
        if isinstance(s.get("updated_at"), str):
            row["updated_at"] = SD._norm(s["updated_at"])
        out["scenes"].append(row)
    for r in (defs or {}).get("schedules") or ():
        row = S.clean_synced(r, cfg)
        if row is None:
            dropped += 1
            continue
        out["schedules"].append(row)
    out["deleted"] = SD.prune((defs or {}).get("deleted"))
    return out, dropped


def install(merged, cfg=None):
    """Make this side hold `merged`. True if anything changed (and was written)."""
    a = SC.replace_all(cfg, merged.get("scenes") or [],
                       [d for d in merged.get("deleted") or () if d["kind"] == "scene"])
    b = S.apply_defs(merged)
    return a or b


# ------------------------------------------------------------- the server
def _state_path(cfg):
    import os
    from . import config
    return os.path.join(config.data_dir(cfg), STATE)


def _seen_runtime_fp(cfg):
    import json
    try:
        with open(_state_path(cfg)) as f:
            return (json.load(f) or {}).get("runtime_fp")
    except (OSError, ValueError):
        return None


def _remember_runtime_fp(cfg, fp):
    import json
    from . import locking
    locking.replace_atomically(_state_path(cfg), lambda f: json.dump(
        {"runtime_fp": fp, "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}, f))


def exchange(body, cfg):
    """/api/agent/sync. (answer, None) or (None, error)."""
    if "defs" not in body:
        same = (body.get("defs_fp") == defs_fp(local_defs(cfg))
                and body.get("runtime_fp") == _seen_runtime_fp(cfg))
        return ({"same": True} if same else {"send": True}), None
    incoming, dropped = clean_incoming(body.get("defs"), cfg)
    merged = SD.merge(local_defs(cfg), incoming)
    install(merged, cfg)
    runtime = body.get("runtime") if isinstance(body.get("runtime"), dict) else {}
    offs = S.handover()                  # before mirror_runtime marks the Pi in charge
    S.mirror_runtime(runtime)
    _remember_runtime_fp(cfg, S.runtime_fp(runtime))
    return {"defs": local_defs(cfg), "settings": settings(cfg), "handover": offs,
            "dropped": dropped}, None
