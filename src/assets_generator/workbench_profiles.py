"""Strict local-only real Backend profiles for the fixed workbench template."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

from .backend_registry import BackendRegistry, ResolvedPlan, resolve_plan
from .backends.environment_identity import backend_environment_identity
from .backends.model_identity import snapshot_digest, snapshot_state
from .backends.sam_instances import SAMInstanceProposer
from .backends.source_identity import backend_source_identity
from .operators import Trellis2Backend, TripoSRBackend
from .pipeline import load_default_operator_specs, load_default_pipeline
from .serialization import canonical_json_bytes
from .workbench_engine import BackendProfile

_BACKENDS = Path(__file__).parent / "backends"


_CHECKS: ContextVar[list[Callable[[], None]] | None] = ContextVar("profile_checks", default=None)


_PROGRESS: ContextVar[Callable[[str], None] | None] = ContextVar("profile_progress", default=None)
_T = TypeVar("_T")


def _step(label: str, operation: Callable[[], _T]) -> _T:
    report = _PROGRESS.get()
    started = time.monotonic()
    if report:
        report(f"正在核验 {label}…")
    try:
        result = operation()
    except Exception:
        if report:
            report(f"核验失败 {label}（{time.monotonic() - started:.1f}s）")
        raise
    if report:
        report(f"核验完成 {label}（{time.monotonic() - started:.1f}s）")
    return result


def _signature(path: Path) -> list[Any]:
    stat = path.stat()
    return [
        str(path.resolve()),
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    ]


def _guard(probe: Callable[[], Any], before: Any) -> None:
    def check() -> None:
        try:
            unchanged = probe() == before
        except OSError as error:
            raise ValueError("backend resources changed; reload profiles") from error
        if not unchanged:
            raise ValueError("backend resources changed; reload profiles")

    check()
    checks = _CHECKS.get()
    if checks is not None:
        checks.append(check)


def _snapshot_digest(path: Path) -> str:
    before = snapshot_state(path)
    digest = snapshot_digest(path)
    _guard(lambda: snapshot_state(path), before)
    return digest


def _digest(path: Path) -> str:
    before = _signature(path)
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    _guard(lambda: _signature(path), before)
    return "sha256:" + value.hexdigest()


def _path(raw: Any, *, directory: bool = False, executable: bool = False) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("profile requires explicit local resource paths")
    path = Path(raw).expanduser().absolute()  # Preserve venv Python symlinks.
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError(f"local resource does not exist: {path}")
    if executable and not os.access(path, os.X_OK):
        raise ValueError(f"Python interpreter is not executable: {path}")
    if not directory and not executable and path.stat().st_size == 0:
        raise ValueError(f"local resource is empty: {path}")
    return path


def _object(raw: Any, allowed: set[str], required: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) - allowed or required - set(raw):
        raise ValueError(f"profile requires {sorted(required)} and only allows {sorted(allowed)}")
    return raw


def _environment_identity(python: Path, packages: tuple[str, ...]) -> dict[str, Any]:
    # Inspect installed bytes, not CUDA availability or model inference. No downloads.
    probe = r"""
import hashlib,importlib.metadata,importlib.util,json,pathlib,sys
packages={}
tracked={}
roots=[]
def signature(path):
    stat=path.stat()
    return [str(path.resolve()),stat.st_dev,stat.st_ino,stat.st_size,
            stat.st_mtime_ns,stat.st_ctime_ns]
def content_digest(path):
    before=signature(path)
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    if before!=signature(path):raise RuntimeError('environment changed while hashing')
    tracked[str(path)]=before
    return value.digest()
def listing(root):
    return sorted(str(path) for path in root.rglob('*') if path.is_file()
                  and '__pycache__' not in path.parts and path.suffix!='.pyc')
for name in json.loads(sys.argv[1]):
    dist=importlib.metadata.distribution(name)
    digest=hashlib.sha256()
    package_root=pathlib.Path(str(dist.locate_file(name.replace('-','_'))))
    for root in [package_root,pathlib.Path(str(dist._path))]:
        if root.is_dir(): roots.append([str(root),listing(root)])
    for entry in sorted(dist.files or [],key=str):
        if '__pycache__' in entry.parts or entry.suffix=='.pyc': continue
        path=pathlib.Path(str(dist.locate_file(entry)))
        if path.is_file():
            digest.update(str(entry).encode()+b'\0')
            digest.update(content_digest(path))
    if name == 'segment_anything':
        spec=importlib.util.find_spec('segment_anything')
        if spec is None or not spec.submodule_search_locations:
            raise RuntimeError('segment_anything package sources are unavailable')
        for root in sorted(spec.submodule_search_locations):
            root=pathlib.Path(root)
            roots.append([str(root),listing(root)])
            for path in sorted(root.rglob('*')):
                if not path.is_file() or '__pycache__' in path.parts or path.suffix=='.pyc':
                    continue
                digest.update(path.relative_to(root).as_posix().encode()+b'\0')
                digest.update(content_digest(path))
    packages[name]={'version':dist.version,'content_digest':'sha256:'+digest.hexdigest()}
