"""The Scenes section of the Pool control tab, and its script.

A scene is several controls in one tap -- "filter, sheer descent and pool
light", "spa session" -- run through /api/control as action "scene", which
server.command() expands into ordinary commands (scenes.py says why that is
the whole of the design). This module is the page half: the cards, the Run
button, and the editor.

RENDERED: the editor's rows, one per control, from the shared vocabulary --
which switches exist and what each heater's range is. Those change only with
a deploy. FETCHED, from /api/scenes: the scenes themselves and the templates,
because a scene saved in one tab must appear in another without a render.
Every name in a scene was typed by a person, so it goes in with textContent.
"""
import html

from . import commands as C, schedule


def esc(s):
    return html.escape(str(s), quote=True)


def _rows(cfg):
    sw = []
    for dev, spec in C.SWITCHES.items():
        if dev == "SWG/Boost":
            continue                    # not a toggle; see panels.py
        lbl = esc(C.label_for(dev, cfg))
        sw.append(f'''<label class="f f-sm scene-row" data-device="{esc(dev)}" data-kind="switch">
          <span>{lbl}</span>
          <select aria-label="{lbl} in this scene">
            <option value="">leave as it is</option><option value="1">on</option><option value="0">off</option>
          </select></label>''')
    for dev in C.HEATERS:
        spec = C.SETPOINTS[dev]
        lbl = esc(C.label_for(dev, cfg))
        sw.append(f'''<div class="f f-sm scene-row" data-device="{esc(dev)}" data-kind="heater"
             data-min="{spec["min"]}" data-max="{spec["max"]}">
          <span>{lbl}</span>
          <select aria-label="{lbl} in this scene">
            <option value="">leave as it is</option><option value="heat">heat to…</option><option value="0">off</option>
          </select>
          <input type="number" min="{spec["min"]}" max="{spec["max"]}" step="1" hidden
                 aria-label="{lbl}: heat to, {spec["min"]}–{spec["max"]} °F" placeholder="{spec["min"]}–{spec["max"]} °F">
        </div>''')
    return "".join(sw)


def section(cfg=None):
    from . import config
    cfg = cfg if cfg is not None else config.load()
    offs = "".join(f'<option value="{h:g}">{h:g} h</option>' for h in schedule.OFF_AFTER_CHOICES)
    return f'''
<section id="ctl-scenes">
  <h2>Scenes: several controls in one tap</h2>
  <p class="sub">Press several controls at once. A scene always runs in a safe
  order (the pump first, a heater last) and stops if any control is refused.
  &ldquo;Then switch off after&rdquo; leaves the filter pump to the panel's
  program.</p>
  <div class="scene-cards" id="scene-list"><p class="empty">Reading the scenes…</p></div>
  <p class="sub" id="scene-msg" role="status"></p>
  <div class="factions scene-new">
    <button type="button" class="btn" id="scene-new">New scene</button>
    <span class="sub">or start from:</span>
    <span id="scene-templates"></span>
  </div>

  <div class="card scene-editor" id="scene-editor" hidden>
    <h3 class="fh" id="scene-editor-title">New scene</h3>
    <div class="frow">
      <label class="f"><span>Name</span>
        <input type="text" id="scene-name" maxlength="40" aria-label="The scene's name"></label>
      <label class="f f-sm"><span>Then switch off after</span>
        <select id="scene-off" aria-label="Switch what this scene turns on off again after">
          <option value="">never</option>{offs}</select></label>
    </div>
    <div class="frow scene-rows">{_rows(cfg)}</div>
    <p class="scene-preview" id="scene-preview" aria-live="polite"></p>
    <div class="factions">
      <button type="button" class="btn primary" id="scene-save">Save the scene</button>
      <button type="button" class="btn" id="scene-cancel">Cancel</button>
      <span class="fmsg" id="scene-form-msg" role="status"></span>
    </div>
  </div>
</section>'''


