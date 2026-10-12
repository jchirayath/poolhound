"""Equipment health: the filter, the salt cell, the heater, and refusal.

Each case is a synthetic pool whose answer is known in advance. The ones that
matter most are the ones where the honest answer is "not enough data yet", or
where the obvious arithmetic would blame the wrong thing: a valve read as a
dirty filter, a latched salt reading counted as many, a cold week blamed on
the cell, the spa's heating rate averaged into the pool's.

Timestamps are naive local times throughout, so no case depends on the zone of
the machine running it.
"""
import datetime as dt
import os
import tempfile

from . import equipment as E
from .render import num, when
from .selftest import check

T0 = dt.datetime(2026, 8, 1)


def _row(t, pump=1, rpm=2000, watts=600, spa=0, sheer=0, solar="0", **kw):
    r = {"ts": t.strftime("%Y-%m-%dT%H:%M:%S"), "pump": str(pump),
         "pump_rpm": "" if rpm is None else str(rpm),
         "pump_watts": "" if watts is None else f"{watts:.0f}",
         "spa": str(spa), "sheer": str(sheer), "solar_valve": solar}
    r.update({k: str(v) for k, v in kw.items()})
    return r


def _days(n, watts_on_day, start=T0, hours=(8, 12), every=15, rpm=2000):
    """`n` days of a pump running `hours`, sampled every `every` minutes, with
    the draw on each day given by watts_on_day(i)."""
    out = []
    for i in range(n):
        d = start + dt.timedelta(days=i)
        t = d.replace(hour=0)
        while t < d + dt.timedelta(days=1):
            on = hours[0] <= t.hour < hours[1]
            out.append(_row(t, pump=1 if on else 0, rpm=rpm if on else None,
                            watts=watts_on_day(i) if on else None))
            t += dt.timedelta(minutes=every)
    return out


def t_steady_state_leaves_out_the_start_the_valves_and_a_gap():
    """Only samples that describe the plumbing at rest are compared."""
    t = T0.replace(hour=8)
    q = dt.timedelta(minutes=15)
    rows = [
        _row(t - q, pump=0, rpm=None, watts=None),
        _row(t, watts=900),                          # the pump just started
        _row(t + q, watts=600),                      # settled: kept
        _row(t + 2 * q, watts=640, sheer=1),         # sheer open: plumbing
        _row(t + 3 * q, watts=600),                  # just after it shut
        _row(t + 4 * q, watts=601),                  # settled again: kept
        _row(t + 4 * q + dt.timedelta(hours=1), watts=602),  # after a gap
        _row(t + 5 * q + dt.timedelta(hours=1), watts=603, spa=""),  # unknown
        _row(t + 6 * q + dt.timedelta(hours=1), watts=604, solar=""),
        _row(t + 7 * q + dt.timedelta(hours=1), watts=605, solar=""),
    ]
    got = [w for _, _, w in E.steady(rows, when, num)]
    check("only settled samples with every valve shut are steady",
          got, [600, 601, 605])


def t_a_loading_filter_is_measured_and_asks_for_a_clean():
    # 0.6% a day from a clean filter: the last week sits ~13% under the first.
    rows = _days(28, lambda i: 600 * (1 - 0.006 * i))
    now = T0 + dt.timedelta(days=28)
    f = E.filter_loading(rows, [T0], now, when, num)
    check("the drift is measured", f["grade"], "measured")
    check("and it is past the cleaning threshold", f["status"], "clean")
    check("measured at the one speed it ran", f["rpm"], 2000)
    check("the fall is the right size", round(f["drift"], 2), -0.13)
    acts = E.actions({"filter": f, "cell": {"grade": "insufficient"},
                      "long": []})
    check("Home is told to clean it", [a["title"] for a in acts],
          ["Clean the filter"])
    check("as something to do now", acts[0]["pri"], "do")


