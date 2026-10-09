# the server

poolhound's system of record since 2026-09-12. Before that it ran on a
workstation; the history from that period is in the same CSVs and nothing was
lost in the move.

## The shape of it

```
                    the internet
                         │
              ┌──────────┴──────────┐
              │  Caddy (the server)      │  TLS, and the only thing that decides
              │  poolhound.example.com │  who may do what
              │  pool.example.com      │
              └──────────┬──────────┘
        public ──────────┼────────── Entra ID (oauth2-proxy)
        stats, chemistry │ control, chemicals, settings, every write
        calculator, help │
              ┌──────────┴──────────┐
              │  poolhound          │  python:3.12-slim, expose 8787,
              │  (docker)           │  NEVER published
              └────┬──────────┬─────┘
                   │          │
       /mnt/poolhound   Key Vault   poolhound_vault   /api/agent/* ← bearer
       Azure Files      the vault   a local docker      token, no Entra
       the CSVs         passwords   volume, /vault            ↑
                                          ┌──────────┴──────────┐
                                          │  the Pi             │
                                          │  poolhound-agent    │  connects OUT.
                                          │  → AqualinkD        │  Nothing dials in.
                                          └─────────────────────┘
```

Three classes of request, and they must not be confused:

- **public** — the stats, the chemistry reference, the volume calculator. No
  sign-in by design: they are the useful, harmless part.
- **private** — control, the dose log, settings, every write. Entra ID.
- **agent** — `/api/agent/*`, bearer token at the application, and it **must**
  bypass Entra: oauth2-proxy answers a machine with an HTML sign-in page, which
  an agent cannot satisfy and would retry for ever.

`/api/health` is the awkward one: both builds call it, so it can be neither.
It asks oauth2-proxy, passes the identity through when there is a session, and
on 401 proxies the request anyway, anonymously.

## Why the valuable things are not on the server

The CSVs are on an Azure Files share and the passwords in Key Vault, read
through the VM's managed identity. The server holds neither, so it can be rebuilt,
replaced or moved without losing a reading or re-typing a password.

The services are `waterguru`, `leslies`, `smtp` and `ai` — the last being the
key for the Ask AI tab, which is a credential like any other and goes the same
way. Its endpoint and model are ordinary settings in `config.toml`; only the
key is a secret. An install that never configures it has an Ask AI tab that
says so and sends nothing.

One qualification, added when the credential store was given a durable home.
Key Vault answers for the services named `poolhound-<service>-user` and
`-password`. Anything else — SMTP is the realistic case — falls back to the
encrypted vault, and that now lives in a docker volume (`poolhound_vault`,
mounted `/vault`, mode 0700) rather than in the container layer, where a
`--force-recreate` was deleting it while Settings reported the credential as
stored. A container rebuild keeps it; a VM rebuild does not, so anything you
care about surviving the server entirely belongs in Key Vault. `vault.put()` now
refuses when Key Vault holds the secret OR when it cannot be reached, rather
than writing a local copy that the vault would shadow.

Deliberately not on the share: it is mounted `file_mode=0660`, which is the
wrong permissions for a secret.

`config.toml` lives on the share, at `/mnt/poolhound/config.toml`. That is the
only durable place for it — `save_settings` refuses to write anywhere else when
`POOLHOUND_DATA` is set, because a config inside the container is a setting that
reports success and is gone by morning. `bootstrap-server.sh` seeds it from
`config.example.toml` if the share has none.

Key Vault rather than an encrypted file because the file needs a key, and on a
server that key has to sit on the same disk as the thing it protects. That is a
lock with the key taped to the door. A managed identity has no key at all.

## Putting it back

```bash
az login
./deploy/bootstrap-server.sh            # provision, deploy, verify
./deploy/bootstrap-server.sh --check    # verify only, change nothing
```

It recreates the mount, the container, the vhost, the cron and the image. It
does **not** recreate the CSVs, the config, the passwords or the agent token —
and that is the design, not a gap. A script that could recreate the secrets
would be a script that contains them.

If the share is empty it says so and refuses to call the run a success.

## Operating it

```bash
az ssh config --file /tmp/poolhound-ssh.config -g "$RG" -n "$VM" --overwrite
ssh -F /tmp/poolhound-ssh.config "$SERVER_IP"

sudo docker compose -f $STACK/compose.yml logs poolhound --tail 50
sudo grep poolhound /var/log/syslog | tail          # did cron fire
sudo $STACK/poolhound/collect.sh render
```

