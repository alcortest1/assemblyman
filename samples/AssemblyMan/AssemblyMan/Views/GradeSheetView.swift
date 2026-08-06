/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// GradeSheetView.swift
//
// The verdict, on the phone. The portal draws the same payload for whoever is watching
// remotely; this is for the person who did the work and is standing at the bench.
//
// The one distinction the layout exists to make: a criterion can fail because the work is
// wrong, or because the photograph does not show it. They are different things to tell a
// student — the second is fixed by taking another picture, and reading it as "you did this
// badly" is both wrong and discouraging. So they never share a mark or a colour, and the
// footer says plainly when everything that failed did so for want of a view.
//
// Outcome is carried by a glyph and a bar, never by colour alone: this is read on a phone
// held at arm's length in a workshop, sometimes by someone colourblind.
//

import SwiftUI

struct GradeSheetView: View {
  let grade: GradeProtocol.Grade
  let onDismiss: () -> Void

  var body: some View {
    VStack(spacing: 0) {
      header
      Divider().overlay(Theme.divider)

      if let observed = grade.observed, !observed.isEmpty {
        Text(observed)
          .font(Theme.body(13))
          .foregroundStyle(Theme.neutral700)
          .frame(maxWidth: .infinity, alignment: .leading)
          .padding(.horizontal, Theme.screenPadding)
          .padding(.vertical, 12)
        Divider().overlay(Theme.divider)
      }

      ScrollView {
        VStack(alignment: .leading, spacing: 0) {
          ForEach(grade.criteria ?? []) { criterion in
            CriterionRow(criterion: criterion)
            Divider().overlay(Theme.divider.opacity(0.5))
          }
          if let defects = grade.criticalDefects, !defects.isEmpty {
            criticalDefects(defects)
          }
        }
      }

      footer
    }
    .background(Theme.bg)
  }

  // MARK: - Header

  private var header: some View {
    HStack(alignment: .top, spacing: 12) {
      verdictBadge

      VStack(alignment: .leading, spacing: 2) {
        Text(grade.subtask ?? grade.subtaskCode ?? "—")
          .headingStyle(19)
          .lineLimit(2)
        if let code = grade.taskCode {
          Text([code, grade.taskTitle].compactMap { $0 }.joined(separator: " · "))
            .font(Theme.body(11))
            .foregroundStyle(Theme.neutral600)
            .lineLimit(2)
        }
      }

      Spacer(minLength: 8)

      VStack(alignment: .trailing, spacing: 6) {
        if let passed = grade.passed, let total = grade.total, !grade.isRunning {
          Text("\(passed)/\(total)")
            .font(Theme.heading(20))
            .foregroundStyle(Theme.text)
            .monospacedDigit()
        }
        Button(action: onDismiss) {
          Image(systemName: "xmark")
            .font(.system(size: 12, weight: .semibold))
            .foregroundStyle(Theme.neutral700)
            .frame(width: 28, height: 28)
            .overlay(Rectangle().stroke(Theme.neutral400, lineWidth: Theme.hairline))
        }
        .accessibilityLabel("Close grade")
      }
    }
    .padding(.horizontal, Theme.screenPadding)
    .padding(.vertical, 16)
  }

  private var verdictBadge: some View {
    let (label, color) = badge
    return Text(label)
      .font(Theme.heading(13))
      .tracking(1.4)
      .foregroundStyle(color)
      .padding(.horizontal, 9)
      .padding(.vertical, 5)
      .overlay(Rectangle().stroke(color, lineWidth: Theme.hairline))
  }

  private var badge: (String, Color) {
    if grade.isRunning { return ("GRADING", Theme.neutral600) }
    if grade.didFail { return ("UNAVAILABLE", Theme.neutral600) }
    switch (grade.overall ?? "").uppercased() {
    case "PASS": return ("PASS", GradeColor.pass)
    case "FAIL": return ("FAIL", GradeColor.fail)
    default: return (grade.overall ?? "—", Theme.neutral600)
    }
  }

  // MARK: - Sections

