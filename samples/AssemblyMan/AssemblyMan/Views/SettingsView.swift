/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// SettingsView.swift
//
// Session and overlay preferences, reachable from the ready header and from the in-session
// controls. Quality and frame rate apply to the next session; overlay toggles take effect
// immediately.
//

import SwiftUI

struct SettingsView: View {
  @Bindable var settings: AppSettings
  var onBack: () -> Void
  var onDisconnect: () -> Void

  var body: some View {
    ZStack {
      Theme.bg.ignoresSafeArea()

      VStack(spacing: 0) {
        header

        ScrollView {
          VStack(alignment: .leading, spacing: 20) {
            agentSection
            overlaySection
            sessionSection
            captureSection

            OutlineButton(title: "Disconnect glasses", action: onDisconnect)
              .accessibilityIdentifier("disconnect_button")
              .padding(.bottom, 8)
          }
          .padding(.horizontal, Theme.screenPadding)
          .padding(.top, 16)
          .padding(.bottom, 8)
        }
      }
      .padding(.top, 12)
      .padding(.bottom, 16)
    }
  }

  // MARK: - Header

  private var header: some View {
    HStack(spacing: 10) {
      IconButton(glyph: .chevronLeft, accessibilityLabel: "Back", iconSize: 16, action: onBack)
        .accessibilityIdentifier("settings_back_button")

      Text("Settings")
        .headingStyle(20)

      Spacer()

      Text("Ray-Ban Meta")
        .overlineStyle(size: 9, color: Theme.neutral500)
    }
    .padding(.horizontal, Theme.screenPadding)
    .padding(.bottom, 12)
    .overlay(alignment: .bottom) {
      Rectangle().fill(Theme.divider).frame(height: Theme.hairline)
    }
  }

  // MARK: - Sections

  private var agentSection: some View {
    Section(
      title: "Agent",
      caption: "Rides along on the session and speaks through the frames."
    ) {
      VStack(spacing: 0) {
        ForEach(Array(AppSettings.Agent.allCases.enumerated()), id: \.element.id) { index, agent in
          Button {
            settings.agent = agent
          } label: {
            HStack(alignment: .top, spacing: 10) {
              SelectionBox(isOn: settings.agent == agent)
                .padding(.top, 1)

              VStack(alignment: .leading, spacing: 1) {
                Text(agent.name)
                  .font(Theme.body(13.5, weight: .semibold))
                  .foregroundStyle(Theme.text)
                Text(agent.detail)
                  .font(Theme.body(11.5))
                  .foregroundStyle(Theme.neutral600)
                  .lineSpacing(2)
                  .fixedSize(horizontal: false, vertical: true)
              }

              Spacer(minLength: 0)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .contentShape(Rectangle())
          }
          .buttonStyle(PressableStyle(pressedOverlay: Theme.text.opacity(0.05)))
          .accessibilityAddTraits(settings.agent == agent ? [.isSelected] : [])

          if index < AppSettings.Agent.allCases.count - 1 {
            Rectangle().fill(Theme.divider).frame(height: Theme.hairline)
          }
        }
      }
      .blueprintFrame()
    }
  }

  private var overlaySection: some View {
    Section(title: "Overlays") {
      VStack(spacing: 0) {
        ToggleRow(
          label: "Viewfinder marks",
          detail: "Registration crosses at the frame corners",
          isOn: $settings.showsViewfinderMarks
        )
        Rectangle().fill(Theme.divider).frame(height: Theme.hairline)
        ToggleRow(
          label: "Thirds grid",
          detail: "Rule-of-thirds guides over the feed",
          isOn: $settings.showsThirdsGrid
        )
        Rectangle().fill(Theme.divider).frame(height: Theme.hairline)
        ToggleRow(
          label: "Live status chip",
          detail: "REC dot, resolution and frame rate",
          isOn: $settings.showsStatusChip
        )
        Rectangle().fill(Theme.divider).frame(height: Theme.hairline)
        ToggleRow(
          label: "Elapsed timer",
          detail: "Session clock, top right",
          isOn: $settings.showsElapsedTimer
        )
      }
      .blueprintFrame()
    }
  }

  private var sessionSection: some View {
    Section(title: "Session") {
      VStack(spacing: 0) {
        ToggleRow(
          label: "Stream over Wi-Fi",
          detail: "Higher-resolution video than Bluetooth. Uses more battery.",
          isOn: $settings.streamsOverWiFi
        )

        Rectangle().fill(Theme.divider).frame(height: Theme.hairline)

        PickerRow(label: "Quality") {
          SegmentedPicker(
            options: settings.availableQualities,
            selection: settings.quality,
            title: \.label,
            select: { settings.quality = $0 }
          )
        }

        Rectangle().fill(Theme.divider).frame(height: Theme.hairline)

        PickerRow(label: "Frame rate") {
          SegmentedPicker(
            options: AppSettings.FrameRate.allCases,
            selection: settings.frameRate,
            title: \.label,
            select: { settings.frameRate = $0 }
          )
        }
      }
      .blueprintFrame()
    }
  }

  private var captureSection: some View {
    Section(title: "Capture") {
      VStack(spacing: 0) {
        ToggleRow(
          label: "Save to Photos",
          detail: "File every capture to the camera roll",
          isOn: $settings.savesToPhotos
        )
        Rectangle().fill(Theme.divider).frame(height: Theme.hairline)
        ToggleRow(
          label: "Shutter cue",
          detail: "Play a sound in the frames on capture",
          isOn: $settings.playsShutterCue
        )
      }
      .blueprintFrame()
    }
  }
}

// MARK: - Building blocks

/// Overline-labelled group with an optional explanatory caption.
private struct Section<Content: View>: View {
  let title: String
  var caption: String?
  @ViewBuilder var content: () -> Content

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      VStack(alignment: .leading, spacing: 2) {
        Text(title)
          .overlineStyle(color: Theme.accent700)
        if let caption {
          Text(caption)
            .font(Theme.body(11.5))
            .foregroundStyle(Theme.neutral500)
            .fixedSize(horizontal: false, vertical: true)
        }
      }
      content()
    }
  }
}

private struct ToggleRow: View {
  let label: String
  let detail: String
  @Binding var isOn: Bool

  var body: some View {
    HStack(spacing: 10) {
      VStack(alignment: .leading, spacing: 1) {
        Text(label)
          .font(Theme.body(13.5, weight: .semibold))
          .foregroundStyle(Theme.text)
        Text(detail)
          .font(Theme.body(11.5))
          .foregroundStyle(Theme.neutral600)
          .fixedSize(horizontal: false, vertical: true)
      }

      Spacer(minLength: 0)

      SquareCheckbox(isOn: isOn, accessibilityLabel: label) {
        isOn.toggle()
      }
    }
    .padding(.horizontal, 12)
    .padding(.vertical, 10)
  }
}

private struct PickerRow<Content: View>: View {
  let label: String
  @ViewBuilder var content: () -> Content

  var body: some View {
    HStack(spacing: 10) {
      Text(label)
        .font(Theme.body(13.5, weight: .semibold))
        .foregroundStyle(Theme.text)
      Spacer(minLength: 0)
      content()
    }
    .padding(.horizontal, 12)
    .padding(.vertical, 10)
  }
}

/// Square radio indicator used by the agent list.
private struct SelectionBox: View {
  let isOn: Bool

  var body: some View {
    ZStack {
      Rectangle().strokeBorder(Theme.neutral400, lineWidth: Theme.hairline)
      if isOn {
        Rectangle()
          .fill(Theme.accent)
          .frame(width: 10, height: 10)
      }
    }
    .frame(width: 18, height: 18)
  }
}
