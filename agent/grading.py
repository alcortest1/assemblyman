"""Grades one still against one subtask rubric, condition by condition.

The realtime model watches video and talks; it is the wrong thing to ask for a
verdict. A live session sends it a stream of frames at low resolution, it has no
way to hold still on the one that shows finished work, and its answer arrives as
speech rather than as something the portal can draw. So grading is a separate,
non-realtime call: `frame_grabber` picks the sharpest recent frame, this module
puts that one frame and one rubric to a vision model, and the structured reply
is what the overlay renders and the assistant reads aloud.

The overall verdict is computed here, not asked for. The rubric states the rule —
every criterion passes and no critical defect is present — and that is
arithmetic. Models get it a few points wrong often enough to matter, and this is
a student's result.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from google import genai
from google.genai import types

from criteria_prompt import Rubric

logger = logging.getLogger("assemblyman-grading")

# The live model hosts the conversation; it does not grade. Grading is a separate
# call to a pro model on one captured still — slower than flash, and worth it,
# because this verdict goes on a student's work and the operator has explicitly
# stopped to ask for it.
GRADER_MODEL = os.getenv("ASSEMBLYMAN_GRADER_MODEL", "gemini-3.1-pro-preview")

# A grade that never returns is worse than one that fails: the operator is left
# looking at a spinner with their hands full.
TIMEOUT_S = float(os.getenv("ASSEMBLYMAN_GRADE_TIMEOUT", "45"))

SYSTEM = """\
You are grading a photograph of a student's finished aircraft-maintenance work \
for an FAA Part 147 training pilot, against a rubric supplied to you.

Judge EACH numbered criterion independently and return a verdict for every one, \
in the order given. Do not merge them, skip them, or add any.

PASS means you can see, in this photograph, that the criterion is satisfied.
FAIL means either that you can see it is not satisfied, or that the photograph \
does not show it. The rubric is explicit that a criterion you cannot check is \
marked "FAIL — not demonstrated in image", so an unobservable condition is a \
FAIL and never a PASS. Say which of the two it was in your note.

Then list the rubric's critical defects that you can actually SEE in the frame. \
A defect you cannot rule out is not a defect you saw — list only what is \
affirmatively visible.

Judge only visible evidence. Never infer torque, pressure, internal condition, \
material type, or an exact dimension from a photograph. If the rubric asks for a \
measurement and no scale reference is in frame, that criterion FAILS as not \
demonstrated.

