"""Install one verified offline model set and report machine-readable progress to the app."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

MANIFEST = Path(__file__).with_name("models.json")
FILES = json.loads(MANIFEST.read_bytes())
SET_ID = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()[:16]
TOTAL = sum(item["size"] for item in FILES)
SPEECH_REVISION = FILES[0]["url"].split("/resolve/")[1].split("/")[0]


def directory():
    library = Path(os.environ.get("RUMBLE_DATA_DIR") or "~/Library/Application Support/Rumble").expanduser().resolve()
    return library / "models" / SET_ID


def status():
    root = directory()
    ready = (root / ".complete").is_file() and all(
        (root / item["file"]).is_file() and (root / item["file"]).stat().st_size == item["size"]
        for item in FILES
    )
    return {"ready": ready, "bytes": TOTAL, "directory": str(root)}


def emit(**data):
    print(json.dumps(data), flush=True)


def valid(path, item):
    if not path.exists() or path.stat().st_size != item["size"]:
        return False
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest() == item["sha256"]


def fetch(item, partial, report):
    # curl uses macOS trust settings and can resume multi-gigabyte model assets.
    if partial.exists() and partial.stat().st_size >= item["size"]:
        partial.unlink()
    with subprocess.Popen(["/usr/bin/curl", "--fail", "--location", "--silent", "--show-error",
                           "--retry", "3", "--retry-all-errors", "--continue-at", "-", "--output",
                           str(partial), item["url"]]) as child:
        try:
            while child.poll() is None:
                if partial.exists():
                    report(partial.stat().st_size)
                time.sleep(0.25)
            if child.returncode:
                raise RuntimeError(f"Download failed for {item['file']}. Retry to resume.")
        finally:
            if child.poll() is None:
                child.terminate()


def install(source):
    root = directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        (root / ".complete").unlink(missing_ok=True)
        completed = 0
        for item in FILES:
            name = item["file"]
            destination = root / name
            destination.parent.mkdir(exist_ok=True, parents=True)
            partial = destination.with_suffix(destination.suffix + ".partial")

            def progress(size):
                emit(event="progress", file=name, completed=completed + size, total=TOTAL)

            if not valid(destination, item):
                progress(0)
                if source:
                    with (source / name).open("rb") as original, partial.open("wb") as copy:
                        while chunk := original.read(8 * 1024 * 1024):
                            copy.write(chunk)
                            progress(copy.tell())
                else:
                    fetch(item, partial, progress)
                if not valid(partial, item):
                    partial.unlink(missing_ok=True)
                    raise ValueError(f"File verification failed for {name}. Select the pinned model set or download it.")
                partial.replace(destination)
            completed += item["size"]
            emit(event="progress", file=name, completed=completed, total=TOTAL)
        (root / "revision").write_text(SPEECH_REVISION + "\n")
        marker = root / ".complete.tmp"
        marker.write_text(SET_ID)
        marker.replace(root / ".complete")
    emit(event="ready", **status())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["model-status", "models", "model-path"])
    parser.add_argument("--from", dest="source", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "model-status":
            emit(**status())
        elif args.action == "model-path":
            print(directory())
        else:
            install(args.source)
    except (OSError, ValueError, RuntimeError) as error:
        emit(event="error", message=str(error))
        sys.exit(1)
