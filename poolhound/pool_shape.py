#!/usr/bin/env python3
"""Estimate pool volume by tracing its outline on a photograph.

WHAT A PHOTOGRAPH CAN AND CANNOT TELL YOU
  It cannot tell you scale. A small pool photographed close and a large pool
  photographed far away produce identical pixels, and no amount of knowing the
  phone helps: focal length fixes the camera's ANGLE of view, not its distance
  from the subject. This is monocular scale ambiguity and it has no solution
  from one ordinary photo.

  So one real measurement is required — a single distance, measured once with a
  tape. Click its two ends in the photo, type the number, and every other
  dimension follows. Two minutes of work removes the one unknown that no amount
  of processing can supply.

  EXIF is still read, and it does two useful things. It reports the phone, which
  the page shows back so it is obvious the right photo was loaded. And where the
  photo was taken from a known height looking straight down — an upstairs window,
  a drone — focal length plus that height gives ground scale directly, which is
  offered as an alternative to the tape.

WHY THIS BEATS A TAPE MEASURE ANYWAY, FOR THIS POOL
  Length x width x average depth is exact for a rectangle and wrong for
  everything else. The trade applies a "free-form factor" of about 0.85 to
  curved pools, which is a guess about a shape nobody measured.

  Tracing the actual waterline removes that guess. The shoelace formula gives
  the true enclosed area of whatever curve the pool happens to be, and area is
  the term the volume is most sensitive to. For a curved pool this is the single
  largest accuracy gain available short of draining it.

  What remains uncertain is the floor. A pool's depth profile is hidden under
  the water and is modelled here rather than measured, which is why the result
  carries a stated range instead of a number pretending to be exact.

AND IT IS STILL ONLY A PRIOR
  The fitted acid response measures the volume directly: a known dose of a known
  strength produces a pH change whose size is inversely proportional to the water
  it went into. Once enough doses are logged, that number supersedes this one.
  This exists to make the starting estimate defensible, not final.
"""
import json, math, os

from . import config
from . import locking

GALLONS_PER_CUBIC_FOOT = 7.480519

# ------------------------------------------------------------- refilling it
# A WATER BILL ALMOST NEVER QUOTES A PRICE PER GALLON.
#
# It quotes per 1,000 gallons, or per CCF — a hundred cubic feet, which is 748
# gallons and the unit most US utilities actually bill in. Ask for "the rate
# per gallon" and the number somebody has in front of them is 4.50, so they
# type 4.50 and the calculator says it costs $90,000 to fill a 20,000 gallon
# pool. A field that invites a thousand-fold error is not a field that needs a
# warning, it is a field that needs the unit beside it.
#
# Derived from GALLONS_PER_CUBIC_FOOT rather than written as 748.052: one
# definition of what a cubic foot holds, and the CCF follows from it.
GALLONS_PER_CCF = 100 * GALLONS_PER_CUBIC_FOOT

# (key, what the bill calls it, how many gallons one of them is)
REFILL_RATE_UNITS = (
    ("gal",  "per gallon",          1.0),
    ("kgal", "per 1,000 gallons",   1000.0),
    ("ccf",  "per CCF (748 gal)",   GALLONS_PER_CCF),
)


def refill_cost(gallons, rate, unit="gal"):
    """What it costs to put `gallons` of water in, at `rate` per `unit`.

    Here rather than in the page script because the page is not the only
    plausible caller and because an arithmetic error in a figure somebody uses
    to decide whether to drain a pool is worth a test. Returns None when there
    is nothing to say — no volume yet, or no rate entered — so the caller can
    show a dash instead of a confident $0.00.
    """
    try:
        gallons, rate = float(gallons), float(rate)
    except (TypeError, ValueError):
        return None
    if not (gallons > 0) or rate < 0:
        return None
    per = dict((k, g) for k, _, g in REFILL_RATE_UNITS).get(unit)
    if not per:
        return None
    return gallons / per * rate

# ---------------------------------------------------------------- plane geometry
def shoelace(points):
    """Enclosed area of a simple polygon, in the units the points are given in.

    Sign is dropped, so the outline may be traced clockwise or anticlockwise —
    nobody tracing a pool on a screen should have to think about winding order.
    """
    n = len(points)
    if n < 3:
        return 0.0
    total = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0

def perimeter(points):
    n = len(points)
    if n < 2:
        return 0.0
    return sum(math.dist(points[i], points[(i + 1) % n]) for i in range(n))

def solve(a, b):
    """Gaussian elimination with partial pivoting. Small and self-contained
    because pulling in numpy for one 8x8 solve would make the whole project
    depend on it for a feature most installs will never open."""
    n = len(a)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r == col:
                continue
            f = m[r][col] / m[col][col]
            for c in range(col, n + 1):
                m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]

def homography(src, dst):
    """Map four image points onto four real-world points.

    A pool photographed from the deck is seen in perspective: the far edge is
    compressed and the near edge stretched, so tracing it and measuring the area
    directly can be wrong by tens of percent. Given four points that form a known
    rectangle on the water's plane — the corners of the coping, a deck slab, the
    spa — this recovers the transform that undoes it.

    Returns the 8 coefficients of the projective transform, or None if the four
    points are degenerate (three in a line, or two the same).
    """
    if len(src) != 4 or len(dst) != 4:
        return None
    a, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.append(u)
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y]); b.append(v)
    return solve(a, b)

def apply_homography(h, points):
    out = []
    for x, y in points:
        d = h[6] * x + h[7] * y + 1.0
        if abs(d) < 1e-12:
            return None
        out.append(((h[0] * x + h[1] * y + h[2]) / d,
                    (h[3] * x + h[4] * y + h[5]) / d))
    return out

# --------------------------------------------------------------------- volume
def average_depth(shallow, deep, flat_shallow=0.0, flat_deep=0.0):
    """Mean depth over the surface, given the two ends and how much of the
    surface sits over flat floor at each.

    With no flat sections this is the midpoint, which is exact for a floor that
    slopes evenly from end to end. Real pools usually have a flat shallow area,
    a slope, then a flat deep end, and the midpoint overestimates whenever the
    shallow flat is the larger share — which it almost always is.
    """
    flat_shallow = max(0.0, min(1.0, flat_shallow))
    flat_deep = max(0.0, min(1.0, flat_deep))
    if flat_shallow + flat_deep > 1.0:
        scale = 1.0 / (flat_shallow + flat_deep)
        flat_shallow *= scale
        flat_deep *= scale
    slope = 1.0 - flat_shallow - flat_deep
    return flat_shallow * shallow + flat_deep * deep + slope * (shallow + deep) / 2.0

def volume(area_sqft, shallow_ft, deep_ft, flat_shallow=0.0, flat_deep=0.0,
           ledge_sqft=0.0, ledge_depth_ft=0.0, spa_gallons=0.0):
    """Gallons, from a traced area and a depth profile.

    Steps, benches and a tanning ledge are handled by removing their footprint
    from the main body and adding it back at its own shallow depth, rather than
    by a blanket percentage. A ledge is often 60-80 square feet at ten inches;
    treated as pool floor that is water nobody has, and the error runs one way.
    """
    area_sqft = max(0.0, area_sqft)
    ledge_sqft = max(0.0, min(ledge_sqft, area_sqft))
    body = area_sqft - ledge_sqft
    avg = average_depth(shallow_ft, deep_ft, flat_shallow, flat_deep)
    cubic = body * avg + ledge_sqft * max(0.0, ledge_depth_ft)
    gal = cubic * GALLONS_PER_CUBIC_FOOT + max(0.0, spa_gallons)
    return {"area_sqft": area_sqft, "body_sqft": body, "average_depth_ft": avg,
            "cubic_feet": cubic, "gallons": gal}

# Where the error comes from, as a percentage of the final volume. Area enters
# squared through the scale reference, so a 2% error in one measured distance is
# 4% in the answer — which is why the tape measurement is worth doing carefully
# and the depth profile is worth arguing about.
ERROR_TERMS = {
    "scale":       (4.0, "one measured distance, entering area squared"),
    "tracing":     (3.0, "how closely the clicked outline follows the waterline"),
    "perspective": (6.0, "an angled photo that has not been rectified"),
    "depth":       (8.0, "the floor is modelled, not measured"),
    "features":    (2.0, "steps, benches and the ledge, approximated"),
}

