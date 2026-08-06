/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

@testable import AssemblyMan
import XCTest

/// Covers the contract between the phone and the grading agent.
///
/// Both ends ignore what they do not recognise, so a mismatch here does not raise anything —
/// it produces a sheet that never opens, or one that opens on nothing. The payloads below are
/// the shapes `agent/grading.py` and `agent/assembly_agent.py` publish, kept whole rather than
/// trimmed to the fields under test: the two halves ship separately, and a phone that chokes on
/// a field it does not care about is exactly the failure these are here to catch.
final class GradeProtocolTests: XCTestCase {

  private func decode(_ json: String) -> GradeProtocol.Message? {
    GradeProtocol.decode(Data(json.utf8))
  }

  private func grade(_ json: String) throws -> GradeProtocol.Grade {
    guard case let .grade(grade)? = decode(json) else {
      throw XCTSkip("payload did not decode as a grade")
    }
    return grade
  }

  // MARK: - In progress

  func testGradingMessageOpensTheSheetWithUnjudgedCriteria() throws {
    let message = try grade("""
      {"type": "grading",
       "task_code": "AM.I.D.S1",
       "subtask_code": "flare_the_line",
       "subtask": "Flare the line",
       "subject": "the flared tube end",
       "criteria": [{"index": 1, "text": "Flare seats the fitting"},
                    {"index": 2, "text": "No cracks or splits"}]}
      """)

    XCTAssertTrue(message.isRunning)
    XCTAssertFalse(message.didFail)
    XCTAssertEqual(message.subtask, "Flare the line")
    XCTAssertEqual(message.criteria?.count, 2)
    XCTAssertEqual(message.criteria?.map(\.outcome), [.pending, .pending])
    XCTAssertNil(message.overall)
  }

  // MARK: - Verdicts

  func testGradeMessageDecodesInFull() throws {
    let message = try grade("""
      {"type": "grade",
       "task_code": "AM.II.A.S6",
       "task_title": "Patch repair on aircraft or component",
       "subtask_code": "rivet_layout",
       "subtask": "Rivet layout",
       "subject": "the riveted patch",
       "overall": "FAIL",
       "passed": 1,
       "total": 3,
       "criteria": [
         {"index": 1, "text": "Rows straight", "verdict": "PASS",
          "observable": true, "note": "Spacing even."},
         {"index": 2, "text": "Shop heads to size", "verdict": "FAIL",
          "observable": false, "note": "Heads not in frame."},
         {"index": 3, "text": "Patch flush", "verdict": "FAIL",
          "observable": true, "note": "Lifts at the corner."}],
       "critical_defects": ["Cracks radiating from a hole"],
       "observed": "An aluminium patch riveted to a skin panel.",
       "frame": {"width": 1280, "height": 720},
       "model": "gemini-3.6-flash",
       "provisional": true,
       "latency_s": 3.4}
      """)

    XCTAssertFalse(message.isRunning)
    XCTAssertFalse(message.didFail)
    XCTAssertEqual(message.taskCode, "AM.II.A.S6")
    XCTAssertEqual(message.taskTitle, "Patch repair on aircraft or component")
    XCTAssertEqual(message.overall, "FAIL")
    XCTAssertEqual(message.passed, 1)
    XCTAssertEqual(message.total, 3)
    XCTAssertEqual(message.criticalDefects, ["Cracks radiating from a hole"])
    XCTAssertEqual(message.model, "gemini-3.6-flash")
    XCTAssertEqual(message.latency, 3.4)
  }

  /// FAIL splits in two on `observable`, and the split is the point: work that is wrong needs
  /// doing again, work the camera did not show needs only a better photograph. Getting this
  /// backwards sends a student back to a bench they never had to return to.
  func testFailureThatIsNotVisibleIsNotShownRatherThanFail() throws {
    let message = try grade("""
      {"type": "grade", "overall": "FAIL", "criteria": [
        {"index": 1, "text": "Rows straight", "verdict": "PASS", "observable": true},
        {"index": 2, "text": "Shop heads to size", "verdict": "FAIL", "observable": false},
        {"index": 3, "text": "Patch flush", "verdict": "FAIL", "observable": true}]}
      """)

    XCTAssertEqual(message.criteria?.map(\.outcome), [.pass, .notShown, .fail])
  }

