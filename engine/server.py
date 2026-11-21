import argparse
import asyncio
import fcntl
import json
import hmac
import socket
import logging
import os
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import webbrowser
from collections import deque
from contextlib import aclosing, asynccontextmanager, nullcontext, suppress
from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID, uuid4

import meeting_brief
import meeting_exports
from session_store import SHUTDOWN_INTERRUPTION, MissingSession, open_library
from annotation_assets import read_staged_annotation, staged_annotation_paths
from diagnostics import PROCESS_LOG, log_directory, open_log
from native_recordings import capture_active, is_meeting_stage, list_drafts, meeting_title_path, read_meeting_title, read_wav_header, recording_marker, recording_metadata, recover_wav, recover_tracks, source_tracks
from managed_preferences import ManagedFeatureError, meetings_managed, require_annotation, require_meetings
from dictation_output import MAX_REWRITE_DEADLINE, keeps_rewrite_numbers, rewrite_deadline, rewrite_messages, vocabulary_terms
from meeting_rolling import read_rolling, rolling_path
from meeting_retention import readable_source
from meeting_transcript import FRAME as MEETING_FRAME, RATE as MEETING_RATE, read_window, merge_sources
from saved_meetings import export_name, get_meeting, meeting_detail, render_export
from sdk_discovery import SDK_MAX_REQUESTS, SDK_MAX_UPLOAD, SDK_VERSION, publish_sdk, remove_sdk, sdk_proof

from fastapi import APIRouter, Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import ClientDisconnect

from audio_file import (
    DURATION_ERROR, FILE_ERROR, MAX_AUDIO_SECONDS, PAUSE_AWARE_CHUNKING, audio_blocks, audio_duration, dictation_has_signal,
    make_playable,
)
from insights import Insights
from writing_api import router as writing_routes
from rumble_model import DIARIZATION_ID, DIARIZATION_REVISION, REVISION, LANGUAGES, MAX_SAMPLES, MODEL_ID, SAMPLE_RATE, RumbleModel, prepare_model

from engine_lifecycle import (
    HOST, PORT, URL, API_VERSION, ClientLeases, desktop_control_path, engine_proof, install_token, library_id, library_path,
    running_engine, startup_lock,
)
from engine_paths import WORKSPACE_ROOT

DIST = WORKSPACE_ROOT / "dist"
MAX_BYTES = MAX_SAMPLES * 4
LANGUAGE_OPTIONS = [
    {"code": code, "name": name}
    for code, name in sorted(LANGUAGES.items(), key=lambda language: language[1])
]
logger = logging.getLogger("uvicorn.error")


class SpeechInference:
    """Serialize access to Rumble and let an already queued dictation go first."""

    def __init__(self):
        self.condition = asyncio.Condition()
        self.active = False
        self.waiting_dictation = 0

    @asynccontextmanager
    async def turn(self, *, dictation=False):
        async with self.condition:
            self.waiting_dictation += int(dictation)
            try:
                await self.condition.wait_for(
                    lambda: not self.active and (dictation or not self.waiting_dictation)
                )
                self.active = True
            finally:
                self.waiting_dictation -= int(dictation)
                if dictation and not self.active:
                    self.condition.notify_all()
        try:
            yield
        finally:
            async with self.condition:
                self.active = False
                self.condition.notify_all()


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
    app.state.inference = SpeechInference()
    app.state.diarizer = None
    app.state.live_speakers = {}
    app.state.meeting_subscriber = None
    root = library_path()
    app.state.jobs = set()
    with open_library(root) as library:
        app.state.library = library
        app.state.native_token = install_token(root)
        app.state.staging = root / "staging"
        app.state.staging.mkdir(mode=0o700, exist_ok=True)
        app.state.submitting = set()
        app.state.sdk_pending = 0
        app.state.sdk = None
        app.state.draining = False
        app.state.leases = ClientLeases()
        managed_meetings = meetings_managed()
        app.state.insights = Insights(library, meetings_managed=managed_meetings)
        start_job(app.state, publish_meeting_exports(app.state))
        async def load_speech():
            async with app.state.insights.model.audio_priority():
                await load_model(app)

        loading = asyncio.create_task(load_speech())
        monitor = asyncio.create_task(watch_idle(app))
        # Recover drafts from this startup only. New drafts stay under the current recorder's control.
        state = app.state
        state.startup_stranded = {marker.stem for marker in state.staging.glob("*.recording")}
        state.startup_recovery = not managed_meetings and bool(state.startup_stranded or library.has_pending_meetings())
        recovery = asyncio.create_task(resume_meetings(state))
        try:
            if getattr(state, "sdk_port", None):
                state.sdk = publish_sdk(root, state.sdk_port)
            yield
        finally:
            if state.sdk is not None:
                remove_sdk(root, state.sdk)
                state.sdk = None
            monitor.cancel()
            recovery.cancel()
            await asyncio.gather(monitor, recovery, return_exceptions=True)
            jobs = list(app.state.jobs)
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            await app.state.insights.close()
            await loading


async def watch_idle(app):
    next_expiry = 0
    while True:
        await asyncio.sleep(5)
        state = app.state
        if time.monotonic() >= next_expiry and not state.draining:
            next_expiry = time.monotonic() + 3600
            start_job(state, sweep_meeting_retention(state))
        busy = (state.jobs or state.submitting or state.sdk_pending or state.insights.active_job or state.insights.chat_count
                or state.insights.model.audio_users or state.insights.wake.is_set())
        if state.leases.idle(busy) and getattr(state, "request_shutdown", None):
            state.request_shutdown()
            return


async def sweep_meeting_retention(state):
    try:
        await publish_meeting_exports(state)
        failures = await run_in_threadpool(state.library.expire_meetings)
        if failures:
            logger.warning("%d meeting archives are still waiting for the Trash.", len(failures))
        audio_waiting = await run_in_threadpool(state.library.expire_meeting_audio)
        if audio_waiting:
            logger.warning("%d meeting recordings are still waiting for the Trash.", len(audio_waiting))
    except (OSError, sqlite3.Error):
        logger.exception("Could not apply meeting retention.")


async def publish_meeting_exports(state, *, key=None):
    try:
        await meeting_exports.flush_async(state.library, key=key)
    except (OSError, sqlite3.Error):
        logger.exception("Could not process managed meeting exports.")


async def resume_meetings(state):
    def available():
        return (state.status == "ready" and not state.draining and not state.jobs and not state.submitting
                and not state.insights.model.audio_users and not state.inference.waiting_dictation
                and not getattr(state, "is_shutting_down", lambda: False)())

    try:
        while state.startup_recovery:
            await asyncio.sleep(2)
            if state.status == "error":
                return
            if not available():
                continue
            try:
                if await run_in_threadpool(capture_active, state.staging) or not available():
                    continue
                if not await asyncio.shield(start_job(state, recover_pending_meeting(state, state.startup_stranded))):
                    return
            except (OSError, sqlite3.Error, RuntimeError):
                logger.exception("Could not resume pending meeting finalization.")
    finally:
        state.startup_recovery = False


async def recover_pending_meeting(state, stranded):
    # The SQLite claim is synchronous and short so cancellation cannot consume
    # an attempt before the worker has taken responsibility for it.
    pending = state.library.claim_meeting_finalization()
    if pending is None:
        return await recover_stranded_meeting(state, stranded)
    key, options = pending["id"], pending["options"]
    source = state.library.audio / key / pending["filename"]
    tracks = ({name: source.parent / f"{name}.wav" for name in ("mic", "system")}
              if options.get("sourceSpeakerTurns") else None)
    await process_saved_audio(state, key, source, options, pending["duration"], tracks=tracks,
                              checkpoint=pending["next_frame"], recovered=True)
    return True


