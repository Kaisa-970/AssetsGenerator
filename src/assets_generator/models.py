from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

SCHEMA_VERSION = "1.0"

ARTIFACT_KINDS = frozenset(
    {
        "rgb_image",
        "rgba_image",
        "binary_mask",
        "depth_map",
        "texture_2d",
        "normal_map",
        "metallic_roughness_map",
        "triangle_mesh",
        "gltf_asset",
        "point_cloud",
        "gaussian_splat",
        "quality_evidence",
        "zip_bundle",
        "observation_bundle",
        "camera_collection",
        "completion_candidate",
        "asset_definition",
        "asset_release",
        "quality_report",
        "provenance_record",
        "build_run",
    }
)

STRUCTURED_KINDS = frozenset(
    {
        "camera_record",
        "spatial_transform",
        "image_warp",
        "backend_native_frame",
        "camera_registration",
        "pbr_material",
        "observation_bundle",
        "asset_definition",
        "asset_release",
        "asset_spatial_info",
        "frame",
        "semantic_info",
        "quality_report",
        "export_profile",
        "segmentation_result",
        "component_provenance",
    }
)


@dataclass(frozen=True)
class BlobIdentity:
    digest: str
    byte_size: int


@dataclass(frozen=True)
class BlobLocation:
    digest: str
    uri: str


@dataclass(frozen=True)
class ArtifactIdentity:
    kind: str
    schema_name: str
    schema_version: str
    blob_digest: str
    identity_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArtifactAnnotations:
    labels: dict[str, str] = field(default_factory=dict)
    created_at: str | None = None
    debug: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArtifactManifest:
    artifact_id: str
    identity: ArtifactIdentity
    annotations: ArtifactAnnotations = field(default_factory=ArtifactAnnotations)


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str


@dataclass(frozen=True)
class StructuredValue:
    kind: str
    schema_name: str
    schema_version: str
    value: dict[str, Any]
    provenance_id: str | None = None


PortValue: TypeAlias = ArtifactRef | StructuredValue


@dataclass(frozen=True)
class Confidence:
    value: float
    method: str
    method_version: str
    calibration_domain: str | None
    evidence_ids: list[str]


@dataclass(frozen=True)
class ProvenanceRecord:
    provenance_id: str
    output_id: str
    output_artifact_id: str
    derived_from_artifact_ids: list[str]
    operator: str
    operator_version: str
    backend: str
    backend_version: str
    model_digest: str | None
    container_digest: str | None
    parameters: dict[str, Any]
    seed: int | None
    run_id: str
    node_id: str
    attempt: int
    source: str
    confidence: Confidence | None = None
    score: float | None = None
    score_method: str | None = None


@dataclass(frozen=True)
class Frame:
    frame_id: str
    kind: str
    handedness: Literal["right", "left"]
    up_axis: str
    forward_axis: str | None
    unit: str


@dataclass(frozen=True)
class BackendNativeFrame:
    frame_id: str
    handedness: Literal["right", "left"]
    up_axis: str
    forward_axis: str | None
    forward_status: Literal["declared", "estimated", "unknown"]
    unit: str


@dataclass(frozen=True)
class SpatialTransform:
    source_frame_id: str
    target_frame_id: str
    matrix: list[list[float]]


@dataclass(frozen=True)
class CameraRecord:
    camera_id: str
    image_view_id: str
    model: Literal["pinhole", "opencv"]
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    distortion: list[float]
    camera_frame_id: str
    T_world_camera: SpatialTransform | None
    source: str
    confidence: Confidence | None = None
    score: float | None = None
    score_method: str | None = None


@dataclass(frozen=True)
class ObservationView:
    view_id: str
    image: ArtifactRef
    mask: ArtifactRef | None = None
    depth: ArtifactRef | None = None
    camera: CameraRecord | None = None


@dataclass(frozen=True)
class ObservationBundle:
    observation_id: str
    views: list[ObservationView]


