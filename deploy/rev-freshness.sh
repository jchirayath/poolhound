#!/usr/bin/env bash
# =============================================================================
# rev-freshness.sh — is a deployed REVISION the code that is supposed to be
# running, or only the code that happens to be?
#
#   ./deploy/rev-freshness.sh [--no-fetch] [-C repo] [--what LABEL] REVISION
#
# Exit 0 when the deployed revision is current (warnings may still be printed),
# 1 when it is not, or when that cannot be established. Called by
# `bootstrap-server.sh --check` for the server and `update-pi.sh --check` for
# the Pi; the comparison lives here, once, so the two cannot grade the same
# drift differently, and so bin/selftest can drive it against a scratch
# repository — neither caller can be run without the real hosts behind it.
#
# WHY THIS EXISTS
#
# `bootstrap-server.sh --check` compared the image's REVISION against the two
# rendered pages, which answers "is what is deployed WORKING". It never asked
# "is what is deployed CURRENT", and the two are different questions with the
# same green tick. MEASURED on 2026-09-28: the server was found three commits
# behind, every image build having failed for four days, while --check printed
# "served public page is the deployed build (<old rev>)" — which was true.
#
# TWO REFERENCES, BECAUSE THEY DRIFT APART. "Current" means current with
# origin/main (what has been merged and pushed) AND with this checkout's local
# main (what is about to be). Pushing and deploying are separate acts here:
# origin was once 22 commits behind local main while the server was current,
# and a check against only one of the two would have called that state either
# entirely fine or entirely broken.
#
# THE REVISION IS UNTRUSTED INPUT. It is a file read back off another machine,
# and nothing on that machine is in a position to vouch for it — the server's
# source directory is root-owned but a compromised container is exactly the
# actor CLAUDE.md's backup-filename paragraph is about. So it is matched
# against the only shapes `git describe --always --dirty --tags` writes BEFORE
# git sees any of it, it is never placed in a shell string, and what is printed
# back of an unrecognised one is reduced to a safe alphabet first.
# =============================================================================
set -euo pipefail

ok()  { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m  !\033[0m %s\n' "$*"; }

FETCH=1
REPO="$(cd "$(dirname "$0")/.." && pwd)"
WHAT="the deployed build"
while [[ $# -gt 1 ]]; do
  case "$1" in
    --no-fetch) FETCH=0; shift ;;
    -C)         REPO="$2"; shift 2 ;;
    --what)     WHAT="$2"; shift 2 ;;
    *)          break ;;
  esac
