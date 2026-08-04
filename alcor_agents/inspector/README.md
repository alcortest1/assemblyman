# Task Pack Inspector

A local, read-only web app for looking at everything compiled for a task in one
place: the pack, the normalized procedure, the reference videos, the sampled
frames, and the sub-subtask segmentation produced by frame-by-frame review.

```bash
cd alcor_agents
./.venv/bin/python inspector/server.py        # http://127.0.0.1:8765
```

It reads straight from the working tree, so it is safe to leave running while
packs are being recompiled — reload the page to pick up changes.

Every tab but one is read-only. **Photo assessment** is the exception: it writes
edited criteria and completed grading runs under `build/photo_eval/`, and it is
the only part that makes outbound network calls. Nothing else is ever written.

## What each tab shows

| Tab | Contents |
|---|---|
| Compiled pack | `tasks/<ACS>/pack.yaml` rendered as steps → checks → error modes, per variant, with severity colouring. Assumptions and open questions are surfaced rather than buried. A pack carrying `provenance:` was machine-drafted and gets a banner saying so. |
| Atoms | A derived task → variant → subtask → atom hierarchy. Activity atoms come from reviewed segment labels; correctness atoms come from checks; defect atoms come from error modes. Each atom shows reference intervals, labeled-evaluation readiness, and — where one was drafted — the photo criterion for its subtask with the source behind each condition. |
| Photo assessment | Put a VLM to the actual pilot question: does this photo of finished work pass or fail against a written criterion? Grades a frame of any step, subtask or clip against text you can edit, across Opus 5, Gemini 3.6 Flash and GPT-5.6, side by side. See below. |
| Procedure | The governing FAA handbook extract **followed by** `tasks/<ACS>/procedure.md`. Both, in that order, because the skill sheet says what to do and the handbook says what the result must measure up to — and the numeric limits exist only in the latter. A section the campus never cited is marked provisional. |
| Evaluations | Dataset readiness and side-by-side agent-system precision, recall, defect recall, coverage, and false passes at task and atom level. Reads `evals/datasets/*.json` and `build/evals/runs/*.json`; schemas are documented in `docs/evals.md`. |
| Videos & segments | The clip with a segment timeline underneath. Each band is one sub-subtask, coloured by the procedure step it belongs to. Click a band to seek the video and open that sub-subtask's frame subsequence. |
| Frames | Every sampled frame for a clip, at either resolution. Click to enlarge. |
| Analysis | `tasks/<ACS>/ANALYSIS.md`, the reasoning behind the pack, when present. |
| References | Extracted handbook sections and other reference files. |
| Raw | The `tasks.csv` row, `sources.json`, `steps.json` and the raw `pack.yaml`. |

The sidebar shows every task in `data/processed/tasks.csv` with badges for pack
status, video count, extracted frame count, and how many clips have been
segmented — so it doubles as a progress view over the compilation work.

## Where the data comes from

```
data/videos/<ACS>/*.mp4                       source clips
build/index/<ACS>/<clip>/t*.jpg               4 fps @ 480px — boundary discovery
build/frames/<ACS>/<clip>/t*.jpg              4 fps @ 960px — detail review
build/analysis/<ACS>/<clip>.segments.json     sub-subtask boundaries
tasks/<ACS>/procedure.md, steps.json          the AIM skill sheet, normalized
tasks/<ACS>/references/handbook/*.md          the FAA pages that govern the task
tasks/<ACS>/pack.yaml                         compiled pack
build/criteria/<ACS>.json                     drafted photo criteria + attribution
```

Two of the packs were compiled by hand; the rest are drafted from the procedure
sheet and handbook and carry a `provenance:` block saying so. `docs/criteria.md`
describes that pipeline and the attribution rules it enforces:

```bash
./.venv/bin/python packs/link_handbook.py       # locate each task's handbook pages
./.venv/bin/python packs/compile_pack.py --all  # draft packs + criteria
./.venv/bin/python packs/pack_lint.py           # schema, references, assumptions
```

Frame filenames encode their source timestamp (`t000012_25.jpg` = 12.25 s), so a
frame is citable back to the video without a lookup table, and a sub-subtask's
frame subsequence is a filename filter rather than a separate copy of the data.

Regenerate frames with:

```bash
./.venv/bin/python packs/extract_frames.py probe data/videos/<ACS>
./.venv/bin/python packs/extract_frames.py sample <clip.mp4> build/frames/<ACS>/<clip> --fps 4 --width 960
```

## Photo assessment

Needs an OpenRouter key, in the environment or in the gitignored `alcor_agents/.env`:

```bash
export OPENROUTER_API_KEY=sk-or-v1-...
```

Without one the tab still renders — targets, frames and criteria all work — and
only the Run button is disabled.

**Targets** come from two sources, in order of precision.

Every reviewed sub-subtask becomes a candidate pointing at its `frame_end`: the
last frame of an interval is the completed state, which is what a student would
actually photograph. Each clip also gets a task-level target. This is the richest
evidence, and its target ids are the keys that saved criteria and past runs
already use, so they are produced unchanged.

But segmentation is a separate frame-by-frame review pass and exists for two of
the eleven tasks, so deriving targets only from segments left the other nine with
an empty tab even once their packs existed. **Any pack step no segment covers now
becomes a target in its own right**, along with the pack's required-evidence
photos and a task roll-up. These start with no frame attached; pick one from the
extracted clips, or leave it — a target without a photo is still worth having,
because the criterion is the deliverable and the photo it will grade is usually
one a student has yet to take. AM.III.M.S5 has no source video at all, and its
criteria exist regardless.

Where `build/criteria/<ACS>.json` has a drafted criterion for a step, that is the
default rather than the pack's one-line checks, because it carries the numeric
standards those checks summarise away, and each of its conditions names the
source it rests on. See `docs/criteria.md`.

**Criteria are editable, and that is the experiment.** The same photo graded
against differently worded criteria gives different verdicts, so finding wording
a model reads the way an instructor does is the point. Text is derived from the
pack — a step's `checks` where they exist, since those are already written as
acceptance criteria — and the tab says plainly when it fell back to a reviewer's
description instead, because a description of what happened is not an acceptance
condition. Edits save to `build/photo_eval/<ACS>/prompts.json`; the pack text
stays recoverable via *Reset to pack text*.

**Drafting a criterion — procedure sheet plus handbook.** *Draft criterion from
procedure + photo* proposes an acceptance criterion from the sources rather than
from the frame. It is sent the whole normalized `procedure.md` (Step Instructions
*and* Senior Mechanic Notes, where most acceptance detail lives) together with
every extracted handbook page the pack cites. Both are needed: the skill sheet
says what to do, the handbook says what the result must measure up to, and
numeric limits usually exist in only one of them.

Where a pack cites no handbook — nine of the eleven pilot tasks have no compiled
pack at all — the relevant pages are located by content search over
`packs/handbook_search.py`, scoped to the handbook for that task's subject
(General → 30B, Airframe → 31B, Powerplant → 32B). Scoping matters: "safety wire"
appears in all three, and unscoped search put an Airframe vernier-scale page
above the actual safety-wire section. Searched pages are labelled as located by
search and never reviewed, so anything drawn from them is provisional.

Search runs against a cached text index built once with
`python packs/handbook_search.py --build` (~2.5 min, 2.3 MB for all three
handbooks). Without it a query re-parses 400 MB of PDF and takes tens of seconds.

The photo is supplied only so the model can judge what a camera at that distance
can resolve. Drafting a criterion from the image it will grade is circular — left
alone a model will describe what it sees and call that the standard — so the
prompt forbids writing the photo's contents into the criterion and asks
separately what the photo *cannot* support. In practice it does say so: on the
AM.II.K.S3 crimp frame it drafted eight conditions and then stated plainly that
this photo could not support any of them.

Every condition is attributed. A criterion an instructor cannot trace back to a
source is one they cannot defend to a student, and over-attribution is the
failure mode to watch: an early draft credited "6–8 twists per inch" to the FAA
handbook, which never states a per-inch figure at all — it says only "tight and
even". A number may now be credited to the handbook only if it appears verbatim
in the supplied text, and any handbook attribution must quote the phrase it rests
on. Where a pack's handbook link is `cited_by_source: false` — AM.I.E.S1's is,
having been located during compilation rather than cited by AIM — anything drawn
from it is labelled provisional.

Drafting also surfaces source conflicts. On AM.I.E.S1 it found that AIM's sheet
says to cut the pigtail "leaving 6-8 wraps" while FAA-H-8083-30B 7-80 specifies
"1⁄4 to 1⁄2 inch (three to six twists)". Both readings were confirmed against the
documents. That is an open question for AIM, not something a grader should quietly
pick a side on.

**Only photo-observable checks are graded.** The packs mark each check
`observable: photo | video | measurement`, and only the `photo` ones go into the
criterion. This matters more than it sounds: a compound criterion is only as
gradeable as its least gradeable clause, so folding a pull test in alongside a
visual check drags the whole verdict to `unsure` and the visual part never gets
assessed. Excluded checks are shown beside the frame rather than hidden, so it
stays obvious what a photo is *not* covering.

