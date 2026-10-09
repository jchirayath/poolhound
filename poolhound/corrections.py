"""Overrides for readings that are wrong, applied every time they are read.

WHY NOT JUST DELETE THE ROW

Because it comes back. Both labs return their WHOLE history on every pull and
the collectors dedup on the measurement timestamp, so a row deleted from the CSV
is re-added by the next cron run — the fix would appear to work and quietly undo
itself overnight.

WHY NOT EDIT THE ROW IN PLACE

Because the CSV is a record of what the lab said, and a record you can silently
rewrite is not one. A bad reading is a real event: the sample was fouled, the
pod misread, somebody tested the spa instead of the pool. Keeping the original
and layering a correction over it means the page can show the corrected figure
while the raw response in data/raw/ still says what actually arrived.

So this is an append-only list of corrections keyed by (source, measured), and
the loaders apply it on the way past. Two kinds:

    drop   ignore this reading entirely
    set    replace one field's value

A correction carries who made it and why, because in six months "why is the
alkalinity on 21 August different from the lab report?" is a question somebody
will ask, and the answer should be on the page rather than in a memory.
"""

import csv, datetime as dt, os

from . import config
from . import locking

FILE = "corrections.csv"
COLS = ["at", "by", "source", "measured", "field", "action", "value", "note"]
SOURCES = {"lab": "WaterGuru lab", "leslies": "Leslie's", "readings": "WaterGuru pod",
           "manual": "Entered by hand"}

def path(cfg=None):
    return os.path.join(config.data_dir(cfg), FILE)

def load(cfg=None):
    p = path(cfg)
    if not os.path.exists(p):
        return []
    with open(p, newline="") as f:
        return [r for r in csv.DictReader(f) if r.get("source")]

# Which file each source is stored in. corrections.py cannot import render --
# render imports this -- and the pairing is one line either way, so it is written
# out rather than reached for. render.CORRECTABLE is the same pairing inverted;
# if a fifth source is ever added, both want it.
SOURCE_FILE = {"lab": "lab.csv", "leslies": "leslies.csv",
               "readings": "readings.csv", "manual": "manual.csv"}

def targets(source, cfg=None):
    """Every `measured` value this source actually holds.

    Used to refuse a correction that names a row nothing has. Read raw, NOT
    through render.rows(): that applies corrections, so a row already dropped
    would look absent and a second correction about it -- a `keep` to undo the
    drop -- would be refused as pointing at nothing. The question here is
    whether the reading was ever collected, not whether it currently survives.
    """
    f = SOURCE_FILE.get(source)
    if not f:
        return set()
    p = os.path.join(config.data_dir(cfg), f)
    if not os.path.exists(p):
        return set()
    with open(p, newline="") as fh:
        return {(r.get("measured") or "").strip()
                for r in csv.DictReader(fh) if (r.get("measured") or "").strip()}

def columns(source, cfg=None):
    """Every column this source's file actually has.

    The sibling of targets(). Read raw for the same reason: the question is what
    the file is shaped like, not what survives correction.
    """
    f = SOURCE_FILE.get(source)
    if not f:
        return []
    p = os.path.join(config.data_dir(cfg), f)
    if not os.path.exists(p):
        return []
    with open(p, newline="") as fh:
        try:
            return [c.strip() for c in next(csv.reader(fh))]
        except StopIteration:
            return []

