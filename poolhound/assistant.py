#!/usr/bin/env python3
"""Ask a question about this pool, answered against this pool's own record.

WHAT THIS IS FOR
  "I add a quarter gallon of muriatic acid every week — is that normal, or
  should I raise my alkalinity?" That question has an answer in the data: how
  much acid, how often, what the alkalinity actually is, what this pool's pH
  does after a dose. A general model knows pool chemistry in the abstract and
  knows nothing about THIS pool. A language model handed the pool's own numbers
  can explain them, which is the half it is good at.

THE MODEL IS NOT ASKED TO DO ARITHMETIC
  It is handed figures this product has already computed — the current
  readings with their source and age, the targets for a salt pool, the volume,
  the recent doses and their cadence, and the fitted acid response where there
  is one. That is the same rule the rest of the product follows: the dose
  arithmetic lives in chemicals.py and nothing else reimplements it. A model
  asked to multiply gallons by a coefficient will sometimes get it wrong and
  will always sound equally certain.

  So the prompt says, in as many words, that the numbers are given and must not
  be recomputed, and that anything the context does not contain is not known.

ONE CLIENT, ANY PROVIDER
  Everything speaks the OpenAI chat-completions shape now — OpenAI itself,
  OpenRouter, Together, Azure OpenAI, and Ollama and LM Studio locally. So
  there is one request format here and a base URL in the config, which means a
  local model is a configuration change rather than a second client. urllib,
  because the whole product is stdlib plus six pinned packages and an SDK for
  one POST is not worth the image.

WHAT LEAVES THE HOUSE
  A few kilobytes of pool chemistry: readings, targets, volume, doses. No
  address, no credential, no identity, no equipment state — context() builds a
  fixed set of fields rather than serialising whatever happens to be to hand,
  because "send the summary" is the kind of instruction that quietly grows.
  Every call is a third party receiving that, which is why it is off until
  somebody configures it and why the panel says so before the first question.
"""

import datetime as dt
import json
import os
import threading
import urllib.error
import urllib.request

from . import config, vault

# Where to send the question, and as what. base_url is everything up to the
# path, so a provider is a one-line change and a local model is the same line.
DEFAULTS = {
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",
    "timeout_s": 45,
    "max_tokens": 700,
}

# The one service name the vault and the settings form both use.
SERVICE = "ai"

# How many questions may be asked in an hour. Not a rate limit against abuse —
# every write already carries one — but a BUDGET: each of these costs money at
# somebody's provider, and a page that can spend without a ceiling is a page
# that will, the first time a script or a stuck key holds a button down.
ASK_LIMIT, ASK_WINDOW_S = 20, 3600

# WHERE THE SPENDING IS WRITTEN DOWN, because for as long as this was a bare
# list it was A CEILING A RESTART REMOVED. docs/THREAT_MODEL.md carried it as
# an open item — abuse case 9, "partly mitigated: the budget is per-process" —
# and the gap is wider than it sounds: `docker compose up -d --force-recreate`
# is in the runbook, the container restarts on a new image, and each of those
# hands back the full twenty questions however many were just asked.
#
# This is the same problem the Pi's guard had, one layer up, and it takes the
# same shape of answer: wall clock rather than monotonic, because monotonic
# restarts with the process and the process restarting is the event this has to
# read across. Which also means a timestamp in the FUTURE is dropped rather
# than believed — a clock that jumped forward would otherwise hold the budget
# down for an hour of real time that has already passed.
_asked = []
_LOADED = False
_LOCK = threading.Lock()
STATE = "ask-budget.json"


def _state_path():
    return os.path.join(config.data_dir(), STATE)


def _load():
    """The timestamps a previous process wrote, once per process.

    NEVER RAISES, and that direction is deliberate: an unreadable budget file
    must not be the thing that stops somebody asking about their own pool. The
    cost of failing open here is bounded by the ceiling itself — at worst one
    extra window's worth — while the cost of failing closed is a tab that
    reports a spending limit nobody can clear.
    """
    global _LOADED
    _LOADED = True
    # NOT OVER THE TOP OF WHAT THIS PROCESS ALREADY HOLDS. A lazy load that
    # replaces the list unconditionally is a load that can undo a spend: the
    # first caller of budget_left() after a _spend() would read the file as the
    # truth and drop the question just recorded. It is also what a test setting
    # the list by hand means — and the case asserting the ceiling is enforced
    # passed only because something else had happened to load first.
    if _asked:
        return
    try:
        with open(_state_path(), encoding="utf-8") as f:
            saved = json.load(f)
        now = dt.datetime.now().timestamp()
        _asked[:] = sorted(float(t) for t in saved.get("asked", [])
                           if isinstance(t, (int, float))
                           and 0 < float(t) <= now)
    except Exception:                                     # noqa: BLE001
        pass


