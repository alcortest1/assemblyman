/*
 * Ultralytics YOLO integration for AssemblyMan.
 *
 * Ultralytics YOLO is licensed under AGPL-3.0. Commercial distribution
 * requires an Ultralytics Enterprise license. See THIRD_PARTY_NOTICES.md.
 */

import CoreGraphics
import Foundation
import UIKit
import UltralyticsYOLO

enum VisionOverlayMode: String, CaseIterable, Identifiable {
  case mobileSAM
  case yoloObjects
  case yoloScene
  case yoloDetect

  var id: Self { self }

  var label: String {
    switch self {
    case .mobileSAM: return "MobileSAM"
    case .yoloObjects: return "YOLO Objects"
    case .yoloScene: return "YOLO Scene"
    case .yoloDetect: return "YOLO Detect"
    }
  }

  var usesMobileSAM: Bool {
    self == .mobileSAM
  }

  var usesYOLOColorMap: Bool {
    self != .mobileSAM
  }
}

enum VisionFrameRate: Double, CaseIterable, Identifiable {
  case half = 0.5
  case one = 1
  case two = 2
  case five = 5
  case ten = 10
  case fifteen = 15

  var id: Self { self }

  var label: String {
    switch self {
    case .half: return "0.5"
    case .one: return "1"
    case .two: return "2"
    case .five: return "5"
    case .ten: return "10"
    case .fifteen: return "15"
    }
  }

  var interval: Duration {
    .milliseconds(Int((1_000 / rawValue).rounded()))
  }
}

enum YOLOOverlayClass: String, CaseIterable, Identifiable {
  case floor
  case wall
  case person
  case laptop
  case table

  var id: Self { self }

  var label: String {
    rawValue.capitalized
  }

  var color: UIColor {
    let components = rgb
    return UIColor(
      red: CGFloat(components.red) / 255,
      green: CGFloat(components.green) / 255,
      blue: CGFloat(components.blue) / 255,
      alpha: 1
    )
  }

  var rgb: (red: UInt8, green: UInt8, blue: UInt8) {
    switch self {
    case .floor:
      return (255, 212, 59)
    case .wall:
      return (77, 140, 255)
    case .person:
      return (255, 61, 128)
    case .laptop:
      return (0, 214, 214)
    case .table:
      return (255, 122, 51)
    }
  }

  var bit: UInt8 {
    switch self {
    case .floor: return 1 << 0
    case .wall: return 1 << 1
    case .person: return 1 << 2
    case .laptop: return 1 << 3
    case .table: return 1 << 4
    }
  }

  static func mappedClass(for modelLabel: String) -> Self? {
    switch modelLabel.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
    case "floor", "road", "sidewalk", "terrain":
      return .floor
    case "wall":
      return .wall
    case "person":
      return .person
    case "laptop":
      return .laptop
    case "table", "dining table":
      return .table
    default:
      return nil
    }
  }
}

enum YOLOInferenceResult {
  case success(image: UIImage, inferenceMilliseconds: Int, coloredRegions: Int)
  case failure(message: String)
}

