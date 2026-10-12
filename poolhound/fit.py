"""What moves this pool: THEORY.md's coefficient table, fitted from its own record.

WHY THIS EXISTS
  "Measuring what actually moves this pool's chemistry" is the sentence the
  project opens with, and until this module the measuring stopped at two
  numbers: trajectory()'s P and k for free chlorine, and the acid ratio the
  dose table works out one dose at a time. THEORY.md ends with a table of
  eight coefficients "the fit should recover". This is that fit.

IDENTIFIABILITY IS THE OUTPUT, NOT A FOOTNOTE TO IT
  Most of what a regression on one pool's record can say is that it cannot
  yet say something. The cell and the spillover both scale with pump hours,
  and with the dial left alone they are the same column twice; sun and
  temperature rise together across a season; the sheer descent may have run
  on four days of eighty. A fit that printed a number for each of those would
  be printing the arithmetic's choice of how to split one effect between two
  names, at full confidence. So every coefficient comes back as one of:

    measured   its interval is clear of zero, on the side physics says
    combined   it cannot be told apart from a named partner on this record,
               and the figure printed is the two TOGETHER — with the ratio
               they ran at, which is what makes the combined figure usable
    not yet    the interval spans zero, the sign is wrong, or the input never
               varied — and in each case the row says what variation in the
               record would settle it, and roughly how much more

  which is the shape a future experiment planner needs: "need N more days with
  X varied", not "insufficient data".

THE TWO MODELS, AND WHAT THE LINEARISATION ASSUMES
  Between two consecutive daily readings from the SAME instrument (never a
  pod reading paired with a lab's — CLAUDE.md: two labs disagreeing is data,
  and a pair across them measures the labs, not the pool):

    dFC  =  k_prod · Σ(dial/100 · pump h)
          − FC̄ · ( k_uv · sun  +  k_dem · days  +  k_temp · days · (T − T̄)
                   + k_cya · sun · (CYA − CYĀ) )
          + chlorine poured in (chemicals.effects(), subtracted, not fitted)

    dpH  =  k_spill · pump h  +  k_cell · Σ(dial/100 · pump h)
          + k_sheer · sheer h  +  k_spa · spa h  +  k_heat · heater h
          + k_temp_pH · days · (T − T̄)

  FC̄ is the mean of the two readings: the trapezoid rule for the integral of
  FC over the stretch, the same approximation trajectory() makes. It assumes
  FC moves roughly linearly between readings; it does not — it climbs while
  the cell runs and falls under the sun — so k_uv is the loss rate against the
  MORNING level, which is the level the pod reads. Loss is linear in the
  rate's parts, which is THEORY.md's "UV destroys a fraction, not a fixed
  amount". CYA enters as a first-order term about the record's own mean: it
  says what stabiliser does within the range this pool has actually held, and
  nothing outside it. Temperature and CYA are centred so k_dem is the demand
  at this record's mean water temperature and k_uv the sun loss at its mean
  stabiliser — said beside the figures, because a coefficient quoted without
  the conditions it was measured at is half a number.

  pH has no intercept, as in THEORY.md, and that was tried the other way: a
  per-day drift term "with nothing running" is physically fair — a still
  surface does exchange CO₂ — but on a simulated pool whose pump ran four to
  nine hours a day it doubled the spillover's interval and left it straddling
  zero. A pump that runs every day makes "per day" and "per pump hour" nearly
  the same column, so whatever a still surface does lands inside the
  spillover figure, which is where THEORY.md already puts it.

  Acid is NOT a regressor. Its response is the dose table's measurement
  (chemicals.dose_response(), reused): each settled acid dose against the
  readings either side, with the drift the pH model measured over those hours
  taken out. A stretch holding an acid dose is left out of the pH fit rather
  than having a predicted effect subtracted, because the prediction is the
  very thing THEORY.md records as off by 1.4–1.9×, and subtracting a figure
  known to be wrong would hand the error to the per-hour terms.

WHAT IS LEFT OUT, AND BY WHICH EXISTING RULE
  The partial day: a stretch is only a stretch once its closing reading
  exists, so today is never in it (trajectory()'s rule, by construction).
  A stretch the panel was not heard for (upkeep.WHOLE_DAY_SAMPLES' fraction):
  an outage is not a pump that was off. A stretch opening or closing inside a
  dose's settle window (upkeep.SETTLE_H). Water temperature only with flow
  (commands.drop_stale_flow_readings(), again at read time). A reading the
  owner has marked as fouled is already gone — render.rows() applies
  corrections.csv — and that is the ONLY outlier rule: large residuals are
  listed for a person to look at, never dropped by the fit.

  CYA is held constant between lab results, never interpolated (THEORY.md).
  Rain and evaporation are recorded, and still not used to move it: whether
  rain dilutes depends on whether the level overflowed or was pumped down,
  and evaporation concentrates nothing if an autofill tops up with fresh
  water. The record holds neither, so the rain since the last CYA is SAID
  rather than modelled.

STDLIB ONLY
  No numpy, deliberately: the image is stdlib plus nine pins, and the largest
  system solved here is seven by seven. Gauss-Jordan for the inverse, Jacobi
  for the eigenvalues the collinearity test needs, a seeded bootstrap for an
  interval that does not lean on equal variance.
"""
import bisect
import datetime as dt
import html
import math
import random

from . import chemicals as CH
from . import commands as _cmd
from . import upkeep as _up

# ---------------------------------------------------------------- the rules
# Consecutive readings this far apart are a day's change; the same window
# trajectory() reads.
GAP_DAYS = (0.5, 2.5)
# The share of a stretch the panel must have been heard for. The same bar
# upkeep sets for a whole day, as a fraction, so the two agree on what an
# outage is.
COVER_MIN = _up.WHOLE_DAY_SAMPLES / 96.0
# A dose and a reading this close together measure a gradient, not the pool.
SETTLE = dt.timedelta(hours=_up.SETTLE_H)
# Below this many usable stretches a model is not fitted at all.
MIN_DAYS = 14
# An hours term needs this many stretches in which the thing actually ran.
MIN_ACTIVE = 5
# Belsley's rule of thumb: a condition index above 30 on unit-length columns
# is a dependence strong enough to make two coefficients one.
COND_MAX = 30.0
# Below that, two unresolved estimates this anticorrelated are still one
# measurement. See step 3 of _fit().
CORR_MAX = 0.8
# Bootstrap resamples, and the seed — a render must not change its answer
# between two runs over the same record.
BOOT = 200
SEED = 20261009
# How many more days is an answer worth giving. Past this, "more of the same"
# is not what will settle it.
MORE_DAYS_CAP = 365

# The sun's day, for splitting a day's energy across a reading taken at
# breakfast. A half-sine from 06:00 to 20:00: a fixed shape, wrong in detail
# at either solstice, and the error only moves sun between two neighbouring
# stretches. Weather is daily; the pod is not on midnight.
SUN_FROM_H, SUN_TO_H = 6.0, 20.0


