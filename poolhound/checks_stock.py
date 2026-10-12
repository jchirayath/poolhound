"""What is on the shelf: counts, purchases, doses, and how long it lasts.

A wrong answer here is a jug that runs out on a Saturday, or a page that tells
somebody they have -1.4 gallons of acid. Each case is a small shelf whose
answer is worked out by hand in its docstring.

Timestamps are naive local times throughout, so no case depends on the zone of
the machine running it; the one that writes through validate() round-trips
through the local offset and reads the same wall clock back.
"""
import datetime as dt
import os

from . import stock as S
from .render import num, when
from .selftest import check

NOW = dt.datetime(2026, 9, 30, 12, 0)
SKEW = dt.timedelta(minutes=5)


def _s(day, kind, chem, amount, unit, pct="31.45", paid=""):
    return {"ts": f"2026-09-{day:02d}T10:00:00", "kind": kind, "chemical": chem,
            "amount": str(amount), "unit": unit, "pct": pct, "paid": paid}


def _d(day, chem, amount, unit, hour=12):
    return {"ts": f"2026-09-{day:02d}T{hour:02d}:00:00", "chemical": chem,
            "amount": str(amount), "unit": unit, "pct": "31.45"}


def _est(key, kind, year, unit="gal", year_hi=None):
    return {"rows": [{"key": key, "kind": kind, "year": year, "week": None,
                      "year_lo": None, "year_hi": year_hi, "unit": unit,
                      "basis": "a fixture"}]}


def t_on_hand_is_the_count_plus_purchases_minus_doses():
    """Count 1.5 gal on the 5th; buy 2 gal on the 10th; pour 1 qt on the 12th.

    1.5 + 2 - 0.25 = 3.25 gal. A purchase on the 2nd and a dose on the 3rd are
    before the count and already in it, and a dose stamped the same minute as
    the count is taken as counted.
    """
    print("\n  stock — the count is the baseline")
    stock = [_s(2, "purchase", "acid", 4, "gal"),
             _s(5, "stocktake", "acid", 1.5, "gal"),
             _s(10, "purchase", "acid", 2, "gal")]
    doses = [_d(3, "acid", 64, "floz"),
             {**_d(5, "acid", 32, "floz"), "ts": "2026-09-05T10:00:00"},
             _d(12, "acid", 1, "qt")]
    h = S.on_hand("acid", stock, doses, when, num)
    check("on hand is 3.25 gal (in fl oz)", round(h["base"], 6), 3.25 * 128)
    check("measured from the count", h["from"], "stocktake")
    check("one purchase and one dose since", (h["purchases"], h["doses"]), (1, 1))
    check("a chemical never recorded is None, not zero",
          S.on_hand("salt", stock, doses, when, num), None)


def t_without_a_count_the_first_purchase_is_the_baseline():
    """No count: buy 2 gal on the 10th and 1 gal on the 20th; doses of 1 qt on
    the 8th (before anything was recorded) and 1 qt on the 15th.

    2 + 1 - 0.25 = 2.75. Subtracting the dose on the 8th would charge the
    record with acid that came out of a jug nobody wrote down.
    """
    print("\n  stock — no count yet")
    stock = [_s(10, "purchase", "acid", 2, "gal"), _s(20, "purchase", "acid", 1, "gal")]
    doses = [_d(8, "acid", 32, "floz"), _d(15, "acid", 32, "floz")]
    h = S.on_hand("acid", stock, doses, when, num)
    check("on hand is 2.75 gal", round(h["base"] / 128, 6), 2.75)
    check("measured from purchases", h["from"], "purchases")


