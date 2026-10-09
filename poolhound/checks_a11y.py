#!/usr/bin/env python3
"""What assistive technology actually receives.

Every case here is a defect that was MEASURED in a browser on the deployed
build, with Playwright's own accessible-name computation rather than by reading
the markup — which is the only way any of these could have been found, because
all seven look correct on screen. The numbers in each docstring are what that
run reported.

These are the pure, cheap half. The half that needs a browser is still a browser
job: `ariaSnapshot()` on the control grid, and `document.activeElement` after
pressing Calculate.
"""
import re

from .selftest import check


def t_no_two_elements_answer_to_the_same_id():
    """An id is a name, and two things with one name is an ambiguity the page
    resolves by guessing.

    The Poolhound info tab shows the same architecture figure Help does —
    deliberately, because a second simplified drawing of the same architecture
    is a second thing that can be wrong, and this product has already shipped
    two diagrams that disagreed with their own config table. But both panels
    are in one file, so the figure had to be asked for an id prefix. Without
    one the page carries two `ad-t` titles, and `aria-labelledby="ad-t ad-d"`
    then names whichever the browser reaches first; the arrow markers, which
    are referenced as `url(#ph-ar-a)`, resolve the same way.

    Measured across the whole rendered page rather than inside the figure,
    because the collision is between components that know nothing about each
    other — which is exactly the kind neither component's own tests can see.
    """
    import collections
    import os
    import re
    from . import config, render
    print("\n  ids — no two elements share a name")

    site = config.site_dir(render.CFG)
    for name in ("index.html", "admin.html"):
        p = os.path.join(site, name)
        if not os.path.exists(p):
            continue            # nothing rendered yet; bin/render is the gate
        page = open(p).read()
        ids = re.findall(r'\sid="([^"]+)"', page)
        dup = sorted(k for k, n in collections.Counter(ids).items() if n > 1)
        check(f"{name}: every id is unique", dup, [])

    # And the figure that forced this actually got its prefix, on the build
    # that carries both copies.
    from . import help as helpdoc
    one, two = helpdoc._arch_simple(), helpdoc._arch_simple(idp="x-")
    check("the drawing takes an id prefix",
          'id="x-ad-t"' in two and 'id="ad-t"' in one, True)
    check("and its internal references move with it",
          "url(#x-ph-ar-a)" in two and "url(#ph-ar-a)" not in two, True)


def t_control_names_come_from_the_device_vocabulary():
    """Seven identical pairs of `button "On"` / `button "Off"`.

    MEASURED, before: Playwright's name computation on `#tab-control .ctlgrid`
    returned `button "On"`, `button "Off"` seven times over, and the four
    setpoint fields as `spinbutton "60–104"` (twice), `"34–42"` and `"0–100"` —
    the range hint, which is the one part of the field that says nothing about
    what it controls. Two of those were the pool and the spa heater. This is the
    tab that starts a gas heater.

    MEASURED, after: 7 groups and 14 buttons each carrying the circuit's name,
    and four spinbuttons named "<device> — new setpoint, <range>".

    The name is taken from commands.label_for(), which is the vocabulary the server
    and the agent validate against, so a device added there cannot ship with a
    button that says only "On" — and an owner who renames a circuit under
    [circuits] renames what the screen reader says too.
    """
    from . import commands as CMD, panels
    print("\n  control cards — every button says which circuit")

    html = panels.control_panel()
    missing = []
    for dev in list(CMD.SWITCHES) + list(CMD.SETPOINTS):
        if dev == "SWG/Boost":
            continue            # deliberately not offered as a button
        lbl = CMD.label_for(dev)
        # The card is a named group AND each control carries its own name. Both,
        # because arriving at the group and landing on one button out of context
        # are different journeys.
        if f'aria-labelledby="ctl-n-{panels._slug(dev)}"' not in html:
            missing.append(f"{dev}: card is not a named group")
        if f'>{lbl}</b>' not in html:
            missing.append(f"{dev}: the <b> the group is named by is not there")
        if f'aria-label="Turn the {lbl} ' not in html and \
           f'aria-label="Set the {lbl}"' not in html:
            missing.append(f"{dev}: its buttons carry no name")
    check("every device on the tab names itself on its own controls", missing, [])

    # And the ids are usable: two devices carry a slash in the id the agent
    # knows them by, which is legal in an id attribute and a syntax error in the
    # CSS selector the next reader will reach for.
    check("a device id with a slash becomes a usable id fragment",
          panels._slug("SWG/Percent"), "swg-percent")
    check("and two different devices do not collide",
          panels._slug("SWG/Percent") == panels._slug("SWG/Boost"), False)


