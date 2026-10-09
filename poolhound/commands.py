#!/usr/bin/env python3
"""What may be asked of the pool, and on what terms.

WHY THIS IS ONE FILE USED BY BOTH ENDS
  the server builds commands and the agent on the Pi executes them. If each carried
  its own idea of what is legal they would drift, and the drift would be
  discovered the first time the server sent something the agent had never heard of —
  or worse, the first time the agent accepted something the server should never have
  been able to construct.

THE AGENT IS THE AUTHORITY, NOT the server
  the server is the internet-facing machine, sitting behind Entra. That is good
  authentication for people and no defence at all if the host itself is taken.
  AqualinkD has no password: anything that can reach port 8000 can run the
  equipment. So the rule is that the agent validates every command against this
  file before touching the panel, and the server's approval is necessary but never
  sufficient. A compromised the server gets the vocabulary below and nothing else —
  no arbitrary URLs, no other hosts, no devices not named here.

THE FOUR RULES THAT ARE NOT ABOUT AUTHENTICATION
  Expiry. A command queued while the link was down must not fire on
  reconnection. "Turn on the spa heater", delivered four hours late to an empty
  house, is the exact failure this whole design exists to avoid.

  Idempotency. Every command carries an id and the agent remembers what it has
  run. A dropped acknowledgement causes the server to resend; without this that
  becomes a second execution.

  Cooldown. Gas heaters are damaged by short cycling, and a UI that is laggy
  invites people to press twice. Per-device cooldowns are enforced here rather
  than hoped for in the interface.

  Rate. A bounded number of commands a minute, so a stuck client or a hostile
  one cannot sit there toggling a contactor.
"""
import datetime as dt, re, uuid

# Every device the agent will act on, with the values it will accept. Anything
# absent is read-only by construction — including the temperature sensors, the
# salt reading, and Extra_Aux, which on this panel drives the solar valve and is
# not something a person should be flipping from a phone.
SWITCHES = {
    "Filter_Pump": {"label": "Filter pump",   "cooldown_s": 120, "column": "pump",
                    "why": "Short cycling a pump motor shortens its life."},
    "Spa":         {"label": "Spa",           "cooldown_s": 60, "column": "spa"},
    "Aux_1":       {"label": "Pool light",    "cooldown_s": 5,  "column": "pool_light"},
    "Aux_2":       {"label": "Sheer descent", "cooldown_s": 15, "column": "sheer"},
    "Aux_3":       {"label": "Spa light",     "cooldown_s": 5,  "column": "spa_light"},
    # No column: the panel reports nothing about these, so their state can only
    # ever read "no reading" and a command to them is confirmed by the next
    # sample arriving rather than by the sample agreeing. Stated here rather
    # than left as an absence in four separate lookup tables.
    #
    # `nameable` is the other half of that. Every other control on the tab
    # carries a sentence about consequence -- "short cycling a pump motor
    # shortens its life", "the most expensive thing the pool can do" -- and
    # these two carried nothing at all, on a product whose stated hazard is that
    # the pool is a physical system. An owner who is not the author was offered
    # two buttons called "Aux 4" and "Aux 5" with no state and no description
    # and no way to find out, and had to decide whether one of them starts a
    # waterfall or a booster pump running dry. The panel genuinely does not name
    # them, so the name has to come from the person who wired them: [circuits]
    # in config.toml, read by _labels() below. Until it does, the card says so.
    "Aux_4":       {"label": "Aux 4",         "cooldown_s": 15, "column": None,
                    "nameable": True,
                    "why": "This panel reports no state for this circuit, so "
                           "poolhound cannot confirm afterwards what it did — "
                           "only that the command was delivered."},
    "Aux_5":       {"label": "Aux 5",         "cooldown_s": 15, "column": None,
                    "nameable": True,
                    "why": "This panel reports no state for this circuit, so "
                           "poolhound cannot confirm afterwards what it did — "
                           "only that the command was delivered."},
    "SWG/Boost":   {"label": "Salt cell boost", "cooldown_s": 300, "column": None,
                    "why": "Boost runs the cell hard; it is not a toggle."},
}

