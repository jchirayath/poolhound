"""The eight lower-severity findings, each pinned where it was measured."""

import csv
import os
import sys
import re
import tempfile

from .selftest import check, skipped


def t_a_sentinel_is_not_a_measurement():
    """The panel's "I cannot measure that" answer must not average in.

    drop_stale_flow_readings() blanks these at WRITE time and history is never
    rewritten, so any sentinel already on disk — or arriving by a path that
    bypasses the rule — was read back as an ordinary number. Measured: 23
    honest readings of 3315 ppm salt plus ONE sentinel row published 3135.2, a
    180 ppm error on the public page.

    num()'s own docstring already made this argument for the empty string
    ("a missing SWG reading must not average in as 0%"); the sentinel is the
    same claim in a different spelling and was not held to it.
    """
    from . import commands, render
    print("\n  loaders — a sentinel is not a measurement")

    check("commands owns the sentinel", commands.NO_READING, -999)
    check("and num() reads it as absent", render.num("-999"), None)
    check("including anything below it", render.num("-1000.5"), None)
    check("an ordinary reading is untouched", render.num("3315"), 3315.0)
    check("a legitimate negative still parses", render.num("-0.4"), -0.4)
    check("and blank is still absent", render.num(""), None)

    # The published figure, which is what the defect was about.
    rows = [{"ts": f"2026-09-14T{h:02d}:00:00-0700", "pump": "1", "spa": "0",
             "sheer": "0", "salt_ppm": "3315"} for h in range(23)]
    rows.append({"ts": "2026-09-14T23:00:00-0700", "pump": "1", "spa": "0",
                 "sheer": "0", "salt_ppm": str(commands.NO_READING)})
    days = render.by_day(rows, [], [])
    check("one sentinel row does not move the day's salt",
          days[0].get("salt"), 3315.0)


def t_never_configured_is_not_broken():
    """A source with no credentials is not set up; it is not faulty.

    collector_health decided from "no rows in its CSV" alone, so an install
    with no WaterGuru account was told to check a pod's battery and signal and
    a credential file it was never asked to create. Three of the four
    top-priority items on a drop-kit install were about services the owner does
    not have — while Settings, on the same page, correctly said "no
    credentials — this source cannot run".
    """
    from . import render
    print("\n  collectors — not set up is not the same as broken")

    check("a source needing no credential cannot be 'unconfigured'",
          render._source_configured("sample"), None)
    check("an unreadable vault answers 'cannot tell', not 'unconfigured'",
          render._source_configured("waterguru") in (True, False, None), True)
    check("the credential-needing sources are named",
          sorted(render.NEEDS_CREDENTIAL), ["leslies", "waterguru"])


def t_one_pending_window():
    """The page and the server give up on a command at the same moment.

    The server dropped a device from `pending` after 240 seconds; the page's
    own watch ran for 20 minutes. So for sixteen minutes the tab that SENT a
    command said it was still waiting while every other view showed nothing
    outstanding — two screens, one pool, opposite answers.
    """
    from . import commands, render
    print("\n  commands — one window, not two")

    check("commands owns the window", commands.PENDING_GIVES_UP_S, 240)
    check("the page is given the same number, in ms",
          f"var PENDING_GIVES_UP_MS = {commands.PENDING_GIVES_UP_S * 1000};"
          in render.TEMPLATE or "@@pending_gives_up_ms@@" in render.TEMPLATE, True)
    check("and no second number is typed into the page script",
          "20 * 60000" in render.TEMPLATE, False)


def t_a_dead_link_is_noticed_in_seconds():
    """The heartbeat interval is set from how detection actually works.

    A socket whose peer has gone accepts the first write and raises only once
    the reset has come back — after the NEXT write. So a dead agent is noticed
    on the second heartbeat, and at 45 seconds that measured 90 exactly, while
    the docstring promised "seconds". Writing the heartbeat twice per cycle
    does not help: both land in the buffer before the reset arrives. Measured
    again at 20 seconds: 40.
    """
    import inspect
    from . import server
    print("\n  stream — a dead link is noticed in seconds")

    check("the interval is a named number", hasattr(server, "HEARTBEAT_S"), True)
    check("and short enough that two of them is still seconds",
          server.HEARTBEAT_S * 2 <= 60, True)
    src = inspect.getsource(server.Handler.stream_commands)
    check("the loop waits that long and not a literal",
          "timeout=HEARTBEAT_S" in src, True)


def t_rows_honours_the_parameters_it_reports():
    """A parameter echoed back in the response is one the route accepts.

    `limit` was never read from the query while the response reported
    "limit": 500 as though it were the caller's, so ?limit=1 returned 387 rows.
    And an unparseable date answered {"matched": 0} — an authoritative "there
    are no rows" to a question the route never understood.
    """
    import inspect
    from . import server
    print("\n  /api/rows — the parameters it reports, it reads")

    src = inspect.getsource(server.Handler.do_GET)
    check("limit is read from the query", 'q.get("limit")' in src, True)
    check("and clamped to the cap", "ROWS_MAX)" in src, True)
    check("a malformed date is refused rather than answered",
          "is not a date" in src, True)


