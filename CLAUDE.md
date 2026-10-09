# Working on poolhound

Measuring what actually moves this pool's chemistry, rather than following a
chart written for someone else's pool. This file is orientation; the real
documents are:

- **[README.md](README.md)** — how it is built, the architecture, and why each
  decision went the way it did.
- **[THEORY.md](THEORY.md)** — the chemistry, why it is fittable, and what each
  coefficient buys you.
- **[SITE.md](SITE.md)** — how the website is put together: the two builds, the
  tab model, the render pipeline, and how to add a tab without breaking the
  boundary between them.
- **[deploy/README.md](deploy/README.md)** — the server: the runbook, and how to put
  it back from nothing.
- **[docs/THREAT_MODEL.md](docs/THREAT_MODEL.md)** — assets, actors, trust
  boundaries and abuse cases, each with the control that mitigates it and an
  honest status. **[docs/ASVS-L2.md](docs/ASVS-L2.md)** asks the opposite
  question: whether OWASP ASVS 5.0 Level 2 would recognise the controls,
  chapter by chapter, each verdict marked *gated* / *driven* / *read* by the
  evidence behind it, with six gaps ranked. **[SECURITY.md](SECURITY.md)** is
  the reporting policy and the fix-time commitments.
- **[docs/AGENT-PROTOCOL.md](docs/AGENT-PROTOCOL.md)** — why the Pi connects
  outward, the two channels, delivery semantics, and how the agent is kept
  alive.

## Where it runs

Since 2026-09-12 the system of record is **the server**, not a workstation.

**Nothing operational runs on a workstation, and the code now enforces it.**
`bin/wg-collect`, `bin/leslies`, `bin/watch`, `bin/chem` and `bin/backup` call
`config.refuse_off_deployment()` before they do anything and exit with an
explanation when `POOLHOUND_DATA` is unset — which is the marker the image sets
and a checkout does not, and which `save_settings` already treats as the
difference between a durable deployment and a scratch directory. The override
is `POOLHOUND_COLLECT_ANYWAY=1`, and developing a parser does not need it:
`--dry-run` and `--from-file` are the documented route.

The
readings live on an Azure Files share mounted only on the server, so anything that
collects or writes from a laptop appends to a second pool history that nothing
serves and nothing reconciles. The controller poller (`bin/sample`, and the
polling half of `aqualink.py`) was deleted outright for that reason rather than
left unused, and with it the Refresh menu's "Pool controller" button and the
`[aqualink]` host setting that only it read. A workstation runs the build and
the gates — `bin/render`, `bin/selftest`, `bin/drive`, `bin/contrast`,
`bin/screenshots` — and **one operational job, which is a PULL and not a
collect**: `deploy/pull-backup.sh` brings the newest server tarball off the VM
on a daily LaunchAgent, because a workstation is off-box and the VM's local
disk is not. It writes no CSV, touches no data directory, and is read-only on
the server — which is the whole of the difference from the thing
`refuse_off_deployment()` exists to stop, a pull that APPENDS to a second pool
history nothing serves. Running `bin/backup` here instead would be that defect
wearing a backup's clothes: this checkout's `data/` holds `runs.csv` and
nothing else, so a nightly job over it would write a dated tarball every night
containing none of the pool. `pull-backup.sh` refuses a tarball with no
`samples.csv` in it for exactly that reason.

| Piece | Where | What it does |
|---|---|---|
| the site and the API | the server, in Docker behind Caddy | serves both builds, holds the command queue |
| the readings | **Azure Files share**, mounted `/mnt/poolhound` | the CSVs; not on the server's disk |
| the passwords | **Azure Key Vault**, read by the VM's managed identity | no *application* credential on the server; the share's 0600 mount key is the one exception |
| the controller sampler | **the Pi**, `poolhound-agent.service` | reads the panel, pushes OUT to the server |
| **poolhound local**, optional | **the Pi**, behind its own web server | a page on the house network that drives AqualinkD directly; installed by nothing here, copy in `deploy/poolhound-local.html`. The **poolhound server** is the first row |
| the other collectors | the server, `/etc/cron.d/poolhound` | WaterGuru, Leslie's, the watchdog |
| the recovery point | the server's **local disk**, `/var/lib/poolhound/backups` | `bin/backup` at 02:40 UTC: a dated tarball of the history, deliberately NOT on the share |
| the copy that survives the VM | **a workstation**, `~/Backups/poolhound` (0700) | `deploy/pull-backup.sh` on a 04:10 LaunchAgent: pulls the newest tarball, verifies it opens AND holds `samples.csv`, 30 days kept |
| the gates | GitHub Actions, `.github/workflows/gates.yml` | on every push and PR, so they no longer run only where somebody remembered |

The server is disposable and the data is not. `deploy/bootstrap-server.sh` rebuilds the
host from nothing and deliberately **cannot** recreate the share or the vault — a
script that could recreate the secrets would be a script that contains them.

## What to run

```bash
bin/render           # regenerate BOTH builds: site/index.html and site/admin.html
bin/selftest         # the pure functions; non-zero on a failure
bin/drive            # both builds in a real browser at phone width
bin/contrast         # WCAG on every palette
bin/screenshots      # retake docs/screenshots and the landing page's cards
bin/brand            # rewrite docs/poolhound*.svg from brand.py

# These five REFUSE on a workstation, and the server is where they run:
bin/watch            # has a collector gone quiet? notify if so
bin/wg-collect       # pull WaterGuru (spends one of the two calls a day we respect)
bin/leslies          # pull Leslie's; returns the whole history, safe to re-run
bin/chem --help      # log a dose, and see the whole chemical catalogue
bin/backup           # copy the history OFF the share; 02:40 UTC in cron, 30 days kept

bin/serve            # the container's entry point; not something to run here
bin/agent            # the Pi side; do not run it on a workstation

# The off-box copy. A PULL, so it runs HERE and not on the server, and it is
# the one operational job a workstation does run:
deploy/pull-backup.sh          # fetch the newest tarball off the VM, verify, prune
deploy/pull-backup.sh --check  # is there still a current copy on this machine?

# on the server
ssh -F /tmp/poolhound-ssh.config "$SERVER_IP"        # az ssh config mints the cert
sudo $STACK/poolhound/collect.sh render
./deploy/bootstrap-server.sh --check             # is any of it actually working
```

## Two builds, not one page with things hidden

`bin/render` writes **`index.html`** (public) and **`admin.html`** (authenticated).
The private panels are not hidden in the public build, they are **not in the
file**, along with the script that drives them, and the build asserts that no
house address, token path or control endpoint survived into it.

Client-side hiding is not a boundary: "the tab is not shown" is one view-source
away from "here is the internal address of the pool controller". Splitting at
build time means a misconfigured proxy rule fails by showing too little.

Public: **Poolhound info** (the landing page), Pool Volume Calculator, Pool
chemistry, Guide, Safety & privacy. Authenticated as well: Pool control, Home,
Chemicals, Ask AI, Collection, Settings. Eleven tabs; `SITE.md` is the full
account of how they are classified and ordered. The Guide tab is still `help`
in code — its id, `help.py`, `#help` links — and the notes below that say
"Help" are about that tab, written before it was renamed.

