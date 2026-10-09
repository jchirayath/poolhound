"""The three structural gates, and the one that checks the others.

These exist because of what one week of review actually found. Roughly a third
of every defect was one fact in two places that had drifted; a fifth was a list
that claimed to be complete and was not; and a sixth was a CHECK that passed
while measuring nothing — which is the one that let the other two survive
unnoticed for as long as they did.
"""

import os

from .selftest import FAILURES, SKIPPED, check, skipped
from . import config, inventory, seams


def t_no_fact_is_held_in_two_places():
    """A seam's fact appears in the module that owns it, and nowhere else.

    The design of this project is a set of sentences — "chemicals.py is the
    only place the dose arithmetic lives", "style.SERIES is the single source"
    — and a sentence cannot detect a second copy. Nine had already appeared:
    two palette colours redrawn in the favicon, the panel's no-flow sentinel
    retyped in the agent, the console identity written as a literal by two
    writers, and three credential paths typed beside the module that owns them.
    """
    print("\n  seams — one fact, one owner")
    found = seams.copies(config.root())
    check(f"no fact is held outside its owning module ({len(found)} found)",
          found, [])
    check("and the table names the seams it claims to",
          len(seams.SEAMS) >= 6, True)


def t_every_inventory_matches_what_exists():
    """A list that claims to be complete is compared with what it claims.

    Ten defects were a hand-kept inventory that had stopped being complete —
    bin/contrast passing three times over a colour it had never measured,
    ROLE_UI naming no control for three gated routes, do_GET's routes never
    listed at all. Both directions: a declaration naming something that no
    longer exists is an entry nobody re-reads, sitting where the next reader
    will trust it.
    """
    print("\n  inventories — the list against the thing")
    for name, missing in inventory.absent():
        skipped(f"inventory: {name}", f"{missing} is not in this tree")
    g = inventory.gaps()
    check(f"every registered inventory matches reality ({len(g)} gap(s))", g, [])
    check("and inventories are actually registered",
          len(inventory.names()) >= 3, True)
    # AND SOMETHING WAS ACTUALLY COMPARED. Every inventory declaring a `needs`
    # that this tree lacks would skip them all and leave the two assertions
    # above passing over an empty comparison, which is the shape of gate this
    # file exists to refuse.
    answered = len(inventory.names()) - len(inventory.absent())
    check(f"and some of them were answerable here ({answered})",
          answered >= 3, True)


# What must make each detector FAIL. The whole point of this file.
#
# A check that cannot be shown to fail has not been shown to check anything,
# and this project has shipped at least nine that could not: selftest and
# contrast with no __main__ (python -m imported them, ran nothing, exited 0);
# a sentinel regex that required whoami() adjacent to the comparison and so
# matched none of the five call sites it was written for; a Caddy check that
# matched literals against a config storing wildcards; ROLE_UI selectors that
# matched no element; a corruption guard sitting below an early return that is
# taken on every ordinary day.
#
# Each entry is (detector, a callable that breaks the world, what it proves).
# The meta-check applies the break, runs the detector, and asserts it noticed.
def _break_a_seam():
    original = list(seams.SEAMS)
    # A pattern that certainly occurs in real code, owned by a module that does
    # not exist — so every module holding it is reported as a copy. The first
    # version of this used a pattern that happened to occur nowhere, so the
    # proof passed by proving nothing, which is precisely the failure this case
    # exists to catch. It caught itself.
    seams.SEAMS.append(("a fact nobody owns", "nosuchmodule.py",
                        r"def [a-z_]+\(", {}))
    return lambda: seams.SEAMS.__setitem__(slice(None), original)


def _break_an_inventory():
    from . import access
    stolen = dict(access.ROLE_UI)
    access.ROLE_UI.pop(next(iter(access.ROLE_UI)))
    return lambda: (access.ROLE_UI.clear(), access.ROLE_UI.update(stolen))


def _break_the_seam_gate_for(name):
    """Remove one seam so the detector that reads seams.copies() goes quiet.

    Used by the low-severity cases that assert "no copy of this fact exists":
    deleting the seam makes the question unaskable, which is the failure mode
    those cases are really guarding — a fact stops being watched and nobody
    notices, exactly as the clock-skew seam stopped seeing
    `timedelta(minutes=5)`.
    """
    original = list(seams.SEAMS)
    seams.SEAMS[:] = [x for x in seams.SEAMS if x[0] != name]
    return lambda: seams.SEAMS.__setitem__(slice(None), original)


def _break_source_file(rel, needle, replacement):
    """Rewrite one line of a repo file on disk, and put it back.

    For the detectors that read a file rather than a module -- the Dockerfile,
    the Caddyfile, a deploy script. Done on disk because that is what the
    detector reads; restored in a finally by the meta-check.

    NONE WHEN THE FILE IS NOT IN THIS TREE, which is the rule two breakers in
    this file learned the hard way: the image carries no deploy/, so opening
    one unconditionally raised FileNotFoundError inside the image's own build
    gate and failed every build for four days.
    """
    path = os.path.join(config.root(), *rel.split("/"))
    if not os.path.exists(path):
        return None
    original = open(path, encoding="utf-8").read()
    if needle not in original:
        return None
    with open(path, "w", encoding="utf-8") as f:
        f.write(original.replace(needle, replacement, 1))

    def undo():
        with open(path, "w", encoding="utf-8") as f:
            f.write(original)
    return undo


def _break_live_code(rel, module_name, needle, replacement):
    """Rewrite one line of a module and RELOAD it, so the change is in force.

    _break_source_file is for a detector that READS a file -- the Dockerfile,
    the vhost, a deploy script. It is useless against a detector that CALLS a
    function: the module was imported before the rewrite, so the old code
    object is still the one running and the proof reports "did not notice" on
    a breaker that is sitting right there in the file. Eight of them did, on
    the first run of this registry's newest entries.

    importlib.reload mutates the module object in place, so a sibling that did
    `from . import switchlog` sees the new functions through the same binding
    -- and the undo reloads again, which is what puts the real code back for
    every case that runs after this one.

    NONE WHEN THE FILE IS NOT IN THIS TREE, for the reason every breaker here
    carries that clause: the image ships no deploy/ and an unconditional open
    once failed every build for four days.
    """
    import importlib
    path = os.path.join(config.root(), *rel.split("/"))
    if not os.path.exists(path):
        return None
    original = open(path, encoding="utf-8").read()
    if needle not in original:
        return None
    mod = importlib.import_module(module_name)

    # AND THE BYTECODE CACHE HAS TO GO WITH IT.
    #
    # reload() trusts a .pyc whose recorded source mtime and SIZE still match
    # the file. Both writes here happen inside one second, and a breaker that
    # swaps two words of equal length -- "on" for "off" -- changes neither. So
    # the break took effect and THE UNDO DID NOT: the module was left broken
    # for every case that ran after it. Measured, and it is the worst possible
    # direction for this helper to fail in, because the damage lands on
    # unrelated detectors and looks like their bug.
    #
    # The seven breakers that happened to change the file's length worked
    # perfectly, which is how a defect like this stays hidden.
    cache = importlib.util.cache_from_source(path)

    def write(text):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            os.remove(cache)
        except OSError:
            pass
        importlib.invalidate_caches()
        importlib.reload(mod)

    write(original.replace(needle, replacement, 1))
    return lambda: write(original)


def _break_source_claim(module_name, needle, replacement):
    """Rewrite one line in a module's source so a source-reading check fails.

    Several detectors assert on inspect.getsource(). The honest way to break
    one is to change the source it reads — done in memory, on a copy, and put
    back, so nothing is written to disk.
    """
    import importlib, inspect
    mod = importlib.import_module(f"poolhound.{module_name}")
    real = inspect.getsource
    def fake(obj):
        out = real(obj)
        return out.replace(needle, replacement) if needle in out else out
    inspect.getsource = fake
    return lambda: setattr(inspect, "getsource", real)


PROVES = [
    (t_no_fact_is_held_in_two_places, _break_a_seam,
     "a second copy of a fact"),
    (t_every_inventory_matches_what_exists, _break_an_inventory,
     "an inventory that has lost an entry"),
]


def _let_collectors_run_anywhere():
    """Make the guard wave everything through.

    Behaviour, not source: the detector RUNS each collector and reads its exit
    code, so patching inspect.getsource would break nothing -- which the
    meta-check has now said four separate times today.
    """
    from . import config
    real = config.refuse_off_deployment
    config.refuse_off_deployment = lambda tool: None
    return lambda: setattr(config, "refuse_off_deployment", real)


def _ignore_the_notify_switch():
    """Make notify() stop honouring the kill switch.

    Breaks BEHAVIOUR, not source: the detector executes notify() rather than
    reading it, so patching inspect.getsource -- which is the right tool for
    the detectors that read source -- broke nothing and the meta-check said so.
    That is the fourth time today it has caught exactly that mistake.
    """
    from . import watch
    real = watch.notifications_disabled
    watch.notifications_disabled = lambda: False
    return lambda: setattr(watch, "notifications_disabled", real)


def _register_headers():
    """Breakers for the security headers and the per-page policy.

    The first is the edit that reads like hardening and is an outage; the
    second is the allowance the policy used to carry; the third is the CDN
    reference that `default-src 'none'` exists to make impossible.
    """
    from . import assistant as A, checks_assistant as AC
    from . import checks_collection as C, checks_vault as V
    from . import checks_gates as G, headers as H

    def _make_the_vhost_policy_a_value_not_a_default():
        """Drop Caddy's `?`, so the vhost REPLACES the per-page policy.

        Two characters. The baseline it would replace it with says
        `script-src 'none'`, so every served page would be handed a policy
        refusing its own script — and the diff reads like someone tightening
        the vhost.
        """
        return _break_source_file("deploy/poolhound.caddy",
                                  '\t\t?Content-Security-Policy',
                                  '\t\tContent-Security-Policy')

    def _allow_inline_script_again():
        """Put back `script-src 'unsafe-inline'` — the pre-fix policy.

        Not by deleting the detector's subject but by restoring the exact
        string this project served until the digests went in, which is the
        regression worth proving the detector catches.
        """
        real = H.policy
        H.policy = lambda script_digests=(), style_digests=(): (
            "default-src 'none'; img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; form-action 'none'; base-uri 'none'; "
            "frame-ancestors 'none'")

        def undo():
            H.policy = real
        return undo

    def _put_a_cdn_in_the_built_page():
        """One external script tag in the rendered public build.

        The page would simply stop working — default-src 'none' admits no
        origin — and nothing about the markup or the server would say why.
        NONE WHEN THE PAGES ARE NOT HERE: the deploy image carries no rendered
        pages, and a breaker that opens one unconditionally is how a
        FileNotFoundError once failed every image build for four days.
        """
        from . import config
        path = os.path.join(config.site_dir(config.load()), "index.html")
        if not os.path.exists(path):
            return None
        original = open(path, encoding="utf-8").read()
        needle = "<script>"
        if needle not in original:
            return None
        with open(path, "w", encoding="utf-8") as f:
            f.write(original.replace(
                needle,
                '<script src="https://cdn.example.com/x.js"></script><script>',
                1))

        def undo():
            with open(path, "w", encoding="utf-8") as f:
                f.write(original)
        return undo

    def _let_a_restart_hand_the_budget_back():
        """Stop persisting the spend — the pre-fix behaviour exactly.

        Not a caricature: this is what assistant.py did until the state file
        went in, and the defect it produced is a ceiling that `docker compose
        up --force-recreate` removes.
        """
        real = A._save
        A._save = lambda: None

        def undo():
            A._save = real
        return undo

    def _let_the_outgoing_token_live_for_ever():
        """Accept a second slot with no deadline — two live credentials.

        ON DISK, NOT ON THE MODULE, because the detector reloads vault.py
        inside its own isolation and a reload puts a monkeypatch straight
        back. _break_source_file is the tool for a detector that re-reads what
        it is testing.
        """
        return _break_source_file(
            "poolhound/vault.py",
            "    if len(parts) < 2:",
            "    if len(parts) == 1:\n"
            "        return (parts[0], _dt.datetime.max.replace(\n"
            "            tzinfo=_dt.timezone.utc)), None\n"
            "    if len(parts) < 2:")

    def _call_a_second_directory_offbox():
        """Stop asking which filesystem the destination is on.

        The defect wearing the fix's clothes: a second directory beside the
        first reads as configured, passes every check that counts tarballs,
        and dies with the host it exists to outlive.
        """
        bk = C.load_backup()
        if bk is None:
            return None
        real = bk._same_device
        bk._same_device = lambda a, b: False

        def undo():
            bk._same_device = real
        return undo

    PROVES.extend([
        (G.t_the_vhost_sends_the_same_headers_the_server_does,
         _make_the_vhost_policy_a_value_not_a_default,
         "a vhost policy that replaces the per-page one instead of defaulting it"),
        (G.t_the_policy_never_allows_inline_script,
         _allow_inline_script_again,
         "script-src 'unsafe-inline' coming back"),
        (G.t_the_rendered_pages_match_the_policy_they_get,
         _put_a_cdn_in_the_built_page,
         "a third party reaching the page that reads this household's data"),
        (AC.t_the_spending_ceiling_survives_a_restart,
         _let_a_restart_hand_the_budget_back,
         "a spending ceiling a container restart hands back"),
        (V.t_a_setting_cannot_rewrite_another_key, _escape_only_the_quote,
         "a settings field that can write a second config key"),
        (V.t_the_agent_token_can_be_rotated_without_an_outage,
         _let_the_outgoing_token_live_for_ever,
         "an outgoing token with no deadline, which is a second permanent secret"),
        (C.t_the_offbox_copy_refuses_a_destination_that_dies_with_the_host,
         _call_a_second_directory_offbox,
         "a second copy on the same disk, which the VM takes with it"),
    ])


