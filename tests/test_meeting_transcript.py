import unittest

import numpy as np

from meeting_transcript import EchoWindow, FRAME, merge_sources


def signal(levels):
    return np.repeat(levels * 32767, FRAME).astype("<i2").tobytes()


def speech(text, start, end, language="en"):
    return {"words": [{"text": text, "start": start, "end": end}], "language": language}


class MeetingEchoTests(unittest.TestCase):
    def test_delayed_echo_dropped_but_double_talk_keeps_local_sentence(self):
        remote = np.zeros(550)
        remote[25:450] = np.random.default_rng(9).uniform(0.012, 0.15, size=425)
        mic = np.roll(remote, 4) * 0.25
        mic[196:245] += 0.24
        window = EchoWindow(signal(mic), signal(remote), 0)
        self.assertEqual(window.classify(0.8, 2), "echo")
        self.assertEqual(window.classify(3.95, 4.7), "local")
        last = speech("Local voice.", 3.95, 4.7)["words"][0]
        result = merge_sources({
            "mic": {"words": [*speech("Remote sentence.", 0.8, 2)["words"], last], "language": "en"},
            "system": speech("Remote sentence.", 0.72, 1.92),
        }, window, 0, 11)
        self.assertEqual(result["text"], "Remote sentence. Local voice.")
        self.assertEqual([word["speaker"] for word in result["words"]], [2, 1])
        self.assertEqual(result["echoSuppressedUtterances"], 1)

    def test_headphones_keep_similar_words_spoken_independently(self):
        remote = np.random.default_rng(4).uniform(0.015, 0.16, size=500)
        local = np.random.default_rng(11).uniform(0.015, 0.16, size=500)
        window = EchoWindow(signal(local), signal(remote), 0)
        self.assertEqual(window.classify(1, 3), "local")
        result = merge_sources({name: speech("Agreed.", 1, 3) for name in ("mic", "system")}, window, 0, 10)
        self.assertEqual(result["text"], "Agreed. Agreed.")
        self.assertEqual(result["echoSuppressedUtterances"], 0)

    def test_silent_remote_and_missing_word_timing_preserve_microphone_text(self):
        level = np.full(80, 0.02)
        window = EchoWindow(signal(level), signal(level * 0), 0)
        result = merge_sources({"mic": speech("Local voice.", None, None), "system": {"words": []}}, window, 4, 5.6)
        self.assertEqual(result["segments"], [{"text": "Local voice.", "speaker": 1, "start": 4, "end": 5.6}])
        self.assertIsNone(result["words"][0]["start"])
