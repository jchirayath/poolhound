# How the website is put together

poolhound has no web framework, no client-side router and no build toolchain.
It has one Python function that writes two HTML files, and a directory that a
web server points at. This document is how that works and where to change it.

[README.md](README.md) is the architecture of the whole system;
[THEORY.md](THEORY.md) is the chemistry; [deploy/README.md](deploy/README.md) is
the runbook for the host. This is the page.

---

## Two files, not one page with things hidden

`bin/render` writes **`site/index.html`** and **`site/admin.html`**. They are the
same product; the public one is the authenticated one with the six private
panels cut out of it, along with the script that drives them.

The private panels are **not in the public file**. Not hidden with CSS, not
gated by a flag the browser evaluates — absent. "The tab is not shown" is one
view-source away from "here is the internal address of the pool controller", so
the split happens at build time, where a mistake fails by showing too little.

The build then proves it, rather than trusting the excision:

| check | what it refuses |
|---|---|
| `PUBLIC_MUST_SURVIVE` | a public tab's id or a public route that went missing |
| `PUBLIC_MUST_SURVIVE_COUNTS` | tabs whose contents were emptied while the wrapper survived |
| `PRIVATE_MUST_SURVIVE_COUNTS` | the same, for the authenticated build, which nothing cuts |
| `PUBLIC_MIN_BYTES` | a public page far smaller than the material it is made of |
| the leak list | a house address, a token path, a control endpoint, or a household pattern from `site.never_publish` |
| the fetch allowlist | any `fetch()` in the public script whose target is not a literal, or is a route an anonymous visitor may not call |
| the dead-link check | a link or a sentence pointing at a tab this build does not contain |

Every one of those exists because it once failed. The excision itself cuts each
panel at its own `<!-- /tab -->` end marker and refuses if the marker is not
found exactly once — an earlier version ran "to the next panel or the end of
`<main>`", which is the pattern that takes one element too many when markup
moves.

---

## The tabs

Six constants in `render.py`, and nothing else decides this:

```python
PUBLIC_TABS  = ("info", "volume", "chemistry", "help", "policies")
PRIVATE_TABS = ("control", "home", "chemicals", "collection", "settings", "ask")
TAB_ORDER    = ("home", "control", "settings", "chemicals", "ask", "collection",
                "info", "volume", "chemistry", "help", "policies")
TAB_GROUP_START = "info"
PUBLIC_DEFAULT_TAB  = "info"
PRIVATE_DEFAULT_TAB = "home"
```

**Each build opens on the first tab of its own order**, and that is the rule
rather than a coincidence: a nav whose first item is not the page you land on
reads as a bug in the nav. The authenticated build therefore opens on Home —
where the water is now, what is running, and what to do next — and the
controls sit one tab along. It opened on Pool control until the nav put Home
first, on the reasoning that somebody who signs in came to operate the pool.
Still true; but the dashboard answers "is anything wrong" before you have asked
anything, which is the question you actually arrive with.

`PUBLIC_TABS` is both the classification **and** the public nav order.
`PRIVATE_TABS` is what the public build cuts. `TAB_ORDER` is the authenticated
nav. A tab in neither classification tuple **stops the build** — the excision is
a denylist, so an unclassified tab would ship to anonymous visitors silently,
and guessing in either direction is worse than refusing.

The divider at `TAB_GROUP_START` separates the screens that operate the pool
from the ones that explain it, and it means something checkable: **everything
after it is exactly `PUBLIC_TABS`, in the public order.** A check asserts both
halves, so the line cannot quietly come to separate something else.

Each build's opening tab is **written**, not inherited: `set_default_tab()`
rewrites every button and every panel from one argument and then asserts exactly
one selected button, exactly one visible panel, and the same one. The template
can only carry one default and the two builds want different ones.

The page script reads that choice back out of the markup rather than naming a
tab of its own. It used to name one, which reopened Home over the controls on
the authenticated build and — once Home went private — left the public build
with every panel hidden and nothing selected: a page that rendered correctly and
went blank a frame later.

### The masthead says what this is, then what it does

A visitor who has never heard of this arrives at a wordmark and whatever sits
under it, and for a long time what sat under it was three clauses of behaviour
run into one serif sentence: reach the controller, dose from the record, work
out the volume. Every word true, and no noun anywhere naming the category they
belonged to — a list of verbs offered to somebody who had not yet been told
what the thing was.

So the shape now matches the content. One sentence saying what poolhound **is**
— `info.CATEGORY`, read by the browser tab, the `<meta name="description">` and
this line alike, because a product that types its category twice shortly calls
itself two things in one document — and then the three things it **does**, as
three `<li>`s rather than as clauses of each other. Both builds carry the whole
block: the public page and the signed-in page are the same product.

