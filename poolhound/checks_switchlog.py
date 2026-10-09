"""Who switched what, and the four ways that answer can be wrong.

A wrong answer here is not a wrong-looking page: it tells somebody their pool
did something it did not do, or credits their own action to a stranger. Every
case below is one the implementation could plausibly get wrong, and two of them
are failures the panel's own data makes easy -- a blank reading read as "off",
and a command whose confirming sample lands in the next interval.
"""
import datetime as dt

from . import switchlog
from .selftest import check

# A sample, with every switch column the derivation looks at.
_OFF = {"pump": "0", "spa": "0", "sheer": "0", "pool_light": "0",
        "spa_light": "0"}


def _s(ts, **on):
    r = dict(_OFF)
    r.update(on)
    r["ts"] = ts
    return r


def _cmd(at, device, detail="set=1", by="someone@example.com"):
    return {"at": at, "by": by, "action": "control", "object": device,
            "detail": detail, "id": "x"}


def t_a_change_between_two_samples_is_one_event():
    """The ordinary case, and the shape of the answer.

    Two samples, one circuit different. One event, timed at the sample that
    reported it, carrying the pair it was seen across -- because "07:43" alone
    would claim a precision fifteen-minute sampling does not have.
    """
    print("\n  switchlog — a change the samples witnessed")
    evs = switchlog.events([
        _s("2026-10-08T07:28:00-0700"),
        _s("2026-10-08T07:43:00-0700", pool_light="1"),
    ])
    check("one event", len(evs), 1)
    check("named from the device vocabulary", evs[0]["label"], "Pool light")
    check("and it went on", evs[0]["word"], "on")
    check("timed at the sample that reported it",
          evs[0]["at"][:19], "2026-10-08T07:43:00")
    check("and it carries the interval it was seen across",
          evs[0]["between"],
          ["2026-10-08T07:28:00-0700", "2026-10-08T07:43:00-0700"])
    check("credited to the panel, since no command accounts for it",
          evs[0]["how"], switchlog.ELSEWHERE)
    check("and does not claim an exact time", evs[0]["exact"], False)


def t_a_blank_reading_is_not_off():
    """The failure the panel's own data makes easy.

    Several columns are blank when the pump is off -- the cell reports no
    salinity without flow, and a circuit the panel says nothing about reads
    empty rather than "0". Treated as off, a gap in the record invents a
    switch-off at the start of it and a switch-on at the end: two events, from
    one absence, both of them fiction.
    """
    print("\n  switchlog — an absent reading is not an off one")
    evs = switchlog.events([
        _s("2026-10-08T07:00:00-0700", pool_light="1"),
        _s("2026-10-08T07:15:00-0700", pool_light=""),
        _s("2026-10-08T07:30:00-0700", pool_light="1"),
    ])
    check("a gap in the record invents nothing", evs, [])
    # And the other direction: a reading that ARRIVES after a gap is not a
    # switch either, because nothing says what it was doing in between.
    evs = switchlog.events([
        _s("2026-10-08T07:00:00-0700", pool_light=""),
        _s("2026-10-08T07:15:00-0700", pool_light="1"),
    ])
    check("nor does the first real reading after one", evs, [])


