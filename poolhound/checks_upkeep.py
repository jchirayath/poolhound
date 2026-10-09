"""What upkeep takes: the acid balance, the salt trend, and what follows from them.

A wrong answer here is a wrong shopping list -- and in the acid's case, the
chemical that damages plaster. Each case is a synthetic pool whose answer is
known in advance, and two of them are the failures this pool's own record
produced on the first run: an agent outage read as a pump that was off, and a
bag of salt read as the pool gaining salt by itself.

Timestamps are naive local times throughout, so no case depends on the zone of
the machine running it.
"""
import datetime as dt

from . import chemicals as CH
from . import upkeep as U
from .render import num, when
from .selftest import check

GAL = 15000.0
TA = 70.0
PCT = 31.45
START = dt.date(2026, 9, 1)


def _days(n, pump=6.0, salt=None, sheer=0.0):
    """n by_day()-shaped days from START, newest first like the real thing."""
    out = []
    for i in range(n):
        d = START + dt.timedelta(days=i)
        out.append({"date": d.isoformat(),
                    "hours": {"pump": pump, "spa": 0.0, "sheer": sheer, "heat": 0.0},
                    "swg": 40.0,
                    "salt": None if salt is None else salt(i)})
    return sorted(out, key=lambda x: x["date"], reverse=True)


def _samples(n, missing=()):
    """A whole day's worth of sample stamps for every day not in `missing`."""
    out = []
    for i in range(n):
        if i in missing:
            continue
        d = START + dt.timedelta(days=i)
        out += [{"ts": f"{d.isoformat()}T{h:02d}:{m:02d}:00"}
                for h in range(24) for m in (0, 15, 30, 45)]
    return out


def _ph(day, v, hour=19):
    d = START + dt.timedelta(days=day)
    return {"measured": f"{d.isoformat()}T{hour:02d}:30:00", "ph": str(v)}


def _acid(day, floz, hour=10, pct=PCT):
    d = START + dt.timedelta(days=day)
    return {"ts": f"{d.isoformat()}T{hour:02d}:00:00", "chemical": "acid",
            "amount": str(floz), "unit": "floz", "pct": str(pct)}


def _now(n):
    return dt.datetime.combine(START + dt.timedelta(days=n), dt.time(23, 0))


def _run(days, samples, doses, readings, n):
    return U.acid(days, U.coverage(samples, when), doses, readings, GAL, TA,
                  PCT, _now(n), when, num)


def t_acid_is_what_went_in_when_ph_ends_where_it_started():
    """The plain balance: two doses, pH back where it began.

    Readings on day 0 and day 28, both 7.8, with 128 fl oz between. Pump hours
    are the 28 days after the first reading, six a day. Nothing else should
    enter the answer.
    """
    print("\n  upkeep — the acid balance")
    n = 30
    # The first reading's own day ran twelve hours, all of them before that
    # evening's reading, so none of them belong in the balance. A uniform six
    # every day could not tell: an extra day counted is repaid as a negative
    # gap, and the off-by-one hid inside the arithmetic meant for outages.
    days = _days(n)
    days[-1]["hours"]["pump"] = 12.0
    r = _run(days, _samples(n), [_acid(1, 64), _acid(15, 64)],
             [_ph(0, 7.8), _ph(14, 8.1), _ph(28, 7.8)], n)
    check("a balance is drawn", r["ok"], True)
    check("both doses are counted", r["doses"], 2)
    check("pump hours are the days after the first reading, through the last",
          round(r["pump_h"], 6), 28 * 6.0)
    check("acid per pump hour is acid in over pump hours",
          round(r["per_pump_h"], 6), round(128 / (28 * 6.0), 6))


def t_a_net_rise_is_priced_as_the_acid_it_would_have_taken():
    """pH ending higher than it started means holding it cost MORE than went in."""
    n = 30
    r = _run(_days(n), _samples(n), [_acid(1, 64), _acid(15, 64)],
             [_ph(0, 7.8), _ph(28, 8.0)], n)
    per = CH.effects("acid", 1, "floz", PCT, GAL, TA)["ph"]
    check("the 0.2 rise is added at the catalogue's own rate",
          round(r["demand_floz"], 6), round(128 + 0.2 / -per, 6))
    check("and the answer is larger than what went in",
          r["demand_floz"] > r["acid_in_floz"], True)


