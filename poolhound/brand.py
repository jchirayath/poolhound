"""The poolhound mark, in one place.

WHY THIS FILE EXISTS

The mark was drawn twice — once in help.py's MARKS table for the architecture
diagrams, and once inline in the page masthead — with the paths retyped. Two
copies of a drawing drift the way two copies of anything else do, and this one
had already done it once: the favicon carried two palette colours typed by hand,
so re-picking a series colour would have left the mark in the old one while
every page still looked plausible.

So: one set of paths, one set of colours, and seams.py knows the mark belongs
here. The masthead, the favicon, the architecture diagram and anything added
later all call the same function.

WHAT IT IS

A pool in section — the two walls and the sloping floor — holding water, with
a wave for the surface and a drop falling into it. The drop replaced a plain
dot: a dot is a mark that happens to be round, and a drop says what the
product is about before the word beside it is read. The flat waterline it used
to hang over read as a pool waiting to be filled; a wave says the pool has
water in it, and the water is what is measured.

Three colours, each doing one job: the drop in the product's blue, the wave in
a lighter blue so the two waters are told apart, and the walls in the spa
orange — the opposite hue, so the pool reads as the container and the blues as
what is in it, and a pair that stays apart for all three colour deficiencies.

The drop is the SAME path the drop icon uses elsewhere in the product, scaled
into place rather than redrawn, so the vocabulary is consistent down to the
curve.
"""

import os

from .style import SERIES
from .icons import _PATHS

VIEWBOX = "0 0 128 128"

# The colours. Two from the series palette, so the mark and the charts share a
# vocabulary, and one that exists only here. The wave's sky blue is 2.5:1 on
# the light tile — under the 3:1 WCAG asks of a graphic, which a logo is
# exempt from — and was chosen over the darker blues that pass because those
# sat too close to the drop to read as a second shade. A lighter wall (amber,
# #f4b740) was tried and rejected: the orange holds the pool's shape better.
WALL = SERIES["spa"]
DROP = SERIES["pump"]
WAVE = "#0ea5e9"

# The pool, in section: open at the top, because the surface is the wave
# rather than a ruled line. 88 units wide, using the box the old drop-above-
# the-rim layout left empty.
_POOL = '<path d="M22 30v38l84 32V30"/>'

# The surface: two parallel waves, each three half-waves of 18 units and 3
# high, drawn thinner than the walls (WAVE_W against 9) so they read as water
# and not as more pool. Set in from both walls so they sit inside the pool
# rather than joining it — with round caps they run x 34..94 against inner wall
# faces at 26.5 and 101.5. Twelve units apart, which leaves six of air between
# the strokes; the lower one's left end clears the sloping floor by about
# three. The middle half-wave of each dips at x=64, directly under the drop.
WAVE_W = 6
_WAVE = ('<path d="M37 50q9-6 18 0t18 0t18 0"/>'
         '<path d="M37 62q9-6 18 0t18 0t18 0"/>')

# icons._PATHS["drop"], scaled from its 24-unit box into the 128 one and moved
# into place above the wave. Written as a transform rather than as new path
# data so the shape cannot drift from the icon it is a copy of.
# MEASURED, NOT EYEBALLED, and on the TRANSFORMED box: getBBox() reports the
# path's local box before the transform, which is the measurement that looked
# fine the first time this drop was placed and had it overlapping the rim.
#
# The icon's ink is x 6..18, y 3..19. Scale 1.4 makes it 16.8 x 22.4; the
# translate puts its tip at (64, 14), above the walls' tops, and its bottom at
# 36.4 — about eight units of air over the upper wave's stroke, so it reads as
# falling into the water rather than sitting on it.
_DROP_XFORM = 'translate(47.2,9.8) scale(1.4)'

# READ, NOT RETYPED. The note above says the drop is "the SAME path the drop
# icon uses, scaled into place rather than redrawn, so the shape cannot drift
# from the icon it is a copy of" — and then the path was typed out again right
# here, which is a copy that can drift while a comment promises it cannot. Two
# of the three defects this file was written to fix were exactly that shape.
# Taking it from icons.py is what makes the sentence true.
_DROP = _PATHS["drop"]


def _drawing():
    """Walls, wave and drop in their colours — the whole mark without its
    frame, shared by every rendering so the lock-up and the inline mark cannot
    place or colour them differently."""
    return (f'<g fill="none" stroke-width="9" stroke-linecap="round" '
            f'stroke-linejoin="round"><g stroke="{WALL}">{_POOL}</g>'
            f'<g stroke="{WAVE}" stroke-width="{WAVE_W}">{_WAVE}</g></g>'
            f'<g transform="{_DROP_XFORM}"><path d="{_DROP}" fill="{DROP}"/></g>')


