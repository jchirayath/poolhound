"""Equipment anomalies: the panel's own samples read for damage, danger and
waste that is happening NOW, rather than for wear over weeks.

WHAT THIS IS, AND WHY IT IS NOT equipment.py

equipment.py reads the same columns for slow drift -- a filter loading over a
month, a cell scaling over a season -- and grades each answer measured, bounded
or not enough data. That is a judgement made over weeks, and it is right never
to wake anybody for it. This module asks the other question: is something
wrong this hour that costs money, breaks equipment or hurts somebody if it
waits until morning? A heater firing with no water through it, a spa at
106 degF, a freezing night with the pump stopped. Those are EVENTS with a start
and, eventually, an end, and the product's job with an event is to say so once
when it starts and once when it ends.

So the two modules share samples and helpers and not a purpose. Where an answer
already exists it is reused, never recomputed: steady-state pump samples come
from equipment.steady(), heater runs from equipment.heater_runs(), "longer than
it usually is" from equipment.long_runs(), on/off runs from equipment._runs_of(),
the stale-flow rule from commands.drop_stale_flow_readings(), what the household
limit did from schedule.net_lookup(). The notifier is watch.notify(), through
watch._watch_anomalies(); there is no second one.

EACH RULE IS A SMALL NAMED FUNCTION with a stated severity, a stated reason it
matters (damage, safety or cost), and an [anomaly.<rule>] table in config.toml
that can switch it off, stop it notifying, or move its threshold. RULES below is
the whole list and the one place a threshold's default lives.

SEVERITY, AND WHY AN "info" RULE DOES NOT NOTIFY BY DEFAULT

`serious` rules notify by default, at "serious", which is the email bar a fresh
install has. `info` rules are shown on Home and notify only when asked to
([anomaly.<rule>] notify = true): on the server there is no desktop, so an info
notice below the email bar reaches nobody, and watch counts an alert nobody
could be sent as undelivered -- the run goes red over a sensor that is merely
stuck. The stock alert made the same choice for the same reason.

ONCE PER OCCURRENCE, AND A NOTICE WHEN IT ENDS

An occurrence is keyed by rule, subject and the sample it started at, so the
same heater fault is one alert however many watchdog runs see it. A serious one
still open a day later is said again, once a day, which is the rule every other
standing fault in watch.py follows. When an occurrence that was told about
ends, that is said too, at the SAME severity as the alert: the person who was
emailed that the spa was at 106 is the person who needs to hear it came down,
and an "info" recovery would go nowhere on a server whose bar is "serious" --
and then be retried forever as undelivered. Only a delivered send is recorded,
the rule every branch of watch.py learned separately.

COORDINATION WITH THE HOUSEHOLD LIMIT

[schedule.max_on] switches a device off past a limit, and tells somebody when
it does. A long run that limit has already acted on is NOT alerted here as
well: the scheduler's message is the message, and Home carries what it did. A
device with no limit set gets an alert that says so -- this notice is then the
only safeguard -- and one with a limit not yet reached says when it will act.

FIFTEEN-MINUTE SAMPLING BOUNDS EVERYTHING HERE

The agent samples every fifteen minutes. A condition that comes and goes
between two samples is invisible, and a time on this page is good to fifteen
minutes. Where one stray sample could be a transition caught mid-change -- the
heater's state flipping a second before the pump's -- a rule asks for two in a
row, and says so.

IDEAS CONSIDERED AND REJECTED, because the data cannot show them reliably

  * The cell producing WITHOUT FLOW. swg_pct is the cell's SETPOINT, not its
    output, and it reads the same with the pump off as on; the panel's flow
    interlock decides whether the cell is powered, and nothing in the sample
    says whether it was. A rule would fire every night.
  * Heater SHORT-CYCLING. A heater at its setpoint cycles legitimately, on a
    period of minutes; sampled every fifteen, a healthy thermostat and a faulty
    one alias to the same 1-0-1 pattern.
  * FREEZE RISK FROM THE FORECAST. The weather record holds days that have
    ENDED (weather.py asks the archive for yesterday), so "tonight will freeze"
    is not in it. The panel's own air sensor, which freeze protection itself
    acts on, is used instead.
  * FILTER PRESSURE, FLOW RATE, AN ACTUAL CELL OUTPUT. No sensor for any of
    them reaches the sample.
  * A STUCK SALT READING. The cell reports salt in steps of 100 ppm and a
    healthy pool holds one step for days, so "the same value for days" is the
    normal case.
  * AUX 4 AND AUX 5 LEFT ON. The panel reports no state for them
    (commands.SWITCHES), so there is nothing to measure a run against.
  * PUMP PRIMING LOST. Covered by the pump-power rule: a pump that has lost its
    prime draws far less at the same speed, which is what that rule sees.
"""
import datetime as dt
import html

from . import commands as C
from . import equipment as E

# How far back Home's history reaches and watch looks for something new.
LOOKBACK = dt.timedelta(days=7)
# Extra history read before LOOKBACK, for the rules whose baseline or run
# starts before the window: the pump-power baseline (a week) and a sensor stuck
# for four days.
LEAD = dt.timedelta(days=8)
# A sample interval longer than this is a gap: what happened inside it is
# unknown, so a run does not continue across it. equipment.GAP, not a copy.
GAP = E.GAP
# A serious occurrence still open is said again this often.
REMIND = dt.timedelta(hours=24)

SEVERITY_ORDER = {"info": 0, "serious": 1, "critical": 2}

