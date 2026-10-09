"""The Guide tab (id `help`) — how the pool controller is wired, configured and recovered.

WHY THIS LIVES IN THE SITE AND NOT IN A REPO README

It is read at the panel. Breaker off, enclosure open, frequently on a phone,
sometimes with no working internet and always at the moment a lookup is least
convenient. A README in a checkout on a laptop indoors is not available then.
So the wiring map, the pinout, the verification commands and the failure modes
are carried inline here, in the same page as the controls, and the page pulls
nothing from a CDN.

WHAT IS WRITTEN HERE IS WHAT THIS HARDWARE DID

Not what the manual claims. Several of these entries exist because the
documented answer was wrong on this panel and cost a day to disprove — the
colour collision, the PDA label matching, the extended device ID. Those are
recorded with the evidence that settled them, because the next person to read
this (which is usually the same person, eighteen months later) will otherwise
re-run the same experiment.

PUBLIC VERSUS AUTHENTICATED

The wiring is reference material and is not a secret — it is in the Jandy
manual and the AqualinkD wiki, just less accurately. What is not published is
which host this estate runs on, which account it runs as, and the serial number
of the adapter. Those come through _sub() so the public build renders a
placeholder in exactly the place the authenticated build renders the real
value, rather than losing the sentence around it.
"""

import html

from . import commands as C
from . import config
from .style import SERIES as _SERIES
from . import brand as _brand

# The project's own home. One constant, because it appears in several places
# here and in the footer, and a repository that has moved should move once.
REPO = "https://github.com/jchirayath/poolhound"
REPO_NAME = REPO.split("//", 1)[1]

# Where AqualinkD listens, and what that means, stated ONCE.
#
# Help used to draw ":8000, localhost only" in both architecture diagrams, and
# say "its own loopback" in both of their <desc>s and again in the components
# table -- while the settings table two sections further down prescribed
# listen_address = http://0.0.0.0:8000, which is every interface on the house
# LAN. Four labels and a config row disagreeing about the exposure of the ONE
# service in this system that has no authentication, and disagreeing in the
# direction that understates it. A reader who believed "localhost only" would
# conclude the house LAN is not part of the attack surface, which is the single
# conclusion this product's whole shape says they must not draw.
#
# So the address and the sentence about it live here, next to each other, and
# the diagrams, the prose and the aqualinkd.conf table all read them.
AQUALINKD_BIND = "http://0.0.0.0:8000"
AQUALINKD_REACH = ("every interface on the Pi \u2014 anything on the house "
                   "network can reach it, and it has no authentication at all")
# DERIVED, so the third and fourth mentions of the port are not two more copies
# of it. An SVG has eighty pixels for a label and cannot carry the whole bind
# address; it can carry ":8000", and ":8000" typed by hand is the thing that
# goes stale when somebody changes listen_address in aqualinkd.conf.
_PORT_LABEL = ":" + AQUALINKD_BIND.rsplit(":", 1)[-1]

# Identifying values, kept out of the public build. The placeholder is chosen
# to still read as an instruction: "your-pi.local" tells a reader what shape of
# thing belongs there, where a stripped span would leave a hole in the sentence.
# The real values come from config.toml, which is NOT in this repository. Only
# the placeholders are here.
#
# This matters for more than tidiness: the private build substitutes the real
# hostname, account and adapter serial into the page, so hard-coding them in the
# source published them to anybody who read the source — which, for an
# open-source project, is everybody. A deployment that sets none of them renders
# the placeholders in both builds and loses nothing but specificity.
PLACEHOLDER = {
    "host":   "your-pi.local",
    "user":   "pi",
    "serial": "XXXXXXXX",
}

# `site.host` means two incompatible things and this file reads the wrong one.
#
# watch.site_url() takes site.host as the PUBLIC WEB ADDRESS: it prefixes
# https:// and appends #home, and that string is the one call to action in every
# alert email. This page takes the same key as the PI'S LAN HOSTNAME and prints
# it after `ssh pi@`. Set it correctly for notifications and Help tells the owner
# to ssh to their public web host; set it for the Pi and every alert on their
# phone links to https://raspberrypi.local/#home. One of the two was always
# going to be wrong and nothing documented either meaning.
#
# So the Pi's name gets its own key, `site.pi_host`, and site.host stays what
# watch.py has always used it for. The fallback keeps every existing install
# rendering exactly what it renders today until somebody sets the new key --
# there is no upgrade step and no install breaks.
_ALIASES = {"host": ("pi_host", "host")}

def _private():
    from . import config
    try:
        site = (config.load().get("site") or {})
    except SystemExit:
        site = {}
    out = {}
    for k in PLACEHOLDER:
        value = ""
        for key in _ALIASES.get(k, (k,)):
            value = str(site.get(key) or "").strip()
            if value:
                break
        out[k] = (value or PLACEHOLDER[k], PLACEHOLDER[k])
    return out

def _sub(text, public):
    for key, (real, generic) in _private().items():
        text = text.replace(f"@{key}@", generic if public else real)
    return text

# ---------------------------------------------------------------- components

def _table(caption, head, rows):
    """A reference table. The caption carries the finding, not a restatement of
    the column headers — a caption that says "wire colours" tells the reader
    nothing they cannot see."""
    th = "".join(f"<th>{c}</th>" for c in head)
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    # The caption sits OUTSIDE the scrolling box. Inside it, a <caption> takes
    # the table's width rather than the viewport's, so on a phone the sentence
    # that carries the finding runs off the screen and has to be scrolled to
    # sideways — while the grid it describes is the part that should scroll.
    cap = f'<p class="ref-cap">{caption}</p>' if caption else ""
    return (f'{cap}<div class="scroll"><table class="ref">'
            f"<thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>")

def _danger(title, body):
    return (f'<div class="danger"><p class="danger-h">{title}</p>{body}</div>')

def _shell(lines):
    """A command block. Comments are kept because at the panel the reason a
    command is being run matters as much as the command."""
    out = []
    for ln in lines:
        cls = "c" if ln.startswith("#") else ""
        out.append(f'<span class="{cls}">{html.escape(ln)}</span>')
    return f'<pre class="cmd">{chr(10).join(out)}</pre>'


def _public_tabs():
    """The tabs the public build actually carries, from the module that cuts it.

    This used to be a second literal tuple, sitting here, next to the links that
    depend on it -- and the comment justifying that placement said the two
    "cannot drift". They were already two: render.py declares the same tuple
    beside PRIVATE_TABS and its excision, and that copy had no readers at all.
    Whichever one a later editor found, there was a 50% chance of changing the
    dead one and shipping a public page whose Help links into tabs that are not
    in the file -- a dead end that reads as a broken page, to anonymous visitors,
    since Help is served to them byte-for-byte.

    Imported at call time rather than at module scope: render imports help, so
    the reverse at import time is a cycle.
    """
    from . import render
    return render.PUBLIC_TABS

# WHAT HAPPENS WHEN A PATH IS NOT ON THE PRIVATE LIST. One sentence, because
# there were two and one of them was the claim the Caddyfile retracted.
EDGE_FAILURE_MODE = (
    "A path the list does not name is still proxied through \u2014 the last rule "
    "is a catch-all \u2014 and is then refused by poolhound itself, which issues "
    "no session token to a caller the proxy did not vouch for and requires admin "
    "for any route its own policy does not mention. So forgetting to list one "
    "costs a layer rather than opening a door, and <code>bin/selftest</code> "
    "compares the two lists so it cannot go unnoticed.")

def _count(n):
    """A small number as a word, because "4 tabs are public" reads as a count
    of something rather than as a sentence. Falls back to the digits above
    what prose spells out."""
    return ("no", "one", "two", "three", "four", "five", "six", "seven",
            "eight", "nine", "ten")[n] if n <= 10 else str(n)


def _tablink(tab, label, public=False):
    """Link to a tab, or name it plainly when this build does not have it.

    data-tab-link is what the page script already binds for anchors that cross
    tabs, so Help can point at a tab and land there \u2014 which was the missing
    half of "if you do not understand this tab, read Help": all seven of Help's
    internal links used to point only at itself.
    """
    # NB: the class must not contain the word "priv" \u2014 the public build strips
    # any span whose class does, which is how an earlier spelling of this note
    # vanished from the public page while rendering fine on the authenticated
    # one. The stripper is right; the name was wrong.
    if public and tab not in _public_tabs():
        return (f'{_c(label)} <span class="needs-signin">(needs sign-in \u2014 not on '
                f'this page)</span>')
    return f'<a href="#{tab}" data-tab-link="{tab}">{_c(label)}</a>'

def _link(url, text):
    """An outward link. rel=noreferrer as well as noopener: these go to vendor
    sites, and there is no reason to tell them which page of a private pool
    dashboard the reader came from."""
    return (f'<a class="ext" href="{html.escape(url, quote=True)}" target="_blank" '
            f'rel="noopener noreferrer">{text}</a>')

def _c(s):
    return f"<code>{html.escape(s)}</code>"

# ------------------------------------------------------- architecture diagram

# Drawn as inline SVG rather than in a diagram language, for the same reason as
# the wiring drawing below: this page fetches nothing, from anywhere. The
# README's version of this picture is mermaid because GitHub renders mermaid;
# nothing renders it here, and adding something that would means adding a third
# party to a page that reads this household's data.
#
# THERE ARE TWO DRAWINGS, NOT ONE
# The first is the shape — five boxes and the direction of every arrow, which is
# what somebody arriving at this tab needs and all they need. The second names
# every service, port and cadence, and is a reference to come back to. One
# drawing carrying both jobs was either too thin to answer a question or too
# dense to answer the first one.

# ----------------------------------------------------------------- the logos
#
# WHY THESE ARE DRAWN HERE TOO
#   Same rule as icons.py: the page loads nothing from anywhere, so a vendor's
#   own PNG is not an option and neither is a CDN. These are schematic marks,
#   about as much of each logo as survives being 20 pixels tall.
#
#   They are the one place in this drawing that carries a literal colour rather
#   than a token, and for the same reason the wiring diagram does: a mark has a
#   real colour, and Jandy blue in the light theme and Jandy blue in the dark
#   theme are the same blue. Each one is a self-contained badge or glyph — white
#   on its own fill, or a shape with no ground — so none of them depends on what
#   is behind it. The glyphs that are ours rather than a vendor's use
#   currentColor and inherit the token like everything else.
#
#   Every one sits beside a text label that says the same thing, so all of them
#   are decorative and none is announced.
_LOGOS = {
    # (native width, native height, markup in that coordinate space)
    "jandy": (64, 24,
        '<rect width="64" height="24" rx="4" fill="#0072ce"/>'
        '<text x="32" y="17" text-anchor="middle" font-family="system-ui,sans-serif"'
        ' font-size="13" font-weight="700" letter-spacing="1.4" fill="#ffffff">JANDY</text>'),
    "leslies": (64, 24,
        '<rect width="64" height="24" rx="4" fill="#0a5ca8"/>'
        '<text x="32" y="17" text-anchor="middle" font-family="Georgia,serif"'
        ' font-style="italic" font-size="14" fill="#ffffff">Leslie&#8217;s</text>'),
    "usb": (24, 24,
        '<g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">'
        '<path d="M12 20.4V5.2"/><path d="M12 13.6 7.4 9.8"/><path d="M12 16.4 17.6 11.8"/></g>'
        '<circle cx="12" cy="21" r="2.2" fill="currentColor"/>'
        '<path d="M12 2.2 14.8 7.2 9.2 7.2z" fill="currentColor"/>'
        '<rect x="5.4" y="7.8" width="4" height="4" rx=".6" fill="currentColor"/>'
        '<circle cx="17.6" cy="11.6" r="2" fill="currentColor"/>'),
    "rpi": (24, 24,
        '<g fill="#75a928">'
        '<ellipse cx="9" cy="5.6" rx="3.4" ry="1.8" transform="rotate(-38 9 5.6)"/>'
        '<ellipse cx="15" cy="5.6" rx="3.4" ry="1.8" transform="rotate(38 15 5.6)"/></g>'
        '<g fill="#c7203e"><circle cx="12" cy="10.8" r="2.15"/><circle cx="8.9" cy="12.7" r="2.15"/>'
        '<circle cx="15.1" cy="12.7" r="2.15"/><circle cx="10.3" cy="15.7" r="2.15"/>'
        '<circle cx="13.7" cy="15.7" r="2.15"/><circle cx="12" cy="18.5" r="2.15"/></g>'),
    "docker": (24, 24,
        '<g fill="#2496ed">'
        '<rect x="6.6" y="10.2" width="3" height="2.7" rx=".3"/>'
        '<rect x="10.1" y="10.2" width="3" height="2.7" rx=".3"/>'
        '<rect x="13.6" y="10.2" width="3" height="2.7" rx=".3"/>'
        '<rect x="10.1" y="7" width="3" height="2.7" rx=".3"/>'
        '<rect x="13.6" y="7" width="3" height="2.7" rx=".3"/>'
        '<path d="M2.4 13.8h18.4c.3 2.2-.8 4.3-2.8 5.3-1.6.8-3.6 1.1-5.9 1.1-4.6 0-8.3-1.9-9.7-6.4z"/></g>'),
    "caddy": (24, 24,
        '<path d="M12 2.4 20 5.4v6.2c0 4.6-3.3 8.2-8 10-4.7-1.8-8-5.4-8-10V5.4z" fill="#0aa8d2"/>'
        '<path d="M12 8.2a2.6 2.6 0 0 0-2.6 2.6v1.1H8.8v4.9h6.4v-4.9h-.6v-1.1A2.6 2.6 0 0 0 12 8.2zm0 1.6'
        'c.6 0 1 .45 1 1v1.1h-2v-1.1c0-.55.4-1 1-1z" fill="#ffffff"/>'),
    "entra": (24, 24,
        '<rect x="2.6" y="2.6" width="8.6" height="8.6" fill="#f25022"/>'
        '<rect x="12.8" y="2.6" width="8.6" height="8.6" fill="#7fba00"/>'
        '<rect x="2.6" y="12.8" width="8.6" height="8.6" fill="#00a4ef"/>'
        '<rect x="12.8" y="12.8" width="8.6" height="8.6" fill="#ffb900"/>'),
    # Two flat tones rather than a gradient: a gradient needs an id, and this
    # mark is emitted three times on the page.
    "azure": (24, 24,
        '<path d="M12 2.4 2.8 21.4h6.6L12 15.6z" fill="#0f6cbd"/>'
        '<path d="M12 2.4 21.2 21.4h-6.6L12 15.6z" fill="#3ec6f0"/>'),
    "python": (24, 24,
        '<path d="M11.8 2.4c-3 0-4.9 1.1-4.9 3v2.4h5v.9H5.2c-1.9 0-3.4 1.4-3.4 4.3 0 2.9 1.1 4.2 3 4.2'
        'h1.6v-2.8c0-2 1.5-3.4 3.5-3.4h4.4c1.8 0 3.2-1.3 3.2-3.1V5.4c0-1.7-1.7-3-4.7-3zm-2.7 2'
        'a1.05 1.05 0 1 1 0 2.1 1.05 1.05 0 0 1 0-2.1z" fill="#3572a5"/>'
        '<path d="M12.2 21.6c3 0 4.9-1.1 4.9-3v-2.4h-5v-.9h6.7c1.9 0 3.4-1.4 3.4-4.3 0-2.9-1.1-4.2-3-4.2'
        'h-1.6v2.8c0 2-1.5 3.4-3.5 3.4H9.7c-1.8 0-3.2 1.3-3.2 3.1v2.5c0 1.7 1.7 3 4.7 3zm2.7-2'
        'a1.05 1.05 0 1 1 0-2.1 1.05 1.05 0 0 1 0 2.1z" fill="#ffd43b"/>'),
    "wg": (24, 24,
        '<path d="M12 2.4S5.4 10.2 5.4 14.2a6.6 6.6 0 0 0 13.2 0C18.6 10.2 12 2.4 12 2.4z" fill="#00a6d6"/>'
        '<path d="M8.6 14.6c1.2 1.7 2.1 2.3 3.4 2.3s2.2-.6 3.4-2.3" fill="none" stroke="#ffffff"'
        ' stroke-width="1.4" stroke-linecap="round"/>'),
    "clock": (24, 24,
        '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.7"/>'
        '<path d="M12 6.6V12l3.5 2.2" fill="none" stroke="currentColor" stroke-width="1.7"'
        ' stroke-linecap="round"/>'),
    "user": (24, 24,
        '<circle cx="12" cy="8" r="3.6" fill="none" stroke="currentColor" stroke-width="1.7"/>'
        '<path d="M4.8 20.2c.7-3.9 3.5-6.1 7.2-6.1s6.5 2.2 7.2 6.1" fill="none" stroke="currentColor"'
        ' stroke-width="1.7" stroke-linecap="round"/>'),
    "key": (24, 24,
        '<circle cx="8.4" cy="12" r="4.2" fill="none" stroke="currentColor" stroke-width="1.7"/>'
        '<path d="M12.6 12H21m-3.6 0v3.2M20.4 12v2.2" fill="none" stroke="currentColor"'
        ' stroke-width="1.7" stroke-linecap="round"/>'),
    # OURS, FROM brand.py. This was a second hand-drawn copy of the mark,
    # carrying two palette colours typed in by hand — so re-picking a series
    # colour would have left the logo in the old one while every page still
    # looked plausible. One drawing, one place; seams.py knows where it lives.
    "poolhound": _brand.diagram_mark(),
}

