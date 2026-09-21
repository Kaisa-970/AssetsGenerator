"""Audited local deployment fingerprint; never trust a self-reported version alone."""

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from .remote_protocol import RemoteIdentity
from .serialization import canonical_json_bytes, sha256_bytes


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def deployment_identity(profile: dict[str, Any]) -> RemoteIdentity:
    repo = Path(profile["repo"])
    files = {
        str(p.relative_to(repo)): file_digest(p) for p in sorted((repo / "sam3").rglob("*.py"))
    }
    for p in sorted((repo / "sam3/assets").glob("*")):
        if p.is_file():
            files[str(p.relative_to(repo))] = file_digest(p)
    source = {
        name: file_digest(Path(__file__).with_name(name))
        for name in (
            "sam3_text_service.py",
            "sam3_text_contract.py",
            "sam3_text_identity.py",
            "remote_service_worker.py",
            "remote_service_process.py",
            "remote_service_store.py",
        )
    }
    environment = json.loads(
        subprocess.check_output(
            [
                profile["python"],
                "-c",
                "import importlib.metadata as m,json; "
                "print(json.dumps({n:m.version(n) for n in "
                "['torch','torchvision','numpy','Pillow','timm']}))",
            ],
            text=True,
        )
    )
    if environment != profile["environment"]:
        raise ValueError("SAM3 environment versions changed")
    body = {
        "schema": "sam3-text-deployment@1",
        "source": files,
        "service": source,
        "runner": file_digest(Path(profile["runner"])),
        "checkpoint": file_digest(Path(profile["checkpoint"])),
        "python": str(Path(profile["python"]).expanduser().absolute()),
        "python_digest": file_digest(Path(profile["python"])),
        "environment": profile["environment"],
        "device": "cuda",
    }
    return RemoteIdentity(profile["service_id"], sha256_bytes(canonical_json_bytes(body)))
