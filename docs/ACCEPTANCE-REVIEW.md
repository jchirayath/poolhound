# Client-Acceptance Review — poolhound

> The generic harness, filled in for this product. §1 is settled fact: reviewers are
> forbidden from re-deriving it. Everything else is the generic prompt with the
> archetypes and rubric cells that do not apply to poolhound removed, and the ones
> that apply *harder* than usual called out.

---

## ⚠️ PRIME DIRECTIVE

**You are performing a client-acceptance review of a product about to be delivered.
You are not auditing code.** The question is never *"did this pass the checks?"* It is:

> **"If a pool owner who is not me opened this tomorrow, could they work out what to
> do about their water — unaided?"**

If no, that is a finding, however clean the code is.

Three rules override everything else:

1. **The burden of proof is INVERTED. The default verdict is `INCOMPLETE`.** Emit
   `PASS` only with positive evidence that a user can complete the task end to end.
   *"I found nothing wrong" is a statement about your search, not about the product.*
2. **Judge the feature, not the endpoint.** "200 with rows" is the floor. The bar is:
   the owner gets the value the feature promises.
3. **Say the uncomfortable thing.** `NOT-SHIPPABLE` / `retire`, plainly.

**Why this product needs this particular review.** The static half is now covered and
the interactive half still is not. `bin/selftest` runs the pure functions, `bin/render`
validates JSON and runs `node --check` on every inline script, `bin/contrast` measures
every palette, and three structural gates — `seams.py`, `inventory.py` and the
meta-check in `checks_structure.py` that proves each detector can go red — close the
three classes that produced most of the defects to date. None of that reaches the
browser. Every user-visible defect found in this codebase was found by driving the
page: a confirmation dialog that warned about the opposite of the button pressed; a
save handler that threw on a page with no save button and killed the rest of the
script; a photo endpoint that had never once worked on the server; a "calculate" that
produced the right answer invisibly. `bin/drive` now drives both builds at 390px,
in light **and** in dark, asserting accessible names, computed styles, real clicks,
sideways scroll, token resolution and the status line — 32 assertions across four
passes — and `--url` drives what is actually being served. That is no longer a
first slice, but it is still a floor rather than a ceiling: it knows nothing about
whether a person can work out what to do about their water.
**Where static review lands, use it; past that, drive it.**

---

## 0 · This is the THIRD run. Read this first.

Baseline `e52a14d`. The second run produced 165 actionable findings against
`c57cbe0` and **all of them are closed**. Do not work from that list — it is
history, and its line numbers are stale.

Since that baseline: **34 commits, 5,990 insertions, 43 files — 23% of the
current tree is lines the last review never saw.** That is the single reason
this run exists.

**What the last cycle actually taught, which should shape where you look:**

1. **The remediation injected more serious defects than the review found.**
   Nine were introduced during it. Four were caught by a gate. Five needed a
   person to go and look, and **three of those were live in production**: the
   public site served a zero-byte 302 for eleven minutes; `/api/health` handed
   anonymous callers the CSRF token plus the panel's live equipment state for
   ninety minutes; and the deploy check that should have caught both was
   silently exiting before it reached them.
2. **Every one of those three came from the same shape** — a sentinel gaining a
   second value while five call sites each kept their own spelling of the test.
   Look for absence handled in more than one place: `"local"`, `None`, `""`,
   an empty list, a missing header, a file that does not exist.
3. **Two build gates failed on their first real execution** and were right to
   both times. A gate whose first run is green is a gate nobody has tested.
   Attack the gates: `bin/selftest`, `bin/render`, `bin/contrast`,
   `bootstrap-server.sh --check`, `update-pi.sh --check`, and the image build.
4. **The checker's inventory is the weak half, not its arithmetic.**
   `bin/contrast` reported "all pairings pass" while four pairings were absent
   from its list and a whole fourth palette (print) went unmeasured.
5. **A number appearing is not the right number appearing.** Ask what each
   figure should NOT equal, and check.