The Entra SSH certificate is short-lived — re-run `az ssh config` when it
expires, which is roughly hourly.

On the Pi — from a machine on the house LAN, which is the only place that can
see both it and the server. `PI_USER` and `PI_KEY` default to the current user and
`~/.ssh/id_ed25519`; set them if that is not the login.

**Two things bite here every time.** `PI_KEY`'s default is very likely not the
key that authorises you on the Pi, so `--check` fails at the SSH step before it
has told you anything. And if `site.pi_host` is an mDNS `.local` name, **ssh on
macOS will not resolve it even though `ping` and `dscacheutil` both do** — so it
reports the host as unknown while the Pi is sitting there answering. Set
`PI_HOST` to the address, or give the Pi a `Host` entry in `~/.ssh/config`:

```bash
PI_HOST=<address> PI_KEY=~/.ssh/<key> ./deploy/update-pi.sh --check
```

```bash
./deploy/update-pi.sh --check     # revision, service state, pump telemetry
./deploy/update-pi.sh             # ship it and restart

systemctl status poolhound-agent  # on the Pi itself
journalctl -u poolhound-agent -n 50
```

**After any change to the unit's sandbox, watch the watchdog timestamp actually
move.** This is the one property neither a unit file nor `bin/selftest` can
establish, because it only advances when systemd has received a datagram:

```bash
systemctl show poolhound-agent -p WatchdogTimestamp -p NRestarts -p MainPID
sleep 60 && systemctl show poolhound-agent -p WatchdogTimestamp -p NRestarts
```

The timestamp should advance about every 30 s (`agent.WATCHDOG_PING_S`) against
the 120 s deadline, with `NRestarts` and `MainPID` unchanged. `WatchdogSec`
shipped once with `RestrictAddressFamilies=AF_INET AF_INET6` and no `AF_UNIX`:
`socket()` raised `EAFNOSUPPORT`, the agent's non-fatal notify handler swallowed
it exactly as designed, not one ping was ever sent, and systemd killed a
perfectly healthy agent every two minutes — while the journal showed only the
agent cheerfully logging that it was *about* to ping. If `NRestarts` is climbing
by one every two minutes, that is this.

Restarts during a real server outage land about twelve minutes apart
(`LINK_STALE_S` 600 plus `WatchdogSec` 120) against `StartLimitBurst=20` per
300 s, so a long outage cannot rate-limit the unit into a dead state.

```bash
systemctl status poolhound-agent-ensure.timer   # the healer is enabled
systemctl list-timers poolhound-agent-ensure.timer
journalctl -u poolhound-agent-ensure --since -1d   # silent unless it ACTED
```

A quiet healer is the healthy state: it logs one line at warning level when it
had to start the agent, and nothing at all otherwise.

`--check` compares the Pi's `REVISION` against the local git rev and exits
non-zero on a mismatch. It used to print `agent.py`'s mtime, which `rsync -a`
copies from the source checkout — so it reported when the repo was cloned, not
what was deployed, and could not detect the three-week drift this script exists
to prevent. The first run after that was fixed found the agent 34 commits
behind.

AqualinkD on the Pi listens on **0.0.0.0:8000 with no authentication of any
kind**. Anything on the house LAN can drive the pool. That is why the server has no
route to it and the agent connects outward instead.

## Run every tool through `collect.sh`, never from a shell on the server

```bash
sudo $STACK/poolhound/collect.sh render     # yes
cd ~/poolhound && bin/chem acid 16 floz                      # NO
```

Not a style preference. `locking.exclusive()` keys its lock directory on the
absolute path of the data directory, and the two namespaces disagree about what
that path is: inside the container it is `/data`, on the host it is
`/mnt/poolhound`. They hash differently, and the lock files are in the
container's `/tmp` rather than the host's anyway — so a writer started from a
host shell takes a lock **nobody else is looking at** and appends to the share
with no mutual exclusion and no warning. A render holds that lock for about half
a second every six hours, and a cron collector holds it too; that is the window
a host-side `bin/chem` would land in.

`collect.sh` runs its argument with `docker compose exec`, inside the one
container every other writer is also inside, which is where the invariant
actually holds. The same applies to anything else that writes: `bin/chem`,
`bin/wg-collect`, `bin/leslies`, `bin/render`.

## Files here

