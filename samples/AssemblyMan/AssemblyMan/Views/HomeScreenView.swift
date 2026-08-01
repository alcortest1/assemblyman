/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// HomeScreenView.swift
//
// Connect screen. Shown until the app is registered with the DAT SDK; connecting hands off
// to the Meta AI app to authorize the link.
//

import MWDATCore
import SwiftUI

struct HomeScreenView: View {
  var viewModel: WearablesViewModel
  var openSettings: () -> Void

  private var isConnecting: Bool {
    viewModel.registrationState == .registering
  }

  var body: some View {
    ZStack {
      Theme.bg.ignoresSafeArea()

      VStack(alignment: .leading, spacing: 16) {
        masthead
        figure
        features

        Spacer(minLength: 8)

        Text("Connecting opens the Meta AI app to authorize the link. Nothing streams until you say so.")
          .font(Theme.body(11.5))
          .foregroundStyle(Theme.neutral500)
          .multilineTextAlignment(.center)
          .lineSpacing(3)
          .fixedSize(horizontal: false, vertical: true)
          .frame(maxWidth: .infinity)
          .padding(.horizontal, 10)

        PrimaryButton(
          title: isConnecting ? "Contacting Meta AI…" : "Connect glasses",
          isBusy: isConnecting
        ) {
          viewModel.connectGlasses()
        }
        .accessibilityIdentifier("connect_glasses_button")
      }
      .padding(.horizontal, Theme.screenPadding)
      .padding(.top, 28)
      .padding(.bottom, 24)
    }
  }

  // MARK: - Sections

  private var masthead: some View {
    VStack(alignment: .leading, spacing: 6) {
      HStack(alignment: .firstTextBaseline) {
        Text("Meta Wearables DAT")
          .overlineStyle(color: Theme.accent700)
        Spacer()
        Text("Sample 01")
          .overlineStyle(color: Theme.neutral500)
      }

      HStack(alignment: .top, spacing: 10) {
        Text("AssemblyMan")
          .headingStyle(38)
        Spacer(minLength: 0)
        IconButton(
          glyph: .slidersHorizontal,
          accessibilityLabel: "Settings",
          action: openSettings
        )
        .accessibilityIdentifier("settings_button")
      }

      Text("A camera link for Ray-Ban Meta glasses.")
        .font(Theme.body(13))
        .foregroundStyle(Theme.neutral600)
    }
  }

  /// Framed plate presenting the brand mark as a technical figure.
  private var figure: some View {
    VStack(spacing: 0) {
      OperatorGlyph(size: 88)
        .frame(height: 132)
        .frame(maxWidth: .infinity)

      Text("Fig. 01 — Operator glyph")
        .overlineStyle(size: 9, color: Theme.neutral500)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 8)
        .padding(.vertical, 5)
        .overlay(alignment: .top) {
          Rectangle().fill(Theme.divider).frame(height: Theme.hairline)
        }
    }
    .frame(width: 200)
    .blueprintFrame()
    .frame(maxWidth: .infinity)
    .padding(.vertical, 4)
  }

  private var features: some View {
    VStack(spacing: 0) {
      FeatureRow(
        index: "01",
        glyph: .video,
        title: "Point-of-view capture",
        detail: "Stream and photograph exactly what you see, straight from the frames."
      )
      FeatureRow(
        index: "02",
        glyph: .volume,
        title: "Open-ear audio",
        detail: "Capture cues play through the frames without closing off the room."
      )
      FeatureRow(
        index: "03",
        glyph: .hand,
        title: "Hands-free by default",
        detail: "Both hands stay on the work. The camera rides along.",
        isLast: true
      )
    }
  }
}

/// Numbered, hairline-separated capability row.
private struct FeatureRow: View {
  let index: String
  let glyph: Icon.Glyph
  let title: String
  let detail: String
  var isLast: Bool = false

  var body: some View {
    HStack(alignment: .top, spacing: 12) {
      Text(index)
        .font(Theme.body(10))
        .foregroundStyle(Theme.neutral500)
        .padding(.top, 3)

      Icon(glyph: glyph, size: 22)
        .padding(.top, 1)

      VStack(alignment: .leading, spacing: 2) {
        Text(title)
          .font(Theme.body(14, weight: .semibold))
          .foregroundStyle(Theme.text)
        Text(detail)
          .font(Theme.body(12.5))
          .foregroundStyle(Theme.neutral600)
          .lineSpacing(3)
          .fixedSize(horizontal: false, vertical: true)
      }

      Spacer(minLength: 0)
    }
    .padding(.vertical, 12)
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
