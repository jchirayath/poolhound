"""Credentials and the write paths — the cases behind the VAULT remediation.

Every case here is a defect that was demonstrated against a running server
before it was fixed, and each one asserts the ANSWER rather than the shape of
the code: a vault that accepts the re-entry its own error instructs, a dose that
stays addressable when two share a minute, a corrected value held to the same
range a hand-entered one is, and an isolation that covers the agent token.

Discovered by selftest.cases() because the file is named checks_*.py.
"""
import csv
import importlib
import os
import tempfile

from .selftest import check


def _isolated_vault():
    """A vault module pointed at a fresh directory, and the env restored after.

    Returns (module, directory, restore). NOTHING in this file may touch the
    operating user's ~/.poolhound, ~/.waterguru or keychain items under the bare
    names — that is the boundary three of these defects escaped through, and a
    check that escapes it while testing it is worse than no check.
    """
    from . import vault
    prev = os.environ.get("POOLHOUND_VAULT")
    prev_tok = os.environ.get("POOLHOUND_AGENT_TOKEN_FILE")
    prev_kv = os.environ.get("POOLHOUND_KEYVAULT")
    d = tempfile.mkdtemp()
    os.environ["POOLHOUND_VAULT"] = d
    os.environ.pop("POOLHOUND_AGENT_TOKEN_FILE", None)
    os.environ.pop("POOLHOUND_KEYVAULT", None)
    importlib.reload(vault)

    def restore():
        for k, v in (("POOLHOUND_VAULT", prev),
                     ("POOLHOUND_AGENT_TOKEN_FILE", prev_tok),
                     ("POOLHOUND_KEYVAULT", prev_kv)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(vault)
    return vault, d, restore


# ------------------------------------------------- an unreadable vault.enc
def t_vault_accepts_the_re_entry_it_instructs():
    """An error that says "enter them again" has to let somebody enter them again.

    Measured on a sandboxed server with one byte of vault.enc flipped: Settings
    correctly reported the passwords unrecoverable, then saving one returned
    that same sentence (400) because put() read the store before writing it, and
    Forget answered NOTHING — curl exit 52, the socket closed with no HTTP
    status, because drop() raised from outside save_credential's handler. The
    only remedy left was deleting the file by hand, inside a 0700 volume on the
    server.

    The unreadable ciphertext is backed up rather than dropped: it is still the
    only copy of those passwords, and a keychain entry removed by accident can
    come back.
    """
    vault, d, restore = _isolated_vault()
    print("\n  vault — a store that cannot be read still accepts a new password")
    try:
        if not vault._keychain_available():
            print("    skip  no keychain on this host; nothing to encrypt against")
            return
        vault.put("waterguru", "first@example.invalid", "first-secret")
        blob = bytearray(open(vault.VAULT, "rb").read())
        blob[-1] ^= 0xFF                       # the ciphertext no longer verifies
        open(vault.VAULT, "wb").write(bytes(blob))

        # A READ still refuses. Presenting an empty store as a healthy one would
        # be the same lie in the other direction.
        check("a read of an undecryptable store still reports the failure",
              vault.get("waterguru"), None)
        check("and status() carries the reason rather than a blank",
              bool(vault.status()["error"]), True)

        where = vault.put("waterguru", "second@example.invalid", "second-secret")
        check("a WRITE starts a fresh store instead of refusing",
              bool(where), True)
        check("and the new password is the one that reads back",
              vault.get("waterguru"), ("second@example.invalid", "second-secret"))
        backups = [f for f in os.listdir(d) if ".unreadable-" in f]
        check("the unreadable ciphertext was backed up, not deleted",
              len(backups), 1)

        # Forget, on a store that cannot be read, which is the half that closed
        # the socket. It must ANSWER.
        blob = bytearray(open(vault.VAULT, "rb").read())
        blob[-1] ^= 0xFF
        open(vault.VAULT, "wb").write(bytes(blob))
        vault.drop("waterguru")
        check("drop() on an undecryptable store returns rather than raising",
              vault.status()["services"]["waterguru"]["stored"], False)
    finally:
        restore()


def t_vault_put_keeps_a_password_it_is_not_given():
    """The password field's placeholder says `unchanged`. It has to mean it.

    status() returns the USERNAME on purpose — "getting that wrong is a common
    and confusing failure" — and put() required both fields, so the one field
    the product deliberately displays was the one it could not correct without
    the password. Measured over HTTP: a save with the password left blank
    answered 400 before, 200 after, and the stored password read back unchanged.
    """
    vault, d, restore = _isolated_vault()
    print("\n  vault — a blank password means unchanged, not refused")
    try:
        if not vault._keychain_available():
            print("    skip  no keychain on this host; nothing to encrypt against")
            return
        vault.put("leslies", "typo@example.invalid", "the-real-secret")
        vault.put("leslies", "fixed@example.invalid", "")
        check("the username is corrected", vault.get("leslies")[0],
              "fixed@example.invalid")
        check("and the password is the one already stored",
              vault.get("leslies")[1], "the-real-secret")
        # With nothing stored there is nothing to keep, and storing a login that
        # cannot log in is worse than refusing.
        try:
            vault.put("waterguru", "nobody@example.invalid", "")
            got = "accepted"
        except vault.VaultError:
            got = "refused"
        check("a blank password with nothing stored is refused", got, "refused")
    finally:
        restore()


# --------------------------------------------- one accessor for a file path
def t_credential_file_is_the_key_the_readers_use():
    """Writer and readers must look under ONE name. They did not.

    save_credential read cfg["smtp"]["credentials"] — a section that appears in
    no config.toml, in no config.DEFAULTS and nowhere else in the repository, so
    it was None on every install — while watch.py reads
    notify.smtp_credentials, which is the key the Settings form beside it edits.
    So a file-store save of the mail password always landed on the built-in
    ~/.poolhound_smtp whatever the form said.

    Measured against config.example.toml: the old key answered None for smtp;
    credential_file() answers what the form wrote.
    """
    from . import vault
    print("\n  vault — one accessor answers where a credential file lives")
    cfg = {"waterguru": {"credentials": "/tmp/ph-wg"},
           "leslies": {"credentials": "/tmp/ph-le"},
           "notify": {"smtp_credentials": "/tmp/ph-smtp"}}
    # Derived from SERVICES, so a service added without a config key is caught
    # here rather than by a save that quietly goes somewhere else.
    check("every service declares where its file is configured",
          sorted(s for s in vault.SERVICES if vault.config_key(s)),
          sorted(vault.SERVICES))

    # RUN WITH THE ISOLATION OFF, because the accessor answers "" under it and
    # three of these checks would then pass while measuring nothing — which is
    # the failure mode selftest.py opens by naming. Nothing here touches disk:
    # credential_file() resolves a path and returns it.
    prev = os.environ.pop("POOLHOUND_VAULT", None)
    importlib.reload(vault)
    try:
        check("smtp follows notify.smtp_credentials, the key the form writes",
              vault.credential_file("smtp", cfg), "/tmp/ph-smtp")
        check("waterguru follows its own key",
              vault.credential_file("waterguru", cfg), "/tmp/ph-wg")
        # THE SECTION THAT WAS BEING READ, named as data rather than quoted as a
        # fact: nothing in the product supplies it, which is the whole defect.
        missing = vault.config_key("smtp")[0]
        check("and it is not the section the writer used to read",
              (missing, missing in cfg), ("notify", True))
        # A configured path is still refused under isolation, for the reason
        # put_file() gives: config.DEFAULTS names one on every install.
        os.environ["POOLHOUND_VAULT"] = tempfile.mkdtemp()
        importlib.reload(vault)
        # DERIVED FROM THE SERVICES, not a literal of the right length. The
        # claim here is "every service resolves to nothing under isolation",
        # and spelling that as ["", "", ""] made it also assert "there are
        # three of them" — so adding a fourth service failed this case for a
        # reason that had nothing to do with isolation.
        check("an isolated install resolves no credential file at all",
              [vault.credential_file(s, cfg) for s in vault.SERVICES],
              [""] * len(vault.SERVICES))
    finally:
        os.environ.pop("POOLHOUND_VAULT", None)
        if prev is not None:
            os.environ["POOLHOUND_VAULT"] = prev
        importlib.reload(vault)

    # And the WRITER goes through it. put_file is replaced by a recorder rather
    # than called: the old code would have written this household's real
    # ~/.poolhound_smtp, and a check that damages the thing it protects on the
    # one run where something is wrong is not a check.
    from . import server
    seen = {}
    real = vault.put_file
    vault.put_file = lambda svc, u, p, path=None: seen.setdefault("path", path) or "x"
    try:
        server.save_credential({"service": "smtp", "username": "u", "password": "p",
                                "store": "file"}, cfg)
    finally:
        vault.put_file = real
    check("save_credential hands put_file the accessor's answer",
          seen.get("path"), vault.credential_file("smtp", cfg) or None)


# ------------------------------------------------------------- agent token
def t_agent_token_is_inside_the_isolation():
    """POOLHOUND_VAULT must move the Pi's shared secret too.

    The token FILE was declared in server.py and read there, before
    vault.agent_token() was consulted — an absolute path POOLHOUND_VAULT does
    not move. So a review server started on the server or the Pi with the vault
    relocated authenticated agents with the household's real shared secret while
    every isolation assertion in the suite passed. This is the third credential
    route found outside the guard and each was one the vault module did not own.

    The exemption for a path somebody typed is kept, and it is narrow: a
    built-in DEFAULT is not a deliberate choice, which is the distinction
    put_file() had to learn the expensive way.
    """
    vault, d, restore = _isolated_vault()
    print("\n  vault — the agent token is behind the isolation like everything else")
    try:
        p = os.path.join(d, "stand-in-for-etc-poolhound.token")
        with open(p, "w") as f:
            f.write("a-households-shared-secret\n")
        os.chmod(p, 0o600)
        vault.TOKEN_FILE = p
        vault.TOKEN_FILE_IS_DEFAULT = True
        check("ISOLATED is set", vault.ISOLATED, True)
        check("a built-in default path is NOT read under isolation",
              vault.agent_token(create=False), None)
        vault.TOKEN_FILE_IS_DEFAULT = False
        check("a path somebody exported on purpose still is",
              vault.agent_token(create=False), "a-households-shared-secret")
    finally:
        restore()


def t_agent_token_separates_absent_from_unreachable():
    """"No token" and "could not ask" had the same answer, and opposite fixes.

    kv_get() never raises: it records the true reason in _kv_errors and returns
    None. put() was taught that distinction; agent_token was not — so an
    unreachable vault, an expired managed identity, a 403 or a 429 all arrived
    as "no token", every /api/agent/* route closed, and the Pi went silent with
    the same symptom as the house being off the air. With create=True the advice
    was to write a SECOND token into a vault that already holds one.

    Measured against a Key Vault name that does not resolve: agent_token raised
    with the reason, the server answered 503 rather than 403, and /api/health
    carried the sentence.
    """
    vault, d, restore = _isolated_vault()
    print("\n  vault — an unreachable Key Vault is not an absent token")
    real_get = vault.kv_get
    try:
        os.environ["POOLHOUND_KEYVAULT"] = "poolhound-selftest-no-such-vault"

        # kv_get STANDS IN, rather than a real lookup against a name that does
        # not resolve. That would put a ten-second network timeout in the suite
        # and would be measuring urllib; what is under test is what agent_token
        # does with a failure kv_get has already recorded — which is exactly the
        # contract the real one keeps: never raise, record the reason, return
        # None.
        def refused(name):
            vault._kv_errors[name] = "HTTP 403"
            return None
        vault.kv_get = refused
        for create in (False, True):
            try:
                got = repr(vault.agent_token(create=create))
            except vault.VaultError as e:
                got = "raised" if "403" in str(e) else f"raised without the reason: {e}"
            check(f"create={create}: the reason is reported, not swallowed",
                  got, "raised")

        # And a vault that genuinely answers "no such secret" — a 404, which is
        # the one code kv_get does not record — still reports absent.
        def absent(name):
            vault._kv_errors.pop(name, None)
            return None
        vault.kv_get = absent
        check("a vault that answers 'no such secret' still reports absent",
              vault.agent_token(create=False), None)
    finally:
        vault.kv_get = real_get
        vault._kv_errors.pop(vault.TOKEN_SECRET, None)
        restore()


# -------------------------------------------------------------- dose identity
def _chem_fixture(rows):
    """A chemicals.csv in a fresh directory, plus a cfg naming it.

    POOLHOUND_DATA IS SET, not just the cfg. config.data_dir() lets the
    environment win over the config file — deliberately, so a container can be
    pointed at a share without a config edit — so a fixture that names a
    directory only in `cfg` writes to the operating install's data directory
    whenever that variable happens to be exported. Which is how these cases were
    first run, and they reported failures about a file they were not touching.

    Returns (directory, path, cfg, restore).
    """
    from . import chemicals as CH
    d = tempfile.mkdtemp()
    p = os.path.join(d, "chemicals.csv")
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CH.CHEM_COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in CH.CHEM_COLS})
    prev = os.environ.get("POOLHOUND_DATA")
    os.environ["POOLHOUND_DATA"] = d

    def restore():
        if prev is None:
            os.environ.pop("POOLHOUND_DATA", None)
        else:
            os.environ["POOLHOUND_DATA"] = prev
    return d, p, {"paths": {"data": d}, "pool": {"volume_gallons": 18400}}, restore


