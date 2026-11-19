"""Find short exchanges where dropping a reply or a condition can reverse the meaning."""
from collections import Counter
import math
import re

from place_hints import display_place, place_hints

WORDS = re.compile(r"[^\W\d_]+")
NEGATIVE = re.compile(r"\b(?:don't|do not|cannot|shouldn't|never|لا|مش|ليس|לא|אין|nunca|jamais|nicht)\b", re.I)
CONDITION = re.compile(r"\b(?:if|depending|provided|unless|حسب|إذا|لو|תלוי|אם|según|dépend|falls)\b", re.I)
PERIOD = re.compile(r"\b(?:days?|weeks?|months?|hours?|شهور|شهر\w*|أسبوع\w*|اسبوع\w*|أيام|يوم\w*|"
                    r"שבוע\w*|חודש\w*|ימים|semana\w*|semaine\w*|wochen?)\b", re.I)


POSITION = re.compile(r"\b(?:should|must|have to|ought to|need to|instead|rather|actually|"
                      r"لازم|يجب|بالعكس|צריך|חייב|דווקא|אלא|debería|debe|il faut|muss)\b", re.I)
PROPOSAL = re.compile(r"\b(?:we (?:could|can|might|should)|let['’]s|maybe|perhaps|ممكن|نقدر|يمكن|"
                      r"אולי|נוכל|אפשר|podríamos|pourrions|könnten)\b", re.I)
RESPONSE = re.compile(r"(?:^|[.!?؟]\s*)(?:yes|no|agreed|approved|confirmed|sounds good|let['’]s|"
                      r"نعم|لا|موافق|اتفقنا|أوافق|כן|לא|אישרנו|מאושר|מסכים|סיכמנו)\b", re.I)

# An answer may stand alone ("Agreed") or refer back without repeating a noun.
SHORT_REPLY = re.compile(r"^(?:yes|no|agreed|approved|confirmed|sounds good|not yet|"
                         r"yes[, ]+let['’]s (?:do|book|try) (?:it|that)|"
                         r"نعم|لا|موافق|اتفقنا|أوافق|כן|לא|אישרנו|מאושר|מסכים|סיכמנו)[.!?؟ ]*$", re.I)


def find_exchanges(rows):
    hints = place_hints(rows)
    texts = [re.sub(r"^Speaker [A-Z]+:\s*", "", row["text"]).strip() for row in rows]
    tokens = [WORDS.findall(s.casefold()) for s in texts]
    counts = Counter(w for line in tokens for w in set(line))
    candidates = []
    for i, (row, line) in enumerate(zip(rows, tokens)):
        if len(set(line)) < 5:
            continue
        # Only nearby continuations of the same subject belong to this exchange.
        end = i
        for j in range(i + 1, min(i + 3, len(rows))):
            shared = set(tokens[j]) & set(tokens[j - 1])
            if rows[j]["start"] - rows[i]["start"] > 20 or len(shared) < 2:
                break
            end = j
        hint = hints.get(row["id"])
        if CONDITION.search(texts[i]) and (PERIOD.search(texts[i]) or hint):
            kind = "conditional"
            end = i
            if i + 1 < len(rows) and rows[i+1]["start"] - row["start"] <= 20:
                reply = texts[i+1]
                shared_subject = any(len(w) >= 4 for w in set(line) & set(tokens[i+1]))
                if RESPONSE.search(reply) and (shared_subject or SHORT_REPLY.fullmatch(reply)):
                    end = i + 1
        elif NEGATIVE.search(texts[i]) and any(
                POSITION.search(texts[j]) and not NEGATIVE.search(texts[j]) for j in range(i + 1, end + 1)):
            kind = "contrast"
        else:
            continue
        pairs = Counter(zip(line, line[1:]))
        nearby = {pair for words in tokens[max(0, i-1):i] + tokens[i+1:i+2] for pair in zip(words, words[1:])}
        repeated = [(a,b) for (a,b), n in pairs.items() if a != b and min(len(a), len(b)) > 1 and (n > 1 or (a,b) in nearby)]
        salience = max((math.log1p(len(rows)/(1+counts[a])) + math.log1p(len(rows)/(1+counts[b]))
                        for a,b in repeated), default=0.) * (len(set(line))/len(line))**2
        if not (salience or hint or kind == "conditional" and PROPOSAL.search(texts[i])):
            continue
        if sum(len(text) for text in texts[i:end+1]) > 900:
            # Long legacy blocks stay in the full source. Never force the decoder
            # to echo an unbounded noisy block as a supporting quotation.
            continue
        candidates.append((bool(hint), salience, i, end, kind, hint))
    chosen = {}
    for _, _, i, end, kind, hint in sorted(candidates, reverse=True):
        if kind in chosen:
            continue
        chosen[kind] = {"kind": kind, "id": rows[i]["id"], "start": rows[i]["start"],
                        "statements": texts[i:end+1], "place": hint}
    return sorted(chosen.values(), key=lambda item: item["start"])


