"""A transcription-only process whose lifetime is owned by its parent."""
import asyncio
from contextlib import asynccontextmanager, contextmanager
import hmac
from http.client import HTTPConnection, HTTPException as HTTPProtocolError
import json
import os
from pathlib import Path
import re
import secrets
from selectors import DefaultSelector, EVENT_READ
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from threading import Event, Thread
from urllib.parse import urlencode

from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware

from sdk_discovery import SDK_VERSION, sdk_identity, sdk_proof

SDK_PATH = "/api/sdk/v1"


def run_embedded(router, inference_factory, model_factory):
    import uvicorn

    if os.isatty(0) or os.isatty(1):
        raise RuntimeError("Start the embedded Jard helper with piped stdin and stdout.")
    if os.getpgrp() != os.getpid():
        os.setsid()
    finished = Event()
    try:
        with (tempfile.TemporaryDirectory(prefix="jard-embedded-") as scratch,
              socket.socket() as listener,
              os.fdopen(os.dup(1), "w") as handshake):
            # Preserve stdout for the ready record even if a model prints during startup.
            os.dup2(2, 1)
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            root = Path(scratch)
            # Keep model-generated cache files with the owned uploads so exit cleans up both.
            os.environ["TORCHINDUCTOR_CACHE_DIR"] = str(root / "torch")
            record = sdk_identity(root, port)

            @asynccontextmanager
            async def lifespan(app):
                app.state.model = await asyncio.to_thread(model_factory)
                app.state.status = "ready"
                yield

            app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
            app.include_router(router)
            app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1"])
            app.state.sdk = record
            app.state.sdk_embedded = True
            app.state.sdk_upload_root = root
            app.state.inference = inference_factory()
            app.state.sdk_pending = 0
            app.state.draining = False
            server = uvicorn.Server(uvicorn.Config(app, access_log=False, ws="none", timeout_graceful_shutdown=3))

            def watch_parent():
                try:
                    while os.read(0, 4096):
                        pass
                except OSError:
                    pass
                server.should_exit = True
                if not finished.wait(5):
                    shutil.rmtree(root, ignore_errors=True)
                    os.killpg(os.getpgrp(), signal.SIGKILL)

            def report_ready():
                while not finished.wait(.02):
                    if server.should_exit:
                        return
                    if server.started:
                        try:
                            print(json.dumps({"type": "ready", "url": f"http://127.0.0.1:{port}", **record}),
                                  file=handshake, flush=True)
                        except OSError:
                            server.should_exit = True
                        return

            Thread(target=watch_parent, daemon=True).start()
            Thread(target=report_ready, daemon=True).start()
            server.run(sockets=[listener])
            return 0 if server.started else 1
    finally:
        finished.set()


def read_ready(process, timeout):
    data = bytearray()
    deadline = time.monotonic() + timeout
    with DefaultSelector() as selector:
        selector.register(process.stdout, EVENT_READ)
        while b"\n" not in data:
            if not selector.select(max(0, deadline - time.monotonic())):
                raise RuntimeError("The embedded Jard helper did not become ready.")
            block = os.read(process.stdout.fileno(), 4097 - len(data))
            if not block:
                raise RuntimeError("The embedded Jard helper stopped before it was ready. Check that Jard’s model is installed.")
            data.extend(block)
            if len(data) > 4096:
                raise RuntimeError("Jard returned an invalid embedded startup record.")
    try:
        line, _, rest = data.partition(b"\n")
        record = json.loads(line)
        port = record["port"]
        if (record["type"] != "ready" or record["pid"] != process.pid or record["apiVersion"] != SDK_VERSION
                or type(port) is not int or not 1 <= port <= 65535 or record["url"] != f"http://127.0.0.1:{port}"
                or not re.fullmatch(r"[0-9a-f]{64}", record["token"]) or not re.fullmatch(r"[0-9a-f]{64}", record["libraryId"])
                or rest.strip()):
            raise ValueError("unexpected helper")
        return record
    except (TypeError, ValueError, KeyError) as error:
        raise RuntimeError("Jard returned an invalid embedded startup record.") from error


@contextmanager
def owned_transcriber():
    helper = os.environ.get("JARD_EMBEDDED_HELPER")
    command = ([helper, "service", "--embedded"] if helper else
               [sys.executable, str(Path(__file__).with_name("server.py")), "--embedded"])
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          start_new_session=True) as process:
        try:
            yield read_ready(process, 60)
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=5.5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=1.5)
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError("The owned Jard helper did not exit.") from error


def sdk_transcription(record, audio, languages):
    connection = HTTPConnection("127.0.0.1", record["port"], timeout=3)
    try:
        challenge = secrets.token_hex(16)
        connection.request("GET", SDK_PATH + "/identify?" + urlencode({"challenge": challenge}))
        identity = connection.getresponse()
        received = json.loads(identity.read(4096)).get("proof")
        if (identity.status != 200 or not isinstance(received, str)
                or not hmac.compare_digest(received, sdk_proof(record, challenge)) or connection.sock is None):
            raise RuntimeError("Could not verify the local Jaro transcription service.")
        # HTTPConnection must use the proven socket or fail before sending audio.
        connection.auto_open = False
        connection.sock.settimeout(180)
        query = urlencode({"language": languages, "speech_screen": "true"}, doseq=True)
        route = SDK_PATH + "/transcriptions?" + query
        connection.request("POST", route, body=audio, headers={
            "Authorization": "Bearer " + record["token"], "Content-Type": "audio/wav",
        })
        response = connection.getresponse()
        result = json.loads(response.read(8 * 1024 * 1024))
        if response.status != 200:
            detail = result.get("detail", "Local transcription failed.")
            raise RuntimeError(detail.get("message", "Local transcription failed.") if isinstance(detail, dict) else detail)
        text = result["transcript"]
        if not isinstance(text, str):
            raise ValueError("Invalid transcript.")
        return text
    except (HTTPProtocolError, ValueError, AttributeError, KeyError) as error:
        raise RuntimeError("The local Jaro transcription service returned an invalid response.") from error
    finally:
        connection.close()