def t_two_doses_in_one_minute_stay_editable():
    """A minute is not an identity, and the form only records minutes.

    The dose form's time field is <input type=datetime-local>, so two doses in
    the same minute — or one double-click — wrote two rows with identical
    timestamps, and `ts` was the only thing edit and delete matched on. Measured
    before the fix: two POSTs one second apart, then 400 from BOTH
    /api/chemical/delete and /api/chemical/edit, permanently, while the page
    went on drawing Edit and Delete beside them. The dose log is the record the
    whole fitting premise rests on and it had become append-only by accident.

    Written against the two ROUTES rather than against find_dose alone: the
    editing half had a second bug of the same kind — it rewrote every row
    matching the timestamp, so one edit would have overwritten both.
    """
    from . import server, chemicals as CH
    from .selftest import in_deploy_tz
    print("\n  doses — two in one minute are two rows, each addressable")
    # THE FIXTURE'S OFFSET IS THE DEPLOYMENT'S OFFSET, and this case cannot be
    # written without one: edit_chemical() rewrites `ts` into the canonical
    # spelling, which is the HOST's offset, and the delete below then addresses
    # the row by the literal string above. On a UTC runner the edited row comes
    # back as +0000 and the delete finds nothing — the product behaving
    # correctly, in a zone the fixture had not said it was in.
    ts = "2026-09-15T09:30:00-0700"
    d, p, cfg, restore = _chem_fixture([
        {"ts": ts, "chemical": "acid", "amount": "32", "unit": "floz",
         "pct": "31.45", "note": "first", "by": "me"},
        {"ts": ts, "chemical": "acid", "amount": "16", "unit": "floz",
         "pct": "31.45", "note": "second", "by": "me"},
    ])
    try:
        with in_deploy_tz():
            _two_in_one_minute(server, CH, p, ts, cfg)
    finally:
        restore()


