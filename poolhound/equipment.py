"""Equipment health: what the panel's own samples say about the filter, the
salt cell and the heater, as trends over weeks rather than readings of a moment.

WHAT THIS IS FOR

Everything here was already on disk. Every fifteen minutes the agent writes the
pump's speed and draw, the cell's output and the salt it reads, the water and
air temperatures and the state of every circuit -- and the product used them to
draw a day strip and price a kilowatt-hour. Three slow failures are written in
the same columns, and none of them announces itself: a filter loading up, a
cell scaling over, a heater losing output. Each costs money for weeks before
anybody notices, and each is visible as a DRIFT in a figure that should hold
still. So each signal below compares this pool with itself, never with a
chart, and says which of three things its answer is:

  MEASURED      the record pins it down: enough like-for-like samples at both
                ends, and a difference larger than the scatter between them.
  BOUNDED       the record says it is no more than this -- usually "no change
                bigger than the noise", which is a real answer and not a shrug.
  INSUFFICIENT  not enough data yet, said with what would fix it. Never a
                confident number on a thin record.

THE FILTER, AND WHICH WAY A DIRTY ONE MOVES THE WATTS

A variable-speed pump held at a fixed RPM is a centrifugal pump on a fixed
curve. A filter loading with dirt raises the head the pump works against, and
the operating point slides back along the curve to LESS flow at MORE head. For
the radial impellers pool pumps use, shaft power rises with flow across the
whole working range -- so less flow is less power, and a dirtying filter shows
up as the watts FALLING at a given speed. (A pump in constant-FLOW mode does
the opposite: it raises its speed to hold the flow, and the watts climb. This
pool's panel commands speeds, which is why the comparison is binned by RPM.)

That direction is an assumption from the physics, not something this record
has demonstrated, and it is stated on the page. A RISE at fixed speed is
reported too, as the thing it more likely is: a basket or filter cleaned and
not marked here, a valve opened, a suction leak drawing air -- or a motor
bearing going, which is why a rise is worth a look rather than ignoring.

Like with like, or the drift is the plumbing:
  * one speed. Draw rises steeply with speed (roughly its cube), so a mean
    across speeds measures the schedule. The commonest steady speed is used.
  * steady state. The first minutes after the pump starts are priming and air,
    and a speed change ramps. A sample counts only once the pump, its speed and
    the valves below have held for SETTLE.
  * the same path through the pipes. The spa valves send the water somewhere
    else entirely; the sheer descent opens another return and drops the head;
    the solar valve (Extra_Aux) pushes it up a roof and raises it. A sample
    with any of those on, or within SETTLE of one changing, is excluded.

THE CELL, AND WHAT IS NOT THE CELL'S FAULT

The yield is chlorine made per hour of cell running at full output -- the
same quantity as THEORY.md's k_prod, and fit.py owns how it is estimated:
each window here is fit.cell_rate(), which is fit.analyse()'s free-chlorine
model run over that window alone. This used to divide render.trajectory()'s
P by the window's mean cell-hours, while Home's "What moves this pool" fitted
k_prod with the sun, the demand and the poured chlorine separated out -- two
estimators of one coefficient on one page, free to disagree, and the cruder
one (P absorbs whatever the sun did that fortnight) was the one judging the
cell. Now a window's yield IS the fit's k_prod for those days, and a window
in which the fit cannot clear it of zero is not fitted, and is counted.

A cell's yield falls as it scales and ages. It also falls when the SALT is low
(the cell throttles) and when the WATER is cold (most cells derate under about
60 degF and stop near 50), and a window where either was true is set aside and
SAID to be, rather than read as the cell failing. A window with a chlorine dose
in it is set aside too: the fit would credit the jug to the cell. And the fit
cannot tell a loss that does not scale with chlorine -- a busy afternoon, a
storm -- from a cell making less, which is one reason this is so often only a
bound. The salt figure goes through commands.drop_stale_flow_readings(),
because SWG/PPM latches its last value with the pump off and history written
before that rule existed still carries the repeats.

THE HEATER, AND WHY POOL AND SPA ARE NEVER ONE NUMBER

Degrees of water temperature per hour of heater firing. The spa is a few
hundred gallons and the pool many thousands, so the same heater moves one tens
of degrees an hour and the other a fraction of one; pooled, the figure would
measure how often each was heated. They are kept apart. AqualinkD reports a
heater as `on` only while it is firing -- `enabled` is armed and idle, and the
agent writes that as 0 -- so a run of 1s is a run of heat. A pool run with the
solar valve open is excluded (the roof is a second heater), the first sample
of every run is dropped (it reads the water standing in the pipe), and a run
stops counting once the water is within half a degree of the setpoint, where
the heater cycles.

Grouped by OUTDOOR temperature rather than by calendar season, because heat
lost to the air is what makes a winter run slower, and because a month means
opposite things in two hemispheres. The outdoor figure is the weather
record's daily mean air temperature where that day has one, and the panel's
own air sensor where it does not; each run says which (outdoor_from). The
sun still warms the pool while the heater fires, so a daytime summer rate
includes it.

AND THE CHEAP ONE

Anything switched on that stayed on far longer than it usually does, and a
pump schedule that moved without poolhound sending a command -- the shape a
schedule edited at the panel leaves. Both are read off the same samples; the
attribution is switchlog.events(), which is the product's one answer to "who
switched that".

THE RECORD OF WHAT WAS DONE

Cleaning a filter or a cell resets what "clean" looks like, so the product has
to be told. Nothing recorded it -- a backwash is not a dose, and putting it in
chemicals.csv would have put a "backwash" into the dose catalogue, the cost
ledger and the dose-response fit. maintenance.csv is its own append-only
record, written through locking.append_row like every shared CSV, with the
column list here and nowhere else.

Pure, like upkeep.py: the caller hands in rows and the loaders' parsing
functions, so nothing here reads a file except record() and load().
"""
import datetime as dt
import html
import math
import os

from . import commands

# --------------------------------------------------------------- the record
FILE = "maintenance.csv"
COLS = ["ts", "task", "note", "by", "id"]
# What can be marked done, and what each resets. Two, because two of the three
# signals have a baseline a cleaning restarts; the heater has no such thing.
TASKS = {
    "filter": "Filter cleaned or backwashed",
    "cell":   "Salt cell cleaned",
}

# ------------------------------------------------------------- the judgement
# A sample interval longer than this is a gap in the record, not continuity.
# The same twenty minutes render.SEG_MAX caps a day's runtime at; read from
# there in assess() so the two cannot drift, and defaulted here for the pure
# functions a test calls directly.
GAP = dt.timedelta(minutes=20)
# How long the pump, its speed and the valves must have held before a sample
# describes the plumbing rather than a transition.
SETTLE = dt.timedelta(minutes=10)
# The circuits that change the path the water takes. Each excluded for the
# reason in the module docstring; blank counts as off ONLY for the solar valve,
# whose column rows written before it existed leave empty. A blank spa or sheer
# is "the panel said nothing", which is not off.
HYDRAULIC = ("spa", "sheer", "solar_valve")
BLANK_IS_OFF = ("solar_valve",)
# Speeds within this of each other are one speed. A panel reports the speed it
# commanded, so this only absorbs a pump that reports its measured one.
RPM_BIN = 25

# How far back the filter comparison looks, and the two ends it compares.
WINDOW_DAYS = 120
BASE_DAYS = 7
CUR_DAYS = 7
# The fewest steady samples either end may rest on, and the fewest days it may
# span: one afternoon is one afternoon's water temperature and one valve state.
MIN_SAMPLES = 12
MIN_DAYS = 2
# The drift that is worth watching, and the drift that is worth acting on.
# JUDGEMENTS, NOT MEASUREMENTS. The usual rule is to clean a filter when its
# pressure gauge reads 8-10 psi over clean; on a typical variable-speed curve
# that is very roughly a tenth of the draw at a fixed speed. No pool here has
# yet been watched through a whole cycle to calibrate it, and the page says so.
FILTER_WATCH = 0.05
FILTER_CLEAN = 0.10

