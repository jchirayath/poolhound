#!/usr/bin/env python3
"""Notice when a collector has stopped, and say so out loud.

WHY THIS WATCHES THE DATA, NOT THE JOBS
  "Did the job run?" is the wrong question. It has three different failure modes
  and only one of them is visible to launchd:

    the job never fired            — the Mac was off, or the job was unloaded
    the job fired and failed       — the Pi unreachable, credentials expired,
                                     the vendor changed their API
    the job fired and succeeded    — but the pod itself stopped measuring, so
                                     the newest reading is still last week's

  Only the second shows up as a non-zero exit code. All three produce exactly
  one symptom, which is that the newest row is older than it should be — so that
  is what is checked. The job's exit code is read too, but as corroboration for
  the message rather than as the trigger.

THE SLEEPING-MAC PROBLEM
  This machine sleeps. When it does, the sampler stops as surely as if the Pi
  had died, and a watchdog that only compared timestamps would announce a fault
  every morning. That is worse than no watchdog: an alarm that is usually wrong
  gets dismissed without being read, including on the day it is right.

  So the watchdog keeps its own heartbeat. If its previous run is also long ago,
  the machine was not awake, and the collectors are given a grace period to
  catch up before anything is said. The test is self-referential and needs no
  power-management API: whatever stopped the watchdog stopped the collectors
  too.

NOT SAYING IT TWICE
  A fault that persists for a week must not produce a notification every half
  hour. Each source's state is remembered, and a notification is sent when the
  state CHANGES — including back to healthy, because "the Pi is reporting again"
  is the message that closes the loop and stops someone chasing a fixed problem.
  A standing fault is repeated once a day, so it is not forgotten either.
"""
import datetime as dt, json, os, re, shutil, subprocess, sys

from . import config
from . import locking
from .render import rows, collector_health, ago, num, best_lab, targets, when, newest

# Sources worth waking someone for. Leslie's is deliberately absent: it only
# updates when a sample is physically carried into a store, so its silence is
# never a fault and alerting on it would train the alert to be ignored.
# Weather is absent for a different reason with the same answer: nothing is
# lost while it is quiet, because the next run that works asks the archive for
# every day still missing, so its silence is a delay and never a gap. The
# Collection card grades it late after two missed days, which is the place a
# delay belongs; a 3 a.m. email about yesterday's sunshine is not.
ALERT_ON = {"sample", "waterguru"}

# Equipment worth being told about the moment it starts. The gas heaters are on
# by default because they cost money and because a heater running when nobody
# asked it to is the kind of fault that shows up on a bill rather than on a
# dashboard. Solar is off by default: it is free, it comes on with the sun, and
# a notification every sunny afternoon is one nobody will keep reading.
EQUIPMENT = {
    "pool_heat":  {"label": "Pool heater",  "default": True,
                   "why": "Gas heating is the most expensive thing this pool can do, "
                          "and warmer water also raises pH and burns chlorine faster."},
    "spa_heat":   {"label": "Spa heater",   "default": True,
                   "why": "Gas heating is expensive, and the spa runs a higher cell "
                          "output, so a long session moves the chemistry."},
    "spa":        {"label": "Spa",          "default": False,
                   "why": "Spa mode aerates hard and drives the cell higher."},
    # On by default, and asked for. Freeze protection is the panel acting on its
    # own: it runs the pump because the air is cold enough to risk ice in the
    # plumbing, at whatever hour that happens. It is the one piece of equipment
    # here nobody scheduled and nobody pressed a button for, which makes "it is
    # running now" genuinely news rather than a log line -- and if it is running
    # in daylight in summer, something is wrong with the sensor or the setpoint.
    "freeze":     {"label": "Freeze protection", "default": True,
                   "why": "The panel started the pump on its own because the air is "
                          "cold enough to risk freezing the plumbing. Nothing is "
                          "wrong, but it runs until the air warms up, so it can "
                          "add hours the schedule did not ask for."},
}

# "Way out" is not the same as "outside the band". The dashboard already says
# when a number is off target; waking somebody up should be reserved for the
# cases where waiting until tomorrow actually costs something.
def chemistry_alarms(latest, lab, TG):
    """Conditions worth interrupting someone for, and why each one is urgent.

    Every threshold here is chosen for consequence, not for distance from
    target. A pH of 7.7 is off and can wait; a pH of 8.1 has cut the working
    sanitiser to a fifth while the water is also depositing scale. The point of
    a separate severity level is that an alarm which fires for ordinary drift is
    an alarm that gets dismissed unread.
    """
    out = []
    fc = num(latest.get("free_cl"))
    ph = num(latest.get("ph"))
    salt = (lab.get("salt") or {}).get("value")
    status = (latest.get("status") or "").upper()

    if fc is not None:
        floor = TG["free_cl"][0]
        if fc <= 0.3:
            out.append(("free_cl", "critical", f"Free chlorine is {fc:.1f} ppm",
                        "The pool is effectively unsanitised. Algae can establish in a "
                        "day at this temperature, and clearing it costs far more than "
                        "preventing it. Add liquid chlorine now and find out why the "
                        "cell stopped."))
        elif fc < floor * 0.5:
            out.append(("free_cl", "serious", f"Free chlorine is {fc:.1f} ppm, "
                        f"against a floor of {floor:.1f}",
                        "Below half the minimum for this pool's cyanuric acid, which is "
                        "the level at which chlorine stops keeping up rather than merely "
                        "running low."))
        elif fc > TG["free_cl"][2] * 2:
            out.append(("free_cl", "serious", f"Free chlorine is {fc:.1f} ppm",
                        "More than double the ceiling. Not dangerous to swimmers at this "
                        "level, but the cell is working far harder than the pool needs "
                        "and it is bleaching liners and swimwear."))

    if ph is not None:
        if ph >= 8.0:
            out.append(("ph", "serious", f"pH is {ph:.2f}",
                        "At this pH only about a fifth of the free chlorine is in the "
                        "active form — the test still reads the same number while most "
                        "of it has stopped working. Scale also starts forming on the "
                        "cell plates, which cuts output and compounds it."))
        elif ph <= 7.0:
            out.append(("ph", "serious", f"pH is {ph:.2f}",
                        "Aggressive water. It will take calcium out of plaster, grout "
                        "and any exposed concrete, and that damage does not reverse when "
                        "the number is fixed."))

    if salt is not None and salt < TG["salt"][0] * 0.85:
        out.append(("salt", "serious", f"Salt is {salt:.0f} ppm",
                    "Well below the cell's working range. Most cells throttle back or "
                    "stop entirely rather than run dry, so this usually turns into a "
                    "chlorine problem within a couple of days."))

    if status == "RED":
        out.append(("status", "serious", "WaterGuru is reporting RED",
                    "The vendor's own assessment of this reading is its most severe. "
                    + (latest.get("alerts") or "")))
    return out

