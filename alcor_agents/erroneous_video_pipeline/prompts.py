"""The three prompts, and the rule they share.

Every prompt here is built the same way: the procedure and the grading criteria
define what "correct" means, the video supplies only what is physically present,
and the model is forbidden from inventing an error the rubric does not already
name. That direction of authority is the whole point. A model left to invent
defects produces plausible-looking damage that no criterion grades, and a
negative example that fails no stated criterion is not a negative example — it is
just a different video.

The generation prompt is written in *positive preservation* terms. "Do not change
the bench" leaves a generator free to reinterpret the bench; naming the bench,
the bender, the bare forearms and the camera position as things that continue
unchanged gives it something to hold onto. Exactly one clause describes a change.
"""

from __future__ import annotations

import json

ANALYSIS_SYSTEM = """\
You are analysing first-person aviation-maintenance training footage so that a \
controlled, deliberately erroneous version can be generated for use as a negative \
example in a grading dataset.

You are given the video, the written procedure for the operation, and the grading \
criteria a student's work is judged against.

Your job is to describe what is physically in the footage, and to propose errors \
that the SUPPLIED CRITERIA would mark as failures.

Rules that matter:

1. Ground every candidate error in the criteria or the procedure text you were \
given. Quote the criterion it violates. If you cannot point to one, do not \
propose the error.
2. Describe only what you can actually see. Do not assume gloves, tools, \
markings or fixtures that are not visible in the footage — the generation prompt \
is built from your description, and anything you invent will be rendered into \
the output as an unintended change.
3. The edit window must be the SHORTEST span that contains the result-changing \
action plus enough of the finished result to be graded. Everything outside it is \
preserved untouched, so a wide window costs fidelity for nothing.
4. Prefer errors that are visible in a still frame of the finished work. An error \
that only exists mid-motion cannot be graded from the completed artifact.
5. Do not propose errors that require destroying, cutting or damaging equipment, \
or that would injure the technician.

Reply with JSON only, no prose around it:

{"task_action": "<the operation performed, in one sentence>",
 "scene_description": "<room, bench, lighting, background, what is on the bench>",
 "camera_description": "<mount, viewpoint, motion, framing, field of view>",
 "tools_and_equipment": ["<each tool actually visible, with colour and type>"],
 "technician_description": "<hands, sleeves, gloves or bare skin, clothing — only what is visible>",
 "original_successful_result": "<what the correct finished artifact looks like at the end>",
 "editable_time_start": <seconds>,
 "editable_time_end": <seconds>,
 "edit_window_rationale": "<why this is the shortest span containing the result-changing action>",
 "key_frames": [<timestamps in seconds worth using as first/last frame references>],
 "constraints_to_preserve": ["<things that must look identical in a regenerated segment>"],
 "candidate_errors": [
   {"error_id": "<snake_case>",
    "description": "<what the technician does differently>",
    "visible_change": "<what is observably different in the finished artifact>",
    "rubric_criterion_violated": "<quote the criterion text>",
    "severity": "clear_fail" | "borderline",
    "generation_feasibility": "high" | "medium" | "low"}
 ]}
"""

QA_SYSTEM = """\
You are the independent quality check on a synthetic negative example for an \
aviation-maintenance grading dataset.

You are given the ORIGINAL video, the GENERATED video, the error plan that was \
supposed to be applied, the procedure, and the grading criteria.

You are not grading the student. You are deciding whether this generated clip is \
usable as a labelled negative example. Be adversarial: it is far cheaper to \
reject a good clip than to ship one whose defect is invisible or whose scene \
silently changed.

Judge these separately:

- Is the INTENDED error clearly visible in the finished artifact? Not merely \
plausible, not merely implied by motion — visible in the result.
- Did anything else change? Different bench, different tools, extra or missing \
hands, altered camera position or framing, changed lighting or colour, added \
text or labels, warped anatomy, objects appearing or vanishing.
- Are there ADDITIONAL task defects beyond the intended one? A clip that fails \
three criteria cannot be labelled as isolating one.
- Would the criteria mark this FAIL?

Preservation scores are 0.0-1.0 where 1.0 means indistinguishable from the \
original in that respect.

Reply with JSON only:

{"target_error_visible": true | false,
 "target_error_confidence": 0.0-1.0,
 "scene_preservation_score": 0.0-1.0,
 "equipment_preservation_score": 0.0-1.0,
 "camera_preservation_score": 0.0-1.0,
 "unintended_changes": ["<each difference that is not the intended error>"],
 "additional_task_defects": ["<other rubric failures introduced>"],
 "rubric_result": "PASS" | "FAIL",
 "accepted": true | false,
 "reason": "<why, in one or two sentences>"}
"""


def analysis_messages(video_data_uri: str, procedure: str, criteria: str,
                      task_code: str, subtask_id: str) -> list[dict]:
    return [
        {"role": "system", "content": ANALYSIS_SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text":
                f"TASK CODE: {task_code}\nSUBTASK: {subtask_id}\n\n"
                f"=== PROCEDURE ===\n{procedure.strip()}\n\n"
                f"=== GRADING CRITERIA ===\n{criteria.strip()}\n\n"
                "Analyse the attached footage and propose candidate errors that "
                "these criteria would fail."},
            {"type": "video_url", "video_url": {"url": video_data_uri}},
        ]},
    ]


# Providers cap prompt length and the models endpoint does not publish the limit.
# `runway/aleph-2` rejects anything over 1000 characters, so that is the default
# ceiling; a longer prompt is not worth a failed submission.
MAX_PROMPT_CHARS = 1000


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip().rstrip(".")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rsplit(" ", 1)[0] + "…"


