"""Create and inspect the editable workspace for named Output steps."""
import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import stat
import tempfile
import time

from engine_paths import ENGINE_ROOT, WORKSPACE_ROOT
from output_transform_runtime import CONFIG_NAMES, execute_config


STARTER = "jaro.config.ts"
RESOURCES = ("jaro.d.ts", "tsconfig.json", "AGENTS.md")


def config_directory():
    configured = os.environ.get("XDG_CONFIG_HOME")
    prefix = Path(configured) if configured and Path(configured).is_absolute() else Path.home() / ".config"
    return prefix / "jaro"


def find_config(directory):
    candidates = []
    for name in sorted(CONFIG_NAMES):
        path = directory / name
        try:
            details = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(details.st_mode):
            raise ValueError(f"{name} must be a regular configuration file.")
        candidates.append(path)
    if len(candidates) > 1:
        raise ValueError("Keep one jaro.config.js, .mjs, .ts, or .mts in the configuration folder.")
    return next(iter(candidates), None)


def _write_new(path, source):
    # Link the complete file into place, without replacing a user's file or link.
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp") as pending:
        pending.write(source.read_bytes())
        pending.flush()
        try:
            os.link(pending.name, path)
        except FileExistsError:
            return False
    return True


def initialize_workspace(directory=None):
    directory = (directory or config_directory()).expanduser()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = directory.resolve()
    config = find_config(directory)
    kit = (ENGINE_ROOT / "transform-starter" if WORKSPACE_ROOT == ENGINE_ROOT
           else WORKSPACE_ROOT / "desktop" / "transforms" / "starter")
    created = []
    for name in RESOURCES:
        if _write_new(directory / name, kit / name):
            created.append(name)
    if config is None and _write_new(directory / STARTER, kit / STARTER):
        created.append(STARTER)
    config = find_config(directory)
    if config is None:
        raise ValueError("The configuration was removed while initializing its folder.")
    return {"directory": str(directory), "path": str(config), "created": created}


def _stamps(paths):
    stamps = []
    for path in paths:
        try:
            value = path.lstat()
            stamps.append((value.st_ino, value.st_dev, value.st_size, value.st_ctime_ns, value.st_mtime_ns))
        except OSError:
            stamps.append(None)
    return tuple(stamps)


@dataclass
class Inspection:
    config: Path
    review: dict
    paths: tuple
    stamp: tuple
    until: float


class ConfigInspector:
    """Share review work across Output views. Approvals always reload the source."""

    def __init__(self, helper):
        self.helper = helper
        self.cached = None
        self.lock = asyncio.Lock()

    async def inspect(self, path, *, fresh=False):
        config = Path(path)
        async with self.lock:
            entry = self.cached if self.cached and self.cached.config == config else None
            if entry and not fresh and time.monotonic() < entry.until:
                if await asyncio.to_thread(_stamps, entry.paths) == entry.stamp:
                    return entry.review
            try:
                result = await execute_config(self.helper, config)
            except ValueError as error:
                paths = entry.paths if entry else (config, config.parent)
                retained = entry.review.get("transforms", []) if entry else []
                review = {"kind": "embedded", "message": str(error), "transforms": retained, "stale": bool(retained)}
                ttl = 1
            else:
                paths = set()
                for name in result.pop("files", ()):
                    source = config.parent / name
                    paths.add(source)
                    paths.add(source.parent)
                paths = tuple(sorted(paths | {config, config.parent}))
                review = {"kind": "embedded", **result}
                # File timestamps are a display cache. The version review and run
                # paths independently read and hash the actual source files.
                ttl = 5
            stamp = await asyncio.to_thread(_stamps, paths)
            self.cached = Inspection(config, review, paths, stamp, time.monotonic() + ttl)
            return review
