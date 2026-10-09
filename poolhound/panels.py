#!/usr/bin/env python3
"""The Chemicals and Settings tabs.

WHY THESE PAGES CAN WRITE, WHEN THE REST OF THE SITE IS STATIC
  Everything else here is generated once and read. Logging a dose and changing
  the pool volume are not reads, and the honest options were a form that writes
  or a form that pretends. So `bin/serve` runs a small loopback-only HTTP server
  that accepts exactly three POSTs and re-renders afterwards.

  When that server is not running — the page opened straight off disk, or served
  by anything else — the forms do not break and do not silently discard input.
  They detect the missing API on load and turn into the equivalent command line,
  which is the same code path underneath. The page is honest about which mode it
  is in rather than showing a Save button that does nothing.

WHY THE FORM PREVIEWS THE EFFECT
  A dose is entered as a volume and matters as a change in the water. Showing
  "32 fl oz of 31.45% acid" back to the reader confirms only that they typed it;
  showing "pH -0.21, alkalinity -8 ppm" is the thing they actually wanted to
  know, and it is computed from the same constants as the CLI — see
  chemicals.py, which exports them to the browser rather than letting a second
  implementation drift.
"""
import csv, datetime as dt, html, json, os, re

from . import chemicals as CH
from . import config
from . import pool_shape
from . import vault
from . import access
from . import commands as CMD
from . import switchlog
from .queue_ import QUEUE
from . import templates as TPL
from .render import (rows, num, when, ago, best_lab, targets, newest,
                     data_table, DAILY_MEASURES, MEASURE_NAMES, MATTERS)
from .icons import icon

CFG  = config.load()
POOL = CFG.get("pool", {})
# GAL_SET says whether anybody has actually told this install how big the pool
# is. GAL keeps a usable number so the arithmetic below has something to work
# with, but nothing may PRESENT that number as this pool's without checking.
GAL_SET = config.volume(CFG) is not None
GAL  = config.volume(CFG) or 15000.0

def reload():
    """Re-read the configuration into this module's globals. See render.reload().

    GAL matters most here: catalogue_json() bakes it into the <script id=
    "catalogue"> block the browser multiplies for every dose preview, so a stale
    value here is a wrong number on screen rather than merely an old label.
    """
    global CFG, POOL, GAL, GAL_SET
    CFG  = config.load()
    POOL = CFG.get("pool", {})
    GAL_SET = config.volume(CFG) is not None
    GAL  = config.volume(CFG) or 15000.0

def esc(v):
    return html.escape(str(v if v is not None else ""))

# ---------------------------------------------------------------- chemicals tab
# How many dose rows the log renders at once. Named so the number in the markup
# and the number in the sentence admitting the truncation cannot drift apart.
DOSE_SHOW = 40

def _unattributed(row):
    """What to show when a dose has no name against it.

    A DASH MEANT TWO DIFFERENT THINGS. A row written before `by` existed and a
    row where recording the name failed rendered identically — so a genuine
    future gap would look like ordinary history. The owner read a blank on a
    12 Sep row and reasonably concluded that the dose they had logged that
    morning had not been attributed either; it had.

    The id column arrived with attribution, which makes it a reliable marker of
    which era a row is from: no id and no name is history, no name WITH an id
    is a gap worth noticing.
    """
    if not row.get("id"):
        return ('<span class="sub" title="logged before poolhound recorded '
                'who">not recorded</span>')
    return ('<span class="bad" title="this dose was logged without a name, '
            'which should not happen">missing</span>')


def chemicals_panel(upkeep_est=None):
    log = rows("chemicals.csv")

    # THE CHEMISTRY AND WHAT IT SAYS ON THE BAG. Listing only the chemistry is
    # how "there is no option to add Leslie's Alkalinity Up" came to be a
    # reasonable thing to conclude: it is `bicarb`, the picker said "Sodium
    # bicarbonate", and the catalogue named the retail product for exactly the
    # two entries where somebody had happened to. See chemicals.SOLD_AS.
    opts = "".join(
        f'<option value="{k}" data-phase="{c["phase"]}">'
        f'{esc(CH.shelf_label(k))}</option>'
        for k, c in CH.CHEMICALS.items())

    logrows = "".join(
        f'<tr><td>{esc((r.get("ts") or "")[:16].replace("T", " "))}</td>'
        f'<td>{esc(CH.CHEMICALS.get(r.get("chemical",""), {}).get("label", r.get("chemical","")))}</td>'
        f'<td class="n">{esc(r.get("amount"))} {esc(r.get("unit"))}</td>'
        f'<td class="n">{esc(r.get("pct"))}%</td>'
        f'<td>{esc(r.get("note"))}</td>'
        # Who logged it. Blank for rows written before this was recorded, and
        # shown as a dash rather than as an empty cell so "nobody knows" reads
        # differently from "the column is broken".
        f'<td class="by">{esc(r.get("by")) or _unattributed(r)}</td>'
        # Every field the edit form needs rides on the button, so loading a row
        # back into the form needs no second lookup and cannot pick up a value
        # that has been re-rendered since.
        f'<td class="rm">'
        # THE ID, NOT ONLY THE TIME. ts was a dose's whole identity and the form
        # records to the minute, so two doses in one minute made BOTH rows
        # permanently uneditable and undeletable -- while the page went on
        # rendering an Edit and a Delete button on each. The row now carries the
        # id it was written with, and ts stays for the human-readable half.
        f'<button type="button" class="x dose-edit" data-id="{esc(r.get("id"))}" '
        f'data-ts="{esc(r.get("ts"))}" '
        f'data-chemical="{esc(r.get("chemical"))}" data-amount="{esc(r.get("amount"))}" '
        f'data-unit="{esc(r.get("unit"))}" data-pct="{esc(r.get("pct"))}" '
        f'data-note="{esc(r.get("note"))}" '
        f'aria-label="Edit this entry" title="Edit">&#9998;</button>'
        f'<button type="button" class="x" data-del="{esc(r.get("id") or r.get("ts"))}" '
        f'data-del-kind="{"id" if r.get("id") else "ts"}" '
        f'aria-label="Delete this entry" title="Delete">&times;</button></td></tr>'
        for r in reversed(log[-DOSE_SHOW:]))
    # Said, not swallowed. This table showed the last 40 of however many with no
    # count and no route to the rest -- verbatim the defect data_table() exists
    # to end, on the one table whose rows the owner AUTHORED. It cannot go
    # through data_table() yet because every row carries its own edit and delete
    # buttons and data_table() escapes its cells; render.data_table() needs an
    # optional `actions=callable(row)->html` column first, which is a change to
    # a file this pass does not own. Until then the count is at least honest and
    # the whole file is one click away.
    shown = min(len(log), DOSE_SHOW)
    dose_truth = (f'showing the most recent <b>{shown}</b> of {len(log):,} logged '
                  f'&middot; <a class="dt-get" href="/api/export/chemicals.csv" download>&darr; every '
                  f'row as CSV</a>' if len(log) > DOSE_SHOW else
                  f'{len(log):,} dose{"" if len(log) == 1 else "s"} logged'
                  if log else '')
    if not logrows:
        logrows = ('<tr><td colspan="7" class="empty">Nothing logged yet. Every dose '
                   'recorded here is one the model can attribute a change to; every '
                   'dose that is not is noise it has to absorb.</td></tr>')

    now = dt.datetime.now().strftime("%Y-%m-%dT%H:%M")
    # The preview multiplies through the pool volume, so quoting one nobody has
    # set would present a figure about a notional pool as a figure about theirs.
    dose_lede = (
        f"The effect is estimated against this pool's {GAL:,.0f} gallons as you "
        f"type. Those are starting figures from the chemistry, not measurements of "
        f"this pool \u2014 logging the dose is what eventually replaces them with "
        f"the real response." if GAL_SET else
        "No pool volume has been set yet, and every figure here multiplies through "
        "it. Set the volume on the Settings tab and the estimates below become "
        "about your water.")
    return f'''
<p class="sub helpto"><a href="#help" data-tab-link="help">Why strength and timing matter →</a></p>
<p class="lede">Record what went into the pool. <b>Strength is not optional</b> — a
gallon of muriatic at 14.5% and a gallon at 31.45% differ by more than a factor of
two, and without it a volume cannot be converted into an expected change, so the
regression would be fitting noise.</p>

<section>
  <h2>Log a dose</h2>
  <p class="sub">{dose_lede}</p>
  <div class="card">
    <form id="chem-form" class="form">
      <div class="frow">
        <label class="f"><span>Chemical</span>
          <select name="chemical" id="chem-select">{opts}</select></label>
        <label class="f f-sm"><span>Amount</span>
          <input type="number" name="amount" id="chem-amount" step="any" min="0" value="32" required></label>
        <label class="f f-sm"><span>Unit</span>
          <select name="unit" id="chem-unit"></select></label>
        <label class="f f-sm"><span id="pct-label">Strength</span>
          <input type="number" name="pct" id="chem-pct" step="any" min="0" max="100" required>
          <datalist id="pct-list"></datalist></label>
      </div>
      <div class="frow">
        <label class="f"><span>When</span>
          <input type="datetime-local" name="ts" id="chem-ts" value="{now}"></label>
        <label class="f f-wide"><span>Note <i>optional</i></span>
          <input type="text" name="note" placeholder="e.g. after the pH test read 7.9"></label>
      </div>
      <p class="blurb" id="chem-blurb"></p>
      <div class="preview" id="chem-preview"></div>
      <div class="factions">
        <button type="submit" class="btn primary">Log this dose</button>
        <span class="fmsg" id="chem-msg"></span>
      </div>
      <div class="offline-note">
        <p>The write API is not running, so this form cannot save. Start it with
        <code>bin/serve</code>, or log the dose from a terminal:</p>
        <pre id="chem-cli">bin/chem acid 32 floz</pre>
      </div>
    </form>
  </div>
</section>

<section>
  <h2>What has been added</h2>
  <p class="sub">Nothing here is inferred. If it was not logged, the model cannot see
  it — and a change in the water with no recorded cause is what makes a coefficient
  wrong rather than merely uncertain.</p>
  <div class="card"><div class="scroll"><table>
    <thead><tr><th>When</th><th>Chemical</th><th>Amount</th><th>Strength</th><th>Note</th>
    <th>Logged by</th><th><span class="sr">Edit or remove</span></th></tr></thead>
    <tbody>{logrows}</tbody></table></div>
    {f'<p class="sub" style="margin:10px 0 0">{dose_truth}</p>' if dose_truth else ''}
    <p class="sub" style="margin:12px 0 0">A log you can only append to is a log that
    carries its own typos forever, and a dose that never happened is worse for the fit
    than a missing one — the model would credit a real change to it. Removing an entry
    needs the write API running.</p>
  </div>
</section>
{_ledger_section(log)}
{_upkeep_section(upkeep_est)}'''

def _price_inputs():
    """One box per chemical in the catalogue, named by the setting."""
    out = []
    for chem, name in config.PRICE_SETTINGS.items():
        have = config.chemical_price(chem, CFG)
        from . import upkeep
        p = upkeep.price_for(chem, None, float(POOL.get("acid_pct", 31.45)))
        ref = p["price"] if p else None
        head, unit = CH.SHORT_NAMES[chem], f"per {CH.price_unit(chem)}"
        out.append(
            f'<label class="f f-sm"><span>{esc(head)} <i>{unit}</i></span>'
            f'<input type="number" name="{name}" step="0.01" min="0.01" max="999"'
            f' value="{f"{have:.2f}" if have else ""}"'
            f' placeholder="{f"typical {ref:.2f}" if ref else "not entered"}"></label>')
    return "".join(out)


def _ledger_section(log):
    """What was actually added, month by month, for a period the reader picks.

    The figures are computed by upkeep.ledger() and embedded as data; the
    page script only picks the months, groups and adds. Without a script the
    section says so rather than showing an empty table.
    """
    from . import upkeep
    L = upkeep.ledger(log, {k: config.chemical_price(k, CFG)
                            for k in config.PRICE_SETTINGS}, when, num)
    # Inside a script element "</" ends it, whatever the type says.
    data = json.dumps(L, separators=(",", ":")).replace("</", "<\\/")
    return f'''
<section id="dose-months">
  <h2>What has been added, by month</h2>
  <p class="sub">Every dose logged above, totalled by month and costed at your
  prices where you have entered them, typical retail where you have not. Pick a
  period; it is trimmed to the months the log actually covers.</p>
  <div class="card">
    <div class="dm-ctl">
      <label class="f f-sm"><span>Period</span>
        <select id="dm-preset">
          <option value="r12">Last 12 months</option>
          <option value="ytd">This year</option>
          <option value="prev">Last year</option>
          <option value="all">Everything logged</option>
          <option value="custom">Custom</option>
        </select></label>
      <label class="f f-sm"><span>From <i>month</i></span>
        <input type="month" id="dm-from" placeholder="YYYY-MM"></label>
      <label class="f f-sm"><span>To <i>month</i></span>
        <input type="month" id="dm-to" placeholder="YYYY-MM"></label>
    </div>
    <p class="sub" id="dm-note" aria-live="polite"></p>
    <div class="scroll"><table id="dm-table">
      <caption class="sr">Chemicals added, by month, with what they cost</caption>
      <thead></thead><tbody></tbody><tfoot></tfoot></table></div>
    <noscript><p class="sub">This table is put together in the browser, and
    scripts are off.</p></noscript>
  </div>
  <script type="application/json" id="dose-ledger">{data}</script>
</section>'''


