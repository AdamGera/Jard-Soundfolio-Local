"""Local, grounded writing and chat over the immutable timestamped transcript."""
import asyncio
import json
import hashlib
import logging
import re
import unicodedata
from contextlib import asynccontextmanager
from pathlib import Path
from inspect import getsource

import httpx

import chat_context
import local_qwen
import note_stream
import session_notes
import title_words
import writing_cache
from local_qwen import LocalQwen, source_messages
from session_notes import LANGUAGES, passages, publish

# Invalidate complete artifacts and in-flight checkpoints when their implementation changes.
# Individual successful model requests use exact request-based keys in LocalQwen.structured.
PIPELINE_REVISION = hashlib.sha256(
    Path(__file__).read_bytes() + Path(local_qwen.__file__).read_bytes() + Path(title_words.__file__).read_bytes() +
    json.dumps(LANGUAGES, sort_keys=True).encode() + session_notes.SOURCE_FORMAT_REVISION.encode() + local_qwen.MODEL_SHA.encode() + local_qwen.BIN_SHA.encode()
).hexdigest()

LOG = logging.getLogger("uvicorn.error")
STRING = {"type": "string"}
INTEGER = {"type": "integer"}
# window_notes uses nonnegative indices for chronological transcript windows.
BRIEF, TITLE, SUMMARY, CHAPTERS = -1, -2, -3, -4
CITATION = re.compile(r"\[P(\d+)\]")


