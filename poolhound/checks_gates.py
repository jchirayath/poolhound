"""What the gates LOOK AT — the half of a check nobody re-reads.

WHY THIS FILE EXISTS

A mutation review injected a defect under each of the three gates and watched
them pass. The arithmetic in all three was sound every time; what was wrong was
uniformly the INVENTORY — the list of things the gate walks. A check is a
question asked of a set, and a question asked of the wrong set answers about
the wrong thing while printing the same green line.

Four demonstrations, each reproduced as a case below:

  * `/api/all-rows` added to do_GET and to nothing else answered 200 with the
    payload, to an anonymous caller, with no token — and bin/selftest, bin/render
    and bootstrap-server.sh --check all exited 0. The route inventory read
    do_POST, so its scope was "every WRITE route", and read routes are the half
    that hands data OUT.
  * Misspelling both halves of the pool-shape lock-out selector passed every
    gate: the case asserted the selector sets match access.NEEDS, are disjoint
    and are non-empty, and a selector that matches NOTHING satisfies all three.
    The page then iterated zero elements and both Save buttons stayed live for
    a `view` reader.
  * Deleting a collector's line from the status share made it vanish from the
    check rather than fail it, because the check iterated the files that
    happened to be there. `fails=0`, and the deploy printed "nothing lost."
  * The proxy check asserted every private route IS gated and never that the
    two deliberately public ones are NOT, so widening the pool-shape matcher —
    the exact regression the vhost warns about three lines above it — put the
    public volume calculator behind Entra and the gate reported green.

Every case here was demonstrated by injecting the defect, watching the gate
fail, and removing the injection. A gate that cannot be shown to fail is not a
gate.
"""
import glob
import os
import re

from .selftest import check, skipped


# ------------------------------------------------------------ a CSS selector
# resolved against real markup.
#
# WHY THIS AND NOT A REGEX. The defect being caught is a selector that names no
# element, and every cheap approximation of "does this selector match" answers
# yes for a selector that matches nothing: searching for the id text finds it in
# the page's own copy of access.ui_selector(), and searching for `id="x"` cannot
# express `#chem-form button`, which is what eight of the twelve entries are.
#
# UNSUPPORTED SYNTAX RAISES rather than returning zero. A `>` or a `:not()`
# arriving in ROLE_UI would otherwise be reported as "this selector names no
# element" — the defect's own symptom — and the next person would go looking at
# the page instead of at this parser.
from html.parser import HTMLParser

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}


class _Node:
    __slots__ = ("tag", "attrs", "kids")

    def __init__(self, tag, attrs):
        self.tag, self.attrs, self.kids = tag, attrs, []

    def walk(self):
        for k in self.kids:
            yield k
            yield from k.walk()


