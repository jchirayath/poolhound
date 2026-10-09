#!/usr/bin/env python3
"""Render the chemistry reference — what moves each factor, and by how much.

WHY THIS PAGE EXISTS
  The dashboard says pH is 7.8. It does not say that the salt cell and the spa
  spillover are both pushing it up every hour the pump runs, that the high side
  of the alkalinity range is making it climb faster, that 7.8 quietly cuts the
  sanitising fraction of the measured chlorine by more than half, or that
  simply driving pH down to 7.4 would make the water aggressive to plaster at
  this pool's calcium level. Those are the facts that turn a number into a
  decision, and they do not fit on a tile.

  Everything here is either textbook chemistry or arithmetic on THIS pool's
  volume and current test results. Dose figures are computed from the
  configured volume rather than copied from a chart for some other pool — and
  the volume is an estimate, which is stated wherever it changes a number.

WHAT IS DELIBERATELY NOT HERE
  Fitted coefficients. The whole point of poolhound is to measure this pool's
  actual response instead of trusting a table. The numbers below are the
  starting estimates the fit will replace; the page says so where it matters.
"""
import datetime as dt, html, math, os
from . import chemicals as CH
from . import config, style

CFG  = config.load()
SITE = config.site_dir(CFG)
DATA = config.data_dir(CFG)
POOL = CFG.get("pool", {})
GAL_SET = config.volume(CFG) is not None
# Has a lab ever reported this pool's alkalinity? current_ta() falls back to 90
# when none has, and acid moves pH further in a less buffered pool -- so quoting
# a dose against the wrong alkalinity is wrong by the ratio of the two, 23% in
# the OVERDOSE direction at this pool's measured 73. The volume gets three
# disclosures for exactly this; alkalinity got none.
TA_SET = False        # set properly in reload(), after render is importable
GAL  = config.volume(CFG) or 15000.0
EST  = bool(POOL.get("volume_estimated", True))
ACID = float(POOL.get("acid_pct", 31.45))

from .render import (fill, rows, num, when, targets, best_lab, verdict, ago,
                     current_ta, ta_is_measured)   # one source of truth

V = GAL / 10000.0        # every dose constant below is per 10,000 gallons

# The alkalinity the acid figures are computed against. Measured where there is
# a lab result, and 90 only as the "we do not know yet" fallback — the same
# default chemicals.py uses. This is not decoration: acid moves pH further in a
# less buffered pool, so a dose quoted against the wrong TA is wrong by the ratio
# of the two. At this pool's measured 73 against the assumed 90 that is 23%, in
# the overdose direction, for the chemical that damages plaster.
TA = 90.0

def reload():
    """Re-read the configuration into this module's globals. See render.reload().

    V is derived from GAL and must be recomputed with it, or every dose figure on
    the Pool chemistry tab keeps scaling to the volume this process booted with.
    """
    global CFG, SITE, DATA, POOL, GAL, GAL_SET, EST, ACID, V, TA, TA_SET
    CFG  = config.load()
    SITE = config.site_dir(CFG)
    DATA = config.data_dir(CFG)
    POOL = CFG.get("pool", {})
    GAL_SET = config.volume(CFG) is not None
    GAL  = config.volume(CFG) or 15000.0
    EST  = bool(POOL.get("volume_estimated", True))
    ACID = float(POOL.get("acid_pct", 31.45))
    V    = GAL / 10000.0
    TA   = current_ta()
    TA_SET = ta_is_measured()

# ------------------------------------------------------------------ dose maths
# There is NO dose arithmetic here. These are the inverse of chemicals.py's
# catalogue — "how much for this change" rather than "what does this amount do" —
# and they delegate, because for a while they did not.
#
# The copy that used to live here re-typed every constant and then drifted: it
# applied a flat 4 ppm-TA-per-0.1-pH rule, which is the TA-90 case frozen, so
# every acid figure on this page was quoted for a pool more buffered than this
# one. Six of the seven chemicals agreed to the digit; acid is the only dose that
# depends on a measurement, and it was the only one that was wrong.

def _for(chem, measure, delta, pct=None):
    got = CH.amount_for(chem, measure, delta, GAL, pct, TA)
    return 0.0 if got is None else got

def acid_floz(delta_ph, pct=None):
    """Muriatic acid to drop pH by `delta_ph`, against the MEASURED alkalinity.

    Acid does not act on pH directly — it consumes carbonate alkalinity, and pH
    falls as a consequence, which is why the same dose moves pH further in a
    low-TA pool. That dependence is the whole reason this cannot be a constant.
    """
    return abs(_for("acid", "ph", -abs(delta_ph), pct or ACID))

def ch_clause(ch, band):
    """Whether added calcium is a bonus depends on this pool's calcium.

    This asserted "which on this pool is a bonus rather than a cost -- calcium
    is on the low side" with nothing consulted. On a pool whose calcium is
    already high it is the opposite of true, and cal-hypo is exactly the wrong
    thing to reach for. The number is on file when a lab has reported it; when
    none has, the honest answer is that it depends and we do not know.
    """
    if ch is None:
        return (", which is a bonus on a pool low in calcium and a cost on one "
                "that is already high — no calcium reading is on file, so this "
                "one cannot say which")
    if band and ch < band[0]:
        return f", which at this pool's measured {ch:.0f} ppm is a bonus rather than a cost"
    if band and ch > band[2]:
        return f", which at this pool's measured {ch:.0f} ppm is a cost, not a bonus"
    return f", which at this pool's measured {ch:.0f} ppm is neither much help nor much harm"


def spillover_clause():
    """The spillover is only a constant on a pool that HAS one.

    "On this pool the spillover runs the entire time the pump runs" was stated
    unconditionally, on a page that is also served to an install whose spa is
    isolated behind a valve, or which has no spa at all.
    """
    if (POOL.get("spa_shares_water") is True):
        return (" On this pool the spillover runs the entire time the pump runs, "
                "so this is a constant, not an occasional event. The sheer descent "
                "and the spa jets stack on top of it.")
    return (" Whether that is a constant or an occasional event depends on the "
            "plumbing: a spillover spa aerates for as long as the pump runs, while "
            "a sheer descent or a set of jets only does so when it is switched on.")


def pool_ref():
    """What to CALL the water a dose figure is about.

    GAL falls back to 15,000 when nobody has said how big the pool is, and
    seven figures below described their amounts as being "in this pool" or
    "here" regardless -- so an install that had been told nothing read amounts
    for a notional pool as claims about the reader's water. The volume badge
    and the dose basis already say when the figure is notional; the sentences
    between them did not, and those are the ones somebody acts on.

    Same rule as config.volume() returning None: the caller has to ask.
    """
    return "in this pool" if GAL_SET else "in a notional 15,000 gal pool"


def here():
    """The short form, for figures that end '... per 10 ppm here'."""
    return "here" if GAL_SET else "in a notional 15,000 gal pool"


def acid_ta_cost(delta_ph, pct=None):
    """The alkalinity the acid for `delta_ph` also consumes, in ppm.

    The prose used to say "about 4 ppm each time" as a literal, beside a pH
    figure that had just been corrected to scale with the measured alkalinity.
    4 ppm is the TA-90 case: the acid needed for 0.1 pH in a LESS buffered pool
    is smaller, so it costs less alkalinity too. At this pool's measured 73 it
    is nearer 3. Same dose, same catalogue, one direction each — so the two
    halves of one sentence cannot disagree about how buffered this water is.
    """
    floz = acid_floz(delta_ph, pct)
    eff = CH.effects("acid", floz, "floz", pct or ACID, GAL, ta=TA)
    return abs((eff or {}).get("ta", 0.0))

def bicarb_lb(delta_ta):            return _for("bicarb", "ta", delta_ta)
def liquid_cl_floz(ppm, pct=12.5):  return _for("chlorine", "free_cl", ppm, pct)
def calhypo_lb(ppm, pct=73.0):      return _for("shock", "free_cl", ppm, pct)
def salt_lb(ppm):                   return _for("salt", "salt", ppm)
def cya_lb(ppm):                    return _for("cya", "cya", ppm)
def cacl_lb(ppm):                   return _for("calcium", "ch", ppm)

def oz(x):
    """Print a volume the way it is actually bought and poured."""
    if x >= 128: return f"{x/128:.2f} gal".replace(".00 ", " ")
    if x >= 32:  return f"{x/32:.1f} qt ({x:.0f} fl oz)"
    return f"{x:.0f} fl oz"

def lb(x):
    return f"{x*16:.0f} oz" if x < 1 else f"{x:.1f} lb"

# ------------------------------------------------------------------- factor DB
# strength: how much this driver actually matters in a pool like this one.
# It is rendered as a word AND a three-segment bar, never as colour alone.
DOM, MOD, MIN = "dominant", "moderate", "minor"

def co2_ceiling(ta_ppm):
    """The pH at which aeration stops working, for a given alkalinity.

    Water exchanges CO2 with the air until the two balance. Below that point the
    pool is supersaturated and every bubble, spillover and sheer drives CO2 out
    and pH up; at it, there is nothing left to drive off and aeration does
    nothing at all.

    From Henry's law and the first dissociation of carbonic acid:
        pH = pK1 + log10([HCO3-] / [CO2*])
    with [CO2*] set by atmospheric CO2 and bicarbonate standing in for
    alkalinity, which is a fair approximation between pH 7 and 8.5.

    The consequence is the useful part: the ceiling is a function of ALKALINITY.
    Lower the alkalinity and the ceiling comes down with it. That is a second,
    independent reason for the advice a salt pool already gets, and unlike "it
    buffers less" it says exactly where the pH will stop.
    """
    if not ta_ppm or ta_ppm <= 0:
        return None
    KH, PCO2, PK1 = 0.034, 4.0e-4, 6.35
    return PK1 + math.log10((ta_ppm / 50000.0) / (KH * PCO2))

