/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// LocalGrader.swift
//
// Grades one still against one rubric on the phone, with no network.
//
// The hosted grader is a single non-realtime call — one image, one rubric, structured reply —
// which is the one shape edge inference is actually good at, and the reason this is the half of
// the assistant that comes off the network first. What it is not is a like-for-like swap:
// LFM2-VL-3B is a 2.6B language model with a 400M vision tower, and Liquid's own card says to
// fine-tune it for narrow use cases and not to use it for safety-critical decisions. Grading a
// Part 147 student's work is narrow and consequential, so two things are load-bearing here:
// `GenerationOptions.setResponseFormat` constrains decoding to the schema, which removes
// malformed JSON as a failure mode entirely; and `GradeAssembler` re-derives the parts a weak
// model gets wrong. The sheet names the model that graded, so a verdict is never anonymous.
//
// @MainActor rather than an actor of its own, unlike MobileSAMProcessor: the download progress
// and readiness here are UI state that Settings binds to directly, and the SDK does its work on
// its own threads either way. LiveKitRelay is the same shape for the same reason.
//

import Foundation
import LeapSDK
import UIKit
import os

/// The model's reply, with decoding constrained to this shape.
///
/// A port of `RESPONSE_SCHEMA` in agent/grading.py. The guides are the schema's descriptions —
/// they are what the model is told each field means, so they are the prompt as much as the
/// system message is.
@Generatable("A verdict for every numbered criterion in the rubric, in order.")
struct LocalGradeReport: Codable {
  @Guide("What the photograph actually shows, under 30 words.")
  var observed: String

  @Guide("One entry per numbered criterion, in the rubric's order. Do not merge, skip or add.")
  var criteria: [Entry]

  @Guide("The rubric's critical defects that are affirmatively visible, quoted from its list.")
  var criticalDefectsSeen: [String]

  @Generatable("One criterion, judged on its own.")
  struct Entry: Codable {
    @Guide("1-based, matching the rubric")
    var index: Int

    @Guide("PASS or FAIL")
    var verdict: String

    @Guide("False when the photo does not show this at all")
    var observable: Bool

    @Guide("Under 20 words, naming what you saw rather than what you assumed")
    var note: String
  }
}

@Observable
@MainActor
final class LocalGrader {

  // MARK: - Configuration

  /// The LEAP model library name and quantization.
  ///
  /// Q8_0 rather than a 4-bit quant because it is what Liquid publishes a LEAP manifest for:
  /// `leap/Q8_0.json` in LiquidAI/LFM2-VL-3B-GGUF pairs the Q8 checkpoint with the Q8 vision
  /// projector, 3.32 GB together. There is a Q4_K_M GGUF at 2.15 GB and no manifest for it, so
  /// reaching for it means authoring and hosting one — worth doing if 3.32 GB proves too much
  /// on device, and not worth doing before it is measured.
  static let modelName = LocalGradingEngine.modelName
  static let quantization = LocalGradingEngine.quantization

  /// What the sheet prints in its footer, so a verdict says where it came from.
  static let modelLabel = LocalGradingEngine.modelLabel

  // MARK: - Observable state

  enum State: Equatable {
    case notLoaded
    /// Fetching 3.32 GB. Progress is 0…1.
    case downloading(progress: Double)
    case loading
    case ready
    case failed(String)
  }

  private(set) var state: State = .notLoaded

  /// Whether a grade can be run right now without a download.
  var isReady: Bool { state == .ready }

  /// Whether the app ships rubrics to grade against at all. Without them the engine has
  /// nothing to say, and the UI should not offer it.
  var hasRubrics: Bool { !catalogue.isEmpty }

  let catalogue = RubricCatalogue.bundled

  // MARK: - Private

  @ObservationIgnored private var runner: (any ModelRunner)?
  @ObservationIgnored private var loadTask: Task<Void, Never>?
  @ObservationIgnored private var memoryWarningTask: Task<Void, Never>?
  @ObservationIgnored private let log =
    Logger(subsystem: "com.alcorlabs.assemblyman", category: "local-grader")

  init() {
    // Several GB of weights is the largest single thing this app holds, and it shares the
    // device with WebRTC encode and two CoreML models. When the system says it is short, the
    // grader is the right thing to give up — it can be reloaded from cache, and being jetsammed
    // mid-session loses the stream as well.
    memoryWarningTask = Task { [weak self] in
      let warnings = NotificationCenter.default.notifications(
        named: UIApplication.didReceiveMemoryWarningNotification
      )
      for await _ in warnings {
        guard let self else { return }
        guard self.runner != nil else { continue }
        self.log.warning("memory warning — unloading the on-device grader")
        await self.unload()
      }
    }
  }

  deinit {
    memoryWarningTask?.cancel()
  }

  // MARK: - Lifecycle

