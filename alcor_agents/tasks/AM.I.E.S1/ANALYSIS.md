# AM.I.E.S1 — task analysis

Working notes behind `pack.yaml`. Written from the three AIM procedure documents
([`procedure.md`](procedure.md)), the FAA AMT General Handbook Chapter 7 extract
([`references/handbook/safety-wire.md`](references/handbook/safety-wire.md)), and
frame-by-frame review of the six reference videos.

The purpose of this file is to record *why* the pack is shaped the way it is —
particularly the things that make this task harder to verify than it first looks.

---

## 1. This ACS code is three procedures, not one

`tasks.csv` lists one task. The source documents are three distinct procedures
with different step counts, different tools, and different acceptance criteria:

| Variant | Procedure | Steps | Primary tool |
|---|---|---|---|
| A | Wire safety on bolts, with safety wire pliers | 5 | Safety wire pliers (twist-knob type) |
| B | Wire safety on bolts, by hand | 4 | Bare hands + duck bill and needle nose pliers |
| C | Wire safety on a turnbuckle, by hand | 4 | Bare hands + duck bill pliers |

They are not interchangeable. A and B produce the same artifact by different
means; C produces a completely different artifact judged against different rules
(4 turns per wire, streamlined silhouette, ≤3 threads exposed) and is the only
variant the FAA handbook gives a wire-gauge table for.

**Design consequence.** A session cannot verify anything until it knows which
variant is being performed, and the step numbering differs between them (step 3
is "Perform wire wraps" in A but "Pigtail the wire" in B). The pack therefore
keys steps by `variant` + `step_id`, never by step number alone.

**Variant detection is feasible from the first frames.** The discriminators are
large and unambiguous — safety wire pliers in hand vs. bare hands, and a
turnbuckle barrel vs. a pair of drilled-head bolts in the workpiece. This should
be a confirmation ("Looks like the pliers method on bolts — right?") rather than
a silent inference, because getting it wrong silently means every subsequent
verdict is measured against the wrong standard.

---

## 2. The verifiability problem — the core insight for this task

The inspection step reads like a checklist of five checks. Four of them are
**tactile**, and a camera cannot perform them:

> Poke a piece of safety wire between the bolt head and the wire that wraps
> around it, if it slides between, the safety is too loose. Tug up on the wire
> going around the bolt head to ensure it doesn't go over the bolt head. Measure
> an inch of wraps and verify there are 6-8 wraps. Strum the safety for tautness,
> if it can move it has too many wraps. Ensure the pigtail has defined wraps and
> is not a sharp hazard.

Tautness, slip-fit clearance, and snag hazard are all *force* judgements. A
system that reports "the safety wire is properly tensioned" from a photograph is
fabricating. This is the failure mode most likely to make a maintenance
instructor distrust the whole product, and it is not fixed by a better model.

The pack therefore classifies every check by what evidence could possibly settle
it:

| `verifiability` | Meaning | Evidence | Example |
|---|---|---|---|
| `state` | A property of the finished work, visible in a still | photo or frame | pigtail is bent back toward the bolt head |
| `action` | Evidence the operator *performed* a required action | video only — a still cannot show it | the poke test was carried out |
| `attested` | Physical property requiring touch or force | operator's spoken answer, recorded as their claim | the safety is taut when strummed |
| `unverifiable` | Cannot be established by this system at all | none | installed torque value |

`attested` is the important one. The agent prompts ("Strum it — does it move?"),
records the operator's answer as *their* attestation, and never converts that
into its own observation. The verdict payload keeps the two separate: `observed`
vs `attested_by_operator`. That is also the honest artifact for a training
record — an instructor can see which judgements the trainee made themselves.

This is why the interaction for this task must include the inspection step as a
guided dialogue, not a photo verdict. The demo story is stronger for it: the
system knows the limits of its own senses.

---

## 3. Where the AIM procedure and the FAA handbook disagree

Recorded because a verifier must not silently pick a side.

**Pigtail length.** The AIM documents (variants A and B) say:

> Cut the remaining wire leaving 6-8 wraps.

The FAA general rules say:

> Pigtail of 1⁄4 to 1⁄2 inch (three to six twists) should be made at the end of
> the wiring.

6–8 twists against 3–6 twists. These overlap only at 6. A pigtail cut to the AIM
instruction can exceed the FAA maximum.

**Resolution adopted:** the AIM document is the operative standard for the
assessment, because the trainee is being graded against what the instructor
taught. The FAA range is recorded as `regulatory_note` on the same check, and the
agent may mention it when asked, but a trainee at 7 twists is **not** flagged as
an error. The pack records this as an explicit `conflict` entry so the choice is
visible and reviewable rather than buried in a prompt.

A second, softer divergence: the AIM inspection says "if it can move it has too
many wraps", while FAA rule 6 says wire between the nuts should be "as taut as
possible without over twisting". These describe the same failure (over-twisting)
and do not conflict.

---

## 4. What is actually measurable from a wearable camera

Ordered by how confident a verifier can be. This ordering is the basis for the
`confidence_ceiling` field on each check — a cap the verifier may not exceed no
matter how certain the model sounds.

**Reliable (ceiling: high)**
- Wire is present between the two bolts at all.
- The loop around the bolt head sits below the head rather than riding over it
  (FAA rule 7) — a large, high-contrast geometric relationship.
