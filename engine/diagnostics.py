"""Local diagnostic files and an offline tail reader."""
from contextlib import suppress
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
import time

EVENT_LOG = "live.ndjson"
PROCESS_LOG = "server.log"
TAIL_BYTES = 4 * 1024 * 1024
EVENT_LIMIT = 10 * 1024 * 1024
EVENT_KEEP = EVENT_LIMIT // 2


def log_directory():
    return Path(os.environ.get("RUMBLE_LOG_DIR") or Path.home() / "Library/Logs/Jaro")


def open_log(name, *, append=False):
    path = log_directory() / name
    if append:
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        directory = path.parent.lstat()
        if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.geteuid():
            raise OSError("Jaro needs a log directory owned by the current user.")
    flags = os.O_RDWR | os.O_APPEND | os.O_CREAT if append else os.O_RDONLY
    descriptor = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid() or details.st_mode & 0o022:
            raise OSError("Jaro logs must be regular files owned by the current user.")
        if append and details.st_mode & 0o077:
            os.fchmod(descriptor, 0o600)
        return os.fdopen(descriptor, "a+b" if append else "rb")
    except BaseException:
        os.close(descriptor)
        raise


def write_event(kind, phase, *, inference_ms=None):
    fields = {"inferenceMs": inference_ms} if type(inference_ms) is int and inference_ms >= 0 else {}
    _append_event(kind, phase, fields)


def write_transform_event(phase, **fields):
    _append_event("transformation", phase, fields)


def _append_event(kind, phase, fields):
    event = {"time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "kind": kind, "phase": phase, **fields}
    line = json.dumps(event, separators=(",", ":")).encode() + b"\n"
    with suppress(OSError), open_log(EVENT_LOG, append=True) as log:
        fcntl.flock(log, fcntl.LOCK_EX | fcntl.LOCK_NB)
        size = os.fstat(log.fileno()).st_size
        if size >= EVENT_LIMIT:
            log.seek(size - EVENT_KEEP)
            log.readline()
            recent = log.read(EVENT_KEEP)
            log.truncate(0)
            log.write(recent)
        log.write(line)


def initial_tail(log, lines, follow):
    size = os.fstat(log.fileno()).st_size
    start = max(0, size - TAIL_BYTES)
    log.seek(max(0, start - 1))
    if start and log.read(1) != b"\n" and not log.readline(TAIL_BYTES).endswith(b"\n"):
        return b"", True
    data = log.read(max(0, size - log.tell()))
    if follow and data and not data.endswith(b"\n"):
        ending = data.rfind(b"\n") + 1
        log.seek(ending - len(data), os.SEEK_CUR)
        data = data[:ending]
    if not lines:
        return b"", False
    start_of_line = len(data) - int(data.endswith(b"\n"))
    for _ in range(lines):
        start_of_line = data.rfind(b"\n", 0, start_of_line)
        if start_of_line < 0:
            break
    return data[start_of_line + 1:], False


def tail_log(name, lines, follow, output):
    with open_log(name) as log:
        details = os.fstat(log.fileno())
        identity = (details.st_dev, details.st_ino)
        data, skipping = initial_tail(log, lines, follow)
        position = log.tell()
    output.write(data)
    output.flush()
    while follow:
        time.sleep(.25)
        try:
            log = open_log(name)
        except FileNotFoundError:
            continue
        with log:
            details = os.fstat(log.fileno())
            current = (details.st_dev, details.st_ino)
            if current != identity or details.st_size < position:
                identity, position, skipping = current, 0, False
            log.seek(position)
            while line := log.readline(TAIL_BYTES):
                complete = line.endswith(b"\n")
                if not complete and not skipping and len(line) < TAIL_BYTES:
                    break
                position = log.tell()
                if complete and not skipping:
                    output.write(line)
                skipping = not complete
        output.flush()
