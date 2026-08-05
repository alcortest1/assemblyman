"""Named controlled-mistake archetypes that can be requested by id.

Stage 1 only proposes errors it can tie to a written criterion, which is the
right default — an error no rubric grades produces a clip that fails nothing.
But some deviations are worth generating anyway: bend angle and measurement are
real maintenance faults that the *current* compiled criteria happen not to
grade, because `criteria/AM.I.D.S1/AM.I.D.S1__bend_the_line.txt` records that
they "require measurement or the template in hand and are not graded here".

So this catalogue exists to let an operator ask for one by name. What it must
not do is let the resulting clip be mislabelled. Every plan records
`rubric_grounded`, and a variant whose deviation no criterion covers is labelled
`UNGRADED_VARIANT` rather than `FAIL` — it is a real physical deviation and a
useful stimulus, but calling it a rubric failure would put a wrong answer key
into the dataset.

`applies_to` is advisory: it keeps `--error wrong_bend_angle` from being offered
against a safety-wire clip, without hard-blocking an operator who knows better.
"""

from __future__ import annotations

ARCHETYPES: dict[str, dict] = {
    "wrong_bend_angle": {
        "description": "the technician stops bending early, releasing the bender "
                       "before the forming wheel reaches the marked target angle",
        "visible_change": "the completed tube is clearly under-bent — the angle "
                          "between the two straight runs is visibly shallower than "
                          "the target, while the bend itself stays smooth and undamaged",
        "criterion_hint": "bend angle matches the specified target",
        "applies_to": ["bend_the_line"],
        "severity": "clear_fail",
        "generation_feasibility": "high",
    },
    "bend_at_wrong_mark": {
        "description": "the technician seats the tube in the bender offset from the "
                       "layout mark, so the bend forms away from the marked location",
        "visible_change": "the centre of the completed bend sits visibly several "
                          "inches from the felt-tip layout mark, which remains "
                          "visible on the straight section beside it, while the bend "
                          "angle and tube condition stay correct",
        "criterion_hint": "bend is centred on the marked bend location",
        "applies_to": ["bend_the_line"],
        "severity": "clear_fail",
        "generation_feasibility": "high",
    },
    "wrong_turn_count": {
        "description": "the technician applies visibly fewer twists per inch than "
                       "the standard requires along the wire run",
        "visible_change": "the safety wire between the fasteners shows a clearly "
                          "loose, open twist with far fewer turns per inch than "
                          "specified, while routing and anchoring stay correct",
        "criterion_hint": "wire is twisted at the specified turns per inch",
        "applies_to": ["wire_safety_on_bolts_by_hand",
                       "wire_safety_on_bolts_with_safety_wire_pliers",
                       "wire_safety_on_a_turnbuckle_by_hand"],
        "severity": "clear_fail",
        "generation_feasibility": "medium",
    },
    "safety_wire_loosening_direction": {
        "description": "the technician routes the safety wire so it pulls the "
                       "fastener in the loosening direction",
        "visible_change": "the wire leaves the bolt head on the side that would "
                          "back the fastener off, so tension runs counter to the "
                          "tightening direction",
        "criterion_hint": "wire is routed in the tightening direction",
        "applies_to": ["wire_safety_on_bolts_by_hand",
                       "wire_safety_on_bolts_with_safety_wire_pliers"],
        "severity": "clear_fail",
        "generation_feasibility": "medium",
    },
    "incorrect_flare": {
        "description": "the technician seats the tube too far through the flaring "
                       "die before forming the flare",
        "visible_change": "the finished flare is visibly oversized and split at its "
                          "lip rather than forming a clean, even cone",
        "criterion_hint": "flare is even and free of splits or cracks",
        "applies_to": ["flare_the_line"],
        "severity": "clear_fail",
        "generation_feasibility": "medium",
    },
    "excessive_slack": {
        "description": "the technician leaves a long loop of slack in the run "
                       "rather than drawing it taut",
        "visible_change": "a pronounced loop of slack hangs between the anchor "
                          "points instead of a taut run",
        "criterion_hint": "no excessive slack",
        "applies_to": [],
        "severity": "clear_fail",
        "generation_feasibility": "medium",
    },
}


def get(error_id: str) -> dict | None:
    """The archetype for `error_id`, shaped like a Stage-1 candidate error."""
    entry = ARCHETYPES.get(error_id)
    if not entry:
        return None
    return {
        "error_id": error_id,
        "description": entry["description"],
        "visible_change": entry["visible_change"],
        "rubric_criterion_violated": entry["criterion_hint"],
        "severity": entry["severity"],
        "generation_feasibility": entry["generation_feasibility"],
        "from_catalog": True,
    }


def for_subtask(subtask_id: str | None) -> list[str]:
    if not subtask_id:
        return sorted(ARCHETYPES)
    return sorted(k for k, v in ARCHETYPES.items()
                  if not v["applies_to"] or subtask_id in v["applies_to"])