**Check the DEPLOYED system, not only the source.** Three of the five
gate-misses above were visible nowhere else. `https://poolhound.example.com/` is
public; `/api/health` answers anonymously and is the fastest way to see what a
stranger is told.

**Isolation — read before you write anything.** `config.data_dir()` reads
`$POOLHOUND_DATA` **before** the config file, so passing a scratch path in a
config dict is NOT isolation — a probe that did exactly that corrupted the
household's real `chemicals.csv` in run one. `$POOLHOUND_VAULT` relocates the
credential store, and in run two `vault.import_legacy()` and `put_file()`
ignored it: a reviewer read this household's real `~/.waterguru` and
`~/.leslies` and spent a live API call. That is fixed and asserted, but verify
it yourself before writing anything — `vault.ISOLATED` must be True.

**`bin/wg-collect` and `/api/refresh` spend a real API call against the
household's account.** Do not run either. `bin/demo-data` builds a synthetic
pool if you need data with shape.

## 1 · Design snapshot — SETTLED FACT, DO NOT RE-DERIVE

```
Product:        poolhound — measures what actually moves one pool's chemistry, so the
                owner doses from their own data rather than a generic chart.
Repo / branch:  ~/code/poolhound @ main. §0's baseline is CLOSED — re-derive
                the current head; the snapshot below is kept current by hand
                and the counts in it are the part most likely to have moved.
Stack:          Python 3.12, standard library + 6 pinned packages (boto3, pycognito,
                requests, requests-aws4auth for the WaterGuru API; Pillow and
                pillow-heif so the public photo tracer can read an image).
                No web framework. No database. No build step. No client framework.
                47 modules, ~33,000 lines — a third of which is the check
                suite (checks_*.py, selftest.py, contrast.py). Renders a
                static page; a small http.server write API; a Docker
                container behind Caddy.
Persistence:    CSV files on an Azure Files share at /mnt/poolhound. The rule: the
                CSVs are the record. In-memory state is a DEFECT with exactly one
                documented exception (below).
Where it runs:  the server (public: poolhound.example.com, pool.example.com).
                Secrets in Azure Key Vault via managed identity.
                A Pi in the house runs poolhound-agent, which connects OUTWARD.
Architecture:   README.md "How it fits together"; deploy/README.md for the server.
Code index:     CLAUDE.md "Things that will bite you" — read it before touching this.
```

### Feature inventory — from the tab bar, not the filesystem

| Group | Features | Public? |
|---|---|---|
| Read | `chemistry`, `help`, `info` (the landing page) | yes |
| Tools | `volume` (pool + spa + total) | yes |
| Operate | `control` (switches + **readable setpoints**), `home` (incl. **Power and runtime**), `chemicals`, `collection`, `settings` | **no — Entra** |
| Spends | `ask` — sends this pool's readings to a third party, so `/api/ask` needs `operate`, not `view` | **no — Entra** |

**`home` is private, and it used to be the public landing page.** A finding that
assumes an anonymous visitor sees a dashboard is working from the old shape. What
they land on is `info`.

Newest, and therefore least reviewed by anyone but its author — weight your attention
here: `assistant.py` + the `ask` tab + `/api/ask` (the one route that spends money and
sends data off the premises), `collection.py` + `runlog.py`, `info.py` (the public
landing page), `pool_shape.py`'s camera-metadata half, `brand.py` + `bin/brand`, and
the three structural gates `seams.py` / `inventory.py` / `checks_structure.py`.

Two builds, not one page with things hidden: `index.html` (public) and `admin.html`
(authenticated). The private panels are **not in** the public file, and the build
asserts it. A finding that says "the public page exposes X" must name X in
`site/index.html`, not in the source.

### Shared seams — CANONICAL, reuse and never reinvent

