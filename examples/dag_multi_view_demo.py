"""Run the DA3 -> Open3D -> release YAML DAG with explicit local resources.

Use PYTHONPATH=src python examples/dag_multi_view_demo.py --help. Resource
identities are recomputed on resume/retry; incompatible resources fail closed.
No models or Python environments are downloaded or installed by this script.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from functools import partial
from pathlib import Path

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.backend_registry import BackendRegistry, resolve_plan
from assets_generator.backends.da3 import DA3GeometryFrontend
from assets_generator.backends.open3d_tsdf import Open3DReconstruction
from assets_generator.backends.source_identity import backend_source_identity
from assets_generator.dag_adapters import AdapterRegistry
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_multi_view import (
    GeometryAdapter,
    MultiViewProfile,
    ReconstructionAdapter,
    ReleaseAdapter,
)
from assets_generator.dag_persistence import DagRepository
from assets_generator.models import ArtifactRef
from assets_generator.multi_view_relations import register_multi_view_relations
from assets_generator.pipeline import (
    compile_pipeline,
    load_default_operator_specs,
    load_multi_view_pipeline,
    load_operator_specs,
    load_pipeline,
)
from assets_generator.relations import default_relation_registry
from assets_generator.serialization import to_primitive
from assets_generator.workbench_profiles import (
    _CHECKS,
    _digest,
    _environment_identity,
    _guard,
    _path,
    _signature,
    _snapshot_digest,
)


def profile(args: argparse.Namespace) -> MultiViewProfile:
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
        backends = Path(__file__).resolve().parents[1] / "src/assets_generator/backends"
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


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=("start", "resume", "retry"))
    result.add_argument("--store", required=True, type=Path)
    result.add_argument("--directory", required=True, type=Path)
    result.add_argument("--observations", help="existing ObservationBundle Artifact ID (start)")
    result.add_argument("--run", help="existing parent DAG run ID (resume/retry)")
    result.add_argument("--node", help="failed/interrupted node ID (retry)")
    result.add_argument("--expected-revision", type=int, help="current run revision (retry)")
    for name in ("da3-python", "da3-repo", "da3-model", "open3d-python"):
        result.add_argument(f"--{name}", required=True)
    result.add_argument("--process-res", type=int, default=392)
    result.add_argument("--da3-timeout", type=float, default=1800.0)
    result.add_argument("--open3d-timeout", type=float, default=1800.0)
    result.add_argument("--voxel-size-ratio", type=float, default=0.01)
    result.add_argument("--sdf-trunc-ratio", type=float, default=0.04)
    result.add_argument("--depth-trunc-ratio", type=float, default=3.0)
    result.add_argument("--up-axis", choices=("+X", "-X", "+Y", "-Y", "+Z", "-Z"), default="-Y")
    return result


def main() -> None:
    cli = parser()
    args = cli.parse_args()
    if args.command == "start":
        if not args.observations or args.run or args.node or args.expected_revision is not None:
            cli.error("start requires --observations and rejects retry/resume options")
    elif not args.run or args.observations:
        cli.error("resume/retry requires --run and rejects --observations")
    if args.command == "retry" and (not args.node or args.expected_revision is None):
        cli.error("retry requires --node and --expected-revision")
    if args.command == "resume" and (args.node or args.expected_revision is not None):
        cli.error("resume rejects retry options")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    local_profile = profile(args)
    adapters = AdapterRegistry()
    for adapter in (GeometryAdapter, ReconstructionAdapter, ReleaseAdapter):
        adapters.register(adapter(local_profile))
    relations = default_relation_registry()
    register_multi_view_relations(relations)
    base = Path(__file__).resolve().parent
    plan = adapters.bind_plan(
        compile_pipeline(
            load_pipeline(base / "dag-multi-view-asset.yaml"),
            load_operator_specs(base / "dag-multi-view-operators.yaml"),
            relation_registry=relations,
            require_explicit_joins=True,
        ),
        relation_registry=relations,
    )
    store = LocalArtifactStore(args.store)
    repository = DagRepository(store, args.directory)
    with repository:
        engine = DagEngine(repository, adapters, relations)
        if args.command == "start":
            run = engine.create(plan, {"observations": ArtifactRef(args.observations)})
            print(f"run_id={run.run_id}", flush=True)
            run = engine.drain(run.run_id)
        elif args.command == "resume":
            run = engine.drain(args.run)
        else:
            run = engine.retry(args.run, args.node, args.expected_revision)
        print(json.dumps(to_primitive(run), indent=2))


if __name__ == "__main__":
    main()