| | |
|---|---|
| `bootstrap-server.sh` | provision and verify, from nothing |
| `Dockerfile` | python:3.12-slim; slim not alpine because `cryptography` has no musl wheel |
| `requirements.txt` | pinned; only the WaterGuru collector needs any of it |
| `compose.poolhound.yml` | the service, merged into the server's compose.yml |
| `compose.yml` | **the whole stack** — Caddy and poolhound, for a server running nothing else. This is the one to use |
| `poolhound.caddy` | the site: what is public, what is behind the sign-in, and why it is one `route` block. Addresses come from `{$POOLHOUND_PUBLIC_HOSTS}` |
| `caddy` | `Caddyfile` (the wrapper that imports the rest) and the two auth snippets — `auth-password.caddy` and `auth-forward.caddy` |
| `poolhound-cron` | the collectors; the sampler is NOT here, it is on the Pi |
| `poolhound-logrotate` | keeps the collectors' logs from filling the server's disk |
| `collect.sh` | runs one collector inside the running container |
| `kuma-heartbeat.py` | creates the Uptime Kuma PUSH monitor that watches `bin/watch`. Nothing inside poolhound can report its own absence, and an HTTP monitor would not have caught the forty-hour outage either — the site was up the whole time; what was dead was the thing that asks questions |
| `poolhound-agent.service` | the Pi unit. `update-pi.sh` installs it; it is NOT ordered after `aqualinkd.service`, which is `WantedBy=multi-user.target` *and* `After=multi-user.target` and so drags anything behind it into an ordering cycle. It carries `WatchdogSec=120` with `NotifyAccess=main`, and `RestrictAddressFamilies` **must** keep `AF_UNIX` — the ping is an AF_UNIX datagram, and without it systemd kills a healthy agent every two minutes |
| `poolhound-agent-ensure.service` | the healer: one shot, starts the agent if it is not running, and says so at warning level only when it had to |
| `poolhound-agent-ensure.timer` | runs the healer 2 min after boot and every 5 min. Covers what `Restart=always` cannot — a start job systemd deleted, or a service stopped by hand and forgotten. **To stop the agent deliberately, stop this timer too** (`systemctl stop poolhound-agent-ensure.timer poolhound-agent`) or `systemctl mask poolhound-agent`; otherwise it comes back within 5 min |
| `update-pi.sh` | ships the agent to the Pi, and `--check` compares its REVISION |
| `poolhound-local.html` | **poolhound local**: a copy of the optional page on the author's Pi — friendly circuit names, verified switching, and the agent's activity list — with the hostname removed. Nothing installs it; it is a reference, and `bin/screenshots` photographs it against a synthetic AqualinkD. See "Naming the door a panel change came through" |
| `pull-backup.sh` | run on a WORKSTATION: brings the newest server tarball off the VM, verifies it opens and holds `samples.csv`, prunes to 30 days. Read-only on the server, and it writes no CSV anywhere — it is not a collector, which is the distinction `refuse_off_deployment()` is about. `--check` grades what is already local and exits non-zero when it has gone stale |
| `launchd` | `com.poolhound.backup-pull.plist`: the daily 04:10 schedule for the above, on a Mac. launchd rather than cron because macOS sleeps and cron does not fire while it is asleep — a backup that silently does not happen on the nights it matters |
| `server.env.example` | the template for `server.env`: resource group, VM, vault, share |
| `server.env` | yours; gitignored. Names, not secrets — the secrets stay in Key Vault. It is also the ONLY place this deployment's hostnames and stack path are written down |

## Running it anywhere: the short version

For a server that runs poolhound and nothing else, which is what this expects.
No cloud account, no identity provider, no second repository.

```bash
cp deploy/server.env.example deploy/.env         # then edit: PUBLIC_HOSTS, ACME_EMAIL

docker run --rm caddy:2 caddy hash-password --plaintext 'your password' \
  | sed "s|^|owner |" > deploy/caddy/users.caddy  # who may sign in

docker compose -f deploy/compose.yml --env-file deploy/.env up -d
```

That is the whole thing. Caddy gets a certificate for `PUBLIC_HOSTS`, the
container seeds `config.toml` beside the readings on first run and tells you it
did, and you set the pool's volume on the Settings tab.

**The username is the identity.** `access.py` decides what each person may do,
keyed on the name in `users.caddy` — so `admins = ["owner"]` under `[access]`
in `config.toml` gives that person everything, and anybody else you add lands
on `view` until you say otherwise.

