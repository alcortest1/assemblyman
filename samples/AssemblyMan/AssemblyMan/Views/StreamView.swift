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

  /// Hands the room code to the iOS share sheet so a viewer can be sent it.
  @State private var isSharingRoomCode = false

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
      if settings.showsSegmentMasks {
        SegmentMaskOverlay()
          .padding(.horizontal, 18)
          .padding(.top, 74)
          .padding(.bottom, 118)
      }
      if settings.showsThirdsGrid {
        thirdsGrid
      }
      if viewModel.isReticleOverlayEnabled,
        viewModel.visionOverlayMode.usesMobileSAM,
        viewModel.segmentationTargetMode == .reticle
      {
        CenterReticleOverlay()
      }

      VStack(spacing: 10) {
        topBar

        // A stalled feed is otherwise indistinguishable from a very still scene: the last
        // frame stays on screen and nothing says the glasses stopped sending. Rebuilding says
        // so quietly — the operator has their hands full, and this is a status, not a
        // decision they have to make.
        if viewModel.isReconnecting || viewModel.isFeedStalled {
          HStack {
            HStack(spacing: 7) {
              if viewModel.isReconnecting {
                Spinner(size: 11, color: .white)
              } else {
                Icon(glyph: .triangleAlert, size: 12, color: .white)
              }
              Text(
                viewModel.isReconnecting
                  ? "Reconnecting to the glasses…"
                  : "No frames for \(viewModel.secondsSinceLastFrame)s"
              )
              .font(Theme.body(11, weight: .semibold))
              .foregroundStyle(.white)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(Theme.accent800.opacity(0.9))
            .overlay { Rectangle().strokeBorder(.white.opacity(0.4), lineWidth: Theme.hairline) }
            Spacer(minLength: 0)
          }
        }

        // Keep the session identity and the expanded controls on separate rows. Together
        // they are wider than the content area on an iPhone, so sharing an HStack caused
        // the fixed-width controls panel to compress or cover the room code.
        if let roomCode = viewModel.relay.roomCode {
          HStack {
            roomChip(roomCode)
            Spacer(minLength: 0)
          }
        }

        if showsOverlayControls {
          HStack {
            Spacer(minLength: 0)
            OverlayControlsView(viewModel: viewModel, settings: settings)
              .transition(.move(edge: .top).combined(with: .opacity))
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
        PhotoPreviewView(
          photo: photo,
          // Only offered once the agent has said what it can grade. Without that the picker
          // would open on an empty list, which reads as a broken button rather than as an
          // assistant that is not in the room.
          onGrade: viewModel.canRequestGrade ? { viewModel.offerCapturedPhotoForGrading() } : nil
        ) {
          viewModel.dismissPhotoPreview()
        }
      }
    }
    // The verdict, and the picker that precedes a hand-started one. Sheets rather than full
    // screen covers: the operator is mid-job and the feed staying visible behind them is
    // worth more than the extra room.
    .sheet(isPresented: $viewModel.showSubtaskPicker) {
      SubtaskPickerView(
        tasks: viewModel.gradeCatalogue,
        onPick: { taskCode, subtaskCode in
          viewModel.gradeAwaitingPhoto(taskCode: taskCode, subtaskCode: subtaskCode)
        },
        onCancel: { viewModel.cancelSubtaskPicker() }
      )
    }
    .sheet(isPresented: $viewModel.showGradeSheet) {
      if let grade = viewModel.currentGrade {
        GradeSheetView(grade: grade) { viewModel.dismissGradeSheet() }
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
    // Read live from the sink rather than the relay's snapshot, which is only taken at
    // publish time. The elapsed clock re-renders this once a second, which is often enough
    // to watch counters move.
    let diagnostics = viewModel.relay.frameSink.diagnostics
    let compositor = viewModel.relay.frameSink.compositor
    VStack(alignment: .leading, spacing: 3) {
      // What the glasses were asked for against what arrived. First thing to read when the
      // feed misbehaves, and it has to be legible before the relay has offered a single frame
      // — "nothing yet" is itself one of the answers.
      Text(viewModel.deliveredSpec)
        .font(Theme.body(9).monospacedDigit())
        .foregroundStyle(viewModel.isDownscaled ? .orange : .white.opacity(0.5))
        .padding(.horizontal, 6)
        .padding(.vertical, 2)
        .background(Theme.accent900.opacity(0.5))

      if diagnostics.framesOffered > 0 {
        // Assembled in steps rather than one expression: as a single concatenation with
        // interpolation and three conditionals the type checker gives up on it.
        Text(Self.diagnosticsText(diagnostics, compositor, viewModel.relay.microphoneIssue))
          .font(Theme.body(9).monospacedDigit())
          .foregroundStyle(
            diagnostics.isPixelFormatSupported == false || compositor.lastFailure != nil
              ? .red : .white.opacity(0.5)
          )
          .padding(.horizontal, 6)
          .padding(.vertical, 2)
          .background(Theme.accent900.opacity(0.5))
      }
    }
  }

  private static func diagnosticsText(
    _ diagnostics: LiveKitFrameSink.Diagnostics,
    _ compositor: RelayFrameCompositor,
    _ microphoneIssue: String?
  ) -> String {
    var parts: [String] = [
      "\(diagnostics.framesForwarded)/\(diagnostics.framesOffered)",
      diagnostics.pixelFormatDescription,
    ]
    if diagnostics.isPixelFormatSupported == false {
      parts.append("UNSUPPORTED")
    }
    // The failure that otherwise reads as a healthy session: counters climbing, format "—",
    // and a black feed everywhere. Named here because it is the only place it can be seen
    // without attaching a console — collecting device logs needs root.
    if diagnostics.isDeliveringEncodedFrames {
      parts.append("ENCODED \(diagnostics.mediaSubTypeDescription) — NO PIXELS")
    }
    // Present only while burning in an overlay, so its absence is as informative as its
    // value when a viewer reports a bare feed. A climbing skip count means compositing
    // cannot keep up with the frame rate — degradation, not failure.
    if compositor.isCompositing {
      var overlay = "OVERLAY \(diagnostics.framesComposited)"
      if diagnostics.framesSkippedComposite > 0 {
        overlay += "/skip \(diagnostics.framesSkippedComposite)"
      }
      parts.append(overlay)
    }
    if let failure = compositor.lastFailure {
      parts.append(failure)
    }
    // A relay with no audio looks healthy from every other angle, so the reason belongs on
    // screen rather than only in the log.
    if let microphoneIssue {
      parts.append("MIC: \(microphoneIssue)")
    }
    return parts.joined(separator: " · ")
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

private struct OverlayControlsView: View {
  @Bindable var viewModel: StreamSessionViewModel
  @Bindable var settings: AppSettings

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      OverlayToggle(
        title: "Vision overlay",
        systemImage: "person.crop.rectangle.stack",
        isOn: $viewModel.isSegmentationOverlayEnabled,
        showsActivity: viewModel.isGeneratingSegmentation,
        detail: inferenceDetail,
        accessibilityIdentifier: "vision_overlay_toggle"
      )
      VisionModePicker(selection: $viewModel.visionOverlayMode)
      if viewModel.visionOverlayMode.usesMobileSAM {
        MobileSAMTargetPicker(selection: $viewModel.segmentationTargetMode)
      } else {
        YOLOColorLegend(mode: viewModel.visionOverlayMode)
      }
      VisionFrameRatePicker(selection: $viewModel.segmentationFrameRate)
      OverlayToggle(
        title: "Grid",
        systemImage: "grid",
        isOn: $settings.showsThirdsGrid
      )
      if viewModel.visionOverlayMode.usesMobileSAM,
        viewModel.segmentationTargetMode == .reticle
      {
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

  private var inferenceDetail: String? {
    guard let milliseconds = viewModel.segmentationInferenceMilliseconds else {
      return nil
    }
    if let regions = viewModel.segmentationColoredRegions {
      return "\(milliseconds) ms · \(regions)"
    }
    return "\(milliseconds) ms"
  }
}

private struct VisionModePicker: View {
  @Binding var selection: VisionOverlayMode

  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      Text("Model / task")
        .overlineStyle(size: 9, color: .white.opacity(0.7))

      Picker("Model and task", selection: $selection) {
        ForEach(VisionOverlayMode.allCases) { mode in
          Text(mode.label).tag(mode)
        }
      }
      .pickerStyle(.menu)
      .tint(.white)
      .accessibilityIdentifier("vision_mode_picker")
    }
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

private struct VisionFrameRatePicker: View {
  @Binding var selection: VisionFrameRate

  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      HStack {
        Text("Processing rate")
          .overlineStyle(size: 9, color: .white.opacity(0.7))
        Spacer()
        Text("\(selection.label) FPS")
          .font(Theme.body(10).monospacedDigit())
          .foregroundStyle(.white.opacity(0.72))
      }

      Picker("Vision processing rate", selection: $selection) {
        ForEach(VisionFrameRate.allCases) { rate in
          Text("\(rate.label) FPS").tag(rate)
        }
      }
      .pickerStyle(.menu)
      .tint(.white)
      .accessibilityIdentifier("vision_fps_picker")
    }
  }
}

private struct YOLOColorLegend: View {
  let mode: VisionOverlayMode

  private var visibleClasses: [YOLOOverlayClass] {
    YOLOOverlayClass.legendClasses(for: mode)
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      Text("Color map")
        .overlineStyle(size: 9, color: .white.opacity(0.7))

      ScrollView(.horizontal, showsIndicators: false) {
        LazyHGrid(
          rows: [
            GridItem(.fixed(15), alignment: .leading),
            GridItem(.fixed(15), alignment: .leading),
          ],
          alignment: .top,
          spacing: 5
        ) {
          ForEach(visibleClasses) { overlayClass in
            HStack(spacing: 6) {
              RoundedRectangle(cornerRadius: 2)
                .fill(Color(uiColor: overlayClass.color))
                .frame(width: 10, height: 10)
              Text(overlayClass.label)
                .font(Theme.body(10))
                .foregroundStyle(.white.opacity(0.82))
            }
            .fixedSize(horizontal: true, vertical: false)
          }
        }
      }
      .frame(height: 35)
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
