"""The palette checker CLAUDE.md refers to, which did not exist.

CLAUDE.md says the series palette "is validated as a set ... Re-picking one in
isolation means re-running the checker", and there was no checker to re-run. A
rule enforced by a sentence is a rule that holds until somebody is in a hurry --
and it had already stopped holding: in light mode the `good` and `warn` verdict
pills sat at 4.42:1 and 4.09:1 against their own fills, under the 4.5 floor for
11px text, while the comment above them said the set was validated.

It also catches the mistake that is easy to make in THIS stylesheet
specifically: the dark palette is declared twice, once for `prefers-color-scheme`
and once for the explicit `[data-theme="dark"]` stamp, so a colour changed in
one block and not the other silently gives two different dark themes. Editing
those two blocks inconsistently is exactly what happened while fixing the
contrast this module now measures.

WCAG 2.1 relative luminance and contrast ratio, which is arithmetic rather than
opinion: 4.5:1 for body text, 3:1 for large text and for graphical objects such
as a chart line against its ground.

    bin/contrast          report and exit non-zero on a failure
"""
import re

AA_TEXT = 4.5          # body text, and anything at pill size
AA_LARGE = 3.0         # >=18.66px bold or >=24px, and graphical objects


def _rgb(h):
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lum(c):
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(x) for x in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a, b):
    """Contrast between two hex colours, 1.0 to 21.0."""
    la, lb = _lum(_rgb(a)), _lum(_rgb(b))
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _block(css, start_pat):
    """The declarations of the first rule whose selector matches `start_pat`."""
    m = re.search(start_pat, css, re.M)
    if not m:
        return {}
    i = css.index("{", m.start())
    depth, j = 0, i
    while j < len(css):
        if css[j] == "{": depth += 1
        elif css[j] == "}":
            depth -= 1
            if depth == 0: break
        j += 1
    return dict(re.findall(r"--([a-z0-9_-]+)\s*:\s*(#[0-9a-fA-F]{3,6})", css[i:j]))


def palettes(css):
    """Every declared palette: light, dark-by-preference, dark-by-stamp, print."""
    # ^ needs MULTILINE, and the bare :root rule must not be confused with
    # :root[data-theme=...] further down -- the first spelling of this matched
    # nothing at all, so the light palette came back empty and the checker
    # cheerfully reported that zero pairings passed. A checker that can pass
    # while measuring nothing is the defect it exists to find.
    light = _block(css, r"^:root\s*\{")
    out = {"light": light}
    # A FOURTH PALETTE: print. @media print redefines --good, --warn and --bad
    # to inks that survive a monochrome printer, and nothing measured them --
    # so the printed verdict pills sat at 4.42 and 4.09 against a 4.5 floor
    # while the checker reported every pairing passing. "The three declared
    # palettes" was true of the checker and not of the stylesheet.
    for name, pat in (("dark (prefers-color-scheme)",
                       r"@media\s*\(prefers-color-scheme:\s*dark\)"),
                      ("dark ([data-theme])", r":root\[data-theme=\"dark\"\]"),
                      ("print", r"@media\s*print")):
        d = dict(light)
        d.update(_block(css, pat))
        out[name] = d
    return out


