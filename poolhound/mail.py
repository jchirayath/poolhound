"""What an alert email looks like. One owner, two renderings of one input.

WHY THIS IS A MODULE AND NOT A FORMAT STRING IN watch.py

The body used to be built where it was sent:

    body = "\\n".join(x for x in (subtitle, "", message, "",
                                  f"Dashboard: {url}" if url else "",
                                  f"Sent by poolhound on {os.uname().nodename}.")
                      if x is not None)

Three things were wrong with that and only one of them was cosmetic. The filter
tests `is not None` against values that are `""` when absent, so it never
removed anything and an alert with no subtitle and no dashboard link opened with
two blank lines and carried a gap where the link would have been. The hostname
is `os.uname().nodename`, which on a workstation is somebody's laptop and inside
the container is a random hex container id — a different meaningless string
every deploy. And prose assembled at the call site cannot be rendered twice, so
adding an HTML part would have meant a second copy of the same paragraph.

So the input is STRUCTURED — a headline, a detail, an ordered list of facts, a
link — and `text()` and `html()` are two renderings of it. Neither can carry a
fact the other does not, which is the property that matters: an email read in a
client that refuses HTML must not be missing the reading that the alert is
about.

WHY THE PALETTE IS PARSED RATHER THAN TYPED

`style.CSS` is the single source for colour and `bin/contrast` measures it. An
email cannot use the custom properties it defines — Outlook renders through Word
and has no `var()`, and the parts of Gmail that strip `<style>` leave nothing to
define them in — so every colour here has to be a literal in an inline `style=`
attribute. Typing those literals would be a second palette, drifting silently,
which is the defect `seams.py` exists to catch. They are read out of
`style.CSS` instead: one source, inlined at send time.

WHAT AN EMAIL CLIENT WILL ACTUALLY RENDER

Not a web page. This is deliberately 2005-shaped HTML because the alternative
does not arrive:

  * tables for layout, not flex or grid — Outlook on Windows renders through
    Word, which has neither;
  * every style inlined on the element — Gmail on a non-Gmail account strips
    `<style>` blocks entirely;
  * the logo is a PNG attached by Content-ID, not `<svg>` and not a `data:`
    URI. Gmail strips inline SVG and blocks `data:` images, so both of those
    render as nothing at all. `cid:` is the only form that survives, and it is
    also the only one that does not phone home to fetch a picture;
  * `text/plain` first in the multipart, which is what a client picks when it
    prefers plain — and what a screen reader on a text-only client reads.

NEVER COLOUR ALONE, HERE TOO. The severity chip carries its word as text, for
the same reason the equipment cards do: a mail client in forced dark mode, a
monochrome display and a colour-blind reader all get the severity from the
label rather than from the fill behind it.
"""

import os
import re

from . import brand
from . import style

# --------------------------------------------------------------- the palette

def _light_tokens():
    """The `--name:value` pairs from the FIRST `:root` block of style.CSS.

    The first block is the light theme, which is the one an email wants: a mail
    client applies its own dark treatment and cannot be handed two palettes to
    choose between. Parsed rather than copied — see the module docstring.
    """
    # COMMENTS COME OUT FIRST, and that ordering is the whole correctness of
    # this function. Two ways it was wrong when it stripped them afterwards:
    # the stylesheet's opening comment is the sentence "Every colour is a token
    # defined on bare :root", so a search for the selector found PROSE 2
    # characters in; and the comments inside the block discuss colours that
    # were REJECTED for contrast (`--ink3 was #667180 ... under the 4.5 floor`),
    # so a hex read out of one ships a value the stylesheet deliberately does
    # not use. It found nothing at all, tok() fell back to #000000 for every
    # name, and the severity chip rendered as a black pill with black text on
    # it -- invisible, and only visible as a defect in a browser.
    css = re.sub(r"/\*.*?\*/", " ", style.CSS, flags=re.S)
    # `:root {` with a space, and BARE: the dark blocks are
    # `:root:not([data-theme="light"])` and `:root[data-theme="dark"]`, and
    # matching either would hand an email the dark palette.
    m = re.search(r"(?m)^\s*:root\s*\{", css)
    if not m:
        return {}
    block = css[m.end():css.find("}", m.end())]
    return {t.group(1): t.group(2).strip()
            for t in re.finditer(r"--([a-z0-9-]+)\s*:\s*([^;]+);", block)}

TOKENS = _light_tokens()

# The colours this template asks style.CSS for. Declared, so the ask can be
# compared against what the stylesheet actually defines -- renaming a token
# should break a check here rather than silently paint something black.
NEEDED = ("ink", "ink2", "ink3", "bg", "panel", "line", "accent",
          "btnink", "good", "goodbg", "warn", "warnbg", "bad", "badbg")

def tok(name):
    """A colour, from the stylesheet. NO PER-CALL FALLBACK, on purpose.

    These were `tok("ink", "#16202b")`, and every fallback was a second copy of
    the value it was standing in for -- the exact drift seams.py exists to
    catch, written in the one place guaranteed not to be read while things
    work. They also hid the parse bug: with the block never found, every
    fallback fired and the page rendered in plausible-looking colours except
    the severity chip, which came out a black pill with black text on it.
    Missing is now loud.
    """
    return TOKENS[name]

