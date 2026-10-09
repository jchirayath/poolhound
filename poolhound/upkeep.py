"""What keeping this pool balanced takes, by the week and by the year.

Estimated from this pool's own record rather than from a chart, and each figure
says which of three things it is: MEASURED (the record pins it down), BOUNDED
(the record says it is no more than this), or DERIVED (it follows from a
measured one by chemistry, not by fitting).

THE ACID FIGURE IS A MASS BALANCE, NOT A REGRESSION. THEORY.md writes pH as a
sum of per-hour terms -- the cell and the spillover per pump hour, the sheer
descent and the spa per hour of their own -- and a regression would recover
them only from a record in which they vary independently. This one does not:
the pump runs about six hours a day, every day, and the sheer and the spa
barely run at all. So the one question the record CAN answer is the plain one:
over a stretch that starts and ends on a pH reading, how much acid went in, and
where did pH end up against where it started. Acid in, plus the acid it would
have taken to cancel any net rise, is what holding pH cost over those pump
hours. One coefficient, per pump hour, with the sheer and the spa folded into
it and SAID to be -- until they run enough to price separately.

The yearly figure is that rate run for a year at the CURRENT runtime, and it
says so: a record that holds one season cannot know another, and warm water
off-gases faster.

THE ARITHMETIC IS NOT HERE. Every conversion between an amount and its effect
goes through chemicals.effects() or chemicals.amount_for(), which is the one
place the coefficients live. This module only decides which amounts to ask
about.

Pure, like the loaders it is fed by: the caller passes rows and the parsing
functions, the way chemicals.dose_response() is called, so this neither reads
a file nor imports render.
"""
import datetime as dt
import html
import math

from . import chemicals as CH

# How far back the record is read. Long enough to hold several dosing cycles,
# short enough that a schedule from last season does not set this one's rate.
WINDOW_DAYS = 120
# The shortest stretch a balance is drawn over. Under two weeks, one late or
# early dose is most of the answer.
MIN_SPAN_DAYS = 14
# Acid needs about a turnover to mix, so a reading taken sooner than this after
# a dose measures a gradient, not the pool. The same reason dose_response()
# waits; a longer wait here because the balance hangs on its two ends.
SETTLE_H = 6
# The runtime the weekly figure is quoted at: the mean of this many recent
# complete days.
RUNTIME_DAYS = 14
# The pod reports pH to a tenth. Each end of the balance can be out by half a
# step either way, read here as 0.1 on the pair -- the error that dominates
# when the stretch is short.
PH_STEP = 0.1
# A day's salt figure is the panel's mean while water flowed. On a day the pump
# ran less than this it is a few samples, and the cell's sensor is noisy.
SALT_MIN_PUMP_H = 2.0
SALT_MIN_POINTS = 14
SALT_MIN_SPAN = 21


# A whole day is 96 samples at fifteen minutes. Under 22 hours' worth, the
# pump block may be what is missing.
WHOLE_DAY_SAMPLES = 88


def coverage(samples, when):
    """How many panel samples each local day has, keyed by ISO date."""
    out = {}
    for r in samples:
        t = when(r.get("ts"))
        if t:
            k = t.date().isoformat()
            out[k] = out.get(k, 0) + 1
    return out


def _whole(day, cov):
    """Whether the panel was heard from for (nearly) all of that day.

    by_day() reports zero pump hours both for a day the pump was off and for a
    day no sample arrived, and a SHORT count for a day the agent was down for
    part of it. Only the first is runtime. MEASURED on this pool: asking merely
    whether a day had any sample counted the agent's 71-hour outage, and a day
    with 68 samples and one pump hour, as short pump days, and quoted the
    runtime at 4.7 hours a day while the pump ran six.
    """
    return cov.get(day["date"], 0) >= WHOLE_DAY_SAMPLES


def _acid_floz(row, ref_pct, num):
    """A logged acid dose, in fl oz at the strength the figures are quoted in."""
    base = CH.to_base("acid", num(row.get("amount")), row.get("unit") or "")
    pct = num(row.get("pct")) or ref_pct
    if base is None:
        return None
    return base * pct / ref_pct


