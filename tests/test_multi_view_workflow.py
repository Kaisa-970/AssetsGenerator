from __future__ import annotations

import io
import json

import pytest
import trimesh
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.backend_registry import BackendRegistry, resolve_plan
from assets_generator.contracts import ContractError
from assets_generator.models import (
    SCHEMA_VERSION,
    ArtifactRef,
    BackendNativeFrame,
    CameraRecord,
    ComponentProvenance,
    PBRMaterial,
    SemanticInfo,
    SpatialTransform,
    StructuredValue,
)
from assets_generator.multi_view_workflow import build_multi_view_asset
from assets_generator.observation_import import import_observation_manifest
from assets_generator.operators import GeometryFrontendOutput, ReconstructionOutput
from assets_generator.pipeline import load_default_operator_specs, load_multi_view_pipeline
from assets_generator.serialization import sha256_bytes, to_primitive


class ContractGeometryFrontend:
    def estimate(
        self, store: LocalArtifactStore, observations: ArtifactRef
    ) -> GeometryFrontendOutput:
        camera = CameraRecord(
            "camera_front",
            "front",
            "pinhole",
            8,
            6,
            10.0,
            10.0,
            4.0,
            3.0,
            [],
            "camera_front",
            SpatialTransform(
                "camera_front",
                "contract_world",
                [
                    [1.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0, 0.0],
                    [0.0, 0.0, 0.0, 1.0],
                ],
            ),
            "estimated",
            score=0.8,
            score_method="contract-test",
        )
        points = store.persist_bytes(
            b"contract point cloud",
            kind="point_cloud",
            schema_name="contract_points",
            schema_version="1.0",
            identity_metadata={"frame_id": "contract_world", "unit": "relative_unit"},
        )
        return GeometryFrontendOutput(
            [
                StructuredValue(
                    "camera_record", "CameraRecord", SCHEMA_VERSION, to_primitive(camera)
                )
            ],
            [],
            points,
            {
                "backend_version": "geometry-runtime-test",
                "model_digest": "sha256:" + "1" * 64,
                "container_digest": "sha256:" + "2" * 64,
                "parameters": {"feature_scale": 2},
                "backend_source": {
                    "revision": "geometry-revision",
                    "source_digest": "sha256:" + "3" * 64,
                    "dirty": False,
                },
            },
        )


class TrackingGeometryFrontend(ContractGeometryFrontend):
    def __init__(self) -> None:
        self.calls = 0

    def estimate(self, store, observations):
        self.calls += 1
        return super().estimate(store, observations)


class ContractReconstruction:
    def reconstruct(
        self,
        store: LocalArtifactStore,
        observations: ArtifactRef,
        cameras: list[StructuredValue],
        depths: list[ArtifactRef],
        points: ArtifactRef,
    ) -> ReconstructionOutput:
        scene = trimesh.Scene(trimesh.creation.box(extents=[1.0, 2.0, 3.0]))
        data = scene.export(file_type="glb")
        assert isinstance(data, bytes)
        mesh = store.persist_bytes(
            data,
            kind="triangle_mesh",
            schema_name="glTF",
            schema_version="2.0",
            identity_metadata={
                "frame_id": "reconstruction_native",
                "unit": "relative_unit",
                "up_axis": "+Y",
                "forward_axis": None,
            },
        )
        material = PBRMaterial([0.8, 0.7, 0.6, 1.0])
        frame = BackendNativeFrame(
            "reconstruction_native", "right", "+Y", None, "unknown", "relative_unit"
        )
        component = ComponentProvenance("body", mesh, "reconstructed")
        return ReconstructionOutput(
            mesh,
            StructuredValue("pbr_material", "PBRMaterial", SCHEMA_VERSION, to_primitive(material)),
            StructuredValue(
                "backend_native_frame",
                "BackendNativeFrame",
                SCHEMA_VERSION,
                to_primitive(frame),
            ),
            [
                StructuredValue(
                    "component_provenance",
                    "ComponentProvenance",
                    SCHEMA_VERSION,
                    to_primitive(component),
                )
            ],
            {
                "backend_version": "reconstruction-runtime-test",
                "model_digest": "sha256:" + "4" * 64,
                "container_digest": "sha256:" + "5" * 64,
                "parameters": {"iterations": 12},
                "backend_source": {
                    "revision": "reconstruction-revision",
                    "source_digest": "sha256:" + "6" * 64,
                    "dirty": True,
                },
            },
        )


class WrongGeometryFrontend(ContractGeometryFrontend):
    def estimate(
        self, store: LocalArtifactStore, observations: ArtifactRef
    ) -> GeometryFrontendOutput:
        value = super().estimate(store, observations)
        wrong = store.persist_bytes(
            b"wrong",
            kind="triangle_mesh",
            schema_name="wrong",
            schema_version="1.0",
            identity_metadata={"frame_id": "contract_world", "unit": "relative_unit"},
        )
        return GeometryFrontendOutput(value.cameras, value.depths, wrong, value.backend_metadata)


class ArtifactCameraFrontend(ContractGeometryFrontend):
    def estimate(self, store, observations):
        value = super().estimate(store, observations)
        return GeometryFrontendOutput(
            [value.points], value.depths, value.points, value.backend_metadata
        )


class NonCollectionCameraFrontend(ContractGeometryFrontend):
    def estimate(self, store, observations):
        value = super().estimate(store, observations)
        return GeometryFrontendOutput(
            value.points, value.depths, value.points, value.backend_metadata
        )


class MissingDepthCollectionFrontend(ContractGeometryFrontend):
    def estimate(self, store, observations):
        value = super().estimate(store, observations)
        return GeometryFrontendOutput(value.cameras, None, value.points, value.backend_metadata)


