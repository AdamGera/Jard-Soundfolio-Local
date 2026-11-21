"""SQLite storage for derived notes. Transcripts and their source filenames stay immutable."""

import json
import re
from collections import Counter
from pathlib import Path

import personal_notes
from writing_cache import memo_key

SOURCE_FORMAT_REVISION = "timed-sentences-v1-weighted-language-v1"
LANGUAGES = json.loads(Path(__file__).with_name("overview_languages.json").read_text())
CODES = {name.casefold(): code for code, name in LANGUAGES.items()}
CODES.update({code.lower(): code for code in LANGUAGES})
CODES.update(arabic="ar", chinese="zh-Hans", filipino="tl", norwegian="nb", albanian="als", tibetan="bo")

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
 id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
 base TEXT NOT NULL, preference TEXT NOT NULL DEFAULT 'auto', displayed TEXT
);
CREATE TABLE IF NOT EXISTS overviews (
 id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE, lang TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued', title TEXT, body TEXT, error TEXT,
 generation INTEGER NOT NULL DEFAULT 0, scope TEXT, PRIMARY KEY(id,lang)
);
CREATE TABLE IF NOT EXISTS window_notes (
 id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE, part INTEGER NOT NULL, data TEXT NOT NULL,
 PRIMARY KEY(id,part)
);
CREATE TABLE IF NOT EXISTS chat_turns (
 id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, message_id TEXT NOT NULL,
 question TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'streaming',
 PRIMARY KEY(id,message_id)
);
"""


def reuse_ready(db, key, target):
    """A copy belongs to the new session, so its audio, title and deletion stay independent."""
    own = db.execute("SELECT status,generation,queued_personal FROM overviews WHERE id=? AND lang=?", (key, target)).fetchone()
    if own and own["status"] == "ready":
        return True
    if (own and (own["generation"] or own["queued_personal"])) or db.execute(
        "SELECT 1 FROM notes JOIN overviews USING(id) WHERE notes.id=? AND lang=base AND generation>0", (key,)
    ).fetchone():
        return False
    from insights import PIPELINE_REVISION
    peer = db.execute(
        """SELECT v.body,peer.id AS donor FROM notes n JOIN notes peer ON n.source_digest=peer.source_digest
                       AND n.revision=peer.revision JOIN overviews v ON v.id=peer.id
                       JOIN overviews b ON b.id=peer.id AND b.lang=peer.base AND b.generation=0
                       WHERE n.id=? AND n.revision=? AND peer.id!=n.id AND v.status='ready' AND v.generation=0 AND
                       v.queued_personal='' AND v.published_personal='' AND
                       coalesce(json_extract(v.body, '$.manuallyEdited'), 0)=0 AND
                       v.lang=CASE WHEN ?='und' THEN peer.base ELSE ? END
                       ORDER BY v.rowid LIMIT 1""",
        (key, PIPELINE_REVISION, target, target),
    ).fetchone()
    if not peer:
        return False
    # The peer index and editorial stages correspond to identical timestamped source and pipeline.
    db.execute(
        """INSERT OR IGNORE INTO window_notes(id,part,data)
               SELECT ?,part,data FROM window_notes WHERE id=?""",
        (key, peer["donor"]),
    )
    db.execute(
        """INSERT INTO writing_result_sessions(id,digest,for_overview)
               SELECT ?,digest,1 FROM writing_result_sessions WHERE id=? AND for_overview=1
               ON CONFLICT(id,digest) DO UPDATE SET for_overview=1""",
        (key, peer["donor"]),
    )
    publish(db, key, target, json.loads(peer["body"]))
    return True


def speaker_context(db, key):
    """Use the same measured activity and denominator as the player's analytics tab."""
    row = db.execute("SELECT json_extract(transcript, '$.speakerActivity') FROM sessions WHERE id=?", (key,)).fetchone()
    if not row or row[0] is None:
        return None
    activity = json.loads(row[0])
    speakers = [
        {"label": f"Speaker {chr(64 + item['speaker'])}", "seconds": round(item["seconds"], 2),
         "sharePercent": round(100 * item["share"], 1)}
        for item in activity
    ]
    return {"speakerCount": len(speakers), "speakers": speakers}


