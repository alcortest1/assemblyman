/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// SegmentMaskOverlay.swift
//
// Segment Anything overlay for the live session: dashed masks over detected objects with a
// confidence chip on each.
//
// The DAT SDK surfaces no segmentation, so the masks here are the indicative shapes from
// the design rather than live inference. Swapping in a real segmenter means replacing
// `Self.masks` with model output — the drawing code is already normalised to the viewport.
//

import SwiftUI

struct SegmentMaskOverlay: View {

  var body: some View {
    GeometryReader { geometry in
      ZStack(alignment: .topLeading) {
        ForEach(Array(Self.masks.enumerated()), id: \.offset) { _, mask in
          VectorPath(data: mask.path, viewBox: 1)
            .path(in: CGRect(origin: .zero, size: geometry.size))
            .fill(mask.tint.fill)
          VectorPath(data: mask.path, viewBox: 1)
            .path(in: CGRect(origin: .zero, size: geometry.size))
            .stroke(
              mask.tint.stroke,
              style: StrokeStyle(lineWidth: 1.5, dash: [5, 3])
            )
        }

        ForEach(Array(Self.masks.enumerated()), id: \.offset) { _, mask in
          Text(mask.label)
            .font(Theme.overline(9))
            .tracking(1.1)
            .textCase(.uppercase)
            .foregroundStyle(mask.tint.stroke)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(Theme.accent900.opacity(0.75))
            .overlay {
              Rectangle().strokeBorder(mask.tint.chipBorder, lineWidth: Theme.hairline)
            }
            .position(
              x: geometry.size.width * mask.labelPosition.x,
              y: geometry.size.height * mask.labelPosition.y
            )
        }
      }
    }
    .allowsHitTesting(false)
    .accessibilityHidden(true)
  }

  // MARK: - Mask data

  /// Masks authored against the design's 366x620 viewport, normalised to 0...1 so they
  /// track the viewfinder at any screen size.
  private struct Mask {
    let path: String
    let label: String
    let tint: Tint
    let labelPosition: CGPoint
  }

  private enum Tint {
    case accent
    case white

    var fill: Color {
      switch self {
      case .accent: return Theme.accent300.opacity(0.28)
      case .white: return .white.opacity(0.16)
      }
    }

    var stroke: Color {
      switch self {
      case .accent: return Theme.accent300
      case .white: return .white.opacity(0.8)
      }
    }

    var chipBorder: Color {
      switch self {
      case .accent: return Theme.accent300.opacity(0.6)
      case .white: return .white.opacity(0.5)
      }
    }
  }

  private static let masks: [Mask] = [
    Mask(
      path: normalised("M70 180 Q120 120 190 150 Q240 175 225 250 Q205 320 130 305 Q60 285 70 180 Z"),
      label: "Mask 01 · 0.97",
      tint: .accent,
      labelPosition: CGPoint(x: 0.30, y: 0.22)
    ),
    Mask(
      path: normalised("M230 330 Q300 310 330 370 Q345 430 290 460 Q230 480 205 425 Q190 365 230 330 Z"),
      label: "Mask 02 · 0.91",
      tint: .white,
      labelPosition: CGPoint(x: 0.70, y: 0.58)
    ),
    Mask(
      path: normalised("M90 420 Q140 395 175 440 Q200 490 155 525 Q100 545 75 495 Q60 450 90 420 Z"),
      label: "Mask 03 · 0.84",
      tint: .accent,
      labelPosition: CGPoint(x: 0.28, y: 0.76)
    ),
  ]

  private static let designWidth: CGFloat = 366
  private static let designHeight: CGFloat = 620

  /// Rewrites the design's absolute coordinates into a 0...1 space so `VectorPath` can
  /// scale them to whatever the viewfinder happens to be.
  private static func normalised(_ data: String) -> String {
    var output = ""
    var number = ""
    var isX = true

    func flush() {
      guard !number.isEmpty, let value = Double(number) else {
        output += number
        number = ""
        return
      }
      let divisor = isX ? designWidth : designHeight
      output += String(format: "%.5f", value / Double(divisor))
      isX.toggle()
      number = ""
    }

    for character in data {
      if character.isNumber || character == "." || character == "-" {
        number.append(character)
      } else {
        flush()
        output.append(character)
        // Commands reset which axis the next number belongs to.
        if character.isLetter { isX = true }
      }
    }
    flush()
    return output
  }
}

#Preview {
  ZStack {
    Theme.accent900
    SegmentMaskOverlay()
      .padding(.horizontal, 18)
      .padding(.top, 74)
      .padding(.bottom, 118)
  }
  .ignoresSafeArea()
}