LABELS = {"sample": "Pool controller (Pi)", "waterguru": "WaterGuru"}
JOBS   = {"sample": "net.aspl.poolhound-sample",
          "waterguru": "net.aspl.poolhound-waterguru"}

# If our own previous run is older than this, the machine was asleep or off.
HEARTBEAT_GAP = dt.timedelta(minutes=45)
# How long the collectors get to catch up after the machine wakes.
GRACE = dt.timedelta(minutes=40)
# A fault that has not changed is still repeated this often, so it is not lost.
REMIND_EVERY = dt.timedelta(hours=24)

def equipment_events(samples, after_ts=None):
    """Find where a piece of equipment started or stopped since we last looked.

    Edges are read from the sample stream rather than from whatever happens to
    be true right now. The sampler runs every fifteen minutes and this every
    thirty, so a heater that came on and went off between two checks would be
    invisible to a poll of current state — and a short unexplained heater cycle
    is exactly the kind of thing worth seeing.

    Only samples after the last one already examined are considered, so nothing
    is reported twice and a first run does not replay the whole history.
    """
    rows_ = [r for r in samples if when(r.get("ts"))]
    rows_.sort(key=lambda r: when(r["ts"]))
    events, last_seen = [], None
    for key in EQUIPMENT:
        prev, prev_t = None, None
        for r in rows_:
            ts = when(r["ts"])
            v = r.get(key)
            if v not in ("0", "1"):
                continue
            v = v == "1"
            if prev is not None and v != prev:
                # Only report edges we have not already reported.
                if after_ts is None or ts > after_ts:
                    ev = {"key": key, "on": v, "at": ts}
                    if not v and prev_t:
                        ev["ran_minutes"] = (ts - prev_t).total_seconds() / 60.0
                    events.append(ev)
                if v:
                    prev_t = ts
            elif prev is None and v:
                prev_t = ts
            prev = v
    for r in rows_:
        ts = when(r["ts"])
        if ts and (last_seen is None or ts > last_seen):
            last_seen = ts
    events.sort(key=lambda e: e["at"])
    return events, last_seen

def state_path(cfg):
    return os.path.join(config.data_dir(cfg), ".watch-state.json")

def load_state(cfg):
    try:
        with open(state_path(cfg)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

def save_state(cfg, st):
    """The "already told you" record, written atomically.

    Same fixed-".tmp" defect as pool_shape.save(), and the consequence here is
    worse than a lost file: this state is what stops the notifier mailing the
    same alert every thirty minutes. A truncated read falls back to "nothing has
    been sent", so a corrupted write turns into a repeat alarm at 02:00 -- and
    the watchdog runs on a timer, so it races itself whenever one run overruns.
    """
    locking.replace_atomically(state_path(cfg), lambda f: json.dump(st, f, indent=1))

def last_exit(job):
    """The launchd exit code, for the message only. A collector that cannot
    reach the Pi exits 1, and saying so turns 'no data' into 'no data, and the
    sampler is reporting an error', which points at a different fix.

    macOS only, and that is now stated rather than discovered. On the server this runs
    inside python:3.12-slim where there is no launchctl and the job names are
    launchd labels for a machine that stopped being the system on 2026-09-12 --
    so the subprocess raised FileNotFoundError, the except swallowed it, and the
    answer was always None. Returning None is still right there; spending a
    process launch per check to arrive at it is not.
    """
    if not shutil.which("launchctl"):
        return None
    try:
        r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{job}"],
                           capture_output=True, text=True, timeout=10)
        for line in r.stdout.splitlines():
            if "last exit code" in line:
                return line.split("=")[-1].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None

# Severity ordering, so email can be held to a higher bar than the desktop.
SEVERITY = {"info": 0, "serious": 1, "critical": 2}

def recipients(cfg):
    """Who gets the email, as a list.

    Comma- or space-separated in one field, because the alternative is a
    repeating form control for something that is nearly always one address and
    occasionally two. Deduplicated with order kept, so pasting a list twice does
    not send everybody two copies.
    """
    raw = (cfg.get("notify", {}).get("email_to") or "")
    out, seen = [], set()
    for part in re.split(r"[,;\s]+", raw):
        a = part.strip()
        if a and "@" in a and a.lower() not in seen:
            seen.add(a.lower()); out.append(a)
    return out

def smtp_credentials(path):
    """Username and password for the mail server, or (None, None) if it wants none.

    From the encrypted vault or Key Vault where one exists, otherwise the
    plaintext file the config points at. A Gmail account needs an APP PASSWORD
    rather than the account password — Google rejects the latter outright.

    Returning "no credentials, and that is fine" is a real answer, not a
    failure: a relay that trusts this host by its network address — which is how
    the server's postfix is configured for the docker bridge — wants no AUTH at all,
    and offering it one is an error rather than a courtesy.
    """
    from . import vault
    try:
        user, pw, src = vault.credentials_for("smtp", path or None)
    except vault.VaultError as e:
        return None, str(e)
    if user and pw:
        return (user, pw), None
    return (None, None), None

def resolved_sender(cfg):
    """The From address this install will actually use, or "" if it has none.

    One function, because the test email used to DESCRIBE where the address
    comes from ("the SMTP username") while send_email derived a different one.
    A test whose job is to show what a real alert looks like has to ask the
    same question the real alert asks.
    """
    n = (cfg.get("notify") or {})
    if n.get("email_from"):
        return n["email_from"]
    try:
        from . import vault
        creds = vault.credentials_for("smtp") or ("", "", None)
        return creds[0] or ""
    except Exception:
        return ""