def _upkeep_section(est):
    """What keeping it balanced takes. Absent, not empty, when nothing was
    computed -- the public build cuts this tab, and a section saying "no
    estimate" there would be a section about nothing."""
    if not est:
        return ""
    from . import upkeep
    return f'''
<section id="upkeep">
  <h2>What upkeep takes, by week and by year</h2>
  <p class="sub">Worked out from this pool's own record — the doses logged
  here, the pH either side of them, the pump's runtime and the panel's salt
  — rather than from a chart. Each figure says how it is known.</p>
  {upkeep.block(est)}
</section>'''

# ----------------------------------------------------------------- settings tab
def _cred_status(service, legacy_path=None):
    """Where this service's credentials actually are, and whether they will work.

    This used to check only for a FILE, which made it wrong on any deployment
    that had moved past one: on the server the passwords are in Azure Key Vault
    and the page reported "missing" beside a collector that was working
    perfectly. A status display that contradicts the thing it describes teaches
    people to stop reading it.

    It resolves the credential the same way the collectors do — and by the same
    call, so the two cannot disagree — but never shows it. What comes back is
    where it was found, not what it was.
    """
    try:
        user, secret, src = vault.credentials_for(service, legacy_path)
    except vault.VaultError as e:
        return "warn", str(e)[:120], ""
    if user and secret:
        where = {"keyvault": "Azure Key Vault, read with this host's managed identity — "
                             "no copy on disk",
                 "vault":    "the encrypted vault, key held in the system keychain",
                 "file":     f"a plain file at {legacy_path} — move it into the vault below"}
        return ("ok" if src != "file" else "warn"), where.get(src, src), src
    p = os.path.expanduser(legacy_path or "")
    if p and os.path.exists(p) and os.stat(p).st_mode & 0o077:
        return "warn", f"{legacy_path} is readable by others; the collector will refuse it", "file"
    return "missing", "no credentials — add them below and this source cannot run", ""

def _last(rowset, field="measured"):
    ds = [r.get(field) for r in rowset if r.get(field)]
    return ago(max(ds)) if ds else "never"

def targets_panel():
    """Show every opinion on what this pool should be, side by side.

    Three exist and they do not agree. Ours is derived — free chlorine as a
    fraction of cyanuric acid, alkalinity and calcium set by the sanitiser type.
    WaterGuru publishes its own in the dashboard payload. Leslie's states a salt
    range beside its results.

    Presenting only one of them would be a choice disguised as a fact. The
    disagreements are small except where they are not: WaterGuru aims cyanuric
    acid at 65 against a measurement of 38, which is a real recommendation to
    add stabiliser, and it would raise the free-chlorine target with it.
    """
    lab_rows  = rows("lab.csv")
    les_rows  = rows("leslies.csv")
    man_rows  = rows("manual.csv")
    pod_rows  = rows("readings.csv")
    wg_t      = rows("wg_targets.csv")
    # The SAME source list Home and the Pool chemistry tab reconcile over.
    # This panel left manual.csv out, so an owner with a drop kit and no
    # WaterGuru account compared vendor targets against a "current" column of
    # dashes -- and the two tabs could state different current alkalinities,
    # which is the one thing render.py owning the loaders exists to prevent.
    LAB = best_lab((newest(lab_rows), "WaterGuru"),
                   (newest(les_rows), "Leslie's"),
                   (newest(man_rows), "By hand"))
    # pH and free chlorine come through the daily reconciliation, pod included,
    # rather than off the end of readings.csv. `rows[-1]` is the last row
    # APPENDED, not the newest MEASURED -- and a backfill or a --from-file
    # replay puts an older reading last, which is the defect render.build()
    # records for this same pair of measures.
    DAILY = best_lab((newest(pod_rows), "WaterGuru pod"),
                     (newest(lab_rows), "WaterGuru"),
                     (newest(les_rows), "Leslie's"),
                     (newest(man_rows), "By hand"),
                     measures=DAILY_MEASURES)
    ours = targets(LAB, POOL.get("sanitiser"),
                   (newest(les_rows)).get("sanitizer", ""))
    vendor = newest(wg_t, "fetched")
    if not vendor:
        return ""

    LABELS = {"free_cl": "Free chlorine", "ph": "pH", "ta": "Alkalinity",
              "ch": "Calcium", "cya": "Cyanuric acid", "salt": "Salt"}
    FMT = {"ph": "{:.1f}", "free_cl": "{:.1f}"}
    rowsout, flagged = [], []
    for k, name in LABELS.items():
        f = FMT.get(k, "{:.0f}")
        band = ours.get(k)
        mine = f"{f.format(band[0])}–{f.format(band[2])}" if band else "—"
        aim = f.format(band[1]) if band else "—"
        v = num(vendor.get(k))
        theirs = f.format(v) if v is not None else "—"
        cur = (DAILY if k in DAILY_MEASURES else LAB).get(k, {}).get("value")
        curtxt = f.format(cur) if cur is not None else "—"
        # Flag only where the two aims differ by enough to change an action.
        # A proportional test alone gets this exactly backwards: it flagged pH
        # 7.5 against 7.6 — a difference nobody could dose for — while missing
        # cyanuric acid 50 against 65, which is the one real recommendation in
        # the table. The absolute floor is what carries the meaning.
        gap = ""
        if v is not None and band and abs(v - band[1]) >= MATTERS.get(k, 0):
            gap = "worth reconciling"
            flagged.append((name, band[1], v, k))
        rowsout.append(
            f'<tr><td>{esc(name)}</td><td class="n">{esc(curtxt)}</td>'
            f'<td class="n">{esc(aim)}<span class="rel">{esc(mine)}</span></td>'
            f'<td class="n">{esc(theirs)}</td>'
            f'<td>{esc(gap)}</td></tr>')

    if flagged:
        bits = ", ".join(f"<b>{esc(n.lower())}</b> ({f'{a:g}'} vs {f'{b:g}'})"
                         for n, a, b, _ in flagged)
        note = (f"Where a row says <b>worth reconciling</b> the two aims differ by enough "
                f"to change what you would do: {bits}. "
                + ("Cyanuric acid is the consequential one — it is the only measure here "
                   "that moves another target, because free chlorine is defined as a "
                   "fraction of it, so raising CYA raises the chlorine this pool needs."
                   if any(k == "cya" for *_, k in flagged) else ""))
    else:
        note = ("Nothing here needs reconciling: every vendor target sits close enough to "
                "the derived one that either would lead to the same action.")

    return f'''
<section>
  <h2>How this pool is judged</h2>
  <p class="sub">Three opinions on where each number should sit, and they do not
  fully agree. Ours is derived from the chemistry and this pool's sanitiser;
  WaterGuru publishes its own in the dashboard payload; Leslie's states a salt
  range beside its results. Only ours drives the tiles and the actions — the rest
  is here so that choice is visible rather than hidden.</p>
  <div class="card"><div class="scroll"><table>
    <thead><tr><th>Measure</th><th>Now</th>
      <th>poolhound<br><span class="th2">aim, and range</span></th>
      <th>WaterGuru<br><span class="th2">vendor target</span></th><th></th></tr></thead>
    <tbody>{"".join(rowsout)}</tbody></table></div>
    <p class="sub" style="margin:14px 0 0">{note}</p>
  </div>
</section>'''

