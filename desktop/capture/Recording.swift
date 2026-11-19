import Foundation
import Darwin

enum Source: String { case mic, system }
enum CaptureMode: String { case mic, system, both }
struct CaptureError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

/// Timestamps from both producers use the host clock. Render in short windows, allowing late
/// callbacks to arrive before committing their portion of the mix to disk.
struct Timeline {
    static let rate = 48_000
    static let limit = 3 * 3600 * rate
    struct Block {
        let start: Int
        let samples: [Float]
        var end: Int { start + samples.count }
    }
    private var blocks = [Block]()
    private var lastEnd = [Source: Int]()
    private(set) var cursor = 0
    private(set) var lateFrames = 0
    let mode: CaptureMode

    init(mode: CaptureMode) { self.mode = mode }

    mutating func add(_ samples: [Float], at frame: Int, from source: Source) {
        guard !samples.isEmpty, frame < Self.limit else { return }
        // Round-off at callback boundaries must not insert a one-sample hole or double a sample.
        let previous = lastEnd[source]
        let start = previous.map { abs(frame - $0) <= 2 ? $0 : frame } ?? frame
        lastEnd[source] = start + samples.count
        lateFrames += max(0, min(samples.count, cursor - max(0, start)))
        if start + samples.count > cursor {
            blocks.append(Block(start: start, samples: samples))
        }
    }

    mutating func render(through frame: Int) -> [Int16] {
        let end = min(max(frame, cursor), Self.limit)
        guard end > cursor else { return [] }
        var mix = [Float](repeating: 0, count: end - cursor)
        for block in blocks {
            let lo = max(cursor, block.start), hi = min(end, block.end)
            if hi <= lo { continue }
            for i in lo..<hi { mix[i - cursor] += block.samples[i - block.start] }
        }
        blocks.removeAll { $0.end <= end }
        cursor = end
        let gain: Float = mode == .both ? 0.5 : 1
        return mix.map { Int16((max(-1, min(1, $0 * gain)) * 32767).rounded()) }
    }
}

/// Keep a standard 44-byte PCM WAV on disk. A checkpointed length also makes an interrupted
/// recording playable. The last uncheckpointed payload can be recovered from the file size.
final class WaveSink {
    private let file: FileHandle
    private(set) var frames = 0
    private var lastHeader = 0

    init(path: String) throws {
        // The engine supplies a private, pre-created empty staging file. O_NOFOLLOW plus
        // fstat avoids accidentally replacing an existing recording or following a symlink.
        let fd = open(path, O_WRONLY | O_CREAT | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, 0o600)
        guard fd >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        var info = stat()
        guard fstat(fd, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG, info.st_size == 0 else {
            close(fd)
            throw CaptureError("Choose an empty recording file.")
        }
        file = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        try checkpoint()
    }

    func append(_ samples: [Int16]) throws {
        guard !samples.isEmpty else { return }
        try samples.withUnsafeBytes { try file.write(contentsOf: $0) }
        frames += samples.count
        if frames - lastHeader >= Timeline.rate { try checkpoint() }
    }

    private func checkpoint() throws {
        var header = Data()
        func tag(_ string: String) { header.append(contentsOf: string.utf8) }
        func number<T: FixedWidthInteger>(_ value: T) {
            var le = value.littleEndian
            withUnsafeBytes(of: &le) { header.append(contentsOf: $0) }
        }
        let bytes = UInt32(frames * 2)
        tag("RIFF"); number(bytes + 36); tag("WAVE"); tag("fmt "); number(UInt32(16))
        number(UInt16(1)); number(UInt16(1)); number(UInt32(Timeline.rate))
        number(UInt32(Timeline.rate * 2)); number(UInt16(2)); number(UInt16(16))
        tag("data"); number(bytes)
        let count = header.withUnsafeBytes { pwrite(file.fileDescriptor, $0.baseAddress, $0.count, 0) }
        guard count == 44 else { throw CaptureError("Could not checkpoint the recording header.") }
        if frames == 0 { try file.seek(toOffset: 44) }
        lastHeader = frames
    }

    func finish() throws {
        // Discard any tail from a partially failed write before advertising the final length.
        try file.truncate(atOffset: UInt64(44 + frames * 2))
        try checkpoint()
        try file.synchronize()
        try file.close()
    }
}