It is assembled from `info.PURPOSE` and not typed into the template, which is
the entire reason it cannot drift from the landing page directly below it. That
has been got wrong twice — the masthead named one purpose of three, was fixed,
and the landing page's own opening section then made the identical cut a week
later. `checks_chrome.t_the_page_and_the_masthead_describe_the_same_product`
asserts the arithmetic rather than the wording.

### Two tabs are private for different reasons

**Home** is a dashboard, and a dashboard answers "how is it doing" to somebody
who has not been told what "it" is. Nothing on it is secret — it is derived
from the same measurements the chemistry reference is — but the argument for
publishing chemistry is that it is useful to *somebody else's* pool, and that
argument does not reach this household's current chlorine. The masthead's
freshness line and overall verdict are gated with it.

**Ask AI** is private for a second reason on top of that one: asking sends this
pool's readings to a third party and spends money doing it, so `/api/ask` needs
`operate` rather than `view` — the floor for merely reading the private tabs is
not enough for a route that acts.

### Adding a tab

1. Add the panel to `TEMPLATE`, with an `id="tab-<name>"` and a closing
   `<!-- /<name> -->` marker.
2. Add a `<button role="tab" data-tab="<name>">` to the nav.
3. Put `<name>` in `PUBLIC_TABS` **or** `PRIVATE_TABS`, and in `TAB_ORDER`.
4. Add its heading substrings to `icons.SECTION_ICONS` — the coverage ceiling is
   zero, so an unmapped heading fails the build.
5. If it is public, add its id to `PUBLIC_MUST_SURVIVE`.

`bin/render` will tell you which of these you forgot.

---

## The render pipeline

```
bin/render
  └─ render.build(public=…)          one at a time; it rebinds module globals
      └─ _build()
          reload()                   CFG / DATA / SITE / POOL, and panels' copies
          loaders                    rows, num, when, ago, targets, best_lab, verdict
          fill(TEMPLATE, **panels)   @@token@@ substitution; raises on an unfilled one
          with_subnav()              per-panel jump list, derived from the headings
          ── public build only ──
          strip priv:start/end       the script that drives private panels
          classify + excise          per-tab, at the end marker
          leak list / fetch allowlist / dead links / prose check
          ───────────────────────
          order_tabs()               nav laid out from the tuple, divider inserted
          set_default_tab()          one selected button, one visible panel
          a11y_wiring()              tabs to panels, status regions
          check_page(), check_svg_styles()
          replace_atomically()       the served file is never half-written
          copy_cards()               the landing page's screenshots
```

**One render at a time.** `_build()` rebinds this module's globals, and the
server renders from daemon threads after every write and three times per agent
sample — two overlapping renders once swapped the pool volume out from under
each other halfway down a page.

**A render can happen in a different process from the one serving.** Cron's
render and `docker compose exec … render` each get their own empty in-memory
queue, so anything *live* — whether an agent is connected, what is pending, the
command history, the current setpoints — must be fetched by the browser from
`/api/health` or `/api/commands`, never baked in.

---

## The template

`TEMPLATE` is an ordinary triple-quoted string in `render.py`, and it carries the
page's JavaScript and a JSON catalogue. Two consequences:

- **Placeholders are `@@name@@`, not `{name}`.** The page is full of braces.
  `fill()` raises on any token left unfilled.
- **`\n` inside a JavaScript string literal must be written `\\n`**, because
  Python parses this file first. This has broken the whole page script three
  times. `\s` and `\d` in a regex literal need the same.
- **A fact the browser holds is still a fact with one owner.** The series
  colours were retyped here as the JS fallbacks — `#2a78d6`, `#eb6834`,
  `#1baf7a` — in the one place `bin/contrast` cannot measure and a screenshot
  cannot show, so re-picking a colour in `style.py` left the charts drawing the
  old one wherever the CSS token was missing. They are `@@c_pump@@`,
  `@@c_spa@@` and `@@c_sheer@@`, filled from `style.SERIES` like the legend
  beside them. The seams gate used to blank every triple-quoted run before
  looking for a duplicated fact, which made 59% of the package — and 78% of
  `render.py` — invisible to it; it now asks `ast` which strings are docstrings
  and leaves this one in scope.

`bin/render` runs `node --check` over every inline script when node is installed
— and node **is** in the deploy image, deliberately, because that check is the
entire automated gate between an apostrophe in a JS string literal and a dead
page. Every embedded JSON block is validated always.

---

## Where each panel lives

