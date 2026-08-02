/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

import MWDATCamera
import MWDATCore
import Observation
import SwiftUI

enum StreamingStatus {
  case streaming
  case waiting
  case stopped
}

/// ViewModel for video streaming UI. Delegates device management to DeviceSessionManager.
@Observable
@MainActor
final class StreamSessionViewModel {
  // MARK: - State

  var currentVideoFrame: UIImage?
  var hasReceivedFirstFrame: Bool = false
  var streamingStatus: StreamingStatus = .stopped
  var showError: Bool = false
  var errorMessage: String = ""
  var requiresDATAppUpdate: Bool = false

  var capturedPhoto: UIImage?
  var showPhotoPreview: Bool = false
  var showPhotoCaptureError: Bool = false
  var isCapturingPhoto: Bool = false

  var isSegmentationOverlayEnabled: Bool = false {
    didSet {
      if !isSegmentationOverlayEnabled {
        segmentationTask?.cancel()
        segmentationOverlay = nil
        isGeneratingSegmentation = false
        segmentationInferenceMilliseconds = nil
        segmentationColoredRegions = nil
        lastSegmentationTime = nil
      }
    }
  }
  var visionOverlayMode: VisionOverlayMode = .mobileSAM {
    didSet {
      guard visionOverlayMode != oldValue else { return }
      segmentationTask?.cancel()
      segmentationOverlay = nil
      segmentationInferenceMilliseconds = nil
      segmentationColoredRegions = nil
      lastSegmentationTime = nil
    }
  }
  var segmentationTargetMode: MobileSAMTargetMode = .reticle {
    didSet {
      guard segmentationTargetMode != oldValue else { return }
      segmentationTask?.cancel()
      segmentationOverlay = nil
      segmentationInferenceMilliseconds = nil
      segmentationColoredRegions = nil
      lastSegmentationTime = nil
    }
  }
  var segmentationFrameRate: VisionFrameRate = .one {
    didSet {
      guard segmentationFrameRate != oldValue else { return }
      lastSegmentationTime = nil
    }
  }
  var isReticleOverlayEnabled: Bool = true

  /// Seconds since the last frame arrived from the glasses, while streaming. Zero while the
  /// feed is healthy. Anything above a couple of seconds means the picture on screen is a
  /// still of the past.
  private(set) var secondsSinceLastFrame: Int = 0
  /// True once the gap is long enough that this is a stall rather than a slow frame.
  var isFeedStalled: Bool { secondsSinceLastFrame >= 3 }
  @ObservationIgnored private var lastFrameAt: Date?
  /// Coalesces preview frames so the main actor never accumulates a backlog.
  @ObservationIgnored private let previewGate = PreviewFrameGate()
  /// Reset by a frame arriving, so a session that recovers gets its full budget back.
  @ObservationIgnored private var feedRecoveryAttempts = 0
  /// The vision overlay drawn over the feed.
  ///
  /// Also handed to the relay, so a remote viewer sees the same overlay rather than the bare
  /// camera. Every path that hides it on screen sets this to nil, so mirroring it here keeps
  /// the two in step without a second switch to forget.
  var segmentationOverlay: UIImage? {
    didSet {
      relay.frameSink.compositor.setOverlay(segmentationOverlay?.cgImage)
    }
  }
  var isGeneratingSegmentation: Bool = false
  var segmentationInferenceMilliseconds: Int?
  var segmentationColoredRegions: Int?
  var segmentationRevision: UInt = 0

  var hasActiveDevice: Bool { sessionManager.hasActiveDevice }
  var isDeviceSessionReady: Bool { sessionManager.isReady }

  var isStreaming: Bool { streamingStatus != .stopped }

  /// Mirrors the glasses feed into a LiveKit room. Owned here because the relay must live
  /// exactly as long as the streaming session — surviving the Settings screen, which layers
  /// above this view rather than replacing it, and ending when the session does.
  let relay: LiveKitRelay

  // MARK: - Private