def uncertainty(rectified, has_ledge, from_template=False, from_camera=False):
    """Combined in quadrature, because these errors are independent and adding
    them outright would produce a range so wide it says nothing."""
    terms = dict(ERROR_TERMS)
    if rectified:
        terms["perspective"] = (0.5, "rectified from four known points")
    if not has_ledge:
        terms["features"] = (1.0, "no steps or ledge declared")
    if from_template:
        # Nothing was traced and no photograph was involved, so those errors are
        # gone — but the real pool only approximates the stock outline, and that
        # is a bigger unknown than either of them.
        terms.pop("perspective", None)
        terms["tracing"] = (2.0, "the two dimensions entered, squared into area")
        terms["shape"] = (7.0, "your pool only approximately matches the stock design")
        terms["scale"] = (0.0, "no scale reference needed")
    elif from_camera:
        # Height is now the scale reference, and it enters area squared just as a
        # tape measurement would — but it is estimated rather than measured.
        terms["scale"] = (12.0, "camera height above the water, estimated and "
                                "entering area squared")
        terms["perspective"] = (2.0, "solved from the camera tilt")
        if from_camera is not True and from_camera < 25:
            # A shallow view compresses the far end into very few pixels, so a
            # one-pixel tracing error there is worth feet on the ground.
            terms["perspective"] = (14.0, f"a {from_camera:.0f} degree view is very "
                                          f"oblique — the far end is only a few pixels deep")
    pct = math.sqrt(sum(v[0] ** 2 for v in terms.values()))
    return pct, terms

# -------------------------------------------------------------------- storage
def path(cfg=None):
    return os.path.join(config.data_dir(cfg or config.load()), "pool_shape.json")