def shape_panel(public=False):
    """The photo tracing tool, shown beside the volume it sets.

    PUBLIC MODE
    The calculator itself is useful to anybody with a pool and a phone, and
    nothing it does is specific to this house: it takes an outline and gives
    back a volume. So in the public build it is the whole tool minus two
    things — the outline THIS pool has saved, which is a fact about somebody
    home, and the button that overwrites it, which is a write.

    Everything the analysis works out is put on the page. It computes the shape
    classification, what that implies for the depth model, the split between pool
    and spa, the bench, a comparison against length x width, and where the
    uncertainty comes from — and for a while it computed all of that and showed
    none of it, which is the same as not having done it.
    """
    # A visitor must not be shown the outline of the owner's pool, nor be able
    # to load it — so it is not read at all in public mode rather than read and
    # then withheld, which would put it in the rendered page for anyone who
    # viewed source.
    saved = None if public else pool_shape.load()
    have = bool(saved and saved.get("points"))
    summary, saved_json = "", "null"
    if have:
        v, err = pool_shape.compute(saved)
        if v:
            saved_json = json.dumps(saved)
            summary = (f'<div class="saved-shape">'
                       f'<div class="ss-h"><b>{v["gallons"]:,.0f} gal</b>'
                       f'<span>saved {esc((saved.get("saved") or "")[:10])}</span>'
                       f'<button type="button" class="btn" id="shape-reload">Reload this outline</button></div>'
                       f'<div class="ss-b">{v["area_sqft"]:,.0f} sq ft &middot; '
                       f'{v["average_depth_ft"]:.1f} ft average &middot; '
                       f'{esc(v["shape_kind"])} &middot; &plusmn;{v["uncertainty_pct"]:.0f}%</div>'
                       f'</div>')

    return f'''
<details class="tool" id="shape-tool">
  <summary>Measure it from a photograph</summary>
  <div class="tool-body">
  {summary}
  <p class="sub">Length &times; width &times; average depth is exact for a rectangle and
  wrong for everything else — on a curved pool it can overstate the water by a third,
  and the trade's habit of multiplying by 0.85 is a guess about a shape nobody
  measured. Tracing the actual waterline replaces that guess with the real enclosed
  area, which is the term the volume is most sensitive to.</p>

  <p class="sub"><b>One real measurement is still needed.</b> A photograph cannot
  supply scale — a small pool photographed close and a large one photographed far away
  produce identical pixels, and knowing the phone does not help, because focal length
  fixes the camera's angle of view rather than its distance. Measure any one distance
  with a tape, click its two ends below, and everything else follows.</p>

  <div class="frow">
    <label class="f"><span>Photograph <i>overhead works best</i></span>
      <input type="file" id="shape-file" accept="image/*"></label>
    <span class="fmsg" id="shape-exif"></span>
  </div>

  <p class="sub" style="margin:4px 0 0">A satellite screenshot of your own address is
  the easiest overhead shot to get and needs no rectifying. A photo from the deck works
  too — use <b>Rectify</b> to mark four corners of anything you know is a rectangle on
  the water's plane, or the perspective will distort the area.</p>

  <div class="canvas-wrap" id="shape-stage" hidden>
    <div class="modes" role="group" aria-label="Tracing mode">
      <button type="button" class="btn primary" id="shape-detect">Find the water for me</button>
      <button type="button" class="btn mode" data-mode="trace" aria-pressed="true">Pool outline</button>
      <button type="button" class="btn mode" data-mode="scale" aria-pressed="false">Set scale</button>
      <button type="button" class="btn mode" data-mode="camera" aria-pressed="false">Use the camera</button>
      <button type="button" class="btn mode" data-mode="rectify" aria-pressed="false">Rectify <i>optional</i></button>
      <button type="button" class="btn" id="shape-undo">Undo point</button>
      <button type="button" class="btn" id="shape-clear">Clear</button>
    </div>
    <p class="detected" id="shape-detected" hidden></p>
    <p class="hint" id="shape-hint"></p>
    <canvas id="shape-canvas" width="900" height="600"></canvas>

    <div class="frow" id="scale-row" hidden>
      <label class="f f-sm"><span>That distance is</span>
        <input type="number" id="scale-ft" step="0.1" min="0.5" placeholder="feet"></label>
      <span class="fmsg" id="scale-note">Click two points you have measured.</span>
    </div>
    <div class="frow" id="ends-row" hidden>
      <span class="fmsg">The floor falls from <b>A</b> to <b>B</b>.</span>
      <button type="button" class="btn" id="shape-flip">Swap: shallow end is the other one</button>
    </div>
    <div class="frow" id="cam-row" hidden>
      <label class="f f-sm"><span>Camera height <i>ft above the water</i></span>
        <input type="number" id="cam-height" step="0.5" min="1" max="400" value="5.5"></label>
      <label class="f f-sm"><span>Tilt down <i>degrees</i></span>
        <input type="number" id="cam-tilt" step="1" min="1" max="90" value="45"></label>
      <span class="fmsg" id="cam-note"></span>
    </div>
    <div class="frow" id="rect-row" hidden>
      <label class="f f-sm"><span>Rectangle width</span>
        <input type="number" id="rect-w" step="0.1" min="0.5" placeholder="feet"></label>
      <label class="f f-sm"><span>Rectangle length</span>
        <input type="number" id="rect-l" step="0.1" min="0.5" placeholder="feet"></label>
      <span class="fmsg">Click its four corners, clockwise from the near-left.</span>
    </div>
  </div>

  <h3 class="fh">The floor, which the photograph cannot see</h3>
  <div class="frow">
    <label class="f"><span>How the floor is shaped</span>
      <select id="depth-model">
        <option value="">Let the outline decide</option>
        <option value="linear">One steady slope, end to end</option>
        <option value="lobes">Two basins at different depths</option>
        <option value="constant">All one depth</option>
      </select></label>
    <span class="fmsg" id="model-note"></span>
  </div>
  <div class="frow" id="lobe-row" hidden>
    <label class="f f-sm"><span>Near lobe depth</span>
      <input type="number" id="d-lobe-near" step="0.1" min="0.5" max="30" value="3.5"></label>
    <label class="f f-sm"><span>Far lobe depth</span>
      <input type="number" id="d-lobe-far" step="0.1" min="0.5" max="30" value="6"></label>
  </div>
  <div class="frow" id="uniform-row" hidden>
    <label class="f f-sm"><span>Depth throughout</span>
      <input type="number" id="d-uniform" step="0.1" min="0.5" max="30" value="4"></label>
  </div>
  <div class="frow" id="ramp-row">
    <label class="f f-sm"><span>Shallow end</span>
      <input type="number" id="d-shallow" step="0.1" min="0.5" max="20" value="3.5"></label>
    <label class="f f-sm"><span>Deep end</span>
      <input type="number" id="d-deep" step="0.1" min="0.5" max="30" value="6"></label>
  </div>
  <p class="sub" style="margin:8px 0 0">The floor is taken to fall steadily from one end
  to the other. That single assumption is all the depth model needs, because the shape
  supplies the rest: the outline is sliced across its long axis and each slice
  contributes its own real surface area at its own depth. A wide shallow end therefore
  pulls the average down on its own, without anyone having to estimate what share of the
  water is shallow.</p>
  <details class="sub-adv"><summary>Flat sections, if the floor is not a plain ramp</summary>
    <div class="frow" style="margin-top:10px">
      <label class="f f-sm"><span>Flat at shallow end <i>% of length</i></span>
        <input type="number" id="f-shallow" step="5" min="0" max="100" value="0"></label>
      <label class="f f-sm"><span>Flat at deep end <i>% of length</i></span>
        <input type="number" id="f-deep" step="5" min="0" max="100" value="0"></label>
    </div>
  </details>

  <h3 class="fh">Steps and a tanning ledge in the pool</h3>
  <div class="frow">
    <label class="f f-sm"><span>Their footprint</span>
      <input type="number" id="ledge-sqft" step="5" min="0" value="0" placeholder="sq ft"></label>
    <label class="f f-sm"><span>Average depth there</span>
      <input type="number" id="ledge-depth" step="0.1" min="0" value="1.2"></label>
  </div>
  <p class="sub" style="margin:8px 0 0">Their footprint is removed from the body and
  added back at its own shallow depth, rather than shaved off as a percentage. A ledge
  is often 60&ndash;80 sq ft at ten inches; counted as pool floor it is water you do not
  have, and that error only runs one way.</p>

  <p class="sub" style="margin:14px 0 0">Measuring a spa? It has its own section
  below &mdash; it is a different shape of problem and gets its own answer.</p>


  <div class="result" id="shape-result"><span class="ph">Trace the outline and set the
  scale to see a volume.</span></div>
  <!-- THE ANSWER LANDED AND SAID NOTHING. This block is the whole point of
       the calculator and it renders roughly 1180px down a panel that does not
       itself scroll, so scrollIntoView could leave it off screen; focus stayed
       on <body>, and with no live region on the page nothing announced it
       either. Focus is MOVED here by showVerdictIn() instead of announced,
       because the block is a dozen figures with their own headings — a
       role="status" would read all of it aloud as one sentence, where landing
       in it lets the reader walk it. tabindex="-1" makes it a focus target
       without putting it in the tab order. -->
  <div class="verdict" id="shape-verdict" tabindex="-1" role="group"
       aria-label="The volume this outline works out to" hidden></div>

  <div class="factions">
    <button type="button" class="btn primary pure" id="shape-calc" disabled>Calculate the volume</button>
    <span class="fmsg need" id="shape-need">Trace the waterline, then set the scale.</span>
    {"" if public else '<button type="button" class="btn" id="shape-save" disabled>Use it as the pool volume</button>'}
    <span class="fmsg" id="shape-msg"></span>
  </div>
  <div class="offline-note"><p>The volume is calculated here either way — that
  runs on the server and needs no sign-in. <b>Saving it as this pool's volume</b>
  is what needs the write API: sign in, or start it locally with
  <code>bin/serve</code>.</p></div>
  </div>
</details>'''

def design_panel(public=False):
    """Pick a stock design and give it dimensions.

    The third way to get a volume, for the common case of knowing "it is a
    sixteen by thirty-two kidney" and having no usable overhead photograph. The
    designs are outlines, not formulas, so this route joins the traced one
    immediately and inherits every bit of the shape handling.
    """
    cards = []
    for key, tpl in TPL.TEMPLATES.items():
        pts = tpl["pts"]
        d = " ".join(("M" if i == 0 else "L") + f"{4 + x * 92:.1f},{4 + (1 - y) * 52:.1f}"
                     for i, (x, y) in enumerate(pts)) + " Z"
        cards.append(
            f'<button type="button" class="design" data-design="{key}" aria-pressed="false">'
            f'<svg viewBox="0 0 100 60" aria-hidden="true">'
            f'<path d="{d}" /></svg>'
            f'<span class="d-name">{esc(tpl["name"])}</span>'
            f'<span class="d-fill">{TPL._unit_area(pts):.0%} of its box</span></button>')

    return f'''
<details class="tool" id="design-tool">
  <summary>Pick a standard design and give it dimensions</summary>
  <div class="tool-body">
  <p class="sub">For when you know the shape and the size but have no overhead
  photograph. Each design is stored as an <b>outline</b>, not a formula — scaling it
  by your two dimensions produces the same kind of shape the tracing tool produces,
  so it goes down identical code from there: the same long-axis detection, the same
  slicing, the same area-weighted depth.</p>
  <p class="sub">The percentage under each is how much of its bounding box the shape
  actually fills. That is the honest version of the trade's "free-form factor" —
  measured from the outline rather than remembered as 0.85.</p>

  <div class="designs">{"".join(cards)}</div>
  <p class="d-note" id="design-note">Pick a design to start.</p>

  <div class="frow">
    <label class="f f-sm"><span>Length</span>
      <input type="number" id="t-length" step="0.5" min="4" max="200" value="32"></label>
    <label class="f f-sm"><span>Width</span>
      <input type="number" id="t-width" step="0.5" min="4" max="200" value="16"></label>
    <label class="f f-sm"><span>Shallow end</span>
      <input type="number" id="t-shallow" step="0.1" min="0.5" max="20" value="3.5"></label>
    <label class="f f-sm"><span>Deep end</span>
      <input type="number" id="t-deep" step="0.1" min="0.5" max="30" value="6.5"></label>
  </div>
  <div class="frow">
    <label class="f f-sm"><span>Steps / ledge <i>sq ft</i></span>
      <input type="number" id="t-ledge" step="5" min="0" value="0"></label>
    <label class="f f-sm"><span>Depth there</span>
      <input type="number" id="t-ledge-d" step="0.1" min="0" value="1.2"></label>
    <label class="f f-sm"><span>Round spa <i>diameter, ft</i></span>
      <input type="number" id="t-spa-dia" step="0.5" min="0" value="0"></label>
    <label class="f f-sm"><span>Spa depth</span>
      <input type="number" id="t-spa-depth" step="0.1" min="0" value="3.5"></label>
  </div>

  <div class="result" id="design-result"><span class="ph">Pick a design and set the
  dimensions.</span></div>
  <div class="verdict" id="design-verdict" tabindex="-1" role="group"
       aria-label="The volume this design works out to" hidden></div>
  <div class="factions">
    <button type="button" class="btn primary pure" id="design-calc" disabled>Calculate the volume</button>
    {"" if public else '<button type="button" class="btn" id="design-save" disabled>Use it as the pool volume</button>'}
    <span class="fmsg" id="design-msg"></span>
  </div>
  <div class="offline-note"><p>The volume is calculated here either way — that
  runs on the server and needs no sign-in. <b>Saving it as this pool's volume</b>
  is what needs the write API: sign in, or start it locally with
  <code>bin/serve</code>.</p></div>
  </div>
</details>'''

# Which circuit each sample column reports, so the buttons show what is actually
# true rather than what was last asked for.
STATE_COL = {d: c for d, c in CMD.STATE_COLUMN.items() if c}

