"""The agent's interlocks: what still refuses a command after a restart."""

import os
import socket
import shutil
import tempfile
import time

from .selftest import check, skipped


def _cmd(cid, device="Pool_Heater", action="set", value=90):
    return {"id": cid, "device": device, "action": action, "value": value}


def t_an_interlock_survives_the_agent_restarting():
    """A restart must not be a way to clear the gas heater's cooldown.

    MEASURED, by reading the code against its own docstring. Guard held `done`,
    `last` and `recent` in process memory while agent.py's module docstring
    said it "keeps its state in /run" and Claims said "the agent's other state
    lives in /run for the same reason". STATE_DIR was created at startup and
    nothing was ever written into it.

    update-pi.sh restarts the service on every deploy, so deploying cleared the
    300s cooldown on Pool_Heater — the only interlock between a double-press
    and short-cycling a pool heater — and forgot which command ids had already
    run, so a resend arriving across the restart would execute twice.

    A reboot still clears it, and that is deliberate rather than overlooked:
    /run is a tmpfs, nothing may touch this Pi's card, and MAX_AGE_S is 300s so
    every command from before a reboot is expired on arrival anyway.
    """
    from . import agent, commands as C
    print("\n  agent — the interlocks outlive the process")

    d = tempfile.mkdtemp()
    path = os.path.join(d, "guard.json")
    try:
        g = agent.Guard(path=path)
        cmd = _cmd("abc-1")
        g.check(cmd)                      # legal the first time
        g.record(cmd)

        check("recording an executed command writes the state file",
              os.path.exists(path), True)

        # THE RESTART. A second Guard over the same path is what systemd
        # builds after `systemctl restart`.
        g2 = agent.Guard(path=path)

        refused = ""
        try:
            g2.check(_cmd("def-2"))
        except C.Refused as e:
            refused = str(e)
        check("the heater cooldown is still held after a restart",
              "cooldown" in refused, True)

        replay = ""
        try:
            g2.check(_cmd("abc-1"))
        except C.Refused as e:
            replay = str(e)
        check("and an already-executed id is still refused as a resend",
              "resend" in replay, True)

        # NOT FOREVER. A cooldown that outlived its own duration would refuse
        # legitimate commands, which is the opposite failure.
        old = time.time() - (agent._longest_cooldown() + 60)
        g2.last = {"Pool_Heater": old}
        g2._save()
        g3 = agent.Guard(path=path)
        check("a cooldown older than itself is not restored",
              "Pool_Heater" in g3.last, False)

        # A CLOCK THAT WENT BACKWARDS leaves a timestamp in the future, and
        # `now - future` is negative, which reads as "changed ages ago".
        g3.last = {"Pool_Heater": time.time() + 3600}
        g3._save()
        g4 = agent.Guard(path=path)
        check("a timestamp from the future is dropped, not trusted",
              "Pool_Heater" in g4.last, False)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_the_agent_still_starts_when_its_state_is_unreadable():
    """Bookkeeping must not be able to stop the pool's only sampler.

    The agent is what reads the panel. A Guard that raised on a corrupt file
    would take sampling down with it to protect a cooldown, which is the wrong
    trade — so it starts empty and says so.
    """
    from . import agent
    print("\n  agent — unreadable state is loud, not fatal")

    d = tempfile.mkdtemp()
    path = os.path.join(d, "guard.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("{this is not json")
        g = agent.Guard(path=path)
        check("a corrupt state file still yields a working Guard",
              (g.done, g.last, g.recent), ({}, {}, []))

        # AND A DESTINATION IT CANNOT WRITE must not raise either -- the
        # command has already run by the time _save() is called.
        g2 = agent.Guard(path=os.path.join(d, "no", "such", "dir", "g.json"))
        g2.path = "/proc/cannot-write-here/guard.json"
        raised = False
        try:
            g2.record(_cmd("xyz-3"))
        except Exception:                                  # noqa: BLE001
            raised = True
        check("an unwritable destination does not fail the command", raised, False)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_the_watchdog_ping_means_the_link_is_alive():
    """Not "a thread ran" — that was never in doubt.

    MEASURED. After a reboot with DNS not yet up, the agent sat `active` for
    eight minutes logging "stream lost (URLError); retrying in 60s" and
    reaching nothing. `systemctl is-active` said active, so the ensure timer
    correctly did nothing: it asks whether a process exists, and the process
    was the one thing that was fine.

    A ping on a timer would have kept that agent alive indefinitely. The ping
    is withheld once the server has been silent for LINK_STALE_S, so it is a
    statement about the LINK rather than about the scheduler.
    """
    from . import agent
    print("\n  agent — the watchdog ping is about the link, not the thread")

    keep = dict(agent._LINK)
    try:
        agent.link_alive()
        age = agent.link_age()
        check("contact just now reads as fresh", age is not None and age < 2, True)

        agent._LINK["at"] = time.time() - (agent.LINK_STALE_S + 30)
        stale = agent.link_age()
        check("silence past the threshold reads as stale",
              stale > agent.LINK_STALE_S, True)

        # THE THRESHOLD HAS TO CLEAR A HEALTHY QUIET NIGHT. The server sends
        # stream heartbeats inside HEARTBEAT_TIMEOUT and reconnect backoff
        # tops out at 60s, so an idle pool must never look stale.
        check("the threshold is well past a healthy gap",
              agent.LINK_STALE_S > agent.HEARTBEAT_TIMEOUT * 2, True)
        # AND THE PING MUST BE MORE FREQUENT THAN THE UNIT'S WatchdogSec, or
        # systemd kills a healthy agent for being slow to speak.
        check("pings are far more frequent than the threshold",
              agent.WATCHDOG_PING_S * 4 < agent.LINK_STALE_S, True)
    finally:
        agent._LINK.clear()
        agent._LINK.update(keep)


def t_notifying_systemd_never_takes_the_agent_down():
    """A watchdog that can kill the pool's sampler by failing to report is
    worse than no watchdog.

    sd_notify is a no-op with no NOTIFY_SOCKET — the case when the agent is
    run by hand — and swallows socket errors, so nothing in the control path
    depends on systemd having started it.
    """
    from . import agent
    print("\n  agent — notifying systemd is never fatal")

    keep = os.environ.get("NOTIFY_SOCKET")
    try:
        os.environ.pop("NOTIFY_SOCKET", None)
        check("no socket means no-op, not an exception",
              agent.sd_notify("WATCHDOG=1"), False)

        os.environ["NOTIFY_SOCKET"] = "/nonexistent/poolhound-test.sock"
        check("an unreachable socket is reported, not raised",
              agent.sd_notify("WATCHDOG=1"), False)

        # AND IT REALLY SENDS when something is listening. Otherwise the two
        # cases above are the only ones ever exercised and the happy path is
        # untested -- which is how a watchdog comes to kill a healthy agent.
        d = tempfile.mkdtemp()
        path = os.path.join(d, "notify.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            srv.bind(path)
            srv.settimeout(3)
            os.environ["NOTIFY_SOCKET"] = path
            check("a listening socket gets the datagram",
                  agent.sd_notify("WATCHDOG=1"), True)
            check("and it is the word systemd looks for",
                  srv.recv(64), b"WATCHDOG=1")
        finally:
            srv.close()
            shutil.rmtree(d, ignore_errors=True)
    finally:
        if keep is None:
            os.environ.pop("NOTIFY_SOCKET", None)
        else:
            os.environ["NOTIFY_SOCKET"] = keep


def t_the_unit_and_the_agent_agree_about_the_watchdog():
    """WatchdogSec and NotifyAccess have to be read together.

    NotifyAccess defaults to `none` for every Type except notify, so
    WATCHDOG=1 would be received and discarded while systemd killed the agent
    every WatchdogSec for never pinging. Shipping one without the other turns
    a liveness check into a reboot loop on a gas-heater controller.
    """
    from . import agent, config
    from .checks_gates import _parse_unit
    print("\n  agent — the unit's watchdog matches the code's")

    p = os.path.join(config.root(), "deploy", "poolhound-agent.service")
    if not os.path.exists(p):
        skipped("the agent unit's watchdog", "deploy/ is not in this tree")
        return
    svc = dict(_parse_unit(p).get("Service", []))

    check("the unit asks for a watchdog", "WatchdogSec" in svc, True)
    check("and allows the main process to answer it",
          svc.get("NotifyAccess"), "main")

    secs = int("".join(c for c in svc.get("WatchdogSec", "0") if c.isdigit()))
    check("the deadline is longer than the ping interval",
          secs > agent.WATCHDOG_PING_S, True)
    # THE DEADLINE MUST NOT BE THE THING THAT DECIDES. The staleness judgement
    # lives in the agent; WatchdogSec only bounds how long after that decision
    # systemd waits. A deadline shorter than the ping interval would kill a
    # healthy agent, and one shorter than a reconnect would pre-empt the
    # agent's own retry.
    check("and longer than a single reconnect backoff", secs >= 120, True)

    # AF_UNIX, OR THE WATCHDOG KILLS THE AGENT EVERY TWO MINUTES.
    #
    # MEASURED on the first deploy of WatchdogSec. sd_notify opens an AF_UNIX
    # datagram socket; RestrictAddressFamilies allowed only AF_INET and
    # AF_INET6, so socket() raised EAFNOSUPPORT and the agent's deliberately
    # non-fatal handler swallowed it. Not one ping was sent, systemd killed
    # the process at start+120s and restarted it, and would have gone on
    # doing that forever. NRestarts went to 1 at 21:10:26 against a 21:08:15
    # start.
    #
    # Two correct behaviours composing into a restart loop, with nothing in
    # the journal admitting it: the agent logged that it was pinging and then
    # silently could not. Asking the unit for this pair together is the only
    # place the contradiction is visible.
    fams = svc.get("RestrictAddressFamilies", "")
    if "WatchdogSec" in svc and fams:
        check("a sandbox that allows the notify socket to exist at all",
              "AF_UNIX" in fams.split(), True)
