"""Schedules and auto-offs: the cases where a wrong answer lights a gas heater.

Every case drives schedule.tick() -- the function the server's thread runs --
against a temporary data directory, with the command path and the notifier
replaced by recorders. Nothing here reaches a queue, an agent or a mailbox,
and nothing reads the host's zone: the calendar cases name their instants in
UTC and the schedule is interpreted in config.DEPLOY_TZ, which is the point.
"""
import datetime as dt, json, os, shutil, tempfile

from . import access, commands as C, schedule as S
from .selftest import check

UTC = dt.timezone.utc


def _u(*a):
    return dt.datetime(*a, tzinfo=UTC)


class _Rec:
    """Stands in for server.command(): records what would have been issued."""
    def __init__(self, answer=None, err=None):
        self.calls, self.answer, self.err = [], answer, err

    def __call__(self, cmd, by, cfg, off_after_h=None):
        self.calls.append({**cmd, "by": by, "off_after_h": off_after_h})
        if self.err:
            return None, self.err
        return (self.answer if self.answer is not None
                else {"id": "x", "result": {"ok": True}}), None


class _Notes:
    def __init__(self, ok=True):
        self.sent, self.ok = [], ok

    def __call__(self, **kw):
        self.sent.append(kw)
        return self.ok


def _dir():
    return tempfile.mkdtemp(prefix="ph-sched-")


def _put(d, st):
    base = S._empty()
    base.update(st)
    with open(S.path(d), "w") as f:
        json.dump(base, f)


def _get(d, now):
    return S._load(d, now)


def _sched(**kw):
    row = {"id": "s1", "action": "set", "device": "Aux_1", "value": 1,
           "repeat": "daily", "time": "19:00", "by": "someone@example.com",
           "created": "2026-01-01T00:00:00+00:00", "last_key": None,
           "off_after_h": None}
    row.update(kw)
    return row


def _run(d, start, end, step_s=60, **kw):
    """Tick every `step_s` from start to end; return the issue recorder."""
    rec = kw.pop("issue", None) or _Rec()
    t = start
    while t <= end:
        S.tick(cfg={}, now=t, data=d, issue=rec, notify=_Notes(), samples=[], **kw)
        t += dt.timedelta(seconds=step_s)
    return rec


def t_off_means_an_ordinary_command():
    """What "off" is, per device, from the shared vocabulary."""
    print("\n  schedule — what switching each thing off means")
    check("a switch is off at 0", C.off_command("Spa"),
          {"action": "set", "device": "Spa", "value": 0})
    # A heater is stopped by its OWN switch since 2026-10-10 -- the panel's
    # POOL HEATER / SPA HEATER. Before, the spa was switched off and the pool
    # heater asked for its minimum: stand-ins, the first resting on an
    # assumption ("the spa heater follows spa mode") no sample ever showed.
    check("the spa heater is stopped by its own switch",
          C.off_command("Spa_Heater"), {"action": "heat", "device": "Spa_Heater", "value": 0})
    check("and so is the pool heater, its temperature left alone",
          C.off_command("Pool_Heater"), {"action": "heat", "device": "Pool_Heater", "value": 0})
    check("a heater switched off is an off",
          C.is_off({"action": "heat", "device": "Spa_Heater", "value": 0}), True)
    check("freeze protection has no automatic off", C.off_command("Freeze_Protect"), None)
    check("nor does boost, which stops itself", C.off_command("SWG/Boost"), None)
    check("an off is an off", C.is_off({"action": "set", "device": "Spa", "value": 0}), True)
    check("an on is not", C.is_off({"action": "set", "device": "Spa", "value": 1}), False)
    check("a heater raised is not an off",
          C.is_off({"action": "setpoint", "device": "Pool_Heater", "value": 88}), False)
    # Every off command is one the agent itself accepts -- no new vocabulary.
    stamp = dt.datetime.now(UTC).isoformat()
    bad = []
    for dev in list(C.SWITCHES) + list(C.SETPOINTS):
        offc = C.off_command(dev)
        if offc is None:
            continue
        try:
            C.validate({"id": "0" * 16, "issued": stamp, **offc})
        except C.Refused as e:
            bad.append((dev, str(e)))
    check("and every one passes commands.validate()", bad, [])


