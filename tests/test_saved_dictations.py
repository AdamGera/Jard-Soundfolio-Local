from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from engine_paths import ENGINE_ROOT
from saved_dictations import DEFAULT_BUNDLE_ID, history_page, render_history


class SavedDictationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name)
        self.path = self.home / "Library" / "Preferences" / (DEFAULT_BUNDLE_ID + ".plist")
        self.path.parent.mkdir(parents=True)
        self.now = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)

    def entry(self, key, hours, text, *, raw=None, app="Notes"):
        entry = {"id": key, "time": (self.now - timedelta(hours=hours)).timestamp(), "app": app, "text": text, "seconds": 2.5}
        if raw is not None:
            entry["raw"] = raw
        return entry

    def store(self, entries, settings=None, path=None):
        path = path or self.path
        data = {"JardSystemDictationHistory": entries}
        if settings is not None:
            data["JardSystemDictation"] = settings
        path.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_BINARY))
        path.chmod(0o600)

    def history(self, **options):
        return history_page(self.path, now=self.now, **options)

    def test_filters_and_retention_apply_before_pagination(self):
        self.store([
            self.entry("expired", 25, "Release blocked."),
            self.entry("new", 1, "Release complete.", app="Terminal"),
            self.entry("match", 2, "Done.", raw="Release check."),
            self.entry("second", 3, "Release notes.", app="NOTES"),
            self.entry("outside", 5, "Release delayed."),
        ], {"retention": 0, "previousRetention": 1})
        filtered = dict(since="4h", until="1h", app="notes", grep="release", limit=1)
        result = self.history(**filtered)
        self.assertEqual(result["retention"], "off")
        self.assertTrue(result["truncated"])
        self.assertEqual(result["entries"][0]["id"], "match")
        self.assertNotIn("raw_text", result["entries"][0])
        oldest = self.history(raw=True, oldest_first=True, **filtered)["entries"][0]
        self.assertEqual((oldest["id"], oldest["application"], oldest["raw_text"]), ("second", "NOTES", None))
        ids = [row["id"] for row in self.history()["entries"]]
        self.assertNotIn("expired", ids)

    def test_ranked_search_uses_one_document_per_take_and_the_best_text(self):
        self.store([
            self.entry("recent", 1, "Book list.", raw="Paperbacks arrived."),
            self.entry("same", 2, "Paperbacks arrived.", raw="Paperbacks arrived Tuesday."),
            self.entry("other", 3, "Call the library."),
        ])
        result = self.history(search="paperbak")
        self.assertCountEqual([entry["id"] for entry in result["entries"]], ["recent", "same"])
        self.assertIn("**Paperbacks**", next(entry["snippet"] for entry in result["entries"] if entry["id"] == "recent"))
        self.assertEqual(len(self.history(search='"paperback arrived"')["entries"]), 2)
        with self.assertRaisesRegex(ValueError, "oldest-first"):
            self.history(search="paperbacks", oldest_first=True)

    def test_retention_and_entries_are_read_from_one_open_snapshot(self):
        old = self.entry("old", 3, "Readable before exchange.")
        self.store([old], {"retention": 7})
        replacement = self.path.with_suffix(".replacement")
        self.store([self.entry("expired", 48, "Hidden after exchange.")], {"retention": 1}, replacement)
        load = plistlib.load

        def swap_while_open(file):
            replacement.replace(self.path)
            return load(file)

        with patch("saved_dictations.plistlib.load", side_effect=swap_while_open):
            result = self.history()
        self.assertEqual([row["id"] for row in result["entries"]], ["old"])
        self.assertEqual(self.history()["entries"], [])

    def test_missing_invalid_and_non_private_preferences(self):
        self.assertEqual(self.history()["entries"], [])
        self.assertFalse(self.path.exists())
        self.store([], {"retention": "forever"})
        with self.assertRaisesRegex(ValueError, "retention"):
            self.history()
        self.path.write_bytes(b"<plist><dict malformed")
        with self.assertRaisesRegex(ValueError, "invalid"):
            self.history()
        self.path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "private"):
            self.history()
        target = self.path.with_suffix(".original")
        self.path.rename(target)
        self.path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "Could not open"):
            self.history()

    def test_cli_reads_a_legacy_page_and_versioned_history_without_creating_a_library(self):
        self.now = datetime.now(timezone.utc)
        row = self.entry("match", 1, "Finished.", raw="Paperbacks finished.")
        row.update(mode="app:Notes", kind="dictation")
        self.store([row])
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        missing = self.home / "missing-library"
        environment = {**os.environ, "HOME": str(self.home), "RUMBLE_DATA_DIR": str(missing),
                       "JARD_DESKTOP_BUNDLE_ID": DEFAULT_BUNDLE_ID, "PYTHONDONTWRITEBYTECODE": "1"}
        cli = [sys.executable, str(ENGINE_ROOT / "jardctl.py")]
        old = json.loads(subprocess.check_output(cli + ["--json", "history", "--query", "paperbacks"], env=environment))
        self.assertEqual(old, {"entries": [row], "total": 1, "words": 1, "seconds": 2.5})
        skipped = json.loads(subprocess.check_output(cli + ["history", "--offset", "1"], env=environment))
        self.assertEqual((skipped["entries"], skipped["total"]), ([], 1))
        versioned = json.loads(subprocess.check_output(cli + ["history", "--since", "3h", "--raw", "--json"], env=environment))
        self.assertEqual(versioned["version"], 1)
        self.assertEqual(versioned["entries"][0]["mode"], "app:Notes")
        self.assertIn("Paperbacks finished.", render_history(versioned))
        invalid = subprocess.run(cli + ["history", "--limit", "10001", "--json"], env=environment, capture_output=True)
        self.assertEqual(invalid.returncode, 1)
        self.assertFalse(invalid.stdout)
        self.assertIn("history limit", json.loads(invalid.stderr)["error"])
        self.assertFalse(missing.exists())
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), before)