def ph_note(ph, ch, TG):
    """What the two forces on pH are actually asking for, at today's calcium.

    Chlorine effectiveness always wants pH low. Whether the saturation index
    wants it HIGH depends on how much calcium there is to hold the index up --
    which is a measurement, and was written down as "at the low end".
    """
    lead = ("Two forces pull pH in opposite directions here. Chlorine "
            "effectiveness wants it low. ")
    if ch is None:
        return (lead + "Whether the saturation index pulls the other way depends on "
                "calcium, and there is no calcium reading on file. Record one on "
                "Settings, or wait for the next lab result.")
    if ch < TG["ch"][0]:
        return (lead + f"The saturation index — with calcium at {ch:.0f} ppm, below "
                f"the {TG['ch'][0]:.0f}–{TG['ch'][2]:.0f} this pool wants — wants it "
                f"high. See the tension section below; the resolution is not a pH "
                f"number, it is more calcium.")
    if ch > TG["ch"][2]:
        return (lead + f"At {ch:.0f} ppm of calcium — above the "
                f"{TG['ch'][0]:.0f}–{TG['ch'][2]:.0f} this pool wants — the saturation "
                f"index is not pulling the other way at all: it has no headroom to "
                f"spend, and the water is already inclined to deposit. Both forces "
                f"point down, and nothing but replacing water takes calcium out, so "
                f"pH is the only lever.")
    return (lead + f"The saturation index wants it high, and at {ch:.0f} ppm of "
            f"calcium — inside the {TG['ch'][0]:.0f}–{TG['ch'][2]:.0f} this pool "
            f"wants — there is room to give it some. See the tension section below.")


def tension_framing(ph):
    """Which of the three cases this pool's pH is actually in.

    THE TENSION IS A CLAIM ABOUT THIS POOL TODAY, AND IT HAS TO BE TRUE. Both
    headings and the lede were written for a pool at 7.8 and then printed
    whatever the reading was -- so a pool at 7.2 was told "chlorine wants pH
    down" while sitting BELOW the number that sentence is measured against, and
    read "two correct pieces of advice point in opposite directions" about two
    that were pointing the same way. Below 7.4 there is no trade-off to make.

    Returns (lede, chlorine heading, plaster heading, SI sentence, resolve
    lede). Pure, and out here rather than inside panel(), because a wrong
    ANSWER is what bin/selftest is for and panel() cannot be called without a
    whole pool behind it.
    """
    move = abs(ph - 7.4)
    if ph > 7.4:
        return (
            "Two correct pieces of advice point in opposite directions, and "
            "following either one alone makes something worse.",
            "Chlorine wants pH down",
            "The plaster wants pH up",
            f"Saturation index moves one-for-one with pH: dropping pH by "
            f"{move:.1f} drops SI by {move:.1f}.",
            "The way out is not a pH number.")
    if move < 0.05:
        return (
            "pH is at the number both rules are measured against, so there is "
            "nothing to trade off today. What follows is what to watch.",
            "Chlorine is getting what it wants",
            "So is the plaster",
            "Saturation index moves one-for-one with pH, and pH is already at "
            "7.4, so neither rule is asking for a move.",
            "Nothing to resolve right now.")
    return (
        "Both rules point the same way here — up — so there is no trade-off to "
        "make today. The two sides below are what each one is asking for, not a "
        "conflict.",
        "Chlorine is comfortable",
        "The plaster wants pH up",
        f"Saturation index moves one-for-one with pH: raising pH by {move:.1f} "
        f"back to 7.4 raises SI by {move:.1f}.",
        "There is no tension to resolve at this pH.")


def resolution(ch, ta, TG):
    """What to actually do, in order, at today's calcium and alkalinity.

    THREE CASES, NOT TWO. This tested `ch < ch_target` and fell to an else --
    and ch_target is the MIDDLE of the band, so a pool ABOVE the band landed in
    the else and was told "Calcium is already at 520 ppm, inside the 250–450
    this pool wants". It printed the figure and the band it was outside of, in
    one sentence. The reason was inverted with it: calcium over the band means
    the saturation index has LESS headroom, not more.

    AND "CALCIUM FIRST" IS ONLY AN ORDER IF CALCIUM IS A JOB. Both alkalinity
    branches ended with an instruction to start with calcium, including beside
    a sentence that had just said calcium needs nothing.

    Pure, and out here, for the same reason as tension_framing(): these are
    wrong ANSWERS, and bin/selftest is where those get caught.
    """
    ch_target = TG["ch"][1]
    ca_job = ch < ch_target

    if ca_job:
        step_ca = (f"Raise calcium hardness toward {ch_target:.0f} ppm — about "
                   f"{lb(cacl_lb(ch_target - ch))} of 77% calcium chloride — and the "
                   f"saturation index gains roughly "
                   f"{0.1 * (ch_target - ch) / 70:.2f}, which buys the room to hold pH "
                   f"at 7.5 without the water turning aggressive.")
    elif ch <= TG["ch"][2]:
        step_ca = (f"Calcium is already at {ch:.0f} ppm, inside the "
                   f"{TG['ch'][0]:.0f}–{TG['ch'][2]:.0f} this pool wants, so the "
                   f"saturation index has room and pH can come down.")
    else:
        # Nothing takes calcium out of a pool but draining and refilling, so
        # this branch has no dose to offer. Saying so is the actionable half.
        step_ca = (f"Calcium is {ch:.0f} ppm, <b>above</b> the "
                   f"{TG['ch'][0]:.0f}–{TG['ch'][2]:.0f} this pool wants, so the "
                   f"saturation index has no headroom to spend and the water is "
                   f"already inclined to deposit rather than dissolve. Nothing removes "
                   f"calcium but replacing water, so pH is the lever: bringing it down "
                   f"is what keeps this from scaling, and it is not optional here the "
                   f"way it is on a softer pool.")

    if ta is None:
        return step_ca

    if ta > TG["ta"][2]:
        step_ta = ((" Then bring" if ca_job else " Bring")
                   + f" alkalinity from {ta:.0f} down toward {TG['ta'][1]:.0f} using "
                   f"acid plus the aeration this pool already produces for free, so pH "
                   f"stops climbing back so fast.")
        order = (" Calcium first, alkalinity second, pH last — pH is the symptom."
                 if ca_job else
                 " Alkalinity first, then pH — pH is the symptom. Calcium needs nothing.")
    elif ta < TG["ta"][0]:
        # THE SAME BUG, ONE MEASUREMENT OVER. The branch above tested only
        # "above the band" and let everything else fall to an else that said
        # "already inside the 80–120" -- so a pool at TA 60 was told its
        # alkalinity was inside a range it is twenty ppm under. Low TA is not a
        # tidy state either: it is the one that makes pH erratic, which is the
        # opposite of "needs nothing".
        step_ta = (f" Alkalinity is {ta:.0f}, <b>below</b> the "
                   f"{TG['ta'][0]:.0f}–{TG['ta'][2]:.0f} a salt pool wants. That does "
                   f"not slow the pH climb so much as make it erratic — pH swings on "
                   f"very little, in both directions — and it is why acid feels like it "
                   f"does nothing one week and too much the next. Bicarbonate brings it "
                   f"back up without touching calcium.")
        order = (" Calcium and alkalinity first, pH last — pH will not sit still until "
                 "the buffer does."
                 if ca_job else
                 " Alkalinity first, then pH — pH will not sit still until the buffer "
                 "does.")
    else:
        step_ta = (f" Alkalinity needs nothing: at {ta:.0f} it is already inside the "
                   f"{TG['ta'][0]:.0f}–{TG['ta'][2]:.0f} a salt pool wants, which also "
                   f"means it is not what is pushing pH up. The cell and the constant "
                   f"spillover are, and neither is going to stop — so acid is a routine "
                   f"cost of running this pool, not a sign something is wrong.")
        order = (" Calcium first, then pH. Alkalinity is already done."
                 if ca_job else
                 " pH is the only lever here. Alkalinity and calcium are both where "
                 "they should be."
                 if ch <= TG["ch"][2] else
                 " pH is the only lever here, and alkalinity is already done.")
    return step_ca + step_ta + order