def t_units_convert_and_a_solid_is_never_a_volume():
    """A 40 lb bag counted in kg and dosed in oz is still one quantity, and a
    gallon of salt is refused on the way in -- by the validator the CLI and the
    API share -- exactly as `bin/chem salt 1 gal` is."""
    print("\n  stock — units go through the catalogue")
    stock = [_s(1, "stocktake", "salt", 18.1437, "kg", "100")]
    doses = [_d(2, "salt", 160, "oz")]           # 10 lb, as weight ounces
    h = S.on_hand("salt", stock, doses, when, num)
    check("18.1437 kg less 160 oz is 30 lb", round(h["base"], 2), 30.0)

    row, why = S.validate({"kind": "purchase", "chemical": "salt",
                           "amount": "1", "unit": "gal"}, NOW, when, SKEW)
    check("a gallon of salt is refused", row, None)
    check("and the refusal names the unit", "'gal' is not a unit" in (why or ""), True)
    row, why = S.validate({"kind": "stocktake", "chemical": "shock",
                           "amount": "0", "unit": "lb"}, NOW, when, SKEW)
    check("a count of nothing is a count", why, None)
    row, why = S.validate({"kind": "purchase", "chemical": "shock",
                           "amount": "0", "unit": "lb"}, NOW, when, SKEW)
    check("a purchase of nothing is not", row is None, True)
    row, why = S.validate({"kind": "purchase", "chemical": "acid", "amount": "2",
                           "unit": "gal", "paid": "19.49",
                           "ts": "2026-09-29T08:30"}, NOW, when, SKEW)
    check("a purchase with a price is accepted", why, None)
    check("and keeps its wall-clock time", when(row["ts"]),
          dt.datetime(2026, 9, 29, 8, 30))
    row, why = S.validate({"kind": "stocktake", "chemical": "acid", "amount": "2",
                           "unit": "gal", "paid": "19.49"}, NOW, when, SKEW)
    check("a count carries no price", row["paid"], "")
    row, why = S.validate({"kind": "purchase", "chemical": "acid", "amount": "2",
                           "unit": "gal", "ts": "2026-10-02T08:30"}, NOW, when, SKEW)
    check("a purchase tomorrow is refused", why, "that time is in the future")


def t_a_negative_stock_asks_for_a_count():
    """Buy 1 gal, log 2 gal of doses: the record says -1 gal, which no shelf
    has ever held. The page says so and asks for a count; it never prints the
    negative, and Home carries it."""
    from . import render
    print("\n  stock — more used than bought")
    stock = [_s(1, "purchase", "acid", 1, "gal")]
    doses = [_d(2, "acid", 1, "gal"), _d(9, "acid", 1, "gal")]
    rows = S.summary(stock, doses, _est("acid", "measured", 5.0), NOW, when, num,
                     31.45, 3.0)
    check("the state is overdrawn", rows[0]["state"], "overdrawn")
    page = S.block(rows, 3.0)
    check("the page asks for a stocktake",
          "The record says you have used more than you recorded buying; "
          "take a stocktake." in page, True)
    check("and prints no negative quantity", "-1.00 gal" in page or "−" in page, False)
    acts = render.stock_actions(rows)
    check("Home carries it, as keep-an-eye-on", [a["pri"] for a in acts], ["watch"])


def t_without_a_rate_it_says_so_and_falls_back_to_the_doses():
    """No upkeep figure and one dose: no rate, and the page says so rather than
    dividing by nothing. Three doses of 1 qt over 20 days: the rate is the two
    before the newest over the span -- 64 fl oz / 20 days -- so 3 gal lasts
    384 / 3.2 = 120 days."""
    print("\n  stock — the rate, and its absence")
    stock = [_s(1, "stocktake", "chlorine", 3, "gal", "12.5")]
    one = [_d(2, "chlorine", 32, "floz")]
    rows = S.summary(stock, one, None, NOW, when, num, 31.45, 3.0)
    check("one dose is no rate", rows[0]["state"], "no-rate")
    check("and the page says it is not known", "Not known yet" in S.left_cell(rows[0]), True)

    stock = [_s(1, "stocktake", "chlorine", 3, "gal", "12.5")]
    three = [_d(1, "chlorine", 32, "floz", hour=9), _d(11, "chlorine", 32, "floz", hour=9),
             _d(21, "chlorine", 32, "floz", hour=9)]
    r = S.rate("chlorine", None, three, NOW, when, num)
    check("three doses over 20 days is 3.2 fl oz a day", round(r["per_day"], 6), 3.2)
    check("and it says where it came from", r["kind"], "logged")


