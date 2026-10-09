# poolhound against OWASP ASVS 5.0, Level 2

> Written for whoever has to decide whether this is safe enough to keep running
> — the owner, or somebody reviewing it on their behalf. It assumes
> [docs/THREAT_MODEL.md](THREAT_MODEL.md) has been read: that page says what is
> worth protecting and from whom, and this one asks a published standard
> whether the protections are the usual ones.

**Standard:** OWASP Application Security Verification Standard 5.0.0 (May 2025),
Level 2 — "recommended for most applications handling sensitive data".
Seventeen chapters, V1 through V17.

**Assessed:** 2026-10-07, against `main`.

## How to read this, and what it is not

**It is a chapter-level assessment, not a requirement-by-requirement
certification.** ASVS 5.0 L2 is roughly 170 requirements. What follows is a
verdict per chapter with the specific controls that carry it and the specific
places it falls short — which is the form that is useful for deciding whether
to keep running this, and is honest about the effort behind it. A
requirement-by-requirement sign-off is a different document and a much longer
afternoon.

**The verdicts are evidence-weighted and the evidence differs.** Some are
checked by `bin/selftest` on every push and have a breaker in
`checks_structure.py` proving the check can go red. Some were verified in a
browser. Some are a reading of the code. The table says which, because "we
assert this" and "a gate fails if this stops being true" are not the same claim
and this project has a file about what happens when they are confused.

| mark | means |
|---|---|
| **gated** | a check in CI fails if this stops being true |
| **driven** | verified in a real browser, by `bin/drive` |
| **read** | established by reading the code, with no automated check behind it |
| **n/a** | the chapter addresses something this application does not do |
| **gap** | ASVS L2 asks for something that is not here |

## Scope, and three things that make this unusual

**Single tenant, one household, two or three people.** There is no tenancy
boundary, no customer data belonging to anyone else, and no registration flow.
Requirements written for multi-tenant SaaS are marked n/a rather than claimed.

**Authentication is delegated and is not in this codebase.** Microsoft Entra
via oauth2-proxy, enforced by Caddy, in front of `/admin.html` and every gated
route. poolhound never sees a password, never issues a session, and never
handles a token exchange. That moves V6, V7, V9 and V10 almost entirely out of
this repository and into the deployment — which is a real answer, not a dodge,
but it means an ASVS pass on this code is not an ASVS pass on the system. The
deployment's half is in [deploy/README.md](../deploy/README.md).

**The highest-value asset is not confidentiality.** It is integrity and
availability of an irreplaceable measurement history, and the ability to start
a gas heater. ASVS weights confidentiality heavily; where this assessment
diverges from the standard's emphasis, that is why.

---

## The chapters

### V1 · Encoding and Sanitization — **gated**

Output encoding is the chapter where this project has a scar. `/api/refresh`
returns the last line a collector printed, which is routinely a vendor's own
error body, and all of it went into `msg.innerHTML` by string concatenation on
**admin.html** — the build that holds the pool controls. A payload of
`<img src=x onerror=…>` fired the handler, demonstrated against the pre-fix
page.

The fix was `textContent` and `createTextNode` rather than an escaping helper,
on the argument that there is then no call left to forget, and the **whole
block** is asserted free of `innerHTML` rather than the two lines that
interpolated that day. Server-side, `html.escape` is applied at every
interpolation into the template and `render.fill()` raises on an unfilled
placeholder.

Since 2026-10-07 there is a second layer that does not depend on anybody
remembering: the Content-Security-Policy names each page's one inline script by
SHA-256 digest, so injected markup cannot execute even if it is reached. See
V3.

### V2 · Validation and Business Logic — **gated**

Every write route validates against a declared shape before the handler reads a
byte, and the dose plausibility bounds are enforced in one place rather than at
the route that happened to need them first — a defect this project shipped, in
which the product accepted a dose it then refused to let anybody fix.

Business-logic controls that ASVS asks for and that are here for their own
reasons: per-device cooldowns and idempotency on commands, enforced **on the
Pi** and surviving the agent process; a rate limit on writes; a spending ceiling
on the assistant that now survives a restart; `commands.py` as a closed
vocabulary validated at both ends, so a compromised server gets that vocabulary
and nothing else.

`migrate_columns` refusing a row longer than its header is a business-logic
control wearing a data-integrity hat: those overflow fields are recorded
readings, and it stops and asks for a person rather than dropping them.