def _sun_cum(h):
    if h <= SUN_FROM_H:
        return 0.0
    if h >= SUN_TO_H:
        return 1.0
    return (1 - math.cos(math.pi * (h - SUN_FROM_H) / (SUN_TO_H - SUN_FROM_H))) / 2


def daylight_share(day, a, b):
    """The fraction of `day`'s sun energy that falls in [a, b)."""
    lo = dt.datetime.combine(day, dt.time())
    ha = max((a - lo).total_seconds() / 3600.0, 0.0)
    hb = min((b - lo).total_seconds() / 3600.0, 24.0)
    if hb <= ha:
        return 0.0
    return _sun_cum(hb) - _sun_cum(ha)


def sun_between(a, b, wx):
    """MJ/m² of sun in [a, b), or None if a day that matters has no figure."""
    total, d = 0.0, a.date()
    while d <= b.date():
        share = daylight_share(d, a, b)
        if share > 0.01:
            v = (wx.get(d.isoformat()) or {}).get("sun_mj_m2")
            if v is None:
                return None
            total += v * share
        d += dt.timedelta(days=1)
    return total


# ---------------------------------------------------------------- the terms
# Each coefficient, in the order that decides which name survives when two
# cannot be separated: the earlier one keeps the figure and the later one is
# reported as inside it. That order is THEORY.md's — the spillover is the
# baseline and the cell the increment; the sun is the dominant chlorine term.
#
#   key      THEORY.md's name, where it has one
#   sign     +1 / -1: the side of zero physics puts it on
#   driver   the per-stretch quantity whose variation identifies it
#   spread   how much that driver must vary before the term is asked about
#   hours    True for a thing that runs: counted by the stretches it ran in
#   vary     what would separate it, said as a variation in the record
#   ran      for a thing that runs, the days that count: "with X"
#   ratio    when this term is folded into another, how the combined figure
#            reads: the ratio is this column per unit of the partner's, which
#            for the cell inside the spillover is the dial it averaged
def _t(key, theory, name, unit, scale, fmt, sign, driver, spread, vary,
       ran="", ratio=""):
    return dict(key=key, theory=theory, name=name, unit=unit, scale=scale,
                fmt=fmt, sign=sign, driver=driver, spread=spread, vary=vary,
                hours=bool(ran), ran=ran, ratio=ratio)


FC_TERMS = [
    _t("prod", "k_prod", "Chlorine the salt cell makes",
       "ppm per pump-hour at full output", 1.0, ".2f", +1, "swg_h", 0.3,
       "pump hours or the cell's dial", ran="the cell running"),
    _t("uv", "k_uv", "Chlorine the sun burns off",
       "% of the chlorine per MJ/m² of sun", 100.0, ".2f", +1, "sun", 1.0,
       "sunny and overcast days"),
    _t("demand", "", "Standing demand, sun aside",
       "% of the chlorine per day", 100.0, ".1f", +1, "days", 0.0,
       "the sun, so that sun loss and the rest come apart"),
    _t("temp", "k_temp", "Extra chlorine demand per degree",
       "% of the chlorine per day, per °F warmer", 100.0, ".2f", +1, "T", 1.0,
       "water temperature on days of similar sun — a warm cloudy week, a "
       "cool clear one"),
    _t("cya", "k_cya", "What stabiliser buys",
       "% of the chlorine per MJ/m², per 10 ppm more CYA", 1000.0, ".2f", -1,
       "C", 3.0, "the stabiliser level — a second, different CYA result"),
]

PH_TERMS = [
    _t("spill", "k_spill", "pH rise from the spa spillover",
       "pH per pump-hour", 1.0, ".3f", +1, "pump_h", 0.3,
       "pump hours", ran="the pump running"),
    _t("cell", "k_cell", "pH rise from the salt cell",
       "pH per pump-hour at full output", 1.0, ".3f", +1, "swg_h", 0.3,
       "the cell's dial while pump hours stay about the same — a few weeks "
       "at a different setting", ran="the cell running",
       ratio="the cell at the {pct:.0f}% it averaged"),
    _t("sheer", "k_sheer", "pH rise from the sheer descent",
       "pH per hour it runs", 1.0, ".3f", +1, "sheer_h", 0.3,
       "the sheer descent's hours", ran="the sheer descent running"),
    _t("spa", "", "pH rise from a spa session",
       "pH per spa hour", 1.0, ".3f", +1, "spa_h", 0.3,
       "spa sessions", ran="a spa session"),
    _t("heat", "", "pH rise from a heater running",
       "pH per heater-hour", 1.0, ".3f", +1, "heat_h", 0.3,
       "heater hours apart from spa sessions", ran="a heater on"),
    _t("temp", "k_temp", "pH rise per degree",
       "pH per day, per °F warmer", 1.0, ".4f", +1, "T", 1.0,
       "water temperature while the hours stay about the same"),
]


# ------------------------------------------------------------- linear algebra
def _inverse(A):
    """Gauss-Jordan with partial pivoting. None when the matrix is singular."""
    n = len(A)
    M = [list(map(float, row)) + [1.0 if i == j else 0.0 for j in range(n)]
         for i, row in enumerate(A)]
    scale = max((abs(A[i][i]) for i in range(n)), default=0.0) or 1.0
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-12 * scale:
            return None
        M[c], M[p] = M[p], M[c]
        piv = M[c][c]
        M[c] = [v / piv for v in M[c]]
        for r in range(n):
            if r != c and M[r][c]:
                f = M[r][c]
                M[r] = [a - f * b for a, b in zip(M[r], M[c])]
    return [row[n:] for row in M]


def _eig(A, sweeps=60):
    """Eigenvalues and eigenvectors (as columns) of a small symmetric matrix.

    Cyclic Jacobi rotations. Exact enough for a condition index, and the
    eigenvector of the smallest eigenvalue is what names WHICH columns are
    the dependent ones.
    """
    n = len(A)
    a = [list(map(float, r)) for r in A]
    v = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for _ in range(sweeps):
        off = sum(a[i][j] ** 2 for i in range(n) for j in range(n) if i != j)
        if off < 1e-22:
            break
        for p in range(n):
            for q in range(p + 1, n):
                if abs(a[p][q]) < 1e-300:
                    continue
                th = (a[q][q] - a[p][p]) / (2 * a[p][q])
                t = (1.0 if th >= 0 else -1.0) / (abs(th) + math.sqrt(th * th + 1))
                c = 1 / math.sqrt(t * t + 1)
                s = t * c
                for k in range(n):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p], a[k][q] = c * akp - s * akq, s * akp + c * akq
                for k in range(n):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k], a[q][k] = c * apk - s * aqk, s * apk + c * aqk
                for k in range(n):
                    vkp, vkq = v[k][p], v[k][q]
                    v[k][p], v[k][q] = c * vkp - s * vkq, s * vkp + c * vkq
    return [a[i][i] for i in range(n)], v