def runtime(days, cov, today):
    """Mean pump hours a day over the recent days the panel was heard for whole."""
    done = [d for d in days if d["date"] < today.isoformat() and _whole(d, cov)]
    done.sort(key=lambda d: d["date"], reverse=True)
    recent = done[:RUNTIME_DAYS]
    if not recent:
        return None, 0
    return sum(d["hours"]["pump"] for d in recent) / len(recent), len(recent)


def acid(days, cov, doses, readings, gallons, ta, acid_pct, now, when, num):
    """Acid per pump hour, from the doses and the pH either side of them.

    Returns {"ok": False, "why": ...} when the record cannot answer yet, so the
    page says what is missing rather than printing a figure from too little.
    """
    if not gallons:
        return {"ok": False, "why": "the pool's volume is not set"}
    since = now - dt.timedelta(days=WINDOW_DAYS)
    ph = sorted((t, v) for t, v in
                ((when(r.get("measured")), num(r.get("ph"))) for r in readings)
                if t and v is not None and t >= since)
    acids = sorted((when(r.get("ts")), r) for r in doses
                   if (r.get("chemical") or "") == "acid" and when(r.get("ts")))
    acids = [(t, r) for t, r in acids if t >= since]
    if len(acids) < 2:
        return {"ok": False, "why": f"{_plural(len(acids), 'acid dose', 'acid doses')} "
                f"logged in the last {WINDOW_DAYS} days; a balance needs at "
                f"least two"}

    first = acids[0][0]
    before = [(t, v) for t, v in ph if t < first and first - t <= dt.timedelta(days=3)]
    if not before:
        return {"ok": False, "why": "no pH reading in the three days before the "
                "first acid dose, so there is nothing to start the balance from"}
    t0, ph0 = before[-1]
    # The end: the newest reading that is not within SETTLE_H of a dose before
    # it, and the doses counted are exactly those it has had time to see.
    end = None
    for t, v in reversed(ph):
        if t <= first:
            break
        recent = [a for a, _ in acids if a <= t and t - a < dt.timedelta(hours=SETTLE_H)]
        if not recent:
            end = (t, v)
            break
    if end is None:
        return {"ok": False, "why": "no settled pH reading after the doses yet"}
    t1, ph1 = end
    span = (t1 - t0).total_seconds() / 86400
    if span < MIN_SPAN_DAYS:
        return {"ok": False, "why": f"the record spans {span:.0f} days of dosing; "
                f"a balance needs {MIN_SPAN_DAYS}"}

    counted = [(t, r) for t, r in acids if t0 < t <= t1]
    amounts = [_acid_floz(r, acid_pct, num) for _, r in counted]
    if any(a is None for a in amounts):
        return {"ok": False, "why": "an acid dose has a unit that is not a volume"}
    acid_in = sum(amounts)

    # The net rise, priced as acid. If pH ended higher than it started, holding
    # it would have taken this much more; lower, this much less.
    per_floz = CH.effects("acid", 1, "floz", acid_pct, gallons, ta)["ph"]
    rise = ph1 - ph0
    demand = acid_in + rise / -per_floz
    slack = PH_STEP * math.sqrt(2) / -per_floz

    # Pump hours between the two readings. Both are taken in the evening, after
    # the day's pump block, so the hours that moved pH are those of the days
    # after the first reading up to and including the day of the last. A day
    # the panel was not heard from for whole is counted at the stretch's own
    # average, and the page says how many there were.
    lo_d, hi_d = t0.date().isoformat(), t1.date().isoformat()
    inside = [d for d in days if lo_d < d["date"] <= hi_d]
    heard = [d for d in inside if _whole(d, cov)]
    if not heard:
        return {"ok": False, "why": "no pump runtime was recorded over the stretch"}
    mean_h = sum(d["hours"]["pump"] for d in heard) / len(heard)
    n_days = (t1.date() - t0.date()).days
    holes = max(n_days - len(heard), 0)
    pump_h = sum(d["hours"]["pump"] for d in heard) + holes * mean_h
    if pump_h < 20:
        return {"ok": False, "why": f"only {pump_h:.0f} pump hours over the stretch"}

    rate = demand / pump_h
    held = [v for t, v in ph if t0 <= t <= t1]
    return {
        "ok": True, "pct": acid_pct,
        "start": t0, "end": t1, "ph_start": ph0, "ph_end": ph1,
        "doses": len(counted), "acid_in_floz": acid_in,
        "demand_floz": demand, "slack_floz": slack,
        "pump_h": pump_h, "holes": holes,
        "sheer_h": sum(d["hours"].get("sheer", 0) for d in heard),
        "spa_h": sum(d["hours"].get("spa", 0) for d in heard),
        "per_pump_h": rate,
        "per_pump_h_lo": max(demand - slack, 0) / pump_h,
        "per_pump_h_hi": (demand + slack) / pump_h,
        "held": sum(held) / len(held),
    }


