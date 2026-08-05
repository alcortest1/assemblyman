"""Unit tests: model selection, manifest creation, prompt construction, splicing.

`unittest` rather than pytest, which is not installed in this venv. The splicing
tests build real videos with ffmpeg's `testsrc` generator — a few hundred
kilobytes and about a second each — because the failure this suite most needs to
catch is a filter chain that produces the wrong duration, and that cannot be
tested against a mock.

    .venv/bin/python -m unittest discover -s erroneous_video_pipeline/tests -v
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from erroneous_video_pipeline import (analysis, media, models, outputs,
                                      planning, prompts, qa)
from erroneous_video_pipeline.config import ROOT


def make_video(path: Path, seconds: float, *, size: str = "320x240",
               fps: int = 30, colour: str = "red") -> Path:
    """A real, tiny, decodable clip."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [media.FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=size={size}:rate={fps}:duration={seconds}",
         "-f", "lavfi", "-i", f"color=c={colour}:size={size}:rate={fps}:duration={seconds}",
         "-filter_complex", "[0:v][1:v]blend=all_mode=average",
         "-c:v", "libx264", "-crf", "28", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", "-t", str(seconds), str(path)],
        check=True, capture_output=True)
    return path


# --------------------------------------------------------- model selection

def capability_row(model_id, *, ars, durations=None, resolutions=None,
                   frames=None, seed=False, pricing=None, description=""):
    return {"id": model_id, "name": model_id, "supported_aspect_ratios": ars,
            "supported_durations": durations, "supported_resolutions": resolutions,
            "supported_frame_images": frames, "seed": seed, "generate_audio": False,
            "pricing_skus": pricing or {"cents_per_second_output": "10"},
            "description": description}