def factors(cur, TG):
    ph  = num(cur.get("ph"))
    fc  = num(cur.get("free_cl"))
    lab = cur
    ta  = num(cur.get("ta"))
    ch  = num(cur.get("ch"))

    # The alkalinity narrative has to follow the measurement. Written as fixed
    # prose it said "at TA 95 this pool is at the top of the range" — which was
    # true of one lab result and false of the next one, seven days later, which
    # read 73. Advice built on a stale number sends someone to buy acid they do
    # not need.
    if ta is None:
        ta_story = ("No alkalinity reading on file. Until there is one, there is no "
                    "way to say whether the buffer is feeding the pH climb or not.")
        ta_route = ("Acid drops both pH and alkalinity; aeration then returns pH "
                    "while leaving alkalinity where it fell. Repeat.")
    elif ta > TG["ta"][2]:
        ta_story = (f"TA is the reservoir of dissolved CO₂ available to be lost, so the "
                    f"higher it is the faster pH rebounds after acid. At {ta:.0f} this pool "
                    f"is above the {TG['ta'][0]:.0f}–{TG['ta'][2]:.0f} a salt pool wants, "
                    f"and that excess is actively feeding the climb.")
        ta_route = (f"The one way to lower alkalinity without ending up with low pH. Acid "
                    f"drops both; aeration then brings pH back while leaving TA where it "
                    f"fell. Repeat. This pool aerates continuously by design, so the second "
                    f"half of the trick happens for free — it is the practical route from "
                    f"{ta:.0f} down toward {TG['ta'][1]:.0f}.")
    elif ta < TG["ta"][0]:
        ta_story = (f"At {ta:.0f} the buffer is below the {TG['ta'][0]:.0f}–{TG['ta'][2]:.0f} "
                    f"this pool wants. Low alkalinity does not slow the pH climb so much as "
                    f"make it erratic — pH swings on very little, in both directions.")
        ta_route = ("Not what this pool needs right now: alkalinity is already below target, "
                    "and taking it lower buys instability rather than pH control.")
    else:
        ta_story = (f"TA is the reservoir of dissolved CO₂ available to be lost, so a high "
                    f"one makes pH rebound fast after acid. At {ta:.0f} this pool is already "
                    f"inside the {TG['ta'][0]:.0f}–{TG['ta'][2]:.0f} range a salt pool wants "
                    f"— lower than a chlorine pool would run, precisely because the cell is "
                    f"a permanent upward push. So alkalinity is <b>not</b> what is driving "
                    f"pH up here. The cell and the spillover are.")
        ta_route = (f"The classic fix for a pool whose pH will not stay down — but not this "
                    f"pool's problem. At TA {ta:.0f} the buffer is already where a salt pool "
                    f"wants it, and taking it lower trades pH control for pH instability.")

    return [
      dict(key="ph", name="pH", unit="", value=ph, band=TG["ph"], fmt="{:.2f}",
        what="How acidic or basic the water is, on a log scale — 8.0 is ten times "
             "less acidic than 7.0. It is not a sanitiser or a nutrient; it is the "
             "dial that decides how well every other chemical in the pool works.",
        why="pH sets the split between hypochlorous acid (HOCl, the form that "
            "actually kills things) and hypochlorite ion (OCl⁻, which barely does). "
            "At 7.2 about 63% of your free chlorine is HOCl; at 7.5, 50%; at 7.8, "
            "roughly 30%; at 8.0, 22%. Every test reads the same total either "
            "way. This is why a pool can show plenty of chlorine and still go "
            "cloudy — and it is the single strongest argument for keeping pH down.",
        up=[
          (DOM, "Salt cell electrolysis",
           "At the cathode the cell splits water and releases hydrogen gas, leaving "
           "hydroxide behind. The chlorine cycle would be pH-neutral if everything "
           "stayed in the pool, but the H₂ bubbles off, so the alkalinity does not "
           "get consumed back. Rate tracks cell output × pump hours."),
          (DOM, "Aeration — the spa spillover above all",
           "Pool water carries more dissolved CO₂ than it would at equilibrium with "
           "air. Anything that breaks the surface drives CO₂ out, carbonic acid goes "
           "with it, and pH climbs — with no change in alkalinity at all."
           + spillover_clause()),
          (MOD if (ta or 0) > TG["ta"][2] else MIN, "Total alkalinity", ta_story),
          (MOD, "Warm water — including both heaters",
           "CO₂ is less soluble as water warms, so heating drives it out of solution: "
           "the same mechanism as aeration arriving through a different door. Nothing "
           "the burner produces touches the water, so the effect is purely thermal, "
           "which means solar gain does it too — on a sunny day the sun heats this "
           "pool harder than the heater does. Water temperature is measured directly, "
           "so the fit uses that rather than heater hours as a proxy."),
          (MIN, "Cal-hypo shock, soda ash, fresh plaster",
           "Cal-hypo slurry is around pH 11.7 and lifts pH as it dissolves. Soda ash "
           "(sodium carbonate) is the deliberate way to raise pH, rarely needed on a "
           "salt pool. Plaster leaches lime for months after a resurface."),
        ],
        down=[
          (DOM, "Muriatic acid",
           f"The working tool. Roughly {oz(acid_floz(0.1))} of {ACID:g}% acid per 0.1 pH "
           f"{pool_ref()}, which also costs about {acid_ta_cost(0.1):.0f} ppm of "
           f"alkalinity each time. "
           f"Pour it slowly over a return jet with the pump running, never into the "
           f"skimmer."),
          (MOD, "Acid plus aeration, used deliberately", ta_route),
          (MIN, "Rain, fill water, trichlor",
           "Rain is mildly acidic and dilutes. Trichlor tablets are strongly acidic "
           "(pH 2.8) but drag CYA up with them, which is why they are the wrong tool "
           "on a pool that already has a cell."),
        ],
        # DERIVED, because this asserted a measurement. "with calcium at the low
        # end ... the resolution is not a pH number, it is more calcium" was
        # written when one lab result read low, and then printed to a pool at
        # any calcium at all -- including one ABOVE the band, where more calcium
        # is the opposite of the answer. Same failure the alkalinity narrative
        # above already had and already fixed: prose that states a number is
        # prose that goes wrong.
        note=ph_note(ph, ch, TG)),

      dict(key="free_cl", name="Free chlorine", unit=" ppm", value=fc,
        band=TG["free_cl"], fmt="{:.1f}",
        what="Chlorine still available to sanitise, as opposed to chlorine already "
             "spent on something. On a salt pool it is made continuously by the cell "
             "rather than poured in.",
        why="Free chlorine is the only thing standing between the pool and algae or "
            "pathogens, but its target is not a fixed number — it is a fraction of "
            "cyanuric acid. CYA binds chlorine into a reservoir that UV cannot "
            "destroy, releasing it slowly, so more CYA demands more FC to keep the "
            "same active concentration.",
        up=[
          (DOM, "Salt cell output × pump hours",
           "The product is what matters, not either alone: 40% for 8 hours and 80% for "
           "4 hours put nearly the same chlorine in the water. Cell output is higher "
           "in spa mode on this system, which is the one condition that separates "
           "cell output from pump hours — and therefore the only natural experiment "
           "available for telling the two effects apart."),
          (MOD, "Liquid chlorine",
           f"About {oz(liquid_cl_floz(1))} of 12.5% per 1 ppm {here()}; "
           f"{oz(liquid_cl_floz(5))} takes the pool up 5 ppm. Adds nothing but salt."),
          (MOD, "Cal-hypo shock",
           f"About {lb(calhypo_lb(1))} of 73% per 1 ppm. It also adds roughly 0.7 ppm "
           f"of calcium hardness per ppm of chlorine{ch_clause(num(cur.get('ch')), TG.get('ch'))}."),
          (MIN, "Cool water, cloud, no swimmers",
           "Not a source; an absence of demand. FC holds where it would otherwise fall."),
        ],
        down=[
          (DOM, "Sunlight",
           "UV destroys unprotected chlorine within hours. CYA is the shield and the "
           "protection curve is steep at the bottom: near zero CYA a pool can lose "
           "most of its FC in an afternoon, while the curve flattens above about 30. "
           + (f"At CYA {num(lab.get('cya')):.0f} this pool is properly protected."
              if num(lab.get("cya")) else "No CYA reading on file.")),
          (DOM, "Organic load",
           "Leaves, pollen, sunscreen, sweat, urine, algae. Every gram of it consumes "
           "chlorine permanently. Bather load after a party is visible in the next "
           "day's reading."),
          (MOD, "Ammonia and chloramines",
           "Chlorine binds to nitrogen compounds, becomes combined chlorine, and stops "
           "sanitising. Clearing it needs enough FC to reach breakpoint — roughly ten "
           "times the combined chlorine — not a token dose."),
          (MOD, "Warm water — including both heaters",
           "Every reaction that consumes chlorine runs faster. Demand in August is not "
           "the same pool as demand in March, and a heated pool carries a higher "
           "standing demand than a cold one at identical sun and CYA."),
          (MIN, "Phosphates and metals",
           (f"Phosphate is {num(lab.get('phosphates')):.0f} ppb; algae feed on it and "
              f"algae eat chlorine, though below roughly 500 ppb it is not the binding "
              f"constraint. " if num(lab.get("phosphates")) else "")
           + "Copper and iron catalyse chlorine loss and stain surfaces."),
        ],
        note="High pH does not lower free chlorine — it lowers what free chlorine can "
             "do. A pool at 6.2 ppm and pH 7.8 has less active sanitiser than one at "
             "4 ppm and pH 7.4."),

      dict(key="ta", name="Total alkalinity", unit=" ppm", value=num(lab.get("ta")),
        band=TG["ta"], fmt="{:.0f}",
        what="Dissolved carbonates and bicarbonates — the water's resistance to pH "
             "change, and the reservoir that feeds pH back up after acid.",
        why="TA is why pH is stubborn, and also why it keeps coming back. Too low and "
            "pH swings wildly on nothing; too high and pH climbs relentlessly no "
            "matter how much acid goes in. Salt pools want the lower end, 70–80, "
            "precisely because the cell is a permanent upward push.",
        up=[
          (DOM, "Sodium bicarbonate (baking soda)",
           f"About {lb(bicarb_lb(10))} per 10 ppm {here()}. Raises TA strongly and pH "
           f"barely — the reason it is the standard tool."),
          (MOD, "Soda ash", "Raises both TA and pH; use only when both are low."),
          (MIN, "Fill water", "Municipal water often arrives at TA 80–120 and top-ups "
           "after evaporation slowly pull the pool toward it."),
        ],
        down=[
          (DOM, "Muriatic acid",
           f"About {oz(acid_floz(0.25))} of {ACID:g}% per 10 ppm of TA {pool_ref()} — "
           f"the same pour that moves pH about 0.25."),
          (DOM, "Acid plus aeration", ta_route),
          (MIN, "Dilution", "Rain overflow and refills, if the fill water is softer "
           "than the pool."),
        ],
        note="Aeration alone never lowers TA. It only raises pH. Every claim that "
             "'aerating brings alkalinity down' is really describing the acid half "
             "of the cycle."),

      dict(key="cya", name="Cyanuric acid", unit=" ppm", value=num(lab.get("cya")),
        band=TG["cya"], fmt="{:.0f}",
        what="Chlorine stabiliser. It bonds reversibly to chlorine, hiding it from UV "
             "and releasing it as the free chlorine is consumed.",
        # AND WHERE ITS OWN BAND COMES FROM. Every other target on this page is
        # derived -- free chlorine from this pool's CYA, alkalinity and calcium
        # from the fact that it runs a cell. This one is a flat (30, 50, 80)
        # typed into targets(), the only generic band left, and nothing said so.
        # A reader comparing their 45 against "30-80" had no way to know it was
        # the one number here not worked out for their pool, while the sentence
        # beside it explains in detail how the FC band was.
        why="CYA sets the free-chlorine target — this is the single most misunderstood "
            "relationship in pool care. The rule for a salt pool is FC between 5% and "
            "10% of CYA, aiming near 7.5%."
            + (f" At CYA {num(lab.get('cya')):.0f} that is "
               f"{TG['free_cl'][0]:.1f}–{TG['free_cl'][2]:.1f} ppm, aim "
               f"{TG['free_cl'][1]:.1f}." if num(lab.get("cya")) else "")
            + (f" Note that the {TG['cya'][0]:.0f}–{TG['cya'][2]:.0f} band shown for "
               f"CYA itself is a general recommendation, not a figure worked out "
               f"from this pool — it is the one target on this page that does not "
               f"adapt. The free-chlorine band above it does, which is why the two "
               f"are worth reading together."),
        up=[
          (DOM, "Stabiliser, added deliberately",
           f"About {lb(cya_lb(10))} per 10 ppm {here()}. It dissolves slowly — expect a "
           f"week before the test catches up, and do not re-dose in the meantime."),
          (MOD, "Trichlor tablets and dichlor",
           "Each carries CYA in with the chlorine. Convenient at first, then the CYA "
           "creeps past 80 and the FC requirement climbs with it. On a salt pool there "
           "is no reason to use them at all."),
        ],
        down=[
          (DOM, "Draining and refilling",
           "The only practical route. CYA is not consumed, not filtered, not burned "
           "off. Replacing a third of the water removes a third of the CYA."),
          (MIN, "Slow biological breakdown",
           "Real but unreliable — some pools lose 10–20 ppm over a winter, others none."),
        ],
        note="Nothing you can buy removes CYA. Every dose is effectively permanent, "
             "so under-dose and re-test rather than the reverse."),

      dict(key="ch", name="Calcium hardness", unit=" ppm", value=num(lab.get("ch")),
        band=TG["ch"], fmt="{:.0f}",
        what="Dissolved calcium. Not a sanitiser and not a nuisance — a structural "
             "requirement for plaster, tile grout and any exposed concrete.",
        why="Water that is short of calcium takes it from wherever it can: plaster, "
            "grout, the surface of a pebble finish. The damage is invisible for years "
            "and then it is etching. Too much and calcium precipitates as scale on "
            "the salt cell plates, where it cuts output.",
        up=[
          (DOM, "Calcium chloride", f"About {lb(cacl_lb(10))} of 77% flake per 10 ppm "
           f"here. Dissolve in a bucket first; it gets hot."),
          (MOD, "Cal-hypo shock", "Every shock quietly raises calcium — a real "
           "advantage when hardness is on the low side."),
          (MOD, "Evaporation", "Water leaves; calcium stays. Hardness only ever climbs "
           "between refills."),
        ],
        down=[
          (DOM, "Dilution", "Drain and refill, exactly as with CYA."),
          (MIN, "Deliberate precipitation", "Possible with flocculants at high pH, "
           "messy, and not worth it at this pool's level."),
        ],
        note="Low calcium is the quiet half of this pool's pH problem. It is what "
             "makes a low pH aggressive, and therefore what forces pH to sit higher "
             "than the chlorine would like."),

      dict(key="salt", name="Salt", unit=" ppm", value=num(lab.get("salt")),
        band=TG["salt"], fmt="{:.0f}",
        what="Sodium chloride in solution — the cell's raw material. At these levels "
             "it is about a tenth of seawater and barely tasteable.",
        why="The cell converts chloride to chlorine, the chlorine does its work and "
            "reverts to chloride. Salt is a catalyst in practice, not a consumable. "
            "Too little and the cell throttles back or refuses to run; too much and "
            "it corrodes fittings and trips the controller.",
        up=[(DOM, "Pool salt", f"About {lb(salt_lb(100))} per 100 ppm here. Brush it "
             "off the floor and run the pump for a day before re-testing."),
            (MOD, "Evaporation", "Concentrates whatever is already dissolved.")],
        down=[(DOM, "Water loss that carries salt out",
               "Splash-out, backwashing, rain overflow, draining. Not evaporation — "
               "that leaves salt behind."),
              (MIN, "Rain", "Dilutes only if the pool overflows.")],
        note="If salt reads low and no water has left the pool, suspect the sensor "
             "before the salt — cell probes drift as they scale up."),
    ]