def t_a_cleaning_restarts_the_baseline():
    """Marked clean three days ago: the dirty weeks before do not count."""
    rows = _days(31, lambda i: 600 * (1 - 0.006 * i) if i < 28 else 600)
    now = T0 + dt.timedelta(days=31)
    f = E.filter_loading(rows, [T0, T0 + dt.timedelta(days=28)], now, when, num)
    check("too soon after a cleaning to say anything", f["grade"],
          "insufficient")
    check("and no prompt to clean it again", f["status"], "unknown")
    check("the baseline is the clean filter", round(f["baseline"]), 600)
    check("it says how long to wait", "days" in f["better"][0], True)


def t_noise_is_a_bound_not_a_drift():
    import random
    rnd = random.Random(7)
    rows = _days(28, lambda i: 600)
    for r in rows:
        if r["pump_watts"]:
            r["pump_watts"] = f"{600 + rnd.uniform(-12, 12):.0f}"
    f = E.filter_loading(rows, [T0], T0 + dt.timedelta(days=28), when, num)
    check("a flat draw is bounded, not measured", f["grade"], "bounded")
    check("and called steady", f["status"], "steady")
    check("with a bound a few percent wide", f["bound"] < 0.03, True)
    check("and nothing to do", E.actions({"filter": f, "cell": {"grade": "x"},
                                          "long": []}), [])