# Every pairing that renders text or a line on a ground, with the floor that
# applies to it. Named rather than discovered, because a checker that guesses
# which colours meet is a checker nobody trusts.
PAIRS = [
    ("ink",    "bg",      AA_TEXT,  "body text on the page"),
    ("ink",    "panel",   AA_TEXT,  "body text on a card"),
    ("ink2",   "panel",   AA_TEXT,  "secondary text on a card"),
    ("ink3",   "panel",   AA_TEXT,  "muted labels on a card"),
    ("ink3",   "bg",      AA_TEXT,  "muted labels on the page"),
    ("good",   "goodbg",  AA_TEXT,  "the ok verdict pill"),
    ("warn",   "warnbg",  AA_TEXT,  "the low/high verdict pill"),
    ("bad",    "badbg",   AA_TEXT,  "the bad verdict pill"),
    ("pump",   "panel",   AA_LARGE, "the pump series line"),
    ("spa",    "panel",   AA_LARGE, "the spa series line"),
    ("sheer",  "panel",   AA_LARGE, "the sheer descent series line"),
    ("pump",   "bg",      AA_LARGE, "the pump series line on the page"),
    ("spa",    "bg",      AA_LARGE, "the spa series line on the page"),
    ("sheer",  "bg",      AA_LARGE, "the sheer descent series line on the page"),
    # THE PAIRINGS THAT WERE NOT MEASURED. Each of these failed AA while this
    # checker reported "all pairings pass", because passing is only a statement
    # about the list -- and the list is the part nobody re-reads. The equipment
    # pills sat at 4.37 and 2.78, the primary button's label at 4.42, and the
    # link accent at 2.59. A checker is only as honest as its inventory.
    ("ink3",   "nonebg",  AA_TEXT,  "the off / unknown equipment pill"),
    ("ink2",   "nonebg",  AA_TEXT,  "the over pill"),
    ("btnink", "accent",  AA_TEXT,  "the primary button label"),
    ("aimink", "panel",   AA_TEXT,  "the link accent on a card"),
    ("aimink", "bg",      AA_TEXT,  "the link accent on the page"),
    # THE SHOWPIECE, a new ground with text on it — registered in the same
    # change that declared it. The comment above is about the three separate
    # occasions this checker reported "all pairings pass" over a colour
    # nobody had given it, and the token inventory below refused to let this
    # surface ship unmeasured.
    ("deepink", "deep",   AA_TEXT,  "text on the showpiece panel"),
    ("deepink2", "deep",  AA_TEXT,  "secondary text on the showpiece panel"),
    ("deepink", "deep-2", AA_TEXT,  "text on a box inside the showpiece"),
    ("deepink2", "deep-2", AA_TEXT, "secondary text inside the showpiece"),
    ("deep-line", "deep", AA_LARGE, "the showpiece's own strokes and arrows"),
    # The dashed target line on the charts. A graphical object that carries
    # information, so WCAG 1.4.11 asks 3:1 of it, not 4.5 -- and nothing asked
    # anything of it at all until the inventory check below was written.
    ("aim",    "panel",   AA_LARGE, "the target line on a chart"),
    ("aim",    "bg",      AA_LARGE, "the target line on the page"),
]

# TOKENS A PAIRING DELIBERATELY DOES NOT MEASURE, and why.
#
# THE INVENTORY IS THE PART NOBODY RE-READS -- this file says so twice already,
# about two different omissions, and it happened a third time: --aim sat at
# 2.39:1 on the light page while "all pairings pass" printed, because no
# pairing named it. Every previous fix added the missing pairings by hand,
# which is the same list-maintained-by-hand that failed before.
#
# So the list is now closed against the stylesheet: every token declared on
# :root is either named by a pairing above or excused here, by name, with a
# reason. A new token is a build failure until somebody decides which it is.
NOT_MEASURED = {
    "line":  "a hairline rule between rows; decorative, and removing it loses "
             "no information a reader needs. WCAG 1.4.11 covers boundaries that "
             "identify a control, which these do not.",
    "line2": "the same rule, one step fainter, used inside cards.",
}

# ---------------------------------------------------------------- colour vision
# The series must be separable FROM EACH OTHER, which is the colourblind half of
# the rule and is not a contrast-with-ground question at all.
#
# The first version of this compared LUMINANCE RATIO and failed the palette at
# 1.14 against a 1.35 floor I had invented. Both halves were wrong: luminance
# ratio does not measure whether two hues can be told apart, and a threshold with
# no source behind it is a number that fails whatever it happens to fail.
#
# This simulates dichromatic vision (Vienot 1999) and measures CIE76 dE in Lab
# between the simulated colours -- the same arithmetic an accessibility tool
# uses. Orange and green are the classic deutan/protan confusion pair, which is
# precisely why CLAUDE.md requires the non-colour fallbacks; the point of
# measuring is to know how far apart they actually are under each condition
# rather than to assume.
SERIES = ("pump", "spa", "sheer")

