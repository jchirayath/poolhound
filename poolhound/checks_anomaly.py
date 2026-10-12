"""Equipment anomalies: each rule fires on the fault it is for, stays quiet on
an ordinary pool, and is said once.

Every case builds a synthetic sample stream whose answer is known. The quiet
half matters as much as the firing half: an alarm that goes off on a normal
day is an alarm somebody learns to delete, and then it is not there on the
night the heater runs dry.

Timestamps are naive local times throughout, so no case depends on the zone
of the machine running it.
"""
import datetime as dt

from . import anomaly as A
from .render import num, when
from .selftest import check

T0 = dt.datetime(2026, 8, 1)
Q = dt.timedelta(minutes=15)


def _row(t, **kw):
    """One sample of an ordinary afternoon: pump on at 2000 RPM, valves shut."""
    h = t.hour + t.minute / 60.0
    r = {"ts": t.strftime("%Y-%m-%dT%H:%M:%S"), "pump": "1", "pump_rpm": "2000",
         "pump_watts": "600", "swg_pct": "40", "salt_ppm": "3300",
         "pool_temp": f"{80 + (h - 12) * 0.2:.1f}", "air_temp": f"{70 + (h - 12):.1f}",
         "spa_temp": "", "spa": "0", "sheer": "0", "pool_light": "0",
         "spa_light": "0", "pool_heat": "0", "spa_heat": "0", "solar_valve": "0",
         "freeze": "0", "pool_set": "88", "spa_set": "102", "freeze_set": "38"}
    r.update({k: str(v) for k, v in kw.items()})
    return r


def _pool(days, start=T0, edit=None):
    """`days` of an ordinary pool: pump 08:00-16:00, off otherwise.

    edit(t, row) may change a row; it is handed every sample in order.
    """
    out = []
    t = start
    end = start + dt.timedelta(days=days)
    while t < end:
        h = t.hour + t.minute / 60.0
        on = 8 <= h < 16
        r = _row(t) if on else _row(t, pump="0", pump_rpm="", pump_watts="",
                                    salt_ppm="", pool_temp="")
        # The air moves every hour, day and night, the way real air does.
        r["air_temp"] = f"{70 + 8 * ((h - 14) / 12) ** 2 * -1 + 8:.1f}"
        if edit:
            edit(t, r)
        out.append(r)
        t += Q
    return out


def _detect(rows, now=None, cfg=None, **kw):
    now = now or when(rows[-1]["ts"]) + dt.timedelta(minutes=5)
    return A.detect(rows, now, when, num, cfg=cfg, **kw)


def _rules(occs, sev=None):
    return sorted({o["rule"] for o in occs
                   if sev is None or o["severity"] == sev})


def t_an_ordinary_pool_has_no_anomalies():
    """Ten ordinary days: nothing, at any severity."""
    rows = _pool(10)
    check("an ordinary pool trips nothing", _rules(_detect(rows)), [])


def t_a_spa_left_on_is_serious_whatever_is_usual():
    """The absolute limit does not learn: a household that always forgets the
    spa has made forgetting the usual, and it is still a five-hour spa."""
    def spa_every_day(t, r):
        if 16 <= t.hour < 21:
            r.update(pump="1", pump_rpm="2750", pump_watts="900", spa="1",
                     pool_temp="", spa_temp="100")
    rows = _pool(10, edit=spa_every_day)
    got = [o for o in _detect(rows) if o["rule"] == "long_on"]
    check("every five-hour spa in the window is an occurrence",
          (len(got), {o["severity"] for o in got}), (7, {"serious"}))
    check("and it says nothing will switch the spa off",
          "the only safeguard" in got[0]["detail"], True)

    # Still on at the newest sample: open.
    rows = _pool(2) + [_row(T0 + dt.timedelta(days=2, hours=h / 4), spa="1",
                            spa_temp="100", pool_temp="")
                       for h in range(0, 21)]
    o = [o for o in _detect(rows) if o["rule"] == "long_on"][0]
    check("a spa still on is open", o["open"], True)


