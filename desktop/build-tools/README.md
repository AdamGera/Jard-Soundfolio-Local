# Building the Mac app

Use an Apple Silicon Mac on macOS 26 with Xcode command-line tools, Node 24+, Bun, uv, Rust 1.97 with Cargo, and Native SDK CLI 0.11.1. Install the CLI on the build machine with `npm install -g @native-sdk/cli@0.11.1`. The builder uses Zig 0.16.0 and downloads a checksum-verified copy when needed. The shell links the pinned Native SDK AppKit host and uses the system WKWebView to display the same built React application as the browser. The build uses Cargo’s locked dependencies for the isolated JavaScript transformation helper. Users installing the finished app need only macOS.

Build the local disk image from the repository root.

```sh
python3 desktop/build-tools/package.py --dmg
```

The builder writes the app and drag-to-Applications DMG to `desktop/out/`. The build uses the configured Jard publisher identity when present. A fresh development checkout without that identity produces an ad-hoc local build. For a notarized build, use your Developer ID Application identity and a Keychain profile previously configured with Apple's `notarytool`.

```sh
python3 desktop/build-tools/package.py --dmg \
  --identity "Developer ID Application: YOUR TEAM NAME (TEAM_ID)" \
  --notary-profile "YOUR_KEYCHAIN_PROFILE"
```

The build verifies CPython and llama.cpp archives, installs hash-locked wheels into a portable Python runtime, and compiles the required FFmpeg decoders from upstream source. PyTorch, FFmpeg, FFprobe, the language catalogs, and the Metal-enabled writer executable are included inside the app. The model weights are installed separately at first use. Setup can download the pinned files or copy them from an existing `model/` directory. Progress and checksums apply to either source.

For an isolated test, set `RUMBLE_DATA_DIR` to a fresh private temporary directory, `RUMBLE_LOG_DIR` inside that directory, and `RUMBLE_PORT` to an unused local port. These variables work with the bundle's executable and helpers. This keeps fixtures separate from the everyday session library. A packaging smoke test can use `Contents/Helpers/jard-engine model-status`, followed by `Contents/Helpers/jard-engine models --from /path/to/model` and `Contents/Helpers/jard-engine start`.

## Local iteration

Use `/Applications/Jaro.app` for local UI checks. Build and sign a temporary staging bundle with the same publisher. Quit using Jard's regular capture and engine guards, replace the app at the installed path, and reopen it against the same library. Verify the running workspace and use macOS Trash for the superseded bundle and other generated copies. Keep the staging bundle closed until it is installed.

## Updating the sound preview

Regenerate the Settings preview after changing the indicator renderer or `desktop/assets/dictation-sounds/preview.json`. The same schedule controls native cue playback and the web preview.

```sh
preview_build=$(mktemp -d)
xcrun swiftc -O -target arm64-apple-macos26.0 \
  desktop/indicator/DotScopeSimulation.swift desktop/indicator/JardDictationMeter.swift \
  desktop/build-tools/render-sound-preview.swift -o "$preview_build/render"
"$preview_build/render" desktop/assets/dictation-sounds/preview.json src/assets/dictation-preview.png
```

## Reading and watching meetings

The bundle includes `jardctl` in `Contents/Helpers`. The saved-meeting commands read the local session database directly and also work with Jard closed.

```sh
jard='/Applications/Jaro.app/Contents/Helpers/jardctl'
"$jard" meetings list --since 7d --json
"$jard" meetings search '"launch review" pricing' --until today --json
"$jard" meetings show latest --from 01:30 --to 02:15 --md
"$jard" meetings show latest --vtt
"$jard" meetings watch
```

Use the same executable with the `mcp` argument for a local stdio MCP client. It exposes meeting discovery, transcript and notes search, timed transcript reads, saved notes, and `jard-meeting://{meeting_id}` Markdown resources. The CLI and MCP label the rolling transcript while Jard records and finalizes it. Interrupted meetings retain readable provisional text when it was saved. They use the same meeting ID after recovery. Use `--no-in-progress` or `include_in_progress: false` to exclude current recordings and finalizations. Date filters match the meeting's full time span, and a date in `--until` includes that entire local day.

When Jard reopens, it resumes pending meetings from their last committed audio checkpoint once the model is ready and the recorder is idle. Interrupted sessions show `finalizationPending` while waiting. Two unexpected stops during recovery leave the recording and available transcript in Sessions under the original ID.

