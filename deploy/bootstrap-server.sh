#!/usr/bin/env bash
# =============================================================================
# bootstrap-server.sh — stand poolhound up on a host that has nothing on it.
#
#   ./bootstrap-server.sh            provision, deploy, verify
#   ./bootstrap-server.sh --check    verify only, change nothing
#
# THE POINT OF THIS FILE
# the server is disposable. If it is rebuilt, or replaced, or moved to another
# region, this script puts poolhound back — and the pool's history comes back
# with it, because the history was never on the server in the first place. It lives
# on an Azure Files share and the passwords live in Key Vault; this host holds
# neither a copy of the data nor a key to the secrets.
#
# WHAT IS AND IS NOT RECREATED
#   recreated here : the mount, the container, the vhost, the cron, the image
#   NOT recreated  : the CSVs, the config, the passwords, the agent token
#
# The second list is the valuable one, and none of it is in this repo. That is
# the design. A script that could recreate the secrets would be a script that
# contains them.
#
# PREREQUISITES
#   az login, with Virtual Machine Administrator Login on the VM and
#   Key Vault Secrets Officer on the vault (only needed to CHANGE secrets;
#   the VM reads them with its own managed identity and needs nothing from you).
# =============================================================================
set -euo pipefail

# WHERE THIS DEPLOYMENT LIVES IS NOT IN THIS REPOSITORY.
#
# Put it in deploy/server.env, which is gitignored, and this reads it. The
# defaults below are placeholders: naming a real resource group, storage account
# and key vault in a public repo does not hand anybody a credential, but it does
# hand them a map, and there is no reason to.
#
#   cp deploy/server.env.example deploy/.env   # then fill it in
#
# EITHER NAME. compose.yml is pointed at it with --env-file and the convention
# there is `.env`; this script predates that and read `server.env`. Reading
# both means one file configures both paths, which is the whole point of
# having one file.
for _envf in .env server.env; do
  [ -f "$(dirname "$0")/$_envf" ] && . "$(dirname "$0")/$_envf" && break
done

RG="${RG:-CHANGE-ME-resource-group}"
VM="${VM:-CHANGE-ME-vm}"
HOST_IP="${SERVER_IP:-CHANGE-ME-ip}"
STACK="${STACK:-/home/azureuser/stack}"
STORAGE="${STORAGE:-CHANGE-ME-storage-account}"
SHARE="${SHARE:-poolhound}"
VAULT="${VAULT:-CHANGE-ME-key-vault}"
# The vhost is a TEMPLATE carrying @@placeholders@@, and these fill them. No
# CHANGE-ME default: a vhost installed with a placeholder still in it is a
# Caddyfile that will not adapt, and failing here names the setting instead.
PUBLIC_HOSTS="${PUBLIC_HOSTS:-}"
OAUTH2_HOST="${OAUTH2_HOST:-}"
# WHAT THIS HOST IS CALLED, and what its containers are prefixed with.
#
# Both were spelled out in this file — the container prefix thirteen times —
# and both are facts about one person's deployment that CLAUDE.md says no
# tracked file may carry. The Compose project name is the stack directory by
# another route, which is the second of the two the rule names.
#
# Defaults are the LOGICAL names, so a fresh clone reads sensibly and a
# misconfigured one fails by looking for a container that does not exist
# rather than by finding somebody else's.
SERVER_NAME="${SERVER_NAME:-webhost}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-poolhound}"
PH_CTR="${COMPOSE_PROJECT}-poolhound-1"
CADDY_CTR="${COMPOSE_PROJECT}-caddy-1"
# The first host, which is the one the verifier fetches and the one the
# compose service is told to answer on.
PRIMARY_HOST="$(printf '%s' "$PUBLIC_HOSTS" | cut -d, -f1 | tr -d ' ')"
SSHCFG="${SERVER_SSH_CONFIG:-/tmp/poolhound-ssh.config}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"

