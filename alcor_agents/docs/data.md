# Dataset

Source material for the Alcor AI pilot with **AIM Fremont** — 11 FAA maintenance
tasks that the campus selected for photo-based AI assessment, plus the reference
material needed to judge whether a student performed each task correctly.

Everything under `data/` is either supplied by AIM (confidential) or a public FAA
handbook. Everything under `data/processed/` and `tasks/` is **derived** and can be
regenerated from the sources by the scripts described below.

> **Note on repository size.** `data/` currently holds ~4.5 GB (handbook PDFs and
> videos) and is *not* covered by the repository `.gitignore`. Decide whether these
> binaries should be committed, tracked with LFS, or ignored before the next commit.

---

## 1. Source assets (`data/`)

| Asset | Size | Origin |
| --- | --- | --- |
| `Alcor_Pilot_AMS_Selections (1).xlsx` | 72 KB | AIM Fremont's task/instructor selections |
| `drive-download-aim-procedures-confidential/*.docx` | 13 files, 248 KB | AIM procedure skill sheets (confidential) |
| `FAA-H-8083-31B_…_Handbook.pdf` | 107 MB | FAA AMT Handbook — **Airframe** (1,052 pp) |
| `amtg_handbook.pdf` | 88 MB | FAA AMT Handbook — **General**, 8083-30B (677 pp) |
| `amt_powerplant_handbook.pdf` | 205 MB | FAA AMT Handbook — **Powerplant**, 8083-32B (500 pp) |
| `videos/<ACS_CODE>/*.mp4` | 40 files, 3.27 GB | First-person task videos, from the Drive links in the workbook |
| `videos/youtube/*.mp4` | 10 files, 51 MB | TrainWithAIM review videos (~3 min each) |

FAA handbooks are U.S. Government works in the public domain. The AIM procedure
sheets and task videos are AIM's material, shared for this pilot — check with AIM
before redistributing them or the derived packs.

### 1.1 The workbook

Three sheets:

- **Pilot Overview** — scope prose: 3-month pilot, 11 distinct tasks
  (General 7 / Airframe 2 / Powerplant 2), 33 delivery instances, and the three
  pilot components (photo assessment, automated documentation, model-training capture).
- **Task Delivery** — the substantive sheet, in two sections:
  - *Section 1: Block Delivery* — the 11 tasks as taught in their natural block.
  - *Section 2: Capstone Delivery* — the same 11 tasks repeated under each of two
    Capstone instructors (Ahmad Erakat, Angel Javier), who each cover all 11.
    11 + 22 = the 33 delivery instances.
- **Summary** — task counts by subject and an instructor roster with roles.

Columns on the Task Delivery sheet:

| Column | Meaning |
| --- | --- |
| `#` | Task number, 1–11 |
| `Subject` | General / Airframe / Powerplant |
| `Block` | Course block the task sits in (1, 2, 3, 5, 6, 16) |
| `ACS Code` | FAA Airman Certification Standards code — the stable task identifier |
| `Task / Skill` | Task title |
| `Instructor(s)` | Semicolon-separated; may carry `(primary)` / `(Capstone)` roles |
| `Photo-Assessment Fit` | **Draft judgement, not AIM's input** — flagged in the workbook as a starting point to confirm with Alcor |
| `Notes / Discussion` | Free text; only populated for the two calculation tasks |
| `Week/Day Taught` | e.g. `Week 4; Day 3` |
| `Document Procedures` | Google Docs links; newline-separated when several |
| `First-Person Video` | Google Drive links; newline-separated, up to 9 per task |
| `TrainWithAIM Video` | One YouTube link per task |

Two things worth carrying forward:

- **`Photo-Assessment Fit` is a draft.** The workbook says so explicitly. Of the 11
  tasks, 7 are rated High, 1 Medium-High, 1 "Document", and 2 Low.
- **Three tasks have no physical deliverable.** `AM.I.C.S3` and `AM.I.C.S5` are
  weight-and-balance calculations; `AM.I.I.S1` is a completed FAA Form 337. The
  workbook flags these as a scoping question for single-photo assessment.

### 1.2 Procedure sheets

