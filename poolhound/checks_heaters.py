"""The heaters: a switch of their own, and a temperature that reaches the panel.

Two defects found on 2026-10-10, each of which looked fine from this side. The
agent sent every heater temperature to AqualinkD's on/off route, which answers
anything but 0 or 1 with 400 "Invalid value" -- so no heater or freeze-
protection setpoint from poolhound could ever have been set. And a heater had
no switch here at all: "off" was the spa switched off, on an assumption no
sample ever showed. Each case below is one of those, or a way the fix for it
could go wrong while looking right.
"""
import os
import shutil
import tempfile

from .selftest import check


def t_a_heater_is_switched_by_heat_and_nothing_else_is():
    print("\n  heaters — `heat` switches a heater, and only a heater")
    from . import commands as C
    ok = C.validate(C.new_command("heat", "Spa_Heater", 1, "someone"))
    check("a heater switched on passes", (ok["device"], ok["value"]), ("Spa_Heater", 1))
    check("and reads as a sentence", C.describe(ok), "Spa heater on")
    check("the audit's spelling of it reads the same",
          C.describe(C.from_detail("Spa_Heater", "heat=1")), "Spa heater on")
    for dev, val, why in (("Spa", 1, "the spa is a switch, not a heater"),
                          ("Freeze_Protect", 1, "freeze protection has no heater switch"),
                          ("Pool_Heater", 88, "a temperature is not a switch position")):
        try:
            C.validate(C.new_command("heat", dev, val, "someone"))
            got = "accepted"
        except C.Refused:
            got = "refused"
        check(f"refused: {why}", got, "refused")
    check("a heater switched off is an off, for the scheduler",
          C.is_off({"action": "heat", "device": "Pool_Heater", "value": 0}), True)
    check("a temperature is never an off",
          C.is_off({"action": "setpoint", "device": "Pool_Heater", "value": 60}), False)


def t_a_temperature_goes_to_aqualinkds_setpoint_route():
    print("\n  heaters — a temperature reaches the panel on the route that sets one")
    from . import agent as A, commands as C
    sent = []
    real = A.aq_set
    d = tempfile.mkdtemp(prefix="poolhound-heat-")
    try:
        A.aq_set = lambda device, value, timeout=20: (sent.append((device, value)) or (200, "Ok"))
        g = A.Guard(path=os.path.join(d, "guard.json"))
        for dev, val in (("Pool_Heater", 88), ("Spa_Heater", 102), ("Freeze_Protect", 38),
                         ("SWG/Percent", 40)):
            A.execute(C.validate(C.new_command("setpoint", dev, val, "someone")), g)
        A.execute(C.validate(C.new_command("heat", "Spa_Heater", 1, "someone")), g)
    finally:
        A.aq_set = real
        shutil.rmtree(d, ignore_errors=True)
    check("heater and freeze-protection temperatures go to /setpoint",
          sent[:3], [("Pool_Heater/setpoint", 88), ("Spa_Heater/setpoint", 102),
                     ("Freeze_Protect/setpoint", 38)])
    check("the salt cell's percentage keeps its own route", sent[3], ("SWG/Percent", 40))
    check("and a heater's switch goes to its on/off route", sent[4], ("Spa_Heater", 1))


def t_a_heaters_temperature_and_switch_keep_separate_cooldowns():
    print("\n  heaters — set the temperature, then switch it on: not short-cycling")
    from . import agent as A, commands as C
    d = tempfile.mkdtemp(prefix="poolhound-heat-")
    try:
        g = A.Guard(path=os.path.join(d, "guard.json"))
        temp = C.validate(C.new_command("setpoint", "Spa_Heater", 102, "someone"))
        on = C.validate(C.new_command("heat", "Spa_Heater", 1, "someone"))
        g.check(temp)
        g.record(temp)

        def refused(cmd):
            try:
                g.check(cmd)
                return False
            except C.Refused:
                return True
        check("switching the heater on right after setting it is allowed", refused(on), False)
        g.record(on)
        off = C.validate(C.new_command("heat", "Spa_Heater", 0, "someone"))
        check("but switching it straight off again is short-cycling, and refused",
              refused(off), True)
        again = C.validate(C.new_command("setpoint", "Spa_Heater", 100, "someone"))
        check("and the temperature keeps its own cooldown", refused(again), True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_a_heaters_automatic_off_is_confirmed_by_the_panel():
    print("\n  heaters — an automatic heater-off is done when the panel says off, not never")
    import datetime as dt
    from . import schedule as S
    now = dt.datetime(2026, 10, 10, 20, 0, tzinfo=dt.timezone.utc)
    off = {"action": "heat", "device": "Spa_Heater", "value": 0}
    sent = now - dt.timedelta(minutes=10)
    on_then_off = [(now - dt.timedelta(minutes=20), {"spa_heat": "1"}),
                   (now - dt.timedelta(minutes=2), {"spa_heat": "0"})]
    check("the panel reporting the heater off confirms it",
          S.panel_agrees(off, on_then_off, after=sent), True)
    check("still reporting it on says it has not taken",
          S.panel_agrees(off, [(now - dt.timedelta(minutes=2), {"spa_heat": "1"})], after=sent), False)
    check("a report from before the off was sent says nothing yet",
          S.panel_agrees(off, [(now - dt.timedelta(minutes=12), {"spa_heat": "0"})], after=sent), None)
