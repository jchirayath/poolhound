#!/usr/bin/env python3
"""Standard pool shapes, as unit outlines.

WHY THESE ARE OUTLINES AND NOT FORMULAS
  The obvious way to add stock shapes is a formula per shape: length x width for
  a rectangle, pi/4 of that for an oval, a fudge factor for a kidney. That would
  be a third body of volume arithmetic sitting beside the traced-outline path and
  the dose maths, free to drift from both.

  Instead each design is defined once as a polygon in a unit box — x along the
  length, y across the width, both 0..1 — and scaling it by the dimensions
  someone types produces exactly the kind of outline the tracing tool produces.
  Everything downstream is then shared: the same long-axis detection, the same
  slicing, the same area-weighted depth, the same spa handling, the same
  uncertainty. A kidney picked from this list and the same kidney traced off a
  photograph go through identical code.

  It also means the browser needs no generators of its own. The unit polygons are
  exported as JSON and scaled there for the preview, so the picture on screen and
  the number in the record come from one definition.

WHAT THE UNIT AREA TELLS YOU
  Each shape's area as a fraction of its bounding box is the honest version of
  the trade's "free-form factor". A rectangle is 1.00, an oval 0.79, a kidney
  about 0.75 — and those are computed from the outline rather than remembered,
  which is the entire point.
"""
import math

def _rect():
    return [(0, 0), (1, 0), (1, 1), (0, 1)]

def _ellipse(n=72):
    return [(0.5 + 0.5 * math.cos(2 * math.pi * i / n),
             0.5 + 0.5 * math.sin(2 * math.pi * i / n)) for i in range(n)]

def _roman(ends=1, n=30):
    """A rectangle closed by a half-round at one or both ends — the classic
    'Roman end'. The radius is half the width, so the curve meets the straight
    sides tangentially, which is what makes it read as one shape rather than a
    rectangle with a bite taken out."""
    r = 0.5
    pts = []
    if ends == 2:
        for i in range(n + 1):                      # left half-round
            a = math.pi / 2 + math.pi * i / n
            pts.append((r + r * math.cos(a) * (r / 0.5) * 0.5, 0.5 + r * math.sin(a)))
        pts = [(x * 0 + max(0.0, x), y) for x, y in pts]
    else:
        pts.append((0, 0))
    straight_start = 0.5 if ends == 2 else 0.0
    pts.append((straight_start, 0))
    pts.append((1 - 0.5, 0))
    for i in range(n + 1):                          # right half-round
        a = -math.pi / 2 + math.pi * i / n
        pts.append((1 - r + r * math.cos(a), 0.5 + r * math.sin(a)))
    pts.append((straight_start, 1))
    if ends != 2:
        pts.append((0, 1))
    return pts

def _kidney(n=64):
    """One convex belly, one concave waist on the same side. Built from two
    offset circles blended, which is how a kidney is actually formed on site."""
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        r = 1 + 0.30 * math.cos(a) - 0.22 * math.cos(2 * a)
        x = 0.5 + 0.5 * r * math.cos(a) / 1.30
        y = 0.5 + 0.5 * r * math.sin(a) / 1.18
        pts.append((min(1, max(0, x)), min(1, max(0, y))))
    return pts

def _figure8(n=90):
    """Two lobes joined at a neck — the shape the analyser calls two-lobed."""
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        r = 1 + 0.52 * math.cos(2 * a)
        x = 0.5 + 0.5 * r * math.cos(a) / 1.52
        y = 0.5 + 0.5 * r * math.sin(a) / 1.0
        pts.append((min(1, max(0, x)), min(1, max(0, y))))
    return pts

def _true_l(leg=0.55, arm=0.5):
    return [(0, 0), (1, 0), (1, arm), (leg, arm), (leg, 1), (0, 1)]

def _lazy_l(leg=0.58, arm=0.46, cut=0.12):
    """An L with the inside corner cut off on the diagonal, which is how most
    'lazy L' pools are actually built — a square inside corner is a place for
    debris to sit and a swimmer to bark a shin."""
    return [(0, 0), (1, 0), (1, arm), (leg + cut, arm),
            (leg, arm + cut), (leg, 1), (0, 1)]

def _grecian(c=0.16):
    """Rectangle with the corners cut at 45 degrees."""
    return [(c, 0), (1 - c, 0), (1, c), (1, 1 - c),
            (1 - c, 1), (c, 1), (0, 1 - c), (0, c)]

def _lagoon(n=80):
    """A soft free-form with several gentle bulges — the 'lagoon' or 'natural'
    shape. Deliberately irregular: it exists to show that an outline nobody has
    a formula for is handled exactly like the ones that do."""
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        r = (1 + 0.16 * math.sin(3 * a + 0.6) + 0.10 * math.cos(2 * a)
             - 0.07 * math.sin(5 * a))
        x = 0.5 + 0.5 * r * math.cos(a) / 1.30
        y = 0.5 + 0.5 * r * math.sin(a) / 1.22
        pts.append((min(1, max(0, x)), min(1, max(0, y))))
    return pts