class RegisteredCameraFrontend(ContractGeometryFrontend):
    def estimate(self, store, observations):
        value = super().estimate(store, observations)
        camera = StructuredValue(
            "camera_record",
            "CameraRecord",
            SCHEMA_VERSION,
            {**value.cameras[0].value, "source": "registered"},
        )
        return GeometryFrontendOutput([camera], value.depths, value.points, value.backend_metadata)


class StructuredDepthFrontend(ContractGeometryFrontend):
    def estimate(self, store, observations):
        value = super().estimate(store, observations)
        return GeometryFrontendOutput(
            value.cameras, [value.cameras[0]], value.points, value.backend_metadata
        )


class DanglingDepthFrontend(ContractGeometryFrontend):
    def estimate(self, store, observations):
        value = super().estimate(store, observations)
        return GeometryFrontendOutput(
            value.cameras,
            [ArtifactRef("sha256:" + "f" * 64)],
            value.points,
            value.backend_metadata,
        )


class MalformedCameraFrontend(ContractGeometryFrontend):
    def estimate(
        self, store: LocalArtifactStore, observations: ArtifactRef
    ) -> GeometryFrontendOutput:
        value = super().estimate(store, observations)
        malformed = StructuredValue(
            "camera_record",
            "CameraRecord",
            SCHEMA_VERSION,
            {**value.cameras[0].value, "fx": "invalid"},
        )
        return GeometryFrontendOutput(
            [malformed], value.depths, value.points, value.backend_metadata
        )


class MismatchedCameraSizeFrontend(ContractGeometryFrontend):
    def estimate(
        self, store: LocalArtifactStore, observations: ArtifactRef
    ) -> GeometryFrontendOutput:
        value = super().estimate(store, observations)
        camera = StructuredValue(
            "camera_record",
            "CameraRecord",
            SCHEMA_VERSION,
            {**value.cameras[0].value, "width": 4, "height": 4},
        )
        return GeometryFrontendOutput([camera], value.depths, value.points, value.backend_metadata)


class InconsistentWorldCameraFrontend(ContractGeometryFrontend):
    def estimate(
        self, store: LocalArtifactStore, observations: ArtifactRef
    ) -> GeometryFrontendOutput:
        value = super().estimate(store, observations)
        front = CameraRecord(
            **{
                **value.cameras[0].value,
                "T_world_camera": SpatialTransform(
                    "camera_front",
                    "world_front",
                    [
                        [1.0, 0.0, 0.0, 0.0],
                        [0.0, 1.0, 0.0, 0.0],
                        [0.0, 0.0, 1.0, 0.0],
                        [0.0, 0.0, 0.0, 1.0],
                    ],
                ),
            }
        )
        side = CameraRecord(
            "camera_side",
            "side",
            "pinhole",
            8,
            6,
            10.0,
            10.0,
            4.0,
            3.0,
            [],
            "camera_side",
            SpatialTransform(
                "camera_side",
                "world_side",
                [
                    [1.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0, 0.0],
                    [0.0, 0.0, 0.0, 1.0],
                ],
            ),
            "estimated",
        )
        return GeometryFrontendOutput(
            [
                StructuredValue(
                    "camera_record", "CameraRecord", SCHEMA_VERSION, to_primitive(front)
                ),
                StructuredValue(
                    "camera_record", "CameraRecord", SCHEMA_VERSION, to_primitive(side)
                ),
            ],
            value.depths,
            value.points,
            value.backend_metadata,
        )


class MalformedComponentReconstruction(ContractReconstruction):
    def reconstruct(
        self,
        store: LocalArtifactStore,
        observations: ArtifactRef,
        cameras: list[StructuredValue],
        depths: list[ArtifactRef],
        points: ArtifactRef,
    ) -> ReconstructionOutput:
        value = super().reconstruct(store, observations, cameras, depths, points)
        component = StructuredValue(
            "component_provenance",
            "ComponentProvenance",
            SCHEMA_VERSION,
            {**value.components[0].value, "source": "unsupported"},
        )
        return ReconstructionOutput(
            value.mesh,
            value.material,
            value.native_frame,
            [component],
            value.backend_metadata,
        )


class FailingReconstruction:
    def reconstruct(self, store, observations, cameras, depths, points):
        raise RuntimeError("reconstruction exploded")


class WrongComponentMeshReconstruction(ContractReconstruction):
    def reconstruct(
        self,
        store: LocalArtifactStore,
        observations: ArtifactRef,
        cameras: list[StructuredValue],
        depths: list[ArtifactRef],
        points: ArtifactRef,
    ) -> ReconstructionOutput:
        value = super().reconstruct(store, observations, cameras, depths, points)
        other = store.persist_bytes(
            store.blob_path(value.mesh).read_bytes(),
            kind="triangle_mesh",
            schema_name="glTF",
            schema_version="2.0",
            identity_metadata={"frame_id": "other", "unit": "relative_unit"},
        )
        component = StructuredValue(
            "component_provenance",
            "ComponentProvenance",
            SCHEMA_VERSION,
            {**value.components[0].value, "artifact": to_primitive(other)},
        )
        return ReconstructionOutput(
            value.mesh,
            value.material,
            value.native_frame,
            [component],
            value.backend_metadata,
        )


class UnassociatedDepthFrontend(ContractGeometryFrontend):
    def estimate(
        self, store: LocalArtifactStore, observations: ArtifactRef
    ) -> GeometryFrontendOutput:
        value = super().estimate(store, observations)
        output = io.BytesIO()
        Image.new("I;16", (8, 6), 1000).save(output, format="PNG")
        depth = store.persist_bytes(
            output.getvalue(),
            kind="depth_map",
            schema_name="png-depth-u16",
            schema_version="1.0",
            identity_metadata={"frame_id": "camera_front", "unit": "millimeter"},
        )
        return GeometryFrontendOutput(value.cameras, [depth], value.points, value.backend_metadata)


