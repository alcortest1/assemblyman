/* AIM Inspector — data inspection and evals for the Alcor × AIM Fremont pilot.
 *
 * Ported from the Claude Design source "AIM Inspector.dc.html". The screens, copy,
 * state shape and interaction rules follow that design; the browser-chrome frame it
 * was mocked inside is dropped, since here the browser is the browser. The design's
 * `url` value drives the real address bar instead, so a screen is linkable.
 *
 * No build step and no dependencies: index.html holds the shell, this file owns
 * state and builds the screens. The task data below is the design's seeded pilot
 * set — swap `DATA` for the live `alcor_agents/inspector/server.py` API to put it
 * on the working tree.
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

  /* ── seeded data ───────────────────────────────────────────────────────── */

  var RAIL_TS = ['t000041_50', 't000027_25', 't000018_00', 't000033_75', 't000029_50',
                 't000024_25', 't000031_00', 't000022_50', 't000019_75'];

  function mk(code, short, title, subject, steps, corr, def, targets, handbook, hbProv,
              clipNames, segmented, hand, subs) {
    return {
      code: code, short: short, title: title, subject: subject, steps: steps,
      corr: corr, def: def, targets: targets, handbook: handbook, hbProv: hbProv,
      clipNames: clipNames, clips: clipNames.length, segmented: segmented, hand: hand,
      atoms: corr + def,
      subtasks: subs.map(function (s, i) {
        return Object.assign({ ts: RAIL_TS[i % RAIL_TS.length] }, s);
      })
    };
  }

  function sub(label, sheet, n, atoms) {
    return { label: label, sheet: sheet, stepsCount: n, atomsCount: atoms, steps: null };
  }
  function C(id, text, obs, src) { return { id: id, text: text, obs: obs, src: src }; }
  function E(id, text, sev) { return { id: id, text: text, sev: sev }; }

  var ds1Clips = ['route_the_line', 'cut_the_line', 'deburr_the_line', 'bend_the_line',
                  'flare_the_line', 'test_fluid_line', 'install_fluid_line'];

  var ds1 = mk('AM.I.D.S1', 'Rigid line', 'Fabricate a rigid line with a flare and a bend',
    'General', 23, 95, 73, 27, '30B 9-1..9-7', 'cited', ds1Clips, false, false, [
      Object.assign(sub('Route the line', 'route_the_line', 3, 16), {
        steps: [
          { id: 'dl.s1', text: 'Use the safety wire method to map tubing length and bends.',
            checks: [
              C('c1', 'Both ends of the safety wire are seated into their respective fittings, with the wire spanning the complete route.', 'photo', 'Sheet: cut excess "to allow it to feed into the other fitting"'),
              C('c2', 'Felt-tip marks are present on the wire at each bend location and at both fitting ends.', 'photo', 'Sheet: "Use a marker to mark the location of each bend and ends."'),
              C('c3', 'The wire path clears surrounding structure and components rather than cutting through an obstruction.', 'photo', 'Sheet: route "taking the most appropriate route"'),
              C('c4', 'No bend in the mapped wire route is tighter than the standard bend radius for the tube size selected.', 'measurement', 'Handbook FAA-H-8083-30B p.9-2: standard bend radii by tube size — excluded from photo criterion')],
            errors: [
              E('e1', 'Wire ends not fed fully into the fittings — mapped length short, finished line will not reach.', 'critical'),
              E('e2', 'Bend locations unmarked or marked after the wire moved.', 'major'),
              E('e3', 'Route cuts through an obstruction the tube cannot share.', 'critical')] },
          { id: 'dl.s2', text: 'Carefully remove the wire, keeping its shape.',
            checks: [
              C('c1', 'Wire is free of the fittings/airframe and held clear as a separate shaped piece.', 'photo', 'Source: procedure sheet'),
              C('c2', 'Removed wire retains its bent contour, not straightened or flattened.', 'photo', 'Source: procedure sheet'),
              C('c3', 'A drawing or photograph of the wire’s shape exists as a record before further handling.', 'document', 'Source: procedure sheet, note 2 — excluded from photo criterion')],
            errors: [
              E('e1', 'Wire shown pulled straight or bends visibly opened out.', 'critical'),
              E('e2', 'Marks on wire smeared or absent.', 'major')] },
          { id: 'dl.s3', text: 'Straighten the wire and measure the total length.',
            checks: [
              C('c1', 'The safety wire lies straight along its full length, no bows, kinks or curls.', 'photo', 'Source: procedure sheet'),
              C('c2', 'The wire is laid alongside a tape measure or scale for reading.', 'photo', 'Source: procedure sheet'),
              C('c3', 'The recorded tubing length equals the wire length plus 1/2 inch for each marked bend.', 'document', 'Sheet note 3: bend allowance — excluded from photo criterion')],
            errors: [
              E('e1', 'Wire still visibly bowed or kinked while being measured.', 'major')] }
        ],
        sheetPoints: [
          { n: '1.', text: 'Safety wire spans the full route with both ends inserted into their fittings.' },
          { n: '2.', text: 'Felt-tip marks visible on the wire at bend points and both ends.' },
          { n: '3.', text: 'Wire path runs clear of surrounding structure, not through an obstruction.' },
          { n: 'D1.', text: 'graded as absence: wire end loose or not seated in a fitting.' }],
        excluded: '[measurement] bend radius vs. standard table — shown here, never sent to a grader.',
        run: {
          rows: [
            { label: '1 · wire spans route, ends seated',
              cells: [['pass', '0.82'], ['pass', '0.78'], ['pass', '0.74'], ['pass', '0.66']],
              neg: { label: 'P1 · safety wire spans the route with at least 3 in of slack beyond each fitting',
                src: 'spans the route → spans it with 3 in of slack',
                cells: [['fail', '✓'], ['fail', '✓'], ['fail', '✓'], ['accepted', 'pass ✗ accepted']] } },
            { label: '2 · felt-tip marks at bends + ends',
              cells: [['unsure', 'occluded'], ['pass', '0.71'], ['unsure', 'hand covers'], ['pass', '0.69']],
              skip: 'P2 dropped — perturbing "marks at each bend" to "marks every 2 in along the wire" needs a scale reference in frame, so it can only return unsure' },
            { label: '3 · path clears structure',
              cells: [['pass', '0.88'], ['pass', '0.90'], ['pass', '0.85'], ['pass', '0.81']],
              neg: { label: 'P3 · wire path crosses through structure at one point, as the route requires',
                src: 'runs clear of structure → crosses through it',
                cells: [['fail', '✓'], ['fail', '✓'], ['unsure', 'not_pass ✓'], ['fail', '✓']] } },
            { label: 'D1 · no loose / unseated wire end',
              cells: [['pass', '0.79'], ['pass', '0.76'], ['pass', '0.72'], ['pass', '0.70']],
              neg: { label: 'P4 · a wire end protruding more than 1/16 in from its fitting is the defect',
                src: 'defect threshold: unseated → protruding >1/16 in',
                cells: [['fail', '✓'], ['fail', '✓'], ['fail', '✓'], ['unsure', 'not_pass ✓']] } }],
          rollup: [['review', 'review · 1 unsure'], ['pass', 'pass · 4/4'],
                   ['review', 'review · 1 unsure'], ['pass', 'pass · 4/4']],
          controlStats: '12 perturbed points · not passed 11 · accepted 1 (GPT-5.6 Sol)',
          negLines: [
            { mark: 'P1', from: '1. Safety wire spans the full route with both ends inserted into their fittings.', text: 'Safety wire spans the full route with at least 3 in of slack beyond each fitting.', status: 'perturbed' },
            { mark: 'P2', from: '2. Felt-tip marks visible on the wire at bend points and both ends.', text: 'Felt-tip marks appear every 2 in along the wire, as well as at bends and ends.', status: 'skipped · needs a scale' },
            { mark: 'P3', from: '3. Wire path runs clear of surrounding structure, not through an obstruction.', text: 'Wire path crosses through structure at one point, as the route requires.', status: 'perturbed' },
            { mark: 'P4', from: 'D1. wire end loose or not seated in a fitting.', text: 'A wire end protruding more than 1/16 in from its fitting is the defect.', status: 'perturbed' }],
          replies: {
            'r0m0': 'The safety wire runs continuously from the upper fitting to the lower manifold fitting; both ends disappear into the fitting bores, consistent with being seated. Confidence 0.82.',
            'r0m1': 'Wire visible across the full span; both terminations at fittings. Slight glare at the lower end but the wire clearly enters the bore. Confidence 0.78.',
            'r0m2': 'Both ends appear engaged with their fittings. The route is continuous. Confidence 0.74.',
            'r0m3': 'Wire spans the route. The lower termination is partially shadowed; I read it as seated. Confidence 0.66.',
            'r1m0': 'Unsure — the operator’s left hand covers the mid-span where bend marks would sit, and at 960px felt-tip contrast on 0.041” wire is below what I can resolve. A macro shot of the marked spans would settle this.',
            'r1m1': 'Two dark transverse marks visible near the upper bend and at the free end. I take these as the felt-tip marks. Confidence 0.71.',
            'r1m2': 'Unsure — the hand and pliers occlude the two lower bend locations. Marks at the visible upper bend only; cannot confirm both ends.',
            'r1m3': 'Marks discernible at the upper bend and both terminations under magnification. Confidence 0.69.',
            'r2m0': 'The wire path stays clear of the bracket and the adjacent hose run; no crossing through structure. Confidence 0.88.',
            'r2m1': 'Clear standoff from surrounding structure along the visible route. Confidence 0.90.',
            'r2m2': 'No obstruction contact visible. Confidence 0.85.',
            'r2m3': 'Path is clear of structure in frame. Confidence 0.81.',
            'r3m0': 'No loose or unseated wire end is visible; both terminations engaged. Absence of defect confirmed at this resolution. Confidence 0.79.',
            'r3m1': 'The work shows no such defect. Confidence 0.76.',
            'r3m2': 'No unseated end visible. Confidence 0.72.',
            'r3m3': 'No such defect visible. Confidence 0.70.',
            'n0m0': 'Fail — the wire is routed taut between the two fittings and trimmed at each end. There is no slack, certainly not 3 in. The condition is visible and not met.',
            'n0m1': 'Fail — no slack is present at either fitting; the wire follows the route directly.',
            'n0m2': 'Fail — both ends terminate at their fittings with no excess length.',
            'n0m3': 'Pass — a loop of wire near the upper fitting could account for the slack the condition calls for. Confidence 0.61. [Accepted: the same model read this frame as a taut, fully seated route when grading the original point.]'
          }
        },
        frameProv: 'frame_suggested', frameFile: 't000041_50.jpg', frameShort: 'last frame of route_the_line',
        frameNote: 'Suggested: section 1 of 7 on route_the_line, step 3 of 3 — 100% through the clip. Not a reviewed interval; the picker overrides it.',
        refs: '1 clip', runs: '1 · 32 calls'
      }),
      sub('Cut the line', 'cut_the_line', 4, 13),
      sub('Deburr the line', 'deburr_the_line', 2, 17),
      sub('Bend the line', 'bend_the_line', 4, 35),
      sub('Flare the line', 'flare_the_line', 4, 36),
      sub('Test fluid line', 'test_fluid_line', 3, 25),
      sub('Install fluid line', 'install_fluid_line', 3, 26)
    ]);

  // The cut subtask carries lighter step detail but a full saved run.
  ds1.subtasks[1].steps = [
    { id: 'ct.s1', text: 'Decide the size of tubing to use.',
      checks: [
        C('c1', 'A single length of rigid tubing stock is selected and in hand or on the bench.', 'photo', 'Source: procedure sheet'),
        C('c2', 'Printed diameter/wall-thickness markings or alloy color band are visible on the selected tubing.', 'photo', 'Source: procedure sheet'),
        C('c3', 'Measured outside diameter of the selected stock equals the specified OD (e.g., 0.25").', 'measurement', 'Excluded from photo criterion')],
      errors: [E('e1', 'Selected stock is bare/unmarked with no identification visible.', 'major')] },
    { id: 'ct.s2', text: 'Mark the cut location per the measured wire length.',
      checks: [
        C('c1', 'A single, clean transverse mark is visible at the cut location.', 'photo', 'Source: procedure sheet'),
        C('c2', 'The marked length matches the recorded wire measurement.', 'document', 'Excluded from photo criterion')],
      errors: [E('e1', 'Multiple conflicting marks on the stock.', 'minor')] },
    { id: 'ct.s3', text: 'Cut the tubing with the tubing cutter, rotating with light, even pressure.',
      checks: [
        C('c1', 'The cut end is square to the tube axis.', 'photo', 'Handbook 30B p.9-4: "cut squarely"'),
        C('c2', 'No crush or ovality at the cut — the tube section remains round.', 'photo', 'Handbook 30B p.9-4')],
      errors: [E('e1', 'Tube crushed or visibly ovalled by over-tightening the cutter.', 'critical')] },
    { id: 'ct.s4', text: 'Verify the cut length against the plan.',
      checks: [
        C('c1', 'Tube laid against tape/scale for verification.', 'photo', 'Source: procedure sheet'),
        C('c2', 'Measured length equals planned length within tolerance.', 'measurement', 'Excluded from photo criterion')],
      errors: [E('e1', 'Length short — line cannot reach both fittings.', 'critical')] }
  ];

  Object.assign(ds1.subtasks[1], {
    sheetPoints: [
      { n: '1.', text: 'The cut end is square to the tube axis.' },
      { n: '2.', text: 'Cut is at the marked location; a single clean mark is visible.' },
      { n: '3.', text: 'Tube section remains round — no crush or ovality at the cut.' },
      { n: 'D1.', text: 'graded as absence: tube end crushed flat or kinked at the cut.' }],
    excluded: '[measurement] cut length vs. plan · [document] recorded worksheet length.',
    frameProv: 'frame_suggested', frameFile: 't000027_25.jpg', frameShort: 'last frame of cut_the_line',
    frameNote: 'Suggested: section 2 of 7, step 4 of 4 — 100% through cut_the_line. Not a reviewed interval.',
    refs: '1 clip', runs: '1 · 32 calls',
    run: {
      rows: [
        { label: '1 · cut end square to axis',
          cells: [['pass', '0.75'], ['pass', '0.80'], ['pass', '0.72'], ['pass', '0.77']],
          neg: { label: 'P1 · the cut end is chamfered at 45°, not left square to the axis',
            src: 'square to the axis → chamfered at 45°',
            cells: [['fail', '✓'], ['fail', '✓'], ['fail', '✓'], ['fail', '✓']] } },
        { label: '2 · cut at the marked location',
          cells: [['unsure', 'mark gone'], ['unsure', 'mark gone'], ['pass', '0.64'], ['unsure', 'mark gone']],
          skip: 'P2 dropped — perturbing the mark tolerance to 1/32 in needs a scale reference the frame does not carry, and the mark was consumed by the cut' },
        { label: '3 · tube remains round at the cut',
          cells: [['pass', '0.83'], ['pass', '0.86'], ['pass', '0.80'], ['pass', '0.78']],
          neg: { label: 'P3 · tube section is flattened to a visible oval at the cut, as this process requires',
            src: 'remains round → flattened to an oval',
            cells: [['fail', '✓'], ['fail', '✓'], ['fail', '✓'], ['fail', '✓']] } },
        { label: 'D1 · no crush / kink at the cut',
          cells: [['pass', '0.81'], ['pass', '0.84'], ['pass', '0.79'], ['pass', '0.76']],
          neg: { label: 'P4 · any visible tool mark on the tube surface is a critical defect',
            src: 'defect threshold: crushed/kinked → any tool mark',
            cells: [['fail', '✓'], ['fail', '✓'], ['unsure', 'not_pass ✓'], ['fail', '✓']] } }],
      rollup: [['review', 'review · 1 unsure'], ['review', 'review · 1 unsure'],
               ['pass', 'pass · 4/4'], ['review', 'review · 1 unsure']],
      controlStats: '12 perturbed points · not passed 12 · accepted 0',
      negLines: [
        { mark: 'P1', from: '1. The cut end is square to the tube axis.', text: 'The cut end is chamfered at 45°, not left square to the axis.', status: 'perturbed' },
        { mark: 'P2', from: '2. Cut is at the marked location; a single clean mark is visible.', text: 'The cut sits within 1/32 in of the marked location.', status: 'skipped · needs a scale' },
        { mark: 'P3', from: '3. Tube section remains round — no crush or ovality at the cut.', text: 'Tube section is flattened to a visible oval at the cut, as this process requires.', status: 'perturbed' },
        { mark: 'P4', from: 'D1. tube end crushed flat or kinked at the cut.', text: 'Any visible tool mark on the tube surface is a critical defect.', status: 'perturbed' }],
      replies: {
        'r1m0': 'Unsure — the cut consumed the marked location, so no mark remains to verify against. The cut face itself is visible and square, but "at the marked location" is unverifiable from this frame.',
        'n2m3': 'Fail — the tube section at the cut is round, not flattened to an oval. The condition is visible and contradicted.'
      }
    }
  });

  var TASKS = [
    mk('AM.I.C.S3', 'Ballast calc', 'Calculate ballast weight shift and required weight location',
      'General', 4, 16, 12, 8, '30B 6-3..6-6', 'searched', ['ballast_shift_1'], false, false,
      [sub('Procedure', 'procedure', 4, 28)]),
    mk('AM.I.C.S5', 'Weight & balance', 'Calculate weight and balance after an equipment change',
      'General', 7, 27, 22, 11, '30B 6-3..6-6', 'searched', ['weight_balance_1'], false, false,
      [sub('Procedure', 'procedure', 7, 49)]),
    ds1,
    mk('AM.I.D.S7', 'Flexible hose', 'Fabricate a flexible hose', 'General', 14, 56, 45, 20,
      '30B 9-16..9-23', 'cited +drafted steps',
      ['determine_the_distance_and_hose', 'cut_the_hose', 'assemble_the_end_fitting', 'proof_test_the_hose_assembly'],
      false, false, [
        sub('Determine distance & hose', 'determine_the_distance_and_hose', 3, 22),
        sub('Cut the hose', 'cut_the_hose', 3, 24),
        sub('Assemble the end fitting', 'assemble_the_end_fitting', 5, 35),
        sub('Proof test the assembly', 'proof_test_the_hose_assembly', 3, 20)]),
    mk('AM.I.D.S8', 'Flareless fitting', 'Fabricate a flareless-fitting-tube connection',
      'General', 11, 44, 37, 14, '30B 9-5..9-8', 'cited +drafted steps',
      ['cut_the_tubing', 'preset_the_sleeve', 'inspect_the_preset_connection'], false, false, [
        sub('Cut the tubing', 'cut_the_tubing', 4, 28),
        sub('Preset the sleeve', 'preset_the_sleeve', 4, 30),
        sub('Inspect the preset connection', 'inspect_the_preset_connection', 3, 23)]),
    mk('AM.I.E.S1', 'Safety wire', 'Install safety wire on nuts, bolts, and turnbuckles',
      'General', 13, 27, 23, 177, '30B 7-77..7-80', 'hand-compiled · assumed',
      ['safety_wire_bolts_1', 'safety_wire_pliers_1', 'safety_wire_pliers_2', 'safety_wire_pliers_3',
       'insert_wire_for_double_wrap_turnbuckle_safety'], true, true, [
        sub('Bolts by hand', 'wire_safety_on_bolts_by_hand', 5, 18),
        sub('Bolts with pliers', 'wire_safety_on_bolts_with_safety_wire_pliers', 4, 16),
        sub('Turnbuckle by hand', 'wire_safety_on_a_turnbuckle_by_hand', 4, 16)]),
    mk('AM.I.I.S1', 'FAA Form 337', 'Complete an FAA Form 337 for a major repair or alteration',
      'General', 10, 41, 31, 14, '30B 2-14..2-17', 'searched', ['form_337_1'], false, false,
      [sub('Procedure', 'procedure', 10, 72)]),
    mk('AM.II.A.S6', 'Patch repair', 'Prepare and install a patch to repair an aircraft or component',
      'Airframe', 32, 136, 105, 37, '31B 4-85..4-96', 'cited',
      ['identify_the_damage', 'remove_the_damage', 'remove_the_rivet', 'flush_patch_1', 'flush_patch_2',
       'rivet_layout', 'drill_the_holes', 'set_up_rivet_gun', 'rivet_the_material'], false, false, [
        sub('Identify the damage', 'identify_the_damage', 3, 24),
        sub('Remove the damage', 'remove_the_damage', 4, 28),
        sub('Remove the rivet', 'remove_the_rivet', 3, 25),
        sub('Create the patch doubler', 'create_the_patch_doubler', 4, 30),
        sub('Create the patch filler', 'create_the_patch_filler', 3, 24),
        sub('Rivet layout', 'rivet_layout', 4, 29),
        sub('Drill the holes', 'drill_the_holes', 4, 27),
        sub('Set up rivet gun', 'set_up_rivet_gun', 3, 26),
        sub('Rivet the material', 'rivet_the_material', 4, 28)]),
    mk('AM.II.K.S3', 'Elec connector', 'Assemble an aircraft electrical connector',
      'Airframe', 19, 27, 26, 131, '31B 9-92..9-94', 'hand-compiled · cited',
      ['elect_conn_2', 'elect_conn_3', 'elect_conn_4', 'elect_conn_5'], true, true, [
        sub('Prepare the wire', 'prepare_the_wire', 4, 11),
        sub('Set up the DNC crimper', 'set_up_the_dnc_crimper', 4, 10),
        sub('Crimp the wire', 'crimp_the_wire', 4, 11),
        sub('Insert the pin', 'insert_the_pin_into_the_electrical_connector', 4, 11),
        sub('Test the connector', 'test_the_connector', 3, 10)]),
    mk('AM.III.F.S11', 'Wire lacing', 'Replace a wire bundle lacing', 'Powerplant', 15, 62, 51, 19,
      '31B 9-86..9-89', 'cited', ['wire_lacing_1', 'wire_lacing_2', 'wire_lacing_3'], false, false, [
        sub('Determine lacing distance', 'determine_the_distance_of_lacing', 3, 26),
        sub('Tie a starting knot', 'tie_a_starting_knot_using_the_single_cord_lacing_method', 4, 30),
        sub('Tie hitches along the bundle', 'tie_a_hitch_along_the_bundle', 4, 29),
        sub('Tie a finishing knot', 'tie_a_finishing_knot_on_a_bundle', 4, 28)]),
    mk('AM.III.M.S5', 'Propeller repair', 'Perform a minor repair to a metal propeller blade',
      'Powerplant', 9, 38, 32, 13, '32B 10-44..10-47', 'searched', [], false, false, [
        sub('Prepare the damaged area', 'prepare_the_damaged_area', 3, 24),
        sub('Blend the damage out', 'blend_the_damage_out', 3, 24),
        sub('Clean the repair', 'clean_the_repair', 3, 22)])
  ];

  var EVAL_ROWS = [
    ['AM.I.C.S3', 'Ballast calc', '32', '81%', '6%', '75 pts', '26', '92%', '2'],
    ['AM.I.C.S5', 'Weight & balance', '28', '61%', '0%', '61 pts', '17', '100%', '0'],
    ['AM.III.F.S11', 'Wire lacing', '96', '36%', '8%', '28 pts', '35', '86%', '2'],
    ['AM.I.D.S1', 'Rigid line', '248', '44%', '16%', '27 pts', '108', '69%', '10'],
    ['AM.II.A.S6', 'Patch repair', '258', '28%', '7%', '21 pts', '64', '66%', '6'],
    ['AM.I.D.S7', 'Flexible hose', '148', '24%', '5%', '19 pts', '35', '63%', '0'],
    ['AM.I.I.S1', 'FAA Form 337', '32', '19%', '0%', '19 pts', '6', '100%', '0'],
    ['AM.I.D.S8', 'Flareless fitting', '116', '22%', '4%', '18 pts', '26', '73%', '2'],
    ['AM.II.K.S3', 'Elec connector', '175', '19%', '11%', '8 pts', '33', '73%', '4'],
    ['AM.I.E.S1', 'Safety wire', '84', '35%', '32%', '2 pts', '29', '66%', '7']
  ];

  var MODEL_ROWS = [
    ['Opus 5', '26%', '6%', '20 pts', '76', '74%', '2', true],
    ['Gemini 3.1 Pro', '35%', '11%', '24 pts', '105', '78%', '6', false],
    ['Gemini 3.6 Flash', '33%', '11%', '22 pts', '98', '79%', '8', false],
    ['GPT-5.6 Sol', '34%', '14%', '19 pts', '100', '63%', '17', false]
  ];

  var MODELS = ['Opus 5', 'Gemini 3.1 Pro', 'Gem 3.6 Flash', 'GPT-5.6 Sol'];

  var DEFAULT_POINTS = [
    { n: '1.', text: 'Every fitting, fastener and termination the sheet calls for is in place on the finished work.' },
    { n: '2.', text: 'Surfaces worked in this subtask are clean and free of burrs, swarf and tool damage.' },
    { n: '3.', text: 'Identification markings on the installed parts face outward, readable without disturbing the work.' },
    { n: 'D1.', text: 'graded as absence: work left deformed, damaged or incorrectly seated at this stage.' }
  ];

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

  function findTask(code) {
    for (var i = 0; i < TASKS.length; i++) if (TASKS[i].code === code) return TASKS[i];
    return null;
  }

  function openTask(code, tab) {
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
    if (x === '✓') return v + ' ✓';
    if (v === 'unsure') return x.indexOf('not_pass') === 0 ? 'unsure ' + x : 'unsure · ' + x;
    return state.showConfidence ? v + ' · ' + x : v;
  }

  // Points a subtask's sheet is graded on — its own where compiled, else the generic set.
  function pointsOf(st) { return st.sheetPoints || DEFAULT_POINTS; }

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
      frameProv: st.frameProv || (task.segmented ? 'frame_reviewed' : task.clips ? 'frame_suggested' : 'no frame'),
      frameFile: st.frameFile || (task.clips ? st.ts + '.jpg' : '— no source video —'),
      frameShort: st.frameShort || (task.clips ? 'last frame of ' + st.sheet : 'take one from the picker'),
      frameNote: st.frameNote || (task.segmented
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

  function renderHeader() {
    $navTasks.setAttribute('aria-selected', String(state.nav !== 'evals'));
    $navEvals.setAttribute('aria-selected', String(state.nav === 'evals'));
  }

  var SUBJECT_ORDER = ['General', 'Airframe', 'Powerplant'];

  function renderSidebar() {
    clear($sidebar);
    var task = findTask(state.taskCode);
    var onTask = state.nav === 'task' && !!task;

    SUBJECT_ORDER.forEach(function (name) {
      var inGroup = TASKS.filter(function (t) { return t.subject === name; });
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
    var cards = TASKS.map(function (t) {
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
            el('span', { class: 'stat-val', text: String(t.targets) })
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
                  task.def + ' defect · ' + task.targets + ' photo targets'
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
          crosshair(14), el('span', { class: 'rail-ts', text: s.ts })
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
        el('div', { class: 'sheet' }, st.points.map(function (p) {
          return el('span', {}, [el('b', { text: p.n }), ' ' + p.text]);
        }))
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
          el('span', { class: 'sheet-hint', text: 'Each numbered point becomes its own model call. Defects graded as absences.' })
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
            el('span', { class: 'empty-title', text: 'No saved run for this target' }),
            el('span', {
              class: 'empty-body',
              text: 'The compiled criterion is ready (' + st.points.length + ' points). Run grades each point independently across 4 models, alongside the perturbed sheet. Results save to build/photo_eval/' + task.code + '/.'
            }),
            el('span', { class: 'plate-note', text: runCost })
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
      ].concat(MODELS.map(function (m) {
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

    out.push(el('div', { class: 'rollup' }, [
      el('div', { class: 'rollup-label', text: 'Subtask roll-up — one fail fails · unsure → review' })
    ].concat(run.rollup.map(function (c) {
      return el('div', { class: 'rollup-cell' }, [
        el('span', { class: cellCls(c[0]), style: 'font-size:9px;font-weight:600', text: c[1] })
      ]);
    }))));

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
        el('span', { class: 'reply-model', text: MODELS[+m[3]] }),
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
    var frameCount = 16;
    var stepsIn = task.subtasks[Math.min(ci, task.subtasks.length - 1)].stepsCount;
    var dur = 40 + ci * 7;
    var fi = state.frameIdx == null ? frameCount - 1 : state.frameIdx;

    // Frame filenames encode their source timestamp, so a frame is citable without a lookup.
    function tsOf(k) {
      var t = dur * k / (frameCount - 1);
      return 't' + String(Math.floor(t)).padStart(6, '0') + '_' +
             String((Math.round(t * 4) % 4) * 25).padStart(2, '0');
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
        el('span', { class: 'clip-meta', text: frameCount + ' frames · 4 fps @ 960px' })
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
        el('span', { class: 'plate-file', text: tsOf(fi) + '.jpg' }),
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
        }, [el('span', { class: 'frame-ts', text: tsOf(i) })]);
      }))
    ]);

    return el('div', { class: 'videos' }, [clipList, pane]);
  }

  /* tab · documentation */

  function docsFor(task) {
    var searched = task.hbProv.indexOf('searched') >= 0;
    var uncited = task.hbProv.indexOf('assumed') >= 0;
    var vol = task.handbook.split(' ')[0];
    var pages = task.handbook.split(' ')[1];
    var hbFile = 'faa_h_' + vol.toLowerCase()
      .replace('30b', '8083_30b').replace('31b', '8083_31b').replace('32b', '8083_32b') +
      '_' + pages.replace(/\.\./g, '_').replace(/-/g, '_') + '.md';
    var secNames = task.subtasks.map(function (s) { return s.label; });
    var HEAD = 'col-label';

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
      { name: 'Procedure sheet', meta: 'normalized · ' + secNames.length + ' sections',
        title: 'AIM skill sheet — ' + task.title, path: 'tasks/' + task.code + '/procedure.md',
        prov: 'confidential · AIM Fremont', provCls: 'tag tag-outline', warn: false, isProse: true,
        blocks: [
          { head: 'Sections', headStyle: HEAD, lines: secNames.map(function (n, i) {
            return { n: String(i + 1).padStart(2, '0'), text: n + ' — Step Instructions and a matching Senior Mechanic Notes list' };
          }) },
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
        rows: [
          { a: 'procedure.docx', b: '9f3c1a…e07b', c: 'AIM skill sheet — verbatim step text' },
          { a: 'steps.json', b: '41b8d0…2c9a', c: 'Sections, steps, note references' },
          { a: 'tasks.csv', b: '7e02af…55d1', c: 'Workbook row — title, subject, photo fit, week/day' },
          { a: hbFile, b: 'c5da93…18f4', c: 'Handbook extract with provenance sidecar' }
        ] },
      { name: 'Assumptions & questions', meta: 'open items',
        title: 'Assumptions and open questions', path: 'tasks/' + task.code + '/pack.yaml',
        prov: 'linted both ways', provCls: 'tag tag-outline', warn: false, isProse: true,
        blocks: [
          { head: 'Assumptions', headStyle: HEAD, lines: (searched || uncited
              ? [{ n: 'a1', text: 'Handbook pages were located rather than cited; standards drawn from them are provisional. resolve_by: AIM confirms the governing reference.' }]
              : [{ n: 'a1', text: 'Every inferred item is flagged assumed: true with a reason and a resolve_by. The linter fails a pack whose assumption has no matching flag, and vice versa.' }])
            .concat(task.hbProv.indexOf('drafted steps') >= 0
              ? [{ n: 'a2', text: 'The skill sheet stops before the work its own title describes, yet lists equipment no documented step touches. The missing operations are drafted in steps_supplement.json from the pages the sheet itself cites and marked origin: drafted. These are proposals about scope, not campus standards.' }]
              : []) },
          { head: 'Open questions for AIM', headStyle: HEAD, lines: [
            { n: 'q1', text: 'Which framing does a submitted photo need — close, unobstructed, oblique where flushness matters, with a scale reference where a dimension matters? evidence.required is where that belongs.' },
            { n: 'q2', text: 'Measurement checks (pull tests, continuity, torque) are acceptance criteria the campus states but a photograph cannot settle. What instrumented or witnessed evidence accompanies the photos?' }] }
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

  function renderEvals() {
    var modelTable = el('div', { class: 'table-box' }, [
      el('div', { class: 'model-head' }, ['Model', 'Criteria', 'Perturbed', 'Drop', 'Decisive', 'Flipped', 'Accepted ⚠']
        .map(function (h) { return el('span', { text: h }); }))
    ].concat(MODEL_ROWS.map(function (m) {
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
    ].concat(EVAL_ROWS.map(function (r) {
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
        ['All tasks', '1,217', '32%', '10%', '21 pts', '379', '73%', '33']
          .map(function (v) { return el('span', { text: v }); }))
    ]));

    return el('div', { class: 'screen' }, [
      el('div', { class: 'screen-head' }, [
        el('h1', { class: 'screen-title', text: 'Evals — criteria vs. their perturbations' }),
        el('span', {
          class: 'screen-sub',
          text: 'every subtask sheet, graded against its own perturbed sheet on the same frames · 1,217 points ×2 · 4 models · $34.68'
        })
      ]),
      el('div', { class: 'blueprint notice' }, [
        corners(), svg(WARN_ICON),
        el('span', { class: 'notice-text' }, [
          el('b', { text: 'One-class data: every reference frame is work an instructor accepted.' }),
          ' 0 of 11 tasks have labeled negatives, so precision and defect recall cannot be computed — the perturbation controls below are the floor that separates a grader from a model that passes everything.'
        ])
      ]),
      el('div', { class: 'evals-cols' }, [
        el('div', { class: 'evals-models' }, [
          el('h2', { class: 'sec-title', text: 'Which grader to trust' }),
          modelTable,
          el('span', { class: 'note' }, [
            el('b', { text: 'Accepted' }),
            ' = passed a criterion on work that contradicts it, where the same model had shown it can see the condition. Raw pass rates look interchangeable; this column is what separates them. Opus 5: 2 in 76 decisive pairs. GPT-5.6 Sol: 17 in 100.'
          ])
        ]),
        el('div', { class: 'evals-ready' }, [
          el('h2', { class: 'sec-title', text: 'Dataset readiness' }),
          el('div', { class: 'blueprint', style: 'padding:12px 14px;display:flex;flex-direction:column;gap:8px' }, [
            corners(),
            el('div', { class: 'kv' }, [el('span', { text: 'Labeled datasets (evals/datasets/)' }), el('b', { text: '0' })]),
            el('div', { class: 'kv' }, [el('span', { text: 'Agent runs (build/evals/runs/)' }), el('b', { text: '0' })]),
            el('div', { class: 'kv' }, [el('span', { text: 'Photo-eval runs (build/photo_eval/)' }), el('b', { text: '11 tasks' })]),
            el('div', { class: 'kv' }, [
              el('span', { text: 'Atoms with labeled negatives' }), tag('tag tag-neutral', '0 of 1,175')
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
          text: 'The drop measures the photograph as much as the grader — it orders tasks by how gradeable their evidence is. Top rows are worksheet photos; the bottom two (AM.II.K.S3, AM.I.E.S1) take frames from mid-action head-mounted footage. AM.III.M.S5 has criteria and no frames, permanently. Click a row to open the task.'
        })
      ])
    ]);
  }

  /* ── routing ───────────────────────────────────────────────────────────── */

  function syncHash() {
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

    var task = findTask(state.taskCode);
    if (state.nav === 'evals') append($main, renderEvals());
    else if (state.nav === 'task' && task) append($main, renderTask(task));
    else append($main, renderHome());

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
    render();
  });

  applyRoute();
  render();
})();