def _two_in_one_minute(server, CH, p, ts, cfg):
    # Addressed by timestamp, both rows match: refused, and the refusal now
    # carries the way out rather than just the word "refusing".
    res, err = server.delete_chemical({"ts": ts}, cfg, "me")
    check("a timestamp naming two rows is still refused", res, None)
    check("and the refusal names the ids so there is a way forward",
          bool(err) and "id" in err, True)

    rows = list(csv.DictReader(open(p)))
    ids = [r["id"] for r in rows]
    check("both rows now carry an id", all(ids), True)
    check("and the two ids differ", len(set(ids)), 2)

    # Edit ONE of them.
    first = next(r for r in rows if r["note"] == "first")
    res, err = server.edit_chemical({"id": first["id"], "amount": 11}, cfg, "me")
    check("an edit by id succeeds", err, None)
    after = list(csv.DictReader(open(p)))
    check("the named row changed",
          next(r["amount"] for r in after if r["id"] == first["id"]), "11")
    check("and the OTHER row in the same minute did not",
          sorted(r["amount"] for r in after), ["11", "16"])

    # Delete the other.
    second = next(r for r in after if r["note"] == "second")
    res, err = server.delete_chemical({"id": second["id"]}, cfg, "me")
    check("a delete by id succeeds", err, None)
    left = list(csv.DictReader(open(p)))
    check("one row is gone", len(left), 1)
    check("and it was the right one", left[0]["note"], "first")

    # A row that does not share its minute is still addressable the old way, so
    # a page that has not been re-rendered keeps working.
    res, err = server.delete_chemical({"ts": ts}, cfg, "me")
    check("a timestamp naming ONE row still works", err, None)
    check("nothing is left", len(list(csv.DictReader(open(p)))), 0)

    check("CHEM_COLS carries the id column once", CH.CHEM_COLS.count("id"), 1)