def _ols(xs, ys):
    """Slope and its standard error, for a straight line through the points."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0 or n < 3:
        return None, None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    resid = sum((y - my - b * (x - mx)) ** 2 for x, y in zip(xs, ys))
    return b, math.sqrt(resid / (n - 2) / sxx)


def salt(days, doses, gallons, now, when, num):
    """How fast salt leaves, from the panel's daily figure.

    The cell does not consume salt: it splits chloride and the chlorine returns
    to chloride when it is used. Salt leaves only with water -- splash-out,
    backwash, rain overflowing, a drain -- so its loss is a measure of how much
    of the pool is being replaced, and the same fraction applies to stabiliser.

    Logged salt is subtracted first, so a bag added mid-stretch is not read as
    the pool gaining salt by itself. The answer carries a two-standard-error
    band, and when that band reaches zero the honest figure is the upper bound.
    """
    if not gallons:
        return {"ok": False, "why": "the pool's volume is not set"}
    since = (now - dt.timedelta(days=WINDOW_DAYS)).date().isoformat()
    today = now.date().isoformat()
    added = []
    for r in doses:
        if (r.get("chemical") or "") != "salt" or not when(r.get("ts")):
            continue
        e = CH.effects("salt", num(r.get("amount")), r.get("unit") or "",
                       num(r.get("pct")) or 100.0, gallons)
        if e:
            added.append((when(r.get("ts")).date().isoformat(), e["salt"]))
    pts = []
    for d in days:
        if not (since <= d["date"] < today) or d.get("salt") is None:
            continue
        if d["hours"]["pump"] < SALT_MIN_PUMP_H:
            continue
        before = sum(ppm for on, ppm in added if on < d["date"])
        pts.append((when(d["date"]).toordinal(), d["salt"] - before))
    if len(pts) < SALT_MIN_POINTS:
        return {"ok": False, "why": f"{len(pts)} days with a salt reading; "
                f"a trend needs {SALT_MIN_POINTS}"}
    span = max(x for x, _ in pts) - min(x for x, _ in pts)
    if span < SALT_MIN_SPAN:
        return {"ok": False, "why": f"salt readings span {span} days; "
                f"a trend needs {SALT_MIN_SPAN}"}
    b, se = _ols([x for x, _ in pts], [y for _, y in pts])
    if b is None:
        return {"ok": False, "why": "the salt readings do not vary in time"}
    level = sum(y for _, y in pts) / len(pts)
    loss, lo, hi = -b, max(-b - 2 * se, 0.0), max(-b + 2 * se, 0.0)
    return {
        "ok": True, "points": len(pts), "span": span, "level": level,
        "measured": lo > 0,
        "ppm_day": max(loss, 0.0), "ppm_day_lo": lo, "ppm_day_hi": hi,
        # The share of the water replaced each day, which is what carries over
        # to every other dissolved thing that nothing consumes.
        "frac_day": max(loss, 0.0) / level, "frac_day_lo": lo / level,
        "frac_day_hi": hi / level,
    }


def estimate(days, samples, doses, readings, gallons, ta, acid_pct, cya_aim,
             now, when, num, prices=None):
    """Every upkeep figure the record supports, in the units they are bought in.

    Rows are {"key", "label", "week", "year", "year_lo", "year_hi", "unit",
    "kind", "basis"}, with week/year None where there is no figure, and `kind`
    one of measured / bounded / derived / none / unknown. Each row is also
    costed -- "cost_year", "cost_lo", "cost_hi" and the "price" used -- from
    `prices`, the household's own per-unit prices keyed by chemical, falling
    back to the labelled reference.
    """
    cov = coverage(samples, when)
    hours, n_rt = runtime(days, cov, now.date())
    a = acid(days, cov, doses, readings, gallons, ta, acid_pct, now, when, num)
    s = salt(days, doses, gallons, now, when, num)
    out = {"runtime_h": hours, "runtime_days": n_rt, "acid": a, "salt": s,
           "rows": [], "gallons": gallons}
    rows = out["rows"]

    if a["ok"] and hours:
        wk = a["per_pump_h"] * hours * 7
        yr = a["per_pump_h"] * hours * 365
        rows.append(dict(
            key="acid", label=f"Muriatic acid, {acid_pct:g}%", unit="gal",
            week=wk / 128, year=yr / 128,
            year_lo=a["per_pump_h_lo"] * hours * 365 / 128,
            year_hi=a["per_pump_h_hi"] * hours * 365 / 128,
            kind="measured",
            basis=(f"{a['acid_in_floz'] / 128:.2f} gal went in over "
                   f"{(a['end'] - a['start']).days} days and pH went from "
                   f"{a['ph_start']:.1f} to {a['ph_end']:.1f}")))
        out["acid_per_extra_hour_year"] = a["per_pump_h"] * 365 / 128

        # Acid lowers alkalinity as well as pH, and the aeration it is fighting
        # raises pH WITHOUT raising alkalinity back. So holding pH with acid
        # drains alkalinity at a rate the acid figure fixes, and that is what
        # bicarbonate replaces. Derived, not fitted.
        ta_wk = -CH.effects("acid", wk, "floz", acid_pct, gallons, ta)["ta"]
        lb_per_ppm = CH.amount_for("bicarb", "ta", 1.0, gallons)
        rows.append(dict(
            key="bicarb", label="Baking Soda/Alkalinity Up", unit="lb",
            week=ta_wk * lb_per_ppm, year=ta_wk / 7 * 365 * lb_per_ppm,
            year_lo=None, year_hi=None, kind="derived",
            basis=(f"replaces the {ta_wk:.0f} ppm of alkalinity a week the acid "
                   f"takes out")))
    else:
        rows.append(dict(key="acid", label=f"Muriatic acid, {acid_pct:g}%",
                         unit="gal", week=None, year=None, year_lo=None,
                         year_hi=None, kind="unknown",
                         basis=a.get("why") if not a["ok"] else
                         "no recent pump runtime to quote it at"))

    if s["ok"]:
        lb_ppm = CH.amount_for("salt", "salt", 1.0, gallons)
        yr, lo, hi = (s[k] * 365 * lb_ppm for k in ("ppm_day", "ppm_day_lo", "ppm_day_hi"))
        rows.append(dict(
            key="salt", label="Pool salt", unit="lb",
            week=yr / 52.14 if s["measured"] else None, year=yr if s["measured"] else None,
            year_lo=lo, year_hi=hi,
            kind="measured" if s["measured"] else "bounded",
            basis=(f"the panel's salt over {s['span']} days, {s['points']} readings, "
                   f"averaging {s['level']:.0f} ppm")))
        if cya_aim:
            lb_c = CH.amount_for("cya", "cya", 1.0, gallons)
            f = {k: s[k] * 365 * cya_aim * lb_c
                 for k in ("frac_day", "frac_day_lo", "frac_day_hi")}
            rows.append(dict(
                key="cya", label="Stabiliser (cyanuric acid)", unit="lb",
                week=f["frac_day"] / 52.14 if s["measured"] else None,
                year=f["frac_day"] if s["measured"] else None,
                year_lo=f["frac_day_lo"], year_hi=f["frac_day_hi"],
                kind="derived" if s["measured"] else "bounded",
                basis=(f"leaves with the same water the salt does, held at "
                       f"{cya_aim:g} ppm")))
    else:
        rows.append(dict(key="salt", label="Pool salt", unit="lb", week=None,
                         year=None, year_lo=None, year_hi=None, kind="unknown",
                         basis=s["why"]))

    rows.append(dict(key="chlorine", label="Chlorine", unit="", week=0.0,
                     year=0.0, year_lo=None, year_hi=None, kind="none",
                     basis="the salt cell makes it from the salt"))
    _price(rows, prices or {}, acid_pct)
    out["cost_year"] = sum(r["cost_year"] for r in rows
                           if r.get("cost_year") is not None)
    out["cost_up_to"] = sum(r["cost_hi"] for r in rows
                            if r["kind"] == "bounded" and r.get("cost_hi") is not None)
    out["priced"] = any(r.get("price") for r in rows)
    return out


def price_for(chem, own, pct):
    """The price a figure is costed at, and whose it is.

    The household's own when one was entered; otherwise the labelled reference
    from chemicals.py; otherwise None. A reference price that names a strength
    -- acid's does -- is for that strength and is not applied to any other.
    """
    if own:
        return {"price": own, "own": True, "source": "your price"}
    ref = CH.REFERENCE_PRICES.get(chem)
    if not ref:
        return None
    if "pct" in ref and abs(ref["pct"] - pct) > 0.01:
        return None
    return {"price": ref["price"], "own": False,
            "source": f"typical retail: {ref['source']}, "
                      f"{CH.REFERENCE_PRICES_AS_OF}"}


def ledger(doses, prices, when, num):
    """Every logged dose as month, amount and cost -- what the browser sums.

    The by-month table on the Chemicals tab takes a period the reader picks,
    so the grouping happens in the page. Everything that is arithmetic about a
    chemical happens HERE, once: the unit is converted to the one the jug or
    bag is sold by, the dose is priced at its own strength, and the page is
    handed numbers it only has to add. A dose that cannot be read -- an
    unknown chemical, a unit that does not fit it -- is counted, not dropped,
    so the page can say how many it left out.
    """
    out, chems, skipped = [], {}, 0
    for r in doses:
        t = when(r.get("ts"))
        chem = r.get("chemical") or ""
        if not t or chem not in CH.CHEMICALS:
            skipped += 1
            continue
        base = CH.to_base(chem, num(r.get("amount")), r.get("unit") or "")
        if base is None:
            skipped += 1
            continue
        unit = CH.price_unit(chem)
        qty = base / 128.0 if unit == "gal" else base
        pct = num(r.get("pct")) or CH.CHEMICALS[chem]["default_pct"]
        p = price_for(chem, (prices or {}).get(chem), pct)
        out.append({"ym": t.strftime("%Y-%m"), "chem": chem,
                    "qty": round(qty, 4),
                    # Not rounded to the cent here: the page sums these, and
                    # three doses rounded first made 1.00 gal at $9.75 a
                    # gallon read $9.76. Rounded once, where it is shown.
                    "cost": round(qty * p["price"], 6) if p else None})
        if chem not in chems:
            chems[chem] = {"name": CH.SHORT_NAMES[chem], "unit": unit}
        if p:
            chems[chem]["price"] = p["price"]
            chems[chem]["source"] = p["source"]
    return {
        "doses": sorted(out, key=lambda d: d["ym"]),
        "chems": chems,
        # Columns in the catalogue's order, so the table does not reshuffle
        # with whichever chemical happened to be logged first.
        "order": [k for k in CH.CHEMICALS if k in chems],
        "first": min((d["ym"] for d in out), default=None),
        "skipped": skipped,
    }


def _price(rows, prices, acid_pct):
    """Cost each row a year, and its range, where it has a price."""
    for r in rows:
        r["cost_year"] = r["cost_lo"] = r["cost_hi"] = None
        p = price_for(r["key"], prices.get(r["key"]), acid_pct)
        r["price"] = p
        if not p:
            continue
        if r["year"] is not None:
            r["cost_year"] = r["year"] * p["price"]
        if r.get("year_lo") is not None:
            r["cost_lo"] = r["year_lo"] * p["price"]
        if r.get("year_hi") is not None:
            r["cost_hi"] = r["year_hi"] * p["price"]


# --------------------------------------------------------------------- the page

def _amt(v, unit, week=False):
    """An amount at the precision the record has: a week's acid to a hundredth
    of a gallon, a year's to a tenth under ten and to the gallon above."""
    if v is None:
        return "—"
    if unit == "gal":
        return f"{v:.2f} gal" if week else f"{v:.1f} gal" if v < 10 else f"{v:.0f} gal"
    if unit == "lb":
        return f"{v:.1f} lb" if week or v < 10 else f"{v:.0f} lb"
    return "none"