13 `.docx` files for 11 ACS codes — `AM.I.E.S1` (safety wire) has **three**
variants (bolts by hand, bolts with pliers, turnbuckle by hand) whose acceptance
criteria differ.

They share a template: *Before You Begin* → *Safety & Equipment* → one bold section
per phase of work, each with a **Step Instructions** list and a matching **Senior
Mechanic Notes** list. Steps carry their note reference as bare trailing digits —
`Deburr the tubing ends2-3.` means *see notes 2 and 3*.

Two parsing traps, both handled in `packs/ingest.py`:

- The trailing digits are ambiguous when the step text itself ends in a number:
  `Fill out block 13.` is *block 1*, note *3*. Resolved by only accepting a
  reference that starts at the next unconsumed note number.
- One sheet has a typo — `Senior Mechanic Notes"` ends with a curly quote instead
  of a colon — so labels are matched on their text, not their terminator.

---

## 2. Derived data (`data/processed/`)

Regenerate everything with:

```bash
python3 scripts/xlsx_to_csv.py            # workbook  -> CSVs
python3 scripts/docx_to_markdown.py       # .docx     -> markdown
python3 scripts/download_drive_videos.py  # Drive     -> data/videos/<ACS_CODE>/
python3 scripts/download_youtube_videos.py# YouTube   -> data/videos/youtube/
python3 packs/handbook_index.py --build   # PDFs      -> page-label indices
```

| Output | Rows | Contents |
| --- | --- | --- |
| `tasks.csv` | 11 | One row per distinct ACS task (from the block-delivery section) |
| `task_delivery.csv` | 33 | One row per delivery instance; `delivery_type` = block/capstone |
| `instructors.csv` | 11 | Instructor roster with `role_type` and `coverage` |
| `videos_manifest.csv` | 40 | Drive videos: ACS code, file id, path, size, status |
| `youtube_manifest.csv` | 10 | TrainWithAIM videos; `acs_codes` is comma-separated (one video serves two tasks) |
| `handbook_index/{30B,31B,32B}.json` | — | Printed page label → PDF page index |
| `procedures/*.md` | 13 + index | Markdown conversions of the skill sheets |

The CSVs are tidy and load directly:

```python
import pandas as pd
tasks = pd.read_csv("data/processed/tasks.csv")
```

Multi-valued URL cells are joined with `" | "` and paired with a count column
(`doc_procedure_count`, `first_person_video_count`). `Week 4; Day 3` is split into
integer `week` / `day`. `Photo-Assessment Fit` is split into the full string plus a
bare `photo_fit_level` (`High` / `Low` / `Document` / `Medium-High`).

### Handbook page indices

Procedure sheets cite handbook pages by **printed chapter-relative label**
(`pages 9-92 to 9-94`), which is not the PDF page index. `packs/handbook_index.py`
scans each PDF once and caches the mapping.

Text extraction does not preserve layout, so the printed label lands at the *end*
of the extracted text in 31B but at the *start* in 30B and 32B. The indexer detects
which per handbook — without that, 30B yielded 88 usable labels instead of 626.
Cross-checked against an independent content search: 30B `7-79` → PDF page 322 by
both methods.

---

## 3. Compiled task packs (`tasks/<ACS_CODE>/`)

A **pack** is everything a verifier needs for one task in one directory: the
procedure in full, the handbook pages it cites, the reference videos, and an
explicit machine-readable list of what "done correctly" means.

```
tasks/AM.II.K.S3/
  pack.yaml          the compiled judgement: steps, checks, error modes, evidence
  procedure.md       human-readable procedure(s)
  steps.json         structured steps + notes (drafting aid for pack.yaml)
  sources.json       every input with sha256 — the tamper check
  references/handbook/*.md + *.json   cited pages, with location metadata
  keyframes/         sampled video frames (requires ffmpeg)
```

### Pack schema

