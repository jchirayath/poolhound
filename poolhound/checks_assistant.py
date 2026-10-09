"""The assistant: what it sends, what it refuses, and who may ask."""

from .selftest import check, skipped


def t_the_context_carries_pool_chemistry_and_nothing_else():
    """Every question sends this to a third party, so what is in it is a
    security decision and not a formatting one.

    context() builds a FIXED set of fields. Nothing iterates over whatever a
    row happens to contain, which is the difference between a summary and a
    leak — and it is why this case can be specific about what must not appear.
    """
    from . import assistant, render
    print("\n  assistant — what leaves the house")

    ctx = assistant.context()
    check("it says how big the pool is", "POOL:" in ctx, True)
    check("and lists the current readings", "CURRENT READINGS" in ctx, True)
    check("and the doses", "DOSES LOGGED" in ctx, True)
    check("and tells the model not to recompute them",
          "Do not recompute" in ctx, True)

    # WHAT MUST NOT BE IN IT. The house, the logins, the identities and the
    # controller are all reachable from render's loaders; none of them is a
    # pool-chemistry fact and none is anybody's business at a provider.
    cfg = render.CFG
    site = (cfg.get("site") or {})
    forbidden = {
        "the site host": site.get("host"),
        "the Pi's address": site.get("pi_host"),
        "the Pi's serial": site.get("serial"),
        "a credential path": ((cfg.get("waterguru") or {}).get("credentials")),
    }
    present = {why: n for why, n in forbidden.items() if n}
    if not present:
        # An install that configures none of these — the shipped example
        # config is one — has nothing here that could leak, so asserting it
        # does not leak proves nothing. Said out loud rather than passing
        # four times over empty strings, which is what the deploy image's
        # build gate was doing.
        skipped("the context against this install's own private facts",
                "this config names no host, serial or credential path")
    for why, needle in present.items():
        check(f"it does not carry {why}", str(needle) in ctx, False)

    for word in ("password", "api key", "token", "bearer", "@"):
        check(f"nor anything reading as {word!r}", word in ctx.lower(), False)


def t_the_targets_it_sends_are_this_pools():
    """The free-chlorine target scales with stabiliser, and the model is told so.

    context() called targets(cfg), handing the CONFIG to the parameter that
    takes lab results. No CYA is ever in a config, so every question went out
    with the generic 2-4 ppm while the page showed this pool's own band. A lab
    reporting CYA 80 has to come out as 4-8.
    """
    from . import assistant, render
    print("\n  assistant — the targets are this pool's")
    real = render.rows
    fixture = {
        "lab.csv": [{"measured": "2026-09-30T12:00:00", "cya": "80"}],
        "readings.csv": [{"measured": "2026-10-01T19:30:00", "free_cl": "6.0",
                          "ph": "7.5"}],
    }
    render.rows = lambda name, *a, **k: [dict(r) for r in fixture.get(name, [])]
    try:
        ctx = assistant.context()
    finally:
        render.rows = real
    line = next((l for l in ctx.splitlines() if "Free Chlorine" in l
                 or "free chlorine" in l.lower()), "")
    check("free chlorine is sent with a target", "target" in line, True)
    check("and it is the CYA-80 band, not the generic one",
          "target 4-8" in line, True)


def t_asking_refuses_before_it_spends_anything():
    """Every refusal has to happen before the request goes out — a guard that
    fires after the call has already cost the money it was protecting."""
    from . import assistant
    print("\n  assistant — what it refuses")

    ans, err = assistant.ask("")
    check("an empty question is refused", (ans, bool(err)), (None, True))

    ans, err = assistant.ask("x" * 2100)
    check("and one longer than it will send", "2000 characters" in (err or ""), True)

    # No key stored in the test environment, so this is the real path an
    # unconfigured install takes.
    ans, err = assistant.ask("is my alkalinity low?")
    check("with no key it says so rather than failing obscurely",
          "no API key" in (err or ""), True)
    check("and returns no answer", ans, None)

    # The budget is a ceiling on spending, not a rate limit against abuse.
    check("there is a ceiling on questions per hour",
          assistant.ASK_LIMIT > 0 and assistant.ASK_WINDOW_S >= 3600, True)
    real = list(assistant._asked)
    try:
        import datetime as dt
        now = dt.datetime.now().timestamp()
        assistant._asked[:] = [now] * assistant.ASK_LIMIT
        check("and it is enforced", assistant.budget_left(), 0)
        ans, err = assistant.ask("anything")
        check("a question over the ceiling is refused",
              "ceiling" in (err or ""), True)
    finally:
        assistant._asked[:] = real


