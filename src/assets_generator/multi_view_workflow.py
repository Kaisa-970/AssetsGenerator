from __future__ import annotations

import math
import re
import uuid
from pathlib import Path
from typing import Any

from PIL import Image

from .artifact_store import LocalArtifactStore
from .backend_registry import ResolvedPlan, resolve_plan_contract_digest
from .contracts import ContractError
from .errors import ErrorCode, PipelineError
from .models import (
    SCHEMA_VERSION,
    ArtifactRef,
    AssetRelease,
    BackendNativeFrame,
    BuildRun,
    CameraRecord,
    ComponentProvenance,
    NodeAttempt,
    PBRMaterial,
    PortValue,
    SemanticInfo,
    StructuredValue,
)
from .observations import (
    camera_record_from_mapping,
    inspect_observation_raster,
    observation_bundle_from_artifact,
    validate_camera_record,
)
from .operators import (
    CanonicalOutput,
    GeometryFrontendOutput,
    ReconstructionOutput,
    assemble_asset,
    canonicalize_glb,
    export_release,
    remap_component_provenance,
    validate_geometry,
    validate_semantic_info,
)
from .pipeline import compile_pipeline, load_default_operator_specs, load_multi_view_pipeline
from .runtime import Phase1Runtime, utc_now
from .serialization import canonical_json_bytes, to_primitive
from .spatial import (
    SpatialContractError,
    validate_backend_native_frame,
    validate_mesh_native_frame,
)
from .workflow import (
    BuildResult,
    _materialize_release,
    _persist_build_run,
    _persist_provenance,
)


def _structured(value: StructuredValue, *, kind: str, schema_name: str) -> dict[str, Any]:
    if (
        value.kind != kind
        or value.schema_name != schema_name
        or value.schema_version != SCHEMA_VERSION
    ):
        raise ContractError(f"expected {schema_name}@{SCHEMA_VERSION}")
    return value.value


def _artifact_reference(raw: object, label: str) -> ArtifactRef | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("artifact_id"), str):
        raise ContractError(f"{label} must be an ArtifactRef")
    return ArtifactRef(raw["artifact_id"])


def _material(value: StructuredValue, store: LocalArtifactStore) -> PBRMaterial:
    try:
        raw = _structured(value, kind="pbr_material", schema_name="PBRMaterial")
        material = PBRMaterial(
            base_color_factor=raw["base_color_factor"],
            base_color_texture=_artifact_reference(
                raw.get("base_color_texture"), "PBRMaterial.base_color_texture"
            ),
            normal_texture=_artifact_reference(
                raw.get("normal_texture"), "PBRMaterial.normal_texture"
            ),
            metallic_roughness_texture=_artifact_reference(
                raw.get("metallic_roughness_texture"),
                "PBRMaterial.metallic_roughness_texture",
            ),
            emissive_texture=_artifact_reference(
                raw.get("emissive_texture"), "PBRMaterial.emissive_texture"
            ),
            alpha_mode=raw.get("alpha_mode", "OPAQUE"),
        )
    except (KeyError, TypeError) as error:
        raise ContractError(f"invalid PBRMaterial: {error}") from error
    if len(material.base_color_factor) != 4 or not all(
        isinstance(item, (int, float))
        and not isinstance(item, bool)
        and math.isfinite(item)
        and 0.0 <= item <= 1.0
        for item in material.base_color_factor
    ):
        raise ContractError("PBRMaterial base_color_factor must contain four values in [0, 1]")
    if material.alpha_mode not in {"OPAQUE", "MASK", "BLEND"}:
        raise ContractError(f"invalid PBRMaterial alpha_mode: {material.alpha_mode}")
    for name, reference, kinds in (
        ("base_color_texture", material.base_color_texture, {"texture_2d"}),
        ("normal_texture", material.normal_texture, {"normal_map"}),
        (
            "metallic_roughness_texture",
            material.metallic_roughness_texture,
            {"metallic_roughness_map"},
        ),
        ("emissive_texture", material.emissive_texture, {"texture_2d"}),
    ):
        if reference is None:
            continue
        if not store.verify_digest(reference):
            raise ContractError(f"PBRMaterial {name} references an invalid artifact")
        kind = store.get_manifest(reference.artifact_id).identity.kind
        if kind not in kinds:
            raise ContractError(f"PBRMaterial {name} rejects kind {kind}")
        try:
            with Image.open(store.blob_path(reference)) as image:
                image.load()
        except (OSError, ValueError) as error:
            raise ContractError(f"PBRMaterial {name} is not a decodable image: {error}") from error
    return material


