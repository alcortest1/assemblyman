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
}
