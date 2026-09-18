from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image
from test_instance_proposals import FakeProposer
from test_scene_extraction import setup

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.workbench_engine import BackendProfile, WorkbenchEngine
from assets_generator.workbench_persistence import WorkbenchRepository
from assets_generator.worker import LocalProcessWorker, ProcessJobRequest
from assets_generator.workflow import _import_image


class ProcessProposer(FakeProposer):
    def __init__(self, marker: Path):
        self.worker = LocalProcessWorker()
        self.marker = marker

    def propose(self, store, image):
        self.worker.run(
            ProcessJobRequest(
                [
                    sys.executable,
                    "-c",
                    "import os,pathlib; assert os.environ['HF_HUB_OFFLINE']=='1'; "
                    "pathlib.Path(__import__('sys').argv[1]).write_text('ran')",
                    str(self.marker),
                ],
                self.marker.parent,
                10,
                "job",
            )
        )
        return super().propose(store, image)


def test_backend_worker_runs_only_after_authorization_snapshot(tmp_path):
    (tmp_path / "fixture").mkdir()
    args = setup(tmp_path / "fixture")
    store = LocalArtifactStore(tmp_path / "store")
    image_path = tmp_path / "image.png"
    Image.new("RGB", (8, 6), "red").save(image_path)
    image = _import_image(store, image_path, "rgb_image")
    marker = tmp_path / "backend-ran"
    proposer = ProcessProposer(marker)
    profile = BackendProfile(
        "cpu", proposer, args["resolved_plan"], {"model": "cpu"}, {"model": "fake"}
    )
    with WorkbenchRepository(store, tmp_path / "workbench") as repo:
        engine = WorkbenchEngine(repo, {"cpu": profile})
        run = engine.create(image, "cpu", {}, "create")
        engine.drain()
        run = repo.load(run.run_id)
        assert run.status == "waiting_for_input", run
        execution = run.workbench.stage_states["propose"].current().worker_execution
        assert execution.launch_phase == "exit_observed"
        assert execution.exit_code == 0
        assert execution.last_probe.result == "exited"
        assert marker.read_text() == "ran"
        assert type(proposer.worker) is LocalProcessWorker


def test_unidentified_launcher_failure_recovers_and_allows_retry(tmp_path, monkeypatch):
    import assets_generator.workbench_worker as module

    (tmp_path / "fixture").mkdir()
    args = setup(tmp_path / "fixture")
    store = LocalArtifactStore(tmp_path / "store")
    image = _import_image(store, tmp_path / "fixture/scene.png", "rgb_image")
    marker = tmp_path / "backend-ran"
    profile = BackendProfile(
        "cpu", ProcessProposer(marker), args["resolved_plan"], {"model": "cpu"}, {"model": "fake"}
    )

    class BrokenChannel:
        def __init__(self, *args, **kwargs):
            raise OSError("handshake setup failed")

    with WorkbenchRepository(store, tmp_path / "workbench") as repo:
        engine = WorkbenchEngine(repo, {"cpu": profile})
        run = engine.create(image, "cpu", {}, "create")
        monkeypatch.setattr(module, "GatedLaunchChannel", BrokenChannel)
        engine.drain()
        assert not marker.exists()
        run = engine.recover(run.run_id)
        assert run.status == "interrupted"
        retried = engine.retry(run.run_id, run.workbench.state_revision, "retry")
        assert retried.workbench.stage_states["propose"].active_attempt == 2


def test_authorization_failure_does_not_run_backend(tmp_path, monkeypatch):
    from assets_generator.workbench_state import AuthorizeLaunch

    (tmp_path / "fixture").mkdir()
    args = setup(tmp_path / "fixture")
    store = LocalArtifactStore(tmp_path / "store")
    image = _import_image(store, tmp_path / "fixture/scene.png", "rgb_image")
    marker = tmp_path / "backend-ran"
    profile = BackendProfile(
        "cpu", ProcessProposer(marker), args["resolved_plan"], {"model": "cpu"}, {"model": "fake"}
    )
    with WorkbenchRepository(store, tmp_path / "workbench") as repo:
        engine = WorkbenchEngine(repo, {"cpu": profile})
        run = engine.create(image, "cpu", {}, "create")
        original = engine._event

        def event(run_id, stage_id, payload, **kwargs):
            if isinstance(payload, AuthorizeLaunch):
                raise OSError("authorization not committed")
            return original(run_id, stage_id, payload, **kwargs)

        monkeypatch.setattr(engine, "_event", event)
        engine.drain()
        assert not marker.exists()
        assert repo.load(run.run_id).status == "failed"
