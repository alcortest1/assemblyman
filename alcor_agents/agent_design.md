# Evidence-Grounded Task Testing Agent

## 1. Purpose

This document specifies an agent that evaluates a recorded demonstration of an
AIM maintenance task against the task's compiled procedure, references,
demonstrations, checks, and error modes.

The agent accepts a video, a final image, or both. It produces:

- The detected or user-confirmed task variant.
- A timeline of expected and unexpected fine-grained activities.
- Evidence-linked results for every atomic work unit and correctness criterion.
- A calibrated probability of correctness where the evidence is sufficient.
- Explicit `insufficient_evidence`, `attested`, and `unverifiable` results where
  vision cannot establish correctness.
- A task-level result that cannot conceal a failed critical check by averaging.
- A review artifact in which every visual conclusion links back to exact frames.

The first implementation is an offline evaluation agent for prerecorded media.
A guided capture mode can later reuse the same verification engine to request
better views or physical tests while the demonstration is being recorded.

The word "certification" in this design means an evidence-scoped visual
assessment record. It is not a regulatory or instructor certification unless a
qualified person reviews and signs the record.

## 2. Existing Repository Assets

The agent should build on the current repository rather than introduce a
parallel task representation:

| Asset | Current location | Agent use |
|---|---|---|
| Task procedures | `tasks/<ACS_CODE>/procedure.md` | Human-readable source context |
| Structured source steps | `tasks/<ACS_CODE>/steps.json` | Pack-authoring input |
| Compiled verifier pack | `tasks/<ACS_CODE>/pack.yaml` | Governing runtime specification |
| Source hashes | `tasks/<ACS_CODE>/sources.json` | Source integrity and provenance |
| Handbook extracts | `tasks/<ACS_CODE>/references/` | Rule citations |
| Reference demonstrations | `data/videos/<ACS_CODE>/` | Positive examples and activity mechanics |
| Timestamped frames | `build/index/` and `build/frames/` | Temporal discovery and detail review |
| Reviewed segments | `build/analysis/<ACS_CODE>/*.segments.json` | Initial activity examples and labels |
| Frame extraction | `packs/extract_frames.py` | Media preprocessing |
| Segment slicing | `packs/slice_segments.py` | Interval-to-frame materialization |
| Pack validation | `packs/pack_lint.py` | Runtime readiness gate |

At design time, 11 task directories exist, but only `AM.I.E.S1` and
`AM.II.K.S3` have authored `pack.yaml` files, and both are marked `draft`.
Therefore:

- Draft packs may be used for offline development and evaluation.
- A live assessment must require `packs/pack_lint.py --require-reviewed`.
- A task without a pack cannot receive a correctness verdict. It may only be
  ingested, described, and queued for pack compilation.

## 3. Core Principles

### 3.1 One atomic claim per visual decision

An atomic work unit may contain several claims, but each claim must be evaluated
separately. "Perform wire wraps correctly" is not atomic enough. It should be
split into claims such as:

- The required twisting activity occurred.
- The wire was twisted in the required direction.
- Visible slack was removed.
- The final wraps are uniform.
- Wrap density is within the specified range with a scale reference.

### 3.2 Evidence sufficiency precedes correctness

The agent must not score correctness when the view cannot establish the claim.
It first decides whether the necessary objects, geometry, time interval, scale,
or transcript are present.

### 3.3 Activity occurrence is not correctness

Recognizing an activity proves only that the activity appears to have occurred.
The process technique and resulting state are separate judgments.

### 3.4 Missing evidence is never a pass

If a required step, critical region, physical test, or measurement is not shown,
the result is `insufficient_evidence` or `not_observed`.

### 3.5 Every visual result is traceable

Every visual conclusion must include:

- Video identity and hash.
- A frame interval or one or more frame IDs.
- Timestamps.
- A concise evidence description.
- Visual cues supporting or contradicting the claim.
- Visibility limitations.
- Model, prompt, task-pack, and calibration versions.

### 3.6 Scores are calibrated, not self-declared

A VLM-generated number is a raw feature, not a probability. A score becomes
`p_correct` only after calibration against subject-matter-expert labels.

