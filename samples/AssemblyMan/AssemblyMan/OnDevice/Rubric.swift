/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// Rubric.swift
//
// The rubric sheets, parsed on the phone.
//
// Until now the phone knew nothing about what it could be graded against: the agent parsed
// `alcor_agents/criteria/**/*.txt` and published a catalogue, and the subtask picker drew
// whatever arrived. That is fine while an agent is in the room and useless the moment there
// isn't one, because grading offline needs the criteria themselves, not just their names.
//
// So the same sheets ride in the app bundle and are parsed here. This file is a port of
// agent/criteria_prompt.py — the header fields, the `=====` rule, the four section headings,
// the numbered/bulleted splitting and the subject sentence. Keeping the two in step matters:
// a criterion the phone numbers differently from the agent is a criterion whose verdict lands
// on the wrong line of the sheet.
//

import Foundation
import os

/// One subtask's acceptance criteria, as written in its sheet.
struct Rubric: Equatable, Identifiable {
  let taskCode: String
  let taskTitle: String
  let subtaskCode: String
  let subtask: String
  /// Pulled out of the body so a grade can be reported one condition at a time — the sheet
  /// shows which conditions passed and which failed, which is only possible if they are
  /// addressable individually rather than as a block of prose.
  let criteria: [String]
  let criticalDefects: [String]
  /// "rivet layout marked out on the patch and mating material", from the Assess sentence.
  let subject: String

  var id: String { key }
  var key: String { "\(taskCode)/\(subtaskCode)" }
}

/// Every rubric the app ships with, and the lookup the grader does against it.
struct RubricCatalogue {

  let rubrics: [Rubric]

  var isEmpty: Bool { rubrics.isEmpty }

  /// The bundled corpus, parsed once. A parse failure leaves the catalogue empty rather than
  /// crashing: offline grading then reports itself unavailable, which is a state the UI
  /// already has to handle, where a trap on launch is not.
  static let bundled: RubricCatalogue = {
    let log = Logger(subsystem: "com.alcorlabs.assemblyman", category: "rubrics")
    let urls = Bundle.main.urls(forResourcesWithExtension: "txt", subdirectory: nil) ?? []
    let parsed = urls.compactMap { url -> Rubric? in
      guard let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
      return Rubric(sheet: text)
    }
    // Stable order, so the picker does not reshuffle between launches. `Bundle.urls` makes no
    // ordering promise and has been observed to differ between a clean install and an update.
    let sorted = parsed.sorted { ($0.taskCode, $0.subtaskCode) < ($1.taskCode, $1.subtaskCode) }
    log.info("parsed \(sorted.count) bundled rubric sheet(s) of \(urls.count) candidate file(s)")
    return RubricCatalogue(rubrics: sorted)
  }()

  /// Look up one rubric, tolerantly — the same rule as `Corpus.find` on the agent, so a code
  /// that works against the agent works here.
  func find(taskCode: String, subtaskCode: String) -> Rubric? {
    let task = taskCode.trimmingCharacters(in: .whitespaces).uppercased()
    let subtask = subtaskCode
      .trimmingCharacters(in: .whitespaces)
      .lowercased()
      .replacingOccurrences(of: " ", with: "_")
    guard !subtask.isEmpty else { return nil }

    if let exact = rubrics.first(where: {
      $0.taskCode.uppercased() == task && $0.subtaskCode.lowercased() == subtask
    }) {
      return exact
    }
    // A subtask code that is unique across the corpus identifies its rubric on its own. Two
    // tasks sharing one would not, and guessing between them would grade a student's work
    // against a rubric nobody chose.
    let matches = rubrics.filter { $0.subtaskCode.lowercased() == subtask }
    return matches.count == 1 ? matches[0] : nil
  }

  /// The same shape the agent publishes, so the subtask picker cannot tell the two apart.
  var catalogue: [GradeProtocol.Catalogue.Task] {
    var order: [String] = []
    var grouped: [String: (title: String?, subtasks: [GradeProtocol.Catalogue.Subtask])] = [:]

    for rubric in rubrics {
      if grouped[rubric.taskCode] == nil {
        order.append(rubric.taskCode)
        grouped[rubric.taskCode] = (rubric.taskTitle.isEmpty ? nil : rubric.taskTitle, [])
      }
      grouped[rubric.taskCode]?.subtasks.append(
        GradeProtocol.Catalogue.Subtask(
          subtaskCode: rubric.subtaskCode,
          subtask: rubric.subtask.isEmpty ? nil : rubric.subtask,
          subject: rubric.subject.isEmpty ? nil : rubric.subject,
          criteriaCount: rubric.criteria.count
        )
      )
    }

    return order.compactMap { code in
      guard let entry = grouped[code] else { return nil }
      return GradeProtocol.Catalogue.Task(
        taskCode: code, taskTitle: entry.title, subtasks: entry.subtasks
      )
    }
  }
}