**A bcrypt hash goes in a FILE, never in the env file.** It contains three `$`
and docker compose interpolates them away, which truncates the hash and rejects
every correct password with a 401 indistinguishable from a wrong one. Measured,
not guessed: the first version of this passed the hash as an environment
variable and the end-to-end test refused the right password.

**`users.caddy` is gitignored.** Missing or empty, nobody can sign in and the
public half still works — the right failure for a forgotten setting.

### Passwords here, or an identity provider

`AUTH_MODE` picks one of two snippets, and they are the only difference:

| `AUTH_MODE` | What signs you in | Needs |
|---|---|---|
| `password` (default) | Caddy checks a bcrypt hash from `users.caddy` | nothing else |
| `forward` | oauth2-proxy, and whatever provider it fronts — Entra ID, Google, anything it speaks | a container at `oauth2-proxy:4180`, a registered application, and `OAUTH2_HOST` |


## Backups — what is recoverable, and what is not

The share is the only place the history exists. `bin/backup` writes a dated
tarball of every irreplaceable file to **the host's local disk**, which is the
one place guaranteed to fail separately from the share. Cron runs it at 02:40;
`bootstrap-server.sh --check` grades the newest one and fails if there is none
or it is over 48 hours old.

| | |
|---|---|
| **RPO** | 24 hours — the nightly tarball |
| **RTO** | minutes: `tar xzf` into `/mnt/poolhound` with the container stopped |
| Retained | 30 days, pruned by the job itself |
| Covers | a corrupting write, a bad migration, a mistaken delete, losing the share |
| Does **not** cover | losing the VM and its local disk — **unless `POOLHOUND_BACKUP_OFFBOX` is set, and it is not** |

That last row is the honest limit, and it is now one environment variable
rather than a paragraph with no instruction in it.

`POOLHOUND_BACKUP_OFFBOX` takes a **directory**, not a protocol: a blobfuse
mount, an SMB mount of another machine, a second Azure Files share, a disk.
Nothing new is installed and nothing holds a credential of its own. The
tarball is written locally first, closed, sized and pruned, and only then
copied — so the fast local copy keeps its RPO and this is the one that survives
the host.

**It refuses a destination on the same filesystem**, by `st_dev` rather than by
name, for either the history or the local backups. A second directory on the
same disk is the original defect wearing the fix's clothes: it reads as
configured, passes every check that counts tarballs, and dies with the host it
was meant to outlive. `st_dev` is the question actually being asked, and it
answers correctly for a bind mount, a symlink and a second path into one volume
— which are the three ways somebody arrives at this by accident.

Unset is an honest state and `--check` says so on every run. A destination that
IS set and holds no recent tarball is a **failure**, not a note: somebody
believes there is a copy off this host and there is not.

```bash
$STACK/poolhound/collect.sh backup          # run one now
ls -t /var/lib/poolhound/backups | head     # what is recoverable
```

## Rotating the agent token

The server accepts two tokens for a stated window, so the Pi and the server can
be moved one at a time with no gap in the only sampler this pool has. The
sequence, what each slot is, and the four things a second slot is refused for
are in [../docs/AGENT-PROTOCOL.md](../docs/AGENT-PROTOCOL.md). `--check` warns
on every run while a rotation is open, because the step people skip is the last
one.

## Proving a request came through Caddy

Identity is an HTTP header. Caddy strips the client's copy and sets the real one
from oauth2-proxy — but that happens on the **vhost**, and a sibling container on
the same docker network does not go through it. Without the secret below,
poolhound trusts any identity header from anything that can open a socket to it,
which on a shared stack is every other service on the host.

```bash
openssl rand -hex 32            # put it in the env file that feeds BOTH services
# server.env (or the stack's .env):
POOLHOUND_PROXY_SECRET=<the hex>
docker compose up -d caddy poolhound        # both, or the two disagree
./deploy/bootstrap-server.sh --check        # says "proven by a shared secret"
```

It is **opt-in**: unset, nothing changes, and `--check` and `/api/health`
(`proxy_auth: false`) both report the boundary as unproven rather than letting it
look closed. Set it and a sibling container gets `anonymous`; a loopback
workstation is still `local`.