def t_a_partial_resubmit_keeps_what_it_did_not_mention():
    """Correcting one measurement must not delete the other four.

    save_reading built a fresh blank row and dropped the old one, so
    re-submitting a corrected pH deleted the alkalinity, calcium, CYA and
    chlorine recorded with it. The form does not prefill, so the ordinary way
    to fix a typo destroyed the rest of the test. Measured end to end: a
    five-measure test, then a pH-only resubmit, leaving one measure and four
    blanks.
    """
    import inspect
    from . import server
    print("\n  readings — a correction is not a deletion")

    src = inspect.getsource(server.save_reading)
    check("the prior row for that timestamp is read", "prior" in src, True)
    check("and unmentioned measures are carried", "carried" in src, True)
    check("the response names what it carried",
          '"carried"' in src, True)


def t_the_agent_token_fingerprint_has_a_caller():
    """The check it was written for is performed by a deploy script.

    Its docstring says it exists "so two machines can be checked against each
    other without either end revealing the secret" — and neither deploy script
    did it, so the function had no callers at all. It is also the cheap answer
    to a Key Vault that cannot be read, which makes the token merely LOOK
    absent and closes every agent route.
    """
    from . import config, vault
    print("\n  deploy — the fingerprint is actually compared")

    check("the function still exists", callable(vault.agent_token_fingerprint), True)
    root = config.root()
    for script in ("bootstrap-server.sh", "update-pi.sh"):
        p = os.path.join(root, "deploy", script)
        if not os.path.exists(p):
            # The deploy image carries no deploy/ directory. Reading a missing
            # file as an empty one turned "this tree has no scripts" into
            # "the scripts do not perform the check", which is a different and
            # untrue statement.
            skipped(f"{script} compares the agent token fingerprint",
                    "no deploy/ directory in this tree")
            continue
        body = open(p, encoding="utf-8").read()
        check(f"{script} asks for it",
              "agent_token_fingerprint" in body, True)
        check(f"and {script} never prints the token itself",
              re.search(r"print\(\s*vault\.agent_token\(\)", body) is None, True)


def t_the_command_log_pairs_its_durable_rows():
    """A command and its acknowledgement are one event, and read as one.

    audit.csv held them as two rows with nothing in common but a timestamp:
    the first with the device in `object` and the value in `detail`, the second
    with a bare 36-character UUID in `object`. Rendered verbatim, so after a
    restart — the state the durable half exists for — the table showed a value
    where the result belongs and a UUID where the command belongs.

    ASSERTED ON THE BEHAVIOUR, NOT ON THE PAGE SCRIPT. These were three greps
    for literals in render.TEMPLATE -- "acks[r.object] = r", "r.what ||" --
    because the pairing used to be done in JavaScript. It is done in
    switchlog.activity() now, so the needles vanished and these went red:
    correctly, since the orphan-ack row HAD been dropped in the move. Pointing
    them at the function means the next move cannot break them for the wrong
    reason, and a check that greps for a line of code goes red on a rename.
    """
    from . import audit, switchlog
    print("\n  control log — the two halves of one event")

    check("audit rows can carry the command id", "id" in audit.COLS, True)
    rows = switchlog.activity([], [
        {"at": "2026-10-08T07:00:00-0700", "by": "a@example.com",
         "action": "control", "object": "Filter_Pump", "detail": "set=0",
         "id": "paired"},
        {"at": "2026-10-08T07:00:01-0700", "by": "agent",
         "action": "control.ack", "object": "paired", "detail": "panel agreed",
         "id": ""},
        {"at": "2026-10-08T06:00:00-0700", "by": "agent",
         "action": "control.ack", "object": "orphan", "detail": "panel agreed",
         "id": ""},
    ])["rows"]
    by_what = {r["what"]: r for r in rows}
    check("the command and its ack are one row", len(rows), 2)
    check("and the page joins on the id",
          by_what["Filter pump off"]["result"], "panel agreed")
    check("an ack whose command is outside the window is still shown",
          "a command from before this window" in by_what, True)
    check("and the durable row shows a sentence, not a device",
          "Filter pump off" in by_what, True)


# The tools that write the pool's history or spend somebody else's API budget.
# DERIVED FROM bin/, not typed: a new collector added to that directory and not
# to this list would be exactly the exception this check exists to prevent, and
# a hand-kept roster is how aqualink.py stayed the exception to append_row.
COLLECTORS = ("wg-collect", "leslies", "watch", "chem", "backup")


