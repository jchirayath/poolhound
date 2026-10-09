#!/usr/bin/env bash
# Ship the agent to the Pi and restart it.
#
# WHY THIS EXISTS
#
# bootstrap-server.sh deploys the server. Nothing deployed the Pi, so the agent
# was whatever had last been rsynced by hand -- and it sat three weeks behind
# without anything saying so. The cost of that gap was not abstract: the agent
# hardcoded pump_rpm and pump_watts to "" and the fix could not take effect,
# because shipping it was a step somebody had to remember.
#
# The Pi is NOT reachable from the server and must not be. This runs from a machine
# on the house LAN, which is the only place that can see both.
#
#   ./deploy/update-pi.sh            deploy and restart
#   ./deploy/update-pi.sh --check    report what is running, change nothing
#
# PI_HOST overrides the address; by default it is read from config.toml's
# [aqualink] host, which is the Pi by definition -- AqualinkD runs on it.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
PI_USER="${PI_USER:-$(whoami)}"
PI_KEY="${PI_KEY:-$HOME/.ssh/id_ed25519}"
DEST="${PI_DEST:-/opt/poolhound}"
SVC="${PI_SVC:-poolhound-agent}"

c()   { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m  !\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

CHECK=0
[[ "${1:-}" == "--check" ]] && CHECK=1

# The address comes from config.toml rather than this file, for the same reason
# the server's script takes CHANGE-ME defaults: this repository is meant to be opened,
# and one household's internal addresses are not part of it.
if [[ -z "${PI_HOST:-}" ]]; then
  PI_HOST="$("$SRC/.venv/bin/python" - <<'PY' 2>/dev/null || true
import sys; sys.path.insert(0, ".")
from poolhound import config
site = config.load().get("site") or {}
# site.pi_host, NOT the old [aqualink] host. That section configured the
# workstation poller and is deleted; this script had been borrowing it as the
# Pi's address, which is the exact conflation site.pi_host exists to end --
# one key meaning "the machine on the pool's LAN" to two readers that wanted
# different machines. pi_host falls back to host, as it does everywhere else.
print(site.get("pi_host") or site.get("host") or "")
PY
)"
fi
[[ -n "$PI_HOST" ]] || die "no Pi address: set PI_HOST, or [site] pi_host in config.toml"

SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10 -i "$PI_KEY")
"${SSH[@]}" "$PI_USER@$PI_HOST" true 2>/dev/null \
  || die "cannot ssh to the Pi as $PI_USER with $PI_KEY at $PI_HOST.
  Set PI_USER / PI_KEY, and note that ssh on macOS does NOT resolve mDNS
  .local names even when ping and dscacheutil do — if $PI_HOST ends in
  .local and the Pi is reachable, pass PI_HOST=<address> instead."

c "What is running now"
# An MTIME IS NOT A VERSION. This printed the mtime of agent.py, which rsync -a
# preserves from the source checkout -- so it reported when the repo was cloned,
# not which commit is deployed. The exact blind spot this script's own header
# says it exists to close: "the agent sat three weeks behind without anything
# saying so", and --check could not detect that state.
state=$("${SSH[@]}" "$PI_USER@$PI_HOST" "systemctl is-active $SVC 2>/dev/null" || echo unknown)
pi_rev=$("${SSH[@]}" "$PI_USER@$PI_HOST" "cat $DEST/REVISION 2>/dev/null" | tr -d '\r' | tail -1)
want_rev="$(git -C "$SRC" describe --always --dirty --tags 2>/dev/null || echo unknown)"
printf '  service  : %s/%s\n' "$state" \
  "$("${SSH[@]}" "$PI_USER@$PI_HOST" "systemctl is-enabled $SVC 2>/dev/null" || echo unknown)"
printf '  deployed : %s\n' "${pi_rev:-(no REVISION — deploy again to stamp it)}"
printf '  here     : %s\n' "$want_rev"