def t_a_poolhound_command_is_credited_to_the_person_who_sent_it():
    """A command in the gap is the change, with its own exact time.

    The margin matters and is the one attribution error worth caring about: a
    command issued seconds BEFORE the earlier sample is still this command --
    the panel acts in about a second and the next sample is what catches it --
    and crediting it to "the panel" reports somebody's own action back to them
    as a stranger's.
    """
    print("\n  switchlog — attribution is a join, not a guess")
    samples = [_s("2026-10-08T07:28:00-0700"),
               _s("2026-10-08T07:43:00-0700", spa="1")]
    evs = switchlog.events(
        samples, [_cmd("2026-10-08T07:40:00-0700", "Spa")])
    check("the command in the gap is the change", evs[0]["how"], "poolhound")
    check("credited to the person", evs[0]["by"], "someone@example.com")
    check("at the command's own time, not the sample's",
          evs[0]["at"][:19], "2026-10-08T07:40:00")
    check("and that time is exact", evs[0]["exact"], True)

    # Just before the window, inside the margin.
    evs = switchlog.events(
        samples, [_cmd("2026-10-08T07:27:00-0700", "Spa")])
    check("a command just before the window still counts",
          evs[0]["how"], "poolhound")

    # Far outside it, and for a different device.
    evs = switchlog.events(
        samples, [_cmd("2026-10-08T06:00:00-0700", "Spa")])
    check("one from an hour earlier does not", evs[0]["how"],
          switchlog.ELSEWHERE)
    evs = switchlog.events(
        samples, [_cmd("2026-10-08T07:40:00-0700", "Aux_1")])
    check("nor does one for another circuit", evs[0]["how"],
          switchlog.ELSEWHERE)
    evs = switchlog.events(
        samples, [dict(_cmd("2026-10-08T07:40:00-0700", "Spa"),
                       action="control.ack")])
    check("and an acknowledgement is not a second command",
          evs[0]["how"], switchlog.ELSEWHERE)


def t_a_door_that_claims_a_change_is_named():
    """The product records which local route switched something, and this uses it.

    `commands.DOORS` allowlists local pages that may name themselves, the agent
    POSTs a settled panel change, and `record_panel_change` writes
    `panel.change` with the columns that moved and `by = door:<name>` where one
    claimed it. This file ignored those rows while the panel built on it said
    the origins could not be told apart -- with six of them sitting in the live
    audit log.

    A claim is not proof and must not become an identity: the `door:` prefix is
    the whole point of the allowlist. And a `panel.change` row that nothing
    claimed adds only confirmation, so it must NOT promote the row out of "the
    panel or AqualinkD" -- which would turn "the agent saw this" into "a door
    did this".
    """
    print("\n  switchlog — a door that claims a change is named")
    now = dt.datetime.now().replace(second=0, microsecond=0)
    f = "%Y-%m-%dT%H:%M:%S%z"
    t0 = (now - dt.timedelta(minutes=30)).astimezone()
    t1 = (now - dt.timedelta(minutes=15)).astimezone()
    rows = [_s(t0.strftime(f)), _s(t1.strftime(f), pool_light="1")]

    def claim(by, obj="pool_light", shift=20):
        return [{"at": (t1 - dt.timedelta(seconds=shift)).strftime(f),
                 "by": by, "action": "panel.change", "object": obj,
                 "detail": "seen on the panel", "id": ""}]

    e = switchlog.events(rows)[0]
    check("with no claim it is the panel or AqualinkD", e["how"],
          switchlog.ELSEWHERE)
    e = switchlog.events(rows, claim("door:pool.html"))[0]
    check("a door's claim names the door", e["how"], "door:pool.html")
    check("and never as a bare identity", e["by"].startswith("door:"), True)
    check("dated by the claim, not the sample",
          e["at"][11:19], (t1 - dt.timedelta(seconds=20)).strftime("%H:%M:%S"))
    e = switchlog.events(rows, claim("panel"))[0]
    check("an unclaimed panel row promotes nothing", e["how"],
          switchlog.ELSEWHERE)
    # A claim about a DIFFERENT column is not about this change.
    e = switchlog.events(rows, claim("door:pool.html", obj="spa_temp"))[0]
    check("a claim about another column is not this one", e["how"],
          switchlog.ELSEWHERE)
    # And one from an hour before the gap is not either.
    e = switchlog.events(rows, claim("door:pool.html", shift=3600))[0]
    check("nor is one from outside the window", e["how"],
          switchlog.ELSEWHERE)
    # The filter has to be able to select what the rows can hold.
    from . import commands
    check("every allowlisted door is offerable as a filter",
          [d for d in commands.DOORS if f"door:{d}" not in switchlog.HOWS], [])


