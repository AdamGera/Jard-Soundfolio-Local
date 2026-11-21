import AVFoundation
import CoreAudio
import CoreMedia
import Darwin
import ScreenCaptureKit

enum MeetingSystemAudioMode: String {
    case screen, tap, tapAndScreen

    var backends: [MeetingSystemAudioMode] {
        self == .tapAndScreen ? [.tap, .screen] : [self]
    }
}

private func captureHostBundleID() -> String? {
    let bundleURL = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
    return Bundle(url: bundleURL)?.bundleIdentifier
}

func openScreenSystemAudio(delegate: SCStreamDelegate, output: SCStreamOutput, queue: DispatchQueue) async throws -> SCStream {
    let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: false)
    guard let display = content.displays.first else { throw CaptureError("No Mac display is available for system audio capture.") }
    let parentBundle = captureHostBundleID()
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
    let stream = SCStream(filter: filter, configuration: config, delegate: delegate)
    // ScreenCaptureKit may require a screen output. Only audio callbacks are read.
    try stream.addStreamOutput(output, type: .screen, sampleHandlerQueue: queue)
    try stream.addStreamOutput(output, type: .audio, sampleHandlerQueue: queue)
    try await stream.startCapture()
    return stream
}

func withScreenAudio(_ sample: CMSampleBuffer, _ receive: (AVAudioPCMBuffer, Double) throws -> Void) throws {
    guard sample.isValid, sample.dataReadiness == .ready, sample.numSamples > 0 else { return }
    guard let description = sample.formatDescription,
          let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(description),
          let format = AVAudioFormat(streamDescription: asbd) else {
        throw CaptureError("Mac audio returned an unknown audio format.")
    }
    try sample.withAudioBufferList { buffers, _ in
        guard let pcm = AVAudioPCMBuffer(pcmFormat: format, bufferListNoCopy: buffers.unsafePointer) else {
            throw CaptureError("Could not read Mac audio samples.")
        }
        pcm.frameLength = AVAudioFrameCount(sample.numSamples)
        try receive(pcm, sample.presentationTimeStamp.seconds)
    }
}

private struct AudioTapError: LocalizedError {
    let action: String
    let status: OSStatus
    var errorDescription: String? { "Could not \(action) for Mac audio (\(status))." }
}

private func checkAudioTap(_ status: OSStatus, _ action: String) throws {
    if status != noErr { throw AudioTapError(action: action, status: status) }
}

private final class MeetingAudioTap {
    private var tap: AudioObjectID = 0
    private var device: AudioObjectID = 0
    private var callback: AudioDeviceIOProcID?
    private var running = false

    private static func audioProcess(_ pid: pid_t) -> AudioObjectID? {
        var pid = pid
        var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyTranslatePIDToProcessObject,
            mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var id: AudioObjectID = 0
        var size = UInt32(MemoryLayout.size(ofValue: id))
        return AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address,
                                          UInt32(MemoryLayout.size(ofValue: pid)), &pid, &size, &id) == noErr && id != 0 ? id : nil
    }

    init(hostPID: pid_t?, deliver: @escaping (UnsafePointer<AudioBufferList>, AudioTimeStamp, AVAudioFormat) -> Void) throws {
        let excluded = [getpid(), getppid(), hostPID].compactMap { $0 }.compactMap(Self.audioProcess)
        let description = CATapDescription(monoGlobalTapButExcludeProcesses: excluded)
        description.name = "Jaro Meeting Audio"
        description.isPrivate = true
        description.muteBehavior = .unmuted
        if #available(macOS 26.0, *), let bundle = captureHostBundleID() {
            description.bundleIDs = [bundle]
            description.isProcessRestoreEnabled = true
        }
        do {
            try checkAudioTap(AudioHardwareCreateProcessTap(description, &tap), "create a process tap")
            var address = AudioObjectPropertyAddress(mSelector: kAudioTapPropertyFormat,
                mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
            var asbd = AudioStreamBasicDescription()
            var size = UInt32(MemoryLayout.size(ofValue: asbd))
            try checkAudioTap(AudioObjectGetPropertyData(tap, &address, 0, nil, &size, &asbd), "get the tap format")
            guard let format = AVAudioFormat(streamDescription: &asbd), format.commonFormat == .pcmFormatFloat32,
                  format.channelCount == 1, format.sampleRate.isFinite, format.sampleRate > 0 else {
                throw CaptureError("Mac audio returned an unsupported process tap format.")
            }
            let aggregate: [String: Any] = [
                kAudioAggregateDeviceNameKey: "Jaro Meeting Audio",
                kAudioAggregateDeviceUIDKey: "local.jardaudio.meeting." + UUID().uuidString,
                kAudioAggregateDeviceIsPrivateKey: true,
                kAudioAggregateDeviceTapAutoStartKey: true,
                kAudioAggregateDeviceTapListKey: [[
                    kAudioSubTapUIDKey: description.uuid.uuidString,
                    kAudioSubTapDriftCompensationKey: true,
                ]],
            ]
            try checkAudioTap(AudioHardwareCreateAggregateDevice(aggregate as CFDictionary, &device), "create a tap device")
            try checkAudioTap(AudioDeviceCreateIOProcIDWithBlock(&callback, device, nil) { _, input, time, _, _ in
                deliver(input, time.pointee, format)
            }, "attach the tap callback")
            try checkAudioTap(AudioDeviceStart(device, callback), "start the process tap")
            running = true
        } catch {
            stop()
            throw error
        }
    }

    func stop() {
        if let callback {
            if running { AudioDeviceStop(device, callback) }
            AudioDeviceDestroyIOProcID(device, callback)
            self.callback = nil
            running = false
        }
        if device != 0 { AudioHardwareDestroyAggregateDevice(device); device = 0 }
        if tap != 0 { AudioHardwareDestroyProcessTap(tap); tap = 0 }
    }

    deinit { stop() }
}

