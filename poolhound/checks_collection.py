"""The Collection tab: the record of every fetch, and whether alerts landed."""

import os
import shutil
import tempfile

from .selftest import check, skipped


def t_every_attempt_is_recorded_whichever_way_it_ends():
    """A crash is a row, not the reason there is no row.

    bin/watch died on every run for weeks and the only reason nobody knew is
    that nothing wrote down that it had tried. runlog.Run records from a
    __exit__, so the attempt survives the exception that ended it — and never
    raises on its own, because a collector that worked must not be reported
    broken by its bookkeeping.
    """
    from . import runlog, render
    print("\n  runlog — every attempt is recorded")

    d = tempfile.mkdtemp()
    prev_env, prev_data = os.environ.get("POOLHOUND_DATA"), render.DATA
    try:
        os.environ["POOLHOUND_DATA"] = d
        render.DATA = d
        with runlog.Run("wg-collect", by="cron") as r:
            r.added, r.detail = 3, "three new rows"
        got = runlog.rows()
        check("a clean run is recorded", len(got), 1)
        # Indexed defensively: on a world where recording has been broken this
        # list is empty, and a detector that raises IndexError instead of
        # reporting a failure takes the whole suite down with it — which is how
        # the meta-check found this one.
        check("with what it actually did",
              got[0]["added"] if got else "(nothing recorded)", "3")

        raised = False
        try:
            with runlog.Run("leslies", by="cron"):
                raise RuntimeError("the vendor hung up")
        except RuntimeError:
            raised = True
        check("an exception still propagates", raised, True)
        check("and the attempt is on the record anyway", len(runlog.rows()), 2)
        got = runlog.rows()
        newest = got[0] if got else {}
        check("marked failed", newest.get("outcome"), "failed")
        check("with the error kept",
              "the vendor hung up" in newest.get("detail", ""), True)

        # THE TIE. Timestamps are whole seconds and several collectors finish
        # inside one, so sorting on `at` alone left "the latest" undefined and
        # the page's banner disagreed with the table under it.
        for i in range(3):
            runlog.record("watch", "ok" if i < 2 else "failed", detail=f"n{i}")
        watches = [r for r in runlog.rows() if r["tool"] == "watch"]
        check("the newest of several in one second is the last written",
              watches[0]["detail"] if watches else "(nothing recorded)", "n2")

        # Bookkeeping must never fail the run.
        os.environ["POOLHOUND_DATA"] = "/nonexistent/path/that/cannot/be/made"
        render.DATA = "/nonexistent/path/that/cannot/be/made"
        ok = True
        try:
            runlog.record("render", "ok", detail="x")
        except Exception:                               # noqa: BLE001
            ok = False
        check("recording never raises, even when it cannot write", ok, True)
    finally:
        render.DATA = prev_data
        if prev_env is None:
            os.environ.pop("POOLHOUND_DATA", None)
        else:
            os.environ["POOLHOUND_DATA"] = prev_env
        shutil.rmtree(d, ignore_errors=True)


def t_the_schedule_is_read_not_restated():
    """The cadences come from the crontab that runs the jobs.

    A page saying "daily" beside a job the crontab runs hourly is the drift
    this tab exists to make visible — and it happened: CRON_TZ is inert on the
    deploy host, so the collectors ran seven hours early for five days while
    every check reported green. A second copy of the schedule would have been
    the same defect wearing a new hat.
    """
    from . import collection
    print("\n  collection — the schedule is read from the crontab")

    import os
    from . import config
    crontab = os.path.join(config.root(), *collection.CRON_FILE)
    sched = collection.schedule()
    if not os.path.exists(crontab):
        # NOT A FAILURE, AND NOT A PASS EITHER. A source checkout has the
        # crontab; a stripped build might not. Saying so out loud is the
        # difference between "checked" and "skipped" — the distinction two
        # gates in this project lost entirely by exiting 0 while measuring
        # nothing.
        skipped("the collectors' cadence, read from the crontab",
                "no crontab in this tree")
        return
    check("the crontab was found and parsed", bool(sched), True)
    for tool in ("wg-collect", "leslies", "watch", "render"):
        check(f"{tool} has a cadence", tool in sched, True)
    check("a daily job is a day apart", sched["wg-collect"][1], 86400)
    check("and the watchdog is not", sched["watch"][1] < 3600, True)
    # The hour is stated in UTC, because the host is and its cron ignores
    # CRON_TZ -- saying "14:00" without the zone is how the shift hid.
    check("a daily cadence names its timezone", "UTC" in sched["wg-collect"][0], True)


# THE ELEVEN LINES THAT WERE ACTUALLY ON THE PAGE, kept as the input this
# detector must reject. Verbatim from panels.py and help.py before the fix --
# four rows of the Settings schedule table, and seven statements across Help's
# two architecture drawings. They are the whole argument for the seam: every
# one reads like a fact and not one of them was one.
STALE_CADENCE_LINES = [
    # panels.py, the Settings schedule table: the cron rows and, beneath them,
    # the launchd rows for a workstation collection that no longer exists.
    '("/etc/cron.d/poolhound", "WaterGuru pull", "daily 14:00", "the server"),',
    '("/etc/cron.d/poolhound", "Leslie\'s pull", "daily 14:05", "the server"),',
    '("net.aspl.poolhound-waterguru", "WaterGuru pull", "daily 14:00", "this Mac"),',
    '("net.aspl.poolhound-leslies", "Leslie\'s pull", "daily 14:05", "this Mac"),',
    # help.py, the compact drawing: one summary line.
    '''a('<text class="a-s" x="732" y="570">pulled once a day — 14:00 and 14:05</text>')''',
    # help.py, the detailed drawing's <desc> -- what a screen reader is read,
    # which is the copy nobody looks at and everybody trusts.
    '"WaterGuru at 14:00 and Leslie\'s at 14:05 and runs the watchdog every thirty "',
    # help.py, the two rows of the cron box that carry a time.
    'for y, when, job in ((712, "14:00", "bin/wg-collect"),',
    '                     (729, "14:05", "bin/leslies"),',
    # help.py, why the pull is timed where it is -- two wrong numbers at once.
    '''a('<text class="a-s" x="964" y="706">14:00, so it lands after the 12:45 test</text>')''',
    # help.py, the two arrow labels. THE ONES THAT MATTER MOST: they are what
    # the eye lands on, and they are the two a pattern anchored on a collector
    # name cannot see, because the label is the time and nothing else.
    '''a('<text class="a-e" x="912" y="658" text-anchor="middle">14:00</text>')''',
    '''a('<text class="a-e" x="912" y="768" text-anchor="middle">14:05</text>')''',
]