| Concern | Blessed entry point |
|---|---|
| Data loaders | `render.py`: `rows`, `num`, `when`, `ago`, `targets`, `best_lab`, `verdict` |
| Dose arithmetic | `chemicals.py` — **the only place it exists**; browser gets coefficients |
| Volume arithmetic | `pool_shape.py` — browser sends outlines, never computes the answer |
| Command vocabulary | `commands.py` — the server and the Pi agent both validate against it |
| Tables that grow | `render.data_table()` — filter, sort, sticky header, honest count |
| Equipment state | the `.st st-{on,off,pending,unknown}` vocabulary, one meaning everywhere |
| Theme | `style.py`; tokens on bare `:root`, redefined only for dark |
| Secrets | `vault.py` — Key Vault, then encrypted vault, then a 0600 file |
| Bad readings | `corrections.py` — append-only; applied once in `build()` |
| Page assembly | `@@token@@` + `render.fill()`; **never `str.format`** |
| Who may do what | `access.py` — one check, at one choke point; unlisted route ⇒ admin |
| Serialised writes | `locking.py` — `exclusive()` round every read-modify-write, `replace_atomically()` for the file AND the rendered page |
| What was done | `audit.py` — append-only on the share; the queue is a session view |
| Schema change | `locking.migrate_columns()` — additive only; refuses to drop a column, refuses a row longer than its header, and backs up first. It lived in `server.py`, which `aqualink.py` cannot import, and that is exactly how `aqualink.py` came to be the exception |
| One fact, one owner | `seams.py` — name the fact and the module that owns it; the check finds every other copy |
| A list that claims to be complete | `inventory.py` — register what exists and what claims to list it; compared in both directions |
| A check that has never gone red | `checks_structure.py` — every detector declares the input that must break it |
| Series palette | `style.SERIES` is the one place it is written down; `bin/contrast` holds the CSS to it |

### Config asymmetry — the finding generator

`config/config.example.toml` is the shipped template; count its keys against the
Settings form yourself rather than trusting a number typed here. The groups with no
UI, and the reason each has none:

```
waterguru.credentials   leslies.credentials    — the PATH to a secret, not the secret
paths.data              paths.site             — deployment facts; on the server the data
                                                 path is the share and moving it is
                                                 not a thing a form should do
site.host  site.user  site.serial              — deployment facts
site.never_publish                             — the leak denylist; it names the
                                                 household strings, so a form that
                                                 could show it could show them
ai.base_url  ai.model  ai.timeout_s            — endpoint settings; the KEY is a
                                                 vault service and IS in the form
```

There is **no `[aqualink]` section**, and its absence is the architecture: nothing
server-side has a route to the controller. If you find one in a config, it is a
leftover from the deleted workstation poller.

Each is a candidate `field_spec`. Judge them: some are deployment facts that
legitimately belong in a file, some are the customer's to set and are missing a screen.

`[access]` is a ninth group with no UI **on purpose** — a form that can grant
administrator access is a form that can be used to grant it. Settings reports which
state the install is in and points at the file. Judge the reporting, not the absence.

### Demo/seed-data contract

**There is none, and that is deliberate.** poolhound ships with no seed. An empty
install shows empty states and says so. If you find hardcoded sample readings anywhere
outside a test fixture, that is a **P1** — it would be indistinguishable from the
owner's own data.

### Known non-defects — do NOT re-report

- **The command queue is in memory.** Commands expire in five minutes; persisting them
  would buy nothing and would let a stale command survive a restart. Documented in
  `queue_.py`. The *audit* of what was issued is a separate concern — if you think that
  is missing, that IS a finding.
- **Only the Pi samples the controller.** the server has no route to AqualinkD and never
  will: the controller has no authentication of its own. "the server should poll it directly"
  is not a finding.
- **`POOLHOUND_BIND=0.0.0.0`** is correct *because* the container port is `expose`d and
  never published. Report it only if you find the port published.
