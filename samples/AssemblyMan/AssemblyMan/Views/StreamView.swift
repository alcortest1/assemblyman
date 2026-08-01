/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// StreamView.swift
//
// Live session. Full-bleed feed from the glasses on a dark ground, framed as a viewfinder,
// with the stop and shutter controls beneath. Overlay elements are individually
// switchable from Settings or the in-stream overlay panel.
//

import MWDATCore
import SwiftUI

struct StreamView: View {
  @Bindable var viewModel: StreamSessionViewModel
  var wearablesVM: WearablesViewModel
  var settings: AppSettings
  var openSettings: () -> Void

  /// White frame shown for a beat after the shutter fires.
  @State private var isFlashing = false
  @State private var showsOverlayControls = false

  var body: some View {
    ZStack {
      Theme.accent900.ignoresSafeArea()

      feed

      if viewModel.isSegmentationOverlayEnabled,
        let segmentationOverlay = viewModel.segmentationOverlay
      {
        GeometryReader { geometry in
          Image(uiImage: segmentationOverlay)
            .resizable()
            .aspectRatio(contentMode: .fill)
            .frame(width: geometry.size.width, height: geometry.size.height)
            .clipped()
        }
        .ignoresSafeArea()
        .allowsHitTesting(false)
        .accessibilityHidden(true)
        .id(viewModel.segmentationRevision)
        .transition(.opacity)
        .animation(.easeInOut(duration: 0.18), value: viewModel.segmentationRevision)
      }

      scrims

      if settings.showsViewfinderMarks {
        viewfinder
      }
      if settings.showsThirdsGrid {
        thirdsGrid
      }
      if viewModel.isReticleOverlayEnabled,
        viewModel.segmentationTargetMode == .reticle
      {
        CenterReticleOverlay()
      }

      VStack(spacing: 10) {
        topBar

        if showsOverlayControls {
          HStack {
            Spacer()
            OverlayControlsView(viewModel: viewModel, settings: settings)
              .transition(.move(edge: .top).combined(with: .opacity))
          }
        }

        Spacer()
        controls
      }
      .padding(.horizontal, Theme.screenPadding)
      .padding(.top, 10)
      .padding(.bottom, 24)

      if isFlashing {
        Color.white.ignoresSafeArea()
      }
    }
    .onChange(of: viewModel.capturedPhoto) { _, photo in
      guard photo != nil else { return }
      flash()
    }
    // Teardown is driven by the view model, not by this view's lifetime: the view is also
    // unmounted for reasons that must not end the session.
    .fullScreenCover(isPresented: $viewModel.showPhotoPreview) {
      if let photo = viewModel.capturedPhoto {
        PhotoPreviewView(photo: photo) {
          viewModel.dismissPhotoPreview()
        }
      }
    }
  }

  // MARK: - Feed

  @ViewBuilder
  private var feed: some View {
    if let videoFrame = viewModel.currentVideoFrame, viewModel.hasReceivedFirstFrame {
      GeometryReader { geometry in
        Image(uiImage: videoFrame)
          .resizable()
          .aspectRatio(contentMode: .fill)
          .frame(width: geometry.size.width, height: geometry.size.height)
          .clipped()
      }
      .ignoresSafeArea()
    } else {
      Spinner(size: 28, color: .white)
    }
  }

  /// Darkens the top and bottom edges so the overlay chips stay legible over any feed.
  private var scrims: some View {
    LinearGradient(
      stops: [
        .init(color: Theme.accent900.opacity(0.55), location: 0),
        .init(color: .clear, location: 0.22),
        .init(color: .clear, location: 0.68),
        .init(color: Theme.accent900.opacity(0.6), location: 1),
      ],
      startPoint: .top,
      endPoint: .bottom
    )
    .ignoresSafeArea()
    .allowsHitTesting(false)
  }

  private var viewfinder: some View {
    Color.clear
      .blueprintFrame(border: nil, mark: .white.opacity(0.85))
      .padding(.horizontal, 18)
      .padding(.top, 74)
      .padding(.bottom, 118)
      .allowsHitTesting(false)
  }