**Match test.** Reword a criterion so the photo should no longer satisfy it,
mark the verdict you expect, and run it against the same frame beside the
original. This is what distinguishes a model that reads the criterion from one
that agrees with whatever it is handed. Expectations are `pass`, `fail`,
`unsure`, or `not_pass`.

`not_pass` deserves its own note. The rubric tells a grader to answer `fail`
only when the photo positively shows the criterion violated, and `unsure` when
the subject simply is not depicted. A criterion belonging to a different task is
the second case, so scoring a mismatch control as "must say fail" marks correct
behaviour wrong. `not_pass` accepts either — what matters for a control is that
the model did not pass it.

**Mismatch controls.** Every AIM reference frame is correct work. A run where
all three models pass everything is therefore indistinguishable from three
models that always say pass, which is the one-class problem `docs/evals.md`
describes. So a run can include N deliberately mispaired items — this frame
against a *different* subtask's criterion — where the correct answer is `fail`.
That yields a negative class today without waiting on AIM to record bad work.
It is a floor rather than a ceiling: a mispaired criterion is usually obviously
wrong, so passing the control proves a model is grading at all, not that it
would catch a subtly bad crimp. Real labelled negatives are still needed.

The grader is instructed to answer `unsure` rather than guess, and specifically
to abstain when a criterion needs a measurement and no scale reference is in
frame. Abstentions reduce coverage in `docs/evals.md` rather than counting as
correct, so nothing is gained by hiding behind them.

### First result: sampled video frames are not assessment photos

The first real run on AM.II.K.S3 (24 calls, Opus 5 / Gemini 3.6 Flash /
GPT-5.6 Terra, saved under `build/photo_eval/AM.II.K.S3/`) returned **`unsure`
on every single call** against real pack criteria — all three models, every
frame.

That is not the models failing and not the prompt being too strict. A control on
the same frame settles it: given "a circular electrical connector is held in a
person's hand with its face toward the camera" all three answer `pass`; given
"the workpiece is a metal propeller blade in a bench vise" all three answer
`fail`. Six for six. The rubric produces decisive verdicts when the photo
supports one.

What they will not do is judge *acceptance criteria* from these frames, and
their reasons are consistent and specific: the work is small in a wide
head-mounted POV frame, and the operator's own fingers cover it. On the best
connector-face still in the whole task, Opus 5's objection is that resolving a
pin face against the insert face needs an oblique or profile view at macro
scale — a head-on 960px frame cannot show flushness at all, at any sharpness.

Two consequences for the pilot:

- A **final frame of a reviewed sub-subtask is not a substitute for a
  deliberately taken assessment photo.** These frames are excellent for
  segmentation review and as reference exemplars, which is what they were
  extracted for. They are not the artifact a student would submit.
- The capture instruction is the variable to test next, not the model. What AIM
  needs to specify is framing: close, unobstructed, oblique where flushness
  matters, with a scale reference where a dimension matters. `evidence.required`
  in each pack is where that belongs.

Worth re-running against genuinely photographed work before drawing any
conclusion about which model grades best. On this evidence the three are
indistinguishable, because none of them was given a gradeable photo.

### Confirmed on a second task, with drafted criteria

The same thing happened on AM.III.F.S11 the first time its compiled criteria were
graded (31 calls, Opus 5, against the last frame of `wire_lacing_1`): `unsure` on
almost every condition. The reason it gave is more specific than "too small" and
worth recording — it identified the frame as *unfinished work*:

> In-progress photo: hands tying a dark lacing cord around a white wire bundle
> clamped to a wooden board.

and asked for "completed lacing photographed unobstructed with a ruler in frame
showing bundle diameter, hitch spacing, knot detail, and trimmed tails".

Two things follow. The last frame of a clip is not reliably the *completed* state
— it is wherever the camera stopped, which for an unsegmented task is often
mid-action. And the grader is discriminating rather than rubber-stamping: in the
same run the mismatch control came back `fail`, so the abstentions are a judgement
about the evidence, not a default.

## Implementation notes

No build step and no bundler — React, ReactDOM and htm are vendored under
`vendor/`, and `static/app.js` is plain JavaScript using htm tagged templates
rather than JSX. That keeps the app verifiable without a Node toolchain: it can
be parsed and server-rendered under JavaScriptCore for a smoke test.

The server is Python standard library only, apart from PyYAML for parsing packs.
It implements HTTP range requests, without which video scrubbing silently fails.
Path traversal is refused: everything served must resolve inside `alcor_agents`.
