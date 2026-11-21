"""Exercise starter files, offline commands, and live configuration review."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

from dictation_output import process
from output_transform_workspace import ConfigInspector, initialize_workspace


async def main(helper, cli):
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp).resolve()
        env = {**os.environ, "XDG_CONFIG_HOME": str(base / "config"), "RUMBLE_DATA_DIR": str(base / "library")}

        def command(*args):
            return subprocess.run([*cli, "transforms", *args], env=env, capture_output=True, text=True, timeout=10)

        def run(*args):
            completed = command(*args, "--json")
            assert completed.returncode == 0, completed.stderr
            return json.loads(completed.stdout)

        first = run("init")
        default = base / "config" / "jaro"
        assert Path(first["directory"]) == default
        starter = default / "jaro.config.ts"
        assert Path(first["path"]) == starter
        assert sorted(first["created"]) == ["AGENTS.md", "jaro.config.ts", "jaro.d.ts", "tsconfig.json"]
        checked = run("check")
        assert checked["transforms"][0]["id"] == "trim-lines" and "files" not in checked
        sample = await process("  first  \n  second  ", ("local:trim-lines",),
                               script=starter, script_digest=checked["digest"], script_runtime=helper)
        assert sample.text == "first\nsecond" and not sample.warnings, sample
        assert not run("init")["created"]

        existing = base / "existing"
        existing.mkdir()
        config = existing / "jaro.config.mjs"
        config.write_text('import { title } from "./lib/name"; export default { transformations: { label: { name: title, transform: text => title + ": " + text } } };')
        agent_notes = existing / "AGENTS.md"
        agent_notes.write_text("User notes\n")
        outside = base / "outside"
        outside.write_text("User type definitions\n")
        (existing / "jaro.d.ts").symlink_to(outside)
        results = await asyncio.gather(*(asyncio.to_thread(initialize_workspace, existing) for _ in range(3)))
        assert all(row["path"] == str(config) for row in results)
        assert agent_notes.read_text() == "User notes\n"
        assert outside.read_text() == "User type definitions\n" and (existing / "jaro.d.ts").is_symlink()
        lib = existing / "lib"
        lib.mkdir()
        fallback = lib / "name.js"
        fallback.write_text('export const title = "First";')
        inspector = ConfigInspector(helper)
        before = await inspector.inspect(config)
        assert before["transforms"][0]["name"] == "First", before
        assert await inspector.inspect(config) == before
        preferred = lib / "name.ts"
        preferred.write_text('export const title: string = "Second";')
        updated = await inspector.inspect(config)
        assert updated["digest"] != before["digest"] and updated["transforms"][0]["name"] == "Second", updated
        preferred.write_text("export const title: string = ;")
        invalid = await inspector.inspect(config)
        assert not invalid.get("digest") and invalid["stale"] and invalid["transforms"] == updated["transforms"], invalid
        assert "name.ts" in invalid["message"], invalid
        assert await inspector.inspect(config, fresh=True) == invalid
        failed = await process("original", ("local:label",), script=config, script_digest=updated["digest"], script_runtime=helper)
        assert failed.text == "original" and failed.warnings
        preferred.write_text('export const title: string = "Restored";')
        fixed = await inspector.inspect(config)
        assert fixed["transforms"][0]["name"] == "Restored" and not fixed.get("stale"), fixed
        (existing / "jaro.config.js").write_text("export default {}")
        collision = command("init", "--directory", str(existing), "--json")
        assert collision.returncode and "Keep one" in collision.stderr, collision.stderr
        assert agent_notes.read_text() == "User notes\n"
    print("Offline setup, idempotence, live import refresh, stale definitions, and repaired version passed.")


if __name__ == "__main__":
    cli = sys.argv[2:] or [sys.executable, str(Path(__file__).resolve().parents[2] / "engine/jardctl.py")]
    with tempfile.TemporaryDirectory() as logs, patch.dict(os.environ, {"RUMBLE_LOG_DIR": logs}):
        asyncio.run(main(Path(sys.argv[1]).resolve(), cli))
