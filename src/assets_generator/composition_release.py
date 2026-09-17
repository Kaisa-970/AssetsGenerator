"""Standard asset assembly and GLTF export for reviewed, unfused components."""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
import trimesh

from .alignment import _glb, candidate_frame
from .artifact_store import LocalArtifactStore
from .completion import _checked
from .contracts import ContractError
from .models import (
    AABB,
    AppearanceSet,
    ArtifactRef,
    AssetDefinition,
    AssetRelease,
    AssetSpatialInfo,
    ComponentProvenance,
    GeometrySet,
    QualityCheck,
    QualityReport,
    SemanticInfo,
    StructuredValue,
)
from .operators import _load_scene, _scene_vertices, export_release, material_from_glb
from .serialization import to_primitive
from .workflow import _persist_provenance


def standard_composition_release(
    store: LocalArtifactStore,
    *,
    scene: trimesh.Scene,
    components: list[dict[str, Any]],
    selection: ArtifactRef,
    selected: dict[str, Any],
    alignment: dict[str, Any],
    regions: ArtifactRef,
    mesh: ArtifactRef,
    provenance: ArtifactRef,
    run_id: str,
) -> tuple[ArtifactRef, ArtifactRef, ArtifactRef, dict[str, ArtifactRef]]:
    target_release = ArtifactRef(**alignment["target_release"])
    _checked(store, target_release, "asset_release")
    target = store.read_structured(target_release)
    target_asset = ArtifactRef(**target["asset_definition"])
    _checked(store, target_asset, "asset_definition")
    source_ids = store.read_structured(target_asset)["source_observation_ids"]
    if not source_ids:
        raise ContractError("composition requires source observation identity")
    metadata = store.get_manifest(
        ArtifactRef(**alignment["aligned"]).artifact_id
    ).identity.identity_metadata
    if (
        metadata.get("frame_id") != candidate_frame(target_release)
        or metadata.get("unit") != alignment["target_unit"]
    ):
        raise ContractError("aligned component frame/unit does not match target release")
    unit = alignment["target_unit"]
    if unit not in {"meter", "relative_unit"}:
        raise ContractError("unsupported composition unit")
    evidence = {
        "evidence/selection.json": selection,
        "evidence/regions.json": regions,
        "evidence/alignment.json": ArtifactRef(**selected["alignment"]),
        "evidence/transform.json": ArtifactRef(**alignment["transform"]),
        "provenance/composition.json": provenance,
    }
    for index, review_id in enumerate(selected["review_ids"]):
        evidence[f"evidence/review-{index}.json"] = ArtifactRef(review_id)
    for ref in evidence.values():
        if not store.verify_digest(ref):
            raise ContractError("composition evidence reference has invalid digest")
    # Exact inverse of gltf2-v1 axis conversion; no recentering or scale normalization.
    inverse = np.array([[0, 0, 1, 0], [1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1]], dtype=float)
    canonical_frame = f"{regions.artifact_id}/asset_canonical"
    canonical = scene.copy()
    for geometry in canonical.geometry.values():
        geometry.remove_unreferenced_vertices()
    canonical.apply_transform(inverse)  # type: ignore[no-untyped-call]
    refs = []
    component_records = []
    for component in components:
        name = component["component_id"]
        geometry = scene.geometry[name].copy()
        geometry.remove_unreferenced_vertices()
        part = trimesh.Scene(geometry)
        part.apply_transform(inverse)  # type: ignore[no-untyped-call]
        ref = store.persist_bytes(
            _glb(part),
            kind="triangle_mesh",
            schema_name="glTF",
            schema_version="2.0",
            identity_metadata={
                "frame_id": canonical_frame,
                "unit": unit,
                "up_axis": "+Z",
                "forward_axis": "+X",
                "region_selection": regions.artifact_id,
                "component_id": name,
            },
        )
        refs.append(ref)
        source: Literal["reconstructed", "generated"] = component["source"]
        record = _persist_provenance(
            store,
            run_id=run_id,
            node_id="compose_regions",
            port_name="components",
            element_id=name,
            artifact=ref,
            derived_from=[ArtifactRef(**component["input"]), regions, selection],
            operator="composition_canonical_conversion",
            backend="core",
            backend_version="1",
            parameters={
                "output_element_id": name,
                "matrix": inverse.tolist(),
                "source_frame": metadata["frame_id"],
                "target_frame": canonical_frame,
                "unit": unit,
                "policy": "gltf2-v1-inverse-axes-only",
                "trimesh_version": trimesh.__version__,
            },
            seed=None,
            source=source,
        )
        record_id = store.read_structured(record)["provenance_id"]
        component_records.append(ComponentProvenance(name, ref, source, [record_id]))
        evidence[f"geometry/components/{name}.glb"] = ref
        evidence[f"provenance/{name}.json"] = record
    combined = store.persist_bytes(
        _glb(canonical),
        kind="triangle_mesh",
        schema_name="glTF",
        schema_version="2.0",
        identity_metadata={
            "frame_id": canonical_frame,
            "unit": unit,
            "up_axis": "+Z",
            "forward_axis": "+X",
        },
    )
    # Validate serialized geometry, including decoded textures and finite texture coordinates.
    loaded = _load_scene(store.blob_path(combined).read_bytes())
    points = _scene_vertices(loaded)
    if not np.isfinite(points).all():
        raise ContractError("composition geometry contains nonfinite vertices")
    for geometry in loaded.geometry.values():
        if not len(geometry.faces):
            raise ContractError("composition contains empty geometry")
        if geometry.visual.kind == "texture":
            uv = geometry.visual.uv
            if uv is not None and not np.isfinite(uv).all():
                raise ContractError("composition contains invalid UV")
            material = geometry.visual.material
            for field in [
                "baseColorTexture",
                "normalTexture",
                "metallicRoughnessTexture",
                "emissiveTexture",
            ]:
                texture = getattr(material, field, None)
                if texture is not None:
                    texture.load()
                    if uv is None or len(uv) != len(geometry.vertices):
                        raise ContractError("textured composition requires per-vertex UV")
    checks = [
        QualityCheck(name, True, True, "composition-basic-v1", "pass", evidence_artifacts=[regions])
        for name in [
            "geometry_nonempty_finite",
            "appearance_loadable",
            "references_valid",
            "frame_unit_consistent",
        ]
    ]
    checks.append(
        QualityCheck(
            "simulation_readiness",
            True,
            "unverified",
            "composition-basic-v1",
            "warn",
            "unfused components; seams, overlaps, watertightness and physical quality unverified",
        )
    )
    qa = store.persist_structured(
        StructuredValue(
            "quality_report",
            "QualityReport",
            "1.0",
            to_primitive(QualityReport("composition-basic-v1", checks, "warn")),
        )
    )
    spatial = AssetSpatialInfo(
        canonical_frame,
        AABB(points.min(axis=0).tolist(), points.max(axis=0).tolist()),
        None,
        "relative" if unit == "relative_unit" else "unknown",
        unit,
        "unknown",
    )
    material = material_from_glb(store, combined)
    asset = AssetDefinition(
        f"asset_{regions.artifact_id.split(':')[1]}",
        "1.0",
        "Reviewed component composition",
        GeometrySet(refs),
        AppearanceSet([material]),
        spatial,
        SemanticInfo(None, "unknown"),
        None,
        source_ids,
        [qa.artifact_id],
        component_records,
    )
    asset_ref = store.persist_structured(
        StructuredValue("asset_definition", "AssetDefinition", "1.0", to_primitive(asset))
    )
    exported, release = export_release(
        store, asset_ref, combined, material, qa, appearance_mode="preserve_mesh"
    )
    export_provenance = _persist_provenance(
        store,
        run_id=run_id,
        node_id="compose_regions",
        port_name="exported",
        artifact=exported,
        derived_from=[mesh, regions, selection, *refs],
        operator="export",
        backend="core",
        backend_version="gltf2-v1",
        parameters={"appearance_mode": "preserve_mesh", "canonical_inverse": inverse.tolist()},
        seed=None,
        source="mixed",
    )
    evidence["provenance/export.json"] = export_provenance
    # ExportOperator converts canonical axes back to the target GLB axes exactly once.
    release = AssetRelease(asset_ref, {**release.files, **evidence}, release.export_profile)
    release_ref = store.persist_structured(
        StructuredValue("asset_release", "AssetRelease", "1.0", to_primitive(release))
    )
    return asset_ref, release_ref, qa, release.files