def t_charts_are_not_announced_as_their_own_tick_labels():
    """A chart's accessible name was every tooltip in it, run together.

    MEASURED, before: 20 `svg[role=img]` on the public page, 3 named — the Help
    diagrams, which already did it correctly. The free-chlorine trend computed a
    name beginning "target 2.90.84.17.3" and continuing through both labs'
    dated readings; the six lab-history panels announced as bare number strings
    that never said which measure they were; the 30 day strips shared one
    468-character name and not one of them contained its date.

    MEASURED, after: 41 of 41 named on the authenticated page, each with a
    title and a one-sentence desc, and every strip naming its own day.

    An svg with no aria-labelledby falls back to its text content, which for
    these is the axis ticks and every point tooltip. The tie is what stops that.
    """
    from . import render
    print("\n  charts — a name, not a recital of the tooltips")

    lab, names = render.name_svg("A title", "A description.")
    check("the tie names both elements", bool(re.fullmatch(
        r' aria-labelledby="cx\d+t cx\d+d"', lab)), True)
    check("and the elements it names are there",
          bool(re.search(r'<title id="cx\d+t">A title</title>'
                         r'<desc id="cx\d+d">A description\.</desc>', names)), True)

    # UNIQUE PER SVG. Two charts sharing a title id would give the second one
    # the first one's name — silently, and only for people who cannot see that
    # the pictures differ.
    a, _ = render.name_svg("x", "y")
    b, _ = render.name_svg("x", "y")
    check("two charts never share an id", a == b, False)

    # A title that carries markup characters must not become markup.
    _, esc = render.name_svg('a "<b>" & more', "d")
    check("a title is escaped into the element", "<b>" in esc, False)

    # And the strip names the DAY, which is the fact all thirty of them lacked.
    s = render.strip({"date": "2026-09-14", "segs": [], "chem": [],
                      "hours": {"pump": 6.5, "spa": 0, "sheer": 0}})
    check("a day strip names its own date", "Monday 14 September" in s, True)
    check("and says what ran, in the figures printed beside it",
          "pump 6.5h" in s, True)
    check("and the tie is on the svg element itself",
          bool(re.search(r'<svg[^>]*role="img"[^>]*aria-labelledby=', s)), True)


def t_status_messages_and_tabs_are_wired():
    """Nothing on either page was a live region, and no tab named its panel.

    MEASURED, before, on both builds:
    `document.querySelectorAll('[aria-live],[role=status],[role=alert],output')`
    matched ZERO elements, while every form on the page reports through a .fmsg
    span — "logged — reloading", "saved as the pool volume", or why a write was
    refused. And all seven tabs carried aria-controls of nothing, with no panel
    carrying aria-labelledby.

    MEASURED, after: 22 status regions on the authenticated page, 13 on the
    public one, and 7 of 7 tabs tied to their panels both ways.

    Derived from the class and the role the page already carries rather than
    from a list, and applied after the public build's excisions, so a tab whose
    panel was cut out points at nothing rather than at a missing id.
    """
    from . import render
    print("\n  a11y_wiring() — status regions and the tab/panel tie")

    page = ('<button role="tab" data-tab="home">H</button>'
            '<button data-tab="settings" role="tab">S</button>'
            '<button role="tab" data-tab="gone">G</button>'
            '<button class="btn">Save</button>'
            '<div class="panel" id="tab-home" role="tabpanel">a</div>'
            '<div class="panel" id="tab-settings" role="tabpanel" hidden>b</div>'
            '<span class="fmsg" id="m1"></span>'
            '<span class="fmsg bad">no</span>'
            '<div class="f f-sm fmsg" role="note">already spoken for</div>')
    out, n_status, n_tabs = render.a11y_wiring(page)
    check("every .fmsg without a role becomes a status region", n_status, 2)
    check("and one that already declares a role is left alone",
          'role="note"' in out and out.count('role="status"'), 2)
    check("both tabs with a panel are tied", n_tabs, 2)
    check("the tab is tied to its panel",
          'aria-controls="tab-home"' in out, True)
    # ATTRIBUTE ORDER IS NOT SOMETHING AN AUTHOR THINKS ABOUT. The same lesson
    # declared_tabs() records: a rule that requires role and data-tab adjacent
    # and in one order silently skips the tab written the other way round.
    check("including the one whose attributes are the other way round",
          'aria-controls="tab-settings"' in out, True)
    check("and the panel names the tab back",
          'id="tab-settings" aria-labelledby="tabbtn-settings"' in out, True)
    # THE PUBLIC BUILD CUTS PANELS OUT. A tab pointing at an id that is not in
    # the file is worse than no tie: it is a promise the document cannot keep.
    check("a tab whose panel this build cut out is not tied",
          'aria-controls="tab-gone"' in out, False)
    check("and an ordinary button is untouched",
          '<button class="btn">Save</button>' in out, True)

    # The build asserts both are non-zero, because a regex that stops matching
    # is invisible on screen and shows up only in a screen reader.
    empty, s0, t0 = render.a11y_wiring("<p>nothing here</p>")
    check("a page with neither reports zero rather than pretending", (s0, t0), (0, 0))