# The cell: windows of this many days, stepped back by STEP.
CELL_WINDOW = 21
CELL_STEP = 7
COLD_WATER = 60.0          # deg F, under which most cells derate
CELL_DROP = 0.25           # a fall worth saying out loud
# A day counts toward cell-hours only if the panel was heard from for most of
# it; a day with a hole is a pump that may have run unseen.
WHOLE_DAY = 80

# The heater: runs shorter than this say more about the sensor than the heat.
MIN_RUN = {"pool": dt.timedelta(minutes=45), "spa": dt.timedelta(minutes=20)}
# Outdoor temperature bands, deg F. Lower bound inclusive.
BANDS = [(None, 50, "below 50°"), (50, 65, "50–65°"),
         (65, 80, "65–80°"), (80, None, "80° and above")]

# Long-on: how many completed runs make a "usual", and how far past it is long.
USUAL_RUNS = 5
LONG_FACTOR = 2.0
LONG_FLOOR = dt.timedelta(hours=1)
LONG_LOOKBACK = dt.timedelta(days=60)
LONG_RECENT = dt.timedelta(days=7)
# A schedule move: the start or stop shifting by this much against the median
# of the week before, and holding for HOLD days after.
SHIFT = dt.timedelta(minutes=30)
HOLD = 2

GRADE_WORD = {"measured": "Measured", "bounded": "Bounded",
              "insufficient": "Not enough data yet"}


# ============================================================== the record
def path(cfg=None):
    from . import config
    return os.path.join(config.data_dir(cfg), FILE)


def record(task, cfg=None, by="", note="", at=None):
    """Append one thing that was done. Never rewrites, never removes.

    `at` is when it was DONE, which may be yesterday: a filter backwashed on
    Saturday and marked on Monday must reset the baseline from Saturday, or the
    two days in between are compared against themselves.

    Returns the row written. Raises ValueError on a task this module does not
    know or a time in the future -- the server turns either into a 400.
    """
    from . import config, locking
    from . import chemicals as CH
    from .render import CLOCK_SKEW
    if task not in TASKS:
        raise ValueError(f"unknown task {task!r} — one of {', '.join(TASKS)}")
    now = dt.datetime.now()
    at = at or now
    # A cleaning cannot have happened in the future, and the tolerance for a
    # clock that disagrees is the one the dose log uses, not a second opinion.
    if at > now + CLOCK_SKEW:
        raise ValueError("that time is in the future")
    row = {"ts": at.astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
           "task": task, "note": str(note or "")[:200],
           "by": str(by or "")[:120], "id": CH.new_id()}
    locking.append_row(config.data_dir(cfg), "maintenance", path(cfg), COLS, row)
    return row


def done(rows, task, when):
    """Every time `task` was recorded, as sorted naive-local datetimes."""
    out = [when(r.get("ts")) for r in rows or () if (r.get("task") or "") == task]
    return sorted(t for t in out if t)


# ============================================================ small helpers
def _on(v):
    return str(v if v is not None else "").strip() in ("1", "1.0")


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def _spread(xs, floor=1.0):
    """A robust standard deviation: 1.4826 x the median absolute deviation.

    Robust because one sample taken while a skimmer basket was choked, or in
    the second after a valve moved, should not widen the band everything else
    is judged against. Floored at the instrument's own resolution -- the panel
    reports whole watts -- so a run of identical readings is not infinitely
    certain.
    """
    m = _median(xs)
    if m is None:
        return None
    return max(1.4826 * _median([abs(x - m) for x in xs]), floor)


def _sorted(samples, when):
    out = [(when(s.get("ts")), s) for s in samples or ()]
    out = [(t, s) for t, s in out if t]
    out.sort(key=lambda p: p[0])
    return out


def _hours(td):
    return td.total_seconds() / 3600.0


def _day(t):
    return t.date().isoformat()


# ================================================================ 1. filter
def steady(samples, when, num, gap=GAP, settle=SETTLE):
    """[(t, rpm, watts)] for samples that describe the plumbing at rest.

    A sample qualifies when the pump is running at a reported speed and draw,
    none of HYDRAULIC is on, and the pump, its speed and every one of those
    valves have held their state for `settle` -- with a gap in the record
    counting as a change, because nobody knows what happened inside it.
    """
    out = []
    prev_t, prev_state, since = None, None, None
    for t, s in _sorted(samples, when):
        rpm = num(s.get("pump_rpm"))
        rbin = None if rpm is None else int(round(rpm / RPM_BIN)) * RPM_BIN
        valves = tuple(str(s.get(c, "") or "").strip() for c in HYDRAULIC)
        state = (_on(s.get("pump")), rbin, valves)
        if prev_t is None or t - prev_t > gap or state != prev_state:
            since = t
        prev_t, prev_state = t, state
        if not state[0] or not rbin or rbin <= 0:
            continue
        w = num(s.get("pump_watts"))
        if w is None or w <= 0:
            continue
        if any(v != "0" and not (v == "" and c in BLANK_IS_OFF)
               for c, v in zip(HYDRAULIC, valves)):
            continue
        if t - since < settle:
            continue
        out.append((t, rbin, w))
    return out


