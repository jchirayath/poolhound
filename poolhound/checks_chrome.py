"""The page chrome: the masthead, the tab bar, and the stylesheet dressing them.

Every case here is something that was actually wrong while this tab restructure
was being made, and every one of them rendered a page that looked plausible in
a screenshot. Four were found only by asking a browser what it had.
"""

import os
import re

from .selftest import check, skipped

# ------------------------------------------------------------------ the tabs

def t_the_default_tab_is_written_not_inherited():
    """One selected button, one visible panel, and the same one.

    The template can carry only one default and there are two builds that want
    different ones, so the public page used to inherit whatever the markup
    said. That was correct by coincidence until the coincidence broke: the tab
    the markup selects is now one the public build cuts out, which would have
    left a page with no selected tab and every panel hidden — blank, valid, and
    serving.
    """
    from . import render
    print("\n  the opening tab — written, and asserted")

    page = ('<nav class="tabs" role="tablist">'
            '<button role="tab" data-tab="home" aria-selected="true">h</button>'
            '<button role="tab" data-tab="info" aria-selected="false">i</button>'
            '</nav>'
            '<div class="panel" id="tab-home" role="tabpanel">H</div>'
            '<div class="panel" id="tab-info" role="tabpanel" hidden>I</div>')

    out = render.set_default_tab(page, "info")
    check("the named tab is the selected one",
          re.findall(r'data-tab="([a-z]+)"[^>]*aria-selected="true"', out), ["info"])
    check("and the one that was selected is not",
          'data-tab="home" aria-selected="false"' in out, True)
    check("its panel is the visible one",
          re.findall(r'id="tab-([a-z]+)"(?![^>]*\shidden)', out), ["info"])
    check("and the panel that was visible is hidden",
          bool(re.search(r'id="tab-home"[^>]*\shidden', out)), True)

    # AND IT IS AN ASSERTION, NOT A HOPE. A tab that is not in the page must
    # stop the build rather than produce the blank page described above.
    try:
        render.set_default_tab(page, "settings")
        raised = ""
    except ValueError as e:
        raised = str(e)
    check("a default that is not in the page is refused",
          "selected=[]" in raised and "visible=[]" in raised, True)


def t_the_nav_order_is_data_and_disagreement_stops_the_build():
    """order_tabs lays the bar out, and refuses to guess.

    The order used to be the sequence the buttons happened to sit in, with the
    public build deleting some of them — so the public order was whatever was
    left over, and Poolhound info came third on the page whose whole job is to
    be read first.
    """
    from . import render
    print("\n  the nav order — laid out from a tuple")

    nav = ('<nav class="tabs" role="tablist">'
           '<button role="tab" data-tab="home" aria-selected="false">h</button>'
           '<button role="tab" data-tab="info" aria-selected="false">i</button>'
           '<button role="tab" data-tab="help" aria-selected="false">?</button>'
           '</nav>')
    out = render.order_tabs(nav, ("help", "home", "info"))
    check("the buttons come out in the order given",
          re.findall(r'data-tab="([a-z]+)"', out), ["help", "home", "info"])

    # A button the order does not name would be silently dropped from the page
    # while remaining in the build; an order naming a button that is not there
    # is a rule protecting nothing. Both are refused.
    for order, why in ((("help", "home"), "a button the order omits"),
                       (("help", "home", "info", "ghost"), "a tab that is not there")):
        try:
            render.order_tabs(nav, order)
            raised = False
        except ValueError:
            raised = True
        check(f"{why} is refused", raised, True)