### 3.7 The system abstains

The supported terminal states are:

- `pass`
- `fail`
- `review`
- `insufficient_evidence`
- `not_observed`
- `attested`
- `unverifiable`
- `not_applicable`

## 4. Operating Modes

### 4.1 Offline batch mode

Inputs:

- ACS task code.
- Optional variant.
- Demonstration video, final images, or both.
- Optional transcript, measurement record, or instructor attestation.

Outputs:

- Machine-readable JSON report.
- Human-readable HTML or Markdown report.
- Activity timeline.
- Review queue containing uncertain and critical results.

### 4.2 Guided capture mode

Guided capture uses partial results to request missing evidence:

- "Show both bolt heads and the entire wire span."
- "Place a ruler beside the twisted span."
- "Hold the pigtail still from an oblique angle."
- "Perform the probe test and state whether the wire fits underneath."

Guided capture must preserve the distinction between what the camera observes
and what the operator or instructor attests.

## 5. Task Pack Runtime Contract

The existing `pack.yaml` remains the authoritative task source. The agent
compiles it into a normalized verification plan. A future schema version should
add the following fields without invalidating existing v1 packs:

```yaml
activities:
  - id: bp.pull_twist_knob
    step_id: bp.s3
    label: Pull and return the safety-wire plier twist knob
    occurrence: repeated
    expected_order_after: [bp.lock_plier_jaws]
    allowed_concurrent_states: [bp.hold_wire_tension]
    duration_hint_s: [0.3, 3.0]
    demonstrations:
      positive: []
      negative: []

checks:
  - id: bp.s4.c1
    statement: The pigtail is curled back toward the bolt head.
    severity: major
    verifiability: state
    evidence_requirement:
      media: image_or_video
      required_objects: [pigtail, bolt_head]
      required_relationships: [both_visible_together]
      preferred_view: oblique_close
    scoring_family: geometric_relationship
    confidence_ceiling: high
    automation_policy: eligible_after_calibration
```

The normalized plan must contain:

- Task and variant identity.
- Ordered steps and fine-grained activities.
- Prerequisites and allowed concurrency.
- Atomic correctness checks.
- Known error modes and severity.
- Required evidence and capture instructions.
- Verifiability classification.
- Positive, negative, near-miss, and uncertain demonstrations.
- Rule precedence and recorded source conflicts.
- Per-check automation policy and thresholds.

If the pack does not specify enough information for a check, the compiler must
mark that check `review_required`; it must not invent requirements.

## 6. Verifiability Classes

Every check is assigned exactly one primary class:

| Class | Meaning | Permitted evidence |
|---|---|---|
| `state` | Visible property of the work or setup | One or more clear frames |
| `action` | Required action occurred | Continuous video interval |
| `temporal_relation` | Order, repetition, or no forbidden action | Video timeline |
| `measurement` | Requires scale, instrument, OCR, or sensor value | Visual plus calibrated reference or record |
| `document` | Requires label, part record, worksheet, or specification | OCR/document evidence |
| `attested` | Physical result only a person can judge | Recorded action plus attributed statement |
| `unverifiable` | Available evidence cannot establish it | No automated pass |

Examples:

- Safety-wire pigtail orientation: `state`.
- Inspection action performed: `action`.
- Nut was not loosened to align the hole: `temporal_relation`, requiring
  sufficiently continuous coverage.
- Six to eight wraps per inch: `measurement`.
- Correct wire gauge: `document` or instrument measurement, not appearance of
  installed wire.
- Tautness when strummed: `attested`.
- Installed torque without a torque record: `unverifiable`.

## 7. System Architecture