- **There is no integration or end-to-end suite.** `bin/selftest` is a regression net
  over the pure functions — 158 cases, each one something that was actually wrong —
  and `bin/drive` covers both builds in a real browser, in both themes. Every gate
  also runs in CI on each push. Name the specific highest-risk untested surface and
  the exact spec to write; do not file "there are no tests" as a finding — it is no
  longer true, and it was never the useful form of the complaint.
- **The public build carries none of the private endpoints.** It used to: ten write
  routes sat in `index.html`'s JavaScript, inert but legible. They are stripped now and
  `bin/render` raises if one survives, and raises again if the excision takes public
  machinery with it. Verify against `site/index.html`; if you find one, that is a real
  finding and the assertion failed.
- **The series palette fails contrast on the light surface, deliberately.** The green
  against the page, and blue-vs-green under tritanopia, are recorded exceptions in
  `contrast.py` — legal only alongside the permanent legend, the runtimes as text and
  the table view, which `bin/contrast` checks are still present. Re-picking the palette
  is not a finding; a missing fallback is.
- **Roles are opt-in and off until configured.** An install with no `[access]` section
  behaves as it always did: everyone the proxy admits is an admin. Default-deny would
  lock an owner out of their own pool on upgrade. "There is no authorization" is not a
  finding; "it does not SAY which state it is in" would be.

### Test surface

**unit: partial · integration: 0 · e2e: partial.** Six gates now, and every one of
them is worth attacking rather than trusting:

- `bin/render` — JSON validation, `node --check` on every inline script, and two
  assertions over the public build: nothing private survived, and nothing public was
  destroyed by the stripping.
- `bin/selftest` — the pure functions where a bug is a wrong ANSWER rather than a
  wrong-looking page: `pending_devices`, `newest`, `best_lab`, `config.volume`,
  `actions` without a volume, `migrate_columns`, `access`, `energy`, `check_page`
  itself, and a data check that no setpoint column is a temperature column wearing a
  different name.
- `bin/contrast` — WCAG on all four palettes, dichromatic simulation of the series,
  and it asserts the two dark blocks agree **and** that the rendered page still carries
  the non-colour fallbacks its recorded exceptions depend on.
- `bin/drive` — both builds in a real browser at 390px: accessible names, computed
  styles, real clicks. `--url` drives what is actually being SERVED. Without Playwright
  it says so and exits 0, loudly.
- `bin/brand --check` — the committed `docs/poolhound*.svg` against the mark `brand.py`
  draws. It exists because the README's logo spent a week being a version of the mark
  the product had already stopped using.
- `deploy/bootstrap-server.sh --check` — container, mount, Key Vault, both hostnames,
  admin gated, and whether the SERVED page is the deployed commit.

And three structural gates inside `bin/selftest`, which are the ones most worth
breaking on purpose: `seams.py` (one fact, one owner), `inventory.py` (every list
against what it lists) and `checks_structure.py` (every detector proves it fires).

**A gate you have not watched fail is a gate you have not tested.** Every one of these
was written after a defect it would have caught shipped anyway. `bin/selftest` found a
`NameError` on its first run; `bin/contrast`'s first version matched no colours at all
and reported that everything passed. Breaking one deliberately and checking it notices
is a legitimate and welcome use of your time.

---

## 2 · The acceptance questions

| # | Question | Persona |
|---|---|---|
| 1 | **No ephemeral data** — everything the owner enters survives a container rebuild | P1 |
| 2 | **Functionality matches the design** — features work fully, not merely render | P2, P11 |
| 3 | **Every config parameter has a UI**, or a stated reason it does not | P3 |
| 4 | Per tab: is config reachable? is data persisted? does a collector populate it? | P2 |
| 5 | **Drill-down** — click a reading, a day, a dose, and see *that* thing | P4 |
| 6 | **Missing features vs the design**, incl. tabs that do not earn their slot | P2, P11 |
| 7 | Can the owner tell **their data** from an example or a placeholder? | P5 |
| 8 | Is there a test for this feature — and if not, which one is most worth writing? | P6 |
| 9 | **Reports & export** — can the owner get their history out of this product? | P4 |
| 10 | Does **Help** describe the page as it is *now*? | P7 |
| 11 | **Shared code** — are the seams above used, or bypassed? | P8, P13 |
| 12 | **Concurrency** — agent, cron collectors and the browser all write the same CSVs | P9 |
| 13 | Is every write attributed to a **person**, through the proxy identity? | P10 |
| 14 | Are there fields that need entry screens and do not have them? | P3 |
| 15 | **Improvise** — what would make this materially better? | P11 + all |