def t_the_group_divider_follows_the_order_it_divides():
    """The rule between the screens that operate the pool and the ones that
    explain it, drawn from the order rather than written into the markup.

    On the public build the group-start button is the FIRST button, and a
    divider before it would be a line hanging off the left edge of the bar.
    """
    from . import render
    print("\n  the nav divider")

    def nav(*tabs):
        return ('<nav class="tabs" role="tablist">' + "".join(
            f'<button role="tab" data-tab="{t}" aria-selected="false">x</button>'
            for t in tabs) + "</nav>")

    out = render.order_tabs(nav("home", "info", "help"), ("home", "info", "help"))
    before = out.split('data-tab="info"')[0]
    check("a divider is drawn before the group that starts it",
          out.count('class="tabsep"'), 1)
    check("and it is drawn immediately before that button",
          before.rstrip().endswith('<span class="tabsep" aria-hidden="true"></span>\n  <button role="tab"'),
          True)

    out = render.order_tabs(nav("info", "help"), ("info", "help"))
    check("and none when that button is the first one",
          out.count('class="tabsep"'), 0)

    check("the group start is a tab that exists",
          render.TAB_GROUP_START in render.TAB_ORDER, True)
    # AND THE DIVIDER MEANS SOMETHING. Everything after it is the page a
    # stranger gets, in the sequence they get it in — so the tail of the
    # authenticated order is PUBLIC_TABS exactly. Without this the divider is
    # a decorative line: it would still be drawn if a private tab drifted past
    # it, and a reader who had learnt what it separates would be wrong.
    i = render.TAB_ORDER.index(render.TAB_GROUP_START)
    check("everything after it is the public build, in the public order",
          render.TAB_ORDER[i:], render.PUBLIC_TABS)
    check("and nothing public is stranded before it",
          [t for t in render.TAB_ORDER[:i] if t in render.PUBLIC_TABS], [])


def t_the_script_takes_its_default_from_the_page():
    """The tab router used to fall back to the literal string 'home', twice.

    On the authenticated build that reopened Home over the controls the build
    had just selected. On the public build, where there is no Home at all, it
    matched no tab — so every panel was hidden and nothing was selected: a page
    that rendered correctly and went blank a frame later. It looked right in a
    screenshot of the file and wrong only in a browser.
    """
    from . import render
    print("\n  the tab router's fallback")

    i = render.TEMPLATE.find("------ tabs ---")
    j = render.TEMPLATE.find("canvas paint", i)
    router = render.TEMPLATE[i:j]
    check("the tab router is where it is expected", i > 0 and j > i, True)
    # ANY QUOTED TAB NAME AT ALL, not just the `show('home')` shape. The first
    # version of this line matched only a call, and the defect it was written
    # for is `show(hash || 'home', false)` -- so it passed against the live
    # bug and failed only against a COMMENT that happened to quote the older
    # spelling. The breaker in checks_structure said so: it put the real
    # defect back and this case did not notice.
    names = set(render.PUBLIC_TABS) | set(render.PRIVATE_TABS)
    check("it names no tab as a literal fallback",
          sorted({m for m in re.findall(r"""['"]([a-z]+)['"]""", router)
                  if m in names}),
          [])
    check("it reads the selected tab out of the markup instead",
          'aria-selected="true"' in router and "FIRST" in router, True)


def t_the_two_builds_open_where_they_were_told_to():
    """Measured on the pages this build actually writes, not on a fixture."""
    from . import render, config
    print("\n  both builds, as written to disk")

    check("the public default is a public tab",
          render.PUBLIC_DEFAULT_TAB in render.PUBLIC_TABS, True)
    check("and the private default is a private one",
          render.PRIVATE_DEFAULT_TAB in render.PRIVATE_TABS, True)
    check("the landing page is public", "info" in render.PUBLIC_TABS, True)
    check("and Home is not", "home" in render.PRIVATE_TABS, True)
    check("every tab in the nav order is classified",
          sorted(render.TAB_ORDER),
          sorted(set(render.PUBLIC_TABS) | set(render.PRIVATE_TABS)))

    site = config.site_dir(render.CFG)
    for name, want in (("index.html", render.PUBLIC_DEFAULT_TAB),
                       ("admin.html", render.PRIVATE_DEFAULT_TAB)):
        p = os.path.join(site, name)
        if not os.path.exists(p):
            continue                    # nothing rendered yet; bin/render is the gate
        page = open(p).read()
        check(f"{name} opens on {want}",
              re.findall(r'data-tab="([a-z]+)"[^>]*aria-selected="true"', page), [want])
        check(f"{name} shows exactly that panel",
              re.findall(r'id="tab-([a-z]+)"(?![^>]*\shidden)', page), [want])
    if os.path.exists(os.path.join(site, "index.html")):
        page = open(os.path.join(site, "index.html")).read()
        check("and the public nav is in the public order",
              re.findall(r'data-tab="([a-z]+)"',
                         re.search(r'<nav class="tabs".*?</nav>', page, re.S).group(0)),
              list(render.PUBLIC_TABS))