def t_the_household_limit_is_not_alerted_twice():
    """A run [schedule.max_on] already switched off is the scheduler's to tell.
    A device with a limit not yet reached says when it will act."""
    rows = _pool(1) + [_row(T0 + dt.timedelta(days=1) + h * Q, spa_heat="1",
                            spa="1", spa_temp=f"{80 + h * 0.2:.1f}",
                            pool_temp="")
                       for h in range(0, 11)]
    at = T0 + dt.timedelta(days=1, hours=2, minutes=30)
    net = lambda dev, since: ({"at": at.astimezone(), "state": "off"}
                              if dev == "Spa_Heater" else None)
    o = [o for o in _detect(rows, net=net) if o["rule"] == "long_on"
         and o["subject"] == "spa_heat"][0]
    check("the net's action is carried", "switched it off at 02:30" in o["handled"],
          True)
    check("and nothing is sent about it", A.plan([o], {}, at), [])
    o = [o for o in _detect(rows) if o["rule"] == "long_on"
         and o["subject"] == "spa_heat"][0]
    check("with no net action, the default 3 h limit is named",
          "will switch it off at 03:00" in o["detail"], True)
    o = [o for o in _detect(rows, cfg={"schedule": {"max_on": {"Spa_Heater": 0}}})
         if o["rule"] == "long_on" and o["subject"] == "spa_heat"][0]
    check("and with the limit removed, this is the only safeguard",
          "only safeguard" in o["detail"], True)


def t_a_heater_without_flow_is_serious_and_one_sample_is_not():
    start = T0 + dt.timedelta(days=2, hours=20)

    def heat(n):
        def e(t, r):
            if start <= t < start + n * Q:
                r["pool_heat"] = "1"
        return e
    got = _detect(_pool(3, edit=heat(2)))
    check("two samples of heater-on, pump-off", _rules(got, "serious"),
          ["heat_no_flow"])
    check("one sample is a transition, not a fault",
          _rules(_detect(_pool(3, edit=heat(1)))), [])


def t_a_heater_that_does_not_warm_the_water_is_seen():
    def run(rate):
        def e(t, r):
            if t.date() == (T0 + dt.timedelta(days=2)).date() and 9 <= t.hour < 13:
                k = (t.hour - 9) * 4 + t.minute // 15
                r.update(pool_heat="1", pool_temp=f"{78 + rate * k / 4:.1f}")
        return e
    got = _detect(_pool(3, edit=run(0.0)))
    check("four hours of firing at 78° is a heater not heating",
          _rules(got), ["heat_no_rise"])
    check("a heater climbing a degree an hour is not",
          _rules(_detect(_pool(3, edit=run(1.0)))), [])


def t_a_spa_too_hot_needs_water_moving_past_the_probe():
    def hot(pump):
        def e(t, r):
            if t.date() == (T0 + dt.timedelta(days=2)).date() and 19 <= t.hour < 20:
                r.update(pump=pump, spa="1", spa_temp="106", pool_temp="")
        return e
    check("106° in the spa with the pump on", _rules(_detect(_pool(3, edit=hot("1")))),
          ["spa_too_hot"])
    # The stale-flow rule: with the pump off the probe reads the pipe, and
    # commands.drop_stale_flow_readings() blanks it before anything is judged.
    check("the same figure with the pump off is the pipe, not the spa",
          _rules(_detect(_pool(3, edit=hot("0")))), [])


def t_a_heater_firing_past_its_setpoint_is_seen():
    def e(t, r):
        if t.date() == (T0 + dt.timedelta(days=2)).date() and 12 <= t.hour < 13:
            r.update(pool_heat="1", pool_temp="92", pool_set="88")
    check("92° against 88° with the heater firing",
          _rules(_detect(_pool(3, edit=e))), ["heat_past_set"])


def t_freezing_air_with_the_pump_stopped_is_serious():
    def cold(pump):
        def e(t, r):
            if t.date() == (T0 + dt.timedelta(days=2)).date() and 2 <= t.hour < 4:
                r.update(air_temp="33", pump=pump,
                         **({"pump_rpm": "2000", "pump_watts": "600"} if pump == "1" else {}))
        return e
    got = _detect(_pool(3, edit=cold("0")))
    check("33° air against a 38° setpoint, pump stopped",
          _rules(got, "serious"), ["freeze_risk"])
    check("and it is urgent", got[0]["urgent"], True)
    check("the same night with freeze protection running is not a risk",
          _rules(_detect(_pool(3, edit=cold("1"))), "serious"), [])


def t_a_sudden_change_in_pump_draw_is_not_filter_drift():
    def fall(t, r):
        if t.date() == (T0 + dt.timedelta(days=9)).date() and r["pump"] == "1":
            r["pump_watts"] = "380"
    got = _detect(_pool(10, edit=fall))
    check("a third less draw within a day", _rules(got), ["pump_power"])
    check("and it says which way", "less" in got[0]["title"], True)

    def drift(t, r):
        if r["pump"] == "1":
            r["pump_watts"] = f"{600 * (1 - 0.01 * (t - T0).days):.0f}"
    check("a filter loading 1% a day is Equipment health's, not this",
          _rules(_detect(_pool(10, edit=drift))), [])