  private var thirdsGrid: some View {
    GeometryReader { geometry in
      let width = geometry.size.width
      let height = geometry.size.height
      ZStack {
        ForEach([1.0 / 3.0, 2.0 / 3.0], id: \.self) { fraction in
          Rectangle()
            .fill(.white.opacity(0.3))
            .frame(width: Theme.hairline)
            .position(x: width * fraction, y: height / 2)
          Rectangle()
            .fill(.white.opacity(0.3))
            .frame(height: Theme.hairline)
            .position(x: width / 2, y: height * fraction)
        }
      }
    }
    .padding(.horizontal, 18)
    .padding(.top, 74)
    .padding(.bottom, 118)
    .allowsHitTesting(false)
  }

  // MARK: - Overlay chrome

  private var topBar: some View {
    HStack {
      if settings.showsStatusChip {
        HStack(spacing: 7) {
          Rectangle()
            .fill(.white)
            .frame(width: 7, height: 7)
            .blinking()
          Text(settings.liveLabel)
            .font(Theme.overline(10))
            .tracking(1.2)
            .textCase(.uppercase)
            .foregroundStyle(.white)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(Theme.accent900.opacity(0.65))
        .overlay { Rectangle().strokeBorder(.white.opacity(0.25), lineWidth: Theme.hairline) }
      }

      Spacer()

      HStack(spacing: 8) {
        if settings.showsElapsedTimer {
          Text(viewModel.elapsedText)
            .font(Theme.body(11).monospacedDigit())
            .tracking(0.9)
            .foregroundStyle(.white)
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(Theme.accent900.opacity(0.65))
            .overlay {
              Rectangle().strokeBorder(.white.opacity(0.25), lineWidth: Theme.hairline)
            }
        }

        IconButton(
          glyph: .circleDot,
          accessibilityLabel: "Overlay controls",
          edge: 28,
          iconSize: 14,
          tint: .white,
          border: .white.opacity(0.25),
          background: Theme.accent900.opacity(0.65)
        ) {
          withAnimation(.snappy(duration: 0.2)) {
            showsOverlayControls.toggle()
          }
        }
        .accessibilityIdentifier("overlay_controls_button")

        IconButton(
          glyph: .slidersHorizontal,
          accessibilityLabel: "Settings",
          edge: 28,
          iconSize: 14,
          tint: .white,
          border: .white.opacity(0.25),
          background: Theme.accent900.opacity(0.65),
          action: openSettings
        )
        .accessibilityIdentifier("settings_button")
      }
    }
    .frame(minHeight: 28)
  }

  private var controls: some View {
    HStack(spacing: 12) {
      Button {
        viewModel.stopSession()
      } label: {
        HStack(spacing: 8) {
          Icon(glyph: .stopSquare, size: 14, color: .white, filled: true)
          Text("Stop session")
            .font(Theme.body(13, weight: .semibold))
            .tracking(13 * 0.06)
            .textCase(.uppercase)
        }
        .foregroundStyle(.white)
        .frame(maxWidth: .infinity)
        .frame(height: 52)
        .background(Theme.accent900.opacity(0.55))
        .overlay { Rectangle().strokeBorder(.white.opacity(0.6), lineWidth: Theme.hairline) }
      }
      .buttonStyle(PressableStyle(pressedOverlay: Theme.accent900.opacity(0.3)))
      .accessibilityIdentifier("stop_streaming_button")

      Button {
        viewModel.capturePhoto()
      } label: {
        Icon(glyph: .camera, size: 20, color: Theme.accent900)
          .frame(width: 52, height: 52)
          .background(.white)
      }
      .buttonStyle(PressableStyle(pressedOverlay: Theme.accent100))
      .blueprintFrame(border: nil, mark: .white.opacity(0.85))
      .accessibilityLabel("Capture photo")
      .accessibilityIdentifier("capture_photo_button")
    }
  }

  // MARK: - Capture feedback

  private func flash() {
    isFlashing = true
    Task {
      try? await Task.sleep(nanoseconds: 170_000_000)
      isFlashing = false
    }
  }
}

private struct OverlayControlsView: View {
  @Bindable var viewModel: StreamSessionViewModel
  @Bindable var settings: AppSettings

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      OverlayToggle(
        title: "MobileSAM",
        systemImage: "person.crop.rectangle.stack",
        isOn: $viewModel.isSegmentationOverlayEnabled,
        showsActivity: viewModel.isGeneratingSegmentation,
        detail: viewModel.segmentationInferenceMilliseconds.map { "\($0) ms" },
        accessibilityIdentifier: "mobile_sam_overlay_toggle"
      )
      MobileSAMTargetPicker(selection: $viewModel.segmentationTargetMode)
      MobileSAMFrameRatePicker(selection: $viewModel.segmentationFrameRate)
      OverlayToggle(
        title: "Grid",
        systemImage: "grid",
        isOn: $settings.showsThirdsGrid
      )
      if viewModel.segmentationTargetMode == .reticle {
        OverlayToggle(
          title: "Reticle",
          systemImage: "scope",
          isOn: $viewModel.isReticleOverlayEnabled
        )
      }
    }
    .padding(14)
    .frame(width: 260)
    .background(Theme.accent900.opacity(0.88))
    .overlay {
      Rectangle().strokeBorder(.white.opacity(0.28), lineWidth: Theme.hairline)
    }
    .foregroundStyle(.white)
    .accessibilityIdentifier("overlay_controls_panel")
  }
}

