/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// DeveloperSection.swift
//
// MockDeviceKit controls inside Settings: pair simulated glasses and drive their state
// without hardware. Compiled out of release builds.
//
// This is the Industry-styled home for what used to live behind the floating debug button.
//

#if DEBUG

import MWDATMockDevice
import SwiftUI

struct DeveloperSection: View {

  @Bindable var mockKit: MockDeviceKitView.ViewModel

  /// The design caps the fleet so the list stays readable.
  private static let deviceLimit = 3

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      VStack(alignment: .leading, spacing: 2) {
        Text("Developer")
          .overlineStyle(color: Theme.accent700)
        Text("MockDeviceKit — simulate devices and states without hardware. Debug builds only.")
          .font(Theme.body(11.5))
          .foregroundStyle(Theme.neutral500)
          .fixedSize(horizontal: false, vertical: true)
      }

      VStack(spacing: 0) {
        HStack(spacing: 10) {
          VStack(alignment: .leading, spacing: 1) {
            Text("MockDeviceKit")
              .font(Theme.body(13.5, weight: .semibold))
              .foregroundStyle(Theme.text)
            Text("\(mockKit.cardViewModels.count) of \(Self.deviceLimit) paired")
              .font(Theme.body(11.5))
              .foregroundStyle(Theme.neutral600)
          }

          Spacer(minLength: 0)

          SquareCheckbox(isOn: mockKit.isEnabled, accessibilityLabel: "MockDeviceKit") {
            if mockKit.isEnabled {
              mockKit.disable()
            } else {
              mockKit.enable()
            }
          }
          .accessibilityIdentifier("mockdevicekit_toggle")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)

        if mockKit.isEnabled {
          Rectangle().fill(Theme.divider).frame(height: Theme.hairline)

          OutlineButton(
            title: "Pair Ray-Ban Meta",
            height: 36,
            fontSize: 12,
            action: mockKit.pairGlasses
          )
          .disabled(mockKit.cardViewModels.count >= Self.deviceLimit)
          .opacity(
            mockKit.cardViewModels.count >= Self.deviceLimit ? Theme.disabledOpacity : 1
          )
          .accessibilityIdentifier("pair_mock_device_button")
          .padding(12)
        }
      }
      .blueprintFrame()

      if mockKit.isEnabled {
        VStack(spacing: 12) {
          ForEach(mockKit.cardViewModels, id: \.id) { device in
            MockDevicePlate(device: device) {
              mockKit.unpairDevice(device.device)
            }
          }
        }
        .padding(.top, 2)
      }
    }
  }
}

// MARK: - Device plate

/// One simulated device: identity, hinge/power/don state, captouch, and camera source.
private struct MockDevicePlate: View {

  @Bindable var device: MockDeviceCardView.ViewModel
  let onUnpair: () -> Void

  var body: some View {
    VStack(spacing: 0) {
      header
      stateToggles
      captouchRow
      cameraSourceRow
    }
    .blueprintFrame()
  }

  private var header: some View {
    HStack(spacing: 10) {
      VStack(alignment: .leading, spacing: 1) {
        Text(device.deviceName)
          .font(Theme.body(13.5, weight: .semibold))
          .foregroundStyle(Theme.text)
        Text(device.id)
          .font(Theme.body(10))
          .tracking(0.6)
          .foregroundStyle(Theme.neutral500)
      }

      Spacer(minLength: 0)

      OutlineButton(title: "Unpair", height: 28, fontSize: 11, fillsWidth: false, action: onUnpair)
    }
    .padding(.horizontal, 12)
    .padding(.vertical, 10)
    .overlay(alignment: .bottom) {
      Rectangle().fill(Theme.divider).frame(height: Theme.hairline)
    }
  }

  private var stateToggles: some View {
    VStack(spacing: 0) {
      MockToggleRow(label: "Power", isOn: device.isPoweredOn) {
        device.isPoweredOn ? device.powerOff() : device.powerOn()
      }
      MockToggleRow(label: "Donned", isOn: device.isDonned) {
        device.isDonned ? device.doff() : device.don()
      }
      MockToggleRow(label: "Unfolded", isOn: device.isUnfolded) {
        device.isUnfolded ? device.fold() : device.unfold()
      }
    }
  }

  private var captouchRow: some View {
    HStack(spacing: 8) {
      Text("Captouch")
        .font(Theme.body(13))
        .foregroundStyle(Theme.text)
      Spacer(minLength: 0)
      OutlineButton(title: "Tap", height: 28, fontSize: 11, fillsWidth: false) {
        device.captouchTap()
      }
      OutlineButton(title: "Tap & hold", height: 28, fontSize: 11, fillsWidth: false) {
        device.captouchTapAndHold()
      }
    }
    .padding(.horizontal, 12)
    .padding(.vertical, 8)
    .overlay(alignment: .bottom) {
      Rectangle().fill(Theme.divider).frame(height: Theme.hairline)
    }
  }

  private var cameraSourceRow: some View {
    HStack(spacing: 10) {
      Text("Camera source")
        .font(Theme.body(13))
        .foregroundStyle(Theme.text)
      Spacer(minLength: 0)
      SegmentedPicker(
        options: CameraSource.allCases,
        selection: currentSource,
        title: \.label
      ) { source in
        switch source {
        case .front: device.setCameraFeed(.front)
        case .back: device.setCameraFeed(.back)
        case .videoFile: break  // Chosen through the media picker, not the segment.
        }
      }
    }
    .padding(.horizontal, 12)
    .padding(.vertical, 8)
  }

  private var currentSource: CameraSource {
    if let facing = device.cameraSource {
      return facing == .front ? .front : .back
    }
    return device.hasCameraFeed ? .videoFile : .front
  }

  enum CameraSource: CaseIterable, Hashable {
    case front
    case back
    case videoFile

    var label: String {
      switch self {
      case .front: return "Front"
      case .back: return "Back"
      case .videoFile: return "Video file"
      }
    }
  }
}

private struct MockToggleRow: View {
  let label: String
  let isOn: Bool
  let toggle: () -> Void

  var body: some View {
    HStack(spacing: 10) {
      Text(label)
        .font(Theme.body(13))
        .foregroundStyle(Theme.text)
      Spacer(minLength: 0)
      SquareCheckbox(isOn: isOn, accessibilityLabel: label, action: toggle)
    }
    .padding(.horizontal, 12)
    .padding(.vertical, 8)
    .overlay(alignment: .bottom) {
      Rectangle().fill(Theme.divider).frame(height: Theme.hairline)
    }
  }
}

#endif
