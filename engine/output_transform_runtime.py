"""The isolated JavaScript and TypeScript runner for named Output steps."""
import asyncio
from contextlib import suppress
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

from diagnostics import write_transform_event

from output_transform_operations import fetch, run_program
from output_transform_policy import DISABLED_MESSAGE, PROGRAM_MESSAGE, TransformPolicy, changed_message, current_policy, wait_for_policy_change


CONFIG_NAMES = frozenset(("jaro.config.js", "jaro.config.mjs", "jaro.config.ts", "jaro.config.mts"))
CAPABILITIES = ("network", "programs", "pageContext")


def is_embedded_config(path):
    return Path(path).name in CONFIG_NAMES


async def _send(pipe, document):
    pipe.write(json.dumps(document, ensure_ascii=False).encode() + b"\n")
    await pipe.drain()


def _bytes(text):
    return len(text.encode("utf-8", errors="replace")) if isinstance(text, str) else 0


def _decoded_size(encoded):
    return len(encoded) * 3 // 4 - encoded.count("=") if isinstance(encoded, str) else 0


def _diagnostic_name(text):
    return "".join(char for char in text[:253] if char.isprintable())


def _operation_event(kind, spec, reply, started):
    if not isinstance(spec, dict):
        return
    response = reply.get("response") or {}
    fields = {"durationMs": round((time.monotonic() - started) * 1000), "outcome": "error" if "error" in reply else "completed"}
    if kind == "fetch":
        try:
            host = urlsplit(spec.get("url") or "").hostname
        except (ValueError, TypeError, AttributeError):
            host = None
        fields.update(host=_diagnostic_name(host or ""), status=response.get("status"),
                      requestBytes=_bytes(spec.get("body")), responseBytes=_decoded_size(response.get("body")))
        write_transform_event("network", **fields)
    else:
        name = spec.get("file")
        program = "sh" if spec.get("shell") else Path(name).name if isinstance(name, str) else ""
        fields.update(program=_diagnostic_name(program), exitCode=response.get("code"), timedOut=response.get("timedOut") is True,
                      inputBytes=_bytes(spec.get("input")), stdoutBytes=_decoded_size(response.get("stdout")),
                      stderrBytes=_decoded_size(response.get("stderr")))
        write_transform_event("program", **fields)


async def _conversation(process, request, workspace, grants, still_approved, policy):
    operations = {}

    async def invoke(kind, job, spec):
        started = time.monotonic()
        try:
            if policy != current_policy():
                raise ValueError(changed_message())
            if kind != "fetch" and policy == TransformPolicy.LOOPBACK:
                raise ValueError(PROGRAM_MESSAGE)
            if (not grants.get("network" if kind == "fetch" else "programs")
                    or (still_approved is not None and not still_approved())):
                raise ValueError("Allow this operation in Dictation Output.")
            if not isinstance(spec, dict):
                raise ValueError("Invalid transformation operation.")
            response = await (fetch(spec, loopback=policy == TransformPolicy.LOOPBACK)
                              if kind == "fetch" else run_program(spec, workspace))
            reply = {"id": job, "response": response}
        except asyncio.CancelledError:
            write_transform_event("network" if kind == "fetch" else "program", outcome="cancelled",
                                  durationMs=round((time.monotonic() - started) * 1000))
            raise
        except ValueError as error:
            reply = {"id": job, "error": str(error)}
        except OSError:
            reply = {"id": job, "error": "The transformation operation failed."}
        _operation_event(kind, spec, reply, started)
        with suppress(BrokenPipeError, ConnectionResetError):
            await _send(process.stdin, reply)

    def retired(job, task):
        operations.pop(job, None)
        if not task.cancelled():
            task.exception()

    try:
        await _send(process.stdin, request)
        last_id = 0
        logged = 0
        while raw := await process.stdout.readline():
            result = json.loads(raw)
            if not isinstance(result, dict):
                break
            kind = result.get("op")
            if kind is None:
                return result
            if kind == "console":
                level, message = result.get("level"), result.get("message")
                if logged < 32 and level in ("debug", "info", "warn", "error") and isinstance(message, str):
                    logged += 1
                    bounded = message.encode("utf-8")[:4096].decode("utf-8", errors="ignore")
                    write_transform_event("console", level=level, message=bounded)
                continue
            job = result.get("id")
            if type(job) is not int or job <= 0:
                break
            if kind == "cancel":
                if task := operations.get(job):
                    task.cancel()
            elif kind in ("fetch", "exec") and job > last_id and len(operations) < 64:
                last_id = job
                task = asyncio.create_task(invoke(kind, job, result.get("spec")))
                operations[job] = task
                task.add_done_callback(lambda finished, job=job: retired(job, finished))
            else:
                break
        return None
    finally:
        tasks = list(operations.values())
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _until_revoked(check):
    while check():
        await asyncio.sleep(0.04)