# ------------------------------------------------------------------ the rules
#
# Every default below is a judgement, and each says where it came from. They
# are all overridable per install under [anomaly.<rule>] in config.toml --
# config.example.toml lists every one -- because a heater sized for a spa and a
# heater sized for a pool do not climb at the same rate, and nothing here has
# calibrated them against this pool yet.
RULES = {
    "long_on": {
        "label": "On far longer than anybody means it to be",
        "severity": "serious", "matters": "cost",
        "why": "A gas heater or a spa left running is the most expensive thing "
               "this pool can do, and it shows up on a bill rather than on a "
               "dashboard.",
        # Hours of an unbroken run, per state column; 0 removes one.
        # spa_heat 2 h: a spa reaches temperature inside an hour from pool
        #   temperature (this pool's own heater_rates measure the spa in tens of
        #   degrees an hour), so two hours of FIRING without reaching setpoint is
        #   past any soak. Deliberately under [schedule.max_on]'s 3 h, so this
        #   says so before the household limit acts.
        # pool_heat 12 h: raising a pool ten degrees takes most of a day on a
        #   residential heater, so only a run past half a day is unusual.
        # spa 4 h: spa mode -- the valves, the spa pump speed, a higher cell
        #   output -- past an evening's soak.
        "params": {"spa_heat": 2.0, "pool_heat": 12.0, "spa": 4.0},
    },
    "left_on": {
        "label": "Left on",
        "severity": "info", "matters": "cost",
        "why": "Cheap, but nobody meant it: a light or the sheer descent "
               "running all night. The sheer also aerates, which raises pH.",
        "params": {"pool_light": 8.0, "spa_light": 8.0, "sheer": 8.0},
    },
    "long_usual": {
        "label": "On longer than it usually is",
        "severity": "info", "matters": "cost",
        "why": "Measured against this circuit's own recent runs, not a chart: "
               "equipment.long_runs() decides, and this only says so.",
        "params": {},
    },
    "heat_no_flow": {
        "label": "Heater firing with no water moving",
        "severity": "serious", "matters": "damage", "urgent": True,
        "why": "A gas heater with no water through it overheats its heat "
               "exchanger within minutes; the pressure switch that should stop "
               "it has either failed or been bypassed, or the panel is reporting "
               "a heater that cannot be firing.",
        # min_rpm 0: only a STOPPED pump counts. The speed a variable-speed pump
        # needs for its heater's flow switch is the installer's figure, and a
        # universal guess would raise a serious alarm on every heating day of a
        # pool whose heater is happy at that speed.
        "params": {"min_rpm": 0.0, "samples": 2.0},
    },
    "heat_no_rise": {
        "label": "Heater firing and the water not warming",
        "severity": "serious", "matters": "cost",
        "why": "Gas is being called for and no heat is arriving: a failed "
               "ignition, a gas supply problem or a scaled heat exchanger. "
               "Every hour of it is gas, or a heater in lockout, for nothing.",
        # Pool: 2 h for at most 0.5 degF. A residential heater on a residential
        # pool climbs one to three degrees an hour; the panel reports whole
        # degrees, so "no more than half a degree" is "the reading did not move".
        # Spa: 30 min for at most 2 degF, against tens of degrees an hour.
        "params": {"pool_minutes": 120.0, "pool_rise": 0.5,
                   "spa_minutes": 30.0, "spa_rise": 2.0},
    },
    "spa_too_hot": {
        "label": "Spa hotter than is safe",
        "severity": "serious", "matters": "safety", "urgent": True,
        "why": "104 °F is the ceiling the US Consumer Product Safety "
               "Commission gives for a hot tub, and the limit US spa standards "
               "build in: above it, heat stroke and drowning from drowsiness "
               "become real risks, and sooner for children and in pregnancy.",
        "params": {"limit": 104.0, "samples": 2.0},
    },
    "heat_past_set": {
        "label": "Heater firing above its setpoint",
        "severity": "serious", "matters": "safety",
        "why": "A heater still firing well past the temperature it was set to "
               "has a thermostat or sensor that is not stopping it. In the spa "
               "that ends at a temperature nobody chose.",
        "params": {"margin": 2.0, "samples": 2.0},
    },
    "freeze_risk": {
        "label": "Freezing air and the pump stopped",
        "severity": "serious", "matters": "damage", "urgent": True,
        "why": "Still water in pipes, the filter and the heater freezes and "
               "splits them. Freeze protection exists to run the pump at this "
               "temperature, and it is not running.",
        # The panel's own freeze_set where the sample carries it; this where it
        # does not. 38 degF is the Jandy AquaLink factory setting, inside the
        # 34-42 range commands.SETPOINTS admits.
        "params": {"default_set": 38.0, "samples": 2.0},
    },
    "pump_power": {
        "label": "Pump drawing far more or far less than usual at its speed",
        "severity": "serious", "matters": "damage",
        "why": "At a fixed speed the draw should hold to a few percent. A sudden "
               "fall is air in the suction, a lost prime, a blocked impeller or a "
               "closed valve -- a pump running dry ruins its seal. A sudden rise "
               "is a motor working against something: a seizing bearing, debris "
               "in the impeller. Distinct from the slow fall of a loading filter, "
               "which Equipment health measures over weeks.",
        # 25%: a loading filter moves the draw 5-10% over weeks
        # (equipment.FILTER_WATCH/FILTER_CLEAN); a quarter within a day is not
        # that. Against the median of the week before, at the same speed, with
        # the same valves shut -- equipment.steady() decides which samples.
        "params": {"change": 0.25, "baseline_days": 7.0, "samples": 2.0},
    },
    "cell_stress": {
        "label": "Salt cell asked to produce in conditions that wear it",
        "severity": "info", "matters": "damage",
        "why": "A cell run with too little salt, or in cold water, wears its "
               "plate coating faster -- which is most of a cell's cost. Most "
               "cells throttle or stop themselves; the panel reports the output "
               "it ASKED for, so this is a prompt to check, not proof of harm.",
        # salt_min: unset uses the bottom of this pool's salt target, the same
        # figure Equipment health sets low-salt windows aside by. cold_f 50:
        # the temperature most cells (Hayward's and Jandy's manuals among them)
        # stop producing at; equipment.COLD_WATER, 60, is where they DERATE.
        "params": {"salt_min": None, "cold_f": 50.0, "samples": 4.0},
    },
    "no_circulation": {
        "label": "Pump has not run",
        "severity": "serious", "matters": "damage",
        "why": "Water that does not move is not filtered or chlorinated: the "
               "salt cell makes nothing without flow, and algae can take hold "
               "in a day of warm still water.",
        # gap_hours: a hole in the record shorter than this is not taken as the
        # pump having run unseen; longer, nobody knows, and the run restarts.
        "params": {"hours": 24.0, "gap_hours": 2.0},
    },
    "sensor_stuck": {
        "label": "A sensor reporting the same value for days",
        "severity": "info", "matters": "data",
        "why": "Air temperature moves every hour and pool temperature every "
               "day. A value that does not is a sensor or a connection that has "
               "stopped, and every figure built on it -- freeze protection's "
               "air reading included -- is wrong with it.",
        "params": {"air_hours": 36.0, "water_hours": 96.0},
    },
    "sensor_range": {
        "label": "A reading outside what is physically possible",
        "severity": "info", "matters": "data",
        "why": "It is the panel or a sensor that is wrong, not the pool; the "
               "reading is not believed anywhere it would matter.",
        "params": {},
    },
}

