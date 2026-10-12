#!/usr/bin/env bash
# Build AqualinkD with the panel-programs patch, for the Pi.
#
# WHY A PATCH AND NOT A FORK
#
# AqualinkD can switch the panel's circuits and keep its own cron schedules, but
# it cannot read the programs stored IN the panel -- the ones that keep running
# when the Pi, AqualinkD or the server are down. pda-programs.patch adds that:
# a PDA programmer thread that walks MENU > PROGRAM read-only, and
# GET /api/programs and /api/programs/refresh. It is written against one tagged
# release, verified by checksum, so what is built here is exactly what was
# reviewed; README.md in this directory says why each piece is shaped as it is.
#
# THE TEST HARNESS IS A GATE. extras/test_pda_programs.c feeds screens captured
# off this panel through AqualinkD's own menu tracker and the new parser. A
# build whose harness fails produces no binary at all.
#
#   ./deploy/aqualinkd/build.sh            build into a fresh directory, print it
#   OUT=/some/dir ./deploy/aqualinkd/build.sh
#
# Needs docker. On an arm64 machine (an Apple-silicon Mac) the container is
# native; elsewhere docker emulates arm64, slowly. Built on Debian bookworm, so
# the binary runs on bookworm and on anything newer, the Pi's trixie included.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TAG="v3.1.0"
URL="https://github.com/aqualinkd/AqualinkD/archive/refs/tags/$TAG.tar.gz"
SHA256="58b7cae159d6bdf5671fe07456da7668e04e5ca985dc49a3506ae59a6f945787"
IMAGE="debian:bookworm-slim"
OUT="${OUT:-$(mktemp -d "${TMPDIR:-/tmp}/aqualinkd-programs.XXXXXX")}"

c()   { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || die "docker is required"
mkdir -p "$OUT"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/aqualinkd-src.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

c "AqualinkD $TAG"
curl -fsSL "$URL" -o "$WORK/src.tgz" || die "could not download $URL"
got="$(shasum -a 256 "$WORK/src.tgz" | cut -d' ' -f1)"
[[ "$got" == "$SHA256" ]] || die "checksum mismatch: got $got, want $SHA256"
ok "checksum matches"
tar -xzf "$WORK/src.tgz" -C "$WORK"
SRC="$WORK/AqualinkD-${TAG#v}"
[[ -d "$SRC/source" ]] || die "unexpected archive layout"

c "Applying pda-programs.patch"
(cd "$SRC" && patch -p1 --forward --quiet < "$HERE/pda-programs.patch") \
  || die "the patch does not apply to $TAG"
ok "applied"

c "Building for arm64 in $IMAGE"
# Single quotes: nothing in this command is expanded on THIS machine.
docker run --rm --platform linux/arm64 -v "$SRC":/w -w /w "$IMAGE" sh -c '
  set -e
  apt-get update -qq >/dev/null
  apt-get install -y -qq --no-install-recommends gcc make libc6-dev libsystemd-dev >/dev/null 2>&1
  make -j4 >/w/build.log 2>&1 || { tail -30 /w/build.log; exit 1; }
  F="-D AQ_PDA -D AQ_MANAGER -D MG_TLS=3 -D MG_ENABLE_SSI=0 -I./source"
  OBJS=$(ls build/*.o | grep -v -e /aqualinkd.o -e /rs485mon.o)
  gcc -Wall -O1 $F -o /w/test_pda_programs extras/test_pda_programs.c $OBJS -lpthread -lm -lrt -lsystemd
  /w/test_pda_programs
' || die "build or test harness failed; no binary written"

cp "$SRC/release/aqualinkd" "$OUT/aqualinkd"
shasum -a 256 "$OUT/aqualinkd" | tee "$OUT/aqualinkd.sha256"
ok "built: $OUT/aqualinkd"
echo "$OUT"
