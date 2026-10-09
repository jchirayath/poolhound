#!/usr/bin/env python3
"""Create (idempotently) the Uptime Kuma PUSH monitor that watches this watchdog.

    kuma-heartbeat.py <kuma.db> --base-url https://uptime.example.com [--show-url]

WHY A PUSH MONITOR AND NOT AN HTTP ONE

Every alarm poolhound raises depends on `bin/watch` running, so none of them can
report that it did not. MEASURED: the share failed to mount after a reboot, the
container refused to start rather than seed a second pool history, and
collect.sh skipped every job for forty hours — `watch` 86 times, every thirty
minutes. The free-chlorine staleness alarm due at 07:37 UTC arrived seventeen
hours late, because the process that raises alarms was one of the casualties.

An HTTP monitor pointed at the site would not have caught it either: the site
was up the whole time. What was dead was the thing that ASKS QUESTIONS. Only a
push monitor inverts that — it alarms on the ping it did not receive, which is
the one shape of check that keeps working when its subject is dead.

WHY SQLITE AND NOT THE API

Uptime Kuma 1.x has no REST API for monitor CRUD -- it speaks only a websocket
protocol, which is why the stack pins 1.x (the client library that scripts it
never followed 2.x). The established pattern on this stack is to write the row directly — see
`seed-uptime-kuma.py`, which seeds the HTTP monitors the same way and carries
the same caveat, which is repeated here because it is the one that bites:

  THE CALLER MUST STOP THE CONTAINER FIRST. Kuma caches the monitor list in
  memory and only reloads it on boot, so a row inserted under a running Kuma is
  invisible until it restarts and may be written over.

Only the columns that matter are set. Every NOT NULL column in `monitor` carries
a schema DEFAULT (verified against the live 1.23.17 database before this was
written), so a partial INSERT stays compatible across 1.x schema revisions.

THE PUSH URL IS A CREDENTIAL. It carries its token in the path, so anything
holding it can report this monitor up — which is exactly the wrong thing to be
forgeable. It is printed only with --show-url, and it belongs in poolhound's
vault under the `heartbeat` service, not in a settings field and not in a shell
history.
"""
import argparse
import secrets
import sqlite3
import sys

# Three missed heartbeats before the monitor goes red. bin/watch runs every 30
# minutes from cron, so one late run is ordinary and three in a row is not.
DEFAULT_INTERVAL = 90 * 60
DEFAULT_NAME = "poolhound watchdog"

DESCRIPTION = (
    "bin/watch pings this on every COMPLETED run. Red means the watchdog did "
    "not run — not that the pool is unwell. The pool's own alarms arrive by "
    "email; this one exists because nothing inside poolhound can report its "
    "own absence."
)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("db", help="path to kuma.db")
    ap.add_argument("--base-url", required=True,
                    help="the Kuma origin, e.g. https://uptime.example.com")
    ap.add_argument("--name", default=DEFAULT_NAME)
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                    help=f"seconds between expected pings (default {DEFAULT_INTERVAL})")
    ap.add_argument("--show-url", action="store_true",
                    help="print the push URL. It is a credential: it goes in the "
                         "vault, not in a terminal you leave open")
    a = ap.parse_args(argv)

    conn = sqlite3.connect(a.db)
    try:
        row = conn.execute(
            "SELECT id, push_token, interval, active FROM monitor "
            "WHERE type = 'push' AND name = ?", (a.name,)).fetchone()

        if row:
            # IDEMPOTENT, AND IT REUSES THE TOKEN. Minting a new one would
            # silently invalidate the URL already sitting in poolhound's vault,
            # so the monitor would go red and the fix would look like the fault.
            mid, token, interval, active = row
            print(f"  exists: {a.name!r} (id={mid}, interval={interval}s, "
                  f"active={active}) — token reused")
            if interval != a.interval:
                conn.execute("UPDATE monitor SET interval = ? WHERE id = ?",
                             (a.interval, mid))
                conn.commit()
                print(f"  interval updated {interval}s -> {a.interval}s")
        else:
            # Owner is the first user, as seed-uptime-kuma.py does: on this
            # stack the admin account is id 1.
            u = conn.execute("SELECT id FROM user ORDER BY id LIMIT 1").fetchone()
            user_id = u[0] if u else 1
            token = secrets.token_urlsafe(24)
            cur = conn.execute(
                "INSERT INTO monitor "
                "(name, type, push_token, description, user_id, active, "
                " interval, retry_interval, maxretries) "
                "VALUES (?, 'push', ?, ?, ?, 1, ?, ?, 0)",
                (a.name, token, DESCRIPTION, user_id, a.interval,
                 min(a.interval, 900)),
            )
            conn.commit()
            # maxretries 0: the interval is already three times the ping
            # cadence, so a lapsed window IS the fault and there is nothing
            # useful to retry.
            print(f"  created: {a.name!r} (id={cur.lastrowid}, "
                  f"interval={a.interval}s, maxretries=0)")
    finally:
        conn.close()

    url = f"{a.base_url.rstrip('/')}/api/push/{token}"
    if a.show_url:
        print(f"\n  {url}\n")
        print("  Store it in poolhound's vault as the `heartbeat` credential.")
    else:
        print(f"  push URL ready ({len(token)}-char token). Re-run with "
              f"--show-url to print it.")
    print("  RESTART Kuma now: it only reloads monitors on boot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
