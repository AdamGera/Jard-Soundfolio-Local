"""App-owned device and file operations for the shared React interface.

The Native SDK host accepts messages from its own local setup document and the
authenticated loopback engine. It sends one request per line over a private pipe.
The engine remains the only owner of sessions and model inference.
"""

import asyncio
import contextlib
import datetime
import fcntl
import json
import mimetypes
import os
import shutil
import signal
import sys
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine_lifecycle import URL, library_path, running_engine


def send(message):
    try:
        print(
            json.dumps(message, ensure_ascii=False, separators=(",", ":")), flush=True
        )
    except (BrokenPipeError, OSError):
        # Closing a window must still finalize the recording and release the engine lease.
        pass


def emit(event, **values):
    send({"event": "jard-capture", "detail": {"event": event, **values}})


def explain(error):
    if isinstance(error, httpx.HTTPStatusError):
        with contextlib.suppress(ValueError, KeyError):
            return error.response.json()["detail"]
    return str(error) or type(error).__name__


async def run_helper(path, *args):
    child = await asyncio.create_subprocess_exec(
        str(path), *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await child.communicate()
    finally:
        if child.returncode is None:
            child.terminate()
            await child.wait()
    if child.returncode:
        raise RuntimeError(
            err.decode(errors="replace").strip() or f"{Path(path).name} failed."
        )
    return out.decode()


def transcript_export(data):
    fields = (
        "id",
        "created",
        "audioUrl",
        "audioPath",
        "recording",
        "live",
        "diarization",
        "model",
        "modelRevision",
        "speakerModel",
        "speakerModelRevision",
    )
    return {
        **{k: data[k] for k in fields},
        "source": data["audioName"],
        "languageHints": data["languageHints"],
        **data["transcript"],
    }


class MacBridge:
    def __init__(self, helpers, http):
        self.helpers = Path(helpers)
        self.http = http
        self.root = library_path()
        self.client_id = str(uuid4())
        self.connected = False
        self.lease_task = None
        self.setup_lock = asyncio.Lock()
        self.capture_lock = asyncio.Lock()
        self.job = None
        self.recorder = None
        self.stage = None
        self.capture = None
        self.settings = {}
        self.stage_lock = None
        self.dialog_lock = asyncio.Lock()
        self.capture_session = None

    async def api(self, method, route, **options):
        response = await self.http.request(method, "/api/" + route, **options)
        response.raise_for_status()
        return response.json() if response.content else None

    async def dialog(self, command, *args):
        async with self.dialog_lock:
            result = await run_helper(self.helpers / "jard-dialog", command, *args)
            return json.loads(result)

    async def lease(self):
        while True:
            try:
                await self.api("POST", "clients/" + self.client_id)
            except httpx.ConnectError:
                # Another client may have explicitly quit the shared engine.
                # Finalize on disk so the recording is recoverable at next launch.
                await self.stop()
            except httpx.HTTPError:
                pass
            await asyncio.sleep(18)

    async def prepare(self):
        async with self.setup_lock:
            return await self.connect_if_ready()

    async def connect_if_ready(self):
        # Never pass a token or expose native controls to an unrelated local service.
        existing = await asyncio.to_thread(running_engine)
        if not existing:
            status = json.loads(
                await run_helper(self.helpers / "jard-engine", "model-status")
            )
            if not status["ready"]:
                return status
            await run_helper(self.helpers / "jard-engine", "start")
            if not await asyncio.to_thread(running_engine):
                raise RuntimeError("The local engine stopped during startup.")
        self.http.headers["X-Jard-Token"] = (
            (self.root / ".native-token").read_text().strip()
        )
        await self.api("POST", "clients/" + self.client_id)
        self.connected = True
        if self.lease_task is None:
            self.lease_task = asyncio.create_task(self.lease())
        send({"navigate": "app"})
        return {"ready": True}

    async def setup(self, source):
        async with self.setup_lock:
            args = []
            if source == "folder":
                path = (await self.dialog("open-models")).get("path")
                if not path:
                    return {"ready": False}
                args = ["--from", path]
            elif source != "download":
                raise ValueError("Select a model folder or download the models.")
            p = await asyncio.create_subprocess_exec(
                str(self.helpers / "jard-engine"),
                "models",
                *args,
                stdout=asyncio.subprocess.PIPE,
            )
            error = ""
            try:
                async for line in p.stdout:
                    event = json.loads(line)
                    if event.get("event") == "error":
                        error = event["message"]
                    send({"event": "setup", "detail": event})
                if await p.wait():
                    raise ValueError(error or "Model setup failed. Try again.")
            finally:
                if p.returncode is None:
                    p.terminate()
                    await p.wait()
            return await self.connect_if_ready()

    async def devices(self):
        devices = json.loads(await run_helper(self.helpers / "jard-capture", "devices"))
        drafts = await self.api("GET", "native/staging")
        return {
            "microphone": devices["microphone"],
            "capture": self.capture,
            "drafts": [
                {"id": d["id"], "seconds": max(0, (d["bytes"] - 44) / 96000)}
                for d in drafts
                if not self.stage or d["id"] != self.stage["id"]
            ],
        }

    @staticmethod
    def options(args):
        hints = args.get("languages", [])
        if not isinstance(hints, list) or any(
            not isinstance(code, str) for code in hints
        ):
            raise ValueError("Invalid language hints.")
        return {
            "language": hints,
            "diarization": "true" if args.get("diarization") is True else "false",
            "live": "true" if args.get("live") is True else "false",
        }

    async def submit(self, stage_id, options, *, name, mime, recording, recover=False):
        return await self.api(
            "POST",
            f"native/staging/{stage_id}/submit",
            params={
                **options,
                "name": name,
                "mime": mime,
                "recording": str(recording).lower(),
                "recover": str(recover).lower(),
            },
        )

    async def open_audio(self, args):
        if self.stage:
            raise ValueError("Stop the recording before opening another file.")
        selection = (await self.dialog("open-audio")).get("path")
        if not selection:
            return None
        stage = await self.api("POST", "native/staging")
        try:
            await asyncio.to_thread(shutil.copyfile, selection, stage["path"])
            return await self.submit(
                stage["id"],
                self.options(args),
                name=Path(selection).name,
                mime=mimetypes.guess_type(selection)[0] or "application/octet-stream",
                recording=False,
            )
        except BaseException:
            with contextlib.suppress(httpx.HTTPError):
                await self.api("DELETE", "native/staging/" + stage["id"])
            raise

    async def record(self, args):
        # Job ownership is established before the permission prompt returns. Repeated
        # clicks and page reloads consequently cannot create a second capture.
        async with self.capture_lock:
            if self.job and not self.job.done():
                raise ValueError("A recording is already active.")
            mode = args.get("mode", "both")
            if mode not in ("both", "mic", "system"):
                raise ValueError("Unknown recording source.")
            self.settings = self.options(args)
            if (await self.api("GET", "status"))["status"] != "ready":
                raise ValueError("Wait for the speech model to finish loading.")
            stage = await self.api(
                "POST", "native/staging", params={"recording": "true"}
            )
            try:
                self.stage_lock = (
                    Path(stage["path"]).with_suffix(".recording").open("rb")
                )
                fcntl.flock(self.stage_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.recorder = await asyncio.create_subprocess_exec(
                    str(self.helpers / "jard-capture"),
                    "record",
                    "--mode",
                    mode,
                    "--output",
                    stage["path"],
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                )
            except BaseException:
                if self.stage_lock:
                    self.stage_lock.close()
                    self.stage_lock = None
                await self.api("DELETE", "native/staging/" + stage["id"])
                raise
            self.stage = stage
            self.capture_session = {
                "mode": mode,
                "live": bool(args.get("live")),
                "seconds": 0,
                "text": "",
                "segments": [],
            }
            self.job = asyncio.create_task(self.record_events())
        return {"starting": True}

    async def live_preview(self):
        block = 4 * 48000
        start = 0
        parts = []
        warned = False
        while self.stage:
            await asyncio.sleep(0.35)
            if not self.capture:
                continue
            available = max(0, (Path(self.stage["path"]).stat().st_size - 44) // 2)
            if not self.capture["live"]:
                start = available
                continue
            if available - start < block:
                continue
            if available - start > block * 3:
                start = available - block
            try:
                value = await self.api(
                    "POST",
                    f"native/staging/{self.stage['id']}/preview",
                    params={**self.settings, "start": start, "frames": block},
                )
                if self.capture and self.capture["live"] and value.get("text"):
                    # Keep the provisional UI bounded. The final pass always uses the full take.
                    parts = (parts + [value])[-160:]
                    self.capture["text"] = " ".join(part["text"] for part in parts)
                    self.capture["segments"] = [
                        segment
                        for part in parts
                        for segment in part.get("segments", [])
                    ]
                    emit(
                        "preview",
                        text=self.capture["text"],
                        segments=self.capture["segments"],
                    )
                warned = False
            except httpx.HTTPError:
                if not warned:
                    emit(
                        "warning",
                        message="Live preview is delayed. The recording is still being saved.",
                    )
                warned = True
            start += block

    async def record_events(self):
        child, stage = self.recorder, self.stage
        path = Path(stage["path"])
        preview = asyncio.create_task(self.live_preview())
        reason = ""
        permission = {}
        try:
            async for raw in child.stdout:
                event = json.loads(raw)
                kind = event.get("event")
                if kind == "started":
                    self.capture = self.capture_session
                    emit(
                        "started",
                        mode=self.capture["mode"],
                        microphone=event["microphone"],
                    )
                elif kind == "levels" and self.capture:
                    self.capture["seconds"] = event["seconds"]
                    emit(
                        "levels",
                        seconds=event["seconds"],
                        mic=event["mic"],
                        system=event["system"],
                        microphone=event["microphone"],
                    )
                elif kind == "sourceChanged":
                    emit(kind, microphone=event["microphone"], message=event["message"])
                elif kind == "error":
                    reason = event["message"]
                    if event.get("permission") in ("mic", "system"):
                        permission = {"permission": event["permission"]}
                elif kind == "finished":
                    reason = event.get("reason", "Stopped")
            status = await child.wait()
            if status and not reason:
                reason = "The recorder exited unexpectedly."
        except (OSError, ValueError, KeyError) as e:
            reason = explain(e)
            if child.returncode is None:
                child.terminate()
                await child.wait()
        finally:
            preview.cancel()
            with contextlib.suppress(asyncio.CancelledError, OSError):
                await preview
            self.capture = None
            self.recorder = None
        try:
            if path.is_file() and path.stat().st_size > 44:
                emit("finished", reason=reason or "Stopped")
                data = await self.submit(
                    stage["id"],
                    self.settings,
                    name=datetime.datetime.now().strftime(
                        "Recording %Y-%m-%d %H.%M.%S"
                    ),
                    mime="audio/wav",
                    recording=True,
                    recover=True,
                )
                emit("accepted", id=data["id"])
            else:
                await self.api("DELETE", "native/staging/" + stage["id"])
                emit(
                    "error",
                    message=reason
                    or "No audio was captured. Check the selected source and its macOS permission.",
                    **permission,
                )
        except (httpx.HTTPError, OSError) as e:
            emit(
                "error",
                message="The recording was preserved for recovery. " + explain(e),
            )
        finally:
            self.stage_lock.close()
            self.stage_lock = None
            self.stage = self.capture_session = None

    async def stop(self):
        child = self.recorder
        if child and child.returncode is None:
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                child.stdin.write(b"stop\n")
                await child.stdin.drain()

    async def export(self, args):
        key, kind = str(UUID(args["id"])), args["kind"]
        if kind not in ("audio", "json"):
            raise ValueError("Unsupported export format.")
        session = await self.api("GET", "sessions/" + key)
        name = (
            session["audioName"]
            if kind == "audio"
            else Path(session["audioName"]).stem + ".json"
        )
        choice = (await self.dialog("save", name)).get("path")
        if not choice:
            return None
        dest = Path(choice)
        if dest.resolve().is_relative_to(self.root.resolve()):
            raise ValueError(
                "Choose an export destination outside the session library."
            )

        def write():
            with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as temp:
                partial = Path(temp.name)
            try:
                if kind == "audio":
                    shutil.copyfile(self.root / session["audioPath"], partial)
                else:
                    partial.write_text(
                        json.dumps(
                            transcript_export(session), indent=2, ensure_ascii=False
                        )
                        + "\n"
                    )
                partial.replace(dest)
            finally:
                partial.unlink(missing_ok=True)

        await asyncio.to_thread(write)
        return {"saved": True}

    async def dispatch(self, command, args):
        if command == "jard.prepare":
            return await self.prepare()
        if command == "jard.setup":
            return await self.setup(args.get("source"))
        if not self.connected:
            raise ValueError("Open the local workspace first.")
        if command == "jard.devices":
            return await self.devices()
        if command == "jard.record":
            return await self.record(args)
        if command == "jard.stop":
            await self.stop()
            if self.job:
                await asyncio.shield(self.job)
            return None
        if command == "jard.live":
            enabled = args.get("enabled") is True
            if self.capture_session:
                self.capture_session["live"] = enabled
            self.settings["live"] = str(enabled).lower()
            return None
        if command == "jard.openAudio":
            return await self.open_audio(args)
        if command == "jard.export":
            return await self.export(args)
        if command == "jard.open":
            if not await asyncio.to_thread(running_engine):
                raise ValueError("Local engine is unavailable.")
            await run_helper("/usr/bin/open", URL)
            return None
        if command in ("jard.discard", "jard.recover"):
            key = str(UUID(args["id"]))
            if self.stage and key == self.stage["id"]:
                raise ValueError("Stop the recording first.")
            drafts = await self.api("GET", "native/staging")
            if not any(d["id"] == key for d in drafts):
                raise ValueError("Recording draft is unavailable.")
            if command == "jard.discard":
                return await self.api("DELETE", "native/staging/" + key)
            return await self.submit(
                key,
                self.options(args),
                name="Recovered recording",
                mime="audio/wav",
                recording=True,
                recover=True,
            )
        raise ValueError("Unsupported native action.")

    async def reply(self, message):
        try:
            args = message.get("payload", {})
            if not isinstance(args, dict):
                raise TypeError("Expected native command options.")
            result = await self.dispatch(message["command"], args)
            response = {"id": message["id"], "ok": True, "result": result}
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
            httpx.HTTPError,
        ) as e:
            response = {
                "id": message["id"],
                "ok": False,
                "error": {"message": explain(e)},
            }
        send({"response": response})

    async def close(self):
        await self.stop()
        if self.job:
            try:
                await asyncio.wait_for(asyncio.shield(self.job), 15)
            except TimeoutError:
                if self.recorder and self.recorder.returncode is None:
                    self.recorder.kill()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(self.job), 15)
        if self.lease_task:
            self.lease_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.lease_task
        if self.connected:
            with contextlib.suppress(httpx.HTTPError):
                await self.api("DELETE", "clients/" + self.client_id)


async def main():
    helpers = Path(os.environ["JARD_UI_EXECUTABLE"]).resolve().parent
    async with httpx.AsyncClient(
        base_url=URL, trust_env=False, timeout=httpx.Timeout(120, connect=3)
    ) as http:
        bridge = MacBridge(helpers, http)
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        transport, _ = await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer
        )
        loop.add_signal_handler(signal.SIGTERM, transport.close)
        tasks = set()
        try:
            while raw := await reader.readline():
                try:
                    message = json.loads(raw)
                    if not isinstance(message.get("id"), str) or not isinstance(
                        message.get("command"), str
                    ):
                        continue
                except (ValueError, AttributeError):
                    continue
                task = asyncio.create_task(bridge.reply(message))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await bridge.close()
            transport.close()


if __name__ == "__main__":
    asyncio.run(main())