class ColorDepthFrontend(ContractGeometryFrontend):
    def estimate(
        self, store: LocalArtifactStore, observations: ArtifactRef
    ) -> GeometryFrontendOutput:
        value = super().estimate(store, observations)
        output = io.BytesIO()
        Image.new("RGB", (8, 6), "red").save(output, format="PNG")
        depth = store.persist_bytes(
            output.getvalue(),
            kind="depth_map",
            schema_name="png",
            schema_version="1.0",
            identity_metadata={
                "frame_id": "camera_front",
                "unit": "millimeter",
                "view_id": "front",
            },
        )
        return GeometryFrontendOutput(value.cameras, [depth], value.points, value.backend_metadata)


class InvalidDepthSentinelFrontend(ContractGeometryFrontend):
    def estimate(
        self, store: LocalArtifactStore, observations: ArtifactRef
    ) -> GeometryFrontendOutput:
        value = super().estimate(store, observations)
        output = io.BytesIO()
        Image.new("I;16", (8, 6), 1000).save(output, format="PNG")
        depth = store.persist_bytes(
            output.getvalue(),
            kind="depth_map",
            schema_name="png-depth-u16",
            schema_version="1.0",
            identity_metadata={
                "frame_id": "camera_front",
                "unit": "millimeter",
                "view_id": "front",
                "invalid_value": "unknown",
            },
        )
        return GeometryFrontendOutput(value.cameras, [depth], value.points, value.backend_metadata)


class ValidDepthFrontend(ContractGeometryFrontend):
    def estimate(
        self, store: LocalArtifactStore, observations: ArtifactRef
    ) -> GeometryFrontendOutput:
        value = super().estimate(store, observations)
        output = io.BytesIO()
        Image.new("I;16", (8, 6), 1000).save(output, format="PNG")
        depth = store.persist_bytes(
            output.getvalue(),
            kind="depth_map",
            schema_name="png",
            schema_version="1.0",
            identity_metadata={
                "frame_id": "camera_front",
                "unit": "relative_unit",
                "view_id": "front",
            },
        )
        return GeometryFrontendOutput(value.cameras, [depth], value.points, value.backend_metadata)


class MissingWorldTransformFrontend(ContractGeometryFrontend):
    def estimate(self, store, observations):
        value = super().estimate(store, observations)
        camera = StructuredValue(
            "camera_record",
            "CameraRecord",
            SCHEMA_VERSION,
            {**value.cameras[0].value, "T_world_camera": None},
        )
        return GeometryFrontendOutput([camera], value.depths, value.points, value.backend_metadata)


class MismatchedPointFrameFrontend(ContractGeometryFrontend):
    def estimate(self, store, observations):
        value = super().estimate(store, observations)
        points = store.persist_bytes(
            b"other frame points",
            kind="point_cloud",
            schema_name="contract_points",
            schema_version="1.0",
            identity_metadata={"frame_id": "other_world", "unit": "relative_unit"},
        )
        return GeometryFrontendOutput(value.cameras, value.depths, points, value.backend_metadata)


class MismatchedDepthFrameFrontend(ValidDepthFrontend):
    def estimate(self, store, observations):
        value = super().estimate(store, observations)
        depth = store.persist_bytes(
            store.blob_path(value.depths[0]).read_bytes(),
            kind="depth_map",
            schema_name="png",
            schema_version="1.0",
            identity_metadata={
                "frame_id": "other_camera",
                "unit": "relative_unit",
                "view_id": "front",
            },
        )
        return GeometryFrontendOutput(value.cameras, [depth], value.points, value.backend_metadata)


class MismatchedDepthUnitFrontend(ValidDepthFrontend):
    def estimate(self, store, observations):
        value = super().estimate(store, observations)
        depth = store.persist_bytes(
            store.blob_path(value.depths[0]).read_bytes(),
            kind="depth_map",
            schema_name="png",
            schema_version="1.0",
            identity_metadata={
                "frame_id": "camera_front",
                "unit": "meter",
                "view_id": "front",
            },
        )
        return GeometryFrontendOutput(value.cameras, [depth], value.points, value.backend_metadata)


class MismatchedFrameReconstruction(ContractReconstruction):
    def reconstruct(self, store, observations, cameras, depths, points):
        value = super().reconstruct(store, observations, cameras, depths, points)
        frame = BackendNativeFrame("different_frame", "right", "+Y", None, "unknown", "meter")
        return ReconstructionOutput(
            value.mesh,
            value.material,
            StructuredValue(
                "backend_native_frame",
                "BackendNativeFrame",
                SCHEMA_VERSION,
                to_primitive(frame),
            ),
            value.components,
            value.backend_metadata,
        )


class InvalidAxisReconstruction(ContractReconstruction):
    def reconstruct(self, store, observations, cameras, depths, points):
        value = super().reconstruct(store, observations, cameras, depths, points)
        return ReconstructionOutput(
            value.mesh,
            value.material,
            StructuredValue(
                "backend_native_frame",
                "BackendNativeFrame",
                SCHEMA_VERSION,
                {**value.native_frame.value, "up_axis": "BAD"},
            ),
            value.components,
            value.backend_metadata,
        )


class MismatchedUnitReconstruction(ContractReconstruction):
    def reconstruct(self, store, observations, cameras, depths, points):
        value = super().reconstruct(store, observations, cameras, depths, points)
        frame = BackendNativeFrame("reconstruction_native", "right", "+Y", None, "unknown", "meter")
        return ReconstructionOutput(
            value.mesh,
            value.material,
            StructuredValue(
                "backend_native_frame",
                "BackendNativeFrame",
                SCHEMA_VERSION,
                to_primitive(frame),
            ),
            value.components,
            value.backend_metadata,
        )