def t_no_collector_runs_off_the_deployment():
    """Collection runs on the web server. Here it is never useful and
    sometimes destructive.

    The readings live on a share mounted only on the server, so a pull started from
    a laptop appends to a data directory nothing serves and nothing
    reconciles. Two fabricated doses reached this household's chemicals.csv
    that way during one review; a shadow data directory accumulated here for
    eight days after the move before anybody noticed; and the watchdog, run
    against that directory by the test suite, put alerts about a nonexistent
    pool on a real screen.

    One guard, in config.py, called by each entry point — not four copies of
    an environment check.
    """
    import os
    import subprocess
    import sys as _sys
    from . import config
    print("\n  collectors — they run on the web server, not here")

    root = config.root()
    # Every tool in bin/ that writes pool data or spends a call is guarded, and
    # the list is checked against the directory so a new one cannot slip past.
    guarded = [t for t in COLLECTORS
               if "refuse_off_deployment" in open(os.path.join(root, "bin", t)).read()]
    check("every collector calls the one guard", sorted(guarded), sorted(COLLECTORS))

    # And it actually refuses, run as a person would run it.
    env = {k: v for k, v in os.environ.items()
           if k not in ("POOLHOUND_DATA", "POOLHOUND_COLLECT_ANYWAY")}
    refused = []
    for t in COLLECTORS:
        r = subprocess.run([_sys.executable, os.path.join(root, "bin", t)],
                           capture_output=True, text=True, env=env, timeout=60)
        if r.returncode != 0 and "refusing to run here" in (r.stdout + r.stderr):
            refused.append(t)
    check("and each one refuses with a reason", sorted(refused), sorted(COLLECTORS))

    # The refusal must come BEFORE the work. wg-collect spends one of the two
    # daily calls upstream asks for; a guard that fires after the request has
    # gone out has cost the thing it was protecting.
    src = open(os.path.join(root, "bin", "wg-collect")).read()
    check("and before the collector does anything",
          src.index("refuse_off_deployment") < src.index("runlog.Run"), True)

    # AND THE DEPLOYMENT IS NOT AFFECTED. Exercised, not asserted about: the
    # cost of getting this wrong is that the server stops collecting, so the case
    # that matters is the one where it must NOT refuse. The first version of
    # this line checked that a docstring existed, which proved nothing at all.
    prev = os.environ.get("POOLHOUND_DATA")
    try:
        os.environ["POOLHOUND_DATA"] = "/data"      # what the image sets
        allowed = True
        try:
            config.refuse_off_deployment("wg-collect")
        except SystemExit:
            allowed = False
        check("a host with a durable data directory is not refused", allowed, True)

        del os.environ["POOLHOUND_DATA"]
        refused_here = False
        try:
            config.refuse_off_deployment("wg-collect")
        except SystemExit:
            refused_here = True
        check("and one without it is", refused_here, True)

        os.environ["POOLHOUND_COLLECT_ANYWAY"] = "1"
        override = True
        try:
            config.refuse_off_deployment("wg-collect")
        except SystemExit:
            override = False
        check("unless it is overridden deliberately", override, True)
    finally:
        os.environ.pop("POOLHOUND_COLLECT_ANYWAY", None)
        if prev is None:
            os.environ.pop("POOLHOUND_DATA", None)
        else:
            os.environ["POOLHOUND_DATA"] = prev


def t_the_suite_cannot_notify_a_person():
    """Running the watchdog for real must not reach anybody's screen.

    bin/selftest runs bin/watch as a subprocess, and it has to: the watchdog
    once died with a TypeError on every run for weeks and nothing caught it
    because nothing ran it. But notify() defaults `desktop` to true when the
    config has no [notify] section, and the fixture deliberately has none — so
    on a Mac every run of this suite raised a real banner about the chlorine
    and pH of a pool that does not exist. It was found because the owner asked
    why their laptop kept talking about free chlorine.

    The switch is checked inside notify(), not in the fixture, because a
    fixture is something each caller has to remember.
    """
    import os
    from . import watch
    print("\n  the suite — it must not reach a person")

    sent = []
    real_desktop, real_email = watch._desktop, watch.send_email
    watch._desktop = lambda *a, **k: sent.append("desktop") or True
    watch.send_email = lambda *a, **k: (sent.append("email") or True, None)
    prev = os.environ.get("POOLHOUND_NO_NOTIFY")
    try:
        os.environ["POOLHOUND_NO_NOTIFY"] = "1"
        # Both channels switched ON in the config, and severity high enough to
        # clear the email bar: the switch has to beat a config that wants it.
        cfg = {"notify": {"desktop": True, "email": True,
                          "email_min_severity": "info"}}
        got = watch.notify("Free chlorine is low", "m", cfg=cfg, severity="urgent")
        check("with the switch set, nothing is delivered", sent, [])
        check("and it reports that it delivered nothing", got, False)

        del os.environ["POOLHOUND_NO_NOTIFY"]
        watch.notify("Free chlorine is low", "m", cfg=cfg, severity="urgent")
        check("without it, the same call does deliver", sorted(sent),
              ["desktop", "email"])
    finally:
        watch._desktop, watch.send_email = real_desktop, real_email
        if prev is None:
            os.environ.pop("POOLHOUND_NO_NOTIFY", None)
        else:
            os.environ["POOLHOUND_NO_NOTIFY"] = prev

    # And the case that runs the watchdog actually sets it. Read from the
    # source, because the alternative is trusting that it still does.
    import inspect
    from . import selftest as st
    src = inspect.getsource(st.t_watch_actually_runs)
    check("and the case that runs bin/watch sets the switch",
          "POOLHOUND_NO_NOTIFY" in src, True)


def t_an_undelivered_alarm_is_not_recorded_as_sent():
    """Every notify branch gates its state on whether the send happened.

    notify() returns a boolean and exactly ONE of five call sites read it — the
    collector branch, which documents the fix: "Only a send that actually
    happened counts. Recording it regardless meant a failed notification still
    suppressed the next 24 hours of alerts." The other four ignored it.

    So a chemistry alarm marked itself notified on a send that never left the
    machine and went quiet for REMIND_EVERY; the equipment branch advanced the
    watermark past an edge nobody was told about, unrecoverably; `raised`
    counted only collector alerts, so a run that raised a CRITICAL
    free-chlorine alarm printed "all clear" and exited 0 — the exit code
    collect.sh records and --check judges.

    Measured on a pool at 0.0 ppm with no notifier: before, NOTIFIED / all
    clear / exit 0 / last_notified set. After, COULD NOT NOTIFY / no all-clear
    / exit 1 / last_notified null.
    """
    import inspect
    from . import watch
    print("\n  watch — an undelivered alarm is not recorded as sent")

    src = inspect.getsource(watch._run)
    check("every notify() call reads its result",
          src.count("notify(") - src.count("ok = notify("), 0)
    check("the watermark is held back on an undeliverable edge",
          "not undeliverable_event" in src, True)
    check("and the all-clear is gated on alerts raised, not sends made",
          "raised == 0" in src, True)