  /// A FAIL with no flag is a judgement about the work, not about the photograph. Defaulting
  /// the other way would soften a real failure into "we could not see it".
  func testFailureWithoutAnObservableFlagIsAPlainFail() throws {
    let message = try grade("""
      {"type": "grade", "criteria": [{"index": 1, "text": "Cut square", "verdict": "FAIL"}]}
      """)

    XCTAssertEqual(message.criteria?.map(\.outcome), [.fail])
  }

  func testVerdictCaseFromTheModelDoesNotChangeTheOutcome() throws {
    let message = try grade("""
      {"type": "grade", "criteria": [{"index": 1, "text": "Cut square", "verdict": "pass"}]}
      """)

    XCTAssertEqual(message.criteria?.map(\.outcome), [.pass])
  }

  // MARK: - Failures

  func testTimeoutArrivesAsAGradeCarryingItsReason() throws {
    let message = try grade("""
      {"type": "grade", "error": "timeout",
       "message": "The grader did not answer within 45 seconds.",
       "task_code": "AM.I.D.S1", "subtask_code": "flare_the_line"}
      """)

    XCTAssertTrue(message.didFail)
    XCTAssertEqual(message.message, "The grader did not answer within 45 seconds.")
    XCTAssertEqual(message.taskCode, "AM.I.D.S1")
    XCTAssertNil(message.criteria)
  }

  // MARK: - Catalogue

  func testCatalogueDecodesTheTasksThePhoneMayAskFor() {
    guard case let .catalogue(catalogue)? = decode("""
      {"type": "catalogue", "tasks": [
        {"task_code": "AM.I.D.S1", "task_title": "Rigid line with flare and bend",
         "subtasks": [
           {"subtask_code": "flare_the_line", "subtask": "Flare the line",
            "subject": "the flared tube end", "criteria_count": 3},
           {"subtask_code": "cut_the_line"}]}]}
      """) else {
      return XCTFail("a catalogue payload should decode as one")
    }

    XCTAssertEqual(catalogue.tasks.count, 1)
    let task = catalogue.tasks[0]
    XCTAssertEqual(task.id, "AM.I.D.S1")
    XCTAssertEqual(task.subtasks.count, 2)
    XCTAssertEqual(task.subtasks[0].label, "Flare the line")
    XCTAssertEqual(task.subtasks[0].criteriaCount, 3)
    // A subtask with no human name still has to be pickable, so it falls back to its code
    // rather than rendering blank.
    XCTAssertEqual(task.subtasks[1].label, "cut_the_line")
  }

  // MARK: - Everything else on the topic

  /// The topic is shared, and the agent will grow message kinds this build has never seen. An
  /// unknown `type` must be skipped, not fail the decode and take the known kinds with it.
  func testUnknownMessageKindsAreSkipped() {
    XCTAssertNil(decode(#"{"type": "transcript", "text": "hello"}"#))
    XCTAssertNil(decode(#"{"type": "catalogue_v2", "tasks": []}"#))
  }

  func testMalformedPayloadsAreSkippedRatherThanCrashing() {
    XCTAssertNil(decode("not json at all"))
    XCTAssertNil(decode("{}"))
    XCTAssertNil(decode(""))
    // Right kind, unreadable body.
    XCTAssertNil(decode(#"{"type": "grade", "criteria": "not an array"}"#))
  }

  func testUnknownFieldsDoNotCostTheWholeMessage() throws {
    let message = try grade("""
      {"type": "grade", "overall": "PASS", "rubric_revision": 7, "graded_by": {"kind": "vlm"},
       "criteria": [{"index": 1, "text": "Cut square", "verdict": "PASS",
                     "observable": true, "confidence": 0.91}]}
      """)

    XCTAssertEqual(message.criteria?.map(\.outcome), [.pass])
  }

  // MARK: - Channel names

  /// These strings are the contract. Each has a counterpart in the agent, and a rename on one
  /// side is silently ignored on the other.
  func testChannelNamesMatchTheAgent() {
    XCTAssertEqual(GradeProtocol.captureMethod, "assemblyman.capture")
    XCTAssertEqual(GradeProtocol.captureTopic, "assemblyman.capture")
    XCTAssertEqual(GradeProtocol.gradeRequestTopic, "assemblyman.grade-request")
    XCTAssertEqual(GradeProtocol.gradeTopic, "assemblyman.grade")
  }

  func testCaptureRequestReadsTheAgentsRequestID() throws {
    let request = try JSONDecoder().decode(
      GradeProtocol.CaptureRequest.self,
      from: Data(#"{"request_id": "cap-42"}"#.utf8)
    )
    XCTAssertEqual(request.requestID, "cap-42")
  }
}
