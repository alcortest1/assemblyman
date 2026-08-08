/* AIM Inspector — data inspection and evals for the Alcor × AIM Fremont pilot.
 *
 * Ported from the Claude Design source "AIM Inspector.dc.html". The screens, copy,
 * state shape and interaction rules follow that design; the browser-chrome frame it
 * was mocked inside is dropped, since here the browser is the browser. The design's
 * `url` value drives the real address bar instead, so a screen is linkable.
 *
 * No build step and no dependencies: index.html holds the shell, this file owns
 * state and builds the screens. Everything rendered comes from the extract under
 * data/, which scripts/build_portal_data.py writes out of the alcor_agents working
 * tree — compiled packs, drafted criteria, and the saved photo-eval runs with both
 * polarities. Nothing here is seeded; when a figure is missing it is missing.
 */
(function () {
  'use strict';

  /* ── DOM helpers ───────────────────────────────────────────────────────── */

  function append(node, children) {
    if (children === null || children === undefined || children === false) return;
    if (Array.isArray(children)) {
      for (var i = 0; i < children.length; i++) append(node, children[i]);
      return;
    }
    node.appendChild(children.nodeType ? children : document.createTextNode(String(children)));
  }

  function el(tag, props, children) {
    var node = document.createElement(tag);
    if (props) {
      for (var k in props) {
        var v = props[k];
        if (v === null || v === undefined || v === false) continue;
        if (k === 'class') node.className = v;
        else if (k === 'text') node.textContent = v;
        else if (k === 'on') { for (var e in v) node.addEventListener(e, v[e]); }
        else node.setAttribute(k, v === true ? '' : v);
      }
    }
    append(node, children);
    return node;
  }

  // Inline SVG: parsed through a wrapper so the markup stays readable here.
  function svg(markup) {
    var box = document.createElement('div');
    box.innerHTML = markup;
    return box.firstElementChild;
  }

  // The blueprint frame's registration marks.
  function corners() {
    return ['tl', 'tr', 'bl', 'br'].map(function (p) {
      return el('i', { class: 'corner ' + p });
    });
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  var WARN_ICON =
    '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--color-accent-800)" ' +
    'stroke-width="1.5" aria-hidden="true"><path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"></path></svg>';

  function crosshair(size) {
    return svg(
      '<svg width="' + size + '" height="' + size + '" viewBox="0 0 24 24" fill="none" ' +
      'stroke="rgba(255,255,255,0.5)" stroke-width="1.5" aria-hidden="true"><path d="M12 2v20M2 12h20"></path></svg>'
    );
  }

  function tag(cls, text, extra) {
    return el('span', { class: cls + ' ' + (extra || 'tag-xs'), text: text });
  }

  /* ── data ──────────────────────────────────────────────────────────────── */

  /* The extract under data/ is built from the alcor_agents working tree by
     scripts/build_portal_data.py — packs, drafted criteria and the saved
     photo-eval runs. index.json and evals.json load at boot; a task's steps,
     criteria and run load the first time that task is opened. */

  var DATA = { index: null, evals: null, tasks: {}, pending: {}, error: null };

  function getJSON(path) {
    return fetch(path, { cache: 'no-cache' }).then(function (r) {
      if (!r.ok) throw new Error(path + ' \u2192 ' + r.status);
      return r.json();
    });
  }

  function taskList() { return (DATA.index && DATA.index.tasks) || []; }
  function modelNames() { return (DATA.index && DATA.index.models) || []; }

  function ensureTask(code) {
    if (!code || DATA.tasks[code] || DATA.pending[code]) return;
    DATA.pending[code] = true;
    getJSON('data/tasks/' + encodeURIComponent(code) + '.json')
      .then(function (t) { DATA.tasks[code] = t; })
      .catch(function (e) { DATA.error = String((e && e.message) || e); })
      .then(function () { delete DATA.pending[code]; render(); });
  }

  // Graded frames are copied at full size; the Videos strip is downscaled. Try the
  // sharp one first and fall back, so a frame we did not copy just shows the plate.
  function plateImage(sources, alt) {
    // No frame recorded — the plate stands on its own rather than showing a broken img.
    if (!sources || !sources.length) return null;
    var i = 0;
    var img = el('img', { class: 'plate-img', alt: alt || '', loading: 'lazy', src: sources[0] });
    img.addEventListener('error', function () {
      i += 1;
      if (i < sources.length) img.src = sources[i];
      else if (img.parentNode) img.parentNode.removeChild(img);
    });
    return img;
  }

  function framePaths(code, video, file) {
    if (!code || !video || !file) return [];
    var enc = encodeURIComponent(code) + '/' + encodeURIComponent(video) + '/' + encodeURIComponent(file);
    return ['data/frames/' + enc, 'data/thumbs/' + enc];
  }


  // No placeholder criterion set: a subtask with nothing compiled says so. The four
  // generic points that used to stand in here read exactly like compiled text.
  var NO_POINTS_NOTE = 'No compiled criterion for this subtask — no entry in ' +
    'build/criteria/ and no saved run to draw points from.';

  // Mirrors SAMPLE_FPS in scripts/build_portal_data.py, which picks the frames this
  // reads. A rate, not a count: the Videos tab's 16-frame strip means a different
  // thing on a 28 s clip than on a 105 s one, and a sampled sequence must not.
  var SAMPLE_FPS_LABEL = '0.5 fps';

  /* ── state ─────────────────────────────────────────────────────────────── */

  var state = {
    nav: 'home',        // 'home' | 'task' | 'evals'
    taskCode: null,
    tab: 'detail',      // 'detail' | 'assess' | 'vassess' | 'videos' | 'docs'
    sub: 0,
    expanded: null,     // step id whose checks/errors are open
    clipIdx: 0,
    frameIdx: null,     // null → the clip's last frame
    focusOn: false,     // Videos tab: the area-of-focus editor replaces the plate
    reply: null,        // 'r<row>m<model>' | 'n<row>m<model>'
    doc: 0,
    drafted: null,      // '<taskCode>#<subIdx>' once a perturbed sheet is drafted
    showConfidence: true
  };

  function setState(patch) {
    Object.assign(state, patch);
    render();
  }

  // The index carries every task's headline counts; the full pack arrives per task.
  function findTask(code) {
    var list = taskList();
    for (var i = 0; i < list.length; i++) if (list[i].code === code) return list[i];
    return null;
  }

  function fullTask(code) { return DATA.tasks[code] || null; }

  function openTask(code, tab) {
    ensureTask(code);
    setState({
      nav: 'task', taskCode: code, tab: tab || 'detail', sub: 0,
      expanded: null, clipIdx: 0, frameIdx: null, reply: null, doc: 0,
      focusOn: false
    });
  }

  /* ── derivations ───────────────────────────────────────────────────────── */

  function subIndex(task) { return Math.min(state.sub, task.subtasks.length - 1); }

  /* The subtasks grouped by the clip they are graded on, in first-appearance order.
     Both assessment screens are asking a question about a clip — Photo assessment
     about its last frame, Video assessment about a sequence sampled across it — so
     the clip is the axis to pick along. AM.I.D.S1's thirty subtasks are seven clips
     carrying three to five spans each, and a thirty-cell rail makes you scroll past
     four spans of bending to reach the flare.

     A subtask the run recorded no clip against collects into a trailing group rather
     than being dropped: four tasks have one, AM.III.M.S5 is nothing but those, and a
     rail keyed on clips would otherwise make a compiled criterion unreachable. */
  function clipGroups(task) {
    var order = [], byClip = {}, loose = [];
    task.subtasks.forEach(function (s, i) {
      if (!s.frameVideo) { loose.push(i); return; }
      if (!byClip[s.frameVideo]) { byClip[s.frameVideo] = []; order.push(s.frameVideo); }
      byClip[s.frameVideo].push(i);
    });
    var groups = order.map(function (clip) { return { clip: clip, subs: byClip[clip] }; });
    if (loose.length) groups.push({ clip: null, subs: loose });
    return groups;
  }

  function groupOf(groups, sub) {
    for (var i = 0; i < groups.length; i++) {
      if (groups[i].subs.indexOf(sub) !== -1) return i;
    }
    return 0;
  }

  function clipsLabel(t) {
    return t.clips ? t.clips + (t.clips === 1 ? ' clip' : ' clips') : 'no video';
  }

  function cellCls(v) {
    return v === 'pass' ? 'tag tag-accent'
      : v === 'fail' ? 'tag tag-neutral'
      : v === 'accepted' ? 'tag tag-accent-2'
      : 'tag tag-outline';
  }

  function cellTxt(v, x) {
    if (v === 'accepted') return x;
    if (v === 'none') return x;            // ungraded — never dressed up as a verdict
    if (x === '✓') return v + ' ✓';
    if (v === 'unsure') return x.indexOf('not_pass') === 0 ? 'unsure ' + x : 'unsure · ' + x;
    return state.showConfidence ? v + ' · ' + x : v;
  }

  // Points a subtask's sheet is graded on. Empty where nothing is compiled.
  function pointsOf(st) { return st.sheetPoints || []; }

  function subtaskView(task, i) {
    var st = task.subtasks[i];
    var points = pointsOf(st);
    var hasRun = !!st.run;
    return {
      raw: st,
      num: String(i + 1).padStart(2, '0'),
      label: st.label, sheet: st.sheet, stepsCount: String(st.stepsCount),
      hasSteps: !!st.steps,
      points: points,
      excluded: st.excluded || '[measurement] and [document] checks stay beside the frame, never in the criterion.',
      // A subtask no run covered has no frame at all. Saying so beats naming one:
      // the fallback built a filename out of a field the extract does not carry
      // and the plate read "undefined.jpg".
      frameProv: st.frameProv ||
        (st.frameFile ? (task.segmented ? 'frame_reviewed' : 'frame_suggested') : 'no frame chosen'),
      frameFile: st.frameFile || (task.clips ? '— no frame chosen —' : '— no source video —'),
      frameShort: st.frameShort ||
        (task.clips ? 'pick one from ' + clipsLabel(task) : 'take one from the picker'),
      frameNote: st.frameNote || (!st.frameFile
        ? 'No frame: the latest run did not grade this subtask, so none was recorded.'
        : task.segmented
        ? 'Reviewed interval: names both the clip and the frame the work ended on.'
        : task.clips ? 'Even-pace guess along the clip — not a reviewed interval.'
        : 'No source video ("not AIM developed"). The criterion exists regardless.'),
      refs: st.refs || (task.clips ? task.clips + ' clips' : 'none'),
      runs: st.runs || 'none',
      hasRun: hasRun,
      controlStats: hasRun ? st.run.controlStats : ''
    };
  }

  function runCostText(points, keptCount, haveSheet) {
    if (!points.length) return NO_POINTS_NOTE;
    if (!haveSheet) {
      var total = points.length * 4;
      return points.length + ' points, × 4 models = ' + total + ' calls · ~$' +
        (total * 0.028).toFixed(2) +
        ' — no perturbed sheet yet; drafting one adds 1 call, then its kept lines are graded alongside.';
    }
    var n = (points.length + keptCount) * 4;
    return points.length + ' points + ' + keptCount + ' kept perturbations, × 4 models = ' +
      n + ' calls · ~$' + (n * 0.028).toFixed(2);
  }

  /* A control is the SAME criterion with its stated standard altered so correct work no
     longer meets it — 6–8 twists per inch becomes 2–4, square becomes chamfered, round
     becomes flattened. Same subject, same evidence, a specification the work does not
     satisfy. Two shapes are refused: a perturbation only a measurement could settle (it
     measures the framing, not the grader) and one loose enough that correct work still
     passes — a control is the same bar moved, never a lower one. */
  var PERTURB_SUBS = [
    [/every fitting, fastener and termination the sheet calls for is in place on the finished work/i,
     'at least one fitting or fastener the sheet calls for is deliberately left off the finished work'],
    [/clean and free of burrs, swarf and tool damage/i, 'left carrying a burr ring at each worked edge'],
    [/identification markings on the installed parts face outward, readable without disturbing the work/i,
     'identification markings on the installed parts face inward, hidden until the work is disturbed'],
    [/\bsquare to\b/i, 'chamfered at 45° to'],
    [/\bremains round\b/i, 'is flattened to a visible oval'],
    [/\bfelt-tip\b/i, 'scribed'],
    [/\bclear of\b/i, 'in contact with'],
    [/\bflush\b/i, 'proud of the surrounding surface']
  ];

  function perturb(s) {
    var body = s.replace(/\.$/, '');
    // Never move a citation: a handbook page span is an address, not a standard.
    var cited = /\d+-\d+\s*\.\.\s*\d+-\d+/.test(body) || /\bhandbook\b|\bFAA-H-\d/i.test(body);
    var range = cited ? null : body.match(/(\d+)\s*(?:–|-|to)\s*(\d+)/);
    if (range) {
      var lo = Math.max(1, Math.round(+range[1] / 3));
      var hi = Math.max(lo + 1, Math.round(+range[2] / 3));
      return { status: 'perturbed', text: body.replace(range[0], lo + '–' + hi) + '.' };
    }
    var num = body.match(/(\d+(?:\/\d+)?(?:\.\d+)?)\s*(in\b|inch(?:es)?|mm|°|degrees?)/i);
    if (num) return { status: 'skipped · needs a scale', text: body + '.' };
    for (var i = 0; i < PERTURB_SUBS.length; i++) {
      var re = PERTURB_SUBS[i][0], rep = PERTURB_SUBS[i][1];
      if (!re.test(body)) continue;
      return { status: 'perturbed', text: body.replace(re, rep) + '.' };
    }
    return { status: 'skipped · nothing to perturb', text: body + '.' };
  }

  function draftPerturbed(points) {
    return points.map(function (p, i) {
      var isDefect = p.n.indexOf('D') === 0;
      var bare = p.text.replace(/^graded as absence:\s*/, '');
      if (isDefect) {
        var stem = bare.replace(/\.$/, '');
        return {
          mark: 'P' + (i + 1), from: p.n + ' ' + p.text,
          text: 'Any trace of ' + stem.charAt(0).toLowerCase() + stem.slice(1) +
                ' is a critical defect, at any severity.',
          status: 'perturbed'
        };
      }
      var r = perturb(bare);
      return { mark: 'P' + (i + 1), from: p.n + ' ' + bare, text: r.text, status: r.status };
    });
  }

  function keptOf(lines) {
    return lines.filter(function (l) { return l.status.indexOf('skipped') !== 0; }).length;
  }

  /* ── render · header + sidebar ─────────────────────────────────────────── */

  var $main = document.getElementById('main');
  var $sidebar = document.getElementById('sidebar-list');
  var $navTasks = document.getElementById('nav-tasks');
  var $navEvals = document.getElementById('nav-evals');
  var $stats = document.getElementById('hdr-stats');

  function renderHeader() {
    $navTasks.setAttribute('aria-selected', String(state.nav !== 'evals'));
    $navEvals.setAttribute('aria-selected', String(state.nav === 'evals'));
    var s = DATA.index && DATA.index.stats;
    $stats.textContent = s
      ? s.tasks + ' TASKS · ' + s.atoms.toLocaleString() + ' ATOMS · ' +
        s.targets.toLocaleString() + ' PHOTO TARGETS · ' + s.reviewed + ' REVIEWED'
      : '';
  }

  var SUBJECT_ORDER = ['General', 'Airframe', 'Powerplant'];

  function renderSidebar() {
    clear($sidebar);
    var task = findTask(state.taskCode);
    var onTask = state.nav === 'task' && !!task;

    SUBJECT_ORDER.forEach(function (name) {
      var inGroup = taskList().filter(function (t) { return t.subject === name; });
      if (!inGroup.length) return;
      var rows = inGroup.map(function (t) {
        var current = onTask && t.code === state.taskCode;
        return el('button', {
          class: 'side-task', type: 'button', 'aria-current': String(current),
          on: { click: function () { openTask(t.code); } }
        }, [
          el('span', { class: 'side-task-top' }, [
            el('span', { class: 'side-task-name', text: t.code + ' · ' + t.short }),
            el('span', { class: 'side-task-atoms', text: String(t.atoms) })
          ]),
          el('span', { class: 'side-task-tags' }, [
            tag(t.hand ? 'tag tag-accent' : 'tag tag-outline', t.hand ? 'hand' : 'draft'),
            tag('tag tag-outline', clipsLabel(t)),
            t.segmented ? tag('tag tag-outline', 'segmented') : null
          ])
        ]);
      });
      append($sidebar, el('div', { class: 'side-group' }, [
        el('div', { class: 'side-group-name', text: name })
      ].concat(rows)));
    });
  }

  /* ── render · home, the task browser ───────────────────────────────────── */

  function renderHome() {
    var cards = taskList().map(function (t) {
      return el('button', {
        class: 'blueprint card-task', type: 'button',
        on: { click: function () { openTask(t.code); } }
      }, [
        corners(),
        el('span', { class: 'card-task-top' }, [
          el('span', { class: 'card-code', text: t.code }),
          tag('tag tag-outline', t.subject)
        ]),
        el('span', { class: 'card-task-title', text: t.title }),
        el('span', { class: 'card-stats' }, [
          el('span', { class: 'stat' }, [
            el('span', { class: 'stat-label', text: 'Steps' }),
            el('span', { class: 'stat-val', text: String(t.steps) })
          ]),
          el('span', { class: 'stat' }, [
            el('span', { class: 'stat-label', text: 'Atoms' }),
            el('span', { class: 'stat-val', text: String(t.atoms) })
          ]),
          el('span', { class: 'stat' }, [
            el('span', { class: 'stat-label', text: 'Targets' }),
            el('span', {
              class: 'stat-val',
              // A hand-compiled pack has no criteria file; its targets come from the run.
              title: 'counted from ' + (t.targetsProv || 'build/criteria/'),
              text: String(t.targets) + (t.targetsProv === 'saved run' ? '*' : '')
            })
          ]),
          el('span', { class: 'stat stat-wide' }, [
            el('span', { class: 'stat-label', text: 'Handbook' }),
            el('span', { class: 'stat-text', text: t.handbook })
          ])
        ]),
        el('span', { class: 'card-tags' }, [
          tag(t.hand ? 'tag tag-accent' : 'tag tag-outline', t.hand ? 'hand-compiled' : 'drafted · unreviewed'),
          tag('tag tag-outline', t.hbProv),
          tag('tag tag-outline', clipsLabel(t))
        ])
      ]);
    });

    return el('div', { class: 'screen' }, [
      el('div', { class: 'screen-head' }, [
        el('h1', { class: 'screen-title', text: 'Pilot tasks' }),
        el('span', { class: 'screen-sub', text: 'FAA ACS tasks selected by AIM Fremont · one sweep drafted, $15 Opus 5 · nothing SME-reviewed yet' })
      ]),
      el('div', { class: 'blueprint notice' }, [
        corners(), svg(WARN_ICON),
        el('span', { class: 'notice-text' }, [
          el('b', { text: 'Every pack is machine-drafted and unreviewed' }),
          ' (reviewed_by: null). A passing grade against these criteria tests the pipeline, not a student. pack_lint --require-reviewed gates them out of live sessions.'
        ])
      ]),
      el('div', { class: 'cards' }, cards)
    ]);
  }

  /* ── render · task detail ──────────────────────────────────────────────── */

  var TABS = [
    { id: 'detail', label: 'Hierarchy' },
    { id: 'assess', label: 'Photo assessment' },
    { id: 'vassess', label: 'Video assessment' },
    { id: 'videos', label: 'Videos & frames' },
    { id: 'docs', label: 'Documentation' }
  ];

  function renderTask(task) {
    var i = subIndex(task);
    var st = subtaskView(task, i);

    var head = el('div', { class: 'task-head' }, [
      el('div', { class: 'task-head-row' }, [
        el('h1', { class: 'task-title', text: task.code + ' — ' + task.title }),
        el('span', {
          class: (task.hand ? 'tag tag-accent' : 'tag tag-neutral') + ' tag-xs',
          text: task.hand ? 'HAND-COMPILED · DRAFT' : 'DRAFTED · UNREVIEWED'
        }),
        el('span', { class: 'task-meta', text: task.subject + ' · ' + task.subtasks.length + ' subtasks' }),
        el('span', { class: 'spacer' }),
        el('span', { class: 'task-hb', text: 'Handbook ' + task.handbook + ' · ' + task.hbProv })
      ]),
      el('div', { class: 'tabs', role: 'tablist', 'aria-label': 'Task views' },
        TABS.map(function (t) {
          return el('button', {
            class: 'tab', type: 'button', role: 'tab',
            'aria-selected': String(state.tab === t.id), text: t.label,
            on: { click: function () { setState({ tab: t.id, reply: null }); } }
          });
        }).concat([
          el('span', { class: 'spacer' }),
          el('span', {
            class: 'tabs-meta',
            text: task.steps + ' steps · ' + task.corr + ' correctness · ' +
                  task.def + ' defect · ' + task.targets + ' photo targets' +
                  (task.targetsProv === 'saved run' ? ' (from the run — no criteria file)' : '')
          })
        ]))
    ]);

    var parts = [head];
    // The assessment screens pick along the clip; the others still pick along the subtask.
    var byClip = state.tab === 'assess' || state.tab === 'vassess';
    if (state.tab !== 'docs') {
      parts.push(byClip ? renderClipRail(task, i, state.tab === 'vassess')
                        : renderRail(task, i));
    }

    if (state.tab === 'detail') parts.push(renderHierarchy(task, st));
    else if (state.tab === 'assess') parts.push(renderAssess(task, st));
    else if (state.tab === 'vassess') parts.push(renderVideoAssess(task, st));
    else if (state.tab === 'videos') parts.push(renderVideos(task));
    else parts.push(renderDocs(task));

    return el('div', { class: 'task' }, parts);
  }

  function renderRail(task, current) {
    return el('div', { class: 'rail' }, task.subtasks.map(function (s, i) {
      return el('button', {
        class: 'rail-cell', type: 'button', 'aria-current': String(i === current),
        on: { click: function () { setState({ sub: i, expanded: null, reply: null }); } }
      }, [
        el('span', { class: 'plate rail-plate' }, [
          crosshair(14),
          plateImage(framePaths(task.code, s.frameVideo, s.frameFile), ''),
          el('span', { class: 'rail-ts', text: (s.frameFile || '').replace('.jpg', '') })
        ]),
        el('span', { class: 'rail-name', text: String(i + 1).padStart(2, '0') + ' ' + s.label }),
        el('span', { class: 'rail-sub', text: s.stepsCount + ' steps · ' + s.atomsCount + ' atoms' })
      ]);
    }));
  }

  /* The rail the two assessment screens use: one cell per clip, with that clip's
     spans listed beneath it. `withSpans` is what separates them — Video assessment
     grades a sequence sampled across a span, so its rows carry the interval and the
     frame count; Photo assessment grades one still, so its rows carry the timestamp
     of the frame that actually goes to the model. Same clips, different evidence,
     and the rows say which. */
  function renderClipRail(task, current, withSpans) {
    var groups = clipGroups(task);
    var active = groupOf(groups, current);

    var rail = el('div', { class: 'rail' }, groups.map(function (g, gi) {
      var head = task.subtasks[g.subs[0]];
      var points = g.subs.reduce(function (n, i) {
        return n + pointsOf(task.subtasks[i]).length;
      }, 0);
      return el('button', {
        class: 'rail-cell', type: 'button', 'aria-current': String(gi === active),
        on: { click: function () { setState({ sub: g.subs[0], expanded: null, reply: null }); } }
      }, [
        el('span', { class: 'plate rail-plate' }, [
          crosshair(14),
          plateImage(framePaths(task.code, head.frameVideo, head.frameFile), ''),
          el('span', { class: 'rail-ts', text: (head.frameFile || '').replace('.jpg', '') })
        ]),
        el('span', {
          class: 'rail-name',
          text: String(gi + 1).padStart(2, '0') + ' ' + (g.clip || 'No clip recorded')
        }),
        el('span', {
          class: 'rail-sub',
          text: g.subs.length + (g.subs.length === 1 ? ' subtask · ' : ' subtasks · ') +
                points + (points === 1 ? ' point' : ' points')
        })
      ]);
    }));

    var group = groups[active];
    var rows = group.subs.map(function (i) {
      var s = task.subtasks[i];
      var meta;
      if (withSpans) {
        var span = spanFor(task, i);
        meta = !span ? 'no sampled sequence'
          : (span.whole ? 'whole clip · ' + fmtTime(span.t1)
                        : fmtTime(span.t0) + ' – ' + fmtTime(span.t1)) +
            ' · ' + span.frames.length + ' frames';
      } else {
        var t = frameSeconds(s.frameFile);
        meta = (t === null ? 'no frame' : 'last frame · t' + fmtTime(t)) +
               ' · ' + pointsOf(s).length + (pointsOf(s).length === 1 ? ' point' : ' points');
      }
      return el('button', {
        class: 'span-cell', type: 'button', 'aria-current': String(i === current),
        on: { click: function () { setState({ sub: i, expanded: null, reply: null }); } }
      }, [
        el('span', { class: 'span-cell-name', text: s.label }),
        el('span', { class: 'span-cell-meta', text: meta })
      ]);
    });

    return el('div', { class: 'rail-stack' }, [
      rail,
      el('div', { class: 'span-list' }, [
        el('span', {
          class: 'span-list-label',
          text: group.clip
            ? (withSpans ? 'Spans graded on this clip · sampled at ' + SAMPLE_FPS_LABEL
                         : 'Stills graded on this clip · the last frame of each span')
            : 'Subtasks with no clip recorded against them'
        })
      ].concat(rows))
    ]);
  }

  /* tab · hierarchy */

  function renderHierarchy(task, st) {
    var left = [
      el('div', { class: 'sub-head' }, [
        el('h2', { class: 'sub-title', text: st.num + ' · ' + st.label }),
        el('span', {
          class: 'sub-meta',
          text: 'sheet ' + st.sheet + ' · ' + st.stepsCount + ' steps · click a step for its atoms'
        })
      ])
    ];

    if (st.hasSteps) {
      st.raw.steps.forEach(function (s) {
        var open = state.expanded === s.id;
        var body = null;
        if (open) {
          body = el('span', { class: 'step-body' }, [
            el('span', { class: 'step-col' }, [
              el('span', { class: 'col-label', text: 'Checks' })
            ].concat(s.checks.map(function (c) {
              return el('span', { class: 'check' }, [
                el('span', { class: 'check-row' }, [
                  el('span', { class: 'check-id', text: c.id }),
                  el('span', { class: 'check-text', text: c.text }),
                  tag(c.obs === 'photo' ? 'tag tag-accent' : 'tag tag-outline', c.obs)
                ]),
                el('span', { class: 'check-src', text: c.src })
              ]);
            }))),
            el('span', { class: 'step-col' }, [
              el('span', { class: 'col-label', text: 'Error modes' })
            ].concat(s.errors.map(function (e) {
              return el('span', { class: 'err' }, [
                el('span', { class: 'check-id', text: e.id }),
                el('span', { class: 'check-text', text: e.text }),
                tag(e.sev === 'critical' ? 'tag tag-neutral' : 'tag tag-outline', e.sev)
              ]);
            })))
          ]);
        }
        left.push(el('button', {
          class: 'blueprint step', type: 'button', 'aria-expanded': String(open),
          on: { click: function () { setState({ expanded: open ? null : s.id }); } }
        }, [
          corners(),
          el('span', { class: 'step-top' }, [
            el('span', { class: 'step-id', text: s.id }),
            el('span', { class: 'step-text', text: s.text }),
            el('span', { class: 'spacer' }),
            el('span', {
              class: 'step-counts',
              text: s.checks.length + (s.checks.length === 1 ? ' check · ' : ' checks · ') +
                    s.errors.length + (s.errors.length === 1 ? ' error' : ' errors') + ' ' +
                    (open ? '▾' : '▸')
            })
          ]),
          body
        ]));
      });
    } else {
      left.push(el('div', { class: 'empty-note' }, [
        el('span', { class: 'empty-title', text: st.stepsCount + ' steps compiled · atoms drafted' }),
        el('span', {
          class: 'empty-body',
          text: 'Prototype note: full step-level detail is populated for AM.I.D.S1. This task’s pack carries the same structure — steps, checks, error modes, sources.'
        })
      ]));
    }

    var right = el('div', { class: 'pane-side' }, [
      el('div', { class: 'block' }, [
        el('span', { class: 'block-label col-label', text: 'Subtask frame · ' + st.frameProv }),
        el('span', { class: 'plate', style: 'height:150px' }, [
          crosshair(26),
          plateImage(framePaths(task.code, st.raw.frameVideo, st.frameFile), st.label),
          el('span', { class: 'plate-file', text: st.frameFile }),
          el('span', { class: 'tag plate-tag', text: st.frameProv })
        ]),
        el('span', { class: 'plate-note', text: st.frameNote })
      ]),
      el('div', { class: 'block' }, [
        el('div', { class: 'block-head' }, [
          el('span', { class: 'col-label', text: 'Subtask sheet — graded about the finished subtask' }),
          el('button', {
            class: 'linkish sheet-link', type: 'button', text: 'Open in Photo assessment →',
            on: { click: function () { setState({ tab: 'assess', reply: null }); } }
          })
        ]),
        el('div', { class: 'sheet' }, st.points.length
          ? st.points.map(function (p) {
            return el('span', {}, [el('b', { text: p.n }), ' ' + p.text]);
          })
          : [el('span', { class: 'sheet-hint', text: NO_POINTS_NOTE })])
      ]),
      el('div', { class: 'blueprint', style: 'padding:10px 14px;display:flex;flex-direction:column;gap:5px' }, [
        corners(),
        el('span', { class: 'col-label', text: 'Eval readiness' }),
        el('div', { class: 'kv' }, [el('span', { text: 'Correct references' }), el('b', { text: st.refs })]),
        el('div', { class: 'kv' }, [
          el('span', { text: 'Labeled negatives' }), tag('tag tag-neutral', 'needed')
        ]),
        el('div', { class: 'kv' }, [el('span', { text: 'Saved photo runs' }), el('b', { text: st.runs })])
      ])
    ]);

    return el('div', { class: 'pane-split' }, [el('div', { class: 'pane-steps' }, left), right]);
  }

  /* tab · photo assessment */

  function renderAssess(task, st) {
    probeServer();
    loadRunStore('photo', task.code);
    // Newest first, same order as the video tab: a run just made on this page,
    // then one serve.py persisted, then the saved CLI run in the built extract.
    var liveGrid = liveRuns['photo' + task.code + '#' + subIndex(task)] ||
                   (runStores.photo[task.code] || {})[st.raw.sheet] || null;

    // A drafted sheet only stands in where no run was saved for this subtask.
    var draftKey = task.code + '#' + subIndex(task);
    var drafted = !st.hasRun && state.drafted === draftKey;
    var negLines = st.hasRun && st.raw.run.negLines ? st.raw.run.negLines
      : drafted ? draftPerturbed(st.points) : [];
    var hasNeg = negLines.length > 0;
    var keptLines = hasNeg ? keptOf(negLines) : 0;
    // A saved run counts the perturbations it actually graded — the rows carrying a control.
    var keptGraded = st.hasRun
      ? st.raw.run.rows.filter(function (r) { return r.neg; }).length
      : keptLines;
    var runCost = runCostText(st.points, keptGraded, st.hasRun || hasNeg);

    var controls = [
      el('div', { class: 'controls-head' }, [
        el('span', { class: 'col-label', text: 'Controls · perturbed sheet' }),
        hasNeg ? tag('tag tag-outline', 'drafted · ' + keptLines + ' of ' + negLines.length + ' kept') : null
      ])
    ];

    if (hasNeg) {
      controls.push(el('div', { class: 'neg-box' }, negLines.map(function (l) {
        var skipped = l.status.indexOf('skipped') === 0;
        return el('div', { class: 'neg-line' }, [
          el('div', { class: 'neg-row' }, [
            el('span', { class: 'neg-mark', text: l.mark }),
            el('span', { class: 'neg-text' + (skipped ? ' is-skipped' : ''), text: l.text }),
            tag(skipped ? 'tag tag-neutral' : 'tag tag-accent', l.status)
          ]),
          l.from ? el('span', { class: 'neg-from', text: 'from ' + l.from }) : null
        ]);
      })));
      controls.push(el('span', { class: 'plate-note' }, [
        'Each line moves ', el('b', { text: 'one stated standard' }),
        ' so correct work no longer meets it — 6–8 twists per inch becomes 2–4, square becomes chamfered. A perturbation only a measurement could settle, or one loose enough that correct work still passes, is dropped rather than kept.'
      ]));
    }
    if (drafted) {
      controls.push(el('span', {
        style: 'font-size:10px;color:var(--color-accent-800);line-height:1.5',
        text: 'Drafted just now, vetted in code, saved to build/photo_eval/. Not yet graded — Run grades the criterion and its kept perturbations together.'
      }));
    }
    if (!hasNeg) {
      controls.push(el('button', {
        class: 'btn btn-secondary draft-btn', type: 'button', text: 'Draft perturbed sheet · 1 call',
        on: { click: function () { setState({ drafted: draftKey }); } }
      }));
      controls.push(el('span', {
        class: 'plate-note',
        text: 'Moves one stated standard per condition — a tolerance, a count, an angle, a finish — keeping the subject and the evidence it asks for.'
      }));
    }
    controls.push(el('span', { class: 'plate-note' }, [
      'Scored ', el('b', { text: 'not_pass' }),
      ': fail where the frame shows the moved standard unmet, unsure where it cannot be read.'
    ]));

    var left = el('div', { class: 'assess-left' }, [
      el('div', { class: 'assess-left-inner' }, [
        el('h2', { class: 'assess-title', text: st.label + ' — subtask sheet' }),
        el('span', { class: 'plate', style: 'height:132px' }, [
          crosshair(24),
          plateImage(framePaths(task.code, st.raw.frameVideo, st.frameFile), st.label),
          el('span', { class: 'plate-file', text: st.frameFile }),
          el('span', { class: 'tag plate-tag', text: st.frameProv })
        ]),
        el('div', { class: 'frame-pick' }, [
          el('button', { class: 'btn btn-secondary', type: 'button', text: 'Pick a different frame' }),
          el('span', { class: 'plate-note', text: st.frameShort })
        ]),
        el('div', { class: 'block-head', style: 'padding-top:4px' }, [
          el('span', { class: 'col-label', text: 'Criterion · editable' }),
          el('span', { style: 'font-size:10px;color:var(--color-accent-700);cursor:pointer', text: 'Reset to compiled text' })
        ]),
        el('div', { class: 'sheet' }, [
          el('span', {
            class: 'sheet-hint',
            text: st.points.length
              ? 'Each numbered point becomes its own model call. Defects graded as absences.'
              : NO_POINTS_NOTE
          })
        ].concat(st.points.map(function (p) {
          return el('span', {}, [el('b', { text: p.n }), ' ' + p.text]);
        }))),
        el('div', { class: 'block' }, [
          el('span', { class: 'col-label', text: 'Excluded — not photo-observable' }),
          el('span', { class: 'excluded', text: st.excluded })
        ]),
        el('div', { class: 'controls' }, controls),
        (function () {
          var hostedArms = (serverInfo && serverInfo.server && serverInfo.arms) || [];
          var canRun = hostedArms.length && st.points.length &&
                       st.raw.frameFile && st.raw.frameVideo && !liveRunning;
          return el('div', { class: 'run-row' }, [
            el('button', {
              class: 'btn btn-primary blueprint', type: 'button', disabled: !canRun,
              on: { click: function () { runPhotoLive(task, st); } }
            }, [corners(), liveRunning ? 'Running…'
                : hostedArms.length ? 'Run · ' + hostedArms.length + ' models'
                : 'Run · 4 models']),
            el('span', { class: 'plate-note', text: liveRunning ||
              (canRun
                ? 'Live: one call per arm against this frame, the points graded ' +
                  'together, through serve.py. No perturbed sheet rides a live run — ' +
                  'the CLI pipeline grades those. Saves to data/photo_runs/' +
                  task.code + '.json.'
                : runCost) })
          ]);
        })()
      ])
    ]);

    var run = liveGrid || st.raw.run;
    var right = el('div', { class: 'assess-right' },
      run ? renderGrid(task, st, run) : [
        el('div', { class: 'empty-center' }, [
          el('div', { class: 'empty-note' }, [
            el('span', {
              class: 'empty-title',
              text: st.points.length ? 'No saved run for this target' : 'Nothing compiled for this target'
            }),
            el('span', {
              class: 'empty-body',
              text: st.points.length
                ? 'The compiled criterion is ready (' + st.points.length + ' points). Run grades each point independently across ' + modelNames().length + ' models, alongside the perturbed sheet. Results save to build/photo_eval/' + task.code + '/.'
                : NO_POINTS_NOTE + ' The latest saved run for this task graded its other subtasks; this one was not among them.'
            }),
            // runCost repeats that sentence when there is nothing to cost.
            st.points.length ? el('span', { class: 'plate-note', text: runCost }) : null
          ])
        ])
      ]);

    return el('div', { class: 'assess' }, [left, right]);
  }

  function renderGrid(task, st, run) {
    run = run || st.raw.run;

    function cells(list, rk) {
      return list.map(function (c, mi) {
        var key = rk + 'm' + mi;
        return el('button', {
          class: 'cell' + (state.reply === key ? ' is-open' : ''), type: 'button',
          on: { click: function () { setState({ reply: state.reply === key ? null : key }); } }
        }, [el('span', { class: cellCls(c[0]), style: 'font-size:9px', text: cellTxt(c[0], c[1]) })]);
      });
    }

    var out = [
      el('div', { class: 'grid-head' }, [
        el('div', {
          class: 'grid-head-label',
          text: 'Point + perturbed control (one run) · pass ≥ 0.60 · click a cell for the model’s reply'
        })
      ].concat(modelNames().map(function (m) {
        return el('div', { class: 'grid-model', text: m });
      })))
    ];

    run.rows.forEach(function (r, ri) {
      out.push(el('div', { class: 'grid-row' }, [
        el('div', { class: 'grid-row-label', text: r.label })
      ].concat(cells(r.cells, 'r' + ri))));

      if (r.neg) {
        out.push(el('div', { class: 'grid-row-neg' }, [
          el('div', { class: 'grid-row-neg-label' }, [
            tag('tag tag-outline', 'control'),
            el('span', { text: r.neg.label }),
            r.neg.src ? el('span', { class: 'grid-row-neg-src', text: '· ' + r.neg.src }) : null
          ])
        ].concat(cells(r.neg.cells, 'n' + ri))));
      }
      if (r.skip) {
        out.push(el('div', { class: 'grid-skip' }, [
          tag('tag tag-neutral', 'control skipped'), el('span', { text: r.skip })
        ]));
      }
    });

    // The criteria roll-up and the controls roll-up are the pair worth reading
    // together: the same subtask, graded on its own sheet and on the perturbed one.
    var rolls = Array.isArray(run.rollup)
      ? [{ label: 'Subtask roll-up — one fail fails · unsure → review', cells: run.rollup }]
      : [
          { label: 'Criteria roll-up — one fail fails · unsure → review',
            cells: run.rollup.criteria || [] },
          { label: 'Controls roll-up — every control expects fail',
            cells: run.rollup.controls || [], controls: true }
        ];

    rolls.forEach(function (r) {
      if (!r.cells.length) return;
      out.push(el('div', { class: 'rollup' + (r.controls ? ' is-controls' : '') }, [
        el('div', { class: 'rollup-label', text: r.label })
      ].concat(r.cells.map(function (c) {
        // Verdict and the split it was decided from, so "review" shows what is unsettled.
        return el('div', { class: 'rollup-cell', title: c[2] || '' }, [
          el('span', {
            class: cellCls(c[0]), style: 'font-size:9px;font-weight:600',
            text: c[0] === 'none' ? c[1] : c[0]
          }),
          c[0] === 'none' ? null : el('span', { class: 'rollup-split', text: c[1] })
        ]);
      }))));
    });

    out.push(el('div', { class: 'control-note' }, [
      el('span', {
        class: 'control-note-text',
        text: 'Every control expects fail. Decisive pairs: perturbed points whose original the same model passed on the same frame — the photograph settles the condition and the work meets the real standard, so a control pass there has no observability excuse.'
      }),
      el('span', { class: 'spacer' }),
      el('span', { class: 'control-stats', text: run.controlStats || st.controlStats })
    ]));

    var reply = replyFor(run);
    if (reply) out.push(reply);
    return out;
  }

  function replyFor(run) {
    if (!state.reply) return null;
    var m = state.reply.match(/^([rn])(\d+)m(\d+)$/);
    if (!m) return null;
    var row = run.rows[+m[2]];
    var src = m[1] === 'n' ? (row && row.neg) : row;
    if (!src) return null;
    var c = src.cells[+m[3]];
    var text = run.replies[state.reply] || (
      c[0] === 'pass' ? 'Pass — the condition is visible and satisfied in this frame. Confidence ' + c[1] + '.'
      : c[0] === 'fail' ? 'Fail — the photograph contradicts the stated condition.'
      : 'Unsure — the frame does not depict the subject of this condition clearly enough to decide (' + c[1] + ').'
    );

    return el('div', { class: 'reply' }, [
      el('div', { class: 'reply-head' }, [
        el('span', { class: 'reply-model', text: modelNames()[+m[3]] }),
        el('span', {
          class: cellCls(c[0]) + ' tag-xs',
          text: c[0] === 'accepted' ? 'pass ✗ accepted contradiction' : c[0]
        }),
        el('span', { class: 'reply-point', text: src.label }),
        el('span', { class: 'spacer' }),
        el('button', {
          class: 'linkish reply-close', type: 'button', text: 'Close ✕',
          on: { click: function () { setState({ reply: null }); } }
        })
      ]),
      el('div', { class: 'reply-body', text: text })
    ]);
  }

  /* tab · video assessment
   *
   * The same compiled criterion Photo assessment grades on one still, graded instead
   * on a sequence sampled from the clip at SAMPLE_FPS, each frame carrying its own
   * timestamp. The points are passed unchanged — this screen moves the evidence, not
   * the standard, so a verdict that differs differs because of what motion shows.
   *
   * The grid is the design's: the video run's own arms as columns, a verdict cell
   * citing the moment that settled it, the segment roll-up beneath, and a cell's
   * reply carrying the frames its call actually held. It fills from the newest
   * `build/video_eval/<ACS>/vrun_*.json` that graded something and shows the
   * design's empty state everywhere the runner has not reached — which is most
   * tasks. The photo verdicts stay on their own tab; the two runs ask different
   * arms different questions, and drawing them as one grid implied a pairing
   * that does not exist.
   */

  // Seconds off a frame's own name — t000041_50.jpg is 41.50 s. Same encoding the
  // extractor writes and build_portal_data.py reads.
  function frameSeconds(name) {
    var m = /^t(\d+)_(\d+)/.exec(name || '');
    return m ? +m[1] + +m[2] / 100 : null;
  }

  function fmtTime(t) { return t.toFixed(2) + 's'; }

  /* The span of its clip a subtask is graded over. A pack section is graded on the
     whole clip. A sub-subtask — the stepsCount-0 rows the compiler emits one per
     step — ends at its own graded frame and starts at the one before it on that clip,
     which is the interval that step's work actually occupies. Only three tasks carry
     those rows; everywhere else a subtask is its clip's only occupant and the span is
     the clip, which is stated rather than dressed up as an interval. */
  function spanFor(task, i) {
    var st = task.subtasks[i];
    var clip = st.frameVideo;
    if (!clip) return null;
    var frames = (task.samples || {})[clip] || [];
    if (!frames.length) return null;

    var clipEnd = frameSeconds(frames[frames.length - 1]);
    var own = frameSeconds(st.frameFile);
    if (st.stepsCount || own === null) {
      return { clip: clip, t0: 0, t1: clipEnd, whole: true, frames: frames };
    }

    var marks = task.subtasks.filter(function (s) {
      return s.frameVideo === clip && !s.stepsCount && s.frameFile;
    }).map(function (s) {
      return frameSeconds(s.frameFile);
    }).filter(function (t) {
      return t !== null && t < own;
    }).sort(function (a, b) { return a - b; });

    var t0 = marks.length ? marks[marks.length - 1] : 0;
    return {
      clip: clip, t0: t0, t1: own, whole: false,
      frames: frames.filter(function (f) {
        var t = frameSeconds(f);
        // Exclusive at the start so a frame landing on a boundary is not graded twice.
        return t !== null && t <= own + 1e-9 && (t0 === 0 ? t >= 0 : t > t0 + 1e-9);
      })
    };
  }

  function videoChecks(st) {
    var n = 0;
    (st.raw.steps || []).forEach(function (s) {
      s.checks.forEach(function (c) { if (c.obs === 'video') n += 1; });
    });
    return n;
  }

  function videoCostText(pointCount, frameCount, models) {
    if (!pointCount) return NO_POINTS_NOTE;
    var calls = pointCount * models;
    var images = calls * frameCount;
    return pointCount + ' points × ' + models + ' models = ' + calls + ' calls, ' +
      frameCount + ' frames each = ' + images.toLocaleString() + ' images · ~$' +
      (images * 0.028).toFixed(2) + ' priced at the photo run’s per-call rate per frame. ' +
      'Treat it as an order of magnitude, not a quote: the sequence shares one criterion ' +
      'across its frames, and no video run has been costed for real.';
  }

  /* ── the live run ──────────────────────────────────────────────────────────
   *
   * The Run button is real where the on-device arms are serving. Each arm is a
   * llama-server on its own port answering the browser directly (they ship CORS
   * open on localhost), so the portal can put the sequence to them without any
   * backend of its own: fetch the sampled frames, one chat call per arm, the
   * points graded together. Nothing is written — the run lives in this browser
   * session, and build/video_eval/ stays the CLI's. On a deployed portal no arm
   * answers and the button stays inert, which is the truthful state there.
   *
   * The hosted arms are never called from here: their routes need an API key,
   * and a key in a web page is published, not used.
   */

  // The pilot's grading decision: sequences are graded by the Gemini arms, which
  // take a whole 40–50-frame span in one call. The on-device candidates cap at 12
  // frames — a quarter of the evidence — so they are set aside for grading, and
  // this stays false until that decision changes. The machinery below is kept:
  // flipping this back re-enables the in-page run against local servers.
  var ON_DEVICE_GRADING = false;

  // Port registry and per-call frame cap — mirrors the on-device entries in the
  // eval harness registry (id, port, cap). Past ~2× the cap LFM2 stops returning
  // the criteria JSON at all, so the cap is what makes a call a call.
  var LOCAL_ARMS = [
    { id: 'local/lfm2-vl-3b-q8', label: 'LFM2-VL-3B Q8', port: 8081, cap: 12 },
    { id: 'local/lfm2-vl-3b-q4', label: 'LFM2-VL-3B Q4', port: 8082, cap: 12 },
    { id: 'local/lfm2.5-vl-1.6b-q4', label: 'LFM2.5-VL 1.6B', port: 8083, cap: 12 }
  ];

  // Must match the runner's SEQUENCE_PROMPT — the screen's live verdicts and the
  // CLI's saved ones must be answers to the same question or the grid is two
  // experiments drawn as one.
  var SEQUENCE_PROMPT =
    'You are grading a student\'s aircraft-maintenance work for an FAA Part 147 ' +
    'training pilot.\n\n' +
    'You are shown a VIDEO of the procedure being executed — a sequence of frames in ' +
    'chronological order, each labelled with its timestamp. The final frames show the ' +
    'work as the student left it.\n\n' +
    'GRADE THE FINISHED PRODUCT against the numbered criteria. Return a verdict for ' +
    'EVERY numbered criterion, in the order given. Do not merge, skip, or add any.\n\n' +
    'WHAT THE VIDEO CHANGES\n\n' +
    'The product is graded as it ends, but the video is your evidence for how it got ' +
    'there. A condition of the finished work that the last frame obscures — a hand, a ' +
    'tool, the camera angle — may be plainly visible moments earlier: that is ' +
    'evidence, and the timestamp is your citation. A criterion about how the work was ' +
    'done (order, technique, handling) is graded on the frames that show it being done.\n\n' +
    'Do not answer `unsure` because the final frame is unclear if an earlier frame ' +
    'settles the point.\n\n' +
    'VERDICTS\n' +
    '- `pass`  — the video shows the criterion satisfied.\n' +
    '- `fail`  — the video shows it is NOT satisfied.\n' +
    '- `unsure` — no frame shows the feature well enough to decide. ' +
    'Genuinely unsure, not "the last frame was blurry". If the whole video never ' +
    'shows it, that is `unsure` and is a real and useful answer.\n\n' +
    'Judge only what is visible. Never infer a torque, a pressure, an internal ' +
    'condition, a material or an exact dimension from video. If a criterion needs a ' +
    'measurement and no scale reference appears in any frame, it is `unsure`.\n\n' +
    'Cite the timestamp you relied on whenever you answer pass or fail. Keep each ' +
    '`note` under 20 words.\n\n' +
    'Reply with JSON only, no prose around it:\n' +
    '{"criteria": [\n' +
    '   {"index": <1-based, matching the numbering given>,\n' +
    '    "verdict": "pass" | "fail" | "unsure",\n' +
    '    "at": "<timestamp you relied on, e.g. 12.00, or null>",\n' +
    '    "note": "<what you saw that decided it>"}\n' +
    ' ],\n' +
    ' "observed": "<what the sequence shows overall, under 40 words>"}';

  // The photo twin of SEQUENCE_PROMPT: one still, same numbered-criteria JSON,
  // confidence where the video cites a timestamp. The two prompts share their
  // discipline — visible-only, no inferred measurements, unsure is an answer —
  // so a photo verdict and a video verdict remain answers to the same standard.
  var PHOTO_PROMPT =
    'You are grading a student\'s aircraft-maintenance work for an FAA Part 147 ' +
    'training pilot, from a single PHOTOGRAPH of the finished work.\n\n' +
    'GRADE THE FINISHED PRODUCT against the numbered criteria. Return a verdict for ' +
    'EVERY numbered criterion, in the order given. Do not merge, skip, or add any.\n\n' +
    'VERDICTS\n' +
    '- `pass`  — the photograph shows the criterion satisfied.\n' +
    '- `fail`  — the photograph shows it is NOT satisfied.\n' +
    '- `unsure` — the photograph does not show the feature well enough to decide. ' +
    'That is a real and useful answer.\n\n' +
    'Judge only what is visible. Never infer a torque, a pressure, an internal ' +
    'condition, a material or an exact dimension from a photograph. If a criterion ' +
    'needs a measurement and no scale reference appears in frame, it is `unsure`.\n\n' +
    'Keep each `note` under 20 words.\n\n' +
    'Reply with JSON only, no prose around it:\n' +
    '{"criteria": [\n' +
    '   {"index": <1-based, matching the numbering given>,\n' +
    '    "verdict": "pass" | "fail" | "unsure",\n' +
    '    "confidence": 0.0-1.0,\n' +
    '    "note": "<what you saw that decided it>"}\n' +
    ' ],\n' +
    ' "observed": "<what the photo shows overall, under 40 words>"}';

  // Session-scoped: what is serving, and the runs this browser made. The hosted
  // arm list comes from serve.py's /api/health — the server holds the keys and
  // therefore knows which arms have a route; this page never sees a key.
  var armsUp = null;          // on-device: null = not probed · [] = none · [arm, ...]
  var serverInfo = null;      // null = not probed · {server: bool, arms: [{id, label}]}
  var runStores = { video: {}, photo: {} };  // kind → task.code → persisted grids
  var liveRuns = {};          // kind + task.code + '#' + sub → grid made this session
  var liveRunning = null;     // progress line while a run is underway

  function probeServer() {
    if (serverInfo !== null) return;
    serverInfo = { server: false, arms: [] };
    fetch('/api/health')
      .then(function (r) { return r.json(); })
      .then(function (h) {
        if (h && h.server) {
          serverInfo = { server: true, arms: h.arms || [] };
          setState({});
        }
      })
      .catch(function () {});
  }

  // The runs serve.py persisted for this task, fetched once per task and merged
  // under the built extract: a run made after the last data build must not
  // vanish on reload just because the builder has not run since.
  function loadRunStore(kind, code) {
    if (runStores[kind][code] !== undefined) return;
    runStores[kind][code] = null;
    fetch('data/' + kind + '_runs/' + encodeURIComponent(code) + '.json')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (store) {
        if (store) { runStores[kind][code] = store; setState({}); }
      })
      .catch(function () {});
  }

  function probeArms() {
    if (armsUp !== null) return;
    armsUp = [];
    if (!ON_DEVICE_GRADING) return;
    LOCAL_ARMS.forEach(function (arm) {
      fetch('http://127.0.0.1:' + arm.port + '/health')
        .then(function (r) { return r.json(); })
        .then(function (h) {
          if (h && h.status === 'ok') { armsUp.push(arm); setState({}); }
        })
        .catch(function () {});
    });
  }

  function thinFrames(frames, cap) {
    if (frames.length <= cap) return frames;
    var idx = {}, out = [];
    for (var i = 0; i < cap; i++) idx[Math.round(i * (frames.length - 1) / (cap - 1))] = true;
    Object.keys(idx).map(Number).sort(function (a, b) { return a - b; })
      .forEach(function (k) { out.push(frames[k]); });
    return out;
  }

  function fetchFrame(task, clip, name) {
    // The full frame where the tree has one, the thumb where it does not —
    // plateImage's fallback, applied to what the arm is shown.
    var paths = framePaths(task.code, clip, name);
    return fetch(paths[0]).then(function (r) {
      if (!r.ok) return fetch(paths[1]).then(function (r2) {
        if (!r2.ok) throw new Error('no frame ' + name);
        return r2.blob();
      });
      return r.blob();
    }).then(function (blob) {
      return new Promise(function (resolve, reject) {
        var fr = new FileReader();
        fr.onload = function () { resolve(fr.result); };
        fr.onerror = reject;
        fr.readAsDataURL(blob);
      });
    });
  }

  function parseReply(text) {
    var s = String(text || '').replace(/```(?:json)?/g, '');
    var a = s.indexOf('{'), b = s.lastIndexOf('}');
    if (a === -1 || b <= a) return null;
    try { return JSON.parse(s.slice(a, b + 1)); } catch (e) { return null; }
  }

  function runLive(task, st, span) {
    var key = 'video' + task.code + '#' + subIndex(task);
    var arms = armsUp || [];
    if (!arms.length || liveRunning) return;

    var criteria = st.points.map(function (p) { return p.text; });
    var numbered = criteria.map(function (c, i) { return (i + 1) + '. ' + c; }).join('\n');

    liveRunning = 'starting · 0/' + arms.length + ' arms';
    setState({});

    // One arm at a time, matching the CLI runner: the arms share this machine's
    // GPU and memory, and concurrency there is what took a server down.
    var results = [];
    var chain = Promise.resolve();
    arms.forEach(function (arm, ai) {
      chain = chain.then(function () {
        liveRunning = arm.label + ' · ' + ai + '/' + arms.length + ' arms done';
        setState({});
        var sent = thinFrames(span.frames, arm.cap);
        var stamps = sent.map(function (f) { return frameSeconds(f); });
        var labelled = stamps.map(function (s, i) { return (i + 1) + '=t' + s.toFixed(2); }).join(', ');
        return Promise.all(sent.map(function (f) { return fetchFrame(task, span.clip, f); }))
          .then(function (urls) {
            var content = [{ type: 'text', text:
              'THE WORK\n' + st.label + '\n\n' +
              'SEQUENCE\n' + sent.length + ' frames follow in chronological order. ' +
              'Their timestamps in seconds are ' + labelled + '.\n\n' +
              'NUMBERED CRITERIA\n' + numbered + '\n\n' +
              'Return a verdict for all ' + criteria.length + ' criteria, in order, ' +
              'using the whole sequence.' }];
            urls.forEach(function (u) {
              content.push({ type: 'image_url', image_url: { url: u } });
            });
            return fetch('http://127.0.0.1:' + arm.port + '/v1/chat/completions', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                model: arm.id, max_tokens: 4000,
                // LEAP's published sampling — what the phone will run.
                temperature: 0.1, min_p: 0.15, repeat_penalty: 1.05,
                messages: [
                  { role: 'system', content: SEQUENCE_PROMPT },
                  { role: 'user', content: content }
                ]
              })
            });
          })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            var text = (((data.choices || [])[0] || {}).message || {}).content || '';
            results.push({ arm: arm, sent: sent, text: text, parsed: parseReply(text) });
          })
          .catch(function (e) {
            results.push({ arm: arm, sent: sent || [], text: String(e), parsed: null });
          });
      });
    });

    chain.then(function () {
      liveRuns[key] = buildLiveGrid(st, span, results,
        'live run — this browser only, nothing written');
      liveRunning = null;
      setState({ reply: null });
    });
  }

  /* One roll-up cell from one arm's verdicts, in the photo roll-up's own shape:
     status, a compact P/F/U split, and the full sentence for the hover. */
  function rollupCell(verdicts) {
    var got = verdicts.filter(function (v) { return v !== 'none'; });
    if (!got.length) return ['none', 'ungraded', 'no verdicts from this arm'];
    var p = got.filter(function (v) { return v === 'pass'; }).length;
    var f = got.filter(function (v) { return v === 'fail'; }).length;
    var u = got.filter(function (v) { return v === 'unsure'; }).length;
    var status = f ? 'fail' : (u ? 'review' : 'pass');
    return [status, p + 'P ' + f + 'F ' + u + 'U',
            p + ' pass · ' + f + ' fail · ' + u + ' unsure of ' + got.length];
  }

  /* One grid from one set of arm replies, whichever arms they came from. */
  function buildLiveGrid(st, span, results, runId) {
    var rows = st.points.map(function (_, ri) {
      return { cells: results.map(function (res) {
        var item = ((res.parsed || {}).criteria || []).filter(function (c) {
          return c && typeof c === 'object' && +c.index === ri + 1;
        })[0];
        var v = item && String(item.verdict || '').toLowerCase();
        if (v !== 'pass' && v !== 'fail' && v !== 'unsure') return ['none', 'not graded'];
        var at = item.at != null && item.at !== '' && item.at !== 'null' ? String(item.at) : null;
        // Compact cells, like the built grid's: the cited moment, or a clipped
        // note. The full note is one click away in the reply panel.
        var note = String(item.note || '').trim();
        return [v, at ? 't=' + at : (note.length > 22 ? note.slice(0, 21) + '…' : note)];
      }) };
    });
    var rollup = results.map(function (_, mi) {
      return rollupCell(rows.map(function (r) { return r.cells[mi][0]; }));
    });
    var replies = {}, framesSent = {}, capNotes = [];
    results.forEach(function (res, mi) {
      replies['m' + mi] = res.text;
      framesSent['m' + mi] = res.sent;
      if (span.frames.length > res.sent.length) {
        capNotes.push(res.arm.label + ' accepts ' + res.sent.length + ' frames per call — ' +
          (span.frames.length - res.sent.length) + ' of ' + span.frames.length +
          ' dropped at even spacing before the call.');
      }
    });
    return {
      live: true, runId: runId,
      fps: 0.5, models: results.map(function (r) { return r.arm.label; }),
      clip: span.clip, rows: rows, rollup: rollup, replies: replies,
      framesSent: framesSent, capNotes: capNotes, cost: 0
    };
  }

  /* The hosted run: same prompt, same parsing, same grid — only the transport
     differs. serve.py attaches the frames and the key and forwards one call per
     arm; the whole span goes in, uncapped, because taking a sequence whole is
     the reason these are the grading arms. When a perturbed sheet rides along,
     each arm takes a second call with the kept lines as its numbered criteria —
     same prompt, same frames, and the arm is never told these are controls.
     The grid is persisted server-side so it survives a reload and a rebuild. */
  function runHosted(task, st, span, negLines) {
    var key = 'video' + task.code + '#' + subIndex(task);
    var arms = (serverInfo && serverInfo.arms) || [];
    if (!arms.length || liveRunning) return;

    var criteria = st.points.map(function (p) { return p.text; });
    var numbered = criteria.map(function (c, i) { return (i + 1) + '. ' + c; }).join('\n');
    var stamps = span.frames.map(function (f) { return frameSeconds(f); });
    var labelled = stamps.map(function (s, i) { return (i + 1) + '=t' + s.toFixed(2); }).join(', ');
    function seqText(list) {
      return 'THE WORK\n' + st.label + '\n\n' +
        'SEQUENCE\n' + span.frames.length + ' frames follow in chronological order. ' +
        'Their timestamps in seconds are ' + labelled + '.\n\n' +
        'NUMBERED CRITERIA\n' + list.map(function (c, i) { return (i + 1) + '. ' + c; }).join('\n') +
        '\n\nReturn a verdict for all ' + list.length + ' criteria, in order, ' +
        'using the whole sequence.';
    }
    var userText = seqText(criteria);

    // The kept lines, remembering which point each one perturbs — the pairing
    // is what makes a control pass readable against its original.
    var kept = (negLines || []).map(function (l, i) { return { line: l, point: i }; })
      .filter(function (k) { return k.line.status.indexOf('skipped') !== 0; });
    var ctlUserText = kept.length
      ? seqText(kept.map(function (k) { return k.line.text; })) : null;

    liveRunning = 'starting · 0/' + arms.length + ' arms';
    setState({});

    var results = [];
    var chain = Promise.resolve();
    arms.forEach(function (arm, ai) {
      chain = chain.then(function () {
        liveRunning = arm.label + ' · ' + ai + '/' + arms.length + ' arms done';
        setState({});
        var res = { arm: arm, sent: span.frames, text: '', parsed: null,
                    ctlText: '', ctlParsed: null };
        results.push(res);
        return fetch('/api/video/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            model: arm.id, task_code: task.code, clip: span.clip,
            frames: span.frames, system: SEQUENCE_PROMPT, user_text: userText
          })
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            res.text = data.text || (data.error ? '[' + data.error + '] ' + (data.message || '') : '');
            res.parsed = parseReply(data.text || '');
          })
          .catch(function (e) { res.text = String(e); })
          .then(function () {
            if (!ctlUserText) return;
            liveRunning = arm.label + ' · controls · ' + ai + '/' + arms.length + ' arms done';
            setState({});
            return fetch('/api/video/run', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                model: arm.id, task_code: task.code, clip: span.clip,
                frames: span.frames, system: SEQUENCE_PROMPT, user_text: ctlUserText
              })
            })
              .then(function (r) { return r.json(); })
              .then(function (data) {
                res.ctlText = data.text || (data.error ? '[' + data.error + '] ' + (data.message || '') : '');
                res.ctlParsed = parseReply(data.text || '');
              })
              .catch(function (e) { res.ctlText = String(e); });
          });
      });
    });

    chain.then(function () {
      var grid = buildLiveGrid(st, span, results,
        'hosted run · saved to data/video_runs/' + task.code + '.json');
      if (kept.length) attachVideoControls(grid, negLines, kept, results);
      liveRuns[key] = grid;
      persistRun('video', task, st, grid);
      liveRunning = null;
      setState({ reply: null });
    });
  }

  /* Controls onto the video grid, in the photo grid's own dialect: a control
     cell is `fail ✓` (expected), `unsure not_pass ✓`, or — where the same arm
     passed the original point on the same sequence — `accepted`, the decisive
     pair with no observability excuse. The roll-up splits in two like the photo
     grid's, and the stats line counts the same way. */
  function attachVideoControls(grid, negLines, kept, results) {
    var accepted = 0;
    kept.forEach(function (k, ki) {
      var row = grid.rows[k.point];
      if (!row) return;
      var cells = results.map(function (res, mi) {
        var item = ((res.ctlParsed || {}).criteria || []).filter(function (c) {
          return c && typeof c === 'object' && +c.index === ki + 1;
        })[0];
        var v = item && String(item.verdict || '').toLowerCase();
        if (v !== 'pass' && v !== 'fail' && v !== 'unsure') return ['none', 'not graded'];
        var at = item.at != null && item.at !== '' && item.at !== 'null' ? String(item.at) : null;
        if (v === 'unsure') return ['unsure', 'not_pass ✓'];
        if (v === 'fail') return ['fail', at ? 't=' + at : '✓'];
        if (row.cells[mi][0] === 'pass') { accepted += 1; return ['accepted', 'pass ✗ accepted']; }
        return ['pass', at ? 't=' + at : ''];
      });
      row.neg = { label: k.line.mark + ' · ' + k.line.text, src: '', cells: cells };
    });
    negLines.forEach(function (l, i) {
      if (l.status.indexOf('skipped') === 0 && grid.rows[i]) {
        grid.rows[i].skip = l.mark + ' · ' + l.status;
      }
    });
    grid.rollup = {
      criteria: grid.rollup,
      controls: results.map(function (_, mi) {
        return rollupCell(grid.rows.filter(function (r) { return r.neg; })
          .map(function (r) {
            var v = r.neg.cells[mi][0];
            return v === 'accepted' ? 'pass' : v;
          }));
      })
    };
    results.forEach(function (res, mi) { grid.replies['c' + mi] = res.ctlText; });
    grid.negLines = negLines;
    var total = kept.length * results.length;
    grid.controlStats = total + ' perturbed points · not passed ' + (total - accepted) +
                        ' · accepted ' + accepted;
  }

  function persistRun(kind, task, st, grid) {
    var store = runStores[kind][task.code] || {};
    store[st.raw.sheet] = grid;
    runStores[kind][task.code] = store;
    fetch('/api/video/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: kind, task_code: task.code,
                             sheet: st.raw.sheet, grid: grid })
    }).catch(function () {});
  }

  /* The photo run, live: the same still the saved runs graded — the span's last
     frame — put to the same hosted arms with the photo twin of the prompt. One
     call per arm, points graded together. It cannot draft or grade a perturbed
     sheet, and does not pretend to: the grid it builds says so where the saved
     grid prints control stats. */
  function runPhotoLive(task, st) {
    var key = 'photo' + task.code + '#' + subIndex(task);
    var arms = (serverInfo && serverInfo.arms) || [];
    if (!arms.length || liveRunning || !st.raw.frameFile || !st.raw.frameVideo) return;

    var criteria = st.points.map(function (p) { return p.text; });
    var numbered = criteria.map(function (c, i) { return (i + 1) + '. ' + c; }).join('\n');
    var userText =
      'THE WORK\n' + st.label + '\n\n' +
      'One photograph of the finished work is attached.\n\n' +
      'NUMBERED CRITERIA\n' + numbered + '\n\n' +
      'Return a verdict for all ' + criteria.length + ' criteria, in order.';

    liveRunning = 'starting · 0/' + arms.length + ' arms';
    setState({});

    var results = [];
    var chain = Promise.resolve();
    arms.forEach(function (arm, ai) {
      chain = chain.then(function () {
        liveRunning = arm.label + ' · ' + ai + '/' + arms.length + ' arms done';
        setState({});
        return fetch('/api/video/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            model: arm.id, task_code: task.code, clip: st.raw.frameVideo,
            frames: [st.raw.frameFile], system: PHOTO_PROMPT, user_text: userText
          })
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            var text = data.text || (data.error ? '[' + data.error + '] ' + (data.message || '') : '');
            results.push({ arm: arm, text: text, parsed: parseReply(data.text || '') });
          })
          .catch(function (e) {
            results.push({ arm: arm, text: String(e), parsed: null });
          });
      });
    });

    chain.then(function () {
      // Shaped like the saved photo grid, so renderGrid draws it unchanged.
      var replies = {};
      var rows = st.points.map(function (p, ri) {
        return {
          label: (ri + 1) + ' · ' + p.text.slice(0, 76),
          cells: results.map(function (res, mi) {
            var item = ((res.parsed || {}).criteria || []).filter(function (c) {
              return c && typeof c === 'object' && +c.index === ri + 1;
            })[0];
            var v = item && String(item.verdict || '').toLowerCase();
            if (v !== 'pass' && v !== 'fail' && v !== 'unsure') return ['none', 'not graded'];
            if (item.note) replies['r' + ri + 'm' + mi] = String(item.note);
            var conf = typeof item.confidence === 'number'
              ? item.confidence.toFixed(2) : '';
            return [v, conf];
          })
        };
      });
      var rollup = results.map(function (_, mi) {
        return rollupCell(rows.map(function (r) { return r.cells[mi][0]; }));
      });
      var grid = {
        live: true, models: results.map(function (r) { return r.arm.label; }),
        rows: rows, rollup: rollup, replies: replies, negLines: [],
        controlStats: 'live run · no perturbed sheet — the saved control grid ' +
                      'returns if data/photo_runs/' + task.code + '.json is removed'
      };
      liveRuns[key] = grid;
      persistRun('photo', task, st, grid);
      liveRunning = null;
      setState({ reply: null });
    });
  }

  function renderVideoAssess(task, st) {
    if (!task.clips) {
      var vcx = videoChecks(st);
      return renderNotice('No source video',
        'The workbook records this task as "N/A (not AIM developed)", so there is no clip ' +
        'to sample and nothing to grade a sequence on. ' +
        (vcx
          ? 'This subtask carries ' + vcx + (vcx === 1 ? ' check' : ' checks') +
            ' marked [video] — observable only in motion, on footage that was never shot. ' +
            'The criterion does not stop existing because the recording does.'
          : 'Its criterion exists regardless — a criterion cannot depend on a photograph existing.'));
    }

    var span = spanFor(task, subIndex(task));
    if (!span) {
      return renderNotice('No sampled sequence for this subtask',
        st.raw.frameVideo
          ? 'data/thumbs/' + task.code + '/' + st.raw.frameVideo + '/ carries no sample — ' +
            're-run scripts/build_portal_data.py.'
          : 'The latest run did not grade this subtask, so no clip is recorded against it ' +
            'and there is no span to sample.');
    }

    var models = modelNames().length;
    var vc = videoChecks(st);

    probeArms();
    probeServer();
    loadRunStore('video', task.code);

    // Newest first: a run just made on this page, then one serve.py persisted,
    // then whatever the built extract carries from the CLI runner.
    var vrun = liveRuns['video' + task.code + '#' + subIndex(task)] ||
               (runStores.video[task.code] || {})[st.raw.sheet] ||
               st.raw.vrun;

    /* The perturbed sheet is the photo tab's, reused verbatim. A control moves
       the standard; this tab moves the evidence — moving both at once would
       leave nothing to compare, so the lines come from the saved video run,
       else the saved photo run, else the same in-page draft the photo tab makes.
       Scored the photo way: every control expects fail, unsure is not_pass, and
       a pass is the grader accepting a standard the work does not meet. */
    var draftKey = task.code + '#' + subIndex(task);
    var negLines =
      (vrun && vrun.negLines && vrun.negLines.length) ? vrun.negLines
      : (st.hasRun && st.raw.run.negLines && st.raw.run.negLines.length) ? st.raw.run.negLines
      : (state.drafted === draftKey && st.points.length) ? draftPerturbed(st.points)
      : [];
    var keptLines = keptOf(negLines);

    /* left · what the model is handed */

    var seq = el('div', { class: 'seq' }, span.frames.map(function (f) {
      return el('span', { class: 'seq-frame' }, [
        plateImage(framePaths(task.code, span.clip, f), ''),
        el('span', { class: 'seq-ts', text: fmtTime(frameSeconds(f)) })
      ]);
    }));

    var left = el('div', { class: 'assess-left' }, [
      el('div', { class: 'assess-left-inner' }, [
        el('h2', { class: 'assess-title', text: st.label + ' — criterion on clip' }),

        el('div', { class: 'block' }, [
          el('div', { class: 'block-head' }, [
            el('span', { class: 'col-label', text: 'Evidence · sampled sequence' }),
            tag('tag tag-accent', SAMPLE_FPS_LABEL)
          ]),
          el('div', { class: 'span-row' }, [
            el('span', { class: 'span-clip', text: span.clip + '.mp4' }),
            tag('tag tag-outline', span.whole
              ? 'whole clip · ' + fmtTime(span.t1)
              : fmtTime(span.t0) + ' – ' + fmtTime(span.t1)),
            el('span', { class: 'span-count', text: span.frames.length + ' frames' })
          ]),
          seq,
          el('span', { class: 'plate-note' }, [
            'Each frame is passed with its own timestamp — ',
            el('b', { text: 't=' + fmtTime(frameSeconds(span.frames[0])) }),
            ' through ',
            el('b', { text: 't=' + fmtTime(frameSeconds(span.frames[span.frames.length - 1])) }),
            ' — so the model can order what it sees and cite a moment back to the video. ' +
            'Sampled from the 4 fps extraction; the filenames are the timestamps.'
          ]),
          el('span', { class: 'plate-note', text: span.whole
            ? 'This subtask is the only one recorded against this clip, so the span is the ' +
              'whole clip rather than an interval inside it.'
            : span.t0 === 0
            ? 'The span ends on this subtask’s graded frame and starts at the clip’s start — ' +
              'no earlier subtask is recorded against this clip to bound it.'
            : (task.segmented
              ? 'Reviewed interval: the span ends on this subtask’s own graded frame and ' +
                'starts on the previous one’s.'
              : 'The span ends on this subtask’s graded frame and starts on the previous ' +
                'subtask’s on this clip. Those boundaries are compiled, not reviewed.') })
        ]),

        el('div', { class: 'block-head', style: 'padding-top:4px' }, [
          el('span', { class: 'col-label', text: 'Criterion · unchanged from Photo assessment' }),
          el('button', {
            class: 'linkish', type: 'button', text: 'Open in Photo assessment →',
            on: { click: function () { setState({ tab: 'assess', reply: null }); } }
          })
        ]),
        el('div', { class: 'sheet' }, [
          el('span', {
            class: 'sheet-hint',
            text: st.points.length
              ? 'The same points, graded on the sequence instead of the still. Moving the ' +
                'evidence and the standard at once would leave nothing to compare.'
              : NO_POINTS_NOTE
          })
        ].concat(st.points.map(function (p) {
          return el('span', {}, [el('b', { text: p.n }), ' ' + p.text]);
        }))),

        el('div', { class: 'block' }, [
          el('span', { class: 'col-label', text: 'Excluded — carried over from the photo sheet' }),
          el('span', { class: 'excluded', text: st.excluded }),
          vc ? el('span', { class: 'plate-note vc-note' }, [
            el('b', { text: vc + (vc === 1 ? ' check' : ' checks') + ' marked [video]' }),
            vc === 1
              ? ' in this subtask stays out of the sheet, though this is the evidence it was held back for. Admitting it would change the criterion, and then a clip verdict could not be read against the photo one.'
              : ' in this subtask stay out of the sheet, though this is the evidence they were held back for. Admitting them would change the criterion, and then a clip verdict could not be read against the photo one.'
          ]) : null
        ]),

        (function () {
          var controls = [
            el('div', { class: 'controls-head' }, [
              el('span', { class: 'col-label', text: 'Controls · perturbed sheet — shared with Photo assessment' }),
              negLines.length ? tag('tag tag-outline', keptLines + ' of ' + negLines.length + ' kept') : null
            ])
          ];
          if (negLines.length) {
            controls.push(el('div', { class: 'neg-box' }, negLines.map(function (l) {
              var skipped = l.status.indexOf('skipped') === 0;
              return el('div', { class: 'neg-line' }, [
                el('div', { class: 'neg-row' }, [
                  el('span', { class: 'neg-mark', text: l.mark }),
                  el('span', { class: 'neg-text' + (skipped ? ' is-skipped' : ''), text: l.text }),
                  tag(skipped ? 'tag tag-neutral' : 'tag tag-accent', l.status)
                ]),
                l.from ? el('span', { class: 'neg-from', text: 'from ' + l.from }) : null
              ]);
            })));
            controls.push(el('span', { class: 'plate-note' }, [
              'The same moved standards the photo grid grades, put to the sequence. Every control ',
              el('b', { text: 'expects fail' }),
              ' — the video shows work meeting the real standard, so the moved one is unmet. ' +
              'unsure scores not_pass; a pass is the grader accepting a standard the work does not meet.'
            ]));
          } else {
            controls.push(el('button', {
              class: 'btn btn-secondary draft-btn', type: 'button', text: 'Draft perturbed sheet · 1 call',
              on: { click: function () { setState({ drafted: draftKey }); } }
            }));
            controls.push(el('span', { class: 'plate-note', text:
              'No perturbed sheet for this subtask yet. Drafting moves one stated standard per ' +
              'point — the same sheet the photo tab drafts, so a still verdict and a sequence ' +
              'verdict stay answers about the same moved bar.' }));
          }
          return el('div', { class: 'controls' }, controls);
        })(),

        (function () {
          var hostedArms = (serverInfo && serverInfo.server && serverInfo.arms) || [];
          var canLocal = ON_DEVICE_GRADING && armsUp && armsUp.length;
          var note = liveRunning
            ? liveRunning
            : hostedArms.length
            ? 'One call per arm (' + hostedArms.map(function (a) { return a.label; }).join(', ') +
              ') over the whole ' + span.frames.length + '-frame span — no frame cap' +
              (keptLines ? ', plus a control call per arm grading the ' + keptLines +
                           ' kept perturbations on the same frames' : '') + '. ' +
              'serve.py holds the key and forwards the call; the grid saves to ' +
              'data/video_runs/' + task.code + '.json and survives a reload.'
            : (serverInfo && serverInfo.server)
            ? 'serve.py is up but holds no key. Add OPENROUTER_API_KEY=… (all four ' +
              'arms) or GEMINI_API_KEY=… (the Gemini two) to portal/.env and restart ' +
              'it; this button wakes up on its own.'
            : videoCostText(st.points.length, span.frames.length, models);
          return el('div', { class: 'run-row' }, [
            el('button', {
              class: 'btn btn-primary blueprint', type: 'button',
              disabled: !(hostedArms.length || canLocal) || !!liveRunning || !st.points.length,
              on: { click: function () {
                if (hostedArms.length) runHosted(task, st, span, negLines);
                else if (canLocal) runLive(task, st, span);
              } }
            }, [corners(), liveRunning ? 'Running…'
                : hostedArms.length ? 'Run · ' + hostedArms.length + ' models'
                : canLocal ? 'Run · ' + armsUp.length + ' on-device ' +
                             (armsUp.length === 1 ? 'arm' : 'arms')
                : 'Run']),
            el('span', { class: 'plate-note', text: note })
          ]);
        })(),
        (serverInfo && serverInfo.server) ? null : el('span', { class: 'plate-note', text:
          'Run is inert on a static serve: a hosted arm needs a key, and a key in ' +
          'a web page is published, not used. Serve the portal with `python3 serve.py` ' +
          'and an OPENROUTER_API_KEY (or GEMINI_API_KEY) in portal/.env, and the ' +
          'button grades the span live. The grid otherwise reads whatever the newest ' +
          'CLI run left in the data extract.' })
      ])
    ]);

    var right = el('div', { class: 'assess-right' },
      vrun ? renderVideoGrid(task, st, span, vrun) : [
        el('div', { class: 'empty-center' }, [
          el('div', { class: 'empty-note' }, [
            el('span', {
              class: 'empty-title',
              text: st.points.length ? 'No saved run for this segment' : 'Nothing compiled for this target'
            }),
            el('span', {
              class: 'empty-body',
              text: st.points.length
                ? 'The compiled criterion is ready (' + st.points.length + ' points). Run hands ' +
                  'each arm the ' + span.frames.length + '-frame sequence sampled at ' +
                  SAMPLE_FPS_LABEL + ' — one call per subtask, the points graded together. ' +
                  'Results save to build/video_eval/' + task.code + '/.'
                : NO_POINTS_NOTE
            }),
            st.points.length ? el('span', { class: 'plate-note',
              text: videoCostText(st.points.length, span.frames.length, models) }) : null
          ])
        ])
      ]);

    return el('div', { class: 'assess' }, [left, right]);
  }

  /* The video grid, as the design draws it: the run's own arms as columns — today
     the on-device candidates — a verdict cell citing the moment that settled it,
     and a segment roll-up beneath. Clicking a cell opens the arm's reply and the
     frames the call actually carried, which the per-arm cap can make thinner than
     the span; the note above the grid says by how much. */
  function renderVideoGrid(task, st, span, vrun) {
    var v = vrun || st.raw.vrun;
    var photoRows = (st.raw.run && st.raw.run.rows) || [];

    var out = [
      el('div', { class: 'grid-head' }, [
        el('div', {
          class: 'grid-head-label',
          text: 'Segment run · one call per arm, the points graded together · ' +
                'click a cell for the reply and the frames it cites'
        })
      ].concat(v.models.map(function (m) {
        return el('div', { class: 'grid-model', text: m });
      })))
    ];

    if (v.capNotes && v.capNotes.length) {
      out.push(el('div', { class: 'cap-note' }, v.capNotes.map(function (n) {
        return el('span', { text: n });
      })));
    }

    function cellButtons(cells, prefix, ri) {
      return cells.map(function (c, mi) {
        var key = prefix + ri + 'm' + mi;
        return el('button', {
          class: 'cell' + (state.reply === key ? ' is-open' : ''), type: 'button',
          title: c[0] + (c[1] ? ' · ' + c[1] : ''),
          on: { click: function () { setState({ reply: state.reply === key ? null : key }); } }
        }, [el('span', { class: cellCls(c[0]), style: 'font-size:9px', text: cellTxt(c[0], c[1]) })]);
      });
    }

    v.rows.forEach(function (r, ri) {
      var label = (photoRows[ri] && photoRows[ri].label) ||
                  (st.points[ri] ? st.points[ri].n + ' ' + st.points[ri].text
                                 : String(ri + 1) + ' ·');
      out.push(el('div', { class: 'grid-row' }, [
        el('div', { class: 'grid-row-label', text: label })
      ].concat(cellButtons(r.cells, 'v', ri))));

      if (r.neg) {
        out.push(el('div', { class: 'grid-row-neg' }, [
          el('div', { class: 'grid-row-neg-label' }, [
            tag('tag tag-outline', 'control'),
            el('span', { text: r.neg.label }),
            r.neg.src ? el('span', { class: 'grid-row-neg-src', text: '· ' + r.neg.src }) : null
          ])
        ].concat(cellButtons(r.neg.cells, 'c', ri))));
      }
      if (r.skip) {
        out.push(el('div', { class: 'grid-skip' }, [
          tag('tag tag-neutral', 'control skipped'), el('span', { text: r.skip })
        ]));
      }
    });

    // Older runs carry one flat roll-up; a run with controls splits it in two,
    // and the pair reads exactly like the photo grid's.
    var rolls = Array.isArray(v.rollup)
      ? [{ label: 'Segment roll-up — one fail fails · unsure → review', cells: v.rollup }]
      : [
          { label: 'Segment roll-up — one fail fails · unsure → review',
            cells: v.rollup.criteria || [] },
          { label: 'Controls roll-up — every control expects fail',
            cells: v.rollup.controls || [], controls: true }
        ];

    rolls.forEach(function (roll) {
      if (!roll.cells.length) return;
      out.push(el('div', { class: 'rollup' + (roll.controls ? ' is-controls' : '') }, [
        el('div', { class: 'rollup-label', text: roll.label })
      ].concat(roll.cells.map(function (c) {
        // Same cell the photo roll-up draws: status, the P/F/U split, the full
        // sentence on hover — the two tabs must read the same way.
        return el('div', { class: 'rollup-cell', title: c[2] || '' }, [
          el('span', { class: cellCls(c[0]), style: 'font-size:9px;font-weight:600',
                       text: c[0] === 'none' ? c[1] : c[0] }),
          c[0] === 'none' ? null : el('span', { class: 'rollup-split', text: c[1] })
        ]);
      }))));
    });

    var hasControls = v.rows.some(function (r) { return r.neg; });
    out.push(el('div', { class: 'control-note' }, [
      el('span', { class: 'control-note-text', text: hasControls
        ? 'Every control expects fail. Decisive pairs: perturbed points whose original ' +
          'the same arm passed on the same sequence — the video settles the condition ' +
          'and the work meets the real standard, so a control pass there has no ' +
          'observability excuse.'
        : 'No perturbed controls rode this run — it graded the original points only. ' +
          'Draft (or reuse) the perturbed sheet on the left and Run again to grade the ' +
          'kept lines alongside, one extra call per arm.' }),
      el('span', { class: 'spacer' }),
      el('span', { class: 'control-stats', text:
        (v.controlStats ? v.controlStats + ' · ' : '') +
        v.runId + ' · sampled at ' + v.fps + ' fps · $' + (v.cost || 0).toFixed(2) })
    ]));

    var reply = videoReplyFor(task, st, v);
    if (reply) out.push(reply);
    return out;
  }

  /* The reply, and the evidence: the frames this arm's call actually carried, with
     the cited moment marked. The photo reply shows text alone; here the claim is
     about a span, so the frames are the only way to check the citation. */
  function videoReplyFor(task, st, v) {
    if (!state.reply) return null;
    // v<row>m<model> is an original point's cell; c<row>m<model> is its control's.
    var m = state.reply.match(/^([vc])(\d+)m(\d+)$/);
    if (!m) return null;
    var isCtl = m[1] === 'c';
    var row = v.rows[+m[2]];
    var src = isCtl ? (row && row.neg) : row;
    if (!src) return null;
    var c = src.cells[+m[3]];
    var sent = v.framesSent['m' + m[3]] || [];
    var cited = c[1] && c[1].indexOf('t=') === 0
      ? parseFloat(c[1].slice(2).replace(/^t/, '')) : null;

    var strip = sent.length ? el('div', { class: 'seq reply-frames' }, sent.map(function (f) {
      var t = frameSeconds(f);
      var hit = cited !== null && t !== null && Math.abs(t - cited) < 1.01;
      return el('span', { class: 'seq-frame' + (hit ? ' is-cited' : '') }, [
        plateImage(framePaths(task.code, v.clip, f), ''),
        el('span', { class: 'seq-ts', text: t === null ? f : fmtTime(t) })
      ]);
    })) : null;

    return el('div', { class: 'reply' }, [
      el('div', { class: 'reply-head' }, [
        el('span', { class: 'reply-model', text: v.models[+m[3]] }),
        el('span', {
          class: cellCls(c[0]) + ' tag-xs',
          text: c[0] === 'accepted' ? 'pass ✗ accepted contradiction' : c[0]
        }),
        el('span', { class: 'reply-point', text: isCtl
          ? src.label : ((st.points[+m[2]] || {}).text || '') }),
        el('span', { class: 'spacer' }),
        el('button', { class: 'linkish reply-close', type: 'button', text: 'Close ✕',
                       on: { click: function () { setState({ reply: null }); } } })
      ]),
      el('div', { class: 'reply-body', text: v.replies[(isCtl ? 'c' : 'm') + m[3]] ||
        (c[0] === 'none' ? 'This arm returned no verdict for this point.' : (c[1] || '')) }),
      strip ? el('span', { class: 'col-label reply-frames-label', text:
        'The ' + sent.length + ' frames this call carried' +
        (cited !== null ? ' · cited t=' + fmtTime(cited) : '') }) : null,
      strip
    ]);
  }

  /* tab · videos & frames */

  var BAND_SHADES = ['var(--color-accent-300)', 'var(--color-accent-500)',
                     'var(--color-accent-700)', 'var(--color-accent-400)', 'var(--color-accent-600)'];

  /* ── area of focus ────────────────────────────────────────────────────────
   *
   * A keyframed crop box over the source clip. A key is {t, cx, cy, w}: seconds,
   * then centre and width as fractions of the frame. Height IS width — the box
   * keeps the clip's own aspect, so a single number carries the whole zoom and
   * two boxes always interpolate cleanly (no aspect to fight over between keys).
   *
   * The editor lives outside setState: a playing <video> cannot survive the
   * full-DOM re-render every state change performs, so everything per-frame —
   * box position, scrubber, cropped preview — runs on its own rAF loop that
   * dies when its stage leaves the document. Track edits mutate module state
   * and the position survives a re-render through focusSession.
   */

  var focusTracks = {};    // '<code>/<clip>' → { keys: [...] } working copies
  var focusFetched = {};   // code → true once data/focus_tracks/<code>.json was merged
  var focusSession = { key: null, time: 0, playing: false, preview: false, results: {} };

  function focusKeyOf(code, clip) { return code + '/' + clip; }

  function tsSeconds(name) {
    var m = /^t(\d{6})_(\d{2})/.exec(name || '');
    return m ? (+m[1]) + (+m[2]) / 100 : 0;
  }

  // Saved tracks are static data — the page reads them back without the server.
  function ensureFocusTracks(code) {
    if (focusFetched[code]) return;
    focusFetched[code] = true;
    getJSON('data/focus_tracks/' + encodeURIComponent(code) + '.json')
      .then(function (store) {
        Object.keys(store || {}).forEach(function (clip) {
          var k = focusKeyOf(code, clip);
          // An open editor has already made a working copy — an empty one, since
          // focusTrackFor runs before this fetch lands. Fill any keyless copy;
          // never overwrite keys the session has actually drawn.
          var cur = focusTracks[k];
          if ((!cur || !cur.keys.length) && store[clip] && Array.isArray(store[clip].keys)) {
            focusTracks[k] = { keys: store[clip].keys.slice().sort(function (a, b) { return a.t - b.t; }) };
          }
        });
        render();
      })
      .catch(function () {});  // no file yet — nothing was ever saved
  }

  function focusTrackFor(code, clip) {
    var k = focusKeyOf(code, clip);
    if (!focusTracks[k]) focusTracks[k] = { keys: [] };
    return focusTracks[k];
  }

  function clampBox(b) {
    var w = Math.min(1, Math.max(0.08, b.w));
    return { cx: Math.min(1 - w / 2, Math.max(w / 2, b.cx)),
             cy: Math.min(1 - w / 2, Math.max(w / 2, b.cy)), w: w };
  }

  // The box at time t: hold outside the keyed span, smoothstep between keys —
  // the ease keeps a straight lerp's hard starts and stops out of the pan.
  function focusBoxAt(track, t) {
    var keys = track.keys;
    if (!keys.length) return { cx: 0.5, cy: 0.5, w: 1 };
    if (t <= keys[0].t) return keys[0];
    var last = keys[keys.length - 1];
    if (t >= last.t) return last;
    for (var i = 0; i < keys.length - 1; i++) {
      var a = keys[i], b = keys[i + 1];
      if (t >= a.t && t <= b.t) {
        var s = b.t > a.t ? (t - a.t) / (b.t - a.t) : 0;
        s = s * s * (3 - 2 * s);
        return { cx: a.cx + (b.cx - a.cx) * s,
                 cy: a.cy + (b.cy - a.cy) * s,
                 w: a.w + (b.w - a.w) * s };
      }
    }
    return last;
  }

  // An adjustment lands on the key already at the playhead, or becomes one.
  function upsertFocusKey(track, t, box) {
    var b = clampBox(box);
    for (var i = 0; i < track.keys.length; i++) {
      if (Math.abs(track.keys[i].t - t) < 0.12) {
        track.keys[i].cx = b.cx; track.keys[i].cy = b.cy; track.keys[i].w = b.w;
        return;
      }
    }
    track.keys.push({ t: Math.round(t * 100) / 100, cx: b.cx, cy: b.cy, w: b.w });
    track.keys.sort(function (a, b2) { return a.t - b2.t; });
  }

  /* Auto-detection: the clip's sampled strip goes to Gemini Flash through the
   * same /api/video/run route the assessment grids use — the page builds the
   * prompt and parses the reply, the server only holds the key. Gemini answers
   * in its native detection dialect, box_2d [ymin, xmin, ymax, xmax] on a
   * 0-1000 grid, one box per frame; each becomes a keyframe at that frame's
   * filename timestamp. */

  var FOCUS_MODEL = 'google/gemini-3.6-flash';

  // The deliberate safety margin over what the detector called required: boxes
  // are enlarged 5% so an important area at the edge is never what the crop
  // cuts off. Applied where detected keys are built.
  var FOCUS_MARGIN = 1.05;

  var DETECT_SYSTEM = 'You locate the area of focus in frames sampled from a video of an ' +
    'aviation maintenance procedure. The area of focus is where the work is happening: the ' +
    'hands, the tool in use, the part being worked on, and any equipment, wire, hose, line ' +
    'or material that is in use in the activity. Nothing in use may be missed. Reply with ' +
    'JSON only — no prose, no code fences.';

  function detectPrompt(count) {
    return 'Here are ' + count + ' frames sampled in chronological order from one video ' +
      'clip. For each frame, in order, give one bounding box around the area of focus. ' +
      'Cover the hands, the active tool, the part being worked on, and ANY equipment, ' +
      'wire, hose, line or material in use — the full length of a wire being routed, ' +
      'twisted or laced, the tester or gauge being read, the fitting being tightened. ' +
      'It is better to make the box larger than to cut off anything in use; never crop ' +
      'mid-action hands, the workpiece, or an item of equipment that the activity is ' +
      'using. If no action is visible — an empty bench, a title card — use the full ' +
      'frame [0,0,1000,1000].\n\n' +
      'Answer with a JSON array of exactly ' + count + ' entries, one per frame in the ' +
      'order shown: {"frame": <0-based index>, "box_2d": [ymin, xmin, ymax, xmax]} with ' +
      'coordinates normalized to 0-1000.';
  }

  // The model was told "JSON only", but a fenced or wrapped reply still parses,
  // and Flash occasionally glitches a single row — so when JSON.parse fails,
  // well-formed entries are pulled out one by one rather than sinking the reply.
  function parseDetectedBoxes(text) {
    var s = String(text || '').replace(/```[a-z]*\n?/gi, '').trim();
    var candidates = [s];
    var a = s.indexOf('['), b = s.lastIndexOf(']');
    if (a >= 0 && b > a) candidates.push(s.slice(a, b + 1));
    for (var i = 0; i < candidates.length; i++) {
      try {
        var v = JSON.parse(candidates[i]);
        if (Array.isArray(v)) return v;
        if (v && typeof v === 'object') {
          for (var k in v) if (Array.isArray(v[k])) return v[k];
        }
      } catch (e) { /* try the next shape */ }
    }
    var out = [];
    var row = /"frame"\s*:\s*(\d+)[^{[]*?"box_2d"\s*:\s*\[\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*\]/g;
    var m;
    while ((m = row.exec(s))) {
      out.push({ frame: +m[1], box_2d: [+m[2], +m[3], +m[4], +m[5]] });
    }
    return out.length ? out : null;
  }

  function detectedKeys(entries, stamps) {
    var ordered = entries.slice();
    if (ordered.every(function (e) { return e && typeof e.frame === 'number'; })) {
      ordered.sort(function (a, b) { return a.frame - b.frame; });
    }
    var keys = [];
    for (var i = 0; i < ordered.length; i++) {
      var entry = ordered[i];
      var box = entry && entry.box_2d;
      if (!box || box.length !== 4) continue;
      var nums = Array.prototype.map.call(box, Number);
      if (!nums.every(isFinite)) continue;
      // The entry's own frame index picks the timestamp, so a salvaged reply
      // with a dropped row cannot shift every later box onto the wrong moment.
      var at = (typeof entry.frame === 'number' && entry.frame >= 0) ? entry.frame : i;
      if (at >= stamps.length) continue;
      var y0 = nums[0] / 1000, x0 = nums[1] / 1000, y1 = nums[2] / 1000, x1 = nums[3] / 1000;
      // The crop is square in frame fractions — take the box's larger side and
      // pad it, so the detection always fits inside with a little context.
      // FOCUS_MARGIN rides on top of that: a deliberate 5% over what the
      // detector called required, so an important area at the box's edge — a
      // fingertip, the end of a part — is not the thing the crop cuts off. The
      // 3-tap smooth below can also shave a peak; the margin absorbs that too.
      var k = clampBox({ cx: (x0 + x1) / 2, cy: (y0 + y1) / 2,
                         w: Math.max(x1 - x0, y1 - y0) * 1.15 * FOCUS_MARGIN });
      k.t = stamps[at];
      keys.push(k);
    }
    // A 3-tap smooth damps per-frame jitter; the ends stay pinned.
    for (var j = 1; j < keys.length - 1; j++) {
      ['cx', 'cy', 'w'].forEach(function (f) {
        keys[j][f] = keys[j - 1][f] * 0.25 + keys[j][f] * 0.5 + keys[j + 1][f] * 0.25;
      });
    }
    // Keep only keys that actually move the box; first and last always stay.
    var kept = [];
    keys.forEach(function (k2, idx) {
      var prev = kept[kept.length - 1];
      if (!prev || idx === keys.length - 1 ||
          Math.abs(k2.cx - prev.cx) > 0.015 || Math.abs(k2.cy - prev.cy) > 0.015 ||
          Math.abs(k2.w - prev.w) > 0.015) kept.push(k2);
    });
    return kept.map(function (k3) {
      var c = clampBox(k3); c.t = k3.t; return c;
    });
  }

  function focusEditor(task, clip) {
    ensureFocusTracks(task.code);
    var track = focusTrackFor(task.code, clip);
    var key = focusKeyOf(task.code, clip);
    if (focusSession.key !== key) {
      focusSession.key = key; focusSession.time = 0;
      focusSession.playing = false; focusSession.preview = false;
    }

    var rendering = false;
    var recMime = '';

    var video = el('video', {
      class: 'focus-video', muted: true, playsinline: true, preload: 'auto',
      src: 'videos/' + encodeURIComponent(task.code) + '/' + encodeURIComponent(clip) + '.mp4'
    });
    video.muted = true;  // the attribute alone does not satisfy autoplay policy

    var canvas = el('canvas', { class: 'focus-canvas' });
    var ctx = canvas.getContext('2d');
    var boxTag = el('span', { class: 'focus-box-tag' });
    var box = el('div', { class: 'focus-box' }, [
      el('i', { class: 'focus-handle nw', 'data-h': 'nw' }),
      el('i', { class: 'focus-handle ne', 'data-h': 'ne' }),
      el('i', { class: 'focus-handle sw', 'data-h': 'sw' }),
      el('i', { class: 'focus-handle se', 'data-h': 'se' }),
      boxTag
    ]);
    // Fitted to the letterboxed content each frame, so the box's percent
    // coordinates are fractions of the frame itself, not of the stage.
    var frameLayer = el('div', { class: 'focus-layer' }, [box]);
    var notice = el('div', { class: 'focus-missing', hidden: true, text:
      'Source video not reachable — videos/' + task.code + '/' + clip + '.mp4. ' +
      'The static deploy carries extracted frames only; run python3 serve.py ' +
      'beside alcor_agents/ to stream the clip.' });
    var stage = el('div', { class: 'focus-stage' }, [video, canvas, frameLayer, notice]);

    var playBtn = el('button', { class: 'btn btn-secondary focus-btn', type: 'button', text: 'Play' });
    var scrub = el('input', { class: 'focus-scrub', type: 'range', min: '0', max: '1000', value: '0',
                              'aria-label': 'playhead' });
    var ticks = el('div', { class: 'focus-ticks' });
    var timeLbl = el('span', { class: 'focus-time', text: '—' });
    var zoomLbl = el('span', { class: 'focus-zoom', text: '×1.0' });

    var keyBtn = el('button', { class: 'btn btn-secondary focus-btn', type: 'button', text: '+ Keyframe' });
    var detectBtn = el('button', { class: 'btn btn-secondary focus-btn', type: 'button',
                                   text: 'Detect focus · Gem 3.6 Flash' });
    var prevBtn = el('button', { class: 'btn btn-secondary focus-btn', type: 'button', text: 'Cropped preview' });
    var renderBtn = el('button', { class: 'btn btn-primary focus-btn', type: 'button', text: 'Render focus video' });
    var saveBtn = el('button', { class: 'btn btn-secondary focus-btn', type: 'button', text: 'Save track' });
    var clearBtn = el('button', { class: 'linkish', type: 'button', text: 'Clear keyframes' });
    var saveNote = el('span', { class: 'focus-note' });

    var chips = el('div', { class: 'focus-chips' });
    var progFill = el('i');
    var progress = el('div', { class: 'focus-progress' }, [progFill]);
    var resultWrap = el('div', { class: 'focus-result-slot' });

    /* geometry ─ the stage letterboxes; the layer hugs the video content */

    function contentRect() {
      var sw = stage.clientWidth, sh = stage.clientHeight;
      var vw = video.videoWidth || 16, vh = video.videoHeight || 9;
      var scale = Math.min(sw / vw, sh / vh);
      return { left: (sw - vw * scale) / 2, top: (sh - vh * scale) / 2,
               width: vw * scale, height: vh * scale };
    }

    function pointerFrac(e) {
      var r = frameLayer.getBoundingClientRect();
      return { x: (e.clientX - r.left) / (r.width || 1),
               y: (e.clientY - r.top) / (r.height || 1) };
    }

    function drawCrop() {
      var b = clampBox(focusBoxAt(track, video.currentTime));
      var vw = video.videoWidth, vh = video.videoHeight;
      if (!vw || !canvas.width) return;
      ctx.drawImage(video, (b.cx - b.w / 2) * vw, (b.cy - b.w / 2) * vh,
                    b.w * vw, b.w * vh, 0, 0, canvas.width, canvas.height);
    }

    /* keyframe list — chips and scrubber ticks, rebuilt after every edit */

    function refreshKeysUI() {
      clear(chips);
      if (!track.keys.length) {
        append(chips, el('span', { class: 'focus-none', text:
          'No keyframes — the full frame plays. Drag the box, pull a corner or ' +
          'scroll to zoom; every adjustment records a keyframe at the playhead.' }));
      } else {
        append(chips, el('span', { class: 'col-label', text: track.keys.length + ' keyframes' }));
        track.keys.forEach(function (k) {
          append(chips, el('span', { class: 'focus-chip' }, [
            el('button', { class: 'focus-chip-t', type: 'button',
                           text: fmtTime(k.t) + ' ×' + (1 / k.w).toFixed(1),
                           title: 'seek to this keyframe',
                           on: { click: function () { video.currentTime = k.t; } } }),
            el('button', { class: 'focus-chip-x', type: 'button', text: '✕',
                           'aria-label': 'delete keyframe at ' + fmtTime(k.t),
                           on: { click: function () {
                             var at = track.keys.indexOf(k);
                             if (at >= 0) track.keys.splice(at, 1);
                             refreshKeysUI();
                           } } })
          ]));
        });
      }
      clear(ticks);
      if (video.duration) {
        track.keys.forEach(function (k) {
          append(ticks, el('i', { class: 'focus-tick',
                                  style: 'left:' + (k.t / video.duration * 100) + '%' }));
        });
      }
    }

    /* box editing — move, corner-resize, wheel zoom; all key at the playhead */

    var drag = null;
    box.addEventListener('pointerdown', function (e) {
      if (rendering) return;
      var h = e.target.getAttribute && e.target.getAttribute('data-h');
      drag = { mode: h || 'move', b0: clampBox(focusBoxAt(track, video.currentTime)),
               p0: pointerFrac(e) };
      box.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    box.addEventListener('pointermove', function (e) {
      if (!drag) return;
      var p = pointerFrac(e), b0 = drag.b0, b;
      if (drag.mode === 'move') {
        b = { cx: b0.cx + (p.x - drag.p0.x), cy: b0.cy + (p.y - drag.p0.y), w: b0.w };
      } else {
        // Opposite corner anchored, aspect locked — the pointer's larger reach wins.
        var ax = drag.mode.indexOf('w') >= 0 ? b0.cx + b0.w / 2 : b0.cx - b0.w / 2;
        var ay = drag.mode.indexOf('n') >= 0 ? b0.cy + b0.w / 2 : b0.cy - b0.w / 2;
        var w = Math.min(1, Math.max(0.08, Math.max(Math.abs(p.x - ax), Math.abs(p.y - ay))));
        b = { cx: ax + (drag.mode.indexOf('w') >= 0 ? -w / 2 : w / 2),
              cy: ay + (drag.mode.indexOf('n') >= 0 ? -w / 2 : w / 2), w: w };
      }
      upsertFocusKey(track, video.currentTime, b);
    });
    function endDrag() { if (drag) { drag = null; refreshKeysUI(); } }
    box.addEventListener('pointerup', endDrag);
    box.addEventListener('pointercancel', endDrag);

    stage.addEventListener('wheel', function (e) {
      if (rendering || focusSession.preview || !video.videoWidth) return;
      e.preventDefault();
      var b = clampBox(focusBoxAt(track, video.currentTime));
      upsertFocusKey(track, video.currentTime,
        { cx: b.cx, cy: b.cy, w: b.w * Math.exp(e.deltaY * 0.0015) });
      refreshKeysUI();
    }, { passive: false });

    /* transport */

    playBtn.addEventListener('click', function () {
      if (video.paused) video.play(); else video.pause();
    });
    video.addEventListener('play', function () {
      playBtn.textContent = 'Pause';
      if (!rendering) focusSession.playing = true;
    });
    video.addEventListener('pause', function () {
      playBtn.textContent = 'Play';
      if (!rendering) focusSession.playing = false;
    });

    var scrubbing = false;
    scrub.addEventListener('input', function () {
      scrubbing = true;
      if (video.duration) video.currentTime = (+scrub.value / 1000) * video.duration;
    });
    scrub.addEventListener('change', function () { scrubbing = false; });

    keyBtn.addEventListener('click', function () {
      upsertFocusKey(track, video.currentTime, focusBoxAt(track, video.currentTime));
      refreshKeysUI();
    });
    clearBtn.addEventListener('click', function () {
      track.keys.length = 0;
      refreshKeysUI();
    });

    detectBtn.addEventListener('click', function () {
      var strip = ((task.strips || {})[clip] || []).slice(0, 48);
      if (!strip.length) {
        saveNote.textContent = 'no extracted frames to detect from — re-run build_portal_data.py';
        return;
      }
      detectBtn.disabled = true;
      saveNote.textContent = 'detecting · ' + strip.length + ' frames → Gemini 3.6 Flash…';
      fetch('/api/video/run', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: FOCUS_MODEL, task_code: task.code, clip: clip, frames: strip,
          system: DETECT_SYSTEM, user_text: detectPrompt(strip.length)
        })
      }).then(function (r) { return r.json(); }).then(function (j) {
        detectBtn.disabled = rendering;
        if (!j || j.error) {
          saveNote.textContent = 'detect failed — ' + ((j && (j.message || j.error)) || 'no reply');
          return;
        }
        var entries = parseDetectedBoxes(j.text);
        var keys = entries ? detectedKeys(entries, strip.map(tsSeconds)) : [];
        if (!keys.length) {
          saveNote.textContent = 'detect failed — the reply held no usable boxes';
          return;
        }
        track.keys = keys;
        refreshKeysUI();
        saveNote.textContent = 'Gemini keyed ' + keys.length + ' of ' + strip.length +
          ' frames — adjust by hand, then Save track';
      }).catch(function () {
        detectBtn.disabled = rendering;
        saveNote.textContent = 'static serve — detection needs serve.py and its key';
      });
    });

    function syncPreview() {
      stage.classList.toggle('is-preview', focusSession.preview || rendering);
      prevBtn.textContent = focusSession.preview ? 'Edit box' : 'Cropped preview';
    }
    prevBtn.addEventListener('click', function () {
      focusSession.preview = !focusSession.preview;
      syncPreview();
    });

    /* lifecycle */

    video.addEventListener('loadedmetadata', function () {
      // Output keeps the clip's aspect at its own resolution, capped and even —
      // encoders reject odd dimensions.
      var outW = Math.min(video.videoWidth || 640, 1280); outW -= outW % 2;
      var outH = Math.round(outW * video.videoHeight / video.videoWidth / 2) * 2;
      canvas.width = outW; canvas.height = outH || 2;
      if (focusSession.time > 0 && focusSession.time < video.duration) {
        video.currentTime = focusSession.time;
      }
      if (focusSession.playing && !rendering) video.play();
      refreshKeysUI();
    });
    video.addEventListener('error', function () {
      notice.hidden = false;
      frameLayer.style.display = 'none';
      [playBtn, keyBtn, prevBtn, renderBtn].forEach(function (b) { b.disabled = true; });
      timeLbl.textContent = 'no video';
    });

    function tick() {
      if (!stage.isConnected) return;  // re-render replaced this editor — stop
      requestAnimationFrame(tick);
      if (!video.videoWidth) return;
      var r = contentRect();
      frameLayer.style.left = r.left + 'px';
      frameLayer.style.top = r.top + 'px';
      frameLayer.style.width = r.width + 'px';
      frameLayer.style.height = r.height + 'px';
      var t = video.currentTime;
      focusSession.time = t;
      var b = clampBox(focusBoxAt(track, t));
      box.style.left = ((b.cx - b.w / 2) * 100) + '%';
      box.style.top = ((b.cy - b.w / 2) * 100) + '%';
      box.style.width = (b.w * 100) + '%';
      box.style.height = (b.w * 100) + '%';
      boxTag.textContent = fmtTime(t) + ' · ×' + (1 / b.w).toFixed(1);
      zoomLbl.textContent = '×' + (1 / b.w).toFixed(1);
      if (video.duration) {
        if (!scrubbing) scrub.value = String(Math.round(t / video.duration * 1000));
        timeLbl.textContent = fmtTime(t) + ' / ' + fmtTime(video.duration);
        if (rendering) progFill.style.width = (t / video.duration * 100) + '%';
      }
      if ((focusSession.preview || rendering) && canvas.width) drawCrop();
    }
    requestAnimationFrame(tick);

    /* the final video — the crop track rendered in real time to a file */

    function pickMime() {
      var mimes = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm', 'video/mp4'];
      for (var i = 0; i < mimes.length; i++) {
        if (MediaRecorder.isTypeSupported(mimes[i])) return mimes[i];
      }
      return '';
    }

    function setBusy(busy) {
      [playBtn, scrub, keyBtn, detectBtn, prevBtn, renderBtn, saveBtn, clearBtn]
        .forEach(function (b) { b.disabled = busy; });
      progress.classList.toggle('is-on', busy);
      renderBtn.textContent = busy ? 'Rendering — playing the clip once…' : 'Render focus video';
    }

    function showResult() {
      clear(resultWrap);
      var res = focusSession.results[key];
      if (!res) return;
      append(resultWrap, el('div', { class: 'focus-result' }, [
        el('div', { class: 'focus-result-row' }, [
          el('span', { class: 'col-label', text: 'Final focus video' }),
          el('a', { class: 'linkish', href: res.url, download: res.name, text: 'Download ' + res.name }),
          el('span', { class: 'focus-note', text: res.meta })
        ]),
        el('video', { class: 'focus-result-video', src: res.url, controls: true, playsinline: true })
      ]));
    }

    function renderFocusVideo() {
      if (rendering || !video.videoWidth) return;
      if (!window.MediaRecorder || !canvas.captureStream) {
        saveNote.textContent = 'this browser has no MediaRecorder — cannot render here';
        return;
      }
      recMime = pickMime();
      var rec;
      try {
        rec = new MediaRecorder(canvas.captureStream(30),
          recMime ? { mimeType: recMime, videoBitsPerSecond: 8000000 }
                  : { videoBitsPerSecond: 8000000 });
      } catch (err) {
        saveNote.textContent = 'recorder refused: ' + err.message;
        return;
      }
      rendering = true;
      setBusy(true);
      syncPreview();
      var chunks = [];
      rec.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
      rec.onstop = function () {
        rendering = false;
        setBusy(false);
        syncPreview();
        progFill.style.width = '0';
        var old = focusSession.results[key];
        if (old) URL.revokeObjectURL(old.url);
        var blob = new Blob(chunks, { type: recMime || 'video/webm' });
        focusSession.results[key] = {
          url: URL.createObjectURL(blob),
          name: clip + '__focus.' + (recMime.indexOf('mp4') >= 0 ? 'mp4' : 'webm'),
          meta: (blob.size / 1048576).toFixed(1) + ' MB · ' + canvas.width + '×' + canvas.height +
                ' · ' + track.keys.length + ' keyframes, rendered in-browser'
        };
        showResult();
      };
      video.pause();
      var onEnded = function () {
        video.removeEventListener('ended', onEnded);
        drawCrop();  // land the last frame before the stream closes
        rec.stop();
      };
      var onSeeked = function () {
        video.removeEventListener('seeked', onSeeked);
        drawCrop();
        rec.start(250);
        video.addEventListener('ended', onEnded);
        video.play();
      };
      video.addEventListener('seeked', onSeeked);
      video.currentTime = 0;
    }
    renderBtn.addEventListener('click', renderFocusVideo);

    saveBtn.addEventListener('click', function () {
      saveNote.textContent = 'saving…';
      fetch('/api/video/focus', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_code: task.code, clip: clip, track: { keys: track.keys } })
      }).then(function (r) {
        return r.json().then(function (j) {
          saveNote.textContent = r.ok ? 'saved · ' + j.path
            : 'save failed — ' + (j.message || j.error || r.status);
        });
      }).catch(function () {
        saveNote.textContent = 'static serve — tracks need serve.py to persist';
      });
    });

    refreshKeysUI();
    syncPreview();
    showResult();

    return el('div', { class: 'focus-editor' }, [
      stage,
      el('div', { class: 'focus-transport' }, [
        playBtn, timeLbl,
        el('span', { class: 'focus-scrub-wrap' }, [ticks, scrub]),
        zoomLbl, keyBtn
      ]),
      progress,
      el('div', { class: 'focus-actions' }, [
        detectBtn, prevBtn, renderBtn, saveBtn, clearBtn,
        el('span', { class: 'spacer' }), saveNote
      ]),
      chips,
      resultWrap
    ]);
  }

  function renderVideos(task) {
    if (!task.clips) {
      return el('div', { class: 'empty-center' }, [
        el('div', { class: 'empty-note' }, [
          el('span', { class: 'empty-title', text: 'No source video' }),
          el('span', {
            class: 'empty-body',
            text: 'The workbook records this task as "N/A (not AIM developed)". Its criteria exist regardless — a criterion cannot depend on a photograph existing.'
          })
        ])
      ]);
    }

    var names = task.clipNames;
    var ci = Math.min(state.clipIdx, names.length - 1);
    var clip = names[ci];
    // An evenly spaced strip of the clip's extracted frames, named on disk.
    var strip = (task.strips || {})[clip] || [];
    var frameCount = strip.length;
    var stepsIn = task.subtasks[Math.min(ci, task.subtasks.length - 1)].stepsCount || 1;
    var fi = state.frameIdx == null ? frameCount - 1 : Math.min(state.frameIdx, frameCount - 1);

    // Frame filenames encode their source timestamp, so a frame is citable without a lookup.
    function nameOf(k) { return strip[k] || ''; }
    function tsOf(k) { return nameOf(k).replace('.jpg', ''); }

    if (!frameCount) {
      return renderNotice('No extracted frames',
        'data/thumbs/' + task.code + '/' + clip + '/ is empty — re-run scripts/build_portal_data.py.');
    }

    var bandOf = Math.min(Math.floor(fi / (frameCount / stepsIn)), stepsIn - 1);

    var clipList = el('div', { class: 'clips' }, [
      el('div', { class: 'clips-label', text: 'Clips · data/videos/' + task.code + '/' })
    ].concat(names.map(function (name, i) {
      return el('button', {
        class: 'clip-row', type: 'button', 'aria-current': String(i === ci),
        on: { click: function () { setState({ clipIdx: i, frameIdx: null }); } }
      }, [
        el('span', { class: 'clip-name', text: name + '.mp4' }),
        el('span', { class: 'clip-meta', text: 'sampled from 4 fps @ 960px' })
      ]);
    })));

    var focusOn = !!state.focusOn;

    var pane = el('div', { class: 'video-pane' }, [
      el('div', { class: 'video-head' }, [
        el('h2', { class: 'video-title', text: names[ci] }),
        tag('tag tag-outline', task.segmented ? 'reviewed segmentation' : 'suggested boundaries — even pace'),
        el('span', { class: 'sub-meta', text: focusOn
          ? 'drag the box · corners resize · scroll zooms · adjustments key at the playhead'
          : 'click a band to jump · click a frame to inspect' }),
        el('span', { class: 'spacer' }),
        el('button', {
          class: 'btn btn-secondary focus-toggle', type: 'button',
          'aria-pressed': String(focusOn),
          text: focusOn ? 'Exit area of focus' : 'Area of focus',
          on: { click: function () { setState({ focusOn: !state.focusOn }); } }
        })
      ]),
      focusOn ? focusEditor(task, clip) : el('div', { class: 'plate plate-video' }, [
        crosshair(40),
        plateImage(framePaths(task.code, clip, nameOf(fi)), clip),
        el('span', { class: 'plate-file', text: nameOf(fi) }),
        el('span', { class: 'tag plate-tag', text: 'frame ' + (fi + 1) + ' / ' + frameCount })
      ]),
      el('div', { class: 'bands-wrap' }, [
        el('div', { class: 'bands' }, Array.from({ length: stepsIn }, function (_, i) {
          var label = 'step ' + (i + 1) + ' of ' + stepsIn +
            (task.segmented ? ' · reviewed interval' : ' · suggested span');
          return el('button', {
            class: 'band' + (i === bandOf ? ' is-current' : ''), type: 'button',
            title: label, 'aria-label': label,
            style: 'background:' + BAND_SHADES[i % BAND_SHADES.length],
            on: { click: function () {
              var j = Math.min(Math.round((i + 1) * (frameCount / stepsIn)) - 1, frameCount - 1);
              if (state.focusOn) focusSession.time = tsSeconds(nameOf(j));
              setState({ frameIdx: j });
            } }
          });
        })),
        el('div', { class: 'bands-note' }, [
          el('span', {
            text: task.segmented
              ? 'Bands are reviewed sub-subtask intervals; colours follow the pack step.'
              : 'Bands are an even-pace guess: step i of n lands at its own boundary. Segmentation review replaces them.'
          }),
          el('span', { text: 'step ' + (bandOf + 1) + ' of ' + stepsIn })
        ])
      ]),
      el('div', { class: 'frames' }, Array.from({ length: frameCount }, function (_, i) {
        return el('button', {
          class: 'frame' + (i === fi ? ' is-current' : ''), type: 'button',
          'aria-label': 'frame ' + (i + 1), 'aria-current': String(i === fi),
          on: { click: function () {
            // In the focus editor the strip doubles as a scrubber: the frame's
            // filename timestamp is where the rebuilt player resumes.
            if (state.focusOn) focusSession.time = tsSeconds(nameOf(i));
            setState({ frameIdx: i });
          } }
        }, [
          plateImage(framePaths(task.code, clip, nameOf(i)), ''),
          el('span', { class: 'frame-ts', text: tsOf(i) })
        ]);
      }))
    ]);

    return el('div', { class: 'videos' }, [clipList, pane]);
  }

  /* tab · documentation */

  function docsFor(task) {
    var searched = task.hbProv.indexOf('searched') >= 0;
    var uncited = task.hbProv.indexOf('not cited') >= 0;
    var vol = (task.handbook || '').split(' ')[0];
    var pages = (task.handbook || '').split(' ')[1] || '';
    var hbFile = task.handbookFile || '—';
    var HEAD = 'col-label';

    // The sheet's own sections, as steps.json parsed them — not the pack's compiled
    // subtasks. The pack drops the front matter and renames as it compiles, so the
    // two lists differ on most tasks and only one of them is the sheet.
    var sheetSecs = task.sheetSections || [];
    var packSecs = task.subtasks.filter(function (s) { return !s.fromRun; })
      .map(function (s) { return s.label; });

    function carries(s) {
      var parts = [];
      if (s.steps) parts.push(s.steps + (s.steps === 1 ? ' step' : ' steps'));
      if (s.notes) parts.push(s.notes + ' senior mechanic ' + (s.notes === 1 ? 'note' : 'notes'));
      if (s.prereqs) parts.push(s.prereqs + ' prerequisites');
      if (s.safety) parts.push(s.safety + ' safety points');
      if (s.equipment) parts.push(s.equipment + ' equipment items');
      return parts.length ? parts.join(' · ') : 'heading only — nothing parsed under it';
    }

    var uncompiled = sheetSecs.filter(function (s) {
      return packSecs.indexOf(s.name) < 0;
    }).length;

    return [
      { name: 'Handbook extract',
        meta: task.handbook + ' · ' + (searched ? 'located by search' : uncited ? 'located, not cited' : 'cited by sheet'),
        title: 'FAA-H-8083-' + vol + ' · pages ' + pages,
        path: 'tasks/' + task.code + '/references/handbook/' + hbFile,
        prov: searched ? 'located by content search' : uncited ? 'located during compilation' : 'cited_by_source: true',
        provCls: (searched || uncited) ? 'tag tag-neutral' : 'tag tag-accent',
        warn: searched || uncited,
        warnText: searched
          ? 'The skill sheet cites no handbook, so these pages were located by content search scoped to ' + vol + ' and never reviewed. Anything drawn from them is provisional, and the pack must declare it as an assumption.'
          : 'Located during compilation rather than cited by AIM. Any standard taken from it is provisional; the pack carries a matching assumption and the linter enforces the link both ways.',
        isProse: true,
        blocks: [
          { head: 'Extract · verbatim', headStyle: HEAD, lines: [
            { n: 'p.1', text: 'Verbatim handbook text is served from the extract with its printed page label preserved, so every figure a criterion rests on can be traced to the page it came from.' },
            { n: 'p.2', text: 'A number may be credited to the handbook only if it appears verbatim here, and any handbook attribution must quote the phrase it rests on.' },
            { n: 'p.3', text: 'Where the sheet gives a figure and the handbook speaks only qualitatively — "tight and even", "as taut as possible" — the figure belongs to the procedure sheet alone.' }] },
          { head: 'Conflicts recorded, not resolved', headStyle: HEAD, lines: [
            { n: '—', text: 'Where the two sources disagree the conflict is recorded and the procedure sheet is treated as operative: the student is graded against what the instructor taught.' }] }
        ] },
      { name: 'Procedure sheet',
        meta: 'normalized · ' + sheetSecs.length + ' sections' +
          (task.sheetVariants > 1 ? ' · ' + task.sheetVariants + ' variants' : ''),
        title: 'AIM skill sheet — ' + task.title, path: 'tasks/' + task.code + '/procedure.md',
        prov: 'confidential · AIM Fremont', provCls: 'tag tag-outline', warn: false, isProse: true,
        blocks: [
          { head: 'Sections · names and counts only', headStyle: HEAD,
            lines: sheetSecs.length
              ? sheetSecs.map(function (s, i) {
                return {
                  n: String(i + 1).padStart(2, '0'),
                  text: (s.variant ? s.variant + ' · ' : '') + s.name + ' — ' + carries(s) +
                    (packSecs.indexOf(s.name) < 0 ? ' · not compiled into the pack' : '')
                };
              })
              : [{ n: '—', text: 'No steps.json for this task: the sheet was never normalized, so the pack was hand-compiled from the source document.' }] },
          { head: 'Sheet vs. pack', headStyle: HEAD, lines: [
            { n: '—', text: sheetSecs.length
              ? 'The sheet carries ' + sheetSecs.length + ' sections; the pack compiles ' + packSecs.length +
                (uncompiled ? '. The ' + uncompiled + ' left out carry no gradeable step — front matter, safety and equipment — and a criterion is never drafted from them.'
                            : '. Every section with steps was compiled.')
              : 'Nothing to compare: this pack has no normalized sheet.' }] },
          { head: 'Parsing', headStyle: HEAD, lines: [
            { n: '—', text: 'Steps carry note references as bare trailing digits: "Deburr the tubing ends2-3." means see notes 2 and 3. A reference is only accepted when it starts at the next unconsumed note number, so "Fill out block 13." reads as block 1, note 3.' },
            { n: '—', text: 'Most acceptance detail lives in the Senior Mechanic Notes, so drafting is sent the whole normalized sheet rather than the step list alone.' }] }
        ] },
      { name: 'Sources & integrity', meta: 'sha256 · ' + (task.hand ? 'hand-compiled' : 'drafted'),
        title: 'Compilation inputs', path: 'tasks/' + task.code + '/sources.json',
        prov: task.hand ? 'hand-compiled' : 'generator: packs/compile_pack.py',
        provCls: task.hand ? 'tag tag-accent' : 'tag tag-outline',
        warn: !task.hand,
        warnText: 'Drafted by anthropic/claude-opus-5 with reviewed_by: null. pack_lint --require-reviewed refuses it, which is the gate that keeps a drafted pack out of a live student session.',
        isTable: true,
        rows: (task.sources || []).concat(
          task.handbookFile ? [{ a: hbFile, b: '—', c: 'Handbook extract with provenance sidecar' }] : []) },
      { name: 'Assumptions & questions', meta: 'open items',
        title: 'Assumptions and open questions', path: 'tasks/' + task.code + '/pack.yaml',
        prov: 'linted both ways', provCls: 'tag tag-outline', warn: false, isProse: true,
        blocks: [
          { head: 'Assumptions', headStyle: HEAD,
            lines: (task.assumptions || []).length
              ? task.assumptions.map(function (a, i) { return { n: 'a' + (i + 1), text: a }; })
              : [{ n: '—', text: 'The pack records no assumptions.' }] },
          { head: 'Open questions for AIM', headStyle: HEAD,
            lines: (task.openQuestions || []).length
              ? task.openQuestions.map(function (q, i) { return { n: 'q' + (i + 1), text: q }; })
              : [{ n: '—', text: 'The pack records no open questions.' }] }
        ] }
    ];
  }

  function renderDocs(task) {
    var docs = docsFor(task);
    var di = Math.min(state.doc, docs.length - 1);
    var d = docs[di];

    var list = el('div', { class: 'doc-list' }, [
      el('div', { class: 'clips-label', text: 'Documents' })
    ].concat(docs.map(function (dd, i) {
      return el('button', {
        class: 'doc-row', type: 'button', 'aria-current': String(i === di),
        on: { click: function () { setState({ doc: i }); } }
      }, [
        el('span', { class: 'doc-name', text: dd.name }),
        el('span', { class: 'doc-meta', text: dd.meta })
      ]);
    })).concat([
      el('div', { class: 'doc-order' }, [
        el('span', { class: 'col-label', text: 'Reading order' }),
        el('span', {
          class: 'doc-order-text',
          text: 'Handbook first, then the skill sheet: the sheet says what to do, the handbook says what the result must measure up to. Numeric limits usually exist in only one of them.'
        })
      ])
    ]));

    var pane = [
      el('div', { class: 'doc-head' }, [
        el('h2', { class: 'doc-title', text: d.title }),
        el('span', { class: d.provCls + ' tag-xs', text: d.prov }),
        el('span', { class: 'doc-path', text: d.path })
      ])
    ];

    if (d.warn) {
      pane.push(el('div', { class: 'blueprint notice' }, [
        corners(), svg(WARN_ICON), el('span', { class: 'notice-text', text: d.warnText })
      ]));
    }

    if (d.isProse) {
      pane.push(el('div', { class: 'doc-blocks' }, d.blocks.map(function (b) {
        return el('div', { class: 'doc-block' }, [
          el('span', { class: b.headStyle, text: b.head })
        ].concat(b.lines.map(function (ln) {
          return el('div', { class: 'doc-line' }, [
            el('span', { class: 'doc-n', text: ln.n }),
            el('span', { class: 'doc-text', text: ln.text })
          ]);
        })));
      })));
    }

    if (d.isTable) {
      pane.push(el('div', { class: 'doc-table' }, [
        el('div', { class: 'doc-table-head' }, [
          el('span', { text: 'Input' }), el('span', { text: 'sha256' }), el('span', { text: 'Role' })
        ])
      ].concat(d.rows.map(function (r) {
        return el('div', { class: 'doc-table-row' }, [
          el('span', { text: r.a }), el('span', { text: r.b }), el('span', { text: r.c })
        ]);
      }))));
      pane.push(el('span', { class: 'note', style: 'max-width:760px' }, [
        'The linter re-hashes every input: a pack fails if its source sheet changed after ingest. It also checks that each ',
        el('span', { class: 'mono', text: 'assumed: true' }), ' item has a matching entry in ',
        el('span', { class: 'mono', text: 'assumptions' }), ', in both directions.'
      ]));
    }

    return el('div', { class: 'docs' }, [list, el('div', { class: 'doc-pane' }, pane)]);
  }

  /* ── render · evals dashboard ──────────────────────────────────────────── */

  // Both lines quote the run they are drawn from rather than a figure typed in here.
  function evalsSubtitle() {
    var pts = (DATA.evals.totals || [])[1] || '—';
    var r = DATA.evals.run || {};
    // Controls are counted, not assumed: a point whose perturbation was dropped has none.
    var ctl = r.controls != null ? r.controls.toLocaleString() + ' controls' : 'controls not recorded';
    var cost = r.cost != null ? r.cost
      : taskList().reduce(function (n, t) { return n + (t.runCost || 0); }, 0);
    return 'every subtask sheet, graded against its own perturbed sheet on the same frames · ' +
      pts + ' points · ' + ctl + ' · ' + (r.models || modelNames().length) +
      ' models · $' + cost.toFixed(2);
  }

  // The table sorts by drop, so the tasks it ends on are read off the sorted rows
  // rather than named here — the names were typed in and the sort can move them.
  function bottomTwo() {
    var rows = DATA.evals.taskRows || [];
    var last = rows.slice(-2).map(function (r) { return r[0]; });
    return last.length === 2 ? 'two (' + last.join(', ') + ')' : 'rows';
  }

  // Tasks with criteria and no saved run, named from the index rather than asserted.
  function noRunNote() {
    var none = taskList().filter(function (t) { return !t.runCalls; });
    if (!none.length) return 'Every task has a saved run.';
    return none.map(function (t) { return t.code; }).join(', ') +
      (none.length === 1 ? ' has criteria and no run' : ' have criteria and no run') +
      ' — no source video to draw a frame from.';
  }

  function acceptedSpread() {
    var rows = (DATA.evals.modelRows || []).slice().sort(function (a, b) {
      return parseInt(a[6], 10) - parseInt(b[6], 10);
    });
    if (rows.length < 2) return '';
    var lo = rows[0], hi = rows[rows.length - 1];
    return lo[0] + ': ' + lo[6] + ' in ' + lo[4] + ' decisive pairs. ' +
      hi[0] + ': ' + hi[6] + ' in ' + hi[4] + '.';
  }

  function renderEvals() {
    var modelTable = el('div', { class: 'table-box' }, [
      el('div', { class: 'model-head' }, ['Model', 'Criteria', 'Perturbed', 'Drop', 'Decisive', 'Flipped', 'Accepted ⚠']
        .map(function (h) { return el('span', { text: h }); }))
    ].concat((DATA.evals.modelRows || []).map(function (m) {
      return el('div', { class: 'model-row' + (m[7] ? ' is-highlight' : '') }, [
        el('span', { text: m[0] }), el('span', { text: m[1] }), el('span', { text: m[2] }),
        el('span', { text: m[3] }), el('span', { text: m[4] }), el('span', { text: m[5] }),
        el('span', {
          class: 'model-acc' + (parseInt(m[6], 10) > 10 ? ' is-high' : ''),
          text: m[6] + ' / ' + m[4]
        })
      ]);
    })));

    var taskTable = el('div', { class: 'table-box' }, [
      el('div', { class: 'eval-head' }, ['Task', 'Points', 'Criteria', 'Perturbed', 'Drop', 'Decisive', 'Flipped', 'Accepted']
        .map(function (h) { return el('span', { text: h }); }))
    ].concat((DATA.evals.taskRows || []).map(function (r) {
      return el('button', {
        class: 'eval-row', type: 'button',
        on: { click: function () { openTask(r[0], 'assess'); } }
      }, [
        el('span', {}, [el('b', { text: r[0] }), ' ', el('span', { class: 'eval-short', text: r[1] })]),
        el('span', { text: r[2] }), el('span', { text: r[3] }), el('span', { text: r[4] }),
        el('span', { class: 'eval-drop', text: r[5] }), el('span', { text: r[6] }),
        el('span', { text: r[7] }), el('span', { text: r[8] })
      ]);
    })).concat([
      el('div', { class: 'eval-total' },
        (DATA.evals.totals || []).map(function (v) { return el('span', { text: v }); }))
    ]));

    // Readiness is counted by the build script off the tree, not typed in here.
    var rd = DATA.evals.readiness || {
      labeledDatasets: 0, agentRuns: 0, errorClips: 0, errorClipTasks: 0,
      labeledNegativeAtoms: 0, tasks: taskList().length,
      atoms: (DATA.index.stats || {}).atoms || 0,
      photoEvalTasks: taskList().filter(function (t) { return t.runCalls; }).length
    };

    return el('div', { class: 'screen' }, [
      el('div', { class: 'screen-head' }, [
        el('h1', { class: 'screen-title', text: 'Evals — criteria vs. their perturbations' }),
        el('span', {
          class: 'screen-sub',
          text: evalsSubtitle()
        })
      ]),
      el('div', { class: 'blueprint notice' }, [
        corners(), svg(WARN_ICON),
        el('span', { class: 'notice-text' }, [
          el('b', { text: 'One-class data: every reference frame is work an instructor accepted.' }),
          ' ' + rd.labeledDatasets + ' of ' + rd.tasks + ' tasks have labeled negatives, so precision and defect recall cannot be computed — the perturbation controls below are the floor that separates a grader from a model that passes everything.'
        ])
      ]),
      el('div', { class: 'evals-cols' }, [
        el('div', { class: 'evals-models' }, [
          el('h2', { class: 'sec-title', text: 'Which grader to trust' }),
          modelTable,
          el('span', { class: 'note' }, [
            el('b', { text: 'Accepted' }),
            ' = passed a criterion on work that contradicts it, where the same model had shown it can see the condition. Raw pass rates look interchangeable; this column is what separates them. ' + acceptedSpread()
          ])
        ]),
        el('div', { class: 'evals-ready' }, [
          el('h2', { class: 'sec-title', text: 'Dataset readiness' }),
          el('div', { class: 'blueprint', style: 'padding:12px 14px;display:flex;flex-direction:column;gap:8px' }, [
            corners(),
            el('div', { class: 'kv' }, [
              el('span', { text: 'Labeled datasets (evals/datasets/)' }),
              el('b', { text: String(rd.labeledDatasets) })
            ]),
            el('div', { class: 'kv' }, [
              el('span', { text: 'Agent runs (build/evals/runs/)' }),
              el('b', { text: String(rd.agentRuns) })
            ]),
            el('div', { class: 'kv' }, [
              el('span', { text: 'Photo-eval runs (build/photo_eval/)' }),
              el('b', { text: rd.photoEvalTasks + ' tasks' })
            ]),
            // Generated error clips are not labels — they are counted, and named as
            // what they are, rather than left out because they are not a dataset yet.
            el('div', { class: 'kv' }, [
              el('span', { text: 'Generated error clips (build/error_generation/)' }),
              el('b', { text: rd.errorClips + (rd.errorClips ? ' · ' + rd.errorClipTasks + ' task' + (rd.errorClipTasks === 1 ? '' : 's') : '') })
            ]),
            el('div', { class: 'kv' }, [
              el('span', { text: 'Atoms with labeled negatives' }),
              tag('tag tag-neutral', rd.labeledNegativeAtoms.toLocaleString() + ' of ' + (rd.atoms || 0).toLocaleString())
            ])
          ]),
          el('span', {
            class: 'note',
            text: 'Precision, recall, defect recall and coverage appear here once a labeled dataset and an agent run share a task. The UI does not invent metrics when either side is missing.'
          })
        ])
      ]),
      renderVideoTally(),
      el('div', { style: 'display:flex;flex-direction:column;gap:8px' }, [
        el('h2', { class: 'sec-title', text: 'Which tasks produce gradeable evidence' }),
        taskTable,
        el('span', {
          class: 'note',
          text: 'The drop measures the photograph as much as the grader — it orders tasks by how gradeable their evidence is. Top rows are worksheet photos; the bottom ' +
            bottomTwo() + ' take frames from mid-action head-mounted footage. ' + noRunNote() +
            ' Click a row to open the task.'
        })
      ])
    ]);
  }

  /* The video assessments, tallied over the newest valid run per task — the
     same runs the Video assessment tab draws, so this table and those grids
     agree. No perturbed column here: controls ride the photo runs only, so a
     video tally that printed one would be inventing a number. */
  function renderVideoTally() {
    var v = DATA.evals.video;
    if (!v || !v.totals || !v.totals.graded) return null;
    var t = v.totals;
    var table = el('div', { class: 'table-box' }, [
      el('div', { class: 'vev-head' }, ['Model', 'Pass', 'Fail', 'Unsure', 'Ungraded', 'Pass rate']
        .map(function (h) { return el('span', { text: h }); }))
    ].concat((v.models || []).map(function (m) {
      return el('div', { class: 'vev-row' }, m.map(function (x) {
        return el('span', { text: x });
      }));
    })).concat([
      el('div', { class: 'vev-row vev-total' }, [
        el('span', { text: 'All arms' }),
        el('span', { text: String(t.pass) }),
        el('span', { text: String(t.fail) }),
        el('span', { text: String(t.unsure) }),
        el('span', { text: String(t.ungraded) }),
        el('span', { text: t.graded ? Math.round((100 * t.pass) / t.graded) + '%' : '—' })
      ])
    ]));
    return el('div', { style: 'display:flex;flex-direction:column;gap:8px' }, [
      el('h2', { class: 'sec-title', text: 'Video assessment — overall tally' }),
      table,
      el('span', { class: 'note', text:
        t.graded.toLocaleString() + ' verdicts over ' + t.tasks + ' tasks · ' +
        t.calls + ' calls at 0.5 fps · $' + (t.cost || 0).toFixed(2) + '. The same ' +
        'compiled points the photo runs grade, moved onto the span — an ungraded ' +
        'point is a reply that stopped short of it, never a verdict. Controls ride ' +
        'the photo runs only, so no perturbed column appears here.' })
    ]);
  }

  function renderNotice(title, body) {
    return el('div', { class: 'empty-center' }, [
      el('div', { class: 'empty-note' }, [
        el('span', { class: 'empty-title', text: title }),
        el('span', { class: 'empty-body', text: body })
      ])
    ]);
  }

  /* ── routing ───────────────────────────────────────────────────────────── */

  function syncHash() {
    // Before the index lands no code resolves, so writing the hash here would
    // erase the deep link the page was opened on.
    if (!DATA.index) return;
    var task = findTask(state.taskCode);
    var hash = state.nav === 'evals' ? '#/evals'
      : (state.nav === 'task' && task) ? '#/tasks/' + task.code + '/' + state.tab
      : '#/tasks';
    if (location.hash !== hash) history.replaceState(null, '', hash);
  }

  function readHash() {
    var parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
    if (parts[0] === 'evals') return { nav: 'evals', taskCode: null };
    if (parts[0] === 'tasks' && parts[1]) {
      var task = findTask(decodeURIComponent(parts[1]));
      if (task) {
        var tab = TABS.some(function (t) { return t.id === parts[2]; }) ? parts[2] : 'detail';
        return { nav: 'task', taskCode: task.code, tab: tab };
      }
    }
    return { nav: 'home', taskCode: null };
  }

  // Landing on a different task by URL has to start it at its first subtask — otherwise
  // the rail selection, clip and document carry over from whatever was open before.
  function applyRoute() {
    var route = readHash();
    var newTask = route.taskCode !== state.taskCode;
    Object.assign(state, route, { reply: null, expanded: null });
    if (newTask) Object.assign(state, { sub: 0, clipIdx: 0, frameIdx: null, doc: 0 });
  }

  /* ── top-level render ──────────────────────────────────────────────────── */

  // Panes that own their own scroll. Every render rebuilds the DOM, so a screen that
  // has not actually changed has to be handed its scroll position back — otherwise
  // opening a reply or ticking Confidence throws you to the top of the grid.
  var SCROLLERS = ['.assess-right', '.assess-left', '.pane-steps', '.pane-side',
                   '.video-pane', '.clips', '.doc-pane', '.doc-list'];
  var lastScreen = null;

  function screenKey() {
    return [state.nav, state.taskCode, state.tab, state.sub].join('|');
  }

  function readScroll(sels) {
    var snap = {};
    sels.forEach(function (sel) {
      var n = document.querySelector(sel);
      if (n) snap[sel] = n.scrollTop;
    });
    return snap;
  }

  function writeScroll(snap) {
    Object.keys(snap).forEach(function (sel) {
      var n = document.querySelector(sel);
      if (n && snap[sel]) n.scrollTop = snap[sel];
    });
  }

  function render() {
    // Moving to another screen starts at the top; toggling a cell keeps your place.
    var key = screenKey();
    var sameScreen = key === lastScreen;
    var keep = readScroll(sameScreen ? SCROLLERS.concat(['.main', '.sidebar']) : ['.sidebar']);
    lastScreen = key;

    renderHeader();
    renderSidebar();
    clear($main);

    if (DATA.error) {
      append($main, renderNotice('Could not read the extract', DATA.error +
        ' — run scripts/build_portal_data.py from the repo root, then reload.'));
    } else if (!DATA.index) {
      append($main, renderNotice('Loading', 'Reading data/index.json.'));
    } else if (state.nav === 'evals') {
      append($main, renderEvals());
    } else if (state.nav === 'task' && findTask(state.taskCode)) {
      var task = fullTask(state.taskCode);
      append($main, task ? renderTask(task)
        : renderNotice('Loading ' + state.taskCode, 'Reading its pack, criteria and saved run.'));
    } else {
      append($main, renderHome());
    }

    if (!sameScreen) $main.scrollTop = 0;
    writeScroll(keep);

    // A reply opened below the fold is a reply nobody sees.
    var reply = $main.querySelector('.reply');
    if (reply) reply.scrollIntoView({ block: 'nearest' });

    syncHash();
  }

  /* ── wiring ────────────────────────────────────────────────────────────── */

  document.getElementById('go-home').addEventListener('click', function () {
    setState({ nav: 'home', taskCode: null, reply: null });
  });
  $navTasks.addEventListener('click', function () {
    setState({ nav: 'home', taskCode: null, reply: null });
  });
  $navEvals.addEventListener('click', function () {
    setState({ nav: 'evals', reply: null });
  });
  document.getElementById('show-confidence').addEventListener('change', function (e) {
    setState({ showConfidence: e.target.checked });
  });
  window.addEventListener('hashchange', function () {
    applyRoute();
    ensureTask(state.taskCode);
    render();
  });

  // The route can only be resolved once the index names the tasks, so the first
  // paint is the loading notice and the real route lands with the data.
  render();
  Promise.all([getJSON('data/index.json'), getJSON('data/evals.json')])
    .then(function (loaded) {
      DATA.index = loaded[0];
      DATA.evals = loaded[1];
      applyRoute();
      ensureTask(state.taskCode);
    })
    .catch(function (e) { DATA.error = String((e && e.message) || e); })
    .then(render);
})();
