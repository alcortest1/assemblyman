"""Local inspector for compiled task packs, reference videos and frame analysis.

Serves a small React app plus a read-only JSON API over everything that has been
compiled for a task: the pack, the normalized procedure, the source videos, the
sampled frames, and the pass-1 sub-subtask segmentation.

Read-only by design — it opens files under alcor_agents and never writes.

    ./.venv/bin/python inspector/server.py          # http://127.0.0.1:8765
    ./.venv/bin/python inspector/server.py --port 9000
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import mimetypes
import re
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from http.server import HTTPServer, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

try:
    import yaml
except ImportError:  # pack.yaml is then shown as raw text rather than parsed
    yaml = None

try:  # package import when run as `python -m inspector.server` or under tests
    from . import vlm
except ImportError:  # direct `python inspector/server.py`
    import vlm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packs"))
import handbook_search  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
VENDOR = Path(__file__).resolve().parent / "vendor"

TASKS_CSV = ROOT / "data" / "processed" / "tasks.csv"
TASKS_DIR = ROOT / "tasks"
VIDEO_DIR = ROOT / "data" / "videos"
FRAME_SETS = {"detail": ROOT / "build" / "frames", "index": ROOT / "build" / "index"}
ANALYSIS_DIR = ROOT / "build" / "analysis"
EVAL_DATASETS_DIR = ROOT / "evals" / "datasets"
EVAL_RUNS_DIR = ROOT / "build" / "evals" / "runs"
# Photo criteria drafted from the procedure sheet and handbook by
# packs/compile_pack.py, with the source attribution behind each condition.
CRITERIA_DIR = ROOT / "build" / "criteria"
# Hand-organised grading sheets, one per subtask, checked into the repo rather
# than built. `build/criteria` holds the compiler's per-step output; this holds
# the per-subtask instrument the roll-up's subtasks are graded against.
SUBTASK_CRITERIA_DIR = ROOT / "criteria"
# The photo-assessment tab is the one part of the inspector that writes: edited
# criteria and completed grading runs land here, and nowhere else.
PHOTO_DIR = ROOT / "build" / "photo_eval"

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}

# How many frames of a step's own footage a grader is shown. One frame is a
# guess at the instant a step ended, and the guess lands mid-action often enough
# that 81% of the conditions inside a step call came back unobservable — hands
# over the work, a tool still on it, the article in a fixture. Several frames
# spread across the step let an occluded condition be settled from the moment it
# was visible instead. `1` reproduces the single-frame behaviour exactly.
STEP_FRAMES = 3
# A ceiling, because every frame is billed on every call of every model: a run
# of 23 steps across 3 models is 69 calls, and each frame added puts 69 more
# images through the API.
MAX_STEP_FRAMES = 12
# Candidates offered to the best-view picker. Enough to cover the tail of a step
# without paying for a frame every quarter-second.
BEST_VIEW_SAMPLE = 12


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def read_text(path: Path) -> str | None:
    try:
        return path.read_text()
    except Exception:
        return None


@lru_cache(maxsize=1)
def task_rows() -> list[dict]:
    if not TASKS_CSV.exists():
        return []
    with TASKS_CSV.open() as handle:
        return list(csv.DictReader(handle))


def frame_dir(acs: str, video: str, which: str) -> Path:
    return FRAME_SETS.get(which, FRAME_SETS["detail"]) / acs / video


def frame_names(acs: str, video: str, which: str) -> list[str]:
    directory = frame_dir(acs, video, which)
    if not directory.is_dir():
        return []
    # Filenames encode timestamps (`t000012_25.jpg`), so lexical order is
    # chronological order once zero-padded.
    return sorted(p.name for p in directory.glob("t*.jpg"))


def clamp_frames_per_step(value, default: int = STEP_FRAMES) -> int:
    """Read a requested frames-per-step, refusing anything unusable.

    Every frame is billed on every call of every model, so this is a number a
    typo can make expensive. Out-of-range and unparseable values fall back to
    the default rather than raising: a bad query string should not take the
    photo tab down, and the response says which value was used.
    """
    try:
        k = int(value)
    except (TypeError, ValueError):
        return default
    return min(MAX_STEP_FRAMES, k) if k >= 1 else default


def frame_at(names: list[str], fraction: float) -> int:
    """Index of the frame `fraction` of the way through `names`.

    Shared by the single-frame suggestion and the window it is the end of, so
    the two cannot drift: whatever `k=1` returns is exactly the frame the
    one-frame path used to pick.
    """
    if not names:
        return 0
    cut = round(len(names) * min(1.0, max(0.0, fraction))) - 1
    return min(len(names) - 1, max(0, cut))


def sample_frames(window: list[str], k: int) -> list[str]:
    """`k` frames spread evenly across the frames one step occupies.

    The work a step is graded on is the state it *ends* in, so a single frame is
    the last one — that is what `k=1` returns, unchanged from the behaviour this
    replaces. Above one, the frames are equidistant across the whole window with
    both ends included: `k=2` is start and end, `k=3` start/middle/end, `k=4`
    lands at 0, 33, 66 and 100 percent.

    A window shorter than `k` cannot supply `k` distinct frames, and repeating
    one to make up the count would bill for the same image several times, so the
    result is deduplicated and may be shorter than asked for.
    """
    if not window or k <= 0:
        return []
    if k == 1 or len(window) == 1:
        return [window[-1]]
    last = len(window) - 1
    picked = [window[round(last * i / (k - 1))] for i in range(k)]
    return list(dict.fromkeys(picked))


def videos_for(acs: str) -> list[dict]:
    directory = VIDEO_DIR / acs
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        stem = path.stem
        out.append(
            {
                "name": stem,
                "file": f"/files/data/videos/{acs}/{path.name}",
                "bytes": path.stat().st_size,
                "frames": {
                    which: len(frame_names(acs, stem, which)) for which in FRAME_SETS
                },
                "segments_file": (
                    f"/api/segments/{acs}/{stem}"
                    if (ANALYSIS_DIR / acs / f"{stem}.segments.json").exists()
                    else None
                ),
            }
        )
    return out


def task_summary(row: dict) -> dict:
    acs = row["acs_code"]
    pack = TASKS_DIR / acs / "pack.yaml"
    videos = videos_for(acs)
    segmented = sum(1 for v in videos if v["segments_file"])
    return {
        "acs_code": acs,
        "task_no": int(row["task_no"]) if row.get("task_no", "").isdigit() else None,
        "title": row.get("task", ""),
        "subject": row.get("subject", ""),
        "block": row.get("block", ""),
        "photo_fit": row.get("photo_fit_level", ""),
        "week": row.get("week", ""),
        "day": row.get("day", ""),
        "has_pack": pack.exists(),
        "pack_status": pack_status(pack),
        "video_count": len(videos),
        "segmented_videos": segmented,
        "frame_count": sum(v["frames"].get("detail", 0) for v in videos),
    }


def pack_status(pack: Path) -> str | None:
    if not pack.exists():
        return None
    text = read_text(pack) or ""
    match = re.search(r"^status:\s*(\w+)", text, re.MULTILINE)
    return match.group(1) if match else "unknown"


def load_pack(acs: str) -> tuple[dict | None, str | None, str | None]:
    """Return parsed pack, raw text, and parse error without hiding bad YAML."""
    pack_path = TASKS_DIR / acs / "pack.yaml"
    pack_text = read_text(pack_path)
    pack = None
    pack_error = None
    if pack_text and yaml is not None:
        try:
            loaded = yaml.safe_load(pack_text)
            pack = loaded if isinstance(loaded, dict) else None
        except Exception as error:
            pack_error = str(error)
    return pack, pack_text, pack_error


def atom_id(kind: str, *parts: object) -> str:
    """Stable readable ID with a hash suffix to avoid label-slug collisions."""
    raw = ":".join(str(part or "unassigned") for part in parts)
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")[:72]
    suffix = hashlib.sha1(raw.encode()).hexdigest()[:8]
    return f"atom:{kind}:{slug}:{suffix}"


def segment_step_resolver(pack: dict | None):
    """Resolve a segment's `step_id` to the pack step it means.

    Two encodings are in use and both must keep working. AM.II.K.S3 writes pack
    step ids verbatim ("cs.s4"). AM.I.E.S1 predates that and writes 1-based
    integers that index into the steps of *that clip's variant*, where the
    variant comes from the pack's clip→variant map rather than the segment
    file's own local A/B/C label. Getting this wrong silently downgrades a task
    to prose descriptions instead of real acceptance criteria, so it lives in
    one place and is shared by every caller.

    Returns `resolve(video, data, segment) -> (step | None, variant)`.
    """
    steps = (pack or {}).get("steps") or []
    steps_by_variant: dict[str, list[dict]] = {}
    for step in steps:
        steps_by_variant.setdefault(step.get("variant") or "default", []).append(step)

    video_variants: dict[str, str] = {}
    for video in ((pack or {}).get("references") or {}).get("videos") or []:
        path = Path(video.get("path") or "")
        if path.stem and video.get("variant"):
            video_variants[path.stem] = video["variant"]

    def resolve(video: str, data: dict, segment: dict) -> tuple[dict | None, str]:
        variant = video_variants.get(video) or data.get("variant") or "unassigned"
        variant_steps = steps_by_variant.get(variant, [])
        raw = segment.get("step_id")
        if isinstance(raw, bool) or raw is None:
            return None, variant
        if isinstance(raw, int) and 1 <= raw <= len(variant_steps):
            return variant_steps[raw - 1], variant
        if isinstance(raw, str):
            return next((s for s in steps if s.get("id") == raw), None), variant
        return None, variant

    return resolve


def atomic_catalog(acs: str, pack: dict | None) -> dict:
    """Normalize pack checks/error modes and reviewed segments into atoms.

    This is deliberately a derived view. It exposes the atomic concept in the
    inspector before pack schema v2 has first-class `activities:` and
    `atomic_work_units:` fields.
    """
    variants = pack.get("variants") or [] if pack else []
    steps = pack.get("steps") or [] if pack else []
    variant_meta = {
        variant.get("id"): {
            "id": variant.get("id"),
            "label": variant.get("label") or variant.get("id"),
        }
        for variant in variants
        if variant.get("id")
    }
    if not variant_meta and steps:
        for step in steps:
            variant = step.get("variant") or "default"
            variant_meta.setdefault(variant, {"id": variant, "label": variant})

    # Map each reference clip to its declared pack variant. This is safer than
    # interpreting local A/B/C labels, whose meaning is task-specific.
    video_variants: dict[str, str] = {}
    if pack:
        for video in ((pack.get("references") or {}).get("videos") or []):
            path = Path(video.get("path") or "")
            if path.stem and video.get("variant"):
                video_variants[path.stem] = video["variant"]

    step_order: dict[tuple[str, str], int] = {}
    steps_by_variant: dict[str, list[dict]] = {}
    for index, step in enumerate(steps):
        variant = step.get("variant") or "default"
        steps_by_variant.setdefault(variant, []).append(step)
        step_order[(variant, step.get("id"))] = index

    atoms: list[dict] = []

    # A correctness check and a known error mode are both atomic claims, but
    # they remain distinct kinds because their scoring and aggregation differ.
    for step in steps:
        variant = step.get("variant") or "default"
        common = {
            "variant": variant,
            "step_id": step.get("id"),
            "step_title": step.get("text") or step.get("instruction") or step.get("id"),
            "step_order": step_order.get((variant, step.get("id")), 0),
        }
        for check in step.get("checks") or []:
            atoms.append(
                {
                    **common,
                    "id": atom_id("correctness", check.get("id") or "", variant),
                    "source_id": check.get("id"),
                    "kind": "correctness",
                    "label": check.get("statement") or check.get("claim") or check.get("id"),
                    "description": check.get("note"),
                    "observable": check.get("observable"),
                    "verifiability": check.get("verifiability"),
                    "confidence_ceiling": check.get("confidence_ceiling"),
                    "severity": check.get("severity"),
                    "source": "pack.check",
                    "examples": [],
                }
            )
        for error in step.get("error_modes") or []:
            atoms.append(
                {
                    **common,
                    "id": atom_id("defect", error.get("id") or "", variant),
                    "source_id": error.get("id"),
                    "kind": "defect",
                    "label": error.get("statement") or error.get("id"),
                    "description": error.get("note"),
                    "severity": error.get("severity"),
                    "source": "pack.error_mode",
                    "examples": [],
                }
            )

    # Reviewed sub-subtask intervals become activity atoms. Repeated appearances
    # of the same label under the same variant and step are examples of one atom.
    activities: dict[tuple[str, str | None, str], dict] = {}
    resolve_step = segment_step_resolver(pack)
    analysis_root = ANALYSIS_DIR / acs
    for segments_path in sorted(analysis_root.glob("*.segments.json")):
        data = read_json(segments_path)
        if not data or not isinstance(data.get("segments"), list):
            continue
        video = data.get("video") or segments_path.name.removesuffix(".segments.json")
        variant = video_variants.get(video) or data.get("variant") or "unassigned"
        variant_meta.setdefault(variant, {"id": variant, "label": variant})
        variant_steps = steps_by_variant.get(variant, [])

        for segment in data["segments"]:
            pack_step, _ = resolve_step(video, data, segment)
            mapped_step_id = pack_step.get("id") if pack_step else None
            step_title = (
                (pack_step or {}).get("text")
                or segment.get("step_title")
                or "Setup / outside official subtasks"
            )
            label = segment.get("substep_label") or f"segment-{segment.get('seq')}"
            key = (variant, mapped_step_id, label)
            if key not in activities:
                activities[key] = {
                    "id": atom_id("activity", variant, mapped_step_id, label),
                    "source_id": label,
                    "kind": "activity",
                    "variant": variant,
                    "step_id": mapped_step_id,
                    "step_title": step_title,
                    "step_order": (
                        step_order.get((variant, mapped_step_id), -1)
                        if mapped_step_id
                        else -1
                    ),
                    "label": label.replace("-", " "),
                    "description": segment.get("short_description"),
                    "source": "reviewed_segment",
                    "examples": [],
                }
            activities[key]["examples"].append(
                {
                    "video": video,
                    "seq": segment.get("seq"),
                    "t_start": segment.get("t_start"),
                    "t_end": segment.get("t_end"),
                    "frame_start": segment.get("frame_start"),
                    "frame_end": segment.get("frame_end"),
                    "frame_count": segment.get("frame_count"),
                    "confidence": segment.get("confidence"),
                    "description": segment.get("short_description"),
                }
            )

    # Activity atoms come from reviewed segments, and segmentation exists for
    # two of the eleven tasks. For the rest the tab showed zero activities and
    # no subtask structure at all, even though the work plainly has one: the
    # pack's sections ARE the subtasks, and each is filmed as its own clip
    # ("Cut the Tubing" / cut_the_line). Synthesise one activity atom per
    # section so the hierarchy is visible, marked `pack.section` so it is never
    # mistaken for a frame-by-frame review — it carries no reference interval,
    # because nobody has established one.
    if not activities:
        clips = {c["video"]: c for c in frame_candidates(acs)}

        def clip_for(title: str) -> dict | None:
            words = [w for w in re.findall(r"[a-z]{3,}", (title or "").lower())
                     if w not in {"the", "and", "for", "with", "line"}]
            best, best_score = None, 0
            for name, candidate in clips.items():
                names = [w for w in re.findall(r"[a-z]{3,}", name.lower()) if w != "line"]
                score = sum(1 for w in words
                            if any(w.startswith(n[:4]) or n.startswith(w[:4]) for n in names))
                if score > best_score:
                    best, best_score = candidate, score
            return best

        for section in dict.fromkeys(
            s.get("section") for s in steps if s.get("section")
        ):
            member_steps = [s for s in steps if s.get("section") == section]
            clip = clip_for(section)
            variant = member_steps[0].get("variant") or "default"
            atoms.append({
                "id": atom_id("activity", variant, section),
                "source_id": section,
                "kind": "activity",
                "variant": variant,
                "step_id": member_steps[0].get("id"),
                "step_title": section,
                "step_order": step_order.get((variant, member_steps[0].get("id")), -1),
                "label": section,
                "description": (
                    f"{len(member_steps)} procedure steps"
                    + (f", filmed as {clip['video']}" if clip else ", no clip identified")
                ),
                "source": "pack.section",
                "section": section,
                "clip": clip["video"] if clip else None,
                "steps": [s.get("id") for s in member_steps],
                "examples": [],
            })

    atoms.extend(activities.values())
    kind_order = {"activity": 0, "correctness": 1, "defect": 2}
    atoms.sort(
        key=lambda atom: (
            atom.get("variant") or "",
            atom.get("step_order", -1),
            kind_order.get(atom.get("kind"), 9),
            atom.get("label") or "",
        )
    )
    counts = {
        kind: sum(1 for atom in atoms if atom["kind"] == kind)
        for kind in ("activity", "correctness", "defect")
    }
    return {
        "schema_version": 1,
        "source": "derived_from_pack_and_reviewed_segments",
        "task_code": acs,
        "variants": list(variant_meta.values()),
        "atoms": atoms,
        "counts": {**counts, "total": len(atoms)},
        "subtask_count": len(
            {
                (atom.get("variant"), atom.get("step_id"))
                for atom in atoms
                if atom.get("step_id")
            }
        ),
        "notes": [
            "Correctness atoms are compiled pack checks.",
            "Defect atoms are compiled pack error modes.",
            "Activity atoms are unique reviewed segment labels with source intervals as examples.",
            "This is a derived catalog until the task pack schema defines first-class atomic work units.",
        ],
    }


def decision_metrics(items: list[dict]) -> dict:
    """Selective-classification metrics with pass/correct as the positive class."""
    tp = fp = tn = fn = abstained = 0
    abstained_correct = abstained_incorrect = 0
    for item in items:
        truth = item["truth"] == "correct"
        prediction = item.get("prediction")
        if prediction == "pass":
            if truth:
                tp += 1
            else:
                fp += 1
        elif prediction == "fail":
            if truth:
                fn += 1
            else:
                tn += 1
        else:
            abstained += 1
            if truth:
                abstained_correct += 1
            else:
                abstained_incorrect += 1

    total = len(items)
    decided = tp + fp + tn + fn
    actual_correct = tp + fn + abstained_correct
    actual_incorrect = tn + fp + abstained_incorrect

    def ratio(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    return {
        "support": total,
        "decided": decided,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "abstained": abstained,
        "precision": ratio(tp, tp + fp),
        # Abstaining on a correct sample reduces auto-pass recall.
        "recall": ratio(tp, actual_correct),
        # The corresponding safety-oriented metric for incorrect samples.
        "defect_recall": ratio(tn, actual_incorrect),
        "coverage": ratio(decided, total),
        "decided_accuracy": ratio(tp + tn, decided),
        "false_pass_rate": ratio(fp, actual_incorrect),
    }


def evaluation_summary(acs: str, catalog: dict) -> dict:
    """Read labeled datasets and agent run outputs, then compute comparable metrics."""
    datasets = []
    dataset_by_id: dict[str, dict] = {}
    readiness: dict[str, dict] = {
        atom["id"]: {"correct": 0, "incorrect": 0, "total": 0}
        for atom in catalog.get("atoms") or []
    }
    source_to_atom = {
        (atom.get("source_id"), atom.get("variant"), atom.get("step_id")): atom["id"]
        for atom in catalog.get("atoms") or []
    }

    def resolve_atom_id(label: dict) -> str | None:
        direct = label.get("atom_id")
        if direct in readiness:
            return direct
        return source_to_atom.get(
            (label.get("source_id"), label.get("variant"), label.get("step_id"))
        )

    if EVAL_DATASETS_DIR.is_dir():
        for path in sorted(EVAL_DATASETS_DIR.glob("*.json")):
            data = read_json(path)
            if not data or data.get("task_code") != acs:
                continue
            samples = data.get("samples") or []
            atom_labels = 0
            correct = incorrect = 0
            for sample in samples:
                task_label = sample.get("task_label")
                correct += task_label == "correct"
                incorrect += task_label == "incorrect"
                for label in sample.get("atom_labels") or []:
                    atom_id = resolve_atom_id(label)
                    truth = label.get("label")
                    if atom_id and truth in {"correct", "incorrect"}:
                        readiness[atom_id][truth] += 1
                        readiness[atom_id]["total"] += 1
                        atom_labels += 1
                        label["_resolved_atom_id"] = atom_id

            item = {
                "dataset_id": data.get("dataset_id") or path.stem,
                "title": data.get("title") or path.stem,
                "description": data.get("description"),
                "split": data.get("split"),
                "sample_count": len(samples),
                "correct_tasks": correct,
                "incorrect_tasks": incorrect,
                "atom_label_count": atom_labels,
                "samples": samples,
                "_raw": data,
            }
            datasets.append({key: value for key, value in item.items() if key != "_raw"})
            dataset_by_id[item["dataset_id"]] = item

    runs = []
    if EVAL_RUNS_DIR.is_dir():
        for path in sorted(EVAL_RUNS_DIR.glob("*.json")):
            run = read_json(path)
            if not run or run.get("task_code") != acs:
                continue
            dataset = dataset_by_id.get(run.get("dataset_id"))
            if not dataset:
                runs.append(
                    {
                        "run_id": run.get("run_id") or path.stem,
                        "system": run.get("system") or {},
                        "dataset_id": run.get("dataset_id"),
                        "status": "missing_dataset",
                    }
                )
                continue

            predictions = {
                prediction.get("sample_id"): prediction
                for prediction in run.get("predictions") or []
                if prediction.get("sample_id")
            }
            task_items = []
            per_atom: dict[str, list[dict]] = {}
            for sample in dataset["samples"]:
                sample_id = sample.get("sample_id")
                prediction = predictions.get(sample_id) or {}
                if sample.get("task_label") in {"correct", "incorrect"}:
                    task_prediction = prediction.get("task_prediction") or {}
                    task_items.append(
                        {
                            "truth": sample["task_label"],
                            "prediction": task_prediction.get("status"),
                        }
                    )

                predicted_atoms = {
                    item.get("atom_id"): item
                    for item in prediction.get("atom_predictions") or []
                    if item.get("atom_id")
                }
                for truth in sample.get("atom_labels") or []:
                    atom_id = truth.get("_resolved_atom_id") or resolve_atom_id(truth)
                    if not atom_id or truth.get("label") not in {"correct", "incorrect"}:
                        continue
                    atom_prediction = predicted_atoms.get(atom_id) or {}
                    per_atom.setdefault(atom_id, []).append(
                        {
                            "truth": truth["label"],
                            "prediction": atom_prediction.get("status"),
                        }
                    )

            atom_metrics = [
                {"atom_id": atom_id, **decision_metrics(items)}
                for atom_id, items in per_atom.items()
            ]
            runs.append(
                {
                    "run_id": run.get("run_id") or path.stem,
                    "created_at": run.get("created_at"),
                    "system": run.get("system") or {},
                    "dataset_id": run.get("dataset_id"),
                    "thresholds": run.get("thresholds") or {},
                    "status": "complete",
                    "task_metrics": decision_metrics(task_items),
                    "atom_metrics": atom_metrics,
                    "prediction_count": len(predictions),
                }
            )

    def display_path(path: Path) -> str:
        try:
            return str(path.relative_to(ROOT))
        except ValueError:
            return str(path)

    return {
        "schema_version": 1,
        "task_code": acs,
        "datasets": datasets,
        "runs": runs,
        "atom_readiness": readiness,
        "paths": {
            "datasets": display_path(EVAL_DATASETS_DIR),
            "runs": display_path(EVAL_RUNS_DIR),
        },
        "metric_definition": {
            "positive_class": "correct / pass",
            "precision": "Of automatic passes, the fraction truly correct.",
            "recall": "Of truly correct samples, the fraction automatically passed.",
            "defect_recall": "Of truly incorrect samples, the fraction automatically failed.",
            "coverage": "Fraction receiving pass/fail rather than review or insufficient evidence.",
        },
    }


def handbook_sections(acs: str, pack: dict | None) -> list[dict]:
    """The FAA handbook text that governs a task, for display above its procedure.

    The skill sheet says what to do; the handbook says what the result must
    measure up to, and the numeric limits exist only in the latter. Reading the
    procedure without it gives half the standard, so the extract is put in front
    of the procedure rather than filed away under References.

    Provenance rides along: a section located by content search during
    compilation is not something the campus cited, and must not read as though
    it were.
    """
    sections = []
    for reference in ((pack or {}).get("references") or {}).get("handbook") or []:
        text = read_text(TASKS_DIR / acs / (reference.get("file") or ""))
        if not text:
            continue
        sections.append({
            "handbook": reference.get("handbook"),
            "chapter": reference.get("chapter"),
            "pages": reference.get("pages") or [],
            "cited_by_source": bool(reference.get("cited_by_source")),
            "located_by": reference.get("located_by"),
            "file": f"/files/tasks/{acs}/{reference.get('file')}",
            "text": text,
        })
    return sections


def task_detail(acs: str) -> dict | None:
    row = next((r for r in task_rows() if r["acs_code"] == acs), None)
    if row is None:
        return None

    task_dir = TASKS_DIR / acs
    pack, pack_text, pack_error = load_pack(acs)

    references = []
    ref_root = task_dir / "references"
    if ref_root.is_dir():
        for path in sorted(ref_root.rglob("*")):
            if path.is_file():
                rel = path.relative_to(ROOT)
                references.append(
                    {"name": str(path.relative_to(ref_root)), "file": f"/files/{rel}"}
                )

    docs = {}
    for name in ("procedure.md", "ANALYSIS.md"):
        text = read_text(task_dir / name)
        if text:
            docs[name] = text

    atoms = atomic_catalog(acs, pack)
    criteria = read_drafted_criteria(acs)
    # The criterion a check implies belongs beside the check. Joined here rather
    # than in atomic_catalog so the catalog stays a pure view of the pack.
    entries = criteria.get("entries") or {}
    for atom in atoms["atoms"]:
        entry = entries.get(atom.get("step_id")) if atom.get("step_id") else None
        if isinstance(entry, dict) and atom["kind"] == "correctness":
            atom["criterion"] = entry.get("criterion")
            atom["criterion_sources"] = [
                s for s in entry.get("sources") or []
                if isinstance(s, dict) and s.get("condition") == atom.get("label")
            ] or entry.get("sources") or []
            atom["required_framing"] = entry.get("required_framing")
            atom["conflicts"] = entry.get("conflicts") or []
    evaluations = evaluation_summary(acs, atoms)
    for atom in atoms["atoms"]:
        atom["evaluation_samples"] = evaluations["atom_readiness"].get(
            atom["id"], {"correct": 0, "incorrect": 0, "total": 0}
        )

    return {
        "summary": task_summary(row),
        "row": row,
        "pack": pack,
        "pack_text": pack_text,
        "pack_error": pack_error,
        "atoms": atoms,
        "criteria": criteria,
        "handbook_sections": handbook_sections(acs, pack),
        "evaluations": evaluations,
        "steps_json": read_json(task_dir / "steps.json"),
        "sources_json": read_json(task_dir / "sources.json"),
        "docs": docs,
        "references": references,
        "videos": videos_for(acs),
    }


# --------------------------------------------------------- photo assessment


def subtask_key(step: dict) -> str | None:
    """Which subtask a pack step belongs to.

    Its `section` where the pack has one, its `variant` where it does not.
    AM.I.E.S1 was compiled before sections existed: all thirteen of its steps
    carry `section: null` and group by `variant` instead (`bolts_hand`,
    `bolts_pliers`, `turnbuckle_hand`). Reading only `section` left that task
    with no subtask owning any step, so its steps and its sheet-derived
    subtasks sat in two piles that never met.
    """
    return step.get("section") or step.get("variant")


def _photo_checks(step: dict) -> list[dict]:
    """Every check of a step, whatever it is observable through."""
    return [c for c in (step.get("checks") or []) if c.get("statement")]


def _criterion_for_step(step: dict) -> dict:
    """Turn a pack step into its criterion plus provenance.

    Every check goes in, whatever the pack marks it observable through. This
    used to keep only `observable: photo`, on the reasoning that a compound
    criterion is only as gradeable as its least gradeable clause — one pull test
    dragged the whole verdict to `unsure` and the photographable part never got
    assessed. That reasoning belonged to a criterion graded as one blob. Each
    check is now its own call, so a measurement comes back `unsure` on its own
    line and takes nothing else with it, and the rubric is assessed whole rather
    than pre-filtered down to what a camera happens to reach.

    A step with no checks falls back to its instruction text, which describes an
    action rather than an acceptance condition — recorded as a distinct source
    so an action cannot masquerade as a criterion.
    """
    checks = [c for c in (step.get("checks") or []) if c.get("statement")]
    if checks:
        body = "\n".join(f"- {c['statement']}" for c in checks)
        return {"criterion": body, "source": "pack.checks", "excluded": []}
    return {
        "criterion": (step.get("text") or step.get("id") or "").strip(),
        "source": "pack.step_text",
        "excluded": [],
    }


def read_criteria_store(acs: str) -> dict:
    """Load saved criterion edits and match-test variants for a task.

    Accepts a bare string per target (just an edited criterion) as well as the
    full object form, so a store written before variants existed still loads.
    """
    raw = read_json(PHOTO_DIR / acs / "prompts.json") or {}
    store: dict[str, dict] = {}
    for target_id, value in raw.items():
        if isinstance(value, str):
            store[target_id] = {"criterion": value, "variants": [], "negative": None}
        elif isinstance(value, dict):
            variants = [v for v in (value.get("variants") or []) if isinstance(v, dict)]
            negative = value.get("negative")
            store[target_id] = {
                "criterion": value.get("criterion"), "variants": variants,
                # Carried through rather than rebuilt from the two fields that
                # existed when this normaliser was written. An uploaded photo
                # was recorded here by the upload endpoint and then dropped on
                # the next load — and pruned on the next save — so a real
                # assessment photo was written to disk and never graded.
                "upload": value.get("upload"),
                # The negated sheet, its point map and what wrote it. Stored
                # whole rather than as text alone: a control's result is only
                # readable beside the point it negates, and the map is the only
                # thing that pairs them.
                "negative": negative if isinstance(negative, dict)
                            and (negative.get("criterion") or "").strip() else None,
            }
    return store


def write_criteria_store(acs: str, store: dict) -> None:
    # Drop entries that carry neither an edit nor a variant, so clearing an edit
    # leaves the file clean rather than accumulating empty objects.
    pruned = {
        target_id: entry
        for target_id, entry in store.items()
        if (entry.get("criterion") or "").strip() or entry.get("variants")
        or entry.get("negative") or entry.get("upload")
    }
    path = PHOTO_DIR / acs / "prompts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pruned, indent=2) + "\n")


def read_drafted_criteria(acs: str) -> dict:
    """Load `build/criteria/<ACS>.json`, the compiler's attributed criteria.

    Written by packs/compile_pack.py alongside the pack: the pack carries the
    terse check statements, this carries the criterion those imply plus the
    source each condition rests on. Absent for the two hand-compiled tasks,
    which is why every caller falls back to the pack text.
    """
    data = read_json(CRITERIA_DIR / f"{acs}.json") or {}
    entries = data.get("entries")
    return data if isinstance(entries, dict) else {}


def read_subtask_criteria(acs: str) -> dict[str, dict]:
    """Load `criteria/<ACS>/<ACS>__<code>.txt`, the per-subtask grading sheets.

    These are written at the level the work is actually filmed and taught at —
    one sheet per subtask (`bend_the_line`, `flare_the_line`) — and each is a
    complete grading instrument: what to assess, the numbered conditions, the
    critical defects, and how to combine them into a verdict. That is a
    different and better thing than what a subtask target could previously
    show, which was its member steps' criteria concatenated together. A
    concatenation has no notion of the finished subtask, so it could not state
    what the article should look like once the subtask is done.

    Keyed by both the subtask code and its normalised title, because the join
    to a target differs by task: S1's codes are its clip names, while
    AM.II.A.S6 films `flush_patch_1..8` and can only be matched on title.
    """
    entries: dict[str, dict] = {}
    directory = SUBTASK_CRITERIA_DIR / acs
    if not directory.is_dir():
        return entries

    for path in sorted(directory.glob(f"{acs}__*.txt")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        header, _, body = text.partition("=" * 78)
        if not body.strip():
            # No rule bar, so the file is not in the expected shape. Grading
            # against a half-parsed sheet is worse than not offering it.
            continue

        fields = {}
        for line in header.splitlines():
            key, sep, value = line.partition(":")
            if sep and key.strip().isupper():
                fields[key.strip()] = value.strip()
        code = fields.get("SUBTASK CODE") or path.stem.split("__", 1)[-1]

        # The body opens with "<code> — VLM GRADING CRITERIA"; the criterion
        # itself is everything after it up to the provenance footer.
        lines = body.splitlines()
        start = 1 if lines and "VLM GRADING CRITERIA" in lines[0] else 0
        basis_at = next((i for i, l in enumerate(lines) if l.strip() == "Source basis"),
                        len(lines))
        criterion = "\n".join(lines[start:basis_at]).strip()
        if not criterion:
            continue

        # `Notes` record what the sheet's author decided a photograph cannot
        # settle — a flattening percentage, a torque, a pressure reading. They
        # are the sheet's own exclusions, so they belong with the target's
        # other excluded checks rather than inside the text a model grades
        # against, where they would read as conditions to be met.
        notes: list[str] = []
        basis: list[str] = []
        for line in lines[basis_at + 1:]:
            item = line.strip().lstrip("-").strip()
            if not item:
                continue
            label, sep, rest = item.partition(":")
            if sep and label.strip().lower() == "notes":
                # Notes are separated by ".;" and freely contain a plain ";"
                # inside a sentence ("...correct at this stage; flaring is the
                # next subtask."). Splitting on the bare semicolon tore single
                # notes into fragments that read as two half-finished claims
                # about what a photograph can settle, so the split has to be
                # anchored on the sentence end.
                notes.extend(n.strip() for n in re.split(r"(?<=\.);\s*", rest) if n.strip())
            elif sep:
                basis.append(item)

        entry = {
            "code": code,
            "title": fields.get("SUBTASK") or code,
            "criterion": criterion,
            "notes": notes,
            "basis": basis,
            "file": str(path.relative_to(ROOT)),
            "reviewed": False,
        }
        entries[code.lower()] = entry
        title_key = re.sub(r"[^a-z0-9]+", "", entry["title"].lower())
        if title_key:
            entries.setdefault(title_key, entry)
    return entries


SHEET_HEADINGS = ("criteria", "critical defects", "overall decision", "source basis")


def strip_marker(line: str) -> str:
    """A criterion line without its bullet or number, if it had one."""
    match = (re.match(r"\d+[.)]\s+(.+)", line) or re.match(r"[-*•]\s+(.+)", line))
    return (match.group(1) if match else line).strip()


def sheet_checks(criterion: str) -> list[dict]:
    """Split a criterion into the points it will be graded on.

    Sent whole, a criterion comes back as one verdict: you learn the work failed
    and never which of its conditions did. Each point is graded on its own call
    instead, so a failure names itself.

    Two shapes arrive here and both must split. A **subtask sheet** is a
    structured instrument with `Criteria` and `Critical defects` headings. A
    **step criterion** is a bare list of `- ` bullets, which is what
    `compile_pack.py` drafts and what `_criterion_for_step` assembles.

    Only the sheet shape used to split. A step criterion fell through to a
    single call carrying every condition at once, and `apply_thresholds` fails a
    criterion on one failed condition and abstains on one unobservable one — so
    a four-condition step could only pass when all four cleared, which across
    every saved run happened 7 times in 753 calls. Worse, the reasoning that let
    `_criterion_for_step` include `measurement` and `document` checks was that
    each check gets its own call and a measurement "takes nothing else with
    it" — true of sheets, false of steps, so those checks dragged whole steps to
    `unsure`. Splitting the bullet shape is what makes that reasoning true.

    Under a heading, **every line is a condition**, with or without a number or
    a bullet in front of it. The drafted sheets number their conditions, so the
    parser used to require it — and a criterion typed into the browser as a
    bullet list, or as plain lines, produced no points at all. It was not
    reported as unparseable: `sheet_checks` returned nothing, the caller fell
    back to grading the whole criterion in one call, and a ten-condition sheet
    came back as a single verdict that `apply_thresholds` fails on any one
    condition. Three saved criteria in this pilot are written that way.

    Two kinds of point, and they do not have the same polarity. A numbered
    criterion is a condition to be met. A critical defect is the opposite — it
    is a thing whose *presence* fails the work — so grading its wording as
    written would score "the tube is kinked" as a pass when the tube is kinked.
    Defects are restated as absences before they become points, which is the
    whole reason this parser exists rather than a regex at the call site.

    `Overall decision` is a rule for combining points, not a point; the roll-up
    in `handle_photo_run` is what applies it.
    """
    checks: list[dict] = []
    section = None
    for line in (criterion or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        heading = stripped.lower().rstrip(":")
        if heading in SHEET_HEADINGS:
            section = heading
            continue
        if section == "criteria":
            checks.append({"id": f"c{sum(1 for c in checks if not c['defect']) + 1}",
                           "statement": strip_marker(stripped), "defect": False})
        elif section == "critical defects":
            defect = strip_marker(stripped).rstrip(".")
            checks.append({
                "id": f"d{sum(1 for c in checks if c['defect']) + 1}",
                # Phrased so it stays grammatical whatever the defect's own
                # wording is — "No <defect>" breaks on "Tube is kinked".
                "statement": f"The finished work shows no such defect: {defect}.",
                "defect": True,
            })
    if checks or section is not None:
        return checks

    # No headings anywhere, so this is a step criterion: a bare list of
    # conditions, each already one requirement. They are conditions to be met,
    # never defects, so none is restated — a step criterion has no section that
    # inverts polarity, and restating one would negate a condition that was
    # written positively.
    for line in (criterion or "").splitlines():
        stripped = line.strip()
        bullet = re.match(r"[-*•]\s+(.+)", stripped) or re.match(r"\d+[.)]\s+(.+)", stripped)
        if bullet and bullet.group(1).strip():
            checks.append({"id": f"c{len(checks) + 1}",
                           "statement": bullet.group(1).strip(), "defect": False})
    # One point is not a split — it is the whole criterion with a point id
    # attached, and returning it would relabel every single-condition target as
    # a roll-up of one. The caller falls back to grading the criterion whole,
    # which for one condition is the same call either way.
    return checks if len(checks) > 1 else []


def parse_sheet(criterion: str) -> dict:
    """The shape of a criterion, as opposed to the points it grades on.

    `sheet_checks` answers "what will be graded"; this answers "what does the
    instrument look like", which is what a negative sheet has to reproduce. It
    keeps the intro paragraph — it names the article and is the only line saying
    what the photograph is of — the numbered conditions and defect bullets in
    their own sections and in order, and the roll-up rule, so the negation can
    be rebuilt in the original's own shape rather than in whatever shape a model
    felt like emitting.

    A step criterion has no headings at all, just `- ` bullets. It comes back as
    `shape: "bullets"` with everything in `criteria`, and is reassembled the
    same way — a negated step criterion that grew headings would be split
    differently from the criterion it is the control for.
    """
    intro: list[str] = []
    criteria: list[dict] = []
    defects: list[dict] = []
    tail: dict[str, list[str]] = {}
    section = None
    for line in (criterion or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        heading = stripped.lower().rstrip(":")
        if heading in SHEET_HEADINGS:
            section = heading
            tail.setdefault(section, [])
            continue
        if section == "criteria":
            criteria.append({"n": len(criteria) + 1, "text": strip_marker(stripped)})
        elif section == "critical defects":
            defects.append({"n": len(defects) + 1, "text": strip_marker(stripped)})
        elif section is None:
            intro.append(stripped)
        else:
            tail[section].append(stripped)

    if section is not None:
        return {"shape": "sheet", "intro": "\n".join(intro), "criteria": criteria,
                "defects": defects,
                "overall": "\n".join(tail.get("overall decision") or [])}

    # No headings: a bare list of conditions. Anything that is not a bullet is
    # prose above the list and stays as the intro.
    for line in (criterion or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        bullet = re.match(r"[-*•]\s+(.+)", stripped) or re.match(r"\d+[.)]\s+(.+)", stripped)
        if bullet and bullet.group(1).strip():
            criteria.append({"n": len(criteria) + 1, "text": bullet.group(1).strip()})
        else:
            intro.append(stripped)
    return {"shape": "bullets", "intro": "\n".join(intro), "criteria": criteria,
            "defects": [], "overall": ""}


def assemble_negative_sheet(parsed: dict, criteria: dict) -> tuple[str, list]:
    """Rebuild a sheet from negated lines, in the original's own shape.

    Returns the text and the map from each point the splitter will produce back
    to the point it negates. The map cannot be inferred afterwards: ids are
    positional, so a sheet whose second condition was skipped renumbers c3 to
    c2, and reading a control's result against the wrong positive is worse than
    not pairing them at all.

    The defects arrive already inverted, and arithmetically rather than by a
    model. A defect point is graded as "the work shows no such defect", which
    correct work passes; its negation is the assertion that the defect is
    *present*, which correct work fails. That is a mechanical transform of the
    line, so it is done here — and it has to leave the `Critical defects`
    section, because a line under that heading is restated as an absence by the
    splitter and would land back where it started.

    No banner, no "control sheet" heading, nothing inside the graded text that
    says what this is. Statements reach a grader verbatim, and a sheet
    announcing itself as the one the work should fail would be answered by
    reading the label rather than the photograph — which is the behaviour the
    control exists to detect.
    """
    lines: list[str] = []
    mapping: list[dict] = []
    if parsed.get("intro"):
        lines.append(parsed["intro"])

    kept = [(item, criteria[item["n"]]) for item in parsed.get("criteria") or []
            if item["n"] in criteria]
    numbered: list[str] = []
    for original, negated in kept:
        numbered.append(negated["statement"])
        mapping.append({"of": f"c{original['n']}",
                        "kind": negated.get("kind") or "inversion",
                        "changed": negated.get("changed") or "",
                        "positive": original["text"]})
    for original in parsed.get("defects") or []:
        defect = original["text"].rstrip(".")
        numbered.append(f"The finished work shows this defect: {defect}.")
        mapping.append({"of": f"d{original['n']}", "kind": "presence",
                        "changed": "stated as present rather than absent",
                        "positive": original["text"]})

    if numbered:
        if parsed.get("shape") == "sheet":
            lines += ["", "Criteria"]
        else:
            lines.append("")
        for i, statement in enumerate(numbered, start=1):
            lines.append(f"{i}. {statement}" if parsed.get("shape") == "sheet"
                         else f"- {statement}")
            mapping[i - 1]["id"] = f"c{i}"

    if parsed.get("overall"):
        lines += ["", "Overall decision", parsed["overall"]]
    return "\n".join(lines).strip(), mapping


def negative_sheet(acs: str, target: dict, criterion: str | None = None,
                   model: str | None = None, drafter=None) -> dict:
    """Negate one subtask's whole criterion, point for point.

    One call per subtask rather than one per point. The per-point drafter it
    replaces cost seven calls for a seven-point sheet and returned seven loose
    lines that no roll-up could reassemble, so the run could say a control was
    missed but never that the subtask, graded against a criterion that does not
    describe it, still passed.
    """
    drafter = drafter or vlm.draft_negative_sheet
    text = (criterion if criterion is not None else target.get("criterion") or "").strip()
    if not text:
        return {"error": "no_criterion", "criterion": None, "points": [],
                "skipped": [], "cost_usd": 0.0}

    parsed = parse_sheet(text)
    if not parsed["criteria"] and not parsed["defects"]:
        # A single unstructured condition. There is nothing to mirror, and a
        # one-line control is what `negative_variants` already writes.
        return {"error": "unsplittable", "criterion": None, "points": [],
                "skipped": [], "cost_usd": 0.0}

    subject = target.get("step_title") or target.get("label") or ""
    drafted = drafter(model=model, criterion=text, subject=subject,
                      lines=parsed["criteria"])
    spend = drafted.get("cost_usd") or 0.0
    if drafted.get("error"):
        return {"error": drafted["error"], "message": drafted.get("message"),
                "criterion": None, "points": [], "skipped": [], "cost_usd": spend}

    negative, mapping = assemble_negative_sheet(parsed, drafted.get("criteria") or {})
    if not mapping:
        return {"error": "no_points", "criterion": None, "points": [],
                "skipped": drafted.get("skipped") or [], "cost_usd": spend}
    return {"error": None, "criterion": negative, "points": mapping,
            "skipped": drafted.get("skipped") or [],
            "source_points": len(parsed["criteria"]) + len(parsed["defects"]),
            # The exact text this negates, so a criterion edited afterwards can
            # be seen to have left its control behind.
            "of_criterion": text,
            "model": model,
            "drafted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "reviewed_by": None,
            "cost_usd": round(spend, 6)}


def negative_sheets_batch(acs: str, target_ids: list[str], model: str | None = None,
                          edited: dict | None = None, targets: list[dict] | None = None,
                          drafter=None, workers: int = 4) -> dict:
    """Negate a whole selection of subtasks in one pass.

    `edited` carries criteria the operator changed but has not saved: the
    browser is the source of truth for those, and a control drafted from the
    text on disk would be the negation of a criterion nobody is grading.
    """
    edited = edited or {}
    if targets is None:
        pack, _, _ = load_pack(acs)
        targets = photo_targets(acs, pack)
    by_id = {t["target_id"]: t for t in targets}

    def one(target_id: str) -> dict:
        target = by_id.get(target_id)
        if not target:
            return {"target_id": target_id, "error": "unknown_target",
                    "criterion": None, "points": [], "skipped": [], "cost_usd": 0.0}
        return {"target_id": target_id,
                **negative_sheet(acs, target, edited.get(target_id),
                                 model, drafter=drafter)}

    if not target_ids:
        results: list[dict] = []
    else:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(target_ids)))) as pool:
            results = list(pool.map(one, target_ids))
    return {
        "results": results,
        "sheets": sum(1 for r in results if r.get("criterion")),
        "points": sum(len(r.get("points") or []) for r in results),
        # Lines the drafter declined, totalled where an operator will see them:
        # a negative sheet shorter than the criterion it mirrors is a weaker
        # control, and the count is the only warning of that.
        "skipped": sum(len(r.get("skipped") or []) for r in results),
        "cost_usd": round(sum(r.get("cost_usd") or 0 for r in results), 6),
    }


NEGATIVE_SUFFIX = "#negative"


def negative_items(target: dict, base: dict, include: bool = True,
                   inline: object = None) -> list[dict]:
    """Grading points for a subtask's negated sheet.

    Built through `sheet_checks`, like the criterion they mirror, so a negative
    point is one condition and one call — the split the positive side already
    depends on. Without it a negated sheet would arrive as a single call
    carrying every wrong condition at once, and `apply_thresholds` fails a
    criterion on one failed condition, so it would come back `fail` the moment
    any one line landed. That reads like a control working perfectly while
    saying nothing about the other lines.

    Every point expects `fail`. The article is in frame and the wording
    contradicts it, so an abstention is a miss: `unsure` is what a grader
    returns when it cannot see, and a control that cannot be seen has measured
    nothing.
    """
    if not include:
        return []
    if isinstance(inline, str):
        text = inline.strip()
    elif isinstance(inline, dict):
        text = (inline.get("criterion") or "").strip()
    else:
        text = (target.get("negative_criterion") or "").strip()
    if not text:
        return []

    parent = f"{target['target_id']}{NEGATIVE_SUFFIX}"
    label = f"{target['label']} — negated"
    # Which positive point each control negates, where a stored map says so.
    # Ids are positional, so this cannot be recovered by matching c1 to c1 on a
    # sheet that skipped a line — and for the same reason the map is dropped
    # the moment the text stops being the text it was built from. An edit that
    # adds or removes a line shifts every id below it, and a control read
    # against the wrong positive is worse than one left unpaired.
    stored = target.get("negative") or {}
    paired = ({p.get("id"): p for p in (stored.get("points") or []) if isinstance(p, dict)}
              if (stored.get("criterion") or "").strip() == text else {})
    shared = {**base, "polarity": "negative", "negative_of": target["target_id"],
              "expected": "fail"}
    checks = sheet_checks(text)
    if not checks:
        return [{**shared, "target_id": parent, "label": label, "criterion": text}]
    return [{**shared,
             "target_id": f"{parent}::{check['id']}",
             "label": f"{label} · {check['id']}",
             "criterion": check["statement"],
             "rolls_up_to": parent,
             "parent_label": label,
             "parent_criterion": text,
             "check_id": check["id"],
             "negative_of_point": (paired.get(check["id"]) or {}).get("of"),
             "positive_criterion": (paired.get(check["id"]) or {}).get("positive")}
            for check in checks]


def polarity_report(results: list[dict], rollups: list[dict]) -> dict:
    """Pass rates for the criteria and for their negations, side by side.

    The question a positive-only run cannot answer. Every reference frame shows
    work an instructor accepted, so a grader that passes everything and a grader
    that reads produce the same numbers; the negated sheet is the same work
    against a criterion it does not satisfy, and the gap between the two pass
    rates is the part of the result that is about grading rather than about the
    photographs.

    Counted at both levels because they answer different questions. Points say
    how often a single condition was waved through; subtasks say how often the
    roll-up — which is what a student would actually be told — came back `pass`
    against a criterion describing work that is not in the photograph.
    """
    def tally(rows: list[dict]) -> dict:
        graded = [r for r in rows if not r.get("error")]
        passed = sum(1 for r in graded if r.get("verdict") == "pass")
        failed = sum(1 for r in graded if r.get("verdict") == "fail")
        return {
            "points": len(rows), "graded": len(graded), "pass": passed, "fail": failed,
            "unsure": len(graded) - passed - failed,
            "errors": len(rows) - len(graded),
            "pass_rate": round(passed / len(graded), 4) if graded else None,
        }

    def subtasks(rows: list[dict]) -> dict:
        verdicts = Counter(r.get("verdict") for r in rows)
        return {"subtasks": len(rows), "pass": verdicts["pass"],
                "fail": verdicts["fail"], "review": verdicts["review"],
                "pass_rate": round(verdicts["pass"] / len(rows), 4) if rows else None}

    def gap(original: dict, negative: dict) -> float | None:
        if original.get("pass_rate") is None or negative.get("pass_rate") is None:
            return None
        return round(original["pass_rate"] - negative["pass_rate"], 4)

    def paired(rows: list[dict]) -> dict:
        """Negated points whose positive counterpart the photo actually settled.

        The overall gap has a confound that can swallow it whole. These frames
        are footage of work in progress, so a negated line can be *true* of the
        photograph by accident — "a safety wire is missing from one end of the
        turnbuckle" is a correct reading of a frame taken while the first wire
        is still being threaded, and a grader doing its job passes it. Counted
        with the rest, honest readings like that look like a grader agreeing
        with anything.

        So this restricts to the pairs where the same model answered `pass` to
        the positive form of the same condition on the same frame. There the
        photograph demonstrably settles the condition and the work demonstrably
        satisfies it, so the negation is contradicted and `fail` is the only
        correct answer. Anything else is the grader, not the footage.
        """
        positives = {(r.get("model"), r.get("check_id"),
                      (r.get("rolls_up_to") or r.get("target_id") or "")): r
                     for r in rows if (r.get("polarity") or "original") != "negative"
                     and not r.get("error")}
        flipped = held = abstained = 0
        for row in rows:
            if row.get("polarity") != "negative" or row.get("error"):
                continue
            parent = (row.get("negative_of")
                      or (row.get("rolls_up_to") or "").replace(NEGATIVE_SUFFIX, ""))
            positive = positives.get((row.get("model"), row.get("negative_of_point"), parent))
            if not positive or positive.get("verdict") != "pass":
                continue
            if row.get("verdict") == "fail":
                flipped += 1
            elif row.get("verdict") == "pass":
                held += 1
            else:
                abstained += 1
        pairs = flipped + held + abstained
        return {"pairs": pairs, "flipped": flipped, "accepted": held,
                "abstained": abstained,
                "flip_rate": round(flipped / pairs, 4) if pairs else None}

    def side(rows: list[dict], rolls: list[dict], polarity: str) -> tuple[dict, dict]:
        return (tally([r for r in rows if (r.get("polarity") or "original") == polarity]),
                subtasks([r for r in rolls if (r.get("polarity") or "original") == polarity]))

    report: dict = {"models": {}}
    for model in sorted({r.get("model") for r in results if r.get("model")}):
        mine = [r for r in results if r.get("model") == model]
        my_rolls = [r for r in rollups if r.get("model") == model]
        original, original_rolls = side(mine, my_rolls, "original")
        negative, negative_rolls = side(mine, my_rolls, "negative")
        report["models"][model] = {
            "original": original, "negative": negative,
            "original_subtasks": original_rolls, "negative_subtasks": negative_rolls,
            "point_gap": gap(original, negative),
            "subtask_gap": gap(original_rolls, negative_rolls),
            "paired": paired(mine),
        }
    original, original_rolls = side(results, rollups, "original")
    negative, negative_rolls = side(results, rollups, "negative")
    report.update({
        "original": original, "negative": negative,
        "original_subtasks": original_rolls, "negative_subtasks": negative_rolls,
        "point_gap": gap(original, negative),
        "subtask_gap": gap(original_rolls, negative_rolls),
        "paired": paired(results),
    })
    return report


def foreign_criteria(acs: str, limit: int = 400) -> list[dict]:
    """Real criteria points belonging to tasks other than `acs`.

    The third kind of match control, and the only one that costs nothing to
    make: a condition about an article that is simply not in this photograph.
    A grader doing its job answers `unsure` — the subject is absent, not
    violated — so the expectation is `not_pass` rather than `fail`, which is
    exactly the distinction `EXPECTATIONS` was written for.

    Borrowed verbatim rather than invented. A real criterion from a real pack
    reads like the ones around it, so a model cannot pick the control out by
    register, which is the failure a synthetic distractor invites.
    """
    pool: list[dict] = []
    root = SUBTASK_CRITERIA_DIR
    if not root.is_dir():
        return pool
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        if directory.name == acs:
            continue
        for sheet in sorted(read_subtask_criteria(directory.name).values(),
                            key=lambda s: s["code"]):
            for point in sheet_checks(sheet["criterion"]):
                # Defects are already phrased as absences, and an absence about
                # an article that is not present reads as trivially satisfied —
                # the wrong shape for a control. Conditions only.
                if point["defect"]:
                    continue
                pool.append({"task_code": directory.name, "subtask": sheet["code"],
                             "criterion": point["statement"]})
                if len(pool) >= limit:
                    return pool
    return pool


def negative_variants(acs: str, target: dict, points: list[dict], model: str | None = None,
                      kinds: tuple[str, ...] = ("inversion", "substitution", "foreign"),
                      drafter=None) -> tuple[list[dict], float]:
    """Build match-test controls for one target, one control per criteria point.

    Per point rather than per target: a point is already a single condition, so
    negating one at a time keeps the control as sharp as the thing it tests, and
    a miss names the condition it missed.

    Returns the variants and what the drafting cost. Variants are returned, not
    saved — an operator should be able to read a control before it is graded,
    and a generated criterion nobody looked at is exactly the kind of thing that
    quietly becomes a number in a report.
    """
    drafter = drafter or vlm.draft_negative_criteria
    subject = target.get("step_title") or target.get("label") or ""
    variants: list[dict] = []
    spend = 0.0

    if "foreign" in kinds:
        pool = foreign_criteria(acs)
        if pool:
            # Deterministic pick, so re-generating a target does not silently
            # change what it was tested against between two runs.
            seed = sum(map(ord, target["target_id"]))
            borrowed = pool[seed % len(pool)]
            variants.append({
                "id": f"neg-foreign-{seed % len(pool)}",
                "label": f"foreign · {borrowed['task_code']} {borrowed['subtask']}",
                "criterion": borrowed["criterion"],
                "expected": "not_pass",
                "negative_kind": "foreign",
            })

    wanted = tuple(k for k in kinds if k in ("inversion", "substitution"))
    if not wanted or not model:
        return variants, spend

    for point in points:
        drafted = drafter(model=model, criterion=point["statement"], subject=subject)
        spend += drafted.get("cost_usd") or 0
        if drafted.get("error"):
            continue
        for negative in drafted.get("negatives") or []:
            if negative["kind"] not in wanted:
                continue
            variants.append({
                "id": f"neg-{point['id']}-{negative['kind'][:3]}",
                "label": f"{point['id']} {negative['kind']} · {negative['changed']}"[:70],
                "criterion": negative["criterion"],
                "expected": negative["expected"],
                "negative_kind": negative["kind"],
                # What it was made from, so a miss can be read against the
                # positive verdict for the same condition.
                "negative_of": point["id"],
                "positive_criterion": point["statement"],
            })
    return variants, spend


def negative_variants_batch(acs: str, target_ids: list[str], model: str | None = None,
                            kinds: tuple[str, ...] = ("inversion", "substitution", "foreign"),
                            edited: dict | None = None, targets: list[dict] | None = None,
                            drafter=None, workers: int = 4) -> dict:
    """Write controls for a whole selection of targets in one pass.

    `edited` carries criteria the operator has changed but not saved: the
    browser is the source of truth for those, so a control must be drafted from
    what is on screen rather than from what is on disk.

    Targets that yield nothing are reported with a reason rather than dropped —
    a subtask that silently produced no controls reads as one that needs none.
    """
    edited = edited or {}
    if targets is None:
        pack, _, _ = load_pack(acs)
        targets = photo_targets(acs, pack)
    by_id = {t["target_id"]: t for t in targets}

    def one(target_id: str) -> dict:
        target = by_id.get(target_id)
        if not target:
            return {"target_id": target_id, "error": "unknown_target",
                    "variants": [], "points": 0, "cost_usd": 0.0}
        criterion = (edited.get(target_id) or target.get("criterion") or "").strip()
        if not criterion:
            return {"target_id": target_id, "error": "no_criterion",
                    "variants": [], "points": 0, "cost_usd": 0.0}
        points = sheet_checks(criterion) or [{"id": "c1", "statement": criterion,
                                              "defect": False}]
        variants, spend = negative_variants(acs, target, points, model, kinds,
                                            drafter=drafter)
        return {"target_id": target_id, "error": None, "variants": variants,
                "points": len(points), "cost_usd": round(spend, 6)}

    # One drafting call per criteria point, so a task with forty points is forty
    # sequential round trips if this is not fanned out. Targets are independent;
    # points within a target stay in order.
    if not target_ids:
        results: list[dict] = []
    else:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(target_ids)))) as pool:
            results = list(pool.map(one, target_ids))
    return {
        "results": results,
        "points": sum(r["points"] for r in results),
        "variants": sum(len(r["variants"]) for r in results),
        "cost_usd": round(sum(r["cost_usd"] for r in results), 6),
    }


def frame_candidates(acs: str) -> list[dict]:
    """Extracted clips a target with no reviewed frame can be pointed at.

    Most pilot tasks have frames but no segmentation, so nothing maps a frame to
    a step automatically. Rather than leave those targets ungradeable, the
    operator picks the frame — or supplies a real assessment photo later.
    """
    root = FRAME_SETS["detail"] / acs
    if not root.is_dir():
        return []
    out = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        names = frame_names(acs, directory.name, "detail")
        if names:
            out.append({"video": directory.name, "frame_count": len(names),
                        "first_frame": names[0], "last_frame": names[-1]})
    return out


BEST_VIEW_MODEL = "google/gemini-3.6-flash"


def add_best_view(acs: str, target: dict, model: str | None = None,
                  picker=None) -> dict | None:
    """Append the frame of a step that best shows the state it ends in.

    The equidistant sample is blind: it lands where the arithmetic puts it, and
    on head-mounted footage that is regularly a hand or a tool across the
    workpiece. This asks a model which frame of this step's own span actually
    shows the finished article, and adds it to the set — so the grader sees both
    the step's progression and its clearest view of the result.

    It is an addition, never a replacement. The sampled frames establish what
    the step's end state *is*; a picked frame that flattered the work would
    otherwise be the only evidence of it.

    Returns the picker's own result for reporting, or None where there was
    nothing to pick from — an uploaded photo, a hand-picked frame, or a span too
    short to offer a choice.
    """
    if target.get("uploaded") or target.get("frame_picked"):
        return None
    clip, frames = target.get("video"), target.get("frames") or []
    if not clip or not frames:
        return None
    names = frame_names(acs, clip, "detail")
    span = target.get("frame_span")
    if span and len(span) == 2 and names:
        window = names[frame_at(names, span[0]): frame_at(names, span[1]) + 1]
    else:
        window = [f for f in names if f in set(frames)]
    # Nothing to choose between: the sample already is the span.
    if len(window) <= len(frames):
        return None
    step = max(1, len(window) // BEST_VIEW_SAMPLE)
    sampled = window[::step][:BEST_VIEW_SAMPLE]
    if window[-1] not in sampled:
        sampled.append(window[-1])

    picker = picker or vlm.pick_best_frame
    result = picker(
        model=model or BEST_VIEW_MODEL,
        image_paths=[FRAME_SETS["detail"] / acs / clip / n for n in sampled],
        description=target.get("criterion") or target.get("label") or "")
    chosen = result.get("frame")
    # No frame shows the finished article is a real answer on this footage, and
    # the sampled frames still stand. Adding the least bad frame anyway would
    # present a mid-action image as the clearest view of the result.
    if not chosen or chosen in frames:
        return result
    target["frames"] = [*frames, chosen]
    target["frame_urls"] = [*(target.get("frame_urls") or []),
                            f"/files/build/frames/{acs}/{clip}/{chosen}"]
    target["best_view"] = chosen
    return result


def photo_targets(acs: str, pack: dict | None,
                  frames_per_step: int = STEP_FRAMES) -> list[dict]:
    """Build gradeable (frame, criterion) pairs for a task.

    Two sources, in order of precision:

    1. **Reviewed segments.** The final frame of an interval is the completed
       state of that work, so `frame_end` is the frame the target points at.
       This is the richest evidence and its target ids are the keys saved
       criteria and past runs already use, so they are produced unchanged.
    2. **Pack steps.** Segmentation is a separate frame-by-frame review pass and
       exists for two of eleven tasks, so deriving targets only from segments
       left the other nine with an empty tab even once their packs existed. Any
       step no segment covers becomes a target in its own right, with no frame
       attached until one is chosen.

    A target without a frame is still worth having: the criterion is the
    deliverable, and the photo it will grade is usually one a student has yet to
    take.

    `frames_per_step` is how many frames of its own footage a step-level target
    carries. Both sources give a step a span rather than an instant — a reviewed
    interval states its `frame_start` and `frame_end`, and a pack step occupies
    the slice between the previous step's boundary and its own — so both are
    sampled the same way. `frame` remains the last of them, which is the frame
    each target used to carry on its own.
    """
    resolve = segment_step_resolver(pack)
    store = read_criteria_store(acs)
    drafted = (read_drafted_criteria(acs).get("entries") or {})
    candidates = frame_candidates(acs)
    targets: list[dict] = []
    covered_steps: set[str] = set()
    # The reviewed frame each step was last seen finished on. A section whose
    # steps are all segmented has no clip guessed for it — its clip was never a
    # name match — but it does have footage, and the final frame of the last
    # interval covering its work is that footage's account of the finished
    # subtask. That is evidence rather than a guess, so it beats the name-match
    # path below.
    step_frames: dict[str, tuple[str, str]] = {}
    clip_subtasks: dict[str, set[str]] = {}
    saw_clip_level = False

    def with_frames(target: dict, clip: str, window: list[str],
                    span: list[float] | None = None) -> dict:
        """Attach the frames of a step's own footage, sampled across its span.

        The last of them is the target's `frame`, so every reader that wants one
        frame keeps getting the one it got before. `frames` is what the grader is
        actually shown, and it is always non-empty where a frame exists — a
        one-frame target lists itself, rather than making each caller decide
        whether an absent list means one frame or none.
        """
        picked = sample_frames(window, frames_per_step)
        if not picked:
            return target
        target.update({
            "frames": picked,
            "frame_urls": [f"/files/build/frames/{acs}/{clip}/{f}" for f in picked],
            # The span they were drawn from, so the UI can say what "3 frames"
            # covers without recomputing the slice arithmetic.
            "frame_span": span,
            "frame_window": len(window),
        })
        return target

    def apply_store(target: dict) -> dict:
        entry = store.get(target["target_id"]) or {}
        upload = entry.get("upload")
        if upload and (PHOTO_DIR / acs / "uploads" / upload).is_file():
            # An operator-supplied photo beats anything derived from the video —
            # including the sampled frames, which are an attempt to reconstruct
            # from footage the very thing an uploaded photo already is.
            target.update({
                "frame": upload,
                "frame_url": f"/files/build/photo_eval/{acs}/uploads/{upload}",
                "frame_exists": True, "frame_suggested": False, "uploaded": True,
                "upload_path": str(PHOTO_DIR / acs / "uploads" / upload),
                "frames": [upload],
                "frame_urls": [f"/files/build/photo_eval/{acs}/uploads/{upload}"],
                "frame_span": None, "frame_window": None,
            })
        override = (entry.get("criterion") or "").strip()
        target["criterion"] = override or target["criterion_default"]
        target["edited"] = bool(override)
        target["variants"] = entry.get("variants") or []
        negative = entry.get("negative") or None
        target["negative"] = negative
        target["negative_criterion"] = (negative or {}).get("criterion")
        # Whether the stored control still mirrors the criterion on screen. An
        # edited criterion leaves its negation behind describing the previous
        # wording, and grading the two against each other then compares a
        # criterion with the control for a different one.
        target["negative_stale"] = bool(
            negative and (negative.get("of_criterion") or "").strip()
            and (negative.get("of_criterion") or "").strip() != target["criterion"].strip())
        return target

    def drafted_for(step_id: str | None) -> dict:
        entry = drafted.get(step_id) if step_id else None
        return entry if isinstance(entry, dict) else {}

    for segments_path in sorted((ANALYSIS_DIR / acs).glob("*.segments.json")):
        data = read_json(segments_path)
        if not data or not isinstance(data.get("segments"), list):
            continue
        video = data.get("video") or segments_path.name.removesuffix(".segments.json")
        segments = data["segments"]

        # One target per reviewed sub-subtask.
        for segment in segments:
            frame = segment.get("frame_end")
            if not frame:
                continue
            frame_path = FRAME_SETS["detail"] / acs / video / frame
            step, _variant = resolve(video, data, segment)
            if step:
                covered_steps.add(step.get("id"))
                step_frames[step.get("id")] = (video, frame)
                # Which subtask this clip was reviewed as showing. A clip a
                # reviewer already attributed to an existing subtask is another
                # take of it, not a subtask of its own.
                if subtask_key(step):
                    clip_subtasks.setdefault(video, set()).add(subtask_key(step))
                built = _criterion_for_step(step)
                criterion, source, excluded = built["criterion"], built["source"], built["excluded"]
            else:
                # No pack step owns this interval, so there is no acceptance
                # standard for the work in it. There *is* a reviewer's
                # description of the footage — "Static first-person overhead of
                # the workbench: laptop and open handbook at the top…" — and
                # that used to become the criterion, which is not a weaker
                # criterion but a different kind of thing: prose about one
                # moment of a clip, graded against a photograph of another.
                # Across saved runs those 90 targets returned `fail` on 39% of
                # 362 calls, the highest of any source and none of it about
                # workmanship. It is also the circularity `DRAFT_PROMPT` exists
                # to prevent, arriving by a door that prompt does not cover.
                #
                # So the interval is still listed, still carries its
                # description, and still takes a criterion an operator writes —
                # but it is not gradeable until one exists, and says so.
                criterion, source, excluded = "", "segment.description", []
            # A reviewed interval states the frames it spans, so its window is
            # evidence rather than the even-pace guess a pack step gets. Both
            # end on the frame the work was last seen on, which is why both are
            # sampled by the same rule.
            clip_names = frame_names(acs, video, "detail")
            opening = segment.get("frame_start")
            opens_at = clip_names.index(opening) if opening in clip_names else None
            ends_at = clip_names.index(frame) if frame in clip_names else None
            interval = (clip_names[opens_at:ends_at + 1]
                        if opens_at is not None and ends_at is not None and opens_at <= ends_at
                        else [frame])
            target_id = f"{video}:{segment.get('seq')}"
            targets.append(apply_store(with_frames({
                "target_id": target_id,
                # A reviewed interval *inside* a subtask — the code calls these
                # sub-subtasks. It is step-level evidence, not a subtask, and
                # the tab groups it with steps accordingly.
                "kind": "subtask",
                "video": video,
                # The clip this interval was filmed on. Every target names the
                # clip its work appears in, so the frame picker can offer that
                # clip alone rather than all of the task's footage.
                "clip": video,
                "seq": segment.get("seq"),
                "label": (segment.get("substep_label") or "").replace("-", " "),
                "step_id": (step or {}).get("id") or segment.get("step_id"),
                "step_title": (step or {}).get("text") or segment.get("step_title"),
                # Resolved from the pack step the interval was matched to.
                # Without it an interval has no subtask to be listed under, and
                # 150 of them pile up outside the structure of the task.
                "section": subtask_key(step or {}),
                "confidence": segment.get("confidence"),
                "t_end": segment.get("t_end"),
                "frame": frame,
                "frame_url": f"/files/build/frames/{acs}/{video}/{frame}",
                "frame_exists": frame_path.is_file(),
                "description": segment.get("short_description"),
                "criterion_default": criterion,
                "criterion_source": source,
                # Flagged the same way a clip-derived subtask with no sheet is,
                # so the tab already knows how to show it and the run already
                # knows to skip it.
                "needs_criteria": not criterion,
                "frame_candidates": candidates,
            }, video, interval, (
                [round(opens_at / len(clip_names), 3), round((ends_at + 1) / len(clip_names), 3)]
                if opens_at is not None and ends_at is not None and clip_names else None))))

        # A clip that carries reviewed intervals also anchors the subtasks built
        # below: `saw_clip_level` is what tells them a segmented account of this
        # task exists, so a section can take its frame from the interval that
        # covered its last step rather than from a name-matched guess.
        last = segments[-1] if segments else None
        if not (last and last.get("frame_end")):
            continue
        saw_clip_level = True

    # ---------------------------------------------------------- pack-derived
    # Everything above needs a reviewed segment to exist. These do not.

    def suggest_clip(section_title: str) -> str | None:
        """Guess which clip demonstrates a pack section, by name overlap.

        An unsegmented task leaves every step target without a frame, and
        picking one by hand 27 times before you can run anything is enough
        friction to stop the tab being used. Section titles and clip names come
        from the same vocabulary — "Cut the Tubing" against `cut_the_line`,
        "Bending the Tubing" against `bend_the_line` — so a stem match places
        most of them. It is a suggestion, not a segmentation: the operator can
        override it with the frame picker, and the target says the frame was
        suggested rather than reviewed.
        """
        # Three-letter words matter ("Cut the Tubing"), and inflections have to
        # match across the stem boundary ("Bending" against `bend_the_line`), so
        # tokens are compared by shared prefix rather than by equality.
        skip = {"the", "and", "for", "with", "from", "into", "onto", "line"}
        words = [w for w in re.findall(r"[a-z]{3,}", (section_title or "").lower())
                 if w not in skip]
        if not words:
            return None

        def related(a: str, b: str) -> bool:
            return len(a) >= 3 and len(b) >= 3 and (a.startswith(b[:4]) or b.startswith(a[:4]))

        # Sorted, so a tie resolves the same way on every run. Iterating a set
        # here made the winner depend on string hash order, which is randomised
        # per process — the suggested frame could change across restarts, and a
        # criterion that grades differently after a restart is untraceable.
        best, best_score = None, 0
        for clip in sorted({c["video"] for c in candidates}):
            clip_words = [w for w in re.findall(r"[a-z]{3,}", clip.lower()) if w not in skip]
            score = sum(1 for w in words if any(related(w, c) for c in clip_words))
            if score > best_score:
                best, best_score = clip, score
        # With a single clip there is nothing to choose between, so a failed name
        # match is no reason to withhold it. That covers the tasks whose sections
        # are just called "Procedure" — no title can ever match a clip name, and
        # they were the ones left with nothing runnable at all.
        if best is None and len(candidates) == 1:
            return candidates[0]["video"]
        return best

    clip_frames: dict[str, list[str]] = {}

    def frames_of(clip: str) -> list[str]:
        if clip not in clip_frames:
            clip_frames[clip] = frame_names(acs, clip, "detail")
        return clip_frames[clip]

    sheets = read_subtask_criteria(acs)

    # Which clip each sheet was placed on, for the clip-based join below. Filled
    # once the orphan sheets have been placed.
    sheet_by_clip: dict[str, dict] = {}

    def sheet_for(clip: str | None, section_title: str | None) -> dict | None:
        """The grading sheet for a subtask, matched on clip name then title."""
        return (sheets.get((clip or "").lower())
                or sheets.get(re.sub(r"[^a-z0-9]+", "", (section_title or "").lower()))
                # Neither name matched, but both the subtask and a sheet were
                # placed on the same clip, and one clip shows one piece of work.
                # This is the only join AM.I.E.S1 has: its subtask level is the
                # pack's `variant` (`bolts_hand`), and no amount of string
                # matching gets from that to a sheet called "Wire Safety on
                # Bolts by Hand" — but both land on `safety_wire_by_hand`.
                or (sheet_by_clip.get(clip) if clip else None))

    # Which clip each pack section is guessed to come from, resolved once. When
    # several sections land on the same clip they have to share it rather than
    # each spreading over the whole thing, or their steps overlap and the later
    # ones collide on identical frames.
    pack_sections = list(dict.fromkeys(
        subtask_key(s) for s in (pack or {}).get("steps") or [] if subtask_key(s)))
    section_clip = {name: suggest_clip(name) for name in pack_sections}

    # Some campuses name clips after the work (`cut_the_line`), others just
    # number them (`flush_patch_1..8`, `wire_lacing_1..3`). A numbered clip
    # carries nothing for a title to match against, so name matching returns
    # nothing and every section looks like it has no footage — which is
    # indistinguishable, in the output, from footage that never shows finished
    # work. They need different remedies, so they must not be conflated.
    #
    # Where the clips form a numbered series and there is exactly one per
    # section, the correspondence is positional. The equal-count condition is
    # what keeps this from being a wild guess: 8 sections against 8 numbered
    # clips is a filming convention, 4 sections against 3 clips is not, and the
    # latter is left unmatched rather than mapped arbitrarily.
    numbered = sorted(
        (c["video"] for c in candidates if re.search(r"_(\d+)$", c["video"])),
        key=lambda v: int(re.search(r"_(\d+)$", v).group(1)))
    # When every clip is the same word plus an index, the names carry no signal
    # to discriminate on, and any match is an accident of vocabulary — "Create
    # the Patch Filler" hitting `flush_patch_1` on the word "patch" while the
    # other seven sections match nothing. Treating that one accident as a real
    # result blocked the positional reading for all eight, so a same-prefix
    # series is detected and positional wins outright.
    same_prefix = len({re.sub(r"_\d+$", "", v) for v in numbered}) == 1
    if (pack_sections and numbered and same_prefix
            and len(numbered) == len(candidates)
            and len(numbered) == len(pack_sections)):
        section_clip = dict(zip(pack_sections, numbered))
    clip_sections: dict[str, list[str]] = {}
    for name in pack_sections:
        if section_clip.get(name):
            clip_sections.setdefault(section_clip[name], []).append(name)

    # A grading sheet the pack has no section for still describes a finished,
    # photographable piece of work, so it gets a subtask target of its own
    # rather than being dropped on the floor. Two tasks need this and for
    # different reasons: AM.I.E.S1 was hand-compiled before sections existed so
    # every one of its steps carries `section: null`, and AM.II.A.S6 folded
    # "Create the Patch Doubler" in as a note-only heading. In both the sheet is
    # the only record that the subtask exists at all.
    claimed = {sheet["code"] for sheet in
               (sheet_for(section_clip.get(name), name) for name in pack_sections) if sheet}
    by_code = {sheet["code"]: sheet for sheet in sheets.values()}
    unclaimed = [sheet for code, sheet in by_code.items() if code not in claimed]

    # Where every clip is the same word plus an index, the names carry no signal
    # to discriminate on and a name match is the accident described above — so a
    # sheet-only subtask is left without a clip rather than pinned to
    # `flush_patch_1`. Everywhere else its title goes through the same matcher a
    # pack section's title does, which is what places AM.I.E.S1's three sheets
    # on the clips that actually demonstrate them.
    titles_carry_signal = not (numbered and same_prefix and len(numbered) == len(candidates))
    placements = [(sheet, suggest_clip(sheet["title"]) if titles_carry_signal else None)
                  for sheet in unclaimed]
    for sheet, clip in placements:
        if clip:
            sheet_by_clip.setdefault(clip, sheet)

    # A sheet that landed on the same clip as a pack subtask is that subtask's
    # sheet, not a subtask of its own — `sheet_for` will now find it by clip.
    # Emitting it separately as well would list the same piece of work twice:
    # once as `bolts_hand` with AM.I.E.S1's four steps under it, and again as
    # "Wire Safety on Bolts by Hand" with none, both graded and both billed.
    section_clips = {section_clip.get(name) for name in pack_sections}
    orphan_sheets = [sheet for sheet, clip in placements
                     if not (clip and clip in section_clips)]
    for sheet, clip in placements:
        if sheet not in orphan_sheets:
            continue
        section_clip[sheet["title"]] = clip
        if clip:
            clip_sections.setdefault(clip, []).append(sheet["title"])

    def suggest_frame(clip: str, fraction: float) -> str | None:
        """The frame `fraction` of the way through a clip.

        A clip's final frame is the closest thing it has to a finished state, so
        it is right for whatever work ends there. It is wrong for everything
        before it: giving all three steps of "Cut the Tubing" the clip's last
        frame grades "Decide the size of tubing to use" against a photo of
        tubing already cut. That reads as a confident failure on a step the
        student performed correctly, which is worse than having no frame at all,
        because a wrong verdict is harder to notice than a missing one.

        So work is laid out along the clip in the order the procedure performs
        it: sections sharing a clip take successive slices of it, and the steps
        of a section subdivide their own slice. It remains a guess assuming an
        even pace — `frame_suggested` says so and the picker overrides it — but
        it is a guess that moves through the work rather than standing still at
        the end of it.
        """
        names = frames_of(clip)
        if not names:
            return None
        return names[frame_at(names, fraction)]

    def blank_frame(target: dict, section_title: str | None = None,
                    position: tuple[int, int] | None = None) -> dict:
        """A target with a criterion and, where one can be guessed, a frame."""
        target.update({"video": None, "seq": None, "confidence": None, "t_end": None,
                       "frame": None, "frame_url": None, "frame_exists": False,
                       "frame_candidates": candidates})
        clip = section_clip.get(section_title) if section_title else None
        # Recorded whether or not a frame can be guessed from it. A step belongs
        # to the clip its section was matched to, and that is what the frame
        # picker must offer — without it a step fell back to every clip in the
        # task, so picking a frame for "Cut the Tubing, step 2" meant scrolling
        # 1593 frames of seven unrelated subtasks to find the ~160 that could
        # possibly show it.
        target.setdefault("clip", clip)
        if not clip:
            return target

        # The section's own slice of its clip, then the step's slice of that.
        peers = clip_sections.get(clip) or [section_title]
        share = peers.index(section_title) if section_title in peers else 0
        index, count = position or (1, 1)
        fraction = (share + (index / count if count else 1)) / len(peers)
        # Where that slice *starts*, which the single-frame path never needed.
        # A step runs from the end of the previous step's slice to its own, and
        # that span is the footage of this step — the frames the grader is shown
        # are sampled across it rather than taken from its final instant alone.
        opening = (share + ((index - 1) / count if count else 0)) / len(peers)

        frame = suggest_frame(clip, fraction)
        if not frame:
            return target
        path = FRAME_SETS["detail"] / acs / clip / frame
        target.update({
            "video": clip, "frame": frame,
            "frame_url": f"/files/build/frames/{acs}/{clip}/{frame}",
            "frame_exists": path.is_file(),
            "frame_suggested": True,
            # What the guess was based on, so the UI can show its reasoning
            # rather than implying someone chose this frame.
            "frame_position": list(position) if position else None,
            "frame_share": [share + 1, len(peers)] if len(peers) > 1 else None,
            "frame_fraction": round(fraction, 3),
        })
        # Steps only. A subtask target is graded against the finished article,
        # and its slice of the clip spans the whole subtask — sampling across
        # that would hand the grader three moments of work in progress to weigh
        # against a criterion written about the completed result. A step has no
        # such thing as a completed result: it is a slice of work by
        # construction, which is why the frames of its own span are the best
        # account of it available.
        if target.get("kind") != "step":
            return target
        names = frames_of(clip)
        window = names[frame_at(names, opening): frame_at(names, fraction) + 1]
        return with_frames(target, clip, window, [round(opening, 3), round(fraction, 3)])

    # One target per pack section — the level the work is actually filmed and
    # taught at. Without these the tab jumped straight from individual steps to
    # a whole-task roll-up, and the subtasks the task is made of ("Cut the
    # Tubing", "Bending the Tubing") had no representation anywhere.
    sections = dict.fromkeys(
        subtask_key(s) for s in (pack or {}).get("steps") or [] if subtask_key(s)
    )
    # The subtasks to emit: every pack section, plus every grading sheet no pack
    # section corresponds to. Each row carries the sheet it will be graded
    # against, resolved once here so the suppression rule below can see it.
    subtask_rows: list[tuple[str, list[dict], dict | None]] = []
    for section in sections:
        member_steps = [s for s in (pack or {}).get("steps") or []
                        if subtask_key(s) == section]
        # A reviewed segment target grades one interval *inside* the work; the
        # sheet grades the finished subtask those intervals add up to. They are
        # different questions, so a fully segmented section still needs its
        # sheet — dropping it cost AM.II.K.S3 three of its five subtasks, which
        # were the three its segmentation pass happened to cover completely.
        if not sheet_for(section_clip.get(section), section) and all(
                s.get("id") in covered_steps for s in member_steps):
            continue
        subtask_rows.append((section, member_steps, None))
    subtask_rows.extend((sheet["title"], [], sheet) for sheet in orphan_sheets)

    section_targets: list[dict] = []
    # A subtask whose title matched no clip still has footage — the campus filmed
    # it, the name simply carries no signal ("Cut The Hose" against
    # `flex_hose_2`). Clips are numbered in filming order and pack subtasks are
    # listed in procedure order, so the unclaimed clips fall to the unplaced
    # subtasks in sequence. Done before the targets are built, because a subtask
    # given its clip here also gets that clip's frame rather than none.
    #
    # Order is what keeps this honest: the pointer only ever moves forward, so a
    # subtask can be given a clip that comes after the previous subtask's and
    # never one that comes before it. Without this pass an unmatched subtask sat
    # frameless while its own clip was picked up as a subtask of its own — S7
    # reported seven subtasks for six pieces of work, one of them a duplicate.
    ordered_clips = [c["video"] for c in candidates]
    taken: set[str] = set()
    cursor = 0
    for section, _members, _orphan in subtask_rows:
        wanted = section_clip.get(section)
        # A clip a previous subtask already holds is not available to this one.
        # Both of S7's sections match `flex_hose_1` on the word "hose", and
        # letting them share it left five clips unclaimed and two subtasks
        # pointing at the same footage — the second graded against a photo of
        # the first one's work.
        if wanted and wanted in ordered_clips and wanted not in taken:
            taken.add(wanted)
            cursor = max(cursor, ordered_clips.index(wanted) + 1)
            continue
        nxt = next((c for c in ordered_clips[cursor:]
                    if c not in taken and c not in clip_subtasks), None)
        if not nxt:
            # Nothing left to give it. Better clipless than pinned to footage of
            # another subtask's work.
            section_clip[section] = None
            continue
        section_clip[section] = nxt
        taken.add(nxt)
        cursor = ordered_clips.index(nxt) + 1

    # Rebuilt from the final assignment rather than patched alongside it. The
    # first version appended to it and left the old entry in place, so S7's two
    # sections still both counted as sharing `flex_hose_1` — each took half the
    # clip, and "Determine the Distance and Hose" ended at its midpoint while
    # its own clip's second half showed the next subtask's work.
    clip_sections.clear()
    for name, _members, _orphan in subtask_rows:
        if section_clip.get(name):
            clip_sections.setdefault(section_clip[name], []).append(name)

    for section, member_steps, orphan in subtask_rows:
        statements = []
        for member in member_steps:
            entry = drafted_for(member.get("id"))
            text = (entry.get("criterion") or "").strip()
            statements.extend(
                line for line in (text.splitlines() if text
                                  else [f"- {c['statement']}"
                                        for c in _photo_checks(member)])
                if line.strip()
            )
        clip = section_clip.get(section)
        # The subtask's own grading sheet, matched on its clip name first and
        # its title second. It wins over the concatenated step criteria: it is
        # written about the finished subtask rather than assembled from the
        # steps that lead to it, so it can say what the article should look
        # like once the subtask is done — which is the question a subtask
        # target exists to ask.
        sheet = orphan or sheet_for(clip, section)
        if not (statements or sheet):
            continue
        # A section whose every step was segmented has real footage even when no
        # clip name matched its title — AM.II.K.S3 films `elect_conn_2..5`, which
        # no subtask title can ever match. The last interval covering its work
        # names both the clip and the frame that work finished on, so it settles
        # what the name match could not.
        # It also overrules a clip the name match guessed. `elect_conn_2` won
        # "Insert the Pin into the Electrical Connector" on the words electrical
        # and connector, but the work was filmed in `elect_conn_5` — and a
        # subtask whose frame comes from one clip while its clip says another
        # gets filed under the wrong roll-up, beside a finished article it had
        # no part in.
        reviewed = next((step_frames[s["id"]] for s in reversed(member_steps)
                         if s.get("id") in step_frames), None)
        if reviewed:
            clip = reviewed[0]
        # Led by the clip name. The subtasks of a task are known by what the
        # footage is called — `bend_the_line`, `flare_the_line` — and a list
        # ordered by section title alone made you translate every row back to
        # the clip it came from before you could tell which subtask it was.
        # A sheet-only subtask has no pack steps to count, and "(0 steps)" would
        # read as a section that lost its steps rather than one the pack never
        # had; it is named for the sheet it comes from instead.
        steps_note = f"({len(member_steps)} steps)" if member_steps else "(criteria sheet)"
        label = (f"{clip} — {section} {steps_note}" if clip
                 else f"{section} — subtask {steps_note}")
        section_target = blank_frame({
            "target_id": f"section:{re.sub(r'[^a-z0-9]+', '-', section.lower()).strip('-')}",
            # Not "subtask": that kind already means a reviewed sub-subtask
            # interval, which carries a real frame and a confidence. A section
            # is a pack-derived grouping with neither.
            "kind": "section",
            "label": label,
            "step_id": None,
            "step_title": section,
            "section": section,
            "clip": clip,
            "step_count": len(member_steps),
            "description": (sheet["title"] if sheet else
                            f"All photo-gradeable conditions across {len(member_steps)} steps."),
            "criterion_default": (sheet["criterion"] if sheet
                                  else "\n".join(dict.fromkeys(statements))),
            "criterion_source": "criteria.subtask" if sheet else "drafted.section",
            # Provenance stays attached to the criterion it belongs to, so a
            # sheet's claims remain traceable to the procedure and handbook
            # pages they were drafted from.
            "criterion_file": sheet["file"] if sheet else None,
            "criterion_basis": sheet["basis"] if sheet else [],
            "criterion_reviewed": bool(sheet and sheet["reviewed"]),
            "framing": None,
            "sources": [],
            "conflicts": [],
        }, section)
        # Wherever it exists, including over a frame the name match already
        # suggested. That suggestion is an even-pace guess at where a section
        # sits inside its clip; this is a reviewer saying where the work ended.
        # Sections sharing a clip keep their spread either way, since each
        # resolves its own last covered step rather than the clip's endpoint.
        if reviewed:
            video, frame = reviewed
            section_target.update({
                "video": video, "frame": frame,
                "frame_url": f"/files/build/frames/{acs}/{video}/{frame}",
                "frame_exists": (FRAME_SETS["detail"] / acs / video / frame).is_file(),
                # Not `frame_suggested`: this is where a reviewer said the work
                # ended, not an even-pace guess at where it might have. Both
                # keys are set so a frame always says which of the two it is.
                "frame_suggested": False,
                "frame_reviewed": True,
            })
        section_target = apply_store(section_target)
        # Parsed from the *effective* criterion, so an operator's saved edit is
        # split into its own points rather than reverting the subtask to a
        # single call the moment it is edited.
        section_target["checks"] = sheet_checks(section_target["criterion"])
        section_targets.append(section_target)
        targets.append(section_target)

    # One subtask per clip. A campus films one clip per piece of work, so a clip
    # with no subtask is a piece of work the paperwork does not mention: on
    # AM.I.D.S7 the procedure sheet stops after "Cut The Hose" while the footage
    # goes on to mark the hose, fit both end fittings and pressure-test it. Four
    # subtasks existed only as video, and a tab built from the pack alone
    # reported the task as two subtasks long.
    #
    # These carry no criterion. There is no sheet and no pack section behind
    # them, and writing one here would be inventing an acceptance standard for
    # aircraft maintenance out of a video frame. The target exists so the work
    # is visible and so a criterion can be authored against it in the tab; until
    # one is, it says so and cannot be graded.
    claimed_clips = {t["clip"] for t in section_targets if t.get("clip")}
    for candidate in candidates:
        clip = candidate["video"]
        # A clip a reviewer already attributed to a subtask is another take of
        # that subtask, not a new one. AM.I.E.S1 films `safety_wire_pliers_1..4`
        # and the segmentation pass places all five steps of `bolts_pliers` in
        # every one of them — four takes of one subtask, and splitting them into
        # four would quadruple that subtask's weight in any run.
        if clip in claimed_clips or clip in clip_subtasks:
            continue
        frames = frames_of(clip)
        if not frames:
            continue
        frame = frames[-1]
        targets.append(apply_store({
            "target_id": f"clip:{clip}",
            "kind": "section",
            "label": f"{clip} — subtask (no criteria sheet)",
            "step_id": None,
            "step_title": clip,
            "section": clip,
            "clip": clip,
            "step_count": 0,
            "video": clip,
            "seq": None,
            "confidence": None,
            "t_end": None,
            "frame": frame,
            "frame_url": f"/files/build/frames/{acs}/{clip}/{frame}",
            "frame_exists": (FRAME_SETS["detail"] / acs / clip / frame).is_file(),
            # The clip's last frame is the closest thing it has to finished
            # work, and it was not guessed from a name match — it is simply
            # where this clip ends. Neither suggested nor reviewed.
            "frame_suggested": False,
            "frame_reviewed": False,
            "frame_candidates": candidates,
            "description": None,
            "criterion_default": "",
            "criterion_source": "none",
            "criterion_file": None,
            "criterion_basis": [],
            "criterion_reviewed": False,
            "needs_criteria": True,
            "framing": None,
            "sources": [],
            "conflicts": [],
            "checks": [],
        }))

    # Position of each step within its own section, so a suggested frame can
    # advance through the clip rather than every step sharing its last frame.
    section_members: dict[str, list[str]] = {}
    for step in (pack or {}).get("steps") or []:
        if step.get("id"):
            section_members.setdefault(subtask_key(step) or "", []).append(step["id"])

    for step in (pack or {}).get("steps") or []:
        if not step.get("id") or step["id"] in covered_steps:
            continue
        built = _criterion_for_step(step)
        entry = drafted_for(step["id"])
        members = section_members.get(subtask_key(step) or "", [])
        position = ((members.index(step["id"]) + 1, len(members))
                    if step["id"] in members else None)
        # The compiled criterion is preferred where it exists: it was drafted
        # from the procedure sheet and the handbook together, so it carries the
        # numeric standards the one-line pack checks summarise away. The pack
        # text remains the fallback, which is what the two hand-compiled tasks
        # use since nothing drafted them.
        criterion = (entry.get("criterion") or "").strip()
        targets.append(apply_store(blank_frame({
            "target_id": f"step:{step['id']}",
            "kind": "step",
            "label": f"{step['id']} — {step.get('text') or step['id']}",
            "step_id": step["id"],
            "step_title": step.get("text"),
            "section": subtask_key(step),
            "description": entry.get("step_text") or step.get("text"),
            "criterion_default": criterion or built["criterion"],
            "criterion_source": "drafted.step" if criterion else built["source"],
            "framing": entry.get("required_framing"),
            "sources": entry.get("sources") or [],
            "conflicts": entry.get("conflicts") or [],
        }, subtask_key(step), position)))

    return targets


class Handler(SimpleHTTPRequestHandler):
    # Quiet by default; the console is for the operator, not a request log.
    def log_message(self, *args) -> None:  # noqa: D102
        pass

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            return self.send_file(STATIC / "index.html")
        if path.startswith("/static/"):
            return self.send_file(STATIC / path[len("/static/") :])
        if path.startswith("/vendor/"):
            return self.send_file(VENDOR / path[len("/vendor/") :])
        if path.startswith("/api/"):
            return self.handle_api(path[len("/api/") :], query)
        if path.startswith("/files/"):
            return self.send_file(ROOT / path[len("/files/") :], allow_range=True)
        self.send_error(404)

    def handle_api(self, route: str, query: dict) -> None:
        parts = [p for p in route.split("/") if p]

        if parts == ["tasks"]:
            return self.send_json([task_summary(r) for r in task_rows()])

        if len(parts) == 2 and parts[0] == "task":
            detail = task_detail(parts[1])
            return self.send_json(detail) if detail else self.send_error(404)

        if len(parts) == 3 and parts[0] == "segments":
            path = ANALYSIS_DIR / parts[1] / f"{parts[2]}.segments.json"
            data = read_json(path)
            return self.send_json(data) if data else self.send_error(404)

        if parts == ["photo", "models"]:
            return self.send_json({
                "models": vlm.MODELS,
                "defaults": vlm.DEFAULT_MODELS,
                "key": vlm.key_status(),
                "system_prompt": vlm.SYSTEM_PROMPT,
            })

        if len(parts) == 2 and parts[0] == "criteria":
            return self.send_json(read_drafted_criteria(parts[1]))

        if len(parts) == 3 and parts[0] == "photo" and parts[1] == "targets":
            acs = parts[2]
            pack, _, _ = load_pack(acs)
            frames_per_step = clamp_frames_per_step((query.get("frames") or [None])[0])
            return self.send_json({
                "task_code": acs,
                "frames_per_step": frames_per_step,
                "targets": photo_targets(acs, pack, frames_per_step),
            })

        if len(parts) == 3 and parts[0] == "photo" and parts[1] == "runs":
            acs = parts[2]
            # `limit` exists so the browser can restore just the last run on
            # load. A run holds one result per criteria point per model —
            # hundreds of objects — so reading every run off disk to show the
            # newest gets slower with each run performed.
            try:
                limit = int((query.get("limit") or ["0"])[0])
            except ValueError:
                limit = 0
            paths = sorted((PHOTO_DIR / acs).glob("run_*.json"), reverse=True)
            if limit > 0:
                paths = paths[:limit]
            runs = []
            for path in paths:
                data = read_json(path)
                if data:
                    runs.append(data)
            return self.send_json({"task_code": acs, "runs": runs})

        if len(parts) == 3 and parts[0] == "frames":
            acs, video = parts[1], parts[2]
            which = (query.get("set") or ["detail"])[0]
            names = frame_names(acs, video, which)
            base = "frames" if which == "detail" else "index"
            return self.send_json(
                {
                    "set": which,
                    "count": len(names),
                    "base": f"/files/build/{base}/{acs}/{video}",
                    "frames": names,
                }
            )

        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if not path.startswith("/api/"):
            return self.send_error(404)
        parts = [p for p in path[len("/api/") :].split("/") if p]

        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self.send_error(400, "Malformed JSON body")
        if not isinstance(body, dict):
            return self.send_error(400, "Body must be a JSON object")

        # Save or clear an edited criterion. Sending an empty string restores
        # the pack-derived default rather than grading against empty text.
        if len(parts) == 3 and parts[0] == "photo" and parts[1] == "prompts":
            acs = parts[2]
            target_id = body.get("target_id")
            if not target_id:
                return self.send_error(400, "target_id is required")
            store = read_criteria_store(acs)
            entry = store.get(target_id) or {"criterion": None, "variants": []}
            if "criterion" in body:
                entry["criterion"] = (body.get("criterion") or "").strip() or None
            if "variants" in body:
                entry["variants"] = [
                    {
                        "id": str(v.get("id") or f"v{i + 1}"),
                        "label": (v.get("label") or f"variant {i + 1}").strip(),
                        "criterion": (v.get("criterion") or "").strip(),
                        # What the author believes the answer should be. Left
                        # null when they genuinely do not know — an unscored
                        # probe is still worth running.
                        "expected": v.get("expected") if v.get("expected") in vlm.EXPECTATIONS else None,
                        # Provenance for a generated control: which kind it is,
                        # and which criteria point it was made from. A miss is
                        # only readable against the positive verdict for the
                        # same condition, and without these the two cannot be
                        # put side by side.
                        **{k: v[k] for k in
                           ("negative_kind", "negative_of", "positive_criterion")
                           if v.get(k)},
                    }
                    for i, v in enumerate(body.get("variants") or [])
                    if isinstance(v, dict) and (v.get("criterion") or "").strip()
                ]
            # The negated sheet, saved the same way an edited criterion is: it
            # is a criterion, graded by the same splitter against the same
            # frame, and the only thing that makes it a control is the verdict
            # it is expected to earn.
            if "negative" in body:
                negative = body.get("negative")
                if isinstance(negative, dict) and (negative.get("criterion") or "").strip():
                    entry["negative"] = {
                        "criterion": negative["criterion"].strip(),
                        "points": [p for p in (negative.get("points") or [])
                                   if isinstance(p, dict)],
                        "skipped": negative.get("skipped") or [],
                        "of_criterion": (negative.get("of_criterion") or "").strip() or None,
                        "model": negative.get("model"),
                        "drafted_at": negative.get("drafted_at"),
                        "reviewed_by": negative.get("reviewed_by"),
                    }
                else:
                    entry["negative"] = None
            store[target_id] = entry
            write_criteria_store(acs, store)
            saved = read_criteria_store(acs).get(target_id) or {}
            return self.send_json({
                "saved": True,
                "target_id": target_id,
                "edited": bool((saved.get("criterion") or "").strip()),
                "variants": saved.get("variants") or [],
                "negative": saved.get("negative"),
            })

        if parts == ["photo", "draft"]:
            acs = body.get("task_code")
            target_id = body.get("target_id")
            model = body.get("model") or vlm.DEFAULT_MODELS[0]
            if not acs or not target_id:
                return self.send_error(400, "task_code and target_id are required")

            pack, _, _ = load_pack(acs)
            target = next(
                (t for t in photo_targets(acs, pack) if t["target_id"] == target_id), None
            )
            if not target:
                return self.send_error(404, "Unknown target")

            # The procedure defines what "correct" means, so the source is the
            # full normalized skill sheet — Step Instructions *and* Senior
            # Mechanic Notes, where the acceptance detail actually lives. The
            # bare pack step line ("Inspect the termination.") carries almost
            # none of that and produced thin criteria. The sheets are a few KB,
            # so the whole document goes in and the target step is named to
            # focus it.
            steps_by_id = {s.get("id"): s for s in (pack or {}).get("steps") or []}
            step = steps_by_id.get(target.get("step_id"))
            sheet = read_text(TASKS_DIR / acs / "procedure.md")

            sections = []
            sources = []
            if sheet:
                sections.append(f"FULL PROCEDURE SHEET\n{sheet.strip()}")
                sources.append("procedure.md")

            # The skill sheet says what to do; the handbook says what the
            # finished work must measure up to. Numeric standards — 6-8 twists
            # per inch, pigtail turns, wrap direction — live in the handbook and
            # nowhere else, so a criterion drafted without it has no numbers to
            # hold a student to.
            for reference in ((pack or {}).get("references") or {}).get("handbook") or []:
                text = read_text(TASKS_DIR / acs / (reference.get("file") or ""))
                if not text:
                    continue
                pages = ", ".join(reference.get("pages") or [])
                # Provenance travels with the text. AM.I.E.S1's handbook link was
                # located during compilation rather than cited by AIM's sheet,
                # and a criterion resting on an inferred reference must be
                # reviewable as such rather than passing for an AIM standard.
                if reference.get("cited_by_source"):
                    provenance = "cited by the AIM procedure sheet"
                else:
                    provenance = ("NOT cited by the AIM procedure sheet — located during "
                                  "pack compilation and flagged assumed; treat any standard "
                                  "taken from it as provisional and say so")
                sections.append(
                    f"REFERENCE HANDBOOK — {reference.get('handbook')} "
                    f"pages {pages} ({provenance})\n{text.strip()}"
                )
                sources.append(f"{reference.get('handbook')} {pages}")

            # Nine of the eleven pilot tasks have no compiled pack and so cite
            # no handbook at all. Rather than draft a criterion with no
            # authoritative standard behind it, locate the relevant pages by
            # content search — scoped to the handbook for this task's subject,
            # because "safety wire" appears in all three and only the General
            # handbook is the one a General task is taught from.
            if body.get("search_handbook", True) and not sources[1:]:
                row = next((r for r in task_rows() if r["acs_code"] == acs), {})
                subject = row.get("subject") or ""
                keys = [handbook_search.SUBJECT_HANDBOOK.get(subject)] if subject else []
                keys = [k for k in keys if k] or None
                query = " ".join(filter(None, [
                    row.get("task"), (step or {}).get("text"),
                ]))
                try:
                    hits = handbook_search.search(query, keys, top=2)
                except FileNotFoundError:
                    hits = []
                for hit in hits:
                    pages = ", ".join(p for p in (hit.get("labels") or []) if p)
                    sections.append(
                        f"REFERENCE HANDBOOK — {hit['name']} "
                        f"{'pages ' + pages if pages else 'pdf pages ' + str(hit['pdf_indices'])} "
                        "(located by CONTENT SEARCH from the task title, not cited by the "
                        "procedure sheet and not reviewed by an SME — treat every standard "
                        "taken from it as provisional and say so)\n"
                        f"{hit['text'].strip()}"
                    )
                    sources.append(f"{hit['handbook']} {pages or hit['pdf_indices']} (searched)")

            source = "+".join(sources) if sources else "none"

            if step:
                focus = [f"Step being graded — {step.get('id')}: {step.get('text')}"]
                for check in step.get("checks") or []:
                    focus.append(
                        f"Existing check ({check.get('observable')}): {check.get('statement')}"
                    )
                sections.append("\n".join(focus))
                source = f"{source}+pack.step"
            elif target.get("description"):
                sections.append(
                    "Step being graded is not mapped to a numbered procedure step. "
                    f"Reviewed activity: {target['description']}"
                )
                source = f"{source}+segment.description"

            procedure = "\n\n".join(sections)

            # Most targets have no frame: segmentation is what pins a frame to a
            # step and only two tasks have it. Drafting from the procedure and
            # handbook alone is the normal case, not a degraded one — the photo
            # only ever told the model what a camera could resolve.
            frame_path = (
                FRAME_SETS["detail"] / acs / target["video"] / target["frame"]
                if target.get("frame") and target.get("video") else None
            )
            result = vlm.draft_criterion(
                model=model,
                image_path=frame_path if frame_path and frame_path.is_file() else None,
                procedure=procedure,
                title=(pack or {}).get("title"),
            )
            return self.send_json({
                "target_id": target_id,
                "procedure_used": procedure,
                "procedure_source": source,
                **result,
            })

        # A student's own photograph is the real artifact this pilot is about;
        # a sampled video frame is only ever a stand-in for one. Uploads land
        # beside the runs, and are pinned to a target exactly like a picked frame.
        if len(parts) == 3 and parts[0] == "photo" and parts[1] == "upload":
            acs = parts[2]
            target_id = body.get("target_id")
            data_url = body.get("data") or ""
            filename = re.sub(r"[^A-Za-z0-9._-]+", "_", body.get("filename") or "upload")
            if not target_id or not data_url.startswith("data:image/"):
                return self.send_error(400, "target_id and a data:image/... payload are required")
            try:
                header, _, payload = data_url.partition(",")
                raw = base64.b64decode(payload, validate=True)
            except Exception:
                return self.send_error(400, "Payload is not valid base64")
            if len(raw) > 20 * 1024 * 1024:
                return self.send_error(413, "Image exceeds 20 MB")
            suffix = ".png" if "image/png" in header else ".jpg"
            if not filename.lower().endswith((".jpg", ".jpeg", ".png")):
                filename += suffix
            destination = PHOTO_DIR / acs / "uploads" / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)

            store = read_criteria_store(acs)
            entry = store.get(target_id) or {"criterion": None, "variants": []}
            entry["upload"] = filename
            store[target_id] = entry
            write_criteria_store(acs, store)
            return self.send_json({
                "saved": True, "target_id": target_id, "frame": filename,
                "frame_url": f"/files/build/photo_eval/{acs}/uploads/{filename}",
                "bytes": len(raw),
            })

        if len(parts) == 3 and parts[0] == "photo" and parts[1] == "best-frame":
            acs = parts[2]
            target_id = body.get("target_id")
            pack, _, _ = load_pack(acs)
            target = next((t for t in photo_targets(acs, pack)
                           if t["target_id"] == target_id), None)
            if not target:
                return self.send_error(404, "Unknown target")
            clip = body.get("video") or target.get("video")
            if not clip:
                return self.send_error(400, "No clip to search; pick one first")

            names = frame_names(acs, clip, "detail")
            if not names:
                return self.send_error(404, f"No extracted frames for {clip}")
            # Sample evenly rather than sending the whole clip: a 300-frame clip
            # would be hundreds of images and most are near-duplicates.
            count = max(2, min(int(body.get("sample") or 12), 20))
            step = max(1, len(names) // count)
            sampled = names[::step][:count]
            result = vlm.pick_best_frame(
                model=body.get("model") or "google/gemini-3.6-flash",
                image_paths=[FRAME_SETS["detail"] / acs / clip / n for n in sampled],
                description=target.get("criterion") or target.get("label") or "",
            )
            if result.get("frame"):
                result["frame_url"] = f"/files/build/frames/{acs}/{clip}/{result['frame']}"
                result["video"] = clip
            result["sampled"] = len(sampled)
            result["of"] = len(names)
            return self.send_json(result)

        # Generate match-test controls for one target. Returned, not saved: a
        # generated criterion nobody read is exactly the kind of thing that
        # quietly becomes a number in a report, so the operator saves it through
        # the ordinary prompts endpoint once they have looked at it.
        if len(parts) == 3 and parts[0] == "photo" and parts[1] == "negatives":
            acs = parts[2]
            kinds = tuple(k for k in (body.get("kinds") or
                                      ["inversion", "substitution", "foreign"])
                          if k in ("inversion", "substitution", "foreign"))
            model = body.get("model") or BEST_VIEW_MODEL
            pack, _, _ = load_pack(acs)
            by_id = {t["target_id"]: t for t in photo_targets(acs, pack)}

            # The default shape is a whole negated sheet — same sections, same
            # numbering, split into points by the same parser — because that is
            # the only shape whose result can be set beside the original's. The
            # per-point controls remain available for a target with a single
            # unstructured condition, which has no sheet to mirror.
            if (body.get("shape") or "sheet") == "sheet":
                edited = body.get("criteria") if isinstance(body.get("criteria"), dict) else {}
                batch = body.get("target_ids")
                if batch is None:
                    target = by_id.get(body.get("target_id"))
                    if not target:
                        return self.send_error(404, "Unknown target")
                    return self.send_json({
                        "target_id": target["target_id"],
                        **negative_sheet(acs, target,
                                         (body.get("criterion") or "").strip() or None,
                                         model),
                    })
                if not isinstance(batch, list) or not batch:
                    return self.send_error(400, "target_ids must be a non-empty list")
                return self.send_json(negative_sheets_batch(
                    acs, batch, model, edited, targets=list(by_id.values())))

            # `target_ids` writes controls for a whole selection in one request;
            # `target_id` is the original single-target form the per-subtask
            # button still uses. Both run the same drafting path.
            batch = body.get("target_ids")
            if batch is None:
                target_id = body.get("target_id")
                target = by_id.get(target_id)
                if not target:
                    return self.send_error(404, "Unknown target")
                criterion = (body.get("criterion") or target.get("criterion") or "").strip()
                if not criterion:
                    return self.send_error(400, "This target has no criterion to negate")
                points = sheet_checks(criterion) or [{"id": "c1", "statement": criterion,
                                                      "defect": False}]
                variants, spend = negative_variants(acs, target, points, model, kinds)
                return self.send_json({"target_id": target_id, "variants": variants,
                                       "points": len(points), "cost_usd": round(spend, 6)})

            if not isinstance(batch, list) or not batch:
                return self.send_error(400, "target_ids must be a non-empty list")
            edited = body.get("criteria") if isinstance(body.get("criteria"), dict) else {}
            return self.send_json(negative_variants_batch(
                acs, batch, model, kinds, edited, targets=list(by_id.values())))

        if parts == ["photo", "estimate"]:
            return self.send_json(vlm.estimate_cost(
                body.get("models") or [], int(body.get("calls_per_model") or 0),
                float(body.get("images_per_call") or 1)))

        if parts == ["photo", "run"]:
            return self.handle_photo_run(body)

        self.send_error(404)

    def handle_photo_run(self, body: dict) -> None:
        acs = body.get("task_code")
        model_ids = [m for m in (body.get("models") or []) if m in vlm.MODELS_BY_ID]
        wanted = set(body.get("target_ids") or [])
        if not acs or not model_ids or not wanted:
            return self.send_error(400, "task_code, models and target_ids are required")

        pack, _, _ = load_pack(acs)
        frames_per_step = clamp_frames_per_step(body.get("frames_per_step"))
        targets = [t for t in photo_targets(acs, pack, frames_per_step)
                   if t["target_id"] in wanted]
        # Criteria edited in the browser but not yet saved arrive with the run.
        inline = body.get("criteria") or {}
        for target in targets:
            if inline.get(target["target_id"]):
                target["criterion"] = inline[target["target_id"]].strip()

        # A pack-derived target has no frame of its own — segmentation is what
        # maps a frame to a step, and most tasks have none. The operator picks
        # one in the browser and it arrives here, so the criterion can be graded
        # against a real image rather than sitting ungradeable.
        chosen = body.get("frames") or {}
        for target in targets:
            pick = chosen.get(target["target_id"])
            if not (isinstance(pick, dict) and pick.get("video") and pick.get("frame")):
                continue
            video, frame = str(pick["video"]), str(pick["frame"])
            path = FRAME_SETS["detail"] / acs / video / frame
            # Refuse anything that is not an actual extracted frame for this
            # task; the name lands in a filesystem path.
            if frame not in frame_names(acs, video, "detail") or not path.is_file():
                continue
            # A frame chosen by hand is a decision about which photograph the
            # criterion is tried against, so it replaces the sample rather than
            # joining it. Sampling around it would grade footage the operator
            # looked at and passed over.
            target.update({
                "video": video, "frame": frame, "frame_exists": True,
                "frame_url": f"/files/build/frames/{acs}/{video}/{frame}",
                "frames": [frame], "frame_picked": True,
                "frame_urls": [f"/files/build/frames/{acs}/{video}/{frame}"],
            })

        # One extra frame per target: the one that best shows the state the work
        # ends in. The sample is equidistant and therefore blind — it lands where
        # the arithmetic puts it, which on this footage is regularly a hand or a
        # tool across the workpiece. This asks a model which frame of the step
        # actually shows the finished article, and appends it. Chosen once per
        # target and shared by every model grading it, so the cost is one call
        # per target rather than one per cell.
        if body.get("best_view", True):
            for target in targets:
                add_best_view(acs, target, body.get("best_view_model"))

        inline_variants = body.get("variants") or {}
        # The negated sheets, from the browser where the operator may have
        # edited them, falling back to what was saved. Graded on the same frames
        # as the criteria they mirror — a control tried against different
        # evidence measures the evidence, not the grader.
        inline_negatives = body.get("negatives") or {}
        include_negative = bool(body.get("include_negative", True))
        items = []
        for target in targets:
            if not (target["frame_exists"] and target["criterion"]):
                continue
            base = {
                "upload_path": target.get("upload_path"),
                "video": target["video"], "frame": target["frame"],
                "frame_url": target["frame_url"], "step_id": target["step_id"],
                "polarity": "original", "negative_of": None,
                # Every frame the grader is shown, in the order it sees them.
                # `frame` remains the last — the state the step ends in — so a
                # reader wanting one frame still gets the one it always got.
                "frames": target.get("frames") or [],
                "best_view": target.get("best_view"),
                "expected": None,
                "is_control": False, "is_variant": False, "variant_of": None,
                "framing": target.get("framing"),
            }
            # Re-parsed from the criterion actually being graded, so a criterion
            # edited in the browser is still split into its own points. Reading
            # the target's stored checks instead would grade the sheet the
            # operator just replaced.
            checks = sheet_checks(target["criterion"]) or target.get("checks") or []

            if checks:
                # One call per point, so a failure names the condition that
                # failed instead of reporting that the subtask, as a whole, did.
                for check in checks:
                    items.append({**base,
                                  "target_id": f"{target['target_id']}::{check['id']}",
                                  "label": f"{target['label']} · {check['id']}",
                                  "criterion": check["statement"],
                                  "rolls_up_to": target["target_id"],
                                  # The subtask a point belongs to, carried on the
                                  # point itself: the grid reports one row per
                                  # subtask, and a point that cannot name its
                                  # parent's label and whole sheet can only be
                                  # regrouped by picking its id apart.
                                  "parent_label": target["label"],
                                  "parent_criterion": target["criterion"],
                                  "check_id": check["id"]})
            else:
                items.append({**base, "target_id": target["target_id"],
                              "label": target["label"], "criterion": target["criterion"]})

            # The same subtask graded against a criterion that does not describe
            # it. Its points are built exactly as the originals are — same
            # splitter, same roll-up, one call per point — so the two verdicts
            # are the same measurement and the pass rates can be subtracted.
            # What differs is only the verdict each point is expected to earn.
            items.extend(negative_items(target, base, include_negative,
                                        inline_negatives.get(target["target_id"])))

            # Match test: the same frame against reworded criteria. Unsaved
            # variants arrive with the run so the browser stays the source of
            # truth for work in progress.
            variants = inline_variants.get(target["target_id"], target.get("variants") or [])
            for variant in variants:
                criterion = (variant.get("criterion") or "").strip()
                if not criterion:
                    continue
                items.append({
                    "target_id": f"{target['target_id']}#{variant.get('id')}",
                    "label": f"{target['label']} → {variant.get('label') or 'variant'}",
                    "video": target["video"], "frame": target["frame"],
                    # The same frames as the point it varies, or the match test
                    # compares a reworded criterion against different evidence
                    # and attributes the difference to the wording.
                    "frames": target.get("frames") or [],
                    "best_view": target.get("best_view"),
                    "upload_path": target.get("upload_path"),
                    "frame_url": target["frame_url"], "criterion": criterion,
                    "step_id": target["step_id"],
                    "expected": variant.get("expected") if variant.get("expected") in vlm.EXPECTATIONS else None,
                    "is_control": False, "is_variant": True,
                    "variant_of": target["target_id"],
                })
        context = (pack or {}).get("title")

        jobs = []
        for item in items:
            if item.get("upload_path"):
                paths = [item["upload_path"]]
            elif item.get("video") and item.get("frames"):
                paths = [str(FRAME_SETS["detail"] / acs / item["video"] / f)
                         for f in item["frames"]]
            elif item.get("video") and item.get("frame"):
                paths = [str(FRAME_SETS["detail"] / acs / item["video"] / item["frame"])]
            else:
                paths = []
            base_cell = {
                "target_id": item["target_id"], "label": item["label"],
                "frame": item["frame"], "video": item["video"],
                # Which frames produced this verdict. A cell graded on three
                # frames and one graded on one are not the same measurement, and
                # a saved run that does not say which it was cannot be compared
                # with an earlier one.
                "frames": item.get("frames") or ([item["frame"]] if item.get("frame") else []),
                "best_view": item.get("best_view"),
                "criterion": item["criterion"],
                "expected": item["expected"], "is_control": item["is_control"],
                "is_variant": item.get("is_variant", False),
                "variant_of": item.get("variant_of"),
                # Which side of the comparison this verdict belongs to, carried
                # on the result itself. A saved run is regrouped from its
                # results, and a polarity inferred from the id would be one
                # `#negative` string away from silently mixing the two.
                "polarity": item.get("polarity") or "original",
                "negative_of": item.get("negative_of"),
                "negative_of_point": item.get("negative_of_point"),
                "positive_criterion": item.get("positive_criterion"),
            }
            for model_id in model_ids:
                jobs.append({
                    "model": model_id, "criterion": item["criterion"], "context": context,
                    "image_paths": paths,
                    # Frames of one step in chronological order, not a set of
                    # photographs of different subjects. The grader is told which
                    # it is looking at, because the two are read differently.
                    "sequence": len(paths) > 1 and not item.get("upload_path"),
                    "best_view": item.get("best_view"),
                    "mode": "correctness",
                    "cell": {**base_cell, "mode": "correctness",
                             "rolls_up_to": item.get("rolls_up_to"),
                             "parent_label": item.get("parent_label"),
                             "parent_criterion": item.get("parent_criterion"),
                             "check_id": item.get("check_id")},
                })
        if not jobs:
            return self.send_error(400, "Nothing runnable — check frames exist and criteria are non-empty")

        # Thresholds are no longer an operator control, but they are not gone:
        # `apply_thresholds` still refuses to pass a condition the photo cannot
        # show, at any threshold, and that rule is what keeps "not visible" from
        # being rounded to a pass. Fixed at the module defaults.
        pass_at = vlm.DEFAULT_PASS_THRESHOLD
        fail_at = vlm.DEFAULT_FAIL_THRESHOLD
        results = vlm.grade_many(jobs, workers=int(body.get("workers") or 4),
                                 pass_at=pass_at, fail_at=fail_at)

        # Count controls attempted, not controls graded — otherwise a run where
        # every call errored reports "0 controls" and the UI wrongly says the
        # run had none, which is the opposite of the warning it should give.
        controls = [r for r in results if r.get("is_control")]
        graded_controls = [r for r in controls if not r.get("error")]
        # Anything carrying an expectation — a labelled variant or a mismatch
        # control — is scoreable. This is the number that answers "does the
        # verdict actually track the criterion, or is the model just passing
        # everything it is shown?"
        scored = [r for r in results if not r.get("error") and r.get("expected")]
        scored_hits = sum(
            1 for r in scored if vlm.expectation_met(r["expected"], r.get("verdict"))
        )
        variants = [r for r in results if r.get("is_variant") and not r.get("error")]

        # Roll a task's per-check verdicts back into one task verdict, per model.
        # `fail` dominates a `pass`, and an abstention anywhere means the photo
        # set could not settle the task — reported as `review` rather than being
        # rounded to a pass, which is the failure mode that matters here.
        rollups: dict[str, dict] = {}
        for result in results:
            parent = result.get("rolls_up_to")
            if not parent or result.get("error"):
                continue
            entry = rollups.setdefault(f"{parent}|{result['model']}", {
                "target_id": parent, "model": result["model"],
                "polarity": result.get("polarity") or "original",
                "negative_of": result.get("negative_of"),
                "checks": 0, "passed": 0, "failed": [], "unsettled": [],
            })
            entry["checks"] += 1
            if result["verdict"] == "pass":
                entry["passed"] += 1
            elif result["verdict"] == "fail":
                entry["failed"].append(result.get("check_id"))
            else:
                entry["unsettled"].append(result.get("check_id"))
        for entry in rollups.values():
            entry["verdict"] = ("fail" if entry["failed"]
                                else "review" if entry["unsettled"] else "pass")
        run = {
            "schema_version": 1,
            "run_id": f"run_{int(time.time())}_{acs}",
            "task_code": acs,
            "models": model_ids,
            "system_prompt": vlm.SYSTEM_PROMPT,
            "thresholds": {"pass": pass_at, "fail": fail_at},
            "items": items,
            "results": results,
            "rollups": list(rollups.values()),
            "summary": {
                "calls": len(results),
                "errors": sum(1 for r in results if r.get("error")),
                "cost_usd": round(sum(r.get("cost_usd") or 0 for r in results), 6),
                # Controls should come back `fail`; anything else is the model
                # accepting a criterion the photo cannot possibly satisfy.
                "controls": len(controls),
                "controls_graded": len(graded_controls),
                "controls_correct": sum(
                    1 for r in graded_controls
                    if vlm.expectation_met(r.get("expected") or "not_pass", r.get("verdict"))
                ),
                "variants": len(variants),
                "scored": len(scored),
                "scored_correct": scored_hits,
                "match_accuracy": round(scored_hits / len(scored), 3) if scored else None,
                # The criteria against their own negations, which is the only
                # part of the run that separates a grader from a model agreeing
                # with whatever it is handed.
                "polarity": polarity_report(results, list(rollups.values())),
            },
        }
        out = PHOTO_DIR / acs / f"{run['run_id']}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(run, indent=2) + "\n")
        self.send_json(run)

    def send_json(self, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, allow_range: bool = False) -> None:
        path = path.resolve()
        # Everything served must live under the project; refuse traversal.
        if not str(path).startswith(str(ROOT)) or not path.is_file():
            return self.send_error(404)

        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        size = path.stat().st_size
        range_header = self.headers.get("Range") if allow_range else None

        # Video seeking needs byte ranges; without this the player can only play
        # a clip from the start and scrubbing silently fails.
        if range_header and range_header.startswith("bytes="):
            spec = range_header[len("bytes=") :].split(",")[0]
            start_text, _, end_text = spec.partition("-")
            start = int(start_text) if start_text else 0
            end = int(end_text) if end_text else size - 1
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            self.end_headers()
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            return

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        if allow_range:
            self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        with path.open("rb") as handle:
            while chunk := handle.read(65536):
                self.wfile.write(chunk)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"inspector serving {ROOT}")
    print(f"  http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
