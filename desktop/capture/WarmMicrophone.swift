import AVFoundation
import CoreAudio
import Foundation

struct WarmMicrophoneRetry {
    private static let initialDelay = 0.25
    private var delay = initialDelay
    private var backingOffStream = false
    private var retryAt = 0.0

    func canOpen(at now: Double, retryNow: Bool = false) -> Bool {
        retryNow || now >= retryAt
    }

    mutating func openFailed(at now: Double) {
        retryAt = now + delay
        delay = min(delay * 2, backingOffStream ? 60 : 5)
    }

    mutating func opened() {
        retryAt = 0
        if !backingOffStream { delay = Self.initialDelay }
    }

    mutating func deliveredSteadily() {
        backingOffStream = false
        delay = Self.initialDelay
    }

    mutating func streamFailed(at now: Double, steady: Bool) {
        if steady {
            deliveredSteadily()
            retryAt = now
        } else {
            backingOffStream = true
            openFailed(at: now)
        }
    }
}

struct WarmMicrophoneDelivery {
    var openedAt: Double
    private var firstPacketAt: Double?
    private var lastPacketAt: Double?
    var failed = false

    init(openedAt: Double) { self.openedAt = openedAt }

    var steady: Bool {
        guard let firstPacketAt, let lastPacketAt else { return false }
        return lastPacketAt - firstPacketAt >= 2 * Recorder.microphoneStallSeconds
    }

    func stalled(at now: Double) -> Bool {
        failed || now - (lastPacketAt ?? openedAt) >= Recorder.microphoneStallSeconds
    }

    mutating func receive(at now: Double) -> Bool {
        if failed || (lastPacketAt != nil && stalled(at: now)) {
            failed = true
            return false
        }
        firstPacketAt = firstPacketAt ?? now
        lastPacketAt = now
        return true
    }
}

private struct WarmInputOpen {
    let generation: Int
    let route: DictationInputRoute
    let device: InputDevice
    let preferred: Bool
}

private final class WarmInputStream {
    private(set) var engine: AVAudioEngine?

    init(_ engine: AVAudioEngine?) { self.engine = engine }

    func close() {
        guard let engine else { return }
        self.engine = nil
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
    }

    deinit { close() }
}

private struct WarmInputStatus: Equatable {
    let phase: String
    let microphone: String
    let message: String
}

/// Own the mic while idle and keep a short in-memory window for capture handoffs.
/// Each accepted stage still uses the ordinary timed WAV recorder.
private final class WarmMicrophone {
    private static let preRollDuration = 0.45
    private static let handoffWindow = 1.5
    private let uid: String?
    private let avoidBluetooth: Bool
    private let lock = NSLock()
    private let events: AsyncStream<WarmEvent>.Continuation
    private let opener = DispatchQueue(label: "jaro.capture.warm-open", qos: .userInitiated)
    private var stream: WarmInputStream?
    private var opening: WarmInputOpen?
    private var device: InputDevice = .unavailable
    private var recent: [MicrophonePacket] = []
    private var standbyStartedAt = 0.0
    private var take: Recorder?
    private var previousRoute: DictationInputRoute?
    private var savedRetry = WarmMicrophoneRetry()
    private var retry = WarmMicrophoneRetry()
    private var delivery: WarmMicrophoneDelivery?
    private var nextStreamGeneration = 0
    private var streamGeneration: Int?
    private var clockResetUID: String?
    private var availability: WarmInputStatus?
    #if JARD_CAPTURE_FIXTURE
    private var fixtureOpenFailures = ProcessInfo.processInfo.environment["JARD_TEST_WARM_OPEN_FAILURES"].flatMap(Int.init)
    private let fixtureSavedInputGate = ProcessInfo.processInfo.environment["JARD_TEST_WARM_SAVED_INPUT_GATE"]
    private let fixtureTracksLid = ProcessInfo.processInfo.environment["JARD_TEST_MICROPHONE_LID_GATE"] != nil
    private let fixtureOpenGate = ProcessInfo.processInfo.environment["JARD_TEST_WARM_OPEN_GATE"]
    private let fixtureGatedUID = ProcessInfo.processInfo.environment["JARD_TEST_WARM_GATED_UID"]
    #endif