# Clearly different at a glance. A just-noticeable difference is about 2.3; 10
# is "obviously not the same colour"; chart lines a metre from the eye want
# more than that, so the floor is 15 and the report prints the number either way.
SERIES_DE = 15.0

_LMS = ((0.31399022, 0.63951294, 0.04649755),
        (0.15537241, 0.75789446, 0.08670142),
        (0.01775239, 0.10944209, 0.87256922))
_LMS_INV = ((5.47221206, -4.6419601, 0.16963708),
            (-1.1252419, 2.29317094, -0.1678952),
            (0.02980165, -0.19318073, 1.16364789))
_SIM = {
    "deuteranopia": ((1.0, 0.0, 0.0), (0.9513092, 0.0, 0.04866992), (0.0, 0.0, 1.0)),
    "protanopia":   ((0.0, 1.05118294, -0.05116099), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "tritanopia":   ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (-0.86744736, 1.86727089, 0.0)),
}

def _mul(m, v):
    return tuple(sum(m[i][j] * v[j] for j in range(3)) for i in range(3))

def _to_lin(c):
    def f(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return tuple(f(x) for x in c)

def _to_srgb(lin):
    def f(v):
        v = max(0.0, min(1.0, v))
        return v * 12.92 if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
    return tuple(f(x) * 255.0 for x in lin)

def simulate(hexc, kind):
    """The colour as a dichromat sees it."""
    lin = _to_lin(_rgb(hexc))
    lms = _mul(_LMS, lin)
    return _to_srgb(_mul(_LMS_INV, _mul(_SIM[kind], lms)))

def _lab(c):
    lin = _to_lin(c)
    # sRGB D65 -> XYZ
    X = 0.4124 * lin[0] + 0.3576 * lin[1] + 0.1805 * lin[2]
    Y = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    Z = 0.0193 * lin[0] + 0.1192 * lin[1] + 0.9505 * lin[2]
    xn, yn, zn = 0.95047, 1.0, 1.08883
    def f(t):
        return t ** (1 / 3) if t > 216 / 24389 else (841 / 108) * t + 4 / 29
    fx, fy, fz = f(X / xn), f(Y / yn), f(Z / zn)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))

def delta_e(a, b):
    """CIE76 dE between two RGB triples."""
    la, lb = _lab(a), _lab(b)
    return sum((la[i] - lb[i]) ** 2 for i in range(3)) ** 0.5


# Known, deliberate and MITIGATED. CLAUDE.md: "The green fails contrast on the
# light surface alone, which is legal only alongside a non-colour fallback --
# hence the permanent legend, the runtimes as text, and the table view. Do not
# remove those."
#
# EACH EXCEPTION RECORDS THE NUMBER IT WAS GRANTED AT. This used to be a set of
# names with no value bound to them, so the waiver was unconditional: re-picking
# the sheer green drove the same two pairings to 1.11:1 and 1.03:1 -- literally
# invisible against the page -- and bin/contrast printed "all pairings pass" and
# exited 0. CLAUDE.md's "re-picking one colour in isolation means running it" was
# therefore not enforced by running it. An exception is a decision about a
# measurement; without the measurement it is a decision about a string.
#
# MITIGATIONS below are checked too, so the thing that makes these legal has to
# still be in both rendered pages. The alternative -- re-picking a palette that
# has been validated for exactly this and documented -- would be the checker
# overruling the decision it exists to protect.
ACCEPTED = {
    ("light", "the spa series line on the page"): 2.95,
    ("light", "the sheer descent series line"): 2.82,
    ("light", "the sheer descent series line on the page"): 2.60,
    # The printed page inherits the series from light and does not redefine
    # them, so the same exception applies for the same reason -- and it holds
    # only because @media print hides none of the three fallbacks: it touches
    # .dtable for overflow and position, never display. Checked before these
    # two lines were written, not assumed.
    ("print", "the sheer descent series line"): 2.82,
    ("print", "the sheer descent series line on the page"): 2.82,
    # Blue and green converge for tritanopes (~0.01% of people). Deutan and
    # protan, which are the common ones, are 78 and 82 dE apart.
    ("light", "pump vs sheer under tritanopia"): 12.22,
    ("print", "pump vs sheer under tritanopia"): 12.22,
    ("dark (prefers-color-scheme)", "pump vs sheer under tritanopia"): 12.22,
    ("dark ([data-theme])", "pump vs sheer under tritanopia"): 12.22,
}

