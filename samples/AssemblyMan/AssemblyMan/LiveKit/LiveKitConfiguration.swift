/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// LiveKitConfiguration.swift
//
// Where the relay gets its server and credentials, and how a session names its room.
//
// Credentials come from Config/LiveKit.xcconfig by way of Info.plist, the same mechanism the
// project already uses for MetaAppID and ClientToken. When they are absent the relay reports
// itself unconfigured and the glasses stream carries on exactly as it does without it —
// missing credentials must never break the app's existing behaviour.
//

import Foundation

// MARK: - Configuration

struct LiveKitConfiguration: Sendable {
  let host: String
  let apiKey: String
  let apiSecret: String

  /// The scheme is added here rather than stored. `//` starts a comment in an xcconfig, so a
  /// full `wss://host` value would silently truncate to `wss:` — the file carries the host alone.
  var serverURL: String { "wss://\(host)" }

  var isConfigured: Bool { !host.isEmpty && !apiKey.isEmpty && !apiSecret.isEmpty }

  static func fromBundle(_ bundle: Bundle = .main) -> LiveKitConfiguration {
    func value(_ key: String) -> String {
      let raw = (bundle.object(forInfoDictionaryKey: key) as? String) ?? ""
      // An xcconfig variable that was never set reaches the built plist as the literal
      // "$(LIVEKIT_HOST)" rather than an empty string. Treat that as unconfigured.
      let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
      return trimmed.hasPrefix("$(") ? "" : trimmed
    }
    return LiveKitConfiguration(
      host: value("LKServerHost"),
      apiKey: value("LKAPIKey"),
      apiSecret: value("LKAPISecret")
    )
  }
}

// MARK: - Room code

/// The short code an operator reads out so someone else can join their session.
///
/// Six characters from an alphabet with the ambiguous glyphs removed — no I, L, O, 0 or 1 —
/// so a code survives being spoken aloud or read off a screen at arm's length.
struct RoomCode: Equatable, Hashable, Sendable, CustomStringConvertible {

  static let alphabet = Array("ABCDEFGHJKMNPQRSTUVWXYZ23456789")
  static let length = 6

  /// Canonical form: uppercase, no separator. This is the LiveKit room name.
  let raw: String

  /// What the UI shows and a person reads aloud.
  var display: String { "\(raw.prefix(3))-\(raw.suffix(3))" }

  /// LiveKit room name. Dashes are a display affectation, never part of the identity — any
  /// viewer joining by code must normalise the same way before connecting.
  var roomName: String { raw }

  var description: String { display }

  static func random() -> RoomCode {
    // No server-side reservation: 31^6 is ~887M, so a same-code collision between two
    // simultaneous sessions is possible in principle and would put both in one room.
    // Acceptable for a sample; a real deployment would reserve the name.
    RoomCode(raw: String((0..<length).map { _ in alphabet.randomElement()! }))
  }

  /// Parses a code a person typed, tolerating dashes, spaces and lowercase.
  init?(userEntered text: String) {
    let normalised = text
      .uppercased()
      .filter { RoomCode.alphabet.contains($0) }
    guard normalised.count == RoomCode.length else { return nil }
    self.raw = normalised
  }

  private init(raw: String) {
    self.raw = raw
  }
}
