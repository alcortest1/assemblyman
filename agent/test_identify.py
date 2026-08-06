"""Covers the checking that stands between the identifier and a student's grade.

The model call itself is not exercised here — it needs credentials and a photograph.
What is exercised is everything around it, because that is where a wrong answer
turns into a wrong rubric: a code the model invented, a code in the wrong case, a
refusal, and a confidence value nobody defined.

Run: python -m pytest agent/test_identify.py
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

# The google SDK is not needed to exercise the resolution logic, and requiring it
# would make these tests need credentials to run.
if "google.genai" not in sys.modules:  # pragma: no cover - import shim
    google = types.ModuleType("google")
    genai = types.ModuleType("google.genai")
    genai.types = types.ModuleType("google.genai.types")
    genai.Client = object
    sys.modules.setdefault("google", google)
    sys.modules.setdefault("google.genai", genai)
    sys.modules.setdefault("google.genai.types", genai.types)
if "grading" not in sys.modules:  # pragma: no cover - import shim
    grading = types.ModuleType("grading")
    grading.GRADER_MODEL = "stub-model"
    grading._client = lambda: None
    sys.modules["grading"] = grading

import identify  # noqa: E402

CATALOGUE = [
    {
        "task_code": "AM.I.D.S1",
        "task_title": "Rigid line with flare and bend",
        "subtasks": [
            {"subtask_code": "flare_the_line", "subtask": "Flare the line",
             "subject": "the flared tube end"},
            {"subtask_code": "cut_the_line", "subtask": "Cut the line"},
        ],
    },
    {
        "task_code": "AM.II.A.S6",
        "task_title": "Patch repair",
        "subtasks": [{"subtask_code": "rivet_layout", "subtask": "Rivet layout"}],
    },
]


def test_resolve_returns_the_catalogue_spelling_not_the_models():
    # The phone matches the suggestion against its picker by equality, so the codes
    # have to come back exactly as the catalogue spells them.
    result = identify._resolve(
        {"task_code": "am.i.d.s1", "subtask_code": "FLARE_THE_LINE",
         "confidence": "high", "observed": "a flared tube"},
        CATALOGUE,
    )

    assert result["matched"] is True
    assert result["task_code"] == "AM.I.D.S1"
    assert result["subtask_code"] == "flare_the_line"
    assert result["subtask"] == "Flare the line"
    assert result["confidence"] == "high"


def test_an_invented_code_is_treated_as_no_answer():
    # A model asked for a code will happily produce a plausible one. Passed on, it
    # finds no rubric and surfaces as "no rubric for that task and subtask", which
    # reads to the operator as their own mistake.
    result = identify._resolve(
        {"task_code": "AM.X.Y.Z1", "subtask_code": "weld_the_thing",
         "confidence": "high", "observed": "something"},
        CATALOGUE,
    )

    assert result["matched"] is False
    assert result["reason"] == "not_in_catalogue"


def test_a_real_task_with_a_subtask_from_another_one_is_rejected():
    result = identify._resolve(
        {"task_code": "AM.I.D.S1", "subtask_code": "rivet_layout",
         "confidence": "high", "observed": "rivets"},
        CATALOGUE,
    )

    assert result["matched"] is False


def test_no_match_is_honoured_rather_than_second_guessed():
    # An empty bench should produce no suggestion, not the nearest of forty-one.
    result = identify._resolve(
        {"task_code": "NO_MATCH", "subtask_code": "NO_MATCH",
         "confidence": "low", "observed": "an empty bench"},
        CATALOGUE,
    )

    assert result["matched"] is False
    assert result["reason"] == "no_match"
    assert result["observed"] == "an empty bench"


@pytest.mark.parametrize("value", ["certain", "", "VERY HIGH", None])
def test_confidence_outside_the_schema_falls_back_to_low(value):
    result = identify._resolve(
        {"task_code": "AM.I.D.S1", "subtask_code": "cut_the_line",
         "confidence": value, "observed": "a cut tube"},
        CATALOGUE,
    )

    assert result["confidence"] == "low"


def test_prompt_lists_every_subtask_and_leads_with_the_subject():
    prompt = identify._prompt(CATALOGUE)

    for code in ("flare_the_line", "cut_the_line", "rivet_layout"):
        assert code in prompt
    # The subject names the object that will be in the photograph, which is more
    # use to the model than the action that produced it.
    assert "the flared tube end" in prompt


def test_an_empty_catalogue_declines_without_calling_the_model():
    # Nothing to choose from, and a call would be charged for the privilege.
    def explode(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("the model must not be called with no catalogue")

    original, identify._blocking_identify = identify._blocking_identify, explode
    try:
        result = asyncio.run(identify.identify(b"jpeg", []))
    finally:
        identify._blocking_identify = original

    assert result == {"matched": False, "reason": "no_catalogue"}


def test_a_failing_model_call_declines_rather_than_raising():
    # A failed suggestion costs a spinner; a raised one would cost the grade.
    def explode(*_args, **_kwargs):
        raise RuntimeError("no credentials")

    original, identify._blocking_identify = identify._blocking_identify, explode
    try:
        result = asyncio.run(identify.identify(b"jpeg", CATALOGUE))
    finally:
        identify._blocking_identify = original

    assert result["matched"] is False
    assert result["reason"] == "failed"
