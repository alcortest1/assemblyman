/* Task Pack Inspector — browse compiled packs, reference videos, sampled frames
   and the pass-1 sub-subtask segmentation for every task in one place.

   No build step and no in-browser transform: markup is htm tagged templates
   bound to React.createElement, so this file is plain JavaScript. */

const { useState, useEffect, useMemo, useRef, useCallback } = React;
const html = htm.bind(React.createElement);
const F = React.Fragment;

/* ------------------------------------------------------------------ utils */

const api = (path) => fetch(path).then((r) => (r.ok ? r.json() : null));

const fmtTime = (s) =>
  `${Math.floor(s / 60)}:${(s % 60).toFixed(2).padStart(5, "0")}`;

const fmtBytes = (n) =>
  n > 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.round(n / 1e6)} MB`;

const fmtMetric = (value) =>
  value === null || value === undefined ? "—" : `${(value * 100).toFixed(1)}%`;

// Timestamp encoded in the filename: `t000012_25.jpg` -> 12.25
const frameTime = (name) => parseFloat(name.slice(1, -4).replace("_", "."));

// Stable colour per step, so a step keeps its identity across the timeline,
// the segment list and the legend.
const STEP_COLORS = [
  "#6aa6ff", "#58c98a", "#e6b455", "#c98ae0", "#5fd0d0",
  "#e6685f", "#8f9bb3", "#d08a5f", "#7fbf5f",
];
const stepColor = (id) =>
  id === null || id === undefined
    ? "#4a5263" // footage belonging to no official step
    : STEP_COLORS[(Number(id) - 1 + STEP_COLORS.length) % STEP_COLORS.length];

const severityClass = (s) =>
  s === "critical" || s === "major" || s === "minor" ? s : "";

const confidenceClass = (c) =>
  c === "high" ? "good" : c === "low" ? "bad" : "warn";

/* Minimal markdown renderer — headings, lists, tables, quotes, code, rules.
   Enough for the pack docs; deliberately not a full implementation. */
function renderMarkdown(src) {
  const inline = (text) =>
    text
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1");

  const escape = (text) =>
    text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  const out = [];
  let list = null;
  let quote = [];
  let table = null;
  // Handbook extracts are page text inside ```text fences. Without fence
  // handling every bullet and figure callout in them gets re-interpreted as
  // markdown, which turns a verbatim FAA page into scrambled prose — and the
  // whole point of extracting it verbatim is that it is quotable.
  let fence = null;

  const flushList = () => {
    if (list) { out.push(`<${list.tag}>${list.items.join("")}</${list.tag}>`); list = null; }
  };
  const flushQuote = () => {
    if (quote.length) { out.push(`<blockquote>${quote.map(inline).join("<br/>")}</blockquote>`); quote = []; }
  };
  const flushTable = () => {
    if (!table) return;
    const [head, ...body] = table;
    out.push(
      `<table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead>` +
      `<tbody>${body.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`
    );
    table = null;
  };

  for (const raw of src.split("\n")) {
    const line = raw.trimEnd();

    if (/^\s*```/.test(line)) {
      if (fence === null) {
        flushList(); flushQuote(); flushTable();
        fence = [];
      } else {
        out.push(`<pre class="fence"><code>${escape(fence.join("\n"))}</code></pre>`);
        fence = null;
      }
      continue;
    }
    if (fence !== null) { fence.push(raw); continue; }

    if (/^\|(.+)\|$/.test(line)) {
      const cells = line.slice(1, -1).split("|").map((c) => c.trim());
      if (cells.every((c) => /^:?-{2,}:?$/.test(c))) continue; // separator row
      flushList(); flushQuote();
      if (!table) table = [];
      table.push(cells);
      continue;
    }
    flushTable();

    if (/^>\s?/.test(line)) { flushList(); quote.push(line.replace(/^>\s?/, "")); continue; }
    flushQuote();

    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      flushList();
      out.push(`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`);
      continue;
    }
    if (/^(-{3,}|\*{3,})$/.test(line)) { flushList(); out.push("<hr/>"); continue; }

    const ul = line.match(/^\s*[-*]\s+(.*)$/);
    const ol = line.match(/^\s*\d+\.\s+(.*)$/);
    if (ul || ol) {
      const tag = ul ? "ul" : "ol";
      if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
      list.items.push(`<li>${inline((ul || ol)[1])}</li>`);
      continue;
    }
    flushList();

    if (!line.trim()) continue;
    out.push(`<p>${inline(line)}</p>`);
  }
  flushList(); flushQuote(); flushTable();
  // An unterminated fence still has content worth showing.
  if (fence !== null && fence.length) {
    out.push(`<pre class="fence"><code>${escape(fence.join("\n"))}</code></pre>`);
  }
  return out.join("\n");
}

const Markdown = ({ text }) =>
  html`<div class="md" dangerouslySetInnerHTML=${{ __html: renderMarkdown(text || "") }} />`;

const Pill = ({ kind, children }) =>
  html`<span class=${"pill" + (kind ? " " + kind : "")}>${children}</span>`;

/* --------------------------------------------------------------- sidebar */

function Sidebar({ tasks, selected, onSelect }) {
  return html`
    <aside class="sidebar">
      <h1>Tasks · ${tasks.length}</h1>
      ${tasks.map((t) => html`
        <button
          key=${t.acs_code}
          class=${"task-item" + (selected === t.acs_code ? " active" : "")}
          onClick=${() => onSelect(t.acs_code)}
        >
          <span class="code">${t.acs_code}</span>
          <span class="title">${t.title}</span>
          <span class="meta">
            <${Pill} kind=${t.has_pack ? (t.pack_status === "reviewed" ? "good" : "warn") : ""}>
              ${t.has_pack ? `pack: ${t.pack_status}` : "no pack"}
            <//>
            ${t.video_count > 0 && html`<${Pill}>${t.video_count} video<//>`}
            ${t.frame_count > 0 && html`<${Pill}>${t.frame_count} frames<//>`}
            ${t.segmented_videos > 0 && html`<${Pill} kind="good">${t.segmented_videos} segmented<//>`}
          </span>
        </button>
      `)}
    </aside>
  `;
}

/* ------------------------------------------------------------ pack viewer */

function CheckList({ checks }) {
  if (!checks || !checks.length) return null;
  return checks.map((c, i) => html`
    <div key=${c.id || i} class=${"check " + severityClass(c.severity)}>
      <div>${c.statement || c.claim}</div>
      <div class="id">
        ${c.id}
        ${c.observable ? ` · observable: ${c.observable}` : ""}
        ${c.verifiability ? ` · ${c.verifiability}` : ""}
        ${c.confidence_ceiling ? ` · ceiling: ${c.confidence_ceiling}` : ""}
        ${c.severity ? ` · ${c.severity}` : ""}
      </div>
      ${c.note && html`<div class="note">${c.note}</div>`}
    </div>
  `);
}

function PackView({ pack, packText, packError }) {
  if (!pack) {
    if (!packText) return html`<div class="empty">No compiled pack for this task yet.</div>`;
    return html`
      <${F}>
        ${packError && html`<div class="card"><strong>YAML did not parse:</strong> ${packError}</div>`}
        <pre class="raw">${packText}</pre>
      <//>
    `;
  }

  const variants = Array.isArray(pack.variants) ? pack.variants : [];
  const steps = Array.isArray(pack.steps) ? pack.steps : [];
  // A machine-drafted pack and a hand-compiled one both read `status: draft`,
  // so status alone cannot tell them apart. `provenance` is what does, and a
  // reader has to see it before they read a single check as an AIM standard.
  const provenance = pack.provenance;
  const byVariant = new Map();
  for (const s of steps) {
    const key = s.variant || "—";
    if (!byVariant.has(key)) byVariant.set(key, []);
    byVariant.get(key).push(s);
  }
  // Steps that name no variant, or a variant the pack never declared.
  const declared = new Set(variants.map((v) => v.id));
  const orphans = steps.filter((s) => !declared.has(s.variant));

  const stepBlock = (s) => html`
    <div key=${s.id} style=${{ marginTop: 14 }}>
      <div><strong>${s.text || s.instruction}</strong> <${Pill}>${s.id}<//></div>
      <${CheckList} checks=${s.checks} />
      ${s.error_modes && s.error_modes.length > 0 && html`
        <${F}>
          <h4>error modes</h4>
          ${s.error_modes.map((e) => html`
            <div key=${e.id} class=${"check " + severityClass(e.severity)}>
              <div>${e.statement}</div>
              <div class="id">${e.id} · ${e.severity}</div>
            </div>
          `)}
        <//>
      `}
    </div>
  `;

  return html`
    <${F}>
      ${provenance && html`
        <div class="card drafted-banner">
          <h3>Drafted, not reviewed</h3>
          <p>
            Every check, error mode and evidence item in this pack was proposed by
            <code>${provenance.model || "a model"}</code> via <code>${provenance.generator}</code>
            on ${(provenance.drafted_at || "").slice(0, 10)}, from the procedure sheet and
            the FAA handbook. <strong>No subject-matter expert has seen it.</strong>
            Treat a passing grade against these criteria as a test of the pipeline, not
            as an assessment of a student.
          </p>
          ${provenance.sources && html`
            <p class="muted">Compiled from ${provenance.sources.join(", ")}.</p>`}
        </div>
      `}
      <div class="card">
        <h3>${pack.title || pack.acs_code}</h3>
        <dl class="kv">
          <dt>status</dt>
          <dd><${Pill} kind=${pack.status === "reviewed" ? "good" : "warn"}>${pack.status}<//></dd>
          <dt>acs_code</dt><dd>${pack.acs_code}</dd>
          ${pack.subject && html`<${F}><dt>subject</dt><dd>${pack.subject} · block ${pack.block}</dd><//>`}
          ${pack.campus && html`<${F}><dt>campus</dt><dd>${pack.campus}</dd><//>`}
          ${pack.photo_assessment && html`
            <${F}>
              <dt>photo fit</dt>
              <dd>${pack.photo_assessment.fit} — ${pack.photo_assessment.rationale}</dd>
            <//>
          `}
        </dl>
      </div>

      ${variants.map((v) => html`
        <div class="card" key=${v.id}>
          <h3>${v.label} <${Pill}>${v.id}<//></h3>
          ${(byVariant.get(v.id) || []).map(stepBlock)}
          ${!(byVariant.get(v.id) || []).length &&
            html`<div class="note">No steps recorded for this variant.</div>`}
        </div>
      `)}

      ${orphans.length > 0 && html`
        <div class="card">
          <h3>Steps outside a declared variant</h3>
          ${orphans.map(stepBlock)}
        </div>
      `}

      ${pack.evidence && pack.evidence.required && html`
        <div class="card">
          <h3>Required evidence</h3>
          ${pack.evidence.required.map((e) => html`
            <div class="check" key=${e.id}>
              <div>${e.description}</div>
              <div class="id">${e.id} · ${e.medium}${e.assumed ? " · assumed" : ""}</div>
              ${e.framing && html`<div class="note">Framing: ${e.framing}</div>`}
            </div>
          `)}
        </div>
      `}

      ${pack.assumptions && html`
        <div class="card">
          <h3>Assumptions to resolve before review</h3>
          ${pack.assumptions.map((a) => html`
            <div class="check major" key=${a.id}>
              <div>${a.statement}</div>
              <div class="note">${a.reason}</div>
              <div class="id">${a.id} · resolve by: ${a.resolve_by}</div>
            </div>
          `)}
        </div>
      `}

      ${pack.open_questions && html`
        <div class="card">
          <h3>Open questions</h3>
          <ul>${pack.open_questions.map((q, i) => html`<li key=${i}>${q}</li>`)}</ul>
        </div>
      `}
    <//>
  `;
}

/* ------------------------------------------------------------ atom viewer */

const ATOM_KINDS = [
  ["all", "All atoms"],
  ["activity", "Activities"],
  ["correctness", "Correctness"],
  ["defect", "Defects"],
];

const atomKindTone = (kind) =>
  kind === "activity" ? "activity" : kind === "correctness" ? "good" : "bad";

function AtomCard({ atom }) {
  const examples = atom.examples || [];
  const samples = atom.evaluation_samples || { correct: 0, incorrect: 0, total: 0 };
  const evaluationReady = samples.correct > 0 && samples.incorrect > 0;
  return html`
    <article class=${"atom-card " + atom.kind + " " + severityClass(atom.severity)}>
      <div class="atom-head">
        <${Pill} kind=${atomKindTone(atom.kind)}>${atom.kind}<//>
        ${atom.source_id && html`<span class="atom-source-id">${atom.source_id}</span>`}
        ${atom.severity && html`<${Pill} kind=${atom.severity === "critical" ? "bad" : "warn"}>
          ${atom.severity}
        <//>`}
      </div>
      <div class="atom-label">${atom.label}</div>
      ${atom.description && atom.description !== atom.label &&
        html`<p class="atom-description">${atom.description}</p>`}
      <div class="atom-meta">
        ${atom.observable && html`<span>observable: ${atom.observable}</span>`}
        ${atom.verifiability && html`<span>verifiability: ${atom.verifiability}</span>`}
        ${atom.confidence_ceiling && html`<span>ceiling: ${atom.confidence_ceiling}</span>`}
        <span>source: ${atom.source}</span>
      </div>
      ${atom.criterion && html`
        <details class="atom-criterion">
          <summary>Photo criterion drafted for this subtask</summary>
          <pre class="draft-text">${atom.criterion}</pre>
          ${(atom.criterion_sources || []).length > 0 && html`
            <ul class="atom-sources">
              ${atom.criterion_sources.map((s, i) => html`
                <li key=${i}>
                  <${Pill} kind=${s.assumed ? "warn" : "accent"}>${s.source || "unattributed"}<//>
                  ${s.note}
                </li>
              `)}
            </ul>
          `}
          ${atom.required_framing && html`
            <p class="draft-aside"><strong>Required framing:</strong> ${atom.required_framing}</p>`}
        </details>
      `}
      <div class="atom-id">${atom.id}</div>
      <div class="atom-sample-readiness">
        <span><strong>${samples.correct}</strong> labeled correct</span>
        <span class=${samples.incorrect > 0 ? "" : "missing"}>
          <strong>${samples.incorrect}</strong> labeled incorrect
        </span>
        ${examples.length > 0 && html`
          <span><strong>${examples.length}</strong> reference interval${examples.length === 1 ? "" : "s"}</span>
        `}
        <${Pill} kind=${evaluationReady ? "good" : "warn"}>
          ${evaluationReady ? "metric-ready" : "needs both classes"}
        <//>
      </div>

      ${examples.length > 0 && html`
        <details class="atom-examples">
          <summary>
            ${examples.length} reviewed source interval${examples.length === 1 ? "" : "s"}
          </summary>
          <div class="atom-example-list">
            ${examples.map((example, i) => html`
              <div class="atom-example" key=${`${example.video}:${example.seq}:${i}`}>
                <div>
                  <strong>${example.video}</strong>
                  <span class="atom-time">
                    ${fmtTime(example.t_start || 0)}–${fmtTime(example.t_end || 0)}
                  </span>
                  ${example.confidence &&
                    html`<${Pill} kind=${confidenceClass(example.confidence)}>${example.confidence}<//>`}
                </div>
                <div class="atom-frames">
                  ${example.frame_start || "—"} → ${example.frame_end || "—"}
                  ${example.frame_count ? ` · ${example.frame_count} frames` : ""}
                </div>
                ${example.description && html`<p>${example.description}</p>`}
              </div>
            `)}
          </div>
        </details>
      `}
    </article>
  `;
}

function MetricCells({ metrics }) {
  if (!metrics) return html`<td colspan="6">—</td>`;
  return html`
    <${F}>
      <td>${metrics.support}</td>
      <td>${fmtMetric(metrics.precision)}</td>
      <td>${fmtMetric(metrics.recall)}</td>
      <td>${fmtMetric(metrics.defect_recall)}</td>
      <td>${fmtMetric(metrics.coverage)}</td>
      <td>${metrics.fp}</td>
    <//>
  `;
}

function EvaluationsTab({ evaluations, catalog }) {
  const runs = (evaluations && evaluations.runs) || [];
  const datasets = (evaluations && evaluations.datasets) || [];
  const [selectedRunId, setSelectedRunId] = useState(runs.length ? runs[0].run_id : null);

  useEffect(() => {
    if (!runs.some((run) => run.run_id === selectedRunId)) {
      setSelectedRunId(runs.length ? runs[0].run_id : null);
    }
  }, [runs.length, selectedRunId]);

  const selectedRun = runs.find((run) => run.run_id === selectedRunId);
  const atomById = new Map(((catalog && catalog.atoms) || []).map((atom) => [atom.id, atom]));
  const readiness = ((catalog && catalog.atoms) || []).map((atom) => {
    const samples = atom.evaluation_samples || { correct: 0, incorrect: 0, total: 0 };
    return {
      atom,
      samples,
      references: (atom.examples || []).length,
      ready: samples.correct > 0 && samples.incorrect > 0,
    };
  });
  const readyCount = readiness.filter((row) => row.ready).length;

  return html`
    <${F}>
      <section class="eval-hero">
        <div>
          <div class="eyebrow">Evaluation workbench</div>
          <h3>Compare agent systems at atom and task level</h3>
          <p>
            Precision measures trustworthiness of automatic passes; recall measures
            how much correct work is automatically passed. Defect recall is shown
            separately because catching incorrect work is a safety-critical goal.
          </p>
        </div>
        <div class="eval-readiness-ring">
          <strong>${readyCount}/${readiness.length}</strong>
          <span>atoms have both<br/>correct + incorrect labels</span>
        </div>
      </section>

      <div class="eval-grid">
        <section class="card">
          <h3>Labeled datasets</h3>
          ${datasets.length ? datasets.map((dataset) => html`
            <div class="dataset-row" key=${dataset.dataset_id}>
              <div>
                <strong>${dataset.title}</strong>
                <div class="id">${dataset.dataset_id}${dataset.split ? ` · ${dataset.split}` : ""}</div>
              </div>
              <div class="dataset-counts">
                <span>${dataset.sample_count} task samples</span>
                <span>${dataset.correct_tasks} correct</span>
                <span>${dataset.incorrect_tasks} incorrect</span>
                <span>${dataset.atom_label_count} atom labels</span>
              </div>
            </div>
          `) : html`
            <div class="eval-empty">
              No labeled evaluation dataset is registered for this task yet.
              Correct reference demonstrations alone cannot produce precision or
              defect-recall measurements.
            </div>
          `}
        </section>

        <section class="card">
          <h3>Metric definitions</h3>
          <dl class="metric-defs">
            <dt>Precision</dt><dd>Of automatic passes, how many are truly correct?</dd>
            <dt>Recall</dt><dd>Of truly correct samples, how many are automatically passed?</dd>
            <dt>Defect recall</dt><dd>Of truly incorrect samples, how many are automatically failed?</dd>
            <dt>Coverage</dt><dd>How many samples receive pass/fail rather than review or insufficient evidence?</dd>
          </dl>
        </section>
      </div>

      <section class="card">
        <div class="section-heading">
          <div>
            <div class="eyebrow">Task-level comparison</div>
            <h3>Agent systems</h3>
          </div>
          <${Pill}>${runs.length} runs<//>
        </div>
        ${runs.length ? html`
          <div class="scroll-x">
            <table class="metrics-table">
              <thead>
                <tr>
                  <th>System / run</th>
                  <th>Dataset</th>
                  <th>Support</th>
                  <th>Precision</th>
                  <th>Recall</th>
                  <th>Defect recall</th>
                  <th>Coverage</th>
                  <th>False passes</th>
                </tr>
              </thead>
              <tbody>
                ${runs.map((run) => html`
                  <tr key=${run.run_id} class=${selectedRunId === run.run_id ? "selected" : ""}
                      onClick=${() => setSelectedRunId(run.run_id)}>
                    <td>
                      <strong>${run.system.name || run.system.id || "Unnamed system"}</strong>
                      <div class="id">${run.run_id}${run.system.version ? ` · ${run.system.version}` : ""}</div>
                    </td>
                    <td>${run.dataset_id || "—"}</td>
                    ${run.status === "complete"
                      ? html`<${MetricCells} metrics=${run.task_metrics} />`
                      : html`<td colspan="6"><${Pill} kind="bad">${run.status}<//></td>`}
                  </tr>
                `)}
              </tbody>
            </table>
          </div>
        ` : html`
          <div class="eval-empty">
            No agent-system runs exist yet. Run files can represent a direct VLM
            baseline, a temporal VLM system, a hybrid evidence system, or any
            other configuration as long as they use the common prediction schema.
          </div>
        `}
      </section>

      ${selectedRun && selectedRun.status === "complete" && html`
        <section class="card">
          <div class="section-heading">
            <div>
              <div class="eyebrow">Atom-level metrics</div>
              <h3>${selectedRun.system.name || selectedRun.system.id || selectedRun.run_id}</h3>
            </div>
            <${Pill}>${selectedRun.atom_metrics.length} evaluated atoms<//>
          </div>
          <div class="scroll-x">
            <table class="metrics-table atom-metrics-table">
              <thead>
                <tr>
                  <th>Atom</th>
                  <th>Support</th>
                  <th>Precision</th>
                  <th>Recall</th>
                  <th>Defect recall</th>
                  <th>Coverage</th>
                  <th>False passes</th>
                </tr>
              </thead>
              <tbody>
                ${selectedRun.atom_metrics.map((metrics) => {
                  const atom = atomById.get(metrics.atom_id);
                  return html`
                    <tr key=${metrics.atom_id}>
                      <td>
                        <strong>${atom ? atom.label : metrics.atom_id}</strong>
                        <div class="id">${metrics.atom_id}</div>
                      </td>
                      <${MetricCells} metrics=${metrics} />
                    </tr>
                  `;
                })}
              </tbody>
            </table>
          </div>
        </section>
      `}

      <section class="card">
        <div class="section-heading">
          <div>
            <div class="eyebrow">Dataset readiness</div>
            <h3>Labels available per atom</h3>
          </div>
          <${Pill} kind=${readyCount === readiness.length && readiness.length ? "good" : "warn"}>
            ${readyCount} metric-ready
          <//>
        </div>
        <div class="scroll-x">
          <table class="metrics-table readiness-table">
            <thead>
              <tr>
                <th>Atom</th>
                <th>Kind</th>
                <th>Correct labels</th>
                <th>Incorrect labels</th>
                <th>Reference intervals</th>
                <th>Readiness</th>
              </tr>
            </thead>
            <tbody>
              ${readiness.map(({ atom, samples, references, ready }) => html`
                <tr key=${atom.id}>
                  <td><strong>${atom.label}</strong><div class="id">${atom.source_id || atom.id}</div></td>
                  <td><${Pill} kind=${atomKindTone(atom.kind)}>${atom.kind}<//></td>
                  <td>${samples.correct}</td>
                  <td class=${samples.incorrect ? "" : "missing-count"}>${samples.incorrect}</td>
                  <td>${references}</td>
                  <td><${Pill} kind=${ready ? "good" : "warn"}>
                    ${ready ? "can compute both classes" : "needs labeled negatives"}
                  <//></td>
                </tr>
              `)}
            </tbody>
          </table>
        </div>
      </section>

      <div class="card eval-path-note">
        <strong>Evaluation inputs</strong>
        <span>Labeled datasets: <code>${evaluations.paths.datasets}</code></span>
        <span>Agent run outputs: <code>${evaluations.paths.runs}</code></span>
      </div>
    <//>
  `;
}

function AtomsTab({ catalog }) {
  const [kind, setKind] = useState("all");
  const [query, setQuery] = useState("");

  if (!catalog || !catalog.atoms || !catalog.atoms.length) {
    return html`
      <div class="empty">
        No atoms can be derived yet. This task needs a compiled pack, reviewed
        segment labels, or both.
      </div>
    `;
  }

  const needle = query.trim().toLowerCase();
  const filtered = catalog.atoms.filter((atom) => {
    if (kind !== "all" && atom.kind !== kind) return false;
    if (!needle) return true;
    return [
      atom.id, atom.source_id, atom.label, atom.description,
      atom.step_id, atom.step_title, atom.variant,
    ].some((value) => String(value || "").toLowerCase().includes(needle));
  });

  const variantById = new Map((catalog.variants || []).map((v) => [v.id, v]));
  const variantIds = [
    ...(catalog.variants || []).map((v) => v.id),
    ...filtered.map((atom) => atom.variant).filter((id) => !variantById.has(id)),
  ].filter((id, index, all) => id && all.indexOf(id) === index);

  const grouped = variantIds.map((variantId) => {
    const variantAtoms = filtered.filter((atom) => atom.variant === variantId);
    const stepKeys = [...new Set(variantAtoms.map((atom) => atom.step_id || "__setup__"))];
    const steps = stepKeys.map((stepKey) => {
      const atoms = variantAtoms.filter(
        (atom) => (atom.step_id || "__setup__") === stepKey
      );
      return {
        id: stepKey,
        title: atoms[0] ? atoms[0].step_title : "Setup / outside official subtasks",
        atoms,
      };
    });
    return {
      id: variantId,
      label: (variantById.get(variantId) || {}).label || variantId,
      steps,
      count: variantAtoms.length,
    };
  }).filter((group) => group.count > 0);

  const counts = catalog.counts || {};
  return html`
    <${F}>
      <section class="atom-overview">
        <div>
          <div class="eyebrow">Derived atomic catalog</div>
          <h3>Task → variant → subtask → atom</h3>
          <p>
            Activities come from reviewed video segments. Correctness and defect
            atoms come from the compiled task pack.
          </p>
        </div>
        <div class="atom-stats">
          <div><strong>${counts.total || 0}</strong><span>all atoms</span></div>
          <div><strong>${counts.activity || 0}</strong><span>activities</span></div>
          <div><strong>${counts.correctness || 0}</strong><span>correctness</span></div>
          <div><strong>${counts.defect || 0}</strong><span>defects</span></div>
        </div>
      </section>

      <div class="atom-toolbar">
        <div class="atom-kind-filter" role="group" aria-label="Filter atom types">
          ${ATOM_KINDS.map(([id, label]) => html`
            <button key=${id} class=${kind === id ? "active" : ""}
                    onClick=${() => setKind(id)}>
              ${label}
            </button>
          `)}
        </div>
        <label class="atom-search">
          <span>Search atoms</span>
          <input
            type="search"
            value=${query}
            placeholder="ID, action, rule, subtask…"
            onInput=${(event) => setQuery(event.target.value)}
          />
        </label>
      </div>

      <div class="atom-result-note">
        Showing ${filtered.length} of ${counts.total || catalog.atoms.length} atoms
        across ${catalog.subtask_count || 0} official subtasks.
      </div>

      ${grouped.map((variant) => html`
        <section class="atom-variant" key=${variant.id}>
          <header>
            <div>
              <div class="eyebrow">Variant</div>
              <h3>${variant.label}</h3>
            </div>
            <${Pill}>${variant.count} atoms<//>
          </header>

          ${variant.steps.map((step) => html`
            <section class="atom-step" key=${`${variant.id}:${step.id}`}>
              <div class="atom-step-head">
                <div>
                  <div class="eyebrow">${step.id === "__setup__" ? "Outside procedure" : "Subtask"}</div>
                  <h4>${step.title}</h4>
                </div>
                ${step.id !== "__setup__" && html`<${Pill}>${step.id}<//>`}
              </div>
              <div class="atom-grid">
                ${step.atoms.map((atom) => html`<${AtomCard} key=${atom.id} atom=${atom} />`)}
              </div>
            </section>
          `)}
        </section>
      `)}

      ${filtered.length === 0 && html`
        <div class="empty">No atoms match the selected type and search text.</div>
      `}

      <div class="card atom-notes">
        <h3>How this catalog is formed</h3>
        <ul>${(catalog.notes || []).map((note, i) => html`<li key=${i}>${note}</li>`)}</ul>
      </div>
    <//>
  `;
}

/* ----------------------------------------------------------- video viewer */

function Lightbox({ src, caption, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return html`
    <div class="lightbox" onClick=${onClose}>
      <img src=${src} alt=${caption} />
      <div class="cap">${caption}</div>
    </div>
  `;
}

function SegmentTimeline({ segments, duration, activeSeq, onPick }) {
  return html`
    <div class="timeline">
      <div class="timeline-bar">
        ${segments.map((s) => html`
          <button
            key=${s.seq}
            class=${"timeline-seg" + (activeSeq === s.seq ? " active" : "")}
            style=${{ width: ((s.t_end - s.t_start) / duration) * 100 + "%", background: stepColor(s.step_id) }}
            title=${`${s.seq}. ${s.substep_label} (${fmtTime(s.t_start)}–${fmtTime(s.t_end)})`}
            onClick=${() => onPick(s)}
          />
        `)}
      </div>
      <div class="timeline-axis">
        <span>0:00</span><span>${fmtTime(duration)}</span>
      </div>
    </div>
  `;
}

// Free-form detail the pass-2 review adds to a segment. Rendered generically so
// the viewer keeps working as the schema grows.
const DETAIL_KEYS = [
  "mechanics", "hand_mechanics", "equipment_mechanics",
  "equipment", "tools", "materials", "hand_motions", "visual_cues",
];

function SegmentDetail({ segment, frames, onOpenFrame }) {
  const list = useMemo(() => {
    if (!frames) return [];
    return frames.frames.filter((name) => {
      const t = frameTime(name);
      return t >= segment.t_start - 0.13 && t <= segment.t_end + 0.13;
    });
  }, [segment, frames]);

  return html`
    <div class="card" style=${{ marginTop: 14 }}>
      <h3>
        ${segment.seq}. ${segment.substep_label}${" "}
        <${Pill}>${fmtTime(segment.t_start)}–${fmtTime(segment.t_end)}<//>${" "}
        <${Pill}>${list.length} frames<//>${" "}
        ${segment.confidence && html`<${Pill} kind=${confidenceClass(segment.confidence)}>${segment.confidence}<//>`}
      </h3>
      ${segment.step_title && html`
        <div class="note">step ${String(segment.step_id)} — ${segment.step_title}</div>
      `}
      <p>${segment.short_description}</p>
      ${segment.boundary_reason && html`
        <${F}><h4>boundary</h4><p class="note">${segment.boundary_reason}</p><//>
      `}

      ${DETAIL_KEYS.filter((k) => segment[k]).map((key) => html`
        <div key=${key}>
          <h4>${key.replace(/_/g, " ")}</h4>
          ${Array.isArray(segment[key])
            ? html`<ul>${segment[key].map((v, i) =>
                html`<li key=${i}>${typeof v === "string" ? v : JSON.stringify(v)}</li>`)}</ul>`
            : typeof segment[key] === "string"
              ? html`<p>${segment[key]}</p>`
              : html`<pre class="raw">${JSON.stringify(segment[key], null, 2)}</pre>`}
        </div>
      `)}

      <h4>frame subsequence</h4>
      <div class="frame-strip">
        ${list.map((name) => html`
          <img
            key=${name}
            src=${`${frames.base}/${name}`}
            alt=${name}
            title=${name}
            loading="lazy"
            onClick=${() => onOpenFrame(`${frames.base}/${name}`, name)}
          />
        `)}
      </div>
    </div>
  `;
}

function VideoTab({ acs, videos }) {
  const [videoName, setVideoName] = useState(videos.length ? videos[0].name : null);
  const [segments, setSegments] = useState(null);
  const [frames, setFrames] = useState(null);
  const [activeSeq, setActiveSeq] = useState(null);
  const [lightbox, setLightbox] = useState(null);
  const videoRef = useRef(null);

  useEffect(() => {
    if (!videoName) return;
    setSegments(null); setFrames(null); setActiveSeq(null);
    api(`/api/segments/${acs}/${videoName}`).then(setSegments);
    api(`/api/frames/${acs}/${videoName}?set=detail`).then(setFrames);
  }, [acs, videoName]);

  const pick = useCallback((s) => {
    setActiveSeq(s.seq);
    if (videoRef.current) videoRef.current.currentTime = s.t_start;
  }, []);

  const openFrame = useCallback(
    (src, name) => setLightbox({ src, caption: `${videoName} · ${name}` }),
    [videoName]
  );

  if (!videos.length) return html`<div class="empty">No reference videos for this task.</div>`;

  const video = videos.find((v) => v.name === videoName);
  const active = segments && segments.segments.find((s) => s.seq === activeSeq);
  const stepIds = segments ? [...new Set(segments.segments.map((s) => s.step_id))] : [];

  return html`
    <${F}>
      <div class="video-picker">
        ${videos.map((v) => html`
          <button key=${v.name} class=${v.name === videoName ? "active" : ""}
                  onClick=${() => setVideoName(v.name)}>
            ${v.name}${v.segments_file ? " ✓" : ""}
          </button>
        `)}
      </div>

      ${video && html`
        <div class="video-layout">
          <div>
            <video ref=${videoRef} src=${video.file} controls preload="metadata" />
            <div class="note" style=${{ marginTop: 6 }}>
              ${fmtBytes(video.bytes)} · ${video.frames.detail} detail frames · ${video.frames.index} index frames
            </div>

            ${segments ? html`
              <${F}>
                <${SegmentTimeline} segments=${segments.segments} duration=${segments.duration_s}
                                    activeSeq=${activeSeq} onPick=${pick} />
                <div class="legend">
                  ${stepIds.map((id) => html`
                    <span key=${String(id)}>
                      <i class="swatch" style=${{ background: stepColor(id) }} />
                      ${id === null || id === undefined ? "unassigned" : `step ${id}`}
                    </span>
                  `)}
                </div>
                <div class="note" style=${{ marginTop: 8 }}>
                  ${segments.segments.length} sub-subtasks · ${segments.frames_reviewed} frames reviewed
                  · click a band to seek
                </div>

                ${active && html`<${SegmentDetail} segment=${active} frames=${frames} onOpenFrame=${openFrame} />`}

                ${segments.notes && html`
                  <div class="card"><h4>reviewer notes</h4><p class="note">${segments.notes}</p></div>
                `}
              <//>
            ` : html`
              <div class="empty">
                No segmentation for this clip yet — pass 1 has not written
                ${" "}<code>${videoName}.segments.json</code>.
              </div>
            `}
          </div>

          <div>
            <h4 style=${{ marginTop: 0 }}>Sub-subtasks</h4>
            ${segments ? html`
              <div class="seg-list">
                ${segments.segments.map((s) => html`
                  <button key=${s.seq} class=${"seg-row" + (activeSeq === s.seq ? " active" : "")}
                          onClick=${() => pick(s)}>
                    <div class="label">
                      <i class="swatch" style=${{ background: stepColor(s.step_id), marginRight: 6 }} />
                      ${s.seq}. ${s.substep_label}
                    </div>
                    <div class="time">
                      ${fmtTime(s.t_start)}–${fmtTime(s.t_end)} · ${s.frame_count} frames
                    </div>
                  </button>
                `)}
              </div>
            ` : html`<div class="note">—</div>`}
          </div>
        </div>
      `}

      ${lightbox && html`<${Lightbox} ...${lightbox} onClose=${() => setLightbox(null)} />`}
    <//>
  `;
}

/* ---------------------------------------------------------- frames browser */

function FramesTab({ acs, videos }) {
  const [videoName, setVideoName] = useState(videos.length ? videos[0].name : null);
  const [set, setSet] = useState("detail");
  const [data, setData] = useState(null);
  const [lightbox, setLightbox] = useState(null);

  useEffect(() => {
    if (!videoName) return;
    setData(null);
    api(`/api/frames/${acs}/${videoName}?set=${set}`).then(setData);
  }, [acs, videoName, set]);

  if (!videos.length) return html`<div class="empty">No videos, so no frames.</div>`;

  return html`
    <${F}>
      <div class="video-picker">
        ${videos.map((v) => html`
          <button key=${v.name} class=${v.name === videoName ? "active" : ""}
                  onClick=${() => setVideoName(v.name)}>${v.name}</button>
        `)}
      </div>
      <div class="video-picker">
        ${["detail", "index"].map((s) => html`
          <button key=${s} class=${set === s ? "active" : ""} onClick=${() => setSet(s)}>
            ${s === "detail" ? "detail 960px" : "index 480px"}
          </button>
        `)}
      </div>
      ${data ? html`
        <${F}>
          <div class="note" style=${{ margin: "6px 0 12px" }}>${data.count} frames · 4 fps</div>
          <div class="frame-grid">
            ${data.frames.map((name) => html`
              <figure key=${name}>
                <img src=${`${data.base}/${name}`} alt=${name} loading="lazy"
                     onClick=${() => setLightbox({ src: `${data.base}/${name}`, caption: `${videoName} · ${name}` })} />
                <figcaption>${frameTime(name).toFixed(2)}s</figcaption>
              </figure>
            `)}
          </div>
        <//>
      ` : html`<div class="empty">Loading frames…</div>`}
      ${lightbox && html`<${Lightbox} ...${lightbox} onClose=${() => setLightbox(null)} />`}
    <//>
  `;
}

/* ----------------------------------------------------- photo assessment fit */

const postJSON = (path, body) =>
  fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(async (r) => (r.ok ? r.json() : { error: (await r.text()) || r.statusText }));

const verdictTone = (v) =>
  v === "pass" ? "good" : v === "fail" ? "bad"
  // `review` is the rolled-up form of an abstention: one point the photo could
  // not settle leaves the subtask for a human, and it is warned about in the
  // same tone as the abstention it came from rather than left neutral.
  : v === "unsure" || v === "review" ? "warn" : "";

const fmtUSD = (n) =>
  n === null || n === undefined ? "—" : n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;

/* The criterion is the whole experiment: the same photo graded against
   different wording gives different verdicts, and finding wording that a model
   reads the way an instructor does is the point of this tab. So it is a plain
   editable textarea, saved per target, with the pack-derived text always
   recoverable. */
/* Client-side twin of `sheet_checks()` in server.py, kept in step with it.
   The server is authoritative — it re-parses at run time and its split is what
   gets graded — but the criterion is editable here, and an editor who cannot
   see what their wording produced until the results come back is editing
   blind. Both halves must agree, including the defect restatement: a defect
   graded as written scores "the tube is kinked" as a pass on a kinked tube. */
const POINT_HEADINGS = ["criteria", "critical defects", "overall decision", "source basis"];

function parsePoints(criterion) {
  const points = [];
  let heading = null;
  for (const raw of String(criterion || "").split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const lowered = line.toLowerCase().replace(/:$/, "");
    if (POINT_HEADINGS.includes(lowered)) { heading = lowered; continue; }
    if (heading === "criteria") {
      const match = line.match(/^\d+[.)]\s+(.+)/);
      if (match) {
        points.push({ id: `c${points.filter((p) => !p.defect).length + 1}`,
                      statement: match[1].trim(), defect: false });
      }
    } else if (heading === "critical defects") {
      const match = line.match(/^[-*•]\s+(.+)/);
      if (match) {
        points.push({ id: `d${points.filter((p) => p.defect).length + 1}`,
                      statement: `The finished work shows no such defect: ${
                        match[1].trim().replace(/\.$/, "")}.`,
                      defect: true });
      }
    }
  }
  return points;
}

const CRITERION_SOURCE_LABELS = {
  "pack.checks": "from pack checks",
  "pack.evidence": "from pack evidence",
  "pack.step_text": "step text — not an acceptance criterion",
  "drafted.step": "drafted from procedure + handbook",
  "drafted.task": "drafted from procedure + handbook",
  // Both of these used to fall through to the "segment description" default
  // below, which said the text was not an acceptance criterion. It is one, and
  // saying otherwise invites an instructor to discard a criterion that was
  // drafted from the procedure and handbook.
  "drafted.section": "drafted from procedure + handbook",
  "criteria.subtask": "subtask grading sheet — not SME-reviewed",
  fallback: "no pack criterion — generic fallback",
};

/* Where each condition came from. A criterion an instructor cannot trace back to
   a source is one they cannot defend to a student, and the failure to watch for
   is over-attribution — crediting a number to the handbook that the handbook
   never states. The note carries the quoted phrase so that is checkable here
   rather than by re-reading the chapter. */
function SourceTrail({ target }) {
  const sources = (target.sources || []).filter((s) => s && (s.condition || s.source));
  const conflicts = target.conflicts || [];
  if (!sources.length && !conflicts.length) return null;
  return html`
    <${F}>
      ${sources.length > 0 && html`
        <details class="source-trail">
          <summary>${sources.length} attributed condition${sources.length === 1 ? "" : "s"}</summary>
          <ul>
            ${sources.map((s, i) => html`
              <li key=${i}>
                <div class="source-condition">${s.condition}</div>
                <div class="source-meta">
                  <${Pill} kind=${s.assumed ? "warn" : "accent"}>${s.source || "unattributed"}<//>
                  ${s.assumed && html`<${Pill} kind="warn">provisional<//>`}
                </div>
                ${s.note && html`<p class="source-note">${s.note}</p>`}
              </li>
            `)}
          </ul>
        </details>
      `}
      ${conflicts.length > 0 && html`
        <div class="source-conflict">
          <span class="eyebrow">Sources disagree</span>
          <ul>${conflicts.map((c, i) => html`<li key=${i}>${c}</li>`)}</ul>
          <p>
            The procedure sheet is treated as operative, since it is what the student was
            taught. Recorded rather than silently resolved.
          </p>
        </div>
      `}
    <//>
  `;
}

/* Pack-derived targets carry no frame: segmentation is what pins a frame to a
   step, and most tasks have none. Rather than leave the criterion ungradeable,
   the operator picks a frame from the extracted clips. */
function FramePicker({ acs, target, onPick }) {
  const all = target.frame_candidates || [];
  // The clip this target's work was filmed on — its own for a subtask or a
  // reviewed interval, its subtask's for a step. Those frames are the only ones
  // that could show the work, so they are the only ones offered: a frame from
  // another subtask grades exactly as confidently as the right one, and picking
  // one out of 1593 frames of unrelated footage was the easy mistake to make.
  const own = all.filter((c) => c.video === (target.clip || target.video));
  // No clip resolved — the task's footage could not be matched to this work at
  // all. Everything is offered rather than nothing, since an unscoped choice
  // beats no choice; the row already says the frame is a guess.
  const clips = own.length ? own : all;
  const [clip, setClip] = useState(
    clips.find((c) => c.video === target.video)?.video
    || (clips.length ? clips[0].video : null));
  const [frames, setFrames] = useState(null);

  useEffect(() => {
    setFrames(null);
    if (clip) api(`/api/frames/${acs}/${clip}`).then(setFrames);
  }, [acs, clip]);

  if (!clips.length) {
    return html`
      <div class="frame-picker empty">
        No extracted frames for this task, so there is nothing to grade against yet.
        The criterion below is still the deliverable — it is what a submitted photo
        would be judged by.
      </div>
    `;
  }

  return html`
    <div class="frame-picker">
      <div class="eyebrow">Pick a frame to grade against</div>
      <div class="clip-tabs">
        ${clips.map((c) => html`
          <button key=${c.video} class=${"seg" + (clip === c.video ? " active" : "")}
                  onClick=${() => setClip(c.video)}>${c.video} (${c.frame_count})</button>
        `)}
      </div>
      ${frames ? html`
        <div class="frame-strip">
          ${frames.frames.map((name) => html`
            <img key=${name} loading="lazy" src=${`${frames.base}/${name}`} alt=${name}
                 title=${name}
                 class=${"frame-choice" + (target.frame === name ? " chosen" : "")}
                 onClick=${() => onPick(target.target_id, { video: clip, frame: name })} />
          `)}
        </div>
      ` : html`<div class="empty">Loading frames…</div>`}
    </div>
  `;
}

/* The last frame of a clip is where filming stopped, not where the work
   finished. Either let a model find a better one, or let the operator supply
   the photo a student actually took. */
function FrameSource({ acs, target, onFrame }) {
  const [busy, setBusy] = useState(null);
  const [note, setNote] = useState(null);
  const input = useRef(null);

  const findBest = async () => {
    setBusy("find"); setNote(null);
    const r = await postJSON(`/api/photo/best-frame/${acs}`, {
      target_id: target.target_id, video: target.video, sample: 14,
    });
    setBusy(null);
    if (!r || r.error) return setNote(`Could not search: ${(r && r.message) || "failed"}`);
    if (!r.frame) return setNote(r.reason || "No frame shows completed work.");
    setNote(r.reason);
    onFrame({ video: r.video, frame: r.frame, frame_url: r.frame_url });
  };

  const upload = async (file) => {
    if (!file) return;
    setBusy("upload"); setNote(null);
    const data = await new Promise((res) => {
      const reader = new FileReader();
      reader.onload = () => res(reader.result);
      reader.readAsDataURL(file);
    });
    const r = await postJSON(`/api/photo/upload/${acs}`, {
      target_id: target.target_id, filename: file.name, data,
    });
    setBusy(null);
    if (!r || r.error) return setNote(`Upload failed: ${(r && r.error) || ""}`);
    setNote(`Uploaded ${r.frame}`);
    onFrame({ video: null, frame: r.frame, frame_url: r.frame_url, uploaded: true });
  };

  return html`
    <div class="frame-source">
      <button class="btn ghost" disabled=${busy || !target.video}
              onClick=${findBest}>
        ${busy === "find" ? "Searching…" : "Find a finished-work frame"}
      </button>
      <button class="btn ghost" disabled=${busy}
              onClick=${() => input.current && input.current.click()}>
        ${busy === "upload" ? "Uploading…" : "Upload a photo"}
      </button>
      <input type="file" accept="image/*" ref=${input} style=${{ display: "none" }}
             onChange=${(e) => upload(e.target.files && e.target.files[0])} />
      ${target.uploaded && html`<${Pill} kind="good">uploaded<//>`}
      ${target.frame_suggested && html`<${Pill} kind="warn">suggested frame<//>`}
      ${target.frame_reviewed && html`<${Pill} kind="good">reviewed frame<//>`}
      ${note && html`<div class="criterion-hint">${note}</div>`}
    </div>
  `;
}

function CriterionEditor({ acs, target, draft, onDraft, onSaved, models }) {
  const [saving, setSaving] = useState(false);
  const [drafting, setDrafting] = useState(false);
  const [proposal, setProposal] = useState(null);
  const dirty = draft !== undefined && draft !== target.criterion;
  const value = draft !== undefined ? draft : target.criterion || "";
  const isDefault = value.trim() === (target.criterion_default || "").trim();

  const save = async (text) => {
    setSaving(true);
    await postJSON(`/api/photo/prompts/${acs}`, {
      target_id: target.target_id,
      criterion: text,
    });
    setSaving(false);
    onSaved(target.target_id, text);
  };

  return html`
    <div class="criterion-editor">
      <div class="criterion-head">
        <span class="eyebrow">Criterion sent to the model</span>
        <span class=${"criterion-source src-" + (target.criterion_source || "").replace(".", "-")}>
          ${CRITERION_SOURCE_LABELS[target.criterion_source] ||
            "segment description — not an acceptance criterion"}
        </span>
        ${target.edited && html`<${Pill} kind="warn">edited<//>`}
        ${dirty && html`<${Pill} kind="warn">unsaved<//>`}
      </div>
      <${SourceTrail} target=${target} />
      <textarea
        class="criterion-input"
        rows=${Math.min(10, Math.max(3, value.split("\n").length + 1))}
        value=${value}
        spellcheck="false"
        onInput=${(e) => onDraft(target.target_id, e.target.value)}
      />
      ${proposal && html`
        <div class="draft-proposal">
          <div class="criterion-head">
            <span class="eyebrow">
              Drafted from the procedure + handbook${target.frame ? " + this photo" : ""}
            </span>
            <${Pill} kind="accent">${(proposal.model || "").split("/")[1]}<//>
          </div>
          ${proposal.error
            ? html`<p class="run-error">${proposal.error}: ${proposal.message}</p>`
            : html`
              <pre class="draft-text">${proposal.criterion}</pre>
              ${(proposal.not_photo_gradeable || []).length > 0 && html`
                <div class="draft-aside">
                  <strong>Not photo-gradeable:</strong>
                  <ul>${proposal.not_photo_gradeable.map((x, i) =>
                    html`<li key=${i}>${x}</li>`)}</ul>
                </div>`}
              ${proposal.photo_limitations && html`
                <p class="draft-aside"><strong>This photo may not support it:</strong>
                  ${proposal.photo_limitations}</p>`}
              ${proposal.required_framing && html`
                <p class="draft-aside"><strong>Suggested framing:</strong>
                  ${proposal.required_framing}</p>`}
              <p class="draft-warn">
                ${target.frame
                  ? `A criterion drafted from the photo it will grade is circular — it can
                     be written to pass what it already sees. `
                  : ""}
                Read it as an SME before using it, and treat a pass against an unreviewed
                draft as meaningless.
              </p>
              <div class="criterion-actions">
                <button class="btn" onClick=${() => {
                  onDraft(target.target_id, proposal.criterion); setProposal(null);
                }}>Use as criterion</button>
                <button class="btn ghost" onClick=${() => setProposal(null)}>Discard</button>
              </div>
            `}
        </div>
      `}

      <div class="criterion-actions">
        <button class="btn" disabled=${saving || !dirty} onClick=${() => save(value)}>
          ${saving ? "Saving…" : "Save"}
        </button>
        <button class="btn" disabled=${drafting} onClick=${async () => {
          setDrafting(true); setProposal(null);
          const result = await postJSON(`/api/photo/draft`, {
            task_code: acs, target_id: target.target_id,
            model: (models && models[0]) || undefined,
          });
          setDrafting(false); setProposal(result);
        }}>
          ${drafting ? "Drafting…"
            : `Draft criterion from procedure + handbook${target.frame ? " + photo" : ""}`}
        </button>
        <button class="btn ghost" disabled=${isDefault}
                onClick=${() => { onDraft(target.target_id, target.criterion_default || ""); }}>
          Reset to ${(target.criterion_source || "").startsWith("criteria.")
            ? "sheet text"
            : (target.criterion_source || "").startsWith("drafted")
            ? "compiled text" : "pack text"}
        </button>
        ${target.edited && html`
          <button class="btn ghost" onClick=${() => save("")}>Clear saved edit</button>
        `}
        <span class="criterion-hint">
          Edits persist to <code>build/photo_eval/${acs}/prompts.json</code>. Unsaved text is
          still used for the next run.
        </span>
      </div>
    </div>
  `;
}

/* Match test: the same photo against reworded criteria, each labelled with the
   verdict it ought to get. Editing a criterion until it no longer describes the
   photo and watching whether the verdict follows is the only way to tell a
   model that is grading from one that is agreeing with whatever it is handed. */
function VariantEditor({ acs, target, variants, onChange, onSaved }) {
  const [saving, setSaving] = useState(false);

  const update = (i, patch) =>
    onChange(target.target_id, variants.map((v, j) => (j === i ? { ...v, ...patch } : v)));

  const add = (seed) =>
    onChange(target.target_id, [...variants, {
      id: `v${Date.now().toString(36)}`,
      label: seed ? "reworded" : "",
      criterion: seed || "",
      expected: "fail",
    }]);

  const save = async () => {
    setSaving(true);
    const saved = await postJSON(`/api/photo/prompts/${acs}`, {
      target_id: target.target_id,
      variants: variants.filter((v) => (v.criterion || "").trim()),
    });
    setSaving(false);
    if (saved && saved.variants) onSaved(target.target_id, saved.variants);
  };

  return html`
    <div class="variant-block">
      <div class="criterion-head">
        <span class="eyebrow">Match test — same frame, reworded criterion</span>
        ${variants.length > 0 && html`<${Pill}>${variants.length}<//>`}
      </div>
      <p class="criterion-hint">
        Change the wording so the photo <em>should not</em> satisfy it, mark the expected
        verdict, and run. A model that still passes is not reading the criterion.
      </p>

      ${variants.map((v, i) => html`
        <div class="variant-row" key=${v.id || i}>
          <div class="variant-head">
            <input class="variant-label" placeholder="what did you change?"
                   value=${v.label || ""}
                   onInput=${(e) => update(i, { label: e.target.value })} />
            <select class="variant-expected" value=${v.expected || ""}
                    onChange=${(e) => update(i, { expected: e.target.value || null })}>
              <option value="">no expectation</option>
              <option value="pass">should pass</option>
              <option value="not_pass">should not pass (fail or abstain)</option>
              <option value="fail">should fail outright</option>
              <option value="unsure">should abstain</option>
            </select>
            <button class="btn ghost" title="Remove"
                    onClick=${() => onChange(target.target_id,
                      variants.filter((_, j) => j !== i))}>✕</button>
          </div>
          <textarea class="criterion-input" rows="3" spellcheck="false"
                    value=${v.criterion || ""}
                    onInput=${(e) => update(i, { criterion: e.target.value })} />
        </div>
      `)}

      <div class="criterion-actions">
        <button class="btn" onClick=${() => add(target.criterion || "")}>
          Add from current criterion
        </button>
        <button class="btn ghost" onClick=${() => add("")}>Add blank</button>
        <button class="btn" disabled=${saving || !variants.length} onClick=${save}>
          ${saving ? "Saving…" : "Save variants"}
        </button>
      </div>
    </div>
  `;
}

function VerdictCell({ result }) {
  const [open, setOpen] = useState(false);
  if (!result) return html`<td class="verdict-cell">—</td>`;
  if (result.error) {
    return html`
      <td class="verdict-cell err">
        <${Pill} kind="bad">${result.error}<//>
        <div class="verdict-note">${result.message}</div>
      </td>`;
  }
  const scored = Boolean(result.expected);
  const wrong = scored && result.verdict !== result.expected;
  return html`
    <td class=${"verdict-cell" + (wrong ? " expect-wrong" : scored ? " expect-ok" : "")}>
      <button class="verdict-toggle" onClick=${() => setOpen(!open)}>
        <${Pill} kind=${verdictTone(result.verdict)}>${result.verdict}<//>
        <span class="verdict-conf">${result.score !== undefined && result.score !== null
          ? `${result.score}/100` : `${(result.confidence * 100).toFixed(0)}%`}</span>
        ${scored && html`<span class=${"expect-mark " + (wrong ? "bad" : "good")}>
          ${wrong ? "✕" : "✓"}
        </span>`}
      </button>
      ${wrong && html`<div class="verdict-note bad">expected ${result.expected}</div>`}
      ${(result.critical_defects || []).length > 0 && html`
        <div class="verdict-note bad">${result.critical_defects.length} critical defect${
          result.critical_defects.length === 1 ? "" : "s"}</div>`}
      ${result.model_score !== undefined && result.model_score !== result.score && html`
        <div class="cond-tally">model said ${result.model_score}, recomputed ${result.score}</div>`}
      ${result.conditions_total > 0 && html`
        <div class="cond-tally">
          ${result.conditions_passed}✓ ${result.conditions_failed}✗
          ${result.conditions_blocked > 0 && html`<span class="blocked">
            ${result.conditions_blocked} blocked</span>`}
        </div>`}
      ${open && html`
        <div class="verdict-detail">
          ${(result.conditions || []).length > 0 && html`
            <table class="cond-table">
              ${result.conditions.map((c, i) => html`
                <tr key=${i} class=${"cond-" + c.verdict}>
                  <td class="cond-v">${c.verdict === "pass" ? "✓"
                    : c.verdict === "fail" ? "✗" : "—"}</td>
                  <td class="cond-p">${c.p_correct === null || c.p_correct === undefined
                    ? html`<span class="unobs">not visible</span>`
                    : (c.p_correct * 100).toFixed(0) + "%"}</td>
                  <td>
                    <div>${c.text}</div>
                    ${c.note && html`<div class="cond-note">${c.note}</div>`}
                  </td>
                </tr>
              `)}
            </table>`}
          ${result.observed && html`<p><strong>Observed:</strong> ${result.observed}</p>`}
          ${result.rationale && html`<p><strong>Why:</strong> ${result.rationale}</p>`}
          ${result.missing_evidence && html`
            <p><strong>Missing:</strong> ${result.missing_evidence}</p>`}
          <div class="verdict-meta">
            ${result.latency_s}s · ${result.prompt_tokens}→${result.completion_tokens} tok
            · ${fmtUSD(result.cost_usd)}
            ${result.parse !== "json" ? ` · parse: ${result.parse}` : ""}
          </div>
        </div>
      `}
    </td>
  `;
}

/* Every criteria point in a subtask is graded on its own call — that is what
   makes a failure name the condition that failed — but a row per point buries
   the subtask it belongs to under four near-identical lines. The points are
   regrouped here into one row per subtask, one cell per model, so the grid reads
   as "this subtask, by this model" and the points sit inside that cell.

   Same roll-up rule as `handle_photo_run` in server.py: one fail fails the
   subtask, and a point the photo could not settle leaves the subtask for review
   rather than being rounded up to a pass. */
function rollUp(results) {
  const graded = (results || []).filter((r) => r && !r.error);
  if (!graded.length) return null;
  if (graded.some((r) => r.verdict === "fail")) return "fail";
  if (graded.some((r) => r.verdict !== "pass")) return "review";
  return "pass";
}

/* Did this model get the point right? Two sources of truth, and only one of
   them is stated: a variant or control carries the verdict it ought to get, so
   it is scored against that. An unlabelled point is graded against a reference
   frame — footage of work an instructor accepted — so `pass` is the answer that
   frame implies. That inference is the whole caveat on the totals row, and the
   note under the grid says so rather than leaving the number to speak for
   itself. */
function pointCorrect(result) {
  if (!result || result.error || !result.verdict) return false;
  if (result.expected) {
    return result.expected === "not_pass"
      ? result.verdict !== "pass"
      : result.verdict === result.expected;
  }
  return result.verdict === "pass";
}

function SheetCell({ results }) {
  const [open, setOpen] = useState(false);
  const points = results || [];
  const verdict = rollUp(points);
  const passed = points.filter((p) => !p.error && p.verdict === "pass").length;
  const failed = points.filter((p) => !p.error && p.verdict === "fail").length;
  const unsettled = points.filter(
    (p) => !p.error && p.verdict && p.verdict !== "pass" && p.verdict !== "fail").length;
  const errored = points.filter((p) => p.error).length;
  const cost = points.reduce((sum, p) => sum + (p.cost_usd || 0), 0);
  const mark = (p) => (p.error ? "⚠" : p.verdict === "pass" ? "✓"
    : p.verdict === "fail" ? "✗" : "—");
  return html`
    <td class="verdict-cell sheet-cell">
      <button class="verdict-toggle" onClick=${() => setOpen(!open)}>
        <${Pill} kind=${verdictTone(verdict)}>${verdict || "no result"}<//>
        <span class="cond-tally">
          ${passed}✓ ${failed}✗${unsettled > 0 ? html` <span class="blocked">${unsettled}—</span>` : ""}
        </span>
      </button>
      <div class="point-list">
        ${points.map((p) => html`
          <div key=${p.target_id} class=${"point point-" + (p.error ? "err" : p.verdict)}>
            <div class="point-line">
              <span class="point-id">${p.check_id || "—"}</span>
              <span class="point-v">${mark(p)}</span>
              <span class="point-text">${p.error ? p.error : p.criterion}</span>
            </div>
            ${open && html`
              <div class="point-detail">
                ${p.error
                  ? html`<p>${p.message}</p>`
                  : html`
                    <p class="point-conf">
                      ${p.confidence === null || p.confidence === undefined
                        ? html`<span class="unobs">not visible</span>`
                        : `${(p.confidence * 100).toFixed(0)}% confident`}
                      ${p.expected && html`<span class=${"expect-mark " +
                        (pointCorrect(p) ? "good" : "bad")}>
                        ${pointCorrect(p) ? "✓" : "✕"} expected ${p.expected}
                      </span>`}
                    </p>
                    ${p.rationale && html`<p>${p.rationale}</p>`}
                    ${p.missing_evidence && html`
                      <p><strong>Missing:</strong> ${p.missing_evidence}</p>`}
                    <div class="verdict-meta">
                      ${p.latency_s}s · ${p.prompt_tokens}→${p.completion_tokens} tok
                      · ${fmtUSD(p.cost_usd)}
                      ${p.parse !== "json" ? ` · parse: ${p.parse}` : ""}
                    </div>`}
              </div>`}
          </div>
        `)}
      </div>
      ${errored > 0 && html`<div class="verdict-note bad">${errored} call${
        errored === 1 ? "" : "s"} errored</div>`}
      ${open && html`<div class="verdict-meta">${points.length} calls · ${fmtUSD(cost)}</div>`}
    </td>
  `;
}

function ResultsGrid({ run, models, restored }) {
  const byTarget = useMemo(() => {
    const map = new Map();
    (run.results || []).forEach((r) => {
      // A per-point result names the subtask it rolls up to. Runs restored from
      // disk predate that field, so the label prefix is the fallback — the ids
      // are `<target>::c1`, and splitting either one apart is guesswork the
      // parent fields exist to remove.
      const parent = r.rolls_up_to || r.target_id;
      if (!map.has(parent)) {
        map.set(parent, {
          ...r,
          target_id: parent,
          label: r.rolls_up_to
            ? r.parent_label || String(r.label || "").split(" · ")[0]
            : r.label,
          criterion: r.rolls_up_to ? r.parent_criterion || null : r.criterion,
          cells: {},
        });
      }
      const row = map.get(parent);
      (row.cells[r.model] = row.cells[r.model] || []).push(r);
    });
    return [...map.values()];
  }, [run]);

  const disagree = (row) => {
    const verdicts = models.map((m) => rollUp(row.cells[m])).filter(Boolean);
    return new Set(verdicts).size > 1;
  };

  // Every point graded, across every subtask in the run — the question the grid
  // itself cannot answer, because it is spread one subtask per row. Counted on
  // points, not subtasks: a model that fails one point of six has failed the
  // subtask, and rolling up first would hide the five it got.
  const totals = useMemo(() => {
    const out = {};
    models.forEach((m) => {
      const points = (run.results || []).filter((r) => r.model === m);
      const graded = points.filter((p) => !p.error);
      out[m] = {
        total: points.length,
        correct: points.filter(pointCorrect).length,
        pass: graded.filter((p) => p.verdict === "pass").length,
        fail: graded.filter((p) => p.verdict === "fail").length,
        // Everything that is neither — `unsure`, and any verdict a model
        // returned that this build does not know. Bucketed with the
        // abstentions rather than dropped, so the three counts and the errors
        // always add up to the total.
        unsure: graded.filter((p) => p.verdict !== "pass" && p.verdict !== "fail").length,
        errors: points.filter((p) => p.error).length,
        labelled: points.filter((p) => p.expected).length,
      };
    });
    return out;
  }, [run, models]);
  const labelled = models.reduce((n, m) => Math.max(n, totals[m]?.labelled || 0), 0);

  const s = run.summary || {};
  return html`
    <${F}>
      <div class="card run-summary">
        <h3>
          Run ${run.run_id}
          ${restored && html`<${Pill} kind="warn">restored from disk<//>`}
        </h3>
        ${restored && html`
          <p class="warn-note">
            The last run recorded for this task, reloaded from
            <code>build/photo_eval/${run.task_code}/</code>. It reflects the criteria and
            frames as they were then — not any edit made since. Run grading again to
            grade what is on screen now.
          </p>
        `}
        <div class="run-stats">
          <span><strong>${s.calls}</strong> calls</span>
          <span class=${s.errors ? "bad" : ""}><strong>${s.errors}</strong> errors</span>
          <span><strong>${fmtUSD(s.cost_usd)}</strong> spent</span>
          ${s.scored > 0 && html`
            <span class=${s.match_accuracy === 1 ? "good" : s.match_accuracy >= 0.5 ? "" : "bad"}>
              <strong>${s.scored_correct}/${s.scored}</strong> matched expectation
              (${fmtMetric(s.match_accuracy)})
            </span>
          `}
        </div>
        ${s.scored === 0 && html`
          <p class="warn-note">
            Nothing in this run carried an expected verdict, so there is no accuracy to
            report — only what each model happened to say. Every reference frame is correct
            work, so a sheet of passes cannot distinguish a model that grades from one that
            always passes. Reword a criterion under <strong>Match test</strong> and mark
            what it should return.
          </p>
        `}
      </div>

      <div class="card">
        <table class="results-table">
          <thead>
            <tr>
              <th>Target</th>
              ${models.map((m) => html`<th key=${m}>${m.split("/")[1] || m}</th>`)}
            </tr>
          </thead>
          <tbody>
            ${byTarget.map((row) => html`
              <tr key=${row.target_id} class=${(row.is_control ? "control-row " : "") +
                                                (disagree(row) ? "disagree" : "")}>
                <td class="target-cell">
                  ${row.is_control && html`<${Pill} kind="warn">control<//>`}
                  ${row.is_variant && html`<${Pill} kind="accent">variant<//>`}
                  ${row.expected && html`<span class="expect-tag">expect ${row.expected}</span>`}
                  <div class="target-label">${row.label}</div>
                  <code>${row.frame}</code>
                  ${row.criterion && html`
                    <details class="row-criterion">
                      <summary>criterion</summary><pre>${row.criterion}</pre>
                    </details>`}
                  ${disagree(row) && html`<div class="disagree-note">models disagree</div>`}
                </td>
                ${models.map((m) => {
                  const cells = row.cells[m] || [];
                  // A subtask split into points gets the combined cell; a
                  // variant or an unsplit criterion is one call and stays a
                  // single verdict.
                  return cells.length && cells[0].rolls_up_to
                    ? html`<${SheetCell} key=${m} results=${cells} />`
                    : html`<${VerdictCell} key=${m} result=${cells[0]} />`;
                })}
              </tr>
            `)}
          </tbody>
          <tfoot>
            <tr class="totals-row">
              <td class="target-cell"><strong>All criteria points</strong>
                <div class="target-label">every point, across every subtask</div>
              </td>
              ${models.map((m) => {
                const t = totals[m]
                  || { total: 0, correct: 0, pass: 0, fail: 0, unsure: 0, errors: 0 };
                return html`
                  <td key=${m} class="verdict-cell totals-cell">
                    <div class="totals-count">
                      <strong>${t.correct}</strong>/${t.total} correct
                    </div>
                    <div class="totals-split">
                      <span class="t-pass">${t.pass} pass</span>
                      <span class="t-fail">${t.fail} fail</span>
                      <span class="t-unsure">${t.unsure} unsure</span>
                    </div>
                    ${t.errors > 0 && html`
                      <div class="verdict-note bad">${t.errors} errored</div>`}
                  </td>`;
              })}
            </tr>
          </tfoot>
        </table>
        <p class="warn-note">
          A point counts as correct when it matches the verdict it was labelled with,
          and — for the great majority, which carry no label —
          when it comes back <code>pass</code> on a reference frame.
          ${labelled > 0
            ? html` Only ${labelled} point${labelled === 1 ? "" : "s"} in this run
                    carried an expected verdict.`
            : html` No point in this run carried an expected verdict.`}
          Reference frames show work an instructor accepted, so <code>pass</code> is the
          answer they imply — but a model that passes everything scores the same as one
          that grades, which is what <strong>Match test</strong> exists to separate.
        </p>
      </div>
    <//>
  `;
}

function PhotoAssessmentTab({ acs }) {
  const [config, setConfig] = useState(null);
  const [targets, setTargets] = useState(null);
  const [selected, setSelected] = useState(() => new Set());
  const [models, setModels] = useState([]);
  const [drafts, setDrafts] = useState({});
  const [variantDrafts, setVariantDrafts] = useState({});
  const [focus, setFocus] = useState(null);
  const [kind, setKind] = useState("subtask");
  const [query, setQuery] = useState("");
  const [running, setRunning] = useState(false);
  const [run, setRun] = useState(null);
  // Whether the grid below is this session's run or the last one on disk. They
  // look identical, and a restored run is easily misread as confirmation that
  // the edit you just made was graded.
  const [restored, setRestored] = useState(false);
  const [error, setError] = useState(null);
  const [lightbox, setLightbox] = useState(null);

  useEffect(() => {
    api("/api/photo/models").then((c) => { setConfig(c); if (c) setModels(c.defaults); });
  }, []);
  useEffect(() => {
    setTargets(null); setSelected(new Set()); setRun(null); setFocus(null);
    setDrafts({}); setVariantDrafts({});
    api(`/api/photo/targets/${acs}`).then((d) => {
      const list = (d && d.targets) || [];
      setTargets(list);
      // Focus the first row of the tab actually being shown. Focusing
      // targets[0] put the detail pane on a target the visible list did not
      // contain — on a segmented task that meant a reviewed interval while the
      // Subtasks tab was open.
      const first = list.find((t) => t.kind === "section") || list[0];
      if (first) setFocus(first.target_id);
    });
    // Runs are written to build/photo_eval/<ACS>/ and were being kept there
    // and never read: a reload, or a trip to another task and back, wiped the
    // results grid and the only way to see the verdicts again was to pay for
    // them again.
    api(`/api/photo/runs/${acs}?limit=1`).then((d) => {
      const last = d && d.runs && d.runs[0];
      if (last) { setRun(last); setRestored(true); }
    });
  }, [acs]);

  const filtered = useMemo(() => {
    if (!targets) return [];
    const q = query.trim().toLowerCase();
    const hit = (t) => !q || (t.label + " " + (t.step_id || "") + " " + (t.video || "") + " " +
                              (t.section || "") + " " + (t.clip || "")).toLowerCase().includes(q);

    // A pack section IS the subtask — the level the work is filmed and taught
    // at. `kind: "subtask"` is a misnomer kept for the wire format: those are
    // reviewed intervals *inside* a subtask, which the server calls
    // sub-subtasks. They are step-level evidence, and where they exist they
    // replace the step targets they cover, so they belong with the steps.
    //
    // Reading the kind literally put 150 intervals into the Subtasks tab on
    // AM.I.E.S1 and left three rows in Steps — the two levels swapped over on
    // exactly the tasks that have the most footage behind them.
    const isSubtask = (t) => t.kind === "section";
    const isStep = (t) => t.kind === "step" || t.kind === "subtask";

    if (kind === "subtask") return targets.filter((t) => isSubtask(t) && hit(t));

    // Steps, under the subtask each belongs to. A step read on its own — "Decide
    // the size of tubing to use" — does not say which piece of work it is part
    // of, and the two-level shape of the task is the thing this tab is for.
    const rows = [];
    for (const parent of targets.filter(isSubtask)) {
      const shown = targets.filter((t) => isStep(t) && t.section &&
                                          t.section === parent.section && hit(t));
      if (!shown.length) continue;
      rows.push({ ...parent, depth: 0, heading: true });
      rows.push(...shown.map((t) => ({ ...t, depth: 1 })));
    }
    // A step whose section matches no subtask — and every interval the reviewer
    // could not map to a pack step — would otherwise vanish from the only tab
    // that shows it. Listed flat rather than dropped.
    const claimed = new Set(rows.map((r) => r.target_id));
    rows.push(...targets.filter((t) => isStep(t) && !claimed.has(t.target_id) && hit(t)));
    return rows;
  }, [targets, kind, query]);

  const focused = (targets || []).find((t) => t.target_id === focus);
  const keyMissing = config && !config.key.present;

  // Off the criterion currently in the editor, not the one last saved, so the
  // point list and the call estimate both track an edit as it is typed.
  const pointsFor = (t) => parsePoints(
    drafts[t.target_id] !== undefined ? drafts[t.target_id] : t.criterion);

  const variantCount = useMemo(() => {
    if (!targets) return 0;
    return targets
      .filter((t) => selected.has(t.target_id))
      .reduce((n, t) => n + ((variantDrafts[t.target_id] !== undefined
        ? variantDrafts[t.target_id] : t.variants || [])
        .filter((v) => (v.criterion || "").trim()).length), 0);
  }, [targets, selected, variantDrafts]);

  // A target with no photo cannot be graded, and neither can one with no
  // criterion — the clip-derived subtasks have a frame but nothing to judge it
  // against yet. Both are skipped by the server, so the UI must say so before
  // the click rather than after the bill.
  const effective = (t) => (drafts[t.target_id] !== undefined
    ? drafts[t.target_id] : t.criterion) || "";
  const chosen = useMemo(
    () => (targets || []).filter((t) => selected.has(t.target_id)),
    [targets, selected]);
  const framed = useMemo(
    () => chosen.filter((t) => t.frame_exists && effective(t).trim()).length,
    [chosen, drafts]);
  const noPhoto = chosen.filter((t) => !t.frame_exists).length;
  const noCriterion = chosen.filter((t) => t.frame_exists && !effective(t).trim()).length;
  const runnable = framed > 0;

  // Every criteria point is its own call, so a selection of seven subtasks is
  // not seven calls — it is closer to seventy. Counting targets here understated
  // the bill by an order of magnitude.
  const points = useMemo(
    () => (targets || [])
      .filter((t) => selected.has(t.target_id) && t.frame_exists && effective(t).trim())
      .reduce((n, t) => n + Math.max(1, pointsFor(t).length), 0),
    [targets, selected, drafts]);

  const estimate = useMemo(() => {
    if (!config) return null;
    const calls = points + variantCount;
    const per = models
      .map((id) => config.models.find((m) => m.id === id))
      .filter(Boolean)
      .reduce((sum, m) => sum + calls * ((1900 * m.in_per_m + 250 * m.out_per_m) / 1e6), 0);
    return { calls: calls * models.length, usd: per };
  }, [config, models, points, variantCount]);

  const toggle = (id) => setSelected((prev) => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });

  // A frame chosen for a pack-derived target. Held here rather than saved,
  // because which photo a criterion is tried against is a property of the
  // experiment being run, not of the criterion.
  const pickFrame = (id, pick) => setTargets((ts) => ts.map((t) =>
    t.target_id === id
      ? { ...t, video: pick.video, frame: pick.frame, frame_exists: true,
          frame_url: `/files/build/frames/${acs}/${pick.video}/${pick.frame}`,
          frame_picked: true }
      : t));

  const onDraft = (id, text) => setDrafts((d) => ({ ...d, [id]: text }));
  const onSaved = (id, text) => {
    setTargets((ts) => ts.map((t) => t.target_id === id
      ? { ...t, criterion: text || t.criterion_default, edited: Boolean(text) } : t));
    setDrafts((d) => { const next = { ...d }; delete next[id]; return next; });
  };

  const variantsFor = (target) =>
    variantDrafts[target.target_id] !== undefined
      ? variantDrafts[target.target_id]
      : target.variants || [];

  const start = async () => {
    setRunning(true); setError(null);
    const criteria = {};
    Object.entries(drafts).forEach(([id, text]) => { if (text.trim()) criteria[id] = text; });
    // Send in-progress variants so the browser stays the source of truth.
    const variants = {};
    targets.filter((t) => selected.has(t.target_id)).forEach((t) => {
      const list = variantsFor(t).filter((v) => (v.criterion || "").trim());
      if (list.length) variants[t.target_id] = list;
    });
    // Frames the operator picked for targets that had none. Without these the
    // server rebuilds the targets from the pack and they arrive frameless
    // again, so the run would silently drop every one of them.
    const frames = {};
    targets.filter((t) => selected.has(t.target_id) && t.frame_picked).forEach((t) => {
      frames[t.target_id] = { video: t.video, frame: t.frame };
    });
    const result = await postJSON("/api/photo/run", {
      task_code: acs,
      models,
      target_ids: [...selected],
      criteria,
      variants,
      frames,
    });
    setRunning(false);
    if (!result || result.error) setError((result && result.error) || "Run failed");
    else { setRun(result); setRestored(false); }
  };

  if (!targets || !config) return html`<div class="empty">Loading photo assessment…</div>`;
  if (!targets.length) {
    return html`<div class="empty">
      No compiled pack for this task, so there are no criteria to grade against.
      Run <code>python3 packs/compile_pack.py ${acs}</code> to draft them from the
      procedure sheet and handbook.
    </div>`;
  }

  return html`
    <${F}>
      ${keyMissing && html`
        <div class="card key-warning">
          <h3>No OpenRouter API key</h3>
          <p>
            Set <code>OPENROUTER_API_KEY</code> in your environment or in the gitignored
            <code>alcor_agents/.env</code>, then restart the server. Everything below works
            without it except actually calling the models.
          </p>
        </div>
      `}

      <div class="card photo-config">
        <h3>Models</h3>
        <div class="model-picker">
          ${config.models.map((m) => html`
            <label key=${m.id} class=${"model-chip" + (models.includes(m.id) ? " on" : "")}>
              <input type="checkbox" checked=${models.includes(m.id)}
                     onChange=${() => setModels((prev) => prev.includes(m.id)
                       ? prev.filter((x) => x !== m.id) : [...prev, m.id])} />
              <span class="model-name">${m.label}</span>
              <span class="model-vendor">${m.vendor}</span>
              <span class="model-price">$${m.in_per_m}/$${m.out_per_m} per M</span>
            </label>
          `)}
        </div>

        <div class="run-bar">
          <span><strong>${points}</strong> criteria points across <strong>${framed}</strong>
            ${framed === 1 ? "target" : "targets"} × <strong>${models.length}</strong> models
            ${variantCount > 0 ? ` + ${variantCount} variants` : ""}</span>
          ${estimate && html`<span class="estimate">≈ ${estimate.calls} calls · ${fmtUSD(estimate.usd)}</span>`}
          <button class="btn primary"
                  disabled=${running || keyMissing || !runnable || !models.length}
                  onClick=${start}>
            ${running ? "Running…" : "Run grading"}
          </button>
        </div>
        ${noPhoto > 0 && html`
          <div class="run-warning">
            ${noPhoto} selected target${noPhoto === 1 ? " has" : "s have"} no photo and
            will be skipped. Pick a frame on each, or deselect them.
          </div>
        `}
        ${noCriterion > 0 && html`
          <div class="run-warning">
            ${noCriterion} selected subtask${noCriterion === 1 ? " has" : "s have"} no
            criteria yet and will be skipped. These are pieces of work the footage shows
            but the procedure sheet does not document — write the criteria in the box
            below and they will be graded point by point like any other.
          </div>
        `}
        ${error && html`<div class="run-error">${error}</div>`}
      </div>

      <div class="photo-layout">
        <div class="card target-list">
          <div class="target-filters">
            <div class="seg-control">
              ${[["subtask", "Subtasks"], ["step", "Steps"]].map(([id, label]) => html`
                <button key=${id} class=${"seg" + (kind === id ? " active" : "")}
                        onClick=${() => setKind(id)}>${label}</button>
              `)}
            </div>
            <input class="search" placeholder="Filter targets…" value=${query}
                   onInput=${(e) => setQuery(e.target.value)} />
            <div class="bulk">
              ${/* Headings are context, not work. Selecting them here would
                    quietly grade every subtask alongside the steps you asked
                    for, and bill for it. */""}
              <button class="btn ghost" onClick=${() =>
                setSelected(new Set(filtered.filter((t) => !t.heading)
                                            .map((t) => t.target_id)))}>Select all shown</button>
              <button class="btn ghost" onClick=${() => setSelected(new Set())}>Clear</button>
            </div>
          </div>
          <div class="target-rows">
            ${filtered.map((t) => html`
              <div key=${t.target_id}
                   class=${"target-row" + (t.depth ? " nested" : "") +
                           (t.heading ? " heading" : "") +
                           (focus === t.target_id ? " focused" : "") +
                           (!t.heading && selected.has(t.target_id) ? " selected" : "")}
                   onClick=${() => setFocus(t.target_id)}>
                ${t.heading
                  ? html`<span class="row-spacer" />`
                  : html`<input type="checkbox" checked=${selected.has(t.target_id)}
                                onClick=${(e) => e.stopPropagation()}
                                onChange=${() => toggle(t.target_id)} />`}
                ${t.frame_url
                  ? html`<img class="target-thumb" src=${t.frame_url} alt=${t.frame}
                              loading="lazy" />`
                  : html`<div class="target-thumb none" title="No frame chosen yet">no photo</div>`}
                <div class="target-text">
                  <div class="target-label">${t.label}</div>
                  <div class="target-sub">
                    ${t.kind === "section"
                      ? html`<${Pill}>subtask<//>`
                      : t.step_id ? html`<${Pill}>${t.step_id}<//>`
                      : html`<span class="muted">unmapped</span>`}
                    <span class="muted">
                      ${t.kind === "section"
                        // A subtask that exists only as a grading sheet has no
                        // pack steps to count, and "0 steps" would read as one
                        // that lost them rather than one the pack never had.
                        ? (t.step_count
                            ? `${t.step_count} step${t.step_count === 1 ? "" : "s"}`
                            : "criteria sheet")
                        : t.video || t.section || ""}
                    </span>
                    ${t.frame_suggested && html`<${Pill} kind="warn">frame guessed<//>`}
                    ${t.frame_reviewed && html`<${Pill} kind="good">reviewed frame<//>`}
                    ${t.needs_criteria
                      ? html`<${Pill} kind="warn">needs criteria<//>`
                      : t.criterion_source.startsWith("criteria.")
                      ? html`<span class="tick">subtask sheet</span>`
                      : t.criterion_source.startsWith("drafted")
                      ? html`<span class="tick">drafted criterion</span>`
                      : t.criterion_source.startsWith("pack.")
                      ? html`<span class="tick">pack criterion</span>`
                      : html`<span class="muted">description only</span>`}
                    ${t.edited && html`<${Pill} kind="warn">edited<//>`}
                  </div>
                </div>
              </div>
            `)}
          </div>
        </div>

        <div class="card target-detail">
          ${focused ? html`
            <${F}>
              <h3>${focused.label}</h3>
              <div class="detail-sub">
                ${focused.frame
                  ? html`<${F}>
                      ${/* Only a reviewed interval knows when its work ended.
                            A suggested or hand-picked frame has no `t_end`, and
                            printing "at 0:00.00" for it stated a timestamp
                            nobody established — and stated it in the same words
                            used where the time is real. */""}
                      <code>${focused.frame}</code>
                      ${focused.t_end != null
                        ? html` — final frame at ${fmtTime(focused.t_end)}`
                        : ""}
                      · ${focused.video}
                      ${focused.confidence && html` · review confidence ${focused.confidence}`}
                    <//>`
                  : html`<${F}>
                      ${focused.section ? `${focused.section} · ` : ""}
                      no photo attached — criterion only
                    <//>`}
              </div>
              ${focused.frame_suggested && !focused.frame_picked && html`
                <div class="suggested-note">
                  This frame was <strong>guessed</strong>: the section
                  “${focused.section}” was matched to the clip <code>${focused.video}</code>
                  by name
                  ${focused.frame_share
                    ? html`, which it shares with ${focused.frame_share[1] - 1} other
                        section${focused.frame_share[1] === 2 ? "" : "s"} — this one takes
                        slice ${focused.frame_share[0]} of ${focused.frame_share[1]}`
                    : ""}${focused.frame_position
                    ? html`, and step ${focused.frame_position[0]} of
                        ${focused.frame_position[1]} within it lands at
                        ${Math.round(100 * (focused.frame_fraction || 0))}% of the clip`
                    : ""}.
                  It assumes the work runs in procedure order at an even pace, and
                  <strong>no one reviewed it</strong>. Check it, or pick another below.
                </div>
              `}
              ${focused.frame_url
                ? html`<img class="detail-frame" src=${focused.frame_url} alt=${focused.frame}
                            onClick=${() => setLightbox({ src: focused.frame_url,
                                             caption: `${focused.video} · ${focused.frame}` })} />`
                : ""}
              ${/* A reviewed sub-subtask frame was chosen deliberately and is
                    left alone; everything else is either frameless or a guess,
                    and both need the picker. */
                focused.kind !== "subtask" &&
                (!focused.frame_url || focused.frame_suggested || focused.frame_picked) &&
                html`<${FramePicker} acs=${acs} target=${focused} onPick=${pickFrame} />`}
              <${FrameSource} acs=${acs} target=${focused} onFrame=${(f) =>
                setTargets((ts) => ts.map((t) => t.target_id === focused.target_id
                  ? { ...t, ...f, frame_exists: true, frame_suggested: false } : t))} />
              ${focused.description && html`
                <p class="detail-desc"><strong>Reviewed as:</strong> ${focused.description}</p>`}
              ${focused.needs_criteria && !effective(focused).trim() && html`
                <div class="criterion-basis">
                  <span class="eyebrow">No criteria yet</span>
                  <p>
                    The footage shows this piece of work, but the procedure sheet for this
                    task does not document it and no grading sheet exists in
                    <code>criteria/${acs}/</code>. Nothing has been invented for it —
                    write the criteria below and they will be split into pass/fail points
                    like any other subtask.
                  </p>
                </div>
              `}
              ${/* Exactly what will be asked, one call per line, each answered
                    pass or fail on its own. Shown because the criterion below is
                    editable: the points are derived from that text, so an editor
                    needs to see what their wording actually produced rather than
                    finding out from the results grid.

                    A critical defect is listed as its absence. The sheets write
                    defects as things that must NOT be present, and grading that
                    wording as written would score "the tube is kinked" as a pass
                    on a kinked tube. */
                (pointsFor(focused) || []).length > 0 && html`
                <div class="criteria-points">
                  <div class="eyebrow">
                    Graded as ${pointsFor(focused).length} pass/fail
                    point${pointsFor(focused).length === 1 ? "" : "s"}
                  </div>
                  <ol>
                    ${pointsFor(focused).map((c) => html`
                      <li key=${c.id} class=${c.defect ? "defect" : ""}>
                        ${c.defect && html`<${Pill} kind="warn">defect<//>`}
                        ${c.statement}
                      </li>
                    `)}
                  </ol>
                </div>
              `}
              ${/* Where the sheet's conditions came from. These sheets carry
                    numeric standards an instructor has to be able to defend, and
                    they are machine-drafted — so the pages behind them and the
                    fact that no SME has signed them off belong next to the text,
                    not in a file the reader has to go and find. */
                focused.criterion_file && html`
                <div class="criterion-basis">
                  <span class="eyebrow">Subtask grading sheet</span>
                  ${(focused.criterion_basis || []).length > 0 && html`
                    <ul>
                      ${focused.criterion_basis.map((b, i) => html`<li key=${i}>${b}</li>`)}
                    </ul>
                  `}
                  <p>
                    <code>${focused.criterion_file}</code>
                    ${!focused.criterion_reviewed &&
                      " — machine-drafted from the procedure sheet and FAA handbook, not reviewed by a subject-matter expert."}
                  </p>
                </div>
              `}
              ${focused.framing && html`
                <div class="framing-note">
                  <span class="eyebrow">Required framing</span>
                  <p>${focused.framing}</p>
                </div>
              `}
              <${CriterionEditor} acs=${acs} target=${focused}
                                  draft=${drafts[focused.target_id]}
                                  onDraft=${onDraft} onSaved=${onSaved} models=${models} />
              <${VariantEditor} acs=${acs} target=${focused}
                                variants=${variantsFor(focused)}
                                onChange=${(id, list) =>
                                  setVariantDrafts((d) => ({ ...d, [id]: list }))}
                                onSaved=${(id, list) => {
                                  setTargets((ts) => ts.map((t) =>
                                    t.target_id === id ? { ...t, variants: list } : t));
                                  setVariantDrafts((d) => {
                                    const next = { ...d }; delete next[id]; return next;
                                  });
                                }} />
            <//>
          ` : html`<div class="empty">Pick a target.</div>`}
        </div>
      </div>

      ${run && html`<${ResultsGrid} run=${run} models=${run.models} restored=${restored} />`}
      ${lightbox && html`<${Lightbox} ...${lightbox} onClose=${() => setLightbox(null)} />`}
    <//>
  `;
}

/* The skill sheet says what to do; the handbook says what the result must
   measure up to, and the numeric limits — twists per inch, strip lengths, wrap
   counts — exist only in the handbook. Reading the procedure without it gives
   half the standard, so the extract goes in front of the procedure rather than
   being filed away under References. */
function ProcedureTab({ sections, procedure }) {
  const [open, setOpen] = useState(true);
  if (!procedure && !sections.length) {
    return html`<div class="empty">No normalized procedure for this task.</div>`;
  }
  return html`
    <${F}>
      ${sections.map((s, i) => html`
        <div class="card handbook-section" key=${i}>
          <div class="handbook-head">
            <h3>
              ${s.handbook}
              ${s.pages.length ? ` — pages ${s.pages.join(", ")}` : ""}
            </h3>
            <div class="handbook-meta">
              ${s.cited_by_source
                ? html`<${Pill} kind="good">cited by the procedure sheet<//>`
                : html`<${Pill} kind="warn">not cited — located by ${s.located_by || "search"}<//>`}
              <a href=${s.file} target="_blank" rel="noreferrer">open extract</a>
              <button class="btn ghost" onClick=${() => setOpen(!open)}>
                ${open ? "Collapse" : "Expand"}
              </button>
            </div>
          </div>
          ${!s.cited_by_source && html`
            <p class="handbook-warn">
              The procedure sheet cites no available handbook section for this task, so
              this range was located during pack compilation. Any standard taken from it
              is provisional until an SME confirms it.
            </p>
          `}
          ${open && html`<${Markdown} text=${s.text} />`}
        </div>
      `)}
      ${procedure
        ? html`<div class="card"><${Markdown} text=${procedure} /></div>`
        : html`<div class="empty">No normalized procedure for this task.</div>`}
    <//>
  `;
}

/* --------------------------------------------------------------- main app */

function TaskView({ acs }) {
  const [detail, setDetail] = useState(null);
  const [tab, setTab] = useState("pack");

  useEffect(() => {
    setDetail(null);
    setTab("pack");
    api(`/api/task/${acs}`).then(setDetail);
  }, [acs]);

  if (!detail) return html`<div class="content empty">Loading ${acs}…</div>`;

  const s = detail.summary;
  const docs = detail.docs || {};
  const tabs = [
    ["pack", "Compiled pack"],
    ["atoms", `Atoms (${(detail.atoms && detail.atoms.counts.total) || 0})`],
    ["photo", "Photo assessment"],
    ["evaluations", `Evaluations (${(detail.evaluations && detail.evaluations.runs.length) || 0})`],
    ["videos", detail.videos.length ? `Videos & segments (${detail.videos.length})` : "Videos & segments"],
    ["frames", "Frames"],
    ["procedure", "Procedure"],
    docs["ANALYSIS.md"] ? ["analysis", "Analysis"] : null,
    detail.references.length ? ["refs", `References (${detail.references.length})`] : null,
    ["raw", "Raw"],
  ].filter(Boolean);

  return html`
    <${F}>
      <div class="main-head">
        <h2>${s.title}</h2>
        <div class="sub">
          ${s.acs_code} · task ${s.task_no} · ${s.subject} block ${s.block}
          · week ${s.week} day ${s.day} · photo fit ${s.photo_fit}
        </div>
        <div class="tabs">
          ${tabs.map(([id, label]) => html`
            <button key=${id} class=${"tab" + (tab === id ? " active" : "")}
                    onClick=${() => setTab(id)}>${label}</button>
          `)}
        </div>
      </div>

      <div class="content">
        ${tab === "pack" && html`<${PackView} pack=${detail.pack} packText=${detail.pack_text}
                                              packError=${detail.pack_error} />`}
        ${tab === "atoms" && html`<${AtomsTab} catalog=${detail.atoms} />`}
        ${tab === "photo" && html`<${PhotoAssessmentTab} acs=${acs} />`}
        ${tab === "evaluations" && html`
          <${EvaluationsTab} evaluations=${detail.evaluations} catalog=${detail.atoms} />
        `}
        ${tab === "videos" && html`<${VideoTab} acs=${acs} videos=${detail.videos} />`}
        ${tab === "frames" && html`<${FramesTab} acs=${acs} videos=${detail.videos} />`}
        ${tab === "procedure" && html`
          <${ProcedureTab} sections=${detail.handbook_sections || []}
                           procedure=${docs["procedure.md"]} />
        `}
        ${tab === "analysis" && html`<${Markdown} text=${docs["ANALYSIS.md"]} />`}
        ${tab === "refs" && html`
          <div class="card">
            <h3>Reference material</h3>
            <ul>
              ${detail.references.map((r) => html`
                <li key=${r.file}><a href=${r.file} target="_blank" rel="noreferrer">${r.name}</a></li>
              `)}
            </ul>
          </div>
        `}
        ${tab === "raw" && html`
          <${F}>
            <div class="card"><h3>tasks.csv row</h3><pre class="raw">${JSON.stringify(detail.row, null, 2)}</pre></div>
            ${detail.sources_json && html`
              <div class="card"><h3>sources.json</h3><pre class="raw">${JSON.stringify(detail.sources_json, null, 2)}</pre></div>`}
            ${detail.steps_json && html`
              <div class="card"><h3>steps.json</h3><pre class="raw">${JSON.stringify(detail.steps_json, null, 2)}</pre></div>`}
            ${detail.pack_text && html`
              <div class="card"><h3>pack.yaml</h3><pre class="raw">${detail.pack_text}</pre></div>`}
          <//>
        `}
      </div>
    <//>
  `;
}

function App() {
  const [tasks, setTasks] = useState([]);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    api("/api/tasks").then((list) => {
      const all = list || [];
      setTasks(all);
      // Open the most informative task first: one that already has segmented video.
      const best =
        all.find((t) => t.segmented_videos > 0) || all.find((t) => t.has_pack) || all[0];
      if (best) setSelected(best.acs_code);
    });
  }, []);

  return html`
    <div class="app">
      <${Sidebar} tasks=${tasks} selected=${selected} onSelect=${setSelected} />
      <main class="main">
        ${selected
          ? html`<${TaskView} acs=${selected} key=${selected} />`
          : html`<div class="content empty">Select a task.</div>`}
      </main>
    </div>
  `;
}

document.getElementById("root").classList.remove("booting");
ReactDOM.createRoot(document.getElementById("root")).render(html`<${App} />`);