    init(uid: String?, avoidBluetooth: Bool, events: AsyncStream<WarmEvent>.Continuation) {
        self.uid = uid
        self.avoidBluetooth = avoidBluetooth
        self.events = events
    }

    private func locked<T>(_ operation: () throws -> T) rethrows -> T {
        lock.lock()
        defer { lock.unlock() }
        return try operation()
    }

    private func currentRoute() -> DictationInputRoute {
        #if JARD_CAPTURE_FIXTURE
        let system = InputDevice(id: 1, name: "Fixture system microphone", uid: "fixture",
                                 transport: kAudioDeviceTransportTypeBuiltIn)
        var available = [system]
        if let uid, uid != system.uid {
            available.append(InputDevice(id: 2, name: "Fixture saved microphone", uid: uid, transport: kAudioDeviceTransportTypeUSB))
        }
        if fixtureTracksLid {
            available.append(InputDevice(id: 3, name: "Fixture USB microphone", uid: "fixture-usb", transport: kAudioDeviceTransportTypeUSB))
        }
        return DictationInputRoute.resolve(savedUID: uid, available: available, systemDefault: system,
                                           lidClosed: MicrophoneLid.closed, avoidBluetooth: avoidBluetooth)
        #else
        return DictationInputRoute.current(savedUID: uid, avoidBluetooth: avoidBluetooth)
        #endif
    }

    func prepare(retryNow: Bool) throws -> Bool {
        #if !JARD_CAPTURE_FIXTURE
        guard AVCaptureDevice.authorizationStatus(for: .audio) == .authorized else {
            closeEngine()
            let error = RecordingPermissionError(source: .mic)
            publishAvailability("unavailable", message: error.localizedDescription)
            throw error
        }
        #endif
        let route = currentRoute()
        #if JARD_CAPTURE_FIXTURE
        let running = true
        #else
        let running = stream?.engine?.isRunning == true
        #endif
        let changedRoute = previousRoute != route
        if previousRoute?.saved != route.saved { savedRetry = WarmMicrophoneRetry() }
        previousRoute = route
        let now = hostSeconds()
        var workingInput = false
        if let observed = locked({ delivery }) {
            let healthy = running && !observed.stalled(at: now)
            if observed.steady { retry.deliveredSteadily() }
            if healthy && device == route.primary {
                publishInput(for: route)
                return true
            }
            if healthy, let connected = connectedInput() {
                workingInput = (connected == route.system || connected.canStandInForSystemInput) &&
                    !(connected.isInternalMicrophone && MicrophoneLid.closed)
            }
            if workingInput {
                publishInput(for: route)
            } else {
                closeEngine()
                if !healthy {
                    retry.streamFailed(at: now, steady: observed.steady)
                    publishAvailability("unavailable", message: "Jaro is reconnecting the microphone.")
                }
            }
        }

        let immediate = changedRoute || retryNow
        if opening != nil || (workingInput && retryNow) { return workingInput }
        guard retry.canOpen(at: now, retryNow: immediate) else { return workingInput }
        if route.hasFallback, let saved = route.saved,
           savedRetry.canOpen(at: now, retryNow: immediate) {
            open(saved, route: route, preferred: true)
        } else {
            let target = route.hasFallback ? route.system : route.primary
            if !workingInput || (target.id != 0 && device != target) {
                do {
                    open(try route.require(target), route: route, preferred: false)
                } catch {
                    retry.openFailed(at: now)
                    publishAvailability("unavailable", message: error.localizedDescription)
                    throw error
                }
            }
        }
        return workingInput
    }

    private func open(_ selected: InputDevice, route: DictationInputRoute, preferred: Bool) {
        nextStreamGeneration += 1
        let request = WarmInputOpen(generation: nextStreamGeneration, route: route, device: selected, preferred: preferred)
        opening = request
        let resetClock = !selected.uid.isEmpty && clockResetUID == selected.uid
        if resetClock { clockResetUID = nil }
        opener.async { [weak self, events] in
            guard let self else { return }
            let result = Result {
                if resetClock { try self.resetClock(for: selected) }
                return WarmInputStream(try self.openInput(selected, generation: request.generation))
            }
            events.yield(.opened(request, result))
        }
    }

