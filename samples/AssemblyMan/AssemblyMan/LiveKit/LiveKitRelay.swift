/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// LiveKitRelay.swift
//
// Relays the glasses camera into a LiveKit room, so a remote viewer or an assistant can see
// what the operator sees and talk back.
//
// The phone is the relay, not the source: video comes from the glasses by way of the DAT SDK
// and is handed to WebRTC through a buffer capturer (see LiveKitFrameSink). The phone's own
// camera is never published. The microphone is, because the assistant is speech-to-speech.
//
// LiveKit's `Room` is a Combine `ObservableObject` with no published properties, so it cannot
// drive SwiftUI here. Its state is mirrored into this object's observable properties from the
// room delegate instead.
//

import AVFAudio
import Foundation
import LiveKit
import Observation
import os

@Observable
@MainActor
final class LiveKitRelay {

  // MARK: - Observable state

  enum Status: Equatable {
    case idle
    case unconfigured
    case connecting
    /// In the room, video not yet published.
    case connected
    /// Publishing the glasses feed.
    case live
    case reconnecting
    case failed(String)
  }

  private(set) var status: Status = .idle
  private(set) var roomCode: RoomCode?
  private(set) var isVideoPublishing = false
  private(set) var isMicrophoneEnabled = false
  /// Why the microphone is not live, when it should be.
  ///
  /// Worth surfacing rather than logging: a relay with no audio still looks entirely healthy
  /// — video flows, the assistant greets the operator — and the only symptom is that talking
  /// to it does nothing.
  private(set) var microphoneIssue: String?
  private(set) var remoteParticipantCount = 0
  private(set) var isAgentPresent = false
  private(set) var diagnostics: LiveKitFrameSink.Diagnostics = .empty

  var isConfigured: Bool { configuration.isConfigured }

  /// What the ready screen's RELAY row shows.
  var relayLabel: String {
    switch status {
    // The setting decides whether the relay is off. An idle relay with the setting enabled
    // is ready for the next stream, not off.
    case .idle: return isConfigured ? "Ready" : "Not configured"
    case .unconfigured: return "Not configured"
    case .connecting: return "Connecting…"
    case .connected: return "Connected"
    case .live: return roomCode?.display ?? "Live"
    case .reconnecting: return "Reconnecting…"
    case .failed: return "Failed"
    }
  }

  // MARK: - Collaborators

  /// Handed to the frame producer once and captured by its listener closure. A `let`, so the
  /// same instance survives a stream restart and the LiveKit side never sees the gap.
  @ObservationIgnored let frameSink = LiveKitFrameSink()

  @ObservationIgnored private let configuration: LiveKitConfiguration
  @ObservationIgnored private let minter: LiveKitTokenMinter
  @ObservationIgnored private let log = Logger(subsystem: "com.alcorlabs.assemblyman", category: "relay")

  @ObservationIgnored private var room: Room?
  @ObservationIgnored private var videoTrack: LocalVideoTrack?
  @ObservationIgnored private var videoPublication: LocalTrackPublication?
  /// `Room.delegates` holds its delegates weakly, so this strong reference is load-bearing:
  /// without it the proxy is deallocated before the first callback and the UI sits at
  /// "connecting" forever while the relay is in fact working.
  @ObservationIgnored private var delegateProxy: RoomDelegateProxy?
  @ObservationIgnored private var lifecycleTask: Task<Void, Never>?
  /// Invalidates callbacks and async completions from an earlier start/stop cycle.
  @ObservationIgnored private var lifecycleID: UInt64 = 0

  init(configuration: LiveKitConfiguration = .fromBundle()) {
    self.configuration = configuration
    self.minter = LiveKitTokenMinter(
      apiKey: configuration.apiKey,
      apiSecret: configuration.apiSecret
    )
  }

  /// Reports a lifecycle step.
  ///
  /// Goes to stdout as well as the unified log because `os_log` is not reachable over
  /// `devicectl --console`, and a relay that connects and then quietly leaves is only
  /// diagnosable from a device you are holding.
  private func note(_ message: String) {
    log.notice("\(message, privacy: .public)")
    #if DEBUG
    print("[relay] \(message)")
    #endif
  }

  // MARK: - API