| Key | Purpose |
| --- | --- |
| `schema_version`, `status` | `status` is `draft` or `reviewed` |
| `acs_code`, `task_no`, `title`, `subject`, `block`, `week`, `day` | Identity, from the workbook |
| `photo_assessment` | `fit` plus a written rationale |
| `variants` | Only where one ACS code is taught several ways (`AM.I.E.S1`) |
| `steps[]` | `id`, verbatim `text`, `note_refs`, `checks[]`, `error_modes[]` |
| `steps[].checks[]` | `statement` + `observable`: `photo` / `video` / `document` / `measurement` |
| `steps[].error_modes[]` | `statement` + `severity`: `critical` / `major` / `minor` |
| `evidence.required[]` | What the student must capture, and how to frame it |
| `references` | Handbook citations, video paths, TrainWithAIM link |
| `assumptions[]` | Every inference made during compilation |
| `open_questions[]` | Scoping questions for Alcor/AIM |

Two conventions carry the weight:

- **`observable: measurement` means a photo cannot settle it.** Pull tests,
  continuity checks and the safety-wire tightness test are explicit acceptance
  criteria in the source sheets but are tactile or instrument-based. Marking them
  `photo` would let a bad crimp or a loose safety pass.
- **Anything inferred is flagged `assumed: true` and must have a matching entry in
  `assumptions[]`,** with a reason and a `resolve_by`. The linter enforces the link
  in both directions.

### Compile flow

```bash
python3 packs/ingest.py AM.II.K.S3                                    # procedure.md, steps.json, sources.json
python3 packs/extract_handbook.py AM.II.K.S3 --handbook 31B \
        --pages 9-92..9-94 --cited-by-source                          # references/handbook/
python3 packs/keyframes.py AM.II.K.S3 --interval 5                    # keyframes/ (needs ffmpeg)
python3 packs/pack_lint.py AM.II.K.S3                                 # validate
```

Then a human writes `pack.yaml` — the checks and error modes are a judgement call,
not a transform of the source.

`extract_handbook.py` has two location modes: `--pages` (exact, via the label
index) and `--search` (content query, for handbooks or tasks where labels do not
apply). `--cited-by-source` records whether the *procedure sheet itself* names the
reference; when it does not, the extracted file is stamped as an assumed reference
and the pack must declare it.

### Linting

`packs/pack_lint.py` checks three things:

- **schema** — required keys, enum values, unique ids, non-empty verbatim text,
  every step has at least one check
- **referential** — referenced files exist; `sources.json` hashes still match the
  files on disk (so a pack fails if its source `.docx` changed after ingest); the
  pack's handbook citation agrees with the extractor's sidecar
- **assumptions** — every `assumed: true` item has a declared assumption

`--require-reviewed` is the session-use gate: it additionally fails any pack still
marked `draft`. A draft pack has not been through subject-matter review and must
not drive a live student session.

### Current packs

| Pack | Steps | Status | Notes |
| --- | --- | --- | --- |
| `AM.I.E.S1` (task 3, safety wire) | 13 across 3 variants | `draft` | Handbook reference is **assumed** — see below |
| `AM.II.K.S3` (task 9, electrical connector) | 19 across 5 sections | `draft` | Handbook reference cited by source |

Open items recorded in the packs themselves:

- The safety-wire sheets **cite no handbook at all**, unlike every other task.
  FAA-H-8083-30B 7-77…7-80 was located during compilation and is flagged assumed.
  AC 43.13-1B, which the other sheets lean on for safetying, is not in `data/`.
- For `AM.I.E.S1`, the **variant must be supplied at capture time** — acceptance
  criteria differ (6–8 wraps per inch on bolts vs. exactly 4 wraps per wire on a
  turnbuckle).
- Wraps-per-inch and the 1/8" strip length are only measurable from a photo if a
  **scale reference is in frame**.
- Crimper *setup* steps are process checks; a photo of finished work cannot
  evidence them at all.

---

## 4. Known gaps

- **`ffmpeg` is not installed**, so `packs/keyframes.py` cannot sample frames yet
  (`brew install ffmpeg`). The videos themselves are all downloaded.
- **YouTube downloads are 360p.** Without ffmpeg only pre-muxed streams can be
  taken. Re-run with `--best` once ffmpeg is available for full quality.
- **9 packs remain uncompiled** — the tooling handles all 11; only tasks 3 and 9
  have authored `pack.yaml` files.
- **`AM.III.M.S5`** (propeller) has no first-person video; the workbook records it
  as "N/A (not AIM developed)".
