/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

import XCTest

@testable import AssemblyMan

@MainActor
final class LiveKitRelayTests: XCTestCase {

  func testConfiguredIdleRelayIsReady() {
    let relay = LiveKitRelay(
      configuration: LiveKitConfiguration(
        host: "example.livekit.cloud",
        apiKey: "key",
        apiSecret: "secret"
      )
    )

    XCTAssertEqual(relay.relayLabel, "Ready")
  }

  func testUnconfiguredIdleRelaySaysItIsNotConfigured() {
    let relay = LiveKitRelay(
      configuration: LiveKitConfiguration(host: "", apiKey: "", apiSecret: "")
    )

    XCTAssertEqual(relay.relayLabel, "Not configured")
  }
}
