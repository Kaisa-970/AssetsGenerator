from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from .artifact_store import LocalArtifactStore
from .backend_registry import (
    BackendRegistry,
    ResolvedPlan,
    ShapeBackend,
    resolve_plan,
    resolve_plan_contract_digest,
)
from .contracts import ContractError
from .errors import ErrorCode
from .models import (
    SCHEMA_VERSION,
    ArtifactRef,
    AssetRelease,
    BackendNativeFrame,
    BuildRun,
    StructuredValue,
)
from .operators import (
    BiRefNetSegmentationBackend,
    CanonicalOutput,
    PreparedObservation,
    SegmentationOutput,
    ShapeOutput,
    assemble_asset,
    canonicalize_glb,
    export_release,
    material_from_glb,
    prepare_observation,
    validate_binary_mask,
    validate_geometry,
)
from .pipeline import compile_pipeline, load_default_operator_specs, load_default_pipeline
from .provenance import output_id as _output_id  # noqa: F401 - historical import compatibility
from .provenance import persist_build_run as _persist_build_run
from .provenance import persist_provenance as _persist_provenance
from .release_io import materialize_json as _materialize_json  # noqa: F401
from .release_io import materialize_release as _materialize_release
from .runtime import Phase1Runtime, utc_now
from .serialization import to_primitive
from .spatial import SpatialContractError, validate_mesh_native_frame
from .workbench_context import ChildRunContext


@dataclass(frozen=True)
class BuildResult:
    run_id: str
    asset_definition: ArtifactRef
    release_manifest: ArtifactRef
    glb: ArtifactRef
    quality_report: ArtifactRef
    output_directory: Path


def _import_image(store: LocalArtifactStore, path: Path, kind: str) -> ArtifactRef:
    with Image.open(path) as image:
        width, height = image.size
        mode = image.mode
        media_type = Image.MIME.get(image.format or "", "application/octet-stream")
    return store.persist_bytes(
        path.read_bytes(),
        kind=kind,
        schema_name="raster_image",
        schema_version="1.0",
        identity_metadata={
            "media_type": media_type,
            "width": width,
            "height": height,
            "channel_layout": mode,
        },
    )


