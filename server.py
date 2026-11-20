import argparse
import asyncio
import json
import hmac
import socket
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
import webbrowser
from collections import deque
from contextlib import aclosing, asynccontextmanager, suppress
from pathlib import Path
from threading import Thread
from uuid import UUID, uuid4

from session_store import MissingSession, open_library
from native_recordings import list_drafts, read_wav_header, recording_marker, recover_wav

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from audio_file import DURATION_ERROR, FILE_ERROR, MAX_AUDIO_SECONDS, audio_blocks, audio_duration, make_playable
from insights import Insights
from writing_api import router as writing_routes
from rumble_model import DIARIZATION_ID, DIARIZATION_REVISION, REVISION, LANGUAGES, MAX_SAMPLES, MODEL_ID, SAMPLE_RATE, RumbleModel, prepare_model

from engine_lifecycle import (
    HOST, PORT, URL, API_VERSION, ClientLeases, engine_proof, install_token, library_id, library_path,
    running_engine, startup_lock,
)
DIST = Path(__file__).resolve().parent / "dist"
MAX_BYTES = MAX_SAMPLES * 4
LANGUAGE_OPTIONS = [
    {"code": code, "name": name}
    for code, name in sorted(LANGUAGES.items(), key=lambda language: language[1])
]
logger = logging.getLogger("uvicorn.error")


async def load_model(app: FastAPI):
    try:
        app.state.model = await asyncio.to_thread(RumbleModel)
    except Exception:
        logger.exception("Could not load Rumble.")
        app.state.status = "error"
    else:
        try:
            from diarization import Diarizer

            app.state.diarizer = await run_in_threadpool(Diarizer)
        except Exception:
            logger.exception("Speaker labeling could not start. Run Setup Jaro to install its models.")
        try:
            await run_in_threadpool(app.state.model.enable_alignment)
        except Exception:
            logger.exception("Word timing could not start. Run Setup Jaro to install alignment weights.")
        app.state.status = "ready"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.status = "loading"
    app.state.inference = asyncio.Lock()
    app.state.diarizer = None
    app.state.live_speakers = {}
    root = library_path()
    app.state.jobs = set()
    with open_library(root) as library:
        app.state.library = library
        app.state.native_token = install_token(root)
        app.state.staging = root / "staging"
        app.state.staging.mkdir(mode=0o700, exist_ok=True)
        app.state.submitting = set()
        app.state.leases = ClientLeases()
        app.state.insights = Insights(library)
        async def load_speech():
            async with app.state.insights.model.audio_priority():
                await load_model(app)

        loading = asyncio.create_task(load_speech())
        monitor = asyncio.create_task(watch_idle(app))
        try:
            yield
        finally:
            monitor.cancel()
            with suppress(asyncio.CancelledError):
                await monitor
            for job in list(app.state.jobs):
                job.cancel()
            await asyncio.gather(*app.state.jobs, return_exceptions=True)
            await app.state.insights.close()
            await loading


async def watch_idle(app):
    while True:
        await asyncio.sleep(5)
        state = app.state
        busy = (state.jobs or state.submitting or state.insights.active_job or state.insights.chat_count
                or state.insights.model.audio_users or state.insights.wake.is_set())
        if state.leases.idle(busy) and getattr(state, "request_shutdown", None):
            state.request_shutdown()
            return


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(writing_routes)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[HOST])


@app.get("/api/status")
async def status(request: Request, challenge: str = Query("", max_length=64)):
    proof = engine_proof(request.app.state.native_token, challenge) if challenge.isascii() and challenge else None
    return {
        "libraryId": library_id(request.app.state.library.root), "proof": proof,
        "status": request.app.state.status, "model": MODEL_ID, "languages": LANGUAGE_OPTIONS, "apiVersion": API_VERSION,
        "diarization": request.app.state.diarizer is not None,
    }


def check_client(request):
    if request.headers.get("origin", URL) != URL:
        raise HTTPException(403, "Open Jaro on this Mac.")