def t_ids_are_backfilled_not_invented_twice():
    """A row written by bin/chem, or before the column existed, gets one id.

    ensure_ids() runs inside the chemicals lock and the file is rewritten when
    it adds any, so the ids a refusal reports are the ids the next request will
    find. An identity invented for an error message and not written down would
    name something that does not exist.
    """
    from . import chemicals as CH
    print("\n  doses — an id is assigned once and then kept")
    rows = [{"ts": "a", "id": ""}, {"ts": "b"}, {"ts": "c", "id": "already"}]
    check("ensure_ids fills only the blanks", CH.ensure_ids(rows), 2)
    check("and leaves an existing one alone", rows[2]["id"], "already")
    first = [r["id"] for r in rows]
    check("a second pass adds none", CH.ensure_ids(rows), 0)
    check("and changes none", [r["id"] for r in rows], first)
    check("the ids are distinct", len({r["id"] for r in rows}), 3)


# ------------------------------------------------- corrections and coercion
def t_a_correction_is_held_to_the_readings_range():
    """The value that WINS was the one nothing checked.

    /api/reading refuses a pH of 99 — "outside 5-9.5, which is not a reading a
    pool test produces" — and the same figure through /api/lab-correction was
    accepted, stored and applied by the loaders to the row it names. Measured: a
    correction of 999999 on an alkalinity reading rendered a chart whose top
    tick read 1,199,987, and JSON true walked through float() and was stored as
    the string "True".
    """
    from . import server
    print("\n  corrections — held to the same range a hand-entered reading is")
    # Derived from the table the reading form uses, so a range added there is
    # covered here without this file being edited.
    for field, (lo, hi) in sorted(server.MANUAL_RANGE.items()):
        over = hi + max(abs(hi), 1)
        text, err = server.measure_value(field, over)
        check(f"{field} of {over:g} is refused", bool(err), True)
        mid = (lo + hi) / 2
        text, err = server.measure_value(field, mid)
        check(f"{field} of {mid:g} is accepted", (text, err), (f"{mid:g}", None))
    check("a boolean is not a reading",
          server.measure_value("ch", True)[0], None)
    check("nor is a word", server.measure_value("ch", "high")[0], None)
    check("nor is a nan", server.measure_value("ch", float("nan"))[0], None)
    # A column the range table says nothing about still has to be a number: a
    # text value there propagates into the arithmetic as a silent zero.
    check("an unranged measurement is still required to be a number",
          server.measure_value("tds", "lots")[0], None)
    check("and a number in one is kept as written",
          server.measure_value("tds", 4100)[0], "4100")