def t_rows_out_of_order_do_not_invent_switching():
    """The agent re-pushes after a reconnect, so a row arriving late is normal.

    Walked in file order rather than in time order, a re-push puts an older
    sample after a newer one and every circuit that changed in between reads as
    having changed twice -- once forwards and once back. The same defect as
    "newest(), not the last row appended", one layer down.
    """
    print("\n  switchlog — file order is not time order")
    evs = switchlog.events([
        _s("2026-10-08T07:43:00-0700", pump="1"),
        _s("2026-10-08T07:13:00-0700"),
        _s("2026-10-08T07:28:00-0700"),
    ])
    check("one event, not three", len(evs), 1)
    check("and it is the real one", evs[0]["at"][:19],
          "2026-10-08T07:43:00")
    check("newest first", [e["at"][:16] for e in switchlog.events([
        _s("2026-10-08T07:00:00-0700"),
        _s("2026-10-08T07:15:00-0700", pump="1"),
        _s("2026-10-08T07:30:00-0700"),
    ])], ["2026-10-08T07:30", "2026-10-08T07:15"])


def t_a_circuit_the_panel_does_not_report_is_skipped_by_its_column():
    """Aux_4 and Aux_5 have no column, and that is a field rather than a name.

    commands.SWITCHES says so with `column: None`. Skipping them by name here
    would be a second list of which circuits the panel reports, and the next
    circuit added without a column would be read against a column that does
    not exist -- which is a KeyError on the Control tab, or worse, a `.get`
    returning "" on every row and a log that says nothing ever changes.
    """
    print("\n  switchlog — the vocabulary decides, not a second list")
    from . import commands
    no_col = [d for d, s in commands.SWITCHES.items() if not s.get("column")]
    check("some circuits genuinely have no column", bool(no_col), True)
    # A sample carrying a column NAMED like one of them changes nothing.
    rows = [dict(_s("2026-10-08T07:00:00-0700"), **{d: "0" for d in no_col}),
            dict(_s("2026-10-08T07:15:00-0700"), **{d: "1" for d in no_col})]
    check("and a column named after one is not read", switchlog.events(rows), [])
    # Every column the derivation DOES read is a real sample column.
    from . import aqualink
    read = [s["column"] for s in commands.SWITCHES.values() if s.get("column")]
    check("every column it reads exists in the samples",
          sorted(c for c in read if c not in aqualink.COLS), [])


def t_the_summary_counts_both_origins():
    """The figure that says how little of this comes through poolhound.

    It is the whole reason the exact events stay in the payload after the table
    stops drawing them: on this deployment the honest answer is "one of these
    was us", and a count computed from the rows on screen would have said zero.
    """
    print("\n  switchlog — the summary counts what the table does not draw")
    samples = [_s("2026-10-08T07:00:00-0700"),
               _s("2026-10-08T07:15:00-0700", spa="1"),
               _s("2026-10-08T07:30:00-0700", spa="1", pump="1")]
    evs = switchlog.events(samples,
                           [_cmd("2026-10-08T07:12:00-0700", "Spa")])
    check("two events", len(evs), 2)
    check("one of them ours", sum(1 for e in evs if e["exact"]), 1)
    note = switchlog.summary(evs)
    check("and the sentence says both", "1 through poolhound" in note, True)
    check("and names the other origin", switchlog.ELSEWHERE in note, True)
    check("an empty record gets no sentence", switchlog.summary([]), "")