# --------------------------------------------------------------- the masthead

def t_the_public_masthead_does_not_report_on_this_pool():
    """Moving Home behind the sign-in was a decision about whose readings these
    are; the header above it went on printing the same verdict in larger type.

    "NEEDS ATTENTION" and "last reading 8 days ago" are the headline of the
    dashboard that had just been taken away.
    """
    from . import render, config
    print("\n  the masthead — what each build says about the pool")

    site = config.site_dir(render.CFG)
    pub = os.path.join(site, "index.html")
    priv = os.path.join(site, "admin.html")
    if not (os.path.exists(pub) and os.path.exists(priv)):
        skipped("what each build's masthead says about the pool",
                "neither build has been rendered in this tree")
        return
    public, private = open(pub).read(), open(priv).read()
    check("the public masthead carries no overall verdict",
          'class="grade' in public, False)
    check("nor how long ago the pool was last read",
          'class="age"' in public, False)
    check("and the authenticated one carries both",
          'class="grade' in private and 'class="age"' in private, True)


def t_the_favicon_is_one_attribute():
    """The mark, as a data: URL, and the three characters that cannot be left
    alone in one.

    The first version left the SVG's own attribute quotes unencoded, so the
    href ended on the very first one and `">` was printed in the corner of
    every page. `<` and `>` are legal in an attribute value but this build
    reads its own output with regexes that match `<[^>]+>`, to which a stray
    `>` is a tag boundary.
    """
    from . import render, brand
    print("\n  the favicon — one drawing, one attribute")

    href = render._favicon_href()
    check("it is a data URL", href.startswith("data:image/svg+xml,"), True)
    check("it carries no double quote", '"' in href, False)
    check("no raw angle brackets", "<" in href or ">" in href, False)
    check("and the colour literals are encoded", "#" in href, False)

    from urllib.parse import unquote
    svg = unquote(href.split(",", 1)[1])
    check("it is the mark from brand.py, not a second drawing of it",
          brand._POOL.replace('"', "'") in svg, True)


# ------------------------------------------------------------- the stylesheet

# Classes the page uses as handles for its own script — querySelector targets
# and state flags — which have no rule because they are not meant to have one.
# NAMED, NOT COUNTED. A ceiling would let a new unstyled class in as soon as an
# old one was fixed, which is the drift this check exists to stop.
SCRIPT_ONLY_CLASSES = {
    "c-forget", "c-pass", "c-save", "c-store", "c-user",
    "ctl-checking", "ctl-offline", "ctl-setpoint", "ctl-stale-live", "ctl-val",
    "d-close", "dim", "dose-edit", "dstrip", "dt-search", "dt-shown", "dt-truth",
    # The time-window machinery. `.ranged` groups the server-rendered variants
    # of one chart and `.rv` is a variant; the script queries both and toggles
    # the `hidden` attribute, which the browser styles on its own. Neither has
    # anything to draw.
    "ranged", "rv",
    "priv", "role-note", "role-note-what", "set",
    "sp-wait", "spa-design", "sub-adv", "theme-word", "ver",
}

