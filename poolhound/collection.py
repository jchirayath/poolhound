"""The Collection tab: every attempt to fetch or publish, and what came of it.

WHY THIS PAGE EXISTS

Four things knew something about a collection run and none could be looked at:
the status file collect.sh overwrites every run, the cron log on a host the
container cannot read, audit.csv (which records what a PERSON did), and the
watchdog's own alert state. So "did the WaterGuru pull work this week" had no
answer — and the defect that mattered most, a collector running seven hours
early since a migration and fetching the previous day's reading every time, was
invisible for five days while every check reported green.

WHAT THIS PAGE ANSWERS, in the order somebody asks it:

  1. Is anything broken right now?
  2. When did each source last work, and when is it next due?
  3. Did an alarm actually reach anybody?
  4. What happened over the last few days, in order?

THE SCHEDULE IS READ, NOT RESTATED. The cadences come from the crontab this
repository ships, so a page claiming a source runs daily cannot drift from the
file that runs it — the exact drift that hid the seven-hour shift.
"""

import collections
import datetime as dt
import html
import os
import re

from . import cadence as cad
from . import config
from .icons import icon
from .render import ago, data_table, is_future, when

# What each tool is called on the page, and what it is FOR — a reader who has
# never seen this product should be able to tell what "leslies" means without
# already knowing.
# Things that RUN HERE, on a schedule, and file a row in runs.csv when they do.
TOOLS = {
    "wg-collect": ("WaterGuru", "Pod readings and the mailed-in lab result."),
    "leslies":    ("Leslie's", "The in-store bench photometer result."),
    "watch":      ("Watchdog", "Has a collector stopped, is the chemistry out, "
                               "did a heater come on."),
    "render":     ("Page", "Rebuilding the two published pages."),
    "backup":     ("Backup", "Copying the history off the share it lives on — "
                             "the only copy that survives losing the share."),
}

# AND THE ONE THAT ARRIVES INSTEAD OF RUNNING.
#
# The controller was two cards here, and both of them were permanently wrong.
# "Pool controller" was keyed on `bin/sample`, the WORKSTATION tool that polls
# AqualinkD directly across the house network -- which the server has no route to
# and never will, by design. "Pi agent" was keyed on a tool name that nothing
# writes, because the agent runs on the Pi and POSTs; it does not file rows in
# the server's runs.csv. So the single most important source on the page reported
# "never run / nothing recorded yet / not scheduled -- run by hand" twice over,
# beside a masthead reading "last reading 2 hours ago" off the same data.
#
# The direction of the arrow is the whole point of this architecture: the panel
# has no authentication of its own, so nothing reaches in and the Pi connects
# outward. A status tab that models it as something we poll describes a product
# we deliberately did not build.
#
# So it is ONE card, and its freshness comes from the rows themselves rather
# than from a record of an attempt. That is the right question anyway: for a
# pushed source "did it arrive" is the only thing observable from this end, and
# it is true whichever side wrote the row -- the agent in production, or
# bin/sample on a workstation that can see the panel.
PUSHED = "controller"
PUSHED_LABEL = "Pool controller"
PUSHED_WHAT = ("The panel, sampled on the Pi and pushed in. Nothing here "
               "reaches into the house \u2014 the agent connects outward.")

# WHERE A CADENCE COMES FROM, NOW THAT THERE ARE TWO ANSWERS.
#
# The two lab pulls are a SETTING -- `[collection]` in config.toml, owned by
# poolhound/cadence.py, changeable from Settings, enforced in the due test that
# spends the call. The watchdog and the safety-net render are still the
# crontab's: they cost nothing, they call nobody else's service, and their
# cadence is a property of the deployment rather than a choice about it.
#
# So cron is a TICK for the first two and a SCHEDULE for the other two, and one
# function has to merge that. This is it, and it stays the only reader: the page
# asks here and never parses either source itself. Eleven lines of panels.py and
# help.py once wrote the times out by hand and every one of them was nine hours
# stale -- see seams.py, "the collectors' clock times".
CRON_FILE = cad.CRON_FILE

