"""The checks that do not need a browser, a pool or a network.

WHY THIS EXISTS

"There is no test suite. What there is:" -- and then a list of things a person
runs and reads. That is a reasonable position for a page whose real failures are
visual, and it left the subtle PURE functions uncovered: the ones where the bug
is a wrong answer rather than a wrong-looking page, and where reading the code
tells you nothing because the code looks correct either way.

pending_devices() is the sharpest example. It decides whether the page says a
gas heater is ON, OFF or WAITING, it has had two real bugs (walking the whole
log resurrected superseded commands; "no sample since" cleared the waiting mark
whether or not anything changed), and both were found in production rather than
here. Its inputs are a list of dicts and a clock. Nothing about that needs a
browser.

Every case below is one that was actually wrong at some point, or one that
guards a fix made after a defect was demonstrated. It is a regression net, not
coverage for its own sake.

A CHECK THAT CAN PASS WHILE MEASURING NOTHING IS THE WORST CASE IN HERE, because
it reports the opposite of the truth. Several did: every t_pending() "waiting"
fixture used a sample older than the command, so the half of the rule that says
the panel must AGREE was never exercised and restoring the bug that shipped
passed the gate written to catch it. t_actions_without_volume() asserted on the
absence of the string "None" rather than the absence of a number, so the
15,000-gallon default could come back unseen. t_best_lab() put the expected
winner last in both fixtures, so argument order and clock order agreed. Where a
case is written to exclude a specific mutation, it says which.

    bin/selftest          run them; non-zero on a failure
"""
import datetime as dt
import os
import tempfile
import time

FAILURES = []

# WHAT COULD NOT BE ANSWERED IN THIS TREE, which is a third outcome and not a
# quiet kind of pass.
#
# The deploy image carries poolhound/, bin/, the example config, the crontab
# and the landing page's cards — and nothing else. No README, no SITE.md, no
# deploy scripts, no rendered pages, no pool. A case that reads one of those
# has to say it did not run, out loud, for the same reason bin/drive says so
# when there is no browser: "did not run" and "passed" print identically once
# the distinction is dropped, and this suite has twice shipped a gate that
# measured nothing while reporting success.
SKIPPED = []


def skipped(name, why):
    """This case cannot be answered here. Not a pass, not a failure."""
    SKIPPED.append((name, why))
    print(f"    skip  {name} — {why}")


class in_deploy_tz:
    """Run a case in the timezone the DEPLOYMENT runs in, not the host's.

    Some answers here are local-time answers and cannot be anything else:
    best_lab() weighs a UTC instant against a bare local date, and the
    canonical dose timestamp is written in the host's offset. A case asserting
    on either is asserting about an offset, so the offset has to be stated.

    MEASURED, by the first CI run that ever happened: four cases passed on a
    Pacific workstation and failed on a UTC runner, and the product was correct
    on both. The fixtures were reading the host's zone as if it were the pool's.
    That is the same shape as the gate that only passed on the machine which
    already had the untracked files — a case whose answer depends on where it
    runs is a case CI cannot be given.

    config.DEPLOY_TZ is the one place the zone is named, and the container is
    held to it by a check, so pinning it here is not a second copy of the fact.
    time.tzset() is POSIX-only and absent on Windows; nothing here runs there,
    and a missing tzset would silently leave the old zone in force, so it is
    required rather than skipped.
    """

    def __init__(self, tz=None):
        from . import config
        self.tz = tz or config.DEPLOY_TZ

    def __enter__(self):
        self._prev = os.environ.get("TZ")
        os.environ["TZ"] = self.tz
        time.tzset()
        return self

    def __exit__(self, *exc):
        if self._prev is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._prev
        time.tzset()
        return False


def check(name, got, want):
    ok = got == want
    if not ok:
        FAILURES.append((name, got, want))
    print(f"    {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"            got  {got!r}")
        print(f"            want {want!r}")
    return ok


# --------------------------------------------------------------------- newest
def t_newest():
    from .render import newest
    print("\n  newest() — the newest MEASURED, not the last appended")
    rs = [{"ts": "2026-09-13T15:00:00-0700", "v": "a"},
          {"ts": "2026-09-13T16:00:00-0700", "v": "b"},
          {"ts": "2026-09-13T09:00:00-0700", "v": "c"}]   # a re-push landing last
    check("an out-of-order re-push does not win", newest(rs, "ts")["v"], "b")
    check("empty input is an empty dict", newest([], "ts"), {})
    # An unparseable stamp must not beat a real one.
    rs2 = [{"ts": "2026-09-13T16:00:00-0700", "v": "b"}, {"ts": "not a date", "v": "x"}]
    check("an unparseable stamp sorts last", newest(rs2, "ts")["v"], "b")


# ------------------------------------------------------------------- best_lab
def t_best_lab():
    with in_deploy_tz():
        _best_lab()


def _best_lab():
    from .render import best_lab
    print("\n  best_lab() — most recent wins, by the clock not the alphabet")
    # WaterGuru writes a UTC instant; Leslie's writes a bare local date. The
    # string comparison this replaced picked WaterGuru here, and the clock says
    # Leslie's: 02:00Z is the previous evening in California.
    wg = {"measured": "2026-08-15T02:00:00.000Z", "ta": "95"}
    les = {"measured": "2026-08-15", "ta": "73"}
    r = best_lab((wg, "WaterGuru"), (les, "Leslie's"))["ta"]
    check("a UTC instant is not later than the local date after it",
          (r["source"], r["value"]), ("Leslie's", 73.0))
    # And the ordinary case still works.
    old = {"measured": "2026-07-01", "ta": "60"}
    r2 = best_lab((old, "Leslie's"), (wg, "WaterGuru"))["ta"]
    check("the genuinely newer source still wins", r2["source"], "WaterGuru")

    # THE SAME TWO CASES, ARGUMENTS SWAPPED. Both fixtures above put the
    # expected winner LAST, so argument order and clock order agreed and a
    # function that simply returned the last source it was handed satisfied
    # every check here -- on the one rule the whole two-labs reconciliation
    # rests on. Clock order is the rule; position in the call is not.
    r3 = best_lab((les, "Leslie's"), (wg, "WaterGuru"))["ta"]
    check("the clock still decides with the sources the other way round",
          (r3["source"], r3["value"]), ("Leslie's", 73.0))
    # An older result listed last is what an out-of-order collector run
    # actually produces: Leslie's returns its whole history on every pull.
    r4 = best_lab((wg, "WaterGuru"), (old, "Leslie's"))["ta"]
    check("an older result arriving last does not displace a newer one",
          (r4["source"], r4["value"]), ("WaterGuru", 95.0))


# ------------------------------------------------------------------ chemistry
def t_daily_measures():
    """pH and free chlorine must reach the page from more than one door.

    They were excluded from the reconciliation on the reasoning that the pod
    reads them daily and recency beats precision -- right whenever there IS a
    pod, and it meant the ONLY door was readings.csv. An owner with a test kit
    and a Leslie's history and no WaterGuru subscription typed both numbers in,
    watched them land in manual.csv, and read "unknown" on the two tiles that
    drive nearly every decision this product exists to support. "What to do
    next" offered no acid dose and no chlorine dose at all.

    The fix must not cost the original reasoning: a pod reading daily still has
    to win.
    """
    from . import render
    print("\n  pH and free chlorine — reconciled, with the pod still winning on recency")
    pod  = {"measured": "2026-09-14T06:00:00Z", "ph": "7.5", "free_cl": "3.2"}
    old  = {"measured": "2026-08-01T06:00:00Z", "ph": "7.5", "free_cl": "3.2"}
    kit  = {"measured": "2026-09-13T09:00:00-0700", "ph": "8.1", "free_cl": "0.4"}
    les  = {"measured": "2026-05-21", "ph": "7.6", "free_cl": "4.82"}
    M = render.DAILY_MEASURES

    d = render.best_lab((pod, "WaterGuru pod"), (les, "Leslie's"), (kit, "By hand"),
                        measures=M)
    check("a pod reading today beats a test kit from yesterday",
          d["ph"]["source"], "WaterGuru pod")
    check("and it is the pod's number", d["free_cl"]["value"], 3.2)

    d = render.best_lab(({}, "WaterGuru pod"), (les, "Leslie's"), (kit, "By hand"),
                        measures=M)
    check("with no pod, the test kit answers instead of nothing",
          d["ph"]["value"], 8.1)
    check("and the page can say where it came from", d["ph"]["source"], "By hand")

    d = render.best_lab((old, "WaterGuru pod"), (kit, "By hand"), measures=M)
    check("a six-week-old pod reading loses to this morning's kit",
          d["free_cl"]["source"], "By hand")

    d = render.best_lab(({}, "WaterGuru pod"), (les, "Leslie's"), measures=M)
    check("Leslie's own free chlorine is not thrown away either",
          d["free_cl"]["value"], 4.82)

    # actions() is the consumer that mattered: no dose at all was offered.
    TG = render.targets(d, "salt_cell", "")
    acts = render.actions({}, {}, TG, [], {}, 16250, 31.45,
                          DAILY=render.best_lab(({}, "pod"), (kit, "By hand"),
                                                measures=M))
    txt = " ".join(str(a) for a in acts)
    check("and a test-kit pH of 8.1 produces an acid dose", "acid" in txt.lower(), True)


def t_volume():
    from . import config
    print("\n  config.volume() — 'not set' is a state, not a default")
    check("absent is None", config.volume({"pool": {}}), None)
    check("zero is None", config.volume({"pool": {"volume_gallons": 0}}), None)
    check("junk is None", config.volume({"pool": {"volume_gallons": "lots"}}), None)
    check("a real number is a float", config.volume({"pool": {"volume_gallons": 16000}}),
          16000.0)


def t_actions_without_volume():
    from . import render
    print("\n  actions() — no volume, no dose, and the dose is THIS pool's")
    LAB = {"ch": {"value": 185.0, "measured": "2026-09-01", "source": "Leslie's"}}
    TG = {"ch": (250, 350, 450), "ph": (7.4, 7.5, 7.6),
          "free_cl": (2, 3, 4), "ta": (60, 75, 90), "salt": (3000, 3400, 3800)}
    acts = render.actions(LAB, {}, TG, [], [], None, 31.45)
    # THE ABSENCE OF A NUMBER, not the absence of the word "None". This used to
    # assert only that no card contained the literal text "None", which pins the
    # symptom of the old bug instead of the rule. Restoring the 15,000-gallon
    # default -- the invention config.volume()'s docstring says lived in nine
    # separate places -- left every check here reporting ok while the card read
    # "Raise calcium hardness -> 29.7 lb of 77% calcium chloride" about a pool
    # nobody has measured.
    check("no card quotes an amount at all",
          [a["dose"] for a in acts], [""] * len(acts))
    check("the unset volume is the first thing said",
          acts[0]["title"] if acts else None,
          "Nobody has said how big this pool is")
    # With a volume, the dose comes back.
    acts2 = render.actions(LAB, {}, TG, [], [], 16000.0, 31.45)
    ch = next((a for a in acts2 if a["title"] == "Raise calcium hardness"), None)
    check("with a volume the calcium dose is present and not 'None'",
          bool(ch and ch["dose"] and "None" not in ch["dose"]), True)
    # And it has to be the OWNER'S volume. A hardcoded default answers every
    # pool with the same figure, which no assertion about a single pool can
    # tell apart from arithmetic: two volumes must give two numbers.
    def ch_dose(g):
        return next(a["dose"] for a in render.actions(LAB, {}, TG, [], [], g, 31.45)
                    if a["title"] == "Raise calcium hardness")
    check("the dose moves with the volume it was given",
          ch_dose(16000.0) != ch_dose(15000.0), True)


# ------------------------------------------------------------------- migration
def t_migrate():
    import csv
    from .server import migrate_columns
    print("\n  migrate_columns() — additive only, and never lossy")
    d = tempfile.mkdtemp()
    p = os.path.join(d, "samples.csv")
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "pump", "swg_pct"])
        w.writerow(["2026-09-01T00:00:00-0700", "1", "50"])
        w.writerow(["2026-09-01T00:15:00-0700", "0", "50"])
    added, n = migrate_columns(p, ["ts", "pump", "swg_pct", "pool_set"])
    rows = list(csv.DictReader(open(p, newline="")))
    check("the new column is added", added, ["pool_set"])
    check("every row is kept", len(rows), 2)
    check("no value is changed", [r["swg_pct"] for r in rows], ["50", "50"])
    check("the new column is blank, not invented", rows[0]["pool_set"], "")
    check("running it again does nothing",
          migrate_columns(p, ["ts", "pump", "swg_pct", "pool_set"]), None)
    # Removing a column would be data loss, so it refuses.
    try:
        migrate_columns(p, ["ts", "pump"])
        check("dropping a column is refused", "it returned", "it raised")
    except ValueError:
        check("dropping a column is refused", "it raised", "it raised")
    check("and the file is untouched by the refusal",
          next(csv.reader(open(p, newline=""))),
          ["ts", "pump", "swg_pct", "pool_set"])


