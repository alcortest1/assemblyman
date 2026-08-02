/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// RoomCodeTests.swift
//
// The room code is read aloud and typed in by someone else, so its alphabet and its two
// representations — what a person sees, and what LiveKit is asked for — are a contract.
//

import XCTest

@testable import AssemblyMan

final class RoomCodeTests: XCTestCase {

  // MARK: - Generation

  func testGeneratedCodeIsSixCharacters() {
    for _ in 0..<200 {
      XCTAssertEqual(RoomCode.random().raw.count, RoomCode.length)
    }
  }

  func testAlphabetExcludesGlyphsThatAreMisreadAloud() {
    // I/L/O and 0/1 are the pairs people confuse when reading a code over a radio or off a
    // screen at arm's length.
    for character in "ILO01" {
      XCTAssertFalse(
        RoomCode.alphabet.contains(character),
        "\(character) is too easily confused to appear in a spoken code"
      )
    }
  }

  func testGeneratedCodesOnlyUseTheAlphabet() {
    for _ in 0..<200 {
      for character in RoomCode.random().raw {
        XCTAssertTrue(RoomCode.alphabet.contains(character))
      }
    }
  }

  func testGenerationVaries() {
    // Not a randomness test — just a guard against a constant slipping in.
    let codes = Set((0..<50).map { _ in RoomCode.random().raw })
    XCTAssertGreaterThan(codes.count, 1)
  }

  // MARK: - Representations

  func testDisplayFormIsGroupedForReadingAloud() throws {
    let code = try XCTUnwrap(RoomCode(userEntered: "ABCDEF"))
    XCTAssertEqual(code.display, "ABC-DEF")
  }

  func testRoomNameDropsTheSeparator() throws {
    // The dash is presentation only. Anything joining by code has to normalise the same way
    // or it lands in a different room than the one on screen.
    let code = try XCTUnwrap(RoomCode(userEntered: "ABC-DEF"))
    XCTAssertEqual(code.roomName, "ABCDEF")
    XCTAssertFalse(code.roomName.contains("-"))
  }

  // MARK: - Parsing what someone typed

  func testParsingToleratesCaseSpacingAndSeparators() throws {
    let expected = "ABCDEF"
    for input in ["ABCDEF", "abcdef", "ABC-DEF", "abc-def", " ABC DEF ", "a-b-c-d-e-f"] {
      let code = try XCTUnwrap(RoomCode(userEntered: input), "should parse \(input)")
      XCTAssertEqual(code.roomName, expected, "\(input) should normalise to \(expected)")
    }
  }

  func testParsingRejectsAPartialCode() {
    // A short code is a typo, not a room — joining a truncated one would silently land the
    // viewer somewhere else.
    XCTAssertNil(RoomCode(userEntered: "ABC"))
    XCTAssertNil(RoomCode(userEntered: "AB-CD"))
    XCTAssertNil(RoomCode(userEntered: ""))
  }

  func testParsingRejectsAnOversizedCode() {
    XCTAssertNil(RoomCode(userEntered: "ABCDEFG"))
    XCTAssertNil(RoomCode(userEntered: "ABC-DEF-G"))
  }

  func testParsingRejectsCodesMadeOnlyOfExcludedGlyphs() {
    XCTAssertNil(RoomCode(userEntered: "OOO111"))
  }

  func testRoundTripsThroughItsOwnDisplayForm() {
    for _ in 0..<100 {
      let original = RoomCode.random()
      XCTAssertEqual(RoomCode(userEntered: original.display), original)
    }
  }
}
