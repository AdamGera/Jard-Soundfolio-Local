"""Verify a local or packaged transformation helper through the engine API."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from dictation_output import process
from output_transform_runtime import execute_config


async def main(helper):
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder).resolve()
        workspace = root / "workspace"
        workspace.mkdir()
        config = workspace / "jaro.config.ts"
        dependency = workspace / "label.ts"
        settings = workspace / "settings.json"
        selected = "local:add-label"

        settings.write_text('{"suffix":"!"}')
        dependency.write_text('export const prefix: string = "REVIEW";\n')
        config.write_text("""
import { defineJaroConfig } from "@jaro/commands"
import { prefix } from "./label"
import settings from "./settings.json"

interface Context { source?: string; browserHost?: string; windowTitle?: string }

export default defineJaroConfig({
  transformations: {
    "add-label": {
      name: "Add label",
      description: "Label the transcript",
      async transform(text: string, context: Context): Promise<string> {
        if (typeof process !== "undefined" || typeof require !== "undefined" || context.windowTitle) {
          throw new Error("The context exposed an unexpected field");
        }
        await new Promise(resolve => setTimeout(resolve, 2));
        return prefix + ": " + text + " " + context.browserHost + settings.suffix;
      }
    }
  }
})
""")
        review = await execute_config(helper, config)
        assert review["transforms"] == [{"id": "add-label", "name": "Add label", "description": "Label the transcript"}], review
        options = dict(script=config, script_digest=review["digest"], script_runtime=helper,
                       script_context={"source": "dictation", "browserHost": "example.test", "windowTitle": "private"})
        result = await process("HELLO", ("lowercase", selected), **options)
        assert result.text == "REVIEW: hello example.test!" and not result.warnings, result
        preview = await process("HELLO", ("lowercase", selected), builtins_only=True, **options)
        assert preview.text == "hello" and not preview.warnings, preview

        assert (await execute_config(helper, config))["digest"] == review["digest"]
        dependency.write_text('export const prefix: string = "CHANGED";\n')
        changed = await process("HELLO", ("lowercase", selected), **options)
        assert changed.text == "HELLO" and "configuration changed" in changed.warnings[0], changed
        options["script_digest"] = (await execute_config(helper, config))["digest"]
        changed = await process("HELLO", ("lowercase", selected), **options)
        assert changed.text == "CHANGED: hello example.test!", changed
        permissions = iter((True, False))
        revoked = await process("HELLO", (selected,), **options, script_allowed=lambda: next(permissions))
        assert revoked.text == "HELLO" and "Approval" in revoked.warnings[0], revoked

        javascript = workspace / "jaro.config.js"
        javascript.write_text("""
export default { transformations: {
  bounded: { timeout: 25, transform() { while (true) {} } },
  late: { async transform() { return (await import("./label.ts")).prefix } },
  rejected: { transform() { return 4 } }
} };
""")
        entries = await execute_config(helper, javascript)
        for identifier, warning in (("bounded", "25 ms deadline"), ("late", "could not return text"),
                                    ("rejected", "could not return text")):
            result = await process("original", (f"local:{identifier}",), script=javascript,
                                   script_digest=entries["digest"], script_runtime=helper)
            assert result.text == "original" and warning in result.warnings[0], result

        javascript.write_text('await import("./label.ts"); export default {};')
        try:
            await execute_config(helper, javascript)
        except ValueError as error:
            assert "modules can only load" in str(error), error
        else:
            raise AssertionError("Dynamic import ran while inspecting the configuration.")

        outside = root / "outside.ts"
        outside.write_text('export const secret = "outside";')
        linked = workspace / "linked.ts"
        linked.symlink_to(outside)
        for name in ("./linked.ts", "../outside.ts", "node:fs"):
            javascript.write_text(f'import "{name}"; export default {{}};')
            try:
                await execute_config(helper, javascript)
            except ValueError as error:
                assert "cannot import" in str(error), error
            else:
                raise AssertionError(f"Unexpected import allowed: {name}")
    print("Embedded JS and TS, static imports, timers, approval, metadata, and output fallback passed.")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as logs, patch.dict(os.environ, {"RUMBLE_LOG_DIR": logs}):
        asyncio.run(main(Path(sys.argv[1]).resolve()))