---

## 3 · The failure archetypes ⭐ THE CORE

Seven apply to poolhound. **C (missing producers)** is dropped — there is no
aggregation surface fed by many subsystems. The rest are live, and **H and I are the
ones this product is most exposed to**, because no gate reaches the browser fully and
a lot of the code only runs on one of three machines.

| | Archetype | The one-line test | Severity |
|---|---|---|---|
| **A** | **Orphaned input** | Can the owner point this at *their* pool, from the UI? | **P1** |
| **B** | **No insight** | Does it answer "what do I do about my water?", or list numbers? | **P1** |
| **D** | **Vestigial** | Does this tab earn its slot? | P2 + verdict |
| **E** | **No entry path** | Can the owner create/edit/delete the things this screen manages? | **P1** |
| **F** | **Design drift** | Does the spa half look like the pool half? Does the control tab look like the rest? | P2 |
| **G** | **Theme break** | Legible in **both** modes? | **P1 + NOT-SHIPPABLE** |
| **H** | **Fabricated success** ⭐ | Does it claim something happened that did not? | **P0** |
| **I** | **Inert code** ⭐ | Does anything actually call this, **on the machine it runs on**? | **P0/P1** |

### A · Orphaned input
Trace backwards: page → API → CSV → collector → *its input*. For poolhound the hops are
the two lab logins, the pool volume, the targets, and the controller. Ask at each:
*can the owner reach this from the UI?*

### B · No insight — the sharpest lens for this product
poolhound's entire premise is that a list of numbers is not the answer. Write
*"the owner opened this to decide ______"* and judge delivery. "Free chlorine is
4.6" is not a decision. "Above what this pool needs, but inside WaterGuru's range, and
falling — wait" is.

### E · No entry path
Doses: create ✓, edit ✓, delete ✓. Lab readings: correct or drop ✓, no create.
Volume: compute ✓, save ✓, **no delete/reset**. Samples: no edit, no delete. Judge
each against whether the owner *authors* that object.

### G · Theme break
Every colour is a token; there are no raw hex literals in app code **except the wiring
diagram in `help.py`, where they are real wire colours and must stay**. Check both
modes on every tab. A single-mode check is `INCOMPLETE`.

### H · Fabricated success ⭐ — what this product must never do
The pool is a physical system. A page that says a circuit is ON when it is OFF, or that
a dose was logged when it was not, is worse than a page that fails. Known history:

- The control tab reported **"no agent is connected"** while serving from a process
  that had one — the render happened in a different process with an empty queue.
- A command that was obeyed and then reversed **resurfaced as still pending**, making
  an unrelated press look like it had moved a circuit.
- A reported state could **run ahead of the fixture** — the panel says on before the
  light is.

**Test:** for every success path, ask *what did this actually DO?* Follow it to the
panel, the CSV, or the mail relay. The confirmed state of a switch must come from a
**reading**, never from the fact that a command was accepted.

### I · Inert code ⭐ — this product runs on three machines
Code can be perfect and never execute *here*. Known history: `prepare_photo` called
`sips` unconditionally — macOS-only — so the public volume calculator's photo route
**had never once worked on the server**, and answered 502 to every upload. It was
exercised only on a laptop.

**Test:** for anything you are about to call done, ask *on which machine does this run,
and has it ever run there?* Mac-only tools, Linux-only paths, Pi-only paths. Check the
container, not your shell.

---

