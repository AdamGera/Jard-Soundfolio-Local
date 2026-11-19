#!/bin/bash
set -eu
cd -- "$(dirname -- "$0")/.."
bundle="Jard Audio Launcher.app"
xcrun clang -fobjc-arc -Wall -Werror -arch arm64 -mmacosx-version-min=13.0 -framework Cocoa \
  -o "$bundle/Contents/MacOS/JardLauncher" welcome/launcher.m
/usr/bin/codesign --force --sign - "$bundle"