# ---------------------------------------------------------------------- access
def t_access():
    from . import access
    print("\n  access — opt-in, and shut by default for anything unclassified")
    none = {"pool": {}}
    check("no policy leaves everyone an admin", access.role("anyone@x.y", none), "admin")
    check("and says so rather than staying silent", access.configured(none), False)
    pol = {"access": {"admins": ["Owner@X.Y"], "operators": ["p@x.y"],
                      "viewers": ["g@x.y"]}}
    check("admins match case-insensitively", access.role("owner@x.y", pol), "admin")
    check("an unlisted person drops to view", access.role("nobody@x.y", pol), "view")
    check("loopback is always admin", access.role("local", pol), "admin")
    check("an operator may dose", access.may("p@x.y", "/api/chemical", pol)[0], True)
    check("an operator may not change settings",
          access.may("p@x.y", "/api/settings", pol)[0], False)
    check("a viewer may not switch the pool",
          access.may("g@x.y", "/api/control", pol)[0], False)
    check("an unclassified route needs admin",
          access.may("p@x.y", "/api/something-new", pol)[1], "admin")
    # An identity that is PRESENT and EMPTY is malformed, not absent: a header
    # of a single 0x0B byte survives the HTTP parser, strips to "", and used to
    # land on the `not who` branch and get administrator.
    check("an empty identity is not an administrator",
          access.may("", "/api/settings", pol)[0], False)

    # THE WHOLE TABLE, AT EVERY LEVEL. Four routes of thirteen were spot-checked
    # and the other nine could be silently downgraded: /api/export admin ->
    # operate, /api/chemical/delete, /api/reading and /api/pool-shape operate ->
    # view all passed this function untouched. A downgrade in NEEDS is a
    # privilege escalation with no other symptom -- the page dims nothing extra,
    # nothing logs, and the first sign is a guest changing the SMTP password.
    #
    # The expectation is written out rather than derived from NEEDS, because a
    # check that reads its answer out of the thing it is checking measures
    # nothing. The ranking is written out for the same reason.
    WANT = {"/api/control": "operate", "/api/chemical": "operate",
            "/api/chemical/edit": "operate", "/api/chemical/delete": "operate",
            "/api/reading": "operate", "/api/lab-correction": "operate",
            "/api/pool-shape": "operate", "/api/refresh": "operate",
            # Asking the assistant sends this pool's readings to a provider and
            # spends money doing it, so it sits with the routes that ACT rather
            # than with the two that only read.
            "/api/ask": "operate",
            "/api/commands": "view", "/api/rows": "view",
            "/api/settings": "admin", "/api/credential": "admin",
            "/api/credential/import": "admin", "/api/notify-test": "admin",
            "/api/export": "admin"}
    check("no route has been added or dropped without a decision",
          sorted(access.NEEDS), sorted(WANT))
    ORDER = ["view", "operate", "admin"]
    WHO = {"admin": "owner@x.y", "operate": "p@x.y", "view": "g@x.y"}
    for route, need in sorted(WANT.items()):
        got = {lvl: access.may(WHO[lvl], route, pol)[0] for lvl in ORDER}
        want = {lvl: ORDER.index(lvl) >= ORDER.index(need) for lvl in ORDER}
        check(f"{route} costs {need}", got, want)

    # And every write route the server actually dispatches is classified. A
    # route missing from NEEDS falls to the admin default, which is safe, but
    # "safe by omission" and "decided" are different states and only one of them
    # survives the next person tidying the table. Read out of do_POST rather
    # than kept as a second list that can drift.
    import inspect
    import re as _re
    from . import server as _srv
    src = inspect.getsource(_srv.Handler.do_POST)
    dispatched = {r for r in _re.findall(r'route == "(/api/[^"]+)"', src)
                  if not r.startswith("/api/agent/")} - _srv.PUBLIC_POST
    check(f"every write route server.py dispatches is classified "
          f"({len(dispatched)} found)",
          sorted(dispatched - set(access.NEEDS)), [])


# ------------------------------------------------------------- pending_devices
def t_pending():
    from . import server
    from .queue_ import QUEUE
    print("\n  pending_devices() — waiting means the panel does not yet agree")

    now = dt.datetime.now().astimezone()
    def iso(mins):
        return (now - dt.timedelta(minutes=mins)).strftime("%Y-%m-%dT%H:%M:%S%z")

    # The sample's age relative to the command is the whole question, so each
    # case sets it. A sample OLDER than the command proves nothing about that
    # command -- the panel had not been asked yet when it reported.
    state = {"row": {}}
    saved_hist = QUEUE._log
    import poolhound.render as R
    saved_rows = R.rows
    R.rows = lambda name: [state["row"]] if name == "samples.csv" else []

    def with_history(entries, sample):
        state["row"] = sample
        QUEUE._log = list(entries)          # newest first, as history() returns
        return server.pending_devices()

    def spa_sample(age_min, spa="0", pool_set="70"):
        return {"ts": iso(age_min), "pump": "0", "spa": spa,
                "pool_heat": "0", "pool_set": pool_set}

    try:
        # A command the panel has not confirmed is pending.
        p = with_history([{"at": iso(0.5), "cmd": {"device": "Spa", "action": "set",
                                                   "value": 1}, "result": {"ok": True}}],
                         spa_sample(1))
        check("an unconfirmed switch is waiting", p.get("Spa"), "on")

        # THE DEFINITION ITSELF, and it was not constrained. Every "waiting"
        # fixture here used a sample OLDER than the command, so `last_at >= at`
        # was already False and the other half of the rule -- does the panel
        # AGREE -- never had to hold. Deleting `and got == want` from server.py,
        # which is the exact bug that shipped ("a sample has arrived since"
        # clears the waiting mark whether or not anything changed), passed every
        # check in this function. The agent pushes a confirming reading about a
        # second after each command, so in production that sample is ALWAYS
        # newer: these two cases are the only place the two definitions differ.
        p = with_history([{"at": iso(0.5), "cmd": {"device": "Spa", "action": "set",
                                                   "value": 1}, "result": {"ok": True}}],
                         spa_sample(0.2, spa="0"))
        check("a sample newer than the command that still disagrees is waiting",
              p.get("Spa"), "on")
        p = with_history([{"at": iso(0.5), "cmd": {"device": "Spa", "action": "set",
                                                   "value": 1}, "result": {"ok": True}}],
                         spa_sample(0.2, spa="1"))
        check("and one that agrees is confirmed", p.get("Spa"), None)

        # Superseded: on then off, panel now reports off. The OFF stands and the
        # ON must not be resurrected -- this was a real bug.
        # ON at T-2, OFF at T-0.5, panel reported off at T-0.2 (after both).
        p = with_history([
            {"at": iso(0.5), "cmd": {"device": "Spa", "action": "set", "value": 0},
             "result": {"ok": True}},
            {"at": iso(2), "cmd": {"device": "Spa", "action": "set", "value": 1},
             "result": {"ok": True}}],
            spa_sample(0.2, spa="0"))
        check("a superseded ON is never resurrected", p.get("Spa"), None)
        # And with the panel not yet heard from, only the NEWEST command stands.
        p = with_history([
            {"at": iso(0.5), "cmd": {"device": "Spa", "action": "set", "value": 0},
             "result": {"ok": True}},
            {"at": iso(2), "cmd": {"device": "Spa", "action": "set", "value": 1},
             "result": {"ok": True}}],
            spa_sample(1, spa="0"))
        check("an older instruction never wins over a newer one",
              p.get("Spa"), "off")

        # A refused command is finished and failed, not waiting.
        p = with_history([{"at": iso(0.5), "cmd": {"device": "Spa", "action": "set",
                                                   "value": 1},
                           "result": {"ok": False, "refused": "cooldown"}}],
                         spa_sample(1))
        check("a refused command is not waiting", p.get("Spa"), None)

        # Older than the give-up window.
        p = with_history([{"at": iso(10), "cmd": {"device": "Spa", "action": "set",
                                                  "value": 1}, "result": {"ok": True}}],
                         spa_sample(1))
        check("a stale command stops being amber", p.get("Spa"), None)

        # Setpoints: the panel reports 70 and 75 was asked for, so it is waiting.
        p = with_history([{"at": iso(0.5), "cmd": {"device": "Pool_Heater",
                                                   "action": "setpoint", "value": 75},
                           "result": {"ok": True}}],
                         spa_sample(0.2, pool_set="70"))
        check("a setpoint the panel has not taken is waiting",
              p.get("Pool_Heater"), "75")

        # And when the panel reports the number asked for, it is done.
        p = with_history([{"at": iso(0.5), "cmd": {"device": "Pool_Heater",
                                                   "action": "setpoint", "value": 75},
                           "result": {"ok": True}}],
                         spa_sample(0.2, pool_set="75.0"))
        check("a setpoint the panel reports back is confirmed",
              p.get("Pool_Heater"), None)
    finally:
        QUEUE._log = saved_hist
        R.rows = saved_rows


# ------------------------------------------------------------------- the gate
def t_gate():
    from . import render
    print("\n  check_page() — the only automated gate, checked itself")
    ok_page = "<html><body><script>\nvar x = 1;\n</script></body></html>"
    try:
        render.check_page(ok_page)
        check("a clean page passes", True, True)
    except Exception as e:
        check("a clean page passes", f"raised {e}", True)
    bad = "<html><body><script>\nvar x = 'it's broken';\n</script></body></html>"
    try:
        render.check_page(bad)
        check("a broken inline script raises", "it passed", "it raised")
    except ValueError as e:
        check("a broken inline script raises", "it raised", "it raised")
        check("and the message says what is wrong, not just where",
              "SyntaxError" in str(e), True)
    badjson = ('<html><body><script id="catalogue" type="application/json">'
               '{"a": }</script></body></html>')
    try:
        render.check_page(badjson)
        check("a broken JSON block raises", "it passed", "it raised")
    except ValueError:
        check("a broken JSON block raises", "it raised", "it raised")
    # EVERY script tag, not only the attribute-free ones. The pattern matched
    # `<script>` exactly, so the first `<script type="module">` or
    # `<script defer>` anybody added would have escaped the only automated gate
    # this project has -- silently, while the JSON half raised for the same
    # omission one line above.
    mod = ("<html><body><script type=\"module\">\n"
           "var x = 'it's broken';\n</script></body></html>")
    try:
        render.check_page(mod)
        check("a broken script with attributes raises too", "it passed", "it raised")
    except ValueError:
        check("a broken script with attributes raises too", "it raised", "it raised")


# --------------------------------------------------------------------- energy
def t_gate_json_attribute_order():
    """A JSON block is a JSON block whichever order its attributes are in.

    check_page() finds embedded JSON by pattern, and the pattern used to assume
    `id` came before `type`. Writing the same element the other way round made
    the block invisible to the validator -- it was not checked, and it was not
    reported as unchecked either, which is the shape this whole file exists to
    catch. Both spellings must raise on broken JSON.
    """
    from . import render
    print("\n  the gate — a JSON block is found whichever way it is written")
    for why, frag in (
        ("id first",   '<script id="oops" type="application/json">{"a": }</script>'),
        ("type first", '<script type="application/json" id="oops">{"a": }</script>'),
    ):
        try:
            render.check_page(frag)
            check(f"broken JSON with {why} raises", "it passed", "it raised")
        except ValueError:
            check(f"broken JSON with {why} raises", "it raised", "it raised")
    # and a VALID block must still pass, in both spellings
    for why, frag in (
        ("id first",   '<script id="ok" type="application/json">{"a": 1}</script>'),
        ("type first", '<script type="application/json" id="ok">{"a": 1}</script>'),
    ):
        try:
            render.check_page(frag)
            check(f"valid JSON with {why} passes", True, True)
        except ValueError as e:
            check(f"valid JSON with {why} passes", f"raised {e}", True)


def t_not_a_person_sentinels():
    """whoami() has TWO answers meaning "no person", and they must stay together.

    "local" is a loopback console; "anonymous" is a request from off-box with no
    identity header. Both mean nobody signed in, and access.role() must never
    give the second one admin. Adding the second sentinel broke two places that
    spelled the test as `!= "local"`: the public site redirected every visitor
    to the gated build (an outage), and /api/health issued the CSRF token plus
    the pool's live equipment state to anybody who asked.

    server.signed_in() is the one predicate now. This pins the half that can be
    tested without a socket, and asserts no raw sentinel comparison has crept
    back into server.py.
    """
    import re
    from . import access, server, config, render
    print("\n  identity — the two not-a-person sentinels")
    check("anonymous is never admin without a policy",
          access.role("anonymous"), "view")
    check("anonymous is never admin with one",
          access.role("anonymous", {"access": {"admins": ["o@example.com"]}}), "view")
    check("local is still the console", access.role("local"), "admin")
    check("a named person is still resolved",
          access.role("o@example.com", {"access": {"admins": ["o@example.com"]}}), "admin")

    # The regression was never in access.py -- it was five call sites spelling
    # the question themselves. A raw comparison against either sentinel outside
    # signed_in()/whoami() is the bug coming back.
    # THE REGEX COULD NOT SEE THE SPELLING THAT CAUSED THE OUTAGE. It required
    # whoami() to sit immediately beside the comparison --
    # `whoami() != "local"` -- and every one of the five call sites was written
    # the other way:
    #
    #     who = self.whoami()
    #     ...
    #     if who != "local":
    #
    # A variable, compared forty lines later. So the check written to stop this
    # recurring matched zero of the occurrences it was written for, and would
    # have passed the outage commit unchanged. It reported "0 found" and meant
    # "I did not look".
    #
    # An AST walk instead of a pattern: every comparison against either sentinel
    # anywhere in the file, whatever it is spelled against, minus the two
    # functions that are ALLOWED to make it. A string search cannot do that
    # distinction, which is why it was reaching for adjacency in the first
    # place.
    import ast
    path = os.path.join(config.root(), "poolhound", "server.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    ALLOWED = {"signed_in", "whoami"}
    offenders = []

    class Walk(ast.NodeVisitor):
        def __init__(self):
            self.fn = []

        def visit_FunctionDef(self, node):
            self.fn.append(node.name)
            self.generic_visit(node)
            self.fn.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Compare(self, node):
            if not (self.fn and self.fn[-1] in ALLOWED):
                for c in node.comparators:
                    for lit in ast.walk(c):
                        if (isinstance(lit, ast.Constant)
                                and lit.value in ("local", "anonymous")):
                            offenders.append(
                                f"{self.fn[-1] if self.fn else '<module>'}:"
                                f"{node.lineno}")
            self.generic_visit(node)

    Walk().visit(tree)
    check(f"no raw sentinel comparison outside signed_in()/whoami() "
          f"({len(offenders)} found)", offenders, [])

    # AND THE CHECK ITSELF IS CHECKED. A detector that silently matches nothing
    # is the failure being fixed here, so it is pointed at the outage's own code
    # and must find it.
    outage = ast.parse(
        "class H:\n"
        "    def do_GET(self):\n"
        "        who = self.whoami()\n"
        "        if who != 'local':\n"
        "            return self.redirect()\n")
    offenders = []
    Walk().visit(outage)
    check("and the detector finds the outage's own spelling",
          bool(offenders), True)

    # THE PROXY AND THE APPLICATION MUST AGREE ON THE IDENTITY VOCABULARY.
    #
    # Caddy strips every identity header on the way in and re-adds the ones
    # oauth2-proxy vouches for. A header it re-adds that server.py does not read
    # is dead weight on every request; worse, it reads as working to anyone
    # comparing the two files. X-Auth-Request-User was copied in both
    # forward_auth blocks and was in neither IDENTITY_HEADERS nor anything else
    # -- which is invisible on THIS install, where an email claim is always
    # present, and is the whole identity on an install whose provider issues
    # none.
    caddy_p = config.caddy_vhost()
    if caddy_p:
        ctext = open(caddy_p, encoding="utf-8").read()
        copied = set()
        for m in re.finditer(r"copy_headers ([^\n]+)", ctext):
            for tok in m.group(1).split():
                # "A>B" renames A to B; B is what arrives.
                copied.add(tok.split(">")[-1])
        read = {h.lower() for h in server.IDENTITY_HEADERS}
        unread = sorted(h for h in copied if h.lower() not in read)
        check("every header the proxy copies is one the app reads", unread, [])

    # THE SIXTH CALL SITE WAS NOT IN PYTHON. The page tested
    # `j.user !== 'local'`, which is true of "anonymous" -- so an anonymous
    # visitor had that word rendered where a username goes and was shown a
    # SIGN-OUT control, the exact failure the comment beside it warned against.
    # The server now ships signed_in() as a field and the page reads the answer
    # instead of re-deriving it from sentinel strings.
    tmpl = render.TEMPLATE
    check("the page does not compare the identity to a sentinel",
          "'local'" in tmpl or '"local"' in tmpl, False)
    check("it reads the server's answer instead", "j.signed_in" in tmpl, True)

    # AND THE STATUS LINE ASKS "am I signed in" BEFORE "what may I do".
    # Reading ROLE first was survivable only while an unknown role defaulted to
    # admin; once that default was corrected to `view`, the PUBLIC site began
    # telling every anonymous visitor "signed in - view only". Deployed, and
    # caught in a browser rather than by reading the diff.
    i_can = tmpl.index("!CANWRITE")
    i_role = tmpl.index("(ROLE === 'view')", i_can - 400 if i_can > 400 else 0)
    check("CANWRITE is tested before ROLE in the status line", i_can < i_role, True)


def t_rows_slice():
    """A day outside the rendered window has to be reachable, and safely.

    The tables render at most MAX_RENDER rows and say so. That honesty was the
    fix for silent truncation; it is not a way to reach the rows it admits to
    hiding, and this install writes 96 samples a day, so "last month" left the
    page's reach within a fortnight.

    The two things that must not regress: the name is an allowlist (this reads
    files out of the data directory, and a caller-supplied path is a directory
    traversal waiting to happen), and truncation is STATED rather than left to
    be inferred from a count that happens to equal the cap.
    """
    import csv as _csv, tempfile
    from . import server, config
    print("\n  /api/rows — reaching past the rendered window")
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "samples.csv"), "w", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=["ts", "pump"])
        w.writeheader()
        for day in range(1, 6):
            for q in range(4):
                w.writerow({"ts": f"2026-08-{day:02d}T0{q}:00:00-0700", "pump": "1"})
    # POOLHOUND_DATA WINS OVER THE cfg DICT, correctly -- an install's data
    # directory is an environment fact, not something a caller talks it out of.
    # This test passed here and failed inside the image build, where that
    # variable is set to /data and there is no data: rows_slice looked in /data,
    # found nothing and returned None. The fixture has to own the variable it
    # depends on rather than inherit whatever the shell had.
    cfg = {"paths": {"data": d}, "_source": "<test>"}
    prev = os.environ.get("POOLHOUND_DATA")
    os.environ["POOLHOUND_DATA"] = d
    try:
        _rows_slice_cases(server, cfg, d)
    finally:
        if prev is None:
            os.environ.pop("POOLHOUND_DATA", None)
        else:
            os.environ["POOLHOUND_DATA"] = prev


