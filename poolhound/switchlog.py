"""Every change of state a circuit made, whoever made it.

WHY THIS EXISTS

The Control tab's log is headed "What has been switched" and showed only what
was switched THROUGH POOLHOUND. Its own empty state said so -- "Nothing has
been switched through poolhound yet. The pool carries on running its own
schedule." -- which is an honest sentence under a dishonest heading, and on
this deployment it was the whole table: audit.csv held one control row, from
three weeks earlier, while the pump had started and stopped every day of those
three weeks.

So the page answered "what did poolhound do" to somebody asking "what happened
to my pool". The two are not close on an installation where most switching is
done at the panel, and the panel is where somebody standing next to the spa
switches things.

WHAT CAN AND CANNOT BE KNOWN, which is the whole design.

The panel has no log of its own and no way to be asked for one. The only record
of a circuit changing is the agent's fifteen-minute sample, which reports what
every circuit was doing at that instant. A change BETWEEN two samples is
therefore observable; WHEN inside that gap it happened is not, and neither is
WHO. Two things follow and both are stated on the page rather than papered
over:

  * an event's time is the sample that first REPORTED it, so it is accurate to
    the sampling interval and not to the second. `between` carries the pair.
  * a circuit switched on and off again inside one interval leaves no trace at
    all. Nothing here can find it, and a log that implied otherwise would be
    worse than one that admits it.

ATTRIBUTION IS A JOIN, NOT A GUESS. A command poolhound issued is in audit.csv
with an exact timestamp and a person's name. Where such a command lands in the
gap an observed change was seen across, the change is that command arriving and
is credited to them. Where none does, the change is credited to "the panel or
AqualinkD".

THREE LOCAL ORIGINS, AND THE PRODUCT ALREADY RECORDS WHICH. This file said the
server "genuinely cannot tell them apart", which was wrong twice over -- and
wrong in the direction that excuses the gap rather than describing it. Measured
on the Pi and in the live audit log:

  * the wall panel reaches the RS-485 bus directly and leaves nothing behind
    but the next sample. This one really is undetectable.
  * AqualinkD's own web interface is an HTTP request AqualinkD handles. It
    would name the command in its own log at INFO or DEBUG, and runs at NOTICE.
  * poolhound local, the optional page on the Pi, drives AqualinkD through a
    reverse proxy on the
    same Pi, so the proxy sees which route a command came through. Its access
    log is `output discard`.

Both logs are off for one reason, written into the proxy's own config: a steady
drip of small writes is what destroyed this Pi's first two SD cards, the same
constraint that keeps the agent's state in tmpfs.

BUT THERE IS A THIRD RECORD, AND IT IS OURS. `commands.DOORS` is an allowlist
of local pages that may name themselves, `server.record_panel_change` writes
`panel.change` rows carrying the columns that moved and `by = door:<name>`
where one did, and `_claims()` reads them. SIX of those rows were in the live
audit log while the panel built on top of them printed a paragraph about the
origins being indistinguishable. A claim also carries a timestamp of its own,
which beats the sample it would otherwise be dated by.

So the honest statement, and the one the page now makes: a door that says so is
named; the panel and an unclaimed AqualinkD look alike from this end.

ONE SOURCE FOR WHAT A CIRCUIT IS. The devices and the sample column each one
reports in are `commands.SWITCHES`, which is the vocabulary the server and the
agent already validate against, and the names come from
`commands.label_for()`, which honours `[circuits]` in config.toml. A second
list of circuits here would be a second list to forget to update -- and the
ones with `column: None` are skipped BY THAT FIELD rather than by name, because
the panel reports nothing about them and an absent reading is not an off one.
"""
import datetime as dt

from . import commands

