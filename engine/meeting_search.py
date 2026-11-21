"""Rank transcript and note passages and keep their original text for context."""
import math
import re
import unicodedata
from collections import Counter, defaultdict

TOKEN = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
STOP = frozenset("a about an and are as at be but by did do for from had has have he i in is it its of on or our she so that the their them there they this to was we were what when where which who with you".split())
SUFFIXES = (("ingly", 3, ""), ("edly", 3, ""), ("ings", 3, ""), ("ing", 3, ""), ("ied", 2, "y"),
            ("ies", 2, "y"), ("ed", 3, ""), ("es", 3, ""), ("ly", 3, ""), ("s", 3, ""))
SOUNDS = {char: code for code, group in enumerate(("bfpv", "cgjkqsxz", "dt", "l", "mn", "r"), 1) for char in group}


def fold(word):
    decomposed = unicodedata.normalize("NFKD", word.casefold())
    return "".join(c for c in decomposed if not unicodedata.category(c).startswith("M") and c not in "'’")


def words(text):
    return [fold(match.group()) for match in TOKEN.finditer(unicodedata.normalize("NFC", text))]


def stem(word):
    if len(word) <= 3 or not word.isascii() or not word.isalpha():
        return word
    for suffix, minimum, replacement in SUFFIXES:
        if len(word) - len(suffix) >= minimum and word.endswith(suffix):
            if suffix != "s" or not word.endswith(("ss", "us")):
                word = word[:-len(suffix)] + replacement
                break
    if len(word) >= 4 and word[-1] == word[-2] and word[-1] not in "aeioulsz":
        word = word[:-1]
    return word[:-1] if len(word) >= 4 and word.endswith("e") else word


def soundex(word):
    if len(word) < 4 or not word.isascii() or not word.isalpha():
        return None
    last = SOUNDS.get(word[0])
    digits = [word[0]]
    for char in word[1:]:
        if char in "hw":
            continue
        code = SOUNDS.get(char)
        if code is not None and code != last:
            digits.append(str(code))
        last = code
    return ("".join(digits) + "000")[:4]


def distance(left, right, limit):
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous, before = list(range(len(right) + 1)), None
    for i, a in enumerate(left, 1):
        row = [i]
        for j, b in enumerate(right, 1):
            value = min(row[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b))
            if i > 1 and j > 1 and a == right[j - 2] and left[i - 2] == b:
                value = min(value, before[j - 2] + 1)
            row.append(value)
        before, previous = previous, row
    return previous[-1]


def expansions(term, frequency, total, fuzzy):
    result = {term: 1.0}
    if not fuzzy or len(term) < 3:
        return result
    exact = frequency[term]
    common = max(5, total * 0.005)
    phonetic = soundex(term)
    for word, count in frequency.items():
        if word == term:
            continue
        if word.startswith(term):
            result[word] = 0.5
        elif len(term) >= 4 and exact < 3:
            limit = 1 if len(term) < 7 else 2
            edits = distance(term, word, limit)
            if edits <= limit and (count <= common or (not exact and edits == 1)):
                result[word] = 0.35
            elif count <= common and phonetic and phonetic == soundex(word):
                result[word] = 0.2
    return result


def snippet(text, tokens, matched):
    width = min(len(tokens), 32)
    first = min(matched)
    beginning = max(range(len(tokens) - width + 1), key=lambda i: (
        sum(j in matched for j in range(i, i + width)), -abs(max(0, first - 4) - i)))
    end = beginning + width
    position = tokens[beginning].start() if beginning else 0
    output = ["…"] if beginning else []
    for i in range(beginning, end):
        if i in matched:
            token = tokens[i]
            output += [text[position:token.start()], "**", token.group(), "**"]
            position = token.end()
    output.append(text[position:tokens[end - 1].end()] if end < len(tokens) else text[position:])
    if end < len(tokens):
        output.append("…")
    return " ".join("".join(output).split())


def meeting_passages(passages, query, limit, fuzzy, *, stem_phrases=False):
    if len(query) > 500 or query.count('"') % 2:
        raise ValueError("Use up to 500 characters and close any quoted phrases.")
    parts = query.split('"')
    phrases = [words(part) for part in parts[1::2]]
    if stem_phrases:
        phrases = [list(map(stem, phrase)) for phrase in phrases]
    unquoted = [word for part in parts[::2] for word in words(part)]
    significant = [word for word in unquoted if word not in STOP]
    terms = list(dict.fromkeys(map(stem, significant or ([] if phrases else unquoted))))
    if not (terms or phrases) or any(not phrase for phrase in phrases):
        raise ValueError("Enter words or a quoted phrase to search for.")

    corpus, length = [], 0
    documents = defaultdict(set)
    for passage in passages:
        text = unicodedata.normalize("NFC", passage["text"])
        tokens = list(TOKEN.finditer(text))
        folded = [fold(token.group()) for token in tokens]
        stems = list(map(stem, folded))
        positions = defaultdict(list)
        for index, word in enumerate(stems):
            positions[word].append(index)
        for word in positions:
            documents[passage["id"]].add(word)
        if tokens:
            length += len(tokens)
            corpus.append((passage, text, tokens, stems if stem_phrases else folded, positions))
    if not corpus:
        return []
    frequency = Counter(word for document in documents.values() for word in document)
    total, average = len(documents), length / len(corpus)
    weights = [{word: (quality, math.log1p((total - frequency[word] + 0.5) / (frequency[word] + 0.5)))
                for word, quality in expansions(term, frequency, total, fuzzy).items()} for term in terms]

    ranked = []
    for order, (passage, text, tokens, phrase_tokens, positions) in enumerate(corpus):
        matches = set()
        for phrase in phrases:
            found = {i + offset for i in range(len(phrase_tokens) - len(phrase) + 1)
                     if phrase_tokens[i:i + len(phrase)] == phrase for offset in range(len(phrase))}
            if not found:
                break
            matches.update(found)
        else:
            exact, coverage, score = 0, 0.0, 0.0
            normalization = 1.2 * (0.25 + 0.75 * len(tokens) / average)
            for candidates in weights:
                choices = []
                for word in positions.keys() & candidates.keys():
                    quality, rarity = candidates[word]
                    count = len(positions[word])
                    choices.append((quality == 1, quality * rarity * (count * 2.2) / (count + normalization), quality, word))
                if choices:
                    exact_match, weight, quality, word = max(choices)
                    exact += exact_match
                    coverage += quality
                    score += weight
                    matches.update(positions[word])
            if matches and (coverage > 0 or not terms):
                ranked.append((exact == len(terms), coverage, score, -order,
                               {**passage, "snippet": snippet(text, tokens, matches), "score": round(score, 6)}))
    ranked.sort(reverse=True)
    return [hit for *_, hit in ranked[:limit]]
