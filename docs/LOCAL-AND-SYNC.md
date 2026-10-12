# poolhound local and poolhound: the same scenes and schedules, kept in step

poolhound local (the page on the Pi) has the same scenes and schedules as
poolhound (the web app), so a household without the server loses nothing. A
household with both sees the same list in either place. A change made in one
reaches the other, even after a delay, and **the later change wins**.

## Decisions

| | Chosen | Rejected | Revisit if |
|---|---|---|---|
| Who runs schedules | **The Pi, always.** One runner, at the pool: schedules fire when the internet or the server is down, and nothing can fire twice. The server edits, mirrors and alerts | The server fires while it can reach the Pi and the Pi takes over otherwise: needs a hand-over rule that can neither double-fire nor drop | there is ever more than one Pi per pool |
| Where the Pi keeps them | **`/var/lib/poolhound` on the SD card, written only when something changes**: a save, a delete, a schedule firing, an automatic off, a sync that brought a change. A handful of writes a day, each atomic | Memory only: a reboot on a Pi with no server loses everything. A USB stick: another thing to fit and fail | a card dies again; the writes are counted in the agent's log |
| Conflicts | **Last update wins, per scene and per schedule**, by `updated_at` (UTC, both clocks on NTP). A deletion is a change like any other: it leaves a marker (`deleted`) for 90 days so it syncs too | Field-level merging: two people editing one scene within minutes is rare, and a merged scene nobody wrote is worse than either | clocks cannot be trusted on either side |
| When they sync | **Only when something changed.** Each side keeps a fingerprint of its list; the agent asks with its fingerprints on start, on reconnect, after any local change, and when the server says "changed" down the command stream. Equal fingerprints end the exchange in one small request | Polling every few minutes: traffic and card writes for nothing | the stream proves unreliable at carrying the nudge |
| Who may edit locally | **Anyone who can open poolhound local**, recorded as `door:pool.html`. The page is on the house network, where AqualinkD already lets anyone switch anything | A login on the Pi: a password to forget on a page whose point is that it works when everything else does not | the Pi's page is ever reachable from outside the house |
| Settings the Pi needs | **Sent by the server** (circuit names, `[schedule.auto_off]`, `[schedule.max_on]`); built-in defaults without one | Editing them on the Pi: they are the household's limits, set once | a household without the server needs different limits |

## What lives where

| | Edited on | Stored on | Run by |
|---|---|---|---|
| Scenes | either page | both, kept in step | whichever page ran it; the Pi when scheduled |
| Schedules (poolhound's own) | either page | both, kept in step | **the Pi** |
| Automatic offs ("then off after", the household max-on) | created by a press, a scene or a schedule | the Pi; mirrored to the server | **the Pi** |
| What happened (fired, missed, switched off, gave up) | — | the Pi; mirrored to the server, which emails the alerts | — |
| The panel's own programs, AqualinkD's schedules | poolhound (and the panel, AqualinkD) | the panel, the Pi | the panel, cron |

The scheduler's rules are unchanged by the move, because it is the same code
(`schedule.py`) running on the Pi: an ON is never run more than 15 minutes late,
an OFF is retried until the panel reports it off, a recurring schedule runs
once per local date across clock steps and daylight saving, and the household
max-on watches what the panel reports. On the Pi the panel is read directly,
every tick, rather than from fifteen-minute samples.

**What the Pi alone cannot do.** It cannot email: a household without the
server sees missed and failed offs on poolhound local, not in an inbox. And it
cannot check the server's `[access]` list. So the server re-checks it, and
**withdraws** a schedule whose author no longer holds `operate`. The withdrawal
is a change, so it reaches the Pi at the next sync. Schedules made at poolhound
local are kept: the house network is their authority.

## The exchange

```
agent ── POST /api/agent/sync {defs_fp, runtime_fp} ──────────────► server
      ◄─ {"same": true}                                    nothing changed: done
      ◄─ {"send": true}                                    something differs
agent ── POST /api/agent/sync {defs, runtime} ────────────────────► server
                                     merge defs, last update wins; mirror runtime
      ◄─ {defs: the merged list, settings} ─────────────────────── server
agent: save only if that changed anything
server ── stream event {"type": "sync"} ──► agent      after a change on the server
```

`defs` are the scenes and schedules as people wrote them, with their deletion
markers. `runtime` is the Pi's own: pending automatic offs, each schedule's
last run, the history, and when the scheduler last ticked. The server never
edits it; it shows it.

A schedule's `last_key` (the local date it last ran) belongs to both: the later
date wins whichever side sent it. That is how the hand-over cannot run today's
19:00 twice.