def filter_loading(samples, cleans, now, when, num, gap=GAP):
    """Has the pump's draw at its usual speed drifted since the filter was clean?

    Returns a dict the panel and the actions read; see the module docstring for
    the physics and the thresholds. `cleans` are the datetimes the filter was
    marked cleaned.
    """
    res = {"key": "filter", "grade": "insufficient", "status": "unknown",
           "n_steady": 0, "rpm": None, "baseline": None, "current": None,
           "drift": None, "since": None, "cleaned": None, "series": [],
           "cleans": [], "better": [], "why": ""}
    st = [p for p in steady(samples, when, num, gap=gap)
          if p[0] >= now - dt.timedelta(days=WINDOW_DAYS) and p[0] <= now]
    res["n_steady"] = len(st)
    if not st:
        anyw = any((num(s.get("pump_watts")) or 0) > 0 for s in samples or ())
        res["why"] = ("the pump has never reported its wattage, so there is "
                      "nothing to compare" if not anyw else
                      "no sample in the last "
                      f"{WINDOW_DAYS} days has the pump at a settled speed with "
                      "the spa, the sheer descent and the solar valve all off")
        res["better"] = (["a pump that reports Pump_Watts and Pump_RPM to "
                          "AqualinkD"] if not anyw else
                         ["an hour or more a day with the pump running and the "
                          "spa, sheer descent and solar all off"])
        return res

    counts = {}
    for _, r, _ in st:
        counts[r] = counts.get(r, 0) + 1
    ref = max(counts, key=lambda r: (counts[r], r))
    at = [(t, w) for t, r, w in st if r == ref]
    res["rpm"], res["n_ref"] = ref, len(at)
    res["n_other"] = len(st) - len(at)

    last_clean = max((c for c in cleans or () if c <= now), default=None)
    res["cleaned"] = last_clean
    res["cleans"] = [c for c in cleans or ()
                     if c >= now - dt.timedelta(days=WINDOW_DAYS) and c <= now]
    epoch = [(t, w) for t, w in at if last_clean is None or t > last_clean]
    start = last_clean or (epoch[0][0] if epoch else None)
    res["since"] = start

    # A daily figure for the chart: every day in the window, across epochs, so
    # the sawtooth a cleaning makes is visible rather than cut off at the last
    # one.
    byday = {}
    for t, w in at:
        byday.setdefault(t.date(), []).append(w)
    res["series"] = [(d, _median(ws), len(ws)) for d, ws in sorted(byday.items())]

    if not epoch:
        res["why"] = ("nothing at that speed since the filter was marked "
                      "cleaned")
        res["better"] = ["a few days of running since the cleaning"]
        return res

    base_end = start + dt.timedelta(days=BASE_DAYS)
    base = [w for t, w in epoch if t < base_end]
    base_days = {_day(t) for t, _ in epoch if t < base_end}
    last_t = epoch[-1][0]
    cur_start = last_t - dt.timedelta(days=CUR_DAYS)
    cur = [w for t, w in epoch if t > cur_start]
    cur_days = {_day(t) for t, _ in epoch if t > cur_start}
    res["n_base"], res["n_cur"] = len(base), len(cur)
    res["base_window"] = (start, min(base_end, last_t))
    res["cur_window"] = (max(cur_start, start), last_t)

    if len(base) < MIN_SAMPLES or len(base_days) < MIN_DAYS:
        res["why"] = (f"the first {BASE_DAYS} days after "
                      + ("the cleaning" if last_clean else "the record begins")
                      + f" hold {len(base)} steady sample"
                      f"{'' if len(base) == 1 else 's'} at {ref:.0f} RPM, "
                      f"over {len(base_days)} day{'' if len(base_days) == 1 else 's'}"
                      f" — a baseline needs {MIN_SAMPLES} over {MIN_DAYS}")
        res["better"] = [f"more running at {ref:.0f} RPM with the spa, sheer "
                         f"and solar off"]
        return res
    res["baseline"] = _median(base)
    if cur_start < base_end:
        res["why"] = (f"{(last_t - start).days} days since "
                      + ("the cleaning" if last_clean else "the record began")
                      + f"; the comparison needs {BASE_DAYS + CUR_DAYS} so the "
                      "two ends do not overlap")
        res["better"] = [f"{BASE_DAYS + CUR_DAYS - (last_t - start).days} more "
                         "days of running"]
        res["grade"] = "insufficient"
        return res
    if len(cur) < MIN_SAMPLES or len(cur_days) < MIN_DAYS:
        res["why"] = (f"the last {CUR_DAYS} days hold only {len(cur)} steady "
                      f"samples at {ref:.0f} RPM")
        res["better"] = [f"more running at {ref:.0f} RPM with the spa, sheer "
                         f"and solar off"]
        return res

    mb, mc = res["baseline"], _median(cur)
    res["current"] = mc
    # The standard error of a median is about 1.25 sigma / sqrt(n).
    se = math.sqrt((1.2533 * _spread(base)) ** 2 / len(base)
                   + (1.2533 * _spread(cur)) ** 2 / len(cur))
    drift = (mc - mb) / mb
    res["drift"] = drift
    res["resolvable"] = 3 * se / mb
    if abs(mc - mb) <= 3 * se:
        res["grade"], res["status"] = "bounded", "steady"
        res["bound"] = max(abs(drift), 3 * se / mb)
    else:
        res["grade"] = "measured"
        res["status"] = ("clean" if drift <= -FILTER_CLEAN else
                         "loading" if drift <= -FILTER_WATCH else
                         "rising" if drift >= FILTER_WATCH else "steady")
    if not last_clean:
        res["better"].append("mark the next cleaning here, so the baseline is "
                             "a filter known to be clean rather than whatever "
                             "it was when the record began")
    if res["status"] == "steady" and res["grade"] == "bounded":
        res["better"].append("time: a filter loads over weeks, and the "
                             "comparison sharpens as the two ends move apart")
    return res


# ================================================================== 2. cell
def _per_day(samples, when, num, gap=GAP):
    """{date: {cell_h, pump_h, n, salt, temp}} with the latch rule applied."""
    days = {}
    S = _sorted(samples, when)
    for i, (t, s) in enumerate(S):
        nxt = S[i + 1][0] if i + 1 < len(S) else None
        span = min(nxt - t, gap) if nxt else gap
        d = days.setdefault(_day(t), {"cell_h": 0.0, "pump_h": 0.0, "n": 0,
                                      "salt": [], "temp": []})
        d["n"] += 1
        # A COPY: the rule mutates, and these rows are the loader's. And THE
        # RULE decides which salt and temperature readings are live -- not a
        # pump test written again here, which would be a second copy of the
        # latch rule free to drift from the one both collectors apply.
        row = commands.drop_stale_flow_readings(dict(s))
        sp = num(row.get("salt_ppm"))
        if sp is not None and sp > 0:
            d["salt"].append(sp)
        tp = num(row.get("pool_temp"))
        if tp is not None and not _on(row.get("spa")):
            d["temp"].append(tp)
        if _on(row.get("pump")):
            h = _hours(span)
            d["pump_h"] += h
            pct = num(row.get("swg_pct"))
            if pct is not None and pct >= 0:
                d["cell_h"] += h * pct / 100.0
    for d in days.values():
        d["salt"] = _median(d["salt"]) if d["salt"] else None
        d["temp"] = sum(d["temp"]) / len(d["temp"]) if d["temp"] else None
    return days


