"""The saturation index: the arithmetic, what it rests on, and who may see it.

A wrong index is advice about plaster that cannot be taken back. Too high and
the page calls aggressive water balanced while it dissolves the finish; too low
and it sends somebody to buy calcium the pool does not need -- and calcium,
unlike everything else in the catalogue, only leaves a pool by draining it.

The arithmetic is held to PUBLISHED figures rather than to itself: the PHTA/CPO
handbook's two worked examples and its factor tables, and the cyanuric acid
correction Taylor Technologies prints for its alkalinity test. A case that
re-derives the expected value from the same formula it is testing proves only
that the formula agrees with itself.

Timestamps are naive local times throughout, so no case depends on the zone of
the machine running it.
"""
import csv
import datetime as dt
import os
import shutil
import subprocess
import sys
import tempfile

from . import chemicals as CH
from . import config
from .selftest import check


def _close(got, want, tol):
    return got is not None and abs(got - want) <= tol


# ---------------------------------------------------------------- arithmetic
def t_the_index_matches_the_handbooks_worked_examples():
    """The PHTA/CPO handbook's two examples, within the rounding its tables carry.

    The handbook works LSI = pH + TF + CF + AF - 12.1 from printed factor
    tables, every factor rounded to 0.1. Its examples:

      balanced   pH 7.5, 84 F, calcium 300, alkalinity 100  ->  +0.2
      corrosive  pH 7.2, 60 F, calcium 150, alkalinity 75   ->  -0.8

    The 12.1 is its constant for TDS under 1000, so the examples are taken at
    a fresh-fill 300 ppm. Four factors each rounded to the nearest 0.1 can
    disagree with a continuous formula by up to 0.2; held here to 0.1, and to
    the same verdict, which is the half somebody acts on.

    And the factors one at a time, because a right total can hide two wrong
    terms that cancel: calcium 300 -> 2.1 and alkalinity 100 -> 2.0 from the
    handbook's tables, and the temperature factor's STEP between rows -- 60 F
    is 0.4 and 84 F is 0.7, 105 F is 0.9 -- since the temperature term is the
    one this feature exists to apply.
    """
    print("\n  saturation — the handbook's worked examples")
    a = CH.saturation_index(7.5, 84, 300, 100, tds=300)
    b = CH.saturation_index(7.2, 60, 150, 75, tds=300)
    check("the balanced example is +0.2 within the tables' rounding",
          _close(a and a["lsi"], 0.2, 0.1), True)
    check("and reads as balanced", CH.lsi_word(a["lsi"]), "balanced")
    check("the corrosive example is -0.8 within the tables' rounding",
          _close(b and b["lsi"], -0.8, 0.1), True)
    check("and reads as aggressive", CH.lsi_word(b["lsi"]), "aggressive")

    check("calcium 300 is the table's 2.1", _close(a["terms"]["calcium"], 2.1, 0.05), True)
    check("alkalinity 100 is the table's 2.0",
          _close(a["terms"]["alkalinity"], 2.0, 0.05), True)
    # The temperature term enters pHs with the opposite sign to the handbook's
    # TF, so a warmer row has a SMALLER term here.
    t60, t84 = b["terms"]["temperature"], a["terms"]["temperature"]
    t104 = CH.saturation_index(7.5, 104, 300, 100, tds=300)["terms"]["temperature"]
    check("60 F to 84 F moves the index the table's 0.3",
          _close(t60 - t84, 0.3, 0.06), True)
    check("84 F to the spa's 104 F moves it the table's 0.2",
          _close(t84 - t104, 0.19, 0.06), True)
    check("and warmer water reads HIGHER, not lower", t104 < t84 < t60, True)

    # TDS is the gentle term, and it lowers the index as it rises.
    lo = CH.saturation_index(7.5, 84, 300, 100, tds=1000)["lsi"]
    hi = CH.saturation_index(7.5, 84, 300, 100, tds=4000)["lsi"]
    check("a salt pool's TDS lowers the index by about 0.06",
          _close(lo - hi, 0.06, 0.01), True)


