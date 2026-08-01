/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// VectorPath.swift
//
// Builds a SwiftUI `Path` from SVG path data so icon geometry can be copied verbatim from
// the design source instead of being re-approximated by hand. Supports the command set the
// icon set actually uses: M/L/H/V/C/S/Q/T/A/Z in both absolute and relative form.
//

import SwiftUI

/// A shape defined by SVG path data authored in a square design space.
struct VectorPath: Shape {

  /// SVG `d` attribute contents.
  let data: String
  /// Edge length of the viewBox the data was authored in.
  var viewBox: CGFloat = 24

  func path(in rect: CGRect) -> Path {
    let parsed = SVGPathParser(data: data).parse()
    let scale = min(rect.width, rect.height) / viewBox
    let transform = CGAffineTransform(translationX: rect.minX, y: rect.minY)
      .scaledBy(x: scale, y: scale)
    return parsed.applying(transform)
  }
}

// MARK: - Parser

/// Translates SVG path data into a `Path`.
///
/// This is deliberately minimal — it exists to carry a fixed set of authored icons, not to
/// be a general SVG engine. Malformed input yields whatever was parsed up to that point
/// rather than trapping, so a typo in an icon degrades to a partial glyph.
struct SVGPathParser {

  let data: String

  func parse() -> Path {
    var path = Path()
    var scanner = TokenScanner(data)

    // Current point, subpath start, and the reflected control points that S and T need.
    var current = CGPoint.zero
    var subpathStart = CGPoint.zero
    var lastCubicControl: CGPoint?
    var lastQuadControl: CGPoint?
    var command: Character?

    while true {
      if let next = scanner.peekCommand() {
        command = next
        scanner.advance()
      } else if scanner.atEnd {
        break
      } else if command == nil {
        break
      }

      guard let command else { break }
      let relative = command.isLowercase
      let op = Character(command.uppercased())

      // A repeated coordinate set without a fresh letter continues the previous command,
      // except that a repeated moveto is implicitly a lineto.
      func point(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
        relative ? CGPoint(x: current.x + x, y: current.y + y) : CGPoint(x: x, y: y)
      }

      switch op {
      case "M":
        guard let x = scanner.number(), let y = scanner.number() else { return path }
        current = point(x, y)
        subpathStart = current
        path.move(to: current)
        lastCubicControl = nil
        lastQuadControl = nil
        // Subsequent pairs are implicit linetos.
        while scanner.hasNumber {
          guard let lx = scanner.number(), let ly = scanner.number() else { return path }
          current = point(lx, ly)
          path.addLine(to: current)
        }

      case "L":
        while scanner.hasNumber {
          guard let x = scanner.number(), let y = scanner.number() else { return path }
          current = point(x, y)
          path.addLine(to: current)
        }
        lastCubicControl = nil
        lastQuadControl = nil

      case "H":
        while scanner.hasNumber {
          guard let x = scanner.number() else { return path }
          current = relative ? CGPoint(x: current.x + x, y: current.y) : CGPoint(x: x, y: current.y)
          path.addLine(to: current)
        }
        lastCubicControl = nil
        lastQuadControl = nil

      case "V":
        while scanner.hasNumber {
          guard let y = scanner.number() else { return path }
          current = relative ? CGPoint(x: current.x, y: current.y + y) : CGPoint(x: current.x, y: y)
          path.addLine(to: current)
        }
        lastCubicControl = nil
        lastQuadControl = nil

      case "C":
        while scanner.hasNumber {
          guard
            let x1 = scanner.number(), let y1 = scanner.number(),
            let x2 = scanner.number(), let y2 = scanner.number(),
            let x = scanner.number(), let y = scanner.number()
          else { return path }
          let control1 = point(x1, y1)
          let control2 = point(x2, y2)
          current = point(x, y)
          path.addCurve(to: current, control1: control1, control2: control2)
          lastCubicControl = control2
          lastQuadControl = nil
        }

      case "S":
        while scanner.hasNumber {
          guard
            let x2 = scanner.number(), let y2 = scanner.number(),
            let x = scanner.number(), let y = scanner.number()
          else { return path }
          let control1 = reflect(lastCubicControl, about: current)
          let control2 = point(x2, y2)
          current = point(x, y)
          path.addCurve(to: current, control1: control1, control2: control2)
          lastCubicControl = control2
          lastQuadControl = nil
        }

      case "Q":
        while scanner.hasNumber {
          guard
            let x1 = scanner.number(), let y1 = scanner.number(),
            let x = scanner.number(), let y = scanner.number()
          else { return path }
          let control = point(x1, y1)
          current = point(x, y)
          path.addQuadCurve(to: current, control: control)
          lastQuadControl = control
          lastCubicControl = nil
        }

      case "T":
        while scanner.hasNumber {
          guard let x = scanner.number(), let y = scanner.number() else { return path }
          let control = reflect(lastQuadControl, about: current)
          current = point(x, y)
          path.addQuadCurve(to: current, control: control)
          lastQuadControl = control
          lastCubicControl = nil
        }

      case "A":
        while scanner.hasNumber {
          guard
            let rx = scanner.number(), let ry = scanner.number(),
            let rotation = scanner.number(),
            let largeArc = scanner.flag(), let sweep = scanner.flag(),
            let x = scanner.number(), let y = scanner.number()
          else { return path }
          let end = point(x, y)
          appendArc(
            to: &path,
            from: current,
            to: end,
            rx: rx,
            ry: ry,
            rotationDegrees: rotation,
            largeArc: largeArc,
            sweep: sweep
          )
          current = end
          lastCubicControl = nil
          lastQuadControl = nil
        }

      case "Z":
        path.closeSubpath()
        current = subpathStart
        lastCubicControl = nil
        lastQuadControl = nil

      default:
        return path
      }

      if scanner.atEnd { break }
    }

    return path
  }