def _name_the_deployment_in_a_tracked_file():
    """Write a value from deploy/server.env back into a tracked file.

    THE CLAUSE THE OLD CHECK COULD NOT ENFORCE. Its scanner matched dotted
    hostnames only, so a bare word was invisible: the host's own short name sat
    in three tracked files — CLAUDE.md, deploy/bootstrap-server.sh and
    poolhound/agent.py — under a green line reading "no tracked file names the
    host this is deployed on". _name_a_real_host above proves the dotted half;
    this proves the half that was decorative.

    NONE WHEN THERE IS NO server.env. The image and CI do not carry it, and a
    breaker that cannot break the tree in front of it reports that rather than
    raising — the rule two breakers in this file learned the expensive way.
    """
    from . import checks_lowsev as L
    vals = L._deployment_values()
    if not vals:
        return None
    return _break_source_file(
        "README.md", "<p align=\"center\">",
        "<p align=\"center\"><!-- %s -->" % vals[0])


def _escape_only_the_quote():
    """Put back the escaping that made a settings field a TOML injection.

    Not a caricature: this is exactly what toml_value() did until the payload
    in the detector's docstring was demonstrated against it.
    """
    from . import server as S
    real = S.toml_value

    def only_the_quote(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int):
            return str(v)
        return '"' + str(v).replace('"', '\\"') + '"'

    S.toml_value = only_the_quote

    def undo():
        S.toml_value = real
    return undo


def _register_lowsev():
    """Breakers for the checks_lowsev detectors, declared rather than exempted.

    The backlog exists so an unproven detector is visible, not so it is
    comfortable. These four were added after the backlog was recorded, so they
    are proven rather than appended to it.
    """
    from . import checks_lowsev as L
    PROVES.extend([
        (L.t_no_collector_runs_off_the_deployment, _let_collectors_run_anywhere,
         "a collector that will run on a workstation again"),
        (L.t_the_suite_cannot_notify_a_person, _ignore_the_notify_switch,
         "the suite being able to raise a real notification again"),
        (L.t_an_undelivered_alarm_is_not_recorded_as_sent,
         lambda: _break_source_claim("watch", "ok = notify(", "notify("),
         "a notify() call whose result nobody reads"),
        (L.t_one_parser_and_one_skew_for_every_write,
         lambda: _break_the_seam_gate_for("the clock-skew tolerance"),
         "the clock-skew seam being removed"),
        (L.t_a_dose_edit_validates_the_field_everything_indexes_on,
         lambda: _break_source_claim("server", "render.is_future(new_ts)", "False"),
         "an edit that stops refusing a future timestamp"),
        (L.t_every_dose_row_carries_its_id,
         lambda: _break_source_claim("server", 'id=row.get("id", "")', "id=''"),
         "a dose row that stops carrying its id"),
        (L.t_the_tree_names_no_real_deployment, _name_a_real_host,
         "a real hostname written back into a tracked file"),
        (L.t_the_tree_names_no_real_deployment,
         _name_the_deployment_in_a_tracked_file,
         "this deployment's own name written back into a tracked file"),
        (L.t_an_unmounted_share_stops_the_container_rather_than_reseeding_it,
         _seed_over_a_lost_share,
         "a lost share being quietly reseeded into a second pool history"),
        (L.t_the_deploy_adds_a_new_setting_to_an_existing_stack,
         _merge_only_on_first_install,
         "a deploy that cannot add a setting to a host that already has one"),
        (L.t_every_consumer_agrees_what_the_default_sign_in_is,
         _disagree_about_the_default_sign_in,
         "two readers falling back to different sign-in mechanisms"),
    ])


def _disagree_about_the_default_sign_in():
    """Put the defaults back out of step, which is how they shipped.

    ON DISK: the detector reads both files, so this rewrites one of them and
    restores it. compose.yml is the one changed because bootstrap-server.sh is
    the file another editor is most likely to be in.
    """
    from . import config
    p = os.path.join(config.root(), "deploy", "compose.yml")
    # Not in the image, the same as the deploy script below. This is the
    # SECOND breaker to open a deploy/ file unconditionally, and the first
    # one's crash is why nobody saw it: the meta-check called breaker()
    # outside its guard, so the run ended at the earlier of the two and this
    # line was never reached. One crash hid the next.
    if not os.path.exists(p):
        return None
    original = open(p, encoding="utf-8").read()
    assert "auth-${AUTH_MODE:-password}" in original
    with open(p, "w", encoding="utf-8") as f:
        f.write(original.replace("auth-${AUTH_MODE:-password}",
                                 "auth-${AUTH_MODE:-forward}", 1))

    def undo():
        with open(p, "w", encoding="utf-8") as f:
            f.write(original)
    return undo


def _merge_only_on_first_install():
    """Neuter the reconciliation, which is the version that shipped.

    ON DISK, in deploy/bootstrap-server.sh. The first attempt at this patched
    inspect.getsource — and the detector does not read source, it EXTRACTS the
    merge block from that file with a regex and runs it as a subprocess, so
    nothing was broken and the meta-check said so. That is the same mistake,
    in the same direction, that three breakers above already carry a note
    about; it is apparently the easiest one in this file to make.

    `if added:` is what writes the reconciled file back. With it never true,
    the merge runs, reports nothing, and leaves a host missing the setting —
    which is precisely the defect that kept POOLHOUND_REQUIRE_DATA out of the
    container for two days.
    """
    from . import config
    p = os.path.join(config.root(), "deploy", "bootstrap-server.sh")
    # NOT IN THIS TREE IS NOT A CRASH, and this cost four days of deploys.
    #
    # The deploy image carries poolhound/, bin/, the example config, the
    # crontab and the landing cards -- no deploy/ directory. This breaker
    # opened deploy/bootstrap-server.sh unconditionally, so `undo = breaker()`
    # raised FileNotFoundError inside the image's build gate, which is
    # `python -m poolhound.selftest`. Every image build after that commit
    # failed at that line: the server stayed on the revision before it for
    # four days while `bootstrap-server.sh --check` reported the pages it was
    # serving as the deployed build, correctly, because they were.
    #
    # Returning None is how a breaker says it cannot break this tree. The
    # detector skips here for the same missing file, so the meta-check reports
    # the proof as unprovable -- which is the third outcome this project
    # insists on everywhere else.
    if not os.path.exists(p):
        return None
    original = open(p, encoding="utf-8").read()
    assert "\n    if added:\n" in original, "the merge no longer has an 'if added:'"
    with open(p, "w", encoding="utf-8") as f:
        f.write(original.replace("\n    if added:\n", "\n    if False:\n", 1))

    def undo():
        with open(p, "w", encoding="utf-8") as f:
            f.write(original)
    return undo


def _drop_facts_from_the_plain_part():
    """Make text() stop rendering the facts table.

    BEHAVIOUR, not source: the detector compares what text() and html() each
    contain, so this has to change what one of them returns. The regression it
    stands for is the realistic one -- somebody adds a row to the HTML table
    and does not add it to the plain part, and every person who tests the
    change reads it in HTML.
    """
    from . import mail
    real = mail.text
    mail.text = lambda a: f"{a.label.upper()}: {a.headline}\n"
    return lambda: setattr(mail, "text", real)


def _rejoin_the_plain_part_the_old_way():
    """Put back the join that never filtered anything.

    `if x is not None` against values that are "" when absent: the exact
    expression that shipped, so the detector is shown against the real defect
    rather than a caricature of it.
    """
    from . import mail
    real = mail.text
    def old(a):
        return "\n".join(x for x in (
            "", "", a.detail, "",
            f"Dashboard: {a.url}" if a.url else "", "Sent by poolhound.")
            if x is not None)
    mail.text = old
    return lambda: setattr(mail, "text", real)


def _stop_setting_the_date_header():
    """A build that no longer sets Date -- the state this arrived in."""
    from . import mail
    real = mail.build
    def without(*a, **k):
        msg = real(*a, **k)
        del msg["Date"]
        return msg
    mail.build = without
    return lambda: setattr(mail, "build", real)


def _stop_escaping_interpolations():
    from . import mail
    real = mail._esc
    mail._esc = lambda s: str(s)
    return lambda: setattr(mail, "_esc", real)


def _corrupt_the_committed_logo():
    """Point the tree at a docs/ whose PNG is not a PNG.

    Nothing is written to the real docs/: config.root() is redirected at a
    temporary tree for the duration. Deleting the file instead would make the
    detector SKIP, and a skip is not a detector firing.
    """
    import shutil, tempfile
    from . import config
    d = tempfile.mkdtemp(prefix="poolhound-brand-")
    os.makedirs(os.path.join(d, "docs"))
    with open(os.path.join(d, "docs", "poolhound-mark-email.png"), "wb") as f:
        f.write(b"this is not a png")
    real = config.root
    config.root = lambda: d
    def restore():
        config.root = real
        shutil.rmtree(d, ignore_errors=True)
    return restore


def _render_the_logo_as_inline_svg():
    """The form that renders as nothing at all in Gmail."""
    from . import mail
    real = mail.html
    mail.html = lambda a: "<html><body><svg width='28'></svg></body></html>"
    return lambda: setattr(mail, "html", real)


def _leak_the_heartbeat_url():
    """Report the exception text, which is where urllib puts the full URL."""
    from . import watch
    real = watch.heartbeat
    def leaky(cfg, rc, tally, quiet=False):
        from . import vault
        import urllib.request
        _w, url, _s = vault.credentials_for("heartbeat")
        if not url:
            return False
        try:
            urllib.request.urlopen(url, timeout=5)
        except Exception as e:
            if not quiet:
                print(f"  heartbeat not delivered: {e}")
            return False
        return True
    watch.heartbeat = leaky
    return lambda: setattr(watch, "heartbeat", real)


def _write_the_status_only_to_the_share():
    """A tree whose collect.sh records to the share alone.

    The arrangement that lost 86 skip records: the share was the thing that
    failed, so every record of the failure went with it. Built in a temporary
    directory -- deploy/collect.sh on disk is never touched.
    """
    import shutil, tempfile
    from . import config
    real = config.root
    d = tempfile.mkdtemp(prefix="poolhound-collect-")
    os.makedirs(os.path.join(d, "deploy"))
    src = os.path.join(real(), "deploy", "collect.sh")
    body = open(src).read() if os.path.exists(src) else "STATUS_DIR=x\n"
    body = body.replace("/var/lib/poolhound/status", "/mnt/poolhound/.collector-status")
    with open(os.path.join(d, "deploy", "collect.sh"), "w") as f:
        f.write(body)
    config.root = lambda: d
    def restore():
        config.root = real
        shutil.rmtree(d, ignore_errors=True)
    return restore


def _register_watchdog():
    """Breakers for the watchdog-durability detectors."""
    from . import checks_lowsev as L
    PROVES.extend([
        (L.t_the_watchdog_says_it_ran_to_something_outside_itself,
         _leak_the_heartbeat_url,
         "a push URL reaching a log through an exception message"),
        (L.t_a_collector_status_survives_the_share_going_away,
         _write_the_status_only_to_the_share,
         "the status record going back to the share alone"),
    ])


def _raise_the_pixel_ceiling():
    """Put the ceiling back above what a bomb produces.

    BEHAVIOUR, not source: the detector builds a real PNG and calls
    prepare_photo, so patching inspect.getsource breaks nothing.
    """
    from . import pool_shape
    real = pool_shape.MAX_PIXELS
    pool_shape.MAX_PIXELS = 500_000_000
    return lambda: setattr(pool_shape, "MAX_PIXELS", real)


def _register_photo_bomb():
    from . import checks_photo as P
    PROVES.append(
        (P.t_a_decompression_bomb_is_refused_before_it_is_decoded,
         _raise_the_pixel_ceiling,
         "a pixel ceiling that no longer refuses a decompression bomb"))


def _trust_the_header_again():
    """Make whoami() read the identity header without proving the proxy.

    BEHAVIOUR, not source: the detector imports server with the environment
    set and calls whoami(), so patching inspect.getsource breaks nothing. This
    reinstates the version that shipped — the strip list in Caddy, and trust in
    docker network membership for everything that does not traverse it.
    """
    import importlib
    import sys as _sys
    for m in [m for m in _sys.modules if m.startswith("poolhound.server")]:
        del _sys.modules[m]
    S = importlib.import_module("poolhound.server")
    real = S.Handler.whoami

    def leaky(self):
        for h in S.IDENTITY_HEADERS:
            v = (self.headers.get(h) or "").strip()[:120]
            if v:
                return v
        return self._peer_identity()
    S.Handler.whoami = leaky
    return lambda: setattr(S.Handler, "whoami", real)


