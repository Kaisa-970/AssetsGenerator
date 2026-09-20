"""Export a self-contained visual AssetDefinition without replacing mesh appearance."""

from pathlib import Path

from .contracts import ContractError
from .dag_adapters import AdapterSpec, NodeExecutionContext, NodeExecutionResult
from .models import ArtifactRef, StructuredValue
from .operators import export_release, material_from_glb
from .serialization import sha256_bytes, to_primitive


class AssetExportAdapter:
    @property
    def spec(self) -> AdapterSpec:
        fixed = {
            "appearance_mode": "preserve_mesh",
            "export_profile": "gltf2-v1",
            "operator_source_digest": sha256_bytes(
                Path(__file__).with_name("operators.py").read_bytes()
            ),
        }
        return AdapterSpec(
            "asset_export",
            "1",
            ("asset_export@1",),
            parameter_schema={
                "type": "object",
                "properties": {
                    key: {"type": "string", "enum": [value]} for key, value in fixed.items()
                },
            },
            defaults=fixed,
        )

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        asset_ref = context.inputs.get("asset")
        if not isinstance(asset_ref, ArtifactRef) or not context.store.verify_digest(asset_ref):
            raise ContractError("asset export requires a verified AssetDefinition")
        store = context.store
        asset_identity = store.get_manifest(asset_ref.artifact_id).identity
        if (asset_identity.kind, asset_identity.schema_name, asset_identity.schema_version) != (
            "asset_definition",
            "AssetDefinition",
            "1.0",
        ):
            raise ContractError("asset export requires AssetDefinition@1.0")
        asset = store.read_structured(asset_ref)
        materials = asset.get("appearance", {}).get("materials", [])
        if any(
            material.get(key) is not None
            for material in materials
            for key in (
                "base_color_texture",
                "normal_texture",
                "metallic_roughness_texture",
                "emissive_texture",
            )
        ):
            raise ContractError("preserve_mesh export does not apply standalone texture references")
        geometry = asset.get("geometry", {})
        meshes = geometry.get("visual_meshes", [])
        if len(meshes) != 1 or any(
            geometry.get(key) for key in ("gaussians", "point_clouds", "collision_meshes")
        ):
            raise ContractError(
                "asset export v1 requires exactly one visual mesh and no auxiliary geometry"
            )
        if asset.get("physics") is not None:
            raise ContractError("visual asset export does not support physics")
        mesh = ArtifactRef(**meshes[0])
        if not store.verify_digest(mesh):
            raise ContractError("asset visual mesh is missing or corrupt")
        identity = store.get_manifest(mesh.artifact_id).identity
        metadata = identity.identity_metadata
        spatial = asset.get("spatial", {})
        if (
            identity.kind != "triangle_mesh"
            or metadata.get("frame_id") != spatial.get("canonical_frame_id")
            or metadata.get("unit") != spatial.get("unit")
            or not metadata.get("unit")
            or not metadata.get("frame_id")
            or metadata.get("up_axis") != "+Z"
            or metadata.get("forward_axis") != "+X"
        ):
            raise ContractError("asset spatial contract does not match canonical mesh")
        qualities = asset.get("quality_report_ids", [])
        if len(qualities) != 1:
            raise ContractError("asset export v1 requires one geometry quality report")
        quality = ArtifactRef(qualities[0])
        if (
            not store.verify_digest(quality)
            or store.get_manifest(quality.artifact_id).identity.kind != "quality_report"
        ):
            raise ContractError("asset quality report is missing or corrupt")
        report = store.read_structured(quality)
        if report.get("profile") != "geometry-v1" or report.get("overall_status") not in {
            "pass",
            "warn",
        }:
            raise ContractError("asset geometry quality report does not permit export")
        checks = {item["check_id"]: item for item in report.get("checks", [])}
        if any(
            checks.get(key, {}).get("status") != "pass"
            for key in (
                "glb_loadable",
                "mesh_non_empty_finite",
                "blob_digest",
                "spatial_contract",
                "mandatory_provenance",
            )
        ):
            raise ContractError("asset geometry quality report lacks mandatory passing checks")
        provenance = checks["mandatory_provenance"].get("evidence_artifacts", [])
        matched = False
        for raw in provenance:
            reference = ArtifactRef(**raw)
            if not store.verify_digest(reference):
                raise ContractError("asset quality provenance is missing or corrupt")
            record = store.read_structured(reference)
            if (
                record.get("operator") == "canonicalize"
                and record.get("output_artifact_id") == mesh.artifact_id
            ):
                matched = True
        if not matched:
            raise ContractError("asset quality report refers to another mesh")
        glb, release = export_release(
            store,
            asset_ref,
            mesh,
            material_from_glb(store, mesh),
            quality,
            appearance_mode="preserve_mesh",
        )
        release_ref = store.persist_structured(
            StructuredValue("asset_release", "AssetRelease", "1.0", to_primitive(release))
        )
        return NodeExecutionResult({"glb": glb, "release": release_ref})