def obj(**properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def array(items, limit):
    return {"type": "array", "items": items, "maxItems": limit}


TOPIC = obj(quote=STRING, id=INTEGER, label=STRING, detail=STRING)
MAP = obj(summary=STRING, topics=array(TOPIC, 4))
SUMMARY_SCHEMA = obj(lead={"type": "string", "maxLength": 340}, markdown={"type": "string", "maxLength": 1750})
IDENTIFY = (
    "Read this chronological source. Extract specific factual developments, including names, numbers, "
    "conditions and final decisions. Write two compact sentences of summary and at most four topics. "
    "Give each topic a short label, one sentence of detail, and the integer ID of its earliest supporting passage. "
    "For quote, copy 4–10 consecutive words from that passage verbatim, in the original spoken language. "
    "Combine adjacent passages on the same subject. Use only facts explicitly present in the source."
)


def excerpts(rows):
    return "\n".join(f"[P{r['id']}] ({r['start']:.1f}s) {r['text']}" for r in rows)


def clean(value):
    if isinstance(value, str):
        value = re.sub(r"[ \t]*\[P\d+(?:\s*[,;]\s*P?\d+)*\]", "", value)
        value = re.sub(r"[ \t]*\(\s*\)", "", value)
        value = re.sub(r"[ \t]+([.,;!?])", r"\1", value).strip()
    if not isinstance(value, str) or not value:
        raise ValueError("The model returned an empty writing field.")
    return value


def markdown_summary(value, rows=(), locale=""):
    # Accept old checkpoints as well as the writer's free-form Markdown.
    raw = value.get("markdown")
    if raw is None:
        raw = "\n\n".join("## " + s["heading"] + "\n\n" + "\n".join("- " + p for p in s["points"])
                            for s in value.get("sections", []) if s["points"])
    seen = set()

    def distinct(text):
        fresh = []
        for sentence in re.split(r"(?<=[.!?؟。！？])\s+", text.strip()):
            if not sentence:
                continue
            sentence = ground_quantities(sentence, rows, locale) if rows else sentence
            normalized = unicodedata.normalize("NFKD", sentence.casefold())
            plain = "".join(c for c in normalized if not unicodedata.combining(c))
            key = " ".join(re.findall(r"[^\W_]+", plain))
            if key and key not in seen:
                seen.add(key)
                fresh.append(sentence)
        return " ".join(fresh)

    lead = distinct(clean(value["lead"]))
    lines = []
    pending = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            if lines and lines[-1]:
                lines.append("")
            continue
        if re.match(r"^#{1,6}\s", line):
            pending = [clean(line), ""]
            continue
        prefix = re.match(r"^(?:[-*+] |\d+[.)] )", line)
        marker = prefix[0] if prefix else ""
        body = distinct(clean(line[len(marker):])) if line[len(marker):].strip() else ""
        if body:
            lines.extend([*pending, marker + body])
            pending = []
    return {"summary": lead, "markdown": "\n".join(lines).strip()}


def anchored_topics(topics, rows, *, include_quote=False):
    # The 2B model can enumerate its chapters 1,2,3 even when the supporting IDs are 1,7,18.
    # Resolve the source quote first. Retain its given ID when the quote is ambiguous or unavailable.
    def normalized(text):
        return " ".join(re.findall(r"\w+", text.casefold()))

    anchors = {r["id"]: r["start"] for r in rows}
    searchable = [(r["id"], normalized(r.get("text", ""))) for r in rows]
    unique = {}
    for t in topics:
        quote = t.get("quote", "").strip()
        needle = normalized(quote)
        claimed = t["id"]
        matches = [i for i, content in searchable if len(needle) >= 8 and needle in content]
        source = claimed if claimed in matches or not matches else min(matches, key=lambda i: abs(i - claimed))
        if source not in anchors or source in unique:
            continue
        item = {"id": source, "start": anchors[source], "label": clean(t["label"]), "detail": clean(t["detail"])}
        if include_quote and matches:
            item["quote"] = quote[:250]
        unique[source] = item
    return [unique[i] for i in sorted(unique)]


def validate_checkpoint(part, value):
    if part == TITLE:
        title = clean(value["title"])
        if len(title_words.word_ends(title, "und")) > 5:
            raise ValueError("Title exceeds five words.")
    elif part == SUMMARY:
        markdown_summary(value)
    elif part == BRIEF:
        clean(value["understanding"])
        clean(value["writing_brief"])
    elif part == CHAPTERS:
        if not value or sorted({t["id"] for t in value}) != [t["id"] for t in value]:
            raise ValueError("Invalid chapter order.")
        for t in value:
            clean(t["label"])
            clean(t["detail"])
    else:
        clean(value["summary"])
        if not isinstance(value["topics"], list):
            raise ValueError("Invalid window summary.")


SCRIPT_GROUPS = [
    ({"ar","ars","apc","arz","ary","acm","acq","aeb","afb","arq","apd","ayl","fa","ur","prs"}, r"[؀-ۿ]"),
    ({"he","ydd"}, r"[֐-׿]"), ({"ja","zh-Hans","zh-Hant","yue"}, r"[぀-ヿ㐀-鿿]"),
    ({"ko"}, r"[가-힯]"), ({"ru","uk","bg","be","mk"}, r"[Ѐ-ԯ]"),
    ({"hi","mr","ne"}, r"[ऀ-ॿ]"), ({"th"}, r"[฀-๿]"), ({"en"}, r"[A-Za-z]"),
]


def validate_language(fields, target):
    language = LANGUAGES[target]
    script = next((pattern for codes, pattern in SCRIPT_GROUPS if target in codes), None)
    if script:
        for field in fields:
            letters = [c for c in clean(field) if c.isalpha()]
            if len(letters) >= 10 and sum(bool(re.match(script, c)) for c in letters) < .25 * len(letters):
                raise ValueError(f"Write every heading and sentence in {language}, using its own script. "
                                 f"The JSON property names are English, but the text values must be {language}.")


def validate_translation(fields, result, target):
    validate_language(result, target)
    for source, translated in zip(fields, result, strict=True):
        if len(source) >= 30 and source.casefold() == translated.casefold():
            raise ValueError(f"Translate the entire passage into {LANGUAGES[target]} instead of copying it.")


SMALL_NUMBERS = dict(zip(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split(),
    map(str, range(21)), strict=True))


def ground_quantities(point, rows, locale):
    """Retain an original quantified sentence when the note changes its number but copies its wording."""
    def parts(text):
        words = re.findall(r"[^\W_]+", text.casefold())
        return [SMALL_NUMBERS.get(w, w) if locale == "en" else w for w in words]

    def quantities(tokens):
        return {w for w in tokens if w.isdecimal()}

    words = parts(point)
    figures = quantities(words)
    if not figures:
        return point
    phrases = {tuple(words[i:i+4]) for i in range(len(words)-3)}
    candidates = []
    for row in rows:
        text = re.sub(r"^Speaker [^:]{1,30}:\s*", "", row["text"])
        for sentence in re.split(r"(?<=[.!?؟。])\s+", text):
            source = parts(sentence)
            numbers = quantities(source)
            if not numbers:
                continue
            overlap = sum(tuple(source[i:i+4]) in phrases for i in range(len(source)-3))
            if overlap:
                candidates.append((overlap, sentence, numbers))
    if candidates:
        best = max(score for score, _, _ in candidates)
        matches = [(sentence, numbers) for score, sentence, numbers in candidates if score == best]
        if len(matches) == 1 and figures.isdisjoint(matches[0][1]):
            return matches[0][0]
    return point


class ObsoleteGeneration(Exception):
    """The recording was deleted or finalized again while the model was running."""


def note_identity(db, key, expected=None):
    row = db.execute("SELECT source_digest,revision FROM notes WHERE id=?", (key,)).fetchone()
    if row is None or expected is not None and tuple(row) != expected[:2]:
        raise ObsoleteGeneration
    if expected and len(expected) == 4:
        generation = db.execute("SELECT generation FROM overviews WHERE id=? AND lang=?", (key, expected[2])).fetchone()
        if not generation or generation[0] != expected[3]:
            raise ObsoleteGeneration
    return tuple(row)


class Insights:
    def __init__(self, store):
        self.store = store
        self.model = LocalQwen()
        self.previews = note_stream.Previews()
        self.wake = asyncio.Event()
        self.wake.set()
        self.chat_count = 0
        self.active_job = None
        self.worker = asyncio.create_task(self.run())

    def next_job(self):
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            outdated = db.execute("""SELECT DISTINCT n.id FROM notes n JOIN overviews v ON v.id=n.id
                                  WHERE v.status='queued' AND n.revision IS NOT ?""", (PIPELINE_REVISION,)).fetchall()
            for item in outdated:
                session_notes.initialize(db, item[0])
            row = db.execute("""SELECT v.id,v.lang,n.base,n.source_digest,n.revision,v.generation FROM overviews v JOIN notes n ON n.id=v.id
                WHERE v.status='queued' AND (v.lang=n.base OR EXISTS(SELECT 1 FROM overviews b
                WHERE b.id=v.id AND b.lang=n.base AND b.status='ready'))
                ORDER BY (v.lang=n.base) DESC,v.rowid LIMIT 1""").fetchone()
            return (row[0], row[1], row[2], (row[3], row[4], row[1], row[5])) if row else None

    async def preview_job(self, job):
        with self.previews.track(job[0], job[1]):
            await self.overview(*job)

    async def run(self):
        while True:
            await self.wake.wait()
            self.wake.clear()
            while not self.chat_count and (job := self.next_job()):
                with writing_cache.scope(self.store, job[0], fresh=bool(job[3][3])):
                    self.active_job = asyncio.create_task(self.preview_job(job))
                try:
                    await self.active_job
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                except ObsoleteGeneration:
                    continue
                except Exception as exc:
                    LOG.warning("Overview generation failed for %s (%s)", job[0], type(exc).__name__)
                    reason = str(exc) if isinstance(exc, RuntimeError) else "Overview generation failed. Try again."
                    with self.store.connection() as db:
                        db.execute("BEGIN IMMEDIATE")
                        try:
                            note_identity(db, job[0], job[3])
                        except ObsoleteGeneration:
                            continue
                        db.execute("UPDATE overviews SET status='error', error=? WHERE id=? AND lang=?",
                                   (reason, *job[:2]))
                        if job[1] == job[2]:
                            db.execute("UPDATE overviews SET status='blocked',error=? WHERE id=? AND status='queued'",
                                       (reason, job[0]))
                finally:
                    self.active_job = None

    @asynccontextmanager
    async def chat_priority(self):
        self.chat_count += 1
        if self.active_job:
            self.active_job.cancel()
        try:
            async with self.model.lock:
                await self.model.ready()
                yield
        finally:
            self.chat_count -= 1
            self.wake.set()

    async def close(self):
        self.worker.cancel()
        await asyncio.gather(self.worker, return_exceptions=True)
        await self.model.close()

    async def checkpoint(self, key, part, generate, expected):
        with self.store.connection() as db:
            db.execute("BEGIN")
            note_identity(db, key, expected)
            saved = db.execute("SELECT data FROM window_notes WHERE id=? AND part=?", (key, part)).fetchone()
        if saved:
            try:
                value = json.loads(saved[0])
                validate_checkpoint(part, value)
                return value
            except (KeyError, ValueError, TypeError):
                with self.store.connection() as db:
                    db.execute("BEGIN IMMEDIATE")
                    note_identity(db, key, expected)
                    db.execute("DELETE FROM window_notes WHERE id=? AND part=?", (key, part))
        value = await generate()
        validate_checkpoint(part, value)
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            note_identity(db, key, expected)
            db.execute("INSERT INTO window_notes(id,part,data) VALUES(?,?,?)", (key, part, json.dumps(value, ensure_ascii=False)))
        return value

    async def groups(self, rows, render, budget):
        """Size each contiguous group using the same tokenizer that runs inference."""
        start = 0
        while start < len(rows):
            end = min(start + 64, len(rows))
            while await self.model.tokens(render(rows[start:end])) > budget:
                if end == start + 1:
                    raise ValueError("A transcript passage exceeds the local model context.")
                end = start + (end - start) // 2
            yield rows[start:end]
            start = end

    async def outline(self, key, rows, expected, windows=None):
        if windows is None:
            windows = [part async for part in self.groups(rows, excerpts, 3600)]
        if len(windows) <= 1:
            return windows, []
        notes = []
        for i, part in enumerate(windows):
            async def describe():
                data = await self.model.structured(IDENTIFY, excerpts(part), MAP, 550)
                data["topics"] = anchored_topics(data["topics"], part, include_quote=True)
                return data

            note = await self.checkpoint(key, i, describe, expected)
            notes.append({"window": i, "first": part[0]["id"], "last": part[-1]["id"], **note})
        return windows, notes

    async def condensed(self, notes, rows):
        """Consolidate very long outlines in chronological batches, retaining source IDs."""
        render = lambda items: json.dumps(items, ensure_ascii=False, separators=(",", ":"))
        current = notes
        while current and await self.model.tokens(render(current)) > 4100:
            merged = []
            async for group in self.groups(current, render, 3600):
                data = await self.model.structured(IDENTIFY, group, MAP, 500)
                anchors = {t["id"] for n in group for t in n["topics"]}
                anchors.update(n["first"] for n in group)
                data["topics"] = anchored_topics(data["topics"], [r for r in rows if r["id"] in anchors], include_quote=True)
                merged.append({"first": group[0]["first"], "last": group[-1]["last"], **data})
            current = merged
        return current

    async def short_title(self, value, language):
        locale = next(code for code, name in LANGUAGES.items() if name == language)
        title = title_words.title_text(clean(value))
        if len(title_words.word_ends(title, locale)) > 5:
            retry = await self.model.structured(
                f"Shorten this session title in {language}. Name only the specific main subject. "
                "Use one to four words, preferably two or three. No subtitle, label, quotation marks or explanation.",
                title, obj(shortTitle=STRING), 45, on_partial=lambda v: note_stream.patch(title=v.get("shortTitle", "")))
            title = title_words.title_text(clean(retry["shortTitle"]))
        return title_words.compact_title(title, locale)

    async def short_summary(self, rows, evidence, language, brief, locale):
        # For a handful of sentences, a verbatim key point is shorter and more reliable than a paraphrase.
        # The editor still selects the important facts, their order and their heading.
        source = {r["id"]: re.sub(r"^Speaker [^:]{1,30}:\s*", "", r["text"]).strip() for r in rows}
        schema = obj(mainPassage=INTEGER, heading=STRING, supportingPassages=array(INTEGER, 4))

        def check(selection):
            if selection["mainPassage"] not in source or any(i not in source for i in selection["supportingPassages"]):
                raise ValueError("Choose only passage IDs present in the source.")
            validate_language([selection["heading"]], locale)

        def partial(v):
            main = source.get(v.get("mainPassage"), "")
            lines = [source[i] for i in dict.fromkeys(v.get("supportingPassages", [])) if i in source and source[i] != main]
            body = "\n".join("- " + line for line in lines)
            heading = v.get("heading")
            note_stream.patch(summary=main, markdown=(f"## {heading}\n\n" if heading and body else "") + body)

        edit = await self.model.structured(
            f"Edit a very short recording into a concise note in {language}. Select the one passage containing "
            "the most important final decision or finding as the lead. Select only the supporting passages "
            "needed to understand it, in the best reading order. Give those details a short descriptive heading. "
            "Use the actual integer passage IDs from the recording.", evidence, schema, 120, context=brief, validate=check, on_partial=partial)
        main = edit["mainPassage"]
        support = [source[i] for i in dict.fromkeys(edit["supportingPassages"]) if i != main]
        return {"lead": source[main], "sections": [{"heading": edit["heading"], "points": support}] if support else []}

    async def write(self, key, rows, evidence, language, expected):
        # The brief is generated from the recording. It determines what should be emphasized in the note.
        locale = next(code for code, name in LANGUAGES.items() if name == language)
        brief = await self.checkpoint(key, BRIEF, lambda: self.model.structured(
            f"Write the understanding and instructions in {language}. Identify what this recording actually is, its subject and outcome. Then write your own short "
            "editorial brief telling a writer which distinct, useful facts matter for this particular recording. "
            "Repeated mentions are one point. For a narrow subject, a short answer is enough. "
            "Use one sentence for the understanding and two short sentences for the instructions. Preserve uncertainty. Never assume this was a meeting or invent tasks, agreements or participants.",
            evidence, obj(understanding={"type": "string", "maxLength": 180},
                          writing_brief={"type": "string", "maxLength": 270}), 480), expected)
        async def name_session():
            result = await self.model.structured(
                f"Name this recording in {language}. Use the shortest specific title. Aim for one to four words. Five words is the absolute maximum. "
                "Give its specific subject as a plain, natural title. Omit generic labels, quotation marks and subtitles.",
                evidence, obj(title=STRING), 65, context=brief, validate=lambda v: validate_language([v["title"]], locale),
                on_partial=lambda v: note_stream.patch(title=v.get("title", "")))
            return {"title": await self.short_title(result["title"], language)}

        title = await self.checkpoint(key, TITLE, name_session, expected)
        note_stream.patch(title=title["title"])

        async def draft():
            if 1 < len(rows) <= 5 and len(excerpts(rows)) <= 650:
                return await self.short_summary(rows, evidence, language, brief, locale)
            return await self.model.structured(
                f"Write a useful, direct note in {language}, as if answering someone who asked what the "
                "recording says. Put the main point in one short lead. Write the remaining distinct details "
                "as natural Markdown: a paragraph often suffices. Add bullets or a heading only when they "
                "help the reader. Report each idea once in the whole note, even if several speakers or source "
                "windows repeat it. Stop when you run out of new information. If the lead says everything, "
                "return that same sentence as markdown. Ground every claim in the evidence. Keep quantities "
                "attached to the right objects, distinguish proposals from decisions, and preserve uncertainty "
                "and conditions. Omit passage numbers. Use the editorial brief to choose what matters.",
                evidence, SUMMARY_SCHEMA, 1150, context=brief,
                validate=lambda v: validate_language([v["lead"], v["markdown"]], locale),
                on_partial=lambda v: note_stream.patch(summary=v.get("lead", ""), markdown=v.get("markdown", "")))

        written = await self.checkpoint(key, SUMMARY, draft, expected)
        summary = markdown_summary(written, rows, locale)
        note_stream.patch(**summary)
        topics = await self.checkpoint(key, CHAPTERS, lambda: self.timeline(rows, evidence, language, title["title"], summary["summary"]), expected)
        note_stream.patch(topics=topics)
        return {"title": clean(title["title"]), **summary, "topics": topics}

    async def timeline(self, rows, evidence, language, title, lead):
        locale = next(code for code, name in LANGUAGES.items() if name == language)

        def check_chapters(chapters):
            validate_language([s for t in chapters for s in (t["label"], t["detail"])], locale)

        spans = [[]]
        for row in rows:
            if spans[-1] and row["start"] - spans[-1][-1]["start"] >= 180:
                spans.append([])
            spans[-1].append(row)

        def provisional(t):
            # An inferred passage ID stays private until we have resolved the complete source quote.
            return {k: t[k] for k in ("label", "detail") if isinstance(t.get(k), str)}

        async def chapters(part, source, previous):
            generated = await self.model.structured(
                f"Create a chronological topic timeline in {language}. Begin a chapter when the discussion "
                "changes subject, including when an earlier subject resumes. Work through the passage IDs in order. "
                "Describe only the adjacent passages BEFORE the next topic starts. In quote copy a short exact "
                "phrase from the chapter's first passage, in the original spoken language. Give its actual integer "
                "passage ID, a descriptive name for the subject (never Topic 1 or similar), and one specific sentence of detail. Use one chapter for a single "
                "subject and up to twelve for long sessions.",
                source, obj(topics=array(TOPIC, 12)), 1250, context={"firstPassage": part[0]["id"]},
                validate=lambda v: check_chapters(v["topics"]),
                on_partial=lambda v: note_stream.patch(topics=[*previous, *(
                    provisional(t) for t in v.get("topics", []) if isinstance(t, dict) and t.get("label")
                )]))
            result = anchored_topics(generated["topics"], part)
            if not result:
                # Keep the fallback within the span as well.
                result = [{"label": title, "detail": lead if len(spans) == 1 else part[0]["text"][:200]}]
            result[0].update(id=part[0]["id"], start=part[0]["start"])
            return result

        if len(spans) == 1:
            return await self.chapter_details(await chapters(rows, evidence, []), rows, locale)
        # In a recording with long silent intervals, a later decision must not be attached to the opening.
        # Ask the writer to describe each sparse interval independently. Dense intervals still get topic detection.
        result = []
        batch = []

        async def describe_batch():
            if not batch:
                return
            labels = {f"section{part[0]['id']}": part for part in batch}
            spec = obj(**{k: obj(label=STRING, detail=STRING) for k in labels})
            bounds = {k: [part[0]["id"], part[-1]["id"]] for k, part in labels.items()}
            contents = {k: excerpts(part) for k, part in labels.items()}
            descriptions = await self.model.structured(
                f"Write each timeline chapter in {language}. For each section give a short label and one "
                "sentence reporting only what was said during THAT interval. Keep names, numbers, proposals and "
                "actual decisions distinct. The listed intervals are separated by silence. Never move facts "
                "between intervals. Prefer concrete details to a generic recap.",
                contents, spec, 750, context={"inclusivePassageBounds": bounds},
                validate=lambda v: check_chapters(v.values()),
                on_partial=lambda v: note_stream.patch(topics=[*result, *(
                    {"id": part[0]["id"], "start": part[0]["start"], **provisional(v[k])}
                    for k, part in labels.items() if isinstance(v.get(k), dict) and v[k].get("label")
                )]))
            for k, part in labels.items():
                description = descriptions[k]
                result.append({"id": part[0]["id"], "start": part[0]["start"],
                               "label": clean(description["label"]), "detail": clean(description["detail"])})
            batch.clear()

        for part in spans:
            if await self.model.tokens(excerpts(part)) > 650:
                await describe_batch()
                async for group in self.groups(part, excerpts, 3400):
                    result.extend(await chapters(group, excerpts(group), result))
            else:
                batch.append(part)
                if len(batch) == 5:
                    await describe_batch()
        await describe_batch()
        return await self.chapter_details(result, rows, locale)

    async def chapter_details(self, chapters, rows, locale):
        """Write descriptions from each chapter's own original passages without changing its anchors or label."""
        note_stream.patch(topics=chapters)
        work = []
        for i, chapter in enumerate(chapters):
            end = chapters[i+1]["id"] - 1 if i+1 < len(chapters) else len(rows)
            part = rows[chapter["id"]-1:end]
            if len(part) <= 1 and not (part and re.search(r"[.!?؟。]\s+\S", part[0]["text"])):
                continue
            # On long chapters keep the beginning, development and end within a bounded local context.
            sample = part
            while len(sample) > 2 and await self.model.tokens(excerpts(sample)) > 950:
                count = max(2, (len(sample)+1)//2)
                sample = [part[round(j*(len(part)-1)/(count-1))] for j in range(count)]
            work.append((chapter, excerpts(sample)))

        for i in range(0, len(work), 3):
            batch = {f"chapter{c['id']}": (c, source) for c, source in work[i:i+3]}
            evidence = {k: {"title": c["label"], "passages": src} for k, (c, src) in batch.items()}
            try:
                descriptions = await self.model.structured(
                    f"Write the timeline description for each chapter in {LANGUAGES[locale]}. "
                    "Use one or two short, factual sentences. Explain what happened and, if these passages "
                    "say why it mattered or what happened next, state that directly. Begin each sentence with "
                    "its subject, without phrases like 'A concrete development is'. For a single point, "
                    "state just that point. Preserve names, numbers, proposals, conditions "
                    "and unresolved questions. Do not repeat the heading or import details from another chapter.",
                    evidence, obj(**{k: {"type": "string", "maxLength": 350} for k in batch}), 850,
                    validate=lambda v: validate_language(v.values(), locale),
                    on_partial=lambda v: note_stream.patch(topics=[
                        {**c, "detail": v.get(f"chapter{c['id']}", c["detail"])} for c in chapters]))
                for k, (chapter, _) in batch.items():
                    chapter["detail"] = clean(descriptions[k])
            except (ValueError, httpx.HTTPError):
                # The anchor-selection pass already supplied a grounded description for each chapter.
                LOG.info("Keeping original descriptions for %d timeline chapters.", len(batch))
        return chapters

    async def localize(self, original, language, rows):
        source_language = LANGUAGES.get(original.get("language"), "the source language")
        words = f"Translate the provided writing from {source_language} into {language}. Use fluent {language} for all output. "
        fields = {k: original[k] for k in ("title", "summary", "markdown") if original.get(k)}
        locale = next(code for code, name in LANGUAGES.items() if name == language)
        translated_keys = {"title": "translatedTitle", "summary": "translatedLead", "markdown": "translatedMarkdown"}

        def partial_note(v):
            note_stream.patch(**{k: v.get(translated_keys[k], "") for k in fields})

        def check_note(result):
            validate_translation(fields.values(), [result[translated_keys[k]] for k in fields], locale)

        translated = await self.model.structured(
            words + "Translate each source field once into its corresponding field, preserving its meaning "
            "and Markdown formatting. Add no new points or examples. Keep every name, date and quantity. "
            "Keep the title specific and short: one to four words, five at most.",
            {("sourceLead" if k == "summary" else "source"+k.title()): value for k, value in fields.items()},
            obj(**{translated_keys[k]: STRING for k in fields}), 1500, validate=check_note, on_partial=partial_note)
        version = {k: clean(translated[translated_keys[k]]) for k in fields}
        version.update(markdown_summary({"lead": version["summary"], "markdown": version.get("markdown", "")}))
        version["title"] = await self.short_title(version["title"], language)
        note_stream.patch(**version)
        version["topics"] = []
        # The model translates strings. The application owns chapter identity, order and timestamps.
        for start in range(0, len(original["topics"]), 4):
            batch = original["topics"][start:start + 4]
            strings = {f"chapter{i + 1}": {"label": t["label"], "detail": t["detail"]} for i, t in enumerate(batch)}
            schema = obj(**{k: obj(label=STRING, detail=STRING) for k in strings})
            def check_chapters(result):
                validate_translation([strings[k][field] for k in strings for field in ("label", "detail")],
                                     [result[k][field] for k in strings for field in ("label", "detail")], locale)

            result = await self.model.structured(words + "Keep each label short. Translate each complete description, preserving its specific details, conditions and outcome.",
                                                  strings, schema, 1000, validate=check_chapters,
                                                  on_partial=lambda v: note_stream.patch(topics=[*version["topics"], *(
                                                      {**t, "label": v[k]["label"], "detail": v[k].get("detail", "")}
                                                      for k, t in zip(strings, batch) if isinstance(v.get(k), dict) and v[k].get("label")
                                                  )]))
            for k, t in zip(strings, batch, strict=True):
                version["topics"].append({**t, "label": clean(result[k]["label"]), "detail": clean(result[k]["detail"])})
        note_stream.patch(topics=version["topics"])
        return version

    async def overview(self, key, target, base, expected):
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            note_identity(db, key, expected)
            if session_notes.reuse_ready(db, key, target):
                return
            rows = passages(db, key)
            parent = db.execute("SELECT body FROM overviews WHERE id=? AND lang=?", (key, base)).fetchone()
        if not rows:
            raise ValueError("No transcript passages available.")
        async with self.model.lock:
            await self.model.ready()
            code = target
            if target == "und":
                sample = [rows[i] for i in sorted({int((len(rows)-1)*j/5) for j in range(6)})]
                detected = await self.model.structured(
                    "Identify the predominant language in the recording excerpts. Select its code from the catalog.",
                    {"excerpts": excerpts(sample), "catalog": LANGUAGES}, obj(code={"type": "string", "enum": list(LANGUAGES)}), 40)
                code = detected["code"]
            if target != base:
                version = await self.localize(json.loads(parent[0]), LANGUAGES[code], rows)
            else:
                _, notes = await self.outline(key, rows, expected)
                evidence = await self.condensed(notes, rows) if notes else excerpts(rows)
                version = await self.write(key, rows, evidence, LANGUAGES[code], expected)
            with self.store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                note_identity(db, key, expected)
                publish(db, key, target, {**version, "language": code})

    async def retrieve(self, windows, notes, question, recent, displayed=None):
        # Every window (including the end) participates in routing, even when the index exceeds one prompt.
        render = lambda items: json.dumps(items, ensure_ascii=False, separators=(",", ":"))
        ranking = []
        async for group in self.groups(notes, render, 3400):
            result = await self.model.structured(
                "Select up to three window numbers most relevant to the user's question and recent conversation. "
                "Match meaning across languages. Return numbers in relevance order.",
                group, obj(windows=array(INTEGER, 3)), 75, context={"question": question, "recent": recent, "displayedNote": displayed})
            allowed = {n["window"] for n in group}
            ranking.extend(i for i in result["windows"] if i in allowed)
        if not ranking:
            ranking = [0, len(windows)-1]
        candidates = [r for i in dict.fromkeys(ranking) for r in windows[i]]
        if await self.model.tokens(excerpts(candidates)) <= 3100:
            return candidates
        # Ask for passage matches with a small neighborhood for context around each match.
        selected_ids = []
        previews = [{"id": r["id"], "preview": r["text"][:160]} for r in candidates]
        async for page in self.groups(previews, render, 3000):
            choice = await self.model.structured(
                "Select up to six passage IDs most relevant to the question. Put direct evidence first.",
                page, obj(ids=array(INTEGER, 6)), 75, context={"question": question, "recent": recent, "displayedNote": displayed})
            valid = {p["id"] for p in page}
            selected_ids.extend(i for i in choice["ids"] if i in valid)
        if not selected_ids:
            selected_ids = [candidates[0]["id"], candidates[-1]["id"]]
        by_id = {r["id"]: r for r in candidates}
        neighbors = [j for i in selected_ids for j in (i - 1, i + 1)]
        ids = dict.fromkeys(selected_ids + neighbors)
        return [by_id[i] for i in ids if i in by_id]

    async def chat_tokens(self, key, question, history, state=None):
        with self.store.connection() as db:
            db.execute("BEGIN")
            expected = note_identity(db, key)
            if state is None:
                state = chat_context.snapshot(db, key)
            if state["source"] != expected[0]:
                raise ObsoleteGeneration
        rows, windows = await chat_context.prepared_source(self, key, state, excerpts)
        if not rows:
            return
        # Keep conversational context within its own budget, retaining recent turns in chronological order.
        recent = []
        for msg in reversed(history[-10:]):
            candidate = [msg, *recent]
            if await self.model.tokens(json.dumps(candidate, ensure_ascii=False)) > 850:
                break
            recent = candidate
        if await self.model.tokens(question) > 1100:
            raise ValueError("Please shorten the question to fit the local model.")
        prefix = await chat_context.prepared_note(self.model, state)
        with self.store.connection() as db:
            note_identity(db, key, expected)
            notes = chat_context.completed_windows(db, key, windows)
        if notes:
            try:
                selected = await self.retrieve(windows, notes, question, recent, prefix)
            except (ValueError, httpx.HTTPError):
                selected = chat_context.lexical_passages(rows, question, recent, state["note"])
        elif len(windows) > 1:
            selected = chat_context.lexical_passages(rows, question, recent, state["note"])
        else:
            selected = rows
        evidence = excerpts(selected)
        digest = ""
        valid = {}
        if notes and prefix is None:
            topics = [t for n in notes for t in n["topics"]]
            digest = "\n".join(f"[P{t['id']}] {t['label']}: {t['detail']}" for t in topics)
            while topics and await self.model.tokens(digest) > 1800:
                topics = topics[::2] if len(topics) > 1 else []
                digest = "\n".join(f"[P{t['id']}] {t['label']}" for t in topics)
        extra = chat_context.additional_chapters(state["note"], prefix, selected, question)
        while extra and await self.model.tokens(chat_context.encoded(extra)) > 550:
            extra.pop()
        material = {"displayedNote": prefix} if prefix else {"sourceOutline": digest} if digest else {}
        context_size = await self.model.tokens(chat_context.encoded([material, extra]))
        while len(selected) > 1 and context_size + await self.model.tokens(evidence) > 5100:
            selected = selected[:-1]
            evidence = excerpts(selected)
        # A factual citation can resolve only when the model received the actual source passage.
        valid.update({r["id"]: r["start"] for r in selected})
        if material and len(windows) > 1:
            # The saved note is a stable prefix even when subsequent questions retrieve different passages.
            messages = source_messages(material)
            evidence_turn = {"originalTranscriptPassages": evidence}
            if extra:
                evidence_turn["additionalDisplayedChapters"] = extra
            messages.extend([{"role": "user", "content": chat_context.encoded(evidence_turn)},
                             {"role": "assistant", "content": "I have the relevant passages."}])
        else:
            if extra:
                material["additionalDisplayedChapters"] = extra
            messages = source_messages({**material, "originalTranscriptPassages": evidence})
        messages[0]["content"] += chat_context.POLICY
        original_ids = {str(r["start"]): r["id"] for r in rows}
        for msg in recent:
            content = msg["content"]
            if msg["role"] == "assistant":
                # Restore the exact tokenized citation form produced by the model on its earlier turn.
                content = re.sub(r"⟦([0-9.]+)⟧",
                                 lambda m: f"[P{original_ids[m[1]]}]" if m[1] in original_ids else "", content)
            messages.append({"role": msg["role"], "content": content})
        messages.append({"role": "user", "content": question})
        buffer = ""
        async for token in self.model.generate(messages, limit=550):
            buffer += token.replace("⟦", "").replace("⟧", "")
            partial = re.search(r"\[(?:P\d*)?$", buffer)
            end = partial.start() if partial else len(buffer)
            if end:
                yield CITATION.sub(lambda m: f"⟦{valid[int(m[1])]}⟧" if int(m[1]) in valid else "", buffer[:end])
                buffer = buffer[end:]

# Exact chat answers depend on retrieval, grounding, citations and engine behavior, independently of note styling.

STAGE_REVISIONS = {"chat": writing_cache.memo_key("transcript-chat-v1", [
    local_qwen.MODEL_SHA, local_qwen.BIN_SHA, local_qwen.REQUEST, local_qwen.RUNTIME, local_qwen.SOURCE_POLICY,
    IDENTIFY, MAP, CITATION.pattern, getsource(chat_context),
    *(getsource(f) for f in (source_messages, LocalQwen.generate, LocalQwen.structured, excerpts, anchored_topics,
                             Insights.groups, Insights.outline, Insights.condensed, Insights.retrieve, Insights.chat_tokens)),
])}
