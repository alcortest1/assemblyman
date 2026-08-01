/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// OperatorGlyph.swift
//
// The AssemblyMan brand mark: a hard hat over a pair of glasses, drawn as geometry rather
// than shipped as a bitmap so it stays crisp at every size and can recolor for dark grounds.
// Coordinates are authored in the 48x48 design space and scaled to the requested size.
//

import SwiftUI

struct OperatorGlyph: View {

  /// Rendered edge length. The glyph is square.
  let size: CGFloat
  var hatColor: Color = Theme.accent
  var lensColor: Color = Theme.text

  /// Small sizes lose the hairline, so the spec thickens the stroke below 28pt.
  private var strokeWidth: CGFloat { size <= 28 ? 3 : 2.5 }

  private var scale: CGFloat { size / Self.designSize }

  var body: some View {
    ZStack {
      HatShape()
        .stroke(hatColor, style: strokeStyle)
      LensShape()
        .stroke(lensColor, style: strokeStyle)
    }
    .frame(width: Self.designSize, height: Self.designSize)
    .scaleEffect(scale)
    .frame(width: size, height: size)
    .accessibilityHidden(true)
  }

  private var strokeStyle: StrokeStyle {
    StrokeStyle(lineWidth: strokeWidth, lineCap: .square)
  }

  fileprivate static let designSize: CGFloat = 48
}

// MARK: - Geometry

/// Hard-hat dome and brim.
private struct HatShape: Shape {

  func path(in rect: CGRect) -> Path {
    var path = Path()

    // Dome: a half circle centred at (24,19) with radius 11, sweeping over the top from
    // (13,19) to (35,19). Angles increase clockwise on screen because y points down, so
    // 180 -> 360 passes through the 270 apex.
    path.addArc(
      center: CGPoint(x: 24, y: 19),
      radius: 11,
      startAngle: .degrees(180),
      endAngle: .degrees(360),
      clockwise: false
    )

    // Brim.
    path.move(to: CGPoint(x: 8.5, y: 19.5))
    path.addLine(to: CGPoint(x: 39.5, y: 19.5))

    return path
  }
}

/// Two lenses and the bridge between them.
private struct LensShape: Shape {

  func path(in rect: CGRect) -> Path {
    var path = Path()

    path.addRect(CGRect(x: 12, y: 27, width: 10, height: 8))
    path.addRect(CGRect(x: 26, y: 27, width: 10, height: 8))

    path.move(to: CGPoint(x: 22, y: 30.5))
    path.addLine(to: CGPoint(x: 26, y: 30.5))

    return path
  }
}

// MARK: - Wordmark lockup

/// Glyph plus the condensed uppercase wordmark, used in headers.
struct OperatorLockup: View {

  var glyphSize: CGFloat
  var textSize: CGFloat
  var hatColor: Color = Theme.accent
  var lensColor: Color = Theme.text
  var textColor: Color = Theme.text

  var body: some View {
    HStack(spacing: glyphSize * 0.38) {
      OperatorGlyph(size: glyphSize, hatColor: hatColor, lensColor: lensColor)
      Text("AssemblyMan")
        .headingStyle(textSize, color: textColor)
        .tracking(textSize * 0.02)
    }
  }
}

#Preview {
  VStack(spacing: 24) {
    OperatorGlyph(size: 88)
    OperatorLockup(glyphSize: 26, textSize: 30)
    OperatorLockup(
      glyphSize: 26,
      textSize: 30,
      hatColor: Theme.accent300,
      lensColor: .white,
      textColor: .white
    )
    .padding(24)
    .background(Theme.accent900)
  }
  .padding(40)
  .background(Theme.bg)
}
