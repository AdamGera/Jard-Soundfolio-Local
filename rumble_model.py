import logging
import shutil
from pathlib import Path

MODEL_ID = "Reson8/Rumble-2-Flash"
REVISION = "92ec9141fb1178c5e77fd0020fe86a33c355060b"
MODEL_DIR = Path(__file__).resolve().parent / "model"
SAMPLE_RATE = 16_000
MAX_SAMPLES = 25 * SAMPLE_RATE
DIARIZATION_ID = "nvidia/Nemotron-3-Diarization"
DIARIZATION_REVISION = "52a432672732425e4c19765b9fec9b5810e6f4ba"
DIARIZATION_DIR = MODEL_DIR / "nemotron3"

# The model card's languages, plus Hebrew verified with this checkpoint.
LANGUAGES = {
    "af": "Afrikaans",
    "sq": "Albanian",
    "ar": "Arabic",
    "eu": "Basque",
    "bn": "Bengali",
    "bs": "Bosnian",
    "yue": "Cantonese",
    "ca": "Catalan",
    "ceb": "Cebuano",
    "zh": "Chinese",
    "hr": "Croatian",
    "cs": "Czech",
    "da": "Danish",
    "nl": "Dutch",
    "en": "English",
    "fil": "Filipino",
    "fi": "Finnish",
    "fr": "French",
    "de": "German",
    "el": "Greek",
    "he": "Hebrew",
    "hi": "Hindi",
    "hu": "Hungarian",
    "is": "Icelandic",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "lv": "Latvian",
    "lt": "Lithuanian",
    "mk": "Macedonian",
    "ms": "Malay",
    "mt": "Maltese",
    "nb": "Norwegian Bokmål",
    "fa": "Persian",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sr": "Serbian",
    "es": "Spanish",
    "sv": "Swedish",
    "th": "Thai",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "vi": "Vietnamese",
}

MODEL_FILES = (
    "LICENSE",
    "README.md",
    "added_tokens.json",
    "chat_template.jinja",
    "config.json",
    "generation_config.json",
    "merges.txt",
    "model.safetensors",
    "preprocessor_config.json",
    "processor_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
)


def prepare_assets(repo, revision, directory, names):
    from huggingface_hub import hf_hub_download, try_to_load_from_cache

    for name in names:
        target = directory / name
        if target.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        cached = try_to_load_from_cache(repo, name, revision=revision)
        if not isinstance(cached, str):
            cached = hf_hub_download(repo, name, revision=revision)
        temporary = target.with_suffix(target.suffix + ".part")
        shutil.copyfile(cached, temporary)
        temporary.replace(target)


def prepare_model():
    prepare_assets(MODEL_ID, REVISION, MODEL_DIR, MODEL_FILES)
    (MODEL_DIR / "revision").write_text(REVISION + "\n")
    prepare_assets(MODEL_ID, REVISION, MODEL_DIR, ("ctc/ctc_head.safetensors", "ctc/ctc_vocab.json"))
    prepare_assets(
        DIARIZATION_ID, DIARIZATION_REVISION, DIARIZATION_DIR,
        ("README.md", "config.json", "processor_config.json", "model.safetensors"),
    )


def language_prompt(language_codes: tuple[str, ...]) -> str | None:
    if not language_codes:
        return None
    # A processor prompt adds context to one clip. Its language option forces a
    # single output language per clip, even when a batch passes a list.
    names = ", ".join(LANGUAGES[code] for code in language_codes)
    return f"\nExpected spoken languages: {names}. Transcribe speech in its original language."


class RumbleModel:
    def __init__(self):
        if not all((MODEL_DIR / name).is_file() for name in MODEL_FILES):
            raise RuntimeError("Run Setup Jard Soundfolio Local to install the local model.")
        if (MODEL_DIR / "revision").read_text().strip() != REVISION:
            raise RuntimeError("The local checkpoint revision does not match.")

        import numpy as np
        import torch
        from transformers import AutoModelForMultimodalLM, AutoProcessor

        if not torch.backends.mps.is_available():
            raise RuntimeError("This demo requires PyTorch Metal on an Apple Silicon Mac.")
        self.np = np
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(MODEL_DIR, local_files_only=True)
        self.model = AutoModelForMultimodalLM.from_pretrained(
            MODEL_DIR,
            local_files_only=True,
            dtype=torch.bfloat16,
            attn_implementation="eager",
        ).to("mps").eval()
        self.aligner = None
        self._decode(np.zeros(SAMPLE_RATE * 2, dtype=np.float32), max_new_tokens=1)

    def enable_alignment(self):
        from speaker_alignment import CtcAligner

        self.aligner = CtcAligner(MODEL_DIR / "ctc", self.model.model.audio_tower, self.processor.tokenizer)

    def transcribe(
        self, pcm: bytes, language_codes: tuple[str, ...] = (), *, timed: bool = False,
        cache_implementation: str | None = None,
    ):
        audio = self.np.frombuffer(pcm, dtype="<f4").copy()
        if not self.np.isfinite(audio).all():
            raise ValueError("Audio contains invalid samples.")
        if self.np.max(self.np.abs(audio)) < 0.001:
            return "", None, []
        prompt = language_prompt(language_codes)
        if timed and self.aligner:
            with self.aligner.capture() as states:
                text, language = self._decode(audio, prompt=prompt, cache_implementation=cache_implementation)
            try:
                words = self.aligner.words(text, states[0] if states else None)
            except (RuntimeError, ValueError, ArithmeticError):
                logging.getLogger("uvicorn.error").warning("Word timing was unavailable for an audio block.")
                words = []
            return text, language, words
        text, language = self._decode(audio, prompt=prompt, cache_implementation=cache_implementation)
        return text, language, []

    def _decode(
        self, audio, max_new_tokens: int = 512, prompt: str | None = None,
        cache_implementation: str | None = None,
    ) -> tuple[str, str | None]:
        with self.torch.inference_mode():
            inputs = self.processor.apply_transcription_request(audio=audio, prompt=prompt)
            inputs = inputs.to(device=self.model.device, dtype=self.model.dtype)
            output = self.model.generate(
                **inputs, max_new_tokens=max_new_tokens, cache_implementation=cache_implementation,
            )
            generated = output[:, inputs["input_ids"].shape[-1]:]
            result = self.processor.decode(generated, return_format="parsed")[0]
        return result["transcription"].strip(), result["language"]