def _dur(seconds):
    """A cooldown, in the units a person thinks in. 300 is five minutes; nobody
    reads it as five minutes while it is written 300."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    if seconds % 60:
        return f"{seconds // 60} min {seconds % 60} s"
    return f"{seconds // 60} min"

def _logo(name, x, y, h=20):
    """One mark, scaled to h pixels tall with its top-left corner at x, y.

    Emitted inline rather than as a <use> of a shared <symbol>: a sprite would
    put the definitions in one place and the drawing in another, and half of
    these appear exactly twice on the whole page.
    """
    w0, h0, body = _LOGOS[name]
    s = h / h0
    return (f'<g transform="translate({x} {y}) scale({s:g})" aria-hidden="true">'
            f'{body}</g>')

def _logo_w(name, h=20):
    """How wide that mark comes out, for laying the label beside it."""
    w0, h0, _ = _LOGOS[name]
    return w0 * h / h0

# --------------------------------------------------------- the shape of it all

# THE SCHEDULE THESE DRAWINGS STATE IS READ, NOT DRAWN IN.
#
# Seven lines in this file wrote the collectors' times out by hand -- two SVG
# arrow labels, two rows of the cron box, a summary line, a sentence about why
# the WaterGuru pull is timed where it is, and the <desc> a screen reader gets.
# Every one said 14:00 and 14:05, which the crontab had held until the CRON_TZ
# fix moved it to 23:00 and 23:05 UTC. Help says what the deployment does, so a
# diagram stating a schedule the deployment does not run is the same class of
# defect as the one that drew AqualinkD as "localhost only": the drawing is
# what somebody trusts instead of reading the file.
#
# collection.schedule() parses the crontab, which the deploy image carries, and
# Cadence.clock is that fact at the width a label next to an arrow can afford.
def _clock(tool):
    """One collector's time of day for an SVG label. Bare: the zone is stated
    once by whatever encloses it, because "23:00 UTC" does not fit twice."""
    from . import collection
    c = collection.schedule().get(tool)
    # A tree with no crontab cannot draw a time it does not have, and must not
    # invent one -- "did not run" is a third outcome here too.
    return c.clock if c else "\u2014"


def _cadence(tool):
    """The same fact as a sentence, for prose and for a <desc>."""
    from . import collection
    return collection.cadence(tool)


def _tick(tool):
    """How often CRON fires a collector, which for the two pulls is not how
    often it pulls. Drawn from the crontab's own fields so the box under the
    heading `/etc/cron.d/poolhound` shows what that file actually says."""
    from . import cadence
    minute, hour = cadence.cron_fields().get(tool, ("", ""))
    if not hour:
        return "\u2014"
    return f"{minute} {hour}".strip()


def _arch_simple(idp=""):
    """Five boxes and the direction of every arrow.

    ONE DRAWING, TWO PLACEMENTS. The Poolhound info tab shows this same figure
    as its "how it works", and both panels are in the same file — so every id
    in it is prefixed by the caller. Two copies of an SVG on one page is two
    elements answering to `ad-t`, which makes `aria-labelledby` ambiguous and
    leaves a screen reader to pick one; the arrow markers are referenced by
    `url(#...)` and would resolve to whichever came first.

    Drawing a second, simpler diagram for the landing page was the obvious
    alternative and is the thing this file exists to avoid: a third statement
    of the architecture that can disagree with the two already here. Help has
    been wrong about this exact subject before — it drew AqualinkD as
    "localhost only" in two diagrams and both of their descriptions while its
    own config table said otherwise.
    """
    p = []
    a = p.append
    a('<figure class="arch">')
    a(f'<svg viewBox="0 0 1000 626" role="img" aria-labelledby="{idp}ad-t {idp}ad-d">')
    a(f'<title id="{idp}ad-t">How the pieces of poolhound connect</title>')
    a(f'<desc id="{idp}ad-d">Left to right: a Jandy Aqualink panel with an AquaPure '
      'salt cell is wired over RS485 to an FTDI USB adapter, which plugs into a '
      "Raspberry Pi running AqualinkD and the poolhound agent. The Pi opens both "
      'connections to the web server, pushing a sample out every fifteen minutes '
      'and holding one open for commands to come back down it; nothing outside '
      'the house opens a connection toward the pool. The server runs Caddy, an '
      "Entra ID sign-in, the poolhound container and Azure storage. A browser "
      "out on the public internet reaches the server, and the server pulls "
      "WaterGuru and Leslie's once a "
      'day. There is a second route that does not involve the server at all: a '
      'browser already on the house network can reach the web interface '
      'AqualinkD serves on the Pi and control the panel directly, with no '
      'sign-in; the change is recorded only as one the agent saw. The Pi also '
      'runs an optional web server of its own, serving the household\'s pool '
      'page: it reaches AqualinkD through /poolapi, and before each command it '
      'tells the agent through /poolclaim that the change is its own, and it '
      'shows the agent\'s list of recent activity through /poolactivity.</desc>')
    a('<defs>')
    a(f'  <marker id="{idp}ph-ar-a" viewBox="0 0 10 10" refX="9.5" refY="5" markerWidth="7"'
      ' markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="var(--ink)"/></marker>')
    a(f'  <marker id="{idp}ph-ar-n" viewBox="0 0 10 10" refX="9.5" refY="5" markerWidth="6"'
      ' markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="var(--ink3)"/></marker>')
    a('</defs>')

    # -- how to read the arrows, in the space the flow does not use
    a('<text class="a-glbl" x="20" y="40">HOW TO READ THE ARROWS</text>')
    a(f'<path class="a-f" d="M20 56 L66 56" marker-end="url(#{idp}ph-ar-a)"/>')
    a('<text class="a-s" x="80" y="60">readings, pushed out by the Pi</text>')
    a(f'<path class="a-f" d="M20 80 L66 80" stroke-dasharray="6 5" marker-end="url(#{idp}ph-ar-a)"/>')
    a('<text class="a-s" x="80" y="84">commands, collected by the Pi</text>')
    a(f'<path class="a-l" d="M20 104 L66 104" marker-end="url(#{idp}ph-ar-n)"/>')
    a('<text class="a-s" x="80" y="108">requests, and the daily pulls</text>')

    # -- at the pool
    a('<text class="a-glbl" x="20" y="140">AT THE POOL</text>')
    a('<rect class="a-grp" x="4" y="150" width="668" height="418" rx="12"/>')

    a('<rect class="a-box" x="20" y="182" width="200" height="256" rx="9"/>')
    a(_logo("jandy", 36, 196, 21))
    a('<text class="a-t" x="36" y="240">Aqualink PDA-6</text>')
    a('<text class="a-s" x="36" y="257">+ AquaPure salt cell</text>')
    a('<path class="a-rule" d="M36 270 H204"/>')
    a('<text class="a-m" x="36" y="290">FILTER PUMP</text>')
    a('<text class="a-m" x="36" y="310">salt cell · heaters</text>')
    a('<text class="a-m" x="36" y="330">AUX1 pool light</text>')
    a('<text class="a-m" x="36" y="350">AUX2 sheer descent</text>')
    a('<text class="a-m" x="36" y="370">AUX3 spa light</text>')
    a('<path class="a-rule" d="M36 384 H204"/>')
    a('<text class="a-s" x="36" y="404">the only thing here that</text>')
    a('<text class="a-s" x="36" y="420">actually controls anything</text>')

    a('<rect class="a-box" x="260" y="272" width="148" height="76" rx="9"/>')
    a('<g class="a-ic">' + _logo("usb", 323, 286, 22) + '</g>')
    a('<text class="a-t" x="334" y="326" text-anchor="middle">FTDI USB-RS485</text>')
    a('<text class="a-s" x="334" y="341" text-anchor="middle">two wires, 9600 baud</text>')

    a('<rect class="a-box" x="448" y="182" width="208" height="364" rx="9"/>')
    a(_logo("rpi", 464, 196, 26))
    a('<text class="a-t" x="498" y="216">Raspberry Pi</text>')
    a('<text class="a-s" x="464" y="246">writes nothing to its card</text>')
    a('<path class="a-rule" d="M464 260 H640"/>')
    a('<text class="a-m" x="464" y="280">aqualinkd.service</text>')
    # NOT localhost. Checked on the Pi: aqualinkd listens on 0.0.0.0:8000, so
    # it is reachable from anything on the house LAN. Saying "localhost only"
    # here told the reader the opposite of the truth about the one service in
    # this system that has no authentication -- and understating an exposure in
    # a security diagram is worse than drawing nothing. CLAUDE.md has always
    # said it correctly ("anything that can reach AqualinkD on the house LAN can
    # start the spa heater"); this picture disagreed with it.
    a('<text class="a-s" x="464" y="297">:8000, open on the house LAN</text>')
    a('<text class="a-s" x="464" y="313">no authentication at all</text>')
    a('<text class="a-m" x="464" y="341">poolhound-agent.service</text>')
    a('<text class="a-s" x="464" y="358">reads it every fifteen minutes</text>')
    a('<text class="a-s" x="464" y="374">and connects OUT to the server</text>')
    a('<path class="a-rule" d="M464 388 H640"/>')
    a('<text class="a-s" x="464" y="408">it checks every command again</text>')
    a('<text class="a-s" x="464" y="424">before the panel sees it</text>')
    # THE THIRD SERVICE ON THE PI, DRAWN RATHER THAN MENTIONED. poolhound local,
    # the page the household uses at the poolside was a phrase in the LAN box above and
    # nothing in this one, so the picture of the Pi showed two services on a
    # box that runs three -- and the third is where the door claim comes from.
    # Marked optional because it is: poolhound neither installs nor needs it.
    a('<path class="a-rule" d="M464 438 H640"/>')
    a('<text class="a-m" x="464" y="458">caddy :80 · poolhound local</text>')
    a('<text class="a-s" x="464" y="475">the page at the pool, OPTIONAL</text>')
    a('<text class="a-s" x="464" y="495">/poolapi → aqualinkd</text>')
    a('<text class="a-s" x="464" y="511">/poolclaim → the agent: “mine”</text>')
    a('<text class="a-s" x="464" y="527">/poolactivity ← the agent’s log</text>')

    a(f'<path class="a-l" d="M220 310 L256 310" marker-end="url(#{idp}ph-ar-n)"/>')
    a('<text class="a-e" x="238" y="302" text-anchor="middle">RS485</text>')
    a(f'<path class="a-l" d="M408 310 L444 310" marker-end="url(#{idp}ph-ar-n)"/>')
    a('<text class="a-e" x="426" y="302" text-anchor="middle">USB</text>')

    # -- THE OTHER WAY IN, which this picture did not have.
    #
    # The diagram showed exactly one route from a person to the pool: browser →
    # the server → down the held-open connection → the agent → the panel. That
    # is the route poolhound builds, and it is not the only one that exists.
    # aqualinkd serves its own web UI on every interface of the Pi with no
    # authentication, so anybody already on the house LAN can point a browser
    # at it and drive the Jandy without poolhound, without Entra, and without
    # leaving a row in audit.csv.
    #
    # The two lines beside the Pi said so in words. Having no ARROW for it
    # meant the drawing's shape still claimed a single path, and the shape is
    # what a reader takes away from a diagram -- which is the same failure as
    # drawing the bind address as localhost, one level up: the fact was written
    # down correctly and the picture disagreed with it.
    #
    # It is drawn as a request, in the style the legend already explains, and
    # labelled with what it skips rather than only where it goes.
    a(f'<rect class="a-box2" x="448" y="24" width="224" height="96" rx="9"/>')
    a('<g class="a-ic">' + _logo("user", 464, 36, 18) + '</g>')
    a('<text class="a-t" x="488" y="50">You, on the house LAN</text>')
    a('<text class="a-s" x="464" y="74">aqualinkd’s web UI, or poolhound</text>')
    a('<text class="a-s" x="464" y="92">local on the Pi — straight to the</text>')
    a('<text class="a-s" x="464" y="110">panel, no sign-in, logged as seen</text>')
    a(f'<path class="a-l" d="M560 120 L560 178" marker-end="url(#{idp}ph-ar-n)"/>')
    # LEFT of the arrow, and it has to be. At x=566 the plate ran from 566 to
    # 670 and the "outbound only" pill starts at 646 -- an opaque white plate
    # over the label that names the whole design. Measured in the browser, not
    # read off the coordinates, which is how it got written at 566.
    a('<rect class="a-plate" x="448" y="139" width="108" height="15"/>')
    a(f'<text class="a-k" x="452" y="151">{_PORT_LABEL} · no auth</text>')

    # -- the boundary, and the only two arrows that cross it
    a('<path class="a-bd" d="M690 132 L690 568"/>')
    a('<rect class="a-box" x="646" y="128" width="88" height="20" rx="10"/>')
    a('<text class="a-k" x="690" y="142" text-anchor="middle">outbound only</text>')
    a(f'<path class="a-f" d="M656 296 L708 296" marker-end="url(#{idp}ph-ar-a)"/>')
    a('<text class="a-k" x="684" y="288" text-anchor="middle">15 min</text>')
    a(f'<path class="a-f" d="M716 324 L664 324" stroke-dasharray="6 5" marker-end="url(#{idp}ph-ar-a)"/>')
    a('<rect class="a-plate" x="645" y="330" width="86" height="15"/>')
    a('<text class="a-k" x="688" y="342" text-anchor="middle">commands</text>')

    # -- the server
    a('<rect class="a-box" x="716" y="182" width="268" height="256" rx="9"/>')
    a(_logo("poolhound", 732, 194, 22))
    a('<text class="a-t" x="762" y="211">Web server</text>')
    a('<text class="a-s" x="732" y="236">one container behind a reverse proxy</text>')
    a('<path class="a-rule" d="M732 250 H968"/>')
    a('<g class="a-ic">' + _logo("caddy", 732, 264, 17) + '</g>')
    a('<text class="a-s" x="756" y="277">Caddy — TLS, and who may do what</text>')
    a(_logo("entra", 732, 294, 17))
    a('<text class="a-s" x="756" y="307">Entra ID — sign-in for the private half</text>')
    a(_logo("docker", 732, 324, 17))
    a('<text class="a-s" x="756" y="337">poolhound — the site, API and queue</text>')
    a(_logo("azure", 732, 354, 17))
    a('<text class="a-s" x="756" y="367">Azure Files + Key Vault — data, secrets</text>')
    a('<path class="a-rule" d="M732 384 H968"/>')
    a('<text class="a-s" x="732" y="404">public · private · agent — which one a</text>')
    a('<text class="a-s" x="732" y="420">request is, is decided at the proxy</text>')

    # -- who asks, and what is read from outside
    a('<rect class="a-box2" x="716" y="24" width="268" height="100" rx="9"/>')
    a('<g class="a-ic">' + _logo("user", 732, 38, 18) + '</g>')
    # NAMED FOR WHERE IT IS, now that there are two of them. "You, in a
    # browser" was unambiguous while it was the only person in the picture;
    # beside "You, on the house LAN" it stopped being, because that one is a
    # browser too. The distinction the diagram exists to draw is which side of
    # the boundary the reader is standing on, so the labels say that and leave
    # "browser" to the body lines and the caption.
    a('<text class="a-t" x="756" y="52">You, on the public internet</text>')
    a('<text class="a-s" x="732" y="76">public: stats · chemistry · calculator</text>')
    a('<text class="a-s" x="732" y="94">signed in: control · doses · settings</text>')
    a('<text class="a-s" x="732" y="112">two builds, not one page with tabs hidden</text>')
    a(f'<path class="a-l" d="M850 124 L850 178" marker-end="url(#{idp}ph-ar-n)"/>')

    a('<text class="a-glbl" x="716" y="466">SOMEBODY ELSE\'S SERVICE</text>')
    a('<rect class="a-grp-d" x="700" y="476" width="296" height="138" rx="12"/>')
    a('<rect class="a-box" x="716" y="492" width="268" height="106" rx="9"/>')
    a(_logo("wg", 732, 504, 18))
    a('<text class="a-s" x="756" y="517">WaterGuru — pod daily + mail-in lab</text>')
    a(_logo("leslies", 732, 532, 17))
    a('<text class="a-s" x="' + f'{732 + _logo_w("leslies", 17) + 8:g}' + '" y="545">'
      'in-store bench photometer</text>')
    a('<path class="a-rule" d="M732 552 H968"/>')
    # THE CADENCE, NOT TWO TIMES. These were "23:00 and 23:05" when the crontab
    # decided and the two lines had different minutes. The hour is the setting
    # now and both sources share it, so listing them read "23:00 and 23:00" --
    # a sentence that has stopped saying anything.
    a('<text class="a-s" x="732" y="570">'
      + _cadence("wg-collect") + ', both</text>')
    a('<text class="a-s" x="732" y="586">kept apart: two labs disagreeing is data</text>')
    # Clear of the group label to its left, which it used to run through.
    a(f'<path class="a-l" d="M920 438 L920 488" marker-end="url(#{idp}ph-ar-n)"/>')

    a('</svg>')
    a('<figcaption>The pair of arrows across the dashed line is the whole design. '
      'The Pi pushes a sample out every fifteen minutes and holds one connection '
      'open for commands to come back down it; nothing outside the house ever '
      'opens a connection toward the pool. The arrow at the top of the pool is '
      'the other half of the truth: AqualinkD serves its own web interface on '
      'the Pi, so a browser already on the house network controls the panel '
      'without poolhound in the path — which is why being on that network '
      'is the thing this design treats as privileged.</figcaption>')
    a('</figure>')
    return "".join(p)

# ------------------------------------------------------- the same thing, shorter

# What the three boxes say. One copy, because the drawing comes in two layouts
# and two sets of literals would be two drawings that can disagree.
_GLANCE = (
    ("jandy", 22, "Jandy panel",
     ("runs the pumps,", "heater, salt cell", "and lights")),
    ("rpi", 28, "Raspberry Pi",
     ("reads the panel and", "passes commands to it")),
    ("poolhound", 28, "Web server",
     ("keeps every reading", "and serves this site")),
)


def _glance_box(x, y, w, which):
    """One of the three boxes, its top-left corner at x, y."""
    logo, lh, title, lines = _GLANCE[which]
    out = [f'<rect class="a-box" x="{x}" y="{y}" width="{w}" height="150" rx="10"/>',
           _logo(logo, x + 18, y + 16 + (28 - lh) // 2, lh),
           f'<text class="g-t" x="{x + 18}" y="{y + 68}">{title}</text>']
    out += [f'<text class="g-s" x="{x + 18}" y="{y + 92 + 18 * i}">{t}</text>'
            for i, t in enumerate(lines)]
    return "".join(out)


def _glance_svg(tall):
    """The drawing laid out across (a page) or down (a phone).

    Not one drawing shrunk to fit: at phone width the wide layout puts its
    lettering at seven pixels, or scrolls the web server, which is the box the
    picture is about, out of view.
    """
    k = "glv" if tall else "gl"
    p = []
    a = p.append
    vb = "0 0 360 656" if tall else "0 0 760 236"
    a(f'<svg class="g-{"tall" if tall else "wide"}" viewBox="{vb}" role="img"'
      f' aria-labelledby="{k}-t {k}-d">')
    a(f'<title id="{k}-t">poolhound in three pieces</title>')
    a(f'<desc id="{k}-d">{"Top to bottom" if tall else "Left to right"}: the Jandy '
      'Aqualink panel that runs the pool equipment, wired to a Raspberry Pi in '
      'the house, which connects out over the internet to the web server. The '
      'Pi sends readings up every fifteen minutes and collects commands back '
      'down the same connection. The server never opens a connection toward '
      'the house.</desc>')
    a(f'<defs><marker id="{k}-ar" viewBox="0 0 10 10" refX="9.5" refY="5"'
      ' markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
      '<path d="M0 0 L10 5 L0 10 z" fill="var(--ink)"/></marker></defs>')
    ar = f'url(#{k}-ar)'

    if tall:
        a('<text class="a-glbl" x="20" y="20">AT THE HOUSE</text>')
        a(_glance_box(20, 32, 320, 0))
        a(f'<path class="a-f" d="M180 186 L180 236" marker-start="{ar}" marker-end="{ar}"/>')
        a('<text class="g-k" x="192" y="216">wire</text>')
        a(_glance_box(20, 240, 320, 1))
        a('<path class="a-bd" d="M10 440 L350 440"/>')
        a('<rect class="a-pill" x="20" y="429" width="110" height="22" rx="11"/>')
        a('<text class="a-pt" x="75" y="444" text-anchor="middle">OUTBOUND ONLY</text>')
        a(f'<path class="a-f" d="M200 394 L200 486" marker-end="{ar}"/>')
        a('<text class="g-k" x="192" y="414" text-anchor="end">readings</text>')
        a(f'<path class="a-f" d="M250 486 L250 394" stroke-dasharray="6 5" marker-end="{ar}"/>')
        a('<text class="g-k" x="258" y="414">commands</text>')
        a('<text class="a-glbl" x="20" y="476">ON THE INTERNET</text>')
        a(_glance_box(20, 490, 320, 2))
    else:
        a('<text class="a-glbl" x="10" y="24">AT THE HOUSE</text>')
        a('<text class="a-glbl" x="550" y="24">ON THE INTERNET</text>')
        a(_glance_box(10, 44, 180, 0))
        a(f'<path class="a-f" d="M194 119 L246 119" marker-start="{ar}" marker-end="{ar}"/>')
        a('<text class="g-k" x="220" y="108" text-anchor="middle">wire</text>')
        a(_glance_box(250, 44, 190, 1))
        a('<path class="a-bd" d="M495 36 L495 206"/>')
        a('<rect class="a-pill" x="440" y="206" width="110" height="22" rx="11"/>')
        a('<text class="a-pt" x="495" y="221" text-anchor="middle">OUTBOUND ONLY</text>')
        a(f'<path class="a-f" d="M444 100 L546 100" marker-end="{ar}"/>')
        # Plates under the two labels, because the boundary runs through the
        # middle of both: the ground's own colour, so they read as a gap in
        # the dashed line rather than as boxes.
        a('<rect class="g-plate" x="462" y="76" width="66" height="19"/>')
        a('<text class="g-k" x="495" y="90" text-anchor="middle">readings</text>')
        a(f'<path class="a-f" d="M546 140 L444 140" stroke-dasharray="6 5" marker-end="{ar}"/>')
        a('<rect class="g-plate" x="455" y="148" width="80" height="19"/>')
        a('<text class="g-k" x="495" y="162" text-anchor="middle">commands</text>')
        a(_glance_box(550, 44, 200, 2))
    a('</svg>')
    return "".join(p)


def _arch_glance():
    """Three boxes: the panel, the Pi, the web server. The top of the landing page.

    A THIRD DRAWING, AND ONLY THE CLAIMS THE OTHER TWO ALREADY MAKE. `_arch_simple`
    argues against a simpler copy, because a copy can come to disagree with
    the original. This one is allowed because it leaves out every detail that
    could change, such as ports, services, schedules and vendors. What is left
    is the fact the whole design rests on: the panel is wired to the Pi, and
    every connection between the house and the server is one the Pi opens. If
    that stops being true, all three drawings are wrong together, and the
    longer two say so in more words.

    Two layouts in one figure, and CSS shows one: `display:none` also takes the
    hidden one out of the accessibility tree, so a screen reader meets one
    title, not two. One placement, so the ids are fixed rather than prefixed.
    """
    return ('<figure class="arch glance">' + _glance_svg(False) + _glance_svg(True)
            + '<figcaption>The Pi starts every connection to the server. It sends '
            'readings up every fifteen minutes and collects commands on the way '
            'back, so nothing on the internet can reach the pool directly. '
            '<a href="#info-built">The full picture</a> is further down.'
            '</figcaption></figure>')

# ------------------------------------------------- the same thing, named fully

def _arch_detail():
    """Every service, port and cadence — the version to come back to."""
    p = []
    a = p.append
    a('<figure class="arch wide">')
    a('<svg viewBox="0 0 1280 996" role="img" aria-labelledby="af-t af-d">')
    a('<title id="af-t">Every service in poolhound, and what carries what</title>')
    a('<desc id="af-d">The same path with the parts named. At the pool: the Jandy '
      'Aqualink PDA-6 panel and AquaPure cell, its circuits listed; an FTDI '
      'USB-RS485 cable at 9600 baud; and a Raspberry Pi running aqualinkd.service, '
      'which listens on port 8000 of every interface with no authentication and is '
      'therefore reachable by anything on the house network — a browser '
      'already on that network reaches it directly, controls the panel, and '
      'is recorded only as a change the agent saw — and '
      'poolhound-agent.service, which keeps '
      'its state in tmpfs and opens both connections outward. The Pi also runs a '
      'web server of its own, serving poolhound local, an optional page that drives AqualinkD '
      'through a reverse proxy; poolhound does not require it and does not '
      'install it, but before each command that page tells the agent, through '
      'the same proxy, that the change is its own, so the switch log can name '
      'it, and reads the agent\'s list of recent activity, the app\'s commands '
      'included, through /poolactivity. On the server: Caddy '
      'sorts every request into public, private or agent; private goes on through '
      'oauth2-proxy and Entra ID; the poolhound container holds the write API, the '
      'renderer, the command queue and the dose arithmetic; and a cron table pulls '
      f"WaterGuru {_cadence('wg-collect')} and Leslie's {_cadence('leslies')}, both "
      f"set in config.toml rather than in the crontab, which ticks hourly and lets "
      f"each collector decide whether it is due; and it runs the watchdog "
      f"{_cadence('watch')}. Not on the server: an Azure Files share with the CSVs, "
      'and an Azure Key Vault with the passwords and the agent token.</desc>')
    a('<defs>')
    a('  <marker id="ph-af-a" viewBox="0 0 10 10" refX="9.5" refY="5" markerWidth="7"'
      ' markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="var(--ink)"/></marker>')
    a('  <marker id="ph-af-n" viewBox="0 0 10 10" refX="9.5" refY="5" markerWidth="6"'
      ' markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="var(--ink3)"/></marker>')
    a('</defs>')

    # ---- zones
    a('<rect class="a-grp" x="24" y="150" width="306" height="766" rx="12"/>')
    a('<text class="a-glbl" x="42" y="176">AT THE POOL · HOUSE LAN</text>')
    a('<rect class="a-grp" x="366" y="150" width="530" height="766" rx="12"/>')
    a('<text class="a-glbl" x="384" y="176">THE WEB SERVER · A VM, DOCKER</text>')
    a('<rect class="a-grp-d" x="930" y="150" width="330" height="386" rx="12"/>')
    a('<text class="a-glbl" x="948" y="176">NOT ON THE SERVER</text>')
    a('<rect class="a-grp-d" x="930" y="572" width="330" height="278" rx="12"/>')
    a('<text class="a-glbl" x="948" y="598">SOMEBODY ELSE\'S SERVICE</text>')

    # ---- who is asking
    # THREE KINDS OF ASKER, NOT TWO. The third reaches the pool without
    # touching the server, so it is drawn on the pool's side of the picture and
    # its arrow goes down the outside of the column -- past the panel and the
    # adapter, which it does not go through -- to aqualinkd itself. See the
    # overview diagram for why the absence of this arrow was a defect rather
    # than a simplification.
    a('<text class="a-glbl" x="24" y="30">ON THE HOUSE NETWORK</text>')
    a('<rect class="a-box2" x="24" y="40" width="286" height="76" rx="9"/>')
    a('<g class="a-ic">' + _logo("user", 36, 52, 17) + '</g>')
    a('<text class="a-t" x="58" y="65">You, already on the house LAN</text>')
    a('<text class="a-s" x="36" y="88">aqualinkd serves its own web UI; a browser here</text>')
    a('<text class="a-s" x="36" y="104">drives the panel with no sign-in and no audit row</text>')
    a('<polyline class="a-l" points="100,116 100,132 34,132 34,648 40,648"'
      ' marker-end="url(#ph-af-n)"/>')
    # BESIDE ITS OWN LINE. This sat at x=320, which is two hundred pixels from
    # the polyline it describes and directly on top of "https · one public
    # hostname" -- so the one arrow that bypasses the server was labelled in
    # the middle of the label belonging to the arrow that does not.
    a('<rect class="a-plate" x="104" y="124" width="212" height="16"/>')
    a(f'<text class="a-e" x="108" y="137">http {_PORT_LABEL} · straight to the panel</text>')

    # AND THESE TWO ARE SOMEWHERE ELSE AGAIN. "Anyone" and "You, signed in" are
    # on the far side of the boundary from the box above, and nothing in the
    # picture said so -- the edge label said "one public hostname", which is a
    # fact about the server rather than about where the reader is standing. One
    # group label, over the pair that come in over the internet.
    a('<text class="a-glbl" x="600" y="30">OVER THE PUBLIC INTERNET</text>')
    a('<rect class="a-box2" x="600" y="40" width="132" height="76" rx="9"/>')
    a('<g class="a-ic">' + _logo("user", 612, 52, 17) + '</g>')
    a('<text class="a-t" x="634" y="65">Anyone</text>')
    a('<text class="a-s" x="612" y="88">Home · chemistry</text>')
    a('<text class="a-s" x="612" y="104">calculator · Guide</text>')
    a('<rect class="a-box2" x="748" y="40" width="132" height="76" rx="9"/>')
    a('<g class="a-ic">' + _logo("user", 760, 52, 17) + '</g>')
    a('<text class="a-t" x="782" y="65">You, signed in</text>')
    a('<text class="a-s" x="760" y="88">control · chemicals</text>')
    a('<text class="a-s" x="760" y="104">settings · every write</text>')
    a('<polyline class="a-l" points="666,116 666,138 439,138 439,192"'
      ' marker-end="url(#ph-af-n)"/>')
    a('<polyline class="a-l" points="814,116 814,138 670,138"/>')
    a('<text class="a-e" x="452" y="130">https · one public hostname</text>')

    # ---- the panel
    a('<rect class="a-box" x="44" y="196" width="266" height="186" rx="9"/>')
    a(_logo("jandy", 60, 212, 22))
    a('<text class="a-t" x="60" y="256">Aqualink PDA-6 Combo</text>')
    a('<text class="a-s" x="60" y="272">+ AquaPure cell · holds the schedule</text>')
    a('<path class="a-rule" d="M60 284 H294"/>')
    a('<text class="a-m" x="60" y="300">FILTER PUMP — ePump, 0x78</text>')
    a('<text class="a-m" x="60" y="318">AUX1 pool light · AUX2 sheer</text>')
    a('<text class="a-m" x="60" y="336">AUX3 spa light · AUX4/5 spare</text>')
    a('<text class="a-m2" x="60" y="354">spa + pool heaters, valves</text>')
    a('<text class="a-s" x="60" y="372">labels are matched as text, never renamed</text>')
    a('<path class="a-l" d="M177 382 L177 420" marker-end="url(#ph-af-n)"/>')
    a('<text class="a-e" x="188" y="406">RS485 · 9600 baud, 2 wires</text>')

    # ---- the adapter
    a('<rect class="a-box" x="44" y="424" width="266" height="76" rx="9"/>')
    a('<g class="a-ic">' + _logo("usb", 60, 438, 22) + '</g>')
    a('<text class="a-t" x="92" y="454">FTDI USB-RS485-WE</text>')
    a('<text class="a-s" x="60" y="476">black → orange, yellow → yellow</text>')
    a('<text class="a-s" x="60" y="492">addressed by serial number, not ttyUSB0</text>')
    a('<path class="a-l" d="M177 500 L177 538" marker-end="url(#ph-af-n)"/>')
    a('<text class="a-e" x="188" y="524">USB serial</text>')

    # ---- the pi
    a('<rect class="a-box" x="44" y="542" width="266" height="358" rx="9"/>')
    a(_logo("rpi", 60, 556, 26))
    a('<text class="a-t" x="94" y="576">Raspberry Pi</text>')
    a('<text class="a-s" x="94" y="592">two SD cards died here; it writes nothing</text>')
    a('<path class="a-rule" d="M60 606 H294"/>')
    a('<text class="a-m" x="60" y="626">aqualinkd.service</text>')
    # The same correction as the other diagram: aqualinkd binds 0.0.0.0:8000,
    # verified on the Pi with `ss -lntp`. "on 127.0.0.1" said the controller was
    # unreachable from the network when in fact anything on the house LAN can
    # drive the pool, which is the single most important security fact in this
    # system and the reason the server has no route to it.
    a('<text class="a-s" x="60" y="643">HTTP :8000, open on the LAN — no auth at all</text>')
    a('<text class="a-m" x="60" y="671">poolhound-agent.service</text>')
    a('<text class="a-s" x="60" y="688">python3, stdlib only · token from a mode-600 file</text>')
    a('<text class="a-s" x="60" y="704">state in /run, which is RAM</text>')
    a('<text class="a-s" x="60" y="720">checks every command against commands.py</text>')
    a('<path class="a-rule" d="M60 734 H294"/>')
    a('<text class="a-s" x="60" y="754">both connections are opened from here</text>')
    a('<text class="a-s" x="60" y="770">an SSE link, held open, with a heartbeat</text>')
    # AND THE THIRD SERVICE, WHICH POOLHOUND DOES NOT NEED.
    #
    # The box listed the two poolhound depends on and stopped, so a reader
    # rebuilding this Pi could not tell that poolhound local is optional and
    # installed by nothing in this repository -- nor, the other way,
    # that it is the route somebody at the poolside actually uses.
    #
    # VERIFIED RATHER THAN ASSUMED: the agent's POOLHOUND_AQUALINK points at
    # AqualinkD's own port, `poolhound-agent.service` declares no dependency on
    # the web server, and update-pi.sh probes AqualinkD on loopback. This
    # repository names the page in one place, commands.DOORS, so that the page
    # can say which changes were its own; nothing here installs it or needs it.
    # So it is marked optional, which is the fact a reader needs and the one the
    # picture was quietest about.
    a('<path class="a-rule" d="M60 784 H294"/>')
    a('<text class="a-m" x="60" y="804">caddy :80 · poolhound local — OPTIONAL</text>')
    a('<text class="a-s" x="60" y="820">a page at the pool; nothing installs it</text>')
    a('<text class="a-s" x="60" y="838">/poolapi → aqualinkd :8000, its commands</text>')
    a('<text class="a-s" x="60" y="854">/poolclaim → agent :8791, “that was mine”</text>')
    a('<text class="a-s" x="60" y="870">/poolactivity → agent, what the app did</text>')
    a('<text class="a-s" x="60" y="886">poolhound runs the same without it</text>')

    # ---- the boundary
    a('<path class="a-bd" d="M348 156 L348 844"/>')
    a('<text class="a-k" x="348" y="300" text-anchor="middle"'
      ' transform="rotate(-90 348 300)">no port forward · no tunnel · nothing dials in</text>')

    # ---- caddy, as the gate every request passes
    a('<rect class="a-box" x="384" y="196" width="110" height="596" rx="9"/>')
    a('<g class="a-ic">' + _logo("caddy", 428, 208, 22) + '</g>')
    a('<text class="a-t" x="439" y="250" text-anchor="middle">Caddy</text>')
    a('<text class="a-s" x="439" y="266" text-anchor="middle">TLS :443</text>')
    # Short, and centred high: at its full length it ran down through the
    # agent's arrow label, and two strings crossing at a right angle is two
    # strings nobody reads.
    a('<text class="a-s" x="401" y="468" text-anchor="middle"'
      ' transform="rotate(-90 401 468)">strips the identity headers first</text>')
    for y, word in ((290, "PRIVATE"), (382, "PUBLIC"), (574, "AGENT")):
        a(f'<rect class="a-pill" x="414" y="{y}" width="72" height="20" rx="10"/>')
        a(f'<text class="a-pt" x="450" y="{y + 14}" text-anchor="middle">{word}</text>')

    a('<path class="a-l" d="M494 300 L592 262" marker-end="url(#ph-af-n)"/>')
    a('<text class="a-e" x="546" y="268" text-anchor="middle">every write</text>')
    a('<path class="a-l" d="M494 392 L596 392" marker-end="url(#ph-af-n)"/>')
    a('<text class="a-e" x="546" y="384" text-anchor="middle">stats · chemistry</text>')

    # ---- the sign-in gate
    a('<rect class="a-box" x="600" y="196" width="280" height="92" rx="9"/>')
    a(_logo("entra", 616, 210, 18))
    a('<text class="a-t" x="642" y="224">oauth2-proxy → Entra ID</text>')
    a('<text class="a-s" x="616" y="248">the sign-in gate for the private half</text>')
    a('<text class="a-s" x="616" y="266">never in front of /api/agent/*: a machine</text>')
    a('<text class="a-s" x="616" y="282">cannot answer an HTML sign-in page</text>')
    a('<path class="a-l" d="M740 288 L740 326" marker-end="url(#ph-af-n)"/>')
    a('<text class="a-e" x="752" y="312">identity headers</text>')

    # ---- the application
    a('<rect class="a-box" x="600" y="330" width="280" height="290" rx="9"/>')
    a(_logo("docker", 616, 344, 20))
    a(_logo("python", 642, 344, 20))
    a('<text class="a-t" x="672" y="360">poolhound</text>')
    a('<text class="a-s" x="616" y="382">python:3.12-slim · 8787, never published</text>')
    a('<path class="a-rule" d="M616 394 H864"/>')
    a('<text class="a-m" x="616" y="414">server.py</text>')
    a('<text class="a-s" x="616" y="430">the write API — Host, Origin and token</text>')
    a('<text class="a-m" x="616" y="452">render.py</text>')
    a('<text class="a-s" x="616" y="468">index.html and admin.html — two files</text>')
    a('<text class="a-m" x="616" y="490">queue_.py</text>')
    a('<text class="a-s" x="616" y="506">where a command waits for the agent</text>')
    a('<text class="a-m" x="616" y="528">chemicals.py · commands.py</text>')
    a('<text class="a-s" x="616" y="544">the only dose arithmetic; the catalogue</text>')
    a('<path class="a-rule" d="M616 558 H864"/>')
    a('<text class="a-s" x="616" y="578">site/ is a cache — losing it costs a render</text>')
    a('<text class="a-s" x="616" y="594">pending means the panel does not yet agree</text>')

    # ---- the agent's one connection, in both directions
    a('<path class="a-f" d="M310 568 L592 568" marker-end="url(#ph-af-a)"/>')
    a('<text class="a-k" x="460" y="558" text-anchor="middle">'
      'POST /api/agent/sample every 15 min · bearer token</text>')
    a('<path class="a-f" d="M600 606 L318 606" stroke-dasharray="6 5"'
      ' marker-end="url(#ph-af-a)"/>')
    a('<text class="a-k" x="460" y="624" text-anchor="middle">'
      'queued commands, down the link the Pi opened</text>')

    # ---- the collectors
    a('<rect class="a-box" x="600" y="660" width="280" height="132" rx="9"/>')
    a('<g class="a-ic">' + _logo("clock", 616, 674, 17) + '</g>')
    a('<text class="a-t" x="640" y="688">/etc/cron.d/poolhound</text>')
    # SAID ONCE, because it does not fit four times and because leaving it off
    # entirely is how the seven-hour drift stayed invisible: the server is
    # Etc/UTC and its cron does not honour CRON_TZ.
    a('<text class="a-s" x="786" y="688">times are UTC</text>')
    # A TICK IS NOT A SCHEDULE, and the drawing has to show the difference or
    # it states a cadence the deployment does not run. The top two lines fire
    # HOURLY and cadence.py decides whether the pull happens; drawing "23:00"
    # against bin/wg-collect here would say cron pulls once a day, which is
    # what it used to do and is now the setting's job.
    for y, when, job in ((712, _tick("wg-collect"), "bin/wg-collect"),
                         (729, _tick("leslies"), "bin/leslies"),
                         (746, _clock("watch"), "bin/watch"),
                         (763, _clock("render"), "bin/render")):
        a(f'<text class="a-m2" x="616" y="{y}">{when}</text>')
        a(f'<text class="a-m2" x="700" y="{y}">{job}</text>')
    a('<text class="a-s" x="616" y="781">the two pulls tick; '
      '[collection] decides</text>')
    a('<path class="a-l" d="M620 660 L620 624" marker-end="url(#ph-af-n)"/>')
    a('<text class="a-e" x="632" y="646">collect.sh, inside the container</text>')

    # ---- what the server does not hold
    a('<rect class="a-box" x="948" y="196" width="294" height="204" rx="9"/>')
    a(_logo("azure", 964, 210, 20))
    a('<text class="a-t" x="992" y="226">Azure Files</text>')
    a('<text class="a-s" x="964" y="250">mounted /mnt/poolhound — '
      'not the server’s disk</text>')
    a('<path class="a-rule" d="M964 262 H1226"/>')
    for y, name, note in ((282, "samples.csv", "every 15 min"),
                          (302, "readings.csv · lab.csv", "WaterGuru"),
                          (322, "leslies.csv", "Leslie's"),
                          (342, "chemicals.csv", "doses you logged"),
                          (362, "corrections.csv", "the fixes")):
        a(f'<text class="a-m" x="964" y="{y}">{name}</text>')
        a(f'<text class="a-s" x="1226" y="{y}" text-anchor="end">{note}</text>')
    a('<text class="a-s" x="964" y="386">a bad reading is corrected here, never deleted</text>')

    a('<rect class="a-box" x="948" y="420" width="294" height="98" rx="9"/>')
    a('<g class="a-ic">' + _logo("key", 964, 434, 18) + '</g>')
    a(_logo("azure", 988, 434, 18))
    a('<text class="a-t" x="1014" y="448">Azure Key Vault</text>')
    a('<text class="a-s" x="964" y="472">read by the VM’s managed identity</text>')
    a('<text class="a-s" x="964" y="489">lab passwords · the agent’s bearer token</text>')
    a('<text class="a-s" x="964" y="506">no credential in the image or on the host</text>')
    a('<path class="a-l" d="M880 360 L944 360" marker-end="url(#ph-af-n)"/>')
    a('<text class="a-e" x="912" y="352" text-anchor="middle">CSVs</text>')
    a('<path class="a-l" d="M880 470 L944 470" marker-end="url(#ph-af-n)"/>')
    a('<text class="a-e" x="912" y="462" text-anchor="middle">secrets</text>')

    # ---- the labs
    a('<rect class="a-box" x="948" y="618" width="294" height="96" rx="9"/>')
    a(_logo("wg", 964, 632, 20))
    a('<text class="a-t" x="992" y="648">WaterGuru</text>')
    a('<text class="a-s" x="964" y="672">pod in the skimmer, daily · mail-in lab</text>')
    a('<text class="a-s" x="964" y="689">one API call a day, because they ask for that</text>')
    # NOT "14:00, so it lands after the 12:45 test". Both numbers were wrong
    # together: the pull is at 23:00 UTC and the pod's measurement window is
    # 19:37-21:15 UTC, of which 12:45 was the Pacific reading. The point
    # survives without restating either -- being after the pod is the property
    # that matters, not the wall-clock hour.
    a('<text class="a-s" x="964" y="706">' + _clock("wg-collect")
      + ' UTC, after the pod has measured</text>')
    a('<rect class="a-box" x="948" y="730" width="294" height="104" rx="9"/>')
    a(_logo("leslies", 964, 744, 20))
    a('<text class="a-s" x="964" y="786">in-store bench photometer</text>')
    a('<text class="a-s" x="964" y="803">no API — a three-call XHR chain, and the</text>')
    a('<text class="a-s" x="964" y="820">whole history back every time</text>')
    a('<path class="a-l" d="M880 666 L944 666" marker-end="url(#ph-af-n)"/>')
    a('<text class="a-e" x="912" y="658" text-anchor="middle">'
      + _clock("wg-collect") + '</text>')
    a('<path class="a-l" d="M880 776 L944 776" marker-end="url(#ph-af-n)"/>')
    a('<text class="a-e" x="912" y="768" text-anchor="middle">'
      + _clock("leslies") + '</text>')

    a('</svg>')
    # THE CLAIM THIS USED TO MAKE WAS RETRACTED IN THE CADDYFILE AND LEFT
    # STANDING HERE. It said a new endpoint is "closed until somebody
    # deliberately opens it". It is not: the vhost ends with a catch-all that
    # proxies anything unmatched, so a route missing from the private list
    # reaches the application anonymously and is refused by the application
    # alone. /api/reading -- a write -- had been missing, and was held by that
    # one remaining layer.
    #
    # Understating an exposure is the worst direction for a security drawing to
    # be wrong in, and this page is served to anonymous visitors byte for byte.
    a('<figcaption>Which of the three classes a request falls into is decided at '
      'the proxy rather than by the application, and the private list is written '
      'out path by path rather than as “everything except the public ones”. A '
      'path the list does not name is still proxied through — the last rule is a '
      'catch-all — and is then refused by poolhound itself, which issues no '
      'session token to a caller the proxy did not vouch for and requires '
      'admin for any route its own policy does not mention. So forgetting to '
      'list one costs a layer rather than opening a door, and '
      '<code>bin/selftest</code> compares the two lists so it cannot go '
      'unnoticed. The classes are the table below.</figcaption>')
    a('</figure>')
    return "".join(p)

# ------------------------------------------------------------------ diagram

WIRING_SVG = """
<figure class="wiring">
<svg viewBox="0 0 760 250" role="img" aria-labelledby="wd-t wd-d">
<title id="wd-t">Jandy RS485 terminal block wired to the FTDI USB-RS485 adapter</title>
<desc id="wd-d">Jandy black terminal connects to FTDI orange. Jandy yellow connects
to FTDI yellow. Jandy red and green, and the FTDI black ground lead, are all left
unconnected.</desc>
<defs><style>
  /* The box and label colours are tokens so the diagram survives both themes.
     The WIRE colours are literals and must stay that way: they are the actual
     colours of the actual wires, which is the entire point of the drawing.

     The PROSE is not a wire and does not get that exemption. Three text
     elements used to carry the wire colour as a fill, and on the light surface
     (--panel is #ffffff there) they measured 1.96:1 for "yellow to yellow",
     3.15:1 for "black to orange" and 4.11:1 for the sentence warning that
     +12 V on a signal pin destroys the adapter -- all under the 4.5 floor, the
     yellow one effectively invisible, and all of them fine in dark, so a
     single-mode check passed. The two sentences it matters most that somebody
     reads were the two hardest to read. The colour cue is carried by the wire
     drawn beside each label and by the swatch beside each pin, which is how
     the pin list has always done it.

     NOTHING IN THIS COMMENT MAY LOOK LIKE A TAG -- no angle brackets, at
     all, even here inside the comment. A style element in an HTML document
     is raw text, but this one is SVG foreign content, where the parser is
     NOT in raw-text mode: the first thing that looks like a start tag ends
     the style element, and every rule after it is discarded. Writing the
     word "text" in angle brackets in this very comment silently dropped the
     whole rule set, so .lbl, .ttl, .sm and .bx all fell back to black on a
     white box. The page still rendered and it looked entirely plausible in
     a screenshot. It was found by asking the browser for
     styleEl.sheet.cssRules.length, which answered 0. */
  /* SCOPED TO `svg`, BECAUSE A STYLE ELEMENT INSIDE AN INLINE SVG IS NOT
     SCOPED TO IT. It is part of the same document stylesheet as everything
     else, so these five bare class selectors were global -- and .lbl is also
     the class on the label span inside every tab button. Every tab in the
     product was being set in the diagram's monospace face, on both builds, by
     a rule written for a wiring drawing. Prefixing with `svg ` is the whole
     fix: nothing outside a drawing can match now, and the drawings are
     unchanged. */
  svg .lbl{font:500 13.5px ui-monospace,Menlo,monospace;fill:var(--ink2)}
  svg .ttl{font:500 14px system-ui,sans-serif;fill:var(--ink)}
  svg .sm{font:12.5px ui-monospace,Menlo,monospace;fill:var(--ink3)}
  svg .wrn{font:500 12.5px ui-monospace,Menlo,monospace;fill:var(--bad)}
  svg .bx{fill:var(--bg);stroke:var(--line)}