# The bounds a reading must fall inside to be a reading at all. Generous on
# purpose: this is "the sensor is broken", not "the pool is odd".
RANGES = {
    "air_temp":   (-40.0, 140.0, "°F"),
    "pool_temp":  (33.0, 115.0, "°F"),
    "spa_temp":   (33.0, 115.0, "°F"),
    "salt_ppm":   (0.0, 10000.0, " ppm"),
    # 3450 RPM is the top speed of a residential variable-speed pump, and the
    # largest residential pumps draw about 3 kW.
    "pump_rpm":   (0.0, 3600.0, " RPM"),
    "pump_watts": (0.0, 4000.0, " W"),
}

COLUMN_LABEL = {
    "air_temp": "Air temperature", "pool_temp": "Pool temperature",
    "spa_temp": "Spa temperature", "salt_ppm": "Salt reading",
    "pump_rpm": "Pump speed", "pump_watts": "Pump draw",
}


# ------------------------------------------------------------ configuration
def settings(cfg=None):
    """({rule: params with enabled and notify}, [keys not understood]).

    A key this module does not know is RETURNED, not dropped: a misspelt
    threshold that silently kept its default is a setting somebody believes
    in and nothing honours. Home lists them.
    """
    raw = (cfg or {}).get("anomaly") or {}
    out, unknown = {}, []
    if not isinstance(raw, dict):
        return ({k: {"enabled": True, "notify": v["severity"] != "info",
                     **v["params"]} for k, v in RULES.items()}, ["anomaly"])
    for name, spec in RULES.items():
        p = {"enabled": True, "notify": spec["severity"] != "info",
             **spec["params"]}
        mine = raw.get(name)
        if isinstance(mine, dict):
            for k, v in mine.items():
                if k not in p:
                    unknown.append(f"{name}.{k}")
                elif k in ("enabled", "notify"):
                    if isinstance(v, bool):
                        p[k] = v
                    else:
                        unknown.append(f"{name}.{k}")
                else:
                    try:
                        p[k] = float(v)
                    except (TypeError, ValueError):
                        unknown.append(f"{name}.{k}")
        elif mine is not None:
            unknown.append(name)
        out[name] = p
    for k in raw:
        if k not in RULES:
            unknown.append(k)
    return out, unknown


# ------------------------------------------------------------ small helpers
def _v(s, col):
    return str(s.get(col, "") if s.get(col) is not None else "").strip()


def _on(v):
    return v in ("1", "1.0")


def _off(v):
    return v in ("0", "0.0")


def _flow(s):
    """The row with readings that need flow blanked when the pump is off. A
    COPY: the rule mutates, and these rows are the loader's."""
    return C.drop_stale_flow_readings(dict(s))


def _spans(S, pred, gap, min_n=1):
    """[(start, end, n, closed)]: runs of samples where pred() is True.

    pred returns True, False, or None for "this sample does not say" -- which
    neither extends nor ends a run. A gap in the record longer than `gap` ends
    one unclosed (nobody knows what happened inside it). `end` is the last
    sample the condition held at; `closed` is True when a sample said it had
    stopped. Runs of fewer than `min_n` samples are dropped: one sample caught
    mid-transition is not a finding.
    """
    out, cur, prev_t = [], None, None
    for t, s in S:
        if cur is not None and prev_t is not None and t - prev_t > gap:
            if cur[2] >= min_n:
                out.append((cur[0], cur[1], cur[2], False))
            cur = None
        prev_t = t
        v = pred(s)
        if v is None:
            continue
        if v:
            if cur is None:
                cur = [t, t, 0]
            cur[1] = t
            cur[2] += 1
        elif cur is not None:
            if cur[2] >= min_n:
                out.append((cur[0], cur[1], cur[2], True))
            cur = None
    if cur is not None and cur[2] >= min_n:
        out.append((cur[0], cur[1], cur[2], False))
    return out


def _dur(td):
    m = td.total_seconds() / 60.0
    if m < 90:
        return f"{m:.0f} min"
    return f"{m / 60:.1f} h"


def _t(t):
    return f"{t:%a %-d %b %H:%M}"


def _occ(ctx, rule, subject, start, end, closed, title, detail, facts=(),
         severity=None, **kw):
    """One occurrence, in the shape watch.py and the page read."""
    spec = RULES[rule]
    o = {"rule": rule, "subject": subject,
         "key": f"{rule}:{subject}:{start:%Y-%m-%dT%H:%M}",
         "severity": severity or spec["severity"],
         "urgent": bool(spec.get("urgent")), "matters": spec["matters"],
         "start": start, "end": end,
         "open": (not closed) and end == ctx["last_t"],
         "title": title, "detail": detail, "facts": list(facts),
         "handled": None, "as_of": ctx["last_t"]}
    o.update(kw)
    return o


