from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str
    data: dict[str, Any] | None = None


@dataclass(frozen=True)
class PreflightReport:
    ready: bool
    checks: list[CheckResult]


_ENVIRONMENT_PROBE = r"""
import importlib
import json
import sys

repo = sys.argv[1]
sys.path.insert(0, repo)
modules = [
    "PIL",
    "einops",
    "huggingface_hub",
    "imageio",
    "omegaconf",
    "torchmcubes",
    "transformers",
    "trimesh",
    "tsr.system",
]
imports = {}
for module in modules:
    try:
        imported = importlib.import_module(module)
        imports[module] = {
            "ok": True,
            "version": getattr(imported, "__version__", None),
        }
    except Exception as exc:
        imports[module] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

payload = {
    "python": sys.executable,
    "python_version": sys.version.split()[0],
    "imports": imports,
}
try:
    import torch

    cuda_available = torch.cuda.is_available()
    payload["torch"] = {
        "version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "cuda_available": cuda_available,
        "device_name": torch.cuda.get_device_name(0) if cuda_available else None,
        "device_capability": list(torch.cuda.get_device_capability(0)) if cuda_available else None,
        "compiled_arches": torch.cuda.get_arch_list() if cuda_available else [],
    }
except Exception as exc:
    payload["torch_error"] = f"{type(exc).__name__}: {exc}"

print(json.dumps(payload))
"""


def _path_check(name: str, path: Path, *, directory: bool = False) -> CheckResult:
    resolved = path.expanduser().resolve()
    valid = resolved.is_dir() if directory else resolved.is_file()
    kind = "directory" if directory else "file"
    if valid:
        return CheckResult(name, "pass", f"{kind} exists", {"path": str(resolved)})
    return CheckResult(name, "fail", f"{kind} does not exist", {"path": str(resolved)})


def _repo_checks(repo: Path) -> list[CheckResult]:
    resolved = repo.expanduser().resolve()
    root = _path_check("triposr_repo", resolved, directory=True)
    if root.status == "fail":
        return [root]
    expected = [resolved / "run.py", resolved / "tsr" / "system.py"]
    missing = [str(path.relative_to(resolved)) for path in expected if not path.is_file()]
    if missing:
        return [
            root,
            CheckResult(
                "triposr_repo_layout",
                "fail",
                "expected TripoSR source files are missing",
                {"missing": missing},
            ),
        ]
    return [
        root,
        CheckResult("triposr_repo_layout", "pass", "TripoSR source layout is present"),
    ]


def _model_check(model: str) -> CheckResult:
    path = Path(model).expanduser()
    if path.is_dir():
        resolved = path.resolve()
        expected = [resolved / "config.yaml", resolved / "model.ckpt"]
        missing = [p.name for p in expected if not p.is_file() or p.stat().st_size == 0]
        if not missing:
            return CheckResult(
                "model_snapshot",
                "pass",
                "local model snapshot is present",
                {"path": str(resolved), "required_files": [p.name for p in expected]},
            )
        return CheckResult(
            "model_snapshot",
            "fail",
            "local model snapshot lacks required nonempty files",
            {"path": str(resolved), "missing": missing},
        )
    return CheckResult(
        "model_snapshot",
        "fail",
        "model is not a local snapshot directory; preflight will not download it",
        {"model": model},
    )


def _driver_check() -> CheckResult:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return CheckResult("nvidia_driver", "fail", "nvidia-smi was not found on PATH")
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult("nvidia_driver", "fail", str(exc))
    if result.returncode != 0:
        return CheckResult(
            "nvidia_driver",
            "fail",
            result.stderr.strip() or "nvidia-smi failed",
        )
    devices = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not devices:
        return CheckResult("nvidia_driver", "fail", "nvidia-smi reported no GPU")
    return CheckResult(
        "nvidia_driver",
        "pass",
        "NVIDIA driver and GPU are visible",
        {"devices": devices},
    )


