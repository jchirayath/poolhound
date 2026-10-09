"""The alert email: that it carries the facts, and that it owns no palette.

Every case here is a defect this feature shipped with or was one edit away
from. The email was plain text assembled at the call site, and adding a second
rendering is exactly the moment those become visible:

  * the body joined its parts with a filter testing `is not None` against
    values that are `""` when absent, so an alert with no subtitle and no link
    opened with two blank lines and carried a gap where the link was not;
  * it signed off `Sent by poolhound on {os.uname().nodename}` -- somebody's
    laptop on a workstation, a fresh random container id on the server;
  * the subject was `("[poolhound] " if not urgent else "[poolhound] ")`, a
    ternary whose branches are the same string, so the urgent marking somebody
    intended has never been applied;
  * `Date`, `Message-ID` and `Auto-Submitted` were all absent, because
    EmailMessage does not add them and send_message does not either;
  * the "can poolhound reach you" message existed TWICE, and the two copies
    had already drifted over how they describe the sender.

Discovered by selftest.cases() because the file is named checks_*.py.
"""
import os
import re

from .selftest import check, skipped


def _alert(**kw):
    from . import mail
    base = dict(subject="Free chlorine is low", headline="Free chlorine is low",
                detail="Free chlorine 0.4 ppm is below the 1.9 floor.",
                facts=[("Free chlorine", "0.4 ppm"), ("pH", "7.9")],
                url="https://poolhound.example.com/#home", severity="critical")
    base.update(kw)
    return mail.Alert(**base)


def t_both_renderings_carry_every_fact():
    """The plain part is not an afterthought, and a check has to say so.

    This is the property that makes two renderings safe: a reader whose client
    refuses HTML must not be missing the reading the alert is ABOUT. Nothing
    structural prevents someone adding a row to the HTML table and not to
    text(), and the failure would be invisible to anyone reading in HTML --
    which is everyone who tests it.
    """
    from . import mail
    print("\n  mail — the text part carries what the HTML part does")
    a = _alert()
    t, h = mail.text(a), mail.html(a)
    for k, v in a.facts:
        check(f"text carries {k!r}", k in t and v in t, True)
        check(f"html carries {k!r}", k in h and v in h, True)
    check("text carries the headline", a.headline in t, True)
    check("text carries the link", a.url in t, True)
    check("text names the severity in words", "CRITICAL" in t, True)
    # The word, not only the colour -- the same rule the equipment cards keep.
    check("html names the severity in words", "Critical" in h, True)


def t_absent_parts_leave_no_hole():
    """An alert with no detail, no facts and no link is not four blank lines."""
    from . import mail
    print("\n  mail — nothing optional leaves a gap behind it")
    t = mail.text(mail.Alert(subject="Back within safe limits", severity="info"))
    check("no blank line opens the body", t.startswith("FOR INFORMATION:"), True)
    check("no run of three newlines anywhere", "\n\n\n" in t, False)
    check("and no dangling Dashboard label", "Dashboard:" in t, False)
    # The failure this replaces: `if x is not None` never filtered "".
    joined = "\n".join(x for x in ("", "", "m", "", "", "f") if x is not None)
    check("the old filter would have kept every empty string",
          joined.count("\n\n"), 2)


def t_the_email_owns_no_palette():
    """Every colour comes from style.CSS. No literal may live here.

    `tok()` used to take a per-call fallback -- `tok("ink", "#16202b")` -- and
    each one was a second copy of the value it stood in for, in the one place
    guaranteed not to be read while things work. They also hid a parse bug:
    the selector search matched the stylesheet's opening COMMENT, every
    fallback fired, and the severity chip rendered as a black pill with black
    text on it.

    Comments are blanked before looking, because this project writes its
    comments as the history of what went wrong and they legitimately quote the
    colours that were rejected.
    """
    import inspect
    from . import mail, seams
    print("\n  mail — the palette is style.CSS's, and there is no second copy")
    # inspect.getsource, not open(): checks_structure breaks source-reading
    # detectors by patching it, and a detector it cannot break is a detector
    # nothing has shown to fire.
    code = seams._code_only(inspect.getsource(mail))
    check("no hex colour literal in mail.py's code",
          sorted(set(re.findall(r"#[0-9a-fA-F]{3,8}\b", code))), [])
    missing = [n for n in mail.NEEDED if n not in mail.TOKENS]
    check(f"style.CSS defines every token the email asks for "
          f"({len(mail.NEEDED)} asked, {len(mail.TOKENS)} defined)", missing, [])
    # The parse must find the LIGHT block, not one of the dark ones.
    check("the palette parsed is the light one", mail.TOKENS.get("panel"), "#ffffff")
    check("and not the dark one", mail.TOKENS.get("bg") == "#0f151c", False)


