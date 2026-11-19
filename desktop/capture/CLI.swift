import Foundation
import Darwin

@main enum CaptureCLI {
    static func main() async {
        let args = Array(CommandLine.arguments.dropFirst())
        if args == ["devices"] {
            emit(["event": "devices", "microphone": InputDevice.current().name])
            return
        }
        guard args.count == 5, args[0] == "record", args[1] == "--mode", let mode = CaptureMode(rawValue: args[2]),
              args[3] == "--output", args[4].hasPrefix("/") else {
            emit(["event": "error", "message": "Use devices or record --mode both|mic|system --output /absolute/file.wav"])
            exit(2)
        }
        signal(SIGPIPE, SIG_IGN)
        let (events, stop) = AsyncStream<String>.makeStream(bufferingPolicy: .bufferingOldest(1))
        let signals = [SIGTERM, SIGINT].map { number -> DispatchSourceSignal in
            signal(number, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: number, queue: .global())
            source.setEventHandler { stop.yield("Stopped") }
            source.resume()
            return source
        }
        let recorder = Recorder(mode: mode, path: args[4], stopReason: stop)
        DispatchQueue.global().async {
            while let command = readLine() {
                if command == "stop" { stop.yield("Stopped"); return }
            }
            stop.yield("Recorder connection closed")
        }
        do {
            #if JARD_CAPTURE_FIXTURE
            try startSyntheticInput(recorder)
            #else
            try await recorder.start()
            #endif
            var iterator = events.makeAsyncIterator()
            let reason = await iterator.next() ?? "Stopped"
            try await recorder.finish(reason: reason)
        } catch {
            try? await recorder.finish(reason: error.localizedDescription)
            var failure = ["event": "error", "message": error.localizedDescription]
            if let permission = error as? RecordingPermissionError {
                failure["permission"] = permission.source.rawValue
            }
            emit(failure)
            exit(1)
        }
        signals.forEach { $0.cancel() }
    }
}