class _Tree(HTMLParser):
    """A tolerant element tree. Text is discarded: nothing here matches on it."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node(None, {})
        self._stack = [self.root]

    def handle_starttag(self, tag, attrs):
        n = _Node(tag, {k: (v or "") for k, v in attrs})
        self._stack[-1].kids.append(n)
        if tag not in _VOID:
            self._stack.append(n)

    def handle_startendtag(self, tag, attrs):
        self._stack[-1].kids.append(_Node(tag, {k: (v or "") for k, v in attrs}))

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return


_PART = re.compile(r"""
    (?P<tag>[a-zA-Z][a-zA-Z0-9-]*)?
    (?P<rest>(?:\#[A-Za-z0-9_-]+|\.[A-Za-z0-9_-]+|\[[A-Za-z0-9_-]+(?:=[^\]]*)?\])*)
    $""", re.X)


def _compound(part):
    """(tag, ids, classes, attrs) for one compound selector, or raise."""
    m = _PART.match(part)
    if not m:
        raise ValueError(f"unsupported selector syntax: {part!r}")
    ids, classes, attrs = [], [], []
    for tok in re.findall(r"\#[A-Za-z0-9_-]+|\.[A-Za-z0-9_-]+|\[[^\]]*\]",
                          m.group("rest") or ""):
        if tok[0] == "#":
            ids.append(tok[1:])
        elif tok[0] == ".":
            classes.append(tok[1:])
        else:
            k, _, v = tok[1:-1].partition("=")
            attrs.append((k, v.strip("\"'") if _ else None))
    return (m.group("tag"), ids, classes, attrs)


def _hits(node, comp):
    tag, ids, classes, attrs = comp
    if tag and node.tag != tag:
        return False
    if ids and node.attrs.get("id") not in ids:
        return False
    have = set((node.attrs.get("class") or "").split())
    if any(c not in have for c in classes):
        return False
    for k, v in attrs:
        if k not in node.attrs:
            return False
        if v is not None and node.attrs[k] != v:
            return False
    return True


def count_matches(html, selector):
    """How many elements in `html` this selector selects.

    Descendant combinators only, which is all ROLE_UI uses; anything else
    raises rather than quietly answering zero.
    """
    if not selector.strip():
        return 0
    if re.search(r"[>+~:()]", selector):
        raise ValueError(f"selector uses syntax this matcher does not "
                         f"implement: {selector!r}")
    parts = [_compound(p) for p in selector.split()]
    t = _Tree()
    t.feed(html)
    here = [t.root]
    for i, comp in enumerate(parts):
        nxt = []
        for n in here:
            nxt += [d for d in n.walk() if _hits(d, comp)]
        # Dedup by identity; a node reachable from two ancestors is one element.
        seen, uniq = set(), []
        for n in nxt:
            if id(n) not in seen:
                seen.add(id(n))
                uniq.append(n)
        here = uniq
        if not here:
            return 0
    return len(here)


# ----------------------------------------------------------- the GET routes
#
# THE ONE DELIBERATE EXCEPTION, and why it is one. Everything else do_GET names
# has to be in access.NEEDS.
#
# Held here rather than in server.py because this is a statement about what the
# GATE excuses, and an excuse that lives beside the thing it excuses is an
# excuse that gets extended by whoever is adding the route.
PUBLIC_GET = {
    "/api/health": "the handshake. It is what tells an anonymous reader the "
                   "page is read-only, and it withholds the token, the role "
                   "and every operational field unless signed_in() — so it is "
                   "public by design and the decision is enforced inside it "
                   "rather than by the route table.",
}

# NOT ROUTES, and each for a different reason, so none of them may be lumped in
# with the exception above.
_NOT_A_ROUTE = {
    # The 404 arm: `startswith("/api/")` is the catch-all that refuses anything
    # unrecognised. A prefix is not an endpoint.
    "/api/",
    # The Pi's outward SSE link. Authenticated by agent_ok() against a shared
    # secret, not by an identity the proxy vouched for, so access.NEEDS — which
    # is a table about PEOPLE — has nothing to say about it. do_POST's
    # inventory excludes /api/agent/ for the same reason.
    "/api/agent/commands",
}


def get_routes():
    """Every /api path do_GET dispatches, read out of do_GET itself.

    Derived, because a second list is a list that drifts: the write half of
    this inventory was derived from do_POST and correct, and the read half did
    not exist at all.
    """
    import inspect
    from . import server as _srv
    src = inspect.getsource(_srv.Handler.do_GET)
    found = set()
    for raw in re.findall(r'"(/api/[A-Za-z0-9/_-]*)"', src):
        if raw in _NOT_A_ROUTE or raw.startswith("/api/agent/"):
            continue
        # "/api/export/" is the prefix arm of the same route, not a second one.
        found.add(raw.rstrip("/"))
    return found


def t_every_get_route_is_classified():
    """A read route is a route. The inventory only walked the writes.

    MEASURED: a `/api/all-rows` arm added to do_GET and to nothing else — not
    access.NEEDS, not access.ROLE_UI, not the Caddy @private list — answered
    200 with the payload to an anonymous caller carrying no token, while
    bin/selftest, bin/render and bootstrap-server.sh --check all exited 0. The
    only inventory of routes in the suite read inspect.getsource(do_POST),
    so its stated scope, "every write route", was also its real one.

    Reads are the half that hands data OUT. Classifying them here closes the
    other two gates behind it for free: t_role_dimming_is_derived_and_fails_closed
    requires every access.NEEDS route to name its controls, and
    t_caddy_covers_every_private_route requires every one to be gated at the
    proxy — so a GET route reaching NEEDS reaches all three.
    """
    from . import access
    print("\n  routes — the READS are inventoried too, not only the writes")

    routes = get_routes()
    check(f"do_GET dispatches routes this can see ({len(routes)} found)",
          len(routes) >= 3, True)
    check("every /api route do_GET dispatches is classified or publicly excused",
          sorted(routes - set(access.NEEDS) - set(PUBLIC_GET)), [])

    # And the other direction, both ways round, because an exception list is an
    # inventory too and rots the same way.
    check("nothing is excused as public AND gated by access.NEEDS",
          sorted(set(PUBLIC_GET) & set(access.NEEDS)), [])
    check("and every publicly-excused route is still a route do_GET has",
          sorted(set(PUBLIC_GET) - routes), [])

    # The pure calculator routes are POSTs, and the route table's own exception
    # list for writes says which they are. If one is ever offered over GET as
    # well it lands in `routes`, is in neither NEEDS nor PUBLIC_GET, and the
    # assertion above fails — which is the wanted outcome, so this only records
    # that they are deliberately not here yet.
    from . import server as _srv
    check("the pure calculator routes are not GETs",
          sorted(routes & set(_srv.PUBLIC_POST)), [])


# -------------------------------------------------- the lock-out selectors
_PAGES = {}


def _fixture_data(d):
    """The one row a control needs to exist at all.

    /api/chemical/delete's only control is the × on a logged dose, so a render
    against an empty history has nothing for its selector to name and the check
    would report the defect it exists to catch. One dose is the whole fixture:
    everything else ROLE_UI names is page furniture that is always drawn.
    """
    import csv
    from .chemicals import CHEM_COLS
    with open(os.path.join(d, "chemicals.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CHEM_COLS)
        w.writeheader()
        w.writerow({"ts": "2026-09-14T10:00:00-0700", "chemical": "acid",
                    "amount": "16", "unit": "floz", "pct": "31.45",
                    "note": "selftest fixture", "by": "local"})


def _rendered(name):
    """A page rendered from a FIXTURE, into a throwaway directory.

    RENDERED, NOT READ. bin/selftest runs at image build, where no page has
    been rendered and there is no data — so a case that reads site/admin.html
    would skip exactly where the deploy relies on it, and on a workstation it
    would measure a page rendered hours ago against selectors changed since.

    FROM A FIXTURE, not from this install's history, for the reason the suite's
    own docstring gives about reading files somebody can regenerate: whether a
    control exists would otherwise depend on whether this particular pool
    happens to have a dose logged, so the same code would pass on the server and
    fail in the image. A temp DATA and a temp SITE also mean a check cannot
    write the page the site serves, or a byte of the history.
    """
    import shutil
    import tempfile
    from . import render
    if name in _PAGES:                 # three cases want these; one render each
        return _PAGES[name]
    prev = {k: os.environ.get(k) for k in ("POOLHOUND_SITE", "POOLHOUND_DATA")}
    d = tempfile.mkdtemp()
    site, data = os.path.join(d, "site"), os.path.join(d, "data")
    os.makedirs(site)
    os.makedirs(data)
    _fixture_data(data)
    try:
        os.environ["POOLHOUND_SITE"] = site
        os.environ["POOLHOUND_DATA"] = data
        render.build(public=(name == "index.html"))
        with open(os.path.join(site, name), encoding="utf-8") as f:
            _PAGES[name] = f.read()
        return _PAGES[name]
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        render.reload()               # rebind SITE/DATA to the real config
        shutil.rmtree(d, ignore_errors=True)


def t_role_ui_selectors_name_real_elements():
    """A lock-out selector that matches nothing locks nothing out.

    MEASURED: changing the pool-shape entry so that both of its ids were
    misspelled passed every gate. The existing case asserts the ROLE_UI and
    access.NEEDS route sets agree, that the operate and admin selector sets are
    disjoint, and that neither is empty — and a selector naming no element
    satisfies all three. The page's lock-out pass then ran
    querySelectorAll(...).forEach over an empty list and both Save buttons
    stayed live for a `view` reader, who could redefine the volume every dose
    on the site multiplies through.

    So the selectors are resolved against the authenticated page, which is the
    one the lock-out runs on. bin/contrast already checks the rendered page for
    the three non-colour fallbacks; this is the same move for the same reason —
    a name is only a name until something looks for what it names.
    """
    from . import access
    print("\n  roles — every lock-out selector names something on the page")
    page = _rendered("admin.html")
    for route in sorted(access.ROLE_UI):
        sel = access.ROLE_UI[route]
        if not sel:
            # Deliberately empty: a read route with no control to dim, or the
            # export anchors, which are hidden by class instead. The existing
            # case asserts which routes may be empty; this one only measures
            # the ones that are not.
            continue
        # PER ROUTE, NOT PER COMMA MEMBER. These are written as a blanket
        # "button, input, select" over a form, and a form with no <select> in
        # it is not a defect — #reading-form has none. What must not happen is
        # a route whose controls, all of them together, come to nothing.
        n = sum(count_matches(page, s.strip())
                for s in sel.split(",") if s.strip())
        check(f"{route} names {n} control(s) on admin.html", n > 0, True)

    # AND EVERY ANCHOR IS A REAL ANCHOR. The blanket above passes on one
    # surviving member, so a single misspelled name inside a longer list goes
    # unseen: misspelling only the first of `.cred button, .cred input, .cred
    # select` still dims fifteen controls and leaves the buttons live.
    #
    # THE ANCHOR, NOT THE WHOLE MEMBER. What must exist is the id or class the
    # selector hangs off — it is a name written twice, once here and once in
    # the page, which is the thing a typo destroys. The tag under it may
    # legitimately be absent: #reading-form has no <select> in it and never
    # did, and demanding one would be this check inventing a requirement.
    for route in sorted(access.ROLE_UI):
        for one in [s.strip() for s in (access.ROLE_UI[route] or "").split(",")]:
            anchor = one.split()[0] if one else ""
            if not re.search(r"[#.\[]", anchor):
                continue                     # a bare tag anchors nothing
            check(f"{route}: {anchor} is on admin.html",
                  count_matches(page, anchor) > 0, True)

    # AND THE WHOLE SELECTOR AS THE PAGE USES IT. The page does not call
    # querySelectorAll per route; it calls it once per level with the string
    # ui_selector() builds, and a comma list is only as good as its worst
    # member — but it is that string the browser is handed.
    for level in ("operate", "admin"):
        sel = access.ui_selector(level)
        n = sum(count_matches(page, s.strip())
                for s in sel.split(",") if s.strip())
        check(f"the {level} lock-out covers controls on the page ({n} found)",
              n > 0, True)


# ------------------------------------------------- the proxy, BOTH directions
def _private_block():
    """Caddy's @private path patterns, or None if there is no vhost here."""
    from . import config
    p = config.caddy_vhost()
    if not p:
        return None
    with open(p, encoding="utf-8") as f:
        txt = f.read()
    block = re.search(r"@private \{(.*?)\n\t\t\}", txt, re.S)
    if not block:
        return []
    return [x for m in re.finditer(r"path ([^\n]+)", block.group(1))
            for x in m.group(1).split()]


def t_the_public_routes_are_not_gated():
    """The proxy check only ever asked whether the gate was SHUT.

    MEASURED: widening the matcher to `path /api/pool-shape /api/pool-shape/*`
    — the exact regression the comment three lines above it in the vhost warns
    about — puts /api/pool-shape/compute behind Entra, so the public volume
    calculator stops answering for every anonymous visitor, and the existing
    case reported green. It iterates access.NEEDS and asks "is this one
    gated?", and over-gating cannot fail a question shaped like that.

    Both directions now. `/api/pool-shape` SAVES and must stay gated; its
    /compute sibling is pure and must not be — which is the whole reason the
    matcher carries no trailing wildcard for that one path.
    """
    import fnmatch
    from . import server
    print("\n  proxy — the two public routes are NOT behind the sign-in")
    pats = _private_block()
    if pats is None:
        print("    skip  no Caddy vhost in this checkout")
        return
    check("the @private block still has patterns to read", bool(pats), True)

    # Derived from server.PUBLIC_POST: the one place that decides which routes
    # skip the write guards is the one place that decides which routes the
    # proxy must leave open. Two lists here would be two lists to drift.
    for r in sorted(server.PUBLIC_POST):
        hit = [x for x in pats if fnmatch.fnmatch(r, x)]
        check(f"{r} is left public by the proxy", hit, [])

    # And the SAVE sibling is still shut, so "nothing is gated" cannot pass the
    # assertion above.
    check("/api/pool-shape itself is still gated",
          any(fnmatch.fnmatch("/api/pool-shape", x) for x in pats), True)


# ---------------------------------------------------------- the cron roster
COLLECTORS = {"wg-collect", "leslies", "watch", "render", "backup"}


def cron_collectors(path=None):
    """The collectors deploy/poolhound-cron actually schedules."""
    from . import config
    p = path or os.path.join(config.root(), "deploy", "poolhound-cron")
    if not os.path.exists(p):
        return None
    # SCHEDULE LINES ONLY. The header prose in that file says "collect.sh
    # records each run's exit status", and a regex over the whole file read
    # `records` as a fifth collector — a roster is only as good as the thing it
    # is read out of, which is this module's entire subject.
    out = set()
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.search(r"collect\.sh\s+([a-z-]+)", line)
            if m:
                out.add(m.group(1))
    return out


def t_the_collector_roster_is_declared_by_cron():
    """A collector that stops reporting must not stop being checked.

    MEASURED: deleting one collector's line from the status share — the state a
    collector that has stopped writing its file altogether produces — left
    `fails=0` and the deploy printed "Done — nothing lost." The check iterated
    `/mnt/poolhound/.collector-status/*` and judged whatever was there, so
    absence was silence and the remaining green lines carried the run.

    deploy/poolhound-cron declares exactly four collectors and the check never
    read it. bootstrap-server.sh now derives the roster from that file and fails
    on a rostered collector with no status at all; this case guards the
    derivation, because a roster that comes back empty would restore the defect
    exactly.

    The expected four are written out rather than derived from the same file,
    for the reason t_access gives: a check that reads its answer out of the
    thing it is checking measures nothing.
    """
    from . import config
    print("\n  collectors — the roster comes from cron, not from what turned up")
    got = cron_collectors()
    if got is None:
        skipped("the collector roster, derived from the crontab",
                "no cron file in this tree")
        return
    check("every scheduled collector is found", sorted(got), sorted(COLLECTORS))

    # And the deploy check reads that file rather than carrying its own copy.
    p = os.path.join(config.root(), "deploy", "bootstrap-server.sh")
    if not os.path.exists(p):
        # No deploy/ in the image. Reading the absent script as an empty string
        # turned "not in this tree" into "the script does not do it", and the
        # gate then reported the deploy check as broken on every image build.
        skipped("bootstrap-server.sh deriving the roster from the crontab",
                "no deploy/bootstrap-server.sh in this tree")
        return
    src = open(p, encoding="utf-8").read()
    check("bootstrap-server.sh derives the roster from deploy/poolhound-cron",
          "poolhound-cron" in src, True)
    check("and it judges a rostered collector that filed no status at all",
          "collector_gaps" in src, True)


# ------------------------------------------------------- the suite's own size
def t_the_suite_notices_itself_shrinking():
    """A deleted case is a silent downgrade of the gate.

    MEASURED: deleting t_pending — one of the cases this suite's docstring
    calls out by name as "actually wrong at some point", the one deciding
    whether the page says a heater is ON, OFF or WAITING — left bin/selftest
    exiting 0 with "all checks pass" and nothing in the output stating how many
    cases had run. main() refused only below 20, against a roster of 40, so
    half the suite could go before the floor was touched.

    The floor is now this module-by-module count and the run prints it. Sibling
    checks_*.py files may appear and grow freely — that is what discovery is
    for — but the core file cannot shrink without somebody editing the number
    and saying why.
    """
    from . import selftest
    print("\n  the suite — it states its size, and refuses a shrunken roster")
    n = selftest.core_case_count()
    check(f"the core file still holds its recorded case count ({n})",
          n >= selftest.CORE_CASES, True)
    check("and a roster short of it is refused",
          selftest.roster_is_whole(selftest.CORE_CASES - 1), False)
    check("while the roster as it stands is accepted",
          selftest.roster_is_whole(n), True)


# --------------------------------------------- the never-colour-alone dots
def t_the_tab_dots_are_inventoried():
    """bin/contrast measured text-on-ground and the series, and not this pair.

    MEASURED: the two tab-status dots are 3.96 dE apart under deuteranopia on
    the light palette — against the 15.0 the series must clear, and barely
    above the 2.3 at which two colours become tellable apart at all. They are
    7px circles with no text, so for the most common colour deficiency they
    were one indicator wearing two meanings, and the checker printed "all
    pairings pass" because no entry named them.

    contrast.dot_states() now derives the states from the stylesheet, so a
    third one cannot arrive unmeasured. This case holds the derivation: an
    empty answer would put the inventory back exactly where it was.
    """
    from . import contrast, style
    print("\n  contrast — the tab dots are in the inventory")
    states = contrast.dot_states(style.CSS)
    check("the status-dot states are found in the stylesheet",
          sorted(states), ["bad", "warn"])
    check("and every colour-only pair among them is measured or excused",
          contrast.dot_failures(style.CSS, _dot_pages()), [])


def _dot_pages():
    """Both builds, rendered, for the non-colour channel probe."""
    return {n: _rendered(n) for n in ("index.html", "admin.html")}


def t_identity_is_not_believed_on_network_position_alone():
    """A sibling container must not be able to claim to be the owner.

    poolhound's own source states the weakness and then leaves it standing:
    "these headers are trusted because of DOCKER NETWORK MEMBERSHIP plus Caddy
    stripping them on the one path from outside. Anything that can already open
    a socket to this container can claim to be anybody." Measured on the
    author's stack: eleven sibling containers on the default bridge, and
    neither compose file declares a `networks:` key.

    The secret is opt-in, for the reason access.py's policy is: an upgrade that
    began refusing every identity header would lock the owner out of their own
    pool. So this asserts BOTH halves — that nothing changes when it is unset,
    and that a forged header is refused when it is set.
    """
    import importlib
    import sys as _sys
    print("\n  identity — a sibling container cannot claim to be anybody")

    def whoami(secret, hdrs, peer):
        # Sets the module attribute rather than re-importing. Re-importing
        # would discard any patch checks_structure's breaker has applied, and
        # a detector its breaker cannot reach is one nothing has shown to fire.
        from . import server as S
        real = S.PROXY_SECRET
        S.PROXY_SECRET = secret or ""
        try:
            class Stub(S.Handler):
                def __init__(self, h, p):
                    self.headers = h
                    self.client_address = (p, 0)
            return Stub(hdrs, peer).whoami()
        finally:
            S.PROXY_SECRET = real

    ID, SEC, SIB = "X-MS-CLIENT-PRINCIPAL-NAME", "s3cret", "172.18.0.9"
    check("unset: today's behaviour is unchanged",
          whoami(None, {ID: "attacker@x.y"}, SIB), "attacker@x.y")
    check("set: a forged identity with no proxy header is anonymous",
          whoami(SEC, {ID: "attacker@x.y"}, SIB), "anonymous")
    check("set: a guessed secret is anonymous",
          whoami(SEC, {ID: "attacker@x.y", "X-Poolhound-Proxy-Auth": "guess"}, SIB),
          "anonymous")
    check("set: the proxy's own request is believed",
          whoami(SEC, {ID: "owner@x.y", "X-Poolhound-Proxy-Auth": SEC}, SIB),
          "owner@x.y")
    check("set: a loopback workstation is still local",
          whoami(SEC, {}, "127.0.0.1"), "local")

    # Caddy must present it, and AFTER the strips — before them a client could
    # supply it and the check would believe the client.
    from . import config
    vhost = config.caddy_vhost()
    if not vhost or not os.path.exists(vhost):
        skipped("Caddy presents the secret after the strips",
                "no Caddy vhost in this checkout")
        return
    src = open(vhost).read()
    set_at = src.find("request_header X-Poolhound-Proxy-Auth")
    last_strip = src.rfind("request_header -X-")
    check("the vhost sets the proxy header", set_at > 0, True)
    check("and sets it AFTER every strip", set_at > last_strip, True)


def _unquoted_heredoc_expansions(path):
    """Backticks and $( ) inside an UNQUOTED heredoc, with their line numbers.

    `<<EOF` expands on the machine running the script; `<<'EOF'` does not.
    Everything between the two is otherwise identical to read, which is the
    whole problem: the difference is two characters on a line the reader has
    usually scrolled past by the time they reach the body.
    """
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().split("\n")
    except OSError:
        return None

    start = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
    found, term, dash = [], None, False
    for n, line in enumerate(lines, 1):
        if term is not None:
            stripped = line.strip() if dash else line
            if stripped == term:
                term = None
            else:
                # \$ and \` are escaped and reach the far side intact.
                bare = re.sub(r"\\.", "", line)
                if "`" in bare or "$(" in bare:
                    found.append(n)
            continue
        m = start.search(line)
        if m and not m.group(1):            # no quote => it expands HERE
            term, dash = m.group(2), "<<-" in line
    return found


def t_no_heredoc_runs_the_deploy_on_the_wrong_machine():
    """A heredoc that expands locally is a command run on the workstation.

    MEASURED TWICE. The first time, a comment written in this project's
    ordinary backtick style sat inside `sudo bash -s <<EOF` and ran `nofail`,
    `mount -a`, `set -e` and `df -h` on a developer's Mac. Six instances were
    found and fixed by reading.

    The seventh was missed by that same reading, survived for ten days and
    announced itself on a real deploy: a Python docstring, inside the embedded
    script, holding `KEY: value`, `- mount` and `section:`. Three "command not
    found" lines, and the docstring arrived at the server with the text gone.

    Reading found six of seven. That is the argument for this being a check:
    an audit that misses one is an audit that will miss the next one, and the
    substitution that worries me is not the one that errors — it is the kind
    that succeeds quietly, like a pair holding only redirections.
    """
    print("\n  deploy — no heredoc expands on the machine launching it")
    from . import config
    root = config.root()
    scripts = sorted(glob.glob(os.path.join(root, "deploy", "*.sh")))
    if not scripts:
        skipped("heredocs that expand on the wrong machine",
                "no deploy/*.sh in this tree")
        return
    for p in scripts:
        hits = _unquoted_heredoc_expansions(p)
        rel = os.path.relpath(p, root)
        if hits is None:
            skipped(f"heredocs in {rel}", "unreadable here")
            continue
        check(f"{rel} runs nothing locally from inside a heredoc", hits, [])


# Keys systemd accepts ONLY in [Unit]. The StartLimit pair is here because it
# was in [Service], where systemd ignores it and says so once per boot — so
# the generous restart window the comment argued for was never in force.
_UNIT_ONLY_KEYS = {
    "StartLimitIntervalSec", "StartLimitInterval", "StartLimitBurst",
    "After", "Before", "Requires", "Wants", "Requisite", "BindsTo",
    "PartOf", "Conflicts", "OnFailure", "Description", "Documentation",
}


def _parse_unit(path):
    """{section: [(key, value)]} — enough to ask where a key landed."""
    out, section = {}, None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                out.setdefault(section, [])
            elif "=" in line and section:
                k, v = line.split("=", 1)
                out[section].append((k.strip(), v.strip()))
    return out


def t_a_systemd_unit_says_what_it_means():
    """Two ways a unit file can be wrong while looking right.

    MEASURED, and it cost three days of pool samples.

    ORDERING. poolhound-agent.service had `After=aqualinkd.service`, and
    aqualinkd ships with `After=network.target multi-user.target` while being
    `WantedBy=multi-user.target`. That put our unit in a cycle, and systemd
    breaks a cycle by DELETING a job:

      multi-user.target: Found ordering cycle on poolhound-agent.service/start
      multi-user.target: Job poolhound-agent.service/start deleted to break
                         ordering cycle starting with multi-user.target/start

    It chose ours. The unit then reads `inactive (dead)`, Result=success,
    NRestarts=0, with no ExecStart in the journal — which is exactly what a
    service nobody asked to start looks like, so `is-enabled` said "enabled"
    and the agent simply never ran after a reboot.

    A unit that is WantedBy=X must never be After=X. It is the shape that
    caused this, it is always wrong, and it is checkable.

    SECTIONS. StartLimitIntervalSec and StartLimitBurst sat in [Service],
    where systemd ignores them — logging "Unknown key ... ignoring" once per
    boot, in a journal nobody reads until something is already broken.
    """
    print("\n  systemd — the units mean what they say")
    from . import config
    root = config.root()
    units = sorted(glob.glob(os.path.join(root, "deploy", "*.service"))
                   + glob.glob(os.path.join(root, "deploy", "*.timer")))
    if not units:
        skipped("systemd units", "no deploy/*.service in this tree")
        return
    for p in units:
        rel, u = os.path.relpath(p, root), _parse_unit(p)
        misplaced = sorted({k for sec, kvs in u.items() if sec != "Unit"
                            for k, _ in kvs if k in _UNIT_ONLY_KEYS})
        check(f"{rel}: every [Unit] key is in [Unit]", misplaced, [])

        after = {t for k, v in u.get("Unit", []) if k == "After"
                 for t in v.split()}
        wanted = {t for k, v in u.get("Install", []) if k == "WantedBy"
                  for t in v.split()}
        check(f"{rel}: not ordered after the target that wants it",
              sorted(after & wanted), [])


# The two files that carry a second copy of the pool's timezone, and the key
# each one sets it with. Neither can import config, which is why this is a
# check and not a refactor.
_TZ_FILES = (("deploy/Dockerfile", "TZ="),
             ("deploy/compose.poolhound.yml", "TZ:"))


def t_the_deployment_pins_the_pools_timezone():
    """The container's TZ is load-bearing, and it is written down three times.

    Two of this product's answers are LOCAL-time answers:

      * best_lab() weighs a WaterGuru UTC instant against a bare Leslie's
        local date. 02:00Z is the previous evening in California, so which lab
        is "most recent" depends on the offset the host is in.
      * edit_chemical() stores the canonical dose timestamp as
        when().astimezone().strftime(...), so the one spelling every dose is
        indexed by is the host's offset.

    Neither fails loudly in the wrong zone. The first quotes a stale
    alkalinity — the defect behind "say what the data says, not what a chart
    says" — and the second rewrites a timestamp the page then cannot address.

    MEASURED, by the first CI run this repository ever had: four cases passed
    on a Pacific workstation and failed on a UTC runner, with the product
    correct in both. The suite was reading the host's zone as if it were the
    pool's, which is the same shape as a gate that only passes on the machine
    that already has the untracked files.

    config.DEPLOY_TZ is the owner. The Dockerfile and the compose file cannot
    import it, so the copies stay and are held to it here — in both
    directions: a zone set in neither file would leave the container on UTC,
    and a zone set to something else would be worse than unset, because it
    would be confidently wrong.
    """
    print("\n  timezone — the deployment runs in the pool's zone")
    from . import config
    root = config.root()
    seen = 0
    for rel, key in _TZ_FILES:
        path = os.path.join(root, *rel.split("/"))
        if not os.path.exists(path):
            skipped(f"{rel} pinning TZ to the pool's zone", "not in this tree")
            continue
        seen += 1
        text = open(path, encoding="utf-8").read()
        zones = re.findall(re.escape(key) + r'\s*"?([A-Za-z]+/[A-Za-z_]+)"?', text)
        check(f"{rel} pins a timezone", bool(zones), True)
        check(f"{rel} pins the one config.py names",
              sorted(set(zones)), [config.DEPLOY_TZ] if zones else [])
    if seen:
        check("and the zone has exactly one owner in the package",
              sum(config.DEPLOY_TZ in open(
                  os.path.join(root, "poolhound", f), encoding="utf-8").read()
                  for f in sorted(os.listdir(os.path.join(root, "poolhound")))
                  if f.endswith(".py")), 1)


# --------------------------------------------- the demo pool's clock, which is
#                                               what the screenshots are of
def t_the_demo_pool_is_never_dated_in_the_future():
    """A picture whose content depends on the hour it was taken.

    `bin/demo-data` pins the demo pool's clock so the pump block is finished
    and the Power and Trend panels have something to show. It pinned it to
    14:12 TODAY — `now().replace(hour=14, minute=12)` — which is in the FUTURE
    for most of the working day.

    MEASURED: `bin/screenshots` run at 07:41 put two red advisories on the
    landing page's Home card — "Pool controller has 25 reading(s) dated in the
    future (newest 2026-10-08 14:00) — a clock is wrong somewhere" — and the
    product was right every time. The same command after lunch produced "13
    hours ago" and looked correct, which is why this survived every previous
    retake. It is the time-of-day shape of the timezone defect above: the
    machine was the assumption.

    So the invariant is checked against the generator's own constant and its
    own newest row, not against a second copy of the arithmetic. Both, because
    `samples()` caps rows at `NOW` itself — a pin in the future is the only way
    a row gets ahead of the clock, and a row ahead of the clock is the thing
    that shows up in a picture.
    """
    print("\n  the demo pool — nothing in it is dated in the future")
    import datetime as _dt
    import importlib.util
    from . import config
    path = os.path.join(config.root(), "bin", "demo-data")
    if not os.path.exists(path):
        skipped("the demo pool's clock", "no bin/demo-data in this tree")
        return
    # WITH AN EXPLICIT LOADER. bin/demo-data has no .py suffix, and
    # spec_from_file_location infers the loader from the extension — so it
    # returns a spec whose loader is None and module_from_spec raises
    # AttributeError on it. The traceback looked like a broken check rather
    # than a failing one, which is the third outcome this suite has a rule
    # about.
    import importlib.machinery
    import sys as _sys
    spec = importlib.util.spec_from_file_location(
        "_demo_data", path,
        loader=importlib.machinery.SourceFileLoader("_demo_data", path))
    mod = importlib.util.module_from_spec(spec)
    # AND WITHOUT LEAVING ANYTHING BEHIND. Loading it wrote bin/__pycache__,
    # which the bin/ inventory then reported as an entry point README does not
    # list — a check that fails a different check by running is worse than no
    # check, and this suite runs against a tree somebody is about to commit.
    _bytecode = _sys.dont_write_bytecode
    _sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(mod)
    finally:
        _sys.dont_write_bytecode = _bytecode
    now = _dt.datetime.now()
    check("the pinned clock has already happened", mod.NOW <= now, True)
    # And no more than a day behind it, or the demo pool is a stale pool and
    # every freshness panel in the pictures is grading the generator.
    check("and is within a day of it", (now - mod.NOW) < _dt.timedelta(days=1),
          True)
    newest = max(r["ts"] for r in mod.samples())
    check("the newest controller sample is not ahead of the clock",
          _dt.datetime.strptime(newest, "%Y-%m-%dT%H:%M:%S%z")
          <= now.astimezone(), True)


# ------------------------------------------------- the response headers, both
#                                                   owners of them
def _vhost_header_block():
    """The vhost's top-level `header { … }` as {name: value}, `?` kept.

    None when there is no vhost in this tree — the deploy image carries no
    deploy/ directory, and reading an absent file as an empty one would turn
    "not in this tree" into "the vhost sends nothing", which is the shape of
    the two deploy checks that once reported the deploy as broken on every
    image build.
    """
    from . import config
    p = config.caddy_vhost()
    if not p or not os.path.exists(p):
        return None
    text = open(p, encoding="utf-8").read()
    # The LAST top-level block, which is the one outside every handle{}: the
    # route blocks above it carry request_header lines, not response headers.
    block = re.search(r"\n\theader \{\n(.*?)\n\t\}", text, re.S)
    if not block:
        return {}
    out = {}
    for line in block.group(1).split("\n"):
        m = re.match(r'\s*(\??)([A-Za-z][A-Za-z0-9-]*)\s+"(.*)"\s*$', line)
        if m:
            out[m.group(1) + m.group(2)] = m.group(3)
    return out


def t_the_vhost_sends_the_same_headers_the_server_does():
    """Two owners of one header set, and the two sets were different.

    MEASURED, by reading both: end_headers() sent four security headers and the
    vhost sent four, and they were not the same four. The vhost had
    Strict-Transport-Security, which only it can meaningfully send, and had no
    Content-Security-Policy at all — so a Caddy-generated error page, which
    never reaches poolhound, was served with no policy. Neither copy was
    wrong on its own terms; what was wrong was that there were two.

    BOTH DIRECTIONS, because a one-way check cannot see the interesting half.
    A header in headers.STATIC and not in the vhost is an internet visitor not
    getting it. A header in the vhost and not in STATIC is the drift this case
    exists to catch — a policy decision made in a file the package cannot
    import and nothing in the package knows about.

    AND THE POLICY MUST BE A DEFAULT, not a value. A bare
    `Content-Security-Policy` in that block replaces what poolhound sent, and
    what poolhound sends is the per-page policy naming that page's one inline
    script by digest. Writing the baseline there without Caddy's `?` prefix
    would therefore downgrade every served page to a policy that refuses its
    own script — a change that reads like hardening and is an outage.
    """
    from . import headers
    print("\n  headers — the vhost and the server send one set, not two")
    got = _vhost_header_block()
    if got is None:
        skipped("the vhost sends the same headers the server does",
                "no Caddy vhost in this tree")
        return
    check("the vhost has a top-level header block", bool(got), True)

    missing = sorted(n for n in headers.STATIC if n not in got)
    check(f"every header the server sends is on the vhost ({len(got)} there)",
          missing, [])
    wrong = sorted(n for n, v in headers.STATIC.items()
                   if n in got and got[n] != v)
    check("and with the same value", wrong, [])

    for n, v in headers.VHOST_ONLY.items():
        check(f"the vhost still sends {n}, which is its alone", got.get(n), v)

    # The other direction: nothing on the vhost that the package has never
    # heard of. The removals are Caddy's own syntax and are not values.
    known = set(headers.STATIC) | set(headers.VHOST_ONLY) | {
        "?Content-Security-Policy"}
    unknown = sorted(n for n in got if n.lstrip("?") not in
                     {k.lstrip("?") for k in known})
    check("and nothing on the vhost the package does not own", unknown, [])

    check("the policy is a DEFAULT the per-page one overrides",
          "?Content-Security-Policy" in got, True)
    check("and it is not also set outright, which would replace it",
          "Content-Security-Policy" in got, False)
    check("the defaulted policy is the baseline",
          got.get("?Content-Security-Policy"), headers.BASELINE_CSP)


def t_the_policy_never_allows_inline_script():
    """`script-src 'unsafe-inline'` is the directive this policy used to carry.

    Everything else about it was already tight — default-src 'none', no
    external origin anywhere, no 'unsafe-eval'. Inline script was allowed
    wholesale, which is the one allowance that makes CSP unable to help with
    the XSS this project actually shipped: a collector's own error line,
    containing `<img src=x onerror=…>`, rendered as markup on admin.html.

    The fix was a hash, and this is the case that stops the allowance coming
    back — including by the back door, which is what the second half is about:
    a hash in script-src does NOT cover an inline event handler attribute, so
    `onclick="save()"` would need 'unsafe-hashes' to work and would fail
    confusingly without it. script-src-attr 'none' makes it fail honestly, and
    digests() reports the attribute rather than accommodating it.
    """
    from . import headers
    print("\n  headers — the policy names its script instead of allowing any")
    pol = headers.policy(["sha256-aaa"], ["sha256-bbb"])
    script = [d for d in pol.split("; ") if d.startswith("script-src ")][0]
    check("script-src carries the digest", "'sha256-aaa'" in script, True)
    check("and never 'unsafe-inline'", "unsafe-inline" in script, False)
    check("nor 'unsafe-eval'", "unsafe-eval" in pol, False)
    check("inline event handlers are refused outright",
          "script-src-attr 'none'" in pol, True)

    # A page with no inline script does not become a page that may load one.
    bare = headers.policy([], [])
    check("no digests means script-src 'none', not 'self'",
          "script-src 'none'" in bare, True)
    check("the baseline refuses script too",
          "script-src 'none'" in headers.BASELINE_CSP, True)

    # STYLE IS SPLIT, and the loose allowance covers attributes alone.
    check("style elements are named by digest",
          "style-src-elem 'self' 'sha256-bbb'" in pol, True)
    check("and 'unsafe-inline' is confined to attributes",
          "style-src-attr 'unsafe-inline'" in pol, True)

    # The two things that would make the policy a lie, reported rather than
    # silently accommodated.
    s, st, ext, on = headers.digests(
        '<script src="https://cdn.example.com/x.js"></script>'
        '<link rel="stylesheet" href="https://cdn.example.com/x.css">'
        '<button onclick="save()">go</button>'
        '<script type="application/json">{"a":1}</script>'
        '<script>var a=1;</script><style>b{color:red}</style>')
    check("an external script is reported", len(ext), 2)
    check("an inline handler attribute is reported", on, ["<button onclick>"])
    check("a JSON data block is not hashed as script", len(s), 1)
    check("and the real style element is", len(st), 1)


def t_the_rendered_pages_match_the_policy_they_get():
    """The policy is derived from the page, so the page must stay derivable.

    bin/drive serves the real header and reads the browser's console back,
    which is the only proof that these digests are the ones Chromium computes.
    This is the half that holds without a browser: that each build carries
    exactly one executable inline script, that nothing external crept in — a
    CDN reference would be refused by default-src 'none' and the page would
    simply stop working — and that no inline event handler attribute appeared,
    which script-src-attr 'none' would refuse.

    SKIPPED RATHER THAN SILENT when the pages are not here: the deploy image
    carries no rendered pages, and reading an absent page as an empty one would
    report "no inline script" as a pass.
    """
    from . import config, headers
    print("\n  headers — each built page is one the policy can describe")
    site = config.site_dir(config.load())
    for name in ("index.html", "admin.html"):
        p = os.path.join(site, name)
        if not os.path.exists(p):
            skipped(f"{name} matches the policy it is served with",
                    "not rendered in this tree")
            continue
        s, st, ext, on = headers.digests(open(p, encoding="utf-8").read())
        check(f"{name}: exactly one executable inline script", len(s), 1)
        check(f"{name}: nothing is loaded from anywhere else", ext, [])
        check(f"{name}: no inline event handler attribute", on, [])
        check(f"{name}: its style elements are hashable", len(st) > 0, True)
        pol = headers.csp_for_file(p)
        check(f"{name}: the policy it gets names that script",
              s[0] in pol if s else False, True)
