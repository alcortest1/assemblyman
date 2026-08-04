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
| AM.I.D.S7 | 7 | 29 | 21 | 11 | 30B 9-16..9-23 |
| AM.I.D.S8 | 3 | 12 | 11 | 7 | 30B 9-5..9-8 |
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
