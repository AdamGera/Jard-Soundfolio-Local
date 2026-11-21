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
SPEECH_URL = FILES[0]["url"].rsplit("/", 1)[0] + "/"
SPEECH_FILES = [item for item in FILES if item["url"].startswith(SPEECH_URL)]
SPEECH_TOTAL = sum(item["size"] for item in SPEECH_FILES)
SPEECH_REVISION = FILES[0]["url"].split("/resolve/")[1].split("/")[0]


def directory():
    library = Path(os.environ.get("RUMBLE_DATA_DIR") or "~/Library/Application Support/Rumble").expanduser().resolve()
    return library / "models" / SET_ID


def model_directory(speech_only):
    alternate = os.environ.get("JARD_MODEL_DIR") if speech_only else None
    return Path(alternate).expanduser().resolve() if alternate else directory()


def installed_size_matches(root, item):
    try:
        path = root / item["file"]
        return path.is_file() and path.stat().st_size == item["size"]
    except OSError:
        return False


def status(speech_only=False):
    root = model_directory(speech_only)
    files = SPEECH_FILES if speech_only else FILES
    complete = (root / ".complete").is_file()
    if speech_only:
        complete = complete or (root / ".speech-complete").is_file()
        try:
            revision_matches = (root / "revision").read_text().strip() == SPEECH_REVISION
        except (OSError, UnicodeError):
            revision_matches = False
        # Source checkouts prepared before the packaged installer have only a revision file.
        complete = revision_matches and (complete or root != directory())
    ready = complete and all(installed_size_matches(root, item) for item in files)
    return {"ready": ready, "bytes": SPEECH_TOTAL if speech_only else TOTAL, "directory": str(root)}


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


def install(source, speech_only=False):
    root = model_directory(speech_only)
    files = SPEECH_FILES if speech_only else FILES
    total = SPEECH_TOTAL if speech_only else TOTAL
    marker_name = ".speech-complete" if speech_only else ".complete"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if speech_only and source is None and (ready := status(True))["ready"]:
            emit(event="ready", **ready)
            return
        (root / marker_name).unlink(missing_ok=True)
        completed = 0
        for item in files:
            name = item["file"]
            destination = root / name
            destination.parent.mkdir(exist_ok=True, parents=True)
            partial = destination.with_suffix(destination.suffix + ".partial")

            def progress(size):
                emit(event="progress", file=name, completed=completed + size, total=total)

            if not valid(destination, item):
                progress(0)
                if source is not None or not valid(partial, item):
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
            emit(event="progress", file=name, completed=completed, total=total)
        revision = root / "revision.tmp"
        revision.write_text(SPEECH_REVISION + "\n")
        revision.replace(root / "revision")
        marker = root / (marker_name + ".tmp")
        marker.write_text(SET_ID)
        marker.replace(root / marker_name)
    emit(event="ready", **status(speech_only))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["model-status", "models", "model-path"])
    parser.add_argument("--from", dest="source", type=Path)
    parser.add_argument("--speech-only", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.action == "model-status":
            emit(**status(args.speech_only))
        elif args.action == "model-path":
            print(directory())
        else:
            install(args.source, args.speech_only)
    except (OSError, ValueError, RuntimeError) as error:
        emit(event="error", message=str(error))
        sys.exit(1)
