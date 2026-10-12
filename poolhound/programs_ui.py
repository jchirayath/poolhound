"""The 'Programmed on the panel' section of the Pool control tab, and its script.

Kept beside schedule_ui.py rather than inside it because it is a different
thing: that section is this server's own scheduler, which presses buttons
through the agent; this one shows what the panel and AqualinkD do by
themselves, and changes a panel program's times through the ordinary command
path (a `program` command to /api/control).

EVERYTHING IS FETCHED, from /api/panel-schedules, for CLAUDE.md's reason: a
render happens in a different process, and when the Pi last read the panel is
a fact about now. The rendered markup is the frame and the words; the cards
arrive with the data, every string a text node, because every string in them
was typed by a person into a panel or a cron file.

READ AT A GLANCE, EDITED IN PLACE. A table of four columns asked the reader to
turn "10:00 AM / 4:00 PM / ALL DAYS" into "is it on now"; each programmed
device is a card instead, with its window drawn on a 24-hour bar, the time
now marked on it, and that answer written out beside it -- never the bar
alone. Devices with no program are one line, not eight empty rows. A change
happens inside the card: the bar previews the new window as it is typed,
and the progress of the change -- sent, the panel being changed, saved or
not -- is shown on the card it is about.
"""
import html

from . import config, programs


def section(cfg=None):
    cfg = cfg if cfg is not None else config.load()
    tz = html.escape(config.DEPLOY_TZ)
    return f'''
<section id="ctl-programs">
  <h2>Programmed on the panel, and in AqualinkD</h2>
  <p class="sub">What the pool does by itself, every day, with nobody pressing
  anything. Times are the pool's clock, {tz}.</p>

  <div class="v-warn" id="prog-overlap" role="alert" hidden></div>

  <details class="prog-why">
    <summary>Two schedules: which should I use?</summary>
    <p><b>The Jandy panel's programs</b> are stored in the panel and run by it.
    They keep running when the Pi, AqualinkD or this site are down, and after a
    power cut. <b>AqualinkD's schedules</b> live on the Pi and stop whenever it
    does. The two do not know about each other.</p>
    <p><b>Use the panel's programs.</b> Put the pump's and the heaters' regular
    hours there, and use AqualinkD only for something the panel cannot do.
    Never schedule the same equipment in both: each switches it on its own
    timetable, and one undoes the other. Neither is the list above, which
    this server runs.</p>
  </details>

  <div class="prog-head">
    <h3 class="fh">The Jandy panel's programs</h3>
    <span class="pill p-ok">Recommended</span>
  </div>
  <p class="sub" id="prog-status" role="status">Reading what the Pi last reported…</p>
  <div class="prog-cards" id="prog-panel"><p class="empty">Reading…</p></div>
  <p class="sub" id="prog-none"></p>
  <p class="sub" id="prog-panel-note"></p>

  <h3 class="fh">AqualinkD's schedules, on the Pi</h3>
  <p class="sub">For what the panel cannot do. They stop whenever the Pi does,
  so the pump's and heaters' regular hours belong in the panel's programs above.</p>
  <div id="prog-aqd"><p class="empty">Reading…</p></div>
  <p class="sub" id="prog-aqd-note" role="status"></p>
</section>'''


