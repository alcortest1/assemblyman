/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// Theme.swift
//
// Tokens for the "Industry" design language: a light technical ground, steel-blue accent,
// condensed uppercase headings, and square corners everywhere. These values are the Swift
// mirror of the design system's token sheet — change them here, not at the call site.
//

import SwiftUI

enum Theme {

  // MARK: - Ground

  static let bg = Color(hex: 0xF2_F2_F3)
  static let surface = Color(hex: 0xE9_E9_EA)
  static let text = Color(hex: 0x1D_1F_20)

  /// Hairline rule used for every border and row separator in the system.
  static let divider = text.opacity(0.16)

  // MARK: - Accent ramp

  static let accent = Color(hex: 0x59_80_A6)
  static let accent100 = Color(hex: 0xEE_F6_FF)
  static let accent200 = Color(hex: 0xD6_EB_FF)
  static let accent300 = Color(hex: 0xB5_D9_FD)
  static let accent400 = Color(hex: 0x94_BC_E3)
  static let accent500 = Color(hex: 0x74_9D_C4)
  static let accent600 = Color(hex: 0x59_7E_A3)
  static let accent700 = Color(hex: 0x41_61_80)
  static let accent800 = Color(hex: 0x2C_45_5D)
  /// Dark field — the ground for the live session and photo preview.
  static let accent900 = Color(hex: 0x1D_2D_3D)

  // MARK: - Neutral ramp

  static let neutral100 = Color(hex: 0xF5_F5_F8)
  static let neutral200 = Color(hex: 0xE7_E7_EA)
  static let neutral300 = Color(hex: 0xD4_D4_D7)
  static let neutral400 = Color(hex: 0xB7_B7_BA)
  static let neutral500 = Color(hex: 0x98_98_9B)
  static let neutral600 = Color(hex: 0x7A_7A_7D)
  static let neutral700 = Color(hex: 0x5D_5D_60)
  static let neutral800 = Color(hex: 0x42_42_44)
  static let neutral900 = Color(hex: 0x2B_2B_2D)

  // MARK: - Metrics

  /// Corner radius is 0 across the entire system; named so call sites read intentionally.
  static let radius: CGFloat = 0
  static let hairline: CGFloat = 1
  static let screenPadding: CGFloat = 22
  /// Opacity applied to any disabled control.
  static let disabledOpacity: Double = 0.45

  // MARK: - Type

  /// Barlow Condensed SemiBold, always uppercase at the call site.
  ///
  /// Falls back to the system face at a condensed width when the Barlow files are not
  /// bundled, so the app renders correctly before the fonts are added.
  static func heading(_ size: CGFloat) -> Font {
    if UIFont(name: headingFaceName, size: size) != nil {
      return .custom(headingFaceName, fixedSize: size)
    }
    return .system(size: size, weight: .semibold).width(.condensed)
  }

  static func body(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
    let face = weight == .semibold || weight == .bold ? bodySemiboldFaceName : bodyFaceName
    if UIFont(name: face, size: size) != nil {
      return .custom(face, fixedSize: size)
    }
    return .system(size: size, weight: weight)
  }

  /// Small uppercase label used for overlines, spec-table keys, and tags.
  static func overline(_ size: CGFloat = 10) -> Font {
    body(size, weight: .regular)
  }

  private static let headingFaceName = "BarlowCondensed-SemiBold"
  private static let bodyFaceName = "Barlow-Regular"
  private static let bodySemiboldFaceName = "Barlow-SemiBold"
}

// MARK: - Text conveniences

extension View {

  /// Applies the system's overline treatment: uppercase, wide tracking, small.
  func overlineStyle(size: CGFloat = 10, tracking: CGFloat = 0.12, color: Color) -> some View {
    font(Theme.overline(size))
      .tracking(size * tracking)
      .textCase(.uppercase)
      .foregroundStyle(color)
  }

  /// Uppercase condensed heading.
  func headingStyle(_ size: CGFloat, color: Color = Theme.text) -> some View {
    font(Theme.heading(size))
      .textCase(.uppercase)
      .foregroundStyle(color)
  }
}

// MARK: - Hex initializer

extension Color {

  /// Builds a color from a 24-bit RGB literal, e.g. `0x59_80_A6`.
  init(hex: UInt32) {
    self.init(
      .sRGB,
      red: Double((hex >> 16) & 0xFF) / 255,
      green: Double((hex >> 8) & 0xFF) / 255,
      blue: Double(hex & 0xFF) / 255,
      opacity: 1
    )
  }
}