### V3 · Web Frontend Security — **gated** and **driven**

The strongest chapter, and the one most recently worked on.

| control | state |
|---|---|
| `Content-Security-Policy` | `default-src 'none'`, no external origin anywhere, no `'unsafe-eval'`, `frame-ancestors 'none'`, `base-uri 'none'`, `form-action 'none'` |
| inline script | named by **SHA-256 digest** taken from the bytes being served. No `script-src 'unsafe-inline'` |
| inline event handlers | `script-src-attr 'none'`. There is not one in either build, and now there cannot be |
| inline style | `<style>` elements by digest in `style-src-elem`; `'unsafe-inline'` confined to `style-src-attr` for the ~70 chart `style="…"` attributes; plain `style-src` last as the fallback |
| CSRF | three layers on every write — loopback `Host`, `Origin` when present, and a per-process token from `/api/health`. Demonstrated against a working exploit |
| CORS | no `Access-Control-Allow-Origin` is ever sent; withholding it is what makes the token secret. Preflights refused outright |
| clickjacking | `frame-ancestors 'none'` plus `X-Frame-Options: DENY` for older browsers |
| other headers | `nosniff`, `Referrer-Policy: no-referrer`, a `Permissions-Policy` naming and denying every powerful feature, `COOP`/`CORP: same-origin` |

**`bin/drive` reads the browser's own console back and fails on any policy
violation.** That matters more than it sounds: a wrong digest refuses the page's
own script, the browser says so in the console and nowhere else, and the page
still answers 200 — which is the exact shape of every user-visible defect this
project has shipped.

**Deviation:** `style-src-attr 'unsafe-inline'` remains. The charts carry
per-element geometry as style attributes and an attribute cannot be hashed
individually. L2 tolerates this; it is recorded because the loose allowance used
to cover every `<style>` block as well, and now covers attributes alone.

### V4 · API and Web Service — **gated**

One choke point. `access.may()` runs before a handler reads a byte, deny by
default: **a route missing from `access.NEEDS` requires `admin`**, so a new
endpoint is shut until somebody opens it. That inventory is compared against
Caddy's `@private` block **in both directions** — every private route is gated
at the proxy, and the two deliberately public routes are not accidentally gated,
because a one-way check cannot see over-gating and over-gating took the public
volume calculator off the air once.

`do_GET`'s routes are inventoried too. They were not, and a read route added to
`do_GET` and nothing else was live and unauthenticated with every gate green.

Two public POSTs by design — `/api/pool-shape/compute` and `/api/photo` — both
pure, both rate-limited. `/api/pool-shape` with no suffix saves and stays gated;
the Caddy matcher carries no trailing wildcard for exactly that reason.

### V5 · File Handling — **gated**

One upload surface, `/api/photo`, anonymous by design, and it is the most
exposed thing in the image.

**Bytes are not pixels** is the control that matters. The endpoint was bounded
at 60 MB on the wire and not at all on the decode: a PNG of 225 million white
pixels is 246 KB compressed and expands to ~675 MB of RGB in a container with
`mem_limit 512m`. One request, one OOM kill, from anyone on the internet —
and Pillow's own bomb check only *warns* below 179 Mpx. `pool_shape.MAX_PIXELS`
refuses it **from the file header**, before any decoder runs, using a size
parser that needs no imaging library; `MAX_IMAGE_PIXELS` is the backstop for
formats it cannot read.

A rate limiter counts arrivals and bounds no work in flight, so `_PHOTO_SLOTS`
caps concurrent decodes at two and answers 503 beyond that — backpressure
rather than a dead container. The returned image is stripped of metadata
deliberately; carrying EXIF across would put a GPS fix in a public response.

Nothing is stored. There is no upload directory and no filename from a caller
ever reaches a path.

### V6 · Authentication — **delegated**, deployment-side

Microsoft Entra via oauth2-proxy. poolhound never sees a credential, so the
password, MFA and recovery requirements in this chapter are Entra's and are met
or not met by the tenant's own policy.

**What is in this codebase** is the agent's bearer token, and that is the honest
gap: machine authentication, static, with nothing binding the token to the
device. Since 2026-10-07 it can be **rotated without an outage** — the server
accepts a second token for a stated window, the window is mandatory, and
`vault.py` refuses a slot with no deadline, an unreadable deadline, a passed
deadline, or a value identical to the current token. The runbook is in
[AGENT-PROTOCOL.md](AGENT-PROTOCOL.md).

