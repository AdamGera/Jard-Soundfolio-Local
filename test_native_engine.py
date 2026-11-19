"""The Mac app and browser share one engine, one library and accepted jobs."""
import asyncio
import os
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from engine_lifecycle import ClientLeases, engine_proof, library_id
from server import URL, app


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

    def test_clients_can_detach_independently(self):
        a,b = (str(uuid4()) for _ in range(2))
        for key in (a,b):
            self.assertEqual(self.client.post(f"/api/clients/{key}").status_code, 204)
        self.assertEqual(self.client.delete(f"/api/clients/{a}").status_code, 204)
        self.assertIn(b, app.state.leases.deadlines)
        self.assertNotIn(a, app.state.leases.deadlines)
        self.assertEqual(self.client.post("/api/quit", headers={"Origin":"https://example.net","X-Jard-Action":"quit"}).status_code, 403)


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
