#!/usr/bin/env python3
"""The chemical catalogue and the dose arithmetic, in one place.

WHY THIS MODULE EXISTS
  Three things need to know what a gallon of acid does to this pool: the CLI
  that logs a dose, the reference page that prints a dose table, and the form on
  the dashboard that previews an effect as you type. Written three times they
  drift, and the one that drifts silently is the form — it would keep answering
  confidently with last month's arithmetic.

  So the catalogue and the maths live here, and the browser gets the same
  constants as JSON rather than a second implementation.

THE UNIT TRAP
  "32 oz" is not one quantity. For muriatic acid it is a volume; for cal-hypo it
  is a weight, and the two differ by whatever the density happens to be. Every
  chemical therefore declares a PHASE, and the unit table is chosen from it — so
  the form can only offer units that mean something for what is selected, and
  the CLI can reject the combination instead of quietly computing nonsense.

EVERY DOSE CONSTANT IS THE SAME ONE RELATION
  Water weighs 8.33 lb/gal, so 10,000 gallons is 83,300 lb and one pound of a
  pure substance dissolved in it is 12 ppm. Each solid below is that relation
  with a purity fraction; each liquid is the same by volume. Nothing here is a
  number copied off a bucket.
"""
import re

# Volume units -> US fluid ounces.
VOLUME_UNITS = {"floz": 1.0, "oz": 1.0, "cup": 8.0, "qt": 32.0, "gal": 128.0,
                "l": 33.814, "ml": 0.033814}
# Weight units -> pounds.
WEIGHT_UNITS = {"lb": 1.0, "oz": 0.0625, "kg": 2.20462, "g": 0.00220462}

# ppm of a measure per pound of PURE substance in 10,000 gallons.
PPM_PER_LB_10K = 12.0

# The shape of chemicals.csv. It lives here because BOTH writers of that file --
# the API in server.py and bin/chem -- already import this module, and when each
# of them declared its own copy they disagreed: six columns against seven.
#
# Every logged dose carries who logged it. The identity was authenticated at the
# door -- whoami() reads it from the proxy header -- and was then thrown away, so
# in a two-person household either person could log, edit or delete a dose with
# no record of which. The dose log is the record the whole fitting premise rests
# on.
# `id` IS THE ROW'S IDENTITY, AND `ts` NEVER WAS ONE. The dose form's time field
# is an <input type=datetime-local>, which is minute-resolution, and edit and
# delete both matched on the timestamp alone -- so two doses in the same minute,
# or one impatient double-click, produced two rows that could never afterwards
# be edited or deleted by anybody: both routes answered "N entries share that
# timestamp - refusing to guess" while the page went on drawing Edit and Delete
# beside them. Measured: two POSTs a second apart with the same minute-resolution
# time gave 2 rows, then 400 from /api/chemical/delete AND /api/chemical/edit,
# permanently. A log that can only be appended to is exactly what the edit route
# exists to prevent.
#
# Random rather than derived from the row, because two identical doses a minute
# apart are a real thing an owner does and any hash of the contents collides for
# them -- which is the defect again with more steps. Short because it is opaque:
# nothing reads it but a match.
#
# Additive, so locking.migrate_columns adds the column to an existing file and
# backs it up first. Rows written before this -- and by bin/chem, which builds
# its row without one -- carry an empty id until a write touches the file;
# ensure_ids() fills those in, and the ts match is kept for them.
CHEM_COLS = ["ts", "chemical", "amount", "unit", "pct", "note", "by", "id"]

# Long enough that a collision is not a thing that happens: 12 hex characters is
# 2**48, against a dose log of a few thousand rows over the life of a pool.
ID_CHARS = 12

