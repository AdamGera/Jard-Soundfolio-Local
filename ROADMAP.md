# Roadmap

Jaro is an Apple Silicon Mac app with local transcription, system-wide dictation, searchable dictation history, meeting recordings and notes, and a local TypeScript SDK. The desktop builds also include annotated screen capture and a signed updater. See the [user and developer guides](docs/README.md) for current entry points.

## Verify core macOS workflows

- Exercise physical dictation shortcuts, permission recovery, and insertion into more apps.
- Test meeting recovery and audio route changes during real calls. Verify calendar consent and reminders on a test calendar.
- Check screen selection, annotation, and delivery with real macOS permissions and displays.
- Verify the public signed upgrade path from an older install and confirm the installed permission grants survive it.

## Explore experimental workflows

After the core verification, evaluate expanded shortcut bindings, live transcription
controls, and additional meeting and capture workflows with Jaro's local models.

The current desktop target is macOS on Apple Silicon.
