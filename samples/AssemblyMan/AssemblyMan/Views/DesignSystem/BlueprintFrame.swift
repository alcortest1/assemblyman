/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// BlueprintFrame.swift
//
// The framing device the whole app is built from: a hairline square border with four "+"
// registration marks straddling its corners, as if the element had been drawn on a
// technical plan. Applied to cards, figures, the primary button, and the shutter.
//

import SwiftUI

extension View {

  /// Wraps the view in the system's blueprint framing.
  ///
  /// - Parameters:
  ///   - border: Hairline border colour. Pass `nil` for marks without a border — used over
  ///     the live feed, where the frame would fight the image.
  ///   - mark: Registration-mark colour.
  func blueprintFrame(
    border: Color? = Theme.divider,
    mark: Color = Theme.text.opacity(0.55)
  ) -> some View {
    modifier(BlueprintFrame(border: border, mark: mark))
  }
}

struct BlueprintFrame: ViewModifier {

  var border: Color?
  var mark: Color

  func body(content: Content) -> some View {
    content
      .overlay {
        if let border {
          Rectangle()
            .strokeBorder(border, lineWidth: Theme.hairline)
        }
      }
      // Drawn last and deliberately unclipped: the marks straddle the border corners.
      .overlay {
        RegistrationMarks()
          .stroke(mark, lineWidth: Theme.hairline)
      }
  }
}

/// Four crosses, each centred on a corner of the bounding rect.
struct RegistrationMarks: Shape {

  /// Full width of a cross arm, from the spec's 11x11pt mark.
  var span: CGFloat = 11

  func path(in rect: CGRect) -> Path {
    var path = Path()
    let reach = span / 2

    let corners = [
      CGPoint(x: rect.minX, y: rect.minY),
      CGPoint(x: rect.maxX, y: rect.minY),
      CGPoint(x: rect.minX, y: rect.maxY),
      CGPoint(x: rect.maxX, y: rect.maxY),
    ]

    for corner in corners {
      path.move(to: CGPoint(x: corner.x - reach, y: corner.y))
      path.addLine(to: CGPoint(x: corner.x + reach, y: corner.y))
      path.move(to: CGPoint(x: corner.x, y: corner.y - reach))
      path.addLine(to: CGPoint(x: corner.x, y: corner.y + reach))
    }

    return path
  }
}

#Preview {
  VStack(spacing: 40) {
    Text("Framed card")
      .padding(24)
      .blueprintFrame()

    Text("Marks only")
      .padding(24)
      .blueprintFrame(border: nil, mark: .white)
      .background(Theme.accent900)
  }
  .padding(60)
  .background(Theme.bg)
}