def _save():
    """Atomic, and never raises — the question has already been asked.

    Atomic because a half-written file read by the next start is the one way
    this could be worse than holding nothing at all.
    """
    try:
        p = _state_path()
        tmp = f"{p}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"asked": _asked}, f)
        os.replace(tmp, p)
    except Exception:                                     # noqa: BLE001
        pass


def _spend():
    """Record one question, in memory and on disk."""
    with _LOCK:
        if not _LOADED:
            _load()
        _asked.append(dt.datetime.now().timestamp())
        _save()


def settings(cfg=None):
    """The configured endpoint, with the defaults filled in."""
    ai = dict(DEFAULTS)
    ai.update((cfg or config.load()).get("ai") or {})
    return ai


def configured(cfg=None):
    """Is there both a key and an endpoint? Reported, never the key itself."""
    try:
        u, p, _src = vault.credentials_for(SERVICE)
    except Exception:
        u, p = None, None
    return bool(p) and bool(settings(cfg).get("base_url"))


def budget_left():
    """How many questions are left in this window, across restarts.

    Reads the previous process's record the first time it is asked, which is
    also what /api/health reports — so the number on the tab is the number the
    route will enforce rather than one that only this process believes.
    """
    with _LOCK:
        if not _LOADED:
            _load()
        now = dt.datetime.now().timestamp()
        while _asked and now - _asked[0] > ASK_WINDOW_S:
            _asked.pop(0)
        return max(ASK_LIMIT - len(_asked), 0)


# --------------------------------------------------------------- the context

def _fmt(v, nd=2):
    """A number without a tail of zeros — and without losing the ones that are
    part of it. `"18400".rstrip("0")` is "184", which is what this did: it told
    the model the pool held a hundred and eighty-four gallons."""
    if v is None:
        return "unknown"
    t = f"{v:.{nd}f}"
    return t.rstrip("0").rstrip(".") if "." in t else t


