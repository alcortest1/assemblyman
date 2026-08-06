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

  // MARK: - Encoded frames
  //
  // Asking the DAT SDK for `.hvc1` instead of `.raw` delivers sample buffers of encoded bytes
  // with no CVImageBuffer at all. Every consumer needs pixels: `makeUIImage()` returns nil so
  // the preview never updates, and WebRTC's capturer drops the buffer so the relay publishes
  // black — while the offered and forwarded counters climb happily. That combination cost
  // several days once, and these pin the counter that tells it apart from a bad pixel format.

  func testEncodedFramesAreDistinguishedFromAnUnsupportedPixelFormat() {
    var encoded = LiveKitFrameSink.Diagnostics()
    encoded.framesOffered = 225
    encoded.framesForwarded = 225
    encoded.framesWithoutPixelBuffer = 225
    encoded.firstMediaSubType = kCMVideoCodecType_HEVC

    XCTAssertTrue(encoded.isDeliveringEncodedFrames)
    XCTAssertEqual(encoded.mediaSubTypeDescription, "hvc1")
    // The giveaway: frames flowed, but no pixel format was ever determined, because no frame
    // ever carried pixels to read one from.
    XCTAssertEqual(encoded.pixelFormatDescription, "—")

    var unsupported = LiveKitFrameSink.Diagnostics()
    unsupported.framesOffered = 225
    unsupported.framesForwarded = 225
    unsupported.firstPixelFormat = kCVPixelFormatType_24RGB
    unsupported.isPixelFormatSupported = false

    XCTAssertFalse(
      unsupported.isDeliveringEncodedFrames,
      "pixels WebRTC will not take is a different fault from no pixels at all"
    )
  }

  func testAHealthyFeedIsNotReportedAsEncoded() {
    var healthy = LiveKitFrameSink.Diagnostics()
    healthy.framesOffered = 100
    healthy.framesForwarded = 100
    healthy.firstPixelFormat = kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange
    healthy.isPixelFormatSupported = true

    XCTAssertFalse(healthy.isDeliveringEncodedFrames)
    XCTAssertEqual(healthy.mediaSubTypeDescription, "—")
  }

  func testMediaSubTypeIsPlaceholderBeforeAnyFrame() {
    XCTAssertEqual(LiveKitFrameSink.Diagnostics().mediaSubTypeDescription, "—")
  }

  /// A buffer shaped like an HEVC sample really does carry no image buffer — the premise the
  /// counter rests on, pinned so a future SDK change cannot quietly invalidate it.
  func testAnEncodedSampleBufferCarriesNoImageBuffer() throws {
    let encoded = try makeEncodedSampleBuffer()

    XCTAssertNil(
      CMSampleBufferGetImageBuffer(encoded),
      "an encoded sample is a block of bytes; there are no pixels to hand to WebRTC"
    )
    let description = try XCTUnwrap(CMSampleBufferGetFormatDescription(encoded))
    XCTAssertEqual(CMFormatDescriptionGetMediaSubType(description), kCMVideoCodecType_HEVC)

    // And the raw path this app depends on does carry one.
    XCTAssertNotNil(CMSampleBufferGetImageBuffer(try makeSampleBuffer()))
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

  /// A sample buffer shaped like one the SDK delivers under `VideoCodec.hvc1`: encoded bytes in
  /// a block buffer, no image buffer.
  private func makeEncodedSampleBuffer(
    codec: CMVideoCodecType = kCMVideoCodecType_HEVC
  ) throws -> CMSampleBuffer {
    let length = 64
    var blockBuffer: CMBlockBuffer?
    XCTAssertEqual(
      CMBlockBufferCreateWithMemoryBlock(
        allocator: kCFAllocatorDefault,
        memoryBlock: nil,
        blockLength: length,
        blockAllocator: kCFAllocatorDefault,
        customBlockSource: nil,
        offsetToData: 0,
        dataLength: length,
        flags: 0,
        blockBufferOut: &blockBuffer
      ),
      noErr
    )
    let block = try XCTUnwrap(blockBuffer)
    XCTAssertEqual(CMBlockBufferAssureBlockMemory(block), noErr)

    var formatDescription: CMVideoFormatDescription?
    XCTAssertEqual(
      CMVideoFormatDescriptionCreate(
        allocator: kCFAllocatorDefault,
        codecType: codec,
        width: 64,
        height: 48,
        extensions: nil,
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
    var sampleSize = length
    var sampleBuffer: CMSampleBuffer?
    XCTAssertEqual(
      CMSampleBufferCreate(
        allocator: kCFAllocatorDefault,
        dataBuffer: block,
        dataReady: true,
        makeDataReadyCallback: nil,
        refcon: nil,
        formatDescription: description,
        sampleCount: 1,
        sampleTimingEntryCount: 1,
        sampleTimingArray: &timing,
        sampleSizeEntryCount: 1,
        sampleSizeArray: &sampleSize,
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