def new_id():
    """One dose identity."""
    import secrets
    return secrets.token_hex(ID_CHARS // 2)

def ensure_ids(rows):
    """Give every row an id, and say whether any needed one.

    Called inside the chemicals lock before a match, so the ids a refusal
    reports are the ids the file holds -- an identity invented for an error
    message and not written down would name something the next request cannot
    find.
    """
    added = 0
    seen = {(r.get("id") or "").strip() for r in rows}
    for r in rows:
        if not (r.get("id") or "").strip():
            i = new_id()
            while i in seen:                 # not expected; not assumed either
                i = new_id()
            seen.add(i)
            r["id"] = i
            added += 1
    return added


CHEMICALS = {
    "acid": dict(
        label="Muriatic acid", phase="liquid", default_pct=31.45,
        pct_choices=[31.45, 14.5],
        pct_label="strength",
        blurb="Lowers pH and alkalinity together. It does not act on pH directly — "
              "it consumes carbonate alkalinity and pH falls as a consequence, "
              "which is why the same dose moves pH further in a low-alkalinity pool.",
        # 25.6 fl oz of 31.45% per 10 ppm of TA per 10,000 gal is solid. The
        # step from alkalinity to pH is the soft part, and it is NOT a constant:
        # a carbonate system resists pH change in proportion to how much
        # bicarbonate is in it, so the same acid moves pH further in a
        # low-alkalinity pool. The familiar "0.1 pH per 4 ppm of TA" is quoted
        # for pools around TA 90, so it is anchored there and scaled.
        #
        # This is not academic. Predicting a 0.25 gal dose at TA 73 with the flat
        # rule gave -0.21 pH against -0.30 to -0.40 actually measured; scaling
        # for alkalinity closes most of that gap without fitting anything to a
        # single observation.
        effects=lambda floz, pct, V, ta=90.0: {
            "ta": -(floz / (25.6 * V * (31.45 / pct))) * 10.0,
            "ph": -(floz / (25.6 * V * (31.45 / pct))) * 10.0 / 4.0 * 0.1
                  * (90.0 / max(ta, 30.0)),
        }),
    "chlorine": dict(
        label="Liquid chlorine", phase="liquid", default_pct=12.5,
        pct_choices=[12.5, 10.0, 8.25],
        pct_label="available chlorine",
        blurb="Raises free chlorine and nothing else worth tracking — it leaves "
              "only salt behind, which a salt pool wants anyway.",
        effects=lambda floz, pct, V, ta=90.0: {"free_cl": floz * pct / (128.0 * V)}),
    "shock": dict(
        label="Cal-hypo shock", phase="solid", default_pct=73.0,
        pct_choices=[73.0, 65.0, 48.0],
        pct_label="available chlorine",
        blurb="Raises free chlorine and calcium hardness together. On a pool "
              "short of calcium that second effect is a bonus rather than a cost.",
        effects=lambda lb, pct, V, ta=90.0: {
            "free_cl": lb * PPM_PER_LB_10K * pct / 100.0 / V,
            "ch": lb * PPM_PER_LB_10K * pct / 100.0 / V * 0.7}),
    "bicarb": dict(
        label="Sodium bicarbonate", phase="solid", default_pct=100.0,
        pct_choices=[100.0], pct_label="purity",
        blurb="Raises alkalinity strongly and pH barely — which is exactly why "
              "it is the standard tool for alkalinity and a poor one for pH.",
        effects=lambda lb, pct, V, ta=90.0: {"ta": lb * (pct / 100.0) * 10.0 / 1.4 / V}),
    "soda_ash": dict(
        label="Soda ash", phase="solid", default_pct=100.0,
        pct_choices=[100.0], pct_label="purity",
        blurb="Raises pH and alkalinity together. Rarely needed on a salt pool, "
              "where the cell already pushes pH up on its own.",
        effects=lambda lb, pct, V, ta=90.0: {"ph": lb * (pct / 100.0) * 0.25 / 0.75 / V,
                                    "ta": lb * (pct / 100.0) * 10.0 / 1.7 / V}),
    "calcium": dict(
        label="Calcium chloride", phase="solid", default_pct=77.0,
        pct_choices=[77.0, 94.0], pct_label="purity",
        blurb="Raises calcium hardness. Dissolve it in a bucket first — it gets "
              "hot enough to matter.",
        effects=lambda lb, pct, V, ta=90.0: {"ch": lb * (pct / 77.0) * 10.0 / 1.2 / V}),
    "cya": dict(
        label="Stabiliser (cyanuric acid)", phase="solid", default_pct=100.0,
        pct_choices=[100.0], pct_label="purity",
        blurb="Raises cyanuric acid, which raises the free-chlorine target with "
              "it. Dissolves over about a week — do not re-dose before then, and "
              "remember nothing removes it but draining.",
        effects=lambda lb, pct, V, ta=90.0: {"cya": lb * (pct / 100.0) * 100.0 / 8.33 / V}),
    # ------------------------------------------------------- branded liquids
    #
    # Two products that are bought off a shelf rather than as a commodity, and
    # they are here for opposite reasons. One has a dose rate on the label that
    # can be turned into a coefficient; the other does not, and saying so is
    # the point.
    "cya_liquid": dict(
        label="Liquid stabiliser (Leslie's Instant Conditioner Plus)",
        phase="liquid", default_pct=100.0, pct_choices=[100.0],
        pct_label="as sold",
        blurb="The same cyanuric acid as the granular kind, already dissolved. "
              "It acts immediately instead of sitting in a sock for a week, "
              "which is the whole reason to pay more for it — and it is why a "
              "re-test the next day means something here and does not for the "
              "granules.",
        # FROM THE LABEL, NOT FROM A CHEMISTRY TEXT: "4 ounces added to 10,000
        # gallons = 1 ppm increase", and "1 gallon raises CYA level by 32 ppm
        # per 10,000 gallons". Those agree — 128 / 4 = 32 — which is the check
        # worth doing, because a third-party summary of this product quoted 35
        # ppm per gallon against the same 4 oz per ppm, and the two cannot both
        # be true.
        effects=lambda floz, pct, V, ta=90.0: {"cya": floz / (4.0 * V)}),
    "nophos": dict(
        label="Phosphate remover (Leslie's NoPHOS)",
        phase="liquid", default_pct=100.0, pct_choices=[100.0],
        pct_label="as sold",
        blurb="Lanthanum, which binds phosphate into a solid the filter then "
              "catches. Dose rate on the label is 4 fl oz per 8,000 gallons. "
              "Expect the filter pressure to rise and the water to cloud for a "
              "day; that haze IS the phosphate leaving.",
        # NO PREDICTED EFFECT, DELIBERATELY. The label gives a dose RATE and no
        # ppb figure, because how much phosphate a lanthanum dose removes
        # depends on how much is in the water to bind — it is stoichiometric
        # against a quantity this product only knows from a lab result that
        # arrives days later. Every other entry here converts an amount into a
        # predicted change; inventing one for this would put a fabricated
        # number in front of somebody about to pour something into a pool.
        #
        # An empty effect map is not the same as an effect of zero, and every
        # consumer now says which it is — see `no_effect` below.
        no_effect="how much phosphate this removes depends on how much is in "
                  "the water, which only the next lab result can say. The dose "
                  "is logged so the drop can be attributed to it.",
        effects=lambda floz, pct, V, ta=90.0: {}),
    "salt": dict(
        label="Pool salt", phase="solid", default_pct=100.0,
        pct_choices=[100.0], pct_label="purity",
        blurb="Raises salt, the cell's raw material. Brush it off the floor and "
              "run the pump a day before re-testing.",
        effects=lambda lb, pct, V, ta=90.0: {"salt": lb * (pct / 100.0) * 100.0 / 8.33 / V}),
}

# What each effect key is called, and how it prints.
EFFECT_LABELS = {"ph": ("pH", "{:+.2f}"), "ta": ("alkalinity", "{:+.0f} ppm"),
                 "free_cl": ("free chlorine", "{:+.1f} ppm"),
                 "ch": ("calcium", "{:+.0f} ppm"), "cya": ("cyanuric acid", "{:+.0f} ppm"),
                 "salt": ("salt", "{:+.0f} ppm")}

def units_for(chem):
    return VOLUME_UNITS if CHEMICALS[chem]["phase"] == "liquid" else WEIGHT_UNITS

def to_base(chem, amount, unit):
    """Convert a bought quantity into the unit the maths uses: fluid ounces for
    a liquid, pounds for a solid. Returns None when the unit is meaningless for
    the chemical — 'a gallon of salt' is not a dose."""
    table = units_for(chem)
    if unit not in table:
        return None
    return amount * table[unit]

# THE LARGEST DOSE THAT IS A DOSE, in each chemical's base unit — fluid ounces
# for a liquid, pounds for a solid.
#
# ONE BOUND, HERE, because there were two and they disagreed. `0 < amount <= 1000`
# was written inline in the EDIT route and nowhere else, so POST /api/chemical
# accepted 5,000 gal of acid and reported an estimated alkalinity change of
# -202,511 ppm as guidance; `bin/chem acid 9000 gal` exited 0 and printed
# -364,520 ppm. The same value through the edit route answered 400 "that amount
# is not plausible" — so the product accepted a dose it then refused to let
# anybody fix, and rendered an Edit button on the row that could not succeed.
# Editing only the NOTE of such a row failed too, because amount defaults to
# what is on file.
#
# In base units, not raw input, because 1000 fl oz and 1000 gal are not the same
# claim — the old bound treated them as one. These are generous: the largest
# sensible single dose for a residential pool is well inside them, and the job
# here is to refuse a decimal-point slip, not to second-guess a big pool.
MAX_BASE = {"liquid": 2560.0,   # 20 US gallons of liquid, in fl oz
            "solid":  200.0}    # 200 lb

def plausible_amount(chem, amount, unit):
    """(ok, why). The one place that decides whether a dose is a dose.

    Called by the log route, the edit route and the CLI, so a dose that can be
    recorded can always be corrected.
    """
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return False, "amount has to be a number"
    if amount <= 0:
        return False, "an amount has to be more than zero"
    base = to_base(chem, amount, unit)
    if base is None:
        return False, f"{unit!r} is not a unit for a {CHEMICALS[chem]['phase']}"
    cap = MAX_BASE[CHEMICALS[chem]["phase"]]
    if base > cap:
        human = cap / units_for(chem)[unit]
        return False, (f"{amount:g} {unit} of {CHEMICALS[chem]['label']} is not a "
                       f"dose — the most this will record is about {human:g} {unit}. "
                       f"Check the decimal point.")
    return True, ""

def effects(chem, amount, unit, pct, gallons, ta=90.0):
    """Effect of a dose. Alkalinity is passed where it matters — only acid and
    soda ash care, and only because pH response depends on how buffered the
    water is."""
    base = to_base(chem, amount, unit)
    if base is None:
        return None
    return CHEMICALS[chem]["effects"](base, float(pct), gallons / 10000.0, ta)

# WHAT IT SAYS ON THE BAG, because that is what a person is holding.
#
# MEASURED, as a complaint: "there is no option to add Leslie's Alkalinity Up".
# There is -- it is `bicarb`, and the picker listed it as "Sodium bicarbonate".
# The catalogue named the retail product for exactly the two entries where
# somebody had happened to do it, "Leslie's Instant Conditioner Plus" and
# "Leslie's NoPHOS", and named the chemistry for the other eight. So somebody
# standing in the shop with a bag of Alkalinity Up read ten chemical names,
# matched none of them, and concluded the product could not record it.
#
# GENERIC SHELF NAMES, NOT BRANDS, except where the brand is already in the
# label. Every brand sells "Alkalinity Up"; naming one implies the others are
# not covered, and asserting a specific product's composition is a claim about
# a label this project cannot read. The purity field is where a blend gets
# declared, and bicarb accepts only 100% -- so a product that is NOT pure
# sodium bicarbonate cannot be entered as one, which is the correct refusal.
#
# AND NO NAME MAY BELONG TO TWO ENTRIES. That is the dangerous case and the
# reason this is a table rather than a sentence in each entry: "pH Up" is soda
# ash and "Alkalinity Up" is bicarbonate, they move different measures, and a
# picker that offered the same shelf name for both would hand somebody the
# wrong coefficient with full confidence. checks_catalogue asserts the sets are
# disjoint.
# SPELLED AS A BAG SPELLS IT -- "Alkalinity Up", "pH Up" -- because this text
# is shown to somebody comparing it with the thing in their hand. Matching is
# case-insensitive via _norm(); a blanket .title() would render "pH Up" as
# "Ph Up", which is not what anybody is holding.
SOLD_AS = {
    "acid":       ("Muriatic acid", "pool acid", "hydrochloric acid"),
    "chlorine":   ("liquid chlorine", "sodium hypochlorite", "chlorinating liquid"),
    "shock":      ("cal-hypo", "calcium hypochlorite", "Power Powder"),
    "bicarb":     ("Alkalinity Up", "Alkalinity Increaser", "baking soda",
                   "sodium bicarbonate"),
    "soda_ash":   ("pH Up", "pH Increaser", "sodium carbonate", "washing soda"),
    "calcium":    ("Hardness Plus", "Calcium Hardness Increaser",
                   "calcium chloride"),
    "cya":        ("Stabiliser", "Stabilizer", "Conditioner", "cyanuric acid"),
    "cya_liquid": ("Instant Conditioner", "liquid stabiliser", "liquid conditioner"),
    "nophos":     ("NoPHOS", "phosphate remover"),
    "salt":       ("pool salt", "sodium chloride"),
}


# WHAT IT COSTS ON THE SHELF, as a reference and never as a fact about this
# household. Electricity has no default price on purpose -- "a made-up tariff
# turns an honest energy figure into a fabricated money figure" -- and these
# are not made up, but they are still somebody else's receipt. So they are
# used only where nobody has entered a price in Settings, and wherever one is
# used the page names it: the shop, the pack, and the month it was read.
#
# The cheapest current listing for the grade this product doses with, per the
# unit the upkeep table quotes. Acid is priced at 31.45% only: a reference for
# one strength is not a reference for another, and the weaker acid sells for
# nearly the same per gallon while doing less than half the work.
REFERENCE_PRICES_AS_OF = "October 2026"
REFERENCE_PRICES = {
    "acid":       {"price": 9.75, "unit": "gal", "pct": 31.45,
                   "source": "Leslie's, two 1-gallon jugs for $19.49"},
    "chlorine":   {"price": 7.50, "unit": "gal",
                   "source": "Leslie's, four 1-gallon jugs (10-12.5%) for $29.99"},
    "shock":      {"price": 6.99, "unit": "lb",
                   "source": "Leslie's Power Powder Plus, twelve 1 lb bags for $83.88"},
    "bicarb":     {"price": 1.00, "unit": "lb",
                   "source": "Home Depot, 12 lb of baking soda for $11.98"},
    "soda_ash":   {"price": 3.30, "unit": "lb",
                   "source": "Leslie's, 10 lb for $32.99"},
    "calcium":    {"price": 1.62, "unit": "lb",
                   "source": "Home Depot, 16 lb of calcium chloride for $25.88"},
    "cya":        {"price": 5.00, "unit": "lb",
                   "source": "Lowe's, 4 lb for $19.98"},
    "cya_liquid": {"price": 39.99, "unit": "gal",
                   "source": "Leslie's, 1 gallon for $39.99"},
    "nophos":     {"price": 50.46, "unit": "gal",
                   "source": "Leslie's, 3 L for $39.99"},
    "salt":       {"price": 0.20, "unit": "lb",
                   "source": "Home Depot and Lowe's, 40 lb for $7.98"},
}

# The name a column or a price box can afford. The catalogue's label is the
# chemistry and shelf_label() adds what it is sold as; both are too long to
# head a column of a month-by-month table on a phone.
SHORT_NAMES = {
    "acid": "Muriatic acid", "chlorine": "Liquid chlorine", "shock": "Shock",
    "bicarb": "Baking Soda/Alkalinity Up", "soda_ash": "Soda ash",
    "calcium": "Calcium", "cya": "Stabiliser", "cya_liquid": "Liquid stabiliser",
    "nophos": "NoPHOS", "salt": "Pool salt",
}


def price_unit(chem):
    """The unit a chemical is priced and summed in: gallons for a liquid,
    pounds for a solid -- what the jug or the bag is sold by."""
    return "gal" if CHEMICALS[chem]["phase"] == "liquid" else "lb"


def _norm(name):
    """One spelling for comparison, so the disjointness check and any search
    agree about whether two shelf names are the same name."""
    return " ".join(str(name).lower().split())


def sold_as(chem):
    """The names this chemical appears under on a shelf."""
    return SOLD_AS.get(chem, ())


def shelf_label(chem):
    """The picker's text: the chemistry, and what it is sold as.

    Both, not one or the other. The chemistry is what the coefficients belong
    to and what a label has to be checked against; the shelf name is what the
    person actually bought. A name already inside the label is not repeated.
    """
    c = CHEMICALS.get(chem)
    if c is None:
        return chem
    lab = _norm(c["label"])
    names = [n for n in sold_as(chem) if _norm(n) not in lab]
    if not names:
        return c["label"]
    return f"{c['label']} \u2014 sold as {' / '.join(names[:3])}"


def amount_for(chem, measure, delta, gallons, pct=None, ta=90.0):
    """How much of `chem` moves `measure` by `delta`, in that chemical's base unit.

    The inverse of effects(), and the reason a second copy of the dose arithmetic
    grew in chemistry.py: the reference tables there need "how much for −0.1 pH"
    and only the forward direction was published here. That copy then drifted —
    it used a flat 4 ppm-TA-per-0.1-pH rule this module's own docstring records
    as measurably wrong, and produced acid doses 23% high, on the public tab, in
    the overdose direction, for the one chemical that damages plaster.

    Inverting rather than re-deriving is safe because every effect is linear in
    amount, which is asserted below. Returns None where the chemical does not
    move that measure at all, so a caller cannot silently divide by zero and
    print a confident number.
    """
    per_unit = CHEMICALS[chem]["effects"](1.0, float(pct if pct is not None
                                                     else CHEMICALS[chem]["default_pct"]),
                                          gallons / 10000.0, ta)
    one = per_unit.get(measure)
    if not one:
        return None
    return delta / one

def _assert_linear():
    """Doubling a dose doubles its effect — the property amount_for() inverts."""
    for key, c in CHEMICALS.items():
        a = c["effects"](1.0, c["default_pct"], 1.5, 90.0)
        b = c["effects"](2.0, c["default_pct"], 1.5, 90.0)
        for m, v in a.items():
            if v and abs(b[m] - 2 * v) > 1e-9 * max(1.0, abs(v)):
                raise AssertionError(f"{key}.{m} is not linear in amount")
_assert_linear()

def _decimals(fmt):
    """How many decimal places a format spec asks for."""
    m = re.search(r"\.(\d+)f", fmt or "")
    return int(m.group(1)) if m else 1


def _unit(fmt):
    """Whatever trails the number in a format spec, e.g. ' ppm'."""
    m = re.search(r"\}(.*)$", fmt or "")
    return (m.group(1) if m else "").strip()


def as_json(gallons, ta=90.0, gallons_known=True):
    """The same catalogue, shaped for the browser.

    The effect coefficients are sent as linear factors rather than as code, so
    the page multiplies where Python would call a lambda and there is still only
    one definition of what a dose does. Each factor is the effect of ONE base
    unit (one fluid ounce, or one pound) at the reference strength, which is
    exactly what the lambdas above reduce to once the pool volume is fixed.
    """
    out = {}
    for key, c in CHEMICALS.items():
        one = c["effects"](1.0, c["default_pct"], gallons / 10000.0, ta)
        out[key] = {
            "label": c["label"], "phase": c["phase"],
            # So the browser's own rendering of the picker, and any search in
            # it, agree with the server's about what a thing is called.
            "shelf_label": shelf_label(key), "sold_as": list(sold_as(key)),
            "default_pct": c["default_pct"], "pct_choices": c["pct_choices"],
            "pct_label": c["pct_label"], "blurb": c["blurb"],
            # WHY there is no predicted effect, where there is none. An empty
            # per_unit renders as an empty preview, which reads as "no effect"
            # rather than "not predicted" — the two are opposite claims.
            "no_effect": c.get("no_effect", ""),
            "per_unit": one, "ref_pct": c["default_pct"],
            "units": sorted(units_for(key).keys()),
        }
    return {"gallons": gallons, "alkalinity": ta,
            # Whether the volume above is this pool's or a placeholder. The page
            # must not present an effect "on 15,000 gal" to somebody who has
            # never said their pool is 15,000 gallons.
            "gallons_known": bool(gallons_known),
            "chemicals": out,
            "volume_units": VOLUME_UNITS, "weight_units": WEIGHT_UNITS,
            # decimals and unit are exported explicitly, not left to be
            # recovered from the Python format spec. The browser used to sniff
            # it -- fmt.indexOf('.0f'), fmt.indexOf('ppm') -- which is a
            # cross-language contract held together by substring search: a
            # measure needing three decimals, or a unit that is not ppm, and the
            # page silently rounds to the wrong place with nothing raising.
            # Python keeps using its own spec; the browser gets the facts.
            "effect_labels": {k: {"name": v[0], "fmt": v[1],
                                  "decimals": _decimals(v[1]),
                                  "unit": _unit(v[1])}
                              for k, v in EFFECT_LABELS.items()}}


# ============================================================ DOSE RESPONSE
# The whole point of logging doses is to stop guessing what they do. This is
# where a logged addition meets the readings either side of it.

def dose_response(chem_rows, readings, when_fn, num_fn, window_h=36):
    """Pair each logged dose with the readings before and after it.

    "Before" is the newest reading strictly earlier than the dose, and "after"
    the first one at least an hour later — a pod sitting in the skimmer measures
    a slug of acid that has not mixed yet, and the number it returns then is
    about the water next to the pod rather than about the pool.

    A baseline of several prior readings is carried too, because a single
    previous value can itself be the outlier: this pool read 7.7, 7.8, 7.9 on
    consecutive quiet days, so crediting a dose with the full distance from the
    highest of those overstates it.
    """
    import datetime as _dt
    obs = []
    rs = sorted([r for r in readings if when_fn(r.get("measured"))],
                key=lambda r: when_fn(r["measured"]))
    for c in chem_rows:
        t = when_fn(c.get("ts"))
        if not t:
            continue
        before = [r for r in rs if when_fn(r["measured"]) < t]
        after = [r for r in rs if when_fn(r["measured"]) >= t + _dt.timedelta(hours=1)]
        if not before or not after:
            continue
        a = after[0]
        gap_h = (when_fn(a["measured"]) - t).total_seconds() / 3600.0
        if gap_h > window_h:
            continue
        base = [num_fn(r.get("ph")) for r in before[-4:] if num_fn(r.get("ph")) is not None]
        obs.append({
            "dose": c, "at": t, "hours_to_reading": gap_h,
            "before": before[-1], "after": a,
            "ph_before": num_fn(before[-1].get("ph")),
            "ph_after": num_fn(a.get("ph")),
            "ph_baseline": sum(base) / len(base) if base else None,
            "fc_before": num_fn(before[-1].get("free_cl")),
            "fc_after": num_fn(a.get("free_cl")),
        })
    return obs
