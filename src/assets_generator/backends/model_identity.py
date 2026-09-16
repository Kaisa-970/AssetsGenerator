from __future__ import annotations

import hashlib
from pathlib import Path


def snapshot_digest(snapshot: Path) -> str:
    ignored = {"cache", "caches", "logs", "log", "tmp", "temp", "__pycache__"}
    files = sorted(
        p
        for p in snapshot.rglob("*")
        if p.is_file()
        and not any(part.lower() in ignored for part in p.relative_to(snapshot).parts)
    )
    if not files:
        raise ValueError(f"empty model snapshot: {snapshot}")
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