if (( CHECK )); then
  # And --check now ASSERTS. It exited 0 whether the service was active, failed
  # or absent, having printed facts and judged none of them.
  fails=0
  [[ "$state" == "active" ]] || { warn "the agent is '$state', not running"; fails=$((fails+1)); }
  if [[ -z "$pi_rev" ]]; then
    warn "the Pi carries no REVISION — cannot tell which code is running there"
    fails=$((fails+1))
  elif [[ "$pi_rev" != "$want_rev" ]]; then
    warn "STALE AGENT — the Pi is running '$pi_rev', this checkout is '$want_rev'"
    warn "  run ./deploy/update-pi.sh to ship it"
    fails=$((fails+1))
  else
    ok "the Pi is running the current code ($pi_rev)"
  fi

  # THE SAME SECRET AT BOTH ENDS, compared without revealing it. The
  # fingerprint function existed for exactly this and had no callers, so the
  # one question neither script asked was whether the Pi and the server agree about
  # the token — which is the difference between "the agent is down" and "the
  # agent is being refused".
  pi_fp=$("${SSH[@]}" "$PI_USER@$PI_HOST" \
    "cd $DEST 2>/dev/null && python3 -c \"from poolhound import vault; print(vault.agent_token_fingerprint() or '')\"" \
    2>/dev/null | tr -d '\r' | tail -1)
  if [[ -z "$pi_fp" ]]; then
    warn "the Pi resolves NO agent token — it cannot authenticate to the server"
    fails=$((fails+1))
  else
    ok "the Pi holds an agent token (fingerprint $pi_fp)"
    echo "    the server's, for comparison:  ./deploy/bootstrap-server.sh --check"
  fi

  c "Is the agent reporting pump telemetry?"
  # AND THIS ASKED THE QUESTION WITHOUT JUDGING THE ANSWER. It printed three
  # fields and contributed nothing to `fails`, so --check exited 0 with
  # "(absent)" three times on screen -- while its own comment right here says
  # those columns mean "either an agent too old to read the fields or a pump
  # that does not report them, and those are very different problems". Naming
  # two different problems and then distinguishing neither is how the salt
  # ceiling bug survived: bin/sample stored a blank for salt on every sample it
  # ever took, and the column read as a panel that does not report salinity.
  #
  # The remote end emits one parseable line so the judging happens here, where
  # the other checks are, rather than being buried in a nested quoting level.
  telem=$("${SSH[@]}" "$PI_USER@$PI_HOST" "
    curl -fsS --max-time 8 http://127.0.0.1:8000/api/devices 2>/dev/null \
      | python3 -c \"
import json,sys
d={x['id']:x for x in json.load(sys.stdin).get('devices',[])}
p=d.get('Filter_Pump',{})
print('TELEM|' + '|'.join(str(p.get(k, '')) for k in ('Pump_RPM','Pump_Watts','Pump_GPM')))
\" || echo 'NOANSWER'
  " 2>/dev/null | tr -d '\r' | tail -1)

  if [[ "$telem" == NOANSWER* || -z "$telem" ]]; then
    # AqualinkD binds 0.0.0.0:8000 with no authentication and the agent is on
    # the same box, so no answer here is the controller being unreachable from
    # the machine whose entire job is reading it.
    warn "AqualinkD did not answer on 127.0.0.1:8000 — the agent cannot read the panel"
    fails=$((fails+1))
  else
    IFS='|' read -r _tag rpm watts gpm <<< "$telem"
    printf '  Pump_RPM    %s\n  Pump_Watts  %s\n  Pump_GPM    %s\n' \
      "${rpm:-(absent)}" "${watts:-(absent)}" "${gpm:-(absent)}"
    if [[ -z "$rpm" && -z "$watts" && -z "$gpm" ]]; then
      # All three missing is the agent-too-old case. A pump that is simply OFF
      # still reports the fields, as zeros.
      warn "the panel reports NO pump telemetry at all — RPM, watts and GPM are all absent"
      warn "  an agent too old to read them, or a pump that does not report them; they are different problems"
      fails=$((fails+1))
    elif [[ -z "$rpm" || -z "$watts" || -z "$gpm" ]]; then
      warn "pump telemetry is partial — some fields are reported and some are not"
      fails=$((fails+1))
    else
      ok "the panel is reporting all three pump fields"
    fi
  fi
  (( fails == 0 )) && ok "nothing to fix" || warn "$fails problem(s)"
  exit $(( fails > 0 ))
fi

c "Backing up the current install"
"${SSH[@]}" "$PI_USER@$PI_HOST" \
  "sudo tar czf /tmp/poolhound-pre-\$(date +%Y%m%d-%H%M%S).tgz -C \$(dirname $DEST) \$(basename $DEST)"
ok "backup written to /tmp on the Pi"

c "Stamping the revision"
# So the agent can say what it is running and --check can compare it. Written
# into the rsync payload and removed from the checkout straight after, for the
# same reason the server's deploy does: version_string() and agent.revision() both
# read this file, and leaving it behind makes every later local render claim the
# commit of the last deploy.
REV="$(git -C "$SRC" describe --always --dirty --tags 2>/dev/null || echo unknown)"
printf '%s\n' "$REV" > "$SRC/REVISION"
trap 'rm -f "$SRC/REVISION"' EXIT
ok "$REV"

c "Shipping the source"
# Same exclusions as the server's deploy, plus the screenshots: the Pi runs the agent
# and never renders a page, so the images are dead weight on an SD card.
rsync -az --delete --rsync-path="sudo rsync" \
  -e "ssh -o BatchMode=yes -i $PI_KEY" \
  --exclude '.git' --exclude '.venv' --exclude 'data' --exclude 'site' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude 'config/config.toml' \
  --exclude 'docs/screenshots' \
  "$SRC/" "$PI_USER@$PI_HOST:$DEST/"
ok "source on the Pi"

c "Installing the systemd units"
# THIS DEPLOY DID NOT INSTALL THEM, AND THEY HAD ALREADY DRIFTED.
#
# rsync ships the source to $DEST and stopped there, so the unit under
# /etc/systemd/system was whatever had been hand-placed on the Pi — dated
# 2026-09-12, with a Description naming the server's hostname that the
# repository's copy deliberately does not contain. A unit file in the tree
# that no deploy installs is documentation, not configuration, and the proof
# is that the ordering-cycle fix for this exact unit could not have reached
# the Pi by running this script.
#
# Copied through a temp path because $DEST is owned by root after rsync and
# install(1) wants to set the mode itself.
for unit in poolhound-agent.service poolhound-agent-ensure.service \
            poolhound-agent-ensure.timer; do
  "${SSH[@]}" "$PI_USER@$PI_HOST" \
    "sudo install -m 0644 -o root -g root '$DEST/deploy/$unit' \
       '/etc/systemd/system/$unit'"
  ok "$unit"
done
"${SSH[@]}" "$PI_USER@$PI_HOST" "sudo systemctl daemon-reload"

# THE HEALER IS ENABLED HERE, not assumed. A timer that is shipped and never
# enabled is the same class of defect as a unit that is in the tree and never
# installed.
"${SSH[@]}" "$PI_USER@$PI_HOST" \
  "sudo systemctl enable --now poolhound-agent-ensure.timer" >/dev/null 2>&1
tstate=$("${SSH[@]}" "$PI_USER@$PI_HOST" \
  "systemctl is-active poolhound-agent-ensure.timer" 2>/dev/null || echo unknown)
[[ "$tstate" == "active" ]] \
  && ok "the ensure timer is $tstate — a stopped agent is started within 5 min" \
  || warn "the ensure timer is $tstate — the agent will NOT self-heal"

# AND THE CYCLE THAT CAUSED ALL THIS, asked of the running system rather than
# of the file. systemd only reports it at boot, so this reads the current
# boot's journal: a deleted job leaves `inactive (dead)` with Result=success,
# which is indistinguishable from "nobody asked for it" at a glance.
if "${SSH[@]}" "$PI_USER@$PI_HOST" \
     "journalctl -b --no-pager 2>/dev/null | grep -q 'ordering cycle.*$SVC'"; then
  # A CYCLE IS REPORTED ONCE, AT BOOT, AND NEVER WITHDRAWN. So this says
  # nothing about whether the unit just installed still has the problem --
  # and reporting it the same way either way would mean every deploy between
  # the fix and the next reboot looked like the fix had failed.
  if "${SSH[@]}" "$PI_USER@$PI_HOST" \
       "grep -qE '^After=.*aqualinkd' /etc/systemd/system/$SVC.service"; then
    warn "ORDERING CYCLE on $SVC this boot — its start job was DELETED, and the"
    warn "  unit just installed still orders behind aqualinkd.service, which is"
    warn "  WantedBy=multi-user.target and After=multi-user.target. Fix After=."
  else
    warn "systemd reported an ordering cycle on $SVC EARLIER THIS BOOT and"
    warn "  deleted its start job — that is the history of this boot, not the"
    warn "  state of the unit just installed, which no longer orders behind it."
    warn "  Nothing to do: it clears at the next reboot, and the ensure timer"
    warn "  would start the agent within 5 minutes even if it recurred."
  fi
else
  ok "no ordering cycle on $SVC this boot"
fi

c "Restarting the agent"
"${SSH[@]}" "$PI_USER@$PI_HOST" "sudo systemctl restart $SVC"
sleep 5
state=$("${SSH[@]}" "$PI_USER@$PI_HOST" "systemctl is-active $SVC" 2>/dev/null || echo unknown)
[[ "$state" == "active" ]] && ok "agent is $state" || die "agent is $state — check journalctl -u $SVC"

c "Verifying"
"${SSH[@]}" "$PI_USER@$PI_HOST" "sudo journalctl -u $SVC --since '-30 seconds' --no-pager | tail -4 | sed 's/^/    /'"
warn "the next sample is the proof: check pump_rpm and pump_watts are populated"
c "Done."
