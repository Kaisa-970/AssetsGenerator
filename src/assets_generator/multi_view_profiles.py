"""Validated local DA3/Open3D configuration shared by CLI and node editor."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from functools import partial
from pathlib import Path
from typing import Any

from .backend_registry import BackendRegistry, resolve_plan
from .backends.da3 import DA3GeometryFrontend
from .backends.open3d_tsdf import Open3DReconstruction
from .backends.source_identity import backend_source_identity
from .dag_multi_view import MultiViewProfile
from .pipeline import load_default_operator_specs, load_multi_view_pipeline
from .workbench_profiles import (
    _CHECKS,
    _digest,
    _environment_identity,
    _guard,
    _path,
    _signature,
    _snapshot_digest,
)


@dataclass(frozen=True)
class MultiViewProfileConfig:
    da3_python: str
    da3_repo: str
    da3_model: str
    open3d_python: str
    process_res: int = 392
    da3_timeout: float = 1800.0
    open3d_timeout: float = 1800.0
    voxel_size_ratio: float = 0.01
    sdf_trunc_ratio: float = 0.04
    depth_trunc_ratio: float = 3.0
    up_axis: str = "-Y"

    def __post_init__(self) -> None:
        for name in ("da3_python", "da3_repo", "da3_model", "open3d_python"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} requires an explicit local resource path")
        if (
            isinstance(self.process_res, bool)
            or not isinstance(self.process_res, int)
            or self.process_res < 14
        ):
            raise ValueError("process_res must be an integer of at least 14")
        for name in (
            "da3_timeout",
            "open3d_timeout",
            "voxel_size_ratio",
            "sdf_trunc_ratio",
            "depth_trunc_ratio",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be positive and finite")
        if self.sdf_trunc_ratio < self.voxel_size_ratio:
            raise ValueError("sdf_trunc_ratio must be at least voxel_size_ratio")
        if not isinstance(self.up_axis, str) or self.up_axis not in {
            "+X",
            "-X",
            "+Y",
            "-Y",
            "+Z",
            "-Z",
        }:
            raise ValueError("up_axis must be a signed coordinate axis")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> MultiViewProfileConfig:
        allowed = {field.name for field in fields(cls)}
        required = {"da3_python", "da3_repo", "da3_model", "open3d_python"}
        if set(raw) - allowed or required - set(raw):
            raise ValueError(
                f"multi-view profile requires {sorted(required)} and only allows {sorted(allowed)}"
            )
        return cls(**raw)


def load_multi_view_profile(
    config: MultiViewProfileConfig | Mapping[str, Any],
) -> MultiViewProfile:
    """Bind verified local DA3/Open3D resources without launching inference."""
    if isinstance(config, Mapping):
        config = MultiViewProfileConfig.from_mapping(config)
    if not isinstance(config, MultiViewProfileConfig):
        raise ValueError("multi-view profile requires a config object or mapping")
    args = config
    checks: list[Callable[[], None]] = []
    token = _CHECKS.set(checks)
    try:
        da3_python = _path(args.da3_python, executable=True)
        da3_repo = _path(args.da3_repo, directory=True)
        da3_model = _path(args.da3_model, directory=True)
        open3d_python = _path(args.open3d_python, executable=True)
        for python in (da3_python, open3d_python):
            _guard(partial(_signature, python), _signature(python))
        _path(str(da3_model / "config.json"))
        _path(str(da3_model / "model.safetensors"))
        geometry = DA3GeometryFrontend(
            da3_python,
            da3_repo,
            da3_model,
            process_res=args.process_res,
            timeout_seconds=args.da3_timeout,
        )
        reconstruction = Open3DReconstruction(
            open3d_python,
            voxel_size_ratio=args.voxel_size_ratio,
            sdf_trunc_ratio=args.sdf_trunc_ratio,
            depth_trunc_ratio=args.depth_trunc_ratio,
            up_axis=args.up_axis,
            timeout_seconds=args.open3d_timeout,
        )
        source = backend_source_identity(da3_repo)
        _guard(partial(backend_source_identity, da3_repo), source)
        backends = Path(__file__).parent / "backends"
        identity = {
            "geometry": {
                "backend": "da3",
                "source": {key: value for key, value in source.items() if key != "path"},
                "model_digest": _snapshot_digest(da3_model),
                "runner_digest": _digest(backends / "da3_runner.py"),
                "adapter_digest": _digest(backends / "da3.py"),
                "environment": _environment_identity(
                    da3_python,
                    (
                        "torch",
                        "torchvision",
                        "numpy",
                        "Pillow",
                        "transformers",
                        "safetensors",
                        "huggingface-hub",
                        "omegaconf",
                        "einops",
                    ),
                ),
                "parameters": {
                    "process_res": args.process_res,
                    "timeout_seconds": args.da3_timeout,
                },
            },
            "reconstruction": {
                "backend": "open3d-tsdf",
                "runner_digest": _digest(backends / "open3d_runner.py"),
                "adapter_digest": _digest(backends / "open3d_tsdf.py"),
                "environment": _environment_identity(open3d_python, ("open3d", "numpy", "Pillow")),
                "parameters": {
                    "voxel_size_ratio": args.voxel_size_ratio,
                    "sdf_trunc_ratio": args.sdf_trunc_ratio,
                    "depth_trunc_ratio": args.depth_trunc_ratio,
                    "up_axis": args.up_axis,
                    "timeout_seconds": args.open3d_timeout,
                },
            },
        }
        registry = BackendRegistry()
        registry.register(
            name="geometry_frontend",
            operator="geometry_frontend@1",
            backend_version="da3-base",
            implementation=geometry,
        )
        registry.register(
            name="reconstruction",
            operator="reconstruction@1",
            backend_version="open3d-tsdf",
            implementation=reconstruction,
        )
        plan = resolve_plan(
            load_multi_view_pipeline(), registry, operator_specs=load_default_operator_specs()
        )

        def check() -> None:
            for callback in checks:
                callback()

        check()
        return MultiViewProfile(plan, identity, check, test_only=False)
    finally:
        _CHECKS.reset(token)
