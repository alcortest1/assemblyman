"""Works out which subtask a photograph of finished work belongs to.

The operator knows what they just built; making them say so twice — once to the
bench and once to a picker eleven tasks and forty-one subtasks deep — is asking
them to do the model's job. So the photograph is put to a vision model first,
with the catalogue of things this room can grade, and the picker opens on its
answer.

It is a suggestion and nothing more. The identification never grades on its own:
a wrong guess acted on silently would mark a student's work against the wrong
rubric, which is worse than any amount of scrolling. The operator confirms, and
the picker is a picker whatever the model said.

Two rules keep it honest. The reply is checked against the catalogue, because a
model asked for a code will happily invent a plausible one — and an invented code
resolves to no rubric, which surfaces as an error the operator cannot act on.
And the model is told to say so when nothing fits: a bench with no finished work
on it should produce no suggestion rather than the nearest of forty-one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from google.genai import types

from grading import GRADER_MODEL, _client

logger = logging.getLogger("assemblyman-identify")

# Same default as the grader. Identification is the easier of the two jobs —
# naming what is in a picture rather than judging it against a rubric — so it is
# separable if a run wants a cheaper model here and a better one for the verdict.
IDENTIFIER_MODEL = os.getenv("ASSEMBLYMAN_IDENTIFIER_MODEL", GRADER_MODEL)

# Shorter than the grader's. This one is in front of an operator holding a phone
# with a picker waiting on it, not part of a verdict worth waiting for.
TIMEOUT_S = float(os.getenv("ASSEMBLYMAN_IDENTIFY_TIMEOUT", "15"))

SYSTEM = """\
You are looking at a photograph taken by a student in an FAA Part 147 aircraft \
maintenance workshop, of work they have just finished.

You will be given a list of subtasks the school can assess. Name the one the \
photograph shows finished work for, using the exact codes from the list.

Judge only what is visible. Identify the work in front of you, not the work you \
would expect at that bench: a photograph of a flared tube is a flare whatever \
else is on the table.

Return NO_MATCH when the photograph shows no finished work from the list — an \
empty bench, a tool, a person, a wall, or a piece of work that is plainly none \
of the listed subtasks. A wrong guess sends a student's work to the wrong \
rubric, so a refusal is the better answer whenever you are not reasonably sure.

Confidence is "high" only when the work is unambiguous and clearly shown, \
"medium" when the subtask is likely but the photograph is partial or could \
plausibly be one of two, and "low" when you are guessing.\
"""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "task_code": {
            "type": "string",
            "description": 'Exact task code from the list, or "NO_MATCH".',
        },
        "subtask_code": {
            "type": "string",
            "description": 'Exact subtask code from the list, or "NO_MATCH".',
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "observed": {
            "type": "string",
            "description": "What the photograph shows, under 20 words.",
        },
    },
    "required": ["task_code", "subtask_code", "confidence", "observed"],
}

NO_MATCH = "NO_MATCH"


def _prompt(catalogue: list[dict]) -> str:
    """Lists every subtask the room can grade, one per line.

    The subject — "the flared tube end" — does more work here than the subtask
    name does, because it names the object that will be in the photograph rather
    than the action that produced it.
    """
    lines: list[str] = []
    for task in catalogue:
        title = task.get("task_title") or task.get("task_code", "")
        lines.append(f"\n{task.get('task_code', '')} — {title}")
        for subtask in task.get("subtasks") or []:
            code = subtask.get("subtask_code", "")
            name = subtask.get("subtask") or code
            subject = subtask.get("subject")
            suffix = f" — finished work: {subject}" if subject else ""
            lines.append(f"  {code} — {name}{suffix}")
    return (
        "ASSESSABLE SUBTASKS\n"
        + "\n".join(lines)
        + "\n\nWhich one does this photograph show finished work for?"
    )


def _blocking_identify(jpeg: bytes, catalogue: list[dict]) -> dict[str, Any]:
    response = _client().models.generate_content(
        model=IDENTIFIER_MODEL,
        contents=[
            types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"),
            _prompt(catalogue),
        ],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM,
            temperature=0,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
        ),
    )
    return json.loads(response.text)


def _resolve(raw: dict[str, Any], catalogue: list[dict]) -> dict[str, Any]:
    """Checks the model's answer against the catalogue.

    A code that is not in the catalogue is treated as no answer rather than
    passed on. Downstream it would find no rubric and surface as "no rubric for
    that task and subtask", which reads to the operator as their own mistake.
    """
    task_code = str(raw.get("task_code") or "").strip()
    subtask_code = str(raw.get("subtask_code") or "").strip()
    confidence = str(raw.get("confidence") or "low").strip().lower()
    observed = str(raw.get("observed") or "").strip()

    if task_code == NO_MATCH or subtask_code == NO_MATCH:
        return {"matched": False, "reason": "no_match", "observed": observed}

    for task in catalogue:
        if str(task.get("task_code", "")).upper() != task_code.upper():
            continue
        for subtask in task.get("subtasks") or []:
            if str(subtask.get("subtask_code", "")).lower() != subtask_code.lower():
                continue
            return {
                "matched": True,
                # Echo the catalogue's spelling, not the model's, so the phone can
                # match it against the picker by equality.
                "task_code": task["task_code"],
                "task_title": task.get("task_title"),
                "subtask_code": subtask["subtask_code"],
                "subtask": subtask.get("subtask"),
                "confidence": confidence if confidence in ("high", "medium", "low") else "low",
                "observed": observed,
            }

    logger.info("identifier named %s / %s, which is not in the catalogue",
                task_code, subtask_code)
    return {"matched": False, "reason": "not_in_catalogue", "observed": observed}


async def identify(jpeg: bytes, catalogue: list[dict]) -> dict[str, Any]:
    """Suggest a task and subtask for one photograph. Never raises.

    Errors come back as an unmatched result: the picker still opens, the
    operator still picks, and nothing about the grade that follows is different.
    A failed suggestion costs a few seconds of a spinner; a raised one would
    cost the grade.
    """
    if not catalogue:
        return {"matched": False, "reason": "no_catalogue"}

    try:
        raw = await asyncio.wait_for(
            asyncio.to_thread(_blocking_identify, jpeg, catalogue), timeout=TIMEOUT_S
        )
    except asyncio.TimeoutError:
        logger.warning("identification timed out after %.0fs", TIMEOUT_S)
        return {"matched": False, "reason": "timeout"}
    except Exception as error:  # noqa: BLE001 - a failed suggestion must not fail the grade
        logger.warning("identification failed: %s", error)
        return {"matched": False, "reason": "failed"}

    result = _resolve(raw, catalogue)
    if result.get("matched"):
        logger.info("identified %s / %s (%s)", result["task_code"],
                    result["subtask_code"], result["confidence"])
    return result