def load(cfg=None):
    try:
        with open(path(cfg)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None

def save(record, cfg=None):
    """The traced outline, written so a concurrent save cannot be read half-done.

    This used a FIXED "<path>.tmp" with no lock. Two saves overlapping -- two
    browser tabs, or a save landing while the notifier writes its own state --
    each opened the SAME temp path: one truncated the other's file mid-write and
    whichever replace() ran second won, so os.replace's atomicity guaranteed
    nothing. Measured before this fix: 8 of 40 concurrent saves raised, and 15 of
    40 concurrent reads found JSON that would not parse.

    locking.replace_atomically() uses a unique temp name per writer, and the lock
    serialises the saves themselves. This is the whole record of twenty minutes
    of tracing a photograph, and the photograph is deliberately never stored.
    """
    cfg = cfg or config.load()
    p = path(cfg)
    with locking.exclusive(config.data_dir(cfg), "pool_shape"):
        locking.replace_atomically(p, lambda f: json.dump(record, f, indent=1))
    return p

def _spa_part(shape):
    """The spa alone, from a record that carries one.

    Split out so the spa-only path and the pool-plus-spa path cannot drift: one
    of them computing the bench differently from the other is exactly the bug
    that would go unnoticed, because both answers look plausible.

    Only handles a spa already expressed in feet — a stock shell, or an outline
    drawn at a known diameter. A spa traced in pixels needs the photograph's
    scale, which only exists on the full path.
    """
    spa = shape.get("spa") or {}
    spts = [(float(x), float(y)) for x, y in spa.get("points", [])]
    if len(spts) < 3:
        return None, "give the spa at least three points, or pick a shell"
    if not spa.get("unit_feet"):
        return None, "the spa outline has no scale; trace it on the photograph instead"
    out, err = spa_volume(spts, float(spa.get("depth_ft") or 0),
                          float(spa.get("bench_width_ft") or 0),
                          float(spa.get("bench_depth_ft") or 0),
                          float(spa.get("step_sqft") or 0),
                          float(spa.get("step_depth_ft") or 0))
    if err:
        return None, f"spa: {err}"
    return out, None


def compute(shape):
    """Recompute the volume from stored inputs — the authoritative number.

    The outline is analysed first and the depth model follows from what the
    shape turns out to be, unless the person tracing it has said otherwise. A
    butterfly gets two basins, a plunge pool gets one depth, everything else
    gets a slope integrated over its real area distribution. None of it is
    chosen silently: the classification and its reasoning are returned so the
    page can show what was decided and why.

    The browser computes the same figure live so it moves as the outline is
    dragged, but what gets written is this. A preview and a stored value that
    quietly disagree is the sort of bug found months later by someone wondering
    why the dashboard reports something the tool never showed them.
    """
    # A stock design is just another way of arriving at an outline. It is scaled
    # here and then goes down exactly the same road as a traced one — same axis
    # detection, same slicing, same area-weighted depth. There is deliberately no
    # per-shape volume formula anywhere.
    from_template = False
    if shape.get("template"):
        from . import templates as TPL
        tp = TPL.points_for(shape["template"], float(shape.get("length_ft") or 0),
                            float(shape.get("width_ft") or 0))
        if tp is None:
            return None, ("unknown design, or dimensions outside 0-200 ft")
        pts = tp
        from_template = True
    else:
        pts = [(float(x), float(y)) for x, y in shape.get("points", [])]
    # A spa on its own is a real question, not a malformed pool.
    #
    # Somebody with a spa and no pool — or working the two out one at a time,
    # which is how the calculator now asks for them — has nothing to put in the
    # pool outline. Demanding three points there made the spa impossible to
    # measure alone, and the error said "trace the waterline" about a pool that
    # was never part of the question.
    spa_only = (len(pts) < 3 and (shape.get("spa") or {}).get("points"))
    if spa_only:
        got, err = _spa_part(shape)
        if err:
            return None, err
        return {"gallons": got["gallons"], "pool_only_gallons": 0.0,
                "area_sqft": got["area_sqft"], "average_depth_ft": got["depth_ft"],
                "uncertainty_pct": 8.0,
                "shape_kind": "spa", "shape_why": "Measured as a spa: one depth "
                "throughout, less the bench around the inside.",
                "spa": got}, None
    if len(pts) < 3:
        return None, "trace at least three points around the waterline"

    # Scale straight off the camera, when the photograph carries enough to place
    # the water plane. Expressed as a four-point rectification so it joins the
    # path that already exists rather than becoming a second way to scale.
    cam = shape.get("camera")
    if cam and not shape.get("rectify") and not from_template:
        got = camera_ground_points(
            float(cam.get("img_w") or 0), float(cam.get("img_h") or 0),
            float(cam.get("focal35") or 0), float(cam.get("height_ft") or 0),
            float(cam.get("tilt_deg") or 0), float(cam.get("roll_deg") or 0))
        if got is None:
            return None, ("the camera geometry does not close — check the height and "
                          "tilt, and note that rays above the horizon never meet the water")
        shape = dict(shape)
        shape["rectify"] = {"src": got[0], "dst": got[1]}

    rect = shape.get("rectify")
    rectified = False
    if from_template:
        rectified = False            # already in feet; nothing to undo
    elif rect and rect.get("src") and rect.get("dst"):
        h = homography([tuple(map(float, p)) for p in rect["src"]],
                       [tuple(map(float, p)) for p in rect["dst"]])
        if h is None:
            return None, "the four rectification points are degenerate"
        moved = apply_homography(h, pts)
        if moved is None:
            return None, "the rectification maps a point to infinity"
        # The horizon check on the sampling corners is not enough: those four
        # points can sit safely below the horizon while a TRACED point sits
        # above it, and the homography will map that one to a wild coordinate
        # without complaining. On a shallow oblique view the far end of a pool
        # is exactly where that happens, so the result is checked rather than
        # the inputs.
        xs = [q[0] for q in moved]; ys = [q[1] for q in moved]
        span = max(max(xs) - min(xs), max(ys) - min(ys))
        if not all(math.isfinite(q[0]) and math.isfinite(q[1]) for q in moved):
            return None, "the rectification produced a non-finite point"
        if span > 500:
            return None, (f"the rectified outline spans {span:.0f} ft, which is not a "
                          f"pool — part of it is at or beyond the horizon. Raise the "
                          f"tilt, or scale from a measured distance instead")
        pts, rectified = moved, True
    else:
        ft_per_px = float(shape.get("ft_per_px") or 0)
        if ft_per_px <= 0:
            return None, "set the scale first — click a known distance"
        pts = [(x * ft_per_px, y * ft_per_px) for x, y in pts]

    depths = {k: float(shape.get(k) or 0) for k in
              ("shallow_ft", "deep_ft", "flat_shallow", "flat_deep",
               "uniform_ft", "lobe_near_ft", "lobe_far_ft",
               "ledge_sqft", "ledge_depth_ft", "spa_gallons")}

    # Plausibility, checked on the ANSWER rather than on any one input.
    #
    # Every scaling route fails the same way when its scale is wrong: a mistyped
    # tape distance, a camera height in metres, a very oblique view. The symptom
    # is always an outline that is not a pool. A 3-degree view of this test
    # polygon came back as 42,755 sq ft and 1.6 million gallons and passed every
    # input check, because each input was individually reasonable.
    #
    # So the bound is on the result: an Olympic pool is 1,320 sq ft, and nothing
    # anyone doses by hand is four times that.
    _area = shoelace(pts)
    _xs = [q[0] for q in pts]; _ys = [q[1] for q in pts]
    _span = max(max(_xs) - min(_xs), max(_ys) - min(_ys))
    if _area > 5000 or _span > 250:
        return None, (f"that works out to {_area:,.0f} sq ft and {_span:.0f} ft across, "
                      f"which is not a pool — the scale is wrong. Check the measured "
                      f"distance, or the camera height and tilt.")
    if _area < 20:
        return None, (f"that works out to only {_area:.1f} sq ft — the scale is wrong, "
                      f"or the outline is not closed around the water.")

    info = analyse(pts)
    if not info:
        return None, "could not analyse that outline — are the points in order?"
    model = shape.get("model") or info["model"]
    if model not in ("linear", "lobes", "constant"):
        return None, f"unknown depth model {model!r}"
    if model == "lobes" and not info["waist"]:
        # Asked for two basins on a shape with no neck: fall back rather than
        # invent a waist at the midpoint, and say so.
        model = "linear"

    # Whichever depths the chosen model reads must be present and sane.
    needed = {"linear": ("shallow_ft", "deep_ft"),
              "lobes": ("lobe_near_ft", "lobe_far_ft"),
              "constant": ("uniform_ft",)}[model]
    for k in needed:
        if not (0 < depths[k] <= 30):
            return None, f"{k.replace('_ft','').replace('_',' ')} must be between 0 and 30 feet"

    v, err = volume_from_shape(pts, depths, model=model,
                               flip=bool(shape.get("flip")))
    if err:
        return None, err

    # The spa is measured on its own terms — flat floor, perimeter bench — and
    # then added, because with a spillover the two share one body of water and
    # every dose is diluted by both.
    spa_out = None
    spa = shape.get("spa")
    if spa and spa.get("points"):
        spts = [(float(x), float(y)) for x, y in spa["points"]]
        if len(spts) >= 3:
            # A spa that came from a stock design, or one drawn as a circle of a
            # given diameter, is already in feet. Only pixels need converting.
            if spa.get("unit_feet") or from_template:
                pass
            elif rectified:
                spts = apply_homography(h, spts) or []
            else:
                spts = [(x * ft_per_px, y * ft_per_px) for x, y in spts]
            if len(spts) >= 3:
                spa_out, serr = spa_volume(
                    spts, float(spa.get("depth_ft") or 0),
                    float(spa.get("bench_width_ft") or 0),
                    float(spa.get("bench_depth_ft") or 0),
                    float(spa.get("step_sqft") or 0),
                    float(spa.get("step_depth_ft") or 0))
                if serr:
                    return None, f"spa: {serr}"
                v["gallons"] += spa_out["gallons"]

    pct, terms = uncertainty(rectified, depths["ledge_sqft"] > 0,
                             from_template=from_template,
                             from_camera=(float(cam.get("tilt_deg") or 0)
                                          if cam and not from_template else False))
    naive = naive_gallons(pts, depths) if model != "constant" else None
    v.update({
        "rectified": rectified,
        "perimeter_ft": info["perimeter_ft"],
        "uncertainty_pct": pct,
        "low": v["gallons"] * (1 - pct / 100),
        "high": v["gallons"] * (1 + pct / 100),
        "terms": {k: {"pct": a, "why": b} for k, (a, b) in terms.items()},
        "naive_gallons": naive,
        "from_template": from_template,
        "spa": spa_out,
        "pool_only_gallons": v["gallons"] - (spa_out["gallons"] if spa_out else 0.0),
        "shape_kind": info["kind"],
        "shape_why": info["why"],
        "auto_model": info["model"],
        "model_overridden": bool(shape.get("model")) and shape.get("model") != info["model"],
    })
    # The strip series is large and only the page needs it; keep the summary.
    v["shape"] = {k: info[k] for k in
                  ("kind", "model", "why", "solidity", "compactness",
                   "elongation", "length_ft", "mean_width_ft", "waist")}
    return v, None


# ============================================================================
# SHAPE ANALYSIS
#
# The area of a traced outline is exact for any shape — that is what the
# shoelace formula does, and a butterfly needs no more help than a rectangle.
# Depth is the part that shape actually changes.
#
# A depth profile is one-dimensional: shallow at one end, deep at the other.
# Turning that into a volume means knowing how much WATER SURFACE sits at each
# point along that axis, and for anything other than a rectangle the surface is
# not spread evenly. A butterfly whose shallow lobe is the larger one holds far
# more shallow water than deep, and a model working from "35% of the area is
# flat shallow" cannot know that — it is being told a number the shape could
# have supplied.
#
# So the outline is measured rather than described: find the long axis, slice
# the polygon into strips across it, and weight each strip's depth by its own
# real area. The shape then decides its own average depth, and the only thing
# left for a person to supply is what the photograph genuinely cannot show —
# how deep each end is.
# ============================================================================

def centroid_and_axis(points):
    """Long axis of the outline, by area-weighted second moments.

    Vertex averages are not used: clicking twenty points around one sweeping
    curve and four down a straight edge would drag the centroid into the curve
    and tilt the axis, so a more careful trace would give a worse answer. The
    polygon's own moments do not care how many points describe an edge.
    """
    n = len(points)
    if n < 3:
        return None
    a = 0.0
    cx = cy = 0.0
    for i in range(n):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(a) < 1e-12:
        return None
    a /= 2.0
    cx /= (6.0 * a)
    cy /= (6.0 * a)

    # Second moments about the centroid, again by exact polygon integration.
    ixx = iyy = ixy = 0.0
    for i in range(n):
        x0, y0 = points[i][0] - cx, points[i][1] - cy
        x1, y1 = points[(i + 1) % n][0] - cx, points[(i + 1) % n][1] - cy
        cross = x0 * y1 - x1 * y0
        ixx += (y0 * y0 + y0 * y1 + y1 * y1) * cross
        iyy += (x0 * x0 + x0 * x1 + x1 * x1) * cross
        ixy += (x0 * y1 + 2 * x0 * y0 + 2 * x1 * y1 + x1 * y0) * cross
    ixx /= 12.0; iyy /= 12.0; ixy /= 24.0

    # Principal axis: atan2 of the doubled angle is the standard closed form,
    # but it identifies the principal PAIR, not which of the two is the long one
    # — the branch it lands on depends on the sign of (iyy - ixx), so mirroring
    # a shape can hand back the short axis instead. That is not a rounding
    # error: slicing across the short axis makes every strip span the whole pool,
    # the width series goes flat, a waist becomes undetectable and the area
    # distribution collapses to symmetric. A mirrored butterfly was analysed as
    # "simple" and given a dead-flat 5.00 ft average because of it.
    #
    # So the answer is checked rather than trusted: measure the extent along
    # both candidates and keep the longer.
    theta = 0.5 * math.atan2(2.0 * ixy, iyy - ixx)
    def extent(ang):
        c, s = math.cos(-ang), math.sin(-ang)
        proj = [(x - cx) * c - (y - cy) * s for x, y in points]
        return max(proj) - min(proj)
    if extent(theta + math.pi / 2) > extent(theta):
        theta += math.pi / 2
    return (cx, cy), theta

def convex_hull(points):
    """Andrew's monotone chain."""
    pts = sorted(set(map(tuple, points)))
    if len(pts) < 3:
        return list(pts)
    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2:
                (x1, y1), (x2, y2) = out[-2], out[-1]
                if (x2 - x1) * (p[1] - y1) - (y2 - y1) * (p[0] - x1) > 0:
                    break
                out.pop()
            out.append(p)
        return out
    return half(pts)[:-1] + half(reversed(pts))[:-1]

def clip_slab(points, lo, hi):
    """The part of a polygon between two vertical lines (Sutherland-Hodgman).

    Used to measure how much surface area sits in each slice along the long
    axis. Clipping is exact, so a strip that cuts through a curved edge is
    measured, not approximated by counting vertices.
    """
    def clip(poly, keep, boundary):
        if not poly:
            return []
        out = []
        for i in range(len(poly)):
            cur, prv = poly[i], poly[i - 1]
            cin, pin = keep(cur), keep(prv)
            if cin:
                if not pin:
                    out.append(intersect(prv, cur, boundary))
                out.append(cur)
            elif pin:
                out.append(intersect(prv, cur, boundary))
        return out
    def intersect(p, q, xb):
        dx = q[0] - p[0]
        if abs(dx) < 1e-15:
            return (xb, p[1])
        tt = (xb - p[0]) / dx
        return (xb, p[1] + tt * (q[1] - p[1]))
    poly = clip(list(points), lambda p: p[0] >= lo, lo)
    poly = clip(poly, lambda p: p[0] <= hi, hi)
    return poly

def strip_profile(points, n_strips=48):
    """Slice the outline across its long axis and measure each slice.

    Returns, per strip: its position along the axis as 0..1, its true area, and
    its width across the axis. The width series is what reveals a waist — the
    pinch between the two lobes of a butterfly or a figure-of-eight.
    """
    ca = centroid_and_axis(points)
    if not ca:
        return None
    (cx, cy), theta = ca
    cos_t, sin_t = math.cos(-theta), math.sin(-theta)
    rot = [((x - cx) * cos_t - (y - cy) * sin_t,
            (x - cx) * sin_t + (y - cy) * cos_t) for x, y in points]

    xs = [p[0] for p in rot]
    x0, x1 = min(xs), max(xs)
    span = x1 - x0
    if span <= 0:
        return None
    step = span / n_strips
    strips = []
    for i in range(n_strips):
        lo, hi = x0 + i * step, x0 + (i + 1) * step
        piece = clip_slab(rot, lo, hi)
        a = shoelace(piece) if len(piece) >= 3 else 0.0
        ys = [p[1] for p in piece]
        width = (max(ys) - min(ys)) if ys else 0.0
        strips.append({"t": (i + 0.5) / n_strips, "area": a, "width": width})
    return {"strips": strips, "length": span, "theta": theta,
            "rotated": rot, "centroid": (cx, cy)}

def find_waist(strips):
    """The narrowest interior pinch, if there is one worth calling a waist.

    A butterfly or figure-of-eight has two bulges with a neck between them. The
    test is deliberately strict: the neck must be a genuine interior minimum and
    must be appreciably narrower than the smaller of the two lobes either side.
    Loosening it would find a "waist" in every kidney and split pools that are
    really one basin, which would be worse than not looking.
    """
    n = len(strips)
    if n < 9:
        return None
    w = [s["width"] for s in strips]
    # Ignore the tapering ends, where width falls to zero by definition.
    lo, hi = int(n * 0.2), int(n * 0.8)
    if hi - lo < 3:
        return None
    idx = min(range(lo, hi), key=lambda i: w[i])
    left_peak = max(w[:idx]) if idx > 0 else 0.0
    right_peak = max(w[idx + 1:]) if idx < n - 1 else 0.0
    neck = w[idx]
    if neck <= 0 or min(left_peak, right_peak) <= 0:
        return None
    ratio = neck / min(left_peak, right_peak)
    if ratio > 0.72:
        return None
    return {"index": idx, "t": strips[idx]["t"], "ratio": ratio,
            "neck_width": neck,
            "left_area": sum(s["area"] for s in strips[:idx]),
            "right_area": sum(s["area"] for s in strips[idx:])}

def analyse(points):
    """Describe the traced shape, and say which depth model suits it.

    The classification is reported to the reader rather than applied silently.
    An algorithm that quietly picks a different formula is one nobody can check,
    and the person who traced the outline is the only one who knows whether the
    pinch it found is really a waist or just a set of steps intruding.
    """
    area = shoelace(points)
    peri = perimeter(points)
    if area <= 0 or peri <= 0:
        return None

    hull = convex_hull(points)
    hull_area = shoelace(hull) if len(hull) >= 3 else area
    solidity = area / hull_area if hull_area > 0 else 1.0
    # 1.0 for a circle, lower as an outline gets longer or more indented.
    compactness = 4 * math.pi * area / (peri * peri)

    prof = strip_profile(points)
    waist = find_waist(prof["strips"]) if prof else None
    length = prof["length"] if prof else 0.0
    mean_width = area / length if length > 0 else 0.0
    elongation = length / mean_width if mean_width > 0 else 1.0

    if waist:
        kind, model = "two-lobed", "linear"
        why = (f"A neck {waist['neck_width']:.1f} ft across separates two lobes — "
               f"{waist['ratio']:.0%} of the narrower lobe's width. The floor is still "
               f"taken to fall steadily from one end to the other; what the two lobes "
               f"change is how much water sits at each depth, and that is measured. "
               f"If the lobes are genuinely separate basins at different depths, switch "
               f"to the two-basin model.")
    elif elongation < 1.6 and compactness > 0.62:
        kind, model = "compact", "constant"
        why = ("Round or squarish, with no long axis worth speaking of. A single "
               "depth describes it better than a slope, and a slope invented for "
               "it would just be noise.")
    elif solidity > 0.92:
        kind, model = "simple", "linear"
        why = ("Close to convex — a rectangle, oval or gentle curve. The floor falls "
               "steadily along the long axis, and each slice contributes its own "
               "surface area at its own depth.")
    else:
        kind, model = "free-form", "linear"
        why = (f"Curved and indented: {solidity:.0%} of its own convex hull. "
               f"Depth still runs along the long axis, but the area at each "
               f"point is measured rather than assumed, which is exactly what "
               f"length x width would get wrong.")

    # A waist is worth reporting even when it does not change the model, because
    # it is the one thing a reader can check the algorithm on by looking at their
    # own pool.
    suggest = "lobes" if waist else None

    return {
        "kind": kind, "model": model, "why": why, "suggests": suggest,
        "area_sqft": area, "perimeter_ft": peri,
        "solidity": solidity, "compactness": compactness,
        "elongation": elongation, "length_ft": length, "mean_width_ft": mean_width,
        "hull_area_sqft": hull_area,
        "waist": waist,
        "strips": [{"t": s["t"], "area": s["area"], "width": s["width"]}
                   for s in (prof["strips"] if prof else [])],
    }


# ---------------------------------------------------------------- depth models
def depth_linear(t, shallow, deep, flat_shallow=0.0, flat_deep=0.0):
    """Depth at position t along the long axis, 0 at the shallow end.

    The default is a plain gradual fall from one end to the other, which is what
    most pools are and what this one is. The optional flat sections are given as
    fractions of LENGTH rather than of area, because length is what a person can
    see and pace out; converting that into water is the strip integration's job,
    not the reader's.

    Note what this function does NOT have to know: the shape. A ramp from 3 ft to
    7 ft is the same ramp in a rectangle and in a butterfly. What differs is how
    much surface sits at each point along it, and that is measured from the
    outline rather than assumed here — which is why a simple depth rule and a
    shape-aware volume are not in tension.
    """
    a = max(0.0, min(1.0, flat_shallow))
    b = 1.0 - max(0.0, min(1.0, flat_deep))
    if b <= a:
        return (shallow + deep) / 2.0
    if t <= a:
        return shallow
    if t >= b:
        return deep
    return shallow + (deep - shallow) * (t - a) / (b - a)

def depth_lobes(t, near, far, t_waist, blend=0.12):
    """Two basins joined at a neck, each with its own depth.

    The blend across the neck is a smoothstep rather than a jump: the floor of a
    real pool does not step, and a discontinuity here would put the whole neck's
    area at one depth or the other depending on which side of a strip boundary
    it happened to fall.
    """
    lo, hi = t_waist - blend, t_waist + blend
    if t <= lo:
        return near
    if t >= hi:
        return far
    u = (t - lo) / (hi - lo)
    return near + (far - near) * (u * u * (3 - 2 * u))

def volume_from_shape(points, depths, model=None, flip=False):
    """Volume by integrating depth over the outline's real area distribution.

    This is the part that makes the shape matter. Rather than multiplying one
    average depth by the whole area, each slice of the pool contributes its own
    area at its own depth. On a butterfly whose shallow lobe is the larger one
    that difference is the whole answer: the shallow water is weighted by how
    much of it there actually is.
    """
    info = analyse(points)
    if not info:
        return None, "could not analyse that outline"
    model = model or info["model"]
    strips = info["strips"]
    if not strips:
        return None, "outline too small to slice"

    # t runs along the long axis in an arbitrary direction; the person tracing
    # is the only one who knows which end is shallow.
    def pos(t):
        return 1.0 - t if flip else t

    if model == "constant":
        d_of = lambda t: depths.get("uniform_ft", depths.get("shallow_ft", 0.0))
    elif model == "lobes":
        tw = (info["waist"] or {}).get("t", 0.5)
        d_of = lambda t: depth_lobes(pos(t), depths.get("lobe_near_ft", 0.0),
                                     depths.get("lobe_far_ft", 0.0), tw)
    else:
        d_of = lambda t: depth_linear(pos(t), depths.get("shallow_ft", 0.0),
                                      depths.get("deep_ft", 0.0),
                                      depths.get("flat_shallow", 0.0),
                                      depths.get("flat_deep", 0.0))

    cubic = sum(s["area"] * d_of(s["t"]) for s in strips)
    area = info["area_sqft"]

    # Steps and a ledge sit inside the outline and are shallower than whatever
    # the model puts there, so their footprint is removed from the body and
    # returned at its own depth.
    ledge = max(0.0, min(float(depths.get("ledge_sqft") or 0.0), area))
    if ledge > 0 and area > 0:
        cubic = cubic * (1.0 - ledge / area) + ledge * float(depths.get("ledge_depth_ft") or 0.0)

    gallons = cubic * GALLONS_PER_CUBIC_FOOT + float(depths.get("spa_gallons") or 0.0)
    return {
        "model": model, "shape": info,
        "area_sqft": area, "cubic_feet": cubic, "gallons": gallons,
        "average_depth_ft": cubic / area if area > 0 else 0.0,
    }, None

def naive_gallons(points, depths):
    """What the usual method would have said, for comparison.

    Kept so the page can show the difference rather than assert an improvement.
    A number that claims to be better without showing what it beat is asking to
    be trusted rather than checked.
    """
    info = analyse(points)
    if not info:
        return None
    # THE BOX, not the area. This used to take area_sqft / length_ft as the
    # "width", which makes length x width exactly the TRUE AREA -- so the
    # comparison was area x depth x 0.85 and the 0.85 was being applied on top
    # of a figure that already accounted for the shape. That double-counts:
    # against a kidney filling 80% of its box it reported the usual method as
    # 32% low when the usual method is only about 6% high, which overstates what
    # this tool buys you. A number that claims to be better has to beat the
    # thing people actually do, not a worse version of it.
    #
    # Somebody with a tape measures the long axis and the widest point, which is
    # the ORIENTED bounding box, and the strips already carry the widths.
    length = info["length_ft"]
    widths = [st["width"] for st in (info.get("strips") or []) if st.get("width")]
    box_w = max(widths) if widths else info["mean_width_ft"]
    avg = (float(depths.get("shallow_ft") or 0) + float(depths.get("deep_ft") or 0)) / 2
    # length x width x average depth, with the trade's free-form fudge applied
    # the way the trade applies it: to everything, including a rectangle, which
    # is exactly why it is a guess rather than a measurement.
    return length * box_w * avg * GALLONS_PER_CUBIC_FOOT * 0.85


# ==================================================================== THE SPA
# A spa is not a small pool and should not be modelled as one. It has no slope:
# a flat floor at one depth, and a bench running round the inside wall that a
# person sits on. That bench is most of the difference between area x depth and
# the water actually in there — a 7 ft round spa with a 16 inch bench loses
# roughly a third of its volume to it.
#
# The bench is not asked for in square feet, because nobody knows that number.
# It is asked for as a WIDTH, which is a thing you can measure by looking, and
# its area is derived from the traced perimeter: insetting a shape by w removes
# a ring of area P*w - pi*w^2, exact for a circle and close enough for the
# rounded polygons spas actually are.
#
# Whether the spa counts toward the pool total depends on the plumbing, not on
# geometry. This one spills over continuously, so the two share a body of water
# and every dose is diluted by both — which is why the total is what the
# chemistry uses, and the split is reported so the number can be checked.

def ring_area(perimeter_ft, area_sqft, width_ft):
    """Area of a band of given width running inside a closed outline.

    For a circle this is exactly P*w - pi*w^2. For an octagon or a rounded
    rectangle it is within a percent or two, and it degrades gracefully: a band
    wider than the shape can hold is capped at the whole area rather than going
    negative, which is what the naive formula does when someone types a bench
    wider than the spa.
    """
    if width_ft <= 0 or perimeter_ft <= 0 or area_sqft <= 0:
        return 0.0
    # P*w - pi*w^2 is a parabola: it peaks and then falls, and past the peak it
    # goes negative. Clamping that with max(0, ...) is exactly backwards — a
    # bench wider than the spa reported NO bench at all rather than a spa that
    # is all bench. The honest bound is the inscribed radius, 2A/P for a circle
    # and close for the rounded shapes spas are: at or beyond it, the band has
    # consumed the whole surface.
    inradius = 2.0 * area_sqft / perimeter_ft
    if width_ft >= inradius:
        return area_sqft
    ring = perimeter_ft * width_ft - math.pi * width_ft * width_ft
    return max(0.0, min(area_sqft, ring))

def spa_volume(points, depth_ft, bench_width_ft=0.0, bench_depth_ft=0.0,
               step_sqft=0.0, step_depth_ft=0.0):
    """Gallons in a spa: a flat floor, less the bench people sit on.

    Returns the parts as well as the total, because a spa volume that comes out
    surprisingly small is almost always the bench, and being able to see the
    bench separately is what makes that checkable rather than mysterious.
    """
    area = shoelace(points)
    if area <= 0:
        return None, "trace the spa outline first"
    if not (0 < depth_ft <= 8):
        return None, "spa depth must be between 0 and 8 feet"

    peri = perimeter(points)
    bench = ring_area(peri, area, max(0.0, bench_width_ft))
    step = max(0.0, min(step_sqft, max(0.0, area - bench)))
    floor = max(0.0, area - bench - step)

    cubic = (floor * depth_ft
             + bench * max(0.0, min(bench_depth_ft, depth_ft))
             + step * max(0.0, min(step_depth_ft, depth_ft)))
    return {
        "area_sqft": area, "perimeter_ft": peri,
        "floor_sqft": floor, "bench_sqft": bench, "step_sqft": step,
        "depth_ft": depth_ft, "bench_depth_ft": bench_depth_ft,
        "cubic_feet": cubic,
        "gallons": cubic * GALLONS_PER_CUBIC_FOOT,
        "average_depth_ft": cubic / area if area > 0 else 0.0,
        "as_box_gallons": area * depth_ft * GALLONS_PER_CUBIC_FOOT,
    }, None


# ======================================================= SCALE FROM THE CAMERA
# Focal length alone cannot give scale — a small pool close up and a large one
# far away make identical pixels. That is true and it is also not the whole
# story, because the water is a PLANE, and a plane changes everything.
#
# Given where the camera was relative to that plane, every pixel has exactly one
# possible ground position: cast the ray, intersect the plane, done. The camera
# supplies the ray directions through its focal length. What it cannot supply is
# its own height above the water — so that is the one number to type, and it
# replaces walking around with a tape.
#
# Tilt does not have to be typed. Apple writes AccelerationVector into the
# MakerNote: the gravity vector in device coordinates. With the rear camera
# looking along the device's -Z, a phone held level reads (0,-1,0) and one
# pointing straight down reads (0,0,-1), so the downward tilt is asin(-gz).
# Verified against a real iPhone 15 Pro Max file, which read -1.0155 on z for a
# shot taken looking down.
#
# The result is fed through the existing four-point rectification rather than
# becoming a second scaling path: compute where four image corners land on the
# water, and that is a homography like any other.

def camera_ground_points(img_w, img_h, focal35, height_ft, tilt_deg, roll_deg=0.0,
                         corners=None):
    """Where four image points land on the water plane.

    Returns (src_pixels, dst_feet) ready for homography(), or None if the
    geometry does not close — which happens when a ray points at or above the
    horizon and therefore never meets the water at all. That is a real
    condition, not an error to paper over: the top of a photograph taken from
    eye level often IS sky, and pretending it maps to ground would put the far
    end of the pool at an imaginary distance.
    """
    if not (img_w > 0 and img_h > 0 and focal35 > 0 and height_ft > 0):
        return None
    if not (1.0 <= tilt_deg <= 90.0):
        return None

    # 35mm equivalent focal length is defined against a 36mm-wide frame, so the
    # focal length in pixels follows from the image width alone.
    f_px = img_w * focal35 / 36.0
    cx, cy = img_w / 2.0, img_h / 2.0
    th = math.radians(tilt_deg)
    ro = math.radians(roll_deg)
    cos_r, sin_r = math.cos(ro), math.sin(ro)

    if corners is None:
        # Sample well inside the frame: the extreme corners of a wide lens are
        # where distortion is worst and where rays are most likely to be sky.
        corners = [(img_w * 0.15, img_h * 0.55), (img_w * 0.85, img_h * 0.55),
                   (img_w * 0.85, img_h * 0.95), (img_w * 0.15, img_h * 0.95)]

    src, dst = [], []
    for (u, v) in corners:
        x = (u - cx) / f_px
        y = (v - cy) / f_px
        # Undo camera roll so the horizon is level before tilting.
        x, y = x * cos_r + y * sin_r, -x * sin_r + y * cos_r
        # Camera looks along +Z with image y downward; pitch it down by th.
        Yp = -y * math.cos(th) - math.sin(th)
        Zp = -y * math.sin(th) + math.cos(th)
        if Yp >= -1e-6:
            return None                     # at or above the horizon
        t = -height_ft / Yp
        src.append((u, v))
        dst.append((t * x, t * Zp))
    return src, dst

# Which way is UP in the finished picture, expressed in the phone's own axes.
#
# THE VECTOR IS IN DEVICE AXES AND THE PICTURE IS NOT. Apple reports the
# accelerometer in the handset's frame: +X toward the left side, +Y toward the
# bottom, +Z into the screen. The photograph has since been turned upright — by
# the EXIF orientation tag, which is exactly the record of how far the handset
# was rotated when the shutter fired. Read the vector without applying that and
# the roll is out by whatever the rotation was.
#
# For a pool that is ninety degrees. Nobody photographs a pool in portrait, so
# every real photograph took the landscape branch, and the roll came back near
# -90 when the true answer was near zero. Measured on a real iPhone photograph:
# device-frame roll -91.7, actual roll -1.7, and the horizon in the picture
# dead level. The tilt was right all along, because it comes from the Z
# component alone and no amount of rotation about the lens axis changes that.
#
# Each entry is (up, right) as (axis, sign) pairs into (x, y).
_IMAGE_AXES = {
    1: ((0, -1), (1, -1)),      # landscape as shot, nothing rotated
    3: ((0, +1), (1, +1)),      # landscape, upside down
    6: ((1, -1), (0, +1)),      # portrait — the frame this used to assume
    8: ((1, +1), (0, -1)),      # portrait, the other way up
}


def tilt_from_gravity(gx, gy, gz, orientation=6):
    """Downward tilt and roll, in the axes of the UPRIGHT picture.

    The rear camera looks along the device's -Z, so a phone held level reads
    (0,-1,0) and one aimed at the ground reads (0,0,-1). Tilt follows from the
    Z component and is the same whichever way up the handset was.

    Roll is not. It is the angle of gravity within the image plane, so it has
    to be measured in the image's axes rather than the handset's, and
    `orientation` is the EXIF tag that relates the two. The default is 6 —
    portrait — because that is what this function assumed when it had no such
    argument, so an old caller gets exactly its old answer.

    A mirrored or missing orientation returns no roll rather than a guess: zero
    is what the page used before any of this was readable, and a roll nobody
    can verify is the one number here that goes wrong silently, since the page
    shows the tilt for correction and applies the roll without asking.
    """
    n = math.sqrt(gx * gx + gy * gy + gz * gz)
    if n < 1e-6:
        return None
    gx, gy, gz = gx / n, gy / n, gz / n
    tilt = math.degrees(math.asin(max(-1.0, min(1.0, -gz))))

    axes = _IMAGE_AXES.get(orientation)
    if axes is None:
        return {"tilt_deg": tilt, "roll_deg": 0.0}
    (ua, us), (ra, rs) = axes
    comp = (gx, gy)
    up, right = us * comp[ua], rs * comp[ra]
    roll = math.degrees(math.atan2(right, up)) if (abs(up) + abs(right)) > 1e-6 else 0.0
    return {"tilt_deg": tilt, "roll_deg": roll}


# ================================================== READING THE PHOTO ITSELF
# HEIC is what an iPhone writes by default, and browsers are split on it: Safari
# decodes it, Chrome and Firefox do not. A tool that asks for a photograph of a
# pool and then rejects the format that phone took it in is not much of a tool.
#
# So the file is handed to the server, which is running on a Mac and therefore
# has sips — a converter Apple ships with the operating system. That also moves
# EXIF parsing off the browser, where reading Apple's MakerNote meant walking an
# IFD by hand in JavaScript, and onto a machine with real tools.

import base64, os, shutil, subprocess, tempfile

# Pillow's shipped default (89 Mpx) only emits a WARNING; it raises only above
# twice that. Lowered to our own ceiling so the formats image_size() cannot
# parse are refused by the decoder rather than decoded.
try:                                                        # pragma: no cover
    from PIL import Image as _PILImage
    _PILImage.MAX_IMAGE_PIXELS = 40_000_000
except Exception:                                           # noqa: BLE001
    pass                                # no Pillow here; the header guard stands

HEIC_MAGIC = (b"ftypheic", b"ftypheix", b"ftyphevc", b"ftypmif1", b"ftypmsf1",
              b"ftypheim", b"ftypheis", b"ftyphevm", b"ftyphevs")

def is_heic(data):
    head = data[:64]
    return any(m in head for m in HEIC_MAGIC)

def _exif_via_exiftool(path):
    if not shutil.which("exiftool"):
        return {}
    try:
        r = subprocess.run(
            ["exiftool", "-json", "-n", "-Model", "-Make", "-FocalLengthIn35mmFormat",
             "-AccelerationVector", "-Orientation", "-ImageWidth", "-ImageHeight",
             path],
            capture_output=True, text=True, timeout=25)
        if r.returncode != 0 or not r.stdout.strip():
            return {}
        import json as _json
        return (_json.loads(r.stdout) or [{}])[0]
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}

