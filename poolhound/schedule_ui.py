"""The schedule section of the Pool control tab, and the script that drives it.

WHY THIS IS NOT IN render.py OR panels.py

Both are large and both are edited constantly; the scheduler's markup and its
page script are one feature and are kept together here so that one change to
the feature is one diff. render.TEMPLATE carries a single `@@schedule_js@@`
inside a `priv:start`/`priv:end` pair, so the public build cuts it out with
every other private handler and the leak assertion still sees the routes if it
ever survives.

WHAT IS RENDERED AND WHAT IS FETCHED

Rendered: the form, whose choices come from the shared vocabulary -- what each
device can be told, which devices may carry "off after", what the safety net
is. Those are the same in every process and change only with a deploy or a
config edit.

Fetched, from /api/schedules: everything that is LIVE. What is due and when,
the pending auto-offs, what fired and what was missed, and whether the thread
that does it is alive. CLAUDE.md's rule, earned twice on this very tab: a
render happens in a different process from the one serving, and a list of
upcoming commands baked at 06:00 is wrong by 06:01.

Everything the server sends is put on the page with textContent. A schedule's
`by` is an identity from a proxy header and a refusal is the agent's own words.
"""
import html

from . import commands as C, config, schedule

esc = html.escape


def _choices(cfg=None):
    """(value, label, kind, min, max) for every command a schedule may carry.

    From commands.catalogue() -- the same list the cards are built from -- so
    the form cannot offer something the agent would refuse. SWG/Boost is left
    out for the reason the Control tab gives: it is not a toggle.
    """
    cat = C.catalogue(cfg)
    out = []
    for dev, spec in cat["switches"].items():
        if dev == "SWG/Boost":
            continue
        out.append((f"set|{dev}|1", f"{spec['label']} on", "on", None, None))
        out.append((f"set|{dev}|0", f"{spec['label']} off", "off", None, None))
    for dev, spec in cat["setpoints"].items():
        out.append((f"setpoint|{dev}", f"{spec['label']} to…", "set",
                    spec["min"], spec["max"]))
    return out


def after_select(device, cfg=None, idn=""):
    """The "then off after" control for one card, or "" if the device has none.

    Pre-selected to the configured default, because the safe choice should be
    the one that happens when nobody chooses. "Never" is the first option and
    says so; the household max-on net still applies to it, and the label says
    THAT too, because a person picking "never" on the spa heater should not be
    surprised three hours later.
    """
    cfg = cfg if cfg is not None else config.load()
    hours = schedule.auto_off_hours(cfg)
    if device not in hours:
        return ""
    offc = C.off_command(device)
    then = C.describe(offc, cfg)
    label = C.label_for(device, cfg)
    default = hours[device]
    opts = ['<option value="">never</option>'] + [
        f'<option value="{h:g}"{" selected" if h == default else ""}>'
        f'{h:g} h</option>' for h in schedule.OFF_AFTER_CHOICES]
    net = schedule.max_on_hours(cfg).get(device)
    netnote = (f' The household limit of {net:g} h applies whatever is chosen.'
               if net else "")
    return (f'<label class="ctl-after">Then {esc(then)} after '
            f'<select class="fsel ctl-offafter" id="{esc(idn)}" '
            f'aria-label="After switching the {esc(label)} on: {esc(then)} after">'
            f'{"".join(opts)}</select></label>'
            f'<p class="ctl-cool">An automatic off is an ordinary command, '
            f'audited as auto-off.{esc(netnote)}</p>')


