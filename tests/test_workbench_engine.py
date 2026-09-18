from __future__ import annotations

from pathlib import Path

from PIL import Image
from test_instance_proposals import FakeProposer
from test_scene_extraction import setup

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.workbench_engine import BackendProfile, WorkbenchEngine
from assets_generator.workbench_persistence import WorkbenchRepository
from assets_generator.workflow import _import_image


def test_fixed_template_preview_confirm_and_publish(tmp_path: Path) -> None:
    (tmp_path / "fixture").mkdir()
    args = setup(tmp_path / "fixture")
    store = LocalArtifactStore(tmp_path / "store")
    image_path = tmp_path / "image.png"
    Image.new("RGB", (8, 6), "red").save(image_path)
    image = _import_image(store, image_path, "rgb_image")
    profile = BackendProfile(
        "fake",
        FakeProposer(),
        args["resolved_plan"],
        {"model": "fake-sam"},
        {"model": "fake-shape"},
    )
    with WorkbenchRepository(store, tmp_path / "workbench") as repo:
        engine = WorkbenchEngine(repo, {"fake": profile})
        run = engine.create(image, "fake", {}, "create")
        engine.drain()
        run = repo.load(run.run_id)
        assert run.status == "waiting_for_input", run
        assert run.workbench is not None
        engine.preview(run.run_id, run.workbench.state_revision, "p0", True, True)
        run = repo.load(run.run_id)
        assert run.workbench is not None
        engine.decision(run.run_id, run.workbench.state_revision, "confirm", "tester")
        engine.drain()
        run = repo.load(run.run_id)
        assert run.status == "succeeded", run
        assert run.workbench is not None
        generated = run.workbench.stage_states["generate"].current()
        child = repo.load(generated.child_run_id)
        assert "selection_binding" in child.inputs
        assert "import_binding" in child.inputs
        assert generated.outputs["release"]


def fixture_engine(tmp_path):
    (tmp_path / "fixture").mkdir()
    args = setup(tmp_path / "fixture")
    store = LocalArtifactStore(tmp_path / "store")
    image = _import_image(store, tmp_path / "fixture/scene.png", "rgb_image")
    profile = BackendProfile(
        "fake", FakeProposer(), args["resolved_plan"], {"model": "sam"}, {"model": "shape"}
    )
    return store, image, profile


def test_confirmed_decision_interruption_can_explicitly_retry(tmp_path):
    store, image, profile = fixture_engine(tmp_path)
    with WorkbenchRepository(store, tmp_path / "workbench") as repo:
        engine = WorkbenchEngine(repo, {"fake": profile})
        run = engine.create(image, "fake", {}, "create")
        engine.drain()
        run = repo.load(run.run_id)
        engine.preview(run.run_id, run.workbench.state_revision, "p0", False, False)
        run = repo.load(run.run_id)
        engine.decision(run.run_id, run.workbench.state_revision, "decision", "reviewer")
        restarted = WorkbenchEngine(repo, {"fake": profile})
        run = restarted.recover(run.run_id)
        assert run.status == "interrupted"
        revision = run.workbench.state_revision
        restarted.retry(run.run_id, revision, "retry")
        # Same command returned after response loss, even with old revision.
        assert restarted.retry(run.run_id, revision, "retry").status == "running"
        restarted.drain()
        assert repo.load(run.run_id).status == "succeeded"


def test_waiting_recovery_preserves_request_and_draft(tmp_path):
    store, image, profile = fixture_engine(tmp_path)
    with WorkbenchRepository(store, tmp_path / "workbench") as repo:
        engine = WorkbenchEngine(repo, {"fake": profile})
        run = engine.create(image, "fake", {}, "create")
        engine.drain()
        run = repo.load(run.run_id)
        engine.preview(run.run_id, run.workbench.state_revision, "p0", True, True)
        before = repo.load(run.run_id)
        restarted = WorkbenchEngine(repo, {"fake": profile})
        after = restarted.recover(run.run_id)
        assert after.status == "waiting_for_input"
        assert (
            before.workbench.stage_states["select"].draft
            == after.workbench.stage_states["select"].draft
        )
        assert (
            before.workbench.stage_states["select"].request_ref
            == after.workbench.stage_states["select"].request_ref
        )
        assert before.workbench.stage_states["propose"] == after.workbench.stage_states["propose"]