def build_image_asset(
    *,
    image_path: Path,
    mask_path: Path | None,
    store_path: Path,
    output_path: Path,
    backend: ShapeBackend | None = None,
    segmentation_backend: BiRefNetSegmentationBackend | None = None,
    backend_registry: BackendRegistry | None = None,
    resolved_plan: ResolvedPlan | None = None,
    backend_name: str | None = None,
    seed: int = 42,
    pipeline_type: str = "512",
    asset_name: str | None = None,
    run_id: str | None = None,
    child_context: ChildRunContext | None = None,
    input_binding: ArtifactRef | None = None,
) -> BuildResult:
    if backend_name is not None and backend is None:
        raise ContractError("backend_name requires an inline backend")
    configured_sources = sum(
        value is not None for value in (backend, backend_registry, resolved_plan)
    )
    if configured_sources > 1:
        raise ContractError("configure exactly one of backend, backend_registry, or resolved_plan")
    store = LocalArtifactStore(store_path)
    if run_id is None:
        run_id = f"run_{uuid.uuid4().hex}"
    elif not re.fullmatch(r"run_[A-Za-z0-9_-]+", run_id):
        raise ContractError("invalid explicit run_id")
    if child_context is None and (store.root / "runs" / f"{run_id}.json").exists():
        raise ContractError("explicit run_id already exists")
    pipeline = load_default_pipeline()
    specs = load_default_operator_specs()
    compile_pipeline(pipeline, specs)
    if resolved_plan is None:
        registry = backend_registry or BackendRegistry()
        if backend is not None:
            registry.register(
                name=backend_name or "inline_shape_backend",
                operator="shape_generation@1",
                backend_version="configured",
                implementation=backend,
            )
            resolved_plan = resolve_plan(
                pipeline,
                registry,
                operator_specs=specs,
                backend_overrides={"generate_shape": backend_name or "inline_shape_backend"},
            )
        else:
            resolved_plan = resolve_plan(pipeline, registry, operator_specs=specs)
    if (
        resolved_plan.pipeline_name != pipeline.name
        or resolved_plan.pipeline_version != pipeline.version
    ):
        raise ValueError("resolved plan does not match the selected pipeline")
    expected_digest = resolve_plan_contract_digest(pipeline, specs)
    if resolved_plan.contract_digest != expected_digest:
        raise ContractError("resolved plan contract digest does not match the selected pipeline")
    shape_binding = resolved_plan.backend_for("generate_shape", "shape_generation@1")
    if set(resolved_plan.backends) != {"generate_shape"}:
        raise ContractError("only generate_shape backend binding is supported")
    if shape_binding.node_id != "generate_shape":
        raise ContractError("resolved backend node identity mismatch")
    shape_backend = shape_binding.shape_backend()
    runtime = Phase1Runtime(store, pipeline, specs)
    run = BuildRun(
        run_id,
        pipeline.name,
        pipeline.version,
        "running",
        {},
        runtime.attempts,
        utc_now(),
        None,
        {node_id: binding.name for node_id, binding in resolved_plan.backends.items()},
        resolved_plan.contract_digest,
        {node_id: binding.backend_version for node_id, binding in resolved_plan.backends.items()},
    )
    if child_context is not None:
        child_context.begin(run)

        def checkpoint() -> None:
            child_context.persist(run)

        runtime.checkpoint = checkpoint
    provenance: list[ArtifactRef] = []
    try:
        image_ref = _import_image(store, image_path, "rgb_image")
        provided_mask_ref = (
            _import_image(store, mask_path, "binary_mask") if mask_path is not None else None
        )
        export_profile = StructuredValue(
            "export_profile",
            "GLTF2Profile",
            SCHEMA_VERSION,
            {"profile": "gltf2-v1", "appearance_mode": "preserve_mesh"},
        )
        run.inputs = {
            "source_image": image_ref,
            "export_profile": export_profile,
        }
        if provided_mask_ref is not None:
            run.inputs["source_mask"] = provided_mask_ref
        if input_binding is not None:
            from .workbench_binding import verify_imported_binding

            mapping = verify_imported_binding(store, input_binding, image_ref, provided_mask_ref)
            run.inputs["selection_binding"] = input_binding
            run.inputs["import_binding"] = mapping
        runtime.validate_pipeline_inputs(
            {key: value for key, value in run.inputs.items() if key in pipeline.inputs}
        )
        if child_context is not None:
            child_context.persist(run)

        def execute_resolve_mask() -> tuple[SegmentationOutput, dict[str, Any]]:
            if provided_mask_ref is not None:
                validate_binary_mask(store, image_ref, provided_mask_ref)
                value = SegmentationOutput(
                    provided_mask_ref,
                    StructuredValue(
                        "segmentation_result",
                        "SegmentationResult",
                        SCHEMA_VERSION,
                        {"source": "provided", "threshold": None},
                    ),
                    {"backend": "user", "backend_version": "provided-mask"},
                )
            else:
                if segmentation_backend is None:
                    raise ValueError("mask is required when no segmentation backend is configured")
                value = segmentation_backend.segment(store, image_ref)
            return value, {"mask": value.mask, "result": value.result}

        mask_inputs: dict[str, Any] = {"image": image_ref}
        if provided_mask_ref is not None:
            mask_inputs["mask"] = provided_mask_ref
        resolved_mask = runtime.run_node(
            "resolve_mask",
            mask_inputs,
            execute_resolve_mask,
            backend="user" if provided_mask_ref is not None else "birefnet_lite",
            execution_mode=lambda value: "cache_hit" if value.cache_hit else "executed",
        )
        mask_ref = resolved_mask.mask
        provenance.append(
            _persist_provenance(
                store,
                run_id=run_id,
                node_id="resolve_mask",
                port_name="mask",
                artifact=mask_ref,
                derived_from=[image_ref] if provided_mask_ref is None else [],
                operator="segmentation",
                backend=str(resolved_mask.backend_metadata["backend"]),
                backend_version=str(resolved_mask.backend_metadata["backend_version"]),
                parameters={
                    "threshold": resolved_mask.result.value.get("threshold"),
                    "peak_cuda_memory_mb": resolved_mask.backend_metadata.get(
                        "peak_cuda_memory_mb"
                    ),
                },
                seed=None,
                source="user" if provided_mask_ref is not None else "generated",
                model_digest=resolved_mask.backend_metadata.get("model_digest"),
            )
        )

        def execute_prepare() -> tuple[PreparedObservation, dict[str, Any]]:
            value = prepare_observation(store, image_ref, mask_ref)
            bundle_ref = store.persist_structured(value.bundle)
            return value, {
                "bundle": bundle_ref,
                "rgba": value.rgba,
                "image_warp": value.image_warp,
            }

        prepared = runtime.run_node(
            "prepare_observation", {"image": image_ref, "mask": mask_ref}, execute_prepare
        )
        observation_ref = runtime.attempts[-1].outputs["bundle"]
        assert isinstance(observation_ref, ArtifactRef)
        provenance.append(
            _persist_provenance(
                store,
                run_id=run_id,
                node_id="prepare_observation",
                port_name="bundle",
                artifact=observation_ref,
                derived_from=[image_ref, mask_ref],
                operator="prepare_observation",
                backend="core",
                backend_version="0.1.0",
                parameters={"mask_threshold": 127},
                seed=None,
                source="derived",
            )
        )

        def execute_generate() -> tuple[ShapeOutput, dict[str, Any]]:
            value = shape_backend.generate(
                store, prepared.rgba, seed=seed, pipeline_type=pipeline_type
            )
            return value, {
                "mesh": value.mesh,
                "material": value.material,
                "native_frame": value.native_frame,
            }

        def validate_shape_result(value: ShapeOutput) -> None:
            try:
                frame = BackendNativeFrame(**value.native_frame.value)
                metadata = store.get_manifest(value.mesh.artifact_id).identity.identity_metadata
                validate_mesh_native_frame(metadata, frame)
            except (TypeError, SpatialContractError) as error:
                raise ContractError(f"invalid shape Backend spatial output: {error}") from error

        generated = runtime.run_node(
            "generate_shape",
            {"image": prepared.rgba},
            execute_generate,
            backend=shape_binding.name,
            execution_mode=lambda value: "cache_hit" if value.cache_hit else "executed",
            validate_result=validate_shape_result,
        )
        provenance.append(
            _persist_provenance(
                store,
                run_id=run_id,
                node_id="generate_shape",
                port_name="mesh",
                artifact=generated.mesh,
                derived_from=[prepared.rgba, *([input_binding] if input_binding else [])],
                operator="shape_generation",
                backend=shape_binding.name,
                backend_version=str(
                    generated.backend_metadata.get("backend_version", shape_binding.backend_version)
                ),
                parameters={
                    "pipeline_type": pipeline_type,
                    "peak_cuda_memory_mb": generated.backend_metadata.get("peak_cuda_memory_mb"),
                    **{
                        key: generated.backend_metadata[key]
                        for key in (
                            "postprocess_mode",
                            "seed_effective",
                            "pipeline_type_effective",
                            "chunk_size",
                            "mc_resolution",
                            "foreground_ratio",
                            "vertex_count",
                            "face_count",
                            "validated_vertex_count",
                            "validated_face_count",
                            "native_frame_validation",
                            "native_frame_validation_evidence_digest",
                            "native_frame_backend_source_digest",
                        )
                        if key in generated.backend_metadata
                    },
                },
                seed=seed,
                source="generated",
                model_digest=generated.backend_metadata.get("model_digest"),
            )
        )

        native_frame = BackendNativeFrame(**generated.native_frame.value)

        def execute_canonicalize() -> tuple[CanonicalOutput, dict[str, Any]]:
            value = canonicalize_glb(store, generated.mesh, native_frame)
            return value, {
                "mesh": value.mesh,
                "canonical_frame": value.frame,
                "transform": value.transform,
                "spatial_info": value.spatial_info,
                "components": [],
            }

        canonical = runtime.run_node(
            "canonicalize",
            {"mesh": generated.mesh, "native_frame": generated.native_frame, "components": []},
            execute_canonicalize,
        )
        provenance.append(
            _persist_provenance(
                store,
                run_id=run_id,
                node_id="canonicalize",
                port_name="mesh",
                artifact=canonical.mesh,
                derived_from=[generated.mesh],
                operator="canonicalize",
                backend="core",
                backend_version="0.1.0",
                parameters=canonical.result.rule_parameters,
                seed=None,
                source="derived",
            )
        )

        def execute_validate() -> tuple[tuple[Any, ArtifactRef], dict[str, Any]]:
            value = validate_geometry(
                store, canonical.mesh, derived_from=generated.mesh, run_id=run_id
            )
            reference = store.persist_structured(
                StructuredValue(
                    "quality_report", "QualityReport", SCHEMA_VERSION, to_primitive(value)
                )
            )
            return (value, reference), {"report": reference}

        report, report_ref = runtime.run_node(
            "validate",
            {"mesh": canonical.mesh, "material": generated.material},
            execute_validate,
        )
        if report.overall_status == "fail":
            raise RuntimeError("geometry validation failed")
        provenance.append(
            _persist_provenance(
                store,
                run_id=run_id,
                node_id="validate",
                port_name="report",
                artifact=report_ref,
                derived_from=[canonical.mesh],
                operator="validation",
                backend="core",
                backend_version="0.1.0",
                parameters={"profile": "geometry-v1"},
                seed=None,
                source="derived",
            )
        )

        material = material_from_glb(store, generated.mesh)
        generated_material = StructuredValue(
            "pbr_material", "PBRMaterial", SCHEMA_VERSION, to_primitive(material)
        )

        def execute_assemble() -> tuple[tuple[Any, ArtifactRef], dict[str, Any]]:
            value = assemble_asset(
                canonical.mesh,
                material,
                canonical.result.spatial_info,
                prepared.bundle.value["observation_id"],
                report_ref,
                name=asset_name,
            )
            reference = store.persist_structured(
                StructuredValue(
                    "asset_definition", "AssetDefinition", SCHEMA_VERSION, to_primitive(value)
                )
            )
            return (value, reference), {"asset": reference}

        _, asset_ref = runtime.run_node(
            "assemble_asset",
            {
                "mesh": canonical.mesh,
                "material": generated_material,
                "spatial": canonical.spatial_info,
                "quality": report_ref,
                "observations": observation_ref,
                "components": [],
            },
            execute_assemble,
        )
        provenance.append(
            _persist_provenance(
                store,
                run_id=run_id,
                node_id="assemble_asset",
                port_name="asset",
                artifact=asset_ref,
                derived_from=[canonical.mesh, report_ref, observation_ref],
                operator="assemble_asset",
                backend="core",
                backend_version="0.1.0",
                parameters={},
                seed=None,
                source="derived",
            )
        )

        def execute_export() -> tuple[
            tuple[ArtifactRef, AssetRelease, ArtifactRef, ArtifactRef], dict[str, Any]
        ]:
            glb, _ = export_release(
                store,
                asset_ref,
                canonical.mesh,
                material,
                report_ref,
                appearance_mode="preserve_mesh",
            )
            export_provenance = _persist_provenance(
                store,
                run_id=run_id,
                node_id="export",
                port_name="glb",
                artifact=glb,
                derived_from=[asset_ref, canonical.mesh],
                operator="export",
                backend="core",
                backend_version="0.1.0",
                parameters={"profile": "gltf2-v1", "appearance_mode": "preserve_mesh"},
                seed=None,
                source="derived",
            )
            release_files = {
                "geometry/visual.glb": glb,
                "qa/quality-report.json": report_ref,
                **{
                    f"provenance/{index:03d}.json": item
                    for index, item in enumerate([*provenance, export_provenance])
                },
            }
            release = AssetRelease(asset_ref, release_files, "gltf2-v1")
            release_ref = store.persist_structured(
                StructuredValue(
                    "asset_release", "AssetRelease", SCHEMA_VERSION, to_primitive(release)
                )
            )
            return (glb, release, release_ref, export_provenance), {
                "glb": glb,
                "release": release_ref,
            }

        glb_ref, release, release_ref, export_provenance = runtime.run_node(
            "export",
            {
                "asset": asset_ref,
                "mesh": canonical.mesh,
                "material": generated_material,
                "quality": report_ref,
                "profile": export_profile,
            },
            execute_export,
        )
        provenance.append(export_provenance)
        from .models import NodeAttempt

        release_attempt = NodeAttempt(
            "materialize_release",
            1,
            "release_materialization@1",
            "core",
            "running",
            "executed",
            utc_now(),
            None,
            None,
        )
        runtime.attempts.append(release_attempt)
        release_attempt.status = "succeeded"
        release_attempt.finished_at = utc_now()
        run.status = "succeeded"
        run.finished_at = release_attempt.finished_at
        run_ref = child_context.persist(run) if child_context else _persist_build_run(store, run)
        try:
            _materialize_release(store, output_path, release_ref, release, run_ref)
        except Exception:
            release_attempt.status = "failed"
            release_attempt.error_code = ErrorCode.RELEASE_FAILED.value
            release_attempt.finished_at = utc_now()
            run.status = "failed"
            run.finished_at = utc_now()
            (child_context.persist(run) if child_context else _persist_build_run(store, run))
            raise
        return BuildResult(run_id, asset_ref, release_ref, glb_ref, report_ref, output_path)
    except Exception:
        if run.status == "failed":
            raise
        run.status = "failed"
        run.finished_at = utc_now()
        (child_context.persist(run) if child_context else _persist_build_run(store, run))
        raise
