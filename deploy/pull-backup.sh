#!/usr/bin/env bash
# =============================================================================
# pull-backup.sh — bring the pool's history off the VM and onto this machine.
#
#   ./pull-backup.sh            pull the newest tarball, verify it, prune
#   ./pull-backup.sh --check    grade what is already here, change nothing
#
# WHY THIS EXISTS
#
# docs/THREAT_MODEL.md's third open item, and the first of the two gaps
# docs/ASVS-L2.md calls out as the ones that would actually hurt: the nightly
# tarball `bin/backup` writes lands on the VM's own local disk. That is the one
# place guaranteed to fail separately from the Azure Files share, which is
# exactly what it was for — and it does not survive losing the VM. Everything
# irreplaceable in this product would then be gone: years of readings that
# cannot be re-measured.
#
# A workstation is off-box. That is the whole of the argument.
#
# WHAT THIS IS NOT
#
# IT IS NOT A COLLECTOR, and the distinction is the one CLAUDE.md's "nothing
# operational runs on a workstation" rule is actually about.
# `config.refuse_off_deployment()` exists because a pull started from a laptop
# APPENDS to a second data directory that nothing serves and nothing
# reconciles — two fabricated doses reached this household's chemicals.csv
# that way, and a shadow data directory accumulated for eight days before
# anybody noticed. This writes no CSV, touches no data directory, and reads
# only. It is read-only on the server too: it `cat`s a file and takes nothing
# else.
#
# Running `bin/backup` here instead would be the defect that rule exists to
# stop, wearing a backup's clothes. MEASURED on 2026-10-07: this checkout's
# data/ directory holds runs.csv and nothing else, 12 KB. A nightly job over
# it would produce a dated tarball, every night, containing none of the pool —
# and would look exactly like a working backup.
#
# THE FRESHNESS QUESTION IS ABOUT THE SERVER'S TARBALL, NOT THIS DOWNLOAD.
#
# A successful copy of a three-day-old tarball is a successful copy of a
# three-day-old tarball. This project has the same lesson written down about
# collectors — "a skip is not a success, and a wall of skips is not freshness",
# where lateness was measured from the most recent ATTEMPT while a skip wrote a
# fresh timestamp and collected nothing. So the age that is graded here is the
# age of the file ON THE SERVER, and a pull that works perfectly over a stale
# tarball is reported as stale.
#
# PREREQUISITES
#   az login, with Virtual Machine Administrator Login on the VM. The same
#   certificate bootstrap-server.sh uses, reused when it is still good.
# =============================================================================
set -euo pipefail

for _envf in .env server.env; do
  [ -f "$(dirname "$0")/$_envf" ] && . "$(dirname "$0")/$_envf" && break
done

RG="${RG:-CHANGE-ME-resource-group}"
VM="${VM:-CHANGE-ME-vm}"
HOST_IP="${SERVER_IP:-CHANGE-ME-ip}"
SSHCFG="${SERVER_SSH_CONFIG:-/tmp/poolhound-ssh.config}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"

# WHERE THE COPIES LAND, and deliberately NOT inside the repository.
#
# This tree is publishable and is meant to be opened; one household's pool
# history has no business inside it, which is why data/ is gitignored and why
# the CSVs were removed from version control on purpose. A backup directory in
# here would be one `git add -A` away from publishing the thing this exists to
# protect. Outside, by default, and the guard below refuses the inside case
# whatever anybody sets.
DEST="${POOLHOUND_PULL_DIR:-$HOME/Backups/poolhound}"
REMOTE_DIR="/var/lib/poolhound/backups"
KEEP_DAYS="${POOLHOUND_PULL_KEEP_DAYS:-30}"
# Two nights of slack, the same figure bootstrap-server.sh --check grades the
# server's own copy against. A nightly job that missed once is not an alarm.
STALE_H="${POOLHOUND_PULL_STALE_H:-48}"

