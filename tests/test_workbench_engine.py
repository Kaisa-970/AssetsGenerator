from __future__ import annotations

from pathlib import Path

import pytest
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


def test_release_manifests_and_published_run_must_match_store(tmp_path):
    import json

    import pytest

    from assets_generator.contracts import ContractError

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
        for name in ("asset.json", "release.json"):
            path = output / name
            original = path.read_bytes()
            path.unlink()
            with pytest.raises(FileNotFoundError):
                engine.recover(run.run_id)
            path.write_bytes(b"{}")
            with pytest.raises(ContractError, match="published file"):
                engine.recover(run.run_id)
            path.write_bytes(original)
        path = output / "run.json"
        original = path.read_bytes()
        published = json.loads(original)
        published["parent_run_id"] = "run_other_parent"
        path.write_text(json.dumps(published))
        with pytest.raises(ContractError, match="published run"):
            engine.recover(run.run_id)
        path.write_bytes(original)
        assert engine.recover(run.run_id).status == "succeeded"


@pytest.mark.parametrize("probe_result", ["alive", "unknown"])
def test_other_run_orphan_blocks_compute_until_explicit_retry(tmp_path, probe_result):
    from assets_generator.workbench_models import ProcessObservation, WorkerExecution

    store, image, profile = fixture_engine(tmp_path)

    class Probe:
        result = probe_result

        def observe(self, identity):
            return ProcessObservation(
                "now", self.result, identity if self.result == "alive" else None
            )

    probe = Probe()
    with WorkbenchRepository(store, tmp_path / "workbench") as repo:
        engine = WorkbenchEngine(repo, {"fake": profile}, probe=probe)
        orphan = engine.create(image, "fake", {}, "orphan")
        engine.drain()
        orphan = repo.load(orphan.run_id)
        attempt = orphan.workbench.stage_states["propose"].current()
        attempt.worker_execution = WorkerExecution(
            "job",
            attempt.child_run_id,
            "launch",
            "release_authorized",
            "host",
            "boot",
            123,
            456,
            123,
        )
        repo.commit(orphan)
        new = engine.create(image, "fake", {}, "new")
        engine.drain()
        blocked = repo.load(new.run_id)
        assert blocked.status == "failed"
        assert "admission blocked" in blocked.workbench.stage_states["propose"].current().error_code
        assert (
            repo.load(
                blocked.workbench.stage_states["propose"].current().child_run_id
            ).node_attempts
            == []
        )
        # Clear old group; this does not implicitly restart the rejected calculation.
        probe.result = "exited"
        engine.drain()
        assert repo.load(new.run_id).status == "failed"
        engine.retry(new.run_id, blocked.workbench.state_revision, "retry")
        engine.drain()
        assert repo.load(new.run_id).status == "waiting_for_input"


def test_corrupt_waiting_draft_isolated_without_poisoning_writer(tmp_path):
    from assets_generator.workbench_service import LocalWorkbenchService

    store, image, profile = fixture_engine(tmp_path)
    with WorkbenchRepository(store, tmp_path / "workbench") as repo:
        engine = WorkbenchEngine(repo, {"fake": profile})
        broken = engine.create(image, "fake", {}, "broken")
        engine.drain()
        broken = repo.load(broken.run_id)
        engine.preview(broken.run_id, broken.workbench.state_revision, "p0", True, True)
        broken = repo.load(broken.run_id)
        draft = broken.workbench.stage_states["select"].draft
        # Its complement is another proposal mask in this fixture; use a dedicated
        # same-kind corrupt ref for the draft to isolate waiting evidence validation.
        isolated = store.persist_bytes(
            b"invalid pixels",
            kind="binary_mask",
            schema_name="raster_image",
            schema_version="1.0",
            identity_metadata={"media_type": "image/png"},
        )
        from dataclasses import replace

        broken.workbench.stage_states["select"].draft = replace(draft, final_mask=isolated)
        repo.commit(broken)
        store.blob_path(isolated).unlink()
        service = LocalWorkbenchService(WorkbenchEngine(repo, {"fake": profile}))
        service.recover_runs()
        assert "recovery_error" in service.get_run(broken.run_id)
        good = service.engine.create(image, "fake", {}, "good")
        service.engine.drain()
        assert repo.load(good.run_id).status == "waiting_for_input"
        service.close()


def test_corrupt_owned_run_process_evidence_blocks_admission(tmp_path):
    store, image, profile = fixture_engine(tmp_path)
    with WorkbenchRepository(store, tmp_path / "workbench") as repo:
        engine = WorkbenchEngine(repo, {"fake": profile})
        old = engine.create(image, "fake", {}, "old")
        engine.drain()
        (store.root / "runs" / f"{old.run_id}.json").write_text("{}")
        new = engine.create(image, "fake", {}, "new")
        engine.drain()
        failed = repo.load(new.run_id)
        assert failed.status == "failed"
        assert (
            "cannot verify process evidence"
            in failed.workbench.stage_states["propose"].current().error_code
        )


def test_durable_worker_exit_is_not_invalidated_by_later_pid_reuse(tmp_path):
    from assets_generator.workbench_models import ProcessObservation, WorkerExecution

    store, image, profile = fixture_engine(tmp_path)

    class Probe:
        def observe(self, identity):
            raise AssertionError("terminal exited process must not be reprobed")

    with WorkbenchRepository(store, tmp_path / "workbench") as repo:
        engine = WorkbenchEngine(repo, {"fake": profile}, probe=Probe())
        old = engine.create(image, "fake", {}, "old")
        engine.drain()
        old = repo.load(old.run_id)
        attempt = old.workbench.stage_states["propose"].current()
        attempt.worker_execution = WorkerExecution(
            "job",
            attempt.child_run_id,
            "launch",
            "exit_observed",
            "host",
            "boot",
            123,
            456,
            123,
            exit_code=0,
            last_probe=ProcessObservation("then", "exited"),
        )
        repo.commit(old)
        new = engine.create(image, "fake", {}, "new")
        engine.drain()
        assert repo.load(new.run_id).status == "waiting_for_input"