async def recover_stranded_meeting(state, stranded):
    if not stranded:
        return False
    drafts = await run_in_threadpool(list_drafts, state.staging)
    candidates = [draft for draft in reversed(drafts) if draft["meeting"] and draft["id"] in stranded]
    if not candidates:
        # If a recorder acquired its claim while we scanned, its draft could still become available.
        return await run_in_threadpool(capture_active, state.staging)
    draft = candidates[0]
    key = draft["id"]
    try:
        with recording_marker(Path(draft["path"])).open("rb") as claim:
            fcntl.flock(claim, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Validate the same stage and import it under the recorder's existing UUID.
            await submit_staged_audio(state, UUID(key), (), recording=True, diarization=True,
                                      name="Recovered meeting", mime="audio/wav", live=True, recover=True)
    except BlockingIOError:
        return True
    except FileNotFoundError:
        pass
    except (HTTPException, OSError):
        logger.warning("Could not import interrupted meeting %s. Its files remain available for recovery.", key)
    stranded.discard(key)
    return True


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
sdk_router = APIRouter(prefix="/api/sdk/v1")
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
    state = request.app.state
    if state.draining:
        raise HTTPException(409, "Jard is preparing to restart.")
    state.leases.renew(str(key))
    return Response(status_code=204)


@app.delete("/api/clients/{key}", status_code=204)
async def detach_client(key: UUID, request: Request):
    check_client(request)
    request.app.state.leases.detach(str(key))
    return Response(status_code=204)


@app.get("/api/clients/{key}/stream")
async def connect_browser(key: UUID, request: Request):
    check_client(request)
    state = request.app.state

    async def stream():
        # An empty stream lets EventSource reconnect after a committed restart.
        if state.draining:
            return
        with state.leases.browser_connection():
            while True:
                yield ": Jaro connected\n\n"
                await asyncio.sleep(20)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@app.get("/api/meetings/events")
async def meeting_events(request: Request):
    check_client(request)
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    state = request.app.state
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode(), state.native_token.encode()):
        raise HTTPException(401, "Valid Jard credentials are required.", headers={"WWW-Authenticate": "Bearer"})
    if state.meeting_subscriber is not None:
        raise HTTPException(409, "subscriber-busy")
    events = asyncio.Queue(maxsize=32)
    state.meeting_subscriber = events

    async def stream():
        last = time.monotonic()
        try:
            while not getattr(state, "is_shutting_down", lambda: False)():
                try:
                    identifier = await asyncio.wait_for(events.get(), timeout=1)
                except TimeoutError:
                    if time.monotonic() - last < 15:
                        continue
                    yield ": keep-alive\n\n"
                else:
                    if identifier is None:
                        return
                    yield "data: " + json.dumps({"type": "meeting.finished", "id": identifier}, separators=(",", ":")) + "\n\n"
                last = time.monotonic()
        finally:
            if state.meeting_subscriber is events:
                state.meeting_subscriber = None

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@app.post("/api/quit")
async def quit_app(request: Request):
    if request.headers.get("origin") != URL or request.headers.get("x-jard-action") != "quit":
        raise HTTPException(403, "Quit from the local Jaro app.")
    return Response(status_code=202, background=BackgroundTask(os.kill, os.getpid(), signal.SIGTERM))


@app.exception_handler(ManagedFeatureError)
async def managed_feature(request: Request, error: ManagedFeatureError):
    return JSONResponse({"detail": str(error), "code": "managed"}, status_code=403)


@app.exception_handler(MissingSession)
async def missing_session(request: Request, error: MissingSession):
    return JSONResponse({"detail": "Session no longer exists."}, status_code=404)


@app.get("/api/sessions")
def list_sessions(request: Request, q: str = "", meetings: bool = False):
    state = request.app.state
    headers = {"Cache-Control": "no-store"}
    if state.startup_recovery:
        headers["X-Jard-Meeting-Recovery"] = "pending"
    rows = state.library.list(q.strip(), meetings_only=meetings)
    if meetings_managed():
        for row in rows:
            if row["meeting"]:
                row["meetingsManaged"] = True
    return JSONResponse(rows, headers=headers)


@app.get("/api/sessions/{key}")
async def get_session(request: Request, key: UUID):
    session = await run_in_threadpool(request.app.state.library.get, str(key))
    if not session.get("meetingsManaged") and session["overview"] and session["overview"]["status"] == "queued":
        request.app.state.insights.wake.set()
    return JSONResponse(session, headers={"Cache-Control": "no-store"})


@app.get("/api/sessions/{key}/audio")
def session_audio(request: Request, key: UUID, track: Literal["mic", "system"] | None = None):
    path, mime = request.app.state.library.media(str(key), track=track)
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@app.get("/api/sessions/{key}/annotation/{kind}")
def session_annotation_asset(request: Request, key: UUID, kind: Literal["manifest", "contact-sheet"]):
    path, mime = request.app.state.library.annotation_asset(str(key), kind)
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@app.get("/api/sessions/{key}/export")
def export_meeting(request: Request, key: UUID, kind: Literal["md", "srt", "vtt", "notes"]):
    session = get_meeting(request.app.state.library, str(key), turns=kind != "notes", staged=False)
    name = export_name(session, kind)
    body = render_export(meeting_detail(session), kind)
    media = {"md": "text/markdown", "notes": "text/markdown", "vtt": "text/vtt", "srt": "application/x-subrip"}
    return Response(body, media_type=media[kind], headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                    "Content-Disposition": "attachment; filename*=UTF-8''" + quote(name, safe="")})


@app.get("/api/sessions/{key}/managed-export")
def managed_meeting_export(request: Request, key: UUID):
    check_client(request)
    with request.app.state.library.connection() as db:
        value = meeting_exports.read(db, str(key))
    return JSONResponse(value, headers={"Cache-Control": "no-store"})


@app.put("/api/sessions/{key}/meeting-title")
async def rename_saved_meeting(request: Request, key: UUID, title: Annotated[str, Body(embed=True, min_length=1, max_length=200)]):
    if request.headers.get("origin") != URL:
        raise HTTPException(403, "Rename meetings from the Jard workspace on this Mac.")
    state = request.app.state
    if state.draining:
        raise HTTPException(409, "Jard is preparing to restart.")

    async def rename():
        name = await run_in_threadpool(state.library.rename_meeting, str(key), title)
        await publish_meeting_exports(state, key=str(key))
        with state.library.connection() as db:
            return {"title": name, "export": meeting_exports.read(db, str(key))}

    try:
        return await asyncio.shield(start_job(state, rename()))
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@app.get("/api/native/meeting-exports")
def meeting_export_preference(request: Request):
    check_native(request)
    with request.app.state.library.connection() as db:
        return meeting_exports.preferences(db)


class MeetingExportPreference(BaseModel):
    folder: str | None = Field(max_length=4096)


@app.put("/api/native/meeting-exports")
def set_meeting_export_preference(request: Request, preference: MeetingExportPreference):
    check_native(request)
    state = request.app.state
    if state.draining:
        raise HTTPException(409, "Jard is preparing to restart.")
    try:
        with state.library.connection() as db:
            meeting_exports.set_folder(db, state.library.root, preference.folder)
            return meeting_exports.preferences(db)
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(400, getattr(error, "strerror", None) or str(error)) from error