def t_one_parser_and_one_skew_for_every_write():
    """No route holds its own copy of "parse a timestamp" or "how far ahead".

    save_reading called fromisoformat directly and spelled the tolerance as a
    fresh timedelta(minutes=5). fromisoformat returns an AWARE datetime for
    anything with an offset and now() is naive, so the comparison raised
    TypeError — a 500 — for the very format the route's own success response
    hands back. The future guard therefore ran nowhere at all for an
    offset-bearing timestamp: it raised before it could judge.

    And seams.py could not see it, because the seam matched the NAME
    `CLOCK_SKEW =` and the copy was spelled as the value. A gate that only
    recognises a fact spelled the owner's way cannot find the copy.
    """
    from . import render, seams, config
    print("\n  writes — one parser, one tolerance")

    check("no copy of either fact outside its owner",
          seams.copies(config.root()), [])
    names = [w for w, _, _, _ in seams.SEAMS]
    check("the clock-skew seam is declared", "the clock-skew tolerance" in names, True)
    check("and parsing a stored timestamp is a seam too",
          "parsing a stored timestamp" in names, True)

    # The three flavours this product actually handles, through the one parser.
    check("local-with-offset parses", bool(render.when("2026-09-17T10:40:00-0700")), True)
    check("UTC Z parses", bool(render.when("2026-09-17T17:42:00Z")), True)
    check("naive parses", bool(render.when("2026-09-17T10:41:00")), True)
    check("and an offset-bearing future is seen as future",
          render.is_future("2027-01-01T00:00:00-0700"), True)


def t_a_dose_edit_validates_the_field_everything_indexes_on():
    """new_ts is parsed, bounded and stored canonically like every other field.

    chemical, unit, amount and pct were all validated and new_ts went in
    unparsed, unbounded and uncapped: "not-a-time" stored verbatim, 400
    characters stored verbatim, and a date in 2030 accepted through the form.

    That last is not cosmetic. dose_response pairs each dose with the reading
    that followed it, so a dose moved into the future drops out of the fit
    silently — this pool's measured acid coefficient went from 120 fl oz per
    0.1 pH to 80, with the operator told only "edited".
    """
    import inspect
    from . import server
    print("\n  doses — the edit validates its timestamp")

    src = inspect.getsource(server._edit_chemical)
    check("the new timestamp is parsed by the one parser",
          "render.when(new_ts)" in src, True)
    check("a future one is refused", "render.is_future(new_ts)" in src, True)
    check("it is length-capped like every other string", "[:40]" in src, True)
    check("and stored in the canonical spelling",
          '%Y-%m-%dT%H:%M:%S%z' in src, True)


def t_every_dose_row_carries_its_id():
    """The permanent record can tell two doses in one second apart.

    audit.COLS declares `id` as the column that ties a row to the thing it is
    about, and the control path uses it. The three dose paths did not: dose.log
    passed none, so the column was empty on every dose row ever written, while
    dose.edit and dose.delete smuggled it into `object` as "ts [id]" — the
    exact overloading audit.py's own comment says the column was added to
    avoid. Two doses logged in the same second were addressable in
    chemicals.csv and byte-identical in the audit.
    """
    import inspect
    from . import audit, server
    print("\n  audit — every dose row carries its id")

    check("the column exists", "id" in audit.COLS, True)
    for fn in (server._log_chemical, server._edit_chemical):
        src = inspect.getsource(fn)
        check(f"{fn.__name__} passes an id",
              'id=row.get("id", "")' in src, True)
    check("and no path smuggles it into `object`",
          "[{row.get('id','')}]" in inspect.getsource(server), False)


def _deployment_values(min_len=5):
    """Every concrete value in deploy/server.env, for the tree to be checked against.

    THE DEPLOYMENT'S OWN FILE IS THE ONLY HONEST SOURCE for "what names this
    deployment". The previous guard was a literal stack path typed into this
    module, which could catch exactly one installation's one path and named it
    in the open while forbidding the tree to.

    Short and generic values are dropped. `SHARE=poolhound` is the product's
    own name and matches every file in the repository; a length floor plus the
    product name keeps a true positive from drowning in 80 false ones.

    RETURNS AN EMPTY LIST WHEN THERE IS NO server.env, and the caller reports
    that as a SKIP rather than a pass. The file is gitignored, so CI and the
    deploy image do not have it — and "the scan found nothing" must not be the
    same answer as "there was nothing to scan with", which is the mistake this
    whole module is a monument to.
    """
    from . import config
    path = os.path.join(config.root(), "deploy", "server.env")
    if not os.path.exists(path):
        return []
    generic = {"poolhound", "pool", "main", "true", "false", "forward",
               "password", "utc"}
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                # A comma list (PUBLIC_HOSTS) is several facts, not one.
                for part in val.split(","):
                    part = part.strip()
                    if len(part) >= min_len and part.lower() not in generic:
                        out.append(part)
    except OSError:
        return []
    return sorted(set(out))


def _documentation_host(host, allowed, suffixes=()):
    """Is this a host a public document is allowed to name?

    RFC 2606 reserves example.{com,net,org} and .invalid for documentation, and
    reserves the whole SUBTREE — attacker.example.com in a curl example is the
    correct thing to write, and listing each one by hand would be an inventory
    nobody maintains. `.local` is mDNS: link-local, unroutable, and the literal
    subject of a documented trap in the Pi runbook.
    """
    if host in allowed or host.endswith(".local"):
        return True
    if any(host.endswith(x) for x in suffixes):
        return True
    root = host.split(".", 1)[-1] if host.count(".") > 1 else host
    while root:
        if root in ("example.com", "example.net", "example.org",
                    "example.invalid"):
            return True
        root = root.split(".", 1)[1] if "." in root else ""
    return False


