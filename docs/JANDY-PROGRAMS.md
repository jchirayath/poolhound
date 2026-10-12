# The Jandy panel's own programs

How the PDA handheld's PROGRAM menus look on the wire, how the reader in
[`deploy/aqualinkd`](../deploy/aqualinkd/) walks them, and why it is shaped
the way it is. The Guide tab draws the same map; this is the long form.

Mapped 2026-10-09 on this panel, a **PDA-PS6 Combo**, which AqualinkD 3.1.0
addresses as a PDA at `0x60` (`panel_type = PD-6 Combo`, `device_id=0x60`).
Every screen below was read off the panel itself. Two passes were made through
AqualinkD's PDA simulator, viewing only and never editing: one read the
rendered screen, and one recorded the raw packets.

## Two kinds of schedule, and why both are kept

| | Panel programs | AqualinkD schedules |
|---|---|---|
| Stored in | the Jandy panel | `/etc/cron.d/aqualinkd` on the Pi |
| Run by | the panel | cron, calling AqualinkD's API |
| Still runs without the Pi | **yes** | no |
| Read by stock AqualinkD | **no** | yes, `GET /api/schedules` |
| Read by AqualinkD with the patch | yes, `GET /api/programs` | yes |
| Changed from Pool control | **ON and OFF times**, one program per device | yes: add, change, pause, delete (switch off only, for a heater) |

Neither of these is poolhound's own scheduler (`schedule.py`), which presses
buttons through the agent and is recorded in `audit.csv`. The Pool control tab
shows all three, with the two above in their own section, *Programmed on the
panel, and in AqualinkD*.

**When the panel and AqualinkD both schedule the same device, they fight.**
Each switches the device on its own timetable and neither knows about the
other. The section shows them side by side, explains the difference, and
recommends keeping schedules in the panel first. It also raises a warning
naming any equipment that has a panel program and an enabled AqualinkD
schedule, and the AqualinkD editor warns before saving one for equipment the
panel already programs. Fixing one is deleting the AqualinkD schedule, which
Pool control can do. How AqualinkD's schedules are saved, and why each field
is rebuilt rather than passed through, is in the README under *The panel's own
programs*.

## The keys

| Key | Code AqualinkD sends |
|---|---|
| Up | `0x06` |
| Down | `0x05` |
| Select | `0x04` |
| Back | `0x02` |
| Page up | `0x03` |
| Page down | `0x01` |

## The screens, line by line

A PDA screen has ten lines, numbered 0–9, each 16 characters. These are the
exact strings, spaces included.

### 1. Home

```
0  "     FRI  3:42PM"
1  "AIR             "
2  " 68`     76`    "
4  "POOL MODE     ON"   ← the cursor starts HERE. Select toggles the pool.
5  "POOL HEATER  OFF"
6  "SPA MODE     OFF"
7  "SPA HEATER   OFF"
8  "MENU            "
9  "EQUIPMENT ON/OFF"
```

To reach `MENU`, press Down four times.

### 2. Main menu

```
0  "   MAIN MENU    "
2  "HELP           >"   ← cursor
3  "PROGRAM        >"
4  "SET TEMP       >"
5  "SET TIME       >"
6  "PDA OPTIONS    >"
7  "SYSTEM SETUP   >"
```

**The layout changes with the pump.** While the filter pump runs, `SET
AquaPure` is added and `BOOST POOL` appears on line 9. The list then starts on
line 1, so PROGRAM moves to line 2. AqualinkD's `find_pda_menu_item()` already
handles both layouts. That is one reason the reader uses AqualinkD's navigation
and does not count key presses.

### 3. PROGRAM: the device list

```
0  "    PROGRAM     "
2  "FILTER PUMP     "   ← cursor
3  "SPA             "
4  "VSP1 SPD PGM    "   a submenu of pump speeds, not a schedule; not entered
5  "POOL HEAT       "
6  "SPA HEAT        "
7  "SOLAR HEAT      "
8  "POOL LIGHT      "
9  "SHEER DSCNT     "
```

**The list is a ring.** Press Down on line 9 and the panel sends SHIFTLINES
(`0x0F`, start 2, end 9, direction `0xFF`), then redraws line 9 with the next
device. The cursor stays on line 9. After `SPA LIGHT`, `AUX4` and `AUX5`, the
list comes back round to `FILTER PUMP`. The reader stops when the first
device's name returns.

### 4a. One device, with a program

```
0  "  FILTER PUMP   "
1  "   PGM 1 of 1   "
3  "ON      10:00 AM"
4  "OFF      4:00 PM"
5  "ALL DAYS        "
7  "ADD      PROGRAM"   ← cursor. Select here ADDS a program.
8  "DELETE   PROGRAM"
9  "CHANGE   PROGRAM"
```

### 4b. One device, with none

```
0  "      SPA       "
4  "  NO PROGRAMS   "
5  "    ENTERED     "
9  "  ADD PROGRAM   "   ← cursor
```