```text
Task pack and references
          |
          v
  Verification-plan compiler
          |
          +---------------------------+
          |                           |
          v                           v
 Submitted media                Reference demonstrations
          |                           |
          v                           v
 Media/provenance pipeline      Demonstration index
          |
          v
 Quality and coverage gate
          |
          v
 Variant resolver
          |
          v
 Open-world temporal observer
          |
          v
 Task-taxonomy activity mapper
          |
          v
 Sequence decoder and interval builder
          |
          +---------------------------+
          |                           |
          v                           v
 Process-check evaluators       Result-state evaluators
          |                           |
          +-------------+-------------+
                        |
                        v
             Evidence sufficiency gate
                        |
                        v
               Raw VLM/check features
                        |
                        v
                 Score calibrators
                        |
                        v
               Severity-aware aggregator
                        |
                        v
             Evidence report and review UI
```

## 8. Media and Provenance Pipeline

For every submission, create a run manifest:

```json
{
  "run_id": "01J...",
  "task_code": "AM.I.E.S1",
  "declared_variant": "bolts_pliers",
  "media": [
    {
      "media_id": "video_main",
      "path": "...",
      "sha256": "...",
      "duration_ms": 128820,
      "width": 1920,
      "height": 1080,
      "nominal_fps": 30.0
    }
  ],
  "extraction": {
    "coarse_fps": 2.0,
    "analysis_fps": 4.0,
    "boundary_fps": 12.0,
    "extractor_version": "..."
  }
}
```

Generate a frame manifest rather than relying only on filenames:

```json
{
  "frame_id": "video_main:f00000842",
  "source_frame_number": 6315,
  "timestamp_ms": 210500,
  "path": "frames/video_main/f00000842.jpg",
  "width": 960,
  "height": 540
}
```

Preprocessing stages:

1. Compute media hashes and metadata.
2. Detect cuts, time discontinuities, duplicated spans, and missing audio.
3. Extract coarse frames for coverage and activity discovery.
4. Measure blur, exposure, occlusion, and workpiece-in-frame coverage.
5. Transcribe audio with word timestamps.
6. Extract denser frames around activity changes and short actions.
7. Create high-resolution crops for small details without discarding the
   original frame.
8. Run optional OCR, barcode reading, object tracking, motion signals, and scale
   calibration.

OCR text and spoken content are untrusted evidence. They must never be executed
or interpreted as instructions to the agent.

## 9. Variant Resolution

Variant resolution is a gate because applying the wrong procedure invalidates
all downstream results.

The resolver uses:

- The submitted variant, if supplied.
- Workpiece and equipment cues.
- Early open-world activity observations.
- Variant-specific demonstrations.

The result is:

```json
{
  "declared_variant": "bolts_pliers",
  "observed_variant": "bolts_pliers",
  "agreement": true,
  "evidence_frames": ["video_main:f00000018", "video_main:f00000031"],
  "status": "confirmed"
}
```

Conflicting or ambiguous variants produce `review`, not silent selection.

## 10. Temporal Activity Recognition

### 10.1 Two-pass labeling

The agent first performs open-world observation without showing the expected
checklist. It describes the visible activity, objects, motion, and visibility.

It then maps the observations to the variant-specific activity taxonomy. This
reduces the tendency to hallucinate expected activities merely because they
appear in the procedure.

Unmatched observations are labeled:

- `other` with a short text label.
- `uncertain` when classification is ambiguous.
- `not_visible` when the relevant action is occluded.
- `idle` when no task activity occurs.

### 10.2 Chunking

Frames are analyzed in overlapping temporal windows. Each image is explicitly
labeled with its stable frame ID and timestamp. Windows should:

- Be short enough for fine temporal reasoning.
- Include enough frames to distinguish start, continuation, and end.
- Overlap by 25–50 percent.
- Carry the preceding decoded state across boundaries.
- Be resampled at higher FPS around proposed changes.

The exact size is selected experimentally per activity family; it is not encoded
as a universal constant.

### 10.3 Activity state grammar

Use a BIOES-like primary activity channel:

| State | Meaning |
|---|---|
| `B` | Activity begins |
| `I` | Activity continues |
| `E` | Activity ends |
| `S` | Entire activity occurs within one sampled frame |
| `O` | Other, idle, or background |

Valid transitions for the same activity instance:

```text
O -> O | B | S
B -> I | E
I -> I | E
E -> O | B | S
S -> O | B | S
```

The decoder may allow a penalized transition when sampling misses a boundary.
It must record that the boundary was inferred.

