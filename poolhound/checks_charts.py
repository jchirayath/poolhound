"""The trend charts: which sources are drawn, and over what window."""

import datetime as dt
import re

from .selftest import check

_BAND = (3.0, 4.5, 6.0)


def _series(n, start_days_ago, step_days=1, v=3.0):
    now = dt.datetime.now()
    return [((now - dt.timedelta(days=start_days_ago - i * step_days)).isoformat(),
             v + i * 0.01) for i in range(n)]


def t_every_source_is_drawn_with_its_own_mark():
    """Three instruments report pH and free chlorine, and one of them was drawn
    nowhere.

    line_chart took exactly one secondary series, named `refs`, so the pod and
    Leslie's were drawn and by-hand results were not — although the entry form
    accepts them, validates them, stores them in manual.csv and audits them.
    Somebody with a test kit and no pod subscription could type a chlorine
    reading and watch it not appear on the chart of chlorine.

    Distinguished by SHAPE, not hue: colour already carries equipment identity
    on this page, and a third colour here would make one hue mean two things.
    That is also why a third source costs nothing in the palette.
    """
    from . import render
    print("\n  charts — one mark per source")

    svg = render.line_chart(
        _series(6, 30), _BAND, title="t",
        extra=[(_series(2, 25, 5), "Leslie's", "s-les"),
               (_series(2, 20, 5), "By hand", "s-man")])
    check("the pod is a line and carries its source", 'class="line s-pod"' in svg, True)
    check("its points do too", 'class="dot s-pod"' in svg, True)
    check("Leslie's is marked and classed", svg.count('class="refmark s-les"'), 2)
    check("and so is by hand", svg.count('class="refmark s-man"'), 2)

    # The shapes differ, which is the channel that survives without colour.
    les = re.search(r'class="refmark s-les"', svg)
    man = re.search(r'class="refmark s-man"', svg)
    check("both are actually drawn", bool(les and man), True)
    d_les = re.findall(r'd="([^"]+)" class="refmark s-les"', svg)
    d_man = re.findall(r'd="([^"]+)" class="refmark s-man"', svg)
    check("and their path data is not the same shape",
          bool(d_les and d_man and d_les[0] != d_man[0]), True)

    # The description has to name them, because it is what a screen reader gets
    # instead of the marks.
    # The <desc> is escaped into the element, so the apostrophe is an entity —
    # asserting on the raw spelling passed on the source with no apostrophe in
    # it and failed on the one that has one.
    import html as _h
    check("the description names Leslie's",
          _h.escape("Leslie's") + " result" in svg, True)
    check("and names by hand", "By hand result" in svg, True)


def t_the_window_offers_every_span_and_shows_one():
    """A control that offers a span must not produce an empty frame without
    saying which span is empty."""
    from . import render
    print("\n  charts — the time window")

    # Readings only in the last few days: the year and all windows have them,
    # the week does too, and a month-old window is the same data.
    html_ = render.ranged_chart(
        lambda since: render.line_chart(
            [(m, v) for m, v in _series(5, 4)
             if not since or render.when(m) >= since], _BAND, title="t"))

    keys = re.findall(r'class="rv" data-range="(\w+)"', html_)
    check("one variant per declared span", keys, [k for k, _, _ in render.CHART_RANGES])
    shown = re.findall(r'data-range="(\w+)"(?!.*?hidden)', html_)
    vis = [k for k in keys
           if re.search(rf'data-range="{k}"(?! hidden)[^>]*>', html_)]
    check("exactly one is visible", len(vis), 1)
    check("and it is the default", vis[0], "all")

    # A window this pool has no readings in says so, rather than borrowing the
    # chart's "no readings collected yet" — which would be false.
    old = render.ranged_chart(
        lambda since: render.line_chart(
            [(m, v) for m, v in _series(3, 300)
             if not since or render.when(m) >= since], _BAND, title="t"))
    check("an empty window names itself", "No readings in the last week" in old, True)
    check("and does not claim there are none at all",
          "No readings collected yet" in old, False)


def t_the_control_is_one_per_section_not_one_per_chart():
    """The trend charts are, in line_chart's own words, "two charts, same x,
    stacked". A window that applied to one of them would break the only
    property that makes reading them together mean anything."""
    from . import render
    print("\n  charts — one window for the stacked pair")

    bar = render.range_bar()
    check("the bar offers every declared span",
          re.findall(r'data-range="(\w+)"', bar), [k for k, _, _ in render.CHART_RANGES])
    check("exactly one is pressed", bar.count('aria-pressed="true"'), 1)
    check("the variants carry no bar of their own",
          "rangebar" in render.ranged_chart(
              lambda since: render.line_chart(_series(3, 5), _BAND, title="t")), False)
    check("and the page's script scopes the window to the section",
          "closest('section')" in render.TEMPLATE, True)


def t_no_window_is_offered_that_the_declaration_does_not_name():
    """CHART_RANGES is the one statement of what the control offers. The bar,
    the variants and the stylesheet all have to agree with it."""
    from . import render, config, style
    import os
    print("\n  charts — the spans are declared once")

    keys = [k for k, _, _ in render.CHART_RANGES]
    check("there is no day window", "day" in keys, False)
    check("and the shortest is a week", keys[0], "week")
    check("all is last and unbounded",
          (keys[-1], render.CHART_RANGES[-1][2]), ("all", None))
    check("the stylesheet dresses the buttons", ".rbtn" in style.CSS, True)
    check("and the source toggles", ".sbtn" in style.CSS, True)
    check("and can hide every source the charts draw",
          all(f".hide-{c} .{c}" in style.CSS
              for c in ("s-pod", "s-les", "s-man", "s-wg")), True)

    site = config.site_dir(render.CFG)
    p = os.path.join(site, "admin.html")
    if not os.path.exists(p):
        return
    page = open(p).read()
    bars = page.count('class="rangebar"')
    if bars:                      # an install with no readings renders no charts
        check("the page carries one window control, not one per chart", bars, 1)