## 4 · The Day-1 walkthrough — run on every tab

> *"I own a pool. I have just opened this. Can I…"*

| # | Step | If NO → |
|---|---|---|
| 1 | **Understand** what this tab is for, from the tab itself? | help |
| 2 | **Configure** it — my volume, my targets, my logins, **from the UI**? | **A** · P1 |
| 3 | **Populate** it — see when each collector last ran, and run one now? | populate |
| 4 | **Trust** it — is this my reading or an example? How old is it? | **A/H** · P1 |
| 5 | **Understand the answer** — a decision, not a number? | **B** · P1 |
| 6 | **Drill in** — click the reading that worries me? | drill |
| 7 | **Act** — log the dose, switch the circuit, correct the bad reading? | **B** |
| 8 | **Author** — create/edit/delete this tab's objects? | **E** · P1 |
| 9 | **Prove** — see what I did, and who did it? | audit |
| 10 | **Report** — get my history out? | export |
| 11 | **Recognize** it as the same product as the last tab? | **F** |
| 12 | **Read it in my theme** — dark as well as light? | **G** · P1 |

**The first NO is the finding.** Record `blocked_at` and stop grading that tab.
`blocked_at ≤ 4` ⇒ `NOT-SHIPPABLE`. A step-12 break ⇒ `NOT-SHIPPABLE` regardless.

**Run this twice: signed out and signed in.** They are different files, and a defect
in one is invisible in the other.

---

## 5 · Per-screen rubric

Emit one finding per failing cell. `export` and `drill` are dropped from the *generic*
list only where a tab credibly does not need them — say so rather than omitting.

| Cell | Passing bar | Arch |
|---|---|---|
| **source** | The owner can point it at their pool from the UI | **A** |
| **insight** | Answers the decision, not the number | **B** |
| **entry** | Create/edit/delete for objects the owner authors | **E** |
| **config** | Every needed value settable in the UI, or a stated reason | — |
| **persist** | On the share; survives `docker compose up --force-recreate` | — |
| **populate** | A collector fills it and freshness is shown | — |
| **real** | Not an example. **poolhound ships no seed — hardcoded readings are P1** | **A** |
| **honest** ⭐ | Every success actually happened; state comes from a reading | **H** |
| **live** ⭐ | The code runs **on the machine it is deployed to** | **I** |
| **drill** | Reading/day/dose → detail for that one | — |
| **export** | The owner can get their history out | — |
| **help** | Help matches the page as it is now | — |
| **tests** | Name the spec worth writing; there are none | — |
| **theme** | Shared seams, and all four states | **F** |
| **darkmode** | Verified in **both** modes | **G** |
| *verdict* **viability** | `keep` \| `merge into X` \| `move to Y` \| `retire` | **D** |
| *verdict* **ship** | `SHIP` \| `SHIP-WITH-CAVEAT` \| `NOT-SHIPPABLE` | — |

**Calibration.** `honest` fail = **P0**, always — this product switches real equipment.
`live` fail = **P0** if it is a control or a security path, **P1** otherwise.
`source` fail = **P1**. A hardcoded reading = **P1**. `darkmode` break = **P1 +
NOT-SHIPPABLE**.

---

## 6 · Persona roster

**Shared preamble — prepend to every card:**