def t_a_valve_is_not_a_dirty_filter():
    """The solar valve opening for longer each day must not read as drift.

    Its samples draw far more; if they were averaged in, a pool whose solar
    season lengthened would be told its filter was getting cleaner.
    """
    rows = []
    for i in range(28):
        d = T0 + dt.timedelta(days=i)
        for q in range(16):
            t = d.replace(hour=8) + dt.timedelta(minutes=15 * q)
            # Most of the morning by the last week, so that if these samples
            # were let in they would carry the median with them.
            solar = q >= 16 - (i // 2)
            rows.append(_row(t, watts=780 if solar else 600,
                             solar="1" if solar else "0"))
        rows.append(_row(d.replace(hour=12), pump=0, rpm=None, watts=None))
    f = E.filter_loading(rows, [T0], T0 + dt.timedelta(days=28), when, num)
    check("valve-open samples are not compared", f["status"], "steady")
    check("and the baseline is the valve-shut draw", round(f["baseline"]), 600)


def t_a_rise_is_not_called_a_dirty_filter():
    rows = _days(28, lambda i: 600 * (1 + 0.006 * i))
    f = E.filter_loading(rows, [T0], T0 + dt.timedelta(days=28), when, num)
    check("a rise at fixed speed is its own status", f["status"], "rising")
    acts = E.actions({"filter": f, "cell": {"grade": "x"}, "long": []})
    check("which is watched, not cleaned", [(a["pri"], "Clean" in a["title"])
                                           for a in acts], [("watch", False)])


def t_a_thin_record_says_so_instead_of_guessing():
    rows = _days(3, lambda i: 600 * (1 - 0.05 * i))
    f = E.filter_loading(rows, [], T0 + dt.timedelta(days=3), when, num)
    check("three days is not enough", f["grade"], "insufficient")
    check("and states no current figure", f["current"], None)
    check("and does not prompt", E.actions({"filter": f, "cell": {"grade": "x"},
                                            "long": []}), [])
    none = _days(10, lambda i: None)
    f = E.filter_loading(none, [], T0 + dt.timedelta(days=10), when, num)
    check("a pump that reports no watts is named as the reason",
          "wattage" in f["why"], True)


def t_latched_salt_is_one_reading_not_many():
    """SWG/PPM repeats its last value with the pump off. A day's salt must be
    the live readings, not a vote weighted by how long the pump was off."""
    rows = []
    d = T0
    for q in range(96):
        t = d + dt.timedelta(minutes=15 * q)
        on = 32 <= q < 40
        rows.append(_row(t, pump=1 if on else 0, salt_ppm=3300 if on else 3600,
                         swg_pct=50, pool_temp=80))
    days = E._per_day(rows, when, num)
    check("the latched figure does not count", days[d.date().isoformat()]["salt"],
          3300.0)
    check("cell-hours are pump hours at the dial",
          round(days[d.date().isoformat()]["cell_h"], 2), 1.0)


def _cell_pool(n_days, temp=80.0, salt=3300.0):
    samples, readings = [], []
    for i in range(n_days):
        d = T0 + dt.timedelta(days=i)
        for q in range(96):
            t = d + dt.timedelta(minutes=15 * q)
            on = 32 <= q < 56
            samples.append(_row(t, pump=1 if on else 0, swg_pct=50,
                                salt_ppm=salt if on else "",
                                pool_temp=temp if on else ""))
        readings.append({"measured": (d.replace(hour=9)).strftime("%Y-%m-%dT%H:%M:%S"),
                         "free_cl": "3.0", "day": i})
    return samples, readings


def t_the_cell_refuses_a_thin_record():
    s, r = _cell_pool(5)
    c = E.cell_yield(s, r, [], [], T0 + dt.timedelta(days=5), when, num,
                     fit=lambda rr, ss: {"per_cell_hour": 1.0})
    check("five readings fit nothing", c["grade"], "insufficient")
    check("and it says how many it has",
          c["why"].startswith("5 free-chlorine readings"), True)
    check("and what would help", bool(c["better"]), True)


def t_cold_water_and_low_salt_are_not_the_cells_fault():
    s, r = _cell_pool(60, temp=55.0)
    c = E.cell_yield(s, r, [], [], T0 + dt.timedelta(days=60), when, num,
                     fit=lambda rr, ss: {"per_cell_hour": 1.0}, salt_low=2700)
    check("every cold window is set aside", all("cold water" in w["flags"]
                                                for w in c["windows"]), True)
    check("so nothing is said about the cell", c["grade"], "insufficient")
    s, r = _cell_pool(60, salt=2400.0)
    c = E.cell_yield(s, r, [], [], T0 + dt.timedelta(days=60), when, num,
                     fit=lambda rr, ss: {"per_cell_hour": 1.0}, salt_low=2700)
    check("low salt is named rather than blamed",
          all("low salt" in w["flags"] for w in c["windows"]), True)


def t_a_cell_making_less_is_measured_only_when_it_clears_the_scatter():
    s, r = _cell_pool(90)
    # Production falls with the window's last day: a cell scaling over.
    def fit(rr, ss):
        last = max(x["day"] for x in rr)
        return {"per_cell_hour": 2.0 * (1 - last / 150)}
    c = E.cell_yield(s, r, [], [], T0 + dt.timedelta(days=90), when, num, fit=fit)
    check("a steady fall across many windows is measured", c["grade"], "measured")
    check("and called a decline", c["status"], "declining")
    check("per cell-hour, against 3 cell-hours a day",
          round(c["windows"][-1]["cell_h"], 2), 3.0)
    s, r = _cell_pool(30)
    c = E.cell_yield(s, r, [], [], T0 + dt.timedelta(days=30), when, num,
                     fit=lambda rr, ss: {"per_cell_hour": 1.0})
    check("one window is a level, not a trend", c["status"], "level")
    check("and only bounded", c["grade"], "bounded")


def t_a_cleaned_cell_is_a_wait_not_a_failed_fit():
    """Marked cleaned ten days ago: the reason given is the cleaning."""
    s, r = _cell_pool(60)
    c = E.cell_yield(s, r, [], [T0 + dt.timedelta(days=50)],
                     T0 + dt.timedelta(days=60), when, num,
                     fit=lambda rr, ss: {"per_cell_hour": 1.0})
    check("nothing is judged across a cleaning", c["windows"], [])
    check("and the wait is named as the cleaning's",
          "marked cleaned" in c["why"], True)


def _heat(start, n, body, step, temp0, spa_on=0, heat=1, set_=104, solar="0",
          both=False):
    out = []
    for k in range(n):
        t = start + dt.timedelta(minutes=15 * k)
        temp = temp0 + step * k
        r = _row(t, spa=spa_on, solar=solar, air_temp=70,
                 pool_heat=heat if body == "pool" or both else 0,
                 spa_heat=heat if body == "spa" or both else 0,
                 pool_set=set_, spa_set=set_)
        r["spa_temp" if body == "spa" else "pool_temp"] = f"{temp:.1f}"
        # One sensor in the shared plumbing: in spa mode the "pool"
        # temperature is the spa's water, which is exactly why the mode has
        # to decide which body a run belongs to.
        if body == "spa":
            r["pool_temp"] = f"{temp:.1f}"
        out.append(r)
    return out


def t_pool_and_spa_heat_are_never_one_number():
    # Both heaters enabled at the panel for the evening, which is ordinary:
    # only the valves decide which water the heat goes into.
    s = (_heat(T0.replace(hour=8), 9, "pool", 0.25, 78)          # 1 deg/h
         + _heat(T0.replace(hour=18), 6, "spa", 5.0, 80, spa_on=1,
                 both=True))                                       # 20 deg/h
    pool = E.heater_runs(s, "pool", when, num)
    spa = E.heater_runs(s, "spa", when, num)
    check("the pool's rate is the pool's", [round(r["rate"], 1) for r in pool], [1.0])
    check("the spa's rate is the spa's", [round(r["rate"], 1) for r in spa], [20.0])
    check("the first sample of a run is the pipe, not the water",
          pool[0]["start"], T0.replace(hour=8, minute=15))


def t_a_heater_run_is_cut_at_the_setpoint_and_the_sun_is_left_out():
    # 10 deg/h towards 95: 80, 82.5 ... and 95 is where the heater cycles.
    s = _heat(T0.replace(hour=18), 10, "spa", 2.5, 80, spa_on=1, set_=95)
    r = E.heater_runs(s, "spa", when, num)
    check("only the climb to the setpoint counts", round(r[0]["rise"], 1), 10.0)
    check("at the rate it climbed", round(r[0]["rate"], 1), 10.0)
    sol = _heat(T0.replace(hour=8), 9, "pool", 0.25, 78, solar="1")
    check("a pool run with the solar valve open is not the heater's",
          E.heater_runs(sol, "pool", when, num), [])
    short = _heat(T0.replace(hour=8), 3, "pool", 0.25, 78)
    check("a run too short to mean anything is refused",
          E.heater_runs(short, "pool", when, num), [])


def t_outdoor_comes_from_whatever_is_handed_in():
    s = _heat(T0.replace(hour=8), 9, "pool", 0.25, 78)
    h = E.heater_rates(s, "pool", when, num, outdoor=lambda t, smp: 40.0)
    check("a weather feed can stand in for the panel's air sensor",
          h["bands"][0]["band"], E.BANDS[0][2])
    check("one run is a bound, not a measurement", h["grade"], "bounded")


def t_the_weather_record_is_preferred_and_the_panel_stands_in():
    """A day with a weather row uses its mean air; a day without, the panel's
    sensor -- and each run says which, because they are different figures."""
    s = (_heat(T0.replace(hour=8), 9, "pool", 0.25, 78)
         + _heat(T0.replace(hour=8) + dt.timedelta(days=1), 9, "pool", 0.25, 78))
    wx = {T0.date().isoformat(): {"air_mean_f": 45.0}}
    runs = E.heater_runs(s, "pool", when, num, outdoor=E.outdoor_from(wx, num))
    check("the weather day reads the weather record, the other the panel",
          [(r["outdoor"], r["outdoor_src"]) for r in runs],
          [(45.0, "weather"), (70.0, "panel")])
    est = E.assess(s, [], [], [], [], T0 + dt.timedelta(days=2), when, num,
                   weather=wx)
    check("and assess() hands the record through",
          sorted(est["heat_pool"]["bands"][0]["sources"]
                 + est["heat_pool"]["bands"][1]["sources"]),
          ["panel", "weather"])


def t_a_light_left_on_is_noticed_and_a_short_history_is_not_judged():
    def lights(n, last_on_h):
        rows = []
        for i in range(n):
            d = T0 + dt.timedelta(days=i)
            for q in range(96):
                t = d + dt.timedelta(minutes=15 * q)
                h = q / 4
                on = 20 <= h < 22 or (i == n - 1 and h >= 20 - last_on_h + 2)
                rows.append(_row(t, pump=0, rpm=None, watts=None,
                                 pool_light=1 if on else 0))
        return rows
    rows = lights(12, 12)
    now = T0 + dt.timedelta(days=12)
    got = E.long_runs(rows, now, when)
    check("a light on for twelve hours against a usual two is noticed",
          [(g["device"], g["open"]) for g in got], [("Aux_1", True)])
    check("and its usual is its own", round(got[0]["usual_h"], 1), 2.0)
    got = E.long_runs(lights(4, 12), T0 + dt.timedelta(days=4), when)
    check("three runs are not enough to know what usual is", got, [])


def t_a_schedule_moved_at_the_panel_is_seen_and_ours_is_not():
    rows = []
    for i in range(16):
        d = T0 + dt.timedelta(days=i)
        start = 9.5 if i >= 8 else 8.0
        for q in range(96):
            t = d + dt.timedelta(minutes=15 * q)
            on = start <= q / 4 < start + 6
            rows.append(_row(t, pump=1 if on else 0, rpm=2000 if on else None,
                             watts=600 if on else None))
    now = T0 + dt.timedelta(days=16)
    got = E.schedule_moves(rows, [], now, when)
    check("the move is reported once, on the day it happened",
          sorted((m["date"].day, m["which"]) for m in got),
          [(9, "start"), (9, "stop")])
    ours = [{"at": (T0 + dt.timedelta(days=8, hours=9, minutes=20))
             .strftime("%Y-%m-%dT%H:%M:%S"), "action": "control",
             "object": "Filter_Pump", "by": "owner@example.com"}]
    check("a move a poolhound command accounts for is not reported",
          E.schedule_moves(rows, ours, now, when), [])


def t_a_cleaning_is_recorded_once_and_refuses_nonsense():
    work = tempfile.mkdtemp(prefix="poolhound-maint-")
    old = os.environ.get("POOLHOUND_DATA")
    os.environ["POOLHOUND_DATA"] = work
    try:
        row = E.record("filter", {}, by="owner@example.com", note="backwashed")
        with open(os.path.join(work, E.FILE)) as f:
            lines = f.read().splitlines()
        check("a header, then the row", lines[0], ",".join(E.COLS))
        check("one row", len(lines), 2)
        check("the task is kept", row["task"], "filter")
        for bad, kw in (("unknown task", {"task": "pump"}),
                        ("a time in the future",
                         {"task": "cell",
                          "at": dt.datetime.now() + dt.timedelta(days=1)})):
            try:
                E.record(kw.pop("task"), {}, **kw)
                refused = False
            except ValueError:
                refused = True
            check(f"{bad} is refused", refused, True)
        with open(os.path.join(work, E.FILE)) as f:
            check("and nothing refused was written", len(f.read().splitlines()), 2)
    finally:
        if old is None:
            os.environ.pop("POOLHOUND_DATA", None)
        else:
            os.environ["POOLHOUND_DATA"] = old


# ------------------------------------------------------------------ breakers
#
# Every case above is a detector, and a detector has to be shown to go red.
# Each breaker is the plausible wrong version of one line of equipment.py --
# the edit somebody tidying it would make -- registered with the meta-check in
# checks_structure.py, which rewrites the line, reloads the module, and
# asserts the case notices.
def _eq(needle, replacement):
    from .checks_structure import _break_live_code
    return lambda: _break_live_code("poolhound/equipment.py",
                                    "poolhound.equipment", needle, replacement)


def _register():
    from .checks_structure import PROVES
    PROVES.extend([
        (t_steady_state_leaves_out_the_start_the_valves_and_a_gap,
         _eq("        if t - since < settle:", "        if False:"),
         "the priming sample after a start compared as if it were settled"),
        (t_a_loading_filter_is_measured_and_asks_for_a_clean,
         _eq('"clean" if drift <= -FILTER_CLEAN else',
             '"clean" if drift <= -FILTER_CLEAN * 3 else'),
         "a filter past the threshold not asked to be cleaned"),
        (t_a_cleaning_restarts_the_baseline,
         _eq("epoch = [(t, w) for t, w in at if last_clean is None or t > last_clean]",
             "epoch = [(t, w) for t, w in at if True]"),
         "the dirty weeks before a cleaning kept in the new baseline"),
        (t_noise_is_a_bound_not_a_drift,
         _eq("    if abs(mc - mb) <= 3 * se:", "    if abs(mc - mb) <= 0 * se:"),
         "scatter reported as a measured drift"),
        (t_a_valve_is_not_a_dirty_filter,
         _eq('if any(v != "0" and not (v == "" and c in BLANK_IS_OFF)',
             'if any(v == "x" and not (v == "" and c in BLANK_IS_OFF)'),
         "samples with the solar valve open compared with ones without"),
        (t_a_rise_is_not_called_a_dirty_filter,
         _eq('"rising" if drift >= FILTER_WATCH else "steady")',
             '"steady" if drift >= FILTER_WATCH else "steady")'),
         "a rise at fixed speed called steady"),
        (t_a_thin_record_says_so_instead_of_guessing,
         _eq("    if cur_start < base_end:", "    if False:"),
         "two overlapping ends of three days compared as a trend"),
        (t_latched_salt_is_one_reading_not_many,
         _eq("row = commands.drop_stale_flow_readings(dict(s))", "row = dict(s)"),
         "the latched salt figure counted once per pump-off sample"),
        (t_the_cell_refuses_a_thin_record,
         _eq("    if len(rd) < 8:", "    if len(rd) < 2:"),
         "a cell judged on five readings without saying so"),
        (t_cold_water_and_low_salt_are_not_the_cells_fault,
         _eq("        if temp is not None and temp < COLD_WATER:",
             "        if False:"),
         "cold water blamed on the cell"),
        (t_a_cell_making_less_is_measured_only_when_it_clears_the_scatter,
         _eq('    if len(clean) >= 4 and abs(b["yield"] - a["yield"]) > 2 * sd:',
             "    if False:"),
         "a steady decline never called measured"),
        (t_a_cleaned_cell_is_a_wait_not_a_failed_fit,
         _eq("    if last_clean and end - last_clean < dt.timedelta(days=CELL_WINDOW):",
             "    if False:"),
         "a cleaning the owner made read as readings that would not fit"),
        (t_pool_and_spa_heat_are_never_one_number,
         _eq('and _on(s.get("spa")) == spa_mode', "and True"),
         "the spa's heating rate counted as the pool's"),
        (t_a_heater_run_is_cut_at_the_setpoint_and_the_sun_is_left_out,
         _eq("and not (sp is not None and temp >= sp - 0.5))",
             "and not (sp is not None and temp >= sp + 99))"),
         "a heater cycling at its setpoint averaged into the climb"),
        (t_outdoor_comes_from_whatever_is_handed_in,
         _eq("    outdoor = outdoor or outdoor_from(None, num)\n",
             "    outdoor = outdoor_from(None, num)\n"),
         "a weather feed handed in and ignored"),
        (t_the_weather_record_is_preferred_and_the_panel_stands_in,
         _eq('        v = day.get("air_mean_f")\n', '        v = None\n'),
         "the weather record's air temperature never used"),
        (t_a_light_left_on_is_noticed_and_a_short_history_is_not_judged,
         _eq("        if len(done_) < USUAL_RUNS:", "        if len(done_) < 1:"),
         "three runs taken as what usual is"),
        (t_a_schedule_moved_at_the_panel_is_seen_and_ours_is_not,
         _eq("            if d in commanded:", "            if False:"),
         "our own command reported as a schedule edited at the panel"),
        (t_a_cleaning_is_recorded_once_and_refuses_nonsense,
         _eq("    if task not in TASKS:", "    if False:"),
         "a task nobody defined written into the record"),
    ])


_register()
