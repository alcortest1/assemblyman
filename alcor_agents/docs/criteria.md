# Drafted packs and photo criteria

Two of the eleven pilot tasks — AM.I.E.S1 and AM.II.K.S3 — were compiled by hand,
and everything the inspector shows about a task is built on that compilation:
`checks` become correctness atoms, `error_modes` become defect atoms, and both
supply the text a photograph is graded against. The other nine had a procedure
sheet and nothing else, so the Atoms and Photo-assessment tabs rendered empty for
them.

This describes the pipeline that fills that gap, what it produces, and — more
importantly — what its output is and is not allowed to be taken for.

## The pipeline

```
data/processed/tasks.csv          the workbook row: title, subject, photo fit, week/day
tasks/<ACS>/steps.json            sections -> steps -> Senior Mechanic Notes
tasks/<ACS>/procedure.md          the same sheet, normalized for reading
        |
        |  packs/link_handbook.py        find the governing FAA handbook pages
        v
tasks/<ACS>/references/handbook/*.md     verbatim extract + provenance sidecar
        |
        |  packs/compile_pack.py         one model call per step, one per task
        v
tasks/<ACS>/pack.yaml             checks, error modes, evidence  -> atoms
build/criteria/<ACS>.json         the photo criterion + attribution -> criteria
```

Both outputs come from the same pass, because they are two views of one
judgement. The pack is what an SME reviews and what the atoms are built from, so
it stays terse — one sentence per check. The criteria store carries the
attribution trail behind each condition, which is what makes a criterion
defensible to a student but would bury the pack.

## Finding the handbook — `packs/link_handbook.py`

A criterion is only as defensible as the standard behind it, and the numbers —
twists per inch, strip lengths, wrap counts, thread exposure — live in the FAA
handbooks rather than in AIM's skill sheets. Most sheets say which pages to read,
in their "You Need to" section:

> Review pages 9-86 to 9-89 from chapter 9 in the FAA-H-8083-31B.

That is a citation by the campus and carries real authority. Three other cases do
not, and conflating them is the whole point of this step:

| Case | Tasks | Recorded as |
|---|---|---|
| Cites a handbook in `data/` | AM.I.D.S1, AM.I.D.S7, AM.I.D.S8, AM.II.A.S6, AM.II.K.S3, AM.III.F.S11 | `cited_by_source: true`, located by printed page label |
| Cites AC 43.13-1B, which is **not** in `data/` | AM.I.D.S1, AM.III.M.S5 | nearest FAA section by content search, `cited_by_source: false` |
| Cites nothing | AM.I.C.S3, AM.I.C.S5, AM.I.E.S1, AM.I.I.S1 | content search, `cited_by_source: false` |

`cited_by_source: false` is not cosmetic. It changes the drafting prompt to
"treat any standard taken from it as provisional and say so", it forces an
`assumed: true` flag that `pack_lint.py` requires a matching `assumptions:` entry
for, and it drives the amber banner over that section in the Procedure tab.

A task that cites a real handbook *and* an absent AC keeps only the real one. A
second, provisional extract of pages already covered adds noise to every drafting
prompt without adding a standard; the missing AC stays recorded as a note.

Existing references are never overwritten — AM.I.E.S1's was located and
considered during hand compilation, and re-deriving it would replace a judgement
with a search hit. Pass `--force` to redo one deliberately.

## Compiling — `packs/compile_pack.py`

The skeleton is deterministic and involves no model at all: step ids, ordering,
sections, safety, equipment, references and video paths all come from
`steps.json` and `tasks.csv`. Step ids follow the convention AM.II.K.S3
established — initials of the first two significant words of the section, then
the step number, so "Tie a Hitch Along the Bundle" step 2 is `th.s2`. Ids appear
in every verdict, dataset label and eval row, so they have to be readable and to
stay put.

Only judgement is asked of the model, once per step:

- **checks** — what the finished work must satisfy, each marked `photo`, `video`,
  `measurement` or `document`
- **error_modes** — how the step characteristically goes wrong, with a severity
- **criterion** — the photo-gradeable conditions those imply, with the source of
  each

and once per task: the required evidence photos, the task-level criterion, and
the rationale for the campus's photo-fit rating.

### The `observable` field carries the safety weight

A pull test, a continuity reading, a torque value and a tautness judgement are
never `photo`. Marking one `photo` is the failure that matters: it lets a grader
pass work it cannot actually see. `_criterion_for_step()` in `inspector/server.py`
drops everything that is not `photo` before a criterion reaches a grader, and
shows what it set aside beside the frame — because a compound criterion is only as
gradeable as its least gradeable clause, so folding a pull test in alongside a
visual check drags the whole verdict to `unsure` and the visual part never gets
assessed at all.