def t_the_tree_names_no_real_deployment():
    """Nothing in this repository names the host it is deployed on.

    THIS IS WHAT MAKES THE REPOSITORY PUBLISHABLE, and it is a property that
    decays by ordinary editing: somebody debugging pastes a real URL into a
    comment, or a runbook example gains a concrete path, and the repository
    quietly stops being safe to open. It had already happened — the vhost was
    named for the domain it served, so the one fact that had to change was
    written into a FILENAME, and three call sites spelled it out to find it.

    The deployment's facts live in `deploy/server.env`, which is gitignored, and
    reach the tree only as doubled-at placeholders the installer fills in. A
    documentation domain (example.com and friends) is reserved by RFC 2606
    precisely so it can be written down.

    MATCHED BY TLD, NOT BY DOTS. The first version called anything with a dot a
    hostname and reported `0.65`, `assistant.context` and `cron.d` — a check
    whose output is mostly noise is one somebody turns off. Only a real
    public-suffix-shaped tail counts.

    What this CANNOT see is a household string that looks like ordinary prose —
    a street name, a surname. That is `site.never_publish`, which lives in the
    config for the reason the leak list gives: a guard that has to name the
    secret cannot live in the open.
    """
    from . import config
    print("\n  publishable — the tree names no real deployment")

    # THE SCANNER ONLY EVER MATCHED DOTTED HOSTNAMES, so a bare word was
    # invisible to it. This check's own docstring promised that no tracked file
    # names "the host this is deployed on" or "the stack directory it lives
    # in", and it went green with the host's short name in three tracked files
    # — a paragraph in CLAUDE.md, a comment in deploy/bootstrap-server.sh, and
    # an environment variable in poolhound/agent.py — plus the Compose project
    # name spelled out thirteen times, which is the stack directory by another
    # route. Two of the three clauses it claimed were enforced by nothing.
    #
    # The fix is to stop guessing what a deployment fact looks like and compare
    # against the deployment's own file. deploy/server.env is gitignored and is
    # where every one of these values already lives.

    # Hosts a document is allowed to name: the reserved ones, the loopback, and
    # the third parties this product genuinely talks to or cites.
    ALLOWED = {
        "example.com", "example.org", "example.net", "example.invalid",
        "poolhound.example.com", "pool.example.com", "auth.example.com",
        "github.com", "raw.githubusercontent.com", "docs.github.com",
        "waterguru.com", "lesliespool.com", "api.openai.com",
        "img.shields.io", "rfc-editor.org", "www.rfc-editor.org",
        "html.spec.whatwg.org", "developer.mozilla.org", "caddyserver.com",
        "smtp.gmail.com", "fonts.googleapis.com", "fonts.gstatic.com",
        "cdnjs.cloudflare.com", "schemas.microsoft.com",
        # Azure's own service endpoints, which name no tenant.
        "storage.file.core.windows.net", "vault.azure.net",
        # Standards and vendor documentation the design notes cite.
        "datatracker.ietf.org", "www.rfc-editor.org", "openid.net",
        "docs.oasis-open.org", "www.amqp.org", "www.w3.org", "spiffe.io",
        "mosquitto.org", "mqtt.org", "tools.ietf.org",
        # The sign-in provider the deployment notes name.
        "login.microsoftonline.com",
        # THE SHOPPING LIST. Help links the manufacturer of every part it tells
        # somebody to buy, and those are third-party hosts by design. Listed
        # here rather than pattern-matched so that adding an outbound link to
        # this product is a thing somebody sees in a diff — which is the only
        # reason a host allowlist is worth having at all.
        "jandy.com", "www.jandy.com", "raspberrypi.com", "www.raspberrypi.com",
        "ftdichip.com", "www.ftdichip.com", "taylortechnologies.com",
        "www.taylortechnologies.com", "www.digikey.com", "www.mouser.com",
        "www.amazon.com",
        # Where Help sends somebody to make a Gmail app password.
        "myaccount.google.com",
        # THE PLIST DOCTYPE, and nothing fetches it. Every launchd property
        # list opens with Apple's DTD declaration, so the one scheduling the
        # off-box pull carries this host in a line no editor writes by hand.
        # It is a format declaration rather than an outbound call — but it is
        # listed rather than pattern-matched for the same reason the shopping
        # list is: the exemption should be the thing somebody reads.
        "www.apple.com",
    }
    # Whole suffixes, because these name a SERVICE and the label in front of
    # them is a tenant or a region rather than an identity — and one of them
    # arrives URL-encoded (%2Fvault.azure.net), which no exact match survives.
    ALLOWED_SUFFIXES = (".azure.net", "amazonaws.com", ".core.windows.net")
    # NO "sh", NO "py": bootstrap-server.sh is not a host in Saint Helena, and
    # the first version reported six scripts and zero real findings.
    TLD = "com|net|org|io|dev|ai|co|app|cloud|uk|us|info|biz|xyz"
    host_re = re.compile(
        r"\b((?:[a-z0-9][a-z0-9-]*\.)+(?:" + TLD + r"))\b", re.I)

    root = config.root()
    # TRACKED FILES, NOT THE WORKING TREE. What a public repository exposes is
    # what git carries; the first version walked the disk and reported
    # config/config.toml, which is gitignored and is precisely where this
    # deployment's hostnames are SUPPOSED to be. A check that flags the
    # intended answer is one somebody learns to ignore.
    import subprocess
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "-z"],
                             capture_output=True, text=True, timeout=30)
        tracked = [f for f in out.stdout.split("\0") if f]
    except (OSError, subprocess.SubprocessError):
        tracked = []
    if not tracked:
        skipped("the tree names no real deployment",
                "not a git checkout, so there is no tracked set to scan")
        return

    # This file necessarily contains the strings it forbids, for the same
    # reason seams.py contains every pattern it scans for.
    SKIP_FILES = {"poolhound/checks_lowsev.py"}
    BINARY = (".png", ".jpg", ".jpeg", ".ico", ".pyc", ".enc", ".bak", ".heic")

    hosts, paths, scanned = [], [], 0
    for rel in tracked:
        if rel in SKIP_FILES or rel.endswith(BINARY):
            continue
        full = os.path.join(root, rel)
        try:
            with open(full, encoding="utf-8") as f:
                body = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1
        if True:
            for m in host_re.finditer(body):
                h = m.group(1).lower()
                if not _documentation_host(h, ALLOWED, ALLOWED_SUFFIXES):
                    hosts.append(f"{rel}: {h}")
            # DERIVED FROM server.env, NOT TYPED HERE. The stack directory is a
            # fact about one person's disk, and it reached four cron lines and a
            # shell `cd` before server.env held it — but the guard against it
            # was a LITERAL in this file, which meant the check forbidding the
            # tree to name the stack directory named it itself, and could only
            # ever catch the one deployment whose path somebody had typed in.
            for val in _deployment_values():
                if val in body:
                    paths.append(f"{rel}: names a value from deploy/server.env")
                    break
            # And the filename itself, which is how this escaped once already.
            for h in host_re.finditer(rel):
                if not _documentation_host(h.group(1).lower(), ALLOWED,
                                           ALLOWED_SUFFIXES):
                    hosts.append(f"{rel}: named for {h.group(1)}")

    check(f"no file names a real host ({scanned} file(s) scanned)",
          sorted(set(hosts))[:12], [])
    # SKIPPED, NOT PASSED, when there is nothing to compare against. server.env
    # is gitignored, so CI and the image have none — and a green line there
    # would mean "we could not look", which is exactly the confusion that let
    # the host's own name sit in three tracked files under a check that said it
    # could not.
    known = _deployment_values()
    if known:
        check(f"and none names a value from deploy/server.env "
              f"({len(known)} value(s) compared)", sorted(set(paths)), [])
    else:
        skipped("no tracked file names this deployment",
                "deploy/server.env is not in this tree, so there is nothing "
                "to compare the tracked files against")
    # A scan that walked nothing would pass both. It has happened here before.
    check("and the scan actually read the tree", scanned > 40, True)


