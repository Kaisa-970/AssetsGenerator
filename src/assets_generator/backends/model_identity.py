from __future__ import annotations

import hashlib
from pathlib import Path


def snapshot_files(snapshot: Path) -> list[Path]:
    ignored = {"cache", "caches", "logs", "log", "tmp", "temp", "__pycache__", ".cache", ".git"}
    files = sorted(
        p
        for p in snapshot.rglob("*")
        if p.is_file()
        and not any(part.lower() in ignored for part in p.relative_to(snapshot).parts)
    )
    if not files:
        raise ValueError(f"empty model snapshot: {snapshot}")
    return files


def snapshot_state(snapshot: Path) -> list[list[str | int]]:
    result: list[list[str | int]] = []
    for path in snapshot_files(snapshot):
        stat = path.stat()
        result.append(
            [
                path.relative_to(snapshot).as_posix(),
                str(path.resolve()),
                stat.st_dev,
                stat.st_ino,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            ]
        )
    return result


def snapshot_digest(snapshot: Path) -> str:
    files = snapshot_files(snapshot)
    identity = hashlib.sha256()
    for path in files:
        content = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                content.update(chunk)
        identity.update(path.relative_to(snapshot).as_posix().encode())
        identity.update(b"\0")
        identity.update(content.digest())
    return f"sha256:{identity.hexdigest()}"
