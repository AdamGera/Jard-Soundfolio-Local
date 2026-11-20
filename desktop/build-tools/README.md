# Building the Mac app

Use an Apple Silicon Mac on macOS 26 with Xcode command-line tools, Node 24+, Bun, uv, and Native SDK CLI 0.11.1. Install the CLI on the build machine with `npm install -g @native-sdk/cli@0.11.1`. The builder uses Zig 0.16.0 and downloads a checksum-verified copy when needed. The shell links the pinned Native SDK AppKit host and uses the system WKWebView to display the same built React application as the browser. Users installing the finished app need only macOS.

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

Create a draft GitHub release tagged `v<version>` at the build commit. Attach the DMG as `Jaro-arm64.dmg`, the matching source as `Jaro-native-source.zip`, and both generated update files, `Jaro-arm64.zip` and `native-update.json`. Publish it as the latest stable release once all four assets are present. The app reads the feed from the latest release and downloads the version-specific ZIP only after the user chooses to install. Leave the assets at their published URLs.

Forks distributing their own builds should generate their own key with `native update keygen --private-key /private/path/native-update.key` and configure their public key and release URLs before shipping.
