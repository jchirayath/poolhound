"""Which module owns which fact, checked rather than asserted in prose.

WHY THIS FILE EXISTS

The design of this project is a set of sentences: "chemicals.py is the only
place the dose arithmetic lives", "render.py owns the loaders", "style.SERIES is
the single source". Every one was true when written and enforced by nothing, so
each is a promise the code can break in silence.

Measured over one week of review, roughly a third of every defect found was a
second copy of a fact that had drifted from the first:

  * five call sites each spelling "is this a person" as `!= "local"`, plus a
    sixth in the page script, so adding a second not-a-person answer caused an
    outage;
  * the dose plausibility bound written inline in the EDIT route only, so the
    product accepted a dose it then refused to let anybody fix;
  * the SMTP credential path read under `smtp.credentials` by the writer and
    `notify.smtp_credentials` by every reader — the writer's key existing
    nowhere in the repo;
  * PUBLIC_TABS declared twice, with the copy nobody read sitting in the
    obvious place to edit;
  * the pending-command window as 240 seconds in the server and 20 minutes in
    the page;
  * the pool volume range enforced by one writer of it and not the other.

A comment saying "this is the only copy" cannot detect a second copy. A test
can. Each entry below names a fact, the module allowed to hold it, and a
pattern that finds it — and the check asserts the pattern appears nowhere else.

ADDING A SEAM COSTS ONE LINE, and is the moment to add it: when you write
`# the one place X lives`, put X here instead, because the comment is the part
that will still be there after it stops being true.
"""

import ast
import io
import os
import re
import tokenize