def send_email(cfg, alert, urgent=None):
    """One message, over STARTTLS.

    A desktop banner is glanced at while standing next to the pool; an email is
    read on a phone somewhere else, so it carries the numbers and the reasoning
    rather than a headline, and it has to survive being read in whatever client
    happens to open it. That last clause is why it is multipart rather than the
    HTML some of this used to be assembled into: `mail.text()` is a first-class
    rendering, not a fallback, and `mail.build()` puts it FIRST in the
    multipart so a client that prefers plain gets the whole alert.

    `alert` is a mail.Alert. This took (subject, body) strings, which is the
    form that made the second rendering impossible -- see mail.py.
    """
    from . import mail
    import smtplib, ssl

    n = cfg.get("notify", {})
    to = recipients(cfg)
    if not to:
        return False, "no notification address configured — add one on the Settings tab"
    # vault.py owns where a service's credential file lives. This default was
    # typed here, and the WRITER of the same file looked it up under a
    # different key entirely -- one fact, three spellings.
    from . import vault as _v
    creds, err = smtp_credentials(n.get("smtp_credentials")
                                  or _v.credential_file("smtp", cfg))
    if err:
        return False, err
    # WHO THIS IS FROM IS A SETTING, NOT A GUESS — and it must never be somebody
    # else's domain.
    #
    # The fallback here was f"poolhound@{n.get('mail_domain', 'example.com')}", so
    # any install with no email_from and a relay that needs no sign-in — the
    # exact combination the Settings form invites, "blank if the relay needs no
    # sign-in" — sent every alert as poolhound@example.com, which is this author's
    # domain and not the operator's. Verified on the wire against a local
    # listener: `mail from:<poolhound@example.com>` on a config that mentions no
    # domain at all. It fails SPF and DKIM at any real relay, which turns every
    # future alert into a silently spam-filed one: the precise failure the
    # notifications feature exists to prevent.
    #
    # `mail_domain` was the documented remedy and existed only as a comment in
    # config.example.toml — absent from server.SETTABLE, so the one screen that
    # configures mail could not set the one field that was wrong.
    #
    # Refusing is the honest answer. A message with no From is rejected by the
    # relay anyway; the difference is that this names the setting to fill in
    # rather than surfacing as an SMTP error.
    sender = resolved_sender(cfg)
    if not sender:
        return False, ("no From address: set 'email_from' on Settings. The relay "
                       "needs one, and there is no sign-in to borrow a username "
                       "from.")

    if urgent is not None:
        alert.urgent = urgent
    if alert.site is None:
        alert.site = (cfg.get("site", {}) or {}).get("host") or None
    msg = mail.build(alert, sender, to)

    host = n.get("smtp_host", "smtp.gmail.com")
    port = int(n.get("smtp_port", 587))
    # STARTTLS by default and off only if explicitly disabled — a relay on this
    # machine or on the LAN may not offer it, and refusing to send at all would
    # be worse than sending over a link that never leaves the house. Anything
    # reaching the internet must keep it on.
    use_tls = n.get("smtp_tls", True)
    try:
        with smtplib.SMTP(host, port, timeout=30) as s:
            if use_tls:
                # An in-stack relay on a private bridge may not advertise
                # STARTTLS at all. Refusing to send in that case would be worse
                # than sending over a link that never leaves the host, so this
                # downgrades rather than failing — but ONLY when the server
                # genuinely does not offer it, never on a handshake failure,
                # which is what a downgrade attack looks like.
                if s.has_extn("starttls"):
                    s.starttls(context=ssl.create_default_context())
                    s.ehlo()
            if creds[0] and creds[1]:
                s.login(creds[0], creds[1])
            s.send_message(msg)
        return True, None
    except Exception as e:
        # The exception text can carry the server's rejection reason, which is
        # usually the actionable part ("application-specific password required").
        return False, f"{type(e).__name__}: {e}"

def site_url(cfg=None, anchor="#home"):
    """Where to send somebody who is reading this on a phone.

    Every alert used to carry http://127.0.0.1:8787 -- the loopback address of
    the machine that SENT it. On a workstation that is merely useless in an
    email; on the server it is a port inside a container that nothing outside can
    reach, and the notification's one call to action was a dead link. site.host
    exists for this and nothing used it.

    Falls back to loopback only when no host is configured, which is the
    workstation case where loopback is the right answer.

    AND IT POINTS AT THE BUILD THAT HAS THE TAB. Every alert links to the tab
    named in `anchor`, and all of them name private ones -- these mails go to
    the people who own the pool, about their pool. The bare domain serves the
    PUBLIC build, which does not contain those tabs at all, so the link would
    have opened the landing page with the alert's anchor quietly discarded.
    Asking for the authenticated build instead means the proxy takes them
    through sign-in and lands them where the mail said.
    """
    from . import render
    host = str(((cfg or {}).get("site") or {}).get("host") or "").strip()
    page = "admin.html" if anchor.lstrip("#") in render.PRIVATE_TABS else ""
    if not host:
        return f"http://127.0.0.1:8787/{page}{anchor}"
    if not host.startswith(("http://", "https://")):
        host = "https://" + host
    return host.rstrip("/") + "/" + page + anchor

def notifications_disabled():
    """Is every channel switched off for this process?

    Its own function rather than an inline environ check so that it is a seam:
    a check can prove the switch works by turning it off, which an `if
    os.environ.get(...)` buried in notify() does not allow without patching the
    environment of the whole test run.

    Read at send time, not at import, so a process that sets it after start is
    still covered.
    """
    return bool(os.environ.get("POOLHOUND_NO_NOTIFY"))


def notify(title, message, subtitle="", url=None, urgent=False,
           cfg=None, severity="info", facts=()):
    """Raise a notification on every channel that is switched on.

    Desktop and email are not the same medium and should not carry the same
    traffic. A banner is free to glance past; an email arrives on a phone and
    competes with everything else there, so it defaults to a higher severity bar.
    Both are driven from this one call so a new kind of alert cannot reach one
    channel and quietly miss the other.
    """
    # A KILL SWITCH THAT NO CALLER CAN GET PAST.
    #
    # bin/selftest runs bin/watch for real — it has to, because the watchdog
    # once died with a TypeError on every run and nothing noticed, so the case
    # that matters is "did it complete". It ran it against a fixture config
    # with no [notify] section, and `desktop` defaults to TRUE, so on a Mac
    # every run of the test suite raised a real notification on a real desk
    # about the chlorine and pH of a pool that does not exist. The suite was
    # run twenty-five times in one afternoon.
    #
    # Checked HERE rather than in the test's fixture, because the fixture is a
    # thing each caller has to remember and this is a thing no caller can
    # forget. It is read at the moment of sending rather than at import, so a
    # process that sets it after start is still covered.
    if notifications_disabled():
        return False

    n = (cfg or {}).get("notify", {})
    ok = False

    if n.get("email", False):
        bar = SEVERITY.get(n.get("email_min_severity", "serious"), 1)
        if SEVERITY.get(severity, 0) >= bar:
            # STRUCTURED, not joined here. The old join filtered on
            # `is not None` against values that are "" when absent, so it
            # removed nothing: an alert with no subtitle and no link opened
            # with two blank lines and carried a gap where the link was not.
            # And it signed off with os.uname().nodename -- somebody's laptop
            # on a workstation, a fresh random container id on the server.
            from . import mail
            # The subtitle is the one-line summary under the title on a desktop
            # banner; in an email the equivalent slot is the first paragraph, so
            # it leads the detail rather than being dropped. It must not restate
            # the severity -- the chip already carries that word, and the
            # chemistry alarm used to pass sev.capitalize() here, which in an
            # email renders "Critical" twice, once as a badge and once as a
            # sentence that is not one.
            lead = (subtitle or "").strip()
            if lead.lower() in {s[0].lower() for s in mail.SEVERITY.values()}:
                lead = ""
            detail = f"{lead}\n\n{message}" if lead else message
            sent_ok, err = send_email(cfg or {}, mail.Alert(
                subject=title, headline=title, detail=detail,
                facts=facts, url=url, severity=severity, urgent=urgent))
            ok = ok or sent_ok
            if err:
                print(f"  email failed: {err}", file=sys.stderr)

    if not n.get("desktop", True):
        return ok
    return _desktop(title, message, subtitle, url, urgent) or ok

