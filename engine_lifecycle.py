"""Coordination shared by the browser launcher and the packaged app."""
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import socket
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


HOST = "127.0.0.1"
PORT = int(os.environ.get("RUMBLE_PORT", 8765))
URL = f"http://{HOST}:{PORT}"
API_VERSION = 3
LEASE_SECONDS = 90


def library_path():
    return Path(os.environ.get("RUMBLE_DATA_DIR") or "~/Library/Application Support/Rumble").expanduser().resolve()


def library_id(root):
    return hashlib.sha256(os.fsencode(root)).hexdigest()


@contextmanager
def startup_lock():
    root = library_path()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / ".launch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def install_token(root):
    # Only the process holding .server.lock can create this file.
    path = root / ".native-token"
    if path.is_file():
        token = path.read_text().strip()
        if len(token) == 64:
            return token
    token = secrets.token_hex(32)
    partial = root / ".native-token.new"
    with open(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as output:
        output.write(token + "\n")
    os.replace(partial, path)
    return token


def engine_proof(token, challenge):
    return hmac.digest(token.encode(), challenge.encode("ascii"), "sha256").hex()


def running_engine():
    """None if the port is free. A busy incompatible port is a recoverable launch error."""
    try:
        with socket.create_connection((HOST, PORT), timeout=0.5):
            pass
    except ConnectionRefusedError:
        return None
    except OSError as error:
        raise RuntimeError(f"Could not reach {URL}: {error}") from error
    challenge = secrets.token_hex(16)
    try:
        with urlopen(f"{URL}/api/status?challenge={challenge}", timeout=2) as response:
            info = json.loads(response.read(32_768))
        if info.get("model") != "Reson8/Rumble-2-Flash":
            raise ValueError("unexpected server")
    except (OSError, URLError, ValueError, AttributeError) as error:
        raise RuntimeError(f"Another application is using {URL}. Close it and reopen Jard Audio.") from error
    if info.get("apiVersion") != API_VERSION:
        raise RuntimeError("Another Jard Audio version is running. Quit it, then reopen Jard Audio.")
    root = library_path()
    if info.get("libraryId") != library_id(root):
        raise RuntimeError("The running Jard Audio engine is using a different session library.")
    try:
        token = (root / ".native-token").read_text().strip()
    except OSError as error:
        raise RuntimeError("Could not verify the running Jard Audio engine.") from error
    if not hmac.compare_digest(info.get("proof", ""), engine_proof(token, challenge)):
        raise RuntimeError("Could not verify the running Jard Audio engine.")
    return info


class ClientLeases:
    def __init__(self):
        self.deadlines = {}
        self.streams = set()
        self.last_activity = time.monotonic()

    def renew(self, client):
        now = time.monotonic()
        self.deadlines[client] = now + LEASE_SECONDS
        self.last_activity = now

    def detach(self, client):
        self.deadlines.pop(client, None)

    def idle(self, busy):
        now = time.monotonic()
        self.deadlines = {k: v for k, v in self.deadlines.items() if v > now}
        if busy or self.streams or self.deadlines:
            self.last_activity = now
            return False
        return now - self.last_activity >= 30