def t_a_bound_gives_at_least_and_is_never_low():
    """Salt bounded at no more than 365 lb a year -- a pound a day -- and 28 lb
    on the shelf: four weeks at the CEILING, so at least four weeks. Under a
    threshold of five that is still not "low", because the true figure may be
    far longer; the same rate MEASURED is."""
    print("\n  stock — a bound is a floor on time")
    stock = [_s(1, "stocktake", "salt", 28, "lb", "100")]
    est = _est("salt", "bounded", None, unit="lb", year_hi=365.0)
    rows = S.summary(stock, [], est, NOW, when, num, 31.45, 5.0)
    check("four weeks, at least", round(rows[0]["weeks"], 6), 4.0)
    check("is not low", rows[0]["state"], "ok")
    check("and is said as a floor", S.left_cell(rows[0]).startswith("at least"), True)
    measured = _est("salt", "measured", 365.0, unit="lb")
    rows = S.summary(stock, [], measured, NOW, when, num, 31.45, 5.0)
    check("the same rate measured is low", rows[0]["state"], "low")


def t_weaker_acid_on_the_shelf_goes_faster():
    """upkeep quotes acid at 31.45%. At a measured 36.5 gal a year of it -- a
    tenth of a gallon a day -- two gallons of 31.45% last 20 days, and two of
    14.5% last 20 * 14.5/31.45 days."""
    print("\n  stock — strength changes the rate, not the gallons")
    stock = [_s(1, "stocktake", "acid", 2, "gal", "14.5")]
    rows = S.summary(stock, [], _est("acid", "measured", 36.5), NOW, when, num,
                     31.45, 3.0)
    check("days left at the weaker strength", round(rows[0]["weeks"] * 7, 6),
          round(20 * 14.5 / 31.45, 6))
    check("and the gallons are the gallons", round(rows[0]["qty"], 6), 2.0)


def t_a_purchase_price_fills_a_gap_and_settings_wins():
    """Two jugs for $19.49 is $9.745 a gallon, used where Settings has no acid
    price and saying which purchase it came from; a Settings price beats it;
    and 14.5% acid is not the price of 31.45%."""
    from . import upkeep
    print("\n  stock — what was paid becomes your price")
    stock = [_s(3, "purchase", "acid", 1, "gal", paid="12.00"),
             _s(9, "purchase", "acid", 2, "gal", paid="19.49"),
             _s(20, "purchase", "acid", 1, "gal", pct="14.5", paid="5.00")]
    got = S.household_prices({}, stock, 31.45, when, num)
    check("the newest same-strength purchase", round(got["acid"]["price"], 4), 9.745)
    p = upkeep.price_for("acid", got["acid"], 31.45)
    check("is costed as the household's own", p["own"], True)
    check("and says when it was paid", "9 Sep 2026" in p["source"], True)
    got = S.household_prices({"acid": 8.5}, stock, 31.45, when, num)
    check("a Settings price wins", got["acid"], 8.5)


def t_low_stock_is_the_last_thing_on_home():
    """A low shelf is "keep an eye on", with no dose -- so it is never queued
    as a water change -- and stock_actions() is appended after every other
    item, because Home is ordered by consequence and nothing is wrong with
    the water."""
    from . import render
    print("\n  stock — Home's least urgent item")
    stock = [_s(1, "stocktake", "acid", 0.25, "gal")]
    rows = S.summary(stock, [], _est("acid", "measured", 13.0), NOW, when, num,
                     31.45, 3.0)
    check("one week of acid is low", rows[0]["state"], "low")
    acts = render.stock_actions(rows)
    check("one action, keep an eye on, no dose",
          [(a["pri"], a["dose"]) for a in acts], [("watch", "")])
    import inspect
    src = inspect.getsource(render._build)
    check("and it is added after actions() has ordered the rest",
          src.index("ACTS = actions(") < src.index("ACTS += stock_actions("), True)


def t_stock_is_history_and_is_backed_up():
    """stock.csv cannot be re-measured, so a directory holding it is a pool and
    the nightly tarball takes it -- bin/backup reads config.HISTORY_FILES."""
    from . import config
    print("\n  stock — the record is history")
    check("stock.csv is history", S.FILE in config.HISTORY_FILES, True)


