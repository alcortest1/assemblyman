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
# The photo-assessment tab is the one part of the inspector that writes: edited
# criteria and completed grading runs land here, and nowhere else.
PHOTO_DIR = ROOT / "build" / "photo_eval"

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}


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


def _photo_checks(step: dict) -> list[dict]:
    """The checks of a step a photograph could actually settle."""
    return [c for c in (step.get("checks") or [])
            if c.get("statement") and (c.get("observable") or "photo") == "photo"]


def _criterion_for_step(step: dict) -> dict:
    """Turn a pack step into a photo-gradeable criterion plus its provenance.

    Only checks marked `observable: photo` go into the criterion. The packs
    deliberately mark pull tests, continuity checks and measured lengths as
    `measurement` or `video` because a photograph cannot settle them, and
    folding those into the text asked a grader to answer an unanswerable
    question: a compound criterion is only as gradeable as its least gradeable
    clause, so one tactile check drags the whole verdict to `unsure` and the
    photographable part never gets assessed at all. Excluded checks are returned
    rather than dropped, so the UI can show what was set aside and why.

    A step with no photo checks falls back to its instruction text, which
    describes an action rather than an acceptance condition — recorded as a
    distinct source so an action cannot masquerade as a criterion.
    """
    checks = [c for c in (step.get("checks") or []) if c.get("statement")]
    gradeable = [c for c in checks if (c.get("observable") or "photo") == "photo"]
    excluded = [
        {"statement": c["statement"], "observable": c.get("observable")}
        for c in checks if c not in gradeable
    ]
    if gradeable:
        body = "\n".join(f"- {c['statement']}" for c in gradeable)
        return {"criterion": body, "source": "pack.checks", "excluded": excluded}
    return {
        "criterion": (step.get("text") or step.get("id") or "").strip(),
        "source": "pack.step_text",
        "excluded": excluded,
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
            store[target_id] = {"criterion": value, "variants": []}
        elif isinstance(value, dict):
            variants = [v for v in (value.get("variants") or []) if isinstance(v, dict)]
            store[target_id] = {"criterion": value.get("criterion"), "variants": variants}
    return store


def write_criteria_store(acs: str, store: dict) -> None:
    # Drop entries that carry neither an edit nor a variant, so clearing an edit
    # leaves the file clean rather than accumulating empty objects.
    pruned = {
        target_id: entry
        for target_id, entry in store.items()
        if (entry.get("criterion") or "").strip() or entry.get("variants")
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


def photo_targets(acs: str, pack: dict | None) -> list[dict]:
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
    """
    resolve = segment_step_resolver(pack)
    store = read_criteria_store(acs)
    drafted = (read_drafted_criteria(acs).get("entries") or {})
    candidates = frame_candidates(acs)
    targets: list[dict] = []
    covered_steps: set[str] = set()
    saw_clip_level = False

    def apply_store(target: dict) -> dict:
        entry = store.get(target["target_id"]) or {}
        upload = entry.get("upload")
        if upload and (PHOTO_DIR / acs / "uploads" / upload).is_file():
            # An operator-supplied photo beats anything derived from the video.
            target.update({
                "frame": upload,
                "frame_url": f"/files/build/photo_eval/{acs}/uploads/{upload}",
                "frame_exists": True, "frame_suggested": False, "uploaded": True,
                "upload_path": str(PHOTO_DIR / acs / "uploads" / upload),
            })
        override = (entry.get("criterion") or "").strip()
        target["criterion"] = override or target["criterion_default"]
        target["edited"] = bool(override)
        target["variants"] = entry.get("variants") or []
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
                built = _criterion_for_step(step)
                criterion, source, excluded = built["criterion"], built["source"], built["excluded"]
            else:
                criterion = (segment.get("short_description") or "").strip()
                source, excluded = "segment.description", []
            target_id = f"{video}:{segment.get('seq')}"
            targets.append(apply_store({
                "target_id": target_id,
                "kind": "subtask",
                "video": video,
                "seq": segment.get("seq"),
                "label": (segment.get("substep_label") or "").replace("-", " "),
                "step_id": (step or {}).get("id") or segment.get("step_id"),
                "step_title": (step or {}).get("text") or segment.get("step_title"),
                "confidence": segment.get("confidence"),
                "t_end": segment.get("t_end"),
                "frame": frame,
                "frame_url": f"/files/build/frames/{acs}/{video}/{frame}",
                "frame_exists": frame_path.is_file(),
                "description": segment.get("short_description"),
                "criterion_default": criterion,
                "criterion_source": source,
                "excluded_checks": excluded,
                "frame_candidates": candidates,
            }))

        # Task level. `evidence.required` lists SEPARATE photos, each with its
        # own framing and medium — so it must not be joined into one criterion
        # and pointed at one frame. That asks a single photo to satisfy three
        # different capture requirements at once, which fails by construction
        # and tells you nothing about the work. One target per required photo,
        # plus a roll-up that receives the whole set.
        last = segments[-1] if segments else None
        if not (last and last.get("frame_end")):
            continue
        saw_clip_level = True

        required = [r for r in (((pack or {}).get("evidence") or {}).get("required") or [])
                    if isinstance(r, dict)]
        photo_items = [r for r in required if (r.get("medium") or "photo") == "photo"]
        non_photo = [
            {"statement": r.get("description") or r.get("id"), "observable": r.get("medium")}
            for r in required if r not in photo_items
        ]
        frame = last["frame_end"]
        frame_url = f"/files/build/frames/{acs}/{video}/{frame}"
        frame_exists = (FRAME_SETS["detail"] / acs / video / frame).is_file()

        for item in photo_items:
            description = (item.get("description") or item.get("statement") or "").strip()
            if not description:
                continue
            targets.append(apply_store({
                "target_id": f"{video}:ev:{item.get('id')}",
                "kind": "evidence",
                "video": video,
                "seq": None,
                "label": f"{video} — required photo {item.get('id')}",
                "step_id": None,
                "step_title": "Task level — required evidence",
                "confidence": None,
                "t_end": last.get("t_end"),
                "frame": frame,
                "frame_url": frame_url,
                "frame_exists": frame_exists,
                "description": description,
                # `framing` is a capture instruction, not an acceptance
                # condition, so it drives the adequacy grader rather than being
                # folded into the criterion the work is judged against.
                "framing": item.get("framing"),
                "evidence_id": item.get("id"),
                "assumed": bool(item.get("assumed")),
                "criterion_default": description,
                "criterion_source": "pack.evidence",
                "excluded_checks": non_photo,
                # `description` and `framing` say what to photograph and how —
                # they are capture instructions, not acceptance conditions. The
                # pack declares no link from an evidence item to the checks it
                # supports, so inventing one here would put words in the SME's
                # mouth. These targets answer "is this the required photo?" and
                # nothing else; workmanship is judged by the roll-up.
                "adequacy_only": True,
            }))

        # The roll-up asks the actual task-level question — is the finished work
        # correct — against every photo-observable check in the pack, and is fed
        # the whole frame set rather than one frame.
        # Carried as discrete checks, not one joined blob: a compound criterion
        # fails whole the moment any clause fails, so you learn that something
        # is wrong but never which. Each check is graded on its own call.
        pack_checks = [
            {"id": check.get("id"), "statement": check["statement"], "step_id": step.get("id")}
            for step in (pack or {}).get("steps") or []
            for check in step.get("checks") or []
            if (check.get("observable") or "photo") == "photo" and check.get("statement")
        ]
        criterion = ("\n".join(f"- {c['statement']}" for c in pack_checks) if pack_checks
                     else "The finished work is complete and correct.")
        targets.append(apply_store({
            "target_id": f"{video}:task",
            "kind": "task",
            "video": video,
            "seq": None,
            "label": f"{video} — whole task (all photo checks)",
            "step_id": None,
            "step_title": "Task level — roll-up",
            "confidence": None,
            "t_end": last.get("t_end"),
            "frame": frame,
            "frame_url": frame_url,
            "frame_exists": frame_exists,
            "description": last.get("short_description"),
            "criterion_default": criterion,
            "criterion_source": "pack.checks" if pack_checks else "fallback",
            "excluded_checks": non_photo,
            "multi_image": True,
            "checks": pack_checks,
            "frame_candidates": candidates,
        }))

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

        best, best_score = None, 0
        for clip in {c["video"] for c in candidates}:
            clip_words = [w for w in re.findall(r"[a-z]{3,}", clip.lower()) if w not in skip]
            score = sum(1 for w in words if any(related(w, c) for c in clip_words))
            if score > best_score:
                best, best_score = clip, score
        return best

    clip_frames: dict[str, list[str]] = {}

    def frames_of(clip: str) -> list[str]:
        if clip not in clip_frames:
            clip_frames[clip] = frame_names(acs, clip, "detail")
        return clip_frames[clip]

    def suggest_frame(clip: str, position: tuple[int, int] | None) -> str | None:
        """Which frame of a clip stands for this piece of work.

        A clip's final frame is the closest thing it has to a finished state, so
        it is right for the section as a whole. It is wrong for the individual
        steps inside that section: giving all three steps of "Cut the Tubing"
        the clip's last frame grades "Decide the size of tubing to use" against a
        photo of tubing already cut. That reads as a confident failure on a step
        the student performed correctly, which is worse than having no frame at
        all — a wrong verdict is harder to notice than a missing one.

        The steps of a section run in order through its clip, so step i of n has
        finished at roughly i/n of the way through. That boundary frame is the
        guess. It is still only a guess — `frame_suggested` says so, and the
        picker overrides it — but it is one that moves through the work instead
        of standing still at the end of it.
        """
        names = frames_of(clip)
        if not names:
            return None
        if not position:
            return names[-1]
        index, count = position
        if count < 1:
            return names[-1]
        # 1-based index, so the final step lands on the final frame.
        cut = round(len(names) * index / count) - 1
        return names[min(len(names) - 1, max(0, cut))]

    def blank_frame(target: dict, section_title: str | None = None,
                    position: tuple[int, int] | None = None) -> dict:
        """A target with a criterion and, where one can be guessed, a frame."""
        target.update({"video": None, "seq": None, "confidence": None, "t_end": None,
                       "frame": None, "frame_url": None, "frame_exists": False,
                       "frame_candidates": candidates})
        clip = suggest_clip(section_title) if section_title else None
        frame = suggest_frame(clip, position) if clip else None
        if clip and frame:
            path = FRAME_SETS["detail"] / acs / clip / frame
            target.update({
                "video": clip, "frame": frame,
                "frame_url": f"/files/build/frames/{acs}/{clip}/{frame}",
                "frame_exists": path.is_file(),
                "frame_suggested": True,
                # Where in the clip the guess came from, so the UI can say "step
                # 2 of 3" rather than implying someone chose this frame.
                "frame_position": list(position) if position else None,
            })
        return target

    # One target per pack section — the level the work is actually filmed and
    # taught at. Without these the tab jumped straight from individual steps to
    # a whole-task roll-up, and the subtasks the task is made of ("Cut the
    # Tubing", "Bending the Tubing") had no representation anywhere.
    sections = dict.fromkeys(
        s.get("section") for s in (pack or {}).get("steps") or [] if s.get("section")
    )
    for section in sections:
        member_steps = [s for s in (pack or {}).get("steps") or []
                        if s.get("section") == section]
        if all(s.get("id") in covered_steps for s in member_steps):
            continue
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
        if not statements:
            continue
        targets.append(apply_store(blank_frame({
            "target_id": f"section:{re.sub(r'[^a-z0-9]+', '-', section.lower()).strip('-')}",
            # Not "subtask": that kind already means a reviewed sub-subtask
            # interval, which carries a real frame and a confidence. A section
            # is a pack-derived grouping with neither.
            "kind": "section",
            "label": f"{section} — subtask ({len(member_steps)} steps)",
            "step_id": None,
            "step_title": section,
            "section": section,
            "description": f"All photo-gradeable conditions across {len(member_steps)} steps.",
            "criterion_default": "\n".join(dict.fromkeys(statements)),
            "criterion_source": "drafted.section",
            "excluded_checks": [],
            "framing": None,
            "sources": [],
            "conflicts": [],
        }, section)))

    # Position of each step within its own section, so a suggested frame can
    # advance through the clip rather than every step sharing its last frame.
    section_members: dict[str, list[str]] = {}
    for step in (pack or {}).get("steps") or []:
        if step.get("id"):
            section_members.setdefault(step.get("section") or "", []).append(step["id"])

    for step in (pack or {}).get("steps") or []:
        if not step.get("id") or step["id"] in covered_steps:
            continue
        built = _criterion_for_step(step)
        entry = drafted_for(step["id"])
        members = section_members.get(step.get("section") or "", [])
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
            "section": step.get("section"),
            "description": entry.get("step_text") or step.get("text"),
            "criterion_default": criterion or built["criterion"],
            "criterion_source": "drafted.step" if criterion else built["source"],
            "excluded_checks": built["excluded"] + [
                {"statement": s, "observable": "not photo-gradeable"}
                for s in entry.get("not_photo_gradeable") or []
            ],
            "framing": entry.get("required_framing"),
            "sources": entry.get("sources") or [],
            "conflicts": entry.get("conflicts") or [],
        }, step.get("section"), position)))

    if saw_clip_level:
        return targets

    # No segmented clip, so the clip-level targets above never ran. Emit the
    # same two kinds without a clip to hang them on.
    required = [r for r in (((pack or {}).get("evidence") or {}).get("required") or [])
                if isinstance(r, dict)]
    photo_items = [r for r in required if (r.get("medium") or "photo") == "photo"]
    non_photo = [
        {"statement": r.get("description") or r.get("id"), "observable": r.get("medium")}
        for r in required if r not in photo_items
    ]

    for item in photo_items:
        description = (item.get("description") or item.get("statement") or "").strip()
        if not description:
            continue
        targets.append(apply_store(blank_frame({
            "target_id": f"ev:{item.get('id')}",
            "kind": "evidence",
            "label": f"Required photo {item.get('id')}",
            "step_id": None,
            "step_title": "Task level — required evidence",
            "description": description,
            "framing": item.get("framing"),
            "evidence_id": item.get("id"),
            "assumed": bool(item.get("assumed")),
            "criterion_default": description,
            "criterion_source": "pack.evidence",
            "excluded_checks": non_photo,
            "adequacy_only": True,
        })))

    pack_checks = [
        {"id": check.get("id"), "statement": check["statement"], "step_id": step.get("id")}
        for step in (pack or {}).get("steps") or []
        for check in step.get("checks") or []
        if (check.get("observable") or "photo") == "photo" and check.get("statement")
    ]
    if not (pack_checks or drafted_for("task")):
        return targets

    task_entry = drafted_for("task")
    task_criterion = (task_entry.get("criterion") or "").strip()
    targets.append(apply_store(blank_frame({
        "target_id": "task",
        "kind": "task",
        "label": "Whole task (all photo checks)",
        "step_id": None,
        "step_title": "Task level — roll-up",
        "description": None,
        "criterion_default": task_criterion or "\n".join(
            f"- {c['statement']}" for c in pack_checks),
        "criterion_source": "drafted.task" if task_criterion else "pack.checks",
        "excluded_checks": non_photo + [
            {"statement": s, "observable": "not photo-gradeable"}
            for s in task_entry.get("not_photo_gradeable") or []
        ],
        "sources": task_entry.get("sources") or [],
        "multi_image": True,
        "checks": pack_checks,
    })))

    return targets


def build_mismatch_jobs(targets: list[dict], count: int) -> list[dict]:
    """Pair frames with criteria they should NOT satisfy.

    Every reference frame in this dataset is correct work, so a run where each
    model passes everything is indistinguishable from a run where each model
    simply always says pass. Mispairing a frame with another subtask's criterion
    produces an item whose correct answer is `fail`, which is what makes the
    results discriminative. It is a synthetic negative and no substitute for the
    labelled negatives docs/evals.md calls for — a mispaired criterion is
    usually *obviously* wrong, so passing this control is a floor, not a ceiling.
    """
    usable = [t for t in targets if t.get("frame_exists") and t.get("criterion")]
    if len(usable) < 2 or count <= 0:
        return []
    pairs = []
    for offset, target in enumerate(usable[:count]):
        # Prefer a criterion from a different pack step; fall back to any other.
        other = next(
            (o for o in usable[offset + 1 :] + usable[:offset]
             if o["step_id"] != target["step_id"]),
            None,
        ) or usable[(offset + len(usable) // 2) % len(usable)]
        if other["target_id"] == target["target_id"]:
            continue
        pairs.append({
            "target_id": f"{target['target_id']}~mismatch",
            "source_target_id": target["target_id"],
            "criterion_from": other["target_id"],
            "label": f"{target['label']} × criterion from “{other['label']}”",
            "frame": target["frame"],
            "video": target["video"],
            "frame_url": target["frame_url"],
            "criterion": other["criterion"],
            "expected": "not_pass",
            "is_control": True,
        })
    return pairs


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
            return self.send_json({"task_code": acs, "targets": photo_targets(acs, pack)})

        if len(parts) == 3 and parts[0] == "photo" and parts[1] == "runs":
            acs = parts[2]
            runs = []
            for path in sorted((PHOTO_DIR / acs).glob("run_*.json"), reverse=True):
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
                    }
                    for i, v in enumerate(body.get("variants") or [])
                    if isinstance(v, dict) and (v.get("criterion") or "").strip()
                ]
            store[target_id] = entry
            write_criteria_store(acs, store)
            saved = read_criteria_store(acs).get(target_id) or {}
            return self.send_json({
                "saved": True,
                "target_id": target_id,
                "edited": bool((saved.get("criterion") or "").strip()),
                "variants": saved.get("variants") or [],
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

        if parts == ["photo", "estimate"]:
            return self.send_json(vlm.estimate_cost(
                body.get("models") or [], int(body.get("calls_per_model") or 0)))

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
        targets = [t for t in photo_targets(acs, pack) if t["target_id"] in wanted]
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
            target.update({
                "video": video, "frame": frame, "frame_exists": True,
                "frame_url": f"/files/build/frames/{acs}/{video}/{frame}",
            })

        # "holistic" grades the assembly as a whole against a weighted rubric;
        # "correctness" grades condition by condition. Same photo, same grid.
        # Read before the item loop, which branches on it when deciding whether
        # to expand a roll-up into one call per check.
        grading_mode = body.get("grading_mode") or "correctness"
        if grading_mode not in ("correctness", "holistic"):
            return self.send_error(400, "grading_mode must be correctness or holistic")

        # One frame per clip: on a task filmed as one clip per subtask, that is
        # the whole finished story and is what a roll-up should be shown.
        rollup_submission = sorted({
            str(FRAME_SETS["detail"] / acs / c["video"] / c["last_frame"])
            for c in frame_candidates(acs) if c.get("last_frame")
        })

        inline_variants = body.get("variants") or {}
        items = []
        for target in targets:
            # A roll-up is graded against the whole photo set, so it is runnable
            # once a submission can be assembled even though it has no frame
            # pinned to itself. Requiring its own frame silently dropped it from
            # every run on an unsegmented task.
            if target.get("multi_image") and target["criterion"] and rollup_submission:
                pass
            elif not (target["frame_exists"] and target["criterion"]):
                continue
            base = {
                "upload_path": target.get("upload_path"),
                "video": target["video"], "frame": target["frame"],
                "frame_url": target["frame_url"], "step_id": target["step_id"],
                "expected": body.get("base_expected") or None,
                "is_control": False, "is_variant": False, "variant_of": None,
                "framing": target.get("framing"),
                "multi_image": bool(target.get("multi_image")),
            }
            edited = target.get("edited") or inline.get(target["target_id"])
            checks = target.get("checks") or []

            if target.get("adequacy_only"):
                # Capture instruction: the only answerable question is whether
                # the photo is the one that was asked for.
                items.append({**base, "target_id": target["target_id"],
                              "label": target["label"], "criterion": target["criterion"],
                              "adequacy_only": True})
            elif checks and not edited and grading_mode != "holistic":
                # A roll-up expands to one call per check, so a task-level result
                # names the specific check that failed. An explicit edit means
                # the author wants their own single criterion used instead.
                for check in checks:
                    items.append({**base,
                                  "target_id": f"{target['target_id']}::{check['id']}",
                                  "label": f"{target['label']} · {check['step_id']}",
                                  "criterion": check["statement"],
                                  "rolls_up_to": target["target_id"],
                                  "check_id": check["id"]})
            else:
                items.append({**base, "target_id": target["target_id"],
                              "label": target["label"], "criterion": target["criterion"]})
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
                    "frame_url": target["frame_url"], "criterion": criterion,
                    "step_id": target["step_id"],
                    "expected": variant.get("expected") if variant.get("expected") in vlm.EXPECTATIONS else None,
                    "is_control": False, "is_variant": True,
                    "variant_of": target["target_id"],
                })
        items += [
            {**job, "is_variant": False, "variant_of": None}
            for job in build_mismatch_jobs(targets, int(body.get("mismatch_count") or 0))
        ]

        context = (pack or {}).get("title")
        # A roll-up gets the whole submission: every selected frame, deduplicated
        # and in clip order, so "is the task done" is asked of the photo set the
        # student would actually hand in.
        submission = sorted({
            t.get("upload_path")
            or str(FRAME_SETS["detail"] / acs / t["video"] / t["frame"])
            for t in targets if t["frame_exists"] and (t.get("upload_path") or t.get("video"))
        })
        # A task like "Fabricate a rigid line" is filmed as one clip per
        # subtask — cut, deburr, bend, flare, route, install, test — and the
        # roll-up asks whether the finished article is right, which no single
        # subtask's photo can answer. When a roll-up is graded and the selection
        # has not already supplied a spread of photos, fall back to one frame
        # per clip so the submission covers every subtask rather than whichever
        # one happened to be selected alongside it.
        # A roll-up asks whether the finished article is right, and on a task
        # filmed as one clip per subtask that question spans every clip. Using
        # only the frames of whichever targets happened to be selected alongside
        # it answered a narrower question than the one being asked, so the
        # per-clip set is unioned in rather than used as a fallback.
        if any(t.get("multi_image") for t in targets):
            submission = sorted(set(submission) | set(rollup_submission))
        # Adequacy is only meaningful where the pack states a framing
        # requirement, i.e. the required-evidence targets.
        grade_adequacy = bool(body.get("grade_adequacy"))

        jobs = []
        for item in items:
            # A roll-up graded across the whole photo set has no single frame of
            # its own; only the multi-image path is used for it.
            frame_path = item.get("upload_path") or (
                str(FRAME_SETS["detail"] / acs / item["video"] / item["frame"])
                if item.get("video") and item.get("frame") else None
            )
            multi = item.get("multi_image") and (len(submission) > 1 or not frame_path)
            base_cell = {
                "target_id": item["target_id"], "label": item["label"],
                "frame": item["frame"], "video": item["video"],
                "criterion": item["criterion"],
                "expected": item["expected"], "is_control": item["is_control"],
                "is_variant": item.get("is_variant", False),
                "variant_of": item.get("variant_of"),
            }
            for model_id in model_ids:
                if not item.get("adequacy_only"):
                    jobs.append({
                        "model": model_id, "criterion": item["criterion"], "context": context,
                        "image_paths": submission if multi else None,
                        "image_path": None if multi else frame_path,
                        "mode": grading_mode,
                        "cell": {**base_cell, "mode": grading_mode,
                                 "rolls_up_to": item.get("rolls_up_to"),
                                 "check_id": item.get("check_id")},
                    })
                if (grade_adequacy or item.get("adequacy_only")) and item.get("framing"):
                    jobs.append({
                        "model": model_id, "criterion": item["criterion"],
                        "framing": item["framing"], "mode": "adequacy",
                        "image_path": frame_path,
                        # An adequacy result is about the photo, so it carries no
                        # workmanship expectation and must not be scored as one.
                        "cell": {**base_cell, "mode": "adequacy",
                                 "target_id": f"{item['target_id']}@adequacy",
                                 "label": f"{item['label']} — photo usable?",
                                 "expected": None},
                    })
        if not jobs:
            return self.send_error(400, "Nothing runnable — check frames exist and criteria are non-empty")

        pass_at = float(body.get("pass_threshold") or vlm.DEFAULT_PASS_THRESHOLD)
        fail_at = float(body.get("fail_threshold") or vlm.DEFAULT_FAIL_THRESHOLD)
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
