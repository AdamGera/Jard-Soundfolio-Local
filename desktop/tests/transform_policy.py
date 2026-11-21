"""Check managed transform policy and proxy routing without changing Mac settings."""
import asyncio
from contextlib import suppress
from pathlib import Path
import json
import os
import socket
import sys
import tempfile
from unittest.mock import AsyncMock, patch

import httpcore
import httpx

from dictation_output import inspect_output_script, process
from output_transform_network import LoopbackBackend, _system_proxy, proxy_for
from output_transform_operations import fetch
from output_transform_policy import DISABLED_MESSAGE, LOOPBACK_MESSAGE, TransformPolicy, current_policy
from output_transform_runtime import execute_config


async def address_check():
    loop = asyncio.get_running_loop()
    local = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.2", 443))
    remote = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))
    marker = object()
    connector = AsyncMock(return_value=marker)
    backend = LoopbackBackend()
    with patch.object(httpcore.AnyIOBackend, "connect_tcp", connector):
        with patch.object(loop, "getaddrinfo", AsyncMock(return_value=[local])):
            assert await backend.connect_tcp("local.example.test", 443) is marker
            connector.assert_awaited_once_with("127.0.0.2", 443, local_address=None, socket_options=None)
        connector.reset_mock()
        with patch.object(loop, "getaddrinfo", AsyncMock(return_value=[local, remote])):
            try:
                await backend.connect_tcp("mixed.example.test", 443)
            except ValueError as error:
                assert str(error) == LOOPBACK_MESSAGE
            else:
                raise AssertionError("A mixed DNS result was accepted.")
            connector.assert_not_called()


