/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// GradeProtocol.swift
//
// The wire contract between the phone and the grading agent. Every name and shape here has a
// counterpart in agent/photo_capture.py and agent/assembly_agent.py; changing one without the
// other breaks a path that fails silently, because an unrecognised topic is simply ignored at
// both ends. Kept in one file so the two halves can be read side by side.
//
// Why three channels rather than one:
//
//   assemblyman.capture        RPC. The agent asks for a photograph and gets an acknowledgement.
//                              Small, and needs an answer, which is what RPC is for.
//   assemblyman.capture        Byte stream. The photograph itself, hundreds of kilobytes, well
//                              past the ~15 kB a data packet carries. Matched to its request by
//                              `request_id` in the stream attributes.
//   assemblyman.grade-request  Byte stream. A photograph the operator sent unasked, from the
//                              capture button, carrying the codes they picked.
//   assemblyman.grade          Data packets. Verdicts and the rubric catalogue, broadcast to
//                              everyone in the room — the portal draws them too.
//

import Foundation

enum GradeProtocol {

  /// RPC method the agent invokes on this phone to ask for a still.
  static let captureMethod = "assemblyman.capture"

  /// Byte-stream topic the captured JPEG goes back on.
  static let captureTopic = "assemblyman.capture"

  /// Byte-stream topic for a photograph the operator chose to have graded.
  static let gradeRequestTopic = "assemblyman.grade-request"

  /// Byte-stream topic for a photograph sent to be named rather than graded.
  static let identifyRequestTopic = "assemblyman.identify-request"

  /// Data topic carrying verdicts and the catalogue.
  static let gradeTopic = "assemblyman.grade"

  // MARK: - Inbound

  /// What the agent sends when it wants a photograph.
  struct CaptureRequest: Decodable {
    let requestID: String

    enum CodingKeys: String, CodingKey {
      case requestID = "request_id"
    }
  }

  /// A message on the grade topic. `type` decides which of the rest is populated, so it is
  /// decoded first and on its own — a payload whose type this build does not know must be
  /// skipped rather than failing the whole decode and taking the known kinds with it.
  struct Envelope: Decodable {
    let type: String
  }

  /// One rubric line and how it was judged.
  struct Criterion: Decodable, Identifiable, Hashable {
    let index: Int
    let text: String
    /// Absent on the in-progress message, which lists the criteria before any are judged.
    let verdict: String?
    let observable: Bool?
    let note: String?

    var id: Int { index }

    enum Outcome {
      case pending
      /// Satisfied, and visibly so.
      case pass
      /// Visibly not satisfied.
      case fail
      /// Not satisfied because the photograph does not show it. A different thing to tell a
      /// student than work that is wrong, and the only one they fix with a better photo.
      case notShown
    }

    var outcome: Outcome {
      guard let verdict else { return .pending }
      if verdict.uppercased() == "PASS" { return .pass }
      return (observable == false) ? .notShown : .fail
    }
  }

  /// A verdict, or a grade in progress. One type for both: the in-progress message carries the
  /// criteria with no verdicts, so the sheet can open with the list already drawn and fill in.
  struct Grade: Decodable {
    let type: String
    let taskCode: String?
    let taskTitle: String?
    let subtaskCode: String?
    let subtask: String?
    let subject: String?
    let overall: String?
    let passed: Int?
    let total: Int?
    let criteria: [Criterion]?
    let criticalDefects: [String]?
    let observed: String?
    let model: String?
    let latency: Double?
    let error: String?
    let message: String?

    var isRunning: Bool { type == "grading" }
    var didFail: Bool { error != nil }

    enum CodingKeys: String, CodingKey {
      case type, subtask, subject, overall, passed, total, criteria, observed, model, error, message
      case taskCode = "task_code"
      case taskTitle = "task_title"
      case subtaskCode = "subtask_code"
      case criticalDefects = "critical_defects"
      case latency = "latency_s"
    }
  }

  /// What the phone can ask to be graded against, published by the agent on join.
  struct Catalogue: Decodable {
    let tasks: [Task]

    struct Task: Decodable, Identifiable, Hashable {
      let taskCode: String
      let taskTitle: String?
      let subtasks: [Subtask]

      var id: String { taskCode }

      enum CodingKeys: String, CodingKey {
        case taskCode = "task_code"
        case taskTitle = "task_title"
        case subtasks
      }
    }

    struct Subtask: Decodable, Identifiable, Hashable {
      let subtaskCode: String
      let subtask: String?
      let subject: String?
      let criteriaCount: Int?

      var id: String { subtaskCode }
      var label: String { subtask ?? subtaskCode }

      enum CodingKeys: String, CodingKey {
        case subtask, subject
        case subtaskCode = "subtask_code"
        case criteriaCount = "criteria_count"
      }
    }
  }

  /// The agent's guess at which subtask a photograph shows, sent back before the operator
  /// picks so the picker can open on it.
  ///
  /// A suggestion and nothing more — it never starts a grade on its own. Acting on a wrong
  /// guess silently would mark a student's work against the wrong rubric, which is worse than
  /// any amount of scrolling. `matched` is false whenever the agent declined to guess: an
  /// empty bench, a photograph of none of the listed work, a model that invented a code, or a
  /// call that timed out. The picker opens either way.
  struct Identification: Decodable {
    let matched: Bool
    let taskCode: String?
    let taskTitle: String?
    let subtaskCode: String?
    let subtask: String?
    /// "high", "medium" or "low". Shown rather than acted on — the operator decides what a
    /// low-confidence guess is worth.
    let confidence: String?
    /// What the photograph shows, in the model's words.
    let observed: String?
    /// Why there is no suggestion: "no_match", "not_in_catalogue", "timeout", "failed",
    /// "no_catalogue".
    let reason: String?

    enum CodingKeys: String, CodingKey {
      case matched, subtask, confidence, observed, reason
      case taskCode = "task_code"
      case taskTitle = "task_title"
      case subtaskCode = "subtask_code"
    }
  }

  // MARK: - Decoding

  /// Reads a message off the grade topic, or nil if it is not one this build understands.
  static func decode(_ data: Data) -> Message? {
    guard let envelope = try? JSONDecoder().decode(Envelope.self, from: data) else { return nil }
    switch envelope.type {
    case "grade", "grading":
      guard let grade = try? JSONDecoder().decode(Grade.self, from: data) else { return nil }
      return .grade(grade)
    case "catalogue":
      guard let catalogue = try? JSONDecoder().decode(Catalogue.self, from: data) else { return nil }
      return .catalogue(catalogue)
    case "identification":
      guard let identification = try? JSONDecoder().decode(Identification.self, from: data)
      else { return nil }
      return .identification(identification)
    default:
      return nil
    }
  }

  enum Message {
    case grade(Grade)
    case catalogue(Catalogue)
    case identification(Identification)
  }
}