The VLM emits noisy per-frame or per-window observations. Python code enforces
the grammar using dynamic programming or Viterbi decoding with:

- VLM label confidence.
- Activity duration priors.
- Task-order priors.
- Minimum-gap and hysteresis rules.
- Agreement across overlapping windows.
- Visibility and occlusion penalties.

Primary activity intervals should normally be non-overlapping. Concurrent
conditions such as `holding_tension` or `stabilizing_workpiece` belong in a
secondary-state channel and may overlap.

### 10.4 Interval record

```json
{
  "activity_id": "bp.pull_twist_knob",
  "instance_id": "bp.pull_twist_knob:004",
  "phase_source": "decoded",
  "frame_interval": {
    "start_frame": "video_main:f00000316",
    "end_frame": "video_main:f00000327",
    "start_ms": 79000,
    "end_ms": 81750
  },
  "key_frames": {
    "start": "video_main:f00000316",
    "maximum_extension": "video_main:f00000322",
    "end": "video_main:f00000327"
  },
  "description": "The locked plier knob moves outward and returns.",
  "visibility": "clear",
  "mapping_confidence": 0.91
}
```

## 11. Counting Strategy

The agent distinguishes three counting problems.

### 11.1 Separate activity occurrences

Count validated decoded intervals in deterministic code. For example, two
`cut_wire` intervals establish that two cutting activities were observed.

### 11.2 Repetitions within an activity

Do not ask the VLM to count repetitions over a long interval. Define the
repeated cycle as a leaf activity or generate cycle candidates from:

- Tool or hand motion trajectories.
- Optical-flow peaks.
- Key poses.
- Object displacement.

The VLM verifies ambiguous cycle candidates. Python counts accepted cycle
intervals.

### 11.3 Spatial elements in the finished artifact

Wraps, threads, pins, or holes are not solved by temporal segmentation. They
require a suitable macro view, an optional scale reference, specialized visual
processing, and an uncertainty path.

Activity count is never substituted for an outcome requirement. For example,
the number of safety-wire plier pulls does not prove a specified number of wraps
per inch.

## 12. Atomic Correctness Evaluation

For each atomic claim, the evaluator receives only:

- The exact governing rule and its source.
- The required evidence definition.
- The proposed activity interval or state frames.
- Nearby temporal context.
- Relevant positive demonstrations.
- Relevant negative and near-miss demonstrations.
- Optional CV/OCR/motion measurements.

The evaluator does not receive every task document and every reference video in
one prompt.

Each atomic evaluator runs in this order:

1. Confirm the required objects and relationships are visible.
2. Describe observable facts without issuing a verdict.
3. Map facts to the rule.
4. Identify supporting evidence.
5. Search for contradicting evidence and known error modes.
6. Produce `pass`, `fail`, or `insufficient_evidence`.
7. Emit raw scoring features.

Process conformance and result conformance are separate:

```json
{
  "work_unit_id": "bp.s3.gator_roll",
  "process_conformance": {
    "status": "pass",
    "raw_score": 0.92
  },
  "result_conformance": {
    "status": "review",
    "raw_score": 0.74
  }
}
```

## 13. Visual Evidence Record

Every visual check result uses the following contract:

```json
{
  "check_id": "bp.s4.c1",
  "claim": "The pigtail is curled back toward the bolt head.",
  "status": "pass",
  "evidence_type": "visual_state",
  "media_id": "video_main",
  "media_sha256": "...",
  "frame_interval": {
    "start_frame": "video_main:f00000842",
    "end_frame": "video_main:f00000858",
    "start_ms": 210500,
    "end_ms": 214500
  },
  "key_frames": [
    "video_main:f00000847",
    "video_main:f00000852"
  ],
  "description": "The cut end curves back toward the second bolt head and lies approximately parallel to the mounting surface.",
  "visual_cues": [
    {
      "frame_id": "video_main:f00000852",
      "cue": "The terminal wire end curves downward toward the bolt head.",
      "region": {
        "bbox_normalized": [0.61, 0.38, 0.81, 0.67]
      },
      "objects": ["pigtail", "bolt_head"],
      "relationship": "pigtail terminates beside the bolt head"
    }
  ],
  "supporting_evidence": [],
  "contradicting_evidence": [],
  "visibility": {
    "rating": "clear",
    "focus": "acceptable",
    "occlusion": "none",
    "required_geometry_visible": true
  },
  "evidence_sufficiency_score": 0.95,
  "raw_correctness_score": 0.89,
  "calibrated_p_correct": 0.86,
  "limitations": [],
  "rule_source": {
    "pack_version": "...",
    "reference": "AM.I.E.S1 bolts-with-pliers step 4"
  },
  "inference": {
    "model": "...",
    "model_version": "...",
    "prompt_version": "...",
    "calibrator_version": "..."
  }
}
```