  /// Downloads and loads the model. Explicit, and never called implicitly by a grade: the first
  /// call pulls 3.32 GB, and starting that behind the operator's back — on a shop hotspot, mid
  /// session — is not a thing to do by inference from a settings toggle.
  func prepare() {
    guard runner == nil, loadTask == nil else { return }

    loadTask = Task { [weak self] in
      guard let self else { return }
      self.state = .downloading(progress: 0)
      do {
        let loaded = try await Leap.load(
          model: Self.modelName,
          quantization: Self.quantization,
          downloadProgressHandler: { progress, _ in
            Task { @MainActor [weak self] in
              self?.state = progress < 1.0 ? .downloading(progress: progress) : .loading
            }
          }
        )
        self.runner = loaded
        self.state = .ready
        self.log.info("loaded \(Self.modelName) \(Self.quantization)")
      } catch {
        self.state = .failed(error.localizedDescription)
        self.log.error("could not load model: \(error.localizedDescription, privacy: .public)")
      }
      self.loadTask = nil
    }
  }

  /// Gives the weights back.
  ///
  /// Called on a memory warning and when the operator turns the engine off. This app is also
  /// running WebRTC encode, a Metal compositor at 30 fps and CoreML segmentation, so a few GB
  /// held for a grade nobody is going to ask for is a jetsam risk taken for nothing.
  func unload() async {
    loadTask?.cancel()
    loadTask = nil
    if let runner {
      await runner.unload()
    }
    runner = nil
    state = .notLoaded
  }

  // MARK: - Grading

  /// Grades one JPEG against one rubric. Never throws — errors come back as a grade that says
  /// what went wrong, the same contract the agent's `grade()` keeps.
  func grade(jpeg: Data, rubric: Rubric) async -> GradeProtocol.Grade {
    guard let runner else {
      return GradeAssembler.failure(
        rubric: rubric, taskCode: rubric.taskCode, subtaskCode: rubric.subtaskCode,
        error: "not_loaded",
        message: "The on-device grader is not loaded. Download it in Settings."
      )
    }

    let started = ContinuousClock.now
    do {
      let report = try await withTimeout(LocalGradingEngine.timeout) {
        try await LocalGradingEngine.run(
          runner: runner, jpeg: LocalGradingEngine.downsized(jpeg), rubric: rubric
        )
      }
      let elapsed = ContinuousClock.now - started
      let latency = Double(elapsed.components.seconds)
        + Double(elapsed.components.attoseconds) / 1e18

      return GradeAssembler.assemble(
        report, rubric: rubric, model: LocalGradingEngine.modelLabel,
        latency: (latency * 100).rounded() / 100
      )
    } catch is TimeoutError {
      log.warning("grading timed out")
      return GradeAssembler.failure(
        rubric: rubric, taskCode: rubric.taskCode, subtaskCode: rubric.subtaskCode,
        error: "timeout",
        message: "The on-device grader did not answer within 90 seconds."
      )
    } catch {
      log.warning("grading failed: \(error.localizedDescription, privacy: .public)")
      return GradeAssembler.failure(
        rubric: rubric, taskCode: rubric.taskCode, subtaskCode: rubric.subtaskCode,
        error: "failed", message: String(error.localizedDescription.prefix(300))
      )
    }
  }
}

/// Everything that touches the model.
///
/// Off the main actor deliberately: `LocalGrader` is main-actor-isolated so Settings can bind
/// to its download state directly, and a 3B model generating tokens has no business inheriting
/// that isolation. Splitting them here is what keeps the UI-facing object simple without
/// scattering `nonisolated` over the parts that do the work.
enum LocalGradingEngine {

  /// The LEAP model library name and quantization.
  ///
  /// Q8_0 rather than a 4-bit quant because it is what Liquid publishes a LEAP manifest for:
  /// `leap/Q8_0.json` in LiquidAI/LFM2-VL-3B-GGUF pairs the Q8 checkpoint with the Q8 vision
  /// projector, 3.32 GB together. There is a Q4_K_M GGUF at 2.15 GB and no manifest for it, so
  /// reaching for it means authoring and hosting one — worth doing if 3.32 GB proves too much
  /// on device, and not worth doing before it is measured.
  static let modelName = "LFM2-VL-3B"
  static let quantization = "Q8_0"
  static let modelLabel = "\(modelName) \(quantization) · on device"

  /// LFM2-VL tiles above 512×512 and the tiles cost tokens and seconds without adding detail
  /// this rubric needs. Liquid's own example downsizes to the same edge before sending.
  static let maxImageEdge: CGFloat = 512

  /// A grade that never returns is worse than one that fails: the operator is left looking at
  /// a spinner with their hands full. Longer than the agent's 45 s because a 3B model on a
  /// phone is slower than a hosted one, and shorter than patience.
  static let timeout: Duration = .seconds(90)

