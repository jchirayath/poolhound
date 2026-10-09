"""Every attempt to collect or publish, and what came of it.

WHY THIS FILE EXISTS

Four things already knew something about a collection run and none of them
could be looked at:

  * collect.sh wrote the LAST outcome per tool to a status file on the share —
    one line, overwritten every run, so a failure that fixed itself an hour
    later left no trace;
  * the cron log holds the output, on the server's host, where the container that
    renders the page cannot read it;
  * audit.csv records what a PERSON did, deliberately, and a collector is not
    a person;
  * the watchdog's state file remembers what it has already alerted about, not
    what it did.

So "did the WaterGuru pull work this week" had no answer, and the one that
mattered most — a collector running seven hours early since a migration,
fetching the previous day's reading every time — was invisible for five days
while every check reported green.

WHAT A ROW IS

One attempt. Written whether it succeeded, failed or never started, from a
`finally`, so a crash is recorded rather than being the reason nothing is. The
counts of what the run actually DID are what makes the difference between "it
ran" and "it worked": a pull that returns 200 and adds no rows is a different
event from one that adds four, and a watchdog run that raises two alarms and
delivers none is not a quiet night.

THE CSVs ARE THE RECORD, so this is one, written through locking.append_row
like every other — the header is created once and a column added to COLS
migrates the file with a backup rather than corrupting it.
"""

import datetime as dt
import os
import time

from . import config, locking

FILE = "runs.csv"

# at        when the attempt finished, local with offset
# tool      wg-collect | leslies | watch | render | sample | agent
# outcome   ok | failed | skipped | refused
# seconds   how long it took, so a slow vendor is visible before it is a timeout
# added     rows this run appended to the file it owns
# detail    one line a person can read; the error when there is one
# alerts    alarms this run RAISED
# sent      ...of which a notifier actually carried
# undelivered   ...of which none could
# by        who or what started it: cron, local, the page's refresh control
COLS = ["at", "tool", "outcome", "seconds", "added", "detail",
        "alerts", "sent", "undelivered", "by"]

OUTCOMES = ("ok", "failed", "skipped", "refused")


def path(cfg=None):
    return os.path.join(config.data_dir(cfg), FILE)


def _safe_by(by):
    """Who started a run — a name, never whatever object was handed over.

    MEASURED. `bin/backup` called `runlog.Run("backup", cfg)`, and `by` was the
    second positional parameter, so the whole config dict went into this column
    on every backup: forty characters of
    "{'waterguru': {'credentials': ''}, 'path" in runs.csv, which is an
    append-only file that bin/backup then copies into every tarball.

    What stopped that being a credential leak was the [:40] truncation landing
    before any value — luck, not a control. The rule is that credentials are
    never echoed into a log, and a column that str()s whatever it is handed
    cannot hold that line; the next caller's object may order differently.

    So: a string is a name, None is absent, and anything else is recorded as
    its TYPE. The type name says what went wrong and carries none of the
    contents. `by` is keyword-only on both record() and Run now, which makes
    that exact slip a TypeError — this is for the ones that are not.
    """
    if by is None:
        return ""
    if isinstance(by, str):
        return by[:40]
    return f"invalid:{type(by).__name__}"


def record(tool, outcome, *, seconds=None, added=None, detail="",
           alerts=0, sent=0, undelivered=0, by="cron", cfg=None):
    """Append one attempt. Never raises — a run is not failed by its bookkeeping.

    The last clause is the whole of why this is safe to call from a `finally`:
    a collector that worked must not be reported as broken because the share
    was briefly unwritable, and one that failed must not lose its own error to
    a second error raised while recording the first.
    """
    try:
        if outcome not in OUTCOMES:
            outcome = "failed"
        row = {
            "at": dt.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
            "tool": str(tool)[:40],
            "outcome": outcome,
            "seconds": "" if seconds is None else f"{float(seconds):.1f}",
            "added": "" if added is None else str(int(added)),
            "detail": str(detail or "").replace("\n", " ")[:300],
            "alerts": str(int(alerts or 0)),
            "sent": str(int(sent or 0)),
            "undelivered": str(int(undelivered or 0)),
            "by": _safe_by(by),
        }
        d = config.data_dir(cfg)
        locking.append_row(d, "runs", os.path.join(d, FILE), COLS, row)
    except Exception:                                   # noqa: BLE001
        pass


