/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

@testable import AssemblyMan
import XCTest

/// Covers the rules that keep the frame-rate ladder from oscillating.
///
/// Each step restarts the glasses stream, so the interesting cases are not "does it step down"
/// but every case where it must not: a brief dip, a step already taken, the stall recovery
/// already restarting, the floor. This codebase has had a restart loop before, and the ladder
/// is a second thing that restarts on its own schedule.
final class FrameRateLadderTests: XCTestCase {

  private let start = Date(timeIntervalSince1970: 1_000_000)

  /// Feeds `seconds` of downscaled evidence one second apart, returning every step it took.
  @discardableResult
  private func run(
    _ ladder: inout FrameRateLadder,
    downscaledFor seconds: Int,
    from: Date? = nil,
    isRecovering: Bool = false
  ) -> [UInt] {
    let base = from ?? start
    var steps: [UInt] = []
    for second in 0..<seconds {
      let now = base.addingTimeInterval(TimeInterval(second))
      if let step = ladder.observe(isDownscaled: true, isRecovering: isRecovering, now: now) {
        steps.append(step)
      }
    }
    return steps
  }

  // MARK: - Stepping down

  func testHoldsTheChosenRateWhileTheTierArrivesInFull() {
    var ladder = FrameRateLadder(startingAt: 30)
    for second in 0..<60 {
      let step = ladder.observe(
        isDownscaled: false,
        isRecovering: false,
        now: start.addingTimeInterval(TimeInterval(second))
      )
      XCTAssertNil(step, "a healthy feed must never move the ladder")
    }
    XCTAssertEqual(ladder.current, 30)
    XCTAssertFalse(ladder.hasStepped)
  }

  func testStepsDownOnlyAfterTheToleranceHasElapsed() {
    var ladder = FrameRateLadder(startingAt: 30)

    let short = run(&ladder, downscaledFor: FrameRateLadder.toleranceSeconds - 1)
    XCTAssertEqual(short, [], "a dip shorter than the tolerance is the SDK's own ladder working")
    XCTAssertEqual(ladder.current, 30)

    let step = ladder.observe(
      isDownscaled: true,
      isRecovering: false,
      now: start.addingTimeInterval(TimeInterval(FrameRateLadder.toleranceSeconds))
    )
    XCTAssertEqual(step, 24)
    XCTAssertEqual(ladder.current, 24)
    XCTAssertTrue(ladder.hasStepped)
  }

  /// A run of bad seconds broken by a good one starts over — otherwise a feed that dips once a
  /// minute would accumulate its way to a step it never earned.
  func testAHealthySecondResetsTheRunOfEvidence() {
    var ladder = FrameRateLadder(startingAt: 30)
    run(&ladder, downscaledFor: FrameRateLadder.toleranceSeconds - 1)

    XCTAssertNil(ladder.observe(isDownscaled: false, isRecovering: false, now: start))
    let steps = run(
      &ladder,
      downscaledFor: FrameRateLadder.toleranceSeconds - 1,
      from: start.addingTimeInterval(100)
    )

    XCTAssertEqual(steps, [], "the run restarted, so the tolerance has not been met again")
    XCTAssertEqual(ladder.current, 30)
  }

  // MARK: - Not oscillating

  func testWaitsOutTheCooldownBeforeSteppingAgain() {
    var ladder = FrameRateLadder(startingAt: 30)
    run(&ladder, downscaledFor: FrameRateLadder.toleranceSeconds)
    XCTAssertEqual(ladder.current, 24)

    // Enough fresh evidence for a second step, but inside the cooldown.
    let tooSoon = run(
      &ladder,
      downscaledFor: FrameRateLadder.toleranceSeconds,
      from: start.addingTimeInterval(1)
    )
    XCTAssertEqual(tooSoon, [], "a restarted stream has not had time to be judged yet")
    XCTAssertEqual(ladder.current, 24)

    let afterCooldown = run(
      &ladder,
      downscaledFor: FrameRateLadder.toleranceSeconds,
      from: start.addingTimeInterval(FrameRateLadder.cooldownSeconds + 1)
    )
    XCTAssertEqual(afterCooldown, [15])
    XCTAssertEqual(ladder.current, 15)
  }