def calendar_context(db, key):
    row = db.execute("SELECT json_extract(options,'$.meetingCalendar') FROM sessions WHERE id=?", (key,)).fetchone()
    return json.loads(row[0]) if row and row[0] else None


def initialize(db, key, *, refinalize=False):
    from insights import PIPELINE_REVISION

    prior = db.execute("SELECT * FROM notes WHERE id=?", (key,)).fetchone()
    if prior and prior["revision"] == PIPELINE_REVISION and not refinalize:
        return
    saved = prior and db.execute(
        "SELECT status FROM overviews WHERE id=? AND lang=? AND body IS NOT NULL",
        (key, prior["base"])).fetchone()
    if saved and not refinalize:
        # Finished notes are library items. A software update alone must not rewrite them.
        if saved["status"] == "queued" and prior["revision"] != PIPELINE_REVISION:
            db.execute("DELETE FROM window_notes WHERE id=?", (key,))
            db.execute("UPDATE notes SET revision=? WHERE id=?", (PIPELINE_REVISION, key))
        return
    row = db.execute("SELECT status, length(search_text) AS size FROM sessions WHERE id=?", (key,)).fetchone()
    if not row or row["status"] != "complete":
        return
    weights = Counter()
    start = 0
    for end, lang, size in db.execute(
        """SELECT seconds,json_extract(payload,'$.language'),
          length(json_extract(payload,'$.text')) FROM chunks WHERE session_id=? ORDER BY seconds""",
        (key,),
    ):
        code = CODES.get((lang or "").casefold())
        if code and size:
            weights[code] += end - start
        start = end
    base = weights.most_common(1)[0][0] if weights else "und"
    source = [base, passages(db, key), speaker_context(db, key)]
    calendar = calendar_context(db, key)
    if calendar:
        source.append(calendar)
    digest = memo_key("transcript-and-speakers-v2", source)
    if prior and prior["source_digest"] == digest and (saved or prior["revision"] == PIPELINE_REVISION):
        return
    pref = prior["preference"] if prior else "auto"
    if prior and prior["source_digest"] == digest:
        # Keep prior language versions readable while rebuilding the current version in the background.
        db.execute("DELETE FROM window_notes WHERE id=?", (key,))
        db.execute("UPDATE notes SET revision=?,base=? WHERE id=?", (PIPELINE_REVISION, base, key))
        db.execute("UPDATE overviews SET status='stale',error=NULL WHERE id=?", (key,))
    else:
        if prior:
            db.execute("UPDATE chat_turns SET status='stopped' WHERE id=? AND status='streaming'", (key,))
            db.execute("DELETE FROM notes WHERE id=?", (key,))
            db.execute("DELETE FROM writing_result_sessions WHERE id=?", (key,))
        db.execute(
            "INSERT INTO notes(id,base,preference,source_digest,revision) VALUES(?,?,?,?,?)",
            (key, base, pref, digest, PIPELINE_REVISION),
        )
    # Pre-cache releases stored turns without a fingerprint. Bind once at upgrade.
    db.execute(
        "UPDATE chat_turns SET source_digest=? WHERE id=? AND source_digest IS NULL",
        ((prior["source_digest"] if prior else None) or digest, key),
    )
    if row["size"]:
        context = personal_notes.as_context(db, key)
        db.execute(
            """INSERT INTO overviews(id,lang,queued_personal) VALUES(?,?,?) ON CONFLICT(id,lang)
                   DO UPDATE SET status='queued',queued_personal=excluded.queued_personal""",
            (key, base, context),
        )
        reuse_ready(db, key, base)
        if pref != "auto" and pref != base:
            db.execute(
                """INSERT INTO overviews(id,lang,queued_personal) VALUES(?,?,?) ON CONFLICT(id,lang)
                   DO UPDATE SET status='queued',queued_personal=excluded.queued_personal""",
                (key, pref, context),
            )
            reuse_ready(db, key, pref)