class InvalidUnitReconstruction(ContractReconstruction):
    def reconstruct(self, store, observations, cameras, depths, points):
        value = super().reconstruct(store, observations, cameras, depths, points)
        return ReconstructionOutput(
            value.mesh,
            value.material,
            StructuredValue(
                "backend_native_frame",
                "BackendNativeFrame",
                SCHEMA_VERSION,
                {**value.native_frame.value, "unit": "millimeter"},
            ),
            value.components,
            value.backend_metadata,
        )


class DanglingTextureReconstruction(ContractReconstruction):
    def reconstruct(self, store, observations, cameras, depths, points):
        value = super().reconstruct(store, observations, cameras, depths, points)
        return ReconstructionOutput(
            value.mesh,
            StructuredValue(
                "pbr_material",
                "PBRMaterial",
                SCHEMA_VERSION,
                {
                    **value.material.value,
                    "base_color_texture": {"artifact_id": "sha256:" + "0" * 64},
                },
            ),
            value.native_frame,
            value.components,
            value.backend_metadata,
        )


class TexturedReconstruction(ContractReconstruction):
    def reconstruct(self, store, observations, cameras, depths, points):
        value = super().reconstruct(store, observations, cameras, depths, points)
        mesh = trimesh.creation.box(extents=[1.0, 2.0, 3.0])
        uv = mesh.vertices[:, :2].copy()
        uv -= uv.min(axis=0)
        ranges = uv.max(axis=0)
        ranges[ranges == 0.0] = 1.0
        uv /= ranges
        mesh.visual = trimesh.visual.texture.TextureVisuals(uv=uv)
        scene = trimesh.Scene(mesh)
        mesh_data = scene.export(file_type="glb")
        assert isinstance(mesh_data, bytes)
        mesh_ref = store.persist_bytes(
            mesh_data,
            kind="triangle_mesh",
            schema_name="glTF",
            schema_version="2.0",
            identity_metadata={
                "frame_id": "reconstruction_native",
                "unit": "relative_unit",
                "up_axis": "+Y",
                "forward_axis": None,
            },
        )
        texture_data = io.BytesIO()
        Image.new("RGBA", (2, 2), (12, 34, 56, 255)).save(texture_data, format="PNG")
        texture = store.persist_bytes(
            texture_data.getvalue(),
            kind="texture_2d",
            schema_name="png",
            schema_version="1.0",
        )
        material = PBRMaterial([0.8, 0.7, 0.6, 1.0], base_color_texture=texture)
        return ReconstructionOutput(
            mesh_ref,
            StructuredValue("pbr_material", "PBRMaterial", SCHEMA_VERSION, to_primitive(material)),
            value.native_frame,
            [
                StructuredValue(
                    "component_provenance",
                    "ComponentProvenance",
                    SCHEMA_VERSION,
                    to_primitive(ComponentProvenance("body", mesh_ref, "reconstructed")),
                )
            ],
            value.backend_metadata,
        )


class CorruptTextureReconstruction(ContractReconstruction):
    def reconstruct(self, store, observations, cameras, depths, points):
        value = super().reconstruct(store, observations, cameras, depths, points)
        texture = store.persist_bytes(
            b"not an image",
            kind="texture_2d",
            schema_name="png",
            schema_version="1.0",
        )
        material = PBRMaterial([0.8, 0.7, 0.6, 1.0], base_color_texture=texture)
        return ReconstructionOutput(
            value.mesh,
            StructuredValue("pbr_material", "PBRMaterial", SCHEMA_VERSION, to_primitive(material)),
            value.native_frame,
            value.components,
            value.backend_metadata,
        )


class InvalidMetadataReconstruction(ContractReconstruction):
    def reconstruct(self, store, observations, cameras, depths, points):
        value = super().reconstruct(store, observations, cameras, depths, points)
        return ReconstructionOutput(
            value.mesh,
            value.material,
            value.native_frame,
            value.components,
            {"backend_version": "test", "model_digest": object()},
        )


class InvalidDigestReconstruction(ContractReconstruction):
    def reconstruct(self, store, observations, cameras, depths, points):
        value = super().reconstruct(store, observations, cameras, depths, points)
        return ReconstructionOutput(
            value.mesh,
            value.material,
            value.native_frame,
            value.components,
            {**value.backend_metadata, "model_digest": "abc"},
        )


class InvalidDigestFieldsReconstruction(ContractReconstruction):
    def __init__(self, field: str, digest: str) -> None:
        self.field = field
        self.digest = digest

    def reconstruct(self, store, observations, cameras, depths, points):
        value = super().reconstruct(store, observations, cameras, depths, points)
        metadata = dict(value.backend_metadata)
        if self.field == "source_digest":
            metadata["backend_source"] = {
                **metadata["backend_source"],
                "source_digest": self.digest,
            }
        else:
            metadata[self.field] = self.digest
        return ReconstructionOutput(
            value.mesh,
            value.material,
            value.native_frame,
            value.components,
            metadata,
        )


class InjectedProvenanceReconstruction(ContractReconstruction):
    def reconstruct(self, store, observations, cameras, depths, points):
        value = super().reconstruct(store, observations, cameras, depths, points)
        component = StructuredValue(
            "component_provenance",
            "ComponentProvenance",
            SCHEMA_VERSION,
            {**value.components[0].value, "provenance_ids": ["sha256:" + "0" * 64]},
        )
        return ReconstructionOutput(
            value.mesh,
            value.material,
            value.native_frame,
            [component],
            value.backend_metadata,
        )


