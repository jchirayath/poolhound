#!/usr/bin/env python3
"""Inline SVG icons.

WHY THESE ARE DRAWN HERE RATHER THAN FETCHED
  The page loads nothing from anywhere — no CDN, no icon font, no network call
  at all — and the Content-Security-Policy that ships with the server enforces
  it. An icon set is the usual first crack in that: it is small, it is somebody
  else's URL, and it arrives without anyone deciding to add a third party to a
  tool that reads this household's data.

  So they are paths, in the source, about forty bytes each. They also inherit
  currentColor, which means one definition works in both themes and beside any
  text colour rather than needing a light and a dark copy.

WHY THEY ARE DECORATIVE
  Every icon here sits beside a label that already says the same thing, so all of
  them carry aria-hidden. An icon that is the only carrier of meaning is a
  failure for anyone using a screen reader, and a stroke of paint is a poor place
  to put information that has to survive being read aloud.
"""

# 24x24 viewBox, stroked, no fills — consistent weight at any size.
_PATHS = {
    "home":      "M3 11l9-8 9 8M5 9v11h14V9",
    "flask":     "M9 3v6L4 20h16L15 9V3M9 3h6M8 14h8",
    "book":      "M4 5a2 2 0 012-2h12v18H6a2 2 0 01-2-2z M8 7h8M8 11h6",
    "sliders":   "M4 7h10M18 7h2M4 17h4M12 17h8M16 5v4M8 15v4",
    "drop":      "M12 3c0 0-6 6.5-6 10a6 6 0 1012 0c0-3.5-6-10-6-10z",
    "gauge":     "M12 20a8 8 0 118-8M12 12l5-3",
    "chart":     "M4 19V5M4 19h16M8 16v-5M12 16V8M16 16v-3",
    "clock":     "M12 21a9 9 0 100-18 9 9 0 000 18zM12 7v5l3 2",
    "beaker":    "M6 3h12M9 3v7l-4 8a2 2 0 002 3h10a2 2 0 002-3l-4-8V3",
    "alert":     "M12 4l9 16H3zM12 10v4M12 17v.5",
    "check":     "M4 12l5 5L20 6",
    "sun":       "M12 8a4 4 0 100 8 4 4 0 000-8zM12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19",
    "refresh":   "M20 11a8 8 0 10-2 6M20 5v6h-6",
    "camera":    "M3 8h3l2-2h8l2 2h3v12H3zM12 17a4 4 0 100-8 4 4 0 000 8z",
    "shapes":    "M4 14h7v7H4zM14.5 3L21 14h-13z",
    "bell":      "M6 9a6 6 0 1112 0c0 5 2 6 2 6H4s2-1 2-6M10 20a2 2 0 004 0",
    "plug":      "M9 3v6M15 3v6M6 9h12v3a6 6 0 01-12 0zM12 18v3",
    "scale":     "M12 4v16M6 8h12M6 8l-3 6h6zM18 8l-3 6h6z",
    "thermo":    "M12 3a2 2 0 012 2v8a4 4 0 11-4 0V5a2 2 0 012-2zM12 9v5",
    "target":    "M12 21a9 9 0 100-18 9 9 0 000 18zM12 16a4 4 0 100-8 4 4 0 000 8zM12 13v-1",
    "list":      "M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01",
    "spark":     "M13 2L4 14h7l-1 8 9-12h-7z",
    # FOUR GLYPHS THAT DID NOT EXIST, which is why four sections had no honest
    # icon to be given. Forcing a near-match would have been worse than the
    # generic list glyph: an icon that means the wrong thing is read before the
    # label beside it is.
    "key":       "M14 7a4 4 0 11-3.4 6.1L4 20H2v-2l6.9-6.6A4 4 0 0114 7zM15.5 8.5h.01",
    "download":  "M12 4v10M8 11l4 4 4-4M4 19h16",
    "shield":    "M12 3l8 3v6c0 4.5-3.3 7.8-8 9-4.7-1.2-8-4.5-8-9V6z",
    "wrench":    "M15 3a5 5 0 00-4.6 7L3 17.4V21h3.6l7.4-7.4A5 5 0 1015 3z",
}

