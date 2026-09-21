"""Compatibility gates for moving persisted identities out of workflows."""

import ast
import hashlib
import json
from pathlib import Path

import pytest

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.models import StructuredValue
from assets_generator.provenance import persist_provenance
from assets_generator.serialization import canonical_json_bytes


@pytest.mark.parametrize("element", [None, "generated_0", "generated_1"])
def test_provenance_preserves_exact_legacy_bytes_and_artifact_identity(tmp_path, element):
    store = LocalArtifactStore(tmp_path)
    mesh = store.persist_structured(StructuredValue("quality_evidence", "Test", "1.0", {}))
    legacy_key = (
        b"run:node:1:components"
        if element is None
        else json.dumps(
            ["output-element-v1", "run", "node", 1, "components", element],
            separators=(",", ":"),
        ).encode()
    )
    output_id = "sha256:" + hashlib.sha256(legacy_key).hexdigest()
    expected = {
        "provenance_id": "sha256:" + hashlib.sha256(f"provenance:{output_id}".encode()).hexdigest(),
        "output_id": output_id,
        "output_artifact_id": mesh.artifact_id,
        "derived_from_artifact_ids": [mesh.artifact_id],
        "operator": "assemble@1",
        "operator_version": "1",
        "backend": "core",
        "backend_version": "1",
        "model_digest": None,
        "container_digest": None,
        "parameters": {"mode": "preserve"},
        "seed": None,
        "run_id": "run",
        "node_id": "node",
        "attempt": 1,
        "source": "generated",
        "confidence": None,
        "score": None,
        "score_method": None,
    }
    baseline = store.persist_bytes(
        canonical_json_bytes(expected),
        kind="provenance_record",
        schema_name="ProvenanceRecord",
        schema_version="1.0",
        identity_metadata={"media_type": "application/json"},
    )
    actual = persist_provenance(
        store,
        run_id="run",
        node_id="node",
        port_name="components",
        artifact=mesh,
        derived_from=[mesh],
        operator="assemble@1",
        backend="core",
        backend_version="1",
        parameters={"mode": "preserve"},
        seed=None,
        source="generated",
        element_id=element,
    )
    assert actual == baseline
    assert store.blob_path(actual).read_bytes() == canonical_json_bytes(expected)


def test_shared_modules_do_not_depend_on_workflows_or_operators():
    root = Path(__file__).parents[1] / "src/assets_generator"
    forbidden = {"workflow", "operators", "scene_workflow", "multi_view_workflow"}
    for name in ("mesh_io", "provenance", "release_io"):
        for node in ast.walk(ast.parse((root / f"{name}.py").read_text())):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in forbidden