  private let sessionManager: DeviceSessionManager
  private let wearables: WearablesInterface
  private let settings: AppSettings
  private var stream: MWDATCamera.Stream?

  private var stateListenerToken: AnyListenerToken?
  private var videoFrameListenerToken: AnyListenerToken?
  private var errorListenerToken: AnyListenerToken?
  private var photoDataListenerToken: AnyListenerToken?
  /// Set when a stop is only a step towards restarting with a new configuration.
  @ObservationIgnored private var wantsRestart = false

  /// Seconds since the current session started streaming, shown in the overlay clock.
  var elapsedSeconds: Int = 0
  @ObservationIgnored private var elapsedTask: Task<Void, Never>?

  var elapsedText: String {
    String(format: "%02d:%02d", elapsedSeconds / 60, elapsedSeconds % 60)
  }

  private var segmentationTask: Task<Void, Never>?
  private var lastSegmentationTime: ContinuousClock.Instant?
  private let mobileSAMProcessor = MobileSAMProcessor()
  private let yoloProcessor = YOLOProcessor()

  // MARK: - Init

  /// `relay` is injectable for tests. It defaults to nil rather than to `LiveKitRelay()`
  /// because a default argument is evaluated outside the actor, and the relay is
  /// main-actor-isolated — building it in the body keeps that isolation intact.
  init(
    wearables: WearablesInterface,
    settings: AppSettings,
    relay: LiveKitRelay? = nil
  ) {
    self.wearables = wearables
    self.settings = settings
    self.relay = relay ?? LiveKitRelay()
    self.sessionManager = DeviceSessionManager(wearables: wearables)
  }

  // MARK: - Public API

  func handleStartStreaming() async {
    let permission = Permission.camera
    do {
      var status = try await wearables.checkPermissionStatus(permission)
      if status != .granted {
        status = try await wearables.requestPermission(permission)
      }
      guard status == .granted else {
        showError("Permission denied")
        return
      }
      await startSession()
    } catch {
      // Use `localizedDescription` for user-facing text — `description` is
      // always English and intended for logs.
      showError("Permission error: \(error.localizedDescription)")
    }
  }

  func stopSession() {
    wantsRestart = false
    stream?.stop()
  }

  /// Rebuilds the stream so a changed `StreamConfiguration` takes effect.
  ///
  /// A stopped session is terminal, so this cannot mutate the running stream — it stops the
  /// current one and starts a fresh session once the SDK reports `.stopped`.
  func restartStream() {
    guard isStreaming else { return }
    wantsRestart = true
    stream?.stop()
  }

  /// Stops both the stream and the underlying device session. Call in test tearDown.
  func endSession() {
    wantsRestart = false
    relay.stop()
    stream = nil
    clearListeners()
    stopElapsedClock()
    streamingStatus = .stopped
    currentVideoFrame = nil
    hasReceivedFirstFrame = false
    lastFrameAt = nil
    secondsSinceLastFrame = 0
    feedRecoveryAttempts = 0
    previewGate.reset()
    resetSegmentation()
    sessionManager.cleanup()
  }

  func capturePhoto() {
    guard !isCapturingPhoto, streamingStatus == .streaming else {
      showPhotoCaptureError = true
      return
    }
    isCapturingPhoto = true
    let success = stream?.capturePhoto(format: .jpeg) ?? false
    if !success {
      isCapturingPhoto = false
      showPhotoCaptureError = true
    }
  }

  func dismissError() {
    showError = false
    errorMessage = ""
  }

  func dismissPhotoCaptureError() {
    showPhotoCaptureError = false
  }

  func dismissPhotoPreview() {
    showPhotoPreview = false
    capturedPhoto = nil
  }

  // MARK: - Private