# ================================================================== the rules
def long_on(ctx, p, rule="long_on"):
    """A heater or the spa reported on, without a break, past an absolute
    limit -- independent of what is usual, because a household that always
    forgets the spa makes forgetting the usual."""
    out = []
    from . import schedule
    limits = schedule.max_on_hours(ctx["cfg"])
    for col in RULES[rule]["params"]:
        hours = p.get(col) or 0
        if hours <= 0:
            continue
        dev = C.COLUMN_DEVICE.get(col)
        label = C.label_for(dev, ctx["cfg"]) if dev else col
        for a, b, closed in E._runs_of(ctx["S"], col, ctx["gap"]):
            length = b - a
            if length < dt.timedelta(hours=hours):
                continue
            if b < ctx["since"]:
                continue
            # E._runs_of ends a closed run AT the sample that said off; the
            # occurrence is open only when the run reaches the newest sample.
            o = _occ(ctx, rule, col, a, b, closed, "", "")
            limit = limits.get(dev)
            net = ctx["net"](dev, a) if (ctx["net"] and dev) else None
            if o["open"]:
                o["title"] = f"{label} has been on for {_dur(length)}"
            else:
                o["title"] = f"{label} was on for {_dur(length)}"
            lines = [f"On since {_t(a)}"
                     + (f", as of {b:%H:%M}." if o["open"] else
                        f", until {b:%H:%M}." if closed else
                        f"; the record has a gap after {b:%H:%M}, so it ran at "
                        f"least this long."),
                     f"The limit here is {hours:g} h."]
            if net:
                when_ = net["at"].astimezone().replace(tzinfo=None)
                said = {"off": f"The household limit switched it off at "
                               f"{when_:%H:%M}.",
                        "trying": f"The household limit has been trying to "
                                  f"switch it off since {when_:%H:%M}; the "
                                  f"panel has not yet reported it off.",
                        "gave up": f"The household limit tried to switch it "
                                   f"off from {when_:%H:%M} and gave up. It is "
                                   f"probably still on: check the panel."
                        }[net["state"]]
                lines.append(said)
                # The scheduler said so when it acted; this does not say it
                # again. Home carries it.
                o["handled"] = said
            elif limit:
                cut = a + dt.timedelta(hours=limit)
                if length >= dt.timedelta(hours=limit):
                    lines.append(f"It is past the household limit of {limit:g} h "
                                 f"and nothing has switched it off: the "
                                 f"scheduler may not be running. Switch it off "
                                 f"on Pool control.")
                elif o["open"]:
                    lines.append(f"The household limit ([schedule.max_on], "
                                 f"{limit:g} h) will switch it off at "
                                 f"{cut:%H:%M} if it is still on.")
            elif dev and C.off_command(dev) is not None:
                lines.append("Nothing will switch it off automatically: "
                             "[schedule.max_on] sets no limit for it, so this "
                             "notice is the only safeguard.")
            o["detail"] = " ".join(lines)
            o["facts"] = [("On since", _t(a)), ("For", _dur(length)),
                          ("Limit", f"{hours:g} h")]
            o["device"] = dev
            out.append(o)
    return out


def left_on(ctx, p):
    """The same absolute test for the cheap circuits, at info."""
    return long_on(ctx, p, rule="left_on")


def long_usual(ctx, p):
    """equipment.long_runs(), promoted to an occurrence. Not recomputed: the
    "usual" is that function's, and so is the decision."""
    out = []
    for r in ctx["long"] or ():
        col = C.state_column(r["device"])
        if not col:
            continue
        if r["end"] < ctx["since"]:
            continue
        o = _occ(ctx, "long_usual", col, r["start"], r["end"], not r["open"],
                 (f"{r['label']} has been on for {r['hours']:.1f} h" if r["open"]
                  else f"{r['label']} was on for {r['hours']:.1f} h"),
                 (f"Since {r['start']:%a %H:%M}. Over its last {r['runs']} runs "
                  f"it was usually on for about {r['usual_h']:.1f} h. Leave it if "
                  f"that was meant; this is only how it compares."),
                 [("Usual", f"{r['usual_h']:.1f} h"),
                  ("This run", f"{r['hours']:.1f} h")])
        o["open"] = bool(r["open"])
        o["device"] = r["device"]
        out.append(o)
    return out


def heat_no_flow(ctx, p):
    """A heater reported firing while the pump is stopped (or below the
    configured minimum speed), two samples running."""
    out = []
    n = int(p.get("samples") or 2)
    min_rpm = p.get("min_rpm") or 0
    for body, (heat_c, _t_c, _s_c, _mode) in E.BODIES.items():
        def pred(s, heat_c=heat_c):
            h = _v(s, heat_c)
            if _off(h):
                return False
            if not _on(h):
                return None
            pump = _v(s, "pump")
            if _off(pump):
                return True
            if _on(pump) and min_rpm > 0:
                rpm = ctx["num"](s.get("pump_rpm"))
                if rpm is not None and rpm < min_rpm:
                    return True
                return False if rpm is not None else None
            return False if _on(pump) else None
        label = "Pool heater" if body == "pool" else "Spa heater"
        for a, b, k, closed in _spans(ctx["S"], pred, ctx["gap"], n):
            if b < ctx["since"]:
                continue
            o = _occ(ctx, "heat_no_flow", heat_c, a, b, closed,
                     f"{label} reported firing with the pump "
                     + ("stopped" if not min_rpm else
                        f"stopped or under {min_rpm:.0f} RPM"),
                     f"{k} samples in a row from {_t(a)} to {b:%H:%M}. Switch "
                     f"the heater off at the panel and have the flow switch "
                     f"looked at before it fires again.",
                     [("From", _t(a)), ("Samples", str(k))])
            out.append(o)
    return out


def heat_no_rise(ctx, p):
    """A heater run long enough to have warmed the water, that did not.

    The runs are equipment.heater_runs()'s, so they share its exclusions: the
    first sample of a run (the water standing in the pipe), the pool with the
    solar valve open, and the water at its setpoint where a heater cycles. The
    rise is measured over the first `minutes` of the run, from flow-valid
    readings only, so a run that eventually warmed does not hide a first two
    hours that did nothing -- and the occurrence does not vanish once it does.
    """
    out = []
    S = ctx["S"]
    for body, (heat_c, temp_c, _s, _m) in E.BODIES.items():
        mins = p.get(f"{body}_minutes") or 0
        limit = p.get(f"{body}_rise")
        if mins <= 0 or limit is None:
            continue
        span = dt.timedelta(minutes=mins)
        for r in E.heater_runs(ctx["rows"], body, ctx["when"], ctx["num"],
                               gap=ctx["gap"]):
            if r["end"] - r["start"] < span or r["end"] < ctx["since"]:
                continue
            pts = [(t, ctx["num"](_flow(s).get(temp_c))) for t, s in S
                   if r["start"] <= t <= r["start"] + span]
            pts = [(t, v) for t, v in pts if v is not None]
            if len(pts) < 2:
                continue
            rise = pts[-1][1] - pts[0][1]
            if rise > limit:
                continue
            label = "Pool heater" if body == "pool" else "Spa heater"
            o = _occ(ctx, "heat_no_rise", heat_c, r["start"], r["end"],
                     r["end"] != ctx["last_t"],
                     f"{label} fired for {_dur(span)} and the water rose "
                     f"{rise:+.1f}°",
                     f"From {pts[0][1]:.1f}° at {pts[0][0]:%H:%M} to "
                     f"{pts[-1][1]:.1f}° at {pts[-1][0]:%H:%M}; the limit here "
                     f"is a rise of more than {limit:g}° in {_dur(span)}. "
                     f"Check the heater for an error code or a lockout, and "
                     f"that the gas is on.",
                     [("Run from", _t(r["start"])), ("Rise", f"{rise:+.1f}°")])
            o["open"] = r["end"] == ctx["last_t"]
            out.append(o)
    return out


