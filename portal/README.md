# AIM Inspector

Data inspection and evals for the Alcor × AIM Fremont pilot: compiled task packs,
the photo-assessment criteria drafted from them, the reference footage those
criteria are graded against, and the perturbation controls that separate a grader
from a model that passes everything.

Implemented from the Claude Design source `AIM Inspector.dc.html`, on the Industry
design system. No build step and no dependencies.

## Run

The screens read a JSON extract under `data/`, built from the `alcor_agents`
working tree. Build it first, then serve:

```bash
python3 scripts/build_portal_data.py    # from the repo root
cd portal && python3 serve.py           # → http://localhost:8080
```

`serve.py` is standard library only and serves the same static files
`python3 -m http.server` would — plus the one write any screen is allowed: the
**Run** buttons on Photo and Video assessment. Those grade through the hosted
arms, whose routes need an API key, and a key in a web page is published, not
used — so the key lives server-side, in `portal/.env` (gitignored, never
deployed):

```
OPENROUTER_API_KEY=sk-or-...   # routes all four arms
GEMINI_API_KEY=...             # or this — routes the two Gemini arms
```

Served statically (or deployed), the buttons stay inert and say why; every
screen still reads.

`build_portal_data.py` walks the same sources `alcor_agents/inspector/server.py`
reads — `tasks/<ACS>/pack.yaml`, `build/criteria/<ACS>.json`, the latest run in
`build/photo_eval/<ACS>/`, and the extracted frames — and writes only what the
portal renders. `--no-images` re-emits the JSON without recopying frames.

`procedure.md` is confidential AIM material and is never copied. The Documentation
tab lists its section names and how many steps, notes, safety points and equipment
items each carries — counts, never the text. Those names come from `steps.json`,
the normalized sheet, not from the pack: the pack drops the front matter and
renames as it compiles, so the two lists differ on most tasks and only one of them
is the sheet.

## Screens

| Screen | Contents |
|---|---|
| Task browser | The eleven pilot tasks as cards — steps, atoms, photo targets, governing handbook pages, and how each pack was compiled. Every pack is machine-drafted and unreviewed; the banner says so, because a passing grade against these criteria tests the pipeline, not a student. |
| Hierarchy | A subtask's steps expanded into checks and error modes, each carrying the source it rests on and whether it is observable from a photograph, a video, a measurement or a document. Beside it: the frame the subtask would be graded on, its sheet, and eval readiness. |
| Photo assessment | The subtask sheet, its excluded non-photo-observable checks, and the perturbed sheet drafted from it. The grid grades each point across four models, with the control row beneath its original. Click any verdict cell for the model's reply. |
| Video assessment | The same criterion, moved onto the clip: a sequence sampled at 0.5 fps over the subtask's span, each frame labelled with its own timestamp. The grid is the design's — the newest `build/video_eval/` run's own arms as columns, each verdict citing the moment that settled it, the segment roll-up beneath, and a cell's reply carrying the frames its call held. Tasks no run has reached show the design's empty state. |
| Videos & frames | Clips with a band timeline per step and the sampled frames beneath. Frame filenames encode their source timestamp (`t000041_50.jpg` = 41.50 s), so a frame is citable back to the video without a lookup table. |
| Documentation | Handbook extract, normalized procedure sheet, compilation inputs with their hashes, and the pack's assumptions and open questions. |
| Evals | Which grader to trust and which tasks produce gradeable evidence. The **Accepted** column is the one that matters — a model passing a criterion on work that contradicts it, where the same model had already shown it can see the condition. |

## What the data is

Real pilot data, not a fixture. Eleven FAA ACS tasks; ten carry a saved photo-eval
run. Across those runs: 1,952 criterion points and 1,228 perturbed controls, graded
by four models over 3,180 calls, at $54.42. Every number on the Evals screen is
computed from them — the drop, the decisive pairs, the flip rate and the accepted
contradictions. The figures move whenever a run is added, so treat any quoted here
as the state of the tree at the last build, not a constant. Points outrun controls
because the later runs re-graded originals without regenerating every perturbed
sheet; the Evals subtitle counts both rather than assuming one is twice the other.

| File | Contents |
|---|---|
| `data/index.json` | Task list with headline counts, model names, totals |
| `data/tasks/<ACS>.json` | Steps, checks, error modes, criteria, and the grid for each subtask |
| `data/evals.json` | The model table and the per-task table |
| `data/frames/` | The frames a run actually graded, full size |
| `data/thumbs/` | Two picks per clip: the Videos tab's 16-frame strip, and the 0.5 fps sequence Video assessment grades on |
| `data/video_runs/` · `data/photo_runs/` | Grids the Run buttons made through `serve.py`, keyed by subtask sheet — kept apart from the built extract so a rebuild does not erase them, and outranking it on screen |

`frames/` and `thumbs/` are gitignored: they are derived from `alcor_agents/build/`,
which is itself untracked. Re-run the build script after cloning.