def _register_identity():
    from . import checks_gates as G
    PROVES.append(
        (G.t_identity_is_not_believed_on_network_position_alone,
         _trust_the_header_again,
         "identity believed on docker network position alone again"))


def _let_the_backup_land_on_the_share():
    """Remove the destination guard, which is the defect wearing a fix's clothes.

    BEHAVIOUR, not source: the detector CALLS run() with a destination inside
    the data directory and expects SystemExit, so patching getsource is inert.
    """
    from . import checks_collection as C
    bk = C.load_backup()
    if bk is None:
        return lambda: None
    real = bk.run

    def unguarded(cfg=None, out=None):
        import os as _os
        import tarfile as _t
        dest = out or bk.backup_dir(cfg or {})
        _os.makedirs(dest, exist_ok=True)
        path = _os.path.join(dest, "poolhound-unguarded.tgz")
        with _t.open(path, "w:gz"):
            pass
        return {"path": path, "bytes": 0, "copied": [], "missing": [], "pruned": []}
    bk.run = unguarded
    return lambda: setattr(bk, "run", real)


def _let_the_run_log_stringify_anything():
    """Put back the `str(by or "")[:40]` that wrote a config dict to runs.csv.

    BEHAVIOUR, not source: the detector CALLS record() and reads the row back,
    so patching inspect.getsource is inert. record() reaches _safe_by through
    the module global, which is why the guard is a named function and not four
    inline characters — an inline guard has nowhere for a breaker to stand.

    This only reinstates half the defect; the other half is below.
    """
    from . import runlog
    real = runlog._safe_by
    runlog._safe_by = lambda by: str(by or "")[:40]
    return lambda: setattr(runlog, "_safe_by", real)


def _let_run_take_by_positionally():
    """Make `Run("backup", cfg)` a working call again, as it was when it shipped.

    The keyword-only marker is the control that turns the original slip into a
    TypeError at the call site. A breaker that only addressed _safe_by would
    leave this half of the detector green no matter what, which is the
    "passed while measuring nothing" failure this gate exists for.
    """
    from . import runlog
    real = runlog.Run.__init__

    def positional(self, tool, by="cron", cfg=None):
        return real(self, tool, by=by, cfg=cfg)
    runlog.Run.__init__ = positional
    return lambda: setattr(runlog.Run, "__init__", real)


def _register_backup():
    from . import checks_collection as C
    PROVES.extend([
        (C.t_the_backup_lands_somewhere_that_outlives_the_share,
         _let_the_backup_land_on_the_share,
         "a backup that will happily write inside the share it is backing up"),
        (C.t_the_run_log_records_a_name_and_never_an_object,
         _let_the_run_log_stringify_anything,
         "a run-log column that str()s whatever object it is handed"),
        (C.t_the_run_log_records_a_name_and_never_an_object,
         _let_run_take_by_positionally,
         "a config dict landing in `by` through a positional call again"),
    ])


def _register_mail():
    """Breakers for the alert-email detectors."""
    from . import checks_mail as M
    def _hide_a_runtime_asset():
        """Point one declared asset at a file that is not there.

        The image's own gate is `python -m poolhound.selftest`, so this is the
        check that must go red when a runtime asset is missing -- and the
        first version of this defect went unnoticed precisely because the only
        thing that noticed reported it as a skip.
        """
        from . import config
        original = dict(config.RUNTIME_ASSETS)
        config.RUNTIME_ASSETS["docs/a-file-that-is-not-there.png"] = (
            "nothing; this exists to prove the check fires")
        def undo():
            config.RUNTIME_ASSETS.clear()
            config.RUNTIME_ASSETS.update(original)
        return undo

    def _stop_copying_the_logo():
        """Remove the COPY line, which is the Dockerfile as it actually
        shipped: cards copied, the email mark not."""
        return _break_source_file("deploy/Dockerfile",
                                  "COPY docs/poolhound-mark-email.png", "# ")

    PROVES.extend([
        (M.t_every_runtime_asset_is_actually_present, _hide_a_runtime_asset,
         "a file the product reads at run time being absent"),
        (M.t_the_image_carries_every_runtime_asset, _stop_copying_the_logo,
         "a runtime asset the Dockerfile does not copy into the image"),
        (M.t_absent_parts_leave_no_hole, _rejoin_the_plain_part_the_old_way,
         "the join that leaves a blank line where nothing was"),
        (M.t_headers_a_relay_will_not_add_for_us, _stop_setting_the_date_header,
         "automated mail going out with no Date"),
        (M.t_html_escapes_what_it_interpolates, _stop_escaping_interpolations,
         "vendor text reaching the markup unescaped"),
        (M.t_the_logo_is_a_real_png_of_the_right_size, _corrupt_the_committed_logo,
         "a committed logo that is not a usable PNG"),
        (M.t_the_multipart_is_the_shape_clients_expect,
         _render_the_logo_as_inline_svg,
         "a logo in a form Gmail renders as nothing"),
        (M.t_both_renderings_carry_every_fact, _drop_facts_from_the_plain_part,
         "a reading that reaches the HTML part and not the plain one"),
        (M.t_the_email_owns_no_palette,
         lambda: _break_source_claim("mail", "def tok(name):",
                                     'def tok(name, fallback="#000000"):'),
         "a colour literal written back into the email template"),
        (M.t_one_copy_of_the_test_message,
         lambda: _break_source_claim(
             "watch", "ok, err = send_email(cfg, mail.test_alert(cfg))",
             'ok, err = send_email(cfg, "This is poolhound checking that it '
             'can reach you")'),
         "a second copy of the test message growing back in watch.py"),
    ])


def _seed_over_a_lost_share():
    """Make seed_config ignore the flag, which is the version that shipped.

    BEHAVIOUR, NOT SOURCE: the detector CALLS seed_config and looks at what
    landed on disk, so patching inspect.getsource breaks nothing — the mistake
    the meta-check has caught more times than any other in this file.
    """
    from . import config
    real = config.seed_config

    def unguarded(root=None):
        keep = os.environ.pop("POOLHOUND_REQUIRE_DATA", None)
        try:
            return real(root)
        finally:
            if keep is not None:
                os.environ["POOLHOUND_REQUIRE_DATA"] = keep

    config.seed_config = unguarded
    return lambda: setattr(config, "seed_config", real)


def _name_a_real_host():
    """Put a deployment's own hostname into a tracked file, on disk.

    ON DISK, because the detector reads the tracked set with `git ls-files`
    and then opens each file — patching inspect.getsource reaches none of
    that, which the meta-check has said about four other breakers today. The
    file is restored in the undo, and it is a file the suite already writes to
    in other cases rather than a new one.
    """
    from . import config
    p = os.path.join(config.root(), "config", "config.example.toml")
    original = open(p, encoding="utf-8").read()
    # ASSEMBLED, NOT WRITTEN OUT. Spelled as one literal it is a real hostname
    # sitting in a tracked file, so the detector found it here and failed the
    # ordinary run — the breaker broke the world permanently rather than for
    # the length of the proof. The alternative was exempting this whole file
    # from the scan, which is a worse trade: breakers are exactly where a real
    # host would get pasted.
    fake = "poolhound." + "somebodyshouse" + ".net"
    with open(p, "w", encoding="utf-8") as f:
        f.write(original + f'\n# host = "{fake}"\n')

    def undo():
        with open(p, "w", encoding="utf-8") as f:
            f.write(original)
    return undo




def _register_collection():
    """Breakers for the Collection tab's detectors."""
    from . import checks_collection as C, collection, render, runlog

    # THESE BREAK BEHAVIOUR, NOT SOURCE. The first attempt at these four
    # patched inspect.getsource, which is the right tool for a detector that
    # READS source and useless against one that exercises the thing. All four
    # reported "did not fire" and were correct to: the world had not been
    # broken in any way they could see.
    def _stop_recording():
        real = runlog.record
        runlog.record = lambda *a, **k: None
        # Run() calls record() by module attribute, so this reaches both.
        return lambda: setattr(runlog, "record", real)

    def _lose_the_crontab():
        """Make the crontab PRESENT and unreadable, not absent.

        The first version pointed CRON_FILE at a file that does not exist —
        and once the detector learned to say "not checkable here" for a
        stripped build, that became a graceful skip rather than a failure, so
        the breaker stopped breaking anything. The meta-check caught it.

        A schedule that parses to nothing while the file is right there is the
        real defect: the page would show no cadence and nobody would know why.
        """
        real = collection.schedule
        collection.schedule = lambda: {}
        return lambda: setattr(collection, "schedule", real)

    def _summarise_the_oldest():
        real = collection.summary
        def oldest(runs):
            return real(list(reversed(list(runs))))
        collection.summary = oldest
        return lambda: setattr(collection, "summary", real)

    def _poll_the_panel_again():
        """Model the controller as something that runs here, which is the
        defect exactly as it shipped: two cards, both permanently 'never run'."""
        real = collection.TOOLS.copy()
        collection.TOOLS["sample"] = ("Pool controller",
                                      "A reading straight off the panel.")
        def undo():
            collection.TOOLS.clear()
            collection.TOOLS.update(real)
        return undo

    def _make_the_tab_public():
        real_priv, real_pub = render.PRIVATE_TABS, render.PUBLIC_TABS
        render.PRIVATE_TABS = tuple(t for t in real_priv if t != "collection")
        render.PUBLIC_TABS = real_pub + ("collection",)
        def undo():
            render.PRIVATE_TABS = real_priv
            render.PUBLIC_TABS = real_pub
        return undo

    def _weaken_the_clock_pattern():
        """Put the first draft of the clock-time pattern back.

        NOT by removing the seam, which is the easy break and proves only that
        the check reads seams.SEAMS. The first draft required a collector NAME
        on the same line as the time, and MEASURED against the eleven real
        lines it finds seven: it misses both SVG arrow labels, which are the
        two a reader's eye lands on first, the line explaining why the pull is
        timed where it is, and the compact drawing's summary -- because each of
        those is a time and nothing else. A detector that finds most of a
        defect is exactly the shape of gate this file exists to refuse, so that
        is what it must be shown to catch.
        """
        original = list(seams.SEAMS)
        # (?m) and the ^ lookahead, so the name counts wherever on the line it
        # sits. Without them it finds three rather than seven: a lookahead at
        # the match start cannot see a name that PRECEDES the time, which is
        # how all four of the Settings rows were written.
        weak = (r"""(?m)^(?=.*(?:wg-collect|leslies|WaterGuru|Leslie))"""
                r""".*?["'][^"'\n\[\]{}]*(?<![\d:T+-])"""
                r"""(?:[01]?[0-9]|2[0-3]):[0-5][0-9](?![\d:])""")
        seams.SEAMS[:] = [
            (w, o, weak if w == "the collectors' clock times" else pat, e)
            for w, o, pat, e in seams.SEAMS]
        return lambda: seams.SEAMS.__setitem__(slice(None), original)

    def _spend_the_budget_every_hour():
        """Let the floor go, which is the version a dropdown would ship.

        THE FLOOR IS THE ONLY THING between an hourly crontab tick and a vendor
        who asks for two calls a day. The cadence moved off the crontab so it
        could be a setting, and the moment it did, "how often" became a number
        a person types -- so the number has to be refused on the READ path,
        where the call is actually spent, and not only in the form.

        Dropping it to one hour is what the product would do if somebody added
        "every hour" to CHOICES and nothing downstream disagreed: 24 calls a
        day against a budget of 2, silently, on a schedule.
        """
        from . import cadence
        original = dict(cadence.SOURCES["wg-collect"])
        cadence.SOURCES["wg-collect"]["floor_hours"] = 1
        def undo():
            cadence.SOURCES["wg-collect"].clear()
            cadence.SOURCES["wg-collect"].update(original)
        return undo

    def _let_the_floor_drop_a_change():
        """Make the rate floor DISCARD instead of hold, which is the version
        that looks right and loses data.

        A floor exists so a flapping thermostat cannot write hundreds of rows.
        The obvious implementation returns False and forgets -- and then the
        change it was tidying up is gone, which is strictly worse than the
        noise. This is the same shape as the dose plausibility bound that was
        written in one route and not the other: the guard works, and quietly
        takes a real value with it.
        """
        from . import agent
        real = agent.ChangeWatch.saw
        def dropping(self, fingerprint, now):
            if (self._last_push is not None
                    and now - self._last_push < self.min_gap):
                self.known = fingerprint        # forgotten, not held
                self._pending = None
                return False
            return real(self, fingerprint, now)
        agent.ChangeWatch.saw = dropping
        return lambda: setattr(agent.ChangeWatch, "saw", real)

    def _trust_the_door_claim():
        """Record whatever the claim says, which is the obvious implementation.

        It arrives from an unauthenticated page on the house LAN. Trusting it
        puts an unverified identity into an append-only audit log -- a record
        that looks authoritative and is not, which is strictly worse than
        having none. The allowlist and the `door:` prefix are the only things
        standing between "which door" and "who", so this removes them.
        """
        from . import commands as CMD
        real = CMD.door_label
        CMD.door_label = lambda raw: (str(raw).strip() or None)
        return lambda: setattr(CMD, "door_label", real)

    def _name_who_ran_it():
        """Show poolhound local who sent the command, which is one word away.

        `cmd["by"]` is right there and reads like the helpful thing to put in
        an activity list. It is a signed-in identity, and this list is served
        to the whole house LAN with no sign-in at all.
        """
        from . import agent
        real = agent.record_command

        def leaky(activity, cmd, result):
            activity.add(C_.describe(cmd), f"poolhound app ({cmd.get('by')})",
                         ok=bool(result.get("ok")), device=cmd.get("device"))
        from . import commands as C_
        agent.record_command = leaky
        return lambda: setattr(agent, "record_command", real)

    PROVES.extend([
        (C.t_the_pool_pages_activity_says_which_way_never_who, _name_who_ran_it,
         "a signed-in identity shown on an unauthenticated LAN page"),
        (C.t_a_door_may_claim_a_change_but_never_an_identity, _trust_the_door_claim,
         "an unverified identity written into the audit log"),
        (C.t_a_panel_change_is_pushed_once_it_has_settled,
         _let_the_floor_drop_a_change,
         "a rate floor that discards a real change instead of holding it"),
        (C.t_a_tick_that_is_not_due_spends_nothing, _spend_the_budget_every_hour,
         "a cadence floor that would let an hourly tick spend the day's budget"),
        (C.t_no_page_restates_the_collectors_clock, _weaken_the_clock_pattern,
         "a clock-time detector that finds only seven of the eleven"),
        (C.t_every_attempt_is_recorded_whichever_way_it_ends, _stop_recording,
         "an attempt that is not written down"),
        (C.t_the_schedule_is_read_not_restated, _lose_the_crontab,
         "the crontab no longer being read"),
        (C.t_the_summary_agrees_with_the_rows_it_summarises, _summarise_the_oldest,
         "a summary that takes the OLDEST attempt per tool"),
        (C.t_a_source_that_arrives_is_not_judged_on_whether_it_ran,
         _poll_the_panel_again,
         "the controller modelled as something we poll"),
        (C.t_the_collection_tab_is_private, _make_the_tab_public,
         "the tab losing its private classification"),
        (C.t_the_control_banners_do_not_contradict_each_other,
         _bake_the_promise_back,
         "the render claiming a command will be delivered again"),
        (C.t_an_unattributed_dose_says_which_kind_it_is,
         lambda: _collapse_unattributed(),
         "both kinds of blank name rendering the same again"),
    ])