  private func criticalDefects(_ defects: [String]) -> some View {
    VStack(alignment: .leading, spacing: 6) {
      Text("Critical defects seen")
        .overlineStyle(size: 10, color: GradeColor.fail)
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(GradeColor.fail.opacity(0.12))

      ForEach(defects, id: \.self) { defect in
        HStack(alignment: .top, spacing: 8) {
          Text("•").foregroundStyle(GradeColor.fail)
          Text(defect).font(Theme.body(13)).foregroundStyle(Theme.text)
        }
        .padding(.horizontal, 10)
      }
      .padding(.bottom, 2)
    }
    .padding(.vertical, 8)
    .overlay(Rectangle().stroke(GradeColor.fail.opacity(0.55), lineWidth: Theme.hairline))
    .padding(Theme.screenPadding)
  }

  private var footer: some View {
    VStack(spacing: 6) {
      Divider().overlay(Theme.divider)
      HStack(alignment: .top, spacing: 10) {
        Text(footerNote)
          .font(Theme.body(11))
          .foregroundStyle(Theme.neutral600)
          .frame(maxWidth: .infinity, alignment: .leading)
        if !meta.isEmpty {
          Text(meta)
            .font(Theme.body(11))
            .foregroundStyle(Theme.neutral500)
            .monospacedDigit()
        }
      }
      .padding(.horizontal, Theme.screenPadding)
      .padding(.bottom, 10)
    }
  }

  /// The failed criteria, or an empty list while the grade is still running.
  private var failed: [GradeProtocol.Criterion] {
    (grade.criteria ?? []).filter { $0.outcome == .fail || $0.outcome == .notShown }
  }

  private var footerNote: String {
    if grade.isRunning { return "Reading the photograph…" }
    if grade.didFail { return grade.message ?? "The grader could not run." }
    if !failed.isEmpty, failed.allSatisfy({ $0.outcome == .notShown }) {
      return "Everything that failed did so because the photo does not show it. "
        + "Frame the finished work and grade again."
    }
    return "Machine-drafted rubric — a first opinion for an instructor to confirm, not a final mark."
  }

  private var meta: String {
    guard !grade.isRunning, !grade.didFail else { return "" }
    return [grade.model, grade.latency.map { String(format: "%.1fs", $0) }]
      .compactMap { $0 }
      .joined(separator: " · ")
  }
}

// MARK: - Criterion row

private struct CriterionRow: View {
  let criterion: GradeProtocol.Criterion

  var body: some View {
    HStack(alignment: .top, spacing: 0) {
      Rectangle()
        .fill(barColor)
        .frame(width: 3)

      HStack(alignment: .top, spacing: 10) {
        Text(mark)
          .font(.system(size: 14, weight: .semibold))
          .foregroundStyle(markColor)
          .frame(width: 16)
          .accessibilityHidden(true)

        VStack(alignment: .leading, spacing: 3) {
          Text(criterion.text)
            .font(Theme.body(14))
            .foregroundStyle(isPending ? Theme.neutral500 : Theme.text)
          if let note = criterion.note, !note.isEmpty, !isPending {
            Text(note)
              .font(Theme.body(12))
              .foregroundStyle(Theme.neutral600)
          }
        }
        Spacer(minLength: 0)
      }
      .padding(.horizontal, 12)
      .padding(.vertical, 10)
    }
    .accessibilityElement(children: .combine)
    .accessibilityLabel("\(spokenOutcome): \(criterion.text)")
  }

  private var isPending: Bool { criterion.outcome == .pending }

  private var mark: String {
    switch criterion.outcome {
    case .pending: return "·"
    case .pass: return "✓"
    case .fail: return "✕"
    case .notShown: return "?"
    }
  }

  private var markColor: Color {
    switch criterion.outcome {
    case .pending: return Theme.neutral400
    case .pass: return GradeColor.pass
    case .fail: return GradeColor.fail
    case .notShown: return GradeColor.notShown
    }
  }

  private var barColor: Color {
    criterion.outcome == .pending ? Theme.neutral300 : markColor
  }

  private var spokenOutcome: String {
    switch criterion.outcome {
    case .pending: return "Not yet graded"
    case .pass: return "Passed"
    case .fail: return "Failed"
    case .notShown: return "Failed, not shown in the photo"
    }
  }
}

enum GradeColor {
  static let pass = Color(hex: 0x2F_7D_4F)
  static let fail = Color(hex: 0xB0_3A_3A)
  /// Amber, deliberately not red: the work may well be right, the photograph just cannot say.
  static let notShown = Color(hex: 0x9A_75_0A)
}