Belt and braces, and better: give poolhound its own docker network with only
Caddy attached. `deploy/compose.yml` (the standalone stack) is the one to edit;
on a merged stack the network lives in the host's own compose file, which this
repository deliberately does not carry.
**Switching, standalone** — one setting and one command:

```bash
sed -i 's/^AUTH_MODE=.*/AUTH_MODE=forward/' deploy/.env
docker compose -f deploy/compose.yml --env-file deploy/.env up -d caddy
```

**Switching, on a host where poolhound shares Caddy with other services:**

```bash
sed -i 's/^AUTH_MODE=.*/AUTH_MODE=forward/' deploy/.env
ssh "$SERVER_IP" 'sudo bash -c "
  cd $STACK
  cp poolhound/src/deploy/caddy/auth-forward.caddy sites/00-poolhound-auth.caddy
  docker compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile </dev/null
  docker compose exec -T caddy caddy reload   --config /etc/caddy/Caddyfile --adapter caddyfile </dev/null"'
```

**Validate before you reload, always.** On a shared host every other vhost
rides on that config: a Caddyfile that fails to adapt takes all of them down,
not just this one.

**PIN `AUTH_MODE` rather than relying on the default.** An install that leaves
it unset gets whatever the reader that happens to bring it up falls back to —
and those two readers disagreed once, so a standalone bring-up of an
Entra-backed install would have swapped the sign-in for a bcrypt file nobody
had written. `bin/selftest` now asserts every reader states the same default,
but the setting being explicit is the thing that makes it a decision rather
than an accident.

**The two mechanisms are interchangeable by construction.** Each defines the
same two Caddy snippets — `poolhound_auth` (what gates the private routes) and
`poolhound_identity` (how `/` and `/api/health` learn who you are without
turning anyone away). The vhost imports both names and has no opinion about
how identity arrives, so the route list stays in one file. `bin/selftest`
compares the names the vhost imports against the names *every* snippet
defines, because a name missing from one of them breaks only the mode you are
not currently running.

Both set the same identity header, which is the seam poolhound actually reads —
`access.py` never learns which one ran. And both leave `/api/agent/*` alone:
the Pi authenticates with a bearer token at the application, because a daemon
cannot satisfy a sign-in page.

**The route list lives in `poolhound.caddy` only.** Two Caddyfiles, one per
auth mechanism, would be two routing tables that can disagree about which paths
are private — and `bin/selftest` compares that list against `access.NEEDS` by
reading one file.

### Secrets without a cloud

`vault.py` tries Azure Key Vault, then an encrypted store, then a mode-600
file. `compose.yml` sets no `POOLHOUND_KEYVAULT`, so a standalone install uses
the encrypted store in its own volume and needs no Azure at all. Set the
WaterGuru and Leslie's logins on the Settings tab; nothing else is required.

## Naming the door a panel change came through

The agent watches the panel and pushes a sample the moment anything changes, so
**every** change is captured whichever of the six doors it came through —
AqualinkD's own UI, poolhound local (the optional page on the Pi), `aquapda_sim`, `aqmanager`, the
wall remote, the keypad, or the panel's own schedules. That needs no
configuration and is what the agent does on its own.

Naming *which* door is optional, and only a door wired to say so can do it. The
agent listens on `127.0.0.1:8791` for a claim (`POOLHOUND_CLAIM_PORT` in
`/etc/poolhound/agent.env` moves it; the first default, 8788, was already
taken on the Pi this runs on):

```
POST /claim   {"door": "pool.html"}
```

It records the claim and nothing else: there is no path from that port to the
panel, to the server's write API, or to any credential. A door must be listed in
`commands.DOORS` to be recorded at all, and what reaches the audit log is
`door:<name>` — never an identity, because the claim arrives from an
unauthenticated page and a name nothing verified is worse in an append-only log
than no name at all.

**Two pieces live outside this repository, and nothing here can check them.**
That is stated rather than hidden: `bin/selftest` gates what is in the tree, and
these are on the Pi. Both have been wired on the author's Pi since 2026-10-08,
and they are kept with that Pi's own configuration, not here. A copy of the
page that makes the call is `deploy/poolhound-local.html` — a reference and the
subject of the landing page's picture, not something any script installs, so
the Pi's own copy is still the one that runs and the two can differ.

