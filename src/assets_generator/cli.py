from __future__ import annotations

import argparse
import json
from pathlib import Path

from .backend_registry import BackendRegistry, resolve_plan
from .pipeline import (
    compile_pipeline,
    load_default_operator_specs,
    load_default_pipeline,
    load_operator_specs,
    load_pipeline,
)
from .serialization import to_primitive


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="assets-generator")
    subparsers = parser.add_subparsers(dest="command", required=True)
    compile_command = subparsers.add_parser("compile-pipeline")
    compile_command.add_argument("--pipeline", type=Path)
    compile_command.add_argument("--operators", type=Path)

    import_observations = subparsers.add_parser("import-observations")
    import_observations.add_argument("--manifest", type=Path, required=True)
    import_observations.add_argument("--store", type=Path, required=True)

    build = subparsers.add_parser("build")
    build.add_argument("--image", type=Path, required=True)
    build.add_argument("--mask", type=Path)
    build.add_argument("--store", type=Path, default=Path("artifact-store"))
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--name")
    build.add_argument("--seed", type=int, default=42)
    build.add_argument(
        "--pipeline-type", choices=["512", "1024", "1024_cascade", "1536_cascade"], default="512"
    )
    build.add_argument(
        "--trellis-repo", type=Path, default=Path("/home/ypkwsl/Workspace/TRELLIS.2")
    )
    build.add_argument(
        "--trellis-python",
        type=Path,
        default=Path("/home/ypkwsl/DevTools/miniconda3/envs/TRELLTS/bin/python"),
    )
    build.add_argument("--trellis-model", default="microsoft/TRELLIS.2-4B")
    build.add_argument("--shape-backend", choices=["trellis2", "triposr"])
    build.add_argument("--backend-timeout", type=float, default=1800.0)
    build.add_argument("--triposr-python", type=Path)
    build.add_argument("--triposr-repo", type=Path)
    build.add_argument("--triposr-model", default="stabilityai/TripoSR")
    build.add_argument("--triposr-frame-validation", type=Path)
    build.add_argument("--triposr-timeout", type=float, default=900.0)
    build.add_argument("--triposr-chunk-size", type=int, default=8192)
    build.add_argument("--triposr-mc-resolution", type=int, default=256)
    build.add_argument("--triposr-foreground-ratio", type=float, default=0.85)
    build.add_argument(
        "--segmentation-python",
        type=Path,
        default=Path("/home/ypkwsl/DevTools/miniconda3/envs/TRELLTS/bin/python"),
    )
    build.add_argument("--segmentation-threshold", type=int, default=128)
    build.add_argument("--segmentation-timeout", type=float, default=300.0)
    return parser


def _shape_registry(args: argparse.Namespace) -> BackendRegistry:
    from .operators import Trellis2Backend, TripoSRBackend

    registry = BackendRegistry()
    registry.register(
        name="trellis2",
        operator="shape_generation@1",
        backend_version="1.0.0",
        implementation=Trellis2Backend(
            args.trellis_python,
            args.trellis_repo,
            args.trellis_model,
            timeout_seconds=args.backend_timeout,
        ),
    )
    triposr_configured = args.triposr_python is not None and args.triposr_repo is not None
    if triposr_configured:
        registry.register(
            name="triposr",
            operator="shape_generation@1",
            backend_version="1.0.0",
            implementation=TripoSRBackend(
                args.triposr_python,
                args.triposr_repo,
                args.triposr_model,
                timeout_seconds=args.triposr_timeout,
                chunk_size=args.triposr_chunk_size,
                mc_resolution=args.triposr_mc_resolution,
                foreground_ratio=args.triposr_foreground_ratio,
                frame_validation=args.triposr_frame_validation,
            ),
        )
    if args.shape_backend == "triposr" and (
        not triposr_configured or args.triposr_frame_validation is None
    ):
        raise ValueError(
            "triposr requires --triposr-python, --triposr-repo, and --triposr-frame-validation"
        )
    if (args.triposr_python is None) != (args.triposr_repo is None):
        raise ValueError("--triposr-python and --triposr-repo must be provided together")
    return registry


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    if args.command == "compile-pipeline":
        specs = (
            load_operator_specs(args.operators) if args.operators else load_default_operator_specs()
        )
        pipeline = load_pipeline(args.pipeline) if args.pipeline else load_default_pipeline()
        compile_pipeline(pipeline, specs)
        print(f"{pipeline.name}@{pipeline.version}: valid")
        return 0
    if args.command == "import-observations":
        from .artifact_store import LocalArtifactStore
        from .observation_import import import_observation_manifest

        reference = import_observation_manifest(args.manifest, LocalArtifactStore(args.store))
        print(json.dumps(to_primitive(reference), indent=2))
        return 0
    from .operators import BiRefNetSegmentationBackend
    from .workflow import build_image_asset

    segmentation_backend = BiRefNetSegmentationBackend(
        args.segmentation_python,
        threshold=args.segmentation_threshold,
        timeout_seconds=args.segmentation_timeout,
    )
    pipeline = load_default_pipeline()
    try:
        registry = _shape_registry(args)
    except ValueError as error:
        parser.error(str(error))
    plan = resolve_plan(
        pipeline,
        registry,
        operator_specs=load_default_operator_specs(),
        backend_overrides={"generate_shape": args.shape_backend} if args.shape_backend else None,
    )
    result = build_image_asset(
        image_path=args.image,
        mask_path=args.mask,
        store_path=args.store,
        output_path=args.output,
        segmentation_backend=segmentation_backend,
        resolved_plan=plan,
        seed=args.seed,
        pipeline_type=args.pipeline_type,
        asset_name=args.name,
    )
    print(json.dumps(to_primitive(result), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