class TestModelSelection(unittest.TestCase):
    def setUp(self):
        self.info = media.MediaInfo(
            path="x.mp4", duration_s=80.0, width=1440, height=1080, fps=29.97,
            video_codec="hevc", pix_fmt="yuv420p10le", color_transfer="arib-std-b67",
            has_audio=True)

    def test_rejects_models_without_the_source_aspect_ratio(self):
        rows = [capability_row("v/16x9-only", ars=["16:9", "9:16"],
                               durations=[5, 8], frames=["first_frame"])]
        with self.assertRaises(models.NoSuitableModel):
            models.select_model(rows, self.info, 8.0)

    def test_picks_a_model_that_supports_four_by_three(self):
        rows = [capability_row("v/16x9", ars=["16:9"], durations=[8], frames=["first_frame"]),
                capability_row("v/4x3", ars=["4:3"], durations=[8], frames=["first_frame"])]
        self.assertEqual(models.select_model(rows, self.info, 8.0).model_id, "v/4x3")

    def test_prefers_first_last_frame_over_first_frame_only(self):
        rows = [capability_row("v/first", ars=["4:3"], durations=[8], frames=["first_frame"]),
                capability_row("v/both", ars=["4:3"], durations=[8],
                               frames=["first_frame", "last_frame"])]
        selection = models.select_model(rows, self.info, 8.0)
        self.assertEqual(selection.mode, models.MODE_FIRST_LAST_FRAME)
        self.assertEqual(selection.model_id, "v/both")

    def test_video_reference_only_when_hosting_is_enabled(self):
        rows = [capability_row("v/both", ars=["4:3"], durations=[8],
                               frames=["first_frame", "last_frame"]),
                capability_row("runway/aleph-2", ars=["4:3"], durations=None, frames=None)]
        self.assertEqual(models.select_model(rows, self.info, 8.0).mode,
                         models.MODE_FIRST_LAST_FRAME)
        allowed = models.select_model(rows, self.info, 8.0, allow_video_reference=True)
        self.assertEqual(allowed.mode, models.MODE_VIDEO_REFERENCE)
        self.assertEqual(allowed.model_id, "runway/aleph-2")

    def test_verified_video_reference_outranks_a_cheaper_guess(self):
        """A described capability must not beat an empirically verified one on price."""
        rows = [capability_row("cheap/guess", ars=["4:3"], durations=[8],
                               frames=["first_frame", "last_frame"],
                               pricing={"duration_seconds": "0.01"},
                               description="supports instruction-guided edits"),
                capability_row("runway/aleph-2", ars=["4:3"],
                               pricing={"cents_per_second_output": "28"})]
        selection = models.select_model(rows, self.info, 8.0, allow_video_reference=True)
        self.assertEqual(selection.model_id, "runway/aleph-2")
        self.assertEqual(selection.capability.video_reference_basis, "verified")

    def test_named_model_is_validated_not_trusted(self):
        rows = [capability_row("v/16x9", ars=["16:9"], durations=[8], frames=["first_frame"])]
        with self.assertRaises(models.NoSuitableModel):
            models.select_model(rows, self.info, 8.0, requested="v/16x9")
        with self.assertRaises(models.NoSuitableModel):
            models.select_model(rows, self.info, 8.0, requested="v/not-real")

    def test_duration_must_be_reachable(self):
        rows = [capability_row("v/short", ars=["4:3"], durations=[2, 3], frames=["first_frame"])]
        with self.assertRaises(models.NoSuitableModel):
            models.select_model(rows, self.info, 18.0)

    def test_cost_estimates_across_sku_shapes(self):
        cents = models.Capability.from_api(
            capability_row("a", ars=["4:3"], pricing={"cents_per_second_output": "28"}))
        self.assertAlmostEqual(cents.estimate_cost(8, None), 2.24, places=3)

        dollars = models.Capability.from_api(
            capability_row("b", ars=["4:3"], pricing={"duration_seconds": "0.13"}))
        self.assertAlmostEqual(dollars.estimate_cost(8, None), 1.04, places=3)

        floor = models.Capability.from_api(capability_row(
            "c", ars=["4:3"], pricing={"cents_per_second_output": "28",
                                       "minimum_cents_per_generation": "560"}))
        self.assertAlmostEqual(floor.estimate_cost(1, None), 5.60, places=3)

        per_res = models.Capability.from_api(capability_row(
            "d", ars=["4:3"], pricing={"duration_seconds_720p": "0.05",
                                       "duration_seconds_1080p": "0.20"}))
        self.assertAlmostEqual(per_res.estimate_cost(10, "1080p"), 2.0, places=3)

        # Per-token video pricing cannot be derived from a duration; say so
        # rather than invent a number the budget guard would then trust.
        tokens = models.Capability.from_api(
            capability_row("e", ars=["4:3"], pricing={"video_tokens": "0.000007"}))
        self.assertIsNone(tokens.estimate_cost(8, None))

    def test_aspect_ratio_tolerance(self):
        self.assertEqual(media.nearest_aspect_ratio(1920, 1080)[0], "16:9")
        self.assertEqual(media.nearest_aspect_ratio(1440, 1080)[0], "4:3")
        label, error = media.nearest_aspect_ratio(1912, 1080)
        self.assertEqual(label, "16:9")
        self.assertLess(error, 0.01)          # off-by-8px is still 16:9
        _, wide = media.nearest_aspect_ratio(2190, 1080)
        self.assertGreater(wide, 0.05)        # 2:1 is genuinely off-menu


# ------------------------------------------------------ prompt construction

ANALYSIS = {
    "scene_description": "an orange plywood workbench in a bright training hangar",
    "camera_description": "a head-mounted first-person camera",
    "technician_description": "bare tattooed forearms, no gloves",
    "tools_and_equipment": ["blue-handled hand tube bender", "yellow tape measure"],
    "constraints_to_preserve": ["same bender", "same aluminium tube"],
}
ERROR = {
    "error_id": "wrong_bend_angle",
    "description": "stop bending early so the tube finishes under-bent",
    "visible_change": "the finished tube sits at a visibly shallower angle than the mark",
    "rubric_criterion_violated": "Bend angle matches the specified target",
}


