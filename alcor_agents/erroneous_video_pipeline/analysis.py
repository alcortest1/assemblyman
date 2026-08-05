"""Stage 1 — read the source footage and propose grounded candidate errors.

The source clips are 27-288 MB of 10-bit HEVC. None of that can be sent to a
chat model directly, so `media.build_analysis_proxy` makes a 640px, 6 fps SDR
copy first — small enough to inline as base64, detailed enough to identify
tools, hand position and the state of the workpiece.

Results are cached per (video, model) because Stage 1 is the expensive half of a
dry run and its answer does not change between attempts at the same clip.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

from .config import ROOT, Settings
from .discovery import VideoRecord, read_criteria, read_procedure
from . import catalog, media, prompts

CACHE_DIR = ROOT / "build" / "error_analysis"

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


class AnalysisError(RuntimeError):
    pass


def extract_json(text: str) -> dict:
    """Parse a JSON object out of a model reply that may be fenced or padded."""
    if not text or not text.strip():
        raise AnalysisError("model returned an empty reply")
    fenced = _FENCE.search(text)
    candidate = fenced.group(1) if fenced else text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(candidate[start:end + 1])
        except json.JSONDecodeError as exc:
            raise AnalysisError(f"reply was not valid JSON: {exc}") from exc
    raise AnalysisError("reply contained no JSON object")


def data_uri(path: Path, mime: str = "video/mp4") -> str:
    return f"data:{mime};base64," + base64.b64encode(Path(path).read_bytes()).decode()


def _clamp_window(analysis: dict, duration: float, *,
                  min_s: float = 3.0, max_s: float = 20.0) -> tuple[float, float, list[str]]:
    """Keep the proposed window inside the clip and inside model duration limits.

    A model asked for "the shortest span" will sometimes return a window running
    past the end of the clip, or a 0.4 s instant, or the entire video. All three
    are unusable, and all three are cheaper to correct here than to discover
    after paying for a generation.
    """
    notes: list[str] = []
    try:
        start = float(analysis.get("editable_time_start", 0.0))
        end = float(analysis.get("editable_time_end", 0.0))
    except (TypeError, ValueError):
        raise AnalysisError("editable_time_start/end were not numbers")

    if end <= start:
        raise AnalysisError(f"empty edit window: {start}-{end}")
    if start < 0:
        start, notes = 0.0, notes + ["window started before the clip; clamped to 0"]
    if end > duration:
        notes.append(f"window ended past the clip ({end:.1f}s > {duration:.1f}s); clamped")
        end = duration
    if end - start < min_s:
        end = min(duration, start + min_s)
        notes.append(f"window shorter than {min_s}s; widened")
    if end - start > max_s:
        # Keep the tail: the finished artifact is what a grader must see.
        start = max(0.0, end - max_s)
        notes.append(f"window longer than the {max_s}s generation ceiling; "
                     f"kept the final {max_s}s so the completed result is inside it")
    return round(start, 3), round(end, 3), notes


def analyse(record: VideoRecord, client, settings: Settings, *,
            model: str | None = None, refresh: bool = False,
            proxy_seconds: float | None = None) -> dict:
    """Run Stage 1 for one video, or return the cached result."""
    model = model or settings.vlm_model
    video = ROOT / record.video_path
    info = media.probe(video)
    cache = CACHE_DIR / record.task_code / f"{video.stem}__{model.replace('/', '_')}.json"

    if cache.exists() and not refresh:
        cached = json.loads(cache.read_text())
        cached["cached"] = True
        return cached

    proxy = CACHE_DIR / record.task_code / f"{video.stem}.proxy.mp4"
    media.build_analysis_proxy(
        video, proxy, info,
        end=proxy_seconds if proxy_seconds else None)

    criteria = read_criteria(record)
    if not criteria:
        raise AnalysisError(
            f"{record.video_path} has no criteria file; a candidate error cannot "
            "be grounded in a rubric that does not exist")
    procedure = read_procedure(record)

    messages = prompts.analysis_messages(
        data_uri(proxy), procedure, criteria, record.task_code,
        record.subtask_id or "unknown")

    # Even with response_format=json_object these replies occasionally come back
    # malformed — a stray quote inside a description is enough. The reply is
    # cheap (~$0.04) and the failure is not sticky, so re-asking beats aborting a
    # whole sweep. Temperature is raised slightly on retry so a model that has
    # locked onto a broken continuation takes a different path.
    analysis = None
    last: Exception | None = None
    for attempt in range(max(1, settings.max_retries)):
        response = client.chat(model, messages, max_tokens=4000,
                               temperature=0.2 + 0.2 * attempt)
        choices = response.get("choices") or []
        if not choices:
            last = AnalysisError(f"no choices in analysis reply: {str(response)[:400]}")
            continue
        try:
            analysis = extract_json(choices[0]["message"]["content"])
            break
        except AnalysisError as exc:
            last = exc
    if analysis is None:
        raise AnalysisError(f"analysis did not return usable JSON: {last}")

    start, end, notes = _clamp_window(analysis, info.duration_s)
    analysis["editable_time_start"], analysis["editable_time_end"] = start, end
    analysis["window_adjustments"] = notes
    analysis["_meta"] = {
        "model": model,
        "video": record.video_path,
        "task_code": record.task_code,
        "subtask_id": record.subtask_id,
        "proxy": str(proxy.relative_to(ROOT)),
        "proxy_bytes": proxy.stat().st_size,
        "source": info.as_dict(),
        "usage": response.get("usage", {}),
    }
    if not analysis.get("candidate_errors"):
        raise AnalysisError("analysis proposed no candidate errors")

    cache.parent.mkdir(parents=True, exist_ok=True)
    temp = cache.with_suffix(".json.part")
    temp.write_text(json.dumps(analysis, indent=2))
    temp.replace(cache)
    analysis["cached"] = False
    return analysis


def pick_error(analysis: dict, error_id: str | None,
               subtask_id: str | None = None) -> dict:
    """Choose which candidate error to build, preferring clearly gradeable ones.

    A named error that Stage 1 did not propose falls back to the archetype
    catalogue. That is how a deliberately ungraded deviation — bend angle,
    measurement — can still be requested: the analysis will not invent an error
    no criterion covers, but an operator may legitimately want one anyway. The
    resulting plan records that it is not rubric-grounded so the label stays
    honest.
    """
    candidates = analysis.get("candidate_errors") or []
    if error_id:
        match = next((c for c in candidates if c.get("error_id") == error_id), None)
        if match is not None:
            return match
        archetype = catalog.get(error_id)
        if archetype is not None:
            return archetype
        known = ", ".join(c.get("error_id", "?") for c in candidates)
        available = ", ".join(catalog.for_subtask(subtask_id))
        raise AnalysisError(
            f"{error_id!r} is neither a candidate for this clip nor a known "
            f"archetype.\n  proposed for this clip: {known}\n  catalogue: {available}")
    feasibility = {"high": 0, "medium": 1, "low": 2}
    severity = {"clear_fail": 0, "borderline": 1}
    return min(candidates, key=lambda c: (
        severity.get(c.get("severity"), 2),
        feasibility.get(c.get("generation_feasibility"), 3)))
