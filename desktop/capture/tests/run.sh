#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
build="$(mktemp -d)"
trap 'rm -rf "$build"' EXIT
xcrun swiftc -O -swift-version 5 CLI.swift Capture.swift Recording.swift MeetingSystemAudio.swift WarmMicrophone.swift -o "$build/jard-capture"
"$build/jard-capture" devices
xcrun swiftc -O -swift-version 5 Capture.swift Recording.swift MeetingSystemAudio.swift WarmMicrophone.swift tests/CaptureTests.swift -o "$build/tests"
"$build/tests" "$@"
xcrun swiftc -O -swift-version 5 -D JARD_CAPTURE_FIXTURE CLI.swift Capture.swift Recording.swift MeetingSystemAudio.swift WarmMicrophone.swift tests/SyntheticInput.swift -o "$build/lifecycle"
python3 tests/lifecycle.py "$build/lifecycle"
python3 tests/warm_lifecycle.py "$build/lifecycle"
