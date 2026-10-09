"""Lists that claim to be complete, checked against what actually exists.

WHY THIS FILE EXISTS

A list used as "every X" is a promise, and a promise maintained by hand is one
somebody will forget. Over one week of review, ten separate defects were a
hand-kept inventory that had quietly stopped being complete:

  * bin/contrast reported "all pairings pass" three separate times while a
    colour it had never measured sat below the floor — the equipment pills at
    2.78, the printed verdict pills at 4.09, the target line on every chart at
    2.39;
  * SECTION_ICONS covered 22 of 45 headings and the other 23 silently took the
    generic glyph, indistinguishable from a heading deliberately given it;
  * the Caddy private-route list was compared against a hand-typed six, then
    against fifteen matched as literals against a config that stores wildcards;
  * ROLE_UI named no control for three gated routes, so a `view` reader had
    three live buttons that answered 403 on press;
  * do_GET's routes were never inventoried at all, so a read route added to it
    and to nothing else was live and unauthenticated with every gate green;
  * the collector check iterated whatever status files happened to exist, so a
    collector that stopped filing simply stopped appearing.

The shape is always the same: something knows the truth, and something else
claims to list it, and nothing compares them. This registers the pair.

REGISTERING COSTS TWO CALLABLES and is the moment to do it: when you write a
list and think "this is all of them", it is an inventory, and the thought is
the thing that will not survive the next person.
"""

import hashlib
import os

# name -> (what actually exists, what the list declares, why a gap matters,
#          the repo files the comparison needs)
#
# Both sides return a set of comparable strings. The check asserts they are
# EQUAL, not that one contains the other: a declaration naming something that
# no longer exists is an entry nobody re-reads, sitting where the next reader
# will trust it.
_REGISTRY = {}


def register(name, actual, declared, why, needs=()):
    """Declare that `declared` claims to list everything `actual` finds.

    `needs` names repo-relative files the comparison reads. The deploy image
    carries poolhound/, bin/, the example config and the crontab — no README,
    no SITE.md, no deploy/ — so an inventory that reads a document has to be
    able to say "not in this tree" rather than "could not be compared", which
    is the wording for a parser that broke and a different thing entirely.
    """
    _REGISTRY[name] = (actual, declared, why, tuple(needs))


def absent():
    """(name, the files it needs) for every inventory this tree cannot answer."""
    out = []
    for name, (_a, _d, _w, needs) in sorted(_REGISTRY.items()):
        missing = [n for n in needs if not _exists(n)]
        if missing:
            out.append((name, ", ".join(missing)))
    return out


def _exists(rel):
    from . import config
    return os.path.exists(os.path.join(config.root(), *rel.split("/")))


def gaps():
    """Every inventory that has drifted, in both directions.

    An inventory whose documents are not in this tree is not compared and not
    counted as passing either — absent() is what reports it.
    """
    out = []
    unanswerable = {n for n, _ in absent()}
    for name, (actual, declared, why, _needs) in sorted(_REGISTRY.items()):
        if name in unanswerable:
            continue
        try:
            have, said = set(actual()), set(declared())
        except Exception as e:                       # noqa: BLE001
            out.append(f"{name}: could not be compared ({type(e).__name__}: {e}) "
                       f"— an inventory that cannot be read is not checked")
            continue
        for missing in sorted(have - said):
            out.append(f"{name}: {missing!r} exists and the list does not name it. {why}")
        for ghost in sorted(said - have):
            out.append(f"{name}: {ghost!r} is named by the list and does not exist. "
                       f"An entry for something gone is one nobody re-reads.")
    return out


def names():
    return sorted(_REGISTRY)