def t_the_tab_is_private_and_the_route_is_gated():
    """The answer is about this household's pool and the question goes to
    somebody else's server. Neither belongs on the public page."""
    from . import access, render
    print("\n  assistant — who may ask")

    check("the tab is classified private", "ask" in render.PRIVATE_TABS, True)
    check("and not public", "ask" in render.PUBLIC_TABS, False)
    check("the route needs more than a reader",
          access.NEEDS.get("/api/ask"), "operate")
    check("and it names the control it gates",
          "ask-form" in (access.ROLE_UI.get("/api/ask") or ""), True)

    # The public build must carry neither the panel nor a call to the route.
    import os
    from . import config
    p = os.path.join(config.site_dir(render.CFG), "index.html")
    if os.path.exists(p):
        pub = open(p).read()
        check("the public page has no Ask panel", 'id="tab-ask"' in pub, False)
        check("and never calls the route", "/api/ask" in pub, False)


def t_the_provider_is_configured_not_compiled_in():
    """"Bound to any AI" is a base URL, not a branch per vendor. One request
    shape, so pointing this at a model on your own network is a setting."""
    from . import assistant, server, vault
    print("\n  assistant — any OpenAI-compatible endpoint")

    check("the endpoint is a setting", "base_url" in assistant.DEFAULTS, True)
    check("and so is the model", "model" in assistant.DEFAULTS, True)
    check("both are settable through the form",
          all(k in server.SETTABLE for k in ("ai_base_url", "ai_model")), True)
    # THE KEY IS NOT. A secret in the settings map is a secret in config.toml,
    # which the rest of the product treats as a file it may print.
    check("the key is NOT a setting",
          any("key" in k for k in server.SETTABLE), False)
    check("it is a vault service, like every other credential",
          assistant.SERVICE in vault.SERVICES, True)
    check("with no legacy plaintext path invented for it",
          vault.SERVICES[assistant.SERVICE].get("legacy"), None)


def t_a_provider_error_names_what_the_reader_can_change():
    """The advice for one HTTP status must be the advice for THAT status.

    MEASURED, on the live deployment. The OpenAI account's credit ran out,
    the provider answered 429 with `insufficient_quota`, and the page said
    "the provider answered 429. Check the key and the model name." Both were
    correct -- a 164-character sk-proj key read from the vault, and
    gpt-4o-mini, which is a real model -- and neither can produce a 429: a bad
    key is 401 and a bad model is 404. So the one sentence this function
    replaced was wrong for the code it was most likely to be shown for, and
    pointed at the only two things that could not have caused it.

    Pure, so it belongs here rather than in a browser: wrong advice renders
    exactly like right advice, and no screenshot distinguishes them.
    """
    from . import assistant as A
    print("\n  assistant — an error says what the reader can change")

    quota = A.http_error(429, "insufficient_quota")
    check("429 on quota says the account is out of credit",
          "out of credit" in quota, True)
    check("and says it is not a setting here",
          "not a setting here" in quota, True)
    check("and offers the escape this product actually has",
          "base_url" in quota, True)
    check("and does NOT send them to check the key",
          "Check the key" in quota, False)

    # THE OTHER 429, which needs a minute rather than a card.
    rate = A.http_error(429, "rate_limit_exceeded")
    check("429 on rate says it clears on its own", "clears on its own" in rate, True)
    check("but still mentions credit, because providers use 429 for both",
          "out of credit" in rate, True)
    check("and the two 429s do not give the same advice", quota == rate, False)

    # PRECEDENCE. `code and "quota" in code or code == "..."` parses as
    # `(code and "quota" in code) or (...)`, and this picks between two
    # unrelated remedies. A None code must not raise and must not claim credit.
    nocode = A.http_error(429, None)
    check("a 429 with no identifier still answers", bool(nocode), True)
    check("and does not assert which 429 it is",
          "out of credit (" in nocode, False)

    check("401 points at the key", "key was refused" in A.http_error(401), True)
    check("404 points at the model",
          "no such model" in A.http_error(404, "model_not_found"), True)
    check("a 500 says it is their fault",
          "their end" in A.http_error(503), True)
    # An unknown status must still produce a sentence rather than a KeyError.
    check("an unrecognised status still answers", bool(A.http_error(418)), True)

    # SENTENCE CASE, because the advice follows a full stop. Printed lowercase
    # for every mapped status until this was read back.
    for st in (400, 401, 403, 404, 413):
        msg = A.http_error(st, "x")
        tail = msg.split("). ", 1)[-1]
        check(f"{st}'s advice starts with a capital",
              tail[:1].isupper(), True)


