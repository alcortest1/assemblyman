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

  // MARK: Photo assessment
  //
  // A still can be taken for two reasons, and they must not be confused. The operator pressing
  // capture wants to see the photo; the agent asking for one wants it graded and the operator
  // is looking at their work, not the phone. Same shutter, different destination — so the
  // pending request records which it is, and the preview only opens for the first.

  /// The verdict to show, or the grade currently running.
  var currentGrade: GradeProtocol.Grade?
  var showGradeSheet: Bool = false
  /// True from the moment a grade is asked for until the verdict lands, including the shutter.
  var isGrading: Bool = false
  /// A photo the operator took and may choose to have graded.
  var photoAwaitingGrade: Data?
  var showSubtaskPicker: Bool = false

  /// What this room can grade against. Empty until the agent publishes it.
  var gradeCatalogue: [GradeProtocol.Catalogue.Task] { relay.catalogue }
  var canRequestGrade: Bool { !relay.catalogue.isEmpty && streamingStatus == .streaming }

  /// Who asked for the still in flight, and what to do with it when it arrives.
  private enum PhotoPurpose {
    case preview
    case grading(CheckedContinuation<Data?, Never>)
  }

  @ObservationIgnored private var photoPurpose: PhotoPurpose = .preview
  /// Fails the waiting continuation rather than leaving the agent hanging on a shutter that
  /// never fires — a capture that silently never returns is the one failure the operator
  /// cannot diagnose from where they are standing.
  @ObservationIgnored private var captureTimeout: Task<Void, Never>?

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

  // MARK: Delivered-stream measurement
  //
  // A stream configuration is a request, not a promise: the SDK runs its own ladder and drops
  // resolution and then frame rate when the link cannot carry what was asked for, silently and
  // without a state change. So "the glasses are dropping frames" has three quite different
  // causes — nothing arriving, arriving smaller than requested, or arriving slower — and they
  // are indistinguishable from the picture alone. These record what actually turned up.

  /// Pixel dimensions of the most recent frame, which is the SDK's answer to the resolution
  /// that was requested. Smaller than the requested tier means the ladder stepped down.
  private(set) var deliveredFrameSize: CGSize?
  /// Frames drawn in the last whole second.
  private(set) var deliveredFramesPerSecond: Int = 0
  @ObservationIgnored private var framesThisSecond: Int = 0

  /// Requested versus delivered, for the diagnostics line: `720x1280@30 → 504x896@17`.
  var deliveredSpec: String {
    let wanted = "\(settings.quality.dimensionsLabel)@\(settings.frameRate.rawValue)"
    guard let size = deliveredFrameSize else { return "\(wanted) → nothing yet" }
    let got = "\(Int(size.width))x\(Int(size.height))@\(deliveredFramesPerSecond)"
    return wanted == got ? wanted : "\(wanted) → \(got)"
  }

  /// True when the SDK is sending a smaller frame than the tier asked for — the signature of a
  /// link that cannot carry the requested quality.
  var isDownscaled: Bool {
    guard let size = deliveredFrameSize else { return false }
    let requested = settings.quality.frameSize
    return Int(size.width) * Int(size.height)
      < Int(requested.width) * Int(requested.height)
  }

  /// True once the gap is long enough that this is a stall rather than a slow frame.
  ///
  /// Reported and nothing more. Nothing here rebuilds the stream on the operator's behalf: a
  /// restart costs seconds of black picture, and three of them in a row cost more than the
  /// stall they were answering. A session that stops delivering stays stopped and says so, and
  /// the operator decides whether to start another.
  var isFeedStalled: Bool { secondsSinceLastFrame >= 3 }
  /// Rebuilding the stream underneath a session that is still, from the operator's point of
  /// view, running — which now happens only when they change a setting that is baked into the
  /// stream at creation. Drives an indicator rather than an alert.
  private(set) var isReconnecting = false
  @ObservationIgnored private var lastFrameAt: Date?
  /// Coalesces preview frames so the main actor never accumulates a backlog.
  @ObservationIgnored private let previewGate = PreviewFrameGate()
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
    wireGrading()
  }

  /// Connects the relay's two grading edges to this view model: the agent asking for a
  /// photograph, and a verdict coming back.
  private func wireGrading() {
    relay.onCaptureRequest = { [weak self] in
      guard let self else { return nil }
      return await self.capturePhotoForGrading()
    }
    relay.onGrade = { [weak self] grade in
      guard let self else { return }
      self.currentGrade = grade
      self.isGrading = grade.isRunning
      // Opens on the in-progress message, so the seconds the model spends thinking are
      // visible as work rather than as nothing having happened.
      self.showGradeSheet = true
    }
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
    deliveredFrameSize = nil
    deliveredFramesPerSecond = 0
    framesThisSecond = 0
    // The next session starts from the operator's choice again: the link it will run over is
    // not the one this session learned about.
    previewGate.reset()
    // A grade waiting on a shutter that will now never fire has to be released, or the agent
    // sits on the RPC until its own timeout with no idea the session ended underneath it.
    captureTimeout?.cancel()
    captureTimeout = nil
    failPendingCapture()
    currentGrade = nil
    showGradeSheet = false
    showSubtaskPicker = false
    photoAwaitingGrade = nil
    isGrading = false
    resetSegmentation()
    sessionManager.cleanup()
  }

  func capturePhoto() {
    guard !isCapturingPhoto, streamingStatus == .streaming else {
      showPhotoCaptureError = true
      return
    }
    photoPurpose = .preview
    isCapturingPhoto = true
    let success = stream?.capturePhoto(format: .jpeg) ?? false
    if !success {
      isCapturingPhoto = false
      showPhotoCaptureError = true
    }
  }

  // MARK: - Photo assessment

  /// Takes a still for the agent to grade. Nil when the shutter cannot fire or does not
  /// return; the agent then grades the relayed video frame instead.
  ///
  /// The DAT SDK delivers a photo through a listener rather than returning it, so the
  /// continuation resumed in `handlePhotoData` is what turns that into something awaitable.
  func capturePhotoForGrading() async -> Data? {
    guard streamingStatus == .streaming, !isCapturingPhoto else { return nil }

    isGrading = true
    return await withCheckedContinuation { (continuation: CheckedContinuation<Data?, Never>) in
      photoPurpose = .grading(continuation)
      isCapturingPhoto = true

      guard stream?.capturePhoto(format: .jpeg) == true else {
        isCapturingPhoto = false
        isGrading = false
        photoPurpose = .preview
        continuation.resume(returning: nil)
        return
      }

      captureTimeout?.cancel()
      captureTimeout = Task { [weak self] in
        try? await Task.sleep(for: .seconds(12))
        guard !Task.isCancelled else { return }
        await MainActor.run { self?.failPendingCapture() }
      }
    }
  }

  private func failPendingCapture() {
    guard case .grading(let continuation) = photoPurpose else { return }
    photoPurpose = .preview
    isCapturingPhoto = false
    isGrading = false
    continuation.resume(returning: nil)
  }

  /// Offers the photo the operator just took for grading. They pick the subtask; the agent
  /// owns the rubrics, so the phone can only name one it was told about.
  func offerCapturedPhotoForGrading() {
    guard let photo = capturedPhoto, let jpeg = photo.jpegData(compressionQuality: 0.92) else {
      return
    }
    photoAwaitingGrade = jpeg
    showPhotoPreview = false
    showSubtaskPicker = true
  }

  func gradeAwaitingPhoto(taskCode: String, subtaskCode: String) {
    guard let jpeg = photoAwaitingGrade else { return }
    showSubtaskPicker = false
    photoAwaitingGrade = nil
    isGrading = true
    Task { [relay] in
      await relay.sendForGrading(jpeg, taskCode: taskCode, subtaskCode: subtaskCode)
    }
  }

  func cancelSubtaskPicker() {
    showSubtaskPicker = false
    photoAwaitingGrade = nil
  }

  func dismissGradeSheet() {
    showGradeSheet = false
    currentGrade = nil
    isGrading = false
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

    let config = activeStreamConfiguration()

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

  /// The configuration to open the next stream with: exactly what the operator asked for.
  ///
  /// Nothing steps it down any more. The SDK runs its own adaptive ladder — lowering resolution
  /// first and frame rate second — and a second ladder here was competing with it rather than
  /// helping: the app would ask for less, the SDK would adapt to that, and the two would settle
  /// somewhere neither had chosen. One place decides, and it is the one holding the link.
  ///
  /// If the delivered frame is persistently smaller than the tier, that is worth knowing and
  /// the diagnostics line says so — but the answer is for the operator to pick a lower tier in
  /// Settings, where it will stick and be visible, not for the app to quietly do it.
  private func activeStreamConfiguration() -> StreamConfiguration {
    StreamConfiguration(
      // Raw, not `.hvc1`, and this is not a bandwidth preference — it is what the pipeline
      // can actually consume. An HEVC sample buffer carries a CMBlockBuffer of encoded
      // bytes and no CVImageBuffer, and both consumers of a frame need pixels:
      // `makeUIImage()` returns nil, so the on-screen preview never updates and the stall
      // banner reads "no frames"; and WebRTC's BufferCapturer drops every buffer, so the
      // track's dimensions never resolve and `publish()` times out. The symptom is a
      // session that looks connected and shows nothing, with frames visibly being offered —
      // 225 offered, 225 forwarded, pixel format never determined.
      //
      // The reasons `.hvc1` was chosen are real: raw is the worst case for a
      // bandwidth-limited link, and per the 0.5.0 changelog raw also pauses streaming when
      // the app is backgrounded. Getting them back means decoding HEVC to pixel buffers
      // ourselves — a VTDecompressionSession between the DAT publisher and both consumers —
      // not simply asking for the codec again.
      videoCodec: VideoCodec.raw,
      resolution: settings.quality.streamingResolution,
      frameRate: UInt(settings.frameRate.rawValue)
    )
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
      stream = nil
      clearListeners()
      sessionManager.stopCurrentSession()

      if wantsRestart {
        // A rebuild is not the end of the session, so almost none of the teardown below
        // applies. Holding the status at `.waiting` keeps `isStreaming` true, which keeps
        // `StreamSessionView` on the live screen — dropping to `.stopped` swapped in the
        // Ready screen mid-session. The last frame stays up and the clock keeps running for
        // the same reason: the stream restarted, the session did not.
        //
        // The relay is deliberately left alone: the room code stays valid and anyone watching
        // keeps their connection, seeing only a brief freeze. Tearing it down here would mint
        // a new code and drop every viewer on each rebuild.
        wantsRestart = false
        streamingStatus = .waiting
        isReconnecting = true
        Task { await handleStartStreaming() }
      } else {
        currentVideoFrame = nil
        streamingStatus = .stopped
        hasReceivedFirstFrame = false
        isReconnecting = false
        stopElapsedClock()
        resetSegmentation()
        relay.stop()
      }
    case .waitingForDevice, .starting, .stopping, .paused:
      streamingStatus = .waiting
    case .streaming:
      streamingStatus = .streaming
      isReconnecting = false
      // Seeded here rather than on the first frame. `checkFeedLiveness` bails on a nil
      // `lastFrameAt`, so a stream that reached `.streaming` and then delivered nothing at all
      // was invisible to the watchdog — the one case where an operator waits longest.
      if lastFrameAt == nil { lastFrameAt = Date() }
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
        self?.sampleDeliveredFrameRate()
        self?.checkFeedLiveness()
      }
    }
  }

  /// Closes off the last second's frame count. Driven by the session clock rather than its own
  /// timer, so it costs nothing and cannot outlive the session.
  private func sampleDeliveredFrameRate() {
    deliveredFramesPerSecond = framesThisSecond
    framesThisSecond = 0
    #if DEBUG
    // Logged as well as shown, because the interesting runs are the ones being watched over a
    // cable rather than over the operator's shoulder.
    if isStreaming {
      let sink = relay.frameSink.diagnostics
      print(
        "[feed] \(deliveredSpec)"
          + (isDownscaled ? " DOWNSCALED" : "")
          + " preview-dropped=\(previewGate.dropped)"
          + " relay=\(sink.framesForwarded)/\(sink.framesOffered)"
          + (sink.framesSkippedComposite > 0 ? " skipped=\(sink.framesSkippedComposite)" : "")
          + (secondsSinceLastFrame > 0 ? " gap=\(secondsSinceLastFrame)s" : "")
      )
    }
    #endif
  }

  private func stopElapsedClock() {
    elapsedTask?.cancel()
    elapsedTask = nil
  }

  private func drainPreviewGate() {
    guard let image = previewGate.take() else { return }
    lastFrameAt = Date()
    if secondsSinceLastFrame != 0 { secondsSinceLastFrame = 0 }

    currentVideoFrame = image
    if !hasReceivedFirstFrame {
      hasReceivedFirstFrame = true
    }
    // In pixels, not points: a UIImage built from a pixel buffer carries the frame's real
    // dimensions only once its scale is applied, and comparing points against the SDK's pixel
    // tiers would report a downscale on every device with a retina screen.
    deliveredFrameSize = CGSize(
      width: image.size.width * image.scale,
      height: image.size.height * image.scale
    )
    framesThisSecond += 1
    scheduleSegmentation(for: image)
  }

  /// Measures how long the glasses have been silent, and reports it.
  ///
  /// A stalled feed is otherwise invisible: the SDK reports no error and no state change, the
  /// last frame stays on screen, and the session looks live. Over Bluetooth it happens for
  /// ordinary reasons — the link saturates, the frames warm up and throttle.
  ///
  /// It used to answer that by rebuilding the stream, up to three times. That is gone. Every
  /// rebuild costs seconds of black picture and a fresh negotiation with the glasses, so a
  /// link that is struggling got a burst of restarts on top of whatever was already wrong; the
  /// cure was reliably worse than the stall, and the loop it could fall into needed two further
  /// mechanisms — a healthy-streak timer and a handshake deadline — to contain it. All three
  /// are now unnecessary rather than tuned. The gap is shown, and starting another session is
  /// the operator's call.
  private func checkFeedLiveness() {
    guard streamingStatus == .streaming, let lastFrameAt else { return }
    secondsSinceLastFrame = Int(Date().timeIntervalSince(lastFrameAt))
  }

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
    captureTimeout?.cancel()
    captureTimeout = nil

    let purpose = photoPurpose
    photoPurpose = .preview

    switch purpose {
    case .grading(let continuation):
      // Straight back to the agent. No preview: the operator is looking at the work they
      // just asked about, and a full-screen photo of it is in the way.
      continuation.resume(returning: data.data)
    case .preview:
      if let image = UIImage(data: data.data) {
        capturedPhoto = image
        showPhotoPreview = true
      }
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