  func testStopsAtTheFloorHoweverBadTheFeedGets() {
    var ladder = FrameRateLadder(startingAt: 30)
    var now = start
    // Far more evidence, and far more time, than two steps need.
    for round in 0..<10 {
      now = start.addingTimeInterval(TimeInterval(round) * (FrameRateLadder.cooldownSeconds + 30))
      run(&ladder, downscaledFor: FrameRateLadder.toleranceSeconds + 2, from: now)
    }

    XCTAssertEqual(ladder.current, FrameRateLadder.floor)
    XCTAssertEqual(ladder.steps, FrameRateLadder.maxSteps)
  }

  /// The ladder is one-way. Climbing back would rediscover the ceiling and step down again,
  /// which is an oscillation with a stream restart on every swing.
  func testNeverClimbsBackWhenTheFeedRecovers() {
    var ladder = FrameRateLadder(startingAt: 30)
    run(&ladder, downscaledFor: FrameRateLadder.toleranceSeconds)
    XCTAssertEqual(ladder.current, 24)

    for second in 0..<120 {
      XCTAssertNil(ladder.observe(
        isDownscaled: false,
        isRecovering: false,
        now: start.addingTimeInterval(TimeInterval(200 + second))
      ))
    }
    XCTAssertEqual(ladder.current, 24, "a recovered feed must not restore the rate")
  }

  /// The stall recovery restarts the stream and lowers both values itself. If the ladder also
  /// counted those seconds it would answer the recovery's own restarts with more restarts.
  func testStandsDownEntirelyWhileTheStallRecoveryIsRunning() {
    var ladder = FrameRateLadder(startingAt: 30)

    let steps = run(&ladder, downscaledFor: FrameRateLadder.toleranceSeconds * 3, isRecovering: true)

    XCTAssertEqual(steps, [])
    XCTAssertEqual(ladder.current, 30)
    XCTAssertEqual(ladder.steps, 0)
  }

  /// Recovery must not bank evidence either: the seconds it swallowed cannot count towards the
  /// next step once it finishes.
  func testEvidenceGatheredDuringRecoveryDoesNotCarryOver() {
    var ladder = FrameRateLadder(startingAt: 30)
    run(&ladder, downscaledFor: FrameRateLadder.toleranceSeconds * 2, isRecovering: true)

    let afterwards = run(
      &ladder,
      downscaledFor: FrameRateLadder.toleranceSeconds - 1,
      from: start.addingTimeInterval(100)
    )
    XCTAssertEqual(afterwards, [], "the ladder must earn its evidence after recovery ends")
  }

  // MARK: - Starting points

  func testAnOperatorAlreadyOnTwentyFourStepsStraightToTheFloor() {
    var ladder = FrameRateLadder(startingAt: 24)
    let steps = run(&ladder, downscaledFor: FrameRateLadder.toleranceSeconds)

    XCTAssertEqual(steps, [15])
    XCTAssertEqual(ladder.current, 15)
  }

  func testAnOperatorAlreadyAtTheFloorNeverRestartsTheStream() {
    var ladder = FrameRateLadder(startingAt: FrameRateLadder.floor)
    let steps = run(&ladder, downscaledFor: FrameRateLadder.toleranceSeconds * 4)

    XCTAssertEqual(steps, [], "there is nowhere below the floor, so nothing should restart")
    XCTAssertEqual(ladder.current, FrameRateLadder.floor)
    XCTAssertFalse(ladder.hasStepped)
  }

  func testNextRateBelowFollowsTheSDKsValidValues() {
    XCTAssertEqual(FrameRateLadder.next(below: 30), 24)
    XCTAssertEqual(FrameRateLadder.next(below: 24), 15)
    XCTAssertNil(FrameRateLadder.next(below: 15))
    XCTAssertNil(FrameRateLadder.next(below: 7))
  }
}
