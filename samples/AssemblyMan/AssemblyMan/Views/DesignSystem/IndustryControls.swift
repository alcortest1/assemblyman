/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// IndustryControls.swift
//
// Shared controls for the Industry design language. Everything here is square-cornered,
// hairline-bordered, and uppercase — the pill buttons and rounded cards the app used
// before are gone.
//

import SwiftUI

// MARK: - Primary action

/// Full-width accent button with registration marks. The app's main call to action.
struct PrimaryButton: View {

  let title: String
  var height: CGFloat = 52
  var fontSize: CGFloat = 14
  var isBusy: Bool = false
  var isDisabled: Bool = false
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      HStack(spacing: 8) {
        if isBusy {
          Spinner(size: 14, color: Theme.bg)
        }
        Text(title)
          .font(Theme.heading(fontSize))
          .textCase(.uppercase)
          .tracking(fontSize * 0.06)
      }
      .frame(maxWidth: .infinity)
      .frame(height: height)
      .foregroundStyle(Theme.bg)
      .background(Theme.accent)
    }
    .buttonStyle(PressableStyle(pressedOverlay: Theme.accent700))
    .blueprintFrame(border: Theme.accent, mark: Theme.text.opacity(0.55))
    .disabled(isDisabled || isBusy)
    .opacity(isDisabled ? Theme.disabledOpacity : 1)
  }
}

// MARK: - Secondary action

/// Outlined button on the light ground. Used for update prompts and destructive actions —
/// the system has no filled destructive colour.
struct OutlineButton: View {

  let title: String
  var height: CGFloat = 44
  var fontSize: CGFloat = 13
  var fillsWidth: Bool = true
  var background: Color = .clear
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      Text(title)
        .font(Theme.body(fontSize, weight: .semibold))
        .textCase(.uppercase)
        .tracking(fontSize * 0.06)
        .padding(.horizontal, 14)
        .frame(maxWidth: fillsWidth ? .infinity : nil)
        .frame(height: height)
        .foregroundStyle(Theme.text)
        .background(background)
        .overlay { Rectangle().strokeBorder(Theme.neutral400, lineWidth: Theme.hairline) }
    }
    .buttonStyle(PressableStyle(pressedOverlay: Theme.text.opacity(0.07)))
  }
}

/// Square outlined icon button, 34pt by default — the header and back affordances.
struct IconButton: View {

  let glyph: Icon.Glyph
  var accessibilityLabel: String
  var edge: CGFloat = 34
  var iconSize: CGFloat = 18
  var tint: Color = Theme.text
  var border: Color = Theme.divider
  var background: Color = .clear
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      Icon(glyph: glyph, size: iconSize, color: tint)
        .frame(width: edge, height: edge)
        .background(background)
        .overlay { Rectangle().strokeBorder(border, lineWidth: Theme.hairline) }
    }
    .buttonStyle(PressableStyle(pressedOverlay: Theme.text.opacity(0.07)))
    .accessibilityLabel(accessibilityLabel)
  }
}

// MARK: - Tags

/// Small uppercase status chip.
struct Tag: View {

  let text: String
  var foreground: Color = Theme.accent800
  var background: Color = Theme.accent100
  /// Optional leading glyph, blinking — used by "waiting for device".
  var pulsingGlyph: Icon.Glyph?

  var body: some View {
    HStack(spacing: 5) {
      if let pulsingGlyph {
        Icon(glyph: pulsingGlyph, size: 10, color: foreground, weight: 2)
          .modifier(BlinkModifier())
      }
      Text(text)
        .font(Theme.overline(9))
        .tracking(0.9)
        .textCase(.uppercase)
    }
    .foregroundStyle(foreground)
    .padding(.horizontal, 8)
    .padding(.vertical, 3)
    .background(background)
  }

  static func online() -> Tag {
    Tag(text: "Glasses online")
  }

  static func waiting() -> Tag {
    Tag(
      text: "Waiting for device",
      foreground: Theme.neutral800,
      background: Theme.neutral100,
      pulsingGlyph: .hourglass
    )
  }
}

// MARK: - Spec table

/// One label/value row inside a blueprint-framed plate. Rows are hairline-separated.
struct SpecRow<Value: View>: View {

  let label: String
  var isFirst: Bool = false
  @ViewBuilder var value: () -> Value

  var body: some View {
    HStack {
      Text(label)
        .overlineStyle(color: Theme.neutral500)
      Spacer(minLength: 12)
      value()
    }
    .padding(.horizontal, 12)
    .padding(.vertical, 9)
    .overlay(alignment: .top) {
      if !isFirst {
        Rectangle()
          .fill(Theme.divider)
          .frame(height: Theme.hairline)
      }
    }
  }
}