def _desktop(title, message, subtitle="", url=None, urgent=False):
    """Send a desktop notification.

    terminal-notifier is preferred only because its notifications can carry a
    click target — landing on the dashboard is the first thing anyone will want
    to do. osascript is the fallback and is always present on macOS.
    """
    if shutil.which("terminal-notifier"):
        cmd = ["terminal-notifier", "-title", title, "-message", message,
               "-group", "poolhound"]
        if subtitle: cmd += ["-subtitle", subtitle]
        if url: cmd += ["-open", url]
        if urgent: cmd += ["-sound", "Basso"]
        try:
            # returncode is the point: terminal-notifier and osascript both exist
            # and still fail — notifications denied for the terminal, no GUI
            # session, a launchd context with no Aqua access. Returning True on
            # "the binary was found" reported a banner nobody saw, and the caller
            # then recorded a send and suppressed the alert for 24 hours.
            r = subprocess.run(cmd, capture_output=True, timeout=15)
            if r.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    if shutil.which("osascript"):
        # Quotes are the only metacharacter that matters inside an AppleScript
        # string literal, and these strings are ours — but escape anyway, since
        # an alert text will eventually include a device name someone chose.
        def q(s): return s.replace("\\", "\\\\").replace('"', '\\"')
        script = f'display notification "{q(message)}" with title "{q(title)}"'
        if subtitle:
            script += f' subtitle "{q(subtitle)}"'
        if urgent:
            script += ' sound name "Basso"'
        try:
            r = subprocess.run(["osascript", "-e", script],
                               capture_output=True, timeout=15)
            if r.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    return False

def _started_by():
    """cron, or a person at a terminal. The page's refresh control passes its
    own value; this is the fallback for everything else."""
    import os
    from . import runlog
    return runlog.started_by()      # one answer, not a second copy of the rule


def main():
    quiet = "--quiet" in sys.argv
    force = "--test" in sys.argv
    cfg = config.load()

    if "--test-email" in sys.argv:
        # Finding out that mail was misconfigured at the moment the pool needs
        # attention is the worst possible time to find out.
        n = cfg.get("notify", {})
        if not n.get("email"):
            print("  notify.email is false in config.toml — nothing to test")
            return 1
        # mail.test_alert is the ONLY copy of this message. This one said
        # "Sending as: the SMTP username" while the envelope carried the
        # resolved sender -- the defect server.py's copy carries a comment
        # about having fixed, still live here because the fix reached the copy
        # somebody was looking at.
        from . import mail
        ok, err = send_email(cfg, mail.test_alert(cfg))
        if ok:
            print(f"  sent to {n.get('email_to')} — check it arrived")
            return 0
        print(f"  FAILED: {err}")
        if "Application-specific password" in str(err) or "5.7.9" in str(err):
            print("  Gmail needs an app password, not the account password:")
            print("  https://myaccount.google.com/apppasswords")
        return 1
    now = dt.datetime.now()

    # THE WHOLE RUN HOLDS THE LOCK, because the state is read at the top and
    # written at the bottom with SMTP sends in between. save_state writes
    # atomically, which makes each write whole and says nothing about the
    # read-then-write pair — and cron fires this every thirty minutes, so it
    # races itself whenever one run overruns. The run that overruns is the run
    # that MAILS, because an SMTP round trip is the slow path. Measured: 39 of
    # 40 "already told you" records lost; 20 of 20 duplicate sends unrecorded.
    #
    # A second watchdog that cannot get in should SKIP, not queue: by the time
    # it acquired, the first run would have answered the same question.
    try:
        _watch_lock = locking.exclusive(config.data_dir(cfg), "watch", timeout=5.0)
        _watch_lock.__enter__()
    except locking.LockTimeout:
        if not quiet:
            print("  another watchdog run is still going — skipping this one")
        from . import runlog
        runlog.record("watch", "skipped", detail="another run still going",
                      by=_started_by(), cfg=cfg)
        return 0
    # ON THE RECORD WHICHEVER WAY IT ENDS. bin/watch died on every run for
    # weeks and the only reason nobody knew is that nothing wrote down that it
    # had tried. The record is written from a finally, so a crash is a row
    # rather than the reason there is no row.
    from . import runlog
    with runlog.Run("watch", by=_started_by(), cfg=cfg) as r:
        try:
            rc, tally = _run(cfg, now, quiet, force)
        finally:
            _watch_lock.__exit__(None, None, None)
        r.alerts = tally.get("alerts", 0)
        r.sent = tally.get("sent", 0)
        r.undelivered = tally.get("undelivered", 0)
        r.detail = tally.get("detail", "")
        r.outcome = "ok" if rc == 0 else "failed"
        # THE RUN HAPPENED. Said out loud, to something outside this host.
        heartbeat(cfg, rc, tally, quiet)
        return rc


