/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

@testable import AssemblyMan
import Foundation
import SwiftUI
import UIKit
import XCTest

/// Configuration-contract tests for the app bundle.
///
/// Nothing below is enforced by the compiler. The URL scheme, the `MWDAT` dictionary, and
/// the asset-catalog names are all string lookups resolved at runtime, so a half-finished
/// rename still builds — and then fails at registration time or renders a blank image.
/// These tests pin the contract described in `docs/system.md` §6.
final class AppConfigurationTests: XCTestCase {

  private static let appName = "AssemblyMan"
  private static let urlScheme = "assemblyman"

  private func infoDictionary() throws -> [String: Any] {
    try XCTUnwrap(Bundle.main.infoDictionary, "App bundle is missing its Info.plist")
  }

  private func mwdatDictionary() throws -> [String: Any] {
    try XCTUnwrap(
      infoDictionary()["MWDAT"] as? [String: Any],
      "Info.plist is missing the required MWDAT dictionary"
    )
  }

  /// Every scheme declared under `CFBundleURLTypes`.
  private func declaredURLSchemes() throws -> [String] {
    let urlTypes = try XCTUnwrap(
      infoDictionary()["CFBundleURLTypes"] as? [[String: Any]],
      "Info.plist is missing CFBundleURLTypes"
    )
    return urlTypes.flatMap { $0["CFBundleURLSchemes"] as? [String] ?? [] }
  }

  // MARK: - Registration contract

  /// `MWDAT.AppLinkURLScheme` is the scheme Meta AI calls back on, but iOS only delivers
  /// that callback if the same scheme is also registered in `CFBundleURLTypes`. Renaming
  /// one without the other breaks registration silently: the handoff to Meta AI succeeds
  /// and the app simply never receives the return URL.
  func testAppLinkURLSchemeIsDeclaredInBundleURLTypes() throws {
    let appLink = try XCTUnwrap(
      mwdatDictionary()["AppLinkURLScheme"] as? String,
      "MWDAT dictionary is missing AppLinkURLScheme"
    )

    XCTAssertTrue(
      appLink.hasSuffix("://"),
      "AppLinkURLScheme should be a full scheme prefix ending in '://', got \(appLink)"
    )

    let scheme = String(appLink.dropLast(3))
    XCTAssertEqual(scheme, Self.urlScheme)
    XCTAssertTrue(
      try declaredURLSchemes().contains(scheme),
      "AppLinkURLScheme '\(scheme)' is not declared in CFBundleURLTypes — "
        + "Meta AI's registration callback will never reach the app"
    )
  }

  /// `RegistrationView` only forwards URLs carrying a `metaWearablesAction` query item, and
  /// only the app's own scheme is routed to it. Both halves have to line up for a callback
  /// shaped like the one Meta AI sends to be recognised.
  func testRegistrationCallbackURLUsesTheDeclaredScheme() throws {
    let callback = try XCTUnwrap(
      URL(string: "\(Self.urlScheme)://auth?metaWearablesAction=registration"),
      "Could not build a callback URL for scheme \(Self.urlScheme)"
    )

    XCTAssertTrue(
      try declaredURLSchemes().contains(XCTUnwrap(callback.scheme)),
      "The app does not declare the scheme of its own registration callback"
    )

    let components = try XCTUnwrap(URLComponents(url: callback, resolvingAgainstBaseURL: false))
    XCTAssertTrue(
      components.queryItems?.contains(where: { $0.name == "metaWearablesAction" }) == true,
      "Callback URL is missing the metaWearablesAction query item RegistrationView filters on"
    )
  }

  // MARK: - Bundle identity

  func testBundleNameMatchesRenamedApp() throws {
    XCTAssertEqual(try infoDictionary()["CFBundleName"] as? String, Self.appName)
  }

  /// Catches a partial rename: any surviving `cameraaccess` in the bundle identity means
  /// the old name is still baked into the shipped app.
  func testBundleIdentityCarriesNoStaleAppName() throws {
    let identity: [String?] = [
      try infoDictionary()["CFBundleName"] as? String,
      Bundle.main.bundleIdentifier,
      try declaredURLSchemes().joined(separator: ","),
      try mwdatDictionary()["AppLinkURLScheme"] as? String,
    ]

    for value in identity.compactMap({ $0 }) {
      XCTAssertFalse(
        value.lowercased().contains("cameraaccess"),
        "Stale pre-rename identifier in bundle configuration: \(value)"
      )
    }
  }

