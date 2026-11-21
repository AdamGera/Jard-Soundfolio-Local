"""Offline possible spellings for places mentioned in a travel context."""
from collections import defaultdict
from functools import cache
import json
from pathlib import Path
import re
import unicodedata

WORD = re.compile(r"[^\W\d_]+")
# Direct travel/visit language. Bare prepositions (in, at, في, ב) also introduce
# ordinary nouns and do not qualify an utterance for place-name matching.
TRAVEL = re.compile(
    r"\b(?:visit(?:ed|ing)?|trip|travel(?:ling|ing|ed|led)?|flying|flight|arrive[ds]?|airport|"
    r"نزور|زيارة|لزيارة|نسافر|السفر|رحلة|نطلع|نروح|المطار|"
    r"טיסה|טיול|ביקור|לבקר|נבקר|ניסע|לנסוע|לטוס)\b", re.I)
SCHEDULE = re.compile(
    r"\b(?:days?|weeks?|months?|tomorrow|next|هناك|يومين|أسبوع(?:ين)?|شهور|شهرين|بكرة|"
    r"שבוע|שבועות|שבועיים|חודש|מחר|שם)\b", re.I)


def spelling(name):
    return "".join(c for c in unicodedata.normalize("NFKD", name.casefold()) if c.isalpha())


def sound_key(name):
    # Preserve the first and last letter and intervening consonants. Long vowels
    # may be omitted, inserted or spaced differently by multilingual ASR.
    name = name.translate(str.maketrans("ظىة", "ضيه"))
    vowels = "aeiouyاوي"
    if re.search(r"[א-ת]", name):
        vowels = "וי"
    return name[:1] + "".join(c for c in name[1:-1] if c not in vowels) + name[-1:]


@cache
def place_names():
    return json.loads(Path(__file__).with_name("place_names.json").read_text(encoding="utf-8"))["names"]


def display_place(name, locale, heard=""):
    script = {"ar": "ARABIC", "he": "HEBREW"}.get(locale.split("-")[0])
    if script:
        aliases = place_names().get(name, [])
        if heard in aliases and any(unicodedata.name(c, "").startswith(script) for c in heard):
            return heard
        for alias in aliases:
            if any(unicodedata.name(c, "").startswith(script) for c in alias):
                return alias
    return name


@cache
def catalog():
    names = place_names()
    index = defaultdict(lambda: defaultdict(set))
    for name, aliases in names.items():
        for alias in [name, *aliases]:
            form = spelling(alias)
            if len(form) >= 5:
                index[sound_key(form)][name].add(form)
    return index


def place_hints(rows):
    """Map passage ID to original ASR spelling and a possible canonical English name.

    Travel cues and spelling comparisons currently cover English, Arabic and Hebrew.
    Ambiguous phonetic matches are discarded; the transcript is never changed.
    """
    result = {}
    for row in rows:
        text = re.sub(r"^Speaker [A-Z]+:\s*", "", row["text"])
        cues = list(TRAVEL.finditer(text))
        if not cues:
            continue
        words = list(WORD.finditer(text))
        scheduled = bool(SCHEDULE.search(text))
        matches = []
        for i, word in enumerate(words):
            preceding = [cue for cue in cues if cue.end() <= word.start()]
            if not preceding:
                continue
            gap = text[preceding[-1].end():word.start()]
            if re.search(r"[.!?؟;؛]", gap) or len(gap.split()) > (14 if scheduled else 3):
                continue
            for width in (1, 2, 3):
                group = words[i:i+width]
                if len(group) != width:
                    break
                phrase = text[group[0].start():group[-1].end()]
                if not all(c.isalpha() or c in " -" for c in phrase):
                    continue
                if "LATIN" in unicodedata.name(phrase[0], "") and not all(w.group()[0].isupper() for w in group):
                    continue
                form = spelling(phrase)
                # Hebrew destination prefix attaches directly to the proper noun.
                forms = (form, form[1:]) if form.startswith("ל") else (form,)
                for candidate in forms:
                    key = sound_key(candidate)
                    possibilities = catalog().get(key, {})
                    if len(key) < 4 or len(candidate) < 5 or len(possibilities) != 1:
                        continue
                    place, known = next(iter(possibilities.items()))
                    if not any(abs(len(candidate) - len(alias)) <= 2 for alias in known):
                        continue
                    matches.append((place, phrase))
        if matches and len({place for place, _ in matches}) == 1:
            place, phrase = matches[0]
            result[row["id"]] = {"asHeard": phrase, "possiblePlace": place}
    return result