def t_a_malformed_stamp_does_not_take_the_tab_down():
    """The history is irreplaceable and a reader of it must not be the hazard.

    One unparseable `ts` in four thousand rows is one event that cannot be
    placed. It is not a Control tab that will not load, and it is not a 500
    from /api/commands -- which is the endpoint the pending-command poll and
    the agent banner also hang off.
    """
    print("\n  switchlog — one bad row is one bad row")
    evs = switchlog.events([
        _s("2026-10-08T07:00:00-0700"),
        _s("not a timestamp", pump="1"),
        _s("2026-10-08T07:30:00-0700", pump="1"),
    ])
    check("the bad row is dropped, the real change kept", len(evs), 1)
    check("and it is timed from a row that parses",
          evs[0]["at"][:19], "2026-10-08T07:30:00")
    check("an empty history is empty, not an error",
          switchlog.events([]), [])
    check("and so is a single sample",
          switchlog.events([_s("2026-10-08T07:00:00-0700")]), [])


def t_a_stored_switch_off_does_not_read_as_an_on():
    """The durable log inverted every switch-off, and only after a restart.

    audit.csv keeps a command as `detail`: "set=1", "set=0". The rebuild split
    on "=" and handed describe() the STRING "0" -- and describe() asks whether
    the value is truthy, where "0" is TRUE. So the half of the table that
    survives a restart reported every switch-OFF as ON, while the session half,
    which keeps the original command object with a real 0 in it, was right.
    Correct until the restart this half exists for, and then silently wrong.
    """
    print("\n  switchlog — a stored command rebuilds to what was sent")
    from . import commands
    check("set=0 is off",
          commands.describe(commands.from_detail("Filter_Pump", "set=0")),
          "Filter pump off")
    check("set=1 is on",
          commands.describe(commands.from_detail("Filter_Pump", "set=1")),
          "Filter pump on")
    check("a setpoint keeps its number",
          commands.describe(commands.from_detail("Pool_Heater", "setpoint=88")),
          "Pool heater to 88")
    check("an action with no value is not 'to None'",
          commands.describe(commands.from_detail("Filter_Pump", "resync")),
          "resync the panel")
    check("and a bare action reads as itself",
          commands.describe(commands.from_detail("Filter_Pump", "wiggle")),
          "Filter pump wiggle")
    # The types, not just the sentences: `value` is what every other reader of
    # a command gets, and a string "0" is the bug one layer down.
    check("the value comes back as a number",
          commands.from_detail("Filter_Pump", "set=0")["value"], 0)
    check("and a textual boolean is a boolean",
          commands.from_detail("Filter_Pump", "set=false")["value"], False)


def _samples_over(hours, col="pump"):
    """One sample an hour, with `col` toggling, ending now."""
    now = dt.datetime.now().replace(minute=0, second=0, microsecond=0)
    out = []
    for h in range(hours, -1, -1):
        t = now - dt.timedelta(hours=h)
        out.append(_s(t.astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
                      **{col: str(h % 2)}))
    return out