@app.put("/api/native/meeting-exports/{option}")
def set_meeting_export_option(request: Request, option: Literal["notes", "descriptive"], enabled: Annotated[bool, Body(embed=True, strict=True)]):
    check_native(request)
    state = request.app.state
    if state.draining:
        raise HTTPException(409, "Jard is preparing to restart.")
    with state.library.connection() as db:
        if option == "notes":
            meeting_exports.set_separate_notes(db, enabled)
        else:
            meeting_exports.set_descriptive_names(db, enabled)
        return meeting_exports.preferences(db)


@app.get("/api/native/meeting-exports/{key}")
def locate_meeting_export(request: Request, key: UUID):
    check_native(request)
    with request.app.state.library.connection() as db:
        row = db.execute("SELECT * FROM meeting_exports WHERE id=? AND status='ready'", (str(key),)).fetchone()
    if row is not None:
        try:
            with meeting_exports.checked_folder(row) as directory:
                return {"path": str(meeting_exports.transcript_path(directory, row))}
        except OSError:
            pass
    raise HTTPException(404, "The exported transcript is no longer at its saved location.")


@app.get("/api/native/meetings/{key}/excerpt")
def native_meeting_excerpt(request: Request, key: UUID):
    check_native(request)
    session = get_meeting(request.app.state.library, str(key), staged=False)
    transcript = session["transcript"]
    turns = session["meetingTurns"] or transcript["segments"]
    if not turns and transcript["text"].strip():
        turns = [{"speaker": None, "start": None, "end": None, "text": transcript["text"]}]
    value = {"turns": turns, "labels": transcript.get("speakerNames", {}),
             "available": True, "settled": session["status"] == "complete"}
    result = JSONResponse(value, headers={"Cache-Control": "no-store"})
    if len(result.body) > 2 * 1024 * 1024:
        raise HTTPException(413, "Open Sessions to copy or export this longer meeting transcript.")
    return result


@app.get("/api/native/meetings/{key}/completion")
def native_meeting_completion(request: Request, key: UUID):
    check_native(request)
    with request.app.state.library.connection() as db:
        row = db.execute(f"""SELECT s.name,s.options,s.status,s.duration,s.processed,s.error,
            length(trim(s.search_text)) AS speech,
            EXISTS(SELECT 1 FROM meeting_finalization f WHERE f.session_id=s.id) AS finalizing
            FROM {readable_source(db)} s WHERE s.id=? AND
            coalesce(json_extract(s.options,'$.meeting'),json_extract(s.options,'$.sourceSpeakerTurns'),0)""",
                         (str(key),)).fetchone()
        if not row:
            raise MissingSession(key)
        brief = meeting_brief.read(db, str(key)) or {}
        exported = meeting_exports.read(db, str(key)) or {}
    options = json.loads(row["options"])
    return {"status": "processing" if row["finalizing"] else row["status"],
            "title": options.get("meetingTitle") or (options.get("meetingCalendar") or {}).get("title") or brief.get("title") or
                     options.get("provisionalTitle") or row["name"],
            "duration": row["duration"] or row["processed"], "hasSpeech": bool(row["speech"]),
            "summaryStatus": brief.get("status"), "summary": brief.get("summary"), "error": row["error"],
            "exportStatus": exported.get("status"), "exportError": exported.get("error")}


@app.delete("/api/sessions/{key}/audio")
async def remove_meeting_audio(request: Request, key: UUID):
    check_client(request)
    state = request.app.state
    if state.draining:
        raise HTTPException(409, "Jard is preparing to restart.")
    try:
        removed = await asyncio.shield(start_job(state, run_in_threadpool(state.library.remove_meeting_audio, str(key))))
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except OSError as error:
        raise HTTPException(503, "Could not prepare the meeting audio for the Trash.") from error
    return {"audioRemovedAt": removed}


@app.get("/api/meetings/audio-retention")
def get_audio_retention(request: Request):
    return {"days": request.app.state.library.meeting_audio_days()}


@app.get("/api/meetings/retention")
def get_meeting_retention(request: Request):
    return {"days": request.app.state.library.meeting_retention_days()}


@app.get("/api/meetings/automatic-notes")
def get_automatic_meeting_notes(request: Request):
    check_client(request)
    if meetings_managed():
        return JSONResponse({"enabled": False, "managed": True}, headers={"Cache-Control": "no-store"})
    with request.app.state.library.connection() as db:
        enabled, _ = meeting_brief.automatic_policy(db)
    return JSONResponse({"enabled": enabled}, headers={"Cache-Control": "no-store"})


@app.put("/api/meetings/automatic-notes")
def set_automatic_meeting_notes(request: Request, enabled: Annotated[bool, Body(embed=True, strict=True)]):
    if request.headers.get("origin") != URL:
        raise HTTPException(403, "Change meeting notes from Jaro on this Mac.")
    require_meetings()
    state = request.app.state
    if state.draining:
        raise HTTPException(409, "Jard is preparing to restart.")
    with state.library.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        meeting_brief.set_automatic(db, enabled)
    return {"enabled": enabled}


@app.put("/api/meetings/retention")
async def set_meeting_retention(request: Request, days: int):
    if request.headers.get("origin") != URL:
        raise HTTPException(403, "Change retention from Jaro on this Mac.")
    if days not in (0, 1, 7, 30):
        raise HTTPException(400, "Choose one of the meeting retention periods.")
    state = request.app.state
    if state.draining:
        raise HTTPException(409, "Jard is preparing to restart.")

    async def update():
        await run_in_threadpool(state.library.set_meeting_retention_days, days)
        start_job(state, sweep_meeting_retention(state))

    await asyncio.shield(start_job(state, update()))
    return {"days": days}


@app.put("/api/meetings/audio-retention")
async def set_audio_retention(request: Request, days: int):
    check_client(request)
    if days not in (-1, 0, 1, 7, 30):
        raise HTTPException(400, "Choose one of the meeting audio retention periods.")
    state = request.app.state
    if state.draining:
        raise HTTPException(409, "Jard is preparing to restart.")

    async def update():
        await run_in_threadpool(state.library.set_meeting_audio_days, days or None)
        start_job(state, sweep_meeting_retention(state))

    await asyncio.shield(start_job(state, update()))
    return {"days": days or None}


@app.delete("/api/sessions/{key}")
async def delete_session(request: Request, key: UUID):
    if request.headers.get("origin", URL) != URL:
        raise HTTPException(403, "Open Jaro from its local launcher.")
    state = request.app.state
    if state.draining:
        raise HTTPException(409, "Jaro is preparing to restart.")
    try:
        waiting = await asyncio.shield(start_job(state, run_in_threadpool(state.library.delete, str(key))))
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except (OSError, sqlite3.Error) as error:
        logger.exception("Could not remove a session.")
        raise HTTPException(503, "Could not prepare the session for removal. Try again.") from error
    if waiting:
        return JSONResponse({"trashPending": True}, status_code=202)
    return Response(status_code=204)


def check_audio_request(request: Request):
    if request.headers.get("origin", URL) != URL:
        raise HTTPException(403, "Open Jaro from its local launcher.")
    if request.headers.get("content-type") != "application/octet-stream":
        raise HTTPException(415, "Expected audio bytes.")


def transcription_languages(request: Request, *, require_ready=True) -> tuple[str, ...]:
    if require_ready and request.app.state.status != "ready":
        raise HTTPException(503, "The local model is unavailable.")

    languages = tuple(dict.fromkeys(request.query_params.getlist("language")))
    if any(code not in LANGUAGES for code in languages):
        raise HTTPException(400, "Choose a language from the list.")
    return languages