def heartbeat(cfg, rc, tally, quiet=False):
    """Tell an external monitor this run completed. Never fails the run.

    WHY THIS IS NOT ANOTHER CHECK INSIDE poolhound. Every alarm this product
    raises depends on this process running, so none of them can report that it
    did not. MEASURED: /mnt/poolhound failed to mount after a reboot, the
    container refused to start -- correctly, rather than seeding a second pool
    history -- and collect.sh skipped every job for forty hours. `watch` was
    skipped 86 times, every thirty minutes, and the free-chlorine staleness
    alarm that was due at 07:37 UTC arrived seventeen hours late. The only
    trace was 86 lines in a log nobody reads and a status file, on the missing
    share, still saying exit 0 from before it all started.
    
    A push monitor inverts that: it alarms on the ping it did NOT get, which is
    the one shape of check that keeps working when its subject is dead.

    PINGED ON EVERY COMPLETED RUN, including one that found alarms and one that
    could not deliver them. This says "the watchdog ran", not "the pool is
    fine" -- conflating the two would take the monitor down every time the
    chlorine was low, and train somebody to ignore it.
    """
    from . import vault
    try:
        _who, url, _src = vault.credentials_for("heartbeat")
    except Exception:
        return False
    if not url:
        return False
    import urllib.parse, urllib.request
    # status=up means THIS PROCESS COMPLETED. rc is carried in the message
    # rather than the status for the reason in the docstring.
    q = urllib.parse.urlencode({
        "status": "up",
        "msg": f"rc={rc} alerts={tally.get('alerts', 0)} "
               f"sent={tally.get('sent', 0)} "
               f"undelivered={tally.get('undelivered', 0)}",
    })
    sep = "&" if "?" in url else "?"
    try:
        # Short timeout: bookkeeping must not wedge a cron job, and the monitor
        # noticing a LATE ping is the same signal as it noticing a missing one.
        with urllib.request.urlopen(f"{url}{sep}{q}", timeout=5) as r:
            ok = 200 <= r.status < 300
    except Exception as e:
        # THE URL IS A CREDENTIAL and must not reach a log. urllib puts the
        # full URL into the text of most of its exceptions, so the type is
        # reported and the message is not.
        if not quiet:
            print(f"  heartbeat not delivered ({type(e).__name__})")
        return False
    if not quiet and not ok:
        print("  heartbeat refused by the monitor")
    return ok


