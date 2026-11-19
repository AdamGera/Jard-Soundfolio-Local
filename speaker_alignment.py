import json
import re
from contextlib import contextmanager
from itertools import pairwise

import numpy as np
import torch
from safetensors.torch import load_file

# Preserve exact text slices, including punctuation and languages without spaces.
WORDS = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]|[^\s\u3040-\u30ff\u3400-\u9fff]+")


def word_records(text, words, start, duration):
    """Keep untimed words and preserve the original separators for display."""
    if not words:
        words = [(match.start(), None) for match in WORDS.finditer(text)]
    bounds = [left for left, _ in words] + [len(text)]
    records = []
    for (_, timing), (left, right) in zip(words, pairwise(bounds), strict=True):
        piece = text[left:right]
        records.append({
            "text": piece.rstrip(),
            "separator": piece[len(piece.rstrip()):],
            "start": round(start + min(timing[0], duration), 3) if timing else None,
            "end": round(start + min(timing[1], duration), 3) if timing else None,
            **({} if timing else {"blockStart": start}),
        })
    return records


def ctc_path(scores, labels, blank):
    """Return the CTC state selected for each frame, or None if no path fits."""
    expanded = np.full(2 * len(labels) + 1, blank)
    expanded[1::2] = labels
    skip = np.zeros(len(expanded), dtype=bool)
    skip[3::2] = labels[1:] != labels[:-1]
    frames = len(scores)
    if not frames:
        return None

    history = np.zeros((frames, len(expanded)), dtype=np.uint8)
    previous = np.full(len(expanded), -np.inf, dtype=np.float32)
    previous[:2] = scores[0, expanded[:2]]
    for frame in range(1, frames):
        step = np.r_[-np.inf, previous[:-1]]
        jump = np.r_[-np.inf, -np.inf, previous[:-2]]
        candidates = np.stack((previous, step, np.where(skip, jump, -np.inf)))
        history[frame] = candidates.argmax(axis=0)
        previous = candidates.max(axis=0) + scores[frame, expanded]

    state = len(expanded) - 2 + int(previous[-1] >= previous[-2])
    if not np.isfinite(previous[state]):
        return None
    path = np.empty(frames, dtype=np.int32)
    for frame in range(frames - 1, -1, -1):
        path[frame] = state
        state -= int(history[frame, state])
    return path


class CtcAligner:
    def __init__(self, directory, tower, tokenizer):
        vocab = json.loads((directory / "ctc_vocab.json").read_text())
        weights = load_file(directory / "ctc_head.safetensors")
        parameter = next(tower.parameters())
        self.weight = weights["weight"].to(parameter.device, parameter.dtype)
        self.bias = weights["bias"].to(parameter.device, parameter.dtype)
        self.classes = {token: index for index, token in enumerate(vocab["token_ids"])}
        self.blank = vocab["blank"]
        self.fps = vocab["frames_per_second"]
        self.tower = tower
        self.tokenizer = tokenizer

    @contextmanager
    def capture(self):
        states = []
        hook = self.tower.ln_post.register_forward_hook(lambda _layer, _args, output: states.append(output))
        try:
            yield states
        finally:
            hook.remove()

    @torch.inference_mode()
    def words(self, text, states):
        if not text or states is None:
            return []
        encoding = self.tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
        sequence = [(self.classes[token], span) for token, span in zip(
            encoding.input_ids, encoding.offset_mapping, strict=True
        ) if token in self.classes and span[1] > span[0]]
        if not sequence or len(sequence) > len(states):
            return []

        rows = list(dict.fromkeys([self.blank, *(token for token, _ in sequence)]))
        columns = {row: index for index, row in enumerate(rows)}
        indices = torch.tensor(rows, device=self.weight.device)
        logits = torch.nn.functional.linear(
            states, self.weight.index_select(0, indices), self.bias.index_select(0, indices)
        ).float().cpu().numpy()
        labels = np.array([columns[token] for token, _ in sequence], dtype=np.int32)
        path = ctc_path(logits, labels, blank=0)
        if path is None:
            return []

        tokens = []
        for index, (_, span) in enumerate(sequence):
            frames = np.flatnonzero(path == 2 * index + 1)
            if frames.size and np.max(logits[frames, labels[index]] - logits[frames, 0]) >= -6:
                tokens.append((*span, frames[0] / self.fps, (frames[-1] + 1) / self.fps))

        words = []
        for match in WORDS.finditer(text):
            anchors = [token for token in tokens if token[1] > match.start() and token[0] < match.end()]
            timing = (float(anchors[0][2]), float(anchors[-1][3])) if anchors else None
            words.append((match.start(), timing))
        return words
