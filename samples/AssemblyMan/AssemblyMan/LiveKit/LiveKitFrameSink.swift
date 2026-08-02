/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// LiveKitFrameSink.swift
//
// The hot path: glasses frames in, WebRTC out.
//
// This is the one object in the relay that is deliberately not `@MainActor`. Frames arrive on
// the DAT SDK's delivery thread, and `BufferCapturer.capture` only hands the buffer to WebRTC's
// own processing queue before returning — so forwarding here costs microseconds and belongs on
// the thread the frame arrived on. Routing 30 frames a second through the main actor instead
// would queue them behind SwiftUI layout and, because `Task { @MainActor }` makes no ordering
// promise, could deliver them out of order.
//
// All mutable state lives inside the lock, which is what makes the type `Sendable` outright
// rather than `@unchecked Sendable`.
//

import CoreMedia
import CoreVideo
import Foundation
import LiveKit
import os

final class LiveKitFrameSink: Sendable {

  /// What the sink saw. Surfaced in the debug menu, and the basis for diagnosing the
  /// pixel-format failure below.
  struct Diagnostics: Equatable, Sendable {
    var framesOffered: UInt64 = 0
    var framesForwarded: UInt64 = 0
    var framesComposited: UInt64 = 0
    var firstPixelFormat: OSType?
    var isPixelFormatSupported: Bool?

    static let empty = Diagnostics()

    /// The four-character code Core Video uses, e.g. "420v", "420f", "BGRA".
    var pixelFormatDescription: String {
      guard let format = firstPixelFormat else { return "—" }
      let bytes = [
        UInt8((format >> 24) & 0xFF), UInt8((format >> 16) & 0xFF),
        UInt8((format >> 8) & 0xFF), UInt8(format & 0xFF),
      ]
      let text = String(bytes: bytes, encoding: .ascii) ?? ""
      return text.allSatisfy { $0.isASCII && !$0.isNewline } ? text : "0x\(String(format, radix: 16))"
    }
  }

  private struct State {
    var capturer: BufferCapturer?
    var isArmed = false
    var diagnostics = Diagnostics()
  }

  private let state = OSAllocatedUnfairLock(initialState: State())
  private let onFirstFrame: @Sendable () -> Void

  /// Burns the on-device vision overlay into the published frames. Idle — and free — until
  /// an overlay is set.
  let compositor = RelayFrameCompositor()

  init(onFirstFrame: @escaping @Sendable () -> Void = {}) {
    self.onFirstFrame = onFirstFrame
    // `supportedPixelFormats` is a lazily-initialised static whose initialiser does a
    // `DispatchQueue.liveKitWebRTC.sync`. Touch it here, on a quiet thread at construction,
    // so that blocking hop never lands on the main actor or on a frame delivery thread.
    _ = VideoCapturer.supportedPixelFormats
  }

  var diagnostics: Diagnostics { state.withLock { $0.diagnostics } }

  var hasForwardedFrame: Bool { state.withLock { $0.diagnostics.framesForwarded > 0 } }

  /// Starts forwarding into `capturer`. Called from the relay on the main actor.
  func arm(with capturer: BufferCapturer) {
    state.withLock {
      $0.capturer = capturer
      $0.isArmed = true
      // Counters describe the current publication, so they reset with it.
      $0.diagnostics = Diagnostics()
    }
  }

  func disarm() {
    state.withLock {
      $0.capturer = nil
      $0.isArmed = false
    }
  }

  /// Forwards one glasses frame to WebRTC. Runs on the DAT SDK's frame delivery thread.
  func capture(_ sampleBuffer: CMSampleBuffer) {
    let (capturer, isFirstForwarded) = state.withLock { state -> (BufferCapturer?, Bool) in
      state.diagnostics.framesOffered &+= 1
      guard state.isArmed, let capturer = state.capturer else { return (nil, false) }

      if state.diagnostics.firstPixelFormat == nil,
        let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) {
        let format = CVPixelBufferGetPixelFormatType(pixelBuffer)
        state.diagnostics.firstPixelFormat = format
        // WebRTC silently drops anything outside this set, logging only at warning level.
        // Recording it here is what turns that into a diagnosable failure.
        state.diagnostics.isPixelFormatSupported =
          VideoCapturer.supportedPixelFormats.contains { $0.uint32Value == format }
      }

      state.diagnostics.framesForwarded &+= 1
      return (capturer, state.diagnostics.framesForwarded == 1)
    }

    guard let capturer else { return }

    // Outside the lock: `capture` is non-blocking, but there is no reason to hold a lock
    // across a call into another library.
    //
    // With the on-device overlay showing, the viewer should see it too, so the overlay is
    // composited in before publishing. With no overlay set this whole branch is skipped and
    // the buffer reaches WebRTC exactly as it arrived — the common case stays zero-copy.
    if compositor.isCompositing,
      let source = CMSampleBufferGetImageBuffer(sampleBuffer),
      let composited = compositor.composite(source) {
      // The composite is a fresh buffer, so the presentation time has to be carried across
      // by hand or WebRTC paces the stream from its own clock and the video stutters.
      let presentationTime = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
      let timeStampNs = presentationTime.isValid
        ? Int64(CMTimeGetSeconds(presentationTime) * Double(NSEC_PER_SEC))
        : VideoCapturer.createTimeStampNs()
      capturer.capture(composited, timeStampNs: timeStampNs, rotation: ._0)
      state.withLock { $0.diagnostics.framesComposited &+= 1 }
    } else {
      capturer.capture(sampleBuffer)
    }

    if isFirstForwarded { onFirstFrame() }
  }
}
