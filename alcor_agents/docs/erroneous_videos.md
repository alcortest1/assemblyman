# Synthetic negative examples

`erroneous_video_pipeline` generates deliberately-wrong versions of the AIM task
videos, for use as labelled negative examples when evaluating VLM graders.

For each source clip it produces a variant in which **exactly one** rubric
violation is introduced — an under-bent tube, a safety wire routed the wrong way
— while the scene, camera, bench, tools, technician, lighting, frame rate,
dimensions and all timing outside the edited window are preserved.

> Everything this pipeline writes is synthetic and deliberately incorrect. Each
> clip is labelled `FAIL` and marked `synthetic: true`. None of it is footage of
> real student work and it must not be presented as such.

---

## 1. Why this exists

`docs/evals.md` scores graders on real footage of work done *correctly*. That
measures whether a model agrees when the answer is "pass", but says almost
nothing about the failure that matters — a confident PASS on defective work. Real
negative examples are scarce: students are taught to do the task right, and
filming mistakes deliberately costs bench time and consumables.

Generating them keeps the rest of the frame fixed, so a grader that flips to FAIL
is responding to the defect rather than to a different room.

---

## 2. Install

Everything runs on what the repo venv already has: Python 3.9, `urllib`, and the
`ffmpeg` binary from the `imageio-ffmpeg` wheel. There is nothing to install.

```bash
cp .env.example .env      # then add your OPENROUTER_API_KEY
.venv/bin/python -m unittest discover -s erroneous_video_pipeline/tests -t .
```

`ffprobe` is used when present but is not required; probing falls back to parsing
ffmpeg's banner, the same technique `packs/extract_frames.py` uses.

**Video-to-video only:** `cloudflared`, and only if you enable it — see §6.

---

## 3. Usage

```bash
# What is discoverable, and how each clip binds to a rubric
python -m erroneous_video_pipeline discover

# Which generation models can hold this source, and why the rest were rejected
python -m erroneous_video_pipeline models --video bend_the_line.mp4 --window 8

# Analyse and write error plans. Costs a few cents of analysis; generates nothing.
python -m erroneous_video_pipeline plan --task-code AM.I.D.S1

# Dry run: selects a model, prints the estimate, submits nothing
python -m erroneous_video_pipeline generate --video bend_the_line.mp4

# Real generation. Requires --execute AND a cap.
python -m erroneous_video_pipeline generate --video bend_the_line.mp4 \
    --error wrong_bend_angle --execute --max-cost 20 --confirm

python -m erroneous_video_pipeline generate-all --execute --max-cost 20 --resume
python -m erroneous_video_pipeline qa --video <out.mp4> --plan <error_plan.json>
python -m erroneous_video_pipeline report
```

Flags: `--dry-run` is the default; `--execute` opts in. Also `--limit`,
`--task-code`, `--subtask`, `--error`, `--video-model`, `--analysis-model`,
`--seed`, `--max-cost`, `--resume`, `--confirm`, `--video-reference`.

---

## 4. How a clip is built

1. **Discovery** binds each video to a task code, subtask, procedure section and
   criteria file.
2. **Stage 1** sends a 640px/6fps SDR proxy to a video-capable model with the
   procedure and the criteria, and asks for the scene, the camera, the tools, the
   shortest result-changing edit window, and candidate errors — each of which
   must quote the criterion it violates.
3. **`error_plan.json`** is written before anything is spent.
4. **Stage 2** picks a model from live `/videos/models` capabilities, generates
   only the edit-window segment, and splices it into the untouched remainder.
5. **QA** compares original and generated windows against the plan, procedure and
   criteria, and applies the acceptance gate in §5.
6. Accepted clips append to `manifest.jsonl`; rejections go to
   `failed_generations.jsonl` with reasons, and the next attempt's prompt is
   amended with what went wrong.

### Output layout

```
generated_errors/
  AM.I.D.S1/bend_the_line/wrong_bend_angle/
    bend_the_line__wrong_bend_angle__v01.mp4
    error_plan.json  generation_request.json  generation_response.json
    qa_result.json   metadata.json
  manifest.jsonl  failed_generations.jsonl  cost_report.csv  generation_summary.md
```

---

## 4b. Requested errors, and honest labels

Stage 1 only proposes errors it can tie to a written criterion. Some real
maintenance faults are not covered by the compiled criteria — for `bend_the_line`
the criteria file states that bend angle and bend location "require measurement
or the template in hand and are not graded here". Those can still be requested by
name from the archetype catalogue (`catalog.py`):

```bash
python -m erroneous_video_pipeline generate --video bend_the_line.mp4 \
    --error wrong_bend_angle --execute --max-cost 8 --video-reference
```

What the pipeline will not do is mislabel the result. A plan whose deviation
matches no compiled criterion is recorded as:

- `rubric_grounded: false`
- `label: "UNGRADED_VARIANT"` rather than `"FAIL"`
- `violated_criteria: []`, with the analysis's own wording kept separately in
  `claimed_criterion_violated`
- a `rubric_coverage_note` explaining that it is a stimulus, not a labelled
  negative example

The QA gate drops only its "must be graded FAIL" requirement for these — the
rubric genuinely does pass them — while visibility and preservation still apply
in full.

| Archetype | Applies to |
| --- | --- |
| `wrong_bend_angle`, `bend_at_wrong_mark` | `bend_the_line` |
| `incorrect_flare` | `flare_the_line` |
| `wrong_turn_count`, `safety_wire_loosening_direction` | the `AM.I.E.S1` safety-wire subtasks |
| `excessive_slack` | any |

## 5. The acceptance gate

