"""What is on the shelf, and how long it lasts at this pool's rate.

"About three weeks of acid left" is two facts multiplied: how much there is,
and how fast it goes. The second already exists -- upkeep.estimate() works it
out from this pool's own record and says whether it is MEASURED, BOUNDED or
DERIVED -- so this module only has to know the first, and has to know it
honestly.

WHY A STOCKTAKE, AND NOT ONLY PURCHASES
  On hand could be computed from purchases minus logged doses alone, and that
  figure would drift for ever: a dose somebody forgot to log is a jug that
  never empties on paper, and a dose logged twice is one that empties twice.
  Nothing in the record can ever pull it back. A count of what is actually on
  the shelf can, so the rule is

      on hand = the last stocktake
              + purchases after it
              - logged doses after it

  and a stocktake is the reset. Without one, the baseline is the first
  purchase, and doses before it are not counted -- they came out of stock
  that was never recorded, and subtracting them would make a full shelf read
  as an empty one.

  "After" is strict. A stocktake is what was there at that moment, so a dose
  or a purchase stamped at the same minute is taken as already in the count.

A NEGATIVE IS A FINDING, NOT A QUANTITY
  If the arithmetic comes out below zero the record says more was poured than
  was ever bought, which cannot be true of a shelf. The page does not print a
  negative number of gallons; it says the record disagrees with itself and
  asks for a count, because a count is the only thing that settles it.

PHYSICAL QUANTITIES, IN THE UNIT IT IS SOLD BY
  Stock is what is in the jug, whatever its strength: two gallons of 14.5%
  acid are two gallons on the shelf. Doses subtract the same way. Only the
  RATE is strength-dependent -- upkeep quotes acid at the configured strength,
  and weaker acid on the shelf is used up proportionally faster -- and that
  one conversion is made here and said.

THE ARITHMETIC IS NOT HERE EITHER. Units convert through chemicals.to_base(),
and whether an amount is plausible is chemicals.plausible_stock(), so `bin/chem
stocktake salt 1 gal` is refused for the reason `bin/chem salt 1 gal` is.

Pure, like upkeep: the caller passes rows and the parsing functions.
"""
import datetime as dt
import html

from . import chemicals as CH

FILE = "stock.csv"

# The shape of stock.csv, in one place: bin/chem and the server both write it
# and both import this. A file of its own rather than rows in chemicals.csv,
# because every reader of that file -- the acid balance, the dose response,
# the by-month ledger, the assistant -- takes each row as something poured into
# the pool. A purchase row there would be a dose to every reader that forgot
# to filter it out, and the acid balance would price a case of jugs on the
# shelf as acid that went into the water.
#
# `paid` is what the receipt says for this line, in money, not per unit: that
# is the number a person has in their hand. The unit price is derived.
# `id` for the reason chemicals.CHEM_COLS gives: a timestamp is not an
# identity, and every row here is editable.
COLS = ["ts", "kind", "chemical", "amount", "unit", "pct", "paid", "note",
        "by", "id"]

# What each kind of row is called on the page.
KINDS = {"purchase": "Bought", "stocktake": "Counted"}

# How the rate behind "weeks left" is known. upkeep's own vocabulary, plus one:
# a rate read straight off the logged doses, which is what was POURED and says
# nothing about what the pool needed.
_HOW = {"measured": "measured", "derived": "derived", "bounded": "at most",
        "logged": "from logged doses", "none": "not used up"}

# The shortest stretch of logged doses a rate is read from, and how far back.
LOGGED_MIN_DAYS = 14
LOGGED_WINDOW_DAYS = 120
# Past this, "how long" is said as "more than two years" rather than a date.
LONG_WEEKS = 104


# --------------------------------------------------------------- validation