# ONE CADENCE, TWO WIDTHS OF THE SAME SENTENCE -- a sentence for the page and a
# `clock` for a label inside an SVG box with eighty pixels to spend. Indexable,
# so `sched[tool][0]` and `[1]` still mean what they meant.
Cadence = collections.namedtuple("Cadence", "human seconds clock settable")


def schedule():
    """When each collector runs: {tool: Cadence(human, seconds, clock, settable)}.

    Read, never restated. A page that says "daily" beside a job that runs
    hourly is the drift this whole tab exists to make visible, and it happened
    -- CRON_TZ is inert on the deploy host, so the pulls ran seven hours early
    for five days while every check reported green.

    `settable` is the half a reader needs and the page could not previously
    say: whether this cadence is something they may change here, or a fact
    about the deployment they would have to edit a file to alter.
    """
    fields = cad.cron_fields()
    out = {}
    for tool in cad.SOURCES:
        # A tick is not a schedule. These lines fire hourly and cadence.py
        # decides, so reading the crontab for them would report the heartbeat
        # as the cadence -- "hourly", against a budget of two calls a day.
        if cad.every_hours(tool) is None:
            continue
        out[tool] = Cadence(cad.human(tool), cad.every_hours(tool) * 3600,
                            cad.clock(tool), True)
    for tool, (minute, hour) in fields.items():
        if tool in out:
            continue
        if hour.startswith("*/"):
            every, human = int(hour[2:]) * 3600, f"every {hour[2:]} hours"
            clock = f"{minute} {hour}"
        elif hour == "*" and minute.startswith("*/"):
            every, human = int(minute[2:]) * 60, f"every {minute[2:]} minutes"
            clock = minute
        elif hour == "*":
            every, human, clock = 3600, "hourly", "hourly"
        else:
            # UTC, and said so: the server is Etc/UTC and its cron does not
            # honour CRON_TZ, which is exactly how the schedule drifted seven
            # hours without anybody seeing it.
            every = 86400
            human = f"daily at {int(hour):02d}:{int(minute):02d} UTC"
            clock = f"{int(hour):02d}:{int(minute):02d}"
        out[tool] = Cadence(human, every, clock, False)
    return out


def cadence(tool, sched=None):
    """One scheduled tool's cadence as a sentence, or why it has none.

    THREE ANSWERS, NOT TWO, and the distinction is the reason this is a
    function. "The crontab does not schedule this tool" and "there is no
    crontab in this build" are different facts, and answering "run by hand" to
    both is a guess dressed as a fact -- the deploy image carries the crontab,
    a bare checkout of the site directory does not.

    Called by the Collection tab and by the Settings schedule beside it. It was
    written out twice before, and the copy in Settings was the one that drifted.
    """
    sched = schedule() if sched is None else sched
    c = sched.get(tool)
    if c:
        return c.human
    return ("not scheduled \u2014 run by hand" if sched
            else "schedule unknown here \u2014 the crontab is not in this build")


def pushed_cadence():
    """How often the controller's samples arrive, per the agent that sends them.

    agent.SAMPLE_EVERY owns this, not the crontab: the Pi decides, and the
    server has no say. Settings restated it as "every 15 min" in a table cell,
    which would have gone stale the moment the Pi's interval changed.
    """
    from . import agent as _agent
    return f"pushed by the Pi about every {round(_agent.SAMPLE_EVERY / 60)} minutes"


# WHICH STATES MEAN SOMEBODY HAS TO DO SOMETHING. One owner, because this was
# written twice — here and in render._collection_badge() — and both copies said
# ("failed", "late"), so a source that had NEVER RUN was counted by neither.
# The tab's banner said "Every scheduled source has run recently and none
# reported a failure" over a card reading "never run", and the tab badge was
# absent beside it. Agreeing with each other is not the same as being right.
NEEDS_ATTENTION = ("failed", "late", "never")