**The order the lines arrive in matters.** After the CLEAR, the panel sends
lines 7–9 and the highlight first, then the title and the times, and a STATUS
packet last. The reader waits for that STATUS before reading.

**A device's title is its own name.** POOL HEAT's program screen starts with
`"   POOL HEAT    "`, which is the exact line 0 that AqualinkD's `pda_m_type()`
uses to recognise the pool heater's *setpoint* screen. The patch therefore
tests for a program screen first, by its ADD PROGRAM line on 7–9 or its NO
PROGRAMS line on 4. Those lines arrive before the title, so the screen is
recognised correctly from its first packet.

### 5. CHANGE PROGRAM: the editor

Mapped 2026-10-09 by changing SOLAR HEAT's ON time from 10:40 AM to 10:05 AM
by hand through the simulator. The change was then confirmed by the reader
walking the panel on its own.

From a device's screen (4a), press Down twice to `CHANGE   PROGRAM`, then
Select. **There is no CLEAR.** The panel rewrites lines 1 and 6–9 in place
and switches from highlighting a whole line to highlighting characters
(`CMD_PDA_HIGHLIGHTCHARS`, `0x10`, with line, first and last character):

```
0  "   SOLAR HEAT   "
1  " CHANGE PROGRAM "
3  "ON      10:40 AM"   ← chars 8-9 highlighted: the ON hour
4  "OFF      3:55 PM"
5  "ALL DAYS        "
6  "Use ARROW KEYS"     (followed by stray bytes after a NUL)
7  "to set value."
8  "Press SELECT"
9  "to continue."
```

Select moves to the next field. Each move first re-sends HIGHLIGHTCHARS for
3/0-12, 4/0-12 and 5/0-7, then the field itself:

| Field | Line, characters | Up / Down |
|---|---|---|
| ON hour | 3, 8–9 | **one hour per press**: Up +1, Down −1, both seen live. There is no separate AM/PM field, so the hour carries it; crossing 11/12 has not been seen |
| ON minutes | 3, 11–12 | **one minute per press**. The panel redraws line 3 after each one, so the reader can watch the value |
| OFF hour | 4, 8–9 | as ON hour |
| OFF minutes | 4, 11–12 | as ON minutes |
| days | 5, 0–7 | not yet tried. It held `ALL DAYS` |

**The Select on the days field saves.** The panel sends a CLEAR and redraws the
device's screen (4a) with the new times, with the cursor on `CHANGE   PROGRAM`.
The only confirmation is the redrawn screen. Nothing was seen that would
cancel an edit part way through. Back was not pressed inside the editor, so
whether Back cancels without saving is unknown.

A setter therefore types nothing. It counts presses against what the panel
redraws: Select through the fields, Up or Down on a minute field until line 3
or 4 shows the wanted value, checking after every press, and then Select
through to the save. The redrawn 4a screen, read back, is the proof it took.

## What was on this panel when it was mapped

| Device | On | Off | Days |
|---|---|---|---|
| FILTER PUMP | 10:00 AM | 4:00 PM | ALL DAYS |
| SOLAR HEAT | 10:40 AM (10:05 AM since the editor was mapped) | 3:55 PM | ALL DAYS |
| every other device | no program | | |

This table is a record of one day, not a live reading. The Pool control tab
shows what the panel holds now.

## Changing a program from Pool control

The *Change* button on a device that holds one program opens two time fields.
Saving sends a `program` command (`"1040-1555"`, 24-hour) through
`/api/control`, which goes through the same checks as every other control. The
server also checks the Pi's last report. The agent sends AqualinkD a
`PUT /api/programs/set/<DEVICE>/<HHMM>-<HHMM>` and acknowledges that the change
was **accepted**. It then follows the change until the panel answers, and
pushes a fresh report. The page says *Sent* until that report arrives. It then
shows the panel's answer: *Done*, with the times read back, or *FAILED*, with
the reason and what the panel holds now.

Validated on 2026-10-09 end to end against a stand-in AqualinkD: a click in the
browser, then the server, the queue, the real agent, the PUT, the panel's
answer and the fresh report, and the row changed. A second change inside the
cooldown was refused by the Pi's Guard, and the form showed why.

**On the real panel, 2026-10-10.** The first live change (SOLAR HEAT from 10:05
back to 10:40) **failed safe**. The setter reported "the program editor did not
open on the ON hour", and a full re-read showed every program unchanged. The
cause was AqualinkD's own wait: `waitForPDAMessageType()` looks at the *last*
packet before it waits. Once one highlight had arrived, each later wait
returned at once, before the key just pressed had reached the panel. The Down
presses to CHANGE PROGRAM ran ahead of the screen, a stale read said CHANGE,
and a third Down already in flight wrapped the cursor to ADD. Select then
opened the **ADD** editor, which the failure path left with Back. That showed
something new: **Back cancels the ADD editor without adding anything.**