**The tab tuples in `render.py` are the whole of it.** `PUBLIC_TABS` is both the
public classification and the public nav order; `PRIVATE_TABS` is what the
public build cuts out; `TAB_ORDER` is the authenticated nav. Each build's
opening tab is WRITTEN by `set_default_tab()` — one selected button, one visible
panel, asserted — because the template can carry only one default and the two
builds want different ones. The page script reads that choice back out of the
markup rather than naming a tab of its own; it used to name one, which reopened
Home over the controls on one build and left the other with every panel hidden
and nothing selected.

Home is private on purpose: it is a dashboard, and the argument for publishing
the chemistry reference — that it is useful to somebody else's pool — does not
reach this household's current chlorine. The masthead's freshness line and
overall verdict are gated with it.

**The landing page's screenshots are a build INPUT.** `bin/screenshots` drives a
browser over a synthetic pool from `bin/demo-data`, writes full captures to
`docs/screenshots/` for README and cropped cards to `docs/screenshots/cards/`
for the page. The container that renders in production has no browser, so the
cards are committed and `COPY`d into the image; `bin/render` copies them into
the site directory. They are pictures of a pool that does not exist, and the
captions say so.

## Things that will bite you

**The page template is not a format string, and it is not raw either.** It
carries the page's JavaScript and a JSON catalogue, both full of braces, so
placeholders are `@@name@@` and `render.fill()` raises on an unfilled one. And
because `TEMPLATE` is an ordinary `"""` string, **`\n` inside a JavaScript string
literal becomes a real newline when Python parses this file** — it must be
written `\\n`. That has broken the whole page script three times. `\s` and `\d`
in a regex literal need the same treatment.

**A render can happen in a different process from the one serving.** Cron's
render and `docker compose exec … render` each get their own empty in-memory
queue. Anything live — whether an agent is connected, what is pending, the
command history, the current setpoints — must be fetched by the browser from
`/api/health` or `/api/commands`, never baked into the render. Baking it
produced a page that said "no agent is connected" while serving from a process
that had one; the rule was then applied to that banner and NOT to the command
table beside it, so cron's six-hourly render erased the history twice a day and
asserted a restart that had not happened.

**"Is it running" and "what is it set to" are different questions.** The page
compared a commanded setpoint of `88` against the heater's on/off state `on`,
never matched, and showed "waiting" for twenty minutes while the panel had
agreed within a second — with the amber pending badge REPLACING the on/off one,
so the card stopped answering whether the heater was running. Switch marks and
setpoint marks are kept apart, and the setpoint has its own badge.

**A setpoint range that refuses the panel's own reading is wrong by
construction.** Freeze protection was declared 55–65; the panel reports 34.
`bin/selftest` now checks every `SETPOINTS` range against the values
`samples.csv` actually holds, because the panel is the authority and it says so
in every sample.

**Pending means the panel does not yet agree.** Not "no sample has arrived
since": the agent pushes a confirming reading about a second after every command,
so that definition cleared the waiting mark whether or not anything changed. And
only the NEWEST command per device counts — walking the whole log resurrected an
instruction that had already been obeyed and reversed, which made an unrelated
command look like it had moved a circuit.

**Every writer of a shared CSV calls `locking.append_row`, never a bare
append.** The header is written once, at creation, so adding a column to a COLS
list corrupts every row appended afterwards — and three writers got this wrong
at once: the controller sampler put 19 fields under a 15-field header, and `server.py` and
`chem.py` each declared their own `chemicals.csv` shape and disagreed about
`by`. All three exited 0 and printed their usual success line. Lock, migrate,
append is now one call in `locking.py`, which is the module every writer can
reach — `migrate_columns` lived in `server.py`, which `aqualink.py` cannot
import, and that is exactly how `aqualink.py` came to be the exception. A column
list lives in ONE place: `aqualink.COLS`, `chemicals.CHEM_COLS`.

`migrate_columns` refuses two things and backs up before the third. A column on
disk that the code no longer knows is data loss with a tidy name. A row LONGER
than its header is a file some writer already corrupted, and those overflow
fields are recorded readings — it stops and asks for a person rather than
dropping them while reporting how many columns it ADDED. And it copies the file
to a timestamped `.bak` first: this is the only operation that rewrites all of
an irreplaceable history.

**The day still in progress is not a day.** `trajectory()` scales production by
recent-runtime over average-runtime and took "recent" as the last day in its
window — today, part-run. Cron renders every six hours, so most renders land
mid-afternoon: 4.75 h counted against a 7.29 h average gave a ratio of 0.65 and
an adjusted equilibrium of 2.86 ppm, *under* the 2.9 target, while the same pool
at its last complete day (6.50 h) was settling towards 3.66. The Power card
offered to give back runtime that was never spare, and a salt cell that was
over-producing read as correct. Prorating the partial day by elapsed clock is
worse, not better — the pump runs in a block, so hours seen by 14:10 scale up to
more than the day will hold. The data says which days are finished; the partial
one is dropped from BOTH statistics and rejoins tomorrow, whole.

**A plausibility ceiling is not a unit, and `-999` is not the only way a
sensor lies.** Two bugs in one reading. `num()`'s default `hi=500` is a
TEMPERATURE range, and it was left on `salt_ppm`, where it discards every value
a salt pool can produce — this pool runs 3400, so the old poller stored a blank
for salt on every sample it ever took and the column read as a panel that does
not report salinity. The identical bug was already documented on `num()` itself
and fixed two lines above for `Pump_RPM`/`Pump_Watts`. Salt only entered the
data at all when the Pi's agent, which applies no ceiling, took over the
sampling. And separately: `SWG/PPM` does NOT answer `-999` without flow the way
the water probes do — it latches, repeating its last measured figure for as
long as the pump is off, so 131 of the first 189 stored readings were one
measurement counted 131 times while `by_day()` averaged them all. The docs
asserted the sentinel covered "the SWG and water temperature" alike, which is
why nobody looked. `commands.drop_stale_flow_readings()` is the one rule both
collectors pass their row through; history is not rewritten.

**A config is not a history, and the guard must ask first.** `seed_config()`
refused to start a REQUIRE_DATA deployment whose data directory was empty — and
tested that by asking whether `config.toml` was missing, BELOW an
`if os.path.exists(target): return`. `config.toml` is the one file in that
directory a deploy script writes by itself: `bootstrap-server.sh` seeds it from
the host, onto the share, before the container starts. So the single condition
that disarmed the guard was the condition the deploy creates. Run bootstrap
while the CIFS mount is down and the chain is: `mount -a` exits 0 because the
fstab line says `nofail`; the deploy prints `✓ mounted` from `df -h`, which
reports the ROOT filesystem when the path is not a mount point; it seeds
`config.toml` onto the empty local directory; the container starts, returns on
the first line of `seed_config`, and the collectors begin a second pool
history. The guard now asks `has_history()` — `samples.csv`, `readings.csv`,
`leslies.csv`, `lab.csv`, `chemicals.csv`, `manual.csv` — and asks it before
anything else; the deploy asserts `mountpoint -q` and refuses to seed over a
directory that is not a mount. The detector's own "healthy" fixture was this
bug: a REQUIRE_DATA deployment holding a config and no readings.