def _ols(X, y):
    """(beta, covariance, residuals) or None when X'X is singular."""
    n, p = len(X), len(X[0]) if X else 0
    if n <= p or p == 0:
        return None
    XtX = [[sum(X[k][i] * X[k][j] for k in range(n)) for j in range(p)]
           for i in range(p)]
    inv = _inverse(XtX)
    if inv is None:
        return None
    Xty = [sum(X[k][i] * y[k] for k in range(n)) for i in range(p)]
    beta = [sum(inv[i][j] * Xty[j] for j in range(p)) for i in range(p)]
    res = [y[k] - sum(X[k][i] * beta[i] for i in range(p)) for k in range(n)]
    s2 = sum(r * r for r in res) / (n - p)
    cov = [[s2 * inv[i][j] for j in range(p)] for i in range(p)]
    return beta, cov, res


def t_crit(df, z=1.959964):
    """Student's t at 97.5%, by the Cornish-Fisher expansion (good from df 3)."""
    df = max(df, 3)
    return (z + (z ** 3 + z) / (4 * df)
            + (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * df ** 2)
            + (3 * z ** 7 + 19 * z ** 5 + 17 * z ** 3 - 15 * z) / (384 * df ** 3))


def _std(xs):
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


# ------------------------------------------------------------ the record
class _Record:
    """The record, indexed once: sample spans, weather, labs, doses."""

    def __init__(self, samples, labs, doses, wx, gallons):
        from . import render as R
        self.R = R
        self.spans = list(R.sample_spans(samples))
        self.times = [t for t, _, _ in self.spans]
        self.wx = wx or {}
        self.gallons = gallons
        self.doses = sorted(((R.when(d.get("ts")), d) for d in doses
                             if R.when(d.get("ts"))), key=lambda p: p[0])
        # Per lab source, per measure, (when, value) oldest first: only rows
        # that actually carry the measure, so a newer row without a CYA does
        # not hide an older one with it.
        self.labs = []
        for rows, name in labs:
            by = {}
            for r in rows:
                t = R.when(r.get("measured"))
                if not t:
                    continue
                for m in ("cya", "ta"):
                    v = R.num(r.get(m))
                    if v is not None:
                        by.setdefault(m, []).append((t, v, r))
            for m in by:
                by[m].sort(key=lambda p: p[0])
            self.labs.append((name, by))

    def as_of(self, measure, t):
        """The lab value in force at t: most recent wins, never averaged."""
        cands = []
        for name, by in self.labs:
            pts = [p for p in by.get(measure, []) if p[0] <= t]
            if pts:
                cands.append((pts[-1][2], name))
        got = self.R.best_lab(*cands, measures=[measure]).get(measure)
        return got

    def exposure(self, a, b):
        """What happened between a and b, from the panel and the weather."""
        R = self.R
        i = max(bisect.bisect_left(self.times, a - R.SEG_MAX), 0)
        out = {"covered": 0.0, "pump_h": 0.0, "swg_h": 0.0, "sheer_h": 0.0,
               "spa_h": 0.0, "heat_h": 0.0, "dial_unknown": 0.0}
        temps = []
        while i < len(self.spans) and self.spans[i][0] < b:
            t, span, s = self.spans[i]
            i += 1
            lo, hi = max(t, a), min(t + span, b)
            h = (hi - lo).total_seconds() / 3600.0
            if h <= 0:
                continue
            out["covered"] += h
            if s.get("pump") == "1":
                out["pump_h"] += h
                dial = R.num(s.get("swg_pct"))
                if dial is None:
                    out["dial_unknown"] += h
                else:
                    out["swg_h"] += dial / 100.0 * h
            if s.get("sheer") == "1":
                out["sheer_h"] += h
            if s.get("spa") == "1":
                out["spa_h"] += h
            if s.get("pool_heat") == "1" or s.get("spa_heat") == "1":
                out["heat_h"] += h
            # The water's temperature only while water moves past the probe.
            r = _cmd.drop_stale_flow_readings(dict(s))
            v = R.num(r.get("pool_temp"))
            if v is not None:
                temps.append(v)
        span_h = (b - a).total_seconds() / 3600.0
        out["cover"] = out["covered"] / span_h if span_h > 0 else 0.0
        out["T"] = sum(temps) / len(temps) if temps else None
        out["sun"] = sun_between(a, b, self.wx) if self.wx else None
        out["days"] = span_h / 24.0
        return out

    def dose_effect(self, d, measure, t):
        """The predicted change in `measure`, via chemicals.effects(); or why not."""
        R = self.R
        chem = (d.get("chemical") or "").strip()
        if chem not in CH.CHEMICALS:
            return None, "a dose of something the catalogue does not know"
        # Does it move this measure at all? Asked at a nominal volume, so a
        # calcium dose is irrelevant to pH whether or not the volume is set.
        probe = CH.CHEMICALS[chem]["effects"](1.0, CH.CHEMICALS[chem]["default_pct"],
                                              1.0, 90.0)
        if not probe.get(measure):
            return 0.0, None
        if not self.gallons:
            return None, "a dose, and no pool volume to say what it did"
        ta = (self.as_of("ta", t) or {}).get("value") or 90.0
        amt = R.num(d.get("amount"))
        pct = R.num(d.get("pct")) or CH.CHEMICALS[chem]["default_pct"]
        eff = (CH.effects(chem, amt, d.get("unit") or "", pct, self.gallons, ta)
               if amt is not None else None)
        if eff is None:
            return None, "a dose with an amount or unit that cannot be read"
        return eff.get(measure, 0.0), None


# How each reason a stretch is left out reads on the page. The keys are what
# the fit records; the counts are always shown, never just the survivors.
WHY = {
    "gap": "readings more than {hi:g} days or less than {lo:g} apart",
    "cover": "the panel was not heard from for the whole stretch",
    "dose": "inside a dose's settle window",
    "acid": "an acid dose in the stretch (measured separately, below)",
    "unpriced": "a dose whose effect cannot be worked out",
    "temp": "no water temperature with the pump running",
    "sun": "no weather recorded for those days",
    "cya": "before the first stabiliser (CYA) result",
}


def _pairs(series, measure, R):
    """Consecutive readings of one measure from ONE instrument."""
    out = []
    for name, rows in series:
        pts, seen = [], set()
        for r in rows:
            t, v = R.when(r.get("measured")), R.num(r.get(measure))
            k = (r.get("measured") or "")[:16]
            if t and v is not None and k not in seen:
                seen.add(k)
                pts.append((t, v))
        pts.sort(key=lambda p: p[0])
        for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
            out.append((name, t0, v0, t1, v1))
    return out