def _bake_the_promise_back():
    """Make control_panel() emit the delivery promise again.

    Patching inspect.getsource was the wrong tool once the detector started
    reading the RENDERED page rather than the source — the breaker stopped
    breaking and the meta-check said so, which is the third time today it has
    caught exactly that.
    """
    from . import panels
    real = panels.control_panel
    panels.control_panel = lambda *a, **k: (
        real(*a, **k) + "<div>a command will still be delivered</div>")
    return lambda: setattr(panels, "control_panel", real)


def _collapse_unattributed():
    """Put the ambiguous dash back, which is the defect."""
    from . import panels
    real = panels._unattributed
    panels._unattributed = lambda row: "&ndash;"
    return lambda: setattr(panels, "_unattributed", real)

def _doctored_site(edit):
    """Serve the checks a COPY of the rendered pages with `edit` applied.

    Three of the chrome detectors read index.html and admin.html off disk,
    because the pages this build actually writes are the only honest subject
    for "what does the public page say about the pool". Breaking those means
    breaking a page, and the pages are real files somebody is serving -- so the
    pair is copied to a temporary directory, damaged there, and site_dir() is
    pointed at it for the length of the case.

    POOLHOUND_SITE is set as well as the function patched: config.site_dir()
    consults the environment FIRST, so patching the function alone leaves any
    caller that reads the variable looking at the real directory.
    """
    import os, shutil, tempfile
    from . import config
    real_dir = config.site_dir()
    tmp = tempfile.mkdtemp(prefix="poolhound-doctored-")
    for name in ("index.html", "admin.html"):
        src = os.path.join(real_dir, name)
        if os.path.exists(src):
            with open(os.path.join(tmp, name), "w") as f:
                f.write(edit(name, open(src).read()))
    real_fn, real_env = config.site_dir, os.environ.get("POOLHOUND_SITE")
    config.site_dir = lambda cfg=None: tmp
    os.environ["POOLHOUND_SITE"] = tmp

    def undo():
        config.site_dir = real_fn
        if real_env is None:
            os.environ.pop("POOLHOUND_SITE", None)
        else:
            os.environ["POOLHOUND_SITE"] = real_env
        shutil.rmtree(tmp, ignore_errors=True)
    return undo


def _drop_the_figures_id_prefix():
    """Put the same drawing on the page twice under one set of ids.

    Behaviour, not source: the detector reads the RENDERED page, so this has to
    change what gets rendered. It makes the prefix a no-op, which is exactly
    what adding the figure to a second panel without one would have done.
    """
    from . import help as helpdoc
    real = helpdoc._arch_simple
    helpdoc._arch_simple = lambda idp="": real()
    return lambda: setattr(helpdoc, "_arch_simple", real)


def _register_catalogue():
    """Breakers for the branded chemicals and the un-predicted dose."""
    from . import checks_catalogue as B, chemicals as C

    def _mis_scale_the_stabiliser():
        """The 35-ppm-per-gallon figure a third-party summary gave, which does
        not agree with the same label's 4-oz-per-ppm."""
        real = C.CHEMICALS["cya_liquid"]["effects"]
        C.CHEMICALS["cya_liquid"]["effects"] = (
            lambda floz, pct, V, ta=90.0: {"cya": floz * 35.0 / (128.0 * V)})
        def undo():
            C.CHEMICALS["cya_liquid"]["effects"] = real
        return undo

    def _let_the_blank_speak_for_itself():
        """Drop the reason, so an unpredicted dose renders as no effect."""
        real = C.CHEMICALS["nophos"].get("no_effect")
        C.CHEMICALS["nophos"]["no_effect"] = ""
        def undo():
            C.CHEMICALS["nophos"]["no_effect"] = real
        return undo

    def _invent_a_phosphate_coefficient():
        """Predict a ppb drop the label does not state — the exact thing this
        entry exists to refuse."""
        real = C.CHEMICALS["nophos"]["effects"]
        C.CHEMICALS["nophos"]["effects"] = (
            lambda floz, pct, V, ta=90.0: {"phosphates": -floz * 125.0 / V})
        def undo():
            C.CHEMICALS["nophos"]["effects"] = real
        return undo

    def _let_one_shelf_name_serve_two():
        """Give "Alkalinity Up" to soda ash as well, which is the mistake this
        guards and the one that cannot be allowed to happen quietly.

        The two chemicals move different measures by different amounts. A
        picker offering the same shelf name for both hands somebody the wrong
        coefficient with full confidence -- the same shape as the stabiliser
        scaled to a figure its own label contradicted.
        """
        from . import chemicals as CH
        original = dict(CH.SOLD_AS)
        CH.SOLD_AS["soda_ash"] = CH.SOLD_AS["soda_ash"] + ("Alkalinity Up",)
        def undo():
            CH.SOLD_AS.clear()
            CH.SOLD_AS.update(original)
        return undo

    def _take_away_the_shelf_names():
        """Leave the catalogue naming only its chemistry, which is how it
        shipped and why "there is no option to add Alkalinity Up" was a
        reasonable conclusion to reach."""
        from . import chemicals as CH
        original = dict(CH.SOLD_AS)
        CH.SOLD_AS.clear()
        def undo():
            CH.SOLD_AS.clear()
            CH.SOLD_AS.update(original)
        return undo

    PROVES.extend([
        (B.t_every_chemical_says_what_it_is_sold_as, _let_one_shelf_name_serve_two,
         "one shelf name standing for two different chemicals"),
        (B.t_every_chemical_says_what_it_is_sold_as, _take_away_the_shelf_names,
         "a catalogue that names only its chemistry"),
        (B.t_the_liquid_stabiliser_matches_its_own_label, _mis_scale_the_stabiliser,
         "a stabiliser scaled to a figure its own label contradicts"),
        (B.t_a_dose_with_no_prediction_says_so, _let_the_blank_speak_for_itself,
         "an unpredicted dose rendering as no effect at all"),
        (B.t_every_chemical_can_be_inverted_and_priced,
         _invent_a_phosphate_coefficient,
         "a phosphate coefficient invented from nowhere"),
    ])


def _register_assistant():
    """Breakers for the assistant: what it sends, and who may ask."""
    from . import checks_assistant as A, assistant, access, render

    def _swap(obj, name, value):
        real = getattr(obj, name)
        setattr(obj, name, value)
        return lambda: setattr(obj, name, real)

    def _put_the_house_in_the_context():
        """Append the config wholesale, which is what "send a summary of the
        pool" becomes the first time somebody reaches for the easy version.

        THE WHOLE CONFIG, not the [site] table. Leaking only [site] made this
        proof depend on the install having one: against the shipped example
        config, where every site fact is commented out, it appended "SITE: {}"
        — a leak of nothing — and the proof reported the detector as failing
        to notice. It was the deploy image's config that exposed it, which is
        the environment the build gate actually runs in.
        """
        real = assistant.context
        def leaky(*a, **k):
            return real(*a, **k) + "\nCONFIG: " + repr(render.CFG)
        assistant.context = leaky
        return lambda: setattr(assistant, "context", real)

    def _ask_without_checking_anything():
        return _swap(assistant, "ask",
                     lambda q, cfg=None, timeout=None: ("sure", None))

    def _open_the_route_to_readers():
        real = dict(access.NEEDS)
        access.NEEDS["/api/ask"] = "view"
        def undo():
            access.NEEDS.clear()
            access.NEEDS.update(real)
        return undo

    def _hard_code_the_provider():
        return _swap(assistant, "DEFAULTS",
                     {k: v for k, v in assistant.DEFAULTS.items()
                      if k != "base_url"})

    def _one_sentence_for_every_status():
        """Put the message that shipped back: one line for all of them.

        "the provider answered N. Check the key and the model name." It was
        shown for a 429 caused by an exhausted credit balance, where the key
        and the model were both correct and neither could have produced that
        code. A detector that passes against this string is a detector that
        would have let the real defect through.
        """
        return _swap(assistant, "http_error",
                     lambda status, code=None:
                     f"the provider answered {status}. "
                     f"Check the key and the model name.")

    def _echo_the_providers_message():
        """Read error.message, which is the body rule this guards.

        The provider can quote the request back, and the request is this
        pool's volume, readings and doses. The shape allowlist is what stops
        it; this removes the allowlist.
        """
        import json as _json
        def leaky(e):
            try:
                err = (_json.loads(e.read().decode("utf-8", "replace"))
                       .get("error") or {})
            except Exception:                            # noqa: BLE001
                return None
            return err.get("code") or err.get("type") or err.get("message")
        return _swap(assistant, "_provider_code", leaky)

    PROVES.extend([
        (A.t_a_provider_error_names_what_the_reader_can_change,
         _one_sentence_for_every_status,
         "one sentence of advice for every provider status"),
        (A.t_a_provider_error_never_carries_the_body,
         _echo_the_providers_message,
         "the provider's message, and the request in it, reaching the page"),
        (A.t_the_context_carries_pool_chemistry_and_nothing_else,
         _put_the_house_in_the_context,
         "the house getting into what is sent to a provider"),
        (A.t_asking_refuses_before_it_spends_anything, _ask_without_checking_anything,
         "an assistant that spends without checking anything first"),
        (A.t_the_tab_is_private_and_the_route_is_gated, _open_the_route_to_readers,
         "the route opened to anybody who can read the private tabs"),
        (A.t_the_provider_is_configured_not_compiled_in, _hard_code_the_provider,
         "the endpoint stopping being a setting"),
    ])