# How far a recorded value may drift before the exception stops applying.
# Rounding in a re-picked hex is worth a hundredth; anything larger is a
# decision somebody made without re-reading the one above.
DRIFT = 0.05

# And a floor no fallback argues past. A legend tells a reader which line is
# which; it does not make a line they cannot see visible. Applied to contrast
# ratios only -- the colour-vision exceptions are a dE on a different scale and
# are pinned by their recorded value alone.
HARD_FLOOR = 2.5


def _accept(pname, what, value, floor):
    """Does the recorded exception still cover this measurement?

    Returns (covered, why_not). A failure that is NOT covered is reported with
    the reason, because "this used to be accepted" is the most misleading thing
    a checker can stay silent about.
    """
    rec = ACCEPTED.get((pname, what))
    if rec is None:
        return False, ""
    if value < rec - DRIFT:
        return False, f"was accepted at {rec:.2f}, now {value:.2f}"
    if floor in (AA_TEXT, AA_LARGE) and value < HARD_FLOOR:
        return False, (f"{value:.2f}:1 is below the {HARD_FLOOR} floor, which no "
                       f"non-colour fallback excuses")
    return True, ""


# What makes the exceptions above legal: colour is never the only channel.
#
# Each entry is (the fallback's pattern, what it is, what has to be on the page
# before the fallback CAN exist). The third element is the fix for a gate that
# failed on a pool with no data: the runtime text is emitted per runtime bar, so
# an install whose charts are empty has no bars, no text, and was told its
# palette's colour-blindness exceptions were illegal. A first-run owner running
# the documented checks got a hard failure pointing at the colour palette.
#
# The reverse mattered more. Because the probe could only see the fallback on an
# install that already HAS data, it was silent about a removed fallback on any
# pool whose charts were empty — which is the one thing it exists to catch.
# Asking "is there a chart here?" first makes the answer meaningful in both
# directions: present when it should be, and NOT CHECKABLE said out loud rather
# than reported as a pass or a failure.
# AND THE SAME ARGUMENT REACHES THE OTHER TWO. They were written with no
# precondition at all — always required, on every build — which was true while
# every build had charts on it. The public build now has none: Home moved
# behind the sign-in and took all eight of them with it, so the page draws
# nothing in the series palette and the exceptions those fallbacks license do
# not arise on it. Reported as MISSING, the check said a page with no chart on
# it needed a legend for the chart.
#
# `class="chart"` is the precondition for both, and it is the honest one: the
# legend names the series a chart uses, and the table view is the non-colour
# route to the figures a chart draws. No chart, neither obligation — and if a
# chart ever returns to the public page, both are demanded again by existing
# rather than by anybody remembering this.
MITIGATIONS = [
    (r'class="legend"', "the permanent legend naming each series",
     r'class="chart"', "this build draws no chart"),
    # The runtime text is emitted once per DAY ROW, so class="drow" is what has
    # to exist before the fallback can. NOT class="bars" — that is the dominance
    # indicator in the chemistry prose and is present on an empty install, which
    # is how the first version of this precondition kept reporting MISSING.
    (r'class="run"', "the runtimes printed as text beside the bars",
     r'class="drow', "this build has no runtime bars"),
    (r"Every number on this page, as tables", "the table view of every figure",
     r'class="chart"', "this build draws no chart"),
]