def _native_frame(value: StructuredValue) -> BackendNativeFrame:
    try:
        frame = BackendNativeFrame(
            **_structured(
                value,
                kind="backend_native_frame",
                schema_name="BackendNativeFrame",
            )
        )
    except TypeError as error:
        raise ContractError(f"invalid BackendNativeFrame: {error}") from error
    try:
        validate_backend_native_frame(frame)
    except SpatialContractError as error:
        raise ContractError(f"invalid BackendNativeFrame: {error}") from error
    return frame


def _validate_mesh_frame(
    store: LocalArtifactStore, mesh: ArtifactRef, frame: BackendNativeFrame
) -> None:
    metadata = store.get_manifest(mesh.artifact_id).identity.identity_metadata
    try:
        validate_mesh_native_frame(metadata, frame)
    except SpatialContractError as error:
        raise ContractError(f"reconstruction {error}") from error


def _backend_metadata(metadata: object, label: str) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        raise ContractError(f"{label} backend_metadata must be an object")
    try:
        canonical_json_bytes(metadata)
    except (TypeError, ValueError) as error:
        raise ContractError(f"{label} backend_metadata must be canonical JSON: {error}") from error
    backend_version = metadata.get("backend_version")
    if backend_version is not None and (
        not isinstance(backend_version, str) or not backend_version
    ):
        raise ContractError(f"{label} backend_metadata.backend_version must be non-empty")
    for name in ("model_digest", "container_digest"):
        item = metadata.get(name)
        if item is not None and not _sha256_digest(item):
            raise ContractError(f"{label} backend_metadata.{name} must be a sha256 digest")
    parameters = metadata.get("parameters")
    if parameters is not None and not isinstance(parameters, dict):
        raise ContractError(f"{label} backend_metadata.parameters must be an object")
    backend_source = metadata.get("backend_source")
    if backend_source is not None:
        if not isinstance(backend_source, dict):
            raise ContractError(f"{label} backend_metadata.backend_source must be an object")
        if (
            not isinstance(backend_source.get("revision"), str)
            or not backend_source["revision"]
            or not _sha256_digest(backend_source.get("source_digest"))
            or not isinstance(backend_source.get("dirty"), bool)
        ):
            raise ContractError(
                f"{label} backend_metadata.backend_source requires revision, source_digest, dirty"
            )
    return metadata


def _sha256_digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", value) is not None


def _provenance_parameters(metadata: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"inference": dict(metadata.get("parameters", {}))}
    if "backend_source" in metadata:
        result["backend_source"] = dict(metadata["backend_source"])
    return result