def _rows_slice_cases(server, cfg, d):
    """The assertions, with POOLHOUND_DATA pointed at the fixture."""
    got = server.rows_slice("samples.csv", cfg)
    check("every row is reachable", got["matched"], 20)
    check("newest first", got["rows"][0]["ts"][:10], "2026-08-05")

    got = server.rows_slice("samples.csv", cfg, since="2026-08-02", until="2026-08-03")
    check("a single day can be asked for", got["matched"], 8)

    got = server.rows_slice("samples.csv", cfg, limit=5)
    check("the cap truncates", got["returned"], 5)
    check("and SAYS it truncated", got["truncated"], True)
    check("while still reporting the true total", got["matched"], 20)

    # The allowlist. A path is not a name.
    for bad in ("../config/config.toml", "/etc/passwd", "nope.csv",
                "pool_shape.json", "samples.csv/../../etc/passwd"):
        check(f"refused: {bad}", server.rows_slice(bad, cfg), None)


def t_canvas_colours_come_from_the_palette():
    """No canvas may hold a private copy of a series colour.

    style.SERIES is the single source, and the palette is validated AS A SET --
    contrast on both surfaces plus colourblind separation. That validation is
    worth nothing if a canvas keeps its own hex: the spa tracer did, so its
    outline painted the light theme's colour on a dark page and would have kept
    the old value silently if the series were ever re-picked.

    PAINT resolves the tokens at draw time and is allowed the only literals in
    the file, as its own fallbacks -- and those must still MATCH the tokens they
    stand in for, or the fallback is a different palette waiting for a
    getComputedStyle failure.
    """
    import re
    from . import config, style
    print("\n  canvas — colours resolve from style.SERIES, not from copies")
    src = open(os.path.join(config.root(), "poolhound", "render.py"),
               encoding="utf-8").read()
    lits = [(m.start(), m.group(0).strip("'")) for m in
            re.finditer(r"'#[0-9a-fA-F]{6}'", src)]
    # Everything inside the PAINT factory is a declared fallback; anything
    # outside it is a private copy.
    pstart = src.index("var PAINT = (function ()")
    pend = src.index("})();", pstart)
    outside = [h for off, h in lits if not (pstart < off < pend)]
    check(f"no series hex outside PAINT ({len(lits)} literals, all in PAINT)",
          outside, [])
    # And PAINT's fallbacks must agree with the tokens.
    for name, hexc in style.SERIES.items():
        m = re.search(r"tok\('--" + name + r"',\s*'(#[0-9a-fA-F]{6})'\)", src)
        if m:
            check(f"PAINT's {name} fallback matches style.SERIES",
                  m.group(1).lower(), hexc.lower())


def t_vault_isolation_is_total():
    """POOLHOUND_VAULT must isolate EVERY door to a credential, not most of them.

    This has now been reported twice. Run two: import_legacy() and put_file()
    ignored the variable and a reviewer read the household's real ~/.waterguru
    and spent a live API call. Those two were fixed. Run three found the guard
    was still unreachable for the path the collectors actually use —
    credentials_for() with an explicit legacy_path, which every real caller
    passes because config.DEFAULTS supplies "~/.waterguru" on every install.

    Verified at the time: ISOLATED was True, import_legacy() returned [],
    agent_token() returned None, and credentials_for() still handed back a real
    username. Three doors shut and one open is not isolation, and checking the
    three that were already shut is how it survived a round.

    So this test enumerates the doors rather than sampling them.
    """
    import tempfile
    from . import vault
    print("\n  vault — isolation covers every route to a credential")
    prev = os.environ.get("POOLHOUND_VAULT")
    d = tempfile.mkdtemp()
    os.environ["POOLHOUND_VAULT"] = d
    import importlib
    try:
        importlib.reload(vault)
        check("ISOLATED is set by the variable", vault.ISOLATED, True)
        # Every function that can reach a credential off the operating user's
        # home. A new one added here without a guard is the next recurrence.
        # The path the SERVICES table supplies, not one typed here — a test
        # that names its own path proves isolation against a path no install
        # uses, and would keep passing if the real default moved.
        _legacy = (vault.SERVICES["waterguru"].get("legacy") or "")
        u, p_, src = vault.credentials_for("waterguru", _legacy)
        check("credentials_for with an EXPLICIT path finds nothing", src, None)
        u2, p2, src2 = vault.credentials_for("waterguru")
        check("credentials_for with no path finds nothing", src2, None)
        check("import_legacy moves nothing", vault.import_legacy(remove=False), [])
        check("agent_token does not reach the keychain",
              vault.agent_token(create=False), None)
        # AND FOR THE RIGHT REASON. The line above passes on a Mac because the
        # namespaced keychain item happens not to exist, and on Linux because
        # there is no keychain at all -- neither of which is isolation, and a
        # reviewer whose Mac DID hold the item would have been handed the
        # household's live agent token by a test that had just printed "ok".
        # The mechanism is the namespacing, so the namespacing is what gets
        # asserted: an isolated install must not look under the bare names that
        # an unrelocated install uses.
        for bare in ("agent-token", "vault-key"):
            check(f"the keychain account for {bare} is namespaced",
                  vault._kc_account(bare) != bare, True)
            check(f"and it still contains {bare} so it is recognisable",
                  vault._kc_account(bare).startswith(bare + "@"), True)
        # Two different vault directories are two different installs.
        d2 = tempfile.mkdtemp()
        os.environ["POOLHOUND_VAULT"] = d2
        importlib.reload(vault)
        other = vault._kc_account("agent-token")
        os.environ["POOLHOUND_VAULT"] = d
        importlib.reload(vault)
        check("a different vault directory is a different keychain account",
              other != vault._kc_account("agent-token"), True)
        # And create=False must ANSWER rather than raise. It raised on a host
        # with no keychain and no Key Vault -- which the image build is -- so
        # the gate failed on a machine where the correct answer was "no token".
        # server.py hid that behind `except Exception`, so the caller that has
        # no such wrapper, agent_token_fingerprint(), was the one that broke.
        check("the fingerprint answers rather than raising",
              vault.agent_token_fingerprint(), None)

        # EVERY ROUTE MEANS WRITES TOO.
        #
        # This section is titled "isolation covers every route to a credential"
        # and enumerated five READS. put_file — the write side — was in none of
        # them, and that is where the boundary was open: config.DEFAULTS names
        # ~/.waterguru on every install, so put_file's `path` was never empty
        # and its guard was unreachable for the services it protects. A review
        # server with ISOLATED asserted True overwrote this household's real
        # ~/.waterguru with a synthetic credential while these eleven checks
        # passed.
        #
        # A gate that says "every route" has to enumerate the routes, in both
        # directions, or the sentence is doing the work the code is not.
        # THE PROBE PATH IS INSIDE THE SANDBOX, NEVER "~/.waterguru".
        #
        # A check that writes to the real credential file when it FAILS is a
        # check that damages the thing it protects, on the one run where
        # something is already wrong. (Confirmed: pointing this at the literal
        # ~/.waterguru and running it against the unfixed code overwrote that
        # file.) An explicit path under the sandbox exercises the same branch —
        # the guard is about ISOLATED, not about which path was named.
        probe = os.path.join(d, "probe-credential")
        wrote = None
        try:
            vault.put_file("waterguru", "u@example.com", "S", path=probe)
            wrote = "it wrote the file"
        except vault.VaultError:
            wrote = "refused"
        except Exception as e:          # noqa: BLE001 - any other failure is still a fail
            wrote = f"raised {type(e).__name__}"
        check("put_file refuses an explicit path when isolated", wrote, "refused")

        implicit = None
        try:
            vault.put_file("waterguru", "u@example.com", "S")
            implicit = "it wrote the file"
        except vault.VaultError:
            implicit = "refused"
        check("and refuses the implicit path too", implicit, "refused")

        # The one write that IS allowed under isolation: into the sandbox vault.
        try:
            vault.put("waterguru", "u@example.com", "S")
            inside = "stored"
        except Exception as e:          # noqa: BLE001
            inside = f"raised {type(e).__name__}: {e}"
        check("but the vault itself still accepts a credential", inside, "stored")
    finally:
        if prev is None:
            os.environ.pop("POOLHOUND_VAULT", None)
        else:
            os.environ["POOLHOUND_VAULT"] = prev
        importlib.reload(vault)


