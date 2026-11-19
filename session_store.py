"""SQLite session metadata and inference output, with managed audio alongside it."""
import fcntl
import json
import mimetypes
import os
import shutil
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import session_notes
import writing_cache


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def transcript_from_chunks(chunks, seconds, speakers, interruption=None):
    text, languages, segments, words = [], [], [], []
    intervals = {}
    for chunk in chunks:
        if chunk["text"]:
            text.append(chunk["text"])
            language = chunk.get("language")
            if language and language not in languages:
                languages.append(language)
        words.extend(chunk["words"])
        for segment in chunk["segments"]:
            if segments and segments[-1]["speaker"] == segment["speaker"]:
                segments[-1]["text"] += " " + segment["text"]
                if segment["end"] is not None:
                    segments[-1]["end"] = segment["end"]
            else:
                segments.append(dict(segment))
        for turn in chunk.get("activity", []):
            intervals.setdefault(turn["speaker"], []).append((turn["start"], turn["end"]))
    rows = []
    for speaker, spans in sorted(intervals.items()):
        merged = []
        for start, end in sorted(spans):
            start, end = max(0, start), min(seconds, end)
            if end <= start:
                continue
            if merged and start <= merged[-1]["end"]:
                merged[-1]["end"] = max(end, merged[-1]["end"])
            else:
                merged.append({"start": start, "end": end})
        if merged:
            rows.append(dict(speaker=speaker, intervals=merged, seconds=sum(s["end"] - s["start"] for s in merged)))
    total = sum(row["seconds"] for row in rows)
    for row in rows:
        row["share"] = row["seconds"] / total
    result = dict(text=" ".join(text), durationSeconds=seconds, detectedLanguages=languages,
                  segments=segments, words=words, speakerActivity=rows if speakers else None)
    if interruption:
        result["interruption"] = interruption
    return result


class MissingSession(KeyError):
    pass


@contextmanager
def open_library(root):
    # Recovery can only run while this server owns the library.
    root = Path(root).expanduser()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".server.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store = SessionStore(root)
        for upload in root.glob(".upload-*"):
            if upload.is_dir():
                shutil.rmtree(upload)
        yield store