def section(cfg=None):
    """The markup. Static except for what the vocabulary and config say."""
    cfg = cfg if cfg is not None else config.load()
    def _opt(v, lbl, k, lo, hi):
        rng = f' data-min="{lo}" data-max="{hi}"' if lo is not None else ""
        return f'<option value="{esc(v)}" data-kind="{k}"{rng}>{esc(lbl)}</option>'
    opts = "".join(_opt(*c) for c in _choices(cfg))
    reps = "".join(f'<option value="{esc(k)}">{esc(w)}</option>'
                   for k, w in schedule.REPEAT_WORDS.items())
    after = '<option value="">never</option>' + "".join(
        f'<option value="{h:g}">{h:g} h</option>' for h in schedule.OFF_AFTER_CHOICES)
    autos = schedule.auto_off_hours(cfg)
    autos_txt = ", ".join(f"{C.label_for(d, cfg)} ({C.describe(C.off_command(d), cfg)})"
                          for d in autos) or "nothing"
    nets = schedule.max_on_hours(cfg)
    nets_txt = ("; ".join(f"{C.label_for(d, cfg)}: {h:g} hours, then "
                          f"{C.describe(C.off_command(d), cfg)}"
                          for d, h in nets.items())
                or "none is configured")
    return f'''
<section id="ctl-schedule">
  <h2>Scheduled, and switched off again</h2>
  <p class="sub">A schedule is a button pressed later: at the time, this server
  issues the ordinary command, through the same checks, cooldowns and audit as
  a press — recorded as <b>schedule</b>, not as the person who set it. Times are
  the pool's own clock, {esc(config.DEPLOY_TZ)}.</p>
  <p class="sub">Something switched <b>on</b> runs only within
  {schedule.GRACE_S // 60} minutes of its time; later than that it is recorded
  as missed and never run, because a heater lit hours late is lit for nobody.
  Something switched <b>off</b> that cannot be delivered is tried again until
  the panel reports it off, and somebody is told.</p>
  <p class="sub">The household limit, which applies to every press — here, at
  the wall panel or anywhere else, because it reads what the panel reports:
  {esc(nets_txt)}. It is set in <code>[schedule.max_on]</code> in config.toml,
  not on this page. Devices that can be switched off automatically:
  {esc(autos_txt)}.</p>
  <div class="v-warn" id="sched-dead" hidden style="margin-bottom:14px"></div>
  <div class="card">
    <form id="sched-form" class="form">
      <div class="frow">
        <label class="f"><span>What</span>
          <select id="sched-what" aria-label="What the schedule does">{opts}</select></label>
        <label class="f f-sm" id="sched-val-wrap" hidden><span>Value</span>
          <input type="number" id="sched-val" step="1"
                 aria-label="The setpoint value to schedule"></label>
      </div>
      <div class="frow">
        <label class="f f-sm"><span>Repeat</span>
          <select id="sched-rep" aria-label="How often it repeats">{reps}</select></label>
        <label class="f f-sm" id="sched-date-wrap"><span>Date</span>
          <input type="date" id="sched-date" aria-label="The date it runs"></label>
        <label class="f f-sm"><span>Time</span>
          <input type="time" id="sched-time" aria-label="The time it runs"></label>
        <label class="f f-sm" id="sched-after-wrap"><span>Then off after</span>
          <select id="sched-after" aria-label="Switch it off again after">{after}</select></label>
      </div>
      <div class="factions">
        <button type="submit" class="btn primary" id="sched-add">Add the schedule</button>
        <span class="fmsg" id="sched-msg" role="status"></span>
      </div>
    </form>
  </div>

  <h3 class="fh">Due</h3>
  <div class="scroll"><table>
    <thead><tr><th>Next</th><th>What</th><th>When</th><th>Set by</th><th></th></tr></thead>
    <tbody id="sched-list"><tr><td colspan="5" class="empty">Reading the schedule…</td></tr></tbody>
  </table></div>

  <h3 class="fh">Waiting to switch off</h3>
  <div class="scroll"><table>
    <thead><tr><th>Due</th><th>What</th><th>Why</th><th>Status</th><th></th></tr></thead>
    <tbody id="sched-offs"><tr><td colspan="5" class="empty">Reading the schedule…</td></tr></tbody>
  </table></div>

  <h3 class="fh">What ran, and what did not</h3>
  <div class="scroll"><table>
    <thead><tr><th>When</th><th>What</th><th>Outcome</th><th>Detail</th><th>By</th></tr></thead>
    <tbody id="sched-hist"><tr><td colspan="5" class="empty">Reading the schedule…</td></tr></tbody>
  </table></div>
  <p class="sub" id="sched-hist-note"></p>
  <p class="sub" id="sched-alive" role="status"></p>
</section>'''