    func acceptOpen(_ request: WarmInputOpen, result: Result<WarmInputStream, Error>, canSwitch: Bool) throws {
        let isCurrent = opening?.generation == request.generation
        if isCurrent { opening = nil }
        guard isCurrent && request.route == currentRoute() else {
            if case let .success(replacement) = result { retire(replacement) }
            return
        }
        switch result {
        case let .success(replacement):
            guard canSwitch else {
                retire(replacement)
                return
            }
            let previous = stream
            stream = replacement
            device = request.device
            locked {
                streamGeneration = request.generation
                delivery = WarmMicrophoneDelivery(openedAt: hostSeconds())
                standbyStartedAt = 0
                recent.removeAll()
            }
            retire(previous)
            clockResetUID = nil
            retry.opened()
            if request.preferred { savedRetry.opened() }
            publishInput(for: request.route)
        case let .failure(error):
            let now = hostSeconds()
            if error is RecordingPermissionError {
                if canSwitch {
                    closeEngine()
                    publishAvailability("unavailable", message: error.localizedDescription)
                }
                throw error
            }
            if request.preferred {
                savedRetry.openFailed(at: now)
            } else {
                retry.openFailed(at: now)
                if stream == nil { publishAvailability("unavailable", message: error.localizedDescription) }
                throw error
            }
        }
    }

    private func publishInput(for route: DictationInputRoute) {
        let fallback = uid.map { $0 != device.uid } ?? (device != route.system)
        publishAvailability(fallback ? "fallback" : "ready", microphone: device.name)
    }

    private func publishAvailability(_ phase: String, microphone: String = "", message: String = "") {
        let status = WarmInputStatus(phase: phase, microphone: microphone, message: message)
        guard status != availability else { return }
        availability = status
        emit(["event": "warmAvailability", "phase": phase, "microphone": microphone, "message": message])
    }

    private func resetClock(for selected: InputDevice) throws {
        #if JARD_CAPTURE_FIXTURE
        emit(["event": "fixtureWarmClockReset"])
        #else
        let reset = resetMicrophoneClock(selected)
        emit(["event": "microphoneClockReset", "result": reset.rawValue])
        if reset == .restoreFailed { throw CaptureError("Could not restore the microphone’s sample rate.") }
        #endif
    }

    private func openInput(_ selected: InputDevice, generation: Int) throws -> AVAudioEngine? {
        #if JARD_CAPTURE_FIXTURE
        if let failures = fixtureOpenFailures {
            emit(["event": "fixtureWarmOpen", "failing": failures > 0, "time": hostSeconds()])
            if failures > 0 {
                fixtureOpenFailures = failures - 1
                throw CaptureError("Fixture microphone could not open.")
            }
        }
        if fixtureSavedInputGate != nil || fixtureTracksLid {
            let failing = fixtureSavedInputGate.map { selected.uid == uid && !FileManager.default.fileExists(atPath: $0) } ?? false
            emit(["event": "fixtureWarmRouteOpen", "uid": selected.uid, "failing": failing, "time": hostSeconds()])
            if failing { throw CaptureError("Fixture saved microphone could not open.") }
        }
        if let gate = fixtureOpenGate, fixtureGatedUID == nil || fixtureGatedUID == selected.uid,
           !FileManager.default.fileExists(atPath: gate) {
            emit(["event": "fixtureWarmOpening", "uid": selected.uid])
            let deadline = hostSeconds() + 10
            while !FileManager.default.fileExists(atPath: gate) && hostSeconds() < deadline {
                Thread.sleep(forTimeInterval: 0.02)
            }
            guard FileManager.default.fileExists(atPath: gate) else { throw CaptureError("Fixture open timed out.") }
        }
        return nil
        #else
        guard AVCaptureDevice.authorizationStatus(for: .audio) == .authorized else {
            throw RecordingPermissionError(source: .mic)
        }
        let converter = MonoConverter()
        return try openMicrophone(selected, pinDevice: true) { [weak self] buffer, time in
            guard let self else { return }
            let timestamp = time.isHostTimeValid ? AVAudioTime.seconds(forHostTime: time.hostTime)
                : hostSeconds() - Double(buffer.frameLength) / buffer.format.sampleRate
            do {
                try converter.append(buffer, at: timestamp) { self.receive($0, time: $1, generation: generation) }
            } catch {
                self.locked {
                    guard self.streamGeneration == generation else { return }
                    self.delivery?.failed = true
                    self.take?.microphoneFailed(error.localizedDescription)
                }
            }
        }
        #endif
    }