done
[[ $# -eq 1 ]] || { echo "usage: $0 [--no-fetch] [-C repo] [--what LABEL] REVISION" >&2; exit 2; }
rev="$1"

# How many subjects to list before saying how many were left out. A list that
# stops at ten without saying so is the truncation CLAUDE.md forbids.
CAP=10

fails=0
fail(){ warn "$*"; fails=$((fails+1)); }
g()   { git -C "$REPO" "$@"; }

# ---------------------------------------------------------------- the shape
# `git describe --always --dirty --tags` writes one of three things:
#   abc1234            a bare abbreviated hash (no tag reachable — this repo)
#   v1.2-3-gabc1234    a tag, a distance and a hash
#   v1.2               a tag, exactly, when HEAD is the tagged commit
# each optionally followed by -dirty. The third carries no hash and cannot be
# compared without trusting a name read off another machine, so it is REFUSED
# rather than resolved: loudly unanswerable is better than quietly wrong, and
# this repository has no tags today. Validate first; git sees only the hex.
re_hex='^([0-9a-f]{7,40})(-dirty)?$'
re_desc='^[A-Za-z0-9._/+-]+-[0-9]+-g([0-9a-f]{7,40})(-dirty)?$'
sha="" dirty=""
if [[ "$rev" =~ $re_hex ]]; then
  sha="${BASH_REMATCH[1]}"; dirty="${BASH_REMATCH[2]}"
elif [[ "$rev" =~ $re_desc ]]; then
  sha="${BASH_REMATCH[1]}"; dirty="${BASH_REMATCH[2]}"
else
  shown=$(printf '%s' "$rev" | LC_ALL=C tr -cd 'A-Za-z0-9._+-' | cut -c1-60)
  fail "UNRECOGNISED REVISION — $WHAT carries '${shown}', which is not a revision git describe writes"
  warn "  cannot tell whether it is current; redeploy from a clean checkout of main to stamp it"
  exit 1
fi

# ---------------------------------------------------------------- the fetch
# READ-ONLY: it moves refs/remotes/origin/main in this clone and touches no
# branch, no working tree and nothing on the remote.
#
# A FAILED FETCH IS A FAILURE, not a note. The same rule --check already
# applies to a missing REVISION: "cannot tell" is the same state as "does not
# match" for anybody relying on the answer, and an answer computed against an
# origin/main last fetched a month ago would grade a month-old server current.
# The comparison still runs against the copy this clone has, labelled as such,
# because a stale answer that says it is stale is still worth reading.
origin_note=""
if (( FETCH )); then
  if GIT_TERMINAL_PROMPT=0 g -c http.lowSpeedLimit=1 -c http.lowSpeedTime=20 \
       fetch --quiet origin main 2>/dev/null; then
    :
  else
    fail "could not fetch origin — 'is it current' is answered against this clone's LAST-FETCHED origin/main"
    origin_note=" (as last fetched — may itself be stale)"
  fi
fi

has_ref(){ g rev-parse --verify --quiet "$1^{commit}" >/dev/null; }
ORIGIN=refs/remotes/origin/main
MAIN=refs/heads/main

full=$(g rev-parse --verify --quiet "${sha}^{commit}" 2>/dev/null || true)
if [[ -z "$full" ]]; then
  # Not here at all — not merely "not on main". An abbreviation that matches
  # two objects lands here too, which is the right answer: it names nothing.
  fail "UNKNOWN COMMIT — $WHAT is $rev, and this clone has no such commit"
  warn "  built from a commit never pushed to origin, from another machine's tree, or one since rewritten"
  exit 1
fi

if [[ -n "$dirty" ]]; then
  # DISTINCT FROM BEHIND. A dirty tree is code no commit records: the hash says
  # where it started, and nothing says what was on top. It can be on main's tip
  # and still be something nobody can rebuild.
  fail "DEPLOYED FROM A DIRTY TREE — $WHAT is $rev: uncommitted changes on top of ${sha}, recorded nowhere"
fi

# relation SHA REF -> "equal" | "behind N" | "ahead N" | "diverged"
relation() {
  local tip; tip=$(g rev-parse --verify --quiet "$2^{commit}")
  if [[ "$1" == "$tip" ]]; then echo equal
  elif g merge-base --is-ancestor "$1" "$tip"; then echo "behind $(g rev-list --count "$1..$tip")"
  elif g merge-base --is-ancestor "$tip" "$1"; then echo "ahead $(g rev-list --count "$tip..$1")"
  else echo diverged
  fi
}

# list_capped "<header>" <git log revision args...>
list_capped() {
  local header="$1"; shift
  local all n; all=$(g log --oneline --no-decorate "$@")
  n=$(printf '%s\n' "$all" | grep -c . || true)
  (( n > 0 )) || return 0
  warn "  $header"
  printf '%s\n' "$all" | head -n "$CAP" | sed 's/^/        /'
  if (( n > CAP )); then
    warn "  … and $((n - CAP)) more not shown"
  fi
}

short=$(g rev-parse --short "$full")
missing=()        # refs the deployed build is missing commits from

# ------------------------------------------------- against origin/main
# behind   -> FAILURE: merged, pushed, and not running. The 2026-09-28 state.
# diverged -> FAILURE, distinct: the server runs a commit main does not hold —
#             an unmerged branch, or a main since rewritten under it.
# ahead    -> WARNING: the server runs commits origin has never seen. Nothing
#             is wrong with the deployment; the push has drifted, and the only
#             copy of what is serving is on one workstation.
if ! has_ref "$ORIGIN"; then
  fail "this clone has no origin/main — cannot tell whether $WHAT is current"
else
  r=$(relation "$full" "$ORIGIN")
  case "$r" in
    equal)    ok "$WHAT ($short) is origin/main${origin_note}" ;;
    behind*)  fail "BEHIND — $WHAT ($short) is ${r#behind } commit(s) behind origin/main${origin_note}"
              missing+=("$ORIGIN") ;;
    ahead*)   warn "UNPUSHED — $WHAT ($short) is ${r#ahead } commit(s) AHEAD of origin/main${origin_note}"
              warn "  it serves code origin does not hold; push main" ;;
    diverged) fail "NOT ON MAIN — $WHAT ($short) is not an ancestor of origin/main${origin_note}"
              list_capped "deployed, and not on origin/main:" "$ORIGIN..$full"
              missing+=("$ORIGIN") ;;
  esac
fi

# ------------------------------------------------- against local main
# Graded the same way, because local main is what the next deploy ships: a
# deployment behind it is one somebody has merged for and not shipped. AHEAD
# of local main only means this checkout has not been pulled, which says
# nothing about the server, so it is a warning.
if ! has_ref "$MAIN"; then
  warn "this clone has no local main — compared against origin/main only"
else
  r=$(relation "$full" "$MAIN")
  case "$r" in
    equal)    ok "$WHAT ($short) is this checkout's main" ;;
    behind*)  fail "BEHIND — $WHAT ($short) is ${r#behind } commit(s) behind this checkout's main"
              missing+=("$MAIN") ;;
    ahead*)   warn "this checkout's main is ${r#ahead } commit(s) behind $WHAT — pull before deploying from here" ;;
    diverged) fail "NOT ON MAIN — $WHAT ($short) is not an ancestor of this checkout's main"
              missing+=("$MAIN") ;;
  esac
fi

if (( ${#missing[@]} )); then
  # One list for both refs, because the commits a server is missing from
  # origin/main and from main are usually the same commits, and printing them
  # twice makes a short list look long.
  list_capped "merged and not deployed (git log --oneline $short..main):" "${missing[@]}" "^$full"
fi

# ------------------------------------------------- the push itself
# A WARNING whatever it finds. This is about the workstation and the remote,
# not about anything deployed — but it is the drift that made "current" need
# two references in the first place, and nobody checks it unless asked.
if has_ref "$ORIGIN" && has_ref "$MAIN"; then
  r=$(relation "$(g rev-parse "$MAIN")" "$ORIGIN")
  case "$r" in
    ahead*)   warn "PUSH HAS DRIFTED — local main is ${r#ahead } commit(s) ahead of origin/main" ;;
    behind*)  warn "local main is ${r#behind } commit(s) behind origin/main — git pull" ;;
    diverged) warn "local main and origin/main have DIVERGED — neither contains the other" ;;
  esac
fi

(( fails == 0 ))