**gap (accepted):** the token in steady state has no expiry. ASVS L2 would ask
for one. For a single device on one LAN this is a documented trade; the fix is
client credentials with an expiry, and it is not worth it for one device.

### V7 · Session Management — **delegated**

oauth2-proxy owns the session cookie, its lifetime, its flags and its
invalidation. poolhound issues no session of its own.

The one token poolhound mints is **not a session token** despite living at
`/api/health`: it is a per-process CSRF token, it conveys no identity, and it is
withheld from anyone the proxy did not vouch for. That withholding was once a
real bug — `who != "local"` meant "signed in", and the moment `whoami()` gained
a second not-a-person answer, every anonymous visitor was issued one. The
predicate is now `signed_in()` in one place and the answer is shipped to the
page rather than re-derived there.

### V8 · Authorization — **gated**

`access.py`, three roles, one choke point, deny by default.

**Roles are opt-in**, which is a deliberate deviation: an install with no
`[access]` section keeps the old behaviour where everyone the proxy admits is an
admin, because default-deny on upgrade would lock owners out of their own pool.
Once any name is listed the policy is live and an unlisted person drops to
`view`. ASVS would prefer deny-by-default unconditionally; the mitigation is
that `/api/health` reports which state the install is in, so the gap is visible
rather than assumed closed.

**The server refuses; the page only dims what it cannot use.** That is the same
boundary as the two builds — client-side hiding is not a boundary — and the
role selectors are inventoried against real elements, because a selector that
matches nothing satisfies every check written about it and leaves both Save
buttons live for a reader.

The policy is not editable through the UI, on purpose.

### V9 · Self-contained Tokens — **n/a**

No JWTs are issued, parsed or validated by this application. The agent token is
an opaque 32-byte random string compared with `hmac.compare_digest`. Entra's
tokens are terminated at oauth2-proxy and never reach poolhound.

### V10 · OAuth and OIDC — **delegated**, deployment-side

The flow is oauth2-proxy's and Entra's. This repository configures the proxy and
asserts which paths it must cover; it implements none of the protocol. The
assertions that *are* here — the `@private` route list compared against
`access.NEEDS` in both directions, and the eight identity-header deletions
required to appear **before** the oauth2-proxy hop — are the parts that fail
open if they drift.

### V11 · Cryptography — **read**

| use | what |
|---|---|
| credential store on a workstation | AES-256-GCM, `cryptography`, key from the OS keychain |
| credentials on the server | Azure Key Vault via the VM's managed identity — no application credential on the host |
| agent token | `secrets.token_urlsafe(32)` |
| CSRF token | `secrets` |
| every secret comparison | `hmac.compare_digest` — the proxy secret, the agent token, the session token |
| fingerprints for comparing two machines | SHA-256, truncated, never the secret |

`cryptography` is pinned, and the pinning is the point: it encrypts the
credential vault and it was a transitive of a transitive, whatever PyPI served
that morning.

The share's 0600 mount key is the one application credential on the host, and
is named as the exception it is.

### V12 · Secure Communication — **gated**

TLS terminates at Caddy, which is also where HSTS is sent
(`max-age=31536000; includeSubDomains`) — the one header the vhost owns alone,
because sent from poolhound it would reach a loopback workstation over `http://`
and tell the browser to stop using the URL the tool is served at.

**gap (a decision, not an oversight):** HSTS carries no `preload`. Preloading is
a one-way commitment for the whole domain and every subdomain under it, and the
domain serves other things. It is left for the owner to decide rather than
defaulted on.

Outbound: two labs and one AI provider, all HTTPS, all parsed defensively.
`POOLHOUND_BIND` accepts loopback or `0.0.0.0` and nothing else, and `0.0.0.0`
is safe **only** because the container `expose`s its port and never publishes
it — two facts that have to be read together.

### V13 · Configuration — **gated**

| control | state |
|---|---|
| dependencies pinned, transitives included | nine exact versions in `deploy/requirements.txt` |
| vulnerability scan | `osv-scanner`, in CI on every push **and daily on a schedule**, plus a deploy gate that refuses a dirty scan |
| secret scanning | `gitleaks` over the working tree and over all 234 commits |
| dependency updates | `.github/dependabot.yml`, grouped |
| CI supply chain | first-party actions pinned to commit SHAs, not tags; `persist-credentials: false`; read-only token; `pull_request`, never `pull_request_target`; no `${{ }}` in either workflow |
| secrets in the repository | none, and `bin/selftest` refuses a tracked file naming the deployment's host or any hostname outside a curated roster |
| error messages | see the deviation below |

