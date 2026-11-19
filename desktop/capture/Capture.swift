import AVFoundation
import CoreAudio
import CoreMedia
import ScreenCaptureKit

struct RecordingPermissionError: LocalizedError {
    let source: Source
    var errorDescription: String? {
        source == .mic ? "macOS has not allowed microphone access." : "macOS has not allowed Screen & System Audio Recording."
    }
}

func isSystemCaptureDenial(_ error: Error) -> Bool {
    var pending = [error as NSError]
    var visited = Set<ObjectIdentifier>()
    while let cause = pending.popLast() {
        guard visited.insert(ObjectIdentifier(cause)).inserted else { continue }
        if cause.domain == SCStreamErrorDomain && cause.code == SCStreamError.Code.userDeclined.rawValue { return true }
        if let underlying = cause.userInfo[NSUnderlyingErrorKey] as? NSError { pending.append(underlying) }
        if let causes = cause.userInfo[NSMultipleUnderlyingErrorsKey] as? [NSError] { pending.append(contentsOf: causes) }
    }
    return false
}

struct InputDevice: Equatable {
    let id: AudioDeviceID
    let name: String

    static func current() -> Self {
        var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDefaultInputDevice,
            mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var id = AudioDeviceID(kAudioObjectUnknown)
        var size = UInt32(MemoryLayout.size(ofValue: id))
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &id) == noErr,
              id != kAudioObjectUnknown else { return .init(id: 0, name: "No microphone") }
        address.mSelector = kAudioObjectPropertyName
        var value: Unmanaged<CFString>?
        size = UInt32(MemoryLayout.size(ofValue: value))
        if AudioObjectGetPropertyData(id, &address, 0, nil, &size, &value) == noErr, let value {
            return .init(id: id, name: value.takeRetainedValue() as String)
        }
        return .init(id: id, name: "Mac default microphone")
    }
}

func emit(_ fields: [String: Any]) {
    guard var data = try? JSONSerialization.data(withJSONObject: fields) else { return }
    data.append(10)
    try? FileHandle.standardOutput.write(contentsOf: data)
}

private func hostSeconds() -> Double { CMClockGetTime(CMClockGetHostTimeClock()).seconds }

/// One instance per input stream. AVAudioConverter retains its sample-rate conversion
/// state across callbacks when a Bluetooth device delivers 16/24/44.1 kHz input.
final class MonoConverter {
    private var converter: AVAudioConverter?
    private let mono = AVAudioFormat(standardFormatWithSampleRate: Double(Timeline.rate), channels: 1)!

    func samples(_ input: AVAudioPCMBuffer) throws -> [Float] {
        guard input.frameLength > 0 else { return [] }
        if converter?.inputFormat != input.format {
            converter = AVAudioConverter(from: input.format, to: mono)
            converter?.downmix = true
            converter?.primeMethod = .none
        }
        guard let converter, input.format.sampleRate > 0 else { throw CaptureError("Unsupported audio input format.") }
        let capacity = AVAudioFrameCount(ceil(Double(input.frameLength) * mono.sampleRate / input.format.sampleRate)) + 32
        guard let out = AVAudioPCMBuffer(pcmFormat: mono, frameCapacity: capacity) else {
            throw CaptureError("Could not allocate an audio conversion buffer.")
        }
        var provided = false
        var error: NSError?
        let status = converter.convert(to: out, error: &error) { _, state in
            if provided { state.pointee = .noDataNow; return nil }
            provided = true
            state.pointee = .haveData
            return input
        }
        if let error { throw error }
        guard status != .error, let data = out.floatChannelData else { throw CaptureError("Audio conversion failed.") }
        return Array(UnsafeBufferPointer(start: data[0], count: Int(out.frameLength)))
    }
}

final class Recorder: NSObject, SCStreamOutput, SCStreamDelegate {
    let mode: CaptureMode
    let path: String
    private let queue = DispatchQueue(label: "jard.capture.mix")
    private let systemQueue = DispatchQueue(label: "jard.capture.system")
    private let micControl = DispatchQueue(label: "jard.capture.microphone")
    private let systemConverter = MonoConverter()
    private let stopReason: AsyncStream<String>.Continuation
    private var input: InputDevice
    private var engine: AVAudioEngine?
    private var systemStream: SCStream?
    private var observer: NSObjectProtocol?
    // Accessed on micControl only. Retiring an old observer before installing its
    // replacement avoids notifications from a detached device restarting the new tap.
    private var micEnabled = true
    private var generation = 0
    private var retryAfter = 0.0
    private var timer: DispatchSourceTimer?
    // Only the mix queue accesses the fields below.
    private var sink: WaveSink?
    private var timeline: Timeline
    private var epoch = 0.0
    private var accepting = false
    private var levels = [Source: Float]()
    private var lastMicCallback = 0.0
    private var lastTick = 0.0

