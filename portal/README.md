# AIM Inspector

Data inspection and evals for the Alcor × AIM Fremont pilot: compiled task packs,
the photo-assessment criteria drafted from them, the reference footage those
criteria are graded against, and the perturbation controls that separate a grader
from a model that passes everything.

Implemented from the Claude Design source `AIM Inspector.dc.html`, on the Industry
design system. No build step and no dependencies.

## Run

Any static file server works:

```bash
cd portal && python3 -m http.server 8080
# → http://localhost:8080
```

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

The task set is the design's seeded pilot data: eleven FAA ACS tasks, with full
step-level detail populated for `AM.I.D.S1` (Route the line, Cut the line) and
saved grading runs for those two subtasks. Everything else carries the structure
without the leaf detail, exactly as the prototype does.

To put this on the working tree, replace `DATA`/`TASKS` in `app.js` with calls to
the live inspector API in `alcor_agents/inspector/server.py`, which already serves
packs, criteria, frames and saved photo-eval runs from disk.

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
- **Drafting a perturbed sheet runs locally.** The design's generator is ported as
  written, including its two refusals — it will not move a handbook page citation
  (an address, not a standard), and it drops a perturbation that needs a scale
  reference in frame rather than keeping one only a measurement could settle.

## Design system

`styles/industry.css` is the Industry system, identical to the copy in `web/`. It is
the source of truth for the look; retune it there rather than overriding it here.
