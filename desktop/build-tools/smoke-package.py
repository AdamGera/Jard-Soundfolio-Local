#!/usr/bin/env python3
"""Verify a packaged app with system PATH, temporary HOME, and a disposable session library."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import socket
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen


def check_linked_libraries(app):
    # Wheel dylibs can retain their upstream install ID. Only a load command
    # creates a runtime dependency, so inspect those independently from the ID.
    from package import is_mach
    count = 0
    for path in app.rglob("*"):
        if not is_mach(path):
            continue
        count += 1
        commands = subprocess.check_output(["otool", "-l", path], text=True)
        for block in commands.split("Load command ")[1:]:
            lines = block.splitlines()
            fields = [line.strip() for line in lines]
            kind = next((line.split()[-1] for line in fields if line.startswith("cmd ")), "")
            if kind not in {"LC_LOAD_DYLIB", "LC_LOAD_WEAK_DYLIB", "LC_REEXPORT_DYLIB", "LC_LOAD_UPWARD_DYLIB"}:
                continue
            name = next(line[5:].split(" (offset ")[0] for line in fields if line.startswith("name "))
            assert name.startswith(("@", "/usr/lib/", "/System/Library/", "/Library/Apple/")), (path, name)
    print(f"Verified runtime library paths in {count} packaged Mach-O binaries.", flush=True)


def run(app, model_source):
    app = app.resolve()
    helpers = app / "Contents/Helpers"
    subprocess.run(["codesign", "--verify", "--deep", "--strict", app], check=True)
    check_linked_libraries(app)
    with (app / "Contents/Info.plist").open("rb") as f:
        assert plistlib.load(f)["LSMinimumSystemVersion"] == "26.0"
    with tempfile.TemporaryDirectory(prefix="jard package ") as scratch:
        work = Path(scratch)
        library = work / "library"
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        env = dict(HOME=str(work), PATH="/usr/bin:/bin", RUMBLE_DATA_DIR=str(library),
                   RUMBLE_LOG_DIR=str(work / "logs"), RUMBLE_PORT=str(port))
        def helper(*args):
            return subprocess.check_output([str(helpers / args[0]), *args[1:]], cwd=work, env=env, text=True)
        assert not json.loads(helper("jard-engine", "model-status"))["ready"]
        print("Clean model setup and default microphone:", helper("jard-capture", "devices").strip(), flush=True)
        for name in ("ffmpeg", "ffprobe"):
            assert "7.1.1" in helper(name, "-version")
        if model_source is None:
            return
        assert json.loads(helper("jard-engine", "models", "--from", str(model_source.resolve())).splitlines()[-1])["ready"]
        helper("jard-engine", "start")
        def api(path, data=None, headers=None):
            req = Request(base + path, data=data, headers=headers or {})
            return urlopen(req, timeout=120)
        with api("/api/status") as r:
            status = json.load(r)
        expected = hashlib.sha256(os.fsencode(library.resolve())).hexdigest()
        assert status["libraryId"] == expected, "The engine is serving a different library."
        try:
            for _ in range(240):
                with api("/api/status") as r:
                    status = json.load(r)
                if status["status"] != "loading":
                    break
                time.sleep(0.5)
            assert status["status"] == "ready" and status["diarization"], status["status"]
            with api("/") as r:
                assert b"<html" in r.read()
            audio = work / "sample.aiff"
            subprocess.run(["/usr/bin/say", "-o", audio, "The books for the library arrive on Monday."], check=True, env=env)
            with api("/api/transcribe-file?name=sample.aiff&language=en&diarization=true", audio.read_bytes(),
                     {"Content-Type": "application/octet-stream"}) as r:
                assert r.headers["X-Session-ID"]
                key = r.headers["X-Session-ID"]
                events = [json.loads(line) for line in r if line.strip()]
            assert events[-1].get("done"), events[-1]
            with api("/api/sessions/" + key) as r:
                session = json.load(r)
            assert session["status"] == "complete" and "library" in session["transcript"]["text"].lower()
            assert session["transcript"]["words"] and session["transcript"]["segments"]
            with api("/api/sessions/" + key + "/audio") as r:
                assert r.read() == audio.read_bytes()
            print("Packaged speech, word alignment, speakers, web assets, shared session and audio playback passed.", flush=True)
        finally:
            with api("/api/quit", b"", {"Origin": base, "X-Jard-Action": "quit"}) as r:
                assert r.status == 202
            for _ in range(60):
                with socket.socket() as check:
                    if check.connect_ex(("127.0.0.1", port)) != 0:
                        break
                time.sleep(0.5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--models", type=Path, help="Existing pinned model folder to verify and exercise actual inference.")
    args = parser.parse_args()
    run(args.app, args.models)
