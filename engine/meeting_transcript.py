"""Timed meeting turns from the capture helper's aligned PCM tracks.

The envelope classifier is adapted from work by Christopher Langton under the MIT license. See NOTICE.
"""
import math
import re
from difflib import SequenceMatcher

import numpy as np

from native_recordings import read_wav_header

RATE = 48_000
FRAME = RATE // 50  # 20 ms per energy sample
CONTEXT = 5 * RATE
SPEAKER = {"mic": 1, "system": 2}


def read_window(tracks, start, frames):
    """Read only audio committed by both writers, including a bounded echo context."""
    context_start = max(0, (start - CONTEXT) // FRAME * FRAME)
    context_end = start + frames + CONTEXT
    data = {}
    for name, path in tracks.items():
        with path.open("rb") as stream:
            read_wav_header(stream)
            stream.seek(44 + 2 * context_start)
            data[name] = stream.read(2 * (context_end - context_start))
    available = min(len(pcm) // 2 for pcm in data.values())
    begin = start - context_start
    if available < begin + frames:
        return None
    clip = {name: pcm[2 * begin:2 * (begin + frames)] for name, pcm in data.items()}
    echo = EchoWindow(data["mic"][:available * 2], data["system"][:available * 2], context_start)
    return clip, echo


def energy(pcm):
    audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
    audio = np.pad(audio, (0, -len(audio) % FRAME)).reshape(-1, FRAME)
    return 10 * np.log10(np.mean(audio * audio, axis=1) + 1e-10)


class EchoWindow:
    def __init__(self, mic, system, start):
        self.start = start / RATE
        self.mic = energy(mic)
        remote = energy(system)
        self.remote = self.expected = None
        # Positive lag means the microphone heard the system track later.
        frames = np.arange(len(self.mic))
        padded = np.pad(remote, (38, 16), constant_values=-100)
        correlation = 0.2
        best_lag = None
        for lag in sorted(range(-15, 31), key=abs):
            aligned = padded[38 + frames - lag]
            active = aligned > -55
            if np.count_nonzero(active) < 25:
                continue
            x, y = self.mic[active], aligned[active]
            x, y = x - x.mean(), y - y.mean()
            scale = np.linalg.norm(x) * np.linalg.norm(y)
            score = float(x @ y / scale) if scale > 0 else 0.0
            if score > correlation:
                correlation, best_lag, self.remote = score, lag, aligned
        if best_lag is not None:
            self.expected = np.maximum.reduce([
                padded[38 + frames - best_lag - tail] - 1.5 * abs(tail) for tail in range(-1, 9)
            ])

    def classify(self, start, end):
        if self.remote is None:
            return "local"
        first = max(0, math.floor((start - self.start) * 50))
        last = min(len(self.mic), max(first + 1, math.ceil((end - self.start) * 50)))
        if last <= first:
            return "local"
        left, right = max(0, first - 250), min(len(self.mic), last + 250)
        floor = np.percentile(self.mic[left:right], 10)

        def gain(lo, hi, minimum):
            remote = self.remote[lo:hi]
            active = remote > -55
            if np.count_nonzero(active) < minimum:
                return None
            loud = active & (remote >= np.median(remote[active]))
            return np.percentile((self.mic[lo:hi] - self.expected[lo:hi])[loud], 35)

        sides = [value for value in (gain(left, first, 10), gain(last, right, 10)) if value is not None]
        bleed = max(sides) if sides else gain(left, right, 1)
        if bleed is None:
            return "local"
        active = self.mic[first:last] >= floor + 6
        local = active & (self.mic[first:last] > np.maximum(floor, self.expected[first:last] + bleed) + 9)
        count = np.count_nonzero(active)
        if count < 10:
            if not np.any(local) and np.count_nonzero(self.expected[first:last] > -55) * 2 >= last - first:
                return "echo"
            return "local"
        ratio = np.count_nonzero(local) / count
        if ratio >= 0.5:
            return "local"
        bursts = np.cumsum(np.r_[0, local])
        width = min(50, len(local))
        if np.max(bursts[width:] - bursts[:-width]) >= 25:
            return "local"
        return "echo" if ratio <= 0.2 else "ambiguous"


SENTENCE_END = re.compile(r'''[.?!…؟。！？]["'’”»)]*$''')


def source_turns(result, source, start, end):
    """Group aligned words into utterances without inventing word timestamps."""
    words = result["words"]
    if not words:
        return []
    groups = [[]]
    for word in words:
        if groups[-1]:
            previous = groups[-1][-1]
            pause = (previous["end"] is not None and word["start"] is not None and
                     word["start"] - previous["end"] >= 0.6)
            if pause or SENTENCE_END.search(previous["text"]):
                groups.append([])
        groups[-1].append({**word, "speaker": SPEAKER[source]})
    turns = []
    for group in groups:
        text = "".join(word["text"] + word.get("separator", " ") for word in group).strip()
        if not text:
            continue
        first = next((word["start"] for word in group if word["start"] is not None), None)
        last = next((word["end"] for word in reversed(group) if word["end"] is not None), None)
        turns.append({"speaker": SPEAKER[source], "text": text, "start": first if first is not None else start,
                      "end": last if last is not None else end, "words": group, "language": result["language"],
                      "aligned": first is not None and last is not None})
    return turns


def merge_sources(recognized, echo, start, end):
    """Suppress mic bleed before publishing the transcript and keeping timing."""
    remote = source_turns(recognized["system"], "system", start, end)
    local = source_turns(recognized["mic"], "mic", start, end)
    kept = list(remote)
    suppressed = 0
    for turn in local:
        signal = echo.classify(turn["start"], turn["end"]) if echo and turn["aligned"] else "local"
        nearby = [item["text"] for item in remote if item["end"] >= turn["start"] - 1.5
                  and item["start"] <= turn["end"] + 1.5] if signal != "local" else []
        if signal == "ambiguous" and nearby:
            words = re.findall(r"\w+", turn["text"].casefold())
            remote_words = re.findall(r"\w+", " ".join(nearby).casefold())
            match = sum(part.size for part in SequenceMatcher(None, words, remote_words, autojunk=False).get_matching_blocks())
            if words and match / len(words) >= 0.6:
                signal = "echo"
        if signal == "echo" and nearby:
            suppressed += 1
        else:
            kept.append(turn)
    kept.sort(key=lambda turn: (turn["start"], turn["speaker"]))
    return {
        "text": " ".join(turn["text"] for turn in kept),
        "language": next((turn["language"] for turn in kept if turn["language"]), None),
        "languages": list(dict.fromkeys(turn["language"] for turn in kept if turn["language"])),
        "segments": [{key: turn[key] for key in ("speaker", "text", "start", "end")} for turn in kept],
        "words": [word for turn in kept for word in turn["words"]],
        "activity": [{key: turn[key] for key in ("speaker", "start", "end")} for turn in kept if turn["aligned"]],
        "echoSuppressedUtterances": suppressed,
    }