# `column` is the ON-OFF state column; `value_column` is where the setpoint
# VALUE lands. They are different questions -- "is the heater running" and "what
# is it set to" -- and only the first had an answer.
# THE DOORS TO THIS PANEL THAT CAN SAY SO.
#
# Six things can change this panel -- AqualinkD's own UI, poolhound local
# (the optional page on the Pi), aquapda_sim, aqmanager, the Jandy wall remote, the panel's
# keypad and
# its internal schedules -- and the panel reports only the RESULT. The agent
# watches for that result, so every one of them is captured as a change. Which
# one it came through is a separate claim, and only a door that is wired to say
# so can make it.
#
# AN ALLOWLIST, AND A PREFIX, BECAUSE THE CLAIM IS NOT PROVED. It arrives from
# an unauthenticated page on the house LAN, so anything on that network can
# make it. That is tolerable for "which door" and NOT for "which person": a
# claim of `somebody@example.com` would put an identity nothing verified into an
# append-only audit log, and an audit you cannot trust is worse than none
# because it gets believed. So a door must be named here to be recorded at all,
# and what gets written is `door:<name>` -- a string that cannot be mistaken
# for somebody's identity, whatever the claim said.
DOORS = {
    # The key is what the page sends, so it stays "pool.html" whatever the page
    # is called: renaming it here would refuse every claim the live page makes.
    "pool.html": "poolhound local, the optional page on the Pi",
}


def door_label(raw):
    """`door:<name>` for a door we know, or None. Never an identity."""
    name = str(raw or "").strip()
    return f"door:{name}" if name in DOORS else None


SETPOINTS = {
    "Pool_Heater":   {"label": "Pool heater",  "min": 60, "max": 104, "cooldown_s": 300,
                      "column": "pool_heat", "value_column": "pool_set",
                      "why": "Gas heaters are damaged by short cycling, and this is "
                             "the most expensive thing the pool can do."},
    "Spa_Heater":    {"label": "Spa heater",   "min": 60, "max": 104, "cooldown_s": 300,
                      "column": "spa_heat", "value_column": "spa_set",
                      "why": "As the pool heater, and the spa reaches temperature fast "
                             "enough that repeated adjustment is rarely warranted."},
    # 34-42, not 55-65. The panel reports freeze_set = 34.0 on every sample that
    # carries it (read off /mnt/poolhound/samples.csv on the server, 14 of the last
    # 400 rows -- the rest predate the column). 55-65 was a number chosen rather
    # than read, and it refused the one value this setpoint is ever observed
    # holding: the card rendered "now 34" inside an input whose min was 55, so
    # the browser marked the field invalid on load and pressing Set was refused
    # by poolhound's own validator, in a message that blamed the operator. 42 is
    # the top of the Jandy freeze-protection range; the panel is the authority
    # and t_setpoint_ranges_admit_the_panel now checks it against real samples.
    #
    # column is "freeze", not None. The agent has been writing that column all
    # along and watch.py mails the owner when it changes -- while the one card
    # devoted to freeze protection was painted "unknown" forever and the device
    # was absent from /api/health, because this line said the panel was silent
    # about it. Two halves of the product disagreeing about whether a fact is
    # knowable, with the control tab the half that was wrong.
    "Freeze_Protect": {"label": "Freeze protection", "min": 34, "max": 42,
                      "cooldown_s": 300, "column": "freeze", "value_column": "freeze_set",
                      "why": "Freeze protection is what stops the plumbing splitting. "
                             "Raising it runs the pump on cold nights; lowering it is "
                             "a decision worth making deliberately, once."},
    "SWG/Percent":   {"label": "Salt cell output", "min": 0, "max": 100, "cooldown_s": 600,
                      "column": None, "value_column": "swg_pct",
                      "why": "Chlorine takes days to find its new level after a change. "
                             "Adjusting again before it settles means neither change can "
                             "be attributed afterwards."},
}

ACTIONS = ("set", "setpoint", "all_off", "resync")

