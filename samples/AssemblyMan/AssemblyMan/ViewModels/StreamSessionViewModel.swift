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

    videoFrameListenerToken = stream.videoFramePublisher.listen { [weak self] frame in
      // The relay is fed on the delivery thread, before the hop: handing the buffer to
      // WebRTC is a non-blocking enqueue, and going by way of the main actor would put a
      // 30-per-second workload behind SwiftUI layout with no ordering guarantee.
      sink.capture(frame.sampleBuffer)

      // The on-device preview still needs the main actor.
      Task { @MainActor in self?.handleVideoFrame(frame) }
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
      }
    }
  }

  private func stopElapsedClock() {
    elapsedTask?.cancel()
    elapsedTask = nil
  }

  private func handleVideoFrame(_ frame: VideoFrame) {
    if let image = frame.makeUIImage() {
      currentVideoFrame = image
      if !hasReceivedFirstFrame {
        hasReceivedFirstFrame = true
      }
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

}