```
render.py       the page shell, the loaders, the charts, and the Home panel
info.py         Poolhound info — the public landing page
chemistry.py    Pool chemistry, and its own EXTRA_CSS
panels.py       Pool control, Chemicals, Settings, and the volume calculator
collection.py   Collection — every fetch, and whether alerts were delivered
assistant.py    Ask — the question, the context it sends, and the client
help.py         Guide (tab id `help`), and the two architecture drawings
pool_shape.py   the photo tracer's geometry, and the camera metadata
style.py        every rule, and the colour tokens
icons.py        the icon paths, and the heading → icon table
brand.py        the mark: masthead, favicon, diagrams, the Info tab, and
                docs/poolhound*.svg — which bin/brand writes, because GitHub
                renders an <img> and cannot run anything
```

`render.py` **owns the loaders** — `rows`, `num`, `when`, `ago`, `targets`,
`best_lab`, `verdict`. Other panels import them, so two tabs cannot disagree
about what the current alkalinity is.

---

## Styling

Every colour is a token on bare `:root`, redefined only for dark. The common
case is the un-stamped "system theme" state, and a colour whose only definition
sits inside a media query does not apply there. `bin/contrast` measures every
text-on-ground pairing in all four palettes — light, both dark blocks and print
— simulates three colour deficiencies, and checks the rendered page still
carries the non-colour fallbacks its exceptions depend on.

Two rules that are not preferences: **never colour alone** — every equipment
state ships with its word — and **never a dual-axis chart**.

### Two families, and the split carries information

**If the pool said it, it is set in sans. If a person wrote it, it is set in
serif.** Every figure, unit, label, control, table cell, badge and chart is
`--sans`; every heading, the masthead statement and the three lines under it,
and all the explanatory prose is `--serif`. That is a rule a reader learns without being told, and it is the
reason for a second family rather than a taste for one — this product is a
record of measurements, and most of it is prose explaining what the
measurements mean.

**No web font, and not as a compromise.** The policy is `default-src 'none'`
with no `font-src` at all, so a hosted face would be refused by our own server
and work everywhere else — the precise failure mode `blob:` was added to
`img-src` to avoid. Both are stacks of faces already on the machine; `ui-serif`
lets a browser answer with its own.

### The scale

It was `h1` 19px, `h2` 15px, `h3` 13px, body 14px: **a section heading one
pixel larger than the paragraph under it.** Nothing established rank, so forty
cards on one page all spoke at the same volume and the page read as an internal
tool whatever was on it. The tokens are real intervals now, and `--fig` — a
measurement — is deliberately the same size as `--display`, because on an
instrument the number *is* the headline.

| token | light | phone | role |
|---|---|---|---|
| `--lead` | 27px | 23px | the public masthead's one sentence |
| `--display` | 33px | 27px | the wordmark |
| `--h2` | 21px | 19px | a section |
| `--h3` | 15.5px | — | a card title |
| `--prose` | 15.5px | — | serif body |
| `--body` | 15px | — | sans body |
| `--fig` | 33px | 28px | a measurement, tabular |
| `--meta` | 12.5px | — | source, age, units |

### Surfaces, and what a card is for

Three levels, not one. A **section** is space and a hairline rule. A **card** is
a *discrete object* — a tile, an advisory, a control — and is flat with a
hairline, because a shadow under every one of forty boxes flattens the very
hierarchy a shadow exists to express. `--lift` is kept for the things that
genuinely float: the refresh menu, the dialog. Radius is `--r-sm` / `--r-md` /
`--r-lg` rather than the seven values that were in this file.

**Prose narrows its container, not its line.** Text was capped at 66–76ch
inside a 1040px box, so a real paragraph ran 380–480px wide with a card border
at 1000px around it — the right half of every section empty and the box
advertising it. `--measure` is the one cap, and data keeps the full width.

**The two navs are not peers.** `.tabs` chose the panel and is the louder, in
its own rail sized to its tabs; `.subnav` is a jump-list inside that panel and
gives up its container for quiet text with a sticky rule. They were
near-identical pill rows stacked ten pixels apart, so neither read as senior
and the second looked like more of the first.

**Eleven tabs do not fit one rail on the authenticated build, by choice.** Ten
filled it to the pixel (1048 of 1048 at 1098px), so Safety & privacy put the
signed-in nav on two rows at desktop widths, and that was taken over folding it
into Help or a footer-only page. The public build's five still fit one row, and
at phone width the rail scrolls sideways either way. What must not happen is
the row count CHANGING with the selected tab, which is the paragraph below.

Ten tabs did not fit one 1098px rail by much, and the **selected** tab is bolder
than the others — so slack has to be real rather than exact. At 1098 against
1098 the bar reflowed when you changed tab, and which tab you were on decided
whether the menu was one row or two.