def _state(row, sched, did=None):
    """ok / skipped / late / failed / never, for one tool.

    `row` is the most recent attempt; `did` is the most recent attempt that was
    not a skip. Both are needed, and that is the whole point — see below.
    """
    if row is None:
        return "never", "has never run"
    outcome = (row.get("outcome") or "").strip()
    at = row.get("at", "")
    if outcome == "failed":
        return "failed", f"failed {ago(at)}"

    # LATENESS IS MEASURED FROM THE LAST ATTEMPT THAT DID SOMETHING.
    #
    # A skip writes a row with a fresh `at` and collects nothing, so measuring
    # the gap from the most recent attempt let a wall of skips hold the clock
    # permanently fresh. That is not hypothetical: collect.sh skipped every job
    # for forty hours when the share failed to mount — 86 logged skips, every
    # thirty minutes, `watch` among them — and this tab would have called all
    # of it recent. The attempt is evidence that cron fired; only a run that
    # was not skipped is evidence that anything was collected.
    if did is None:
        return "never", (f"has never completed a run — the last attempt "
                         f"skipped {ago(at)}")
    ref_at = did.get("at", "")
    d = when(ref_at)
    if d and sched:
        gap = (dt.datetime.now() - d).total_seconds()
        # Two full cadences before calling it late: one missed run is a blip,
        # two is a pattern, and crying wolf is how a status strip stops being
        # read at all.
        if gap > sched[1] * 2:
            return "late", (f"last completed a run {ago(ref_at)}, "
                            f"and it is scheduled {sched[0]}")

    # A SKIP IS NOT A SUCCESS, and it used to render as the green "working"
    # pill. It gets its own word. It is deliberately NOT in NEEDS_ATTENTION:
    # the ordinary skip is "the previous run is still going", and a pattern of
    # them now surfaces as `late` above, which is the honest signal.
    if outcome == "skipped":
        return "skipped", (f"skipped {ago(at)} — "
                           f"{row.get('detail') or 'already running'}; "
                           f"last completed run {ago(ref_at)}")
    return "ok", f"ran {ago(at)}"


def _pushed_card():
    """The controller's card, judged on the readings rather than on an attempt.

    `late` is two full cadences, the same rule the scheduled collectors get,
    and the cadence is read from agent.SAMPLE_EVERY rather than restated here
    — the Pi is what decides how often it pushes.

    A future timestamp is not freshness. is_future() already exists because a
    clock-skewed writer once made a reading look newer than now; a pushed row
    comes from a different machine, which is exactly where that happens.
    """
    from . import agent as _agent
    from .render import rows
    every = _agent.SAMPLE_EVERY
    cad = pushed_cadence()

    newest = None
    for r in rows("samples.csv"):
        at = (r.get("ts") or "").strip()
        d = when(at)
        if d and not is_future(at) and (newest is None or d > newest[0]):
            newest = (d, at)

    if newest is None:
        state, text = "never", "nothing has arrived yet"
        row = None
    else:
        d, at = newest
        gap = (dt.datetime.now() - d).total_seconds()
        state = "late" if gap > every * 2 else "ok"
        text = (f"last arrived {ago(at)}" if state == "ok"
                else f"last arrived {ago(at)}, and it is {cad}")
        row = {"at": at, "outcome": "ok", "detail": "", "added": "", "seconds": ""}
    return {"tool": PUSHED, "label": PUSHED_LABEL, "what": PUSHED_WHAT,
            "state": state, "text": text, "schedule": cad, "row": row}


