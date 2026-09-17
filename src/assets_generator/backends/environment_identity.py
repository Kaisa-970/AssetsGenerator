from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


def current_environment_identity() -> dict[str, Any]:
    import sys

    import torch
    import torchmcubes
    import torchmcubes_module

    torch_extension = Path(torch._C.__file__).resolve()
    module_path = Path(torchmcubes.__file__).resolve()
    extension_path = Path(torchmcubes_module.__file__).resolve()
    package_root = module_path.parent
    package_digest = hashlib.sha256()
    for path in sorted(
        item
        for item in package_root.rglob("*")
        if item.is_file() and "__pycache__" not in item.parts and item.suffix != ".pyc"
    ):
        package_digest.update(path.relative_to(package_root).as_posix().encode())
        package_digest.update(b"\0")
        package_digest.update(path.read_bytes())
    return {
        "python_version": sys.version.split()[0],
        "torch_version": torch.__version__,
        "torch_extension": str(torch_extension),
        "torch_extension_digest": (
            f"sha256:{hashlib.sha256(torch_extension.read_bytes()).hexdigest()}"
        ),
        "cuda_version": torch.version.cuda,
        "torchmcubes_version": getattr(torchmcubes, "__version__", None),
        "torchmcubes_module": str(module_path),
        "torchmcubes_package_digest": f"sha256:{package_digest.hexdigest()}",
        "torchmcubes_extension": str(extension_path),
        "torchmcubes_extension_digest": (
            f"sha256:{hashlib.sha256(extension_path.read_bytes()).hexdigest()}"
        ),
    }


_PROBE = """
import json
from environment_identity import current_environment_identity
print(json.dumps(current_environment_identity(), sort_keys=True))
"""


def backend_environment_identity(python: Path) -> dict[str, Any]:
    executable = python.expanduser().absolute()
    try:
        result = subprocess.run(
            [str(executable), "-B", "-c", _PROBE],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        identity = json.loads(result.stdout)
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ) as error:
        raise ValueError(f"cannot identify backend environment {executable}: {error}") from error
    if not isinstance(identity, dict):
        raise ValueError(
            f"cannot identify backend environment {executable}: expected a JSON object"
        )
    return identity


_OPEN3D_PROBE = r"""
import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path

import open3d

def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return "sha256:" + value.hexdigest()

def installed_content(name):
    dist = importlib.metadata.distribution(name)
    if not dist.files:
        raise RuntimeError("missing installed file manifest: " + name)
    value = hashlib.sha256()
    for relative in sorted(dist.files, key=str):
        if "__pycache__" in relative.parts or relative.suffix == ".pyc":
            continue
        path = Path(dist.locate_file(relative))
        value.update(str(relative).encode())
        value.update(b"\0")
        value.update(digest(path).encode())
        value.update(b"\0")
    return {"version": dist.version, "installed_content_digest": "sha256:" + value.hexdigest()}


package_root = Path(open3d.__file__).resolve().parent
distribution = importlib.metadata.distribution("open3d")
metadata_path = Path(distribution._path) / "METADATA"
record_path = Path(distribution._path) / "RECORD"
installer_path = Path(distribution._path) / "INSTALLER"
build_config_path = package_root / "_build_config.py"
extension_candidates = sorted(package_root.glob("*/pybind*.so"))
if not extension_candidates:
    raise RuntimeError("cannot locate Open3D pybind extension")
identity = {
    "python_executable": sys.executable,
    "python_executable_resolved": str(Path(sys.executable).resolve()),
    "python_executable_digest": digest(Path(sys.executable).resolve()),
    "python_version": sys.version.split()[0],
    "packages": {
        name: installed_content(name) for name in ("open3d", "numpy", "Pillow", "trimesh")
    },
    "open3d_version": open3d.__version__,
    "open3d_package_root": str(package_root),
    "open3d_extension": str(extension_candidates[0]),
    "open3d_extension_digest": digest(extension_candidates[0]),
    "open3d_build_config_digest": digest(build_config_path),
    "distribution_metadata_digest": digest(metadata_path),
    "distribution_record_digest": digest(record_path),
    "installer": (
        installer_path.read_text(encoding="utf-8").strip() if installer_path.is_file() else None
    ),
}
payload = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
identity["environment_digest"] = "sha256:" + hashlib.sha256(payload).hexdigest()
print(json.dumps(identity, sort_keys=True))
"""


def open3d_environment_identity(python: Path) -> dict[str, Any]:
    executable = python.expanduser().absolute()
    try:
        result = subprocess.run(
            [str(executable), "-B", "-c", _OPEN3D_PROBE],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        identity = json.loads(result.stdout)
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ) as error:
        raise ValueError(f"cannot identify Open3D environment {executable}: {error}") from error
    if not isinstance(identity, dict) or not isinstance(identity.get("environment_digest"), str):
        raise ValueError(
            f"cannot identify Open3D environment {executable}: expected identity with digest"
        )
    identity["configured_python"] = str(executable)
    return identity
