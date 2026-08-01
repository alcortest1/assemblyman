/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// NonStreamView.swift
//
// Ready screen. Reports the state of the link and starts a session. Also hosts the
// first-run checklist sheet and the firmware/app update prompts.
//

import MWDATCore
import SwiftUI

struct NonStreamView: View {
  var viewModel: StreamSessionViewModel
  @Bindable var wearablesVM: WearablesViewModel
  var settings: AppSettings
  var openSettings: () -> Void

  private var isUpdateRequired: Bool {
    wearablesVM.requiresFirmwareUpdate || viewModel.requiresDATAppUpdate
  }

  var body: some View {
    ZStack {
      Theme.bg.ignoresSafeArea()

      VStack(spacing: 14) {
        header
        statusPlate

        Spacer(minLength: 0)
        readyBlock
        Spacer(minLength: 0)

        if isUpdateRequired {
          UpdateRequiredCard(
            showFirmwareUpdate: wearablesVM.requiresFirmwareUpdate,
            showDATAppUpdate: viewModel.requiresDATAppUpdate,
            onUpdateFirmware: {
              Task { await wearablesVM.openFirmwareUpdate() }
            },
            onUpdateGlassesApp: {
              Task { await wearablesVM.openDATGlassesAppUpdate() }
            }
          )
        }

        PrimaryButton(
          title: "Start session",
          isDisabled: !viewModel.hasActiveDevice || isUpdateRequired
        ) {
          Task { await viewModel.handleStartStreaming() }
        }
        .accessibilityIdentifier("start_streaming_button")
      }
      .padding(.horizontal, Theme.screenPadding)
      .padding(.top, 26)
      .padding(.bottom, 24)
    }
    .sheet(isPresented: $wearablesVM.showGettingStartedSheet) {
      GettingStartedSheetView()
        .presentationDetents([.height(430)])
        .presentationDragIndicator(.hidden)
        .presentationBackground(Theme.bg)
    }
  }

  // MARK: - Sections

  private var header: some View {
    HStack(spacing: 8) {
      OperatorLockup(glyphSize: 22, textSize: 20)
      Tag(text: "Linked")
      Spacer()
      IconButton(glyph: .slidersHorizontal, accessibilityLabel: "Settings", action: openSettings)
        .accessibilityIdentifier("settings_button")
    }
    .padding(.bottom, 10)
    .overlay(alignment: .bottom) {
      Rectangle().fill(Theme.divider).frame(height: Theme.hairline)
    }
  }

  /// Spec table describing the link, in the manner of a drawing's title block.
  private var statusPlate: some View {
    VStack(spacing: 0) {
      SpecRow(label: "Device", isFirst: true) {
        Text("Ray-Ban Meta")
          .font(Theme.body(13, weight: .semibold))
          .foregroundStyle(Theme.text)
      }
      SpecRow(label: "Link") {
        // Identified rather than matched on text: the uppercase treatment is a display
        // transform, so the accessibility label is not reliably the rendered string.
        Group {
          if viewModel.hasActiveDevice {
            Tag.online()
          } else {
            Tag.waiting()
          }
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier(
          viewModel.hasActiveDevice ? "link_status_online" : "link_status_waiting"
        )
      }
      SpecRow(label: "Session") {
        Text(settings.streamSpec)
          .font(Theme.body(13))
          .foregroundStyle(Theme.neutral700)
          .accessibilityIdentifier("session_spec")
      }
      SpecRow(label: "Agent") {
        Text(settings.agent.name)
          .font(Theme.body(13))
          .foregroundStyle(Theme.neutral700)
      }
    }
    .blueprintFrame()
  }

  private var readyBlock: some View {
    VStack(spacing: 14) {
      OperatorGlyph(size: 48)
        .frame(width: 84, height: 84)
        .blueprintFrame()

      Text("Camera link ready")
        .headingStyle(26)
        .accessibilityIdentifier("ready_title")

      Text("Start a session to mirror the glasses camera here. Capture a still any time — the LED on the frames tells people nearby.")
        .font(Theme.body(13))
        .foregroundStyle(Theme.neutral600)
        .multilineTextAlignment(.center)
        .lineSpacing(4)
        .fixedSize(horizontal: false, vertical: true)
        .frame(maxWidth: 290)
    }
    .frame(maxWidth: .infinity)
    .padding(.horizontal, 8)
  }
}

// MARK: - Update prompt

/// Blueprint card on an accent tint, shown when the glasses need software before a session
/// can start.
struct UpdateRequiredCard: View {
  let showFirmwareUpdate: Bool
  let showDATAppUpdate: Bool
  let onUpdateFirmware: () -> Void
  let onUpdateGlassesApp: () -> Void