// MARK: - Toggles

/// Square checkbox — the system has no iOS switch.
struct SquareCheckbox: View {

  let isOn: Bool
  var edge: CGFloat = 20
  var accessibilityLabel: String
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      ZStack {
        Rectangle()
          .strokeBorder(Theme.neutral400, lineWidth: Theme.hairline)
        if isOn {
          Rectangle()
            .fill(Theme.accent)
            .frame(width: edge * 0.6, height: edge * 0.6)
        }
      }
      .frame(width: edge, height: edge)
      .contentShape(Rectangle())
    }
    .buttonStyle(.plain)
    .accessibilityLabel(accessibilityLabel)
    .accessibilityAddTraits(isOn ? [.isSelected] : [])
  }
}

/// Hairline-bordered segmented control; the selected option fills with accent.
struct SegmentedPicker<Option: Hashable>: View {

  let options: [Option]
  let selection: Option
  let title: (Option) -> String
  let select: (Option) -> Void

  var body: some View {
    HStack(spacing: 0) {
      ForEach(Array(options.enumerated()), id: \.offset) { index, option in
        let isSelected = option == selection
        Button {
          select(option)
        } label: {
          Text(title(option))
            .font(Theme.body(12, weight: isSelected ? .semibold : .regular))
            .foregroundStyle(isSelected ? Theme.bg : Theme.text)
            .padding(.horizontal, 12)
            .frame(height: 28)
            .background(isSelected ? Theme.accent : .clear)
        }
        .buttonStyle(PressableStyle(pressedOverlay: Theme.text.opacity(0.07)))

        if index < options.count - 1 {
          Rectangle()
            .fill(Theme.divider)
            .frame(width: Theme.hairline, height: 28)
        }
      }
    }
    .overlay { Rectangle().strokeBorder(Theme.divider, lineWidth: Theme.hairline) }
  }
}

// MARK: - Feedback

/// Bottom-centred, self-dismissing message. Replaces alerts for non-blocking feedback;
/// hard errors still use a real `alert`.
struct ToastView: View {

  let message: String

  var body: some View {
    Text(message)
      .font(Theme.body(12))
      .foregroundStyle(Theme.bg)
      .padding(.horizontal, 14)
      .padding(.vertical, 9)
      .background(Theme.text)
      .transition(.move(edge: .bottom).combined(with: .opacity))
  }
}

/// Indeterminate circular progress ring matching the system's stroke weights.
struct Spinner: View {

  var size: CGFloat = 14
  var color: Color = Theme.text

  @State private var angle: Double = 0

  var body: some View {
    Circle()
      .trim(from: 0, to: 0.75)
      .stroke(color, style: StrokeStyle(lineWidth: 1.5, lineCap: .butt))
      .frame(width: size, height: size)
      .rotationEffect(.degrees(angle))
      .onAppear {
        guard Motion.allowsContinuousAnimation else { return }
        withAnimation(.linear(duration: 0.8).repeatForever(autoreverses: false)) {
          angle = 360
        }
      }
      .accessibilityHidden(true)
  }
}

// MARK: - Motion

enum Motion {

  /// Whether animations that never settle — the live blink, the spinner — should run.
  ///
  /// XCUITest waits for the app to go idle before delivering an event, and a
  /// `repeatForever` animation means that never happens: taps time out instead of landing.
  /// Under UI test these elements hold still; everything else about them is unchanged.
  static let allowsContinuousAnimation = !ProcessInfo.processInfo.arguments
    .contains("--ui-testing")
}

// MARK: - Shared modifiers

/// Steps the control one ramp darker while held, per the interaction spec.
struct PressableStyle: SwiftUI.ButtonStyle {

  var pressedOverlay: Color

  func makeBody(configuration: Configuration) -> some View {
    configuration.label
      .overlay {
        if configuration.isPressed {
          Rectangle().fill(pressedOverlay)
        }
      }
  }
}

/// 1.2s step blink used by live indicators.
struct BlinkModifier: ViewModifier {

  @State private var dim = false

  func body(content: Content) -> some View {
    content
      .opacity(dim ? 0.25 : 1)
      .onAppear {
        guard Motion.allowsContinuousAnimation else { return }
        withAnimation(.easeInOut(duration: 0.6).repeatForever(autoreverses: true)) {
          dim = true
        }
      }
  }
}

extension View {

  /// Applies the live-indicator blink.
  func blinking() -> some View {
    modifier(BlinkModifier())
  }
}