class InvalidRegionMapReconstruction(ContractReconstruction):
    def reconstruct(self, store, observations, cameras, depths, points):
        value = super().reconstruct(store, observations, cameras, depths, points)
        component = StructuredValue(
            "component_provenance",
            "ComponentProvenance",
            SCHEMA_VERSION,
            {**value.components[0].value, "region_map": "invalid"},
        )
        return ReconstructionOutput(
            value.mesh,
            value.material,
            value.native_frame,
            [component],
            value.backend_metadata,
        )


class RegionMapReconstruction(ContractReconstruction):
    def reconstruct(self, store, observations, cameras, depths, points):
        value = super().reconstruct(store, observations, cameras, depths, points)
        region_map = store.persist_bytes(
            b"opaque region evidence",
            kind="quality_evidence",
            schema_name="contract-region-map",
            schema_version="1.0",
        )
        component = StructuredValue(
            "component_provenance",
            "ComponentProvenance",
            SCHEMA_VERSION,
            {**value.components[0].value, "region_map": to_primitive(region_map)},
        )
        return ReconstructionOutput(
            value.mesh,
            value.material,
            value.native_frame,
            [component],
            value.backend_metadata,
        )


class WrongKindRegionMapReconstruction(ContractReconstruction):
    def reconstruct(self, store, observations, cameras, depths, points):
        value = super().reconstruct(store, observations, cameras, depths, points)
        component = StructuredValue(
            "component_provenance",
            "ComponentProvenance",
            SCHEMA_VERSION,
            {**value.components[0].value, "region_map": to_primitive(value.mesh)},
        )
        return ReconstructionOutput(
            value.mesh,
            value.material,
            value.native_frame,
            [component],
            value.backend_metadata,
        )


def _observations(tmp_path, store: LocalArtifactStore) -> ArtifactRef:
    Image.new("RGB", (8, 6), "red").save(tmp_path / "front.png")
    Image.new("RGB", (8, 6), "blue").save(tmp_path / "side.png")
    manifest = tmp_path / "observations.json"
    manifest.write_text(
        json.dumps(
            {
                "views": [
                    {"view_id": "front", "image": "front.png"},
                    {"view_id": "side", "image": "side.png"},
                ]
            }
        ),
        encoding="utf-8",
    )
    return import_observation_manifest(manifest, store)


def _plan(geometry: object, reconstruction: object):
    registry = BackendRegistry()
    registry.register(
        name="contract_geometry",
        operator="geometry_frontend@1",
        backend_version="geometry-test",
        implementation=geometry,
    )
    registry.register(
        name="contract_reconstruction",
        operator="reconstruction@1",
        backend_version="reconstruction-test",
        implementation=reconstruction,
    )
    return resolve_plan(
        load_multi_view_pipeline(),
        registry,
        operator_specs=load_default_operator_specs(),
        backend_overrides={
            "estimate_geometry": "contract_geometry",
            "reconstruct": "contract_reconstruction",
        },
    )


def test_multi_view_workflow_executes_contract_pipeline(tmp_path) -> None:
    store_path = tmp_path / "store"
    store = LocalArtifactStore(store_path)
    observations = _observations(tmp_path, store)
    output = tmp_path / "release"
    plan = _plan(ContractGeometryFrontend(), ContractReconstruction())

    result = build_multi_view_asset(
        observations=observations,
        store_path=store_path,
        output_path=output,
        resolved_plan=plan,
        asset_name="contract box",
    )

    assert result.output_directory == output
    loaded = trimesh.load(
        io.BytesIO((output / "geometry/visual.glb").read_bytes()), file_type="glb"
    )
    assert isinstance(loaded, trimesh.Scene)
    asset = json.loads((output / "asset.json").read_text(encoding="utf-8"))
    assert asset["component_provenance"][0]["component_id"] == "body"
    assert asset["component_provenance"][0]["source"] == "reconstructed"
    assert asset["component_provenance"][0]["artifact"] == asset["geometry"]["visual_meshes"][0]
    assert len(asset["component_provenance"][0]["provenance_ids"]) == 1
    provenance_records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((output / "provenance").glob("*.json"))
    ]
    reconstruction_record = next(
        record for record in provenance_records if record["node_id"] == "reconstruct"
    )
    geometry_records = [
        record for record in provenance_records if record["node_id"] == "estimate_geometry"
    ]
    geometry_record = next(
        record
        for record in geometry_records
        if store.get_manifest(record["output_artifact_id"]).identity.kind == "point_cloud"
    )
    camera_record = next(
        record
        for record in geometry_records
        if store.get_manifest(record["output_artifact_id"]).identity.kind == "camera_collection"
    )
    camera_collection = store.read_structured(ArtifactRef(camera_record["output_artifact_id"]))
    assert camera_collection["cameras"][0]["camera_id"] == "camera_front"
    assert camera_record["output_artifact_id"] in reconstruction_record["derived_from_artifact_ids"]
    assert (
        json.loads((output / "release.json").read_text(encoding="utf-8"))["files"][
            "evidence/geometry/cameras.json"
        ]["artifact_id"]
        == camera_record["output_artifact_id"]
    )
    assert geometry_record["backend_version"] == "geometry-runtime-test"
    assert geometry_record["model_digest"] == "sha256:" + "1" * 64
    assert geometry_record["container_digest"] == "sha256:" + "2" * 64
    assert geometry_record["parameters"] == {
        "inference": {"feature_scale": 2},
        "backend_source": {
            "revision": "geometry-revision",
            "source_digest": "sha256:" + "3" * 64,
            "dirty": False,
        },
    }
    assert reconstruction_record["backend_version"] == "reconstruction-runtime-test"
    assert reconstruction_record["model_digest"] == "sha256:" + "4" * 64
    assert reconstruction_record["container_digest"] == "sha256:" + "5" * 64
    assert reconstruction_record["parameters"] == {
        "inference": {"iterations": 12},
        "backend_source": {
            "revision": "reconstruction-revision",
            "source_digest": "sha256:" + "6" * 64,
            "dirty": True,
        },
    }
    assert asset["component_provenance"][0]["provenance_ids"] == [
        reconstruction_record["provenance_id"]
    ]
    report = json.loads((output / "qa/quality-report.json").read_text(encoding="utf-8"))
    render_back = next(check for check in report["checks"] if check["check_id"] == "render_back")
    assert render_back["applicable"] is False
    assert render_back["status"] == "skipped"
    assert render_back["reason"] == "camera registration is not implemented for this pipeline"
    run = json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert run["pipeline_name"] == "multi_view_asset_v1"
    assert run["status"] == "succeeded"
    assert run["resolved_backends"] == {
        "estimate_geometry": "contract_geometry",
        "reconstruct": "contract_reconstruction",
    }
    assert run["resolved_plan_contract_digest"] == plan.contract_digest
    assert run["resolved_backend_versions"] == {
        "estimate_geometry": "geometry-test",
        "reconstruct": "reconstruction-test",
    }
    assert [attempt["node_id"] for attempt in run["node_attempts"]] == [
        "estimate_geometry",
        "reconstruct",
        "canonicalize",
        "validate",
        "assemble_asset",
        "export",
        "materialize_release",
    ]
    assert run == store.get_build_run(result.run_id)
    release = json.loads((output / "release.json").read_text(encoding="utf-8"))
    for reference in release["files"].values():
        assert store.verify_digest(ArtifactRef(**reference))
    assert store.verify_digest(result.asset_definition)
    assert store.verify_digest(result.release_manifest)