</style></defs>

<rect class="bx" x="18" y="26" width="228" height="198" rx="10"/>
<text class="ttl" x="34" y="52">Jandy RS485 block</text>
<text class="sm" x="34" y="70">inside the enclosure</text>

<rect class="bx" x="514" y="26" width="228" height="198" rx="10"/>
<text class="ttl" x="530" y="52">FTDI USB-RS485</text>
<text class="sm" x="530" y="70">to the Pi</text>

<rect x="34" y="88" width="17" height="17" rx="3" fill="#C6362F" stroke="var(--line2)"/>
<text class="lbl" x="60" y="101">1  Red   +12 V</text>
<rect x="34" y="120" width="17" height="17" rx="3" fill="#1A1A1C" stroke="var(--line2)"/>
<text class="lbl" x="60" y="133">2  Black  Data +</text>
<rect x="34" y="152" width="17" height="17" rx="3" fill="#D8B733" stroke="var(--line2)"/>
<text class="lbl" x="60" y="165">3  Yellow Data −</text>
<rect x="34" y="184" width="17" height="17" rx="3" fill="#3E8E52" stroke="var(--line2)"/>
<text class="lbl" x="60" y="197">4  Green  Ground</text>

<rect x="708" y="120" width="17" height="17" rx="3" fill="#D9782B" stroke="var(--line2)"/>
<text class="lbl" x="530" y="133">Orange  Data +</text>
<rect x="708" y="152" width="17" height="17" rx="3" fill="#D8B733" stroke="var(--line2)"/>
<text class="lbl" x="530" y="165">Yellow  Data −</text>
<rect x="708" y="184" width="17" height="17" rx="3" fill="#1A1A1C" stroke="var(--line2)"/>
<text class="lbl" x="530" y="197">Black   Ground</text>