Where a task has no run, no criteria file, or no source video, the screen says so
rather than filling in. `AM.I.E.S1` and `AM.II.K.S3` were hand-compiled and have no
`build/criteria/` entry: their sheets were built straight from the pack and only the
run records them, so their photo-target count is read off the saved run and marked
`*` on the card. It used to read 0, which was the count of a file that does not
exist rather than of the targets those runs graded.

A subtask the latest run did not cover shows no frame and no criterion rather than
a placeholder — `AM.I.E.S1`'s "Procedure" section and `AM.II.K.S3`'s "Test the
Connector" are both in that state.

### The rail on the assessment screens is one cell per clip

Hierarchy and Videos & frames rail along the subtask. Both assessment screens rail
along the **clip** instead, with that clip's subtasks listed beneath it, because the
clip is what each is asking a question about — Photo assessment about its last frame,
Video assessment about a sequence sampled across it.

With the probe rows gone (below), most tasks are one subtask per clip and the rail is
1:1 either way. It still earns its keep where they are not: `AM.II.K.S3` films "Prepare
the Wire" and "Crimp the Wire" on the same `elect_conn_3`, so that clip is one cell
carrying two rows, and `AM.III.M.S5` has no footage at all, so its three subtasks
collect under a single **No clip recorded** cell rather than three cells with no clip
between them. Nothing becomes unreachable.

Each row states the evidence its screen would send, which is the whole difference
between the two: `last frame · t41.50s · 10 points` on Photo assessment, `whole clip ·
40.00s · 21 frames` on Video assessment.

### Subtasks are the pack's sections, not the run's probes

A run grades more than the pack's sections, and two of those extra targets were being
appended to the rail as subtasks of their own. `step:dl.s1` is one step of
`section:determine-the-line-route`; `section:…#vmsghdgf5` is that same section's
criterion reworded for a match test. Both belong under a section already on screen.

Carried onto the rail they put the same clip up three to six times: AM.I.D.S1 read
**30 subtasks over 7 clips** and AM.I.D.S7 **19 over 4**, while the tasks whose runs
graded sections alone were right all along — AM.II.A.S6 at 8, AM.I.D.S8 at 3. Both now
read 7 and 4.

The reworded probe did a second kind of damage. It carries no `rolls_up_to`, so it
groups under the empty string — and `slug("")` is a substring of every label, so it won
the match for whatever section was tested first. That is why AM.I.D.S7's "Cut The Hose"
showed **1 point on `flex_hose_1`** while its real 7-point `section:cut-the-hose` sat
lower down the rail under the same name on `flex_hose_2`. Filtering at grouping time
rather than at the append fixes the match as well as the count.

`is_probe()` in `scripts/build_portal_data.py` is the single rule, and
`video_eval.subtasks_from()` applies the same one — a clip verdict against a row the
screen does not draw is a verdict nobody can read. Unknown id shapes are kept: dropping
a graded result the Evals table still scores is the worse failure.

## Photo assessment

One still per subtask — the last frame of its span, the state the work ends in —
graded against the compiled criterion, one row per point, one column per model.
Cells read `pass · 0.82` with confidence on, or just the verdict; click a cell
for the model's reply. The subtask roll-up beneath is the pipeline's: one fail
fails, an unsure sends it to review.

Two ways a photo grid gets made:

- **The saved runs** (`build/photo_eval/<ACS>/run_*.json`, via OpenRouter) are
  the runs of record: one call per point, so a failure names the condition that
  failed, and each point rides with its **perturbed control** — the same
  criterion with one stated standard moved so correct work no longer meets it.
  A control a model passes where it passed the original is an *accepted
  contradiction*, and those are what the Evals screen scores. The extract shows
  the newest saved run that graded something.
- **The Run button** (needs `serve.py` + a key) grades this frame live: one call
  per arm, the points graded together, same visible-only discipline. It cannot
  draft or grade a perturbed sheet and says so where the saved grid prints its
  control stats. Live grids persist to `data/photo_runs/<ACS>.json` and outrank
  the saved grid on screen until that file is removed.

What a photograph cannot settle stays out of the sheet: `[measurement]`,
`[document]` and `[video]` checks are listed beside the frame, never folded into
the criterion a model grades.

## Video assessment

The screen grades the *same* compiled criterion Photo assessment grades — the points
are passed through unchanged. Only the evidence moves: instead of one still, the
model is handed the span's sequence sampled at `SAMPLE_FPS` (**0.5 fps**), each
frame labelled with its own timestamp. Moving the evidence and the standard at once
would leave nothing to read a verdict against.