def test_multi_view_workflow_rejects_tampered_observation_bundle_before_backend(tmp_path) -> None:
    store_path = tmp_path / "store"
    store = LocalArtifactStore(store_path)
    observations = _observations(tmp_path, store)
    blob = store.blob_path(observations)
    blob.write_bytes(blob.read_bytes() + b"\n")
    geometry = TrackingGeometryFrontend()

    with pytest.raises(ContractError, match="artifact has invalid digest"):
        build_multi_view_asset(
            observations=observations,
            store_path=store_path,
            output_path=tmp_path / "release",
            resolved_plan=_plan(geometry, ContractReconstruction()),
        )

    assert geometry.calls == 0


def test_geometry_frontend_depth_has_independent_provenance(tmp_path) -> None:
    store_path = tmp_path / "store"
    store = LocalArtifactStore(store_path)
    observations = _observations(tmp_path, store)
    output = tmp_path / "release"

    build_multi_view_asset(
        observations=observations,
        store_path=store_path,
        output_path=output,
        resolved_plan=_plan(ValidDepthFrontend(), ContractReconstruction()),
    )

    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((output / "provenance").glob("*.json"))
    ]
    geometry_records = [record for record in records if record["node_id"] == "estimate_geometry"]
    run_id = json.loads((output / "run.json").read_text(encoding="utf-8"))["run_id"]
    assert {record["output_id"] for record in geometry_records} == {
        sha256_bytes(f"{run_id}:estimate_geometry:1:cameras".encode()),
        sha256_bytes(f"{run_id}:estimate_geometry:1:points".encode()),
        sha256_bytes(f"{run_id}:estimate_geometry:1:depths[0]".encode()),
    }
    assert len(geometry_records) == 3
    assert len({record["output_artifact_id"] for record in geometry_records}) == 3


def test_multi_view_workflow_rejects_invalid_backend_output_and_records_failure(
    tmp_path,
) -> None:
    store_path = tmp_path / "store"
    store = LocalArtifactStore(store_path)
    observations = _observations(tmp_path, store)
    plan = _plan(WrongGeometryFrontend(), ContractReconstruction())

    with pytest.raises(ContractError, match="rejects kind triangle_mesh"):
        build_multi_view_asset(
            observations=observations,
            store_path=store_path,
            output_path=tmp_path / "release",
            resolved_plan=plan,
        )

    run_index = next((store.root / "runs").glob("*.json"))
    run = store.get_build_run(run_index.stem)
    assert run["status"] == "failed"
    assert run["node_attempts"][-1]["node_id"] == "estimate_geometry"
    assert run["node_attempts"][-1]["error_code"] == "contract_error"
    assert run["resolved_plan_contract_digest"] == plan.contract_digest
    assert run["resolved_backend_versions"] == {
        "estimate_geometry": "geometry-test",
        "reconstruct": "reconstruction-test",
    }


def test_multi_view_workflow_materializes_valid_texture(tmp_path) -> None:
    store_path = tmp_path / "store"
    store = LocalArtifactStore(store_path)
    observations = _observations(tmp_path, store)
    output = tmp_path / "release"

    build_multi_view_asset(
        observations=observations,
        store_path=store_path,
        output_path=output,
        resolved_plan=_plan(ContractGeometryFrontend(), TexturedReconstruction()),
    )

    release = json.loads((output / "release.json").read_text(encoding="utf-8"))
    assert "textures/base-color" in release["files"]
    with Image.open(output / "textures/base-color") as texture:
        assert texture.size == (2, 2)
        assert texture.getpixel((0, 0)) == (12, 34, 56, 255)
    scene = trimesh.load(
        io.BytesIO((output / "geometry/visual.glb").read_bytes()),
        file_type="glb",
        force="scene",
    )
    geometry = next(iter(scene.geometry.values()))
    embedded = geometry.visual.material.baseColorTexture
    assert embedded.size == (2, 2)
    assert embedded.convert("RGBA").getpixel((0, 0)) == (12, 34, 56, 255)
    assert len(geometry.visual.uv) == len(geometry.vertices)
    assert list(geometry.visual.material.baseColorFactor) == [204, 178, 153, 255]
    asset = json.loads((output / "asset.json").read_text(encoding="utf-8"))
    assert (
        asset["appearance"]["materials"][0]["base_color_texture"]
        == release["files"]["textures/base-color"]
    )
    texture_ref = ArtifactRef(**release["files"]["textures/base-color"])
    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((output / "provenance").glob("*.json"))
    ]
    texture_record = next(
        record for record in records if record["output_artifact_id"] == texture_ref.artifact_id
    )
    assert texture_record["node_id"] == "reconstruct"
    assert texture_record["backend_version"] == "reconstruction-runtime-test"
    asset_record = next(record for record in records if record["node_id"] == "assemble_asset")
    assert texture_ref.artifact_id in asset_record["derived_from_artifact_ids"]