@app.post("/api/clients/{key}", status_code=204)
async def renew_client(key: UUID, request: Request):
    check_client(request)
    request.app.state.leases.renew(str(key))
    return Response(status_code=204)


@app.delete("/api/clients/{key}", status_code=204)
async def detach_client(key: UUID, request: Request):
    check_client(request)
    request.app.state.leases.detach(str(key))
    return Response(status_code=204)


@app.get("/api/clients/{key}/stream")
async def connect_browser(key: UUID, request: Request):
    check_client(request)
    leases = request.app.state.leases

    async def stream():
        # A live connection keeps suspended background tabs attached even when
        # the browser throttles timers. A closed tab releases its connection.
        connection = object()
        leases.streams.add(connection)
        try:
            while True:
                leases.renew(str(key))
                yield ": Jaro connected\n\n"
                await asyncio.sleep(20)
        finally:
            leases.streams.discard(connection)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@app.post("/api/quit")
async def quit_app(request: Request):
    if request.headers.get("origin") != URL or request.headers.get("x-jard-action") != "quit":
        raise HTTPException(403, "Quit from the local Jaro app.")
    return Response(status_code=202, background=BackgroundTask(os.kill, os.getpid(), signal.SIGTERM))


@app.exception_handler(MissingSession)
async def missing_session(request: Request, error: MissingSession):
    return JSONResponse({"detail": "Session no longer exists."}, status_code=404)


@app.get("/api/sessions")
def list_sessions(request: Request, q: str = ""):
    return JSONResponse(request.app.state.library.list(q.strip()), headers={"Cache-Control": "no-store"})


@app.get("/api/sessions/{key}")
async def get_session(request: Request, key: UUID):
    session = await run_in_threadpool(request.app.state.library.get, str(key))
    if session["overview"] and session["overview"]["status"] == "queued":
        request.app.state.insights.wake.set()
    return JSONResponse(session, headers={"Cache-Control": "no-store"})


@app.get("/api/sessions/{key}/audio")
def session_audio(request: Request, key: UUID):
    path, mime = request.app.state.library.media(str(key))
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@app.delete("/api/sessions/{key}")
def delete_session(request: Request, key: UUID):
    if request.headers.get("origin", URL) != URL:
        raise HTTPException(403, "Open Jaro from its local launcher.")
    try:
        request.app.state.library.delete(str(key))
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return Response(status_code=204)


def check_audio_request(request: Request):
    if request.headers.get("origin", URL) != URL:
        raise HTTPException(403, "Open Jaro from its local launcher.")
    if request.headers.get("content-type") != "application/octet-stream":
        raise HTTPException(415, "Expected audio bytes.")


def transcription_languages(request: Request) -> tuple[str, ...]:
    if request.app.state.status != "ready":
        raise HTTPException(503, "The local model is unavailable.")

    languages = tuple(dict.fromkeys(request.query_params.getlist("language")))
    if any(code not in LANGUAGES for code in languages):
        raise HTTPException(400, "Choose a language from the list.")
    return languages


async def run_inference(request: Request, function, *args, **kwargs):
    async with request.app.state.inference:
        return await run_in_threadpool(function, *args, **kwargs)


