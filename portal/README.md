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
cd portal && python3 -m http.server 8080
# → http://localhost:8080
```

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
| `data/thumbs/` | An evenly spaced strip per clip for the Videos tab |

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
