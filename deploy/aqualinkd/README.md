# AqualinkD, patched to read the panel's own programs

Stock AqualinkD can switch the panel's circuits and keep its own cron
schedules. It cannot read the programs stored **in** the Jandy panel, and those
are the ones that keep the filter pump on its hours when the Pi, AqualinkD or
the server are down. `pda-programs.patch` adds a reader for them, and a setter for a
program's ON and OFF times. It also closes a hole in stock AqualinkD's own
schedule save (below).

| File | What it is |
|---|---|
| `pda-programs.patch` | against the AqualinkD **v3.1.0** tag: `pda_programs.c`/`.h`, the hooks into the PDA menu tracker, the programmer and the web server, and `extras/test_pda_programs.c` |
| `build.sh` | downloads v3.1.0, checks it against a pinned SHA-256, applies the patch, builds for arm64 in Docker, and **runs the test harness — a failing harness writes no binary** |
| `install-pi.sh` | `--check` is read-only; given a build directory it installs; `--rollback` puts the stock binary back. The first install keeps the stock binary as `aqualinkd.stock` and never overwrites that copy |

```bash
./deploy/aqualinkd/build.sh                       # prints the directory it built into
PI_HOST=<ip> PI_KEY=~/.ssh/<key> ./deploy/aqualinkd/install-pi.sh --check
PI_HOST=<ip> PI_KEY=~/.ssh/<key> ./deploy/aqualinkd/install-pi.sh <that directory>
PI_HOST=<ip> PI_KEY=~/.ssh/<key> ./deploy/aqualinkd/install-pi.sh --rollback
```

## What it adds

```
GET /api/programs                                  the last complete read, the state of the
                                                   current one, and the last change's result
PUT /api/programs/refresh                          start a read; returns at once
PUT /api/programs/set/<DEVICE>/<HHMM>-<HHMM>       change one device's ON and OFF times,
                                                   24-hour, e.g. .../SOLAR HEAT/1040-1555
```

**The two routes that act are HTTP PUT only, and refused over AqualinkD's
websocket.** As a GET, any page open in a browser on the house network could
fire one with an image tag. A cross-site PUT needs a CORS preflight, which
AqualinkD never grants, and a websocket can be opened by any site. Their
lengths are checked against the URI's own length rather than left to
`strncmp`, because a websocket URI is not NUL-terminated. Poolhound's agent
sends both as PUT, and Pool control's *Change* form reaches the second one
through it.

**AqualinkD's own schedule save is hardened.** Stock v3.1.0's
`/api/schedules/set` writes each field of each schedule unescaped into
`/etc/cron.d/aqualinkd` as `root curl ... <url> -d value=<value>`, copies
each into a fixed 20- or 40-byte buffer with no length check, and accepts any
HTTP method. So a GET from any page open in the house emptied the file, and a
text/plain POST (which needs no CORS preflight) could add a line that runs a
command as root. The patch:

- accepts the save over HTTP **only as a PUT** (`schedules/set is HTTP PUT
  only` otherwise). AqualinkD's own schedule page saves over its websocket and
  is unchanged;
- copies a field only if it fits its buffer;
- parses and checks **every** entry before the file is opened: the five cron
  fields only digits and `* , / -`, the url `/api/<id>/set` of letters, digits,
  `_` and `/`, the value digits only. One bad entry refuses the whole save
  (`Error schedule N refused, nothing saved`), so the file is never left half
  written.

The harness feeds it a command in the url, an overlong field, a non-numeric
value, a space in a cron field, a missing field and a good entry followed by a
bad one; each must be refused without the file being created. Checked on the
live Pi on 2026-10-10: GET and POST refused with 400, both bad PUTs refused,
the cron file's checksum unchanged, AqualinkD still running.

**Changing a program** goes through CHANGE PROGRAM (`docs/JANDY-PROGRAMS.md`,
section 5). It changes the ON and OFF times of a device that has exactly one
program, and nothing else:

- **It refuses** a device the last read did not list, a device with no program
  (adding one is unmapped), and a device with several (moving between them is
  unseen). It also refuses a change while another is running.
