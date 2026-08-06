/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// SubtaskPickerView.swift
//
// Picks what a photograph should be graded against, for the capture-button path. The voice
// path does not need this — the operator says what they were doing and the assistant works
// out the codes — but a photo taken from the phone arrives with no such context, and grading
// it against the wrong rubric produces a confident, meaningless verdict.
//
// The list comes from the agent, which owns the rubrics; the phone never guesses at one. Until
// the catalogue arrives there is nothing to show, and the view says so rather than presenting
// an empty list that looks like a loading failure.
//

import SwiftUI

struct SubtaskPickerView: View {
  let tasks: [GradeProtocol.Catalogue.Task]
  /// The agent's guess at what the photograph shows, if it made one. Opens that task and marks
  /// the subtask; it never picks for the operator, who still has to press it.
  var suggestion: GradeProtocol.Identification?
  /// True while the guess is still being made, so the header can say so rather than implying
  /// the list is all there is.
  var isIdentifying: Bool = false
  let onPick: (String, String) -> Void
  let onCancel: () -> Void

  @State private var expanded: String?
  /// Set once from the suggestion, so re-opening a section the operator collapsed does not
  /// spring back open under them.
  @State private var didApplySuggestion = false

  var body: some View {
    content
      // The guess arrives after the sheet is already up, so this reacts to it rather than
      // reading it once at presentation time.
      .onAppear { applySuggestionIfNeeded() }
      .onChange(of: suggestion?.subtaskCode) { _, _ in applySuggestionIfNeeded() }
  }

  private var content: some View {
    VStack(spacing: 0) {
      header
      Divider().overlay(Theme.divider)

      if tasks.isEmpty {
        empty
      } else {
        ScrollView {
          VStack(spacing: 0) {
            ForEach(tasks) { task in
              taskSection(task)
              Divider().overlay(Theme.divider)
            }
          }
        }
      }
    }
    .background(Theme.bg)
  }

  private var header: some View {
    HStack(alignment: .firstTextBaseline) {
      VStack(alignment: .leading, spacing: 2) {
        Text("Grade against")
          .headingStyle(20)
        Text(subtitle)
          .font(Theme.body(12))
          .foregroundStyle(Theme.neutral600)
      }
      Spacer()
      Button("Cancel", action: onCancel)
        .font(Theme.body(14))
        .foregroundStyle(Theme.accent700)
    }
    .padding(.horizontal, Theme.screenPadding)
    .padding(.vertical, 16)
  }

  /// Says what the agent made of the photograph, because a subtask sitting pre-marked with no
  /// explanation reads as the app having decided rather than guessed.
  private var subtitle: String {
    if isIdentifying { return "Working out what this photo shows…" }
    guard let suggestion, suggestion.matched, let subtask = suggestion.subtask else {
      return "Pick the subtask this photo shows"
    }
    let confidence = suggestion.confidence ?? "low"
    return confidence == "high"
      ? "This looks like \(subtask) — confirm or pick another"
      : "This might be \(subtask) — check before grading"
  }

  /// Whether the agent named this subtask.
  private func isSuggested(_ subtask: GradeProtocol.Catalogue.Subtask) -> Bool {
    guard let suggestion, suggestion.matched else { return false }
    return suggestion.subtaskCode == subtask.subtaskCode
  }

  /// Opens the suggested task once the guess arrives. The operator lands on the right section
  /// instead of scrolling to it, and can still go anywhere else in the list.
  private func applySuggestionIfNeeded() {
    guard !didApplySuggestion, let suggestion, suggestion.matched,
      let taskCode = suggestion.taskCode
    else { return }
    didApplySuggestion = true
    expanded = taskCode
  }

  private var empty: some View {
    VStack(spacing: 8) {
      Text("No rubrics yet")
        .headingStyle(16, color: Theme.neutral700)
      Text("The assistant publishes what it can grade when it joins the room. "
        + "If this stays empty, it is not in the session.")
        .font(Theme.body(13))
        .foregroundStyle(Theme.neutral600)
        .multilineTextAlignment(.center)
    }
    .padding(Theme.screenPadding)
    .frame(maxWidth: .infinity, maxHeight: .infinity)
  }

  /// One task, its subtasks revealed on tap. Eleven tasks of three to nine subtasks each is
  /// too long a flat list to scan while holding a phone in a workshop.
  private func taskSection(_ task: GradeProtocol.Catalogue.Task) -> some View {
    VStack(spacing: 0) {
      Button {
        expanded = (expanded == task.taskCode) ? nil : task.taskCode
      } label: {
        HStack(spacing: 10) {
          VStack(alignment: .leading, spacing: 2) {
            Text(task.taskCode)
              .overlineStyle(size: 10, color: Theme.accent700)
            Text(task.taskTitle ?? task.taskCode)
              .font(Theme.body(14))
              .foregroundStyle(Theme.text)
              .multilineTextAlignment(.leading)
          }
          Spacer(minLength: 8)
          Text("\(task.subtasks.count)")
            .font(Theme.body(12))
            .foregroundStyle(Theme.neutral500)
          Image(systemName: expanded == task.taskCode ? "chevron.up" : "chevron.down")
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(Theme.neutral500)
        }
        .padding(.horizontal, Theme.screenPadding)
        .padding(.vertical, 13)
        .contentShape(Rectangle())
      }
      .buttonStyle(PressableStyle(pressedOverlay: Theme.accent100))

      if expanded == task.taskCode {
        ForEach(task.subtasks) { subtask in
          Button {
            onPick(task.taskCode, subtask.subtaskCode)
          } label: {
            HStack(alignment: .top, spacing: 10) {
              Rectangle()
                .fill(isSuggested(subtask) ? Theme.accent : Theme.accent400)
                .frame(width: 3)
              VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                  Text(subtask.label)
                    .font(Theme.body(14, weight: isSuggested(subtask) ? .semibold : .regular))
                    .foregroundStyle(Theme.text)
                    .multilineTextAlignment(.leading)
                  if isSuggested(subtask) {
                    // Marked, not selected. The operator still presses it, because a guess
                    // that grades on its own would mark a student against the wrong rubric.
                    Tag(text: "Suggested")
                      .accessibilityLabel("Suggested by the assistant")
                  }
                }
                if let subject = subtask.subject, !subject.isEmpty {
                  Text(subject)
                    .font(Theme.body(12))
                    .foregroundStyle(Theme.neutral600)
                    .multilineTextAlignment(.leading)
                }
              }
              Spacer(minLength: 8)
              if let count = subtask.criteriaCount {
                Text("\(count)")
                  .font(Theme.body(11))
                  .foregroundStyle(Theme.neutral500)
                  .accessibilityLabel("\(count) criteria")
              }
            }
            .padding(.trailing, Theme.screenPadding)
            .padding(.leading, Theme.screenPadding)
            .padding(.vertical, 10)
            .contentShape(Rectangle())
          }
          .buttonStyle(PressableStyle(pressedOverlay: Theme.accent200))
          .background(Theme.surface.opacity(0.6))
        }
      }
    }
  }
}