async def decode_pcm(request: Request, pcm: bytes, languages, speakers=None, offset=0, live=False):
    def inference():
        if live and speakers:
            speakers.push(pcm, offset)
        model = request.app.state.model
        def recognize(cache_implementation=None):
            return model.transcribe(
                pcm, languages, timed=not live or speakers is not None, cache_implementation=cache_implementation,
            )

        try:
            # Fixed-size decoder storage avoids growing Metal tensors on every final-pass token.
            recognized = recognize(None if live else "static")
        except RuntimeError as error:
            if live or "out of memory" not in str(error).lower():
                raise
            recognized = None
        if recognized is None:
            # Retry outside the exception handler so its traceback can release tensors.
            model.torch.mps.empty_cache()
            logger.warning("Retrying ASR with a dynamic cache after a memory error at %.2f seconds.", offset / SAMPLE_RATE)
            recognized = recognize()
        text, language, words = recognized
        duration = len(pcm) / (4 * SAMPLE_RATE)
        segments, word_times = speakers.label(
            text, words, offset, duration, include_word_times=not live,
        ) if speakers else ([], [])
        if not speakers and not live:
            from speaker_alignment import word_records

            word_times = word_records(text, words, offset / SAMPLE_RATE, duration)
        result = {"text": text, "language": language, "segments": segments, "words": word_times}
        if speakers and not live:
            result["activity"] = speakers.intervals(offset, duration)
        return result

    return await run_inference(request, inference)


def live_speakers(request, session_id):
    state = request.app.state
    if not session_id or state.diarizer is None:
        return None
    now = time.monotonic()
    for key, (used, _) in list(state.live_speakers.items()):
        if now - used > 120:
            del state.live_speakers[key]
    existing = state.live_speakers.get(session_id)
    speakers = existing[1] if existing else state.diarizer.session(live=True)
    state.live_speakers[session_id] = (now, speakers)
    return speakers


@app.post("/api/transcribe")
async def transcribe(request: Request, session: UUID | None = None, offset: int = 0):
    check_audio_request(request)
    languages = transcription_languages(request)
    pcm = bytearray()
    async for packet in request.stream():
        if len(pcm) + len(packet) > MAX_BYTES:
            raise HTTPException(413, "Send up to 25 seconds of audio per request.")
        pcm.extend(packet)
    if not pcm or len(pcm) % 4:
        raise HTTPException(400, "The audio could not be read.")
    if offset < 0:
        raise HTTPException(400, "The live audio offset could not be read.")

    try:
        async with request.app.state.insights.model.audio_priority():
            return await decode_pcm(request, bytes(pcm), languages, live_speakers(request, session), offset, live=True)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except Exception as error:
        logger.exception("Rumble inference failed.")
        raise HTTPException(500, "Transcription failed. Try the audio again.") from error


async def save_upload(request: Request, source: Path):
    with source.open("wb") as output:
        async for packet in request.stream():
            await asyncio.to_thread(output.write, packet)


@app.post("/api/playback")
async def playback(request: Request, saved: UUID | None = None):
    check_audio_request(request)
    folder = tempfile.TemporaryDirectory(prefix="soundfolio-player-")
    source = Path(folder.name) / "source"
    target = source.with_suffix(".m4a")
    try:
        if saved:
            source, _ = request.app.state.library.media(str(saved))
        else:
            await save_upload(request, source)
        await make_playable(source, target)
        return FileResponse(target, media_type="audio/mp4", background=BackgroundTask(folder.cleanup))
    except ValueError as error:
        folder.cleanup()
        raise HTTPException(400, str(error)) from error
    except BaseException:
        folder.cleanup()
        raise


@app.post("/api/transcribe-file")
async def transcribe_file(
    request: Request, recording: bool = False, diarization: bool = False, session: UUID | None = None,
    name: str = "", mime: str = "", live: bool = False, progress_only: bool = False,
    notes_draft: UUID | None = None,
):
    check_audio_request(request)
    languages = transcription_languages(request)
    if session:
        request.app.state.live_speakers.pop(session, None)
    with tempfile.TemporaryDirectory(prefix=".upload-", dir=request.app.state.library.root) as folder:
        source = Path(folder) / "audio"
        await save_upload(request, source)
        return await accept_audio(request, source, name, mime, languages, recording, diarization, live, progress_only,
                                  notes_draft=str(notes_draft) if notes_draft else None)


