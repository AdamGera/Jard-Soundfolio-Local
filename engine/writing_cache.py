"""Content-addressed local model results with session ownership and last-reference cleanup."""

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar

_active = ContextVar("writing_cache_session", default=None)

SCHEMA = """
CREATE TABLE IF NOT EXISTS writing_results (
    digest TEXT PRIMARY KEY, result TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS writing_result_sessions (
    id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    digest TEXT NOT NULL REFERENCES writing_results(digest) ON DELETE CASCADE,
    PRIMARY KEY(id, digest)
);
CREATE INDEX IF NOT EXISTS result_owners ON writing_result_sessions(digest);
CREATE TRIGGER IF NOT EXISTS remove_unowned_writing_result AFTER DELETE ON writing_result_sessions
WHEN NOT EXISTS(SELECT 1 FROM writing_result_sessions WHERE digest=OLD.digest)
BEGIN DELETE FROM writing_results WHERE digest=OLD.digest; END;
"""


def memo_key(kind, inputs):
    canonical = json.dumps(
        [kind, inputs], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@contextmanager
def scope(store, key, purpose="writing", *, fresh=False):
    if fresh:
        # Explicit regeneration is session-local. Neither read nor replace shared exact results.
        token = _active.set(None)
        try:
            yield
        finally:
            _active.reset(token)
        return
    with store.connection() as db:
        row = db.execute("SELECT source_digest,revision FROM notes WHERE id=?", (key,)).fetchone()
    identity = tuple(row) if row else (None, None)
    token = _active.set((store, key, purpose, identity))
    try:
        yield
    finally:
        _active.reset(token)


def recall(digest):
    context = _active.get()
    if not context:
        return None
    store, key, purpose, identity = context
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            """SELECT result FROM writing_results WHERE digest=? AND EXISTS
                         (SELECT 1 FROM notes WHERE id=? AND source_digest=? AND revision=?)""",
            (digest, key, *identity),
        ).fetchone()
        if row:
            link(db, key, digest, purpose)
            return json.loads(row["result"])
    return None


def remember(digest, value):
    context = _active.get()
    if not context:
        return
    store, key, purpose, identity = context
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute(
            "SELECT 1 FROM notes WHERE id=? AND source_digest=? AND revision=?", (key, *identity)
        ).fetchone():
            return
        db.execute(
            "INSERT OR IGNORE INTO writing_results VALUES(?,?)",
            (digest, json.dumps(value, ensure_ascii=False)),
        )
        link(db, key, digest, purpose)


def forget(digest):
    """Evict a proven invalid cached result before regenerating it."""
    context = _active.get()
    if context:
        with context[0].connection() as db:
            db.execute("DELETE FROM writing_results WHERE digest=?", (digest,))


def link(db, key, digest, purpose):
    db.execute(
        """INSERT INTO writing_result_sessions(id,digest,for_overview) VALUES(?,?,?)
               ON CONFLICT(id,digest) DO UPDATE SET for_overview=max(for_overview,excluded.for_overview)""",
        (key, digest, int(purpose == "writing")),
    )


def migrate(db):
    """Support libraries created both before notes and during the first notes release."""
    db.executescript(SCHEMA)
    if "for_overview" not in {r["name"] for r in db.execute("PRAGMA table_info(writing_result_sessions)")}:
        db.execute("ALTER TABLE writing_result_sessions ADD COLUMN for_overview INTEGER NOT NULL DEFAULT 0")
    if "generation" not in {r["name"] for r in db.execute("PRAGMA table_info(overviews)")}:
        db.execute("ALTER TABLE overviews ADD COLUMN generation INTEGER NOT NULL DEFAULT 0")
    for table, columns in [
        ("notes", ("source_digest", "revision")),
        ("overviews", ("scope",)),
        ("chat_turns", ("source_digest", "request_digest", "error")),
    ]:
        existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        for column in columns:
            if column not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
    db.execute("CREATE INDEX IF NOT EXISTS note_source_revision ON notes(source_digest,revision)")
    db.execute(
        "CREATE INDEX IF NOT EXISTS completed_chat_request ON chat_turns(request_digest) WHERE status='complete'"
    )