def _stretches(rec, series, measure):
    """Usable stretches for one measure, and the count of each reason not."""
    R = rec.R
    rows, skipped = [], {}

    def skip(why):
        skipped[why] = skipped.get(why, 0) + 1

    first_cya = min((p[0] for _, by in rec.labs for p in by.get("cya", [])),
                    default=None)
    for name, t0, v0, t1, v1 in _pairs(series, measure, R):
        days = (t1 - t0).total_seconds() / 86400.0
        if not (GAP_DAYS[0] <= days <= GAP_DAYS[1]):
            skip("gap")
            continue
        x = rec.exposure(t0, t1)
        if x["cover"] < COVER_MIN or x["dial_unknown"] > 0.25:
            skip("cover")
            continue
        poured, why = 0.0, None
        for td, d in rec.doses:
            if td >= t1:
                break
            if td < t0 - SETTLE:
                continue
            eff, bad = rec.dose_effect(d, measure, td)
            if bad:
                why = "unpriced"
                break
            if not eff:
                continue
            if td < t0 or t1 - td < SETTLE:
                why = "dose"
                break
            if measure == "ph":
                why = "acid" if (d.get("chemical") or "") == "acid" else "dose"
                break
            poured += eff
        if why:
            skip(why)
            continue
        if x["T"] is None:
            skip("temp")
            continue
        if measure == "free_cl":
            if rec.wx and x["sun"] is None:
                skip("sun")
                continue
            c = rec.as_of("cya", t0) if first_cya else None
            if first_cya and not c:
                skip("cya")
                continue
            x["C"] = c["value"] if c else None
            x["C_src"] = c
        rows.append(dict(x, source=name, t0=t0, t1=t1, v0=v0, v1=v1,
                         y=(v1 - v0) - poured, poured=poured))
    return rows, skipped


# ---------------------------------------------------------------- one model
def _design(rows, measure, terms):
    """The regressor columns for each term, and the centres they used."""
    Tbar = sum(r["T"] for r in rows) / len(rows)
    cs = [r["C"] for r in rows if r.get("C") is not None]
    Cbar = sum(cs) / len(cs) if cs else None
    cols = {}
    for t in terms:
        k = t["key"]
        col = []
        for r in rows:
            fbar = (r["v0"] + r["v1"]) / 2
            if measure == "free_cl":
                v = {"prod": lambda: r["swg_h"],
                     "uv": lambda: -fbar * r["sun"],
                     "demand": lambda: -fbar * r["days"],
                     "temp": lambda: -fbar * r["days"] * (r["T"] - Tbar),
                     "cya": lambda: -fbar * r["sun"] * (r["C"] - Cbar),
                     }[k]()
            else:
                v = {"spill": lambda: r["pump_h"], "cell": lambda: r["swg_h"],
                     "sheer": lambda: r["sheer_h"], "spa": lambda: r["spa_h"],
                     "heat": lambda: r["heat_h"],
                     "temp": lambda: r["days"] * (r["T"] - Tbar)}[k]()
            col.append(v)
        cols[k] = col
    return cols, Tbar, Cbar


def _fit(rows, measure, terms, unavailable):
    """Fit one model and grade every coefficient in it."""
    n = len(rows)
    order = [t["key"] for t in terms]
    spec = {t["key"]: t for t in terms}
    res = {"measure": measure, "n": n, "terms": {}, "order": order,
           "ok": False, "merged": {}}

    def grade(k, status, **kw):
        res["terms"][k] = dict(spec[k], status=status, **kw)

    for k, why in unavailable.items():
        grade(k, "not_yet", blocker="no_input", why=why, days=0)
    live = [k for k in order if k not in unavailable]
    if n < MIN_DAYS:
        for k in live:
            grade(k, "not_yet", blocker="too_few_days", days=n,
                  more_days=MIN_DAYS - n,
                  why=f"{n} usable day{'s' if n != 1 else ''} so far; the fit "
                      f"starts at {MIN_DAYS}")
        res["why"] = (f"{n} usable day{'s' if n != 1 else ''} so far, and a fit "
                      f"needs {MIN_DAYS}")
        return res

    cols, Tbar, Cbar = _design(rows, measure, [spec[k] for k in live])
    res["Tbar"], res["Cbar"] = Tbar, Cbar

    # 1. A term whose driver never moved, or a thing that hardly ran.
    active = []
    for k in live:
        t = spec[k]
        drv = [r.get(t["driver"]) for r in rows]
        if any(v is None for v in drv):
            grade(k, "not_yet", blocker="no_input", days=0,
                  why="the input it needs is not in the record")
            continue
        on = sum(1 for v in drv if v > 0.05) if t["hours"] else n
        if t["hours"] and on < MIN_ACTIVE:
            grade(k, "not_yet", blocker="too_few_days", days=on,
                  more_days=MIN_ACTIVE - on,
                  why=f"ran on {on} of {n} days; it needs {MIN_ACTIVE} before "
                      f"it can be priced")
            continue
        if t["spread"] and _std(drv) < t["spread"]:
            grade(k, "not_yet", blocker="no_variation", days=n,
                  why=_flat_words(t, drv))
            continue
        if not any(abs(v) > 1e-12 for v in cols[k]):
            grade(k, "not_yet", blocker="no_variation", days=n,
                  why=_flat_words(t, drv))
            continue
        active.append(k)

    # 2. Collinearity: merge the dependent pair the smallest eigenvalue names,
    #    the later term into the earlier, until the condition index is sane.
    merged = {}
    while len(active) >= 2:
        norms = {k: math.sqrt(sum(v * v for v in cols[k])) for k in active}
        Z = [[cols[k][i] / norms[k] for k in active] for i in range(n)]
        G = [[sum(Z[r][a] * Z[r][b] for r in range(n)) for b in range(len(active))]
             for a in range(len(active))]
        vals, vecs = _eig(G)
        lo = min(range(len(vals)), key=lambda i: vals[i])
        cond = math.sqrt(max(vals) / vals[lo]) if vals[lo] > 1e-15 else float("inf")
        res["cond"] = cond
        if cond <= COND_MAX:
            break
        comp = sorted(range(len(active)), key=lambda j: -abs(vecs[j][lo]))[:2]
        a, b = sorted((active[j] for j in comp), key=order.index)
        # b, the later name, is folded into a. The ratio is what makes the
        # combined figure usable: b ran at `ratio` units per unit of a.
        bb = sum(v * v for v in cols[a])
        ratio = sum(x * y for x, y in zip(cols[b], cols[a])) / bb if bb else 0.0
        merged[b] = {"into": a, "ratio": ratio, "cond": cond}
        active.remove(b)
    res["merged"] = merged
    if not active:
        res["why"] = "nothing in the record varies enough to fit"
        return res

    # 3. Ordinary least squares -- and a second, softer kind of dependence.
    #
    # A condition index under 30 does not mean two terms come apart. On the
    # demo pool, with the dial moved once from 45 to 60, the cell and the
    # spillover sat at 22: both intervals straddled zero, and the page said
    # each was "too small to see" -- while together they were the clearest
    # pH signal in the record. Two estimates that trade one for the other
    # (correlation below -CORR_MAX) and are each unresolved are one
    # measurement, and are merged exactly as the strict case is.
    y = [r["y"] for r in rows]
    while True:
        X = [[cols[k][i] for k in active] for i in range(n)]
        got = _ols(X, y)
        if got is None:
            res["why"] = "the record is degenerate for this model"
            return res
        beta, cov, resid = got
        df = n - len(active)
        tc = t_crit(df)
        se_ = [math.sqrt(max(cov[j][j], 0.0)) for j in range(len(active))]
        open_ = [j for j in range(len(active)) if abs(beta[j]) < tc * se_[j]]
        worst = None
        for i in open_:
            for j in open_:
                if i < j and se_[i] and se_[j]:
                    r = cov[i][j] / (se_[i] * se_[j])
                    if r < -CORR_MAX and (worst is None or r < worst[0]):
                        worst = (r, i, j)
        if not worst:
            break
        a, b = sorted((active[worst[1]], active[worst[2]]), key=order.index)
        bb = sum(v * v for v in cols[a])
        ratio = sum(x * y_ for x, y_ in zip(cols[b], cols[a])) / bb if bb else 0.0
        merged[b] = {"into": a, "ratio": ratio, "corr": worst[0]}
        active.remove(b)
    res["merged"] = merged
    rng = random.Random(SEED)
    boots = {k: [] for k in active}
    for _ in range(BOOT):
        idx = [rng.randrange(n) for _ in range(n)]
        g = _ols([X[i] for i in idx], [y[i] for i in idx])
        if g:
            for j, k in enumerate(active):
                boots[k].append(g[0][j])
    ybar = sum(y) / n
    tss = sum((v - ybar) ** 2 for v in y)
    rss = sum(r * r for r in resid)
    sd = math.sqrt(rss / df) if df > 0 else 0.0
    res.update(ok=True, r2=1 - rss / tss if tss else 0.0, resid_sd=sd, df=df,
               beta={k: beta[j] for j, k in enumerate(active)},
               active=active)
    # The days the record says least about how the model works: large
    # residuals, listed for a person, never dropped by the fit.
    res["odd"] = sorted(
        ({"t0": rows[i]["t0"], "t1": rows[i]["t1"], "resid": resid[i],
          "source": rows[i]["source"]}
         for i in range(n) if sd and abs(resid[i]) > 3 * sd),
        key=lambda o: -abs(o["resid"]))[:3]

    for j, k in enumerate(active):
        t = spec[k]
        est, se = beta[j], math.sqrt(max(cov[j][j], 0.0))
        lo, hi = est - tc * se, est + tc * se
        bs = sorted(boots[k])
        if len(bs) >= 20:
            # The WIDER of the two. OLS assumes every day is equally noisy and
            # they are not; the bootstrap assumes nothing about that and is
            # ragged on a small record. Taking the union costs a little
            # sharpness and never claims more than either one supports.
            lo = min(lo, bs[int(0.025 * len(bs))])
            hi = max(hi, bs[int(0.975 * len(bs)) - 1])
        days = (sum(1 for v in (r.get(t["driver"]) for r in rows) if v and v > 0.05)
                if t["hours"] else n)
        inside = [m for m, v in merged.items() if v["into"] == k]
        clear = lo > 0 or hi < 0
        right = (t["sign"] == 0 or (t["sign"] > 0 and lo > 0)
                 or (t["sign"] < 0 and hi < 0))
        kw = dict(est=est, se=se, lo=lo, hi=hi, days=days, inside=inside)
        if inside:
            # What the combined figure IS: this term plus each folded-in one
            # at the ratio the record ran them at. Without the ratio the
            # figure cannot be used, because it is only true at that ratio.
            kw["together"] = "both together" + "".join(
                ", " + (spec[m]["ratio"].format(pct=merged[m]["ratio"] * 100)
                        if spec[m]["ratio"] else
                        f"{_lc(spec[m]['name'])} at {merged[m]['ratio']:.3g} per unit")
                for m in inside)
        if clear and right:
            grade(k, "combined" if inside else "measured", **kw)
        elif clear:
            grade(k, "not_yet", blocker="sign", **kw,
                  why="the record points the wrong way for this — something "
                      "unmodelled is moving with it")
        else:
            more = None
            if est and (t["sign"] == 0 or est * t["sign"] > 0):
                need = days * (tc * se / abs(est)) ** 2 - days
                more = max(int(math.ceil(need)), 1)
                if more > MORE_DAYS_CAP:
                    more = None
            right_way = bool(est) and (t["sign"] == 0 or est * t["sign"] > 0)
            grade(k, "not_yet", blocker="wide", more_days=more, **kw,
                  why=("too small to see against the day-to-day noise yet"
                       if more else
                       "too small to see in a record this size" if right_way
                       else "no sign of it yet"))
    for b, m in merged.items():
        other = _lc(spec[m["into"]]["name"])
        grade(b, "inside", blocker="collinear", days=n, partner=m["into"],
              ratio=m["ratio"],
              why=(f"moved in step with {other} on every day of this record"
                   if "cond" in m else
                   f"this record cannot tell it from {other}: the fit can "
                   f"trade one for the other and fit as well"))
    return res