def test_multi_view_workflow_materializes_region_map_evidence(tmp_path) -> None:
    store_path = tmp_path / "store"
    store = LocalArtifactStore(store_path)
    observations = _observations(tmp_path, store)
    output = tmp_path / "release"

    build_multi_view_asset(
        observations=observations,
        store_path=store_path,
        output_path=output,
        resolved_plan=_plan(ContractGeometryFrontend(), RegionMapReconstruction()),
    )

    release = json.loads((output / "release.json").read_text(encoding="utf-8"))
    path = "evidence/region-maps/000"
    assert path in release["files"]
    assert (output / path).read_bytes() == b"opaque region evidence"
    asset = json.loads((output / "asset.json").read_text(encoding="utf-8"))
    assert asset["component_provenance"][0]["region_map"] == release["files"][path]
    region_map_id = release["files"][path]["artifact_id"]
    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((output / "provenance").glob("*.json"))
    ]
    region_record = next(
        record for record in records if record["output_artifact_id"] == region_map_id
    )
    assert region_record["node_id"] == "reconstruct"
    assert region_record["output_id"] == sha256_bytes(
        f"{json.loads((output / 'run.json').read_text(encoding='utf-8'))['run_id']}:"
        "reconstruct:1:components[0].region_map".encode()
    )
    assert region_record["provenance_id"] in asset["component_provenance"][0]["provenance_ids"]
    asset_record = next(record for record in records if record["node_id"] == "assemble_asset")
    assert region_map_id in asset_record["derived_from_artifact_ids"]


def test_multi_view_workflow_rejects_invalid_semantics_before_backend(tmp_path) -> None:
    store_path = tmp_path / "store"
    store = LocalArtifactStore(store_path)
    observations = _observations(tmp_path, store)
    geometry = TrackingGeometryFrontend()

    with pytest.raises(ContractError, match="SemanticInfo source is invalid"):
        build_multi_view_asset(
            observations=observations,
            store_path=store_path,
            output_path=tmp_path / "release",
            resolved_plan=_plan(geometry, ContractReconstruction()),
            semantics=SemanticInfo("chair", "invalid"),
        )

    assert geometry.calls == 0
    run_index = next((store.root / "runs").glob("*.json"))
    run = store.get_build_run(run_index.stem)
    assert run["status"] == "failed"
    assert run["node_attempts"][-1]["node_id"] == "estimate_geometry"
    assert run["node_attempts"][-1]["error_code"] == "contract_error"