def control_panel():
    """The pool controls.

    Two things this deliberately does not do. It does not show a switch as ON
    because somebody pressed it — state comes from the last sample the panel
    actually reported, so a command that silently failed reads as not having
    happened. And it does not offer a control the agent would refuse: the
    buttons are built from commands.catalogue(), so the page cannot present
    something that gets rejected on arrival.
    """
    samples = rows("samples.csv")
    last = newest(samples, "ts")
    age = ago(last.get("ts")) if last.get("ts") else "never"
    fresh = False
    d = when(last.get("ts"))
    if d:
        fresh = (dt.datetime.now() - d).total_seconds() < 2400

    cat = CMD.catalogue()
    # NOT read from QUEUE here any more.
    #
    # Whether an agent is connected is live state held by the SERVING process,
    # and the page is frequently rendered by a DIFFERENT one — `bin/render` from
    # cron, or `docker compose exec ... render`, each with its own empty
    # in-memory queue. Those always reported zero agents and baked "No agent is
    # connected" into a page served by a process that had one. It is also stale
    # the instant the agent reconnects, which happens on every deploy.
    #
    # So the banner starts as "checking" and the browser asks /api/health, which
    # is answered by the process that actually holds the connection.

    def sw(dev, spec):
        col = STATE_COL.get(dev)
        on = str(last.get(col, "")) == "1" if col else None
        state = ("on" if on else "off") if col else "unknown"
        known = col is not None and last.get(col, "") != ""
        why = spec.get("why")
        # data-state drives BOTH the card and its pill, so the border and the
        # word can never disagree — which they could when each read the same
        # boolean separately and one of them was updated later.
        st = ("on" if on else "off") if known else "unknown"
        # THE DEVICE NAME REACHED THE SCREEN AND NOT THE BUTTON.
        #
        # The label sat in a sibling <b> with nothing tying it to the controls
        # beneath it, so the browser's own name computation — measured with
        # Playwright on the rendered page — returned seven identical pairs of
        # `button "On"` / `button "Off"` with nothing distinguishing the pool
        # light from the filter pump. This is the tab that starts a gas heater;
        # "press the third Off" is not an instruction anybody should have to
        # reconstruct by counting. Two ties, because they answer different
        # questions: the card is a named GROUP, so arriving at it says which
        # circuit this is, and each button carries its own name, so reaching one
        # out of context still says what it switches. Both are built from
        # spec["label"], which is commands.label_for() — the same vocabulary the
        # agent validates against, not a second copy typed here.
        nid = f'ctl-n-{_slug(dev)}'
        lbl = esc(spec["label"])
        return f'''<div class="ctl" data-device="{key_esc(dev)}" data-state="{st}"
           role="group" aria-labelledby="{nid}">
        <div class="ctl-h">{icon("plug",15)}<b id="{nid}">{lbl}</b>
          <span class="st st-{st}">{esc(state) if known else "no reading"}</span></div>
        <div class="ctl-b">
          <button type="button" class="btn ctl-on"  data-v="1"
                  aria-label="Turn the {lbl} on">On</button>
          <button type="button" class="btn ctl-off" data-v="0"
                  aria-label="Turn the {lbl} off">Off</button>
        </div>
        {f'<p class="ctl-why">{esc(why)}</p>' if why else ''}
        <p class="ctl-cool">{spec["cooldown_s"]}s cooldown</p>
      </div>'''

    def sp(dev, spec):
        col = STATE_COL.get(dev)
        on = str(last.get(col, "")) == "1" if col else None
        # From the sample, through commands.SETPOINTS' own value_column, rather
        # than a special case for the one setpoint that happened to have a
        # column. Three of the four could be commanded and never read back, so
        # "setpoint not reported" was permanent and setting the heater to 80
        # could never be confirmed -- the panel reports all of them; nothing
        # asked. Still empty when the sample carries nothing, which is the
        # honest state for a spa heater the panel answers -999 for.
        cur = last.get(spec.get("value_column") or "", "")
        # Pre-filling with the MINIMUM when the panel does not report a setpoint
        # was a lie in the shape of a number: the box read as "the heater is at
        # 65" when the truth is "nobody here knows". Left empty, with the range
        # as a placeholder, so the field asks a question instead of answering
        # one wrongly — and the Set handler refuses an empty box rather than
        # sending it as a zero.
        # The SAME state vocabulary the switches use. These cards wore the
        # verdict pills (.p-ok / .p-unknown) instead -- a second colour language
        # for equipment state on the one screen that has both, and one with no
        # "waiting" in it at all, so a heater could never show the amber pending
        # mark that every switch beside it can. CLAUDE.md: green on, grey off,
        # amber waiting, faint unknown, "used by the control cards and the Home
        # strip alike". data-state drives the card and the badge together.
        sp_known = col is not None and last.get(col, "") != ""
        sp_st = ("on" if on else "off") if sp_known else "unknown"
        # Named the same way as the switches above, and for the same measured
        # reason: the four number boxes were announced as `spinbutton "60–104"`
        # twice, `"34–42"` and `"0–100"` — the RANGE HINT, which is the one part
        # of the field that says nothing about what it controls. Two of them
        # were the pool and spa heaters and they were indistinguishable. The
        # range stays in the name, after the device, because it is genuinely
        # useful there; it is no longer the whole of it.
        nid = f'ctl-n-{_slug(dev)}'
        lbl = esc(spec["label"])
        rng = f'{spec["min"]}–{spec["max"]}'
        return f'''<div class="ctl set" data-device="{key_esc(dev)}"
           data-state="{sp_st}" data-current="{esc(cur)}"
           role="group" aria-labelledby="{nid}">
        <div class="ctl-h">{icon("thermo",15)}<b id="{nid}">{lbl}</b>
          {f'<span class="st st-{sp_st}">{"on" if on else "off"}</span>' if col else ''}
          {f'<span class="c-note">now {esc(cur)}</span>' if cur != "" else '<span class="c-note dim">setpoint not reported</span>'}
          <!-- Setpoint progress gets its OWN badge. It used to overwrite the
               on/off one, so while a setpoint was confirming the card stopped
               answering whether the heater was running -- for up to twenty
               minutes, on the most expensive thing the pool can do. Empty and
               hidden until the page has something to say. -->
          <span class="st sp-wait" hidden></span></div>
        <div class="ctl-b">
          <input type="number" class="ctl-val" min="{spec["min"]}" max="{spec["max"]}"
                 value="{esc(cur) if cur != "" else ""}"
                 aria-label="{lbl} — new setpoint, {rng}"
                 placeholder="{spec["min"]}–{spec["max"]}" step="1">
          <button type="button" class="btn ctl-setpoint"
                  aria-label="Set the {lbl}">Set</button>
        </div>
        {f'<p class="ctl-why">{esc(spec.get("why"))}</p>' if spec.get("why") else ''}
        <p class="ctl-cool">{spec["cooldown_s"]}s cooldown &middot; {spec["min"]}–{spec["max"]}</p>
      </div>'''

    # Boost is in the agent's vocabulary and the agent WILL accept it -- it is
    # not offered here because it is not a toggle: it runs the cell hard for a
    # fixed period and then stops, so a button that looks like the others would
    # behave unlike all of them. Filtering it silently made Help's "anything not
    # on this list the agent refuses" true in one direction only, and left the
    # one control a reader might go looking for simply absent.
    boost = cat["switches"].get("SWG/Boost") or {}
    switches = "".join(sw(k, v) for k, v in cat["switches"].items()
                       if k != "SWG/Boost")
    boost_note = (
        f'<p class="sub" style="margin:10px 0 0"><b>{esc(boost.get("label", "Salt cell boost"))}</b> '
        f'is deliberately not a button here. {esc(boost.get("why") or "")} The agent '
        f'accepts it; this page does not offer it, because a control that runs for a '
        f'fixed period and stops does not belong beside switches that stay where you '
        f'put them.</p>' if boost else "")
    setpoints = "".join(sp(k, v) for k, v in cat["setpoints"].items())

    # FETCHED, NOT BAKED. This table used to be rendered from QUEUE.history() at
    # build time -- and cron renders every six hours through `docker compose
    # exec`, a different process whose queue is empty. So twice a day the page
    # erased the command history and stated "Nothing issued yet this session ...
    # it starts empty after a restart", asserting a restart that had not
    # happened, while the process actually serving the page still held every
    # command. CLAUDE.md: anything live is fetched by the browser from an
    # endpoint, never baked into the render. That rule was applied to the
    # offline banner and not to the table beside it.
    #
    # The rows arrive from /api/commands, which returns both the session ring
    # AND audit.csv -- the durable record the sentence below has always pointed
    # at and which nothing in the product could read. audit.load() had no
    # callers at all.
    # colspan FIVE. The table gained a How column and this placeholder kept
    # four, which renders as a cell short of the header and lets the browser
    # decide what to do about it.
    hrows = ('<tr><td colspan="5" class="empty">Reading the switch log…</td></tr>')
    hcount = ("Commands are appended to audit.csv on the share, which survives a "
              "restart even though the session view does not. Changes made "
              "elsewhere are read back out of samples.csv, which is the only "
              "place they are recorded at all.")

    # THE OPTIONS ARE DERIVED, which is the whole point of switchlog.devices()
    # and switchlog.HOWS. A filter whose menu is typed here offers a circuit
    # the derivation never looks at — the reader picks it, gets an empty table,
    # and concludes that circuit has never been touched. The menu and the
    # values the rows carry have to be one set.
    #
    # Rendered server-side rather than built in the page script, for the same
    # reason the two control banners are: wording in one language.
    f_device = '<option value="">every circuit</option>' + "".join(
        f'<option value="{key_esc(d)}">{esc(lbl)}</option>'
        for d, lbl in switchlog.devices())
    f_how = ('<option value="">poolhound and the panel</option>'
             + "".join(f'<option value="{key_esc(h)}">{esc(h)}</option>'
                       for h in switchlog.HOWS))
    f_days = "".join(
        f'<option value="{key_esc(v)}"{" selected" if v == "30" else ""}>'
        f'{esc(lbl)}</option>' for v, lbl, _ in switchlog.WINDOWS)

    # Two banners, both present, both hidden until the live check says which
    # applies. Rendering the text now and revealing it later keeps the wording
    # out of the JavaScript, where it would be a second copy to keep in step.
    banner = (f'<div class="v-warn ctl-offline" id="ctl-offline" hidden '
              f'style="margin-bottom:14px">{icon("alert",15)} '
              f'<b>No agent is connected</b>, so nothing here can reach the panel. '
              f'The pool carries on running its own schedule — the controls are the '
              f'thing that is unavailable, not the pool.</div>'
              f'<div class="sub ctl-checking" id="ctl-checking" '
              f'style="margin-bottom:14px">Checking whether the pool agent is '
              f'connected&hellip;</div>')
    # Staleness is a DIFFERENT fact from connectedness and both can be true at
    # once — an agent that just reconnected after an outage is connected and its
    # last reading is hours old. Appended rather than assigned, because
    # overwriting meant the reader was told one of the two things that were
    # wrong and never the other.
    if not fresh and last:
        # THE RENDER MUST NOT CLAIM A COMMAND WILL BE DELIVERED. Whether an
        # agent is connected is a LIVE fact the browser fetches; this banner is
        # baked at build time and cannot know it. So when the agent was down —
        # which is the common case, because a down agent is WHY the readings
        # are stale — the page said nothing could reach the panel and, one line
        # below, promised the command would arrive anyway. (Described rather
        # than quoted: a check asserts that promise is absent from this file,
        # and a comment repeating it would fail the check that keeps it gone.)
        #
        # The durable half of the fact is stated here; the live half is left to
        # the script, which already reads agents from /api/health for the
        # offline banner above. When no agent is connected that banner has
        # already said so and this one adds nothing.
        banner += (f'<div class="v-warn" style="margin-bottom:14px">{icon("alert",15)} '
                   f'The last reading from the panel was {esc(age)}, so the states '
                   f'below may be stale.<span class="ctl-stale-live"></span></div>')

    return f'''
<p class="sub helpto"><a href="#help" data-tab-link="help">How a command reaches the panel →</a></p>
<section id="ctl-circuits">
  <h2>Circuits</h2>
  <p class="sub">What each switch shows is the last state the panel <b>reported</b>,
  not what was last asked of it — so a command that quietly failed reads as not having
  happened. Last heard from the panel {esc(age)}.</p>
  {banner}
  <div class="card"><div class="ctlgrid">{switches}</div>{boost_note}</div>
</section>

<section id="ctl-setpoints">
  <h2>Setpoints</h2>
  <p class="sub">Ranges and cooldowns come from the agent, not from this page, so
  nothing offered here can be refused on arrival for being out of bounds.</p>
  <div class="card"><div class="ctlgrid">{setpoints}</div></div>
</section>

<section id="ctl-actions">
  <h2>Everything off</h2>
  <p class="sub">Shuts the circuits in order and leaves the filter pump until last —
  killing the pump first strands anything still running on it.</p>
  <div class="card">
    <div class="factions">
      <button type="button" class="btn" id="ctl-alloff">{icon("check",14)} Turn everything off</button>
      <button type="button" class="btn" id="ctl-resync">{icon("refresh",14)} Resync the panel</button>
      <span class="fmsg" id="ctl-msg"></span>
    </div>
  </div>
</section>

<section id="ctl-log">
  <h2>What has been switched</h2>
  <p class="sub">Every circuit that changed state, whoever changed it — through
  poolhound, at the panel, or through AqualinkD — with every command poolhound
  issued and what the agent said back, refusals included.</p>
  <p class="sub">The panel keeps no log, so a change made anywhere but here is
  known only from the agent's fifteen-minute sample: the time is <b>when it was
  first reported</b>, not when somebody pressed it, and a circuit switched on
  and off again between two samples leaves no trace at all. Rows poolhound
  issued carry their own exact time.</p>
  <p class="sub">There are three other ways to switch a circuit here. Somebody
  at the <b>wall panel</b> reaches the bus directly and leaves nothing behind
  but the next sample — that one is genuinely undetectable. <b>AqualinkD's own
  web interface</b> and the <b>poolhound local</b>, the optional page on the Pi, which drives AqualinkD
  through a reverse proxy, are each an HTTP request, and a page that says so
  <b>is named here</b>: a door on the allowlist can claim a change, and the
  claim appears above as <code>door:</code> with its own timestamp rather than
  the sample's. A change nothing claimed is reported as the panel or AqualinkD
  together, because without a claim the two look alike from this end.</p>
  <p class="sub">A door's claim is a claim, not proof — it arrives from an
  unauthenticated page on the house network, so it is recorded under a
  <code>door:</code> prefix that cannot be mistaken for a person. Neither
  AqualinkD nor the proxy logs requests on its own: the proxy discards its
  access log and AqualinkD runs at <code>NOTICE</code>, because a steady drip
  of small writes is what destroyed this Pi's first two SD cards. Turning
  either on is a decision about card life.</p>
  <div class="card">
    <div class="frow" role="group" aria-label="Filter the log">
      <label class="flbl" for="ctl-f-device">Circuit</label>
      <select id="ctl-f-device" class="fsel">{f_device}</select>
      <label class="flbl" for="ctl-f-how">Origin</label>
      <select id="ctl-f-how" class="fsel">{f_how}</select>
      <label class="flbl" for="ctl-f-days">When</label>
      <select id="ctl-f-days" class="fsel">{f_days}</select>
    </div>
    <div class="scroll"><table>
    <thead><tr><th>When</th><th>What</th><th>How</th><th>By</th><th>Result</th></tr></thead>
    <tbody id="ctl-hist">{hrows}</tbody></table></div>
    <div class="frow" style="margin-top:12px">
      <button type="button" class="btn" id="ctl-prev">Newer</button>
      <button type="button" class="btn" id="ctl-next">Older</button>
      <span class="sub" id="ctl-page" aria-live="polite"></span>
    </div>
  </div>
  <p class="sub" style="margin-top:8px" id="ctl-hist-note">{hcount}</p>
</section>'''

