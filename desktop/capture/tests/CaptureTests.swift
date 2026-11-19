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
        var valid = true
        for i in 0..<100 {
            try converter.append(packet, at: Double(i) / 50) { mono, time in
                valid = valid && abs(time * Double(Timeline.rate) - Double(frames)) < 0.001
                    && abs((mono.last ?? 0) - 0.25) < 0.05
                frames += mono.count
            }
        }
        try converter.finish { mono, _ in frames += mono.count }
        try check(valid, "level and packet continuity at \(rate) Hz")
        try check(abs(frames - 2 * Timeline.rate) <= 1, "resampled duration at \(rate) Hz")
    }
}

// Actual mic taps can deliver 100 ms despite the requested 1024-frame tap size.
private func checkMicContinuity() throws {
    let rate = Timeline.rate, packetSize = rate / 10
    let format = AVAudioFormat(standardFormatWithSampleRate: Double(rate), channels: 1)!
    let packet = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: UInt32(packetSize))!
    packet.frameLength = packet.frameCapacity
    let converter = MonoConverter()
    var timeline = Timeline(mode: .mic)
    var expected = [Int16]()
    func collect(_ samples: [Float], _ time: Double) {
        timeline.add(samples, at: Int((time * Double(rate)).rounded()), from: .mic)
    }
    for i in 0..<441 {
        for j in 0..<packetSize {
            let phase = Double(i * packetSize + j) * 2 * Double.pi * 220 / Double(rate)
            let sample = Float(0.21 + 0.05 * sin(phase))
            packet.floatChannelData![0][j] = sample
            expected.append(Int16((sample * 32767).rounded()))
        }
        try converter.append(packet, at: Double(i) / 10, emit: collect)
    }
    try converter.finish(emit: collect)
    let output = timeline.render(through: expected.count)
    let errors = zip(output, expected).filter { abs(Int($0) - Int($1)) > 1 }.count
    try check(errors == 0, "100 ms mic packets have \(errors) incorrect samples, including the final packet")
    print("44.1 s mic tone: all \(output.count) samples match the reference.")
}

private func checkInputGap() throws {
    let format = AVAudioFormat(standardFormatWithSampleRate: 48000, channels: 1)!
    let packet = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 4800)!
    packet.frameLength = 4800
    let converter = MonoConverter()
    var timeline = Timeline(mode: .mic)
    func collect(_ samples: [Float], _ time: Double) {
        timeline.add(samples, at: Int((time * 48000).rounded()), from: .mic)
    }
    packet.floatChannelData![0].initialize(repeating: 0.25, count: 4800)
    try converter.append(packet, at: 0, emit: collect)
    packet.floatChannelData![0].initialize(repeating: -0.25, count: 4800)
    try converter.append(packet, at: 0.15, emit: collect)
    try converter.finish(emit: collect)
    let output = timeline.render(through: 12000)
    try check(output[..<4800].allSatisfy { $0 == 8192 } &&
              output[4800..<7200].allSatisfy { $0 == 0 } &&
              output[7200...].allSatisfy { $0 == -8192 }, "preserve real input gap and both converter tails")
}

private func checkConvertedSync() throws {
    let rate = Timeline.rate
    let markers = [2.047, 65.057, 118.043]
    var onsets = [Source: [Int]]()
    var baseMix = Timeline(mode: .both)
    for source in [Source.mic, .system] {
        let mic = source == .mic
        let hz = mic ? 44100 : 48000, size = mic ? 4410 : 512
        let offset = mic ? 0.137 : 0.021, drift = mic ? 0.99986 : 1.00014
        let channels = mic ? 1 : 2
        let format = AVAudioFormat(standardFormatWithSampleRate: Double(hz), channels: UInt32(channels))!
        let packet = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: UInt32(size))!
        packet.frameLength = packet.frameCapacity
        let converter = MonoConverter()
        var single = Timeline(mode: mic ? .mic : .system)
        var edges = [Int]()
        var high = false
        func collect(_ samples: [Float], _ time: Double) {
            let frame = Int((time * Double(rate)).rounded())
            single.add(samples, at: frame, from: source)
            baseMix.add(samples, at: frame, from: source)
            let start = single.cursor
            for (i, sample) in single.render(through: frame - rate / 4).enumerated() {
                if !high && sample > 8000 { edges.append(start + i) }
                high = sample > 8000
            }
        }
        // A shared 10 ms marker occurs at the same host time on independent clocks.
        for block in 0..<Int(ceil(120 * Double(hz) / Double(size))) {
            let t = offset + Double(block * size) * drift / Double(hz)
            for c in 0..<channels { packet.floatChannelData![c].initialize(repeating: 0, count: size) }
            for marker in markers where marker < t + Double(size) * drift / Double(hz) && marker + 0.01 > t {
                let lo = max(0, Int(ceil((marker - t) * Double(hz) / drift)))
                let hi = min(size, Int(ceil((marker + 0.01 - t) * Double(hz) / drift)))
                if hi > lo {
                    for c in 0..<channels { for j in lo..<hi { packet.floatChannelData![c][j] = 0.5 } }
                }
            }
            try converter.append(packet, at: t, emit: collect)
        }
        try converter.finish(emit: collect)
        try check(edges.count == markers.count, "all \(source) markers survive")
        onsets[source] = edges
    }
    let mix = baseMix.render(through: 120 * rate)
    var worst = 0
    for (i, marker) in markers.enumerated() {
        let mic = onsets[.mic]![i], system = onsets[.system]![i]
        let target = Int((marker * Double(rate)).rounded())
        worst = max(worst, abs(mic - system))
        try check(abs(target - mic) < 48 && abs(target - system) < 48, "both markers within 1 ms of host time")
        try check(mix[target + 240] > 15000, "mic and system markers overlap in the final mix")
    }
    try check(worst < 48, "mic/system offset under 1 ms")
    print("120 s independently clocked inputs: maximum marker offset \(worst) frames at 48 kHz.")
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
        try checkMicContinuity()
        try checkInputGap()
        try checkConvertedSync()
        try checkFile(directory: directory, seconds: CommandLine.arguments.contains("--long") ? 10800 : 3)
    }
}