    init(mode: CaptureMode, path: String, stopReason: AsyncStream<String>.Continuation) {
        self.mode = mode
        self.path = path
        self.stopReason = stopReason
        self.input = InputDevice.current()
        self.timeline = Timeline(mode: mode)
    }

    func start() async throws {
        if mode != .system {
            guard input.id != 0 else { throw CaptureError("Choose an input in System Settings → Sound.") }
            guard AVCaptureDevice.authorizationStatus(for: .audio) == .authorized else {
                throw RecordingPermissionError(source: .mic)
            }
        }
        if mode != .mic {
            do { try await startSystem() }
            catch where isSystemCaptureDenial(error) {
                throw RecordingPermissionError(source: .system)
            }
        }
        if mode != .system { try micControl.sync { try startMic() } }
        try beginWriting()
    }

    func beginWriting() throws {
        try queue.sync {
            sink = try WaveSink(path: path)
            epoch = hostSeconds()
            lastMicCallback = epoch
            lastTick = epoch
            accepting = true
        }
        emit(["event": "started", "mode": mode.rawValue, "microphone": input.name])
        let clock = DispatchSource.makeTimerSource(queue: queue)
        clock.schedule(deadline: .now() + 0.25, repeating: 0.25, leeway: .milliseconds(15))
        clock.setEventHandler { [weak self] in self?.tick() }
        timer = clock
        clock.resume()
    }