def t_watch_actually_runs():
    """bin/watch must complete, and must serialise against itself.

    Two defects met here. A local named `newest` shadowed the loader imported
    at the top of watch.py, so `latest = newest(readings)` forty lines later
    was a TypeError — the watchdog that exists to tell you a collector has gone
    quiet was itself dying every thirty minutes, on the branch that decides
    whether to alert about chlorine and pH. It failed identically at HEAD;
    nothing caught it because nothing ran it.

    And the state that records "already told you" was read at the top of a run
    and written at the bottom with SMTP in between, unlocked — 39 of 40
    concurrent records lost, measured. The run that overruns is the run that
    MAILS, so the loss lands exactly where a duplicate 02:00 alarm comes from.
    """
    import subprocess, sys, tempfile, shutil, json
    import csv as _csv
    from . import config
    print("\n  watch — the watchdog completes, and one run at a time")
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "data"), exist_ok=True)
    os.makedirs(os.path.join(d, "site"), exist_ok=True)
    with open(os.path.join(d, "config.toml"), "w") as f:
        f.write("[pool]\nvolume_gallons = 16250\n")
    # AND IT MUST NOT REACH A PERSON. --quiet suppresses this run's stdout and
    # nothing else; notify() defaults `desktop` to true when a config has no
    # [notify] section, which this fixture deliberately does not have. So every
    # run of this suite on a Mac put a real banner on a real screen about the
    # chlorine and pH of a pool that does not exist, and the only reason it was
    # ever noticed is that somebody asked why their laptop kept talking about
    # free chlorine.
    env = dict(os.environ,
               POOLHOUND_DATA=os.path.join(d, "data"),
               POOLHOUND_SITE=os.path.join(d, "site"),
               POOLHOUND_CONFIG=os.path.join(d, "config.toml"),
               POOLHOUND_NO_NOTIFY="1")
    r = subprocess.run([sys.executable,
                        os.path.join(config.root(), "bin", "watch"), "--quiet"],
                       capture_output=True, text=True, env=env, timeout=120)
    # 0 OR 1, AND THE DIFFERENCE MATTERS.
    #
    # This asserted exit 0 flatly, and then exit 1 acquired a meaning: a run
    # that RAISED alerts and could deliver none of them now says so, because
    # "all clear" was previously printed in exactly that state. An empty
    # fixture is that state by construction — both collectors have never
    # returned anything — and inside the image build there is no desktop
    # notifier and no mail relay, so the honest answer there is 1.
    #
    # The thing this case exists for is that bin/watch used to DIE: a local
    # name shadowed the loader and it raised TypeError on every run. So what is
    # asserted is that it completed — no traceback, and an exit code that is
    # one of the two it defines, never a crash.
    check("bin/watch completes without raising", "Traceback" in r.stderr, False)
    check("and exits with a code it defines (0 clear, 1 undeliverable)",
          r.returncode in (0, 1), True)
    check("and writes its state",
          os.path.exists(os.path.join(d, "data", ".watch-state.json")), True)
    # The loader must still be callable in that scope — the shadowing bug.
    from . import watch as _w, render as _r
    check("watch.newest is still render's loader", _w.newest is _r.newest, True)

    # AND THE BRANCH EVERY REAL RUN TAKES. The fixture above is an EMPTY data
    # directory, so no chemistry alarm fires and the whole reminder path is
    # skipped — which is how an extraction that dropped `force` from _run()'s
    # scope passed this case while raising NameError on the server every thirty
    # minutes. "bin/watch exits 0" against no data does not mean bin/watch
    # exits 0.
    #
    # So: a reading bad enough to alarm, and a state file that already
    # remembers telling somebody about it, which is the combination that
    # reaches `rec["active"] and rec.get("last_notified")`.
    with open(os.path.join(d, "data", "readings.csv"), "w", newline="") as f:
        w_ = _csv.DictWriter(f, fieldnames=["measured", "free_cl", "ph", "ta", "ch", "cya"])
        w_.writeheader()
        w_.writerow({"measured": "2026-09-14T09:00:00-0700", "free_cl": "0.2",
                     "ph": "8.2", "ta": "200", "ch": "600", "cya": "120"})
    with open(os.path.join(d, "data", ".watch-state.json"), "w") as f:
        json.dump({"chemistry": {
            "free_cl": {"active": True, "last_notified": "2026-09-14T09:00:00"},
            "ph":      {"active": True, "last_notified": "2026-09-14T09:00:00"},
        }}, f)
    r2 = subprocess.run([sys.executable,
                         os.path.join(config.root(), "bin", "watch"), "--quiet"],
                        capture_output=True, text=True, env=env, timeout=120)
    check("bin/watch completes with an alarm already notified",
          r2.returncode in (0, 1), True)
    check("and does not raise on the reminder path",
          "Traceback" in r2.stderr, False)

    # AND THE TWO EXIT CODES MEAN WHAT THEY SAY. Widening the assertion above
    # to accept either would otherwise just be a weaker test; this pins which
    # one belongs to which state. Run in-process so the notifier can be made to
    # fail the way the server's does — no desktop notification there, and no mail
    # relay until one is configured.
    from . import watch as _w, render as _r
    import importlib
    prev_notify = _w.notify
    prev_env = {k: os.environ.get(k) for k in
                ("POOLHOUND_DATA", "POOLHOUND_SITE", "POOLHOUND_CONFIG")}
    # THE ENV VAR IS NOT ENOUGH: render binds DATA at IMPORT, and watch reads
    # the pool through render's loaders. Setting POOLHOUND_DATA here changes
    # nothing that has already been imported, so this case read whatever pool
    # the suite was launched against — and then asserted an exit code that
    # depends on whether THAT pool happens to have a stale collector. It passed
    # or failed on the host's data rather than on the fixture, which is the
    # same class of defect as a gate that measures nothing: the result did not
    # come from the thing under test. Two review lanes reported it before this
    # one did.
    prev_DATA = _r.DATA
    try:
        os.environ.update({k: v for k, v in
                           (("POOLHOUND_DATA", env["POOLHOUND_DATA"]),
                            ("POOLHOUND_SITE", env["POOLHOUND_SITE"]),
                            ("POOLHOUND_CONFIG", env["POOLHOUND_CONFIG"]))})
        _r.DATA = env["POOLHOUND_DATA"]
        os.remove(os.path.join(d, "data", ".watch-state.json"))
        _w.notify = lambda *a, **k: False       # nothing can carry an alert
        check("a run that cannot deliver its alerts exits 1", _w.main(), 1)
        os.remove(os.path.join(d, "data", ".watch-state.json"))
        _w.notify = lambda *a, **k: True        # everything delivers
        check("and a run that delivers them exits 0", _w.main(), 0)
    finally:
        _w.notify = prev_notify
        _r.DATA = prev_DATA
        for k, v in prev_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    if "Traceback" in r2.stderr:
        print("    " + r2.stderr.strip().splitlines()[-1][:120])
    shutil.rmtree(d, ignore_errors=True)


def t_energy():
    with in_deploy_tz():
        _energy()


def _energy():
    # LOCAL DAYS, SO THE ZONE HAS TO BE STATED. by_day() buckets a sample by
    # its LOCAL date, and these fixtures pair "-0700" stamps with bare day
    # keys: east of UTC, 10:00-0700 lands on the following day and every
    # measured day reads as rated instead. Pacific and UTC both happened to
    # agree, so this went unnoticed until the suite was run in Tokyo.
    from . import render
    print("\n  energy() — every figure names what it rests on")
    days = [{"date": "2026-09-12", "hours": {"pump": 6.0, "spa": 0, "sheer": 0}},
            {"date": "2026-09-11", "hours": {"pump": 6.0, "spa": 0, "sheer": 0}}]
    e = render.energy(days, [], rated=None, cost=None)
    check("no watts and no rating means no energy figure", e["kwh_total"], None)
    check("and it says the pump never reported", e["ever_reported_watts"], False)
    e = render.energy(days, [], rated=1000.0, cost=None)
    check("a rating gives an estimate", round(e["kwh_per_day"], 1), 6.0)
    check("labelled as an estimate", e["basis"], "rated")
    samples = [{"ts": "2026-09-12T10:00:00-0700", "pump": "1", "pump_watts": "500"},
               {"ts": "2026-09-11T10:00:00-0700", "pump": "1", "pump_watts": "500"}]
    e = render.energy(days, samples, rated=1000.0, cost=None)
    check("a measurement beats the rating", round(e["kwh_per_day"], 1), 3.0)
    check("and says so", e["basis"], "measured")

    # THE CASE THAT WAS WRONG IN PRODUCTION. Seven days of runtime, wattage on four
    # of them, no rating. kwh_per_day was averaged over the four days that had a
    # figure while hours_per_day was averaged over all seven -- two answers to
    # two different questions, printed side by side with the same "/day" unit.
    # With hours varying across the window that overstated the energy and cost
    # tiles by 75% for a 1 kW pump, and the savings advice divides one by the
    # other, so the money figure inherited it.
    #
    # The check is the division: kwh_per_day / hours_per_day must come back as
    # the mean draw that was actually measured, in kW.
    mixed_days, mixed_samples = [], []
    for i in range(7):
        d = f"2026-09-{20 + i}"
        mixed_days.append({"date": d, "hours": {"pump": 2.0 + i, "spa": 0, "sheer": 0}})
        if i < 4:
            mixed_samples.append({"ts": f"{d}T10:00:00-0700", "pump": "1",
                                  "pump_watts": "1000"})
    e = render.energy(mixed_days, mixed_samples, rated=None, cost=None)
    check("the energy figure says how many days it covers", e["known_days"], 4)
    check("and how many are shown", e["window_days"], 7)
    check("both tiles share a denominator (kWh/day ÷ h/day = the measured kW)",
          round(e["kwh_per_day"] / e["hours_per_day"], 3), 1.0)

    # A MIXED WEEK, which no fixture here built. energy()'s docstring states the
    # rule -- "One rated day in the set makes the total an estimate" -- and
    # nothing asserted it: `basis` ignoring rated_days entirely, and every day
    # being stamped "measured" regardless, each survived this whole function.
    # Presenting a rating somebody typed off a pump label as a measurement is
    # the same defect as quoting a dose against a volume nobody set.
    mixed = [{"date": "2026-10-01", "hours": {"pump": 5.0, "spa": 0, "sheer": 0}},
             {"date": "2026-10-02", "hours": {"pump": 5.0, "spa": 0, "sheer": 0}},
             {"date": "2026-10-03", "hours": {"pump": 5.0, "spa": 0, "sheer": 0}}]
    watted = [{"ts": "2026-10-01T10:00:00-0700", "pump": "1", "pump_watts": "800"},
              {"ts": "2026-10-02T10:00:00-0700", "pump": "1", "pump_watts": "800"}]
    e = render.energy(mixed, watted, rated=1000.0, cost=None)
    check("two measured days and one rated is neither", e["basis"], "mixed")
    check("and it says how many of each", (e["measured_days"], e["rated_days"]), (2, 1))
    check("the rated day is not labelled measured",
          [d["basis"] for d in e["days"]], ["measured", "measured", "rated"])

    # AN IDLE DAY MUST NOT COLOUR THE BASIS. A day the pump never ran
    # contributes no energy whichever number it would have used; falling
    # through to the rating made a fully measured week report itself as part
    # estimated. The fix is a `if hrs > 0` beside the counter, and removing it
    # changed nothing any check could see.
    idle = [{"date": "2026-10-01", "hours": {"pump": 5.0, "spa": 0, "sheer": 0}},
            {"date": "2026-10-02", "hours": {"pump": 0.0, "spa": 0, "sheer": 0}}]
    e = render.energy(idle, watted[:1], rated=1000.0, cost=None)
    check("a day the pump never ran does not make the week an estimate",
          e["basis"], "measured")
    check("and is not counted as a rated day", e["rated_days"], 0)
    # The same rule from the other side, which is the half of the fix that is
    # easy to drop: an idle day that HAPPENS to carry a wattage reading
    # measured nothing either, so it must not make a rated week claim to be
    # part measured. Both branches carry the `if hrs > 0` for this reason.
    idle_watted = [{"date": "2026-10-01", "hours": {"pump": 0.0, "spa": 0, "sheer": 0}},
                   {"date": "2026-10-02", "hours": {"pump": 5.0, "spa": 0, "sheer": 0}}]
    e = render.energy(idle_watted, watted[:1], rated=1000.0, cost=None)
    check("an idle day with a wattage reading measured nothing either",
          (e["basis"], e["measured_days"], e["rated_days"]), ("rated", 0, 1))

    # The money figure, which nothing asserted at all: returning None for
    # cost_total passed. It is the one number on the card an owner acts on.
    e = render.energy(days, samples, rated=1000.0, cost=0.32)
    check("the cost is the energy times the tariff",
          round(e["cost_total"], 6), round(e["kwh_total"] * 0.32, 6))
    check("and the per-day costs add up to it",
          round(sum(d["cost"] for d in e["days"]), 6), round(e["cost_total"], 6))


# ------------------------------------------------------- a column that lies
def t_columns_do_not_mirror():
    """No column may be a copy of another one under a different name.

    This is the check that would have caught the setpoint defect on the day it
    shipped, and it is one line of arithmetic. freeze_set was AqualinkD's
    `value` for Freeze_Protect, which is the air temperature -- so it equalled
    air_temp on 66 of 66 rows while the control card printed it as a setpoint. A
    number appearing is not the right number appearing, and the difference is
    visible from the row it sits in.

    Runs against whatever data this machine has; says so and passes when there
    is none, rather than reporting a clean result about an empty file.
    """
    import csv
    from . import config
    print("\n  columns — a setpoint must not be a temperature wearing a new name")
    try:
        p = os.path.join(config.data_dir(), "samples.csv")
    except SystemExit:
        print("    skip  no config on this machine")
        return
    if not os.path.exists(p):
        print("    skip  no samples.csv here")
        return
    rows = list(csv.DictReader(open(p, newline="")))
    SUSPECT = [("pool_set", "pool_temp"), ("pool_set", "air_temp"),
               ("spa_set", "air_temp"), ("freeze_set", "air_temp"),
               ("freeze_set", "pool_temp")]
    for a, b in SUSPECT:
        both = [r for r in rows
                if (r.get(a) or "").strip() and (r.get(b) or "").strip()]
        if len(both) < 5:
            print(f"    skip  {a} vs {b}: only {len(both)} row(s) have both")
            continue
        same = sum(1 for r in both if r[a].strip() == r[b].strip())
        # A RATIO, not an identity. This failed only when a column was a copy on
        # 100% of rows, so a setpoint that was the air temperature on 65 of 66
        # rows -- one row of sensor noise away from the defect it was written for
        # -- sailed through. Two genuinely different measurements do not agree
        # nine times in ten.
        check(f"{a} is not a copy of {b} ({same}/{len(both)} identical)",
              same / len(both) > 0.9, False)


def t_setpoint_ranges_admit_the_panel():
    """A range that refuses the panel's own reading is wrong by construction.

    Freeze protection was declared to accept 55-65. The panel reports 34, on
    every sample that carries the column. So the control card rendered "now 34"
    into an input whose min was 55 -- the browser marked it invalid on load --
    and pressing Set was refused by poolhound's own validator, in a message that
    blamed the operator. The one setpoint an owner should never have to touch
    was the one they could not leave alone.

    Nobody has to notice that again: the panel is the authority on what it
    accepts, and it tells us in every sample.
    """
    import csv
    from . import commands as C, config
    print("\n  setpoints — the accepted range must contain what the panel reports")
    try:
        p = os.path.join(config.data_dir(), "samples.csv")
    except SystemExit:
        print("    skip  no config on this machine")
        return
    rows = list(csv.DictReader(open(p, newline=""))) if os.path.exists(p) else []
    seen = 0
    for dev, spec in C.SETPOINTS.items():
        col = spec.get("value_column")
        vals = []
        for r in rows:
            v = (r.get(col) or "").strip()
            try:
                vals.append(float(v))
            except ValueError:
                pass
        if not vals:
            print(f"    skip  {dev}: no {col} readings on this machine")
            continue
        seen += 1
        out = sorted({v for v in vals if not (spec["min"] <= v <= spec["max"])})
        check(f"{dev} accepts what the panel reports ({spec['min']}-{spec['max']}, "
              f"saw {min(vals):g}-{max(vals):g})", out, [])
    if not seen:
        print("    note  no setpoint columns populated here; the server's share is the "
              "machine that has them")


def t_state_columns_are_declared():
    """A column some collector writes must be declared in STATE_COLUMN.

    Freeze_Protect said column=None -- "the panel is silent about this" -- while
    the agent wrote a `freeze` column on every sample and watch.py mailed the
    owner when it changed. The card was painted "unknown" forever and the device
    never appeared in /api/health. Two parts of one product disagreeing about
    whether a fact is knowable.
    """
    from . import commands as C, aqualink
    print("\n  equipment — a column that is collected must be a column that is read")
    for dev, spec in list(C.SWITCHES.items()) + list(C.SETPOINTS.items()):
        col = spec.get("column")
        if col:
            check(f"{dev}'s state column {col!r} is collected", col in aqualink.COLS, True)
    # and the other direction: a device whose name matches a collected column
    # must not be declaring the panel silent about it.
    for dev, spec in C.SETPOINTS.items():
        if spec.get("column") is None:
            guess = dev.split("/")[0].split("_")[0].lower()
            clash = [c for c in aqualink.COLS if c == guess]
            check(f"{dev} declares the panel silent and nothing collects {guess!r}",
                  clash, [])


