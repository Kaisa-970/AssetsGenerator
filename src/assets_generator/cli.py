from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .backend_registry import BackendRegistry, resolve_plan
from .models import ArtifactRef
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

    workbench = subparsers.add_parser("workbench", help="Local fixed photo-to-asset workbench")
    workbench.add_argument("--config", type=Path, required=True)
    workbench.add_argument("--store", type=Path, required=True)
    workbench.add_argument("--directory", type=Path, required=True)
    workbench.add_argument("--port", type=int, default=8765)

    import_observations = subparsers.add_parser("import-observations")
    import_observations.add_argument("--manifest", type=Path, required=True)
    import_observations.add_argument("--store", type=Path, required=True)

    build = subparsers.add_parser("build")
    build.add_argument("--image", type=Path, required=True)
    build.add_argument("--mask", type=Path)
    build.add_argument("--store", type=Path, default=Path("artifact-store"))
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--name")
    _shape_arguments(build)
    build.add_argument(
        "--segmentation-python",
        type=Path,
        default=Path("/home/ypkwsl/DevTools/miniconda3/envs/TRELLTS/bin/python"),
    )
    build.add_argument("--segmentation-threshold", type=int, default=128)
    build.add_argument("--segmentation-timeout", type=float, default=300.0)
    multi = subparsers.add_parser("build-multi-view", help="DA3 → Open3D reconstruction")
    multi.add_argument("--observations", required=True, help="Artifact ID or reference JSON")
    multi.add_argument("--store", type=Path, required=True)
    multi.add_argument("--output", type=Path, required=True)
    multi.add_argument("--name")
    for name in ("da3-python", "da3-repo", "da3-model", "open3d-python"):
        multi.add_argument(f"--{name}", type=Path, required=True)
    multi.add_argument("--da3-process-res", type=int, default=392)
    multi.add_argument("--da3-timeout", type=float, default=1800.0)
    multi.add_argument("--open3d-timeout", type=float, default=1800.0)
    multi.add_argument("--voxel-size-ratio", type=float, default=0.01)
    multi.add_argument("--sdf-trunc-ratio", type=float, default=0.04)
    multi.add_argument("--depth-trunc-ratio", type=float, default=3.0)
    multi.add_argument("--up-axis", choices=["+X", "-X", "+Y", "-Y", "+Z", "-Z"], default="-Y")
    candidate = subparsers.add_parser("build-candidate", help="Independent generation candidate")
    for name in ("observations", "reconstruction-release", "view-id"):
        candidate.add_argument(f"--{name}", required=True)
    candidate.add_argument("--store", type=Path, required=True)
    candidate.add_argument("--output", type=Path, required=True)
    _shape_arguments(candidate)
    review = subparsers.add_parser("review-candidate", help="Local review and publication")
    review.add_argument("--candidate", required=True)
    review.add_argument("--store", type=Path, required=True)
    review.add_argument("--output", type=Path, required=True)
    review.add_argument("--port", type=int, default=8765)
    inspect = subparsers.add_parser("inspect", help="Verify and inspect artifact or run")
    inspect.add_argument("--store", type=Path, required=True)
    selection = inspect.add_mutually_exclusive_group(required=True)
    selection.add_argument("--artifact", help="Artifact ID or reference JSON")
    selection.add_argument("--run", help="BuildRun ID")
    scene = subparsers.add_parser(
        "build-scene", help="Assemble supplied releases and explicit poses"
    )
    extraction = subparsers.add_parser(
        "extract-scene", help="Generate objects from supplied scene masks"
    )
    for command in (scene, extraction):
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--store", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
    _shape_arguments(extraction)
    proposals = subparsers.add_parser(
        "propose-instances", help="SAM automatic unknown-class mask proposals"
    )
    proposals.add_argument("--image", type=Path, required=True)
    proposals.add_argument("--sam-python", type=Path, required=True)
    proposals.add_argument("--checkpoint", type=Path, required=True)
    proposals.add_argument("--points-per-side", type=int, default=16)
    proposals.add_argument("--max-instances", type=int, default=20)
    proposals.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    choose = subparsers.add_parser(
        "select-instances", help="Explicitly choose proposal IDs for extraction"
    )
    choose.add_argument("--proposals", required=True)
    choose.add_argument("--proposal-id", action="append", required=True)
    choose.add_argument("--reviewer", required=True)
    for command in (proposals, choose):
        command.add_argument("--store", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
    instance_review = subparsers.add_parser(
        "review-instances", help="Local visual review of instance proposals"
    )
    instance_review.add_argument("--proposals", required=True)
    instance_review.add_argument("--store", type=Path, required=True)
    instance_review.add_argument("--output", type=Path, required=True)
    instance_review.add_argument("--port", type=int, default=8765)
    layout_review = subparsers.add_parser(
        "review-scene-layout", help="Local visual editing and publication of a scene layout"
    )
    layout_review.add_argument("--manifest", type=Path, required=True)
    layout_review.add_argument("--store", type=Path, required=True)
    layout_review.add_argument("--output", type=Path, required=True)
    layout_review.add_argument("--port", type=int, default=8765)
    collision = subparsers.add_parser(
        "build-collision", help="Derive a deterministic collision proxy from an asset release"
    )
    collision.add_argument("--release", required=True, help="Artifact ID or reference JSON")
    collision.add_argument("--store", type=Path, required=True)
    collision.add_argument("--output", type=Path, required=True)
    collision.add_argument("--method", choices=["convex-hull"], default="convex-hull")
    calibration = subparsers.add_parser(
        "calibrate-scale", help="Apply an explicit point-distance metric scale measurement"
    )
    calibration.add_argument("--release", required=True, help="Artifact ID or reference JSON")
    calibration.add_argument("--measurement", type=Path, required=True)
    calibration.add_argument("--store", type=Path, required=True)
    calibration.add_argument("--output", type=Path, required=True)
    rigid_body = subparsers.add_parser(
        "apply-rigid-body", help="Apply explicit SI rigid-body properties to a metric asset"
    )
    rigid_body.add_argument("--release", required=True, help="Artifact ID or reference JSON")
    rigid_body.add_argument("--properties", type=Path, required=True)
    rigid_body.add_argument("--store", type=Path, required=True)
    rigid_body.add_argument("--output", type=Path, required=True)
    usd = subparsers.add_parser("export-usd", help="Export a metric rigid asset using OpenUSD")
    usd.add_argument("--release", required=True, help="Artifact ID or reference JSON")
    usd.add_argument("--store", type=Path, required=True)
    usd.add_argument("--output", type=Path, required=True)
    return parser


def _shape_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--seed", type=int, default=42)
    command.add_argument(
        "--pipeline-type", choices=["512", "1024", "1024_cascade", "1536_cascade"], default="512"
    )
    command.add_argument(
        "--trellis-repo", type=Path, default=Path("/home/ypkwsl/Workspace/TRELLIS.2")
    )
    command.add_argument(
        "--trellis-python",
        type=Path,
        default=Path("/home/ypkwsl/DevTools/miniconda3/envs/TRELLTS/bin/python"),
    )
    command.add_argument("--trellis-model", default="microsoft/TRELLIS.2-4B")
    command.add_argument("--shape-backend", choices=["trellis2", "triposr"])
    command.add_argument("--backend-timeout", type=float, default=1800.0)
    command.add_argument("--triposr-python", type=Path)
    command.add_argument("--triposr-repo", type=Path)
    command.add_argument("--triposr-model", default="stabilityai/TripoSR")
    command.add_argument("--triposr-frame-validation", type=Path)
    command.add_argument("--triposr-timeout", type=float, default=900.0)
    command.add_argument("--triposr-chunk-size", type=int, default=8192)
    command.add_argument("--triposr-mc-resolution", type=int, default=256)
    command.add_argument("--triposr-foreground-ratio", type=float, default=0.85)


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


def _reference(value: str) -> ArtifactRef:
    if value.startswith("sha256:"):
        artifact_id = value
    else:
        raw = json.loads(Path(value).read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) != {"artifact_id"}:
            raise ValueError("reference file must contain only artifact_id")
        artifact_id = raw["artifact_id"]
    if not isinstance(artifact_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact_id):
        raise ValueError("invalid artifact ID")
    return ArtifactRef(artifact_id)


def main() -> int:
    from .artifact_store import ArtifactStoreError
    from .contracts import ContractError

    parser = _parser()
    args = parser.parse_args()
    try:
        return _execute(parser, args)
    except (ValueError, OSError, ArtifactStoreError, ContractError) as error:
        parser.error(str(error))
    return 2


def _execute(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    if args.command == "workbench":
        from .workbench_app import serve_workbench

        serve_workbench(
            config_path=args.config,
            store_path=args.store,
            directory=args.directory,
            port=args.port,
        )
        return 0
    if args.command == "export-usd":
        from .usd_export import export_usd

        usd_result = export_usd(
            release=_reference(args.release), store_path=args.store, output_path=args.output
        )
        print(json.dumps(to_primitive(usd_result), indent=2))
        return 0
    if args.command == "apply-rigid-body":
        from .rigid_body import apply_rigid_body

        rigid_body_result = apply_rigid_body(
            release=_reference(args.release),
            properties_path=args.properties,
            store_path=args.store,
            output_path=args.output,
        )
        print(json.dumps(to_primitive(rigid_body_result), indent=2))
        return 0
    if args.command == "calibrate-scale":
        from .metric_scale import calibrate_metric_scale

        scale_result = calibrate_metric_scale(
            release=_reference(args.release),
            measurement_path=args.measurement,
            store_path=args.store,
            output_path=args.output,
        )
        print(json.dumps(to_primitive(scale_result), indent=2))
        return 0
    if args.command == "build-collision":
        from .collision import build_collision_asset

        collision_result = build_collision_asset(
            release=_reference(args.release),
            store_path=args.store,
            output_path=args.output,
            method=args.method,
        )
        print(json.dumps(to_primitive(collision_result), indent=2))
        return 0
    if args.command == "propose-instances":
        from .backends.sam_instances import SAMInstanceProposer
        from .instance_proposals import propose_instances

        proposal_result = propose_instances(
            image_path=args.image,
            store_path=args.store,
            output_path=args.output,
            backend=SAMInstanceProposer(
                args.sam_python,
                args.checkpoint,
                device=args.device,
                points_per_side=args.points_per_side,
                max_instances=args.max_instances,
            ),
        )
        print(json.dumps(proposal_result, indent=2))
        return 0
    if args.command == "select-instances":
        from .instance_proposals import select_instance_proposals

        selection_result = select_instance_proposals(
            proposals=_reference(args.proposals),
            proposal_ids=args.proposal_id,
            reviewer=args.reviewer,
            store_path=args.store,
            output_path=args.output,
        )
        print(json.dumps(selection_result, indent=2))
        return 0
    if args.command == "review-instances":
        from .instance_review import InstanceReviewSession, create_instance_review_server

        instance_session = InstanceReviewSession(
            args.store, _reference(args.proposals), args.output
        )
        server = create_instance_review_server(instance_session, args.port)
        print(f"http://127.0.0.1:{server.server_port}/ (Ctrl+C 关闭)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    if args.command == "build-scene":
        from .scene_workflow import build_scene

        print(
            json.dumps(
                build_scene(
                    manifest_path=args.manifest, store_path=args.store, output_path=args.output
                ),
                indent=2,
            )
        )
        return 0
    if args.command == "review-scene-layout":
        from .scene_layout_review import (
            SceneLayoutReviewSession,
            create_scene_layout_review_server,
        )

        layout_session = SceneLayoutReviewSession(args.store, args.manifest, args.output)
        server = create_scene_layout_review_server(layout_session, args.port)
        print(f"http://127.0.0.1:{server.server_port}/ (Ctrl+C 关闭)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    if args.command == "extract-scene":
        from .scene_extraction import extract_scene_objects

        scene_plan = resolve_plan(
            load_default_pipeline(),
            _shape_registry(args),
            operator_specs=load_default_operator_specs(),
            backend_overrides={"generate_shape": args.shape_backend}
            if args.shape_backend
            else None,
        )
        extraction = extract_scene_objects(
            manifest_path=args.manifest,
            store_path=args.store,
            output_path=args.output,
            resolved_plan=scene_plan,
            seed=args.seed,
            pipeline_type=args.pipeline_type,
        )
        print(json.dumps(to_primitive(extraction), indent=2))
        return 0
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
    if args.command == "build-multi-view":
        from .backends.da3 import DA3GeometryFrontend
        from .backends.open3d_tsdf import Open3DReconstruction
        from .multi_view_workflow import build_multi_view_asset
        from .pipeline import load_multi_view_pipeline

        multi_registry = BackendRegistry()
        multi_registry.register(
            name="geometry_frontend",
            operator="geometry_frontend@1",
            backend_version="da3-base",
            implementation=DA3GeometryFrontend(
                args.da3_python,
                args.da3_repo,
                args.da3_model,
                process_res=args.da3_process_res,
                timeout_seconds=args.da3_timeout,
            ),
        )
        multi_registry.register(
            name="reconstruction",
            operator="reconstruction@1",
            backend_version="open3d-tsdf",
            implementation=Open3DReconstruction(
                args.open3d_python,
                voxel_size_ratio=args.voxel_size_ratio,
                sdf_trunc_ratio=args.sdf_trunc_ratio,
                depth_trunc_ratio=args.depth_trunc_ratio,
                up_axis=args.up_axis,
                timeout_seconds=args.open3d_timeout,
            ),
        )
        multi_plan = resolve_plan(
            load_multi_view_pipeline(), multi_registry, operator_specs=load_default_operator_specs()
        )
        multi_result = build_multi_view_asset(
            observations=_reference(args.observations),
            store_path=args.store,
            output_path=args.output,
            resolved_plan=multi_plan,
            asset_name=args.name,
            export_appearance_mode="preserve_mesh",
        )
        print(json.dumps(to_primitive(multi_result), indent=2))
        return 0
    if args.command == "build-candidate":
        from .completion import build_completion_candidate

        candidate_plan = resolve_plan(
            load_default_pipeline(),
            _shape_registry(args),
            operator_specs=load_default_operator_specs(),
            backend_overrides={"generate_shape": args.shape_backend}
            if args.shape_backend
            else None,
        )
        candidate_result = build_completion_candidate(
            observations=_reference(args.observations),
            reconstruction_release=_reference(args.reconstruction_release),
            view_id=args.view_id,
            store_path=args.store,
            output_path=args.output,
            resolved_plan=candidate_plan,
            seed=args.seed,
            pipeline_type=args.pipeline_type,
        )
        print(json.dumps(to_primitive(candidate_result), indent=2))
        return 0
    if args.command == "review-candidate":
        from .alignment_review import AlignmentReviewSession, create_review_server

        alignment_session = AlignmentReviewSession(
            args.store, _reference(args.candidate), args.output
        )
        server = create_review_server(alignment_session, args.port)
        print(f"http://127.0.0.1:{server.server_port}/ (Ctrl+C 关闭)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    if args.command == "inspect":
        from .artifact_store import LocalArtifactStore

        store = LocalArtifactStore(args.store)
        if args.run:
            if not re.fullmatch(r"run_[A-Za-z0-9_-]+", args.run):
                raise ValueError("invalid run ID")
            ref = _reference(str(store.root / "runs" / f"{args.run}.json"))
        else:
            ref = _reference(args.artifact)
        if not store.verify_digest(ref):
            raise ValueError("artifact digest verification failed")
        manifest = store.get_manifest(ref.artifact_id)
        inspected = {"manifest": to_primitive(manifest), "verified": True}
        if manifest.identity.identity_metadata.get("media_type") == "application/json":
            inspected["value"] = store.read_structured(ref)
        print(json.dumps(inspected, indent=2))
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