# AND ONE COMPUTED FAMILY. The reading tiles are emitted as `class="tile
# s-{state}"`, so the individual names never appear in the source as literals
# and which of them a page carries depends entirely on the pool: an install
# with no data wears s-unknown on every tile and none of the others. The page
# script reads `.s-low, .s-high` to count what needs attention; the rest are
# hooks with nothing to draw.
#
# Listed as a prefix because that is what it is. Spelling out today's four
# states would be a fifth copy of a set that lives in render.verdict(), and the
# roster would go stale the first time a state was added — which is the failure
# this whole check exists to catch, committed by the check itself.
SCRIPT_ONLY_PREFIXES = ("s-",)


def _unstyled(page):
    """Classes the page wears that no stylesheet in it dresses.

    Reads the rendered page's own <style> blocks rather than style.CSS,
    because the architecture diagrams carry their rules inline and those are
    part of the same document stylesheet as everything else.
    """
    css = re.sub(r"/\*.*?\*/", "",
                 "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", page, re.S)),
                 flags=re.S)
    defined = set(re.findall(r"\.([a-z][\w-]*)", css))
    used = {c for m in re.finditer(r'class="([^"]*)"', page)
            for c in m.group(1).split()
            # Plain CSS identifiers only: `class="` also occurs inside the
            # page's own JavaScript string literals, where the "class" is a
            # fragment of an expression.
            if re.fullmatch(r"[a-z][a-z0-9-]*", c)}
    return sorted(used - defined)


def _rendered(what):
    """The built pages, or a loud skip when this tree has none.

    Returns a list of (name, path). The two cases below used to `continue` past
    a page that was not there, which in the deploy image — where NEITHER is
    built — made them iterate over nothing, assert nothing and print nothing
    at all. That is the quiet version of the failure this whole file exists to
    catch, and it was invisible until the runner learned to distinguish "did
    not run" from "passed".
    """
    from . import config, render
    site = config.site_dir(render.CFG)
    pages = [(n, os.path.join(site, n)) for n in ("index.html", "admin.html")]
    have = [(n, p) for n, p in pages if os.path.exists(p)]
    if not have:
        skipped(what, "neither build has been rendered in this tree")
    return have


def t_a_class_the_page_wears_has_a_rule():
    """THREE COMPONENTS ASKED FOR STYLING THAT DID NOT EXIST, and all three
    shipped looking broken to anybody who opened the tab.

    The Collection tab named `.srcgrid`, `.srch` and `.v-ok`, none of which had
    a rule, and asked for `class="card srcrow"` where `.srcrow` is a two-column
    grid belonging to a different component entirely: the state pill printed on
    top of the source name and every description was thrown against the far
    edge. Its pill asked for `.stpill` where the base class is `.st`. The
    Poolhound info tab then made the same mistake with `.srcnote` within the
    hour.

    A missing rule is invisible to every other check here — the markup is
    valid, the build is clean, the leak list is satisfied — and visible to
    every person who looks at the page.
    """
    from . import render, config
    print("\n  every class the page wears has a rule")

    for name, p in _rendered("every class the page wears has a rule"):
        undressed = [c for c in _unstyled(open(p).read())
                     if c not in SCRIPT_ONLY_CLASSES
                     and not c.startswith(SCRIPT_ONLY_PREFIXES)]
        check(f"{name} wears nothing undefined", undressed, [])

    # And the roster is not allowed to grow stale in the other direction: a
    # name listed here that nothing emits any more is an exemption protecting
    # nothing, and the next real one hides behind it.
    #
    # ASKED OF THE SOURCE, NOT OF TODAY'S RENDER. This compared against the
    # rendered pages, and nine of these classes are emitted only when there is
    # data to emit them for — the dose-edit control, the table filter, the
    # stale-reading slot. On an install with an empty data directory the pages
    # carry none of them and every one of those exemptions read as dead. It
    # failed the first time this machine's data directory was deleted, which
    # is precisely the coupling that deletion existed to end. A class is alive
    # if the code can still produce it; whether this pool happens to have the
    # data that would is a different question and not this check's.
    import importlib, inspect, pkgutil, poolhound
    src = []
    for m in pkgutil.iter_modules(poolhound.__path__):
        if m.name.startswith("checks_"):
            continue        # a check naming a class does not make the page wear it
        try:
            src.append(inspect.getsource(importlib.import_module(f"poolhound.{m.name}")))
        except OSError:
            pass
    body = "\n".join(src)
    check("and no exemption outlives the class it excused",
          sorted(c for c in SCRIPT_ONLY_CLASSES if c not in body), [])


