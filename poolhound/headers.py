"""The response headers that make the two pages safe to serve, in one place.

WHY THIS FILE EXISTS

Two reasons, and the second is the one that cost something.

**The policy said `script-src 'unsafe-inline'`.** Everything else about the
Content-Security-Policy was already tight — `default-src 'none'`, no external
origin anywhere, no `'unsafe-eval'`, so a future edit reaching for a CDN fails
visibly. But inline script was allowed wholesale, which means the policy could
not help with the one XSS this project has actually shipped: `/api/refresh`
hands the page a collector's own last line, including vendor error bodies, and
a payload of `<img src=x onerror=…>` fired the handler on **admin.html**, the
build that holds the pool controls. That was fixed with `textContent` and
`createTextNode` rather than an escaping helper, on the argument that there is
then no call left to forget — but the whole-block invariant is maintained by
reading, and CSP is the layer that does not have to be remembered.

MEASURED, which is what makes dropping it cheap: `admin.html` carries exactly
**one** executable inline `<script>`. The other six are
`type="application/json"` data blocks, which CSP does not govern because the
browser never executes them, and the only `onerror=` in either page is prose in
the Help tab describing this very defect. So the policy needs one hash, not a
refactor, and `script-src-attr 'none'` is free on top: there is no inline event
handler attribute in either build, and now there cannot be.

**The headers had two owners that disagreed.** `server.py` sent four and the
Caddy vhost sent four, and they were not the same four: the vhost had
`Strict-Transport-Security`, which only it can meaningfully send, and lacked
the CSP entirely — so a Caddy-generated error page, which never reaches
poolhound, was served with no policy at all. That is the `seams.py` defect in
its textbook form: two copies of one fact, the quieter copy wrong. `STATIC` and
`BASELINE_CSP` are the one statement of it now, `server.py` sends them, and
`checks_gates.t_the_vhost_sends_the_same_headers_the_server_does` holds the
vhost to them.

HOW THE HASHES STAY RIGHT

Not by writing a sidecar at render time. A render can happen in a different
process from the one serving — cron's render and `docker compose exec … render`
each get their own — and this project already has a paragraph about what baking
a build-time answer into a served page costs. So the digest is taken from **the
bytes being served**, in `csp_for_file`, keyed on the file's mtime and size.
A page rendered by any process, at any time, is served with the policy that
matches it, and a policy that does not match fails by refusing the page's own
script rather than by quietly permitting somebody else's.
"""

import base64
import hashlib
import os
from html.parser import HTMLParser

# ---------------------------------------------------------------- the headers
#
# HSTS IS NOT HERE, on purpose. TLS terminates at Caddy; a Strict-Transport-
# Security header sent from this process would either be stripped as redundant
# or, on a loopback workstation, tell the browser to refuse the http:// URL the
# tool is served at. It is the one header the vhost owns alone, and the check
# that compares the two knows that.
#
# Cross-Origin-Resource-Policy is `same-origin` rather than `same-site`: this
# household's dashboard is not a resource any other site has a reason to
# embed, and `same-site` would admit a sibling subdomain on the same stack.
STATIC = {
    "X-Content-Type-Options": "nosniff",
    # Superseded by frame-ancestors below, and kept because it is the only one
    # of the two that an older browser obeys. Two spellings of one intent, not
    # two intents.
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # EVERY powerful feature is named and denied rather than the list being
    # left to the browser's defaults, which differ between browsers and move.
    # The pool-tracing tool reads a file the person CHOSE from a file input,
    # which is not a permission-gated feature, so nothing here costs the
    # product anything.
    "Permissions-Policy": (
        "accelerometer=(), ambient-light-sensor=(), autoplay=(), battery=(), "
        "camera=(), display-capture=(), document-domain=(), "
        "encrypted-media=(), fullscreen=(), geolocation=(), gyroscope=(), "
        "hid=(), idle-detection=(), local-fonts=(), magnetometer=(), "
        "microphone=(), midi=(), payment=(), picture-in-picture=(), "
        "publickey-credentials-get=(), screen-wake-lock=(), serial=(), "
        "usb=(), xr-spatial-tracking=()"),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}

# The header only Caddy can send, named here so the check that compares the two
# owners can say "this one is the vhost's alone" rather than being silent about
# a difference.
VHOST_ONLY = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}

# ------------------------------------------------------------------ the policy
#
# The directives that do not depend on what is in the page. Order is the order
# a reader wants them in, not one the browser cares about.
#
# img-src carries blob: because the pool-tracing tool hands a chosen photo to
# the canvas as an object URL — same-origin data the page created itself.
# Without it the feature is silently dead under our own server and works
# everywhere else, which is exactly how it shipped broken the first time.
_COMMON = (
    "default-src 'none'",
    "img-src 'self' data: blob:",
    "connect-src 'self'",
    "form-action 'none'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
    # THERE IS NO INLINE EVENT HANDLER IN EITHER BUILD and this is what keeps
    # it that way. A hash in script-src does not cover an `onclick=` attribute
    # — allowing those needs 'unsafe-hashes' — so without this directive a
    # future `onclick="save()"` would be refused with a confusing error; with
    # it, it is refused with an honest one. Either way the page tells you.
    "script-src-attr 'none'",
)

# What a response that is not one of our HTML pages gets: JSON, a CSV export,
# a 404, a 421. Nothing in those executes, so nothing in those needs allowing,
# and this is also the value the vhost defaults in for its own error pages.
BASELINE_CSP = "; ".join(_COMMON + ("script-src 'none'", "style-src 'none'"))