Evidence rules:

- Actions cite the complete action interval and representative start, middle,
  and end frames.
- Static states cite the clearest frames.
- Geometric claims cite frames containing all relevant objects together.
- Repeated activities cite every counted instance.
- Absence claims cite the complete adequately observed interval.
- Measurements cite the object and scale or instrument in the same evidence
  bundle.
- Failed checks retain both supporting and contradicting evidence.
- Insufficient-evidence results cite the frames showing blur, occlusion, missing
  scale, or missing geometry.
- Bounding boxes are navigation aids, not precise measurements unless produced
  and validated by a calibrated measurement component.

## 14. Correctness Scores and Calibration

### 14.1 Score semantics

The agent stores:

- `activity_observed_score`
- `evidence_sufficiency_score`
- `raw_correctness_score`
- `calibrated_p_correct`
- `contradiction_score`
- `visibility_score`
- `model_agreement_score`, when multiple passes are used

`calibrated_p_correct` means:

> Estimated probability that this atomic claim is correct, conditional on the
> submitted evidence being sufficient and drawn from the validated deployment
> distribution.

### 14.2 Decision policy

The initial policy may use 0.85 as an experimental auto-pass threshold:

```python
if evidence_sufficiency < evidence_threshold:
    verdict = "insufficient_evidence"
elif calibrated_p_correct >= pass_threshold:
    verdict = "pass"
elif calibrated_p_correct <= fail_threshold:
    verdict = "fail"
else:
    verdict = "review"
```

Suggested initial development values:

```text
evidence_threshold = 0.90
pass_threshold = 0.85
fail_threshold = 0.20
```

These are routing defaults, not certification guarantees. Production thresholds
must be selected from validation data and may vary by:

- Check family.
- Error severity.
- Camera/view requirements.
- Task and variant.
- Calibration sample size.

Checks with a low confidence ceiling, checks without sufficient negative data,
and tactile or otherwise nonvisual checks are never eligible for automatic pass.

### 14.3 Calibration

When native class probabilities are available, retain them as raw features.
Otherwise use structured VLM scores and repeated judgments. Fit a calibrator such
as isotonic regression, Platt scaling, or beta calibration on a dedicated
calibration split.

The calibration record includes:

- Training-data version.
- Check family.
- Model and prompt version.
- Calibration method.
- Sample count and label distribution.
- Reliability curve.
- Brier score and expected calibration error.
- Date and approval status.

A model, prompt, task-pack, or preprocessing change invalidates calibration until
the compatibility policy or revalidation says otherwise.

## 15. Demonstration Library

Correct demonstrations are useful positive examples but cannot define the
decision boundary alone. For every high-value atomic check, build:

- Correct examples.
- Clearly incorrect examples.
- Near-miss examples.
- Correct examples from different operators and views.
- Incorrect examples that superficially resemble the reference.
- Occluded, blurred, incomplete, and out-of-distribution examples.

Demonstrations are indexed by:

- Task, variant, step, activity, and check.
- Correctness label.
- Error-mode label.
- Frame interval and key frames.
- Workpiece, operator, viewpoint, and lighting metadata.
- SME reviewer and review version.

Reference and evaluation examples must not come from overlapping portions of the
same recording.

## 16. Optional Specialized Vision Components