def _plural(n, one, many):
    return f"{n} {one if n == 1 else many}"


def _year_cell(r):
    if r["kind"] == "none":
        return "none"
    if r["kind"] == "bounded":
        return f"at most {_amt(r['year_hi'], r['unit'])}"
    if r["year"] is None:
        return "—"
    cell = _amt(r["year"], r["unit"])
    if r.get("year_lo") is not None and r.get("year_hi") is not None:
        cell += (f'<br>{_amt(r["year_lo"], r["unit"])} to '
                 f'{_amt(r["year_hi"], r["unit"])}')
    return cell


_KIND = {"measured": "measured", "bounded": "not measurable yet",
         "none": "not needed", "unknown": "not enough record"}
# A derived figure says what it is derived FROM, which differs by row.
_FROM = {"bicarb": "follows from the acid", "cya": "follows from the salt"}


def _how(r):
    if r["kind"] == "derived":
        return _FROM[r["key"]]
    return _KIND[r["kind"]]


def _money(v):
    return f"${v:,.0f}" if v >= 10 else f"${v:,.2f}"


def _cost_cell(r):
    """A year's cost at the row's price, in the same shape as its amount."""
    if r["kind"] == "none":
        return "$0"
    if r["kind"] == "unknown":
        return "—"
    if not r.get("price"):
        return "no price"
    if r["kind"] == "bounded":
        return f"up to {_money(r['cost_hi'])}" if r.get("cost_hi") is not None else "—"
    cell = _money(r["cost_year"])
    if r.get("cost_lo") is not None and r.get("cost_hi") is not None:
        cell += f"<br>{_money(r['cost_lo'])} to {_money(r['cost_hi'])}"
    return cell


