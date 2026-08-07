/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// GradeAssemblerTests.swift
//
// The three rules that stand between a weak model and a student's result. They matter more on
// device than they do on the agent: the hosted grader is a frontier model, and this one is 2.6B.
// Each test below is a way the small model can be wrong that must not reach the sheet.
//

@testable import AssemblyMan
import XCTest

@MainActor
final class GradeAssemblerTests: XCTestCase {

  private let rubric = Rubric(
    taskCode: "AM.II.A.S6",
    taskTitle: "Prepare and Install a Patch",
    subtaskCode: "rivet_layout",
    subtask: "Perform Rivet Layout",
    criteria: [
      "Marker edge-distance line runs parallel to each patch edge",
      "Edge-distance lines are present along every edge",
      "Rivet dots sit on the marked lines",
    ],
    criticalDefects: [
      "A marked dot lies on or past the material edge, no margin",
      "Rivet dots scattered with no discernible row or line alignment",
    ],
    subject: "rivet layout marked out on the patch"
  )

  private func entry(
    _ index: Int, _ verdict: String, observable: Bool = true, note: String = "seen"
  ) -> GradeAssembler.RawCriterion {
    GradeAssembler.RawCriterion(
      index: index, verdict: verdict, observable: observable, note: note
    )
  }

  /// Every rubric criterion appears, even one the model never answered
  func testMissingCriteriaAreBackfilled() {
    // The model returned two of three. A rubric line that silently vanished from the sheet
    // reads as a criterion that did not apply, when it is one nobody checked.
    let raw = GradeAssembler.RawReport(
      observed: "A patch with layout marks.",
      criteria: [entry(1, "PASS"), entry(3, "PASS")],
      criticalDefectsSeen: []
    )

    let grade = GradeAssembler.assemble(raw, rubric: rubric, model: "m", latency: 1)

    XCTAssertTrue(grade.total == 3)
    XCTAssertTrue(grade.criteria?.count == 3)
    let second = grade.criteria?[1]
    XCTAssertTrue(second?.verdict == "FAIL")
    XCTAssertTrue(second?.observable == false)
    XCTAssertTrue(second?.note == "No verdict returned for this criterion.")
    // Unanswered is FAIL, so the work does not pass on the strength of a short reply.
    XCTAssertTrue(grade.overall == "FAIL")
    XCTAssertTrue(grade.passed == 2)
  }

  /// A verdict that is neither PASS nor FAIL fails rather than passing
  func testUnreadableVerdictsFail() {
    let raw = GradeAssembler.RawReport(
      observed: "",
      criteria: [entry(1, "probably ok"), entry(2, ""), entry(3, "pass")],
      criticalDefectsSeen: []
    )

    let grade = GradeAssembler.assemble(raw, rubric: rubric, model: "m", latency: 1)

    XCTAssertTrue(grade.criteria?[0].verdict == "FAIL")
    XCTAssertTrue(grade.criteria?[1].verdict == "FAIL")
    // Case and surrounding space are the model's business, not the student's.
    XCTAssertTrue(grade.criteria?[2].verdict == "PASS")
  }

  /// A defect the rubric does not list is dropped
  func testInventedDefectsAreRejected() {
    // Failing the work on a standard nobody wrote down is the worst thing this path can do.
    let raw = GradeAssembler.RawReport(
      observed: "",
      criteria: (1...3).map { entry($0, "PASS") },
      criticalDefectsSeen: ["The patch is the wrong alloy", "Corrosion under the doubler"]
    )

    let grade = GradeAssembler.assemble(raw, rubric: rubric, model: "m", latency: 1)

    XCTAssertTrue(grade.criticalDefects?.isEmpty == true)
    XCTAssertTrue(grade.overall == "PASS")
  }

  /// A paraphrased defect is matched back to the rubric's own wording
  func testParaphrasedDefectsAreMatched() {
    // Models asked to quote from a list truncate and paraphrase. Dropping a real defect over a
    // missing clause is as wrong as inventing one, so containment counts either way — and the
    // wording that reaches the sheet is the rubric's, not the model's.
    let raw = GradeAssembler.RawReport(
      observed: "",
      criteria: (1...3).map { entry($0, "PASS") },
      criticalDefectsSeen: ["a marked dot lies on or past the material edge"]
    )

    let grade = GradeAssembler.assemble(raw, rubric: rubric, model: "m", latency: 1)

    XCTAssertTrue(grade.criticalDefects == ["A marked dot lies on or past the material edge, no margin"])
    XCTAssertTrue(grade.overall == "FAIL")
  }

