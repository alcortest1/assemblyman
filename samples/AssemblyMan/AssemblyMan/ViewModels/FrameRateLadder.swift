/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// FrameRateLadder.swift
//
// Decides when a session should give up frames per second to hold its resolution.
//
// The SDK's own ladder lowers resolution first and frame rate second, so a feed arriving
// smaller than the tier that was asked for has already spent step one. Asking again for a
// resolution the link has just shown it cannot carry only buys more compression — the SDK
// documentation is explicit that lower settings yield *higher* visual quality, because there
// is less of it to squeeze. The lever that wins the frame back is the one the SDK reaches for
// last: fewer, larger, less-compressed frames out of the same bandwidth.
//
// It lives apart from the view model because the rules that matter here are the ones that stop
// it oscillating, and those are worth testing without a glasses session in the room. Restarting
// a stream on a timer is how a recovery loop gets built by accident, and this codebase has
// already had one (see "Stop the stall recovery restarting in a loop").
//

import Foundation

/// A one-way ladder from the operator's chosen frame rate down to the SDK's floor.
struct FrameRateLadder: Equatable {

  /// Rates the SDK will hold, from AGENTS.md: 2, 7, 15, 24, 30. Only the top of that range is
  /// useful here — below 15 the picture stops reading as motion, and a student watching their
  /// own hands would rather have a smaller frame than a slideshow.
  static let floor: UInt = 15

  /// How long the feed must arrive small before it counts as the wrong configuration rather
  /// than the SDK's ladder riding out a bad moment.
  static let toleranceSeconds = 8
  /// Two steps is the whole budget: 30 → 24 → 15.
  static let maxSteps = 2
  /// Long enough for a restarted stream to settle and be judged on its own merits.
  static let cooldownSeconds: TimeInterval = 20

  /// The rate the next stream should ask for.
  private(set) var current: UInt
  /// Steps taken this session. Never decreases — the ladder does not climb.
  private(set) var steps = 0

  private let chosen: UInt
  private var downscaledSeconds = 0
  private var lastStepAt: Date?

  init(startingAt rate: UInt) {
    self.chosen = rate
    self.current = rate
  }

  /// True once the session is running below what the operator asked for.
  var hasStepped: Bool { current < chosen }

  /// Feeds in one second of evidence.
  ///
  /// - Parameters:
  ///   - isDownscaled: whether the last frame was smaller than the requested tier.
  ///   - isRecovering: whether the stall recovery is already restarting the stream. The ladder
  ///     stands down entirely while it is: that path lowers both values on its own, and its
  ///     restarts would otherwise be read here as fresh evidence and answered with more.
  ///   - now: injected so the cooldown can be tested without waiting through it.
  /// - Returns: the new rate when it decides to step down, otherwise nil.
  mutating func observe(isDownscaled: Bool, isRecovering: Bool, now: Date) -> UInt? {
    guard !isRecovering else { return nil }

    guard isDownscaled else {
      // The tier is arriving in full. Whatever we settled on is working — hold it rather than
      // climbing back and rediscovering the ceiling.
      downscaledSeconds = 0
      return nil
    }

    downscaledSeconds += 1
    guard downscaledSeconds >= Self.toleranceSeconds else { return nil }
    guard steps < Self.maxSteps else { return nil }
    if let lastStepAt, now.timeIntervalSince(lastStepAt) < Self.cooldownSeconds { return nil }
    guard let next = Self.next(below: current) else { return nil }

    current = next
    steps += 1
    lastStepAt = now
    // Spent, so the next step needs its own run of evidence rather than inheriting this one.
    downscaledSeconds = 0
    return next
  }

  /// The next rate down that the SDK will hold, or nil at the floor.
  static func next(below rate: UInt) -> UInt? {
    if rate > 24 { return 24 }
    if rate > floor { return floor }
    return nil
  }
}