def spa_too_hot(ctx, p):
    """The spa's water above the safe ceiling, in spa mode with water moving
    past the probe, two samples in a row."""
    limit = p.get("limit") or 104.0
    n = int(p.get("samples") or 2)
    peak = {}

    def pred(s):
        if not _on(_v(s, "spa")):
            return None
        # The stale-flow rule decides whether the probe reads the spa: with
        # the pump off it reads the pipe, and is blanked.
        t = ctx["num"](_flow(s).get("spa_temp"))
        if t is None:
            return None
        if t > limit:
            peak[s.get("ts")] = t
        return t > limit
    out = []
    for a, b, k, closed in _spans(ctx["S"], pred, ctx["gap"], n):
        if b < ctx["since"]:
            continue
        hi = max((v for (t, s) in ctx["S"] if a <= t <= b
                  for v in [peak.get(s.get("ts"))] if v is not None), default=None)
        out.append(_occ(ctx, "spa_too_hot", "spa_temp", a, b, closed,
                        f"The spa reached {hi:.0f}°F" if hi else
                        f"The spa is above {limit:g}°F",
                        f"Above {limit:g}°F in {k} samples from {_t(a)} to "
                        f"{b:%H:%M}. Nobody should be in it; lower the spa "
                        f"setpoint and check the heater stops.",
                        [("Highest", f"{hi:.0f}°F" if hi else "?"),
                         ("Limit", f"{limit:g}°F")]))
    return out


def heat_past_set(ctx, p):
    """A heater still firing with its body's water past setpoint + margin."""
    margin = p.get("margin")
    margin = 2.0 if margin is None else margin
    n = int(p.get("samples") or 2)
    out = []
    for body, (heat_c, temp_c, set_c, mode) in E.BODIES.items():
        def pred(s, heat_c=heat_c, temp_c=temp_c, set_c=set_c, mode=mode):
            h = _v(s, heat_c)
            if _off(h):
                return False
            if not _on(h) or _on(_v(s, "spa")) != mode:
                return None
            row = _flow(s)
            t, sp = ctx["num"](row.get(temp_c)), ctx["num"](row.get(set_c))
            if t is None or sp is None:
                return None
            return t >= sp + margin
        label = "Pool heater" if body == "pool" else "Spa heater"
        for a, b, k, closed in _spans(ctx["S"], pred, ctx["gap"], n):
            if b < ctx["since"]:
                continue
            row = _flow(next(s for t, s in ctx["S"] if t == b))
            out.append(_occ(ctx, "heat_past_set", heat_c, a, b, closed,
                            f"{label} firing above its setpoint",
                            f"The water read {row.get(temp_c)}° against a "
                            f"setpoint of {row.get(set_c)}° with the heater "
                            f"still firing, {k} samples from {_t(a)}. Lower the "
                            f"setpoint or switch it off, and have the "
                            f"thermostat or its sensor checked.",
                            [("Water", f"{row.get(temp_c)}°"),
                             ("Setpoint", f"{row.get(set_c)}°")]))
    return out


def freeze_risk(ctx, p):
    """Air at or below freeze protection's setpoint, or freeze protection
    reporting active, with the pump stopped -- two samples running."""
    n = int(p.get("samples") or 2)
    dflt = p.get("default_set") or 38.0
    num = ctx["num"]

    def pred(s):
        pump = _v(s, "pump")
        if _on(pump):
            return False
        if not _off(pump):
            return None
        fz = _on(_v(s, "freeze"))
        air = num(s.get("air_temp"))
        fs = num(s.get("freeze_set"))
        fs = dflt if fs is None else fs
        if air is None and not fz:
            return None
        return fz or air <= fs
    out = []
    for a, b, k, closed in _spans(ctx["S"], pred, ctx["gap"], n):
        if b < ctx["since"]:
            continue
        row = next(s for t, s in ctx["S"] if t == b)
        air = num(row.get("air_temp"))
        fs = num(row.get("freeze_set"))
        out.append(_occ(ctx, "freeze_risk", "pump", a, b, closed,
                        "Freezing air and the pump is not running",
                        (f"Air {air:.0f}°F" if air is not None else "Freeze "
                         "protection active")
                        + f" against a freeze setpoint of "
                        + (f"{fs:.0f}°F" if fs is not None else
                           f"{dflt:.0f}°F (assumed: the panel did not say)")
                        + f", with the pump stopped in {k} samples from {_t(a)}. "
                        "Start the pump now, and find out why freeze protection "
                        "did not.",
                        [("Air", f"{air:.0f}°F" if air is not None else "?"),
                         ("Freeze setpoint",
                          f"{fs:.0f}°F" if fs is not None else f"{dflt:.0f}°F assumed")]))
    return out