def context(build=None):
    """What the model is told about this pool, as plain text.

    A FIXED SET OF FIELDS, not a dump. Every line here was chosen; nothing
    iterates over whatever a row happens to contain. That is the difference
    between a summary and a leak, and it is why adding a field is an edit here
    rather than a side effect somewhere else.

    Built from render's loaders, so the figures are the ones the page shows.
    A second path to "what is the alkalinity" would eventually disagree with
    the first, which is the defect this product keeps finding.
    """
    from . import render

    cfg = render.CFG
    out = []
    a = out.append

    gal = config.volume(cfg) if hasattr(config, "volume") else None
    a(f"POOL: {_fmt(gal, 0) if gal else 'volume not set'} gallons"
      f"{'' if gal else ' — every dose figure depends on this and it is unset'}"
      f", salt cell, plaster.")

    # Current readings, each with where it came from and how old it is — the
    # two things that decide how much weight an answer may put on a number.
    lab = render.best_lab((render.newest(render.rows("lab.csv")), "WaterGuru lab"),
                          (render.newest(render.rows("leslies.csv")), "Leslie's"),
                          (render.newest(render.rows("manual.csv")), "By hand"))
    daily = render.best_lab(
        (render.newest(render.rows("readings.csv")), "WaterGuru pod"),
        (render.newest(render.rows("lab.csv")), "WaterGuru lab"),
        (render.newest(render.rows("leslies.csv")), "Leslie's"),
        (render.newest(render.rows("manual.csv")), "By hand"),
        measures=render.DAILY_MEASURES)
    # The same call the page makes. This was targets(cfg), which handed the
    # CONFIG to a parameter that expects lab results: no CYA was ever found in
    # it, so the free-chlorine target sent to the model was the generic 2-4 ppm
    # while the page beside it scaled the target to this pool's stabiliser --
    # and the sanitiser and Leslie's own salt range were never passed at all.
    tg = render.targets(
        lab, (cfg.get("pool") or {}).get("sanitiser"),
        (render.newest(render.rows("leslies.csv")) or {}).get("sanitizer", ""))

    a("")
    a("CURRENT READINGS (value, source, when measured):")
    for m in render.DAILY_MEASURES + render.LAB_MEASURES:
        e = (daily if m in render.DAILY_MEASURES else lab).get(m) or {}
        v = e.get("value")
        if v is None:
            continue
        band = tg.get(m)
        aim = (f", target {_fmt(band[0])}-{_fmt(band[2])}" if band else "")
        a(f"  {render.MEASURE_NAMES.get(m, m)}: {_fmt(v)} "
          f"(from {e.get('source', 'unknown')}, {render.ago(e.get('measured'))}"
          f"{aim})")

    # The doses, and the CADENCE — which is the actual subject of "am I adding
    # this much acid too often". Given as the record plus the interval, not as
    # a verdict: the verdict is what is being asked for.
    # `ts`, which is chemicals.CHEM_COLS's own name for it. `at` is the RUN
    # log's column, and reading it here found every dose to be undated and so
    # reported a pool that had never had anything poured into it.
    doses = [r for r in render.rows("chemicals.csv") if r.get("ts")]
    doses.sort(key=lambda r: r.get("ts", ""))
    recent = doses[-12:]
    a("")
    a(f"DOSES LOGGED ({len(doses)} in total, most recent last):")
    for r in recent:
        a(f"  {(r.get('ts') or '')[:10]}  {r.get('chemical', '?')}  "
          f"{r.get('amount', '?')} {r.get('unit', '')}"
          f"{' at ' + r.get('pct') + '%' if r.get('pct') else ''}")
    if not recent:
        a("  none logged")

    for chem in ("acid",):
        sames = [r for r in doses if (r.get("chemical") or "") == chem]
        if len(sames) >= 2:
            d0, d1 = render.when(sames[0]["ts"]), render.when(sames[-1]["ts"])
            if d0 and d1 and d1 > d0:
                days = (d1 - d0).days / max(len(sames) - 1, 1)
                a(f"  {chem}: {len(sames)} doses, about one every "
                  f"{_fmt(days, 1)} days")

    # What the product itself already concluded, so the model is arguing with
    # the page rather than beside it. This called chemicals.acid_fit(), which
    # never existed, behind a hasattr() that kept the absence quiet -- so the
    # section was promised here and never once sent. It is the Chemicals tab's
    # upkeep table now, computed by the same call, so the two cannot differ.
    try:
        import datetime as _dt
        from . import upkeep
        samples = render.rows("samples.csv")
        readings = render.rows("readings.csv")
        est = upkeep.estimate(
            render.by_day(samples, doses, readings), samples, doses, readings,
            gal, render.current_ta(),
            float((cfg.get("pool") or {}).get("acid_pct", 31.45)),
            (tg.get("cya") or (None,) * 3)[1], _dt.datetime.now(),
            render.when, render.num,
            prices={k: config.chemical_price(k, cfg)
                    for k in config.PRICE_SETTINGS})
    except Exception:
        est = None
    if est:
        a("")
        a("UPKEEP, ESTIMATED FROM THIS POOL'S RECORD (per week, per year):")
        out.extend(upkeep.context_lines(est))

    a("")
    a("NOTE: these figures are computed by poolhound from its own record. "
      "Do not recompute them. Anything not listed here is not known.")
    return "\n".join(out)


SYSTEM = (
    "You are answering questions about one specific swimming pool, using only "
    "the figures supplied to you about it.\n"
    "- The numbers given are already computed. Do not recalculate doses or "
    "volumes; quote what you are given.\n"
    "- If the answer depends on something not in the context, say which "
    "measurement is missing rather than assuming a typical value.\n"
    "- This pool is on a salt chlorine generator, which drives pH up "
    "continuously, so regular acid is expected rather than a fault.\n"
    "- Two labs disagreeing is data, not error. Never average them and do not "
    "call either wrong.\n"
    "- Be brief and concrete. Give the reasoning, then the recommendation. "
    "Say plainly when the honest answer is that the record is too thin."
)