def _lc(name):
    """A name in the middle of a sentence. "pH" is not "Ph", nor "ph"."""
    return name if name[:2] == "pH" else name[:1].lower() + name[1:]


def _cap(s):
    """The start of a sentence. str.capitalize() lowercases the rest: "PH"."""
    return s[:1].upper() + s[1:] if s[:2] != "pH" else s


def _flat_words(t, drv):
    lo, hi = min(drv), max(drv)
    what = {"T": f"the water stayed between {lo:.0f} and {hi:.0f}°F",
            "C": f"every stabiliser result in the window read {lo:.0f}"
                 + (f"–{hi:.0f}" if hi - lo >= 0.5 else "") + " ppm",
            "sun": f"the sun ran {lo:.0f}–{hi:.0f} MJ/m² every day"}
    return what.get(t["driver"], "it barely varied across the record")


# ---------------------------------------------------------------- acid
def _acid(rec, pod, doses, ph_model, acid_pct, now):
    """k_acid: chemicals.dose_response()'s pairs, with the measured drift out.

    The pairing is the dose table's own, so this and that table cannot pair a
    dose differently. What is added is the drift: the per-hour pH terms the
    model above measured, applied to the hours between the two readings, so a
    rise that would have happened anyway is not credited to the acid.
    """
    R = rec.R
    out = {"key": "acid", "theory": "k_acid",
           "name": "What a fluid ounce of acid does",
           "unit": f"fl oz of {acid_pct:g}% acid per 0.1 pH", "fmt": ".0f",
           "doses": [], "status": "not_yet"}
    if not rec.gallons:
        out.update(blocker="no_input", why="the pool's volume is not set")
        return out
    acids = [d for d in doses if (d.get("chemical") or "") == "acid"]
    obs = CH.dose_response(acids, pod, R.when, R.num)
    beta = (ph_model or {}).get("beta") or {}
    Tbar = (ph_model or {}).get("Tbar")
    ratios = []
    for o in obs:
        if o["ph_before"] is None or o["ph_after"] is None:
            continue
        if o["hours_to_reading"] < _up.SETTLE_H:
            continue
        d = o["dose"]
        eff, bad = rec.dose_effect(d, "ph", o["at"])
        if bad or not eff:
            continue
        t0, t1 = R.when(o["before"]["measured"]), R.when(o["after"]["measured"])
        drift = 0.0
        if beta:
            x = rec.exposure(t0, t1)
            feats = {"spill": x["pump_h"], "cell": x["swg_h"],
                     "sheer": x["sheer_h"], "spa": x["spa_h"],
                     "heat": x["heat_h"],
                     "temp": (x["days"] * (x["T"] - Tbar)
                              if x["T"] is not None and Tbar is not None else 0.0)}
            drift = sum(b * feats[k] for k, b in beta.items())
        got = o["ph_after"] - o["ph_before"] - drift
        ratios.append(got / eff)
        out["doses"].append({"at": o["at"], "ratio": got / eff})
    n = len(ratios)
    out["days"] = n
    out["drift_removed"] = bool(beta)
    per_floz = CH.effects("acid", 1, "floz", acid_pct, rec.gallons,
                          (rec.as_of("ta", now) or {}).get("value") or 90.0)["ph"]
    out["ta_measured"] = rec.as_of("ta", now) is not None
    out["predicted"] = 0.1 / -per_floz
    if n < 3:
        out.update(blocker="too_few_days", more_days=3 - n,
                   why=f"{n} acid dose{'s' if n != 1 else ''} with a settled "
                       f"reading after; a figure needs 3")
        if n:
            out["first"] = 0.1 / -(per_floz * sum(ratios) / n)
        return out
    m = sum(ratios) / n
    se = _std(ratios) / math.sqrt(n)
    lo, hi = m - t_crit(n - 1) * se, m + t_crit(n - 1) * se
    out["ratio"], out["ratio_lo"], out["ratio_hi"] = m, lo, hi
    if lo <= 0:
        out.update(blocker="wide", why="the doses so far scatter too widely "
                   "to say what one does")
        return out
    # Inverted into the unit a person doses in: fl oz per 0.1 pH. A larger
    # ratio is a stronger response and FEWER ounces, so the ends swap.
    out.update(status="measured", est=0.1 / -(per_floz * m),
               lo=0.1 / -(per_floz * hi), hi=0.1 / -(per_floz * lo))
    return out