# Apple's MakerNote, read without exiftool.
#
# WHY THIS EXISTS. The tilt of the camera is not typed by anybody who does not
# have to: Apple writes the accelerometer reading into the photo, and
# tilt_from_gravity() turns it into the pitch and roll the ground-plane
# homography needs. But that value lives in the MakerNote, which only the
# exiftool path read — and exiftool is not in the deploy image and is not going
# to be, since it brings perl with it and this image is deliberately small. So
# the automatic tilt had never once run on the server. It was a documented
# degradation ("tilt has to be set by hand"), and the degradation was total: on
# the machine that serves every visitor, it was always by hand.
#
# THE FORMAT. A MakerNote block that begins "Apple iOS\0", then two bytes of
# version, then a byte-order mark, then an ordinary TIFF IFD. The one catch,
# and the reason a generic EXIF reader does not get this for free: the value
# offsets inside that IFD are relative to the START OF THE MAKERNOTE BLOCK, not
# to the TIFF header the rest of the file is measured from. Tag 0x0008 is the
# acceleration vector, three signed rationals, in g.
#
# THIS PARSES BYTES A STRANGER POSTED. /api/photo is one of the two public POST
# routes, so every length here is checked against the buffer before it is used
# and the whole thing is wrapped by its caller. It allocates nothing that the
# header did not already pay for, and it never raises.
_APPLE_MN_MAGIC = b"Apple iOS\x00"
_APPLE_TAG_ACCEL = 0x0008