def t_the_routes_write_edit_and_remove_one_row():
    """Through the server's own functions, in a throwaway data directory:
    a purchase written, its amount edited in place by id, then removed."""
    import csv, shutil, tempfile
    from . import config, server
    print("\n  stock — the write routes")
    d = tempfile.mkdtemp()
    data = os.path.join(d, "data"); os.makedirs(data)
    cfgp = os.path.join(d, "config.toml")
    with open(cfgp, "w") as f:
        f.write("[pool]\nvolume_gallons = 16250\n")
    keys = ("POOLHOUND_DATA", "POOLHOUND_CONFIG")
    prev = {k: os.environ.get(k) for k in keys}
    try:
        os.environ.update(POOLHOUND_DATA=data, POOLHOUND_CONFIG=cfgp)
        cfg = config.load()
        r, e = server.log_stock({"kind": "purchase", "chemical": "acid",
                                 "amount": "2", "unit": "gal", "paid": "19.49"},
                                cfg, "p@x.y")
        check("a purchase is recorded", e, None)
        p = os.path.join(data, S.FILE)
        rows = list(csv.DictReader(open(p)))
        check("one row, attributed, with an id",
              (len(rows), rows[0]["by"], bool(rows[0]["id"])), (1, "p@x.y", True))
        r, e = server.edit_stock({"id": rows[0]["id"], "amount": "3"}, cfg, "q@x.y")
        rows = list(csv.DictReader(open(p)))
        check("edited in place", (e, len(rows), rows[0]["amount"], rows[0]["paid"]),
              (None, 1, "3", "19.49"))
        r, e = server.edit_stock({"id": rows[0]["id"], "unit": "lb"}, cfg, "q@x.y")
        check("an edit is held to the same units", e is not None, True)
        r, e = server.delete_stock({"id": rows[0]["id"]}, cfg, "q@x.y")
        check("and removed", (e, len(list(csv.DictReader(open(p))))), (None, 0))
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(d, ignore_errors=True)


def t_bin_chem_records_stock_and_refuses_a_solid_by_volume():
    """`bin/chem stocktake salt 1 gal` is refused, naming the unit, for the
    reason `bin/chem salt 1 gal` is; an option that does not exist stops the
    command rather than becoming the note; and a real purchase is written."""
    import csv, shutil, subprocess, sys, tempfile
    from . import config
    print("\n  stock — bin/chem bought and stocktake")
    d = tempfile.mkdtemp()
    dd = os.path.join(d, "data"); os.makedirs(dd)
    with open(os.path.join(d, "config.toml"), "w") as f:
        f.write("[pool]\nvolume_gallons = 16250\n")
    env = dict(os.environ, POOLHOUND_DATA=dd, POOLHOUND_SITE=os.path.join(d, "site"),
               POOLHOUND_CONFIG=os.path.join(d, "config.toml"))
    chem = os.path.join(config.root(), "bin", "chem")

    def run(*a):
        return subprocess.run([sys.executable, chem, *a], capture_output=True,
                              text=True, env=env, timeout=120)
    try:
        r = run("stocktake", "salt", "1", "gal")
        check("a gallon of salt is refused", r.returncode != 0, True)
        check("and the refusal names the unit",
              "'gal' is not a unit" in r.stdout + r.stderr, True)
        r = run("bought", "acid", "2", "gal", "--dry-run")
        check("an unknown option is refused", r.returncode != 0, True)
        p = os.path.join(dd, S.FILE)
        check("and neither wrote anything", os.path.exists(p), False)
        r = run("bought", "acid", "2", "gal", "--paid", "19.49", "two jugs")
        check("a purchase is recorded", r.returncode, 0)
        rows = list(csv.DictReader(open(p))) if os.path.exists(p) else []
        check("with its price and its note",
              [(x["kind"], x["paid"], x["note"]) for x in rows],
              [("purchase", "19.49", "two jugs")])
    finally:
        shutil.rmtree(d, ignore_errors=True)
