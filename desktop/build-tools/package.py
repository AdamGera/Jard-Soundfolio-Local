#!/usr/bin/env python3
"""Build (or repackage) Jaro using only pinned inputs and macOS build tools."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import ssl
import subprocess
import tempfile

TOOLS = Path(__file__).resolve().parent
DESKTOP = TOOLS.parent
REPO = DESKTOP.parent
CACHE = DESKTOP / ".build"
OUT = DESKTOP / "out"
PUBLISHER = Path.home() / ".config/jard/soundfolio-local/macos-publisher"
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
        "insights.py", "local_qwen.py", "native_recordings.py", "note_stream.py", "overview_exchanges.py", "place_hints.py",
        "personal_notes.py", "rumble_model.py", "server.py", "session_notes.py", "session_store.py",
        "speaker_alignment.py", "title_words.py", "writing_api.py", "writing_cache.py",
        "overview_autonyms.json", "overview_languages.json", "place_names.json",
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
        "JardUpdates": json.loads((DESKTOP / "app.json").read_bytes())["updates"],
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


def sign_with_publisher(app, directory, *, release=False):
    certificate = directory / "certificate.pem"
    key = directory / "private-key.pem"
    if key.resolve().is_relative_to(REPO) or not key.is_file() or key.stat().st_mode & 0o077:
        raise SystemExit("Keep private-key.pem outside the checkout with owner-only permissions.")
    if not certificate.is_file():
        raise SystemExit("The signing identity is missing certificate.pem.")
    der = ssl.PEM_cert_to_DER_cert(certificate.read_text())
    if release or directory == PUBLISHER.resolve():
        pinned = ssl.PEM_cert_to_DER_cert((TOOLS / "jard-publisher.crt").read_text())
        if der != pinned:
            raise SystemExit("This build must match the pinned publisher certificate.")
    signer = shutil.which("rcodesign") or Path.home() / ".local/bin/rcodesign"
    if not Path(signer).is_file() or subprocess.check_output([signer, "--version"], text=True).strip() != "apple-codesign 0.29.0":
        raise SystemExit("Publisher signing requires rcodesign 0.29.0 on PATH.")

    entitlements = TOOLS / "capture.entitlements"
    run(signer, "sign", "--pem-file", key, "--pem-file", certificate, "--timestamp-url", "none",
        "--entitlements-xml-file", entitlements,
        "--entitlements-xml-file", f"Contents/Helpers/jard-capture:{entitlements}", app)
    run("codesign", "--verify", "--deep", "--strict", app)
    # Check the actual signer of both processes involved in recording access.
    # Apple's requirement language represents a certificate by its SHA-1 fingerprint.
    fingerprint = hashlib.sha1(der).hexdigest()
    with (app / "Contents/Info.plist").open("rb") as plist:
        bundle_id = plistlib.load(plist)["CFBundleIdentifier"]
    for path, identifier in ((app, bundle_id), (app / "Contents/Helpers/jard-capture", "jard-capture")):
        requirement = f'=identifier "{identifier}" and certificate leaf = H"{fingerprint}"'
        run("codesign", "--verify", "--strict", "-R", requirement, path)


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
    signing = parser.add_mutually_exclusive_group()
    signing.add_argument("--publisher-identity", type=Path, metavar="DIRECTORY",
                         help="Sign using a persistent publisher certificate.pem and private-key.pem")
    signing.add_argument("--identity", help="Keychain signing identity, or - for an explicitly ad-hoc development build")
    parser.add_argument("--notary-profile", help="Keychain notarytool profile for publishing")
    parser.add_argument("--dmg", action="store_true")
    parser.add_argument("--update-key", type=Path, help="Sign an installable update ZIP and feed with an external Ed25519 seed")
    parser.add_argument("--notes", default="", help="Release notes shown in the native update prompt")
    opts = parser.parse_args()
    if opts.identity is None and opts.publisher_identity is None and (PUBLISHER.exists() or opts.update_key):
        opts.publisher_identity = PUBLISHER
    if opts.update_key and opts.identity == "-":
        parser.error("Release updates need the persistent publisher certificate or a signing identity.")
    opts.identity = opts.identity or "-"
    if opts.update_key:
        opts.update_key = opts.update_key.expanduser().resolve()
        if opts.update_key.is_relative_to(REPO) or not opts.update_key.is_file():
            parser.error("Use a private update signing key stored outside the checkout.")
    if opts.notary_profile and opts.identity == "-":
        parser.error("Notarization needs a Developer ID Application identity.")
    if opts.publisher_identity and not opts.publisher_identity.expanduser().is_dir():
        parser.error("The publisher identity directory is missing. Restore it from the maintainer backup.")
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
        app = stage / "Jaro.app"
        run("native", "package", "--target", "macos", "--signing", "none", "--binary", DESKTOP / "zig-out/bin/jard-soundfolio-local", "--output", app, cwd=DESKTOP)
        assemble(app)
        if opts.publisher_identity:
            sign_with_publisher(app, opts.publisher_identity.expanduser().resolve(), release=bool(opts.update_key))
        else:
            sign(app, opts.identity)
        if opts.notary_profile:
            notarize(app, opts.notary_profile)
        if opts.update_key:
            with (app / "Contents/Info.plist").open("rb") as stream:
                version = plistlib.load(stream)["CFBundleVersion"]
            archive = stage / "Jaro-arm64.zip"
            # The installer expects exactly one top-level .app, including its symlinks and executable modes.
            run("ditto", "-c", "-k", "--norsrc", "--keepParent", app, archive)
            url = f"https://github.com/AdamGera/Jaro/releases/download/v{version}/{archive.name}"
            feed = stage / "native-update.json"
            run("native", "update", "sign", "--manifest", DESKTOP / "app.json",
                "--target", "macos-aarch64", "--archive", archive, "--url", url,
                "--notes", opts.notes, "--private-key", opts.update_key, "--output", feed)
        if opts.dmg or opts.notary_profile:
            image_root = stage / "image"
            image_root.mkdir()
            image_app = image_root / "Jaro.app"
            app.rename(image_app)
            app = image_app
            (image_root / "Applications").symlink_to("/Applications")
            image = stage / "Jaro-arm64.dmg"
            run("hdiutil", "create", "-srcfolder", image_root, "-volname", "Jaro", "-format", "UDZO", image)
            if opts.identity != "-":
                run("codesign", "--sign", opts.identity, "--timestamp", image)
            if opts.notary_profile:
                notarize(image, opts.notary_profile)
            image.replace(OUT / image.name)
        output = OUT / "Jaro.app"
        if output.exists():
            output.rename(stage / "previous.app")
        app.rename(output)
        if opts.update_key:
            archive.replace(OUT / archive.name)
            feed.replace(OUT / feed.name)
        print(output)


if __name__ == "__main__":
    main()
