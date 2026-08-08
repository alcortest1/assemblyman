/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// GradeAssembler.swift
//
// Turns a model's reply into the verdict the sheet draws — a port of `_assemble` in
// agent/grading.py, and deliberately a close one.
//
// It matters more here than it does on the agent. Gemini is a frontier model being asked a
// question it is good at; the on-device grader is a 3B model being asked the same question,
// and the three rules below are what stand between a weak reply and a student's result:
//
//   * Every rubric criterion appears in the output whether or not the model returned one.
//     A rubric line that silently vanished from the sheet reads as a criterion that did not
//     apply, when in fact it is one nobody checked.
//   * Reported critical defects are matched back against the rubric's own list. A model that
//     invents a defect would otherwise fail the work on a standard nobody wrote down.
//   * The overall verdict is computed here, never asked for. The rule is arithmetic — every
//     criterion passes and no critical defect is present — and models get arithmetic wrong
//     often enough to matter.
//
// Pure and free of the SDK on purpose, so the rules can be tested without a model.
//

import Foundation

enum GradeAssembler {

  /// One criterion as the model returned it.
  struct RawCriterion {
    let index: Int
    let verdict: String
    let observable: Bool
    let note: String
  }

  /// The model's whole reply, already decoded.
  struct RawReport {
    let observed: String
    let criteria: [RawCriterion]
    let criticalDefectsSeen: [String]
  }

  /// The in-progress message, so the sheet opens with the criteria already listed and fills in
  /// as the verdict lands. The agent publishes the same thing before its model call; on device
  /// there is no wire, so it is built directly.
  static func inProgress(rubric: Rubric, model: String) -> GradeProtocol.Grade {
    GradeProtocol.Grade(
      type: "grading",
      taskCode: rubric.taskCode,
      taskTitle: rubric.taskTitle,
      subtaskCode: rubric.subtaskCode,
      subtask: rubric.subtask,
      subject: rubric.subject,
      overall: nil,
      passed: nil,
      total: rubric.criteria.count,
      criteria: rubric.criteria.enumerated().map { offset, text in
        GradeProtocol.Criterion(
          index: offset + 1, text: text, verdict: nil, observable: nil, note: nil
        )
      },
      criticalDefects: nil,
      observed: nil,
      model: model,
      latency: nil,
      error: nil,
      message: nil
    )
  }

  /// A grade that could not be produced. Errors come back as data rather than as a thrown
  /// error, for the same reason they do on the agent: the operator has their hands full and a
  /// sheet that says why is worth more than a spinner that stops.
  static func failure(
    rubric: Rubric?, taskCode: String, subtaskCode: String, error: String, message: String
  ) -> GradeProtocol.Grade {
    GradeProtocol.Grade(
      type: "grade",
      taskCode: rubric?.taskCode ?? taskCode,
      taskTitle: rubric?.taskTitle,
      subtaskCode: rubric?.subtaskCode ?? subtaskCode,
      subtask: rubric?.subtask,
      subject: rubric?.subject,
      overall: nil, passed: nil, total: nil, criteria: nil, criticalDefects: nil,
      observed: nil, model: nil, latency: nil,
      error: error,
      message: message
    )
  }

  /// The verdict the sheet draws.
  static func assemble(
    _ raw: RawReport,
    rubric: Rubric,
    model: String,
    latency: Double
  ) -> GradeProtocol.Grade {
    var byIndex: [Int: RawCriterion] = [:]
    for item in raw.criteria where byIndex[item.index] == nil {
      byIndex[item.index] = item
    }

    let criteria: [GradeProtocol.Criterion] = rubric.criteria.enumerated().map { offset, text in
      let index = offset + 1
      guard let item = byIndex[index],
            case let verdict = item.verdict.trimmingCharacters(in: .whitespaces).uppercased(),
            verdict == "PASS" || verdict == "FAIL"
      else {
        return GradeProtocol.Criterion(
          index: index, text: text, verdict: "FAIL", observable: false,
          note: "No verdict returned for this criterion."
        )
      }
      return GradeProtocol.Criterion(
        index: index,
        text: text,
        verdict: verdict,
        // The distinction the sheet needs: work that is wrong and work that was not
        // photographed are both FAIL, and the student can only act on the second one by
        // taking a better picture.
        observable: item.observable,
        note: item.note.trimmingCharacters(in: .whitespacesAndNewlines)
      )
    }

    let defects = matchedDefects(raw.criticalDefectsSeen, against: rubric.criticalDefects)
    let failed = criteria.filter { $0.verdict == "FAIL" }
    let overall = (failed.isEmpty && defects.isEmpty) ? "PASS" : "FAIL"

    return GradeProtocol.Grade(
      type: "grade",
      taskCode: rubric.taskCode,
      taskTitle: rubric.taskTitle,
      subtaskCode: rubric.subtaskCode,
      subtask: rubric.subtask,
      subject: rubric.subject,
      overall: overall,
      passed: criteria.count - failed.count,
      total: criteria.count,
      criteria: criteria,
      criticalDefects: defects,
      observed: raw.observed.trimmingCharacters(in: .whitespacesAndNewlines),
      model: model,
      latency: latency,
      error: nil,
      message: nil
    )
  }

  /// Only defects the rubric actually lists, in the rubric's own wording.
  ///
  /// Exact match first, then containment either way — a model asked to quote from a list will
  /// often paraphrase or truncate, and dropping a real defect over a missing clause is as
  /// wrong as inventing one. What is never allowed is a defect with no counterpart at all.
  private static func matchedDefects(_ seen: [String], against listed: [String]) -> [String] {
    var matched: [String] = []
    for candidate in seen {
      let text = candidate.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
      guard !text.isEmpty else { continue }
      let hit = listed.first { $0.lowercased() == text }
        ?? listed.first { $0.lowercased().contains(text) || text.contains($0.lowercased()) }
      if let hit, !matched.contains(hit) {
        matched.append(hit)
      }
    }
    return matched
  }
}