# ---------------------------------------------------------------- the lot
def _fc_model(rec, series, wx):
    """The free-chlorine fit over these series: (model, stretches, skipped).

    One function for Home's table and Equipment health's windows, so the two
    cannot set up the model differently -- which inputs are missing, and what
    a missing one does to the terms that need it.
    """
    rows, skipped = _stretches(rec, series, "free_cl")
    unavailable = {}
    if not wx:
        unavailable["uv"] = ("no weather is recorded — add [weather] to "
                             "config.toml and the sun is measured from then on")
        unavailable["cya"] = "it acts through the sun, which is not recorded"
        for r in rows:
            r["sun"] = 0.0
    elif not any(by.get("cya") for _, by in rec.labs):
        unavailable["cya"] = "no stabiliser (CYA) result is on file"
    if "cya" in unavailable:
        for r in rows:
            r["C"] = 0.0
    return _fit(rows, "free_cl", FC_TERMS, unavailable), rows, skipped


def analyse(samples, readings, manual, doses, labs, wx, gallons, acid_pct,
            now=None):
    """Every coefficient the record supports, graded. See the module docstring.

    `labs` is [(rows, source name), ...] for the lab results, which is where
    CYA and TA come from; `wx` is render.weather_by_day(). Pure apart from the
    loaders it reads through render.
    """
    now = now or dt.datetime.now()
    rec = _Record(samples, labs, doses, wx, gallons)
    series = [("WaterGuru pod", readings), ("By hand", manual)]
    out = {"now": now}

    out["fc"], rows, skipped = _fc_model(rec, series, wx)
    out["fc"]["skipped"] = skipped
    out["fc"]["rows"] = rows

    prow, pskip = _stretches(rec, series, "ph")
    out["ph"] = _fit(prow, "ph", PH_TERMS, {})
    out["ph"]["skipped"] = pskip
    out["ph"]["rows"] = prow

    out["acid"] = _acid(rec, readings, doses, out["ph"] if out["ph"]["ok"] else None,
                        acid_pct, now)
    out["budget"] = _budget(out["fc"])
    out["cya"] = _cya_note(rec, rows, wx, now)
    return out


def cell_rate(readings, samples, doses, labs, wx, gallons):
    """k_prod over just these readings and samples: Equipment health's window.

    THE ONE ESTIMATOR of chlorine made per full-output cell-hour. equipment.py
    judges the cell window by window and used to do it with trajectory()'s P
    divided by cell-hours, beside this table's k_prod fitted another way. Two
    numbers for one coefficient on one page is the defect seams.py exists
    for, so the cell's windows are this model, run on less record. Only a
    MEASURED figure is a yield; anything else is a window that did not fit.
    """
    rec = _Record(samples, labs, doses, wx, gallons)
    model, _, _ = _fc_model(rec, [("WaterGuru pod", readings)], wx)
    t = model["terms"].get("prod") or {}
    if t.get("status") != "measured":
        return None
    return {"per_cell_hour": t["est"], "lo": t["lo"], "hi": t["hi"]}


def _budget(fc):
    """Where the chlorine goes on a typical recent day, from measured terms only."""
    if not fc.get("ok"):
        return None
    rows = sorted(fc["rows"], key=lambda r: r["t1"])[-14:]
    if not rows:
        return None
    T = fc["terms"]
    use = {k for k, t in T.items() if t["status"] in ("measured", "combined")}
    # A budget with the losses and no income is not a budget: "burnt off by
    # the sun -0.94 ppm" alone reads as a pool losing a ppm a day, when the
    # cell's share is merely unmeasured. No production figure, no line.
    if "prod" not in use or len(use & {"uv", "demand"}) == 0:
        return None
    n = len(rows)
    fc_ = sum((r["v0"] + r["v1"]) / 2 for r in rows) / n
    per = lambda key: sum(r[key] for r in rows) / sum(r["days"] for r in rows)
    out = {"fc": fc_, "days": n, "parts": []}
    if "prod" in use:
        out["parts"].append(("made by the cell", T["prod"]["est"] * per("swg_h")))
    if "uv" in use:
        out["parts"].append(("burnt off by the sun", -T["uv"]["est"] * fc_ * per("sun")))
    if "demand" in use:
        out["parts"].append(("consumed, sun aside", -T["demand"]["est"] * fc_))
    return out if out["parts"] else None


def _cya_note(rec, rows, wx, now):
    """The stabiliser the fit held constant: its value, age, and rain since."""
    c = rec.as_of("cya", now)
    if not c:
        return None
    t = rec.R.when(c["measured"])
    rain = None
    if wx and t:
        rs = [v.get("precip_in") for d, v in wx.items() if d >= t.date().isoformat()]
        rain = sum(r for r in rs if r) if rs else None
    return {"value": c["value"], "source": c["source"], "measured": c["measured"],
            "age": rec.R.ago(c["measured"]), "rain_in": rain}


# ---------------------------------------------------------------- words
STATUS_WORD = {"measured": "measured", "combined": "combined",
               "inside": "combined", "not_yet": "not yet"}
STATUS_PILL = {"measured": "p-ok", "combined": "p-over", "inside": "p-over",
               "not_yet": "p-unknown"}


def _num(v, t):
    return format(v * t["scale"], t["fmt"])


def figure(t):
    """The estimate with its interval, in the term's own unit."""
    if t.get("est") is None:
        return ""
    lo, hi = sorted((t["lo"] * t["scale"], t["hi"] * t["scale"]))
    f = t["fmt"]
    return (f"{format(t['est'] * t['scale'], f)} "
            f"({format(lo, f)} to {format(hi, f)})")