The setter now presses a key, waits for AqualinkD to send it, and then waits
on packets that arrive *afterwards* until the screen shows the change. It
re-reads the cursor after the panel settles before every Select. The second
live run moved SOLAR HEAT to 10:40 in 49 s. The read-back and a separate full
re-read agreed, and every other device was untouched. That run also showed the
minutes field's Up direction (05 → 40).

**The hour field, both ways, and the production path.** At 12:17 the same day,
SOLAR HEAT's ON was moved from 10:40 to 11:40 through the full web path: Pool
control's Change form, the server, the queue, a real agent, the patched
AqualinkD and the panel's editor. It took 40 s; the read-back and a separate
full re-read agreed. Up steps the hour +1, as the setter assumed. At 12:59 the
owner, signed in to the live site, moved it back to 10:40 the same way. The
audit names them, the agent's acknowledgement joins on the same command id,
the panel saved it 27 s after the press, and a full re-read showed every
device as it started. Down steps the hour −1.

## Checking that a program ran: SOLAR HEAT is a mode, not a switch

Tested 2026-10-10 with SOLAR HEAT's program moved to start at 10:05. A watcher
read AqualinkD's `/api/devices` every 10 s from 09:58 to 10:20. It saw the
filter pump come on at 10:01 (its program starts at 10:00). It reported
`SOLAR HEAT` as `off` throughout.

**That reading was the wrong signal.** AqualinkD maps SOLAR HEAT to
`Extra_Aux`, which is the solar *valve*. The panel's own EQUIPMENT ON/OFF
screen, read at 11:02 the same morning, said:

```
SOLAR HEAT   ENA
```

`ENA` means *enabled*: the program has put solar heating into its mode, and
the panel opens the valve only when the roof is warmer than the water. With
the air at 60°F and the pool at 71°F, it left the valve closed. AqualinkD
reports "enabled, valve closed" as plain `off`, so its API cannot show that a
SOLAR HEAT program started. Use the EQUIPMENT ON/OFF screen for that, or wait
for a warm enough afternoon to see the valve open. The same caution applies to
the `solar_valve` sample column, which in PDA mode read 1 for two days, pump
off included, and then 0 after an AqualinkD restart.

## What is not known yet

- **More than one program on a device.** Every device here had at most one,
  so how the panel moves between `PGM 1 of 2` and `PGM 2 of 2` has not been
  seen. The reader records the program it can see together with `of`, and the
  page says the others were not read. This is the first thing to map when a
  device gets a second program.
- **The rest of the editor.** CHANGE is mapped above: both directions of the
  minute and hour fields, and saving, are seen. Not seen: the hour crossing
  11/12 (where AM and PM should flip), the days field's choices, DELETE, and
  what Back does inside CHANGE. Back inside **ADD** cancels without adding:
  seen 2026-10-10.
- **Other PDA firmware.** These strings come from one panel. A different
  revision may word them differently. The reader refuses a screen it does not
  recognise and does not guess, and it reports the failure in `/api/programs`.

## The reader, in brief

`get_PDA_aqualink_programs()` in `deploy/aqualinkd/pda-programs.patch` does
the following:

1. It takes AqualinkD's single programming slot. Only one menu operation runs
   at a time, and a button press made meanwhile waits behind it.
2. It uses AqualinkD's own `goto_pda_menu()` to go from home to MENU to
   PROGRAM.
3. For each device on the list, it presses Select, waits for the screen to be
   fully drawn, copies all ten lines, parses them, then presses Back. It skips
   `VSP1 SPD PGM`.
4. It moves Down and checks that the highlight moved.
5. When the first device name comes round again, it publishes every program at
   once, so `/api/programs` never serves half a walk.
6. It returns home.

Any step that does not find the screen it expects stops the walk. The walk
then records why in `error` and returns home without publishing anything.

`set_PDA_aqualink_program()` is the setter. It walks to the device the same
way and checks the device holds exactly one program. If the times already
match, it does nothing. Otherwise it moves to CHANGE PROGRAM and steps through
the editor (section 5), pressing Up or Down and checking each redraw. It
Selects through the days field unchanged, which saves, and then reads the
device screen back. A change only counts once that read-back agrees.

```
GET /api/programs          the last complete read, and the state of the current one
PUT /api/programs/refresh  start a read; returns at once
PUT /api/programs/set/<DEVICE>/<HHMM>-<HHMM>   change one device's ON/OFF times
```

```json
{"type":"programs","state":"ok","read_at":1791588420,"started_at":1791588360,
 "error":"","programs":[
  {"device":"FILTER PUMP","pgm":1,"of":1,"on":"10:00 AM","off":"4:00 PM","days":"ALL DAYS"},
  {"device":"SPA","pgm":0,"of":0,"on":"","off":"","days":""}]}
```

`state` is one of `never_read`, `reading`, `ok`, `failed` or `unsupported`. The
last of these means a panel that is not a PDA.