A candidate is accepted only when **all** hold:

- the intended defect is clearly visible, with confidence ≥ 0.60;
- scene, equipment and camera preservation each ≥ 0.85;
- the rubric result is `FAIL`;
- no additional task defects were introduced;
- the file decodes, is not substantially black, and the window is ≥ 2 s.

A missing score counts as a failure, and the QA model's own `accepted` field is
advisory — the gate is applied in code. The asymmetry is deliberate: rejecting a
good clip costs one regeneration, while accepting a bad one puts a mislabelled
example into a grading set where it silently corrupts every downstream result.

---

## 6. What gets sent where, and the video-reference trade-off

Running this transmits AIM footage to whichever OpenRouter provider serves the
chosen model, under that provider's retention terms. Only proxies and the
edit-window segment are sent, never the full-resolution originals.

The API constrains *how*, and the constraint is asymmetric:

| Reference | Accepts base64 `data:` | Needs a public URL |
| --- | --- | --- |
| `image_url` (first/last frame) | yes | no |
| `video_url` (video-to-video) | **no** — `Only HTTPS URLs are allowed` | **yes** |

So frame-guided generation needs no infrastructure, while true video-to-video
(`runway/aleph-2`) requires the clip to be fetchable over public HTTPS.
`hosting.py` does that with a `cloudflared` quick tunnel: it serves only the
edit-window segment, at a path with 32 random hex characters, and tears the
tunnel down as soon as the job completes. **For the life of that tunnel the clip
is fetchable by anyone with the URL, unauthenticated.** That is a real exposure of
confidential material, which is why `ALLOW_VIDEO_REFERENCE` is off by default and
`--video-reference` must be passed explicitly.

Frame-guided mode avoids the public URL entirely at some cost in fidelity: the
model reconstructs motion from two stills instead of editing the footage in
context.

---

## 7. Cost control

`POST /videos` returns 202 and **has no cancel endpoint** — `DELETE /videos/{id}`
and `POST /videos/{id}/cancel` both 404. A submitted job runs to completion and
bills in full. Everything below follows from that:

- dry run is the default; `--execute` is required to spend;
- `MAX_GENERATION_COST` / `--max-cost` is charged against the *estimate* before
  submission, so the cap cannot be discovered retroactively;
- `--confirm` prints model, defect, window and estimate and waits for `yes`;
- every attempt is appended to `cost_report.csv`;
- unpriceable models (per-token video SKUs) report `None` rather than a guess,
  and must be confirmed.

Indicative prices at 8 s, 4:3: `runway/aleph-2` ≈ $2.24 (video-to-video),
`alibaba/wan-2.7` ≈ $0.80 (first+last frame). Analysis and QA calls are ~$0.003
each.

---

## 8. Known constraints

- **Aspect ratio is the binding filter.** Most AIM clips are 1440×1080 (4:3),
  which rules out Veo, Sora, Kling and Gen-4.5. Eleven of the 21 current models
  can hold 4:3. `form_337.mp4` (2190×1080) is ~12% off any offered ratio and will
  be resampled.
- **The good clips are HLG HDR 10-bit HEVC.** Generated segments are SDR Rec.709,
  so the whole output is tone-mapped to SDR rather than butt-spliced, which means
  prefix and suffix are re-encoded rather than stream-copied. Already-SDR H.264
  sources skip this.
- **Only 10 of 40 clips bind to a rubric automatically.** AM.I.D.S1's seven clips
  are named for their operations; the rest are numbered (`flex_hose_1`) and carry
  no subtask in the name. `discover` suggests a binding where the tokens are
  unambiguous and otherwise asks for `--subtask` rather than guessing — a wrong
  binding would attach an error to the wrong criteria.
- **Prompts are capped at 1000 characters.** `runway/aleph-2` rejects longer ones
  with a 400, and `/videos/models` publishes no field for this, so the limit is a
  constant. Prompts are assembled in priority order — the defect clause first and
  never trimmed, then preservation, then the prohibition list — so a long scene
  description cannot crowd out the thing that makes the clip a negative example.
- **Generation quality is not assumed.** The QA gate exists because current
  models frequently fail to render a specific, physically-precise defect while
  holding a scene fixed. Expect rejections, and read `failed_generations.jsonl`.

### Observed behaviour of `runway/aleph-2` (2026-08-05, `bend_the_line`)

Two paid attempts at `wrong_bend_angle`, $1.40 each:

| Attempt | Scene | Equipment | Camera | Defect visible | Outcome |
| --- | --- | --- | --- | --- | --- |
| 1 | 1.00 | 1.00 | 1.00 | no | *"identical to the original"* |
| 2 (with retry feedback) | 0.60 | 0.30 | 0.80 | no | floating/detached hands, warped tool, tube vanishes |

The lesson is that **scene preservation and edit compliance trade off sharply**.
Left alone the model reproduces the source almost exactly and silently declines
the edit; pushed harder it degrades the subject rather than changing its
geometry. Neither attempt produced a usable negative example.

Practical implications when choosing an error:

- **Local deformations** (a kink, a wrinkle, a split flare) are far more
  renderable than **global geometry** (an angle, a position along the tube).
- A no-op result is the *cheapest* failure mode and the most common — the QA
  gate catches it via `target_error_visible: false` at 1.0 preservation, which
  is otherwise indistinguishable from a perfect clip on preservation scores
  alone. Never accept on preservation without the visibility check.
- Cost estimates run conservative: 12 s at aleph-2's published 28¢/s estimates
  $3.36 but billed $1.40. The guard reserves the estimate, so caps bind earlier
  than actual spend requires.