  /// The same defect reported twice is listed once
  func testDuplicateDefectsCollapse() {
    let raw = GradeAssembler.RawReport(
      observed: "",
      criteria: (1...3).map { entry($0, "PASS") },
      criticalDefectsSeen: [
        "A marked dot lies on or past the material edge, no margin",
        "a marked dot lies on or past the material edge",
      ]
    )

    let grade = GradeAssembler.assemble(raw, rubric: rubric, model: "m", latency: 1)

    XCTAssertTrue(grade.criticalDefects?.count == 1)
  }

  /// Overall is computed, never taken from the model
  func testOverallIsArithmetic() {
    // Every criterion passes and no defect is present. Nothing in the reply says "PASS" at the
    // top level and nothing needs to: the rubric states the rule and the rule is arithmetic.
    let allPass = GradeAssembler.RawReport(
      observed: "", criteria: (1...3).map { entry($0, "PASS") }, criticalDefectsSeen: []
    )
    #expect(GradeAssembler.assemble(allPass, rubric: rubric, model: "m", latency: 1).overall
      == "PASS")

    let oneFail = GradeAssembler.RawReport(
      observed: "",
      criteria: [entry(1, "PASS"), entry(2, "FAIL"), entry(3, "PASS")],
      criticalDefectsSeen: []
    )
    #expect(GradeAssembler.assemble(oneFail, rubric: rubric, model: "m", latency: 1).overall
      == "FAIL")

    // A defect fails the work even with every criterion passing.
    let defectOnly = GradeAssembler.RawReport(
      observed: "",
      criteria: (1...3).map { entry($0, "PASS") },
      criticalDefectsSeen: ["Rivet dots scattered with no discernible row or line alignment"]
    )
    let graded = GradeAssembler.assemble(defectOnly, rubric: rubric, model: "m", latency: 1)
    XCTAssertTrue(graded.overall == "FAIL")
    XCTAssertTrue(graded.passed == 3)
  }

  /// `observable` survives, because it is the difference the student can act on
  func testUnobservableIsCarriedThrough() {
    let raw = GradeAssembler.RawReport(
      observed: "",
      criteria: [
        entry(1, "FAIL", observable: false, note: "Edge is out of frame."),
        entry(2, "FAIL", observable: true, note: "No line along the top edge."),
        entry(3, "PASS"),
      ],
      criticalDefectsSeen: []
    )

    let grade = GradeAssembler.assemble(raw, rubric: rubric, model: "m", latency: 1)

    XCTAssertTrue(grade.criteria?[0].outcome == .notShown)
    XCTAssertTrue(grade.criteria?[1].outcome == .fail)
    XCTAssertTrue(grade.criteria?[2].outcome == .pass)
  }

  /// The in-progress grade lists the criteria with no verdicts
  func testInProgressOpensTheSheet() {
    let grade = GradeAssembler.inProgress(rubric: rubric, model: "LFM2-VL-3B")

    XCTAssertTrue(grade.isRunning)
    XCTAssertTrue(grade.criteria?.count == 3)
    XCTAssertTrue(grade.criteria?.allSatisfy { $0.outcome == .pending } == true)
    // Named up front, so the sheet says which grader is thinking, not just that something is.
    XCTAssertTrue(grade.model == "LFM2-VL-3B")
  }

  /// A failed grade carries the codes so the sheet can still say what it was about
  func testFailureKeepsItsSubject() {
    let grade = GradeAssembler.failure(
      rubric: rubric, taskCode: "x", subtaskCode: "y",
      error: "timeout", message: "Did not answer."
    )

    XCTAssertTrue(grade.didFail)
    XCTAssertTrue(grade.taskCode == "AM.II.A.S6")
    XCTAssertTrue(grade.subtaskCode == "rivet_layout")
    XCTAssertTrue(grade.message == "Did not answer.")
  }
}