  private func startSession() async {
    let deviceSession: DeviceSession
    do {
      deviceSession = try await sessionManager.getSession()
      requiresDATAppUpdate = false
    } catch DeviceSessionError.datAppOnTheGlassesUpdateRequired {
      requiresDATAppUpdate = true
      showError(DeviceSessionError.datAppOnTheGlassesUpdateRequired.localizedDescription)
      return
    } catch {
      showError("Failed to start session: \(error.localizedDescription)")
      return
    }

    guard deviceSession.state == .started else {
      showError("Device session is not ready. Please try again.")
      return
    }

    let config = StreamConfiguration(
      videoCodec: VideoCodec.raw,
      resolution: settings.quality.streamingResolution,
      frameRate: UInt(settings.frameRate.rawValue)
    )

    do {
      guard let newStream = try deviceSession.addStream(config: config) else {
        showError("Unable to create stream. Please try again.")
        return
      }
      stream = newStream
      streamingStatus = .waiting
      setupListeners(for: newStream)
      newStream.start()

      // Fire-and-forget: the room connects alongside the glasses stream rather than behind
      // it. Idempotent, so the restart path below re-enters this without disturbing a room
      // that is already up.
      if settings.relaysToLiveKit {
        relay.start(agent: settings.agent)
      }
    } catch {
      showError("Failed to start stream: \(error.localizedDescription)")
    }
  }

  private func setupListeners(for stream: MWDATCamera.Stream) {
    stateListenerToken = stream.statePublisher.listen { [weak self] state in
      Task { @MainActor in self?.handleStateChange(state) }
    }

    // Resolved here, on the main actor, so the background closure below captures a plain
    // Sendable value instead of reaching through main-actor-isolated `self` on the frame
    // delivery thread.
    let sink = relay.frameSink

    let gate = previewGate

    videoFrameListenerToken = stream.videoFramePublisher.listen { [weak self] frame in
      // The relay is fed on the delivery thread, before the hop: handing the buffer to
      // WebRTC is a non-blocking enqueue, and going by way of the main actor would put a
      // 30-per-second workload behind SwiftUI layout with no ordering guarantee.
      sink.capture(frame.sampleBuffer)

      // Decode here rather than on the main actor. `makeUIImage` is a full image conversion,
      // and running it per frame on the actor that also draws the UI is work the preview
      // does not need to own.
      guard let image = frame.makeUIImage() else { return }

      // One frame in flight at a time. Scheduling a hop per frame queued work without limit
      // whenever the main actor fell behind, holding every frame's buffer alive until it
      // caught up — which over Bluetooth means a burst after a hiccup freezes the screen.
      if gate.offer(image) {
        Task { @MainActor in self?.drainPreviewGate() }
      }
    }

    errorListenerToken = stream.errorPublisher.listen { [weak self] error in
      Task { @MainActor in self?.handleError(error) }
    }

    photoDataListenerToken = stream.photoDataPublisher.listen { [weak self] data in
      Task { @MainActor in self?.handlePhotoData(data) }
    }
  }

  private func clearListeners() {
    stateListenerToken = nil
    videoFrameListenerToken = nil
    errorListenerToken = nil
    photoDataListenerToken = nil
  }

  private func handleStateChange(_ state: StreamState) {
    switch state {
    case .stopped:
      currentVideoFrame = nil
      streamingStatus = .stopped
      stream = nil
      clearListeners()
      hasReceivedFirstFrame = false
      stopElapsedClock()
      resetSegmentation()
      sessionManager.stopCurrentSession()

      if wantsRestart {
        // A configuration change rebuilds the DAT session but deliberately leaves the room
        // alone: the code stays valid and anyone watching keeps their connection, seeing
        // only a brief freeze. Tearing the relay down here would mint a new code and drop
        // every viewer on each settings change.
        wantsRestart = false
        Task { await handleStartStreaming() }
      } else {
        relay.stop()
      }
    case .waitingForDevice, .starting, .stopping, .paused:
      streamingStatus = .waiting
    case .streaming:
      streamingStatus = .streaming
      startElapsedClock()
    }
  }