def icon(name, size=16, cls=""):
    d = _PATHS.get(name)
    if not d:
        return ""
    c = f' class="{cls}"' if cls else ""
    return (f'<svg{c} width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true"><path d="{d}"/></svg>')

# Which icon belongs to which section heading. Matched on a substring of the
# heading so a reworded title keeps its icon instead of silently losing it.
SECTION_ICONS = [
    ("what to do",            "spark"),
    ("where the water",       "drop"),
    ("trend",                 "chart"),
    ("lab history",           "beaker"),
    ("what changed",          "scale"),
    ("two instruments",       "scale"),
    ("what the pool did",     "clock"),
    ("model readiness",       "target"),
    ("log a dose",            "beaker"),
    ("what has been added",   "list"),
    ("what upkeep takes",     "scale"),
    ("what your doses",       "target"),
    ("what the salt cell",    "plug"),
    ("where the advice",      "alert"),
    ("interaction map",       "shapes"),
    ("dose reference",        "scale"),
    ("how to use this",       "book"),
    ("the pool",              "drop"),
    ("notifications",         "bell"),
    ("how this pool is judged", "target"),
    ("where the data",        "plug"),
    # "Collection schedule" and "Where the data comes from" were two Settings
    # sections describing the same three sources from two angles; the cadence
    # is a setting now and the status is on the Collection tab, so what is left
    # here is the one control. The two keys above and below stay: the matcher
    # is a substring scan, an entry for a heading that no longer exists costs
    # nothing, and the inventory compares in both directions anyway.
    ("collection schedule",   "clock"),
    ("how often the labs",    "clock"),

    # THE HALF THAT HAD NO MAPPING AT ALL. Twenty-three headings on the
    # authenticated page and fourteen on the public one matched nothing and
    # took the generic glyph, which is indistinguishable from a heading
    # deliberately given it — so more than half the page was unmapped and
    # nothing said so.
    #
    # Ordered after the keys above because matching is first-substring-wins and
    # these are the broader phrases; a key added here must not shadow one there.
    ("running right now",     "gauge"),
    ("power and runtime",     "plug"),
    ("the spa",               "thermo"),
    ("pool and spa together", "drop"),
    ("why not length",        "shapes"),
    ("circuits",              "plug"),
    ("setpoints",             "sliders"),
    ("everything off",        "alert"),
    ("what has been switched", "clock"),
    ("logins",                "key"),
    ("a reading that is wrong", "alert"),
    ("what was done",         "clock"),
    ("record a test result",  "flask"),
    ("your data",             "download"),
    ("what this is for",      "book"),
    ("what each tab does",    "book"),
    ("is this safe",          "shield"),
    ("the parts, and how",    "shapes"),
    ("what you need to buy",  "list"),
    ("wiring the panel",      "wrench"),
    ("usb adapter",           "plug"),
    ("aqualinkd settings",    "sliders"),
    ("built on other",        "book"),
    ("rest of the documentation", "book"),

    # The Collection tab.
    ("where collection stands", "gauge"),
    ("alerts, and whether",     "bell"),
    ("what has been tried",     "clock"),

    # The Ask tab.
    ("ask about this pool",     "spark"),
    ("what it is told",         "book"),

    # Poolhound info — the public landing page.
    ("three pieces",            "shapes"),
    ("what poolhound is",       "drop"),
    ("what it reads",           "gauge"),
    ("what it looks like",      "camera"),
    ("what you can use here",   "spark"),
    ("how it is put together",  "shapes"),

    # Safety & privacy.
    ("safety and disclaimer",   "alert"),
    ("no support",              "wrench"),
    ("privacy",                 "shield"),
]

def icon_for(title):
    t = (title or "").lower()
    for key, name in SECTION_ICONS:
        if key in t:
            return name
    return "list"