- The pigtail is bent back/under rather than standing proud (FAA rule 1).
- Turnbuckle: wires are laid along the barrel and inserted at both ends.

**Achievable but view-dependent (ceiling: medium)**
- **Direction of pull** — whether the wire tends to tighten the bolt (FAA rule 5,
  and the highest-severity error in the whole task). Determinable from routing:
  which side of each bolt head the wire leaves, and whether it approaches the
  next bolt clockwise. Needs a view that contains *both* bolt heads and the span
  between them; from a single close-up it is genuinely indeterminate. This is the
  check most worth engineering a specific evidence prompt for ("hold still with
  both bolts in view").
- Turnbuckle wrap count (≥4 turns) — countable when the shank is side-on and in
  focus.
- Turnbuckle thread exposure (≤3 threads) — this is the small-visual-detail case
  for this task, analogous to reading a port label. Threads are fine, regular,
  and low-contrast; success depends on working distance and motion blur.

**Hard (ceiling: low — expect `uncertain` to dominate)**
- **Wraps per inch (6–8).** Two compounding problems: resolving individual
  twists at working distance, and establishing scale. There is no ruler in
  frame, so "per inch" needs a reference of known size — the bolt head
  across-flats is the only reliable one, and it requires identifying the bolt
  size first. Counting twists over the whole span and dividing by an estimated
  span length is the fallback, and it is an estimate. The pack should not claim
  a number; it should report a range with an explicit basis, or defer.

**Not measurable**
- Tautness, slip-fit, snag sharpness (see §2), installed torque, wire material
  and diameter (0.032" vs 0.041" is not resolvable at working distance, and the
  spool label is out of frame — this is `attested` at best).

---

## 5. Error modes worth detecting, by severity

`critical` errors are ones where the finished work looks plausible but is wrong —
the case where a second pair of eyes has the most value.

| Error | Severity | Why it matters | Verifiable |
|---|---|---|---|
| Wire routed so pull loosens the fastener | critical | Defeats the entire purpose; looks correct to a novice | `state`, medium ceiling |
| Loop rides up over the bolt head | critical | Creates a slack loop; FAA rule 7 | `state`, high |
| Wire reused / previously twisted | critical | FAA rule 2, new wire each application | `state`, low — kinks are the only tell |
| Pigtail left straight and proud | major | Snag and laceration hazard; FAA rule 1 | `state`, high |
| More than three widely spaced bolts in one series | major | Exceeds handbook limit | `state`, medium |
| Turnbuckle wrapped fewer than 4 turns | major | Below handbook minimum | `state`, medium |
| More than 3 threads exposed on turnbuckle | major | Handbook limit; indicates rigging not adjusted | `state`, low |
| Wraps per inch outside 6–8 | minor | Workmanship; over-twisting also embrittles | `state`, low |
| Nut loosened to align the safety wire hole | critical | Explicitly forbidden; destroys the torque | `action`, video only |
| Inspection step skipped entirely | major | The trainee never checked their work | `action`, video only |

The last two are the argument for keeping a video/action channel rather than
settling for photo verification. Neither leaves any trace in the finished
artifact — a photo of a correctly-wired pair of bolts is identical whether or not
the mechanic loosened a nut to align the hole.

---

## 6. What the reference videos are for

Six clips, ~5.9 minutes total, all first-person:

| Clip | Variant | Duration |
|---|---|---|
| `safety_wire_by_hand` | B | 128.8 s |
| `insert_wire_for_double_wrap_turnbuckle_safety` | C | 87.1 s |
| `safety_wire_pliers_1` … `_4` | A | 17.0–58.6 s |

They serve three distinct purposes, and conflating them would be a mistake:

1. **Reference images per step** — what correct completion looks like, embedded
   in the pack so the verifier compares against a known-good exemplar rather than
   reasoning from text alone.
2. **A mechanics record** — the tools, materials, and hand motions actually used
   at each sub-subtask, extracted frame by frame. This is what lets the agent
   answer "how do I hold it?" with the technique the instructor teaches rather
   than a generic answer, and it is what makes proactive step-tracking possible
   at all: the agent recognises the *motion* it is watching, not just the
   artifact state.
3. **Eval material** — these are demonstrations of correct technique, so they
   supply positive cases only. Negative and uncertain cases must be staged
   deliberately; they cannot be mined from this footage. Noted here because it is
   the main gap in the dataset for this task.

Frames are sampled at 4 fps. Sub-subtask boundaries are discovered by reading
every frame rather than cut on a fixed grid, because the micro-actions have
wildly different durations — clamping the jaws is under a second, a run of
twisting cycles is tens of seconds — and a fixed grid would split the long ones
and merge the short ones.

---

## 7. Open questions

- **Which variant does the demo target?** Variant A (pliers) is the shortest
  path to a complete run and has four clips of reference footage. Variant C
  (turnbuckle) has the better verification story — countable turns and the
  thread-exposure check are the strongest small-detail cases in the task.
- **How is the workpiece staged for evaluation?** Wraps-per-inch and
  direction-of-pull both depend on a repeatable rig. Without one, run-to-run
  differences in lighting and working distance will dominate the metrics.
- **Does the trainee narrate?** The `attested` mechanism needs the operator to
  answer. If assessment is meant to be silent, those checks degrade to "not
  established" and the inspection step becomes largely unverifiable.