def mark(size=26, cls="mark", bg=False):
    """The mark as an inline SVG.

    Its colours are fixed rather than following the text around it: the walls
    used to take currentColor so the mark went grey with the heading, and the
    three-colour mark is recognised by exactly the colours that would remove.

    bg: the rounded tile, for a favicon or an app icon where the mark sits on
    an unknown ground and needs its own.
    """
    tile = (f'<rect width="128" height="128" rx="28" fill="#eef4fb"/>' if bg else "")
    return (
        f'<svg class="{cls}" viewBox="{VIEWBOX}" width="{size}" height="{size}" '
        f'aria-hidden="true">{tile}{_drawing()}</svg>')


def favicon_svg():
    """The mark as a standalone file, for <link rel=icon>.

    Its own tile and its own colours: a favicon is rendered against a browser
    chrome this product has no say over, so currentColor would be a guess.
    """
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">'
            + mark(size=128, cls="", bg=True)
              .split(">", 1)[1].rsplit("</svg>", 1)[0]
            + "</svg>")


def diagram_mark():
    """(width, height, body) for help.py's MARKS table, which draws vendor logos
    beside ours at a shared scale."""
    body = mark(size=128, cls="", bg=True)
    return (128, 128, body.split(">", 1)[1].rsplit("</svg>", 1)[0])


# ---------------------------------------------------------------------------
# The files README reads
#
# WHY THESE ARE GENERATED AND NOT DRAWN
#
# docs/poolhound.svg, its dark twin and the square tile were a THIRD copy of
# the mark — drawn by hand before this module existed and never touched again.
# When the dot became a drop they stayed dots, so the logo at the top of the
# README was a version of the product's mark that the product had stopped
# using, and nothing anywhere could notice: no check reads docs/, and seams.py
# scans poolhound/*.py only.
#
# They are still committed files, because GitHub renders an <img> and cannot
# run anything. But they are now OUTPUT: `bin/brand` writes them, and
# inventory.py compares what is on disk against what this module produces, so
# the copy that ships is the copy that can be regenerated.

# The mark's bounding box in its own 128-unit viewBox, stroke included.
#
# MEASURED, NOT EYEBALLED — and measured on the TRANSFORMED drop, for the same
# reason the note on _DROP_XFORM gives: getBBox() reports a path's local box
# before its transform, which is the measurement that looked fine and was not.
#
#   pool   x 22..106, y 30..100, plus half of the 9-unit stroke on each side
#   waves  x 34..94, y 44..68 — inside the pool
#   drop   x 55.6..72.4, y 14..36.4 — its tip above the walls (the icon's
#          ink through translate(47.2,9.8) scale(1.4))
#
# The union is what a wordmark has to lay out against; using the viewBox
# instead would leave air on one side and none on the other, which is how a
# logo comes to sit off-centre in its own box.
MARK_BBOX = (17.5, 14, 110.5, 104.5)

# "poolhound" at the size and weight below, measured in a browser rather than
# guessed: 247.44 units. The stack falls back differently on every platform, so
# the box carries 8% of slack — enough for the widest of the fallbacks, and not
# so much that the wordmark floats in an empty frame the way the old one did
# (424 units of box for 247 units of text).
_FONT = ("ui-sans-serif,-apple-system,Segoe UI,Roboto,Helvetica,Arial,"
         "sans-serif")
_TEXT_SIZE = 50
_TEXT_WIDTH = 247.44
_TEXT_SLACK = 1.08

_H = 130            # the box's height; the mark and the text are centred in it
_MARK_H = 68        # the mark's ink height, a little above the text's
_PAD = 18
_GAP = 22

# Where the baseline goes so the TEXT'S INK is centred on the same line the
# mark is. Not the em box: "poolhound" has ascenders and one descender, so its
# ink runs from about 0.72em above the baseline to 0.21em below, and centring
# the em box instead sets the word visibly high.
_CAP, _DESC = 0.72, 0.21


def _wordmark_geometry():
    """(scale, dx, dy, text_x, baseline, width) — every number derived."""
    x0, y0, x1, y1 = MARK_BBOX
    s = _MARK_H / (y1 - y0)
    dx = _PAD - x0 * s
    dy = _H / 2 - (y0 + y1) / 2 * s
    text_x = _PAD + (x1 - x0) * s + _GAP
    baseline = _H / 2 + (_CAP - _DESC) / 2 * _TEXT_SIZE
    width = round(text_x + _TEXT_WIDTH * _TEXT_SLACK + _PAD)
    return s, dx, dy, text_x, baseline, width


def _ink(dark):
    """The word's colour, from style.py rather than picked again here.

    The mark's own colours are the same in both files — WALL, WAVE and DROP
    were chosen to hold on either ground — so the word is the only thing the
    dark variant changes. The old dark file carried #5aa0f0 and #5fd39a, two
    colours that existed nowhere in the product; taking `--ink` from the
    palette is what keeps the README's word the page's word.
    """
    from . import contrast, style
    p = contrast.palettes(style.CSS)["dark ([data-theme])" if dark else "light"]
    return p["ink"]