def _install():
    """Register the inventories this codebase actually has.

    Done in a function rather than at import so a module that fails to import
    reports as a comparison failure above, rather than taking the whole check
    down with it.
    """
    from . import access, brand, icons, render, style

    register(
        "route -> controls (access.ROLE_UI)",
        lambda: set(access.NEEDS),
        lambda: set(access.ROLE_UI),
        "A gated route with no control listed is a live button that answers 403.")

    register(
        "colour tokens measured by bin/contrast",
        lambda: set(_root_tokens(style.CSS)),
        lambda: _contrast_covered(),
        "A token no pairing measures is a colour nobody has ever checked.")

    register(
        "the committed brand files (bin/brand)",
        lambda: {f"{rel} {_digest(make())}" for rel, make in brand.FILES.items()},
        lambda: {f"{rel} {_digest(_read(rel))}" for rel in brand.FILES},
        "GitHub renders an <img> and cannot run anything, so the README's logo "
        "is a file — and it stayed a dot for a week after the mark became a "
        "drop. Run bin/brand.",
        needs=("docs/poolhound.svg",))

    register(
        "bin/ entry points README lists",
        lambda: _bin_entry_points(),
        lambda: _readme_layout("bin/"),
        "README's repository layout is where a reader looks for the command "
        "they need; bin/drive is one of the four gates and was missing from it.",
        needs=("README.md",))

    register(
        "poolhound modules README lists",
        lambda: _package_modules(),
        lambda: _readme_layout("poolhound/"),
        "Same list, same reader. seams.py and inventory.py are two of the four "
        "gates and neither appeared in it.",
        needs=("README.md",))

    register(
        "deploy/ files deploy/README lists",
        lambda: _deploy_files(),
        lambda: _table_codes("deploy/README.md", "## Files here"),
        "The runbook's file table is what somebody rebuilding the server reads "
        "instead of ls.",
        needs=("deploy/README.md",))

    register(
        "gated routes README's API table lists",
        lambda: set(access.NEEDS) | _DOCUMENTED_UNGATED,
        lambda: _api_table_routes(),
        "A route missing from the table is a capability nobody knows the "
        "product has — /api/ask spends money and was not in it.",
        needs=("README.md",))

    register(
        "caddy snippets the vhost imports",
        lambda: _imported_snippets(),
        lambda: _snippets_every_auth_file_defines(),
        "Sign-in mechanisms are swapped by mounting one file as the other, so "
        "they have to be interchangeable. A name the vhost imports and one of "
        "them does not define is a Caddyfile that will not adapt — and the "
        "install that adapts is whichever mode the operator is NOT running.",
        needs=("deploy/poolhound.caddy",))

    register(
        "section headings -> icon",
        lambda: set(_rendered_headings()),
        lambda: set(_mapped_headings()),
        "An unmapped heading takes the generic glyph, which is what a mapped "
        "one may also do — so the miss is invisible.")


def _root_tokens(css):
    from . import contrast
    return set(contrast.palettes(css)["light"])


def _contrast_covered():
    from . import contrast
    return ({t for pair in contrast.PAIRS for t in pair[:2]}
            | set(contrast.NOT_MEASURED))


def _rendered_headings():
    """The headings the build actually writes, from the rendered pages.

    Read from disk rather than re-rendered: build() is what assembles them, and
    an inventory that re-renders is comparing a list against its own arrangement
    instead of against the product.
    """
    from . import config, render
    site = config.site_dir(render.CFG)
    out = set()
    for name in ("index.html", "admin.html"):
        p = os.path.join(site, name)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            matched, uncovered = render.section_icon_coverage(f.read())
        out |= set(uncovered)
    return out


def _mapped_headings():
    # Nothing: every rendered heading must be MAPPED, so the "uncovered" set
    # above has to be empty. Expressed as an inventory so it is reported in the
    # same voice as the others.
    return set()


# --- the readers the registrations above use -------------------------------
#
# These parse the DOCUMENTS, deliberately. An inventory that read the same
# Python both sides would be comparing a list against its own arrangement; the
# whole question here is whether the prose a person reads still matches the
# tree. A parse that stops working is reported by gaps() as "could not be
# compared", which is the right answer — a list nobody can read is not checked.


def _digest(text):
    if text is None:
        return "missing"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _read(rel):
    from . import config
    path = os.path.join(config.root(), *rel.split("/"))
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return f.read()


def _bin_entry_points():
    from . import config
    d = os.path.join(config.root(), "bin")
    return {f for f in os.listdir(d) if not f.startswith(".")}


def _package_modules():
    from . import config
    d = os.path.join(config.root(), "poolhound")
    return {f for f in os.listdir(d)
            if f.endswith(".py") and f != "__init__.py"}


# FILES THE RUNBOOK MUST DOCUMENT AND A CHECKOUT MUST NOT CONTAIN.
#
# deploy/server.env is gitignored on purpose — it holds this deployment's
# hostnames and stack path, which is the one thing that has to change to
# publish this repository. The runbook documents it precisely BECAUSE it is
# absent and has to be created, so "the README names a file that is not here"
# is the correct state of a fresh clone, not a gap.
#
# Found by running the gates against `git archive HEAD`: every one of them is
# green on a working checkout, where the file exists, and bin/selftest failed
# on a clean tree. A gate that only passes on the machine that already has the
# untracked files is a gate no CI can run.
_UNTRACKED_BY_DESIGN = {"server.env"}