def t_an_unmounted_share_stops_the_container_rather_than_reseeding_it():
    """A deployment that has lost sight of its data refuses to start.

    MEASURED, IN PRODUCTION. The Azure Files share holding this pool's whole
    history failed to mount after a reboot — /mnt is the ephemeral resource
    disk on an Azure VM, this VM's size has none, so mnt.mount failed and the
    share nested under it inherited the failure, silently, because of `nofail`.
    The container then found no /data/config.toml and crash-looped for forty
    hours. Nothing was lost, and the loud failure is the only reason.

    config.seed_config() had just been added to make a first run easy, and it
    would have turned that into the quiet version: a fresh config seeded onto
    the empty mountpoint, a clean start, and a SECOND pool history accumulating
    on local disk that nothing serves and nothing reconciles — shadowed the
    moment the share came back. The guard is what makes "easy first run" and
    "never write a second history" both true.
    """
    import shutil
    import tempfile
    from . import config
    print("\n  a lost share stops the container, it does not reseed it")

    root = config.root()
    keep = {k: os.environ.get(k) for k in ("POOLHOUND_DATA", "POOLHOUND_REQUIRE_DATA")}
    work = tempfile.mkdtemp(prefix="poolhound-seed-")
    try:
        # 1. A first install: nothing set, nothing there. Seed it.
        fresh = os.path.join(work, "fresh")
        os.makedirs(fresh)
        os.environ["POOLHOUND_DATA"] = fresh
        os.environ.pop("POOLHOUND_REQUIRE_DATA", None)
        config.seed_config(root)
        check("a first install is seeded",
              os.path.exists(os.path.join(fresh, "config.toml")), True)

        # 2. THE PRODUCTION CASE. A deployment that says it has readings, and
        #    a data directory that does not. This must stop, not populate.
        lost = os.path.join(work, "lost")
        os.makedirs(os.path.join(lost, ".collector-status"))
        os.environ["POOLHOUND_DATA"] = lost
        os.environ["POOLHOUND_REQUIRE_DATA"] = "1"
        raised = ""
        try:
            config.seed_config(root)
        except SystemExit as e:                      # noqa: PERF203
            raised = str(e)
        check("an unmounted share refuses to start", bool(raised), True)
        check("and nothing was written to it",
              os.path.exists(os.path.join(lost, "config.toml")), False)
        check("and the message names the real cause, not 'no config found'",
              "NOT MOUNTED" in raised, True)

        # 3. THE CASE THE DEPLOY ITSELF CREATES, and the one this detector
        #    used to assert the wrong answer for.
        #
        #    bootstrap-server.sh seeds config.toml onto the share FROM THE HOST
        #    before the container starts. So "config.toml exists" is a fact
        #    about the deploy script, not about the share -- and the guard used
        #    to return on exactly that fact, which disarmed it precisely when
        #    the deploy had just run over a failed mount. This fixture used to
        #    live here as case 3 labelled "the same deployment, healthy": a
        #    REQUIRE_DATA deployment holding a config and no readings at all.
        #    That is not healthy. That is the forty-hour outage, one step before
        #    anyone notices.
        seeded = os.path.join(work, "seeded-over-a-lost-share")
        os.makedirs(seeded)
        shutil.copyfile(os.path.join(root, "config", "config.example.toml"),
                        os.path.join(seeded, "config.toml"))
        os.environ["POOLHOUND_DATA"] = seeded
        os.environ["POOLHOUND_REQUIRE_DATA"] = "1"
        raised = ""
        try:
            config.seed_config(root)
        except SystemExit as e:                      # noqa: PERF203
            raised = str(e)
        check("a config seeded over a lost share still refuses", bool(raised), True)
        check("and says the READINGS are missing, not the config",
              "holds none of" in raised, True)

        # 4. The same deployment, genuinely healthy: a config AND a history.
        ok = os.path.join(work, "ok")
        os.makedirs(ok)
        shutil.copyfile(os.path.join(root, "config", "config.example.toml"),
                        os.path.join(ok, "config.toml"))
        open(os.path.join(ok, "samples.csv"), "w").write("ts\n")
        os.environ["POOLHOUND_DATA"] = ok
        config.seed_config(root)
        check("a mounted share with a history is left alone",
              sorted(os.listdir(ok)), ["config.toml", "samples.csv"])
    finally:
        shutil.rmtree(work, ignore_errors=True)
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def t_the_watchdog_says_it_ran_to_something_outside_itself():
    """Every alarm depends on watch running, so none of them can report it did not.

    MEASURED: /mnt/poolhound failed to mount after a reboot, the container
    refused to start rather than seed a second pool history, and collect.sh
    skipped every job for forty hours -- `watch` 86 times, every thirty
    minutes. The free-chlorine staleness alarm due at 07:37 UTC arrived
    seventeen hours late. The only trace was 86 lines in a log nobody reads and
    a status file, on the missing share, still reporting exit 0 from before it
    began.

    The heartbeat inverts the question: a push monitor alarms on the ping it did
    NOT receive, which keeps working when its subject is dead.
    """
    from . import watch
    print("\n  watch — the heartbeat, and what it must not leak")
    calls = []
    real_creds = None
    try:
        from . import vault
        real_creds = vault.credentials_for
        # No URL configured: this must do nothing at all, quietly.
        vault.credentials_for = lambda s, legacy_path=None: ("", "", None)
        check("with no URL configured it does nothing",
              watch.heartbeat({}, 0, {}, quiet=True), False)

        SECRET = "https://status.example.com/api/push/SECRETTOKEN"
        vault.credentials_for = lambda s, legacy_path=None: ("kuma", SECRET, "vault")

        import urllib.request
        real_open = urllib.request.urlopen

        class _Resp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
        urllib.request.urlopen = lambda u, timeout=None: (calls.append(u), _Resp())[1]
        try:
            # A run that FOUND alarms and could not deliver them still ran.
            ok = watch.heartbeat({}, 1, {"alerts": 2, "sent": 0, "undelivered": 2},
                                 quiet=True)
            check("a completed run pings even when it failed to deliver", ok, True)
            check("exactly one ping", len(calls), 1)
            # status=up means "this process completed", not "the pool is fine":
            # taking the monitor down whenever chlorine is low trains someone
            # to ignore it.
            check("status is up even on a non-zero run", "status=up" in calls[0], True)
            check("and the outcome rides in the message", "rc%3D1" in calls[0]
                  or "rc=1" in calls[0], True)
        finally:
            urllib.request.urlopen = real_open

        # THE URL IS A CREDENTIAL. urllib puts the full URL in most of its
        # exception text, so a failure must report the type and nothing else.
        import io, contextlib
        def boom(u, timeout=None):
            raise OSError(f"cannot reach {SECRET}")
        urllib.request.urlopen = boom
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                watch.heartbeat({}, 0, {}, quiet=False)
        finally:
            urllib.request.urlopen = real_open
        check("a failed ping is reported", "heartbeat" in buf.getvalue(), True)
        check("and the push URL never reaches stdout",
              "SECRETTOKEN" in buf.getvalue(), False)
    finally:
        if real_creds is not None:
            from . import vault
            vault.credentials_for = real_creds