def record(source, measured, action, field="", value="", note="", by="", cfg=None):
    """Append one correction. Never rewrites, never removes.

    Superseding is how a mistake here is undone: a later correction for the same
    (source, measured, field) wins, and `action="keep"` cancels an earlier drop.
    That keeps the history of what was decided, which is the point of the file.
    """
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")
    if action not in ("drop", "set", "keep"):
        raise ValueError(f"unknown action {action!r}")
    if action == "set" and not field:
        raise ValueError("a field is needed to set a value")
    # A correction naming a row that does not exist used to be written, reported
    # as recorded, and listed back on the page -- while changing nothing, ever.
    # It is the owner's only remedy for a bad reading, so a typo in the timestamp
    # silently costing them the fix is the worst possible failure for it.
    measured = (measured or "").strip()
    have = targets(source, cfg)
    if not have:
        # AN UNREADABLE SOURCE DISABLED THE CHECK ENTIRELY. The guard was
        # `if have and measured not in have`, so an empty set -- a missing file,
        # or one whose `measured` column cannot be read, which is what a
        # spreadsheet's UTF-8 BOM produces -- accepted every correction and
        # recorded it permanently while matching nothing.
        raise ValueError(
            f"no {SOURCES[source]} readings can be read, so there is nothing to "
            f"correct. Check the file exists and its header is intact.")
    if measured not in have:
        raise ValueError(
            f"no {SOURCES[source]} reading is recorded at {measured!r}. "
            f"The timestamp has to match the row exactly, as it appears in the "
            f"table.")
    # AND THE SIBLING CHECK ON THE FIELD, which was never written. The timestamp
    # got this treatment because "a correction naming a row that does not exist
    # used to be written, reported as recorded, and listed back on the page --
    # while changing nothing, ever". A correction naming a COLUMN that does not
    # exist did exactly the same thing, and the form invites it: "Which
    # measurement" is free text with no list of valid names, and the page calls
    # the measurement "Alkalinity" everywhere while the column is `ta`.
    #
    # Worse than a no-op: apply() sets _corrected from the field name, so the
    # page MARKED the reading as corrected while still showing the old value.
    if action == "set":
        cols = columns(source, cfg)
        if cols and field not in cols:
            usable = [c for c in cols if c not in ("measured", "source")]
            raise ValueError(
                f"{SOURCES[source]} has no measurement called {field!r}. "
                f"Use one of: {', '.join(sorted(usable))}.")
    # Lock, migrate, append -- the one call every writer of a shared CSV takes.
    # This was a bare append. corrections.csv is append-only and grows for the
    # life of the pool: it is the permanent record of which readings the owner
    # judged wrong and why, and the loaders apply it on EVERY render. A dropped
    # row here silently resurrects a bad lab result on the public page.
    p = path(cfg)
    locking.append_row(config.data_dir(cfg), "corrections", p, COLS,
                       {"at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                        "by": (by or "")[:120], "source": source,
                        "measured": measured, "field": field, "action": action,
                        "value": str(value)[:60], "note": (note or "")[:300]})
    return True

def for_source(source, cfg=None):
    """The corrections that still stand, as {measured: {...}}.

    Later entries win, so the file is walked in order and each one overwrites
    what came before it for the same key.
    """
    out = {}
    for c in load(cfg):
        if c.get("source") != source:
            continue
        key = c.get("measured", "")
        slot = out.setdefault(key, {"drop": False, "fields": {}, "notes": []})
        if c["action"] == "drop":
            slot["drop"] = True
        elif c["action"] == "keep":
            slot["drop"] = False
        elif c["action"] == "set":
            slot["fields"][c["field"]] = c["value"]
        if c.get("note"):
            slot["notes"].append(c["note"])
    return out

def apply(rows, source, cfg=None):
    """Rows with corrections applied. The originals are not touched.

    A corrected row is MARKED, not silently changed: `_corrected` lists the
    fields that were overridden so the page can say so. A figure that quietly
    disagrees with the lab report somebody is holding is worse than no
    correction at all.
    """
    corr = for_source(source, cfg)
    if not corr:
        return rows
    out = []
    for r in rows:
        c = corr.get(r.get("measured", ""))
        if not c:
            out.append(r); continue
        if c["drop"]:
            continue
        if c["fields"]:
            r = dict(r, **c["fields"])
            r["_corrected"] = ", ".join(sorted(c["fields"]))
            if c["notes"]:
                r["_correction_note"] = "; ".join(c["notes"])
        out.append(r)
    return out
