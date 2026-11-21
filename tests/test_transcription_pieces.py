import io
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

import jardctl
from rumble_model import MAX_SAMPLES, SAMPLE_RATE
from transcription_pieces import audio_pieces, quiet_cut, validate_file, wav_piece


class FileTranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.root / "input.wav"

    def test_quiet_cut_preserves_the_quiet_boundary(self):
        samples = np.full(MAX_SAMPLES, 1270, dtype="<i2")
        start = MAX_SAMPLES - SAMPLE_RATE * 2
        samples[start:start + SAMPLE_RATE // 10] = 0
        self.assertEqual(quiet_cut(samples.tobytes()) // 2, start + SAMPLE_RATE // 20)

    def test_truncated_wave_is_rejected_before_endpoint_selection(self):
        audio = wav_piece(b"\x01\x00" * 1000)
        self.path.write_bytes(audio[:-1])
        with self.assertRaises(ValueError):
            validate_file(self.path)

    def test_unknown_wave_size_is_left_to_the_decoder(self):
        audio = bytearray(wav_piece(b"\x01\x00" * 1000))
        audio[4:8] = b"\xff" * 4
        self.path.write_bytes(audio)
        validate_file(self.path)

    @unittest.skipUnless(shutil.which("ffmpeg"), "Requires the local FFmpeg decoder")
    def test_long_decode_preserves_every_sample_in_bounded_pieces(self):
        samples = np.arange(MAX_SAMPLES * 2 + 123, dtype="<i2")
        self.path.write_bytes(wav_piece(samples.tobytes()))
        pieces = list(audio_pieces(self.path))
        self.assertGreater(len(pieces), 2)
        self.assertTrue(all(len(piece) <= MAX_SAMPLES * 2 for piece in pieces))
        self.assertEqual(b"".join(pieces), samples.tobytes())

    def test_cli_buffers_output_and_closes_decoder_when_a_piece_fails(self):
        closed = []
        def pieces(_):
            try:
                yield b"\x01\x00\xee\x00"
                raise ValueError("Bad trailing data.")
            finally:
                closed.append(True)
        args = SimpleNamespace(file=self.path, language=["en"], json=True)
        output = io.StringIO()
        with patch("transcription_pieces.audio_pieces", pieces), patch("jardctl.transcription_service"), \
                patch("sdk_embedded.sdk_transcription", return_value="No partial result"), patch("sys.stdout", output):
            with self.assertRaisesRegex(ValueError, "Bad trailing data"):
                jardctl.transcribe_file(self.root, args)
        self.assertEqual(closed, [True])
        self.assertEqual(output.getvalue(), "")

    def test_cli_reports_silence_without_selecting_a_service(self):
        args = SimpleNamespace(file=self.path, language=[], json=True)
        stderr = io.StringIO()
        def pieces(_):
            yield b"\x00\x00" * 80
        with patch("transcription_pieces.audio_pieces", pieces), patch("jardctl.transcription_service") as start, \
                patch("sys.stderr", stderr), patch("sys.stdout", io.StringIO()) as out:
            with self.assertRaises(SystemExit) as result:
                jardctl.transcribe_file(self.root, args)
        self.assertEqual(result.exception.code, 4)
        self.assertIn('"code": "no-speech"', stderr.getvalue())
        self.assertEqual(out.getvalue(), "")
        start.assert_not_called()