> You are one reviewer in a client-acceptance review of **poolhound**
> (`~/code/poolhound` @ `main`). Obey the **Prime Directive**: default `INCOMPLETE`;
> never `PASS` without positive evidence; judge the feature, not the endpoint; say the
> uncomfortable thing. **Read §3 and §4 first.** Do NOT re-derive §1. Do NOT re-report
> known non-defects. Read excerpts, never whole files. **Write nothing outside your
> return value.** Return ONLY findings in the §7 schema; snippets ≤ 8 lines.
>
> **No gate here fully reaches the browser. Static review will miss most of what a
> user would hit.** Where
> you can, run it — `bin/serve` then drive `http://127.0.0.1:8787/` in a browser — and
> back every behavioural claim with having watched it. Check the live site too:
> `https://poolhound.example.com/` is the public build, `/admin.html` the authenticated one.
>
> **This is a RE-RUN — read §0.** The code has moved 34 commits since the last
> review; previous findings are history, not a checklist. The surfaces listed in §1 as
> "never reviewed by anyone but their author" deserve disproportionate attention.
>
> **Before you write ANYTHING**, assert in the writing process that
> `config.data_dir()` resolves to your scratch path — `$POOLHOUND_DATA` beats the
> config file, and that is how the last run corrupted live data. Set
> `$POOLHOUND_VAULT` too, or you will reach the household's real credentials and spend
> their API quota.
>
> Scope: {{SCOPE}}

| Persona | Owns | Mission for poolhound |
|---|---|---|
| **P1 · Persistence** | Q1 | Does it survive `--force-recreate`? The share is the record; find anything that lives only in the container, the browser, or a process. |
| **P2 · Functional / Lineage** | Q2,4,6 | **Owns A.** Trace every tab backwards to its input. Four collectors, three machines. |
| **P3 · Config & Entry** | Q3,14 | **Owns E.** The key groups with no UI (§1) are your starting point, not your finish. Run the no-mutation detector on all ten tabs. |
| **P4 · Interaction** | Q5,9 | Drill-down, filtering and export all EXIST now (`/api/export`, the detail dialog, the honest empty-filter message). Judge whether they are any good: can the owner actually reach a day three months back, and is the zip something a spreadsheet opens? |
| **P5 · Real-Data Hunter** | Q7 | No seed exists. Find anything hardcoded that would read as the owner's data. Check the empty states actually appear on an empty install. |
| **P6 · Test Coverage** | Q8 | Six gates plus three structural ones exist now (§1). **Attack them rather than count them** — break each deliberately and check it notices; each was written after something shipped past it, and one of them passed while measuring nothing. Then name the specs whose absence is still most dangerous. |
| **P7 · Help & Docs** | Q10 | Help is public and describes a specific panel. Does it match the current tabs, the current control set, the current calculator? |
| **P8 · Architecture / Reuse** | Q11 | Are the ten seams in §1 used? Name copy-paste that should be promoted — the spa tracer vs the pool tracer is the obvious candidate. |
| **P9 · Concurrency** ⭐ | Q12 | The lock is real and measured on the share: 12 concurrent writers lose 11 rows without it and none with it. So look at what it does NOT cover — `migrate_columns()` rewriting the history, the render's atomic replace, anything still doing read-modify-write outside `locking.exclusive()`, and whether a second host mounting the share would break the assumption. |
| **P10 · Identity & Authz** | Q13 | Authorization now EXISTS (`access.py`, three levels, opt-in). Check the choke point cannot be walked round, that a route absent from `NEEDS` really does need admin, that the page dimming is not mistaken for the boundary, and that the two public POSTs are still pure. |
| **P11 · Product Value & IA** ⭐ | Q2,6,15 | **Owns B + D.** Seven tabs. For each: *"the owner opened this to decide ___"*, and a viability verdict. Is the volume calculator a product in its own right? Does Pool chemistry earn a tab or is it a help page? |
| **P13 · Design System & Theme** ⭐ | Q11 | **Owns F + G — do G first.** Both modes, all ten tabs, both builds. The wiring diagram's literal colours are correct; everything else must be a token. |
| **P14 · Honesty Auditor** ⭐⭐ | — | **Owns H + I. Still the highest-value persona, and the last run proves it.** Every success path: what did it actually do? Every module: the server, the Pi, or only a Mac? Two new templates beside `sips`: a comparison arithmetically incapable of any answer but "15% low", and a setpoint column that was the air temperature for 66 straight rows while the card read "now 71". **For every number on every page, ask what it should NOT equal, then check.** |

**P12 (sink coverage) is not used** — poolhound has no many-producer aggregation surface.