def _register_charts():
    """Breakers for the trend charts' sources and time window."""
    from . import checks_charts as G, render

    def _swap(obj, name, value):
        real = getattr(obj, name)
        setattr(obj, name, value)
        return lambda: setattr(obj, name, real)

    def _drop_the_extra_sources():
        """Back to one secondary series, which is where by-hand went missing."""
        real = render.line_chart
        render.line_chart = lambda *a, **k: real(
            *a, **{kk: vv for kk, vv in k.items() if kk != "extra"})
        return lambda: setattr(render, "line_chart", real)

    def _offer_one_window():
        return _swap(render, "CHART_RANGES", [("all", "All", None)])

    def _let_an_empty_window_claim_there_is_nothing():
        """Stop recognising the chart's own empty output, so a window with no
        readings tells the reader their pool has never been measured."""
        real = render.ranged_chart
        def passthrough(build, default="all"):
            import datetime as _dt
            now = _dt.datetime.now()
            out = []
            for key, _t, delta in render.CHART_RANGES:
                svg = build(now - delta if delta else None) or ""
                out.append(f'<div class="rv" data-range="{key}"'
                           f'{"" if key == default else " hidden"}>{svg}</div>')
            return '<div class="ranged">' + "".join(out) + "</div>"
        render.ranged_chart = passthrough
        return lambda: setattr(render, "ranged_chart", real)

    def _give_every_chart_its_own_bar():
        """Put the window control back inside each chart, which let somebody
        put chlorine on a week and pH on a year — two charts drawn to be read
        against a shared x axis, showing different spans of time."""
        real = render.ranged_chart
        render.ranged_chart = lambda build, default="all": (
            render.range_bar(default) + real(build, default))
        return lambda: setattr(render, "ranged_chart", real)

    PROVES.extend([
        (G.t_the_control_is_one_per_section_not_one_per_chart,
         _give_every_chart_its_own_bar,
         "a window control per chart, so the stacked pair can disagree"),
        (G.t_every_source_is_drawn_with_its_own_mark, _drop_the_extra_sources,
         "a chart that draws only one secondary source"),
        (G.t_the_window_offers_every_span_and_shows_one,
         _let_an_empty_window_claim_there_is_nothing,
         "an empty window claiming the pool has never been measured"),
        (G.t_no_window_is_offered_that_the_declaration_does_not_name,
         _offer_one_window,
         "the declared spans reduced behind the control's back"),
    ])


def _register_photo():
    """Breakers for the photo tracer's camera metadata."""
    from . import checks_photo as P, pool_shape as ps

    def _swap(obj, name, value):
        real = getattr(obj, name)
        setattr(obj, name, value)
        return lambda: setattr(obj, name, real)

    def _stop_reading_the_maker_note():
        """Back to where production actually was: the basics, and no tilt."""
        return _swap(ps, "apple_acceleration", lambda mn: None)

    def _trust_the_maker_note():
        """Return a vector for anything at all, which is the failure that
        matters here — a wrong tilt skews every distance off the photograph,
        silently, where a missing one asks the reader for it."""
        return _swap(ps, "apple_acceleration", lambda mn: (0.0, -0.5, -0.86))

    def _read_the_roll_in_the_handsets_frame():
        """Ignore the orientation, which is what this did before.

        Every orientation resolves to the portrait frame again, so a landscape
        photograph comes back ninety degrees out.
        """
        real = ps.tilt_from_gravity
        ps.tilt_from_gravity = lambda gx, gy, gz, orientation=6: real(
            gx, gy, gz, orientation=6)
        return lambda: setattr(ps, "tilt_from_gravity", real)

    def _read_metadata_off_the_conversion():
        """Make the upload itself unreadable, so only a derivative is left.

        BOTH readers, not just one. The first version blanked the Pillow path
        alone and the case still passed, because this machine has exiftool and
        it answered for the original anyway — the breaker was breaking half a
        fallback chain. Production has neither exiftool nor an original that
        carries anything through the conversion, which is the state this
        reproduces.
        """
        real_p, real_e = ps._exif_via_pillow, ps._exif_via_exiftool
        ps._exif_via_pillow = lambda path: ({} if path.endswith("in.bin")
                                            else real_p(path))
        ps._exif_via_exiftool = lambda path: ({} if path.endswith("in.bin")
                                              else real_e(path))

        def undo():
            ps._exif_via_pillow = real_p
            ps._exif_via_exiftool = real_e
        return undo

    def _lose_the_size_again():
        """Reinstate `getexif() or {}`, which cost a photograph without EXIF
        its own width."""
        real = ps._exif_via_pillow
        ps._exif_via_pillow = lambda path: (real(path) if _has_exif(path) else {})
        return lambda: setattr(ps, "_exif_via_pillow", real)

    def _has_exif(path):
        try:
            from PIL import Image
            with Image.open(path) as im:
                return bool(im.getexif())
        except Exception:
            return False

    PROVES.extend([
        (P.t_the_camera_tilt_is_read_without_a_host_binary,
         _stop_reading_the_maker_note,
         "a server that cannot read the camera tilt"),
        (P.t_a_hostile_maker_note_is_refused_not_guessed, _trust_the_maker_note,
         "a parser that returns a tilt for bytes it did not understand"),
        (P.t_the_camera_fields_are_read_from_the_original,
         _read_metadata_off_the_conversion,
         "the camera fields read off a copy that carries none"),
        (P.t_the_roll_is_measured_in_the_pictures_axes,
         _read_the_roll_in_the_handsets_frame,
         "the roll read in the handset's frame instead of the picture's"),
        (P.t_a_landscape_photo_resolves_to_a_ground_plane,
         _read_the_roll_in_the_handsets_frame,
         "a landscape photo whose ground plane will not solve"),
        (P.t_a_photo_with_no_maker_note_degrades_rather_than_breaks,
         _lose_the_size_again,
         "a photograph without metadata losing its own width"),
    ])


def _register_chrome():
    """Breakers for the page chrome: the masthead, the tab bar, the stylesheet.

    Every one of these puts back a defect that was actually in this working
    tree an hour ago, which is the only kind of breaker worth having: a
    detector proves it fires against the thing it was written for.
    """
    from . import checks_chrome as K, checks_a11y as A, render, info, brand

    def _swap(obj, name, value):
        real = getattr(obj, name)
        setattr(obj, name, value)
        return lambda: setattr(obj, name, real)

    def _rename_a_tab_behind_the_documents_back():
        """Change the live tuple and leave the prose saying what it used to.

        This is how documentation actually goes wrong: nobody edits the
        document, somebody edits the code, and the two drift with nothing
        breaking. README carried "seven tabs" for a while after there were
        nine, and the review harness told reviewers to check all seven.
        """
        return _swap(render, "PUBLIC_TABS",
                     tuple("renamed" if t == "volume" else t
                           for t in render.PUBLIC_TABS))

    def _stop_writing_the_default():
        return _swap(render, "set_default_tab", lambda page, tab: page)

    def _stop_ordering():
        return _swap(render, "order_tabs", lambda page, order: page)

    def _lose_the_divider():
        # The group then starts at the first button, so no rule is drawn --
        # right for the public build and wrong for the authenticated one.
        return _swap(render, "TAB_GROUP_START", "home")

    def _put_the_literal_fallback_back():
        return _swap(render, "TEMPLATE", render.TEMPLATE.replace(
            "show(location.hash.slice(1) || FIRST, false);",
            "show(location.hash.slice(1) || 'home', false);"))

    def _unclassify_the_landing_page():
        return _swap(render, "PUBLIC_TABS",
                     tuple(t for t in render.PUBLIC_TABS if t != "info"))

    def _publish_the_verdict():
        return _doctored_site(lambda name, page: (
            page.replace("</header>", '<span class="grade ok">OK</span></header>')
            if name == "index.html" else page))

    def _wear_an_undefined_class():
        return _doctored_site(lambda name, page: page.replace(
            '<main id="main">', '<main id="main"><p class="nosuchrule">x</p>', 1))

    def _unscope_the_diagram_rules():
        return _doctored_site(lambda name, page: page.replace("svg .lbl{", ".lbl{"))

    def _describe_only_the_chemistry():
        """The defect, exactly: the landing page keeps one purpose of three.

        This is what the page shipped with, and what the masthead shipped with
        before that -- the same cut made twice, a week apart, because the three
        were prose rather than a list.
        """
        return _swap(info, "PURPOSE", [info.PURPOSE[1]])

    def _name_a_figure_that_is_not_there():
        return _swap(info, "FIGURES", list(info.FIGURES) + [
            ("no-such-card.png", "Ghost", None, "a" * 50, "a caption")])

    def _redraw_the_favicon_by_hand():
        return _swap(render, "_favicon_href",
                     lambda: "data:image/svg+xml," + brand.favicon_svg())

    def _drop_the_privacy_link():
        """The footer losing a link to one of the three statements -- here the
        one an anonymous visitor most needs to reach before signing in."""
        from . import policies
        real = policies.footer_links
        return _swap(policies, "footer_links", lambda: real().rsplit(" · ", 1)[0])

    PROVES.extend([
        (A.t_no_two_elements_answer_to_the_same_id, _drop_the_figures_id_prefix,
         "one drawing shown twice under a single set of ids"),
        (K.t_the_site_document_states_the_tabs_the_code_has,
         _rename_a_tab_behind_the_documents_back,
         "a document still printing a tab list the code has moved on from"),
        (K.t_the_default_tab_is_written_not_inherited, _stop_writing_the_default,
         "a build that takes the markup's word for which tab opens"),
        (K.t_the_nav_order_is_data_and_disagreement_stops_the_build, _stop_ordering,
         "a nav laid out in whatever order the template happened to hold"),
        (K.t_the_group_divider_follows_the_order_it_divides, _lose_the_divider,
         "the divider between the two groups going missing"),
        (K.t_the_script_takes_its_default_from_the_page,
         _put_the_literal_fallback_back,
         "the tab router naming a tab as a literal again"),
        (K.t_the_two_builds_open_where_they_were_told_to,
         _unclassify_the_landing_page,
         "the landing page losing its public classification"),
        (K.t_the_public_masthead_does_not_report_on_this_pool,
         _publish_the_verdict,
         "this pool's verdict back on the anonymous page"),
        (K.t_the_favicon_is_one_attribute, _redraw_the_favicon_by_hand,
         "a favicon URL whose quotes end the attribute early"),
        (K.t_the_policy_links_land_on_their_sections, _drop_the_privacy_link,
         "a footer that no longer links to the privacy statement"),
        (K.t_a_class_the_page_wears_has_a_rule, _wear_an_undefined_class,
         "a component asking for styling that does not exist"),
        (K.t_a_drawing_does_not_style_the_page_around_it,
         _unscope_the_diagram_rules,
         "a diagram's class rules applying to the whole document"),
        (K.t_the_page_and_the_masthead_describe_the_same_product,
         _describe_only_the_chemistry,
         "a landing page that names one purpose of three"),
        (K.t_every_figure_the_landing_page_names_exists,
         _name_a_figure_that_is_not_there,
         "a caption describing a picture that is not there"),
    ])


_register_collection()
def _let_a_restart_clear_the_interlocks():
    """Stop persisting the Guard, which is how the agent shipped.

    BEHAVIOUR, not source: the detector builds a second Guard over the same
    path and asks what it refuses, so patching inspect.getsource is inert.
    This is the exact state the product was in — STATE_DIR created at startup
    and never written to — so the breaker is the defect, not a caricature of
    it.
    """
    from . import agent
    real = agent.Guard._save
    agent.Guard._save = lambda self: None
    return lambda: setattr(agent.Guard, "_save", real)


def _make_unreadable_state_fatal():
    """Let a corrupt state file propagate, taking the sampler down with it.

    The agent is what reads the panel. Protecting a cooldown by refusing to
    start trades the pool's only sampler for it, which is the wrong way round.
    """
    from . import agent
    real = agent.Guard._load

    def strict(self):
        import json as _json
        try:
            f = open(self.path, encoding="utf-8")
        except FileNotFoundError:
            return
        with f:
            saved = _json.load(f)                    # raises on a corrupt file
        self.done = saved.get("done") or {}
    agent.Guard._load = strict
    return lambda: setattr(agent.Guard, "_load", real)


def _make_an_unwritable_state_fatal():
    """Let a failed write fail the command that already ran.

    By the time _save() is reached the panel has been told. Reporting that as
    a failed command is a lie about physical state, which is the one thing the
    control path must not do.
    """
    from . import agent
    real = agent.Guard._save

    def strict(self):
        import json as _json
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            _json.dump({"done": self.done, "last": self.last,
                        "recent": self.recent}, f)
    agent.Guard._save = strict
    return lambda: setattr(agent.Guard, "_save", real)


def _put_the_lab_s_words_back_into_innerHTML():
    """Restore the concatenation that shipped.

    SOURCE, because this detector reads render.TEMPLATE — the page script is
    a string, so the string is the thing under test and patching it is the
    honest break. This is the exact line that was there.
    """
    from . import render
    real = render.TEMPLATE
    broken = real.replace(
        "d.appendChild(document.createTextNode(' ' + x.message + ' '));",
        "d.innerHTML = d.innerHTML + ' ' + x.message + ' ';", 1)
    if broken == real:
        return lambda: None
    render.TEMPLATE = broken
    return lambda: setattr(render, "TEMPLATE", real)


def _let_the_server_stop_reporting_vendor_text():
    """Make the message a fixed string, so the threat model check measures
    nothing. If this does not go red, that detector is decoration."""
    return _break_source_claim("server", "out[-1].strip()", '"ran"')


def _stop_counting_a_source_that_never_ran():
    """Put the attention set back to what both copies of it said."""
    from . import collection
    real = collection.NEEDS_ATTENTION
    collection.NEEDS_ATTENTION = ("failed", "late")
    return lambda: setattr(collection, "NEEDS_ATTENTION", real)


def _grade_a_skip_as_working():
    """Return the green pill for a skip, which is what _state did."""
    from . import collection
    real = collection._state

    def lenient(row, sched, did=None):
        state, text = real(row, sched, did)
        return ("ok", text) if state == "skipped" else (state, text)
    collection._state = lenient
    return lambda: setattr(collection, "_state", real)


