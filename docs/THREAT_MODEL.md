# Threat model

> SEC-20 wants one page: assets, actors, trust boundaries, abuse cases, and the
> control that mitigates each. This analysis already existed — it was scattered
> across `server.py`'s module docstring, `help.AQUALINKD_BIND`, `access.py`,
> `CLAUDE.md` and `docs/AGENT-PROTOCOL.md`. Scattered is how the most important
> caveat in the system (see B2) stayed written down for months without anyone
> having to decide whether it was still acceptable.

## What this is

One household's swimming pool: chemistry measurement, salt-cell and heater
control, alerting. Single tenant, single pool, one or two people. Published so
the code can be read, not run as a service for anyone else.

## Assets, in the order it would hurt to lose them

| Asset | Where it lives | Losing it costs |
|---|---|---|
| The pool history | CSVs on an Azure Files share | irreplaceable — years of readings that cannot be re-measured |
| Control of the equipment | AqualinkD on the house LAN, reached only by the Pi agent | a gas heater running unattended; a spa heated for a stranger |
| Lab + mail + AI credentials | Key Vault on the server; AES-256-GCM vault on a workstation | the household's accounts at two vendors and its outbound mail |
| The agent bearer token | `/etc/poolhound/agent.token` (0600) on the Pi, Key Vault on the server | the ability to write samples and acknowledge commands |
| Identities in `audit.csv` | the share | who did what, and the email addresses of the people who did it |
| The house address and the Pi's LAN address | `config.toml`, never the public build | the thing that turns "a pool" into "that pool, at that house" |

## Actors

| Actor | Reaches | Trusted to |
|---|---|---|
| Anonymous visitor | the public build, `/api/photo`, `/api/pool-shape/compute` | nothing |
| `view` | read routes | read, not act |
| `operate` | doses, readings, control, the assistant | act on the pool, not on the install |
| `admin` | settings, credentials, export | everything |
| The Pi agent | `/api/agent/*` with a bearer token | push samples, acknowledge commands |
| A sibling container | `poolhound:8787` directly, on the shared docker bridge | nothing, once `POOLHOUND_PROXY_SECRET` is set — see B2 |
| The two labs, the AI provider | outbound only | to return data we parse defensively |

## Trust boundaries

**B1 · internet → Caddy.** TLS, HSTS, Entra SSO via oauth2-proxy for
`/admin.html` and every gated route. Two POSTs are public by design and are
pure. *Mitigations:* `access.NEEDS` compared against Caddy's `@private` block in
both directions by `bin/selftest`; rate limits on the public pair; a pixel
ceiling in front of the image decoder and a semaphore bounding concurrent
decodes. The response headers are `poolhound/headers.py` — one owner for the
set, a per-page CSP whose digests come from the served bytes, and a check
holding the vhost's copy to it in both directions. HSTS is the vhost's alone;
it does **not** carry `preload`, which is a one-way commitment for the whole
domain and a decision rather than a default.

**B2 · Caddy → poolhound.** Identity is the `X-MS-CLIENT-PRINCIPAL-NAME`
header. Caddy strips all five identity headers on the way in and oauth2-proxy
sets the real one — but that stripping happens on the *vhost*, which is a path
only internet traffic takes, so for months this was the weakest boundary in the
system. poolhound's own comment stated the consequence exactly: *"these headers
are trusted because of DOCKER NETWORK MEMBERSHIP plus Caddy stripping them on
the one path from outside. Anything that can already open a socket to this
container can claim to be anybody."* Measured on the author's stack, that bridge
carries **eleven** sibling containers — mail, a media host, a cache, a
database, an uptime monitor, the proxy pair and three unrelated web apps — and
neither compose file declares a `networks:` key, so one HTTP request from any of
them reached
`/api/health`, was believed, and was handed the write token and admin.

*Mitigation:* Caddy presents `X-Poolhound-Proxy-Auth` **after** the strip block,
so a client supplying it has already had it removed, and `whoami()` compares it
with `hmac.compare_digest` **before** reading any identity header. A request
that cannot prove it came through the proxy falls through to the peer test,
which is what keeps a loopback workstation `local`. Verified across all five
states: unset leaves prior behaviour byte-for-byte; a forged identity with no
proxy header is anonymous; a guessed secret is anonymous; the proxy's own
request is believed; loopback is still local.

*Status:* **mitigated when `POOLHOUND_PROXY_SECRET` is set, and the deploy
grades it.** It is opt-in for the reason `access.py`'s policy is — an upgrade
that began refusing every identity header would lock the owner out of their own
pool — so the gap is made visible rather than assumed closed: `/api/health`
reports `proxy_auth`, and `bootstrap-server.sh --check` counts an unset secret
as a **failure** and prints the `openssl` line to fix it. Network isolation is
the belt-and-braces half and remains *documented rather than done*: on a merged
stack the network lives in the host's compose file, which this repository
deliberately does not carry.