@dataclass(frozen=True)
class ComponentProvenance:
    component_id: str
    artifact: ArtifactRef
    source: Literal["observed", "reconstructed", "generated", "mixed"]
    provenance_ids: list[str] = field(default_factory=list)
    region_map: ArtifactRef | None = None


@dataclass(frozen=True)
class AABB:
    minimum: list[float]
    maximum: list[float]


@dataclass(frozen=True)
class AssetSpatialInfo:
    canonical_frame_id: str
    aabb: AABB
    obb: dict[str, Any] | None
    scale_status: Literal["metric", "relative", "unknown"]
    unit: str
    forward_status: Literal["declared", "estimated", "unknown"]


@dataclass(frozen=True)
class PBRMaterial:
    base_color_factor: list[float]
    base_color_texture: ArtifactRef | None = None
    normal_texture: ArtifactRef | None = None
    metallic_roughness_texture: ArtifactRef | None = None
    emissive_texture: ArtifactRef | None = None
    alpha_mode: str = "OPAQUE"


@dataclass(frozen=True)
class SemanticInfo:
    semantic_class: str | None
    source: str


@dataclass(frozen=True)
class GeometrySet:
    visual_meshes: list[ArtifactRef]
    gaussians: list[ArtifactRef] = field(default_factory=list)
    point_clouds: list[ArtifactRef] = field(default_factory=list)
    collision_meshes: list[ArtifactRef] = field(default_factory=list)


@dataclass(frozen=True)
class AppearanceSet:
    materials: list[PBRMaterial]


@dataclass(frozen=True)
class QualityCheck:
    check_id: str
    applicable: bool
    value: float | int | str | None
    threshold_profile: str
    status: Literal["pass", "warn", "fail", "skipped"]
    reason: str | None = None
    evidence_artifacts: list[ArtifactRef] = field(default_factory=list)


@dataclass(frozen=True)
class QualityReport:
    profile: str
    checks: list[QualityCheck]
    overall_status: Literal["pass", "warn", "fail"]


@dataclass(frozen=True)
class AssetDefinition:
    asset_id: str
    asset_version: str
    name: str | None
    geometry: GeometrySet
    appearance: AppearanceSet
    spatial: AssetSpatialInfo
    semantics: SemanticInfo
    physics: dict[str, Any] | None
    source_observation_ids: list[str]
    quality_report_ids: list[str]
    component_provenance: list[ComponentProvenance] = field(default_factory=list)


@dataclass(frozen=True)
class AssetRelease:
    asset_definition: ArtifactRef
    files: dict[str, ArtifactRef]
    export_profile: str


@dataclass
class NodeAttempt:
    node_id: str
    attempt: int
    operator: str
    backend: str | None
    status: str
    execution_mode: str
    started_at: str
    finished_at: str | None
    error_code: str | None
    outputs: dict[str, PortValue | list[PortValue]] = field(default_factory=dict)


@dataclass
class BuildRun:
    run_id: str
    pipeline_name: str
    pipeline_version: str
    status: str
    inputs: dict[str, PortValue]
    node_attempts: list[NodeAttempt]
    started_at: str
    finished_at: str | None
    resolved_backends: dict[str, str] = field(default_factory=dict)
    resolved_plan_contract_digest: str | None = None
    resolved_backend_versions: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if any(not key or not value for key, value in self.resolved_backends.items()):
            raise ValueError("resolved backend names must use non-empty node IDs and names")
        if self.resolved_plan_contract_digest is None:
            if self.resolved_backend_versions:
                raise ValueError("resolved backend versions require a plan contract digest")
            return
        if not self.resolved_plan_contract_digest:
            raise ValueError("resolved plan contract digest must not be empty")
        if set(self.resolved_backend_versions) != set(self.resolved_backends):
            raise ValueError("resolved backend names and versions must use the same node IDs")
        if any(not value for value in self.resolved_backend_versions.values()):
            raise ValueError("resolved backend versions must not be empty")