def t_spring_forward_runs_once_and_late_by_the_gap():
    """02:30 does not exist on 8 March 2026 in the pool's zone."""
    print("\n  schedule — spring forward: a time that does not exist runs once")
    d = _dir()
    try:
        _put(d, {"schedules": [_sched(time="02:30")]})
        # 7 Mar 18:00 PST to 9 Mar 12:00 PDT, in UTC.
        rec = _run(d, _u(2026, 3, 8, 2), _u(2026, 3, 9, 20), step_s=60)
        check("both nights in the window run -- the short one is not skipped",
              len(rec.calls), 2)   # 8 Mar and 9 Mar inside this window
        st = _get(d, _u(2026, 3, 9, 20))
        fired = sorted(h["at"] for h in st["history"] if h["kind"] == "fired")
        check("8 March ran at 02:30 on the old offset, 03:30 on the new",
              fired[0], "2026-03-08T10:30:00+00:00")
        check("9 March at 02:30 PDT", fired[1], "2026-03-09T09:30:00+00:00")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_fall_back_runs_once_not_twice():
    """01:30 happens twice on 1 November 2026. It must run once."""
    print("\n  schedule — fall back: a time that happens twice runs once")
    d = _dir()
    try:
        _put(d, {"schedules": [_sched(time="01:30")]})
        # 1 Nov 00:00 PDT (07:00Z) to 03:00 PST (11:00Z): four real hours.
        rec = _run(d, _u(2026, 11, 1, 7), _u(2026, 11, 1, 11), step_s=60)
        check("one run through a doubled hour", len(rec.calls), 1)
        st = _get(d, _u(2026, 11, 1, 11))
        fired = [h["at"] for h in st["history"] if h["kind"] == "fired"]
        check("at the FIRST 01:30, still on daylight time",
              fired, ["2026-11-01T08:30:00+00:00"])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_an_on_missed_while_down_is_never_run_late():
    """The server was down at 19:00 and came back at 21:00."""
    print("\n  schedule — an ON past its window is recorded, not run")
    d = _dir()
    try:
        _put(d, {"schedules": [_sched(device="Spa", time="19:00")]})
        notes = _Notes()
        rec = _Rec()
        # 19:00 PDT on 9 Oct is 02:00Z on the 10th; two hours later.
        S.tick(cfg={}, now=_u(2026, 10, 10, 4), data=d, issue=rec,
               notify=notes, samples=[])
        check("nothing was sent", rec.calls, [])
        st = _get(d, _u(2026, 10, 10, 4))
        h = st["history"][0]
        check("it is on the record as missed", (h["kind"], h["outcome"]),
              ("missed", "missed"))
        check("at the ordinary severity for an ON", h["severity"], "info")
        check("and somebody was told", len(notes.sent), 1)
        check("the alert is marked delivered", h["alerted"], True)
        # And it does not come back on the next tick.
        S.tick(cfg={}, now=_u(2026, 10, 10, 4, 1), data=d, issue=rec,
               notify=notes, samples=[])
        check("the next look does not record it again",
              sum(1 for x in _get(d, _u(2026, 10, 10, 4, 1))["history"]
                  if x["kind"] == "missed"), 1)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_a_scheduled_off_missed_is_serious():
    print("\n  schedule — a scheduled OFF that was missed is the serious kind")
    d = _dir()
    try:
        _put(d, {"schedules": [_sched(device="Spa", value=0, time="19:00")]})
        notes = _Notes()
        S.tick(cfg={}, now=_u(2026, 10, 10, 4), data=d, issue=_Rec(),
               notify=notes, samples=[])
        h = _get(d, _u(2026, 10, 10, 4))["history"][0]
        check("missed, serious", (h["outcome"], h["severity"]), ("missed", "serious"))
        check("and urgent on the wire", notes.sent and notes.sent[0]["urgent"], True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_a_schedule_is_withdrawn_when_its_author_loses_operate():
    """Made by an operator, who is then dropped from [access]. A recurring
    schedule must not go on lighting the heater on their old authority."""
    print("\n  schedule — the author's authority is asked at every firing")
    policy = {"access": {"operators": ["keeper@example.com"], "default": "view"}}
    d = _dir()
    try:
        _put(d, {"schedules": [_sched(device="Spa", time="19:00",
                                      by="keeper@example.com")]})
        rec = _Rec()
        # 19:00 PDT on 9 Oct is 02:00Z on the 10th; a minute after, in the window.
        S.tick(cfg=policy, now=_u(2026, 10, 10, 2, 1), data=d, issue=rec,
               notify=_Notes(), samples=[])
        check("while they hold operate it is sent", len(rec.calls), 1)

        demoted = {"access": {"operators": [], "default": "view"}}
        notes = _Notes()
        S.tick(cfg=demoted, now=_u(2026, 10, 11, 2, 1), data=d, issue=rec,
               notify=notes, samples=[])
        check("once they do not, nothing more is sent", len(rec.calls), 1)
        st = _get(d, _u(2026, 10, 11, 2, 1))
        check("and the schedule is gone, not merely skipped", st["schedules"], [])
        h = st["history"][0]
        check("the record says withdrawn", (h["kind"], h["outcome"]),
              ("withdrawn", "withdrawn"))
        check("and names whose authority lapsed", "keeper@example.com" in h["detail"], True)
        check("somebody was told", len(notes.sent), 1)
        check("ordinary for an ON", h["severity"], "info")

        _put(d, {"schedules": [_sched(device="Spa", value=0, time="19:00",
                                      by="keeper@example.com")]})
        notes = _Notes()
        S.tick(cfg=demoted, now=_u(2026, 10, 12, 2, 1), data=d, issue=rec,
               notify=notes, samples=[])
        h = _get(d, _u(2026, 10, 12, 2, 1))["history"][0]
        check("a withdrawn OFF is the serious kind — something will not stop",
              (h["kind"], h["severity"], notes.sent and notes.sent[0]["urgent"]),
              ("withdrawn", "serious", True))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_inside_the_grace_window_it_runs_and_says_how_late():
    print("\n  schedule — a few minutes late is still on time")
    d = _dir()
    try:
        _put(d, {"schedules": [_sched(device="Spa", time="19:00", off_after_h=2)]})
        rec = _Rec()
        S.tick(cfg={}, now=_u(2026, 10, 10, 2, 10), data=d, issue=rec,
               notify=_Notes(), samples=[])
        check("sent once", len(rec.calls), 1)
        check("as the scheduler, not as whoever set it", rec.calls[0]["by"],
              access.SCHEDULER)
        check("carrying its off-after to the command path",
              rec.calls[0]["off_after_h"], 2)
        h = _get(d, _u(2026, 10, 10, 2, 10))["history"][0]
        check("and the record says it was ten minutes late",
              h["detail"].endswith("10 min late"), True)
        S.tick(cfg={}, now=_u(2026, 10, 10, 2, 11), data=d, issue=rec,
               notify=_Notes(), samples=[])
        check("and does not run twice", len(rec.calls), 1)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_off_after_is_arranged_replaced_and_retired():
    print("\n  schedule — off-after: set by an ON, replaced by an ON, retired by an OFF")
    d = _dir()
    t0 = _u(2026, 10, 10, 1)
    try:
        on = {"action": "set", "device": "Spa", "value": 1}
        S.note_command(on, "someone@example.com", 2, cfg={}, data=d, now=t0)
        offs = _get(d, t0)["autooffs"]
        check("one auto-off", len(offs), 1)
        check("due two hours later", offs[0]["due"], "2026-10-10T03:00:00+00:00")
        check("to switch the spa off", offs[0]["cmd"], C.off_command("Spa"))
        S.note_command(on, "someone@example.com", 3, cfg={}, data=d,
                       now=t0 + dt.timedelta(hours=1))
        offs = _get(d, t0)["autooffs"]
        check("a second ON replaces it rather than adding one",
              [o["due"] for o in offs], ["2026-10-10T05:00:00+00:00"])
        S.note_command({"action": "set", "device": "Spa", "value": 0},
                       "someone@example.com", None, cfg={}, data=d,
                       now=t0 + dt.timedelta(hours=1, minutes=5))
        check("an OFF makes it moot", _get(d, t0)["autooffs"], [])
        # Nothing to switch off afterwards is refused, not silently ignored.
        check("off-after on an OFF is refused",
              S.check_off_after({"action": "set", "device": "Spa", "value": 0,
                                 "off_after_h": 2})[1] is not None, True)
        check("an hour count nobody offered is refused",
              S.check_off_after({"action": "set", "device": "Spa", "value": 1,
                                 "off_after_h": 99})[1] is not None, True)
        check("a device not configured for it is refused",
              S.check_off_after({"action": "set", "device": "Aux_2", "value": 1,
                                 "off_after_h": 2}, {})[1] is not None, True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_an_auto_off_fires_as_auto_off_and_stands_down_if_already_off():
    print("\n  schedule — an auto-off is an ordinary off, and not sent to something off")
    d = _dir()
    t0 = _u(2026, 10, 10, 1)
    try:
        S.note_command({"action": "set", "device": "Spa", "value": 1},
                       "someone@example.com", 1, cfg={}, data=d, now=t0)
        rec = _Rec()
        S.tick(cfg={}, now=t0 + dt.timedelta(minutes=30), data=d, issue=rec,
               notify=_Notes(), samples=[])
        check("not before it is due", rec.calls, [])
        S.tick(cfg={}, now=t0 + dt.timedelta(hours=1), data=d, issue=rec,
               notify=_Notes(), samples=[])
        check("then the spa off, by auto-off",
              [(c["device"], c["value"], c["by"]) for c in rec.calls],
              [("Spa", 0, access.AUTO_OFF)])
        # The panel confirms it on a later sample: the entry is done.
        later = t0 + dt.timedelta(hours=1, minutes=1)
        sample = (later, {"ts": "", "spa": "0"})
        S.tick(cfg={}, now=later + dt.timedelta(minutes=10), data=d, issue=rec,
               notify=_Notes(), samples=[sample])
        check("and once the panel reports it off, it is finished",
              _get(d, later)["autooffs"], [])
        # A second spa session, switched off by hand before the auto-off:
        d2 = _dir()
        S.note_command({"action": "set", "device": "Spa", "value": 1},
                       "someone@example.com", 1, cfg={}, data=d2, now=t0)
        rec2 = _Rec()
        off_seen = (t0 + dt.timedelta(minutes=40), {"ts": "", "spa": "0"})
        S.tick(cfg={}, now=t0 + dt.timedelta(hours=1), data=d2, issue=rec2,
               notify=_Notes(), samples=[off_seen])
        check("something already off is not sent an off", rec2.calls, [])
        check("and the record says why",
              _get(d2, t0 + dt.timedelta(hours=1))["history"][0]["detail"],
              "already off — nothing sent")
        shutil.rmtree(d2, ignore_errors=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _spa_heater_on(now, hours):
    """Samples every 15 minutes with the spa heater reported on for `hours`."""
    out = [(now - dt.timedelta(hours=hours, minutes=15), {"spa_heat": "0", "spa": "0"})]
    t = now - dt.timedelta(hours=hours)
    while t <= now - dt.timedelta(minutes=2):
        out.append((t, {"spa_heat": "1", "spa": "1"}))
        t += dt.timedelta(minutes=15)
    return out


def t_the_max_on_net_catches_a_press_poolhound_never_saw():
    """The heater was started at the wall panel. The net reads the panel."""
    print("\n  schedule — the household max-on applies to every press")
    d = _dir()
    now = _u(2026, 10, 10, 6)
    try:
        rec = _Rec()
        S.tick(cfg={}, now=now, data=d, issue=rec, notify=_Notes(),
               samples=_spa_heater_on(now, 2.5))
        check("under the limit, nothing", rec.calls, [])
        S.tick(cfg={}, now=now, data=d, issue=rec, notify=_Notes(),
               samples=_spa_heater_on(now, 3.25))
        check("past three hours, the spa heater is switched off by auto-off",
              [(c["device"], c["action"], c["value"], c["by"]) for c in rec.calls],
              [("Spa_Heater", "heat", 0, access.AUTO_OFF)])
        st = _get(d, now)
        check("as the household limit, which cannot be cancelled",
              S.cancel({"id": st["autooffs"][0]["id"]}, "someone@example.com",
                       data=d, now=now)[1] is not None, True)
        S.tick(cfg={}, now=now + dt.timedelta(minutes=1), data=d, issue=rec,
               notify=_Notes(), samples=_spa_heater_on(now, 3.25))
        check("once per run: the same run does not make another",
              sum(1 for o in _get(d, now)["autooffs"] if o["cause"] == "max-on"), 1)
        check("a stale sample says nothing about now",
              S.on_since("Spa_Heater", _spa_heater_on(now, 4),
                         now + dt.timedelta(hours=2)), None)
        check("configured off, it does nothing",
              S.max_on_hours({"schedule": {"max_on": {"Spa_Heater": 0}}}), {})
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_no_agent_means_missed_and_an_alert_not_silence():
    print("\n  schedule — no agent connected: recorded, alerted, retried if it was an off")
    d = _dir()
    t0 = _u(2026, 10, 10, 1)
    try:
        S.note_command({"action": "set", "device": "Spa", "value": 1},
                       "someone@example.com", 1, cfg={}, data=d, now=t0)
        _put_sched = _get(d, t0)
        _put_sched["schedules"].append(_sched(id="s2", repeat="once", device="Aux_1",
                                              at="2026-10-10T02:00:00+00:00"))
        S._save(d, _put_sched)
        rec = _Rec(err="no agent is connected, so nothing can reach the panel.")
        notes = _Notes()
        now = t0 + dt.timedelta(hours=1)
        S.tick(cfg={}, now=now, data=d, issue=rec, notify=notes, samples=[])
        st = _get(d, now)
        hist = {h["what"]: h for h in st["history"] if h["kind"] in ("missed", "auto-off")}
        check("the ON is missed, at the ordinary severity",
              (hist["Pool light on"]["outcome"], hist["Pool light on"]["severity"]),
              ("not delivered", "info"))
        check("the OFF is serious", hist["Spa off"]["severity"], "serious")
        check("and still waiting to be retried",
              [o["attempts"] for o in st["autooffs"]], [1])
        check("both were alerted, the off as urgent",
              sorted((n["severity"], n["urgent"]) for n in notes.sent),
              [("info", False), ("serious", True)])
        # The agent comes back: the retry goes, and is not alerted twice.
        rec.err = None
        notes2 = _Notes()
        S.tick(cfg={}, now=now + dt.timedelta(minutes=5), data=d, issue=rec,
               notify=notes2, samples=[])
        check("the retry is sent when the agent returns",
              [c["device"] for c in rec.calls][-1], "Spa")
        check("without a second alert", notes2.sent, [])
        # An undelivered alert stays unmarked for the watchdog to retry.
        d3 = _dir()
        S.note_command({"action": "set", "device": "Spa", "value": 1},
                       "someone@example.com", 1, cfg={}, data=d3, now=t0)
        S.tick(cfg={}, now=now, data=d3, issue=_Rec(err="no agent"),
               notify=_Notes(ok=False), samples=[])
        check("an alert nobody could deliver is left for bin/watch",
              len(S.unalerted(data=d3, now=now + dt.timedelta(minutes=11))), 1)
        shutil.rmtree(d3, ignore_errors=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_schedules_survive_a_restart():
    print("\n  schedule — on disk, so a recreated container still has them")
    d = _dir()
    now = _u(2026, 10, 9, 18)
    try:
        row, err = S.create({"action": "setpoint", "device": "Spa_Heater", "value": 102,
                             "repeat": "weekdays", "time": "18:30", "off_after_h": 2},
                            "someone@example.com", cfg={}, data=d, now=now)
        check("created", err, None)
        again = _get(d, now)["schedules"]
        check("read back whole after the process that wrote it is gone",
              [(s["device"], s["value"], s["repeat"], s["time"], s["off_after_h"])
               for s in again],
              [("Spa_Heater", 102, "weekdays", "18:30", 2.0)])
        check("its next run is this evening at the pool, a Friday",
              S.next_due(again[0], now).isoformat(), "2026-10-10T01:30:00+00:00")
        check("and after that Monday, not the weekend",
              S.next_due(again[0], _u(2026, 10, 10, 2)).isoformat(),
              "2026-10-13T01:30:00+00:00")
        check("a value the agent would refuse is refused now, not at 18:30",
              S.create({"action": "setpoint", "device": "Spa_Heater", "value": 140,
                        "repeat": "daily", "time": "18:30"}, "x", cfg={}, data=d,
                       now=now)[1] is not None, True)
        check("a one-off in the past is refused",
              S.create({"action": "set", "device": "Spa", "value": 1, "repeat": "once",
                        "at": "2026-10-09T08:00"}, "x", cfg={}, data=d, now=now)[1]
              is not None, True)
        got, err = S.cancel({"id": row["id"]}, "someone@example.com", data=d, now=now)
        check("and cancelled", (err, _get(d, now)["schedules"]), (None, []))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_a_stamp_from_the_future_is_not_believed():
    print("\n  schedule — a clock that went backwards")
    d = _dir()
    now = _u(2026, 10, 10, 1)
    try:
        _put(d, {"alive_at": "2027-01-01T00:00:00+00:00",
                 "schedules": [_sched(created="2027-01-01T00:00:00+00:00")],
                 "history": [{"id": "h", "at": "2027-01-01T00:00:00+00:00",
                              "kind": "fired"}],
                 "autooffs": [{"id": "a", "device": "Spa", "cmd": C.off_command("Spa"),
                               "due": "2027-01-01T00:00:00+00:00", "cause": "off-after",
                               "attempts": 0}]})
        st = _get(d, now)
        check("the liveness stamp is dropped", st["alive_at"], None)
        check("a creation time is brought back to now",
              st["schedules"][0]["created"], S._iso(now))
        check("a history row from the future is dropped", st["history"], [])
        check("an auto-off due beyond any possible off-after is due NOW, not never",
              st["autooffs"][0]["due"], S._iso(now))
        check("an alive stamp a different process can read",
              S.alive_at(data=d, now=now), None)
        # A file nobody can parse is not an empty schedule.
        with open(S.path(d), "w") as f:
            f.write("{not json")
        try:
            S.tick(cfg={}, now=now, data=d, issue=_Rec(), notify=_Notes(), samples=[])
            raised = False
        except RuntimeError:
            raised = True
        check("an unreadable file stops the look, loudly", raised, True)
        check("and is left in place for a person", open(S.path(d)).read(), "{not json")
        check("and reads as a stopped scheduler to the watchdog",
              S.alive_at(data=d, now=now).year, 1970)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_a_command_goes_one_way_whoever_sends_it():
    """The scheduler's default sender IS server.command, which /api/control uses."""
    print("\n  schedule — one command path")
    import inspect
    from . import server
    check("the scheduler sends through server.command",
          "server.command(" in inspect.getsource(S._issue_default), True)
    check("and /api/control does too",
          "command(data, cfg, self.whoami())"
          in inspect.getsource(server.Handler._dispatch_write), True)
    r, err = server.command({"action": "set", "device": "Spa", "value": 1,
                             "off_after_h": 7}, {}, "someone@example.com")
    check("a bad off-after refuses the press before anything is issued",
          (r, err is not None and "off after" in err), (None, True))
    check("a machine name arriving as an identity is never more than view",
          [access.role(m, {}) for m in access.MACHINES], ["view", "view"])
    check("creating needs what pressing needs",
          access.NEEDS["/api/schedule"], access.NEEDS["/api/control"])
    check("and anybody who can see the controls can see the list",
          access.NEEDS["/api/schedules"], access.NEEDS["/api/commands"])
