"""Immutable note snapshot and reusable, bounded material for local transcript conversations."""
import json
import re
from bisect import bisect_right

from inspect import getsource

import writing_cache
from local_qwen import MODEL_SHA, BUILD
from session_notes import SOURCE_FORMAT_REVISION, passages

POLICY = (
    " The saved overview is the note currently displayed to the user. For questions about its title, "
    "summary, headings or timeline, read the corresponding stored fields and quote exact wording when "
    "requested. The timeline is chronological and its start values are seconds into the recording. "
    "For what people actually said, treat original transcript passages as authoritative; a summary "
    "or prior chat answer may be mistaken. Cite an original passage as [P12] using its actual integer "
    "ID. Read the current note in preference to earlier chat messages about a different note."
)


def snapshot(db, key):
    row = db.execute("""SELECT n.source_digest,v.lang,v.body,v.generation,v.status FROM notes n
                    LEFT JOIN overviews v ON n.id=v.id AND v.lang=n.displayed WHERE n.id=?""", (key,)).fetchone()
    body = json.loads(row["body"]) if row["body"] else None
    # Publication of a regenerated result changes the identity even if deterministic wording is identical.
    fingerprint = writing_cache.memo_key("displayed-note-v1", [
        row["lang"], body, row["generation"], row["status"] == "ready"
    ]) if body else None
    return {"source": row["source_digest"], "note": body, "fingerprint": fingerprint}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


async def prepared_source(writer, key, state, render):
    cache_key = writing_cache.memo_key("chat-passage-windows-v1", [
        SOURCE_FORMAT_REVISION, state["source"], MODEL_SHA, BUILD, getsource(render), getsource(writer.groups.__func__)
    ])
    prepared = writing_cache.recall(cache_key)
    if prepared is None:
        with writer.store.connection() as db:
            db.execute("BEGIN")
            if db.execute("SELECT source_digest FROM notes WHERE id=?", (key,)).fetchone()[0] != state["source"]:
                raise ValueError("The transcript changed while preparing this answer.")
            rows = passages(db, key)
        stops = [part[-1]["id"] async for part in writer.groups(rows, render, 3600)]
        prepared = {"rows": rows, "stops": stops}
        writing_cache.remember(cache_key, prepared)
    rows = prepared["rows"]
    stops = prepared["stops"]
    return rows, [rows[start:end] for start, end in zip([0, *stops], stops)]


async def prepared_note(model, state):
    """Keep the exact note when it fits. A large timeline retains chronological first/last and sampled chapters."""
    if not state["note"]:
        return None
    key = writing_cache.memo_key("chat-note-prefix-v1", [state["fingerprint"], MODEL_SHA, BUILD, getsource(prepared_note)])
    prefix = writing_cache.recall(key)
    if prefix is not None:
        return prefix
    note = state["note"]
    prefix = {k: note.get(k, "") for k in ("language", "title", "summary", "markdown")}
    topics = note.get("topics", [])
    prefix.update(topics=topics, chapterCount=len(topics))
    # This budget leaves space for original passages, the user's question, prior turns and generated answer.
    if await model.tokens(encoded(prefix)) > 1750:
        prefix["topics"] = []
        markdown = prefix["markdown"]
        while markdown and await model.tokens(encoded(prefix)) > 1000:
            markdown = markdown[:int(len(markdown) * .75)]
            prefix["markdown"] = markdown + "…"
        count = min(16, len(topics))
        while count:
            indices = [round(i*(len(topics)-1)/max(1, count-1)) for i in range(count)]
            prefix["topics"] = [topics[i] for i in indices]
            if await model.tokens(encoded(prefix)) <= 1750:
                break
            count //= 2
        if not count:
            prefix["topics"] = []
    writing_cache.remember(key, prefix)
    return prefix


def additional_chapters(note, prefix, selected, question):
    """Recover omitted chapters near retrieved passages or explicitly named in the question."""
    if not note or len(prefix["topics"]) == len(note.get("topics", [])):
        return []
    topics = note["topics"]
    included = {t["id"] for t in prefix["topics"]}
    ids = [t["id"] for t in topics]
    candidates = []
    for match in re.finditer(r"\bchapter\s+(\d+)\b", question, re.I):
        ordinal = int(match[1]) - 1
        if 0 <= ordinal < len(topics):
            candidates.append(ordinal)
    terms = set(re.findall(r"\w{3,}", question.casefold()))
    scored = sorted(range(len(topics)), key=lambda i: -len(terms & set(re.findall(r"\w{3,}", topics[i]["label"].casefold()))))
    candidates.extend(i for i in scored if terms & set(re.findall(r"\w{3,}", topics[i]["label"].casefold())))
    candidates.extend(max(0, bisect_right(ids, r["id"]) - 1) for r in selected)
    return [topics[i] for i in dict.fromkeys(candidates) if topics[i]["id"] not in included][:5]


def completed_windows(db, key, windows):
    """Chat reads the existing background index; a first question never waits to generate it."""
    if len(windows) <= 1:
        return []
    records = db.execute("SELECT part,data FROM window_notes WHERE id=? AND part>=0 ORDER BY part", (key,)).fetchall()
    if len(records) != len(windows):
        return []
    notes = []
    for i, (record, part) in enumerate(zip(records, windows)):
        if record["part"] != i:
            return []
        try:
            item = json.loads(record["data"])
            if not isinstance(item["summary"], str) or not isinstance(item["topics"], list):
                return []
        except (ValueError, TypeError, KeyError):
            return []
        notes.append({"window": i, "first": part[0]["id"], "last": part[-1]["id"], **item})
    return notes


def lexical_passages(rows, question, history, note):
    """Select actual passages without inference when the recording's semantic index is unavailable."""
    from collections import Counter
    from math import log

    def words(text):
        return set(re.findall(r"[^\W_]{2,}", text.casefold()))

    query = words(question)
    previous = {w for turn in history[-2:] for w in words(turn["content"])}
    terms = [words(r["text"]) for r in rows]
    counts = Counter(w for passage in terms for w in passage & (query | previous))
    weights = {w: log(1 + len(rows) / n) * (1 if w in query else .2) for w, n in counts.items()}
    scores = [sum(weights.get(w, 0) for w in passage) for passage in terms]
    for chapter in (note or {}).get("topics", []):
        i = chapter["id"] - 1
        if 0 <= i < len(rows):
            scores[i] += 2 * len(query & words(chapter["label"]))
    ranked = sorted(range(len(rows)), key=lambda i: -scores[i])
    anchors = [i for i in ranked[:9] if scores[i] > 0]
    # A cross-language query can have no lexical matches. Include context throughout the recording.
    count = min(8, len(rows))
    anchors.extend(round(i*(len(rows)-1)/max(1, count-1)) for i in range(count))
    neighbors = [j for i in anchors[:6] for j in (i-1, i+1) if 0 <= j < len(rows)]
    return [rows[i] for i in sorted(dict.fromkeys([*anchors, *neighbors]))]