c()   { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m  !\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# Which private routes is a given Caddy running-config NOT gating?
#
# ONE IMPLEMENTATION, because there are two callers -- the post-deploy reload
# check and the verify step -- and when they were written separately they
# disagreed about the same proxy on the same run: one printed "Caddy is serving
# the shipped rules" while the other named three routes it was not. They were
# both wrong. The first tested five hand-typed paths (its own derived list was
# computed on the line above and then ignored); the second derived all fifteen
# from access.NEEDS but matched them as literal strings, so the routes gated by
# `path /api/chemical /api/chemical/*` were reported missing on every run for
# months while the proxy gated them correctly.
#
# A route is gated if the config names it, or names a wildcard above it. Python
# because this is prefix logic over a 36 KB JSON blob, and bash substring
# matching is precisely what got it wrong.
caddy_gaps() {
  # PYTHONPATH, AND NO 2>/dev/null. The heredoc used neither, so `from poolhound
  # import access` succeeded only when the operator happened to be standing in
  # the checkout -- bin/* reach the package via sys.path.insert and this did
  # not, and the package is not installed into the venv. Run the same script by
  # absolute path from anywhere else and the import raised, the bare `except`
  # swallowed it, and the check silently measured SIX hand-typed routes instead
  # of fifteen. That fallback IS the bug this function's header says it removed,
  # reintroduced quietly. Nine private routes, both credential routes among
  # them, went unchecked with the output identical either way.
  PYTHONPATH="$SRC" "$SRC/.venv/bin/python" - "$1" <<'PYEOF'
import re, sys
try:
    from poolhound import access, server
    want = sorted(set(access.NEEDS) - set(server.PUBLIC_POST) - {"/api/health"})
    derived = True
except Exception as e:
    # Still check SOMETHING rather than nothing -- but say so, loudly, on
    # stderr, because a quieter answer from a smaller list is the failure mode.
    print(f"caddy_gaps: could not import poolhound ({e}); falling back to a "
          f"hand-typed list of 6 routes instead of the full table", file=sys.stderr)
    want = ["/api/reading", "/api/commands", "/api/export", "/api/settings",
            "/api/control", "/api/chemical"]
    derived = False
cfg = sys.argv[1] if len(sys.argv) > 1 else ""
have = set(re.findall(r'"(/api/[A-Za-z0-9/*-]*)"', cfg))

def gated(route):
    if route in have:
        return True
    parts = route.strip("/").split("/")
    for i in range(len(parts), 0, -1):
        if "/" + "/".join(parts[:i - 1] + ["*"]) in have:
            return True
        if "/" + "/".join(parts[:i]) + "/*" in have:
            return True
    return False

print(" ".join(r for r in want if not gated(r)))
print(f"caddy_gaps: checked {len(want)} route(s)"
      f"{'' if derived else ' (FALLBACK LIST)'}", file=sys.stderr)
PYEOF
}

# Which DELIBERATELY PUBLIC routes has the proxy gated by mistake?
#
# THE CHECK ABOVE IS ONE-DIRECTIONAL and that is half a check. It asks, of every
# private route, "is this gated?", and no answer to that question can fail when
# the matcher is too WIDE. Widening it to `path /api/pool-shape
# /api/pool-shape/*` -- the exact regression the vhost warns about three lines
# above itself, which is why that one path carries no trailing wildcard -- puts
# /api/pool-shape/compute behind Entra. The public volume calculator then stops
# answering for every anonymous visitor, on a page offered to everybody, and
# this script reported green.
#
# Both routes are pure: they read nothing, write nothing, keep nothing, and
# carry their own rate limit. server.PUBLIC_POST is the one place that decides
# which those are, so it is the one place this reads from as well.
caddy_overgated() {
  PYTHONPATH="$SRC" "$SRC/.venv/bin/python" - "$1" <<'PYEOF'
import re, sys
try:
    from poolhound import server
    want = sorted(server.PUBLIC_POST)
except Exception as e:
    # SAY SO, LOUDLY, and check nothing rather than check a guess. The gaps
    # function's fallback list taught this the hard way: a quieter answer from
    # a smaller list reads identically to a correct one.
    print(f"caddy_overgated: could not import poolhound ({e}); the public "
          f"routes were NOT checked", file=sys.stderr)
    sys.exit(0)
cfg = sys.argv[1] if len(sys.argv) > 1 else ""
have = set(re.findall(r'"(/api/[A-Za-z0-9/*-]*)"', cfg))

def gated(route):
    if route in have:
        return True
    parts = route.strip("/").split("/")
    for i in range(len(parts), 0, -1):
        if "/" + "/".join(parts[:i - 1] + ["*"]) in have:
            return True
        if "/" + "/".join(parts[:i]) + "/*" in have:
            return True
    return False

print(" ".join(r for r in want if gated(r)))
print(f"caddy_overgated: checked {len(want)} public route(s)", file=sys.stderr)
PYEOF
}

# Which collectors is the share NOT reporting on at all?
#
# ABSENCE WAS SILENCE. The collector check iterated whatever files happened to
# be in .collector-status/ and judged each one, so a collector that stops
# writing its status file altogether stops APPEARING rather than failing --
# measured by deleting one line: fails=0, and the run printed "Done -- nothing
# lost." The remaining green lines carried it.
#
# deploy/poolhound-cron declares exactly which collectors run, and this check
# never read it. Derived from that file rather than typed here, because the
# whole defect is an inventory that was not the real one: a fifth collector
# added to cron is checked the day it is added.
collector_roster() {
  # Schedule lines only. That file's own header prose says "collect.sh records
  # each run's exit status", and a grep over the whole thing reads `records` as
  # a collector.
  grep -v '^[[:space:]]*#' "$SRC/deploy/poolhound-cron" \
    | grep -oE 'collect\.sh[[:space:]]+[a-z-]+' \
    | awk '{print $2}' | sort -u
}

collector_gaps() {   # collector_gaps "<statuses blob>"
  local statuses="$1" tool
  for tool in $(collector_roster); do
    printf '%s\n' "$statuses" | cut -f1 | grep -qx "$tool" || printf ' %s' "$tool"
  done
}

CHECK=0
[[ "${1:-}" == "--check" ]] && CHECK=1

command -v az >/dev/null || die "azure-cli required"
AZ_WHO=$(az account show --query user.name -o tsv 2>/dev/null) \
  || die "not logged in — run: az login"

# `az ssh config` once sat here for fifty-three minutes with both of its streams
# sent to /dev/null. The deploy looked hung and could not say why, and nothing it
# printed — a prompt, an error, a progress line — reached anybody. Whatever wedged
# it, silence was ours. So: bound every az call, keep what it said, and do not ask
# for a new certificate while the one on disk is still good.
bounded() {                      # bounded SECONDS cmd...  (no coreutils timeout here)
  local secs="$1"; shift
  "$@" & local pid=$!
  ( sleep "$secs"; kill -TERM "$pid" 2>/dev/null
    sleep 5;       kill -KILL "$pid" 2>/dev/null ) & local dog=$!
  local rc=0; wait "$pid" || rc=$?
  kill "$dog" 2>/dev/null; wait "$dog" 2>/dev/null
  return "$rc"
}

cert_until() {                   # the expiry stamped into the certificate itself
  local cert; cert=$(awk -F'"' '/CertificateFile/{print $2; exit}' "$1" 2>/dev/null)
  [[ -s "$cert" ]] || return 1
  ssh-keygen -L -f "$cert" 2>/dev/null | awk '/Valid:/{print $5; exit}'
}

cert_good() {                    # valid, with ten minutes still to spend
  local until_ exp now
  until_=$(cert_until "$1") || return 1
  [[ -n "$until_" ]] || return 1
  exp=$(date -j -f '%Y-%m-%dT%H:%M:%S' "$until_" '+%s' 2>/dev/null) \
    || exp=$(date -d "$until_" '+%s' 2>/dev/null) || return 1
  now=$(date '+%s')
  (( exp - now > 600 ))
}

c "Entra SSH certificate for $AZ_WHO"
AZLOG="${TMPDIR:-/tmp}/poolhound-az-ssh.log"
if cert_good "$SSHCFG"; then
  ok "reusing the certificate in $SSHCFG — valid until $(cert_until "$SSHCFG")"
else
  bounded 120 az ssh config --file "$SSHCFG" --resource-group "$RG" --name "$VM" \
          --overwrite >"$AZLOG" 2>&1 \
    || { [[ -s "$AZLOG" ]] && sed 's/^/    /' "$AZLOG" >&2
         die "az ssh config did not finish within 120s (or failed) — needs Virtual Machine Administrator Login; its output is in $AZLOG"; }
  ok "minted a certificate — valid until $(cert_until "$SSHCFG")"
fi
SSH=(ssh -F "$SSHCFG" -o BatchMode=yes -o ConnectTimeout=20)
"${SSH[@]}" "$HOST_IP" true || die "cannot reach the server at $HOST_IP"
ok "authenticated as $("${SSH[@]}" "$HOST_IP" whoami)"

# ── the durable pieces: create only if absent ────────────────────────────────
if (( ! CHECK )); then
  c "Azure Files share (the data)"
  if az storage share-rm show -g "$RG" --storage-account "$STORAGE" -n "$SHARE" >/dev/null 2>&1; then
    ok "share $SHARE already exists — NOT touching it, it holds the history"
  else
    az storage share-rm create -g "$RG" --storage-account "$STORAGE" -n "$SHARE" \
      --quota 5 --enabled-protocols SMB -o none
    warn "share $SHARE CREATED EMPTY — restore the CSVs into it before starting"
  fi

  c "Key Vault (the passwords)"
  if az keyvault show -g "$RG" -n "$VAULT" >/dev/null 2>&1; then
    ok "vault $VAULT already exists"
  else
    az keyvault create -g "$RG" -n "$VAULT" -l westus3 \
      --enable-rbac-authorization true --retention-days 7 -o none
    warn "vault $VAULT CREATED EMPTY — load the secrets before the collectors run"
  fi

  c "Managed identity can read the vault"
  PRINCIPAL=$(az vm identity show -g "$RG" -n "$VM" --query principalId -o tsv 2>/dev/null || true)
  [[ -n "$PRINCIPAL" ]] || { az vm identity assign -g "$RG" -n "$VM" -o none
                             PRINCIPAL=$(az vm identity show -g "$RG" -n "$VM" --query principalId -o tsv); }
  SCOPE="/subscriptions/$(az account show --query id -o tsv)/resourceGroups/$RG/providers/Microsoft.KeyVault/vaults/$VAULT"
  az role assignment create --assignee-object-id "$PRINCIPAL" \
     --assignee-principal-type ServicePrincipal --role "Key Vault Secrets User" \
     --scope "$SCOPE" -o none 2>/dev/null || true
  ok "identity $PRINCIPAL may read secrets"

  # ── the host ──────────────────────────────────────────────────────────────
  c "Mounting the share on the server"
  KEY=$(az storage account keys list -g "$RG" -n "$STORAGE" --query "[0].value" -o tsv)
  # `|| mount_rc=$?` captures the status AND stops errexit pre-empting the
  # check below; testing $? on the next line is dead code under `set -e`.
  mount_rc=0
  "${SSH[@]}" "$HOST_IP" "sudo bash -s" <<EOF || mount_rc=$?
set -e
command -v mount.cifs >/dev/null || { apt-get update -qq; apt-get install -y cifs-utils; }
getent group poolhound >/dev/null || groupadd -g 1010 poolhound
id poolhound >/dev/null 2>&1 || useradd -u 1010 -g 1010 -M -s /usr/sbin/nologin poolhound
mkdir -p /etc/smbcredentials /mnt/poolhound
umask 077
printf 'username=%s\npassword=%s\n' '$STORAGE' '$KEY' > /etc/smbcredentials/poolhound.cred
chmod 600 /etc/smbcredentials/poolhound.cred
grep -q '/mnt/poolhound' /etc/fstab || echo '//$STORAGE.file.core.windows.net/$SHARE /mnt/poolhound cifs nofail,_netdev,credentials=/etc/smbcredentials/poolhound.cred,dir_mode=0770,file_mode=0660,uid=1010,gid=1010,serverino,nosharesock,actimeo=30,mfsymlinks 0 0' >> /etc/fstab
mountpoint -q /mnt/poolhound || mount -a
# ASSERT THE MOUNT. The fstab line above says nofail on purpose -- a boot must
# not hang on a missing share -- and the cost is that mount -a exits 0 when the
# CIFS mount fails, so set -e never fires. The deploy then printed
# "mounted: 29G 12% used" from df -h, which reports the ROOT filesystem when the
# path is not a mount point: a plausible number for a share that is not there.
# Everything after it -- seeding config.toml, starting the container -- then ran
# against an empty directory on local disk.
#
# NO BACKTICKS IN THIS COMMENT. It lives inside an UNQUOTED heredoc, so the
# LOCAL shell expands it before ssh ever sees it: the first draft wrote
# nofail, mount -a and set -e in backticks and ran all three on the developer's
# Mac, which is where mount_apfs errors in a deploy log come from.
# The backup destination, on the HOST disk. Created here with the container's
# uid because the bind mount is read-write from inside a process running as
# 1010, and docker would otherwise create it root-owned and unwritable.
mkdir -p /var/lib/poolhound/backups
chown 1010:1010 /var/lib/poolhound/backups
mountpoint -q /mnt/poolhound || {
  echo "the share did not mount at /mnt/poolhound (nofail hides this from mount -a)" >&2
  echo "  mount | grep poolhound ; systemctl status mnt-poolhound.mount" >&2
  exit 1
}
EOF
  [[ $mount_rc -eq 0 ]] || die "the share is not mounted — refusing to seed or start anything over it"
  ok "mounted: $("${SSH[@]}" "$HOST_IP" 'findmnt -n -o SIZE,USE% --target /mnt/poolhound | head -1')"

  c "Shipping the source"
  # The commit being shipped, written into the tree so the deployed copy can say
  # which build it is. .git is excluded from the rsync on purpose, so without
  # this the footer reads "v0.1.0" with no revision and there is no way to tell
  # a fresh page from a month-old one.
  # DEPENDENCIES, BEFORE THE IMAGE IS BUILT FROM THEM (VULN-3, SEC-15).
  #
  # Nothing in this repository scanned them until it was looked for, and the
  # answer was 2 Critical and 31 High: pyjwt carried a 9.1 as a transitive of
  # pycognito, and Pillow + pillow-heif — the two packages that decode bytes
  # posted by an ANONYMOUS caller at /api/photo — carried nineteen advisories
  # between them. All of it invisible because the Dockerfile re-resolves on
  # every build, which is the property that makes "rebuild from scratch" easy
  # and makes "what did we ship" unanswerable.
  #
  # "Did not run" is a third outcome and is printed as one.
  c "Dependencies"
  if command -v osv-scanner >/dev/null 2>&1; then
    if osv-scanner scan source --lockfile="requirements.txt:$SRC/deploy/requirements.txt" \
         >/tmp/poolhound-osv.log 2>&1; then
      ok "no known vulnerabilities in deploy/requirements.txt"
    else
      sed -n 's/^/    /p' /tmp/poolhound-osv.log | tail -20
      die "deploy/requirements.txt has known vulnerabilities — fix or pin before shipping"
    fi
  else
    warn "osv-scanner NOT INSTALLED — dependencies were not scanned (brew install osv-scanner)"
  fi

  REV="$(git -C "$SRC" describe --always --dirty --tags 2>/dev/null || echo unknown)"
  # Written for the rsync, then REMOVED. It was left in the checkout, and
  # version_string() prefers the file over git -- so every page rendered on the
  # developer's own machine afterwards was stamped with the commit of the last
  # deploy, for ever. The footer is documented as the only way a reader can tell
  # a fresh page from a month-old one, and on this machine it was wrong by
  # thirty-four commits.
  printf '%s\n' "$REV" > "$SRC/REVISION"
  trap 'rm -f "$SRC/REVISION"' EXIT
  "${SSH[@]}" "$HOST_IP" "sudo mkdir -p $STACK/poolhound/src && sudo chown -R azureuser $STACK/poolhound"
  rsync -az --delete --rsync-path="sudo rsync" -e "ssh -F $SSHCFG -o BatchMode=yes" \
    --exclude '.git' --exclude '.venv' --exclude 'data' --exclude 'site' \
    --exclude '__pycache__' --exclude '*.pyc' --exclude 'config/config.toml' \
    "$SRC/" "$HOST_IP:$STACK/poolhound/src/"
  ok "source on the server"

  # DRIVEN BEFORE SHIPPED. The eight most user-visible defects found in review
# were invisible to every static gate and to reading the diff: a status line
# that told anonymous visitors they were signed in, invisible spans scrolling
# the page sideways at phone width, an agent-presence check that ran once, and
# every accessibility defect. Each was found by asking a browser what it
# actually had.
#
# Skipped without a browser, loudly — a released copy must deploy without a
# JavaScript toolchain, and "did not run" has to be distinguishable from
# "passed", which is how two gates in this project came to run nothing at all.
if [[ -x "$SRC/.venv/bin/python" ]]; then
  if "$SRC/.venv/bin/python" "$SRC/bin/drive" >/tmp/poolhound-drive.log 2>&1; then
    ok "the pages behave in a browser at phone width"
  else
    if grep -q "no Playwright here" /tmp/poolhound-drive.log; then
      warn "bin/drive skipped: no browser on this machine (pip install playwright)"
    else
      warn "bin/drive FAILED — the built pages misbehave in a browser:"
      sed -n 's/^/    /p' /tmp/poolhound-drive.log | grep -E "FAIL|FAILURE" | head -8
      die "refusing to deploy a page that fails in a browser"
    fi
  fi
else
  # NO else HERE MEANT NO LINE AT ALL. Two lines above, the comment promises
  # this is "skipped without a browser, loudly" — and it was, for a missing
  # Playwright. A missing .venv skipped the whole browser gate and printed
  # nothing, so a deploy from any machine without one shipped with the gate
  # that catches every user-visible defect simply absent from the output.
  # "Did not run" is the third outcome and it has to be said out loud.
  warn "bin/drive did NOT run: no $SRC/.venv/bin/python on this machine"
  warn "  the browser gate was not applied to this build — create the venv"
  warn "  and install playwright, or run bin/drive on a workstation."
fi

# EVERY `docker compose` BELOW REDIRECTS ITS STDIN, and that is not decoration.
# This whole block is piped to `sudo bash -s`, so the remote shell is READING
# ITS OWN SCRIPT FROM STDIN. `docker compose exec` forwards stdin to the
# container, and `-T` only disables the TTY -- it does not detach the stream.
# So the first exec in the block SWALLOWED THE REST OF THE SCRIPT.
#
# MEASURED: the environment guard is the first exec, so everything after it --
# the two vhost copies, collect.sh, the crontab, logrotate, the compose patch,
# `docker compose build`, `docker compose up -d`, the validate and the reload --
# was eaten, every single deploy. bash then exited 0, because the last command
# it managed to read had succeeded, so ssh returned 0, no guard fired, and the
# run printed "service up" and went on to report a stale page it had no way to
# refresh. Verified by markers: M1 and M2 printed, M3 never existed.
#
# A command that reads stdin, inside a script that IS stdin, has to be told not
# to.
c "Compose service, vhost, cron and image"
  # `|| install_rc=$?` BOTH captures the status and stops errexit pre-empting
  # the check below. Testing $? on the next line would be dead code under
  # `set -e`, and testing nothing at all is what shipped.
  install_rc=0
  "${SSH[@]}" "$HOST_IP" "sudo bash -s" <<EOF || install_rc=$?
set -e
cd $STACK
# THE SITE AND THE AUTH SNIPPET, COPIED VERBATIM. The addresses are
# {\$POOLHOUND_PUBLIC_HOSTS} and Caddy expands that from its own environment
# when it adapts the config, so there is nothing to substitute here — which is
# also what lets compose.yml use the same two files unchanged. The Caddy
# service must therefore SEE those variables; on this stack they come from the
# compose file's environment block.
# AND CADDY MUST ACTUALLY SEE THOSE VARIABLES. {\$VAR} expands from the Caddy
# PROCESS's environment, not from this script's — so on a stack whose compose
# file predates this, the site address expands to the empty string and Caddy
# refuses the whole config, taking every other vhost on the host with it.
#
# CHECKED BEFORE THE COPY, WHICH IS THE WHOLE POINT. This sat immediately AFTER
# the two cp lines below and said "Refusing to install a vhost whose addresses
# would expand to nothing" — while the vhost was already on disk. MEASURED: a
# deploy against a stack whose caddy had no POOLHOUND_PUBLIC_HOSTS printed that
# refusal, exited 1, and left sites/poolhound.caddy opening with a bare " {".
# Caddy went on serving from the config already in memory, so every probe
# passed and the site stayed up; the next reload of ANY vhost on that host
# would have failed to adapt and taken all twenty down. A guard that fires
# after the damage is a message, not a guard.
if ! docker compose -f $STACK/compose.yml exec -T caddy \
     printenv POOLHOUND_PUBLIC_HOSTS </dev/null >/dev/null 2>&1; then
  echo "the caddy service does not have POOLHOUND_PUBLIC_HOSTS in its" >&2
  echo "environment. Add these to its environment: block in $STACK/compose.yml" >&2
  # NAMED AS THEY ARE ACTUALLY INSTALLED. This said \${PUBLIC_HOSTS} and
  # \${OAUTH2_HOST}, which are poolhound server.env keys and not names the
  # stack .env has ever carried -- so following the instruction literally set
  # the compose variables from two undefined ones and reproduced the empty
  # expansion this guard exists to prevent. The stack namespaces per-app keys
  # (SECSCAN_*, PETDOOR_*); these match. :? so an unset value fails at compose
  # time rather than expanding to nothing inside Caddy.
  echo "  POOLHOUND_PUBLIC_HOSTS: \"\${POOLHOUND_PUBLIC_HOSTS:?set it in .env}\"" >&2
  echo "  POOLHOUND_OAUTH2_HOST:  \"\${POOLHOUND_OAUTH2_HOST:?set it in .env}\"" >&2
  echo "Refusing to install a vhost whose addresses would expand to nothing." >&2
  exit 1
fi
cp $STACK/poolhound/src/deploy/poolhound.caddy sites/poolhound.caddy
# The snippet defining (poolhound_auth) must be parsed before the site that
# imports it. Snippets are global, so it goes beside the other site files and
# sorts ahead of poolhound.caddy by name.
cp $STACK/poolhound/src/deploy/caddy/auth-${AUTH_MODE:-password}.caddy \
   sites/00-poolhound-auth.caddy
# AND THE ADAPTED CONFIG MUST STILL PARSE. The environment check above answers
# "is the variable set"; this answers "does what we just wrote actually work",
# which is the question that matters and is not the same one. If it does not,
# the two files come straight back out so the host is left with the config it
# was serving rather than one that dies on the next reload.
if ! docker compose -f $STACK/compose.yml exec -T caddy \
     caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile </dev/null >/dev/null 2>&1; then
  rm -f sites/poolhound.caddy sites/00-poolhound-auth.caddy
  echo "the Caddy config does not adapt with the poolhound vhost installed." >&2
  echo "Both files have been removed; the host keeps the config it had." >&2
  docker compose -f $STACK/compose.yml exec -T caddy \
    caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile </dev/null 2>&1 | tail -3 >&2
  exit 1
fi
install -m 0755 $STACK/poolhound/src/deploy/collect.sh poolhound/collect.sh
sed -e 's|@@STACK@@|$STACK|g' \
    $STACK/poolhound/src/deploy/poolhound-cron > /etc/cron.d/poolhound
chmod 0644 /etc/cron.d/poolhound
if grep -qE '@@[A-Z0-9_]+@@' /etc/cron.d/poolhound; then
  echo "the crontab still carries a placeholder — set STACK in deploy/.env" >&2
  exit 1
fi
# The cron jobs now write to a log instead of into MAILTO="". A log with no
# rotation is a disk that fills, which is a worse silence than the one this fixed.
install -m 0644 $STACK/poolhound/src/deploy/poolhound-logrotate /etc/logrotate.d/poolhound
python3 - <<'PY'
p = "$STACK/compose.yml"
t = open(p).read()
orig = t
if "  poolhound:" not in t:
    frag = open("$STACK/poolhound/src/deploy/compose.poolhound.yml").read()
    body = "\n".join(l for l in frag.split("\n") if not l.startswith("#")).strip("\n")
    t = t.replace("\nvolumes:\n", "\n" + body + "\n\nvolumes:\n", 1)
    t = t.replace("volumes:\n  kuma_data:",
                  "volumes:\n  poolhound_site:\n  poolhound_vault:\n  kuma_data:", 1)
# RECONCILED FROM THE FRAGMENT, NOT PATCHED KEY BY KEY.
#
# The branch above only fires on a FIRST install, so an existing service block
# keeps whatever it was created with for ever. Three separate bespoke patches
# had already been written here for exactly that -- POOLHOUND_VAULT, the vault
# volume, and the REVISION build arg -- each added after somebody noticed a
# setting was not taking effect on the one host that matters.
#
# The fourth was POOLHOUND_REQUIRE_DATA, the guard that stops an unmounted
# share being reseeded into a second pool history. It sat in the repository
# for two days and was never in the container: the deploy reported success
# throughout, because a variable that is absent is not an error.
#
# So this no longer names variables. compose.poolhound.yml is the service
# definition; every environment key and every volume in it is ensured present
# here. ADD-ONLY, NEVER OVERWRITE -- an operator who changed TZ or a mount on
# this host keeps their value, and only genuinely missing lines are inserted.
def _block(text, start):
    """The poolhound service block: from its header to the next 2-space key."""
    i = text.find(start)
    if i < 0:
        return None, None
    j = i + len(start)
    for line in text[j:].split("\n"):
        nxt = text.find("\n" + line, j - 1) if line else -1
        break
    k = j
    for ln in text[j:].splitlines(keepends=True):
        if ln.strip() and not ln.startswith("    ") and not ln.startswith("  -"):
            if ln.startswith("  ") and not ln.startswith("   "):
                break
        k += len(ln)
    return i, k


def _keys_of(section, text):
    """KEY: value names, or "- mount" entries, under a section: in a block.

    NO BACKTICKS. This whole script is inside an UNQUOTED heredoc, so a
    backtick pair here is a command substitution that runs on the machine
    launching the deploy, before a byte is sent. These three printed
    "KEY:: command not found", "-: command not found" and
    "section:: command not found" on every run since 2026-09-26.
    """
    out, inside = [], False
    for ln in text.splitlines():
        if ln.strip() == section + ":":
            inside = True
            continue
        if inside:
            if ln.strip().startswith("#") or not ln.strip():
                continue
            if not ln.startswith("      "):
                inside = False
                continue
            body = ln.strip()
            out.append(body.split(":")[0] if section == "environment" else body)
    return out


frag_txt = open("$STACK/poolhound/src/deploy/compose.poolhound.yml").read()
frag_txt = "\n".join(l for l in frag_txt.split("\n") if not l.startswith("#"))

bi, bk = _block(t, "  poolhound:")
if bi is not None:
    block = t[bi:bk]
    added = []
    for section in ("environment", "volumes"):
        want = _keys_of(section, frag_txt)
        have = _keys_of(section, block)
        if not want:
            continue
        # Insert after the LAST existing line of that section, so ordering and
        # any comments an operator added above are left alone.
        lines = block.splitlines(keepends=True)
        anchor = None
        inside = False
        for n, ln in enumerate(lines):
            if ln.strip() == section + ":":
                inside = True
                continue
            if inside:
                if ln.startswith("      ") and ln.strip():
                    anchor = n
                elif ln.strip() and not ln.startswith("      "):
                    break
        if anchor is None:
            continue
        for key in want:
            name = key.split(":")[0] if section == "environment" else key
            if name in have:
                continue
            src = [l for l in frag_txt.splitlines()
                   if l.strip().startswith(name) and l.startswith("      ")]
            if not src:
                continue
            lines.insert(anchor + 1, src[0].rstrip("\n") + "\n")
            anchor += 1
            added.append(f"{section}/{name}")
        block = "".join(lines)
    if added:
        t = t[:bi] + block + t[bk:]
        print("    reconciled from compose.poolhound.yml: " + ", ".join(added))

# Named volumes the fragment mounts must also be declared at the top level.
for vol in ("poolhound_site", "poolhound_vault"):
    if f"  {vol}:" not in t:
        t = t.replace("\nvolumes:\n", f"\nvolumes:\n  {vol}:\n", 1)

i = t.find("  poolhound:")
if i >= 0 and "REVISION:" not in t[i:i + 900]:
    line = "      dockerfile: deploy/Dockerfile\n"
    j = t.find(line, i)
    if j >= 0:
        t = (t[:j + len(line)]
             + "      args:\n        REVISION: \"\${REVISION:-unknown}\"\n"
             + t[j + len(line):])
if t != orig:
    open(p, "w").write(t)
PY
  # Seed the durable config if the share has none. Nothing in the deploy ever
  # put one there -- the rsync excludes config/config.toml deliberately, which is
  # right, and the result was that a rebuilt host had no config on the share at
  # all. save_settings now refuses to write anywhere else, so without this the
  # first save on a fresh host fails with an explanation instead of just losing
  # the setting later. The example holds no secrets and states no volume.
  # AND NOT ONTO AN UNMOUNTED DIRECTORY. Seeding is how a lost share becomes a
  # second pool history: config.toml's presence is what config.seed_config()
  # used to treat as "this deployment is fine", so writing it here over a failed
  # mount disarmed the container's own guard. Checked again at the point of the
  # write, because the mount can go away between the step above and this one.
  if ! mountpoint -q /mnt/poolhound; then
    echo "  REFUSING to seed config.toml: /mnt/poolhound is not a mount point" >&2
    exit 1
  fi
  if [[ ! -f /mnt/poolhound/config.toml ]]; then
    sudo cp "$STACK/poolhound/src/config/config.example.toml" /mnt/poolhound/config.toml
    echo "  seeded /mnt/poolhound/config.toml from the example"
  fi

export REVISION="$REV"
docker compose config --quiet </dev/null
docker compose build poolhound </dev/null
docker compose up -d poolhound </dev/null
# VALIDATE, AND LET IT FAIL. This redirected to /dev/null with its exit status
# discarded, so a vhost Caddy could not parse was reloaded anyway.
docker compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile </dev/null
docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile </dev/null
EOF
# AND THE EXIT STATUS OF ALL THAT IS THE POINT. `ok "service up"` printed
# unconditionally. The whole install above is one `sudo bash -s` heredoc with
# SIX `exit 1` paths in it -- the Caddy environment guard, the adapted-config
# guard, the crontab placeholder guard among them -- and none of them reached
# the operator.
#
# MEASURED: the adapted-config guard fired on "ambiguous site definition", the
# heredoc stopped there, and everything after it was skipped -- collect.sh not
# installed, the crontab not rewritten, `docker compose build` and
# `up -d` never run. Verified afterwards by timestamp: collect.sh, /etc/cron.d
# and /etc/logrotate.d were all still four days old. The deploy printed
# "service up", ran every later check, and finished on a container four days
# stale serving the previous revision. Two of those later checks DID notice --
# "STALE PAGE", twice -- and both named re-rendering as the fix, which was the
# one thing that could not possibly help.
#
# A step that cannot fail is not a step.
[[ $install_rc -eq 0 ]] || die "the install step failed on the server (see above) — everything after it was skipped"
  ok "service up"

  # AND DO THE GATES PASS ON THE MACHINE THAT SERVES THE PAGE?
  #
  # bin/selftest runs at image build, but bin/contrast cannot: it measures a
  # RENDERED page against the palette, and there is no data in the image. Here
  # there is -- the share is mounted and the render just ran -- so this is the
  # only place the palette check can be made against the page the server actually
  # serves. It ran nowhere in the deploy before this, on either half.
  c "Running the gates inside the container"
  if "${SSH[@]}" "$HOST_IP" \
       "sudo docker exec $PH_CTR python -m poolhound.contrast" >/dev/null 2>&1; then
    ok "palette and contrast pass against the served page"
  else
    warn "bin/contrast FAILED against the page the server is serving"
    RENDER_FAILED=1
  fi

  # DID THE RELOAD ACTUALLY APPLY? `caddy reload` exited 0 and printed "adapted
  # config to JSON" while the RUNNING config stayed on an older revision -- the
  # deploy announced "Caddy reloaded" and the proxy went on enforcing rules from
  # some earlier version. Found by diffing the admin API's running config
  # against the file: /api/export had been shipped in the vhost and never
  # reached the running proxy at all.
  #
  # The file is not the proxy. Ask the thing that is actually serving.
  c "Confirming Caddy is running what was shipped"
  running=$("${SSH[@]}" "$HOST_IP" \
    "sudo docker exec $CADDY_CTR wget -qO- http://127.0.0.1:2019/config/ 2>/dev/null" 2>/dev/null)
  # THE DERIVED LIST THAT USED TO BE COMPUTED HERE WAS NEVER USED. It was
  # from the shipped vhost and then thrown away; this loop tested five paths
  # somebody typed here, so the check that exists to catch proxy drift was
  # watching a third of the routes -- and none of the three the verify step
  # below was arguing with. Same source as that step now, same wildcard-aware
  # matching, so the two cannot reach opposite conclusions about one proxy.
  missing=$(caddy_gaps "$running")
  if [[ -n "$missing" ]]; then
    warn "Caddy's RUNNING config is missing:$missing — reloading again"
    "${SSH[@]}" "$HOST_IP" "sudo bash -c 'cd $STACK && docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile'" >/dev/null 2>&1
    sleep 2
    running=$("${SSH[@]}" "$HOST_IP" \
      "sudo docker exec $CADDY_CTR wget -qO- http://127.0.0.1:2019/config/ 2>/dev/null" 2>/dev/null)
    # THE SAME IMPLEMENTATION, because this loop was the one left behind.
    # Six lines below a comment explaining that literal substring matching
    # reported wildcard-gated routes as missing "on every run for months", this
    # re-check did exactly that -- so a reload that had actually succeeded was
    # reported as a proxy serving rules that are not in the repo, and set
    # RENDER_FAILED=1. The fix was applied to the check above and not to the one
    # beside it: the same shape as the banner that was corrected while the
    # command table next to it was not.
    still=$(caddy_gaps "$running")
    if [[ -n "$still" ]]; then
      warn "STILL missing after a second reload:$still"
      warn "  the proxy is enforcing rules that are not the ones in the repo"
      RENDER_FAILED=1
    else
      ok "second reload applied"
    fi
  else
    ok "Caddy is serving the shipped rules"
  fi

  # RENDER. Without this the deploy ships new code and leaves the OLD PAGE up:
  # site/ is a named volume, nothing re-renders on container restart, and every
  # --check probe passes against the stale copy. Observed on a real deploy —
  # all green, old page. The page is the product; shipping the code is only half.
  c "Rendering with the new code"
  # And a failed render FAILS THE DEPLOY. This was `|| warn` and did not touch
  # `fails`, so a render that raised left a stale page up and the deploy still
  # reported success -- with every probe passing, because the probes read the
  # page that was already there.
  if "${SSH[@]}" "$HOST_IP" "sudo $STACK/poolhound/collect.sh render" >/dev/null 2>&1; then
    ok "page rebuilt"
  else
    warn "render FAILED — the site is still serving the previous page"
    RENDER_FAILED=1
  fi
fi

# ── verify ───────────────────────────────────────────────────────────────────
c "Verifying"
fails=${RENDER_FAILED:-0}
chk() { if [[ "$2" == "$3" ]]; then ok "$1"; else warn "$1 — got '$2', wanted '$3'"; fails=$((fails+1)); fi; }

state=$("${SSH[@]}" "$HOST_IP" "sudo docker inspect $PH_CTR --format '{{.State.Status}}'" 2>/dev/null || echo missing)
chk "container running" "$state" "running"

mounted=$("${SSH[@]}" "$HOST_IP" 'mountpoint -q /mnt/poolhound && echo yes || echo no')
chk "share mounted" "$mounted" "yes"

# `sudo wc -l < file` does NOT work: the shell opens the redirect as the
# unprivileged user before sudo ever runs, so a readable-only-by-root file
# reports "Permission denied" and this check announces that the history is
# gone. A verification that false-alarms on a healthy system is worse than no
# verification, because the next person learns to ignore it.
rows=$("${SSH[@]}" "$HOST_IP" 'sudo cat /mnt/poolhound/samples.csv 2>/dev/null | wc -l' | tr -d " ")
rows=${rows:-0}
if (( rows > 1 )); then ok "history present: $((rows-1)) samples"; else warn "samples.csv is empty or missing — RESTORE BEFORE TRUSTING THIS"; fails=$((fails+1)); fi

# `docker compose -f <path>` without also being IN the project directory
# resolves relative build contexts and volumes against the wrong place and can
# sit there indefinitely. cd first. The timeout is belt and braces: a
# verification step that hangs is indistinguishable from a system that is down,
# and it will be run by somebody who has just rebuilt a server and wants an
# answer, not a spinner.
kv=$("${SSH[@]}" -o ConnectTimeout=15 "$HOST_IP" "timeout 45 sudo bash -c 'cd $STACK && docker compose exec -T poolhound python -c \"
import sys; sys.path.insert(0,\\\"/app\\\")
from poolhound import vault
u,p,s = vault.credentials_for(\\\"waterguru\\\")
print(\\\"ok\\\" if u and p else \\\"missing\\\")\"' 2>/dev/null" | tr -d '\r' | tail -1 || true)
chk "Key Vault readable from the container" "$kv" "ok"

# IS THERE A RECOVERY POINT, AND HOW OLD IS IT?
#
# The share was the only copy of everything irreplaceable here: no snapshot
# policy, no backup vault, no copy job — and migrate_columns' own .bak is
# written BESIDE the file, on the same share, so the one backup the code took
# was gone in exactly the case a backup is for. An RPO nobody can state is an
# RPO of "whatever is left".
# ONE STATIC REMOTE COMMAND, and the filename never leaves the remote shell.
#
# This used to read the newest filename back here and then interpolate it into
# a `sudo sh -c "...stat -c %Y '$bk'..."` string — a filename, chosen by
# whoever can write the directory, pasted into a ROOT shell. The directory is
# bind-mounted read-write into the container, which runs as uid 1010, so a
# compromised poolhound could drop a file named poolhound-$(...).tgz and have
# it run as root on the host the next time anybody ran --check. Container to
# host, through the freshness check.
#
# The filename now stays in a remote shell variable that is never re-parsed,
# quoted with -- against a leading dash, and only an integer comes back.
# sudo because the directory is 0700 poolhound: an unprivileged ls finds
# nothing and the check reported "NO BACKUP EXISTS" over a backup that was
# sitting there.
bk_age=$("${SSH[@]}" "$HOST_IP" \
  'sudo sh -c '"'"'f=$(ls -t /var/lib/poolhound/backups/poolhound-*.tgz 2>/dev/null | head -1); [ -n "$f" ] || exit 0; echo $(( ( $(date +%s) - $(stat -c %Y -- "$f") ) / 3600 ))'"'"'' \
  2>/dev/null | tr -d '\r' | tail -1 || true)
if [[ "$bk_age" =~ ^[0-9]+$ ]]; then
  if (( bk_age <= 48 )); then
    ok "a recovery point exists, ${bk_age}h old"
  else
    warn "the newest backup is ${bk_age}h old — the nightly copy is not running"
    fails=$((fails+1))
  fi
else
  warn "NO BACKUP EXISTS — the share is the only copy of this pool's history"
  warn "  run: $STACK/poolhound/collect.sh backup   (cron does it at 02:40)"
  fails=$((fails+1))
fi

# IS THE Caddy -> poolhound BOUNDARY PROVEN, or merely assumed?
#
# Identity is an HTTP header. Caddy strips the client's copy and sets the real
# one, but that happens on the VHOST — a path a sibling container on the same
# docker bridge does not take, so without a shared secret the container trusts
# anything that can open a socket to it. Reported rather than enforced,
# because the secret is opt-in: an upgrade that began refusing every identity
# header would lock the owner out of their own pool.
proxy_auth=$("${SSH[@]}" -o ConnectTimeout=15 "$HOST_IP" "timeout 45 sudo bash -c 'cd $STACK && docker compose exec -T poolhound python -c \"
import sys; sys.path.insert(0,\\\"/app\\\")
from poolhound import server
print(\\\"on\\\" if server.PROXY_SECRET else \\\"off\\\")\"' 2>/dev/null" | tr -d '\r' | tail -1 || true)
if [[ "$proxy_auth" == "on" ]]; then
  ok "identity is proven by a shared secret, not by network position"
else
  warn "POOLHOUND_PROXY_SECRET is NOT set — any container on this docker network can claim to be any user"
  warn "  openssl rand -hex 32, put it in the env file feeding BOTH caddy and poolhound, recreate both"
  fails=$((fails+1))
fi

# HOW MANY CONTAINERS CAN OPEN A SOCKET TO THIS ONE?
#
# The second half of trust boundary B2, and the half docs/THREAT_MODEL.md has
# always carried as "documented rather than done". The proxy secret above
# closes the FORGERY — a sibling can no longer claim to be a person. It does
# not close the REACHABILITY: every container on the shared bridge can still
# talk to poolhound:8787, and the only thing standing in front of the agent
# routes there is a bearer token.
#
# MEASURED, because a number is the only form of this fact worth having.
# "Network isolation is documented rather than done" is a sentence that reads
# the same whether the bridge carries one container or eleven, and on the
# author's stack it carried eleven — mail, a media host, a cache, a
# database, an uptime monitor, the proxy pair and three unrelated web apps.
#
# The networks live in the HOST's compose file, which this repository
# deliberately does not carry, so this cannot be fixed from here. It can be
# counted from here, and a count that grows is a diff somebody should see.
#
# THE UNION, NOT THE WORST ONE. This took the LARGEST container count across
# poolhound's networks, which is the right answer only while there is one
# network. The fix for this gap gives poolhound two — caddy on one, postfix on
# the other, because `smtp_host = "postfix"` and the alert mail has to reach it
# — and a max over those two is 2, so it would have reported "only the proxy
# shares a docker network" with a mail relay also on the list. A check that
# gets quieter the moment somebody acts on it is worse than no check.
#
# So: the distinct set of neighbours across every network this container is
# attached to, minus itself.
#
# AND SEPARATELY, WHETHER IT IS ON THE SHARED BRIDGE, because that is the
# question the count is a proxy for. Two named neighbours is a topology
# somebody chose; `<project>_default` is every service on the host, however
# many that happens to be today.
#
# TWO STATIC REMOTE COMMANDS AND ONLY INTEGERS COME BACK — the lesson the
# backup freshness check paid for. Container names stay on the far side; none
# is read back, so none can be re-parsed here.
net_n=$("${SSH[@]}" "$HOST_IP" \
  'sudo bash -c '"'"'for n in $(docker inspect -f "{{range .NetworkSettings.Networks}}{{println .NetworkID}}{{end}}" '"$PH_CTR"' 2>/dev/null); do docker network inspect -f "{{range .Containers}}{{println .Name}}{{end}}" "$n" 2>/dev/null; done | sort -u | grep -v "^'"$PH_CTR"'$" | grep -c . '"'"'' \
  2>/dev/null | tr -d '\r' | tail -1 || true)
net_shared=$("${SSH[@]}" "$HOST_IP" \
  'sudo bash -c '"'"'for n in $(docker inspect -f "{{range .NetworkSettings.Networks}}{{println .NetworkID}}{{end}}" '"$PH_CTR"' 2>/dev/null); do docker network inspect -f "{{.Name}}" "$n" 2>/dev/null; done | grep -cE "_default$|^bridge$"'"'"'' \
  2>/dev/null | tr -d '\r' | tail -1 || true)
if [[ "$net_n" =~ ^[0-9]+$ && "$net_shared" =~ ^[0-9]+$ ]]; then
  if (( net_shared > 0 )); then
    warn "poolhound is on the SHARED docker bridge with $net_n other container(s), which can reach :8787 directly"
    warn "  the proxy secret stops them claiming an identity; it does not stop them connecting"
    warn "  give poolhound a network of its own in the host's compose file: caddy for"
    warn "  requests, postfix for the alert mail, and nothing else on either"
  elif (( net_n <= 2 )); then
    ok "poolhound is off the shared bridge; $net_n container(s) can reach it"
  else
    warn "poolhound is off the shared bridge, but $net_n container(s) can still reach :8787"
    warn "  the designed set is two — the proxy, and the mail relay the alerts go through"
  fi
else
  warn "could not count what shares poolhound's docker network"
fi

# IS THERE A COPY THAT SURVIVES LOSING THIS VM?
#
# The nightly tarball goes to the host's local disk, which is the one place
# guaranteed to fail separately from the Azure Files share — and that is all it
# buys. deploy/README.md has always said so and, until bin/backup learned a
# second destination, named no way to fix it. This is the third open item in
# docs/THREAT_MODEL.md and it is now one environment variable.
#
# NOT A FAILURE WHEN UNSET, deliberately: an install that has not chosen a
# second destination is in an honest state, and grading it red beside the
# proxy secret would teach somebody to read past both. It is a failure when a
# destination WAS chosen and nothing is arriving there, because that is
# somebody believing in a copy that does not exist.
off=$("${SSH[@]}" "$HOST_IP" "sudo docker exec $PH_CTR sh -c 'echo \"\${POOLHOUND_BACKUP_OFFBOX:-unset}\"'" 2>/dev/null | tr -d '\r' | tail -1 || true)
if [[ -z "$off" || "$off" == "unset" ]]; then
  warn "no off-box backup: losing this VM loses the history, backups and all"
  warn "  set POOLHOUND_BACKUP_OFFBOX to a mount that is not this host (blobfuse, SMB, a second share)"
else
  off_age=$("${SSH[@]}" "$HOST_IP" \
    'sudo docker exec '"$PH_CTR"' sh -c '"'"'f=$(ls -t "$POOLHOUND_BACKUP_OFFBOX"/poolhound-*.tgz 2>/dev/null | head -1); [ -n "$f" ] || exit 0; echo $(( ( $(date +%s) - $(stat -c %Y -- "$f") ) / 3600 ))'"'"'' \
    2>/dev/null | tr -d '\r' | tail -1 || true)
  if [[ "$off_age" =~ ^[0-9]+$ ]] && (( off_age <= 48 )); then
    ok "an off-box recovery point exists, ${off_age}h old"
  else
    warn "an off-box destination IS configured and holds no recent tarball"
    warn "  that is worse than none: somebody believes there is a copy off this host"
    fails=$((fails+1))
  fi
fi

# IS A TOKEN ROTATION HALF DONE?
#
# The step people do not take is the last one. While a second slot exists this
# install accepts two credentials for the agent routes, which is the state the
# slot is FOR — and a state nobody should be in for a month. vault.py refuses a
# slot with no deadline, so the window is real; this is what makes the window
# visible to the person who opened it.
rot=$("${SSH[@]}" "$HOST_IP" "timeout 45 sudo bash -c 'cd $STACK && docker compose exec -T poolhound python -c \"
import sys; sys.path.insert(0,\\\"/app\\\")
from poolhound import vault
p = vault.agent_token_previous()
print(p[1].strftime(\\\"%Y-%m-%d\\\") if p else \\\"none\\\")\"' 2>/dev/null" | tr -d '\r' | tail -1 || true)
# THREE OUTCOMES, BECAUSE THE THIRD ONE HAPPENED. An image built before
# vault.agent_token_previous() existed raises inside the container, prints
# nothing and exits non-zero. Folding that into "none" is a FALSE GREEN about
# a credential — and under `set -euo pipefail` the substitution took the whole
# of --check down with it: no revision comparison, no served-page probe and no
# `bin/drive --url`, which is the gate that catches every user-visible defect
# this project has shipped. Fifteen lines of output, exit 1, and nothing
# saying which question went unanswered. Measured on a server five commits
# behind, by the deploy this check exists to verify. "Did not run" is a third
# outcome everywhere else in this project and it is one here.
if [[ -z "$rot" ]]; then
  warn "could not ask whether an agent token rotation is open"
  warn "  the container answered nothing — most likely an image predating vault.agent_token_previous()"
  warn "  this is not evidence of no rotation; ask again after the deploy"
elif [[ "$rot" == "none" ]]; then
  ok "one agent token is accepted, so no rotation is outstanding"
else
  warn "an agent token rotation is open — the OUTGOING token is accepted until $rot"
  warn "  finish it: confirm the Pi is on the new token, then remove the second slot"
  warn "  docs/AGENT-PROTOCOL.md has the sequence"
fi

# CAN THE CREDENTIAL STORE ACTUALLY BE WRITTEN?
#
# Docker copies a mountpoint's ownership onto a fresh named volume only when
# that mountpoint exists in the image. /data and /site always did; /vault did
# not, so its volume was created root:root and the container -- which runs as
# poolhound -- could not write to it. Settings would have reported a saved
# credential that never landed, which is the failure the vault volume was added
# to prevent, reintroduced one layer down. A mount that cannot be written is
# worse than no mount, because it looks like one.
vw=$("${SSH[@]}" "$HOST_IP" "sudo docker exec $PH_CTR sh -c 'touch /vault/.probe 2>/dev/null && rm -f /vault/.probe && echo ok || echo denied'" 2>/dev/null | tr -d '\r' | tail -1 || true)
chk "credential store is writable" "$vw" "ok"

# IS THE PROXY ENFORCING THE RULES IN THE REPO?
#
# `caddy reload` exits 0 and logs "adapted config to JSON" whether or not the
# running config changes. The deploy announced "Caddy reloaded" while the proxy
# went on enforcing an older vhost -- /api/export had been shipped and never
# reached it. The vhost file is not the proxy; ask the admin API what is
# actually loaded.
cfg=$("${SSH[@]}" "$HOST_IP" \
  "sudo docker exec $CADDY_CTR wget -qO- http://127.0.0.1:2019/config/ 2>/dev/null" 2>/dev/null)
gaps=""
# DERIVED, NOT TYPED. This was six hand-written paths against a NEEDS table of
# fifteen, so nine private routes -- including both credential routes -- were
# never compared with the running config at all. /api/rows was among them: its
# @private rule shipped, never reached the running proxy, and this check said
# "the proxy is enforcing the shipped rules" while the route answered from the
# application. A check written to catch exactly that drift missed it because
# its inventory was a list somebody maintained by hand.
#
# render.py already learned this and derives its leak patterns from the same
# table; this now reads from the one source too.
# AND A WILDCARD GATES WHAT IT COVERS.
#
# The derivation above was right and the MATCHING was not: it asked whether the
# running config contains the literal "/api/chemical/delete", and the Caddyfile
# gates that route with `path /api/chemical /api/chemical/*` -- so the config
# holds the wildcard and never the leaf. Three routes were reported missing on
# every single run, for months, while the proxy was gating all three correctly.
#
# That is the worse failure of the two this check has had. A check that is
# silent when it should shout let /api/rows drift; a check that shouts the same
# three names every time teaches the person reading it to skip the warning
# block, which loses the next real gap as surely as the silence did.
#
# So a route counts as gated if the config names it, or names a wildcard above
# it. Done in Python because this is prefix logic over a 36 KB JSON blob, and
# bash substring matching is what got it wrong in the first place.
gaps=$(caddy_gaps "$cfg")
# AND THE OTHER DIRECTION, because "is it shut?" cannot fail when the matcher
# is too wide. See caddy_overgated(): the public volume calculator disappearing
# behind Entra is a silent outage for every anonymous visitor, and the shape of
# the question above makes it unaskable.
over=$(caddy_overgated "$cfg")
if [[ -z "$cfg" ]]; then
  warn "could not read Caddy's running config — cannot tell which rules are live"
  fails=$((fails+1))
elif [[ -n "$gaps" ]]; then
  warn "Caddy's RUNNING config does not gate:$gaps"
  warn "  reload it: docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile"
  fails=$((fails+1))
elif [[ -n "$over" ]]; then
  warn "Caddy's RUNNING config puts PUBLIC route(s) behind sign-in:$over"
  warn "  both are pure and are offered to everybody; check the @private matcher"
  warn "  has no trailing wildcard on /api/pool-shape"
  fails=$((fails+1))
else
  ok "the proxy is enforcing the shipped rules, and only those"
fi

# IS THE SERVED PAGE THE CODE THAT IS DEPLOYED?
#
# Everything below this point was true of a page rendered a month ago, which is
# how a deploy once reported every probe green while the site served the
# previous build. The footer carries the revision the render was made from, and
# the tree on the server carries the revision that was shipped; if they differ, the
# page is stale no matter how healthy everything else looks.
want_rev=$("${SSH[@]}" "$HOST_IP" "sudo cat $STACK/poolhound/src/REVISION 2>/dev/null" | tr -d '\r' | tail -1 || true)
# FETCH AND PARSE SEPARATELY, because they fail for different reasons and one
# of them used to take the whole script out. This was a single pipeline:
# curl | grep | head | sed. Under `set -o pipefail` a grep that matches nothing
# returns 1, the pipeline returns 1, and `set -e` exits the run -- so when the
# site served a zero-byte 302 the check reported no stale page, no missing page,
# nothing at all. It stopped mid-verify having printed only the checks above it.
#
# That is the precise failure this block exists to catch, and the block's own
# shape hid it. Three outcomes now, each named: the page could not be fetched,
# the page carries no revision, or the revisions can be compared.
pub_code=$(curl -sS -m 20 -o /tmp/ph-served.html -w '%{http_code}' \
           "https://$PRIMARY_HOST/" 2>/dev/null || echo 000)
pub_bytes=$(wc -c < /tmp/ph-served.html 2>/dev/null | tr -d ' ' || echo 0)
got_rev=""
if [[ "$pub_code" == "200" && "${pub_bytes:-0}" -gt 1000 ]]; then
  # `|| true`: no match is an answer, not a crash.
  got_rev=$(LC_ALL=C grep -o 'poolhound</b> v[^<]*' /tmp/ph-served.html 2>/dev/null \
            | head -1 | LC_ALL=C sed 's/.*· //' || true)
fi
# BOTH BUILDS. This compared only the public page, and bin/render writes
# index.html FIRST -- so any failure specific to the authenticated build (whose
# private script is stripped from the public one, giving node --check a whole
# surface index.html does not have) left a FRESH public page beside a STALE
# admin page, and every probe still passed: 200 public, 302 admin, revision
# matches. admin.html cannot be curled from here because Entra gates it, so its
# revision is read out of the site volume over the ssh session already open.
# Same treatment: a missing admin.html reports as an empty revision rather than
# ending the run.
admin_rev=$("${SSH[@]}" "$HOST_IP" \
  "sudo docker exec $PH_CTR sh -c 'grep -o \"poolhound</b> v[^<]*\" /site/admin.html 2>/dev/null | head -1 || true'" 2>/dev/null \
  | tr -d '\r' | sed 's/.*· //' || true)
# THE PAGE ITSELF, BEFORE ITS CONTENTS. "stale" and "absent" are different
# problems with different fixes, and the old code could report neither.
if [[ "$pub_code" != "200" ]]; then
  warn "THE PUBLIC SITE IS NOT SERVING — https://$PRIMARY_HOST/ answered $pub_code"
  warn "  an outage, not a stale page: check the container, then Caddy"
  fails=$((fails+1))
elif [[ "${pub_bytes:-0}" -le 1000 ]]; then
  warn "THE PUBLIC PAGE IS EMPTY — 200 but only ${pub_bytes} bytes"
  warn "  a redirect loop or a failed render serves this; it is not a stale page"
  fails=$((fails+1))
elif [[ -z "$got_rev" ]]; then
  warn "the served page carries no revision in its footer — cannot tell which build it is"
  fails=$((fails+1))
fi

if [[ -z "$want_rev" ]]; then
  # A FAILURE, not a note. "cannot tell" is the same state as "does not match"
  # for anybody relying on this check: the whole point is to answer "is what is
  # deployed what is served", and an unstamped host answers nothing while the
  # run still exits 0.
  warn "no REVISION on the server — cannot tell whether the page matches the code (deploy again to stamp it)"
  fails=$((fails+1))
else
  if [[ "$want_rev" == "unknown" ]]; then
    # The Dockerfile's ARG default. A page stamped "unknown" matches a REVISION
    # of "unknown" by substring and reported a green "served page is the
    # deployed build (unknown)" -- agreement about nothing.
    warn "the server's REVISION is literally 'unknown' — the image was built without one"
    fails=$((fails+1))
  elif [[ "$got_rev" == *"$want_rev"* ]]; then
    ok "served public page is the deployed build ($want_rev)"
  else
    warn "STALE PAGE — the server has '$want_rev', the site is serving '${got_rev:-no revision}'."
    warn "  the code shipped but nothing re-rendered: sudo $STACK/poolhound/collect.sh render"
    fails=$((fails+1))
  fi
  if [[ -z "$admin_rev" ]]; then
    warn "could not read admin.html's revision — the authenticated build may be stale or missing"
    fails=$((fails+1))
  elif [[ "$admin_rev" == *"$want_rev"* ]]; then
    ok "authenticated page is the deployed build ($want_rev)"
  else
    warn "STALE ADMIN PAGE — the server has '$want_rev', admin.html is '$admin_rev'."
    warn "  bin/render writes index.html first, so a failure in the authenticated"
    warn "  build leaves a fresh public page beside a stale private one."
    fails=$((fails+1))
  fi
fi

# DO BOTH ENDS HOLD THE SAME AGENT TOKEN?
#
# vault.agent_token_fingerprint() was written so two machines can be checked
# against each other without either revealing the secret — and the check it
# exists for was performed by neither deploy script, so the function had no
# callers at all. It is also the cheap answer to the failure where Key Vault is
# unreachable and the token merely LOOKS absent, which closes every
# /api/agent/* route and takes the Pi silent.
#
# A hash, never the token. If the two ends disagree the fix is to look, not to
# print the secret into a terminal.
# server_fp, NOT `the server_fp`. The rename that replaced this host's own name
# with "the server" was 306 replacements and one landed inside a shell
# IDENTIFIER, so this line
# read `the server_fp=$(...)` -- which bash parses as running a command called
# `the`, and every --check since has ended in "the: command not found" with the
# agent-token fingerprint never compared at all. The last check in the script,
# so its output looked like the end of a clean run.
server_fp=$("${SSH[@]}" "$HOST_IP" \
  "sudo docker exec $PH_CTR python -c \"from poolhound import vault; print(vault.agent_token_fingerprint() or '')\"" \
  2>/dev/null | tr -d '\r' | tail -1 || true)
if [[ -z "$server_fp" ]]; then
  warn "the server resolves NO agent token — every /api/agent route is closed and the Pi cannot report"
  fails=$((fails+1))
else
  ok "the server holds an agent token (fingerprint $server_fp)"
  echo "    compare with the Pi:  ./deploy/update-pi.sh --check"
fi

# AND THE DEPLOYED PAGE, DRIVEN. Every probe below asks for a status code; a
# 200 says the page was served, not that it works. The most user-visible
# defects this project shipped all returned 200 the whole time: a status line
# telling anonymous visitors they were signed in, invisible spans scrolling the
# page 251px sideways at phone width, charts announced as their own axis ticks.
#
# --check verifies what is LIVE and changes nothing, so this drives the public
# page over the network, read-only. /admin.html is gated and a browser here has
# no session — the probes below already assert it answers 302.
#
# The local-build run in the deploy path tests the CODE; this tests what the server
# is actually serving. They are not the same question, and this project has
# already shipped a fresh public page beside a stale private one.
if [[ -x "$SRC/.venv/bin/python" ]]; then
  if "$SRC/.venv/bin/python" "$SRC/bin/drive" --url "https://$PRIMARY_HOST/" \
       >/tmp/poolhound-drive-live.log 2>&1; then
    ok "the deployed public page behaves in a browser at phone width"
  elif grep -q "no Playwright here" /tmp/poolhound-drive-live.log; then
    warn "browser checks skipped: no Playwright here (pip install playwright)"
  else
    warn "THE DEPLOYED PAGE MISBEHAVES IN A BROWSER:"
    grep -E "FAIL" /tmp/poolhound-drive-live.log | sed 's/^/    /' | head -6
    fails=$((fails+1))
  fi
else
  # Same silent hole as the build-time run above. --check is the thing people
  # read to decide whether the deployment is working; a check that did not
  # happen must not read as one that passed.
  warn "the live browser check did NOT run: no $SRC/.venv/bin/python here"
  fails=$((fails+1))
fi

for host in ${PUBLIC_HOSTS//,/ }; do
  code=$(curl -sS -o /dev/null -m 20 -w '%{http_code}' "https://$host/" 2>/dev/null || echo 000)
  chk "https://$host/ public" "$code" "200"
  code=$(curl -sS -o /dev/null -m 20 -w '%{http_code}' "https://$host/admin.html" 2>/dev/null || echo 000)
  if [[ "$code" == "302" || "$code" == "401" ]]; then ok "https://$host/admin.html is gated ($code)"
  else warn "https://$host/admin.html returned $code — EXPECTED A REDIRECT TO SIGN-IN"; fails=$((fails+1)); fi
done

# ---- Did the collectors actually RUN, and did they succeed? -----------------
#
# Every check above asks whether the site is up. None asked whether the jobs
# that feed it are working, and cron was discarding their stderr into
# MAILTO="" -- so bin/watch died with a TypeError on every run for weeks and
# --check went on printing green. collect.sh now writes one line per collector
# to the share: when it last finished, and with what status.
#
# TWO QUESTIONS NOW, BECAUSE A TICK IS NOT A PULL. watch and render are still
# cron's, so their status file answers both "did it fire" and "did it work".
# wg-collect and leslies fire HOURLY and poolhound/cadence.py decides whether to
# pull, so their status file answers only "is cron ticking" -- it is written on
# every tick, due or not. Grading collection on it alone would have printed
# "wg-collect ran 12m ago, exit 0" on a host that had not pulled for a week,
# which is this check's own blind spot arriving through a change meant to be an
# improvement. The pull is graded separately, from runs.csv, against the
# configured cadence -- see the cadence report below.
echo
c "Collectors — did they run, and did they work"
# THE AGE IS COMPUTED ON THE SERVER, NOT HERE. `date -d` is GNU; this script is run
# from a Mac, where it is not, and every timestamp would have come back
# unparseable -- a check that fails on the machine it is meant to be run from.
# the server has GNU date, and the server is where the files are.
# BOTH LOCATIONS, NEWER WINS. collect.sh writes its status to the share and to
# /var/lib/poolhound/status, because the two cover different outages: the share
# survives a VM rebuild, the host survives the share failing to mount. Reading
# only the share is how a forty-hour outage presented here as "last ran 50h
# ago" -- true, and silent about the eighty-six skips that caused it, because
# every one of those tried to record onto the share that was missing.
statuses=$("${SSH[@]}" "$HOST_IP" "sudo sh -c '
  for t in \$(ls /mnt/poolhound/.collector-status /var/lib/poolhound/status 2>/dev/null \
               | grep -v \"^\$\" | grep -v \":\$\" | sort -u); do
    newest=; newest_ts=0
    for f in \"/mnt/poolhound/.collector-status/\$t\" \"/var/lib/poolhound/status/\$t\"; do
      [ -e \"\$f\" ] || continue
      w=\$(cut -f1 \"\$f\")
      ts=\$(date -d \"\$w\" +%s 2>/dev/null || echo 0)
      if [ \"\$ts\" -ge \"\$newest_ts\" ]; then newest_ts=\$ts; newest=\$f; fi
    done
    [ -n \"\$newest\" ] || continue
    f=\$newest
    line=\$(cat \"\$f\")
    when=\$(printf %s \"\$line\" | cut -f1)
    rc=\$(printf %s \"\$line\" | cut -f2)
    note=\$(printf %s \"\$line\" | cut -f3)
    ts=\$(date -d \"\$when\" +%s 2>/dev/null || echo 0)
    age=\$(( \$(date +%s) - ts ))
    [ \"\$ts\" = 0 ] && age=-1
    printf \"%s\\t%s\\t%s\\t%s\\t%s\\n\" \"\$t\" \"\$age\" \"\$rc\" \"\$when\" \"\$note\"
  done'" 2>/dev/null || true)

if [[ -z "$statuses" ]]; then
  # Not a pass. An empty directory means either collect.sh has not run since
  # this was deployed, or it cannot write to the share -- and the second is the
  # same blindness this check exists to end.
  warn "no collector status on the share — either nothing has run since deploy, or collect.sh cannot write there"
  fails=$((fails+1))
else
  # THE ROSTER FIRST, BEFORE ANYTHING THAT DID REPORT IS JUDGED.
  #
  # Below this, every line is read from the share and graded, which grades only
  # collectors that wrote a line. A collector that stops writing one -- the
  # state of a job whose wrapper never ran at all -- simply stopped appearing:
  # measured by deleting one line, fails=0, "Done — nothing lost." Silence is
  # the loudest thing a collector can do and it was the one thing not checked.
  missing_collectors=$(collector_gaps "$statuses")
  if [[ -n "$missing_collectors" ]]; then
    warn "cron schedules collector(s) that filed NO status at all:$missing_collectors"
    warn "  not late — absent. Check collect.sh runs for them, and can write the share."
    fails=$((fails+1))
  else
    ok "every collector deploy/poolhound-cron schedules filed a status"
  fi
  while IFS=$'\t' read -r tool age rc when note; do
    [[ -z "$tool" ]] && continue
    case "$tool" in
      watch)              max=5400  ;;   # every 30 min, three misses
      render)             max=86400 ;;   # every 6 h, plus a day of slack
      # A TICK, NOT A PULL: these fire hourly, so three missed ticks is the
      # bound -- and it is cron's health being measured here, not collection's.
      # Whether anything was actually collected is the cadence report below.
      wg-collect|leslies) max=10800 ;;   # hourly tick, three misses
      backup)             max=172800;;   # nightly, two nights of slack
      *)                  max=172800;;
    esac
    if [[ "$rc" != "0" ]]; then
      warn "$tool last finished $when with STATUS $rc ($note)"
      fails=$((fails+1))
    elif (( age < 0 )); then
      warn "$tool has an unreadable timestamp: '$when'"
      fails=$((fails+1))
    elif (( age > max )); then
      warn "$tool last ran $((age/3600))h ago — it is scheduled far more often than that"
      fails=$((fails+1))
    else
      # AND AT THE HOUR THE CRONTAB SAYS. Recency alone gave this a clean bill
      # for five days while the collectors ran seven hours early: the grader
      # allows a daily job two days of slack, so "ran 232m ago, exit 0" was
      # true and useless. CRON_TZ is not supported by this host's cron, so the
      # schedule silently meant UTC, and every WaterGuru pull fetched the
      # previous day's reading. A check that only asks "recently?" cannot see a
      # job that moved.
      want_h=$(awk -v t="$tool" '$0 ~ ("collect.sh " t "( |$)") && $1 ~ /^[0-9*]/ {print $2; exit}' \
                 "$SRC/deploy/poolhound-cron")
      # Any wildcard hour -- "*" or "*/6" -- is not a fixed time and there is
      # nothing to compare against. Only a plain number is a promise.
      if [[ -z "$want_h" || "$want_h" == *"*"* ]]; then
        case "$tool" in
          wg-collect|leslies)
            # SAYS WHICH QUESTION IT ANSWERED. "ran 12m ago" on a tick reads as
            # "collected 12m ago" and is not that.
            ok "$tool ticked $((age/60))m ago, exit 0" ;;
          *) ok "$tool ran $((age/60))m ago, exit 0" ;;
        esac
      else
        got_h=$(printf '%s' "$when" | sed -n 's/.*T\([0-9][0-9]\).*/\1/p' | sed 's/^0//')
        want_n=$(printf '%s' "$want_h" | sed 's/^0//')
        if [[ -n "$got_h" && "$got_h" != "$want_n" ]]; then
          warn "$tool last ran at ${got_h}:xx UTC but the crontab schedules hour $want_h — the schedule is not what the file says"
          fails=$((fails+1))
        else
          ok "$tool ran $((age/60))m ago at the scheduled hour, exit 0"
        fi
      fi
    fi
  done <<< "$statuses"

  # AND DID ANYTHING ACTUALLY GET COLLECTED. The loop above graded ticks; this
  # grades pulls, which is the question the tick used to answer for free.
  #
  # Asked of the container, because cadence.py holds the interval, the floor
  # and the "two intervals before it is overdue" rule, and a second copy of any
  # of them in awk would be a second copy to drift. -T and </dev/null for the
  # reason c4b9154 records: the first `docker compose exec` in this script ate
  # the rest of the deploy by consuming stdin.
  pulls=$("${SSH[@]}" "$HOST_IP" "sudo bash -c 'cd $STACK && \
    docker compose exec -T poolhound python -m poolhound.cadence'" \
    </dev/null 2>/dev/null || true)
  if [[ -z "$pulls" ]]; then
    warn "could not ask the container when each lab was last collected"
    fails=$((fails+1))
  else
    while IFS=$'\t' read -r tool hours every state; do
      [[ -z "$tool" ]] && continue
      case "$state" in
        never)
          warn "$tool has never completed a pull — the cadence is every ${every}h"
          fails=$((fails+1)) ;;
        overdue)
          warn "$tool last collected ${hours}h ago, and its cadence is every ${every}h"
          warn "  the tick is firing; the pull is not landing. Check the vendor login."
          fails=$((fails+1)) ;;
        *)
          ok "$tool collected ${hours}h ago, cadence every ${every}h" ;;
      esac
    done <<< "$pulls"
  fi
fi

echo
(( fails == 0 )) && c "Done — $((rows-1)) samples, nothing lost." || die "$fails check(s) failed"