async def run_inference(state, function, *args, dictation=False, **kwargs):
    async with state.inference.turn(dictation=dictation):
        return await run_in_threadpool(function, *args, **kwargs)


async def decode_pcm(state, pcm: bytes, languages, speakers=None, offset=0, live=False, vocabulary=(), *,
                     timed_output=False, dictation=False, measure_inference=False, screen_nonspeech=False,
                     speech_prefix_samples=None):
    def inference():
        if live and speakers:
            speakers.push(pcm, offset)
        model = state.model
        if (screen_nonspeech and getattr(state, "sdk_embedded", False)
                and not getattr(state, "speech_screen_prepared", False)):
            state.speech_screen_prepared = True
            try:
                model.enable_alignment()
            except Exception:
                logger.warning("The local speech screen is unavailable.")

        def recognize(cache_implementation=None):
            options = {"vocabulary": vocabulary} if vocabulary else {}
            if screen_nonspeech:
                options["screen_nonspeech"] = True
                if speech_prefix_samples is not None:
                    options["speech_prefix_samples"] = speech_prefix_samples
            return model.transcribe(
                pcm, languages, timed=not live or speakers is not None or timed_output, cache_implementation=cache_implementation,
                **options,
            )

        started = time.monotonic_ns() if measure_inference else None
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
        elapsed = time.monotonic_ns() - started if started is not None else None
        text, language, words = recognized
        duration = len(pcm) / (4 * SAMPLE_RATE)
        segments, word_times = speakers.label(
            text, words, offset, duration, include_word_times=not live,
        ) if speakers else ([], [])
        if not speakers and (not live or timed_output):
            from speaker_alignment import word_records

            word_times = word_records(text, words, offset / SAMPLE_RATE, duration)
        result = {"text": text, "language": language, "segments": segments, "words": word_times}
        if elapsed is not None:
            result["inferenceNs"] = elapsed
        if speakers and not live:
            result["activity"] = speakers.intervals(offset, duration)
        return result

    return await run_inference(state, inference, dictation=dictation)


async def resample_capture(data):
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-f", "s16le", "-ar", str(MEETING_RATE), "-ac", "1", "-i", "pipe:0",
        "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1", stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        pcm, _ = await proc.communicate(data)
    finally:
        if proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
    if proc.returncode or not pcm:
        raise ValueError("Could not decode captured audio.")
    return pcm


async def decode_meeting(state, tracks, start, frames, languages, *, live=False):
    window = await run_in_threadpool(read_window, tracks, start, frames)
    if window is None:
        raise HTTPException(409, "The requested audio has not been written yet.")
    clips, echo = window
    start_time, end_time = start / MEETING_RATE, (start + frames) / MEETING_RATE
    if frames < MEETING_FRAME:
        # A residual sub-20 ms buffer cannot carry an utterance. Count it in the timeline.
        result = merge_sources({name: {"words": []} for name in clips}, None, start_time, end_time)
    else:
        recognized = {}
        for name, data in clips.items():
            pcm = await resample_capture(data)
            recognized[name] = await decode_pcm(state, pcm, languages, offset=start * SAMPLE_RATE // MEETING_RATE,
                                                live=live, timed_output=True)
        result = await run_in_threadpool(merge_sources, recognized, echo, start_time, end_time)
    result["meetingCoverage"] = {str(speaker): [[start_time, end_time]] for speaker in (1, 2)}
    return result


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
            return await decode_pcm(request.app.state, bytes(pcm), languages, live_speakers(request, session), offset, live=True)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except Exception as error:
        logger.exception("Rumble inference failed.")
        raise HTTPException(500, "Transcription failed. Try the audio again.") from error


async def save_upload(request: Request, source: Path, *, maximum=None):
    size = 0
    with source.open("wb") as output:
        async for packet in request.stream():
            size += len(packet)
            if maximum is not None and size > maximum:
                raise HTTPException(413, {"code": "audio-too-large", "message": "The audio exceeds the SDK upload limit."})
            await asyncio.to_thread(output.write, packet)


@app.post("/api/playback")
async def playback(request: Request, saved: UUID | None = None, track: Literal["mic", "system"] | None = None):
    check_audio_request(request)
    folder = tempfile.TemporaryDirectory(prefix="soundfolio-player-")
    source = Path(folder.name) / "source"
    target = source.with_suffix(".m4a")
    try:
        if saved:
            source, _ = request.app.state.library.media(str(saved), track=track)
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
        return await accept_audio(request.app.state, source, name, mime, languages, recording, diarization, live, progress_only,
                                  notes_draft=str(notes_draft) if notes_draft else None)


async def accept_audio(state, source, name, mime, languages, recording, diarization, live, progress_only, stage_id=None, *,
                       notes_draft=None, tracks=None, meeting_provider=None, meeting_calendar=None, meeting_started=None,
                       rolling=(), applies_retention=True, annotation=None, provisional_title=None, meeting_title=None,
                       meeting_microphone_warning=None):
    library = state.library
    if state.draining:
        raise HTTPException(409, "Jard is preparing to restart.")
    try:
        duration = await audio_duration(source)
        limit = MAX_AUDIO_SECONDS + int(recording)
        if duration is not None and duration > limit:
            raise ValueError(DURATION_ERROR)
        identify_speakers = diarization and state.diarizer is not None and not tracks
        options = dict(recording=recording, live=live, diarization=bool(tracks) or identify_speakers,
                       languageHints=list(languages), model=MODEL_ID, modelRevision=REVISION,
                       speakerModel=DIARIZATION_ID if identify_speakers else None,
                       speakerModelRevision=DIARIZATION_REVISION if identify_speakers else None)
        if tracks:
            options["sourceSpeakerTurns"] = True
        if meeting_provider or tracks:
            options.update(meeting=True, meetingProvider=meeting_provider or "Meeting", retentionApplies=int(applies_retention))
            if not tracks:
                options["audioChunking"] = PAUSE_AWARE_CHUNKING
            if provisional_title:
                options["provisionalTitle"] = provisional_title
            if meeting_title:
                options["meetingTitle"] = meeting_title
            if meeting_microphone_warning == "digitalSilence":
                options["meetingMicrophoneWarning"] = meeting_microphone_warning
        if meeting_started:
            options["meetingStartedAt"] = meeting_started.isoformat()
            if duration is not None:
                options["meetingEndedAt"] = (meeting_started + timedelta(seconds=duration)).isoformat()
        if meeting_calendar:
            options["meetingCalendar"] = meeting_calendar
            name = meeting_calendar["title"].translate(str.maketrans({"/": "／", "\\": "＼", ":": "꞉"}))
        key, source = await run_in_threadpool(library.create, source, name, mime, options, duration,
                                              key=stage_id, notes_draft=notes_draft, tracks=tracks, rolling=rolling, annotation=annotation)
        stored_tracks = {track: source.parent / f"{track}.wav" for track in tracks} if tracks else None
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

    job = start_job(state, process_saved_audio(
        state, key, source, options, duration, tracks=stored_tracks,
        progress_only=progress_only, emit=emit,
    ))
    job.add_done_callback(lambda _: queue.put_nowait(None))

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


def start_job(state, work):
    job = asyncio.create_task(work)
    state.jobs.add(job)
    job.add_done_callback(state.jobs.discard)
    return job


def publish_finished_meeting(state, key):
    events = state.meeting_subscriber
    if events is None:
        return
    if events.full():
        # A slow subscriber can catch up from the library after reconnecting.
        while not events.empty():
            events.get_nowait()
        events.put_nowait(None)
        state.meeting_subscriber = None
    else:
        events.put_nowait(key)