def _prices_note(est):
    """Whose price each cost used, so a reference is never read as a receipt."""
    used = [(r, r["price"]) for r in est["rows"]
            if r.get("price") and r["kind"] not in ("none", "unknown")]
    if not used:
        return ""
    parts = [f"{html.escape(r['label'])} at {_money(p['price'])} a {r['unit']} "
             f"({html.escape(p['source'])})" for r, p in used]
    tail = ("" if all(p["own"] for _, p in used) else
            " Enter what you actually pay on the Settings tab and these "
            "become your own figures.")
    return (f"<b>Priced at</b> {'; '.join(parts)}.{tail}")


def block(est):
    """The section body: a table, then what each figure rests on."""
    esc = html.escape
    rows = "".join(
        f'<tr><td><b>{esc(r["label"])}</b></td>'
        f'<td class="n">{"none" if r["kind"] == "none" else _amt(r["week"], r["unit"], week=True)}</td>'
        f'<td class="n">{_year_cell(r)}</td>'
        f'<td class="n">{_cost_cell(r)}</td>'
        f'<td><b>{esc(_how(r).capitalize())}:</b> {esc(r["basis"])}</td></tr>'
        for r in est["rows"])
    if est.get("priced"):
        up_to = (f" + up to {_money(est['cost_up_to'])}"
                 if est.get("cost_up_to") else "")
        rows += (f'<tr><td><b>A year, all told</b></td><td></td><td></td>'
                 f'<td class="n"><b>{_money(est["cost_year"])}</b>{up_to}</td>'
                 f'<td>what is measured or follows from it'
                 f'{", plus the most the unmeasured rows could cost" if up_to else ""}'
                 f'</td></tr>')
    p = [f'<div class="card"><div class="scroll"><table>'
         f'<thead><tr><th>Chemical</th><th>Per week</th><th>Per year</th>'
         f'<th>Cost a year</th><th>How it is known</th></tr></thead>'
         f'<tbody>{rows}</tbody></table></div>']

    h, a, s = est["runtime_h"], est["acid"], est["salt"]
    notes = []
    if h is not None:
        notes.append(
            f"<b>Quoted at {h:.1f} pump hours a day</b>, the average of the last "
            f"{est['runtime_days']} complete days. Acid scales with runtime: "
            + (f"each extra hour a day adds about "
               f"{est['acid_per_extra_hour_year']:.1f} gal a year."
               if "acid_per_extra_hour_year" in est else "")
        )
    if a["ok"]:
        folded = []
        if a["sheer_h"] < 20:
            folded.append(f"the sheer descent ran {a['sheer_h']:.0f} h")
        if a["spa_h"] < 10:
            folded.append(f"the spa ran {a['spa_h']:.0f} h")
        notes.append(
            f"<b>The acid figure is a balance, not a guess.</b> Between the pH "
            f"reading of {a['start']:%-d %b} and that of {a['end']:%-d %b}, "
            f"{a['doses']} doses added {a['acid_in_floz'] / 128:.2f} gal and pH "
            f"moved {a['ph_end'] - a['ph_start']:+.1f}, over {a['pump_h']:.0f} pump "
            f"hours. The range is what a tenth of a pH unit of reading error at "
            f"either end is worth. pH was held around {a['held']:.1f}; holding it "
            f"lower takes more, because the further pH sits below where aeration "
            f"stops pushing it, the faster it climbs."
            + (f" {_plural(a['holes'], 'day', 'days')} in that stretch "
               f"{'has' if a['holes'] == 1 else 'have'} an incomplete panel "
               f"record, counted at the stretch's average runtime."
               if a["holes"] else ""))
        if folded:
            many = len(folded) > 1
            notes.append(
                f"<b>Aeration is inside that rate, not beside it.</b> Over the "
                f"stretch {' and '.join(folded)} — too little to price "
                f"separately, so {'their' if many else 'its'} share is folded "
                f"into the per-pump-hour figure. More of {'either' if many else 'it'} "
                f"will raise acid use, and once it runs enough the record can "
                f"say by how much.")
    if s["ok"] and not s["measured"]:
        notes.append(
            f"<b>Salt is not leaving fast enough to see yet.</b> The cell does not "
            f"use salt up; it leaves only with water. Over {s['span']} days the "
            f"panel's daily figure scatters more than any trend in it, so what the "
            f"record supports is an upper bound, and stabiliser, which leaves with "
            f"the same water, is bounded the same way.")
    notes.append(
        "<b>One season, run for a year.</b> The yearly figures are this record's "
        "rate continued for twelve months. Warmer water gives off CO₂ faster "
        "and a summer schedule runs the pump longer, so a summer will cost more "
        "acid than these figures say.")
    if _prices_note(est):
        notes.insert(0, _prices_note(est))
    p.append("".join(f'<p class="sub" style="margin:12px 0 0">{n}</p>' for n in notes))
    p.append("</div>")
    return "".join(p)