def t_a_cell_asked_to_work_in_low_salt_is_noted_not_alarmed():
    def low(t, r):
        if t.date() == (T0 + dt.timedelta(days=2)).date() and r["pump"] == "1":
            r["salt_ppm"] = "2500"
    got = _detect(_pool(3, edit=low), salt_low=3000)
    check("producing at 2500 ppm against a 3000 floor",
          [(o["rule"], o["severity"]) for o in got], [("cell_stress", "info")])

    def latched(t, r):
        if r["pump"] == "0":
            r["salt_ppm"] = "2500"          # the latch, with no water moving
            r["swg_pct"] = "40"
    check("a latched figure with the pump off is not the cell producing",
          _rules(_detect(_pool(3, edit=latched), salt_low=3000)), [])


def t_a_pump_that_has_not_run_is_serious_and_a_quiet_agent_is_not_this():
    def stop(t, r):
        if t >= T0 + dt.timedelta(days=1, hours=10):
            r.update(pump="0", pump_rpm="", pump_watts="", salt_ppm="", pool_temp="")
    rows = _pool(3, edit=stop)
    got = _detect(rows)
    check("over a day without the pump", _rules(got, "serious"), ["no_circulation"])
    late = when(rows[-1]["ts"]) + dt.timedelta(hours=6)
    check("but samples six hours old are the collector alert's to raise",
          _rules(_detect(rows, now=late), "serious"), [])


def t_a_sensor_that_stopped_is_the_samplers_fault_not_the_pools():
    def stuck(t, r):
        if t >= T0 + dt.timedelta(days=1):
            r["air_temp"] = "71"
    check("air reading 71° for two days", _rules(_detect(_pool(3, edit=stuck))),
          ["sensor_stuck"])

    def wild(t, r):
        if t == T0 + dt.timedelta(days=2, hours=3):
            r["air_temp"] = "212"
    got = _detect(_pool(3, edit=wild))
    check("one impossible reading", [(o["rule"], o["severity"]) for o in got],
          [("sensor_range", "info")])


def t_config_moves_a_threshold_and_switches_a_rule_off():
    def cold(t, r):
        if t.date() == (T0 + dt.timedelta(days=2)).date() and 2 <= t.hour < 4:
            r.update(air_temp="33")
    rows = _pool(3, edit=cold)
    check("freeze_risk switched off in config",
          _rules(_detect(rows, cfg={"anomaly": {"freeze_risk": {"enabled": False}}}),
                 "serious"), [])
    check("a lower default setpoint, when the panel does not say",
          _rules(_detect([dict(r, freeze_set="") for r in rows],
                         cfg={"anomaly": {"freeze_risk": {"default_set": 30}}}),
                 "serious"), [])
    _, unknown = A.settings({"anomaly": {"freeze_risk": {"limt": 3}, "nosuch": {}}})
    check("a key it does not understand is said, not swallowed",
          sorted(unknown), ["freeze_risk.limt", "nosuch"])


def _open_heater(n_on, n_off=0):
    """A pool whose heater fires with the pump stopped for n_on samples at
    the end of day 2, then (optionally) stops."""
    end = T0 + dt.timedelta(days=2, hours=20)
    return _pool(2) + [
        dict(_row(end + i * Q, pump="0", pump_rpm="", pump_watts="",
                  salt_ppm="", pool_temp=""),
             pool_heat="1" if i < n_on else "0")
        for i in range(n_on + n_off)]


def t_said_once_when_it_starts_and_once_when_it_ends():
    from . import watch as W
    sent = []

    def fake(**kw):
        sent.append((kw["title"], kw["severity"]))
        return True
    st = {"anomaly": {}}
    rows = _open_heater(3)
    now = when(rows[-1]["ts"]) + dt.timedelta(minutes=5)
    W._watch_anomalies({}, now, st, True, False, samples=rows, notify_fn=fake,
                       inputs={})
    check("the fault is said", [s[1] for s in sent], ["serious"])
    W._watch_anomalies({}, now + Q, st, True, False, samples=rows, notify_fn=fake,
                       inputs={})
    check("and not again on the next run", len(sent), 1)
    rows = _open_heater(3, n_off=2)
    W._watch_anomalies({}, now + 3 * Q, st, True, False, samples=rows,
                       notify_fn=fake, inputs={})
    check("its end is said, at the severity it was raised at",
          sent[-1], ("Ended: " + sent[0][0], "serious"))
    W._watch_anomalies({}, now + 4 * Q, st, True, False, samples=rows,
                       notify_fn=fake, inputs={})
    check("and the end is said once", len(sent), 2)


