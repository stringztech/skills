#!/usr/bin/env swift

import AppKit
import Foundation
import Vision

struct OCRBox: Encodable {
    let text: String
    let bbox: [Int]
    let confidence: Float
    let source: String
    let alternatives: [String]
}

struct OCRResult: Encodable {
    let engine: String
    let boxes: [OCRBox]
}

guard CommandLine.arguments.count == 2 else {
    FileHandle.standardError.write(Data("usage: vision_ocr.swift IMAGE\n".utf8))
    exit(64)
}

let imagePath = CommandLine.arguments[1]
guard let image = NSImage(contentsOfFile: imagePath) else {
    FileHandle.standardError.write(Data("unable to open image\n".utf8))
    exit(65)
}

var proposedRect = NSRect(origin: .zero, size: image.size)
guard let cgImage = image.cgImage(forProposedRect: &proposedRect, context: nil, hints: nil) else {
    FileHandle.standardError.write(Data("unable to decode image\n".utf8))
    exit(66)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
if #available(macOS 13.0, *) {
    request.automaticallyDetectsLanguage = true
}

do {
    try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
} catch {
    FileHandle.standardError.write(Data("Vision OCR failed: \(error)\n".utf8))
    exit(67)
}

let pixelWidth = CGFloat(cgImage.width)
let pixelHeight = CGFloat(cgImage.height)
var boxes: [OCRBox] = []
for observation in request.results ?? [] {
    let candidates = observation.topCandidates(3)
    guard let best = candidates.first else { continue }
    let normalized = observation.boundingBox
    let left = Int((normalized.minX * pixelWidth).rounded())
    let top = Int(((1.0 - normalized.maxY) * pixelHeight).rounded())
    let width = Int((normalized.width * pixelWidth).rounded())
    let height = Int((normalized.height * pixelHeight).rounded())
    boxes.append(
        OCRBox(
            text: best.string,
            bbox: [left, top, width, height],
            confidence: best.confidence,
            source: "ocr",
            alternatives: candidates.dropFirst().map { $0.string }
        )
    )
}

boxes.sort {
    if $0.bbox[1] == $1.bbox[1] { return $0.bbox[0] < $1.bbox[0] }
    return $0.bbox[1] < $1.bbox[1]
}

let encoder = JSONEncoder()
encoder.outputFormatting = [.sortedKeys]
let payload = OCRResult(engine: "macos_vision", boxes: boxes)
do {
    FileHandle.standardOutput.write(try encoder.encode(payload))
    FileHandle.standardOutput.write(Data("\n".utf8))
} catch {
    FileHandle.standardError.write(Data("unable to encode OCR result\n".utf8))
    exit(68)
}
