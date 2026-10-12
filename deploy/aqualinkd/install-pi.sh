#!/usr/bin/env bash
# Put a patched AqualinkD on the Pi, check it, or put the stock one back.
#
#   PI_HOST=<ip> PI_KEY=~/.ssh/<key> ./deploy/aqualinkd/install-pi.sh --check
#   PI_HOST=<ip> PI_KEY=~/.ssh/<key> ./deploy/aqualinkd/install-pi.sh <dir from build.sh>
#   PI_HOST=<ip> PI_KEY=~/.ssh/<key> ./deploy/aqualinkd/install-pi.sh --rollback
#
# THE STOCK BINARY IS KEPT, ONCE. The first install copies the running
# /usr/local/bin/aqualinkd to aqualinkd.stock and never overwrites that copy
# again, so --rollback always means "what was there before any of this", not
# "the previous patched build".
#
# AqualinkD is restarted, which drops the panel link for a few seconds. The
# panel runs its own programs regardless, and the agent's samples and commands
# resume when AqualinkD answers again.
set -euo pipefail

PI_HOST="${PI_HOST:?set PI_HOST to the Pi address}"
PI_USER="${PI_USER:-$(whoami)}"
PI_KEY="${PI_KEY:-$HOME/.ssh/id_ed25519}"
BIN=/usr/local/bin/aqualinkd
SSH=(ssh -i "$PI_KEY" -o BatchMode=yes -o ConnectTimeout=10 "$PI_USER@$PI_HOST")

c()   { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m  !\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# What is running, and can it read the panel's programs. Read-only.
check() {
  c "AqualinkD on $PI_HOST"
  "${SSH[@]}" "sha256sum $BIN | cut -c1-16; ls $BIN.stock >/dev/null 2>&1 && echo 'stock copy kept' || echo 'no stock copy (never patched)'; systemctl is-active aqualinkd"
  local ans
  ans="$("${SSH[@]}" "curl -s -m 5 localhost:8000/api/programs" || true)"
  if [[ "$ans" == *'"type":"programs"'* ]]; then
    ok "/api/programs answers: ${ans:0:160}"
  else
    warn "/api/programs does not answer as the patch does: this is stock AqualinkD"
  fi
}

restart_and_wait() {
  "${SSH[@]}" "sudo systemctl restart aqualinkd"
  for _ in $(seq 1 20); do
    sleep 2
    if "${SSH[@]}" "curl -s -m 3 -o /dev/null -w '%{http_code}' localhost:8000/api/devices" | grep -q 200; then
      ok "AqualinkD answering again"
      return 0
    fi
  done
  die "AqualinkD did not answer within 40s of a restart — run --rollback"
}

case "${1:-}" in
  --check) check; exit 0 ;;
  --rollback)
    c "Restoring the stock AqualinkD"
    "${SSH[@]}" "test -f $BIN.stock" || die "no $BIN.stock on the Pi; nothing to roll back to"
    "${SSH[@]}" "sudo install -m 755 $BIN.stock $BIN"
    restart_and_wait
    check
    exit 0 ;;
  ""|-*) sed -n 2,6p "$0"; exit 2 ;;
esac

DIR="$1"
[[ -f "$DIR/aqualinkd" && -f "$DIR/aqualinkd.sha256" ]] || die "$DIR has no aqualinkd from build.sh"
want="$(cut -d' ' -f1 "$DIR/aqualinkd.sha256")"
[[ "$(shasum -a 256 "$DIR/aqualinkd" | cut -d' ' -f1)" == "$want" ]] || die "$DIR/aqualinkd does not match its checksum"

c "Installing the patched AqualinkD on $PI_HOST"
scp -q -i "$PI_KEY" -o BatchMode=yes "$DIR/aqualinkd" "$PI_USER@$PI_HOST:/tmp/aqualinkd.new"
# The hash is checked again on the far side: scp to a card that has already
# destroyed two is not something to take on trust.
"${SSH[@]}" "echo '$want  /tmp/aqualinkd.new' | sha256sum -c --quiet" || die "the copy on the Pi does not match"
"${SSH[@]}" "test -f $BIN.stock || sudo cp -p $BIN $BIN.stock"
ok "stock binary kept as $BIN.stock"
"${SSH[@]}" "sudo install -m 755 /tmp/aqualinkd.new $BIN && rm -f /tmp/aqualinkd.new"
restart_and_wait
check