def t_the_stabiliser_correction_is_taylors_table():
    """Carbonate alkalinity is TA less a pH-dependent share of the CYA.

    Taylor Technologies' correction factors for cyanuric acid in a
    total-alkalinity test: 0.22 at pH 7.0, 0.26 at 7.2, 0.30 at 7.4, 0.33 at
    7.6, 0.35 at 7.8. chemicals.cya_alkalinity() computes the share from the
    pKa rather than reading the table, and must land on every row. A flat
    "subtract a third" is right at 7.5 and nowhere else.
    """
    print("\n  saturation — the stabiliser correction")
    TAYLOR = {7.0: 0.22, 7.2: 0.26, 7.4: 0.30, 7.6: 0.33, 7.8: 0.35}
    off = {ph: round(CH.cya_alkalinity(1.0, ph), 3) for ph, f in TAYLOR.items()
           if abs(CH.cya_alkalinity(1.0, ph) - f) > 0.005}
    check("every row of Taylor's table, to the printed digit", off, {})
    check("and it depends on pH",
          CH.cya_alkalinity(60, 7.8) - CH.cya_alkalinity(60, 7.2) > 3, True)
    r = CH.saturation_index(7.5, 84, 300, 80, cya=60)
    check("the index is computed on carbonate, not total, alkalinity",
          _close(r["carbonate_alk"], 80 - 60 * 0.31, 0.6), True)
    check("no stabiliser means nothing is subtracted",
          CH.saturation_index(7.5, 84, 300, 80)["carbonate_alk"], 80.0)


# ---------------------------------------------------------------- the report
NOW = dt.datetime(2026, 10, 9, 15, 0)


def _lab(v, days_ago=1, source="Leslie's"):
    return {"value": v, "source": source,
            "measured": (NOW - dt.timedelta(days=days_ago)).isoformat(timespec="minutes")}


def _sample(hours_ago, temp, pump="1", **kw):
    r = {"ts": (NOW - dt.timedelta(hours=hours_ago)).isoformat(timespec="minutes"),
         "pump": pump, "pool_temp": str(temp)}
    r.update(kw)
    return r


def _pool(**over):
    LAB = {"ch": _lab(300), "ta": _lab(80), "cya": _lab(50)}
    DAILY = {"ph": _lab(7.5, 0, "WaterGuru pod")}
    for k, v in over.items():
        (DAILY if k == "ph" else LAB)[k] = v
    return {k: v for k, v in LAB.items() if v is not None}, \
           {k: v for k, v in DAILY.items() if v is not None}


def t_a_missing_reading_is_named_not_guessed():
    """No calcium or no alkalinity means no index, and the report says which.

    There is no typical calcium that would make an answer about THIS water;
    a default here would be the 15,000-gallon notional pool again, wearing
    a chemistry label. TDS and CYA are different -- absent, they are filled or
    left out, and the report has to say so.
    """
    from . import render
    print("\n  saturation — what is missing is named")
    samples = [_sample(1, 84)]
    for k, word in (("ch", "a calcium reading"), ("ta", "an alkalinity reading"),
                    ("ph", "a pH reading")):
        LAB, DAILY = _pool(**{k: None})
        s = render.saturation(LAB, DAILY, samples, now=NOW)
        check(f"with no {render.SAT_NAMES[k]} the report names it",
              word in s["missing"], True)
        check(f"and computes nothing", s["now"]["result"], None)
    LAB, DAILY = _pool()
    s = render.saturation(LAB, DAILY, samples, now=NOW)
    check("with all three there is an index", s["now"]["result"] is not None, True)
    check("TDS not measured is assumed, and said", (s["tds"], s["tds_measured"],
          "assumed" in s["tds_note"]), (CH.TDS_ASSUMED, False, True))
    LAB, DAILY = _pool(salt=_lab(3400))
    s = render.saturation(LAB, DAILY, samples, now=NOW)
    check("on a salt pool the floor is the salt itself", s["tds"], 3400)
    LAB, DAILY = _pool(cya=None)
    s = render.saturation(LAB, DAILY, samples, now=NOW)
    check("no CYA is said, because it reads the index high",
          "LOWER" in s["cya_note"], True)
    s = render.saturation(LAB, DAILY, [], now=NOW)
    check("no water temperature at all is no index, not a guessed one",
          (s["now"], s["coldest"]), (None, None))