def t_a_write_handler_always_answers():
    """A closed socket is not an HTTP status, and the browser cannot read one.

    `(data.get("chemical") or "").strip()` handles None and nothing else, so a
    JSON body carrying a number where a string belongs raised AttributeError out
    of the handler: do_POST wrapped only LockTimeout and ValueError, the socket
    closed with no status line, and fetch() rejected with a bare network error —
    indistinguishable from the server being down. Measured: 11 of 14
    type-confusion probes got no status; after, 0 of 27, and none of them a 500.

    _s() and _num() are what turn a wrong TYPE into the ordinary wrong-VALUE
    refusal each route already knows how to give.
    """
    from . import server
    print("\n  writes — a wrong type is refused, not raised")
    check("a number becomes its text", server._s(5), "5")
    check("None becomes empty", server._s(None), "")
    check("a list becomes something no validator accepts",
          server._s(["acid"]).strip() in ("", "acid"), False)
    check("true is not a number", server._num(True), None)
    check("nor is a list", server._num([1]), None)
    check("a real number survives", server._num("31.45"), 31.45)

    # And the route says so rather than raising. No lock, no file: the point is
    # the return, not the write.
    d, p, cfg, restore = _chem_fixture([])
    for body, why in (({"chemical": 5, "amount": 1, "unit": "floz", "pct": 31},
                       "a number where a chemical belongs"),
                      ({"chemical": ["acid"], "amount": 1, "unit": "floz", "pct": 31},
                       "a list where a chemical belongs"),
                      ({"chemical": "acid", "amount": True, "unit": "floz", "pct": 31},
                       "a boolean where an amount belongs"),
                      ({"chemical": "acid", "amount": 1, "unit": "floz",
                        "pct": 31, "ts": 5}, "a number where a time belongs")):
        res, err = server.log_chemical(body, cfg, "me")
        check(f"{why} is refused with a message", (res is None, bool(err)),
              (True, True))
    restore()