async def accept_audio(request, source, name, mime, languages, recording, diarization, live, progress_only, stage_id=None, *, notes_draft=None):
    library = request.app.state.library
    try:
        duration = await audio_duration(source)
        limit = MAX_AUDIO_SECONDS + int(recording)
        if duration is not None and duration > limit:
            raise ValueError(DURATION_ERROR)
        options = dict(recording=recording, live=live, diarization=diarization and request.app.state.diarizer is not None,
                       languageHints=list(languages), model=MODEL_ID, modelRevision=REVISION,
                       speakerModel=DIARIZATION_ID if diarization and request.app.state.diarizer else None,
                       speakerModelRevision=DIARIZATION_REVISION if diarization and request.app.state.diarizer else None)
        key, source = await run_in_threadpool(library.create, source, name, mime, options, duration, key=stage_id, notes_draft=notes_draft)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(503, "Could not open the audio or find FFmpeg.") from error
    except OSError as error:
        logger.exception("Could not prepare audio.")
        raise HTTPException(500, "Could not prepare audio. Check available disk space.") from error

    queue = asyncio.Queue()
    connected = stage_id is None

    def emit(**payload):
        if connected:
            queue.put_nowait(json.dumps(payload, ensure_ascii=False) + "\n")

    async def process():
        samples = 0
        try:
            diarizer = request.app.state.diarizer
            speakers = diarizer.session() if diarization and diarizer else None
            pending = deque()

            async def transcript(pcm, offset):
                result = await decode_pcm(request, pcm, languages, speakers, offset)
                seconds = (offset + len(pcm) // 4) / SAMPLE_RATE
                await run_in_threadpool(library.append, key, result, seconds)
                emit(**({} if progress_only else result), seconds=seconds, duration=duration)

            emit(seconds=0, duration=duration)
            async with aclosing(audio_blocks(source, multilingual=len(languages) > 1)) as blocks:
                async for pcm in blocks:
                    offset = samples
                    samples += len(pcm) // 4
                    if samples > limit * SAMPLE_RATE:
                        raise ValueError(DURATION_ERROR)
                    if speakers:
                        await run_inference(request, speakers.push, pcm)
                        pending.append((pcm, offset))
                        while pending and speakers.covers(pending[0][1] + len(pending[0][0]) // 4):
                            await transcript(*pending.popleft())
                    else:
                        await transcript(pcm, offset)
            if not samples:
                raise ValueError(FILE_ERROR)
            if speakers and pending:
                await run_inference(request, speakers.push, b"", final=True)
                for block in pending:
                    await transcript(*block)
            await run_in_threadpool(library.finish, key)
            request.app.state.insights.wake.set()
            emit(done=True, duration=samples / SAMPLE_RATE)
        except asyncio.CancelledError:
            library.finish(key, "Processing stopped when Jaro closed.")
            raise
        except Exception as error:
            logger.exception("Could not complete session %s.", key)
            message = str(error) if isinstance(error, ValueError) else "Processing stopped. Open Sessions for the saved audio and completed text."
            try:
                await run_in_threadpool(library.finish, key, message)
            finally:
                emit(error=message)
        finally:
            queue.put_nowait(None)

    async def process_with_priority():
        async with request.app.state.insights.model.audio_priority(release_idle=duration is not None and duration >= 15 * 60):
            await process()

    job = asyncio.create_task(process_with_priority())
    request.app.state.jobs.add(job)
    job.add_done_callback(request.app.state.jobs.discard)

    if stage_id is not None:
        return JSONResponse({"id": key}, status_code=202)

    async def stream():
        nonlocal connected
        try:
            while (line := await queue.get()) is not None:
                yield line
        finally:
            connected = False

    return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"X-Session-ID": key})


def check_native(request):
    check_client(request)
    token = request.headers.get("x-jard-token", "")
    if not hmac.compare_digest(request.app.state.native_token.encode(), token.encode()):
        raise HTTPException(403, "Open Jaro on this Mac to access desktop staging.")