def t_a_temperature_without_flow_is_not_the_waters():
    """The probe is in the plumbing; with the pump off it reads the pipe.

    commands.drop_stale_flow_readings() is the rule both collectors write
    through, and rows from before it are still on disk. The reader applies
    it too: a 4 a.m. pipe at 48 F must neither be "the water now" nor "the
    coldest the water has been", or the page calls a warm pool aggressive.
    """
    from . import render
    print("\n  saturation — a temperature with no flow is not the water's")
    LAB, DAILY = _pool()
    samples = [_sample(30, 80), _sample(6, 84),
               _sample(2, 48, pump="0"),               # the pipe, at night
               _sample(1, 47, pump="0")]
    s = render.saturation(LAB, DAILY, samples, now=NOW)
    check("now is the last reading with water moving", s["now"]["temp_f"], 84.0)
    check("and it says the pump is off at the moment", s["now"]["pump_off_now"], True)
    check("the coldest is the coldest WATER, not the coldest pipe",
          s["coldest"]["temp_f"], 80.0)
    old = [_sample(24 * 40, 60), _sample(1, 84)]
    s = render.saturation(LAB, DAILY, old, now=NOW)
    check(f"and it looks back {render.LSI_WINDOW_DAYS} days, no further",
          s["coldest"]["temp_f"], 84.0)
    spa = [_sample(1, 84, spa_set="102.0")]
    s = render.saturation(LAB, DAILY, spa, now=NOW)
    check("the spa is computed at its setpoint", (s["spa"]["kind"], s["spa"]["temp_f"]),
          ("setpoint", 102.0))
    check("and reads higher than the pool, because it is hotter",
          s["spa"]["result"]["lsi"] > s["now"]["result"]["lsi"], True)
    s = render.saturation(LAB, DAILY, spa, shares=False, now=NOW)
    check("a spa with its own water is not described by the pool's readings",
          s["spa"], None)


def t_the_oldest_reading_bounds_the_index():
    """An index is no fresher than the stalest reading it is built from."""
    from . import render
    print("\n  saturation — the oldest input bounds the answer")
    LAB, DAILY = _pool(ch=_lab(300, days_ago=60, source="WaterGuru lab"))
    s = render.saturation(LAB, DAILY, [_sample(1, 84)], now=NOW)
    check("a two-month-old calcium is what bounds it",
          (s["oldest"]["name"], s["oldest"]["source"]), ("calcium", "WaterGuru lab"))
    row = {"measured": "2026-10-04", "saturation_index": "-0.11"}
    s = render.saturation(LAB, DAILY, [_sample(1, 84)],
                          reported=((row, "Leslie's"), ({}, "WaterGuru lab")), now=NOW)
    check("a lab's own index is carried beside it, by name",
          [(r["source"], r["value"]) for r in s["reported"]], [("Leslie's", -0.11)])
    check("and is never averaged into the computed one",
          s["now"]["result"]["lsi"] != -0.11, True)


# ---------------------------------------------------------------- Home
def _acts(sat, ph=7.5, ch=300.0, ta=80.0):
    from . import render
    LAB = {"ch": _lab(ch), "ta": _lab(ta), "cya": _lab(50)}
    DAILY = {"ph": _lab(ph, 0, "WaterGuru pod")}
    TG = render.targets(LAB, "salt_cell", "")
    return render.actions(LAB, {}, TG, [{"ts": "x"}], [], 16000.0, 31.45,
                          DAILY=DAILY, sat=sat)