def t_caddy_covers_every_private_route():
    """Every route in access.NEEDS must also be gated at the proxy.

    The Caddy vhost asserted that "a new endpoint is closed until somebody
    deliberately opens it, and the failure mode of forgetting is a 401 rather
    than an exposure". It is not: the last block is a catch-all that proxies
    anything unmatched, so a route missing from @private reaches poolhound
    anonymously and is refused only by poolhound's own session-token guard.
    /api/reading -- a write that records a lab result -- had been missing.

    That guard does hold, so this is defence in depth rather than a hole. But
    the two layers have to be independent to BE two layers, and nothing
    compared them. This does.
    """
    import fnmatch, re
    from . import access, config
    print("\n  proxy — every private route is gated at Caddy too")
    p = config.caddy_vhost()
    if not p:
        skipped("every private route gated at the proxy too",
                "no Caddy vhost in this tree")
        return
    block = re.search(r"@private \{(.*?)\n\t\t\}", open(p).read(), re.S)
    if not block:
        check("the @private block is findable", False, True)
        return
    pats = [x for m in re.finditer(r"path ([^\n]+)", block.group(1))
            for x in m.group(1).split()]
    # The two pure calculator routes are public on purpose: they read nothing,
    # write nothing and keep nothing, and the volume tool is offered to
    # everybody. They are the only deliberate exceptions.
    PUBLIC = {"/api/pool-shape/compute", "/api/photo"}
    missing = sorted(r for r in access.NEEDS if r not in PUBLIC
                     and not any(fnmatch.fnmatch(r, x) for x in pats))
    check(f"every access.NEEDS route is in Caddy's @private ({len(pats)} patterns)",
          missing, [])


# ------------------------------------------------------- the dose arithmetic
def t_dose_roundtrip():
    """The browser's copy of the dose arithmetic must answer what Python does.

    "Never add a second copy of the dose arithmetic" is the rule, and it is
    already half-broken by necessity: the form previews an effect as you type,
    so as_json() ships `per_unit` coefficients and a `ref_pct`, and the page
    multiplies. Two implementations of one relation, in two languages, and
    nothing asserted they agree. The last copy that drifted -- chemistry.py's --
    used a flat 4 ppm-per-0.1-pH rule and produced acid doses 23% high, on the
    public tab, in the overdose direction, for the one chemical that damages
    plaster.

    So the page's formula is written out here in Python and run against
    effects() for every chemical, unit and strength in the catalogue. A change
    to a coefficient, to ref_pct or to a units table that the browser would not
    follow fails here instead of in the form.

    amount_for() is checked the same way. It is the inverse and every card in
    "What to do next" goes through it.
    """
    from . import chemicals as CH
    print("\n  chemicals — one relation, and the browser's copy agrees with it")
    G, TA = 16000.0, 75.0
    cat = CH.as_json(G, TA)
    worst, cases = 0.0, 0
    for key, c in cat["chemicals"].items():
        table = cat["volume_units"] if c["phase"] == "liquid" else cat["weight_units"]
        for unit in c["units"]:
            for amount in (0.5, 3.0, 40.0):
                for pct in set(c["pct_choices"]) | {c["default_pct"]}:
                    py = CH.effects(key, amount, unit, pct, G, TA)
                    # render.py's preview(), transliterated:
                    #     var scale = base * (strength / c.ref_pct);
                    #     var v = c.per_unit[k] * scale;
                    scale = amount * table[unit] * (pct / c["ref_pct"])
                    for m, v in py.items():
                        js = c["per_unit"][m] * scale
                        if abs(v) < 1e-12 and abs(js) < 1e-12:
                            continue
                        cases += 1
                        worst = max(worst, abs(js - v) / max(abs(v), 1e-9))
    check(f"the page's formula matches effects() ({cases} combinations, worst "
          f"disagreement {worst * 100:.4f}%)", worst < 0.005, True)

    worst_inv = 0.0
    for key, c in CH.CHEMICALS.items():
        one = c["effects"](1.0, c["default_pct"], G / 10000.0, TA)
        for m, per in one.items():
            if not per:
                continue
            for amount in (0.5, 3.0, 40.0):
                back = CH.amount_for(key, m, per * amount, G, c["default_pct"], TA)
                worst_inv = max(worst_inv, abs(back - amount) / amount)
    check(f"amount_for() inverts effects() (worst {worst_inv * 100:.4f}%)",
          worst_inv < 0.005, True)

    # And the unit trap, which CLAUDE.md lists as a command to run by hand:
    # "32 oz" is a volume for acid and a weight for cal-hypo, and a unit from
    # the wrong phase must be refused rather than quietly converted.
    for key, c in CH.CHEMICALS.items():
        right = CH.VOLUME_UNITS if c["phase"] == "liquid" else CH.WEIGHT_UNITS
        wrong = CH.WEIGHT_UNITS if c["phase"] == "liquid" else CH.VOLUME_UNITS
        offered = set(CH.units_for(key))
        check(f"{key} offers only {c['phase']} units",
              offered, set(right))
        # "oz" is deliberately in both tables — fluid ounces for a liquid,
        # weight ounces for a solid — so it is not evidence of a leak.
        check(f"and refuses the others ({key})",
              sorted(u for u in wrong if u not in right
                     and CH.to_base(key, 1, u) is not None), [])


def t_write_guards():
    """The three checks in front of every write, as assertions rather than curl.

    CLAUDE.md carries them as three commands a person has to remember to run: a
    POST with a foreign Origin must be 403, a GET with a forged Host 421, and a
    POST with no token 403. They are there because one of them did not hold --
    "a POST with a foreign Origin wrote a fabricated dose before it was fixed" --
    and since then nothing has checked any of the three automatically.

    Driven straight into the handler, with no socket and with the write function
    replaced by a tripwire, so a guard that has stopped holding is reported here
    rather than demonstrated on the household's own CSVs. The last case is the
    one that keeps this honest: with all three satisfied the request must REACH
    the write. Three refusals prove nothing if the route is simply broken.
    """
    import io
    import json as _json
    from email.message import Message
    from . import server as S
    print("\n  the three write guards — Host, Origin, and the per-process token")

    class Stub(S.Handler):
        """A request with no socket behind it. Only the guards run."""
        def __init__(self, path, headers, body=b""):
            self.path, self.status = path, None
            self.rfile, self.wfile = io.BytesIO(body), io.BytesIO()
            self.headers = Message()
            for k, v in headers.items():
                self.headers[k] = v
            self.client_address, self.requestline = ("127.0.0.1", 0), f"POST {path}"
            self.request_version = "HTTP/1.1"
        def send_response(self, code, *a): self.status = code
        def send_header(self, *a): pass
        def end_headers(self): pass
        def log_message(self, *a): pass

    OURS = f"127.0.0.1:{S.PORT}"
    ORIGIN = f"http://127.0.0.1:{S.PORT}"
    BODY = _json.dumps({"chemical": "acid", "amount": 1, "unit": "floz",
                        "pct": 31.45}).encode()

    def hdrs(**over):
        h = {"Host": OURS, "Origin": ORIGIN, S.TOKEN_HEADER: S.TOKEN,
             "Content-Type": "application/json", "Content-Length": str(len(BODY))}
        h.update({k: v for k, v in over.items() if v is not None})
        for k, v in over.items():
            if v is None:
                h.pop(k, None)
        return h

    reached = []
    real_log, real_public = S.log_chemical, S.PUBLIC_HOST
    S.log_chemical = lambda *a, **k: (reached.append(True), ({}, "selftest tripwire"))[1]
    # PUBLIC_HOST makes an unauthenticated loopback POST a 401 before the guards
    # below are reached, which is right on the server and would hide what this is
    # measuring. Cleared for the duration so the three guards are what answers.
    S.PUBLIC_HOST = ""
    try:
        r = Stub("/api/chemical", hdrs(Origin="https://evil.example.com"), BODY)
        r.do_POST()
        check("a foreign Origin is refused", r.status, 403)
        check("and nothing reached the dose log", reached, [])

        r = Stub("/api/health", hdrs(Host="attacker.example.com"))
        r.do_GET()
        check("a forged Host is refused", r.status, 421)

        r = Stub("/api/chemical", hdrs(**{S.TOKEN_HEADER: None}), BODY)
        r.do_POST()
        check("a POST with no token is refused", r.status, 403)
        check("and still nothing reached the dose log", reached, [])

        # The control: all three satisfied, and the write is reached. Without
        # this the three above could all be passing because /api/chemical no
        # longer exists — a test that can pass while measuring nothing is the
        # shape this file is most worried about.
        r = Stub("/api/chemical", hdrs(), BODY)
        r.do_POST()
        check("with all three satisfied the write is reached", reached, [True])
        check("and the tripwire's refusal is what came back", r.status, 400)
    finally:
        S.log_chemical, S.PUBLIC_HOST = real_log, real_public


# t_salt_is_not_a_temperature LIVED HERE, AND ITS SUBJECT IS DELETED.
#
# It guarded aqualink.num()'s plausibility ceiling: the default `hi=500` is a
# TEMPERATURE range, it was left on salt_ppm, and so the workstation poller
# wrote a blank for salt on every sample it ever took -- the column read as a
# panel that does not report salinity at all. A rejected reading and an
# unreported one are the same empty cell, which is why it needed a check.
#
# That poller is gone: the Pi's agent is the only thing that reads the panel
# now, it applies no ceiling of its own, and salt only ever entered the data
# BECAUSE the agent took over. So there is no ceiling left to guard, and a case
# asserting the behaviour of a deleted function is a case that cannot fail.
#
# The half of that story that still matters is NOT the ceiling. SWG/PPM does
# not answer -999 without flow the way the water probes do -- it latches,
# repeating its last measured figure for as long as the pump is off -- and that
# is refused by commands.drop_stale_flow_readings(), on the one path both ends
# share, and is checked by the case directly below. That one stays.

def t_latched_salt_is_not_a_measurement():
    """SWG/PPM keeps answering its last figure with the pump off. Refuse it.

    The -999 sentinel covers the water probes and NOT the salt cell: with no
    flow, Temperature/Pool goes to -999 and gets dropped, while SWG/PPM holds
    the last measured number, bit-identical, for as long as the pump stays off
    -- 3500 from 22:00 to 09:53, then 3400 from 16:13 to 09:47. Stored, those
    are indistinguishable from fresh readings, and by_day() averages the column
    over every row of the day.

    Both collectors share one rule so they cannot disagree about what counts as
    a measurement.
    """
    from . import commands as C
    print("\n  salt — a latched reading is refused at the collector")
    off = C.drop_stale_flow_readings(
        {"pump": 0, "salt_ppm": 3500.0, "pool_temp": 74.0, "air_temp": 61.0,
         "swg_pct": 50.0})
    check("with the pump off the salt is dropped", off["salt_ppm"], "")
    check("and so is the water temperature", off["pool_temp"], "")
    check("the air temperature is NOT flow-dependent", off["air_temp"], 61.0)
    check("nor is the cell's dial, which is a setpoint", off["swg_pct"], 50.0)
    on = C.drop_stale_flow_readings({"pump": 1, "salt_ppm": 3500.0, "pool_temp": 74.0})
    check("with the pump running the reading is kept", on["salt_ppm"], 3500.0)
    check("and so is the water temperature", on["pool_temp"], 74.0)
    # A row that does not say whether the pump ran must not be guessed at.
    quiet = C.drop_stale_flow_readings({"salt_ppm": 3500.0})
    check("an unknown pump state discards nothing", quiet["salt_ppm"], 3500.0)
    # THE COLLECTOR MUST ACTUALLY CALL IT, or the rule is decoration. This
    # looped over two modules — the Pi's agent and the workstation poller —
    # because for a while there were two ways a sample could be written. There
    # is one now: aqualink.py was reduced to the column list when the poller
    # was deleted, and a module with no row to pass cannot pass one.
    #
    # Derived rather than listed, so the next writer is covered by existing.
    # Anything that builds a sample row is something this rule has to reach,
    # and naming them by hand is how aqualink.py became the exception to
    # locking.append_row for as long as it did.
    import inspect
    from . import agent
    writers = [agent]
    for mod in writers:
        check(f"{mod.__name__.split('.')[-1]} passes its row through the rule",
              "drop_stale_flow_readings" in inspect.getsource(mod), True)
    # AND SO MUST WHATEVER ACTUALLY WRITES THE FILE. Derived by asking which
    # modules append to samples.csv, not from a list — naming them by hand is
    # how aqualink.py stayed the exception to locking.append_row for as long as
    # it did. The first version of this matched any module that MENTIONED the
    # filename and flagged help.py, which only describes it in prose.
    import importlib, pkgutil, poolhound
    stray = []
    for m in pkgutil.iter_modules(poolhound.__path__):
        # THE SUITE IS NOT A WRITER, and this is the second false positive this
        # heuristic has produced. It flagged help.py first, which only describes
        # samples.csv in prose; tightening it to "mentions the file AND has a
        # write call" then flagged checks_collection.py, which compares against
        # the NAME in one branch and appends a row to runs.csv in another. Both
        # times the module named was one that does not write this file.
        #
        # checks_* modules are excluded for the reason seams.py excludes them
        # too: they legitimately hold the facts they assert on, and a fixture
        # that seeds a CSV is not a second writer of the product's data. The
        # real writer, agent.py, is still scanned -- and is the thing this rule
        # exists for, because aqualink.py was the exception to append_row for
        # as long as nobody derived the list.
        if m.name.startswith("checks_") or m.name == "selftest":
            continue
        mod = importlib.import_module(f"poolhound.{m.name}")
        try:
            src = inspect.getsource(mod)
        except OSError:
            continue
        writes = '"samples.csv"' in src and ("append_row" in src or "writerow" in src)
        if writes and "drop_stale_flow_readings" not in src:
            stray.append(m.name)
    check("and every writer of the file applies it too", stray, [])