1. **A reverse-proxy hop**, because a browser cannot reach the Pi's loopback — a
   phone's `127.0.0.1` is the phone. The Pi's web server already proxies
   `/poolapi/` to AqualinkD; this is the same idea pointed at the agent. In
   Caddy, rewritten rather than stripped, because the agent answers only
   `POST /claim`:

   ```
   handle /poolclaim {
       rewrite * /claim
       reverse_proxy 127.0.0.1:8791
   }
   ```

2. **One call from the page**, before every command. It must never block the
   command: wait at most a moment for it, swallow any failure, then act. A
   claim that does not arrive costs the door's name, not the record of the
   change, and a poolhound local that cannot switch the pump while the agent restarts
   is the wrong trade. It is awaited briefly rather than fired and forgotten so
   that it lands before the change it explains; the agent holds it for 60s
   (`CLAIM_TTL`) and attaches it to the next change it sees.

   ```js
   function claim() {
     var ctl = new AbortController();
     var t = setTimeout(function () { ctl.abort(); }, 1500);
     return fetch('/poolclaim', {method: 'POST', cache: 'no-store',
       signal: ctl.signal, headers: {'Content-Type': 'application/json'},
       body: JSON.stringify({door: 'pool.html'})})
       .catch(function () { return null; })
       .then(function () { clearTimeout(t); });
   }
   // then: claim().then(function () { return send(path, value); })
   ```

**Checking it, without writing a false row.** A made-up door is refused before
anything is recorded, so it proves the route reaches the agent and leaves the
log alone:

```bash
curl -s -X POST -H 'Content-Type: application/json' \
     -d '{"door":"probe"}' http://<pi>/poolclaim
# {"error": "unknown door"}  → 400: route and agent both up
# 502 from the web server     → the agent is not listening
```

Do not probe with a real door name. The claim would stand for a minute and be
attached to whatever the panel did next.

If either is missing, changes from that page are still captured — the audit row
just reads `no door said it was theirs`, which is the honest answer.

### poolhound local's activity list

The same port answers one read, `GET /activity`: the last 200 things that
happened at the panel, newest first, each with what changed and which way it
came — `poolhound app` for a command this agent executed, the door's name for
a claimed change, `panel or AqualinkD` for the rest. A change the app just
caused is not listed a second time when the watcher sees it arrive. It never
carries who sent a command; see `docs/THREAT_MODEL.md`, B4.

It lives in `/run/poolhound/activity.json`, so it survives the agent
restarting and every deploy, and starts empty after the Pi reboots. Nothing is
written to the card. While the agent is not running nothing is recorded at
all — the panel keeps no log of its own to fill the gap from.

The page reaches it through the same kind of hop as the claim:

```
handle /poolactivity {
    rewrite * /activity
    reverse_proxy 127.0.0.1:8791
}
```

## Why this directory has no real hostnames in it

This repository is public. The vhost and the crontab are **templates** carrying
`@@PLACEHOLDERS@@`, and `bootstrap-server.sh` fills them from `server.env` when it
installs them — `PUBLIC_HOSTS` and `OAUTH2_HOST` into the Caddyfile, `STACK`
into the cron lines. A placeholder that survives installation stops the deploy,
because a Caddyfile with `@@PUBLIC_HOSTS@@` in its site address will not adapt
and failing here names the setting rather than a line number.

`server.env` is gitignored and is the only place this deployment's names are
written down. Keeping it in a small private directory and symlinking it in is
the arrangement that works:

```bash
ln -sf ~/code/poolhound-private/deploy/server.env deploy/server.env
```

There is deliberately **no second, concrete copy** of the vhost kept anywhere.
Two routing tables that can disagree is the drift the four gates exist to
prevent, and no gate can see across two repositories. `bin/selftest` asserts
that no tracked file names a real host or this deployment's stack directory, so
the property cannot quietly stop being true.

## Two traps worth reading before editing

**Caddy sorts bare directives into its own order**, not the order written. A
`request_header -X-…` written before `forward_auth` runs *after* it and deletes
the identity that had just been established. That is why the vhost is one
`route` block. Verify any change against the adapted JSON, not the source:

```bash
docker compose exec caddy caddy adapt --config /etc/caddy/Caddyfile | python3 -m json.tool
```

The eight header DELETEs must appear **before** the oauth2-proxy hop.

**`sudo wc -l < file` does not work.** The shell opens the redirect as the
unprivileged user before sudo runs, so a root-readable file reports "Permission
denied" — which once made the verifier announce that 390 intact readings were
missing. Use `sudo cat file | wc -l`.