### Attribution, and over-attribution

Every condition names its source, and the rule is exact: a number may be credited
to the handbook only if that number appears **verbatim** in the extract supplied,
and any handbook attribution must quote the phrase it rests on. Where the
procedure sheet gives a figure and the handbook only speaks qualitatively — "tight
and even", "as taut as possible" — the figure belongs to the procedure sheet
alone.

This is not hypothetical. An early draft of the AM.I.E.S1 criterion credited "6–8
twists per inch" to the FAA handbook, which states no per-inch figure anywhere.
The compiled AM.III.F.S11 pack gets the same distinction right on its own:

```yaml
- statement: Each new hitch is positioned approximately 6 inches from the previous knot.
  observable: measurement
  source: procedure sheet
  note: >-
    Procedure sheet: 'An approximate 6" gap provides adequate support.'
    Handbook gives no spacing figure for intermediate half hitches.
- statement: Bundle being laced is 1 inch in diameter or less, suiting single cord-lacing.
  observable: measurement
  source: both
  note: >-
    Handbook: 'single cord-lacing method and tying tape may be used for wire
    groups of bundles 1 inch in diameter or less'; sheet repeats 1" limit.
```

Where the two sources genuinely disagree, the conflict is recorded in
`conflicts` rather than silently resolved, with the procedure sheet treated as
operative — the student is graded against what the instructor taught.

## What this output is not

**No subject-matter expert has seen any of it.** A drafted pack and a
hand-compiled one both read `status: draft`, so status alone cannot tell them
apart. `provenance:` is what does:

```yaml
provenance:
  generator: packs/compile_pack.py
  model: anthropic/claude-opus-5
  drafted_at: '2026-08-04T03:12:07Z'
  reviewed_by: null
```

`reviewed_by: null` is the field that matters, and the inspector renders a banner
above any pack that carries this block. `pack_lint.py --require-reviewed` refuses
every one of them, which is the gate that keeps a drafted pack out of a live
student session.

A passing grade against a drafted criterion is a test of the pipeline, not an
assessment of a student.

## What the pilot has now

All eleven tasks, after one sweep at about $15 of Opus 5:

| Task | Steps | Correctness | Defect | Photo targets | Handbook |
|---|--:|--:|--:|--:|---|
| AM.I.C.S3 | 4 | 16 | 12 | 8 | 30B 6-3..6-6 *(searched)* |
| AM.I.C.S5 | 7 | 27 | 22 | 11 | 30B 6-3..6-6 *(searched)* |
| AM.I.D.S1 | 23 | 95 | 73 | 27 | 30B 9-1..9-7 |
| AM.I.D.S7 | 14 | 56 | 45 | 20 | 30B 9-16..9-23 *(+drafted steps)* |
| AM.I.D.S8 | 11 | 44 | 37 | 14 | 30B 9-5..9-8 *(+drafted steps)* |
| AM.I.E.S1 | 13 | 27 | 23 | 177 | 30B 7-77..7-80 *(hand-compiled)* |
| AM.I.I.S1 | 10 | 41 | 31 | 14 | 30B 2-14..2-17 *(searched)* |
| AM.II.A.S6 | 32 | 136 | 105 | 37 | 31B 4-85..4-96 |
| AM.II.K.S3 | 19 | 27 | 26 | 131 | 31B 9-92..9-94 *(hand-compiled)* |
| AM.III.F.S11 | 15 | 62 | 51 | 19 | 31B 9-86..9-89 |
| AM.III.M.S5 | 9 | 38 | 32 | 13 | 32B 10-44..10-47 *(searched)* |

1,175 atoms and 455 photo targets, against 113 atoms and 308 targets across two
tasks before. AM.I.E.S1 and AM.II.K.S3 keep every target id they had; their
counts rise only by the handful of steps no reviewed segment covered.

AM.III.M.S5 is the one task with no source video — "not AIM developed" in the
workbook — so it has criteria and no frames at all, permanently. That is the case
that settles the design: a criterion cannot depend on a photograph existing.