    private func receive(_ samples: [Float], time: Double, generation: Int) {
        locked {
            guard generation == streamGeneration else { return }
            if delivery?.receive(at: hostSeconds()) == false {
                take?.microphoneFailed("The microphone stopped delivering audio.", stalled: true)
                return
            }
            recent.append((samples, time))
            let cutoff = time - Self.handoffWindow
            recent.removeAll { $0.time < cutoff }
            // Also bound memory if the device's clock is interrupted.
            let overflow = recent.count - 256
            if overflow > 0 { recent.removeFirst(overflow) }
            take?.enqueue(samples, at: time, from: .mic)
        }
    }

    private func connectedInput() -> InputDevice? {
        #if JARD_CAPTURE_FIXTURE
        return device
        #else
        return device.currentConnection()
        #endif
    }

    func checkActiveDevice() {
        let connected = connectedInput()
        if (connected ?? device).isInternalMicrophone && MicrophoneLid.closed {
            locked { take?.microphoneFailed(MicrophoneLid.unavailableMessage) }
        } else if connected == nil {
            locked { take?.microphoneFailed("The microphone disconnected during dictation.") }
        }
    }

    func begin(path: String, preRoll: Bool, queuedAt: Double?, stop: AsyncStream<String>.Continuation) throws {
        try locked {
            guard take == nil else { throw CaptureError("A warm take is already recording.") }
            let recorder = Recorder(mode: .mic, path: path, stopReason: stop, externalMicrophone: true, detailedMeter: true)
            #if JARD_CAPTURE_FIXTURE
            if recent.isEmpty { recent = [(Array(repeating: -0.25, count: 960), hostSeconds() - 0.12)] }
            #endif
            let now = hostSeconds()
            let queued = queuedAt.flatMap { $0.isFinite && $0 > 0 ? min($0, now) : nil }
            let limit = queued == nil ? Self.preRollDuration : Self.handoffWindow
            let boundary = if let queued {
                max(now - limit, queued - Self.preRollDuration)
            } else {
                max(now - limit, standbyStartedAt)
            }
            let fresh = preRoll ? microphonePreRoll(recent, from: boundary) : []
            let usingFallback = uid.map { $0 != device.uid } ?? false
            #if JARD_CAPTURE_FIXTURE
            guard let generation = streamGeneration else { throw CaptureError("Fixture stream is unavailable.") }
            try startSyntheticInput(recorder, input: device, usingDefault: usingFallback, preRoll: fresh, preRollLimit: limit) { [weak self] samples, time in
                self?.receive(samples, time: time, generation: generation)
            }
            #else
            try recorder.beginWriting(input: device, usingDefault: usingFallback,
                                      from: fresh.first?.time, preRollLimit: limit)
            for packet in fresh { recorder.enqueue(packet.samples, at: packet.time, from: .mic) }
            #endif
            take = recorder
        }
    }

    func finish(reason: String) async throws {
        let recorder = locked {
            let recorder = take
            take = nil
            standbyStartedAt = hostSeconds()
            return recorder
        }
        if let recorder {
            defer {
                if let recovery = recorder.warmMicrophoneRecovery {
                    let now = hostSeconds()
                    if recovery == .renegotiate { clockResetUID = device.uid }
                    let steady = recovery == .renegotiate || locked { delivery?.steady == true }
                    closeEngine()
                    retry.streamFailed(at: now, steady: steady)
                    if recovery == .reopen {
                        publishAvailability("unavailable", message: "Jaro is reconnecting the microphone.")
                    }
                    #if JARD_CAPTURE_FIXTURE
                    emit(["event": "fixtureWarmReset", "reason": recovery.rawValue])
                    #endif
                }
            }
            try await recorder.finish(reason: reason)
        }
    }

    func requestFinish(releasedAt: Double) {
        locked { take?.requestFinish(releasedAt: releasedAt) }
    }

