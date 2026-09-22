import json
import threading
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

import pytest
from test_node_editor_continuation import fixture as fixture

from assets_generator.models import ArtifactRef
from assets_generator.node_editor import DraftEditor, create_editor_server
from assets_generator.serialization import canonical_json_bytes, to_primitive

# Imported fixture is intentionally injected by pytest.
# ruff: noqa: F811


def test_snapshot_outputs_stay_fixed_when_current_index_changes(fixture, tmp_path):
    service, run, snapshot = fixture
    expected = run.dag.node_states["segment"].current().outputs["mask"]
    index = service.engine.store.root / "runs" / f"{run.run_id}.json"
    # Mutable index now contains unrelated/corrupt bytes. Exact reads never consult it.
    index.write_text("{}")
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = service.snapshot_reference(run.run_id, "segment", "mask", snapshot)
    assert result["reference"] == to_primitive(expected)
    assert result["source_snapshot"] == snapshot
    assert (
        service.snapshot_output(run.run_id, "segment", "mask", snapshot).data
        == service.engine.store.blob_path(expected).read_bytes()
    )
    candidate = service.snapshot_reference(run.run_id, "segment", "candidates~0", snapshot)
    assert candidate["reference"] == to_primitive(expected)
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_snapshot_source_candidate_binding_uses_fixed_original_image(fixture):
    service, run, snapshot = fixture
    state = run.dag.node_states["segment"].current()
    mask = state.outputs["mask"]
    original_image = state.resolved_inputs["image"]
    (service.engine.store.root / "runs" / f"{run.run_id}.json").unlink()
    value = {
        "artifact_id": mask.artifact_id,
        "source": {
            "run_id": run.run_id,
            "node_id": "segment",
            "port": "candidates~0",
            "snapshot": snapshot,
        },
    }
    bound = service._bind_candidate_input(value, {"image": original_image})
    identity = service.engine.store.get_manifest(bound.artifact_id).identity
    evidence = service.engine.store.read_structured(
        ArtifactRef(**identity.identity_metadata["selection_binding"])
    )
    assert evidence["source"]["snapshot"] == snapshot
    assert evidence["image"] == to_primitive(original_image)
    with pytest.raises(ValueError, match="original image"):
        service._bind_candidate_input(value, {"image": run.dag.named_actual_inputs["photo"]})


def test_snapshot_http_identity_ownership_and_query_checks(fixture, tmp_path):
    service, run, snapshot = fixture
    other = service.engine.create(service.engine._plan(run), run.dag.named_actual_inputs)
    server = create_editor_server(DraftEditor(tmp_path / "editor", execution=service), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    query = "?" + urlencode({"snapshot": snapshot["artifact_id"]})
    try:
        path = f"/api/runs/{run.run_id}/snapshot-reference/segment/mask"
        with urlopen(base + path + query) as response:
            reference = json.load(response)
        assert reference["source_snapshot"] == snapshot
        with urlopen(
            base + path.replace("snapshot-reference", "snapshot-output") + query
        ) as response:
            assert response.read().startswith(b"\x89PNG")
        for suffix in ("", "?snapshot=", "?snapshot=a&snapshot=b", "?snapshot=a&bad="):
            with pytest.raises(HTTPError) as caught:
                urlopen(base + path + suffix)
            assert caught.value.code == 400
        with pytest.raises(HTTPError) as caught:
            urlopen(base + path.replace(run.run_id, other.run_id) + query)
        assert caught.value.code == 400
        owner = service.engine.store.root / "parent_run_owners" / f"{run.run_id}.json"
        raw = json.loads(owner.read_bytes())
        raw["workbench_directory"] = "/unowned"
        owner.write_bytes(canonical_json_bytes(raw))
        with pytest.raises(HTTPError) as caught:
            urlopen(base + path + query)
        assert caught.value.code == 400
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_selected_snapshot_image_is_consumed_by_new_run_after_index_changes(fixture):
    from PIL import Image

    service, source, snapshot = fixture
    store = service.engine.store
    selected = service.snapshot_reference(source.run_id, "extract_foreground", "image", snapshot)[
        "reference"
    ]
    # The old run's mutable index is no longer readable; selection stays pinned.
    (store.root / "runs" / f"{source.run_id}.json").write_text("{}")
    raw = {
        "pipeline": "selected_image",
        "version": "1",
        "inputs": {
            "image": {
                "kind": "rgb_image",
                "carriers": ["artifact_ref"],
                "schema_name": "png",
                "schema_version": "1.0",
            }
        },
        "nodes": {
            "resize": {
                "operator": "resize_image@1",
                "adapter": "resize_image@1",
                "inputs": {"image": "pipeline.inputs.image"},
                "parameters": {"width": 2, "height": 2},
            }
        },
    }
    prepared = service.prepare_inputs(raw, input_refs={"image": selected})
    report = service.preflight(raw, **prepared)
    assert report["execution_ready"]
    created = service.start(
        raw, **prepared, preflight_digest=report["digest"], idempotency_key="selected-A"
    )
    service._worker.join(timeout=10)
    assert not service._worker.is_alive()
    completed = service.engine.repository.load(created["run"]["run_id"])
    assert completed.status == "succeeded"
    attempt = completed.dag.node_states["resize"].current()
    assert to_primitive(attempt.resolved_inputs["image"]) == selected
    with Image.open(store.blob_path(attempt.outputs["image"])) as image:
        assert image.size == (2, 2)
        assert list(image.getdata()) == [(255, 0, 0)] * 4
    assert (store.root / "runs" / f"{source.run_id}.json").read_text() == "{}"
