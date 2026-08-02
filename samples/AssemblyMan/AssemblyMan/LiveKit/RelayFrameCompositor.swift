/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// RelayFrameCompositor.swift
//
// Burns the on-device vision overlay into the frames the relay publishes, so a remote viewer
// sees what the operator sees rather than the bare camera feed.
//
// The overlay only ever existed as a SwiftUI layer composited on screen; the relay teed the
// raw buffer straight to WebRTC and so sent none of it. This does that compositing again, in
// pixels, on the frame delivery thread.
//
// It is the only expensive thing on that path, so it is arranged to do as little as possible:
// with no overlay set it does nothing at all and the raw buffer passes through untouched, and
// the overlay — which changes about once a second while frames arrive thirty times a second —
// is converted to a `CIImage` once when it is set, not once per frame.
//

import CoreImage
import CoreVideo
import Foundation
import Metal
import os

final class RelayFrameCompositor: Sendable {

  /// `@unchecked` for one reason: `CVPixelBufferPool` predates `Sendable` and carries no
  /// conformance, so holding one in a lock-protected value trips the checker even though the
  /// access is safe. CoreVideo documents pools as thread-safe, and every field here is only
  /// ever touched under the lock below.
  private struct State: @unchecked Sendable {
    /// Pre-converted and pre-scaled to the frame it will be drawn over, so the per-frame cost
    /// is a composite and a render, nothing else.
    var overlay: CIImage?
    var overlaySourceExtent: CGRect = .zero
    var pool: CVPixelBufferPool?
    var poolDimensions: (width: Int, height: Int) = (0, 0)
    var framesComposited: UInt64 = 0
    var lastFailure: String?
  }

  /// What `composite` carries back out of the lock so the expensive render happens outside it.
  /// `@unchecked` for the same reason as `State`: `CVPixelBufferPool` has no `Sendable`
  /// conformance, and the value never leaves the calling thread.
  private struct Prepared: @unchecked Sendable {
    let overlay: CIImage
    let pool: CVPixelBufferPool
  }

  private let state = OSAllocatedUnfairLock(initialState: State())
  private let context: CIContext

  init() {
    // Metal-backed where possible: a software CIContext cannot keep up at thirty frames a
    // second. `CIContext` is documented as thread-safe, which is what lets the frame delivery
    // thread render without a lock around it.
    if let device = MTLCreateSystemDefaultDevice() {
      context = CIContext(mtlDevice: device, options: [.cacheIntermediates: false])
    } else {
      context = CIContext(options: [.cacheIntermediates: false])
    }
  }

  var isCompositing: Bool { state.withLock { $0.overlay != nil } }

  var framesComposited: UInt64 { state.withLock { $0.framesComposited } }

  var lastFailure: String? { state.withLock { $0.lastFailure } }

  /// Sets the overlay to burn in, or clears it. Called from the main actor whenever the
  /// on-device overlay changes — roughly once a second, not per frame.
  ///
  /// Passing `nil` restores the zero-copy path: frames go to WebRTC exactly as they arrived.
  func setOverlay(_ image: CGImage?) {
    state.withLock {
      guard let image else {
        $0.overlay = nil
        $0.overlaySourceExtent = .zero
        return
      }
      $0.overlay = CIImage(cgImage: image)
      $0.overlaySourceExtent = CGRect(x: 0, y: 0, width: image.width, height: image.height)
    }
  }

  /// Composites the current overlay over `source`, returning a new buffer to publish.
  ///
  /// Returns nil when there is nothing to draw or the render fails — the caller then falls
  /// back to publishing the original buffer, so a compositing problem degrades to an
  /// overlay-free stream rather than a dead one.
  func composite(_ source: CVPixelBuffer) -> CVPixelBuffer? {
    let width = CVPixelBufferGetWidth(source)
    let height = CVPixelBufferGetHeight(source)

    let prepared: Prepared? = state.withLock { state in
      guard let overlay = state.overlay else { return nil }

      if state.pool == nil || state.poolDimensions != (width, height) {
        guard let pool = Self.makePool(width: width, height: height) else {
          state.lastFailure = "could not create a \(width)x\(height) buffer pool"
          return nil
        }
        state.pool = pool
        state.poolDimensions = (width, height)
      }

      // The overlay is generated from a still of the same frame, so it normally matches
      // exactly. Scale anyway: a resolution change mid-session would otherwise composite a
      // stale-sized overlay into the corner.
      var scaled = overlay
      let extent = state.overlaySourceExtent
      if extent.width > 0, extent.height > 0,
        Int(extent.width) != width || Int(extent.height) != height {
        scaled = overlay.transformed(
          by: CGAffineTransform(
            scaleX: CGFloat(width) / extent.width,
            y: CGFloat(height) / extent.height
          )
        )
      }
      return Prepared(overlay: scaled, pool: state.pool!)
    }

    guard let prepared else { return nil }

    var destination: CVPixelBuffer?
    guard
      CVPixelBufferPoolCreatePixelBuffer(kCFAllocatorDefault, prepared.pool, &destination)
        == kCVReturnSuccess,
      let destination
    else {
      state.withLock { $0.lastFailure = "buffer pool exhausted" }
      return nil
    }

    let base = CIImage(cvPixelBuffer: source)
    let composited = prepared.overlay.composited(over: base)
    context.render(composited, to: destination)

    state.withLock { $0.framesComposited &+= 1 }
    return destination
  }

  /// BGRA rather than the glasses' native 420v: Core Image renders to it directly, and WebRTC
  /// accepts it, so the composite needs no intermediate conversion in either direction.
  private static func makePool(width: Int, height: Int) -> CVPixelBufferPool? {
    let attributes: [String: Any] = [
      kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
      kCVPixelBufferWidthKey as String: width,
      kCVPixelBufferHeightKey as String: height,
      // Both are needed for Core Image to render into the buffer without a copy.
      kCVPixelBufferIOSurfacePropertiesKey as String: [:],
      kCVPixelBufferMetalCompatibilityKey as String: true,
    ]
    var pool: CVPixelBufferPool?
    let status = CVPixelBufferPoolCreate(
      kCFAllocatorDefault,
      // Cap the pool so a stalled encoder cannot grow it without bound.
      [kCVPixelBufferPoolMinimumBufferCountKey as String: 3] as CFDictionary,
      attributes as CFDictionary,
      &pool
    )
    return status == kCVReturnSuccess ? pool : nil
  }
}
