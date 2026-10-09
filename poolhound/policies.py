"""Safety, no support, and privacy — the one public tab of statements.

WHY THESE ARE WRITTEN FROM THE CODE

A privacy page is a list of claims about what the software does, and a claim
nobody checked against the code is how a product comes to promise something it
does not do. Every statement below was taken from what the code and the deploy
files actually do — headers.py's policy, server.py's logging, the photo
handler, assistant.context()'s fixed field set, bin/backup's retention — and
says where the answer depends on how somebody deployed it.

It describes THIS site: the deployment the public page is served from. A copy
somebody else runs is theirs to describe, and the page says so rather than
making promises on their behalf.

One tab rather than three because the nav already holds ten and SITE.md
records that ten barely fit one rail; the footer on every tab links to each
section directly, which is what a reader looking for "privacy" actually uses.
"""

# The anchors the footer links to. Named here, once, and read by render.py, so
# a renamed section cannot leave the footer pointing at nothing.
SAFETY_ID = "policies-safety"
SUPPORT_ID = "policies-support"
PRIVACY_ID = "policies-privacy"

SECURITY_URL = "https://github.com/jchirayath/poolhound/blob/main/SECURITY.md"
LICENSE_URL = "https://github.com/jchirayath/poolhound/blob/main/LICENSE"


def _link(href, text):
    return f'<a href="{href}" rel="noopener">{text}</a>'


def footer_links():
    """The three links the site-wide footer carries, on every tab of both builds."""
    return (f'<a href="#{SAFETY_ID}">Safety &amp; disclaimer</a> · '
            f'<a href="#{SUPPORT_ID}">No support</a> · '
            f'<a href="#{PRIVACY_ID}">Privacy</a>')