/// Owns and serializes YOLO inference so incoming stream frames are dropped rather than queued.
actor YOLOProcessor {
  private enum ModelKind: Hashable {
    case detect
    case segment
    case semantic

    var resourceName: String {
      switch self {
      case .detect: return "yolo26n"
      case .segment: return "yolo26n-seg"
      case .semantic: return "yolo26n-sem"
      }
    }

    var task: YOLOTask {
      switch self {
      case .detect: return .detect
      case .segment: return .segment
      case .semantic: return .semantic
      }
    }
  }

  private var models: [ModelKind: YOLO] = [:]

  func makeOverlay(
    for image: CGImage,
    mode: VisionOverlayMode
  ) async -> YOLOInferenceResult {
    let start = ContinuousClock.now

    do {
      let rendered: RenderedOverlay
      switch mode {
      case .mobileSAM:
        return .failure(message: "MobileSAM was sent to the YOLO processor.")
      case .yoloObjects:
        let segmenter = try await model(for: .segment)
        let result = segmenter(image)
        rendered = try Self.render(
          source: image,
          semanticResult: nil,
          objectResult: result,
          drawsDetectionBoxes: false
        )
      case .yoloScene:
        let semanticSegmenter = try await model(for: .semantic)
        let instanceSegmenter = try await model(for: .segment)
        let semanticResult = semanticSegmenter(image)
        let objectResult = instanceSegmenter(image)
        rendered = try Self.render(
          source: image,
          semanticResult: semanticResult,
          objectResult: objectResult,
          drawsDetectionBoxes: false
        )
      case .yoloDetect:
        let detector = try await model(for: .detect)
        let result = detector(image)
        rendered = try Self.render(
          source: image,
          semanticResult: nil,
          objectResult: result,
          drawsDetectionBoxes: true
        )
      }

      return .success(
        image: rendered.image,
        inferenceMilliseconds: Self.milliseconds(from: start.duration(to: .now)),
        coloredRegions: rendered.coloredRegions
      )
    } catch {
      return .failure(message: error.localizedDescription)
    }
  }

  func reset() {
    models.removeAll()
  }

  private func model(for kind: ModelKind) async throws -> YOLO {
    if let model = models[kind], model.isLoaded {
      return model
    }

    let loadedModel: YOLO = try await withCheckedThrowingContinuation { continuation in
      let pendingModel = YOLO(
        kind.resourceName,
        task: kind.task,
        useGpu: true,
        numItemsThreshold: 20
      ) { result in
        continuation.resume(with: result)
      }
      pendingModel.setConfidenceThreshold(0.35)
      models[kind] = pendingModel
    }
    models[kind] = loadedModel
    return loadedModel
  }

  private static func render(
    source: CGImage,
    semanticResult: YOLOResult?,
    objectResult: YOLOResult,
    drawsDetectionBoxes: Bool
  ) throws -> RenderedOverlay {
    let canvasSize = CGSize(width: source.width, height: source.height)
    let format = UIGraphicsImageRendererFormat()
    format.opaque = false
    format.scale = 1

    var coloredRegions = 0
    let renderer = UIGraphicsImageRenderer(size: canvasSize, format: format)
    let image = renderer.image { context in
      if let semanticResult,
        let semanticMask = semanticResult.semanticMask,
        let semanticOverlay = try? makeSemanticOverlay(
          mask: semanticMask,
          names: semanticResult.names
        )
      {
        UIImage(cgImage: semanticOverlay.image).draw(
          in: CGRect(origin: .zero, size: canvasSize)
        )
        coloredRegions += semanticOverlay.coloredRegions
      }

      if drawsDetectionBoxes {
        coloredRegions += drawDetectionBoxes(
          objectResult.boxes,
          in: context.cgContext,
          canvasSize: canvasSize
        )
      } else if let masks = objectResult.masks?.masks,
        let instanceImage = try? makeInstanceOverlay(
          masks: masks,
          boxes: objectResult.boxes
        )
      {
        UIImage(cgImage: instanceImage.image).draw(
          in: CGRect(origin: .zero, size: canvasSize)
        )
        coloredRegions += instanceImage.coloredRegions
        _ = drawDetectionBoxes(
          objectResult.boxes,
          in: context.cgContext,
          canvasSize: canvasSize
        )
      }
    }

    guard image.cgImage != nil else {
      throw YOLOOverlayError.overlayCreationFailed
    }
    return RenderedOverlay(image: image, coloredRegions: coloredRegions)
  }

  private static func makeSemanticOverlay(
    mask: SemanticMask,
    names: [String]
  ) throws -> (image: CGImage, coloredRegions: Int) {
    guard mask.width > 0, mask.height > 0, mask.classMap.count == mask.width * mask.height else {
      throw YOLOOverlayError.invalidSemanticMask
    }

    let classLookup = names.map(YOLOOverlayClass.mappedClass(for:))
    var pixels = [UInt8](repeating: 0, count: mask.classMap.count * 4)
    var visibleClassBits: UInt8 = 0
    for (pixelIndex, classIndex) in mask.classMap.enumerated() {
      let labelIndex = Int(classIndex)
      guard
        classLookup.indices.contains(labelIndex),
        let overlayClass = classLookup[labelIndex]
      else {
        continue
      }
      visibleClassBits |= overlayClass.bit
      write(overlayClass, to: &pixels, at: pixelIndex, alpha: 112)
    }

    return (
      image: try makeImage(pixels: pixels, width: mask.width, height: mask.height),
      coloredRegions: visibleClassBits.nonzeroBitCount
    )
  }

  private static func makeInstanceOverlay(
    masks: [[[Float]]],
    boxes: [Box]
  ) throws -> (image: CGImage, coloredRegions: Int) {
    guard
      let firstMask = masks.first,
      let firstRow = firstMask.first,
      !firstRow.isEmpty
    else {
      throw YOLOOverlayError.invalidInstanceMasks
    }

    let width = firstRow.count
    let height = firstMask.count
    var pixels = [UInt8](repeating: 0, count: width * height * 4)
    var coloredRegions = 0

    // Results are confidence ordered. Paint lower-confidence regions first so
    // a stronger overlapping detection owns the final color.
    for index in boxes.indices.reversed() where masks.indices.contains(index) {
      guard let overlayClass = YOLOOverlayClass.mappedClass(for: boxes[index].cls) else {
        continue
      }
      let mask = masks[index]
      guard mask.count == height, mask.allSatisfy({ $0.count == width }) else {
        continue
      }
      coloredRegions += 1
      for y in 0..<height {
        for x in 0..<width where mask[y][x] > 0 {
          write(overlayClass, to: &pixels, at: y * width + x, alpha: 126)
        }
      }
    }

    return (
      image: try makeImage(pixels: pixels, width: width, height: height),
      coloredRegions: coloredRegions
    )
  }

  @discardableResult
  private static func drawDetectionBoxes(
    _ boxes: [Box],
    in context: CGContext,
    canvasSize: CGSize
  ) -> Int {
    var count = 0
    for box in boxes {
      guard let overlayClass = YOLOOverlayClass.mappedClass(for: box.cls) else {
        continue
      }
      count += 1
      let color = overlayClass.color
      let normalized = box.xywhn
      let rect = CGRect(
        x: normalized.minX * canvasSize.width,
        y: normalized.minY * canvasSize.height,
        width: normalized.width * canvasSize.width,
        height: normalized.height * canvasSize.height
      )

      context.saveGState()
      context.setStrokeColor(color.cgColor)
      context.setLineWidth(max(2, canvasSize.width / 280))
      context.stroke(rect)
      context.restoreGState()

      let label = "\(overlayClass.label) \(Int((box.conf * 100).rounded()))%"
      let attributes: [NSAttributedString.Key: Any] = [
        .font: UIFont.monospacedSystemFont(
          ofSize: max(11, canvasSize.width / 42),
          weight: .semibold
        ),
        .foregroundColor: UIColor.white,
        .backgroundColor: color.withAlphaComponent(0.9),
      ]
      let textSize = (label as NSString).size(withAttributes: attributes)
      let labelY = max(0, rect.minY - textSize.height)
      (label as NSString).draw(
        at: CGPoint(x: max(0, rect.minX), y: labelY),
        withAttributes: attributes
      )
    }
    return count
  }

  private static func write(
    _ overlayClass: YOLOOverlayClass,
    to pixels: inout [UInt8],
    at pixelIndex: Int,
    alpha: UInt8
  ) {
    let color = overlayClass.rgb
    let alphaScale = Float(alpha) / 255
    let offset = pixelIndex * 4
    pixels[offset] = UInt8(Float(color.red) * alphaScale)
    pixels[offset + 1] = UInt8(Float(color.green) * alphaScale)
    pixels[offset + 2] = UInt8(Float(color.blue) * alphaScale)
    pixels[offset + 3] = alpha
  }

  private static func makeImage(
    pixels: [UInt8],
    width: Int,
    height: Int
  ) throws -> CGImage {
    let bitmapInfo = CGBitmapInfo(
      rawValue: CGImageAlphaInfo.premultipliedLast.rawValue
    )
    guard
      let provider = CGDataProvider(data: Data(pixels) as CFData),
      let image = CGImage(
        width: width,
        height: height,
        bitsPerComponent: 8,
        bitsPerPixel: 32,
        bytesPerRow: width * 4,
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: bitmapInfo,
        provider: provider,
        decode: nil,
        shouldInterpolate: true,
        intent: .defaultIntent
      )
    else {
      throw YOLOOverlayError.overlayCreationFailed
    }
    return image
  }

  private static func milliseconds(from duration: Duration) -> Int {
    let components = duration.components
    return
      Int(components.seconds * 1_000)
      + Int(components.attoseconds / 1_000_000_000_000_000)
  }
}

private struct RenderedOverlay {
  let image: UIImage
  let coloredRegions: Int
}

private enum YOLOOverlayError: LocalizedError {
  case invalidSemanticMask
  case invalidInstanceMasks
  case overlayCreationFailed

  var errorDescription: String? {
    switch self {
    case .invalidSemanticMask:
      return "YOLO returned an invalid semantic segmentation mask."
    case .invalidInstanceMasks:
      return "YOLO returned invalid instance segmentation masks."
    case .overlayCreationFailed:
      return "YOLO could not create the live overlay."
    }
  }
}