The schedule is load-bearing. A CVE is published without anybody touching this
repository, and the scan used to run only at deploy time — so an advisory filed
today stayed invisible until the next deploy, on a project that has gone four
days without a successful image build while every probe read green.

### V14 · Data Protection — **read**, with one gated part

**What leaves this system is a fixed list.** `assistant.context()` builds a
closed set of fields — volume, current readings with source and age, targets,
recent doses and their cadence — and never iterates over rows, because "send a
summary" is the instruction that quietly grows into the house address. The model
is handed figures already computed. Any OpenAI-compatible endpoint, so a model
on your own network is a `base_url` change and nothing leaves at all.

**The public build does not contain what it does not show.** The private panels
are excised at build time, not hidden, and the build asserts no house address,
token path or control endpoint survived — a denylist that *stops the build* on
an unclassified tab rather than guessing.

**Credentials are read, never printed**, and that is now enforced where it used
to be a habit: `runlog`'s `by` column records a `str` as a name, `None` as
absent and anything else as its **type**, because every nightly backup once
wrote forty characters of the config dict into an append-only CSV that is shown
on the Collection tab and copied into every tarball.

`audit.csv` holds identities and is on the share, mounted 0660. There is no
retention policy on it.

**gap:** no stated retention or deletion policy for `audit.csv`, which holds
email addresses. L2 asks for one. For a two-person household this is small, and
it is unanswered rather than answered.

### V15 · Secure Coding and Architecture — **gated**

This is the chapter the project's own gates were already built for, and they go
further than L2 asks.

- **One fact, one owner** (`seams.py`): a declared fact, the module that owns
  it, and a check that the pattern appears nowhere else. About a third of every
  defect found in one week of review was a second copy of a fact that had
  drifted.
- **Every inventory is compared with what it inventories** (`inventory.py`): a
  list used as "every X" is a promise, and ten defects were promises maintained
  by hand.
- **Every detector proves it fires** (`checks_structure.py`): each check
  declares an input that must break it, and a meta-check asserts it does. At
  least nine checks in this project have passed while measuring nothing.
- **The page is driven, not read** (`bin/drive`): both builds in a real browser
  at 390px, light and dark.

**Backups** belong here as much as anywhere: a nightly dated tarball taken under
the writers' own per-file locks, off the share, 30 days kept, RPO 24h, RTO
minutes — and a tarball rather than a sync, because a sync mirrors a corruption
as eagerly as a correction, which is the failure mode this is for.

**Off-box, as of 2026-10-07:** `deploy/pull-backup.sh` pulls the newest server
tarball to a workstation on a daily LaunchAgent, verifies it opens *and* holds
`samples.csv` — a tarball that opens is not the same claim as a tarball with a
pool in it — and sets 0700/0600 on what it writes. Losing the VM no longer
loses the history.

**gap:** the server-side push, `POOLHOUND_BACKUP_OFFBOX`, is implemented,
refuses a destination on the same filesystem by `st_dev`, and **is not set**.
The copy that exists depends on a laptop being awake at 04:10 and on the Azure
CLI refreshing its own token unattended, and nothing outside that machine
notices when it stops.

### V16 · Security Logging and Error Handling — **read**, with one deviation

`audit.csv` is append-only and records who did what, with the values: every
dose, every control command, every settings change, joined to the command's
acknowledgement by id rather than guessed at by timestamp. `runs.csv` records
every run of every collector and how it ended.

**A skip is not a success**, which is a logging-integrity control in disguise:
lateness is measured from the last attempt that was **not** a skip, because a
40-hour outage logged 86 skips with fresh timestamps and the gap the Collection
tab measures would never have grown.

**Nothing inside this system can report its own absence**, so the status record
is written to the share **and** to the host's local disk, `--check` takes
whichever is newer, and `bin/watch` pings an external URL on every completed run
so a monitor outside the host alarms on the ping it did not get. That ping says
the watchdog ran, not that the pool is fine, and conflating the two would take
the monitor down every time the chlorine was low.

**Deviation:** a 500 on an authenticated write route returns the exception type
and message to the caller — not a traceback, which goes to the log. ASVS L2
prefers a generic message. The stated reason is that these routes are behind the
session token and a signed-in identity, the reader is the operator, and a
message they can quote is worth more than a tidy one. **This is a conscious
trade and the one place in this assessment where the standard would say no.**
Public routes do not do this.