def cell_yield(samples, readings, chems, cleans, now, when, num, fit,
               salt_low=None, gap=GAP):
    """Chlorine made per full-output cell-hour, window by window.

    `fit(readings, samples)` returns {"per_cell_hour": ppm} for the window --
    in the product, fit.cell_rate(), the same estimator as the Home table's
    "Chlorine the salt cell makes" -- or None when the window does not
    measure it, and that is counted, not hidden.
    """
    res = {"key": "cell", "grade": "insufficient", "status": "unknown",
           "windows": [], "change": None, "better": [], "why": "",
           "salt_low": salt_low}
    rd = sorted((when(r.get("measured")), r) for r in readings or ()
                if when(r.get("measured")) and num(r.get("free_cl")) is not None)
    if len(rd) < 8:
        res["why"] = (f"{len(rd)} free-chlorine reading"
                      f"{'' if len(rd) == 1 else 's'} — each window needs a "
                      f"few weeks of daily readings to fit a production rate")
        res["better"] = ["daily free-chlorine readings — a WaterGuru pod, or "
                         "a test kit entered on Settings"]
        return res
    days = _per_day(samples, when, num, gap=gap)
    partial = max(days, default="")
    last_clean = max((c for c in cleans or () if c <= now), default=None)
    res["cleaned"] = last_clean
    doses = [when(c.get("ts")) for c in chems or ()
             if (c.get("chemical") or "") in ("chlorine", "shock")]
    doses = [d for d in doses if d]

    end = rd[-1][0]
    first = max(rd[0][0], last_clean) if last_clean else rd[0][0]
    # A cleaning restarts the record the cell is judged on. Said as that,
    # rather than falling through to "nothing could be fitted", which blames
    # the readings for a wait the owner started on purpose.
    if last_clean and end - last_clean < dt.timedelta(days=CELL_WINDOW):
        have = max((end - last_clean).days, 0)
        res["why"] = (f"the cell was marked cleaned {last_clean:%-d %b}, and a "
                      f"window needs {CELL_WINDOW} days of readings after that")
        res["better"] = [f"{CELL_WINDOW - have} more days of readings"]
        return res
    unfit = 0
    while end - dt.timedelta(days=CELL_WINDOW) >= first - dt.timedelta(days=1):
        start = end - dt.timedelta(days=CELL_WINDOW)
        w_read = [r for t, r in rd if start < t <= end]
        w_days = [d for k, d in days.items()
                  if start.date().isoformat() < k <= end.date().isoformat()
                  and k != partial and d["n"] >= WHOLE_DAY]
        w_smp = [s for s in samples or ()
                 if when(s.get("ts")) and start < when(s.get("ts")) <= end]
        end_next = end - dt.timedelta(days=CELL_STEP)
        tr = fit(w_read, w_smp) if len(w_days) >= CELL_WINDOW // 2 else None
        if not tr or not tr.get("per_cell_hour") or tr["per_cell_hour"] <= 0:
            unfit += 1
            end = end_next
            continue
        cell_h = sum(d["cell_h"] for d in w_days) / len(w_days)
        if cell_h <= 0.05:
            unfit += 1
            end = end_next
            continue
        salts = [d["salt"] for d in w_days if d["salt"]]
        temps = [d["temp"] for d in w_days if d["temp"] is not None]
        salt = _median(salts) if salts else None
        temp = sum(temps) / len(temps) if temps else None
        flags = []
        if salt is not None and salt_low and salt < salt_low:
            flags.append("low salt")
        if temp is not None and temp < COLD_WATER:
            flags.append("cold water")
        if any(start < d <= end for d in doses):
            flags.append("chlorine added")
        res["windows"].append({
            "start": start, "end": end, "n": len(w_read), "days": len(w_days),
            # The yield is the fit's; production is only that, at this
            # window's cell-hours, for the table that shows both.
            "production": tr["per_cell_hour"] * cell_h, "cell_h": cell_h,
            "yield": tr["per_cell_hour"], "salt": salt, "temp": temp,
            "flags": flags})
        end = end_next
    res["windows"].reverse()
    res["unfit"] = unfit
    clean = [w for w in res["windows"] if not w["flags"]]
    res["n_clean"] = len(clean)
    if not res["windows"]:
        res["why"] = ("no window of readings could be fitted to a production "
                      "rate — the free-chlorine record does not yet settle "
                      "towards anything the model can describe")
        res["better"] = ["a few quiet weeks: no chlorine added by hand and no "
                         "change to the cell's output while they run"]
        return res
    if not clean:
        res["grade"] = "insufficient"
        res["why"] = ("every window that could be fitted had low salt, cold "
                      "water or a chlorine dose in it, so none of them speaks "
                      "for the cell alone")
        res["better"] = ["a window with salt in range, water above "
                         f"{COLD_WATER:.0f}° and no chlorine added"]
        return res
    res["latest"] = clean[-1]["yield"]
    # The first and the last that do not share a day of data.
    a = clean[0]
    b = clean[-1]
    if len(clean) < 2 or b["start"] < a["end"]:
        res["grade"], res["status"] = "bounded", "level"
        res["why"] = ("one window is a level, not a trend: a change needs two "
                      f"windows {CELL_WINDOW} days apart")
        res["better"] = [f"another {CELL_WINDOW} days of readings"]
        return res
    ys = [w["yield"] for w in clean]
    mean = sum(ys) / len(ys)
    sd = math.sqrt(sum((y - mean) ** 2 for y in ys) / (len(ys) - 1)) if len(ys) > 1 else 0
    change = (b["yield"] - a["yield"]) / a["yield"]
    res["change"], res["scatter"] = change, sd / mean if mean else None
    if len(clean) >= 4 and abs(b["yield"] - a["yield"]) > 2 * sd:
        res["grade"] = "measured"
        res["status"] = "declining" if change <= -CELL_DROP else (
                        "rising" if change >= CELL_DROP else "steady")
    else:
        res["grade"], res["status"] = "bounded", "steady"
        res["why"] = (f"the windows scatter by about ±{(sd / mean) * 100:.0f}% "
                      f"among themselves, which is larger than any trend "
                      f"between the first and the last")
        res["better"] = ["more windows: each one is three weeks, and the "
                         "scatter between them is the fit's own noise"]
    return res


# ================================================================ 3. heater
BODIES = {
    # body: (heat column, temperature column, setpoint column, spa must be)
    "pool": ("pool_heat", "pool_temp", "pool_set", False),
    "spa":  ("spa_heat", "spa_temp", "spa_set", True),
}


def _band(v):
    if v is None:
        return None
    for lo, hi, label in BANDS:
        if (lo is None or v >= lo) and (hi is None or v < hi):
            return label
    return None


# Where an outdoor figure came from, in the words the page uses.
OUTDOOR_SOURCE = {
    "weather": "the day's mean air temperature from the weather record",
    "panel":   "the panel's own air sensor during the run",
    "given":   "an outdoor figure supplied by the caller",
}


def outdoor_from(weather, num):
    """outdoor(t, sample) -> (deg F, source): the weather record first.

    `weather` is render.weather_by_day()'s {date: {...}}, read through the
    loader that owns it. Its daily MEAN air temperature is preferred: it is a
    measurement of the day the heater fought, from an instrument that is not
    sitting in the equipment pad beside a gas burner. Where there is no
    weather row for the day -- weather not configured, the newest day not yet
    ended, a gap -- the panel's air sensor stands in, and every run says which
    one it used. The two are not the same quantity (a day's mean against the
    air during the run), which is why the page says so rather than blending
    them silently.
    """
    def f(t, s):
        day = (weather or {}).get(t.date().isoformat()) or {}
        v = day.get("air_mean_f")
        if v is not None:
            return v, "weather"
        a = num(s.get("air_temp"))
        return (a, "panel") if a is not None else (None, None)
    return f


def heater_runs(samples, body, when, num, outdoor=None, gap=GAP):
    """[{start, end, hours, rise, rate, outdoor, outdoor_src}] for one body.

    `outdoor(t, sample)` returns the outside temperature for a sample, either
    as a number or as (number, source); the default reads the panel's air
    sensor, and assess() hands in outdoor_from() with the weather record. It
    is a parameter rather than a column name so another source plugs in here.
    """
    heat_c, temp_c, set_c, spa_mode = BODIES[body]
    outdoor = outdoor or outdoor_from(None, num)

    def out_at(t, s):
        o = outdoor(t, s)
        if isinstance(o, tuple):
            return o
        return (o, "given" if o is not None else None)

    runs, cur, prev_t = [], [], None

    def close():
        if len(cur) < 3:
            return
        pts = cur[1:]              # the first reads the water in the pipe
        a, b = pts[0], pts[-1]
        span = b[0] - a[0]
        if span < MIN_RUN[body]:
            return
        airs = [x[3][0] for x in pts if x[3][0] is not None]
        srcs = {x[3][1] for x in pts if x[3][1]}
        runs.append({"start": a[0], "end": b[0], "hours": _hours(span),
                     "rise": b[1] - a[1], "rate": (b[1] - a[1]) / _hours(span),
                     "outdoor": sum(airs) / len(airs) if airs else None,
                     "outdoor_src": (srcs.pop() if len(srcs) == 1 else
                                     "mixed" if srcs else None),
                     "n": len(pts)})

    for t, s in _sorted(samples, when):
        temp = num(s.get(temp_c))
        sp = num(s.get(set_c))
        ok = (_on(s.get("pump")) and _on(s.get(heat_c))
              and _on(s.get("spa")) == spa_mode
              and str(s.get("spa", "")).strip() in ("0", "1")
              and temp is not None
              and not (body == "pool" and _on(s.get("solar_valve")))
              and not (sp is not None and temp >= sp - 0.5))
        if not ok or (prev_t is not None and t - prev_t > gap):
            close()
            cur = []
        if ok:
            cur.append((t, temp, sp, out_at(t, s)))
        prev_t = t
    close()
    return runs