# Severity: the fill, the ink on it, and THE WORD. The word is not decoration —
# it is the part that survives a client that drops background colours.
SEVERITY = {
    "critical": ("Critical", "bad",  "badbg"),
    "serious":  ("Serious",  "warn", "warnbg"),
    "info":     ("For information", "good", "goodbg"),
}

LOGO_CID = "poolhound-mark"
# DECLARED IN config.RUNTIME_ASSETS, not spelled here. It is a file the product
# reads while running, and the image did not carry it for weeks -- every alert
# email sent successfully, with no logo, because send() degrades rather than
# fails. The declaration is what the image build now checks.
LOGO_FILE = "docs/poolhound-mark-email.png"

def logo_path(root=None):
    from . import config
    return (os.path.join(root, *LOGO_FILE.split("/")) if root
            else config.runtime_asset(LOGO_FILE))

# ------------------------------------------------------------------ the input

class Alert:
    """Everything an alert says, before it is any particular medium.

    `facts` is an ordered list of (label, value). It exists so the readings
    travel as data rather than as a sentence: the chemistry alarm used to embed
    "Now: FC 0.4 ppm, pH 7.9" into its message string, which reads acceptably in
    plain text and cannot be laid out as anything else.
    """
    def __init__(self, subject, headline="", detail="", facts=(), url=None,
                 severity="info", urgent=False, site=None):
        self.subject = subject
        self.headline = headline or subject
        self.detail = detail or ""
        self.facts = [(str(k), str(v)) for k, v in (facts or ())]
        self.url = url
        self.severity = severity if severity in SEVERITY else "info"
        self.urgent = urgent
        self.site = site

    @property
    def label(self):
        return SEVERITY[self.severity][0]

# ----------------------------------------------------------------- rendering

def text(a):
    """The plain-text part. First in the multipart and never an afterthought.

    Built by joining only the blocks that exist, so an alert with no detail, no
    facts and no link is three lines rather than three lines and four blank
    ones.
    """
    blocks = [f"{a.label.upper()}: {a.headline}"]
    if a.detail:
        blocks.append(a.detail.strip())
    if a.facts:
        w = max(len(k) for k, _ in a.facts)
        blocks.append("\n".join(f"  {k.ljust(w)}   {v}" for k, v in a.facts))
    if a.url:
        blocks.append(f"Dashboard: {a.url}")
    blocks.append("You are receiving this because poolhound is configured to "
                  "email alerts.\nChange that on the Settings tab.")
    return "\n\n".join(blocks) + "\n"

def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))

_FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,"
         "Arial,sans-serif")

def html(a):
    """The HTML part: one centred card, tables all the way down."""
    ink, ink2, ink3 = tok("ink"), tok("ink2"), tok("ink3")
    bg, panel, line = tok("bg"), tok("panel"), tok("line")
    accent, btnink = tok("accent"), tok("btnink")
    _, sev_ink, sev_bg = SEVERITY[a.severity]
    sev_ink, sev_bg = tok(sev_ink), tok(sev_bg)

    facts = ""
    if a.facts:
        rows = "".join(
            f'<tr>'
            f'<td style="padding:7px 14px 7px 0;font:400 13px/1.4 {_FONT};'
            f'color:{ink3};white-space:nowrap;vertical-align:top">{_esc(k)}</td>'
            f'<td style="padding:7px 0;font:600 14px/1.4 {_FONT};color:{ink}">'
            f'{_esc(v)}</td></tr>'
            for k, v in a.facts)
        facts = (f'<table role="presentation" cellpadding="0" cellspacing="0" '
                 f'border="0" style="width:100%;margin:0 0 4px;border-top:1px solid '
                 f'{line};border-bottom:1px solid {line}">{rows}</table>')

    detail = ""
    if a.detail:
        # The detail is prose the caller wrote with blank lines between
        # paragraphs. Split on those rather than dropping <br> into it, so a
        # paragraph keeps its own spacing and a client that reflows can.
        paras = [p.strip() for p in re.split(r"\n\s*\n", a.detail.strip()) if p.strip()]
        detail = "".join(
            f'<p style="margin:0 0 12px;font:400 15px/1.55 {_FONT};color:{ink2}">'
            f'{_esc(p)}</p>' for p in paras)

    button = ""
    if a.url:
        button = (
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin:18px 0 4px"><tr><td style="background:{accent};'
            f'border-radius:7px"><a href="{_esc(a.url)}" '
            f'style="display:inline-block;padding:11px 20px;font:600 14px/1 {_FONT};'
            f'color:{btnink};text-decoration:none">Open the dashboard</a></td>'
            f'</tr></table>')

    site = _esc(a.site) if a.site else "poolhound"
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<meta name="supported-color-schemes" content="light only">
<title>{_esc(a.subject)}</title></head>
<body style="margin:0;padding:0;background:{bg};-webkit-text-size-adjust:100%">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{_esc(a.headline)}</div>
<table role="presentation" cellpadding="0" cellspacing="0" border="0"
       style="width:100%;background:{bg}">
 <tr><td align="center" style="padding:24px 12px">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0"
         style="width:100%;max-width:560px;background:{panel};
                border:1px solid {line};border-radius:12px">
   <tr><td style="padding:20px 24px 0">
     <table role="presentation" cellpadding="0" cellspacing="0" border="0">
      <tr>
       <td style="padding-right:10px" valign="middle">
         <img src="cid:{LOGO_CID}" width="28" height="28" alt=""
              style="display:block;width:28px;height:28px;border:0"></td>
       <td valign="middle" style="font:700 17px/1 {_FONT};color:{ink};
                                  letter-spacing:-.01em">poolhound</td>
      </tr>
     </table>
   </td></tr>
   <tr><td style="padding:16px 24px 0">
     <span style="display:inline-block;padding:4px 10px;border-radius:999px;
                  background:{sev_bg};color:{sev_ink};
                  font:700 11px/1.3 {_FONT};text-transform:uppercase;
                  letter-spacing:.06em">{_esc(a.label)}</span>
   </td></tr>
   <tr><td style="padding:12px 24px 0">
     <h1 style="margin:0 0 14px;font:700 21px/1.3 {_FONT};color:{ink}">
       {_esc(a.headline)}</h1>
     {detail}{facts}{button}
   </td></tr>
   <tr><td style="padding:18px 24px 22px">
     <p style="margin:14px 0 0;padding-top:14px;border-top:1px solid {line};
               font:400 12px/1.5 {_FONT};color:{ink3}">
       Sent by poolhound for {site}. You are receiving this because poolhound
       is configured to email alerts &mdash; change that on the Settings tab.</p>
   </td></tr>
  </table>
 </td></tr>