def pump_power(ctx, p):
    """Steady-state draw at one speed against the median of the week before.

    The samples are equipment.steady()'s -- pump settled, spa, sheer and solar
    shut -- so a valve or a speed change is not read as a fault. The baseline
    starts after the last marked filter cleaning, because a cleaning raises
    the draw on purpose. Thin baselines are not judged.
    """
    num = ctx["num"]
    change = p.get("change") or 0.25
    days = dt.timedelta(days=p.get("baseline_days") or 7)
    n = int(p.get("samples") or 2)
    st = E.steady(ctx["rows"], ctx["when"], num, gap=ctx["gap"])
    if not st:
        return []
    cleans = sorted(ctx["cleans"] or ())
    hold = dt.timedelta(hours=1)
    flags = []          # (t, rpm, w, median, sign) for every judged sample
    for t, rpm, w in st:
        if t < ctx["since"] - dt.timedelta(days=1):
            continue
        clean = max((c for c in cleans if c <= t), default=None)
        base = [(t2, w2) for t2, r2, w2 in st
                if r2 == rpm and t - days <= t2 <= t - hold
                and (clean is None or t2 > clean)]
        if len(base) < E.MIN_SAMPLES or len({t2.date() for t2, _ in base}) < E.MIN_DAYS:
            flags.append((t, rpm, w, None, 0))
            continue
        ws = [w2 for _, w2 in base]
        m = E._median(ws)
        sd = E._spread(ws)
        d = w - m
        sign = 0
        if abs(d) / m >= change and abs(d) > 4 * sd:
            sign = 1 if d > 0 else -1
        flags.append((t, rpm, w, m, sign))
    out, cur = [], []

    def close(closed):
        if len(cur) < n:
            return
        a, b = cur[0][0], cur[-1][0]
        if b < ctx["since"]:
            return
        rpm, m, sign = cur[-1][1], cur[-1][3], cur[-1][4]
        ws = [x[2] for x in cur]
        w = E._median(ws)
        word = "more" if sign > 0 else "less"
        o = _occ(ctx, "pump_power", f"rpm{rpm}{'+' if sign > 0 else '-'}",
                 a, b, closed,
                 f"The pump is drawing {abs(w - m) / m * 100:.0f}% {word} than "
                 f"usual at {rpm:.0f} RPM",
                 f"{w:.0f} W against a usual {m:.0f} W at the same speed with "
                 f"the same valves shut, in {len(cur)} settled samples from "
                 f"{_t(a)}. "
                 + ("Check the pump basket and the suction side for air, and "
                    "that no valve has been closed."
                    if sign < 0 else
                    "Listen to the motor, and check the impeller and the "
                    "basket for debris."),
                 [("Now", f"{w:.0f} W"), ("Usual", f"{m:.0f} W"),
                  ("Speed", f"{rpm:.0f} RPM")])
        # Open while the newest settled sample is still flagged: with the pump
        # off nobody knows, and the last word stands.
        o["open"] = not closed and b == st[-1][0]
        out.append(o)
    prev_sign = 0
    for t, rpm, w, m, sign in flags:
        if sign and cur and (sign != prev_sign or rpm != cur[-1][1]):
            close(True)
            cur = []
        if sign:
            cur.append((t, rpm, w, m, sign))
        elif m is not None and cur:
            close(True)
            cur = []
        prev_sign = sign
    close(False)
    return out


def cell_stress(ctx, p):
    """The cell asked to produce, with water moving, in low salt or cold."""
    num = ctx["num"]
    salt_min = p.get("salt_min") or ctx.get("salt_low")
    cold = p.get("cold_f")
    n = int(p.get("samples") or 4)
    out = []

    def producing(s):
        # No pump test of its own: with the pump off the stale-flow rule
        # blanks the salt and the water temperature, so there is nothing to
        # judge -- and a second copy of that rule here would be free to drift
        # from the one both collectors apply.
        row = _flow(s)
        pct = num(row.get("swg_pct"))
        if pct is None:
            return None
        return row if pct > 0 else False

    def low(s):
        row = producing(s)
        if not isinstance(row, dict):
            return row
        sp = num(row.get("salt_ppm"))
        if sp is None or sp <= 0 or not salt_min:
            return None
        return sp < salt_min

    def chill(s):
        row = producing(s)
        if not isinstance(row, dict):
            return row
        if _on(_v(row, "spa")):
            return None
        tp = num(row.get("pool_temp"))
        if tp is None or cold is None:
            return None
        return tp < cold

    for subj, pred, title, why in (
            ("salt", low, "The cell is producing with the salt low",
             f"The cell read under {salt_min or 0:.0f} ppm while asked for "
             f"output. Add salt to the range on the lab's figure, not the cell's."),
            ("cold", chill, "The cell is producing in cold water",
             f"The water was under {cold or 0:.0f}°F while the cell was asked for "
             f"output. Most cells stop themselves here; turning the output to "
             f"0% until it warms spares the plates.")):
        for a, b, k, closed in _spans(ctx["S"], pred, ctx["gap"], n):
            if b < ctx["since"]:
                continue
            out.append(_occ(ctx, "cell_stress", subj, a, b, closed, title,
                            f"{why} {k} samples from {_t(a)} to {b:%H:%M}.",
                            [("From", _t(a)), ("Samples", str(k))]))
    return out


def no_circulation(ctx, p):
    """The pump reported stopped for longer than `hours`, from a record that
    is still arriving: a quiet agent is the collector alert's, not this."""
    hours = p.get("hours") or 24
    gap = dt.timedelta(hours=p.get("gap_hours") or 2)
    if ctx["now"] - ctx["last_t"] > ctx["fresh"]:
        return []

    def pred(s):
        v = _v(s, "pump")
        return True if _off(v) else False if _on(v) else None
    out = []
    for a, b, k, closed in _spans(ctx["S"], pred, gap, 1):
        if b - a < dt.timedelta(hours=hours) or b < ctx["since"]:
            continue
        out.append(_occ(ctx, "no_circulation", "pump", a, b, closed,
                        (f"The pump has not run for {_dur(b - a)}" if not closed
                         else f"The pump did not run for {_dur(b - a)}"),
                        f"Stopped from {_t(a)}"
                        + (f" to {b:%H:%M}." if closed else
                           f", still stopped at {b:%H:%M}.")
                        + f" The limit here is {hours:g} h. Check the pump's "
                          f"schedule at the panel and that its breaker is on.",
                        [("Stopped since", _t(a)), ("For", _dur(b - a))]))
    return out