**Nothing inside this system can report its own absence.** The share failed to
mount after a reboot, the container refused to start rather than seed a second
pool history — correctly — and `collect.sh` then skipped every job for forty
hours: 86 logged skips, `watch` among them, every thirty minutes. The
free-chlorine staleness alarm due at 07:37 UTC arrived seventeen hours late,
because the process that raises alarms was one of the casualties. Worse, the
status record could not say so: it lived only on the share, which is the thing
that was missing, so `record()` fell over quietly 86 times and what survived
said `exit 0, ok` from before it started. The record is now written to the
share AND to `/var/lib/poolhound/status`, and `--check` takes whichever is
newer — neither location survives both failures alone. And `bin/watch` pings a
push URL on every COMPLETED run, so a monitor outside the host alarms on the
ping it did not get. That ping is `status=up` whatever the run found: it says
the watchdog ran, not that the pool is fine, and conflating the two would take
the monitor down every time the chlorine was low.

**`locking.exclusive()` is not re-entrant, and now says so.** flock is held per
open file description, so taking a lock twice in one thread blocks on itself.
A function called from inside the lock wants the `_locked` variant.

**A bad reading is corrected, never deleted.** Both labs return their WHOLE
history on every pull and the collectors dedup on the measurement timestamp, so
a row removed from the CSV is put back by the next cron run — a fix that appears
to work and undoes itself overnight. `corrections.csv` is an append-only list of
drops and field overrides applied by the loaders in `render.build()`, once, at
the point everything else reads from. A logged DOSE is different and is edited in
place: that row is our record of something we did, and nothing will put a wrong
amount back.

**Never truncate without saying so.** The data tables showed the last 40 rows of
394 with no way to reach the rest and nothing admitting it. A reader cannot ask
for what they cannot see is missing. `data_table()` states "showing 40 of 387"
and means it.

**`bin/wg-collect` spends a real API call.** The upstream project asks for no more
than one or two a day. Develop the parser against a saved response instead:

```bash
.venv/bin/python bin/wg-collect --from-file data/raw/wg-YYYYMMDD-HHMM.json
```

`bin/leslies --from-file` and `--dry-run` do the same job for Leslie's.

**Leslie's needs `WaterTest-Home` fetched first.** Its XHR endpoints read the
current pool profile out of the session and that page is what puts it there.
Called cold they answer **500** — not 401, not an empty result — which reads like
a server fault rather than a missing precondition. Their POST bodies are jQuery
`$.param()` output, so a nested object serialises as
`poolProfile[pool_address][city]=…`; a flat urlencode draws the same 500.

**Never add a second copy of the dose arithmetic.** `chemicals.py` is the only
place it lives; the CLI, the Pool chemistry tab and the browser all read it, and
`as_json()` exports coefficients rather than letting the browser reimplement
anything. Same rule for volumes: `pool_shape.py` computes them and the browser
sends outlines, so the figure on screen and the figure that gets saved cannot
disagree.

**`render.py` owns the loaders.** `rows`, `num`, `when`, `ago`, `targets`,
`best_lab`, `verdict`. Other panels import them. Two tabs must not be able to
disagree about what the current alkalinity is.

**`commands.py` is the shared vocabulary.** the server and the agent both validate
against it so they cannot drift. `Extra_Aux` is absent on purpose — it is the
solar valve, actuated by the panel's own logic. Help enumerates BOTH `SWITCHES`
and `SETPOINTS` and says the list is complete, so it has to be: a switch that
`help._PANEL_NAME` does not name now raises at render rather than being filtered
out. Silently dropping the unrecognised key is how salt-cell boost, and then the
whole setpoints half of the Control tab, came to be missing from the one page
that claims to list every control.

**`site.host` is the public web address; `site.pi_host` is the Pi.** One key
used to be both, and the two readers meant opposite things by it: `watch.py`
prefixes `https://` and appends `#home` to build the link in every alert email,
while `help.py` prints it after `ssh pi@`. Set it right for one and it is wrong
for the other, silently. `pi_host` falls back to `host`, so no install breaks —
but a new consumer must decide which of the two it means.

**Help says what the deployment does, not what would be tidier.** AqualinkD
binds `0.0.0.0:8000` with no authentication, and Help drew it as "localhost
only" in two architecture diagrams and both of their `<desc>`s while its own
`aqualinkd.conf` table prescribed the opposite. Understating an exposure is the
worst direction for a security drawing to be wrong in. `help.AQUALINKD_BIND` and
`help.AQUALINKD_REACH` are now the single statement of it.

**A unit file is not evidence that systemd agrees with it.** Three defects in
one week, each of which read green everywhere a reader could look.
`poolhound-agent.service` ordered itself `After=aqualinkd.service`, and
aqualinkd is `After=multi-user.target` while being `WantedBy=multi-user.target`
— a cycle, which systemd breaks by DELETING a job, and it deleted ours. The
agent did not run for 71 hours and presented as `inactive (dead)`,
Result=success, NRestarts=0, no ExecStart in the journal, `is-enabled` saying
"enabled". Two lines at boot are the whole record. **Never order a unit behind a
target that wants it**, and never depend on a third party's ordering being
correct. Separately `StartLimitIntervalSec`/`StartLimitBurst` sat in
`[Service]`, where systemd ignores them and says so once per boot, so the
generous window the comment argued for was never in force. Both are checked now
for every `deploy/*.service`.

**Three mechanisms, three different questions, and all three are needed.**
`Restart=always` answers *it died*. `poolhound-agent-ensure.timer` answers *it
is not running* — the larger question, and the one that covers a start job
systemd deleted or a service stopped by hand. `WatchdogSec` answers *it is
running and not working*: after a reboot with DNS down the agent sat `active`
for eight minutes logging "stream lost; retrying" and reaching nothing, while
`is-active` said yes and was right. **What the ping MEANS is the whole design** —
a ping on a timer proves a thread is scheduled, which was never in doubt, so the
agent pings only while it has had contact with the server inside
`LINK_STALE_S` (600s) and withholds it otherwise. Contact includes a heartbeat,
because an idle pool is the normal case. The ensure timer is quiet when the
answer is yes and `LogLevelMax=warning` keeps ~600 journal records a day off a
card that has already killed two boards. To stop the agent on purpose you must
stop the timer too.

**A watchdog has to be verified against real systemd, and twice it was not.**
`WatchdogSec=120` shipped with `RestrictAddressFamilies=AF_INET AF_INET6`;
`sd_notify` opens an AF_UNIX datagram socket, `socket()` raised
EAFNOSUPPORT, the agent's deliberately non-fatal notify handler swallowed it
exactly as designed, and systemd killed the agent every two minutes forever.
Two correct behaviours composing into a restart loop with nothing in the journal
admitting it. `NotifyAccess=main` is load-bearing for the same reason: its
default is `none` for every Type but `notify`, so the ping would be received
and discarded. The pair is now a check — a unit that sets `WatchdogSec` must
allow the socket the ping needs — but **the proof is
`systemctl show poolhound-agent -p WatchdogTimestamp` actually ADVANCING**. A
unit file cannot show this and neither can a selftest: that timestamp only
moves when systemd has received a datagram.

