from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from .artifact_store import LocalArtifactStore
from .errors import ErrorCode
from .models import (
    SCHEMA_VERSION,
    ArtifactRef,
    AssetRelease,
    BackendNativeFrame,
    BuildRun,
    ProvenanceRecord,
    StructuredValue,
)
from .operators import (
    BiRefNetSegmentationBackend,
    CanonicalOutput,
    PreparedObservation,
    SegmentationOutput,
    ShapeOutput,
    Trellis2Backend,
    assemble_asset,
    canonicalize_glb,
    export_release,
    material_from_glb,
    prepare_observation,
    validate_binary_mask,
    validate_geometry,
)
from .pipeline import compile_pipeline, load_default_operator_specs, load_default_pipeline
from .runtime import Phase1Runtime, utc_now
from .serialization import canonical_json_bytes, sha256_bytes, to_primitive


@dataclass(frozen=True)
class BuildResult:
    run_id: str
    asset_definition: ArtifactRef
    release_manifest: ArtifactRef
    glb: ArtifactRef
    quality_report: ArtifactRef
    output_directory: Path


def _output_id(run_id: str, node_id: str, port_name: str) -> str:
    return sha256_bytes(f"{run_id}:{node_id}:1:{port_name}".encode())


def _persist_provenance(
    store: LocalArtifactStore,
    *,
    run_id: str,
    node_id: str,
    port_name: str,
    artifact: ArtifactRef,
    derived_from: list[ArtifactRef],
    operator: str,
    backend: str,
    backend_version: str,
    parameters: dict[str, Any],
    seed: int | None,
    source: str,
    model_digest: str | None = None,
) -> ArtifactRef:
    output_id = _output_id(run_id, node_id, port_name)
    record = ProvenanceRecord(
        provenance_id=sha256_bytes(f"provenance:{output_id}".encode()),
        output_id=output_id,
        output_artifact_id=artifact.artifact_id,
        derived_from_artifact_ids=[item.artifact_id for item in derived_from],
        operator=operator,
        operator_version="1",
        backend=backend,
        backend_version=backend_version,
        model_digest=model_digest,
        container_digest=None,
        parameters=parameters,
        seed=seed,
        run_id=run_id,
        node_id=node_id,
        attempt=1,
        source=source,
    )
    return store.persist_bytes(
        canonical_json_bytes(record),
        kind="provenance_record",
        schema_name="ProvenanceRecord",
        schema_version=SCHEMA_VERSION,
        identity_metadata={"media_type": "application/json"},
    )


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


def _materialize_json(store: LocalArtifactStore, artifact: ArtifactRef, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(store.blob_path(artifact).read_bytes())


def _persist_build_run(store: LocalArtifactStore, run: BuildRun) -> ArtifactRef:
    return store.record_build_run(
        run.run_id,
        StructuredValue("build_run", "BuildRun", SCHEMA_VERSION, to_primitive(run)),
    )


def _materialize_release(
    store: LocalArtifactStore,
    output_path: Path,
    release_ref: ArtifactRef,
    release: AssetRelease,
    run_ref: ArtifactRef,
) -> None:
    output_path = output_path.expanduser().absolute()
    if output_path.exists():
        raise FileExistsError(f"output directory already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.parent / f".{output_path.name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.mkdir()
        _materialize_json(store, release.asset_definition, temporary / "asset.json")
        _materialize_json(store, release_ref, temporary / "release.json")
        _materialize_json(store, run_ref, temporary / "run.json")
        for relative_path, reference in release.files.items():
            target = temporary / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(store.blob_path(reference), target)
            if not store.verify_digest(reference):
                raise RuntimeError(f"artifact verification failed during release: {relative_path}")
        expected = {"asset.json", "release.json", "run.json", *release.files.keys()}
        actual = {
            str(path.relative_to(temporary)) for path in temporary.rglob("*") if path.is_file()
        }
        if actual != expected:
            raise RuntimeError("materialized release does not match its manifest")
        temporary.replace(output_path)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def build_image_asset(
    *,
    image_path: Path,
    mask_path: Path | None,
    store_path: Path,
    output_path: Path,
    backend: Trellis2Backend,
    segmentation_backend: BiRefNetSegmentationBackend | None = None,
    seed: int = 42,
    pipeline_type: str = "512",
    asset_name: str | None = None,
) -> BuildResult:
    store = LocalArtifactStore(store_path)
    run_id = f"run_{uuid.uuid4().hex}"
    pipeline = load_default_pipeline()
    specs = load_default_operator_specs()
    compile_pipeline(pipeline, specs)
    runtime = Phase1Runtime(store, pipeline, specs)
    run = BuildRun(
        run_id, pipeline.name, pipeline.version, "running", {}, runtime.attempts, utc_now(), None
    )
    provenance: list[ArtifactRef] = []
    try:
        image_ref = _import_image(store, image_path, "rgb_image")
        provided_mask_ref = (
            _import_image(store, mask_path, "binary_mask") if mask_path is not None else None
        )
        export_profile = StructuredValue(
            "export_profile", "GLTF2Profile", SCHEMA_VERSION, {"profile": "gltf2-v1"}
        )
        run.inputs = {
            "source_image": image_ref,
            "export_profile": export_profile,
        }
        if provided_mask_ref is not None:
            run.inputs["source_mask"] = provided_mask_ref
        runtime.validate_pipeline_inputs(run.inputs)

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
                parameters={"threshold": resolved_mask.result.value.get("threshold")},
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
            value = backend.generate(store, prepared.rgba, seed=seed, pipeline_type=pipeline_type)
            return value, {
                "mesh": value.mesh,
                "material": value.material,
                "native_frame": value.native_frame,
            }

        generated = runtime.run_node(
            "generate_shape",
            {"image": prepared.rgba},
            execute_generate,
            backend="trellis2",
            execution_mode=lambda value: "cache_hit" if value.cache_hit else "executed",
        )
        provenance.append(
            _persist_provenance(
                store,
                run_id=run_id,
                node_id="generate_shape",
                port_name="mesh",
                artifact=generated.mesh,
                derived_from=[prepared.rgba],
                operator="shape_generation",
                backend="trellis2",
                backend_version=str(generated.backend_metadata["backend_version"]),
                parameters={"pipeline_type": pipeline_type},
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
            }

        canonical = runtime.run_node(
            "canonicalize",
            {"mesh": generated.mesh, "native_frame": generated.native_frame},
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
            glb, _ = export_release(store, asset_ref, canonical.mesh, report_ref)
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
                parameters={"profile": "gltf2-v1"},
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
                "quality": report_ref,
                "profile": export_profile,
            },
            execute_export,
        )
        provenance.append(export_provenance)
        run.status = "succeeded"
        run.finished_at = utc_now()
        run_ref = _persist_build_run(store, run)
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
        try:
            _materialize_release(store, output_path, release_ref, release, run_ref)
        except Exception:
            release_attempt.status = "failed"
            release_attempt.error_code = ErrorCode.RELEASE_FAILED.value
            release_attempt.finished_at = utc_now()
            run.status = "failed"
            run.finished_at = utc_now()
            _persist_build_run(store, run)
            raise
        release_attempt.status = "succeeded"
        release_attempt.finished_at = utc_now()
        _persist_build_run(store, run)
        return BuildResult(run_id, asset_ref, release_ref, glb_ref, report_ref, output_path)
    except Exception:
        run.status = "failed"
        run.finished_at = utc_now()
        _persist_build_run(store, run)
        raise