def t_a_drawing_does_not_style_the_page_around_it():
    """A <style> inside an inline SVG is NOT scoped to that SVG.

    It is part of the document stylesheet like any other, so help.py's five
    bare class selectors for the architecture diagrams were global — and `.lbl`
    is also the class on the label span inside every tab button. Every tab in
    the product was being set in the wiring diagram's monospace face, on both
    builds, and it had been for as long as the diagrams have existed.
    """
    from . import render, config
    print("\n  a diagram styles itself and nothing else")

    for name, p in _rendered("a diagram styles itself and nothing else"):
        page = open(p).read()
        # FOUND BY THE ELEMENT THAT CLOSES IT, NOT BY THE SVG AROUND IT.
        # Matching `<svg ... </svg>` and looking inside is the obvious way and
        # it is wrong here: the document stylesheet contains prose about
        # drawings, an opening svg tag written in a comment starts a match, and
        # the whole page stylesheet is then read as a diagram's. That is the
        # fifth time a comment naming a thing has been mistaken for the thing
        # in this project. The diagrams put their rules in a defs block, which
        # no comment does.
        # Each style block matched on its own and then asked what follows it.
        # `<style>(.*?)</style></defs>` in one pattern is wrong for the same
        # reason as the attempt above: the document's own style element does
        # not end in a defs block, so the lazy match walks straight past its
        # closing tag and swallows the whole stylesheet on the way to a
        # drawing's.
        blocks = [m.group(1) for m in re.finditer(r"<style[^>]*>(.*?)</style>",
                                                  page, re.S)
                  if page[m.end():m.end() + 7] == "</defs>"]
        check(f"{name}: the drawings' rules were found", len(blocks) > 0, True)
        bare = []
        for style_block in blocks:
            for rule in re.findall(r"([^{}]+)\{", re.sub(r"/\*.*?\*/", "",
                                                        style_block, flags=re.S)):
                for sel in rule.split(","):
                    sel = sel.strip()
                    if sel.startswith(".") and " " not in sel:
                        bare.append(sel)
        check(f"{name}: no drawing carries an unscoped class rule",
              sorted(set(bare)), [])