# What the owner calls each circuit, when the panel cannot say.
#
# DISPLAY ONLY, and deliberately so. The vocabulary above -- which device ids
# exist, what values they take, what they cost -- is the thing the server and the
# agent must not disagree about, and it stays hard-coded here for exactly that
# reason. A name is not part of it: renaming Aux_4 to "Booster pump" changes
# what the card says and changes nothing about what is accepted, so the two ends
# can hold different config files and still be unable to drift.
#
# The sanitising is not decoration. This string is rendered into the control
# card, into the audit line and into the JSON catalogue the browser parses, so
# it is length-capped and stripped of the characters that would let a config
# file reach into any of the three.
LABEL_MAX = 32
_LABELS = None

def _labels(cfg=None):
    """{device: name} from the config's [circuits] table, or {} if there is none.

    Wrapped in the same guard help.py uses: config.load() exits the process when
    there is no config at all, which is correct for a collector being run by
    hand and wrong for a display helper on the Pi, where a missing config must
    degrade to the built-in labels rather than take the agent down mid-command.
    """
    global _LABELS
    own = cfg is None          # did we go and find the config ourselves?
    if own:
        if _LABELS is not None:
            return _LABELS
        try:
            from . import config
            cfg = config.load()
        except SystemExit:     # no config file anywhere; not this module's problem
            cfg = {}
        except Exception:      # unreadable or malformed; the loaders report it
            cfg = {}
    out = {}
    for dev, raw in (cfg.get("circuits") or {}).items():
        if dev not in SWITCHES and dev not in SETPOINTS:
            continue
        name = re.sub(r"[\x00-\x1f<>&\"']", "", str(raw)).strip()[:LABEL_MAX]
        if name:
            out[dev] = name
    if own:
        _LABELS = out          # cached only when it is the process-wide config
    return out

def label_for(device, cfg=None):
    """What to call `device` on a page or in a log line."""
    spec = SWITCHES.get(device) or SETPOINTS.get(device) or {}
    return _labels(cfg).get(device) or spec.get("label") or str(device)

# Appended to `why` for a circuit the panel does not name and the owner has not
# either. It names the file and the section, because "ask whoever administers
# this install" is not an answer when the reader IS that person.
UNNAMED_NOTE = (" The panel does not say what it switches either, and poolhound "
                "will not guess — name it under [circuits] in config.toml "
                "({device} = \"what it switches\") and this card will say so.")

# A command older than this is refused rather than run late.
MAX_AGE_S = 300
# Ceiling on how much can be asked of the panel at once, whoever is asking.
RATE_LIMIT, RATE_WINDOW_S = 12, 60

class Refused(Exception):
    """Raised with a reason fit to show a person and to write to the log."""

def new_command(action, device=None, value=None, by=None):
    """Construct one. Used by the server; the agent never trusts the result."""
    return {
        "id": str(uuid.uuid4()),
        "issued": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "action": action, "device": device, "value": value,
        "by": (by or "unknown")[:120],
    }