private struct MobileSAMTargetPicker: View {
  @Binding var selection: MobileSAMTargetMode

  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      Text("Segmentation target")
        .overlineStyle(size: 9, color: .white.opacity(0.7))

      Picker("Segmentation target", selection: $selection) {
        ForEach(MobileSAMTargetMode.allCases) { mode in
          Text(mode.label).tag(mode)
        }
      }
      .pickerStyle(.segmented)
      .tint(Theme.accent500)
      .accessibilityIdentifier("mobile_sam_target_picker")
    }
  }
}

private struct MobileSAMFrameRatePicker: View {
  @Binding var selection: MobileSAMFrameRate

  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      HStack {
        Text("SAM processing rate")
          .overlineStyle(size: 9, color: .white.opacity(0.7))
        Spacer()
        Text("\(selection.label) FPS")
          .font(Theme.body(10).monospacedDigit())
          .foregroundStyle(.white.opacity(0.72))
      }

      Picker("SAM processing rate", selection: $selection) {
        ForEach(MobileSAMFrameRate.allCases) { rate in
          Text(rate.label).tag(rate)
        }
      }
      .pickerStyle(.segmented)
      .tint(Theme.accent500)
      .accessibilityIdentifier("mobile_sam_fps_picker")
    }
  }
}

private struct OverlayToggle: View {
  let title: String
  let systemImage: String
  @Binding var isOn: Bool
  var showsActivity = false
  var detail: String?
  var accessibilityIdentifier: String?

  var body: some View {
    HStack(spacing: 9) {
      Image(systemName: systemImage)
        .frame(width: 20)

      Text(title)
        .font(Theme.body(13, weight: .semibold))
        .tracking(0.6)

      if showsActivity {
        ProgressView()
          .controlSize(.mini)
          .tint(.white)
      } else if let detail {
        Text(detail)
          .font(Theme.body(10).monospacedDigit())
          .foregroundStyle(.white.opacity(0.65))
      }

      Spacer()

      Toggle("", isOn: $isOn)
        .labelsHidden()
        .tint(Theme.accent500)
        .accessibilityIdentifier(accessibilityIdentifier ?? "\(title)_overlay_toggle")
    }
  }
}

private struct CenterReticleOverlay: View {
  var body: some View {
    ZStack {
      Circle()
        .stroke(.white.opacity(0.85), lineWidth: 1.5)
        .frame(width: 34, height: 34)

      Rectangle()
        .fill(.white.opacity(0.85))
        .frame(width: 50, height: 1)

      Rectangle()
        .fill(.white.opacity(0.85))
        .frame(width: 1, height: 50)

      Circle()
        .fill(Theme.accent300)
        .frame(width: 5, height: 5)
    }
    .shadow(color: .black.opacity(0.5), radius: 2)
    .allowsHitTesting(false)
    .accessibilityHidden(true)
  }
}
