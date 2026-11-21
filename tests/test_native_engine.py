"""The Mac app and browser share one engine, one library and accepted jobs."""
import asyncio
from contextlib import aclosing
import json
import os
import tempfile
import unittest
import wave
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from fastapi import HTTPException

from annotation_assets import staged_annotation_paths
from engine_lifecycle import ClientLeases, engine_proof, library_id
from managed_preferences import ANNOTATION_MANAGED, MEETINGS_MANAGED, annotate_managed, meetings_managed
import meeting_brief
from native_recordings import recording_marker
from server import URL, app, connect_browser, quiesce_update, renew_client
from session_store import SHUTDOWN_INTERRUPTION, SessionStore


class NativeEngineTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        env = patch.dict(os.environ, {"RUMBLE_DATA_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)

        async def load(app):
            app.state.status = "ready"
            app.state.model = SimpleNamespace(transcribe=Mock(return_value=("Hello Jard", "en", [])))

        fake = patch("server.load_model", load)
        fake.start()
        self.addCleanup(fake.stop)
        # No model downloads, paid services or GPU are needed by this test.
        notes = patch("insights.Insights.preview_job", new_callable=AsyncMock, side_effect=RuntimeError("offline test"))
        notes.start()
        self.addCleanup(notes.stop)
        self.client = self.enterContext(TestClient(app, base_url=URL))
        self.secret = (self.root / ".native-token").read_text().strip()
        self.header = {"X-Jard-Token": self.secret}

    def new_stage(self):
        value = self.client.post("/api/native/staging", headers=self.header).raise_for_status().json()
        path = Path(value["path"])
        self.assertTrue(path.is_relative_to(self.root))
        with wave.open(str(path), "wb") as dest:
            dest.setparams((1, 2, 48_000, 0, "NONE", ""))
            dest.writeframes(bytes(2 * 48_000))
        return f"/api/native/staging/{value['id']}", value["id"]

    def test_engine_proves_library_and_native_operations_require_local_token(self):
        info = self.client.get("/api/status?challenge=012345").json()
        self.assertEqual(info["proof"], engine_proof(self.secret, "012345"))
        self.assertEqual(info["libraryId"], library_id(self.root.resolve()))
        self.assertEqual((self.root / ".native-token").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.client.post("/api/native/staging").status_code, 403)
        self.assertEqual(self.client.post("/api/native/staging", headers={**self.header, "Origin":"https://example.net"}).status_code, 403)
        ep, _ = self.new_stage()
        self.assertEqual(self.client.delete(ep, headers=self.header).status_code, 204)
        self.assertEqual(self.client.post(ep + "/submit", headers=self.header).status_code, 404)

    def test_live_preview_uses_written_range_and_submit_is_idempotent(self):
        ep, key = self.new_stage()
        self.assertEqual(self.client.post(ep + "/preview?start=48000&frames=240", headers=self.header).status_code, 409)
        response = self.client.post(ep + "/preview?start=0&frames=48000&language=en", headers=self.header)
        self.assertEqual(response.raise_for_status().json()["text"], "Hello Jard")
        self.assertEqual(len(app.state.model.transcribe.call_args.args[0]), 16_000 * 4)

        opts = {"recording": "true", "name": "Call", "language": "en"}
        session = self.client.post(ep+"/submit", params=opts, headers=self.header).raise_for_status().json()["id"]
        self.assertEqual(session, key)
        self.assertEqual(self.client.post(ep+"/submit", params=opts, headers=self.header).json(), {"id":key})
        self.client.portal.call(self.wait_for_jobs)
        saved = self.client.get(f"/api/sessions/{key}").raise_for_status().json()
        self.assertEqual(saved["status"], "complete")
        self.assertEqual(saved["transcript"]["text"], "Hello Jard")
        self.assertTrue(self.client.get(saved["audioUrl"]).content.startswith(b"RIFF"))
        self.assertEqual(self.client.delete(f"/api/sessions/{key}").status_code, 204)
        self.assertEqual(self.client.get(f"/api/sessions/{key}").status_code, 404)

    @staticmethod
    async def wait_for_jobs():
        await asyncio.wait_for(asyncio.gather(*app.state.jobs), timeout=10)

    def test_meeting_delete_reports_and_retries_a_committed_trash_archive(self):
        source = self.root / "trash-fixture.wav"
        source.write_bytes(b"audio fixture")
        library = app.state.library
        options = {"meeting": True, "recording": True, "diarization": False}
        key, _ = library.create(source, "Meeting fixture", "audio/wav", options, 1)
        library.finish(key)
        url = f"/api/sessions/{key}"
        self.assertEqual(self.client.delete(url, headers={"Origin": "https://example.net"}).status_code, 403)
        with patch.object(library, "_deliver_meeting_archive", side_effect=OSError("Trash unavailable")):
            response = self.client.delete(url)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json(), {"trashPending": True})
        self.assertEqual(self.client.get(url).status_code, 404)

        deliver = library._deliver_meeting_archive
        with patch.object(library, "_deliver_meeting_archive", side_effect=lambda key, _: deliver(key, lambda path: path.rename(self.root / "Fixture Trash"))):
            self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertTrue((self.root / "Fixture Trash" / "Meeting.json").is_file())

    def test_clients_can_detach_independently(self):
        a,b = (str(uuid4()) for _ in range(2))
        for key in (a,b):
            self.assertEqual(self.client.post(f"/api/clients/{key}").status_code, 204)
        self.assertEqual(self.client.delete(f"/api/clients/{a}").status_code, 204)
        self.assertIn(b, app.state.leases.deadlines)
        self.assertNotIn(a, app.state.leases.deadlines)
        self.assertEqual(self.client.post("/api/quit", headers={"Origin":"https://example.net","X-Jard-Action":"quit"}).status_code, 403)

    def test_local_rewrite_follows_spoken_enumeration_and_rejects_numeric_drift(self):
        response_text = "1. Order 1200 labels.\n2. Record −12.5%."
        messages = []

        async def generate(prompt, **options):
            messages.append(prompt)
            yield json.dumps({"text": response_text})

        model = SimpleNamespace(lock=asyncio.Lock(), ready=AsyncMock(), fit_prompt=AsyncMock(), generate=generate)
        route = "/api/native/dictation-rewrite"
        raw = "Point one, order 1200 labels. Point two, record −12.5%."
        payload = {"text": raw, "instruction": "Write a concise list.", "vocabulary": ["Jaro"]}
        with patch.object(app.state.insights, "model", model):
            response = self.client.post(route, json=payload, headers=self.header).raise_for_status()
            self.assertEqual(response.json()["text"], response_text)
            protocol, *examples, request = messages[-1]
            self.assertIn("Unless the writing style asks for another format", protocol["content"])
            self.assertIn("one per line", protocol["content"])
            self.assertIn("including signs and separators", protocol["content"])
            self.assertEqual([turn["role"] for turn in examples], ["user", "assistant", "user", "assistant"])
            self.assertEqual(json.loads(request["content"]), {
                "style": payload["instruction"], "vocabulary": ["Jaro"], "transcript": raw,
            })
            context = {**payload, "application": "Fixture Browser", "browserHost": "https://Example.com:443/private?q=secret"}
            self.client.post(route, json=context, headers=self.header).raise_for_status()
            request = json.loads(messages[-1][-1]["content"])
            self.assertEqual(request["destination"], {"application": "Fixture Browser", "browserHost": "example.com"})
            self.assertNotIn("private", messages[-1][-1]["content"])
            self.assertNotIn("secret", messages[-1][-1]["content"])
            response_text = "- Order 1200 labels.\n- Record −12.5%."
            payload["instruction"] = "Use bullets instead of numbered markers."
            response = self.client.post(route, json=payload, headers=self.header).raise_for_status()
            self.assertEqual(response.json()["text"], response_text)
            response_text = response_text.replace("1200", "1,200")
            refused = self.client.post(route, json=payload, headers=self.header)
            self.assertEqual(refused.status_code, 422)
            self.assertIn("The original wording was kept", refused.json()["detail"])
            self.assertEqual(app.state.insights.chat_count, 0)

    def test_managed_annotation_blocks_admission_but_keeps_saved_assets_readable(self):
        key = str(uuid4())
        video = self.root / "example.mp4"
        video.write_bytes(b"saved video")
        staged_annotation_paths(video)["manifest"].write_text("{}")
        options = {"recording": False, "diarization": False, "languageHints": []}
        app.state.library.create(video, "Saved annotation.mp4", "video/mp4", options, 1, key=key,
                                 annotation={"version": 1, "source": "display", "frames": [], "strokeCount": 0})
        app.state.library.finish(key)
        capture = str(uuid4())
        endpoint = "/api/native/staging"
        params = {"annotation_id": capture}

        annotate_managed.cache_clear()
        self.addCleanup(annotate_managed.cache_clear)
        with patch("managed_preferences.forced_boolean", side_effect=lambda key: key == "DisableAnnotate"):
            refused = {"detail": ANNOTATION_MANAGED, "code": "managed"}
            self.assertNotIn("code", self.client.post(endpoint, params=params).json())
            denied = self.client.post(endpoint, headers=self.header, params=params)
            self.assertEqual((denied.status_code, denied.json()), (403, refused))
            self.assertFalse((app.state.staging / capture).exists())

            stage, draft = self.new_stage()
            denied = self.client.post(stage + "/submit", headers=self.header,
                                      params={"annotation": "true", "mime": "video/mp4"})
            self.assertEqual((denied.status_code, denied.json()), (403, refused))
            self.assertTrue((app.state.staging / draft).exists())
            self.assertEqual(self.client.delete(stage, headers=self.header).status_code, 204)
            saved = self.client.get(f"/api/sessions/{key}").raise_for_status().json()
            self.assertEqual(saved["annotation"]["source"], "display")
            self.assertEqual(self.client.get(saved["audioUrl"]).content, b"saved video")
            self.assertEqual(self.client.get(saved["annotation"]["manifestUrl"]).json(), {})

        annotate_managed.cache_clear()
        allowed = self.client.post(endpoint, headers=self.header, params=params)
        self.assertEqual((allowed.status_code, allowed.json()["id"]), (201, capture))


class ManagedMeetingsEngineTests(unittest.TestCase):
    def test_managed_launch_defers_capture_recovery_and_writing(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"RUMBLE_DATA_DIR": folder}))
        forced = self.enterContext(patch("managed_preferences.forced_boolean",
                                         side_effect=lambda key: key == "DisableMeetings"))
        meetings_managed.cache_clear()
        self.addCleanup(meetings_managed.cache_clear)

        async def ready(app):
            app.state.status = "ready"

        self.enterContext(patch("server.load_model", ready))
        store = SessionStore(folder)
        options = {"meeting": True, "recording": True, "diarization": False, "languageHints": []}
        saved, interrupted = str(uuid4()), str(uuid4())
        passage = {"text": "The next planning meeting is Friday.", "language": "English", "words": [], "segments": []}
        for key in (saved, interrupted):
            wave_path = store.root / f"{key}.wav"
            wave_path.write_bytes(b"fixture audio")
            store.create(wave_path, "Planning", "audio/wav", options, 1, key=key)
            store.append(key, passage, .5, next_frame=24000)
        store.finish(saved)
        store.finish(interrupted, SHUTDOWN_INTERRUPTION, closing=True)
        staged = store.root / "staging" / str(uuid4())
        staged.parent.mkdir()
        staged.write_bytes(b"retained draft")
        recording_marker(staged).write_text(json.dumps({"meetingProvider": "Fixture"}))
        with store.connection() as db:
            meeting_brief.settle(db, saved, 1, {"title": "Planning notes", "summary": "Meet on Friday."})
            meeting_brief.enqueue(db, saved, automatic=True)
            overview = dict(language="en", title="Previous overview", summary="Planning is on Friday.", topics=[])
            db.execute("UPDATE overviews SET body=? WHERE id=?", (json.dumps(overview), saved))
            db.execute("UPDATE notes SET displayed=base WHERE id=?", (saved,))
            checkpoint = db.execute("SELECT next_frame,attempts FROM meeting_finalization WHERE session_id=?",
                                    (interrupted,)).fetchone()
            self.assertIsNotNone(checkpoint)
            retry_budget = tuple(checkpoint)

        managed_writing = self.enterContext(patch("insights.Insights.write_meeting_brief", new_callable=AsyncMock))
        managed_overview = self.enterContext(patch("insights.Insights.preview_job", new_callable=AsyncMock))
        with TestClient(app, base_url=URL) as client:
            header = {"X-Jard-Token": (store.root / ".native-token").read_text().strip()}
            refused = {"detail": MEETINGS_MANAGED, "code": "managed"}
            stage = f"/api/native/staging/{staged.name}"
            session = f"/api/sessions/{saved}"
            for target, parameters in [
                ("/api/native/staging", {"recording": True, "meeting": True}),
                (stage + "/submit", {"recording": True, "recover": True}),
                (stage + "/preview", {"start": 0, "frames": 256}),
                (stage + "/meeting-title", {"through": 256}),
                (session + "/meeting-brief", {}),
            ]:
                with self.subTest(target=target):
                    response = client.post(target, headers=header, params=parameters)
                    self.assertEqual((response.status_code, response.json()), (403, refused))
            overview_request = client.post(session + "/overview", json={"language": "auto", "regenerate": True})
            self.assertEqual((overview_request.status_code, overview_request.json()), (403, refused))
            self.assertEqual(client.get("/api/meetings/automatic-notes").json(), {"enabled": False, "managed": True})
            change = client.put("/api/meetings/automatic-notes", headers={"Origin": URL}, json={"enabled": False})
            self.assertEqual((change.status_code, change.json()), (403, refused))

            detail = client.get(session).raise_for_status().json()
            self.assertTrue(detail["meetingsManaged"])
            self.assertFalse(app.state.startup_recovery)
            self.assertEqual(detail["transcript"]["text"], passage["text"])
            self.assertEqual(detail["overview"]["version"]["summary"], overview["summary"])
            self.assertEqual(client.get(session + "/meeting-brief").json()["summary"], "Meet on Friday.")
            self.assertIn("Meet on Friday.", client.get(session + "/export?kind=md").raise_for_status().text)
            self.assertEqual(staged.read_bytes(), b"retained draft")
            self.assertIsNone(app.state.insights.next_job())
            managed_writing.assert_not_awaited()
            managed_overview.assert_not_awaited()
            with store.connection() as db:
                self.assertEqual(tuple(db.execute("SELECT next_frame,attempts FROM meeting_finalization WHERE session_id=?",
                                                  (interrupted,)).fetchone()), retry_budget)
                self.assertEqual(meeting_brief.read(db, saved)["status"], "queued")
                self.assertEqual(meeting_brief.automatic_policy(db), (True, 0))

        forced.side_effect = None
        forced.return_value = False
        meetings_managed.cache_clear()
        resumed = Event()
        claims = []

        async def observe_recovery(state, stranded):
            claims.append(state.library.claim_meeting_finalization())
            resumed.set()
            return False

        with (patch("insights.Insights.run", new_callable=AsyncMock),
              patch("server.recover_pending_meeting", side_effect=observe_recovery),
              TestClient(app, base_url=URL) as client):
            self.assertTrue(app.state.startup_recovery)
            self.assertEqual(client.get("/api/meetings/automatic-notes").json(), {"enabled": True})
            self.assertEqual(app.state.insights.next_job()[0], saved)
            header = {"X-Jard-Token": (store.root / ".native-token").read_text().strip()}
            self.assertEqual(client.post("/api/native/staging", params={"recording": True, "meeting": True},
                                         headers=header).status_code, 201)
            self.assertTrue(resumed.wait(5), "The engine did not resume its interrupted meeting.")
            self.assertEqual((claims[0]["id"], claims[0]["next_frame"]), (interrupted, retry_budget[0]))


