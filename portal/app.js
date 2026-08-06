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

  /* ── state ─────────────────────────────────────────────────────────────── */

  var state = {
    nav: 'home',        // 'home' | 'task' | 'evals'
    taskCode: null,
    tab: 'detail',      // 'detail' | 'assess' | 'videos' | 'docs'
    sub: 0,
    expanded: null,     // step id whose checks/errors are open
    clipIdx: 0,
    frameIdx: null,     // null → the clip's last frame
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
      expanded: null, clipIdx: 0, frameIdx: null, reply: null, doc: 0
    });
  }

  /* ── derivations ───────────────────────────────────────────────────────── */

  function subIndex(task) { return Math.min(state.sub, task.subtasks.length - 1); }

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
    if (state.tab !== 'docs') parts.push(renderRail(task, i));

    if (state.tab === 'detail') parts.push(renderHierarchy(task, st));
    else if (state.tab === 'assess') parts.push(renderAssess(task, st));
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
        el('div', { class: 'run-row' }, [
          el('button', { class: 'btn btn-primary blueprint', type: 'button' }, [corners(), 'Run · 4 models']),
          el('span', { class: 'plate-note', text: runCost })
        ])
      ])
    ]);

    var right = el('div', { class: 'assess-right' },
      st.hasRun ? renderGrid(task, st) : [
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

  function renderGrid(task, st) {
    var run = st.raw.run;

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
      el('span', { class: 'control-stats', text: st.controlStats })
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

  /* tab · videos & frames */

  var BAND_SHADES = ['var(--color-accent-300)', 'var(--color-accent-500)',
                     'var(--color-accent-700)', 'var(--color-accent-400)', 'var(--color-accent-600)'];

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

    var pane = el('div', { class: 'video-pane' }, [
      el('div', { class: 'video-head' }, [
        el('h2', { class: 'video-title', text: names[ci] }),
        tag('tag tag-outline', task.segmented ? 'reviewed segmentation' : 'suggested boundaries — even pace'),
        el('span', { class: 'sub-meta', text: 'click a band to jump · click a frame to inspect' })
      ]),
      el('div', { class: 'plate plate-video' }, [
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
              setState({ frameIdx: Math.min(Math.round((i + 1) * (frameCount / stepsIn)) - 1, frameCount - 1) });
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
          on: { click: function () { setState({ frameIdx: i }); } }
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