async def process_saved_audio(state, key, source, options, duration, *, tracks=None, checkpoint=0,
                              recovered=False, progress_only=True, emit=lambda **_: None):
    library = state.library
    meeting = options.get("meeting", options.get("sourceSpeakerTurns", False))
    languages = options["languageHints"]
    try:
        async with state.insights.model.audio_priority(release_idle=duration is not None and duration >= 15 * 60):
            if tracks:
                frames = (source.stat().st_size - 44) // 2
                if not frames or checkpoint > frames:
                    raise ValueError("The recorded audio ends before its saved transcript checkpoint.")
                block = MAX_SAMPLES * MEETING_RATE // SAMPLE_RATE
                for start in range(checkpoint, frames, block):
                    length = min(block, frames - start)
                    result = await decode_meeting(state, tracks, start, length, languages)
                    end = start + length
                    await run_in_threadpool(library.append, key, result, end / MEETING_RATE, next_frame=end)
                samples = frames * SAMPLE_RATE // MEETING_RATE
            else:
                samples = await process_mixed_audio(state, key, source, options, duration,
                                                    checkpoint=checkpoint, progress_only=progress_only, emit=emit)
        await run_in_threadpool(library.finish, key)
        if meeting:
            await publish_meeting_exports(state, key=key)
        state.insights.wake.set()
        if meeting:
            publish_finished_meeting(state, key)
        emit(done=True, duration=samples / SAMPLE_RATE)
    except asyncio.CancelledError:
        library.finish(key, SHUTDOWN_INTERRUPTION, closing=True, recovered=recovered)
        raise
    except Exception as error:
        logger.exception("Could not complete session %s.", key)
        message = str(error) if isinstance(error, ValueError) else "Processing stopped. Open Sessions for the saved audio and completed text."
        try:
            await run_in_threadpool(library.finish, key, message)
        finally:
            emit(error=message)