def validate(data, now, when, skew, row=None):
    """The fields of one stock row, checked, or (None, why).

    `when` and `skew` are render.when and render.CLOCK_SKEW, passed in: the one
    parser and the one tolerance for "in the future", which a dose and a reading
    are held to as well.

    `row` is the existing row on an edit: anything not sent keeps its value,
    and the result runs the same checks a new row does -- an edit that could
    write a unit the CLI refuses would be a second, weaker validator.
    """
    row = dict(row or {})

    def got(k):
        v = data.get(k)
        return row.get(k, "") if v is None else v

    kind = str(got("kind") or "").strip()
    if kind not in KINDS:
        return None, "say whether this is a purchase or a stocktake"
    chem = str(got("chemical") or "").strip()
    if chem not in CH.CHEMICALS:
        return None, f"unknown chemical {chem!r}"
    unit = str(got("unit") or "").strip()
    amount = got("amount")
    ok, why = CH.plausible_stock(chem, amount, unit, zero_ok=kind == "stocktake")
    if not ok:
        return None, why
    amount = float(amount)

    raw_pct = got("pct")
    try:
        pct = (float(raw_pct) if str(raw_pct).strip() != ""
               else CH.CHEMICALS[chem]["default_pct"])
    except (TypeError, ValueError):
        return None, "strength is not a number"
    if not (0 < pct <= 100):
        return None, "strength must be between 0 and 100"

    paid = str(got("paid") if kind == "purchase" else "").strip()
    if paid:
        try:
            p = float(paid.lstrip("$"))
        except ValueError:
            return None, "what was paid has to be a number"
        if not (0 < p < 100000) or p != p:
            return None, "what was paid has to be more than zero"
        paid = f"{p:.2f}"

    ts = str(got("ts") or "").strip()[:40]
    t = when(ts) if ts else now
    if t is None:
        return None, (f"could not read the time {ts!r} — expected "
                      f"YYYY-MM-DDTHH:MM. Leave it empty to record it as now.")
    if t > now + skew:
        return None, "that time is in the future"

    row.update({"ts": t.astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
                "kind": kind, "chemical": chem, "amount": f"{amount:g}",
                "unit": unit, "pct": f"{pct:g}", "paid": paid,
                "note": str(got("note") or "").strip()[:200]})
    return row, None


def describe(row):
    """One row as a sentence, for the audit trail and the CLI."""
    paid = f" for ${row['paid']}" if row.get("paid") else ""
    verb = "bought" if row.get("kind") == "purchase" else "counted"
    return (f"{verb} {row.get('amount')} {row.get('unit')} {row.get('chemical')}"
            f" @ {row.get('pct')}%{paid}")


# --------------------------------------------------------------- arithmetic

def _base(row, num):
    a = num(row.get("amount"))
    if a is None:
        return None
    return CH.to_base(row.get("chemical") or "", a, row.get("unit") or "")


def on_hand(chem, stock_rows, doses, when, num):
    """What the record says is on the shelf, in base units, or None.

    None when nothing has been recorded for `chem` -- not zero, which would be
    a claim that the shelf is empty. The result carries how it was reached,
    so the page can say "since the count on 3 Oct" rather than just a figure.
    """
    mine = sorted(((when(r.get("ts")), r) for r in stock_rows
                   if (r.get("chemical") or "") == chem
                   and r.get("kind") in KINDS and when(r.get("ts"))),
                  key=lambda p: p[0])
    skipped = 0
    good = []
    for t, r in mine:
        b = _base(r, num)
        if b is None:
            skipped += 1
        else:
            good.append((t, r, b))
    if not good:
        return None

    counts = [g for g in good if g[1]["kind"] == "stocktake"]
    if counts:
        t0, r0, b0 = counts[-1]
        start, frm, base = t0, "stocktake", b0
        bought = [g for g in good if g[1]["kind"] == "purchase" and g[0] > t0]
        used_after = lambda t: t > t0
    else:
        t0 = good[0][0]
        start, frm, base = t0, "purchases", 0.0
        bought = [g for g in good if g[1]["kind"] == "purchase"]
        # The first purchase is in the baseline; a dose stamped the same
        # minute is taken as poured from it.
        used_after = lambda t: t >= t0
    added = sum(b for _, _, b in bought)

    used, n_doses, bad_doses = 0.0, 0, 0
    for d in doses:
        if (d.get("chemical") or "") != chem:
            continue
        t = when(d.get("ts"))
        if not t or not used_after(t):
            continue
        b = _base(d, num)
        if b is None:
            bad_doses += 1
            continue
        used += b
        n_doses += 1

    latest = good[-1][1]
    return {"chem": chem, "base": base + added - used,
            "start": start, "from": frm, "counted": base if frm == "stocktake" else None,
            "bought": added, "purchases": len(bought),
            "used": used, "doses": n_doses,
            "skipped": skipped + bad_doses,
            "pct": num(latest.get("pct")) or CH.CHEMICALS[chem]["default_pct"]}