  /// Runs the session clock from 00:00 while the stream is live.
  private func startElapsedClock() {
    guard elapsedTask == nil else { return }
    elapsedSeconds = 0
    elapsedTask = Task { [weak self] in
      while !Task.isCancelled {
        try? await Task.sleep(nanoseconds: 1_000_000_000)
        guard !Task.isCancelled else { return }
        self?.elapsedSeconds += 1
        self?.checkFeedLiveness()
      }
    }
  }

  private func stopElapsedClock() {
    elapsedTask?.cancel()
    elapsedTask = nil
  }

  private func drainPreviewGate() {
    guard let image = previewGate.take() else { return }
    lastFrameAt = Date()
    if secondsSinceLastFrame != 0 { secondsSinceLastFrame = 0 }
    // Frames are flowing again, so a later stall gets a fresh recovery budget.
    if feedRecoveryAttempts != 0 { feedRecoveryAttempts = 0 }

    currentVideoFrame = image
    if !hasReceivedFirstFrame {
      hasReceivedFirstFrame = true
    }
    scheduleSegmentation(for: image)
  }

  /// Notices when the glasses stop sending, and rebuilds the stream when they do.
  ///
  /// A stalled feed is silent: the SDK reports no error and no state change, the last frame
  /// stays on screen, and the session looks live. Over Bluetooth this happens for ordinary
  /// reasons — the link saturates, the frames warm up and throttle — and it does not recover
  /// on its own, because nothing downstream knows anything is wrong.
  ///
  /// Rebuilding is the only lever available: a stopped DAT session is terminal, so there is
  /// nothing to nudge, and `restartStream()` already knows how to tear one down and stand a
  /// fresh one up. The relay deliberately survives it, so viewers keep their room code and
  /// the assistant keeps its context across the gap.
  private func checkFeedLiveness() {
    guard streamingStatus == .streaming, let lastFrameAt else { return }
    secondsSinceLastFrame = Int(Date().timeIntervalSince(lastFrameAt))

    guard secondsSinceLastFrame >= Self.stallRecoverySeconds else { return }
    guard feedRecoveryAttempts < Self.maxFeedRecoveryAttempts else { return }

    // Capped rather than endless. If three rebuilds do not bring frames back, the problem is
    // the glasses or the link, and retrying forever would hide that behind a loop while
    // burning battery on both devices.
    feedRecoveryAttempts += 1
    self.lastFrameAt = Date()
    showError(
      "The glasses stopped sending video. Restarting the stream "
        + "(attempt \(feedRecoveryAttempts) of \(Self.maxFeedRecoveryAttempts))."
    )
    restartStream()
  }

  /// How long a gap has to be before it counts as a stall rather than a slow frame. Wide
  /// enough to sit out a Bluetooth hiccup, short enough that nobody stares at a dead picture.
  private static let stallRecoverySeconds = 8
  private static let maxFeedRecoveryAttempts = 3