# (what the fact is, module that owns it, regex that finds a copy of it,
#  modules exempt and why)
SEAMS = [
    # WHICH COLLECTOR STATES NEED A PERSON. Written twice — collection.py's
    # banner and render._collection_badge() — and both copies named only the
    # failed and late states, so a source that had NEVER RUN was counted by
    # neither: the tab said "Every scheduled source has run recently and none
    # reported a failure" directly above a card whose pill read "never run",
    # and the nav badge agreed with it. Two copies that match each other look
    # exactly like one copy that is right.
    ("which collector states need attention", "collection.py",
     r'"failed",\s*"late"',
     {}),

    ("the series palette", "style.py",
     r"#2a78d6|#eb6834|#1baf7a",
     {"contrast.py": "measures them", "checks_a11y.py": "asserts them",
      "checks_gates.py": "asserts them"}),

    # THE SECURITY HEADERS. Written twice for as long as there were two things
    # sending them: server.py's end_headers() and the Caddy vhost, which is not
    # a Python file and so was never visible to this gate at all. The two sets
    # were different — the vhost had HSTS and no Content-Security-Policy, so a
    # Caddy error page carried no policy — and each copy was defensible on its
    # own, which is how a disagreement survives review. headers.py owns the set
    # now; the vhost's copy is held to it by a check, because this gate reads
    # Python and the other owner is a Caddyfile.
    ("the security headers", "headers.py",
     r"X-Content-Type-Options|X-Frame-Options|Referrer-Policy|"
     r"Permissions-Policy|Content-Security-Policy|Cross-Origin-Opener-Policy",
     {"checks_gates.py": "holds the vhost's copy to it",
      "help.py": "draws what the deployment sends"}),

    ("the not-a-person sentinels", "server.py",
     r"""["'](?:local|anonymous)["']""",
     {"access.py": "decides what each may do",
      "checks_vault.py": "asserts the boundary",
      "selftest.py": "asserts the predicate",
      "render.py": "shows who the proxy says you are"}),

    # WHAT THE PRODUCT IS FOR. Three things, and the count is the fact: the
    # masthead tagline named only one of them once, was fixed, and then the
    # landing page's own opening section made the identical cut a week later
    # because the three were a sentence in render.py rather than something
    # anything could read. Both now assemble from info.PURPOSE.
    ("what poolhound is for", "info.py",
     # The WHAT IT DOES half. The third alternative carries its "which":
     # without it the pattern also matched chemistry.py's "every dose figure
     # multiplies through it" -- the volume-not-set badge, which is a
     # different sentence making a different point in a different place, and
     # not a copy of anything. A seam that reports prose overlap as
     # duplication is a seam people learn to ignore.
     r"Reach a pool controller|chart written for somebody|"
     r"which every dose figure multiplies|"
     # And the WHAT IT IS half: the noun the tab, the description and the
     # masthead all take from info.CATEGORY. Retyping it into any one of them
     # is how a product comes to call itself two things in one document.
     r"remote pool management",
     {}),

    ("the dose plausibility bound", "chemicals.py",
     r"MAX_BASE|plausible_amount\s*=",
     {}),

    # THE VALUE, NOT ONLY THE NAME. This matched `CLOCK_SKEW =` and nothing
    # else, so two copies spelled `dt.timedelta(minutes=5)` sat in server.py
    # and the gate reported zero copies. A seam that only recognises the fact
    # when it is spelled the owner's way cannot find the copy — which is the
    # entire job. Found by a reviewer, not by this.
    ("the clock-skew tolerance", "render.py",
     r"CLOCK_SKEW\s*=|timedelta\(\s*minutes\s*=\s*5\s*\)",
     {}),

    # Parsing a stored timestamp. render.when() exists because this product
    # writes local-with-offset and WaterGuru sends UTC 'Z', and a bare
    # fromisoformat handles one of those and returns an aware datetime that
    # cannot be compared with a naive now() — which is how POST /api/reading
    # answered 500 to the very format its own success response hands back.
    ("parsing a stored timestamp", "render.py",
     r"fromisoformat\(",
     {"vault.py": "parses its own store, not pool data",
      "agent.py": "parses the panel's payload on the Pi",
      "leslies.py": "parses the vendor's payload",
      "waterguru.py": "parses the vendor's payload",
      "commands.py": "parses its own issued-at stamps",
      "corrections.py": "parses its own append-only log",
      "watch.py": "parses its own state file"}),

    # \b ON THE RIGHT, because the bare pattern also matches the -9999px that
    # parks the skip link offscreen. Found the moment _code_only stopped
    # blanking style.py, 99.5% of which was invisible to this gate.
    ("the panel's no-flow sentinel", "commands.py",
     r"-999\b",
     {"selftest.py": "asserts it is refused",
      "checks_gates.py": "asserts it is refused"}),

    # The mark was drawn twice — help.py's diagram table and the masthead —
    # with the paths retyped, which is how a logo comes to have two versions.
    # WHITESPACE-TOLERANT, because a person retyping a path types it the way
    # an editor formats one. The committed docs/poolhound*.svg held exactly
    # that — "M32 48 H96" — and this pattern did not match it. It was outside
    # the scanned tree as well, which is the half inventory.py now covers.
    # And it went stale the same way: when the pool was redrawn as an open
    # section the pattern still named the old path, matched nothing anywhere,
    # and guarded a drawing that no longer existed. It names the walls' path.
    ("the poolhound mark", "brand.py",
     r"M\s*22[\s,]*30\s*v\s*38\s*l\s*84[\s,]*32",
     {}),

    ("the credential file locations", "vault.py",
     r"~/\.waterguru|~/\.leslies|~/\.poolhound_smtp",
     {"config.py": "supplies the historical defaults",
      "render.py": "refuses to publish them"}),

    # WHEN THE COLLECTORS RUN. cadence.py owns it: the two lab pulls from
    # `[collection]` in config.toml, which it also bounds, and the watchdog and
    # render from the crontab, which still schedules those two. Nothing else
    # may state a time of day -- collection.schedule() merges the two and every
    # page asks it.
    #
    # MEASURED: eleven lines wrote the times out by hand -- four rows of the
    # Settings schedule table and seven statements in Help's two architecture
    # drawings, including the <desc> a screen reader is given. Every one said
    # 14:00 and 14:05, which is what the crontab held BEFORE the CRON_TZ fix
    # moved the pulls to 23:00 and 23:05 UTC. So Settings and Help stated the
    # schedule nine hours away from the Collection tab, which reads the file,
    # on the same build -- and the stale copies were the ones somebody asking
    # "when does WaterGuru run" would find first, because they sit beside the
    # credentials and in the drawing captioned as what the deployment does.
    # The Collection tab's own docstring says it exists to expose exactly this
    # drift; the drift was one tab over.
    #
    # A CLOCK INSIDE A STRING, which is what a hand-written schedule looks
    # like and what a computed one never does -- Cadence.clock is formatted
    # from the crontab's own fields. The brackets and braces excluded from the
    # gap are byte slices (`data[12:16]`) and format specs (`{q * 15:02d}`)
    # that follow a quote on the same line and are not times at all; the
    # lookbehind drops ISO timestamps and the `+00:00` handed to
    # fromisoformat. Checked against the eleven real lines: it finds every one.
    #
    # AND NO MODULE IS EXEMPT, which is worth stating because the first draft
    # exempted selftest.py for its ISO fixtures and did not need to -- the
    # excluded braces already cover them. An exemption nobody has tried to
    # remove is a hole with a reason written beside it.
    ("the collectors' clock times", "cadence.py",
     r"""["'][^"'\n\[\]{}]*(?<![\d:T+-])(?:[01]?[0-9]|2[0-3]):[0-5][0-9](?![\d:])""",
     {}),
]