class LeaseTests(unittest.TestCase):
    @patch("engine_lifecycle.time.monotonic")
    def test_crashed_client_expires_and_accepted_work_finishes(self, clock):
        clock.return_value = 0
        leases = ClientLeases()
        leases.renew("desktop")
        clock.return_value = 60
        self.assertFalse(leases.idle(False))
        clock.return_value = 180
        self.assertFalse(leases.idle(True))
        self.assertFalse(leases.deadlines)
        clock.return_value = 211
        self.assertTrue(leases.idle(False))

    @patch("engine_lifecycle.time.monotonic")
    def test_browser_disconnect_starts_the_idle_grace_without_a_heartbeat(self, clock):
        clock.return_value = 0
        leases = ClientLeases()
        with leases.browser_connection():
            clock.return_value = 180
            self.assertFalse(leases.idle(False))
            self.assertFalse(leases.deadlines)
            clock.return_value = 200
        clock.return_value = 229
        self.assertFalse(leases.idle(False))
        clock.return_value = 230
        self.assertTrue(leases.idle(False))


class BrowserRestartTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_ownership_and_late_connections_during_restart(self):
        leases = ClientLeases()
        desktop, tab = uuid4(), uuid4()
        leases.renew(str(desktop))
        insights = SimpleNamespace(active_job=None, chat_count=0, model=SimpleNamespace(audio_users=0))
        state = SimpleNamespace(native_token="fixture", leases=leases, draining=False, jobs=set(),
                                submitting=set(), insights=insights, status="ready", sdk_pending=0,
                                request_shutdown=Mock())
        request = SimpleNamespace(headers={"x-jard-token": "fixture"}, app=SimpleNamespace(state=state))

        async def start_stream():
            return (await connect_browser(tab, request)).body_iterator

        first, second, delayed = [await self.enterAsyncContext(aclosing(await start_stream())) for _ in range(3)]

        self.assertIn("Jaro connected", await anext(first))
        self.assertIn("Jaro connected", await anext(second))
        self.assertEqual(set(leases.deadlines), {str(desktop)})
        with self.assertRaisesRegex(HTTPException, "Close other"):
            await quiesce_update(request, desktop)

        await first.aclose()
        self.assertEqual(len(leases.streams), 1)
        with self.assertRaisesRegex(HTTPException, "Close other"):
            await quiesce_update(request, desktop)
        await second.aclose()

        # A native heartbeat remains independent even if it uses the same ID.
        self.assertEqual((await renew_client(tab, request)).status_code, 204)
        with self.assertRaisesRegex(HTTPException, "Close other"):
            await quiesce_update(request, desktop)
        leases.detach(str(tab))
        response = await quiesce_update(request, desktop)
        self.assertEqual(response.status_code, 202)
        self.assertTrue(state.draining)

        for stream in (delayed, await start_stream()):
            with self.assertRaises(StopAsyncIteration):
                await anext(stream)
        self.assertFalse(leases.streams)
        with self.assertRaises(HTTPException) as refused:
            await renew_client(tab, request)
        self.assertEqual(refused.exception.status_code, 409)
        self.assertEqual(set(leases.deadlines), {str(desktop)})