def panel(public=True):
    s = []
    a = s.append

    a('<p class="lede">What this software is and is not responsible for, the '
      'help you should not expect, and what this site records about you. Short, '
      'because each statement here is one the code was checked against.</p>')

    # -- safety and disclaimer ------------------------------------------------
    a(f'<section id="{SAFETY_ID}"><h2>Safety and disclaimer</h2>')
    a('<p class="sub">Pool chemicals and pool equipment can hurt people</p>')
    a('<h3>Chemicals</h3>')
    a('<ul>'
      '<li><b>Every dose here is an estimate, not a prescription.</b> It is a '
      'textbook relation applied to an estimated volume. Treat the first pour of '
      'anything as a probe, test, and let the measured response correct the '
      'number.</li>'
      '<li><b>The product label overrides this page.</b> Read it, and follow its '
      'directions, limits and first-aid instructions.</li>'
      '<li><b>Never mix chemicals</b> — above all, never let chlorine and acid '
      'meet, in a bucket, a feeder or the pool. Together they release chlorine '
      'gas. Add each one to the water separately, never water to the chemical, '
      'and let the pump run between them.</li>'
      '<li>Wear gloves and eye protection, store chemicals apart and out of reach '
      'of children, and test the water yourself before anyone swims. A reading on '
      'this site can be hours old or simply wrong.</li>'
      '</ul>')
    a('<h3>Remote control</h3>')
    a('<ul>'
      '<li><b>The controls switch real equipment</b> — pumps, heaters and the '
      'spa — from anywhere. Someone may be in or near the water. Know who is '
      'there before you switch anything from away.</li>'
      '<li><b>This is not a safety system.</b> The panel’s own interlocks, '
      'the on-site switches and the people at the pool are. Do not rely on this '
      'site for freeze protection, for keeping anyone safe, or for knowing the '
      'state of the equipment: a command can be delayed, refused or lost, and the '
      'page says “waiting” until the panel itself agrees.</li>'
      '<li>Nothing here installs or wires equipment. Electrical, gas and plumbing '
      'work belongs to the manufacturer’s instructions and a licensed '
      'professional.</li>'
      '</ul>')
    a('<h3>Readings, estimates and answers</h3>')
    a('<ul>'
      '<li>Readings come from sensors and labs that can be stale, latched or '
      'wrong; each one is shown with its source and age for that reason. Two labs '
      'that disagree are both shown, not averaged.</li>'
      '<li>The volume calculator gives an estimate with an uncertainty band, and '
      'every dose figure inherits it.</li>'
      '<li>The AI assistant is a language model. It is handed figures already '
      'computed, and it can still be wrong with complete confidence.</li>'
      '</ul>')
    a('<h3>No warranty, and no affiliation</h3>')
    a(f'<p>poolhound is free software under the {_link(LICENSE_URL, "MIT licence")}, '
      'provided “as is”, without warranty of any kind, express or '
      'implied. In no event are the authors liable for any claim, damages or '
      'other liability arising from the software or its use. You use it, and act '
      'on anything it says, at your own risk.</p>')
    a('<p>It is not affiliated with or endorsed by Jandy, Zodiac, AqualinkD, '
      'WaterGuru, Leslie’s or any AI provider. Their names are trademarks '
      'of their owners and appear only to say what this works with.</p>')
    a('</section>')

    # -- no support -----------------------------------------------------------
    a(f'<section id="{SUPPORT_ID}"><h2>No support</h2>')
    a('<p class="sub">A household’s project, published to be read</p>')
    a('<p>poolhound runs one household’s pool. The code is published so it '
      'can be read and reused, not offered as a product or a service. So:</p>')
    a('<ul>'
      '<li>There is no support, no help desk and no promise to answer questions, '
      'issues or pull requests.</li>'
      '<li>There are no releases, no compatibility promise and no hosted copy for '
      'anyone else. It can change or stop at any time.</li>'
      '<li>If you run your own copy, you are its operator — for its safety, its '
      'security and its users’ privacy.</li>'
      '</ul>')
    a(f'<p><b>The one exception is a security vulnerability.</b> Report it '
      f'privately as {_link(SECURITY_URL, "SECURITY.md")} describes — GitHub’s '
      '“Report a vulnerability”, not a public issue. Reports are '
      'acknowledged within three business days. The same channel is the place for '
      'a privacy concern about this site.</p>')
    a('</section>')

    # -- privacy --------------------------------------------------------------
    a(f'<section id="{PRIVACY_ID}"><h2>Privacy</h2>')
    a('<p class="sub">What this site keeps, what it sends, and to whom</p>')
    a('<h3>If you are visiting</h3>')
    a('<ul>'
      '<li><b>No cookies, no analytics, no advertising, no tracking.</b> Every '
      'script, style and image comes from this server; links to other sites are '
      'only links, and the page’s content security policy refuses anything '
      'else. Nothing about you is sold or shared.</li>'
      '<li>Your browser keeps your light/dark choice and the volume '
      'calculator’s water rate in its own local storage. They never leave '
      'it.</li>'
      '<li>The web server keeps an ordinary access log — your IP address, the '
      'address you asked for, the time and your browser’s identification — '
      'to run and protect the site. It is rotated by size, five files kept, and '
      'read by nobody but the operator.</li>'
      '<li><b>A photo you trace is not kept.</b> It is decoded on the server, '
      'sent back to your browser to draw on, and deleted when the request '
      'finishes. Nobody else sees it. The image sent back can still carry the '
      'camera’s own metadata, including location, but only to you. Volume '
      'calculations are not kept either.</li>'
      '<li>Rate limiting counts requests, not who made them.</li>'
      '</ul>')
    a('<h3>If you are signed in</h3>')
    a('<p>Sign-in is handled by the web server in front of poolhound. Your '
      'username or email is recorded against what you do — a dose logged, a '
      'setting changed, a command sent, a collection started — in an append-only '
      'audit log, visible only to people who are signed in. It is kept with the '
      'pool’s history, and in its nightly backups for thirty days.</p>')
    a('<h3>What goes to other services</h3>')
    a('<ul>'
      '<li><b>WaterGuru and Leslie’s</b>: this household’s own account '
      'credentials, to fetch its own test results.</li>'
      '<li><b>The AI provider</b>, only when a signed-in person asks a question: '
      'the pool’s volume, current readings with their targets, and recent '
      'doses. Never an address, a name, an identity or a credential. Questions '
      'and answers are not stored.</li>'
      '<li><b>Email alerts</b> to the household, through its mail relay.</li>'
      '<li><b>An uptime monitor</b>, pinged with counts only.</li>'
      '<li>The site and its data are hosted on Microsoft Azure. Credentials are '
      'read by the software and never written into a page, a log or a backup.</li>'
      '</ul>')
    a('<p>The pool’s controller is never exposed to the internet. A small agent '
      'in the house connects outward to this site and waits, so nothing here can '
      'open a connection to the pool.</p>')
    a('</section>')

    return "".join(s)