# Raw, so `\\n` and regex escapes mean what they say in JavaScript.
SCRIPT = r"""
/* ------------------------------------------------- the panel's programs ---
   Fetched from /api/panel-schedules, never baked. Text nodes throughout:
   every string here was typed into a panel or a cron file by a person. */
(function () {
  var status = document.getElementById('prog-status');
  if (!status) return;
  var STALE_S = @@prog_stale_s@@, TZ = '@@prog_tz@@', DAY = 1440;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = String(text);
    return e;
  }
  function ago(s) {
    if (s == null) return 'never';
    if (s < 90) return s + ' s ago';
    if (s < 5400) return Math.round(s / 60) + ' min ago';
    if (s < 172800) return Math.round(s / 3600) + ' h ago';
    return Math.round(s / 86400) + ' days ago';
  }
  function since(epoch) {
    return epoch ? ago(Math.max(0, Math.round(Date.now() / 1000 - epoch))) : 'never';
  }
  /* The panel's 12-hour time to minutes after midnight, and back. */
  function toMin(t) {
    var m = /^(\d{1,2}):(\d{2}) (AM|PM)$/.exec(t || '');
    return m ? (Number(m[1]) % 12 + (m[3] === 'PM' ? 12 : 0)) * 60 + Number(m[2]) : null;
  }
  function fmt(min) {
    var h = Math.floor(min / 60) % 24, m = min % 60;
    return (h % 12 || 12) + ':' + (m < 10 ? '0' : '') + m + (h < 12 ? ' AM' : ' PM');
  }
  /* An <input type=time> value is 24-hour HH:MM. */
  function inMin(v) { var m = /^(\d{2}):(\d{2})$/.exec(v || ''); return m ? Number(m[1]) * 60 + Number(m[2]) : null; }
  function inVal(min) { var h = Math.floor(min / 60), m = min % 60; return (h < 10 ? '0' : '') + h + ':' + (m < 10 ? '0' : '') + m; }
  function span(on, off) { return off > on ? off - on : DAY - on + off; }
  function dur(min) {
    var h = Math.floor(min / 60), m = min % 60;
    return (h ? h + ' h' : '') + (h && m ? ' ' : '') + (m ? m + ' min' : '') || '0 min';
  }
  /* Minutes after midnight NOW, on the pool's clock rather than the reader's. */
  function nowMin() {
    try {
      var p = new Intl.DateTimeFormat('en-US', {timeZone: TZ, hour: '2-digit', minute: '2-digit', hourCycle: 'h23'})
        .formatToParts(new Date());
      var g = function (t) { return Number((p.filter(function (x) { return x.type === t; })[0] || {}).value); };
      return (g('hour') % 24) * 60 + g('minute');
    } catch (e) { var d = new Date(); return d.getHours() * 60 + d.getMinutes(); }
  }
  function inWindow(on, off, now) { return on < off ? now >= on && now < off : now >= on || now < off; }
  /* "SOLAR HEAT" reads better as "Solar heat"; the panel's own spelling is
     kept for the command and shown on hover. */
  function nice(name) { return name.charAt(0) + name.slice(1).toLowerCase(); }

  /* The 24-hour bar. Decorative: what it shows is also written beside it. */
  function bar(on, off, preview) {
    var wrap = el('div', 'prog-bar');
    wrap.setAttribute('aria-hidden', 'true');
    var track = el('div', 'prog-track');
    function seg(a, b) {
      var s = el('span', 'prog-seg' + (preview ? ' prog-preview' : ''));
      s.style.left = (a / DAY * 100) + '%';
      s.style.width = ((b - a) / DAY * 100) + '%';
      track.appendChild(s);
    }
    if (on < off) seg(on, off); else { seg(on, DAY); seg(0, off); }
    var now = el('span', 'prog-now');
    now.style.left = (nowMin() / DAY * 100) + '%';
    track.appendChild(now);
    wrap.appendChild(track);
    var ticks = el('div', 'prog-ticks');
    ['12 AM', '6 AM', 'Noon', '6 PM', '12 AM'].forEach(function (t) { ticks.appendChild(el('span', null, t)); });
    wrap.appendChild(ticks);
    return wrap;
  }
  function stateLine(on, off) {
    var now = nowMin(), inw = inWindow(on, off, now);
    var left = inw ? (off - now + DAY) % DAY : (on - now + DAY) % DAY;
    return {on: inw, text: inw ? 'On now · off at ' + fmt(off) + ', in ' + dur(left)
                                : 'Off now · on at ' + fmt(on) + ', in ' + dur(left)};
  }

  var editing = null, fast = 0, sentAt = 0, sentFor = null, last = null, roleTries = 0;
  function mayChange() {
    return typeof CANWRITE === 'undefined' || (CANWRITE
      && !(typeof RANK === 'number' && RANK < LEVELS.indexOf('operate')));
  }

  /* One programmed device. */
  function card(r, p) {
    var on = toMin(r.on), off = toMin(r.off);
    var c = el('article', 'prog-card');
    c.setAttribute('aria-label', nice(r.device) + ' program');
    var head = el('div', 'prog-card-head');
    var name = el('h4', 'prog-name', nice(r.device));
    name.title = 'On the panel: ' + r.device;
    head.appendChild(name);
    if (on != null && off != null) {
      var st = stateLine(on, off);
      head.appendChild(el('span', 'st ' + (st.on ? 'st-on' : 'st-off'), st.on ? 'On now' : 'Off now'));
    }
    c.appendChild(head);
    if (on == null || off == null) {
      c.appendChild(el('p', 'sub', 'On ' + r.on + ', off ' + r.off + ', ' + r.days.toLowerCase()));
      return c;
    }
    var times = el('p', 'prog-times');
    times.appendChild(el('b', null, r.on));
    times.appendChild(document.createTextNode(' → '));
    times.appendChild(el('b', null, r.off));
    times.appendChild(document.createTextNode(' · ' + dur(span(on, off)) + ' a day · '
      + (r.days === 'ALL DAYS' ? 'every day' : r.days.toLowerCase())
      + (r.of > 1 ? ' · program ' + r.pgm + ' of ' + r.of + ' (the others are not read yet)' : '')));
    c.appendChild(times);
    c.appendChild(bar(on, off, false));
    c.appendChild(el('p', 'prog-state', stateLine(on, off).text));

    var busy = sentFor === r.device;
    if (busy) c.appendChild(progress());
    if (editing === r.device) { c.appendChild(editor(r, on, off)); return c; }
    if (r.of === 1 && p.state === 'ok') {
      var acts = el('div', 'factions');
      var b = el('button', 'btn', 'Change times');
      b.type = 'button';
      b.setAttribute('aria-label', 'Change the ' + nice(r.device) + ' program’s times');
      b.disabled = !mayChange() || busy || !!sentFor;
      b.addEventListener('click', function () {
        if (!mayChange()) { say('You may not change the panel’s programs.', 'bad'); return; }
        editing = r.device; say(''); draw(last);
        var first = document.querySelector('#prog-panel input');
        if (first) first.focus();
      });
      acts.appendChild(b);
      c.appendChild(acts);
    }
    return c;
  }

  /* The change, on the card it is about: three steps, the current one said.
     Until the Pi reports THIS change, the result on file is an older one. */
  function progress() {
    var st = (last && last.panel && last.panel.set) || {};
    var mine = sentAt && st.requested_at >= sentAt - 120;
    var phase = !mine || st.state === 'queued' || st.state === 'running' ? 1
              : st.state === 'ok' ? 2 : 3;
    var box = el('div', 'prog-progress');
    box.setAttribute('role', 'status');
    [['Sent', 0], ['Updating the panel — about a minute', 1],
     [phase === 3 ? 'Not saved' : 'Saved on the panel', 2]].forEach(function (s) {
      var done = s[1] < phase || (s[1] === 2 && phase >= 2);
      var cls = done ? (phase === 3 && s[1] === 2 ? ' failed' : ' done') : (s[1] === phase ? ' active' : '');
      box.appendChild(el('span', 'prog-step' + cls, (done ? (cls === ' failed' ? '✗ ' : '✓ ') : '') + s[0]));
    });
    return box;
  }

  function editor(r, on, off) {
    var f = el('div', 'prog-edit');
    var row = el('div', 'frow');
    function field(label, v, what) {
      var l = el('label', 'f f-sm');
      l.appendChild(el('span', null, label));
      var i = document.createElement('input');
      i.type = 'time'; i.required = true; i.value = inVal(v);
      i.setAttribute('aria-label', nice(r.device) + ' program: ' + what);
      l.appendChild(i);
      row.appendChild(l);
      return i;
    }
    var iOn = field('On at', on, 'on at'), iOff = field('Off at', off, 'off at');
    f.appendChild(row);
    var preview = el('div', 'prog-preview-wrap');
    var summary = el('p', 'prog-summary');
    summary.setAttribute('aria-live', 'polite');
    f.appendChild(preview);
    f.appendChild(summary);
    var acts = el('div', 'factions');
    var save = el('button', 'btn primary', 'Save to the panel');
    save.type = 'button';
    save.setAttribute('aria-label', 'Save the ' + nice(r.device) + ' program to the panel');
    var cancel = el('button', 'btn', 'Cancel');
    cancel.type = 'button';
    cancel.setAttribute('aria-label', 'Cancel changing the ' + nice(r.device) + ' program');
    acts.appendChild(save); acts.appendChild(cancel);
    f.appendChild(acts);

    /* Everything the reader needs to judge the change, before sending it. */
    function check() {
      var a = inMin(iOn.value), b = inMin(iOff.value);
      preview.textContent = '';
      if (a == null || b == null) { summary.textContent = 'Choose both times.'; summary.className = 'prog-summary bad'; save.disabled = true; return null; }
      if (a === b) { summary.textContent = 'On and off cannot be the same time.'; summary.className = 'prog-summary bad'; save.disabled = true; return null; }
      preview.appendChild(el('p', 'prog-label', 'New times'));
      preview.appendChild(bar(a, b, true));
      var same = a === on && b === off;
      summary.className = 'prog-summary' + (same ? ' muted' : '');
      summary.textContent = same ? 'These are the times the panel already has.'
        : 'On ' + fmt(a) + ' → off ' + fmt(b) + ' · ' + dur(span(a, b)) + ' a day'
          + (b < a ? ', through midnight' : '') + ' · days unchanged (' + r.days.toLowerCase() + ')';
      save.disabled = same;
      return [a, b];
    }
    iOn.addEventListener('input', check);
    iOff.addEventListener('input', check);
    cancel.addEventListener('click', function () { editing = null; draw(last); });
    save.addEventListener('click', function () {
      var t = check();
      if (!t) return;
      if (!confirm('Change the ' + nice(r.device) + ' program on the panel to on ' + fmt(t[0])
                   + ', off ' + fmt(t[1]) + '? Its days stay as they are.')) return;
      save.disabled = cancel.disabled = true;
      var value = inVal(t[0]).replace(':', '') + '-' + inVal(t[1]).replace(':', '');
      fetch('/api/control', {method: 'POST', headers: postHeaders(),
                             body: JSON.stringify({action: 'program', device: r.device, value: value})})
        .then(function (res) { return res.json().then(function (j) { return {ok: res.ok, j: j}; }); })
        .then(function (res) {
          var rr = res.j.result || {};
          if (!res.ok || rr.ok === false) {
            /* The agent's Guard says why in `refused`; AqualinkD's refusal and
               the server's arrive as `error`. Show whichever there is. */
            summary.textContent = 'Not changed: ' + (res.j.error || rr.refused || rr.error || 'refused');
            summary.className = 'prog-summary bad';
            save.disabled = cancel.disabled = false;
            return;
          }
          editing = null; fast = 24; sentAt = Date.now() / 1000; sentFor = r.device;
          say('');
          draw(last);
          load();
        })
        .catch(function (e) {
          summary.textContent = 'Not sent: ' + String(e.message || e);
          summary.className = 'prog-summary bad';
          save.disabled = cancel.disabled = false;
        });
    });
    check();
    return f;
  }

  function say(t, cls) {
    var e = document.getElementById('prog-panel-note');
    e.textContent = t; e.className = 'sub' + (cls ? ' ' + cls : '');
  }
  /* The panel's own word on the last change, once it is about THIS change. */
  function lastChange(st) {
    if (!st) return;
    if (sentAt && !(st.requested_at >= sentAt - 120)) return;
    if (st.state !== 'ok' && st.state !== 'failed') return;
    /* Old news after a day: the cards already show what the panel holds. */
    if (!sentAt && st.done_at && Date.now() / 1000 - st.done_at > 86400) return;
    if (sentAt) { fast = 0; sentAt = 0; sentFor = null; }
    var what = nice(st.device) + ' to on ' + st.want_on + ', off ' + st.want_off;
    if (st.state === 'ok') say('✓ Saved on the panel: ' + what + '. The panel shows on ' + st.now_on + ', off ' + st.now_off
                               + (st.done_at ? ' (' + since(st.done_at) + ')' : '') + '.', 'ok');
    else say('Last change, ' + what + ', was not saved: ' + st.error
             + (st.now_on ? '. The panel shows on ' + st.now_on + ', off ' + st.now_off + '.' : '.'), 'bad');
  }

  var PANEL_WORDS = {
    ok: 'Read from the panel',
    reading: 'Being read from the panel now',
    never_read: 'Not read from the panel yet',
    failed: 'The last read of the panel failed',
    unsupported: 'This panel is not one AqualinkD can read programs from',
    unreachable: 'AqualinkD on the Pi cannot read the panel’s programs — it needs the patch in deploy/aqualinkd',
    invalid: 'AqualinkD answered something that was not a programs list'
  };

  function draw(j) {
    if (!j) return;
    last = j;
    /* WHO MAY CHANGE IT IS NOT KNOWN UNTIL /api/health ANSWERS, and this list
       usually arrives first -- measured: every Change button drew disabled for
       the admin. Draw again once the page has its answer. */
    if (typeof API !== 'undefined' && !API && roleTries++ < 20)
      setTimeout(function () { if (last && !editing) draw(last); }, 500);
    var tp = document.getElementById('prog-panel');
    var none = document.getElementById('prog-none');
    var ta = document.getElementById('prog-aqd');
    tp.textContent = ''; ta.textContent = ''; none.textContent = '';
    if (!j.have) {
      status.textContent = j.unreadable
        ? 'The stored report could not be read. The next one from the Pi replaces it.'
        : 'The Pi has not reported its schedules yet.';
      tp.appendChild(el('p', 'empty', 'Nothing reported.'));
      ta.appendChild(el('p', 'empty', 'Nothing reported.'));
      return;
    }
    var p = j.panel || {}, progs = p.programs || [];
    status.textContent = (PANEL_WORDS[p.state] || p.state)
      + (p.read_at ? ' ' + since(p.read_at) : '') + ' · the Pi reported ' + ago(j.age_s)
      + (p.error ? ' · ' + p.error : '')
      + (j.stale ? ' · later than it should be: it reports every ' + Math.round(STALE_S / 3 / 60) + ' minutes' : '');
    status.className = 'sub' + (j.stale || p.state === 'failed' ? ' bad' : '');

    /* The change's result first: it decides whether a card is still busy.
       Drawn after the cards, a finished change left its card's button
       disabled until the next minute's reload -- measured. */
    lastChange(p.set);
    var set = progs.filter(function (r) { return r.of; });
    var unset = progs.filter(function (r) { return !r.of; });
    if (!set.length) tp.appendChild(el('p', 'empty', p.state === 'ok' ? 'No device has a program on the panel.' : 'No programs read.'));
    set.forEach(function (r) { tp.appendChild(card(r, p)); });
    if (unset.length) none.textContent = 'No program: ' + unset.map(function (r) { return nice(r.device); }).join(', ')
      + '. A program for one of these is added at the panel.';

    /* Equipment both kinds switch. */
    var ov = document.getElementById('prog-overlap');
    var ovs = j.overlaps || [];
    ov.textContent = '';
    ov.hidden = !ovs.length;
    if (ovs.length) {
      var head = el('p');
      head.appendChild(el('b', null, 'Scheduled in both places: ' + ovs.map(function (o) { return nice(o.device); }).join(', ') + '.'));
      head.appendChild(document.createTextNode(' Each switches it on its own timetable, so one will undo the '
        + 'other. Keep the panel’s program and delete the AqualinkD schedule below.'));
      ov.appendChild(head);
      ovs.forEach(function (o) {
        ov.appendChild(el('p', null, nice(o.device) + ' — panel: ' + o.panel + '; AqualinkD: ' + o.aqualinkd.join('; ') + '.'));
      });
    }

    drawCron(ta, j);
  }

  /* ------------------------------------------- AqualinkD's own schedules ---
     AqualinkD replaces its whole list on a save, so every change -- add, edit,
     pause, delete -- sends the complete new list, with the fingerprint of the
     list this page showed. If the Pi's list has changed since, the server and
     the agent both refuse rather than overwrite it. */
  var cronEdit = null;            /* null, 'new', or the index being edited */
  var cronBusy = false;
  var DAYS = [['*', 'Every day'], ['1-5', 'Weekdays'], ['0,6', 'Weekends'], ['pick', 'Choose days']];
  var DAYNAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

  function cronSay(t, cls) {
    var e = document.getElementById('prog-aqd-note');
    e.textContent = t; e.className = 'sub' + (cls ? ' ' + cls : '');
  }
  function entryOf(s) { return s.entry; }
  function deviceOfEntry(e) { return (/^\/api\/([^/]+)\/set$/.exec(e.url) || [])[1] || ''; }
  function choiceFor(a, dev) {
    return (a.choices || []).filter(function (c) { return c.device === dev; })[0] || null;
  }
  function cronWords(e, a) {
    var c = choiceFor(a, e.device || deviceOfEntry(e));
    var d = DAYS.filter(function (x) { return x[0] === e.dayw; })[0];
    var days = d ? d[1].toLowerCase()
      : 'on ' + String(e.dayw).split(',').map(function (n) { return DAYNAMES[Number(n) % 7]; }).join(', ');
    return (c ? c.label : e.device) + ' ' + (String(e.value) === '1' ? 'on' : 'off') + ' at '
      + fmt(Number(e.hour) * 60 + Number(e.min)) + ', ' + days;
  }
  /* A device the panel already programs: the overlap the section warns about. */
  function panelHas(c) {
    var progs = (last && last.panel && last.panel.programs) || [];
    return c && progs.filter(function (p) { return p.of && p.device === c.panel_name; })[0] || null;
  }

  function drawCron(ta, j) {
    var a = j.aqualinkd || {}, sch = a.schedules || [];
    var may = mayChange() && !a.cannot_edit && !cronBusy;
    if (a.error) cronSay(a.error + '.', 'bad');
    else if (a.cannot_edit && !cronBusy) cronSay('Not editable here: ' + a.cannot_edit + '.');
    if (!sch.length && cronEdit !== 'new') {
      ta.appendChild(el('p', a.error ? 'empty' : 'prog-good',
        a.error ? 'Not read.' : 'None on the Pi — the panel’s programs are in charge, as recommended.'));
    }
    if (sch.length) {
      var ul = el('ul', 'cron-list');
      sch.forEach(function (s, i) {
        var li = el('li', 'cron-row' + (s.enabled ? '' : ' cron-paused'));
        var words = cronWords(s.entry, a);
        var txt = el('span', 'cron-what');
        txt.appendChild(el('b', null, s.label));
        txt.appendChild(document.createTextNode(words.slice(s.label.length)));
        li.appendChild(txt);
        if (!s.enabled) li.appendChild(el('span', 'st st-off', 'paused'));
        if (may && cronEdit === null) {
          var acts = el('span', 'factions cron-acts');
          [['Edit', function () { cronEdit = i; cronSay(''); draw(last); }],
           [s.enabled ? 'Pause' : 'Resume', function () {
              var list = sch.map(entryOf);
              list[i] = Object.assign({}, list[i], {enabled: !s.enabled});
              saveCron(list, (s.enabled ? 'Pause' : 'Resume') + ': ' + words + '?');
            }],
           ['Delete', function () {
              var list = sch.map(entryOf).filter(function (_, k) { return k !== i; });
              saveCron(list, 'Delete the AqualinkD schedule: ' + words + '?');
            }]].forEach(function (b) {
            var btn = el('button', 'btn', b[0]);
            btn.type = 'button';
            btn.setAttribute('aria-label', b[0] + ' the schedule: ' + words);
            btn.addEventListener('click', b[1]);
            acts.appendChild(btn);
          });
          li.appendChild(acts);
        }
        ul.appendChild(li);
        if (cronEdit === i) ul.appendChild(cronEditor(a, sch, i));
      });
      ta.appendChild(ul);
    }
    if (cronEdit === 'new') ta.appendChild(cronEditor(a, sch, 'new'));
    else if (may && cronEdit === null && sch.length < @@cron_max@@) {
      var add = el('div', 'factions');
      var b = el('button', 'btn', 'Add an AqualinkD schedule');
      b.type = 'button';
      b.addEventListener('click', function () { cronEdit = 'new'; cronSay(''); draw(last); });
      add.appendChild(b);
      ta.appendChild(add);
    }
  }

  function cronEditor(a, sch, at) {
    var cur = at === 'new' ? {device: 'Aux_1', value: '1', hour: '19', min: '0', dayw: '*', enabled: true}
                           : Object.assign({device: deviceOfEntry(sch[at].entry)}, sch[at].entry);
    var f = el('div', 'prog-edit cron-edit');
    f.setAttribute('role', 'group');
    f.setAttribute('aria-label', at === 'new' ? 'New AqualinkD schedule' : 'Edit the AqualinkD schedule');
    var row = el('div', 'frow');
    function labelled(text, input) {
      var l = el('label', 'f f-sm');
      l.appendChild(el('span', null, text));
      l.appendChild(input);
      row.appendChild(l);
      return input;
    }
    var dev = document.createElement('select');
    (a.choices || []).forEach(function (c) {
      var o = el('option', null, c.label + (c.off_only ? ' (off only)' : ''));
      o.value = c.device; dev.appendChild(o);
    });
    dev.value = cur.device;
    labelled('Switch', dev);
    var what = document.createElement('select');
    [['1', 'On'], ['0', 'Off']].forEach(function (x) { var o = el('option', null, x[1]); o.value = x[0]; what.appendChild(o); });
    what.value = String(cur.value);
    labelled('Turn it', what);
    var time = document.createElement('input');
    time.type = 'time'; time.required = true;
    time.value = inVal(Number(cur.hour) * 60 + Number(cur.min));
    labelled('At', time);
    var days = document.createElement('select');
    DAYS.forEach(function (x) { var o = el('option', null, x[1]); o.value = x[0]; days.appendChild(o); });
    var preset = DAYS.some(function (x) { return x[0] === cur.dayw; });
    days.value = preset ? cur.dayw : 'pick';
    labelled('Days', days);
    f.appendChild(row);
    var pick = el('div', 'cron-days');
    var picked = preset ? [] : String(cur.dayw).split(',');
    var boxes = DAYNAMES.map(function (n, k) {
      var l = el('label', 'cron-day');
      var c = document.createElement('input');
      c.type = 'checkbox'; c.value = String(k); c.checked = picked.indexOf(String(k)) >= 0;
      l.appendChild(c); l.appendChild(document.createTextNode(' ' + n));
      pick.appendChild(l);
      return c;
    });
    f.appendChild(pick);
    var summary = el('p', 'prog-summary');
    summary.setAttribute('aria-live', 'polite');
    f.appendChild(summary);
    var acts = el('div', 'factions');
    var save = el('button', 'btn primary', 'Save to the Pi');
    save.type = 'button';
    var cancel = el('button', 'btn', 'Cancel');
    cancel.type = 'button';
    acts.appendChild(save); acts.appendChild(cancel);
    f.appendChild(acts);

    function entry() {
      var c = choiceFor(a, dev.value);
      if (c && c.off_only && what.value === '1') what.value = '0';
      what.querySelector('option[value="1"]').disabled = !!(c && c.off_only);
      pick.hidden = days.value !== 'pick';
      var m = inMin(time.value);
      var dw = days.value === 'pick'
        ? boxes.filter(function (b) { return b.checked; }).map(function (b) { return b.value; }).join(',')
        : days.value;
      if (m == null) { summary.textContent = 'Choose a time.'; summary.className = 'prog-summary bad'; save.disabled = true; return null; }
      if (!dw) { summary.textContent = 'Choose at least one day.'; summary.className = 'prog-summary bad'; save.disabled = true; return null; }
      var e = {device: dev.value, value: what.value, hour: String(Math.floor(m / 60)), min: String(m % 60),
               dayw: dw, enabled: at === 'new' ? true : !!cur.enabled};
      var p = panelHas(c);
      summary.className = 'prog-summary' + (p ? ' bad' : '');
      summary.textContent = cronWords(e, a) + '.'
        + (c && c.off_only ? ' A heater can only be switched off here: lighting one belongs to poolhound’s own schedule, which checks first and switches it off again.' : '')
        + (p ? ' The panel already programs ' + nice(p.device) + ' (' + p.on + ' to ' + p.off
               + '); scheduling it here too means one undoes the other.' : '');
      save.disabled = false;
      return e;
    }
    [dev, what, time, days].concat(boxes).forEach(function (x) {
      x.addEventListener(x.tagName === 'SELECT' || x.type === 'checkbox' ? 'change' : 'input', entry);
    });
    cancel.addEventListener('click', function () { cronEdit = null; cronSay(''); draw(last); });
    save.addEventListener('click', function () {
      var e = entry();
      if (!e) return;
      var list = sch.map(entryOf);
      if (at === 'new') list.push(e); else list[at] = e;
      var p = panelHas(choiceFor(a, e.device));
      saveCron(list, (at === 'new' ? 'Add' : 'Change to') + ' this AqualinkD schedule: ' + cronWords(e, a) + '?'
        + (p ? '\n\nThe panel already programs ' + nice(p.device) + '. The two will fight; the panel’s program is the one to keep.' : ''));
    });
    setTimeout(entry, 0);
    return f;
  }

  function saveCron(list, question) {
    var a = (last && last.aqualinkd) || {};
    if (!confirm(question + '\n\nAqualinkD’s whole list is saved on the Pi in one go.')) return;
    cronBusy = true; cronSay('Saving to the Pi…');
    draw(last);
    fetch('/api/control', {method: 'POST', headers: postHeaders(),
                           body: JSON.stringify({action: 'cron', value: {expect: a.fingerprint, schedules: list}})})
      .then(function (res) { return res.json().then(function (j) { return {ok: res.ok, j: j}; }); })
      .then(function (res) {
        var rr = res.j.result || {};
        cronBusy = false;
        if (!res.ok || rr.ok === false) {
          cronSay('Not saved: ' + (res.j.error || rr.refused || rr.error || 'refused'), 'bad');
          /* A list that moved on the Pi is reported again at once; read it
             back for the next half minute so the page catches up by itself. */
          fast = Math.max(fast, 3);
          draw(last);
          return;
        }
        cronEdit = null;
        cronSay(res.j.pending ? 'Sent to the Pi; the list below updates when it reports.'
                              : '✓ Saved on the Pi: ' + (rr.detail || 'done') + '.', 'ok');
        fast = Math.max(fast, 6);
        load();
      })
      .catch(function (e) {
        cronBusy = false;
        cronSay('Not sent: ' + String(e.message || e), 'bad');
        draw(last);
      });
  }

  /* Retried once, as the schedule section does: this server speaks HTTP/1.0. */
  function ask(tries) {
    return fetch('/api/panel-schedules', {cache: 'no-store'})
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .catch(function (e) {
        if (tries > 0 && typeof e !== 'number')
          return new Promise(function (res) { setTimeout(res, 250); })
            .then(function () { return ask(tries - 1); });
        return Promise.reject(e);
      });
  }
  function load() {
    return ask(1).then(function (j) { if (!editing && cronEdit === null) draw(j); else last = j; }).catch(function (e) {
      status.textContent = e === 403 ? 'You may not read the schedules.'
                                     : 'The schedules could not be read.';
      ['prog-panel', 'prog-aqd'].forEach(function (id) {
        var t = document.getElementById(id); t.textContent = '';
        t.appendChild(el('p', 'empty', 'Not available.'));
      });
    });
  }
  load();
  /* Every minute, so "on now" stays true; every ten seconds for four minutes
     after a change, so the panel's answer shows when it lands. Never while a
     card is being edited. */
  setInterval(function () {
    if (document.hidden || editing || cronEdit !== null) return;
    if (fast > 0) { fast--; load(); if (!fast) sentFor = null; }
    else if (Date.now() % 60000 < 10000) load();
  }, 10000);
})();
"""


def script():
    from . import commands
    return (SCRIPT.replace("@@prog_stale_s@@", str(programs.STALE_S))
                  .replace("@@cron_max@@", str(commands.CRON_MAX))
                  .replace("@@prog_tz@@", config.DEPLOY_TZ))