def summary(runs):
    """One card per source. The scheduled ones first, then the pushed one.

    The pushed one is LAST on purpose: the four above it are answers to "did
    the thing we run still run", and the controller is an answer to "did the
    thing that runs elsewhere still reach us". Reading them as one list, in one
    order, was how it came to be modelled as something we poll.
    """
    sched = schedule()
    latest, latest_did = {}, {}
    for r in runs:
        t = r.get("tool", "")
        if not t:
            continue
        if t not in latest:
            latest[t] = r
        # ...and separately, the newest one that was not a skip. See _state:
        # a skip is an attempt, not a collection, and a run of them must not
        # read as freshness.
        if t not in latest_did and (r.get("outcome") or "").strip() != "skipped":
            latest_did[t] = r
    out = []
    for tool, (label, what) in TOOLS.items():
        row = latest.get(tool)
        state, text = _state(row, sched.get(tool), latest_did.get(tool))
        out.append({"tool": tool, "label": label, "what": what,
                    "state": state, "text": text,
                    # Absent crontab and "this tool is not in it" are
                    # different answers; cadence() is the one place that knows.
                    "schedule": cadence(tool, sched),
                    "row": row})
    out.append(_pushed_card())
    return out


def _pill(state, text):
    # The colour vocabulary the equipment cards already use, and never colour
    # alone: every pill ships its word.
    word = {"ok": "working", "late": "late", "failed": "failed",
            "never": "never run", "skipped": "skipped"}[state]
    cls = {"ok": "st-on", "late": "st-pending", "failed": "st-bad",
           "never": "st-unknown", "skipped": "st-pending"}[state]
    # `st`, not `stpill`. The base class that carries the pill's shape -- the
    # inline-flex, the status dot, the padding and the radius -- is `.st`, and
    # this file asked for a class that does not exist, so every pill on the tab
    # rendered as a bare coloured word with the card's text running into it.
    # Third of three invented class names in this file; see .source-card.
    return (f'<span class="st {cls}">{html.escape(word)}</span>'
            f'<span class="sub">{html.escape(text)}</span>')


def panel(public=False):
    """The whole tab. Private only — this is operational detail about a house."""
    from . import runlog
    runs = runlog.rows()
    cards = summary(runs)

    broken = [c for c in cards if c["state"] in NEEDS_ATTENTION]
    lede = ("Every attempt to fetch or publish, and what came of it. "
            "Each source is separate because they do not work the same way. "
            "The two labs are pulls, on somebody else's server, and are "
            "rationed. The controller is a push: the Pi samples the panel and "
            "sends it here, so what can be judged from this end is whether it "
            "arrived, not whether we asked.")
    if broken:
        head = (f'<div class="v-warn">{icon("alert", 15)} '
                f'<b>{len(broken)} source(s) need attention:</b> '
                + ", ".join(html.escape(c["label"]) for c in broken) + "</div>")
    else:
        head = (f'<div class="v-ok">{icon("check", 15)} '
                f'Every scheduled source has run recently and none reported a '
                f'failure.</div>')

    grid = "".join(
        f'''<div class="card source-card">
      <div class="srch"><b>{html.escape(c["label"])}</b>{_pill(c["state"], c["text"])}</div>
      <p class="sub">{html.escape(c["what"])}</p>
      <p class="sub"><b>Scheduled:</b> {html.escape(c["schedule"])}</p>
      {_last_detail(c["row"], pushed=c["tool"] == PUSHED)}
    </div>''' for c in cards)

    # ALARMS, AND WHETHER THEY REACHED ANYBODY. A watchdog run that raises two
    # alarms and delivers none is not a quiet night, and until this column
    # existed nothing on the page could tell the two apart.
    alerted = [r for r in runs if (r.get("alerts") or "0") != "0"]
    if alerted:
        rows_ = "".join(
            f'<tr><td>{html.escape((r.get("at") or "")[:16].replace("T", " "))}</td>'
            f'<td class="n">{html.escape(r.get("alerts","0"))}</td>'
            f'<td class="n">{html.escape(r.get("sent","0"))}</td>'
            f'<td class="n {"bad" if (r.get("undelivered") or "0") != "0" else ""}">'
            f'{html.escape(r.get("undelivered","0"))}</td>'
            f'<td>{html.escape(r.get("detail",""))}</td></tr>'
            for r in alerted[:20])
        notif = f'''<section id="col-alerts">
  <h2>{icon("bell", 17)}<span>Alerts, and whether they were delivered</span></h2>
  <p class="sub">An alarm that was raised and not delivered is the one failure
  this product cannot afford quietly: the pool carries on, and nobody is told.
  A non-zero <b>undelivered</b> means no notifier is working — set up email on
  Settings.</p>
  <div class="card"><div class="scroll"><table>
    <thead><tr><th>When</th><th>Raised</th><th>Delivered</th><th>Undelivered</th>
      <th>What</th></tr></thead>
    <tbody>{rows_}</tbody></table></div></div>
</section>'''
    else:
        notif = f'''<section id="col-alerts">
  <h2>{icon("bell", 17)}<span>Alerts, and whether they were delivered</span></h2>
  <div class="card"><p class="empty">No alarm has been raised since this log
  began. When one is, it appears here with whether a notifier actually carried
  it.</p></div>
</section>'''

    history = data_table(
        "Every attempt", runs,
        ["at", "tool", "outcome", "seconds", "added", "alerts", "sent",
         "undelivered", "detail", "by"],
        source="runs.csv")

    return f'''<section id="col-now">
  <h2>{icon("gauge", 17)}<span>Where collection stands</span></h2>
  <p class="sub">{lede}</p>
  {head}
  <div class="srcgrid">{grid}</div>
</section>
{notif}
{_moved()}
<section id="col-history">
  <h2>{icon("clock", 17)}<span>What has been tried</span></h2>
  <p class="sub">One row per attempt, whether it worked or not — written from a
  <code>finally</code>, so a collector that crashes is on the record rather
  than being the reason there is no record. <b>added</b> is how many rows the
  run actually wrote: a pull that succeeds and adds nothing is a different
  event from one that adds four.</p>
  {history}
</section>'''