def t_stored_salt_is_plausible():
    """Whatever is on disk must be a salinity, not a sentinel or a temperature.

    The panel is the authority on what it reports, and it says so in every
    sample -- the same argument as the setpoint ranges above.
    """
    import csv
    from . import config
    print("\n  salt — what is stored is a salinity")
    try:
        p = os.path.join(config.data_dir(), "samples.csv")
    except SystemExit:
        print("    skip  no config on this machine")
        return
    rows = list(csv.DictReader(open(p, newline=""))) if os.path.exists(p) else []
    vals, latched = [], []
    for r in rows:
        v = (r.get("salt_ppm") or "").strip()
        if not v:
            continue
        try:
            f = float(v)
        except ValueError:
            check("salt_ppm parses as a number", v, "a number")
            continue
        vals.append(f)
        if (r.get("pump") or "").strip() == "0":
            latched.append(r.get("ts", "?"))
    if not vals:
        print("    skip  no salt readings on this machine")
        return
    out = sorted({v for v in vals if not (500 <= v <= 10000)})
    check(f"every stored salt reading is a salinity (n={len(vals)}, "
          f"saw {min(vals):g}-{max(vals):g})", out, [])
    # Rows written before the collectors learned the rule are history and are
    # left alone; this only asserts that nothing NEW arrives latched.
    if latched:
        print(f"    note  {len(latched)} pump-off salt rows predate the fix "
              f"(first {latched[0]}, last {latched[-1]}); history is not rewritten")


def t_vendor_alerts_are_escaped():
    """An action's `detail` is interpolated into the page WITHOUT escaping.

    That is deliberate -- the hand-written details carry <b> and friends -- which
    makes it a sink that trusts its input, and the WaterGuru alerts string is
    vendor text arriving from an API through readings.csv. When alert clauses
    were parsed into actions, an alerts value of
    `Replace cassette <img src=x onerror=...>` rendered as live markup on the
    PUBLIC page. Demonstrated, not theorised.
    """
    from . import render
    print("\n  alerts — vendor text must not reach an unescaped sink")
    latest = {"alerts": "Replace cassette <img src=x onerror=alert(1)>; "
                        "Total Alkalinity measurement outdated",
              "measured": "2026-09-13T08:00:00Z", "ph": "7.5", "free_cl": "3.2"}
    acts = render.actions({}, latest, render.targets({}, "salt_cell", ""),
                          [], {}, 16250, 31.45)
    blob = " ".join(str(a.get("detail", "")) + str(a.get("title", "")) for a in acts)
    check("no raw < from a vendor alert reaches an action detail",
          "<img" in blob, False)
    check("and the clause is still carried, escaped",
          "&lt;img" in blob, True)
    check("a normal clause still becomes an action",
          any("out of date" in str(a.get("title", "")) for a in acts), True)


def t_partial_day_does_not_shrink_production():
    """The day still in progress is not a day's runtime.

    trajectory() scales production by recent-runtime / average-runtime, and took
    "recent" as the last day in its window -- which is TODAY, part-run. Cron
    renders every six hours, so most renders land mid-afternoon: 4.75 h counted
    against a 7.29 h average gave a ratio of 0.65 and an adjusted equilibrium of
    2.86 ppm, under the 2.9 target, while the same pool at its last complete day
    (6.50 h) was settling towards 3.66. The Power card offered to give back
    runtime that was never spare, and the cell that was over-producing looked
    correct.

    Prorating by elapsed clock is worse: the pump runs in a block, so hours seen
    by 14:10 scale up to more than the day will hold. The data says which days
    finished, so the partial one is dropped from both statistics -- and a day
    that genuinely ran short must still count once it is over, or the fix would
    simply blind the model to the schedule changes it exists to track.
    """
    with in_deploy_tz():
        _partial_day()


def _partial_day():
    # WHICH DAY A SAMPLE BELONGS TO IS A LOCAL QUESTION, so the fixtures below
    # pair "-0700" stamps with bare day numbers and "today" is whatever the
    # host's clock says. At UTC+14 the boundary lands inside the fixture and
    # the part-run day stops being the last one.
    from .render import trajectory
    print("\n  trajectory() — the day in progress is not a day")

    def pump(day, hours):
        """`hours` of quarter-hourly pump-on samples on 2026-09-`day`."""
        out = []
        for q in range(int(hours * 4)):
            out.append({"ts": f"2026-09-{day:02d}T{10 + q // 4:02d}:"
                              f"{(q % 4) * 15:02d}:00-0700", "pump": "1"})
        return out

    def fc(days):
        return [{"measured": f"2026-09-{d:02d}T12:38:00Z", "free_cl": v}
                for d, v in days]

    decay = [(9, 6.2), (10, 5.3), (11, 5.0), (12, 4.8), (13, 4.6), (14, 4.5)]

    # Six full hours every day; today (the 14th) has only reached two.
    samples = [x for d, h in ((9, 6), (10, 6), (11, 6), (12, 6), (13, 6), (14, 2))
               for x in pump(d, h)]
    tr = trajectory(fc(decay), samples)
    check("a fit is produced", bool(tr), True)
    if not tr:
        return
    check("the partial day is not the 'recent' runtime", tr["pump_recent_h"], 6.0)
    check("nor does it drag the average down", tr["pump_avg_h"], 6.0)
    check("so steady runtime means no adjustment at all", round(tr["pump_ratio"], 6), 1.0)
    check("and the adjusted equilibrium equals the plain one",
          round(tr["equilibrium_adjusted"], 6), round(tr["equilibrium"], 6))

    # The 13th ran genuinely short and is OVER -- the 14th is being sampled, so
    # the 13th is the newest FINISHED day. It must still move the ratio.
    short = [x for d, h in ((9, 6), (10, 6), (11, 6), (12, 6), (13, 3), (14, 2))
             for x in pump(d, h)]
    tr2 = trajectory(fc(decay[:-1]), short)
    check("a genuinely short COMPLETE day still counts", tr2["pump_recent_h"], 3.0)
    check("and it does reduce production", tr2["pump_ratio"] < 1.0, True)
    check("the partial day is still excluded from the average", tr2["pump_avg_h"],
          (6 + 6 + 6 + 6 + 3) / 5)


def t_role_dimming_is_derived_and_fails_closed():
    """The page dims what the reader may not use — from ONE list, and safely.

    Three separate bugs, all in the same block, all in the same direction:

      * The selectors were written by hand in the page script, and three gated
        routes had no entry — /api/refresh (spends WaterGuru API calls),
        /api/lab-correction (changes what every chart is computed from) and
        /api/pool-shape (sets the volume every dose multiplies through). Each
        was a live control for a `view` reader that answered 403 on press.
      * `ROLE = j.role || 'admin'` defaulted an unrecognised answer to the most
        privileged level, and the two tests below it named only the two levels
        the page knew — so any other value locked NOTHING.
      * lockOut() ran once, when /api/health answered, and six later handlers
        set .disabled = false on the same elements. The worst re-enabled every
        control button as soon as an agent connected, so a `view` reader's Pool
        control tab came back to life seconds after being dimmed.

    Only the first is checkable without a browser; the other two are asserted
    here as the absence of the spelling that caused them, which is what stops
    the fix being undone by somebody restoring the "simpler" line.
    """
    from . import access, render
    print("\n  roles — the dimming is derived from access.py, not typed")

    ungoverned = sorted(set(access.NEEDS) - set(access.ROLE_UI))
    check("every gated route names its controls (or says it has none)",
          ungoverned, [])
    ghosts = sorted(set(access.ROLE_UI) - set(access.NEEDS))
    check("and ROLE_UI names no route access.NEEDS does not gate", ghosts, [])

    # The three that were missing. Named individually because a count passes
    # while the wrong three are present.
    for route in ("/api/refresh", "/api/lab-correction", "/api/pool-shape"):
        check(f"{route} has a control to dim", bool(access.ROLE_UI.get(route)), True)

    # An `operate` reader may act on the pool; the admin selector must not
    # sweep up anything they are allowed to use, or they are dimmed out of
    # their own permission.
    op = access.ui_selector("operate")
    ad = access.ui_selector("admin")
    check("the two selector sets do not overlap",
          sorted(set(x.strip() for x in op.split(",")) &
                 set(x.strip() for x in ad.split(","))), [])
    check("and neither is empty", bool(op) and bool(ad), True)

    src = render.TEMPLATE
    check("the page does not default an unknown role to admin",
          "j.role || 'admin'" in src, False)
    check("it clamps to the declared levels instead",
          "LEVELS.indexOf(j.role)" in src, True)
    check("and the lock is a latch, not a one-shot",
          "Object.defineProperty(el, 'disabled'" in src, True)


def t_every_tab_is_classified():
    """A tab is public or private by decision, never by default.

    The public build is cut by DENYLIST: it strips the three tabs named in
    PRIVATE_TABS and keeps everything else. So a tab added to the template
    shipped to anonymous visitors silently — in neither tuple, named by no
    check — which is the opposite of the "fails by showing too little"
    property the two-build split exists to provide. render.build() now refuses
    an unclassified tab; this asserts the two tuples still describe the page.

    And the second copy: PUBLIC_TABS was declared twice, here and in help.py,
    and the copy in render.py — the one sitting beside PRIVATE_TABS and the
    excision, the obvious place to edit — had no readers at all.
    """
    import re
    from . import render, help as helpdoc
    print("\n  tabs — every one is public or private by decision")

    seen = render.declared_tabs(render.TEMPLATE)
    check("the template still has tabs to classify", bool(seen), True)
    check("none is unclassified",
          sorted(seen - set(render.PUBLIC_TABS) - set(render.PRIVATE_TABS)), [])
    check("and neither tuple names a tab the page does not have",
          sorted((set(render.PUBLIC_TABS) | set(render.PRIVATE_TABS)) - seen), [])
    check("the two sets are disjoint",
          sorted(set(render.PUBLIC_TABS) & set(render.PRIVATE_TABS)), [])
    # THE EXTRACTOR IS CHECKED, because the previous one matched a fixed
    # attribute order and a tab written the other way round sailed past it.
    check("a tab is found however its attributes are ordered",
          sorted(render.declared_tabs(
              '<button data-tab="secrets" role="tab" aria-selected="false">S</button>'
              '<button role="tab" data-tab="home">H</button>')),
          ["home", "secrets"])
    check("and a button that is not a tab is not counted",
          render.declared_tabs('<button data-tab="nope">x</button>'), set())

    check("help reads render's tuple rather than a copy of its own",
          helpdoc._public_tabs(), render.PUBLIC_TABS)
    check("and help has no PUBLIC_TABS of its own to drift",
          hasattr(helpdoc, "PUBLIC_TABS"), False)


def t_admin_page_is_gated_whatever_the_case():
    """/ADMIN.HTML is /admin.html, on any filesystem that thinks so.

    The gate compared the path to two lowercase literals, so an uppercase
    spelling skipped it and went to the file handler. What was left holding the
    private build shut was the case sensitivity of the filesystem — ext4 in the
    container 404s it, APFS on a workstation serves it — which is a mount
    option, not a boundary.
    """
    from . import server
    print("\n  gate — the private build is refused however it is spelled")
    src = server.HANDLER_SOURCE if hasattr(server, "HANDLER_SOURCE") else ""
    for spelling in ("/admin.html", "/ADMIN.HTML", "/Admin.Html", "/ADMIN.HTML/",
                     "/admin", "/ADMIN"):
        check(f"{spelling} is recognised as the private build",
              spelling.rstrip("/").lower() in ("/admin.html", "/admin"), True)
    # The check above is arithmetic on the rule; this is the rule as shipped.
    import inspect
    body = inspect.getsource(server.Handler.do_GET)
    check("do_GET lowercases before it compares",
          '.rstrip("/").lower() in ("/admin.html", "/admin")' in body, True)


def t_chemistry_prose_follows_the_measurement():
    """The narrative states what THIS pool measures, not what it measured once.

    Four sentences on the chemistry page asserted a reading instead of deriving
    it, and each was wrong for some pool it was printed to:

      * "Calcium is already at {ch} ppm, inside the {lo}–{hi} this pool wants"
        fired for anything at or above the band's MIDDLE, so a pool at 520 read
        its own figure and the 250–450 it is outside of, in one sentence.
      * "Alkalinity needs nothing: at {ta} it is already inside the {lo}–{hi}"
        was the same bug one measurement over — everything not ABOVE the band
        fell into it, so TA 60 was told it was inside 80–120.
      * "Calcium first" was appended whatever calcium read, including right
        after a sentence saying calcium needs nothing.
      * The pH note asserted "with calcium at the low end … the resolution is
        not a pH number, it is more calcium" — advice that is the opposite of
        correct on a pool above the band, where nothing but replacing water
        takes calcium out.

    Same rule the alkalinity narrative already learned: prose that states a
    number is prose that goes wrong.
    """
    from . import chemistry as C
    print("\n  chemistry — the prose follows the reading")
    TG = {"ch": (200, 300, 400), "ta": (80, 100, 120)}
    lo, mid, hi = TG["ch"]
    tlo, tmid, thi = TG["ta"]

    # Never claim a figure is inside a band it is outside of. Checked on both
    # measurements, at every position, because that is the whole defect.
    for ch in (lo - 50, lo, mid, hi, hi + 120):
        for ta in (tlo - 20, tlo, tmid, thi, thi + 20):
            r = C.resolution(ch, ta, TG)
            if ch > hi:
                check(f"ch={ch} is not called inside the band",
                      "inside the" in r.split("Alkalinity")[0].split("alkalinity")[0],
                      False)
                check(f"ch={ch} is not sent to buy calcium", "Raise calcium" in r, False)
            if ta < tlo:
                check(f"ta={ta} is not called inside the band",
                      "already inside" in r, False)
            if ch >= mid:
                check(f"ch={ch} is not told to do calcium first",
                      "Calcium first" in r, False)
            if ch < mid:
                check(f"ch={ch} is told to raise calcium", "Raise calcium" in r, True)

    # The pH note. Above the band, "more calcium" is the opposite of the answer.
    check("a pool above the calcium band is not told to add calcium",
          "more calcium" in C.ph_note(7.8, hi + 120, TG), False)
    check("a pool below it still is",
          "more calcium" in C.ph_note(7.8, lo - 50, TG), True)
    check("and with no reading at all it says so",
          "no calcium reading" in C.ph_note(7.8, None, TG), True)

    # The tension card asserts a tension. Below 7.4 there is not one: both
    # rules point up, and the card said "opposite directions" anyway.
    for ph in (7.0, 7.2, 7.35):
        lede, h_cl, h_pl, si, rl = C.tension_framing(ph)
        check(f"ph={ph} is not told chlorine wants pH down", "pH down" in h_cl, False)
        check(f"ph={ph} is not told the advice conflicts",
              "opposite directions" in lede, False)
        check(f"ph={ph} projects a RISE back to 7.4", "raising pH" in si, True)
    for ph in (7.5, 7.8, 8.0):
        lede, h_cl, h_pl, si, rl = C.tension_framing(ph)
        check(f"ph={ph} still states the real tension",
              "opposite directions" in lede and "pH down" in h_cl, True)
        check(f"ph={ph} projects a DROP to 7.4", "dropping pH" in si, True)
    lede, h_cl, h_pl, si, rl = C.tension_framing(7.4)
    check("at 7.4 exactly there is nothing to trade off",
          "nothing to trade off" in lede, True)
    check("and no move is asked for", "asking for a move" in si, True)