</table>
</body></html>"""

# ------------------------------------------------------------- the message

def build(a, sender, to, subject_prefix="[poolhound]"):
    """An EmailMessage carrying both renderings and the logo.

    HEADERS THAT WERE MISSING, and why each one is not decoration:

    `Date` and `Message-ID` were absent because `EmailMessage` does not add
    them and `smtplib.send_message` does not either. A relay usually fills them
    in; one on the LAN may not, and a message arriving with neither is scored
    as spam by every filter that looks.

    `Auto-Submitted: auto-generated` is RFC 3834, and it is what stops an
    out-of-office responder replying to an alert — and then to the reply.

    The subject used to read
    `("[poolhound] " if not urgent else "[poolhound] ") + subject`: a ternary
    whose branches are the same string, so the urgent marking somebody intended
    has never once been applied.
    """
    from email.message import EmailMessage
    from email.utils import formatdate, make_msgid

    msg = EmailMessage()
    mark = "⚠ " if a.severity == "critical" else ""
    msg["Subject"] = f"{subject_prefix} {mark}{a.subject}".strip()
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg["Date"] = formatdate(localtime=True)
    domain = sender.rpartition("@")[2] or "poolhound.invalid"
    msg["Message-ID"] = make_msgid(domain=domain)
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Auto-Response-Suppress"] = "All"
    if a.urgent:
        msg["X-Priority"] = "1"
        msg["Importance"] = "High"

    msg.set_content(text(a))
    msg.add_alternative(html(a), subtype="html")

    # The logo rides on the HTML part, not on the message: attached to the
    # message it shows up as a downloadable file in clients that list
    # attachments, on an alert that has none.
    png = logo_path()
    try:
        with open(png, "rb") as f:
            data = f.read()
    except OSError:
        return msg          # no logo committed: send the rest rather than fail
    html_part = msg.get_payload()[-1]
    html_part.add_related(data, "image", "png", cid=f"<{LOGO_CID}>",
                          filename="poolhound.png")
    return msg


def test_alert(cfg, site=None):
    """The "can poolhound reach you" message, in ONE place.

    It was in two: `bin/watch --test-email` and the Settings tab's button each
    carried their own copy, and they had already drifted in the way that
    matters. server.py's copy carries a comment explaining that
    `Sending as: the SMTP username` was wrong -- the envelope said something
    else, on the one message whose entire job is to show what a real alert will
    look like -- and fixes it with `resolved_sender()`. watch.py's copy still
    said `n.get('email_from') or 'the SMTP username'`. The fix reached the copy
    somebody was looking at and not the other one, which is the whole argument
    for seams.py.
    """
    from . import watch
    n = cfg.get("notify", {})
    sender = watch.resolved_sender(cfg)
    return Alert(
        subject="Test message",
        headline="poolhound can reach you",
        detail="Alarms about chlorine, pH, the collectors and the heaters will "
               "arrive the same way as this one.",
        facts=[("Sending as", sender or "not set — a real send will be refused"),
               ("Relay", f"{n.get('smtp_host', 'smtp.gmail.com')}:"
                         f"{n.get('smtp_port', 587)}"),
               ("Encryption", "STARTTLS" if n.get("smtp_tls", True) else "off")],
        url=watch.site_url(cfg, "#home"),
        severity="info", site=site)