  /// Connects and starts publishing. Idempotent — calling it again while already active is a
  /// no-op, which is what makes a stream restart invisible to the room.
  func start(agent: AppSettings.Agent) {
    guard configuration.isConfigured else {
      status = .unconfigured
      note("not configured — add Config/LiveKit.local.xcconfig")
      return
    }
    switch status {
    case .connecting, .connected, .live, .reconnecting:
      note("start ignored, already \(status)")
      return
    case .idle, .unconfigured, .failed:
      break
    }

    let code = roomCode ?? RoomCode.random()
    roomCode = code
    status = .connecting

    lifecycleTask?.cancel()
    lifecycleID &+= 1
    let lifecycleID = lifecycleID
    lifecycleTask = Task { [weak self] in
      await self?.connectAndPublish(code: code, agent: agent, lifecycleID: lifecycleID)
    }
  }

  /// Full teardown. Clears the room code, so the next session gets a fresh one.
  func stop() {
    note("stop() — status was \(status), room \(roomCode?.display ?? "none")")
    lifecycleID &+= 1
    lifecycleTask?.cancel()
    lifecycleTask = nil
    frameSink.disarm()

    let room = self.room
    let publication = self.videoPublication
    self.room = nil
    self.videoTrack = nil
    self.videoPublication = nil
    self.delegateProxy = nil

    status = .idle
    roomCode = nil
    isVideoPublishing = false
    isMicrophoneEnabled = false
    remoteParticipantCount = 0
    isAgentPresent = false

    guard let room else { return }
    Task {
      if let publication {
        try? await room.localParticipant.unpublish(publication: publication)
      }
      await room.disconnect()
    }
  }

  func setMicrophone(enabled: Bool) {
    guard let room, status == .live || status == .connected else { return }
    Task { [weak self] in
      do {
        _ = try await room.localParticipant.setMicrophone(enabled: enabled)
        await MainActor.run { self?.isMicrophoneEnabled = enabled }
      } catch {
        self?.log.error("Microphone toggle failed: \(error.localizedDescription)")
      }
    }
  }

  // MARK: - Connect / publish

