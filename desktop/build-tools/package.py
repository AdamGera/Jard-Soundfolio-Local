#!/usr/bin/env python3
"""Build (or repackage) Jard Soundfolio Local using only pinned inputs and macOS build tools."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile

TOOLS = Path(__file__).resolve().parent
DESKTOP = TOOLS.parent
REPO = DESKTOP.parent
CACHE = DESKTOP / ".build"
OUT = DESKTOP / "out"
MACH = {bytes.fromhex(x) for x in ("cffaedfe", "feedfacf", "cafebabe", "bebafeca", "cafebabf")}


def run(*args, cwd=REPO):
    subprocess.run([str(x) for x in args], cwd=cwd, check=True)


def is_mach(path):
    if path.is_symlink() or not path.is_file():
        return False
    with path.open("rb") as stream:
        return stream.read(4) in MACH


def prepare_sdk():
    pinned = "0.11.1"
    node = subprocess.check_output(["node", "-p", "process.versions.node"], text=True)
    if int(node.split(".")[0]) < 24:
        raise SystemExit("Build with Node 24+ on PATH.")
    native = shutil.which("native")
    if not native or not subprocess.check_output([native, "--version"], text=True).startswith(f"native {pinned} "):
        raise SystemExit(f"Install @native-sdk/cli@{pinned} on the build machine.")
    installed = Path(native).resolve().parent.parent
    sdk = Path(os.environ.get("NATIVE_SDK_PATH", installed)).resolve()
    metadata = json.loads((sdk / "package.json").read_bytes())
    if metadata.get("name") != "@native-sdk/cli" or metadata.get("version") != pinned:
        raise SystemExit(f"NATIVE_SDK_PATH must point to @native-sdk/cli@{pinned}.")
    os.environ["NATIVE_SDK_PATH"] = str(sdk)


def copy_engine(resources):
    target = resources / "engine"
    target.mkdir()
    # Only ship engine source and catalogs, even if a checkout holds private files.
    for name in (
        "audio_file.py", "chat_context.py", "diarization.py", "engine_lifecycle.py",
        "insights.py", "local_qwen.py", "native_recordings.py", "note_stream.py",
        "rumble_model.py", "server.py", "session_notes.py", "session_store.py",
        "speaker_alignment.py", "title_words.py", "writing_api.py", "writing_cache.py",
        "overview_autonyms.json", "overview_languages.json",
    ):
        shutil.copy2(REPO / name, target)
    shutil.copytree(REPO / "dist", target / "dist")
    shutil.copytree(REPO / "licenses", resources / "licenses")
    for name in ("LICENSE", "NOTICE"):
        shutil.copy2(REPO / name, resources / "licenses" / name)
    for notice in ("LICENSE", "NOTICE"):
        shutil.copy2(REPO / notice, resources / notice)
    for name in ("COPYING.LGPLv2.1", "LICENSE.md"):
        shutil.copy2(CACHE / "ffmpeg-7.1.1" / name, resources / "licenses" / f"FFmpeg-{name}")
    shutil.copy2(CACHE / "llama-b12521/LICENSE", resources / "licenses/llama.cpp-LICENSE")
    shutil.copy2(TOOLS / "MODEL-NOTICES.txt", resources / "licenses")
    # Ship the FFmpeg source and exact configuration to reproduce the linked codecs.
    shutil.copy2(CACHE / "ffmpeg-7.1.1.tar.xz", resources / "licenses")
    shutil.copy2(TOOLS / "build-codecs.sh", resources / "licenses")
    shutil.copy2(Path(os.environ["NATIVE_SDK_PATH"]) / "LICENSE", resources / "licenses/Native-SDK-LICENSE")
    (target / "desktop/build-tools").mkdir(parents=True)
    shutil.copy2(DESKTOP / "mac_bridge.py", target / "desktop/mac_bridge.py")
    for name in ("helper.py", "model_setup.py", "models.json"):
        shutil.copy2(TOOLS / name, target / "desktop/build-tools")


def assemble(app):
    contents = app / "Contents"
    info_file = contents / "Info.plist"
    with info_file.open("rb") as data:
        info = plistlib.load(data)
    info.update({
        "LSMinimumSystemVersion": "26.0",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        "NSHumanReadableCopyright": "Copyright © 2026 Jard (Joint AI Research & Dynamics)",
        "NSMicrophoneUsageDescription": "Record your voice together with call audio when you choose Record.",
        "NSScreenCaptureUsageDescription": "Capture the audio from your Mac for call recordings, including when using headphones.",
        "NSAudioCaptureUsageDescription": "Include the sound from calls and other apps in the recording.",
    })
    with info_file.open("wb") as data:
        plistlib.dump(info, data)
    resources = contents / "Resources"
    resources.mkdir(exist_ok=True)
    copy_engine(resources)
    shutil.copy2(DESKTOP / "setup.html", resources / "setup.html")
    runtime = contents / "Resources/PythonRuntime"
    shutil.copytree(CACHE / "python", runtime, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    helpers = contents / "Helpers"
    helpers.mkdir(exist_ok=True)
    run("xcrun", "clang", "-O2", "-arch", "arm64", "-mmacosx-version-min=26", TOOLS / "helper.c", "-o", helpers / "jard-engine")
    shutil.copy2(helpers / "jard-engine", helpers / "jard-ui")
    swift = ["xcrun", "swiftc", "-O", "-parse-as-library", "-swift-version", "5", "-target", "arm64-apple-macos26.0"]
    capture = DESKTOP / "capture"
    run(*swift, *(capture / n for n in ("CLI.swift", "Capture.swift", "Recording.swift")), "-o", helpers / "jard-capture")
    run(*swift, capture / "JardDialogs.swift", "-o", helpers / "jard-dialog")
    for name in ("ffmpeg", "ffprobe"):
        shutil.copy2(CACHE / "ffmpeg-7.1.1" / name, helpers)
    llama = helpers / "llama"
    llama.mkdir()
    for path in (CACHE / "llama-b12521").iterdir():
        if path.name == "llama-server" or path.suffix == ".dylib":
            shutil.copy2(path, llama / path.name, follow_symlinks=False)


def sign(app, identity):
    flags = ["codesign", "--force", "--sign", identity]
    if identity != "-":
        flags += ["--options", "runtime", "--timestamp"]
    with (app / "Contents/Info.plist").open("rb") as plist:
        main_executable = app / "Contents/MacOS" / plistlib.load(plist)["CFBundleExecutable"]
    for path in (app / "Contents").rglob("*"):
        if path != main_executable and is_mach(path):
            args = ["--entitlements", TOOLS / "capture.entitlements"] if path.name == "jard-capture" else []
            run(*flags, *args, path)
    run(*flags, "--entitlements", TOOLS / "capture.entitlements", app)
    run("codesign", "--verify", "--deep", "--strict", app)


def notarize(item, profile):
    if item.suffix == ".app":
        upload = item.with_suffix(".zip")
        run("ditto", "-c", "-k", "--keepParent", item, upload)
    else:
        upload = item
    run("xcrun", "notarytool", "submit", upload, "--keychain-profile", profile, "--wait")
    run("xcrun", "stapler", "staple", item)
    run("xcrun", "stapler", "validate", item)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reuse-shell", action="store_true", help="Use the previously compiled desktop shell")
    parser.add_argument("--identity", default="-", help="Developer ID Application signing identity (default: local ad hoc)")
    parser.add_argument("--notary-profile", help="Keychain notarytool profile for publishing")
    parser.add_argument("--dmg", action="store_true")
    opts = parser.parse_args()
    if opts.notary_profile and opts.identity == "-":
        parser.error("Notarization needs a Developer ID Application identity.")
    CACHE.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    prepare_sdk()
    run(TOOLS / "prepare-runtime.sh")
    run("bun", "install", "--frozen-lockfile")
    run("bun", "run", "build")
    if not opts.reuse_shell:
        run(TOOLS / "build-shell.sh")
    # Build entirely in staging so a compile or signing failure preserves the previous app.
    with tempfile.TemporaryDirectory(dir=OUT, prefix=".package-") as stage:
        stage = Path(stage)
        app = stage / "Jard Soundfolio Local.app"
        run("native", "package", "--target", "macos", "--signing", "none", "--binary", DESKTOP / "zig-out/bin/jard-soundfolio-local", "--output", app, cwd=DESKTOP)
        assemble(app)
        sign(app, opts.identity)
        if opts.notary_profile:
            notarize(app, opts.notary_profile)
        if opts.dmg or opts.notary_profile:
            image_root = stage / "image"
            image_root.mkdir()
            image_app = image_root / "Jard Soundfolio Local.app"
            app.rename(image_app)
            app = image_app
            (image_root / "Applications").symlink_to("/Applications")
            image = stage / "Jard Soundfolio Local.dmg"
            run("hdiutil", "create", "-srcfolder", image_root, "-volname", "Jard Soundfolio Local", "-format", "UDZO", image)
            if opts.identity != "-":
                run("codesign", "--sign", opts.identity, "--timestamp", image)
            if opts.notary_profile:
                notarize(image, opts.notary_profile)
            image.replace(OUT / image.name)
        output = OUT / "Jard Soundfolio Local.app"
        if output.exists():
            output.rename(stage / "previous.app")
        app.rename(output)
        print(output)


if __name__ == "__main__":
    main()