# Files that may hold any fact, and why.
#
# seams.py IS the table of patterns, so it necessarily contains all of them.
# The checks_*.py modules build fixtures — a test for "a future timestamp is
# refused" has to contain a future timestamp — and a test that could not name
# the value it is about would be a test of nothing.
#
# Kept short on purpose: an exemption is a hole, and the reason each one is
# written down is so the next person can tell whether it still holds.
NEVER_SCANNED = {"__init__.py", "seams.py"}


def _exempt_file(name):
    return name in NEVER_SCANNED or name.startswith("checks_")


def _modules(root):
    d = os.path.join(root, "poolhound")
    return sorted(f for f in os.listdir(d)
                  if f.endswith(".py") and not _exempt_file(f))


def _code_only(text):
    """The source with comments and docstrings blanked, offsets preserved.

    A COMMENT DESCRIBING A DEFECT IS NOT A COPY OF THE FACT. This project
    writes its comments as the history of what went wrong, so they legitimately
    quote sentinels, colours and paths — three times already a comment has
    tripped an assertion by naming the thing it warns about. Blanking them is
    what lets the comments stay honest.

    BLANKED, NOT DELETED, so a reported line number is the line in the FILE.
    The first version removed the text and then reported the line it matched
    on, which was a line number in a document that does not exist. A gate that
    points at the wrong place is a gate somebody stops believing — and I read
    three wrong locations before noticing.
    """
    lines = text.split("\n")
    spans = []                       # (line, col, end_line, end_col), 1-indexed

    # COMMENTS, from the real tokenizer. The previous version dropped a line
    # beginning with "#" and otherwise cut at the first "  # ", which missed a
    # comment after a single space and would have cut a "  # " inside a string.
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                spans.append((*tok.start, *tok.end, False))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass

    # DOCSTRINGS, AND ONLY DOCSTRINGS.
    #
    # This used to blank every triple-quoted run in the file, which meant the
    # gate could not see 59% of the package: 78% of render.py, 99.5% of
    # style.py. render.TEMPLATE is a """ string holding the entire page script
    # and its JSON catalogue, so every fact the BROWSER holds was invisible —
    # and the motivating defect in this file's own docstring is "five call
    # sites ... plus a sixth in the page script". The gate written for that
    # copy could not have found it.
    #
    # A docstring is a bare string EXPRESSION statement; TEMPLATE is a string
    # ASSIGNED to a name. The parser knows the difference, so ask it rather
    # than guessing from the quotes.
    try:
        for node in ast.walk(ast.parse(text)):
            if (isinstance(node, ast.Expr)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                v = node.value
                spans.append((v.lineno, v.col_offset,
                              v.end_lineno, v.end_col_offset, True))
    except SyntaxError:
        pass

    # ast reports col_offset in UTF-8 BYTES; tokenize reports CHARACTERS. On
    # any line holding an em dash — which is most of the prose in this project
    # — the two disagree, and blanking past the end of a line made the file
    # longer than it started. Measured: brand.py grew by four characters, and
    # a line number computed from a file of the wrong length is the exact
    # defect this function's docstring was already written about.
    def char_col(line, col, is_bytes):
        return len(line.encode("utf-8")[:col].decode("utf-8", "ignore")) \
            if is_bytes else col

    for sl, sc, el, ec, is_bytes in spans:
        for ln in range(sl, min(el, len(lines)) + 1):
            line = lines[ln - 1]
            a = char_col(line, sc, is_bytes) if ln == sl else 0
            b = char_col(line, ec, is_bytes) if ln == el else len(line)
            a, b = min(a, len(line)), min(b, len(line))
            lines[ln - 1] = line[:a] + " " * max(0, b - a) + line[b:]
    return "\n".join(lines)


def copies(root):
    """Every place a seam's fact appears outside the module that owns it."""
    found = []
    for what, owner, pattern, exempt in SEAMS:
        for name in _modules(root):
            if name == owner or name in exempt:
                continue
            p = os.path.join(root, "poolhound", name)
            with open(p, encoding="utf-8") as f:
                body = _code_only(f.read())
            for m in re.finditer(pattern, body):
                line = body[:m.start()].count("\n") + 1
                found.append(f"{what}: {name} holds a copy "
                             f"({m.group(0)!r}, near line {line}) — "
                             f"{owner} owns it")
    return sorted(set(found))