def _moved():
    """Three sections that used to be in Settings, and belong here.

    SETTINGS IS WHAT YOU CHANGE; THIS TAB IS THE RECORD. Settings had grown to
    eight sections mixing the two, so a reader looking for a control waded
    through logs and a reader looking for the history found it filed under
    configuration. These three are the record:

      Record a test result   its own first line calls a hand-entered kit result
                             "a third source alongside the two collectors",
                             which is this tab's subject exactly.
      A reading that is wrong  corrections.csv — which readings were dropped or
                             overridden, and why. A correction is a fact about
                             the data, not a preference.
      What was done          audit.csv, what a PERSON did. This tab's own
                             docstring names it as one of the four things that
                             knew something about a run and could not be looked
                             at; it is looked at here now.

    Imported here rather than at module scope: panels.py already reaches into
    this module for the cadence, and a cycle at import time would take the
    whole page down to save one line.
    """
    from . import panels
    return (panels.reading_panel() + panels.corrections_panel() +
            panels.audit_panel())


def _last_detail(row, pushed=False):
    if not row:
        # "Nothing recorded yet" is right for a tool that has never run and
        # wrong for a source that has never arrived — one is a job that has
        # not fired, the other is a machine that has not called in.
        return ('<p class="sub">Nothing has been pushed to this install yet.</p>'
                if pushed else '<p class="sub">Nothing recorded yet.</p>')
    if pushed:
        # There is no attempt to describe: the row is a READING, and the card
        # above already says when it arrived. Restating it as "added 1 row"
        # would be dressing a measurement up as a job.
        return ""
    bits = []
    if (row.get("added") or "") not in ("", "0"):
        bits.append(f'added {html.escape(row["added"])} row(s)')
    if row.get("seconds"):
        bits.append(f'took {html.escape(row["seconds"])}s')
    if (row.get("undelivered") or "0") != "0":
        bits.append(f'<b class="bad">{html.escape(row["undelivered"])} alert(s) '
                    f'could not be delivered</b>')
    detail = html.escape(row.get("detail", ""))
    return (f'<p class="sub">{detail}'
            + (f' &middot; {" &middot; ".join(bits)}' if bits else "") + "</p>")