class TestPromptConstruction(unittest.TestCase):
    def test_prompt_states_one_defect_and_preserves_the_rest(self):
        text = prompts.generation_prompt(ANALYSIS, ERROR)
        self.assertIn("under-bent", text)
        self.assertIn("Change only this", text)
        self.assertIn("blue-handled hand tube bender", text)
        self.assertIn("same aluminium tube", text)
        self.assertIn("first-person camera", text)

    def test_prompt_is_grounded_in_the_analysis_not_a_template(self):
        """The spec's worked example says 'gloves'; this footage has none."""
        text = prompts.generation_prompt(ANALYSIS, ERROR)
        self.assertIn("no gloves", text)
        self.assertNotIn("same gloves", text)

    def test_prompt_forbids_unrelated_changes(self):
        text = prompts.generation_prompt(ANALYSIS, ERROR)
        for forbidden in ("extra hands", "camera cuts", "scene changes", "other damage"):
            self.assertIn(forbidden, text)

    def test_prompt_respects_the_provider_length_cap(self):
        """runway/aleph-2 rejects prompts over 1000 characters with a 400."""
        verbose = {
            "scene_description": "a workshop " * 60,
            "camera_description": "a camera " * 60,
            "technician_description": "a technician " * 60,
            "tools_and_equipment": [f"tool number {i}" for i in range(40)],
            "constraints_to_preserve": [f"preserve item {i}" for i in range(40)],
        }
        text = prompts.generation_prompt(verbose, ERROR)
        self.assertLessEqual(len(text), prompts.MAX_PROMPT_CHARS)
        # The defect is the reason the clip exists; it must never be the part
        # that gets trimmed away to make room for scene description.
        self.assertIn("under-bent", text)

    def test_retry_feedback_cannot_burst_the_cap(self):
        """Feedback used to be concatenated after the prompt, overflowing it."""
        note = prompts.retry_note({
            "target_error_visible": False,
            "unintended_changes": [f"changed thing {i}" * 20 for i in range(4)],
            "additional_task_defects": [f"defect {i}" * 20 for i in range(3)]})
        text = prompts.generation_prompt(ANALYSIS, ERROR, info_note=note)
        self.assertLessEqual(len(text), prompts.MAX_PROMPT_CHARS)

    def test_retry_note_names_what_went_wrong(self):
        note = prompts.retry_note({
            "target_error_visible": False,
            "unintended_changes": ["the bench turned into a metal table"],
            "additional_task_defects": ["the tube was kinked"]})
        self.assertIn("did not show the required error", note)
        self.assertIn("metal table", note)
        self.assertIn("kinked", note)

    def test_retry_note_is_empty_when_nothing_was_wrong(self):
        self.assertEqual(prompts.retry_note({"target_error_visible": True}), "")

    def test_analysis_json_survives_fences_and_prose(self):
        self.assertEqual(analysis.extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(analysis.extract_json('Sure!\n{"a": 2}\nHope that helps'), {"a": 2})
        with self.assertRaises(analysis.AnalysisError):
            analysis.extract_json("no json here")


class TestWindowClamping(unittest.TestCase):
    def test_window_past_the_end_is_clamped(self):
        start, end, notes = analysis._clamp_window(
            {"editable_time_start": 70.0, "editable_time_end": 95.0}, 80.0)
        self.assertEqual(end, 80.0)
        self.assertTrue(any("past the clip" in n for n in notes))

    def test_overlong_window_keeps_the_tail(self):
        start, end, notes = analysis._clamp_window(
            {"editable_time_start": 0.0, "editable_time_end": 80.0}, 80.0)
        self.assertEqual(end, 80.0)
        self.assertAlmostEqual(end - start, 20.0, places=3)
        self.assertTrue(any("completed result" in n for n in notes))

    def test_instant_window_is_widened(self):
        start, end, _ = analysis._clamp_window(
            {"editable_time_start": 10.0, "editable_time_end": 10.2}, 80.0)
        self.assertGreaterEqual(end - start, 3.0)

    def test_inverted_window_is_an_error(self):
        with self.assertRaises(analysis.AnalysisError):
            analysis._clamp_window({"editable_time_start": 10.0, "editable_time_end": 4.0}, 80.0)


# --------------------------------------------------------- manifest writing

PLAN = {
    "task_code": "AM.I.D.S1", "subtask_id": "bend_the_line",
    "error_id": "wrong_bend_angle",
    "source_video": "data/videos/AM.I.D.S1/bend_the_line.mp4",
    "edit_window": {"start": 12.4, "end": 18.0},
    "required_error": "the tube is under-bent",
    "violated_criteria": ["Bend angle matches the specified target"],
}


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _row(self):
        return outputs.manifest_row(
            PLAN,
            generated_video=ROOT / "generated_errors" / "x.mp4",
            qa={"target_error_confidence": 0.91, "scene_preservation_score": 0.95,
                "equipment_preservation_score": 0.93, "camera_preservation_score": 0.97},
            selection={"model": "runway/aleph-2", "mode": "video_reference"},
            analysis_model="google/gemini-3.1-pro-preview",
            qa_model="google/gemini-3.1-pro-preview", cost=2.24)

    def test_manifest_row_has_the_required_fields(self):
        row = self._row()
        for key in ("source_video", "generated_video", "task_code", "subtask_id",
                    "label", "error_id", "violated_criteria", "critical_defects",
                    "synthetic", "generation_model", "analysis_model", "qa_model",
                    "cost", "qa_confidence"):
            self.assertIn(key, row)
        self.assertEqual(row["label"], "FAIL")
        self.assertTrue(row["synthetic"])

    def test_jsonl_round_trips_and_appends(self):
        path = self.root / "manifest.jsonl"
        outputs.append_jsonl(path, self._row())
        outputs.append_jsonl(path, self._row())
        self.assertEqual(len(outputs.read_jsonl(path)), 2)

    def test_atomic_write_leaves_no_part_file(self):
        target = self.root / "nested" / "summary.md"
        outputs.write_atomic(target, "hello")
        self.assertEqual(target.read_text(), "hello")
        self.assertFalse(list(self.root.rglob("*.part")))

    def test_api_keys_never_reach_disk(self):
        leak = "sk-or-v1-" + "a" * 40
        outputs.write_atomic(self.root / "x.json", json.dumps({"oops": leak}))
        self.assertNotIn(leak, (self.root / "x.json").read_text())

    def test_cost_report_writes_a_header_once(self):
        for _ in range(2):
            outputs.record_cost(self.root, task_code="AM.I.D.S1",
                                subtask_id="bend_the_line", error_id="e",
                                stage="generate", model="m", estimated=1.0, actual=1.1)
        lines = (self.root / outputs.COSTS).read_text().strip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("timestamp,"))

    def test_summary_reports_totals_and_marks_output_synthetic(self):
        outputs.append_jsonl(self.root / outputs.MANIFEST, self._row())
        outputs.append_jsonl(self.root / outputs.FAILURES,
                             {"task_code": "AM.I.D.S1", "subtask_id": "bend_the_line",
                              "error_id": "e2", "reason": "defect not visible", "cost": 0.5})
        text = outputs.write_summary(self.root).read_text()
        self.assertIn("synthetic", text)
        self.assertIn("$2.74", text)
        self.assertIn("defect not visible", text)

    def test_resume_finds_an_accepted_variant(self):
        outputs.append_jsonl(self.root / outputs.MANIFEST, self._row())
        self.assertIsNotNone(outputs.already_accepted(self.root, PLAN))
        self.assertIsNone(outputs.already_accepted(
            self.root, dict(PLAN, error_id="something_else")))

    def test_output_name_is_idempotent(self):
        self.assertEqual(planning.output_name(PLAN, 1),
                         "bend_the_line__wrong_bend_angle__v01.mp4")
        self.assertEqual(planning.output_name(PLAN, 1), planning.output_name(PLAN, 1))