def next_step(t, spec_by_key):
    """What would settle this coefficient, as a sentence. Planner-shaped."""
    b = t.get("blocker")
    if t["status"] == "measured":
        return "more days narrow it; nothing is needed"
    if t["status"] == "combined":
        names = " and ".join(_lc(spec_by_key[k]["name"]) for k in t["inside"])
        return f"separating it from {names} — see that row"
    if b == "collinear":
        return (f"variation in {t['vary']}. More days like these will not "
                f"separate the two, however many there are")
    with_ = f" with {t['ran']}" if t.get("ran") else ""
    if b == "too_few_days":
        return (f"about {t['more_days']} more "
                + ("day" if t["more_days"] == 1 else "days") + with_)
    if b == "no_variation":
        return f"variation in {t['vary']}"
    if b == "wide" and t.get("more_days"):
        return (f"about {t['more_days']} more days like these{with_}, or more "
                f"variation in {t['vary']}")
    if b == "sign":
        return "a look at what else changed on those days"
    if b == "no_input":
        return t.get("why", "")
    return f"variation in {t['vary']}"


def _term_rows(model, terms):
    spec = {t["key"]: t for t in terms}
    out = []
    for k in model["order"]:
        t = model["terms"].get(k)
        if not t:
            continue
        status = STATUS_WORD[t["status"]]
        if t["status"] in ("combined", "inside"):
            other = (t["inside"] if t["status"] == "combined" else [t["partner"]])
            status += " with " + ", ".join(_lc(spec[o]["name"]) for o in other)
        out.append((t, status, next_step(t, spec)))
    return out


def _model_words(m, what):
    if not m.get("ok"):
        return f"{what}: {m.get('why', 'not fitted')}."
    s = (f"{what}: {m['n']} day-to-day changes used; the model accounts for "
         f"{max(m['r2'], 0) * 100:.0f}% of how they varied, leaving "
         f"±{m['resid_sd']:.2g} a day it cannot explain")
    return s + "."


def _skipped_words(sk):
    if not sk:
        return ""
    lo, hi = GAP_DAYS
    bits = [f"{n} {WHY[k].format(lo=lo, hi=hi)}" for k, n in
            sorted(sk.items(), key=lambda p: -p[1])]
    return "Left out: " + "; ".join(bits) + "."


def block(res):
    """The "What moves this pool" card. Private: every figure is this pool's."""
    esc = html.escape
    if not res:
        return ""
    fc, ph, acid = res["fc"], res["ph"], res["acid"]
    body = []

    def group(title, model, terms, extra=None):
        body.append(f'<tr><th colspan="5" scope="colgroup">{esc(title)}</th></tr>')
        for t, status, nxt in _term_rows(model, terms):
            pill = STATUS_PILL[t["status"]]
            fig = figure(t) if t["status"] != "inside" else ""
            if t["status"] == "not_yet" and fig:
                fig = f'<span class="rel">{esc(fig)} — not clear of zero</span>'
            elif fig:
                fig = esc(fig)
            else:
                fig = "&mdash;"
            said = [w for w in (t.get("together"),
                                t.get("why") if t["status"] in ("not_yet", "inside")
                                else None) if w]
            body.append(
                f'<tr><td><b>{esc(t["name"])}</b>'
                f'<span class="rel">{esc(t["unit"])}'
                f'{(" · " + esc(t["theory"])) if t["theory"] else ""}</span></td>'
                f'<td class="n" data-k="Estimate">{fig}</td>'
                # The word in the pill, the partner beside it: a pill is
                # text-transform:capitalize, and "With PH Rise From..." is
                # what a sentence inside one reads as.
                f'<td><span class="pill {pill}">{esc(STATUS_WORD[t["status"]])}</span>'
                + (f'<span class="rel">{esc(status.split(" ", 1)[1])}</span>'
                   if " with " in status else "") + '</td>'
                f'<td class="n" data-k="Days">{t.get("days", 0)}</td>'
                f'<td>{"".join(esc(_cap(w)) + ". " for w in said)}'
                f'<b>Settled by:</b> {esc(nxt)}</td></tr>')
        if extra:
            body.append(extra)

    group("Free chlorine", fc, FC_TERMS)
    group("pH", ph, PH_TERMS, _acid_row(acid))

    notes = [_model_words(fc, "Free chlorine"), _model_words(ph, "pH")]
    if fc.get("ok"):
        notes.append(
            f"Free chlorine is fitted against the day's mean reading, at the "
            f"water's mean {fc['Tbar']:.0f}°F"
            + (f" and stabiliser {fc['Cbar']:.0f} ppm" if fc.get("Cbar") else "")
            + ": demand is the demand at that temperature, and sun loss the "
              "loss at that stabiliser.")
    b = res.get("budget")
    if b:
        parts = "; ".join(f"{w} {v:+.2f} ppm" for w, v in b["parts"])
        notes.append(f"<b>Where the chlorine goes</b>, on an average day of the "
                     f"last {b['days']} at {b['fc']:.1f} ppm: {parts} — measured "
                     f"terms only.")
    c = res.get("cya")
    if c:
        rain = (f", and {c['rain_in']:.2f} in of rain has fallen since"
                if c.get("rain_in") else "")
        notes.append(
            f"Stabiliser is held at each lab result until the next one, never "
            f"interpolated: the latest is {c['value']:.0f} ppm from "
            f"{esc(c['source'])}, {esc(c['age'])}{rain}. Rain dilutes it only "
            f"if the pool overflowed or was pumped down, which nothing records, "
            f"so it is not modelled.")
    for model, what in ((fc, "free chlorine"), (ph, "pH")):
        sk = _skipped_words(model.get("skipped"))
        if sk:
            notes.append(f"{_cap(what)} — {esc(sk)}")
        for o in model.get("odd") or []:
            notes.append(
                f"The {what} change from {o['t0']:%-d %b} to {o['t1']:%-d %b} "
                f"({esc(o['source'])}) is {o['resid']:+.2f} away from what "
                f"everything else predicts. It is still counted; if that "
                f"reading was fouled, a correction removes it from the fit.")
    notes.append(
        "Every interval is the wider of the textbook one and a bootstrap over "
        "days, at 95%. A figure that is not clear of zero, or is on the wrong "
        "side of it, is not printed as a measurement.")
    return (f'<div class="card" data-fit="household"><div class="scroll"><table class="fit">'
            f'<thead><tr><th>What</th><th>Estimate (95% range)</th>'
            f'<th>Status</th><th>Days</th><th>What would settle it</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>'
            + "".join(f'<p class="sub" style="margin:12px 0 0">{n}</p>' for n in notes)
            + "</div>")


