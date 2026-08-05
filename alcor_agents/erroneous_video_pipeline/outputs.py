"""Writing the dataset: per-variant artifacts, the manifest, and the reports.

Every write here is atomic — a temp file replaced into place — because a run can
be interrupted between a generation costing real money and the record of it, and
a half-written manifest line is indistinguishable from a corrupt dataset. The
manifest is JSONL so an interrupted run leaves valid rows behind rather than a
truncated array.

Only accepted candidates reach `manifest.jsonl`. Rejections go to
`failed_generations.jsonl` with their QA reasons, because knowing which defects
the generator cannot render is a result worth keeping, not a mess to discard.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT, OUTPUT_ROOT, redact

MANIFEST = "manifest.jsonl"
FAILURES = "failed_generations.jsonl"
COSTS = "cost_report.csv"
SUMMARY = "generation_summary.md"

COST_FIELDS = ["timestamp", "task_code", "subtask_id", "error_id", "stage",
               "model", "estimated_usd", "actual_usd", "accepted", "job_id"]


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_atomic(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_text(redact(text))
    temp.replace(path)
    return path


def write_json(path: Path, payload: dict) -> Path:
    return write_atomic(path, json.dumps(payload, indent=2, default=str))


def append_jsonl(path: Path, row: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(redact(json.dumps(row, default=str)) + "\n")
    return path


def read_jsonl(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    rows = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def record_cost(root: Path, *, task_code: str, subtask_id: str | None,
                error_id: str | None, stage: str, model: str,
                estimated: float | None, actual: float | None,
                accepted: bool | None = None, job_id: str | None = None) -> None:
    """Append one row to cost_report.csv, creating the header on first use."""
    path = Path(root) / COSTS
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    with path.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COST_FIELDS)
        if fresh:
            writer.writeheader()
        writer.writerow({
            "timestamp": now(), "task_code": task_code, "subtask_id": subtask_id,
            "error_id": error_id, "stage": stage, "model": model,
            "estimated_usd": f"{estimated:.4f}" if estimated is not None else "",
            "actual_usd": f"{actual:.4f}" if actual is not None else "",
            "accepted": "" if accepted is None else str(bool(accepted)).lower(),
            "job_id": job_id or "",
        })


def manifest_row(plan: dict, *, generated_video: Path, qa: dict, selection: dict,
                 analysis_model: str, qa_model: str, cost: float) -> dict:
    """One accepted output, in the manifest schema."""
    return {
        "source_video": plan["source_video"],
        "generated_video": str(Path(generated_video).relative_to(ROOT)),
        "task_code": plan["task_code"],
        "subtask_id": plan["subtask_id"],
        # Taken from the plan, not hardcoded: a deviation no compiled criterion
        # grades is an UNGRADED_VARIANT, and stamping FAIL on it would make the
        # manifest assert a rubric failure that the rubric does not agree with.
        "label": plan.get("label", "FAIL"),
        "rubric_grounded": plan.get("rubric_grounded", True),
        "rubric_coverage_note": plan.get("rubric_coverage_note"),
        "error_id": plan["error_id"],
        "violated_criteria": plan.get("violated_criteria") or [],
        "critical_defects": [plan.get("required_error")] if plan.get("required_error") else [],
        "synthetic": True,
        "generation_model": selection.get("model"),
        "generation_mode": selection.get("mode"),
        "analysis_model": analysis_model,
        "qa_model": qa_model,
        "cost": round(cost, 4),
        "qa_confidence": qa.get("target_error_confidence"),
        "qa_scene_preservation": qa.get("scene_preservation_score"),
        "qa_equipment_preservation": qa.get("equipment_preservation_score"),
        "qa_camera_preservation": qa.get("camera_preservation_score"),
        "created_at": now(),
    }


def write_summary(root: Path) -> Path:
    """Regenerate generation_summary.md from the manifests on disk."""
    root = Path(root)
    accepted = read_jsonl(root / MANIFEST)
    failed = read_jsonl(root / FAILURES)
    spent = sum(float(r.get("cost") or 0) for r in accepted)
    spent += sum(float(r.get("cost") or 0) for r in failed)

    by_task: dict[str, list[dict]] = {}
    for row in accepted:
        by_task.setdefault(row.get("task_code", "?"), []).append(row)

    lines = [
        "# Generated error dataset",
        "",
        f"Generated {len(accepted)} accepted negative example(s) from "
        f"{len({r.get('source_video') for r in accepted})} source video(s). "
        f"{len(failed)} candidate(s) were rejected.",
        "",
        f"- Total spend recorded: **${spent:.2f}**",
        f"- Written: {now()}",
        "",
        "Every clip here is **synthetic** and deliberately wrong. Each one is a "
        "negative example carrying exactly one intended rubric violation, and is "
        "labelled FAIL. None of it is footage of real student work, and it must "
        "not be presented as such.",
        "",
    ]
    if accepted:
        lines += ["## Accepted", "",
                  "| Task | Subtask | Error | Model | QA conf. | Cost |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for task, rows in sorted(by_task.items()):
            for row in rows:
                conf = row.get("qa_confidence")
                lines.append(
                    f"| {task} | {row.get('subtask_id')} | `{row.get('error_id')}` | "
                    f"{row.get('generation_model')} | "
                    f"{conf if conf is not None else '—'} | ${float(row.get('cost') or 0):.2f} |")
        lines.append("")
    if failed:
        lines += ["## Rejected", "",
                  "| Task | Subtask | Error | Reason |", "| --- | --- | --- | --- |"]
        for row in failed:
            reason = (row.get("reason") or row.get("error") or "—").replace("|", "/")
            lines.append(f"| {row.get('task_code')} | {row.get('subtask_id')} | "
                         f"`{row.get('error_id')}` | {reason[:160]} |")
        lines.append("")
    return write_atomic(root / SUMMARY, "\n".join(lines))


def already_accepted(root: Path, plan: dict) -> dict | None:
    """Find an existing accepted row for this variant, so --resume can skip it."""
    for row in read_jsonl(Path(root) / MANIFEST):
        if (row.get("source_video") == plan["source_video"]
                and row.get("error_id") == plan["error_id"]
                and row.get("subtask_id") == plan["subtask_id"]):
            return row
    return None


def output_root(override: str | Path | None = None) -> Path:
    return Path(override) if override else OUTPUT_ROOT