c()   { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m  !\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# Everything whose loss would be permanent. A tarball that OPENS but holds none
# of these is the failure this verification is for: `tar -tzf` succeeding tells
# you the gzip stream is intact, not that the thing inside is a pool. The names
# come from config.HISTORY_FILES; samples.csv is the one every deployment has.
WANT_INSIDE="samples.csv"

MODE="${1:-pull}"
case "$MODE" in
  --check) printf '%s  poolhound: grading the local copies\n' \
             "$(date '+%Y-%m-%d %H:%M:%S %z')" ;;
  *)       printf '%s  poolhound: pulling the history off the VM\n' \
             "$(date '+%Y-%m-%d %H:%M:%S %z')" ;;
esac

# ---------------------------------------------------------------- the destination
#
# --check NEVER CREATES IT. A directory that this script made a moment ago is
# not evidence of anything, and "it does not exist" is the wrong answer to
# "is there a backup here" — the right one is "no copy exists", which is what
# the --check branch says. A grader that reports a missing directory instead of
# a missing backup is a grader that buries its own finding.
if [ "$MODE" != "--check" ]; then
  mkdir -p "$DEST" 2>/dev/null || true
  [ -d "$DEST" ] || die "$DEST does not exist and could not be created"
  # 700, AND THE TARBALLS 600 BELOW. What arrives here is not just readings:
  # the archive carries config.toml, which holds the house address and the
  # paths to every credential, and audit.csv, which holds the email address of
  # everybody who has ever touched the pool. On the share those files are
  # mounted 0660; a directory under $HOME is 755 by default, which on a shared
  # or managed Mac is every other account on the machine.
  chmod 700 "$DEST" 2>/dev/null || true
fi