executable=pathlib.Path(sys.executable)
identity={'python_version':sys.version,'python_executable_digest':'sha256:'+content_digest(executable).hex(),'packages':packages}
identity['environment_digest']='sha256:'+hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()
for root,expected in roots:
    if listing(pathlib.Path(root))!=expected:raise RuntimeError('environment listing changed')
for path,expected in tracked.items():
    if signature(pathlib.Path(path))!=expected:raise RuntimeError('environment changed')
identity['_resource_signatures']=tracked
identity['_resource_roots']=roots
print(json.dumps(identity))
"""
    try:
        result = subprocess.run(
            [str(python), "-B", "-c", probe, json.dumps(packages)],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
            env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
        )
        value = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError(f"cannot identify local backend environment: {error}") from error
    if not isinstance(value, dict) or not isinstance(value.get("environment_digest"), str):
        raise ValueError("invalid backend environment identity")
    signatures = value.pop("_resource_signatures", {})
    roots = value.pop("_resource_roots", [])
    for filename, expected in signatures.items():
        _guard(partial(_signature, Path(filename)), expected)
    for dirname, expected in roots:

        def listing(path: Path = Path(dirname)) -> list[str]:
            return sorted(
                str(item)
                for item in path.rglob("*")
                if item.is_file() and "__pycache__" not in item.parts and item.suffix != ".pyc"
            )

        _guard(listing, expected)
    return value


def _cached_snapshot(python: Path, name: Any) -> Path:
    if not isinstance(name, str) or not name:
        raise ValueError("conditioning model must name a local directory or cached HF model")
    local = Path(name).expanduser()
    if local.is_dir():
        return local.absolute()
    if Path(name).is_absolute() or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name):
        raise ValueError("invalid local/cached conditioning model")
    script = (
        "import sys,pathlib; from huggingface_hub import try_to_load_from_cache; "
        "value=try_to_load_from_cache(sys.argv[1],'config.json',revision='main'); "
        "assert isinstance(value,str), 'main revision config is absent from local HF cache'; "
        "print(pathlib.Path(value).parent)"
    )
    try:
        result = subprocess.run(
            [str(python), "-B", "-c", script, name],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
        )
        return _path(result.stdout.strip(), directory=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f"conditioning model is absent from the local HF cache: {name}") from error


def _cached_checkpoint(python: Path, stem: str) -> list[Path]:
    pieces = stem.split("/")
    if len(pieces) < 3 or any(part in {"", ".", ".."} for part in pieces):
        raise ValueError("missing local TRELLIS checkpoint or invalid cached HF checkpoint")
    repo_id, filename = "/".join(pieces[:2]), "/".join(pieces[2:])
    script = (
        "import sys,json; from huggingface_hub import hf_hub_download; "
        "print(json.dumps([hf_hub_download(sys.argv[1],sys.argv[2]+suffix,"
        "local_files_only=True) for suffix in ('.json','.safetensors')]))"
    )
    try:
        result = subprocess.run(
            [str(python), "-B", "-c", script, repo_id, filename],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
        )
        paths = json.loads(result.stdout)
        if not isinstance(paths, list) or len(paths) != 2:
            raise ValueError("invalid cached checkpoint paths")
        return [_path(path) for path in paths]
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f"TRELLIS checkpoint is absent from local HF cache: {stem}") from error


def _model_identity(backend: str, model: Path, python: Path) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if backend == "triposr":
        for name in ("config.yaml", "model.ckpt"):
            _path(str(model / name))
    else:
        config = json.loads(_path(str(model / "pipeline.json")).read_text())
        if not isinstance(config, dict) or not isinstance(config.get("args"), dict):
            raise ValueError("TRELLIS pipeline.json requires an args object")
        args = config["args"]
        if not isinstance(args.get("models"), dict) or not args["models"]:
            raise ValueError("TRELLIS pipeline requires local model checkpoints")
        for stem in args["models"].values():
            if not isinstance(stem, str) or Path(stem).is_absolute() or ".." in Path(stem).parts:
                raise ValueError("TRELLIS model checkpoint must be relative to its snapshot")
            local_files = [model / (stem + suffix) for suffix in (".json", ".safetensors")]
            if all(path.is_file() for path in local_files):
                for path in local_files:
                    _path(str(path))
            else:
                cached = _cached_checkpoint(python, stem)
                extra.setdefault("external_checkpoints", {})[stem] = [
                    _digest(path) for path in cached
                ]
        conditioning = args.get("image_cond_model", {})
        if not isinstance(conditioning, dict) or not isinstance(conditioning.get("args"), dict):
            raise ValueError("TRELLIS image_cond_model requires name and args")
        if conditioning.get("name") != "DinoV3FeatureExtractor":
            raise ValueError("local-only TRELLIS profile supports DinoV3FeatureExtractor only")
        conditioning_model = _cached_snapshot(
            python, conditioning.get("args", {}).get("model_name")
        )
        _path(str(conditioning_model / "config.json"))
        if not list(conditioning_model.glob("*.safetensors")):
            raise ValueError("local DINO model requires safetensors weights")
        extra["conditioning_model_digest"] = _snapshot_digest(conditioning_model)
    return {"snapshot_digest": _snapshot_digest(model), **extra}


@dataclass(frozen=True)
class ProposalProfile:
    name: str
    proposer: SAMInstanceProposer
    proposal_identity: dict[str, Any]
    test_only: bool = False
    identity_check: Callable[[], None] | None = None


def _load_proposal_profile(name: str, definition: Any) -> ProposalProfile:
    sam_keys = {
        "python",
        "checkpoint",
        "model_type",
        "device",
        "points_per_side",
        "max_instances",
        "min_area_pixels",
        "pred_iou_thresh",
        "stability_score_thresh",
        "timeout_seconds",
    }
    sam = _object(definition, sam_keys, {"python", "checkpoint"})
    python = _path(sam["python"], executable=True)
    checkpoint = _path(sam["checkpoint"])
    proposer = SAMInstanceProposer(
        python,
        checkpoint,
        **{key: value for key, value in sam.items() if key not in {"python", "checkpoint"}},
    )
    _guard(lambda: _signature(python), _signature(python))
    proposal_identity = {
        "backend": "sam-v1",
        "checkpoint_digest": _step("SAM checkpoint", lambda: _digest(checkpoint)),
        "runner_digest": _digest(_BACKENDS / "sam_instances_runner.py"),
        "environment": _step(
            "SAM environment",
            lambda: _environment_identity(
                python, ("segment_anything", "torch", "torchvision", "numpy", "Pillow")
            ),
        ),
        "parameters": proposer.parameters,
        "timeout_seconds": proposer.timeout_seconds,
    }

    def execution_configuration() -> bytes:
        return canonical_json_bytes(
            {
                "python": str(proposer.python),
                "checkpoint": str(proposer.checkpoint),
                "parameters": proposer.parameters,
                "timeout_seconds": proposer.timeout_seconds,
                "declared_identity": proposal_identity,
            }
        )

    _guard(execution_configuration, execution_configuration())
    return ProposalProfile(name, proposer, proposal_identity)


def _load_profiles(config: dict[str, Any]) -> dict[str, BackendProfile]:
    root = _object(config, {"sam", "profiles"}, {"sam", "profiles"})
    proposal = _load_proposal_profile("sam", root["sam"])
    shapes = _load_shape_profiles(root["profiles"])
    return {
        name: BackendProfile(
            name,
            proposal.proposer,
            shape.shape_plan,
            proposal.proposal_identity,
            shape.shape_identity,
            test_only=False,
        )
        for name, shape in shapes.items()
    }


@dataclass(frozen=True)
class ShapeProfile:
    name: str
    shape_plan: ResolvedPlan
    shape_identity: dict[str, Any]
    test_only: bool = False
    identity_check: Callable[[], None] | None = None


def _load_shape_profiles(definitions: Any) -> dict[str, ShapeProfile]:
    if not isinstance(definitions, dict) or not definitions:
        raise ValueError("at least one named backend profile is required")
    profiles = {}
    common = {"backend", "python", "repo", "model", "timeout_seconds"}
    triposr = {"chunk_size", "mc_resolution", "foreground_ratio", "frame_validation"}
    for name, raw in definitions.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name):
            raise ValueError("invalid backend profile name")
        options = _object(raw, common | triposr, {"backend", "python", "repo", "model"})
        backend = options["backend"]
        if backend not in {"trellis2", "triposr"}:
            raise ValueError("unsupported shape backend")
        if backend == "trellis2" and set(options) & triposr:
            raise ValueError("TripoSR options cannot configure TRELLIS")
        executable = _path(options["python"], executable=True)
        _guard(partial(_signature, executable), _signature(executable))
        repo = _path(options["repo"], directory=True)
        model = _path(options["model"], directory=True)
        timeout = options.get("timeout_seconds", 900.0 if backend == "triposr" else 1800.0)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (float, int))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout_seconds must be positive and finite")
        parameters: dict[str, Any] = {"timeout_seconds": timeout}
        implementation: Trellis2Backend | TripoSRBackend
        frame_identity = None
        if backend == "triposr":
            frame = _path(options.get("frame_validation"))
            _guard(partial(_signature, frame), _signature(frame))
            for key, default in (("chunk_size", 8192), ("mc_resolution", 256)):
                value = options.get(key, default)
                if type(value) is not int or value <= 0:
                    raise ValueError(f"{key} must be a positive integer")
                parameters[key] = value
            ratio = options.get("foreground_ratio", 0.85)
            if (
                isinstance(ratio, bool)
                or not isinstance(ratio, (float, int))
                or not math.isfinite(ratio)
                or not 0 < ratio <= 1
            ):
                raise ValueError("foreground_ratio must be finite and in (0,1]")
            parameters["foreground_ratio"] = ratio
            implementation = TripoSRBackend(
                executable, repo, model, frame_validation=frame, **parameters
            )
            frame_identity = implementation._frame_validation_identity()
        else:
            implementation = Trellis2Backend(executable, repo, str(model), **parameters)
        source = _step(f"{name} source", partial(backend_source_identity, repo))
        _guard(partial(backend_source_identity, repo), source)
        identity = {
            "backend": backend,
            "source": {key: value for key, value in source.items() if key != "path"},
            "model": _step(
                f"{name} model weights", partial(_model_identity, backend, model, executable)
            ),
            "runner_digest": _digest(_BACKENDS / f"{backend}_runner.py"),
            "environment": _step(
                f"{name} environment",
                partial(
                    _environment_identity,
                    executable,
                    ("torch", "numpy", "Pillow", "transformers", "safetensors"),
                ),
            ),
            "parameters": parameters,
        }
        if frame_identity is not None:
            identity["frame_validation_digest"] = frame_identity["evidence_digest"]
            identity["triposr_environment"] = backend_environment_identity(executable)
        registry = BackendRegistry()
        registry.register(
            name=backend,
            operator="shape_generation@1",
            backend_version="1.0.0",
            implementation=implementation,
        )
        plan = resolve_plan(
            load_default_pipeline(),
            registry,
            operator_specs=load_default_operator_specs(),
            backend_overrides={"generate_shape": backend},
        )
        profiles[name] = ShapeProfile(name, plan, identity)
    return profiles


def load_profiles(
    config: dict[str, Any], *, progress: Callable[[str], None] | None = None
) -> dict[str, BackendProfile]:
    checks: list[Callable[[], None]] = []
    token = _CHECKS.set(checks)
    progress_token = _PROGRESS.set(progress)
    try:
        profiles = _load_profiles(config)

        def identity_check() -> None:
            for check in checks:
                check()

        _step("profile resource consistency", identity_check)
        return {
            name: replace(profile, identity_check=identity_check)
            for name, profile in profiles.items()
        }
    finally:
        _CHECKS.reset(token)
        _PROGRESS.reset(progress_token)


def load_shape_profiles(
    config: dict[str, Any], *, progress: Callable[[str], None] | None = None
) -> dict[str, ShapeProfile]:
    """Load only shape resources; SAM is neither configured nor inspected."""
    root = _object(config, {"profiles"}, {"profiles"})
    checks: list[Callable[[], None]] = []
    token = _CHECKS.set(checks)
    progress_token = _PROGRESS.set(progress)
    try:
        profiles = _load_shape_profiles(root["profiles"])

        def identity_check() -> None:
            for check in checks:
                check()

        _step("shape profile resource consistency", identity_check)
        return {
            name: replace(profile, identity_check=identity_check)
            for name, profile in profiles.items()
        }
    finally:
        _CHECKS.reset(token)
        _PROGRESS.reset(progress_token)


def load_proposal_profiles(
    config: dict[str, Any], *, progress: Callable[[str], None] | None = None
) -> dict[str, ProposalProfile]:
    """Load SAM-only deployments without inspecting any shape environment or model."""
    root = _object(config, {"profiles"}, {"profiles"})
    definitions = root["profiles"]
    if (
        not isinstance(definitions, dict)
        or not definitions
        or any(not isinstance(name, str) or not name.strip() for name in definitions)
    ):
        raise ValueError("proposal profiles require nonempty named definitions")
    profiles = {}
    for name, definition in definitions.items():
        checks: list[Callable[[], None]] = []
        token = _CHECKS.set(checks)
        progress_token = _PROGRESS.set(progress)
        try:
            profile = _load_proposal_profile(name, definition)

            def identity_check(checks: tuple[Callable[[], None], ...] = tuple(checks)) -> None:
                for check in checks:
                    check()

            _step("proposal profile resource consistency", identity_check)
            profiles[name] = replace(profile, identity_check=identity_check)
        finally:
            _CHECKS.reset(token)
            _PROGRESS.reset(progress_token)
    return profiles