def wordmark_svg(dark=False):
    """The full lock-up — mark plus name — as a standalone file for README.

    Two files rather than one with a media query: GitHub does not apply CSS to
    an <img>, so the dark variant has to be its own document.
    """
    s, dx, dy, text_x, baseline, width = _wordmark_geometry()
    ink = _ink(dark)
    note = ("Same mark on a dark surface — two files rather than one,\n"
            "       because GitHub applies no CSS to an <img>."
            if dark else
            "A pool in section holding water — a wave for the surface\n"
            "       and a drop falling into it.")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {_H}" '
        f'width="{width}" height="{_H}" role="img" aria-labelledby="t">\n'
        f'  <title id="t">poolhound</title>\n'
        f'  <!-- GENERATED by bin/brand from poolhound/brand.py — the mark is\n'
        f'       one drawing and this is a copy of it, so edit brand.py.\n'
        f'       {note} -->\n'
        f'  <g transform="translate({dx:.2f},{dy:.2f}) scale({s:.4f})">\n'
        f'    {_drawing()}\n'
        f'  </g>\n'
        f'  <text x="{text_x:.0f}" y="{baseline:.0f}" font-family="{_FONT}"\n'
        f'        font-size="{_TEXT_SIZE}" font-weight="650" fill="{ink}" '
        f'letter-spacing="-1.2">poolhound</text>\n'
        f'</svg>\n')


def mark_file_svg():
    """The square tile on its own — the same bytes the favicon is built from."""
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" '
            'width="128" height="128" role="img" aria-labelledby="t">\n'
            '  <title id="t">poolhound</title>\n'
            '  <!-- GENERATED by bin/brand from poolhound/brand.py. -->\n'
            '  ' + favicon_svg().split(">", 1)[1].rsplit("</svg>", 1)[0]
            + '\n</svg>\n')


# The committed files, and what each one is. inventory.py compares this table
# against the bytes on disk in BOTH directions, so a file that stops being
# generated is as loud as one that has drifted.
FILES = {
    "docs/poolhound.svg": lambda: wordmark_svg(dark=False),
    "docs/poolhound-dark.svg": lambda: wordmark_svg(dark=True),
    "docs/poolhound-mark.svg": mark_file_svg,
}


def write_files(root):
    """Write every file in FILES. Returns the ones that changed."""
    changed = []
    for rel, make in sorted(FILES.items()):
        path = os.path.join(root, *rel.split("/"))
        want = make()
        have = None
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                have = f.read()
        if have != want:
            with open(path, "w", encoding="utf-8") as f:
                f.write(want)
            changed.append(rel)
    return changed


# ---------------------------------------------------------------- the raster
# WHY THERE IS A PNG AT ALL, IN A REPOSITORY THAT DRAWS EVERYTHING IN SVG
#
# Alert email. Gmail strips inline <svg> and blocks `data:` images, so both of
# those arrive as nothing at all; a remote <img src="https://..."> is blocked by
# default until the reader clicks "display images", and fetches a picture from
# the pool's own host to do it. A PNG attached by Content-ID is the only form
# that renders everywhere without asking the network for anything.
#
# It is generated rather than drawn, from the same mark() the site and the
# favicon use, so the logo on an email cannot become a fourth copy that drifts
# the way docs/poolhound.svg already did once.
#
# IT IS NOT BYTE-CHECKED, AND THAT IS A REAL DIFFERENCE FROM FILES. A browser's
# PNG encoder is not stable across Chromium versions, so "regenerate and diff"
# would fail on an upgrade that changed nothing about the mark. What is checked
# is that the file exists, is a well-formed PNG, and has the dimensions this
# names -- and `bin/brand --raster` rewrites it from brand.py, which is what
# keeps it honest. Do not "fix" the check by comparing bytes.
RASTER_PX = 128

RASTERS = {
    "docs/poolhound-mark-email.png": RASTER_PX,
}

def png_dimensions(data):
    """(width, height) from a PNG's IHDR, or None if it is not a PNG."""
    import struct
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])

def write_rasters(root):
    """Render every RASTERS entry with Playwright. Workstation only.

    Playwright is a workstation dependency on purpose -- the runtime is stdlib
    plus six packages and the container has no browser -- which is the same
    reason bin/screenshots writes committed files rather than rendering on
    demand. Returns (written, skipped_reason).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [], ("playwright is not installed here; the committed PNG is "
                    "used as-is (it is a build input, like the screenshots)")
    written = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            for rel, px in sorted(RASTERS.items()):
                page = b.new_page(viewport={"width": px, "height": px},
                                  device_scale_factor=2)
                page.set_content(
                    f'<body style="margin:0">{favicon_svg()}</body>'
                    .replace("<svg", f'<svg width="{px}" height="{px}"', 1))
                path = os.path.join(root, *rel.split("/"))
                page.screenshot(path=path, omit_background=True)
                page.close()
                written.append(rel)
        finally:
            b.close()
    return written, None