def sensor_stuck(ctx, p):
    """Air or pool temperature identical across every reading for days."""
    out = []
    for col, key in (("air_temp", "air_hours"), ("pool_temp", "water_hours")):
        hours = p.get(key) or 0
        if hours <= 0:
            continue
        runs, cur = [], None
        for t, s in ctx["S"]:
            row = _flow(s)
            if col == "pool_temp" and _on(_v(row, "spa")):
                continue
            v = ctx["num"](row.get(col))
            if v is None:
                continue
            if cur and v == cur[2]:
                cur[1] = t
                cur[3] += 1
                continue
            if cur:
                runs.append((cur, True))
            cur = [t, t, v, 1]
        if cur:
            runs.append((cur, False))
        for (a, b, v, k), closed in runs:
            if b - a < dt.timedelta(hours=hours) or b < ctx["since"]:
                continue
            o = _occ(ctx, "sensor_stuck", col, a, b, closed,
                     f"{COLUMN_LABEL[col]} has read {v:g}° for {_dur(b - a)}",
                     f"{k} readings in a row, all {v:g}°, from {_t(a)} to "
                     f"{b:%H:%M}. A real {COLUMN_LABEL[col].lower()} moves; "
                     f"this is a sensor or its connection. It is the sampler "
                     f"that is wrong, not the pool.",
                     [("Value", f"{v:g}°"), ("Readings", str(k))])
            # Open while it is still the newest reading of that column.
            o["open"] = not closed
            out.append(o)
    return out


def sensor_range(ctx, p):
    """Any reading outside RANGES, flow-dependent ones only with flow."""
    out = []
    for col, (lo, hi, unit) in RANGES.items():
        bad = {}

        def pred(s, col=col, lo=lo, hi=hi):
            v = ctx["num"](_flow(s).get(col))
            if v is None:
                return None
            if not (lo <= v <= hi):
                bad[s.get("ts")] = v
                return True
            return False
        for a, b, k, closed in _spans(ctx["S"], pred, ctx["gap"], 1):
            if b < ctx["since"]:
                continue
            vals = [bad[s.get("ts")] for t, s in ctx["S"]
                    if a <= t <= b and s.get("ts") in bad]
            out.append(_occ(ctx, "sensor_range", col, a, b, closed,
                            f"{COLUMN_LABEL[col]} read {vals[-1]:g}{unit}",
                            f"Outside {lo:g}–{hi:g}{unit}, the range a working "
                            f"sensor can report, in {k} sample"
                            f"{'' if k == 1 else 's'} from {_t(a)}. The panel "
                            f"or the sensor is wrong, not the pool.",
                            [("Reading", f"{vals[-1]:g}{unit}")]))
    return out


DETECTORS = {
    "long_on": long_on, "left_on": left_on, "long_usual": long_usual,
    "heat_no_flow": heat_no_flow, "heat_no_rise": heat_no_rise,
    "spa_too_hot": spa_too_hot, "heat_past_set": heat_past_set,
    "freeze_risk": freeze_risk, "pump_power": pump_power,
    "cell_stress": cell_stress, "no_circulation": no_circulation,
    "sensor_stuck": sensor_stuck, "sensor_range": sensor_range,
}


# ================================================================ assembly
def detect(samples, now, when, num, cfg=None, salt_low=None, cleans=(),
           net=None, long=None, gap=None):
    """Every occurrence in the last LOOKBACK, open ones first, newest first.

    `samples` are the loader's rows; `when`/`num` the loader's parsers, so
    nothing here reads a file. `net` is schedule.net_lookup()'s function (or
    None, which says nothing about the household limit). `long` is
    equipment.long_runs()'s answer if the caller already has it -- render does,
    from assess() -- and is computed here otherwise. `cleans` are the filter's
    marked cleanings, which restart the pump-power baseline.
    """
    from . import schedule
    conf, _unknown = settings(cfg)
    gap = gap or GAP
    since = now - LOOKBACK
    rows = [s for s in samples or ()
            if (lambda t: t is not None and since - LEAD <= t <= now)(when(s.get("ts")))]
    S = E._sorted(rows, when)
    if not S:
        return []
    if long is None and conf["long_usual"]["enabled"]:
        long = E.long_runs(samples, now, when, cfg=cfg, gap=gap)
    ctx = {"S": S, "rows": rows, "now": now, "when": when, "num": num,
           "cfg": cfg or {}, "gap": gap, "since": since, "last_t": S[-1][0],
           "salt_low": salt_low, "cleans": cleans, "net": net, "long": long,
           "fresh": dt.timedelta(seconds=schedule.SAMPLE_FRESH_S)}
    out = []
    for name, fn in DETECTORS.items():
        p = conf[name]
        if not p.get("enabled", True):
            continue
        for o in fn(ctx, p):
            o["notify"] = bool(p.get("notify"))
            out.append(o)
    # The learned "longer than usual" says nothing the absolute limit has not
    # already said about the same run.
    absolute = {(o["subject"], o["start"]) for o in out
                if o["rule"] in ("long_on", "left_on")}
    out = [o for o in out if not (o["rule"] == "long_usual"
                                  and (o["subject"], o["start"]) in absolute)]
    out.sort(key=lambda o: (not o["open"],
                            -SEVERITY_ORDER.get(o["severity"], 0),
                            -o["start"].timestamp()))
    return out


def unrecognised(cfg=None):
    """The [anomaly] keys this module did not understand."""
    return settings(cfg)[1]


# ======================================================== what watch tells
def plan(occs, state, now, first_run=False, remind=REMIND):
    """What to say this run, as [(kind, occurrence-or-record)] -- and nothing
    is SENT here, so a case can ask what would be.

    kind is "alert", "remind" or "recover". `state` is watch's record of what
    it has already said: {key: {"told": iso or None, "open": bool, ...}}.
    Read only; watch writes it after each send, and only for a send that
    happened.

    On the first run with anomaly watching, an occurrence that has already
    ENDED is adopted silently -- a first run must not replay a week -- while
    one still open is said, because it is still true.
    """
    out = []
    by_key = {o["key"]: o for o in occs}
    for o in occs:
        if not o.get("notify") or o.get("handled"):
            continue
        rec = state.get(o["key"])
        if rec is None:
            if first_run and not o["open"]:
                continue
            out.append(("alert", o))
        elif not rec.get("told"):
            # Seen before and never delivered: try again, the watchdog's rule.
            if o["open"] or not rec.get("adopted"):
                out.append(("alert", o))
        elif (o["open"] and SEVERITY_ORDER.get(o["severity"], 0) >= 1
              and _older(rec.get("told"), now, remind)):
            out.append(("remind", o))
    for key, rec in state.items():
        if not (rec.get("told") and rec.get("open")):
            continue
        o = by_key.get(key)
        if o is None or not o["open"]:
            out.append(("recover", o or rec))
    return out