# A COPY INSIDE THE THING IT IS COPYING IS NOT A COPY. bin/backup refuses a
# destination inside the data directory for this reason; the version of that
# mistake available here is a destination inside the checkout, which is both a
# publication risk and — if POOLHOUND_DATA were ever pointed at this tree — the
# original defect exactly.
case "$(cd "$DEST" 2>/dev/null && pwd || printf '%s' "$DEST")/" in
  "$SRC"/*) die "$DEST is inside the checkout at $SRC.
  This tree is published; one household's pool history does not belong in it.
  Set POOLHOUND_PULL_DIR to somewhere outside, or leave it at the default." ;;
esac

# ---------------------------------------------------------------- --check only
if [ "$MODE" = "--check" ]; then
  c "what is already on this machine"
  newest=$(ls -t "$DEST"/poolhound-*.tgz 2>/dev/null | head -1 || true)
  if [ -z "$newest" ]; then
    warn "NO LOCAL COPY EXISTS in $DEST"
    warn "  the VM is the only thing holding this pool's history"
    warn "  run it once by hand:  ./deploy/pull-backup.sh"
    exit 1
  fi
  age_h=$(( ( $(date '+%s') - $(stat -f %m "$newest") ) / 3600 ))
  n=$(ls -1 "$DEST"/poolhound-*.tgz 2>/dev/null | wc -l | tr -d ' ')
  size=$(du -h "$newest" | cut -f1 | tr -d ' ')
  if [ "$age_h" -le "$STALE_H" ]; then
    ok "$n copy(ies) here; the newest is ${age_h}h old, $size"
    ok "$(basename "$newest")"
    exit 0
  fi
  warn "the newest local copy is ${age_h}h old — the daily pull is not running"
  warn "  launchctl print gui/$(id -u)/com.poolhound.backup-pull | grep -i 'last exit'"
  exit 1
fi

# ---------------------------------------------------------------- the certificate
#
# Lifted from bootstrap-server.sh rather than shared, because a shell library
# between two scripts that are each run by hand is a third thing to keep
# working. The REASON it is bounded is worth carrying across, though: an
# unbounded `az ssh config` once sat here for fifty-three minutes with both
# streams sent to /dev/null, and from a launchd job that is a backup that
# silently never finishes.
command -v az >/dev/null || die "azure-cli required (brew install azure-cli)"
az account show --query user.name -o tsv >/dev/null 2>&1 \
  || die "not logged in to Azure — run: az login
  A scheduled pull cannot do this for you. This is the one failure mode that
  needs a person, which is why it exits non-zero rather than warning."

bounded() {
  local secs="$1"; shift
  "$@" & local pid=$!
  ( sleep "$secs"; kill -TERM "$pid" 2>/dev/null
    sleep 5;       kill -KILL "$pid" 2>/dev/null ) & local dog=$!
  local rc=0; wait "$pid" || rc=$?
  kill "$dog" 2>/dev/null; wait "$dog" 2>/dev/null
  return "$rc"
}

cert_until() {
  local cert; cert=$(awk -F'"' '/CertificateFile/{print $2; exit}' "$1" 2>/dev/null)
  [ -s "$cert" ] || return 1
  ssh-keygen -L -f "$cert" 2>/dev/null | awk '/Valid:/{print $5; exit}'
}

cert_good() {
  local until_ exp now
  until_=$(cert_until "$1") || return 1
  [ -n "$until_" ] || return 1
  exp=$(date -j -f '%Y-%m-%dT%H:%M:%S' "$until_" '+%s' 2>/dev/null) \
    || exp=$(date -d "$until_" '+%s' 2>/dev/null) || return 1
  now=$(date '+%s')
  [ $(( exp - now )) -gt 600 ]
}

c "Entra SSH certificate"
AZLOG="${TMPDIR:-/tmp}/poolhound-az-ssh-pull.log"
if cert_good "$SSHCFG"; then
  ok "reusing $SSHCFG — valid until $(cert_until "$SSHCFG")"
else
  bounded 120 az ssh config --file "$SSHCFG" --resource-group "$RG" --name "$VM" \
          --overwrite >"$AZLOG" 2>&1 \
    || { [ -s "$AZLOG" ] && sed 's/^/    /' "$AZLOG" >&2
         die "az ssh config did not finish within 120s (or failed); see $AZLOG"; }
  ok "minted a certificate — valid until $(cert_until "$SSHCFG")"
fi
SSH=(ssh -F "$SSHCFG" -o BatchMode=yes -o ConnectTimeout=20)
"${SSH[@]}" "$HOST_IP" true 2>/dev/null || die "cannot reach the server at $HOST_IP"

# ---------------------------------------------------------------- what to fetch
#
# A FILENAME OUT OF THAT DIRECTORY IS NOT A SHELL TOKEN, and this project has
# already paid for the lesson: the backup freshness check read the newest
# tarball's name off the server and interpolated it into
# `sudo sh -c "… stat -c %Y '$bk' …"`. That directory is bind-mounted
# read-write into the container, so a compromised poolhound could create
# `poolhound-$(whatever).tgz` and have it run as root on the host the next time
# anybody ran --check. Container to host, through the check added to prove the
# backups were real.
#
# So: the name is chosen entirely on the far side, comes back as a BASENAME
# only, and is refused here unless it matches the shape bin/backup writes —
# `poolhound-YYYYMMDD-HHMMSS.tgz` and nothing else. Only after that is it
# allowed to appear in a command, where it is additionally quoted and guarded
# with `--`.
c "the newest tarball on the server"
name=$("${SSH[@]}" "$HOST_IP" \
  'sudo sh -c '"'"'ls -t /var/lib/poolhound/backups/poolhound-*.tgz 2>/dev/null | head -1'"'"'' \
  2>/dev/null | tr -d '\r' | tail -1 | sed 's|.*/||')

[ -n "$name" ] || die "NO BACKUP EXISTS on the server in $REMOTE_DIR
  Nothing to pull, and nothing protecting this pool's history.
  Run it there:  sudo \$STACK/poolhound/collect.sh backup"

case "$name" in
  poolhound-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9].tgz) ;;
  *) die "the server offered a filename this will not use: $(printf '%q' "$name")
  bin/backup writes poolhound-YYYYMMDD-HHMMSS.tgz. Anything else is either a
  change nobody told this script about or a name somebody chose." ;;
esac