*(+drafted steps)* marks a task whose AIM skill sheet stops before the work its
own title describes. AM.I.D.S8's sheet ends at "Deburr the tubing ends" and
AM.I.D.S7's at "Verify the cuts", yet both sheets list equipment — flareless
sleeve, B-nut and mandrel; MS fittings and hydraulic fluid — that no documented
step ever touches. The missing operations are drafted in
`tasks/<ACS>/steps_supplement.json` from the handbook pages the sheet itself
cites, cross-checked against the reference video, and every step from that file
carries `origin: drafted` and `assumed: true` with an entry in `assumptions:`.
The supplement is a separate file because `packs/ingest.py` rewrites `steps.json`
from the `.docx` on every run, so a step added there would not survive the next
ingest. These steps are proposals about scope, not campus standards, and the
packs stay `draft` until AIM confirms them.

## Running it

```bash
./.venv/bin/python packs/link_handbook.py --dry-run   # report citations, write nothing
./.venv/bin/python packs/link_handbook.py             # extract for every task

./.venv/bin/python packs/compile_pack.py AM.III.F.S11 --dry-run   # skeleton, no calls
./.venv/bin/python packs/compile_pack.py AM.III.F.S11             # one task
./.venv/bin/python packs/compile_pack.py --all                    # every task lacking a pack

./.venv/bin/python packs/pack_lint.py                 # gate: schema, refs, assumptions
```

Roughly one model call per procedure step plus one per task; the eleven pilot
tasks come to about 130 calls. `--force` overwrites an existing pack, which is
how a re-draft on a different model is done — and is why the two hand-compiled
packs are skipped unless it is passed.

## Grading against them

`build/criteria/<ACS>.json` becomes the default criterion for a step target in
the inspector's Photo-assessment tab, in preference to the pack's one-line
checks, because it carries the numeric standards those checks summarise away.
Edits made in the browser still override it and still save to
`build/photo_eval/<ACS>/prompts.json`; *Reset to compiled text* restores the
draft.

### One row per subtask, and the invariant that keeps it honest

`criteria/<ACS>/` holds one sheet per subtask, written about the finished subtask
rather than assembled from the steps leading to it — which is the question a
subtask target exists to ask. Each sheet becomes exactly one row under its task
roll-up, so AM.I.D.S1 lists its seven (`route_the_line`, `cut_the_line`,
`deburr_the_line`, `bend_the_line`, `flare_the_line`, `test_fluid_line`,
`install_fluid_line`) as seven separately gradeable rows.

Targets were built from pack sections and the sheet looked up afterwards, which
silently dropped any sheet whose section was absent, merged away, or fully
segmented — seven of thirty-seven, across three tasks and for three unrelated
reasons:

| Task | Lost | Because |
|---|---|---|
| AM.I.E.S1 | all 3 | hand-compiled before sections existed, so every step carries `section: null` and no subtask target was built at all |
| AM.II.K.S3 | 3 of 5 | a section whose every step a segment covered was skipped, retiring the subtask along with it |
| AM.II.A.S6 | the doubler | "Create the Patch Doubler" was folded in as a note-only heading, leaving its sheet no section to join to |

Subtasks are now built from the union of pack sections and sheets, and a section
covered by segments keeps its sheet — a reviewed segment grades one interval
*inside* the work, the sheet grades the finished subtask those intervals add up
to. The test that guards this asserts the direction that actually catches things:
every **sheet** reaches exactly one target. Asserting every *target* found a
sheet passes trivially while a sheet is never reached, which is how all seven
stayed invisible under a green suite.

A sheet-only subtask is matched to a clip by the same title overlap a pack
section uses, which is what puts AM.I.E.S1's turnbuckle sheet on
`insert_wire_for_double_wrap_turnbuckle_safety` rather than on one of the four
pliers clips. Where the clips are a same-prefix numbered series the names carry
no signal to match against, so it gets no clip at all: the doubler hitting
`flush_patch_1` on the word *patch* would be an accident of vocabulary, and
grading a doubler against footage of damage being identified is worse than
offering no frame. It is still listed, still carries its criterion, and takes a
frame from the picker.

### Graded a point at a time, reported a subtask at a time

A sheet is not one question. `sheet_checks()` splits it into its numbered
criteria and its critical defects and each becomes its own model call, so a
failure names the condition that failed rather than reporting that the subtask,
as a whole, did. Defects are restated as absences before they are graded: taken
as written, "Tube is kinked or collapsed flat at the bend" scores a `pass` on a
kinked tube, which is not merely wrong but backwards, and reads as a clean
result.

**A step criterion splits too**, and for a while it did not. A sheet carries
`Criteria` and `Critical defects` headings; a step criterion is a bare list of
`- ` bullets, which is what `compile_pack.py` drafts and what
`_criterion_for_step` assembles. Only the sheet shape was recognised, so every
step fell through to a single call carrying all its conditions at once — and
`apply_thresholds` fails a criterion on one failed condition and abstains on one
unobservable one. A four-condition step could therefore only pass when all four
cleared, which across every saved run happened **7 times in 753 calls**.

