import AppKit
import ImageIO
import UniformTypeIdentifiers

private enum Cue: String, Decodable {
    case sample, start, stop, done, error, leave
}

private struct Beat: Decodable {
    let frame: Int
    let cue: Cue
}

private struct Preview: Decodable {
    let cue: Cue
    let frames: Int
    let beats: [Beat]
}

private struct Schedule: Decodable {
    let fps: Int
    let columns: Int
    let width: Int
    let height: Int
    let scale: Int
    let previews: [Preview]
    let stillFrames: [Beat]
}

// The reference HUD demo uses a synthetic voice to drive the same scope as live speech.
private func demoRMS(at time: Double) -> Double {
    let phrase = floor(time / 2.4)
    let t = time - phrase * 2.4
    let level: Double
    if t > 1.7 {
        level = 0.04
    } else {
        let shape = pow(sin(.pi * t / 1.7), 0.4)
        let rate = 2 * Double.pi * (4.3 + 0.6 * sin(phrase * 1.7))
        let syllable = 0.5 + 0.5 * sin(rate * t + phrase)
        let stress = 0.75 + 0.25 * sin(2 * Double.pi * 1.1 * t + 2 * phrase)
        level = min(1, max(0, 0.08 + 0.82 * shape * pow(syllable, 1.6) * stress))
    }
    return pow(10, (-58 + 28 * level) / 20)
}

@main
private enum RenderSoundPreview {
    @MainActor static func main() throws {
        let args = CommandLine.arguments
        guard args.count == 3 else {
            fatalError("Pass the preview schedule and destination PNG paths.")
        }
        let schedule = try JSONDecoder().decode(Schedule.self, from: Data(contentsOf: URL(fileURLWithPath: args[1])))
        let tiles = schedule.previews.reduce(schedule.stillFrames.count) { $0 + $1.frames }
        let tileWidth = schedule.width * schedule.scale
        let tileHeight = schedule.height * schedule.scale
        let width = schedule.columns * tileWidth
        let height = ((tiles + schedule.columns - 1) / schedule.columns) * tileHeight
        guard let colorSpace = CGColorSpace(name: CGColorSpace.sRGB),
              let bitmap = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                  bytesPerRow: width * 4, space: colorSpace, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
            fatalError("Could not allocate the preview image.")
        }
        let bounds = NSRect(x: 0, y: 0, width: schedule.width, height: schedule.height)
        var tile = 0
        func paint(_ meter: JardDictationMeter) {
            bitmap.saveGState()
            bitmap.translateBy(x: CGFloat(tile % schedule.columns * tileWidth),
                               y: CGFloat(height - tile / schedule.columns * tileHeight))
            bitmap.scaleBy(x: CGFloat(schedule.scale), y: -CGFloat(schedule.scale))
            bitmap.clip(to: bounds)
            NSGraphicsContext.saveGraphicsState()
            NSGraphicsContext.current = NSGraphicsContext(cgContext: bitmap, flipped: true)
            meter.draw(bounds)
            NSGraphicsContext.restoreGraphicsState()
            bitmap.restoreGState()
            tile += 1
        }
        func render(_ preview: Preview, still: Int? = nil) {
            let meter = JardDictationMeter(frame: bounds)
            meter.reduceMotion = { still != nil }
            var stage: Cue = .start
            var recordingStart = 0
            var beats = preview.beats[...]
            let start = beats.first?.frame ?? 0
            let end = still ?? (preview.frames - 1)
            for frame in start...end {
                while let beat = beats.first, beat.frame == frame {
                    beats = beats.dropFirst()
                    stage = beat.cue
                    switch stage {
                    case .start:
                        recordingStart = frame
                        meter.setStage(0, label: "")
                    case .stop: meter.setStage(1, label: "")
                    case .done: meter.setStage(2, label: "")
                    case .error: meter.setStage(3, label: "")
                    case .leave: meter.beginDismissal()
                    case .sample: preconditionFailure("Sample is a sequence, not a beat.")
                    }
                }
                let speechTime = Double(frame - recordingStart) / Double(schedule.fps)
                let rms = stage == .start ? demoRMS(at: speechTime) : 0
                meter.advance(at: Double(frame) / Double(schedule.fps), rms: rms)
                if let still {
                    if frame == still { paint(meter) }
                } else if frame >= 0 {
                    paint(meter)
                }
            }
        }
        for preview in schedule.previews { render(preview) }
        for still in schedule.stillFrames {
            guard let preview = schedule.previews.first(where: { $0.cue == still.cue }) else {
                fatalError("No preview for the still frame.")
            }
            render(preview, still: still.frame)
        }
        let output = URL(fileURLWithPath: args[2])
        guard tile == tiles, let image = bitmap.makeImage(),
              let png = CGImageDestinationCreateWithURL(output as CFURL, UTType.png.identifier as CFString, 1, nil) else {
            fatalError("Could not write the preview image.")
        }
        CGImageDestinationAddImage(png, image, nil)
        precondition(CGImageDestinationFinalize(png))
    }
}