**A `<style>` inside an inline SVG is not scoped to it.** It joins the document
stylesheet like any other, so the architecture drawings' class rules are written
`svg .lbl { … }`. Unscoped, `.lbl` also matched the label span inside every tab
button, and set the entire tab bar in the wiring diagram's monospace face on
both builds for as long as the diagrams had existed.

**A class the page wears has a rule.** A check compares every class in each
rendered page against every stylesheet in it, with a named roster of
script-only hooks and one documented prefix for the computed `s-{state}` family.
It found four components asking for styling that did not exist.

**No two elements share an id.** The landing page shows the same architecture
figure Help does — one drawing, not a second that could disagree — so the figure
takes an id prefix. Without one the page carried two `ad-t` titles and
`aria-labelledby` named whichever the browser reached first.

---

## Assets

The landing page's screenshots are a **build input**, not a build output.
`bin/screenshots` drives a real browser over a synthetic pool from
`bin/demo-data`, writing full captures to `docs/screenshots/` for README and
cropped cards to `docs/screenshots/cards/` for the page. The container that
renders in production has no browser and never will, so the cards are committed
and `COPY`d into the image; `bin/render` copies them into the site directory.

They are pictures of a pool that does not exist, and the captions say so. A live
public dashboard would be this household's readings, and a hand-cropped
screenshot of the real one would be the same thing with no check on it.

`docs/poolhound.svg`, its dark twin and the square tile are the same shape of
thing: committed files that are OUTPUT. `bin/brand` writes them from
`brand.py`, and `inventory.py` compares the bytes on disk against what
`brand.py` produces — because for a week the README's logo was a version of
the mark the product had already stopped drawing, and nothing could see it.

---

## What the browser is allowed to do

The page loads **nothing** from anywhere — no CDN, no icon font, no web font, no
network call at all, and the CSP that ships with the server enforces it. Icons
are paths in `icons.py`; diagrams are inline SVG; the mark comes from
`brand.py`.

Two routes are public POSTs — `/api/pool-shape/compute` and `/api/photo`. Both
are pure: they read nothing, write nothing and keep nothing, and they carry
their own rate limit. `/api/pool-shape` with no suffix **saves**, and stays
gated; the Caddy matcher has no trailing wildcard for exactly that reason.

The photo endpoint returns the uploaded image **stripped of metadata**,
deliberately. The camera fields it reports are read from the original before
conversion; carrying the tags across into the served copy would put the GPS fix
of somebody's house into the response of a public endpoint.

A photo is also bounded **before a decoder sees it**. `pool_shape.MAX_PIXELS` is
checked from the file header by `image_size()`, which parses without an imaging
library, because bytes are not pixels: a PNG of 225 million white pixels is
246 KB compressed, clears any wire limit, and expands to ~675 MB of RGB in a
container limited to 512 MB. A rate limiter counts arrivals and bounds no work
in flight, so `_PHOTO_SLOTS` caps concurrent decodes at two and answers 503
beyond that — backpressure instead of a dead container.

### Text the page did not write is text, never markup

`/api/refresh` returns the **last line a collector printed**, falling back to
stderr. That line is routinely the vendor's own words — an error body from
Leslie's, a message from the WaterGuru API, the text of an exception quoting a
response — and all of it went into `msg.innerHTML` by string concatenation, on
`admin.html`, the build that holds the pool controls. Driving the pre-fix page
with a collector message of `<img src=x onerror=…>` fired the handler and put
two of those elements in the DOM.

It is `textContent` and `createTextNode` now rather than an escaping helper,
because there is no call left to forget, and the invariant is the **whole
block** rather than the two lines that happened to interpolate: the next field
added to a result would be rendered the way the lines beside it are. A detector
asserts the block contains no `innerHTML` at all, and a second one pins the
threat model by asserting `server.py` really does hand the page a collector's
own output — a rule whose reason has been deleted is a rule somebody will
delete next.

---

## Checking it

```bash
bin/render      # unfilled tokens, broken JSON, broken JS, and every check above
bin/selftest    # the pure functions, and the page's structural invariants
bin/contrast    # WCAG on every palette; non-zero on a failure
bin/drive       # both builds in a real browser at phone width
bin/drive --url https://poolhound.example.com/     # what is actually being served
bin/brand --check   # docs/poolhound*.svg, against the mark brand.py draws
```

**For anything visual or interactive, drive it in a browser.** A screenshot is
not enough and reading the source is not either. The whole series palette fell
back to black for several commits while every screenshot looked plausible; a
confirmation dialog warned about the opposite of the button pressed; the tab bar
rendered in the wrong typeface on every page for months. Each was found by
asking the browser what it actually had, and none by reading the code.