class RegenerationConflict(ValueError):
    pass


def queue_regeneration(db, key, target, note, scope):
    from insights import CHAPTERS, GUIDE, PIPELINE_REVISION, SUMMARY

    job = db.execute("SELECT status,scope,body IS NOT NULL AS saved,generation FROM overviews WHERE id=? AND lang=?",
                     (key, target)).fetchone()
    if scope and not job["saved"]:
        raise RegenerationConflict("Wait for the first version to finish before regenerating this tab.")
    if job["status"] == "queued":
        if job["scope"] != scope:
            raise RegenerationConflict("A writing task is already running for this language.")
        if job["generation"]:
            return
    db.execute("""UPDATE overviews SET generation=generation+1,status='queued',scope=?,error=NULL
                  WHERE id=? AND lang=?""", (scope, key, target))
    if target != note["base"]:
        return
    if note["revision"] != PIPELINE_REVISION:
        db.execute("DELETE FROM window_notes WHERE id=?", (key,))
        db.execute("UPDATE notes SET revision=? WHERE id=?", (PIPELINE_REVISION, key))
    elif scope == "overview":
        db.execute("DELETE FROM window_notes WHERE id=? AND part IN (?,?)", (key, GUIDE, SUMMARY))
    elif scope == "timeline":
        db.execute("DELETE FROM window_notes WHERE id=? AND part=?", (key, CHAPTERS))
    else:
        db.execute("DELETE FROM window_notes WHERE id=? AND part<0", (key,))


def selection(db, key, lang=None, retry=False, *, regenerate=False, update_personal=False, initialize_missing=True):
    if initialize_missing:
        initialize(db, key)
    row = db.execute("SELECT * FROM notes WHERE id=?", (key,)).fetchone()
    if not row:
        return None
    if lang is not None:
        target = row["base"] if lang == "auto" else lang
        db.execute("UPDATE notes SET preference=? WHERE id=?", (lang, key))
        if db.execute("SELECT 1 FROM overviews WHERE id=?", (key,)).fetchone():
            context = personal_notes.as_context(db, key)
            db.execute("INSERT OR IGNORE INTO overviews(id,lang,queued_personal) VALUES(?,?,?)",
                       (key, target, context))
            if update_personal:
                job = db.execute("SELECT status,body FROM overviews WHERE id=? AND lang=?", (key, target)).fetchone()
                if job["status"] == "queued":
                    raise RegenerationConflict("Wait for the current overview to finish.")
                queue_regeneration(db, key, target, row, "overview" if job["body"] else None)
                db.execute("UPDATE overviews SET queued_personal=? WHERE id=? AND lang=?", (context, key, target))
            elif regenerate:
                queue_regeneration(db, key, target, row, regenerate if isinstance(regenerate, str) else None)
            db.execute(
                "UPDATE overviews SET status='queued' WHERE id=? AND lang=? AND status='stale'",
                (key, target),
            )
            reuse_ready(db, key, target)
            # A new translation depends on a successful first overview.
            db.execute(
                """UPDATE overviews SET status='blocked',
                       error=(SELECT error FROM overviews WHERE id=? AND lang=?)
                       WHERE id=? AND lang=? AND status='queued' AND EXISTS(
                       SELECT 1 FROM overviews WHERE id=? AND lang=? AND status='error' AND body IS NULL)""",
                (key, row["base"], key, target, key, row["base"]),
            )
            if retry or regenerate:
                db.execute(
                    """UPDATE overviews SET status='queued',error=NULL WHERE id=? AND status='error'
                       AND (lang=? OR (lang=? AND body IS NULL))""",
                    (key, target, row["base"]),
                )
                db.execute(
                    "UPDATE overviews SET status='queued',error=NULL WHERE id=? AND lang=? AND status='blocked'",
                    (key, target),
                )
        db.execute(
            """UPDATE notes SET displayed=? WHERE id=? AND EXISTS
                   (SELECT 1 FROM overviews WHERE id=? AND lang=? AND body IS NOT NULL)""",
            (target, key, key, target),
        )
    row = db.execute("SELECT * FROM notes WHERE id=?", (key,)).fetchone()
    target = row["base"] if row["preference"] == "auto" else row["preference"]
    item = db.execute("SELECT status,error,scope,published_personal,queued_personal FROM overviews WHERE id=? AND lang=?", (key, target)).fetchone()
    display = db.execute(
        "SELECT body,generation FROM overviews WHERE id=? AND lang=?", (key, row["displayed"])
    ).fetchone()
    return {
        "preference": row["preference"],
        "autoLanguage": row["base"],
        "target": target,
        "status": item["status"] if item else "empty",
        "error": item["error"] if item else None,
        "scope": item["scope"] if item else None,
        "notesChanged": bool(item and item["published_personal"] != personal_notes.fingerprint(personal_notes.as_context(db, key))),
        "updatingPersonal": bool(item and item["status"] == "queued" and item["scope"] == "overview"
                                 and item["published_personal"] != personal_notes.fingerprint(item["queued_personal"])),
        "version": json.loads(display["body"]) if display and display["body"] else None,
        "editRevision": overview_edit_revision(display) if display and display["body"] else None,
    }


