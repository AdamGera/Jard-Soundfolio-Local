"""Count whole title words using macOS's ICU dictionaries, including for unspaced scripts."""
import ctypes
import ctypes.util
import re
from functools import lru_cache


@lru_cache(maxsize=1)
def _icu():
    name = ctypes.util.find_library("icucore")
    if not name:
        return None
    lib = ctypes.CDLL(name)
    # ICU break positions are offsets in UTF-16 code units, not Python string indices.
    lib.ubrk_open.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_int32,
                              ctypes.POINTER(ctypes.c_int32)]
    lib.ubrk_open.restype = ctypes.c_void_p
    for name in ("ubrk_next", "ubrk_getRuleStatus", "ubrk_close"):
        getattr(lib, name).argtypes = [ctypes.c_void_p]
    return lib


def title_text(value):
    edge = " *#`“”‘’\"«»「」『』"
    text = " ".join(value.split()).strip(edge)
    return re.sub(r"^(?:(?:session |meeting )?title|عنوان(?: الجلسة)?|כותרת)\s*[:：]\s*", "", text, flags=re.I).strip(edge)


def word_ends(text, locale):
    """Return the Python end offsets of lexical words. Most scripts can use the Unicode fallback."""
    lib = _icu()
    if lib is not None:
        encoded = text.encode("utf-16-le")
        status = ctypes.c_int32(0)
        iterator = lib.ubrk_open(1, locale.encode("ascii", "ignore"), encoded, len(encoded) // 2, ctypes.byref(status))
        if iterator:
            try:
                if status.value <= 0:
                    ends = []
                    while (end := lib.ubrk_next(iterator)) != -1:
                        if lib.ubrk_getRuleStatus(iterator) >= 100:
                            ends.append(len(encoded[:end * 2].decode("utf-16-le")))
                    return ends
            finally:
                lib.ubrk_close(iterator)
    return [m.end() for m in re.finditer(r"[^\W_]+(?:[’'][^\W_]+)*", text)]


def compact_title(text, locale):
    text = title_text(text)
    ends = word_ends(text, locale)
    if not ends:
        raise ValueError("The model returned a title without words.")
    if len(ends) <= 5:
        return text
    # A last-resort fallback after a separate shortening request. Always keep whole words.
    compact = text[:ends[3]]
    if locale == "en":
        compact = re.sub(r"\s+(?:and|the|for|of|with|on|at|to|a|an|in|is|are|will)$", "", compact, flags=re.I)
    return compact.strip(" -–—,:")