def t_an_undelivered_alert_is_offered_again():
    from . import watch as W
    tries = []
    st = {"anomaly": {}}
    rows = _open_heater(3)
    now = when(rows[-1]["ts"]) + dt.timedelta(minutes=5)
    r = W._watch_anomalies({}, now, st, True, False, samples=rows,
                           notify_fn=lambda **kw: tries.append(1) and False,
                           inputs={})
    check("counted undelivered", r, (1, 0, 1))
    r = W._watch_anomalies({}, now + Q, st, True, False, samples=rows,
                           notify_fn=lambda **kw: True, inputs={})
    check("and sent on the next run that can", r, (1, 1, 0))


def t_the_first_run_does_not_replay_the_week():
    """A fault that ended before anomaly watching began is adopted silently;
    one still open is said, because it is still true."""
    from . import watch as W
    sent = []
    st = {}
    rows = _open_heater(3, n_off=4)
    now = when(rows[-1]["ts"]) + dt.timedelta(minutes=5)
    W._watch_anomalies({}, now, st, True, False, samples=rows,
                       notify_fn=lambda **kw: sent.append(kw) or True, inputs={})
    check("an ended fault is not replayed on a first run", sent, [])
    st = {}
    W._watch_anomalies({}, now, st, True, False, samples=_open_heater(3),
                       notify_fn=lambda **kw: sent.append(kw) or True, inputs={})
    check("an open one is said", len(sent), 1)


def t_an_info_rule_is_shown_and_not_sent_unless_asked():
    def stuck(t, r):
        if t >= T0 + dt.timedelta(days=1):
            r["air_temp"] = "71"
    occs = _detect(_pool(3, edit=stuck))
    check("an info anomaly is not notified by default", A.plan(occs, {}, T0), [])
    occs = _detect(_pool(3, edit=stuck),
                   cfg={"anomaly": {"sensor_stuck": {"notify": True}}})
    check("unless the household asked", [k for k, _ in A.plan(occs, {}, T0)],
          ["alert"])


def t_an_open_serious_anomaly_leads_home():
    occs = _detect(_open_heater(3))
    acts = A.actions(occs)
    check("an open serious anomaly is a Fix item", [a["pri"] for a in acts], ["fix"])
    html_ = A.section(occs)
    check("the section carries its severity as a word", "Serious" in html_, True)


def t_the_example_config_names_every_rule_and_threshold():
    """config.example.toml is where an owner finds out a threshold exists. A
    rule added to RULES and not to it is a setting nobody can discover."""
    import os, re
    from . import config
    from .selftest import skipped
    p = os.path.join(config.root(), "config", "config.example.toml")
    if not os.path.exists(p):
        skipped("the example config names every anomaly rule",
                "no config/config.example.toml in this tree")
        return
    text = open(p, encoding="utf-8").read()
    missing = []
    for name, spec in A.RULES.items():
        block = re.search(r"# \[anomaly\." + re.escape(name) + r"\][^\n]*\n((?:#  ?[a-z_].*\n)*)",
                          text)
        if not block:
            missing.append(name)
            continue
        for k in spec["params"]:
            if not re.search(r"^# " + re.escape(k) + r" = ", block.group(1), re.M):
                missing.append(f"{name}.{k}")
    check("every rule and threshold is in config.example.toml", missing, [])


def _an_extra_rule():
    real = dict(A.RULES)
    A.RULES["nobody_documented_this"] = {"params": {"x": 1.0}, "severity": "info",
                                         "label": "", "why": "", "matters": ""}
    return lambda: (A.RULES.clear(), A.RULES.update(real))


# ------------------------------------------------------------------ breakers
def _an(needle, replacement):
    from .checks_structure import _break_live_code
    return lambda: _break_live_code("poolhound/anomaly.py", "poolhound.anomaly",
                                    needle, replacement)


def _watch_live(needle, replacement):
    from .checks_structure import _break_live_code
    return lambda: _break_live_code("poolhound/watch.py", "poolhound.watch",
                                    needle, replacement)


