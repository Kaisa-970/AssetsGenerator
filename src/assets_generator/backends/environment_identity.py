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