# WHAT EACH STATUS ACTUALLY MEANS.
#
# This was one sentence for every code: "the provider answered N. Check the key
# and the model name." MEASURED: the account's credit ran out, OpenAI answered
# 429 with `insufficient_quota`, and the page sent somebody to check two things
# that were both correct -- a 164-character `sk-proj` key read from the vault,
# and `gpt-4o-mini`, which is a real model. Neither can produce a 429. A wrong
# key is 401 and a wrong model is 404, so the one sentence was wrong for the
# code it was most likely to be shown for, and pointed away from the only
# thing that would have fixed it.
#
# The advice names what the reader can change, and says when there is nothing
# here to change: a billing balance at somebody else's company is not a
# setting on this page, and leaving that unsaid is what turns a five-minute
# problem into a search through Settings.
_HTTP_ADVICE = {
    400: "the provider rejected the request itself. If `base_url` points at a "
         "local or third-party model, it may not accept every field this sends.",
    401: "the stored key was refused. Replace it on Settings, under Logins.",
    403: "the key is valid but not allowed to use this model or endpoint.",
    404: "there is no such model at that endpoint. Check the model name and "
         "`base_url` on Settings.",
    413: "the request was too large for this provider. Lower `max_tokens`.",
}


def http_error(status, code=None):
    """One sentence a reader can act on, for one HTTP status.

    Pure, so it is checked in bin/selftest rather than by driving a page: the
    defect here is wrong ADVICE, which looks exactly like right advice.
    """
    if status == 429:
        # TWO VERY DIFFERENT 429s, and only the provider's own identifier tells
        # them apart. One is "you have spent your money" and needs a card; the
        # other is "you are going too fast" and needs a minute. Advising either
        # one for both is how this message came to be useless.
        # EXPLICIT, because `a and b in a or c` parses as `(a and b in a) or c`
        # and this decides which of two unrelated remedies a reader is sent to.
        out_of_credit = bool(code) and ("quota" in code or "credit" in code or
                                       "billing" in code)
        if out_of_credit:
            return ("the provider says this account is out of credit "
                    f"({code}). That is billing at the provider, not a setting "
                    "here — add credit, or point `base_url` at a model you run "
                    "yourself, which is a one-line change on Settings.")
        return ("the provider is rate-limiting this key"
                + (f" ({code})" if code else "")
                + ". Too many requests too close together; it clears on its "
                  "own. If it persists, the account may be out of credit — "
                  "providers answer 429 for both.")
    if status in _HTTP_ADVICE:
        advice = _HTTP_ADVICE[status]
        return (f"the provider answered {status}"
                + (f" ({code})" if code else "") + ". "
                # Raised, because it follows a full stop. Not .capitalize(),
                # which lowercases the rest and has already turned a vendor's
                # name into "Waterguru" once in this codebase.
                + advice[:1].upper() + advice[1:])
    if 500 <= status <= 599:
        return (f"the provider answered {status}"
                + (f" ({code})" if code else "")
                + " — that is a fault at their end, not a setting here. "
                  "Trying again shortly is the whole remedy.")
    return (f"the provider answered {status}"
            + (f" ({code})" if code else "")
            + ". Check the key and the model name on Settings.")


# How long a provider's own error identifier may be, and what it may contain.
#
# THE BODY IS STILL NOT ECHOED BACK -- that rule is why this is an allowlist on
# SHAPE rather than a read of `error.message`. The body can quote the request
# it is complaining about, which is this pool's context, and it would land in a
# page and a log. `error.code` and `error.type` are short machine identifiers
# ("insufficient_quota", "model_not_found"); anything with a space in it, or
# longer than this, is prose and is dropped rather than shown.
_CODE_MAX = 48


def _provider_code(e):
    """The provider's short error identifier, or None. Never its message."""
    try:
        err = (json.loads(e.read().decode("utf-8", "replace")).get("error") or {})
    except Exception:                                        # noqa: BLE001
        return None
    for k in ("code", "type"):
        v = err.get(k)
        if isinstance(v, str) and v and " " not in v and len(v) <= _CODE_MAX:
            return v
    return None