def _deploy_files():
    from . import config
    d = os.path.join(config.root(), "deploy")
    return {f for f in os.listdir(d)
            if not f.startswith(".") and f != "README.md"} | _UNTRACKED_BY_DESIGN


def _readme_layout(heading):
    """The names indented under `heading` in README's repository-layout block.

    The block is a fenced listing of `name  description` lines grouped by a
    directory line with no indent. Anything that is not a two-column entry —
    a wrapped description, a blank line — is skipped rather than guessed at.
    """
    text = _read("README.md") or ""
    start = text.find("## Repository layout")
    if start < 0:
        raise ValueError("README has no repository layout section")
    block = text[start:].split("```")[1]
    out, inside = set(), False
    for line in block.split("\n"):
        if not line.strip():
            continue
        if not line.startswith(" "):
            # The directory line carries its own description ("bin/  thin
            # entry points; all logic is in the package"), so compare the
            # first field rather than the whole line — matching the line was
            # how this parser reported that README lists no commands at all.
            inside = line.split()[0] == heading
            continue
        if not inside:
            continue
        name = line.split()[0]
        # A wrapped description line is indented past the name column; a real
        # entry starts at two spaces. Without this the continuation of
        # aqualink.py's note read as a file called "the".
        if len(line) - len(line.lstrip()) > 2:
            continue
        out.add(name)
    return out


# Two rows the gated table carries that access.NEEDS deliberately does not.
# /api/health is ungated on purpose — both builds call it before anyone has a
# level — and /api/export/<name> is the prefix arm of /api/export, sharing its
# entry. Written down here rather than filtered silently, because an exception
# nobody can see is how the last route inventory came to miss the whole of
# do_GET.
_DOCUMENTED_UNGATED = {"/api/health", "/api/export/<name>"}


def _api_table_routes():
    """The routes README's GATED table lists — the one with a `Needs` column.

    The section carries three tables: the gated routes, the two public POSTs,
    and the agent's three. Only the first is a claim about access.NEEDS, so
    only the first is compared against it; picking it by its own header means
    adding a fourth table does not silently change what is checked.
    """
    text = _read("README.md") or ""
    start = text.find("## The poolhound API")
    if start < 0:
        raise ValueError("README has no poolhound API section")
    body = text[start:].split("\n## ")[0]
    rows, header = [], None
    for line in body.split("\n"):
        if not line.startswith("|"):
            header = None
            continue
        if header is None:
            header = line
            continue
        if set(line.replace("|", "").strip()) <= set("-: "):
            continue
        if "Needs" in header:
            rows.append(line)
    if not rows:
        raise ValueError("README's API section has no table with a Needs column")
    import re
    return {m.group(1) for m in
            (re.match(r"\|\s*`([^`]+)`", r) for r in rows) if m}


def _auth_snippet_files():
    import glob
    return sorted(glob.glob(os.path.join(config_root(), "deploy", "caddy",
                                         "auth-*.caddy")))


def config_root():
    from . import config
    return config.root()


def _imported_snippets():
    """Every `import <name>` the vhost makes, excluding file-path imports."""
    import re
    text = _read("deploy/poolhound.caddy") or ""
    out = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("import ") or stripped.startswith("#"):
            continue
        target = stripped.split(None, 1)[1].strip()
        # `import /etc/caddy/conf.d/*.caddy` is a FILE import, not a snippet.
        if "/" in target or "*" in target:
            continue
        out.add(target)
    return out


def _snippets_every_auth_file_defines():
    """Names defined by EVERY auth file — the intersection, deliberately.

    One file missing a name is the defect: the mode nobody is running still
    adapts, so the break only appears when somebody switches. Intersecting
    means a name absent from any single file is reported.
    """
    import re
    defined = None
    for path in _auth_snippet_files():
        with open(path, encoding="utf-8") as f:
            names = set(re.findall(r"^\((\w+)\)\s*\{", f.read(), re.M))
        defined = names if defined is None else (defined & names)
    return defined or set()


def _table_codes(doc, heading):
    """The `code`-spanned first cell of every table row under `heading`."""
    import re
    text = _read(doc) or ""
    start = text.find(heading)
    if start < 0:
        raise ValueError(f"{doc} has no section {heading!r}")
    body = text[start + len(heading):]
    nxt = body.find("\n## ")
    if nxt > 0:
        body = body[:nxt]
    out = set()
    for line in body.split("\n"):
        if not line.startswith("|"):
            continue
        m = re.match(r"\|\s*`([^`]+)`", line)
        if m:
            out.add(m.group(1))
    return out


_install()