Meetings Settings controls whole-meeting retention. The 24-hour default covers new meetings, while an explicit setting applies to existing completed meetings too. Expired meetings disappear from Sessions and companion reads. Their audio, transcript, and notes are available together in the macOS Trash. A separate audio window or the confirmed Move meeting audio to Trash action moves only the recording and its source tracks. The transcript and notes stay in Sessions for the lifetime of the meeting.

`meetings watch` emits `{"type":"meeting.finished","id":"…"}` after the final transcript commits. Integrations can use `GET /api/meetings/events` on the local engine with `Authorization: Bearer <native token>`. The token is stored in `.native-token` under the library root. The stream permits one authenticated subscriber, sends keep-alive comments, and carries only the meeting ID. On reconnect, subscribe first, list meetings since the last observed date, and deduplicate by ID.

Model provenance and SHA-256 digests live in `models.json`. The release notes identify ad-hoc builds. For a Developer ID release, verify notarization and stapling and test the downloaded DMG on a separate clean Apple Silicon Mac.

To run the packaged smoke test, pass the built app path to `desktop/build-tools/smoke-package.py`. Add `--models /path/to/model` to exercise real local inference, word timing, speaker labels, and playback. The test creates a temporary copy of the verified weights, runs the bundle using the system PATH, and removes its own library on exit.

## Signing successive builds with the same publisher certificate

For builds distributed without an Apple Developer ID, the packager accepts a publisher-owned code-signing certificate. Use the same certificate and private key for each version, including local installs intended to replace the public app. The certificate identifies the app and its recorder across builds. The separate Ed25519 update key authenticates the GitHub feed.

Install the upstream [rcodesign 0.29.0](https://github.com/indygreg/apple-platform-rs/releases/tag/apple-codesign%2F0.29.0) executable on the build machine. The builder also looks in `~/.local/bin/` for the pinned tool. Store the PEM-encoded code-signing certificate as `certificate.pem` and its private key as `private-key.pem` in a private directory outside the checkout. Restrict the private key to its owner and keep a secure backup of both files. Losing or regenerating the certificate changes the app’s identity. Users receive the signed app in the usual DMG or update ZIP.

```sh
python3 desktop/build-tools/package.py --dmg \
  --publisher-identity "$HOME/.config/jard/soundfolio-local/macos-publisher" \
  --update-key "$HOME/.config/jard/soundfolio-local/native-update.key" \
  --notes "Describe what changed in this version."
```

The maintainer’s default identity directory is `~/.config/jard/soundfolio-local/macos-publisher/`. An update built with this signing mode must match `desktop/build-tools/jard-publisher.crt`. Restore the original identity from backup when moving the build to another Mac. Forks using their own updater should pin their own public certificate.

The packager verifies the app’s signature and checks the signer of both the foreground app and the capture helper. Moving from an ad-hoc release can require a fresh grant. Gatekeeper’s first-launch policy still applies. [Apple’s explanation of app identity](https://developer.apple.com/documentation/technotes/tn3127-inside-code-signing-requirements)

## Publishing in-app updates

Increment the version in `desktop/app.json` and run the build from a clean checkout. Jard's update public key is in that manifest. The private Ed25519 seed belongs to the release maintainer. Keep a secure backup outside the repository. Installed apps trust that key for future updates.

```sh
python3 desktop/build-tools/package.py --dmg \
  --update-key "$HOME/.config/jard/soundfolio-local/native-update.key" \
  --notes "Describe what changed in this version."
```

Create a draft GitHub release tagged `v<version>` at the build commit. Attach the DMG as `Jaro-arm64.dmg`, the matching source as `Jaro-native-source.zip`, and both generated update files, `Jaro-arm64.zip` and `native-update.json`. Publish it as the latest stable release once all four assets are present. The app reads the feed from the latest release and stages the version-specific ZIP in the background after validating the signed feed. The user chooses Restart to Update when the verified bundle is ready. Leave the assets at their published URLs.

To offer a Development channel, generate a separate Ed25519 feed key and provide `--dev-updates /path/to/development.json` when packaging both channels. This file supplies the Development HTTPS `feed_url`, the matching Base64 `public_key`, and an optional HTTPS `release_url` for manual downloads. `--channel dev` stamps a Development build. Pass the Development seed to `--update-key` for that build and publish its archive under `dev-v<version>` as a prerelease. Publish the signed `native-update.json` at the configured Development feed URL. Continue signing each application bundle with the same publisher certificate. Omit `--dev-updates` for bundles that follow the Stable feed alone.

Forks distributing their own builds should generate their own key with `native update keygen --private-key /private/path/native-update.key` and configure their public key and release URLs before shipping.