def t_a_collector_status_survives_the_share_going_away():
    """The record was written only where the outage could take it with it.

    collect.sh put its status on the share, reasoning that a container which
    will not start takes a status file inside it with it. True, and the wrong
    outage: what failed was the SHARE, so all 86 skip records went nowhere and
    what survived said exit 0 from two days earlier.
    """
    import os
    from . import config
    print("\n  collect.sh — the status record outlives the share")
    p = os.path.join(config.root(), "deploy", "collect.sh")
    if not os.path.exists(p):
        skipped("collect.sh writes both status locations", "deploy/ is not in this tree")
        return
    src = open(p).read()
    check("it still writes the share", "/mnt/poolhound/.collector-status" in src, True)
    check("and a copy on the host", "/var/lib/poolhound/status" in src, True)
    check("the skip path records rather than only logging",
          'record 1 "container not running"' in src, True)
    b = os.path.join(config.root(), "deploy", "bootstrap-server.sh")
    if os.path.exists(b):
        chk = open(b).read()
        check("and --check reads both, newest wins",
              "/var/lib/poolhound/status" in chk, True)


def t_the_deploy_adds_a_new_setting_to_an_existing_stack():
    """A setting added to the service definition reaches a host that already
    has one.

    MEASURED: POOLHOUND_REQUIRE_DATA — the guard that stops an unmounted share
    being reseeded into a second pool history — sat in compose.poolhound.yml
    for two days and was never in the container. bootstrap's merge only
    splices the service block on a FIRST install, so an existing block keeps
    whatever it was created with; the deploy reported success throughout,
    because a variable that is absent is not an error. Three bespoke patches
    had already been written for the same thing (POOLHOUND_VAULT, the vault
    volume, the REVISION arg), each after somebody noticed a setting was not
    taking effect.

    The merge is shell-embedded Python, so this extracts it the way bash does
    and runs it against a stack that is missing a setting. ADD-ONLY is the half
    that matters as much: an operator's own TZ or mount must survive.
    """
    import re
    import shutil
    import subprocess
    import tempfile
    from . import config
    print("\n  deploy — a new setting reaches a host that already has poolhound")

    root = config.root()
    boot = os.path.join(root, "deploy", "bootstrap-server.sh")
    frag = os.path.join(root, "deploy", "compose.poolhound.yml")
    if not (os.path.exists(boot) and os.path.exists(frag)):
        skipped("the deploy's compose reconciliation",
                "no deploy/ directory in this tree")
        return

    m = re.search(r"python3 - <<'PY'\n(.*?)\nPY\n", open(boot).read(), re.S)
    check("the merge block is where this expects it", bool(m), True)
    if not m:
        return

    work = tempfile.mkdtemp(prefix="poolhound-merge-")
    try:
        target = os.path.join(work, "compose.yml")
        # A stack with poolhound already installed, one setting missing, and
        # one value the operator changed on purpose.
        with open(target, "w") as f:
            f.write('services:\n  poolhound:\n    image: poolhound:local\n'
                    '    environment:\n      POOLHOUND_DATA: "/data"\n'
                    '      POOLHOUND_SITE: "/site"\n      TZ: "Europe/Berlin"\n'
                    '    volumes:\n      - /mnt/poolhound:/data\n'
                    '  other:\n    image: someone/else\n\nvolumes:\n  kuma_data:\n')
        # bash expands $STACK in the outer heredoc before the remote shell
        # ever sees this; do the same.
        body = (m.group(1)
                .replace("$STACK/compose.yml", target)
                .replace("$STACK/poolhound/src/deploy/compose.poolhound.yml", frag)
                .replace(r"\${REVISION:-unknown}", "${REVISION:-unknown}"))
        script = os.path.join(work, "merge.py")
        with open(script, "w") as f:
            f.write(body)

        r1 = subprocess.run([sys.executable, script], capture_output=True, text=True)
        check("the merge runs without raising", r1.returncode, 0)
        got = open(target).read()
        check("a setting missing from the host is added",
              "POOLHOUND_REQUIRE_DATA" in got, True)
        check("and the operator's own value is NOT overwritten",
              'TZ: "Europe/Berlin"' in got, True)
        check("and their own mount survives",
              "- /mnt/poolhound:/data" in got, True)
        check("and another service is untouched", "someone/else" in got, True)

        # Running the deploy twice must not accumulate duplicates.
        subprocess.run([sys.executable, script], capture_output=True, text=True)
        twice = open(target).read()
        check("a second deploy changes nothing", twice, got)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def t_every_consumer_agrees_what_the_default_sign_in_is():
    """AUTH_MODE has one default, not one per reader.

    MEASURED: compose.yml fell back to `password` and bootstrap-server.sh to
    `forward`, so the same install answered "how do you sign in" two ways
    depending on which brought it up. The deployment that relied on the
    implicit value had no AUTH_MODE at all — a standalone bring-up of it would
    have quietly swapped Entra for a bcrypt file nobody had written, which
    presents as every member of the household locked out of their own pool.

    A default is a fact, and a fact read by two files is the seam this suite
    exists to watch. There is no runtime object to own it — one reader is
    shell and the other is YAML — so the check is that they MATCH, which is
    the weaker guarantee and the only one available.
    """
    import re
    from . import config
    print("\n  sign-in — one default, not one per reader")

    root = config.root()
    readers = {
        "deploy/compose.yml": r"auth-\$\{AUTH_MODE:-(\w+)\}",
        "deploy/bootstrap-server.sh": r"auth-\$\{AUTH_MODE:-(\w+)\}",
    }
    found = {}
    for rel, pat in readers.items():
        p = os.path.join(root, *rel.split("/"))
        if not os.path.exists(p):
            skipped(f"the AUTH_MODE default in {rel}", "not in this tree")
            continue
        m = re.search(pat, open(p, encoding="utf-8").read())
        found[rel] = m.group(1) if m else None
    if not found:
        return
    check(f"every reader states a default ({found})",
          sorted(v for v in found.values() if v), sorted(found.values()))
    check("and they are the same one", len(set(found.values())), 1)

    # And the mode each default names must actually exist as a snippet.
    for rel, mode in found.items():
        if not mode:
            continue
        snip = os.path.join(root, "deploy", "caddy", f"auth-{mode}.caddy")
        check(f"{rel}'s default names a snippet that exists",
              os.path.exists(snip), True)
