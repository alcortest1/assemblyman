#!/usr/bin/env swift

/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// make_app_icon.swift
//
// Renders the AssemblyMan app icon from the Operator glyph (logo sheet option 1d) so the
// icon stays derivable from the mark rather than being a hand-edited bitmap: the glyph in
// its dark-ground variant — hat in accent-300, lenses in white — centred on accent-900.
//
// Usage: swift scripts/make_app_icon.swift <output.png> [size]
//

import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

// MARK: - Tokens

/// accent-900 — the dark field the mark sits on.
let ground = CGColor(red: 0x1D / 255, green: 0x2D / 255, blue: 0x3D / 255, alpha: 1)
/// accent-300 — hat on a dark ground.
let hatColor = CGColor(red: 0xB5 / 255, green: 0xD9 / 255, blue: 0xFD / 255, alpha: 1)
let lensColor = CGColor(gray: 1, alpha: 1)

/// Stroke weight in the glyph's own 48-unit design space.
let strokeWidth: CGFloat = 2.5

/// Fraction of the canvas the mark's ink should span.
let inkFraction: CGFloat = 0.58

// MARK: - Glyph geometry
//
// Authored in the 48x48 design space of the source SVG, with y flipped into Core Graphics'
// y-up convention (yCG = 48 - ySVG) so the numbers below still read against the spec.

let designSize: CGFloat = 48

/// Ink bounds including stroke, used to centre the mark optically rather than by viewBox.
let inkBounds = CGRect(x: 7.25, y: 11.75, width: 33.5, height: 29.5)

func drawGlyph(in context: CGContext) {
  context.setLineCap(.square)
  context.setLineWidth(strokeWidth)

  // Hat dome: centre (24,19) radius 11 in SVG space, sweeping over the top.
  context.setStrokeColor(hatColor)
  context.beginPath()
  context.addArc(
    center: CGPoint(x: 24, y: designSize - 19),
    radius: 11,
    startAngle: .pi,
    endAngle: 0,
    clockwise: true
  )
  context.strokePath()

  // Hat brim.
  context.beginPath()
  context.move(to: CGPoint(x: 8.5, y: designSize - 19.5))
  context.addLine(to: CGPoint(x: 39.5, y: designSize - 19.5))
  context.strokePath()

  // Lenses and bridge.
  context.setStrokeColor(lensColor)
  context.beginPath()
  context.addRect(CGRect(x: 12, y: designSize - 35, width: 10, height: 8))
  context.addRect(CGRect(x: 26, y: designSize - 35, width: 10, height: 8))
  context.strokePath()

  context.beginPath()
  context.move(to: CGPoint(x: 22, y: designSize - 30.5))
  context.addLine(to: CGPoint(x: 26, y: designSize - 30.5))
  context.strokePath()
}

// MARK: - Render

func renderIcon(size: Int, to url: URL) throws {
  let dimension = CGFloat(size)
  guard
    let context = CGContext(
      data: nil,
      width: size,
      height: size,
      bitsPerComponent: 8,
      bytesPerRow: 0,
      space: CGColorSpace(name: CGColorSpace.sRGB)!,
      bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
    )
  else {
    throw RenderError.contextUnavailable
  }

  context.setAllowsAntialiasing(true)
  context.setShouldAntialias(true)

  // Full-bleed ground; iOS applies its own mask.
  context.setFillColor(ground)
  context.fill(CGRect(x: 0, y: 0, width: dimension, height: dimension))

  // Scale the mark's ink to the target fraction and centre it on the canvas.
  let scale = (dimension * inkFraction) / max(inkBounds.width, inkBounds.height)
  context.translateBy(
    x: dimension / 2 - inkBounds.midX * scale,
    y: dimension / 2 - inkBounds.midY * scale
  )
  context.scaleBy(x: scale, y: scale)

  drawGlyph(in: context)

  guard let image = context.makeImage() else { throw RenderError.imageUnavailable }
  guard
    let destination = CGImageDestinationCreateWithURL(
      url as CFURL,
      UTType.png.identifier as CFString,
      1,
      nil
    )
  else {
    throw RenderError.destinationUnavailable
  }
  CGImageDestinationAddImage(destination, image, nil)
  guard CGImageDestinationFinalize(destination) else { throw RenderError.writeFailed }
}

enum RenderError: Error {
  case contextUnavailable
  case imageUnavailable
  case destinationUnavailable
  case writeFailed
}

// MARK: - Entry point

let arguments = CommandLine.arguments
guard arguments.count >= 2 else {
  FileHandle.standardError.write(
    Data("usage: swift make_app_icon.swift <output.png> [size]\n".utf8)
  )
  exit(2)
}

let outputURL = URL(fileURLWithPath: arguments[1])
let size = arguments.count > 2 ? Int(arguments[2]) ?? 1024 : 1024

do {
  try renderIcon(size: size, to: outputURL)
  print("Wrote \(size)x\(size) icon to \(outputURL.path)")
} catch {
  FileHandle.standardError.write(Data("failed to render icon: \(error)\n".utf8))
  exit(1)
}