def policy(script_digests=(), style_digests=()):
    """The policy for a page holding these inline scripts and style elements.

    Both lists are `sha256-…` digests. Empty lists give `'none'` rather than
    `'self'`, because a page with no inline script does not become a page that
    may load one.

    STYLE IS SPLIT IN THREE and the three are not redundant. `style-src-elem`
    takes the hashes of the `<style>` elements. `style-src-attr` keeps
    `'unsafe-inline'`, because the built pages carry some seventy `style="…"`
    attributes — chart geometry, mostly — and an attribute cannot be hashed
    individually. `style-src` is written last as the fallback for a browser
    that implements neither of the granular directives, where it is the only
    one of the three that will be read. A stylesheet attribute is a far smaller
    weapon than a script, and this is the honest split: the loose allowance now
    covers attributes alone instead of covering every `<style>` block as well.
    """
    script = " ".join(f"'{d}'" for d in script_digests)
    style = " ".join(f"'{d}'" for d in style_digests)
    return "; ".join(_COMMON + (
        f"script-src 'self' {script}".strip() if script else "script-src 'none'",
        f"style-src-elem 'self' {style}".strip() if style else "style-src-elem 'none'",
        "style-src-attr 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline'" if style else "style-src 'none'",
    ))


# --------------------------------------------------------------- the digests
#
# WHAT THE BROWSER HASHES, exactly. The element's child text as it appears in
# the source — no trimming, no entity expansion, no normalisation of line
# endings — encoded UTF-8 and SHA-256'd. Getting any of that wrong produces a
# policy that refuses the page's own script, which is a loud failure and the
# reason this is derived from the served bytes and verified in a real browser
# by bin/drive rather than asserted here.
#
# An HTML parser rather than a regex, because the question "is this element
# executable" is a question about its `type` attribute, and the question "where
# does its text end" is one that a regex gets wrong on the first `</script>`
# inside a JavaScript string literal. Python's own parser puts script and style
# into CDATA mode, which is also what disables entity conversion inside them —
# so the text arrives as written, which is what must be hashed.
_JS_TYPES = {
    "", "text/javascript", "application/javascript", "module",
    "application/ecmascript", "text/ecmascript",
}


class _Inline(HTMLParser):
    """Collects the text of every executable <script> and every <style>.

    Also records the things that would make the policy a lie if they appeared:
    an external script or stylesheet (there is no origin in the policy that
    could load one), and an inline event handler attribute (which a hash does
    not cover). Both are reported rather than accommodated.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts, self.styles = [], []
        self.external, self.handlers = [], []
        self._buf, self._kind = None, None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        for k in a:
            # `on` alone is not an event handler; `onerror` is. The HTML spec's
            # set is open-ended, so the shape is the test.
            if k.startswith("on") and len(k) > 2:
                self.handlers.append(f"<{tag} {k}>")
        if tag == "script":
            if "src" in a:
                self.external.append(a["src"])
                return
            if a.get("type", "").strip().lower() in _JS_TYPES:
                self._buf, self._kind = [], "script"
        elif tag == "style":
            self._buf, self._kind = [], "style"
        elif tag == "link" and "stylesheet" in a.get("rel", "").lower():
            self.external.append(a.get("href", ""))

    def handle_data(self, data):
        # Called once per chunk, which is why it accumulates rather than
        # assigns: a long script arrives in pieces and hashing the last piece
        # would produce a digest no browser will ever compute.
        if self._buf is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if self._buf is not None and tag == self._kind:
            (self.scripts if self._kind == "script" else self.styles).append(
                "".join(self._buf))
            self._buf, self._kind = None, None


def _sha256(text):
    return "sha256-" + base64.b64encode(
        hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii")


def digests(html):
    """(script digests, style digests, external refs, handler attributes).

    The last two are always empty in a page this project renders, and the
    checks assert that. They are returned rather than ignored so the thing that
    would quietly break the policy is a value somebody can test for.
    """
    p = _Inline()
    p.feed(html)
    p.close()
    return ([_sha256(s) for s in p.scripts], [_sha256(s) for s in p.styles],
            p.external, p.handlers)


def csp_for_text(html):
    """The policy for this page's own markup."""
    scripts, styles, _ext, _on = digests(html)
    return policy(scripts, styles)


_CACHE = {}


def csp_for_file(path):
    """The policy for the page on disk, or the baseline if it is not a page.

    Keyed on (path, mtime_ns, size) so a render from any process is picked up
    without a restart and without re-reading a 600 KB page on every request.
    The cache is bounded by clearing it rather than evicting: there are two
    pages, so it holds two entries, and a path that changes identity every
    request would be a bug worth noticing rather than a cache to tune.
    """
    try:
        st = os.stat(path)
    except OSError:
        return BASELINE_CSP
    key = (path, st.st_mtime_ns, st.st_size)
    hit = _CACHE.get(key)
    if hit:
        return hit
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return BASELINE_CSP
    val = csp_for_text(text)
    if len(_CACHE) > 8:
        _CACHE.clear()
    _CACHE[key] = val
    return val


# The NAME is part of the fact. send_header("Content-Security-Policy", …) in
# server.py was a second spelling of it — enough for seams.py to report a copy,
# and rightly: a sender that names a header itself is a sender that can be
# given the wrong one. Callers ask for the pairs and send what they get.
CSP_HEADER = "Content-Security-Policy"


def for_response(csp):
    """Every (name, value) a response carries, policy included."""
    return list(STATIC.items()) + [(CSP_HEADER, csp)]
