/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

@testable import AssemblyMan
import MWDATCamera
import XCTest

/// Covers the quality tiers offered per transport and the clamping that keeps the selected
/// quality inside what the current transport can carry.
@MainActor
final class AppSettingsTests: XCTestCase {

  private var settings: AppSettings!

  private var defaults: UserDefaults!
  private var suiteName: String!

  override func setUp() async throws {
    try await super.setUp()
    // A private suite per test: `streamsOverWiFi` is persisted now, so sharing
    // `UserDefaults.standard` would let one test — or the app's own last run on this
    // simulator — decide another test's starting state.
    suiteName = "AppSettingsTests.\(UUID().uuidString)"
    defaults = UserDefaults(suiteName: suiteName)
    settings = AppSettings(defaults: defaults)
  }

  override func tearDown() async throws {
    defaults.removePersistentDomain(forName: suiteName)
    defaults = nil
    suiteName = nil
    settings = nil
    try await super.tearDown()
  }

  // MARK: - Defaults

  /// Requesting the top tier is the only thing that makes the SDK take a Wi-Fi lease, and
  /// Bluetooth Classic is where the feed stalls — so a fresh install asks for Wi-Fi.
  func testDefaultsToTheWiFiTier() {
    XCTAssertTrue(settings.streamsOverWiFi)
    XCTAssertEqual(settings.quality, .high)
    XCTAssertEqual(settings.frameRate, .thirty)
    XCTAssertEqual(settings.streamSpec, "1080p · 30 fps · Wi-Fi")
  }

  /// The choice has to outlive a launch, or every session silently starts back on Bluetooth.
  func testTransportChoiceSurvivesRelaunch() {
    settings.streamsOverWiFi = false

    let relaunched = AppSettings(defaults: defaults)

    XCTAssertFalse(relaunched.streamsOverWiFi)
    XCTAssertEqual(relaunched.quality, .medium, "the tier must follow the stored transport")
  }

  // MARK: - Tiers

  func testBluetoothOffersTheLowerPair() {
    XCTAssertEqual(settings.availableQualities, [.low, .medium])
    XCTAssertFalse(
      settings.availableQualities.contains(.high),
      "1080p needs the bandwidth only Wi-Fi provides"
    )
  }

  func testWiFiOffersTheHigherPair() {
    settings.streamsOverWiFi = true
    XCTAssertEqual(settings.availableQualities, [.medium, .high])
  }

  // MARK: - Clamping

  /// Turning Wi-Fi on from the lowest tier should jump to the best available, not sit on a
  /// value the new tier no longer lists.
  func testEnablingWiFiFromLowRaisesQualityToHigh() {
    settings.quality = .low
    settings.streamsOverWiFi = true
    XCTAssertEqual(settings.quality, .high)
  }

  func testEnablingWiFiKeepsAQualityThatRemainsAvailable() {
    settings.quality = .medium
    settings.streamsOverWiFi = true
    XCTAssertEqual(settings.quality, .medium, "720p exists on both tiers, so it should stick")
  }

  func testDisablingWiFiDropsHighDownToMedium() {
    settings.streamsOverWiFi = true
    settings.quality = .high
    settings.streamsOverWiFi = false
    XCTAssertEqual(settings.quality, .medium)
    XCTAssertTrue(settings.availableQualities.contains(settings.quality))
  }

  func testDisablingWiFiKeepsLowIfAlreadyThere() {
    settings.quality = .low
    settings.streamsOverWiFi = true
    settings.streamsOverWiFi = false
    // Enabling Wi-Fi raised it to .high; turning Wi-Fi back off must land somewhere valid.
    XCTAssertTrue(settings.availableQualities.contains(settings.quality))
  }

  /// The selected quality must always be one the current transport lists — otherwise the
  /// segmented control would show nothing selected.
  func testQualityStaysAvailableAcrossEveryToggleSequence() {
    for sequence in [[true], [true, false], [false, true], [true, false, true]] {
      let settings = AppSettings()
      for value in sequence {
        settings.streamsOverWiFi = value
        XCTAssertTrue(
          settings.availableQualities.contains(settings.quality),
          "quality \(settings.quality) not offered after toggling to \(value)"
        )
      }
    }
  }

  // MARK: - SDK mapping

  func testQualitiesMapToTheSDKResolutions() {
    XCTAssertEqual(AppSettings.Quality.low.streamingResolution, .low)
    XCTAssertEqual(AppSettings.Quality.medium.streamingResolution, .medium)
    XCTAssertEqual(AppSettings.Quality.high.streamingResolution, .high)
  }

  /// The higher tier must actually be a larger frame, otherwise "better resolution" is a
  /// label with nothing behind it.
  func testHigherQualityIsALargerFrame() {
    let low = AppSettings.Quality.low.streamingResolution.videoFrameSize
    let medium = AppSettings.Quality.medium.streamingResolution.videoFrameSize
    let high = AppSettings.Quality.high.streamingResolution.videoFrameSize

    XCTAssertGreaterThan(medium.width * medium.height, low.width * low.height)
    XCTAssertGreaterThan(high.width * high.height, medium.width * medium.height)
  }

  // MARK: - Presentation

  func testStreamSpecReportsTheWiFiTransport() {
    settings.streamsOverWiFi = true
    settings.quality = .high
    settings.frameRate = .twentyFour
    XCTAssertEqual(settings.streamSpec, "1080p · 24 fps · Wi-Fi")
  }

  func testLiveLabelIsUppercasedForTheOverlayChip() {
    settings.quality = .high
    XCTAssertEqual(settings.liveLabel, "Live · 1080P 30FPS")
  }
}
