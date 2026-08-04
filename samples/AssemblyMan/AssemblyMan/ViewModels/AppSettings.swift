/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// AppSettings.swift
//
// User-adjustable session and overlay preferences backing the Settings screen.
//
// Stream quality and frame rate feed StreamConfiguration when a session starts, so changing
// them takes effect on the next session rather than the current one. Overlay and capture
// preferences are presentation-only.
//

import MWDATCamera
import MWDATCore
import Observation
import SwiftUI

@MainActor
@Observable
final class AppSettings {

  // MARK: - Session

  /// Whether to ask for the high-bandwidth video tier.
  ///
  /// This does not select a transport, and nothing here reaches the SDK — the only value that
  /// does is `StreamConfiguration.resolution`. What this toggle does is widen the tier list to
  /// include `.high`, and requesting `.high` is what makes the SDK take a Wi-Fi lease: the
  /// camera framework carries the string "requires medium (BTC) or high (WiFi) bandwidth
  /// link", and the core framework upgrades to SoftAP on demand. Asking for `.medium` pins the
  /// session to Bluetooth Classic.
  ///
  /// On by default because Bluetooth Classic is where the feed stalls. The Wi-Fi path
  /// additionally needs `NSLocalNetworkUsageDescription` and `NSBonjourServices` in
  /// Info.plist, which the app declares.
  var streamsOverWiFi: Bool {
    didSet {
      guard streamsOverWiFi != oldValue else { return }
      defaults.set(streamsOverWiFi, forKey: Self.wiFiKey)
      // Keep the selection inside whatever the new transport can carry.
      if !availableQualities.contains(quality) {
        quality = streamsOverWiFi ? .high : .medium
      }
    }
  }

  /// The tier that actually gets the Wi-Fi lease, so a fresh launch does not start on
  /// Bluetooth and discover the problem mid-session.
  var quality: Quality
  var frameRate: FrameRate = .thirty

  static let wiFiKey = "streamsOverWiFi"
  @ObservationIgnored private let defaults: UserDefaults

  /// `defaults` is injectable so tests are not steered by whatever the device last stored.
  init(defaults: UserDefaults = .standard) {
    self.defaults = defaults
    // `UserDefaults` cannot distinguish an absent boolean from `false`, so the default only
    // applies when nothing has been written yet.
    let prefersWiFi = defaults.object(forKey: Self.wiFiKey) as? Bool ?? true
    self.streamsOverWiFi = prefersWiFi
    self.quality = prefersWiFi ? .high : .medium
  }

  /// Whether to mirror the session into a LiveKit room for remote viewers and the assistant.
  ///
  /// Unlike quality and frame rate this does not feed `StreamConfiguration`, so toggling it
  /// starts or stops the relay directly and must not go through `restartStream()`.
  var relaysToLiveKit: Bool = true

  /// Qualities the current transport can sustain.
  var availableQualities: [Quality] {
    streamsOverWiFi ? [.medium, .high] : [.low, .medium]
  }

  var streamSpec: String {
    "\(quality.label) · \(frameRate.label) · \(streamsOverWiFi ? "Wi-Fi" : "Bluetooth")"
  }

  var liveLabel: String {
    "Live · \(quality.label.uppercased()) \(frameRate.rawValue)FPS"
  }

  // MARK: - Overlays

  var showsViewfinderMarks: Bool = true
  var showsThirdsGrid: Bool = false
  /// Segment Anything masks over detected objects. Presentation-only — the SDK surfaces no
  /// segmentation, so the overlay draws indicative masks rather than live inference.
  var showsSegmentMasks: Bool = false
  var showsStatusChip: Bool = true
  var showsElapsedTimer: Bool = true

  // MARK: - Capture

  var savesToPhotos: Bool = true
  var playsShutterCue: Bool = false

  // MARK: - Agent

  var agent: Agent = .assistant

  // MARK: - Options

  enum Quality: String, CaseIterable, Hashable {
    case low = "480p"
    case medium = "720p"
    case high = "1080p"

    var label: String { rawValue }

    var streamingResolution: StreamingResolution {
      switch self {
      case .low: return .low
      case .medium: return .medium
      case .high: return .high
      }
    }
  }

  enum FrameRate: Int, CaseIterable, Hashable {
    case twentyFour = 24
    case thirty = 30

    var label: String { "\(rawValue) fps" }
  }

  /// Assistants that ride along on a session.
  ///
  /// Published as participant metadata when the relay joins the LiveKit room, so whatever
  /// agent is listening there can tell which assistant the operator asked for.
  enum Agent: String, CaseIterable, Hashable, Identifiable {
    case none
    case assistant
    case inspection
    case parts

    var id: String { rawValue }

    var name: String {
      switch self {
      case .none: return "None — manual"
      case .assistant: return "Assembly Assistant"
      case .inspection: return "Inspection Logger"
      case .parts: return "Parts Spotter"
      }
    }

    var detail: String {
      switch self {
      case .none: return "Stream and shutter only. No agent riding along."
      case .assistant: return "Watches the feed and calls out the next step."
      case .inspection: return "Flags defects and files a still for each one."
      case .parts: return "Identifies parts in view and pulls their spec."
      }
    }
  }
}
