import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call

import numpy as np

from audio_file import split_at_pauses
from diarization import SpeakerSession
from rumble_model import SAMPLE_RATE


class TranscriptionTests(unittest.TestCase):
    def test_multilingual_pause_preserves_every_sample(self):
        phrase = np.full(4 * SAMPLE_RATE, 0.15, dtype="<f4")
        gap = np.zeros(SAMPLE_RATE // 2, dtype="<f4")
        pcm = np.concatenate((phrase, gap, phrase)).tobytes()
        parts = list(split_at_pauses(pcm))
        self.assertEqual(len(parts), 2)
        self.assertEqual(b"".join(parts), pcm)
        self.assertEqual(len(parts[0]), len(pcm) // 2)

    def test_untimed_words_follow_the_current_voice_then_carry_across_silence(self):
        processor = SimpleNamespace(feature_extractor=SimpleNamespace(hop_length=160))
        session = SpeakerSession(None, processor, None)
        frames = np.zeros((300, 8))
        frames[:100, 0] = 0.9
        frames[100:200, 1] = 0.9
        session.blocks.append((0, frames))
        for second, expected in enumerate([1, 2, 2]):
            segments, words = session.label("Two words.", [], second * SAMPLE_RATE, 1, include_word_times=True)
            self.assertEqual([(s["text"], s["speaker"]) for s in segments], [("Two words.", expected)])
            self.assertEqual([w["speaker"] for w in words], [expected, expected])
            self.assertTrue(all(w["start"] is None and w["end"] is None for w in words))


class DecoderCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_final_pass_retries_same_block_with_dynamic_cache_on_memory_error(self):
        from server import decode_pcm

        model = Mock()
        model.transcribe.side_effect = [RuntimeError("MPS backend out of memory"), ("", None, [])]
        state = SimpleNamespace(model=model, inference=asyncio.Lock())
        request = SimpleNamespace(app=SimpleNamespace(state=state))
        pcm = np.zeros(SAMPLE_RATE, dtype="<f4").tobytes()
        await decode_pcm(request, pcm, ("en",))
        self.assertEqual(model.transcribe.call_args_list, [
            call(pcm, ("en",), timed=True, cache_implementation="static"),
            call(pcm, ("en",), timed=True, cache_implementation=None),
        ])
        model.torch.mps.empty_cache.assert_called_once_with()