def ask(question, cfg=None, timeout=None):
    """Put one question to the configured model. Returns (answer, error).

    Never raises. A failure here is a panel that says it could not reach the
    provider, which is a page that still works — and the alternative is a 500
    on a route the rest of the product has no reason to care about.
    """
    q = (question or "").strip()
    if not q:
        return None, "ask something first"
    if len(q) > 2000:
        return None, "that question is longer than this will send (2000 characters)"
    if not budget_left():
        return None, (f"that is {ASK_LIMIT} questions in an hour, which is the "
                      f"ceiling this install sets on what it will spend. "
                      f"It clears as the hour rolls forward.")

    cfg = cfg or config.load()
    ai = settings(cfg)
    try:
        _u, key, _src = vault.credentials_for(SERVICE)
    except Exception:
        key = None
    if not key:
        return None, ("no API key is stored for the assistant — add one on "
                      "Settings, under Logins.")

    body = json.dumps({
        "model": ai.get("model") or DEFAULTS["model"],
        "max_tokens": int(ai.get("max_tokens") or DEFAULTS["max_tokens"]),
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "system", "content": "THIS POOL:\n" + context()},
            {"role": "user", "content": q},
        ],
    }).encode()

    url = str(ai.get("base_url") or DEFAULTS["base_url"]).rstrip("/") + "/chat/completions"
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        # Bearer is what every OpenAI-compatible endpoint takes, including the
        # local ones, which ignore it.
        "Authorization": "Bearer " + key,
    })
    _spend()
    try:
        with urllib.request.urlopen(req, timeout=timeout or ai.get("timeout_s")) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        # _provider_code CONSUMES the body, so it is read once and here.
        return None, http_error(e.code, _provider_code(e))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, f"could not reach {url.split('/v1')[0]} ({type(e).__name__})."
    except ValueError:
        return None, "the provider sent something that was not JSON."

    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None, "the provider's reply had no message in it."
    return (text or "").strip(), None


# ------------------------------------------------------------------ the panel

def panel(public=False):
    """The Ask tab. Private only — the answer is about this household's pool
    and the question is sent to somebody else's server."""
    import html as _h
    from .icons import icon

    ok = configured()
    ai = settings()
    where = str(ai.get("base_url") or "")
    # The HOST, not the URL: a base URL can carry a key in a query string on
    # some gateways, and this line is shown to whoever opens the tab.
    host = where.split("//")[-1].split("/")[0] or "not set"

    # THE FORM IS ALWAYS THERE, disabled when there is nothing to ask. This
    # product dims what a reader may not use rather than removing it — the
    # same rule the role notes follow — and a control that vanishes when
    # unconfigured leaves somebody wondering whether the tab is broken. It also
    # keeps access.ROLE_UI's selector pointing at something that exists, which
    # is what lets the page dim this for a `view` reader too.
    dis = "" if ok else " disabled"
    if ok:
        head = (f'<div class="v-note">{icon("alert", 15)} <b>Questions leave '
                f'this house.</b> Each one sends a summary of this pool — the '
                f'current readings, the targets, the volume and the recent '
                f'doses — to <code>{_h.escape(host)}</code>. No address, no '
                f'login and no equipment state goes with it. '
                f'{budget_left()} question(s) left this hour.</div>')
    else:
        head = (f'<div class="v-warn">{icon("alert", 15)} <b>Not configured.</b> '
                f'Add an API key under Logins on Settings, and set the endpoint '
                f'beside it. Until then this asks nothing and sends nothing.</div>')
    form = f'''<form id="ask-form" class="form" autocomplete="off">
      <label class="f"><span>Your question</span>
        <textarea name="question" id="ask-q" rows="3" maxlength="2000"{dis}
          placeholder="Is a quarter gallon of acid a week normal for this pool, or should I raise the alkalinity?"></textarea></label>
      <div class="factions">
        <button type="submit" class="btn primary"{dis}>Ask</button>
        <span class="fmsg" id="ask-msg"></span>
      </div>
    </form>
    <div class="card ask-answer" id="ask-answer" hidden></div>'''

    return f'''<section id="ask-it">
  <h2>Ask about this pool</h2>
  <p class="sub">The advantage over asking a chatbot the same question is that
  this one is tuned to <b>this pool's parameters</b> — its volume, its targets,
  what its water actually reads today and what has been poured into it. It
  answers from the record rather than from pool chemistry in general.</p>
  <p class="sub">The figures are computed here and handed over already worked
  out. The model is asked to explain them, not to recalculate them, because a
  model that multiplies gallons by a coefficient will sometimes be wrong and
  will always sound equally sure.</p>
  {head}
  {form}
</section>

<section id="ask-context">
  <h2>What it is told</h2>
  <p class="sub">Exactly this, and nothing that is not on this list. It is built
  from the same loaders the rest of the page reads, so the figures here are the
  figures you see everywhere else.</p>
  <div class="card"><pre class="ask-ctx">{_h.escape(context())}</pre></div>
</section>'''