**A comment inside an unquoted heredoc is code.** `<<EOF` expands; `<<'EOF'`
does not — two characters, on a line the reader has scrolled past by the time
they reach the body. This project's ordinary style puts backticks around the
things a comment names, so a comment written inside `bash -s <<EOF` ran
`nofail`, `mount -a`, `set -e` and `df -h` on the developer's own Mac; on a
Linux workstation the same heredoc would have appended a CIFS entry to their
fstab and mounted the household's share onto their laptop. Six instances were
fixed by reading the file, and a seventh — a Python docstring 560 lines down
holding `KEY: value` and `section:` — was found only by a real deploy printing
`KEY:: command not found`. An audit that misses one will miss the next, and the
substitution worth worrying about is the kind that succeeds quietly: one of the
six held only redirections, produced nothing and failed silently. It is a check
now over every `deploy/*.sh`.

**A collector's own words are not markup.** `/api/refresh` returns the LAST LINE
a collector printed, which is routinely the vendor's own error body, and
`server.py` falls back to stderr — exactly where a vendor error lands. All of it
went into `msg.innerHTML` by string concatenation on **admin.html**, the build
that holds the pool controls; a collector message of
`<img src=x onerror=…>` fired the handler. Fixed with `textContent` and
`createTextNode` rather than an escaping helper, because there is no call left
to forget, and the invariant is the WHOLE block rather than the two lines that
interpolated that day.

**A skip is not a success, and a wall of skips is not freshness.** Three
reassuring defects in one tab. A source that had NEVER RUN was counted by
nothing, because the attention set was written out in both the banner and the
badge and both copies had the same hole — two copies that agree look exactly
like one copy that is right. A `skipped` run wore the green "working" pill. And
lateness was measured from the most recent ATTEMPT, while a skip writes a row
with a fresh `at` and collects nothing: the forty-hour outage logged 86 skips
and the gap this tab measures would never have grown. Lateness is measured from
the last attempt that was NOT a skip; `collection.NEEDS_ATTENTION` is the one
copy of the set; and a skip keeps its own word and the amber pill without being
graded broken, because the ordinary skip means the previous run is still going.

**`runlog.Run` takes `by` and `cfg` by keyword only.** `bin/backup` called
`Run("backup", cfg)` — `by` is the second positional — so every nightly backup
wrote `{'waterguru': {'credentials': ''}, 'path` into the `by` column of an
append-only CSV that is shown on the Collection tab and copied into every
tarball. No secret escaped only because `[:40]` happened to stop before a value,
and nothing about `str()` promises that. "Credentials are read, never printed"
cannot be held by a column that stringifies whatever it is handed:
`_safe_by()` records a `str` as a name, `None` as absent and anything else as
its TYPE — `invalid:dict`.

**A case whose answer depends on the host's timezone is a case CI cannot be
given.** The first CI run this repository ever had went red on a commit that
touched documentation, and the four failures were real: `best_lab` picked
WaterGuru's 95 ppm alkalinity where a Pacific workstation picked Leslie's 73 —
the precise stale-lab pair this file already has a habit written about. **The
product was correct in both places.** Two of its answers are local-time answers
and cannot be anything else: `best_lab()` weighs a WaterGuru UTC instant
against a bare Leslie's local date, where `02:00Z` is the previous evening here,
and `edit_chemical()` stores the canonical dose timestamp as
`when().astimezone().strftime(...)`, so the single spelling every dose is
indexed by is the HOST's offset. The container pins `TZ` for exactly that
reason; the test suite did not, and read the host's zone as if it were the
pool's. That is the same shape as a gate that only passes on the machine which
already has the untracked files.

`config.DEPLOY_TZ` is now the one place the zone is named, `selftest.in_deploy_tz()`
is how a case states the offset it is asserting about, and
`checks_gates.t_the_deployment_pins_the_pools_timezone()` holds
`deploy/Dockerfile` and `compose.poolhound.yml` to it in both directions —
unset leaves the container on UTC, and set to something else is worse, because
it is confidently wrong. **That detector found a second copy on its first run**:
Help had the zone typed into a paragraph. The suite is now green from UTC-12 to
UTC+14, which is the only way to know a case is testing the code rather than the
machine — and five of the six fixtures it found were pairing a `-0700` stamp
with a bare local day, one of them stamping `-0700` onto whatever `now()`
returned.

**A filename out of the data directory is not a shell token.** The backup
freshness check read the newest tarball's name off the server and interpolated
it into `sudo sh -c "… stat -c %Y '$bk' …"`. That directory is bind-mounted
read-write into the container, so a compromised container could create
`poolhound-$(whatever).tgz` and have it run as root on the host the next time
anybody ran `--check` — container to host, through the check added to prove the
backups were real. The name now stays in a shell variable, is only ever used
inside double quotes, is guarded with `--`, and the only thing that comes back
is an integer matched against `^[0-9]+$`.

## Constraints that are not preferences

**This repository is publishable, and `bin/selftest` enforces it.** No tracked
file names the host this is deployed on, the stack directory it lives in, or
any hostname outside a curated roster — documentation domains (RFC 2606
reserves the whole `example.com` subtree), the vendors Help links, and the
services the product actually calls. The deployment's own facts live in
`deploy/server.env`, which is gitignored, and reach the tree only as
`@@PLACEHOLDERS@@` that `bootstrap-server.sh` fills in when it installs the
vhost and the crontab; a placeholder that survives installation stops the
deploy rather than shipping a Caddyfile that will not adapt.

That property decays by ordinary editing — somebody pastes a real URL into a
comment while debugging — which is why it is a check and not a paragraph. It
had already decayed once in the worst possible place: the vhost was NAMED for
the domain it served, so the one fact that had to change to publish this was
written into a filename, and three call sites spelled it out to find the file.
`config.caddy_vhost()` globs for it now. Adding an outbound link means adding
its host to that roster, which is the point: a new third party the product
talks to should be visible in a diff.

**`data/`, `site/` and `config/config.toml` are never committed.** They are one
household's pool history, addresses and credentials paths, and this repository is
meant to be opened. The CSVs were tracked once; removing them was deliberate.

**Credentials are read, never printed.** Key Vault on the server, the encrypted vault
or mode-600 files on a workstation. Nothing may echo their contents into a log, a
page or a commit message.

**The controller has no authentication of its own.** Anything that can reach
AqualinkD on the house LAN can start the spa heater. So it is never exposed,
never proxied, and the server has no route to it. The Pi's agent connects **outward**
and waits. Do not "just proxy it".

**Authentication says who; `access.py` says what they may do.** Roles are
opt-in: an install with no `[access]` section keeps the old behaviour, where
everyone the proxy admits is an admin, because default-deny would lock owners
out on upgrade. Once any name is listed the policy is live and an unlisted
person drops to `view`. A route missing from `access.NEEDS` requires `admin`, so
a new endpoint is shut until somebody opens it. The server refuses; the page
only dims what it cannot use, exactly as client-side hiding is not the boundary
between the two builds. The policy is not editable through the UI on purpose.

**Three checks stand in front of every write on a workstation** and all are
load-bearing: **Host** must be a loopback name (DNS rebinding), **Origin** when
present must be ours (CSRF), and a per-process **token** from `/api/health` must
be supplied. Do not relax any, and do not add `Access-Control-Allow-Origin` —
withholding it is what makes the token secret. This was demonstrated, not
assumed: a POST with a foreign Origin wrote a fabricated dose before it was fixed.

