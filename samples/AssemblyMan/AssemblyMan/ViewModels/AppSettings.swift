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
  /// The SDK chooses the transport itself — there is no public API to pick Wi-Fi over
  /// Bluetooth. What the app controls is the resolution it requests, and the top tier is
  /// only deliverable over Wi-Fi, so this toggle gates which qualities are offered. The
  /// Wi-Fi path additionally requires `NSLocalNetworkUsageDescription` and
  /// `NSBonjourServices` in Info.plist, which the app declares.
  var streamsOverWiFi: Bool = false {
    didSet {
      guard streamsOverWiFi != oldValue else { return }
      // Keep the selection inside whatever the new transport can carry.
      if !availableQualities.contains(quality) {
        quality = streamsOverWiFi ? .high : .medium
      }
    }
  }

  var quality: Quality = .medium
  var frameRate: FrameRate = .thirty

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
  /// The DAT SDK exposes no agent API, so this selection is presentation-only for now — it
  /// is carried here rather than in the view so that wiring it up later touches one place.
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