# -------------------------------------------------------------------- rendering
BAR = {DOM: 3, MOD: 2, MIN: 1}

def driver(d):
    strength, title, body = d
    body = "".join(body) if isinstance(body, (list, tuple)) else body
    segs = "".join(f'<i class="{"on" if i < BAR[strength] else ""}"></i>' for i in range(3))
    return (f'<li><div class="dh"><span class="dn">{html.escape(title)}</span>'
            f'<span class="str"><span class="bars">{segs}</span>{strength}</span></div>'
            f'<p>{body}</p></li>')

def card(f):
    v, band, fmt = f["value"], f["band"], f["fmt"]
    from .render import verdict
    state, arrow = verdict(v, band)
    shown = fmt.format(v) if v is not None else "—"
    return f'''<article class="fac" id="{f["key"]}">
  <div class="fhead">
    <h3>{html.escape(f["name"])}</h3>
    <div class="fnow">
      <span class="fv">{shown}<span class="tu">{html.escape(f["unit"])}</span></span>
      <span class="pill p-{state}">{arrow} {state}</span>
      <span class="tt">target {fmt.format(band[0])}–{fmt.format(band[2])}</span>
    </div>
  </div>
  <p class="fwhat">{f["what"]}</p>
  <p class="fwhy"><b>Why it matters.</b> {f["why"]}</p>
  <div class="updown">
    <div class="ud up"><h4><span class="ar">▲</span> What raises it</h4><ul>{"".join(driver(d) for d in f["up"])}</ul></div>
    <div class="ud dn"><h4><span class="ar">▼</span> What lowers it</h4><ul>{"".join(driver(d) for d in f["down"])}</ul></div>
  </div>
  <p class="fnote"><b>Note.</b> {f["note"]}</p>
</article>'''

# Rows are (driver, factor, sign, strength) — the sign is the direction the
# driver pushes the factor, and a blank cell means no meaningful direct effect.
MATRIX = [
  ("Pump hours",        [("pH","+",2),("free_cl","+",3),("ta","",0),("cya","",0),("ch","",0),("salt","",0)]),
  ("Cell output %",     [("pH","+",2),("free_cl","+",3),("ta","",0),("cya","",0),("ch","",0),("salt","",0)]),
  ("Spa spillover",     [("pH","+",3),("free_cl","",0),("ta","",0),("cya","",0),("ch","",0),("salt","",0)]),
  ("Sheer descent",     [("pH","+",2),("free_cl","",0),("ta","",0),("cya","",0),("ch","",0),("salt","",0)]),
  ("Spa mode",          [("pH","+",3),("free_cl","+",2),("ta","",0),("cya","",0),("ch","",0),("salt","",0)]),
  ("Sunlight",          [("pH","",0),("free_cl","−",3),("ta","",0),("cya","",0),("ch","",0),("salt","",0)]),
  ("Pool / spa heater",  [("pH","+",2),("free_cl","−",2),("ta","",0),("cya","",0),("ch","",0),("salt","",0)]),
  ("Warm water",        [("pH","+",1),("free_cl","−",2),("ta","",0),("cya","",0),("ch","",0),("salt","",0)]),
  ("Muriatic acid",     [("pH","−",3),("free_cl","",0),("ta","−",3),("cya","",0),("ch","",0),("salt","",0)]),
  ("Bicarbonate",       [("pH","+",1),("free_cl","",0),("ta","+",3),("cya","",0),("ch","",0),("salt","",0)]),
  ("Liquid chlorine",   [("pH","+",1),("free_cl","+",3),("ta","",0),("cya","",0),("ch","",0),("salt","+",1)]),
  ("Cal-hypo shock",    [("pH","+",2),("free_cl","+",3),("ta","",0),("cya","",0),("ch","+",2),("salt","",0)]),
  ("Rain / refill",     [("pH","−",1),("free_cl","−",1),("ta","−",1),("cya","−",1),("ch","−",1),("salt","−",2)]),
  ("Evaporation",       [("pH","",0),("free_cl","",0),("ta","+",1),("cya","+",1),("ch","+",2),("salt","+",2)]),
]
COLS = [("pH","pH"),("free_cl","FC"),("ta","TA"),("cya","CYA"),("ch","CH"),("salt","Salt")]

# Strength said in the same three words the driver cards use, so the map and the
# cards cannot describe the same driver two different ways.
MX_WORD = {1: MIN, 2: MOD, 3: DOM}