The VLM is the semantic reasoner. Specialized components generate or validate
evidence where evaluation shows they improve performance:

- Blur, exposure, and occlusion detection.
- Tool and equipment detection.
- Workpiece and part detection.
- Hand segmentation for occlusion and crop selection.
- Object tracking.
- OCR and barcode reading for material/tool identity.
- Scale and ruler detection.
- Geometry for routing, spacing, and relative orientation.
- Optical flow and motion peaks for repeated action cycles.
- Audio transcription and attributed attestations.

Specialized components do not issue task-level certification. Their outputs are
versioned inputs to atomic evaluators.

## 17. Aggregation

Required checks are gated, not averaged.

An atomic work unit passes only when:

- Its required activity is observed when occurrence matters.
- Its required process checks pass.
- Its required result-state checks pass.
- Evidence is sufficient for every required visual check.
- No critical error mode is detected.
- Required measurement or attestation evidence is present.

A step passes only when all its required atomic work units pass. A task passes
only when all required steps and all task-level checks pass.

```python
task_pass = (
    variant_confirmed
    and all_required_evidence_present
    and all_required_units_pass
    and not any_critical_failure
    and not any_required_review
)
```

An aggregate numeric score may be shown for analytics, but it must never override
a critical failure or missing required evidence. The primary user-facing result
is categorical and accompanied by the check tree.

## 18. Human Review

The review queue prioritizes:

1. Critical suspected failures.
2. Variant conflicts.
3. Required checks near a threshold.
4. Low-confidence or low-ceiling checks.
5. Missing evidence that may be recoverable by recapture.
6. New `other` activities that may indicate an incomplete taxonomy.

The reviewer sees:

- Rule and source.
- Submitted interval and key frames.
- Correct and incorrect demonstrations.
- Supporting and contradicting cues.
- Raw and calibrated scores.
- Model limitations.
- The ability to correct the verdict, interval, activity label, and explanation.

Reviewer corrections become evaluation or active-learning candidates only after
quality control; they are not automatically treated as training truth.

## 19. Evaluation Design

### 19.1 Dataset split

Split by complete recording and, where possible, by:

- Operator.
- Workpiece.
- Camera/device.
- Lighting and background.
- Recording session.
- Task variant.

Never split neighboring frames or intervals from the same performance across
training and test sets.

### 19.2 Metrics

Report per check, check family, severity, task, and overall:

- Auto-pass precision.
- Correct-work recall and auto-pass coverage.
- False-pass rate.
- Defect recall.
- Critical-defect recall.
- False-fail rate.
- Abstention/review rate.
- Evidence-sufficiency accuracy.
- Evidence localization accuracy.
- Activity interval precision, recall, and temporal IoU.
- Activity count accuracy.
- Brier score and calibration error.

The product acceptance target should prioritize very high auto-pass precision
and critical-defect recall. Exact targets require agreement with AIM and Alcor
because they determine the human-review burden and residual safety risk.

### 19.3 Required evaluation cases

For each critical or major check:

- Correct.
- Incorrect.
- Near-miss.
- Not performed.
- Performed but result incorrect.
- Correct result with process evidence missing.
- Occluded.
- Blurred or too distant.
- Wrong variant.
- Wrong equipment or material.
- Unexpected activity.
- Edited or discontinuous video.

## 20. AM.I.E.S1 First Vertical Slice

AM.I.E.S1 is the preferred first implementation because its reference footage is
already segmented and its analysis explicitly distinguishes visual, temporal,
tactile, and unverifiable checks.

Initial scope:

1. Require the `bolts_pliers` variant.
2. Analyze one continuous submitted video plus final close views.
3. Recognize setup, threading, distance measurement, twisting, pigtail, and
   inspection activities.
4. Decode non-overlapping primary intervals and label unrelated actions
   `other`.
5. Count plier pull/return cycles from atomic cycle intervals.
6. Evaluate:
   - Tool visibly used.
   - Required activities occurred.
   - Wire routing is in the tightening direction when geometry is visible.
   - Loop remains down around the bolt head.
   - Wraps are visibly uniform.
   - Pigtail is defined and curled back.
   - Inspection actions were performed.
