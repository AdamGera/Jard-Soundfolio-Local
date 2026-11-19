import tempfile
import unittest
from pathlib import Path

from session_store import MissingSession, SessionStore


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = SessionStore(self.root / "data")
        self.original = self.root / "meeting.wav"
        self.original.write_bytes(b"original audio")
        copy = self.root / "upload"
        copy.write_bytes(self.original.read_bytes())
        self.key, self.audio = self.store.create(copy, self.original.name, "audio/wav", {
            "recording": False, "diarization": True, "languageHints": ["en", "ar"],
        }, 8)
        for start, end, text in [(0, 4, "أهلاً"), (4, 8, "again")]:
            self.store.append(self.key, {"language": "Arabic", "text": text, "words": [{"text": text}],
                "segments": [{"speaker": 1, "start": start, "end": end, "text": text}],
                "activity": [{"speaker": 1, "start": start, "end": end}]}, end)

    def test_completed_session_and_managed_copy_survive_restart(self):
        self.store.finish(self.key)
        reopened = SessionStore(self.store.root)
        saved = reopened.get(self.key)
        self.assertEqual(saved["transcript"]["text"], "أهلاً again")
        self.assertEqual(saved["transcript"]["speakerActivity"][0]["intervals"], [{"start": 0, "end": 8}])
        self.assertEqual(saved["transcript"]["segments"][0]["end"], 8)
        self.assertEqual(len(saved["transcript"]["words"]), 2)
        self.assertEqual(len(reopened.list("أهلاً AGAIN")), 1)
        reopened.delete(self.key)
        self.assertFalse(self.audio.exists())
        self.assertTrue(self.original.exists())
        with self.assertRaises(MissingSession):
            reopened.get(self.key)
        with reopened.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM chunks").fetchone()[0], 0)

    def test_processing_session_recovers_partial_results_after_restart(self):
        with self.assertRaises(ValueError):
            self.store.delete(self.key)
        saved = SessionStore(self.store.root).get(self.key)
        self.assertEqual(saved["status"], "interrupted")
        self.assertEqual(saved["transcript"]["text"], "أهلاً again")
        self.assertTrue(saved["transcript"]["interruption"])
        self.assertTrue(self.audio.exists())
