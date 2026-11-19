// Compiled only in the process-lifecycle tests. Never opens a capture device.
import CoreMedia
import Foundation

func startSyntheticInput(_ recorder: Recorder) throws {
    if let name = ProcessInfo.processInfo.environment["JARD_TEST_DENIED_SOURCE"], let source = Source(rawValue: name) {
        throw RecordingPermissionError(source: source)
    }
    try recorder.beginWriting()
    let start = CMClockGetTime(CMClockGetHostTimeClock()).seconds
    let fail = ProcessInfo.processInfo.environment["JARD_TEST_INPUT_LOSS"] == "1"
    Task.detached {
        var i = 0
        while !Task.isCancelled {
            let timestamp = start + Double(i) * 0.02
            recorder.enqueue([Float](repeating: 0.25, count: 960), at: timestamp, from: .mic)
            recorder.enqueue([Float](repeating: 0.5, count: 960), at: timestamp, from: .system)
            if fail && i == 88 {
                recorder.enqueue([0.2], at: .nan, from: .mic)
                return
            }
            i += 1
            let remaining = (start + Double(i) * 0.02) - CMClockGetTime(CMClockGetHostTimeClock()).seconds
            if remaining > 0 { try? await Task.sleep(nanoseconds: UInt64(remaining * 1e9)) }
        }
    }
}
