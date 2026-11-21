"""Owner-scoped advertisement for the local transcription SDK."""
import hashlib
import json
import os
import re
import secrets
import stat
import tempfile

from engine_lifecycle import engine_proof

SDK_VERSION = 1
SDK_DISCOVERY = ".jard-sdk.json"
SDK_MAX_UPLOAD = 64 * 1024 * 1024
SDK_MAX_REQUESTS = 2


def sdk_library_id(root):
    folder = root.stat()
    return hashlib.sha256(f"{folder.st_dev}:{folder.st_ino}".encode("ascii")).hexdigest()


def sdk_identity(root, port):
    return {
        "apiVersion": SDK_VERSION, "pid": os.getpid(), "port": port,
        "libraryId": sdk_library_id(root), "token": secrets.token_hex(32),
    }


def read_sdk_record(root, port):
    try:
        descriptor = os.open(root / SDK_DISCOVERY, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            details = os.fstat(source.fileno())
            if not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid() or details.st_mode & 0o077:
                raise ValueError("invalid discovery file")
            data = source.read(4097)
        if len(data) > 4096:
            raise ValueError("oversized discovery file")
        record = json.loads(data)
        if (type(record["apiVersion"]) is not int or record["apiVersion"] != SDK_VERSION
                or type(record["pid"]) is not int or record["pid"] <= 1
                or type(record["port"]) is not int or record["port"] != port or record["libraryId"] != sdk_library_id(root)
                or not re.fullmatch(r"[0-9a-f]{64}", record["token"])):
            raise ValueError("invalid discovery record")
        return record
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise RuntimeError("Could not verify the running Jaro transcription service.") from error


def publish_sdk(root, port):
    record = sdk_identity(root, port)
    descriptor, temporary = tempfile.mkstemp(prefix=".sdk-", dir=root)
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump(record, output)
            output.write("\n")
        os.replace(temporary, root / SDK_DISCOVERY)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return record


def remove_sdk(root, record):
    path = root / SDK_DISCOVERY
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            details = os.fstat(source.fileno())
            if not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid():
                return
            saved = json.loads(source.read(4096))
        if saved == record:
            path.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def sdk_proof(record, challenge):
    identity = ":".join(str(record[key]) for key in ("apiVersion", "pid", "port", "libraryId"))
    return engine_proof(record["token"], f"{identity}:{challenge}")