def _age_seconds(issued, now=None):
    try:
        t = dt.datetime.fromisoformat(str(issued).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise Refused("the command has no usable timestamp")
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    now = now or dt.datetime.now(dt.timezone.utc)
    return (now - t).total_seconds()

def validate(cmd, now=None):
    """Check one command. Returns a normalised copy or raises Refused.

    Deliberately strict about shape as well as content. A command is a small
    fixed structure and anything that does not match it is a bug or an attack,
    neither of which should reach the panel.
    """
    if not isinstance(cmd, dict):
        raise Refused("a command must be an object")

    cid = str(cmd.get("id") or "")
    if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", cid):
        raise Refused("missing or malformed command id")

    action = cmd.get("action")
    if action not in ACTIONS:
        raise Refused(f"unknown action {action!r}")

    age = _age_seconds(cmd.get("issued"), now)
    if age > MAX_AGE_S:
        raise Refused(f"expired — issued {age/60:.0f} minutes ago, and a command that "
                      f"waited that long should be re-issued deliberately rather than "
                      f"run late")
    if age < -60:
        raise Refused("issued in the future; clocks disagree by more than a minute")

    out = {"id": cid, "action": action, "issued": cmd.get("issued"),
           "by": str(cmd.get("by") or "unknown")[:120]}

    if action in ("all_off", "resync"):
        return out

    device = cmd.get("device")
    if action == "set":
        if device not in SWITCHES:
            raise Refused(f"{device!r} is not a switch this agent will operate")
        if cmd.get("value") not in (0, 1, "0", "1", True, False):
            raise Refused("a switch takes 0 or 1")
        out["device"] = device
        out["value"] = 1 if cmd.get("value") in (1, "1", True) else 0
        return out

    if device not in SETPOINTS:
        raise Refused(f"{device!r} is not a setpoint this agent will change")
    spec = SETPOINTS[device]
    try:
        v = int(float(cmd.get("value")))
    except (TypeError, ValueError):
        raise Refused("a setpoint takes a number")
    if not (spec["min"] <= v <= spec["max"]):
        raise Refused(f"{spec['label']} accepts {spec['min']}–{spec['max']}, not {v}")
    out["device"] = device
    out["value"] = v
    return out

def cooldown_for(cmd):
    d = cmd.get("device")
    if cmd.get("action") == "all_off":
        return 30
    if cmd.get("action") == "resync":
        return 60
    return (SWITCHES.get(d) or SETPOINTS.get(d) or {}).get("cooldown_s", 10)

def describe(cmd, cfg=None):
    """One line, for an audit entry and for the page. Must not be reversible into
    anything the reader could not already see.

    Uses the owner's name for the circuit where there is one, so the audit log
    reads "Booster pump on" rather than "Aux 4 on" — a record of something done
    to a physical system is worth being able to read a year later.
    """
    a = cmd.get("action")
    if a == "all_off":
        return "turn everything off"
    if a == "resync":
        return "resync the panel"
    d = cmd.get("device")
    label = label_for(d, cfg)
    if a == "set":
        return f"{label} {'on' if cmd.get('value') else 'off'}"
    if cmd.get("value") is None:
        # An action with no value -- the rest of them are named above, so this
        # is a new one, and "Filter pump to None" is not a sentence.
        return f"{label} {a}" if a else label
    return f"{label} to {cmd.get('value')}"


def from_detail(device, detail, cfg=None):
    """Rebuild the command that a stored audit `detail` came from.

    audit.csv keeps a command as `object` (the device) and `detail` -- the
    action, and the value after an "=" when there is one: "set=1", "set=0",
    "setpoint=88", "all_off". Turning that back into the dict describe() takes
    was done inline in server.py as `detail.split("=", 1)[1]`, which hands over
    a STRING -- and describe() asks `if cmd.get("value")`, where the string
    "0" is TRUE.

    SO EVERY SWITCH-OFF IN THE DURABLE LOG RENDERED AS ON. Measured:
    `set=0` produced "Filter pump on". The session half of the table was
    unaffected because it keeps the original command object with a real 0 in
    it, so the row was right until a restart and then quietly inverted -- and
    a restart is the exact state the durable half exists for.

    It lives here because this is where the vocabulary lives: `describe()` is
    the one sentence a command is written as, and the inverse of it has no
    business being a line of the HTTP layer.
    """
    action, eq, raw = str(detail or "").partition("=")
    value = None
    if eq and raw != "":
        if raw.lower() in ("true", "false"):
            value = raw.lower() == "true"
        else:
            try:
                value = int(raw)
            except ValueError:
                try:
                    value = float(raw)
                except ValueError:
                    value = raw
    return {"device": device, "action": action, "value": value}

def catalogue(cfg=None):
    """What the server may offer, so the page cannot present a control the agent
    would refuse.

    `label` and `why` are resolved here rather than read straight off SWITCHES,
    because a circuit the panel does not name has two different honest cards:
    one that says what the owner called it, and one that admits nobody has said.
    Resolving it in the catalogue keeps that decision in the shared vocabulary
    instead of in the page, where a second copy would go stale.
    """
    def _sw(dev, spec):
        named = _labels(cfg).get(dev)
        why = spec.get("why")
        if spec.get("nameable") and not named:
            why = (why or "") + UNNAMED_NOTE.replace("{device}", dev)
        return {"label": named or spec["label"], "cooldown_s": spec["cooldown_s"],
                "why": why or None, "named": bool(named) or not spec.get("nameable")}

    return {
        "switches": {k: _sw(k, v) for k, v in SWITCHES.items()},
        # value_column travels with the rest: the page reads the CURRENT setpoint
        # through it, and a projection that dropped it silently is why three of
        # the four cards said "setpoint not reported" even once the columns
        # existed and the samples carried them.
        "setpoints": {k: {"label": label_for(k, cfg), "min": v["min"], "max": v["max"],
                          "cooldown_s": v["cooldown_s"], "why": v.get("why"),
                          "value_column": v.get("value_column")}
                      for k, v in SETPOINTS.items()},
        "actions": list(ACTIONS), "max_age_s": MAX_AGE_S,
        "rate_limit": RATE_LIMIT, "rate_window_s": RATE_WINDOW_S,
    }

# Device -> the samples.csv column that reports its state, and the inverse.
#
# This mapping existed FIVE times — twice in server.py forty lines apart, once
# in agent.py on the Pi, once in panels.py and inverted once in render.py — and
# render.py's comment claimed "this is the only place the two meet". Two of
# those copies are on different machines and are released separately, which is
# the exact shape of drift commands.py exists to prevent: the server and the agent
# already validate the vocabulary against this module, and the column is simply
# the other half of the same vocabulary.
#
# A device with column None reports no state at all. That is a fact about the
# panel, not a gap in the table, so it is written down rather than left to be
# inferred from a missing key.
STATE_COLUMN = {d: spec.get("column") for d, spec in
                list(SWITCHES.items()) + list(SETPOINTS.items())}
COLUMN_DEVICE = {col: d for d, col in STATE_COLUMN.items() if col}

def state_column(device):
    """The samples.csv column reporting `device`, or None if the panel is silent."""
    return STATE_COLUMN.get(device)


# --------------------------------------------------- readings that need flow
# Sensors that sit in the plumbing and only mean anything while water is moving
# past them. Both collectors -- the Pi's agent and bin/sample -- pass their
# finished row through drop_stale_flow_readings() before it is written.
#
# The panel's "I cannot measure that right now" answer, as a value rather than
# as a number typed wherever it is needed. agent.py held its own copy and so
# could have drifted from the rule below that interprets it.
# How long a commanded change stays "waiting" before the page and the server
# both stop expecting the panel to agree.
#
# ONE NUMBER, BECAUSE THERE WERE TWO. The server gave up after 240 seconds and
# the page's own watch ran for 20 minutes, so for sixteen minutes the tab that
# SENT a command said it was still waiting while every other view — a second
# browser, a reload, anything reading `pending` from /api/health — showed
# nothing outstanding. Two screens, one pool, opposite answers.
PENDING_GIVES_UP_S = 240

NO_READING = -999

# WHY THIS IS NOT JUST THE -999 RULE
#   AqualinkD answers -999 for Temperature/Pool and Temperature/Spa when the
#   pump is off, and num()/val() already drop that sentinel. SWG/PPM does NOT.
#   It keeps answering the last figure the cell measured, unchanged, for as long
#   as the pump stays off -- 3500 held from 22:00 to 09:53 the next morning, then
#   3400 from 16:13 to 09:47, bit-identical across every sample in between. The
#   docs asserted the sentinel covered "the SWG and water temperature" alike; it
#   covers the temperature and not the salt.
#
#   So 131 of the first 189 salt readings ever stored were the same latched
#   number written down again, indistinguishable in the CSV from 131 fresh
#   measurements. render.by_day() averages the salt column over every row in a
#   day, which meant the daily figure was mostly a weighted vote for whatever
#   the cell happened to read at the moment the pump shut off, and the apparent
#   stability of the series was an artefact of counting one reading many times
#   (all rows sd 79 ppm; live readings only, 153).
#
#   A latched value is not a wrong reading to be corrected later -- it is not a
#   reading at all, and the place to refuse it is the collector, before it
#   becomes history. This is the same judgement the -999 sentinel already gets.
FLOW_DEPENDENT = ("salt_ppm", "pool_temp", "spa_temp")

def drop_stale_flow_readings(row, pump_col="pump"):
    """Blank the flow-dependent readings in `row` when the pump is not running.

    Returns the same dict, mutated. Only the panel's own pump state decides;
    a row that does not say whether the pump ran is left alone, because
    guessing is how a real reading gets thrown away.
    """
    if str(row.get(pump_col, "")).strip() != "0":
        return row
    for col in FLOW_DEPENDENT:
        if col in row:
            row[col] = ""
    return row