def key_esc(v):
    return html.escape(str(v), quote=True)

def _slug(v):
    """A device id as an HTML id fragment.

    Device ids are the agent's vocabulary, not markup: `SWG/Percent` and
    `SWG/Boost` both carry a slash, which is legal in an id attribute and a
    syntax error in the CSS selector any later reader would reach for. Lowered
    and reduced to [a-z0-9-] so the aria-labelledby ties above cannot be the
    thing that breaks when a device is added.
    """
    return re.sub(r"[^a-z0-9]+", "-", str(v).lower()).strip("-") or "x"

def credentials_panel():
    """Interactive credential entry, stored encrypted.

    The alternative this replaces was a path in config.toml pointing at a
    plaintext file someone had to create with printf and remember to chmod. That
    works and is documented, but it is also why the SMTP credentials never got
    created: the friction is at exactly the moment somebody is trying to turn a
    feature on.
    """
    st = vault.status()
    where = st["key_location"]
    rows_ = []
    for key, s in st["services"].items():
        stored = s["stored"]
        # "not set" and "the vault refused to answer" are different problems
        # with different fixes -- a password to type, versus a role assignment
        # or an IMDS outage -- and they rendered identically. kv_get records the
        # reason for anything that is not a plain 404.
        # A VAULT THAT CANNOT BE READ OUTRANKS "stored". These two branches were
        # ordered the other way and the warning below was gated on `not stored`,
        # so a credential saved to the LOCAL store while Key Vault was
        # unreachable flipped the badge green and suppressed the very warning
        # that said the vault had not answered -- on a host where Key Vault is
        # checked first, so the collector would keep using the old password the
        # moment the vault came back.
        if s.get("kv_error"):
            badge = '<span class="pill p-bad">vault unreachable</span>'
        elif stored:
            badge = '<span class="pill p-ok">stored</span>'
        else:
            badge = '<span class="pill p-unknown">not set</span>'
        legacy = ""
        if s.get("kv_error"):
            legacy = (f'<span class="fmsg bad">Key Vault answered '
                      f'<code>{esc(s["kv_error"])}</code> \u2014 this is not the same as '
                      f'the credential being absent, and entering one here will not fix '
                      f'it. Check the managed identity still has get on this vault.'
                      + (' A credential saved here would be shadowed by the vault '
                         'the moment it answers again.' if stored else '')
                      + '</span>')
        if s["legacy_file"] and not stored:
            legacy = (f'<span class="fmsg">a plaintext file still exists at '
                      f'<code>{esc(s["legacy_file"])}</code></span>')
        rows_.append(f'''<div class="cred" data-service="{key}">
      <div class="c-h">{icon("plug",15)}<b>{esc(s["label"])}</b>{badge}
        <span class="c-note">{esc(s["note"])}</span></div>
      <div class="frow">
        <label class="f"><span>{esc(s["user_label"])}</span>
          <input type="text" class="c-user" autocomplete="off"
                 value="{esc(s["username"])}" placeholder="not set"></label>
        <label class="f"><span>{esc(s["secret_label"])}</span>
          <input type="password" class="c-pass" autocomplete="new-password"
                 placeholder="{'unchanged' if stored else 'not set'}"></label>
        <div class="f f-sm" style="justify-content:flex-end">
          <button type="button" class="btn primary c-save">Save</button></div>
        <!-- WHICH SERVICE, ON EVERY CONTROL THAT IS NOT INSIDE A <label>.
             Three services render three identical rows, and the two controls
             here take their meaning entirely from the heading above them. The
             browser's name computation returned three indistinguishable
             `combobox` with the same two options and three buttons reading
             "Forget", so choosing where a password is kept — or deleting one —
             was a decision made by position. The username and secret fields
             next to these are already inside a <label>; these two were not. -->
        <div class="f f-sm"><span>Store it in</span>
          <select class="c-store" aria-label="Where to keep the {esc(s["label"])} password">
            <option value="vault">the encrypted vault</option>
            <option value="file">a plain file</option>
          </select></div>
        <div class="f f-sm" style="justify-content:flex-end">
          <button type="button" class="btn c-forget"
                  aria-label="Forget the {esc(s["label"])} password"{'' if stored else ' disabled'}>Forget</button></div>
      </div>
      {legacy}
      <span class="fmsg c-msg"></span>
    </div>''')

    keyline = {
        "keychain": ('<b>The key is in your macOS keychain</b>, protected by your login '
                     'password. The encrypted file can go into a backup or a synced '
                     'folder without carrying its key with it.'),
        "file": ('<b>The key is in a 0600 file next to the vault</b>, because no keychain '
                 'was available. That is only marginally better than plaintext — anything '
                 'that can read one can read the other — so treat this as a stopgap.'),
        "none": ('No key exists yet. One is generated the first time you save a '
                 'credential, and goes into the macOS keychain.'),
        "keyvault": ('<b>The passwords are in Azure Key Vault</b> and this host holds no '
                     'key at all — it authenticates as itself through its managed '
                     'identity, and the platform vouches for it. Nothing here has to be '
                     're-keyed when the server is rebuilt, because there is nothing here '
                     'to re-key.'),
    # .get rather than [] on purpose: a key location this page has not been
    # taught about must not take the whole Settings tab down with a KeyError.
    # It renders a plain statement of fact and the rest of the page still works.
    }.get(where, f'The key location reports as <b>{esc(where)}</b>, which this page '
                 f'has no description for.')

    imp = ""
    if any(s["legacy_file"] for s in st["services"].values()):
        imp = (f'<div class="factions"><button type="button" class="btn" id="cred-import">'
               f'Import the existing plaintext files</button>'
               f'<span class="fmsg" id="cred-import-msg">They are left in place; nothing '
               f'is deleted.</span></div>')

    err = (f'<p class="v-warn">{esc(st["error"])}</p>' if st.get("error") else "")

    return f'''
<section id="set-credentials">
  <h2>Logins</h2>
  <p class="sub">The passwords the collectors use, kept in an encrypted file rather
  than as plaintext on disk. Enter them here instead of creating files by hand.</p>
  <div class="card">
    {err}
    <p class="sub" style="margin:0 0 14px">{keyline} Encrypting a file and leaving
    its key beside it protects against nothing, so what matters is not the cipher —
    it is that the key lives somewhere the operating system defends. This does not
    protect against anything running as you while that keychain is unlocked, which
    includes poolhound itself: a collector that logs in at two in the morning needs
    the password without anyone present.</p>
    {"".join(rows_)}
    {imp}
  </div>
</section>'''

def notify_panel():
    """Notification settings, on the page rather than in a file.

    These are the switches most likely to be changed on a whim — silence the
    heater alerts for a week, point mail at a different address — and making that
    mean editing TOML and remembering which key is which is how settings end up
    never being changed at all.

    The SMTP password is the one thing not here. It lives in its own file,
    referenced by path, and no form on this page will accept one.
    """
    n = CFG.get("notify", {})
    def chk(key, label, why, dflt=True):
        on = " checked" if n.get(key, dflt) else ""
        return (f'<label class="check n-row"><input type="checkbox" name="n_{key}"{on}>'
                f'<span><b>{esc(label)}</b><i>{why}</i></span></label>')
    sev = n.get("email_min_severity", "serious")
    # Say what this install actually does rather than describing Gmail at a
    # deployment that does not use it. An unauthenticated relay is the normal,
    # correct arrangement on the server and reads as a missing setting unless the
    # page says so.
    host = str(n.get("smtp_host", "") or "")
    if n.get("smtp_credentials"):
        relay_note = ('The password is never stored here or sent to this page — only the '
                      'path to the file holding it, which must be <code>chmod 600</code>. '
                      'Gmail needs an <b>app password</b> rather than your account '
                      'password; it will reject the account password outright.')
    elif host and "." not in host:
        relay_note = (f'<b>{esc(host)}</b> is a relay on this host\'s own network, which '
                      f'accepts mail from this container by address rather than by '
                      f'password. That is why there is no sign-in here, and it is the '
                      f'correct arrangement: the connection never leaves the machine, '
                      f'and there is no credential to leak or rotate.')
    else:
        relay_note = ('With no password file this will connect without signing in, which '
                      'only works if the server already trusts this host. Anything on the '
                      'public internet will refuse it. Gmail needs an <b>app password</b> '
                      'rather than your account password.')

    sevopts = "".join(
        f'<option value="{v}"{" selected" if v == sev else ""}>{esc(l)}</option>'
        for v, l in [("serious", "Only things that will not wait"),
                     ("info", "Everything, including heaters and recoveries")])

    return f'''
<section>
  <h2>Notifications</h2>
  <p class="sub">Silence is the failure mode that costs the most, because it does not
  look like one — a collector that died three weeks ago presents as a calm, healthy
  pool. These decide what interrupts you and where.</p>
  <div class="card">
    <form id="notify-form" class="form">
      <h3 class="fh">What is worth telling you about</h3>
      {chk("collectors", "A source stops reporting",
           "The Pi going quiet for two hours, or WaterGuru for thirty-six.")}
      {chk("chemistry", "Chemistry that will not wait",
           "Not merely off target — chlorine near zero, pH at 8.0 or 7.0, salt below "
           "the cell floor, or WaterGuru reporting red.")}
      {chk("pool_heat", "The pool heater comes on",
           "Gas heating is the most expensive thing this pool does, and a heater "
           "nobody asked for shows up on a bill rather than a dashboard.")}
      {chk("spa_heat", "The spa heater comes on",
           "Same cost, and the spa runs a higher cell output, so a long session "
           "moves the chemistry.")}
      {chk("spa", "The spa comes on", "Spa mode aerates hard and drives the cell higher.", False)}
      {chk("freeze", "Freeze protection starts",
           "The panel starts the pump on its own when the air gets cold enough to risk "
           "ice in the plumbing \u2014 at whatever hour that is. It is the one thing here "
           "nobody scheduled, it runs until the air warms up, and in daylight in summer "
           "it means the sensor or the setpoint is wrong.")}

      <h3 class="fh">Where they go</h3>
      {chk("desktop", "Desktop notifications", "Only reach you at this Mac.")}
      {chk("email", "Email", "Reaches you anywhere, which is the point — and also why "
                             "it is worth being stricter about what gets sent.", False)}
      <div class="frow">
        <label class="f"><span>Send to <i>several, separated by commas</i></span>
          <input type="text" name="n_email_to" value="{esc(n.get("email_to",""))}"
                 placeholder="you@example.com, someone@example.org"></label>
        <label class="f"><span>Send from <i>blank uses the SMTP username</i></span>
          <input type="email" name="n_email_from" value="{esc(n.get("email_from",""))}"
                 placeholder="optional"></label>
        <label class="f"><span>How much to email</span>
          <select name="n_email_min_severity">{sevopts}</select></label>
      </div>
      <div class="frow">
        <label class="f"><span>SMTP server</span>
          <input type="text" name="n_smtp_host" value="{esc(n.get("smtp_host","smtp.gmail.com"))}"></label>
        <label class="f f-sm"><span>Port</span>
          <input type="number" name="n_smtp_port" value="{esc(n.get("smtp_port",587))}"></label>
        <label class="check" style="align-self:end;padding-bottom:9px">
          <input type="checkbox" name="n_smtp_tls"{" checked" if n.get("smtp_tls", True) else ""}>
          <span>STARTTLS</span></label>
      </div>
      <div class="frow">
        <label class="f"><span>Password file <i>blank if the relay needs no sign-in</i></span>
          <input type="text" name="n_smtp_credentials"
                 value="{esc(n.get("smtp_credentials",""))}"
                 placeholder="leave blank for a trusted relay"></label>
      </div>
      <p class="sub" style="margin:0">{relay_note}</p>

      <div class="factions">
        <button type="submit" class="btn primary">Save notification settings</button>
        <button type="button" class="btn" id="notify-test-email">Send a test email</button>
        <button type="button" class="btn" id="notify-test-desktop">Test desktop</button>
        <span class="fmsg" id="notify-msg"></span>
      </div>
      <div class="offline-note"><p>Saving needs the write API. Start it with
      <code>bin/serve</code>.</p></div>
    </form>
  </div>
</section>'''