  // MARK: - Required configuration

  func testMWDATDictionaryDeclaresRequiredKeys() throws {
    let mwdat = try mwdatDictionary()
    for key in ["AppLinkURLScheme", "MetaAppID", "ClientToken", "TeamID"] {
      XCTAssertNotNil(mwdat[key], "MWDAT dictionary is missing required key \(key)")
    }
  }

  func testBackgroundModesSupportDeviceSessions() throws {
    let modes = try XCTUnwrap(
      infoDictionary()["UIBackgroundModes"] as? [String],
      "Info.plist is missing UIBackgroundModes"
    )
    for mode in ["processing", "bluetooth-central", "bluetooth-peripheral"] {
      XCTAssertTrue(modes.contains(mode), "UIBackgroundModes is missing \(mode)")
    }
  }

  /// The Wi-Fi transport added in 0.8.0 needs both of these or device discovery fails.
  func testLocalNetworkDiscoveryIsConfigured() throws {
    let info = try infoDictionary()
    XCTAssertNotNil(info["NSLocalNetworkUsageDescription"])
    XCTAssertEqual(info["NSBonjourServices"] as? [String], ["_bonjour._tcp"])
  }

  // MARK: - Asset catalog

  /// Asset symbols compile against the catalog but resolve by name at runtime: renaming the
  /// `.imageset` directory without renaming the contained file leaves a symbol that builds
  /// and then draws nothing.
  func testRenamedBrandAssetResolves() {
    XCTAssertNotNil(
      UIImage(resource: .assemblyManIcon),
      "assemblyManIcon did not resolve — the imageset or its contents are misnamed"
    )
  }

  func testUnrenamedAssetsSurvivedTheRename() {
    let assets: [ImageResource] = [.smartGlassesIcon, .soundIcon, .tapIcon, .videoIcon, .walkingIcon]
    for asset in assets {
      XCTAssertNotNil(UIImage(resource: asset), "Asset \(asset) did not resolve")
    }
  }
  // MARK: - MobileSAM

  func testMobileSAMResourcesAreBundled() {
    XCTAssertNotNil(
      Bundle.main.url(forResource: "mobile_sam_encoder", withExtension: "mlmodelc")
    )
    XCTAssertNotNil(
      Bundle.main.url(forResource: "mobile_sam_decoder", withExtension: "mlmodelc")
    )
    XCTAssertNotNil(
      Bundle.main.url(
        forResource: "mobile_sam_prompt_encoder_weights",
        withExtension: "json"
      )
    )
  }

  func testMobileSAMProducesReticleAndFullFrameOverlays() async throws {
    let imageURL = try XCTUnwrap(
      Bundle.main.url(forResource: "plant", withExtension: "png")
    )
    let sourceImage = try XCTUnwrap(
      UIImage(contentsOfFile: imageURL.path)?.cgImage
    )

    let processor = MobileSAMProcessor()
    for targetMode in MobileSAMTargetMode.allCases {
      let result = await processor.makeOverlay(
        for: sourceImage,
        targetMode: targetMode
      )
      switch result {
      case .success(let overlay, let inferenceMilliseconds):
        XCTAssertGreaterThan(overlay.size.width, 0, targetMode.label)
        XCTAssertGreaterThan(overlay.size.height, 0, targetMode.label)
        XCTAssertGreaterThanOrEqual(inferenceMilliseconds, 0, targetMode.label)
      case .failure(let message):
        XCTFail("MobileSAM \(targetMode.label) inference failed: \(message)")
      }
    }
  }