  private func connectAndPublish(
    code: RoomCode,
    agent: AppSettings.Agent,
    lifecycleID: UInt64
  ) async {
    var connectingRoom: Room?
    var publishedVideo: LocalTrackPublication?

    do {
      let metadata = RelayMetadata(agent: agent.rawValue, agentName: agent.name).jsonString
      let token = try minter.mint(
        LiveKitTokenMinter.Grant(
          roomName: code.roomName,
          // Stable rather than random: if a previous connection lingers after an abrupt
          // teardown, LiveKit evicts it instead of leaving a ghost in the room.
          identity: "phone-\(code.raw)",
          displayName: "Operator",
          metadata: metadata
        )
      )

      let room = Room(
        delegate: nil,
        connectOptions: ConnectOptions(
          // The assistant replies with audio, so the operator has to be subscribed to hear it.
          autoSubscribe: true,
          // The mic is enabled explicitly after connecting, not as part of the handshake.
          enableMicrophone: false
        ),
        roomOptions: RoomOptions(
          adaptiveStream: false,
          // Lets the server stop the encoder when nobody is watching.
          dynacast: true,
          // Keep the capturer's resolved dimensions when a publication goes away.
          stopLocalTrackOnUnpublish: false,
          // Defaults to true and suspends any track whose source is `.camera`. Our source is
          // the glasses, and DAT keeps delivering in the background, so suspending would
          // freeze the relay the moment the app leaves the foreground.
          suspendLocalVideoTracksInBackground: false
        )
      )
      connectingRoom = room

      let proxy = RoomDelegateProxy { [weak self] event in
        Task { @MainActor in self?.apply(event, lifecycleID: lifecycleID) }
      }
      room.delegates.add(delegate: proxy)

      self.room = room
      self.delegateProxy = proxy

      // Source `.camera` so viewers and the agent treat it as the primary video feed.
      let track = LocalVideoTrack.createBufferTrack(
        name: "glasses",
        source: .camera,
        options: BufferCaptureOptions(),
        reportStatistics: true
      )
      self.videoTrack = track

      // Arm before connecting: frames then flow into the capturer during the websocket and
      // ICE handshake, so the dimensions publish() waits on are already resolved by the time
      // we get there.
      if let capturer = track.capturer as? BufferCapturer {
        frameSink.arm(with: capturer)
      } else {
        throw RelayError.capturerUnavailable
      }

      note("connecting to \(configuration.serverURL) room \(code.display)")
      try await room.connect(url: configuration.serverURL, token: token)
      try Task.checkCancellation()
      guard lifecycleID == self.lifecycleID else { throw CancellationError() }
      status = .connected
      note("connected — waiting for the first glasses frame")

      try await waitForFirstFrame()
      try Task.checkCancellation()
      guard lifecycleID == self.lifecycleID else { throw CancellationError() }
      note("first frame forwarded (\(frameSink.diagnostics.pixelFormatDescription)) — publishing")

      let publication = try await room.localParticipant.publish(
        videoTrack: track,
        options: VideoPublishOptions(
          simulcast: true,
          // Prefer a smooth feed over a sharp one — this is someone's hands at work.
          degradationPreference: .maintainFramerate,
          streamName: "glasses"
        )
      )
      publishedVideo = publication
      try Task.checkCancellation()
      guard lifecycleID == self.lifecycleID else { throw CancellationError() }

      videoPublication = publication
      isVideoPublishing = true
      status = .live
      diagnostics = frameSink.diagnostics

      // Ask before publishing. Without permission iOS hands the capturer a silent input and
      // the track publishes happily, so the failure is invisible from here — the operator
      // hears the assistant greet them and then talks to something that cannot hear.
      let hasMicrophonePermission = await Self.requestMicrophonePermission()
      if !hasMicrophonePermission {
        isMicrophoneEnabled = false
        microphoneIssue = "Microphone access is off. Enable it in Settings › AssemblyMan."
        note("microphone permission denied — the assistant will not hear the operator")
      } else {
        do {
          _ = try await room.localParticipant.setMicrophone(enabled: true)
          try Task.checkCancellation()
          guard lifecycleID == self.lifecycleID else { throw CancellationError() }
          isMicrophoneEnabled = true
          microphoneIssue = nil
          note("microphone live — \(Self.audioRouteDescription())")
        } catch is CancellationError {
          throw CancellationError()
        } catch {
          isMicrophoneEnabled = false
          microphoneIssue = describe(error)
          note("video is live, but the microphone could not start: \(describe(error))")
        }
      }

      note("LIVE in room \(code.display)")
    } catch is CancellationError {
      note("cancelled during setup — a stop() or restart raced this")
      if let connectingRoom {
        if let publishedVideo {
          try? await connectingRoom.localParticipant.unpublish(publication: publishedVideo)
        }
        await connectingRoom.disconnect()
      }
    } catch {
      // An old task may fail after stop() has already started a replacement. It owns only
      // its local room; it must not disarm or relabel the replacement.
      guard lifecycleID == self.lifecycleID else {
        if let connectingRoom { await connectingRoom.disconnect() }
        return
      }

      frameSink.disarm()
      diagnostics = frameSink.diagnostics
      status = .failed(describe(error))
      isVideoPublishing = false
      isMicrophoneEnabled = false
      if self.room === connectingRoom {
        self.room = nil
        videoTrack = nil
        videoPublication = nil
        delegateProxy = nil
      }
      note(
        "FAILED: \(describe(error)) "
          + "[offered=\(diagnostics.framesOffered) forwarded=\(diagnostics.framesForwarded) "
          + "format=\(diagnostics.pixelFormatDescription)]"
      )
      if let connectingRoom { await connectingRoom.disconnect() }
    }
  }

  /// Waits until the sink has actually handed WebRTC a frame.
  ///
  /// `publish()` would wait for dimensions on its own, but only for ten seconds and then with
  /// an error that names nothing useful. Failing here instead lets us say whether no frames
  /// arrived at all or whether they arrived in a pixel format WebRTC cannot encode — which is
  /// the difference between a glasses problem and a format problem.
  private func waitForFirstFrame(timeout: TimeInterval = 8) async throws {
    let deadline = Date().addingTimeInterval(timeout)
    while !frameSink.hasForwardedFrame {
      if Date() >= deadline {
        let diagnostics = frameSink.diagnostics
        self.diagnostics = diagnostics
        throw RelayError.noFrames(diagnostics)
      }
      try await Task.sleep(nanoseconds: 50_000_000)
    }
    diagnostics = frameSink.diagnostics
  }

  // MARK: - Room events