def matrix():
    head = "".join(f'<th><a href="#{k}">{html.escape(n)}</a></th>' for k, n in COLS)
    body = []
    for name, cells in MATRIX:
        tds = []
        for (k, sign, w) in cells:
            if not sign:
                tds.append('<td class="m0"><span class="sr">no effect</span>·</td>')
            else:
                cls = "up" if sign == "+" else "dn"
                # Strength by COUNT, not by transparency. It used to be
                # opacity:.5 / .78, which meant the weakest real cell rendered
                # at 2.08:1 against the surface -- below half the contrast the
                # gate measures for that same token at full strength, and
                # unmeasurable by bin/contrast, which sees only the token. It is
                # also colour-alone in another currency: a reader who cannot
                # tell 50% from 78% lost the magnitude entirely. The same
                # count-not-shade idea .bars already uses for dose strength.
                word = MX_WORD.get(w, MOD)
                tds.append(f'<td class="m{w} {cls}" title="{html.escape(name)} '
                           f'{"raises" if sign=="+" else "lowers"} {k} — {word}">'
                           f'{sign * max(1, w)}'
                           f'<span class="sr"> {word}</span></td>')
        body.append(f'<tr><th scope="row">{html.escape(name)}</th>{"".join(tds)}</tr>')
    return (f'<div class="scroll"><table class="mx"><thead><tr><th></th>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')

def esc(v):
    return html.escape(str(v if v is not None else ""))

def dose_gap_reading(ratio, gal, ta):
    """The two other explanations for a dose that missed its prediction.

    THE DIRECTION FOLLOWS THE MEASUREMENT. This paragraph was fixed prose
    written for a pool whose doses came out STRONGER than predicted: "the pool
    may hold less water than the N gallons configured, which would make every
    dose stronger than predicted; or the alkalinity may have drifted ... which
    does the same thing". The heading three lines above it already derives
    which way the ratio went — and when it says "moved pH LESS than predicted",
    this paragraph explained the opposite of what had just been measured, and
    sent the reader to look for a smaller pool when the evidence pointed at a
    larger one.

    Same failure as the tension card and the alkalinity narrative before it:
    prose that states a direction is prose that goes wrong. Pure and
    module-level so the three cases can be checked.
    """
    if ratio is None:
        return ("Once a few more doses are logged, a ratio that stays away from one "
                f"will say something about either the {gal:,.0f} gallons configured "
                f"or the {ta:.0f} ppm of alkalinity the prediction assumed.")
    if ratio > 1.15:
        return (f"Two other readings of the same number are possible and only more "
                f"doses will separate them: the pool may hold <b>less</b> water than "
                f"the {gal:,.0f} gallons configured, which would make every dose "
                f"stronger than predicted; or the alkalinity may be <b>lower</b> than "
                f"the {ta:.0f} ppm last measured, which buffers less and does the same "
                f"thing. Both are worth knowing, and both show up as this ratio "
                f"staying above one.")
    if ratio < 0.85:
        return (f"Two other readings of the same number are possible and only more "
                f"doses will separate them: the pool may hold <b>more</b> water than "
                f"the {gal:,.0f} gallons configured, which would make every dose "
                f"weaker than predicted; or the alkalinity may be <b>higher</b> than "
                f"the {ta:.0f} ppm last measured, which buffers more and does the same "
                f"thing. Both are worth knowing, and both show up as this ratio "
                f"staying below one.")
    return (f"The ratio is close enough to one that the {gal:,.0f} gallons configured "
            f"and the {ta:.0f} ppm of alkalinity the prediction assumed are both "
            f"consistent with what this pool actually did. More doses will tighten "
            f"that rather than change it.")


def dose_response_block(latest):
    """Every logged dose against what the water actually did next.

    This is the table the whole project is for. Until a dose is logged there is
    nothing to check a prediction against, and the estimates on this page are
    textbook figures applied to an estimated volume — plausible, unverified, and
    quietly wrong in whichever direction this particular pool happens to differ.
    """
    chems = rows("chemicals.csv")
    readings = rows("readings.csv")
    if not chems or not readings:
        return ('<div class="card"><p class="empty">Nothing to check yet. Log a dose on '
                + _ask('the Chemicals tab and the next reading will be paired with it '
                       'here.', 'this pool\u2019s private dashboard and the next reading '
                       'will be paired with it here.') + '</p></div>')

    # The local dedup that used to be here has moved into render.rows(), which
    # is the one path every reader takes. This block having its own copy was the
    # tell: pairing a dose against a doubled reading is visibly wrong, so it got
    # fixed HERE and nowhere else, while the averages, the charts and the
    # notifier went on counting the measurement twice.
    obs = CH.dose_response(chems, readings, when, num)
    if not obs:
        return ('<div class="card"><p class="empty">A dose is logged, but there is no '
                'reading on both sides of it yet. The pod measures once a day.</p></div>')

    ta = num(latest.get("ta")) or 90.0
    # This whole block compares what a dose was PREDICTED to do against what it
    # actually did, and the prediction multiplies through the volume. With no
    # volume there is no prediction to compare against — and the closing
    # paragraph below states "the N gallons configured", which on an install
    # that has been told nothing read "the 15,000 gallons configured".
    gal = config.volume(CFG)
    if gal is None:
        return ('<div class="card"><p class="empty">Doses are logged and readings '
                'surround them, but this install has not been told how big the pool '
                'is — so there is no predicted effect to compare the measurements '
                'against. Set the volume in Settings, or work it out on the Pool '
                'Volume Calculator tab.</p></div>')
    out = []

    def n(v, fmt):
        """A number, or a dash when there is not one.

        Four of the five figures in a row here can legitimately be absent, and
        every one of them used to go straight into a format string. A calcium
        dose is the cheapest way to see it: calcium chloride has no pH effect at
        all, so the predicted pH is None and rendering the row raised
        TypeError \u2014 which does not spoil a cell, it takes down bin/render and
        the page with it. Logging an ordinary dose should not be able to do that.
        """
        return format(v, fmt) if isinstance(v, (int, float)) else "&mdash;"

    for o in obs:
        d = o["dose"]
        pred = CH.effects(d["chemical"], float(d["amount"]), d["unit"],
                          float(d["pct"]), gal, ta=ta) or {}
        p_ph = pred.get("ph")
        got = (o["ph_after"] - o["ph_before"]) if (o["ph_after"] is not None
                                                  and o["ph_before"] is not None) else None
        got_base = (o["ph_after"] - o["ph_baseline"]) if (o["ph_after"] is not None
                                                          and o["ph_baseline"] is not None) else None
        ratio = (got / p_ph) if (got and p_ph) else None
        label = CH.CHEMICALS.get(d["chemical"], {}).get("label", d["chemical"])
        # A chemical that does not move pH has nothing to say in a table about
        # pH response. Said once, rather than as three dashes and a ratio.
        no_ph = p_ph is None
        cells = (f'''<td class="n" colspan="3">does not move pH</td>''' if no_ph else
                 f'''<td class="n">{n(p_ph, "+.2f")}</td>
        <td class="n">{n(got, "+.2f")}<span class="rel">{n(got_base, "+.2f")} vs baseline</span></td>
        <td class="n">{n(ratio, ".1f")}{"" if ratio is None else "&times;"}</td>''')
        out.append(f'''<tr>
        <td>{esc(o["at"].strftime("%-d %b %H:%M"))}</td>
        <td>{esc(label)}<span class="rel">{esc(d["amount"])} {esc(d["unit"])} @ {esc(d["pct"])}%</span></td>
        {cells}
        <td>{n(o["hours_to_reading"], ".1f")} h later</td></tr>''')

    # The newest observation that actually says something about pH. obs[-1] was
    # whatever happened to be last, which on a calcium dose has no pH figures at
    # all and took the arithmetic below down with it.
    usable = [x for x in obs
              if x["ph_after"] is not None and x["ph_before"] is not None
              and (CH.effects(x["dose"]["chemical"], float(x["dose"]["amount"]),
                              x["dose"]["unit"], float(x["dose"]["pct"]), gal,
                              ta=ta) or {}).get("ph")]
    note = ""
    if usable:
        o = usable[-1]
        d = o["dose"]
        pred = CH.effects(d["chemical"], float(d["amount"]), d["unit"],
                          float(d["pct"]), gal, ta=ta)
        got = o["ph_after"] - o["ph_before"]
        floz = CH.to_base(d["chemical"], float(d["amount"]), d["unit"])
        per_tenth = abs(floz / got * 0.1) if got else None
        est_tenth = abs(0.1 / CH.effects("acid", 1, "floz", 31.45, gal, ta=ta)["ph"])
        ratio = abs(got / pred["ph"]) if pred.get("ph") else None

        # The direction FOLLOWS the measurement. This used to open with "moved pH
        # harder than predicted" as fixed prose, which is only true while the
        # ratio is above one — the same failure the alkalinity narrative was
        # rewritten to avoid.
        if ratio is None:
            head = "The doses logged so far do not yet pin down this pool's response."
        elif ratio > 1.15:
            head = "The measurements so far moved pH <b>harder</b> than predicted."
        elif ratio < 0.85:
            head = "The measurements so far moved pH <b>less</b> than predicted."
        else:
            head = "The measurements so far are close to the predicted response."

        gap_note = dose_gap_reading(ratio, gal, ta)
        if per_tenth is not None:
            note = (f'''<p class="sub" style="margin:14px 0 0"><b>{head}</b> Working
    backwards from {"it" if len(usable) == 1 else "the most recent"}, this pool takes
    about <b>{per_tenth:.0f} fl oz</b> of {POOL.get("acid_pct", 31.45):g}% muriatic per
    0.1 pH, against an estimate of {est_tenth:.0f} fl oz. Believe the estimate for now
    \u2014 {"one dose" if len(usable) == 1 else f"{len(usable)} doses"}, read
    {o["hours_to_reading"]:.1f} hours later with the pump running only part of the day,
    is not a fit. Acid needs a full turnover to mix, and a pod sitting in the skimmer
    sees whatever is nearest it.</p>
    <p class="sub" style="margin:10px 0 0">{gap_note}</p>''')
    else:
        note = ('''<p class="sub" style="margin:14px 0 0">Nothing logged so far moves pH,
    so there is no measured response to compare against yet. Log an acid dose and the
    comparison appears here.</p>''')

    return f'''<div class="card">
    <div class="scroll"><table>
      <thead><tr><th>When</th><th>What went in</th><th>Predicted pH</th>
        <th>Measured pH</th><th>Ratio</th><th>Reading</th></tr></thead>
      <tbody>{"".join(out)}</tbody></table></div>
    {note}
  </div>'''