@app.post("/api/native/staging", status_code=201)
async def new_stage(request: Request, recording: bool = False, dictation: bool = False):
    check_native(request)
    key = str(uuid4())
    path = request.app.state.staging / key
    path.touch(mode=0o600, exist_ok=False)
    if dictation:
        path.with_suffix(".dictation").touch(mode=0o600, exist_ok=False)
    elif recording:
        recording_marker(path).touch(mode=0o600, exist_ok=False)
    return {"id": key, "path": str(path)}


@app.get("/api/native/staging")
def recoverable_staging(request: Request):
    check_native(request)
    return JSONResponse(list_drafts(request.app.state.staging), headers={"Cache-Control": "no-store"})


def stage_path(request, key):
    path = request.app.state.staging / str(key)
    if path.is_symlink() or not path.is_file():
        raise HTTPException(404, "The staged file is unavailable.")
    return path


@app.delete("/api/native/staging/{key}", status_code=204)
async def abandon_stage(request: Request, key: UUID, dictation: bool = False):
    check_native(request)
    if key in request.app.state.submitting:
        raise HTTPException(409, "Audio is being accepted.")
    path = stage_path(request, key)
    if dictation and not path.with_suffix(".dictation").is_file():
        raise HTTPException(404, "This dictation is unavailable.")
    path.unlink()
    path.with_suffix(".dictation").unlink(missing_ok=True)
    recording_marker(path).unlink(missing_ok=True)
    return Response(status_code=204)


@app.post("/api/native/staging/{key}/submit")
async def submit_stage(request: Request, key: UUID, recording: bool = False, diarization: bool = False,
                       name: str = "", mime: str = "audio/wav", live: bool = False, recover: bool = False,
                       notes_draft: UUID | None = None):
    check_native(request)
    state = request.app.state
    # The staging ID becomes the session ID, so retrying a lost response cannot duplicate it.
    try:
        await run_in_threadpool(state.library.media, str(key))
    except MissingSession:
        pass
    else:
        return {"id": str(key)}
    if key in state.submitting:
        raise HTTPException(409, "Audio is being accepted.")
    languages = transcription_languages(request)
    source = stage_path(request, key)
    if source.with_suffix(".dictation").is_file():
        raise HTTPException(409, "Dictation belongs in My notes.")
    if recover and not notes_draft and recording_marker(source).is_file():
        with recording_marker(source).open("rb") as marker:
            reference = marker.read(36).decode("ascii", errors="ignore")
        with suppress(ValueError):
            candidate = UUID(reference)
            with state.library.connection() as db:
                if db.execute("SELECT 1 FROM personal_note_drafts WHERE id=?", (str(candidate),)).fetchone():
                    notes_draft = candidate
    state.submitting.add(key)
    try:
        state.live_speakers.pop(key, None)
        if recover:
            if not recording:
                raise HTTPException(400, "Only a recording can be recovered.")
            try:
                await run_in_threadpool(recover_wav, source)
            except ValueError as error:
                raise HTTPException(400, str(error)) from error
        result = await accept_audio(request, source, name, mime, languages, recording, diarization, live, True, str(key),
                                    notes_draft=str(notes_draft) if notes_draft else None)
        recording_marker(source).unlink(missing_ok=True)
        return result
    finally:
        state.submitting.discard(key)


async def dictate_audio(request, source, languages):
    parts = []
    try:
        async with request.app.state.insights.model.audio_priority():
            async with aclosing(audio_blocks(source, multilingual=len(languages) > 1)) as blocks:
                async for pcm in blocks:
                    result = await decode_pcm(request, pcm, languages, live=True)
                    if result["text"].strip():
                        parts.append(result["text"].strip())
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except Exception as error:
        logger.exception("Dictation inference failed.")
        raise HTTPException(500, "Could not transcribe dictation. Try again.") from error
    return {"text": " ".join(parts)}


@app.post("/api/personal-notes/dictation")
async def browser_dictation(request: Request):
    check_audio_request(request)
    languages = transcription_languages(request)
    with tempfile.TemporaryDirectory(prefix=".upload-", dir=request.app.state.library.root) as folder:
        source = Path(folder) / "dictation"
        await save_upload(request, source)
        return await dictate_audio(request, source, languages)