def mitigations_present(page_html):
    """Which non-colour fallbacks are missing from a rendered page.

    A fallback whose host element is not on this page at all is not missing —
    it is not applicable, and is reported separately rather than as a failure.
    """
    missing = []
    for pat, why, needs, _na in MITIGATIONS:
        if needs and not re.search(needs, page_html):
            continue                      # nothing here for the fallback to sit beside
        if not re.search(pat, page_html):
            missing.append(why)
    return missing


def mitigations_report(page_html):
    """(what, state) per fallback, for the printed summary."""
    out = []
    for pat, why, needs, na in MITIGATIONS:
        if needs and not re.search(needs, page_html):
            out.append((why, f"not applicable — {na}"))
        elif re.search(pat, page_html):
            out.append((why, "present"))
        else:
            out.append((why, "MISSING"))
    return out


# ------------------------------------------------------- the tab status dots
#
# THE INVENTORY MISSED THE ONE INDICATOR WITH NO WORDS ON IT.
#
# Everything above measures text against the ground it sits on, or the three
# chart series against each other. The tab dot is neither: a 7px circle with no
# text content, whose entire meaning is its fill. Measured, once somebody
# looked: `.tdot.warn` and `.tdot.bad` are 3.96 dE apart under deuteranopia on
# the light palette — against the 15.0 the series have to clear, and barely
# above the 2.3 at which two colours become tellable apart at all. The most
# common colour deficiency sees one indicator wearing two meanings, and this
# file printed "all pairings pass" because no entry named the pair.
#
# DERIVED FROM THE STYLESHEET, so a third state cannot arrive unmeasured. That
# is the failure mode this module has now had four times — --aim, the print
# palette, the equipment pills, and this — every one of them a list extended by
# hand after the fact.
DOT_BASE = "tdot"


def dot_states(css):
    """The status-dot state classes the stylesheet declares."""
    return {m.group(1) for m in
            re.finditer(rf"\.{DOT_BASE}\.([a-z0-9-]+)\s*\{{", css)}


def _dot_decls(css, state):
    """That state's declarations, as {property: value}."""
    body = _block_text(css, rf"\.{DOT_BASE}\.{state}\s*\{{")
    return dict((k.strip().lower(), v.strip())
                for k, v in re.findall(r"([a-z-]+)\s*:\s*([^;]+);?", body))


_COLOUR_PROPS = {"background", "background-color", "color", "border-color",
                 "border-left-color", "fill", "stroke", "outline-color"}


