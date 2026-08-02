/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// LiveKitTokenMinter.swift
//
// Mints the JWT the app presents when it joins a LiveKit room. The LiveKit SDK ships no
// token generation of its own, so the signing happens here.
//
// ⚠️ THIS SIGNS WITH THE API SECRET ON THE DEVICE, which means the secret ships inside the
// app binary and anyone with the .ipa can extract it and mint tokens for the project. That
// is a deliberate tradeoff for a local sample: it needs no server. Do not carry this pattern
// into a shipping app — there, a backend holds the secret and hands out short-lived tokens,
// which is what the SDK's `TokenSource` protocols are shaped for.
//

import CryptoKit
import Foundation

struct LiveKitTokenMinter: Sendable {

  let apiKey: String
  let apiSecret: String

  struct Grant: Sendable {
    var roomName: String
    var identity: String
    var displayName: String?
    /// Arbitrary JSON handed to other participants — the agent reads the operator's
    /// selected assistant out of this.
    var metadata: String?
    var canPublish: Bool = true
    var canSubscribe: Bool = true
    var canPublishData: Bool = true
    /// Six hours. LiveKit reuses the original token when it reconnects, so a short TTL
    /// turns the first network blip after expiry into a permanent disconnect.
    var ttl: TimeInterval = 6 * 60 * 60
  }

  enum MintError: Error, LocalizedError {
    case missingCredentials
    case emptyIdentity

    var errorDescription: String? {
      switch self {
      case .missingCredentials:
        return "LiveKit credentials are missing. Add Config/LiveKit.local.xcconfig."
      case .emptyIdentity:
        return "A LiveKit token needs a non-empty participant identity."
      }
    }
  }

  func mint(_ grant: Grant, now: Date = Date()) throws -> String {
    guard !apiKey.isEmpty, !apiSecret.isEmpty else { throw MintError.missingCredentials }
    guard !grant.identity.isEmpty else { throw MintError.emptyIdentity }

    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]

    let claims = Claims(
      iss: apiKey,
      sub: grant.identity,
      // Back-dated so a device clock a few seconds fast doesn't get "token not valid yet".
      nbf: Int(now.timeIntervalSince1970) - 30,
      exp: Int(now.addingTimeInterval(grant.ttl).timeIntervalSince1970),
      name: grant.displayName,
      metadata: grant.metadata,
      video: VideoGrant(
        roomJoin: true,
        room: grant.roomName,
        canPublish: grant.canPublish,
        canSubscribe: grant.canSubscribe,
        canPublishData: grant.canPublishData
      )
    )

    let signingInput = try Self.base64URL(encoder.encode(Header()))
      + "." + Self.base64URL(encoder.encode(claims))

    // The key is the raw UTF-8 bytes of the secret. Base64-decoding it first is the classic
    // way to get a signature that looks fine and is rejected with an opaque 401.
    let key = SymmetricKey(data: Data(apiSecret.utf8))
    let signature = HMAC<SHA256>.authenticationCode(for: Data(signingInput.utf8), using: key)

    return signingInput + "." + Self.base64URL(Data(signature))
  }

  // MARK: - JWT shape

  private struct Header: Encodable {
    let alg = "HS256"
    let typ = "JWT"
  }

  private struct VideoGrant: Encodable {
    let roomJoin: Bool
    let room: String
    let canPublish: Bool
    let canSubscribe: Bool
    let canPublishData: Bool
  }

  private struct Claims: Encodable {
    let iss: String
    let sub: String
    let nbf: Int
    let exp: Int
    let name: String?
    let metadata: String?
    let video: VideoGrant
  }

  /// base64url per RFC 7515 §2: standard base64 with `+`→`-`, `/`→`_`, and padding removed.
  /// Leaving the `=` padding on is the other classic cause of a rejected token.
  private static func base64URL(_ data: Data) -> String {
    data.base64EncodedString()
      .replacingOccurrences(of: "+", with: "-")
      .replacingOccurrences(of: "/", with: "_")
      .replacingOccurrences(of: "=", with: "")
  }
}

// MARK: - Participant metadata

/// Published with the token so anything else in the room — the agent, a viewer — can tell what
/// this participant is and which assistant the operator picked.
struct RelayMetadata: Codable, Sendable {
  var role: String = "glasses-relay"
  var agent: String
  var agentName: String
  var device: String = "ray-ban-meta"
  var app: String = "assemblyman"

  var jsonString: String? {
    guard let data = try? JSONEncoder().encode(self) else { return nil }
    return String(data: data, encoding: .utf8)
  }
}