### V17 · WebRTC — **n/a**

No WebRTC anywhere.

---

## The gaps, ranked by what they would cost

| # | Gap | Chapter | Cost if it goes wrong | Closing it |
|---|---|---|---|---|
| 1 | **The off-box copy is a laptop.** `deploy/pull-backup.sh` runs daily and the history is genuinely off the VM now; the server-side push, `POOLHOUND_BACKUP_OFFBOX`, is still unset | V15 | the copy depends on a machine being awake and on `az` refreshing a token silently, and nothing outside that machine notices when it stops | set `POOLHOUND_BACKUP_OFFBOX` to a mount that is not this host, so there is a copy that does not depend on a person's laptop. Keep the pull as the second one |
| 1b | **The pulled archive puts `config.toml` and `audit.csv` on a portable machine** — house address, credential paths, every identity | V14 | a lost or stolen laptop is now in scope in a way it was not before | the directory is 0700 and the tarballs 0600, set rather than assumed. Full-disk encryption is the control that actually carries this |
| ~~2~~ | ~~**Siblings on the docker bridge can reach `:8787`**~~ — **closed 2026-10-07**, measured 11 → 2 with 0 on the shared bridge | V13 | — | two networks in the host's compose file: `poolhound_edge` (caddy) and `poolhound_mail` (postfix, because `smtp_host = "postfix"` and a Caddy-only network would have silently killed every alert email). Neither `internal: true` — egress is load-bearing. Verified after the change: `--check` reports "off the shared bridge; 2 container(s) can reach it", and postfix:587 is still reachable from inside the container |
| 3 | **The agent token has no expiry in steady state** | V6 | a copy of it is a copy of the agent's whole authority: push fabricated samples, acknowledge commands | client credentials with an expiry. Rotation is now possible and cheap, so rotating on a calendar is the interim answer |
| 4 | **No retention policy for `audit.csv`**, which holds email addresses | V14 | nothing dramatic; an unanswered question about personal data | decide a period and prune in `bin/backup`'s job |
| 5 | **500s on authenticated routes return the exception text** | V16 | internal detail to an operator who is already an operator | leave it, knowingly — or make it role-conditional |
| 6 | **HSTS has no `preload`** | V12 | a first-visit downgrade window on a domain already serving HTTPS | submit the domain, accepting it applies to every subdomain for a long time |

Gap 2 is closed. **Gap 1 is now the only one that would actually hurt**, and it
is not a code change in this repository — it is one environment variable,
`POOLHOUND_BACKUP_OFFBOX`, pointed at a mount that is not this host.

Closing gap 2 turned up something worth recording, because it is the failure
mode this whole assessment is written against. The host's compose file is **not
in version control**, and the workstation copy of it was a stale snapshot
missing five lines the server had: `POOLHOUND_PROXY_SECRET` on both caddy and
poolhound, `POOLHOUND_BACKUP_DIR`, `POOLHOUND_BACKUP_OFFBOX`, and the
`/var/lib/poolhound/backups` bind mount. Pushing that copy — which is what the
host repository's own runbook tells you to do — would have reopened abuse case
3 and abuse case 10 in a single `rsync`, and nothing would have reported it.
The change was applied to the server's copy instead, verified purely additive
against the live file first, and the workstation copy was then refreshed from
the server.

## Re-running this

The assessment is a reading; what holds it up between readings is the gates.

```bash
bin/selftest                  # 164 cases, every checks_*.py, and the meta-check
bin/drive                     # both builds, a real browser, 390px, light and dark
bin/contrast                  # WCAG over all four palettes
osv-scanner scan source --lockfile=requirements.txt:deploy/requirements.txt
gitleaks git . --no-banner --redact
./deploy/bootstrap-server.sh --check
```

`--check` is what grades the two gaps that matter: it counts the containers
sharing poolhound's docker network, says whether an off-box recovery point
exists, and warns while an agent-token rotation is open.

`./deploy/pull-backup.sh --check` is the other half, and it runs on the
workstation rather than against the server: it grades how old the newest local
copy is and exits non-zero past 48 hours. That question cannot be asked from
the server, which is the point of the copy.

**It answers "is what is deployed working", never "is what is deployed
current".** A server three commits behind reports every line green. After any
deploy, compare explicitly: `git log --oneline <rev-from-check>..main`.
