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
            draft = service.draft_from_run(run_id)
            assert draft["source_run_id"] == run_id
            assert draft["source_plan_id"] == persisted["plan_id"]
            assert draft["pipeline"]["inputs"]["image"]["kind"] == "rgb_image"
            assert draft["pipeline"]["nodes"]["generate_asset"]["parameters"]["seed"] == 42
            from assets_generator.pipeline import (
                _pipeline_from_raw,
                compile_pipeline,
                load_default_operator_specs,
            )

            cloned_plan = registry.bind_plan(
                compile_pipeline(
                    _pipeline_from_raw(draft["pipeline"]),
                    load_default_operator_specs(),
                    require_explicit_joins=True,
                )
            )
            assert cloned_plan.bindings == engine._plan(repo.load(run_id)).bindings
            draft["pipeline"]["nodes"]["generate_asset"]["parameters"]["seed"] = 123
            assert (
                service.draft_from_run(run_id)["pipeline"]["nodes"]["generate_asset"]["parameters"][
                    "seed"
                ]
                == 42
            )
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
                        service.draft_from_run(run_id)
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


def test_uploaded_image_is_exact_run_input_without_upload_dispatch(tmp_path, monkeypatch):
    from assets_generator.models import ArtifactRef

    store, _, profile = fixture_engine(tmp_path)
    registry, _ = image_plan(profile)
    with DagRepository(store, tmp_path / "dag") as repo:
        engine = DagEngine(repo, registry)
        service = NodeEditorExecution(engine)
        dispatched = []
        monkeypatch.setattr(engine, "drain", lambda run_id: dispatched.append(run_id))
        try:
            data = (tmp_path / "fixture/scene.png").read_bytes()
            uploaded = service.upload_image(data)
            assert service.upload_image(data) == uploaded
            assert service.list_runs() == []
            assert dispatched == []
            ref = ArtifactRef(**uploaded["image_ref"])
            assert store.blob_path(ref).read_bytes() == data
            with pytest.raises(ContractError, match="exactly one"):
                service.start(raw(), "/tmp/ignored", image_ref=uploaded["image_ref"])
            result = service.start(raw(), image_ref=uploaded["image_ref"])
            run_id = result["run"]["run_id"]
            wait(service, run_id)
            assert repo.load(run_id).dag.named_actual_inputs["image"] == ref
            assert dispatched == [run_id]
            store.blob_path(ref).unlink()
            with pytest.raises((ValueError, OSError)):
                service.start(raw(), image_ref=uploaded["image_ref"])
            assert len(service.list_runs()) == 1
        finally:
            service.close()


