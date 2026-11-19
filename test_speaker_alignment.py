import unittest

import numpy as np

from speaker_alignment import ctc_path


class DenseSpeechTest(unittest.TestCase):
    def test_backtracking_past_byte_boundary(self):
        labels = np.tile([1, 2], 75)
        scores = np.full((len(labels), 3), -20.0, dtype=np.float32)
        scores[np.arange(len(labels)), labels] = 0
        path = ctc_path(scores, labels, blank=0)
        np.testing.assert_array_equal(path, 2 * np.arange(len(labels)) + 1)


class PlaybackTimingTest(unittest.TestCase):
    def test_word_records_preserve_script_spacing_and_absolute_seek_times(self):
        from speaker_alignment import word_records

        text = "你好،  مرحبا!"
        words = [(0, (0.12, 0.25)), (1, (0.25, 0.45)), (2, None), (5, (1.0, 2.2))]
        records = word_records(text, words, start=25, duration=2)
        self.assertEqual("".join(w["text"] + w["separator"] for w in records), text)
        self.assertEqual(records[0]["start"], 25.12)
        self.assertEqual(records[-1]["end"], 27)
        self.assertIsNone(records[2]["start"])
        self.assertEqual(records[2]["blockStart"], 25)
