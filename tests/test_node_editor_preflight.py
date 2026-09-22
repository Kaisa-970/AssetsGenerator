import json
import threading
from copy import deepcopy
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from test_dag_preflight import setup as setup

# Imported pytest fixture is intentionally injected by parameter name.
# ruff: noqa: F811
from assets_generator.node_editor import DraftEditor, create_editor_server
from assets_generator.node_editor_execution import NodeEditorExecution, PreflightChanged
from assets_generator.serialization import read_json


def test_prepare_preflight_is_readonly_and_start_rechecks(setup, tmp_path, monkeypatch):
    engine, raw, plan, image = setup
    ref = image("red")
    service = NodeEditorExecution(engine)
    calls = []
    monkeypatch.setattr(engine, "drain", lambda run_id: calls.append(run_id))
    try:
        prepared = service.prepare_inputs(raw, image_ref={"artifact_id": ref.artifact_id})
        inputs = prepared["input_refs"]
        before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
        report = service.preflight(raw, input_refs=inputs)
        assert report["execution_ready"]
        assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
        changed = deepcopy(raw)
        changed["nodes"]["a"]["parameters"]["width"] = 3
        with pytest.raises(PreflightChanged) as caught:
            service.start(
                changed,
                input_refs=inputs,
                preflight_digest=report["digest"],
                idempotency_key="changed",
            )
        assert caught.value.report["digest"] != report["digest"]
        assert service.list_runs() == []
        assert engine.repository.creation_receipt("node-editor:changed") is None
        first = service.start(
            raw, input_refs=inputs, preflight_digest=report["digest"], idempotency_key="same"
        )
        service._worker.join()
        replay = service.start(
            raw, input_refs=inputs, preflight_digest="expired", idempotency_key="same"
        )
        assert replay["run"]["run_id"] == first["run"]["run_id"]
        assert calls == [first["run"]["run_id"]]
    finally:
        service.close()


def test_source_damage_and_changed_reference_require_reconfirmation(setup):
    engine, raw, plan, image = setup
    ref = image("red")
    source = engine.drain(engine.create(plan(raw), {"image": ref}).run_id)
    snapshot = read_json(engine.store.root / "runs" / f"{source.run_id}.json")
    service = NodeEditorExecution(engine)
    inputs = {"image": {"artifact_id": ref.artifact_id}}
    try:
        report = service.preflight(raw, input_refs=inputs, reuse_source=snapshot)
        assert report["nodes"]["a"]["status"] == "reuse"
        other = {"image": {"artifact_id": image("blue").artifact_id}}
        with pytest.raises(PreflightChanged):
            service.start(
                raw, input_refs=other, reuse_source=snapshot, preflight_digest=report["digest"]
            )
        output = source.dag.node_states["a"].current().outputs["image"]
        engine.store.blob_path(output).unlink()
        with pytest.raises(PreflightChanged) as caught:
            service.start(
                raw, input_refs=inputs, reuse_source=snapshot, preflight_digest=report["digest"]
            )
        assert not caught.value.report["execution_ready"]
        assert len(service.list_runs()) == 1
    finally:
        service.close()


def test_preflight_rejects_candidate_import_and_reports_bad_inputs(setup):
    engine, raw, _, image = setup
    ref = image("red")
    service = NodeEditorExecution(engine)
    try:
        with pytest.raises(ValueError, match="prepared exact"):
            service.preflight(
                raw, input_refs={"image": {"artifact_id": ref.artifact_id, "source": {}}}
            )
        engine.store.blob_path(ref).unlink()
        report = service.preflight(raw, input_refs={"image": {"artifact_id": ref.artifact_id}})
        assert not report["execution_ready"]
        assert service.list_runs() == []
    finally:
        service.close()


def test_http_preflight_conflict_response(setup, tmp_path):
    engine, raw, _, image = setup
    service = NodeEditorExecution(engine)
    server = create_editor_server(DraftEditor(tmp_path / "editor", execution=service), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"

    def post(route, body):
        request = Request(
            url + route,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Origin": url},
        )
        with urlopen(request) as response:
            return json.load(response)

    try:
        ref = image("red")
        prepared = post(
            "/api/prepare-inputs", {"pipeline": raw, "image_ref": {"artifact_id": ref.artifact_id}}
        )
        report = post("/api/preflight", {"pipeline": raw, **prepared})
        assert report["execution_ready"]
        with pytest.raises(HTTPError) as caught:
            post(
                "/api/runs",
                {
                    "pipeline": raw,
                    **prepared,
                    "preflight_digest": "stale",
                    "idempotency_key": "http",
                },
            )
        assert caught.value.code == 409
        body = json.load(caught.value)
        assert body["code"] == "preflight_changed"
        assert body["preflight"]["digest"] == report["digest"]
        assert service.list_runs() == []
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
        service.close()


def test_prepare_path_imports_without_run(tmp_path):
    from test_dag_image_adapters import image_plan
    from test_node_editor_execution import raw
    from test_workbench_engine import fixture_engine

    from assets_generator.dag_engine import DagEngine
    from assets_generator.dag_persistence import DagRepository

    store, _, profile = fixture_engine(tmp_path)
    registry, _ = image_plan(profile)
    with DagRepository(store, tmp_path / "dag") as repo:
        service = NodeEditorExecution(DagEngine(repo, registry))
        try:
            prepared = service.prepare_inputs(raw(), str(tmp_path / "fixture/scene.png"))
            assert set(prepared["input_refs"]["image"]) == {"artifact_id"}
            assert service.preflight(raw(), **prepared)["execution_ready"]
            assert service.list_runs() == []
        finally:
            service.close()


def test_changed_invalid_plan_returns_new_blocked_report(setup):
    engine, raw, _, image = setup
    service = NodeEditorExecution(engine)
    try:
        inputs = {"image": {"artifact_id": image("red").artifact_id}}
        report = service.preflight(raw, input_refs=inputs)
        changed = deepcopy(raw)
        changed["nodes"]["a"]["adapter"] = "missing@1"
        with pytest.raises(PreflightChanged) as caught:
            service.start(changed, input_refs=inputs, preflight_digest=report["digest"])
        assert caught.value.report["error"]["reason"] == "plan_invalid"
        assert service.list_runs() == []
    finally:
        service.close()


@pytest.mark.parametrize("change", ["missing", "changed"])
def test_replay_uses_persisted_plan_after_live_registry_change(setup, monkeypatch, change):
    from assets_generator import dag_adapters

    engine, raw, _, image = setup
    service = NodeEditorExecution(engine)
    calls = []
    monkeypatch.setattr(engine, "drain", lambda run_id: calls.append(run_id))
    try:
        inputs = {"image": {"artifact_id": image("red").artifact_id}}
        report = service.preflight(raw, input_refs=inputs)
        options = {
            "input_refs": inputs,
            "preflight_digest": report["digest"],
            "idempotency_key": "lost-response",
        }
        first = service.start(raw, **options)
        service._worker.join()
        if change == "missing":
            engine.registry._adapters.clear()
        else:
            monkeypatch.setattr(
                dag_adapters, "adapter_implementation_digest", lambda adapter: "changed"
            )
        second = service.start(raw, **options)
        assert second["run"]["run_id"] == first["run"]["run_id"]
        assert calls == [first["run"]["run_id"]]
        changed = deepcopy(raw)
        changed["nodes"]["a"]["parameters"]["width"] = 3
        with pytest.raises(ValueError, match="conflict") as caught:
            service.start(changed, **options)
        assert not isinstance(caught.value, PreflightChanged)
    finally:
        service.close()
