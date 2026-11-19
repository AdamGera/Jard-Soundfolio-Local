"""SQLite storage for derived notes. Transcripts and their source filenames stay immutable."""

import json
from collections import Counter
from pathlib import Path

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
 generation INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(id,lang)
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
    own = db.execute("SELECT status,generation FROM overviews WHERE id=? AND lang=?", (key, target)).fetchone()
    if own and own["status"] == "ready":
        return True
    if (own and own["generation"]) or db.execute(
        "SELECT 1 FROM notes JOIN overviews USING(id) WHERE notes.id=? AND lang=base AND generation>0", (key,)
    ).fetchone():
        return False
    from insights import PIPELINE_REVISION
    peer = db.execute(
        """SELECT v.body,peer.id AS donor FROM notes n JOIN notes peer ON n.source_digest=peer.source_digest
                       AND n.revision=peer.revision JOIN overviews v ON v.id=peer.id
                       JOIN overviews b ON b.id=peer.id AND b.lang=peer.base AND b.generation=0
                       WHERE n.id=? AND n.revision=? AND peer.id!=n.id AND v.status='ready' AND v.generation=0 AND
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
    digest = memo_key("transcript-passages-v1", [base, passages(db, key)])
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
        db.execute(
            """INSERT INTO overviews(id,lang) VALUES(?,?) ON CONFLICT(id,lang) DO UPDATE SET status='queued'""",
            (key, base),
        )
        reuse_ready(db, key, base)
        if pref != "auto" and pref != base:
            db.execute(
                """INSERT INTO overviews(id,lang) VALUES(?,?) ON CONFLICT(id,lang) DO UPDATE SET status='queued'""",
                (key, pref),
            )
            reuse_ready(db, key, pref)


def selection(db, key, lang=None, retry=False, *, regenerate=False):
    initialize(db, key)
    row = db.execute("SELECT * FROM notes WHERE id=?", (key,)).fetchone()
    if not row:
        return None
    if lang is not None:
        target = row["base"] if lang == "auto" else lang
        db.execute("UPDATE notes SET preference=? WHERE id=?", (lang, key))
        if db.execute("SELECT 1 FROM overviews WHERE id=?", (key,)).fetchone():
            db.execute("INSERT OR IGNORE INTO overviews(id,lang) VALUES(?,?)", (key, target))
            if regenerate:
                # Repeated clicks on an already queued explicit run coalesce, even across process restarts.
                cursor = db.execute(
                    """UPDATE overviews SET generation=generation+1,status='queued',error=NULL
                       WHERE id=? AND lang=? AND (generation=0 OR status!='queued')""", (key, target))
                if cursor.rowcount and target == row["base"]:
                    from insights import PIPELINE_REVISION
                    if row["revision"] != PIPELINE_REVISION:
                        db.execute("DELETE FROM window_notes WHERE id=?", (key,))
                        db.execute("UPDATE notes SET revision=? WHERE id=?", (PIPELINE_REVISION, key))
                    else:
                        db.execute("DELETE FROM window_notes WHERE id=? AND part<0", (key,))
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
                       SELECT 1 FROM overviews WHERE id=? AND lang=? AND status='error')""",
                (key, row["base"], key, target, key, row["base"]),
            )
            if retry or regenerate:
                db.execute(
                    "UPDATE overviews SET status='queued',error=NULL WHERE id=? AND status='error' AND lang IN (?,?)",
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
    item = db.execute("SELECT status,error FROM overviews WHERE id=? AND lang=?", (key, target)).fetchone()
    display = db.execute(
        "SELECT body FROM overviews WHERE id=? AND lang=?", (key, row["displayed"])
    ).fetchone()
    return {
        "preference": row["preference"],
        "autoLanguage": row["base"],
        "target": target,
        "status": item["status"] if item else "empty",
        "error": item["error"] if item else None,
        "version": json.loads(display[0]) if display else None,
    }


def publish(db, key, target, version):
    if not db.execute("SELECT 1 FROM notes WHERE id=?", (key,)).fetchone():
        return
    progress = db.execute("SELECT generation FROM overviews WHERE id=? AND lang=?", (key, target)).fetchone()
    generation = progress[0] if progress else 0
    actual = version["language"]
    if target == "und" and actual != target:
        db.execute("UPDATE notes SET base=? WHERE id=? AND base='und'", (actual, key))
        db.execute("DELETE FROM overviews WHERE id=? AND lang='und'", (key,))
    db.execute(
        """INSERT INTO overviews(id,lang,status,title,body,generation) VALUES(?,?,'ready',?,?,?)
         ON CONFLICT(id,lang) DO UPDATE SET status='ready',title=excluded.title,body=excluded.body,generation=excluded.generation,error=NULL""",
        (key, actual, version["title"], json.dumps(version, ensure_ascii=False), generation),
    )
    note = db.execute("SELECT base,preference FROM notes WHERE id=?", (key,)).fetchone()
    if generation and actual == note["base"]:
        # Mark translations stale only after successful publication. Keep old bodies readable on failure.
        db.execute("""UPDATE overviews SET generation=generation+1,error=NULL,
                     status=CASE WHEN lang=? OR status IN ('queued','blocked') THEN 'queued' ELSE 'stale' END
                     WHERE id=? AND lang!=?""", (note["preference"], key, actual))
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
    text = " ".join(f"Speaker {chr(64 + s['speaker'])}: {s['text']}" for s in turns) or chunk["text"]
    yield begin, text


def passages(db, key):
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