  private var message: String {
    if showFirmwareUpdate && showDATAppUpdate {
      return "Your glasses firmware and app need updates before AssemblyMan can start."
    }
    if showFirmwareUpdate {
      return "Your glasses firmware needs an update before AssemblyMan can start."
    }
    return "The app on your glasses needs an update before AssemblyMan can start."
  }

  var body: some View {
    HStack(alignment: .top, spacing: 10) {
      Icon(glyph: .triangleAlert, size: 20, color: Theme.accent800)
        .padding(.top, 1)

      VStack(alignment: .leading, spacing: 6) {
        Text("Update required")
          .font(Theme.body(12, weight: .semibold))
          .tracking(12 * 0.08)
          .textCase(.uppercase)
          .foregroundStyle(Theme.accent800)

        Text(message)
          .font(Theme.body(12.5))
          .foregroundStyle(Theme.accent900)
          .lineSpacing(3)
          .fixedSize(horizontal: false, vertical: true)

        if showFirmwareUpdate {
          OutlineButton(
            title: "Update firmware",
            height: 34,
            fontSize: 12,
            fillsWidth: false,
            background: Theme.bg,
            action: onUpdateFirmware
          )
        }

        if showDATAppUpdate {
          OutlineButton(
            title: "Update app on glasses",
            height: 34,
            fontSize: 12,
            fillsWidth: false,
            background: Theme.bg,
            action: onUpdateGlassesApp
          )
        }
      }

      Spacer(minLength: 0)
    }
    .padding(12)
    .background(Theme.accent100)
    .blueprintFrame()
  }
}

// MARK: - First-run checklist

struct GettingStartedSheetView: View {
  @Environment(\.dismiss) var dismiss

  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      Rectangle()
        .fill(Theme.neutral300)
        .frame(width: 44, height: 3)
        .frame(maxWidth: .infinity)

      VStack(alignment: .leading, spacing: 2) {
        Text("Checklist")
          .overlineStyle(color: Theme.accent700)
        Text("Before your first session")
          .headingStyle(22)
      }

      VStack(spacing: 0) {
        ChecklistRow(
          glyph: .video,
          text: "AssemblyMan asks once for permission to use the glasses camera."
        )
        ChecklistRow(
          glyph: .camera,
          text: "Tap the shutter button to capture a still from the live session."
        )
        ChecklistRow(
          glyph: .circleDot,
          text: "The capture LED on the frames stays lit whenever the camera is live.",
          isLast: true
        )
      }

      PrimaryButton(title: "Continue", height: 48, fontSize: 13) {
        dismiss()
      }
    }
    .padding(.horizontal, Theme.screenPadding)
    .padding(.top, 20)
    .padding(.bottom, 32)
    .frame(maxHeight: .infinity, alignment: .top)
  }
}

private struct ChecklistRow: View {
  let glyph: Icon.Glyph
  let text: String
  var isLast: Bool = false

  var body: some View {
    HStack(alignment: .top, spacing: 12) {
      Icon(glyph: glyph, size: 20)

      Text(text)
        .font(Theme.body(13))
        .foregroundStyle(Theme.neutral700)
        .lineSpacing(3)
        .fixedSize(horizontal: false, vertical: true)

      Spacer(minLength: 0)
    }
    .padding(.vertical, 10)
    .overlay(alignment: .top) {
      Rectangle().fill(Theme.divider).frame(height: Theme.hairline)
    }
    .overlay(alignment: .bottom) {
      if isLast {
        Rectangle().fill(Theme.divider).frame(height: Theme.hairline)
      }
    }
  }
}