protocol MeetingSystemAudioConnection {
    var usable: Bool { get }
    func stop() async
}

private final class MeetingSystemAudioSource: NSObject, SCStreamDelegate, SCStreamOutput, MeetingSystemAudioConnection {
    private let queue = DispatchQueue(label: "jard.capture.meeting-system")
    private let pending = DispatchSemaphore(value: 48)
    private let health = NSLock()
    private var failed = false
    private var lastPacket = hostSeconds()
    private let converter = MonoConverter()
    private let receive: ([Float], Double) -> Void
    private var screen: SCStream?
    private var tap: MeetingAudioTap?

    init(receive: @escaping ([Float], Double) -> Void) { self.receive = receive }

    func start(_ backend: MeetingSystemAudioMode, hostPID: pid_t?) async throws {
        if backend == .tap {
            tap = try MeetingAudioTap(hostPID: hostPID) { [weak self] input, time, format in
                self?.receiveTap(input, at: time, format: format)
            }
            health.withLock { lastPacket = hostSeconds() }
        } else {
            screen = try await openScreenSystemAudio(delegate: self, output: self, queue: queue)
        }
    }

    func stop() async {
        if let screen {
            try? await screen.stopCapture()
            self.screen = nil
        }
        tap?.stop()
        tap = nil
        queue.sync { try? converter.finish(emit: forward) }
    }

    var usable: Bool {
        health.withLock { !failed && (tap == nil || hostSeconds() - lastPacket < 3) }
    }

    private func fail() { health.withLock { failed = true } }

    private func forward(_ samples: [Float], at timestamp: Double) {
        guard timestamp.isFinite, abs(timestamp - hostSeconds()) < 10 else { fail(); return }
        receive(samples, timestamp)
    }

    private func receiveTap(_ input: UnsafePointer<AudioBufferList>, at time: AudioTimeStamp, format: AVAudioFormat) {
        health.withLock { lastPacket = hostSeconds() }
        guard input.pointee.mNumberBuffers > 0 else { return }
        let buffer = input.pointee.mBuffers
        let count = Int(buffer.mDataByteSize) / MemoryLayout<Float>.size
        guard count > 0, count <= 65_536, let data = buffer.mData else { return }
        guard pending.wait(timeout: .now()) == .success else { return }
        let floats = Array(UnsafeBufferPointer(start: data.assumingMemoryBound(to: Float.self), count: count))
        let capturedAt = time.mFlags.contains(.hostTimeValid)
            ? AVAudioTime.seconds(forHostTime: time.mHostTime) : hostSeconds() - Double(count) / format.sampleRate
        queue.async {
            defer { self.pending.signal() }
            guard let pcm = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(count)),
                  let samples = pcm.floatChannelData else { self.fail(); return }
            pcm.frameLength = AVAudioFrameCount(count)
            floats.withUnsafeBufferPointer { samples[0].update(from: $0.baseAddress!, count: count) }
            do { try self.converter.append(pcm, at: capturedAt, emit: self.forward) }
            catch { self.fail() }
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) { fail() }

    func stream(_ stream: SCStream, didOutputSampleBuffer sample: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio else { return }
        do {
            try withScreenAudio(sample) { pcm, time in try converter.append(pcm, at: time, emit: forward) }
        } catch { fail() }
    }
}

func openMeetingSystemAudio(_ backend: MeetingSystemAudioMode, hostPID: pid_t?,
                            receive: @escaping ([Float], Double) -> Void) async throws -> any MeetingSystemAudioConnection {
    let source = MeetingSystemAudioSource(receive: receive)
    do {
        try await source.start(backend, hostPID: hostPID)
        return source
    } catch {
        await source.stop()
        throw error
    }
}

func superviseMeetingSystemAudio(mode: MeetingSystemAudioMode, hostPID: pid_t?,
                                 receive: @escaping (UUID, [Float], Double) -> Void,
                                 change: (Bool, MeetingSystemAudioMode?) -> Void,
                                 open: (MeetingSystemAudioMode, pid_t?, @escaping ([Float], Double) -> Void) async throws
                                     -> any MeetingSystemAudioConnection = openMeetingSystemAudio,
                                 pause: (Duration) async -> Bool = { duration in
                                     do { try await Task.sleep(for: duration); return true }
                                     catch { return false }
                                 }) async {
    var delay = 1
    var unavailable = false
    func reportUnavailable() {
        if !unavailable { change(false, nil); unavailable = true }
    }
    while !Task.isCancelled {
        for backend in mode.backends {
            let id = UUID()
            let source: any MeetingSystemAudioConnection
            do { source = try await open(backend, hostPID) { samples, time in receive(id, samples, time) } }
            catch {
                if Task.isCancelled { return }
                continue
            }
            let opened = hostSeconds()
            if !Task.isCancelled { change(true, backend); unavailable = false }
            while !Task.isCancelled && source.usable {
                if !(await pause(.milliseconds(250))) {
                    await source.stop()
                    return
                }
            }
            await source.stop()
            if Task.isCancelled { return }
            reportUnavailable()
            if hostSeconds() - opened >= 6 { delay = 1 }
        }
        reportUnavailable()
        if !(await pause(.seconds(delay))) { return }
        delay = min(30, delay * 2)
    }
}