    func closeEngine() {
        locked {
            streamGeneration = nil
            delivery = nil
            standbyStartedAt = 0
            recent.removeAll()
        }
        let previous = stream
        stream = nil
        retire(previous)
    }

    private func retire(_ retired: WarmInputStream?) {
        guard let retired else { return }
        opener.async { retired.close() }
    }

    deinit { closeEngine() }
}

private enum WarmEvent {
    case command(String)
    case ended(Int, String)
    case opened(WarmInputOpen, Result<WarmInputStream, Error>)
    case maintenance
    case eof
}

private struct WarmStart: Decodable {
    let start: String
    let preRoll: Bool?
    let queuedAt: Double?
}

func runWarmMicrophone(uid: String?, avoidBluetooth: Bool) async {
    #if !JARD_CAPTURE_FIXTURE
    guard AVCaptureDevice.authorizationStatus(for: .audio) == .authorized else {
        emit(["event": "error", "message": RecordingPermissionError(source: .mic).localizedDescription])
        return
    }
    #endif
    let (events, queue) = AsyncStream<WarmEvent>.makeStream()
    let mic = WarmMicrophone(uid: uid, avoidBluetooth: avoidBluetooth, events: queue)
    emit(["event": "warmReady"])
    DispatchQueue.global().async {
        while let line = readLine() { queue.yield(.command(line)) }
        queue.yield(.eof)
    }
    let maintenance = Task {
        while !Task.isCancelled {
            do { try await Task.sleep(for: .milliseconds(250)) } catch { return }
            queue.yield(.maintenance)
        }
    }
    defer {
        queue.finish()
        maintenance.cancel()
    }
    var generation = 0
    var stop: AsyncStream<String>.Continuation?
    var pending: WarmStart?

    func failPending(_ error: Error) {
        guard pending != nil else { return }
        pending = nil
        var event: [String: Any] = ["event": "error", "message": error.localizedDescription]
        if let permission = error as? RecordingPermissionError { event["permission"] = permission.source.rawValue }
        emit(event)
        emit(["event": "takeEnded"])
    }

    func abandonPending() {
        guard pending != nil else { return }
        pending = nil
        emit(["event": "openingAbandoned"])
        emit(["event": "takeEnded"])
    }

    func refreshInput(retryNow: Bool = false) {
        do {
            let ready = try mic.prepare(retryNow: retryNow)
            guard ready, let start = pending else { return }
            let (reasons, continuation) = AsyncStream<String>.makeStream(bufferingPolicy: .bufferingOldest(1))
            try mic.begin(path: start.start, preRoll: start.preRoll ?? true, queuedAt: start.queuedAt, stop: continuation)
            pending = nil
            stop = continuation
            generation += 1
            let id = generation
            Task {
                var iterator = reasons.makeAsyncIterator()
                if let reason = await iterator.next() { queue.yield(.ended(id, reason)) }
            }
        } catch {
            failPending(error)
        }
    }

    refreshInput()
    for await event in events {
        switch event {
        case let .command(line):
            if let release = dictationReleaseTime(line) {
                if stop == nil { abandonPending() }
                else { mic.requestFinish(releasedAt: release) }
            } else if line == "stop" || line == "cancel" {
                if stop == nil { abandonPending() }
                else { stop?.yield(line == "cancel" ? "Cancelled" : "Stopped") }
            } else if stop == nil, pending == nil, let data = line.data(using: .utf8),
                      let start = try? JSONDecoder().decode(WarmStart.self, from: data), start.start.hasPrefix("/") {
                pending = start
                refreshInput(retryNow: true)
            }
        case let .opened(request, result):
            do {
                try mic.acceptOpen(request, result: result, canSwitch: stop == nil)
                if stop == nil { refreshInput() }
            } catch {
                failPending(error)
            }
        case let .ended(id, reason):
            guard id == generation, let active = stop else { continue }
            active.finish()
            stop = nil
            do { try await mic.finish(reason: reason) }
            catch { emit(["event": "error", "message": error.localizedDescription]) }
            emit(["event": "takeEnded"])
        case .maintenance:
            if stop == nil { refreshInput() }
            else { mic.checkActiveDevice() }
        case .eof:
            stop?.finish()
            try? await mic.finish(reason: "Recorder connection closed")
            mic.closeEngine()
            return
        }
    }
}