def t_the_agent_token_can_be_rotated_without_an_outage():
    """With one slot there was no correct order to rotate in.

    docs/THREAT_MODEL.md's first open item was "the agent token — static, no
    expiry, no rotation runbook", and the missing runbook was the symptom. With
    a single accepted value, whichever end you write first the other is
    refused: write Key Vault first and the Pi presents the old token into a
    closed route; write the Pi first and the server refuses it. Either way the
    pool's only sampler is off the air for as long as the person takes, and the
    samples inside that gap do not exist anywhere else.

    So two are accepted, for a stated window. What this asserts is the set of
    refusals that make the second slot safe rather than a second permanent
    credential — which is the other half of the open item, "no expiry":

      * a slot with no deadline is REFUSED, because that is two live
        credentials wearing a rotation's clothes;
      * a deadline that cannot be parsed is refused, not treated as absent —
        "I cannot tell when this expires" and "this does not expire" are the
        same mistake kv_get() was taught not to make about "nothing there";
      * a deadline that has passed is refused, which is the expiry;
      * a second slot holding the SAME value as the first is refused, because
        copying both ends is a no-op rotation that reads as a finished one.
    """
    import datetime as dt
    vault, d, restore = _isolated_vault()
    print("\n  vault — the shared secret can be replaced without a gap")
    try:
        cur = os.path.join(d, "agent.token")
        with open(cur, "w") as f:
            f.write("the-current-secret\n")
        os.chmod(cur, 0o600)
        vault.TOKEN_FILE = cur
        vault.TOKEN_FILE_IS_DEFAULT = False
        prev = vault.previous_token_file()
        check("the second slot sits beside the first", prev, cur + ".previous")
        check("the current token still reads",
              vault.agent_token(create=False), "the-current-secret")

        def write_previous(text):
            with open(prev, "w") as f:
                f.write(text)
            os.chmod(prev, 0o600)
            # The lookup is cached for a minute, which the runbook says; a
            # case that did not clear it would be testing the cache.
            vault._prev_cache[0] = 0.0

        future = (dt.datetime.now(dt.timezone.utc)
                  + dt.timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        past = (dt.datetime.now(dt.timezone.utc)
                - dt.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

        check("with no second slot there is no rotation in progress",
              vault.agent_token_previous(), None)

        write_previous(f"the-outgoing-secret {future}\n")
        got = vault.agent_token_previous()
        check("inside the window the outgoing token is accepted",
              got[0] if got else None, "the-outgoing-secret")
        check("and it carries the deadline it was given",
              got[1].strftime("%Y-%m-%dT%H:%M:%SZ") if got else None, future)
        check("the fingerprint is a hash, not the secret",
              vault.agent_token_previous_fingerprint() != "the-outgoing-secret"
              and len(vault.agent_token_previous_fingerprint() or ""), 12)

        write_previous("the-outgoing-secret\n")
        check("A SLOT WITH NO DEADLINE IS REFUSED",
              vault.agent_token_previous(), None)

        write_previous("the-outgoing-secret soon\n")
        check("an unreadable deadline is refused, not read as absent",
              vault.agent_token_previous(), None)

        write_previous(f"the-outgoing-secret {past}\n")
        check("a deadline that has passed is the expiry",
              vault.agent_token_previous(), None)

        write_previous(f"the-current-secret {future}\n")
        check("and both slots holding one value is a copy, not a rotation",
              vault.agent_token_previous(), None)
    finally:
        restore()


def t_a_setting_cannot_rewrite_another_key():
    """A settings field was a TOML injection, and config.toml holds the policy.

    MEASURED, not theorised. `toml_value()` escaped the quote and nothing else,
    so a value ending in a backslash closed the string early — the backslash it
    emitted paired with the one being escaped and the following quote
    terminated. A newline then started a new line in the file and a trailing
    `#` commented out the closing quote. Written out:

        ai_model = "a\\\\"
        evil_injected = 1
        #"

    parses cleanly and lands `evil_injected` in the config.

    WHY IT MATTERS MORE THAN "/api/settings NEEDS ADMIN". config.toml holds
    `[access]`, which this product says is "not editable through the UI on
    purpose", and `[paths]`, which decides where an irreplaceable history
    lives. A text field able to write arbitrary keys is a route around both.

    Three layers, and the third is the one that will hold when somebody finds
    the next delimiter: escape properly, refuse control characters, and
    RE-PARSE what is about to be written — refusing it if any key the save was
    not asked to touch has changed.
    """
    import tomllib
    from . import server
    print("\n  settings — a value cannot reach a key it was not given")

    # The escaping, against the exact payload that worked.
    evil = 'a\\" \nevil_injected = 1\n#'
    doc = "[ai]\nmodel = " + server.toml_value(evil) + "\n"
    got = tomllib.loads(doc)
    check("the demonstrated payload round-trips as one value",
          got["ai"]["model"], evil)
    check("and injects no second key",
          sorted(k for k in got["ai"] if k != "model"), [])

    for label, raw in (("a trailing backslash", "C:\\dir\\"),
                       ("a bare quote", 'say "hi"'),
                       ("a newline", "a\nb"),
                       ("a tab", "a\tb")):
        d = tomllib.loads("[ai]\nmodel = " + server.toml_value(raw) + "\n")
        check(f"{label} survives exactly", d["ai"]["model"], raw)
        check(f"{label} adds no key",
              sorted(k for k in d["ai"] if k != "model"), [])

    # A control character cannot be escaped into a basic string at all.
    refused = ""
    try:
        server.toml_value("a\x00b")
    except ValueError as e:
        refused = str(e)
    check("a control character is refused rather than written",
          "control character" in refused, True)

    # THE GENERAL GUARD: a save that would touch anything else is refused on
    # disk, whatever got it past the escaping.
    import tempfile
    d = tempfile.mkdtemp()
    path = os.path.join(d, "config.toml")
    with open(path, "w") as f:
        f.write('[ai]\nmodel = "m"\n\n[access]\nowner = "admin"\n')
    server.edit_toml(path, {("ai", "model"): "gpt-4o-mini"})
    after = tomllib.loads(open(path).read())
    check("an ordinary save still lands", after["ai"]["model"], "gpt-4o-mini")
    check("and leaves the policy alone", after["access"]["owner"], "admin")

    # The two fields whose value leaves this household.
    check("a base_url with no scheme is invalid", server._url("api.openai.com"), None)
    check("and a real one is kept",
          server._url("https://api.openai.com/v1"), "https://api.openai.com/v1")
    check("a model name with a space is invalid", server._token("a b", 128), None)