def apple_acceleration(mn):
    """(x, y, z) in g from an Apple MakerNote block, or None.

    Returns None for anything it does not completely understand rather than
    guessing: a wrong tilt is worse than no tilt, because no tilt asks the
    reader for one and a wrong one silently skews every distance on the photo.
    """
    import struct as _st
    if not isinstance(mn, (bytes, bytearray)) or len(mn) < 16:
        return None
    if not mn.startswith(_APPLE_MN_MAGIC):
        return None
    order = mn[12:14]
    if order == b"MM":
        end = ">"
    elif order == b"II":
        end = "<"
    else:
        return None

    base = 14                       # the IFD begins straight after the header
    if base + 2 > len(mn):
        return None
    (count,) = _st.unpack_from(end + "H", mn, base)
    # A MakerNote with hundreds of entries is not one Apple wrote. The cap is a
    # bound on the loop below, not a judgement about the file.
    if not 0 < count <= 256 or base + 2 + 12 * count > len(mn):
        return None

    for i in range(count):
        off = base + 2 + 12 * i
        tag, typ, n, val = _st.unpack_from(end + "HHII", mn, off)
        if tag != _APPLE_TAG_ACCEL:
            continue
        # 10 is SRATIONAL, 5 is RATIONAL. Gravity is signed, so 10 is what is
        # actually written; 5 is accepted because reading it costs nothing.
        if typ not in (5, 10) or n != 3:
            return None
        # THE OFFSET IS RELATIVE TO THE MAKERNOTE, which is the whole trick.
        if val + 24 > len(mn):
            return None
        out = []
        for k in range(3):
            num, den = _st.unpack_from(end + ("ii" if typ == 10 else "II"),
                                       mn, val + 8 * k)
            if den == 0:
                return None
            out.append(num / den)
        return tuple(out)
    return None