def _run(cfg, now, quiet, force):
    """The body of a watchdog run. THE CALLER HOLDS THE watch LOCK.

    `force` IS A PARAMETER BECAUSE THE EXTRACTION DROPPED IT. Splitting this
    body out of main() so the lock could wrap the whole run left four readers of
    `force` behind, reading a name that no longer existed in their scope. Three
    sit on branches the local data never takes; the fourth -- the one that stops
    a second alert about something already reported -- is on the path every real
    run walks, so the server raised NameError on every thirty-minute run while the
    workstation's selftest passed.

    That is the SAME defect this function was extracted to fix, one layer along:
    the watchdog not running, and nothing saying so. It was caught within the
    hour this time, by the collector status check, which is the point of it.
    """
    st = load_state(cfg)

    prev_run = None
    if st.get("last_run"):
        try:
            prev_run = dt.datetime.fromisoformat(st["last_run"])
        except ValueError:
            pass

    # Did WE miss our own schedule? Then the machine was not awake, and the
    # collectors were no more able to run than we were.
    slept = prev_run is not None and (now - prev_run) > HEARTBEAT_GAP
    if slept:
        st["grace_until"] = (now + GRACE).isoformat()
        if not quiet:
            # ago() returns a POINT IN TIME ("4 hours ago"), not a duration, so
            # "was away for 4 hours ago" is what this printed on the server's first
            # successful run.
            print(f"  machine was last awake {ago(prev_run.isoformat())} — "
                  f"grace until {(now + GRACE).strftime('%H:%M')}")
    in_grace = False
    if st.get("grace_until"):
        try:
            in_grace = now < dt.datetime.fromisoformat(st["grace_until"])
        except ValueError:
            in_grace = False

    health = {h["key"]: h for h in collector_health(
        rows("samples.csv"), rows("readings.csv"), rows("leslies.csv"))}

    sent = 0

    raised = 0          # alerts this run decided to send

    undelivered = 0     # ...of which no notifier could carry
    undeliverable_event = False   # an equipment edge nobody could be told about
    seen = st.setdefault("sources", {})
    for key in sorted(ALERT_ON):
        bad = key in health
        rec = seen.setdefault(key, {"bad": False, "since": None, "last_notified": None})
        was_bad = bool(rec.get("bad"))

        if bad and in_grace and not was_bad and not force:
            # A fault that appeared while the machine was asleep is not yet a
            # fault — it is a collector that has not had a chance to run.
            if not quiet:
                print(f"  {LABELS[key]}: stale, but within catch-up grace — holding")
            continue

        if bad:
            h = health[key]
            rec["bad"] = True
            rec["since"] = rec.get("since") or now.isoformat()
            last = rec.get("last_notified")
            due = True
            if was_bad and last and not force:
                try:
                    due = (now - dt.datetime.fromisoformat(last)) > REMIND_EVERY
                except ValueError:
                    due = True
            if due:
                code = last_exit(JOBS[key])
                extra = ""
                if code and code not in ("0",):
                    extra = f" The job is also exiting with code {code}."
                elif code == "0":
                    extra = " The job itself is running without error, so the "\
                            "source is answering but not with new data."
                ok = notify(
                    title=f"{LABELS[key]} has stopped",
                    subtitle=h["text"].capitalize(),
                    message=f"{h['fix']}{extra}",
                    url=site_url(cfg, "#home"), urgent=True,
                    cfg=cfg, severity="serious")
                # Only a send that actually happened counts. Recording it
                # regardless meant a failed notification still suppressed the
                # next 24 hours of alerts — the collector stays down and the
                # product goes quiet about it, which is the one failure mode the
                # watchdog exists to prevent.
                raised += 1
                if ok:
                    rec["last_notified"] = now.isoformat()
                    sent += 1
                else:
                    undelivered += 1
                if not quiet:
                    print(f"  {'NOTIFIED ' if ok else 'COULD NOT NOTIFY'} "
                          f"{LABELS[key]}: {h['text']}"
                          + ("" if ok else "  (no notifier available — will retry)"))
            elif not quiet:
                print(f"  {LABELS[key]}: still stale, already notified "
                      f"{ago(rec['last_notified'])}")
        else:
            if was_bad:
                # Closing the loop matters as much as opening it: without this,
                # someone keeps chasing a problem that fixed itself.
                since = rec.get("since")
                dur = f" It was quiet since {dt.datetime.fromisoformat(since).strftime('%-d %b %H:%M')}." if since else ""
                ok = notify(title=f"{LABELS[key]} is reporting again",
                            message=f"Data is flowing.{dur}",
                            url=site_url(cfg, "#home"),
                            cfg=cfg, severity="info")
                raised += 1
                sent += 1 if ok else 0
                undelivered += 0 if ok else 1
                if not quiet:
                    print(f"  {'RECOVERED' if ok else 'COULD NOT SAY RECOVERED'} "
                          f"{LABELS[key]}")
            rec.update({"bad": False, "since": None, "last_notified": None})

    # ---------------------------------------------------------- equipment
    notify_cfg = cfg.get("notify", {})
    samples = rows("samples.csv")
    seen_ts = None
    if st.get("last_sample_ts"):
        try:
            seen_ts = dt.datetime.fromisoformat(st["last_sample_ts"])
        except ValueError:
            seen_ts = None
    # NOT `newest` — that is the loader imported at the top of this file, and
    # binding over it here made every later call to it a TypeError. bin/watch
    # has been dying on `latest = newest(readings)` forty lines below, on the
    # path that decides whether to alert about chlorine and pH: the watchdog
    # that exists to tell you a collector went quiet was itself silently
    # failing every thirty minutes. Found by running it, not by reading it.
    events, newest_sample_ts = equipment_events(samples, after_ts=seen_ts)

    if seen_ts is None:
        # First run with equipment watching enabled: adopt the current position
        # rather than announcing every heater cycle in the history.
        if not quiet and events:
            print(f"  first look at equipment — {len(events)} historical edges ignored")
        events = []

    for ev in events:
        spec = EQUIPMENT[ev["key"]]
        if not notify_cfg.get(ev["key"], spec["default"]):
            continue
        if force or True:
            when_txt = ev["at"].strftime("%-d %b at %H:%M")
            if ev["on"]:
                ok = notify(title=f"{spec['label']} came on",
                            subtitle=when_txt, message=spec["why"],
                            url=site_url(cfg, "#home"),
                            cfg=cfg, severity="info")
            else:
                ran = ev.get("ran_minutes")
                mins = (f"{ran/60:.1f} hours" if ran and ran >= 90
                        else (f"{ran:.0f} minutes" if ran else "an unknown time"))
                ok = notify(title=f"{spec['label']} went off",
                            subtitle=when_txt,
                            message=f"It ran for {mins}.",
                            url=site_url(cfg, "#home"),
                            cfg=cfg, severity="info")
            raised += 1
            sent += 1 if ok else 0
            undelivered += 0 if ok else 1
            # AND THE WATERMARK ONLY MOVES ON A SEND THAT HAPPENED. st
            # ["last_sample_ts"] is advanced past every event this loop saw, so
            # an edge nobody could be told about was skipped permanently — the
            # one branch here whose loss is unrecoverable.
            if not ok:
                undeliverable_event = True
            if not quiet:
                print(f"  {'NOTIFIED ' if ok else 'COULD NOT NOTIFY'} {spec['label']} "
                      f"{'on' if ev['on'] else 'off'} at {ev['at'].strftime('%H:%M')}")
    # THE WATERMARK IS A RECEIPT, NOT A CLOCK. Advancing it past an edge that
    # could not be delivered loses that edge permanently — the next run starts
    # after it. Held back on an undeliverable one so the event is re-offered
    # once a notifier works again.
    if newest_sample_ts and not undeliverable_event:
        st["last_sample_ts"] = newest_sample_ts.isoformat()

    # ---------------------------------------------------------- filter
    # ONE message per dirty filter, at info. A loading filter costs turnover
    # and chlorine over weeks, not tonight, so it never pages anybody -- but
    # the threshold crossing is a clear, dated event, and Home is somewhere a
    # person has to go and look. Keyed to the cleaning it is measured from, so
    # marking the filter cleaned is what re-arms it, and recorded only on a
    # send that happened, for the reason every branch above gives.
    if notify_cfg.get("filter", True):
        from . import equipment as _eq
        try:
            f = _eq.filter_loading(
                samples, _eq.done(rows(_eq.FILE), "filter", when), now, when, num)
        except Exception as e:                       # noqa: BLE001
            f = {"grade": "insufficient"}
            if not quiet:
                print(f"  filter check could not run: {type(e).__name__}: {e}")
        epoch = (f.get("cleaned") or f.get("since") or now).isoformat()
        if (f.get("grade") == "measured" and f.get("status") == "clean"
                and st.get("filter_alerted") != epoch):
            ok = notify(
                title="The filter is due a clean",
                subtitle=f"{abs(f['drift']) * 100:.0f}% less draw at {f['rpm']:.0f} RPM",
                message=(f"At its usual speed the pump draws {f['current']:.0f} W "
                         f"against {f['baseline']:.0f} W after the last cleaning. "
                         f"A loading filter moves less water, and less water "
                         f"through the cell is less chlorine. Mark it cleaned on "
                         f"Home under Equipment health once it is done."),
                # "#home", not the section's id: site_url() picks the private
                # build from the TAB an anchor names, and a section id is not
                # one, so it would have linked to the public landing page.
                url=site_url(cfg, "#home"), cfg=cfg, severity="info")
            raised += 1
            sent += 1 if ok else 0
            undelivered += 0 if ok else 1
            if ok:
                st["filter_alerted"] = epoch
            if not quiet:
                print(f"  {'NOTIFIED ' if ok else 'COULD NOT NOTIFY'} filter due a clean")

    # ---------------------------------------------------------- chemistry
    if notify_cfg.get("chemistry", True):
        readings = rows("readings.csv")
        lab_rows, les_rows = rows("lab.csv"), rows("leslies.csv")
        latest = newest(readings)
        LAB = best_lab((newest(lab_rows), "WaterGuru"),
                       (newest(les_rows), "Leslie's"))
        TG = targets(LAB, cfg.get("pool", {}).get("sanitiser"),
                     (newest(les_rows)).get("sanitizer", ""))
        alarms = {a[0]: a for a in chemistry_alarms(latest, LAB, TG)}
        chem_state = st.setdefault("chemistry", {})

        for key, (_, sev, title, why) in alarms.items():
            rec = chem_state.setdefault(key, {"active": False, "last_notified": None})
            due = not rec["active"]
            if rec["active"] and rec.get("last_notified") and not force:
                try:
                    due = (now - dt.datetime.fromisoformat(rec["last_notified"])) > REMIND_EVERY
                except ValueError:
                    due = True
            rec["active"] = True
            if due:
                # The reading itself travels with the alarm: an email is read
                # away from the pool, where "pH is high" without the number is
                # not something anyone can act on.
                # The readings travel as DATA, not as a sentence. They were
                # formatted into "Now: FC 0.4 ppm, pH 7.9, status RED." and
                # glued onto the message, which reads acceptably in plain text
                # and cannot be laid out as anything else -- so the HTML part
                # would have had to re-parse a string this code had just built.
                # NOT `now`: that is _run's clock, and shadowing it here made
                # the next statement call .isoformat() on a list of tuples.
                readings = [("Free chlorine", f"{latest.get('free_cl','?')} ppm"),
                            ("pH", f"{latest.get('ph','?')}"),
                            ("Pod status", f"{latest.get('status','?')}")]
                ok = notify(title=title, subtitle=sev.capitalize(),
                            message=why, facts=readings,
                            url=site_url(cfg, "#home"),
                            urgent=(sev == "critical"),
                            cfg=cfg, severity=sev)
                raised += 1
                # ONLY A SEND THAT HAPPENED SUPPRESSES THE NEXT ONE. This set
                # last_notified unconditionally, so a failed delivery silenced
                # the alarm for REMIND_EVERY -- 24 hours -- and a pool sitting
                # at 0.0 ppm free chlorine told nobody, twice an hour, while
                # printing "all clear" and exiting 0. The collector branch
                # above already had this exact fix and this one did not.
                if ok:
                    rec["last_notified"] = now.isoformat()
                    sent += 1
                else:
                    undelivered += 1
                if not quiet:
                    print(f"  {'NOTIFIED ' if ok else 'COULD NOT NOTIFY'} "
                          f"[{sev}] {title}"
                          + ("" if ok else "  (no notifier available — will retry)"))
            elif not quiet:
                print(f"  {title} — still true, already notified")

        for key, rec in list(chem_state.items()):
            if key not in alarms and rec.get("active"):
                # Back inside safe limits. Worth saying, for the same reason a
                # collector recovery is: otherwise someone keeps chasing it.
                ok = notify(title="Back within safe limits",
                            message=f"{key.replace('_',' ')} is no longer in alarm.",
                            url=site_url(cfg, "#home"),
                            cfg=cfg, severity="info")
                raised += 1
                sent += 1 if ok else 0
                undelivered += 0 if ok else 1
                # Clearing `active` on an undelivered recovery would mean the
                # next alarm reads as the first one, and nobody was ever told
                # this one ended.
                if ok:
                    rec.update({"active": False, "last_notified": None})
                if not quiet:
                    print(f"  {'RECOVERED' if ok else 'COULD NOT SAY RECOVERED'} {key}")

    # ---------------------------------------------------------- schedules
    r2, s2, u2 = _watch_schedule(cfg, now, st, quiet, force)
    raised += r2
    sent += s2
    undelivered += u2

    # ---------------------------------------------------------- anomalies
    r3, s3, u3 = _watch_anomalies(cfg, now, st, quiet, force, samples=samples)
    raised += r3
    sent += s3
    undelivered += u3

    # ---------------------------------------------------------- the shelf
    # A jug running low is worth one notice, once, and no reminder: nothing is
    # wrong with the water, and Home already carries it as "keep an eye on".
    # "info", so email holds it to its own bar -- the default "serious" bar
    # keeps it off the phone unless somebody lowered it on purpose.
    #
    # OFF UNLESS ASKED FOR. On the server there is no desktop, so an info
    # notice below the email bar reaches nobody, and an alert nobody could be
    # sent is counted undelivered -- which turns this run red over a shopping
    # list. Somebody who sets notify.stock = true has said where it should go.
    if notify_cfg.get("stock", False):
        from . import stock as _stock
        from .render import stock_now
        shelf_state = st.setdefault("stock", {})
        try:
            short = {e["chem"]: e for e in _stock.needs_buying(stock_now())}
        except Exception as e:
            short = {}
            if not quiet:
                print(f"  stock: could not work it out ({type(e).__name__}: {e})")
        for chem, e in short.items():
            rec = shelf_state.setdefault(chem, {"told": False})
            if rec["told"]:
                continue
            title, detail = _stock.action_text(e)
            ok = notify(title=title, message=detail,
                        url=site_url(cfg, "#chemicals"), cfg=cfg, severity="info")
            raised += 1
            if ok:
                rec["told"] = True
                sent += 1
            else:
                undelivered += 1
            if not quiet:
                print(f"  {'NOTIFIED ' if ok else 'COULD NOT NOTIFY'} {title}")
        # Re-armed when the shelf is stocked again, silently: "you bought
        # acid" is not news to the person who bought it.
        for chem in list(shelf_state):
            if chem not in short:
                shelf_state.pop(chem)

    st["last_run"] = now.isoformat()
    save_state(cfg, st)

    # "ALL CLEAR" MEANS NOTHING WAS WRONG, NOT THAT NOTHING WAS DELIVERED.
    #
    # This was gated on `sent == 0`, and `sent` counts notifications that got
    # OUT — so a run that raised two alerts and failed to deliver either printed
    # "all clear" as its last line and exited 0. A first-run install is exactly
    # that state: email is off in config.example.toml and desktop notification
    # does not exist on the server, so the very first run of the watchdog on the
    # deployed host says everything is fine while telling nobody that both
    # collectors have never returned anything.
    #
    # The phrase was self-contradicting on top of that: `alive` is the set of
    # sources NOT alerting, so zero configured sources rendered as
    # "all clear — nothing reporting".
    #
    # Non-zero exit when an alert could not be delivered, because collect.sh
    # records the status and bootstrap-server.sh --check judges it — this is the
    # one path that turns an undeliverable alert into something a person sees.
    # WHAT THE RUN DID, handed back so main() can put it on the record. The
    # counts are the difference between "the watchdog ran" and "the watchdog
    # reached somebody": a night with two alarms and no delivery is not a quiet
    # night, and for weeks nothing anywhere could tell the two apart.
    tally = {"alerts": raised, "sent": sent, "undelivered": undelivered}
    if undelivered:
        if not quiet:
            print(f"  {raised} alert(s) raised, {sent} delivered — "
                  f"{undelivered} could NOT be delivered. No notifier is "
                  f"working; set up email on Settings, or nothing here will "
                  f"reach you.")
        tally["detail"] = (f"{raised} raised, {sent} delivered, "
                           f"{undelivered} undelivered — no notifier is working")
        return 1, tally
    if not quiet and raised == 0:
        alive = [LABELS[k] for k in sorted(ALERT_ON) if k not in health]
        print(f"  all clear — {', '.join(alive) or 'nothing'} reporting"
              if alive else
              "  nothing is reporting yet — no collector has returned data")
    tally["detail"] = (f"{raised} alert(s) raised, all delivered" if raised
                       else "all clear")
    return 0, tally