# The age of the file ON THE SERVER. Graded below, after the copy, because a
# perfect copy of a stale tarball is still a stale backup — and reporting the
# copy as the success would be the "a skip is not a success" defect again.
# Only an integer comes back, and it is matched as one.
remote_h=$("${SSH[@]}" "$HOST_IP" \
  'sudo sh -c '"'"'f=$(ls -t /var/lib/poolhound/backups/poolhound-*.tgz 2>/dev/null | head -1); [ -n "$f" ] || exit 0; echo $(( ( $(date +%s) - $(stat -c %Y -- "$f") ) / 3600 ))'"'"'' \
  2>/dev/null | tr -d '\r' | tail -1)
case "$remote_h" in ''|*[!0-9]*) remote_h="" ;; esac

ok "$name${remote_h:+, ${remote_h}h old on the server}"

# ---------------------------------------------------------------- the copy
#
# To a .part file first, verified, then moved into place. A half-written
# tarball that carries the final name is worse than no tarball at all: it is
# the only copy somebody will reach for, and it will not open. The same
# argument the agent's guard file makes about an atomic write, for the same
# reason.
target="$DEST/$name"
part="$target.part"
if [ -f "$target" ]; then
  ok "already here — not re-fetching $(du -h "$target" | cut -f1 | tr -d ' ')"
else
  c "copying"
  rm -f "$part"
  # `cat --` with the validated basename. Nothing on the far side re-parses it.
  "${SSH[@]}" "$HOST_IP" "sudo cat -- '$REMOTE_DIR/$name'" > "$part" \
    || { rm -f "$part"; die "the copy failed; nothing was left behind"; }

  [ -s "$part" ] || { rm -f "$part"; die "the copy produced an empty file"; }

  # DOES IT OPEN, AND IS THERE A POOL IN IT? `tar -tzf` succeeding says the
  # gzip stream is intact. It does not say the archive holds anything worth
  # keeping — and this checkout's own data directory would produce a tarball
  # that opens and contains runs.csv alone, which is exactly the shape of a
  # backup that looks fine and is not one.
  inside=$(tar -tzf "$part" 2>/dev/null) \
    || { rm -f "$part"; die "the copy will not open as a gzip tarball"; }
  printf '%s\n' "$inside" | grep -qx "$WANT_INSIDE" \
    || { rm -f "$part"
         die "the tarball opens but has no $WANT_INSIDE in it — that is not this pool.
  What it holds: $(printf '%s' "$inside" | tr '\n' ' ')"; }

  mv -f "$part" "$target"
  chmod 600 "$target" 2>/dev/null || true
  ok "$(du -h "$target" | cut -f1 | tr -d ' ') verified, $(printf '%s' "$inside" | wc -l | tr -d ' ') file(s) inside"
fi

# ---------------------------------------------------------------- prune
#
# A disk that fills is an outage, which is the reason bin/backup prunes its own
# directory on the far side. Same window here. Only this script's own naming
# is ever considered, so nothing else in the directory can be deleted by it.
gone=0
while IFS= read -r old; do
  [ -n "$old" ] || continue
  rm -f -- "$old" && gone=$((gone+1))
done <<< "$(find "$DEST" -maxdepth 1 -name 'poolhound-*.tgz' -type f \
            -mtime +"$KEEP_DAYS" 2>/dev/null)"
[ "$gone" -eq 0 ] || ok "pruned $gone older than $KEEP_DAYS days"

# ---------------------------------------------------------------- the verdict
#
# THE LAST LINE IS ABOUT THE POOL, NOT ABOUT THIS SCRIPT. Exiting 0 here means
# there is a current copy of this household's history on this machine. It does
# NOT mean the download worked, because the download working over a stale
# tarball is a thing that can happen every night for a month.
n=$(ls -1 "$DEST"/poolhound-*.tgz 2>/dev/null | wc -l | tr -d ' ')
if [ -n "$remote_h" ] && [ "$remote_h" -gt "$STALE_H" ]; then
  warn "the server's newest tarball is ${remote_h}h old — its nightly job is not running"
  warn "  this copy is current with the SERVER and neither is current with the pool"
  warn "  check there: sudo \$STACK/poolhound/collect.sh backup"
  exit 1
fi
ok "$n copy(ies) in $DEST — this pool's history now exists somewhere the VM cannot take with it"
