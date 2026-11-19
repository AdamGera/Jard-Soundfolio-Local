import AppKit
import UniformTypeIdentifiers

@main enum JardDialogs {
    static func send(_ result: [String: Any]) {
        guard var bytes = try? JSONSerialization.data(withJSONObject: result) else { exit(1) }
        bytes.append(10)
        FileHandle.standardOutput.write(bytes)
    }

    @MainActor
    static func main() {
        let args = Array(CommandLine.arguments.dropFirst())
        guard let command = args.first,
              (["open-audio", "open-models"].contains(command) && args.count == 1)
              || (command == "save" && args.count == 2)
              || (command == "confirm" && args.count == 3) else {
            send(["error": "Use open-audio, open-models, save <name>, or confirm <title> <message>."])
            exit(2)
        }
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        app.activate()
        switch command {
        case "confirm":
            let alert = NSAlert()
            alert.messageText = args[1]
            alert.informativeText = args[2]
            alert.alertStyle = .warning
            alert.addButton(withTitle: "Confirm")
            alert.addButton(withTitle: "Cancel")
            // Escape always cancels. Return also cancels until the user explicitly picks Confirm.
            alert.buttons[0].keyEquivalent = ""
            alert.window.defaultButtonCell = alert.buttons[1].cell as? NSButtonCell
            send(["confirmed": alert.runModal() == .alertFirstButtonReturn])
        case "save":
            let panel = NSSavePanel()
            panel.canCreateDirectories = true
            panel.nameFieldStringValue = (args[1] as NSString).lastPathComponent
            let result: Any = panel.runModal() == .OK ? (panel.url?.path as Any? ?? NSNull()) : NSNull()
            send(["path": result])
        default:
            let panel = NSOpenPanel()
            let folder = command == "open-models"
            panel.canChooseFiles = !folder
            panel.canChooseDirectories = folder
            panel.allowsMultipleSelection = false
            panel.title = folder ? "Choose the Jard model folder" : "Open audio"
            if !folder {
                // Use the formats supported by the backend FFmpeg decoder.
                let extensions = ["aac", "aif", "aiff", "flac", "m4a", "m4b", "mka", "mkv", "mov",
                                  "mp3", "mp4", "oga", "ogg", "opus", "wav", "wave", "webm"]
                panel.allowedContentTypes = extensions.compactMap { UTType(filenameExtension: $0) }
            }
            let result: Any = panel.runModal() == .OK ? (panel.url?.path as Any? ?? NSNull()) : NSNull()
            send(["path": result])
        }
    }
}