def _measure_lateness_from_the_last_attempt():
    """Judge freshness on any row, skip or not — the forty-hour blindness."""
    from . import collection
    real = collection._state

    def from_any_attempt(row, sched, did=None):
        return real(row, sched, row if did is None else row)
    collection._state = from_any_attempt
    return lambda: setattr(collection, "_state", real)


def _blank_every_triple_quoted_run_again():
    """Put back the regex that could not tell a docstring from TEMPLATE.

    BEHAVIOUR, not source: the detector CALLS _code_only, so patching
    inspect.getsource is inert. This is verbatim what the function did.
    """
    import re as _re
    from . import seams
    real = seams._code_only

    def blind(text):
        def blank(m):
            return _re.sub(r"[^\n]", " ", m.group(0))
        body = _re.sub(r'"""(?:.|\n)*?"""', blank, text)
        body = _re.sub(r"'''(?:.|\n)*?'''", blank, body)
        out = []
        for line in body.split("\n"):
            out.append("" if line.lstrip().startswith("#")
                       else line.split("  # ")[0])
        return "\n".join(out)
    seams._code_only = blind
    return lambda: setattr(seams, "_code_only", real)


def _put_a_backtick_back_inside_the_heredoc():
    """Reinstate the seventh instance, in the place it actually occurred.

    SOURCE, on disk and restored: the detector reads deploy/*.sh off the
    filesystem, so the file is the thing under test. This is the exact text
    that shipped from 2026-09-26 and printed three "command not found" lines
    on a real deploy ten days later.
    """
    return _break_source_file(
        "deploy/bootstrap-server.sh",
        '"""KEY: value names',
        '"""`KEY: value` names')


def _put_the_startlimit_keys_back_in_service():
    """Exactly where they were, where systemd ignores them."""
    return _break_source_file(
        "deploy/poolhound-agent.service",
        "Restart=always\nRestartSec=10\n",
        "Restart=always\nRestartSec=10\nStartLimitIntervalSec=300\n")


def _order_the_agent_behind_the_target_that_wants_it():
    """The shape that produced the cycle, in our own unit this time.

    aqualinkd's bug was `After=multi-user.target` on a unit that is
    `WantedBy=multi-user.target`. Writing it here is the same mistake and must
    be caught here.
    """
    return _break_source_file(
        "deploy/poolhound-agent.service",
        "After=network-online.target\n",
        "After=network-online.target multi-user.target\n")


def _ignore_the_rate_unit():
    """Treat every rate as per-gallon, which is the defect the unit prevents.

    BEHAVIOUR, not source: the detector CALLS refill_cost. A field labelled
    "rate per gallon" with a bill's per-CCF figure typed into it is wrong by
    748x, and this is that code.
    """
    from . import pool_shape
    real = pool_shape.refill_cost
    pool_shape.refill_cost = lambda gallons, rate, unit="gal": (
        None if not (float(gallons or 0) > 0) else float(gallons) * float(rate or 0))
    return lambda: setattr(pool_shape, "refill_cost", real)


def _retype_the_ccf_conversion():
    """Hardcode 748 in the page script instead of exporting it.

    SOURCE, because the detector reads render.TEMPLATE -- the page script is a
    string, so the string is the thing under test.
    """
    from . import render
    real = render.TEMPLATE
    marker = "var UNITS = [];"
    if marker not in real:
        return lambda: None
    render.TEMPLATE = real.replace(
        marker, "var UNITS = [{key:'ccf',label:'per CCF',gallons:748}];", 1)
    return lambda: setattr(render, "TEMPLATE", real)


def _ping_the_watchdog_on_a_timer():
    """Make the ping prove a thread ran, which is the worthless version.

    BEHAVIOUR: link_age() is what the detector reads, so a version that always
    answers "fresh" is exactly the watchdog that kept a DNS-stuck agent alive
    for eight minutes.
    """
    from . import agent
    real = agent.link_age
    agent.link_age = lambda: 0.0
    return lambda: setattr(agent, "link_age", real)


def _let_the_stale_threshold_catch_a_quiet_night():
    """Drop LINK_STALE_S below a healthy gap, so an idle pool looks dead."""
    from . import agent
    real = agent.LINK_STALE_S
    agent.LINK_STALE_S = 60
    return lambda: setattr(agent, "LINK_STALE_S", real)


def _make_sd_notify_raise():
    """Let a failed notification reach the caller.

    The agent pings from a thread in the control path; an exception there is a
    watchdog taking down the pool's only sampler by failing to report on it.
    """
    from . import agent
    real = agent.sd_notify

    def strict(state):
        addr = os.environ.get("NOTIFY_SOCKET")
        if not addr:
            raise RuntimeError("no NOTIFY_SOCKET")
        import socket as _s
        with _s.socket(_s.AF_UNIX, _s.SOCK_DGRAM) as k:
            k.connect(addr)
            k.sendall(state.encode())
        return True
    agent.sd_notify = strict
    return lambda: setattr(agent, "sd_notify", real)


def _block_the_notify_socket_in_the_sandbox():
    """Take AF_UNIX back out of RestrictAddressFamilies.

    This is the unit exactly as it was first deployed: WatchdogSec set, the
    notify socket forbidden, and the agent killed every two minutes for a
    silence it could do nothing about.
    """
    return _break_source_file(
        "deploy/poolhound-agent.service",
        "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX",
        "RestrictAddressFamilies=AF_INET AF_INET6")


def _drop_notifyaccess_from_the_unit():
    """Ship WatchdogSec without NotifyAccess, which is the reboot loop.

    NotifyAccess defaults to `none` for every Type but notify, so WATCHDOG=1
    is received and discarded while systemd kills the agent every WatchdogSec
    for never having pinged.
    """
    return _break_source_file("deploy/poolhound-agent.service",
                              "NotifyAccess=main\n", "")


def _register_agent_watchdog():
    """Breakers for the agent's systemd watchdog.

    NOT _register_watchdog: that name is already taken by the breakers for
    bin/watch's own durability, and defining it twice silently replaced the
    first -- so two lowsev detectors quietly lost their breakers. The gate
    caught it, which is the gate working.
    """
    from . import checks_agent as A
    PROVES.extend([
        (A.t_the_watchdog_ping_means_the_link_is_alive,
         _ping_the_watchdog_on_a_timer,
         "a watchdog ping that proves a thread ran and nothing else"),
        (A.t_the_watchdog_ping_means_the_link_is_alive,
         _let_the_stale_threshold_catch_a_quiet_night,
         "a staleness threshold an idle pool would trip"),
        (A.t_notifying_systemd_never_takes_the_agent_down,
         _make_sd_notify_raise,
         "a failed notification raising into the control path"),
        (A.t_the_unit_and_the_agent_agree_about_the_watchdog,
         _drop_notifyaccess_from_the_unit,
         "WatchdogSec shipped without NotifyAccess, which kills a healthy agent"),
        (A.t_the_unit_and_the_agent_agree_about_the_watchdog,
         _block_the_notify_socket_in_the_sandbox,
         "a sandbox that forbids the socket the watchdog ping needs"),
    ])


def _register_refill():
    from . import checks_photo as P
    PROVES.extend([
        (P.t_a_refill_cost_reads_the_bill_the_way_it_is_written,
         _ignore_the_rate_unit,
         "a per-CCF bill figure costed as though it were per gallon"),
        (P.t_the_page_gets_the_rate_units_from_the_one_place_that_defines_them,
         _retype_the_ccf_conversion,
         "the CCF conversion retyped in the page script"),
    ])


def _register_heredocs():
    from . import checks_gates as G
    PROVES.extend([
        (G.t_a_systemd_unit_says_what_it_means,
         _put_the_startlimit_keys_back_in_service,
         "a [Unit] key in [Service], which systemd ignores"),
        (G.t_a_systemd_unit_says_what_it_means,
         _order_the_agent_behind_the_target_that_wants_it,
         "a unit ordered after the target that wants it"),
    ])
    PROVES.append(
        (G.t_no_heredoc_runs_the_deploy_on_the_wrong_machine,
         _put_a_backtick_back_inside_the_heredoc,
         "a heredoc running commands on the machine launching the deploy"))


def _register_seam_visibility():
    PROVES.append(
        (t_the_seam_gate_can_see_the_page_script,
         _blank_every_triple_quoted_run_again,
         "the gate blind to every triple-quoted string again"))


def _register_collection_states():
    from . import checks_collection as C
    PROVES.extend([
        (C.t_a_source_that_never_ran_is_not_a_source_that_is_fine,
         _stop_counting_a_source_that_never_ran,
         "a source that has never run counted as fine again"),
        (C.t_a_skip_is_not_a_success, _grade_a_skip_as_working,
         "a skipped run wearing the green 'working' pill again"),
        (C.t_a_wall_of_skips_is_not_freshness,
         _measure_lateness_from_the_last_attempt,
         "a wall of skips holding the staleness clock fresh again"),
    ])


def _register_refresh():
    from . import checks_collection as C
    PROVES.extend([
        (C.t_what_a_lab_said_is_never_markup,
         _put_the_lab_s_words_back_into_innerHTML,
         "a lab's error text concatenated into innerHTML again"),
        (C.t_the_message_the_page_shows_is_the_one_a_lab_can_write,
         _let_the_server_stop_reporting_vendor_text,
         "the page no longer showing the collector's own output"),
    ])


def _register_agent():
    from . import checks_agent as A
    PROVES.extend([
        (A.t_an_interlock_survives_the_agent_restarting,
         _let_a_restart_clear_the_interlocks,
         "a deploy restart clearing the gas heater's cooldown again"),
        (A.t_the_agent_still_starts_when_its_state_is_unreadable,
         _make_unreadable_state_fatal,
         "a corrupt state file stopping the pool's only sampler"),
        (A.t_the_agent_still_starts_when_its_state_is_unreadable,
         _make_an_unwritable_state_fatal,
         "a failed write reported as a failed command"),
    ])


_register_headers()
_register_lowsev()
_register_mail()
_register_backup()
_register_identity()
_register_photo_bomb()
_register_agent()
_register_refresh()
_register_collection_states()
_register_watchdog()
_register_chrome()
_register_photo()
_register_charts()
_register_assistant()
_register_catalogue()


# THE DETECTORS THAT DO NOT YET DECLARE A BREAKER, named.
#
# The mechanism reached two of seventy-three when CLAUDE.md claimed it reached
# every one. Widening it found thirty in the checks_* modules with no declared
# breaker — and writing thirty perfunctory breakers to turn the number green
# would be the same dishonesty in the other direction.
#
# So the backlog is written down and may only SHRINK: a detector added without
# a breaker fails immediately, and one removed from this set can never come
# back. That is the pattern the section-icon ceiling already uses, and it is
# the truthful version of "every detector proves it fires" until the set is
# empty.
UNPROVEN_TODAY = {
    "checks_a11y.t_charts_are_not_announced_as_their_own_tick_labels",
    "checks_a11y.t_control_names_come_from_the_device_vocabulary",
    "checks_a11y.t_section_icon_fallback_is_a_measured_number",
    "checks_a11y.t_status_messages_and_tabs_are_wired",
    "checks_a11y.t_the_attention_dot_is_not_colour_alone",
    "checks_gates.t_every_get_route_is_classified",
    "checks_gates.t_role_ui_selectors_name_real_elements",
    "checks_gates.t_the_collector_roster_is_declared_by_cron",
    "checks_gates.t_the_public_routes_are_not_gated",
    "checks_gates.t_the_suite_notices_itself_shrinking",
    "checks_gates.t_the_tab_dots_are_inventoried",
    "checks_lowsev.t_a_dead_link_is_noticed_in_seconds",
    "checks_lowsev.t_a_partial_resubmit_keeps_what_it_did_not_mention",
    "checks_lowsev.t_a_sentinel_is_not_a_measurement",
    "checks_lowsev.t_never_configured_is_not_broken",
    "checks_lowsev.t_one_pending_window",
    "checks_lowsev.t_rows_honours_the_parameters_it_reports",
    "checks_lowsev.t_the_agent_token_fingerprint_has_a_caller",
    "checks_lowsev.t_the_command_log_pairs_its_durable_rows",
    "checks_render_data.t_a_dose_is_addressable_by_id_everywhere_it_is_written",
    "checks_render_data.t_a_hand_entered_reading_does_not_break_the_render",
    "checks_vault.t_a_correction_is_held_to_the_readings_range",
    "checks_vault.t_a_write_handler_always_answers",
    "checks_vault.t_agent_token_is_inside_the_isolation",
    "checks_vault.t_agent_token_separates_absent_from_unreachable",
    "checks_vault.t_credential_file_is_the_key_the_readers_use",
    "checks_vault.t_ids_are_backfilled_not_invented_twice",
    "checks_vault.t_two_doses_in_one_minute_stay_editable",
    "checks_vault.t_vault_accepts_the_re_entry_it_instructs",
    "checks_vault.t_vault_put_keeps_a_password_it_is_not_given",
}