  func testMobileSAMTargetModesCoverCenterAndFullFrameGrid() throws {
    let reticlePoints = MobileSAMTargetMode.reticle.promptPoints(
      imageWidth: 600,
      imageHeight: 900
    )
    let reticlePoint = try XCTUnwrap(reticlePoints.first)
    XCTAssertEqual(reticlePoints.count, 1)
    XCTAssertEqual(reticlePoint.x, 300, accuracy: 0.001)
    XCTAssertEqual(reticlePoint.y, 450, accuracy: 0.001)

    let fullFramePoints = MobileSAMTargetMode.fullFrame.promptPoints(
      imageWidth: 600,
      imageHeight: 900
    )
    XCTAssertEqual(fullFramePoints.count, 9)
    XCTAssertEqual(try XCTUnwrap(fullFramePoints.map(\.x).min()), 100, accuracy: 0.001)
    XCTAssertEqual(try XCTUnwrap(fullFramePoints.map(\.x).max()), 500, accuracy: 0.001)
    XCTAssertEqual(try XCTUnwrap(fullFramePoints.map(\.y).min()), 150, accuracy: 0.001)
    XCTAssertEqual(try XCTUnwrap(fullFramePoints.map(\.y).max()), 750, accuracy: 0.001)
  }

  func testMobileSAMFrameRatesMapToProcessingIntervals() {
    XCTAssertEqual(VisionFrameRate.half.interval, .seconds(2))
    XCTAssertEqual(VisionFrameRate.one.interval, .seconds(1))
    XCTAssertEqual(VisionFrameRate.two.interval, .milliseconds(500))
    XCTAssertEqual(VisionFrameRate.five.interval, .milliseconds(200))
    XCTAssertEqual(VisionFrameRate.ten.interval, .milliseconds(100))
    XCTAssertEqual(VisionFrameRate.fifteen.interval, .milliseconds(67))
  }

  // MARK: - Ultralytics YOLO

  func testYOLONanoModelsAreBundled() {
    for resource in ["yolo26n", "yolo26n-seg", "yolo26n-sem"] {
      XCTAssertNotNil(
        Bundle.main.url(forResource: resource, withExtension: "mlmodelc"),
        "\(resource) is missing from the app bundle"
      )
    }
  }

  func testYOLOColorMapIncludesRequestedClassesOnly() {
    XCTAssertEqual(YOLOOverlayClass.mappedClass(for: "road"), .floor)
    XCTAssertEqual(YOLOOverlayClass.mappedClass(for: "sidewalk"), .floor)
    XCTAssertEqual(YOLOOverlayClass.mappedClass(for: "terrain"), .floor)
    XCTAssertEqual(YOLOOverlayClass.mappedClass(for: "wall"), .wall)
    XCTAssertEqual(YOLOOverlayClass.mappedClass(for: "person"), .person)
    XCTAssertEqual(YOLOOverlayClass.mappedClass(for: "laptop"), .laptop)
    XCTAssertEqual(YOLOOverlayClass.mappedClass(for: "dining table"), .table)
    XCTAssertNil(YOLOOverlayClass.mappedClass(for: "chair"))
    XCTAssertNil(YOLOOverlayClass.mappedClass(for: "car"))
    XCTAssertNil(YOLOOverlayClass.mappedClass(for: "background"))
  }

  func testYOLOProducesOverlaysForEachLiveTask() async throws {
    let imageURL = try XCTUnwrap(
      Bundle.main.url(forResource: "plant", withExtension: "png")
    )
    let sourceImage = try XCTUnwrap(
      UIImage(contentsOfFile: imageURL.path)?.cgImage
    )
    let processor = YOLOProcessor()

    for mode in VisionOverlayMode.allCases where !mode.usesMobileSAM {
      let result = await processor.makeOverlay(for: sourceImage, mode: mode)
      switch result {
      case .success(let overlay, let inferenceMilliseconds, let coloredRegions):
        XCTAssertEqual(Int(overlay.size.width), sourceImage.width, mode.label)
        XCTAssertEqual(Int(overlay.size.height), sourceImage.height, mode.label)
        XCTAssertGreaterThanOrEqual(inferenceMilliseconds, 0, mode.label)
        XCTAssertGreaterThanOrEqual(coloredRegions, 0, mode.label)
      case .failure(let message):
        XCTFail("\(mode.label) inference failed: \(message)")
      }
    }
  }
}
