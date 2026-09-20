import threading
import time
from pathlib import Path

import pytest
import yaml
from test_dag_image_adapters import image_plan
from test_workbench_engine import fixture_engine

from assets_generator.contracts import ContractError
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.node_editor_execution import NodeEditorExecution


def wait(service, run_id):
    deadline = time.monotonic() + 180
    while service.snapshot(run_id)["busy"]:
        assert time.monotonic() < deadline
        time.sleep(0.2)
    return service.snapshot(run_id)


def raw():
    return yaml.safe_load(Path("examples/dag-image-asset.yaml").read_text())


def test_async_start_frozen_plan_readonly_snapshots_and_review(tmp_path, monkeypatch):
    store, _, profile = fixture_engine(tmp_path)
    registry, _ = image_plan(profile)
    with DagRepository(store, tmp_path / "dag") as repo:
        engine = DagEngine(repo, registry)
        service = NodeEditorExecution(engine)
        entered, release = threading.Event(), threading.Event()
        decision_entered, decision_release = threading.Event(), threading.Event()
        original = engine.drain

        def blocked(run_id):
            entered.set()
            assert release.wait(10)
            return original(run_id)

        monkeypatch.setattr(engine, "drain", blocked)
        try:
            graph = raw()
            started = service.start(graph, str(tmp_path / "fixture/scene.png"))
            run_id = started["run"]["run_id"]
            assert entered.wait(2)
            assert started["busy"]
            graph["nodes"]["generate_asset"]["parameters"]["seed"] = 777
            assert (
                engine._plan(repo.load(run_id)).bindings["generate_asset"].parameters["seed"] == 42
            )
            persisted = service.plan(run_id)
            assert persisted["plan_id"] == started["run"]["dag"]["plan_id"]
            assert persisted["bindings"]["generate_asset"]["parameters"]["seed"] == 42
            before = store.blob_path(repo.load(run_id).dag.plan).read_bytes()
            assert service.snapshot(run_id)["run"]["dag"]["revision"] == 0
            assert store.blob_path(repo.load(run_id).dag.plan).read_bytes() == before
            with pytest.raises(ContractError, match="still executing"):
                service.resume(run_id, 0)
            release.set()
            result = wait(service, run_id)
            assert result["run"]["status"] == "waiting_for_input"
            assert service.list_runs() == [{"run_id": run_id, "status": "waiting_for_input"}]
            review = service.review(run_id, "choose_object")
            assert review["url"].startswith("http://127.0.0.1:")
            assert service.review(run_id, "choose_object") == review
            with pytest.raises(ContractError, match="revision"):
                service.resume(run_id, -1)
            assert service.review(run_id, "choose_object") == review
            invalid = raw()
            invalid["nodes"]["candidates"]["adapter"] = "missing@1"
            with pytest.raises(ContractError):
                service.start(invalid, "/missing")
            assert service.review(run_id, "choose_object") == review
            with pytest.raises(ContractError, match="unknown review"):
                service.review(run_id, "missing")
            assert service.review(run_id, "choose_object") == review
            with pytest.raises(ContractError, match="human request"):
                service.review(run_id, "candidates")
            assert service.review(run_id, "choose_object") == review
            reviewer = service._review[0]
            preview = reviewer.preview(
                {
                    "expected_revision": repo.load(run_id).dag.revision,
                    "proposal_id": "p0",
                    "invert": False,
                    "keep_largest": True,
                }
            )
            draft = preview["review"]["draft"]
            original_decide = engine.decide

            def blocked_decision(*args, **kwargs):
                decision_entered.set()
                assert decision_release.wait(60)
                return original_decide(*args, **kwargs)

            monkeypatch.setattr(engine, "decide", blocked_decision)
            reviewer.decision(
                {
                    "expected_revision": preview["revision"],
                    "idempotency_key": "review",
                    "reviewer": "test",
                    "request": draft["request"],
                    "preview_signature": draft["preview_signature"],
                    "final_mask": draft["final_mask"],
                }
            )
            assert decision_entered.wait(2)
            with pytest.raises(ContractError, match="decision is still executing"):
                service.resume(run_id, preview["revision"])
            decision_release.set()
            completed = wait(service, run_id)
            assert completed["run"]["status"] == "succeeded", completed
            assert len(completed["outputs"]) == 4
            assert service.output(run_id, "generate_asset", "glb").data[:4] == b"glTF"
        finally:
            release.set()
            decision_release.set()
            service.close()