def t_home_ranks_the_index_where_it_changes_the_advice():
    """Aggressive water is permanent damage and ranks with calcium; the rest watch.

    Four places the index changes what Home says: the water is aggressive
    with calcium in its band (ranked beside calcium, above pH, because it does
    not reverse); a pH-down dose that would land the index aggressive says so;
    the spa at its setpoint scales; and the coldest water of the month is
    aggressive while today's is not. And one place it CORRECTS Home: "low pH
    makes the water aggressive" was asserted with nothing consulted.
    """
    from . import render
    print("\n  saturation — Home ranks it by consequence")

    def report(ph, ch, ta, temps, spa_set=None):
        LAB = {"ch": _lab(ch), "ta": _lab(ta), "cya": _lab(50)}
        DAILY = {"ph": _lab(ph, 0, "WaterGuru pod")}
        smp = [_sample(h, t) for h, t in temps]
        if spa_set:
            smp[-1]["spa_set"] = str(spa_set)
        return render.saturation(LAB, DAILY, smp, now=NOW)

    # Calcium inside its band (250-450 on a salt pool), alkalinity at the
    # bottom of its own, cold water: aggressive with no single reading out.
    sat = report(7.6, 260, 60, [(1, 62)])
    titles = [a["title"] for a in _acts(sat, ph=7.6, ch=260, ta=60)]
    check("aggressive water with calcium in band is an action",
          "The water is aggressive to plaster" in titles, True)
    check("and it is a 'do', not a 'watch'",
          next(a["pri"] for a in _acts(sat, ph=7.6, ch=260, ta=60)
               if a["title"].startswith("The water is aggressive")), "do")

    # pH high, index only just balanced: the acid this card prescribes
    # tips it. The dose stays; the cost is stated.
    sat = report(7.8, 260, 70, [(1, 70)])
    ph_card = next(a for a in _acts(sat, ph=7.8, ch=260, ta=70)
                   if a["title"] == "Bring pH down")
    check("a pH-down card says where its dose lands the index",
          "aggressive to plaster" in ph_card["detail"], True)
    check("and still carries the dose", bool(ph_card["dose"]), True)
    order = [a["title"] for a in _acts(report(7.8, 260, 60, [(1, 60)]),
                                        ph=7.8, ch=260, ta=60)]
    if "The water is aggressive to plaster" in order and "Bring pH down" in order:
        check("aggressive water ranks above the pH card",
              order.index("The water is aggressive to plaster")
              < order.index("Bring pH down"), True)
    else:
        check("the fixture produced both cards to rank", order, ["both"])

    # Hard, warm water: the spa at 104 scales while the pool does not.
    sat = report(7.6, 420, 90, [(1, 80)], spa_set=104)
    titles = [a["title"] for a in _acts(sat, ph=7.6, ch=420, ta=90)]
    check("a spa setpoint that scales is a watch item",
          "The heated spa is inclined to scale" in titles, True)

    # Balanced today, aggressive at the month's coldest.
    sat = report(7.5, 300, 80, [(24 * 10, 58), (1, 84)])
    titles = [a["title"] for a in _acts(sat)]
    check("cold water that turns it aggressive is said",
          "Cold water turns this pool aggressive" in titles, True)

    # The correction: low pH with the calcium holding the index up.
    sat = report(7.2, 450, 90, [(1, 86)])
    low = next(a for a in _acts(sat, ph=7.2, ch=450, ta=90)
               if a["title"] == "pH is below target")
    check("a low-pH card does not call balanced water aggressive",
          "inside the balanced band" in low["detail"], True)

    # And with no index, Home says what it always said.
    titles = [a["title"] for a in _acts(None)]
    check("no index, no index items",
          [t for t in titles if "aggressive" in t or "scale" in t], [])