def reading_panel():
    """Record a test result — a drop kit, strips, or a store that is not Leslie's.

    ON THE COLLECTION TAB, not in Settings, and its own first sentence is the
    argument: entered here a result "becomes a third source alongside the two
    collectors". That is what this tab is about. It sat under Settings because
    Settings was where anything with a form went, which is how that tab came to
    hold four things you change and four you only read.
    """
    # Defaulted to now: the common case is testing the water and entering it
    # immediately, and a blank required field is one more thing to fill in.
    now = dt.datetime.now().strftime("%Y-%m-%dT%H:%M")
    return f'''
<section id="col-reading">
  <h2>Record a test result</h2>
  <p class="sub">A drop kit, test strips, or a store that is not Leslie's. Entered
  here it becomes a third source alongside the two collectors and is reconciled the
  same way they are with each other &mdash; most recent wins, per measure, never
  averaged. Leave anything you did not test blank.</p>
  <div class="card">
    <form id="reading-form" class="form">
      <div class="frow">
        <label class="f"><span>Source <i>what you tested with</i></span>
          <input type="text" name="source" value="Test kit" maxlength="40"></label>
        <label class="f"><span>Tested at</span>
          <input type="datetime-local" name="measured" value="{now}" required></label>
      </div>
      <div class="frow">
        <label class="f f-sm"><span>pH</span>
          <input type="number" name="ph" step="0.1" min="5" max="9.5"></label>
        <label class="f f-sm"><span>Free chlorine <i>ppm</i></span>
          <input type="number" name="free_cl" step="0.1" min="0" max="30"></label>
        <label class="f f-sm"><span>Total chlorine <i>ppm</i></span>
          <input type="number" name="total_cl" step="0.1" min="0" max="30"></label>
        <label class="f f-sm"><span>Alkalinity <i>ppm</i></span>
          <input type="number" name="ta" step="1" min="0" max="400"></label>
      </div>
      <div class="frow">
        <label class="f f-sm"><span>Calcium <i>ppm</i></span>
          <input type="number" name="ch" step="1" min="0" max="1500"></label>
        <label class="f f-sm"><span>Cyanuric acid <i>ppm</i></span>
          <input type="number" name="cya" step="1" min="0" max="300"></label>
        <label class="f f-sm"><span>Salt <i>ppm</i></span>
          <input type="number" name="salt" step="10" min="0" max="10000"></label>
        <label class="f"><span>Note</span>
          <input type="text" name="note" maxlength="200"
                 placeholder="anything worth remembering about this test"></label>
      </div>
      <div class="frow">
        <button type="submit" class="btn primary">Record</button>
        <span class="fmsg" id="reading-msg"></span>
      </div>
    </form>
  </div>
</section>
'''


def settings_panel():
    samples  = rows("samples.csv")
    readings = rows("readings.csv")
    leslies  = rows("leslies.csv")

    wg_state, wg_note, wg_src = _cred_status("waterguru", CFG.get("waterguru", {}).get("credentials"))
    le_state, le_note, le_src = _cred_status("leslies",   CFG.get("leslies", {}).get("credentials"))

    # How the controller is reached is a fact about the deployment, not a
    # setting — and it is the question the page was silent on. Either this host
    # polls AqualinkD directly, or an agent in the house pushes to it. Those are
    # different security postures and the page should say which one is running.
    last_sample = max((r.get("ts") for r in samples if r.get("ts")), default=None)
    fresh = bool(last_sample and when(last_sample) and
                 (dt.datetime.now() - when(last_sample)).total_seconds() < 3600)
    # ONE ANSWER, BECAUSE THERE IS ONE ARCHITECTURE. This used to branch on
    # whether an [aqualink] host was configured and say "polled directly from
    # this host" if one was — describing the workstation sampler, which is
    # deleted. A page offering two security postures when the product only has
    # one is a page that will eventually describe the wrong one.
    aq_how = ('pushed up by the agent on the Pi &mdash; this host has no route to the '
              'controller and never opens a connection toward it')
    aq_state = "ok" if fresh else ("warn" if last_sample else "missing")
    aq_note = (aq_how if fresh else
               (aq_how + " &mdash; but nothing has arrived for over an hour"
                if last_sample else aq_how + " &mdash; nothing has ever arrived"))

    # HOW OFTEN EACH LAB IS PULLED, which is the one thing this tab could state
    # and not change. The cadence used to be a line in the crontab on the host,
    # so Settings printed it and then told you to go and edit a file; it lives
    # in `[collection]` in config.toml now and poolhound/cadence.py owns it.
    # Cron ticks hourly and the due test decides, which is what made the
    # setting possible at all -- see that module's docstring.
    #
    # THE FLOOR IS SHOWN, NOT JUST ENFORCED. A control that offers a value the
    # server refuses is the defect access.ROLE_UI exists to catch, one layer
    # down: each source's list is built from ITS floor, so "every hour" is not
    # in the WaterGuru dropdown to be picked and rejected. The reason is
    # printed beside it, because a limit with no reason reads as an arbitrary
    # one and gets worked around.
    from . import cadence as _cad

    def _sentence(t):
        """Raise the first letter and leave the rest alone.

        NOT str.capitalize(), which lowercases everything after the first
        character and turned "WaterGuru asks for no more than two calls a day"
        into "Waterguru" on the page -- the vendor's own name, misspelled in
        the sentence explaining their limit. Caught by reading the rendered
        card, not the template.
        """
        return t[:1].upper() + t[1:]

    def _cadence_row(tool, field_every, field_hour):
        src = _cad.SOURCES[tool]
        every = _cad.every_hours(tool, CFG)
        hour = _cad.hour_utc(tool, CFG)
        opts = "".join(
            f'<option value="{h}"{" selected" if h == every else ""}>{esc(word)}'
            f'</option>'
            for h, word in _cad.CHOICES if h >= src["floor_hours"])
        # THE HOUR BOX GOES AWAY WHEN IT STOPS APPLYING, rather than sitting
        # there disabled or -- worse -- enabled and ignored. With two pulls a
        # day there is no single hour to name, and a box that accepts a value
        # nothing honours is the same lie as a schedule the page cannot change.
        hopts = "".join(
            f'<option value="{h}"{" selected" if h == hour else ""}>'
            f'{h:02d}:00 UTC</option>' for h in range(24))
        daily = every >= _cad.DAILY_HOURS
        hour_ctl = (
            f'<label class="f f-sm"><span>Not before <i>UTC</i></span>'
            f'<select name="{field_hour}">{hopts}</select></label>'
            if daily else
            f'<label class="f f-sm"><span>Not before</span>'
            f'<input value="not used at this frequency" disabled></label>')
        return f'''<div class="frow">
        <label class="f f-sm"><span>{esc(src["label"])} <i>how often</i></span>
          <select name="{field_every}">{opts}</select></label>
        {hour_ctl}
        <p class="f-note">{esc(_sentence(src["floor_why"]))}, so nothing
        shorter than {src["floor_hours"]} hours is offered or accepted.</p>
      </div>'''

    cadence_rows = (_cadence_row("wg-collect", "wg_every_hours", "wg_hour_utc") +
                    _cadence_row("leslies", "le_every_hours", "le_hour_utc"))

    # WHETHER ANYBODY HAS CHOSEN, which is a different fact from what the
    # cadence is. "Once a day because that is the default" and "once a day
    # because somebody picked it" read identically on a page that cannot tell
    # them apart, and the first invites a reader to assume the second.
    cadence_state = (
        '<p class="sub" style="margin:0 0 10px">A cadence is set here.</p>'
        if _cad.configured(CFG) else
        '<p class="sub" style="margin:0 0 10px">Nothing has been chosen, so each'
        ' source is on its default \u2014 which is the cadence its crontab was'
        ' already running, so an upgrade did not change when this pool gets'
        ' collected.</p>')

    # Defaulted to now: the common case is testing the water and entering it
    # immediately, and a blank required field is one more thing to fill in.
    now = dt.datetime.now().strftime("%Y-%m-%dT%H:%M")

    # What the download actually contains, counted rather than asserted — a
    # "download everything" that quietly ships two of nine files is the same
    # class of defect as a table that truncates without saying so.
    from . import server as _srv
    have, lines = [], 0
    for fname, what in _srv.EXPORTABLE.items():
        fp = os.path.join(config.data_dir(CFG), fname)
        if os.path.exists(fp):
            n = max(0, sum(1 for _ in open(fp)) - 1)
            have.append(f"{fname} ({n:,})")
            lines += n
    export_note = (f"{len(have)} files, {lines:,} rows: {', '.join(have)}."
                   if have else "Nothing recorded yet — there is nothing to download.")

    sanit = POOL.get("sanitiser", "salt_cell")
    sopts = "".join(f'<option value="{v}"{" selected" if v == sanit else ""}>{esc(l)}</option>'
                    for v, l in [("salt_cell", "Salt cell"), ("liquid", "Liquid chlorine"),
                                 ("tablet", "Tablets / trichlor")])
    aopts = "".join(f'<option value="{v}"{" selected" if abs(float(POOL.get("acid_pct", 31.45)) - v) < .01 else ""}>{v:g}%</option>'
                    for v in (31.45, 14.5))

    # Through the accessors, which answer None when nobody has said — the same
    # distinction config.volume() draws, and for the same reason: a rating and a
    # tariff both multiply straight through into a figure somebody acts on.
    PUMP_W = config.pump_watts(CFG)
    KWH = config.kwh_cost(CFG)

    # THERE IS NOTHING TO SET HERE ANY MORE, and there has not been since the
    # move to the server. These two boxes configured the address of the workstation
    # poller, which is now deleted; the Pi's agent reaches AqualinkD over its
    # own loopback and takes the address from POOLHOUND_AQUALINK on its own
    # install, and has never read this section. They were left visible and
    # disabled so the setting would not be a mystery -- which was the right
    # call while the poller still existed and somebody might have been using
    # it. A disabled box for a program that is gone is just a question.
    AQ_NOTE = ('<div class="v-note" style="margin:0 0 10px">'
               'Nothing is configured here. The Pi samples the panel over its '
               'own loopback and pushes the readings out to this server, which '
               'has no route back to the controller by design \u2014 the panel has '
               'no password of its own, so nothing may reach toward it. How '
               'often it arrives, and whether it has, is on the Collection '
               'tab.</div>')

    # Reported, not editable here. A form that can grant admin is a form that
    # can be used to grant admin, and the one identity that must never depend on
    # the policy is the one editing it — so the policy lives in config.toml,
    # which needs access to the host or the share rather than a browser session.
    # Saying which state this install is in matters either way: "everyone who
    # gets past the proxy is an admin" is a real choice and it should be read as
    # one, not discovered.
    _acc = CFG.get("access") or {}
    if access.configured(CFG):
        _rows = "".join(
            f'<li><b>{esc(lvl)}</b> \u2014 ' +
            (", ".join(esc(x) for x in (_acc.get(key) or [])) or "<i>nobody</i>") + "</li>"
            for lvl, key in (("admin", "admins"), ("operate", "operators"),
                             ("view", "viewers")))
        _dflt = esc(str(_acc.get("default") or "view"))
        ACCESS_NOTE = (
            '<p class="sub" style="margin:0 0 8px">A policy is set. Anyone the '
            'proxy admits who is not named below gets <b>' + _dflt + '</b>.</p>'
            '<ul class="sub" style="margin:0 0 10px 18px">' + _rows + '</ul>'
            '<p class="sub" style="margin:0">Edit <code>[access]</code> in '
            '<code>config.toml</code> to change it. It is not editable here on '
            'purpose: a form that can grant administrator access is a form that '
            'can be used to grant it.</p>')
    else:
        ACCESS_NOTE = (
            '<div class="v-warn" style="margin:0 0 10px">'
            '<b>No access policy is set</b>, so everyone your sign-in admits has '
            'full control \u2014 the heaters, the dose log, these settings and the '
            'credential store. That is the right answer when the proxy admits '
            'exactly your household, and the wrong one if it is a shared sign-in '
            'or a group that can grow.</div>'
            '<p class="sub" style="margin:0">Add an <code>[access]</code> section to '
            '<code>config.toml</code> to change it \u2014 <code>admins</code>, '
            '<code>operators</code> and <code>viewers</code>, each a list of the '
            'addresses your sign-in passes through. See '
            '<code>config/config.example.toml</code>.</p>')

    return (f'''
<p class="sub helpto"><a href="#help" data-tab-link="help">What each of these feeds →</a></p>
<p class="lede">Everything the collectors and the chemistry need to know about this
pool. Saving writes <code>config/config.toml</code>, which is gitignored — it is the
file that keeps one household's addresses and volumes out of a repository meant to
be published.</p>

<section>
  <h2>The pool</h2>
  <p class="sub">Volume is the input every dose calculation multiplies through, so an
  error here scales every recommendation on the Chemicals and Pool chemistry tabs by
  the same factor.</p>
  <div class="card">
    <form id="settings-form" class="form" data-settings>
      <div class="frow">
        <label class="f f-sm"><span>Total volume <i>what doses use</i></span>
          <input type="number" name="volume_gallons" step="100" min="100"
                 value="{f"{GAL:.0f}" if GAL_SET else ""}"
                 placeholder="not measured yet" required></label>
        <label class="f f-sm"><span>Pool alone <i>measured</i></span>
          <input type="number" value="{POOL.get("pool_only_gallons", "") or ""}" disabled
                 placeholder="not measured yet"></label>
        <label class="f f-sm"><span>Spa alone <i>measured</i></span>
          <input type="number" value="{POOL.get("spa_gallons", "") or ""}" disabled
                 placeholder="none traced"></label>
        <label class="f"><span>Sanitiser</span><select name="sanitiser">{sopts}</select></label>
        <label class="f f-sm"><span>Default acid strength</span>
          <select name="acid_pct">{aopts}</select></label>
      </div>
      <label class="check"><input type="checkbox" name="spa_shares_water"
        {"checked" if POOL.get("spa_shares_water", True) else ""}>
        <span><b>The spa shares water with the pool</b><i>True for a spillover spa:
        the two circulate together, so every dose is diluted by both and the
        chemistry uses the combined volume. Turn it off for a spa isolated behind a
        valve — the figures stay separate either way, so this can change later
        without re-tracing anything.</i></span></label>
      <label class="check"><input type="checkbox" name="volume_estimated"
        {"checked" if POOL.get("volume_estimated", True) else ""}>
        <span>Volume is an estimate — say so wherever a number depends on it</span></label>
      <p class="sub" style="margin:10px 0 0">Marking it estimated does not change any
      arithmetic; it changes what the pages claim. A dose computed from a guessed
      volume is a starting point to be corrected by the measured response, and the
      page should say so rather than present it as exact.</p>

      <h3 class="fh">Who may do what</h3>
      {ACCESS_NOTE}

      <h3 class="fh">Power</h3>
      <p class="sub" style="margin:0 0 10px">Both optional, and both blank until
      somebody fills them in. The rating turns measured pump hours into an
      estimated energy figure that the page labels as estimated; the tariff turns
      that into money. Leave either empty and the page reports hours and says why
      there is no further figure, rather than inventing one. Clearing a field
      withdraws it.</p>
      <div class="frow">
        <label class="f f-sm"><span>Pump rating <i>watts, from its label</i></span>
          <input type="number" name="pump_watts" step="10" min="1" max="10000"
                 value="{f"{PUMP_W:.0f}" if PUMP_W else ""}"
                 placeholder="not entered"></label>
        <label class="f f-sm"><span>Electricity <i>per kWh</i></span>
          <input type="number" name="kwh_cost" step="0.001" min="0.001" max="99"
                 value="{f"{KWH:.3f}".rstrip("0").rstrip(".") if KWH else ""}"
                 placeholder="not entered"></label>
      </div>
      <p class="sub" style="margin:0 0 4px">If the pump reports its own wattage to
      the controller the measured figure is used instead and the rating is ignored
      — a variable-speed pump throttled to half speed draws far less than its
      label, so the measurement is always the better number when there is one.</p>

      <h3 class="fh">Chemical prices</h3>
      <p class="sub" style="margin:0 0 10px">What you pay, for the cost column of
      the upkeep table on the Chemicals tab. Left empty, a price falls back to the
      typical retail figure shown in the box, and the table says so beside every
      cost it priced that way. Acid is per gallon at the strength set above.</p>
      <div class="frow" style="align-items:flex-end">{_price_inputs()}</div>

      <h3 class="fh">Pool controller</h3>
      {AQ_NOTE}

      <div class="factions">
        <button type="submit" class="btn primary">Save settings</button>
        <span class="fmsg" id="settings-msg"></span>
      </div>
    </form>
    <p class="sub" style="margin:14px 0 0">To work the volume out from a photograph
    or from a stock design, use the <b>Pool volume</b> tab. The tools live there
    rather than here because they are useful to anybody with a pool, not only to
    somebody who can sign in to this one.</p>
    <form style="display:none">
      <div class="offline-note">
        <p>The write API is not running, so this form cannot save. Start it with
        <code>bin/serve</code>, or edit <code>config/config.toml</code> directly.</p>
      </div>
    </form>
  </div>
</section>

<section id="settings-collection">
  <h2>How often the labs are checked</h2>
  <p class="sub">The two pulls are somebody else&rsquo;s service, so how often they
  happen is a real choice with a real cost. The controller is not here: the Pi
  samples the panel and pushes, and how often it does that is the Pi&rsquo;s to
  decide, not this server&rsquo;s.</p>
  <div class="card">
    {cadence_state}
    <form id="cadence-form" class="form" data-settings>
      {cadence_rows}
      <div class="factions">
        <button type="submit" class="btn primary">Save</button>
        <span class="fmsg" id="cadence-msg"></span>
      </div>
    </form>
    <p class="sub" style="margin:12px 0 0">Saved here and applied on the next
    hourly tick &mdash; nothing to restart and no file to edit. <b>Whether each
    one actually ran</b>, and when it is next due, is on the
    <a href="#collection" data-tab-link="collection">Collection tab</a>; to fetch
    one right now regardless of the cadence, use the Refresh control.</p>
  </div>
</section>

@@CREDS@@
@@NOTIFY@@
@@TARGETS@@
<section id="settings-export">
  <h2>Your data</h2>
  <p class="sub">Everything poolhound has recorded about this pool, as the CSV files
  it stores. Not a report and not a summary — the actual record, so it stays readable
  without this program. The individual tables on Home each carry their own download
  beside the row count.</p>
  <div class="card">
    <p style="margin:0 0 12px"><a class="btn primary dt-get" href="/api/export" download>Download
    everything</a></p>
    <p class="sub" style="margin:0">{export_note}</p>
  </div>
</section>
''').replace("@@CREDS@@", credentials_panel()).replace("@@NOTIFY@@", notify_panel()).replace("@@TARGETS@@", targets_panel())