# How far before the gap a command may sit and still be credited with the
# change seen at the end of it.
#
# A command issued at 07:27 whose sample pair is 07:28 -> 07:43 is this
# command: the panel acts in about a second, and the sample that catches it is
# the next one. Without the margin that command is credited to the panel, which
# is the one attribution error that matters -- it reports a person's own action
# back to them as somebody else's.
#
# Small on purpose. The gap is fifteen minutes and a wider margin starts
# reaching into the PREVIOUS gap, where a different change was already
# attributed; two minutes cannot cross a fifteen-minute interval.
MARGIN_S = 120

# What the panel-or-AqualinkD case is called, once, because it is printed in
# the log and counted in the summary and the two must not drift.
ELSEWHERE = "at the panel or through AqualinkD"


_WHEN = None


def _at(s):
    """An ISO stamp from a CSV, or None. Never raises on a row.

    DELEGATED TO render.when(), which owns parsing a stored timestamp: it knows
    both flavours this product stores -- local-with-offset from our own writers,
    UTC "Z" from the WaterGuru payload -- and it returns naive-local so every
    comparison in the product sits on the clock the pool actually runs on. This
    file held its own strptime/fromisoformat pair for about an hour and seams.py
    named it as a second copy on the first run, which is the gate earning its
    keep: the two would have disagreed about a "Z" stamp, and the panel is not
    the only writer of the file this reads.

    Imported lazily and cached. render.py pulls in help, brand and style, and
    importing all of that at the top of a module the SERVER imports at startup
    is a cost paid on every boot for a function used by one endpoint.

    The history is irreplaceable and a reader of it must not be the thing that
    falls over: a single malformed stamp is one event that cannot be placed,
    not a Control tab that will not load.
    """
    global _WHEN
    if _WHEN is None:
        from .render import when
        _WHEN = when
    return _WHEN(str(s or "").strip())


def _claims(audit_rows):
    """[(when, {columns}, by)] for the panel changes the agent reported.

    THE PRODUCT ALREADY RECORDS WHAT THIS FILE SAID WAS UNKNOWABLE. The agent
    notices a settled panel change and POSTs it; server.record_panel_change
    writes `panel.change` with the columns that moved and, where a door on the
    house LAN claimed it, `by = door:<name>` from the commands.DOORS allowlist.
    Six of those rows were sitting in the live audit log while the panel built
    on top of them printed a paragraph about nothing being able to tell the
    origins apart.

    A claim is not proof and is not treated as one: `door:` is a prefix that
    cannot be mistaken for an identity, which is the whole reason the allowlist
    exists. What it buys is the difference between "something switched this"
    and "poolhound local switched this", on the one record that can say so.
    """
    out = []
    for r in audit_rows or ():
        if (r.get("action") or "") != "panel.change":
            continue
        t = _at(r.get("at"))
        if t is None:
            continue
        cols = {c.strip() for c in str(r.get("object") or "").split(",")
                if c.strip()}
        out.append((t, cols, str(r.get("by") or "")))
    out.sort(key=lambda x: x[0])
    return out


def _commands(audit_rows):
    """[(when, device, detail, by)] for the control commands we issued.

    Acknowledgements are excluded: `control.ack` is the agent reporting back on
    a command already in this list, so counting it would credit one person's
    one action twice.
    """
    out = []
    for r in audit_rows or ():
        if (r.get("action") or "") != "control":
            continue
        t = _at(r.get("at"))
        if t is None:
            continue
        # The row's own stamp is kept alongside the parsed value: it is what an
        # attributed event is dated by, and it is the only spelling of that
        # instant anything wrote down.
        out.append((t, str(r.get("object") or ""), str(r.get("at") or ""),
                    str(r.get("by") or "")))
    out.sort(key=lambda x: x[0])
    return out


