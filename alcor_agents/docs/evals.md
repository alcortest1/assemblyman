# Evaluation datasets and agent-system runs

The Task Pack Inspector compares agent systems at two levels:

- **Atom level:** each labeled activity, correctness criterion, or defect atom.
- **Task level:** whether the complete submitted task should pass.

The web UI reads human-labeled datasets from `evals/datasets/*.json` and agent
predictions from `build/evals/runs/*.json`. It does not invent metrics when
either side is missing.

## Why correct references are not an evaluation set

The existing task videos are correct reference demonstrations. One or two
correct examples per atom help a VLM understand the intended activity, but
precision and defect recall require incorrect examples too.

Until an atom has both correct and incorrect labels, the inspector marks it
`needs labeled negatives`. Metrics from a one-class dataset would not establish
whether an agent can reject a plausible mistake.

## Labeled dataset schema

Each file describes one task and one split. Split complete recordings, not
neighboring frames from the same recording.

```json
{
  "schema_version": 1,
  "dataset_id": "amies1_holdout_v1",
  "title": "AM.I.E.S1 held-out demonstrations",
  "description": "SME-labeled correct, incorrect, and difficult demonstrations.",
  "task_code": "AM.I.E.S1",
  "split": "test",
  "samples": [
    {
      "sample_id": "demo_001",
      "media": {
        "video": "submissions/demo_001.mp4",
        "sha256": "..."
      },
      "variant": "bolts_pliers",
      "task_label": "correct",
      "atom_labels": [
        {
          "atom_id": "atom:correctness:...",
          "label": "correct",
          "frame_interval": {
            "start_frame": "video_main:f00000842",
            "end_frame": "video_main:f00000858"
          },
          "reviewer": "sme-id",
          "notes": "Pigtail is visibly curled back."
        }
      ]
    }
  ]
}
```

Allowed task and atom ground-truth labels are:

- `correct`
- `incorrect`
- `not_applicable`
- `unverifiable`

Only `correct` and `incorrect` participate in binary precision/recall metrics.

An atom can be addressed directly by `atom_id`. During early pack iteration, a
label may instead use the stable source tuple:

```json
{
  "source_id": "bp.s4.c1",
  "variant": "bolts_pliers",
  "step_id": "bp.s4",
  "label": "incorrect"
}
```

## Agent run schema

Every system configuration writes the same prediction format. This permits fair
comparisons among a direct VLM baseline, a temporal system, a hybrid evidence
system, or future implementations.

```json
{
  "schema_version": 1,
  "run_id": "hybrid_v1_amies1_holdout",
  "created_at": "2026-08-02T20:00:00Z",
  "task_code": "AM.I.E.S1",
  "dataset_id": "amies1_holdout_v1",
  "system": {
    "id": "hybrid_evidence",
    "name": "Hybrid evidence agent",
    "version": "1.0.0",
    "model": "model-name",
    "prompt_version": "atomic-v3"
  },
  "thresholds": {
    "evidence": 0.9,
    "pass": 0.85,
    "fail": 0.2
  },
  "predictions": [
    {
      "sample_id": "demo_001",
      "task_prediction": {
        "status": "pass",
        "p_correct": 0.93
      },
      "atom_predictions": [
        {
          "atom_id": "atom:correctness:...",
          "status": "pass",
          "p_correct": 0.91,
          "evidence_sufficiency": 0.96,
          "frame_interval": {
            "start_frame": "video_main:f00000842",
            "end_frame": "video_main:f00000858"
          }
        }
      ]
    }
  ]
}
```

Prediction statuses are:

- `pass`
- `fail`
- `review`
- `insufficient_evidence`
- `not_observed`
- `not_applicable`

Only `pass` and `fail` are decided predictions. Other statuses count as
abstentions and reduce coverage.

## Metric semantics

The positive class is `correct`, predicted by `pass`.

| Metric | Meaning |
|---|---|
| Precision | Of automatic passes, the fraction truly correct |
| Recall | Of truly correct samples, the fraction automatically passed |
| Defect recall | Of truly incorrect samples, the fraction automatically failed |
| Coverage | Fraction receiving pass/fail rather than an abstention |
| False-pass rate | Of truly incorrect samples, the fraction automatically passed |

Abstaining on a correct sample reduces auto-pass recall. Abstaining on an
incorrect sample reduces defect recall. This makes a system that sends
everything to human review visibly safe but low-coverage, rather than appearing
perfect.

Report all metrics with support counts. With only one or two examples, the
numbers are descriptive and too unstable for production threshold selection.