# ---------------------------------------------------- time-window splicing

class TestSplicing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.source = make_video(cls.root / "source.mp4", 6.0, colour="blue")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_fit_duration_trims_a_long_segment(self):
        long = make_video(self.root / "long.mp4", 4.0)
        out = media.fit_duration(long, 2.0, self.root / "trimmed.mp4", fps=30)
        self.assertAlmostEqual(media.probe(out).duration_s, 2.0, delta=0.15)

    def test_fit_duration_pads_a_short_segment_by_holding(self):
        short = make_video(self.root / "short.mp4", 1.0)
        out = media.fit_duration(short, 3.0, self.root / "padded.mp4", fps=30)
        self.assertAlmostEqual(media.probe(out).duration_s, 3.0, delta=0.2)

    def test_splice_preserves_total_duration(self):
        replacement = make_video(self.root / "replacement.mp4", 2.0, colour="green")
        out = self.root / "spliced.mp4"
        record = media.splice(self.source, replacement, 2.0, 4.0, out,
                              work_dir=self.root / "work")
        self.assertTrue(record["duration_preserved"])
        self.assertAlmostEqual(media.probe(out).duration_s, 6.0, delta=0.35)

    def test_splice_normalises_a_mismatched_segment(self):
        """A segment of the wrong size, fps and length must still splice cleanly."""
        odd = make_video(self.root / "odd.mp4", 3.4, size="640x480", fps=24, colour="yellow")
        out = self.root / "spliced_odd.mp4"
        media.splice(self.source, odd, 1.0, 3.0, out, work_dir=self.root / "work2")
        result = media.probe(out)
        self.assertEqual((result.width, result.height), (320, 240))
        self.assertAlmostEqual(result.duration_s, 6.0, delta=0.35)
        self.assertTrue(media.is_playable(out)[0])

    def test_splice_at_the_very_start_has_no_prefix(self):
        replacement = make_video(self.root / "head.mp4", 2.0, colour="green")
        out = self.root / "spliced_head.mp4"
        record = media.splice(self.source, replacement, 0.0, 2.0, out,
                              work_dir=self.root / "work3")
        self.assertEqual(record["parts"], 2)
        self.assertAlmostEqual(media.probe(out).duration_s, 6.0, delta=0.35)

    def test_blank_output_is_rejected(self):
        black = self.root / "black.mp4"
        subprocess.run([media.FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "color=c=black:size=320x240:rate=30:duration=3",
                        "-c:v", "libx264", "-crf", "30", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p", str(black)], check=True, capture_output=True)
        ok, why = media.is_playable(black)
        self.assertFalse(ok)
        self.assertIn("black", why)

    def test_missing_file_is_not_playable(self):
        self.assertFalse(media.is_playable(self.root / "nope.mp4")[0])


# --------------------------------------------------------------- QA gating

class TestQAGate(unittest.TestCase):
    GOOD = {"target_error_visible": True, "target_error_confidence": 0.9,
            "scene_preservation_score": 0.95, "equipment_preservation_score": 0.92,
            "camera_preservation_score": 0.97, "rubric_result": "FAIL",
            "additional_task_defects": [], "_playable": True}

    def test_accepts_a_clean_candidate(self):
        self.assertTrue(qa.decide(dict(self.GOOD), 6.0)[0])

    def test_rejects_low_preservation(self):
        accepted, reasons = qa.decide(dict(self.GOOD, scene_preservation_score=0.5), 6.0)
        self.assertFalse(accepted)
        self.assertTrue(any("scene preservation" in r for r in reasons))

    def test_rejects_invisible_defect(self):
        self.assertFalse(qa.decide(dict(self.GOOD, target_error_visible=False), 6.0)[0])

    def test_rejects_a_pass_verdict(self):
        self.assertFalse(qa.decide(dict(self.GOOD, rubric_result="PASS"), 6.0)[0])

    def test_rejects_extra_defects_so_one_error_stays_isolated(self):
        accepted, reasons = qa.decide(
            dict(self.GOOD, additional_task_defects=["tube also kinked"]), 6.0)
        self.assertFalse(accepted)
        self.assertTrue(any("unrelated defects" in r for r in reasons))

    def test_missing_scores_count_against_acceptance(self):
        missing = {k: v for k, v in self.GOOD.items() if k != "camera_preservation_score"}
        accepted, reasons = qa.decide(missing, 6.0)
        self.assertFalse(accepted)
        self.assertTrue(any("no camera preservation" in r for r in reasons))

    def test_model_self_assessment_does_not_override_the_gate(self):
        """`accepted: true` from the QA model must not rescue a failing clip."""
        accepted, _ = qa.decide(dict(self.GOOD, accepted=True, rubric_result="PASS"), 6.0)
        self.assertFalse(accepted)

    def test_rejects_an_ungradeably_short_window(self):
        self.assertFalse(qa.decide(dict(self.GOOD), 0.5)[0])

    def test_all_failures_are_reported_not_just_the_first(self):
        _, reasons = qa.decide(
            dict(self.GOOD, target_error_visible=False, rubric_result="PASS",
                 scene_preservation_score=0.1), 6.0)
        self.assertGreaterEqual(len(reasons), 3)


# --------------------------------------------------------- budget guardrail

class TestUngradedVariantLabelling(unittest.TestCase):
    """A deviation no criterion grades must not be labelled as a rubric failure."""

    def test_catalog_supplies_an_error_stage_one_will_not_propose(self):
        from erroneous_video_pipeline import catalog
        self.assertIsNotNone(catalog.get("wrong_bend_angle"))
        self.assertIn("wrong_bend_angle", catalog.for_subtask("bend_the_line"))
        # A safety-wire archetype is not offered against a bending subtask.
        self.assertNotIn("wrong_turn_count", catalog.for_subtask("bend_the_line"))

    def test_unmatched_criterion_downgrades_the_label(self):
        record = type("R", (), {
            "task_code": "AM.I.D.S1", "subtask_id": "bend_the_line",
            "video_path": "data/videos/AM.I.D.S1/bend_the_line.mp4",
            "criteria_path": None, "procedure_path": None})()
        plan = planning.build_plan(
            record, {"editable_time_start": 1.0, "editable_time_end": 9.0},
            {"error_id": "wrong_bend_angle", "description": "d",
             "visible_change": "under-bent",
             "rubric_criterion_violated": "bend angle matches target"})
        self.assertFalse(plan["rubric_grounded"])
        self.assertEqual(plan["label"], "UNGRADED_VARIANT")
        self.assertIsNotNone(plan["rubric_coverage_note"])

    def test_qa_does_not_demand_fail_for_an_ungraded_variant(self):
        verdict = dict(TestQAGate.GOOD, rubric_result="PASS")
        self.assertFalse(qa.decide(verdict, 6.0)[0])
        self.assertTrue(qa.decide(verdict, 6.0, require_rubric_fail=False)[0])

    def test_ungraded_variant_still_needs_a_visible_defect(self):
        verdict = dict(TestQAGate.GOOD, rubric_result="PASS", target_error_visible=False)
        self.assertFalse(qa.decide(verdict, 6.0, require_rubric_fail=False)[0])

    def test_manifest_carries_the_plans_label_not_a_hardcoded_fail(self):
        row = outputs.manifest_row(
            dict(PLAN, label="UNGRADED_VARIANT", rubric_grounded=False,
                 violated_criteria=[]),
            generated_video=ROOT / "generated_errors" / "x.mp4",
            qa={"target_error_confidence": 0.9},
            selection={"model": "m", "mode": "video_reference"},
            analysis_model="a", qa_model="q", cost=1.0)
        self.assertEqual(row["label"], "UNGRADED_VARIANT")
        self.assertFalse(row["rubric_grounded"])


class TestBudget(unittest.TestCase):
    def test_reserve_refuses_to_breach_the_cap(self):
        from erroneous_video_pipeline.config import Budget, BudgetExceeded
        budget = Budget(limit=5.0)
        budget.reserve(3.0)
        budget.settle(3.0, 3.0)
        with self.assertRaises(BudgetExceeded):
            budget.reserve(2.5)

    def test_settle_books_the_actual_cost(self):
        from erroneous_video_pipeline.config import Budget
        budget = Budget(limit=10.0)
        budget.reserve(2.0)
        budget.settle(2.0, 0.75)
        self.assertAlmostEqual(budget.spent, 0.75)
        self.assertAlmostEqual(budget.remaining(), 9.25)

    def test_unpriced_job_still_reserves_nothing_but_books_actuals(self):
        from erroneous_video_pipeline.config import Budget
        budget = Budget(limit=1.0)
        budget.reserve(0.0)
        budget.settle(0.0, 0.9)
        self.assertAlmostEqual(budget.spent, 0.9)

    def test_no_limit_means_unbounded(self):
        from erroneous_video_pipeline.config import Budget
        budget = Budget(limit=None)
        budget.reserve(10_000.0)
        self.assertEqual(budget.remaining(), float("inf"))


if __name__ == "__main__":
    unittest.main()
