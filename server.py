import argparse
import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
import webbrowser
from collections import deque
from contextlib import aclosing, asynccontextmanager
from pathlib import Path
from threading import Thread
from urllib.error import URLError
from urllib.request import urlopen
from uuid import UUID

from session_store import MissingSession, open_library

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from audio_file import DURATION_ERROR, FILE_ERROR, MAX_AUDIO_SECONDS, audio_blocks, audio_duration, make_playable
from insights import Insights
from writing_api import router as writing_routes
from rumble_model import DIARIZATION_ID, DIARIZATION_REVISION, REVISION, LANGUAGES, MAX_SAMPLES, MODEL_ID, SAMPLE_RATE, RumbleModel, prepare_model

HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}"
API_VERSION = 2
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
            logger.exception("Speaker labeling could not start. Run Setup Jard Soundfolio Local to install its models.")
        try:
            await run_in_threadpool(app.state.model.enable_alignment)
        except Exception:
            logger.exception("Word timing could not start. Run Setup Jard Soundfolio Local to install alignment weights.")
        app.state.status = "ready"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.status = "loading"
    app.state.inference = asyncio.Lock()
    app.state.diarizer = None
    app.state.live_speakers = {}
    root = os.environ.get("RUMBLE_DATA_DIR") or Path.home() / "Library/Application Support/Rumble"
    app.state.jobs = set()
    with open_library(root) as library:
        app.state.library = library
        app.state.insights = Insights(library)
        async def load_speech():
            async with app.state.insights.model.audio_priority():
                await load_model(app)

        loading = asyncio.create_task(load_speech())
        try:
            yield
        finally:
            for job in list(app.state.jobs):
                job.cancel()
            await asyncio.gather(*app.state.jobs, return_exceptions=True)
            await app.state.insights.close()
            await loading


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(writing_routes)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[HOST])


@app.get("/api/status")
async def status(request: Request):
    return {
        "status": request.app.state.status, "model": MODEL_ID, "languages": LANGUAGE_OPTIONS, "apiVersion": API_VERSION,
        "diarization": request.app.state.diarizer is not None,
    }


@app.post("/api/quit")
async def quit_app(request: Request):
    if request.headers.get("origin") != URL or request.headers.get("x-jard-action") != "quit":
        raise HTTPException(403, "Quit from the local Jard Soundfolio Local app.")
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
        raise HTTPException(403, "Open Jard Soundfolio Local from its local launcher.")
    try:
        request.app.state.library.delete(str(key))
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return Response(status_code=204)


def check_audio_request(request: Request):
    if request.headers.get("origin", URL) != URL:
        raise HTTPException(403, "Open Jard Soundfolio Local from its local launcher.")
    if request.headers.get("content-type") != "application/octet-stream":
        raise HTTPException(415, "Expected audio bytes.")


def transcription_languages(request: Request) -> tuple[str, ...]:
    check_audio_request(request)
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
):
    languages = transcription_languages(request)
    if session:
        request.app.state.live_speakers.pop(session, None)
    library = request.app.state.library
    folder = tempfile.TemporaryDirectory(prefix=".upload-", dir=library.root)
    source = Path(folder.name) / "audio"
    try:
        await save_upload(request, source)
        duration = await audio_duration(source)
        # A browser recording can end slightly after the microphone sample counter.
        limit = MAX_AUDIO_SECONDS + int(recording)
        if duration is not None and duration > limit:
            raise ValueError(DURATION_ERROR)
        options = dict(recording=recording, live=live, diarization=diarization and request.app.state.diarizer is not None,
                       languageHints=list(languages), model=MODEL_ID, modelRevision=REVISION,
                       speakerModel=DIARIZATION_ID if diarization and request.app.state.diarizer else None,
                       speakerModelRevision=DIARIZATION_REVISION if diarization and request.app.state.diarizer else None)
        key, source = await run_in_threadpool(library.create, source, name, mime, options, duration)
    except ValueError as error:
        folder.cleanup()
        raise HTTPException(400, str(error)) from error
    except FileNotFoundError as error:
        folder.cleanup()
        raise HTTPException(503, "Install FFmpeg to open or finish audio recordings.") from error
    except OSError as error:
        folder.cleanup()
        logger.exception("Could not prepare audio.")
        raise HTTPException(500, "Could not prepare audio. Check available disk space.") from error
    except BaseException:
        folder.cleanup()
        raise

    folder.cleanup()
    queue = asyncio.Queue()
    connected = True

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
            library.finish(key, "Processing stopped when Jard Soundfolio Local closed.")
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

    async def stream():
        nonlocal connected
        try:
            while (line := await queue.get()) is not None:
                yield line
        finally:
            connected = False

    return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"X-Session-ID": key})


def running_server_version() -> int | None:
    try:
        with urlopen(f"{URL}/api/status", timeout=0.3) as response:
            info = json.load(response)
            return info.get("apiVersion", 1) if info.get("model") == MODEL_ID else None
    except (URLError, OSError, ValueError, AttributeError):
        return None


def open_browser():
    for _ in range(50):
        if running_server_version() == API_VERSION:
            webbrowser.open(URL)
            return
        time.sleep(0.1)


def start_in_background(*, open_page: bool):
    directory = Path.home() / "Library/Logs/Jard Soundfolio Local"
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / "server.log"
    with log.open("a") as output:
        process = subprocess.Popen(
            [sys.executable, "server.py", "--no-open"], cwd=Path(__file__).resolve().parent,
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
        )
    while running_server_version() != API_VERSION:
        if process.poll() is not None:
            raise SystemExit(f"Jard Soundfolio Local could not start. See {log}")
        time.sleep(0.15)
    if open_page:
        webbrowser.open(URL)
    print("Jard Soundfolio Local is running. Double-click Quit Jard Soundfolio Local to stop it.")


def main():
    parser = argparse.ArgumentParser(description="Run Jard Soundfolio Local locally.")
    parser.add_argument("--setup-model", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--background", action="store_true", help="Start a detached server and open the browser.")
    args = parser.parse_args()
    if args.setup_model:
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        prepare_model()
        return
    if not (DIST / "index.html").is_file():
        parser.error("Run Setup Jard Soundfolio Local to build the browser interface.")

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"

    running = running_server_version()
    if running is not None:
        if running != API_VERSION:
            parser.exit(1, "An older Jard Soundfolio Local server is running. Run Quit Jard Soundfolio Local, then open Start Jard Soundfolio Local to use this version.\n")
        if not args.no_open:
            webbrowser.open(URL)
        return

    if args.background:
        start_in_background(open_page=not args.no_open)
        return

    import uvicorn

    app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")
    if not args.no_open:
        Thread(target=open_browser, daemon=True).start()
    uvicorn.run(app, host=HOST, port=PORT, access_log=False, ws="none", timeout_graceful_shutdown=5)


if __name__ == "__main__":
    main()
