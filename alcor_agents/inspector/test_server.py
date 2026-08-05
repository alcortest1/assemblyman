"""Regression tests for the inspector's atomic catalog and eval metrics."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from inspector import server, vlm


class AtomicCatalogTests(unittest.TestCase):
    def test_safety_wire_catalog_has_all_atom_kinds_and_stable_evidence(self):
        pack, _, error = server.load_pack("AM.I.E.S1")
        self.assertIsNone(error)
        self.assertIsNotNone(pack)

        catalog = server.atomic_catalog("AM.I.E.S1", pack)
        self.assertGreater(catalog["counts"]["activity"], 0)
        self.assertGreater(catalog["counts"]["correctness"], 0)
        self.assertGreater(catalog["counts"]["defect"], 0)
        self.assertEqual(
            catalog["counts"]["total"],
            sum(catalog["counts"][kind] for kind in ("activity", "correctness", "defect")),
        )

        ids = [atom["id"] for atom in catalog["atoms"]]
        self.assertEqual(len(ids), len(set(ids)))
        activity = next(atom for atom in catalog["atoms"] if atom["kind"] == "activity")
        self.assertTrue(activity["examples"])
        self.assertIn("frame_start", activity["examples"][0])
        self.assertIn("frame_end", activity["examples"][0])

    def test_selective_metrics_include_abstentions(self):
        metrics = server.decision_metrics(
            [
                {"truth": "correct", "prediction": "pass"},
                {"truth": "correct", "prediction": "review"},
                {"truth": "incorrect", "prediction": "fail"},
                {"truth": "incorrect", "prediction": "pass"},
            ]
        )
        self.assertEqual(metrics["support"], 4)
        self.assertEqual(metrics["abstained"], 1)
        self.assertEqual(metrics["precision"], 0.5)
        self.assertEqual(metrics["recall"], 0.5)
        self.assertEqual(metrics["defect_recall"], 0.5)
        self.assertEqual(metrics["coverage"], 0.75)

    def test_dataset_and_run_are_joined_by_atom_and_sample(self):
        pack, _, _ = server.load_pack("AM.I.E.S1")
        catalog = server.atomic_catalog("AM.I.E.S1", pack)
        atom = next(item for item in catalog["atoms"] if item["kind"] == "correctness")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            datasets = root / "datasets"
            runs = root / "runs"
            datasets.mkdir()
            runs.mkdir()

            dataset = {
                "schema_version": 1,
                "dataset_id": "fixture",
                "title": "Fixture",
                "task_code": "AM.I.E.S1",
                "split": "test",
                "samples": [
                    {
                        "sample_id": "correct",
                        "task_label": "correct",
                        "atom_labels": [{"atom_id": atom["id"], "label": "correct"}],
                    },
                    {
                        "sample_id": "incorrect",
                        "task_label": "incorrect",
                        "atom_labels": [{"atom_id": atom["id"], "label": "incorrect"}],
                    },
                ],
            }
            run = {
                "schema_version": 1,
                "run_id": "fixture-run",
                "task_code": "AM.I.E.S1",
                "dataset_id": "fixture",
                "system": {"id": "fixture", "name": "Fixture agent"},
                "predictions": [
                    {
                        "sample_id": "correct",
                        "task_prediction": {"status": "pass"},
                        "atom_predictions": [{"atom_id": atom["id"], "status": "pass"}],
                    },
                    {
                        "sample_id": "incorrect",
                        "task_prediction": {"status": "fail"},
                        "atom_predictions": [{"atom_id": atom["id"], "status": "fail"}],
                    },
                ],
            }
            (datasets / "fixture.json").write_text(json.dumps(dataset))
            (runs / "fixture.json").write_text(json.dumps(run))

            old_datasets = server.EVAL_DATASETS_DIR
            old_runs = server.EVAL_RUNS_DIR
            server.EVAL_DATASETS_DIR = datasets
            server.EVAL_RUNS_DIR = runs
            try:
                summary = server.evaluation_summary("AM.I.E.S1", catalog)
            finally:
                server.EVAL_DATASETS_DIR = old_datasets
                server.EVAL_RUNS_DIR = old_runs

        self.assertEqual(summary["atom_readiness"][atom["id"]]["correct"], 1)
        self.assertEqual(summary["atom_readiness"][atom["id"]]["incorrect"], 1)
        self.assertEqual(summary["runs"][0]["task_metrics"]["precision"], 1.0)
        self.assertEqual(summary["runs"][0]["task_metrics"]["defect_recall"], 1.0)
        self.assertEqual(summary["runs"][0]["atom_metrics"][0]["precision"], 1.0)


class ModelConfigTests(unittest.TestCase):
    def test_every_default_model_is_one_the_picker_offers(self):
        """The two lists are edited by hand and drift apart silently. A default
        that is not in MODELS arrives pre-ticked in the browser but has no chip
        to untick it, so the run goes out against a model nobody chose — and the
        failure surfaces as an API error per call, not as a config mistake.
        """
        for model_id in vlm.DEFAULT_MODELS:
            self.assertIn(model_id, vlm.MODELS_BY_ID)

    def test_every_model_carries_the_pricing_the_estimate_needs(self):
        """A missing rate silently prices that model's share of a run at zero,
        which reads as a cheap run rather than an unknown one."""
        for model in vlm.MODELS:
            with self.subTest(model=model["id"]):
                self.assertTrue(model["label"] and model["vendor"])
                self.assertGreater(model["in_per_m"], 0)
                self.assertGreater(model["out_per_m"], 0)


class VerdictParsingTests(unittest.TestCase):
    """A reply we cannot read must never become a silent pass."""

    def test_plain_json(self):
        parsed = vlm.parse_verdict(
            '{"verdict":"pass","confidence":0.9,"observed":"o","rationale":"r"}'
        )
        self.assertEqual(parsed["verdict"], "pass")
        self.assertEqual(parsed["confidence"], 0.9)
        self.assertEqual(parsed["parse"], "json")

    def test_fenced_json_with_surrounding_prose(self):
        parsed = vlm.parse_verdict(
            'Here is my assessment.\n```json\n{"verdict": "fail", "confidence": 0.7}\n```\nHope that helps.'
        )
        self.assertEqual(parsed["verdict"], "fail")
        self.assertEqual(parsed["parse"], "json")

    def test_confidence_is_clamped_and_survives_garbage(self):
        self.assertEqual(vlm.parse_verdict('{"verdict":"pass","confidence":9}')["confidence"], 1.0)
        self.assertEqual(vlm.parse_verdict('{"verdict":"pass","confidence":"x"}')["confidence"], 0.0)

    def test_unreadable_reply_abstains_rather_than_passing(self):
        for reply in ("", "I think it looks fine to me overall.", "{{{"):
            with self.subTest(reply=reply):
                self.assertEqual(vlm.parse_verdict(reply)["verdict"], "unsure")

    def test_keyword_fallback_when_json_is_malformed(self):
        parsed = vlm.parse_verdict('verdict: "fail" — the wire is not seated,,,}')
        self.assertEqual(parsed["verdict"], "fail")
        self.assertEqual(parsed["parse"], "keyword")


class DraftParsingTests(unittest.TestCase):
    def test_clean_draft(self):
        parsed = vlm.parse_draft(json.dumps({
            "criterion": "- A\n- B",
            "not_photo_gradeable": ["pull test"],
            "photo_limitations": "too wide",
            "required_framing": "macro",
        }))
        self.assertEqual(parsed["parse"], "json")
        self.assertEqual(parsed["not_photo_gradeable"], ["pull test"])

    def test_truncated_reply_salvages_the_criterion(self):
        """A criterion list can outrun max_tokens; the useful part comes first."""
        cut = '{"criterion": "- The barrel shows a clean indent.\\n- No bare conductor is vis'
        parsed = vlm.parse_draft(cut)
        self.assertEqual(parsed["parse"], "truncated")
        self.assertIn("clean indent", parsed["criterion"])
        # Escaped newlines must survive as real ones, or the criterion is one blob.
        self.assertIn("\n", parsed["criterion"])
        self.assertIsNotNone(parsed["photo_limitations"])

    def test_unsalvageable_reply_yields_no_criterion(self):
        parsed = vlm.parse_draft("I could not comply with that request.")
        self.assertIsNone(parsed["criterion"])
        self.assertEqual(parsed["parse"], "unparsed")


class PhotoTargetTests(unittest.TestCase):
    def test_suggested_frames_map_sections_to_clips(self):
        """An unsegmented task must still be runnable, and say what it guessed."""
        pack, _, _ = server.load_pack("AM.I.D.S1")
        targets = server.photo_targets("AM.I.D.S1", pack)
        steps = [t for t in targets if t["kind"] == "step" and t.get("frame")]
        self.assertTrue(steps, "no step target picked up a suggested frame")

        # The mapping is by name overlap between a section title and a clip, and
        # it has to survive both short words ("Cut the Tubing" -> cut_the_line)
        # and inflections ("Bending the Tubing" -> bend_the_line).
        by_section = {t.get("section"): t["video"] for t in steps if t.get("section")}
        for section, expected in (("Cut the Tubing", "cut_the_line"),
                                  ("Bending the Tubing", "bend_the_line"),
                                  ("Flare the Tube", "flare_the_line")):
            self.assertEqual(by_section.get(section), expected, section)

        for target in steps:
            self.assertTrue(target["frame_suggested"])

    def test_steps_in_a_section_advance_through_its_clip(self):
        """Each step of a section needs its own frame, not the clip's last one.

        Giving all three steps of "Cut the Tubing" the clip's final frame graded
        "Decide the size of tubing to use" against a photo of tubing already
        cut — a confident failure on a step performed correctly, which is worse
        than no frame at all because a wrong verdict is harder to spot than a
        missing one.
        """
        for acs in ("AM.I.D.S1", "AM.III.F.S11", "AM.I.D.S7"):
            with self.subTest(task=acs):
                pack, _, _ = server.load_pack(acs)
                steps = [t for t in server.photo_targets(acs, pack)
                         if t["kind"] == "step" and t.get("frame")]
                self.assertTrue(steps)

                frames = [(t["video"], t["frame"]) for t in steps]
                self.assertEqual(len(frames), len(set(frames)),
                                 "two steps were given the same suggested frame")

                by_section = {}
                for target in steps:
                    by_section.setdefault(target["section"], []).append(target)
                for section, members in by_section.items():
                    with self.subTest(section=section):
                        # Steps are performed in order, so their frames must
                        # advance through the clip in the same order.
                        names = [t["frame"] for t in members]
                        self.assertEqual(names, sorted(names))
                        index, count = members[-1]["frame_position"]
                        self.assertEqual((index, count), (len(members), len(members)))

                # Sections sharing one clip take successive slices of it rather
                # than each spreading over the whole thing; the section that owns
                # the final slice ends on the clip's last frame.
                by_clip = {}
                for target in steps:
                    by_clip.setdefault(target["video"], []).append(target)
                for clip, members in by_clip.items():
                    with self.subTest(clip=clip):
                        clip_frames = server.frame_names(acs, clip, "detail")
                        self.assertEqual(members[-1]["frame"], clip_frames[-1])

    def test_only_subtasks_and_steps_are_built(self):
        """The tab is two levels — a task is subtasks, a subtask is steps — and
        anything else has no view to appear in. A target with no view is not
        neutral: it still lands in the API and in bulk selections, so it gets
        graded and billed for while being invisible.
        """
        for acs in sorted(p.name for p in server.TASKS_DIR.iterdir() if p.is_dir()):
            with self.subTest(task=acs):
                pack, _, _ = server.load_pack(acs)
                kinds = {t["kind"] for t in server.photo_targets(acs, pack)}
                self.assertTrue(kinds <= {"section", "subtask", "step"}, kinds)

    def test_a_subtask_is_graded_one_point_at_a_time(self):
        """Sent whole, a sheet returns one verdict for ten conditions: you learn
        the subtask failed and never which condition did. Each point is its own
        call so a failure names itself.
        """
        pack, _, _ = server.load_pack("AM.I.D.S1")
        target = next(t for t in server.photo_targets("AM.I.D.S1", pack)
                      if t.get("clip") == "bend_the_line")
        checks = target["checks"]

        criteria = [c for c in checks if not c["defect"]]
        defects = [c for c in checks if c["defect"]]
        self.assertEqual(len(criteria), 6)
        self.assertEqual(len(defects), 4)
        self.assertEqual([c["id"] for c in criteria], ["c1", "c2", "c3", "c4", "c5", "c6"])
        self.assertEqual([c["id"] for c in defects], ["d1", "d2", "d3", "d4"])
        self.assertIn("at least one completed bend", criteria[0]["statement"])

        # The combination rule is not itself a condition, and neither is the
        # provenance footer. Either one graded as a point would be an
        # unanswerable question asked of a photograph.
        joined = " ".join(c["statement"] for c in checks)
        self.assertNotIn("Overall PASS requires", joined)
        self.assertNotIn("FAA-H-8083", joined)

    def test_a_critical_defect_is_graded_as_its_absence(self):
        """The polarity trap. A sheet writes defects as things that must NOT be
        present, so grading "Tube is kinked or collapsed flat at the bend" as
        written scores a PASS on a kinked tube — a verdict that is not merely
        wrong but backwards, and reads as a clean result.
        """
        pack, _, _ = server.load_pack("AM.I.D.S1")
        for target in server.photo_targets("AM.I.D.S1", pack):
            for check in target.get("checks") or []:
                if not check["defect"]:
                    continue
                with self.subTest(check=check["id"]):
                    self.assertTrue(check["statement"].startswith(
                        "The finished work shows no such defect:"))

        # Restated, not merely prefixed: the defect's own wording survives inside
        # the sentence so it stays traceable to the sheet.
        kinked = next(c for t in server.photo_targets("AM.I.D.S1", pack)
                      if t.get("clip") == "bend_the_line"
                      for c in t["checks"] if c["id"] == "d2")
        self.assertIn("kinked or collapsed flat", kinked["statement"])

    def test_an_edited_criterion_is_still_split_into_points(self):
        """Editing is the whole point of the criterion box, and an edit that
        silently collapsed the subtask back to one call would take per-point
        grading away exactly when someone is trying to improve it.
        """
        edited = "Criteria\n1. First condition holds\n2. Second condition holds\n"
        points = server.sheet_checks(edited)
        self.assertEqual([c["statement"] for c in points],
                         ["First condition holds", "Second condition holds"])

        # Free prose with no headings is one criterion, not zero points — the
        # run path falls back to a single call rather than dropping the target.
        self.assertEqual(server.sheet_checks("Just grade the whole thing."), [])

    def test_subtasks_are_graded_against_their_own_criteria_sheet(self):
        """`criteria/<ACS>/` holds one sheet per subtask, written about the
        finished subtask. It beats the previous default — the member steps'
        criteria concatenated — which had no notion of a finished subtask and so
        could not state what the article should look like once one was done.
        """
        for acs in sorted(p.name for p in server.TASKS_DIR.iterdir() if p.is_dir()):
            pack, _, _ = server.load_pack(acs)
            sections = [t for t in server.photo_targets(acs, pack)
                        if t["kind"] == "section"]
            for target in sections:
                with self.subTest(task=acs, section=target["section"]):
                    if target.get("needs_criteria"):
                        # A clip the paperwork never documented. It must carry
                        # nothing rather than something invented: an empty
                        # criterion is skipped by the run, a made-up one would
                        # be graded and reported as an assessment standard.
                        self.assertEqual(target["criterion"], "")
                        self.assertIsNone(target["criterion_file"])
                        self.assertEqual(target["checks"], [])
                        continue
                    # The join is on clip name where the campus names its clips
                    # after the work, and on subtask title where it numbers them
                    # (AM.II.A.S6 films flush_patch_1..8). Every section resolves
                    # by one route or the other; a regression in either shows up
                    # here as a section falling back to the concatenation.
                    self.assertEqual(target["criterion_source"], "criteria.subtask")
                    self.assertTrue(target["criterion_file"])
                    # The provenance footer is metadata about the sheet, not a
                    # condition the work is judged against, so it must not reach
                    # the model as one.
                    self.assertNotIn("Source basis", target["criterion"])

    def test_every_criteria_sheet_reaches_a_target_of_its_own(self):
        """The converse of the test above, and the one that actually caught
        things. That test asks whether every *target* found a sheet, which
        passes trivially when a sheet is never reached: seven of thirty-seven
        were ungradeable while it stayed green.

        Three separate causes, one per task. AM.I.E.S1 was hand-compiled before
        sections existed so all its steps carry `section: null` and no subtask
        target was built at all. AM.II.K.S3 lost the three subtasks its
        segmentation pass covered completely, because a fully covered section
        was skipped. AM.II.A.S6 folded "Create the Patch Doubler" in as a
        note-only heading, leaving its sheet with no section to join to.
        """
        for acs in sorted(p.name for p in server.TASKS_DIR.iterdir() if p.is_dir()):
            with self.subTest(task=acs):
                pack, _, _ = server.load_pack(acs)
                sections = [t for t in server.photo_targets(acs, pack)
                            if t["kind"] == "section"]
                written = {s["code"] for s in server.read_subtask_criteria(acs).values()}
                self.assertTrue(written, f"{acs} has no criteria sheets")

                graded = [t["criterion_file"].split("__")[-1].removesuffix(".txt")
                          for t in sections if t.get("criterion_file")]
                self.assertEqual(written, set(graded))
                # One target per sheet. Two targets sharing a sheet would grade
                # the same subtask twice and double its weight in any run.
                self.assertEqual(len(graded), len(set(graded)))

    def test_a_fully_segmented_subtask_keeps_its_sheet_and_gains_a_real_frame(self):
        """A reviewed segment grades one interval *inside* the work; the sheet
        grades the finished subtask those intervals add up to. Different
        questions, so covering a section with segments must not retire it.

        The segments do settle something the name match could not, though.
        AM.II.K.S3 films `elect_conn_2..5`, which no subtask title can ever
        match, so these three had no clip to hang a frame on. The interval that
        covered the section's last step names both.
        """
        pack, _, _ = server.load_pack("AM.II.K.S3")
        targets = server.photo_targets("AM.II.K.S3", pack)
        by_section = {t["section"]: t for t in targets if t["kind"] == "section"}

        for section in ("Set Up the DNC Crimper", "Crimp the Wire",
                        "Insert the Pin into the Electrical Connector"):
            with self.subTest(section=section):
                target = by_section[section]
                self.assertEqual(target["criterion_source"], "criteria.subtask")
                self.assertTrue(target["frame_exists"], "reviewed footage exists")
                # Evidence, not an even-pace guess, and it must not claim to be
                # the other thing.
                self.assertTrue(target["frame_reviewed"])
                self.assertFalse(target["frame_suggested"])
                self.assertEqual(target["clip"], target["video"])

    def test_a_sheet_with_no_pack_section_is_graded_without_inventing_a_clip(self):
        """AM.II.A.S6's doubler sheet has no section, and its clips are
        `flush_patch_1..8` — a same-prefix numbered series whose names carry no
        signal to match a title against. The eight real sections take those
        clips positionally; a ninth subtask matching `flush_patch_1` on the word
        "patch" would be an accident of vocabulary, and grading the doubler
        against footage of the damage being identified is worse than offering no
        frame at all. So it gets a target and a criterion, and no clip.
        """
        pack, _, _ = server.load_pack("AM.II.A.S6")
        target = next(t for t in server.photo_targets("AM.II.A.S6", pack)
                      if t["target_id"] == "section:create-the-patch-doubler")
        self.assertEqual(target["criterion_source"], "criteria.subtask")
        self.assertIn("doubler", target["criterion"].lower())
        self.assertIsNone(target["clip"])
        self.assertFalse(target["frame_exists"])
        # No pack steps behind it, so it must not report a step count that would
        # read as a section that lost its steps.
        self.assertEqual(target["step_count"], 0)

    def test_sheet_titles_place_subtasks_on_the_clips_that_demonstrate_them(self):
        """AM.I.E.S1's steps carry no section — they group by `variant` — so its
        subtasks are `bolts_hand`, `bolts_pliers` and `turnbuckle_hand`, and no
        string match gets from those to sheets titled "Wire Safety on Bolts by
        Hand". Both sides land on the same clip, and that is the join.

        The pliers subtask is filmed as four takes, `safety_wire_pliers_1..4`.
        Its clip is the one the reviewer put its *last* step in, not the
        alphabetically first of the four — a subtask's frame should show the
        work finished, and take 1 is where it starts.
        """
        pack, _, _ = server.load_pack("AM.I.E.S1")
        targets = server.photo_targets("AM.I.E.S1", pack)
        by_file = {t["criterion_file"].split("__")[-1].removesuffix(".txt"): t
                   for t in targets
                   if t["kind"] == "section" and t.get("criterion_file")}
        self.assertEqual(
            {code: target["clip"] for code, target in by_file.items()},
            {"wire_safety_on_a_turnbuckle_by_hand":
                "insert_wire_for_double_wrap_turnbuckle_safety",
             "wire_safety_on_bolts_by_hand": "safety_wire_by_hand",
             "wire_safety_on_bolts_with_safety_wire_pliers": "safety_wire_pliers_4"})
        for target in by_file.values():
            self.assertTrue(target["frame_exists"])

        # One subtask per sheet, and every step owned by one of them. Read only
        # off `section`, the pack had no subtask that owned any step, so the
        # steps and the sheets sat in two piles that never met.
        self.assertEqual(sorted(t["section"] for t in targets if t["kind"] == "section"),
                         ["bolts_hand", "bolts_pliers", "turnbuckle_hand"])
        self.assertEqual(sum(t["step_count"] for t in targets if t["kind"] == "section"),
                         len(pack["steps"]))

    def test_sheet_notes_parse_as_whole_sentences(self):
        """A sheet's Notes are separated by ".;" and contain a plain ";" inside
        a sentence, so splitting on the bare semicolon tore one note into two
        half-claims. The notes no longer surface in the tab, but the parser
        still reads them and a bad split would corrupt the criterion boundary
        they sit behind.
        """
        sheets = server.read_subtask_criteria("AM.I.D.S1")
        notes = sheets["bend_the_line"]["notes"]
        self.assertEqual(len(notes), 3)
        self.assertIn("only gross flattening or kinking is gradeable", notes[0])
        for note in notes:
            self.assertTrue(note.endswith("."), note)

    def test_a_suggested_clip_does_not_move_between_runs(self):
        """Ties must break the same way every time.

        Scoring iterated a set of clip names, so with equal scores the winner
        followed string hash order — randomised per process. The suggested frame
        could then change across a server restart, which makes a verdict
        impossible to trace back to what was actually graded.
        """
        pack, _, _ = server.load_pack("AM.III.F.S11")
        first = {t["target_id"]: t.get("frame")
                 for t in server.photo_targets("AM.III.F.S11", pack)}
        for _ in range(3):
            again = {t["target_id"]: t.get("frame")
                     for t in server.photo_targets("AM.III.F.S11", pack)}
            self.assertEqual(again, first)

    def test_targets_use_final_frames_and_prefer_pack_checks(self):
        pack, _, _ = server.load_pack("AM.II.K.S3")
        targets = server.photo_targets("AM.II.K.S3", pack)
        self.assertTrue(targets)

        for target in targets:
            # Every target either carries a criterion — the deliverable, which
            # exists whether or not a photo of the work does yet — or says it
            # has none. What it must never do is carry something that is not an
            # acceptance standard while presenting it as one: a reviewer's
            # description of the footage graded as a criterion returns verdicts
            # about prose, and `fail` was its most common answer.
            self.assertTrue(target["criterion"] or target.get("needs_criteria"),
                            f"{target['target_id']} has neither a criterion nor a flag")
            if target["kind"] in ("step", "section"):
                # Derived from the pack, so no reviewed segment pinned a frame
                # to the target itself. A frame can still arrive two ways: it
                # may be *suggested* by matching the step or section title to a
                # clip name, which is what makes an unsegmented task runnable
                # without picking 27 frames by hand; or it may be *reviewed*,
                # taken from the interval that covered the section's last step.
                # Which of the two it was must be stated, or a guess would be
                # indistinguishable from evidence.
                if target["frame"] is None:
                    self.assertFalse(target["frame_exists"])
                else:
                    self.assertNotEqual(
                        bool(target["frame_suggested"]), bool(target.get("frame_reviewed")),
                        f"{target['target_id']} must be exactly one of suggested/reviewed")
                    self.assertTrue(target["frame"].startswith("t"))
                    self.assertIn(target["frame"], target["frame_url"])
                continue
            # The final frame is the completed state; that is the whole premise.
            self.assertTrue(target["frame"].startswith("t"))
            self.assertIn(target["frame"], target["frame_url"])
            self.assertTrue(target["frame_url"].startswith("/files/build/frames/"))

        by_source = {t["criterion_source"] for t in targets}
        self.assertIn("pack.checks", by_source)

        # A segment mapped to a pack step must derive its criterion from that
        # step's checks, not from the reviewer's prose description of the
        # footage. Asserted against criterion_default, since `criterion` is the
        # effective text and may carry a saved user edit.
        mapped = next(t for t in targets if t["criterion_source"] == "pack.checks")
        step = next(s for s in pack["steps"] if s["id"] == mapped["step_id"])
        self.assertIn(step["checks"][0]["statement"], mapped["criterion_default"])

    def test_a_saved_edit_shadows_the_pack_default(self):
        pack, _, _ = server.load_pack("AM.II.K.S3")
        with tempfile.TemporaryDirectory() as temporary:
            original = server.PHOTO_DIR
            server.PHOTO_DIR = Path(temporary)
            try:
                base = server.photo_targets("AM.II.K.S3", pack)[0]
                store = Path(temporary) / "AM.II.K.S3"
                store.mkdir(parents=True)
                (store / "prompts.json").write_text(
                    json.dumps({base["target_id"]: "MY EDITED CRITERION"})
                )
                edited = next(
                    t for t in server.photo_targets("AM.II.K.S3", pack)
                    if t["target_id"] == base["target_id"]
                )
            finally:
                server.PHOTO_DIR = original

        self.assertEqual(edited["criterion"], "MY EDITED CRITERION")
        self.assertTrue(edited["edited"])
        # The pack text must remain recoverable so the edit can be undone.
        self.assertEqual(edited["criterion_default"], base["criterion_default"])

    def test_both_step_id_encodings_resolve_to_pack_criteria(self):
        """Integer-indexed and verbatim step ids must both reach pack checks.

        AM.I.E.S1 writes 1-based integers scoped to a clip's variant; AM.II.K.S3
        writes pack step ids directly. When this regressed, the symptom was
        silent: targets still built, but graded against prose descriptions
        instead of acceptance criteria.
        """
        for acs in ("AM.I.E.S1", "AM.II.K.S3"):
            with self.subTest(task=acs):
                pack, _, _ = server.load_pack(acs)
                targets = server.photo_targets(acs, pack)
                # Scoped to subtasks: the task roll-up also reports pack.checks
                # but aggregates every step, so it has no single step_id.
                from_checks = [t for t in targets
                               if t["criterion_source"] == "pack.checks"
                               and t["kind"] == "subtask"]
                self.assertTrue(from_checks, f"{acs} resolved no pack checks")
                # The mapped id must be a real pack step id, not the raw encoding.
                pack_ids = {s["id"] for s in pack["steps"]}
                for target in from_checks:
                    self.assertIn(target["step_id"], pack_ids)

    def test_every_check_reaches_the_criterion_whatever_its_observable(self):
        """The whole rubric is graded, not the part a camera happens to reach.

        This asserted the opposite while a criterion was graded as one blob:
        folding in a pull test made the whole thing ungradeable, because a
        compound criterion is only as gradeable as its least gradeable clause.
        Each check is now its own call, so that reasoning no longer holds and
        pre-filtering the rubric only hid conditions the assessment includes.
        """
        pack, _, _ = server.load_pack("AM.II.K.S3")
        targets = server.photo_targets("AM.II.K.S3", pack)
        by_step = {s["id"]: s for s in pack["steps"]}

        checked_any = False
        non_photo_seen = False
        for target in targets:
            if target["criterion_source"] != "pack.checks" or target["kind"] != "subtask":
                continue
            step = by_step[target["step_id"]]
            for check in step.get("checks") or []:
                self.assertIn(check["statement"], target["criterion_default"])
                checked_any = True
                if (check.get("observable") or "photo") != "photo":
                    non_photo_seen = True
        self.assertTrue(checked_any)
        # The point of the change: a measurement or pull test is part of the
        # rubric and is graded like anything else. Each check is its own call
        # now, so one that a photo cannot settle returns `unsure` on its own
        # line instead of dragging every other check on the step with it.
        self.assertTrue(non_photo_seen, "fixture no longer covers a non-photo check")

    def test_expectation_semantics(self):
        self.assertTrue(vlm.expectation_met("pass", "pass"))
        self.assertFalse(vlm.expectation_met("pass", "unsure"))
        # not_pass is satisfied by either a fail or an abstention.
        self.assertTrue(vlm.expectation_met("not_pass", "fail"))
        self.assertTrue(vlm.expectation_met("not_pass", "unsure"))
        self.assertFalse(vlm.expectation_met("not_pass", "pass"))
        # No claim, or no verdict, means nothing to score.
        self.assertIsNone(vlm.expectation_met(None, "pass"))
        self.assertIsNone(vlm.expectation_met("pass", None))

    def test_store_accepts_both_legacy_string_and_variant_object_forms(self):
        with tempfile.TemporaryDirectory() as temporary:
            original = server.PHOTO_DIR
            server.PHOTO_DIR = Path(temporary)
            try:
                store = Path(temporary) / "T"
                store.mkdir(parents=True)
                (store / "prompts.json").write_text(json.dumps({
                    "legacy": "just an edited criterion",
                    "modern": {
                        "criterion": "edited",
                        "variants": [{"id": "v1", "label": "negated",
                                      "criterion": "something else", "expected": "fail"}],
                    },
                }))
                loaded = server.read_criteria_store("T")

                # Round-tripping must not silently drop the variants.
                server.write_criteria_store("T", loaded)
                reloaded = server.read_criteria_store("T")
            finally:
                server.PHOTO_DIR = original

        self.assertEqual(loaded["legacy"]["criterion"], "just an edited criterion")
        self.assertEqual(loaded["legacy"]["variants"], [])
        self.assertEqual(len(loaded["modern"]["variants"]), 1)
        self.assertEqual(reloaded["modern"]["variants"][0]["expected"], "fail")

    def test_empty_entries_are_pruned_on_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            original = server.PHOTO_DIR
            server.PHOTO_DIR = Path(temporary)
            try:
                server.write_criteria_store("T", {
                    "keep": {"criterion": "text", "variants": []},
                    "keep_variants": {"criterion": None, "variants": [{"id": "v1"}]},
                    "drop": {"criterion": "   ", "variants": []},
                })
                reloaded = server.read_criteria_store("T")
            finally:
                server.PHOTO_DIR = original

        self.assertIn("keep", reloaded)
        self.assertIn("keep_variants", reloaded)
        self.assertNotIn("drop", reloaded)


class DraftedPackTests(unittest.TestCase):
    """A task compiled by packs/compile_pack.py must reach both tabs.

    Before this, Atoms and Photo assessment were derived only from a
    hand-compiled pack plus reviewed segments, so nine of the eleven pilot tasks
    rendered empty. These assert the drafted path end to end, on a task that has
    a pack but no segmentation.
    """

    ACS = "AM.III.F.S11"

    def setUp(self):
        if not (server.TASKS_DIR / self.ACS / "pack.yaml").exists():
            self.skipTest(f"{self.ACS} has no compiled pack")
        self.pack, _, self.error = server.load_pack(self.ACS)

    def test_pack_parses_and_declares_its_provenance(self):
        self.assertIsNone(self.error)
        # A drafted pack and a hand-compiled one both read `status: draft`, so
        # provenance is the only thing that tells a reader which they are
        # looking at. Losing it would let machine output pass for an AIM standard.
        provenance = self.pack.get("provenance")
        self.assertIsInstance(provenance, dict)
        self.assertTrue(provenance.get("generator"))
        self.assertIsNone(provenance.get("reviewed_by"))
        self.assertEqual(self.pack.get("status"), "draft")

    def test_atoms_are_populated_without_any_segmentation(self):
        self.assertFalse(
            sorted((server.ANALYSIS_DIR / self.ACS).glob("*.segments.json")),
            "this test is about the no-segments path",
        )
        catalog = server.atomic_catalog(self.ACS, self.pack)
        self.assertGreater(catalog["counts"]["correctness"], 0)
        self.assertGreater(catalog["counts"]["defect"], 0)
        # No reviewed footage, so no activity can be derived from a segment.
        # The pack's sections are still the subtasks the work is taught in, and
        # they appear as activities marked `pack.section` — with no reference
        # interval, because none has been established. Anything sourced from a
        # reviewed segment here would be claiming a review that never happened.
        activities = [a for a in catalog["atoms"] if a["kind"] == "activity"]
        self.assertTrue(activities, "sections should surface as subtasks")
        for activity in activities:
            self.assertEqual(activity["source"], "pack.section")
            self.assertEqual(activity["examples"], [])
            self.assertTrue(activity["section"])

    def test_every_step_becomes_a_target_carrying_a_criterion(self):
        targets = server.photo_targets(self.ACS, self.pack)
        steps = {t["step_id"] for t in targets if t["kind"] == "step"}
        self.assertEqual(steps, {s["id"] for s in self.pack["steps"]})
        for target in targets:
            self.assertTrue(target["criterion"].strip())
        # And the subtasks those steps belong to, each carrying its own points.
        subtasks = [t for t in targets if t["kind"] == "section"]
        self.assertTrue(subtasks)
        for subtask in subtasks:
            self.assertTrue(subtask["checks"], subtask["target_id"])

    def test_criteria_are_drafted_from_procedure_and_handbook(self):
        criteria = server.read_drafted_criteria(self.ACS)
        if not criteria:
            self.skipTest("no drafted criteria for this task")
        entries = criteria["entries"]
        self.assertIn("task", entries)
        target = next(t for t in server.photo_targets(self.ACS, self.pack)
                      if t["kind"] == "step" and t["step_id"] in entries)
        self.assertEqual(target["criterion_source"], "drafted.step")
        # Attribution is what makes a criterion defensible; a condition with no
        # source is one an instructor cannot stand behind.
        self.assertTrue(target["sources"])
        self.assertTrue(all(s.get("source") for s in target["sources"]))

    def test_handbook_section_is_offered_with_its_provenance(self):
        sections = server.handbook_sections(self.ACS, self.pack)
        self.assertTrue(sections)
        section = sections[0]
        self.assertTrue(section["text"].strip())
        self.assertIn("cited_by_source", section)
        self.assertTrue(section["file"].startswith("/files/tasks/"))


class FrameChoiceTests(unittest.TestCase):
    def test_candidates_are_offered_for_extracted_clips(self):
        candidates = server.frame_candidates("AM.II.K.S3")
        self.assertTrue(candidates)
        for candidate in candidates:
            self.assertGreater(candidate["frame_count"], 0)
            self.assertTrue(candidate["first_frame"].startswith("t"))

    def test_a_task_with_no_frames_offers_none_rather_than_failing(self):
        self.assertEqual(server.frame_candidates("AM.NOPE.X1"), [])

    def test_only_real_frames_of_this_task_are_accepted(self):
        """The picked frame name lands in a filesystem path.

        Accepting it unchecked would let a run read any file the server can
        reach, so the name is required to be one of the task's own extracted
        frames rather than merely to look plausible.
        """
        names = server.frame_names("AM.II.K.S3", "elect_conn_2", "detail")
        self.assertTrue(names)
        self.assertNotIn("../../../etc/passwd", names)
        self.assertNotIn("t000000_00.jpg", server.frame_names("AM.NOPE.X1", "x", "detail"))


class CriterionSplitTests(unittest.TestCase):
    """A criterion is graded a point at a time, whichever shape it arrives in."""

    SHEET = (
        "Assess the completed flare.\n\nCriteria\n"
        "1. The flare is concentric with the tube bore.\n"
        "2. The sleeve is captive behind the flare.\n\n"
        "Critical defects\n- The flare is split at the rim.\n\n"
        "Overall decision\nPASS requires every criterion to pass.\n")
    STEP = ("- Both wire ends are inserted into the two fittings\n"
            "- Marker marks are visible at each bend location\n"
            "- The wire is a single continuous length\n")

    def test_a_sheet_splits_into_criteria_and_restated_defects(self):
        points = server.sheet_checks(self.SHEET)
        self.assertEqual([p["id"] for p in points], ["c1", "c2", "d1"])
        self.assertFalse(points[0]["defect"])
        # A defect graded as written scores a pass on the defect being present.
        self.assertTrue(points[2]["defect"])
        self.assertIn("shows no such defect", points[2]["statement"])
        # The combining rule is not itself a point.
        self.assertNotIn("PASS requires", " ".join(p["statement"] for p in points))

    def test_a_step_criterion_splits_on_its_bullets(self):
        """The shape that never split, and the reason steps could not pass.

        Sent whole, its conditions are ANDed: one unobservable condition
        abstains the step and one failed condition fails it, so a four-condition
        step passed 7 times in 753 calls across every saved run.
        """
        points = server.sheet_checks(self.STEP)
        self.assertEqual([p["id"] for p in points], ["c1", "c2", "c3"])
        self.assertEqual(points[0]["statement"],
                         "Both wire ends are inserted into the two fittings")
        # Positive conditions, so none is restated as an absence — a step
        # criterion has no section that inverts polarity.
        self.assertFalse(any(p["defect"] for p in points))
        self.assertNotIn("no such defect", " ".join(p["statement"] for p in points))

    def test_a_single_condition_is_not_a_split(self):
        """Splitting it would relabel the target as a roll-up of one."""
        self.assertEqual(server.sheet_checks("- Only one thing to check"), [])
        self.assertEqual(server.sheet_checks("Just grade the whole thing."), [])
        self.assertEqual(server.sheet_checks(""), [])

    def test_a_sheet_with_headings_never_falls_through_to_the_bullet_split(self):
        """Its `Source basis` bullets are provenance, not conditions."""
        sheet = ("Criteria\n1. The flare is concentric.\n\n"
                 "Source basis\n- procedure sheet: AIM S1 p.4\n- handbook: AC 43.13 p.9\n")
        points = server.sheet_checks(sheet)
        self.assertEqual([p["statement"] for p in points], ["The flare is concentric."])

    def test_conditions_split_whether_or_not_they_carry_a_marker(self):
        """Under a heading, every line is a condition.

        Requiring a number meant a criterion typed into the browser as bullets,
        or as plain lines, produced no points at all — and that is not an error
        anyone sees: the caller falls back to grading the whole sheet in one
        call, which `apply_thresholds` fails on any single condition. Three
        saved criteria in this pilot are written exactly that way.
        """
        bare = ("Criteria\nThe flare is concentric with the tube bore.\n"
                "The sleeve is captive behind the flare.\n\n"
                "Critical defects\nThe flare is split at the rim.\n")
        bulleted = ("Criteria\n- The flare is concentric with the tube bore.\n"
                    "- The sleeve is captive behind the flare.\n\n"
                    "Critical defects\n- The flare is split at the rim.\n")
        for shape, text in (("bare", bare), ("bulleted", bulleted)):
            with self.subTest(shape=shape):
                points = server.sheet_checks(text)
                self.assertEqual([p["id"] for p in points], ["c1", "c2", "d1"])
                self.assertEqual(points[0]["statement"],
                                 "The flare is concentric with the tube bore.")
                self.assertIn("shows no such defect", points[2]["statement"])

    def test_every_step_target_now_splits(self):
        """The whole point: no step target may still be graded as one blob."""
        unsplit = []
        for acs in ("AM.I.D.S1", "AM.I.E.S1", "AM.II.A.S6", "AM.III.F.S11"):
            pack, _, _ = server.load_pack(acs)
            for target in server.photo_targets(acs, pack, 3):
                if target["kind"] != "step" or not (target.get("criterion") or "").strip():
                    continue
                points = server.sheet_checks(target["criterion"])
                conditions = [l for l in target["criterion"].splitlines()
                              if re.match(r"\s*[-*•]", l)]
                if len(conditions) > 1 and not points:
                    unsplit.append(f"{acs} {target['target_id']}")
        self.assertEqual(unsplit, [], f"{len(unsplit)} step target(s) still graded whole")


class UngradeableIntervalTests(unittest.TestCase):
    """A reviewed interval with no pack step has no acceptance standard."""

    def test_a_video_description_is_not_offered_as_a_criterion(self):
        """It is prose about one moment of a clip, not a standard.

        Graded as one it returned `fail` on 39% of 362 calls across saved runs —
        the highest rate of any criterion source, and none of it about
        workmanship. The interval stays listed and keeps its description; it
        simply cannot be graded until someone writes a criterion for it.
        """
        found = 0
        for acs in ("AM.I.E.S1", "AM.II.K.S3"):
            pack, _, _ = server.load_pack(acs)
            for target in server.photo_targets(acs, pack, 3):
                if target.get("criterion_source") != "segment.description":
                    continue
                found += 1
                self.assertEqual(target["criterion"], "", target["target_id"])
                self.assertTrue(target["needs_criteria"], target["target_id"])
                # The reviewer's account of the footage is still carried — it is
                # what an author writes the criterion from.
                self.assertTrue(target["description"], target["target_id"])
        self.assertGreater(found, 0, "no such targets found; the test proves nothing")

    def test_an_operator_written_criterion_still_grades(self):
        """Flagging it must not make the target permanently ungradeable."""
        pack, _, _ = server.load_pack("AM.II.K.S3")
        target = next(t for t in server.photo_targets("AM.II.K.S3", pack, 3)
                      if t.get("criterion_source") == "segment.description")
        with tempfile.TemporaryDirectory() as tmp:
            original = server.PHOTO_DIR
            try:
                server.PHOTO_DIR = Path(tmp)
                server.write_criteria_store("AM.II.K.S3", {
                    target["target_id"]: {"criterion": "- The contact is fully seated.",
                                          "variants": []}})
                again = next(t for t in server.photo_targets("AM.II.K.S3", pack, 3)
                             if t["target_id"] == target["target_id"])
                self.assertEqual(again["criterion"], "- The contact is fully seated.")
                self.assertTrue(again["edited"])
            finally:
                server.PHOTO_DIR = original


class NegativeCriteriaTests(unittest.TestCase):
    """Match controls: criteria a photo of correct work should not satisfy.

    A run against reference frames cannot tell a grader from a model that agrees
    with whatever it is handed, because `pass` is the right answer everywhere.
    These are the only thing in the harness that can.
    """

    def _target(self, acs="AM.I.D.S1"):
        pack, _, _ = server.load_pack(acs)
        return next(t for t in server.photo_targets(acs, pack, 3) if t["kind"] == "step")

    def test_foreign_controls_are_borrowed_from_other_tasks_only(self):
        """Never from this task, or it would be a criterion the work may satisfy."""
        pool = server.foreign_criteria("AM.I.D.S1")
        self.assertGreater(len(pool), 20)
        self.assertNotIn("AM.I.D.S1", {p["task_code"] for p in pool})
        # Borrowed verbatim from a real sheet, so it reads like the criteria
        # around it and cannot be picked out by register.
        for item in pool[:10]:
            self.assertTrue(item["criterion"].strip())
            self.assertFalse(item["criterion"].startswith("The finished work shows no"))

    def test_a_foreign_control_expects_not_pass_rather_than_fail(self):
        """The subject is absent, so abstaining is correct behaviour.

        Demanding `fail` would score a grader that says "this article is not in
        the photograph" as wrong, which is the reason `not_pass` exists.
        """
        variants, spend = server.negative_variants(
            "AM.I.D.S1", self._target(), [], None, ("foreign",))
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0]["expected"], "not_pass")
        self.assertEqual(variants[0]["negative_kind"], "foreign")
        # Costs nothing: no model writes it.
        self.assertEqual(spend, 0.0)

    def test_a_foreign_control_is_stable_between_runs(self):
        """Two runs of one target must be tested against the same control.

        A control that moved would make a change in the score unattributable.
        """
        target = self._target()
        first, _ = server.negative_variants("AM.I.D.S1", target, [], None, ("foreign",))
        for _ in range(3):
            again, _ = server.negative_variants("AM.I.D.S1", target, [], None, ("foreign",))
            self.assertEqual(again, first)

    def test_generated_controls_carry_the_point_they_came_from(self):
        """A miss is only readable against the positive verdict for the same
        condition, so the link between them is part of the control."""
        points = [{"id": "c1", "statement": "The twists are even and tight.", "defect": False},
                  {"id": "c2", "statement": "The pigtail is bent back.", "defect": False}]

        def fake(*, model, criterion, subject=None, **kw):
            return {"error": None, "cost_usd": 0.001, "negatives": [
                {"kind": "inversion", "criterion": f"NOT({criterion})",
                 "changed": "reversed", "expected": "fail"},
                {"kind": "substitution", "criterion": f"SWAP({criterion})",
                 "changed": "one value", "expected": "fail"}]}

        variants, spend = server.negative_variants(
            "AM.I.D.S1", self._target(), points, "m",
            ("inversion", "substitution"), drafter=fake)
        self.assertEqual(len(variants), 4)
        self.assertAlmostEqual(spend, 0.002, places=6)
        for variant in variants:
            self.assertEqual(variant["expected"], "fail")
            self.assertIn(variant["negative_of"], ("c1", "c2"))
            self.assertTrue(variant["positive_criterion"])
        self.assertEqual({v["negative_of"] for v in variants}, {"c1", "c2"})

    def _batch_drafter(self):
        def fake(*, model, criterion, subject=None, **kw):
            return {"error": None, "cost_usd": 0.001, "negatives": [
                {"kind": "inversion", "criterion": f"NOT({criterion})",
                 "changed": "reversed", "expected": "fail"}]}
        return fake

    def test_batch_writes_controls_for_every_requested_target(self):
        """The button writes a whole selection at once; each target must come
        back with its own controls, keyed so the browser can file them."""
        targets = [
            {"target_id": "section:a", "label": "A",
             "criterion": "1. The wire is taut.\n2. The pigtail is bent back."},
            {"target_id": "section:b", "label": "B", "criterion": "1. The cut is square."},
        ]
        out = server.negative_variants_batch(
            "AM.I.D.S1", ["section:a", "section:b"], "m", ("inversion",),
            targets=targets, drafter=self._batch_drafter())

        self.assertEqual([r["target_id"] for r in out["results"]],
                         ["section:a", "section:b"])
        self.assertEqual(out["points"], 3)
        self.assertEqual(out["variants"], 3)
        self.assertAlmostEqual(out["cost_usd"], 0.003, places=6)
        for result in out["results"]:
            self.assertIsNone(result["error"])
            for variant in result["variants"]:
                # Provenance has to survive the batch path or a miss cannot be
                # read against the positive verdict for the same condition.
                self.assertEqual(variant["expected"], "fail")
                self.assertTrue(variant["negative_of"])
                self.assertTrue(variant["positive_criterion"])

    def test_batch_reports_a_target_with_no_criterion_rather_than_dropping_it(self):
        """A subtask that silently produced nothing reads as one needing nothing."""
        targets = [{"target_id": "section:a", "label": "A", "criterion": "1. Taut."},
                   {"target_id": "section:empty", "label": "B", "criterion": ""}]
        out = server.negative_variants_batch(
            "AM.I.D.S1", ["section:a", "section:empty", "section:ghost"], "m",
            ("inversion",), targets=targets, drafter=self._batch_drafter())

        errors = {r["target_id"]: r["error"] for r in out["results"] if r["error"]}
        self.assertEqual(errors, {"section:empty": "no_criterion",
                                  "section:ghost": "unknown_target"})
        # The one good target is still written, and the failures cost nothing.
        self.assertEqual(out["variants"], 1)
        self.assertAlmostEqual(out["cost_usd"], 0.001, places=6)

    def test_batch_drafts_from_unsaved_criterion_text(self):
        """The browser is the source of truth for an edit in progress, so a
        control written from the saved text would negate the wrong condition."""
        targets = [{"target_id": "section:a", "label": "A", "criterion": "1. Old text."}]
        out = server.negative_variants_batch(
            "AM.I.D.S1", ["section:a"], "m", ("inversion",),
            edited={"section:a": "1. Edited text."},
            targets=targets, drafter=self._batch_drafter())
        self.assertIn("Edited text", out["results"][0]["variants"][0]["criterion"])

    def test_an_inversion_expects_fail_not_not_pass(self):
        """The article is in frame and the work contradicts the wording.

        Accepting `unsure` would forgive exactly the behaviour the control
        exists to catch — a grader declining to commit on something it can see.
        """
        self.assertFalse(vlm.expectation_met("fail", "unsure"))
        self.assertTrue(vlm.expectation_met("fail", "fail"))
        # Whereas a foreign control tolerates the abstention.
        self.assertTrue(vlm.expectation_met("not_pass", "unsure"))
        self.assertFalse(vlm.expectation_met("not_pass", "pass"))

    def test_the_drafter_rejects_replies_it_cannot_use(self):
        """A control of unknown kind cannot be scored or read, so it is dropped."""
        def reply(text):
            return lambda payload, key: {
                "choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

        good = vlm.draft_negative_criteria(
            model="google/gemini-3.6-flash", criterion="The twists are tight.", key="k",
            post=reply('{"negatives":[{"kind":"inversion","criterion":"The wire is slack.",'
                       '"changed":"reversed"},{"kind":"nonsense","criterion":"x"},'
                       '{"kind":"substitution","criterion":""}],"skipped":null}'))
        self.assertIsNone(good["error"])
        self.assertEqual(len(good["negatives"]), 1)
        self.assertEqual(good["negatives"][0]["expected"], "fail")

        # A criterion whose negation could only ever be answered "unsure" is
        # better skipped than turned into a control that measures nothing.
        skipped = vlm.draft_negative_criteria(
            model="google/gemini-3.6-flash", criterion="The alloy is 2024-T3.", key="k",
            post=reply('{"negatives":[],"skipped":"rests on something a photo cannot settle"}'))
        self.assertEqual(skipped["negatives"], [])
        self.assertIn("cannot settle", skipped["skipped"])

        self.assertEqual(
            vlm.draft_negative_criteria(model="m", criterion="  ", key="k",
                                        post=reply("{}"))["error"], "no_criterion")

    def test_generated_controls_survive_being_saved(self):
        """Provenance must reach the store, or a miss cannot be traced back."""
        with tempfile.TemporaryDirectory() as tmp:
            original = server.PHOTO_DIR
            try:
                server.PHOTO_DIR = Path(tmp)
                server.write_criteria_store("AM.I.D.S1", {"step:x": {
                    "criterion": None,
                    "variants": [{"id": "neg-c1-inv", "label": "c1 inversion",
                                  "criterion": "The wire is slack.", "expected": "fail",
                                  "negative_kind": "inversion", "negative_of": "c1",
                                  "positive_criterion": "The twists are tight."}]}})
                saved = server.read_criteria_store("AM.I.D.S1")["step:x"]["variants"][0]
                self.assertEqual(saved["negative_kind"], "inversion")
                self.assertEqual(saved["negative_of"], "c1")
            finally:
                server.PHOTO_DIR = original


class NegativeSheetTests(unittest.TestCase):
    """A negated sheet is the same instrument aimed the wrong way.

    Same sections, same order, split by the same parser into the same number of
    points — because the whole purpose is to set its pass rate beside the
    criteria's, and two things counted differently cannot be subtracted.
    """

    SHEET = (
        "Assess the completed flare.\n\nCriteria\n"
        "1. The flare is concentric with the tube bore.\n"
        "2. The sleeve is captive behind the flare.\n\n"
        "Critical defects\n- The flare is split at the rim.\n"
        "- The tube is kinked at the bend.\n\n"
        "Overall decision\nPASS requires every criterion to pass.\n")

    def _target(self):
        return {"target_id": "section:flare", "label": "flare_the_line — Flare the Tube",
                "criterion": self.SHEET}

    def _drafter(self, criteria=None, skipped=()):
        def fake(*, model, criterion, subject=None, **kw):
            return {"error": None, "cost_usd": 0.002,
                    "criteria": criteria if criteria is not None else {
                        1: {"statement": "The flare is visibly off-centre in the bore.",
                            "kind": "inversion", "changed": "reversed"},
                        2: {"statement": "The sleeve is loose ahead of the flare.",
                            "kind": "substitution", "changed": "behind → ahead"}},
                    "skipped": list(skipped)}
        return fake

    def test_the_negation_mirrors_the_sheet_and_splits_the_same_way(self):
        out = server.negative_sheet("AM.I.D.S1", self._target(), None, "m",
                                    drafter=self._drafter())
        self.assertIsNone(out["error"])
        points = server.sheet_checks(out["criterion"])
        # Two conditions and two defects in, four points out — the same count
        # the criterion itself grades on.
        self.assertEqual(len(points), 4)
        self.assertEqual([p["of"] for p in out["points"]], ["c1", "c2", "d1", "d2"])
        self.assertEqual(len(server.sheet_checks(self.SHEET)), len(points))
        # The roll-up rule is carried over, so the two sides combine identically.
        self.assertIn("PASS requires every criterion", out["criterion"])

    def test_a_defect_is_negated_by_asserting_its_presence(self):
        """The polarity that decides whether this measures anything at all.

        A defect is graded as an absence, which correct work passes. Asked to
        negate one, a model returns the original defect often enough that the
        control quietly stops controlling — so the inversion is arithmetic:
        state the defect as present, which correct work fails.
        """
        out = server.negative_sheet("AM.I.D.S1", self._target(), None, "m",
                                    drafter=self._drafter())
        points = server.sheet_checks(out["criterion"])
        defect_points = [p for p in points if "flare is split" in p["statement"]]
        self.assertEqual(len(defect_points), 1)
        self.assertIn("shows this defect", defect_points[0]["statement"])
        # And never the absence, which is what the original sheet grades.
        self.assertNotIn("shows no such defect", defect_points[0]["statement"])

    def test_the_text_never_announces_itself_as_a_control(self):
        """It reaches a grader verbatim. A sheet saying the work should fail it
        would be answered by reading the label rather than the photograph."""
        out = server.negative_sheet("AM.I.D.S1", self._target(), None, "m",
                                    drafter=self._drafter())
        lowered = out["criterion"].lower()
        for giveaway in ("control", "negative", "negated", "should fail", "incorrect"):
            self.assertNotIn(giveaway, lowered)

    def test_a_skipped_line_renumbers_but_keeps_its_pairing(self):
        """Ids are positional. A sheet whose first condition could not be
        negated renumbers c2 to c1, and a control read against the wrong
        positive is worse than one not paired at all."""
        out = server.negative_sheet(
            "AM.I.D.S1", self._target(), None, "m",
            drafter=self._drafter(
                criteria={2: {"statement": "The sleeve is loose ahead of the flare.",
                              "kind": "substitution", "changed": "behind → ahead"}},
                skipped=[{"n": 1, "why": "concentricity is not settleable here"}]))
        self.assertEqual(out["points"][0]["id"], "c1")
        self.assertEqual(out["points"][0]["of"], "c2")
        self.assertEqual(out["points"][0]["positive"],
                         "The sleeve is captive behind the flare.")
        self.assertEqual(len(out["skipped"]), 1)

    def test_every_negative_point_expects_fail(self):
        """The article is in frame and the wording contradicts it, so an
        abstention is a miss — `unsure` is the answer for what cannot be seen."""
        out = server.negative_sheet("AM.I.D.S1", self._target(), None, "m",
                                    drafter=self._drafter())
        target = {**self._target(), "negative": out,
                  "negative_criterion": out["criterion"]}
        base = {"video": "v", "frame": "f", "frame_url": "u", "step_id": None,
                "frames": ["f"], "best_view": None, "framing": None,
                "upload_path": None, "expected": None, "is_control": False,
                "is_variant": False, "variant_of": None,
                "polarity": "original", "negative_of": None}
        items = server.negative_items(target, base)
        self.assertEqual(len(items), 4)
        for item in items:
            self.assertEqual(item["expected"], "fail")
            self.assertEqual(item["polarity"], "negative")
            # Rolled up on its own, so the subtask's own verdict stays a
            # statement about the work rather than about the grader.
            self.assertEqual(item["rolls_up_to"], "section:flare#negative")
            self.assertTrue(item["positive_criterion"])

    def test_nothing_is_graded_when_the_run_asks_for_no_negatives(self):
        target = {**self._target(), "negative_criterion": "1. Anything."}
        self.assertEqual(server.negative_items(target, {}, include=False), [])

    def test_unsaved_text_from_the_browser_beats_what_is_on_disk(self):
        """The same rule the criteria follow: a control read on screen and not
        yet saved is still the one the next run grades."""
        target = {**self._target(), "negative_criterion": "Criteria\nOld line.\nSecond."}
        items = server.negative_items(target, {}, True,
                                      "Criteria\nNew line.\nAnother new line.")
        self.assertEqual([i["criterion"] for i in items],
                         ["New line.", "Another new line."])

    def test_the_report_pairs_pass_rates_and_states_the_gap(self):
        results = [
            {"model": "m", "polarity": "original", "verdict": "pass"},
            {"model": "m", "polarity": "original", "verdict": "pass"},
            {"model": "m", "polarity": "original", "verdict": "fail"},
            {"model": "m", "polarity": "original", "verdict": "unsure"},
            {"model": "m", "polarity": "negative", "verdict": "fail"},
            {"model": "m", "polarity": "negative", "verdict": "fail"},
            {"model": "m", "polarity": "negative", "verdict": "pass"},
            {"model": "m", "polarity": "negative", "error": "http_500"},
        ]
        rollups = [{"model": "m", "polarity": "original", "verdict": "pass"},
                   {"model": "m", "polarity": "negative", "verdict": "fail"}]
        report = server.polarity_report(results, rollups)
        self.assertEqual(report["original"]["pass_rate"], 0.5)
        # The errored call is excluded from the rate rather than counted a miss.
        self.assertEqual(report["negative"]["graded"], 3)
        self.assertAlmostEqual(report["negative"]["pass_rate"], 1 / 3, places=4)
        self.assertAlmostEqual(report["point_gap"], 0.5 - 1 / 3, places=4)
        self.assertEqual(report["models"]["m"]["original"]["pass"], 2)
        self.assertEqual(report["negative_subtasks"]["pass_rate"], 0.0)

    def test_a_sheet_with_no_structure_is_left_to_the_per_point_controls(self):
        """One unstructured condition has no sheet to mirror, and a negation
        that invented one would not be split the way the original is."""
        out = server.negative_sheet(
            "AM.I.D.S1", {"target_id": "t", "label": "L", "criterion": "Grade the whole thing."},
            None, "m", drafter=self._drafter())
        self.assertEqual(out["error"], "unsplittable")

    def test_a_batch_names_the_targets_that_produced_nothing(self):
        targets = [{"target_id": "section:a", "label": "A", "criterion": self.SHEET},
                   {"target_id": "section:empty", "label": "B", "criterion": ""}]
        out = server.negative_sheets_batch(
            "AM.I.D.S1", ["section:a", "section:empty", "section:ghost"], "m",
            targets=targets, drafter=self._drafter())
        errors = {r["target_id"]: r["error"] for r in out["results"] if r["error"]}
        self.assertEqual(errors, {"section:empty": "no_criterion",
                                  "section:ghost": "unknown_target"})
        self.assertEqual(out["sheets"], 1)
        self.assertEqual(out["points"], 4)
        self.assertAlmostEqual(out["cost_usd"], 0.002, places=6)

    def test_a_truncated_reply_is_retried_before_being_called_unparseable(self):
        """A reasoning model spends its budget thinking and the JSON arrives cut
        off. Reported as "no negatable line", that reads as a sheet not worth
        controlling — which is how a seven-point sheet yielded one control."""
        replies = ['{"criteria": [{"n": 1, "statement": "Cut off her',
                   '{"criteria": [{"n": 1, "statement": "The flare is off-centre.",'
                   ' "kind": "inversion", "changed": "reversed"}]}']
        budgets = []

        def post(payload, key):
            budgets.append(payload["max_tokens"])
            return {"choices": [{"message": {"content": replies[len(budgets) - 1]}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10}}

        out = vlm.draft_negative_sheet(model="m", criterion=self.SHEET, key="k", post=post)
        self.assertIsNone(out["error"])
        self.assertEqual(len(out["criteria"]), 1)
        # Retried with room rather than with the same budget again.
        self.assertEqual(len(budgets), 2)
        self.assertGreater(budgets[1], budgets[0])


class StepFrameSamplingTests(unittest.TestCase):
    """A step is a slice of work, so it is graded on frames of its own span."""

    WINDOW = [f"t{i:02d}.jpg" for i in range(10)]

    def test_k_frames_are_equidistant_with_both_ends_included(self):
        """The sampling rule, stated as the spec states it.

        k=1 is the frame the step ends on, because the state a step is graded
        on is the state it finishes in. Above one the frames span the whole
        window with both ends included — k=4 lands at 0, 33, 66 and 100 per
        cent, not at four evenly spaced interior points.
        """
        self.assertEqual(server.sample_frames(self.WINDOW, 1), ["t09.jpg"])
        self.assertEqual(server.sample_frames(self.WINDOW, 2), ["t00.jpg", "t09.jpg"])
        self.assertEqual(server.sample_frames(self.WINDOW, 3),
                         ["t00.jpg", "t04.jpg", "t09.jpg"])
        self.assertEqual(server.sample_frames(self.WINDOW, 4),
                         ["t00.jpg", "t03.jpg", "t06.jpg", "t09.jpg"])
        for k in range(2, 9):
            picked = server.sample_frames(self.WINDOW, k)
            self.assertEqual(picked[0], self.WINDOW[0])
            self.assertEqual(picked[-1], self.WINDOW[-1])

    def test_a_short_window_is_deduplicated_rather_than_padded(self):
        """Repeating a frame would bill for the same image several times."""
        self.assertEqual(server.sample_frames(["a", "b"], 4), ["a", "b"])
        self.assertEqual(server.sample_frames(["a"], 3), ["a"])
        self.assertEqual(server.sample_frames([], 3), [])
        self.assertEqual(server.sample_frames(["a", "b"], 0), [])

    def test_one_frame_reproduces_the_single_frame_behaviour_exactly(self):
        """`frames_per_step=1` must not move any frame that already existed.

        The frame each target carries is the key past runs are compared on, so
        a change that shifted it would silently invalidate every saved run
        rather than adding a new capability alongside them.
        """
        for acs in ("AM.I.D.S1", "AM.I.E.S1", "AM.II.A.S6"):
            pack, _, _ = server.load_pack(acs)
            one = {t["target_id"]: t for t in server.photo_targets(acs, pack, 1)}
            many = server.photo_targets(acs, pack, 3)
            self.assertTrue(one)
            for target in many:
                self.assertEqual(target["frame"], one[target["target_id"]]["frame"],
                                 f"{acs} {target['target_id']} moved its frame")
                # And the last sampled frame is that same frame, so a reader
                # taking `frames[-1]` and one taking `frame` cannot disagree.
                if target.get("frames"):
                    self.assertEqual(target["frames"][-1], target["frame"])

    def test_only_steps_and_reviewed_intervals_are_sampled(self):
        """A subtask is graded against the finished article, not moments of it.

        Its slice of the clip spans the whole subtask, so sampling across that
        would weigh three frames of work in progress against a criterion
        written about the completed result.
        """
        pack, _, _ = server.load_pack("AM.I.E.S1")
        targets = server.photo_targets("AM.I.E.S1", pack, 3)
        by_kind = {}
        for target in targets:
            by_kind.setdefault(target["kind"], []).append(target)
        self.assertTrue(by_kind["step"])
        self.assertTrue(by_kind["subtask"])
        self.assertTrue(by_kind["section"])
        for target in by_kind["step"] + by_kind["subtask"]:
            if target.get("frame_exists"):
                self.assertTrue(target.get("frames"), target["target_id"])
        for target in by_kind["section"]:
            self.assertFalse(target.get("frames"), target["target_id"])

    def test_a_step_samples_its_own_span_not_the_whole_clip(self):
        """Successive steps must not all be shown the same three frames."""
        pack, _, _ = server.load_pack("AM.I.D.S1")
        steps = [t for t in server.photo_targets("AM.I.D.S1", pack, 3)
                 if t["kind"] == "step" and t.get("frames")]
        self.assertGreater(len(steps), 3)
        for target in steps:
            self.assertLessEqual(len(target["frames"]), 3)
        # Every step's set differs from every other step's on the same clip.
        by_clip = {}
        for target in steps:
            by_clip.setdefault(target["video"], []).append(tuple(target["frames"]))
        for clip, sets in by_clip.items():
            self.assertEqual(len(sets), len(set(sets)), f"{clip} repeats a frame set")

    def test_a_requested_count_is_clamped_rather_than_trusted(self):
        """Every frame is billed on every call of every model."""
        self.assertEqual(server.clamp_frames_per_step("4"), 4)
        self.assertEqual(server.clamp_frames_per_step(1), 1)
        self.assertEqual(server.clamp_frames_per_step(9999), server.MAX_STEP_FRAMES)
        for junk in (None, "", "three", 0, -2, [3]):
            self.assertEqual(server.clamp_frames_per_step(junk), server.STEP_FRAMES)

    def test_best_view_is_added_to_the_span_never_substituted_for_it(self):
        """The sampled frames establish what the step's end state is.

        A picked frame that flattered the work would otherwise be the only
        evidence of it, which is the failure this whole path exists to avoid.
        """
        pack, _, _ = server.load_pack("AM.I.D.S1")
        target = next(t for t in server.photo_targets("AM.I.D.S1", pack, 3)
                      if t["kind"] == "step" and t.get("frames"))
        sampled = list(target["frames"])
        clip_frames = server.frame_names("AM.I.D.S1", target["video"], "detail")
        elsewhere = next(f for f in clip_frames if f not in sampled)

        result = server.add_best_view(
            "AM.I.D.S1", target, picker=lambda **kw: {"frame": elsewhere, "cost_usd": 0.001})
        self.assertEqual(result["frame"], elsewhere)
        self.assertEqual(target["frames"], [*sampled, elsewhere])
        self.assertEqual(target["best_view"], elsewhere)
        self.assertEqual(len(target["frame_urls"]), len(target["frames"]))

    def test_no_suitable_frame_leaves_the_sample_standing(self):
        """"No frame shows finished work" is an answer, not a failure.

        Adding the least bad frame anyway would present a mid-action image to
        the grader as the clearest view of the result.
        """
        pack, _, _ = server.load_pack("AM.I.D.S1")
        target = next(t for t in server.photo_targets("AM.I.D.S1", pack, 3)
                      if t["kind"] == "step" and t.get("frames"))
        sampled = list(target["frames"])
        server.add_best_view("AM.I.D.S1", target,
                             picker=lambda **kw: {"frame": None, "none_suitable": True})
        self.assertEqual(target["frames"], sampled)
        self.assertIsNone(target.get("best_view"))

    def test_an_uploaded_photo_is_never_joined_by_video_frames(self):
        """An upload already is the thing the sampling tries to reconstruct."""
        target = {"uploaded": True, "video": "clip", "frames": ["u.jpg"],
                  "upload_path": "/tmp/u.jpg"}
        self.assertIsNone(server.add_best_view("AM.I.D.S1", target))
        self.assertEqual(target["frames"], ["u.jpg"])


class SequenceGradingTests(unittest.TestCase):
    """Frames of one step read differently from photos of separate subjects."""

    def _frames(self, count: int) -> list[Path]:
        found = sorted((server.FRAME_SETS["detail"] / "AM.II.K.S3").rglob("t*.jpg"))
        if len(found) < count:
            self.skipTest("not enough extracted frames available")
        return found[:count]

    def _sent(self, **kwargs) -> str:
        captured = {}

        def fake_post(payload, key):
            captured["text"] = payload["messages"][1]["content"][0]["text"]
            captured["images"] = sum(
                1 for p in payload["messages"][1]["content"] if p.get("type") == "image_url")
            return {"choices": [{"message": {"content": '{"verdict":"pass","confidence":0.9}'}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

        result = vlm.grade(model="anthropic/claude-opus-5", criterion="The wire is taut.",
                           key="k", post=fake_post, **kwargs)
        self.assertIsNone(result["error"])
        return captured["text"], captured["images"]

    def test_a_sequence_is_graded_on_its_end_state(self):
        """The rule that keeps extra frames from simply raising the pass rate.

        "Any photo may satisfy the criterion" is right for a submission of
        several subjects and wrong for one piece of work photographed while it
        was being made: it would pass a wire seated at the halfway mark and
        pulled loose by the end.
        """
        text, images = self._sent(image_paths=self._frames(3), sequence=True)
        self.assertEqual(images, 3)
        self.assertIn("chronological order", text)
        self.assertIn("SAME", text)
        self.assertIn("END of the step", text)
        self.assertNotIn("A criterion is met if any photo shows it met", text)

    def test_a_multi_photo_submission_keeps_the_any_photo_rule(self):
        """Task-level evidence is several subjects, and unchanged by this."""
        text, images = self._sent(image_paths=self._frames(3), sequence=False)
        self.assertEqual(images, 3)
        self.assertIn("A criterion is met if any photo shows it met", text)
        self.assertNotIn("END of the step", text)

    def test_one_frame_carries_no_sequence_wording(self):
        text, images = self._sent(image_paths=self._frames(1), sequence=True)
        self.assertEqual(images, 1)
        self.assertNotIn("chronological order", text)

    def test_a_named_best_view_is_flagged_to_the_grader(self):
        """Its position in the sequence would otherwise mislead.

        It is the clearest view of the result, not a later moment than the
        frame before it.
        """
        plain, _ = self._sent(image_paths=self._frames(3), sequence=True)
        named, _ = self._sent(image_paths=self._frames(3), sequence=True,
                              best_view="t000004_00.jpg")
        self.assertNotIn("clearest view", plain)
        self.assertIn("clearest view", named)

    def test_the_estimate_counts_every_image(self):
        """Image tokens dominate, so assuming one would understate the bill.

        Cost is linear in images but not proportional to them: the prompt and
        the reply are paid for once however many frames are attached, which is
        why three frames cost near twice one rather than three times it.
        """
        one, two, three = (vlm.estimate_cost(["anthropic/claude-opus-5"], 10, k)
                           for k in (1, 2, 3))
        self.assertAlmostEqual(three["total_usd"] - one["total_usd"],
                               2 * (two["total_usd"] - one["total_usd"]), places=6)
        self.assertGreater(three["total_usd"], one["total_usd"] * 1.5)
        self.assertLess(three["total_usd"], one["total_usd"] * 3)
        # More frames buy more images, never more calls.
        self.assertEqual(one["calls"], three["calls"])
        # An omitted count must not silently become free.
        self.assertEqual(vlm.estimate_cost(["anthropic/claude-opus-5"], 10)["total_usd"],
                         one["total_usd"])


class GradingTests(unittest.TestCase):
    """Exercise the call path with the transport stubbed — no network."""

    def _frame(self) -> Path:
        frame = next((server.FRAME_SETS["detail"] / "AM.II.K.S3").rglob("t*.jpg"), None)
        if frame is None:
            self.skipTest("no extracted frames available")
        return frame

    def test_grade_parses_reply_and_accounts_cost(self):
        def fake_post(payload, key):
            self.assertEqual(payload["temperature"], 0)
            # The image must actually be attached, or we are grading text alone.
            parts = payload["messages"][1]["content"]
            self.assertTrue(any(p.get("type") == "image_url" for p in parts))
            self.assertTrue(parts[1]["image_url"]["url"].startswith("data:image/"))
            return {
                "choices": [{"message": {"content": '{"verdict":"pass","confidence":0.8}'}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 100},
            }

        result = vlm.grade(
            model="anthropic/claude-opus-5",
            image_path=self._frame(),
            criterion="The safety wire is taut.",
            key="test-key",
            post=fake_post,
        )
        self.assertIsNone(result["error"])
        self.assertEqual(result["verdict"], "pass")
        # 1000 in @ $5/M + 100 out @ $25/M = $0.0075
        self.assertAlmostEqual(result["cost_usd"], 0.0075, places=6)

    def test_missing_key_and_missing_frame_are_reported_not_raised(self):
        no_key = vlm.grade(model="anthropic/claude-opus-5", image_path=self._frame(),
                           criterion="x", key="", post=lambda p, k: {})
        self.assertEqual(no_key["error"], "no_api_key")
        self.assertIsNone(no_key["verdict"])

        missing = vlm.grade(model="anthropic/claude-opus-5", image_path=Path("/nope/none.jpg"),
                            criterion="x", key="k", post=lambda p, k: {})
        self.assertEqual(missing["error"], "missing_frame")

    def test_grade_many_preserves_order_and_echoes_cell_metadata(self):
        frame = str(self._frame())
        jobs = [
            {"model": "google/gemini-3.6-flash", "image_path": frame, "criterion": f"c{i}",
             "cell": {"target_id": f"t{i}", "is_control": i % 2 == 1}}
            for i in range(4)
        ]
        results = vlm.grade_many(
            jobs, key="k",
            post=lambda p, k: {"choices": [{"message": {"content": '{"verdict":"fail","confidence":0.5}'}}],
                               "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        )
        self.assertEqual([r["target_id"] for r in results], ["t0", "t1", "t2", "t3"])
        self.assertEqual([r["is_control"] for r in results], [False, True, False, True])
        self.assertTrue(all(r["verdict"] == "fail" for r in results))

    def test_api_key_is_never_echoed_into_a_result(self):
        secret = "sk-or-v1-should-never-appear"
        result = vlm.grade(
            model="anthropic/claude-opus-5", image_path=self._frame(), criterion="x",
            key=secret,
            post=lambda p, k: {"choices": [{"message": {"content": '{"verdict":"pass"}'}}]},
        )
        self.assertNotIn(secret, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