def t_the_page_and_the_masthead_describe_the_same_product():
    """Three things, said in both places, because they were cut in both places.

    The masthead tagline originally described only the measurement half and
    left out the two a reader is most likely to have arrived for — reaching
    the controller, and working out the volume. That was fixed by rewriting
    the sentence. The landing page was then written with its own opening
    paragraph, which made the identical cut: "a pool is a chemistry experiment
    that somebody swims in", one purpose of three, on the page whose entire
    job is to say what this is.

    Rewriting prose does not stop that; having one thing to read does. The
    masthead is assembled from PURPOSE and so is the section, and this asserts
    the arithmetic rather than the wording.

    AND A SEPARATE CUT, in the other direction: all three clauses were about
    behaviour, so the only thing on the page before a scroll was a list of
    verbs. Nothing said what the product WAS. info.CATEGORY is that noun, read
    by the browser tab, the meta description and the masthead alike, and
    checked here in all three places for the same reason the three purposes
    are — a category typed twice is a product calling itself two things.
    """
    from . import render, info, config
    print("\n  what this is for — three, in both places")

    check("three purposes, not one", len(info.PURPOSE), 3)
    check("the masthead is assembled from them, not typed",
          render.TEMPLATE.count("@@masthead@@") == 1
          and all(c in info.masthead() for _, _, c, _ in info.PURPOSE), True)
    # AND IT SAYS WHAT THE THING IS BEFORE WHAT IT DOES. The masthead was
    # three clauses of behaviour and no noun: a reader who had not heard of
    # this got a list of verbs with nothing naming the category. One phrase,
    # three readers — the tab, the description and the line — because a
    # product that types its category twice calls itself two things.
    check("the category is named once and read three times",
          info.CATEGORY in info.WHAT_IT_IS
          and info.CATEGORY in info.page_title()
          and render.TEMPLATE.count("@@page_title@@") == 1
          and render.TEMPLATE.count("@@description@@") == 1, True)
    # Each one distinct: a copy-paste that leaves two headings the same is a
    # page claiming three things and naming two.
    check("each has its own heading",
          len({h for _, h, _, _ in info.PURPOSE}), 3)
    check("and its own glyph", len({i for i, _, _, _ in info.PURPOSE}), 3)

    # THE CONTROL PURPOSE CREDITS AqualinkD, BY LINK. Remote control is the
    # one purpose that is mostly somebody else's work, and the paragraph
    # promising it named nothing it was built on. The link is written in the
    # text and made an anchor only AFTER escaping, so the text cannot carry
    # markup of its own through the same door.
    body = [b for i, _, _, b in info.PURPOSE if i == "plug"][0]
    check("the remote-control purpose links AqualinkD",
          '<a href="https://github.com/sfeakes/AqualinkD" rel="noopener">'
          'AqualinkD</a>' in info._prose(body), True)
    check("and a tag in the prose is still escaped",
          info._prose('<img src=x onerror=alert(1)> [x](https://a.example/")'),
          '&lt;img src=x onerror=alert(1)&gt; [x](https://a.example/")')

    site = config.site_dir(render.CFG)
    pub = os.path.join(site, "index.html")
    if not os.path.exists(pub):
        return
    page = open(pub).read()
    import html as _h
    absent = [head for _, head, _, _ in info.PURPOSE if _h.escape(head) not in page]
    check("every one of them is on the public page", absent, [])
    # ESCAPED WITH quote=False, which is how masthead() escapes it: the page
    # carries the apostrophe in "somebody else's" rather than an entity. An
    # earlier version of this line escaped the needle with the default
    # quote=True and never matched anything.
    check("and the masthead block is too", info.masthead() in page, True)
    check("which says what this is, not only what it does",
          _h.escape(info.WHAT_IT_IS, quote=False) in page, True)
    # The tab, too. A title-only surface — a bookmark, a search result, a
    # window list — is the one place a page cannot explain itself twice.
    check("and the browser tab names the product",
          f"<title>{_h.escape(info.page_title(), quote=True)}</title>" in page,
          True)


# ---------------------------------------------------------------- the figures

def t_every_figure_the_landing_page_names_exists():
    """The landing page embeds files that are committed rather than rendered,
    because the container that renders in production has no browser in it.

    So the two can drift in a way nothing else here would notice: a caption
    describing a picture that 404s is a page that still builds, still passes
    the leak list, and shows five broken images to every visitor.
    """
    from . import render, info
    print("\n  the landing page's figures")

    missing = [c for c, *_ in info.FIGURES
               if not os.path.exists(os.path.join(render.CARDS_SRC, c))]
    check("every figure named has a file", missing, [])
    check("every figure links a tab that exists",
          sorted({t for _, _, t, *_ in info.FIGURES if t} -
                 (set(render.PUBLIC_TABS) | set(render.PRIVATE_TABS))), [])
    check("and the pictures are alt-texted",
          all(len(alt) > 40 for _, _, _, alt, _ in info.FIGURES), True)