@app.post("/api/native/staging/{key}/dictate")
async def native_dictation(request: Request, key: UUID):
    check_native(request)
    languages = transcription_languages(request)
    path = stage_path(request, key)
    if not path.with_suffix(".dictation").is_file():
        raise HTTPException(404, "This dictation is unavailable.")
    return await dictate_audio(request, path, languages)


@app.post("/api/native/staging/{key}/preview")
async def preview_stage(request: Request, key: UUID, start: int = Query(ge=0, le=MAX_AUDIO_SECONDS * 48000),
                        frames: int = Query(gt=0, le=25 * 48000), diarization: bool = False):
    check_native(request)
    languages = transcription_languages(request)
    path = stage_path(request, key)
    # Our recorder writes PCM16 mono under a fixed 44-byte WAV header. Only use
    # committed samples; the RIFF length gets its final value after Stop.
    def read_pcm():
        with path.open("rb") as f:
            try:
                read_wav_header(f)
            except ValueError as error:
                raise HTTPException(400, str(error)) from error
            f.seek(44 + start * 2)
            return f.read(frames * 2)

    data = await run_in_threadpool(read_pcm)
    if len(data) != frames * 2:
        raise HTTPException(409, "The requested audio has not been written yet.")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-f", "s16le", "-ar", "48000", "-ac", "1", "-i", "pipe:0",
        "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1", stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        pcm, _ = await proc.communicate(data)
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    if proc.returncode or not pcm:
        raise HTTPException(400, "Could not decode live audio.")
    async with request.app.state.insights.model.audio_priority():
        return await decode_pcm(request, pcm, languages, live_speakers(request, key) if diarization else None,
                                start * SAMPLE_RATE // 48000, live=True)


def start_in_background(*, open_page: bool):
    directory = Path(os.environ.get("RUMBLE_LOG_DIR") or Path.home() / "Library/Logs/Jaro")
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / "server.log"
    with startup_lock():
        if not running_engine():
            with log.open("a") as output:
                process = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), "--serve"], cwd=Path(__file__).resolve().parent,
                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
                )
            deadline = time.monotonic() + 60
            while True:
                try:
                    if running_engine():
                        break
                except RuntimeError:
                    # A listener can be bound before the ASGI startup completes.
                    if process.poll() is not None:
                        raise
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(f"Jaro could not start. See {log}")
                time.sleep(0.15)
    if open_page:
        webbrowser.open(URL)


def main():
    parser = argparse.ArgumentParser(description="Run Jaro locally.")
    parser.add_argument("--setup-model", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--ensure", action="store_true", help="Attach to or start the shared engine.")
    parser.add_argument("--background", action="store_true", help="Start a detached server and open the browser.")
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.setup_model:
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        prepare_model()
        return
    if not (DIST / "index.html").is_file():
        parser.error("Run Setup Jaro to build the browser interface.")

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    try:
        if args.ensure or args.background:
            start_in_background(open_page=args.background and not args.no_open and not args.ensure)
            return
        if not args.serve and running_engine():
            if not args.no_open:
                webbrowser.open(URL)
            return
    except RuntimeError as error:
        parser.exit(1, str(error) + "\n")

    import uvicorn

    app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, access_log=False, ws="none",
                                           timeout_graceful_shutdown=5))
    app.state.request_shutdown = lambda: setattr(server, "should_exit", True)
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((HOST, PORT))
        except OSError as error:
            parser.exit(1, f"Cannot start Jaro at {URL}: {error}\n")
        if not args.no_open and not args.serve:
            def open_when_ready():
                while not server.started and not server.should_exit:
                    time.sleep(0.1)
                if server.started:
                    webbrowser.open(URL)
            Thread(target=open_when_ready, daemon=True).start()
        server.run(sockets=[listener])


if __name__ == "__main__":
    main()