def events(samples, audit_rows=(), cfg=None, limit=None):
    """Newest first: every circuit change the samples witnessed.

    Each event is
        {at, until, between, device, label, to, word, how, by, exact}

    `at` is when the change was first REPORTED and `between` is the pair of
    samples it was seen across -- both, because the page has to be able to say
    "some time between 07:28 and 07:43" rather than implying 07:43.

    `exact` is True only for a change a poolhound command accounts for, where
    the command's own timestamp is known to the second. It is the flag the page
    uses to decide whether to show a time or an interval, so the display cannot
    claim a precision this function did not have.
    """
    rows = [r for r in (samples or ()) if _at(r.get("ts"))]
    rows.sort(key=lambda r: _at(r["ts"]))
    issued = _commands(audit_rows)
    claims = _claims(audit_rows)

    cols = [(dev, spec["column"]) for dev, spec in commands.SWITCHES.items()
            if spec.get("column")]
    out = []
    for prev, cur in zip(rows, rows[1:]):
        t0, t1 = _at(prev["ts"]), _at(cur["ts"])
        for dev, col in cols:
            a = str(prev.get(col, "")).strip()
            b = str(cur.get(col, "")).strip()
            # ONLY A REPORTED STATE COUNTS. A blank is "the panel said nothing
            # about this", which is not off -- reading it as off would invent a
            # switch-off at every gap in the record and a switch-on after it.
            if a not in ("0", "1") or b not in ("0", "1") or a == b:
                continue
            # THE STAMP IS CARRIED, NOT REBUILT. Formatting the parsed value
            # back out would mean this file owned a second spelling of how a
            # timestamp is written down, and render.when() returns naive-local
            # deliberately -- so the rebuilt string would have lost the offset
            # every row on disk carries.
            by, exact, at, order = ELSEWHERE, False, cur["ts"], t1
            how = ELSEWHERE
            # A DOOR'S CLAIM, WHERE THERE IS ONE. Checked before poolhound's own
            # commands only in the sense of being a separate question: this says
            # WHICH of the local routes, and the command check below says it was
            # not a local route at all. A `panel.change` row whose `by` is still
            # "panel" adds nothing but confirmation, so it is left alone -- the
            # honest answer there is the one this file already gave.
            for t, cols_, who in claims:
                if col not in cols_:
                    continue
                if t0 - dt.timedelta(seconds=MARGIN_S) < t <= t1:
                    if who.startswith("door:"):
                        by, how, at, order = who, who, \
                            t.strftime("%Y-%m-%dT%H:%M:%S%z"), t
                    break
            for t, obj, stamp, who in issued:
                if obj != dev:
                    continue
                if t0 - dt.timedelta(seconds=MARGIN_S) < t <= t1:
                    # The command's own time, which is known exactly, rather
                    # than the sample's, which is only when we found out.
                    by, exact, at, order = who or "poolhound", True, stamp, t
                    how = "poolhound"
                    break
            out.append({
                "at": at,
                "_order": order,
                "between": [prev["ts"], cur["ts"]],
                "device": dev,
                "label": commands.label_for(dev, cfg),
                "to": b,
                "word": "on" if b == "1" else "off",
                "how": how,
                "by": by,
                "exact": exact,
            })
    # SORTED ON THE PARSED VALUE, not on the string. Two rows written either
    # side of a daylight-saving change carry different offsets, and the later
    # instant is the smaller string -- so a string sort puts the autumn
    # changeover's hour in the wrong order once a year, which is exactly the
    # kind of bug nobody finds in March.
    #
    # AND SLICED AFTER. Sliced before, a limit keeps the OLDEST events: a pool
    # with years of history would open its Control tab on the year it was
    # installed.
    out.sort(key=lambda e: e["_order"], reverse=True)
    out = out[:limit] if limit else out
    for e in out:
        del e["_order"]
    return out


#: What `how` can be, and the order the page offers them in. One list, because
#: the filter's options and the values the rows actually carry have to be the
#: same set — a filter offering a value nothing holds reads as "no activity",
#: which is indistinguishable from a broken log.
#: Derived, not typed: a door that `commands.DOORS` allows can appear in a row,
#: so it has to appear in the filter. Typed out, adding a door would have given
#: the log a value the menu could not select — which reads as "that door has
#: never switched anything".
HOWS = (["poolhound"]
        + [f"door:{d}" for d in sorted(commands.DOORS)]
        + [ELSEWHERE])

