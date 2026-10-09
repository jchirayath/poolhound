"""Renders that only break on data the household happens not to have yet."""
import csv
import os
import shutil
import subprocess
import sys
import tempfile

from .selftest import check
from . import config


def t_a_hand_entered_reading_does_not_break_the_render():
    """bin/render must survive every file a person can legitimately create.

    The public-prose assertion — the public build must not tell its reader to
    use a screen that is not in their file — is correct and catches real
    leaks. The "Entered by hand" table's note named the Settings tab
    unconditionally, and that table is in BOTH builds, so the assertion
    refused the whole render the moment manual.csv held one row. Recording a
    single test-kit result through the UI broke bin/render, and with it cron's
    six-hourly render.

    It was latent only because this household has no manual.csv. Two review
    lanes hit it while doing unrelated work; nothing here did.

    The lesson is the case, not the string: a gate that fires on ordinary data
    is a broken build, so the render is exercised against each optional file
    rather than only against the shape this one pool happens to have.
    """
    print("\n  render — optional files do not break the build")

    root = config.root()
    OPTIONAL = {
        "manual.csv": (
            ["measured", "source", "ph", "free_cl", "ta", "ch", "cya", "salt", "by"],
            {"measured": "2026-09-15T10:00:00-0700", "source": "Taylor K-2006",
             "ph": "7.5", "free_cl": "3.1", "ta": "73", "ch": "260",
             "cya": "45", "salt": "3200", "by": "local"}),
        "corrections.csv": (
            ["at", "source", "measured", "action", "field", "value", "note", "by"],
            {"at": "2026-09-15T11:00:00-0700", "source": "lab",
             "measured": "2026-09-01T10:00:00-0700", "action": "drop",
             "field": "", "value": "", "note": "fouled sample", "by": "local"}),
        "chemicals.csv": (
            ["ts", "chemical", "amount", "unit", "pct", "note", "by"],
            {"ts": "2026-09-15T12:00:00-0700", "chemical": "acid", "amount": "12",
             "unit": "floz", "pct": "31.45", "note": "after the storm",
             "by": "local"}),
    }

    for name, (cols, row) in OPTIONAL.items():
        d = tempfile.mkdtemp()
        dd = os.path.join(d, "data")
        os.makedirs(dd)
        os.makedirs(os.path.join(d, "site"))
        with open(os.path.join(d, "config.toml"), "w") as f:
            f.write("[pool]\nvolume_gallons = 16250\n")
        with open(os.path.join(dd, name), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerow(row)
        env = dict(os.environ, POOLHOUND_DATA=dd,
                   POOLHOUND_SITE=os.path.join(d, "site"),
                   POOLHOUND_CONFIG=os.path.join(d, "config.toml"))
        r = subprocess.run([sys.executable, os.path.join(root, "bin", "render")],
                           capture_output=True, text=True, env=env, timeout=180)
        check(f"a pool whose only data is {name} renders", r.returncode, 0)
        if r.returncode:
            # The reason, not just the code — a build failure that prints no
            # cause is the thing this file exists to stop.
            print("    " + (r.stderr.strip().splitlines() or ["(no stderr)"])[-1][:150])
        shutil.rmtree(d, ignore_errors=True)


def t_a_dose_is_addressable_by_id_everywhere_it_is_written():
    """Every writer of a dose mints an id, and the page sends it back.

    A dose's identity was its timestamp, and the form records to the minute, so
    two doses in one minute made BOTH rows permanently uneditable and
    undeletable — while the page went on rendering an Edit and a Delete button
    on each. The server lane gave the row an id; this pins the two ends it
    could not reach across file boundaries: bin/chem minting one at write time
    rather than leaving the row to be backfilled, and the page's buttons
    carrying it.
    """
    import re
    from . import chemicals as CH, panels, render
    print("\n  doses — addressable by id from every writer")

    check("the column list carries an id", "id" in CH.CHEM_COLS, True)
    a, b = CH.new_id(), CH.new_id()
    check("ids are distinct", a != b, True)
    check("and are shaped like ids", bool(re.fullmatch(r"[0-9a-f]{6,32}", a)), True)

    # The CLI writes one at log time. Reading the source rather than running it
    # here, because running it writes a dose — which is exactly how two
    # fabricated rows reached this household's real log earlier today.
    import inspect
    from . import chem as _cli
    src = inspect.getsource(_cli)
    check("bin/chem mints an id when it logs", "CH.new_id()" in src, True)

    # And the page hands it back on both controls.
    row = {"ts": "2026-09-16T09:30:00-0700", "chemical": "acid", "amount": "1",
           "unit": "floz", "pct": "31.45", "note": "n", "by": "local",
           "id": "abc123def456"}
    markup = panels.dose_log_rows([row]) if hasattr(panels, "dose_log_rows") else ""
    if markup:
        check("the edit button carries the id", 'data-id="abc123def456"' in markup, True)
        check("and the delete button is keyed by it",
              'data-del="abc123def456"' in markup, True)
    else:
        # The row builder is inline; assert against the template instead.
        check("the edit button carries the id",
              'data-id="{esc(r.get("id"))}"' in inspect.getsource(panels), True)
        check("and the delete button prefers it",
              'data-del="{esc(r.get("id") or r.get("ts"))}"' in inspect.getsource(panels), True)
    check("the page sends id on delete when it has one",
          "b.dataset.delKind === 'id'" in render.TEMPLATE, True)
    check("and sends it on edit", "data.id = form.dataset.editingId" in render.TEMPLATE, True)