def t_no_page_restates_the_collectors_clock():
    """Only collection.py may state when a collector runs.

    MEASURED, and it is the drift the tab above exists to expose, arriving one
    tab over. Eleven lines wrote the times out by hand -- four rows of the
    Settings schedule table and seven statements across Help's two architecture
    drawings, the <desc> a screen reader is read included. Every one said 14:00
    and 14:05: the times the crontab held BEFORE the CRON_TZ fix moved the
    pulls to 23:00 and 23:05 UTC. So one build stated the schedule nine hours
    apart in two places, and the stale copy was the one somebody asking "when
    does WaterGuru run" reaches first, because it sits in Settings beside the
    credentials and in a drawing captioned as what the deployment does.

    Both directions, because a clean scan proves nothing on its own: the tree
    must hold no copy, AND the pattern must be shown to reject the eleven lines
    it was written for. The pattern that preceded this one required a collector
    NAME on the same line as the time and so found seven of the eleven -- it
    missed both SVG arrow labels, which are the two a reader looks at first,
    the line explaining why the pull is timed where it is, and the compact
    drawing's summary, because each of those is a time and nothing else.
    """
    from . import cadence, collection, config, seams
    import re
    print("\n  collection — no page restates when a collector runs")

    names = [w for w, _, _, _ in seams.SEAMS]
    check("the clock-time seam is declared",
          "the collectors' clock times" in names, True)
    # THE OWNER MOVED, AND THE SEAM MOVED WITH IT. The pull cadence used to be
    # the crontab's and is now a setting; cadence.py is what reads and bounds
    # it. A seam still naming the old owner would exempt the wrong module.
    check("and cadence.py owns it",
          [o for w, o, _, _ in seams.SEAMS if w == "the collectors' clock times"],
          ["cadence.py"])
    check("no module outside it holds a clock time",
          seams.copies(config.root()), [])

    # THE INPUT THAT MUST BREAK IT. A green line above is evidence only if the
    # same pattern can be shown to go red, and this is the code that was really
    # there rather than a synthetic line written to match.
    pattern = next(pat for w, _, pat, _ in seams.SEAMS
                   if w == "the collectors' clock times")
    for line in STALE_CADENCE_LINES:
        check(f"rejected: {line.strip()[:52]}",
              bool(re.search(pattern, line)), True)

    # AND THE TIMES IT STATES ARE THE ONES ITS OWNER HOLDS, which is the half
    # that catches the drift the other way round -- a page kept in step with a
    # source nobody had updated.
    #
    # Two owners now, and the page has to get both right. The lab pulls are a
    # setting, so their cadence comes from cadence.py; the watchdog and the
    # safety-net render are the crontab's, because they cost nothing and call
    # nobody. A tick is not a schedule: these lines fire HOURLY, and a page
    # that read the crontab for them would report "hourly" against a budget of
    # two calls a day.
    sched = collection.schedule()
    if sched:
        for tool in cadence.SOURCES:
            got = sched.get(tool)
            check(f"{tool} is marked settable",
                  got.settable if got else "(missing from the schedule)", True)
            check(f"and states the cadence cadence.py holds",
                  got.human if got else "(missing)", cadence.human(tool))
        for tool in ("watch", "render"):
            got = sched.get(tool)
            if got:
                check(f"{tool} is the crontab's, not a setting",
                      got.settable, False)
        check("a tick is not reported as the cadence",
              [t for t in cadence.SOURCES
               if (sched.get(t) or collection.Cadence("", 0, "", False)).human
               == "hourly"], [])


def t_the_summary_agrees_with_the_rows_it_summarises():
    """A banner that contradicts the table beneath it is worse than none.

    summary() takes the newest attempt per tool. With ties undefined it
    reported "none reported a failure" over a table listing two — found by
    driving the page, not by reading it.
    """
    from . import collection
    print("\n  collection — the summary matches the history")

    # RELATIVE, NOT A DATE I TYPED. The first version pinned two runs to a
    # fixed afternoon, which was "just now" when it was written and two days
    # stale by the time anybody ran it again — at which point _state called
    # them "late" and the case failed for a reason that had nothing to do with
    # what it tests. A fixture with a calendar date in it is a test with a
    # timer on it.
    import datetime as _dt
    recent = (_dt.datetime.now() - _dt.timedelta(minutes=5)).astimezone().strftime(
        "%Y-%m-%dT%H:%M:%S%z")
    runs = [
        {"at": recent, "tool": "wg-collect", "outcome": "ok",
         "detail": "fine", "alerts": "0", "sent": "0", "undelivered": "0"},
        {"at": recent, "tool": "wg-collect", "outcome": "failed",
         "detail": "503", "alerts": "0", "sent": "0", "undelivered": "0"},
    ]
    # rows() hands them newest-first; summary must take the first it sees.
    cards = {c["tool"]: c for c in collection.summary(runs)}
    check("the first row wins, so ordering is the caller's contract",
          cards["wg-collect"]["state"], "ok")
    cards = {c["tool"]: c for c in collection.summary(list(reversed(runs)))}
    check("and reversing the input reverses the verdict",
          cards["wg-collect"]["state"], "failed")

    # Every tool the page names has a label and a purpose -- a row reading
    # "leslies" tells a reader nothing.
    for tool, (label, what) in collection.TOOLS.items():
        check(f"{tool} is named for a person", bool(label and what), True)
    check("and so is the pushed source",
          bool(collection.PUSHED_LABEL and collection.PUSHED_WHAT), True)


def t_a_source_that_arrives_is_not_judged_on_whether_it_ran():
    """The controller PUSHES. It was modelled as two things we poll.

    "Pool controller" was keyed on bin/sample — the workstation tool that
    polls AqualinkD across the house network, which the server has no route to and
    never will — and "Pi agent" on a tool name nothing writes, because the
    agent runs on the Pi and POSTs rather than filing a row in the server's
    runs.csv. So the most important source on the tab reported "never run /
    nothing recorded yet / not scheduled — run by hand", twice, next to a
    masthead reading the age of the very samples it was denying.

    The direction of that arrow is the architecture: the panel has no
    authentication of its own, so nothing reaches in. A status page that
    models it as a poll describes a product we deliberately did not build.
    """
    import datetime as dt
    from . import collection, render, agent
    print("\n  collection — the controller arrives, it does not run")

    check("neither of the polled spellings is a source any more",
          [t for t in ("sample", "agent") if t in collection.TOOLS], [])
    check("the controller is the pushed one", collection.PUSHED, "controller")

    # Judged on the readings, with the cadence read from the Pi rather than
    # restated: a fresh sample is ok, and two cadences of silence is late.
    real = render.rows
    def fake(name, *a, **k):
        if name != "samples.csv":
            return real(name, *a, **k)
        return [{"ts": stamp[0]}]
    render.rows = fake
    try:
        stamp = [dt.datetime.now().replace(microsecond=0).isoformat()]
        card = collection._pushed_card()
        check("a sample that just arrived reads as working", card["state"], "ok")
        check("and the card says it arrived rather than that it ran",
              "arrived" in card["text"], True)

        old = dt.datetime.now() - dt.timedelta(seconds=agent.SAMPLE_EVERY * 3)
        stamp[0] = old.replace(microsecond=0).isoformat()
        check("three cadences of silence is late",
              collection._pushed_card()["state"], "late")

        # A reading stamped in the future is not freshness. The row comes from
        # a different machine, which is where clock skew actually happens.
        ahead = dt.datetime.now() + dt.timedelta(hours=6)
        stamp[0] = ahead.replace(microsecond=0).isoformat()
        check("a future timestamp does not count as having arrived",
              collection._pushed_card()["state"], "never")
    finally:
        render.rows = real

    check("the cadence is read from the agent, not restated",
          str(round(agent.SAMPLE_EVERY / 60)) in collection._pushed_card()["schedule"],
          True)


