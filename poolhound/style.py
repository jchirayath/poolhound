"""The stylesheet both pages share.

Kept in its own module so the dashboard and the chemistry reference cannot
drift apart visually, and so neither page's Python template has to escape a
brace. Colours are tokens on bare :root, redefined for dark in two guarded
blocks — the unstamped "system" state is the common case and must be the one
that works by default.
"""

# The one place the series palette is written down.
#
# CLAUDE.md: "The series palette is validated as a set ... Re-picking one in
# isolation means re-running the checker." It was written down twice in this
# file and a third time as C_PUMP/C_SPA/C_SHEER in render.py, which draws the
# SVG charts — so a re-pick that honoured the rule and changed the set here
# would still have left the charts on the old colours, silently, with every
# screenshot looking plausible. render.py imports these; the assertion below
# holds the CSS to them.
SERIES = {"pump": "#2a78d6", "spa": "#eb6834", "sheer": "#1baf7a"}

CSS = """/* Every colour is a token defined on bare :root, so the un-stamped
   system-default state renders correctly; the two blocks below redefine only
   the tokens, never the components. */
:root {
  --bg:#f4f6f8; --panel:#ffffff; --line:#dde3ea; --line2:#eef2f6;
  /* --ink3 was #667180, which is 4.37:1 on --nonebg — under the 4.5 floor its
     own 11px/600 pill text needs, and --nonebg is the ground for the whole
     off/unknown half of the equipment vocabulary (.st-off, .st-unknown,
     .p-over, .p-unknown, .grade.unknown). Nothing measured it, because the
     checker paired ink3 with --panel and --bg and never with --nonebg. #5f6a78
     is 4.85:1 there, and every other muted label it carries got darker with
     it (5.50:1 on a card, 5.08:1 on the page). */
  --ink:#16202b; --ink2:#4a5866; --ink3:#5f6a78;
  /* CVD-validated series palette — see README. Do not change one in
     isolation; the check is on the set, in both light and dark. */
  --pump:#2a78d6; --spa:#eb6834; --sheer:#1baf7a;
  /* The ink and the fill of a solid button, as a PAIR. They are deliberately
     theme-invariant — like the series palette, and for the same reason: the
     pair is validated against itself, not against the page, so a theme that
     redefined one half would be a second, unmeasured decision.
     --pump could not be used here. White on --pump is 4.42:1, under 4.5 for
     the 13.5px/600 label the primary button carries, and --pump cannot be
     darkened without re-picking the series, which is validated as a set. So
     the button fill is split off the series token: white on --accent is
     5.04:1, and the hover darkens rather than brightens so the label gets
     MORE contrast under the pointer, not less (brightness(1.08) took it back
     down to 4.42:1). Not repeated in the print block below because nothing
     redefines them, so there is nothing there to undo. */
  --accent:#2a6fc4; --btnink:#ffffff;
  --good:#197b4d; --warn:#996009; --bad:#b3261e;
  --goodbg:#e6f4ec; --warnbg:#fbf1de; --badbg:#fbeae8; --nonebg:#eef1f5;
  --band:rgba(42,120,214,.10); --aim:#78899e; --aimink:#5a6b80;
  --shadow:0 1px 2px rgba(16,32,48,.06);
  /* TWO LAYERS, because one is what makes a shadow read as a grey smudge: a
     tight contact shadow for the edge and a wide soft one for the lift. */
  --lift:0 1px 2px rgba(16,32,48,.07), 0 10px 28px -14px rgba(16,32,48,.22);

  /* ---- THE SHOWPIECE ----
     A deliberately dark surface for the one drawing that explains the whole
     system. Deep water, which is the subject; the architecture figure is
     drawn entirely in --panel/--ink/--line, so this block redefines those
     INSIDE itself and the drawing recolours with no change to the SVG.

     THEME-INVARIANT, like the series palette and the button pair and for the
     same reason: it is one measured decision rather than two, and a surface
     that exists to be the dark thing on the page has nothing to swap to in
     the dark. bin/contrast measures the two text pairings — a new ground with
     text on it that nothing measured is the defect its PAIRS list exists
     for. */
  --deep:#10242e; --deep-2:#173846;
  /* #2d5a6d until bin/contrast measured it at 2.13:1. The arrows and box
     strokes CARRY INFORMATION, so WCAG 1.4.11 asks 3:1 of them — and a stroke
     picked to look right on a dark ground is exactly the kind that lands
     under it. */
  --deep-line:#4a8099;
  --deepink:#e9f2f6; --deepink2:#a6c3d1;

  /* ---- TWO FAMILIES, AND THE SPLIT CARRIES INFORMATION ----
     If the pool said it, it is set in sans: every figure, unit, label,
     control, table cell and chart. If a person wrote it, it is set in serif:
     headings, the masthead line, and the explanatory prose this product is
     mostly made of. That is a rule a reader learns without being told, and it
     is the reason for a second family rather than a taste for one.

     NO WEB FONT, and not as a compromise. The policy is `default-src 'none'`
     with no font-src at all, so a hosted face would be refused by our own
     server and work everywhere else — the precise failure mode blob: was
     added to img-src to avoid. These are stacks of faces that are already on
     the machine; Charter and Iowan Old Style ship on macOS, Georgia is on
     Windows, and ui-serif lets a browser answer with its own. */
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
  --serif:ui-serif,Charter,"Bitstream Charter","Iowan Old Style",Georgia,serif;

  /* ---- THE TYPE SCALE ----
     It used to be h1 19, h2 15, h3 13, body 14 — a section heading ONE PIXEL
     larger than the paragraph under it. Nothing established rank, so forty
     cards on one page all spoke at the same volume and the page read as an
     internal tool whatever was on it. These are real intervals.

     --fig is a measurement and is deliberately the same size as --display:
     on an instrument the number IS the headline. */
  /* A HERO IS NOT A HEADING. The first screen of the public build opened
     with the product's one statement set at prose size in grey, under a 26px
     wordmark, above two stacked navigation bars — so a stranger met three
     bars before a sentence with any weight behind it. --lead is what that
     statement is set at, and is used in exactly one place.

     It is 27px and not the 44px this token held first, because the statement
     is a thirty-six-word sentence rather than a headline: 44px was for a short
     line above it, and that line repeated one of the three things listed
     beside it. See info.WHAT_IT_IS. */
  --lead:23px;
  --display:33px; --h2:21px; --h3:15.5px;
  --body:15px; --prose:15.5px; --meta:12.5px; --micro:11.5px;
  --fig:33px;

  /* ---- MEASURE ----
     Prose was capped at 66-76ch inside a 1040px box, so real text ran 380-480
     px wide and the card borders around it ran 1000 — the right half of every
     section was empty and the boxes advertised it. Prose now narrows its own
     CONTAINER rather than its line, and data keeps the full width. */
  --measure:68ch;

  /* ---- RADIUS, as a scale rather than a habit ----
     There were seven values in this file: 5, 6, 8, 9, 11 and 12 px, assigned
     by whichever rule was written that day. Three, by what the thing is. */
  --r-sm:6px; --r-md:10px; --r-lg:14px;

  /* A chapter break, which 30px of air was not. Narrowed on a phone, where
     the same gap is a third of the viewport and the reader is scrolling
     rather than scanning — see the media query at the end of this file. */
  --gap-section:52px;
}
@media (prefers-color-scheme:dark) {
  :root:not([data-theme="light"]) {
    --bg:#0f151c; --panel:#161e27; --line:#25313d; --line2:#1d2731;
    --ink:#e8eef5; --ink2:#a3b1c0; --ink3:#8792a0;
    --good:#5fd39a; --warn:#e5b95f; --bad:#f08b80;
    --goodbg:#12291f; --warnbg:#2b2415; --badbg:#2e1a18; --nonebg:#1c242e;
    --band:rgba(90,160,240,.13); --aim:#5a7089; --aimink:#9fb0c4;
    --shadow:0 1px 2px rgba(0,0,0,.4);
  }
}
:root[data-theme="dark"] {
  --bg:#0f151c; --panel:#161e27; --line:#25313d; --line2:#1d2731;
  --ink:#e8eef5; --ink2:#a3b1c0; --ink3:#8792a0;
  --good:#5fd39a; --warn:#e5b95f; --bad:#f08b80;
  --goodbg:#12291f; --warnbg:#2b2415; --badbg:#2e1a18; --nonebg:#1c242e;
  --band:rgba(90,160,240,.13); --aim:#5a7089; --aimink:#9fb0c4;
  --shadow:0 1px 2px rgba(0,0,0,.4);
}
* { box-sizing:border-box; }
/* The hidden attribute is display:none in the UA sheet, which ANY display rule
   in this file silently beats — .canvas-wrap being flex was enough to show an
   empty canvas before a photo was chosen. Stated once here so no future layout
   rule can quietly re-break it. */
[hidden] { display:none !important; }
body { margin:0; background:var(--bg); color:var(--ink);
  font:var(--body)/1.6 var(--sans);
  -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility; }
.wrap { max-width:1140px; margin:0 auto; padding:26px 20px 72px; }
/* Long-form prose, wherever it appears. One class rather than a max-width
   retyped on fourteen selectors, which is what this replaces. */
.measure { max-width:var(--measure); }

/* ---- header ---- */
/* One line: the mark and the name, then everything that acts on the page —
   freshness, refresh, theme, identity, and the overall verdict pinned right.
   The description moved out from beside the name and onto its own line below,
   because a subtitle inside an <h1> competes with the controls beside it. */
/* ---- the top bar ----
   One row that reads as a bar: the mark and the name, then everything that
   acts, pushed right. The actions were four pills of identical weight, so
   nothing said which one a visitor wants — `.btn.primary` on the sign-in
   settles it and the rest go quiet. */
header { display:flex; align-items:center; gap:10px; flex-wrap:wrap;
  padding-bottom:16px; border-bottom:1px solid var(--line); margin-bottom:10px; }
header h1 { margin-right:auto; }
header .icon-btn { border-color:transparent; background:none; }
header .icon-btn:hover { border-color:var(--line); background:var(--panel); }
/* THE ONE PLACE THIS PAGE RAISES ITS VOICE. The wordmark sat at 19px among
   four buttons of the same weight, so the first screen of an instrument said
   nothing with any emphasis at all. It is the only serif at display size and
   the only thing on the page that is. */
h1 { font-size:26px; margin:0; letter-spacing:-.02em; display:flex;
  align-items:center; gap:10px; font-family:var(--serif); font-weight:600; }
/* The mark carries its own three colours from brand.py, where it is defined;
   nothing here recolours it. */
h1 .mark { flex:none; }
.wordmark { font-weight:600; }
/* WHAT THIS IS FOR, at the size of a statement rather than a caption. It was
   12.5px grey under a 19px wordmark — three lines of the smallest text on the
   page carrying the only sentence that says what the product does. */
/* ---- the hero ----
   Statement left, the three things it does right, so the first screen uses
   the page rather than a column of it. Single column below 920px, where there
   is no second column to use. */
.hero { margin:6px 0 34px; display:grid; gap:10px 44px; align-items:start; }
/* The authenticated build's masthead. Same three pieces, a third of the room:
   an operator arriving at the dashboard is not being introduced to the
   product, and the first reading should be near the top of the page. */
.hero.compact { margin:0 0 20px; }
.hero.compact .tagline { font-size:var(--prose); font-weight:600;
  line-height:1.4; margin:0; color:var(--ink2); }
.hero.compact .does { display:none; }

/* THE LEAD IS THE HEADLINE. There is no line above it: the one that used to be
   there restated the second of the three things below it, so the first screen
   promised the same thing twice. See info.WHAT_IT_IS. A thirty-six-word
   sentence cannot be set at 44px, so it is set at a size that is still clearly
   the opening statement and takes four lines rather than nine. */
.hero:not(.compact) .tagline { font-size:var(--lead); line-height:1.42;
  letter-spacing:-.012em; color:var(--ink); margin:2px 0 14px;
  max-width:none; }
@media (min-width:920px) {
  /* Explicit placement: the lead holds the left column, the three things it
     does hold the right one. */
  .hero:not(.compact) { grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);
    gap:0 56px; margin-bottom:46px; align-items:start; }
  .hero:not(.compact) .tagline { grid-column:1; grid-row:1;
    margin:0; max-width:38ch; }
  .hero .does { grid-column:2; grid-row:1; margin:4px 0 0; gap:11px; }
  .hero .does li { font-size:14.5px; }
}
.tagline { margin:2px 0 12px; font-family:var(--serif); font-size:var(--prose);
  line-height:1.55; color:var(--ink2); max-width:var(--measure); }
/* AND THE THREE, AS THREE. They were clauses of the sentence above, which
   made three separate promises read as one long qualification of each other.
   A list is the shape of "these are distinct", and it costs the masthead
   about one line of height to say so. The glyph is decoration -- the whole
   claim is in the words beside it -- so it is aria-hidden by icon(). */
.does { margin:0 0 26px; padding:0; list-style:none; display:grid; gap:7px;
  max-width:var(--measure); }
.does li { display:flex; align-items:flex-start; gap:9px; font-size:14px;
  font-family:var(--serif); line-height:1.5; color:var(--ink2); }
.does li svg { flex:0 0 auto; margin-top:2px; color:var(--ink3); }
@media (max-width:520px) { .does li { font-size:13.5px; } }
.age { color:var(--ink3); font-size:var(--meta); }
.grade { margin-left:auto; font-size:12px; font-weight:600; letter-spacing:.06em;
  text-transform:uppercase; padding:4px 11px; border-radius:99px; }
.grade.green,.grade.ok { background:var(--goodbg); color:var(--good); }
.grade.yellow { background:var(--warnbg); color:var(--warn); }
.grade.red { background:var(--badbg); color:var(--bad); }
.grade.unknown { background:var(--nonebg); color:var(--ink3); }
.alerts { background:var(--warnbg); color:var(--warn); border-radius:8px;
  padding:9px 13px; font-size:13px; margin:-6px 0 22px; }
.alerts b { margin-right:7px; }
.navlink { font-size:12.5px; color:var(--ink2); text-decoration:none;
  border-bottom:1px solid var(--line); }
.navlink:hover { color:var(--ink); border-color:var(--ink3); }
.sub a, footer a { color:var(--ink2); }
a:focus-visible, summary:focus-visible, .dot:focus-visible {
  outline:2px solid var(--pump); outline-offset:2px; border-radius:3px; }

/* ---- tiles ---- */
.tiles { display:grid; gap:12px; margin-bottom:var(--gap-section);
  grid-template-columns:repeat(auto-fit,minmax(172px,1fr)); }
.tile { background:var(--panel); border:1px solid var(--line);
  border-radius:var(--r-md); padding:15px 16px 14px; }
/* SENTENCE CASE, WEIGHT INSTEAD OF CAPS. "FREE CHLORINE" tracked out at
   11.5px is harder to read than "Free chlorine" at 12.5, and an instrument
   that shouts its labels is competing with its own numbers. */
.tl { font-size:var(--meta); font-weight:560; color:var(--ink3);
  letter-spacing:0; text-transform:none; }
.tv { font-size:var(--fig); font-weight:640; letter-spacing:-.025em;
  margin:4px 0 7px; font-variant-numeric:tabular-nums; line-height:1.05; }
.tu { font-size:15px; font-weight:400; color:var(--ink3); margin-left:3px; }
.tb { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.pill { font-size:11px; font-weight:600; padding:2px 7px; border-radius:5px; text-transform:capitalize; }
.p-ok { background:var(--goodbg); color:var(--good); }
.p-low,.p-high { background:var(--warnbg); color:var(--warn); }
/* 'over' is above target and not a fault, so it must not wear the warning
   colour that 'high' and 'low' do — putting surplus chlorine in the same voice
   as calcium eating the plaster teaches the reader to discount both. */
.p-over { background:var(--nonebg); color:var(--ink2); }
.c-over { background:var(--nonebg); color:var(--ink2); }
/* A fault, not a warning. "vault unreachable" wore .p-high -- amber, the
   "somewhat off target" voice -- two lines above a red .fmsg.bad sentence
   about the same failure, so one fact spoke in two colours and the quieter one
   won. It is a fault: the credential store cannot be read, so what the page
   says about every secret below it is unverified. --bad/--badbg are already
   measured by bin/contrast's PAIRS. */
.p-bad { background:var(--badbg); color:var(--bad); }
.p-unknown { background:var(--nonebg); color:var(--ink3); }
.tt { font-size:11.5px; color:var(--ink3); font-variant-numeric:tabular-nums; }
.tn { font-size:11.5px; color:var(--ink3); margin-top:7px;
  padding-top:7px; border-top:1px solid var(--line2); }
.tsrc { font-size:10.5px; color:var(--ink3); margin-top:5px; letter-spacing:.02em; }
.srclegend { display:flex; gap:18px; font-size:12px; color:var(--ink2);
  margin:0 0 11px; flex-wrap:wrap; }
.srclegend span { display:inline-flex; align-items:center; gap:6px; }
.refmark { fill:none; stroke:var(--ink2); stroke-width:1.8; }
.th2 { font-weight:400; text-transform:none; letter-spacing:0; color:var(--ink3);
  font-size:10.5px; }
.gap-wide { color:var(--warn); font-weight:600; }
.labgrid { display:grid; gap:16px 18px;
  grid-template-columns:repeat(auto-fit,minmax(258px,1fr)); }
.lab-mini h4 { margin:0; font-size:12.5px; color:var(--ink); }
.lm-h { display:flex; align-items:center; gap:8px; margin-bottom:2px; }
.labline { fill:none; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }
/* Each lab is told apart three ways at once — hue, dash and mark shape — so the
   panels stay readable in greyscale, in print and to a colourblind reader. */
.labline.s-wg  { stroke:var(--pump); }
.labline.s-les { stroke:var(--ink2); stroke-dasharray:5 3; }
/* A third source needs a third way to be told apart that is not only hue: the
   spa orange, a dotted dash, and its own mark shape in the chart code. */
.labline.s-man { stroke:var(--spa); stroke-dasharray:2 3; }
.lastlbl.s-wg  { fill:var(--pump); }
.lastlbl.s-les { fill:var(--ink2); }
.lastlbl.s-man { fill:var(--spa); }
.lastlbl.sm { font-size:11.5px; }
.gap-ok { color:var(--ink3); }
.rel { color:var(--ink3); font-weight:400; margin-left:7px; font-size:11px; }

/* ---- icons, sub-navigation, refresh ---- */
.tabs button { gap:6px; display:inline-flex; align-items:center; }
.tabs button svg { opacity:.75; }
.tabs button[aria-selected="true"] svg { opacity:1; }
h2 { display:flex; align-items:center; gap:10px; }
/* The section glyph as a marker rather than as decoration: on its own tile it
   reads as the start of something, which is the job a heading icon is doing
   here. content-box, so the 17px the icon was asked for stays 17px. */
h2 .h-ico { color:var(--ink2); flex:none; box-sizing:content-box; padding:5px;
  border-radius:8px; background:var(--line2); border:1px solid var(--line); }

/* Sticky, because its whole job is to be reachable from the middle of a long
   panel — a jump-list you have to scroll back up to find is a table of
   contents, not navigation. */
.subnav { position:sticky; top:0; z-index:6; display:flex; gap:2px; flex-wrap:wrap;
  padding:10px 0 11px; margin:-6px 0 26px; background:var(--bg);
  border-bottom:1px solid var(--line); }
/* SUBORDINATE, AND SAYING SO. No pill, no border, no ground until you point
   at it: a jump-list inside a panel is not a peer of the bar that chose the
   panel. The current item is marked by ink, weight and a rule under it —
   three channels, none of them colour alone, like every other state here. */
.subnav a { display:inline-flex; align-items:center; gap:6px; font-size:var(--meta);
  color:var(--ink3); text-decoration:none; padding:6px 9px;
  border-radius:var(--r-sm); white-space:nowrap;
  border:0; box-shadow:inset 0 -2px 0 transparent; }
.subnav a:hover { color:var(--ink); background:var(--line2); }
.subnav a[aria-current="true"] { color:var(--ink); font-weight:620;
  box-shadow:inset 0 -2px 0 var(--pump); }
.subnav a svg { opacity:.7; flex:none; }
/* THE WEIGHT CHANGE MUST NOT CHANGE THE WIDTH. Bolder is wider, the list wraps,
   and the observer that marks the current section watches the content this
   list sits above: at its wrap point, marking a section pushed the list onto a
   second line, the content moved, the observer chose another section, and the
   list unwrapped -- a flicker at thirty frames a second, at the top of the
   landing page. Each label carries an invisible bold copy of itself in the same
   grid cell, so it is as wide as its bold self whether current or not.
   visibility:hidden keeps the copy out of the accessibility tree; bin/drive
   checks every link's width both ways. NOT overflow:hidden on the copy: a
   grid item with it may shrink to nothing, so in a list too long for its row
   the copy held no width at all and the check still failed. */
.subnav a span { display:inline-grid; }
.subnav a span::after { content:attr(data-label); font-weight:620;
  visibility:hidden; height:0; }
@media (max-width:640px) { .subnav { overflow-x:auto; flex-wrap:nowrap; } }

.refresh-wrap { position:relative; }
.icon-btn.refresh { gap:6px; font-size:12.5px; padding:5px 10px; }
.refresh-menu { position:absolute; right:0; top:calc(100% + 7px); z-index:20;
  width:330px; max-width:80vw; background:var(--panel); border:1px solid var(--line);
  border-radius:11px; box-shadow:var(--lift); padding:12px; }
.refresh-menu > p { font-size:12px; color:var(--ink3); margin:0 0 9px; }
.rsrc { display:flex; align-items:flex-start; gap:9px; width:100%; text-align:left;
  background:none; border:0; border-radius:8px; padding:8px 9px; cursor:pointer;
  font:inherit; color:var(--ink); }
.rsrc:hover { background:var(--bg); }
.rsrc[disabled] { opacity:.5; cursor:default; }
.rsrc svg { margin-top:2px; color:var(--ink3); flex:none; }
.rsrc span { display:flex; flex-direction:column; gap:1px; }
.rsrc b { font-size:13px; font-weight:600; }
.rsrc i { font-style:normal; font-size:11.5px; color:var(--ink3); }
.rsrc.all { border-top:1px solid var(--line2); margin-top:4px; padding-top:10px;
  border-radius:0 0 8px 8px; }
.rmsg { font-size:12px; color:var(--ink2); padding:4px 9px 0; }
.rmsg:empty { display:none; }
.rline { padding:3px 0; }
.rline.ok b { color:var(--good); }
.rline.bad { color:var(--bad); }
.rline i { font-style:normal; color:var(--ink3); }
.spin { display:inline-block; width:11px; height:11px; margin-right:7px;
  border:2px solid var(--line); border-top-color:var(--pump); border-radius:50%;
  animation:spin .7s linear infinite; vertical-align:-1px; }
@keyframes spin { to { transform:rotate(360deg); } }
@media (prefers-reduced-motion:reduce) { .spin { animation:none; } }

/* ---- sections ----
   A PANEL IS A STACK OF SECTIONS AND NOTHING SAID WHERE ONE ENDED. The
   chemistry tab runs to eleven of them; the only boundary was 34px of air,
   which at a glance is indistinguishable from the air between a heading and
   its own paragraph. A rule between siblings costs one line and makes the
   structure legible without adding a box around everything — the cards inside
   are already boxes, and a box inside a box flattens the hierarchy it was
   meant to express. */
section { margin-bottom:var(--gap-section); }
section + section { padding-top:var(--gap-section); border-top:1px solid var(--line); }
/* The sub-navigation is sticky, so an anchor that scrolls a heading to y=0
   scrolls it UNDERNEATH the thing that was clicked to get there. Every jump in
   the jump-list landed one heading too far down. */
section[id] { scroll-margin-top:58px; }
h2 { font-size:var(--h2); margin:0 0 6px; letter-spacing:-.012em;
  font-family:var(--serif); font-weight:600; line-height:1.25; }
/* The line under a heading, and it belongs to the heading: serif, because it
   is a sentence somebody wrote, and bonded tightly above the content it
   introduces. */
.sub { font-family:var(--serif); font-size:var(--prose); line-height:1.6;
  color:var(--ink2); margin:0 0 18px; max-width:var(--measure); }
/* A CARD IS A DISCRETE OBJECT, not a wrapper for anything that needs air.
   Flat, with a hairline: a shadow under every one of forty boxes is the
   look this page had, and it flattens the hierarchy a shadow exists to
   express. --lift is kept for the things that genuinely float — a menu, a
   dialog — and for the one advisory that is a fault. */
.card { background:var(--panel); border:1px solid var(--line);
  border-radius:var(--r-md); padding:17px 18px; }
/* A CARD TITLE IS A SENTENCE SOMEBODY WROTE, so it is set in the serif like
   every other heading. The alternative was serif for the page and section
   levels and sans for object titles — defensible, and it leaves a sans
   heading sitting on top of serif prose inside the same small box, which
   reads as two decisions rather than one rule. */
.card > h3 { font-family:var(--serif); font-size:var(--h3); margin:0 0 7px;
  font-weight:600; letter-spacing:-.005em; }
.two { display:grid; gap:14px; grid-template-columns:repeat(auto-fit,minmax(330px,1fr)); }
.ch-h { display:flex; align-items:baseline; gap:9px; margin-bottom:6px; }
.ch-h h3 { font-family:var(--serif); font-size:var(--h3); margin:0; font-weight:600; }

/* ---- charts ---- */
.chart { width:100%; height:auto; display:block; overflow:visible; }
.band { fill:var(--band); }
/* --aim is the GRAPHICAL accent and nothing else: a dashed guide line in the
   architecture diagrams, where SC 1.4.11's 3:1 applies. It was also the colour
   of every link on Help, the sign-out link and the volume total -- TEXT, where
   the floor is 4.5:1, and it measured 2.59 light and 2.60 dark. One token doing
   two jobs with two different floors passes whichever check it is given and
   fails the other; --aimink is the text half, and bin/contrast now measures it.
   Same split, and the same reason, as --accent/--btnink off --pump. */
.aim { stroke:var(--aim); stroke-width:1; stroke-dasharray:3 4; }
.aim-lbl { fill:var(--ink3); font-size:10px; }
.ytick,.xtick { fill:var(--ink3); font-size:10px; font-variant-numeric:tabular-nums; }
.line { fill:none; stroke:var(--pump); stroke-width:2; stroke-linecap:round; stroke-linejoin:round; }
.dot { fill:var(--pump); stroke:var(--panel); stroke-width:2; }
.lastlbl { fill:var(--ink); font-size:12.5px; font-weight:640; font-variant-numeric:tabular-nums; }

/* ---- day strips ---- */
.legend { display:flex; gap:15px; flex-wrap:wrap; align-items:center;
  font-size:12px; color:var(--ink2); margin-bottom:11px; }
.legend i, .run i { width:10px; height:10px; border-radius:3px; display:inline-block;
  vertical-align:-1px; margin-right:5px; }
.hours { display:grid; grid-template-columns:64px 1fr 168px; gap:10px;
  font-size:10px; color:var(--ink3); font-variant-numeric:tabular-nums;
  padding:0 0 4px; }
.hours div:nth-child(2) { display:flex; justify-content:space-between; }
.drow { display:grid; grid-template-columns:64px 1fr 168px; gap:10px; align-items:center;
  padding:7px 0; border-top:1px solid var(--line2); }
.drow:first-of-type { border-top:1px solid var(--line); }
.dwhen { display:flex; flex-direction:column; line-height:1.25; }
.dd { font-size:12.5px; font-weight:640; }
.dm { font-size:11px; color:var(--ink3); }
.strip { width:100%; height:35px; display:block; }
.lane { fill:var(--line2); }
.hgrid { stroke:var(--line); stroke-width:1; }
.chemmark { fill:var(--ink2); }
.dmeta { display:flex; gap:11px; flex-wrap:wrap; font-size:11.5px; color:var(--ink2);
  font-variant-numeric:tabular-nums; grid-column:2; margin-top:-2px; }
.dchips { display:flex; gap:6px; justify-content:flex-end; grid-row:1; grid-column:3; }
.chip { font-size:12px; padding:3px 8px; border-radius:6px; font-variant-numeric:tabular-nums; }
.chip b { font-weight:600; margin-right:5px; opacity:.72; }
.c-ok { background:var(--goodbg); color:var(--good); }
.c-low,.c-high { background:var(--warnbg); color:var(--warn); }
.c-none,.c-unknown { background:var(--nonebg); color:var(--ink3); }
.swg { color:var(--ink3); }

/* ---- tables ---- */
table { border-collapse:collapse; width:100%; font-size:12.5px; }
th { text-align:left; font-weight:600; color:var(--ink3); font-size:11px;
  text-transform:uppercase; letter-spacing:.05em; padding:6px 10px 6px 0;
  border-bottom:1px solid var(--line); white-space:nowrap; }
td { padding:6px 10px 6px 0; border-bottom:1px solid var(--line2); color:var(--ink2);
  white-space:nowrap; }
td.n { font-variant-numeric:tabular-nums; }
.raw td, .raw th { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11.5px; }
/* A WIDE TABLE SCROLLS ITSELF; THE PAGE MUST NOT.
   overflow-x:auto alone does not stop a fixed min-width child from growing the
   BLOCK size of this box, so table.mx's min-width:640px propagated out to the
   document and the whole page scrolled sideways 251px at 390px wide —
   masthead, theme button and the entire tab bar sliding off with it, leaving
   the first tab unreachable without scrolling back. Measured on the LIVE
   public Pool chemistry tab, which is the tab a phone opens most.
   min-width:0 lets this box shrink below its content; max-width:100% stops it
   exceeding its parent. Together they keep the overflow inside, which is what
   overflow-x:auto was always meant to achieve here. */
/* A WIDE TABLE SCROLLS ITSELF; THE PAGE MUST NOT.
   position:relative is the load-bearing part. The visually-hidden .sr spans
   inside these tables are position:absolute with no positioned ancestor, so
   their containing block was the initial one — they escaped this box entirely
   and sat at document x-coordinates up to 641px, which is exactly the width
   the page then scrolled to. clip:rect() hides the PAINT and does nothing
   about the layout box. Making this the containing block puts them back inside,
   where overflow-x clips them.
   Measured at 390px on the live public Pool chemistry tab: the document
   scrolled 251px sideways, carrying the masthead, the theme button and the
   whole tab bar off-screen and leaving the first tab unreachable. body was
   390px throughout — the overflow was never in the flow, which is why it
   survived every attempt to fix it by constraining the table. */
.scroll { overflow-x:auto; margin-bottom:20px; min-width:0; max-width:100%;
          position:relative; }
h4 { font-size:12px; margin:16px 0 7px; color:var(--ink2); }
details { background:var(--panel); border:1px solid var(--line); border-radius:11px;
  padding:12px 16px; box-shadow:var(--shadow); }
summary { cursor:pointer; font-size:13px; font-weight:600; }
summary::marker { color:var(--ink3); }
.empty { color:var(--ink3); font-size:12.5px; margin:6px 0; }
code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px;
  background:var(--line2); padding:1px 5px; border-radius:4px; }
footer { color:var(--ink3); font-size:11.5px; border-top:1px solid var(--line);
  padding-top:13px; margin-top:34px; display:flex; gap:14px; flex-wrap:wrap; }

#tip { position:fixed; pointer-events:none; opacity:0; transition:opacity .1s;
  background:var(--panel); border:1px solid var(--line); box-shadow:var(--shadow);
  border-radius:7px; padding:6px 9px; font-size:12px; z-index:9;
  font-variant-numeric:tabular-nums; }
#tip b { display:block; color:var(--ink3); font-weight:500; font-size:11px; }

@media (max-width:640px) {
  .hours, .drow { grid-template-columns:56px 1fr; }
  .dchips { grid-row:auto; grid-column:2; justify-content:flex-start; }
  .hours div:nth-child(3) { display:none; }
}
@media (prefers-reduced-motion:reduce) { * { transition:none !important; } }

/* ---- print ----
   A printed copy is what gets carried to the pool shed or the store, so the
   page furniture goes and every tab prints, not just the one on screen. */
@media print {
  /* Paper is light, whatever the screen was. Every component here takes its
     colour from the tokens, and only `body` was being forced back to white — so
     a reader printing from a dark browser got the DARK palette's greys on white
     paper: #a3b1c0 body text, near-invisible hairlines, cards with no contrast
     against the page. The whole light set is restored here, not just the two
     properties on body, because a token that is not redefined keeps whatever
     the theme left in it.

     This block wins on specificity of medium, not of selector, so it has to
     repeat the values rather than reference :root. Re-pick the series palette
     as a set and this copy has to move with it — the single copy lives in the
     bare :root block above. */
  :root, :root[data-theme="dark"], :root:not([data-theme="light"]) {
    --bg:#ffffff; --panel:#ffffff; --line:#bbbbbb; --line2:#dddddd;
    --ink:#000000; --ink2:#333333; --ink3:#555555;
    --pump:#2a78d6; --spa:#eb6834; --sheer:#1baf7a;
    --good:#197b4d; --warn:#996009; --bad:#b3261e;
    --goodbg:#e6f4ec; --warnbg:#fbf1de; --badbg:#fbeae8; --nonebg:#eef1f5;
    --band:rgba(42,120,214,.10); --aim:#78899e; --aimink:#5a6b80; --shadow:none;
  }
  .tabs, .skip, .icon-btn, .factions, .offline-note, #tip, .form, .badge,
  .dt-more { display:none !important; }
  /* PAPER DOES NOT SCROLL. This block opened every tab and restored the
     palette, then printed a data table cropped to 430px of its rows with the
     overflow silently gone, and the two diagrams cut off at the edge of the
     sheet — each of them inside an overflow box sized for a phone. CLAUDE.md:
     never truncate without saying so. data_table()'s honest count ("showing
     the most recent 40 of 394", in .dt-bar, which prints) still tells the
     reader what is missing; what it must not do is claim 40 and then print 12.
     The diagrams drop their min-width so they scale to the page instead of
     being guillotined at whatever the margin box allows. */
  .dt-scroll { max-height:none !important; overflow:visible !important; }
  .dtable, .dtable table.raw, .scroll, pre.cmd, .offline-note pre,
  figure.wiring, figure.arch { overflow:visible !important; }
  .dtable table.raw th { position:static; }
  /* !important throughout, because this block sits near the TOP of the file
     and the rules it has to undo are appended hundreds of lines below it — on
     equal specificity the later one wins, so a plain `min-width:0` here was
     overruled by `figure.arch.wide svg { min-width:1240px }` and measured, in
     a browser, as still 1240px with the print block live. */
  figure.wiring svg, figure.arch svg, figure.arch.wide svg,
  figure.arch.glance svg { min-width:0 !important; }
  .panel[hidden] { display:block !important; }
  .panel { break-before:page; }
  .panel:first-of-type { break-before:auto; }
  body { background:#fff; color:#000; }
  .card, .tile, .fac, .actions li { border:1px solid #bbb; box-shadow:none;
    break-inside:avoid; }
  .wrap { max-width:none; padding:0; }
  details { display:block; }
  details > summary { display:none; }
  a[href^="http"]::after { content:" (" attr(href) ")"; font-size:10px; color:#555; }
}

/* ---- tabs ----
   A RAISED BAR RATHER THAN A ROW OF WORDS ON A HAIRLINE. Nine items sharing
   one underline read as one undifferentiated strip: the only thing separating
   "Settings" from "Poolhound info" was a space, and the only thing marking the
   current one was two pixels of blue at the bottom of the page's own edge. The
   bar now sits on the panel colour with its own border, each item is a target
   with its own hover ground, and the selected one is filled as well as
   underlined and bolded.

   NOT COLOUR ALONE, which is the rule everywhere in this product: the selected
   tab carries a fill, a weight change AND the accent rule, on top of the
   aria-selected the markup already had. */
/* TWO BARS OF THE SAME VOICE, TEN PIXELS APART. This one and .subnav were
   near-identical pill rows stacked on top of each other, so neither read as
   the senior of the two and the second one looked like more of the first.
   This is the louder: it sits on the panel in its own rail. .subnav below
   gives up its container and becomes quiet text. */
.tabs { display:flex; align-items:stretch; flex-wrap:wrap; gap:1px;
  margin:-2px 0 30px; padding:5px; background:var(--panel);
  border:1px solid var(--line); border-radius:var(--r-lg); }
.tabs button { appearance:none; background:none; border:0; cursor:pointer;
  font:inherit; font-size:13.5px; color:var(--ink3); padding:9px 9px;
  white-space:nowrap; border-radius:var(--r-sm); }
/* TEN TABS IN 1098px OF RAIL. Measured at 1159px of buttons, so it wrapped
   and left the second row as a void inside a bordered box. Stretching the
   buttons to fill each row was tried and is worse: it centres a lone "Help"
   under nine siblings, which reads as a mistake rather than as a menu.
   Tightening the metrics is the fix, and the slack has to be real rather than
   exact — the SELECTED tab is bolder than the others, so at 1098 against 1098
   the bar reflowed when you changed tab, and which tab you were on decided
   whether the menu was one row or two. */
.tabs button svg { margin-right:-1px; }
.tabs button:hover { color:var(--ink); background:var(--line2); }
.tabs button[aria-selected="true"] { color:var(--ink); font-weight:600;
  background:var(--band); box-shadow:inset 0 -2px 0 var(--pump); }
.tabs button:focus-visible { outline:2px solid var(--pump); outline-offset:-2px; }
/* The divider between the group that operates the pool and the group that
   explains it. Emitted by render.order_tabs(), which is where the grouping is
   decided, and aria-hidden, so the tablist has only tabs in it as far as
   assistive technology is concerned. */
.tabsep { flex:none; align-self:center; width:1px; height:20px; margin:0 5px;
  background:var(--line); }
/* WRAP ON A DESKTOP, SCROLL ON A PHONE. Nine tabs never fit one 390px line,
   and wrapping them there makes the bar four rows tall before the page has
   said anything — so below the breakpoint it becomes a scroller, which is
   exactly what .subnav does directly above and for the same reason. Above it,
   wrapping is right: a row that quietly runs off the edge of a wide screen is
   a menu whose last item nobody finds. */
/* THE RAIL IS AS WIDE AS ITS TABS. The public build has four of them, and a
   full-width bordered box holding four items and 700px of nothing reads as a
   container that failed to fill rather than as a menu. Desktop only: below
   the breakpoint this is a scroller and must keep the page's width. */
@media (min-width:641px) {
  .tabs { width:max-content; max-width:100%; }
}
@media (max-width:640px) {
  .tabs { flex-wrap:nowrap; overflow-x:auto; }
  .tabsep { margin:0 4px; }
}

/* ---- forms ---- */
.form { display:flex; flex-direction:column; gap:13px; }
.frow { display:flex; gap:12px; flex-wrap:wrap; align-items:flex-end; }
.f { display:flex; flex-direction:column; gap:5px; flex:1 1 190px; min-width:0; }
.f.f-sm { flex:0 1 132px; }
.f.f-wide { flex:2 1 300px; }
.f > span { font-size:11.5px; text-transform:uppercase; letter-spacing:.06em;
  color:var(--ink3); }
.f > span i { font-style:normal; text-transform:none; letter-spacing:0; opacity:.7; }
input[type=text], input[type=number], input[type=datetime-local],
input[type=month], select {
  font:inherit; font-size:13.5px; color:var(--ink); background:var(--panel);
  border:1px solid var(--line); border-radius:8px; padding:8px 10px; width:100%;
  font-variant-numeric:tabular-nums; }
input:focus, select:focus { outline:2px solid var(--pump); outline-offset:-1px;
  border-color:var(--pump); }
.check { display:flex; gap:9px; align-items:flex-start; font-size:13px;
  color:var(--ink2); cursor:pointer; }
.check input { margin-top:2px; }
.fh { font-size:12px; text-transform:uppercase; letter-spacing:.06em;
  color:var(--ink3); margin:8px 0 -4px; padding-top:13px;
  border-top:1px solid var(--line2); }
.btn { font:inherit; font-size:13.5px; font-weight:600; border-radius:8px;
  padding:9px 17px; cursor:pointer; border:1px solid var(--line);
  background:var(--panel); color:var(--ink); }
/* The ink is a TOKEN pair with its fill, not a literal white on a series
   colour. This read `background:var(--pump); color:#fff` — 4.42:1, under the
   4.5 the 13.5px/600 label needs, on Calculate, Save and Log this dose, in all
   four palettes at once because --pump is the same value in every one of them.
   The identical mistake had already been found and fixed one rule away, at
   .ctl[data-state="on"] .ctl-on. --accent/--btnink is 5.04:1; see :root. */
.btn.primary { background:var(--accent); border-color:var(--accent);
  color:var(--btnink); }
/* Darker, not brighter: brightening the fill pushed the label back under 4.5. */
.btn.primary:hover { filter:brightness(.92); }
.btn:focus-visible { outline:2px solid var(--pump); outline-offset:2px; }
.factions { display:flex; align-items:center; gap:13px; flex-wrap:wrap;
  padding-top:4px; }
/* READ-ONLY CONTROLS, AND THEY MUST NOT BE .factions.
   That class is hidden outright by `body.no-api .factions .btn:not(.pure)` and
   held at visibility:hidden until the write-token check resolves -- correct for
   anything that writes, and wrong for a filter and a pager, which only change
   what this reader is looking at. In .factions they would disappear for every
   view-only account and for the first moment of every page load, so the log
   would be unfilterable for exactly the people who can do nothing but read it. */
.frow { display:flex; align-items:center; gap:10px; flex-wrap:wrap;
  padding-top:4px; }
/* The by-month table's period picker, and the two rows that total it. The
   controls sit on their bottom edge so a label that wraps does not lift its
   box out of line with the others. */
.dm-ctl { display:flex; gap:12px; flex-wrap:wrap; align-items:flex-end;
  margin:0 0 6px; }
.dm-ctl .f { margin:0; flex:0 1 172px; }
/* Figures that are summed are read down a column, so they line up on the
   right, and the headings over them follow. */
#dm-table td.n, #dm-table thead th + th, #dm-table tfoot td { text-align:right; }
#dm-table tfoot th, #dm-table tfoot td { font-weight:600;
  border-top:1px solid var(--line); }
#dm-table tfoot tr + tr th, #dm-table tfoot tr + tr td { border-top:0; }
.flbl { font-size:11.5px; text-transform:uppercase; letter-spacing:.06em;
  color:var(--ink3); }
/* `select` is width:100% for the forms, which in a flex row is every control
   fighting for the whole line. */
.fsel { width:auto; max-width:100%; padding:6px 9px; font-size:13px; }
/* A DISABLED BUTTON HAS TO LOOK DISABLED. There was no rule at all, so the
   pager's Newer button on page one was fully inked, took focus, and did
   nothing when pressed -- a control that reports the opposite of its state. */
.btn:disabled { opacity:.45; cursor:default; }
.fmsg { font-size:12.5px; color:var(--ink3); }
.fmsg.ok { color:var(--good); }
.fmsg.bad { color:var(--bad); }
.blurb { font-family:var(--serif); font-size:14.5px; line-height:1.6;
  color:var(--ink2); margin:0; max-width:var(--measure); }

/* The two modes are mutually exclusive and neither should flash before the
   health check answers, so both are hidden until the body carries a class. */
.offline-note { display:none; font-size:12.5px; color:var(--ink2);
  background:var(--bg); border-radius:9px; padding:12px 14px; }
.offline-note p { margin:0 0 8px; }
.offline-note pre { margin:0; font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:12px; background:var(--panel); border:1px solid var(--line);
  border-radius:6px; padding:8px 10px; overflow-x:auto; }
body.no-api .offline-note { display:block; }
/* :not(.pure) -- THE CALCULATOR IS NOT A WRITE.
   This hid EVERY button in a .factions row when the page had no write token,
   which on the public deployment is every visitor. Three of those buttons are
   "Calculate the volume", "Calculate the volume" (traced) and "Calculate the
   spa", and they POST to /api/pool-shape/compute and /api/photo -- the two
   routes this product deliberately leaves public because they are pure: they
   read nothing, write nothing and keep nothing. So the Pool Volume Calculator,
   one of the four public tabs and the headline reason to visit the site at all,
   rendered as a form with no way to submit it and a note reading "Start it with
   bin/serve" -- an instruction addressed to a developer, shown to the public.
   Verified in a browser against the live site: display:none on all three.
   What genuinely needs the write API is SAVING the answer as this pool's
   volume, and those buttons are still hidden. */
body.no-api .factions .btn:not(.pure) { display:none; }
body:not(.has-api):not(.no-api) .factions { visibility:hidden; }

/* ---- dose preview ---- */
.preview { display:flex; gap:16px; flex-wrap:wrap; align-items:baseline;
  background:var(--bg); border-radius:9px; padding:12px 14px; }
.preview .pl { font-size:11.5px; text-transform:uppercase; letter-spacing:.06em;
  color:var(--ink3); }
.preview .ph { font-size:13px; color:var(--ink3); }
.eff { display:inline-flex; align-items:baseline; gap:6px; font-size:12.5px;
  color:var(--ink2); }
.eff b { font-size:16px; color:var(--ink); font-variant-numeric:tabular-nums;
  font-weight:640; }

/* ---- skip link, icon button, help ---- */
.skip { position:absolute; left:-9999px; top:0; background:var(--panel); color:var(--ink);
  padding:9px 14px; border-radius:0 0 8px 0; z-index:20; font-size:13px;
  border:1px solid var(--line); }
.skip:focus { left:0; }
.icon-btn { appearance:none; background:none; border:1px solid var(--line);
  border-radius:7px; color:var(--ink3); cursor:pointer; padding:5px 7px;
  display:inline-flex; align-items:center; }
.icon-btn:hover { color:var(--ink); border-color:var(--ink3); }
.icon-btn:focus-visible { outline:2px solid var(--pump); outline-offset:2px; }
[data-help] { border-bottom:1px dotted var(--ink3); cursor:help; }
.sr { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0);
  white-space:nowrap; }
td.rm { width:1%; text-align:right; padding-right:0; }
button.x { appearance:none; background:none; border:0; cursor:pointer; color:var(--ink3);
  font-size:17px; line-height:1; padding:2px 7px; border-radius:6px; }
button.x:hover { color:var(--bad); background:var(--badbg); }
button.x:focus-visible { outline:2px solid var(--pump); outline-offset:1px; }
button.x[disabled] { opacity:.4; cursor:default; }
/* Deleting is only offered when there is somewhere to send the request. */
body:not(.has-api) td.rm { display:none; }
body:not(.has-api) th:last-child .sr { display:none; }

/* ---- action list ---- */
.actions { list-style:none; margin:0; padding:0; display:flex; flex-direction:column;
  gap:10px; counter-reset:act; }
.actions li { background:var(--panel); border:1px solid var(--line);
  border-radius:var(--r-md); padding:16px 18px; border-left-width:3px; }
.actions li.act-do  { border-left-color:var(--warn); }
/* A FAULT IS MARKED, NOT LIFTED. A shadow was tried here and the reason it is
   gone is worth the line: when every advisory is a fault — which is the state
   of a pool nobody has looked at, and so the common first screen — three
   lifted boxes in a column is not emphasis, it is noise. The rail, the stamp
   and the heavier heading are three channels and none of them is a shadow.
   --lift stays for the things that genuinely float: the refresh menu, the
   dialog. */
.actions li.act-fix { border-left-color:var(--bad); border-left-width:4px; }
.actions li.act-fix .a-h h3 { font-weight:700; }
.actions li.act-watch { border-left-color:var(--line); }
/* TWO UP ON A DESKTOP. Every advisory was full width — a 1000px card holding
   a 480px paragraph, repeated down the page, which is the one thing that made
   this read as unfinished rather than dense. No span for the faults: a rule
   that gives a fault the whole row collapses the grid on exactly the page
   where every item is one. */
@media (min-width:920px) {
  .actions { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); }
}
.a-h { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; margin-bottom:6px; }
.a-h h3 { font-family:var(--serif); font-size:var(--h3); margin:0;
  letter-spacing:-.005em; font-weight:600; }
.a-tag { font-size:10.5px; font-weight:600; text-transform:uppercase; letter-spacing:.06em;
  padding:3px 8px; border-radius:5px; white-space:nowrap; }
.act-do .a-tag  { background:var(--warnbg); color:var(--warn); }
.act-fix .a-tag { background:var(--badbg); color:var(--bad); }
.act-watch .a-tag { background:var(--nonebg); color:var(--ink3); }
.a-d { font-family:var(--serif); font-size:14.5px; line-height:1.6;
  color:var(--ink2); margin:0; max-width:var(--measure); }
.a-dose { margin:9px 0 0; font-size:13px; color:var(--ink2); }
.a-dose b { font-size:15px; color:var(--ink); font-variant-numeric:tabular-nums; }
.a-n { font-size:12.5px; color:var(--ink3); margin:7px 0 0; max-width:72ch; }
.badge { display:inline-flex; align-items:center; justify-content:center; min-width:17px;
  height:17px; padding:0 5px; margin-left:7px; border-radius:9px; font-size:11px;
  font-weight:700; background:var(--warn); color:var(--panel); vertical-align:1px; }

/* ---- photo tracing tool ---- */
details.tool { background:var(--bg); border:1px solid var(--line); border-radius:11px;
  padding:0; margin:16px 0 0; box-shadow:none; }
details.tool > summary { padding:13px 16px; font-size:13.5px; }
details.tool[open] > summary { border-bottom:1px solid var(--line); }
.tool-body { padding:16px; display:flex; flex-direction:column; gap:12px; }
.tool-body .sub { margin:0; }
.canvas-wrap { display:flex; flex-direction:column; gap:10px; }
#shape-canvas { max-width:100%; height:auto; border:1px solid var(--line);
  border-radius:9px; cursor:crosshair; background:var(--panel); display:block; }
.modes { display:flex; gap:7px; flex-wrap:wrap; }
.modes .btn { font-size:12.5px; padding:7px 12px; font-weight:500; }
.modes .btn i { font-style:normal; opacity:.6; }
.modes .mode[aria-pressed="true"] { background:var(--pump); border-color:var(--pump);
  color:#fff; font-weight:600; }
.hint { font-size:12.5px; color:var(--ink2); margin:0; min-height:1.4em; }
.detected { font-size:12.5px; color:var(--ink2); margin:0; background:var(--panel);
  border:1px solid var(--line); border-left:3px solid var(--good);
  border-radius:8px; padding:10px 13px; }
.detected.warn { border-left-color:var(--warn); }
.detected.bad { border-left-color:var(--bad); }
input[type=file] { font:inherit; font-size:12.5px; color:var(--ink2); }
.result { display:flex; gap:18px; flex-wrap:wrap; align-items:baseline;
  background:var(--panel); border:1px solid var(--line); border-radius:9px;
  padding:14px 16px; }
.result .pl { font-size:11.5px; text-transform:uppercase; letter-spacing:.06em;
  color:var(--ink3); }
.result .ph { font-size:13px; color:var(--ink3); }
.result .eff b { font-size:19px; }
.saved-shape { background:var(--panel); border:1px solid var(--line); border-radius:9px;
  padding:11px 14px; margin:0 0 12px; }
.ss-h { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; }
.ss-h b { font-size:19px; font-variant-numeric:tabular-nums; }
.ss-h span { font-size:12px; color:var(--ink3); }
.ss-h .btn { margin-left:auto; font-size:12.5px; padding:6px 11px; }
.ss-b { font-size:12.5px; color:var(--ink2); margin-top:5px;
  font-variant-numeric:tabular-nums; }
.verdict { background:var(--panel); border:1px solid var(--line);
  border-left:3px solid var(--pump); border-radius:10px; padding:15px 17px;
  display:flex; flex-direction:column; gap:10px; }
.v-head { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; }
.v-head b { font-size:26px; font-weight:640; font-variant-numeric:tabular-nums;
  letter-spacing:-.5px; }
.v-head span { font-size:12.5px; color:var(--ink3); font-variant-numeric:tabular-nums; }
.v-why { font-family:var(--serif); font-size:14.5px; color:var(--ink2);
  margin:0; max-width:var(--measure); line-height:1.62; }
.v-why b { color:var(--ink); }
.v-split { display:flex; gap:18px; flex-wrap:wrap; align-items:baseline;
  padding:10px 0; border-top:1px solid var(--line2); border-bottom:1px solid var(--line2); }
.v-split .eff b { font-size:17px; }
.v-terms summary { font-size:12.5px; cursor:pointer; color:var(--ink2); }
.v-terms ul { margin:9px 0; padding-left:18px; }
.v-terms li { font-size:12.5px; color:var(--ink2); margin-bottom:4px; }
.v-terms li b { color:var(--ink); font-variant-numeric:tabular-nums; }
.designs { display:grid; gap:8px; grid-template-columns:repeat(auto-fill,minmax(112px,1fr)); }
.design { appearance:none; background:var(--panel); border:1px solid var(--line);
  border-radius:9px; padding:9px 7px 8px; cursor:pointer; font:inherit;
  display:flex; flex-direction:column; align-items:center; gap:3px; }
.design:hover { border-color:var(--ink3); }
.design[aria-pressed="true"] { border-color:var(--pump); box-shadow:0 0 0 1px var(--pump); }
.design svg { width:100%; height:auto; }
.design svg path { fill:color-mix(in srgb, var(--pump) 26%, transparent);
  stroke:var(--pump); stroke-width:2; stroke-linejoin:round; }
.d-name { font-size:12px; font-weight:600; color:var(--ink); }
.d-fill { font-size:10.5px; color:var(--ink3); font-variant-numeric:tabular-nums; }
.d-note { font-size:12.5px; color:var(--ink2); margin:0; min-height:1.4em; }
.ctlgrid { display:grid; gap:10px; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); }
.ctl { background:var(--bg); border:1px solid var(--line); border-radius:10px;
  padding:12px 13px; display:flex; flex-direction:column; gap:8px; }

.ctl-h { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.ctl-h b { font-size:13px; }
.ctl-h svg { color:var(--ink3); flex:none; }
.ctl-b { display:flex; gap:6px; }
.ctl-b .btn { flex:1; font-size:12.5px; padding:7px 0; }
/* The ink is a TOKEN, not a literal white: --good is #1a7f4f in light and
   #5fd39a in dark, so a fixed #fff rode a fill that flips and landed at 1.86:1
   in dark — below even the 3:1 graphics floor — on the button that says a
   circuit is ON. --panel is white in light (unchanged, 5.01:1) and #161e27 in
   dark (9.02:1). No screenshot caught it because the sandbox never has a
   circuit on, so the rule never fired. */
.ctl[data-state="on"] .ctl-on { background:var(--good); border-color:var(--good);
  color:var(--panel); }
.ctl[data-state="off"] .ctl-off { background:var(--nonebg); border-color:var(--line); }
.ctl-b input { flex:1; min-width:0; }
.a-src { font-size:11.5px; color:var(--ink3); margin:6px 0 0; }
td.by { color:var(--ink3); font-size:12px; }
/* ---- drill-down ---- */
/* A row that opens something has to look like it does, and reach the keyboard.
   The cursor and the hover tint are the whole affordance; nothing moves. */
tbody.rows-clickable tr { cursor:pointer; }
tbody.rows-clickable tr:hover { background:var(--line2); }
tbody.rows-clickable tr:focus-visible { outline:2px solid var(--pump); outline-offset:-2px; }
.drow-click { cursor:pointer; }
.drow-click:hover { background:var(--line2); }
.drow-click:focus-visible { outline:2px solid var(--pump); outline-offset:-2px; }

#detail { border:1px solid var(--line); border-radius:12px; padding:0; max-width:min(560px,92vw);
  background:var(--panel); color:var(--ink); box-shadow:0 8px 40px rgba(0,0,0,.22); }
#detail::backdrop { background:rgba(8,14,20,.45); }
.d-head { display:flex; align-items:center; gap:14px; justify-content:space-between;
  padding:14px 16px; border-bottom:1px solid var(--line); margin:0; }
.d-head b { font-size:14px; }
#detail-body { padding:14px 16px 16px; max-height:min(64vh,560px); overflow:auto; }
.dl { display:grid; grid-template-columns:minmax(9em,auto) 1fr; gap:1px; margin:0;
  background:var(--line); border:1px solid var(--line); border-radius:8px; overflow:hidden; }
.dl dt { background:var(--panel); color:var(--ink3); font-size:11.5px; padding:7px 10px;
  text-transform:uppercase; letter-spacing:.05em; }
.dl dd { background:var(--panel); margin:0; padding:7px 10px; font-size:13px;
  font-variant-numeric:tabular-nums; word-break:break-word; }
.d-foot { font-size:12px; color:var(--ink3); margin:12px 0 0; }

.credit { border-left:3px solid var(--sheer); background:var(--goodbg);
  padding:11px 14px; border-radius:0 8px 8px 0; margin:14px 0; font-size:13px; }

.srclink { display:inline-flex; align-items:center; gap:6px; font-size:12.5px;
  color:var(--ink2); text-decoration:none; border:1px solid var(--line);
  border-radius:8px; padding:5px 10px; }
.srclink:hover { color:var(--ink); border-color:var(--ink3); }
.srclink svg { opacity:.75; flex:none; }

.thanks { max-width:1080px; margin:0 auto; padding:0 20px 34px; font-size:12px;
  line-height:1.7; color:var(--ink3); }
.thanks a { color:var(--ink2); }
.thanks a:hover { color:var(--ink); }

.helpto { margin:-6px 0 14px; font-size:12.5px; }
.helpto a { color:var(--ink2); text-decoration:none; border-bottom:1px solid var(--line); }
.helpto a:hover { color:var(--ink); border-color:var(--ink3); }

/* Help's marker for a tab this build does not carry. Deliberately not
   named *priv*: the public build strips spans whose class contains it. */
.needs-signin { color:var(--ink3); font-size:11.5px; white-space:nowrap; }

.dt-get { font-size:12px; color:var(--ink2); text-decoration:none; white-space:nowrap;
  border:1px solid var(--line); border-radius:7px; padding:5px 9px; }
.dt-get:hover { color:var(--ink); border-color:var(--ink3); }
.ctl-why { font-size:11.5px; color:var(--ink3); margin:0; }
.ctl-cool { font-size:11px; color:var(--ink3); margin:0; opacity:.8; }
.ctl-msg { font-size:11.5px; }
td.ok { color:var(--good); }
td.bad { color:var(--bad); }

/* ------------------------------------------------------------------- help --
   Reference material read at the panel: on a phone, in a garage, often in
   sunlight. So the type is a step larger than the dashboard's, the tables
   scroll rather than reflow (a wiring row that wraps is a wiring row that can
   be misread), and the danger blocks are loud on purpose. */
#tab-help p { max-width:var(--measure); line-height:1.68; font-size:var(--prose);
  font-family:var(--serif); color:var(--ink2); }
#tab-help h3 { font-size:13.5px; margin:26px 0 8px; color:var(--ink); }
#tab-help .sub { margin-top:-6px; font-size:11px; letter-spacing:.06em;
  text-transform:uppercase; color:var(--ink3); }
#tab-help ol.proc { max-width:68ch; margin:10px 0 20px; padding-left:22px;
  font-size:13.5px; line-height:1.62; color:var(--ink2); }
#tab-help ol.proc li { margin-bottom:7px; padding-left:3px; }

/* External links are marked, because every other link on this page is an
   in-page jump — a reader at the pool panel on a phone data connection should
   know which ones leave the site before tapping. */
#tab-help a.ext { color:var(--aimink); text-decoration:none;
  border-bottom:1px solid color-mix(in srgb, var(--aimink) 35%, transparent); }
#tab-help a.ext:hover { border-bottom-color:var(--aimink); }
#tab-help a.ext::after { content:"\2197"; font-size:.85em; margin-left:2px;
  opacity:.7; }

#tab-help p.ref-cap { font-size:11.5px; color:var(--ink3); margin:0 0 8px;
  line-height:1.5; max-width:68ch; }
table.ref td { vertical-align:top; padding:8px 14px 8px 0; font-size:12.5px;
  line-height:1.5; }
table.ref td:first-child { white-space:nowrap; }
table.ref .y  { color:var(--good); font-weight:600; }
table.ref .n2 { color:var(--ink3); }

.danger { border:1px solid color-mix(in srgb, var(--bad) 42%, var(--line));
  background:var(--badbg); border-radius:10px; padding:13px 15px; margin:16px 0 20px;
  max-width:72ch; }
.danger-h { font-weight:650; font-size:12.5px; color:var(--bad);
  margin:0 0 7px !important; letter-spacing:.01em; }
.danger p { margin:0 0 8px; font-size:13px; }
.danger p:last-child { margin-bottom:0; }

pre.cmd { background:var(--bg); border:1px solid var(--line); border-radius:9px;
  padding:12px 14px; margin:10px 0 20px; overflow-x:auto;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px;
  line-height:1.75; color:var(--ink2); }
pre.cmd span { display:block; white-space:pre; }
pre.cmd span.c { color:var(--ink3); }

/* The figure sits on --panel and the terminal blocks inside it on --bg, so the
   two boxes read as objects on a surface. Filling both from --bg made the
   blocks vanish into the page and left only their outlines. */
/* The diagram stops being legible below about 620px of drawing width, so it
   scrolls inside its own box rather than shrinking into unreadability on a
   phone — which is the device it is most often read on. */
figure.wiring { margin:18px 0 22px; padding:14px; border:1px solid var(--line);
  border-radius:11px; background:var(--panel); overflow-x:auto; }
figure.wiring svg { display:block; min-width:620px; width:100%; height:auto; }
figure.wiring figcaption { margin-top:10px; font-size:11.5px; color:var(--ink3);
  line-height:1.5; min-width:0; }

.signin { display:inline-flex; align-items:center; gap:6px; text-decoration:none;
  font-size:12px; font-weight:600; padding:6px 11px; border-radius:8px;
  border:1px solid var(--line); background:var(--panel); color:var(--ink2); }
.signin:hover { border-color:var(--aimink); color:var(--aimink); }
.whoami { font-size:11.5px; color:var(--ink3); font-variant-numeric:tabular-nums; }
.whoami.warn { color:var(--warn); font-weight:600; }

/* The name is a button, and the menu hangs off it. */
.whobox { position:relative; display:inline-flex; }
button.whoami { font:inherit; font-size:11.5px; color:var(--ink3); cursor:pointer;
  background:none; border:1px solid transparent; border-radius:7px;
  padding:4px 9px; font-variant-numeric:tabular-nums; }
button.whoami:hover:not(:disabled) { border-color:var(--line); color:var(--ink2); }
button.whoami[aria-expanded="true"] { border-color:var(--line); background:var(--panel); }
button.whoami:disabled { cursor:default; }
button.whoami.warn { color:var(--warn); font-weight:600; }
.whomenu { position:absolute; top:calc(100% + 5px); right:0; z-index:30;
  background:var(--panel); border:1px solid var(--line); border-radius:9px;
  box-shadow:var(--shadow); padding:5px; min-width:130px; }
.whomenu .signout { display:block; padding:7px 11px; border-radius:6px;
  font-size:12.5px; text-decoration:none; color:var(--ink2); white-space:nowrap; }
.whomenu .signout:hover { background:var(--badbg); color:var(--bad); }

.signedout { margin:0 0 16px; padding:11px 14px; border-radius:10px;
  border:1px solid color-mix(in srgb, var(--good) 40%, var(--line));
  background:var(--goodbg); font-size:12.5px; line-height:1.55; color:var(--ink2); }
.signedout span { display:block; margin-top:4px; color:var(--ink3); font-size:12px; }
.signedout a { color:var(--aimink); }

/* The dot is a third signal, after the aria-label and the title — never the
   only one. Sized to read at a glance without competing with the tab label.

   THE TWO SEVERITIES DIFFERED ONLY IN background. Measured in a browser: the
   computed styles of .tdot.warn and .tdot.bad were identical in every property
   but colour, and the severity was otherwise carried only by a `title`
   tooltip — which a keyboard user never opens and a touch user cannot. So a
   reader who cannot separate the amber from the red saw "something", with no
   way to find out which. This project's own rule is that every state ships its
   word; the shape is the channel that survives being read on a phone. A
   ring for "needs attention", a filled square for "outside the target": they
   differ in fill AND in outline AND in silhouette, so any one of the three is
   enough. Same footprint either way, so the tab strip does not reflow when a
   reading crosses a band. */
.tdot { width:8px; height:8px; margin-left:6px; flex:none; box-sizing:border-box;
  display:inline-block; vertical-align:middle; }
.tdot.bad  { background:var(--bad); border-radius:2px; }
.tdot.warn { background:transparent; border:2px solid var(--warn); border-radius:50%; }
[role="tab"] { position:relative; }

/* ------------------------------------------------------------ data tables --
   Built for reading a few hundred rows on a laptop and a few dozen on a phone.
   The bar stays put, the header stays put, and only the rows move. */
.dtable { border:1px solid var(--line); border-radius:11px; background:var(--panel);
  margin:0 0 18px; overflow:hidden; }
.dt-bar { display:flex; align-items:center; gap:10px; flex-wrap:wrap;
  padding:10px 12px; border-bottom:1px solid var(--line); background:var(--bg); }
.dt-name { font-size:12.5px; }
.dt-filter { flex:1; min-width:120px; max-width:240px; font-size:12.5px;
  padding:5px 9px; border:1px solid var(--line); border-radius:7px;
  background:var(--panel); color:var(--ink); }
/* --pump, like every other focus ring in this file. This was the one
   indicator that used --aim, which is a hairline colour: 2.39:1 against the
   light .dt-bar and 2.84:1 against the dark one, both under the 3:1 WCAG 2.2
   SC 1.4.11 asks of a focus indicator, in BOTH themes. --pump is 4.08:1 and
   4.16:1 there. Nine rings said focus looks like this; the tenth, on the
   control a keyboard reader uses to reach a reading three months back, said
   something else and said it more faintly. */
.dt-filter:focus { outline:2px solid var(--pump); outline-offset:1px; }
.dt-count { font-size:11.5px; color:var(--ink3); font-variant-numeric:tabular-nums;
  margin-left:auto; }
.dt-more { font-size:11.5px; padding:4px 10px; }
.dt-note { margin:0; padding:9px 12px; font-size:11.5px; line-height:1.5;
  color:var(--ink3); border-bottom:1px solid var(--line2); }

/* Both axes scroll inside this box, so a wide table never pushes the PAGE
   sideways and a long one never pushes the tabs off the top of the screen. */
.dt-scroll { max-height:430px; overflow:auto; }
.dtable table.raw { width:100%; border-collapse:separate; border-spacing:0; }
.dtable table.raw th {
  position:sticky; top:0; z-index:1; cursor:pointer; white-space:nowrap;
  background:var(--panel); padding:7px 11px;
  border-bottom:1px solid var(--line); }
.dtable table.raw th:hover { color:var(--ink); }
.dtable table.raw th[aria-sort="ascending"]::after  { content:" \2191"; opacity:.75; }
.dtable table.raw th[aria-sort="descending"]::after { content:" \2193"; opacity:.75; }
.dtable table.raw td { padding:6px 11px; white-space:nowrap;
  font-variant-numeric:tabular-nums; border-bottom:1px solid var(--line2); }
.dtable table.raw tbody tr:hover td { background:var(--bg); }
.dt-none { margin:0; padding:14px 12px; font-size:12.5px; color:var(--ink3); }
.dtable-empty { border:1px dashed var(--line); border-radius:11px; padding:12px 14px;
  margin:0 0 18px; font-size:12.5px; color:var(--ink3); display:flex; gap:8px; }
.dtable-empty b { color:var(--ink2); }

@media (max-width:560px) {
  .dt-count { margin-left:0; width:100%; }
  .dt-scroll { max-height:340px; }
}

/* Pool + spa = total, as an equation rather than three numbers in a row. The
   operators are what make it read as arithmetic at a glance. */
.v-parts, .vt-row { display:flex; align-items:flex-end; gap:12px; flex-wrap:wrap; }
.vp, .vt-cell { display:flex; flex-direction:column; line-height:1.15; }
.vp i, .vt-cell i { font-style:normal; font-size:10.5px; letter-spacing:.07em;
  text-transform:uppercase; color:var(--ink3); margin-bottom:3px; }
.vp b, .vt-cell b { font-size:23px; font-variant-numeric:tabular-nums;
  color:var(--ink); }
.vt-cell u { text-decoration:none; font-size:10.5px; color:var(--ink3); }
.vp-op, .vt-op { font-size:17px; color:var(--ink3); padding-bottom:3px; }
.vp-total b, .vt-sum b { color:var(--aimink); }
.v-range { margin:6px 0 0; font-size:11.5px; color:var(--ink3); }

.vt { padding:15px 16px; }
.vt-row { gap:14px; }
.vt-reset { margin-left:auto; font-size:11.5px; padding:4px 11px; }
.vt-note { margin:11px 0 0; font-size:12px; line-height:1.55; color:var(--ink3);
  max-width:62ch; }
.vt.vt-live .vt-sum b { color:var(--good); }

/* ---- cost to refill ----
   Separated from the volume row by a rule rather than a gap: the volume is
   the answer, the cost is something done with it, and a reader scanning the
   card should see where one stops. Tokens on both sides of the dark switch,
   never a colour defined only inside the media query. */
.vt-cost { margin-top:14px; padding-top:13px; border-top:1px solid var(--line); }
.vt-cost-fig { display:flex; align-items:flex-end; gap:12px; flex-wrap:wrap; }
.vt-cost-fig .vt-cell b { color:var(--aimink); }
.vt-cost-rate { display:flex; align-items:center; gap:8px; flex-wrap:wrap;
  margin-top:11px; }
.vt-cost-rate label { font-size:10.5px; letter-spacing:.07em;
  text-transform:uppercase; color:var(--ink3); }
/* The $ and the field read as one control, so they share a border and the
   input gives up its own. */
.vt-rate-in { display:inline-flex; align-items:center; gap:2px;
  border:1px solid var(--line); border-radius:7px; padding:0 8px;
  background:var(--panel); color:var(--ink3); font-size:13px; }
.vt-rate-in input { border:0; background:none; color:var(--ink); width:7.5ch;
  padding:7px 0; font-size:13.5px; font-variant-numeric:tabular-nums; }
.vt-rate-in input:focus { outline:none; }
.vt-rate-in:focus-within { border-color:var(--aimink); }
#vol-rate-unit { font-size:13px; padding:7px 8px; border-radius:7px;
  border:1px solid var(--line); background:var(--panel); color:var(--ink); }
.vt-cost-note { margin:10px 0 0; font-size:11.5px; line-height:1.55;
  color:var(--ink3); max-width:62ch; }

/* .spa-design.on used to live here and never matched anything: the picker sets
   aria-pressed, and .design[aria-pressed="true"] above already styles it. A
   selector that cannot fire is worse than no selector, because it tells the next
   reader there is an `on` state to maintain. */

/* ================================================== equipment state colours ==
   ONE vocabulary for "is this thing running", used everywhere it is asked:
   the control cards, the strip on Home, and anything added later.

     on        green    the panel REPORTED it on
     off       grey     the panel reported it off
     pending   amber    asked for, not yet seen in a reading
     unknown   faint    no reading at all

   Pending is the one that has to exist. Samples arrive every fifteen minutes,
   so a switch that was just turned on reads as off for up to a quarter of an
   hour — which looks identical to a command that failed. Amber says "waiting",
   and the difference between that and grey is the difference between "the pool
   is ignoring me" and "the pool has not been asked yet".

   NEVER COLOUR ALONE: every one of these ships with its word, and pending also
   animates, so it is distinguishable without colour vision and without motion. */
.st { display:inline-flex; align-items:center; gap:5px; font-size:11px;
  font-weight:600; padding:2px 8px; border-radius:999px; text-transform:capitalize;
  white-space:nowrap; border:1px solid transparent; }
.st::before { content:""; width:7px; height:7px; border-radius:50%;
  background:currentColor; flex:none; }
.st-on      { background:var(--goodbg); color:var(--good); }
.st-off     { background:var(--nonebg); color:var(--ink3); }
.st-pending { background:var(--warnbg); color:var(--warn); }
/* A FIFTH STATE, because a collector that ERRORED is not a command that is
   waiting. The Collection tab asked for this class from the day it shipped and
   there was no rule for it, so a failed source rendered as an unpadded, unlit
   word — the one state on that tab that most needed to be seen was the only
   one that was not drawn. The pair is --bad on --badbg, already measured by
   bin/contrast as the bad verdict pill. Found by the check that asks whether
   every class the page wears has a rule; it was the fourth in this file. */
.st-bad     { background:var(--badbg); color:var(--bad); }
/* FAINT IS A GROUND, NOT AN OPACITY. This was `background:var(--nonebg);
   color:var(--ink3); opacity:.75`, and element opacity fades the TEXT along
   with the fill: 11px/600 type composited down to 2.78:1 in light and about
   the same in dark, a hard fail in both, and invisible to a checker that can
   only measure the two colours it was given. So unknown now differs from off
   the way it should — by having no fill at all, and a dashed hairline where
   off has a solid one. That is a shape difference as well as a colour one,
   it reads at 11px, and the pill's own word ("no reading") still carries the
   meaning on its own. The text is plain --ink3 on whatever surface the pill
   sits on, which is measured (ink3/panel, ink3/bg). */
.st-unknown { background:none; border-color:var(--line);
  border-style:dashed; color:var(--ink3); }
.st-pending::before { animation:stpulse 1.4s ease-in-out infinite; }
@keyframes stpulse { 0%,100% { opacity:1 } 50% { opacity:.25 } }
@media (prefers-reduced-motion:reduce) { .st-pending::before { animation:none } }

/* The control card borrows the same states so the card and its pill cannot
   disagree — they are driven from one data-state attribute. */
.ctl[data-state="on"]      { border-color:color-mix(in srgb, var(--good) 55%, var(--line));
                             background:color-mix(in srgb, var(--goodbg) 55%, var(--bg)); }
.ctl[data-state="pending"] { border-color:color-mix(in srgb, var(--warn) 60%, var(--line));
                             background:color-mix(in srgb, var(--warnbg) 45%, var(--bg)); }

/* Right now, on Home. */
.nowstrip { display:flex; flex-wrap:wrap; gap:8px; margin:0; }
.nowstrip .st { font-size:11.5px; padding:4px 10px; }
.nowstrip .st b { font-weight:600; }
/* The <i> here is not decoration: it is the STATE WORD — "on", "off", "no
   reading", the cell output, how old the reading is — which is the non-colour
   channel the whole vocabulary above depends on. `opacity:.75` composited it
   to 3.01:1 on the off pill and 3.03:1 on the on pill in light, under the 4.5
   its 11.5px needs. The label is meant to lead and the value to follow; weight
   does that, and unlike opacity it does not touch the contrast. */
.nowstrip .st i { font-style:normal; font-weight:400; margin-left:2px; }
.st-legend { display:flex; gap:12px; flex-wrap:wrap; margin:10px 0 0;
  font-size:11px; color:var(--ink3); }
.st-legend .st { font-size:10.5px; padding:1px 7px; }

/* The reason a control is unavailable belongs beside the control, not in a
   panel above a 900px canvas. */
.fmsg.need { color:var(--warn); font-weight:600; max-width:44ch; line-height:1.45; }

.cred { padding:14px 0; border-top:1px solid var(--line2); }
.cred:first-of-type { border-top:0; padding-top:2px; }
.c-h { display:flex; align-items:center; gap:9px; flex-wrap:wrap; margin-bottom:9px; }
.c-h b { font-size:13.5px; }
.c-h svg { color:var(--ink3); }
.c-note { font-size:12px; color:var(--ink3); }
.c-msg { display:block; margin-top:6px; }
input[type=password] { font:inherit; font-size:13.5px; color:var(--ink);
  background:var(--panel); border:1px solid var(--line); border-radius:8px;
  padding:8px 10px; width:100%; letter-spacing:.12em; }
input[type=password]:focus { outline:2px solid var(--pump); outline-offset:-1px;
  border-color:var(--pump); }

.n-row { padding:9px 0; border-top:1px solid var(--line2); align-items:flex-start; }
.n-row:first-of-type { border-top:0; }
.n-row span { display:flex; flex-direction:column; gap:2px; }
.n-row b { font-size:13.5px; font-weight:600; color:var(--ink); }
.n-row i { font-style:normal; font-size:12.5px; color:var(--ink3); max-width:74ch; }

/* ---- the Collection tab's source cards, and the Poolhound info tab ----
   THESE CLASSES WERE USED BEFORE THEY EXISTED. The Collection tab shipped
   naming .srcgrid, .srch and .v-ok, none of which had a rule — and worse, it
   asked for `class="card srcrow"`, where .srcrow is the two-column grid below
   that belongs to a completely different component. The result was on the
   page: the state pill printed on top of the source name, the description
   thrown into a right-hand column against the far edge, and no padding inside
   any of the cards. Renamed to .source-card so the collision cannot come back,
   and given the rules it always assumed it had. */
/* 230px rather than 262: the info tab has FOUR sources, and at 262 a 1040px
   column fits three of them, leaving the fourth alone on its own row against a
   two-thirds-empty line. Four fit at 230, and Collection's five still wrap
   evenly. */
.srcgrid { display:grid; gap:12px;
  grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); }
.source-card { display:flex; flex-direction:column; gap:6px; }
.source-card .sub { margin:0; }
.srch { display:flex; align-items:center; gap:9px; flex-wrap:wrap;
  font-size:13.5px; }
.srch svg { color:var(--ink3); flex:none; }

/* The banner forms. .v-warn was a bare text colour and the other two did not
   exist; all three are used as a single line of verdict above a section, so
   they are one shape in three states. */
/* NORMAL FLOW, NOT FLEX. These carry mixed inline content — an icon, then a
   bold clause, then the rest of the sentence — and a flex container would make
   each of those a separate item laid out side by side with a gap between them.
   The bold half of "One caution before reading anything into a pH trend" would
   have been set beside its own sentence rather than at the start of it. */
.v-ok, .v-warn, .v-note { font-size:12.5px; line-height:1.55;
  border-radius:9px; padding:10px 13px; margin:0 0 14px; max-width:78ch; }
.v-ok { color:var(--good); background:var(--goodbg); }
.v-warn { color:var(--warn); background:var(--warnbg); }
.v-note { color:var(--ink2); background:var(--nonebg);
  border:1px solid var(--line); }
.v-ok > svg:first-child, .v-warn > svg:first-child, .v-note > svg:first-child {
  vertical-align:-2px; margin-right:3px; }
.v-note b { color:var(--ink); }

/* ---- the Ask tab ---- */
.ask-answer { white-space:pre-wrap; line-height:1.6; font-size:13.5px;
  color:var(--ink); margin-top:14px; }
/* The context is shown verbatim, because "a summary of your pool is sent" is
   a claim and this is the evidence for it. Monospace and scrollable: it is a
   transcript, not prose, and it must not be mistaken for the answer. */
.ask-ctx { margin:0; font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:11.5px; line-height:1.55; color:var(--ink2); white-space:pre;
  overflow-x:auto; max-height:320px; overflow-y:auto; }
#ask-q { font:inherit; font-size:13.5px; padding:9px 11px; border-radius:8px;
  border:1px solid var(--line); background:var(--panel); color:var(--ink);
  resize:vertical; }
#ask-q:focus { outline:2px solid var(--pump); outline-offset:-1px; }
#ask-q[disabled] { opacity:.6; }

/* ---- chart controls: the time window, and which sources are drawn ----
   The window is chosen between server-rendered variants, so this only shows
   and hides; nothing is redrawn in the browser. The source toggles work the
   same way, on the class every drawn element already carries.

   Buttons rather than a select: four options that are compared against each
   other, all visible at once, is what a segmented control is for — and it
   matches the tab bar above it rather than introducing a second idiom. */
.chartbar { display:flex; flex-wrap:wrap; gap:10px; align-items:center;
  justify-content:space-between; margin:0 0 10px; }
.rangebar, .srcbar { display:inline-flex; flex-wrap:wrap; gap:2px; padding:3px;
  background:var(--panel); border:1px solid var(--line); border-radius:9px; }
.rbtn, .sbtn { appearance:none; background:none; border:0; cursor:pointer;
  font:inherit; font-size:12px; color:var(--ink3); padding:4px 10px;
  border-radius:6px; white-space:nowrap; display:inline-flex; align-items:center;
  gap:6px; }
.rbtn:hover, .sbtn:hover { color:var(--ink); background:var(--line2); }
.rbtn[aria-pressed="true"] { color:var(--ink); font-weight:600;
  background:var(--band); box-shadow:inset 0 -2px 0 var(--pump); }
.rbtn:focus-visible, .sbtn:focus-visible { outline:2px solid var(--pump);
  outline-offset:1px; }

/* A source toggle carries its own mark, so the bar is the legend as well as
   the control — and the mark is the SHAPE the chart draws, which is what
   distinguishes the sources there. Never colour alone, on either. */
.sbtn[aria-pressed="true"] { color:var(--ink); font-weight:600; }
.sbtn[aria-pressed="false"] { opacity:.55; text-decoration:line-through; }
.sbtn svg { flex:none; }

/* Hiding a source. The class is on the section, so one rule reaches the marks,
   the line, the dots and the per-source end labels together — those are four
   elements that must appear and disappear as one, and did not when this was
   tried on the marks alone. */
.hide-s-pod .s-pod, .hide-s-les .s-les, .hide-s-man .s-man,
.hide-s-wg .s-wg { display:none; }

/* ---- Poolhound info: the three things this is for ----
   The thesis of the landing page, so they get more room than the source notes
   below them: a heading each, at h3, and the accent on the glyph. Three cards
   rather than three paragraphs because they are three separate problems that
   happen to share a pool — prose runs them together, which is how the page
   came to read as being about chemistry alone. */
/* SUBGRID, so the three paragraphs start on the same line as each other.
   One heading wraps to two lines and the others do not — at some widths all
   three wrap, at others only one — so any fixed height is wrong at most
   widths and shortening the copy to fit is tuning a sentence to a column.
   Each card spans two of the parent's rows and inherits those tracks, which
   aligns the headings and the bodies at every width there is. Where subgrid
   is unsupported the declaration is dropped and the cards fall back to what
   they did before: slightly ragged, not broken. */
.purpose { display:grid; gap:14px; margin-bottom:16px;
  grid-template-columns:repeat(auto-fit,minmax(275px,1fr)); }
.purpose .card { grid-row:span 2; display:grid; grid-template-rows:subgrid;
  gap:8px; align-content:start; }
.purpose h3 { font-family:var(--serif); font-size:var(--h3); font-weight:600;
  margin:0; display:flex; align-items:flex-start;
  gap:9px; line-height:1.35; letter-spacing:-.1px; }
.purpose h3 svg { color:var(--pump); flex:none; margin-top:1px; }
/* The thesis of the landing page, set as prose rather than as caption text:
   it was 12.5px --ink3, the lightest and smallest combination this
   stylesheet has, carrying the three sentences the page exists to make. */
.purpose p { margin:0; font-family:var(--serif); font-size:14.5px;
  color:var(--ink2); line-height:1.6; }

/* ---- Poolhound info: the figures ----
   Each screenshot is one object — image, hairline, caption — so the caption
   cannot be read as belonging to the picture below it. The image carries its
   intrinsic size in the markup, so the card holds its shape before the file
   arrives and the page does not jump as six of them load. */
/* TWO UP, NOT THREE. At minmax(318px) a 1100px page fitted three of these
   across, which made each figure about 350px wide — a picture of a dense
   dashboard, shrunk until none of it could be read, used as the evidence that
   the dashboard is worth looking at. Two across is roughly 545px each, which
   is the difference between a thumbnail and a screenshot. */
/* min(430px,100%) RATHER THAN 430px, and bin/drive found out why. A bare
   minimum larger than the viewport does not collapse — the track stays 430px
   wide inside a 358px column and the whole PAGE scrolls sideways, carrying
   the tab bar off the edge with it. That is the same defect this project
   shipped once before with an off-screen span, and it is invisible to every
   static check and to a desktop screenshot. */
.shotgrid { display:grid; gap:18px;
  grid-template-columns:repeat(auto-fit,minmax(min(430px,100%),1fr)); }
.shot { margin:0; background:var(--panel); border:1px solid var(--line);
  border-radius:var(--r-md); overflow:hidden; }
.shot img { display:block; width:100%; height:auto;
  border-bottom:1px solid var(--line); background:var(--line2); }
.shot figcaption { font-family:var(--serif); font-size:14px; color:var(--ink2);
  line-height:1.58; padding:13px 15px 14px; }
.shot figcaption b { color:var(--ink); margin-right:4px; }
.shot figcaption a { color:var(--ink); }

/* The three public screens, as a list of routes in rather than as prose. */
.tryish { margin:0; padding:0; list-style:none; display:grid; gap:11px; }
.tryish li { font-family:var(--serif); font-size:14.5px; color:var(--ink2);
  line-height:1.6; padding-left:14px; border-left:2px solid var(--line);
  max-width:var(--measure); }
.tryish a { color:var(--ink); font-weight:600; }

/* ---- source rows ---- */
.srcrow { display:grid; grid-template-columns:1fr auto; gap:3px 14px;
  padding:13px 0; border-top:1px solid var(--line2); }
.srcrow:first-child { border-top:0; padding-top:2px; }
.sr-h { display:flex; align-items:center; gap:9px; font-size:13.5px; }
.sr-n { grid-column:1/-1; font-size:12px; color:var(--ink3);
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
.sr-l { grid-row:1; grid-column:2; font-size:12px; color:var(--ink3);
  font-variant-numeric:tabular-nums; }
.sr-d { grid-column:1/-1; font-family:var(--serif); font-size:14px;
  line-height:1.58; color:var(--ink2); max-width:var(--measure); }
/* The schedule line, part of the source it schedules. It carries a bold
   cadence, a place and a state word, and it wraps to its own line at phone
   width rather than being a fifth table column that pushed the card sideways. */
.sr-s { grid-column:1/-1; margin-top:5px; font-size:12px; color:var(--ink3);
  display:flex; flex-wrap:wrap; align-items:baseline; gap:0 6px; }
.sr-s b { color:var(--ink2); font-weight:600; }
.sr-s code { font-size:11.5px; }
.sr-sep { color:var(--line); }
/* A note under a form row, explaining a bound the control enforces. It spans
   the row rather than sitting beside one field, because the limit it explains
   belongs to the pair: "nothing shorter than 12 hours" is about the frequency
   AND the hour that frequency does or does not admit. */
.f-note { flex-basis:100%; margin:2px 0 0; font-size:12px; color:var(--ink3);
  max-width:70ch; }
.p-warn { background:var(--warnbg); color:var(--warn); }
.p-missing { background:var(--badbg); color:var(--bad); }

/* Reference cells other than the first one wrap.

   `table.ref td:first-child { white-space:nowrap }` above says plainly that the
   author meant only the first column to be unwrappable — but the base rule
   `td { white-space:nowrap }` (up in the shared table block) already applies to
   every cell, so that line has never had anything to do. Nothing showed until a
   table arrived whose last column carries a sentence rather than a phrase: it
   laid out 2,165px wide inside a 1,040px page and had to be read by dragging it
   sideways. Appended rather than fixed in place, and matched on `td + td` so
   the first column keeps the nowrap it is supposed to have. This changes
   nothing about the tables that were already here — they fit without wrapping,
   so there is nothing for it to wrap. */
#tab-help table.ref td + td { white-space:normal; }

/* ---- the architecture diagram, and in-page jumps, on the help tab ----------
   Appended rather than folded into the help block above so the existing rules
   are left exactly as they were.

   Same reasoning as figure.wiring: below about 700px of drawing width the box
   labels start to collide, and an unreadable diagram is worse than one that has
   to be pushed sideways. So it scrolls inside its own box and never widens the
   page — which matters because this is read on a phone at the panel.

   Nothing in the drawing is a literal colour except the vendor marks, which are
   badges and glyphs carrying their own ground. The wiring figure below has
   literals because it depicts wires that really are those colours; everything
   else here is a token, because a literal would only mean "invisible in one of
   the two themes".

   .wide is the detailed drawing. Its floor is higher because it carries four
   times as many labels: shrunk to 700px they are four-pixel type, which is not
   a smaller diagram but an absent one. */
/* The drawing's own tokens, redefined for the dark ground it now sits on.
   Nothing in the SVG changes: it asks for --panel, --ink, --line and --ink2
   and gets the deep set here instead. */
.showpiece { background:var(--deep); border-radius:var(--r-lg);
  padding:20px 22px 16px; margin:20px 0 26px; overflow:hidden;
  --panel:var(--deep-2); --ink:var(--deepink); --ink2:var(--deepink2);
  --ink3:var(--deepink2); --line:var(--deep-line); --line2:var(--deep-line);
  --bg:var(--deep); --aim:var(--deep-line); --aimink:var(--deepink2); }
.showpiece figure.arch { margin:0; padding:0; border:0; background:none; }
.showpiece figure.arch figcaption { color:var(--deepink2); max-width:var(--measure); }
.showpiece figure.arch figcaption b { color:var(--deepink); }

figure.arch { margin:18px 0 22px; padding:14px; border:1px solid var(--line);
  border-radius:11px; background:var(--panel); overflow-x:auto; }
figure.arch svg { display:block; min-width:860px; width:100%; height:auto; }
figure.arch.wide svg { min-width:1240px; }
/* The three-box drawing is the first thing on the landing page, so a phone
   gets it stacked rather than shrunk or scrolled: two layouts in the figure,
   one shown. Neither needs a floor, because each is drawn for its width. */
figure.arch.glance svg { min-width:0; }
figure.arch.glance svg.g-tall { display:none; max-width:420px; margin:0 auto; }
@media (max-width:640px) {
  figure.arch.glance svg.g-wide { display:none; }
  figure.arch.glance svg.g-tall { display:block; }
}
.showpiece figure.arch figcaption a { color:var(--deepink); }
.g-t{font:600 16px system-ui,sans-serif;fill:var(--ink)}
.g-s{font:13px system-ui,sans-serif;fill:var(--ink3)}
.g-k{font:600 12.5px system-ui,sans-serif;fill:var(--ink)}
.g-plate{fill:var(--bg);stroke:none}

/* The two drawings' own vocabulary. It lives here rather than in a <style>
   inside the first <svg>, where it used to sit when there was only one drawing:
   an SVG <style> applies to the whole document, so the second figure was
   silently borrowing the first one's classes and would have rendered as a set
   of black boxes the moment the first was removed or shown on its own.

   Every colour is a token except the vendor marks, which carry their own. The
   arrows across the house boundary are --ink rather than the link accent --aim:
   --aim is less contrasty than --ink3 in BOTH themes, which drew the one pair
   that carries the point fainter than the plumbing around it. */
.a-box{fill:var(--panel);stroke:var(--line);stroke-width:1}
.a-box2{fill:var(--bg);stroke:var(--line);stroke-width:1}
.a-grp{fill:none;stroke:var(--line);stroke-width:1}
.a-grp-d{fill:none;stroke:var(--line);stroke-width:1;stroke-dasharray:4 4}
.a-glbl{font:600 11px system-ui,sans-serif;fill:var(--ink3);letter-spacing:.07em}
.a-t{font:600 12.5px system-ui,sans-serif;fill:var(--ink)}
.a-s{font:10.5px system-ui,sans-serif;fill:var(--ink3)}
.a-e{font:10.5px system-ui,sans-serif;fill:var(--ink2)}
.a-m{font:10.5px ui-monospace,SFMono-Regular,Menlo,monospace;fill:var(--ink)}
.a-m2{font:10.5px ui-monospace,SFMono-Regular,Menlo,monospace;fill:var(--ink2)}
.a-l{fill:none;stroke:var(--ink3);stroke-width:1.3}
.a-f{fill:none;stroke:var(--ink);stroke-width:2}
.a-bd{fill:none;stroke:var(--ink);stroke-width:1.2;stroke-dasharray:3 5}
.a-k{font:600 10.5px system-ui,sans-serif;fill:var(--ink)}
.a-rule{stroke:var(--line2);stroke-width:1}
.a-ic{color:var(--ink3)}
/* Hides whatever a label crosses — the figure's ground, so it shows as
   nothing at all. */
.a-plate{fill:var(--panel);stroke:none}
.a-pill{fill:var(--ink)}
.a-pt{font:600 10px system-ui,sans-serif;fill:var(--panel);letter-spacing:.08em}
figure.arch figcaption { margin-top:10px; font-size:11.5px; color:var(--ink3);
  line-height:1.5; min-width:0; max-width:76ch; }

/* Every other link on the help tab is either an external one (marked with an
   arrow by a.ext) or nothing at all, so an unstyled in-page jump would render
   in the browser's default blue and read as the one link that leaves. */
#tab-help a.jump { color:var(--ink); text-decoration:none;
  border-bottom:1px solid var(--ink3); }
#tab-help a.jump:hover { border-bottom-color:var(--ink); }

/* ---- phone: the same design, with the air taken out of it ----
   A section gap is a chapter break on a desktop and a third of the viewport
   on a phone, where nothing is being compared side by side and the rule above
   each heading is already doing the separating. The display sizes come down
   with it: 33px of serif is a headline on a 1140px page and a shout on a
   390px one.

   LAST IN THE FILE ON PURPOSE. These redefine tokens, not components, which
   is the same discipline the two dark blocks follow — and being last means no
   later rule can out-specify them. */
@media (max-width:640px) {
  :root { --gap-section:34px; --lead:20px; --display:27px; --h2:19px;
    --fig:28px; }
  .wrap { padding:20px 16px 56px; }
  h1 { font-size:23px; }
}
"""


# Two spellings of one decision, checked rather than trusted. The palette is
# declared in the bare :root block and again for print, and both must be the
# set above; a partial re-pick now fails at import instead of shipping a chart
# in one palette and a legend in another.
_expect = " ".join(f"--{n}:{h};" for n, h in SERIES.items())
if CSS.count(_expect) != 2:
    raise AssertionError(
        f"style.py: the series palette is out of step with SERIES. Expected the "
        f"line {_expect!r} twice in CSS (the :root block and the print block), "
        f"found {CSS.count(_expect)}. Re-pick the set, not one colour.")