def heater_rates(samples, body, when, num, outdoor=None, gap=GAP):
    """Rates for one body, grouped by outdoor band. See the module docstring."""
    runs = heater_runs(samples, body, when, num, outdoor=outdoor, gap=gap)
    res = {"key": f"heat_{body}", "body": body, "runs": runs, "bands": [],
           "grade": "insufficient", "better": [], "why": ""}
    if not runs:
        res["why"] = (f"no {body} heater run of "
                      f"{int(MIN_RUN[body].total_seconds() // 60)} minutes or more "
                      f"with the water temperature reported throughout")
        return res
    groups = {}
    for r in runs:
        groups.setdefault(_band(r["outdoor"]) or "outdoor unknown", []).append(r)
    order = [b[2] for b in BANDS] + ["outdoor unknown"]
    for label in order:
        rs = groups.get(label)
        if not rs:
            continue
        rates = [r["rate"] for r in rs]
        g = {"band": label, "runs": len(rs), "hours": sum(r["hours"] for r in rs),
             "rate": _median(rates), "lo": min(rates), "hi": max(rates),
             "first": min(r["start"] for r in rs),
             "last": max(r["start"] for r in rs),
             "grade": "measured" if len(rs) >= 3 else "bounded",
             "sources": sorted({r["outdoor_src"] or "none" for r in rs}),
             "trend": None}
        # A change within one band: the earlier half of its runs against the
        # later half, when each half has three.
        if len(rs) >= 6:
            rs_s = sorted(rs, key=lambda r: r["start"])
            h = len(rs_s) // 2
            e, l = _median([r["rate"] for r in rs_s[:h]]), _median([r["rate"] for r in rs_s[h:]])
            if e:
                g["trend"] = (l - e) / e
        res["bands"].append(g)
    res["grade"] = ("measured" if any(g["grade"] == "measured" for g in res["bands"])
                    else "bounded")
    if res["grade"] == "bounded":
        res["better"] = ["three or more runs in the same outdoor band"]
    return res


# ======================================================== 4. long-on, schedule
def _runs_of(S, col, gap):
    """[(start, end, closed)] runs of `col` == 1 over sorted samples.

    `closed` is False for a run that ends at a gap in the record or at the last
    sample: its length is a floor, not a length.
    """
    out, start, last, prev_t = [], None, None, None
    for t, s in S:
        if prev_t is not None and t - prev_t > gap and start is not None:
            out.append((start, last, False))
            start = None
        v = str(s.get(col, "")).strip()
        if v in ("1", "1.0"):
            if start is None:
                start = t
            last = t
        elif v in ("0", "0.0"):
            if start is not None:
                out.append((start, t, True))
                start = None
        prev_t = t
    if start is not None:
        out.append((start, last, False))
    return out


def long_runs(samples, now, when, cfg=None, gap=GAP):
    """Things that came on and stayed on far longer than they usually do."""
    S = _sorted(samples, when)
    S = [p for p in S if p[0] >= now - LONG_LOOKBACK]
    if not S:
        return []
    last_t = S[-1][0]
    out = []
    for col, dev in sorted(commands.COLUMN_DEVICE.items()):
        if col == "freeze":
            continue        # armed for months by design; not a run
        runs = _runs_of(S, col, gap)
        done_ = [b - a for a, b, closed in runs if closed]
        if len(done_) < USUAL_RUNS:
            continue
        usual = _median(done_)
        p90 = sorted(done_)[int(0.9 * (len(done_) - 1))]
        limit = max(LONG_FACTOR * p90, LONG_FLOOR)
        for a, b, closed in runs:
            open_now = (not closed) and b == last_t
            length = b - a
            if length < limit or b < last_t - LONG_RECENT:
                continue
            if not closed and not open_now:
                continue    # ended at a gap: a floor, not a finding
            out.append({"device": dev, "label": commands.label_for(dev, cfg),
                        "start": a, "end": b, "hours": _hours(length),
                        "usual_h": _hours(usual), "open": open_now,
                        "runs": len(done_)})
    out.sort(key=lambda r: r["start"], reverse=True)
    return out


def schedule_moves(samples, audit_rows, now, when, cfg=None, gap=GAP):
    """Days the pump's start or stop moved and stayed moved, with no command.

    Complete days only: the newest sampled day has not finished, and its stop
    time is "not yet" rather than early. A day the panel was not heard from
    for most of is skipped too -- a missing morning is not a late start.
    """
    from . import switchlog
    S = _sorted(samples, when)
    if not S:
        return []
    per = {}
    for t, s in S:
        per.setdefault(_day(t), []).append((t, s))
    partial = max(per)
    starts, stops, dates = {}, {}, {}
    for d, pts in per.items():
        if d == partial or len(pts) < WHOLE_DAY:
            continue
        runs = _runs_of(pts, "pump", gap)
        if not runs:
            continue
        a = runs[0][0]
        b = runs[-1][1]
        starts[d] = a - a.replace(hour=0, minute=0, second=0, microsecond=0)
        dates[d] = a.date()
        stops[d] = b - b.replace(hour=0, minute=0, second=0, microsecond=0)
    commanded = {e["at"][:10] for e in switchlog.events(samples, audit_rows, cfg=cfg)
                 if e["device"] == "Filter_Pump" and e["how"] == "poolhound"}
    out = []
    days = sorted(starts)
    for which, table in (("start", starts), ("stop", stops)):
        for i, d in enumerate(days):
            before = [table[x] for x in days[max(0, i - 7):i]]
            after = [table[x] for x in days[i:i + HOLD + 1]]
            if len(before) < 4 or len(after) < HOLD + 1:
                continue
            # MEDIANS EITHER SIDE, not one day against a week. A pump block
            # that ends whenever its hours are up wanders by twenty minutes a
            # day on its own, so a single day proves nothing; a step is the
            # days after sitting together somewhere the days before did not.
            was, now_ = _median(before), _median(after)
            if abs(now_ - was) < SHIFT:
                continue
            if any(abs(x - now_) >= abs(x - was) for x in after):
                continue
            # Reported on the day it MOVED, and only then: the day before must
            # still sit with the old schedule, or every day after a move would
            # report it again against a median the move has not yet reached.
            prev = table[days[i - 1]]
            if abs(prev - was) >= abs(prev - now_):
                continue
            if d in commanded:
                continue
            out.append({"day": d, "date": dates[d], "which": which,
                        "was": was, "now": now_})
    since = (now - dt.timedelta(days=30)).date().isoformat()
    return sorted((m for m in out if m["day"] >= since), key=lambda m: m["day"])


# ================================================================ assembly
def assess(samples, readings, chems, maint, audit_rows, now, when, num,
           fit=None, salt_low=None, cfg=None, outdoor=None, weather=None):
    """Every signal, in one dict the panel, the actions and watch.py read.

    `weather` is render.weather_by_day(); the heater bands prefer it over the
    panel's air sensor, day by day (see outdoor_from).
    """
    outdoor = outdoor or outdoor_from(weather, num)
    try:
        from .render import SEG_MAX as gap
    except Exception:
        gap = GAP
    out = {"now": now,
           "filter": filter_loading(samples, done(maint, "filter", when), now,
                                    when, num, gap=gap),
           "heat_pool": heater_rates(samples, "pool", when, num, outdoor, gap),
           "heat_spa": heater_rates(samples, "spa", when, num, outdoor, gap),
           "long": long_runs(samples, now, when, cfg=cfg, gap=gap),
           "moves": schedule_moves(samples, audit_rows, now, when, cfg=cfg, gap=gap),
           "maint": sorted(((when(r.get("ts")), r) for r in maint or ()
                            if when(r.get("ts"))), key=lambda p: p[0],
                           reverse=True)}
    out["cell"] = (cell_yield(samples, readings, chems, done(maint, "cell", when),
                              now, when, num, fit, salt_low=salt_low, gap=gap)
                   if fit else {"key": "cell", "grade": "insufficient",
                                "status": "unknown", "windows": [],
                                "better": [], "why": "no fit supplied"})
    return out