    private func startMic() throws {
        let selected = InputDevice.current()
        guard selected.id != 0 else { throw CaptureError("Choose an input in System Settings → Sound.") }
        let converter = MonoConverter()
        let instance = AVAudioEngine()
        let node = instance.inputNode
        let format = node.outputFormat(forBus: 0)
        guard format.channelCount > 0, format.sampleRate > 0 else { throw CaptureError("The microphone is unavailable.") }
        node.installTap(onBus: 0, bufferSize: 1024, format: nil) { [weak self] buffer, time in
            guard let self else { return }
            do {
                let timestamp = time.isHostTimeValid ? AVAudioTime.seconds(forHostTime: time.hostTime)
                    : hostSeconds() - Double(buffer.frameLength) / buffer.format.sampleRate
                self.enqueue(try converter.samples(buffer), at: timestamp, from: .mic)
            } catch { self.stopReason.yield(error.localizedDescription) }
        }
        engine = instance
        instance.prepare()
        try instance.start()
        queue.sync { input = selected }
        generation += 1
        let installed = generation
        observer = NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange,
            object: instance, queue: nil) { [weak self] _ in
            self?.requestMicRestart(generation: installed)
        }
    }

    private func detachMic() {
        if let observer { NotificationCenter.default.removeObserver(observer) }
        observer = nil
        guard let engine else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        self.engine = nil
    }

    private func requestMicRestart(generation expected: Int? = nil, stalled: Bool = false) {
        micControl.async {
            guard self.micEnabled, expected == nil || expected == self.generation else { return }
            let previous = self.queue.sync { self.input }
            let selected = InputDevice.current()
            guard expected != nil || stalled || selected.id != previous.id || self.engine == nil else { return }
            let now = hostSeconds()
            guard now >= self.retryAfter else { return }
            self.retryAfter = now + 0.75
            self.detachMic()
            do {
                try self.startMic()
                emit(["event": "sourceChanged", "microphone": selected.name, "message": "Recording from " + selected.name])
            } catch {
                self.detachMic()
                emit(["event": "sourceChanged", "microphone": "Unavailable", "message": error.localizedDescription])
            }
        }
    }

    private func startSystem() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: false)
        guard let display = content.displays.first else { throw CaptureError("No Mac display is available for system audio capture.") }
        let bundleURL = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let parentBundle = Bundle(url: bundleURL)?.bundleIdentifier
        let excluded = content.applications.filter { parentBundle != nil && $0.bundleIdentifier == parentBundle }
        let filter = SCContentFilter(display: display, excludingApplications: excluded, exceptingWindows: [])
        let config = SCStreamConfiguration()
        config.width = 2; config.height = 2
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        config.queueDepth = 3
        config.showsCursor = false
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true
        config.sampleRate = Timeline.rate
        config.channelCount = 2
        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        // Some macOS releases require a screen output even for an audio-only stream.
        // The callback ignores screen buffers. Only PCM reaches the recording.
        try stream.addStreamOutput(self, type: .screen, sampleHandlerQueue: systemQueue)
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: systemQueue)
        systemStream = stream
        try await stream.startCapture()
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        stopReason.yield("Mac audio capture stopped. " + error.localizedDescription)
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sample: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sample.isValid, sample.dataReadiness == .ready, sample.numSamples > 0 else { return }
        do {
            guard let description = sample.formatDescription,
                  let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(description),
                  let format = AVAudioFormat(streamDescription: asbd) else {
                throw CaptureError("Mac audio returned an unknown audio format.")
            }
            let samples = try sample.withAudioBufferList { buffers, _ -> [Float] in
                guard let pcm = AVAudioPCMBuffer(pcmFormat: format, bufferListNoCopy: buffers.unsafePointer) else {
                    throw CaptureError("Could not read Mac audio samples.")
                }
                pcm.frameLength = AVAudioFrameCount(sample.numSamples)
                return try systemConverter.samples(pcm)
            }
            enqueue(samples, at: sample.presentationTimeStamp.seconds, from: .system)
        } catch { stopReason.yield(error.localizedDescription) }
    }

    private let slots = DispatchSemaphore(value: 128)
    func enqueue(_ samples: [Float], at seconds: Double, from source: Source) {
        guard !samples.isEmpty else { return }
        guard slots.wait(timeout: .now()) == .success else {
            stopReason.yield("Audio storage fell behind. Recording stopped to preserve the completed audio.")
            return
        }
        queue.async {
            defer { self.slots.signal() }
            guard self.accepting else { return }
            guard seconds.isFinite, abs(seconds - hostSeconds()) < 10 else {
                self.stopReason.yield("Audio input lost its timing reference.")
                return
            }
            let frame = Int(((seconds - self.epoch) * Double(Timeline.rate)).rounded())
            if source == .mic { self.lastMicCallback = hostSeconds() }
            self.timeline.add(samples, at: frame, from: source)
            self.levels[source] = max(self.levels[source] ?? 0, samples.reduce(0) { max($0, abs($1)) })
        }
    }

    // A one-second margin accommodates different mic and system callback latencies.
    private func tick() {
        guard accepting else { return }
        let now = hostSeconds()
        let elapsed = now - epoch
        if now - lastTick > 5 {
            stopReason.yield("Recording was interrupted while the Mac or the recorder was paused.")
            return
        }
        lastTick = now
        if mode != .system && now - lastMicCallback > 5 {
            stopReason.yield("The microphone stopped delivering audio. Check the input before recording again.")
        }
        if elapsed >= 3 * 3600 { stopReason.yield("Three-hour recording limit reached.") }
        if mode != .system && (InputDevice.current().id != input.id || now - lastMicCallback > 1) {
            requestMicRestart(stalled: now - lastMicCallback > 1)
        }
        do { try flush(to: Int((elapsed - 1) * Double(Timeline.rate))) }
        catch { stopReason.yield("Could not write the recording. " + error.localizedDescription) }
        if timeline.lateFrames > Timeline.rate / 20 {
            stopReason.yield("Audio input arrived too late. Recording stopped to preserve timing.")
        }
        emit(["event": "levels", "mic": min(1, levels[.mic] ?? 0), "system": min(1, levels[.system] ?? 0),
              "seconds": max(0, elapsed), "microphone": input.name])
        levels.removeAll(keepingCapacity: true)
    }

    private func flush(to frame: Int) throws {
        let end = min(frame, Timeline.limit)
        while timeline.cursor < end {
            let chunk = timeline.render(through: min(end, timeline.cursor + Timeline.rate / 4))
            try sink?.append(chunk)
        }
    }

    func finish(reason: String) async throws {
        let stopped = hostSeconds()
        timer?.cancel()
        micControl.sync {
            micEnabled = false
            detachMic()
        }
        if let systemStream {
            try? await systemStream.stopCapture()
            systemQueue.sync {}
        }
        try queue.sync {
            guard accepting, let sink else { return }
            accepting = false
            // A suspend/clock jump must not expand a truncated take into hours of silence.
            let end = min(Int(((stopped - epoch) * Double(Timeline.rate)).rounded()), timeline.cursor + 3 * Timeline.rate)
            var finalReason = reason
            do { try flush(to: end) }
            catch { finalReason = "Recording stopped after a storage error. " + error.localizedDescription }
            try sink.finish()
            emit(["event": "finished", "reason": finalReason, "path": path, "seconds": Double(sink.frames) / Double(Timeline.rate)])
        }
    }
}