def t_an_outage_is_a_hole_not_a_pump_that_was_off():
    """MEASURED: the agent's outage counted as zero pump hours.

    Three days with no samples sit inside the stretch. They are runtime
    nobody saw, not runtime that did not happen: counted at the stretch's
    average, said to be, and left out of the quoted runtime entirely.
    """
    n = 30
    days = _days(n)
    for d in days:
        if d["date"] in {(START + dt.timedelta(days=i)).isoformat() for i in (5, 6, 7)}:
            d["hours"]["pump"] = 0.0
    samples = _samples(n, missing={5, 6, 7})
    r = _run(days, samples, [_acid(1, 64), _acid(15, 64)],
             [_ph(0, 7.8), _ph(28, 7.8)], n)
    check("the holes are counted", r["holes"], 3)
    check("and filled at the average, not at zero",
          round(r["pump_h"], 6), 28 * 6.0)
    h, _ = U.runtime(days, U.coverage(samples, when), _now(n).date())
    check("the quoted runtime ignores them", round(h, 6), 6.0)


def t_a_reading_too_soon_after_a_dose_does_not_end_the_balance():
    """A reading two hours after a dose measures a gradient, not the pool.

    The newest reading follows a dose by two hours. The balance ends at the
    reading before it instead, and that last dose is not counted -- it would
    otherwise be credited with an effect nobody has seen yet.
    """
    n = 30
    r = _run(_days(n), _samples(n),
             [_acid(1, 64), _acid(15, 64), _acid(28, 64, hour=17)],
             [_ph(0, 7.8), _ph(27, 7.8), _ph(28, 7.4)], n)
    check("the balance ends on the settled reading",
          r["end"].date(), START + dt.timedelta(days=27))
    check("and does not count the dose it has not seen", r["doses"], 2)


def t_too_little_record_says_why_instead_of_guessing():
    n = 30
    r = _run(_days(n), _samples(n), [_acid(1, 64)], [_ph(0, 7.8), _ph(28, 7.8)], n)
    check("one dose is not a balance", r["ok"], False)
    check("and it says so", "at least two" in r["why"], True)
    r = _run(_days(n), _samples(n), [_acid(1, 64), _acid(5, 64)],
             [_ph(0, 7.8), _ph(8, 7.8)], 9)
    check("nor is a week", r["ok"], False)


def t_acid_strength_is_normalised_before_it_is_added():
    """A dose at 14.5% is under half a dose at 31.45%, and is counted so."""
    n = 30
    weak = _run(_days(n), _samples(n),
                [_acid(1, 64, pct=14.5), _acid(15, 64, pct=14.5)],
                [_ph(0, 7.8), _ph(28, 7.8)], n)
    check("128 fl oz at 14.5% is quoted in 31.45% terms",
          round(weak["acid_in_floz"], 6), round(128 * 14.5 / PCT, 6))


def t_salt_with_no_trend_is_a_bound_not_a_figure():
    """The cell's sensor scatters; a flat line through scatter is no loss seen."""
    print("\n  upkeep — salt leaves with water")
    wobble = [40, -60, 10, 80, -30, -70, 50, 0, -40, 60]
    days = _days(40, salt=lambda i: 3300 + wobble[i % len(wobble)])
    s = U.salt(days, [], GAL, _now(40), when, num)
    check("a trend is read", s["ok"], True)
    check("but it is not called measured", s["measured"], False)
    check("and still has an upper bound", s["ppm_day_hi"] > 0, True)


def t_a_real_decline_is_measured():
    days = _days(40, salt=lambda i: 3400 - 5 * i + (3 if i % 2 else -3))
    s = U.salt(days, [], GAL, _now(40), when, num)
    check("five ppm a day is seen", s["measured"], True)
    check("at about five ppm a day", round(s["ppm_day"]), 5)


