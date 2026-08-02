/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// RelayFrameCompositorTests.swift
//
// The compositor sits on the frame delivery thread between the glasses and WebRTC, so its
// failure modes are quiet ones: a pass-through that silently stops passing through, or a
// composite that lands in the wrong place. These pin the shape rather than the pixels.
//

import CoreImage
import CoreVideo
import XCTest

@testable import AssemblyMan

final class RelayFrameCompositorTests: XCTestCase {

  // MARK: - Pass-through

  func testDoesNothingUntilAnOverlayIsSet() throws {
    let compositor = RelayFrameCompositor()

    XCTAssertFalse(compositor.isCompositing)
    // Returning nil is what keeps the common path zero-copy: the sink then publishes the
    // original buffer untouched.
    XCTAssertNil(compositor.composite(try makePixelBuffer()))
    XCTAssertEqual(compositor.framesComposited, 0)
  }

  func testClearingTheOverlayRestoresPassThrough() throws {
    let compositor = RelayFrameCompositor()
    compositor.setOverlay(try makeOverlay(width: 64, height: 48))
    XCTAssertTrue(compositor.isCompositing)

    compositor.setOverlay(nil)

    XCTAssertFalse(compositor.isCompositing)
    XCTAssertNil(compositor.composite(try makePixelBuffer()))
  }

  // MARK: - Compositing

  func testCompositesIntoANewBufferOfTheSameSize() throws {
    let compositor = RelayFrameCompositor()
    compositor.setOverlay(try makeOverlay(width: 64, height: 48))
    let source = try makePixelBuffer(width: 64, height: 48)

    let result = try XCTUnwrap(compositor.composite(source))

    XCTAssertEqual(CVPixelBufferGetWidth(result), 64)
    XCTAssertEqual(CVPixelBufferGetHeight(result), 48)
    // A distinct buffer — compositing into the source would corrupt the frame the preview
    // is still holding.
    XCTAssertNotEqual(result, source)
    XCTAssertEqual(compositor.framesComposited, 1)
    XCTAssertNil(compositor.lastFailure)
  }

  func testOutputIsAPixelFormatWebRTCCanEncode() throws {
    // The whole relay fails silently if this drifts: WebRTC drops unsupported formats with
    // only a warning, so the stream connects and publishes nothing.
    let compositor = RelayFrameCompositor()
    compositor.setOverlay(try makeOverlay(width: 32, height: 32))

    let result = try XCTUnwrap(compositor.composite(try makePixelBuffer(width: 32, height: 32)))

    XCTAssertEqual(CVPixelBufferGetPixelFormatType(result), kCVPixelFormatType_32BGRA)
  }

  func testScalesAnOverlayThatNoLongerMatchesTheFrame() throws {
    // A quality change mid-session resizes the frames while the last overlay is still the
    // old size; unscaled it would composite into a corner.
    let compositor = RelayFrameCompositor()
    compositor.setOverlay(try makeOverlay(width: 32, height: 32))

    let result = try XCTUnwrap(compositor.composite(try makePixelBuffer(width: 128, height: 96)))

    XCTAssertEqual(CVPixelBufferGetWidth(result), 128)
    XCTAssertEqual(CVPixelBufferGetHeight(result), 96)
  }

  func testReusesItsPoolAcrossFrames() throws {
    let compositor = RelayFrameCompositor()
    compositor.setOverlay(try makeOverlay(width: 64, height: 48))

    for _ in 0..<30 {
      // Released each iteration, so a pool that never recycles would be visible as a
      // failure once the pool's ceiling is reached.
      XCTAssertNotNil(compositor.composite(try makePixelBuffer(width: 64, height: 48)))
    }

    XCTAssertEqual(compositor.framesComposited, 30)
    XCTAssertNil(compositor.lastFailure)
  }

  // MARK: - Helpers

  /// A frame shaped like the ones the glasses deliver.
  private func makePixelBuffer(
    width: Int = 64,
    height: Int = 48,
    format: OSType = kCVPixelFormatType_32BGRA
  ) throws -> CVPixelBuffer {
    var buffer: CVPixelBuffer?
    let attributes: [String: Any] = [
      kCVPixelBufferIOSurfacePropertiesKey as String: [:],
      kCVPixelBufferMetalCompatibilityKey as String: true,
    ]
    XCTAssertEqual(
      CVPixelBufferCreate(
        kCFAllocatorDefault, width, height, format, attributes as CFDictionary, &buffer
      ),
      kCVReturnSuccess
    )
    return try XCTUnwrap(buffer)
  }

  /// A translucent overlay, as the vision processors produce.
  private func makeOverlay(width: Int, height: Int) throws -> CGImage {
    let context = try XCTUnwrap(
      CGContext(
        data: nil,
        width: width,
        height: height,
        bitsPerComponent: 8,
        bytesPerRow: width * 4,
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
      )
    )
    context.setFillColor(red: 0, green: 0.6, blue: 1, alpha: 0.5)
    context.fill(CGRect(x: 0, y: 0, width: width / 2, height: height / 2))
    return try XCTUnwrap(context.makeImage())
  }
}
