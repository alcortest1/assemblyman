"""The independent QA pass — the gate that decides whether a clip enters the dataset.

Acceptance is deliberately conservative, and the reason is asymmetric cost. A
rejected good clip costs one regeneration. An accepted bad clip enters a grading
dataset as a labelled negative example, and every model later trained or
evaluated on it inherits the mistake — silently, because the label says the
defect is there.

So the thresholds below are all ANDed, the model's own `accepted` field is
advisory rather than binding, and anything the model declines to answer counts
against acceptance rather than for it.

The comparison is run on tone-mapped proxies of the *edit window only*, from both
the original and the generated file. Showing a grader two 80-second recordings
that differ for six seconds buries the thing it is being asked to judge.
"""

from __future__ import annotations

from pathlib import Path

from .config import ROOT, Settings
from . import analysis as analysis_mod
from . import media, prompts
from .discovery import VideoRecord, read_criteria, read_procedure

# Every one of these must hold. The preservation floor is the spec's 0.85.
PRESERVATION_FLOOR = 0.85
CONFIDENCE_FLOOR = 0.60
# A generated segment shorter than this leaves a grader nothing to look at even
# if the defect is technically rendered.
MIN_ARTIFACT_SECONDS = 2.0


class QAError(RuntimeError):
    pass


def _window_proxy(video: Path, start: float, end: float, out: Path,
                  *, pad: float = 1.5) -> Path:
    """A small proxy of the edit window, with a little context either side."""
    info = media.probe(video)
    lo = max(0.0, start - pad)
    hi = min(info.duration_s, end + pad)
    return media.build_analysis_proxy(video, out, info, width=512, fps=6.0,
                                      start=lo, end=hi)


def evaluate(record: VideoRecord, plan: dict, generated: Path, client,
             settings: Settings, *, model: str | None = None,
             work_dir: Path | None = None) -> dict:
    """Run the QA comparison and return the verdict, with `accepted` decided here."""
    model = model or settings.vlm_model
    source = ROOT / record.video_path
    start = plan["edit_window"]["start"]
    end = plan["edit_window"]["end"]
    work = Path(work_dir or (ROOT / "build" / "error_qa" / plan["task_code"]))
    work.mkdir(parents=True, exist_ok=True)

    original_proxy = _window_proxy(source, start, end, work / "original_window.mp4")
    generated_proxy = _window_proxy(Path(generated), start, end, work / "generated_window.mp4")

    messages = prompts.qa_messages(
        analysis_mod.data_uri(original_proxy),
        analysis_mod.data_uri(generated_proxy),
        plan,
        read_procedure(record),
        read_criteria(record))
    response = client.chat(model, messages, max_tokens=2000, temperature=0.0)
    choices = response.get("choices") or []
    if not choices:
        raise QAError(f"no choices in QA reply: {str(response)[:300]}")
    verdict = analysis_mod.extract_json(choices[0]["message"]["content"])

    playable, why = media.is_playable(Path(generated))
    verdict["_playable"] = playable
    verdict["_playable_detail"] = why
    verdict["_model"] = model
    verdict["_usage"] = response.get("usage", {})
    verdict["accepted"], verdict["rejection_reasons"] = decide(
        verdict, end - start,
        require_rubric_fail=plan.get("rubric_grounded", True))
    return verdict


def decide(verdict: dict, window_s: float,
           *, require_rubric_fail: bool = True) -> tuple[bool, list[str]]:
    """Apply the acceptance rules. Every failure is named, not just the first.

    A missing score is treated as a failure: the QA model declining to commit is
    not evidence that the clip is fine.

    `require_rubric_fail` is relaxed only for a deviation the compiled criteria
    do not grade. Demanding FAIL there would reject every such clip for the
    correct reason — the rubric really does pass it — so the requirement is
    dropped and the plan instead labels the output `UNGRADED_VARIANT`. The
    visibility and preservation checks still apply in full.
    """
    reasons: list[str] = []

    if not verdict.get("target_error_visible"):
        reasons.append("intended defect is not clearly visible")

    confidence = verdict.get("target_error_confidence")
    if confidence is None:
        reasons.append("no confidence reported for the intended defect")
    elif float(confidence) < CONFIDENCE_FLOOR:
        reasons.append(f"confidence {float(confidence):.2f} below {CONFIDENCE_FLOOR}")

    for key, label in (("scene_preservation_score", "scene"),
                       ("equipment_preservation_score", "equipment"),
                       ("camera_preservation_score", "camera")):
        score = verdict.get(key)
        if score is None:
            reasons.append(f"no {label} preservation score reported")
        elif float(score) < PRESERVATION_FLOOR:
            reasons.append(f"{label} preservation {float(score):.2f} below {PRESERVATION_FLOOR}")

    if require_rubric_fail and (verdict.get("rubric_result") or "").upper() != "FAIL":
        reasons.append(f"rubric result was {verdict.get('rubric_result')!r}, expected FAIL")

    extra = verdict.get("additional_task_defects") or []
    if extra:
        reasons.append(f"unrelated defects introduced: {'; '.join(map(str, extra))[:200]}")

    if not verdict.get("_playable", True):
        reasons.append(f"output not usable: {verdict.get('_playable_detail')}")

    if window_s < MIN_ARTIFACT_SECONDS:
        reasons.append(f"edit window {window_s:.1f}s is too short to grade")

    return (not reasons), reasons
