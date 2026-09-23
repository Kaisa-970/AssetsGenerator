from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import trimesh
from PIL import Image
from test_remote_shape_output import fixture

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.dag_remote_shape import RemoteShapeAdapter
from assets_generator.mesh_appearance import describe_mesh_appearance
from assets_generator.remote_shape_output import export_shape_output


@pytest.mark.parametrize("appearance", ["none", "vertex", "texture"])
def test_actual_glb_appearance(appearance):
    mesh = trimesh.creation.box()
    if appearance == "vertex":
        mesh.visual.vertex_colors = [255, 0, 0, 255]
    elif appearance == "texture":
        mesh.visual = trimesh.visual.TextureVisuals(
            uv=np.zeros((len(mesh.vertices), 2)), image=Image.new("RGB", (2, 2), "red")
        )
    result = describe_mesh_appearance(mesh.export(file_type="glb"), {})
    assert result == {
        "primitive_count": 1,
        "textured_primitives": int(appearance == "texture"),
        "vertex_colored_primitives": int(appearance == "vertex"),
        "postprocess_mode": None,
    }


def test_remote_adapter_persists_fallback_without_changing_wire_mesh(tmp_path):
    source = LocalArtifactStore(tmp_path / "source")
    target = LocalArtifactStore(tmp_path / "target")
    output = fixture(source)
    output.backend_metadata["postprocess_mode"] = "geometry_fallback_no_texture"
    blobs = {key: item.data for key, item in export_shape_output(source, output).items()}
    context = SimpleNamespace(store=target)
    with patch("assets_generator.dag_remote_shape.RemoteOutput.from_job") as descriptor:
        descriptor.side_effect = [
            SimpleNamespace(media_type="model/gltf-binary"),
            SimpleNamespace(media_type="application/json"),
        ]
        result = RemoteShapeAdapter.import_result(None, context, None, blobs)
    mesh = result.outputs["mesh"]
    assert mesh != output.mesh
    assert target.blob_path(mesh).read_bytes() == blobs["mesh"]
    identity = target.get_manifest(mesh.artifact_id).identity
    assert identity.identity_metadata["postprocess_mode"] == "geometry_fallback_no_texture"
    assert (
        "postprocess_mode"
        not in source.get_manifest(output.mesh.artifact_id).identity.identity_metadata
    )
    assert (
        describe_mesh_appearance(blobs["mesh"], identity.identity_metadata)["postprocess_mode"]
        == "geometry_fallback_no_texture"
    )


def test_editor_appearance_reads_verified_output_and_rejects_corruption(tmp_path):
    from assets_generator.dag_persistence import DagRepository
    from assets_generator.node_editor_execution import NodeEditorExecution

    store = LocalArtifactStore(tmp_path / "store")
    output = fixture(store)
    state = SimpleNamespace(status="succeeded")
    repo = DagRepository(store, tmp_path / "repository")
    service = SimpleNamespace(
        _owned=lambda run: None,
        _resolve_output=lambda node, port: output.mesh,
        engine=SimpleNamespace(
            store=store,
            repository=SimpleNamespace(
                load=lambda run: SimpleNamespace(dag=SimpleNamespace(node_states={"shape": state})),
                verify_reference_closure=repo.verify_reference_closure,
            ),
        ),
    )
    assert (
        NodeEditorExecution.appearance(service, "run", "shape", "mesh")["postprocess_mode"] is None
    )
    store.blob_path(output.mesh).write_bytes(b"broken")
    with pytest.raises(ValueError, match="missing/corrupt"):
        NodeEditorExecution.appearance(service, "run", "shape", "mesh")