**B3 · poolhound → the Pi.** The server cannot reach the pool. The agent
connects outward and polls; commands are validated against `commands.py` at
*both* ends, so a compromised server gets that vocabulary and nothing else — no
arbitrary URLs, no other hosts, no devices not named there. Expiry, idempotency
and per-device cooldowns are enforced on the Pi — and they now survive the agent
process, in `/run/poolhound/guard.json`, because every deploy restarts the unit
and each restart used to clear the 300s cooldown that stands between a
double-press and a short-cycled gas heater. `/run` is tmpfs, so this is still no
write to the SD card. *Known gap:* the bearer token is static, with no expiry
and no rotation runbook (see `AGENT-PROTOCOL.md`).

**B4 · the Pi → AqualinkD.** No authentication at all, by AqualinkD's design.
Anything on the house LAN can start the spa heater. Never exposed, never
proxied, no route from the server. This is a property of the LAN, and the
mitigation is that the agent is the only thing that crosses B3.

The agent itself listens in one place, `127.0.0.1:8791`, for two things. A
local page — poolhound local, on the author's install — can claim "the change you are about to see came through me"
(`POST /claim`), and it can read back the recent activity at the panel
(`GET /activity`). The Pi's own web server exposes both to the house LAN (as
`/poolclaim` and `/poolactivity` on the author's install), because a phone's
loopback is the phone. Neither has a path to the panel, the server's write API
or any credential.

- **The claim** accepts only a name from `commands.DOORS` and keeps it for 60
  seconds in memory. The worst a LAN caller can do is put a false `door:` label
  on a panel change that was going to be recorded anyway, under a prefix that
  cannot read as a person.
- **The activity list** says what changed and which way it came: the poolhound
  app, a named page, or the panel. It never says who. A command's `by` is a
  signed-in identity on the server and stays there; a selftest fails if it
  reaches the list. What is left tells a LAN reader when the pump or the spa
  was switched, which is what the panel itself shows anyone standing at it.

Both are tolerable without authentication for the same reason: anything that
can reach them can already drive the panel directly.

**B5 · poolhound → Key Vault.** The VM's managed identity; no application
credential on the host. The share's 0600 mount key is the one exception.

**B6 · poolhound → third parties.** Two labs (credentialed, parsed
defensively), one AI provider. `assistant.context()` sends a *fixed* field list
— volume, current readings, targets, recent doses — and never iterates rows,
because "send a summary" is the instruction that grows into the house address.

## Abuse cases

| # | Someone tries to… | Stopped by | Status |
|---|---|---|---|
| 1 | read this household's chemistry from the public page | build-time excision: the private panels are not in `index.html`, and the build asserts no address, token path or control endpoint survived | mitigated |
| 2 | start the spa heater from the internet | B1 + B3; the server has no route to the panel | mitigated |
| 3 | forge an identity and become admin | B2 — a proxy secret compared before any identity header is read, then the peer test | mitigated with `POOLHOUND_PROXY_SECRET` set; the deploy fails `--check` when it is not |
| 4 | OOM the container with one upload | pixel ceiling before decode + a 2-slot semaphore | mitigated |
| 5 | act above their role | `access.may()` at one choke point, deny-by-default, before the handler reads a byte | mitigated |
| 6 | write a dose from another origin | Host + Origin + per-process token, all three, demonstrated against a real exploit | mitigated |
| 7 | read the credentials | vault only; never logged, never rendered, never exported | mitigated |
| 8 | have the deploy start a second pool history | `has_history()` before anything else, and the deploy refuses to seed over a non-mount | mitigated |
| 9 | exhaust the AI budget or the lab quotas | per-hour budget, cadence floors, audited | mitigated — the budget is written to `ask-budget.json` and survives a restart; a stamp from the future is dropped |
| 10 | delete the history, or corrupt it with a bad write | `bin/backup`: a nightly dated tarball off the share, taken under the writers' own locks, 30 days kept. RPO 24h, RTO minutes | mitigated on this host. `POOLHOUND_BACKUP_OFFBOX` adds a second copy that survives losing the VM, refuses a destination on the same filesystem by `st_dev`, and is graded by `--check`; it is **not set on this deployment** |
| 11 | run script in the owner's browser via a lab's error text | `/api/refresh` hands the page a collector's own last line, including vendor error bodies and stderr; it is rendered with `textContent`/`createTextNode` and the whole block is asserted free of `innerHTML`. **And now a second layer**: the policy names each page's one inline script by SHA-256 digest, taken from the bytes being served, so `script-src 'unsafe-inline'` is gone and `script-src-attr 'none'` refuses an event-handler attribute outright | mitigated — a working payload was demonstrated against the pre-fix page, and `bin/drive` reads the browser's own console back to prove the policy matches the page |
| 12 | escape the container to root on the host | a backup filename chosen inside the bind mount used to be interpolated into `sudo sh -c`; the name is now never re-parsed and only an integer comes back | mitigated |
| 13 | read a credential out of an append-only record | `runlog` takes `by` and `cfg` keyword-only, and `_safe_by()` records anything that is not a `str` as its TYPE rather than its contents | mitigated — the config dict reached that column for two nightly runs |