def t_the_collection_tab_is_private():
    """Operational detail about a house is not public.

    Failure times, vendor errors and whether an alarm reached anybody are
    exactly the "about the HOUSE rather than the POOL" split the two builds
    exist for.
    """
    from . import render
    print("\n  collection — the tab is private")
    check("it is classified private", "collection" in render.PRIVATE_TABS, True)
    check("and not public", "collection" in render.PUBLIC_TABS, False)


def t_an_unattributed_dose_says_which_kind_it_is():
    """A blank name meant two different things and showed one face.

    A row written before `by` existed and a row where recording the name FAILED
    both rendered as a dash, so a genuine future gap would look like ordinary
    history. The owner read a dash on a 12 Sep row and concluded the dose they
    had logged that morning was unattributed too — it was not; the dash was on
    the older row.

    The id column arrived with attribution, so it dates the row: no id and no
    name is history, a name missing WITH an id is a gap worth noticing.
    """
    from . import panels
    print("\n  doses — a blank name says which kind of blank it is")

    legacy = panels._unattributed({"ts": "2026-09-12T10:00:00-0700"})
    modern = panels._unattributed({"ts": "2026-09-18T10:17:00-0700", "id": "69ae7cf5b5d3"})
    check("a pre-attribution row says so", "not recorded" in legacy, True)
    check("and is not styled as a fault", 'class="bad"' in legacy, False)
    check("a row that should have a name and has none is flagged",
          "missing" in modern, True)
    check("and IS styled as a fault", 'class="bad"' in modern, True)
    check("the two do not render the same", legacy != modern, True)
    # Neither may be a bare dash: that is the ambiguity being removed.
    for out in (legacy, modern):
        check("no bare dash survives", "&ndash;" in out, False)


def t_the_control_banners_do_not_contradict_each_other():
    """Two banners, one line apart, disagreeing about the same question.

    The staleness banner is baked at RENDER time and used to end "a command
    will still be delivered, but check the result". Whether an agent is
    connected is a LIVE fact the browser fetches — the render cannot know it,
    and CLAUDE.md says as much: anything live must be fetched, never baked in.

    So when the agent was down the page said both "nothing here can reach the
    panel" and "a command will still be delivered", stacked. And that is the
    COMMON case, not an edge one: a down agent is why the readings are stale,
    so the two appeared together nearly every time either appeared at all.

    Reported by the owner, reading their own pool during a real outage.
    """
    from .selftest import in_deploy_tz
    with in_deploy_tz():
        _control_banners()