def test_compile_before_import_and_stale_commands(tmp_path, monkeypatch):
    store, _, profile = fixture_engine(tmp_path)
    registry, _ = image_plan(profile)
    with DagRepository(store, tmp_path / "dag") as repo:
        service = NodeEditorExecution(DagEngine(repo, registry))
        try:
            invalid = raw()
            invalid["nodes"]["candidates"]["adapter"] = "missing@1"
            with pytest.raises(ContractError):
                service.start(invalid, "/missing-image-should-not-be-read")
            assert service.list_runs() == []
            started = service.start(raw(), str(tmp_path / "fixture/scene.png"))
            run_id = started["run"]["run_id"]
            run = wait(service, run_id)["run"]
            with pytest.raises(ContractError, match="revision"):
                service.resume(run_id, -1)
            with pytest.raises(ContractError, match="revision"):
                service.retry(run_id, "candidates", -1)
            assert service.snapshot(run_id)["run"] == run
            monkeypatch.setattr(
                service.engine, "drain", lambda _: (_ for _ in ()).throw(ValueError("injected"))
            )
            service.resume(run_id, run["dag"]["revision"])
            result = wait(service, run_id)
            assert result["error"] == "ValueError: injected"
            assert result["run"] == run
        finally:
            service.close()
        with pytest.raises(RuntimeError, match="closing"):
            service.resume(run_id, run["dag"]["revision"])


def test_foreign_directory_cannot_read_mutate_or_export(tmp_path):
    store, _, profile = fixture_engine(tmp_path)
    registry, _ = image_plan(profile)
    with DagRepository(store, tmp_path / "first") as repo:
        first = NodeEditorExecution(DagEngine(repo, registry))
        try:
            run_id = first.start(raw(), str(tmp_path / "fixture/scene.png"))["run"]["run_id"]
            wait(first, run_id)
            with DagRepository(store, tmp_path / "second") as other:
                service = NodeEditorExecution(DagEngine(other, registry))
                try:
                    assert service.list_runs() == []
                    with pytest.raises(ContractError, match="another editor"):
                        service.plan(run_id)
                    with pytest.raises(ContractError, match="another editor"):
                        service.snapshot(run_id)
                    with pytest.raises(ContractError, match="another editor"):
                        service.output(run_id, "generate_asset", "glb")
                    with pytest.raises(ContractError, match="another editor"):
                        service.resume(run_id, 0)
                finally:
                    service.close()
        finally:
            first.close()


def test_renamed_input_compiles_but_cannot_start(tmp_path):
    from assets_generator.node_editor import DraftEditor

    store, _, profile = fixture_engine(tmp_path)
    registry, _ = image_plan(profile)
    with DagRepository(store, tmp_path / "dag") as repo:
        service = NodeEditorExecution(DagEngine(repo, registry))
        try:
            editor = DraftEditor(tmp_path / "drafts", execution=service)
            graph = raw()
            assert editor.compile(graph)["execution_ready"]
            graph["inputs"]["photo"] = graph["inputs"].pop("image")
            graph["nodes"]["candidates"]["inputs"]["image"] = "pipeline.inputs.photo"
            result = editor.compile(graph)
            assert result["ok"] and result["bound_plan"]
            assert not result["execution_ready"]
            assert "named image" in result["execution_reason"]
            with pytest.raises(ContractError) as caught:
                service.start(graph, "/must-not-read.png")
            assert str(caught.value) == result["execution_reason"]
            assert service.list_runs() == []
        finally:
            service.close()
