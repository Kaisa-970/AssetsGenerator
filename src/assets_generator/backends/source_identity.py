from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

try:
    from ..serialization import sha256_bytes
except ImportError:

    def sha256_bytes(data: bytes) -> str:
        return f"sha256:{hashlib.sha256(data).hexdigest()}"


def backend_source_identity(repo: Path) -> dict[str, Any]:
    absolute = repo.expanduser().resolve()
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=absolute,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        tracked_diff = subprocess.run(
            ["git", "diff", "--binary", "HEAD"],
            cwd=absolute,
            capture_output=True,
            check=True,
        ).stdout
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=absolute,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError(f"cannot identify backend repository {absolute}: {error}") from error
    identity = hashlib.sha256()
    identity.update(revision.encode())
    identity.update(b"\0")
    identity.update(tracked_diff)
    untracked_files = []
    source_suffixes = {
        ".c",
        ".cc",
        ".cpp",
        ".cu",
        ".cuh",
        ".h",
        ".hpp",
        ".json",
        ".pth",
        ".py",
        ".pyd",
        ".sh",
        ".so",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
    for relative in sorted(untracked):
        path = absolute / relative
        if not path.is_file() or (path.suffix and path.suffix.lower() not in source_suffixes):
            continue
        content_digest = sha256_bytes(path.read_bytes())
        untracked_files.append([relative, content_digest])
        identity.update(relative.encode())
        identity.update(b"\0")
        identity.update(content_digest.encode())
    return {
        "path": str(absolute),
        "revision": revision,
        "dirty": bool(tracked_diff or untracked_files),
        "source_digest": f"sha256:{identity.hexdigest()}",
    }