def t_every_detector_proves_it_fires():
    """Each detector is pointed at the defect it exists to catch, and must fail.

    THIS IS THE GATE THE OTHER GATES NEEDED. Nine checks in this project have
    passed while measuring nothing, and each was found by accident, usually
    while something else was being fixed. A green line is evidence only if the
    same code can be shown to go red.

    Run by breaking the world, calling the detector, and asserting it appended
    a failure — then putting the world back and discarding the failure it was
    asked to produce, so a proof never fails the run it is proving.
    """
    print("\n  detectors — each one proves it fires")
    for detector, breaker, what in PROVES:
        before, skips_before = len(FAILURES), len(SKIPPED)
        # A BREAKER IS NOT ALLOWED TO END THE RUN. It used to be called outside
        # this guard, so one that raised took the whole suite with it and
        # nothing after it ran at all -- including the other eleven checks_*
        # modules. MEASURED: _merge_only_on_first_install opened a deploy
        # script that is not in the image, and the resulting FileNotFoundError
        # failed every image build for four days at the Dockerfile's own gate.
        # A proof that cannot run is reported, loudly, in its own line; it is
        # not a reason to stop asking the remaining questions.
        try:
            undo = breaker()
        except Exception as e:                  # noqa: BLE001
            check(f"proof: {detector.__name__} has a breaker that runs "
                  f"({type(e).__name__}: {e})", False, True)
            continue
        # None means the breaker has said it cannot break THIS tree -- the
        # input is not here. Distinct from a breaker that ran and changed
        # nothing, which is the failure the loop below reports.
        if undo is None:
            skipped(f"proof: {detector.__name__} notices {what}",
                    "the breaker has nothing to break in this tree")
            continue
        try:
            # Silenced: the detector prints its own ok/FAIL lines, and a proof
            # that prints a FAIL it deliberately caused makes a green run look
            # red to anybody reading the log.
            import contextlib, io
            fired = False
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    detector()
                except Exception:               # noqa: BLE001
                    # Raising IS noticing — a detector that blows up on a
                    # broken world has detected it, even if ungracefully. It
                    # counts, and the suite carries on rather than dying at the
                    # proof. (A detector that raises is still worth tidying:
                    # this caught one indexing rows()[0] on an empty list.)
                    fired = True
            fired = fired or len(FAILURES) > before
            # A DETECTOR THAT COULD NOT RUN HAS NOT DECLINED TO FIRE. In the
            # deploy image there are no rendered pages, no README and no
            # deploy scripts, so five detectors here had nothing to look at —
            # and their proofs reported "did not notice", which reads as a
            # broken gate rather than an absent input. It is the same
            # distinction the runner now keeps everywhere else.
            unanswerable = len(SKIPPED) > skips_before
        finally:
            undo()
            del FAILURES[before:]          # the failure was requested, not real
            del SKIPPED[skips_before:]
        if unanswerable and not fired:
            skipped(f"proof: {detector.__name__} notices {what}",
                    "the detector has nothing to look at in this tree")
            continue
        check(f"{detector.__name__} notices {what}", fired, True)

    # AND EVERY DETECTOR IN EVERY checks_*.py MODULE, not just this one.
    #
    # This walked globals() — so it covered the two detectors in this file and
    # said nothing about the other seventy-one, while CLAUDE.md claimed "every
    # detector proves it fires". Demonstrated by a reviewer: a case whose only
    # assertion was `check("this checks nothing at all", True, True)`, added to
    # another checks_ module, was discovered, run, and left the suite green.
    #
    # Every example the claim rests on — selftest and contrast with no
    # __main__, the sentinel regex, the Caddy literal matcher — is a detector
    # in a DIFFERENT file, i.e. outside what the mechanism reached.
    #
    # PROVES is a registry other modules extend, so a detector there declares
    # its breaker the same way. Cases in selftest.py itself are exempt for now
    # and NAMED as such rather than quietly uncounted: that file predates this
    # mechanism by seventy cases, and an exemption somebody can see is a
    # different thing from a gap nobody measured.
    import importlib, pkgutil, poolhound
    unproven = []
    proven = {d for d, _, _ in PROVES}
    for m in sorted(pkgutil.iter_modules(poolhound.__path__), key=lambda x: x.name):
        if not m.name.startswith("checks_"):
            continue
        mod = importlib.import_module(f"poolhound.{m.name}")
        for k, v in vars(mod).items():
            if (k.startswith("t_") and callable(v)
                    and getattr(v, "__module__", None) == mod.__name__
                    and k != "t_every_detector_proves_it_fires"
                    and v not in proven
                    and not getattr(v, "_no_breaker_because", None)):
                unproven.append(f"{m.name}.{k}")
    fresh = sorted(set(unproven) - UNPROVEN_TODAY)
    check(f"no NEW detector arrives without a breaker "
          f"({len(unproven)} in the named backlog)", fresh, [])
    # And the backlog only shrinks. A name that leaves this set has been given
    # a breaker; letting it return would make the set decoration.
    gone = sorted(UNPROVEN_TODAY - set(unproven))
    check("and a detector that gained a breaker has not lost it again",
          [n for n in gone if n in set(unproven)], [])


def t_the_seam_gate_can_see_the_page_script():
    """The gate must look at the code, not only at a third of it.

    MEASURED. _code_only blanked every triple-quoted run in the file, so the
    gate was blind to 59.2% of the package: 78% of render.py, 99.5% of
    style.py. render.TEMPLATE is a \"\"\" string carrying the whole page script
    and its JSON catalogue, so every fact the BROWSER holds was invisible —
    while seams.py's own docstring names "five call sites ... plus a sixth in
    the page script" as a reason this file exists. The gate could not have
    found the copy it was written for.

    It found three the moment it could look: the series palette, retyped as
    JS fallbacks in render.TEMPLATE, in the one place bin/contrast cannot
    measure and a screenshot cannot show.

    A docstring is a bare string EXPRESSION; TEMPLATE is a string ASSIGNED to
    a name, and the parser knows the difference.
    """
    from . import seams, render
    print("\n  seams — the gate can see inside the page script")

    src = (
        'MARKER_ASSIGNED = """\n'
        'the series palette #2a78d6 lives here\n'
        '"""\n'
        'def f():\n'
        '    """A docstring naming #2a78d6, which is prose."""\n'
        '    return 1  # a trailing comment naming #2a78d6\n'
    )
    seen = seams._code_only(src)
    check("an assigned triple-quoted string stays visible",
          "#2a78d6" in seen.split("def f()")[0], True)
    check("a docstring is blanked", seen.count("#2a78d6"), 1)
    check("and so is a trailing comment",
          "trailing comment" in seen, False)

    # OFFSETS, which is the whole reason it blanks rather than deletes.
    check("the text keeps its length", len(seen), len(src))
    check("and its line count", seen.count("\n"), src.count("\n"))

    # ast reports columns in UTF-8 BYTES and tokenize in CHARACTERS; a line
    # with an em dash disagreed and the file came out longer than it went in.
    uni = 'X = """keep — this"""\ndef g():\n    """drop — that"""\n'
    got = seams._code_only(uni)
    check("a non-ASCII line does not shift the offsets", len(got), len(uni))

    # AND THE REAL FILE, not just a fixture.
    vis = seams._code_only(render.TEMPLATE_SOURCE
                           if hasattr(render, "TEMPLATE_SOURCE") else
                           open(render.__file__, encoding="utf-8").read())
    check("render.py's page script is visible to the gate",
          "getElementById" in vis, True)


def _move_the_container_to_another_timezone():
    """Point the Dockerfile's TZ somewhere other than the pool.

    The honest break: a container running on a different offset is not a
    cosmetic difference. best_lab() would weigh a Leslie's local date against
    a WaterGuru UTC instant in the wrong frame and could quote a stale
    alkalinity with nothing on screen looking wrong.

    On disk, because the detector reads the file; None when deploy/ is not in
    this tree, which is the rule that cost four days of image builds.

    The zone is read from config rather than typed, because typing it here
    made this breaker the second copy and the detector it proves caught that
    immediately — which is the gate working on the file written to test it.
    """
    return _break_source_file("deploy/Dockerfile",
                              f"TZ={config.DEPLOY_TZ}", "TZ=Etc/UTC")


def _register_deploy_timezone():
    from . import checks_gates as G
    PROVES.append(
        (G.t_the_deployment_pins_the_pools_timezone,
         _move_the_container_to_another_timezone,
         "a container pinned to a zone the pool is not in"))


def _pin_the_demo_pool_ahead_of_the_clock():
    """Put the demo pool's clock a day into the future.

    The honest break: every freshness panel in every screenshot grades the
    generator instead of the pool, and the landing page's Home card carries
    two red "a clock is wrong somewhere" advisories — which is what it did.

    A DAY AHEAD, not the original `NOW = _PIN`. The defect was that 14:12 today
    is in the future until 14:12, so restoring it literally would give a
    breaker that breaks before lunch and passes after it: the detector would
    read green every afternoon while proving nothing, which is the same trap
    the detector itself exists to close.
    """
    return _break_source_file(
        "bin/demo-data",
        "NOW = _PIN if _PIN <= dt.datetime.now() else _PIN - dt.timedelta(days=1)",
        "NOW = _PIN + dt.timedelta(days=1)")


def _register_demo_clock():
    from . import checks_gates as G
    PROVES.append(
        (G.t_the_demo_pool_is_never_dated_in_the_future,
         _pin_the_demo_pool_ahead_of_the_clock,
         "a demo pool stamped after the clock that reads it"))


# ------------------------------------------------- the switch log, whose cases
#                                                   mostly assert an ABSENCE
#
# Four of these assert that nothing was emitted — no event from a blank
# reading, none from a re-pushed row, none from a circuit the panel does not
# report. A function that returned [] for every input on earth would pass all
# four, which is precisely the shape the proof mechanism exists to catch, so
# each one names the edit that must break it.
def _read_a_blank_reading_as_off():
    """Treat an absent reading as a reported "off".

    The honest break: every gap in the sample record grows a switch-off at the
    start of it and a switch-on at the end, and the log fills with switching
    that never happened at the one time the pool was least observed.
    """
    return _break_live_code(
        "poolhound/switchlog.py",
        "poolhound.switchlog",
        'if a not in ("0", "1") or b not in ("0", "1") or a == b:',
        "if a == b:")


def _walk_the_samples_in_file_order():
    """Stop sorting the samples before walking them.

    The honest break: the agent re-pushes after a reconnect, so an older row
    lands after a newer one, and every circuit that changed in between reads
    as having changed twice — once forwards and once back. A no-op key rather
    than a deleted line, because `sort` is stable and that is what reading the
    file in order actually means.
    """
    return _break_live_code("poolhound/switchlog.py",
        "poolhound.switchlog",
                              'rows.sort(key=lambda r: _at(r["ts"]))',
                              "rows.sort(key=lambda r: 0)")


def _skip_the_unreported_circuits_by_name():
    """Fall back to the device id when SWITCHES gives no column.

    The honest break: Aux_4 and Aux_5 are declared with `column: None` because
    the panel reports nothing about them. Invent a column and the derivation
    reads a field that is never present — silently, as "" on every row, so the
    log says those circuits have never once been touched.
    """
    return _break_live_code(
        "poolhound/switchlog.py",
        "poolhound.switchlog",
        'cols = [(dev, spec["column"]) for dev, spec in commands.SWITCHES.items()\n'
        '            if spec.get("column")]',
        'cols = [(dev, spec.get("column") or dev)\n'
        '            for dev, spec in commands.SWITCHES.items()]')


def _forget_the_margin_before_the_window():
    """Credit only a command issued strictly inside the sample gap.

    The honest break: a command issued seconds before the earlier sample is
    still that command — the panel acts in about a second and the next sample
    is what catches it. Without the margin the log reports a person's own
    action back to them as somebody else's.
    """
    return _break_live_code("poolhound/switchlog.py",
        "poolhound.switchlog",
                              "MARGIN_S = 120", "MARGIN_S = 0")


def _sort_the_switch_log_oldest_first():
    """Reverse the ordering the limit is applied to.

    The honest break: `limit` then keeps the oldest events, so a pool with
    years behind it opens its Control tab on the year it was installed — and
    the table still looks full, which is why nobody would notice.
    """
    return _break_live_code(
        "poolhound/switchlog.py",
        "poolhound.switchlog",
        'out.sort(key=lambda e: e["_order"], reverse=True)',
        'out.sort(key=lambda e: e["_order"])')


def _count_the_summary_from_the_drawn_rows():
    """Report nothing as having come through poolhound.

    The honest break: the summary is the one figure that says how much of this
    pool's switching the product is actually responsible for. Hard-coded to
    zero it reads as "none of it", on a deployment where the answer matters
    enough that it is the reason this panel was rewritten.
    """
    return _break_live_code("poolhound/switchlog.py",
        "poolhound.switchlog",
                              'mine = sum(1 for r in rows '
                              'if r.get("how") == "poolhound")',
                              "mine = 0")


