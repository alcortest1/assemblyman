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
  let onPick: (String, String) -> Void
  let onCancel: () -> Void

  @State private var expanded: String?

  var body: some View {
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
        Text("Pick the subtask this photo shows")
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
              Rectangle().fill(Theme.accent400).frame(width: 3)
              VStack(alignment: .leading, spacing: 2) {
                Text(subtask.label)
                  .font(Theme.body(14))
                  .foregroundStyle(Theme.text)
                  .multilineTextAlignment(.leading)
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
