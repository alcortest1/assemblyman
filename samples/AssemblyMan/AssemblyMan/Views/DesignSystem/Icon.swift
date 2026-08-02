/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// Icon.swift
//
// The Lucide (https://lucide.dev, ISC licence) icons the design calls for, carried as their
// original 24x24 path data and stroked at 1.5 to match. Kept in one place so an icon swap
// is a one-line change rather than a hunt through the views.
//

import SwiftUI

/// A stroked line icon drawn from vector data.
struct Icon: View {

  let glyph: Glyph
  var size: CGFloat = 22
  var color: Color = Theme.accent700
  /// Stroke weight in the icon's own 24pt design space.
  var weight: CGFloat = 1.5
  /// Solid rather than outlined — the stop glyph is the only filled icon in the set.
  var filled: Bool = false

  var body: some View {
    ZStack {
      ForEach(Array(glyph.paths.enumerated()), id: \.offset) { _, data in
        if filled {
          VectorPath(data: data)
            .fill(color)
        } else {
          VectorPath(data: data)
            .stroke(
              color,
              style: StrokeStyle(
                lineWidth: weight * (size / 24),
                lineCap: .round,
                lineJoin: .round
              )
            )
        }
      }
    }
    .frame(width: size, height: size)
    .accessibilityHidden(true)
  }

  enum Glyph {
    case video
    case volume
    case hand
    case slidersHorizontal
    case camera
    case close
    case share
    case hourglass
    case triangleAlert
    case stopSquare
    case circleDot
    case chevronLeft
    case mic
    case micOff

    /// One entry per `<path>` in the source icon.
    var paths: [String] {
      switch self {
      case .video:
        return [
          "m16 13 5.223 3.482a.5.5 0 0 0 .777-.416V7.87a.5.5 0 0 0-.752-.432L16 10.5",
          "M3 6h12a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1z",
        ]
      case .volume:
        return [
          "M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z",
          "M16 9a5 5 0 0 1 0 6",
          "M19.364 18.364a9 9 0 0 0 0-12.728",
        ]
      case .hand:
        return [
          "M18 11V6a2 2 0 0 0-4 0v5",
          "M14 10V4a2 2 0 0 0-4 0v2",
          "M10 10.5V6a2 2 0 0 0-4 0v8",
          "m18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15",
        ]
      case .slidersHorizontal:
        return [
          "M21 4h-7", "M10 4H3", "M21 12h-9", "M8 12H3", "M21 20h-5", "M12 20H3",
          "M14 2v4", "M8 10v4", "M16 18v4",
        ]
      case .camera:
        return [
          "M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z",
          "M12 10a3 3 0 1 1 0 6 3 3 0 0 1 0-6z",
        ]
      case .close:
        return ["M18 6 6 18", "m6 6 12 12"]
      case .share:
        return [
          "M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8",
          "M16 6 12 2 8 6",
          "M12 2v13",
        ]
      case .hourglass:
        return [
          "M5 22h14",
          "M5 2h14",
          "M17 22v-4.172a2 2 0 0 0-.586-1.414L12 12l-4.414 4.414A2 2 0 0 0 7 17.828V22",
          "M7 2v4.172a2 2 0 0 0 .586 1.414L12 12l4.414-4.414A2 2 0 0 0 17 6.172V2",
        ]
      case .triangleAlert:
        return [
          "m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z",
          "M12 9v4",
          "M12 17h.01",
        ]
      case .stopSquare:
        return ["M6 6h12v12H6z"]
      case .circleDot:
        return [
          "M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18z",
          "M12 10.5a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3z",
        ]
      case .chevronLeft:
        return ["m15 18-6-6 6-6"]
      case .mic:
        return [
          "M12 2a3 3 0 0 1 3 3v7a3 3 0 0 1-6 0V5a3 3 0 0 1 3-3z",
          "M19 10v2a7 7 0 0 1-14 0v-2",
          "M12 19v3",
        ]
      case .micOff:
        return [
          "M2 2l20 20",
          "M15 9.34V5a3 3 0 0 0-5.68-1.33",
          "M9 9v3a3 3 0 0 0 5.12 2.12",
          "M19 10v2a7 7 0 0 1-.11 1.23",
          "M5 10v2a7 7 0 0 0 12 5",
          "M12 19v3",
        ]
      }
    }
  }
}

#Preview {
  let glyphs: [Icon.Glyph] = [
    .video, .volume, .hand, .slidersHorizontal, .camera, .close,
    .share, .hourglass, .triangleAlert, .stopSquare, .circleDot, .chevronLeft,
    .mic, .micOff,
  ]
  return LazyVGrid(columns: Array(repeating: GridItem(), count: 4), spacing: 24) {
    ForEach(Array(glyphs.enumerated()), id: \.offset) { _, glyph in
      Icon(glyph: glyph, size: 32, color: Theme.text)
    }
  }
  .padding(40)
  .background(Theme.bg)
}