OVERVIEW_CITATION = re.compile(r"⟦(\d+(?:\.\d+)?)⟧")


def overview_edit_revision(row):
    return memo_key("overview-edit-v1", [row["generation"], row["body"]])


def edit_overview(db, key, language, revision, summary, markdown):
    row = db.execute("SELECT status,body,generation FROM overviews WHERE id=? AND lang=?", (key, language)).fetchone()
    if not row or not row["body"] or row["status"] == "queued":
        raise RegenerationConflict("Wait until the overview is ready to edit.")
    current = json.loads(row["body"])
    existing_markdown = current.get("markdown") or ""
    if current["summary"] == summary and existing_markdown == markdown:
        return
    if overview_edit_revision(row) != revision:
        raise RegenerationConflict("The overview changed while you were editing. Reopen it to start from the latest version.")
    if set(OVERVIEW_CITATION.findall(markdown)) - set(OVERVIEW_CITATION.findall(existing_markdown)):
        raise ValueError("An edit can only use playback timestamps already present in this overview.")
    current.update(summary=summary, markdown=markdown, manuallyEdited=True)
    db.execute("UPDATE overviews SET body=? WHERE id=? AND lang=?",
               (json.dumps(current, ensure_ascii=False), key, language))


def publish(db, key, target, version):
    if not db.execute("SELECT 1 FROM notes WHERE id=?", (key,)).fetchone():
        return
    progress = db.execute("SELECT generation,scope,body,queued_personal,published_personal FROM overviews WHERE id=? AND lang=?", (key, target)).fetchone()
    generation = progress[0] if progress else 0
    scope = progress["scope"] if progress else None
    if scope:
        saved = json.loads(progress["body"])
        fields = ("summary", "markdown") if scope == "overview" else ("topics",)
        # Only the requested tab owns these fields. Keep even unknown legacy metadata.
        for field in fields:
            if field in version:
                saved[field] = version[field]
            else:
                saved.pop(field, None)
        if scope == "overview":
            saved.pop("manuallyEdited", None)
        version = saved
    context = progress["queued_personal"] if progress else ""
    fingerprint = progress["published_personal"] if scope == "timeline" else personal_notes.fingerprint(context)
    actual = version["language"]
    if target == "und" and actual != target:
        db.execute("UPDATE notes SET base=? WHERE id=? AND base='und'", (actual, key))
        db.execute("DELETE FROM overviews WHERE id=? AND lang='und'", (key,))
    db.execute(
        """INSERT INTO overviews(id,lang,status,title,body,generation,queued_personal,published_personal)
           VALUES(?,?,'ready',?,?,?,?,?)
         ON CONFLICT(id,lang) DO UPDATE SET status='ready',title=excluded.title,body=excluded.body,
           generation=excluded.generation,scope=NULL,error=NULL,queued_personal=excluded.queued_personal,
           published_personal=excluded.published_personal""",
        (key, actual, version["title"], json.dumps(version, ensure_ascii=False), generation, context, fingerprint),
    )
    note = db.execute("SELECT base,preference FROM notes WHERE id=?", (key,)).fetchone()
    if generation and not scope and actual == note["base"]:
        # Mark translations stale only after successful publication. Keep old bodies readable on failure.
        db.execute("""UPDATE overviews SET generation=generation+1,error=NULL,scope=NULL,
                     status=CASE WHEN lang=? OR status IN ('queued','blocked') THEN 'queued' ELSE 'stale' END
                     WHERE id=? AND lang!=?""", (note["preference"], key, actual))
    if actual == note["base"]:
        db.execute("UPDATE overviews SET status='queued',error=NULL WHERE id=? AND status='blocked'", (key,))
    db.execute(
        """UPDATE notes SET displayed=? WHERE id=? AND
               (displayed IS NULL OR preference=? OR (preference='auto' AND base=?))""",
        (actual, key, actual, actual),
    )