def _maker_note(path):
    """The raw MakerNote bytes, or None. It lives in the Exif sub-IFD, which is
    a separate table from the one getexif() returns."""
    try:
        from PIL import Image as _Image
        with _Image.open(path) as im:
            sub = (im.getexif() or {}).get_ifd(0x8769) or {}
            mn = sub.get(0x927C)
            return mn if isinstance(mn, (bytes, bytearray)) else None
    except Exception:
        return None


def _exif_via_pillow(path):
    """Everything the tracer needs, without a host binary.

    This used to get "the basics but not the MakerNote", which meant no
    automatic tilt anywhere exiftool was absent — that is, in production. It
    now reads the acceleration vector itself and hands it back in exiftool's
    own spelling, so the caller cannot tell which path supplied it and there is
    only one code path downstream to be right.
    """
    try:
        from PIL import Image as _Image
        with _Image.open(path) as im:
            w, h = im.size
            # NOT `getexif() or {}`. An image with no EXIF returns an Exif
            # object that is falsy but still an Exif — and `or {}` swaps it for
            # a plain dict, which has no get_ifd(). The AttributeError was then
            # swallowed by the except below and the whole function returned
            # nothing, so a photograph without metadata lost even its own
            # WIDTH. The old code only ever called .get() and did not notice.
            ex = im.getexif()
            sub = ex.get_ifd(0x8769) or {}
            out = {"ImageWidth": w, "ImageHeight": h,
                   "Model": ex.get(272), "Make": ex.get(271),
                   # 274 is Orientation: how far the handset was rotated when
                   # the shutter fired, which is what relates the accelerometer's
                   # axes to the picture's. Without it the roll is out by that
                   # rotation — ninety degrees, for any photograph of a pool.
                   "Orientation": ex.get(274),
                   "FocalLengthIn35mmFormat": ex.get(41989) or sub.get(41989)}
            mn = sub.get(0x927C)
            g = apple_acceleration(mn) if isinstance(mn, (bytes, bytearray)) else None
            if g:
                # exiftool -n prints it space-separated; the caller already
                # parses that spelling, so it is the one to speak.
                out["AccelerationVector"] = " ".join(f"{v:.6f}" for v in g)
            return out
    except Exception:
        return {}

