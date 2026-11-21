"""Check versioned transformation capabilities against a disposable Mac fixture."""
import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from dictation_output import process
from output_transform_runtime import execute_config


async def main(helper):
    requests = []
    slow_started = asyncio.Event()
    slow_closed = asyncio.Event()

    async def serve(reader, writer):
        try:
            headers = (await reader.readuntil(b"\r\n\r\n")).decode()
            method, path, _ = headers.splitlines()[0].split()
            length = next((int(line.split(":")[1]) for line in headers.splitlines()
                           if line.lower().startswith("content-length:")), 0)
            data = await reader.readexactly(length)
            requests.append((method, path, data))
            if path == "/slow":
                slow_started.set()
                try:
                    await reader.read()
                finally:
                    slow_closed.set()
                return
            if path == "/redirect":
                writer.write(b"HTTP/1.1 302 Found\r\nLocation: /binary\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            else:
                body = bytes([0, 128, 255]) if path == "/binary" else json.dumps(
                    {"text": data.decode()}).encode()
                writer.write(f"HTTP/1.1 200 OK\r\nX-Fixture: yes\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
            await writer.drain()
        finally:
            writer.close()
            with suppress(ConnectionResetError):
                await writer.wait_closed()

    async with await asyncio.start_server(serve, "127.0.0.1", 0) as server:
        url = "http://127.0.0.1:" + str(server.sockets[0].getsockname()[1])
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory).resolve()
            path = folder / "jaro.config.ts"
            path.write_text("""
import { exec, execFile } from "node:child_process";
import { promisify } from "node:util";

const run = promisify(execFile);
const shell = promisify(exec);
const root = fixtureURL;
setTimeout(() => fetch(root + "/load-timer").catch(() => {}), 1);
export default { transformations: {
  net: { transform: async (text: string) => {
    const echo = await fetch(root + "/echo", { method: "POST", headers: { "x-input": "yes" }, body: text });
    const redirected = await fetch(root + "/redirect");
    const bytes = [...new Uint8Array(await redirected.arrayBuffer())];
    const {text: received} = await echo.json();
    if (!echo.ok || !redirected.url.endsWith("/binary") || redirected.headers.get("X-Fixture") !== "yes")
      throw new Error("Incorrect response");
    return received + " " + bytes.join(",");
  } },
  exec: { transform: async (text: string) => {
    const { stdout } = await run("/bin/cat", [], { input: text });
    const { stdout: value } = await shell('printf %s "$FIXTURE"', { env: { FIXTURE: "approved" } });
    await new Promise((resolve, reject) => execFile("/bin/sh", ["-c", "printf err >&2; exit 7"], (error, out, err) => {
      if (error?.code !== 7 || err !== "err") reject(new Error("Incorrect process result"));
      else resolve(out);
    }));
    return stdout + " " + value;
  } },
  limit: { transform: async () => {
    try { await run("/usr/bin/printf", ["a long string"], { maxBuffer: 2 }); }
    catch (error) { return error.code; }
    return "unexpected success";
  } },
  timeout: { transform: async () => {
    try { await run("/bin/sleep", ["20"], { timeout: 20 }); }
    catch (error) { return error.killed && error.signal === "SIGTERM" ? "expired" : "wrong timeout"; }
    return "unexpected success";
  } },
  cancel: { transform: async () => {
    const controller = new AbortController();
    const request = fetch(root + "/slow", { signal: controller.signal });
    setTimeout(() => controller.abort(), 250);
    try { await request; } catch (error) { return error.name; }
    return "unexpected success";
  } },
  revoke: { transform: async () => {
    await shell('sleep 60 & echo $! > child.pid; wait');
    return "unexpected success";
  } },
  fire: { transform: async () => {
    shell('sleep 60 & echo $! > fire.pid; wait').catch(() => {});
    await new Promise(resolve => setTimeout(resolve, 250));
    return "finished";
  } },
} };
""".replace("fixtureURL", json.dumps(url)).replace('fetch(root + "/echo"', f'fetch("{url}/echo"'))
            review = await execute_config(helper, path)
            assert review["permissions"]["network"] and review["permissions"]["programs"], review
            assert review["permissions"]["hosts"] == ["127.0.0.1"] and "sh" in review["permissions"]["commands"], review
            assert review["permissions"]["partial"], review
            options = {"script": path, "script_runtime": helper, "script_digest": review["digest"]}

            async def take(identifier, allowed, **extra):
                return await process("fixture", (f"local:{identifier}",), **options, script_grants=allowed, **extra)

            for identifier in ("net", "exec"):
                denied = await take(identifier, {})
                assert denied.text == "fixture" and denied.warnings, denied
            assert (await take("net", {"programs": True})).text == "fixture"
            assert (await take("exec", {"network": True})).text == "fixture"
            assert not requests
            assert (await take("net", {"network": True})).text == "fixture 0,128,255"
            assert [event[1] for event in requests] == ["/echo", "/redirect", "/binary"], requests
            assert (await take("exec", {"programs": True})).text == "fixture approved"
            for _ in range(12):
                limited = await take("limit", {"programs": True})
                assert limited.text == "ERR_CHILD_PROCESS_STDIO_MAXBUFFER", limited
            assert (await take("timeout", {"programs": True})).text == "expired"
            assert (await take("cancel", {"network": True})).text == "AbortError"
            await asyncio.wait_for(slow_started.wait(), timeout=1)
            await asyncio.wait_for(slow_closed.wait(), timeout=1)

            approved = True
            task = asyncio.create_task(take("revoke", {"programs": True}, script_allowed=lambda: approved))
            pid_path = folder / "child.pid"
            async with asyncio.timeout(3):
                while not pid_path.exists() or not pid_path.stat().st_size:
                    await asyncio.sleep(0.02)
            pid = int(pid_path.read_text())
            os.kill(pid, 0)
            approved = False
            revoked = await asyncio.wait_for(task, timeout=2)
            assert revoked.text == "fixture" and "Approval" in revoked.warnings[0], revoked
            await wait_for_exit(pid)
            result = await take("fire", {"programs": True})
            assert result.text == "finished" and not result.warnings, result
            await wait_for_exit(int((folder / "fire.pid").read_text()))

            path.write_text(path.read_text().replace("fixtureURL", "otherURL") + "\n// new version\n")
            prior = list(requests)
            changed = await take("net", {"network": True})
            assert changed.text == "fixture" and "configuration changed" in changed.warnings[0], changed
            assert requests == prior
    print("Network and program review, separate grants, async operations, limits, cancellations, and revocation passed.")


async def wait_for_exit(pid):
    async with asyncio.timeout(2):
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            await asyncio.sleep(0.02)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as logs, patch.dict(os.environ, {"RUMBLE_LOG_DIR": logs}), \
            patch("output_transform_network.proxy_for", return_value=None):
        asyncio.run(main(Path(sys.argv[1]).resolve()))