<path d="M51 128 C 300 128, 430 128, 708 128" fill="none" stroke="#D9782B" stroke-width="2.5"/>
<path d="M51 160 C 300 160, 430 160, 708 160" fill="none" stroke="#D8B733" stroke-width="2.5"/>
<text class="sm" x="380" y="120" text-anchor="middle">black to orange</text>
<text class="sm" x="380" y="182" text-anchor="middle">yellow to yellow</text>

<g stroke="#D2544B" stroke-width="2">
  <path d="M96 88 l14 14 M110 88 l-14 14" transform="translate(120,0)"/>
  <path d="M96 184 l14 14 M110 184 l-14 14" transform="translate(120,0)"/>
  <path d="M0 184 l14 14 M14 184 l-14 14" transform="translate(672,0)"/>
</g>
<text class="wrn" x="380" y="228" text-anchor="middle">crossed leads stay
disconnected. +12 V on a signal pin destroys the adapter</text>
</svg>
<figcaption>Two conductors carry everything. Power and ground on both sides are
left alone.</figcaption>
</figure>
"""

# -------------------------------------------------------------------- panel

def help_panel(public=False):
    s = []
    a = s.append

    a('<p class="lede">What poolhound is for, then what it is made of \u2014 how this '
      'pool is wired, configured and recovered. The hardware half is written at the '
      'panel from what it actually did, so it can be read with the breaker off and '
      'no internet.</p>')

    # -- what the product is for ----------------------------------------------
    # FIRST, because everything after it was a runbook with no statement of what
    # the thing is. Help described a panel, an adapter and a daemon in detail and
    # never once said what the page the reader is looking at is for, or what any
    # of its tabs do; the word "calculator" appeared once, inside a table about
    # request classes. A reader who did not already know the product could not
    # learn it here.
    a('<section id="help-using"><h2>What this is for</h2>')
    a('<p class="sub">Three problems, and the tab that answers each</p>')
    a("<p>poolhound exists to solve three specific problems with running this "
      "pool. Everything on the page is one of them; nothing here is a general "
      "pool-care guide, because there are better ones and this one would go "
      "stale.</p>")
    a(_table(None, ("The problem", "Where it is answered", "What it does"),
        [("<b>Reaching the controller from anywhere</b>",
          _tablink("control", "Pool control", public),
          "The panel's own software has no password of its own, so it can never be "
          "exposed to the internet. A small agent in the house connects OUTWARD to "
          "this site and waits, which means nothing on the internet can open a "
          "connection toward the pool. Covered in detail below."),
         ("<b>Dosing from this pool's own response</b>",
          _tablink("home", "Home", public) + " and " + _tablink("chemistry", "Pool chemistry", public),
          "A chart tells you a number; this works out what to do about it, for this "
          "pool's actual volume, against the targets a salt pool wants rather than a "
          "generic band. Log what you pour on " + _tablink("chemicals", "Chemicals", public) +
          " and the page compares what was predicted with "
          "what the water then did."),
         ("<b>Knowing how much water there is</b>",
          _tablink("volume", "Pool Volume Calculator", public),
          "Every dose figure multiplies through the volume, so a guess there is a "
          "wrong answer everywhere. Trace a photograph or pick a stock design and it "
          "measures the real shape, with an uncertainty band. Open to anybody \u2014 "
          "it knows nothing about this pool and needs no sign-in.")]))
    a("<p>The volume calculator is deliberately public. It is useful to any pool "
      "owner, it reads nothing and stores nothing, and there is no reason to put a "
      "login in front of arithmetic.</p>")
    a("</section>")

    # -- what each tab actually does ------------------------------------------
    # Help described the PANEL in exhaustive detail and the PRODUCT in one table
    # of three problems. Measured on the rendered page: "setpoint" 0 hits,
    # "freeze" 0, "export" 0, "correction" 0, "Power and runtime" 0. Roughly
    # 4 KB of the 22 KB answered "what is this and is it safe" and the other
    # 18 KB was RS485 colour codes, FTDI part numbers and aqualinkd.conf
    # directives -- a build manual for one installation, served as the public
    # tab an anonymous visitor lands on. Everything that answers "what do I do
    # about my water" had arrived since Help last looked.
    #
    # The wiring stays (help.py's docstring is right: it is read at the panel
    # with the breaker off). It is just no longer the only half.
    #
    # One decision per tab, and the objects the owner authors there. Where a
    # number is quoted anywhere below it comes from the catalogue or the
    # measurement, never from here -- prose that states a number is prose that
    # goes wrong.
    a('<section id="help-tabs"><h2>What each tab does</h2>')
    a('<p class="sub">One decision per tab, and what you can author there</p>')
    # Imported at call time, like _public_tabs() above and for the same
    # reason: render imports help, so the reverse at module scope is a cycle.
    from . import render
    # COUNTED, NOT TYPED. This sentence said "Four tabs are public and read-only.
    # Three more appear once you are signed in" and both numbers were wrong by
    # the time anybody read them: Collection had been added without a row here
    # at all, and the public four became a different four when Home moved
    # across. render is the file that decides which is which.
    a(f"<p>{_count(len(render.PUBLIC_TABS))} tabs are public and read-only. "
      f"{_count(len(render.PRIVATE_TABS)).capitalize()} more appear once you "
      f"are signed in, and those are the ones that change something.</p>")
    # ROWS BY TAB, LAID OUT IN THE NAV ORDER. Written as a list, the table
    # drifted out of the order of the bar above it and then lost a tab
    # entirely -- Collection shipped with no row here, on the page that says it
    # describes every tab. Keyed by tab name and emitted from render.TAB_ORDER,
    # a missing row is a KeyError at render rather than an omission nobody
    # notices, and the order cannot disagree with the nav.
    rows = {
         "home": (_tablink("home", "Home", public),
          "<b>What, if anything, needs doing today.</b> It opens with "
          "<i>What to do next</i>, which ranks the measurements that are off "
          "target by how much waiting costs rather than by how far off they are, "
          "and it says which lab and which date each ranking came from. Below "
          "that: where the water is now, the trend, lab history as small "
          "multiples, what changed between the last two lab results, what is "
          "running right now, what the pool did, <i>Power and runtime</i> — "
          "pump hours and what they cost — and how close the model is to "
          "having enough doses to fit this pool's own response.",
          "Nothing. Home only reads."),
         "volume": (_tablink("volume", "Pool Volume Calculator", public),
          "<b>How much water there actually is.</b> Every dose figure multiplies "
          "through the volume, so a guess there is a wrong answer everywhere. "
          "Trace the outline on a photograph or pick a stock shape; it slices "
          "the outline rather than assuming length × width × 0.85, "
          "subtracts a ledge at its own depth, and returns an uncertainty band. "
          "Pool and spa are measured separately and reported separately. The "
          "totals card closes with what a refill would cost \u2014 with a unit "
          "beside the rate, because a water bill quotes per CCF or per 1,000 "
          "gallons and almost never per gallon.",
          "The traced outline, if you are signed in. It is saved as "
          + _c("pool_shape.json") + " and is the only thing in the export that "
          "was made by hand rather than collected. An anonymous visitor gets the "
          "arithmetic and nothing is stored."),
         "chemistry": (_tablink("chemistry", "Pool chemistry", public),
          "<b>What to pour, and why.</b> What the last doses actually did, what "
          "the salt cell does to pH and where the rise stops, where the common "
          "advice conflicts, the interaction map, and a dose reference worked "
          "for this pool's volume against the targets a salt pool wants rather "
          "than a generic band.",
          "Nothing. The dose arithmetic is read from one catalogue and displayed."),
         "control": (_tablink("control", "Pool control", public),
          "<b>Switch something, from wherever you are.</b> Two halves: "
          "<i>Circuits</i>, which are on or off, and <i>Setpoints</i>, which "
          "carry a number — pool heater, spa heater, freeze protection and "
          "salt cell output, each with the panel's range, a cooldown and the "
          "panel's own readback. Both are enumerated in full further down this "
          "page. There is an everything-off, and a log of what has been "
          "switched and by whom.",
          "Commands. Every one is recorded with the identity that sent it."),
         "chemicals": (_tablink("chemicals", "Chemicals", public),
          "<b>Record what you poured, so the next answer can be better than a "
          "table.</b> A dose is the input half of the measurement: without it the "
          "response cannot be attributed and the model has nothing to fit. The "
          "catalogue knows the phases and refuses a unit that does not belong to "
          "one — a solid has no gallons. Below the log, what upkeep takes: acid, "
          "baking soda, salt and stabiliser per week and per year, each worked "
          "out from this pool's doses, pH and runtime, and each saying how it "
          "is known.",
          "Doses. A dose is the one row in this product that is EDITED in place "
          "rather than corrected, because it is the record of something you did "
          "and nothing should put a wrong amount back."),
         "settings": (_tablink("settings", "Settings", public),
          "<b>Only what you change.</b> The pool's volume and targets, how "
          "often each lab is checked, the logins (kept encrypted, never "
          "displayed back), notifications, and a copy of everything to "
          "download. It held the status and the logs as well until those moved "
          "to Collection, which is where the record belongs — this tab had "
          "grown to four things you change and four you only read.",
          "Settings and credentials."),
         "collection": (_tablink("collection", "Collection", public),
          "<b>Did the data actually arrive, and is any of it wrong.</b> One "
          "card per source with when it last ran, what it returned and how "
          "often it is meant to run, then every attempt as a table — written "
          "from a <code>finally</code>, so a collector that crashes is on the "
          "record rather than being the reason there is no record. Alarms are "
          "listed with whether a notifier actually carried them, because an "
          "alarm raised and not delivered is a quiet night that was not quiet. "
          "A test you ran yourself is entered here too, because a hand-typed "
          "result is a third source alongside the two collectors.",
          "Hand-typed test results, and corrections to any reading."),
         "ask": (_tablink("ask", "Ask AI", public),
          "<b>A question answered against this pool's own record.</b> The "
          "current readings with their source and age, the targets, the volume "
          "and the recent doses are computed here and handed to a language "
          "model already worked out; it is asked to explain them, not to "
          "recalculate them. The tab prints the whole context it sends, so "
          "what leaves the house is on the screen rather than described. It "
          "does nothing until an API key is stored, and it speaks to any "
          "OpenAI-compatible endpoint \u2014 including one running on your own "
          "network, which is how to ask questions with nothing leaving at all.",
          "Nothing. The question is sent and the answer is shown; neither is "
          "written to the record."),
         "info": (_tablink("info", "Poolhound info", public),
          "<b>What this is, for somebody who has not been told.</b> The public "
          "address lands here: what the product measures, where each number "
          "comes from, and what the screens look like. The figures on it are "
          "rendered from a synthetic pool by <code>bin/screenshots</code> and "
          "say so, so the page that explains the product is not also a "
          "publication of this household's water.",
          "Nothing."),
         # Yes, this page. It is a tab, the table claims to list every tab, and
         # laying the rows out from the nav order is what made the omission a
         # KeyError instead of a thing nobody noticed -- which is also how
         # Collection turned out to have been missing since it shipped.
         "help": (_tablink("help", "Guide", public),
          "<b>How the whole thing works, and how to build one.</b> The "
          "architecture, every control the panel exposes, the parts list, the "
          "wiring, the adapter, what is safe and what is not, and what each of "
          "the other tabs is for — this table. Public, in full: an explanation "
          "that needs a sign-in explains nothing to the person deciding whether "
          "to trust it.",
          "Nothing."),
         "policies": (_tablink("policies", "Safety &amp; privacy", public),
          "<b>What to rely on this for, and what it keeps about you.</b> Chemical "
          "and remote-control safety, the no-warranty licence, the fact that "
          "there is no support, and what the site records and sends where — "
          "each statement checked against what the code does. The footer on "
          "every tab links to each of its three sections.",
          "Nothing."),
    }
    a(_table(
        "The tabs, in the order they appear. “Authored here” is the thing "
        "that only exists because a person typed it — everything else is "
        "collected.",
        ("Tab", "The decision it answers", "Authored here"),
        [rows[t] for t in render.TAB_ORDER]))

    a("<h3>Recording a test you ran yourself</h3>")
    a("<p>The Collection tab carries <b>Record a test result</b>. A strip, a "
      "drop kit or a "
      "reading from a pool shop is a third source alongside the two collectors "
      "— it lands in its own file, it is never mixed into either lab's "
      "history, and it is ranked against them by date the same way they are "
      "ranked against each other.</p>")

    a("<h3>Correcting a reading that is wrong</h3>")
    a("<p>The same tab carries <b>A reading that is wrong</b> — a fouled "
      "sample, a pod misreading, a test run on the spa by mistake. It is "
      "<b>not a delete</b>, and that is not fussiness: both labs return their "
      "whole history on every pull and the collectors match on the measurement "
      "timestamp, so a row deleted from the file is put back by the next "
      "collection. The fix appears to work and undoes itself overnight. A "
      "correction is recorded instead, and applied every time the data is read "
      "— to the tiles, the charts, the targets and the tables alike. The "
      "original is kept, because the record of what the lab said is not the same "
      "thing as the number we believe.</p>")

    a("<h3>Getting your data out</h3>")
    a("<p>Settings → <b>Your data</b> downloads everything poolhound has "
      "recorded as the CSV files it stores — the actual record rather than a "
      "report, so it stays readable without this program. Every table on Home "
      "carries its own download beside its row count. The export includes the "
      "corrections log and the audit trail, and it says in its own readme what "
      "it leaves out.</p>")

    a("<h3>The one alert nobody asked for</h3>")
    a("<p>Notifications are mostly about things you did or things that stopped. "
      "<b>Freeze protection is the exception and it is on by default.</b> The "
      "panel starts the pump by itself when the air is cold enough to risk ice "
      "in the plumbing, at whatever hour that happens, and poolhound mails you "
      "when it does. Nothing is wrong when that arrives — it is the one piece "
      "of equipment here that nobody scheduled and nobody pressed a button for, "
      "which is what makes it worth telling you about, and it runs until the air "
      "warms up so it can add hours the schedule did not ask for. If it fires in "
      "daylight in summer, the sensor or the setpoint is wrong. Its setpoint is "
      "on " + _tablink("control", "Pool control", public) +
      " and the notification can be switched off on " +
      _tablink("settings", "Settings", public) + ".</p>")

    a("<h3>Who may do what</h3>")
    a("<p>Signing in says <i>who</i>; an access policy says <i>what they may "
      "do</i>. There are three levels — <b>view</b> reads the private tabs and "
      "changes nothing, <b>operate</b> switches equipment, logs doses, records "
      "readings and fixes a bad one, and <b>admin</b> adds settings, credentials "
      "and the export. The split that matters is between acting on the pool and "
      "changing what the product knows: a guest may be trusted to turn the spa "
      "on and not to read the household's whole history.</p>")
    a("<p>Roles are <b>opt-in</b>. An install that has never written a policy "
      "behaves as it always did — everyone the sign-in admits is an "
      "administrator — because defaulting to deny would lock owners out of "
      "their own pool on an upgrade, which is a worse failure discovered at a "
      "worse moment. Once any name is listed the policy is live and an unlisted "
      "person drops to the lowest level. " +
      _tablink("settings", "Settings", public) +
      " says which of the two states this install is in. The policy is edited in "
      "the configuration file and deliberately not through this page.</p>")
    a("</section>")

    # -- is this safe ---------------------------------------------------------
    # The README argues this at length for somebody deciding whether to deploy
    # it. This is the short version for somebody who already has it and is about
    # to switch their heater from a phone in an airport. Deliberately NOT a copy
    # of that text: two copies of an argument drift, and this whole product has
    # been bitten by exactly that more than once.
    a('<section id="help-safe"><h2>Is this safe?</h2>')
    a('<p class="sub">Reaching the pool without exposing it</p>')
    a("<p>The pool controller has <b>no password of its own</b> &mdash; not a weak "
      "one, none. Anything that can reach it on the house network can start the "
      "spa heater. So poolhound never makes it reachable: there is no forwarded "
      "port, nothing on the internet can open a connection toward the pool, and "
      "there is no VPN onto the network it sits on.</p>")
    a("<p>Instead the Pi opens <b>one connection outward</b> to this site and waits "
      "on it. Commands travel back down inside the connection the pool itself "
      "opened.</p>")
    a(_table("What each layer refuses. The agent has the last word because it is "
             "the only check an attacker cannot skip by talking to something else.",
        ("Layer", "Refuses"),
        [("The page", "Offers only buttons built from the shared catalogue, so an "
                      "unsupported control cannot be drawn."),
         ("This server", "Shape, device, range, age, and a ceiling of "
                         f"{C.RATE_LIMIT} commands a minute."),
         ("The agent, at the pool",
          "All of the above again, plus per-device cooldowns, and a command it has "
          f"already run. Commands expire after {C.MAX_AGE_S // 60} minutes, so one "
          "captured now cannot be replayed later. Both of those outlive the agent "
          "process, so restarting it — which every deploy does — cannot wave "
          "a cooldown through or let a resend run twice.")]))
    a("<p><b>What that does not protect against, said plainly:</b> anybody on your "
      "home network can still reach the controller directly &mdash; that is what it "
      "is. Anybody this site lets in gets full pool control unless an access "
      "policy has been set &mdash; roles are opt-in, and "
      + _tablink("settings", "Settings", public) +
      " says which state this install is in. "
      "And somebody who took over the server could send commands the agent would "
      "accept &mdash; but they could reach nothing else in the house, because there "
      "is no route, no tunnel and no credential pointing that way. The pool also "
      "keeps running its own schedule regardless: losing this site loses the remote "
      "control and nothing else.</p>")
    a('<p class="sub">The longer argument, including why a VPN is not the answer '
      'here, is in ' + _link(REPO + "#why-this-is-safer-than-exposing-it-or-using-a-vpn",
                             "the project README") + '.</p>')
    a("</section>")

    # -- the shape of the whole thing -----------------------------------------
    # First, because every section after this one is about a single box in the
    # drawing, and a reader who does not yet know which box is which reads them
    # as a list of unrelated components.
    a('<section id="help-architecture"><h2>The parts, and how they connect</h2>')
    a('<p class="sub">The pool, the server, and Azure</p>')
    a('<p>The system runs in three places, and most of what is confusing about '
      'it stops being confusing once you know which is which. At the <b>pool</b> '
      'are the panel and a Raspberry Pi. On the <b>server</b> are the proxy that '
      'decides who may do what and the application that renders this page. In '
      '<b>Azure</b> are the readings and the passwords — deliberately not on the '
      'server. Everything else in the drawing is somebody else\'s service being '
      'read once a day.</p>')
    a(_arch_simple())
    a('<p>The pair of arrows in the middle is the design, and the rest follows '
      'from it. <b>Nothing outside the house ever opens a connection toward the '
      'pool.</b> The Pi pushes a sample out every fifteen minutes and holds one '
      'connection open for commands to travel back down; there is no port '
      'forward, no tunnel, and the server has no route in. The reason is the box '
      'next to it: AqualinkD has no authentication of any kind, so anything that '
      'can reach it can start the spa heater and burn gas. A design in which the '
      'server could reach into the house would be a design in which taking the '
      'server means taking the pool.</p>')
    a('<h3>The same thing, with every service named</h3>')
    a('<p>The drawing above is the shape; this one is the reference. It names the '
      'units that run on the Pi, the three classes the proxy sorts every arrival '
      'into, what is inside the container, when each collector runs, and the two '
      'things that deliberately do not live on the server. Everything in it is '
      'also written out below, one section per box.</p>')
    a(_arch_detail())
    a(_table(
        "What each piece is for. The last three are not on the server, which is "
        "what makes the server disposable: it can be rebuilt from the repository "
        "and nothing is lost.",
        ("Piece", "Where it runs", "What it does"),
        [("<b>Jandy panel</b>", "at the pool",
          "Runs the pump, the salt cell, the heaters, the lights and the valves, and "
          "holds the schedule. It is the only part of this that actually controls "
          "anything; everything else either watches it or asks it."),
         ("<b>AqualinkD</b>", "a Raspberry Pi, at the pool",
          "Speaks RS485 to the panel at 9600 baud and offers an HTTP API on port "
          "8000 of <b>every interface</b> of the Pi, so anything on the house network "
          "can reach it. <b>It has no authentication of any kind</b> — which is why "
          "it is never exposed to the internet, never proxied, and why the rest of "
          "this is shaped the way it is."),
         ("<b>poolhound-agent</b>", "the same Pi",
          "Reads AqualinkD every 15 minutes and pushes the sample out to the server, "
          "and holds one long-lived outbound connection on which commands arrive. Both "
          "connections are opened from this end. It writes nothing to the SD card — "
          "two have already died of write wear — so its state lives in " + _c("/run") +
          ", which is RAM. That state is the interlocks: which commands have already "
          "run, and when each device last changed. It is written to a file there "
          "rather than kept in memory, because every deploy restarts this process and "
          "a restart used to clear the gas heater's cooldown."),
         ("<b>Caddy</b>", "the server",
          "Terminates TLS and sorts every request into one of the three classes below "
          "before anything else sees it. It also strips the identity headers the "
          "application trusts, on every request, so that only Caddy can set them \u2014 "
          "and then presents a shared secret of its own, AFTER that strip, which the "
          "application checks before it reads any identity header. Stripping alone "
          "happens on the vhost, which is a path only traffic from outside takes; the "
          "secret is what makes an identity provable rather than inferred from which "
          "network the request arrived on."),
         ("<b>oauth2-proxy</b>", "the server",
          "The sign-in gate, backed by Entra ID. It stands in front of the private "
          "half and deliberately not in front of the agent's path, for the reason in "
          "the second table."),
         ("<b>poolhound</b>", "the server, in a container",
          "This application. It renders both builds of the site, serves them, and "
          "holds the queue a command waits in until the agent collects it."),
         ("<b>Azure Files share</b>", "Azure, mounted on the server",
          "The CSVs: equipment samples, pod readings, lab results and doses. Not on "
          "the server's disk, so rebuilding the server costs nothing but a render."),
         ("<b>Azure Key Vault</b>", "Azure, read by managed identity",
          "The service passwords and the agent's token, fetched at the moment they "
          "are needed. No application credential is in the image, in the compose "
          "file, or on the host. The one exception is the share's own mount key, "
          "which has to be on disk at 0600 for the mount to come back after a "
          "reboot — it cannot be fetched from a vault that is itself reached over "
          "the network."),
         ("<b>WaterGuru</b>, <b>Leslie's</b>", "their own services",
          "Pod readings and a mailed-in lab from one; an in-store bench photometer "
          "from the other. Pulled once a day. Their results are never averaged "
          "together — two labs disagreeing is data, not error.")]))

    a("<h3>Three classes of request</h3>")
    # THE SAME SENTENCE THE DIAGRAM USES, not a second copy of it. This
    # paragraph still carried the claim the Caddyfile retracted in writing —
    # "a new endpoint is then closed until somebody deliberately opens it, and
    # the cost of forgetting is a 401 rather than an exposure" — while the
    # figcaption four hundred lines above had been corrected. So the public
    # Help page stated both the true and the false version of one fact, and the
    # false one sat in the section that actually enumerates the classes.
    #
    # Understating an exposure is the worst direction for a page served to
    # anonymous visitors to be wrong in, and this project had already written
    # that sentence down about this exact claim.
    a(f"<p>Which class a request falls into is decided at the proxy rather than "
      f"by this application, and the private list is written out path by path "
      f"rather than as &quot;everything except the public ones&quot;. "
      f"{EDGE_FAILURE_MODE}</p>")
    a(_table(None, ("Class", "Who", "How it is let through"),
        [("<b>Public</b>", "anyone",
          "The stats, the chemistry reference and the volume calculator. No sign-in, "
          "on purpose: a reference nobody can open explains this pool to nobody."),
         ("<b>Private</b>", "you, via Entra ID",
          "The controls, the dose log and the settings — and the build of this page "
          "that contains them, which is a <i>different file</i> rather than the same "
          "file with tabs hidden."),
         ("<b>Agent</b>", "the Pi",
          _c("/api/agent/*") + ", on a bearer token checked by the application "
          "itself. It has to bypass the sign-in gate entirely, because oauth2-proxy "
          "answers a machine with an HTML sign-in page — which the agent cannot "
          "satisfy and would retry for ever.")]))
    a('<p>What stops a command being something the panel should not be asked to do '
      'is a separate question, and it is at the end of this tab: '
      '<a class="jump" href="#help-agent">How this page reaches the pool</a>.</p>')
    a("</section>")

    # -- the controller -------------------------------------------------------
    # Everything from here down is the build manual, and it is deliberately
    # second. It used to be the whole page.
    a('<hr style="border:0;border-top:1px solid var(--line);margin:40px 0 22px">')
    a('<p class="lede"><b>Building it.</b> Everything above is about using the page. Everything below '
      'is about the hardware it talks to — the panel, the two wires, the '
      'adapter and the daemon. It is written from what this equipment actually '
      'did rather than from what the manual claims, and it is carried in the page '
      'rather than in a file on a laptop because it is read at the panel, with the '
      'breaker off and often with no working internet.</p>')
    a('<section id="help-controller"><h2>The pool controller</h2>')
    a('<p class="sub">Jandy Aqualink, PDA-6 Combo</p>')
    a('<p>AqualinkD talks to the Jandy panel over an RS485 pair at 9600 baud '
      'through a USB adapter on the Pi. It reads temperatures, pump RPM and '
      'watts and the salt cell, it can switch the auxiliary circuits, and it '
      'holds a schedule. Everything else in this application sits on top of '
      'that one serial link.</p>')
    # Credit where the hard part was done. Every equipment reading on the
    # dashboard and every circuit anybody can switch arrives through somebody
    # else's reverse engineering of an undocumented bus.
    a('<p class="credit"><b>AqualinkD is not part of poolhound.</b> It is an '
      'independent open-source project by Shaun Feakes and its contributors, and '
      'it does the genuinely hard part: the Jandy RS485 protocol is undocumented, '
      'and AqualinkD reverse-engineered it into a clean local HTTP API. Without '
      'it none of the equipment data on this page exists and none of the controls '
      'work \u2014 poolhound simply reads its ' + _c("/api") + ' and issues '
      + _c("PUT") + 's to it. If you have a Jandy or Zodiac panel it is worth your '
      'time: ' + _link("https://github.com/sfeakes/AqualinkD", "the project") + ', '
      + _link("https://github.com/sfeakes/AqualinkD/blob/master/Protocol.md",
              "the protocol notes") + ' and '
      + _link("https://github.com/sfeakes/AqualinkD/wiki", "the wiki") + '.</p>')
    # Generated from commands.py, which the server and the agent both validate
    # against. The list here used to be typed out and had already drifted: it
    # omitted SPA -- the circuit this section's own anecdote is about -- so the
    # one enumeration a reader would trust was missing a control the product
    # ships. A hand-kept copy of a shared vocabulary only ever drifts one way.
    _PANEL_NAME = {"Filter_Pump": "FILTER PUMP", "Spa": "SPA", "Aux_1": "AUX1",
                   "Aux_2": "AUX2", "Aux_3": "AUX3", "Aux_4": "AUX4", "Aux_5": "AUX5"}
    # Switches that are real controls but are NOT circuits on the equipment
    # menu, so they have no panel wording and do not belong in this table. They
    # are listed in the control catalogue further down, which is the table that
    # claims to be complete.
    _NOT_A_CIRCUIT = {"SWG/Boost"}
    # A key in the shared vocabulary that is in neither dict is a bug, and it
    # raises here rather than vanishing. The previous version filtered on
    # `k in _PANEL_NAME` and silently dropped everything else, which is exactly
    # how salt-cell boost came to be absent from a page that said in so many
    # words that it listed every control the site offers. panels.py:630 already
    # recorded this lesson for the Control tab; Help did not learn it.
    _unnamed = [k for k in C.SWITCHES
                if k not in _PANEL_NAME and k not in _NOT_A_CIRCUIT]
    if _unnamed:
        raise RuntimeError(
            "help.py has no panel name for " + ", ".join(_unnamed) +
            " — add it to _PANEL_NAME or to _NOT_A_CIRCUIT")
    _EXTRA = {"Filter_Pump": "Jandy ePump, variable speed, bound at pump ID "
                             + _c("0x78"),
              "Aux_4": "Unused, and never renamed — the control that proves the "
                       "mechanism below",
              "Aux_5": "Unused, and never renamed"}
    _circuits = [(f"<b>{_PANEL_NAME[k]}</b>",
                  _EXTRA.get(k) or html.escape(v["label"]))
                 for k, v in C.SWITCHES.items() if k in _PANEL_NAME]
    a(_table(
        "These are the panel's own names, listed from the same vocabulary the agent "
        "validates against, so this table cannot fall behind the product. Do not "
        "rename them in aqualinkd.conf, for the reason below.",
        ("Circuit", "What it is"), _circuits))
    a(_danger("Do not rename circuits in the config",
      "<p>This is a PDA panel, and in PDA mode AqualinkD does <b>not</b> address "
      "circuits numerically. It navigates the on-screen EQUIPMENT menu and matches "
      "on <b>label text</b>. A label that does not match the panel's own wording "
      "does not merely misname a circuit — it sends the command somewhere else.</p>"
      "<p>This was tried, with friendly names for AUX1 to AUX3, and it went wrong "
      "in both possible ways at once. " + _c("POOL LIGHT") + " and " +
      _c("SHEER DESCENT") + " matched nothing in the menu, so those circuits "
      "silently stopped responding. " + _c("SPA LIGHT") + " was worse: it "
      "<b>contains</b> the word SPA, matched the panel's SPA entry, and switched "
      "on the spa and the spa heater — burning gas — every time the spa light was "
      "pressed.</p>"
      "<p>If you want friendly names, rename the circuits <b>on the panel "
      "itself</b>. AqualinkD reads them from the menu and everything downstream, "
      "this application included, inherits them safely.</p>"))
    a('<h3>The clock</h3>')
    # THE ZONE IS NAMED ONCE, in config.DEPLOY_TZ, and read here. It is not
    # prose trivia: best_lab() weighs a UTC instant against a bare local date
    # and the canonical dose timestamp is written in the host's offset, so the
    # container pins the same zone this paragraph is about. Typed out, this was
    # the second copy, and the gate that holds the Dockerfile and the compose
    # file to that constant found it.
    a('<p>AqualinkD runs with ' + _c("sync_panel_time = yes") + ', so it <b>writes '
      'the Pi\'s local time into the panel</b>. The Pi shipped set to Europe/London '
      'and was eight hours ahead for its first month, which the panel simply '
      'believed — every schedule ran at the wrong time and nothing reported an '
      'error. Both now read ' + html.escape(config.DEPLOY_TZ) + ', which is also '
      'the zone the server\'s container is pinned to \u2014 two of this product\'s '
      'answers are local-time answers. Check the whole chain agrees:</p>')
    a(_shell(["# the Pi and the panel should report the same wall clock",
              "ssh @user@@@host@ 'date; curl -s localhost:8000/api/status | jq -r .time'"]))
    a("</section>")

    # -- wiring ---------------------------------------------------------------
    # ---------------------------------------------------------- shopping list
    #
    # SPLIT BY WHAT IT BUYS YOU, not by price. Somebody arriving at this page is
    # deciding whether the project is worth starting, and the useful division is
    # "what do I need for the part I actually want" — controlling the pool needs
    # three things, measuring it needs none of them, and reaching it from
    # outside the house needs a fourth.
    #
    # NO PRICES. They rot faster than any other fact on this page and there is
    # no way to keep them honest from here. Links go to the manufacturer where
    # there is one, because a retailer URL outlives its product by about a year
    # and then quietly becomes a search page for something else.
    a('<section id="help-shopping"><h2>What you need to buy</h2>')
    # `.sub` inside Help is an uppercase EYEBROW — 11px, letter-spaced — not a
    # lede. Two sentences in one rendered as a shouted paragraph. Short label
    # here, prose in a plain <p>, which is what every other section does.
    a('<p class="sub">Required, optional, and what each one buys</p>')
    a('<p>Three groups, and only the first is needed for the thing most people '
      'come here for. Nothing on this page is affiliated with anything; the '
      'links go to whoever makes the part.</p>')

    a("<h3>To control the pool</h3>")
    a("<p>This is the half that switches circuits and sets the heaters. All "
      "three are required, and the panel is the one you cannot buy your way "
      "into \u2014 either the pool already has a compatible controller or this "
      "project is not for it.</p>")
    a(_table(
        "Required, all three. The adapter has its own section below; it is the "
        "one part with a wiring trap in it.",
        ("Part", "Why", "Notes"),
        [("<b>Jandy / Zodiac Aqualink</b> panel " + _link("https://www.jandy.com/", "jandy.com"),
          "The thing being controlled.",
          "RS or PDA. This install is a PDA-6 Combo. Already at the pool, or "
          "this is the wrong project \u2014 do not buy one to use this."),
         ("<b>FTDI USB-RS485-WE</b> cable " + _link(
             "https://ftdichip.com/products/usb-rs485-we-1800-bt/", "ftdichip.com"),
          "Speaks the panel's RS485 bus.",
          "Get the captive-cable version, not a bare module. Distributor links "
          "are in <i>The USB adapter</i> below, with the wire colours \u2014 which "
          "are the part that costs an afternoon."),
         ("<b>Raspberry Pi</b> " + _link("https://www.raspberrypi.com/products/", "raspberrypi.com"),
          "Runs AqualinkD and the agent.",
          "Any model with USB and network. A Zero 2 W is enough; this install "
          "uses a full-size board. Plus a power supply and a card \u2014 and note "
          "that nothing here writes to that card at runtime, because two of "
          "them have already died of write wear."),
         ]))

    a("<h3>To reach it from outside the house</h3>")
    a("<p>Optional, and it is a real choice rather than a formality. Without "
      "this, poolhound runs on your own network and is reachable on your own "
      "network \u2014 which is a complete, working install, and is the one that "
      "sends nothing anywhere.</p>")
    a(_table(
        "Optional. A machine that is always on and can be reached from wherever "
        "you are. This install rents one; a box on a shelf does the same job.",
        ("Option", "What it costs you", "What it buys"),
        [("<b>A small cloud VM</b> \u2014 this install uses Azure",
          "A few dollars a month, and the pool's data lives on somebody else's "
          "disk.",
          "The pool answers from anywhere, behind a real sign-in, with a "
          "certificate that is not yours to renew."),
         ("<b>A machine on your own network</b> \u2014 a NAS, a mini PC, a second Pi",
          "Nothing recurring. You own the backups and the uptime.",
          "Everything except access from outside the house. The Pi still "
          "pushes to it; it is the same software."),
         ]))

    a("<h3>To measure the water</h3>")
    a("<p>All optional, and they do not depend on each other or on anything "
      "above. A pool with none of these still gets the volume calculator and "
      "the chemistry reference; it just has nothing of its own to plot.</p>")
    a(_table(
        "Optional, and independent. More sources is strictly better \u2014 where "
        "two disagree, that gap is the data rather than an error.",
        ("Source", "What it gives", "What it costs"),
        [("<b>WaterGuru SENSE</b> " + _link("https://waterguru.com/", "waterguru.com"),
          "Free chlorine and pH, daily, without anybody doing anything. Posts a "
          "sample away for a full lab panel periodically.",
          "The device, then a running subscription for the cassettes."),
         ("<b>A Leslie's account</b> " + _link("https://lesliespool.com/", "lesliespool.com"),
          "A full bench-photometer panel \u2014 alkalinity, calcium, cyanuric acid, "
          "salt, phosphates, metals.",
          "Free, and their in-store test is free. You have to carry a sample in."),
         ("<b>A drop test kit</b> \u2014 the Taylor K-2006 is the usual answer " + _link(
             "https://www.taylortechnologies.com/", "taylortechnologies.com"),
          "Everything, whenever you want it, with no subscription and no "
          "driving. Entered by hand and plotted beside the labs.",
          "One purchase, then reagents."),
         ("<b>An AI provider key</b> \u2014 OpenAI, or anything OpenAI-compatible",
          "The Ask AI tab: questions answered against this pool's own numbers "
          "rather than pool chemistry in general.",
          "Pennies per question, or nothing at all if you point it at a model "
          "running on your own network."),
         ]))

    a("<p>The pool panel and the adapter are the only parts with a "
      "compatibility question attached. Everything else is a choice about how "
      "much you want to know and who you are willing to tell.</p>")
    a("</section>")

    a('<section id="help-wiring"><h2>Wiring the panel</h2>')
    a('<p class="sub">RS485, two conductors, nothing else</p>')
    a(_danger("Mains",
      "<p>Kill power <b>at the breaker</b>, not the panel switch. The Aqualink "
      "enclosure carries mains to the pump and heater relays. The RS485 terminals "
      "are low voltage but sit inches from wiring that can kill you. Verify dead "
      "with a non-contact tester before reaching in.</p>"))
    a("<h3>The two colour codes do not agree</h3>")
    a("<p>This is the thing that costs an afternoon. <b>Jandy and FTDI both use "
      "black and yellow, and they mean different things.</b> On the Jandy side "
      "black is the positive data line; on the FTDI cable black is ground. Wiring "
      "black to black is the intuitive move, and it produces total silence on the "
      "bus — no error, no garbage, just nothing, which reads exactly like a broken "
      "cable.</p>")
    a(_table(
        "Measured on this hardware: black to orange and yellow to yellow produced "
        "4,479 bytes in 30 seconds. Black to black produced zero.",
        ("Jandy panel", "Carries", "FTDI cable", "Connect?"),
        [("<b>Black</b>", "Data +", "<b>Orange</b>", '<span class="y">Yes</span>'),
         ("<b>Yellow</b>", "Data −", "<b>Yellow</b>", '<span class="y">Yes</span>'),
         ("<b>Red</b>", "+12 V power", "—", '<span class="n2">Leave it</span>'),
         ("<b>Green</b>", "Ground", "—", '<span class="n2">Leave it</span>'),
         ("—", "Ground, adapter side", "<b>Black</b>", '<span class="n2">Leave it</span>')]))
    a(WIRING_SVG)
    a("<h3>Joining an existing bus</h3>")
    a("<p>RS485 is a shared party line. If a keypad or iAqualink module already "
      "occupies those terminals, <b>do not remove it</b> — land the two new wires "
      "on the same screws alongside. Devices sit in parallel. Replacing one "
      "instead of paralleling onto it is what kills a working keypad.</p>")
    a("<p>Cable: 24 AWG twisted pair, 100 Ω, roughly 16 pF/ft. The twist matters "
      "more than the gauge — untwisted bell wire is the commonest source of "
      "checksum errors. Route it away from the pump and heater power leads.</p>")
    a("<h3>Procedure</h3>")
    a("<ol class='proc'>"
      "<li>Cut power at the breaker and confirm dead. Unplug the adapter from the Pi.</li>"
      "<li>Find the four-position RS485 block inside the enclosure.</li>"
      "<li>Land <b>Jandy black to FTDI orange</b> and <b>yellow to yellow</b>. "
      "Strip about 6 mm, keep the pair twisted right up to the screws, tug-test each.</li>"
      "<li>Leave red, green and the adapter's black ground lead unconnected.</li>"
      "<li>Restore panel power, <b>then</b> plug the adapter into the Pi.</li></ol>")
    a("<h3>Verify before trusting it</h3>")
    a(_shell(["# is the adapter present? it is addressed by its own serial number",
              "ls -l /dev/serial/by-id/",
              "",
              "# is the bus actually talking? expect a few thousand bytes",
              "sudo timeout 30 cat /dev/ttyUSB0 | wc -c",
              "",
              "# what the daemon makes of it",
              "sudo journalctl -u aqualinkd -f"]))
    a(_table(None, ("Symptom", "Cause"),
        [("<b>Total silence</b>, zero bytes",
          "Almost always the colour collision: black landed on the adapter's ground. Move it to orange."),
         ("Garbage or checksum errors",
          "Data + and − swapped. Exchange the two at the adapter. Harmless, and a normal first attempt."),
         ("Intermittent",
          "Untwisted or overlong cable, or routed beside pump power."),
         ("Worked, then stopped",
          "Check " + _c("ls /dev/serial/by-id/") + " — USB or power, not wiring."),
         ("Panel keypad died",
          "An existing device was replaced instead of paralleled onto.")]))
    a("</section>")

    # -- adapter --------------------------------------------------------------
    a('<section id="help-adapter"><h2>The USB adapter</h2>')
    a('<p class="sub">FTDI FT232R, USB-RS485-WE</p>')
    a(_table(None, ("Property", "Value"),
        [("Chip", "FTDI FT232R, " + _link(
            "https://ftdichip.com/products/usb-rs485-we-1800-bt/",
            "USB-RS485-WE-1800-BT") + " captive cable"),
         ("Serial", _c("@serial@")),
         ("Device path", _c("/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_@serial@-if00-port0")),
         ("Bus speed", "9600 baud"),
         ("Powered by", "USB — never from the panel's 12 V"),
         ("Where to buy", _link("https://www.digikey.com/en/products/detail/ftdi-future-technology-devices-international-ltd/USB-RS485-WE-1800-BT/1836397", "DigiKey")
          + " · " + _link("https://www.mouser.com/ProductDetail/FTDI/USB-RS485-WE-1800-BT", "Mouser")
          + " · " + _link("https://www.amazon.com/s?k=FTDI+USB-RS485-WE-1800-BT", "Amazon")),
         ("Datasheet", _link("https://ftdichip.com/wp-content/uploads/2020/07/DS_USB_RS485_CABLES.pdf",
                             "USB to RS485 cables (PDF)"))]))
    a(_danger("Address it by serial number, not ttyUSB0",
      "<p>" + _c("/dev/ttyUSB0") + " is assigned in probe order, so plugging in any "
      "other USB-serial device can silently move the pool controller to " +
      _c("ttyUSB1") + ". The config still points at a real device, and the panel "
      "simply stops answering. The " + _c("by-id") + " path is derived from the "
      "chip's own serial number and cannot be reassigned.</p>"))
    a("<p>" + _c("ftdi_low_latency=YES") + " is set. The FT232R defaults to a 16 ms "
      "read timeout, which is long enough to straddle the gaps between Jandy frames "
      "and make a healthy bus look intermittent.</p>")
    a("</section>")

    # -- config ---------------------------------------------------------------
    a('<section id="help-config"><h2>AqualinkD settings</h2>')
    a('<p class="sub">/etc/aqualinkd.conf, the non-obvious ones</p>')
    a(_table(None, ("Setting", "Value", "Why"),
        [(_c("panel_type"), "PD-6 Combo",
          "The installed default claimed RS-8. The wrong panel type mis-maps every button."),
         (_c("device_id"), _c("0x60"),
          "Pinned. Left at " + _c("0xFF") + " it re-probes the bus for about 70 seconds on every start."),
         (_c("listen_address"), _c(AQUALINKD_BIND),
          # NOT "collides with Caddy". Caddy runs on the server; the Pi runs
          # aqualinkd and the agent and nothing else, and has no route to
          # Caddy at all. README's copy of this same table always said it
          # correctly, so the two documents disagreed about the reason.
          "The default is port 80, which collides with anything else serving HTTP "
          "on the Pi. The directive is "
          + _c("listen_address") + ", not " + _c("socket_port") + ". Note the "
          "address: this is <b>" + AQUALINKD_REACH + "</b>, which is why nothing "
          "ever exposes or proxies it."),
         (_c("read_RS485_swg") + "<br>" + _c("read_RS485_ePump") + "<br>" + _c("read_RS485_vsfPump"),
          "yes",
          "Sniffs devices directly off the bus. This is what surfaces pump RPM, watts and the salt cell."),
         (_c("button_01_pumpID"), _c("0x78"),
          "Binds the discovered ePump to the filter circuit. Without it the daemon finds the pump and discards the readings."),
         (_c("enable_scheduler"), "yes",
          "Needs the " + _c("cron") + " package <b>and</b> a pre-existing " + _c("/etc/cron.d/aqualinkd") + "."),
         (_c("sync_panel_time"), "yes",
          "Writes the Pi's local time into the panel. Requires the Pi's timezone to be right.")]))
    a(_danger("This panel is PDA-only",
      "<p>The documented route to variable-speed pump control is " +
      _c("extended_device_id") + ", and it does not work here. The bus capture "
      "shows the master polling " + _c("0x60") + " to " + _c("0x63") + " and never "
      "touching the RS keypad slots " + _c("0x08") + " to " + _c("0x0b") + ". Those "
      "IDs look free only because this panel cannot use them. Setting " + _c("0x41") +
      " was tried; the panel refused to answer and AqualinkD disabled it by "
      "itself.</p><p>Pump telemetry comes from the " + _c("read_RS485_*") +
      " sniffing instead, which is not bound by the PDA limitation. Do not re-run "
      "the extended-ID experiment.</p>"))
    a(_danger("Schedules need a file nobody creates",
      "<p>The scheduler writes its jobs into " + _c("/etc/cron.d/aqualinkd") + " and "
      "will not create that file. Without it every schedule save is dropped with " +
      _c("Open file failed") + " in the log <b>while the UI still shows the schedule "
      "as accepted</b>. The " + _c("cron") + " binary can also be missing even though "
      + _c("systemctl is-active cron") + " reports active — that name resolves to a "
      "different unit on trixie.</p>"))

    a('<h3>AqualinkD itself</h3>')
    a('<p>Everything above is about <i>this</i> panel. The project, its full '
      'configuration reference and its issue tracker are the place to go for '
      'anything not recorded here — particularly for a different panel type, '
      'where almost none of the PDA-specific findings apply.</p>')
    a(_table(None, ("Resource", "Link"),
        [("Project", _link("https://github.com/sfeakes/AqualinkD", "github.com/sfeakes/AqualinkD")),
         ("Full config reference", _link(
             "https://github.com/sfeakes/AqualinkD/blob/master/release/aqualinkd.conf",
             "aqualinkd.conf, annotated")),
         ("Wiki and setup guide", _link("https://github.com/sfeakes/AqualinkD/wiki", "AqualinkD wiki")),
         ("Issues", _link("https://github.com/sfeakes/AqualinkD/issues", "issue tracker")),
         ("Jandy RS485 protocol notes", _link(
             "https://github.com/sfeakes/AqualinkD/blob/master/Protocol.md", "Protocol.md")),
         ("Panel manual", _link(
             "https://www.jandy.com/-/media/zodiac/global/downloads/h/h0574800.pdf",
             "Jandy AquaLink RS owner manual (PDF)"))]))
    a("</section>")

    # -- this application -----------------------------------------------------
    a('<section id="help-agent"><h2>How this page reaches the pool</h2>')
    a("<p>The controller has <b>no authentication of its own</b>. Anything that can "
      "reach it on the network can switch the heater, which is why it is never "
      "exposed and why this page does not proxy to it.</p>")
    a("<p>Instead the Pi runs an agent that holds a long-lived connection <i>out</i> "
      "to this site and waits for commands. Nothing on the internet can open a "
      "connection toward the pool; the pool opens one toward here. A command is "
      "checked three times on the way — against the catalogue in the browser, "
      "again at this server, and again at the agent, which is the check that "
      "actually protects the panel because it is the only one an attacker cannot "
      "skip by talking to something else.</p>")
    # THREE MECHANISMS, BECAUSE "IS IT ALIVE" IS THREE QUESTIONS, and the page
    # that says the controls work should say what keeps them working. Each of
    # these was added after the failure it answers: a deleted start job (71
    # hours dark), and a process that stayed `active` while reaching nothing.
    a("<p>If that connection is not there, every control on this site is dimmed "
      "and says so — the page asks this server whether the agent is present "
      "rather than assuming it is. Three separate mechanisms keep it there, "
      "because <i>is it alive</i> turns out to be three different "
      "questions:</p>")
    a(_table(
        "What keeps the agent running. All three are needed; each was added "
        "after a real outage the others could not see.",
        ("Mechanism", "The question it answers", "What it caught"),
        [(_c("Restart=always"), "<b>It died.</b>",
          "An ordinary crash, which is the only one of the three that is "
          "obvious."),
         (_c("poolhound-agent-ensure.timer"), "<b>It is not running.</b>",
          "A start job the system removed at boot: the service read "
          "&quot;inactive, success, 0 restarts&quot; for 71 hours while "
          "reporting itself enabled. A check every five minutes starts it and "
          "logs one line when it had to."),
         (_c("WatchdogSec"), "<b>It is running and not working.</b>",
          "A reboot with the name service not yet up: the process sat there "
          "for eight minutes retrying and reaching nothing, which no "
          "&quot;is it running&quot; check can see. The agent reports in only "
          "while it has actually heard from this server, so a stuck one is "
          "replaced by a fresh one that can resolve and reconnect.")]))
    # THE enumeration. It used to read "every control this site offers is in the
    # table above" and point at the circuit table, which is seven circuits --
    # while the Control tab shipped a whole second half: four setpoints and
    # salt-cell boost, none of them named anywhere in Help, in either build.
    # The word "setpoint" did not occur once on the rendered page. A list a
    # reader is told is complete has to be complete, so both halves are
    # generated from the same catalogue the browser, this server and the agent
    # all validate against, and a control added to commands.py turns up here
    # without anybody having to remember this page exists.
    _UNIT = {"SWG/Percent": "%"}
    _degF = "\u00b0F"
    a("<h3>Everything this site can ask the panel to do</h3>")
    a(_table(
        "Switches: on or off, and nothing else. The cooldown is the agent's, "
        "which is the last of the three refusals and the only one an attacker "
        "cannot skip by talking to something else.",
        ("Switch", "In the catalogue", "Cooldown", "Why it has one"),
        [(f"<b>{html.escape(v['label'])}</b>", _c(k), _dur(v["cooldown_s"]),
          html.escape(v["why"]) if v.get("why") else
          "Nothing is harmed by switching it; the delay only stops a stuck finger.")
         for k, v in C.SWITCHES.items()]))
    a(_table(
        "Setpoints: a number, not a state. \u201cIs it running\u201d and \u201cwhat "
        "is it set to\u201d are different questions, and the page keeps them apart. "
        "The range is the panel's own, checked by " + _c("bin/selftest") +
        " against the values the samples actually hold \u2014 freeze protection was "
        "declared 55\u201365 here while every sample reported 34.",
        ("Setpoint", "In the catalogue", "Range", "Cooldown", "Why the cooldown"),
        [(f"<b>{html.escape(v['label'])}</b>", _c(k),
          f"{v['min']}\u2013{v['max']}" + _UNIT.get(k, _degF),
          _dur(v["cooldown_s"]), html.escape(v.get("why") or ""))
         for k, v in C.SETPOINTS.items()]))
    a("<p>Those two tables are the whole of it, and the agent refuses anything "
      "that is not in them \u2014 including the solar valve, which is deliberately "
      "absent because the panel actuates it on its own schedule, and including "
      "every temperature and salt figure, which are collected and never written "
      "to. Each command is checked at three separate layers on the way:</p>")
    a(_table("Where a command that should not happen gets stopped.",
        ("Layer", "Refuses"),
        [("The page", "Offers only buttons built from the shared catalogue, so an unsupported control cannot be drawn."),
         ("This server", "Shape, device, range, age and a ceiling of 12 commands a minute."),
         ("The agent", "All of the above again, plus per-device cooldowns and replay of a command it already ran.")]))
    a("<p>If the agent is not connected the controls say so and do nothing. The "
      "pool carries on running its own schedule regardless — the schedule lives in "
      "the panel, not here, so losing this site loses the remote control and "
      "nothing else.</p>")
    a("</section>")

    # -- acknowledgements -----------------------------------------------------
    # Three other people's products do the work this one reasons about. Said
    # here, in the app, rather than only in a README somebody may never open.
    a('<section id="help-thanks"><h2>Built on other people\u2019s work</h2>')
    a('<p class="sub">Three products this one would not exist without</p>')
    a(_table(None, ("Project", "What it does for poolhound", "Where"),
        [("<b>AqualinkD</b><br><span class=\"sm\">Shaun Feakes and contributors</span>",
          "The Jandy RS485 protocol is undocumented. AqualinkD reverse-engineered it "
          "into a clean local HTTP API &mdash; the genuinely hard part. Every equipment "
          "reading on this page and every circuit you can switch arrives through it. "
          "It is an independent project under its own licence.",
          _link("https://github.com/sfeakes/AqualinkD", "github.com/sfeakes/AqualinkD")
          + "<br>" + _link("https://github.com/sfeakes/AqualinkD/blob/master/Protocol.md",
                           "Protocol.md")
          + "<br>" + _link("https://github.com/sfeakes/AqualinkD/wiki", "wiki")),
         ("<b>WaterGuru</b>",
          "A pod that sits in the skimmer and measures free chlorine and pH daily, "
          "and a laboratory that analyses a posted sample for the things a pod "
          "cannot &mdash; alkalinity, calcium, stabiliser, salt, phosphates.",
          _link("https://waterguru.com/", "waterguru.com")),
         ("<b>Leslie&rsquo;s</b>",
          "A bench photometer in the store, run free on a sample you carry in. It is "
          "the second opinion the whole two-labs comparison depends on: one number is "
          "a reading, two numbers that disagree is information.",
          _link("https://lesliespool.com/", "lesliespool.com")),
         ("<b>poolhound</b>",
          "This. Open source, MIT.",
          _link(REPO, REPO_NAME))]))
    a("<p>None of them is endorsed by or affiliated with this project, and any bug "
      "you find here is ours rather than theirs. The two vendor APIs are not public "
      "&mdash; poolhound reads an account\u2019s own data using that account\u2019s own "
      "credentials, the way their apps do, and every collector saves its raw response "
      "before parsing because one day those will change.</p>")
    a("</section>")

    # -- where the rest of it is ----------------------------------------------
    # Help and the README have different readers and deliberately do not carry
    # the same material. Help is read at the pool, sometimes on a phone with the
    # breaker off; the README is read on a laptop by somebody installing or
    # operating it. Rather than copy one into the other — which is how the dose
    # arithmetic ended up in two places 23% apart — each says what it does not
    # carry and points at the other.
    a('<section id="help-more"><h2>The rest of the documentation</h2>')
    a('<p class="sub">What is here, and what is on GitHub</p>')
    a("<p>This page is written for somebody standing at the pool: what each tab is "
      "for, how the panel is wired, what the settings mean, and whether it is safe. "
      "It is meant to be readable with the breaker off and no internet.</p>")
    a(_table(None, ("Question", "Where it is answered"),
        [("What is each tab for? Is it safe? How is the panel wired, what does the "
          "adapter need, which AqualinkD settings matter?",
          "<b>Here.</b> Everything above."),
         ("How do I install it &mdash; the Pi, the agent, the server, sign-in "
          "without an identity provider, the optional lab accounts?",
          _link(REPO + "#setting-it-up-component-by-component",
                "README &rarr; Setting it up, component by component")),
         ("Why is this safer than a forwarded port or a VPN, in full?",
          _link(REPO + "#why-this-is-safer-than-exposing-it-or-using-a-vpn",
                "README &rarr; Why this is safer")),
         ("What does it collect, and how are the measurement streams kept apart?",
          _link(REPO + "#three-measurement-streams-deliberately-not-merged",
                "README &rarr; Three measurement streams")),
         ("What are poolhound’s own routes, and what does each need?",
          _link(REPO + "#the-poolhound-api", "README &rarr; The poolhound API")),
         ("What do the CSV files look like, and which ones are there?",
          _link(REPO + "#data", "README &rarr; Data")),
         ("Why is the chemistry fittable at all?",
          _link(REPO + "/blob/main/THEORY.md", "THEORY.md")),
         ("Can I read the code?",
          _link(REPO, REPO_NAME) + " &mdash; MIT")]))
    a("<p>If something on this page and something in the README disagree, the code "
      "is the tiebreaker and the disagreement is a bug worth reporting.</p>")
    a("</section>")

    return _sub("\n".join(s), public)
