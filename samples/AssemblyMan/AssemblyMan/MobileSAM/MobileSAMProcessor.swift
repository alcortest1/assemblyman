/*
 * MobileSAM on-device inference for AssemblyMan.
 *
 * MobileSAM model architecture and weights:
 * Copyright MobileSAM contributors, Apache License 2.0.
 *
 * Preprocessing and prompt encoding are adapted from SAMKit by john-rocky,
 * also licensed under the Apache License 2.0.
 */

import CoreGraphics
import CoreML
import Foundation
import UIKit

enum MobileSAMInferenceResult {
  case success(image: UIImage, inferenceMilliseconds: Int)
  case failure(message: String)
}

/// Serializes MobileSAM work so stream frames are dropped instead of queued.
actor MobileSAMProcessor {
  private var runtime: MobileSAMRuntime?

  func makeOverlay(for image: CGImage) -> MobileSAMInferenceResult {
    let start = ContinuousClock.now

    do {
      let runtime = try runtime ?? MobileSAMRuntime()
      self.runtime = runtime
      let overlay = try runtime.segmentCenterObject(in: image)
      let elapsed = start.duration(to: .now)
      let components = elapsed.components
      let milliseconds =
        Int(components.seconds * 1_000)
        + Int(components.attoseconds / 1_000_000_000_000_000)
      return .success(
        image: UIImage(cgImage: overlay),
        inferenceMilliseconds: milliseconds
      )
    } catch {
      return .failure(message: error.localizedDescription)
    }
  }

  func reset() {
    runtime = nil
  }
}

private final class MobileSAMRuntime {
  private static let modelSize = 1_024
  private static let maskSize = 256

  private let encoder: MLModel
  private let decoder: MLModel
  private let preprocessor = Preprocessor(modelSize: modelSize)
  private let promptEncoder: PromptEncoder

  init(bundle: Bundle = .main) throws {
    guard
      let encoderURL = bundle.url(
        forResource: "mobile_sam_encoder",
        withExtension: "mlmodelc"
      ),
      let decoderURL = bundle.url(
        forResource: "mobile_sam_decoder",
        withExtension: "mlmodelc"
      ),
      let promptWeightsURL = bundle.url(
        forResource: "mobile_sam_prompt_encoder_weights",
        withExtension: "json"
      )
    else {
      throw MobileSAMError.modelResourcesMissing
    }

    let configuration = MLModelConfiguration()
    configuration.computeUnits = .all
    configuration.allowLowPrecisionAccumulationOnGPU = true

    encoder = try MLModel(contentsOf: encoderURL, configuration: configuration)
    decoder = try MLModel(contentsOf: decoderURL, configuration: configuration)
    promptEncoder = try PromptEncoder(weightsURL: promptWeightsURL)
  }

  func segmentCenterObject(in image: CGImage) throws -> CGImage {
    let (processedImage, transform) = try preprocessor.process(image)
    let batchedImage = try addBatchDimension(to: processedImage)

    let encoderInput = try MLDictionaryFeatureProvider(dictionary: [
      "image": batchedImage
    ])
    let encoderOutput = try encoder.prediction(from: encoderInput)
    guard
      let imageEmbedding =
        encoderOutput
        .featureValue(for: "image_embeddings")?
        .multiArrayValue
    else {
      throw MobileSAMError.invalidModelOutput("encoder image_embeddings")
    }

    let centerPoint = SamPoint(
      x: CGFloat(image.width) / 2,
      y: CGFloat(image.height) / 2,
      label: .positive
    )
    let (sparseEmbedding, denseEmbedding) = try promptEncoder.encode(
      points: [centerPoint],
      transform: transform
    )
    let decoderInput = try MLDictionaryFeatureProvider(dictionary: [
      "image_embeddings": imageEmbedding,
      "sparse_embeddings": sparseEmbedding,
      "dense_embeddings": denseEmbedding,
    ])
    let decoderOutput = try decoder.prediction(from: decoderInput)

    guard
      let masks = decoderOutput.featureValue(for: "masks")?.multiArrayValue,
      let scores =
        decoderOutput
        .featureValue(for: "iou_predictions")?
        .multiArrayValue
    else {
      throw MobileSAMError.invalidModelOutput("decoder masks or scores")
    }

    return try makeOverlay(
      masks: masks,
      scores: scores,
      transform: transform
    )
  }

