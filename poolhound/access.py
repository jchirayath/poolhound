"""Who may do what, once the proxy has said who they are.

WHAT THIS IS FOR

poolhound had authentication and no authorization. Every write route asked only
"is there an identity?" — so anyone the proxy admitted got the whole product,
including the heaters and the credential store. That is fine when the proxy
admits exactly the household. It stops being fine the moment it does not: a
shared oauth2-proxy in front of several apps, an Entra group that grows, a guest
added for one afternoon. The proxy decides who gets in; nothing decided what
they could do once inside.

THE DEFAULT IS TODAY'S BEHAVIOUR, DELIBERATELY

An install that says nothing about access keeps working exactly as it did:
everybody who gets past the proxy is an admin. Shipping a default-deny would
lock owners out of their own pool on upgrade, which is a worse failure than the
one being fixed and would be discovered at the worst moment.

But "no policy" is a state, not a silence — `configured()` reports it and
Settings says so on the page, the same way an unset pool volume is reported
rather than defaulted. Once a policy IS set, an unlisted person drops to the
lowest level rather than inheriting the old default, because at that point
somebody has thought about it and an omission is more likely to be an omission
than an invitation.

THREE LEVELS, AND WHY NOT MORE

  view     read the private tabs; change nothing
  operate  switch equipment, log doses, record readings, fix a bad reading
  admin    everything, plus settings, credentials and the export

The split that matters is between "can act on the pool" and "can change what the
product knows and how it reaches the outside". A guest may be trusted to turn
the spa on and not to edit the SMTP password or download the household's whole
history. Finer grain than that would be configuration nobody will get right.

THIS IS NOT THE BOUNDARY BY ITSELF

The page is one file for all authenticated users — it cannot be rendered per
person, because a render serves everybody. So the page ASKS its role from
/api/health and dims what the reader cannot use, and the server refuses anyway.
The refusal is the boundary; the dimming is courtesy. Same rule as the two
builds: client-side hiding is not a boundary.
"""

# THE TWO ANSWERS THAT ARE NOT A PERSON, named once.
#
# "local" is a loopback console — being on the machine is the authentication.
# "anonymous" is a request from off-box that carried no identity. Five call
# sites once spelled the first of these as a literal and compared against it
# directly; adding the second caused an outage and a 90-minute token leak,
# because a literal cannot be extended. server.signed_in() is the predicate;
# these are the values it is built from, and the writers that stamp a row with
# "who did this" need the same spelling.
CONSOLE = "local"
ANONYMOUS = "anonymous"
NOT_A_PERSON = (CONSOLE, ANONYMOUS)

LEVELS = ("view", "operate", "admin")
_RANK = {name: i for i, name in enumerate(LEVELS)}

# What each write route costs. A route absent from here needs `admin`, so a new
# endpoint is locked down until somebody says otherwise rather than open until
# somebody remembers — the direction that fails safe.
NEEDS = {
    "/api/control":          "operate",
    "/api/chemical":         "operate",
    "/api/chemical/edit":    "operate",
    "/api/chemical/delete":  "operate",
    "/api/reading":          "operate",
    "/api/lab-correction":   "operate",
    "/api/pool-shape":       "operate",
    "/api/refresh":          "operate",
    # Asking the assistant spends somebody's money at a provider and sends this
    # pool's readings out of the house. `view` is the floor for reading the
    # private tabs; this does more than read, so it sits with the others that
    # act.
    "/api/ask":              "operate",

    # Read routes that are not the whole history. The command log names who
    # started the spa heater, which anybody who can see the control tab can
    # already watch happen.
    "/api/commands":         "view",
    # A date-bounded slice of one CSV. Same data the export hands out whole, so
    # the same floor: anybody who may read the private tabs may page through
    # them.
    "/api/rows":             "view",

    "/api/settings":         "admin",
    "/api/credential":       "admin",
    "/api/credential/import": "admin",
    "/api/notify-test":      "admin",
    "/api/export":           "admin",
}


# WHICH CONTROLS CALL WHICH ROUTE.
#
# The page dims what this reader cannot use. It did that from two CSS selector
# strings hand-written inside the page script, and a hand-written list of
# "every control that writes" is wrong the moment somebody adds one — exactly
# the mistake the public build's leak list already learned, and fixed by
# deriving itself from NEEDS. This is the same fix for the same shape of bug.
#
# Three routes were missing, and each was missing in the direction that reads
# as a broken product rather than as a permission:
#
#   /api/refresh         the Refresh control spends WaterGuru API calls. A
#                        `view` reader could press it and got an unexplained
#                        403 from a button that looked live.
#   /api/lab-correction   "this reading is wrong" is a change to what every
#                        chart and every target is computed from.
#   /api/pool-shape      the calculator's two Save buttons, which set the
#                        volume every dose in the product is figured against.
#
# Keyed by the route so the map and NEEDS are checked against each other:
# render.py raises if a gated route the page script fetches has no entry here.
# A route with genuinely no control gets an empty string, which is a decision
# rather than an omission.
ROLE_UI = {
    "/api/control":          "#tab-control button, #tab-control input, #tab-control select",
    "/api/chemical":         "#chem-form button, #chem-form input, #chem-form select",
    "/api/chemical/edit":    "#chem-form button, #chem-form input, #chem-form select",
    "/api/chemical/delete":  "[data-del]",
    "/api/reading":          "#reading-form button, #reading-form input, #reading-form select",
    "/api/lab-correction":   "#set-corrections button, #set-corrections input, #set-corrections select",
    "/api/pool-shape":       "#shape-save, #design-save",
    "/api/refresh":          ".refresh-wrap button",
    "/api/ask":              "#ask-form button, #ask-form input, #ask-form textarea",
    "/api/settings":         "#settings-form button, #settings-form input, #settings-form select",
    "/api/notify-test":      "#notify-form button, #notify-form input, #notify-form select",
    "/api/credential":       ".cred button, .cred input, .cred select",
    "/api/credential/import": ".cred button, .cred input, .cred select",
    # Read routes. The page reads them to DRAW itself, so there is no control to
    # dim -- a `view` reader is meant to see the command log and page the tables.
    "/api/commands":         "",
    "/api/rows":             "",
    # The download links are anchors, and an anchor has no .disabled -- setting
    # it is a silent no-op, which is how a view-only reader kept a working
    # "download the whole history" link. They are hidden instead, by the page
    # script, and selected by class rather than by href: naming the route in the
    # shared page script puts its literal path into the PUBLIC build, which the
    # leak assertion refuses. Hence the empty string and this paragraph.
    "/api/export":           "",
}