The prompt frames the evidence as what it is: **a video of the procedure being
executed**, and the instruction is to **grade the finished product** — the video
is the evidence for how it got there. A condition the last frame obscures (a
hand, a tool, the angle) may be plainly visible moments earlier, and the verdict
cites that timestamp (`pass · t=14.00`). The goal is the same table Photo
assessment produces — pass / fail / unsure per criterion, per model — with fewer
unsures, because most photo-unsures are a model saying the still does not show
the thing asked about. On AM.I.D.S8 that held: the same 29 points × 4 models
went from 84 unsure on the stills to 11 on the sequences.

The runs of record come from the CLI (`alcor_agents/packs/video_eval.py --all`,
via OpenRouter): **one call per subtask per model**, the points graded together,
because the sheet is a set of conditions about the same article and a grader
that reads them together can use one to place another. The points come from the
latest photo run, so the two tables line up row for row. The Run button (with
`serve.py` + a key) does the same live for one subtask and persists to
`data/video_runs/<ACS>.json`. No perturbed controls ride a video run — a control
probes the grader on the still, and the Photo tab carries it. The on-device
candidates (LFM2) are set aside for grading: they cap at 12 frames per call — a
quarter of a typical span — where the hosted arms take the sequence whole.

That rate is a rate, not a count. The Videos tab's strip is a fixed 16 frames per
clip, which means a different thing on every clip — the 105 s `flareless_fitting_1_rf`
came out at 0.14 fps and the 28 s `flex_hose_1` at 0.57 fps, four times denser for no
reason but length. `sample_picks()` picks by timestamp off the 4 fps extraction, so
the interval between two graded frames is 2.00 s everywhere and cost scales with clip
length, which is the honest behaviour.

A subtask's span is the interval of its clip it is graded over. A pack section takes
the whole clip — the clip was shot for that section, so bounding it against a neighbour
would cut away most of the work — and since the probe rows came off the rail that is
now every subtask on every task: `route_the_line` is graded as one 21-frame sequence
over 0–40.00 s rather than partitioned into three. The screen states "whole clip"
rather than dressing it up as an interval.

The partitioning logic is kept, not deleted, because it is what a *reviewed*
segmentation would feed: where several subtasks genuinely share a clip, each span ends
on that subtask's own graded frame and starts on the previous one's. `spanFor` in
`app.js` and `span_for` in `video_eval.py` implement the same rule, and the exactness
matters — if the runner grades a different span than the screen draws, the tab shows a
verdict beside evidence that did not produce it.

The grid is the design's (`AIM Inspector.dc.html`): the video run's own arms as
columns — not the photo grid's four, which a run made of on-device candidates never
called — a verdict cell citing the moment that settled it (`pass · t=14.00`), the
segment roll-up beneath (one fail fails, unsure → review), and a cell's reply
carrying the frames its call actually held, with the cited moment marked. Where an
arm's per-call frame cap thinned the sequence, a note above the grid says which arm
and by how much — a verdict must be readable against the evidence that produced it,
not the evidence the screen happens to draw.

Three things this screen does not do, and says so on its face:

- **The grid fills only where a run reached it.** `alcor_agents/packs/video_eval.py`
  is the runner; it writes under `build/video_eval/<ACS>/`, and the screen reads the
  newest run there that graded something — on the same rule the photo side uses, so a
  run whose every call failed is skipped rather than replacing a real grid with an
  empty one. A subtask no run has reached shows the design's empty state. The Run
  button is live only under `serve.py` with a key; served statically it is inert
  and says why.
- **No perturbed controls ride a video run.** A control probes the grader's agreement
  on a still; re-running it on the sequence would spend on a question about the photo
  run. The Photo assessment tab carries the perturbed sheet.
- **The `[video]` checks stay excluded.** 83 checks across the eleven tasks are marked
  observable only in motion, and this is the evidence they were held back for.
  Admitting them would change the criterion, and then a clip verdict could no longer
  be read against the photo one. The sheet names the count per subtask instead.

The costing is an order of magnitude, not a quote: it prices every frame at the photo
run's per-call rate, which overstates a sequence that shares one criterion across its
frames. No video run has been costed for real.

## Notes on the port

- **The browser chrome is dropped.** The design mocks the app inside a fake Chrome
  window; here the browser is the browser. The design's `url` value drives the real
  address bar instead — `#/tasks/AM.I.D.S1/assess` — so a screen is linkable and
  survives a reload.
- **Inline styles became classes.** The `.dc.html` writes every style inline because
  it is a single-file template. `styles/inspector.css` carries them as classes; every
  value still resolves to an Industry token.
- **`showConfidence`** was a design-time prop. It is a real toggle in the header,
  and it changes what the verdict cells read: `pass · 0.82` or just `pass`.
- **Drafting a perturbed sheet runs locally.** Where a subtask has no saved run,
  the design's generator is ported as written, including its two refusals — it will not move a handbook page citation
  (an address, not a standard), and it drops a perturbation that needs a scale
  reference in frame rather than keeping one only a measurement could settle.

## Design system

`styles/industry.css` is the Industry system, identical to the copy in `web/`. It is
the source of truth for the look; retune it there rather than overriding it here.
