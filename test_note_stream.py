import unittest

from note_stream import Previews, patch


class DraftTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconnect_and_language_isolation(self):
        previews = Previews()
        with previews.watch("session", "ar") as subscriber:
            started = subscriber.changed
            with previews.track("session", "ar"):
                self.assertTrue(started.is_set())
                previous = subscriber.changed
                patch(summary="مل")
                patch(summary="ملخص")
                self.assertTrue(previous.is_set())
                with previews.watch("session", "ar") as reconnected:
                    self.assertEqual(reconnected.draft, {"language": "ar", "summary": "ملخص"})
                with previews.watch("session", "en") as other:
                    self.assertIsNone(other.draft)
                with previews.watch("other", "ar") as other:
                    self.assertIsNone(other.draft)
                finished = subscriber.changed
            self.assertTrue(finished.is_set())
            self.assertIsNone(subscriber.draft)
        self.assertFalse(previews.channels)