def generation_prompt(analysis: dict, error: dict, info_note: str = "",
                      max_chars: int = MAX_PROMPT_CHARS,
                      mode: str = "first_last_frame") -> str:
    """The positive-preservation prompt for one specific defect, within budget.

    Built from the analysis rather than a template so the preserved details are
    the ones actually in this footage. The spec's worked example names gloves;
    this repository's bend footage shows bare tattooed forearms, and a prompt
    asking for gloves would introduce the very unintended change the QA pass
    exists to catch.

    Assembled in priority order because of the length cap. The change clause is
    what makes this a negative example at all, so it is written first and never
    trimmed; scene detail and the prohibition list are added only while they fit.
    Truncating the defect to make room for "do not add extra hands" would produce
    a beautifully preserved clip of the wrong thing.
    """
    tools = ", ".join((analysis.get("tools_and_equipment") or [])[:4])
    camera = analysis.get("camera_description", "the same first-person camera")
    technician = analysis.get("technician_description", "the same technician")
    scene = analysis.get("scene_description", "the same workshop")
    preserve = analysis.get("constraints_to_preserve") or []

    if mode == "video_reference":
        return _edit_prompt(error, preserve, info_note, max_chars)

    # 1. The one change. Always present, and given the largest single share.
    change = (f"Change only this: {_clip(error['description'], 200)}, so that "
              f"{_clip(error['visible_change'], 260)}.")
    # 2. What continues unchanged, stated positively.
    keep = (f"Keep the same {_clip(camera, 55)}, {_clip(technician, 65)}, "
            f"{_clip(scene, 85)}"
            + (f", and the same {_clip(tools, 105)}" if tools else "")
            + ", in one continuous take with the same hand movement and lighting.")
    # 3. Correctness elsewhere, so exactly one criterion fails.
    rest = ("Everything else about the work stays correct"
            + (f": {_clip('; '.join(preserve), 110)}" if preserve else "") + ".")
    # 4. The prohibitions, last because they are the least load-bearing.
    forbid = ("Do not add objects, text, extra hands, camera cuts, scene changes, "
              "or any other damage.")

    parts = [change, keep, rest, forbid]
    if info_note:
        parts.append(_clip(info_note, 200) + ".")

    return _assemble(parts, change, max_chars)


def _assemble(parts: list[str], required: str, max_chars: int) -> str:
    prompt = ""
    for part in parts:
        candidate = f"{prompt} {part}".strip() if prompt else part
        if len(candidate) > max_chars:
            break
        prompt = candidate
    return prompt or _clip(required, max_chars)


def _edit_prompt(error: dict, preserve: list[str], info_note: str,
                 max_chars: int) -> str:
    """Edit-forward prompt for an in-context video editor.

    A video-to-video model already has the footage and preserves it by default —
    that is the whole premise of `runway/aleph-2`. Restating the bench, the
    lighting and the technician back to it spends most of the 1000-character
    budget describing what it was going to do anyway, and dilutes the one
    instruction that matters.

    That dilution is not hypothetical: the first paid attempt at
    `wrong_bend_angle` came back scoring 1.00/1.00/1.00 on scene, equipment and
    camera preservation with the edit simply not applied — a perfect
    reproduction of the source. So for this mode the prompt leads with an
    imperative edit, keeps only the constraints that stop the edit spilling into
    neighbouring work, and drops the scene description entirely.
    """
    change = (f"Edit the tube so that {_clip(error['visible_change'], 300)}. "
              f"Achieve it by showing that {_clip(error['description'], 180)}.")
    hold = ("Keep the same camera, hands, tools, bench and lighting, and change "
            "nothing else in the frame.")
    rest = (f"Leave the rest of the work correct: {_clip('; '.join(preserve), 140)}."
            if preserve else "")
    forbid = "No warping, no extra or detached hands, no vanishing objects."
    parts = [p for p in (hold, forbid, rest) if p]
    if info_note:
        parts.append(_clip(info_note, 180) + ".")
    return _assemble([change] + parts, change, max_chars)


def qa_messages(original_uri: str, generated_uri: str, error_plan: dict,
                procedure: str, criteria: str) -> list[dict]:
    return [
        {"role": "system", "content": QA_SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text":
                f"=== ERROR PLAN ===\n{json.dumps(error_plan, indent=2)}\n\n"
                f"=== PROCEDURE ===\n{procedure.strip()}\n\n"
                f"=== GRADING CRITERIA ===\n{criteria.strip()}\n\n"
                "The FIRST video is the ORIGINAL. The SECOND is the GENERATED "
                "candidate. Decide whether the generated clip is usable."},
            {"type": "video_url", "video_url": {"url": original_uri}},
            {"type": "video_url", "video_url": {"url": generated_uri}},
        ]},
    ]


def retry_note(qa: dict) -> str:
    """Feedback appended to a regeneration prompt after a QA rejection.

    Naming what went wrong last time is what makes a retry different from a
    re-roll; without it the same prompt tends to produce the same failure.
    """
    parts = []
    if not qa.get("target_error_visible"):
        parts.append("The previous attempt did not show the required error clearly. "
                     "Make the defect unmistakable in the finished artifact.")
    for change in (qa.get("unintended_changes") or [])[:4]:
        parts.append(f"The previous attempt wrongly changed: {change}. Keep it as in the source.")
    for defect in (qa.get("additional_task_defects") or [])[:3]:
        parts.append(f"The previous attempt introduced an unrelated defect: {defect}. "
                     "Perform that part of the work correctly.")
    return (" ".join(parts) or "") and ("\nCorrections from the previous attempt: " + " ".join(parts))
