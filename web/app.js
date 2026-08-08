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

  var DEFAULT_CODE = 'K7F-3QD';
  var ROOM_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789';
  var ROOM_CODE_LENGTH = 6;
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
    live: {
      active: false, connected: false, connectionState: 'disconnected',
      roster: [], hasOperatorVideo: false, camOn: false, micOn: false
    },
    liveError: '',
    elapsed: 0,
    tab: 'room',
    stage: 'op',
    toast: '',
    /* The agent's last grading message, or null when the sheet is closed. Set from the
       LiveKit data topic, never from the portal itself — the portal does not grade. */
    grade: null,
    ovMarks: true,
    ovGrid: false,
    selId: 'assist',
    seq: 1,
    agents: [
      {
        id: 'assist', name: 'Assembly Assistant', custom: false, deployed: true, voice: 'calm',
        instructions: 'Follow the work order step by step. Confirm each step out loud when it looks complete, and call out the next one. Keep it to one sentence.',
        triggers: { capture: false, interval: true, ask: true },
        preview: 'Step 4 confirmed — torque check on the rear bracket passed.'
      },
      {
        id: 'inspect', name: 'Inspection Logger', custom: false, deployed: true, voice: 'muted',
        instructions: 'Watch for defects, wear and misalignment. Flag anything suspect and file a still for it.',
        triggers: { capture: true, interval: false, ask: false },
        preview: 'Thread wear flagged on the M6 fastener. Still filed.'
      },
      {
        id: 'parts', name: 'Parts Spotter', custom: false, deployed: false, voice: 'brisk',
        instructions: 'Identify parts in view and pull their spec: part number, torque, and the tool needed. Answer only when asked.',
        triggers: { capture: false, interval: false, ask: true },
        preview: 'M6×20 socket cap — 9 N·m, 5 mm hex.'
      }
    ],
    people: [
      { id: 'op', name: 'Operator', role: 'Ray-Ban Meta · POV', kind: 'op', speaking: false },
      { id: 'me', name: 'You', role: 'Viewer · this browser', kind: 'viewer', speaking: false },
      { id: 'dana', name: 'Dana R.', role: 'Viewer · shop lead', kind: 'viewer', speaking: true }
    ]
  };

  /* Viewer-side only: these draw over the feed in this browser and change nothing for
     anyone else. Segment Anything was here too, but nothing in the stack produces
     segmentation — drawn over a real operator feed its shapes claimed detections that were
     not happening, so it is gone rather than mislabelled. */
  var OVERLAYS = [
    { key: 'ovMarks', label: 'Viewfinder marks' },
    { key: 'ovGrid', label: 'Thirds grid' }
  ];

  var TRIGGERS = [
    // "On new mask" lived here, fired by Segment Anything. That overlay is gone — there is
    // no segmentation anywhere in the stack — so the trigger went with it rather than
    // remaining as an option that could never fire.
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

  function svg(paths) {
    return '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      paths + '</svg>';
  }

  // Lucide: mic / mic-off drive your own row, volume-2 / volume-x everyone else's — the same
  // distinction a call makes between "I am not speaking" and "I cannot hear you".
  var ICON_MIC = svg(
    '<path d="M12 2a3 3 0 0 1 3 3v7a3 3 0 0 1-6 0V5a3 3 0 0 1 3-3z"></path>' +
    '<path d="M19 10v2a7 7 0 0 1-14 0v-2"></path><path d="M12 19v3"></path>');

  var ICON_MIC_OFF = svg(
    '<path d="M2 2l20 20"></path><path d="M15 9.34V5a3 3 0 0 0-5.68-1.33"></path>' +
    '<path d="M9 9v3a3 3 0 0 0 5.12 2.12"></path><path d="M19 10v2a7 7 0 0 1-.11 1.23"></path>' +
    '<path d="M5 10v2a7 7 0 0 0 12 5"></path><path d="M12 19v3"></path>');

  var ICON_SPEAKER = svg(
    '<path d="M11 5 6 9H2v6h4l5 4z"></path><path d="M16 9a5 5 0 0 1 0 6"></path>' +
    '<path d="M19.4 5.6a9 9 0 0 1 0 12.8"></path>');

  var ICON_SPEAKER_OFF = svg(
    '<path d="M11 5 6 9H2v6h4l5 4z"></path><path d="M22 9l-6 6"></path><path d="M16 9l6 6"></path>');

  // Lucide: video / video-off, for publishing this browser's own camera.
  var ICON_CAM = svg(
    '<path d="m22 8-6 4 6 4V8Z"></path><rect x="2" y="6" width="14" height="12" rx="2"></rect>');

  var ICON_CAM_OFF = svg(
    '<path d="M2 2l20 20"></path><path d="M10.66 6H14a2 2 0 0 1 2 2v3.34l1 1L22 8v8"></path>' +
    '<path d="M16 16a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h2"></path>');

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
  function isLiveRoom() { return !!state.live.active; }

  function canonicalRoomCode(code) {
    return String(code || '').toUpperCase().split('').filter(function (character) {
      return ROOM_ALPHABET.indexOf(character) !== -1;
    }).join('');
  }

  function selected() {
    var byId = state.agents.filter(function (a) { return a.id === state.selId; })[0];
    return byId || state.agents[0];
  }

  /* Roster: people first, then every deployed agent as a participant.
     Agent participant ids are namespaced so a person can't collide with one.
     In a live room the participants are the roster — the seeded people and the studio's
     deployed agents are the standing-in demo. */
  function roster() {
    if (isLiveRoom()) return state.live.roster;

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
    if (!hit && isLiveRoom() && state.stage === 'waiting-op') {
      return {
        id: 'waiting-op', name: 'Operator', role: 'Waiting for POV',
        isOp: true, isViewer: false, isAgent: false, speaking: false, initials: 'OP'
      };
    }
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

  // ── grade sheet ──────────────────────────────────────────────────────────
  /* The agent grades a still of the operator's work against a written rubric and
     publishes the result on a LiveKit data topic. This draws it over the stage:
     which criteria passed, which failed, and — the distinction that matters to a
     student — whether a failure was bad work or an unusable photograph.

     `state.grade` holds the last message received. A `grading` message opens the
     sheet with the criteria greyed out and no verdicts, so the seconds the model
     spends thinking read as work in progress rather than a request that went
     nowhere. */

  var GRADE_DISMISS_MS = 45000;
  var gradeTimer = null;

  function onAgentData(message) {
    if (!message || (message.type !== 'grade' && message.type !== 'grading')) return;
    state.grade = message;
    clearTimeout(gradeTimer);
    // A finished grade clears itself so the stage is not permanently covered; one still
    // running does not, because there is no telling how long the model will take.
    if (message.type === 'grade') {
      gradeTimer = setTimeout(function () { dismissGrade(); }, GRADE_DISMISS_MS);
    }
    renderGrade();
  }

  function dismissGrade() {
    clearTimeout(gradeTimer);
    state.grade = null;
    renderGrade();
  }

  function gradeRow(item, pending) {
    var li = document.createElement('li');
    li.className = 'grade-item' + (pending ? ' pending' : ' ' + String(item.verdict).toLowerCase());
    // A criterion that failed for want of a view is not the same as work that is wrong,
    // and a student can only act on the first by taking another photograph.
    if (!pending && item.verdict === 'FAIL' && item.observable === false) li.className += ' unseen';

    var mark = document.createElement('span');
    mark.className = 'grade-mark';
    mark.textContent = pending ? '·'
      : item.verdict === 'PASS' ? '✓'
      : item.observable === false ? '?' : '✕';
    mark.setAttribute('aria-hidden', 'true');

    var text = document.createElement('span');
    text.className = 'grade-text';
    text.textContent = item.text;

    li.appendChild(mark);
    li.appendChild(text);

    if (!pending && item.note) {
      var note = document.createElement('span');
      note.className = 'grade-item-note';
      note.textContent = item.note;
      li.appendChild(note);
    }
    // Screen readers get the verdict in words; the glyph alone is not a verdict.
    li.setAttribute('aria-label',
      (pending ? 'Not yet graded' : item.verdict === 'PASS' ? 'Passed'
        : item.observable === false ? 'Failed, not shown in the photo' : 'Failed')
      + ': ' + item.text);
    return li;
  }

  function renderGrade() {
    var sheet = $('grade-sheet');
    if (!sheet) return;
    var g = state.grade;
    show(sheet, !!g);
    if (!g) return;

    var pending = g.type === 'grading';
    var failed = g.error ? null : (g.criteria || []).filter(function (c) { return c.verdict === 'FAIL'; });

    var verdict = $('grade-verdict');
    verdict.textContent = pending ? 'GRADING…' : g.error ? 'UNAVAILABLE' : g.overall;
    verdict.className = 'grade-verdict ' + (pending ? 'pending' : g.error ? 'error' : String(g.overall).toLowerCase());

    $('grade-subtask').textContent = g.subtask || g.subtask_code || '—';
    $('grade-task').textContent = [g.task_code, g.task_title].filter(Boolean).join(' · ');
    $('grade-score').textContent = pending || g.error ? ''
      : g.passed + '/' + g.total;

    var observed = $('grade-observed');
    observed.textContent = g.error ? (g.message || '') : (g.observed || '');
    show(observed, !!observed.textContent);

    var list = $('grade-list');
    list.innerHTML = '';
    (g.criteria || []).forEach(function (item) { list.appendChild(gradeRow(item, pending)); });

    var defects = g.critical_defects || [];
    var defectList = $('grade-defect-list');
    defectList.innerHTML = '';
    defects.forEach(function (d) {
      var li = document.createElement('li');
      li.textContent = d;
      defectList.appendChild(li);
    });
    show($('grade-defects'), defects.length > 0);

    // The rubrics are machine-drafted and unreviewed. Saying so on the sheet itself is the
    // only place it reliably reaches whoever is reading the verdict.
    var note = $('grade-note');
    if (pending) note.textContent = 'Reading the frame…';
    else if (g.error) note.textContent = '';
    else if (failed && failed.length && failed.every(function (c) { return c.observable === false; })) {
      note.textContent = 'Everything that failed did so because the photo does not show it.';
    } else note.textContent = 'Machine-drafted rubric — a first opinion, not a final mark.';

    var meta = [];
    if (!pending && !g.error) {
      if (g.model) meta.push(g.model);
      if (g.latency_s) meta.push(g.latency_s + 's');
      if (g.frame && g.frame.width) meta.push(g.frame.width + '×' + g.frame.height);
    }
    $('grade-meta').textContent = meta.join(' · ');
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
    // An empty field intentionally opens the seeded demo. Never turn a placeholder into a
    // real LiveKit room (and a camera permission prompt) just because the SDK loaded.
    if (!raw.trim()) {
      state.code = DEFAULT_CODE;
      enterSession();
      return;
    }
    if (canonicalRoomCode(code).length !== ROOM_CODE_LENGTH) {
      state.codeErr = true;
      state.codeErrText = 'Enter the full six-character code — two groups, like K7F-3QD.';
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

    window.PortalLive.connect(code, {
      onUpdate: onLiveUpdate,
      onData: onAgentData,
      publishLocalMedia: true
    }).then(function (result) {
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
    state.elapsed = isLiveRoom() ? 0 : JOINED_ELAPSED;
    state.tab = 'room';
    state.stage = defaultStage();
    startTimer();
    render();
  }

  /* Stage the operator when there is one — that is what a viewer came to watch. */
  function defaultStage() {
    var op = roster().filter(function (r) { return r.isOp; })[0];
    if (op) return op.id;
    if (isLiveRoom()) return 'waiting-op';
    return (roster()[0] || {}).id || 'op';
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
    state.live = {
      active: false, connected: false, connectionState: 'disconnected',
      roster: [], hasOperatorVideo: false, camOn: false, micOn: false
    };
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
      instructions: '', triggers: { capture: false, interval: false, ask: true },
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

  /* Per-participant audio, the way a call does it: your own row toggles your microphone,
     everyone else's toggles whether you hear them. Muting someone here is local — the room
     still hears them, which is the one thing a call UI must not be ambiguous about. */
  function audioControl(r) {
    if (!state.live.connected) return '';

    if (r.isLocal) {
      var on = !!state.live.micOn;
      return '<button class="pt-audio' + (on ? '' : ' off') + '" type="button" data-mic="1"' +
        ' aria-pressed="' + on + '"' +
        ' aria-label="' + (on ? 'Mute your microphone' : 'Unmute your microphone') + '"' +
        ' title="' + (on ? 'Mute yourself' : 'Unmute yourself') + '">' +
        (on ? ICON_MIC : ICON_MIC_OFF) + '</button>';
    }

    if (!r.hasAudio) return '';
    var heard = !r.mutedByMe;
    return '<button class="pt-audio' + (heard ? '' : ' off') + '" type="button"' +
      ' data-mute="' + esc(r.id) + '" aria-pressed="' + !heard + '"' +
      ' aria-label="' + (heard ? 'Mute ' : 'Unmute ') + esc(r.name) + ' for you"' +
      ' title="' + (heard ? 'Mute for you only' : 'Unmute') + '">' +
      (heard ? ICON_SPEAKER : ICON_SPEAKER_OFF) + '</button>';
  }

  // ── render: session ──────────────────────────────────────────────────────

  /* Participant tiles are reused across renders, keyed by identity.
     Rebuilding them with innerHTML — as every other list here does — would tear down the
     <video> elements on every roster change and restart playback, so each tile is built once
     and updated in place. This is the reconciliation React gives Meet for free. */
  var tiles = Object.create(null);

  function buildTile(r) {
    var el = document.createElement('div');
    el.className = 'pt';
    el.setAttribute('role', 'button');
    el.setAttribute('tabindex', '0');
    el.dataset.stage = r.id;
    el.innerHTML =
      '<span class="pt-avatar">' +
        '<video class="pt-video" autoplay playsinline muted hidden></video>' +
        '<img class="pt-still" src="assets/plant.png" alt="" hidden>' +
        '<span class="pt-glyph"></span>' +
        '<span class="pt-speaking" hidden></span>' +
      '</span>' +
      '<span class="pt-id">' +
        '<span class="pt-name"></span>' +
        '<span class="pt-role"></span>' +
      '</span>' +
      '<span class="pt-tail"></span>';
    return el;
  }

  function updateTile(el, r) {
    var onStage = state.stage === r.id;
    el.classList.toggle('on-stage', onStage);
    el.setAttribute('aria-pressed', onStage ? 'true' : 'false');

    el.querySelector('.pt-name').textContent = r.name;
    el.querySelector('.pt-role').textContent = r.role;
    el.querySelector('.pt-speaking').hidden = !r.speaking;

    var video = el.querySelector('.pt-video');
    var still = el.querySelector('.pt-still');
    var glyph = el.querySelector('.pt-glyph');

    // Whoever is actually sending video gets shown; the rest fall back the way they did.
    var playing = r.hasVideo && window.PortalLive
      ? window.PortalLive.attachVideo(r.id, video)
      : false;
    video.hidden = !playing;
    // The demo still belongs only to the offline operator — never to a live room.
    still.hidden = playing || !r.isOp || isLiveRoom();
    glyph.hidden = playing || !still.hidden;
    if (!glyph.hidden) {
      glyph.innerHTML = r.isAgent ? ICON_AGENT_TILE : '';
      if (!r.isAgent) glyph.textContent = r.initials;
      glyph.className = 'pt-glyph' + (r.isAgent ? '' : ' pt-initials');
    }

    el.querySelector('.pt-tail').innerHTML =
      (onStage ? '<span class="tag tag-accent pt-badge">ON STAGE</span>' : '') +
      audioControl(r) +
      (r.isAgent && r.agentId
        ? '<button class="pt-remove" type="button" data-recall="' + esc(r.agentId) + '"' +
          ' aria-label="Remove ' + esc(r.name) + ' from the room">' + ICON_CLOSE + '</button>'
        : '');
  }

  /* Your own camera and mic, the two controls every call UI puts within reach.
     Hidden entirely offline: there is nothing to publish to a demo roster. */
  function renderMediaControls() {
    var bar = $('media-controls');
    if (!bar) return;
    show(bar, state.live.connected);
    if (!state.live.connected) return;

    var mic = $('toggle-mic');
    var cam = $('toggle-cam');
    mic.setAttribute('aria-pressed', state.live.micOn ? 'true' : 'false');
    cam.setAttribute('aria-pressed', state.live.camOn ? 'true' : 'false');
    mic.innerHTML = (state.live.micOn ? ICON_MIC : ICON_MIC_OFF) +
      '<span>' + (state.live.micOn ? 'Mic on' : 'Mic off') + '</span>';
    cam.innerHTML = (state.live.camOn ? ICON_CAM : ICON_CAM_OFF) +
      '<span>' + (state.live.camOn ? 'Camera on' : 'Camera off') + '</span>';
  }

  function renderParticipants(list) {
    var container = $('participants');
    var seen = Object.create(null);

    list.forEach(function (r, index) {
      var el = tiles[r.id];
      if (!el) { el = buildTile(r); tiles[r.id] = el; }
      updateTile(el, r);
      seen[r.id] = true;
      if (container.children[index] !== el) {
        container.insertBefore(el, container.children[index] || null);
      }
    });

    Object.keys(tiles).forEach(function (id) {
      if (seen[id]) return;
      if (tiles[id].parentNode) tiles[id].parentNode.removeChild(tiles[id]);
      delete tiles[id];
    });
  }

  function renderSession() {
    var list = roster();
    var st = staged();

    show($('stage-op'), !!st && st.isOp);
    show($('stage-viewer'), !!st && st.isViewer);
    show($('stage-agent'), !!st && st.isAgent);

    // Three states, deliberately distinct: a real track, a live room with nobody publishing,
    // and the offline demo. The middle one must never borrow the demo's still — a stand-in
    // frame on a live connection is indistinguishable from a working feed.
    var liveRoom = isLiveRoom();
    var liveVideo = liveRoom && state.live.connected && state.live.hasOperatorVideo;
    var liveWaiting = liveRoom && !liveVideo;

    show($('stage-video'), liveVideo);
    show($('stage-img'), !liveRoom);
    show($('stage-waiting'), liveWaiting && !!st && st.isOp);
    if (liveVideo) window.PortalLive.attachOperatorVideo($('stage-video'));

    if (liveWaiting) {
      var hasOperator = state.live.roster.some(function (r) { return r.isOp; });
      $('stage-waiting-note').textContent = !state.live.connected
        ? 'Connection interrupted — LiveKit is trying to reconnect…'
        : hasOperator
          ? 'Operator is here but not publishing yet…'
          : 'Waiting for the operator to join ' + roomCode() + '…';
    }

    var source = $('screen-session').querySelector('.stage-source');
    if (source) {
      source.textContent = liveRoom
        ? (liveVideo ? 'OPERATOR POV · LIVE' : 'ROOM ' + roomCode() + ' · NO FEED YET')
        : 'OPERATOR POV · RAY-BAN META (DEMO)';
    }

    // A staged viewer shows their camera when they are sending one — the design's
    // "viewers don't broadcast" placeholder only holds when they genuinely aren't.
    if (st && st.isViewer) {
      var viewerVideo = $('stage-viewer-video');
      var viewerPlaying = st.hasVideo && window.PortalLive
        ? window.PortalLive.attachVideo(st.id, viewerVideo)
        : false;
      show(viewerVideo, viewerPlaying);
      show($('staged-initials'), !viewerPlaying);
      show($('staged-note'), !viewerPlaying);
      $('staged-initials').textContent = st.initials;
      $('staged-note').textContent = liveRoom
        ? (st.isLocal ? 'Your camera is off' : st.name + ' has their camera off')
        : 'No video — this is the seeded demo roster';
    }
    if (st && st.isAgent) $('staged-name').textContent = st.name;

    renderMediaControls();

    show($('ov-marks'), state.ovMarks);
    show($('ov-grid'), state.ovGrid);

    $('elapsed').textContent = elapsedText();
    $('participant-count').textContent = list.length + ' in room';
    $('rail-note').textContent = liveRoom
      ? 'Camera and microphone share only while enabled · capture stays with the operator'
      : 'Demo preview · capture stays with the operator';

    renderParticipants(list);

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

    renderGrade();
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
      // Audio controls sit inside a tile that also stages on click, so they claim the event.
      var mic = closest(e.target, '[data-mic]', this);
      if (mic) {
        e.stopPropagation();
        window.PortalLive.setMicrophone(!state.live.micOn);
        return;
      }
      var mute = closest(e.target, '[data-mute]', this);
      if (mute) {
        e.stopPropagation();
        var id = mute.dataset.mute;
        var row = state.live.roster.filter(function (p) { return p.id === id; })[0];
        window.PortalLive.setParticipantMuted(id, !(row && row.mutedByMe));
        return;
      }

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
      if (closest(e.target, 'button', this)) return;
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

    // Toggling publishes or unpublishes for real; the bridge pushes a fresh snapshot back,
    // so the button label follows the room rather than an optimistic local guess.
    $('toggle-mic').addEventListener('click', function () {
      if (window.PortalLive) window.PortalLive.setMicrophone(!state.live.micOn);
    });
    $('toggle-cam').addEventListener('click', function () {
      if (window.PortalLive) window.PortalLive.setCamera(!state.live.camOn);
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

    $('grade-close').addEventListener('click', dismissGrade);
    // Escape closes the sheet. It sits over the stage rather than trapping focus, so the
    // usual dialog dismissal is the one thing that has to work.
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && state.grade) dismissGrade();
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
    if (!window.PortalLive || !state.joined || !state.code || isLiveRoom()) return;
    if (state.joining) return;
    state.joining = true;
    window.PortalLive.connect(state.code, {
      onUpdate: onLiveUpdate,
      onData: onAgentData,
      // A shared link is not consent to turn on this browser's camera and microphone.
      publishLocalMedia: false
    }).then(function (result) {
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
