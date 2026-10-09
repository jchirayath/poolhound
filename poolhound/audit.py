"""Who did what, append-only, on the share.

WHAT THIS IS FOR

Two questions this product could not answer: "who started the spa heater at
02:14?" and "where did that dose go?". The first had no record at all — the
command log was a 200-entry deque in one process, so a restart, or simply the
six-hourly cron render happening in a different process, answered "nothing
issued yet this session" about a pool that had been switched all afternoon. The
second was worse than absent: deleting a logged dose rewrote the file without
it, leaving no row, no tombstone and nothing in the log, so the record the whole
fitting premise rests on could be silently rewritten by either person in the
house.

WHY IT IS SEPARATE FROM THE COMMAND QUEUE

The queue being in memory is correct and deliberate — commands expire in five
minutes and persisting them would let a stale instruction survive a restart.
That is a statement about what is PENDING. What was ISSUED is a different
object with a different lifetime, and conflating the two is why the audit went
missing: it was living inside the thing that is supposed to be forgotten.

WHY APPEND-ONLY

Same reason as corrections.csv. A log that can be rewritten is not a log, and
the whole value here is answering a question asked months later by somebody who
was not there.

WHAT IS NEVER WRITTEN HERE

Secrets. save_credential records that a credential for a service was set or
forgotten, never the credential. Nothing in this file should ever be unsafe to
read aloud.
"""
import csv, datetime as dt, os, sys

from . import access, config, locking

FILE = "audit.csv"
# `id` ties a command to its acknowledgement. Both were written as separate
# rows with nothing in common but their timestamps, so the control log rendered
# the value asked for in the Result column and, on a line of its own, a bare
# 36-character UUID where the command should be — and after a restart, which is
# the state this durable half exists for, that was the whole table.
#
# Added rather than reusing `object`: object is what the row is ABOUT (the
# device, the service, the file), and overloading it to mean "the id, except on
# the rows where it means the device" is the kind of double meaning this
# project keeps paying for. locking.migrate_columns adds the column with a
# timestamped backup.
COLS = ["at", "by", "action", "object", "detail", "id"]

def path(cfg=None):
    return os.path.join(config.data_dir(cfg), FILE)

def record(action, obj="", detail="", by=None, cfg=None, id=""):
    """Append one entry. Never rewrites, never removes.

    Failures here are swallowed on purpose: an audit write that cannot happen
    must not take down the operation it is describing. Losing the entry is bad;
    refusing the dose because the entry could not be written is worse, and the
    thing it is auditing has already happened by the time this is called.

    But swallowed is not the same as silent. It returned False and every one of
    the six call sites ignores it, so a share that had gone read-only would
    produce an audit trail with holes in it and nothing anywhere would say so --
    and an audit you cannot tell is incomplete is worse than none, because it
    gets believed. The failure goes to stderr now, which is cron mail on the server
    and the container log, the same place a failed render reports.
    """
    try:
        p = path(cfg)
        row = {"at": dt.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
               # access.py names the console identity; this used to retype it.
               "by": str(by or access.CONSOLE)[:120],
               "id": str(id or "")[:64],
               "action": str(action)[:60],
               "object": str(obj)[:200],
               "detail": str(detail)[:400]}
        # LOCK *AND* MIGRATE. This took the lock and then did a bare append --
        # the one writer of a shared CSV that did half of locking.py's pairing,
        # which is exactly the split that module exists to end. Measured: add a
        # column to COLS and the next two rows go out as 5 fields then 6 under a
        # 5-field header, record() returns True both times and prints nothing,
        # and the file lands in the state migrate_columns REFUSES to repair --
        # so the corruption is permanent and needs a person. audit.csv is the
        # durable answer to "who switched what", written from six call sites in
        # two processes.
        locking.append_row(os.path.dirname(p) or ".", "audit", p, COLS, row)
        return True
    except Exception as e:
        print(f"audit: could NOT record {action!r} by {by!r}: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return False

def load(cfg=None, limit=None):
    """Newest first, which is the order anybody asking wants them in."""
    p = path(cfg)
    if not os.path.exists(p):
        return []
    with open(p, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r.get("at")]
    rows.reverse()
    return rows[:limit] if limit else rows