def t_a_filter_searches_the_whole_record_not_a_page():
    """The reason the merge is in Python at all.

    Three sources feed this table and each was capped separately in the page.
    A filter applied there searches whatever slice the page happens to hold and
    then reports a count for the record -- "3 results" out of a number it never
    looked at. `matched` and `total` are both returned so the page can say
    which is which.
    """
    print("\n  switchlog — the filter sees everything, the page sees a page")
    rows = _samples_over(200)
    a = switchlog.activity(rows, per=50)
    check("the record is bigger than a page", a["total"] > 50, True)
    check("a page holds per rows", len(a["rows"]), 50)
    check("and says what it is a page of", a["matched"], a["total"])
    check("pages are counted from the matched set",
          a["pages"], -(-a["matched"] // 50))
    # A filter that matches a handful out of hundreds must find all of them,
    # not the ones that happened to be on page one.
    aud = [_cmd("2026-01-01T00:00:00-0700", "Spa")]
    b = switchlog.activity(rows, aud, how="poolhound")
    check("filtering finds the one row out of hundreds", b["matched"], 1)
    check("and total still counts the whole record",
          b["total"], a["total"] + 1)
    c = switchlog.activity(rows, aud, how=switchlog.ELSEWHERE)
    check("the other origin matches all the rest", c["matched"], a["total"])
    check("and the two partition the record",
          b["matched"] + c["matched"], b["total"])


def t_a_filter_value_nothing_can_hold_is_not_applied():
    """An empty table is a claim, and a query string must not be able to make it.

    device=nonsense would filter every row away and render "nothing happened" —
    about a pool that had been running all week. Unknown values are dropped, so
    a mistyped parameter shows the unfiltered record rather than a lie.
    """
    print("\n  switchlog — a query string cannot empty the record")
    rows = _samples_over(50)
    full = switchlog.activity(rows)["matched"]
    check("the record is not empty", full > 0, True)
    for bad in ({"device": "nonsense"}, {"how": "junk"}, {"days": "abc"},
                {"device": "../etc/passwd"}, {"how": "poolhound' OR 1=1"}):
        got = switchlog.activity(rows, **bad)
        check(f"{list(bad)[0]}={list(bad.values())[0]!r} is ignored",
              got["matched"], full)
    # And a value that IS real still filters.
    check("a real device still narrows it",
          switchlog.activity(rows, device="Spa")["matched"], 0)


def t_the_page_number_is_clamped_at_both_ends():
    """Page 0 and page 99 are both things a URL can say.

    page=0 slices from -per, which hands back the LAST page while reporting
    itself as the first. page past the end returns nothing, which reads as an
    empty record rather than as a page that does not exist.
    """
    print("\n  switchlog — the page number is clamped, not trusted")
    rows = _samples_over(200)
    a = switchlog.activity(rows, page=0)
    check("page 0 is page 1", a["page"], 1)
    check("and it is the newest rows", a["from"], 1)
    z = switchlog.activity(rows, page=999)
    check("page 999 is the last page", z["page"], z["pages"])
    check("and it is not empty", len(z["rows"]) > 0, True)
    check("the window it reports is the window it returned",
          z["to"] - z["from"] + 1, len(z["rows"]))
    check("a nonsense page is page 1",
          switchlog.activity(rows, page="; drop")["page"], 1)


def t_a_command_appears_once_not_twice():
    """One press, one row.

    A poolhound command is in audit.csv AND is witnessed by the samples as a
    state change. Both drawn, the table reports one person's one action twice,
    a minute apart, as if they had pressed it again.
    """
    print("\n  switchlog — one press is one row")
    now = dt.datetime.now().replace(second=0, microsecond=0)
    t0 = now - dt.timedelta(minutes=30)
    t1 = now - dt.timedelta(minutes=15)
    f = "%Y-%m-%dT%H:%M:%S%z"
    rows = [_s(t0.astimezone().strftime(f)),
            _s(t1.astimezone().strftime(f), spa="1")]
    aud = [_cmd((t1 - dt.timedelta(minutes=2)).astimezone().strftime(f),
                "Spa", "set=1")]
    a = switchlog.activity(rows, aud)
    spa = [r for r in a["rows"] if r["device"] == "Spa"]
    check("one row for the spa", len(spa), 1)
    check("and it is the command, with its author", spa[0]["by"],
          "someone@example.com")
    check("named as poolhound's", spa[0]["how"], "poolhound")
    check("and it carries a date as well as a time",
          len(spa[0]["when"]), 16)


def t_the_limit_keeps_the_newest_and_the_page_says_how_many():
    """Never truncate without saying so -- and truncate the right end.

    Sliced before the sort, a limit keeps the OLDEST events, so a pool with
    years of history would show a Control tab whose most recent entry was from
    the year it was installed.
    """
    print("\n  switchlog — a limit keeps the newest")
    rows = [_s(f"2026-10-08T{h:02d}:00:00-0700", pump=str(h % 2))
            for h in range(1, 12)]
    evs = switchlog.events(rows, limit=3)
    check("three of them", len(evs), 3)
    check("and they are the newest three",
          [e["at"][11:16] for e in evs], ["11:00", "10:00", "09:00"])
