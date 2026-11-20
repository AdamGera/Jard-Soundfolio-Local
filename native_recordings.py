"""Private draft WAV files produced by the Mac capture helper."""
import fcntl
import os
import struct
from uuid import UUID

from audio_file import MAX_AUDIO_SECONDS


def recording_marker(path):
    return path.with_suffix(".recording")


def list_drafts(folder):
    drafts = []
    for marker in folder.glob("*.recording"):
        try:
            key = str(UUID(marker.stem))
            path = folder / key
            if key != marker.stem or path.is_symlink():
                continue
            with marker.open("rb") as claim:
                fcntl.flock(claim, fcntl.LOCK_SH | fcntl.LOCK_NB)
                info = path.stat()
        except (ValueError, FileNotFoundError, BlockingIOError):
            continue
        if info.st_size > 44:
            drafts.append(dict(id=key, path=str(path), bytes=info.st_size, modified=info.st_mtime))
    return sorted(drafts, key=lambda row: row["modified"], reverse=True)


def read_wav_header(stream):
    header = stream.read(44)
    if (len(header) != 44 or header[:4] != b"RIFF" or header[8:16] != b"WAVEfmt "
            or struct.unpack_from("<IHHIIHH", header, 16) != (16, 1, 1, 48_000, 96_000, 2, 16)
            or header[36:40] != b"data"):
        raise ValueError("This draft needs a 48 kHz mono PCM recording made by Jaro.")


def recover_wav(path):
    if not recording_marker(path).is_file():
        raise ValueError("Only a Jard microphone or Mac audio recording can be recovered.")
    with path.open("r+b") as stream:
        read_wav_header(stream)
        size = stream.seek(0, os.SEEK_END)
        # A short final write can leave half a sample. Preserve every whole sample.
        pcm_size = (size - 44) // 2 * 2
        if not 0 < pcm_size <= (MAX_AUDIO_SECONDS + 1) * 96_000:
            raise ValueError("The draft is empty or exceeds the recording limit.")
        stream.truncate(pcm_size + 44)
        stream.seek(4)
        stream.write(struct.pack("<I", pcm_size + 36))
        stream.seek(40)
        stream.write(struct.pack("<I", pcm_size))
        stream.flush()
        os.fsync(stream.fileno())
