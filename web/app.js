/* AssemblyMan Portal — companion web portal.
 *
 * Ported from the Claude Design source "AssemblyMan Portal.dc.html". The screens,
 * copy, state shape and interaction rules follow that design; the browser-chrome
 * frame it was mocked inside is dropped, since here the browser is the browser.
 *
 * No build step and no dependencies: the markup lives in index.html, this file
 * owns state and syncs the DOM. Session data below is the design's seeded demo
 * roster — swap `state.people` and the media sources for a real transport
 * (LiveKit room + participant tracks) to put it on live data.
 */
(function () {
  'use strict';

  var DEFAULT_CODE = 'K7F-3QD9';
  var JOINED_ELAPSED = 754; // 12:34 — the design opens mid-session

  // ── state ────────────────────────────────────────────────────────────────

  var state = {
    joined: false,
    joining: false,
    code: '',
    codeErr: false,
    codeErrText: '',
    /* Populated by livekit-bridge.js once a real room is joined. While `connected` is false
       the portal renders the design's seeded roster, so every screen still demonstrates
       without a session running. */
    live: { connected: false, roster: [], hasOperatorVideo: false },
    liveError: '',
    elapsed: 0,
    tab: 'room',
    stage: 'op',
    toast: '',
    ovMarks: true,
    ovGrid: false,
    ovSam: true,
    selId: 'assist',
    seq: 1,
    agents: [
      {
        id: 'assist', name: 'Assembly Assistant', custom: false, deployed: true, voice: 'calm',
        instructions: 'Follow the work order step by step. Confirm each step out loud when it looks complete, and call out the next one. Keep it to one sentence.',
        triggers: { mask: true, capture: false, interval: true, ask: true },
        preview: 'Step 4 confirmed — torque check on the rear bracket passed.'
      },
      {
        id: 'inspect', name: 'Inspection Logger', custom: false, deployed: true, voice: 'muted',
        instructions: 'Watch for defects, wear and misalignment. Flag anything suspect, file a still for it, and note the mask it came from.',
        triggers: { mask: true, capture: true, interval: false, ask: false },
        preview: 'MASK 02 flagged — thread wear on the M6 fastener. Still filed.'
      },
      {
        id: 'parts', name: 'Parts Spotter', custom: false, deployed: false, voice: 'brisk',
        instructions: 'Identify parts in view and pull their spec: part number, torque, and the tool needed. Answer only when asked.',
        triggers: { mask: true, capture: false, interval: false, ask: true },
        preview: 'M6×20 socket cap — 9 N·m, 5 mm hex.'
      }
    ],
    people: [
      { id: 'op', name: 'Operator', role: 'Ray-Ban Meta · POV', kind: 'op', speaking: false },
      { id: 'me', name: 'You', role: 'Viewer · this browser', kind: 'viewer', speaking: false },
      { id: 'dana', name: 'Dana R.', role: 'Viewer · shop lead', kind: 'viewer', speaking: true }
    ]
  };

  var OVERLAYS = [
    { key: 'ovMarks', label: 'Viewfinder marks' },
    { key: 'ovGrid', label: 'Thirds grid' },
    { key: 'ovSam', label: 'Segment Anything' }
  ];

  var TRIGGERS = [
    { key: 'mask', label: 'On new mask', desc: 'When Segment Anything finds something' },
    { key: 'capture', label: 'On capture', desc: 'When the operator takes a still' },
    { key: 'interval', label: 'Every 30 seconds', desc: 'Periodic check-in on progress' },
    { key: 'ask', label: 'When asked', desc: 'Operator says the agent’s name' }
  ];

  var VOICES = [
    { key: 'calm', label: 'Calm' },
    { key: 'brisk', label: 'Brisk' },
    { key: 'muted', label: 'Muted' }
  ];

  var ICON_AGENT =
    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" ' +
    'stroke-linecap="round" stroke-linejoin="round" class="ag-icon" aria-hidden="true">' +
    '<path d="M12 8V4H8"></path><rect width="16" height="12" x="4" y="8" rx="1"></rect>' +
    '<path d="M2 14h2"></path><path d="M20 14h2"></path><path d="M15 13v2"></path><path d="M9 13v2"></path></svg>';

  var ICON_AGENT_TILE =
    '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#b5d9fd" stroke-width="1.5" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M12 8V4H8"></path><rect width="16" height="12" x="4" y="8" rx="1"></rect>' +
    '<path d="M2 14h2"></path><path d="M20 14h2"></path><path d="M15 13v2"></path><path d="M9 13v2"></path></svg>';

  var ICON_CLOSE =
    '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" ' +
    'stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg>';

  // ── helpers ──────────────────────────────────────────────────────────────

  function $(id) { return document.getElementById(id); }
  function all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function show(el, on) { if (el) el.hidden = !on; }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function initials(name) {
    return name.split(/\s+/).map(function (w) { return w[0]; }).join('').slice(0, 2).toUpperCase();
  }

  function roomCode() { return state.code || DEFAULT_CODE; }

  function selected() {
    var byId = state.agents.filter(function (a) { return a.id === state.selId; })[0];
    return byId || state.agents[0];
  }

  /* Roster: people first, then every deployed agent as a participant.
     Agent participant ids are namespaced so a person can't collide with one.
     In a live room the participants are the roster — the seeded people and the studio's
     deployed agents are the standing-in demo. */
  function roster() {
    if (state.live.connected) return state.live.roster;

    var people = state.people.map(function (p) {
      return {
        id: p.id, name: p.name, role: p.role, speaking: p.speaking,
        isOp: p.kind === 'op', isViewer: p.kind === 'viewer', isAgent: false,
        initials: initials(p.name)
      };
    });
    var agents = state.agents.filter(function (a) { return a.deployed; }).map(function (a) {
      return {
        id: 'agent:' + a.id, agentId: a.id, name: a.name,
        role: 'Agent · ' + (a.voice === 'muted' ? 'text only' : 'speaking'),
        isOp: false, isViewer: false, isAgent: true,
        speaking: a.voice !== 'muted', initials: 'AI'
      };
    });
    return people.concat(agents);
  }

  function staged() {
    var list = roster();
    var hit = list.filter(function (r) { return r.id === state.stage; })[0];
    return hit || list[0];
  }

  function patchSelected(patch) {
    state.agents = state.agents.map(function (a) {
      if (a.id !== state.selId) return a;
      var next = {};
      for (var k in a) next[k] = a[k];
      for (var p in patch) next[p] = patch[p];
      return next;
    });
  }

  // ── timer ────────────────────────────────────────────────────────────────

  var tick = null;

  function startTimer() {
    stopTimer();
    tick = setInterval(function () {
      state.elapsed += 1;
      $('elapsed').textContent = elapsedText();
    }, 1000);
  }

  function stopTimer() {
    if (tick) { clearInterval(tick); tick = null; }
  }

  function elapsedText() {
    var mm = String(Math.floor(state.elapsed / 60)).padStart(2, '0');
    var ss = String(state.elapsed % 60).padStart(2, '0');
    return mm + ':' + ss;
  }

  // ── toast ────────────────────────────────────────────────────────────────

  var toastTimer = null;

  function toast(msg) {
    clearTimeout(toastTimer);
    state.toast = msg;
    renderToast();
    toastTimer = setTimeout(function () { state.toast = ''; renderToast(); }, 2200);
  }

  function renderToast() {
    var el = $('toast');
    el.textContent = state.toast;
    show(el, !!state.toast);
  }

  // ── routing ──────────────────────────────────────────────────────────────
  /* The design gives each screen its own URL; hash routes keep that without a
     server rewrite rule, so the portal can be served as static files. */

  function path() {
    if (!state.joined) return '#/join';
    return state.tab === 'studio' ? '#/agents' : '#/room/' + roomCode();
  }

  function syncUrl() {
    if (location.hash !== path()) history.replaceState(null, '', path());
    document.title = !state.joined ? 'Join · AssemblyMan Portal'
      : state.tab === 'studio' ? 'Agent Studio · AssemblyMan Portal'
      : 'Room ' + roomCode() + ' · AssemblyMan Portal';
  }

  function readUrl() {
    var h = location.hash || '';
    var room = h.match(/^#\/room\/([A-Za-z0-9-]+)/);
    if (room) {
      state.code = room[1].toUpperCase();
      state.joined = true;
      state.tab = 'room';
      state.elapsed = JOINED_ELAPSED;
      startTimer();
    } else if (/^#\/agents/.test(h)) {
      state.code = DEFAULT_CODE;
      state.joined = true;
      state.tab = 'studio';
      state.elapsed = JOINED_ELAPSED;
      startTimer();
    }
  }

  // ── actions ──────────────────────────────────────────────────────────────

  function join() {
    var raw = $('code').value;
    var code = (raw || DEFAULT_CODE).trim().toUpperCase();
    // An empty field joins the demo room; a partial code is a typo, not a room.
    if (raw && code.replace(/-/g, '').length < 6) {
      state.codeErr = true;
      state.codeErrText = 'Enter the full code — two groups, like K7F-3QD9.';
      render();
      return;
    }
    state.code = code;
    state.codeErr = false;

    // No transport (CDN blocked, or served without the token endpoint): show the design's
    // demo session rather than a dead screen.
    if (!window.PortalLive) {
      enterSession();
      return;
    }

    state.joining = true;
    render();

    window.PortalLive.connect(code, { onUpdate: onLiveUpdate }).then(function (result) {
      state.joining = false;
      if (!result.ok) {
        state.codeErr = true;
        state.codeErrText = result.error;
        render();
        return;
      }
      enterSession();
    });
  }

  function enterSession() {
    state.joined = true;
    state.codeErr = false;
    // A live session starts its clock now; the demo opens mid-session, as the design does.
    state.elapsed = state.live.connected ? 0 : JOINED_ELAPSED;
    state.tab = 'room';
    state.stage = defaultStage();
    startTimer();
    render();
  }

  /* Stage the operator when there is one — that is what a viewer came to watch. */
  function defaultStage() {
    var op = roster().filter(function (r) { return r.isOp; })[0];
    return op ? op.id : (roster()[0] || {}).id || 'op';
  }

  /* Called by the bridge whenever the room changes: someone joins, a track starts, a
     speaker changes. */
  function onLiveUpdate(snapshot) {
    state.live = snapshot;
    if (!state.joined) return;
    // Whoever was staged may have left, and the operator may only now have arrived.
    var stillThere = roster().some(function (r) { return r.id === state.stage; });
    if (!stillThere) state.stage = defaultStage();
    render();
  }

  function leave() {
    stopTimer();
    if (window.PortalLive) window.PortalLive.disconnect();
    state.live = { connected: false, roster: [], hasOperatorVideo: false };
    state.joined = false;
    state.code = '';
    state.elapsed = 0;
    state.tab = 'room';
    state.stage = 'op';
    $('code').value = '';
    render();
  }

  function goTab(tab) { state.tab = tab; render(); }

  function stageOn(id) { state.stage = id; render(); }

  function recall(agentId, name) {
    state.agents = state.agents.map(function (a) {
      return a.id === agentId ? Object.assign({}, a, { deployed: false }) : a;
    });
    if (state.stage === 'agent:' + agentId) state.stage = 'op';
    toast(name + ' recalled from the room');
    render();
  }

  function deploy() {
    var sel = selected();
    patchSelected({ deployed: true });
    toast(sel.name + ' joined room ' + roomCode());
    render();
  }

  function newAgent() {
    var id = 'custom' + state.seq;
    state.agents = state.agents.concat([{
      id: id, name: 'New agent ' + state.seq, custom: true, deployed: false, voice: 'calm',
      instructions: '', triggers: { mask: false, capture: false, interval: false, ask: true },
      preview: 'No output yet — deploy to see it work.'
    }]);
    state.seq += 1;
    state.selId = id;
    state.tab = 'studio';
    render();
    $('agent-name').focus();
    $('agent-name').select();
  }

  function deleteAgent() {
    var sel = selected();
    var rest = state.agents.filter(function (a) { return a.id !== sel.id; });
    state.agents = rest;
    state.selId = rest.length ? rest[0].id : null;
    if (state.stage === 'agent:' + sel.id) state.stage = 'op';
    toast(sel.name + ' deleted');
    render();
  }

  // ── render: header ───────────────────────────────────────────────────────

  function renderHeader() {
    all('[data-when="joined"]').forEach(function (el) { show(el, state.joined); });
    all('[data-when="not-joined"]').forEach(function (el) { show(el, !state.joined); });
    $('hdr-room-code').textContent = roomCode();

    all('.tab').forEach(function (btn) {
      var on = btn.dataset.tab === state.tab;
      btn.setAttribute('aria-selected', on ? 'true' : 'false');
      btn.tabIndex = on ? 0 : -1;
    });
  }

  // ── render: session ──────────────────────────────────────────────────────

  function renderSession() {
    var list = roster();
    var st = staged();

    show($('stage-op'), !!st && st.isOp);
    show($('stage-viewer'), !!st && st.isViewer);
    show($('stage-agent'), !!st && st.isAgent);

    // Swap the stand-in still for the operator's track once one is actually subscribed.
    var liveVideo = state.live.connected && state.live.hasOperatorVideo;
    show($('stage-video'), liveVideo);
    show($('stage-img'), !liveVideo);
    if (liveVideo) window.PortalLive.attachOperatorVideo($('stage-video'));

    var source = $('screen-session').querySelector('.stage-source');
    if (source) {
      source.textContent = state.live.connected
        ? (liveVideo ? 'OPERATOR POV · LIVE' : 'WAITING FOR THE OPERATOR’S CAMERA')
        : 'OPERATOR POV · RAY-BAN META';
    }

    if (st && st.isViewer) $('staged-initials').textContent = st.initials;
    if (st && st.isAgent) $('staged-name').textContent = st.name;

    show($('ov-marks'), state.ovMarks);
    show($('ov-sam'), state.ovSam);
    show($('ov-grid'), state.ovGrid);

    $('elapsed').textContent = elapsedText();
    $('participant-count').textContent = list.length + ' in room';

    $('participants').innerHTML = list.map(function (r) {
      var onStage = state.stage === r.id;
      var avatar =
        r.isOp ? '<img src="assets/plant.png" alt="">'
        : r.isAgent ? ICON_AGENT_TILE
        : '<span class="pt-initials">' + esc(r.initials) + '</span>';
      return '' +
        '<div class="pt' + (onStage ? ' on-stage' : '') + '" role="button" tabindex="0"' +
        ' data-stage="' + esc(r.id) + '" aria-pressed="' + onStage + '">' +
          '<span class="pt-avatar">' + avatar +
            (r.speaking ? '<span class="pt-speaking"></span>' : '') +
          '</span>' +
          '<span class="pt-id">' +
            '<span class="pt-name">' + esc(r.name) + '</span>' +
            '<span class="pt-role">' + esc(r.role) + '</span>' +
          '</span>' +
          (onStage ? '<span class="tag tag-accent pt-badge">ON STAGE</span>' : '') +
          (r.isAgent
            ? '<button class="pt-remove" type="button" data-recall="' + esc(r.agentId) + '"' +
              ' aria-label="Remove ' + esc(r.name) + ' from the room">' + ICON_CLOSE + '</button>'
            : '') +
        '</div>';
    }).join('');

    // Overlay toggles — corner marks are static children, so append rather than replace.
    var box = $('overlays');
    all('.toggle-row', box).forEach(function (el) { el.remove(); });
    OVERLAYS.forEach(function (o) {
      var row = document.createElement('div');
      row.className = 'toggle-row';
      row.innerHTML =
        '<span class="toggle-label">' + esc(o.label) + '</span>' +
        '<button class="check" type="button" data-overlay="' + o.key + '"' +
        ' role="switch" aria-checked="' + !!state[o.key] + '" aria-label="' + esc(o.label) + '">' +
        (state[o.key] ? '<span class="check-fill"></span>' : '') +
        '</button>';
      box.appendChild(row);
    });
  }

  // ── render: studio ───────────────────────────────────────────────────────

  function renderStudio() {
    var sel = selected();
    $('agent-count').textContent = state.agents.length + ' agent' + (state.agents.length === 1 ? '' : 's');

    $('library').innerHTML = state.agents.map(function (a) {
      var on = sel && a.id === sel.id;
      var status = a.deployed ? 'In room' : (a.custom ? 'Draft' : 'Available');
      return '' +
        '<button class="ag' + (on ? ' selected' : '') + '" type="button" data-select="' + esc(a.id) + '"' +
        ' aria-pressed="' + on + '">' + ICON_AGENT +
          '<span class="ag-id">' +
            '<span class="ag-name">' + esc(a.name) + '</span>' +
            '<span class="ag-status">' + esc(status) + '</span>' +
          '</span>' +
          (a.deployed ? '<span class="ag-live"></span>' : '') +
        '</button>';
    }).join('');

    var editor = $('screen-studio').querySelector('.editor');
    if (!sel) { editor.style.visibility = 'hidden'; return; }
    editor.style.visibility = '';

    $('sel-name').textContent = sel.name;
    show($('sel-in-room'), sel.deployed);
    show($('delete-agent'), sel.custom);

    // Only write into a field the user isn't typing in — otherwise the caret jumps.
    var nameInput = $('agent-name');
    if (document.activeElement !== nameInput && nameInput.value !== sel.name) nameInput.value = sel.name;
    var instr = $('agent-instructions');
    if (document.activeElement !== instr && instr.value !== sel.instructions) instr.value = sel.instructions;

    $('voice').innerHTML = VOICES.map(function (v) {
      var on = sel.voice === v.key;
      return '<button class="voice-opt" type="button" data-voice="' + v.key + '"' +
        ' aria-pressed="' + on + '">' + esc(v.label) + '</button>';
    }).join('');

    var box = $('triggers');
    all('.toggle-row', box).forEach(function (el) { el.remove(); });
    TRIGGERS.forEach(function (t) {
      var on = !!sel.triggers[t.key];
      var row = document.createElement('div');
      row.className = 'toggle-row';
      row.innerHTML =
        '<span class="trigger-label">' +
          '<span class="trigger-name">' + esc(t.label) + '</span>' +
          '<span class="trigger-desc">' + esc(t.desc) + '</span>' +
        '</span>' +
        '<button class="check" type="button" data-trigger="' + t.key + '"' +
        ' role="switch" aria-checked="' + on + '" aria-label="' + esc(t.label) + '">' +
        (on ? '<span class="check-fill"></span>' : '') +
        '</button>';
      box.appendChild(row);
    });

    $('deploy-hint').textContent = sel.deployed
      ? 'Live in room ' + roomCode() + ' — it appears in the participants rail and can be staged.'
      : 'Not in the room yet. Deploying attaches it to the live session immediately.';
    show($('recall'), sel.deployed);
    show($('deploy'), !sel.deployed);

    $('preview-line').textContent = sel.preview;
  }

  // ── render ───────────────────────────────────────────────────────────────

  function render() {
    renderHeader();

    show($('screen-join'), !state.joined);
    show($('screen-session'), state.joined && state.tab === 'room');
    show($('screen-studio'), state.joined && state.tab === 'studio');

    var err = $('code-err');
    if (state.codeErrText) err.textContent = state.codeErrText;
    show(err, state.codeErr);
    $('code').setAttribute('aria-invalid', state.codeErr ? 'true' : 'false');

    // Label lives in its own span — the button's other children are the blueprint corner
    // marks, which writing textContent on the button would delete.
    var submit = $('join-form').querySelector('.join-submit');
    if (submit) submit.disabled = state.joining;
    $('join-label').textContent = state.joining ? 'Joining…' : 'Join session';

    if (state.joined) {
      renderSession();
      renderStudio();
    }

    renderToast();
    syncUrl();
  }

  // ── wiring ───────────────────────────────────────────────────────────────

  function closest(node, sel, stop) {
    while (node && node !== stop) {
      if (node.matches && node.matches(sel)) return node;
      node = node.parentNode;
    }
    return null;
  }

  function init() {
    $('join-form').addEventListener('submit', function (e) { e.preventDefault(); join(); });
    $('code').addEventListener('input', function (e) {
      var el = e.target;
      var pos = el.selectionStart;
      el.value = el.value.toUpperCase();
      el.setSelectionRange(pos, pos);
      if (state.codeErr) { state.codeErr = false; render(); }
    });

    $('leave').addEventListener('click', leave);
    all('.tab').forEach(function (btn) {
      btn.addEventListener('click', function () { goTab(btn.dataset.tab); });
    });

    // Participants rail — one listener, two targets: the tile stages, the × recalls.
    $('participants').addEventListener('click', function (e) {
      var rm = closest(e.target, '[data-recall]', this);
      if (rm) {
        e.stopPropagation();
        var tile = closest(rm, '.pt', this);
        var name = tile ? tile.querySelector('.pt-name').textContent : 'Agent';
        recall(rm.dataset.recall, name);
        return;
      }
      var pt = closest(e.target, '[data-stage]', this);
      if (pt) stageOn(pt.dataset.stage);
    });
    $('participants').addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var pt = closest(e.target, '[data-stage]', this);
      if (!pt) return;
      e.preventDefault();
      stageOn(pt.dataset.stage);
    });

    $('overlays').addEventListener('click', function (e) {
      var btn = closest(e.target, '[data-overlay]', this);
      if (!btn) return;
      var k = btn.dataset.overlay;
      state[k] = !state[k];
      render();
    });

    $('add-agent').addEventListener('click', function () { goTab('studio'); });
    $('new-agent').addEventListener('click', newAgent);
    $('delete-agent').addEventListener('click', deleteAgent);
    $('deploy').addEventListener('click', deploy);
    $('recall').addEventListener('click', function () {
      var sel = selected();
      recall(sel.id, sel.name);
    });

    $('library').addEventListener('click', function (e) {
      var btn = closest(e.target, '[data-select]', this);
      if (btn) { state.selId = btn.dataset.select; render(); }
    });

    $('voice').addEventListener('click', function (e) {
      var btn = closest(e.target, '[data-voice]', this);
      if (btn) { patchSelected({ voice: btn.dataset.voice }); render(); }
    });

    $('triggers').addEventListener('click', function (e) {
      var btn = closest(e.target, '[data-trigger]', this);
      if (!btn) return;
      var sel = selected();
      var next = Object.assign({}, sel.triggers);
      next[btn.dataset.trigger] = !next[btn.dataset.trigger];
      patchSelected({ triggers: next });
      render();
    });

    $('agent-name').addEventListener('input', function (e) {
      patchSelected({ name: e.target.value });
      render();
    });
    $('agent-instructions').addEventListener('input', function (e) {
      patchSelected({ instructions: e.target.value });
      render();
    });

    window.addEventListener('hashchange', function () {
      if (location.hash === path()) return;
      stopTimer();
      state.joined = false;
      state.tab = 'room';
      readUrl();
      render();
    });

    // A #/room/CODE link joins directly, but the transport is a module: module scripts run
    // after this classic one and before DOMContentLoaded, so `portal-live-ready` can fire
    // before init() exists to hear it. Listen *and* check whether it already arrived.
    window.addEventListener('portal-live-ready', autoJoin);

    readUrl();
    render();
    if (window.PortalLive) autoJoin();
  }

  /* Connect a hash-route join once the transport is available. */
  function autoJoin() {
    if (!window.PortalLive || !state.joined || !state.code || state.live.connected) return;
    if (state.joining) return;
    state.joining = true;
    window.PortalLive.connect(state.code, { onUpdate: onLiveUpdate }).then(function (result) {
      state.joining = false;
      if (result.ok) {
        state.elapsed = 0;
        state.stage = defaultStage();
      } else {
        // Surfaced rather than swallowed: a hash-route join that fails silently looks
        // identical to the demo roster, which is how a broken transport goes unnoticed.
        state.liveError = result.error;
        toast(result.error);
      }
      render();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