That also quietly invalidated the reasoning behind `_criterion_for_step`
including `measurement` and `document` checks: it argued each check gets its own
call, so a measurement "comes back `unsure` on its own line and takes nothing
else with it". True of sheets, false of steps — those checks dragged whole steps
to `unsure`. Splitting the bullet shape is what makes the argument true.

Reviewed intervals split on the same rule, since their criterion is a pack-checks
bullet list. The cost is real: a full run of AM.I.E.S1 goes from 174 calls to
270. Projected against the saved replies, point-level results go from 1% `pass`
to 29%, and the step roll-up from 1% to 7%.

A single condition is deliberately *not* split. Splitting it would relabel the
target as a roll-up of one, and the call is identical either way.

The grid reports the other way round. Sixty-two points across seven subtasks is
sixty-two rows of near-identical text, so the points are regrouped into one row
per subtask and one cell per model, each cell listing its own points and their
verdicts under the roll-up: one `fail` fails the subtask, and a point the photo
could not settle leaves it for `review` rather than being rounded up to a pass.
That is the rule `handle_photo_run` applies, and the cell applies the same one so
the two cannot drift.

A final row totals every point, per model — how many came back `pass`, `fail`
and `unsure`, and how many were correct. Correct is the labelled verdict where a
variant or control states one, and `pass` everywhere else, because every other
point is graded against a reference frame: footage of work an instructor
accepted. That inference is the row's whole caveat and is stated beneath it. A
model that passes everything scores like one that grades, which is what the match
test exists to separate.

### How sure a grader must be, and what that never buys

`DEFAULT_PASS_THRESHOLD` is **0.60**, set from the replies rather than from
principle. The graders reach 0.95 on 40% of the observable conditions of a
subtask sheet but only 22% of a step's, and on hard footage never: across
AM.I.D.S1's 23 steps, **zero of 93 conditions cleared 0.95**, so no step could
pass whatever its workmanship. Their affirmative answers cluster at 0.70–0.90.
What they express there is ordinary reading of a photograph, not the
near-certainty 0.95 demands, and holding out for near-certainty converted every
ordinary yes into an abstention.

Lowering it does not weaken what protects a bad crimp. `apply_thresholds` keeps
both rules that carry the safety weight, at any threshold: **a condition the
photo cannot show never passes**, and **one failed condition fails the whole
criterion**. What changes is only how sure a grader must be about something it
can actually see. Re-thresholding every saved run from 0.95 to 0.60 moves
1003 `fail` verdicts not at all — it converts 306 abstentions into passes.

Thresholds live in `apply_thresholds` rather than in the prompt precisely so they
can be retuned against a saved run without spending another call, which is how
the figure above was chosen.

### An interval with no pack step has no criterion

Where a reviewed interval resolves to no pack step, there is no acceptance
standard for the work in it. There *is* a reviewer's description of the footage,
and that used to become the criterion — which is not a weaker criterion but a
different kind of thing: prose about one moment of a clip, graded against a
photograph of another. Ninety targets across AM.I.E.S1 and AM.II.K.S3 were built
this way, and they returned `fail` on **39% of 362 calls**, the highest rate of
any criterion source and none of it about workmanship. It is also the
circularity `DRAFT_PROMPT` exists to prevent — a model describing what it sees
and that description becoming the standard — arriving by a door that prompt does
not cover.

Those intervals are still listed and still carry their description, which is what
an author writes a criterion *from*. They are flagged `needs_criteria`, so the
tab shows them and the run skips them, exactly as it already did for a
clip-derived subtask with no sheet.

### Which frame a criterion is tried against

Segmentation is what pins a frame to a step, and only two tasks have been through
it. For the rest, a frame is *suggested* by laying the work out along its clip:

1. Each pack section is matched to a clip by name overlap — "Cut the Tubing"
   against `cut_the_line`, "Bending the Tubing" against `bend_the_line`. Ties
   break in sorted order so the choice cannot move between server restarts. A
   task with a single clip uses it regardless of the name, since there is nothing
   to choose between — that is what makes the tasks whose only section is called
   "Procedure" runnable at all.
2. Sections sharing a clip take **successive slices** of it, in procedure order,
   rather than each spreading over the whole thing.
3. Steps subdivide their section's slice, so step *i* of *n* lands at its own
   boundary and the last step of the last section ends on the clip's final frame.