class SessionStore:
    def __init__(self, root):
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.audio = self.root / "audio"
        self.audio.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "sessions.sqlite3"
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, created TEXT NOT NULL,
                    filename TEXT NOT NULL, mime TEXT NOT NULL, options TEXT NOT NULL,
                    duration REAL, processed REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'processing',
                    error TEXT, transcript TEXT, search_text TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    seconds REAL NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(session_id, seconds)
                );
            """)
            db.executescript(session_notes.SCHEMA)
            writing_cache.migrate(db)
            db.execute("UPDATE chat_turns SET status='stopped' WHERE status='streaming'")
            db.execute("UPDATE sessions SET status='interrupted',error=? WHERE status='processing'",
                       ("Processing stopped when Jard Audio closed.",))
            ids = {row[0] for row in db.execute("SELECT id FROM sessions")}
        # Finish filesystem changes interrupted between a rename and a database commit.
        for path in self.audio.iterdir():
            name = path.name.removesuffix(".deleting")
            try:
                if str(UUID(name)) != name:
                    continue
            except ValueError:
                continue
            if name not in ids:
                shutil.rmtree(path)
            elif path.name.endswith(".deleting"):
                path.rename(self.audio / name)

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA secure_delete=ON")
            with db:
                yield db

    def create(self, source, name, mime, options, duration, *, key=None):
        key = key or str(uuid4())
        created = datetime.now(timezone.utc).isoformat()
        name = Path(name or "").name.strip()[:240] or ("Recording" if options["recording"] else "Audio")
        mime = mime.partition(";")[0].lower()
        suffix = Path(name).suffix.lower()
        if options["recording"] or not suffix[1:].isalnum() or len(suffix) > 10:
            suffix = {"audio/mp4": ".m4a", "audio/wav": ".wav", "video/webm": ".webm", "audio/webm": ".webm"}.get(mime) or mimetypes.guess_extension(mime) or ".audio"
        mime = mimetypes.guess_type("source" + suffix)[0] or mime
        if not mime.startswith("audio/") and mime not in ("video/webm", "video/mp4"):
            mime = "application/octet-stream"
        folder = self.audio / key
        folder.mkdir(mode=0o700)
        filename = "source" + suffix
        try:
            os.replace(source, folder / filename)
            with self.connection() as db:
                db.execute("""INSERT INTO sessions(id,name,created,filename,mime,options,duration)
                              VALUES (?,?,?,?,?,?,?)""", (key, name, created, filename, mime, encode(options), duration))
        except BaseException:
            if (folder / filename).exists():
                os.replace(folder / filename, source)
            shutil.rmtree(folder)
            raise
        return key, folder / filename

    def append(self, key, chunk, seconds):
        with self.connection() as db:
            db.execute("INSERT INTO chunks VALUES(?,?,?)", (key, seconds, encode(chunk)))
            db.execute("UPDATE sessions SET processed=?,search_text=trim(search_text || ' ' || ?) WHERE id=?",
                       (seconds, chunk["text"], key))

    def _result(self, db, row, *, rebuild=False):
        if row["transcript"] and not rebuild:
            return json.loads(row["transcript"])
        chunks = db.execute("SELECT payload FROM chunks WHERE session_id=? ORDER BY seconds", (row["id"],))
        return transcript_from_chunks((json.loads(c[0]) for c in chunks), row["processed"],
                                      json.loads(row["options"])["diarization"], row["error"])

    def finish(self, key, error=None):
        with self.connection() as db:
            db.execute("""UPDATE sessions SET status=?,error=?,duration=
                       CASE WHEN ? IS NULL THEN processed ELSE coalesce(duration,processed) END WHERE id=?""",
                       ("interrupted" if error else "complete", error, error, key))
            row = db.execute("SELECT * FROM sessions WHERE id=?", (key,)).fetchone()
            result = self._result(db, row, rebuild=True)
            db.execute("UPDATE sessions SET transcript=? WHERE id=?", (encode(result), key))

            if not error:
                session_notes.initialize(db, key, refinalize=True)

    def list(self, query):
        with self.connection() as db:
            db.create_function("fold", 1, str.casefold, deterministic=True)
            rows = db.execute("""SELECT s.id,coalesce(o.title,s.name) AS name,s.created,s.duration,s.processed,s.status,
                (SELECT count(*) FROM overviews v WHERE v.id=s.id AND v.status='queued') AS pendingOverview
                FROM sessions s LEFT JOIN notes n ON n.id=s.id LEFT JOIN overviews o ON o.id=s.id AND o.lang=n.displayed
                WHERE ?='' OR instr(fold(s.name),?) OR instr(fold(coalesce(o.title,'')),?) OR instr(fold(s.search_text),?)
                ORDER BY created DESC""", (query, query.casefold(), query.casefold(), query.casefold()))
            return [dict(row) for row in rows]

    def get(self, key):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM sessions WHERE id=?", (key,)).fetchone()
            if not row:
                raise MissingSession(key)
            return {**self._metadata(db, row), "transcript": self._result(db, row)}

    def _metadata(self, db, row):
        key = row["id"]
        overview = session_notes.selection(db, key)
        options = json.loads(row["options"])
        return {**{k: row[k] for k in ("id", "name", "created", "duration", "processed", "status")},
                **options, "overview": overview,
                "name": overview["version"]["title"] if overview and overview["version"] else row["name"],
                "audioName": row["name"] + Path(row["filename"]).suffix if options["recording"] else row["name"],
                "audioPath": f"audio/{key}/{row['filename']}", "audioUrl": f"/api/sessions/{key}/audio"}

    def page(self, key, offset, limit, query):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT id,name,filename,created,duration,processed,status,error,options
                                FROM sessions WHERE id=?""", (key,)).fetchone()
            if row is None:
                raise MissingSession(key)
            # Apply the filter after calculating original positions, so search hits
            # retain their true playback offsets. Only page-sized payloads reach Python.
            db.create_function("fold", 1, str.casefold, deterministic=True)
            condition = " AND instr(fold(json_extract(payload,'$.text')),?)" if query else ""
            params = (key, query.casefold()) if query else (key,)
            total = db.execute("SELECT count(*) FROM chunks WHERE session_id=?" + condition, params).fetchone()[0]
            rows = db.execute("""SELECT * FROM (
                    SELECT row_number() OVER (ORDER BY seconds)-1 AS idx,
                           lag(seconds,1,0) OVER (ORDER BY seconds) AS start,seconds,payload
                    FROM chunks WHERE session_id=?
                ) WHERE 1=1""" + condition + " ORDER BY seconds LIMIT ? OFFSET ?", (*params, limit, offset))
            chunks = [{**json.loads(c["payload"]), "index": c["idx"], "start": c["start"], "end": c["seconds"]} for c in rows]
            return {**self._metadata(db, row), "interruption": row["error"], "offset": offset,
                    "nextOffset": offset + limit if offset + limit < total else None, "totalChunks": total, "chunks": chunks}

    def media(self, key):
        with self.connection() as db:
            row = db.execute("SELECT filename,mime FROM sessions WHERE id=?", (key,)).fetchone()
            if not row:
                raise MissingSession(key)
            return self.audio / key / row["filename"], row["mime"]

    def delete(self, key):
        folder = self.audio / key
        trash = self.audio / (key + ".deleting")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM sessions WHERE id=?", (key,)).fetchone()
            if not row:
                raise MissingSession(key)
            if row["status"] == "processing":
                raise ValueError("This session is still processing.")
            if folder.exists():
                folder.rename(trash)
            try:
                db.execute("DELETE FROM sessions WHERE id=?", (key,))
                db.commit()
            except BaseException:
                if trash.exists():
                    trash.rename(folder)
                raise
        if trash.exists():
            shutil.rmtree(trash)