def wording(item, locale):
    """Constrain only the sensitive exchange. All other sections remain free-form.
    
    The model can shorten the quotations by choosing a compact or full reading.
    It cannot change who accepted a proposal or invent a new term for either side.
    """
    lang = locale.split("-")[0]
    frames = {
        "ar": ("ورد في النقاش «{}»، ثم «{}»", "طُرح الأمر بصيغة مشروطة: «{}»",
               "طُرحت زيارة محتملة، وقد تكون الوجهة {}. ورد في الحديث: «{}»", " وجاء الرد «{}»"),
        "en": ("The discussion put “{}” alongside “{}”", "A speaker proposed “{}”",
               "A possible destination was {}. The speaker said “{}”", " The reply was “{}”"),
        "he": ('בדיון נאמר ״{}״ ובהמשך ״{}״', 'האפשרות הועלתה בתנאי: ״{}״',
               'עלה רעיון לביקור, וייתכן שהיעד הוא {}. נאמר: ״{}״', ' התשובה הייתה ״{}״'),
    }
    # Other output languages retain the exact evidence and let the model translate
    # the discussion in prose. No English sentence is inserted into that language.
    contrast, conditional, travel, reply = frames.get(lang, ("«{}» / «{}»", "«{}»", "≈ {} · «{}»", " → «{}»"))
    lines = item["statements"]
    if item["kind"] == "contrast":
        distinct = []
        for line in lines[:-1]:
            for clause in re.split(r"(?<=[.!?؟。！？])\s+", line):
                if not distinct or clause.casefold() != distinct[-1].casefold():
                    distinct.append(clause)
        last = lines[-1].strip()
        # A shorter quote may drop scene-setting at the beginning; keep the full
        # response to the negation, so the opposite position survives compression.
        opening = next((i for i, clause in enumerate(distinct) if NEGATIVE.search(clause)), 0)
        return list(dict.fromkeys(contrast.format(" ".join(parts), last)
                                  for parts in (distinct[opening:], distinct) if parts))
    source = lines[0]
    hint = item["place"]
    if hint:
        place = display_place(hint["possiblePlace"], locale, hint["asHeard"])
        note = travel.format(place, source)
    else:
        note = conditional.format(source)
    if len(lines) > 1:
        note += reply.format(" ".join(lines[1:]))
    return [note]


def phrase_options(rows):
    """Find precise, code-switched expressions that can anchor noisy multilingual speech."""
    from unicodedata import name

    def script(word):
        return name(word[0], "").partition(" ")[0]

    common = set(("a an the at as in into on of for some any this that these those which with is "
                  "was are were am has have to be it's it my me we our you your i do does would could "
                  "not and what when where how who they their them few something already").split())
    passages = [re.sub(r"^Speaker [A-Z]+:\s*", "", row["text"]) for row in rows]
    scripts = Counter(script(word) for text in passages for word in WORDS.findall(text))
    if len(scripts) < 2 or rows[-1]["start"] <= 0:
        return []
    primary = scripts.most_common(1)[0][0]
    groups = [[] for _ in range(6)]
    for row, text in zip(rows, passages):
        run = []
        previous = 0
        def add():
            if len(run) < 2 or len(run) > 14:
                return
            words = [m.group().casefold() for m in run]
            content = set(words) - common
            if len(content) < 2 or len(content) < len(words) * .5:
                return
            term = text[run[0].start():run[-1].end()]
            if len(term) < 8:
                return
            group = min(len(groups)-1, int(row["start"] / rows[-1]["start"] * len(groups)))
            groups[group].append((term, row["id"], len(content)/len(words)**.4))
        for match in re.finditer(r"[^\W\d_]+(?:['’][^\W\d_]+)*", text):
            current = script(match.group())
            if run and (current != script(run[-1].group()) or text[previous:match.start()].strip(" -")):
                add()
                run = []
            if current != primary:
                run.append(match)
            previous = match.end()
        add()
    result = []
    # An isolated interjection in a third language is weak evidence of a recurring topic.
    spans = Counter(alphabet for group in groups for alphabet in {script(t) for t, _, _ in group})
    for group in groups:
        group = [x for x in group if spans[script(x[0])] >= 2]
        frequency = Counter(term.casefold() for term, _, _ in group)
        ranked = sorted(group, key=lambda x: x[2]*(1 + .6*min(frequency[x[0].casefold()]-1, 3)), reverse=True)
        choices = {}
        best = max((score*(1 + .6*min(frequency[t.casefold()]-1, 3)) for t, _, score in ranked), default=0)
        for term, source, score in ranked:
            if score*(1 + .6*min(frequency[term.casefold()]-1, 3)) < .85*best:
                break
            if term.casefold() not in choices:
                choices[term.casefold()] = (term, source)
        if choices:
            result.append(list(choices.values())[:4])
    return result if len(result) >= 3 else []



def topic_sentence(labels, locale):
    """Join the writer's labels without making a new claim about an outcome."""
    if not labels:
        return ""
    lang = locale.split("-")[0]
    formats = {"ar": ("تناول الحديث ", "، ", "، و"), "he": ("השיחה עסקה בנושאים הבאים: ", ", ", " ו"),
               "en": ("The discussion covered ", ", ", " and ")}
    if lang not in formats:
        return " · ".join(labels)
    opening, separator, final = formats[lang]
    items = separator.join(labels[:-1]) + final + labels[-1] if len(labels) > 1 else labels[0]
    return opening + items + "."