def _python_probe(python: Path, repo: Path) -> list[CheckResult]:
    executable_path = python.expanduser().absolute()
    executable = CheckResult(
        "backend_python",
        "pass" if executable_path.is_file() else "fail",
        "file exists" if executable_path.is_file() else "file does not exist",
        {"path": str(executable_path)},
    )
    if executable.status == "fail":
        return [executable]
    try:
        result = subprocess.run(
            [
                str(executable_path),
                "-B",
                "-c",
                _ENVIRONMENT_PROBE,
                str(repo.expanduser().resolve()),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except subprocess.TimeoutExpired:
        return [
            executable,
            CheckResult("backend_environment", "fail", "environment probe timed out after 60s"),
        ]
    except OSError as exc:
        return [executable, CheckResult("backend_environment", "fail", str(exc))]
    if result.returncode != 0:
        return [
            executable,
            CheckResult(
                "backend_environment",
                "fail",
                result.stderr.strip() or "target Python probe failed",
            ),
        ]
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return [
            executable,
            CheckResult(
                "backend_environment",
                "fail",
                "target Python did not return valid JSON",
                {"stdout": result.stdout.strip()},
            ),
        ]

    imports = payload.get("imports", {})
    missing = sorted(name for name, value in imports.items() if not value.get("ok"))
    import_result = CheckResult(
        "required_imports",
        "fail" if missing else "pass",
        f"missing or broken imports: {', '.join(missing)}" if missing else "required imports load",
        {"imports": imports},
    )
    version_text = str(payload.get("python_version", "unknown"))
    try:
        major, minor, *_ = (int(part) for part in version_text.split("."))
        version_supported = (major, minor) >= (3, 8)
    except ValueError:
        version_supported = False
    version_result = CheckResult(
        "backend_python_version",
        "pass" if version_supported else "fail",
        (
            f"Python {version_text} satisfies TripoSR's Python 3.8+ requirement"
            if version_supported
            else f"Python {version_text} does not satisfy TripoSR's Python 3.8+ requirement"
        ),
        {"python": payload.get("python")},
    )
    torch_data = payload.get("torch")
    if not isinstance(torch_data, dict):
        cuda_result = CheckResult(
            "torch_cuda",
            "fail",
            payload.get("torch_error", "PyTorch probe returned no CUDA information"),
        )
    elif not torch_data.get("cuda_available"):
        cuda_result = CheckResult(
            "torch_cuda", "fail", "PyTorch cannot access CUDA", {"torch": torch_data}
        )
    else:
        capability = torch_data.get("device_capability") or []
        capability_name = f"sm_{''.join(str(value) for value in capability)}"
        arches = torch_data.get("compiled_arches") or []
        supported = capability_name in arches
        cuda_result = CheckResult(
            "torch_cuda",
            "pass" if supported else "fail",
            (
                f"PyTorch supports device capability {capability_name}"
                if supported
                else f"PyTorch build does not list device capability {capability_name}"
            ),
            {"torch": torch_data},
        )
    return [
        executable,
        version_result,
        import_result,
        cuda_result,
    ]


def run_preflight(python: Path, repo: Path, model: str) -> PreflightReport:
    checks = [
        *_repo_checks(repo),
        _model_check(model),
        _driver_check(),
        *_python_probe(python, repo),
    ]
    return PreflightReport(ready=all(check.status == "pass" for check in checks), checks=checks)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only TripoSR environment readiness checks; never downloads or runs inference."
        )
    )
    parser.add_argument("--python", type=Path, required=True, help="TripoSR environment Python")
    parser.add_argument("--repo", type=Path, required=True, help="Local TripoSR checkout")
    parser.add_argument(
        "--model",
        required=True,
        help="Local model snapshot directory (remote IDs are not downloaded)",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_preflight(args.python, args.repo, args.model)
    if args.json:
        print(json.dumps(asdict(report), indent=2, sort_keys=True))
    else:
        for check in report.checks:
            print(f"[{check.status.upper()}] {check.name}: {check.detail}")
        print(f"\nTripoSR environment ready: {'yes' if report.ready else 'no'}")
    return 0 if report.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