## Open items

1. **The agent token in steady state.** Rotation now has a window, a runbook
   and an expiry on the *outgoing* half — see `docs/AGENT-PROTOCOL.md`. The
   token in use still has no expiry and nothing binds it to the device, so a
   copy of it is a copy of the agent's whole authority: push fabricated
   samples, acknowledge commands. Client credentials with an expiry is the next
   step and is not worth it for one device.
2. **There is an off-box copy now, on a workstation, and it is the weaker of
   the two mechanisms.** `deploy/pull-backup.sh` runs on a daily LaunchAgent
   (04:10 local), pulls the newest server tarball, verifies it opens *and*
   holds `samples.csv`, keeps 30 days, and sets 0700/0600 on what it writes.
   Measured 2026-10-07: the first copy landed, 9 files, 176 KB of `samples.csv`
   inside. **Losing the VM no longer loses the history.**

   What is still open is the stronger mechanism and one new exposure:

   * `POOLHOUND_BACKUP_OFFBOX` — a server-side push to a mount, which does not
     depend on a laptop being awake or on `az` refreshing a token silently —
     is implemented and **unset**. It refuses a destination on the same
     *filesystem* as either the history or the local backups, by `st_dev`
     rather than by name, because a second directory on the same disk is the
     original defect wearing the fix's clothes.
   * the pulled archive carries `config.toml` and `audit.csv`, so **the house
     address, the credential paths and every identity are now on a portable
     machine**. That is a deliberate trade against losing the history
     entirely, and it is the reason for the file modes. A lost or stolen laptop
     is now in this threat model's scope in a way it was not yesterday.
   * nothing outside that machine notices when the pull stops.
     `pull-backup.sh --check` answers it and exits non-zero; it is not wired to
     the Uptime Kuma push monitor that watches `bin/watch`.
3. **The dependency and secret scans are new, and one of them has never
   failed.** `osv-scanner` and `gitleaks` run in `supply-chain.yml` on every
   push and daily on a schedule; measured 2026-10-07, the tree is clean in both
   (9 packages, no advisories; 234 commits and 12.18 MB, no findings). A gate
   that has only ever been green is a gate whose failure path is untested —
   unlike every detector in `checks_structure.py`, neither of these can be
   shown to go red without a real finding.

### Closed since this page was written

| Was open | Closed by |
|---|---|
| B2 / abuse case 3 — identity trusted on docker network position | a proxy secret Caddy presents after the strip block, compared with `hmac.compare_digest` before any identity header is read |
| **B2's second half — eleven containers could open a socket to `:8787`** | two dedicated docker networks in the host's compose file, applied 2026-10-07. `poolhound_edge` carries caddy → poolhound; `poolhound_mail` carries poolhound → postfix, because `smtp_host = "postfix"` and a poolhound isolated with Caddy alone would serve every page and silently never deliver another alarm. Neither is `internal: true` — egress to Key Vault, IMDS, two labs, the AI provider and the heartbeat is load-bearing. **Measured after: 2 neighbours, 0 on the shared bridge**, and `socket.create_connection(("postfix", 587))` from inside the container still succeeds |
| Abuse case 10 — one copy of an irreplaceable history | `bin/backup`, nightly, off the share, graded by `--check` and shown on the Collection tab |
| Abuse case 9 — an AI budget a restart handed back | the spend is written to the data directory and read on the next start; `docker compose up --force-recreate` no longer refunds it |
| "no rotation runbook" — which was really "no correct order to rotate in" | a second accepted token for a stated window, with a mandatory deadline, and the half-finished state reported by `/api/health` |
| `script-src 'unsafe-inline'` — the one directive that could not help with abuse case 11 | a digest taken from the page being served, verified in Chromium by `bin/drive` rather than asserted |
| A dependency scan that only ran at deploy time | `supply-chain.yml`, on every push and on a daily schedule, because a CVE is published without anybody touching this repository |
| Two owners of the security headers, sending different sets | `poolhound/headers.py`, with the vhost's copy held to it in both directions |

Both are recorded here rather than deleted: the value of this page is that the
most important caveat in the system stayed written down for months without
anyone having to decide whether it was still acceptable, and a page that erases
its own history makes that easier, not harder.

## Measured against a standard

This page is the analysis this system deserves on its own terms: its assets,
its actors, its abuse cases. [ASVS-L2.md](ASVS-L2.md) asks the opposite
question — whether a published standard would recognise the controls — against
OWASP ASVS 5.0 Level 2, chapter by chapter, with each verdict marked by the
evidence behind it: gated by CI, driven in a browser, or read.

The two agree about what is open, which is the useful outcome. They disagree
about emphasis: ASVS weights confidentiality, and the asset at the top of the
table above is an irreplaceable history whose loss would be about integrity and
availability.

## Keeping this current

Update it when a boundary moves: a new route class, a new outbound third party,
a change to how identity is established, or a new store. `SECURITY.md` points
here; `bin/selftest` asserts no real hostname reaches this file like any other.