# The page script. A raw string so that `\\n` and regex escapes mean what they
# say in JavaScript -- the bug CLAUDE.md records breaking the page three times
# is a """ string that is NOT raw. No HTML is built from server values: every
# cell is a text node.
SCRIPT = r"""
/* ------------------------------------------------------------- schedules ---
   Everything here is fetched from /api/schedules, never baked: the render may
   come from a different process, and "next due" is stale the moment it is
   written. Built with textContent throughout -- `by` is an identity from a
   proxy header and a refusal is the agent's own words. */
(function () {
  var form = document.getElementById('sched-form');
  if (!form) return;
  var what = document.getElementById('sched-what');
  var valWrap = document.getElementById('sched-val-wrap');
  var val = document.getElementById('sched-val');
  var rep = document.getElementById('sched-rep');
  var dateWrap = document.getElementById('sched-date-wrap');
  var date = document.getElementById('sched-date');
  var time = document.getElementById('sched-time');
  var afterWrap = document.getElementById('sched-after-wrap');
  var after = document.getElementById('sched-after');
  var msg = document.getElementById('sched-msg');
  var AUTO = {}, last = null;

  function say(t, cls) { msg.textContent = t; msg.className = 'fmsg ' + (cls || ''); }

  function chosen() {
    var parts = what.value.split('|');
    var o = what.options[what.selectedIndex];
    return {action: parts[0], device: parts[1],
            value: parts[0] === 'set' ? Number(parts[2]) : (val.value === '' ? null : Number(val.value)),
            kind: o ? o.getAttribute('data-kind') : '', opt: o};
  }

  /* The form shows only what applies: a value for a setpoint, a date for a
     one-off, an off-after for something being switched ON that has one. */
  function shape() {
    var c = chosen();
    valWrap.hidden = c.kind !== 'set';
    if (c.kind === 'set' && c.opt) {
      val.min = c.opt.getAttribute('data-min'); val.max = c.opt.getAttribute('data-max');
      val.placeholder = val.min + '–' + val.max;
    }
    dateWrap.hidden = rep.value !== 'once';
    var canAfter = c.kind !== 'off' && AUTO[c.device] !== undefined;
    afterWrap.hidden = !canAfter;
    if (!canAfter) after.value = '';
  }

  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function countdown(iso) {
    if (!iso) return 'not due';
    var s = Math.round((new Date(iso).getTime() - Date.now()) / 1000);
    if (s <= 0) return 'due now';
    var d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    if (d) return 'in ' + d + ' d ' + h + ' h';
    if (h) return 'in ' + h + ' h ' + pad(m) + ' min';
    return 'in ' + m + ' min ' + pad(s % 60) + ' s';
  }
  function clock(iso) {
    if (!iso) return '';
    var t = new Date(iso);
    return t.toLocaleString([], {weekday: 'short', day: 'numeric', month: 'short',
                                 hour: '2-digit', minute: '2-digit'});
  }
  function cell(tr, text, cls) {
    var td = document.createElement('td');
    td.textContent = text == null ? '' : String(text);
    if (cls) td.className = cls;
    tr.appendChild(td);
    return td;
  }
  function empty(tb, text) {
    tb.textContent = '';
    var tr = document.createElement('tr');
    var td = cell(tr, text, 'empty'); td.colSpan = 5;
    tb.appendChild(tr);
  }
  function cancelButton(row, name) {
    var b = document.createElement('button');
    b.type = 'button'; b.className = 'btn';
    b.textContent = 'Cancel';
    b.setAttribute('data-sched-cancel', row.id);
    /* Named for what it cancels, so reaching it out of context still says
       which schedule it ends -- the On/Off defect bin/drive exists for. */
    b.setAttribute('aria-label', 'Cancel ' + name);
    if (!CANWRITE || (typeof RANK === 'number' && RANK < LEVELS.indexOf('operate'))) b.disabled = true;
    b.addEventListener('click', function () {
      if (!confirm('Cancel ' + name + '?')) return;
      b.disabled = true;
      post('/api/schedule/delete', {id: row.id}).then(function (res) {
        if (!res.ok) { say(res.j.error || 'refused', 'bad'); b.disabled = false; return; }
        say('cancelled: ' + name, 'ok');
        load();
      });
    });
    return b;
  }

  function draw(j) {
    last = j;
    AUTO = j.auto_off || {};
    shape();
    var tb = document.getElementById('sched-list');
    if (!j.schedules.length) empty(tb, 'Nothing is scheduled.');
    else {
      tb.textContent = '';
      j.schedules.forEach(function (s) {
        var tr = document.createElement('tr');
        cell(tr, countdown(s.next)).setAttribute('data-next', s.next || '');
        cell(tr, s.what + (s.then ? ', then ' + s.then + ' after ' + s.off_after_h + ' h' : ''));
        cell(tr, s.when);
        cell(tr, s.by);
        var td = document.createElement('td');
        td.appendChild(cancelButton(s, 'the schedule: ' + s.what + ', ' + s.when));
        tr.appendChild(td);
        tb.appendChild(tr);
      });
    }
    var to = document.getElementById('sched-offs');
    if (!j.autooffs.length) empty(to, 'Nothing is waiting to be switched off.');
    else {
      to.textContent = '';
      j.autooffs.forEach(function (o) {
        var tr = document.createElement('tr');
        cell(tr, countdown(o.due)).setAttribute('data-next', o.due || '');
        cell(tr, o.what + ' (' + o.label + ')');
        cell(tr, o.cause === 'max-on' ? 'household limit'
               : o.cause === 'schedule' ? 'a scheduled off, retrying'
               : 'switched on by ' + o.by);
        cell(tr, o.sent_at ? 'sent, waiting for the panel'
               : (o.attempts ? 'not delivered yet, ' + o.attempts + ' attempt(s)' : 'waiting'),
             o.alerted ? 'bad' : '');
        var td = document.createElement('td');
        if (o.cancellable) td.appendChild(cancelButton(o, 'the automatic off: ' + o.what + ' (' + o.label + ')'));
        else td.textContent = 'set in config.toml';
        tr.appendChild(td);
        to.appendChild(tr);
      });
    }
    var th = document.getElementById('sched-hist');
    if (!j.history.length) empty(th, 'Nothing has run yet.');
    else {
      th.textContent = '';
      j.history.forEach(function (h) {
        var tr = document.createElement('tr');
        cell(tr, clock(h.at));
        cell(tr, h.what);
        cell(tr, h.outcome, h.severity === 'serious' ? 'bad' : '');
        cell(tr, h.detail);
        cell(tr, h.by);
        th.appendChild(tr);
      });
    }
    /* Never truncate without saying so. */
    document.getElementById('sched-hist-note').textContent =
      'Showing ' + j.history.length + ' of ' + j.history_total + ' entries kept'
      + (j.unreadable ? '. THE SCHEDULE FILE COULD NOT BE READ — nothing will run until a person looks at it.' : '.');
    alive(j.scheduler, j.runner);
  }

  /* Whether anything is going to run these at all. A thread that died inside
     a live server is exactly what the serving process can report. */
  function alive(sc, rn) {
    var el = document.getElementById('sched-alive');
    var dead = document.getElementById('sched-dead');
    /* Once the Pi has taken over, IT runs these: at the pool, whether or not
       this site is up. Its health arrives with its half-hourly report. */
    if (rn && rn.on_pi) {
      var p = rn.pi || {};
      var mins = rn.report_age_s == null ? null : Math.round(rn.report_age_s / 60);
      el.textContent = 'These run on the Pi at the pool, so they run when this site is down. '
        + (mins === null ? 'The Pi has not reported its scheduler yet.'
                         : 'It reported ' + (mins < 1 ? 'just now' : mins + ' min ago')
                           + (p.last_tick ? ', its scheduler looking every 20 s' : '') + '.')
        + (p.errors ? ' ' + p.errors + ' failed look(s), the last: ' + p.last_error : '')
        + (p.sync_error ? ' Its last sync failed: ' + p.sync_error + '.' : '');
      dead.hidden = !rn.stale;
      dead.textContent = rn.stale
        ? 'The Pi has not reported its scheduler recently. If its agent is down, nothing '
          + 'below will run and no automatic off will happen until it is back.'
        : '';
      return;
    }
    if (!sc) { el.textContent = ''; return; }
    var ago = sc.last_tick ? Math.round((Date.now() - new Date(sc.last_tick).getTime()) / 1000) : null;
    el.textContent = sc.running
      ? 'The scheduler checks every ' + sc.tick_s + ' s; last looked '
        + (ago === null ? 'not yet' : ago + ' s ago') + '.'
        + (sc.errors ? ' ' + sc.errors + ' failed look(s), the last: ' + sc.last_error : '')
      : 'The scheduler is not running in the process serving this page.';
    dead.hidden = !(sc.stale);
    dead.textContent = sc.stale
      ? 'The scheduler has not checked the clock recently, so nothing below will run '
        + 'and no automatic off will happen until it does. The server log says why.'
      : '';
  }

  function post(url, body) {
    return fetch(url, {method: 'POST', headers: postHeaders(), body: JSON.stringify(body)})
      .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
      .catch(function (e) { return {ok: false, j: {error: String(e.message || e)}}; });
  }

  /* Retried once: this server speaks HTTP/1.0, and a burst of fetches at
     load loses one now and then (see the command log's own note). */
  function ask(tries) {
    return fetch('/api/schedules', {cache: 'no-store'})
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .catch(function (e) {
        if (tries > 0 && typeof e !== 'number')
          return new Promise(function (res) { setTimeout(res, 250); })
            .then(function () { return ask(tries - 1); });
        return Promise.reject(e);
      });
  }
  function load() {
    return ask(1).then(draw).catch(function (e) {
      ['sched-list', 'sched-offs', 'sched-hist'].forEach(function (id) {
        empty(document.getElementById(id),
              e === 403 ? 'You may not read the schedule.' : 'The schedule could not be read.');
      });
    });
  }

  /* Scenes appear in "What" as their own group, offered by the Scenes
     section when it has read them -- fetched, so a scene saved in another
     tab is schedulable without a render. A scene carries its own "then off
     after", so the off-after control is hidden for one (shape() does it). */
  window.offerScenes = function (scenes) {
    var g = what.querySelector('optgroup[data-scenes]');
    if (g) g.parentNode.removeChild(g);
    if (!scenes || !scenes.length) return;
    g = document.createElement('optgroup');
    g.label = 'Scenes'; g.setAttribute('data-scenes', '1');
    scenes.forEach(function (sc) {
      var o = document.createElement('option');
      o.value = 'scene|' + sc.id + '|';
      o.setAttribute('data-kind', 'scene');
      o.setAttribute('data-heats', sc.steps.some(function (s) { return s.action === 'heat' && Number(s.value); }) ? '1' : '');
      o.textContent = 'Run the scene: ' + sc.name;
      g.appendChild(o);
    });
    what.appendChild(g);
  };
  if (window.SCENES) window.offerScenes(window.SCENES);
  what.addEventListener('change', shape);
  rep.addEventListener('change', shape);
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var c = chosen();
    if (!time.value) { say('Choose a time.', 'bad'); time.focus(); return; }
    if (rep.value === 'once' && !date.value) { say('Choose a date.', 'bad'); date.focus(); return; }
    if (c.kind === 'set' && c.value === null) { say('Type a value.', 'bad'); val.focus(); return; }
    var body = {action: c.action, device: c.device, value: c.value, repeat: rep.value,
                time: time.value, at: date.value + 'T' + time.value,
                off_after_h: after.value ? Number(after.value) : null};
    /* Gas, scheduled, is the one worth a second look. */
    if ((c.device === 'Pool_Heater' || c.device === 'Spa_Heater' || c.device === 'Spa'
         || (c.kind === 'scene' && c.opt && c.opt.getAttribute('data-heats')))
        && c.kind !== 'off'
        && !confirm('This will ' + c.opt.textContent.replace('…', ' ' + (c.value || ''))
                    + ' ' + (rep.value === 'once' ? 'on ' + date.value : rep.options[rep.selectedIndex].textContent)
                    + ' at ' + time.value + ', whoever is home. Schedule it?')) return;
    say('saving…');
    post('/api/schedule', body).then(function (res) {
      if (!res.ok) { say(res.j.error || 'refused', 'bad'); return; }
      say('scheduled: ' + c.opt.textContent.replace('…', ' ' + (c.value || '')), 'ok');
      load();
    });
  });

  /* Sensible starting values: today, the next whole hour. */
  var now = new Date();
  date.value = now.getFullYear() + '-' + pad(now.getMonth() + 1) + '-' + pad(now.getDate());
  shape();
  load();
  /* The countdowns tick locally; the list is re-read every half minute, and
     not while the tab is hidden. */
  setInterval(function () {
    document.querySelectorAll('#sched-list [data-next], #sched-offs [data-next]').forEach(function (td) {
      td.textContent = countdown(td.getAttribute('data-next'));
    });
  }, 1000);
  setInterval(function () { if (!document.hidden) load(); }, 30000);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) load(); });
  window.reloadSchedules = load;
})();
"""