  private func apply(_ event: RelayRoomEvent, lifecycleID: UInt64) {
    guard lifecycleID == self.lifecycleID else { return }

    switch event {
    case .reconnecting:
      status = .reconnecting
    case .reconnected:
      status = isVideoPublishing ? .live : .connected
    case let .didDisconnect(reason):
      note("room disconnected (\(reason ?? "no error")) while status was \(status)")
      // A deliberate stop() has already moved us to .idle; only report unexpected drops.
      if status != .idle {
        status = reason.map { .failed($0) } ?? .idle
        isVideoPublishing = false
      }
    case let .didFailToConnect(reason):
      note("failed to connect: \(reason ?? "unknown")")
      status = .failed(reason ?? "Could not reach the LiveKit server.")
    case let .participants(count, hasAgent):
      remoteParticipantCount = count
      isAgentPresent = hasAgent
    }
  }

  /// Grants, or asks once, for microphone access.
  ///
  /// The relay is the app's only user of the microphone, so nothing else prompts. iOS returns
  /// a silent input rather than an error when access is missing, which is why this is checked
  /// explicitly instead of relying on the publish to fail.
  private static func requestMicrophonePermission() async -> Bool {
    switch AVAudioApplication.shared.recordPermission {
    case .granted:
      return true
    case .denied:
      return false
    case .undetermined:
      return await AVAudioApplication.requestRecordPermission()
    @unknown default:
      return false
    }
  }

  /// What the audio session is actually doing, for the log.
  ///
  /// An empty input list is the signature of the silent-microphone failure: the track
  /// publishes, the level stays flat, and nothing else says why.
  private static func audioRouteDescription() -> String {
    let session = AVAudioSession.sharedInstance()
    let inputs = session.currentRoute.inputs.map(\.portType.rawValue)
    let outputs = session.currentRoute.outputs.map(\.portType.rawValue)
    return "category=\(session.category.rawValue) mode=\(session.mode.rawValue) "
      + "in=[\(inputs.joined(separator: ","))] out=[\(outputs.joined(separator: ","))]"
  }

  private func describe(_ error: Error) -> String {
    if let relayError = error as? RelayError { return relayError.localizedDescription }
    if let mintError = error as? LiveKitTokenMinter.MintError {
      return mintError.errorDescription ?? "\(mintError)"
    }
    return error.localizedDescription
  }
}

// MARK: - Errors

enum RelayError: Error, LocalizedError {
  case capturerUnavailable
  case noFrames(LiveKitFrameSink.Diagnostics)

  var errorDescription: String? {
    switch self {
    case .capturerUnavailable:
      return "Could not create the video capturer."
    case let .noFrames(diagnostics):
      if diagnostics.framesOffered == 0 {
        return "No frames arrived from the glasses."
      }
      if diagnostics.isPixelFormatSupported == false {
        return "Glasses deliver \(diagnostics.pixelFormatDescription), which WebRTC cannot encode."
      }
      return "Frames arrived but none reached the encoder."
    }
  }
}

// MARK: - Delegate bridge

/// What the relay needs from the room, reduced to values that can cross an isolation boundary.
enum RelayRoomEvent: Sendable {
  case reconnecting
  case reconnected
  case didDisconnect(String?)
  case didFailToConnect(String?)
  case participants(count: Int, hasAgent: Bool)
}

/// Bridges room callbacks — which arrive on the SDK's own threads — onto the main actor.
///
/// This exists rather than conforming `LiveKitRelay` to `RoomDelegate` directly because the
/// relay is `@MainActor` and every callback would otherwise need decorating, and because
/// passing SDK objects across the hop would drag non-Sendable types with them.
final class RoomDelegateProxy: NSObject, RoomDelegate, Sendable {

  private let onEvent: @Sendable (RelayRoomEvent) -> Void

  init(onEvent: @escaping @Sendable (RelayRoomEvent) -> Void) {
    self.onEvent = onEvent
  }

  func roomIsReconnecting(_ room: Room) {
    onEvent(.reconnecting)
  }

  func roomDidReconnect(_ room: Room) {
    onEvent(.reconnected)
  }

  func room(_ room: Room, didDisconnectWithError error: LiveKitError?) {
    onEvent(.didDisconnect(error?.localizedDescription))
  }

  func room(_ room: Room, didFailToConnectWithError error: LiveKitError?) {
    onEvent(.didFailToConnect(error?.localizedDescription))
  }

  func room(_ room: Room, participantDidConnect participant: RemoteParticipant) {
    report(room)
  }

  func room(_ room: Room, participantDidDisconnect participant: RemoteParticipant) {
    report(room)
  }

  private func report(_ room: Room) {
    onEvent(.participants(
      count: room.remoteParticipants.count,
      hasAgent: !room.agentParticipants.isEmpty
    ))
  }
}