  private func scheduleSegmentation(for image: UIImage) {
    guard
      isSegmentationOverlayEnabled,
      segmentationTask == nil,
      let cgImage = image.cgImage
    else {
      return
    }

    let now = ContinuousClock.now
    if let lastSegmentationTime,
      now - lastSegmentationTime < segmentationFrameRate.interval
    {
      return
    }

    let overlayMode = visionOverlayMode
    let targetMode = segmentationTargetMode
    lastSegmentationTime = now
    isGeneratingSegmentation = true
    segmentationTask = Task { [weak self] in
      guard let self else { return }
      let inferenceResult: VisionInferenceResult
      if overlayMode.usesMobileSAM {
        let result = await self.mobileSAMProcessor.makeOverlay(
          for: cgImage,
          targetMode: targetMode
        )
        switch result {
        case .success(let overlay, let inferenceMilliseconds):
          inferenceResult = .success(
            image: overlay,
            inferenceMilliseconds: inferenceMilliseconds,
            coloredRegions: nil
          )
        case .failure(let message):
          inferenceResult = .failure(message: message)
        }
      } else {
        let result = await self.yoloProcessor.makeOverlay(
          for: cgImage,
          mode: overlayMode
        )
        switch result {
        case .success(let overlay, let inferenceMilliseconds, let coloredRegions):
          inferenceResult = .success(
            image: overlay,
            inferenceMilliseconds: inferenceMilliseconds,
            coloredRegions: coloredRegions
          )
        case .failure(let message):
          inferenceResult = .failure(message: message)
        }
      }

      if !Task.isCancelled,
        self.isSegmentationOverlayEnabled,
        self.visionOverlayMode == overlayMode,
        self.segmentationTargetMode == targetMode
      {
        switch inferenceResult {
        case .success(let overlay, let inferenceMilliseconds, let coloredRegions):
          self.segmentationOverlay = overlay
          self.segmentationInferenceMilliseconds = inferenceMilliseconds
          self.segmentationColoredRegions = coloredRegions
          self.segmentationRevision &+= 1
        case .failure(let message):
          self.isSegmentationOverlayEnabled = false
          self.showError("\(overlayMode.label) overlay failed: \(message)")
        }
      }
      self.isGeneratingSegmentation = false
      self.segmentationTask = nil
    }
  }

  private func handleError(_ error: StreamError) {
    let message = error.localizedDescription
    if message != errorMessage {
      showError(message)
    }
  }

  private func handlePhotoData(_ data: PhotoData) {
    isCapturingPhoto = false
    if let image = UIImage(data: data.data) {
      capturedPhoto = image
      showPhotoPreview = true
    }
  }

  private func showError(_ message: String) {
    errorMessage = message
    showError = true
  }

  private func resetSegmentation() {
    segmentationTask?.cancel()
    segmentationOverlay = nil
    isGeneratingSegmentation = false
    segmentationInferenceMilliseconds = nil
    segmentationColoredRegions = nil
    segmentationRevision = 0
    lastSegmentationTime = nil
    Task {
      await mobileSAMProcessor.reset()
      await yoloProcessor.reset()
    }
  }
}

private enum VisionInferenceResult {
  case success(image: UIImage, inferenceMilliseconds: Int, coloredRegions: Int?)
  case failure(message: String)
}

/// Keeps the on-device preview from flooding the main actor.
///
/// Frames arrive on the SDK's delivery thread and the preview has to be drawn on the main
/// actor, so each one needs a hop. Scheduling that hop per frame queues work without limit:
/// when the main actor falls behind — and it does, because it also runs SwiftUI layout and
/// the segmentation the preview triggers — the backlog grows and every queued frame holds its
/// pixel buffer alive. Over Bluetooth that is not hypothetical, since a link hiccup delivers a
/// burst the moment it recovers.
///
/// Only the newest frame is worth drawing, so this holds exactly one and schedules exactly one
/// hop. A preview that skips frames is correct; one that freezes is not.
final class PreviewFrameGate: @unchecked Sendable {

  private struct State {
    var pending: UIImage?
    var isHopScheduled = false
    var dropped: UInt64 = 0
  }

  private let lock = NSLock()
  private var state = State()

  /// Frames replaced before they could be drawn — the queue that never formed.
  var dropped: UInt64 {
    lock.lock(); defer { lock.unlock() }
    return state.dropped
  }

  /// Offers a frame. True means the caller should schedule a main-actor hop, because this is
  /// the only one in flight.
  func offer(_ image: UIImage) -> Bool {
    lock.lock(); defer { lock.unlock() }
    if state.pending != nil { state.dropped &+= 1 }
    state.pending = image
    guard !state.isHopScheduled else { return false }
    state.isHopScheduled = true
    return true
  }

  /// Takes the newest frame and clears the scheduled flag.
  func take() -> UIImage? {
    lock.lock(); defer { lock.unlock() }
    state.isHopScheduled = false
    let image = state.pending
    state.pending = nil
    return image
  }

  func reset() {
    lock.lock(); defer { lock.unlock() }
    state = State()
  }
}
