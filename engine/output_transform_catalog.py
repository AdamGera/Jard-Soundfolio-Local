"""Versioned file sets for named Dictation Output transformations."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import unicodedata


TRANSFORM_ID = re.compile(r"[a-z][a-z0-9-]{0,47}\Z")
CATALOG_LIMIT = 128 * 1024
FILE_LIMIT = 64 * 1024 * 1024
CHUNK = 128 * 1024


@dataclass(frozen=True)
class Transformation:
    id: str
    name: str
    description: str
    entry: str
    files: tuple[str, ...]
    timeout: int

    def summary(self):
        return {"id": self.id, "name": self.name, "description": self.description}


def is_step(value):
    return value.startswith("local:") and TRANSFORM_ID.fullmatch(value.removeprefix("local:")) is not None


def _relative_file(value):
    if not isinstance(value, str) or not value or len(value) > 240 or any(ord(char) < 32 for char in value):
        raise ValueError("Use a relative file path in the transformation catalog.")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("Transformation files must stay inside the catalog folder.")
    return str(path)


def _parse(raw):
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("A transformation catalog repeats a key.")
        return result

    try:
        document = json.loads(raw, object_pairs_hook=unique)
        rows = document["transformations"]
    except (ValueError, TypeError, KeyError) as error:
        raise ValueError("The transformation catalog must contain a transformations object.") from error
    if not isinstance(rows, dict) or not 1 <= len(rows) <= 32:
        raise ValueError("Define between one and 32 local transformations.")
    transforms = {}
    for identifier, row in rows.items():
        if not TRANSFORM_ID.fullmatch(identifier) or not isinstance(row, dict):
            raise ValueError("Each local transformation needs a lowercase identifier.")
        name = row.get("name", identifier)
        description = row.get("description", "")
        timeout = row.get("timeout", 1000)
        files = row.get("files", [])
        if (
            not isinstance(name, str) or not name.strip() or len(name) > 80 or
            not isinstance(description, str) or len(description) > 240 or
            type(timeout) is not int or not 1 <= timeout <= 60_000 or
            not isinstance(files, list) or len(files) > 32
        ):
            raise ValueError(f"Check the name, files, and timeout for {identifier}.")
        entry = _relative_file(row.get("entry"))
        transforms[identifier] = Transformation(identifier, name.strip(), description.strip(), entry,
                                                 tuple(_relative_file(file) for file in files), timeout)
    return transforms


@contextmanager
def _catalog_file(root, name):
    parts = PurePosixPath(name).parts
    parent = os.dup(root)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    with os.fdopen(descriptor, "rb") as file:
        details = os.fstat(file.fileno())
        if not stat.S_ISREG(details.st_mode):
            raise ValueError(f"{name} must be a regular file.")
        yield file, details


def inspect_catalog(path, destination=None, copy_attributes=None):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("Select a local transformation catalog in Dictation Output.")
    root = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with _catalog_file(root, path.name) as (source, _):
            raw = source.read(CATALOG_LIMIT + 1)
        if len(raw) > CATALOG_LIMIT:
            raise ValueError("The transformation catalog is larger than 128 KB.")
        transforms = _parse(raw)
        executables = {transform.entry for transform in transforms.values()}
        files = executables | {file for transform in transforms.values() for file in transform.files}
        if len(files) > 64:
            raise ValueError("Use up to 64 declared files besides the catalog.")
        names = {}
        for name in (*files, path.name):
            for item in (PurePosixPath(name), *PurePosixPath(name).parents):
                key = unicodedata.normalize("NFC", str(item)).casefold()
                if names.setdefault(key, item) != item:
                    raise ValueError("Declared file paths cannot differ only by case or Unicode form.")
        if path.name in files or any(PurePosixPath(path.name) in PurePosixPath(file).parents for file in files):
            raise ValueError("The catalog cannot also be an entry file or a dependency.")
        if destination:
            (destination / path.name).write_bytes(raw)
        digest = hashlib.sha256(hashlib.sha256(raw).digest())
        remaining = FILE_LIMIT
        for name in sorted(files):
            with _catalog_file(root, name) as (source, details):
                if name in executables and not details.st_mode & 0o111:
                    raise ValueError(f"{name} must be executable.")
                if details.st_size > remaining:
                    raise ValueError("The transformation file set exceeds 64 MB.")
                target = destination / name if destination else None
                if target:
                    target.parent.mkdir(parents=True, exist_ok=True)
                content = hashlib.sha256()
                output = target.open("wb") if target else None
                try:
                    while chunk := source.read(CHUNK):
                        remaining -= len(chunk)
                        if remaining < 0:
                            raise ValueError("The transformation file set exceeds 64 MB.")
                        content.update(chunk)
                        if output:
                            output.write(chunk)
                    if output:
                        if copy_attributes:
                            copy_attributes(source, output)
                        os.fchmod(output.fileno(), 0o700 if details.st_mode & 0o111 else 0o600)
                finally:
                    if output:
                        output.close()
            digest.update(name.encode("utf-8") + b"\0")
            digest.update(b"\x01" if details.st_mode & 0o111 else b"\x00")
            digest.update(content.digest())
        return digest.hexdigest(), transforms
    except OSError as error:
        raise ValueError("A declared transformation file is missing or unavailable.") from error
    finally:
        os.close(root)