def _acid_row(a):
    esc = html.escape
    if a["status"] == "measured":
        fig = (f"{a['est']:.0f} ({a['lo']:.0f} to {a['hi']:.0f})")
        nxt = "more doses narrow it"
        why = ""
    else:
        fig = (f'<span class="rel">first: {a["first"]:.0f}</span>'
               if a.get("first") else "&mdash;")
        nxt = (f"about {a['more_days']} more acid dose"
               f"{'s' if a.get('more_days', 0) != 1 else ''}, each read at least "
               f"{_up.SETTLE_H} hours later"
               if a.get("blocker") == "too_few_days" else "more doses")
        why = esc(a.get("why", "")) + ". "
    pred = (f" The textbook figure is {a['predicted']:.0f}"
            + ("" if a.get("ta_measured") else ", against an assumed alkalinity")
            + "." if a.get("predicted") else "")
    drift = (" Drift the pH model measured is taken out." if a.get("drift_removed")
             else "")
    return (f'<tr><td><b>{esc(a["name"])}</b><span class="rel">'
            f'{esc(a["unit"])} · k_acid</span></td>'
            f'<td class="n" data-k="Estimate">{fig if a["status"] != "measured" else esc(fig)}</td>'
            f'<td><span class="pill {STATUS_PILL[a["status"]]}">'
            f'{STATUS_WORD[a["status"]]}</span></td>'
            f'<td class="n" data-k="Doses">{a.get("days", 0)}</td>'
            f'<td>{why}<b>Settled by:</b> {esc(nxt)}.{esc(pred)}{esc(drift)}</td></tr>')


def context_lines(res):
    """The fixed lines Ask AI is sent. Every term, in a fixed order."""
    if not res:
        return ["  not available"]
    out = []
    for model, terms, what in ((res["fc"], FC_TERMS, "free chlorine"),
                               (res["ph"], PH_TERMS, "pH")):
        out.append(f"  {_model_words(model, _cap(what))}")
        for t, status, nxt in _term_rows(model, terms):
            fig = figure(t)
            if t["status"] in ("measured", "combined"):
                out.append(f"  {t['name']}: {fig} {t['unit']} — {status}, "
                           f"{t.get('days', 0)} days"
                           + (f" ({t['together']})" if t.get("together") else ""))
            else:
                out.append(f"  {t['name']}: NOT MEASURED — {t.get('why', '')}"
                           + (f" (inside {status.split(' with ', 1)[1]})"
                              if t["status"] == "inside" else ""))
    a = res["acid"]
    if a["status"] == "measured":
        out.append(f"  {a['name']}: {a['est']:.0f} ({a['lo']:.0f} to "
                   f"{a['hi']:.0f}) {a['unit']} — measured, {a['days']} doses")
    else:
        out.append(f"  {a['name']}: NOT MEASURED — {a.get('why', '')}")
    b = res.get("budget")
    if b:
        out.append(f"  A typical recent day at {b['fc']:.1f} ppm: "
                   + "; ".join(f"{w} {v:+.2f} ppm" for w, v in b["parts"]))
    return out


# ---------------------------------------------------------------- simulation
def simulate(samples, wx, truth, read_at, gallons, ta=75.0, labs=(),
             fc0=3.0, ph0=7.5, noise=(0.15, 0.02), seed=1, acid_rule=None,
             chlorine_rule=None, acid_pct=31.45):
    """A pool that obeys KNOWN coefficients, so the fit can be held to them.

    Walks the same sample spans the fit reads, integrating FC exactly over
    each one (production uniform across the span, loss proportional to FC)
    and pH as the per-hour sum, then reads both at `read_at` with noise. Dose
    rules see the noisy reading — what an owner would see — and the doses
    they return are applied through chemicals.effects(), scaled for acid by
    truth["acid_ratio"], the factor by which this pool departs from the
    textbook.

    `truth` uses THEORY.md's units, per hour and per MJ/m²; temperature terms
    are about truth["T_ref"] and the CYA term about truth["C_ref"].
    Returns (readings, doses) in the CSV shapes the loaders read.
    """
    from . import render as R
    rng = random.Random(seed)
    g = lambda k: truth.get(k, 0.0)
    def series(m):
        return sorted(((R.when(r["measured"]), R.num(r.get(m)))
                       for r in labs if R.num(r.get(m)) is not None),
                      key=lambda p: p[0])
    labs_sorted, ta_sorted = series("cya"), series("ta")
    reads = sorted(read_at)
    fc, ph, T = fc0, ph0, g("T_ref")
    pending, out, doses = [], [], []
    ri = 0

    def cya_at(t):
        v = None
        for tt, c in labs_sorted:
            if tt <= t:
                v = c
        return v if v is not None else g("C_ref")

    def apply_dose(d):
        # At the alkalinity the labs say was in force, as the fit reads it:
        # a simulated pool buffered at one figure while the fit predicts at
        # another would plant an acid ratio nobody chose.
        nonlocal fc, ph
        at = R.when(d["ts"])
        here = [v for tt, v in ta_sorted if tt <= at]
        e = CH.effects(d["chemical"], float(d["amount"]), d["unit"],
                       float(d["pct"]), gallons, here[-1] if here else ta) or {}
        fc += e.get("free_cl", 0.0)
        ph += e.get("ph", 0.0) * (truth.get("acid_ratio", 1.0)
                                  if d["chemical"] == "acid" else 1.0)
        doses.append(d)

    for t, span, s in R.sample_spans(samples):
        end = t + span
        # Readings and doses that fall before this span ends.
        while ri < len(reads) and reads[ri] < end:
            rt = reads[ri]
            ri += 1
            f = max(0.0, fc + rng.gauss(0, noise[0]))
            p = ph + rng.gauss(0, noise[1])
            out.append({"measured": rt.strftime("%Y-%m-%dT%H:%M:%S"),
                        "free_cl": f"{f:.1f}", "ph": f"{p:.2f}"})
            for rule in (acid_rule, chlorine_rule):
                d = rule(rt, f, p) if rule else None
                if d:
                    pending.append(d)
        for d in [d for d in pending if R.when(d["ts"]) < end]:
            pending.remove(d)
            apply_dose(d)
        h = span.total_seconds() / 3600.0
        dd = h / 24.0
        pump = s.get("pump") == "1"
        r = _cmd.drop_stale_flow_readings(dict(s))
        tv = R.num(r.get("pool_temp"))
        if tv is not None:
            T = tv
        dial = (R.num(s.get("swg_pct")) or 0.0) / 100.0 if pump else 0.0
        sun = sun_between(t, end, wx) or 0.0
        C = cya_at(t)
        k = ((g("uv") + g("cya") * (C - g("C_ref"))) * sun
             + g("demand") * dd + g("temp") * (T - g("T_ref")) * dd)
        P = g("prod") * dial * h
        fc = (fc * math.exp(-k) + (P * (1 - math.exp(-k)) / k if k > 1e-12 else P))
        ph += (g("cell") * dial * h + (g("spill") * h if pump else 0.0)
               + (g("sheer") * h if s.get("sheer") == "1" else 0.0)
               + (g("spa") * h if s.get("spa") == "1" else 0.0)
               + (g("heat") * h if (s.get("pool_heat") == "1"
                                     or s.get("spa_heat") == "1") else 0.0)
               + g("ph_temp") * (T - g("T_ref")) * dd)
    return out, doses