def spa_panel(public=False):
    """The spa, measured on its own terms.

    Its own tool rather than a diameter box bolted to the pool calculator,
    because a spa is not a small pool. It is one depth throughout instead of a
    slope, and it has a bench around the inside that people sit on — which
    removes a third of the water they expect and is the single commonest reason
    a spa volume comes out "wrong". Giving the bench its own inputs, and showing
    what it cost, is what makes that checkable instead of mysterious.

    The shapes are manufactured rather than excavated, so the list is different
    from the pool's: round, octagonal and rounded-square cover nearly everything
    that is not a gunite spillover. A lagoon-shaped spa does not exist.
    """
    # Drawn, like the pool designs. A list of names and percentages made the
    # reader translate "octagon, 83% of its box" into a picture in their head,
    # next to a pool picker that simply showed them. Same viewBox and same path
    # construction, so the two sets of cards cannot drift apart visually.
    cards = []
    for k, v in TPL.spa_as_json().items():
        pts = v["pts"]
        d = " ".join(("M" if i == 0 else "L") + f"{4 + x * 92:.1f},{4 + (1 - y) * 52:.1f}"
                     for i, (x, y) in enumerate(pts)) + " Z"
        cards.append(
            f'<button type="button" class="design spa-design" data-spa-design="{esc(k)}" '
            f'aria-pressed="false">'
            f'<svg viewBox="0 0 100 60" aria-hidden="true"><path d="{d}" /></svg>'
            f'<span class="d-name">{esc(v["name"])}</span>'
            f'<span class="d-fill">{v["unit_area"]*100:.0f}% of its box</span></button>')
    cards = "".join(cards)

    return f'''
<!-- Closed by default, like every other tool here. Most pools have no spa, and
     an expanded shell-picker below the pool made the page look like it was
     asking for two measurements when it only needs one. The pool's own tools
     are closed too; opening one is how you say which route you are taking. -->
<details class="tool" id="spa-design-tool">
  <summary>Pick a standard shell and give it dimensions</summary>
  <div class="tool-body">
  <div class="designs">{cards}</div>
  <p class="d-note" id="spa-note">Pick a shell to start, or trace one on the
  photograph above using its <b>Spa outline</b> button.</p>

  <div class="frow">
    <label class="f f-sm"><span>Across <i>ft, or diameter</i></span>
      <input type="number" id="s-length" step="0.5" min="0" max="40" value="7"></label>
    <label class="f f-sm"><span>The other way <i>ft</i></span>
      <input type="number" id="s-width" step="0.5" min="0" max="40" value="7"></label>
    <label class="f f-sm"><span>Water depth <i>ft</i></span>
      <input type="number" id="s-depth" step="0.1" min="0" max="8" value="3.5"></label>
  </div>
  <div class="frow">
    <label class="f f-sm"><span>Bench width <i>in, 0 if none</i></span>
      <input type="number" id="s-bench-w" step="1" min="0" max="48" value="16"></label>
    <label class="f f-sm"><span>Bench below water <i>in</i></span>
      <input type="number" id="s-bench-d" step="1" min="0" max="36" value="16"></label>
    <span class="f f-sm fmsg" style="align-self:end;padding-bottom:9px">A bench
    around the inside is normal and takes real volume with it.</span>
  </div>

  </div>
</details>

<details class="tool" id="spa-photo-tool">
  <summary>Measure it from a photograph</summary>
  <div class="tool-body">
  <p class="sub" style="margin:0 0 10px">Use the pool's photograph if the spa is in
  it, or a picture of its own &mdash; a spa that sits away from the pool will not be
  in the same frame, and forcing one shot to hold both is how you end up with a spa
  eleven pixels across. A traced rim wins over a shell: if you trace one, that is
  what gets calculated.</p>
  <div class="f f-sm" style="flex-direction:row;gap:8px;flex-wrap:wrap;align-items:center">
    <button type="button" class="btn" id="spa-use-pool-photo">Use the pool&rsquo;s photograph</button>
    <label class="btn" style="margin:0">Upload a different one
      <input type="file" id="spa-file" accept="image/*" hidden></label>
    <span class="fmsg" id="spa-photo-msg"></span>
  </div>
  <div class="canvas-wrap" id="spa-stage" hidden>
    <div class="f f-sm" style="flex-direction:row;gap:6px;flex-wrap:wrap">
      <button type="button" class="btn mode" id="spa-mode-trace" aria-pressed="true">Trace the spa</button>
      <button type="button" class="btn mode" id="spa-mode-scale" aria-pressed="false">Set the scale</button>
      <button type="button" class="btn" id="spa-undo">Undo point</button>
      <button type="button" class="btn" id="spa-clear">Clear</button>
      <label class="f f-sm"><span>That distance is <i>ft</i></span>
        <input type="number" id="spa-scale-ft" step="0.1" min="0" style="width:90px"></label>
    </div>
    <p class="hint" id="spa-hint">Click around the rim of the spa.</p>
    <p class="sub" style="margin:0 0 8px">There is no <b>Find the water for me</b>
    here on purpose. That works by colour, and a small pale spa usually will not
    separate from wet deck at this scale &mdash; it would fail more often than it
    worked. A rim is a dozen clicks.</p>
    <canvas id="spa-canvas" width="900" height="600"></canvas>
  </div>

  <div class="result" id="spa-result"><span class="ph">Pick a shell and set the
  measurements.</span></div>
  <div class="verdict" id="spa-verdict" tabindex="-1" role="group"
       aria-label="The volume this spa works out to" hidden></div>
  <div class="factions">
    <button type="button" class="btn primary pure" id="spa-calc" disabled>Calculate the spa</button>
    <span class="fmsg need" id="spa-need">Pick a shell, or trace one on a photograph.</span>
    {"" if public else '<button type="button" class="btn" id="spa-save" disabled>Use it as the spa volume</button>'}
    <span class="fmsg" id="spa-msg"></span>
  </div>
  </div>
</details>'''

ACTION_NAMES = {
    "control": "A control was operated",
    "control.alloff": "Everything was switched off",
    "dose.edit": "A logged dose was edited",
    "dose.delete": "A logged dose was deleted",
    "reading.manual": "A reading was entered by hand",
    "credential": "A credential was set or forgotten",
    "settings": "Settings were saved",
}