class RecoveryTests(unittest.TestCase):
    def test_active_recording_is_excluded_until_its_owner_releases_it(self):
        import fcntl
        from native_recordings import list_drafts, recording_marker

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / str(uuid4())
            path.write_bytes(bytes(46))
            with recording_marker(path).open("wb") as marker:
                fcntl.flock(marker, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(list_drafts(root), [])
            self.assertEqual([r["id"] for r in list_drafts(root)], [path.name])

    def test_draft_survives_restart_and_final_partial_frame_is_repaired(self):
        from native_recordings import list_drafts, recover_wav, recording_marker
        from session_store import open_library
        import struct

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with open_library(root):
                staging = root / "staging"
                staging.mkdir()
                path = staging / str(uuid4())
                with wave.open(str(path), "wb") as out:
                    out.setparams((1, 2, 48000, 0, "NONE", ""))
                    out.writeframes(bytes(96000))
                recording_marker(path).touch()
            with path.open("ab") as out:
                out.write(bytes(1025))  # a crash before checkpointing the header
            with open_library(root):
                self.assertEqual(list_drafts(staging)[0]["id"], path.name)
                recover_wav(path)
                header = path.read_bytes()[:44]
                self.assertEqual(struct.unpack_from("<I", header, 40)[0], 96000 + 1024)
                self.assertEqual(path.stat().st_size, 44 + 96000 + 1024)