async def main(helper):
    requests = []
    waiting = asyncio.Event()
    terminated = asyncio.Event()

    async def serve(reader, writer):
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            first = request.splitlines()[0].decode()
            path = first.split()[1]
            requests.append(path)
            if path == "/slow":
                waiting.set()
                try:
                    await reader.read()
                finally:
                    terminated.set()
                return
            if path == "/leave":
                # Reserved documentation address. The loopback backend rejects it before connecting.
                writer.write(b"HTTP/1.1 302 Found\r\nLocation: http://192.0.2.1/away\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            elif path == "http://example.test/switch":
                host, port = writer.get_extra_info("sockname")[:2]
                writer.write(f"HTTP/1.1 302 Found\r\nLocation: http://{host}:{port}/arrived\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
            else:
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nlocal")
            await writer.drain()
        finally:
            writer.close()
            with suppress(BrokenPipeError, ConnectionResetError):
                await writer.wait_closed()

    await address_check()
    assert current_policy() in tuple(TransformPolicy)
    system_proxy = _system_proxy(httpx.URL("http://localhost"))
    assert system_proxy is None or isinstance(system_proxy, str)
    async with await asyncio.start_server(serve, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        local_url = f"http://127.0.0.1:{port}"
        request = {"url": "http://example.test/from-proxy", "method": "GET", "headers": []}
        with patch.dict(os.environ, {"HTTP_PROXY": f"127.0.0.1:{port}"}, clear=True):
            response = await fetch(request)
            assert response["status"] == 200 and requests[-1] == "http://example.test/from-proxy"
            with patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,.example.test:80"}):
                assert proxy_for(httpx.URL("http://www.example.test")) is None
                await fetch({**request, "url": local_url + "/direct"})
                assert requests[-1] == "/direct"
                with patch.dict(os.environ, {"NO_PROXY": "127.0.0.1"}):
                    await fetch({**request, "url": "http://example.test/switch"})
                    assert requests[-2:] == ["http://example.test/switch", "/arrived"], requests
        with patch.dict(os.environ, {}, clear=True), patch("output_transform_network._system_proxy", return_value=local_url) as system:
            await fetch(request)
            system.assert_called_once()
            assert requests[-1] == "http://example.test/from-proxy"
        with patch.dict(os.environ, {"HTTP_PROXY": local_url}, clear=True), patch("output_transform_network.proxy_for") as proxy:
            await fetch({**request, "url": local_url + "/loop"}, loopback=True)
            assert requests[-1] == "/loop"
            try:
                await fetch({**request, "url": local_url + "/leave"}, loopback=True)
            except ValueError as error:
                assert str(error) == LOOPBACK_MESSAGE
            else:
                raise AssertionError("An off-Mac redirect was followed.")
            assert requests[-1] == "/leave"
            proxy.assert_not_called()

        with tempfile.TemporaryDirectory() as folder, patch("output_transform_policy._forced") as forced:
            root = Path(folder).resolve()
            config = root / "jaro.config.ts"
            config.write_text("""
import { execFile } from "node:child_process"
import { promisify } from "node:util"

const run = promisify(execFile)
export default { transformations: {
  verify: { transform: async () => (await run("/bin/cat", [], { input: "program" })).stdout },
  local: { transform: async () => (await fetch(fixture + "/local")).text() },
  slow: { transform: async () => (await fetch(fixture + "/slow")).text() },
} };
""".replace("fixture", json.dumps(local_url)))
            imposed = set()
            forced.side_effect = lambda name: name in imposed
            review = await execute_config(helper, config)
            options = dict(script=config, script_runtime=helper, script_digest=review["digest"],
                           script_grants={"programs": True, "network": True})
            assert (await process("original", ("local:verify",), **options)).text == "program"
            script = root / "legacy"
            script.write_text("#!/bin/sh\n/bin/echo legacy\n")
            script.chmod(0o700)
            legacy = dict(script=script, script_digest=inspect_output_script(script))
            assert (await process("original", ("script",), **legacy)).text == "legacy"

            pending = root / "pending"
            pid_path = root / "process.pid"
            pending.write_text(f'#!/bin/sh\necho $$ > "{pid_path}"\n/bin/sleep 20\n')
            pending.chmod(0o700)
            legacy_job = asyncio.create_task(process("original", ("script",), script=pending,
                                                     script_digest=inspect_output_script(pending)))
            async with asyncio.timeout(2):
                while not pid_path.exists() or not pid_path.stat().st_size:
                    await asyncio.sleep(0.02)
            pid = int(pid_path.read_text())
            os.kill(pid, 0)
            imposed.add("RestrictTransformsToLoopback")
            stopped = await asyncio.wait_for(legacy_job, timeout=2)
            assert stopped.text == "original" and "restrictions changed" in stopped.warnings[0], stopped
            async with asyncio.timeout(2):
                while True:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    await asyncio.sleep(0.02)
            shell = await process("original", ("script",), **legacy)
            assert shell.text == "original" and "Programs are disabled" in shell.warnings[0], shell
            program = await process("original", ("local:verify",), **options)
            assert program.text == "original" and "disabled transformation programs" in program.warnings[0], program
            local = await process("original", ("local:local",), **options)
            assert local.text == "local" and not local.warnings, local

            task = asyncio.create_task(process("original", ("local:slow",), **options))
            await asyncio.wait_for(waiting.wait(), timeout=4)
            imposed.add("DisableTransforms")
            fallback = await asyncio.wait_for(task, timeout=3)
            assert fallback.text == "original" and DISABLED_MESSAGE in fallback.warnings, fallback
            assert await asyncio.wait_for(terminated.wait(), timeout=2)

            async def rewrite(text):
                return text + " rewritten"

            result = await process("Hello!", ("lowercase", "replace", "local:verify", "no-trailing-punctuation", "rewrite"),
                                   "Hello => Goodbye", rewrite=rewrite, **options)
            assert result.text == "Goodbye! rewritten" and not result.warnings, result
            try:
                await execute_config(None, config)
            except ValueError as error:
                assert str(error) == DISABLED_MESSAGE
            else:
                raise AssertionError("A disabled configuration was loaded.")
    print("Proxy selection, redirect and DNS fencing, managed grants, and in-flight policy changes passed.")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as logs, patch.dict(os.environ, {"RUMBLE_LOG_DIR": logs}):
        asyncio.run(main(Path(sys.argv[1]).resolve()))