def _normalise(pts):
    """Stretch an outline to exactly fill the unit box.

    Without this the contract is a lie. The generators build their curves from
    modulated radii whose extent depends on the modulation, so a "kidney" came
    out filling only 0.54 of its box — which meant a pool entered as 32 by 16 was
    silently neither 32 long nor 16 wide, and the shape read as compact rather
    than elongated because it was not the proportions anyone asked for.

    Normalising here means x really does span the length and y the width, so the
    two numbers someone types are the two dimensions they would measure.
    """
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    x0, x1 = min(xs), max(xs); y0, y1 = min(ys), max(ys)
    dx = (x1 - x0) or 1.0; dy = (y1 - y0) or 1.0
    return [((x - x0) / dx, (y - y0) / dy) for x, y in pts]

TEMPLATES = {
    "rectangle":  dict(name="Rectangle", pts=_rect(),
                       note="Straight sides, square corners. The only shape length "
                            "times width gets exactly right."),
    "roman":      dict(name="Roman end", pts=_roman(1),
                       note="A rectangle closed by a half-round at the deep end."),
    "oval":       dict(name="Oval", pts=_ellipse(),
                       note="A full ellipse in the bounding box."),
    "round":      dict(name="Round", pts=_ellipse(),
                       note="Set length and width the same. Usually a plunge pool "
                            "or a spa, and near enough one depth throughout."),
    "kidney":     dict(name="Kidney", pts=_kidney(),
                       note="One belly, one waist on the same side. The commonest "
                            "free-form, and the one the 0.85 factor was invented for."),
    "figure8":    dict(name="Figure of eight", pts=_figure8(),
                       note="Two lobes joined at a neck. The analyser will spot the "
                            "waist and offer to give each lobe its own depth."),
    "true_l":     dict(name="True L", pts=_true_l(),
                       note="Square inside corner. Often a lap leg with a wider "
                            "shallow end."),
    "lazy_l":     dict(name="Lazy L", pts=_lazy_l(),
                       note="An L with the inside corner cut on the diagonal."),
    "grecian":    dict(name="Grecian", pts=_grecian(),
                       note="Rectangle with the corners cut at forty-five degrees."),
    "lagoon":     dict(name="Lagoon", pts=_lagoon(),
                       note="Soft irregular free-form. No formula exists for it, "
                            "which is exactly why tracing the outline is the method."),
}

for _t in list(TEMPLATES.values()):
    _t["pts"] = _normalise(_t["pts"])

def _unit_area(pts):
    n = len(pts)
    s = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
            for i in range(n))
    return abs(s) / 2.0

def points_for(template, length_ft, width_ft):
    """Scale a unit outline to real feet.

    Note what is NOT here: any per-shape volume rule. The result is a polygon,
    and a polygon is what the rest of the system already knows how to measure.
    """
    t = TEMPLATES.get(template)
    if not t:
        return None
    if not (0 < length_ft <= 200) or not (0 < width_ft <= 200):
        return None
    return [(x * length_ft, y * width_ft) for x, y in t["pts"]]

def as_json():
    """Unit outlines for the browser, so the preview it draws and the volume the
    server records come from the same definition."""
    return {k: {"name": v["name"], "note": v["note"],
                "pts": [[round(x, 5), round(y, 5)] for x, y in v["pts"]],
                "unit_area": round(_unit_area(v["pts"]), 4)}
            for k, v in TEMPLATES.items()}

def _octagon():
    """Eight equal sides — the commonest prefabricated spa shell after round."""
    import math
    return _normalise([(math.cos(math.radians(22.5 + i * 45)),
                        math.sin(math.radians(22.5 + i * 45))) for i in range(8)])

def _squircle(r=0.22):
    """A square with generously rounded corners. Almost every drop-in acrylic
    spa is this rather than a true square — the corners are where the moulding
    radius lives, and treating them as sharp overstates the water by a few
    per cent."""
    pts, seg = [], 8
    import math
    for cx, cy, a0 in ((1 - r, 1 - r, 0), (r, 1 - r, 90), (r, r, 180), (1 - r, r, 270)):
        for i in range(seg + 1):
            a = math.radians(a0 + 90 * i / seg)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts

# Spa shells, kept apart from the pool list.
#
# Not a style choice — a spa is a different measurement problem. It is one
# depth throughout rather than a slope, it nearly always has a perimeter bench
# that removes a third of the water people expect, and the shapes are
# manufactured rather than excavated: round, octagonal and rounded-square cover
# almost everything that is not a gunite spillover. Offering a lagoon here
# would be offering a shape no spa has ever been.
SPA_TEMPLATES = {
    "round":     dict(name="Round", pts=_ellipse(),
                      note="Set diameter in both boxes. The commonest prefabricated "
                           "shell, and the one the bench matters most in."),
    "octagon":   dict(name="Octagon", pts=_octagon(),
                      note="Eight equal sides. Reads as round from a distance and "
                           "holds about 5% more water."),
    "square":    dict(name="Rounded square", pts=_squircle(),
                      note="A square with moulded corners. Treating those corners as "
                           "sharp overstates the water by a few per cent."),
    "rectangle": dict(name="Rectangle", pts=_rect(),
                      note="Straight sides and square corners — usually a gunite spa "
                           "built as part of the pool."),
    "oval":      dict(name="Oval", pts=_ellipse(),
                      note="A full ellipse. Set the long and short measurements "
                           "separately."),
}

def spa_as_json():
    return {k: {"name": v["name"], "note": v["note"],
                "pts": [[round(x, 5), round(y, 5)] for x, y in v["pts"]],
                "unit_area": round(_unit_area(v["pts"]), 4)}
            for k, v in SPA_TEMPLATES.items()}
