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
    /// Frames published without the overlay because a composite was still running. A steady
    /// climb here means compositing cannot keep up with the frame rate.
    var framesSkippedComposite: UInt64 = 0
    var firstPixelFormat: OSType?
    var isPixelFormatSupported: Bool?
    /// Frames that carried no CVImageBuffer at all — encoded samples rather than pixels.
    ///
    /// Distinct from an unsupported pixel format, and the distinction is the whole
    /// diagnosis: an unsupported format means the glasses sent pixels WebRTC will not take,
    /// while this means they sent no pixels at all, which is what asking the DAT SDK for
    /// `.hvc1` instead of `.raw` does. Both end as a silent black relay; only this counter
    /// tells them apart.
    var framesWithoutPixelBuffer: UInt64 = 0
    /// Media subtype of the first frame's format description, e.g. "hvc1", "420v".
    var firstMediaSubType: OSType?

    static let empty = Diagnostics()

    /// The four-character code Core Video uses, e.g. "420v", "420f", "BGRA".
    var pixelFormatDescription: String {
      Self.fourCC(firstPixelFormat)
    }

    var mediaSubTypeDescription: String {
      Self.fourCC(firstMediaSubType)
    }

    /// Reads a FourCC as text, falling back to hex when it is not printable.
    private static func fourCC(_ code: OSType?) -> String {
      guard let code else { return "—" }
      let bytes = [
        UInt8((code >> 24) & 0xFF), UInt8((code >> 16) & 0xFF),
        UInt8((code >> 8) & 0xFF), UInt8(code & 0xFF),
      ]
      let text = String(bytes: bytes, encoding: .ascii) ?? ""
      return text.allSatisfy { $0.isASCII && !$0.isNewline } ? text : "0x\(String(code, radix: 16))"
    }

    /// Set when frames are arriving but none of them carry pixels — the one failure that
    /// looks identical to a healthy session from the outside.
    var isDeliveringEncodedFrames: Bool {
      framesWithoutPixelBuffer > 0 && firstPixelFormat == nil
    }
  }

  private struct State {
    var capturer: BufferCapturer?
    var isArmed = false
    var diagnostics = Diagnostics()
    /// Set while a composite is in flight. Frames arriving meanwhile are published raw
    /// rather than queued — see `capture`.
    var isCompositingFrame = false
  }

  /// Compositing happens here, never on the thread the frame arrived on.
  private let compositeQueue = DispatchQueue(
    label: "com.alcorlabs.assemblyman.relay.composite",
    qos: .userInitiated
  )

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

      if let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) {
        if state.diagnostics.firstPixelFormat == nil {
          let format = CVPixelBufferGetPixelFormatType(pixelBuffer)
          state.diagnostics.firstPixelFormat = format
          // WebRTC silently drops anything outside this set, logging only at warning level.
          // Recording it here is what turns that into a diagnosable failure.
          state.diagnostics.isPixelFormatSupported =
            VideoCapturer.supportedPixelFormats.contains { $0.uint32Value == format }
        }
      } else {
        // No pixels in this frame — an encoded sample. Counted rather than ignored: the
        // relay's symptom is a black feed either way, and without this the log shows frames
        // being forwarded quite happily to a capturer that is discarding all of them.
        state.diagnostics.framesWithoutPixelBuffer &+= 1
        if state.diagnostics.firstMediaSubType == nil,
          let description = CMSampleBufferGetFormatDescription(sampleBuffer) {
          state.diagnostics.firstMediaSubType = CMFormatDescriptionGetMediaSubType(description)
        }
      }

      state.diagnostics.framesForwarded &+= 1
      return (capturer, state.diagnostics.framesForwarded == 1)
    }

    guard let capturer else { return }

    // Outside the lock: `capture` is non-blocking, but there is no reason to hold a lock
    // across a call into another library.
    //
    // With the on-device overlay showing, the viewer should see it too. With no overlay set
    // this whole branch is skipped and the buffer reaches WebRTC exactly as it arrived — the
    // common case stays zero-copy.
    //
    // Compositing is a synchronous GPU render, and this is the DAT SDK's frame delivery
    // thread. Doing it here blocked that thread roughly thirty times a second while on-device
    // segmentation competed for the same GPU; the camera pipeline backed up, the app froze,
    // and the phone dropped out of the room. So the render is moved off this thread, and a
    // frame arriving while one is still running is published raw rather than queued. Losing
    // an overlay on some frames is invisible; stalling the capture pipeline is not.
    if compositor.isCompositing, let source = CMSampleBufferGetImageBuffer(sampleBuffer) {
      let presentationTime = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
      let timeStampNs = presentationTime.isValid
        ? Int64(CMTimeGetSeconds(presentationTime) * Double(NSEC_PER_SEC))
        : VideoCapturer.createTimeStampNs()

      let shouldComposite = state.withLock { state -> Bool in
        guard !state.isCompositingFrame else {
          state.diagnostics.framesSkippedComposite &+= 1
          return false
        }
        state.isCompositingFrame = true
        return true
      }

      if shouldComposite {
        compositeQueue.async { [weak self] in
          guard let self else { return }
          let composited = self.compositor.composite(source)
          // Publishing from this queue is safe — the capturer hands buffers to WebRTC's own
          // queue — and only one composite runs at a time, so frame order is preserved.
          if let composited {
            capturer.capture(composited, timeStampNs: timeStampNs, rotation: ._0)
            self.state.withLock { $0.diagnostics.framesComposited &+= 1 }
          } else {
            capturer.capture(source, timeStampNs: timeStampNs, rotation: ._0)
          }
          self.state.withLock { $0.isCompositingFrame = false }
        }
      } else {
        capturer.capture(source, timeStampNs: timeStampNs, rotation: ._0)
      }
    } else {
      capturer.capture(sampleBuffer)
    }

    if isFirstForwarded { onFirstFrame() }
  }
}