def _component(value: StructuredValue, store: LocalArtifactStore) -> ComponentProvenance:
    try:
        raw = _structured(value, kind="component_provenance", schema_name="ComponentProvenance")
        artifact_raw = raw["artifact"]
        region_raw = raw.get("region_map")
        if not isinstance(artifact_raw, dict):
            raise TypeError("artifact must be an object")
        if region_raw is not None and not isinstance(region_raw, dict):
            raise TypeError("region_map must be an ArtifactRef")
        artifact = ArtifactRef(**artifact_raw)
        region_map = ArtifactRef(**region_raw) if isinstance(region_raw, dict) else None
        component = ComponentProvenance(
            component_id=raw["component_id"],
            artifact=artifact,
            source=raw["source"],
            provenance_ids=raw.get("provenance_ids", []),
            region_map=region_map,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ContractError(f"invalid ComponentProvenance: {error}") from error
    if not isinstance(component.component_id, str) or not component.component_id:
        raise ContractError("ComponentProvenance component_id must not be empty")
    if component.source not in {"observed", "reconstructed", "generated", "mixed"}:
        raise ContractError(f"invalid ComponentProvenance source: {component.source}")
    if not isinstance(component.provenance_ids, list) or not all(
        isinstance(item, str) for item in component.provenance_ids
    ):
        raise ContractError("ComponentProvenance provenance_ids must be strings")
    if not store.verify_digest(component.artifact):
        raise ContractError("ComponentProvenance references an invalid artifact")
    if component.region_map is not None and not store.verify_digest(component.region_map):
        raise ContractError("ComponentProvenance references an invalid region_map")
    if component.region_map is not None:
        kind = store.get_manifest(component.region_map.artifact_id).identity.kind
        if kind != "quality_evidence":
            raise ContractError(f"ComponentProvenance region_map rejects kind {kind}")
    return component


def _cameras(
    store: LocalArtifactStore, values: list[StructuredValue], bundle: Any
) -> list[CameraRecord]:
    views = {view.view_id: view for view in bundle.views}
    cameras = []
    camera_ids: set[str] = set()
    camera_view_ids: set[str] = set()
    for value in values:
        raw = _structured(value, kind="camera_record", schema_name="CameraRecord")
        camera = camera_record_from_mapping(raw)
        if camera.source != "estimated":
            raise ContractError(
                f"geometry frontend camera {camera.camera_id} source must be estimated"
            )
        if camera.camera_id in camera_ids:
            raise ContractError(f"duplicate camera_id: {camera.camera_id}")
        if camera.image_view_id in camera_view_ids:
            raise ContractError(f"duplicate camera view: {camera.image_view_id}")
        try:
            view = views[camera.image_view_id]
        except KeyError as error:
            raise ContractError(
                f"camera {camera.camera_id} references unknown view {camera.image_view_id}"
            ) from error
        validate_camera_record(camera, view)
        with Image.open(store.blob_path(view.image)) as image:
            image_size = image.size
        if (camera.width, camera.height) != image_size:
            raise ContractError(
                f"camera {camera.camera_id} dimensions differ from view {camera.image_view_id}"
            )
        camera_ids.add(camera.camera_id)
        camera_view_ids.add(camera.image_view_id)
        cameras.append(camera)
    world_frame_ids = {
        camera.T_world_camera.target_frame_id
        for camera in cameras
        if camera.T_world_camera is not None
    }
    if len(world_frame_ids) > 1:
        raise ContractError("geometry frontend cameras must share one world frame")
    return cameras


def _validate_geometry_spatial_contract(
    store: LocalArtifactStore,
    cameras: list[CameraRecord],
    depths: list[ArtifactRef],
    points: ArtifactRef,
) -> None:
    cameras_by_view = {camera.image_view_id: camera for camera in cameras}
    world_frames = {
        camera.T_world_camera.target_frame_id
        for camera in cameras
        if camera.T_world_camera is not None
    }
    if len(world_frames) != 1 or any(camera.T_world_camera is None for camera in cameras):
        raise ContractError(
            "geometry frontend cameras must provide transforms into one common world frame"
        )
    world_frame = next(iter(world_frames))
    points_metadata = store.get_manifest(points.artifact_id).identity.identity_metadata
    if points_metadata.get("frame_id") != world_frame:
        raise ContractError("geometry frontend points frame must match the camera world frame")
    points_unit = points_metadata.get("unit")
    for depth in depths:
        metadata = store.get_manifest(depth.artifact_id).identity.identity_metadata
        view_id = metadata.get("view_id")
        if not isinstance(view_id, str):
            raise ContractError("geometry frontend depth requires string view_id metadata")
        camera = cameras_by_view.get(view_id)
        if camera is None:
            raise ContractError(f"geometry frontend depth references unknown camera view {view_id}")
        if metadata.get("frame_id") != camera.camera_frame_id:
            raise ContractError(
                f"geometry frontend depth frame for view {view_id} must match its camera frame"
            )
        if metadata.get("unit") != points_unit:
            raise ContractError(
                "geometry frontend depths and points require a common unit or explicit conversion"
            )


def _validate_depths(store: LocalArtifactStore, depths: list[ArtifactRef], bundle: Any) -> None:
    views = {view.view_id: view for view in bundle.views}
    depth_view_ids: set[str] = set()
    for depth in depths:
        identity = store.get_manifest(depth.artifact_id).identity
        view_id = identity.identity_metadata.get("view_id")
        if not isinstance(view_id, str) or not view_id:
            raise ContractError("geometry frontend depth requires view_id metadata")
        if view_id not in views:
            raise ContractError(f"geometry frontend depth references unknown view {view_id}")
        if view_id in depth_view_ids:
            raise ContractError(f"duplicate geometry frontend depth for view {view_id}")
        invalid_value = identity.identity_metadata.get("invalid_value")
        if invalid_value is not None and (
            not isinstance(invalid_value, (int, float))
            or isinstance(invalid_value, bool)
            or not math.isfinite(invalid_value)
        ):
            raise ContractError(
                f"geometry frontend depth for view {view_id} invalid_value must be finite numeric"
            )
        try:
            depth_size = inspect_observation_raster(
                store.blob_path(depth),
                kind="depth_map",
                label=f"geometry frontend depth for view {view_id}",
            ).size
            with Image.open(store.blob_path(views[view_id].image)) as source_image:
                source_size = source_image.size
        except OSError as error:
            raise ContractError(f"geometry frontend depth is not readable: {error}") from error
        if depth_size != source_size:
            raise ContractError(
                f"geometry frontend depth dimensions differ for view {view_id}: "
                f"{depth_size} != {source_size}"
            )
        depth_view_ids.add(view_id)


def build_multi_view_asset(
    *,
    observations: ArtifactRef,
    store_path: Path,
    output_path: Path,
    resolved_plan: ResolvedPlan,
    semantics: SemanticInfo | None = None,
    asset_name: str | None = None,
) -> BuildResult:
    store = LocalArtifactStore(store_path)
    pipeline = load_multi_view_pipeline()
    specs = load_default_operator_specs()
    compile_pipeline(pipeline, specs)
    if (
        resolved_plan.pipeline_name != pipeline.name
        or resolved_plan.pipeline_version != pipeline.version
    ):
        raise ContractError("resolved plan does not match the selected multi-view pipeline")
    if resolved_plan.contract_digest != resolve_plan_contract_digest(pipeline, specs):
        raise ContractError("resolved plan contract digest does not match the selected pipeline")
    if set(resolved_plan.backends) != {"estimate_geometry", "reconstruct"}:
        raise ContractError("multi-view workflow requires geometry and reconstruction backends")
    geometry_binding = resolved_plan.backend_for("estimate_geometry", "geometry_frontend@1")
    reconstruction_binding = resolved_plan.backend_for("reconstruct", "reconstruction@1")
    geometry_backend = geometry_binding.geometry_frontend_backend()
    reconstruction_backend = reconstruction_binding.reconstruction_backend()
    runtime = Phase1Runtime(store, pipeline, specs)
    run_id = f"run_{uuid.uuid4().hex}"
    export_profile = StructuredValue(
        "export_profile", "GLTF2Profile", SCHEMA_VERSION, {"profile": "gltf2-v1"}
    )
    run_inputs: dict[str, ArtifactRef | StructuredValue] = {
        "observations": observations,
        "export_profile": export_profile,
    }
    semantic_value = None
    if semantics is not None:
        semantic_value = StructuredValue(
            "semantic_info", "SemanticInfo", SCHEMA_VERSION, to_primitive(semantics)
        )
        run_inputs["semantics"] = semantic_value
    run = BuildRun(
        run_id,
        pipeline.name,
        pipeline.version,
        "running",
        run_inputs,
        runtime.attempts,
        utc_now(),
        None,
        {node_id: binding.name for node_id, binding in resolved_plan.backends.items()},
        resolved_plan.contract_digest,
        {node_id: binding.backend_version for node_id, binding in resolved_plan.backends.items()},
    )
    provenance: list[ArtifactRef] = []
    try:
        runtime.validate_pipeline_inputs(run.inputs)
        bundle = observation_bundle_from_artifact(observations, store)

        def execute_geometry() -> tuple[GeometryFrontendOutput, dict[str, Any]]:
            if semantics is not None:
                validate_semantic_info(semantics)
            try:
                value = geometry_backend.estimate(store, observations)
            except (ContractError, PipelineError):
                raise
            except Exception as error:
                raise PipelineError(ErrorCode.BACKEND_FAILED, str(error)) from error
            return value, {
                "cameras": value.cameras,
                "depths": value.depths,
                "points": value.points,
            }

        def validate_geometry_result(value: GeometryFrontendOutput) -> None:
            cameras = _cameras(store, value.cameras, bundle)
            _validate_depths(store, value.depths, bundle)
            _validate_geometry_spatial_contract(store, cameras, value.depths, value.points)
            _backend_metadata(value.backend_metadata, "geometry frontend")

        geometry = runtime.run_node(
            "estimate_geometry",
            {"observations": observations},
            execute_geometry,
            backend=geometry_binding.name,
            execution_mode=lambda value: "cache_hit" if value.cache_hit else "executed",
            validate_result=validate_geometry_result,
        )
        parsed_cameras = _cameras(store, geometry.cameras, bundle)
        reconstruction_cameras = [
            StructuredValue("camera_record", "CameraRecord", SCHEMA_VERSION, to_primitive(camera))
            for camera in parsed_cameras
        ]
        camera_collection = store.persist_structured(
            StructuredValue(
                "camera_collection",
                "CameraCollection",
                SCHEMA_VERSION,
                {"cameras": [to_primitive(camera) for camera in parsed_cameras]},
            )
        )
        provenance.append(
            _persist_provenance(
                store,
                run_id=run_id,
                node_id="estimate_geometry",
                port_name="cameras",
                artifact=camera_collection,
                derived_from=[observations],
                operator="geometry_frontend",
                backend=geometry_binding.name,
                backend_version=str(
                    geometry.backend_metadata.get(
                        "backend_version", geometry_binding.backend_version
                    )
                ),
                parameters=_provenance_parameters(geometry.backend_metadata),
                seed=None,
                source="estimated",
                model_digest=geometry.backend_metadata.get("model_digest"),
                container_digest=geometry.backend_metadata.get("container_digest"),
            )
        )
        provenance.append(
            _persist_provenance(
                store,
                run_id=run_id,
                node_id="estimate_geometry",
                port_name="points",
                artifact=geometry.points,
                derived_from=[observations],
                operator="geometry_frontend",
                backend=geometry_binding.name,
                backend_version=str(
                    geometry.backend_metadata.get(
                        "backend_version", geometry_binding.backend_version
                    )
                ),
                parameters=_provenance_parameters(geometry.backend_metadata),
                seed=None,
                source="estimated",
                model_digest=geometry.backend_metadata.get("model_digest"),
                container_digest=geometry.backend_metadata.get("container_digest"),
            )
        )
        provenance.extend(
            _persist_provenance(
                store,
                run_id=run_id,
                node_id="estimate_geometry",
                port_name=f"depths[{index}]",
                artifact=depth,
                derived_from=[observations],
                operator="geometry_frontend",
                backend=geometry_binding.name,
                backend_version=str(
                    geometry.backend_metadata.get(
                        "backend_version", geometry_binding.backend_version
                    )
                ),
                parameters=_provenance_parameters(geometry.backend_metadata),
                seed=None,
                source="estimated",
                model_digest=geometry.backend_metadata.get("model_digest"),
                container_digest=geometry.backend_metadata.get("container_digest"),
            )
            for index, depth in enumerate(geometry.depths)
        )

        def execute_reconstruction() -> tuple[ReconstructionOutput, dict[str, Any]]:
            try:
                value = reconstruction_backend.reconstruct(
                    store,
                    observations,
                    reconstruction_cameras,
                    geometry.depths,
                    geometry.points,
                )
            except (ContractError, PipelineError):
                raise
            except Exception as error:
                raise PipelineError(ErrorCode.BACKEND_FAILED, str(error)) from error
            return value, {
                "mesh": value.mesh,
                "material": value.material,
                "native_frame": value.native_frame,
                "components": value.components,
            }

        def validate_reconstruction_result(value: ReconstructionOutput) -> None:
            parsed_components = [_component(item, store) for item in value.components]
            component_ids = [component.component_id for component in parsed_components]
            if len(component_ids) != len(set(component_ids)):
                raise ContractError("reconstruction component_id values must be unique")
            if any(component.artifact != value.mesh for component in parsed_components):
                raise ContractError(
                    "reconstruction components must reference the reconstruction mesh"
                )
            if any(component.provenance_ids for component in parsed_components):
                raise ContractError(
                    "reconstruction components cannot provide unverified provenance_ids"
                )
            _material(value.material, store)
            native_frame = _native_frame(value.native_frame)
            _validate_mesh_frame(store, value.mesh, native_frame)
            points_unit = store.get_manifest(
                geometry.points.artifact_id
            ).identity.identity_metadata["unit"]
            if native_frame.unit != points_unit:
                raise ContractError(
                    "reconstruction output unit must match geometry frontend points unit"
                )
            _backend_metadata(value.backend_metadata, "reconstruction")

        reconstructed = runtime.run_node(
            "reconstruct",
            {
                "observations": observations,
                "cameras": list[PortValue](reconstruction_cameras),
                "depths": list[PortValue](geometry.depths),
                "points": geometry.points,
            },
            execute_reconstruction,
            backend=reconstruction_binding.name,
            execution_mode=lambda value: "cache_hit" if value.cache_hit else "executed",
            validate_result=validate_reconstruction_result,
        )
        material = _material(reconstructed.material, store)
        reconstruction_inputs = [
            observations,
            camera_collection,
            geometry.points,
            *geometry.depths,
        ]
        reconstruction_provenance = _persist_provenance(
            store,
            run_id=run_id,
            node_id="reconstruct",
            port_name="mesh",
            artifact=reconstructed.mesh,
            derived_from=reconstruction_inputs,
            operator="reconstruction",
            backend=reconstruction_binding.name,
            backend_version=str(
                reconstructed.backend_metadata.get(
                    "backend_version", reconstruction_binding.backend_version
                )
            ),
            parameters=_provenance_parameters(reconstructed.backend_metadata),
            seed=None,
            source="reconstructed",
            model_digest=reconstructed.backend_metadata.get("model_digest"),
            container_digest=reconstructed.backend_metadata.get("container_digest"),
        )
        provenance.append(reconstruction_provenance)
        texture_artifacts = [
            (port_name, reference)
            for port_name, reference in (
                ("material.base_color_texture", material.base_color_texture),
                ("material.normal_texture", material.normal_texture),
                ("material.metallic_roughness_texture", material.metallic_roughness_texture),
                ("material.emissive_texture", material.emissive_texture),
            )
            if reference is not None
        ]
        provenance.extend(
            _persist_provenance(
                store,
                run_id=run_id,
                node_id="reconstruct",
                port_name=port_name,
                artifact=reference,
                derived_from=reconstruction_inputs,
                operator="reconstruction",
                backend=reconstruction_binding.name,
                backend_version=str(
                    reconstructed.backend_metadata.get(
                        "backend_version", reconstruction_binding.backend_version
                    )
                ),
                parameters=_provenance_parameters(reconstructed.backend_metadata),
                seed=None,
                source="reconstructed",
                model_digest=reconstructed.backend_metadata.get("model_digest"),
                container_digest=reconstructed.backend_metadata.get("container_digest"),
            )
            for port_name, reference in texture_artifacts
        )
        components = [_component(value, store) for value in reconstructed.components]
        reconstruction_provenance_id = store.read_structured(reconstruction_provenance)[
            "provenance_id"
        ]
        if not isinstance(reconstruction_provenance_id, str):
            raise ContractError("reconstruction provenance_id must be a string")
        region_map_provenance: dict[str, str] = {}
        region_map_artifacts = [
            (index, component)
            for index, component in enumerate(components)
            if component.region_map is not None
        ]
        for index, component in region_map_artifacts:
            assert component.region_map is not None
            reference = _persist_provenance(
                store,
                run_id=run_id,
                node_id="reconstruct",
                port_name=f"components[{index}].region_map",
                artifact=component.region_map,
                derived_from=reconstruction_inputs,
                operator="reconstruction",
                backend=reconstruction_binding.name,
                backend_version=str(
                    reconstructed.backend_metadata.get(
                        "backend_version", reconstruction_binding.backend_version
                    )
                ),
                parameters=_provenance_parameters(reconstructed.backend_metadata),
                seed=None,
                source="reconstructed",
                model_digest=reconstructed.backend_metadata.get("model_digest"),
                container_digest=reconstructed.backend_metadata.get("container_digest"),
            )
            provenance.append(reference)
            provenance_id = store.read_structured(reference)["provenance_id"]
            if not isinstance(provenance_id, str):
                raise ContractError("region map provenance_id must be a string")
            region_map_provenance[component.component_id] = provenance_id
        components = [
            ComponentProvenance(
                component.component_id,
                component.artifact,
                component.source,
                list(
                    dict.fromkeys(
                        [
                            *component.provenance_ids,
                            reconstruction_provenance_id,
                            *(
                                [region_map_provenance[component.component_id]]
                                if component.component_id in region_map_provenance
                                else []
                            ),
                        ]
                    )
                ),
                component.region_map,
            )
            for component in components
        ]
        component_values = [
            StructuredValue(
                "component_provenance",
                "ComponentProvenance",
                SCHEMA_VERSION,
                to_primitive(component),
            )
            for component in components
        ]

        native_frame = _native_frame(reconstructed.native_frame)

        def execute_canonicalize() -> tuple[CanonicalOutput, dict[str, Any]]:
            value = canonicalize_glb(store, reconstructed.mesh, native_frame)
            canonical_components = remap_component_provenance(
                [_component(item, store) for item in component_values],
                source_mesh=reconstructed.mesh,
                canonical_mesh=value.mesh,
            )
            return value, {
                "mesh": value.mesh,
                "canonical_frame": value.frame,
                "transform": value.transform,
                "spatial_info": value.spatial_info,
                "components": [
                    StructuredValue(
                        "component_provenance",
                        "ComponentProvenance",
                        SCHEMA_VERSION,
                        to_primitive(component),
                    )
                    for component in canonical_components
                ],
            }

        canonical = runtime.run_node(
            "canonicalize",
            {
                "mesh": reconstructed.mesh,
                "native_frame": reconstructed.native_frame,
                "components": list[PortValue](component_values),
            },
            execute_canonicalize,
        )
        canonical_component_values = runtime.attempts[-1].outputs["components"]
        assert isinstance(canonical_component_values, list)
        if not all(isinstance(value, StructuredValue) for value in canonical_component_values):
            raise ContractError("canonical components must be structured values")
        canonical_components = [
            _component(value, store)
            for value in canonical_component_values
            if isinstance(value, StructuredValue)
        ]
        provenance.append(
            _persist_provenance(
                store,
                run_id=run_id,
                node_id="canonicalize",
                port_name="mesh",
                artifact=canonical.mesh,
                derived_from=[reconstructed.mesh],
                operator="canonicalize",
                backend="core",
                backend_version="0.1.0",
                parameters=canonical.result.rule_parameters,
                seed=None,
                source="derived",
            )
        )

        def execute_validate() -> tuple[tuple[Any, ArtifactRef], dict[str, Any]]:
            report = validate_geometry(
                store, canonical.mesh, derived_from=reconstructed.mesh, run_id=run_id
            )
            reference = store.persist_structured(
                StructuredValue(
                    "quality_report", "QualityReport", SCHEMA_VERSION, to_primitive(report)
                )
            )
            return (report, reference), {"report": reference}

        report, report_ref = runtime.run_node(
            "validate",
            {"mesh": canonical.mesh, "material": reconstructed.material},
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
                parameters={"profile": "geometry-v1", "render_back": "skipped"},
                seed=None,
                source="derived",
            )
        )

        def execute_assemble() -> tuple[tuple[Any, ArtifactRef], dict[str, Any]]:
            asset = assemble_asset(
                canonical.mesh,
                material,
                canonical.result.spatial_info,
                bundle.observation_id,
                report_ref,
                components=canonical_components,
                semantics=semantics,
                name=asset_name,
            )
            reference = store.persist_structured(
                StructuredValue(
                    "asset_definition", "AssetDefinition", SCHEMA_VERSION, to_primitive(asset)
                )
            )
            return (asset, reference), {"asset": reference}

        assemble_inputs: dict[str, Any] = {
            "mesh": canonical.mesh,
            "material": reconstructed.material,
            "spatial": canonical.spatial_info,
            "quality": report_ref,
            "observations": observations,
            "components": canonical_component_values,
        }
        if semantic_value is not None:
            assemble_inputs["semantics"] = semantic_value
        _, asset_ref = runtime.run_node("assemble_asset", assemble_inputs, execute_assemble)
        provenance.append(
            _persist_provenance(
                store,
                run_id=run_id,
                node_id="assemble_asset",
                port_name="asset",
                artifact=asset_ref,
                derived_from=[
                    canonical.mesh,
                    report_ref,
                    observations,
                    *(reference for _, reference in texture_artifacts),
                    *(
                        component.region_map
                        for component in canonical_components
                        if component.region_map is not None
                    ),
                ],
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
            glb, _ = export_release(store, asset_ref, canonical.mesh, material, report_ref)
            export_provenance = _persist_provenance(
                store,
                run_id=run_id,
                node_id="export",
                port_name="glb",
                artifact=glb,
                derived_from=[
                    asset_ref,
                    canonical.mesh,
                    *(reference for _, reference in texture_artifacts),
                ],
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
                "evidence/geometry/cameras.json": camera_collection,
                **{
                    f"evidence/region-maps/{index:03d}": component.region_map
                    for index, component in enumerate(canonical_components)
                    if component.region_map is not None
                },
                **{
                    f"textures/{name}": reference
                    for name, reference in (
                        ("base-color", material.base_color_texture),
                        ("normal", material.normal_texture),
                        ("metallic-roughness", material.metallic_roughness_texture),
                        ("emissive", material.emissive_texture),
                    )
                    if reference is not None
                },
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
                "material": reconstructed.material,
                "quality": report_ref,
                "profile": export_profile,
            },
            execute_export,
        )
        provenance.append(export_provenance)
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
        run_ref = _persist_build_run(store, run)
        try:
            _materialize_release(store, output_path, release_ref, release, run_ref)
        except Exception:
            release_attempt.status = "failed"
            release_attempt.error_code = "release_failed"
            release_attempt.finished_at = utc_now()
            raise
        return BuildResult(run_id, asset_ref, release_ref, glb_ref, report_ref, output_path)
    except Exception:
        run.status = "failed"
        run.finished_at = utc_now()
        _persist_build_run(store, run)
        raise