**`POOLHOUND_BIND` accepts loopback or `0.0.0.0` and nothing else.** 0.0.0.0 is
safe ONLY because the container `expose`s its port and never publishes it. Those
two facts have to be read together; publishing the port would put an
unauthenticated control API on the internet, since Entra is enforced by Caddy.

**The assistant sends this pool's readings to a third party, and nothing
else.** `assistant.context()` builds a FIXED set of fields — volume, current
readings with source and age, targets, recent doses and their cadence. It does
not iterate over rows, because "send a summary" is the instruction that quietly
grows into the house address. The model is handed figures already computed and
told not to recompute them: the dose arithmetic lives in `chemicals.py` and a
model that multiplies gallons by a coefficient is wrong at the same confidence
as when it is right. Any OpenAI-compatible endpoint, so a model on your own
network is a `base_url` change and nothing leaves at all. The key is a vault
service like every other credential and is never a setting. `/api/ask` needs
`operate` — it spends money — and the tab is private, because the answer is
about this household.

**Two routes are public POSTs**: `/api/pool-shape/compute` and `/api/photo`. Both
are pure — they read nothing, write nothing and keep nothing — and they carry
their own rate limit. `/api/pool-shape` with no suffix SAVES and stays gated; the
Caddy matcher has no trailing wildcard for exactly that reason.

**On the server, `$POOLHOUND_DATA/config.toml` is the only durable config.**
Anything else is inside the container and a `--force-recreate` deletes it. The
first-run message names that path, `bootstrap-server.sh` seeds it, and
`save_settings` refuses to write anywhere else when `POOLHOUND_DATA` is set —
because the alternative is a save that reports success and is gone by morning.
The encrypted vault has its own named volume for the same reason, deliberately
NOT the share, which is mounted 0660.

**An empty data directory is not a new install — it may be a lost share.**
`/mnt/poolhound` is a mount point, and a failed mount leaves an empty directory
on local disk that looks exactly like a first run. `config.seed_config()` will
happily populate it, start cleanly, and begin a SECOND pool history that
nothing serves and nothing reconciles — the failure this file has a paragraph
about, arriving through the door marked "make the first run easy".
`POOLHOUND_REQUIRE_DATA=1` says "this deployment already has readings", and an
empty `$POOLHOUND_DATA` then stops the container naming the mount rather than
seeding over it. `os.path.ismount()` cannot answer this from inside the
container: docker makes `/data` a mount point whether or not the HOST path
behind it is mounted, so it is True in both the healthy and the broken case.

MEASURED: the share failed to mount after the server's first reboot since
deployment, and poolhound crash-looped for forty hours. `/mnt` is the ephemeral
resource disk on an Azure VM; this VM's size (`Standard_D2as_v5`) has none, so
`mnt.mount` fails every boot, and `/mnt/poolhound` nested under it inherited
the failure through `RequiresMountsFor=/mnt` — silently, because `nofail`. The
stale `/mnt` line is gone from fstab and a drop-in on `mnt-poolhound.mount`
clears the dependency, so cloud-init putting it back cannot break the share
again. Nothing was lost, and the crash loop is the entire reason.

**The history has a copy that is not on the share.** Everything that cannot be
re-measured lived in exactly one place — one Azure Files share, no snapshot
policy, no backup vault, no copy job — while `bootstrap-server.sh` said,
accurately, "share CREATED EMPTY — restore the CSVs into it before starting"
and named no source to restore FROM. `locking.migrate_columns` does back a file
up before rewriting it, and that is load-bearing, but it writes the `.bak`
BESIDE the original, so the one copy the code takes is gone in precisely the
case a backup is for. `bin/backup` writes a dated tarball to the host's local
disk — the one place guaranteed to fail separately from the share — under the
same per-file locks the writers take, so a tarball cannot catch a half-written
append. **A tarball rather than a sync**, because a sync mirrors a corruption as
eagerly as a correction, which is the failure mode this is for. Thirty days,
pruned by the job, because a disk that fills is an outage. RPO 24h, RTO minutes,
and the honest limit is written in `deploy/README.md`: this does not survive
losing the VM, and closing that is pointing `POOLHOUND_BACKUP_DIR` off-box. **It
refuses a destination inside the data directory** — that configuration is the
original defect wearing the fix's clothes, and it would look like it worked.

**Identity is proven, not inferred from network position.** `poolhound`'s own
source stated the weakness and left it standing: the identity headers were
trusted because of *docker network membership* plus Caddy stripping them on the
one path from outside. Measured on this stack, that bridge carries eleven
sibling containers and neither compose file declares a `networks:` key; the
eight header deletions that make an identity trustworthy live on the VHOST,
which a sibling does not traverse. One HTTP request from any of them reached
`/api/health`, was believed, and was handed the write token and admin. Caddy now
presents a shared secret AFTER the strip block — so a client supplying it has
already had it removed — and `whoami()` compares it with `hmac.compare_digest`
BEFORE reading any identity header, falling through to the peer test otherwise,
which is what keeps a loopback workstation `local`. **Opt-in**, for the same
reason `access.py`'s policy is: an upgrade that began refusing every identity
header would lock the owner out of their own pool. So the gap is visible rather
than assumed closed — `/api/health` reports `proxy_auth`, and
`bootstrap --check` counts an unset `POOLHOUND_PROXY_SECRET` as a failure and
prints the `openssl` line to fix it. **Network isolation is DONE as of
2026-10-07**, in the host's compose file which this repository deliberately
does not carry, and `--check` grades it from here: the distinct set of
containers that can reach `:8787`, and separately whether poolhound is on the
shared bridge at all. Measured before: 11 neighbours, on the bridge. After:
**2, and off the bridge.**

**"Give poolhound and Caddy a network of their own" was the wrong fix.** This
deployment's `smtp_host` is `"postfix"` — a sibling container reached by name
over that bridge — so a poolhound alone with Caddy serves every page and
silently never delivers another staleness alarm. It needs TWO networks:
`poolhound_edge` (caddy → poolhound) and `poolhound_mail` (poolhound →
postfix), and **neither may be `internal: true`**, because this container has
to reach Key Vault, the instance metadata endpoint that authenticates it, two
lab APIs, an AI provider and the heartbeat monitor. The check counts the UNION
across networks rather than the worst single one, because a max over two
dedicated networks is 2 and would have called a mail relay "only the proxy" —
a check that gets quieter the moment somebody acts on it.

**THE HOST'S COMPOSE FILE IS NOT IN VERSION CONTROL, and the workstation copy
was stale.** It was missing five lines the server had: `POOLHOUND_PROXY_SECRET`
on both caddy and poolhound, `POOLHOUND_BACKUP_DIR`,
`POOLHOUND_BACKUP_OFFBOX`, and the `/var/lib/poolhound/backups` bind mount —
because `bootstrap-server.sh` merges the poolhound service block into the
server's copy, and nothing syncs back. The host repository's runbook says to
`rsync` the workstation copy up, which would have reopened abuse case 3 and
abuse case 10 in one command with nothing reporting it. **Edit the SERVER's
copy, verify the diff is purely additive against the live file first, then
refresh the workstation copy from the server.**

