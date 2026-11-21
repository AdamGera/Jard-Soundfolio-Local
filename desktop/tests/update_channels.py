"""Exercise the native updater against disposable, signed HTTPS feeds."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import plistlib
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
from threading import Thread
from time import monotonic, sleep
from uuid import uuid4


def run(*arguments):
    return subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=30).stdout.strip()


def wait_for(path):
    deadline = monotonic() + 30
    while not path.exists() and monotonic() < deadline:
        sleep(.1)
    assert path.is_file(), f"Updater did not write {path.name}"


def exercise(root, executable):
    identity = "local.jardaudio.update-channels." + uuid4().hex
    assets = root / "releases"
    assets.mkdir()
    keys = {channel: root / f"{channel}.key" for channel in ("stable", "dev")}
    public_keys = {channel: run("native", "update", "keygen", "--private-key", seed)
                   for channel, seed in keys.items()}
    run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-noenc", "-days", "1",
        "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
        "-addext", "extendedKeyUsage=serverAuth", "-addext", "keyUsage=digitalSignature,keyEncipherment",
        "-keyout", root / "server.key", "-out", root / "server.pem")
    run("openssl", "x509", "-in", root / "server.pem", "-outform", "DER", "-out", root / "server.cer")
    requests = []
    ranges = []
    feed_requests = []

    class Releases(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            name = self.path.lstrip("/")
            if (root / "cache-mode").is_file() and name in {"stable.json", "development.json", "alternate-feed.json"}:
                self.cached_feed(name)
                return
            if name == "development.json":
                name = "dev-" + (root / "development-feed").read_text() + ".json"
            if name == "stable.json":
                name = (root / "stable-feed").read_text() + ".json"
            requests.append(name)
            if name == "stalled-feed.json":
                self.send_response(200)
                self.send_header("Content-Length", "128")
                self.end_headers()
                try:
                    for _ in range(24):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        sleep(1)
                except (OSError, ssl.SSLError):
                    return
                return
            if name == "resume.zip" and (root / "resume-behavior").read_text() == "resume":
                self.send_response(302)
                self.send_header("Location", "/resume-cdn.zip")
                self.end_headers()
                return
            file = assets / ("resume.zip" if name == "resume-cdn.zip" else name)
            if "/" in name or not file.is_file():
                self.send_error(404)
                return
            if file.name in {"resume.zip", "resume-full.zip"}:
                self.resumable_archive(file)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(file.stat().st_size))
            self.end_headers()
            with file.open("rb") as source:
                shutil.copyfileobj(source, self.wfile)

        def cached_feed(self, name):
            mode = (root / "cache-mode").read_text()
            incoming = self.headers.get("If-None-Match")
            feed_requests.append((mode, name, incoming, self.headers.get("Cache-Control")))
            if mode in {"redirect", "wrong-tag"} and name == "stable.json":
                self.send_response(302)
                self.send_header("Location", "/alternate-feed.json")
                self.end_headers()
                return
            tag = 'W/"old-state"' if mode == "current" else 'W/"shared-state"'
            body = "stable-four.json" if mode == "stable" else "stable-five.json"
            unchanged = incoming == tag and mode in {"stable", "redirect", "current"}
            if mode in {"unsolicited", "wrong-tag"} or unchanged:
                self.send_response(304)
                self.send_header("ETag", '"wrong-state"' if mode == "wrong-tag" else tag.removeprefix("W/"))
                self.end_headers()
                return
            if mode == "malformed":
                tag = "unquoted"
            elif mode == "current":
                body = "stable.json"
            elif mode in {"invalid-signature", "switch-channel"}:
                body = "dev-first.json"
            payload = (assets / body).read_bytes()
            self.send_response(200)
            self.send_header("ETag", tag)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def resumable_archive(self, file):
            behavior = (root / "resume-behavior").read_text()
            value = self.headers.get("Range")
            assert value and value.startswith("bytes="), value
            start, end = map(int, value.removeprefix("bytes=").split("-"))
            size = file.stat().st_size
            assert 0 <= start <= end < size and end - start < 8 * 1024 * 1024
            ranges.append((file.name, behavior, start, end))
            if behavior == "interrupt" and start:
                self.send_error(503)
                return
            if behavior == "full":
                start, end = 0, size - 1
                self.send_response(200)
            else:
                self.send_response(206)
                reported_start = start - 1 if behavior == "invalid" else start
                self.send_header("Content-Range", f"bytes {reported_start}-{end}/{size}")
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()
            with file.open("rb") as source:
                source.seek(start)
                body = source.read(32768 if behavior == "interrupt" else end - start + 1)
            try:
                self.wfile.write(body)
                self.wfile.flush()
                if behavior == "interrupt":
                    self.connection.shutdown(socket.SHUT_WR)
            except (OSError, ssl.SSLError):
                pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Releases) as server:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(root / "server.pem", root / "server.key")
        server.socket = context.wrap_socket(server.socket, server_side=True)
        origin = f"https://localhost:{server.server_port}"
        stable = {"feed_url": origin + "/stable.json", "public_key": public_keys["stable"]}
        dev = {"feed_url": origin + "/development.json", "public_key": public_keys["dev"],
               "release_url": origin + "/notes"}

        def bundle(folder, channel, version, flavor):
            app = folder / "Fixture.app"
            binary = app / "Contents/MacOS/fixture"
            binary.parent.mkdir(parents=True)
            shutil.copyfile(executable, binary)
            binary.chmod(0o755)
            if flavor == "resume":
                (app / "Contents/Resources").mkdir()
                (app / "Contents/Resources/fixture.bin").write_bytes(os.urandom(9 * 1024 * 1024))
            (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
                "CFBundleIdentifier": identity, "CFBundleVersion": version, "CFBundleShortVersionString": version,
                "CFBundlePackageType": "APPL", "CFBundleExecutable": "fixture", "LSUIElement": True,
                "JardUpdates": {**stable, "channel": channel, "development": dev}, "FixtureFlavor": flavor,
                "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
            }))
            run("codesign", "--force", "--sign", "-", "-r", f'=designated => identifier "{identity}"', app)
            return app

        def publish(asset, signer, channel, version="2.0.0"):
            app = bundle(root / asset, channel, version, asset)
            archive = assets / f"{asset}.zip"
            run("ditto", "-c", "-k", "--norsrc", "--keepParent", app, archive)
            manifest = root / "release-app.json"
            config = stable if signer == "stable" else dev
            manifest.write_text(json.dumps({
                "id": identity, "name": "fixture", "display_name": "Fixture", "version": version,
                "platforms": ["macos"], "updates": {key: config[key] for key in ("feed_url", "public_key")},
            }))
            run("native", "update", "sign", "--manifest", manifest, "--target", "macos-aarch64", "--archive", archive,
                "--url", origin + f"/{archive.name}", "--notes", f"Notes for {asset}",
                "--output", assets / f"{asset}.json", "--private-key", keys[signer])

        publish("stable", "stable", "stable")
        publish("stable-four", "stable", "stable", "4.0.0")
        publish("stable-five", "stable", "stable", "5.0.0")
        publish("dev-first", "dev", "dev")
        publish("dev-second", "dev", "dev")
        publish("dev-wrong-bundle", "dev", "stable")
        publish("resume", "stable", "stable", "6.0.0")
        publish("resume-full", "stable", "stable", "7.0.0")
        shutil.copyfile(assets / "stable.json", assets / "dev-invalid-key.json")
        (root / "development-feed").write_text("first")
        (root / "stable-feed").write_text("stable")
        installed = bundle(root, "stable", "3.0.0", "installed")
        entry = installed / "Contents/MacOS/fixture"
        resume_bundle = bundle(root / "resume-install", "stable", "3.0.0", "resume-installed")
        resume_bundle = resume_bundle.rename(root / "Resume.app")
        resume_entry = resume_bundle / "Contents/MacOS/fixture"
        cache_bundle = bundle(root / "cache-install", "stable", "3.0.0", "cache-installed").rename(root / "Cache.app")
        cache_entry = cache_bundle / "Contents/MacOS/fixture"
        cache = Path.home() / "Library/Caches" / f"{identity}.update-archives"
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            subprocess.run([entry], check=True, timeout=90)
            marker = root / "installed-on-quit"
            wait_for(marker)
            assert not (root / "stable-reopened").exists(), "Install on quit relaunched automatically"
            subprocess.run([entry], check=True, timeout=15)
            print(marker.read_text())
            print((root / "stable-reopened").read_text())
            print((root / "late-stage-fenced").read_text())
            assert requests.count("stable.zip") == 1, requests
            assert requests.count("stable-four.zip") == 2, requests
            assert requests.count("stable-five.zip") == 2, requests
            assert requests.count("stalled-feed.json") == 1, requests
            assert requests.count("dev-wrong-bundle.zip") == 1, requests
            assert requests.count("dev-second.zip") == 1, requests
            assert requests.count("dev-first.zip") == 2, requests
            assert requests.count("dev-invalid-key.json") == 1, requests
            assert requests.count("dev-missing.json") == 1, requests
            print("Native signatures, stale release confirmation, downgrade, equal-version switch, and restart refresh verified.")

            def transfer(name, behavior):
                (root / "stable-feed").write_text(name)
                (root / "resume-behavior").write_text(behavior)
                subprocess.run([resume_entry], check=True, timeout=20)
                return [row[2:] for row in ranges if row[:2] == (name + ".zip", behavior)]

            resumed = transfer("resume", "interrupt")
            assert len(resumed) == 3 and resumed[0][0] == 0 and all(start == 32768 for start, _ in resumed[1:]), resumed
            partial, = cache.glob("stable-*.partial")
            assert partial.stat().st_size == 32768
            invalid = transfer("resume", "invalid")
            assert len(invalid) == 3 and all(start == 32768 for start, _ in invalid), invalid
            assert partial.stat().st_size == 32768 and partial.read_bytes() == (assets / "resume.zip").read_bytes()[:32768]
            before_redirect = requests.count("resume.zip")
            resumed = transfer("resume", "resume")
            assert len(resumed) == 2 and resumed[0][0] == 32768 and resumed[1][0] == resumed[0][1] + 1, resumed
            assert requests.count("resume.zip") == before_redirect + 1 and requests.count("resume-cdn.zip") == 2
            assert not partial.exists()
            transfer("resume-full", "interrupt")
            partial, = cache.glob("stable-*.partial")
            with partial.open("r+b") as stream:
                byte = stream.read(1)
                stream.seek(0)
                stream.write(bytes([byte[0] ^ 0xFF]))
            transfer("resume-full", "tamper")
            assert not partial.exists()
            transfer("resume-full", "interrupt")
            assert transfer("resume-full", "full")[0][0] == 32768
            assert not list(cache.glob("*.partial"))
            print("Interrupted signed downloads resumed across processes and HTTPS redirects, rejected mismatched ranges and altered prefixes, and handled servers that return the complete file.")
            stable_downloads = requests.count("stable-four.zip")
            subprocess.run([cache_entry], check=True, timeout=30)
            tag = 'W/"shared-state"'
            assert [(mode, name, validator) for mode, name, validator, _ in feed_requests] == [
                ("unsolicited", "stable.json", None),
                ("current", "stable.json", None),
                ("current", "stable.json", 'W/"old-state"'),
                ("stable", "stable.json", 'W/"old-state"'),
                ("stable", "stable.json", tag),
                ("stable", "stable.json", None),
                ("redirect", "stable.json", tag),
                ("redirect", "alternate-feed.json", None),
                ("redirect", "stable.json", None),
                ("redirect", "alternate-feed.json", tag),
                ("wrong-tag", "stable.json", None),
                ("wrong-tag", "alternate-feed.json", tag),
                ("malformed", "stable.json", None),
                ("malformed", "stable.json", None),
                ("stable", "stable.json", None),
                ("invalid-signature", "stable.json", tag),
                ("invalid-signature", "stable.json", None),
                ("stable", "stable.json", None),
                ("unsolicited", "stable.json", None),
                ("stable", "stable.json", None),
                ("switch-channel", "development.json", None),
            ], feed_requests
            assert "no-cache" in feed_requests[5][3] and "no-cache" in feed_requests[-3][3], feed_requests
            assert requests.count("stable-four.zip") == stable_downloads + 2
            print("Only verified feeds supplied validators, with fresh restart requests and independent channel and redirect resources.")
        finally:
            server.shutdown()
            thread.join()
            subprocess.run(["defaults", "delete", identity], capture_output=True)
            if cache.is_dir() and not cache.is_symlink():
                shutil.rmtree(cache)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="jard-update-channels-") as work:
        exercise(Path(work).resolve(), Path(sys.argv[1]).resolve())