def cell_ph_block(latest, TG):
    """Turning the cell down slows the climb. It does not, by itself, pull pH
    back — and the difference is worth being explicit about, because acting on
    the wrong one of those wastes a week.
    """
    ta = num(latest.get("ta"))
    ph = num(latest.get("ph"))
    fc = num(latest.get("free_cl"))
    ceiling = co2_ceiling(ta)

    ceil_txt = ""
    if ceiling:
        lower_ta = max(ta - 15, 40)
        lower = co2_ceiling(lower_ta)
        ceil_txt = (f'<p class="v-why"><b>Where the rise stops.</b> Aeration can only '
                    f'drive off the CO₂ the water is actually carrying. At alkalinity '
                    f'{ta:.0f} ppm that runs out around <b>pH {ceiling:.1f}</b>, and no amount '
                    f'of spillover will push past it — the pool simply sits there. Taking '
                    f'alkalinity down to {lower_ta:.0f} would move the ceiling to about '
                    f'{lower:.1f}. That is a second and far more concrete reason for the '
                    f'alkalinity a salt pool wants than "it buffers less": it says where pH '
                    f'will stop.</p>')

    obs = ""
    if ceiling and ph is not None:
        near = ph >= ceiling - 0.25
        obs = (f'<p class="v-why"><b>Where this pool is.</b> pH {ph:.2f} against a ceiling '
               f'near {ceiling:.1f}. ' + (
               "That is close enough that aeration has largely stopped being what pushes it "
               "up — what is left is the cell." if near else
               "There is still headroom, so the spillover is doing real work on pH and the "
               "cell is not the only driver.") + "</p>")

    caveat = ""
    if fc is not None and fc > 4:
        caveat = (f'<p class="v-warn"><b>One caution before reading anything into a pH '
                  f'change.</b> Free chlorine is {fc:.1f} ppm, and a high chlorine level '
                  f'biases a phenol-red pH reading <em>upward</em> — the dye is bleached and '
                  f'the colour over-reads. So when chlorine comes down, part of any apparent '
                  f'pH drop can be the measurement correcting rather than the water changing. '
                  f'The way to tell them apart is a reading taken once free chlorine is back '
                  f'in range, or a second opinion from the lab.</p>')

    return ('<div class="verdict" style="border-left-color:var(--spa)">'
            '<p class="v-why"><b>Turning the cell down slows the climb; it does not pull pH '
            'back.</b> The cell raises pH because the hydrogen it makes at the cathode leaves '
            'as gas, so the hydroxide that came with it is never neutralised — and because '
            'those bubbles aerate. Both scale with output multiplied by pump hours, so halving '
            'the dial halves the push. But removing a riser is not the same as adding an acid: '
            'on its own it lets pH level off, and something acidic — rain, fresh fill water, or '
            'acid you pour — is still what brings a number down.</p>'
            + ceil_txt + obs + caveat + record_note()
            + '</div>')

def record_note():
    """What the stored readings actually support, as opposed to what would be
    satisfying to conclude. Written from the files rather than as prose, so it
    stops being wrong the moment more data arrives.
    """
    rs = rows("readings.csv")
    seen, uniq = set(), []
    for r in rs:
        k = r.get("measured", "")[:16]
        if k and k not in seen:
            seen.add(k); uniq.append(r)
    les = rows("leslies.csv")

    bits = []
    if len(uniq) >= 2:
        a, b = uniq[0], uniq[-1]
        fa, fb = num(a.get("free_cl")), num(b.get("free_cl"))
        pa, pb = num(a.get("ph")), num(b.get("ph"))
        # Each half is written only where the pod actually recorded that measure.
        # Both were formatted unconditionally: a pod that reports pH and leaves
        # free chlorine blank -- which is what a flow fault looks like -- raised
        # TypeError inside the f-string and took the WHOLE render down, both
        # builds, with nothing on the page to say why. None is absent, and absent
        # is a sentence you do not write, not a zero and not a crash.
        parts = []
        if fa is not None and fb is not None:
            # And the reading is described by its DIRECTION rather than by the
            # conclusion that was wanted. "which is what a reduced cell output
            # looks like" was printed over a rise as readily as over a fall.
            if fb < fa - 0.1:
                fc_says = ("which is the fall a reduced cell output would "
                           "produce")
            elif fb > fa + 0.1:
                fc_says = ("which is a RISE, and not what cutting cell output "
                           "does — so something else is putting chlorine in, or "
                           "demand has dropped")
            else:
                fc_says = "which is no real change either way"
            parts.append(f'Free chlorine {fa:.1f} &rarr; {fb:.1f} ppm across '
                         f'{len(uniq)} readings, {fc_says}.')
        else:
            parts.append(f'The pod has {len(uniq)} readings on file and no free '
                         f'chlorine among them.')
        if pa is not None and pb is not None:
            parts.append(f'pH {pa:.2f} &rarr; {pb:.2f}.'
                         if abs(pb - pa) >= 0.05 else
                         f'pH has not moved: {pa:.2f} on both.')
        bits.append(
            '<p class="v-why"><b>What the pod has recorded.</b> '
            + " ".join(parts) +
            f' {len(uniq)} readings is not a trend, and this is exactly the case the '
            f'logging exists to settle — a fortnight at the new output will show '
            f'whether the climb stopped, reversed, or merely paused.</p>')

    # The one genuine pH fall in the record is worth putting up precisely because
    # it does not fit the story: chlorine rose while pH fell, which is the
    # opposite of what cutting cell output would do.
    lp = [(r["measured"], num(r.get("ph")), num(r.get("free_cl"))) for r in les
          if num(r.get("ph")) is not None]
    drops = [(lp[i - 1], lp[i]) for i in range(1, len(lp)) if lp[i][1] < lp[i - 1][1] - 0.05]
    if drops:
        (d0, p0, f0), (d1, p1, f1) = drops[-1]
        same_way = (f1 is not None and f0 is not None and f1 < f0)
        bits.append(
            f'<p class="v-why"><b>The one real fall in the record.</b> The lab has pH '
            f'{p0:.1f} on {d0} and {p1:.1f} on {d1} — a genuine drop with no acid logged. '
            + (f'Free chlorine <b>rose</b> over the same stretch, {f0:.1f} to {f1:.1f} ppm, '
               f'which is the opposite of what cutting cell output would do. So whatever '
               f'moved pH then, it was not a smaller cell — worth remembering before '
               f'crediting the next fall to one.'
               if not same_way else
               f'Free chlorine fell with it, {f0:.1f} to {f1:.1f} ppm, which is consistent '
               f'with less production.') + '</p>')
    return "".join(bits)

# Whether THIS render is the public build. Set by panel() and read by the empty
# states below.
#
# Those states end with operator instructions — "Record one on Settings",
# "Log a dose on the Chemicals tab" — and this module's panels are rendered into
# BOTH builds. The public one has no Settings and no Chemicals tab, so a cold
# install told anonymous readers to use two screens that are not in their file.
# The build's dead-link assertion only catches `data-tab-link=` in markup, so
# prose naming a tab in plain text passed it.
#
# A module global rather than a parameter threaded through five call levels,
# which is how CFG/DATA/POOL already work here.
PUBLIC = False

def _ask(private, public_alt):
    """An instruction, or the version of it a public reader can act on."""
    return public_alt if PUBLIC else private