**Dependencies are pinned — including the transitive ones — and the deploy
refuses to ship a dirty scan.** Nothing here had ever scanned, and the first run
answered 5 packages, 53 advisories, 2 Critical and 31 High. The two 9.1s were
both TRANSITIVE and therefore pinned nowhere, which is how they stayed
invisible: the Dockerfile re-resolves on every build, the property that makes
"rebuild from scratch" easy and makes "what did we ship" impossible to answer.
`cryptography` was the one that mattered most and the least visible — it
encrypts the credential vault, and it was a transitive of a transitive, whatever
PyPI served that morning. `pillow` and `pillow-heif` are the packages that
decode bytes posted by an ANONYMOUS caller at `/api/photo`, so they are the most
exposed thing in the image. `osv-scanner` over `deploy/requirements.txt` is a
deploy gate, and it says so loudly when osv-scanner is not installed rather than
passing silently.

**Bytes are not pixels.** The photo upload was bounded at 60 MB on the wire and
not at all on the decode. A PNG of 225 million white pixels is 246 KB
compressed, passes that cap, and expands to ~675 MB of RGB in a container with
`mem_limit 512m` — one request, one OOM kill, from anyone on the internet, and
Pillow's own bomb check only WARNS below 179 Mpx. `pool_shape.MAX_PIXELS`
refuses it from the file HEADER, before any decoder runs, using `image_size()`
which parses without an imaging library; `MAX_IMAGE_PIXELS` is the backstop for
the formats it cannot read. And because a rate limiter counts arrivals while
bounding no work in flight, `_PHOTO_SLOTS` caps concurrent decodes at two and
answers 503 beyond that — backpressure instead of a dead container.

**Nothing writes to the Raspberry Pi's card.** It has already destroyed two. The
agent keeps its few bytes of state in `/run`, which is tmpfs — and for months
that sentence was false in the direction that mattered. `STATE_DIR` was created
at startup and nothing was ever written into it, so the Guard held `done`,
`last` and `recent` in process memory alone. `update-pi.sh` restarts the agent
on every deploy, which meant **every deploy cleared the 300s cooldown on
`Pool_Heater`** — the only thing standing between a double-press and
short-cycling a gas heater — and forgot which command ids had already run, so a
resend arriving across the restart executed a second time instead of being
refused as a replay. The interlocks live in `/run/poolhound/guard.json` now,
written on each `record()` and read back on construction. What that survives is
the PROCESS, not a reboot: `MAX_AGE_S` is 300s, so every command from before a
reboot is expired on arrival anyway, and a cooldown is a claim about the last
few minutes. Wall clock rather than monotonic, because monotonic restarts with
the process and the process restarting is the event this has to read across —
which also means a timestamp in the future is DROPPED rather than believed,
since `now - future` is negative and would read as "changed ages ago". Neither
load nor save can stop the agent: it is the pool's only sampler, and trading
that for a cooldown is the wrong way round.

**The security headers have one owner, and the policy is derived from the page
being served.** `headers.py` holds the set — names included, because
`send_header("Content-Security-Policy", …)` in `server.py` was a second
spelling of it and `seams.py` says so. The vhost's copy is held to it in both
directions by a check, since the two owners used to send different sets: the
vhost had HSTS, which only it can meaningfully send, and no policy at all, so a
Caddy-generated error page had none. Caddy's copy uses `?` — a bare
`Content-Security-Policy` there would REPLACE the per-page one with a baseline
that says `script-src 'none'`, which reads like hardening and is an outage.

**The CSP names each page's one inline script by DIGEST, not by
`'unsafe-inline'`.** Taken from the bytes being served, keyed on mtime and size
— not written at render time, because a render can happen in a different
process from the one serving. `script-src-attr 'none'` refuses an inline event
handler outright; there is not one in either build. The proof is
**`bin/drive` reading the browser's own console back**: a wrong digest refuses
the page's own script, Chromium says so in the console and nowhere else, and
the page still answers 200.

**No web font, and `default-src 'none'` is why.** There is no `font-src` at
all, so a hosted face would be refused by our own server and work everywhere
else — the exact failure mode `blob:` was added to `img-src` to avoid. The two
families are system stacks: `--sans` for anything the pool said, `--serif` for
anything a person wrote. `SITE.md` has the type scale and the surface
vocabulary.

**The agent token has two slots, and the second one must expire.** With one
accepted value there was no correct order to rotate in — whichever end you
wrote first, the other was refused until somebody reached it, and the samples
in that gap do not exist anywhere else. `vault.agent_token_previous()` refuses
a slot with no deadline, a deadline it cannot parse, one that has passed, and a
value identical to the current token: each of those is a second permanent
credential wearing a rotation's clothes. `/api/health` reports whether the Pi is
still on the outgoing one, because the step people skip is the last one.
`docs/AGENT-PROTOCOL.md` has the sequence.

**A spending ceiling a restart hands back is not a ceiling.** The assistant's
budget lives in `ask-budget.json` in the data directory and is read on the next
start. Wall clock, because monotonic restarts with the process and the process
restarting is the event this reads across — which also means a stamp from the
future is DROPPED. Unreadable state fails OPEN: at worst one extra window,
against a tab reporting a limit nobody can clear.

**There are TWO off-box mechanisms and they are not the same one.**
`POOLHOUND_BACKUP_OFFBOX` is a server-side PUSH to a mount, set in the compose
file, and it is **unset**. `deploy/pull-backup.sh` is a workstation-side PULL
on a LaunchAgent, and it **is running** — so the history does now exist
somewhere the VM cannot take with it. That is a real copy and a weaker one than
a push to immutable storage: it depends on the machine being awake at 04:10 and
on `az` refreshing its own token silently, and it puts `config.toml` and
`audit.csv` — the house address, the credential paths, every identity — onto a
portable machine. Hence 0700 on the directory and 0600 on the tarballs, which
the script sets rather than assumes.

**`POOLHOUND_BACKUP_OFFBOX` refuses a destination on the same FILESYSTEM**, by
`st_dev` rather than by name, for either the history or the local backups. A
second directory on the same disk is the original defect wearing the fix's
clothes — it reads as configured, passes every check that counts tarballs, and
dies with the host it was meant to outlive. Unset is honest and `--check` says
so; set-and-empty is a failure, because somebody believes in a copy that does
not exist.

**The series palette is validated as a set, by `bin/contrast`.** `#2a78d6` pump,
`#eb6834` spa, `#1baf7a` sheer. The checker this sentence used to refer to did
not exist, and the palette had already drifted out of compliance while the
comment said otherwise. It now measures every text-on-ground pairing in all
four palettes, simulates deuteranopia, protanopia and tritanopia to check the
series stay apart, and asserts the two dark blocks agree. Re-picking one colour
in isolation means running it. The single source is `style.SERIES`. The green
fails contrast on the light surface alone, which is legal only alongside a
non-colour fallback — hence the permanent legend, the runtimes as text, and the
table view. Do not remove those: `bin/contrast` lists those three exceptions
with their measured numbers and then CHECKS the rendered page still carries all
three fallbacks, failing if one goes missing. Blue and green also converge for
tritanopes (ΔE 12.2, against 78 and 82 for the common deficiencies), which is
the same argument for the same fallbacks.