# ---------------------------------------------------------------- boundary
def _fixture(dd):
    """A pool with everything the index needs, so the private card renders."""
    stamp = (dt.datetime.now() - dt.timedelta(hours=2)).isoformat(timespec="seconds")
    day = (dt.datetime.now() - dt.timedelta(days=3)).date().isoformat()
    files = {
        "leslies.csv": (["measured", "ph", "ta", "ch", "cya", "salt", "tds",
                         "saturation_index"],
                        [{"measured": day, "ph": "7.5", "ta": "80", "ch": "300",
                          "cya": "50", "salt": "3300", "tds": "3900",
                          "saturation_index": "-0.17"}]),
        "readings.csv": (["measured", "ph", "free_cl"],
                         [{"measured": stamp, "ph": "7.5", "free_cl": "3.0"}]),
        "samples.csv": (["ts", "pump", "pool_temp", "spa_set"],
                        [{"ts": stamp, "pump": "1", "pool_temp": "83.0",
                          "spa_set": "102.0"}]),
    }
    for name, (cols, rows_) in files.items():
        with open(os.path.join(dd, name), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in rows_:
                w.writerow(r)


def t_the_public_build_carries_no_household_index():
    """Pool chemistry is public; this household's computed index is not.

    The tab ships to anonymous visitors, so the private card is not rendered
    for that build at all, and the build refuses a public page carrying its
    marker. Rendered here from a fixture, both builds, through bin/render --
    the same door cron uses.
    """
    from . import render
    print("\n  saturation — the public build carries no household index")
    root = config.root()
    d = tempfile.mkdtemp()
    try:
        dd, site = os.path.join(d, "data"), os.path.join(d, "site")
        os.makedirs(dd)
        os.makedirs(site)
        with open(os.path.join(d, "config.toml"), "w") as f:
            f.write("[pool]\nvolume_gallons = 16250\n")
        _fixture(dd)
        env = dict(os.environ, POOLHOUND_DATA=dd, POOLHOUND_SITE=site,
                   POOLHOUND_CONFIG=os.path.join(d, "config.toml"))
        r = subprocess.run([sys.executable, os.path.join(root, "bin", "render")],
                           capture_output=True, text=True, env=env, timeout=240)
        check("both builds render with an index to compute", r.returncode, 0)
        if r.returncode:
            print("    " + (r.stderr.strip().splitlines() or ["(no stderr)"])[-1][:150])
            return
        pub = open(os.path.join(site, "index.html"), encoding="utf-8").read()
        adm = open(os.path.join(site, "admin.html"), encoding="utf-8").read()
        mark = render.HOUSEHOLD_LSI_MARK
        check("the authenticated build carries this pool's index", mark in adm, True)
        check("and says what it was computed at", "83°F" in adm, True)
        check("the public build does not", mark in pub, False)
        check("nor this pool's water temperature", "83°F" in pub, False)
        check("but the public build keeps the reference",
              'id="saturation"' in pub, True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_the_assistant_is_sent_the_index_as_a_fixed_field():
    """The index goes to the model computed, with its temperature and its age.

    A model sent calcium, alkalinity and pH alone computes the index itself,
    at a temperature it has to guess. assistant.context() is a FIXED set of
    fields, so this is a field added on purpose -- and with a reading missing
    it says so rather than going quiet.
    """
    from . import assistant, render
    print("\n  saturation — the assistant is sent it")
    real = render.rows
    stamp = (dt.datetime.now() - dt.timedelta(hours=2)).isoformat(timespec="seconds")
    full = {
        "leslies.csv": [{"measured": stamp[:10], "ta": "80", "ch": "300", "cya": "50",
                         "saturation_index": "-0.17"}],
        "readings.csv": [{"measured": stamp, "ph": "7.5"}],
        "samples.csv": [{"ts": stamp, "pump": "1", "pool_temp": "83.0"}],
    }
    try:
        render.rows = lambda name, *a, **k: [dict(r) for r in full.get(name, [])]
        ctx = assistant.context()
        line = next((l for l in ctx.splitlines() if l.strip().startswith("pool now")), "")
        check("the context has a saturation section", "SATURATION INDEX" in ctx, True)
        check("with the index at the water's temperature", "83F" in line, True)
        check("and the lab's own index beside it, not merged",
              "Leslie's reported its own index -0.17" in ctx, True)
        no_ca = dict(full, **{"leslies.csv": [{"measured": stamp[:10], "ta": "80"}]})
        render.rows = lambda name, *a, **k: [dict(r) for r in no_ca.get(name, [])]
        ctx = assistant.context()
        check("with no calcium it says which reading is missing",
              "needs a calcium reading" in ctx, True)
    finally:
        render.rows = real
