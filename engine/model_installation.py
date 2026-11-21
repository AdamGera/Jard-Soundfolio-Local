"""Prepare the pinned speech model through the desktop's verified installer."""
from contextlib import suppress
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

from engine_paths import WORKSPACE_ROOT
from rumble_model import MODEL_DIR


class ModelUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("Rumble2 Flash is unavailable. Run `jardctl model install` or open Jaro to set up the model.")


def model_command(action):
    helper = os.environ.get("JARD_EMBEDDED_HELPER")
    if helper:
        return [helper, action, "--speech-only"]
    installer = WORKSPACE_ROOT / "desktop/build-tools/model_setup.py"
    return [sys.executable, str(installer), action, "--speech-only"]


def model_environment():
    return {**os.environ, "JARD_MODEL_DIR": str(MODEL_DIR.resolve())}


def model_status():
    result = subprocess.run(model_command("model-status"), env=model_environment(), capture_output=True, timeout=10)
    try:
        status = json.loads(result.stdout)
        if result.returncode or type(status["ready"]) is not bool or type(status["bytes"]) is not int:
            raise ValueError("Invalid model status.")
        return status
    except (TypeError, KeyError, ValueError) as error:
        raise RuntimeError("Could not read the pinned Jaro model status.") from error


def install_model(source=None, *, progress=False):
    command = model_command("models")
    if source is not None:
        command.extend(("--from", str(Path(source).resolve(strict=True))))
    failure = None
    installed = None
    shown_progress = False
    try:
        with subprocess.Popen(command, env=model_environment(), stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True) as process:
            try:
                for line in process.stdout:
                    event = json.loads(line)
                    if event.get("event") == "ready":
                        installed = event
                    elif event.get("event") == "error":
                        failure = event.get("message")
                    elif event.get("event") == "progress" and progress:
                        fraction = event["completed"] / max(event["total"], 1)
                        print(f"\rPreparing Rumble2 Flash… {fraction:.0%}", end="", file=sys.stderr, flush=True)
                        shown_progress = True
                if process.wait() or not installed or installed.get("ready") is not True:
                    raise RuntimeError(failure or "Could not install Rumble2 Flash. Retry to resume the download.")
            finally:
                if process.poll() is None:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        with suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGKILL)
    finally:
        if shown_progress:
            print(file=sys.stderr)
    return {key: installed[key] for key in ("ready", "bytes", "directory")}


def ensure_model(*, allow_prompt):
    status = model_status()
    if status["ready"]:
        return
    if allow_prompt and sys.stdin.isatty() and sys.stderr.isatty():
        size = status["bytes"] / 1_000_000_000
        print(f"Download Rumble2 Flash (up to {size:.1f} GB)? [Y/n] ", end="", file=sys.stderr, flush=True)
        answer = sys.stdin.readline()
        if answer and answer.strip().lower() in ("", "y", "yes"):
            install_model(progress=True)
            return
        if not answer:
            print(file=sys.stderr)
    raise ModelUnavailable()
