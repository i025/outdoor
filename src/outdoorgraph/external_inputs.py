"""Content identities for explicitly declared local operator inputs.

These identities are cache inputs, not output integrity or authenticity checks.
No path is inferred from arbitrary Scene strings and no network resource is read.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import os
from pathlib import Path
import stat
from urllib.parse import unquote, urlsplit


def external_input_path(value, base_dir=None) -> Path:
    if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
        raise ValueError("External input path must be a non-empty local path")
    uri = os.fspath(value)
    if not isinstance(uri, str) or "\0" in uri:
        raise ValueError("External input path must be text without null bytes")
    parsed = urlsplit(uri)
    if parsed.scheme:
        if (parsed.scheme != "file" or parsed.netloc not in ("", "localhost")
                or parsed.query or parsed.fragment):
            raise ValueError("External inputs support only local paths and file URIs")
        uri = unquote(parsed.path)
    path = Path(uri).expanduser()
    if not path.is_absolute():
        if base_dir is None:
            raise ValueError("Relative external input needs an explicit external_input_base_dir")
        base = Path(base_dir).expanduser()
        if not base.is_absolute():
            raise ValueError("External input base directory must be absolute")
        path = base / path
    return path.resolve(strict=True)


def resolve_external_inputs(declarations, base_dir=None):
    """Return resolved paths and streaming byte identities for one node.

    Modification times are deliberately absent from identities: replacement at
    an unchanged path/size/mtime must invalidate the consuming branch.
    """
    if not isinstance(declarations, Mapping):
        raise ValueError("External inputs must map non-empty names to local paths")
    paths, identities = {}, {}
    for name, value in sorted(declarations.items()):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("External input names must be non-empty strings")
        path = external_input_path(value, base_dir)
        if not path.is_file():
            raise ValueError("External input must be a regular file: "+str(path))
        digest, size = hashlib.sha256(), 0
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("External input must be a regular file: "+str(path))
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
            after = os.fstat(stream.fileno())
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        current = path.stat()
        if any(getattr(before, key) != getattr(after, key) or getattr(after, key) != getattr(current, key)
               for key in fields) or size != after.st_size:
            raise ValueError("External input changed while reading: "+str(path))
        paths[name] = str(path)
        identities[name] = {"path": str(path), "size_bytes": size, "sha256": digest.hexdigest()}
    return paths, identities