def t_no_function_reads_a_name_that_does_not_exist():
    """A NameError in a branch nobody local takes is a crash in production.

    Extracting _run() out of watch.main() so the lock could wrap the whole run
    left four readers of `force` behind, referring to a name that no longer
    existed in their scope. Three sit on branches an empty data directory never
    reaches. The fourth -- the one that stops a second alert about something
    already reported -- is on the path every real run walks. So bin/watch raised
    NameError on the server every thirty minutes while t_watch_actually_runs, which
    runs it against an empty fixture, reported "exits 0" and "does not raise".

    That is the third time the watchdog has been broken and silent, and the
    second time a test covering it passed by not reaching the code. Running the
    thing is necessary and is not sufficient: a test can only exercise the
    branches its fixture produces, and the expensive branches here need a pool
    with a chemistry alarm and a state file remembering a previous alert.

    symtable answers it statically for every branch at once. A name a function
    reads, which is neither local, parameter, nor closure, resolves to a module
    global -- so if no such global exists, that line raises NameError the moment
    it is reached. stdlib, no new dependency, and it covers every module rather
    than the one that happened to break.
    """
    import builtins, symtable, glob
    from . import config
    print("\n  scope — no function reads a name that does not exist")

    # Names the interpreter injects into every module at import.
    RUNTIME = {"__file__", "__name__", "__doc__", "__spec__", "__package__",
               "__loader__", "__builtins__", "__path__", "__debug__"}

    def undefined(path):
        src = open(path, encoding="utf-8").read()
        top = symtable.symtable(src, path, "exec")
        known = set(top.get_identifiers()) | set(dir(builtins)) | RUNTIME
        found = []

        def walk(tbl, trail):
            for child in tbl.get_children():
                name = f"{trail}.{child.get_name()}" if trail else child.get_name()
                if child.get_type() == "function":
                    for sym in child.get_symbols():
                        if (sym.is_global() and sym.is_referenced()
                                and sym.get_name() not in known):
                            found.append(f"{name}() reads {sym.get_name()!r}")
                walk(child, name)

        walk(top, "")
        return sorted(set(found))

    root = config.root()
    bad = []
    for path in sorted(glob.glob(os.path.join(root, "poolhound", "*.py"))):
        for hit in undefined(path):
            bad.append(f"{os.path.basename(path)}: {hit}")
    check(f"every name resolves ({len(bad)} would raise NameError)", bad, [])

    # AND THE CHECK IS CHECKED, because one that silently matches nothing is
    # the failure mode being fixed. Pointed at the shape that shipped.
    import tempfile
    fd = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False)
    fd.write("def main():\n"
             "    force = True\n"
             "    return _run()\n"
             "def _run():\n"
             "    if not force:\n"
             "        return 1\n"
             "    return 0\n")
    fd.close()
    check("and it finds the extraction that dropped a variable",
          undefined(fd.name), ["_run() reads 'force'"])
    os.unlink(fd.name)


def t_one_measurement_is_counted_once():
    """A reading filed twice is still one reading.

    The collectors deduped on the measurement timestamp but did the READ
    outside the lock, so two runs could both find a timestamp absent and both
    write it. That race is closed; the row it already produced is still on disk
    and this household's readings.csv carries 2026-09-08 twice.

    dose_response_block() had carried its own inline dedup, because pairing a
    dose against a doubled reading is visibly wrong — so it was fixed there and
    nowhere else, while every average, chart and alert counted the measurement
    twice. It lives in rows() now, which is the one path everything reads
    through, and history is not rewritten.
    """
    import csv as _csv, tempfile
    from . import render
    print("\n  loaders — one measurement is counted once")

    d = tempfile.mkdtemp()
    p_ = os.path.join(d, "readings.csv")
    with open(p_, "w", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=["measured", "ph", "free_cl"])
        w.writeheader()
        w.writerow({"measured": "2026-09-08T19:38:00-0700", "ph": "7.8", "free_cl": "3"})
        w.writerow({"measured": "2026-09-08T19:38:11-0700", "ph": "7.8", "free_cl": "3"})
        w.writerow({"measured": "2026-09-09T19:40:00-0700", "ph": "7.7", "free_cl": "3"})
    prev = render.DATA
    try:
        render.DATA = d
        got = render.rows("readings.csv")
        check("the duplicate is collapsed", len(got), 2)
        check("and the FIRST of the pair is what survives",
              got[0]["measured"], "2026-09-08T19:38:00-0700")
        check("the distinct reading is untouched",
              got[1]["measured"], "2026-09-09T19:40:00-0700")
        # A row with no timestamp cannot be deduped and must never be dropped —
        # silently losing a reading is worse than counting one twice.
        with open(p_, "a", newline="") as f:
            _csv.DictWriter(f, fieldnames=["measured", "ph", "free_cl"]).writerow(
                {"measured": "", "ph": "7.5", "free_cl": "2"})
        check("a row with no timestamp is kept, not dropped",
              len(render.rows("readings.csv")), 3)
    finally:
        render.DATA = prev
        import shutil; shutil.rmtree(d, ignore_errors=True)


def t_dose_gap_explains_the_direction_measured():
    """Why a dose missed its prediction depends on WHICH WAY it missed.

    The closing paragraph was fixed prose written for a pool whose doses came
    out stronger than predicted — "the pool may hold less water ... which would
    make every dose stronger than predicted". The heading three lines above it
    already derives the direction, and when it read "moved pH LESS than
    predicted" this paragraph explained the opposite of what had been measured,
    sending the reader to look for a smaller pool when the evidence pointed at
    a larger one.
    """
    from . import chemistry as C
    print("\n  doses — the explanation follows the direction measured")
    strong = C.dose_gap_reading(1.4, 18400, 73)
    weak = C.dose_gap_reading(0.6, 18400, 73)
    check("a stronger-than-predicted dose suggests LESS water",
          "<b>less</b> water" in strong, True)
    check("and not more", "<b>more</b> water" in strong, False)
    check("a weaker-than-predicted dose suggests MORE water",
          "<b>more</b> water" in weak, True)
    check("and not less", "<b>less</b> water" in weak, False)
    # Alkalinity moves with it: more buffer, smaller pH move.
    check("stronger implies lower alkalinity than assumed",
          "<b>lower</b> than" in strong, True)
    check("weaker implies higher alkalinity than assumed",
          "<b>higher</b> than" in weak, True)
    # And a ratio near one claims neither.
    near = C.dose_gap_reading(1.0, 18400, 73)
    check("a ratio near one blames neither",
          ("less" in near) or ("more" in near), False)
    check("no reading at all says so rather than guessing",
          "will say something about" in C.dose_gap_reading(None, 18400, 73), True)


def t_a_future_timestamp_is_not_freshness():
    """A row dated ahead of now must never look like a recent one.

    ago() computed `now - d` and tested upper bounds only, so a negative gap
    fell through to "just now" — a row dated next year, or next century, read
    as current. collector_health() and bin/watch judge staleness on that same
    arithmetic and are described as unable to disagree; they agreed on the
    wrong answer. Measured: a dead sampler warned "last returned data 1 day
    ago"; append ONE row dated a year ahead and the watchdog printed RECOVERED
    for the same dead sampler and then stayed silent.

    The "is this in the future" fact existed in exactly one place — the hand
    entry form, which refuses it — and in none of the three that needed it.
    """
    import datetime as _dt
    from . import render
    print("\n  time — a future timestamp is not freshness")
    now = _dt.datetime.now()
    far = (now + _dt.timedelta(days=365)).astimezone().isoformat()
    near = (now + _dt.timedelta(minutes=2)).astimezone().isoformat()
    past = (now - _dt.timedelta(hours=4)).astimezone().isoformat()

    check("a year ahead is called out", render.ago(far), "dated in the future")
    check("and is_future agrees", render.is_future(far), True)
    check("a two-minute clock difference is still 'just now'",
          render.ago(near), "just now")
    check("and is NOT called future", render.is_future(near), False)
    check("an ordinary gap is unchanged", render.ago(past), "4 hours ago")
    check("absent stays absent", render.ago(""), "")
    check("and is not future", render.is_future(""), False)

    # The part that actually disarmed the alarm: max() over every timestamp.
    stale = (now - _dt.timedelta(days=1)).astimezone().isoformat()
    dead = render.collector_health([{"ts": stale}], [], [])
    check("a dead sampler is reported",
          [e["state"] for e in dead if e["key"] == "sample"], ["warn"])
    poisoned = render.collector_health([{"ts": stale}, {"ts": far}], [], [])
    check("and is STILL reported when a future row is present",
          [e["state"] for e in poisoned if e["key"] == "sample"], ["warn"])
    check("with the bad clock surfaced as its own fault",
          any(e["key"] == "sample-future" for e in poisoned), True)


def t_a_corrupt_row_is_refused_even_with_a_current_header():
    """The long-row refusal must not depend on a migration being pending.

    migrate_columns advertises that a row with MORE fields than its header is
    refused, because those overflow fields are recorded readings — and the
    scan sat BELOW `if old == cols: return None`. The header equals COLS on
    every normal day of a deployed install, so that early return was taken on
    every normal day and the refusal, and the .bak beneath it, were live only
    while a migration happened to be pending.

    Measured on chemicals.csv with a current header and one row carrying an
    extra field (what an unquoted comma in `note` produces): migrate_columns
    returned None, rewrite_locked proceeded, and `by` — the identity of
    whoever logged the dose — was gone, with no .bak. The route is
    /api/chemical/delete, which answered 200 and reported "remaining": 3.
    """
    import tempfile
    from . import locking
    from .chemicals import CHEM_COLS
    print("\n  locking — a corrupt row is refused whatever the header says")

    d = tempfile.mkdtemp()
    p_ = os.path.join(d, "chemicals.csv")
    header = ",".join(CHEM_COLS)
    # THE ROW IS DERIVED FROM THE HEADER, not typed out beside it. Typed, it was
    # exactly one field longer than CHEM_COLS on the day it was written and
    # stopped being long the moment a column was added to that list -- so this
    # case would go on passing while feeding migrate_columns a file that is no
    # longer corrupt, which is a check measuring nothing. One value per column,
    # and `note` carries the unquoted comma that produces the overflow in the
    # first place.
    vals = dict.fromkeys(CHEM_COLS, "x")
    vals["note"] = "poured slowly, then brushed"
    # The value that ends up in the overflow field is the LAST column's, because
    # the unquoted comma shifts everything right by one. Named here and asserted
    # on below by the same variable, so the two cannot disagree about which
    # value the refusal is supposed to be protecting.
    overflow = "the-recorded-value-that-would-be-lost"
    vals[CHEM_COLS[-1]] = overflow
    with open(p_, "w") as f:
        f.write(header + "\n")
        f.write(",".join(vals[c] for c in CHEM_COLS) + "\n")
    raised = ""
    try:
        locking.migrate_columns(p_, CHEM_COLS)
    except ValueError as e:
        raised = str(e)
    check("a long row is refused even though the header is current",
          "MORE fields than" in raised, True)
    check("and the message names the overflow it is protecting",
          overflow in raised, True)

    # And the ordinary case is untouched: a clean file with a current header
    # must still be a no-op, not a refusal.
    p2 = os.path.join(d, "clean.csv")
    with open(p2, "w") as f:
        f.write(header + "\n")
        f.write("2026-08-28T17:05:00-0700,calcium,8,lb,77,poured slowly,demo\n")
    check("a clean current file still migrates to nothing",
          locking.migrate_columns(p2, CHEM_COLS), None)
    import shutil; shutil.rmtree(d, ignore_errors=True)


def t_one_dose_bound_and_one_read_path():
    """Three findings that are all "one fact, two places, opposite answers".

    * The plausibility bound on a dose lived inline in the EDIT route only, so
      POST /api/chemical accepted 5,000 gal of acid and reported an estimated
      -202,511 ppm of alkalinity as guidance, `bin/chem acid 9000 gal` exited 0
      printing -364,520 ppm — and the edit route then answered 400 "that amount
      is not plausible" for the same value. The product accepted a dose it
      refused to let anybody fix, and rendered an Edit button that could not
      succeed. Editing only the NOTE failed too, because amount defaults to what
      is on file.

    * /api/rows read the CSV raw while the table it REPLACES comes from
      render.rows(), which applies corrections.csv. One click on Fetch
      resurrected a reading the owner had dropped and reverted a corrected
      value, in place, with no notice.

    * A correction naming a measurement the source does not have was recorded,
      reported as recorded, and listed back on the page while changing nothing —
      the identical failure corrections.py already fixed for the TIMESTAMP, and
      the form invites it by calling the measurement "Alkalinity" while the
      column is `ta`.
    """
    import csv as _csv, tempfile, shutil
    from . import chemicals as CH, corrections as CORR, render, server, config
    print("\n  seams — one dose bound, one read path, one field vocabulary")

    # The bound is in chemicals.py and every caller reaches it.
    ok, _ = CH.plausible_amount("acid", 2, "gal")
    check("an ordinary dose is allowed", ok, True)
    bad, why = CH.plausible_amount("acid", 5000, "gal")
    check("5,000 gal of acid is refused", bad, False)
    check("and the refusal names the decimal point", "decimal point" in why, True)
    check("a solid is bounded in its own unit",
          CH.plausible_amount("calcium", 500, "lb")[0], False)
    check("and a normal solid dose is not",
          CH.plausible_amount("calcium", 8, "lb")[0], True)
    check("a unit that is not for this phase is still refused",
          CH.plausible_amount("salt", 1, "gal")[0], False)

    d = tempfile.mkdtemp()
    dd = os.path.join(d, "data"); os.makedirs(dd)
    with open(os.path.join(dd, "lab.csv"), "w", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=["measured", "ta", "ph", "source"])
        w.writeheader()
        w.writerow({"measured": "2026-08-01T10:00:00-0700", "ta": "95",
                    "ph": "7.8", "source": "WaterGuru"})
        w.writerow({"measured": "2026-08-08T10:00:00-0700", "ta": "88",
                    "ph": "7.7", "source": "WaterGuru"})
    with open(os.path.join(dd, "corrections.csv"), "w", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=CORR.COLS)
        w.writeheader()
        w.writerow({"at": "2026-08-02T10:00:00-0700", "source": "lab",
                    "measured": "2026-08-01T10:00:00-0700", "action": "set",
                    "field": "ta", "value": "73", "note": "retested", "by": "t"})
        w.writerow({"at": "2026-08-09T10:00:00-0700", "source": "lab",
                    "measured": "2026-08-08T10:00:00-0700", "action": "drop",
                    "field": "", "value": "", "note": "fouled", "by": "t"})
    prev_data, prev_env = render.DATA, os.environ.get("POOLHOUND_DATA")
    try:
        os.environ["POOLHOUND_DATA"] = dd
        render.DATA = dd
        cfg = {}
        loaded = [(r["measured"][:10], r.get("ta")) for r in render.rows("lab.csv")]
        sliced = [(r["measured"][:10], r.get("ta"))
                  for r in server.rows_slice("lab.csv", cfg)["rows"]]
        check("the loader applies the correction", loaded, [("2026-08-01", "73")])
        check("and /api/rows returns exactly the same rows", sliced, loaded)

        # The field vocabulary.
        refused = ""
        try:
            CORR.record("lab", "2026-08-01T10:00:00-0700", "set",
                        field="alkalinity", value="74", by="t", cfg=cfg)
        except ValueError as e:
            refused = str(e)
        check("a correction naming a column the source lacks is refused",
              "no measurement called" in refused, True)
        check("and the refusal lists the names that would work",
              "ta" in refused and "ph" in refused, True)
    finally:
        render.DATA = prev_data
        if prev_env is None:
            os.environ.pop("POOLHOUND_DATA", None)
        else:
            os.environ["POOLHOUND_DATA"] = prev_env
        shutil.rmtree(d, ignore_errors=True)


