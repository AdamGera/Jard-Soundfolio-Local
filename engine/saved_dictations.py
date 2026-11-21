"""Read the persisted macOS dictation history without starting Jard."""
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import plistlib
import re
import stat
from xml.parsers.expat import ExpatError

from meeting_search import TOKEN, meeting_passages
from saved_meetings import date_bound

DEFAULT_BUNDLE_ID = "local.jardaudio.desktop"
RETENTION_LABELS = {0: "off", 1: "day", 7: "week", 30: "month"}
HISTORY_LIMIT = 250


def preferences_path():
    identifier = os.environ.get("JARD_DESKTOP_BUNDLE_ID", DEFAULT_BUNDLE_ID)
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]*", identifier):
        raise ValueError("The Jard desktop bundle identifier is invalid.")
    return Path.home() / "Library" / "Preferences" / (identifier + ".plist")


def _read_preferences(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise ValueError("Could not open the saved Jard dictation history.") from error
    with os.fdopen(descriptor, "rb") as file:
        details = os.fstat(file.fileno())
        if not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid() or details.st_mode & 0o077:
            raise ValueError("Jard history must be saved in a private file owned by the current user.")
        try:
            result = plistlib.load(file)
        except (plistlib.InvalidFileException, ExpatError, ValueError, TypeError, OverflowError) as error:
            raise ValueError("The saved Jard dictation history is invalid.") from error
    if not isinstance(result, dict):
        raise ValueError("The saved Jard dictation history is invalid.")
    return result


def _saved_history(snapshot, now):
    settings = snapshot.get("JardSystemDictation", {})
    if not isinstance(settings, dict):
        raise ValueError("The saved Jard history retention is invalid.")
    retention = settings.get("retention", 7)
    previous_retention = settings.get("previousRetention", 7)
    if (type(retention) is not int or retention not in RETENTION_LABELS
            or type(previous_retention) is not int or previous_retention not in (1, 7, 30)):
        raise ValueError("The saved Jard history retention is invalid.")

    history = snapshot.get("JardSystemDictationHistory", [])
    if not isinstance(history, list):
        raise ValueError("The saved Jard dictation history is invalid.")
    expires_before = now.timestamp() - (retention or previous_retention) * 86_400
    entries = []
    for entry in history[:HISTORY_LIMIT]:
        if not isinstance(entry, dict):
            raise ValueError("A saved Jard dictation entry is invalid.")
        identifier, text, timestamp = entry.get("id"), entry.get("text"), entry.get("time")
        if (
            not isinstance(identifier, str) or not identifier or not isinstance(text, str)
            or not _number(timestamp) or any(
                not isinstance(entry[field], str) for field in ("raw", "app", "mode", "kind") if field in entry
            ) or ("seconds" in entry and not _number(entry["seconds"]))
        ):
            raise ValueError("A saved Jard dictation entry is invalid.")
        if timestamp >= expires_before:
            entries.append(entry)
    return entries, RETENTION_LABELS[retention]


def _number(value):
    return type(value) in (int, float) and 0 <= value < 253_402_300_800


def _rank(entries, query):
    passages = []
    by_id = {}
    for entry in entries:
        key = entry["id"]
        by_id[key] = entry
        passages.append({"id": key, "text": entry["text"]})
        if entry.get("raw") and entry["raw"] != entry["text"]:
            passages.append({"id": key, "text": entry["raw"]})
    ranked = {}
    for hit in meeting_passages(passages, query, len(passages), True, stem_phrases=True):
        ranked.setdefault(hit["id"], {**by_id[hit["id"]], "score": hit["score"], "snippet": hit["snippet"]})
    return list(ranked.values())


def history_page(path, *, since=None, until=None, app=None, grep=None, search=None, oldest_first=False,
                 query=None, offset=0, limit=50, raw=False, legacy=False, now=None, snapshot=None):
    if not 1 <= limit <= 10_000:
        raise ValueError("Use a history limit from 1 to 10,000.")
    if search is not None and oldest_first:
        raise ValueError("Use either ranked search or oldest-first order.")
    now = now or datetime.now(timezone.utc)
    lower, upper = date_bound(since, now), date_bound(until, now, through=True)
    if lower is not None and upper is not None and lower >= upper:
        raise ValueError("The history start must be before its end.")
    entries, retention = _saved_history(_read_preferences(path) if snapshot is None else snapshot, now)
    first = lower.timestamp() if lower is not None else -math.inf
    last = upper.timestamp() if upper is not None else math.inf
    app_folded, literal, old_query = (value.casefold() if value is not None else None for value in (app, grep, query))
    filtered = []
    for entry in entries:
        if not first <= entry["time"] < last:
            continue
        application = entry.get("app", "").casefold()
        if app_folded is not None and app_folded != application:
            continue
        texts = (entry["text"].casefold(), entry.get("raw", "").casefold())
        if literal is not None and not any(literal in text for text in texts):
            continue
        if old_query is not None and not any(old_query in text for text in (*texts, application)):
            continue
        filtered.append(entry)
    filtered.sort(key=lambda entry: (entry["time"], entry["id"]), reverse=not oldest_first)
    if search is not None:
        filtered = _rank(filtered, search)
    offset = max(0, offset)
    page = filtered[offset:offset + limit]
    if legacy:
        fields = ("id", "time", "text", "raw", "app", "seconds", "mode", "kind")
        return {
            "entries": [{key: entry[key] for key in fields if key in entry} for entry in page],
            "total": len(filtered),
            "words": sum(sum(1 for _ in TOKEN.finditer(entry["text"])) for entry in filtered),
            "seconds": sum(entry.get("seconds", 0) for entry in filtered),
        }
    return {"version": 1, "retention": retention, "truncated": len(filtered) > offset + limit,
            "entries": [_versioned_entry(entry, raw) for entry in page]}


def _versioned_entry(entry, raw):
    result = {
        "id": entry["id"], "timestamp_ms": round(entry["time"] * 1000), "application": entry.get("app"),
        "mode": entry.get("mode"), "kind": entry.get("kind"), "final_text": entry["text"],
    }
    if raw:
        result["raw_text"] = entry.get("raw")
    if "score" in entry:
        result.update(score=entry["score"], snippet=entry["snippet"])
    return result


def render_history(result):
    parts = []
    for entry in result["entries"]:
        date = datetime.fromtimestamp(entry["timestamp_ms"] / 1000, timezone.utc).astimezone()
        labels = " · ".join(filter(None, (entry["application"], entry["mode"])))
        heading = date.isoformat(timespec="seconds")
        lines = [f"{heading}  {labels}" if labels else heading, entry["final_text"]]
        if entry.get("raw_text") is not None:
            lines.extend(("Original transcript", entry["raw_text"]))
        if "snippet" in entry:
            lines.append(entry["snippet"])
        parts.append("\n".join(lines))
    return "\n\n".join(parts) or "No retained dictations matched."