def ui_selector(level, needs=None, ui=None):
    """The controls that call a route needing exactly `level`.

    Returned as one CSS selector list, deduplicated: two routes can share a
    form (#chem-form serves /api/chemical and its /edit sibling) and a selector
    repeated in a querySelectorAll list is harmless but reads as a mistake.
    """
    needs = NEEDS if needs is None else needs
    ui = ROLE_UI if ui is None else ui
    out = []
    for route in sorted(needs):
        if needs[route] != level:
            continue
        for sel in (ui.get(route) or "").split(","):
            sel = sel.strip()
            if sel and sel not in out:
                out.append(sel)
    return ", ".join(out)


def _cfg_section(cfg):
    return (cfg or {}).get("access") or {}


def configured(cfg=None):
    """Has anybody actually written an access policy?

    True when at least one person is named, OR when `default` is set.

    `default` USED TO BE IGNORED, WHICH INVERTED IT. A section holding nothing
    but `default = "view"` was read as "no policy at all", and no policy means
    everybody the proxy admits is an admin — so an owner who wrote exactly the
    key that says "contain everyone who is not named" got maximum privilege for
    everyone, and Settings told them "No access policy is set" without ever
    mentioning that the key they had written was being discarded.

    The lock-everybody-out risk that reasoning was defending against does not
    exist: role() returns admin for the `local` sentinel unconditionally, and
    whoever can write config.toml already has the host or the share. A person
    who can set this key can unset it.
    """
    a = _cfg_section(cfg)
    return (any(a.get(k) for k in ("admins", "operators", "viewers"))
            or bool(str(a.get("default") or "").strip()))


def _listed(a, key, who):
    """Case-insensitive membership. Identity providers are inconsistent about
    the case of an address and nobody typing a policy should have to guess."""
    vals = a.get(key) or []
    if isinstance(vals, str):
        vals = [vals]
    return who.lower() in {str(v).strip().lower() for v in vals if str(v).strip()}


def role(who, cfg=None):
    """The level this identity holds.

    `local` is always admin: that is the loopback case with no proxy in front,
    where being on the machine IS the authentication — the same reasoning
    whoami() already documents. A policy cannot demote it, because the person
    holding the shell can edit the policy anyway, and pretending otherwise would
    be security theatre in the one place the product is honest about not having
    a boundary.
    """
    who = (who or "").strip()
    # ONLY the literal loopback sentinel is admin by default. This used to read
    # `if not who or who == "local"`, so an identity that was PRESENT but empty
    # was promoted to administrator: a header value of a single 0x0B byte
    # survives the HTTP parser, strips to "", is not "local" so it passes the
    # signed-in gate, and then landed here and got everything. The audit then
    # recorded it as `local` — the trail positively asserting the change was made
    # at the console by the owner.
    #
    # An identity that is present and empty is a MALFORMED identity, not an
    # absent one, and it gets the lowest level a policy allows.
    if who == "local":
        return "admin"
    # A request from off-box carrying no identity. Never admin: whoami() used to
    # call this "local" too, which handed the console's privileges to anybody a
    # proxy forwarded without an identity header.
    if who == "anonymous":
        return "view"
    if not who:
        a = _cfg_section(cfg)
        if not configured(cfg):
            return "admin"          # no policy at all: unchanged behaviour
        return "view"
    a = _cfg_section(cfg)
    if not configured(cfg):
        return "admin"                      # no policy: as it always behaved
    if _listed(a, "admins", who):    return "admin"
    if _listed(a, "operators", who): return "operate"
    if _listed(a, "viewers", who):   return "view"
    fallback = str(a.get("default") or "view").strip().lower()
    return fallback if fallback in _RANK else "view"


def allows(have, need):
    return _RANK.get(have, -1) >= _RANK.get(need, len(LEVELS))


def may(who, route, cfg=None):
    """(allowed, needed_level, held_level) for one write route."""
    need = NEEDS.get(route, "admin")
    have = role(who, cfg)
    return allows(have, need), need, have
