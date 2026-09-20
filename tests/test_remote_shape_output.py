import json

import pytest
import trimesh

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.models import BackendNativeFrame, PBRMaterial, StructuredValue
from assets_generator.operators import ShapeOutput
from assets_generator.remote_shape_output import export_shape_output, import_shape_output
from assets_generator.serialization import canonical_json_bytes, to_primitive


def fixture(store):
    mesh = store.persist_bytes(
        trimesh.creation.box().export(file_type="glb"),
        kind="triangle_mesh",
        schema_name="glTF",
        schema_version="2.0",
        identity_metadata={
            "frame_id": "native",
            "unit": "relative_unit",
            "up_axis": "+Y",
            "media_type": "model/gltf-binary",
        },
    )
    return ShapeOutput(
        mesh,
        StructuredValue("pbr_material", "PBRMaterial", "1.0", to_primitive(PBRMaterial([1.0] * 4))),
        StructuredValue(
            "backend_native_frame",
            "BackendNativeFrame",
            "1.0",
            to_primitive(
                BackendNativeFrame("native", "right", "+Y", None, "unknown", "relative_unit")
            ),
        ),
        {"backend": "test"},
    )


def test_shape_transport_roundtrip(tmp_path):
    source = LocalArtifactStore(tmp_path / "source")
    target = LocalArtifactStore(tmp_path / "target")
    output = fixture(source)
    wire = export_shape_output(source, output)
    imported = import_shape_output(target, {key: value.data for key, value in wire.items()})
    assert imported == output
    assert target.verify_digest(imported.mesh)


@pytest.mark.parametrize("damage", ["frame", "digest", "color", "texture"])
def test_shape_transport_rejects_invalid_evidence(tmp_path, damage):
    source = LocalArtifactStore(tmp_path / "source")
    blobs = {k: v.data for k, v in export_shape_output(source, fixture(source)).items()}
    raw = json.loads(blobs["shape_metadata"])
    if damage == "frame":
        raw["native_frame"]["value"]["frame_id"] = "other"
    elif damage == "digest":
        blobs["mesh"] = b"invalid"
    elif damage == "color":
        raw["material"]["value"]["base_color_factor"] = [2.0] * 4
    else:
        raw["material"]["value"]["base_color_texture"] = {"artifact_id": "sha256:" + "a" * 64}
    blobs["shape_metadata"] = canonical_json_bytes(raw)
    with pytest.raises(ValueError):
        import_shape_output(LocalArtifactStore(tmp_path / "target"), blobs)