def _logged_rate(chem, doses, now, when, num):
    """Base units a day from the doses themselves, or None.

    The inter-dose estimator: every dose but the newest, over the time from
    the first to the newest. Counting the newest as well would charge the
    stretch with a dose whose interval has not happened yet, and a fresh jug
    poured yesterday would double the rate.
    """
    since = now - dt.timedelta(days=LOGGED_WINDOW_DAYS)
    ds = sorted((when(d.get("ts")), d) for d in doses
                if (d.get("chemical") or "") == chem and when(d.get("ts")))
    ds = [(t, d) for t, d in ds if since <= t <= now]
    if len(ds) < 2:
        return None
    span = (ds[-1][0] - ds[0][0]).total_seconds() / 86400
    if span < LOGGED_MIN_DAYS:
        return None
    amounts = [_base(d, num) for _, d in ds[:-1]]
    if any(a is None for a in amounts):
        return None
    return {"per_day": sum(amounts) / span, "kind": "logged",
            "basis": f"{len(ds)} doses logged over {span:.0f} days"}


def rate(chem, est, doses, now, when, num, stock_pct=None, acid_pct=None):
    """How fast `chem` leaves the shelf, in base units a day.

    upkeep's figure where it has one -- it is what the pool NEEDS, which is
    what the shelf will be asked for -- then the logged doses, which are what
    was poured. A bounded figure is used at its ceiling, so the time it gives
    is a floor and is said as one.
    """
    row = next((r for r in (est or {}).get("rows", []) if r["key"] == chem), None)
    if row and row["kind"] in ("measured", "derived", "bounded"):
        per_year = row["year"] if row["kind"] != "bounded" else row.get("year_hi")
        if per_year is not None:
            scale = 128.0 if row["unit"] == "gal" else 1.0
            per_day = per_year * scale / 365.0
            basis = row["basis"]
            if chem == "acid" and stock_pct and acid_pct and abs(stock_pct - acid_pct) > 0.01:
                # upkeep quotes acid at the configured strength. A weaker jug
                # does less per gallon, so it empties faster in proportion.
                per_day *= acid_pct / stock_pct
                basis += (f"; quoted at {acid_pct:g}% and converted to the "
                          f"{stock_pct:g}% on the shelf")
            return {"per_day": per_day, "kind": row["kind"], "basis": basis}
    logged = _logged_rate(chem, doses, now, when, num)
    if logged:
        return logged
    if row and row["kind"] == "none":
        return {"per_day": 0.0, "kind": "none", "basis": row["basis"]}
    why = (row or {}).get("basis") if row and row["kind"] == "unknown" else None
    return {"per_day": None, "kind": "unknown",
            "basis": (why + "; and " if why else "") +
                     f"fewer than two doses logged over {LOGGED_MIN_DAYS} days"}


def summary(stock_rows, doses, est, now, when, num, acid_pct=None,
            low_weeks=3.0):
    """Every chemical with a stock record: on hand, rate, and how long.

    Rows are in the catalogue's order. `state` is one of
      overdrawn   the record says more was used than bought
      empty       counted, or worked out, at nothing
      low         fewer than `low_weeks` weeks left at a rate that is not a bound
      ok          more than that
      lasting     the rate is zero -- the salt cell makes it, say
      no-rate     no rate to divide by yet
    """
    out = []
    for chem in CH.CHEMICALS:
        h = on_hand(chem, stock_rows, doses, when, num)
        if h is None:
            continue
        r = rate(chem, est, doses, now, when, num, h["pct"], acid_pct)
        unit = CH.price_unit(chem)
        scale = 128.0 if unit == "gal" else 1.0
        e = dict(h, unit=unit, qty=h["base"] / scale, rate=r,
                 name=CH.SHORT_NAMES[chem], weeks=None, until=None)
        e["per_week"] = (r["per_day"] * 7 / scale) if r["per_day"] else r["per_day"]
        if h["base"] < -1e-6:
            e["state"] = "overdrawn"
        elif h["base"] <= 1e-6:
            e["state"] = "empty"
        elif r["per_day"] is None:
            e["state"] = "no-rate"
        elif r["per_day"] == 0:
            e["state"] = "lasting"
        else:
            days = h["base"] / r["per_day"]
            e["weeks"] = days / 7
            e["until"] = now + dt.timedelta(days=days)
            e["state"] = ("low" if r["kind"] != "bounded" and e["weeks"] < low_weeks
                          else "ok")
        out.append(e)
    return out