  private func reflect(_ control: CGPoint?, about point: CGPoint) -> CGPoint {
    guard let control else { return point }
    return CGPoint(x: 2 * point.x - control.x, y: 2 * point.y - control.y)
  }

  /// Converts an SVG endpoint-parameterised arc to centre form and appends it.
  /// Follows the W3C SVG implementation notes (F.6.5).
  private func appendArc(
    to path: inout Path,
    from start: CGPoint,
    to end: CGPoint,
    rx: CGFloat,
    ry: CGFloat,
    rotationDegrees: CGFloat,
    largeArc: Bool,
    sweep: Bool
  ) {
    // Degenerate radii collapse the arc to a straight line.
    guard rx != 0, ry != 0, start != end else {
      path.addLine(to: end)
      return
    }

    var rx = abs(rx)
    var ry = abs(ry)
    let phi = rotationDegrees * .pi / 180
    let cosPhi = cos(phi)
    let sinPhi = sin(phi)

    let dx2 = (start.x - end.x) / 2
    let dy2 = (start.y - end.y) / 2
    let x1p = cosPhi * dx2 + sinPhi * dy2
    let y1p = -sinPhi * dx2 + cosPhi * dy2

    // Scale up radii that are too small to span the chord.
    let lambda = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lambda > 1 {
      let scale = sqrt(lambda)
      rx *= scale
      ry *= scale
    }

    let numerator = max(
      0,
      rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    )
    let denominator = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    var coefficient = denominator == 0 ? 0 : sqrt(numerator / denominator)
    if largeArc == sweep { coefficient = -coefficient }

    let cxp = coefficient * rx * y1p / ry
    let cyp = -coefficient * ry * x1p / rx

    let cx = cosPhi * cxp - sinPhi * cyp + (start.x + end.x) / 2
    let cy = sinPhi * cxp + cosPhi * cyp + (start.y + end.y) / 2

    func angle(_ ux: CGFloat, _ uy: CGFloat, _ vx: CGFloat, _ vy: CGFloat) -> CGFloat {
      let dot = ux * vx + uy * vy
      let len = sqrt((ux * ux + uy * uy) * (vx * vx + vy * vy))
      guard len != 0 else { return 0 }
      let value = min(1, max(-1, dot / len))
      let result = acos(value)
      return (ux * vy - uy * vx) < 0 ? -result : result
    }

    let startAngle = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    var sweepAngle = angle(
      (x1p - cxp) / rx,
      (y1p - cyp) / ry,
      (-x1p - cxp) / rx,
      (-y1p - cyp) / ry
    )

    if !sweep && sweepAngle > 0 {
      sweepAngle -= 2 * .pi
    } else if sweep && sweepAngle < 0 {
      sweepAngle += 2 * .pi
    }

    // SwiftUI has no elliptical-arc primitive, so draw a unit-circle arc and squash it
    // into place with a transform.
    var arc = Path()
    arc.addArc(
      center: .zero,
      radius: 1,
      startAngle: .radians(startAngle),
      endAngle: .radians(startAngle + sweepAngle),
      clockwise: sweepAngle < 0
    )

    let transform = CGAffineTransform(translationX: cx, y: cy)
      .rotated(by: phi)
      .scaledBy(x: rx, y: ry)
    path.addPath(arc.applying(transform))
  }
}

// MARK: - Tokenising

/// Pulls commands, numbers, and arc flags out of SVG path data.
private struct TokenScanner {

  private let characters: [Character]
  private var index: Int = 0

  init(_ string: String) {
    characters = Array(string)
  }

  var atEnd: Bool {
    var probe = index
    while probe < characters.count, isSeparator(characters[probe]) { probe += 1 }
    return probe >= characters.count
  }

  mutating func advance() {
    index += 1
  }

  /// Returns the command letter at the cursor, if there is one.
  mutating func peekCommand() -> Character? {
    skipSeparators()
    guard index < characters.count else { return nil }
    let character = characters[index]
    return character.isLetter ? character : nil
  }

  var hasNumber: Bool {
    var probe = index
    while probe < characters.count, isSeparator(characters[probe]) { probe += 1 }
    guard probe < characters.count else { return false }
    let character = characters[probe]
    return character.isNumber || character == "-" || character == "+" || character == "."
  }

  /// Arc flags are single characters and may be packed without separators.
  mutating func flag() -> Bool? {
    skipSeparators()
    guard index < characters.count else { return nil }
    let character = characters[index]
    guard character == "0" || character == "1" else { return number().map { $0 != 0 } }
    index += 1
    return character == "1"
  }

  mutating func number() -> CGFloat? {
    skipSeparators()
    var literal = ""

    if index < characters.count, characters[index] == "-" || characters[index] == "+" {
      literal.append(characters[index])
      index += 1
    }

    var sawDot = false
    while index < characters.count {
      let character = characters[index]
      if character.isNumber {
        literal.append(character)
        index += 1
      } else if character == "." && !sawDot {
        sawDot = true
        literal.append(character)
        index += 1
      } else if character == "e" || character == "E" {
        literal.append(character)
        index += 1
        if index < characters.count, characters[index] == "-" || characters[index] == "+" {
          literal.append(characters[index])
          index += 1
        }
      } else {
        break
      }
    }

    guard let value = Double(literal) else { return nil }
    return CGFloat(value)
  }

  private mutating func skipSeparators() {
    while index < characters.count, isSeparator(characters[index]) { index += 1 }
  }

  private func isSeparator(_ character: Character) -> Bool {
    character == " " || character == "," || character == "\n" || character == "\t"
      || character == "\r"
  }
}