def actions(est):
    """Items for Home's "What to do next", in render.actions()'s shape."""
    if not est:
        return []
    out = []
    f = est["filter"]
    if f["grade"] == "measured" and f["status"] == "clean":
        out.append(dict(pri="do", title="Clean the filter",
            detail=(f"At {f['rpm']:.0f} RPM the pump now draws "
                    f"<b>{f['current']:.0f} W</b> against <b>{f['baseline']:.0f} W</b> "
                    f"just after "
                    + ("the last cleaning" if f["cleaned"] else "the record began")
                    + f" — {abs(f['drift']) * 100:.0f}% less. At a fixed speed a "
                    f"pump moves less water, and draws less power, as the filter "
                    f"loads; less water through the cell is less chlorine made."),
            dose="", src=f"the pump's own readings, {f['n_base']} + {f['n_cur']} "
                         f"steady samples",
            note="Mark it done under Equipment health below, so the next "
                 "comparison starts from a clean filter."))
    elif f["grade"] == "measured" and f["status"] == "rising":
        out.append(dict(pri="watch", title="The pump is drawing more at the same speed",
            detail=(f"{f['current']:.0f} W at {f['rpm']:.0f} RPM against "
                    f"{f['baseline']:.0f} W — {f['drift'] * 100:+.0f}%. A dirty "
                    f"filter does the opposite, so this is more likely a cleaning "
                    f"nobody marked, a valve that moved, or air in the suction."),
            dose="", src=f"the pump's own readings, {f['n_base']} + {f['n_cur']} "
                         f"steady samples",
            note="If the filter was cleaned, mark it under Equipment health."))
    c = est["cell"]
    if c["grade"] == "measured" and c["status"] == "declining":
        out.append(dict(pri="watch", title="The salt cell is making less chlorine per hour",
            detail=(f"{abs(c['change']) * 100:.0f}% less per cell-hour than "
                    f"{c['windows'][0]['end']:%-d %b}, in windows with salt in "
                    f"range and warm water. Scale on the plates is the usual cause."),
            dose="", src="the pod's free chlorine and the panel's cell output",
            note="Inspect the cell; mark it under Equipment health if you clean it."))
    # A run longer than usual is anomaly.py's to put on Home now (its
    # long_usual rule reads est["long"]), so that one owner decides whether an
    # absolute limit has already said the same thing about the same run.
    return out


# ================================================================ the panel
def _fmt_td(td):
    m = int(round(td.total_seconds() / 60))
    return f"{m // 60:02d}:{m % 60:02d}"


def _grade(g):
    return GRADE_WORD.get(g, g)


def _pill(status):
    """The reading as a WORD on a tinted ground -- never the tint alone."""
    word, cls = {
        # Sentence case, and the section turns off the pill's capitalize:
        # "Not Known Yet" reads as a label, "Not known yet" as an answer.
        "clean":     ("Clean due", "p-high"),
        "loading":   ("Loading", "p-low"),
        "rising":    ("Rising", "p-low"),
        "declining": ("Declining", "p-low"),
        "steady":    ("Steady", "p-ok"),
        "level":     ("Level only", "p-over"),
        "unknown":   ("Not known yet", "p-unknown"),
    }.get(status, (status, "p-unknown"))
    return f'<span class="pill {cls}">{html.escape(word)}</span>'


def _span(a, b):
    if not a or not b:
        return ""
    return f"{a:%-d %b} – {b:%-d %b}"


def filter_chart(f, name_svg):
    """Daily median draw at the reference speed. One measure, one axis.

    The baseline is drawn as the reference line and the band either side of it
    is FILTER_WATCH, so "inside the band" reads as "not moved enough to
    watch". Each cleaning is a dashed vertical rule labelled in words, never by
    colour alone.
    """
    pts = [(dt.datetime.combine(d, dt.time()), w, n) for d, w, n in f["series"]]
    if len(pts) < 2:
        return ""
    W, H, L, R, T, B = 720, 168, 48, 14, 14, 26
    ys = [w for _, w, _ in pts]
    base = f.get("baseline")
    if base:
        ys += [base * (1 - FILTER_WATCH), base * (1 + FILTER_WATCH)]
    lo, hi = min(ys), max(ys)
    pad = (hi - lo) * 0.2 or 5
    lo, hi = lo - pad, hi + pad
    t0 = pts[0][0].timestamp()
    t1 = max(pts[-1][0].timestamp(), t0 + 86400)

    def X(t): return L + (t.timestamp() - t0) / (t1 - t0) * (W - L - R)
    def Y(v): return T + (hi - v) / (hi - lo) * (H - T - B)

    desc = (f"Line chart of the pump's median draw at {f['rpm']:.0f} RPM, one "
            f"point a day, {len(pts)} days from {pts[0][0]:%-d %b} to "
            f"{pts[-1][0]:%-d %b}, between {min(w for _, w, _ in pts):.0f} W and "
            f"{max(w for _, w, _ in pts):.0f} W."
            + (f" The baseline since the last cleaning is {base:.0f} W." if base else "")
            + (f" {len(f['cleans'])} cleaning{'s are' if len(f['cleans']) != 1 else ' is'}"
               f" marked." if f["cleans"] else ""))
    lab, names = name_svg(f"Pump draw at {f['rpm']:.0f} RPM", desc)
    o = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img"{lab}>', names]
    if base and f.get("since"):
        x0 = max(L, X(f["since"]))
        o.append(f'<rect x="{x0:.1f}" y="{Y(base * (1 + FILTER_WATCH)):.1f}" '
                 f'width="{W - R - x0:.1f}" '
                 f'height="{Y(base * (1 - FILTER_WATCH)) - Y(base * (1 + FILTER_WATCH)):.1f}" '
                 f'class="band"/>')
        o.append(f'<line x1="{x0:.1f}" y1="{Y(base):.1f}" x2="{W - R}" '
                 f'y2="{Y(base):.1f}" class="aim"/>')
        o.append(f'<text x="{W - R - 2}" y="{Y(base) - 5:.1f}" class="aim-lbl" '
                 f'text-anchor="end">baseline {base:.0f} W</text>')
    for v in (lo + (hi - lo) * k for k in (0, 0.5, 1)):
        o.append(f'<text x="{L - 8}" y="{Y(v) + 4:.1f}" class="ytick" '
                 f'text-anchor="end">{v:.0f}</text>')
    for c in f["cleans"]:
        if c.timestamp() < t0:
            continue
        x = X(c)
        o.append(f'<line x1="{x:.1f}" y1="{T}" x2="{x:.1f}" y2="{H - B}" '
                 f'class="eq-mark"/>')
        o.append(f'<text x="{x + 4:.1f}" y="{T + 10}" class="eq-mark-lbl">'
                 f'cleaned {c:%-d %b}</text>')
    # Broken at each cleaning, so the line does not draw a fall the pump never
    # made as a slope between the dirty day and the clean one.
    segs, cur = [], []
    marks = sorted(f["cleans"])
    for t, w, n in pts:
        if cur and any(cur[-1][0] < m <= t for m in marks):
            segs.append(cur)
            cur = []
        cur.append((t, w, n))
    segs.append(cur)
    for seg in segs:
        if len(seg) > 1:
            d = " ".join(f'{"M" if i == 0 else "L"}{X(t):.1f},{Y(w):.1f}'
                         for i, (t, w, _) in enumerate(seg))
            o.append(f'<path d="{d}" class="line"/>')
    for t, w, n in pts:
        o.append(f'<circle cx="{X(t):.1f}" cy="{Y(w):.1f}" r="3.5" class="eq-pt">'
                 f'<title>{t:%a %-d %b} — {w:.0f} W, median of {n} steady '
                 f'sample{"" if n == 1 else "s"}</title></circle>')
    lt, lw, _ = pts[-1]
    o.append(f'<text x="{X(lt) - 8:.1f}" y="{Y(lw) - 9:.1f}" class="lastlbl" '
             f'text-anchor="end">{lw:.0f} W</text>')
    o.append(f'<text x="{L}" y="{H - 6}" class="xtick">{pts[0][0]:%-d %b}</text>')
    o.append(f'<text x="{W - R}" y="{H - 6}" class="xtick" text-anchor="end">'
             f'{pts[-1][0]:%-d %b}</text>')
    o.append("</svg>")
    return "".join(o)