def t_the_attention_dot_is_not_colour_alone():
    """.tdot.warn and .tdot.bad differed in `background` and in nothing else.

    MEASURED, before: comparing the two computed styles in a browser, property
    by property, the ONLY difference was background-color. The severity was
    otherwise carried by a `title` tooltip, which a keyboard user never opens
    and a touch user cannot — so a reader who cannot separate amber from red saw
    "something" with no way to find out which.

    MEASURED, after: 24 computed properties differ that are not colours —
    border widths, styles and radii — rendering as a filled square against a
    hollow circle. Same footprint, so the tab strip does not reflow when a
    reading crosses a band.

    This project's rule is that every state ships its word. The word is in the
    tab's aria-label; the shape is the channel that survives on a phone.
    """
    from . import style
    print("\n  the tab attention dot — shape as well as hue")

    def decls(sel):
        m = re.search(re.escape(sel) + r'\s*\{([^}]*)\}', style.CSS)
        return {k.strip(): v.strip() for k, v in
                (d.split(":", 1) for d in m.group(1).split(";") if ":" in d)} if m else {}

    bad, warn = decls(".tdot.bad"), decls(".tdot.warn")
    check("both severities are still defined", bool(bad and warn), True)
    non_colour = {k for k in set(bad) | set(warn)
                  if bad.get(k) != warn.get(k) and k != "background"}
    check("they differ in something that is not the fill colour",
          bool(non_colour), True)
    # And the shared rule keeps them the same size, or the strip jumps.
    base = decls(".tdot")
    check("the two are the same size whichever applies",
          (base.get("width"), base.get("height"), base.get("box-sizing")),
          ("8px", "8px", "border-box"))


def t_section_icon_fallback_is_a_measured_number():
    """Half the section headings silently took the generic glyph.

    icons.SECTION_ICONS is 21 hand-maintained heading substrings and icon_for()
    answers "list" for anything it does not recognise, so a heading that matches
    nothing is indistinguishable from one deliberately given the list glyph and
    the miss is silent.

    MEASURED against the pages this build writes: 23 of 45 headings on the
    authenticated page and 14 of 30 on the public one match no key at all. That
    is the number this case exists to state out loud and to stop getting worse —
    a heading added without a mapping now fails here, naming itself, rather than
    quietly joining the majority.

    The table is READ, never copied: a second list of substrings beside the
    first is the defect this project keeps finding.
    """
    from . import render
    from .icons import SECTION_ICONS
    print("\n  section icons — the fallback rate, stated")

    matched, uncovered = render.section_icon_coverage(
        '<section><h2>What to do</h2>'
        '<section><h2>Lab history</h2>'
        '<section><h2>A heading nobody mapped</h2>')
    check("a mapped heading counts as covered", matched, 2)
    check("and an unmapped one is named rather than absorbed",
          uncovered, ["A heading nobody mapped"])
    # An <h2> carrying its icon and a <span>, which is how sections() rewrites
    # them, must still be read as its own title.
    m2, u2 = render.section_icon_coverage(
        '<h2><svg aria-hidden="true"><path d="M0 0"/></svg><span>Lab history</span></h2>')
    check("a heading that has already been given its icon still matches", (m2, u2), (1, []))
    check("the table is the one in icons.py, not a copy",
          any(k == "lab history" for k, _ in SECTION_ICONS), True)

    # THE CEILING IS NOW ZERO, on the pages this build actually writes. Read
    # from disk rather than rendered here: build() is the thing that assembles
    # them, and a case that re-renders would be measuring its own arrangement
    # instead.
    #
    # It was 14 and 23 — the measured fallback rate at the time, recorded so it
    # could not get worse. Every one of those headings now has a mapping, and
    # four glyphs that did not exist were added rather than forcing a
    # near-match: an icon that means the wrong thing is read before the label
    # beside it is. A ceiling well above the actual is slack a regression hides
    # in, so it tracks the truth.
    import os
    from . import config
    site = config.site_dir(render.CFG)
    worse = []
    for name, ceiling in (("index.html", 0), ("admin.html", 0)):
        p = os.path.join(site, name)
        if not os.path.exists(p):
            continue            # nothing rendered yet; bin/render is the gate
        with open(p) as f:
            _m, un = render.section_icon_coverage(f.read())
        if len(un) > ceiling:
            worse.append(f"{name}: {len(un)} heading(s) take the generic glyph, "
                         f"ceiling is {ceiling} — {sorted(set(un))[:4]}")
    check("every heading on both builds has a mapping", worse, [])

    # AND THE TABLE CANNOT SHADOW ITSELF. Matching is first-substring-wins, so
    # a broad key added above a narrow one silently steals it — the mapping
    # would still be "covered" and would draw the wrong picture, which is the
    # failure this whole case is about, one level down.
    from .icons import icon_for as _for, _PATHS
    shadowed = [k for k, want in SECTION_ICONS if _for(k) != want]
    check("no mapping is shadowed by an earlier, broader one", shadowed, [])
    check("and every mapping names a glyph that exists",
          sorted({n for _, n in SECTION_ICONS} - set(_PATHS)), [])