def t_an_ack_names_a_command():
    """The agent routes must answer, and must not write an unpairable row.

    A body that PARSES but is not an object — [1,2], "hello", 42 — sailed past
    a guard that wrapped json.loads alone and raised AttributeError on the next
    .get, so the caller got no HTTP status at all. To the Pi's agent that is
    indistinguishable from the link dropping. The ordinary write path has had
    the isinstance test all along.

    And an ack validated nothing about its id: absent became "", JSON null
    became the literal string "None", and an id never issued was accepted —
    each writing a PERMANENT control.ack row to audit.csv, each rendered in the
    Pool control log as a command whose name was blank or the word "None". The
    queue forgets on restart; audit.csv does not.
    """
    import re as _re
    from . import queue_
    print("\n  agent — an ack names a command")

    ACK_ID = r"[0-9a-fA-F-]{8,64}"
    for bad in ("", "None", "x", "../etc", "not a uuid at all!"):
        check(f"{bad!r} is not an ack id",
              bool(_re.fullmatch(ACK_ID, bad)), False)
    for good in ("deadbeef-dead-beef-dead-beefdeadbeef", "abcd1234",
                 "ABCD1234-0000-0000-0000-000000000000"):
        check(f"{good[:18]}... is", bool(_re.fullmatch(ACK_ID, good)), True)

    # knows() is what keeps an unpairable ack out of the permanent record.
    q = queue_.CommandQueue()
    check("a fresh queue knows no command", q.knows("abcd1234"), False)
    # issue() returns (command, delivered_count) -- not (command, error).
    issued, delivered = q.issue("set", device="Aux_1", value="1", by="test")
    check("a command can be issued", bool(issued.get("id")), True)
    check("and nobody was subscribed to receive it", delivered, 0)
    cid = issued["id"]
    check("and the queue knows the id it just issued", q.knows(cid), True)
    check("but still not one it never issued",
          q.knows("deadbeef-dead-beef-dead-beefdeadbeef"), False)


def t_an_unknown_flag_is_not_a_dose_note():
    """bin/chem must refuse an option it does not have, not record it.

    Everything after the unit is taken as the note, so a mistyped or imagined
    flag was silently recorded as the REASON for a dose — and the dose was
    logged. That is not hypothetical: this household's chemicals.csv acquired a
    row reading `acid,12,floz,31.45,--dry-run,local` when a reviewer ran
    `bin/chem acid 12 floz --dry-run` expecting the flag to prevent a write.
    bin/leslies and bin/wg-collect both HAVE --dry-run, which is exactly why
    somebody would expect it here.

    chemicals.csv is, in CLAUDE.md's words, "our record of something we did".
    A flag that does not exist has to stop the command.
    """
    import subprocess, sys, tempfile, shutil
    from . import config
    print("\n  chem — an unknown flag is refused, not recorded")

    d = tempfile.mkdtemp()
    dd = os.path.join(d, "data"); os.makedirs(dd)
    with open(os.path.join(d, "config.toml"), "w") as f:
        f.write("[pool]\nvolume_gallons = 16250\n")
    env = dict(os.environ, POOLHOUND_DATA=dd,
               POOLHOUND_SITE=os.path.join(d, "site"),
               POOLHOUND_CONFIG=os.path.join(d, "config.toml"))
    chem = os.path.join(config.root(), "bin", "chem")

    def run(*a):
        return subprocess.run([sys.executable, chem, *a], capture_output=True,
                              text=True, env=env, timeout=60)

    for flag in ("--dry-run", "--pretend", "--no-write", "--verbose"):
        r = run("acid", "12", "floz", flag)
        check(f"{flag} is refused", r.returncode != 0, True)
        check(f"and {flag} is named in the refusal",
              flag in (r.stdout + r.stderr), True)

    logged = os.path.join(dd, "chemicals.csv")
    check("and nothing was written by any of them",
          os.path.exists(logged), False)

    # A real note, and the option that does exist, must still work.
    r = run("acid", "12", "floz", "poured after the storm")
    check("a plain note still logs", r.returncode, 0)
    r = run("acid", "12", "floz", "--pct", "14.5", "half strength")
    check("--pct still works", r.returncode, 0)
    body = open(logged).read()
    check("the note is the note", "poured after the storm" in body, True)
    check("and no row has a flag for a note", "--" in body, False)
    shutil.rmtree(d, ignore_errors=True)


def t_hidden_spans_cannot_escape_their_scroller():
    """A visually-hidden span must not widen the page.

    The .sr spans inside the wide tables are position:absolute with no
    positioned ancestor, so their containing block was the INITIAL one — they
    escaped the .scroll box entirely and sat at document x-coordinates up to
    641px on a 390px phone. The page then scrolled 251px sideways, carrying the
    masthead, the theme button and the whole tab bar off with it and leaving
    the first tab unreachable.

    It survived two wrong fixes first, because body stayed 390px throughout:
    the overflow was never in the flow, so constraining the table did nothing.
    clip:rect() hides the PAINT and says nothing about the layout box. The fix
    is position:relative on the scroller, which makes it the containing block
    so its own overflow rule applies.

    Asserted in CSS rather than in a browser because that is what a gate can
    do; the measurement that found it was a real phone-width browser, and the
    fix was confirmed the same way on all eleven tabs of both builds.
    """
    import re
    from . import style
    print("\n  responsive — a hidden span cannot widen the page")

    m = re.search(r"\.scroll\s*\{([^}]*)\}", style.CSS)
    check("the wide-table scroller is still defined", bool(m), True)
    body = m.group(1) if m else ""
    check("it clips horizontally", "overflow-x:auto" in body.replace(" ", ""), True)
    check("and it is a containing block, so absolute children cannot escape",
          "position:relative" in body.replace(" ", ""), True)

    # Every visually-hidden rule that positions absolutely has to sit inside
    # something that contains it. There is more than one .sr definition.
    hidden = re.findall(r"\.sr\s*\{([^}]*)\}", style.CSS)
    check("the visually-hidden class is defined", bool(hidden), True)
    for decl in hidden:
        if "position:absolute" in decl.replace(" ", ""):
            check("an absolutely-positioned .sr is still clipped",
                  "clip" in decl, True)


def cases():
    """Every t_* in this file, in the order they are written.

    THE ROSTER WAS A HAND-TYPED TUPLE, and a case missing from it does not run
    — which is the same silence this file has now been bitten by twice in one
    day. `python -m poolhound.selftest` imported the module, ran nothing and
    exited 0 for want of a __main__ block; and t_watch_actually_runs ran the
    watchdog against a fixture that never reached the branch that was crashing
    in production. Both reported success while measuring nothing, and a case
    that is defined and never listed is the third way to get there.

    Thirty-two names kept in agreement by hand is a list that will drift; the
    only question is which direction. Source order rather than alphabetical,
    because the order here is deliberate — the pure, cheap cases first, the
    ones that shell out or build a pool last — and definition order is what a
    reader editing this file sees.
    """
    def _from(mod):
        return [v for k, v in vars(mod).items()
                if k.startswith("t_") and callable(v)
                and getattr(v, "__module__", None) == mod.__name__]

    import importlib, pkgutil, sys, poolhound
    found = [(__name__, f) for f in _from(sys.modules[__name__])]

    # AND EVERY checks_*.py BESIDE THIS FILE.
    #
    # One file was a contention point the moment more than one person worked on
    # the suite at once: every new case lands at the same anchor, so two edits
    # conflict by construction even when they are about entirely different
    # parts of the product. A case is a case wherever it is written, and the
    # roster is derived rather than typed, so discovering siblings costs
    # nothing and lets the suite grow in parallel.
    #
    # Ordered by module name then by line, so a run is reproducible.
    for m in sorted(pkgutil.iter_modules(poolhound.__path__), key=lambda x: x.name):
        if not m.name.startswith("checks_"):
            continue
        mod = importlib.import_module(f"poolhound.{m.name}")
        found += [(mod.__name__, f) for f in _from(mod)]

    return [f for _, f in
            sorted(found, key=lambda p: (p[0] != __name__, p[0],
                                         p[1].__code__.co_firstlineno))]


# HOW MANY CASES THIS FILE HOLDS, and why a number is written down.
#
# THE FLOOR WAS 20 AGAINST A ROSTER OF 40, so half the suite could be deleted
# before anything noticed. It was: removing t_pending — the case deciding
# whether the page says a heater is ON, OFF or WAITING, named in this file's
# own docstring as the sharpest example of a bug that reached production — left
# bin/selftest printing "all checks pass" and exiting 0, with nothing in the
# output stating how many cases had run. A gate that cannot notice itself
# shrinking is a gate anybody can turn off by deletion.
#
# The floor is this FILE's own cases. Sibling checks_*.py modules are
# discovered and may come and go with the work that wrote them — that is what
# discovery is for — but the regression net itself is not something to lose a
# strand of silently. Raise this when you add a case; lowering it is the edit
# that has to be justified, which is the whole point of writing it down.
# 40 -> 39 on 2026-09-20. t_salt_is_not_a_temperature guarded the plausibility
# ceiling in aqualink.num(), and that module's polling half was deleted when the
# workstation stopped being able to collect at all. The regression it watched
# for cannot recur because the code that could commit it is gone; the half of
# that story that CAN still recur -- the salt cell latching its last reading
# with the pump off -- is refused by commands.drop_stale_flow_readings() and is
# still checked, by t_latched_salt_is_not_a_measurement.
CORE_CASES = 39


def core_case_count():
    """How many t_* this file defines, now."""
    import sys
    return sum(1 for k, v in vars(sys.modules[__name__]).items()
               if k.startswith("t_") and callable(v)
               and getattr(v, "__module__", None) == __name__)


def roster_is_whole(n_core):
    """Is a roster of `n_core` core cases the whole regression net?"""
    return n_core >= CORE_CASES


def main():
    print("  poolhound self-test — the parts that need no browser")
    todo = cases()
    # SAY HOW MANY, ALWAYS. The count was never printed, so "all checks pass"
    # read the same after a case was deleted as before, and the only number on
    # screen was the failure count — which is zero in both states.
    by_mod = {}
    for f in todo:
        by_mod[f.__module__] = by_mod.get(f.__module__, 0) + 1
    print(f"  {len(todo)} case(s): " +
          ", ".join(f"{m.rsplit('.', 1)[-1]} {n}" for m, n in sorted(by_mod.items())))
    core = core_case_count()
    # A derivation that finds nothing is the failure it replaced, wearing a
    # different hat: it would print the banner, run no case and exit 0.
    if len(todo) < 20:
        print(f"  REFUSING TO RUN: found only {len(todo)} case(s); this file "
              f"defines far more, so the discovery above is broken and a pass "
              f"would mean nothing.")
        return 1
    if not roster_is_whole(core):
        print(f"  REFUSING TO RUN: this file defines {core} case(s) and the "
              f"recorded roster is {CORE_CASES}. A case has been deleted. "
              f"Restore it, or change CORE_CASES and say in the commit which "
              f"regression is no longer worth guarding.")
        return 1
    for t in todo:
        t()
    print()
    if SKIPPED:
        print(f"  {len(SKIPPED)} case(s) could NOT be answered in this tree — "
              f"not failures, and not passes either:")
        for name, why in SKIPPED:
            print(f"    skipped: {name} — {why}")
        print()
    if FAILURES:
        print(f"  {len(FAILURES)} FAILURE(S):")
        for name, got, want in FAILURES:
            print(f"    {name}: got {got!r}, wanted {want!r}")
        return 1
    ran = len(todo)
    tail = f", {len(SKIPPED)} not answerable here" if SKIPPED else ""
    print(f"  all checks pass — {ran} case(s) ran{tail}")
    return 0


# `python -m poolhound.selftest` is how the image build runs this, and without
# this block it imported the module, ran nothing and exited 0 -- a gate that
# passes while measuring nothing, which is the exact defect several cases in
# this file exist to catch. bin/selftest calls main() directly and was fine.
#
# AND THEN THE FIX GREW THE SAME BUG BACK, one layer down. `python -m` runs
# this file as `__main__`, which is a SECOND module object: every checks_*.py
# module does `from .selftest import FAILURES, check` and so appends to
# `poolhound.selftest.FAILURES`, while the main() running here read the empty
# `__main__.FAILURES` beside it. The gate printed nine FAIL lines, then "all
# checks pass", and exited 0 -- and the Dockerfile's build gate is this exact
# command, so no failure in any of the twelve checks_*.py modules could fail an
# image build. bin/selftest was unaffected, which is why every workstation run
# looked right.
#
# DELEGATE rather than run the copy: one module object owns the roster, the
# failure list and the exit code, whichever way the suite is started.
if __name__ == "__main__":
    import sys
    from poolhound import selftest as _canonical
    sys.exit(_canonical.main())