def _control_banners():
    from . import panels, render
    print("\n  control — the banners agree with each other")

    # THE RENDERED PAGE, not the source. The first version of this read
    # inspect.getsource() and failed twice over: the phrase it forbids appeared
    # in a COMMENT explaining the defect, and the phrase it requires is split
    # across two lines of an f-string and so never appears contiguously. What
    # ships is the question; the source is only how it got there.
    # BUILT AGAINST A POOL THAT HAS A READING, not against whatever this
    # machine's data directory happens to hold. The stale banner is emitted
    # only when there IS a last reading to call stale — correctly, since an
    # install with no samples has nothing to say is old — so on an empty
    # install this asserted the absence of a thing that was right to be
    # absent. It failed the moment the workstation's shadow data directory was
    # deleted, which is exactly the coupling that deletion was meant to end: a
    # check whose answer depends on the machine it runs on is not checking the
    # code. The fallback that used to grep the module for the class is gone
    # with it — it made the case pass by finding the string anywhere at all.
    #
    # AND THE STAMP IT BUILDS HAS TO AGREE WITH THE CLOCK IT IS BUILT FROM.
    # This read `datetime.now() - 9h` and then stamped the result "-0700",
    # which is only the same instant on a workstation already in that offset.
    # Run in Tokyo it describes a time sixteen hours in the FUTURE, nothing is
    # stale, and the banner this case asserts on is correctly absent — the
    # paragraph above, one line down. The offset now comes from the zone in
    # force, and the zone in force is the deployment's.
    import datetime as dt
    real = panels.rows
    recent = (dt.datetime.now().astimezone()
              - dt.timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M:%S%z")
    def fake(name, *a, **k):
        if name == "samples.csv":
            return [{"ts": recent, "pump": "0", "pool_temp": "74"}]
        return real(name, *a, **k)
    panels.rows = fake
    try:
        built = panels.control_panel()
    finally:
        panels.rows = real
    check("the page carries a slot for the live half",
          "ctl-stale-live" in (built or ""), True)
    check("and does not bake in a promise about delivery",
          "will still be delivered" in (built or ""), False)
    check("which the script fills from the agent count",
          "ctl-stale-live" in render.TEMPLATE, True)
    # The delivery sentence must exist ONLY in the script, where the answer is
    # known, and must be conditional on a connection.
    i = render.TEMPLATE.find("A command will still be delivered")
    check("the sentence lives in the script", i > 0, True)
    check("and is on the connected branch",
          "connected" in render.TEMPLATE[max(0, i - 200):i], True)


def t_a_tick_that_is_not_due_spends_nothing():
    """The due test, which is the only thing standing between an hourly tick
    and a vendor budget of two calls a day.

    THE WHOLE POINT IS THE ARITHMETIC, so it is checked here rather than by
    driving a page: a wrong answer is a wrong ANSWER, either a call spent that
    should not have been or a reading never fetched. Every case below is one
    the mechanism has to get right for the crontab to be allowed to tick.
    """
    import datetime as dt
    from . import cadence, render, runlog
    print("\n  cadence — a tick that is not due spends nothing")

    d = tempfile.mkdtemp()
    prev_env, prev_data = os.environ.get("POOLHOUND_DATA"), render.DATA
    try:
        os.environ["POOLHOUND_DATA"] = d
        render.DATA = d
        cfg = {"collection": {"waterguru_every_hours": 24,
                              "waterguru_hour_utc": 23}}

        # NOTHING COLLECTED YET IS ALWAYS DUE. A fresh install that waited for
        # an hour that had already passed would collect nothing until tomorrow.
        due, why = cadence.due("wg-collect", cfg)
        check("with nothing collected yet, it is due", due, True)
        check("and says why", "nothing has been collected" in why, True)

        # A SUCCESS FOUR MINUTES AGO IS NOT DUE AGAIN.
        runlog.record("wg-collect", "ok", detail="pulled")
        due, why = cadence.due("wg-collect", cfg)
        check("straight after a pull, it is not due", due, False)
        check("and names the cadence", "once a day" in why, True)

        # THE SLACK, WHICH IS WHY A DAILY PULL DOES NOT WALK ROUND THE CLOCK.
        # Cron fires on the hour; a success at 23:04 puts the next 24-hour
        # boundary four minutes after the 23:00 tick that should run it, so
        # without slack the pull moves an hour later every single day.
        # IN UTC, EXPLICITLY. Written naive-local first, which made "at the
        # hour" mean 06:00 UTC on a Pacific workstation and 23:00 UTC on the
        # server -- two of these cases failed here and passed there, which is
        # the worst direction for a test about a timezone to be wrong in.
        UTC = dt.timezone.utc
        base = dt.datetime(2026, 9, 20, 23, 4, 0, tzinfo=UTC)
        at_the_tick = dt.datetime(2026, 9, 21, 23, 0, 0, tzinfo=UTC)
        check("23h56m after a pull, the next on-the-hour tick IS due",
              cadence.due("wg-collect", cfg, now=at_the_tick,
                          )[0] if _seed(runlog, base) else None, True)
        # And not twice in one interval: the tick an hour earlier is refused,
        # so the slack can never admit two pulls into one period.
        check("but the tick an hour before that is not",
              cadence.due("wg-collect", cfg,
                          now=at_the_tick - dt.timedelta(hours=1))[0], False)

        # THE HOUR FLOOR. Due by elapsed time, held back until the hour.
        old = dt.datetime(2026, 9, 18, 23, 0, 0, tzinfo=UTC)
        _seed(runlog, old)
        morning = dt.datetime(2026, 9, 21, 9, 0, 0, tzinfo=UTC)
        got, why = cadence.due("wg-collect", cfg, now=morning)
        check("three days later but before the hour, still not due", got, False)
        check("and says which hour it is waiting for", "23:00 UTC" in why, True)
        check("at the hour, due",
              cadence.due("wg-collect", cfg,
                          now=dt.datetime(2026, 9, 21, 23, 0, 0, tzinfo=UTC))[0], True)

        # A FAILED PULL IS NOT THIS PERIOD'S PULL. Otherwise one vendor outage
        # at the due hour costs the whole day's reading, and the product would
        # report itself as having collected.
        d2 = tempfile.mkdtemp()
        os.environ["POOLHOUND_DATA"], render.DATA = d2, d2
        runlog.record("wg-collect", "failed", detail="the vendor hung up")
        check("a failed pull leaves it due", cadence.due("wg-collect", cfg)[0], True)
        shutil.rmtree(d2, ignore_errors=True)

        # A PERSON IS NOT GATED. The Refresh control exists to spend a call.
        os.environ["POOLHOUND_DATA"], render.DATA = d, d
        check("a cron tick is gated",
              cadence.tick_gate("wg-collect", "cron", cfg,
                                now=morning)[0], False)
        check("and a person asking is not",
              cadence.tick_gate("wg-collect", "jacob", cfg, now=morning)[0], True)
        check("nor is the console",
              cadence.tick_gate("wg-collect", "local", cfg, now=morning)[0], True)

        # A TOOL THIS MODULE HAS NEVER HEARD OF RUNS. Saying no by omission is
        # how salt-cell boost went missing from the page that lists every
        # control; a collector must not stop because a dict does not name it.
        check("an unknown tool is never gated",
              cadence.tick_gate("watch", "cron", cfg)[0], True)

        # THE FLOOR IS ENFORCED ON THE READ PATH, not only in the form: the
        # share is writable by anybody who can reach the host.
        greedy = {"collection": {"waterguru_every_hours": 1}}
        check("an hourly cadence typed into the file is clamped to the floor",
              cadence.every_hours("wg-collect", greedy),
              cadence.SOURCES["wg-collect"]["floor_hours"])
        check("and twelve hours is two calls a day, the vendor's ceiling",
              cadence.SOURCES["wg-collect"]["floor_hours"], 12)
        check("a cadence longer than a week is refused too, so the staleness "
              "alarm is not left ringing on purpose",
              cadence.every_hours("wg-collect",
                                  {"collection": {"waterguru_every_hours": 10000}}),
              cadence.CEILING_HOURS)

        # SUB-DAILY HAS NO HOUR, and must not print one.
        twice = {"collection": {"waterguru_every_hours": 12,
                                "waterguru_hour_utc": 23}}
        check("twice a day names no hour", cadence.hour_utc("wg-collect", twice), None)
        check("and does not state one either",
              "23:00" in (cadence.human("wg-collect", twice) or ""), False)
    finally:
        render.DATA = prev_data
        if prev_env is None:
            os.environ.pop("POOLHOUND_DATA", None)
        else:
            os.environ["POOLHOUND_DATA"] = prev_env
        shutil.rmtree(d, ignore_errors=True)


def _seed(runlog, at):
    """Put one successful pull on the record at `at`, and nothing else.

    Seeded rather than recorded, because runlog.record() stamps now() and every
    case here is about the distance between then and now.

    THROUGH locking.append_row, like every other writer of a shared CSV. A bare
    csv.writerow here would have been the fixture quietly exempting itself from
    the rule the product enforces -- and the gate that asks which modules write
    a CSV without the flow rule flagged this module the moment it appeared,
    which is the check doing exactly its job on a test.
    """
    from . import config, locking
    p = runlog.path()
    if os.path.exists(p):
        os.remove(p)                    # one row, not one appended to the last
    locking.append_row(config.data_dir(), "runs", p, runlog.COLS,
                       {"at": at.isoformat(), "tool": "wg-collect",
                        "outcome": "ok", "detail": "pulled", "by": "cron"})
    return True


def t_a_panel_change_is_pushed_once_it_has_settled():
    """ChangeWatch: when a panel change is worth a row, and when it is noise.

    MEASURED, and it is why this exists. On 2026-09-30 the pump ran a second
    block from 21:00 past midnight. The runtime was in the data -- by_day()
    accumulates it -- but the MOMENT it changed was not, because the heartbeat
    is fifteen minutes and the WaterGuru pod measured before that evening and
    not again until the next. A gradual overnight rise in free chlorine
    therefore arrived on the page as one unexplained step, and the audit log
    was empty because five of the six doors to that panel are invisible to it.

    Every case below is a way this could record the wrong thing: a menu walk as
    four events, an agent restart as a change, a flapping thermostat as
    hundreds of rows, or -- worst -- a real change silently dropped by the
    rate floor that was meant to tidy it up.
    """
    from . import agent, aqualink
    print("\n  agent — a panel change is pushed once it has settled")

    OFF = {"pump": 0, "spa": 0, "sheer": 0, "pool_temp": 72.0}
    ON = dict(OFF, pump=1)

    # THE FIRST OBSERVATION IS NOT A CHANGE. This service restarts on every
    # deploy; without it each restart writes a spurious event.
    w = agent.ChangeWatch(settle=4, min_gap=45)
    check("the first observation is never a change",
          w.saw(agent.state_of(OFF), 0), False)
    check("and an unchanged panel is not either",
          w.saw(agent.state_of(OFF), 5), False)

    # SETTLE. A PDA press can be reported as a burst; it is one event.
    check("a change is not pushed the instant it is seen",
          w.saw(agent.state_of(ON), 10), False)
    check("nor while it is still within the settle window",
          w.saw(agent.state_of(ON), 12), False)
    check("and is pushed once it has held", w.saw(agent.state_of(ON), 15), True)
    check("then not again for the same state",
          w.saw(agent.state_of(ON), 20), False)

    # A PRESS AND AN UN-PRESS INSIDE THE WINDOW IS NOT AN EVENT.
    w2 = agent.ChangeWatch(settle=4, min_gap=0)
    w2.saw(agent.state_of(OFF), 0)
    check("a state that returns before settling is not pushed",
          [w2.saw(agent.state_of(ON), 1), w2.saw(agent.state_of(OFF), 2),
           w2.saw(agent.state_of(OFF), 9)], [False, False, False])

    # THE FLOOR COALESCES, IT DOES NOT DROP. A floor that discards loses the
    # change it exists to record, which is worse than the noise.
    w3 = agent.ChangeWatch(settle=1, min_gap=45)
    w3.saw(agent.state_of(OFF), 0)
    check("the first change gets through", w3.saw(agent.state_of(ON), 2), False)
    check("  (after settling)", w3.saw(agent.state_of(ON), 4), True)
    spa = dict(ON, spa=1)
    check("a second change inside the floor is held, not pushed",
          [w3.saw(agent.state_of(spa), 10), w3.saw(agent.state_of(spa), 12)],
          [False, False])
    check("and lands once the floor expires, still carrying the change",
          w3.saw(agent.state_of(spa), 60), True)

    # A MEASUREMENT IS NOT A STATE CHANGE. Temperature drifts every few
    # seconds; treating it as an event would push a row per tick and write the
    # weather into the pool's history.
    w4 = agent.ChangeWatch(settle=0, min_gap=0)
    w4.saw(agent.state_of(OFF), 0)
    warmer = dict(OFF, pool_temp=79.5, air_temp=95.0, salt_ppm=3100.0)
    check("a temperature change alone is not an event",
          w4.saw(agent.state_of(warmer), 1), False)
    check("but the cell's output percentage is, because it is a setting",
          [w4.saw(agent.state_of(dict(OFF, swg_pct=60)), 2),
           w4.saw(agent.state_of(dict(OFF, swg_pct=60)), 3)][-1], True)

    # TYPES. read_sample() yields ints for switches and floats for setpoints;
    # a round trip through the server and the CSV makes every one a string.
    # 1 and "1" are the same panel state and must not read as an event.
    w5 = agent.ChangeWatch(settle=0, min_gap=0)
    w5.saw(agent.state_of({"pump": 1, "pool_set": 104.0}), 0)
    check("an int and its string are the same state",
          w5.saw(agent.state_of({"pump": "1", "pool_set": "104.0"}), 1), False)

    # EVERY COLUMN IS CLASSIFIED, both directions. A column added to
    # aqualink.COLS and to neither list would be silently unwatched -- or
    # worse, watched as if it were state and pushed on every drift.
    cols = set(aqualink.COLS) - {"ts"}
    watched, measured = agent.watched_columns(), agent.MEASURED_COLUMNS
    check("no sample column is unclassified", sorted(cols - watched - measured), [])
    check("and none is claimed that does not exist",
          sorted((watched | measured) - cols), [])
    check("and none is both", sorted(watched & measured), [])
    # DERIVED, not typed. A control added to commands.py is watched without
    # anybody remembering to, which is the promise this makes.
    from . import commands as CMD
    for dev, sw in CMD.SWITCHES.items():
        if sw.get("column"):
            check(f"{dev} is watched", sw["column"] in watched, True)


def t_a_door_may_claim_a_change_but_never_an_identity():
    """Which door a change came through is recordable; who did it is not.

    THE CLAIM ARRIVES FROM AN UNAUTHENTICATED PAGE on the house LAN, so
    anything on that network can make it. That is tolerable for "which door"
    and is not for "which person": a claim of `somebody@example.com` would put an
    identity nothing verified into an append-only log, and an audit you cannot
    trust is worse than none because it gets believed. So a door is
    allowlisted in commands.DOORS and written as `door:<name>`, which cannot
    be mistaken for somebody's identity whatever the claim said.
    """
    from . import agent, aqualink, commands as CMD, config, render, server
    print("\n  agent — a door may claim a change, never an identity")

    check("a known door is labelled", CMD.door_label("pool.html"), "door:pool.html")
    check("an unknown one is not", CMD.door_label("aqualinkd"), None)
    # EVERY SHAPE OF THE FORGERY THIS EXISTS TO REFUSE.
    for forged in ("somebody@example.com", "jacobc", "admin", "pool.html@evil",
                   "../pool.html", "POOL.HTML", ""):
        check(f"refused as a door: {forged!r}", CMD.door_label(forged), None)

    d = tempfile.mkdtemp()
    prev_env, prev_data = os.environ.get("POOLHOUND_DATA"), render.DATA
    try:
        os.environ["POOLHOUND_DATA"] = d
        render.DATA = d
        shutil.copy(os.path.join(config.root(), "config", "config.example.toml"),
                    os.path.join(d, "config.toml"))
        cfg = config.load()

        r, e = server.record_panel_change(
            {"changed": ["pump"], "door": "somebody@example.com"}, cfg)
        check("a forged identity still records the CHANGE", bool(r), True)
        check("but not as that identity", (r or {}).get("door"), "")
        rows = render.rows("audit.csv")
        check("and the row says the panel, not a person",
              rows[-1].get("by") if rows else "(nothing)", "panel")
        check("and holds no part of the forged name",
              any("jacobc" in str(v) for v in (rows[-1] if rows else {}).values()),
              False)

        # ARBITRARY TEXT MUST NOT REACH A PERMANENT RECORD. The ack route
        # accepted any id and wrote it to audit.csv forever; this is the same
        # shape and takes the same lesson.
        _, e = server.record_panel_change(
            {"changed": ["'; DROP", "<script>"], "door": "pool.html"}, cfg)
        check("a column name that does not exist is refused", bool(e), True)
        _, e = server.record_panel_change({"changed": "pump"}, cfg)
        check("and `changed` must be a list", bool(e), True)

        r, _ = server.record_panel_change({"changed": ["pump", "sheer"],
                                           "door": "pool.html"}, cfg)
        check("a real door is recorded", (r or {}).get("door"), "door:pool.html")
    finally:
        render.DATA = prev_data
        if prev_env is None:
            os.environ.pop("POOLHOUND_DATA", None)
        else:
            os.environ["POOLHOUND_DATA"] = prev_env
        shutil.rmtree(d, ignore_errors=True)

    # A CLAIM EXPLAINS ONE CHANGE. Left in place, a schedule firing an hour
    # later would inherit a door it never came through -- a false record of
    # exactly the kind this replaces.
    c = agent.Claims(ttl=60)
    c.add("door:pool.html", 100)
    check("the claim is taken by the change it explains", c.take(101), "door:pool.html")
    check("and is gone for the next one", c.take(102), None)
    c.add("door:pool.html", 100)
    check("a claim older than its ttl is not used", c.take(200), None)
    check("and nobody claiming is not an error", agent.Claims().take(0), None)

    # LOOPBACK ONLY, and there is no second option. The server's bind accepts
    # 0.0.0.0 because the container never publishes its port; there is no such
    # second fact here.
    check("the claim endpoint binds loopback", agent.CLAIM_HOST, "127.0.0.1")
    check("and is not the port anything else uses",
          agent.CLAIM_PORT not in (8787, 8000), True)


def t_the_pool_pages_activity_says_which_way_never_who():
    """poolhound local lists what the app did, and never who did it.

    The page's log was the browser's memory, so a pump the poolhound app
    switched never appeared on it. The agent now keeps the list, and serves it
    UNAUTHENTICATED to the house LAN through the Pi's web server. That is
    acceptable only because of what it leaves out: a command's `by` is a
    signed-in identity on the server, and it must not cross onto a page any
    phone on the Wi-Fi can read.
    """
    import json as _json, threading as _th, urllib.request as _ur
    from . import agent
    print("\n  agent — poolhound local's activity says which way, never who")

    d = tempfile.mkdtemp()
    try:
        path = os.path.join(d, "activity.json")
        act = agent.Activity(path=path, keep=5)
        cmd = {"id": "x", "action": "set", "device": "Filter_Pump", "value": 1,
               "by": "somebody@example.com"}
        agent.record_command(act, cmd, {"ok": True})
        shown = _json.dumps(act.recent())
        check("an app command is listed", act.recent()[0]["what"], "Filter pump on")
        check("as the app", act.recent()[0]["how"], "poolhound app")
        check("and nobody's identity reaches the page", "somebody" in shown, False)

        # THE APP'S OWN CHANGE ARRIVING is not a second event.
        agent.record_change(act, [("pump", "1")], None)
        check("the change the app caused is not listed twice", len(act.recent()), 1)

        agent.record_change(act, [("spa_light", "1")], "door:pool.html")
        check("a claimed change names its door", act.recent()[0]["how"], "door:pool.html")
        agent.record_change(act, [("sheer", "0")], None)
        check("an unclaimed one is the panel or AqualinkD",
              act.recent()[0]["how"], "panel or AqualinkD")
        check("and says what happened", act.recent()[0]["what"], "Sheer descent off")
        agent.record_change(act, [("pool_set", "88.0")], None)
        check("a setpoint reads as a number", act.recent()[0]["what"],
              "Pool heater set to 88")
        n = len(act.recent())
        agent.record_change(act, [("pool_set", "")], None)
        check("a reading that went blank is not an action", len(act.recent()), n)

        refused = {"id": "y", "action": "set", "device": "Spa", "value": 1}
        agent.record_command(act, refused, {"ok": False, "refused": "cooldown"})
        check("a refusal says so", act.recent()[0]["what"], "Spa on — refused by the Pi")

        check("the list is capped", len(act.recent()), 5)
        again = agent.Activity(path=path, keep=5)
        check("and survives the agent restarting",
              [e["what"] for e in again.recent()], [e["what"] for e in act.recent()])

        # A STAMP FROM THE FUTURE is dropped on load, as Guard drops one.
        with open(path, "w") as f:
            _json.dump({"events": [{"t": 9e12, "at": "x", "what": "w", "how": "h",
                                    "ok": True}]}, f)
        check("a stamp from the future is not believed",
              agent.Activity(path=path).recent(), [])
        with open(path, "w") as f:
            f.write("{not json")
        check("an unreadable file starts empty rather than stopping the agent",
              agent.Activity(path=path).recent(), [])

        # AND OVER HTTP, which is what the page actually receives.
        prev = agent.CLAIM_PORT
        agent.CLAIM_PORT = 0
        try:
            srv = agent.claim_server(agent.Claims(), activity=again)
        finally:
            agent.CLAIM_PORT = prev
        _th.Thread(target=srv.handle_request, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}/activity"
        with _ur.urlopen(url, timeout=5) as r:
            body = _json.loads(r.read())
        srv.server_close()
        check("GET /activity answers the list", len(body.get("events", [])), 5)
        check("with only what a page needs",
              sorted({k for e in body["events"] for k in e}),
              ["at", "how", "ok", "what"])
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ONE module object for bin/backup, shared by the detector and by the breaker
# in checks_structure. Loading it twice gives two objects, and a breaker that
# patches the one the detector does not hold is a breaker that cannot fire —
# which is how this detector first went green against a deliberately broken
# backup. dont_write_bytecode because bin/ is an inventory of entry points and
# a __pycache__ directory appearing in it is a finding against that inventory.
_BACKUP = None


def load_backup():
    """bin/backup as an importable module, or None when bin/ is not here."""
    global _BACKUP
    if _BACKUP is None:
        import sys as _sys
        from importlib.machinery import SourceFileLoader
        from . import config
        script = os.path.join(config.root(), "bin", "backup")
        if not os.path.exists(script):
            return None
        prev = _sys.dont_write_bytecode
        _sys.dont_write_bytecode = True
        try:
            _BACKUP = SourceFileLoader("_poolhound_backup", script).load_module()
        finally:
            _sys.dont_write_bytecode = prev
    return _BACKUP


def t_the_backup_lands_somewhere_that_outlives_the_share():
    """A copy that dies with the thing it is copying is not a backup.

    Every irreplaceable file in this product lived in exactly one place: one
    Azure Files share, with no snapshot policy, no backup vault and no copy
    job. locking.migrate_columns does take a backup before it rewrites — and
    writes the .bak BESIDE the original, so the one copy the code makes is
    gone in precisely the case a backup is for.

    The destination guard is the half worth testing: pointing it inside the
    data directory would reproduce the defect while looking like a fix.
    """
    import tarfile
    import tempfile
    print("\n  backup — the copy outlives the share")

    bk = load_backup()
    if bk is None:
        skipped("the backup copies the history off the share", "no bin/ in this tree")
        return

    work = tempfile.mkdtemp(prefix="poolhound-bk-")
    data = os.path.join(work, "data")
    out = os.path.join(work, "out")
    os.makedirs(data)
    for f in ("samples.csv", "chemicals.csv", "audit.csv", "config.toml"):
        with open(os.path.join(data, f), "w") as fh:
            fh.write("ts,x\n")
    keep = os.environ.get("POOLHOUND_DATA")
    os.environ["POOLHOUND_DATA"] = data
    try:
        res = bk.run({}, out=out)
        with tarfile.open(res["path"]) as t:
            inside = sorted(t.getnames())
        check("it copies every file that was there", inside,
              ["audit.csv", "chemicals.csv", "config.toml", "samples.csv"])
        check("and names the ones that were not", "leslies.csv" in res["missing"], True)
        check("the tarball is outside the data directory",
              os.path.abspath(res["path"]).startswith(os.path.abspath(data)), False)

        # THE GUARD. A destination inside the share is the defect wearing the
        # fix's clothes, and it must refuse rather than succeed.
        refused = ""
        try:
            bk.run({}, out=os.path.join(data, "backups"))
        except SystemExit as e:                       # noqa: PERF203
            refused = str(e)
        check("a destination inside the share is refused", "REFUSING" in refused, True)

        newest, age = bk.newest(out)
        check("and the newest one is findable, for --check to grade",
              newest is not None and age is not None and age < 60, True)
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
        if keep is None:
            os.environ.pop("POOLHOUND_DATA", None)
        else:
            os.environ["POOLHOUND_DATA"] = keep


def t_the_run_log_records_a_name_and_never_an_object():
    """`by` says who started a run. It must not be able to say anything else.

    MEASURED, in production. bin/backup called `runlog.Run("backup", cfg)` and
    `by` was the second positional parameter, so every nightly backup wrote
    forty characters of the config dict into runs.csv — a file that is
    append-only, is served on the Collection tab, and that bin/backup copies
    into every tarball. The truncation stopped short of any value, which is
    where the luck was: nothing about str() promises that.

    Two properties, because one without the other leaves the hole open. The
    call cannot be made that way any more, AND a wrong type that arrives by
    some other route is recorded as its type rather than its contents.
    """
    from . import runlog, render
    print("\n  runlog — `by` is a name, not whatever was passed")

    d = tempfile.mkdtemp()
    prev_env, prev_data = os.environ.get("POOLHOUND_DATA"), render.DATA
    try:
        os.environ["POOLHOUND_DATA"] = d
        render.DATA = d

        # THE EXACT SLIP, which must now be impossible rather than silent.
        cfg = {"waterguru": {"credentials": "/run/secrets/wg"},
               "path": "/mnt/poolhound"}
        refused = False
        try:
            runlog.Run("backup", cfg)                  # noqa: B018
        except TypeError:
            refused = True
        check("a config passed where `by` sits is a TypeError, not a row",
              refused, True)

        # AND THE BACKSTOP, for a wrong type arriving some other way.
        runlog.record("backup", "ok", by=cfg)
        rows = [r for r in runlog.rows() if r["tool"] == "backup"]
        got = rows[0]["by"] if rows else "(nothing recorded)"
        check("a dict is recorded as its type", got, "invalid:dict")
        check("and none of its contents reach the row",
              "credentials" in got or "secrets" in got, False)

        runlog.record("backup", "ok", by="cron")
        rows = [r for r in runlog.rows() if r["tool"] == "backup"]
        check("an ordinary name is still written through",
              rows[0]["by"] if rows else "(nothing recorded)", "cron")
    finally:
        shutil.rmtree(d, ignore_errors=True)
        render.DATA = prev_data
        if prev_env is None:
            os.environ.pop("POOLHOUND_DATA", None)
        else:
            os.environ["POOLHOUND_DATA"] = prev_env


def _refresh_block():
    """The page code that renders what a collector printed.

    Sliced from the real TEMPLATE rather than copied, because a second copy of
    the thing under test passes while the shipped code is wrong.
    """
    import re
    from . import render
    t = render.TEMPLATE
    i = t.index("menu.querySelectorAll('.rsrc').forEach")
    j = t.index("})();", i)
    # Block comments only. A `//` stripper would have to know about string
    # literals to avoid eating a URL, and getting that wrong fails GREEN.
    return re.sub(r"/\*.*?\*/", "", t[i:j], flags=re.S)


def t_what_a_lab_said_is_never_markup():
    """Collector output reaches the page as text, never as HTML.

    /api/refresh runs each collector and returns the last line it printed.
    That line is frequently the vendor's own words — an error body from
    Leslie's, a message from the WaterGuru API, the text of an exception that
    quotes a response. All of it was going into msg.innerHTML by string
    concatenation, on the authenticated page, so a lab could put a <script>
    tag on the Refresh menu of the one build that holds the controls.

    The invariant is the whole block rather than the two lines that
    interpolated: a result gains a field, somebody renders it the way the
    lines beside it are rendered, and the exception is back. textContent and
    createTextNode cannot be made to parse markup, so there is no escaping
    function here to forget to call.
    """
    print("\n  refresh — a lab's words are text, not markup")
    block = _refresh_block()
    check("the block that renders collector output uses no innerHTML",
          block.count("innerHTML"), 0)
    check("the collector's message goes in as a text node",
          "createTextNode(' ' + x.message + ' ')" in block, True)
    check("and so does a thrown error",
          "bad.textContent = e.message || e" in block, True)


def t_the_message_the_page_shows_is_the_one_a_lab_can_write():
    """The claim above is only worth anything if the text is really theirs.

    Named so the next person does not have to take the threat model on trust:
    server.py takes the LAST LINE of the collector's stdout, or of its stderr
    when stdout is empty, and OSError's str(). A collector reporting what a
    vendor returned is the ordinary case, not a contrived one.
    """
    import inspect
    from . import server
    print("\n  refresh — the message is third-party text by construction")
    src = inspect.getsource(server)
    i = src.index('"source": key, "label": spec["label"], "ok": r.returncode == 0')
    nearby = src[i:i + 400]
    check("the message is the collector's own last line of output",
          "out[-1].strip()" in nearby, True)
    check("falling back to stderr, which is where a vendor error lands",
          "err[-1].strip()" in nearby, True)


def _run(tool, outcome, minutes_ago, detail=""):
    import datetime as _dt
    at = (_dt.datetime.now().astimezone()
          - _dt.timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S%z")
    return {"at": at, "tool": tool, "outcome": outcome, "detail": detail,
            "added": "", "seconds": "1.0", "alerts": "0", "sent": "0",
            "undelivered": "0", "by": "cron"}


def t_a_source_that_never_ran_is_not_a_source_that_is_fine():
    """"never run" is the most broken a collector can be, and it read as green.

    The banner counted ("failed", "late") and so did the nav badge, so a card
    whose own pill said "never run" sat directly under "Every scheduled source
    has run recently and none reported a failure". Two copies of the same
    omission look exactly like one correct rule, which is why the set is a
    seam now rather than a literal in two files.
    """
    from . import collection as C
    print("\n  collection — a source that never ran needs attention")

    check("never is in the set that needs a person",
          "never" in C.NEEDS_ATTENTION, True)
    cards = C.summary([])                     # no runs at all, ever
    states = {c["state"] for c in cards}
    check("with no run log every card is 'never'", states, {"never"})
    broken = [c for c in cards if c["state"] in C.NEEDS_ATTENTION]
    check("and every one of them is counted as needing attention",
          len(broken), len(cards))


def t_a_skip_is_not_a_success():
    """A skipped run collected nothing, and it rendered as the green pill.

    `_state` returned ("ok", "skipped …") so the tab showed "working".
    """
    from . import collection as C
    print("\n  collection — a skip has its own word")

    runs = [_run("wg-collect", "skipped", 5, "already running"),
            _run("wg-collect", "ok", 30)]
    card = [c for c in C.summary(runs) if c["tool"] == "wg-collect"][0]
    check("the state is not ok", card["state"], "skipped")
    check("the pill says so rather than 'working'",
          "skipped" in C._pill(card["state"], card["text"]), True)
    check("and it does not say working",
          ">working<" in C._pill(card["state"], card["text"]), False)
    # A LONE SKIP IS NOT AN ALARM. The ordinary one means the previous run is
    # still going; a PATTERN of them shows up as `late`, which is the check
    # below. Grading one skip as broken is how a status strip stops being read.
    check("one skip beside a recent run is not an alarm",
          card["state"] in C.NEEDS_ATTENTION, False)


def t_a_wall_of_skips_is_not_freshness():
    """The forty-hour outage, as this tab would have graded it.

    collect.sh skipped every job for forty hours when the share failed to
    mount — 86 logged skips, one every thirty minutes. Each one wrote a row
    with a fresh `at` and collected nothing, and lateness was measured from
    the most recent ATTEMPT, so the gap never grew and nothing was ever late.
    Lateness is measured from the last attempt that was not a skip.
    """
    from . import collection as C
    print("\n  collection — skips do not hold the clock fresh")

    sched = {"wg-collect": ("every hour", 3600)}
    # Forty hours of half-hourly skips over one real run before them.
    runs = ([_run("wg-collect", "skipped", m, "share not mounted")
             for m in range(5, 40 * 60, 30)]
            + [_run("wg-collect", "ok", 40 * 60 + 10)])
    state, text = C._state(runs[0], sched["wg-collect"],
                           next(r for r in runs if r["outcome"] != "skipped"))
    check("a wall of skips over a stale run reads as late", state, "late")
    check("and the text names the last COMPLETED run, not the last attempt",
          "last completed a run" in text, True)
    check("late is something a person is told about",
          state in C.NEEDS_ATTENTION, True)

    # AND A TOOL THAT HAS ONLY EVER SKIPPED has never collected anything.
    only = [_run("leslies", "skipped", 5, "share not mounted")]
    state2, text2 = C._state(only[0], ("every day", 86400), None)
    check("a tool that has only ever skipped has never run", state2, "never")
    check("and that is counted", state2 in C.NEEDS_ATTENTION, True)


def t_the_offbox_copy_refuses_a_destination_that_dies_with_the_host():
    """"Off-box" that is another directory on the same disk is not off-box.

    THE THIRD OPEN ITEM in docs/THREAT_MODEL.md: the nightly tarball lands on
    the host's local disk, which fails separately from the Azure Files share
    and not separately from the VM. A second destination closes it — and
    reopens, one level out, the exact defect this file already refuses one
    version of. A backup directory inside the data directory is a copy that
    dies with the thing it is copying; a second directory on the same
    filesystem is a copy that dies with the host it was meant to outlive, and
    it looks configured, passes every check that counts tarballs, and is
    useless in the only case it exists for.

    st_dev rather than string comparison, because the ways to arrive at one
    filesystem under two names — a bind mount, a symlink, a second path into
    the same volume — are exactly the ways somebody arrives at this by
    accident.

    AND AN UNSET DESTINATION IS NOT A FAILURE, which is the other half. Most
    installs have not chosen one; that state has to be cheap and has to read
    differently from a mount that stopped accepting writes, because the second
    one means somebody believes there is a copy off this host and there is not.
    """
    import tarfile
    import tempfile
    print("\n  backup — a second copy, or an honest account of not having one")

    bk = load_backup()
    if bk is None:
        skipped("the off-box copy refuses a destination on the same disk",
                "no bin/ in this tree")
        return

    work = tempfile.mkdtemp(prefix="poolhound-off-")
    data = os.path.join(work, "data")
    out = os.path.join(work, "out")
    os.makedirs(data)
    with open(os.path.join(data, "samples.csv"), "w") as fh:
        fh.write("ts,x\n")
    keep = os.environ.get("POOLHOUND_DATA")
    os.environ["POOLHOUND_DATA"] = data
    try:
        check("unset is not configured, and says so",
              bk.copy_offbox("anything", {}, dest=""),
              ("", "not configured"))

        res = bk.run({}, out=out)
        path = res["path"]
        check("an ordinary run reports having no off-box copy",
              res["offbox_why"], "not configured")

        _dest, why = bk.copy_offbox(path, {}, dest=os.path.join(data, "off"))
        check("a destination inside the share is refused",
              why, "is inside the data directory")

        # The same filesystem as the history. Under a temp directory both
        # paths are on one device, which is what makes this testable at all.
        _dest, why = bk.copy_offbox(path, {}, dest=os.path.join(work, "beside"))
        check("AND SO IS ONE ON THE SAME FILESYSTEM AS THE HISTORY",
              "same filesystem as the history" in why, True)

        # A real second destination. /dev/shm on Linux and /tmp under a
        # different device are not portable, so the honest version of this
        # case is to prove the refusals and then prove the copy itself with
        # the device test stubbed — the alternative is a case that only runs
        # on a machine with two filesystems mounted where this expects them,
        # which is the shape of a gate that passes where it was written.
        real = bk._same_device
        bk._same_device = lambda a, b: False
        try:
            dest = os.path.join(work, "elsewhere")
            got, why = bk.copy_offbox(path, {}, dest=dest)
            check("a genuine second destination is written", why, "")
            check("and holds the same tarball",
                  os.path.basename(got), os.path.basename(path))
            with tarfile.open(got) as t:
                check("which still opens, and holds the history",
                      t.getnames(), ["samples.csv"])
            check("with nothing left half-copied beside it",
                  [f for f in os.listdir(dest) if f.startswith(".")], [])
        finally:
            bk._same_device = real

        _dest, why = bk.copy_offbox(path, {}, dest="/proc/poolhound/nope")
        check("a destination that cannot be created is reported, not raised",
              bool(why), True)
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
        if keep is None:
            os.environ.pop("POOLHOUND_DATA", None)
        else:
            os.environ["POOLHOUND_DATA"] = keep