def _older(stamp, now, td):
    try:
        t = dt.datetime.strptime(str(stamp)[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return True
    return now - t > td


def record(o, told=None):
    """The state entry for an occurrence, as watch stores it."""
    return {"rule": o["rule"], "subject": o["subject"], "title": o["title"],
            "severity": o["severity"], "start": o["start"].isoformat(),
            "open": bool(o["open"]), "told": told}


def recovery_text(o):
    """(title, message) for an occurrence that has ended."""
    title = o.get("title") or RULES.get(o.get("rule"), {}).get("label", "Anomaly")
    end = o.get("end")
    msg = ("No longer seen in the panel's samples"
           + (f" after {end:%a %H:%M}." if isinstance(end, dt.datetime) else ".")
           + " " + (o.get("handled") or ""))
    return f"Ended: {title}", msg.strip()


# ================================================================ the page
def actions(occs):
    """Items for Home's "What to do next", in render.actions()'s shape.

    Open serious occurrences are "fix" -- the caller puts them first, because
    a heater with no water through it outranks any dose. Open info ones are
    "keep an eye on". Ended ones are history and live in the section below.
    """
    out = []
    for o in occs or ():
        if not o["open"]:
            continue
        serious = SEVERITY_ORDER.get(o["severity"], 0) >= 1
        out.append(dict(
            pri="fix" if serious else "watch", title=o["title"],
            detail=html.escape(o["detail"]), dose="",
            src=f"the panel's samples, as of {_t(o['as_of'])}",
            note=RULES[o["rule"]]["why"]))
    return out


def _sev_pill(sev):
    """The severity as a WORD on a tinted ground -- never the tint alone."""
    from . import mail
    word = mail.SEVERITY.get(sev, (sev,))[0]
    cls = "p-bad" if SEVERITY_ORDER.get(sev, 0) >= 1 else "p-over"
    return f'<span class="pill {cls}">{html.escape(word)}</span>'


def _item(o):
    esc = html.escape
    state = ("Now" if o["open"] else "Ended")
    when_ = (f"since {_t(o['start'])}, as of {_t(o['as_of'])}" if o["open"]
             else f"{_t(o['start'])} – {o['end']:%a %H:%M}")
    told = ""
    if o.get("handled"):
        told = f'<p class="an-d">{esc(o["handled"])}</p>'
    return (f'<li class="an-item">'
            f'<div class="an-h">{_sev_pill(o["severity"])}'
            f'<span class="pill p-unknown">{state}</span>'
            f'<b>{esc(o["title"])}</b></div>'
            f'<p class="an-when">{esc(when_)}</p>'
            f'<p class="an-d">{esc(o["detail"])}</p>{told}'
            f'<p class="an-why"><b>Why it matters ({esc(o["matters"])}):</b> '
            f'{esc(RULES[o["rule"]]["why"])}</p></li>')


def section(occs, cfg=None, has_samples=True):
    """Home's Anomalies section, or nothing on a build that has no answer."""
    if occs is None:
        return ""
    esc = html.escape
    conf, unknown = settings(cfg)
    now_ = [o for o in occs if o["open"]]
    past = [o for o in occs if not o["open"]]
    p = ['<section id="home-anomalies">\n  <h2>Anomalies</h2>\n'
         '  <p class="sub">Something wrong this hour that costs money, breaks '
         'equipment or is unsafe, read off the panel’s own samples: a heater '
         'with no water through it, a spa too hot, a freezing night with the '
         'pump stopped. Slow wear is under Equipment health below.</p>\n'
         '  <div class="card">']
    if not has_samples:
        p.append('<p class="empty">No equipment samples yet, so nothing to '
                 'judge.</p>')
    else:
        p.append(f'<h3>Open now ({len(now_)})</h3>')
        p.append(f'<ul class="an-list">{"".join(_item(o) for o in now_)}</ul>'
                 if now_ else
                 '<p class="empty">Nothing unusual in the newest samples.</p>')
        p.append(f'<h3>Ended in the last {LOOKBACK.days} days ({len(past)})</h3>')
        p.append(f'<ul class="an-list">{"".join(_item(o) for o in past)}</ul>'
                 if past else
                 f'<p class="empty">Nothing in the last {LOOKBACK.days} days.</p>')
    off = [RULES[k]["label"] for k, v in conf.items() if not v.get("enabled", True)]
    quiet = [RULES[k]["label"] for k, v in conf.items()
             if v.get("enabled", True) and not v.get("notify")]
    p.append('<p class="sub" style="margin:12px 0 0">'
             f'{len(RULES)} rules, each with its own threshold under '
             '<code>[anomaly]</code> in config.toml. Serious ones notify, once '
             'when they start and once when they end, and again each day one '
             'stays open; the rest are shown here only'
             + (f' ({esc(", ".join(quiet))})' if quiet else '')
             + '. Samples arrive every fifteen minutes, so a time here is good '
             'to fifteen minutes and anything shorter is not seen.'
             + (f' <b>Switched off in config:</b> {esc(", ".join(off))}.' if off else '')
             + (f' <b>Not understood in [anomaly], and ignored:</b> '
                f'{esc(", ".join(unknown))}.' if unknown else '')
             + '</p></div>\n</section>')
    return "".join(p)


EXTRA_CSS = """
.an-list { list-style:none; margin:6px 0 12px; padding:0; }
.an-item { border-top:1px solid var(--line); padding:10px 0; }
.an-h { display:flex; flex-wrap:wrap; align-items:center; gap:6px 8px; }
#home-anomalies .pill { text-transform:none; }
.an-when { margin:4px 0 0; font-size:12.5px; color:var(--ink3); font-variant-numeric:tabular-nums; }
.an-d { margin:4px 0 0; font-size:13.5px; line-height:1.5; }
.an-why { margin:4px 0 0; font-size:12.5px; line-height:1.45; color:var(--ink2); }
"""
