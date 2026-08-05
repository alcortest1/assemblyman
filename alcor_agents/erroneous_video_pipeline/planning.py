"""Turn a Stage-1 analysis into the `error_plan.json` contract for one variant.

The plan is the auditable middle step: it is written before any money is spent,
it is what the generation prompt is built from, and it is what the QA pass grades
the result against. Keeping it as a file rather than as in-memory state is what
makes `--dry-run` genuinely useful — every plan can be read and corrected before
a single job is submitted.
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import ROOT
from .discovery import VideoRecord, read_criteria


def _criteria_lines(criteria: str) -> tuple[list[str], list[str]]:
    """Split a compiled criteria file into its numbered criteria and critical defects.

    The files follow one layout, written by `packs/generate_criteria.py`:
    a numbered `Criteria` block, then a dashed `Critical defects` block.
    """
    numbered, defects = [], []
    section = None
    for raw in criteria.splitlines():
        line = raw.strip()
        low = line.lower()
        if low.startswith("criteria"):
            section = "criteria"
            continue
        if low.startswith("critical defect"):
            section = "defects"
            continue
        if low.startswith(("overall decision", "source basis")):
            section = None
            continue
        if not line:
            continue
        if section == "criteria" and re.match(r"^\d+\.\s+", line):
            numbered.append(re.sub(r"^\d+\.\s+", "", line))
        elif section == "defects" and line.startswith("-"):
            defects.append(line.lstrip("- ").strip())
    return numbered, defects


def match_criteria(criteria: str, error: dict) -> list[str]:
    """Which *written* criteria this error violates — real rubric lines only.

    Returns empty when nothing in the compiled criteria matches. That emptiness
    is load-bearing: `build_plan` reads it as "not rubric-grounded" and labels
    the output `UNGRADED_VARIANT` instead of `FAIL`.

    Falling back to the model's own `rubric_criterion_violated` text would defeat
    the whole check. That string is a paraphrase the analysis wrote, so echoing
    it into `violated_criteria` would make every error look grounded — including
    bend angle, which this task's criteria explicitly decline to grade — and the
    manifest would then assert a rubric failure no rubric agrees with.
    """
    numbered, _ = _criteria_lines(criteria)
    quoted = (error.get("rubric_criterion_violated") or "").strip()
    if not numbered or not quoted:
        return []
    exact = [c for c in numbered if c.lower() in quoted.lower() or quoted.lower() in c.lower()]
    if exact:
        return exact
    words = {w for w in re.findall(r"[a-z]{4,}", quoted.lower())}
    scored = sorted(((len(words & set(re.findall(r"[a-z]{4,}", c.lower()))), c)
                     for c in numbered), reverse=True)
    return [scored[0][1]] if scored and scored[0][0] >= 3 else []


def build_plan(record: VideoRecord, analysis: dict, error: dict) -> dict:
    """The `error_plan.json` body for one (video, error) pair."""
    criteria = read_criteria(record)
    violated = match_criteria(criteria, error)
    _, critical = _criteria_lines(criteria)

    preserve = list(analysis.get("constraints_to_preserve") or [])
    tools = list(analysis.get("tools_and_equipment") or [])
    if not preserve:
        preserve = [f"same {t}" for t in tools[:6]]

    return {
        "task_code": record.task_code,
        "subtask_id": record.subtask_id,
        "error_id": error.get("error_id"),
        "source_video": record.video_path,
        "edit_window": {
            "start": analysis["editable_time_start"],
            "end": analysis["editable_time_end"],
        },
        "required_error": error.get("visible_change") or error.get("description"),
        "error_description": error.get("description"),
        "severity": error.get("severity"),
        "generation_feasibility": error.get("generation_feasibility"),
        "must_preserve": preserve,
        "must_not_change": [
            "technician identity and appearance",
            "background and room",
            "workbench surface and layout",
            "equipment arrangement",
            "number of tools visible",
            "camera position, framing and motion style",
            "lighting and colour",
        ],
        "acceptance_test": [
            f"{error.get('visible_change', 'the intended defect')} is clearly visible "
            "in the finished artifact",
            "the workpiece remains the same object, engaged with the same tools",
            "no unrelated defects are introduced",
            "scene, equipment and camera are indistinguishable from the source",
        ],
        "violated_criteria": violated,
        # What the analysis *claimed* it violated, kept separate from the real
        # rubric lines above so a reviewer can see the gap when they disagree.
        "claimed_criterion_violated": error.get("rubric_criterion_violated"),
        "criteria_path": record.criteria_path,
        "procedure_path": record.procedure_path,
        "critical_defects_in_rubric": critical,
        "analysis_model": (analysis.get("_meta") or {}).get("model"),
        "synthetic": True,
        # A deviation the compiled criteria do not grade is still a real,
        # useful stimulus — but it is not a rubric failure, and labelling it
        # FAIL would put a wrong answer key into the dataset. Bend angle and
        # measurement are exactly this case for `bend_the_line`: the criteria
        # file records that both "require measurement or the template in hand
        # and are not graded here".
        "rubric_grounded": bool(violated),
        "label": "FAIL" if violated else "UNGRADED_VARIANT",
        "rubric_coverage_note": (
            None if violated else
            "The deviation is real but no compiled criterion for this subtask "
            "grades it, so this clip is not a rubric failure. Use it as a "
            "stimulus, not as a labelled negative example, until a criterion "
            "covers the property it changes."),
        "from_catalog": bool(error.get("from_catalog")),
    }


def variant_dir(plan: dict, root: Path | None = None) -> Path:
    return ((root or (ROOT / "generated_errors")) / plan["task_code"]
            / (plan["subtask_id"] or "unknown") / (plan["error_id"] or "unknown"))


def output_name(plan: dict, version: int = 1) -> str:
    """Idempotent artifact name: same plan and version always yields the same file."""
    stem = Path(plan["source_video"]).stem
    return f"{stem}__{plan['error_id']}__v{version:02d}.mp4"


def next_version(plan: dict, root: Path | None = None) -> int:
    directory = variant_dir(plan, root)
    if not directory.is_dir():
        return 1
    stem = Path(plan["source_video"]).stem
    pattern = re.compile(rf"^{re.escape(stem)}__{re.escape(plan['error_id'] or '')}__v(\d+)\.mp4$")
    used = [int(m.group(1)) for m in
            (pattern.match(p.name) for p in directory.glob("*.mp4")) if m]
    return max(used) + 1 if used else 1