def test_upload_rejects_invalid_images_without_artifacts(tmp_path):
    from PIL import UnidentifiedImageError

    store, _, profile = fixture_engine(tmp_path)
    registry, _ = image_plan(profile)
    with DagRepository(store, tmp_path / "dag") as repo:
        service = NodeEditorExecution(DagEngine(repo, registry))
        before = store.find_artifacts("rgb_image")
        try:
            for data in (b"", b"x" * (20 * 1024 * 1024 + 1)):
                with pytest.raises(ContractError, match="20 MiB"):
                    service.upload_image(data)
            with pytest.raises(UnidentifiedImageError):
                service.upload_image(b"not an image")
            data = (tmp_path / "fixture/scene.png").read_bytes()
            import struct
            import zlib

            oversized = bytearray(data)
            oversized[16:24] = struct.pack(">II", 20000, 20000)
            oversized[29:33] = struct.pack(">I", zlib.crc32(oversized[12:29]))
            with pytest.raises(ContractError, match="25 megapixels"):
                service.upload_image(bytes(oversized))
            with pytest.raises(OSError):
                service.upload_image(data[: len(data) // 2])
            assert store.find_artifacts("rgb_image") == before
            assert service.list_runs() == []
        finally:
            service.close()


def test_creation_retry_reuses_run_across_restart_and_detects_conflict(tmp_path, monkeypatch):
    store, _, profile = fixture_engine(tmp_path)
    registry, _ = image_plan(profile)
    directory = tmp_path / "dag"
    dispatched = []
    with DagRepository(store, directory) as repo:
        engine = DagEngine(repo, registry)
        service = NodeEditorExecution(engine)
        monkeypatch.setattr(engine, "drain", lambda run_id: dispatched.append(run_id))
        uploaded = service.upload_image((tmp_path / "fixture/scene.png").read_bytes())
        options = {"image_ref": uploaded["image_ref"], "idempotency_key": "request-one"}
        try:
            result = service.start(raw(), **options)
            run_id = result["run"]["run_id"]
            wait(service, run_id)
            assert service.start(raw(), **options)["run"]["run_id"] == run_id
            changed = raw()
            changed["nodes"]["generate_asset"]["parameters"]["seed"] = 778
            with pytest.raises(ValueError, match="conflict"):
                service.start(changed, **options)
            assert dispatched == [run_id]
        finally:
            service.close()
    with DagRepository(store, directory) as repo:
        engine = DagEngine(repo, registry)
        service = NodeEditorExecution(engine)
        monkeypatch.setattr(engine, "drain", lambda run_id: dispatched.append(run_id))
        try:
            assert service.start(raw(), **options)["run"]["run_id"] == run_id
            assert dispatched == [run_id]
            (store.root / "runs" / f"{run_id}.json").unlink()
            with pytest.raises(ContractError, match="refusing to recreate"):
                service.start(raw(), **options)
            assert dispatched == [run_id]
        finally:
            service.close()


def test_creation_reservation_survives_before_first_snapshot(tmp_path, monkeypatch):
    store, _, profile = fixture_engine(tmp_path)
    registry, _ = image_plan(profile)
    directory = tmp_path / "dag"
    with DagRepository(store, directory) as repo:
        engine = DagEngine(repo, registry)
        service = NodeEditorExecution(engine)
        uploaded = service.upload_image((tmp_path / "fixture/scene.png").read_bytes())
        options = {"image_ref": uploaded["image_ref"], "idempotency_key": "before-create"}
        captured = []

        def crash(plan, inputs, run_id=None):
            captured.append(run_id)
            raise RuntimeError("simulated crash before snapshot")

        monkeypatch.setattr(engine, "create", crash)
        with pytest.raises(RuntimeError, match="simulated crash"):
            service.start(raw(), **options)
        service.close()
    with DagRepository(store, directory) as repo:
        engine = DagEngine(repo, registry)
        service = NodeEditorExecution(engine)
        monkeypatch.setattr(engine, "drain", lambda run_id: None)
        try:
            result = service.start(raw(), **options)
            assert result["run"]["run_id"] == captured[0]
            wait(service, captured[0])
            assert len(service.list_runs()) == 1
        finally:
            service.close()


def test_creation_replay_while_worker_active_does_not_dispatch_again(tmp_path, monkeypatch):
    store, _, profile = fixture_engine(tmp_path)
    registry, _ = image_plan(profile)
    with DagRepository(store, tmp_path / "dag") as repo:
        engine = DagEngine(repo, registry)
        service = NodeEditorExecution(engine)
        entered, release = threading.Event(), threading.Event()
        calls = []

        def blocked(run_id):
            calls.append(run_id)
            entered.set()
            assert release.wait(10)

        monkeypatch.setattr(engine, "drain", blocked)
        try:
            uploaded = service.upload_image((tmp_path / "fixture/scene.png").read_bytes())
            options = {"image_ref": uploaded["image_ref"], "idempotency_key": "active"}
            first = service.start(raw(), **options)
            assert entered.wait(2)
            second = service.start(raw(), **options)
            assert second["run"]["run_id"] == first["run"]["run_id"]
            assert second["busy"]
            with pytest.raises(ContractError, match="still executing"):
                service.start(raw(), image_ref=uploaded["image_ref"], idempotency_key="new-active")
            assert repo.creation_receipt("node-editor:new-active") is None
            assert len(service.list_runs()) == 1
            assert calls == [first["run"]["run_id"]]
        finally:
            release.set()
            service.close()


def test_creation_replay_after_snapshot_before_dispatch_does_not_resume(tmp_path, monkeypatch):
    store, _, profile = fixture_engine(tmp_path)
    registry, _ = image_plan(profile)
    directory = tmp_path / "dag"
    with DagRepository(store, directory) as repo:
        engine = DagEngine(repo, registry)
        service = NodeEditorExecution(engine)
        uploaded = service.upload_image((tmp_path / "fixture/scene.png").read_bytes())
        options = {"image_ref": uploaded["image_ref"], "idempotency_key": "after-create"}
        original = engine.create

        def crash(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("simulated crash after snapshot")

        monkeypatch.setattr(engine, "create", crash)
        with pytest.raises(RuntimeError, match="simulated crash"):
            service.start(raw(), **options)
        run_id = service.list_runs()[0]["run_id"]
        service.close()
    with DagRepository(store, directory) as repo:
        engine = DagEngine(repo, registry)
        service = NodeEditorExecution(engine)
        calls = []
        monkeypatch.setattr(engine, "drain", lambda run_id: calls.append(run_id))
        try:
            assert service.start(raw(), **options)["run"]["run_id"] == run_id
            assert calls == []
            assert (directory / "created_runs" / f"{run_id}.json").exists()
            (store.root / "runs" / f"{run_id}.json").unlink()
            with pytest.raises(ContractError, match="refusing to recreate"):
                service.start(raw(), **options)
            assert calls == []

        finally:
            service.close()


def test_direct_resume_establishes_creation_marker(tmp_path, monkeypatch):
    store, _, profile = fixture_engine(tmp_path)
    registry, _ = image_plan(profile)
    directory = tmp_path / "dag"
    with DagRepository(store, directory) as repo:
        engine = DagEngine(repo, registry)
        service = NodeEditorExecution(engine)
        monkeypatch.setattr(engine, "drain", lambda run_id: None)
        try:
            result = service.start(
                raw(), str(tmp_path / "fixture/scene.png"), idempotency_key="resume"
            )
            run_id = result["run"]["run_id"]
            wait(service, run_id)
            marker = directory / "created_runs" / f"{run_id}.json"
            marker.unlink()  # initial snapshot survived but pre-dispatch marker did not
            service.resume(run_id, result["run"]["dag"]["revision"])
            wait(service, run_id)
            assert marker.exists()
            (store.root / "runs" / f"{run_id}.json").unlink()
            with pytest.raises(ContractError, match="refusing to recreate"):
                service.start(raw(), str(tmp_path / "fixture/scene.png"), idempotency_key="resume")
        finally:
            service.close()


def test_multi_view_editor_start_replay_and_resume(tmp_path, monkeypatch):
    from test_dag_multi_view import setup

    from assets_generator.node_editor import DraftEditor
    from assets_generator.pipeline import load_operator_specs

    store, observations, registry, relations, _, calls = setup(tmp_path, monkeypatch)
    specs_path = Path("examples/dag-multi-view-operators.yaml")
    graph = yaml.safe_load(Path("examples/dag-multi-view-asset.yaml").read_text())
    with DagRepository(store, tmp_path / "editor") as repo:
        service = NodeEditorExecution(
            DagEngine(repo, registry, relations),
            specs=load_operator_specs(specs_path),
            relations=relations,
        )
        editor = DraftEditor(tmp_path / "drafts", operators=specs_path, execution=service)
        try:
            assert editor.compile(graph)["execution_ready"]
            options = {
                "observations_ref": {"artifact_id": observations.artifact_id},
                "idempotency_key": "multiview",
            }
            started = service.start(graph, **options)
            run_id = started["run"]["run_id"]
            result = wait(service, run_id)
            assert result["run"]["status"] == "succeeded", result
            assert calls == {"geometry": 1, "reconstruction": 1}
            draft = service.draft_from_run(run_id)
            assert draft["pipeline"]["inputs"]["observations"]["kind"] == "observation_bundle"
            assert editor.compile(draft["pipeline"])["execution_ready"]
            assert service.snapshot(run_id)["run"]["dag"] == result["run"]["dag"]
            assert calls == {"geometry": 1, "reconstruction": 1}
            assert service.start(graph, **options)["run"]["run_id"] == run_id
            service.resume(run_id, result["run"]["dag"]["revision"])
            assert wait(service, run_id)["run"]["status"] == "succeeded"
            assert calls == {"geometry": 1, "reconstruction": 1}
            assert repo.load(run_id).dag.named_actual_inputs == {"observations": observations}
            with pytest.raises(ContractError, match="input source"):
                service.start(graph, image_ref={"artifact_id": observations.artifact_id})
            for output in result["outputs"]:
                assert service.output(run_id, output["node_id"], output["port"]).data
        finally:
            service.close()


def test_observation_import_preserves_order_and_does_not_execute(tmp_path):
    import io

    from PIL import Image

    from assets_generator.models import ArtifactRef
    from assets_generator.observations import observation_bundle_from_artifact

    store, _, profile = fixture_engine(tmp_path)
    registry, _ = image_plan(profile)
    with DagRepository(store, tmp_path / "dag") as repo:
        service = NodeEditorExecution(DagEngine(repo, registry))
        try:
            refs = []
            for color in ("red", "blue"):
                stream = io.BytesIO()
                Image.new("RGB", (8, 8), color).save(stream, format="PNG")
                refs.append(service.upload_image(stream.getvalue())["image_ref"])
            result = service.import_observations(refs)
            assert service.import_observations(refs) == result
            bundle = observation_bundle_from_artifact(
                ArtifactRef(**result["observations_ref"]), store
            )
            assert [view.image.artifact_id for view in bundle.views] == [
                ref["artifact_id"] for ref in refs
            ]
            assert [view.view_id for view in bundle.views] == ["view_000", "view_001"]
            assert all(
                view.camera is None and view.mask is None and view.depth is None
                for view in bundle.views
            )
            assert (
                service.import_observations(refs[::-1])["observations_ref"]
                != result["observations_ref"]
            )
            assert service.list_runs() == []
            for invalid in ([], refs[:1], refs * 17, [refs[0], refs[0]], [{}, refs[0]]):
                with pytest.raises(ContractError):
                    service.import_observations(invalid)
            stream = io.BytesIO()
            Image.new("RGBA", (8, 8)).save(stream, format="PNG")
            rgba = service.upload_image(stream.getvalue())["image_ref"]
            with pytest.raises(ContractError, match="RGB mode"):
                service.import_observations([refs[0], rgba])
            store.blob_path(ArtifactRef(**refs[0])).unlink()
            with pytest.raises(ContractError, match="digest"):
                service.import_observations(refs)
        finally:
            service.close()