  /// One constrained call. Static so it holds nothing of the main actor while the model runs.
  static func run(
    runner: any ModelRunner, jpeg: Data, rubric: Rubric
  ) async throws -> GradeAssembler.RawReport {
    var options = GenerationOptions(
      // The sampling LEAP's own manifest for this checkpoint specifies. The hosted grader runs
      // at temperature 0; this is the model's published setting and is what the Mac-side
      // measurement is taken at, so the two agree.
      temperature: 0.1,
      minP: 0.15,
      repetitionPenalty: 1.05,
      // Same points, same reply, run to run. A grade that changes when nothing did is not
      // something a student can be shown.
      rngSeed: 0,
      maxOutputTokens: 2048,
      enableThinking: false
    )
    try options.setResponseFormat(type: LocalGradeReport.self)

    let conversation = runner.createConversation(systemPrompt: systemPrompt)
    let message = ChatMessage(
      role: .user,
      // Image first, then the rubric — the same order the hosted grader sends.
      content: [.fromJPEGData(jpeg), .text(userPrompt(rubric))]
    )

    var text = ""
    for try await response in conversation.generateResponse(
      message: message, generationOptions: options
    ) {
      switch response {
      case .chunk(let chunk):
        text += chunk
      case .complete:
        break
      default:
        break
      }
    }

    guard let data = text.data(using: .utf8) else {
      throw LocalGraderError.unreadableReply
    }
    let decoded = try JSONDecoder().decode(LocalGradeReport.self, from: data)
    return GradeAssembler.RawReport(
      observed: decoded.observed,
      criteria: decoded.criteria.map {
        GradeAssembler.RawCriterion(
          index: $0.index, verdict: $0.verdict, observable: $0.observable, note: $0.note
        )
      },
      criticalDefectsSeen: decoded.criticalDefectsSeen
    )
  }

  // MARK: - Prompts
  //
  // Word for word from agent/grading.py. The point of the on-device path is to answer the same
  // question the hosted grader answers, and a prompt that drifted would make every comparison
  // between them — including the Mac-side measurement the model was chosen on — meaningless.

  static let systemPrompt = """
    You are grading a photograph of a student's finished aircraft-maintenance work \
    for an FAA Part 147 training pilot, against a rubric supplied to you.

    Judge EACH numbered criterion independently and return a verdict for every one, \
    in the order given. Do not merge them, skip them, or add any.

    PASS means you can see, in this photograph, that the criterion is satisfied.
    FAIL means either that you can see it is not satisfied, or that the photograph \
    does not show it. The rubric is explicit that a criterion you cannot check is \
    marked "FAIL — not demonstrated in image", so an unobservable condition is a \
    FAIL and never a PASS. Say which of the two it was in your note.

    Then list the rubric's critical defects that you can actually SEE in the frame. \
    A defect you cannot rule out is not a defect you saw — list only what is \
    affirmatively visible.

    Judge only visible evidence. Never infer torque, pressure, internal condition, \
    material type, or an exact dimension from a photograph. If the rubric asks for a \
    measurement and no scale reference is in frame, that criterion FAILS as not \
    demonstrated.

    Keep every note under 20 words and name what you saw, not what you assumed.
    """

  static func userPrompt(_ rubric: Rubric) -> String {
    let numbered = rubric.criteria.enumerated()
      .map { "\($0.offset + 1). \($0.element)" }
      .joined(separator: "\n")
    let defects = rubric.criticalDefects.map { "- \($0)" }.joined(separator: "\n")
    let subject = rubric.subject.isEmpty ? "work" : rubric.subject

    return """
      TASK \(rubric.taskCode) — \(rubric.taskTitle)
      SUBTASK \(rubric.subtaskCode) — \(rubric.subtask)

      Assess the completed \(subject) visible in the image.

      NUMBERED CRITERIA
      \(numbered)

      CRITICAL DEFECTS
      \(defects)

      Return a verdict for all \(rubric.criteria.count) criteria, in order.
      """
  }

  // MARK: - Image

  /// Scales the still down to the model's native tile and re-encodes. Returns the original
  /// bytes if it is already small enough or cannot be decoded — a grade on a large image is
  /// slower, a grade on nothing is a failure.
  static func downsized(_ jpeg: Data) -> Data {
    guard let image = UIImage(data: jpeg) else { return jpeg }
    let longest = max(image.size.width, image.size.height)
    guard longest > maxImageEdge else { return jpeg }

    let scale = maxImageEdge / longest
    let size = CGSize(width: image.size.width * scale, height: image.size.height * scale)
    let resized = UIGraphicsImageRenderer(size: size).image { _ in
      image.draw(in: CGRect(origin: .zero, size: size))
    }
    return resized.jpegData(compressionQuality: 0.9) ?? jpeg
  }
}


enum LocalGraderError: Error {
  case unreadableReply
}

private struct TimeoutError: Error {}

/// Runs `work`, failing it if it outlives `limit`.
private func withTimeout<T: Sendable>(
  _ limit: Duration, _ work: @escaping @Sendable () async throws -> T
) async throws -> T {
  try await withThrowingTaskGroup(of: T.self) { group in
    group.addTask { try await work() }
    group.addTask {
      try await Task.sleep(for: limit)
      throw TimeoutError()
    }
    guard let first = try await group.next() else { throw TimeoutError() }
    group.cancelAll()
    return first
  }
}