def t_a_bag_of_salt_is_not_the_pool_gaining_salt():
    """MEASURED shape: salt logged mid-stretch reads as a jump unless removed.

    The pool loses 5 ppm a day; on day 20 a bag adds what 40 lb adds. Read raw,
    the line through that is a GAIN. With the logged dose taken out first it is
    the same loss as without the bag.
    """
    bag = CH.effects("salt", 40, "lb", 100, GAL)["salt"]
    days = _days(40, salt=lambda i: 3400 - 5 * i + (bag if i > 20 else 0)
                 + (3 if i % 2 else -3))
    dose = {"ts": (START + dt.timedelta(days=20)).isoformat() + "T12:00:00",
            "chemical": "salt", "amount": "40", "unit": "lb", "pct": "100"}
    s = U.salt(days, [dose], GAL, _now(40), when, num)
    check("the loss is still measured", s["measured"], True)
    check("at the rate the pool actually loses", round(s["ppm_day"]), 5)


def t_what_follows_from_the_acid_and_the_salt():
    """Baking soda and stabiliser are derived, through the catalogue, not fitted."""
    print("\n  upkeep — the derived rows")
    n = 40
    days = _days(n, salt=lambda i: 3400 - 5 * i + (3 if i % 2 else -3))
    est = U.estimate(days, _samples(n), [_acid(1, 64), _acid(15, 64)],
                     [_ph(0, 7.8), _ph(28, 7.8)], GAL, TA, PCT, 50.0, _now(n),
                     when, num)
    rows = {r["key"]: r for r in est["rows"]}
    acid_wk_floz = rows["acid"]["week"] * 128
    ta_drop = -CH.effects("acid", acid_wk_floz, "floz", PCT, GAL, TA)["ta"]
    check("baking soda replaces exactly what the acid takes",
          round(rows["bicarb"]["week"], 6),
          round(CH.amount_for("bicarb", "ta", ta_drop, GAL), 6))
    frac = est["salt"]["frac_day"]
    check("stabiliser leaves at the salt's rate, at its target",
          round(rows["cya"]["year"], 6),
          round(CH.amount_for("cya", "cya", frac * 365 * 50.0, GAL), 6))
    check("chlorine is none, not unknown", rows["chlorine"]["kind"], "none")


def _priced(prices=None, pct=PCT):
    n = 40
    days = _days(n, salt=lambda i: 3300 + (40 if i % 2 else -40))
    return U.estimate(days, _samples(n), [_acid(1, 64), _acid(15, 64)],
                      [_ph(0, 7.8), _ph(28, 7.8)], GAL, TA, pct, 50.0, _now(n),
                      when, num, prices=prices)


def t_a_cost_says_whose_price_it_used():
    """A reference price is somebody else's receipt, and is labelled as one.

    With nothing entered, acid is costed at the reference and says "typical
    retail"; with a price entered, that price is used and says "your price".
    The year's cost is the year's amount at that price, nothing else.
    """
    print("\n  upkeep — what it costs, and at whose price")
    ref = _priced()
    acid = next(r for r in ref["rows"] if r["key"] == "acid")
    check("unset, acid is priced at the reference",
          acid["price"]["price"], CH.REFERENCE_PRICES["acid"]["price"])
    check("and says so", acid["price"]["source"].startswith("typical retail"), True)
    check("the cost is the amount at that price",
          round(acid["cost_year"], 6), round(acid["year"] * acid["price"]["price"], 6))
    own = _priced(prices={"acid": 12.0})
    acid = next(r for r in own["rows"] if r["key"] == "acid")
    check("set, the household's own price wins", acid["price"]["price"], 12.0)
    check("and is called theirs", acid["price"]["own"], True)


def t_a_reference_acid_price_is_for_its_own_strength():
    """The 31.45% price is not a price for 14.5% acid, which does half the work."""
    weak = _priced(pct=14.5)
    acid = next(r for r in weak["rows"] if r["key"] == "acid")
    check("no reference price is applied to another strength", acid["price"], None)
    check("and the cell says so rather than showing $0",
          U._cost_cell(acid), "no price")