async def execute_config(helper, path, take=None, *, still_approved=None, log_setup=False):
    policy = current_policy()
    if policy == TransformPolicy.DISABLED:
        raise ValueError(DISABLED_MESSAGE)
    config = Path(path)
    if not config.is_absolute() or not is_embedded_config(config):
        raise ValueError("Select a Jaro JavaScript or TypeScript configuration.")
    if helper is None:
        raise ValueError("The embedded transformation runtime is unavailable.")
    request = {"entry": config.name, "log_setup": log_setup}
    grants = {}
    if take is not None:
        grants = {key: take.get("grants", {}).get(key) is True for key in CAPABILITIES}
        context = take.get("context")
        if not isinstance(context, dict):
            context = {}
        if not grants["pageContext"]:
            context = {key: value for key, value in context.items() if key in ("source", "application", "browserHost")}
        request["run"] = {**take, "context": context, "grants": grants, "loopback": policy == TransformPolicy.LOOPBACK}
    try:
        process = await asyncio.create_subprocess_exec(
            str(helper), cwd=config.parent, env={}, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=1 << 20,
        )
    except OSError as error:
        raise ValueError("Could not open the transformation configuration or runtime.") from error
    started = time.monotonic()
    result = None
    conversation = asyncio.create_task(_conversation(process, request, config.parent, grants, still_approved, policy))
    watchers = {asyncio.create_task(wait_for_policy_change(policy)): changed_message}
    if still_approved is not None:
        watchers[asyncio.create_task(_until_revoked(still_approved))] = (
            lambda: "Approval for the output script changed. Review it in Dictation Output."
        )
    try:
        async with asyncio.timeout(4 if take is None else 64):
            done, _ = await asyncio.wait((conversation, *watchers), return_when=asyncio.FIRST_COMPLETED)
            for task, reason in watchers.items():
                if task in done:
                    raise ValueError(reason())
            result = await asyncio.shield(conversation)
    except TimeoutError as error:
        raise ValueError("The transformation runtime exceeded its time limit.") from error
    except (json.JSONDecodeError, UnicodeError, BrokenPipeError, ConnectionResetError) as error:
        raise ValueError("The transformation runtime returned an invalid result.") from error
    finally:
        for task in watchers:
            task.cancel()
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        if not process.stdin.is_closing():
            process.stdin.transport.abort()
        conversation.cancel()
        await asyncio.gather(conversation, *watchers, return_exceptions=True)
        await asyncio.gather(process.stdout.read(), process.wait(), return_exceptions=True)
        if take is not None:
            completed = isinstance(result, dict) and not result.get("error") and isinstance(result.get("text"), str)
            step = take.get("step")
            write_transform_event("finished" if completed else "failed",
                                  step=_diagnostic_name(step) if isinstance(step, str) else "",
                                  durationMs=round((time.monotonic() - started) * 1000))
    if not isinstance(result, dict):
        raise ValueError("The transformation runtime returned an invalid result.")
    if error := result.get("error"):
        raise ValueError(error)
    return result
