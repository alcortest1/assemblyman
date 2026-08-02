/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// LiveKitFrameSinkTests.swift
//
// The sink runs on the frame delivery thread and is the thing that fails silently when the
// glasses hand over a pixel format WebRTC cannot encode. These tests pin the gate (nothing
// leaves a disarmed sink) and the counters the diagnosis depends on.
//

import CoreMedia
import CoreVideo
import XCTest

@testable import AssemblyMan

final class LiveKitFrameSinkTests: XCTestCase {

  func testStartsEmpty() {
    let sink = LiveKitFrameSink()
    XCTAssertEqual(sink.diagnostics, .empty)
    XCTAssertFalse(sink.hasForwardedFrame)
  }

  func testADisarmedSinkCountsFramesButForwardsNone() throws {
    let sink = LiveKitFrameSink()
    let buffer = try makeSampleBuffer()

    for _ in 0..<5 { sink.capture(buffer) }

    // Offered still counts, so "glasses are producing but we are not publishing" is
    // distinguishable from "no frames at all".
    XCTAssertEqual(sink.diagnostics.framesOffered, 5)
    XCTAssertEqual(sink.diagnostics.framesForwarded, 0)
    XCTAssertFalse(sink.hasForwardedFrame)
  }

  func testDisarmingStopsForwardingWithoutLosingTheOfferedCount() throws {
    let sink = LiveKitFrameSink()
    let buffer = try makeSampleBuffer()

    sink.capture(buffer)
    sink.disarm()
    sink.capture(buffer)

    XCTAssertEqual(sink.diagnostics.framesOffered, 2)
    XCTAssertEqual(sink.diagnostics.framesForwarded, 0)
  }

  func testFirstFrameCallbackDoesNotFireWhileDisarmed() throws {
    let didFire = Expectation()
    let sink = LiveKitFrameSink { didFire.fulfill() }

    sink.capture(try makeSampleBuffer())

    XCTAssertFalse(didFire.value, "nothing was published, so nothing should signal a first frame")
  }

  func testCaptureIsSafeFromConcurrentThreads() throws {
    let sink = LiveKitFrameSink()
    let buffer = try makeSampleBuffer()

    // Frames arrive on the SDK's delivery thread while the main actor arms and disarms.
    // The counters must survive that without tripping the thread sanitiser.
    DispatchQueue.concurrentPerform(iterations: 200) { _ in
      sink.capture(buffer)
    }

    XCTAssertEqual(sink.diagnostics.framesOffered, 200)
  }

  func testPixelFormatDescriptionRendersAFourCharacterCode() {
    var diagnostics = LiveKitFrameSink.Diagnostics()
    diagnostics.firstPixelFormat = kCVPixelFormatType_32BGRA
    XCTAssertEqual(diagnostics.pixelFormatDescription, "BGRA")

    diagnostics.firstPixelFormat = kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange
    XCTAssertEqual(diagnostics.pixelFormatDescription, "420v")
  }

  func testPixelFormatDescriptionIsPlaceholderBeforeAnyFrame() {
    XCTAssertEqual(LiveKitFrameSink.Diagnostics().pixelFormatDescription, "—")
  }

  // MARK: - Helpers

  /// A minimal but valid sample buffer, shaped like one the DAT SDK delivers.
  private func makeSampleBuffer(
    width: Int = 64,
    height: Int = 48,
    format: OSType = kCVPixelFormatType_32BGRA
  ) throws -> CMSampleBuffer {
    var pixelBuffer: CVPixelBuffer?
    XCTAssertEqual(
      CVPixelBufferCreate(kCFAllocatorDefault, width, height, format, nil, &pixelBuffer),
      kCVReturnSuccess
    )
    let image = try XCTUnwrap(pixelBuffer)

    var formatDescription: CMVideoFormatDescription?
    XCTAssertEqual(
      CMVideoFormatDescriptionCreateForImageBuffer(
        allocator: kCFAllocatorDefault,
        imageBuffer: image,
        formatDescriptionOut: &formatDescription
      ),
      noErr
    )
    let description = try XCTUnwrap(formatDescription)

    var timing = CMSampleTimingInfo(
      duration: CMTime(value: 1, timescale: 30),
      presentationTimeStamp: .zero,
      decodeTimeStamp: .invalid
    )
    var sampleBuffer: CMSampleBuffer?
    XCTAssertEqual(
      CMSampleBufferCreateForImageBuffer(
        allocator: kCFAllocatorDefault,
        imageBuffer: image,
        dataReady: true,
        makeDataReadyCallback: nil,
        refcon: nil,
        formatDescription: description,
        sampleTiming: &timing,
        sampleBufferOut: &sampleBuffer
      ),
      noErr
    )
    return try XCTUnwrap(sampleBuffer)
  }
}

/// Minimal thread-safe flag — the sink's callback is `@Sendable` and may arrive off the main
/// thread, so a plain captured `var` would not be legal to read back.
private final class Expectation: @unchecked Sendable {
  private let lock = NSLock()
  private var didFulfill = false

  var value: Bool {
    lock.lock()
    defer { lock.unlock() }
    return didFulfill
  }

  func fulfill() {
    lock.lock()
    didFulfill = true
    lock.unlock()
  }
}