#: The time windows the page offers, in days. None is "everything".
WINDOWS = [("1", "the last day", 1), ("7", "the last week", 7),
           ("30", "the last month", 30), ("all", "everything", None)]

PER_PAGE = 50


def _int(v, default):
    """An integer from a query string, or the default. Never raises."""
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def _display(t):
    """A date AND a time. The log reaches back through the whole history, and a
    column of clock times with no day in it is a log with its ordering taken
    out — the oldest row looks exactly like this afternoon's."""
    return t[:10] + " " + t[11:16] if len(t) >= 16 else t


def activity(samples, audit_rows=(), session=(), cfg=None, device="",
             how="", days=None, page=1, per=PER_PAGE):
    """Everything that happened, merged, filtered, sorted and paginated.

    THE MERGE IS HERE AND NOT IN THE BROWSER, and the reason is the filters.
    Three sources feed this table: the commands this process issued, the
    commands audit.csv kept, and the changes the samples witnessed. Each was
    capped separately and drawn in source order -- so a filter applied in the
    page would have searched the first fifty of one source and the first
    hundred and twenty of another, reported "3 results", and been wrong in a
    way nobody could see. A filter that silently searches part of the record is
    worse than no filter.

    So: merge everything, filter the whole of it, and paginate what is left.
    `matched` and `total` are both returned because the page has to be able to
    say "showing 50 of 212 matching, out of 274" -- which is the only honest
    version of a filtered, paginated table.

    THE WORDING IS BUILT HERE. The page draws cells and does not compose
    sentences; that is the same reason the control banners are rendered in
    Python and revealed by the script rather than assembled in it.
    """
    from . import commands
    rows = []

    # -- what this process issued, which is lost on a restart
    for h in session or ():
        res = h.get("result") or {}
        said = (res.get("refused") or res.get("error")
                or ("done" if res.get("detail") is None else str(res["detail"]))
                if h.get("result") else "sent, no reply yet")
        rows.append({"at": h.get("at") or "", "device": (h.get("cmd") or {}).get("device", ""),
                     "what": h.get("what") or "", "how": "poolhound",
                     "by": h.get("by") or "", "result": said,
                     "cls": "ok" if res.get("ok") else ("bad" if h.get("result") else ""),
                     "dim": False})

    # -- what audit.csv kept, which survives it
    acks = {r.get("object"): r for r in (audit_rows or ())
            if (r.get("action") or "") == "control.ack"}
    for r in audit_rows or ():
        if (r.get("action") or "") != "control":
            continue
        ack = acks.get(r.get("id")) if r.get("id") else None
        rows.append({
            "at": r.get("at") or "",
            "device": r.get("object") or "",
            "what": commands.describe(
                commands.from_detail(r.get("object") or "",
                                     r.get("detail") or ""), cfg),
            "how": "poolhound", "by": r.get("by") or "",
            "result": (ack.get("detail") or "no result reported") if ack
                      else "no reply recorded",
            "cls": "ok" if ack else "", "dim": True})

    # AN ACKNOWLEDGEMENT WHOSE COMMAND IS NOT HERE IS STILL EVIDENCE.
    #
    # The agent replies with the command's id, and audit.csv keeps the reply as
    # its own row. If the command itself is older than this window -- or was
    # issued by a process that has since restarted -- the reply has nothing to
    # pair with, and dropping it is the silent truncation this project refuses
    # everywhere else. It says what it is: something happened, and the command
    # that caused it is out of view.
    issued_ids = {r.get("id") for r in (audit_rows or ())
                  if (r.get("action") or "") == "control" and r.get("id")}
    for cid, ack in acks.items():
        if cid in issued_ids:
            continue
        rows.append({"at": ack.get("at") or "", "device": "",
                     "what": "a command from before this window",
                     "how": "poolhound", "by": ack.get("by") or "",
                     "result": ack.get("detail") or "no result reported",
                     "cls": "", "dim": True})

    # -- and what was switched without us, which is most of it
    for e in events(samples, audit_rows, cfg):
        if e["exact"]:
            # Already above, from audit.csv, with its acknowledgement. Drawing
            # both would report one person's one press twice.
            continue
        b = e["between"]
        rows.append({
            "at": e["at"], "device": e["device"],
            "what": f"{e['label']} → {e['word']}",
            "how": e["how"], "by": "—",
            "result": (f"first reported in the {b[1][11:16]} sample, "
                       f"so some time after {b[0][11:16]}"),
            "cls": "", "dim": True})

    # THE FILTERS COME OFF A QUERY STRING, so none of them may raise and none
    # may be believed. An unknown device or `how` is dropped rather than
    # applied: filtering on a value no row can hold returns an empty table,
    # which reads as "nothing happened" -- a lie, and one an unparseable
    # parameter should not be able to tell. `days` that is not a number is no
    # window at all.
    known = {d for d, _ in devices(cfg)}
    device = device if device in known else ""
    how = how if how in HOWS else ""
    days = _int(days, 0) or None

    total = len(rows)
    keep = rows
    if device:
        keep = [r for r in keep if r["device"] == device]
    if how:
        keep = [r for r in keep if r["how"] == how]
    if days:
        # Compared as datetimes, not as strings: rows either side of a
        # daylight-saving change carry different offsets and the later instant
        # is the smaller string.
        cut = dt.datetime.now() - dt.timedelta(days=days)
        keep = [r for r in keep if (_at(r["at"]) or dt.datetime.min) >= cut]
    keep.sort(key=lambda r: _at(r["at"]) or dt.datetime.min, reverse=True)

    per = max(1, min(500, _int(per, PER_PAGE) or PER_PAGE))
    pages = max(1, -(-len(keep) // per))
    # CLAMPED, not trusted. page=0 would slice from -per and hand back the LAST
    # page while claiming to be the first; a page past the end would return
    # nothing and read as an empty record.
    page = min(max(1, _int(page, 1) or 1), pages)
    start = (page - 1) * per
    for r in keep:
        r["when"] = _display(r["at"])
    # `note` summarises the WHOLE record, not the filtered view: it is the
    # answer to "where does this pool's switching come from", which a filter
    # must not be able to change.
    return {"rows": keep[start:start + per], "matched": len(keep),
            "total": total, "page": page, "pages": pages, "per": per,
            "note": summary(rows),
            "from": start + 1 if keep else 0,
            "to": min(start + per, len(keep))}


def devices(cfg=None):
    """(id, label) for every circuit this log can hold a row about.

    Derived from the vocabulary, so the filter cannot offer a circuit the
    derivation never looks at — nor miss one it does.
    """
    from . import commands
    return [(d, commands.label_for(d, cfg))
            for d, s in commands.SWITCHES.items() if s.get("column")]


def summary(rows):
    """One sentence about where the switching actually comes from.

    The reason this is computed rather than left to a reader counting rows: the
    answer on this deployment is that almost none of it comes from poolhound,
    and that is worth saying out loud on the page that offers the buttons.

    COUNTED ON `how`, OVER THE SAME ROWS THE TABLE HOLDS. It counted events and
    keyed on `exact`, which is a different population from the merged list --
    an event is a change the samples witnessed, and a row can also be a command
    that moved nothing, or an acknowledgement from before the window. On the
    real pool that was 66 against 67, printed two inches apart on the same
    panel, and a reader cannot tell a deliberate distinction from an
    off-by-one. Both numbers describe the merged rows now.
    """
    n = len(rows)
    if not n:
        return ""
    mine = sum(1 for r in rows if r.get("how") == "poolhound")
    return (f"{n} entr{'y' if n == 1 else 'ies'} in the record: "
            f"{mine} through poolhound, {n - mine} {ELSEWHERE}.")