# Home's priority for a shelf running low: "keep an eye on". Home's badge
# counts only what is actionable now, and a badge lit by the shopping list is a
# badge nobody reads -- nothing is wrong with the water.
HOME_PRI = "watch"


def needs_buying(rows):
    """The entries worth an action on Home: low, empty, or a record that has
    gone negative. A bound is never low on its own say-so."""
    return [e for e in rows if e["state"] in ("low", "empty", "overdrawn")]


def household_prices(settings, stock_rows, acid_pct, when, num):
    """What this household pays, per chemical: Settings first, then the
    newest purchase that recorded a price, else nothing.

    Settings wins because it is a deliberate statement -- somebody typed what
    they pay -- where a receipt is one shop on one day. A purchase is still
    far better than a reference price, so it fills every gap Settings leaves,
    and says which purchase it came from.

    A purchase of acid at a strength other than the one prices are quoted at
    is not used: half-strength acid at nearly the same price per gallon is
    not the price of the acid the figures are about.
    """
    out = {}
    for chem in CH.CHEMICALS:
        own = (settings or {}).get(chem)
        if own:
            out[chem] = own
            continue
        best = None
        for r in stock_rows:
            if r.get("kind") != "purchase" or (r.get("chemical") or "") != chem:
                continue
            paid, t, b = num(r.get("paid")), when(r.get("ts")), _base(r, num)
            if not paid or not t or not b:
                continue
            if chem == "acid" and acid_pct and abs((num(r.get("pct")) or 0) - acid_pct) > 0.01:
                continue
            if best is None or t > best[0]:
                best = (t, paid, b)
        if best:
            t, paid, b = best
            qty = b / (128.0 if CH.price_unit(chem) == "gal" else 1.0)
            out[chem] = {"price": paid / qty,
                         "source": f"what you paid, {t:%-d %b %Y}"}
    return out


# --------------------------------------------------------------------- page

def _qty(v, unit):
    if unit == "gal":
        return f"{v:.2f} gal" if v < 10 else f"{v:.1f} gal"
    return f"{v:.1f} lb" if v < 100 else f"{v:.0f} lb"


def _weeks(w):
    if w < 1:
        return f"{w * 7:.0f} days"
    return f"{w:.1f} weeks" if w < 10 else f"{w:.0f} weeks"


# The pill classes the tiles already use, so a state here reads in the same
# colour as the same judgement elsewhere -- and every one carries its word.
_STATE = {"overdrawn": ("p-bad", "check"), "empty": ("p-bad", "empty"),
          "low": ("p-low", "low"), "ok": ("p-ok", "enough"),
          "lasting": ("p-ok", "lasting"), "no-rate": ("p-unknown", "unknown")}


def left_cell(e):
    """The "how long" cell. Never a negative quantity; never a bare figure."""
    s = e["state"]
    if s == "overdrawn":
        return ("The record says you have used more than you recorded buying; "
                "take a stocktake.")
    if s == "empty":
        return "None left."
    if s == "lasting":
        return "Not being used up."
    if s == "no-rate":
        return "Not known yet: no rate of use to divide by."
    if e["weeks"] > LONG_WEEKS:
        # A date in 2072 is the arithmetic, not a forecast: no rate read off
        # one season holds for decades, and the jug will have gone off first.
        return "More than two years at this rate."
    lead = "at least " if e["rate"]["kind"] == "bounded" else "about "
    return f"{lead}{_weeks(e['weeks'])}, until around {e['until']:%-d %b %Y}"


def on_hand_cell(e):
    if e["state"] == "overdrawn":
        return "—"
    return _qty(max(e["qty"], 0.0), e["unit"])


def rate_cell(e):
    r = e["rate"]
    if r["per_day"] is None:
        return "—"
    if r["per_day"] == 0:
        return "none"
    return f"{'up to ' if r['kind'] == 'bounded' else ''}{_qty(e['per_week'], e['unit'])} a week"