Keep every note under 20 words and name what you saw, not what you assumed.\
"""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "observed": {
            "type": "string",
            "description": "What the photograph actually shows, under 30 words.",
        },
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "1-based, matching the rubric"},
                    "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
                    "observable": {
                        "type": "boolean",
                        "description": "False when the photo does not show this at all",
                    },
                    "note": {"type": "string"},
                },
                "required": ["index", "verdict", "observable", "note"],
            },
        },
        "critical_defects_seen": {
            "type": "array",
            "items": {"type": "string", "description": "Quoted from the rubric's list"},
        },
    },
    "required": ["observed", "criteria", "critical_defects_seen"],
}


_CLIENT: genai.Client | None = None

# The two models are reached by different routes, and this is not a preference.
#
# The live model exists only on AI Studio, so the session must hold an API key.
# The pro model is the other way round on the keys we have: AI Studio answers
# `gemini-3.1-pro-preview` with 429 and `limit: 0` — no free-tier quota for it at
# all — while the Vertex project serves it. So the grader goes over Vertex when a
# service account is configured, and falls back to the API key when it is not.
#
# Set ASSEMBLYMAN_GRADER_VERTEX=0 to force the API key, which is the right move
# if the AI Studio key is on a paid plan and the Vertex project is not.
USE_VERTEX = (
    os.getenv("ASSEMBLYMAN_GRADER_VERTEX", "1") == "1"
    and bool(os.getenv("GOOGLE_CLOUD_PROJECT"))
    and bool(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
)


def _client() -> genai.Client:
    """One client for the process, kept alive deliberately.

    `Client.models` does not hold a strong reference back to its client, so a
    fresh `genai.Client(...).models.generate_content(...)` can have the client
    collected — and its connection pool closed — before the request goes out.
    That surfaces as "Cannot send a request, as the client has been closed",
    which reads like an API fault rather than a lifetime bug. Caching it also
    keeps the connection pool warm across grades.
    """
    global _CLIENT
    if _CLIENT is None:
        if USE_VERTEX:
            _CLIENT = genai.Client(
                vertexai=True,
                project=os.environ["GOOGLE_CLOUD_PROJECT"],
                location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
            )
            logger.info("grader on Vertex (%s / %s)", os.environ["GOOGLE_CLOUD_PROJECT"],
                        os.getenv("GOOGLE_CLOUD_LOCATION", "global"))
        else:
            _CLIENT = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
            logger.info("grader on the AI Studio key")
    return _CLIENT


def _prompt(rubric: Rubric) -> str:
    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(rubric.criteria, 1))
    defects = "\n".join(f"- {d}" for d in rubric.critical_defects)
    return (
        f"TASK {rubric.task_code} — {rubric.task_title}\n"
        f"SUBTASK {rubric.subtask_code} — {rubric.subtask}\n\n"
        f"Assess the completed {rubric.subject or 'work'} visible in the image.\n\n"
        f"NUMBERED CRITERIA\n{numbered}\n\n"
        f"CRITICAL DEFECTS\n{defects}\n\n"
        f"Return a verdict for all {len(rubric.criteria)} criteria, in order."
    )


def _blocking_grade(jpeg: bytes, rubric: Rubric) -> dict[str, Any]:
    response = _client().models.generate_content(
        model=GRADER_MODEL,
        contents=[
            types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"),
            _prompt(rubric),
        ],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM,
            temperature=0,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
        ),
    )
    return json.loads(response.text)


def _assemble(raw: dict[str, Any], rubric: Rubric, frame: dict) -> dict[str, Any]:
    """Turn the model's reply into the payload the overlay draws.

    Every criterion in the rubric appears in the result whether or not the model
    returned one for it. A rubric line that silently vanished from the overlay
    would read as a criterion that did not apply, when in fact it is one nobody
    checked.
    """
    by_index = {}
    for item in raw.get("criteria") or []:
        try:
            by_index[int(item.get("index"))] = item
        except (TypeError, ValueError):
            continue

    criteria = []
    for i, text in enumerate(rubric.criteria, 1):
        item = by_index.get(i) or {}
        verdict = str(item.get("verdict", "")).strip().upper()
        if verdict not in ("PASS", "FAIL"):
            verdict = "FAIL"
            note = "No verdict returned for this criterion."
            observable = False
        else:
            note = str(item.get("note") or "").strip()
            observable = bool(item.get("observable", True))
        criteria.append({
            "index": i,
            "text": text,
            "verdict": verdict,
            # The distinction the overlay needs: work that is wrong and work
            # that was not photographed are both FAIL, and the student can only
            # act on the second one by taking a better picture.
            "observable": observable,
            "note": note,
        })

    # Only defects the rubric actually lists. A model that invents one would
    # otherwise fail the work on a standard nobody wrote down.
    listed = {d.lower(): d for d in rubric.critical_defects}
    defects = []
    for seen in raw.get("critical_defects_seen") or []:
        text = str(seen).strip()
        match = listed.get(text.lower())
        if match:
            defects.append(match)
        else:
            partial = [v for k, v in listed.items() if text.lower() in k or k in text.lower()]
            if partial:
                defects.append(partial[0])
    defects = list(dict.fromkeys(defects))

    failed = [c for c in criteria if c["verdict"] == "FAIL"]
    overall = "PASS" if not failed and not defects else "FAIL"

    return {
        "type": "grade",
        "route": "vertex" if USE_VERTEX else "ai_studio",
        "task_code": rubric.task_code,
        "task_title": rubric.task_title,
        "subtask_code": rubric.subtask_code,
        "subtask": rubric.subtask,
        "subject": rubric.subject,
        "overall": overall,
        "passed": len(criteria) - len(failed),
        "total": len(criteria),
        "criteria": criteria,
        "critical_defects": defects,
        "observed": str(raw.get("observed") or "").strip(),
        "frame": frame,
        "model": GRADER_MODEL,
        # Machine-drafted rubrics, unreviewed by an SME. The overlay says so.
        "provisional": True,
    }


async def grade(jpeg: bytes, rubric: Rubric, frame: dict | None = None) -> dict[str, Any]:
    """Grade one frame against one rubric. Never raises — errors come back as data."""
    started = time.monotonic()
    try:
        raw = await asyncio.wait_for(
            asyncio.to_thread(_blocking_grade, jpeg, rubric), timeout=TIMEOUT_S
        )
    except asyncio.TimeoutError:
        logger.warning("grading timed out after %.0fs", TIMEOUT_S)
        return {"type": "grade", "error": "timeout",
                "message": f"The grader did not answer within {TIMEOUT_S:.0f} seconds.",
                "task_code": rubric.task_code, "subtask_code": rubric.subtask_code}
    except Exception as error:  # noqa: BLE001 - a failed grade must not end the session
        logger.warning("grading failed: %s", error)
        return {"type": "grade", "error": "failed", "message": str(error)[:300],
                "task_code": rubric.task_code, "subtask_code": rubric.subtask_code}

    result = _assemble(raw, rubric, frame or {})
    result["latency_s"] = round(time.monotonic() - started, 2)
    logger.info(
        "graded %s: %s (%d/%d, %d critical) in %.1fs",
        rubric.key, result["overall"], result["passed"], result["total"],
        len(result["critical_defects"]), result["latency_s"],
    )
    return result


def spoken_summary(result: dict[str, Any]) -> str:
    """What the assistant says out loud. The overlay carries the detail."""
    if result.get("error"):
        return f"I could not grade that: {result.get('message', 'the grader failed')}."

    parts = [f"{result['overall']}. {result['passed']} of {result['total']} criteria passed."]
    if result.get("critical_defects"):
        parts.append("Critical: " + "; ".join(result["critical_defects"]))
    failed = [c for c in result.get("criteria", []) if c["verdict"] == "FAIL"]
    unseen = [c for c in failed if not c["observable"]]
    if unseen and len(unseen) == len(failed):
        parts.append("Everything that failed did so because the photo does not show it — "
                     "try again with a clearer view.")
    elif failed:
        parts.append("Failed: " + "; ".join(c["text"] for c in failed[:3]))
    return " ".join(parts)