# What an image actually starts with. Checked because the endpoint is public and
# was accepting anything: posting the bytes "notanimage" came back as
# {"data_url": "data:image/jpeg;base64,bm90YW5pbWFnZQ=="} with HTTP 200 — the
# server relabelling arbitrary input as a JPEG, and the page then drawing a
# blank canvas with no explanation of why.
MAGIC = {
    b"\xff\xd8\xff":        "jpeg",
    b"\x89PNG\r\n\x1a\n":  "png",
    b"GIF87a":              "gif",
    b"GIF89a":              "gif",
    b"BM":                  "bmp",
}

def image_kind(data):
    for sig, kind in MAGIC.items():
        if data.startswith(sig):
            return kind
    if len(data) > 12 and data[4:8] == b"ftyp":
        return "heic"                     # or any other ISO-BMFF; is_heic decides
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None

# The decoded-pixel ceiling for an uploaded photo, in front of every decoder.
# 40 Mpx is well above any phone (a 48 Mpx sensor writes ~12 Mpx by default and
# the tracer downscales to 1600 across regardless) and well under what fits in
# this container. See the comment in prepare_photo for why bytes are not enough.
MAX_PIXELS = 40_000_000


def image_size(data):
    """Width and height from the file header, with no imaging library.

    Needed because the resize step is optional now: where there is no converter
    the dimensions still have to come from somewhere, and the page uses them to
    scale the outline. Only the two formats a phone or a screenshot produces.
    """
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            return (int.from_bytes(data[16:20], "big"),
                    int.from_bytes(data[20:24], "big"))
        if data[:3] == b"\xff\xd8\xff":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1; continue
                m = data[i + 1]
                # SOF0-SOF15, excluding the four that are not frame headers
                if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                    return (int.from_bytes(data[i + 7:i + 9], "big"),
                            int.from_bytes(data[i + 5:i + 7], "big"))
                i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    except Exception:
        pass
    return (None, None)