def context_lines(est):
    """The same figures for the assistant, as plain lines."""
    out = []
    h = est["runtime_h"]
    if h is not None:
        out.append(f"  quoted at {h:.1f} pump hours a day")
    for r in est["rows"]:
        if r["kind"] == "none":
            out.append(f"  {r['label']}: none needed ({r['basis']})")
        elif r["kind"] == "unknown":
            out.append(f"  {r['label']}: not known yet ({r['basis']})")
        elif r["kind"] == "bounded":
            cost = (f", up to {_money(r['cost_hi'])}"
                    if r.get("cost_hi") is not None else "")
            out.append(f"  {r['label']}: at most {_amt(r['year_hi'], r['unit'])} a year"
                       f"{cost} (no measurable loss; {r['basis']})")
        else:
            cost = (f", {_money(r['cost_year'])} a year"
                    if r.get("cost_year") is not None else "")
            out.append(f"  {r['label']}: {_amt(r['week'], r['unit'], True)} a week, "
                       f"{_amt(r['year'], r['unit'])} a year{cost} "
                       f"({r['kind']}; {r['basis']})")
    if est.get("priced"):
        out.append(f"  total a year: {_money(est['cost_year'])}"
                   + (f" plus up to {_money(est['cost_up_to'])} unmeasured"
                      if est.get("cost_up_to") else ""))
        out += [f"  {r['label']} priced at {_money(r['price']['price'])} a "
                f"{r['unit']} ({r['price']['source']})" for r in est["rows"]
                if r.get("price") and r["kind"] not in ("none", "unknown")]
    return out