def basis_cell(e):
    """Where the on-hand figure comes from, then where the rate does."""
    frm = (f"counted {e['start']:%-d %b}" if e["from"] == "stocktake" else
           f"no count yet; from the first purchase, {e['start']:%-d %b}")
    bits = [frm]
    if e["purchases"] and e["from"] == "stocktake":
        bits.append(f"{e['purchases']} bought since")
    elif e["purchases"] and e["from"] != "stocktake":
        bits.append(f"{e['purchases']} purchase{'' if e['purchases'] == 1 else 's'}")
    if e["doses"]:
        bits.append(f"{e['doses']} dose{'' if e['doses'] == 1 else 's'} logged since")
    if e["skipped"]:
        bits.append(f"{e['skipped']} entr{'y' if e['skipped'] == 1 else 'ies'} "
                    f"with a unit that does not fit, left out")
    r = e["rate"]
    return ("; ".join(bits) + f". <b>Rate ({html.escape(_HOW.get(r['kind'], 'not known'))}):</b> "
            + html.escape(r["basis"]))


def block(rows, low_weeks):
    """The table of what is on hand, or the sentence saying how to start one."""
    esc = html.escape
    if not rows:
        return ('<p class="empty">Nothing recorded yet. Record a purchase when '
                'you buy something, or count what is on the shelf now and '
                'record that as a stocktake — either one starts the figure, '
                'and the doses you log afterwards are taken off it.</p>')
    body = []
    for e in rows:
        cls, word = _STATE[e["state"]]
        body.append(
            f'<tr class="st-{e["state"]}"><td><b>{esc(e["name"])}</b></td>'
            f'<td class="n">{on_hand_cell(e)}</td>'
            f'<td class="n">{rate_cell(e)}</td>'
            # The word travels with the colour: never colour alone.
            f'<td><span class="pill {cls}">{esc(word)}</span> {esc(left_cell(e))}</td>'
            f'<td class="sub">{basis_cell(e)}</td></tr>')
    return (f'<div class="scroll"><table id="stock-table">'
            f'<caption class="sr">What is on hand, how fast it is used, and how '
            f'long it lasts</caption>'
            f'<thead><tr><th>Chemical</th><th>On hand</th><th>Use</th>'
            f'<th>Lasts</th><th>How it is known</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>'
            f'<p class="sub" style="margin:10px 0 0">"Low" is under '
            f'{low_weeks:g} weeks at a rate that is measured, derived or read '
            f'from your doses. A rate that is only a ceiling gives a time that '
            f'is a floor, so it says "at least" and is never called low. Set '
            f'the threshold as <code>[stock] low_weeks</code> in config.toml.</p>')


def action_text(e):
    """Title and detail for a Home action, in the page's own voice."""
    if e["state"] == "overdrawn":
        return (f"Count the {e['name'].lower()}",
                "The record says you have used more than you recorded buying; "
                "take a stocktake. A dose logged twice, or a purchase never "
                "recorded, makes the shelf on paper disagree with the real one, "
                "and only a count settles which.")
    if e["state"] == "empty":
        return (f"Out of {e['name'].lower()}",
                f"The record says none is left (counted or worked out "
                f"{'from the count' if e['from'] == 'stocktake' else 'from purchases'} "
                f"of {e['start']:%-d %b}).")
    return (f"{e['name']} is running low",
            f"About {_weeks(e['weeks'])} left at "
            f"{_qty(e['per_week'], e['unit'])} a week "
            f"({_HOW.get(e['rate']['kind'], '')}), so until around "
            f"{e['until']:%-d %b}. {_qty(e['qty'], e['unit'])} on hand.")


def context_lines(rows):
    """The same figures for the assistant, as plain lines."""
    out = []
    for e in rows:
        if e["state"] == "overdrawn":
            out.append(f"  {e['name']}: unknown — the record shows more used than "
                       f"bought; a stocktake is needed")
            continue
        line = f"  {e['name']}: {_qty(max(e['qty'], 0), e['unit'])} on hand"
        if e["state"] == "ok" and e["weeks"] > LONG_WEEKS:
            line += ", more than two years at the current rate"
        elif e["state"] in ("ok", "low"):
            lead = "at least " if e["rate"]["kind"] == "bounded" else "about "
            line += (f", {lead}{_weeks(e['weeks'])} at {_qty(e['per_week'], e['unit'])}"
                     f" a week ({_HOW.get(e['rate']['kind'], '')}), until around "
                     f"{e['until']:%Y-%m-%d}")
        elif e["state"] == "lasting":
            line += ", not being used up"
        elif e["state"] == "no-rate":
            line += ", rate of use not known yet"
        out.append(line + (" — LOW" if e["state"] == "low" else ""))
    return out