def panel(public=False):
    global PUBLIC
    PUBLIC = public
    # newest MEASURED, not last appended, and all three sources — the same
    # reconciliation render.build() does. This page and Home read the same CSVs
    # and must not be able to disagree about what the current alkalinity is;
    # keeping a second, shorter version of the selection here is exactly how
    # they would.
    from .render import newest
    readings = rows("readings.csv")
    lab      = rows("lab.csv")        # WaterGuru — mailed-in sample, their lab
    leslies  = rows("leslies.csv")    # Leslie's — bench photometer, in store
    manual   = rows("manual.csv")     # a kit, strips, another store
    wg_lab   = newest(lab)
    photo    = newest(leslies)
    byhand   = newest(manual)
    LAB      = best_lab((wg_lab, "WaterGuru"), (photo, "Leslie's"),
                        (byhand, "By hand"))

    latest = dict(newest(readings))
    latest.update({k: v["value"] for k, v in LAB.items()})
    TG = targets(LAB, POOL.get("sanitiser"), photo.get("sanitizer", ""))
    F = factors(latest, TG)

    src_line = ""
    if LAB:
        by = {}
        for k, v in LAB.items(): by.setdefault(v["source"], []).append(v["measured"][:10])
        src_line = " · ".join(f"{s} {sorted(d)[-1]}" for s, d in by.items())

    ph = num(latest.get("ph")); ch = num(latest.get("ch"))
    hocl = {7.0:75, 7.2:63, 7.4:52, 7.5:50, 7.6:40, 7.8:30, 8.0:22}
    def hocl_at(p):
        ks = sorted(hocl); p = min(max(p, ks[0]), ks[-1])
        for a, b in zip(ks, ks[1:]):
            if a <= p <= b:
                return hocl[a] + (hocl[b] - hocl[a]) * (p - a) / (b - a)
        return hocl[ks[-1]]

    ta = num(latest.get("ta"))
    # AN EMPTY STATE, NOT AN EMPTY SECTION. With no pH or no calcium this left
    # `tension` as "", so the page rendered the "Where the advice conflicts"
    # heading, a lede promising "this is the one that matters here", and then
    # nothing at all -- with two nav entries pointing at it. Same shape as the
    # empty state dose_response_block already uses: say which reading is
    # missing, because that is the actionable half.
    missing = [w for w, v in (("a pH reading", ph), ("a calcium reading", ch)) if not v]
    tension = ("" if not missing else
               f'<p class="empty">This needs a pH and a calcium reading before it can '
               f'say which rule wins, and there is no {" or ".join(missing)} on file. '
               + _ask('Record one on Settings, or wait for the next lab result.',
                      'It will appear once the next lab result arrives.') + '</p>')
    if ph and ch:
        active_now, active_74 = hocl_at(ph), hocl_at(7.4)
        ch_target = TG["ch"][1]

        # Calcium is the lever in both cases; what changes is whether alkalinity
        # is a second job or already done. Getting this wrong sends someone to
        # lower a TA that is already correct.
        resolve = resolution(ch, ta, TG)

        fc = num(latest.get("free_cl"))
        # Derived, not written down. The chlorine sentence used to say "The
        # measured {fc} ppm" with fc defaulted to 0, so a pool with no free
        # chlorine reading was told it had none — the most alarming number this
        # product can print, manufactured from a blank cell.
        if fc is None:
            fc_clause = (" There is no free-chlorine reading on file, so how much "
                         "of it is actually working cannot be said.")
        else:
            # "MOST OF IT SWITCHED OFF" IS ONLY TRUE ABOVE 7.4. The clause was
            # written for this pool at 7.8 and then printed unconditionally, so
            # at pH 7.2 -- where MORE of the chlorine is active than it would be
            # at 7.4 -- it reported the larger figure first and called it the
            # loss. The comparison is the same arithmetic either way; which one
            # is the good news is not.
            fc_clause = (f" The measured <b>{fc:.1f} ppm</b> is therefore doing the "
                         f"work of about <b>{fc * active_now / 100:.1f} ppm</b> today, "
                         f"against <b>{fc * active_74 / 100:.1f} ppm</b> if pH were "
                         f"7.4"
                         + (" — the same chlorine, most of it switched off."
                            if active_now < active_74 else
                            " — the same chlorine, and more of it working than 7.4 "
                            "would give you."
                            if active_now > active_74 else
                            ", which is where it already is."))

        # The drop and the projected index were the literals 0.4 and -0.5, which
        # are only true at pH 7.8 and one particular SI. They were a constant
        # wearing the shape of a calculation.
        # NOT clamped. This was max(0.0, ph - 7.4), so a pool below 7.4 was told
        # "dropping pH by 0.0 drops SI by 0.0" -- arithmetic about a move nobody
        # is making, printed under a heading asserting chlorine wants pH down
        # when it is already down. The card states which way the water actually
        # has to move, and says plainly when there is no tension to resolve.
        si_move = ph - 7.4
        si_drop = abs(si_move)
        si_now = num(latest.get("saturation_index"))
        if si_now is None:
            si_clause = (" No saturation index has been reported, so where pH 7.4 "
                         "would leave the water cannot be projected.")
        else:
            si_src = (LAB.get("saturation_index") or {}).get("source") or "the lab"
            # SI moves one-for-one WITH pH, in the direction pH moves. Going
            # from 7.2 to 7.4 RAISES it; subtracting a clamped magnitude got
            # that backwards for every pool below 7.4.
            proj = si_now - si_move
            si_clause = (f" {esc(si_src)} last reported SI <b>{si_now:+.1f}</b>, so pH "
                         f"7.4 at today's calcium of {ch:.0f} ppm would put the water "
                         f"near <b>{proj:+.1f}</b>"
                         + (" — aggressive enough to pull calcium out of plaster and "
                            "grout rather than deposit it." if proj < -0.3
                            else ", which the plaster can live with."))
        lede, h_cl, h_pl, si_sentence, resolve_lede = tension_framing(ph)
        tension = f'''<div class="card tension">
  <h3>The tension this pool is actually in</h3>
  <p>{lede}</p>
  <div class="tgrid">
    <div class="tside">
      <h4>{h_cl}</h4>
      <p>At pH {ph:.1f}, roughly <b>{active_now:.0f}%</b> of free chlorine is in the
      active HOCl form. At 7.4 it would be <b>{active_74:.0f}%</b>.{fc_clause}</p>
    </div>
    <div class="tside">
      <h4>{h_pl}</h4>
      <p>{si_sentence}{si_clause}</p>
    </div>
  </div>
  <p class="resolve"><b>{resolve_lede}</b> {resolve}</p>
</div>'''

    doses = "".join(
      f'<tr><td>{html.escape(a)}</td><td class="n">{html.escape(b)}</td><td>{c}</td></tr>'
      for a, b, c in [
        (f"Muriatic acid {ACID:g}%", oz(acid_floz(0.1)),
         f"lowers pH 0.1 (and TA ~{acid_ta_cost(0.1):.0f} ppm)"),
        (f"Muriatic acid {ACID:g}%", oz(acid_floz(0.25)), "lowers TA 10 ppm (and pH ~0.25)"),
        ("Muriatic acid 14.5%", oz(acid_floz(0.1, 14.5)), "lowers pH 0.1 — half strength, double the pour"),
        ("Sodium bicarbonate", lb(bicarb_lb(10)), "raises TA 10 ppm"),
        ("Liquid chlorine 12.5%", oz(liquid_cl_floz(1)), "raises FC 1 ppm"),
        ("Liquid chlorine 12.5%", oz(liquid_cl_floz(5)), "raises FC 5 ppm — a shock dose"),
        ("Cal-hypo 73%", lb(calhypo_lb(1)), "raises FC 1 ppm, CH ~0.7 ppm"),
        ("Calcium chloride 77%", lb(cacl_lb(10)), "raises CH 10 ppm"),
        ("Stabiliser (CYA)", lb(cya_lb(10)), "raises CYA 10 ppm — slow, do not re-dose early"),
        ("Pool salt", lb(salt_lb(100)), "raises salt 100 ppm"),
      ])

    # The table names a chemical, an amount and a purity and then stopped --
    # the reader is one step from doing the thing and the page offered no route
    # to it. Build-aware, because on the public build the Chemicals tab does not
    # exist and linking to it would land the reader nowhere.
    from .help import _tablink
    dose_act = ("Adding one of these? Log it on "
                + _tablink("chemicals", "Chemicals", public=public)
                + " — a dose that is recorded is a change the model can attribute, "
                  "and one that is not is noise it has to absorb.")
    return fill(PANEL,
        dose_act=dose_act,
        # The headline figure. Rendered as "15,000 gal" on an install that had
        # been told nothing — the single most prominent fabricated number on the
        # page, directly above prose calling it "this pool".
        vol_badge=(f'<div class="vol"><b>{GAL:,.0f} gal</b>'
                   f'<span>{" (estimated)" if EST else ""}</span></div>'
                   if GAL_SET else
                   '<div class="vol"><b>volume not set</b>'
                   '<span>every dose figure multiplies through it</span></div>'),
        # "not copied from a generic chart" is only true once somebody has said
        # how big this pool is. On an install that has not, the table below IS a
        # generic chart for a notional 15,000 gallon pool — asserting otherwise
        # is the one claim on this page that would be flatly false.
        dose_basis=(f"Computed from {GAL:,.0f} gallons"
                    f"{' (estimated)' if EST else ''}, not copied from a generic "
                    f"chart." if GAL_SET else
                    "No pool volume has been set, so the amounts below are for a "
                    "notional 15,000 gallon pool and are NOT about your water. Set "
                    "the volume in Settings and they become specific to it."),
        # Three states, not two. "Nobody has said how big this pool is" and
        # "somebody estimated it" are different facts, and branching on EST
        # alone printed the second one directly under a badge reading "volume
        # not set" — the page asserting both at once, in adjacent elements.
        est_note=(("No volume has been set at all, so there is nothing below to "
                   "be uncertain about in the usual way: the amounts are for a "
                   "notional 15,000 gallon pool. Set the volume in Settings and "
                   "they start describing this water."
                   if not GAL_SET else
                   "Volume is an estimate, so every dose below inherits that "
                   "uncertainty — treat the first pour of anything as a probe, log it, "
                   "and let the measured response correct the number."
                   if EST else "Volume is measured.")
                  + ("" if TA_SET else
                     " No lab has reported this pool's alkalinity, so the acid "
                     "figures are scaled by a nominal 90 ppm rather than a measured "
                     "one. Acid moves pH further in a less buffered pool, so if the "
                     "real figure is lower these amounts are too large — record a "
                     + _ask("test result on Settings and they become specific to this ",
                            "test result and they become specific to this ") +
                     "water.")),
        cards="".join(card(f) for f in F), matrix=matrix(), tension=tension,
        cell_ph=cell_ph_block(latest, TG),
        dose_response=dose_response_block(latest),
        src_line=html.escape(src_line or "no lab tests on file"),
        # Home is behind the sign-in, and this tab is not: on the public build
        # _tablink names it in plain text instead of linking somewhere this
        # file cannot go. Hard-coded here once, which the build caught the
        # moment Home was reclassified -- that check is why this line is a
        # token rather than an anchor.
        home_link=_tablink("home", "lab history on Home", public=public),
        doses=doses, acid=f"{ACID:g}")