async def process_mixed_audio(state, key, source, options, duration, *, checkpoint, progress_only, emit):
    languages = options["languageHints"]
    meeting = options.get("meeting", options.get("sourceSpeakerTurns", False))
    diarizer = state.diarizer
    speakers = diarizer.session() if options["diarization"] and diarizer else None
    pending = deque()
    samples = 0
    conversion = MEETING_RATE // SAMPLE_RATE
    resume_at = checkpoint // conversion

    async def transcript(pcm, offset):
        result = await decode_pcm(state, pcm, languages, speakers, offset)
        end = offset + len(pcm) // 4
        seconds = end / SAMPLE_RATE
        if meeting and result["text"] and not result["segments"]:
            result["segments"] = [{"speaker": None, "start": offset / SAMPLE_RATE, "end": seconds, "text": result["text"]}]
        await run_in_threadpool(state.library.append, key, result, seconds, next_frame=end * conversion if meeting else None)
        emit(**({} if progress_only else result), seconds=seconds, duration=duration)

    if checkpoint % conversion:
        raise ValueError("The saved mixed-audio checkpoint is invalid.")
    emit(seconds=resume_at / SAMPLE_RATE, duration=duration)
    limit = MAX_AUDIO_SECONDS + int(options["recording"])
    # Checkpoints from earlier mixed meetings refer to the original fixed-window
    # decoder (including its multilingual pause split).
    pause_aware = not meeting or options.get("audioChunking") == PAUSE_AWARE_CHUNKING
    async with aclosing(audio_blocks(source, multilingual=len(languages) > 1, pause_aware=pause_aware)) as blocks:
        async for pcm in blocks:
            offset = samples
            samples += len(pcm) // 4
            if samples > limit * SAMPLE_RATE:
                raise ValueError(DURATION_ERROR)
            if offset < resume_at < samples:
                raise ValueError("The recorded audio does not match its saved transcript checkpoint.")
            if speakers:
                await run_inference(state, speakers.push, pcm)
            if samples <= resume_at:
                # A local diarizer still sees the prefix to reconstruct its speaker identities.
                continue
            if speakers:
                pending.append((pcm, offset))
                while pending and speakers.covers(pending[0][1] + len(pending[0][0]) // 4):
                    await transcript(*pending.popleft())
            else:
                await transcript(pcm, offset)
    if not samples:
        raise ValueError(FILE_ERROR)
    if samples < resume_at:
        raise ValueError("The recorded audio ends before its saved transcript checkpoint.")
    if speakers and pending:
        await run_inference(state, speakers.push, b"", final=True)
        for block in pending:
            await transcript(*block)
    return samples


def check_native(request):
    check_client(request)
    token = request.headers.get("x-jard-token", "")
    if not hmac.compare_digest(request.app.state.native_token.encode(), token.encode()):
        raise HTTPException(403, "Open Jaro on this Mac to access desktop staging.")


@app.post("/api/native/dictation-rewrite")
async def rewrite_dictation(request: Request):
    check_native(request)
    payload = await request.json()
    text = payload.get("text") if isinstance(payload, dict) else None
    instruction = payload.get("instruction") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip() or len(text) > 8000:
        raise HTTPException(400, "Rewrite a dictation of up to 8,000 characters.")
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 2500:
        raise HTTPException(400, "Set rewrite instructions of up to 2,500 characters.")
    try:
        vocabulary = vocabulary_terms(payload.get("vocabulary", []))
        deadline = rewrite_deadline(payload.get("deadline", MAX_REWRITE_DEADLINE))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    schema = {"type": "object", "properties": {"text": {"type": "string"}},
              "required": ["text"], "additionalProperties": False}
    messages = rewrite_messages(text, instruction, vocabulary,
                                application=payload.get("application"), browser_host=payload.get("browserHost"))
    state = request.app.state
    model = state.insights.model
    state.insights.chat_count += 1
    try:
        async with asyncio.timeout(deadline), model.lock:
            await model.ready()
            await model.fit_prompt(messages, 1200)
            rewritten = json.loads("".join([piece async for piece in model.generate(messages, schema=schema, limit=1200)]))
        output = rewritten["text"].strip()
        if not output:
            raise ValueError("The rewrite returned no text.")
    except TimeoutError as error:
        logger.info("Local dictation rewrite exceeded its deadline of %d seconds.", deadline)
        raise HTTPException(422, f"The local rewrite exceeded its {deadline}-second deadline.") from error
    except Exception as error:
        logger.warning("Local dictation rewrite did not complete (%s).", type(error).__name__)
        raise HTTPException(422, "The local rewrite could not preserve the dictation.") from error
    finally:
        state.insights.chat_count -= 1
    if not keeps_rewrite_numbers(text, output):
        raise HTTPException(422, "The local rewrite changed a number. The original wording was kept.")
    return {"text": output}


@app.post("/api/native/update-quiesce/{client}", status_code=202)
async def quiesce_update(request: Request, client: UUID):
    check_native(request)
    state = request.app.state
    now = time.monotonic()
    if state.leases.streams or any(key != str(client) and expiry > now for key, expiry in state.leases.deadlines.items()):
        raise HTTPException(409, "Close other Jard windows or browser tabs before restarting.")
    if (state.draining or state.jobs or state.submitting or state.insights.active_job or state.insights.chat_count or
            state.insights.model.audio_users or state.status == "loading" or getattr(state, "sdk_pending", 0)):
        raise HTTPException(409, "Wait for the local engine to finish its current work.")
    shutdown = getattr(state, "request_shutdown", None)
    if shutdown is None:
        raise HTTPException(503, "The local engine could not be restarted.")
    state.draining = True
    return Response(status_code=202, background=BackgroundTask(shutdown))


def calendar_details(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid calendar details.")
    title, attendees = value.get("title"), value.get("attendees")
    if not isinstance(title, str) or not 1 <= len(title) <= 160 or not isinstance(attendees, list) or len(attendees) > 32:
        raise ValueError("Invalid calendar details.")
    if any(not isinstance(name, str) or len(name) > 120 for name in attendees):
        raise ValueError("Invalid calendar attendees.")
    if any(ord(char) < 32 or ord(char) == 127 for field in (title, *attendees) for char in field):
        raise ValueError("Invalid calendar details.")
    return {"title": title, "attendees": attendees}


@app.post("/api/native/staging", status_code=201)
async def new_stage(request: Request, recording: bool = False, dictation: bool = False, meeting: bool = False,
                    provider: str = Query("Meeting", min_length=1, max_length=80), calendar: dict | None = Body(None),
                    annotation_id: UUID | None = None):
    check_native(request)
    if annotation_id is not None:
        require_annotation()
    if meeting:
        require_meetings()
    if request.app.state.draining:
        raise HTTPException(409, "Jard is preparing to restart.")
    if meeting and (not recording or dictation):
        raise HTTPException(400, "A meeting must use a two-source recording stage.")
    metadata = {"meetingProvider": provider, "retentionApplies": True}
    if calendar is not None:
        if not meeting:
            raise HTTPException(400, "Calendar details require a meeting.")
        try:
            metadata["meetingCalendar"] = calendar_details(calendar)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
    if annotation_id and (recording or dictation or meeting):
        raise HTTPException(400, "An annotation must use a movie staging file.")
    key = str(annotation_id or uuid4())
    if annotation_id and await run_in_threadpool(request.app.state.library.exists, key):
        raise HTTPException(409, "This annotation has already been saved.")
    path = request.app.state.staging / key
    files = [path]
    if dictation:
        files.append(path.with_suffix(".dictation"))
    elif recording:
        files.extend(source_tracks(path).values() if meeting else ())
        files.append(recording_marker(path))
    created = []
    try:
        for file in files:
            file.touch(mode=0o600, exist_ok=False)
            created.append(file)
        if meeting:
            recording_marker(path).write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf8")
    except OSError as error:
        for file in created:
            file.unlink(missing_ok=True)
        if annotation_id and isinstance(error, FileExistsError):
            raise HTTPException(409, "This annotation is already being staged.") from error
        raise
    return {"id": key, "path": str(path)}


@app.get("/api/native/staging")
def recoverable_staging(request: Request):
    check_native(request)
    state = request.app.state
    drafts = list_drafts(state.staging)
    if state.startup_recovery:
        drafts = [draft for draft in drafts if not (draft["meeting"] and draft["id"] in state.startup_stranded)]
    return JSONResponse(drafts, headers={"Cache-Control": "no-store"})


def stage_path(state, key):
    path = state.staging / str(key)
    if path.is_symlink() or not path.is_file():
        raise HTTPException(404, "The staged file is unavailable.")
    return path


@app.delete("/api/native/staging/{key}", status_code=204)
async def abandon_stage(request: Request, key: UUID, dictation: bool = False):
    check_native(request)
    if key in request.app.state.submitting:
        raise HTTPException(409, "Audio is being accepted.")
    path = stage_path(request.app.state, key)
    if dictation and not path.with_suffix(".dictation").is_file():
        raise HTTPException(404, "This dictation is unavailable.")
    path.unlink()
    path.with_suffix(".dictation").unlink(missing_ok=True)
    recording_marker(path).unlink(missing_ok=True)
    rolling_path(path).unlink(missing_ok=True)
    meeting_title_path(path).unlink(missing_ok=True)
    for track in source_tracks(path).values():
        track.unlink(missing_ok=True)
    for asset in staged_annotation_paths(path).values():
        asset.unlink(missing_ok=True)
    return Response(status_code=204)


@app.post("/api/native/staging/{key}/submit")
async def submit_stage(request: Request, key: UUID, recording: bool = False, diarization: bool = False,
                       name: str = "", mime: str = "audio/wav", live: bool = False, recover: bool = False,
                       notes_draft: UUID | None = None, annotation: bool = False):
    check_native(request)
    return await submit_staged_audio(request.app.state, key, transcription_languages(request, require_ready=False), recording=recording,
                                    diarization=diarization, name=name, mime=mime, live=live, recover=recover,
                                    notes_draft=notes_draft, annotation=annotation)


async def submit_staged_audio(state, key, languages, *, recording, diarization, name, mime, live, recover, notes_draft=None, annotation=False):
    if annotation:
        require_annotation()
    if meetings_managed() and await run_in_threadpool(is_meeting_stage, state.staging / str(key)):
        require_meetings()
    if state.draining:
        raise HTTPException(409, "Jard is preparing to restart.")
    if key in state.submitting:
        raise HTTPException(409, "Audio is being accepted.")
    state.submitting.add(key)
    try:
        if await run_in_threadpool(state.library.exists, str(key)):
            clear_recording_stage(state.staging / str(key))
            return {"id": str(key)}
        if state.status != "ready":
            raise HTTPException(503, "The local model is unavailable.")
        source = stage_path(state, key)
        if source.with_suffix(".dictation").is_file():
            raise HTTPException(409, "Dictation belongs in My notes.")
        annotation_info = None
        if annotation:
            if recording or recover or mime != "video/mp4" or notes_draft is not None:
                raise HTTPException(400, "An annotation requires a staged MP4 movie.")
            try:
                annotation_info = await run_in_threadpool(read_staged_annotation, source)
            except (OSError, ValueError) as error:
                raise HTTPException(400, str(error)) from error
        meeting_provider = meeting_calendar = meeting_started = meeting_title = meeting_microphone_warning = None
        applies_retention = False
        if recover and recording_marker(source).is_file():
            with recording_marker(source).open("rb") as marker:
                metadata = recording_metadata(marker)
            applies_retention = metadata.get("retentionApplies") is True
            value = metadata.get("meetingProvider")
            if isinstance(value, str) and value:
                meeting_provider = value
                meeting_title = metadata.get("meetingTitle")
                meeting_microphone_warning = metadata.get("meetingMicrophoneWarning")
                saved_languages = metadata.get("languageHints")
                if not languages and isinstance(saved_languages, list) and all(isinstance(code, str) and code in LANGUAGES for code in saved_languages):
                    languages = tuple(dict.fromkeys(saved_languages))[:4]
                if isinstance(metadata.get("meetingStartedAt"), str):
                    with suppress(ValueError, OverflowError):
                        parsed = datetime.fromisoformat(metadata["meetingStartedAt"])
                        if parsed.utcoffset() == timedelta(0) and 2000 <= parsed.year < 9999:
                            meeting_started = parsed
                if "meetingCalendar" in metadata:
                    with suppress(ValueError):
                        meeting_calendar = calendar_details(metadata["meetingCalendar"])
            if not notes_draft and (candidate := metadata.get("notesDraft")) and isinstance(candidate, str):
                with state.library.connection() as db:
                    if db.execute("SELECT 1 FROM personal_note_drafts WHERE id=?", (candidate,)).fetchone():
                        notes_draft = candidate
        state.live_speakers.pop(key, None)
        tracks = None
        rolling = ()
        if recover:
            if not recording:
                raise HTTPException(400, "Only a recording can be recovered.")
            try:
                if not meeting_provider and any(path.is_file() for path in source_tracks(source).values()):
                    meeting_provider = "Meeting"
                frames = await run_in_threadpool(recover_wav, source)
                tracks = await run_in_threadpool(recover_tracks, source, frames)
                if meeting_provider:
                    rolling = read_rolling(source, frames)
            except ValueError as error:
                raise HTTPException(400, str(error)) from error
        result = await accept_audio(state, source, name, mime, languages, recording, diarization, live, True, str(key),
                                    notes_draft=str(notes_draft) if notes_draft else None, tracks=tracks,
                                    meeting_provider=meeting_provider, meeting_calendar=meeting_calendar,
                                    meeting_started=meeting_started, rolling=rolling, applies_retention=applies_retention,
                                    annotation=annotation_info, meeting_title=meeting_title,
                                    meeting_microphone_warning=meeting_microphone_warning,
                                    provisional_title=read_meeting_title(source) if meeting_provider else None)
        clear_recording_stage(source)
        return result
    finally:
        state.submitting.discard(key)


def clear_recording_stage(path):
    for file in (path, *source_tracks(path).values(), recording_marker(path), rolling_path(path),
                 meeting_title_path(path), *staged_annotation_paths(path).values()):
        try:
            file.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove completed staging files for %s", path.name)


async def dictate_audio(request, source, languages, vocabulary=(), *, sdk=False, measure_inference=False,
                        screen_recording=False, release_frame=None):
    parts = []
    samples = 0
    inference_ns = 0
    before_release = None if release_frame is None else release_frame * SAMPLE_RATE // 48_000
    state = request.app.state
    audio_priority = nullcontext() if getattr(state, "sdk_embedded", False) else state.insights.model.audio_priority()
    try:
        async with audio_priority:
            if screen_recording and not await run_in_threadpool(dictation_has_signal, source, release_frame):
                return {"text": "", "inferenceMs": 0}
            async with aclosing(audio_blocks(source, multilingual=len(languages) > 1, pause_aware=True)) as blocks:
                async for pcm in blocks:
                    count = len(pcm) // 4
                    if sdk:
                        if await request.is_disconnected():
                            raise ClientDisconnect()
                        samples += count
                        if samples > MAX_AUDIO_SECONDS * SAMPLE_RATE:
                            raise ValueError(DURATION_ERROR)
                    options = {}
                    if before_release is not None:
                        if before_release == 0 and not parts:
                            break
                        if 0 < before_release < count:
                            options["speech_prefix_samples"] = before_release
                        before_release = max(0, before_release - count)
                    result = await decode_pcm(state, pcm, languages, live=True, vocabulary=vocabulary, dictation=not sdk,
                                              measure_inference=measure_inference, screen_nonspeech=screen_recording, **options)
                    if measure_inference:
                        inference_ns += result["inferenceNs"]
                    if result["text"].strip():
                        parts.append(result["text"].strip())
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except ClientDisconnect:
        raise
    except Exception as error:
        logger.exception("Dictation inference failed.")
        raise HTTPException(500, "Could not transcribe dictation. Try again.") from error
    result = {"text": " ".join(parts)}
    if sdk:
        result["durationMs"] = round(samples * 1000 / SAMPLE_RATE)
    if measure_inference:
        result["inferenceMs"] = round(inference_ns / 1_000_000)
    return result


@app.post("/api/personal-notes/dictation")
async def browser_dictation(request: Request):
    check_audio_request(request)
    languages = transcription_languages(request)
    with tempfile.TemporaryDirectory(prefix=".upload-", dir=request.app.state.library.root) as folder:
        source = Path(folder) / "dictation"
        await save_upload(request, source)
        return await dictate_audio(request, source, languages)


@app.post("/api/native/transcribe")
async def native_transcribe_file(request: Request):
    check_native(request)
    return await browser_dictation(request)


def sdk_record(request, *, authenticate=True):
    state = request.app.state
    if "origin" in request.headers:
        raise HTTPException(403, "Use the Jard SDK from a trusted local process.")
    record = getattr(state, "sdk", None)
    if record is None:
        raise HTTPException(503, "The Jard SDK is unavailable.")
    if authenticate:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode(), record["token"].encode()):
            raise HTTPException(401, "Valid Jard SDK credentials are required.", headers={"WWW-Authenticate": "Bearer"})
    return record


@sdk_router.get("/identify")
async def sdk_identify(request: Request, challenge: str = Query(pattern=r"^[0-9a-f]{32}$")):
    record = sdk_record(request, authenticate=False)
    return JSONResponse({"proof": sdk_proof(record, challenge)}, headers={"Cache-Control": "no-store"})


@sdk_router.get("/health")
async def sdk_health(request: Request):
    sdk_record(request)
    state = request.app.state
    return {"status": "stopping" if state.draining else state.status, "model": MODEL_ID}


@sdk_router.get("/capabilities")
async def sdk_capabilities(request: Request):
    sdk_record(request)
    try:
        available = await sdk_desktop_request(request.app.state, "sdk-available", timeout=.5)
    except HTTPException:
        available = False
    return {"apiVersion": SDK_VERSION, "transcription": True, "serviceCapture": available is True,
            "maxUploadBytes": SDK_MAX_UPLOAD, "maxAudioSeconds": MAX_AUDIO_SECONDS}


@sdk_router.get("/models")
async def sdk_models(request: Request):
    sdk_record(request)
    return {"models": [{"id": MODEL_ID, "ready": request.app.state.status == "ready",
                        "supportsLanguageDetection": True, "languages": LANGUAGE_OPTIONS}]}


async def sdk_desktop_request(state, action, *, timeout=11, **options):
    unavailable = {"code": "service-capture-unavailable", "message": "Open Jaro to use its microphone."}
    if getattr(state, "sdk_embedded", False) or not getattr(state, "native_token", None) or not hasattr(state, "library"):
        raise HTTPException(503, unavailable)
    try:
        async with asyncio.timeout(timeout):
            path = await asyncio.to_thread(desktop_control_path, state.library.root)
            reader, writer = await asyncio.open_unix_connection(path, limit=8 * 1024 * 1024)
            try:
                writer.write(json.dumps({"action": action, "token": state.native_token, **options}).encode() + b"\n")
                await writer.drain()
                reply = json.loads(await reader.readline())
            finally:
                writer.close()
                with suppress(ConnectionError):
                    await writer.wait_closed()
    except (OSError, TimeoutError, ValueError) as error:
        raise HTTPException(503, unavailable) from error
    if not reply.get("ok"):
        raise HTTPException(reply.get("status", 409), {"code": reply.get("code", "dictation-busy"),
                            "message": reply.get("error", "The recording command failed.")})
    return reply["result"]


@sdk_router.post("/dictations")
async def sdk_start_dictation(request: Request, source: str = Query("SDK", max_length=80)):
    sdk_record(request)
    state = request.app.state
    if state.draining:
        raise HTTPException(503, {"code": "engine-stopping", "message": "Jard is preparing to restart."})
    return await sdk_desktop_request(state, "sdk-start", languages=transcription_languages(request), source=source)


@sdk_router.api_route("/dictations/{capture}/{action}", methods=["GET", "POST"])
async def sdk_control_dictation(request: Request, capture: UUID, action: str, cursor: int = Query(0, ge=0, le=10**9)):
    sdk_record(request)
    expected = {"audio": "GET", "levels": "GET", "finish": "POST", "cancel": "POST", "heartbeat": "POST"}.get(action)
    if expected is None or expected != request.method:
        raise HTTPException(404, "Recording command not found.")
    owner = request.headers.get("x-jard-dictation-token", "")
    if not re.fullmatch(r"[0-9a-f]{64}", owner):
        raise HTTPException(404, {"code": "dictation-not-found", "message": "This recording is unavailable."})
    return await sdk_desktop_request(request.app.state, "sdk-" + action, capture=str(capture), owner=owner, cursor=cursor)


@sdk_router.post("/transcriptions")
async def sdk_transcribe(request: Request, speech_screen: bool = False):
    sdk_record(request)
    state = request.app.state
    if state.draining:
        raise HTTPException(503, {"code": "engine-stopping", "message": "Jard is preparing to restart."})
    if state.sdk_pending >= SDK_MAX_REQUESTS:
        raise HTTPException(429, {"code": "queue-full", "message": "Jard is busy with other SDK requests."})
    if request.headers.get("content-type") not in ("application/octet-stream", "audio/wav"):
        raise HTTPException(415, "Expected audio bytes.")
    if request.headers.get("content-length", "").isdigit() and int(request.headers["content-length"]) > SDK_MAX_UPLOAD:
        raise HTTPException(413, {"code": "audio-too-large", "message": "The audio exceeds the SDK upload limit."})
    languages = transcription_languages(request)
    state.sdk_pending += 1
    try:
        root = state.sdk_upload_root if getattr(state, "sdk_embedded", False) else state.library.root
        with tempfile.TemporaryDirectory(prefix=".upload-", dir=root) as folder:
            source = Path(folder) / "dictation"
            await save_upload(request, source, maximum=SDK_MAX_UPLOAD)
            result = await dictate_audio(request, source, languages, sdk=True, screen_recording=speech_screen)
            return {"transcript": result["text"], "durationMs": result["durationMs"]}
    except ClientDisconnect as error:
        raise HTTPException(499, "The SDK client disconnected.") from error
    finally:
        state.sdk_pending -= 1


app.include_router(sdk_router)


@app.post("/api/native/staging/{key}/dictate")
async def native_dictation(request: Request, key: UUID,
                           release_frame: Annotated[int | None, Query(ge=0, le=3 * 3600 * 48_000)] = None):
    check_native(request)
    languages = transcription_languages(request)
    path = stage_path(request.app.state, key)
    if not path.with_suffix(".dictation").is_file():
        raise HTTPException(404, "This dictation is unavailable.")
    vocabulary = tuple(dict.fromkeys(word.strip() for word in request.query_params.getlist("vocabulary") if word.strip()))
    if len(vocabulary) > 32 or any(len(word) > 80 for word in vocabulary):
        raise HTTPException(400, "Use up to 32 vocabulary entries of at most 80 characters.")
    return await dictate_audio(request, path, languages, vocabulary, screen_recording=True, measure_inference=True,
                               release_frame=release_frame)


def staged_meeting_title_rows(path, through):
    try:
        with recording_marker(path).open("rb") as marker:
            # A live recorder owns the marker exclusively. Recovery uses its stored title.
            try:
                fcntl.flock(marker, fcntl.LOCK_SH | fcntl.LOCK_NB)
                return []
            except BlockingIOError:
                if not recording_metadata(marker).get("meetingProvider"):
                    return []
        return [{"start": turn["start"] or 0, "text": f"{'You' if turn['speaker'] == 1 else 'Them'}: {turn['text']}"}
                for _, _, result in read_rolling(path, through) for turn in result["segments"] if turn["text"].strip()]
    except OSError:
        return []


@app.post("/api/native/staging/{key}/meeting-title")
async def write_staged_meeting_title(request: Request, key: UUID, through: int = Query(gt=0, le=(MAX_AUDIO_SECONDS + 1) * MEETING_RATE)):
    check_native(request)
    require_meetings()
    state = request.app.state
    if state.draining or key in state.submitting:
        raise HTTPException(409, "Meeting title generation is unavailable while saving or restarting.")

    def current_policy():
        with state.library.connection() as db:
            return meeting_brief.automatic_policy(db)

    policy = current_policy()
    if not policy[0]:
        return {"automatic": False}
    rows = await run_in_threadpool(staged_meeting_title_rows, stage_path(state, key), through)
    if not rows:
        raise HTTPException(409, "There is no live meeting transcript to title.")
    model = state.insights.model

    async def write():
        async with asyncio.timeout(30):
            await model.available.wait()
            async with model.lock:
                if current_policy() != policy:
                    return None
                await model.ready()
                if current_policy() != policy:
                    return None
                return await meeting_brief.generate_live_title(model, rows)

    job = start_job(state, write())
    try:
        while not job.done():
            if await request.is_disconnected():
                return Response(status_code=499)
            await asyncio.wait([job], timeout=.2)
        if current_policy() != policy:
            return {"automatic": False}
        return {"title": job.result()}
    except TimeoutError:
        raise HTTPException(504, "The local meeting title timed out.") from None
    except (RuntimeError, ValueError):
        raise HTTPException(503, "The local writer could not name this meeting.") from None
    finally:
        if not job.done():
            job.cancel()
        await asyncio.gather(job, return_exceptions=True)


@app.post("/api/native/staging/{key}/preview")
async def preview_stage(request: Request, key: UUID, start: int = Query(ge=0, le=MAX_AUDIO_SECONDS * 48000),
                        frames: int = Query(gt=0, le=25 * 48000), diarization: bool = False, meeting: bool = False):
    check_native(request)
    if meetings_managed() and (meeting or await run_in_threadpool(is_meeting_stage, request.app.state.staging / str(key))):
        require_meetings()
    languages = transcription_languages(request)
    path = stage_path(request.app.state, key)
    if meeting:
        async with request.app.state.insights.model.audio_priority():
            try:
                return await decode_meeting(request.app.state, source_tracks(path), start, frames, languages, live=True)
            except (OSError, ValueError) as error:
                raise HTTPException(400, "Could not decode meeting preview audio.") from error
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
    try:
        pcm = await resample_capture(data)
    except ValueError as error:
        raise HTTPException(400, "Could not decode live audio.") from error
    async with request.app.state.insights.model.audio_priority():
        return await decode_pcm(request.app.state, pcm, languages, live_speakers(request, key) if diarization else None,
                                start * SAMPLE_RATE // 48000, live=True, dictation=path.with_suffix(".dictation").is_file())


def start_in_background(*, open_page: bool):
    log = log_directory() / PROCESS_LOG
    with startup_lock():
        if not running_engine():
            with open_log(PROCESS_LOG, append=True) as output:
                process = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), "--serve"], cwd=WORKSPACE_ROOT,
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


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def workspace_page():
    return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-store"})


def main():
    parser = argparse.ArgumentParser(description="Run Jaro locally.")
    parser.add_argument("--setup-model", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--ensure", action="store_true", help="Attach to or start the shared engine.")
    parser.add_argument("--background", action="store_true", help="Start a detached server and open the browser.")
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--embedded", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.setup_model:
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        prepare_model()
        return
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    if args.embedded:
        from sdk_embedded import run_embedded

        try:
            raise SystemExit(run_embedded(sdk_router, SpeechInference, RumbleModel))
        except (OSError, RuntimeError) as error:
            parser.exit(1, str(error) + "\n")
    if not (DIST / "index.html").is_file():
        parser.error("Run Setup Jaro to build the browser interface.")
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
    app.state.is_shutting_down = lambda: server.should_exit
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((HOST, PORT))
        except OSError as error:
            parser.exit(1, f"Cannot start Jaro at {URL}: {error}\n")
        app.state.sdk_port = listener.getsockname()[1]
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