def _chunk_passages(chunk, begin, end):
    """Use timed sentence/turn starts so even short recordings can have distinct seek targets."""
    words = chunk["words"]
    sentence_ends = (".", "?", "!", "؟", "。", "！", "？", "…")
    if words and all("separator" in word for word in words):
        first = 0
        start = next((word["start"] for word in words if word.get("start") is not None), begin)
        for i in range(1, len(words) + 1):
            if i < len(words):
                current, previous = words[i], words[i - 1]
                anchor = current.get("start")
                if anchor is None or anchor <= start:
                    continue
                different_speaker = current.get("speaker") != previous.get("speaker")
                sentence = previous["text"].rstrip('”"»’').endswith(sentence_ends)
                if not (different_speaker or (sentence and anchor - start >= 3) or anchor - start >= 14):
                    continue
            text = "".join(w["text"] + w["separator"] for w in words[first:i]).strip()
            speaker = words[first].get("speaker")
            if text:
                yield (
                    max(begin, min(end, start)),
                    (f"Speaker {chr(64 + speaker)}: " if speaker else "") + text,
                )
            if i < len(words):
                first, start = i, words[i]["start"]
        return
    turns = chunk.get("segments") or []
    text = " ".join(f"Speaker {chr(64 + s['speaker'])}: {s['text']}" if s.get("speaker") else s["text"] for s in turns) or chunk["text"]
    yield begin, text


def passages(db, key):
    # Mixed-track recovery can retain attributed provisional turns in the published transcript.
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='meeting_rolling'").fetchone():
        row = db.execute("""SELECT transcript FROM sessions WHERE id=? AND status='complete'
            AND EXISTS(SELECT 1 FROM meeting_rolling WHERE session_id=?)""", (key, key)).fetchone()
        if row and row[0]:
            transcript = json.loads(row[0])
            names = transcript.get("speakerNames", {})
            return [{"id": index, "start": turn["start"] or 0,
                     "text": f"{label}: {turn['text']}" if (label := names.get(str(turn["speaker"]))) else turn["text"]}
                    for index, turn in enumerate(transcript["segments"], 1)]
    rows = []
    begin = 0
    for end, payload in db.execute(
        "SELECT seconds,payload FROM chunks WHERE session_id=? ORDER BY seconds", (key,)
    ):
        chunk = json.loads(payload)
        if chunk["text"].strip():
            for start, text in _chunk_passages(chunk, begin, end):
                rows.append({"id": len(rows) + 1, "start": start, "text": text})
        begin = end
    return rows