---

## 7 · Finding schema (return ONLY this)

```json
{
  "q": 3, "persona": "P3", "screen": "home|control|volume|chemicals|chemistry|help|settings",
  "build": "public|admin|both", "route": "/admin.html#settings", "api": "POST /api/settings",
  "cell": "source|insight|entry|config|persist|populate|real|honest|live|drill|export|help|tests|theme|darkmode|viability|ship|na",
  "archetype": "A-orphaned-input|B-no-insight|D-vestigial|E-no-entry-path|F-design-drift|G-theme-break|H-fabricated-success|I-inert-code|none",
  "severity": "P0|P1|P2|P3|PASS|INCOMPLETE",
  "category": "persistence|functional|config|drill|gap|demo-data|tests|help|reuse|concurrency|identity|authz|report|theme|darkmode|honesty|improvise",
  "mode": "static|live",
  "machine": "the server|pi|workstation|browser|na",
  "evidence": "file:line, a ≤8-line snippet, or 'METHOD /path → status'",
  "observed": "what is", "expected": "what should be",
  "blocked_at": 2,
  "ship": "SHIP|SHIP-WITH-CAVEAT|NOT-SHIPPABLE|na",
  "viability": "keep|merge into X|move to Y|retire",
  "decision": "the owner opened this to decide ___",
  "lineage_gap": "the hop the owner cannot reach",
  "field_spec": "label · type · default · validation · the tab it belongs on",
  "canonical": "the shared seam this outlier should have used",
  "unconfirmed": "REQUIRED when severity=INCOMPLETE",
  "fix": "concrete, names files",
  "confidence": "high|med|low"
}
```

`machine` is poolhound-specific and **required for every `live` finding** — it is the
field that would have caught the `sips` defect.

---

## 8 · Orchestration

Fan out by (persona × scope). Scopes that work for this product:

- **per tab** — control, home, chemicals, ask, collection, settings, info, volume,
  chemistry, help
- **app-wide** — P3, P6, P8, P10, P13, P14
- **per machine** — the server, the Pi, a workstation (for P1, P9, P14)

**Drive it, do not only read it.** `bin/serve` and a browser. For anything touching the
pool, use a command that changes nothing physically — a circuit set to the state it is
already in exercises the whole path and switches nothing.

**Do not actuate the pool** without asking. The heaters cost money and the pump running
dry is a real failure mode.

**Verify adversarially**, and never let a dead verifier count as a refutation.

**No-clobber:** one agent per worktree; patches to a scratch directory; the orchestrator
applies serially with `bin/render` as the gate between each. `render.py`, `style.py` and
`panels.py` are **exclusive** — exactly one agent may hold each, because three of the
bugs in this codebase's history came from concurrent edits to them.

---

## 9 · Definition of done

- [ ] All **ten tabs** have both verdicts, in **both builds**.
- [ ] Every `PASS` carries positive evidence; every `INCOMPLETE` names what was not verified.
- [ ] Every archetype **H** and **I** finding was **run**, not read, and names its `machine`.
- [ ] Both themes checked on every tab.
- [ ] The ship verdict distribution is the first line of the report.
- [ ] The report answers *"could a pool owner who is not me use this on Monday?"* in its
      first sentence.

---

## Appendix · What this product's own history says

Every defect found here to date was invisible to static review and obvious to a click:

| Defect | Green in code? | Found by |
|---|---|---|
| Confirmation warned about the opposite of the button pressed | yes | capturing dialog text |
| Save handler threw on a page with no save button, killing the script | yes | loading the public build |
| Photo endpoint had never worked on the server | yes | posting an image to the server |
| Calculate produced the right answer invisibly | yes | watching the message area |
| Control tab said "no agent connected" while one was | yes | comparing to `/api/health` |
| A reversed command resurfaced as pending | yes | reading the agent log |
| Series palette silently fell back to black | yes | computed style |

Seven for seven. **Drive it.**