// MARK: - Parsing

extension Rubric {

  /// The sheet's own section headings, each on a line of its own. A section runs to the next
  /// one of these rather than to the next unindented line, because every criterion is itself
  /// unindented.
  private static let headings = ["Criteria", "Critical defects", "Overall decision", "Source basis"]

  /// Parses one sheet. Nil when it carries no task and subtask code, which is what a file that
  /// is not a rubric looks like — the app bundle is flat, so this is also the filter that keeps
  /// unrelated text resources out of the catalogue.
  init?(sheet raw: String) {
    let taskCode = Self.headerField(raw, "TASK CODE")
    let subtaskCode = Self.headerField(raw, "SUBTASK CODE")
    guard !taskCode.isEmpty, !subtaskCode.isEmpty else { return nil }

    // Everything past the `=====` rule. Falling back to the whole file would read the header
    // block as body, and "TASK TITLE: …" is not a criterion. Matched as "a line of nothing but
    // equals signs" rather than as a fixed 78-character string, so a regenerated corpus that
    // rules to a different width still parses.
    let lines = raw.split(separator: "\n", omittingEmptySubsequences: false)
    let ruleIndex = lines.firstIndex { line in
      let trimmed = line.trimmingCharacters(in: .whitespaces)
      return trimmed.count >= 8 && trimmed.allSatisfy { $0 == "=" }
    }
    let body = ruleIndex
      .map { lines[lines.index(after: $0)...].joined(separator: "\n") }
      ?? raw

    let criteria = Self.numbered(in: Self.section(body, "Criteria"))
    let defects = Self.bulleted(in: Self.section(body, "Critical defects"))
    // A sheet with no numbered criteria cannot be graded condition by condition, and grading
    // it as prose is the failure mode the whole per-criterion design exists to avoid.
    guard !criteria.isEmpty else { return nil }

    self.taskCode = taskCode
    self.taskTitle = Self.headerField(raw, "TASK TITLE")
    self.subtaskCode = subtaskCode
    self.subtask = Self.headerField(raw, "SUBTASK")
    self.criteria = criteria
    self.criticalDefects = defects
    self.subject = Self.subject(in: body)
  }

  /// "TASK CODE:    AM.II.A.S6" — the first line that opens with the label.
  private static func headerField(_ text: String, _ label: String) -> String {
    for line in text.split(separator: "\n", omittingEmptySubsequences: false) {
      guard line.hasPrefix("\(label):") else { continue }
      return line.dropFirst(label.count + 1).trimmingCharacters(in: .whitespaces)
    }
    return ""
  }

  /// The lines under `heading`, up to the next known heading or the end of the body.
  private static func section(_ body: String, _ heading: String) -> String {
    let lines = body.split(separator: "\n", omittingEmptySubsequences: false)
    var collecting = false
    var collected: [Substring] = []

    for line in lines {
      let trimmed = line.trimmingCharacters(in: .whitespaces)
      if trimmed == heading {
        collecting = true
        continue
      }
      if collecting, headings.contains(trimmed) {
        break
      }
      if collecting {
        collected.append(line)
      }
    }
    return collected.joined(separator: "\n")
  }

  /// "1. condition" lines, in order. The numbering in the file is what the verdict indexes
  /// refer to, so order here is the contract, not a presentation choice.
  private static func numbered(in section: String) -> [String] {
    section.split(separator: "\n").compactMap { line in
      let trimmed = line.trimmingCharacters(in: .whitespaces)
      guard let dot = trimmed.firstIndex(of: "."),
            !trimmed[trimmed.startIndex..<dot].isEmpty,
            trimmed[trimmed.startIndex..<dot].allSatisfy(\.isNumber)
      else { return nil }
      let text = trimmed[trimmed.index(after: dot)...].trimmingCharacters(in: .whitespaces)
      return text.isEmpty ? nil : text
    }
  }

  private static func bulleted(in section: String) -> [String] {
    section.split(separator: "\n").compactMap { line in
      let trimmed = line.trimmingCharacters(in: .whitespaces)
      guard trimmed.hasPrefix("- ") || trimmed.hasPrefix("• ") else { return nil }
      let text = trimmed.dropFirst(2).trimmingCharacters(in: .whitespaces)
      return text.isEmpty ? nil : text
    }
  }

  /// "Assess the completed <subject> visible in the image."
  private static func subject(in body: String) -> String {
    let collapsed = body.replacingOccurrences(
      of: "\\s+", with: " ", options: .regularExpression
    )
    guard let start = collapsed.range(of: "Assess the completed ", options: .caseInsensitive),
          let end = collapsed.range(
            of: " visible in the image", options: .caseInsensitive,
            range: start.upperBound..<collapsed.endIndex
          )
    else { return "" }
    return String(collapsed[start.upperBound..<end.lowerBound])
      .trimmingCharacters(in: .whitespaces)
  }
}
