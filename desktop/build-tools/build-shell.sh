#!/bin/bash
set -euo pipefail
desktop=$(cd -- "$(dirname -- "$0")/.." && pwd)
sdk="${NATIVE_SDK_PATH:?Set NATIVE_SDK_PATH to the @native-sdk/cli 0.11.1 SDK source directory.}"
platform="$sdk/src/platform/macos"
binary="${1:-$desktop/zig-out/bin/jard-soundfolio-local}"
mkdir -p "$(dirname -- "$binary")" "$desktop/.build"
flags=(-O2 -arch arm64 -mmacosx-version-min=26.0 -fobjc-arc -I "$platform")
object="$desktop/.build/native-sdk-0.11.1-appkit.o"
if [[ ! -f "$object" || "$platform/appkit_host.m" -nt "$object" || "$platform/appkit_host.h" -nt "$object" || "$0" -nt "$object" ]]; then
    xcrun clang "${flags[@]}" -Wno-deprecated-declarations -Wno-incompatible-pointer-types -c "$platform/appkit_host.m" -o "$object.$$"
    mv "$object.$$" "$object"
fi
updater="$desktop/.build/native-sdk-0.11.1-update.o"
if [[ ! -f "$updater" || "$sdk/src/updater/c_api.zig" -nt "$updater" || "$sdk/src/updater/feed.zig" -nt "$updater" || "$0" -nt "$updater" ]]; then
    zig=$(command -v zig || true)
    if [[ -z "$zig" || "$("$zig" version)" != "0.16.0" ]]; then
        zig="$HOME/.native/toolchains/zig-0.16.0/zig"
    fi
    if [[ ! -x "$zig" ]]; then
        zig="$desktop/.build/zig-aarch64-macos-0.16.0/zig"
    fi
    if [[ ! -x "$zig" ]]; then
        archive="$desktop/.build/zig-macos-arm64.tar.xz"
        curl --fail --location --retry 3 https://ziglang.org/download/0.16.0/zig-aarch64-macos-0.16.0.tar.xz --output "$archive"
        echo "b23d70deaa879b5c2d486ed3316f7eaa53e84acf6fc9cc747de152450d401489  $archive" | shasum -a 256 -c -
        tar -xf "$archive" -C "$desktop/.build"
    fi
    "$zig" build-obj -target aarch64-macos.26.0 -O ReleaseSmall -lc -fcompiler-rt "$sdk/src/updater/c_api.zig" "-femit-bin=$updater.$$"
    mv "$updater.$$" "$updater"
fi
sources=("$desktop/mac_host.m" "$desktop/jard_interface_language.m" "$desktop/system_dictation.m" "$desktop/jard_login_item.m" "$desktop/jard_recording_environment.m" "$desktop/jard_dictation_media.m" "$desktop/jard_dictation_browser.m" "$desktop/jard_dictation_typing.m" "$desktop/jard_secure_hotkeys.m" "$desktop/jard_updater.m" "$desktop/jard_annotation.m" "$desktop/jard_annotation_ink.m" "$desktop/jard_annotation_region.m" "$desktop/jard_annotation_route_panel.m" "$desktop/jard_annotation_terminal.m" "$desktop/jard_phone_annotation.m" "$desktop/jard_meetings.m" "$desktop/jard_meeting_audio_access.m" "$desktop/jard_meeting_delta.m" "$desktop/meeting_presence.m" "$desktop/jard_calendar.m" "$desktop/jard_meeting_panel.m")
if [[ -n "${JARD_TEST_SOURCE:-}" ]]; then
    [[ "$binary" != "$desktop/zig-out/bin/jard-soundfolio-local" ]] || { echo "Supply a separate output path for the QA host." >&2; exit 1; }
    flags+=(-DJARD_TESTING=1)
    sources+=("$JARD_TEST_SOURCE")
fi
frameworks=()
for name in AppKit WebKit AVFoundation Accelerate AudioToolbox CoreAudio CoreMedia CoreMediaIO IOKit CoreVideo CoreGraphics CoreImage ImageIO MediaToolbox Security ScreenCaptureKit Metal QuartzCore Foundation CoreText UniformTypeIdentifiers ApplicationServices Carbon ServiceManagement EventKit; do
    frameworks+=(-framework "$name")
done
indicator="$desktop/.build/jard-indicator.o"
xcrun swiftc -O -whole-module-optimization -parse-as-library -c -module-name JardIndicator \
    -target arm64-apple-macos26.0 "$desktop/indicator/DotScopeSimulation.swift" \
    "$desktop/indicator/JardDictationMeter.swift" -o "$indicator.$$"
mv "$indicator.$$" "$indicator"
xcrun clang "${flags[@]}" "${sources[@]}" "$object" "$updater" "$indicator" "${frameworks[@]}" \
    -L "$(xcrun --show-sdk-path)/usr/lib/swift" -lswiftCore -o "$binary"