def test_child_success_parent_event_loss_recovers_without_reexecution(tmp_path, monkeypatch):
    store, image, profile = fixture_engine(tmp_path)
    with WorkbenchRepository(store, tmp_path / "workbench") as repo:
        engine = WorkbenchEngine(repo, {"fake": profile})
        run = engine.create(image, "fake", {}, "create")
        original_complete = engine._complete

        def crash(*args, **kwargs):
            raise SystemExit("simulated service death")

        monkeypatch.setattr(engine, "_complete", crash)
        import pytest

        with pytest.raises(SystemExit):
            engine.drain()
        before = repo.load(run.run_id)
        child_id = before.workbench.stage_states["propose"].current().child_run_id
        assert repo.load(child_id).status == "succeeded"
        monkeypatch.setattr(engine, "_complete", original_complete)
        restarted = WorkbenchEngine(repo, {"fake": profile})
        restarted.recover(run.run_id)
        restarted.drain()
        after = repo.load(run.run_id)
        assert after.status == "waiting_for_input"
        assert after.workbench.stage_states["propose"].current().child_run_id == child_id
        assert after.workbench.stage_states["propose"].current().execution_mode == "restored"


def test_corrupt_publication_prevents_successful_recovery(tmp_path, monkeypatch):
    store, image, profile = fixture_engine(tmp_path)
    with WorkbenchRepository(store, tmp_path / "workbench") as repo:
        engine = WorkbenchEngine(repo, {"fake": profile})
        run = engine.create(image, "fake", {}, "create")

        def crash(*args, **kwargs):
            raise SystemExit("simulated service death")

        monkeypatch.setattr(engine, "_complete", crash)
        import pytest

        with pytest.raises(SystemExit):
            engine.drain()
        run = repo.load(run.run_id)
        output = Path(
            run.workbench.stage_states["propose"].current().command_receipt.output_location
        )
        (output / "proposals.json").unlink()
        restarted = WorkbenchEngine(repo, {"fake": profile})
        recovered = restarted.recover(run.run_id)
        assert recovered.status == "interrupted"


def test_generated_input_replacement_rejected_before_shape_backend(tmp_path):
    import pytest

    from assets_generator.contracts import ContractError
    from assets_generator.workbench_binding import verify_imported_binding

    store, image, profile = fixture_engine(tmp_path)
    with WorkbenchRepository(store, tmp_path / "workbench") as repo:
        engine = WorkbenchEngine(repo, {"fake": profile})
        run = engine.create(image, "fake", {}, "create")
        engine.drain()
        run = repo.load(run.run_id)
        engine.preview(run.run_id, run.workbench.state_revision, "p0", False, False)
        run = repo.load(run.run_id)
        engine.decision(run.run_id, run.workbench.state_revision, "confirm", "reviewer")
        engine.drain()
        finished = repo.load(run.run_id)
        binding = finished.workbench.stage_states["select"].current().outputs["binding"]
        wrong = tmp_path / "wrong.png"
        Image.new("L", (8, 6), 255).save(wrong)
        wrong_ref = _import_image(store, wrong, "binary_mask")
        with pytest.raises(ContractError, match="actual model input"):
            verify_imported_binding(store, binding, image, wrong_ref)


def test_completed_release_file_deletion_is_not_hidden_by_success(tmp_path):
    import pytest

    store, image, profile = fixture_engine(tmp_path)
    with WorkbenchRepository(store, tmp_path / "workbench") as repo:
        engine = WorkbenchEngine(repo, {"fake": profile})
        run = engine.create(image, "fake", {}, "create")
        engine.drain()
        run = repo.load(run.run_id)
        engine.preview(run.run_id, run.workbench.state_revision, "p0", False, False)
        run = repo.load(run.run_id)
        engine.decision(run.run_id, run.workbench.state_revision, "confirm", "reviewer")
        engine.drain()
        run = repo.load(run.run_id)
        output = Path(
            run.workbench.stage_states["generate"].current().command_receipt.output_location
        )
        (output / "geometry/visual.glb").unlink()
        with pytest.raises(FileNotFoundError):
            WorkbenchEngine(repo, {"fake": profile}).recover(run.run_id)