EXTRA_CSS = """
.lede { font-size:15.5px; line-height:1.62; color:var(--ink2); max-width:68ch;
  margin:0 0 8px; }
.lede b { color:var(--ink); font-weight:600; }
.vol { display:inline-flex; align-items:baseline; gap:6px; background:var(--panel);
  border:1px solid var(--line); border-radius:8px; padding:5px 11px;
  font-variant-numeric:tabular-nums; font-size:13px; margin:10px 0 4px; }
.vol b { font-size:16px; }
.toc { display:flex; gap:7px; flex-wrap:wrap; margin:16px 0 30px; }
.toc a { font-size:12.5px; padding:5px 11px; border-radius:99px; text-decoration:none;
  background:var(--panel); border:1px solid var(--line); color:var(--ink2); }
.toc a:hover { color:var(--ink); border-color:var(--ink3); }

.fac { background:var(--panel); border:1px solid var(--line); border-radius:12px;
  padding:18px 20px 16px; box-shadow:var(--shadow); margin-bottom:14px;
  scroll-margin-top:14px; }
.fhead { display:flex; align-items:baseline; gap:14px; flex-wrap:wrap;
  padding-bottom:11px; border-bottom:1px solid var(--line); margin-bottom:13px; }
.fhead h3 { font-size:17px; margin:0; letter-spacing:-.2px; }
.fnow { display:flex; align-items:baseline; gap:9px; margin-left:auto; flex-wrap:wrap; }
.fv { font-size:20px; font-weight:640; font-variant-numeric:tabular-nums; }
.fwhat { font-size:13.5px; color:var(--ink2); margin:0 0 9px; max-width:72ch; }
.fwhy { font-size:13.5px; color:var(--ink2); margin:0 0 15px; max-width:72ch; }
.fwhy b, .fnote b { color:var(--ink); }
.updown { display:grid; gap:14px; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); }
.ud h4 { font-size:11.5px; text-transform:uppercase; letter-spacing:.07em;
  margin:0 0 9px; display:flex; align-items:center; gap:6px; }
.ud .ar { font-size:9px; }
.ud.up h4 { color:var(--warn); }
.ud.dn h4 { color:var(--good); }
.ud ul { list-style:none; margin:0; padding:0; }
.ud li { padding:9px 0 9px 11px; border-left:2px solid var(--line); margin-bottom:2px; }
.ud.up li { border-left-color:color-mix(in srgb, var(--warn) 45%, var(--line)); }
.ud.dn li { border-left-color:color-mix(in srgb, var(--good) 45%, var(--line)); }
.dh { display:flex; align-items:center; gap:9px; flex-wrap:wrap; margin-bottom:3px; }
.dn { font-size:13px; font-weight:600; }
.str { font-size:10.5px; color:var(--ink3); text-transform:uppercase;
  letter-spacing:.05em; display:inline-flex; align-items:center; gap:5px; }
.bars { display:inline-flex; gap:2px; }
.bars i { width:4px; height:9px; border-radius:1px; background:var(--line);
  display:inline-block; }
.bars i.on { background:var(--ink3); }
.ud li p { font-size:12.5px; color:var(--ink2); margin:0; line-height:1.55; max-width:60ch; }
.fnote { font-size:12.5px; color:var(--ink2); margin:15px 0 0; padding-top:12px;
  border-top:1px solid var(--line2); max-width:74ch; }

table.mx { font-size:12.5px; table-layout:fixed; min-width:640px; }
table.mx th[scope=row] { width:30%; text-transform:none; letter-spacing:0; font-size:12.5px;
  color:var(--ink2); font-weight:500; padding-right:14px; border-bottom:1px solid var(--line2); }
table.mx thead th a { color:var(--ink3); text-decoration:none; }
table.mx thead th a:hover { color:var(--ink); }
table.mx td { text-align:center; font-weight:700; font-size:14px;
  border-bottom:1px solid var(--line2); padding:6px 0; }
table.mx td.up { color:var(--warn); }
table.mx td.dn { color:var(--good); }
table.mx td.m0 { color:var(--line); font-weight:400; }
/* No opacity ramp here. Strength is the number of marks (see matrix()), so
   every glyph renders at the full token contrast bin/contrast measures. */
table.mx td.m1, table.mx td.m2, table.mx td.m3 { letter-spacing:-0.5px; }
.sr { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); }
.mxkey { font-size:12px; color:var(--ink3); margin-top:10px; display:flex; gap:16px;
  flex-wrap:wrap; }

.tension { border-left:3px solid var(--pump); }
.tension h3 { font-size:15px; margin:0 0 8px; }
.tension p { font-size:13.5px; color:var(--ink2); max-width:74ch; }
.tgrid { display:grid; gap:16px; grid-template-columns:repeat(auto-fit,minmax(290px,1fr));
  margin:14px 0 4px; }
.tside { background:var(--bg); border-radius:9px; padding:13px 15px; }
.tside h4 { margin:0 0 7px; font-size:12.5px; }
.tside p { margin:0; font-size:13px; }
.resolve { border-top:1px solid var(--line); padding-top:13px; margin-top:14px !important; }
"""

# @@name@@ and render.fill(), not str.format. CLAUDE.md forbids str.format on a
# page template for a concrete reason: the moment this gains a <style> rule, an
# inline script or any CSS at all, every literal brace has to be doubled or the
# whole tab raises at render time. It happened to contain no braces, so it
# happened to work -- luck, not design, and the seam that already exists raises
# on an unfilled token instead of on a stylesheet.
PANEL = """<p class="lede">Six numbers describe this pool, and every one of them is pushed by
something. Some of those things are equipment that runs on a schedule, some are the
weather, and some are what gets poured in. This is the causal map: for each factor,
<b>what raises it, what lowers it, and by how much in this pool</b> — because a dose
is a volume, and a volume is meaningless without knowing the pool it goes into.</p>

@@vol_badge@@
<p class="lede" style="font-size:13px">@@est_note@@</p>
<p class="lede" style="font-size:12.5px">Lab values here come from @@src_line@@.
WaterGuru analyses a posted sample, Leslie's a carried-in one, and anything entered
by hand is whatever you tested with. Where more than one reports a measure the more
recent result is used and they are never averaged; the
@@home_link@@ shows how each has been
moving.</p>

<div class="toc">
  <a href="#ph">pH</a><a href="#free_cl">Free chlorine</a><a href="#ta">Alkalinity</a>
  <a href="#cya">Cyanuric acid</a><a href="#ch">Calcium</a><a href="#salt">Salt</a>
  <a href="#matrix">Interaction map</a><a href="#doses">Dose table</a><a href="#tension">The tension</a>
</div>

<section>@@cards@@</section>

<section id="dose-response">
  <h2>What your doses actually did</h2>
  <p class="sub">Every logged addition against the reading that followed it. This is
  the table the rest of the page is working towards: until a dose is checked against
  the water, every figure here is a textbook number applied to an estimated volume —
  plausible, unverified, and wrong in whichever direction this pool happens to differ.</p>
  @@dose_response@@
</section>

<section id="cell-and-ph">
  <h2>What the salt cell does to pH, and where the rise stops</h2>
  <p class="sub">Two things worth separating, because they behave differently and
  the difference decides what turning the cell down will actually achieve.</p>
  @@cell_ph@@
</section>

<section id="tension">
  <h2>Where the advice conflicts</h2>
  <p class="sub">Correct rules can still contradict each other. This is the one that
  matters here.</p>
  @@tension@@
</section>

<section id="matrix">
  <h2>Interaction map</h2>
  <p class="sub">Every driver down the side, every factor across the top. A cell shows
  the direction that driver pushes that factor, and how many marks it carries is how
  much that driver matters — <b>+</b> minor, <b>++</b> moderate, <b>+++</b> dominant, the
  same three words the driver cards use. Read a row to
  see what one action does to the whole pool at once — the reason no single dose is
  ever a single change.</p>
  <div class="card">
    @@matrix@@
    <div class="mxkey">
      <span><b style="color:var(--warn)">+</b> raises</span>
      <span><b style="color:var(--good)">−</b> lowers</span>
      <span><b style="color:var(--ink3)">·</b> no direct effect</span>
      <span>one mark minor &middot; two moderate &middot; three dominant</span>
    </div>
  </div>
</section>

<section id="doses">
  <h2>Dose reference for this pool</h2>
  <p class="sub">@@dose_basis@@
  These are starting estimates — poolhound replaces them with this pool's measured
  response as logged doses accumulate, which is the entire point of logging them.</p>
  <div class="card"><div class="scroll"><table>
    <thead><tr><th>Add</th><th>Amount</th><th>Effect</th></tr></thead>
    <tbody>@@doses@@</tbody></table></div></div>
  <p class="sub" style="margin:10px 0 0">@@dose_act@@</p>
</section>

<section>
  <h2>How to use this with the data</h2>
  <div class="card">
    <p class="sub" style="margin:0 0 10px"><b>The heaters are in here twice on purpose.</b>
    They appear as a driver in their own right and again as "warm water", because they
    act only through temperature — and so does the sun. Water temperature is sampled
    directly, so the fit can use the thing that actually causes the effect instead of
    heater runtime standing in for it, and can then report whether heater hours add
    anything beyond the temperature they produce.</p>
    <p class="sub" style="margin:0">Everything above is textbook direction with
    arithmetic on this pool's volume. What it cannot tell you is the size of the
    coefficients <em>here</em> — how much pH this particular spillover adds per hour,
    or what the cell really produces at 40%. That only comes from pairing daily
    chemistry against daily runtime, which is what the dashboard collects. Log every
    addition with its strength; the fit is only as good as the record of what went in.</p>
  </div>
</section>
"""

if __name__ == "__main__":
    print(f"  chemistry panel: {len(panel()):,} bytes")