# Raw, so `\\n` and regex escapes mean what they say in JavaScript.
SCRIPT = r"""
/* ------------------------------------------------------------------- scenes ---
   Fetched from /api/scenes, never baked. Names are a person's words: text
   nodes only. Running one is /api/control with action "scene". */
(function () {
  var list = document.getElementById('scene-list');
  if (!list) return;
  var msg = document.getElementById('scene-msg');
  var ed = document.getElementById('scene-editor');
  var nameIn = document.getElementById('scene-name');
  var offIn = document.getElementById('scene-off');
  var formMsg = document.getElementById('scene-form-msg');
  var preview = document.getElementById('scene-preview');
  var PUMP = 'Filter_Pump', last = null, editingId = null, roleTries = 0;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = String(text);
    return e;
  }
  function may() {
    return typeof CANWRITE === 'undefined' || (CANWRITE
      && !(typeof RANK === 'number' && RANK < LEVELS.indexOf('operate')));
  }
  function say(t, cls) { msg.textContent = t; msg.className = 'sub' + (cls ? ' ' + cls : ''); }
  function rowFor(dev) { return ed.querySelector('.scene-row[data-device="' + dev + '"]'); }
  function label(dev) { var r = rowFor(dev); return r ? r.querySelector('span').textContent : dev; }
  function stepWords(st) {
    var l = label(st.device);
    if (st.action === 'setpoint') return l + ' to ' + st.value + '°F';
    return l + (Number(st.value) ? ' on' : ' off');
  }
  function heats(sc) { return sc.steps.some(function (s) { return s.action === 'heat' && Number(s.value); }); }

  /* ---- the cards ---- */
  function card(sc) {
    var c = el('article', 'scene-card');
    c.setAttribute('aria-label', 'Scene ' + sc.name);
    c.appendChild(el('h4', 'scene-name', sc.name));
    var steps = el('p', 'scene-steps');
    sc.steps.forEach(function (st, i) {
      if (i) steps.appendChild(document.createTextNode(' → '));
      steps.appendChild(el('span', 'scene-step', stepWords(st)));
    });
    c.appendChild(steps);
    if (sc.off_after_h)
      c.appendChild(el('p', 'sub scene-then', 'Then off after ' + sc.off_after_h + ' h: '
        + (sc.then_off || []).join(', ') + '. The filter pump stays with the panel’s program.'));
    var acts = el('div', 'factions');
    var run = el('button', 'btn primary', 'Run');
    run.type = 'button';
    run.setAttribute('aria-label', 'Run the scene ' + sc.name);
    var edit = el('button', 'btn', 'Edit');
    edit.type = 'button';
    edit.setAttribute('aria-label', 'Edit the scene ' + sc.name);
    var del = el('button', 'btn', 'Delete');
    del.type = 'button';
    del.setAttribute('aria-label', 'Delete the scene ' + sc.name);
    [run, edit, del].forEach(function (b) { b.disabled = !may(); acts.appendChild(b); });
    c.appendChild(acts);
    var out = el('div', 'scene-out');
    out.setAttribute('role', 'status');
    c.appendChild(out);
    run.addEventListener('click', function () { runScene(sc, run, out); });
    edit.addEventListener('click', function () { openEditor(sc, sc.id); });
    del.addEventListener('click', function () {
      if (!confirm('Delete the scene ' + sc.name + '? A schedule that runs it will then do nothing.')) return;
      post('/api/scene/delete', {id: sc.id}).then(function (res) {
        if (!res.ok) { say(res.j.error || 'refused', 'bad'); return; }
        say('Deleted ' + sc.name + '.', 'ok'); load();
      });
    });
    return c;
  }

  function runScene(sc, btn, out) {
    var words = sc.steps.map(stepWords).join(', then ');
    var q = (heats(sc) ? 'This lights a gas heater — the most expensive thing the pool does.\n\n' : '')
          + 'Run ' + sc.name + ': ' + words + '?'
          + (sc.off_after_h ? '\n\nThen off again after ' + sc.off_after_h + ' h.' : '');
    if (!confirm(q)) return;
    btn.disabled = true;
    out.textContent = '';
    out.appendChild(el('p', 'scene-progress', 'Running… each control is pressed in turn.'));
    post('/api/control', {action: 'scene', device: sc.id}).then(function (res) {
      btn.disabled = !may();
      out.textContent = '';
      var j = res.j || {};
      var ul = el('ul', 'scene-ran');
      (j.ran || []).forEach(function (r) {
        ul.appendChild(el('li', 'ok', '✓ ' + r.what + (r.pending ? ' — sent, waiting for the panel' : ' — accepted')));
        /* The control cards below follow it to the panel's answer. */
        if (window.markPending && r.device)
          window.markPending(r.device, r.action === 'setpoint' ? String(r.value) : (Number(r.value) ? 'on' : 'off'),
                             r.action === 'setpoint');
      });
      if (!res.ok || (j.result && j.result.ok === false)) {
        var why = String(res.j.error || (j.result && j.result.error) || 'refused').replace(/\.+$/, '');
        /* The server already says "stopped at <step>: <reason>". */
        why = /^stopped at /i.test(why) ? 'Stopped at ' + why.slice(11) : 'Stopped: ' + why;
        ul.appendChild(el('li', 'bad', '✗ ' + why + '. Nothing after it was pressed.'));
      } else if (j.off_after_h) {
        ul.appendChild(el('li', null, 'Off again in ' + j.off_after_h + ' h — see “Waiting to switch off” below.'));
      }
      out.appendChild(ul);
      if (window.reloadSchedules) window.reloadSchedules();
      if (window.reloadHistory) window.reloadHistory();
    });
  }

  /* ---- the editor ---- */
  function stepsFromForm() {
    var steps = [];
    ed.querySelectorAll('.scene-row').forEach(function (r) {
      var dev = r.dataset.device, v = r.querySelector('select').value;
      if (v === '') return;
      if (r.dataset.kind === 'switch') { steps.push({action: 'set', device: dev, value: Number(v)}); return; }
      if (v === '0') { steps.push({action: 'heat', device: dev, value: 0}); return; }
      var t = r.querySelector('input').value;
      steps.push({action: 'setpoint', device: dev, value: t === '' ? null : Number(t)});
      steps.push({action: 'heat', device: dev, value: 1});
    });
    return steps;
  }
  /* The order the server will run them in, said before saving. */
  function orderKey(st) {
    var on = st.action === 'setpoint' || Number(st.value);
    if (!on) return [0, st.action === 'heat' ? 0 : st.device === PUMP ? 2 : 1];
    return [1, st.device === PUMP ? 0 : st.action === 'setpoint' ? 2 : st.action === 'heat' ? 3 : 1];
  }
  function showPreview() {
    ed.querySelectorAll('.scene-row[data-kind="heater"]').forEach(function (r) {
      r.querySelector('input').hidden = r.querySelector('select').value !== 'heat';
    });
    var steps = stepsFromForm().sort(function (a, b) {
      var x = orderKey(a), y = orderKey(b); return x[0] - y[0] || x[1] - y[1];
    });
    preview.textContent = steps.length
      ? 'Runs: ' + steps.map(function (s) { return s.action === 'setpoint' && s.value == null ? label(s.device) + ' to ?' : stepWords(s); }).join(' → ')
      : 'Choose at least one control.';
  }
  function openEditor(sc, id) {
    editingId = id || null;
    document.getElementById('scene-editor-title').textContent = id ? 'Edit ' + sc.name : 'New scene';
    nameIn.value = sc ? sc.name : '';
    offIn.value = sc && sc.off_after_h ? String(sc.off_after_h) : '';
    ed.querySelectorAll('.scene-row').forEach(function (r) {
      r.querySelector('select').value = '';
      var i = r.querySelector('input'); if (i) i.value = '';
    });
    ((sc && sc.steps) || []).forEach(function (st) {
      var r = rowFor(st.device);
      if (!r) return;
      var sel = r.querySelector('select');
      if (st.action === 'set') sel.value = String(st.value);
      else if (st.action === 'heat') { if (!Number(st.value)) sel.value = '0'; else sel.value = 'heat'; }
      else if (st.action === 'setpoint') { sel.value = 'heat'; r.querySelector('input').value = st.value; }
    });
    formMsg.textContent = '';
    ed.hidden = false;
    showPreview();
    nameIn.focus();
  }
  ed.addEventListener('change', showPreview);
  ed.addEventListener('input', showPreview);
  document.getElementById('scene-new').addEventListener('click', function () { openEditor(null, null); });
  document.getElementById('scene-cancel').addEventListener('click', function () { ed.hidden = true; });
  document.getElementById('scene-save').addEventListener('click', function () {
    var steps = stepsFromForm();
    if (!nameIn.value.trim()) { formMsg.textContent = 'Give it a name.'; formMsg.className = 'fmsg bad'; nameIn.focus(); return; }
    if (!steps.length) { formMsg.textContent = 'Choose at least one control.'; formMsg.className = 'fmsg bad'; return; }
    if (steps.some(function (s) { return s.action === 'setpoint' && s.value == null; })) {
      formMsg.textContent = 'Give each heater a temperature to heat to.'; formMsg.className = 'fmsg bad'; return;
    }
    var body = {name: nameIn.value.trim(), steps: steps, off_after_h: offIn.value ? Number(offIn.value) : null};
    if (editingId) body.id = editingId;
    formMsg.textContent = 'Saving…'; formMsg.className = 'fmsg';
    post('/api/scene', body).then(function (res) {
      if (!res.ok) { formMsg.textContent = res.j.error || 'refused'; formMsg.className = 'fmsg bad'; return; }
      ed.hidden = true;
      say('Saved ' + res.j.name + '.', 'ok');
      load();
    });
  });

  function post(url, body) {
    return fetch(url, {method: 'POST', headers: postHeaders(), body: JSON.stringify(body)})
      .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
      .catch(function (e) { return {ok: false, j: {error: String(e.message || e)}}; });
  }

  function draw(j) {
    last = j;
    if (typeof API !== 'undefined' && !API && roleTries++ < 20)
      setTimeout(function () { if (last) draw(last); }, 500);
    list.textContent = '';
    if (!j.scenes.length) list.appendChild(el('p', 'empty', 'No scenes yet. Start from one below, or make your own.'));
    j.scenes.forEach(function (sc) { list.appendChild(card(sc)); });
    var tp = document.getElementById('scene-templates');
    tp.textContent = '';
    j.templates.forEach(function (t) {
      var b = el('button', 'btn scene-template', t.name);
      b.type = 'button';
      b.setAttribute('aria-label', 'Start a new scene from ' + t.name);
      b.disabled = !may();
      b.addEventListener('click', function () { openEditor(t, null); });
      tp.appendChild(b);
    });
    document.getElementById('scene-new').disabled = !may();
    window.SCENES = j.scenes;
    if (window.offerScenes) window.offerScenes(j.scenes);
  }
  function load() {
    return fetch('/api/scenes', {cache: 'no-store'})
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(draw)
      .catch(function (e) {
        list.textContent = '';
        list.appendChild(el('p', 'empty', e === 403 ? 'You may not read the scenes.' : 'The scenes could not be read.'));
      });
  }
  window.reloadScenes = load;
  load();
})();
"""