  private func addBatchDimension(to image: MLMultiArray) throws -> MLMultiArray {
    guard image.shape.count == 3 else { return image }

    let batched = try MLMultiArray(
      shape: [1, 3, Self.modelSize as NSNumber, Self.modelSize as NSNumber],
      dataType: .float32
    )
    memcpy(
      batched.dataPointer,
      image.dataPointer,
      image.count * MemoryLayout<Float32>.size
    )
    return batched
  }

  private func makeOverlay(
    masks: MLMultiArray,
    scores: MLMultiArray,
    transform: TransformParams
  ) throws -> CGImage {
    guard
      masks.shape.count == 4,
      masks.shape[2].intValue == Self.maskSize,
      masks.shape[3].intValue == Self.maskSize
    else {
      throw MobileSAMError.invalidModelOutput("unexpected mask shape \(masks.shape)")
    }

    let maskCount = masks.shape[1].intValue
    guard maskCount > 0 else {
      throw MobileSAMError.invalidModelOutput("empty mask output")
    }

    let bestMask =
      (0..<maskCount).max {
        scores[[0, $0] as [NSNumber]].floatValue
          < scores[[0, $1] as [NSNumber]].floatValue
      } ?? 0

    let maskScale = Float(Self.maskSize) / Float(Self.modelSize)
    let cropX = max(0, Int((transform.padX * maskScale).rounded(.down)))
    let cropY = max(0, Int((transform.padY * maskScale).rounded(.down)))
    let cropWidth = min(
      Self.maskSize - cropX,
      max(1, Int((Float(transform.originalWidth) * transform.scale * maskScale).rounded()))
    )
    let cropHeight = min(
      Self.maskSize - cropY,
      max(1, Int((Float(transform.originalHeight) * transform.scale * maskScale).rounded()))
    )

    var pixels = [UInt8](repeating: 0, count: cropWidth * cropHeight * 4)
    for y in 0..<cropHeight {
      for x in 0..<cropWidth {
        let logit = masks[
          [0, bestMask, cropY + y, cropX + x] as [NSNumber]
        ].floatValue

        // SAM's default decision boundary is logit 0. Preserve a little
        // softness at the boundary to avoid a visibly jagged live overlay.
        let alpha: UInt8
        if logit <= 0 {
          alpha = 0
        } else {
          let probability = 1 / (1 + exp(-min(logit, 12)))
          let strength = min(1, max(0, (probability - 0.5) * 2))
          alpha = UInt8(strength * 118)
        }

        let offset = (y * cropWidth + x) * 4
        let alphaScale = Float(alpha) / 255
        pixels[offset] = UInt8(13 * alphaScale)
        pixels[offset + 1] = UInt8(220 * alphaScale)
        pixels[offset + 2] = UInt8(255 * alphaScale)
        pixels[offset + 3] = alpha
      }
    }

    let colorSpace = CGColorSpaceCreateDeviceRGB()
    let bitmapInfo = CGBitmapInfo(
      rawValue: CGImageAlphaInfo.premultipliedLast.rawValue
    )
    guard
      let provider = CGDataProvider(data: Data(pixels) as CFData),
      let image = CGImage(
        width: cropWidth,
        height: cropHeight,
        bitsPerComponent: 8,
        bitsPerPixel: 32,
        bytesPerRow: cropWidth * 4,
        space: colorSpace,
        bitmapInfo: bitmapInfo,
        provider: provider,
        decode: nil,
        shouldInterpolate: true,
        intent: .defaultIntent
      )
    else {
      throw MobileSAMError.overlayCreationFailed
    }

    return image
  }
}

enum MobileSAMError: LocalizedError {
  case modelResourcesMissing
  case invalidModelOutput(String)
  case preprocessingFailed(String)
  case overlayCreationFailed

  var errorDescription: String? {
    switch self {
    case .modelResourcesMissing:
      return "MobileSAM model resources are missing from the app bundle."
    case .invalidModelOutput(let output):
      return "MobileSAM returned an invalid \(output) output."
    case .preprocessingFailed(let message):
      return "MobileSAM preprocessing failed: \(message)"
    case .overlayCreationFailed:
      return "MobileSAM could not create the live overlay."
    }
  }
}

struct SamPoint {
  let x: CGFloat
  let y: CGFloat
  let label: SamPointLabel
}

enum SamPointLabel: Int {
  case negative = 0
  case positive = 1
}

struct SamBox {
  let x0: Float
  let y0: Float
  let x1: Float
  let y1: Float
}

struct SamMaskRef {
  let width: Int
  let height: Int
  let logits: [Float]?
  let alpha: Data
}

enum SamError: LocalizedError {
  case preprocessingFailed(String)
}