def _register():
    from .checks_structure import PROVES
    PROVES.extend([
        (t_an_ordinary_pool_has_no_anomalies,
         _an('            if b - a < dt.timedelta(hours=hours) or b < ctx["since"]:',
             '            if b < ctx["since"]:'),
         "air that changed at every sample called a stuck sensor"),
        (t_a_spa_left_on_is_serious_whatever_is_usual,
         _an("            if length < dt.timedelta(hours=hours):",
             "            if length < dt.timedelta(hours=hours * 9):"),
         "a five-hour spa let through because it happens every day"),
        (t_the_household_limit_is_not_alerted_twice,
         _an('        if not o.get("notify") or o.get("handled"):',
             '        if not o.get("notify"):'),
         "a run the household limit switched off alerted a second time"),
        (t_a_heater_without_flow_is_serious_and_one_sample_is_not,
         _an('    n = int(p.get("samples") or 2)\n    min_rpm',
             '    n = 1\n    min_rpm'),
         "one sample caught mid-transition called a heater running dry"),
        (t_a_heater_that_does_not_warm_the_water_is_seen,
         _an("            rise = pts[-1][1] - pts[0][1]",
             "            rise = 99"),
         "a heater calling for gas and warming nothing passed as fine"),
        (t_a_spa_too_hot_needs_water_moving_past_the_probe,
         _an('        t = ctx["num"](_flow(s).get("spa_temp"))',
             '        t = ctx["num"](s.get("spa_temp"))'),
         "the pipe's temperature with the pump off read as the spa's"),
        (t_a_heater_firing_past_its_setpoint_is_seen,
         _an("            return t >= sp + margin", "            return False"),
         "a heater past its setpoint never seen"),
        (t_freezing_air_with_the_pump_stopped_is_serious,
         _an("        if _on(pump):\n            return False\n        if not _off(pump):",
             "        if False:\n            return False\n        if not _off(pump) and not _on(pump):"),
         "freeze protection running the pump read as the pump stopped"),
        (t_a_sudden_change_in_pump_draw_is_not_filter_drift,
         _an("        if abs(d) / m >= change and abs(d) > 4 * sd:",
             "        if abs(d) > 0:"),
         "a filter's slow drift called a pump fault"),
        (t_a_cell_asked_to_work_in_low_salt_is_noted_not_alarmed,
         _an("        row = _flow(s)\n        pct = num(row.get(\"swg_pct\"))",
             "        row = dict(s)\n        pct = num(row.get(\"swg_pct\"))"),
         "the latched salt figure with no flow read as the cell producing"),
        (t_a_pump_that_has_not_run_is_serious_and_a_quiet_agent_is_not_this,
         _an('    if ctx["now"] - ctx["last_t"] > ctx["fresh"]:\n        return []',
             '    if False:\n        return []'),
         "a quiet agent reported as a stopped pump"),
        (t_a_sensor_that_stopped_is_the_samplers_fault_not_the_pools,
         _an('            if not (lo <= v <= hi):', '            if False:'),
         "a reading of 212° air believed"),
        (t_config_moves_a_threshold_and_switches_a_rule_off,
         _an('        if not p.get("enabled", True):\n            continue',
             '        if False:\n            continue'),
         "a rule switched off in config that still fires"),
        (t_said_once_when_it_starts_and_once_when_it_ends,
         _an('        if o is None or not o["open"]:\n            out.append(("recover", o or rec))',
             '        if False:\n            out.append(("recover", o or rec))'),
         "a fault that ended and nobody was told"),
        (t_an_undelivered_alert_is_offered_again,
         _watch_live("            state.setdefault(key, A.record(o, told=None))",
                     "            state.setdefault(key, A.record(o, told=now.isoformat()))"),
         "an alert nobody received recorded as sent"),
        (t_the_first_run_does_not_replay_the_week,
         _an("            if first_run and not o[\"open\"]:\n                continue",
             "            if False:\n                continue"),
         "a first run mailing a week of faults that are over"),
        (t_an_info_rule_is_shown_and_not_sent_unless_asked,
         _an('        p = {"enabled": True, "notify": spec["severity"] != "info",\n             **spec["params"]}',
             '        p = {"enabled": True, "notify": True,\n             **spec["params"]}'),
         "a stuck air sensor counted undelivered on a server with no desktop"),
        (t_an_open_serious_anomaly_leads_home,
         _an('            pri="fix" if serious else "watch", title=o["title"],',
             '            pri="watch", title=o["title"],'),
         "a heater running dry filed under keep an eye on"),
        (t_the_example_config_names_every_rule_and_threshold, _an_extra_rule,
         "a rule nobody can find out how to configure"),
    ])


_register()