def t_a_provider_error_never_carries_the_body():
    """The provider's identifier may be shown; its message may not.

    The body can quote back the request it is complaining about, which is this
    pool's context -- volume, readings, doses -- and it would land in a page
    and in a log. So `error.code` and `error.type` are read and everything
    else is dropped, on an allowlist of SHAPE: a short token with no spaces.
    A provider that puts prose in that field, or echoes the request into it,
    gets nothing through.
    """
    import io, json, urllib.error
    from . import assistant as A
    print("\n  assistant — a provider's error body stays out of the page")

    def err(payload):
        raw = json.dumps(payload).encode()
        return urllib.error.HTTPError("u", 429, "Too Many Requests", {},
                                      io.BytesIO(raw))

    check("a short identifier is read",
          A._provider_code(err({"error": {"code": "insufficient_quota"}})),
          "insufficient_quota")
    check("type is read when code is absent",
          A._provider_code(err({"error": {"type": "invalid_request_error"}})),
          "invalid_request_error")
    # THE THREE WAYS THE CONTEXT COULD HAVE COME BACK.
    check("prose in the code field is dropped",
          A._provider_code(err({"error": {"code": "you asked about a pool of "
                                                  "18500 gallons"}})), None)
    check("an over-long token is dropped",
          A._provider_code(err({"error": {"code": "x" * 200}})), None)
    check("the message field is never read",
          A._provider_code(err({"error": {"message": "pool volume 18500"}})), None)
    check("a body that is not JSON is survivable",
          A._provider_code(urllib.error.HTTPError(
              "u", 429, "x", {}, io.BytesIO(b"<html>nope"))), None)
    check("and neither is a bare list",
          A._provider_code(err(["insufficient_quota"])), None)

    # AND THE WHOLE SENTENCE CANNOT CARRY IT EITHER, which is the property that
    # actually matters -- the code above is only one route in.
    leak = A.http_error(429, A._provider_code(
        err({"error": {"code": "quota", "message": "18500 gallons, pH 7.4"}})))
    check("the rendered advice holds no part of the message",
          "18500" in leak or "7.4" in leak, False)


def t_the_spending_ceiling_survives_a_restart():
    """A ceiling a restart removes is not a ceiling.

    docs/THREAT_MODEL.md carried this as abuse case 9, "partly mitigated: the
    budget is per-process", and the gap is wider than that phrasing suggests:
    `docker compose up -d --force-recreate` is in the runbook, the container
    restarts on every new image, and each of those handed back the full
    twenty questions however many had just been asked. The ceiling exists
    because each question costs money at somebody's provider.

    Three properties, and the third is the one the Pi's guard taught:

      * what one process spent, the next one sees;
      * a stamp older than the window has rolled off, so the budget clears;
      * a stamp in the FUTURE is dropped rather than believed. Wall clock is
        the only clock that can read across a restart, which means a clock
        that jumped forward would otherwise hold the budget down for an hour
        of real time that has already gone by.
    """
    import datetime as dt, os, tempfile
    from . import assistant
    print("\n  assistant — the spending ceiling outlives the process")

    real_path, real_asked, real_loaded = (
        assistant._state_path, list(assistant._asked), assistant._LOADED)
    tmp = tempfile.mkdtemp()
    assistant._state_path = lambda: os.path.join(tmp, "ask-budget.json")

    def as_a_new_process():
        """Forget everything this process knows, the way a restart does."""
        assistant._asked[:] = []
        assistant._LOADED = False

    try:
        as_a_new_process()
        before = assistant.budget_left()
        check("a fresh install starts with the whole budget",
              before, assistant.ASK_LIMIT)
        assistant._spend()
        assistant._spend()
        check("two questions leave two fewer",
              assistant.budget_left(), assistant.ASK_LIMIT - 2)
        check("and something was written down",
              os.path.exists(assistant._state_path()), True)

        as_a_new_process()
        check("THE NEXT PROCESS SEES THEM",
              assistant.budget_left(), assistant.ASK_LIMIT - 2)

        # The window rolls forward.
        stale = dt.datetime.now().timestamp() - assistant.ASK_WINDOW_S - 60
        assistant._asked[:] = [stale] * assistant.ASK_LIMIT
        assistant._save()
        as_a_new_process()
        check("a window that has passed clears the budget",
              assistant.budget_left(), assistant.ASK_LIMIT)

        # A clock that jumped. Written by hand rather than through _spend,
        # because _spend cannot produce one — which is the point: the stamp
        # arrives from the file, written by a process whose clock was wrong.
        import json
        with open(assistant._state_path(), "w", encoding="utf-8") as f:
            json.dump({"asked": [dt.datetime.now().timestamp() + 86400]
                       * assistant.ASK_LIMIT}, f)
        as_a_new_process()
        check("a stamp from the future is dropped, not believed",
              assistant.budget_left(), assistant.ASK_LIMIT)

        # Unreadable state FAILS OPEN, and that direction is deliberate: the
        # cost is one extra window at worst, against a tab reporting a limit
        # nobody can clear.
        with open(assistant._state_path(), "w", encoding="utf-8") as f:
            f.write("{not json at all")
        as_a_new_process()
        check("and an unreadable record does not lock the tab",
              assistant.budget_left(), assistant.ASK_LIMIT)
    finally:
        assistant._state_path = real_path
        assistant._asked[:] = real_asked
        assistant._LOADED = real_loaded