def _block_text(css, start_pat):
    m = re.search(start_pat, css, re.M)
    if not m:
        return ""
    i = css.index("{", m.start())
    depth, j = 0, i
    while j < len(css):
        if css[j] == "{":
            depth += 1
        elif css[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return css[i + 1:j]


# A colour-only pair is legal ONLY with a non-colour channel, and the channel
# has to be on the page before the exception counts — the same contract the
# series exceptions live under, with the same probe.
#
# Recorded with the measurement, not just the name: "an exception is a decision
# about a measurement; without the measurement it is a decision about a string."
# 3.96 dE under deuteranopia is the number this was granted at.
#
# NO REVERSE CHECK ON THIS ONE, deliberately, and it is the only entry here
# without one. Giving the dots a shape difference is work in hand; the day it
# lands the pair stops being colour-only, drops out of the derivation above and
# never reaches this table — and an "excuse for something that no longer
# exists" assertion would turn that fix into a failure.
DOT_CHANNELS = {
    ("bad", "warn"): (
        3.96,
        "each dot carries its own sentence, set beside the colour: the reason "
        "as a title and the tab's name plus that reason as an aria-label, so "
        "the state is readable without seeing the fill at all",
        # What has to be in the rendered page for that to be true. The dot is
        # created by the page script, so the evidence is the script that makes
        # it — a dot built without these lines is a dot with nothing but colour.
        #
        # SEARCHED IN THE DOT SCANNER, NOT IN THE PAGE. A bare `aria-label`
        # probe passes on any page with an aria-label anywhere in it, which is
        # every page this product renders: deleting the dot's own
        # setAttribute line left the probe green. A probe whose subject is the
        # whole document is a probe about the wrong thing, which is the defect
        # this section exists for, one level up.
        (r"aria-label", r"\.title\s*=")),
}

# The page script that builds the dot, and nothing else. Anchored on the
# className assignment, which is the line that gives a dot its state.
_DOT_SCRIPT = re.compile(r"\.className\s*=\s*'" + DOT_BASE + r" '.{0,900}",
                         re.S)


def dot_channel_region(page_html):
    """The part of a rendered page that builds the status dot, or ''."""
    m = _DOT_SCRIPT.search(page_html)
    return m.group(0) if m else ""


def dot_failures(css, pages):
    """Every dot state pair that colour alone has to carry, unexcused.

    `pages` maps a page name to its rendered HTML. A pair is clean when the
    states are separable under every simulation, or when they differ by
    something that is not a colour, or when DOT_CHANNELS names the non-colour
    channel AND every page carries it.
    """
    out = []
    pal = palettes(css)["light"]           # the worst of the four, measured
    states = sorted(dot_states(css))
    for i, a in enumerate(states):
        for b in states[i + 1:]:
            da, db = _dot_decls(css, a), _dot_decls(css, b)
            non_colour = {k: v for k, v in da.items()
                          if k not in _COLOUR_PROPS} != \
                         {k: v for k, v in db.items() if k not in _COLOUR_PROPS}
            if non_colour:
                continue               # told apart by shape, not by hue
            worst, worst_kind = None, ""
            for kind in _SIM:
                ca, cb = pal.get(_token(da)), pal.get(_token(db))
                if not ca or not cb:
                    continue
                d = delta_e(simulate(ca, kind), simulate(cb, kind))
                if worst is None or d < worst:
                    worst, worst_kind = d, kind
            if worst is not None and worst >= SERIES_DE:
                continue               # far enough apart on their own
            key = tuple(sorted((a, b)))
            rec = DOT_CHANNELS.get(key)
            if rec is None:
                out.append((f".{DOT_BASE}.{a} and .{DOT_BASE}.{b} differ only "
                            f"in colour ({worst:.2f} dE under {worst_kind}) and "
                            f"nothing says what makes that legal"))
                continue
            granted, _why, probes = rec
            if worst is not None and worst < granted - DRIFT:
                out.append((f".{DOT_BASE}.{a} vs .{DOT_BASE}.{b} was accepted "
                            f"at {granted:.2f} dE, now {worst:.2f}"))
            for name, html_ in sorted(pages.items()):
                region = dot_channel_region(html_)
                if not region:
                    out.append(f"{name} has no .{DOT_BASE} scanner in it, so "
                               f"the non-colour channel cannot be verified")
                    continue
                for pat in probes:
                    if not re.search(pat, region):
                        out.append(f"the non-colour channel the .{DOT_BASE} "
                                   f"states depend on is gone from {name}")
                        break
    return out


def _token(decls):
    """The palette token a state's fill comes from, e.g. var(--bad) -> bad."""
    for k in ("background", "background-color", "color", "fill", "stroke",
              "border-color", "border-left-color"):
        v = decls.get(k)
        if v:
            m = re.search(r"var\(--([a-z0-9_-]+)\)", v)
            if m:
                return m.group(1)
    return None


def _failures(css):
    """Every measurement that misses its floor, before acceptance is considered.

    Yields (palette, what, a, b, value, floor).
    """
    for pname, pal in palettes(css).items():
        for fg, bg, floor, what in PAIRS:
            if fg not in pal or bg not in pal:
                continue
            r = ratio(pal[fg], pal[bg])
            if r < floor:
                yield (pname, what, pal[fg], pal[bg], r, floor)
        for i, a in enumerate(SERIES):
            for b in SERIES[i + 1:]:
                if a not in pal or b not in pal:
                    continue
                for kind in _SIM:
                    d = delta_e(simulate(pal[a], kind), simulate(pal[b], kind))
                    if d < SERIES_DE:
                        yield (pname, f"{a} vs {b} under {kind}",
                               pal[a], pal[b], d, SERIES_DE)


def check(css):
    """Every failure, as (palette, what, a, b, ratio, floor). Empty means clean."""
    bad = []
    for pname, what, fg, bg, v, floor in _failures(css):
        ok, why = _accept(pname, what, v, floor)
        if not ok:
            bad.append((pname, f"{what} ({why})" if why else what, fg, bg, v, floor))
    # EVERY DECLARED TOKEN IS MEASURED OR EXCUSED BY NAME.
    #
    # Three times now this checker has reported "all pairings pass" about a
    # colour it never looked at, because passing is a statement about the list
    # and each fix extended the list by hand. --aim was the third: a dashed
    # target line on every chart, at 2.39:1 on the light page, named by no
    # pairing. Closing the list against the stylesheet is the only version of
    # this fix that does not need repeating.
    p = palettes(css)
    declared = set(p["light"])
    named = {t for pair in PAIRS for t in pair[:2]}
    unclassified = sorted(declared - named - set(NOT_MEASURED))
    for t in unclassified:
        bad.append(("light", f"--{t} is declared and no pairing measures it; "
                             f"add it to PAIRS or to NOT_MEASURED with a reason",
                    str(p["light"].get(t)), "-", 0.0, 0.0))
    # And the other direction: an excuse for a token that no longer exists is an
    # excuse nobody re-reads, sitting where the next reader will trust it.
    for t in sorted(set(NOT_MEASURED) - declared):
        bad.append(("light", f"--{t} is excused in NOT_MEASURED and is not "
                             f"declared on :root", "-", "-", 0.0, 0.0))

    # The two dark blocks are one decision written twice.
    a, b = p["dark (prefers-color-scheme)"], p["dark ([data-theme])"]
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            bad.append(("dark", f"--{k} differs between the two dark blocks",
                        str(a.get(k)), str(b.get(k)), 0.0, 0.0))
    return bad


def accepted(css):
    """The known exceptions that actually fired, so they are reported not hidden.

    With the value they were RECORDED at beside the value measured now, so a
    drift is legible on the way past rather than only when it crosses a floor.
    """
    out = []
    for pname, what, _fg, _bg, v, floor in _failures(css):
        ok, _ = _accept(pname, what, v, floor)
        if ok:
            out.append((pname, what, v, floor, ACCEPTED[(pname, what)]))
    return out


def main():
    from . import style
    bad = check(style.CSS)
    pals = palettes(style.CSS)
    for pname, pal in pals.items():
        print(f"\n  {pname}")
        for fg, bg, floor, what in PAIRS:
            if fg in pal and bg in pal:
                r = ratio(pal[fg], pal[bg])
                print(f"    {what:44s} {r:5.2f}:1  "
                      f"{'ok' if r >= floor else 'FAILS ' + str(floor)}")
    acc = accepted(style.CSS)
    if acc:
        print("\n  accepted, because colour is not the only channel:")
        for pname, what, v, floor, rec in acc:
            drift = "" if abs(v - rec) < 0.005 else f", recorded at {rec:.2f}"
            print(f"    [{pname}] {what}: {v:.2f} against {floor}{drift}")
    # config.site_dir(), not <package parent>/site. This hardcoded the repo's
    # own site/ directory, so in the server's container -- where the page is at
    # $POOLHOUND_SITE and /app/site does not exist -- the whole mitigation check
    # SILENTLY DISAPPEARED and the checker printed "all pairings pass" having
    # verified nothing about the thing those exceptions depend on. This module's
    # own docstring: a checker that can pass while measuring nothing is the
    # defect it exists to find. Even on a workstation it read the repo's page
    # rather than the configured one, so a render to a different site dir was
    # checked against a page rendered hours earlier from a different config.
    #
    # And a missing page is a FAILURE, not a skip. Three WCAG failures are
    # accepted here on the stated grounds that the legend, the runtimes as text
    # and the table view are present; no evidence means no exception.
    #
    # BOTH BUILDS. This read index.html alone, and admin.html carries the same
    # charts -- plus the control panels, which is the surface the equipment
    # vocabulary lives on. A fallback stripped from the authenticated build only
    # is exactly the shape the two-build split makes possible.
    import os
    from . import config
    print("\n  the fallbacks those exceptions depend on:")
    gone = False
    pages = {}
    for name in ("index.html", "admin.html"):
        page = os.path.join(config.site_dir(), name)
        if not os.path.exists(page):
            print(f"    {name}: NOT RENDERED — {page} does not exist, so the")
            print("      fallbacks the accepted exceptions depend on cannot be")
            print("      verified. Run bin/render first.")
            gone = True
            continue
        html_ = open(page, encoding="utf-8").read()
        pages[name] = html_
        missing = mitigations_present(html_)
        print(f"    {name}")
        for why, state in mitigations_report(html_):
            print(f"      {why:50s} {state}")
        gone = gone or bool(missing)
    if gone:
        print("\n  the accepted exceptions are NOT legal without these.")
        return 1

    # THE TAB STATUS DOTS, which nothing measured until the pair was 3.96 dE
    # apart under deuteranopia with no text on either one. Derived from the
    # stylesheet so a third state is measured the day it is written, and
    # reported with its number whether it passes or not — an exception printed
    # without its measurement is the shape this file has already been wrong in.
    dot_bad = dot_failures(style.CSS, pages)
    print("\n  the tab status dots — colour is never the only channel:")
    _pal = palettes(style.CSS)["light"]
    _states = sorted(dot_states(style.CSS))
    for i, a in enumerate(_states):
        for b in _states[i + 1:]:
            ta, tb = _token(_dot_decls(style.CSS, a)), _token(_dot_decls(style.CSS, b))
            if not (ta and tb and ta in _pal and tb in _pal):
                continue
            for kind in sorted(_SIM):
                d = delta_e(simulate(_pal[ta], kind), simulate(_pal[tb], kind))
                print(f"    .{DOT_BASE}.{a} vs .{DOT_BASE}.{b} under "
                      f"{kind:14s} {d:6.2f} dE")
            rec = DOT_CHANNELS.get(tuple(sorted((a, b))))
            if rec:
                print(f"      accepted at {rec[0]:.2f} dE because {rec[1]}")
    if dot_bad:
        print(f"\n  {len(dot_bad)} dot failure(s):")
        for line in dot_bad:
            print(f"    {line}")
        return 1

    if bad:
        print(f"\n  {len(bad)} failure(s):")
        for pname, what, a, b, r, floor in bad:
            detail = f"{r:.2f}:1 against {floor}" if floor else f"{a} vs {b}"
            print(f"    [{pname}] {what}: {detail}")
        return 1
    print("\n  all pairings pass, and the two dark blocks agree")
    return 0


# Same reason as selftest.py: the deploy runs this as `python -m
# poolhound.contrast` inside the container, and without this block that
# imported the module, checked nothing and exited 0. A contrast gate that
# always passes is worse than no gate, because it is quoted as evidence.
if __name__ == "__main__":
    import sys
    sys.exit(main())