def t_headers_a_relay_will_not_add_for_us():
    """Date, Message-ID and Auto-Submitted, and the subject that was dead code."""
    from . import mail
    print("\n  mail — the headers automated mail has to carry")
    msg = mail.build(_alert(urgent=True), "poolhound@pool.example.com",
                     ["someone@example.com"])
    for h in ("Date", "Message-ID", "Auto-Submitted"):
        check(f"{h} is set", bool(msg.get(h)), True)
    check("Auto-Submitted says auto-generated", msg.get("Auto-Submitted"),
          "auto-generated")
    check("Message-ID uses the sender's domain",
          msg.get("Message-ID").endswith("@pool.example.com>"), True)
    check("an urgent alert is marked urgent", msg.get("Importance"), "High")
    # The ternary whose branches were identical.
    check("a critical subject is distinguishable from an ordinary one",
          msg["Subject"] != mail.build(_alert(severity="info"),
                                       "p@e.example.com", ["a@b.example.com"])["Subject"],
          True)
    check("and still starts with the tag people filter on",
          msg["Subject"].startswith("[poolhound] "), True)


def t_the_multipart_is_the_shape_clients_expect():
    """text/plain FIRST, html second, and the logo related to the html."""
    from . import mail
    print("\n  mail — multipart/alternative, plain first, logo by cid")
    msg = mail.build(_alert(), "poolhound@pool.example.com", ["a@b.example.com"])
    types = [p.get_content_type() for p in msg.walk()]
    check("the message is multipart/alternative", types[0], "multipart/alternative")
    check("plain comes before html", types.index("text/plain") <
          types.index("text/html"), True)
    if "image/png" not in types:
        skipped("the logo rides on the html part",
                "docs/poolhound-mark-email.png is not in this tree")
        return
    check("the logo is related to the html, not attached to the message",
          "multipart/related" in types, True)
    html = [p for p in msg.walk() if p.get_content_type() == "text/html"][0]
    check("and the html references it by cid",
          f"cid:{mail.LOGO_CID}" in html.get_content(), True)
    # A `data:` URI or an <svg> would render as nothing in Gmail.
    check("no data: URI image", "src=\"data:" in html.get_content(), False)
    check("no inline svg", "<svg" in html.get_content(), False)


def t_the_logo_is_a_real_png_of_the_right_size():
    from . import brand
    from . import config
    print("\n  mail — the committed logo")
    path = os.path.join(config.root(), "docs", "poolhound-mark-email.png")
    if not os.path.exists(path):
        skipped("the email logo is a valid PNG", "docs/ is not in this tree")
        return
    with open(path, "rb") as f:
        data = f.read()
    dims = brand.png_dimensions(data)
    check("it is a PNG", dims is not None, True)
    want = brand.RASTER_PX * 2          # rendered at 2x so 28px is crisp
    check(f"it is {want}x{want} (2x of RASTER_PX)", dims, (want, want))
    check("and small enough to attach to every alert", len(data) < 60000, True)


def t_html_escapes_what_it_interpolates():
    """A reading is vendor text and the subject is ours; both get escaped."""
    from . import mail
    print("\n  mail — nothing interpolated reaches the markup raw")
    a = _alert(headline='pH <script>alert("x")</script> high',
               detail="a & b", facts=[("Pod status", "<b>RED</b>")])
    h = mail.html(a)
    check("the script tag is escaped", "<script>" in h, False)
    check("and shows as text", "&lt;script&gt;" in h, True)
    check("the ampersand is escaped", "a &amp; b" in h, True)
    check("vendor markup in a reading is escaped", "&lt;b&gt;RED&lt;/b&gt;" in h, True)