- **Every press is checked.** After each Up or Down, it reads the line the panel
  redraws, and the value must have moved by exactly one step. Both
  directions of both fields are seen on the live panel (Up +1, Down −1). The
  first hour press is still allowed to reveal a reversed direction, once, for
  a panel that behaves differently.
- **What a failure leaves.** No way out of the editor except saving has been
  seen, so a change that goes wrong puts the current field back and Selects
  through the rest unchanged. Fields already finished stay changed.
- **The result is the panel's own answer.** After saving, the device screen is
  read back. `set` in `GET /api/programs` gives `ok` only when the times read
  back are the times asked for, and it always carries what the panel shows
  (`now_on`, `now_off`).

A read walks the PDA menus (MENU, then PROGRAM, then each device), reads each
device's screen and presses Back. It **never** presses Select on a device's own
screen, because every selectable line there adds, deletes or changes a program.
Every screen, line by line, is in
[`docs/JANDY-PROGRAMS.md`](../../docs/JANDY-PROGRAMS.md).

The poolhound agent asks for a read about once a day and reports the result
with AqualinkD's own `/api/schedules` every 30 minutes. A stock AqualinkD
answers `/api/programs` with an error. The agent reports that, and the Pool
control tab says the patch is missing.

## Why it is shaped this way

- **Inside AqualinkD, not beside it.** An outside script could press the keys
  through the simulator websocket. It would then be guessing at timing,
  competing with AqualinkD for the panel, and it could not tell one screen from
  another except by counting key presses. Inside AqualinkD, the reader waits on
  the panel's own packets and uses AqualinkD's `goto_pda_menu()`, which checks
  which screen is showing before it presses anything. It also holds the single
  programming slot, so it cannot interleave with a button press.
- **A new screen type, tested before the old ones.** A device's program screen
  is titled with the device's own name. POOL HEAT's program screen therefore
  starts with the exact line 0 that AqualinkD uses to recognise the pool
  heater's *setpoint* screen. `PM_PROGRAM_DEVICE` is recognised by its ADD
  PROGRAM line, which the panel sends before the title, and it is tested first.
  The harness checks both directions: a program screen is not taken for the
  setpoint screen, and the setpoint screen is still recognised.
- **Explicit cases in `pda.c`.** An unrecognised screen falls to AqualinkD's
  unknown-line handler, which sets device LEDs from any line that ends in
  N, F or A. The program screens get their own empty case so that handler never
  sees them.
- **Published whole.** The walk fills a private array and copies it into
  AqualinkD's state only when the first device comes round again. A walk that
  stops early records why and publishes nothing.
- **It refuses rather than guesses.** A screen the parser does not recognise
  stops the walk with `state: failed` and a reason.
- **A GET never blocks.** AqualinkD's web server is single-threaded and a walk
  takes about a minute. So `/api/programs` serves the last result, and
  `/refresh` starts a new walk and returns at once.

## Known limits

- **A device with more than one program** reports the one on screen, with
  `of`. How the panel moves to the second has not been seen on this panel yet.
- **Changing times only.** ADD, DELETE and the days field are not mapped yet,
  so they are not offered. Proven live on 2026-10-10: minutes and hours in both
  directions, from AqualinkD's API and from Pool control on the live site.
  Back inside CHANGE has never been pressed, and the hour crossing 11/12 has not
  been seen.
- **The waits count packets, not seconds.** This is AqualinkD's existing
  design, and its own PDA threads have the same exposure. Upstream PR #538
  moves the waits to deadlines; rebase onto it when it lands.
- **The setter does not use AqualinkD's `waitForPDAMessageType()`.** That
  function looks at the *last* packet before it waits. On the first live change
  it let the key presses run ahead of the screen, and the cursor wrapped from
  CHANGE to ADD. That change failed safe. The setter now presses, waits for the
  key to be sent, and then waits on packets that arrive afterwards until the
  *screen* shows the change. It reads the cursor again after a settle before
  every Select. The reader still uses the old waits; it re-checks what it read
  and has walked the live panel correctly every time.
- **The patch is local.** It should be offered upstream. Until then, every
  AqualinkD upgrade means rebasing it and running `build.sh` again.