def _filter_row(f):
    if f["grade"] == "measured":
        fig = (f"{f['drift'] * 100:+.1f}% at {f['rpm']:.0f} RPM "
               f"({f['baseline']:.0f} → {f['current']:.0f} W)")
    elif f["grade"] == "bounded":
        fig = (f"within ±{f['bound'] * 100:.1f}% at {f['rpm']:.0f} RPM "
               f"({f['baseline']:.0f} W)")
    elif f.get("baseline"):
        fig = f"baseline {f['baseline']:.0f} W at {f['rpm']:.0f} RPM"
    else:
        fig = "—"
    basis = (f"{f.get('n_base', 0)} steady samples in {_span(*f['base_window'])} "
             f"against {f.get('n_cur', 0)} in {_span(*f['cur_window'])}"
             if f.get("current") is not None else
             f"{f['n_steady']} steady sample{'' if f['n_steady'] == 1 else 's'}"
             f" in the last {WINDOW_DAYS} days"
             + (f"; {f['why']}" if f["why"] else ""))
    return ("Filter loading", fig, f["status"], f["grade"], basis, f["better"])


def _cell_row(c):
    if c.get("latest") is not None:
        fig = f"{c['latest']:.2f} ppm per cell-hour"
        if c["change"] is not None and c["grade"] == "measured":
            fig += f" ({c['change'] * 100:+.0f}% since {c['windows'][0]['end']:%-d %b})"
    else:
        fig = "—"
    ws = c["windows"]
    basis = (f"{len(ws)} window{'' if len(ws) == 1 else 's'} of {CELL_WINDOW} "
             f"days fitted, {c.get('n_clean', 0)} free of low salt, cold water "
             f"and hand-added chlorine"
             if ws else "")
    basis = "; ".join(x for x in (basis, c["why"]) if x)
    return ("Salt cell yield", fig, c["status"], c["grade"], basis, c["better"])


_SRC_WORDS = {"weather": "the weather record", "panel": "the panel's air sensor",
              "given": "a supplied figure", "mixed": "both, within one run",
              "none": "nothing"}
_SRC_SHORT = {"weather": "weather", "panel": "panel sensor", "given": "supplied",
              "mixed": "both", "none": "none"}


def _sources_said(runs):
    """Which outdoor figure the runs used, counted, in words."""
    n = {}
    for r in runs:
        k = r.get("outdoor_src") or "none"
        n[k] = n.get(k, 0) + 1
    return ", ".join(f"{_SRC_WORDS.get(k, k)} for {v} run{'' if v == 1 else 's'}"
                     for k, v in sorted(n.items()))


def _heat_row(h, label):
    if h["bands"]:
        best = max(h["bands"], key=lambda g: g["runs"])
        fig = (f"{best['rate']:.1f}°/h with outdoor {best['band']}"
               + (f" ({best['trend'] * 100:+.0f}% later runs vs earlier)"
                  if best["trend"] is not None else ""))
        n = len(h["runs"])
        basis = (f"{n} run{'' if n == 1 else 's'}, "
                 f"{sum(r['hours'] for r in h['runs']):.1f} h of firing, "
                 f"{_span(min(r['start'] for r in h['runs']), max(r['start'] for r in h['runs']))}")
        basis += "; outdoor from " + _sources_said(h["runs"])
        status = "steady" if h["grade"] == "measured" else "level"
    else:
        fig, basis, status = "—", h["why"], "unknown"
    return (label, fig, status, h["grade"], basis, h["better"])


