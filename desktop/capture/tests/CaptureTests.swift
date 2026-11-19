import AVFoundation
import Foundation
import ScreenCaptureKit

private func check(_ condition: @autoclosure () -> Bool, _ message: String) throws {
    if !condition() { throw CaptureError("Test failed: " + message) }
}

private func checkMix() throws {
    var both = Timeline(mode: .both)
    both.add([0.5, 1, 1], at: 1, from: .mic)
    both.add([-1, 1, 0.75], at: 2, from: .system)
    try check(both.render(through: 3) == [0, 8192, 0], "input offset and overlap")
    try check(both.render(through: 7) == [32767, 12288, 0, 0], "peak, trailing channel, silence")
    both.add([1, 1, 1], at: 0, from: .system)
    try check(both.lateFrames == 3, "dropped late samples reported")
    for (source, mode) in [(Source.mic, CaptureMode.mic), (.system, .system)] {
        var only = Timeline(mode: mode)
        only.add([1, -1], at: 0, from: source)
        try check(only.render(through: 2) == [32767, -32767], "single-source gain")
    }
}

private func checkClockDrift() throws {
    // Two independent clocks drift in opposite directions. The later callback for the
    // earlier-timestamped source still has to align with its partner on the host timeline.
    let seconds = 120, rate = Timeline.rate
    let markers = [2 * rate + 192, 65 * rate + 192, 118 * rate + 192]
    var found = [Int]()
    var timeline = Timeline(mode: .both)
    for block in 0..<(seconds * 50 + 2) {
        for (source, drift) in [(Source.system, 1.000140), (.mic, 0.999860)] {
            let start = Double(block * 960) * drift
            var samples = [Float](repeating: 0, count: 960)
            for mark in markers {
                let sample = Int(((Double(mark) - start) / drift).rounded())
                if samples.indices.contains(sample) {
                    // A short pulse on each channel gives the overlap a unique higher level.
                    for index in sample..<min(sample + 9, samples.count) { samples[index] = 1 }
                }
            }
            timeline.add(samples, at: Int(start.rounded()), from: source)
        }
        let start = timeline.cursor
        let mixed = timeline.render(through: max(0, block * 960 - rate))
        for i in mixed.indices where mixed[i] == 32767 { found.append(start + i) }
    }
    for mark in markers {
        try check(found.contains(where: { abs($0 - mark) <= 4 }), "capture clocks remain aligned at frame \(mark)")
    }
}

private func checkConverter() throws {
    for rate in [16_000, 24_000, 44_100, 48_000] {
        let format = AVAudioFormat(standardFormatWithSampleRate: Double(rate), channels: 2)!
        let packet = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: UInt32(rate / 50))!
        packet.frameLength = packet.frameCapacity
        for i in 0..<Int(packet.frameLength) {
            packet.floatChannelData![0][i] = 0.25
            packet.floatChannelData![1][i] = 0.25
        }
        let converter = MonoConverter()
        var frames = 0
        for _ in 0..<100 {
            let mono = try converter.samples(packet)
            try check(!mono.isEmpty && abs((mono.last ?? 0) - 0.25) < 0.05, "converted microphone level")
            frames += mono.count
        }
        try check(abs(frames - 2 * Timeline.rate) < 128, "resampled duration at \(rate) Hz")
    }
}

private func checkFile(directory: URL, seconds: Int) throws {
    let path = directory.appendingPathComponent("synthetic.wav").path
    let sink = try WaveSink(path: path)
    var timeline = Timeline(mode: .both)
    let second = Timeline.rate
    for i in 0..<seconds {
        // Known near- and far-end pulses verify offsets over the entire run.
        timeline.add([0.5, 0.5], at: i * second + 12, from: .mic)
        timeline.add([-0.5], at: i * second + second - 2, from: .system)
        for end in 1...4 {
            try sink.append(timeline.render(through: i * second + end * (second / 4)))
        }
    }
    try sink.finish()
    let url = URL(fileURLWithPath: path)
    let info = try FileManager.default.attributesOfItem(atPath: path)
    try check((info[.size] as? Int) == 44 + seconds * second * 2, "exact WAV size")
    let reader = try AVAudioFile(forReading: url)
    try check(reader.length == Int64(seconds * second), "Core Audio reads full duration")
    let file = try FileHandle(forReadingFrom: url)
    defer { try? file.close() }
    for i in [0, seconds / 2, seconds - 1] {
        for (offset, expected) in [(12, Int16(8192)), (second - 2, Int16(-8192))] {
            try file.seek(toOffset: UInt64(44 + (second * i + offset) * 2))
            let data = try file.read(upToCount: 2)!
            let sample = data.withUnsafeBytes { $0.loadUnaligned(as: Int16.self) }
            try check(sample == expected, "known sample at second \(i), offset \(offset)")
        }
    }
    do {
        _ = try WaveSink(path: path)
        throw CaptureError("Existing file was replaced.")
    } catch let e as CaptureError where e.message == "Choose an empty recording file." {}
    if seconds == 10800 {
        try check(timeline.render(through: (seconds + 1) * second).isEmpty, "three-hour cap")
    }
    print("Verified \(seconds)s and \(sink.frames) samples on disk, with labeled mic/system markers.")
}

@main private enum Tests {
    static func main() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let denial = NSError(domain: SCStreamErrorDomain, code: SCStreamError.Code.userDeclined.rawValue)
        let wrapped = NSError(domain: NSCocoaErrorDomain, code: 1, userInfo: [NSUnderlyingErrorKey: denial])
        let multiple = NSError(domain: NSCocoaErrorDomain, code: 2, userInfo: [NSMultipleUnderlyingErrorsKey: [wrapped]])
        try check(isSystemCaptureDenial(denial) && isSystemCaptureDenial(multiple), "recognize direct and wrapped TCC denial")
        try check(!isSystemCaptureDenial(NSError(domain: SCStreamErrorDomain, code: SCStreamError.Code.failedToStart.rawValue)), "unrelated stream failures stay distinct")
        try checkMix()
        try checkClockDrift()
        try checkConverter()
        try checkFile(directory: directory, seconds: CommandLine.arguments.contains("--long") ? 10800 : 3)
    }
}
