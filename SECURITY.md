# Security policy

## Reporting a vulnerability

Please report security issues **privately** — do not open a public issue.

- GitHub: **Security → Report a vulnerability** (private vulnerability reporting), or
- Email: the address in this repository's git history.

Include what you found, how to reproduce it, and the impact you expect. Reports
are acknowledged within **3 business days** with a first assessment within
**7 days**.

## Commitments (ENGINEERING_STANDARD VULN-1…7)

| Severity | Fixed within |
|---|---|
| Critical (or actively exploited) | 7 days |
| High | 30 days |
| Medium | 90 days |
| Low | best effort, next time the surrounding code is touched |

Fixed vulnerabilities are described in the **commit that fixes them**. There is
no `CHANGELOG.md` — this repository's history is the changelog, and each commit
message states the defect, how it was found, what was measured and what now
stops it recurring. `git log --oneline` is the index.

## What this software is, and what that means for scope

poolhound measures and controls **one household's swimming pool**. It is
published so the code can be read and reused, not run as a service for anyone
else — there is no multi-tenancy, no customer data, and no hosted instance.

That makes two things true, and both are deliberate rather than oversights:

- **The repository names no deployment.** No tracked file carries the host it
  runs on, its stack directory, or any hostname outside a curated roster, and
  `bin/selftest` enforces that on every run. A finding that depends on knowing
  the author's hostname is a finding about the author's hosting, not this code.
- **The controller has no authentication of its own.** AqualinkD binds
  `0.0.0.0:8000` on the house LAN with no password. It is never exposed and
  never proxied; the Pi's agent connects *outward* only. This is stated in
  `help.AQUALINKD_BIND` and drawn in the architecture diagrams. Do not propose
  proxying it — the outward-only agent is the mitigation.

## The parts most worth your attention

| Surface | Why |
|---|---|
| `POST /api/photo`, `POST /api/pool-shape/compute` | the only two unauthenticated POSTs. They read nothing, keep nothing, and carry their own rate limit — but `/api/photo` decodes attacker-supplied image bytes |
| `poolhound/access.py` | the whole authorization model: three roles, deny-by-default, one choke point |
| the identity header | `X-MS-CLIENT-PRINCIPAL-NAME` is set by the proxy and stripped on the way in. Since the proxy also presents a shared secret **after** that strip, which `whoami()` compares with `hmac.compare_digest` before reading any identity header, trust no longer rests on docker network position — but it is opt-in, and `/api/health` reports `proxy_auth` so the install says which state it is in |
| `bin/backup` | the only copy of the history that is not on the share. It refuses a destination inside the data directory, and its freshness check never re-parses a filename it read back from the server |
| `poolhound/vault.py` | credential storage — Key Vault on a server, AES-256-GCM on a workstation |
| `/api/agent/*` | the only routes reachable from the internet without a session, authenticated by a bearer token compared with `hmac.compare_digest`. A **second** token is accepted for a stated window so the thing can be rotated without taking the pool's only sampler off the air; the window is mandatory and `vault.py` refuses a slot with no deadline, an unreadable one, a passed one, or a value equal to the current token |
| `poolhound/headers.py` | the response headers and the Content-Security-Policy. The policy names each page's one inline script by SHA-256 digest **taken from the bytes being served**, so there is no `script-src 'unsafe-inline'` and no build artefact to get out of step. `bin/drive` reads the browser's own console back to prove the digests are the ones Chromium computes |

## Supported versions

Only `main` receives fixes, and `main` is what is deployed. **There are no
releases**, which is why the table above measures Low severity against the next
time the code is touched rather than against a release that would never
arrive.

## How this is checked

`bin/selftest` (including the three write-guard exploits that once worked),
`bin/drive` in a real browser, and `bin/contrast`. All three run in CI on every
push and pull request (`.github/workflows/gates.yml`), which invokes the
documented commands rather than restating them, and fails if the browser gate
reports that it did not run.

**Dependencies and secrets are a second workflow, and it carries a schedule**
(`.github/workflows/supply-chain.yml`). `osv-scanner` runs over
`deploy/requirements.txt` — which is the shipped image, nine exact versions,
three of them transitives pinned because a transitive is re-resolved on every
build and is the thing nobody can answer for afterwards. `gitleaks` runs over
the working tree **and over every commit**, because a credential removed in a
later commit is still in the history, the way the CSVs were.

The schedule is the point of the split: a CVE is published without anybody
touching this repository, so a dependency scan that only runs on push answers a
question nobody asked that day. It used to be a deploy gate alone, which meant
an advisory filed today stayed invisible until the next deploy — and this
project has gone four days without a successful image build while every probe
read green. `.github/dependabot.yml` raises the pins so the scan has somewhere
to go.

Measured on 2026-10-07, before either was wired in: 9 packages and no
advisories; 234 commits and 12.18 MB of working tree, no findings. Both gates
start clean, which is the only state worth starting from — and worth saying
plainly, because unlike every detector in `checks_structure.py` neither of them
can be shown to go red without a real finding.

See `docs/THREAT_MODEL.md` for assets, trust boundaries and abuse cases,
and `docs/ASVS-L2.md` for this system measured against OWASP ASVS 5.0
Level 2 — chapter by chapter, with the six gaps ranked and two of them
named as the ones that would actually hurt.