**Equipment state has one colour vocabulary**: green on, grey off, amber waiting,
faint unknown — used by the control cards and the Home strip alike. Never colour
alone: every one ships with its word, and the pending pulse respects
`prefers-reduced-motion`.

**Never a dual-axis chart.** Two measures of different scale get two frames.

**Colour tokens are defined on bare `:root` and only redefined for dark.** The
common case is the un-stamped "system theme" state, and a colour whose only
definition sits inside a media query does not apply there.

## Two habits worth keeping

**Say what the data says, not what a chart says.** The chemistry page once
recommended lowering alkalinity toward 80 because a stale lab result read 95. The
current result read 73 — already correct for a salt pool. Prose that states a
number is prose that goes wrong; the page derives its narrative from the
measurement. Targets come from `targets()`, which knows the pool is on a salt
cell, rather than from a generic band.

**Two labs disagreeing is data, not error.** WaterGuru analyses a posted sample,
Leslie's a carried-in one. Where they differ the cause may be the labs or the pool
moving between the two dates — one dose of acid moves this pool's alkalinity
about as far as the observed gap. Never average them, and do not call either one
wrong on the page.

## The four gates, and why each exists

These are not style rules. Each one is the generalisation of a class of defect
this project actually shipped, and the counts are measured over one week of
review, not estimated.

**One fact, one owner — `poolhound/seams.py`.** About a third of every defect
found was a second copy of a fact that had drifted: five call sites each
spelling "is this a person" as `!= "local"` (an outage and a 90-minute token
leak); the dose plausibility bound written only in the edit route, so the
product accepted a dose it then refused to let anybody fix; the SMTP credential
path read under one key by the writer and another by every reader; `PUBLIC_TABS`
declared twice with the dead copy in the obvious place to edit. Every one of
those was covered by a sentence in this file saying it could not happen.

When you write `# the one place X lives`, put X in `SEAMS` instead. The comment
is the part that will still be there after it stops being true.

**And the gate itself could not see 59% of the package.** `_code_only` blanked
every triple-quoted run before looking for a duplicated fact — 59.2% of
`poolhound/` invisible, 78% of `render.py`, 99.5% of `style.py`. `render.TEMPLATE`
is a `"""` string holding the entire page script and its JSON catalogue, so
**every fact the BROWSER holds was outside the gate**, and `seams.py`'s own
docstring gives as a motivating defect a sixth copy living in that page script.
A docstring is a bare string EXPRESSION; `TEMPLATE` is a string ASSIGNED to a
name, and the parser knows the difference, so it is asked — `ast` for
docstrings, `tokenize` for comments. (`ast` reports `col_offset` in UTF-8 BYTES
and `tokenize` in CHARACTERS, which disagree on every line holding an em dash;
a wrong length is a wrong line number, which is the defect blanking exists to
avoid.) It then found three real copies, the worst being the series palette
retyped as the JS fallbacks in `TEMPLATE` — the one place `bin/contrast` cannot
measure and a screenshot cannot show. Those are `@@c_pump@@`/`@@c_spa@@`/
`@@c_sheer@@` now, filled from `style.SERIES` like the legend beside them.

**Every inventory is compared with what it inventories —
`poolhound/inventory.py`.** A list used as "every X" is a promise, and ten
defects were a promise maintained by hand: `bin/contrast` reporting "all
pairings pass" three separate times over a colour it had never measured; 23 of
45 headings silently taking the generic icon; `do_GET`'s routes never
inventoried at all, so a read route added to it and nothing else was live and
unauthenticated with every gate green. Register the pair — what exists, what
the list claims — and the check compares them in both directions.

**Every detector proves it fires — `poolhound/checks_structure.py`.** This is
the one the others needed. At least nine checks here have passed while
measuring nothing: `selftest` and `contrast` with no `__main__`, so `python -m`
imported them, ran nothing and exited 0; a sentinel regex requiring `whoami()`
adjacent to the comparison, matching none of the five sites it was written for;
a Caddy check matching literals against a config that stores wildcards; a
corruption guard sitting below an early return taken on every ordinary day. A
green line is evidence only if the same code can be shown to go red, so each
detector declares the input that must break it, and a meta-check asserts it
does.

**The page is driven, not read — `bin/drive`.** No static gate reaches this
class, and it contains the most user-visible defects of the lot: the public
site telling every anonymous visitor "signed in — view only"; invisible
screen-reader spans escaping their container and scrolling the whole page 251px
sideways at phone width; the agent-presence check running once at load, so
every control stayed live after the Pi dropped; fourteen buttons named "On" and
"Off" with nothing tying them to a device. All found by asking the browser what
it had — computed styles, accessible names, real clicks — and none by reading.
`bin/drive` loads both builds at 390px and asserts what only a browser knows.

TWO MODES, BECAUSE THEY ANSWER DIFFERENT QUESTIONS. `bin/drive` alone drives
the LOCAL build — that tests the code, since names, wiring, scroll and the
status line come from the template rather than from this pool's data. The
deploy runs it and refuses to ship a page that fails. `bin/drive --url …`
drives what is actually being SERVED, read-only, and `--check` runs it against
the live public page: a 200 says the page was served, not that it works, and
every user-visible defect this project shipped returned 200 throughout. The
public page only — /admin.html is gated and a browser has no session there.

Playwright is a WORKSTATION dependency and deliberately not in
`deploy/requirements.txt`: the runtime is stdlib plus nine pins — four for the
WaterGuru API, two so the photo tracer can read an image, and three transitives
pinned because a transitive is a thing nobody can answer for otherwise. Without
a browser `bin/drive` says so and exits 0 — loudly, because "did not run" must
be distinguishable from "passed".

AND THE LOUDNESS HAD TO BE REAL. Both `bin/drive` invocations in
`bootstrap-server.sh` sat inside `if [[ -x "$SRC/.venv/bin/python" ]]` with no
else branch, so on a machine without a venv the entire browser gate was skipped
and printed NOTHING, under a comment promising it is "skipped without a browser,
loudly" — true only for a missing Playwright. The gate that catches every
user-visible defect this project has shipped could be absent from a deploy with
`--check` going all green. Both say so now, and the `--check` one counts it as a
failure, because `--check` is what people read to decide the deployment works.
CI greps for the skip line too, since a runner with no browser would otherwise
look exactly like a pass.

NOTHING EVER DROVE THE DARK PALETTE until it did. `bin/contrast` reads the
palettes out of the stylesheet and checks them as TEXT, which cannot answer
whether the dark block APPLIES: a token defined only inside the media query, a
selector that wins over it, or a colour resolving to nothing and falling back to
black are all invisible to a reader of the CSS and to every screenshot taken in
light mode — the state the series palette actually spent several commits in.
Both builds are driven again with `color_scheme=dark`, in a fresh context so
nothing has stamped `data-theme`.

**A CVE is published without anybody touching this repository**, so the
dependency scan has a SCHEDULE and lives in its own workflow.
`.github/workflows/supply-chain.yml` runs `osv-scanner` over
`deploy/requirements.txt` and `gitleaks` over the working tree AND over every
commit — a credential removed in a later commit is still in the history, the
way the CSVs were. It used to be a deploy gate alone, which meant an advisory
filed today stayed invisible until the next deploy, on a project that has gone
four days without a successful image build while every probe read green. Both
tools are installed with `go install` at a pinned version rather than through
somebody else's action, and the first-party actions are pinned to commit SHAs,
because a tag is a mutable pointer in another repository.