def _watch_schedule(cfg, now, st, quiet, force):
    """The scheduler, from OUTSIDE the process that runs it.

    Two things only a different process can see. The thread stamps alive_at
    into schedules.json every few minutes; if that goes stale, the thing that
    would have switched the spa heater off is not running, and it cannot say
    so itself. And an alert the scheduler could not deliver -- no relay, a
    refused login -- is retried here on the watchdog's cadence, because the
    scheduler only tries once and a heater that did not switch off is the one
    message that must not be lost to a transient SMTP error.

    Returns (raised, sent, undelivered) for the run's tally.
    """
    from . import schedule
    raised = sent = undelivered = 0
    utc = schedule.utcnow()
    rec = st.setdefault("scheduler", {"bad": False, "last_notified": None})
    data = config.data_dir(cfg)
    alive = schedule.alive_at(data=data, now=utc)
    stale = alive is not None and (utc - alive).total_seconds() > schedule.ALIVE_STALE_S
    if stale:
        due = force or not rec.get("bad")
        if rec.get("bad") and rec.get("last_notified") and not force:
            try:
                due = (now - dt.datetime.fromisoformat(rec["last_notified"])) > REMIND_EVERY
            except ValueError:
                due = True
        rec["bad"] = True
        if due:
            ok = notify(title="The scheduler has stopped",
                        subtitle=f"Last alive {ago(alive.astimezone().isoformat())}",
                        message="Nothing scheduled will run, and no automatic off — "
                                "including the household limit on the spa heater — "
                                "will happen until it is running again. The server "
                                "log says why; restarting poolhound starts it.",
                        url=site_url(cfg, "#control"), urgent=True,
                        cfg=cfg, severity="serious")
            raised += 1
            if ok:
                rec["last_notified"] = now.isoformat()
                sent += 1
            else:
                undelivered += 1
            if not quiet:
                print(f"  {'NOTIFIED ' if ok else 'COULD NOT NOTIFY'} scheduler stopped")
    elif rec.get("bad"):
        rec.update({"bad": False, "last_notified": None})
        if not quiet:
            print("  scheduler is running again")

    late = schedule.unalerted(data=data, now=utc)
    if late:
        ids = schedule.send_alerts(late, cfg)
        raised += len(late)
        sent += len(ids)
        undelivered += len(late) - len(ids)
        if ids:
            schedule.mark_alerted(ids, data=data, now=utc)
        if not quiet:
            print(f"  scheduler alerts: {len(ids)} of {len(late)} delivered on retry")
    return raised, sent, undelivered


