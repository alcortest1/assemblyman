/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// LiveKitTokenMinterTests.swift
//
// The token is signed on-device and rejected by the server with an opaque 401 when anything
// about its encoding is wrong, so these tests pin the details that are easy to get subtly
// wrong: the signing key, the base64url alphabet, the absent padding, and the claim shape.
//

import CryptoKit
import XCTest

@testable import AssemblyMan

final class LiveKitTokenMinterTests: XCTestCase {

  private let apiKey = "APItestkey"
  private let apiSecret = "shhh-this-is-the-secret"

  private var minter: LiveKitTokenMinter {
    LiveKitTokenMinter(apiKey: apiKey, apiSecret: apiSecret)
  }

  private func grant() -> LiveKitTokenMinter.Grant {
    LiveKitTokenMinter.Grant(
      roomName: "ABCDEF",
      identity: "phone-ABCDEF",
      displayName: "Operator",
      metadata: #"{"agent":"assistant"}"#
    )
  }

  // MARK: - Structure

  func testTokenHasThreeBase64URLSegments() throws {
    let token = try minter.mint(grant())
    let segments = token.split(separator: ".", omittingEmptySubsequences: false)
    XCTAssertEqual(segments.count, 3)

    for segment in segments {
      XCTAssertFalse(segment.isEmpty)
      // Padding must be stripped and the URL-safe alphabet used — a stock base64 encoder
      // emits "+", "/" and "=", all of which make the token invalid.
      XCTAssertFalse(segment.contains("="), "base64url segments carry no padding")
      XCTAssertFalse(segment.contains("+"), "base64url uses - rather than +")
      XCTAssertFalse(segment.contains("/"), "base64url uses _ rather than /")
    }
  }

  func testHeaderDeclaresHS256() throws {
    let token = try minter.mint(grant())
    let header = try decodeSegment(token, index: 0)
    XCTAssertEqual(header["alg"] as? String, "HS256")
    XCTAssertEqual(header["typ"] as? String, "JWT")
  }

  // MARK: - Claims

  func testClaimsCarryIssuerIdentityAndVideoGrant() throws {
    let token = try minter.mint(grant())
    let claims = try decodeSegment(token, index: 1)

    XCTAssertEqual(claims["iss"] as? String, apiKey)
    XCTAssertEqual(claims["sub"] as? String, "phone-ABCDEF")
    XCTAssertEqual(claims["name"] as? String, "Operator")
    XCTAssertEqual(claims["metadata"] as? String, #"{"agent":"assistant"}"#)

    let video = try XCTUnwrap(claims["video"] as? [String: Any])
    XCTAssertEqual(video["room"] as? String, "ABCDEF")
    XCTAssertEqual(video["roomJoin"] as? Bool, true)
    XCTAssertEqual(video["canPublish"] as? Bool, true)
    // The assistant replies with audio, so the relay has to be able to subscribe.
    XCTAssertEqual(video["canSubscribe"] as? Bool, true)
  }

  func testNotBeforeIsBackdatedAndExpiryHonoursTTL() throws {
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    var grant = grant()
    grant.ttl = 3600

    let claims = try decodeSegment(try minter.mint(grant, now: now), index: 1)
    let nbf = try XCTUnwrap(claims["nbf"] as? Int)
    let exp = try XCTUnwrap(claims["exp"] as? Int)

    // Back-dated so a device clock running slightly fast is not rejected outright.
    XCTAssertLessThan(nbf, Int(now.timeIntervalSince1970))
    XCTAssertEqual(exp, Int(now.timeIntervalSince1970) + 3600)
  }

  func testDefaultTTLOutlastsALongSession() throws {
    // LiveKit reuses the original token when it reconnects, so a token that expires during a
    // session turns the next network blip into a permanent disconnect.
    XCTAssertGreaterThanOrEqual(LiveKitTokenMinter.Grant(roomName: "R", identity: "i").ttl, 3600 * 4)
  }

  // MARK: - Signature

  func testSignatureIsHMACOfTheRawSecretBytes() throws {
    let token = try minter.mint(grant())
    let segments = token.split(separator: ".")
    let signingInput = "\(segments[0]).\(segments[1])"

    // Recomputed independently. The key is the secret's UTF-8 bytes — base64-decoding it
    // first is the classic mistake, and produces a token the server rejects.
    let expected = HMAC<SHA256>.authenticationCode(
      for: Data(signingInput.utf8),
      using: SymmetricKey(data: Data(apiSecret.utf8))
    )
    let expectedSegment = Data(expected).base64EncodedString()
      .replacingOccurrences(of: "+", with: "-")
      .replacingOccurrences(of: "/", with: "_")
      .replacingOccurrences(of: "=", with: "")

    XCTAssertEqual(String(segments[2]), expectedSegment)
  }

  func testADifferentSecretProducesADifferentSignature() throws {
    let a = try minter.mint(grant())
    let b = try LiveKitTokenMinter(apiKey: apiKey, apiSecret: "other").mint(grant())
    XCTAssertNotEqual(a.split(separator: ".")[2], b.split(separator: ".")[2])
  }

  // MARK: - Refusals

  func testMissingCredentialsThrows() {
    XCTAssertThrowsError(try LiveKitTokenMinter(apiKey: "", apiSecret: "s").mint(grant()))
    XCTAssertThrowsError(try LiveKitTokenMinter(apiKey: "k", apiSecret: "").mint(grant()))
  }

  func testEmptyIdentityThrows() {
    var grant = grant()
    grant.identity = ""
    // LiveKit rejects a join with no identity, so catching it here beats an opaque failure
    // at connect time.
    XCTAssertThrowsError(try minter.mint(grant))
  }

  // MARK: - Helpers

  private func decodeSegment(_ token: String, index: Int) throws -> [String: Any] {
    let segment = String(token.split(separator: ".", omittingEmptySubsequences: false)[index])
    var base64 = segment
      .replacingOccurrences(of: "-", with: "+")
      .replacingOccurrences(of: "_", with: "/")
    // Put back the padding the encoder stripped.
    while base64.count % 4 != 0 { base64 += "=" }

    let data = try XCTUnwrap(Data(base64Encoded: base64))
    return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
  }
}