def t_the_site_document_states_the_tabs_the_code_has():
    """SITE.md prints the four tab tuples. A document that prints a constant is
    a second copy of it.

    This is the failure this project keeps finding, and documentation is where
    it is least visible: nothing breaks, the page is fine, and the only symptom
    is a reader who now believes something untrue. README carried "seven tabs"
    for a while after there were nine, and the acceptance-review harness told
    reviewers to check all seven — which would have skipped two.

    So the tuples are parsed back out of the prose and compared. Not the
    surrounding sentences, which are allowed to be prose; the lists, which are
    facts with an owner.
    """
    import os
    import re
    from . import config, render
    print("\n  SITE.md — the tab tuples it prints are the ones that exist")

    path = os.path.join(config.root(), "SITE.md")
    if not os.path.exists(path):
        skipped("SITE.md's tab tuples against the live ones",
                "no SITE.md in this tree — the deploy image carries no docs")
        return
    src = open(path).read()

    for name in ("PUBLIC_TABS", "PRIVATE_TABS", "TAB_ORDER"):
        m = re.search(name + r"\s*=\s*\(([^)]*)\)", src, re.S)
        if not m:
            check(f"SITE.md states {name}", False, True)
            continue
        doc = tuple(x.strip().strip('"') for x in
                    m.group(1).replace("\n", " ").split(",") if x.strip())
        check(f"SITE.md's {name} is the live one", doc, getattr(render, name))

    for name in ("TAB_GROUP_START", "PUBLIC_DEFAULT_TAB", "PRIVATE_DEFAULT_TAB"):
        m = re.search(name + r'\s*=\s*"([a-z]+)"', src)
        check(f"SITE.md's {name} is the live one",
              m and m.group(1), getattr(render, name))

    # And the files it maps are files. A file map naming something that moved
    # sends a reader to a path that does not exist, which is the one kind of
    # documentation error that wastes time before it is even doubted.
    block = src.split("## Where each panel lives", 1)
    named = re.findall(r"^([a-z_]+\.py)", block[1], re.M) if len(block) > 1 else []
    missing = [f for f in named
               if not os.path.exists(os.path.join(config.root(), "poolhound", f))]
    check("every module the file map names exists", missing, [])
    check("and the map is not empty", len(named) >= 8, True)


def t_the_policy_links_land_on_their_sections():
    """The footer on every tab links to Safety, No support and Privacy.

    Three anchors in a footer, pointing at three section ids in one tab: the
    shape of link that dies quietly when a section is renamed, because the
    router resolves an unknown hash to the front page rather than failing. So
    the ids are read from policies.py by both ends, and this asserts each one
    is a section on BOTH builds — the public one above all, since a privacy
    statement behind a sign-in is not one anybody can read before signing in.
    """
    from . import render, policies, config
    print("\n  safety, support and privacy — linked from every tab")
    check("the tab is public", "policies" in render.PUBLIC_TABS, True)
    ids = (policies.SAFETY_ID, policies.SUPPORT_ID, policies.PRIVACY_ID)
    panel = policies.panel(public=True)
    check("each footer link names a section the panel has",
          [i for i in ids if f'id="{i}"' not in panel
           or f'href="#{i}"' not in policies.footer_links()], [])
    site = config.site_dir(render.CFG)
    for name in ("index.html", "admin.html"):
        p = os.path.join(site, name)
        if not os.path.exists(p):
            continue
        page = open(p).read()
        foot = page[page.find("<footer>"):page.find("</footer>")]
        check(f"{name}: the footer carries all three links",
              all(f'href="#{i}"' in foot for i in ids), True)
        check(f"{name}: and each lands on a section in the policies tab",
              [i for i in ids if not re.search(
                  r'id="tab-policies".*?<section id="' + i + '"', page, re.S)], [])