7. Require a ruler view for wraps-per-inch.
8. Record tautness and probe-test outcomes as attributed attestations.
9. Mark installed torque, unseen material properties, and unseen history as not
   visually established.

The existing correct videos provide positive examples only. Staged incorrect,
near-miss, and insufficient-evidence recordings are required before score
calibration or automated passing.

## 21. Proposed Implementation Layout

```text
task_tester/
  cli.py
  config.py
  plan_compiler.py
  media.py
  provenance.py
  quality.py
  variants.py
  temporal/
    observer.py
    mapper.py
    decoder.py
    intervals.py
    counting.py
  verification/
    evidence.py
    atomic.py
    process.py
    state.py
    measurements.py
    attestations.py
  scoring/
    features.py
    calibrate.py
    policy.py
    aggregate.py
  models/
    base.py
    claude.py
    gemini.py
  reports/
    schema.py
    json_report.py
    html_report.py
  prompts/
    open_world/
    activity_mapping/
    evidence_sufficiency/
    atomic_correctness/
    contradiction_review/
  schemas/
    run_manifest.schema.json
    verification_plan.schema.json
    activity_timeline.schema.json
    check_result.schema.json
    task_report.schema.json
  tests/

build/runs/<run_id>/
  run_manifest.json
  frames.json
  frames/
  crops/
  transcript.json
  observations.json
  activity_timeline.json
  check_results/
  task_report.json
  report.html
```

Model adapters expose the same structured interface so model comparisons do not
change the verification logic. Prompt templates, schemas, thresholds, and model
settings are version-controlled.

## 22. Delivery Phases

### Phase 1: Deterministic skeleton

- Pack readiness gate.
- Media manifests and timestamped frames.
- JSON schemas.
- Manual or imported activity intervals.
- Evidence-linked atomic result format.
- Rule-based aggregation.

### Phase 2: VLM baseline

- Variant resolver.
- Open-world observation.
- Closed-world activity mapping.
- Atomic evidence and correctness prompts.
- Raw scores and review reports.
- No automatic certification.

### Phase 3: Temporal decoding and counting

- Overlapping-window inference.
- BIOES state decoder.
- Boundary refinement.
- Deterministic interval counting.
- Repeat-cycle candidate detection.

### Phase 4: Evaluation and calibration

- SME-labeled negative and uncertain cases.
- Leak-free train/calibration/test split.
- Per-family calibrators.
- Threshold selection and reliability reporting.

### Phase 5: Automation policy

- Automatic pass only for validated check families.
- Severity-aware thresholds.
- Human review queue.
- Model/prompt/calibration drift controls.

### Phase 6: Guided capture

- Live evidence coverage.
- Recapture instructions.
- Guided tactile tests and attributed responses.
- Final instructor sign-off.

## 23. Non-Goals for the First Version

- Proving physical properties from appearance alone.
- Inferring hidden torque, material provenance, or prior actions from a final
  image.
- Treating similarity to a correct demonstration as correctness.
- Treating a raw VLM confidence value as calibrated certainty.
- Automatically compiling governing requirements without SME review.
- Averaging away failed critical requirements.
- Claiming regulatory certification without qualified human authority.

## 24. Definition of Done for the First Agent

The first agent is complete when it can take a reviewed task pack and a submitted
video and:

1. Validate pack and media provenance.
2. Resolve or reject the task variant.
3. Produce a complete primary activity timeline with `other`, `uncertain`, and
   visibility states.
4. Enforce valid activity transitions and emit deterministic intervals.
5. Evaluate every pack check or explicitly explain why it cannot be evaluated.
6. Attach exact frames, intervals, descriptions, and visual cues to every visual
   result.
7. Keep evidence sufficiency separate from correctness.
8. Produce raw and, where available, calibrated correctness scores.
9. Apply critical-check gates without averaging.
10. Generate a reproducible JSON and human-review report.
11. Pass unit, schema, integration, and fixed-fixture regression tests.
12. Refuse a live automatic pass when the pack, calibrator, evidence, or
    automation policy is not approved.