@pytest.mark.parametrize(
    ("geometry", "reconstruction", "message", "node_id", "error_code"),
    [
        (
            ArtifactCameraFrontend(),
            ContractReconstruction(),
            "cameras rejects carrier artifact_ref",
            "estimate_geometry",
            "contract_error",
        ),
        (
            NonCollectionCameraFrontend(),
            ContractReconstruction(),
            "cameras requires a list value",
            "estimate_geometry",
            "contract_error",
        ),
        (
            MissingDepthCollectionFrontend(),
            ContractReconstruction(),
            "depths requires a list value",
            "estimate_geometry",
            "contract_error",
        ),
        (
            RegisteredCameraFrontend(),
            ContractReconstruction(),
            "source must be estimated",
            "estimate_geometry",
            "contract_error",
        ),
        (
            StructuredDepthFrontend(),
            ContractReconstruction(),
            "depths rejects carrier structured",
            "estimate_geometry",
            "contract_error",
        ),
        (
            DanglingDepthFrontend(),
            ContractReconstruction(),
            "artifact has invalid digest",
            "estimate_geometry",
            "contract_error",
        ),
        (
            MalformedCameraFrontend(),
            ContractReconstruction(),
            "non-finite calibration",
            "estimate_geometry",
            "contract_error",
        ),
        (
            MismatchedCameraSizeFrontend(),
            ContractReconstruction(),
            "dimensions differ from view",
            "estimate_geometry",
            "contract_error",
        ),
        (
            InconsistentWorldCameraFrontend(),
            ContractReconstruction(),
            "cameras must share one world frame",
            "estimate_geometry",
            "contract_error",
        ),
        (
            MissingWorldTransformFrontend(),
            ContractReconstruction(),
            "transforms into one common world frame",
            "estimate_geometry",
            "contract_error",
        ),
        (
            MismatchedPointFrameFrontend(),
            ContractReconstruction(),
            "points frame must match",
            "estimate_geometry",
            "contract_error",
        ),
        (
            MismatchedDepthFrameFrontend(),
            ContractReconstruction(),
            "must match its camera frame",
            "estimate_geometry",
            "contract_error",
        ),
        (
            MismatchedDepthUnitFrontend(),
            ContractReconstruction(),
            "require a common unit",
            "estimate_geometry",
            "contract_error",
        ),
        (
            ContractGeometryFrontend(),
            MalformedComponentReconstruction(),
            "invalid ComponentProvenance source",
            "reconstruct",
            "contract_error",
        ),
        (
            ContractGeometryFrontend(),
            FailingReconstruction(),
            "reconstruction exploded",
            "reconstruct",
            "backend_failed",
        ),
        (
            ContractGeometryFrontend(),
            WrongComponentMeshReconstruction(),
            "must reference the reconstruction mesh",
            "reconstruct",
            "contract_error",
        ),
        (
            ContractGeometryFrontend(),
            MismatchedFrameReconstruction(),
            "frame_id does not match",
            "reconstruct",
            "contract_error",
        ),
        (
            ContractGeometryFrontend(),
            InvalidAxisReconstruction(),
            "invalid BackendNativeFrame: invalid up axis",
            "reconstruct",
            "contract_error",
        ),
        (
            ContractGeometryFrontend(),
            InvalidUnitReconstruction(),
            "invalid native frame unit",
            "reconstruct",
            "contract_error",
        ),
        (
            ContractGeometryFrontend(),
            MismatchedUnitReconstruction(),
            "unit does not match",
            "reconstruct",
            "contract_error",
        ),
        (
            ContractGeometryFrontend(),
            CorruptTextureReconstruction(),
            "is not a decodable image",
            "reconstruct",
            "contract_error",
        ),
        (
            ContractGeometryFrontend(),
            DanglingTextureReconstruction(),
            "references an invalid artifact",
            "reconstruct",
            "contract_error",
        ),
        (
            ContractGeometryFrontend(),
            InvalidMetadataReconstruction(),
            "backend_metadata must be canonical JSON",
            "reconstruct",
            "contract_error",
        ),
        (
            ContractGeometryFrontend(),
            InvalidDigestReconstruction(),
            "model_digest must be a sha256 digest",
            "reconstruct",
            "contract_error",
        ),
        (
            ContractGeometryFrontend(),
            InjectedProvenanceReconstruction(),
            "cannot provide unverified provenance_ids",
            "reconstruct",
            "contract_error",
        ),
        (
            ContractGeometryFrontend(),
            InvalidRegionMapReconstruction(),
            "region_map must be an ArtifactRef",
            "reconstruct",
            "contract_error",
        ),
        (
            ContractGeometryFrontend(),
            WrongKindRegionMapReconstruction(),
            "region_map rejects kind triangle_mesh",
            "reconstruct",
            "contract_error",
        ),
        (
            UnassociatedDepthFrontend(),
            ContractReconstruction(),
            "depth requires view_id metadata",
            "estimate_geometry",
            "contract_error",
        ),
        (
            ColorDepthFrontend(),
            ContractReconstruction(),
            "single-channel numeric raster",
            "estimate_geometry",
            "contract_error",
        ),
        (
            InvalidDepthSentinelFrontend(),
            ContractReconstruction(),
            "invalid_value must be finite numeric",
            "estimate_geometry",
            "contract_error",
        ),
    ],
)
def test_multi_view_workflow_records_backend_semantic_failures(
    tmp_path, geometry, reconstruction, message, node_id, error_code
) -> None:
    store_path = tmp_path / "store"
    store = LocalArtifactStore(store_path)
    observations = _observations(tmp_path, store)

    with pytest.raises((ContractError, RuntimeError), match=message):
        build_multi_view_asset(
            observations=observations,
            store_path=store_path,
            output_path=tmp_path / "release",
            resolved_plan=_plan(geometry, reconstruction),
        )

    run_index = next((store.root / "runs").glob("*.json"))
    run = store.get_build_run(run_index.stem)
    assert run["status"] == "failed"
    assert run["node_attempts"][-1]["node_id"] == node_id
    assert run["node_attempts"][-1]["error_code"] == error_code


@pytest.mark.parametrize("field", ["model_digest", "container_digest", "source_digest"])
@pytest.mark.parametrize("prefix", ["+", "-", " "])
def test_multi_view_workflow_rejects_non_hex_backend_digests(tmp_path, field, prefix) -> None:
    store_path = tmp_path / "store"
    store = LocalArtifactStore(store_path)
    observations = _observations(tmp_path, store)
    digest = "sha256:" + prefix + "a" * 63

    with pytest.raises(ContractError, match="sha256 digest|requires revision"):
        build_multi_view_asset(
            observations=observations,
            store_path=store_path,
            output_path=tmp_path / "release",
            resolved_plan=_plan(
                ContractGeometryFrontend(), InvalidDigestFieldsReconstruction(field, digest)
            ),
        )


def test_multi_view_release_materialization_failure_is_atomic(tmp_path, monkeypatch) -> None:
    store_path = tmp_path / "store"
    store = LocalArtifactStore(store_path)
    observations = _observations(tmp_path, store)
    output = tmp_path / "release"
    monkeypatch.setattr(
        "assets_generator.workflow.shutil.copyfile",
        lambda *args: (_ for _ in ()).throw(OSError("copy failed")),
    )

    with pytest.raises(OSError, match="copy failed"):
        build_multi_view_asset(
            observations=observations,
            store_path=store_path,
            output_path=output,
            resolved_plan=_plan(ContractGeometryFrontend(), ContractReconstruction()),
        )

    assert not output.exists()
    assert not list(tmp_path.glob(".release.*.tmp"))
    run_index = next((store.root / "runs").glob("*.json"))
    run = store.get_build_run(run_index.stem)
    assert run["status"] == "failed"
    assert run["node_attempts"][-1]["node_id"] == "materialize_release"
    assert run["node_attempts"][-1]["status"] == "failed"
    assert run["node_attempts"][-1]["error_code"] == "release_failed"