The rule this enforces is that **only the work that ends where a frame was taken
may be graded against it.** Giving all three steps of "Cut the Tubing" the clip's
last frame graded "Decide the size of tubing to use" against a photo of tubing
already cut — a confident `fail` on a step the student performed correctly. That
is worse than having no frame, because a wrong verdict is harder to notice than a
missing one. On AM.I.D.S1 this collapsed 23 steps onto 7 frames; they now get 23.

The whole-task roll-up takes the final frame of the last section that maps to a
clip, since that is the closest thing an unsegmented task has to a photograph of
finished work. Without it the single target the pilot most cares about — is the
finished work correct — was the one target that could never run.

Every suggested frame is marked `frame_suggested`, and the UI states what the
guess rested on ("section 2 of 2 on this clip, step 3 of 4 within it, 88% of the
way through") so it is never mistaken for a reviewed interval. The picker
overrides it on any target.

Where a subtask *has* been segmented, none of the above applies: the interval
covering its last step names both the clip and the frame the work ended on, and
that is evidence rather than an even-pace guess, so it wins. Those targets are
marked `frame_reviewed` instead. This also settles the clip, which matters more
than the frame — name overlap put "Insert the Pin into the Electrical Connector"
on `elect_conn_2` on the words *electrical* and *connector*, but the work was
filmed in `elect_conn_5`, and a subtask whose frame comes from one clip while its
clip says another is filed under the wrong roll-up.

### A step is graded on several frames of its own span

Everything above chooses **one** frame, and for a subtask that is the right
number: it is graded against the finished article, and moments of it in progress
are not evidence about that. A step is not a finished article. It is a slice of
work by construction, its frame is a guess at the instant the slice ended, and
the guess lands mid-action often enough to dominate the results — across every
saved run, **81% of the conditions inside a step call came back unobservable**,
objecting to a hand, a tool or a fixture rather than to the work.

So a step-level target carries `frames_per_step` frames of its own span instead,
sampled by `sample_frames()`:

| k | frames |
|---|---|
| 1 | the frame the step ends on — identical to the single-frame behaviour |
| 2 | start and end |
| 3 | start, middle, end |
| 4 | 0%, 33%, 66%, 100% |

Both ends are always included and the window is deduplicated, so a step shorter
than *k* frames sends what it has rather than paying for the same image twice.
The span itself comes from whichever source the target has: a reviewed interval
states its `frame_start` and `frame_end`, and a pack step occupies the slice
between the previous step's boundary and its own. `frame` remains the last of
them, so every saved run stays comparable — `frames_per_step=1` moves no frame
that already existed, and there is a test that asserts exactly that across three
tasks.

One further frame is added at run time: the one a model picks as the clearest
view of the state the work ends in, searched over the step's own span. It is an
**addition, never a substitution** — the sampled frames are what establish what
that end state is, and a picked frame that flattered the work would otherwise be
the only evidence of it. Where no frame shows finished work the picker says so
and the sample stands, because presenting a mid-action image as the clearest view
of a result is worse than presenting no such view at all. It costs one call per
target, shared by every model grading it.

The wording matters as much as the frames. A task-level submission is several
photographs of **different subjects**, and any one of them may satisfy a
condition. Frames of one step are the opposite: one piece of work photographed
repeatedly while it was being made. Graded under the any-photo rule they would
pass a wire that was seated at the halfway mark and pulled loose by the end — the
pass rate would rise, and rise for the wrong reason. `sequence_note()` says
instead that the verdict is about the state at the **end** of the step, that
earlier frames exist only to see past what occludes it there, and that a
condition the final frame shows unmet is never credited from an earlier one.

Cost is linear in frames but not proportional to them: the prompt and the reply
are paid for once however many are attached, so three frames land near twice the
price of one rather than three times it.

### It is still not an assessment photo

All of the above only chooses among frames of a reference video, and
`inspector/README.md` already records what those are worth: **the final frame of
a reviewed sub-subtask is not a substitute for a deliberately taken assessment
photo.** Grading AM.III.F.S11's compiled criteria against `wire_lacing_1`
reproduced it precisely — Opus 5 answered `unsure` on almost every condition and
said why: *"In-progress photo: hands tying a dark lacing cord around a white wire
bundle"*, asking for *"completed lacing photographed unobstructed with a ruler in
frame"*. On AM.I.D.S1 the objections are occlusion — *"cut area occluded"*,
*"flared end concealed inside the tool"* — which is what a head-mounted camera
does during the action.

That is the grader working, not failing; in the same runs the mismatch controls
came back `fail`. The criterion is the deliverable here; the photograph that will
satisfy it is one a student has yet to take.
