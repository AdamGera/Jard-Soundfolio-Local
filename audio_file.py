import asyncio
import json
from pathlib import Path

import numpy as np

from rumble_model import MAX_SAMPLES, SAMPLE_RATE

MAX_AUDIO_SECONDS = 3 * 60 * 60
FILE_ERROR = "This audio could not be opened. Try a WAV, MP3, or M4A file."
DURATION_ERROR = "Choose audio up to 3 hours long."
INPUT_OPTIONS = (
    "-protocol_whitelist", "file,pipe",
    "-format_whitelist", "aac,aiff,flac,matroska,webm,mov,mp3,ogg,wav",
)


async def stop_process(process: asyncio.subprocess.Process):
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.communicate()


async def audio_duration(path: Path) -> float | None:
    process = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", *INPUT_OPTIONS, "-select_streams", "a:0",
        "-show_entries", "stream=index,duration:format=duration", "-of", "json", path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await process.communicate()
    finally:
        await stop_process(process)
    if process.returncode:
        raise ValueError(FILE_ERROR)
    metadata = json.loads(output)
    if not metadata.get("streams"):
        raise ValueError(FILE_ERROR)
    stream = metadata["streams"][0]
    duration = stream.get("duration", metadata.get("format", {}).get("duration"))
    return float(duration) if duration and duration != "N/A" else None


def split_at_pauses(pcm: bytes):
    """Keep all samples and give clearly separated utterances their own ASR context."""
    audio = np.frombuffer(pcm, dtype="<f4")
    frame = SAMPLE_RATE // 50  # 20 ms
    frames = audio[:len(audio) // frame * frame].reshape(-1, frame)
    quiet = np.mean(frames * frames, axis=1) < 0.003 ** 2
    edges = np.flatnonzero(np.diff(np.pad(quiet, 1))).reshape(-1, 2)
    start = 0
    for left, right in edges:
        if left == 0 or right == len(frames) or right - left < 14:
            continue
        cut = int(left + right) * frame // 2
        if min(cut - start, len(audio) - cut) >= 3 * SAMPLE_RATE:
            yield pcm[start * 4:cut * 4]
            start = cut
    yield pcm[start * 4:]


async def audio_blocks(path: Path, *, multilingual=False):
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-nostdin", *INPUT_OPTIONS, "-i", path,
        "-map", "0:a:0", "-t", str(MAX_AUDIO_SECONDS + 1), "-ac", "1",
        "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        while True:
            try:
                block = await process.stdout.readexactly(MAX_SAMPLES * 4)
            except asyncio.IncompleteReadError as error:
                block = error.partial
            if block:
                if multilingual:
                    for part in split_at_pauses(block):
                        yield part
                else:
                    yield block
            if len(block) < MAX_SAMPLES * 4:
                break
        if await process.wait():
            raise ValueError(FILE_ERROR)
    finally:
        await stop_process(process)


async def make_playable(source: Path, target: Path):
    """Make a seekable browser copy for unsupported or unindexed media."""
    duration = await audio_duration(source)
    if duration and duration > MAX_AUDIO_SECONDS + 1:
        raise ValueError(DURATION_ERROR)
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-nostdin", *INPUT_OPTIONS, "-i", source,
        "-map", "0:a:0", "-t", str(MAX_AUDIO_SECONDS + 1), "-c:a", "aac",
        "-b:a", "96k", "-ac", "2", "-movflags", "+faststart", target,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        if await process.wait():
            raise ValueError(FILE_ERROR)
    finally:
        await stop_process(process)
