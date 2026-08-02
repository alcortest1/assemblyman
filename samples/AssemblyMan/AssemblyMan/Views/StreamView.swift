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
// switchable from Settings.
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

  /// Hands the room code to the iOS share sheet so a viewer can be sent it.
  @State private var isSharingRoomCode = false

  var body: some View {
    ZStack {
      Theme.accent900.ignoresSafeArea()

      feed
      scrims

      if settings.showsViewfinderMarks {
        viewfinder
      }
      if settings.showsSegmentMasks {
        SegmentMaskOverlay()
          .padding(.horizontal, 18)
          .padding(.top, 74)
          .padding(.bottom, 118)
      }
      if settings.showsThirdsGrid {
        thirdsGrid
      }

      VStack {
        topBar
        if let roomCode = viewModel.relay.roomCode {
          HStack {
            roomChip(roomCode)
            Spacer(minLength: 0)
          }
        }

        #if DEBUG
        HStack {
          relayDiagnostics
          Spacer(minLength: 0)
        }
        #endif
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
    .sheet(isPresented: $isSharingRoomCode) {
      if let roomCode = viewModel.relay.roomCode {
        ShareSheet(activityItems: [
          "Join my AssemblyMan session — room \(roomCode.display)",
          URL(string: "https://assemblyman.vercel.app/#/room/\(roomCode.display)")!,
        ])
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

  /// The code someone else types to watch this session. Sits under the status row so it reads
  /// as session identity rather than as another piece of live telemetry.
  private func roomChip(_ roomCode: RoomCode) -> some View {
    HStack(spacing: 8) {
      Text("Room")
        .font(Theme.overline(10))
        .tracking(1.2)
        .textCase(.uppercase)
        .foregroundStyle(.white.opacity(0.75))

      Text(roomCode.display)
        .font(Theme.body(12, weight: .semibold).monospacedDigit())
        .tracking(1.68)
        .foregroundStyle(.white)

      Button {
        isSharingRoomCode = true
      } label: {
        Icon(glyph: .share, size: 12, color: .white)
          .frame(width: 22, height: 22)
          .overlay { Rectangle().strokeBorder(.white.opacity(0.35), lineWidth: Theme.hairline) }
      }
      .buttonStyle(PressableStyle(pressedOverlay: .white.opacity(0.15)))
      .accessibilityLabel("Share room code")
    }
    .padding(.leading, 10)
    .padding(.trailing, 4)
    .padding(.vertical, 4)
    .background(Theme.accent900.opacity(0.65))
    .overlay { Rectangle().strokeBorder(.white.opacity(0.25), lineWidth: Theme.hairline) }
    .accessibilityIdentifier("room_code_chip")
  }

  #if DEBUG
  /// Whether frames are reaching the encoder, and in what pixel format.
  ///
  /// WebRTC drops any buffer whose format it cannot encode, and it does so quietly — the
  /// symptom is a relay that connects and then publishes nothing. Showing offered-vs-forwarded
  /// counts alongside the format turns that into something readable at a glance.
  @ViewBuilder
  private var relayDiagnostics: some View {
    let diagnostics = viewModel.relay.diagnostics
    if diagnostics.framesOffered > 0 {
      Text(
        "\(diagnostics.framesForwarded)/\(diagnostics.framesOffered) · \(diagnostics.pixelFormatDescription)"
          + (diagnostics.isPixelFormatSupported == false ? " · UNSUPPORTED" : "")
      )
      .font(Theme.body(9).monospacedDigit())
      .foregroundStyle(diagnostics.isPixelFormatSupported == false ? .red : .white.opacity(0.5))
      .padding(.horizontal, 6)
      .padding(.vertical, 2)
      .background(Theme.accent900.opacity(0.5))
    }
  }
  #endif

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

      // Only meaningful while the relay is up — there is nobody to talk to otherwise.
      if viewModel.relay.status == .live {
        Button {
          viewModel.relay.setMicrophone(enabled: !viewModel.relay.isMicrophoneEnabled)
        } label: {
          Icon(
            glyph: viewModel.relay.isMicrophoneEnabled ? .mic : .micOff,
            size: 20,
            color: .white
          )
          .frame(width: 52, height: 52)
          .background(Theme.accent900.opacity(0.55))
          .overlay { Rectangle().strokeBorder(.white.opacity(0.6), lineWidth: Theme.hairline) }
        }
        .buttonStyle(PressableStyle(pressedOverlay: Theme.accent900.opacity(0.3)))
        .accessibilityLabel(viewModel.relay.isMicrophoneEnabled ? "Mute microphone" : "Unmute microphone")
        .accessibilityIdentifier("microphone_button")
      }

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