def block(est, name_svg):
    """The section's body. `name_svg` is render.name_svg, passed in so this
    module never imports the renderer."""
    esc = html.escape
    f, c = est["filter"], est["cell"]
    rows = [_filter_row(f), _cell_row(c),
            _heat_row(est["heat_pool"], "Pool heater"),
            _heat_row(est["heat_spa"], "Spa heater")]
    # A list, not a five-column table: at phone width a table that wide
    # scrolls its verdict and its basis off the right edge, and those are the
    # two columns that say whether to believe the figure at all.
    sigs = "".join(
        f'<div class="eq-sig"><div class="eq-sig-h"><b>{esc(name)}</b>'
        f'{_pill(status)}</div>'
        f'<div class="eq-fig">{esc(fig)}</div>'
        f'<p class="eq-how"><b>{esc(_grade(grade))}:</b> {esc(basis)}</p>'
        + (f'<p class="eq-how"><b>Would be better with:</b> '
           f'{esc("; ".join(better))}</p>' if better else "")
        + '</div>'
        for name, fig, status, grade, basis, better in rows)
    p = [f'<div class="card"><div class="eq-sigs">{sigs}</div>'
         '<p class="sub" style="margin:12px 0 0">Each figure compares this pool '
         'with itself. <b>Measured</b> means both ends rest on enough like-for-like '
         'samples and the difference is larger than the scatter between them; '
         '<b>Bounded</b> means the record shows no change bigger than its own '
         'noise, which is an answer, not a shrug; <b>Not enough data yet</b> '
         'says what is missing.</p></div>']

    # -- the filter, with its picture and the control that resets it
    chart = filter_chart(f, name_svg) if f["series"] else ""
    p.append('<div class="card" style="margin-top:12px">'
             '<div class="ch-h"><h3>Pump draw at its usual speed</h3>'
             f'{_pill(f["status"])}</div>')
    p.append(chart or '<p class="empty">Nothing to draw yet: '
             + esc(f["why"] or "no steady samples") + '.</p>')
    p.append(
        '<p class="sub" style="margin:10px 0 0">One point a day: the median '
        'draw over the samples where the pump had held '
        + (f'<b>{f["rpm"]:.0f} RPM</b>' if f["rpm"] else 'one speed')
        + f' for {int(SETTLE.total_seconds() // 60)} minutes with the spa, the '
        'sheer descent and the solar valve all off'
        + (f' ({f["n_other"]} steady samples at other speeds are not drawn)'
           if f.get("n_other") else '')
        + '. <b>The direction assumed:</b> at a fixed speed a loading filter '
        'raises the head, the pump moves less water, and the draw FALLS. '
        f'The shaded band is ±{FILTER_WATCH * 100:.0f}% of the baseline; a '
        f'fall past {FILTER_CLEAN * 100:.0f}% asks for a cleaning. Both '
        'thresholds are judgements, roughly the 8–10 psi over clean a '
        'pressure gauge is usually read against, and nothing on this pool has '
        'calibrated them yet — one full cycle from clean to cleaned will. '
        'A rise at a fixed speed is not a dirty filter, and is reported as '
        'something else.</p></div>')

    # -- the record of what was done, and the form that adds to it
    last = {k: next((t for t, r in est["maint"] if r.get("task") == k), None)
            for k in TASKS}
    lines = "".join(
        f'<li><b>{esc(TASKS[k])}</b>: '
        + (f'last marked {t:%a %-d %b %Y}' if t else 'never marked')
        + '</li>' for k, t in last.items())
    opts = "".join(f'<option value="{k}">{esc(v)}</option>' for k, v in TASKS.items())
    p.append(
        '<div class="card" style="margin-top:12px"><h3>What was done</h3>'
        f'<ul class="eq-done">{lines}</ul>'
        '<form id="maint-form" class="eq-form">'
        '<label>What<select name="task">' + opts + '</select></label>'
        '<label>When <span class="fmsg">(empty is now)</span>'
        '<input type="datetime-local" name="ts"></label>'
        '<label>Note<input type="text" name="note" maxlength="200" '
        'placeholder="optional"></label>'
        '<button type="submit" class="btn primary">Mark it done</button>'
        '<span class="fmsg" id="maint-msg" role="status"></span>'
        '</form>'
        '<p class="sub" style="margin:10px 0 0">A cleaning restarts the '
        'comparison: the week after it becomes the new baseline. The record is '
        'kept in maintenance.csv and never rewritten; a mark made in error is '
        'superseded by the next real one.</p></div>')

    # -- the cell's windows, as a table (the non-colour route to every figure)
    if c["windows"]:
        def wrow(w):
            salt = "" if w["salt"] is None else f'{w["salt"]:.0f}'
            temp = "" if w["temp"] is None else f'{w["temp"]:.0f}°'
            flags = esc(", ".join(w["flags"])) if w["flags"] else "counted"
            return (f'<tr><td>{w["start"]:%-d %b} – {w["end"]:%-d %b}</td>'
                    f'<td class="n">{w["yield"]:.2f}</td>'
                    f'<td class="n">{w["production"]:.2f}</td>'
                    f'<td class="n">{w["cell_h"]:.1f}</td>'
                    f'<td class="n">{salt}</td><td class="n">{temp}</td>'
                    f'<td>{flags}</td></tr>')
        trs = "".join(wrow(w) for w in c["windows"])
        p.append(
            '<div class="card" style="margin-top:12px"><h3>Salt cell, window by window</h3>'
            '<div class="scroll"><table><thead><tr><th>Window</th>'
            '<th>ppm per cell-hour</th><th>Made, ppm/day</th>'
            '<th>Cell-hours/day</th><th>Salt</th><th>Water</th><th></th></tr></thead>'
            f'<tbody>{trs}</tbody></table></div>'
            '<p class="sub" style="margin:10px 0 0">Production is the rate the '
            'free-chlorine fit on Home already uses; a cell-hour is an hour of '
            'pump running with the cell at 100%. Windows with low salt or cold '
            'water are shown and not counted: the cell throttles in both, and '
            'blaming it for either would be wrong. So are windows with chlorine '
            'added by hand, which the fit would credit to the cell.</p></div>')

    # -- heaters, by outdoor band
    hrows = []
    for key, label in (("heat_pool", "Pool"), ("heat_spa", "Spa")):
        for g in est[key]["bands"]:
            hrows.append(
                f'<tr><td>{label}</td><td>{esc(g["band"])}</td>'
                f'<td class="n">{g["runs"]}</td><td class="n">{g["hours"]:.1f}</td>'
                f'<td class="n">{g["rate"]:.1f}°/h</td>'
                f'<td class="n">{g["lo"]:.1f} to {g["hi"]:.1f}</td>'
                f'<td>{esc(", ".join(_SRC_SHORT.get(x, x) for x in g["sources"]))}</td>'
                f'<td>{esc(_grade(g["grade"]))}'
                + (f'; later runs {g["trend"] * 100:+.0f}%' if g["trend"] is not None else '')
                + '</td></tr>')
    if hrows:
        p.append(
            '<div class="card" style="margin-top:12px"><h3>Heater, by outdoor temperature</h3>'
            '<div class="scroll"><table><thead><tr><th>Body</th><th>Outdoor</th>'
            '<th>Runs</th><th>Hours</th><th>Median rise</th><th>Range</th>'
            '<th>Outdoor from</th><th></th></tr></thead>'
            f'<tbody>{"".join(hrows)}</tbody></table></div>'
            '<p class="sub" style="margin:10px 0 0">Degrees of water temperature '
            'per hour the heater fired, never pooled across the pool and the spa: '
            'one is a few hundred gallons and the other many thousands. Outdoor '
            'is the day’s mean air temperature from the weather record where '
            'that day has one, and otherwise the panel’s own air sensor during '
            'the run — two different quantities, so each row says which it '
            'rests on. A pool run with the solar valve '
            'open is excluded, and the sun still warms the water in a daytime '
            'run, so a summer rate includes it.</p></div>')

    # -- the cheap one
    items = []
    for r in est["long"]:
        items.append(
            f'<li><b>{esc(r["label"])}</b> '
            + (f'has been on since {r["start"]:%a %-d %b %H:%M} — '
               f'{r["hours"]:.1f} h as of {r["end"]:%H:%M}'
               if r["open"] else
               f'was on for {r["hours"]:.1f} h from {r["start"]:%a %-d %b %H:%M}')
            + f', against a usual {r["usual_h"]:.1f} h over its last '
              f'{r["runs"]} runs.</li>')
    for m in est["moves"]:
        items.append(
            f'<li><b>The pump’s {m["which"]} moved</b> on '
            f'{m["date"]:%a %-d %b}, from about '
            f'{_fmt_td(m["was"])} to {_fmt_td(m["now"])}, and stayed there, '
            f'with no poolhound command that day — the shape a schedule '
            f'edited at the panel leaves.</li>')
    p.append(
        '<div class="card" style="margin-top:12px"><h3>Unusually long, or moved without a command</h3>'
        + (f'<ul class="eq-done">{"".join(items)}</ul>' if items else
           '<p class="empty">Nothing stayed on unusually long in the last '
           f'{LONG_RECENT.days} days, and the pump schedule has not moved '
           'without a command in the last month.</p>')
        + f'<p class="sub" style="margin:10px 0 0">"Usual" is each circuit’s own '
          f'last {LONG_LOOKBACK.days} days, and needs {USUAL_RUNS} completed runs '
          f'before anything is called long; "long" is more than '
          f'{LONG_FACTOR:g}× its 90th-percentile run. A change is seen only '
          f'at the next sample, so a time here is good to fifteen minutes.</p></div>')
    return "".join(p)


def section(est, name_svg):
    """The whole Home section, or nothing on a build that has no estimate."""
    if not est:
        return ""
    return ('<section id="home-equipment">\n  <h2>Equipment health</h2>\n'
            '  <p class="sub">What the panel’s own samples say about the '
            'filter, the salt cell and the heater, as trends over weeks. Each '
            'figure says how it is known, how many samples it rests on, and '
            'what would make it better.</p>\n  ' + block(est, name_svg)
            + '\n</section>')


# Rules this section needs that the stylesheet did not have. Tokens only, so
# bin/contrast's palettes cover every colour used.
EXTRA_CSS = """
.eq-mark { stroke:var(--ink3); stroke-width:1; stroke-dasharray:4 3; }
.eq-mark-lbl { fill:var(--ink2); font-size:10px; }
.eq-pt { fill:var(--pump); stroke:var(--panel); stroke-width:1.5; }
.eq-sigs { display:grid; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr)); gap:14px 22px; }
.eq-sig { border-top:1px solid var(--line); padding-top:10px; min-width:0; }
.eq-sig-h { display:flex; align-items:center; justify-content:space-between; gap:8px; }
#home-equipment .pill { text-transform:none; }
.eq-fig { font-size:15px; font-weight:640; font-variant-numeric:tabular-nums; margin:6px 0 4px; }
.eq-how { margin:4px 0 0; font-size:12.5px; line-height:1.45; color:var(--ink2); }
.eq-done { margin:6px 0 10px; padding-left:18px; font-size:13.5px; line-height:1.55; }
.eq-form { display:flex; flex-wrap:wrap; gap:10px 14px; align-items:flex-end; }
.eq-form label { display:flex; flex-direction:column; gap:4px; font-size:12.5px; color:var(--ink2); }
"""

# The page script's half: posting a mark. Inside priv:start/priv:end in the
# template, so the public build carries neither the call nor the route name.
# textContent throughout -- the server's error text is not markup.
JS = """
(function () {
  var f = document.getElementById('maint-form');
  if (!f) return;
  f.addEventListener('submit', function (e) {
    e.preventDefault();
    var msg = document.getElementById('maint-msg');
    var b = f.querySelector('button');
    if (!API || !CANWRITE) {
      msg.textContent = 'Saving needs the server, signed in.';
      msg.className = 'fmsg bad';
      return;
    }
    b.disabled = true;
    msg.textContent = 'Saving';
    msg.className = 'fmsg';
    fetch('/api/maintenance', {method: 'POST', headers: postHeaders(),
        body: JSON.stringify({task: f.task.value, ts: f.ts.value,
                              note: f.note.value})})
      .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.j.error || 'refused');
        if (wroteOk(msg, res.j, 'Marked')) location.reload();
        else b.disabled = false;
      })
      .catch(function (err) {
        b.disabled = false;
        msg.textContent = 'Not saved: ' + err.message;
        msg.className = 'fmsg bad';
      });
  });
})();
"""
