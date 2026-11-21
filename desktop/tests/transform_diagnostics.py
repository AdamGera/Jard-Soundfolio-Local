"""Verify explicit script console output and privacy of automatic diagnostics."""
import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

from diagnostics import EVENT_LOG
from dictation_output import process
from output_transform_runtime import execute_config


async def main(helper, cli):
    async def serve(reader, writer):
        try:
            headers = (await reader.readuntil(b"\r\n\r\n")).decode()
            size = next(int(line.split(":")[1]) for line in headers.splitlines()
                        if line.lower().startswith("content-length:"))
            await reader.readexactly(size)
            writer.write(b"HTTP/1.1 201 Created\r\nContent-Length: 16\r\nConnection: close\r\n\r\nPrivate response")
            await writer.drain()
        finally:
            writer.close()
            with suppress(ConnectionResetError):
                await writer.wait_closed()

    async with await asyncio.start_server(serve, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp).resolve()
            config = workspace / "jaro.config.ts"
            logs = workspace / "logs"
            file = logs / EVENT_LOG
            env = {"RUMBLE_LOG_DIR": str(logs)}
            config.write_text("""
import { execFile } from "node:child_process";
import { promisify } from "node:util";
const run = promisify(execFile);
console.log("Explicit configuration output");
export default { transformations: {
  mixed: { transform: async (text: string) => {
    console.info("Chosen note", { ok: true });
    console.error("Literal line\nSecond line");
    await fetch("http://127.0.0.1:PORT/private-url?query=SecretQuery", { method: "POST", body: text });
    await run("/usr/bin/printf", ["%s", "PrivateArgument"], { input: text, env: { SAMPLE: "PrivateEnvironment" } });
    return "finished";
  } },
  rejected: { transform(text: string) { throw new Error(text + " PrivateException") } },
  chatter: { transform() {
    console.debug("é".repeat(3000));
    for (let i = 0; i < 40; i++) console.warn("Message " + i);
    return "bounded";
  } },
} };
""".replace("PORT", str(port)).replace('line\nSecond', 'line\\nSecond'))
            with patch.dict(os.environ, env), patch("output_transform_network.proxy_for", return_value=None):
                review = await execute_config(helper, config)
                assert not file.exists(), "Background review wrote console output."
                checked = subprocess.run([*cli, "transforms", "check", "--directory", str(workspace), "--json"],
                                         capture_output=True, text=True, timeout=10)
                assert checked.returncode == 0, checked.stderr
                assert json.loads(checked.stdout)["digest"] == review["digest"]
                options = dict(script=config, script_runtime=helper, script_digest=review["digest"],
                               script_grants={"programs": True, "network": True})
                result = await process("PrivateDictation", ("local:mixed",), **options)
                assert result.text == "finished" and not result.warnings, result
                result = await process("PrivateDictation", ("local:rejected",), **options)
                assert result.text == "PrivateDictation" and result.warnings, result
                result = await process("PrivateDictation", ("local:chatter",), **options)
                assert result.text == "bounded" and not result.warnings, result
                output = subprocess.check_output([*cli, "logs", "-n", "100"], text=True)
            events = [json.loads(line) for line in output.splitlines()]
            assert all(event["kind"] == "transformation" for event in events), events[:4]
            for secret in ("PrivateDictation", "PrivateArgument", "PrivateEnvironment", "PrivateException",
                           "Private response", "private-url", "SecretQuery"):
                assert secret not in output
            console = [event for event in events if event["phase"] == "console"]
            assert len(console) == 35, len(console)
            assert [event["level"] for event in console[:4]] == ["info", "info", "error", "debug"]
            assert console[0]["message"] == "Explicit configuration output"
            assert console[1]["message"] == 'Chosen note {"ok":true}'
            assert console[2]["message"] == "Literal line\nSecond line"
            assert len(console[3]["message"].encode()) == 4096
            request, = [event for event in events if event["phase"] == "network"]
            assert request["host"] == "127.0.0.1" and request["status"] == 201, request
            assert request["requestBytes"] == 16 and request["responseBytes"] == 16, request
            program, = [event for event in events if event["phase"] == "program"]
            assert program["program"] == "printf" and program["exitCode"] == 0 and program["stdoutBytes"] == 15, program
            assert any(event["step"] == "rejected" for event in events if event["phase"] == "failed")
            assert file.stat().st_mode & 0o077 == 0
    print("Manual check and take console output, private log bounds, and redacted operation diagnostics passed.")


if __name__ == "__main__":
    cli = sys.argv[2:] or [sys.executable, str(Path(__file__).resolve().parents[2] / "engine/jardctl.py")]
    asyncio.run(main(Path(sys.argv[1]).resolve(), cli))