def started_by():
    """cron, a person at a terminal, or whatever started this deliberately.

    The page's refresh control sets POOLHOUND_RUN_BY so a manual pull is
    distinguishable from the scheduled one — which matters, because a manual
    pull spends the same daily API call.
    """
    import sys
    from . import access
    if os.environ.get("POOLHOUND_RUN_BY"):
        return os.environ["POOLHOUND_RUN_BY"]
    # NOT "cron" BY GUESS. The first version answered "cron" whenever stdin was
    # not a terminal, which is true of any script, any CI step and any manual
    # run from a non-interactive shell — so runs.csv claimed cron had done
    # things nobody had scheduled. The crontab is the one place that KNOWS, and
    # it says so by exporting POOLHOUND_RUN_BY; everything else is "manual",
    # which is a smaller claim and a true one.
    return access.CONSOLE if sys.stdin.isatty() else "manual"


def row_count(name, cfg=None):
    """How many data rows a CSV holds right now, header excluded.

    Read raw rather than through the loaders: this is a measurement of the
    FILE, and corrections or the sentinel rule would make two runs that added
    the same rows report different numbers.
    """
    p = os.path.join(config.data_dir(cfg), name)
    if not os.path.exists(p):
        return 0
    try:
        with open(p, newline="", encoding="utf-8") as f:
            return max(sum(1 for _ in f) - 1, 0)
    except OSError:
        return 0


class Run:
    """Context manager that records the attempt whichever way it ends.

    Used as:

        with runlog.Run("leslies", by="cron") as r:
            n = pull()
            r.added = n
            r.detail = f"{n} new result(s)"

    An exception propagates — the caller still fails, and the CLI still exits
    non-zero — but the attempt is on the record with the error in `detail`,
    which is the half that was missing when bin/watch died every thirty minutes
    for weeks with nothing anywhere saying so.
    """

    def __init__(self, tool, *, by="cron", cfg=None):
        self.tool, self.by, self.cfg = tool, by, cfg
        self.added = None
        self.detail = ""
        self.outcome = "ok"
        self.alerts = self.sent = self.undelivered = 0
        self._t0 = None

    def __enter__(self):
        self._t0 = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.outcome = "failed"
            if not self.detail:
                self.detail = f"{exc_type.__name__}: {exc}"
        record(self.tool, self.outcome,
               seconds=time.monotonic() - self._t0,
               added=self.added, detail=self.detail,
               alerts=self.alerts, sent=self.sent,
               undelivered=self.undelivered, by=self.by, cfg=self.cfg)
        return False                    # never swallow


def rows(cfg=None):
    """Every recorded attempt, newest first.

    FILE ORDER BREAKS THE TIE, and it has to. Timestamps here are whole
    seconds, and several collectors finish inside one — so sorting on `at`
    alone left "the latest attempt" undefined, and the page's banner said
    "none reported a failure" while the table beneath it listed two. A summary
    that disagrees with the rows it summarises is worse than no summary.

    This file is append-only, so a later line IS a later run: sorting by
    (timestamp, line number) is a total order, and the newest is the newest.

    Read through render's loader so corrections and the sentinel rule apply
    here as everywhere else.
    """
    from . import render
    out = render.rows(FILE)
    return [r for _, r in sorted(
        ((i, r) for i, r in enumerate(out)),
        key=lambda pair: (pair[1].get("at", ""), pair[0]), reverse=True)]
