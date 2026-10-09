#!/usr/bin/env bash
# Run one poolhound collector inside the running container.
#
# `exec` into the running service rather than `run` a new one: `run` starts a
# second container with the same bind mount, and two processes appending to the
# same CSV over CIFS is exactly the race this avoids. It also means a collector
# cannot start at all if the service is down, which is the correct failure —
# a collector writing data nobody is serving is a silent problem.
set -euo pipefail
# SELF-LOCATING, because this file is installed at $STACK/poolhound/collect.sh
# and the compose project it drives lives one directory up. It used to name
# one install's absolute path, which made the stack layout a fact in the
# repository rather than a fact in server.env.
cd "$(dirname "$(readlink -f "$0")")/.."

TOOL="${1:?usage: collect.sh <wg-collect|leslies|watch|render|backup> [args...]}"
shift || true

case "$TOOL" in
  wg-collect|leslies|watch|render|chem|backup) ;;
  *) echo "refusing to run '$TOOL' — not a known collector" >&2; exit 2 ;;
esac

# WHERE A FAILURE GETS RECORDED.
#
# Cron discarded stderr into MAILTO="", so every collector that died did so in
# silence -- bin/watch for weeks, on every run, which is the job whose whole
# purpose is to notice silence. The output now goes to a log; this records the
# OUTCOME, next to the data, where bootstrap-server.sh --check can judge it
# without ssh-ing around looking for a log.
#
# On the share deliberately. The point of the record is to survive the thing
# that failed: a container that will not start takes a status file inside it
# with it, and a VM that is rebuilt takes /var/log.
STATUS_DIR=/mnt/poolhound/.collector-status
# AND A COPY ON THE HOST, because the share is one of the things that fails.
#
# The share was the only place this was written, with the reasoning above: a
# container that will not start takes a status file inside it with it. True,
# and it covers the wrong outage. MEASURED: /mnt/poolhound failed to mount
# after a reboot, the container correctly refused to start rather than seed a
# second history, and collect.sh skipped every job for forty hours -- 86 times,
# including `watch` every thirty minutes. Each skip called record() and every
# one of them wrote into the unmounted directory or nowhere at all, because
# record() is deliberately best-effort. What survived was the status from
# BEFORE the outage, saying exit 0, ok.
#
# Neither location covers both failures on its own: the share survives a VM
# rebuild and not an unmounted share; the host survives an unmounted share and
# not a VM rebuild. So both are written and the reader takes whichever is
# newer.
LOCAL_STATUS_DIR=/var/lib/poolhound/status

record() {
  # $1 exit status, $2 note. Best-effort: a collector must not fail because its
  # bookkeeping did, so every write here is allowed to fall over quietly.
  for d in "$STATUS_DIR" "$LOCAL_STATUS_DIR"; do
    mkdir -p "$d" 2>/dev/null || continue
    printf '%s\t%s\t%s\n' "$(date -Is)" "$1" "$2" > "$d/$TOOL" 2>/dev/null || true
  done
}

if ! docker compose ps --status running --services 2>/dev/null | grep -qx poolhound; then
  echo "$(date -Is) poolhound container is not running — skipping $TOOL" >&2
  record 1 "container not running"
  exit 1
fi

# NOT exec. exec replaces this shell, so nothing was left to record what
# happened -- the status file would only ever have said "started".
set +e
# -e, because `docker compose exec` does not inherit the host's environment.
# Without it every scheduled run would record itself as "manual", which is the
# opposite of the mistake the default avoids and just as untrue.
docker compose exec -T -e POOLHOUND_RUN_BY="${POOLHOUND_RUN_BY:-cron}" \
  poolhound python "/app/bin/$TOOL" "$@"
rc=$?
set -e
record "$rc" "$([ "$rc" -eq 0 ] && echo ok || echo "exit $rc")"
exit "$rc"