def t_one_copy_of_the_test_message():
    """Two copies had already drifted over how they name the sender."""
    import importlib, inspect
    print("\n  mail — the test message has one owner")
    for name in ("watch", "server"):
        src = inspect.getsource(importlib.import_module(f"poolhound.{name}"))
        check(f"{name}.py does not spell out a test body",
              "This is poolhound checking that it can reach you" in src, False)
        check(f"{name}.py calls mail.test_alert", "mail.test_alert" in src, True)
    from . import mail
    a = mail.test_alert({"notify": {"smtp_host": "smtp.example.com",
                                    "smtp_port": 25, "smtp_tls": False}})
    labels = [k for k, _ in a.facts]
    check("it states the resolved sender", "Sending as" in labels, True)
    check("it states the relay", "Relay" in labels, True)
    # The drift itself: one copy described the sender as "the SMTP username".
    check("and never describes it as 'the SMTP username'",
          "the SMTP username" in mail.text(a), False)


def t_every_runtime_asset_is_actually_present():
    """A file the product READS is a fault wherever it is missing.

    MEASURED: every alert email since the move to the server went out with no
    logo. mail.send() reads docs/poolhound-mark-email.png and, not finding it,
    sends the rest rather than failing -- correct behaviour, and exactly why
    nothing ever said so. The Dockerfile copied docs/screenshots/cards/ and not
    that file, so it was never in the image.

    AND A CHECK DID SAY SOMETHING, in the one wording that reads as harmless.
    The multipart case above calls skipped(..., "docs/poolhound-mark-email.png
    is not in this tree"), which is right for a stripped checkout and means
    "this product cannot send a logo" in the image build. One sentence, two
    facts, and the louder one invisible.

    SO THIS DOES NOT SKIP. It is the image build's own gate -- `python -m
    poolhound.selftest` in the Dockerfile -- and a missing runtime asset must
    fail it rather than be reported as an absent document.
    """
    from . import config
    print("\n  assets — every file the product reads at run time is here")
    missing = config.missing_runtime_assets()
    check(f"no declared runtime asset is missing ({len(missing)} absent)",
          missing, [])
    check("and some are actually declared",
          len(config.RUNTIME_ASSETS) >= 4, True)
    # EACH ONE NAMES ITS CONSEQUENCE, so a failure above says what breaks
    # rather than only which file is gone.
    check("every asset says what reads it",
          [r for r, why in config.RUNTIME_ASSETS.items() if not why], [])


def t_the_image_carries_every_runtime_asset():
    """What the code reads at run time, against what the Dockerfile copies.

    The other direction, and the one that catches the mistake at the moment it
    is made rather than after an image is built: a new asset added to
    RUNTIME_ASSETS and not to the Dockerfile is a product that works on a
    workstation and is quietly broken in production, which is this defect
    exactly.
    """
    import os
    import re
    from . import config
    print("\n  assets — the image carries what the code reads")

    dockerfile = os.path.join(config.root(), "deploy", "Dockerfile")
    if not os.path.exists(dockerfile):
        # NOT A FAILURE AND NOT A PASS. deploy/ is not in the image, so this
        # comparison genuinely cannot be made there -- unlike the one above,
        # which can and must.
        skipped("runtime assets against the Dockerfile",
                "deploy/Dockerfile is not in this tree")
        return
    with open(dockerfile, encoding="utf-8") as f:
        copied = [m.group(1).rstrip("/") for m in
                  re.finditer(r"^COPY\s+(\S+)\s", f.read(), re.M)]
    check("the Dockerfile was parsed", len(copied) >= 4, True)
    uncopied = sorted(
        rel for rel in config.RUNTIME_ASSETS
        # A directory copied wholesale covers the paths beneath it.
        if not any(rel == c or rel.startswith(c + "/") or c.startswith(rel + "/")
                   for c in copied))
    check(f"every runtime asset is COPYed into the image ({len(uncopied)} not)",
          uncopied, [])