def audit_panel():
    """The durable record of who changed what, on the page that promises it.

    audit.py is written from six places and, until this panel, was read from
    none: `audit.load()` had zero callers anywhere in the product, the export
    manifest called audit.csv "Who changed what", and the only route to it was
    downloading a zip and opening a CSV. A record nobody can read is a record
    nobody can be held to.

    Rendered rather than fetched, unlike the command history beside it, and the
    difference is the point: the command ring is in-memory state belonging to
    ONE process, so a render from cron has none of it. audit.csv is a file on
    the share -- the same kind of thing samples.csv is -- so reading it at build
    time is reading data, not guessing at another process's memory. Every write
    re-renders, so it is current the moment anything lands in it.
    """
    from . import audit
    try:
        entries = audit.load(CFG)
    except Exception:
        entries = []

    if not entries:
        table = ('<div class="dtable-empty"><b>What was done</b>'
                 '<span>nothing recorded yet — this fills as commands, doses, '
                 'readings and settings are written</span></div>')
    else:
        # audit.load() is newest-first and data_table() reverses what it is
        # given, so it goes in oldest-first and comes out newest-first once.
        table = data_table(
            "What was done",
            [{"When": (e.get("at") or "")[:16].replace("T", " "),
              "By": (e.get("by") or "").split("@")[0] or "—",
              "What": ACTION_NAMES.get(e.get("action") or "",
                                       e.get("action") or ""),
              "Which": e.get("object") or "",
              "Detail": e.get("detail") or ""}
             for e in reversed(entries)],
            ["When", "By", "What", "Which", "Detail"],
            source="audit.csv")

    return f'''
<section id="set-audit">
  <h2>What was done</h2>
  <p class="sub">Every command, dose edit, hand-entered reading, credential and
  settings change, with who made it. <b>Append-only</b> — nothing here can be edited
  or removed from inside the product, which is the only thing that makes it worth
  reading months later. Credentials themselves are never written to it: it records
  that one was set, never what it was.</p>
  <p class="sub"><b>It is not everything, and here is what it is missing.</b>
  Logging a dose and recording a lab correction are not repeated here, because each
  of those rows already carries who wrote it in the file it lives in &mdash; the
  Chemicals log and the corrections table above. Saving a volume from the Pool
  Volume Calculator is a real gap: it changes <code>config.toml</code> and appears
  in neither place. Saving the same field from the form above <em>is</em> recorded
  here.</p>
  {table}
</section>'''

def corrections_panel():
    """Drop or override a reading that is wrong.

    In Settings rather than as a control on every row of three tables: a
    correction is a rare, deliberate act with a reason attached, and putting a
    delete affordance beside ninety rows of good data invites the accident it is
    meant to fix.
    """
    from . import corrections as CORR
    existing = CORR.load()

    # Through data_table(), like every other growing table. This one showed the
    # last 12 silently -- no count, no filter, no CSV -- which is the defect
    # data_table() was written to end, left standing on a table nothing else
    # could reach. data_table() escapes its own cells, so the values go in raw.
    table = data_table(
        "Corrections applied",
        [{"Recorded": (c["at"] or "")[:10],
          "Source": CORR.SOURCES.get(c["source"], c["source"]),
          "Reading": (c["measured"] or "")[:16],
          "What": (c["action"] + (f' {c["field"]}={c["value"]}'
                                  if c["action"] == "set" else "")),
          "Why": c.get("note") or "",
          "By": (c.get("by") or "").split("@")[0]}
         for c in existing],
        ["Recorded", "Source", "Reading", "What", "Why", "By"],
        note=("Every one of these is applied each time the data is read. "
              "Nothing recorded here was deleted."),
        source="corrections.csv")
    if not existing:
        table = ('<div class="dtable-empty"><b>Corrections applied</b>'
                 '<span>nothing corrected — readings are shown exactly as the '
                 'labs reported them</span></div>')

    sopts = "".join(f'<option value="{k}">{esc(v)}</option>' for k, v in CORR.SOURCES.items())

    return f'''
<section id="set-corrections">
  <h2>A reading that is wrong</h2>
  <p class="sub">A fouled sample, a pod misreading, a test run on the spa by
  mistake. <b>Not a delete</b> &mdash; both labs return their whole history on
  every pull, so a row removed from the file is put back by the next collection.
  This records the correction and it is applied every time the data is read, to
  the tiles, the charts, the targets and the tables alike. The original is kept.</p>
  <div class="card">
    <div class="frow">
      <label class="f f-sm"><span>Which source</span>
        <select id="corr-source">{sopts}</select></label>
      <label class="f f-sm"><span>Measured <i>as shown in the table</i></span>
        <input type="text" id="corr-measured" placeholder="2026-08-08"></label>
      <label class="f f-sm"><span>Do what</span>
        <select id="corr-action">
          <option value="drop">ignore the whole reading</option>
          <option value="set">correct one value</option>
          <option value="keep">undo a previous correction</option>
        </select></label>
    </div>
    <div class="frow" id="corr-setrow" hidden>
      <label class="f f-sm"><span>Which measurement</span>
        <input type="text" id="corr-field" placeholder="ta"></label>
      <label class="f f-sm"><span>Correct value</span>
        <input type="text" id="corr-value" placeholder="74"></label>
    </div>
    <div class="frow">
      <label class="f f-wide"><span>Why <i>this is the part somebody reads in six months</i></span>
        <input type="text" id="corr-note" placeholder="sample was taken from the spa"></label>
    </div>
    <div class="factions">
      <button type="button" class="btn primary" id="corr-save">Record the correction</button>
      <span class="fmsg" id="corr-msg"></span>
    </div>
    <div style="margin-top:14px">{table}</div>
  </div>
</section>'''

def volume_panel(public=False):
    """The volume calculator as a tab in its own right.

    It was buried in Settings, which is the right place for the OWNER — the
    number it produces is a setting and belongs beside the field it fills. But
    the tool is not really about this pool at all: it turns an outline into a
    volume, which is a problem every pool owner has and almost nobody has a good
    answer to. Length x width x average depth is exact for a rectangle and wrong
    for everything else, and the trade's 0.85 fudge is a guess about a shape
    nobody measured.

    So it is public, and the two things that ARE about this pool — the saved
    outline and the button that overwrites it — are simply not in the public
    build.
    """
    if public:
        intro = ('<p class="sub helpto"><a href="#help" data-tab-link="help">'
                 'Why length times width is wrong \u2192</a></p>'
                 '<p class="lede">Work out how much water a pool actually holds, '
                 'from a photograph you trace or from a stock design and two '
                 'measurements. Nothing is uploaded or kept: the picture is '
                 'processed and discarded within the same request.</p>')
    else:
        intro = ('<p class="sub helpto"><a href="#help" data-tab-link="help">'
                 'Why length times width is wrong \u2192</a></p>'
                 '<p class="lede">Work the volume out from a photograph or a stock '
                 'design, then use it as this pool&rsquo;s volume.</p>')

    why = (
      '<section id="vol-why"><h2>Why not length times width</h2>'
      '<p>Volume is area times average depth, and of those two terms the area is '
      'both the larger and the easier to get badly wrong. A rectangle is exact. '
      'Anything with a curve in it is not: on a kidney or a free-form, length '
      'times width can overstate the water by a third, because it measures the '
      'box the pool sits in rather than the pool.</p>'
      '<p>The trade&rsquo;s answer is to multiply by 0.85. That number is a guess '
      'about a shape nobody measured, and it gets applied identically to a lagoon '
      'and a Grecian &mdash; which differ by more than twenty per cent in how much '
      'of their bounding box they actually fill. Tracing the waterline replaces '
      'the guess with the enclosed area, which is the term the answer is most '
      'sensitive to.</p>'
      '<p>Depth is treated as a gradual slope from one end to the other, which is '
      'what almost every residential pool is. Steps, benches and a spa are handled '
      'separately, because each is a volume that a single average depth gets wrong '
      'in a predictable direction.</p>'
      '<p>Why it matters: every dose on the Pool chemistry tab is parts per million, '
      'and parts per million of the wrong volume is the wrong dose. A pool assumed '
      'to be 15,000 gallons that actually holds 12,000 gets twenty-five per cent '
      'more acid than it asked for, every time.</p>'
      '</section>')

    # The running total. Placed LAST because it is the conclusion, and it stays
    # visible with dashes until something fills it in — an empty card that
    # explains what it is waiting for reads better than one that appears from
    # nowhere once you happen to press the right button.
    total = (
      '<section id="vol-sum"><h2>Pool and spa together</h2>'
      '<div class="card vt" id="vol-total">'
      '  <div class="vt-row">'
      '    <span class="vt-cell"><i>Pool</i><b data-t="pool">&mdash;</b><u>gal</u></span>'
      '    <span class="vt-op">+</span>'
      '    <span class="vt-cell"><i>Spa</i><b data-t="spa">&mdash;</b><u>gal</u></span>'
      '    <span class="vt-op">=</span>'
      '    <span class="vt-cell vt-sum"><i>Total</i><b data-t="total">&mdash;</b><u>gal</u></span>'
      '    <button type="button" class="btn vt-reset">Clear</button>'
      '  </div>'
      '  <p class="vt-note"></p>'
      # WHAT IT COSTS TO PUT THE WATER BACK.
      #
      # At the bottom, and after the volume rather than beside it: the volume
      # is the answer this tab exists for, and the cost is a thing you can do
      # with it. It stays a dash until a rate is entered — a confident $0.00
      # over an empty field is worse than nothing.
      #
      # The unit sits NEXT TO the rate because a bill quotes per CCF or per
      # 1,000 gallons, so "rate per gallon" is an invitation to be wrong by a
      # factor of a thousand. The options come from pool_shape, which owns the
      # conversion.
      '  <div class="vt-cost">'
      '    <div class="vt-cost-fig">'
      '      <span class="vt-cell"><i>Cost to refill</i>'
      '        <b data-t="cost">&mdash;</b></span>'
      '    </div>'
      '    <div class="vt-cost-rate">'
      '      <label for="vol-rate">Water rate</label>'
      '      <span class="vt-rate-in">'
      '        <span aria-hidden="true">$</span>'
      '        <input id="vol-rate" type="number" min="0" step="0.0001"'
      '               inputmode="decimal" placeholder="0.00"'
      '               aria-label="Water rate, in dollars">'
      '      </span>'
      '      <select id="vol-rate-unit" aria-label="What the rate is per">'
      '      </select>'
      '    </div>'
      '    <p class="vt-cost-note sub">Water is usually billed per CCF or per '
      '      1,000 gallons &mdash; check the unit above matches your bill. '
      '      This is the water only: it does not include sewer charges, which '
      '      some utilities bill on water used, nor the chemicals to balance '
      '      a fresh fill.</p>'
      '  </div>'
      '</div></section>')

    return (intro
            + '<section id="vol-pool"><h2>The pool</h2>'
            + '<p class="sub">Pick whichever is easier. A stock design needs two '
              'measurements; tracing a photograph needs no measurements at all beyond '
              'one known distance, and is the only one that gets a free-form right.</p>'
            + design_panel(public)
            + shape_panel(public) + '</section>'
            + '<section id="vol-spa"><h2>The spa</h2>'
            + '<p class="sub">Skip this if there is no spa. A spa is a different '
              'measurement problem: one depth throughout rather than a slope, and a '
              'perimeter bench that removes far more water than people expect. It can '
              'use the same photograph as the pool or its own, or no photograph at '
              'all &mdash; most are a manufactured shell with a diameter you can read '
              'off a tape.</p>'
            + spa_panel(public) + '</section>'
            + total
            + why)

def catalogue_json():
    # The measured alkalinity, not the 90 default: this block is what the
    # browser multiplies for every dose preview, and acid scales with TA.
    # as_json has taken a ta argument all along and no caller passed one, so
    # the page shipped "alkalinity": 90.0 while the tile above read 73.
    from .render import current_ta
    return json.dumps(CH.as_json(GAL, current_ta(), gallons_known=GAL_SET))

def templates_json():
    """Unit outlines for the browser, so the thumbnail it draws, the preview it
    computes and the volume the server records all come from one definition."""
    return json.dumps(TPL.as_json())


def refill_units_json():
    """The rate units, from pool_shape, so the browser divides by the same
    number the Python would. A CCF written as 748 in a page script is the
    second copy of a conversion that already has an owner."""
    from . import pool_shape as _ps
    return json.dumps([{"key": k, "label": lbl, "gallons": g}
                       for k, lbl, g in _ps.REFILL_RATE_UNITS])

def spa_templates_json():
    """Spa shells for the browser, kept as their own block rather than merged
    into the pool list — the two pickers must not be able to offer each other's
    shapes, and one combined object would make that a filtering bug waiting to
    happen."""
    return json.dumps(TPL.spa_as_json())

def saved_shape_json():
    """The stored outline, so it can be reloaded and adjusted rather than
    re-traced. The photograph itself is not kept — it would be megabytes in a
    JSON file — so a reload draws the outline on a plain ground and says to
    re-choose the picture if you want to re-trace against it."""
    s = pool_shape.load()
    return json.dumps(s) if s and s.get("points") else "null"
