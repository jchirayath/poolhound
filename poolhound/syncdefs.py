"""What the Pi and the server keep in step, and how two copies are merged.

The scenes and the schedules people write are kept on both machines: the Pi
runs them, the server shows them, and either page can change them. See
docs/LOCAL-AND-SYNC.md. This module is the ONE place that decides how two
copies become one, so the agent and the server cannot merge differently.

LAST UPDATE WINS, PER ITEM. Every scene and schedule carries `updated_at`, set
when somebody saves it. A deletion is an update too: it leaves a marker,
{"id", "kind", "updated_at"} in `deleted`, so it travels like an edit and
cannot be undone by the other side's older copy. Markers are kept for
TOMBSTONE_DAYS, longer than any outage a sync has to bridge.

ONE FIELD IS NOT LAST-UPDATE-WINS: a schedule's `last_key`, the local date
(or "once") it last ran for. It is set by FIRING, not by editing, and the
later date wins whichever copy holds it. Otherwise an edit on the server,
made before the Pi's 19:00 run reached it, would hand back an older key and
the 19:00 would run twice.

STDLIB ONLY: the agent imports it.
"""
import datetime as dt
import hashlib
import json

TOMBSTONE_DAYS = 90
# "autooff": an automatic off cancelled on the page that is not running it.
# The Pi owns the automatic offs; a marker is how the server says "not this one".
KINDS = ("scene", "schedule", "autooff")

# A schedule's own fields: what a person wrote. Anything else on the row is
# the runner's (last_key, handled separately) or the page's.
SCHEDULE_FIELDS = ("id", "action", "device", "value", "repeat", "time", "at",
                   "off_after_h", "by", "created", "updated_at")
SCENE_FIELDS = ("id", "name", "steps", "off_after_h", "by", "saved", "updated_at")


def stamp(now=None):
    """UTC, to the millisecond: two saves in one second must still order."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds")


def _when(row):
    """A row's update time as a comparable string. A row from before sync
    existed has none; its creation time stands in, and failing that the
    epoch, so any real edit beats it."""
    for k in ("updated_at", "created"):
        v = row.get(k)
        if isinstance(v, str) and v:
            return _norm(v)
    s = row.get("saved")
    if isinstance(s, (int, float)):
        return stamp(dt.datetime.fromtimestamp(s, dt.timezone.utc))
    return "1970-01-01T00:00:00.000+00:00"


def _norm(v):
    """Any of our ISO stamps, in the one form that sorts correctly."""
    try:
        t = dt.datetime.fromisoformat(str(v))
    except ValueError:
        return "1970-01-01T00:00:00.000+00:00"
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return stamp(t)


def tombstone(kind, rid, now=None):
    return {"id": str(rid), "kind": kind, "updated_at": stamp(now)}


def prune(deleted, now=None):
    """Markers younger than TOMBSTONE_DAYS, one per (kind, id), the newest."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cut = stamp(now - dt.timedelta(days=TOMBSTONE_DAYS))
    best = {}
    for d in deleted or ():
        if not isinstance(d, dict) or d.get("kind") not in KINDS or not d.get("id"):
            continue
        d = {"id": str(d["id"])[:40], "kind": d["kind"], "updated_at": _norm(d.get("updated_at"))}
        if d["updated_at"] < cut:
            continue
        k = (d["kind"], d["id"])
        if k not in best or best[k]["updated_at"] < d["updated_at"]:
            best[k] = d
    return sorted(best.values(), key=lambda d: (d["kind"], d["id"]))


def _later_key(a, b):
    """The later of two last_key values: an ISO date, "once", or None."""
    if a == "once" or b == "once":
        return "once"
    return max((k for k in (a, b) if k), default=None)


def merge(mine, theirs, now=None):
    """Two copies of {"scenes", "schedules", "deleted"} -> one.

    Per item, the copy with the later `updated_at` wins, a deletion marker
    included. On a tie the deletion wins, then the copy whose JSON sorts last
    -- arbitrary, but the same answer on both machines, which is what matters.
    """
    out = {"scenes": [], "schedules": [], "deleted": []}
    for kind, key in (("scene", "scenes"), ("schedule", "schedules")):
        cand = {}
        for side in (mine, theirs):
            for row in (side or {}).get(key) or ():
                if isinstance(row, dict) and row.get("id"):
                    cand.setdefault(str(row["id"]), []).append((_when(row), 0, row))
            for d in (side or {}).get("deleted") or ():
                if isinstance(d, dict) and d.get("kind") == kind and d.get("id"):
                    cand.setdefault(str(d["id"]), []).append((_norm(d.get("updated_at")), 1, None))
        for rid, options in sorted(cand.items()):
            options.sort(key=lambda o: (o[0], o[1], json.dumps(o[2], sort_keys=True)))
            when, is_del, row = options[-1]
            if is_del:
                out["deleted"].append({"id": rid, "kind": kind, "updated_at": when})
                continue
            row = dict(row)
            if kind == "schedule":
                keys = [o[2].get("last_key") for o in options if o[2]]
                row["last_key"] = None
                for k in keys:
                    row["last_key"] = _later_key(row["last_key"], k)
            out[key].append(row)
    out["deleted"] += [d for side in (mine, theirs) for d in (side or {}).get("deleted") or ()
                       if isinstance(d, dict) and d.get("kind") == "autooff"]
    out["deleted"] = prune(out["deleted"], now)
    return out


def defs_only(doc):
    """The part that is synced, in a stable order: what a fingerprint covers."""
    return {"scenes": sorted((dict(r) for r in doc.get("scenes") or ()), key=lambda r: str(r.get("id"))),
            "schedules": sorted(({k: r.get(k) for k in SCHEDULE_FIELDS + ("last_key",) if k in r}
                                 for r in doc.get("schedules") or ()), key=lambda r: str(r.get("id"))),
            "deleted": prune(doc.get("deleted"))}


def fingerprint(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]