def t_the_total_keeps_a_bound_out_of_the_sum():
    """Salt with no measurable loss is "up to", never folded into the total."""
    est = _priced()
    rows = {r["key"]: r for r in est["rows"]}
    check("salt here is a bound", rows["salt"]["kind"], "bounded")
    check("the total is only what is measured or follows from it",
          round(est["cost_year"], 6),
          round(rows["acid"]["cost_year"] + rows["bicarb"]["cost_year"], 6))
    check("and the bound is carried beside it",
          round(est["cost_up_to"], 6),
          round(rows["salt"]["cost_hi"] + rows["cya"]["cost_hi"], 6))


def t_a_price_setting_is_accepted_cleared_and_refused():
    """The four price boxes on Settings, through the writer's own table."""
    from . import config, server
    for name in config.PRICE_SETTINGS.values():
        conv = server.SETTABLE[name][2]
        check(f"{name}: a price is stored", conv("9.75"), 9.75)
        check(f"{name}: blank clears it", conv(" ") is server.CLEAR, True)
        check(f"{name}: zero is refused", conv("0"), None)


def _dose(day, chem, amount, unit, pct=""):
    d = START + dt.timedelta(days=day)
    return {"ts": f"{d.isoformat()}T10:00:00", "chemical": chem,
            "amount": str(amount), "unit": unit, "pct": str(pct)}


def t_the_ledger_hands_the_page_numbers_it_only_has_to_add():
    """Each dose in the unit it is sold by, priced at its own strength.

    32 fl oz is a quarter gallon; a pound is a pound. Acid at 31.45% takes the
    reference price, acid at 14.5% takes none -- it is not the acid that price
    was read for -- and an entered price beats the reference.
    """
    print("\n  upkeep — the ledger behind the by-month table")
    L = U.ledger([_dose(1, "acid", 32, "floz", 31.45),
                  _dose(2, "acid", 1, "gal", 14.5),
                  _dose(40, "bicarb", 4, "lb", 100)],
                 {"bicarb": 2.0}, when, num)
    d = L["doses"]
    check("a liquid is summed in gallons", d[0]["qty"], 0.25)
    check("priced at the reference for its strength",
          d[0]["cost"], round(0.25 * CH.REFERENCE_PRICES["acid"]["price"], 6))
    # MEASURED on the live page: a gallon in three doses read $9.76 at $9.75,
    # because each dose was rounded to the cent before the month summed them.
    split = U.ledger([_dose(1, "acid", 32, "floz", 31.45),
                      _dose(2, "acid", 32, "floz", 31.45),
                      _dose(3, "acid", 64, "floz", 31.45)], {}, when, num)
    check("a gallon in three doses costs what a gallon costs",
          round(sum(x["cost"] for x in split["doses"]), 2),
          CH.REFERENCE_PRICES["acid"]["price"])
    check("a weaker acid is left unpriced, not priced wrongly", d[1]["cost"], None)
    check("an entered price beats the reference", d[2]["cost"], 8.0)
    check("each dose carries its month", [x["ym"] for x in d],
          ["2026-09", "2026-09", "2026-10"])
    check("and the log's first month is known", L["first"], "2026-09")


def t_an_unreadable_dose_is_counted_not_dropped():
    L = U.ledger([_dose(1, "acid", 32, "floz", 31.45),
                  _dose(1, "unobtainium", 1, "lb"),
                  _dose(1, "salt", 1, "gal")], {}, when, num)
    check("only the readable dose is summed", len(L["doses"]), 1)
    check("and the two it could not read are counted", L["skipped"], 2)


def t_every_chemical_can_be_priced_and_named():
    """The ten-entry tables have to cover the catalogue, in the right unit.

    A chemical with no price setting has no box in Settings; one whose
    setting says per lb while it is sold by the gallon would cost every dose
    at a figure 128 times wrong. Compared against the catalogue, both ways.
    """
    from . import config
    cat = set(CH.CHEMICALS)
    check("every chemical has a price setting", set(config.PRICE_SETTINGS), cat)
    check("and a short name", set(CH.SHORT_NAMES), cat)
    check("and a reference price", set(CH.REFERENCE_PRICES), cat)
    wrong = [k for k in cat
             if not config.PRICE_SETTINGS[k].endswith("_per_" + CH.price_unit(k))
             or CH.REFERENCE_PRICES[k]["unit"] != CH.price_unit(k)]
    check("each priced in the unit it is sold by", wrong, [])