def anomaly_inputs(cfg, samples=None, now=None):
    """What anomaly.detect() needs beyond the samples, read through the same
    loaders the page uses -- so the alert and Home cannot disagree about the
    salt floor, the cleanings or what the household limit did."""
    from . import equipment as _eq, schedule as _sch
    les = rows("leslies.csv")
    LAB = best_lab((newest(rows("lab.csv")), "WaterGuru"), (newest(les), "Leslie's"))
    TG = targets(LAB, (cfg.get("pool") or {}).get("sanitiser"),
                 newest(les).get("sanitizer", ""))
    try:
        net = _sch.net_lookup(data=config.data_dir(cfg))
    except Exception:                                # noqa: BLE001
        net = None          # no schedules file is "the net did nothing"
    return {"salt_low": (TG.get("salt") or (None,))[0],
            "cleans": _eq.done(rows(_eq.FILE), "filter", when), "net": net}


def _watch_anomalies(cfg, now, st, quiet, force, samples=None, notify_fn=None,
                     inputs=None):
    """Equipment anomalies: say each once when it starts and once when it ends.

    The detecting is anomaly.py's and so is deciding what is due; this only
    sends, and records a send that HAPPENED -- an undelivered alert stays
    untold and is offered again next run, the rule every branch above learned.
    Returns (raised, sent, undelivered).
    """
    from . import anomaly as A
    send = notify_fn or notify
    raised = sent = undelivered = 0
    samples = rows("samples.csv") if samples is None else samples
    try:
        occs = A.detect(samples, now, when, num, cfg=cfg,
                        **(anomaly_inputs(cfg, samples, now) if inputs is None
                           else inputs))
    except Exception as e:                           # noqa: BLE001
        # A detector that raises must not take the collector and chemistry
        # alarms down with it -- but it must not be silent either.
        if not quiet:
            print(f"  anomalies could not be checked: {type(e).__name__}: {e}")
        return 0, 0, 0
    first = "anomaly" not in st
    state = st.setdefault("anomaly", {})
    for kind, o in A.plan(occs, state, now, first_run=first and not force):
        sev = o.get("severity") or "serious"
        if kind == "recover":
            title, msg = A.recovery_text(o)
            ok = send(title=title, message=msg, url=site_url(cfg, "#home"),
                      cfg=cfg, severity=sev)
        else:
            spec = A.RULES[o["rule"]]
            ok = send(title=o["title"],
                      subtitle=("Still open, since " if kind == "remind" else "Since ")
                               + f"{o['start']:%-d %b %H:%M}",
                      message=f"{o['detail']}\n\n{spec['why']}",
                      facts=o.get("facts") or (), url=site_url(cfg, "#home"),
                      urgent=bool(o.get("urgent")), cfg=cfg, severity=sev)
        raised += 1
        key = o.get("key") or next((k for k, r in state.items() if r is o), None)
        if ok:
            sent += 1
            if kind == "recover":
                # Kept, closed, until it leaves the window: dropping it would
                # make the same ended occurrence look new on the next run.
                if key in state:
                    state[key]["open"] = False
            else:
                state[key] = A.record(o, told=now.isoformat(timespec="seconds"))
        else:
            undelivered += 1
            if kind == "alert":
                state.setdefault(key, A.record(o, told=None))
        if not quiet:
            print(f"  {'NOTIFIED ' if ok else 'COULD NOT NOTIFY'} "
                  f"[{kind}] {o.get('title')}")
    # What was seen and needs no telling: the first run's adopted history, an
    # info rule nobody asked to hear, a run the household limit dealt with.
    # Recorded so a later run does not take it for new, and kept open/closed
    # truthfully so a recovery is offered only for something that was told.
    for o in occs:
        rec = state.get(o["key"])
        if rec is None:
            if o.get("notify") and not o.get("handled") and not (first and not o["open"]):
                continue                        # due, and its send failed above
            rec = state[o["key"]] = A.record(o, told=None)
            rec["adopted"] = True
        # A told occurrence that has ended stays "open" here until its
        # recovery is DELIVERED -- marking it closed first would mean nobody
        # is ever told it ended.
        if rec.get("told") and rec.get("open") and not o["open"]:
            continue
        rec["open"] = bool(o["open"])
    # Forget what has fallen out of the window and is not open.
    horizon = (now - A.LOOKBACK - dt.timedelta(days=1)).isoformat()
    for k in [k for k, r in state.items()
              if not r.get("open") and str(r.get("start") or "") < horizon]:
        state.pop(k)
    return raised, sent, undelivered


if __name__ == "__main__":
    sys.exit(main())