def _let_a_bad_stamp_become_the_epoch():
    """Parse an unreadable timestamp as 1970 instead of refusing it.

    The honest break: the unplaceable row joins the record at the beginning of
    time, so the first real sample after it reads as a change from whatever
    that row happened to hold — one corrupt field inventing an event, with
    nothing on the page admitting the row was unreadable.
    """
    return _break_live_code(
        "poolhound/switchlog.py",
        "poolhound.switchlog",
        "    return _WHEN(str(s or \"\").strip())",
        "    return _WHEN(str(s or \"\").strip()) or dt.datetime(1970, 1, 1)")


def _call_an_acknowledgement_a_command():
    """Count control.ack rows as commands.

    The honest break: `control.ack` is the agent reporting back on a command
    already in the list, so one person's one press is credited twice — and the
    ack arrives about a second after the panel agreed, which is well inside the
    window, so it is the copy that wins.
    """
    return _break_live_code(
        "poolhound/switchlog.py",
        "poolhound.switchlog",
        'if (r.get("action") or "") != "control":',
        'if not (r.get("action") or "").startswith("control"):')


def _report_a_switch_on_as_a_switch_off():
    """Invert the word an event carries.

    The honest break: the log says the spa heater went off at the moment it
    came on. Every number on the page would still be right and the one word a
    reader actually acts on would be wrong, which is the direction this
    project has already shipped once — a confirmation dialog warning about the
    opposite of the button pressed.
    """
    return _break_live_code("poolhound/switchlog.py",
        "poolhound.switchlog",
                              '"word": "on" if b == "1" else "off",',
                              '"word": "off" if b == "1" else "on",')


def _read_a_stored_value_as_a_string():
    """Hand describe() the raw text after the "=" again.

    The honest break: the string "0" is truthy, so every switch-OFF in the
    half of the log that survives a restart reads as ON. It was like this, and
    the session half being correct is what hid it.
    """
    return _break_live_code(
        "poolhound/commands.py", "poolhound.commands",
        '            try:\n                value = int(raw)',
        '            try:\n                value = str(raw)')


def _filter_the_page_instead_of_the_record():
    """Paginate first, then filter.

    The honest break: the filter searches the fifty rows this page happens to
    hold and reports a count for the hundreds it did not look at. A filter that
    quietly searches part of the record is worse than no filter, and this is
    the reason the merge is in Python at all.
    """
    return _break_live_code(
        "poolhound/switchlog.py", "poolhound.switchlog",
        "    total = len(rows)\n    keep = rows",
        "    total = len(rows)\n    keep = rows[:per]")


def _believe_a_filter_value_nothing_can_hold():
    """Apply `device` and `how` without checking them against the vocabulary.

    The honest break: `?device=nonsense` filters every row away, and the page
    says no circuit has ever changed state -- about a pool that has been
    running all week. A query string must not be able to make that claim.
    """
    return _break_live_code(
        "poolhound/switchlog.py", "poolhound.switchlog",
        '    device = device if device in known else ""\n'
        '    how = how if how in HOWS else ""',
        "    device, how = device, how")


def _trust_the_page_number():
    """Stop clamping the page.

    The honest break: page=0 slices from -per and hands back the LAST page
    while reporting itself as the first; a page past the end returns nothing,
    which reads as an empty record rather than a page that does not exist.
    """
    return _break_live_code(
        "poolhound/switchlog.py", "poolhound.switchlog",
        "    page = min(max(1, _int(page, 1) or 1), pages)",
        "    page = _int(page, 1)")


def _draw_a_command_from_both_sides():
    """List the witnessed change as well as the command that caused it.

    The honest break: one person's one press appears twice, a minute apart, as
    if they had pressed it again -- on the log somebody would read to work out
    whether a heater had been double-started.
    """
    return _break_live_code(
        "poolhound/switchlog.py", "poolhound.switchlog",
        "        if e[\"exact\"]:\n            # Already above",
        "        if False:\n            # Already above")


def _throw_away_the_doors_claim():
    """Stop reading the panel.change rows.

    The honest break: the product's own record of WHICH local route switched
    something is discarded, and the panel reverts to telling the reader the
    origins cannot be told apart — while six rows saying otherwise sit in the
    audit log. That was the state this detector was written for.
    """
    return _break_live_code(
        "poolhound/switchlog.py", "poolhound.switchlog",
        '        if (r.get("action") or "") != "panel.change":',
        '        if True:')


def _register_switchlog():
    from . import checks_switchlog as W
    PROVES.extend([
        (W.t_a_blank_reading_is_not_off, _read_a_blank_reading_as_off,
         "an absent reading counted as an off one"),
        (W.t_rows_out_of_order_do_not_invent_switching,
         _walk_the_samples_in_file_order,
         "a re-pushed row read as two changes"),
        (W.t_a_circuit_the_panel_does_not_report_is_skipped_by_its_column,
         _skip_the_unreported_circuits_by_name,
         "a column invented for a circuit the panel does not report"),
        (W.t_a_poolhound_command_is_credited_to_the_person_who_sent_it,
         _forget_the_margin_before_the_window,
         "a command credited to the panel for arriving a minute early"),
        (W.t_the_limit_keeps_the_newest_and_the_page_says_how_many,
         _sort_the_switch_log_oldest_first,
         "a limit that keeps the oldest events"),
        (W.t_the_summary_counts_both_origins,
         _count_the_summary_from_the_drawn_rows,
         "a summary that credits poolhound with none of it"),
        (W.t_a_malformed_stamp_does_not_take_the_tab_down,
         _let_a_bad_stamp_become_the_epoch,
         "an unreadable stamp joining the record at the epoch"),
        # The ack case lives in the attribution detector, which is therefore
        # registered twice: PROVES pairs are tested independently, and one
        # detector can be the proof of more than one property.
        (W.t_a_poolhound_command_is_credited_to_the_person_who_sent_it,
         _call_an_acknowledgement_a_command,
         "an acknowledgement counted as a second command"),
        (W.t_a_change_between_two_samples_is_one_event,
         _report_a_switch_on_as_a_switch_off,
         "a change reported in the wrong direction"),
        (W.t_a_stored_switch_off_does_not_read_as_an_on,
         _read_a_stored_value_as_a_string,
         "a stored switch-off that rebuilds as an on"),
        (W.t_a_filter_searches_the_whole_record_not_a_page,
         _filter_the_page_instead_of_the_record,
         "a filter that searches one page and counts the record"),
        (W.t_a_filter_value_nothing_can_hold_is_not_applied,
         _believe_a_filter_value_nothing_can_hold,
         "a query string that can empty the record"),
        (W.t_the_page_number_is_clamped_at_both_ends,
         _trust_the_page_number,
         "a page number taken from a URL unclamped"),
        (W.t_a_command_appears_once_not_twice,
         _draw_a_command_from_both_sides,
         "one press listed twice, from both sides"),
        (W.t_a_door_that_claims_a_change_is_named,
         _throw_away_the_doors_claim,
         "a door's own claim thrown away"),
    ])


# ------------------------------------------------------------------- upkeep
#
# Each breaker is the plausible wrong version of one line of upkeep.py: a
# wrong acid figure is a wrong amount of the chemical that damages plaster,
# so every way the balance could quietly drift has to be shown to be caught.
def _upkeep(needle, replacement):
    return lambda: _break_live_code("poolhound/upkeep.py", "poolhound.upkeep",
                                    needle, replacement)


def _price_nophos_by_the_pound():
    """Name a liquid's price per pound -- every dose of it then costs 128 times
    what a gallon-priced box would say, with nothing on the page looking off."""
    from . import config
    real = config.PRICE_SETTINGS["nophos"]
    config.PRICE_SETTINGS["nophos"] = "nophos_per_lb"
    def undo():
        config.PRICE_SETTINGS["nophos"] = real
    return undo


def _register_upkeep():
    from . import checks_upkeep as K, checks_assistant as A
    PROVES.extend([
        (A.t_the_targets_it_sends_are_this_pools,
         lambda: _break_live_code(
             "poolhound/assistant.py", "poolhound.assistant",
             'tg = render.targets(\n        lab, (cfg.get("pool")',
             'tg = render.targets(\n        cfg, (cfg.get("pool")'),
         "the config handed to targets() as if it were lab results"),
        (K.t_the_ledger_hands_the_page_numbers_it_only_has_to_add,
         _upkeep('qty = base / 128.0 if unit == "gal" else base', "qty = base"),
         "fluid ounces summed as if they were gallons"),
        (K.t_the_ledger_hands_the_page_numbers_it_only_has_to_add,
         _upkeep('"cost": round(qty * p["price"], 6) if p else None})',
                 '"cost": round(qty * p["price"], 2) if p else None})'),
         "each dose rounded to the cent before the month sums them"),
        (K.t_an_unreadable_dose_is_counted_not_dropped,
         _upkeep("        if base is None:\n            skipped += 1\n            continue",
                 "        if base is None:\n            continue"),
         "an unreadable dose dropped without a word"),
        (K.t_every_chemical_can_be_priced_and_named, _price_nophos_by_the_pound,
         "a liquid's price setting named per pound"),
        (K.t_a_cost_says_whose_price_it_used,
         _upkeep("    if own:\n        return {\"price\": own",
                 "    if False:\n        return {\"price\": own"),
         "the household's own price ignored for the reference"),
        (K.t_a_reference_acid_price_is_for_its_own_strength,
         _upkeep('if "pct" in ref and abs(ref["pct"] - pct) > 0.01:',
                 "if False:"),
         "a 31.45% price applied to 14.5% acid"),
        (K.t_the_total_keeps_a_bound_out_of_the_sum,
         _upkeep('out["cost_year"] = sum(r["cost_year"] for r in rows\n'
                 '                           if r.get("cost_year") is not None)',
                 'out["cost_year"] = sum((r.get("cost_year") or r.get("cost_hi") or 0)\n'
                 '                           for r in rows)'),
         "an upper bound added into the year's total as if it were spent"),
        (K.t_a_price_setting_is_accepted_cleared_and_refused,
         lambda: _break_live_code(
             "poolhound/server.py", "poolhound.server",
             "(float(v) if 0 < float(v) < 1000 else None))\n"
             "       for name in config.PRICE_SETTINGS.values()},",
             "(float(v) if 0 <= float(v) < 1000 else None))\n"
             "       for name in config.PRICE_SETTINGS.values()},"),
         "a price of zero accepted"),
        (K.t_acid_is_what_went_in_when_ph_ends_where_it_started,
         _upkeep('inside = [d for d in days if lo_d < d["date"] <= hi_d]',
                 'inside = [d for d in days if lo_d <= d["date"] <= hi_d]'),
         "the first reading's own day counted as pump hours after it"),
        (K.t_a_net_rise_is_priced_as_the_acid_it_would_have_taken,
         _upkeep("demand = acid_in + rise / -per_floz", "demand = acid_in"),
         "a pool that ended higher than it started, costed as if it had not"),
        (K.t_an_outage_is_a_hole_not_a_pump_that_was_off,
         _upkeep('return cov.get(day["date"], 0) >= WHOLE_DAY_SAMPLES',
                 'return cov.get(day["date"], 0) >= 0'),
         "an agent outage read as a pump that was off"),
        (K.t_a_reading_too_soon_after_a_dose_does_not_end_the_balance,
         _upkeep("t - a < dt.timedelta(hours=SETTLE_H)]",
                 "t - a < dt.timedelta(hours=0)]"),
         "a reading two hours after a dose taken as the pool"),
        (K.t_too_little_record_says_why_instead_of_guessing,
         _upkeep("if len(acids) < 2:", "if len(acids) < 1:"),
         "a balance drawn from a single dose"),
        (K.t_acid_strength_is_normalised_before_it_is_added,
         _upkeep("return base * pct / ref_pct", "return base"),
         "a 14.5% dose counted as if it were 31.45%"),
        (K.t_salt_with_no_trend_is_a_bound_not_a_figure,
         _upkeep('"measured": lo > 0,', '"measured": True,'),
         "scatter reported as a measured loss"),
        (K.t_a_real_decline_is_measured,
         _upkeep("loss, lo, hi = -b, max(-b - 2 * se, 0.0), max(-b + 2 * se, 0.0)",
                 "loss, lo, hi = b, max(b - 2 * se, 0.0), max(b + 2 * se, 0.0)"),
         "a falling salt line read the wrong way up"),
        (K.t_a_bag_of_salt_is_not_the_pool_gaining_salt,
         _upkeep('before = sum(ppm for on, ppm in added if on < d["date"])',
                 "before = 0"),
         "a logged bag of salt read as the pool gaining salt"),
        (K.t_what_follows_from_the_acid_and_the_salt,
         _upkeep('ta_wk = -CH.effects("acid", wk, "floz", acid_pct, gallons, ta)["ta"]',
                 'ta_wk = -CH.effects("acid", wk / 7, "floz", acid_pct, gallons, ta)["ta"]'),
         "a day's alkalinity loss quoted as a week's"),
    ])


_register_upkeep()
_register_seam_visibility()
_register_heredocs()
_register_refill()
_register_agent_watchdog()
_register_deploy_timezone()
_register_demo_clock()
_register_switchlog()