def _pillow_to_jpeg(src, dst, max_px=None):
    """Convert (and optionally shrink) an image with Pillow. True on success.

    The cross-platform half of this pipeline. Everything else here shells out to
    sips, magick, convert or exiftool — and the server's image has none of them, so
    the resize, the EXIF read and HEIC decoding had never once run on the machine
    that serves the public calculator. It was exercised only on a laptop, which
    is the same way the original `sips` 502 shipped.

    Pillow is a wheel, not an apt tree, which keeps the image close to the size
    the Dockerfile argues for. pillow-heif registers a HEIC opener when present;
    without it HEIC still fails, but with a message that says so.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return False
    try:
        try:
            import pillow_heif                    # registers HEIC with Pillow
            pillow_heif.register_heif_opener()
        except Exception:
            pass
        with Image.open(src) as im:
            # Honour the EXIF orientation tag rather than baking a sideways
            # photo in: the tracer asks people to click along the waterline, and
            # a rotated image makes every one of those clicks wrong.
            im = ImageOps.exif_transpose(im)
            if max_px and max(im.size) > max_px:
                im.thumbnail((max_px, max_px), Image.LANCZOS)   # only ever shrinks
            im.convert("RGB").save(dst, "JPEG", quality=72, optimize=True)
        return os.path.exists(dst)
    except Exception:
        return False

def prepare_photo(data, max_px=1600):
    """Turn whatever was uploaded into something a browser can draw, plus what
    the camera recorded about the moment it was taken.

    Downscaled deliberately. Tracing precision is limited by how steadily
    somebody clicks, not by sensor pixels, and 1600 across keeps the round trip
    small enough to hand back inline. Because focal length in pixels is derived
    from the image width, resizing stays self-consistent: the geometry uses the
    same dimensions the outline was traced on.
    """
    if not data:
        return None, "no image data"
    if len(data) > 60 * 1024 * 1024:
        return None, "image is larger than 60 MB"

    kind = image_kind(data)
    if not kind:
        return None, ("that does not look like an image. JPEG, PNG, GIF, BMP, WebP "
                      "or HEIC from a phone.")

    # PIXELS, NOT BYTES — and read from the header, before anything decodes.
    #
    # The byte cap above bounds the upload and does not bound the DECODE, which
    # is what allocates. A PNG of 170 million white pixels compresses to a few
    # hundred KB, passes the 60 MB test, and expands to roughly 510 MB of RGB
    # in a container whose mem_limit is 512 MB: one request, one OOM kill, from
    # anyone on the internet, because /api/photo is a deliberate public POST.
    # Pillow's own bomb check only WARNS below 179 Mpx, so it does not stop this.
    #
    # image_size() parses the header with no imaging library, which is exactly
    # what a guard in front of the decoder needs. For the formats it cannot read
    # (HEIC, WebP, GIF, BMP) MAX_IMAGE_PIXELS below is the backstop.
    wh = image_size(data)
    if wh and wh[0] * wh[1] > MAX_PIXELS:
        return None, (f"that image is {wh[0]}x{wh[1]} pixels, more than this "
                      f"can decode. Resize it under "
                      f"{MAX_PIXELS // 1_000_000} megapixels and try again.")

    tmpdir = tempfile.mkdtemp(prefix="poolhound-photo-")
    try:
        src = os.path.join(tmpdir, "in.bin")
        with open(src, "wb") as f:
            f.write(data)

        converted = False
        work = src
        if is_heic(data):
            out = os.path.join(tmpdir, "conv.jpg")
            ok = False
            if shutil.which("sips"):
                r = subprocess.run(["sips", "-s", "format", "jpeg", src, "--out", out],
                                   capture_output=True, timeout=60)
                ok = r.returncode == 0 and os.path.exists(out)
            if not ok:
                ok = _pillow_to_jpeg(src, out)          # works on the server too
            if not ok:
                for argv in (["magick", src, out], ["convert", src, out],
                             ["heif-convert", src, out]):
                    if not shutil.which(argv[0]):
                        continue
                    try:
                        r = subprocess.run(argv, capture_output=True, timeout=60)
                    except (OSError, subprocess.SubprocessError):
                        continue
                    if r.returncode == 0 and os.path.exists(out):
                        ok = True
                        break
            if not ok:
                return None, ("this is a HEIC file and there is no converter here. "
                              "Export it as JPEG on the phone, or set Camera > Formats "
                              "to Most Compatible.")
            work, converted = out, True

        # EXIF IS READ FROM THE ORIGINAL. This read the CONVERTED copy, on the
        # stated grounds that "sips carries the tags across, and reading the
        # original would mean a second HEIC parser". Both halves stopped being
        # true when the server learned to convert for itself.
        #
        # The converter here is _pillow_to_jpeg, which saves without an `exif=`
        # argument and therefore carries NOTHING across. So for HEIC — the
        # format an iPhone produces by default — every camera field was read
        # off a file that had none: no model, no focal length, and no
        # acceleration vector, which is the tilt. Not a degradation for the
        # common case; an absence.
        #
        # And there is no second parser: pillow-heif registers HEIC with
        # Pillow, so the original is opened by exactly the code that opens
        # everything else. The converted copy stays as a fallback for the sips
        # path, where Pillow cannot open the original at all.
        #
        # THE SERVED COPY STAYS STRIPPED, DELIBERATELY. It goes back to the
        # browser as a data URL, and "carry the tags across the conversion"
        # would have put the GPS fix of somebody's house into the response of a
        # public endpoint. Reading the original and serving a stripped copy is
        # the combination that is right; the two must not be conflated.
        meta = (_exif_via_exiftool(src) or _exif_via_pillow(src)
                or _exif_via_exiftool(work) or _exif_via_pillow(work))

        # Resize AND re-encode as JPEG. sips -Z alone keeps the input format, so
        # a screenshot came back as a 3.5 MB PNG — which then has to travel as
        # base64 inside a JSON response. The page only ever draws this at 900px.
        # OPTIONAL, because sips is macOS-only and this runs on Linux in a
        # container. It was called unconditionally, so every upload on the
        # server died with FileNotFoundError: 'sips' — a 502 on the public
        # calculator's headline feature, on a path that had only ever been
        # exercised on a laptop.
        small = os.path.join(tmpdir, "small.jpg")
        final = work
        # Only ever SHRINK. `sips -Z` enlarges too, so a 120x80 test image came
        # back as a 1600x1066 JPEG — twenty-eight kilobytes of invented pixels
        # that the page then has to carry as base64. ImageMagick's `>` suffix
        # says the same thing; sips has no equivalent, so the check is here.
        _w, _h = image_size(data)
        too_big = (not _w) or max(_w, _h) > max_px
        for argv in ([] if not too_big else
                    [["sips", "-s", "format", "jpeg", "-s", "formatOptions", "72",
                      "-Z", str(max_px), work, "--out", small],
                     ["magick", work, "-resize", f"{max_px}x{max_px}>", "-quality", "72", small],
                     ["convert", work, "-resize", f"{max_px}x{max_px}>", "-quality", "72", small]]):
            if not shutil.which(argv[0]):
                continue
            try:
                r = subprocess.run(argv, capture_output=True, timeout=60)
            except (OSError, subprocess.SubprocessError):
                continue
            if r.returncode == 0 and os.path.exists(small):
                final = small
                break
        # No host tool did it — which on the server is every time, since the image
        # carries none of them. Pillow needs no subprocess and is the only one of
        # these that is guaranteed present wherever the dependencies installed.
        if too_big and final is work and _pillow_to_jpeg(work, small, max_px):
            final = small

        with open(final, "rb") as f:
            blob = f.read()
        # Un-resized and enormous: the whole thing has to travel as base64
        # inside a JSON response and then sit in the page. Better to say so than
        # to hand the browser 40 MB of data URL and let it die quietly.
        if final is work and len(blob) > 8 * 1024 * 1024:
            return None, (f"that image is {len(blob)/1048576:.0f} MB and this server has "
                          f"no way to shrink it. Export it smaller, or screenshot it.")
        dims = _exif_via_pillow(final) or {}
        if not dims.get("ImageWidth"):
            w, h = image_size(blob)
            if w:
                dims = dict(dims, ImageWidth=w, ImageHeight=h)

        tilt = None
        av = meta.get("AccelerationVector")
        if isinstance(av, str):
            try:
                av = [float(x) for x in av.replace(",", " ").split()]
            except ValueError:
                av = None
        if isinstance(av, (list, tuple)) and len(av) == 3:
            # The orientation comes from the SAME file the vector did, because
            # the two only mean anything together.
            try:
                orient = int(meta.get("Orientation") or 0) or None
            except (TypeError, ValueError):
                orient = None
            tilt = tilt_from_gravity(*[float(x) for x in av],
                                     **({"orientation": orient} if orient else {}))

        def _f(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        return {
            # The MIME has to match the BYTES. It was hard-coded to jpeg, so an
            # unresized PNG went out labelled as a JPEG — browsers sniff and
            # render it anyway, which is exactly why it survived unnoticed.
            "data_url": ("data:image/" + ({"jpeg": "jpeg", "png": "png", "gif": "gif",
                                           "bmp": "bmp", "webp": "webp"}.get(
                             image_kind(blob) or "", "jpeg"))
                         + ";base64," + base64.b64encode(blob).decode()),
            "width": dims.get("ImageWidth"), "height": dims.get("ImageHeight"),
            "model": " ".join(str(meta.get(k)) for k in ("Make", "Model")
                              if meta.get(k)).strip() or None,
            "focal35": _f(meta.get("FocalLengthIn35mmFormat")),
            "tilt_deg": round(tilt["tilt_deg"], 1) if tilt else None,
            "roll_deg": round(tilt["roll_deg"], 1) if tilt else None,
            "converted_from_heic": converted,
            "bytes": len(blob),
        }, None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