**The gates run in CI now, and CI runs the DOCUMENTED COMMANDS.**
`.github/workflows/gates.yml` invokes `bin/selftest`, the `-m` form, `bin/render`,
`bin/contrast`, `bin/brand --check` and `bin/drive` — rather than restating what
checking means, which is the second copy `seams.py` exists for. Running them the
way CI would, against `git archive HEAD`, found three things true of every fresh
clone: every gate needs `config/config.toml`, which is never committed, so CI
seeds it from the example the way a first run does; `bin/contrast` needs
`bin/render` to have run first, because it asserts the RENDERED page still
carries the three non-colour fallbacks; and `bin/selftest` FAILED on a clean
tree, because the deploy inventory compared the runbook's file table against
`ls` and `deploy/server.env` is gitignored on purpose. A gate that only passes on
the machine that already has the untracked files is a gate no CI can run.
`pull_request`, never `pull_request_target` — the latter runs with the base
repo's token and secrets against a fork's code — a read-only token, and no
`${{ }}` anywhere in the file, since every documented Actions injection begins
with an event field interpolated into a shell.

AND THE FIRST RUN PAID FOR ITSELF, which is the argument for CI in one line: it
went red immediately, on six fixtures that assumed the host ran in the pool's
timezone — see the entry above. Nothing on a workstation could have asked that
question, because the workstation IS the assumption.

**`python -m poolhound.selftest` is not `bin/selftest`, and for a while it was
not a gate either.** `-m` runs the file as `__main__`, which is a SECOND module
object: every `checks_*.py` does `from .selftest import FAILURES, check` and so
appends to `poolhound.selftest.FAILURES`, while the `main()` running under `-m`
read the empty `__main__.FAILURES` beside it. It printed nine FAIL lines, then
"all checks pass", and exited 0. The Dockerfile's build gate is that exact
command, so no failure in any `checks_*.py` module could fail an
image build — and `bin/selftest`, which imports normally, was unaffected, which
is why every workstation run looked right. The `__main__` block now DELEGATES to
the package module. This is the same defect as the one it was written to fix,
one layer down: the original `-m` bug was "no `__main__`, so it ran nothing".

**"Did not run" is a third outcome, and the runner now prints it.** The deploy
image carries `poolhound/`, `bin/`, the example config, the crontab and the
landing page's cards — no README, no SITE.md, no `deploy/`, no rendered pages.
The cases that read one of those call `selftest.skipped(name, why)` and are
listed by name at the end of the run — counted by the runner, because a number
typed here is a number that drifts, and this sentence carried a stale one; `inventory.register(..., needs=[...])` does the
same for a list whose document is not in the tree, and a proof whose detector
skipped is reported as unprovable here rather than as a detector that failed to
fire. Do not turn any of these back into a silent `return` or an
`open(p) if exists else ""` — reading an absent file as an empty one turns "not
in this tree" into "the script does not do it", which is how two deploy checks
came to report the deploy as broken on every image build.

## Checking your work

`bin/selftest` covers the pure functions where a bug is a wrong ANSWER rather
than a wrong-looking page — `pending_devices`, `newest`, `best_lab`,
`migrate_columns`, `access`, `energy`, and `check_page` itself. Every case in it
is one that was actually wrong at some point. It is a regression net, not
coverage; the visual half still has to be driven in a browser. Beyond that:

```bash
bin/selftest                    # the pure functions; non-zero on a failure
bin/drive                       # both builds in a real browser at phone width
bin/drive --url https://poolhound.example.com/   # what is actually being served
bin/render                      # unfilled tokens, broken JSON and broken JS all raise
bin/contrast                    # WCAG on every palette; non-zero on a failure
bin/chem salt 1 gal             # must be refused: gal is not a unit for a solid
bin/brand --check               # is README's logo still the mark brand.py draws
./deploy/bootstrap-server.sh --check
osv-scanner scan source --lockfile=requirements.txt:deploy/requirements.txt

# the Pi, and the one thing a unit file cannot tell you
PI_HOST=<ip> PI_KEY=~/.ssh/<key> ./deploy/update-pi.sh --check   # REVISION vs local rev
systemctl show poolhound-agent -p WatchdogTimestamp -p NRestarts  # must ADVANCE
systemctl status poolhound-agent-ensure.timer                     # the healer is enabled

# the three exploits that used to work — all must be refused
curl -s -X POST -H 'Origin: https://evil.example.com' \
     -H 'Content-Type: application/json' \
     -d '{"chemical":"acid","amount":1,"unit":"floz","pct":31.45}' \
     localhost:8787/api/chemical                       # 403
curl -s -H 'Host: attacker.example.com' localhost:8787/api/health   # 421
curl -s -X POST -H 'Content-Type: application/json' -d '{}' \
     localhost:8787/api/chemical                       # 403, no token

# and the live boundary
curl -o /dev/null -w '%{http_code}\n' https://poolhound.example.com/            # 200
curl -o /dev/null -w '%{http_code}\n' https://poolhound.example.com/admin.html  # 302
```

Both deploys verify what they shipped. `bootstrap-server.sh --check` compares
the REVISION in the image against BOTH rendered pages — `bin/render` writes
`index.html` first, so a failure in the authenticated build leaves a fresh
public page beside a stale private one with every probe passing.
`update-pi.sh --check` compares the Pi's REVISION against the local git rev and
exits non-zero; it used to print an `agent.py` mtime, which `rsync -a` copies
from the source checkout and so reports when the repo was cloned. Neither
deploy leaves `REVISION` in the working tree: `version_string()` reads it, so
a stray copy stamps every later local render with the commit of the last
deploy.

**But `bootstrap-server.sh --check` answers "is what is deployed working", never
"is what is deployed current", and it is all green on a stale server.** It
compares the image REVISION against the two rendered pages, so a server three
commits behind reports every line green — including `served public page is the
deployed build (<old rev>)`, which is true. MEASURED: on 2026-09-28 the server
was found three commits behind, having failed EVERY image build for four days.
`python -m poolhound.selftest` is the Dockerfile's build gate, and a proof
breaker opened `deploy/bootstrap-server.sh`, which is not in the image —
`FileNotFoundError`, exit 1, build dead — while the site kept serving, the
collectors kept running and `--check` kept passing. After any deploy, compare
explicitly: `git log --oneline <rev-from-check>..main`. And treat "the install
step failed" as a four-day-old problem rather than a new one.

`bin/render` runs `node --check` on every inline script when node is installed,
and validates every embedded JSON block always. Do not remove that: an apostrophe
in a hand-written JS string literal once silently disabled the entire page script
while every screenshot still looked correct.

**For anything visual or interactive, drive it in a browser.** A screenshot is not
enough and reading the source is not either. The whole series palette was falling
back to black for several commits while every screenshot looked plausible; a
confirmation dialog warned about the opposite of the button pressed; a save
handler threw on a page that had no save button and killed the rest of the
script. Each was found by asking the browser what it actually had — computed
style, captured dialog text, real clicks — and none by reading the code.
