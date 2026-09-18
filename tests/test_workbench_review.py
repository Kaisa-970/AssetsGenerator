from dataclasses import replace

import pytest
from test_workbench_engine import fixture_engine

from assets_generator.errors import ErrorCode, PipelineError
from assets_generator.workbench_engine import WorkbenchEngine
from assets_generator.workbench_persistence import WorkbenchRepository
from assets_generator.workbench_service import LocalWorkbenchService


@pytest.mark.parametrize(
    "code", [ErrorCode.BACKEND_TIMEOUT, ErrorCode.BACKEND_FAILED, ErrorCode.BACKEND_UNAVAILABLE]
)
def test_parent_preserves_worker_failure_code_and_detail(tmp_path, code):
    store, image, profile = fixture_engine(tmp_path)

    class BrokenProposer:
        def propose(self, store, image):
            raise PipelineError(code, "runner failed for a specific reason")

    profile = replace(profile, proposer=BrokenProposer())
    with WorkbenchRepository(store, tmp_path / "workbench") as repo:
        engine = WorkbenchEngine(repo, {"fake": profile})
        run = engine.create(image, "fake", {}, "create")
        engine.drain()
        attempt = repo.load(run.run_id).workbench.stage_states["propose"].current()
        assert attempt.error_code == code.value
        assert attempt.error_detail == "runner failed for a specific reason"
        child = repo.load(attempt.child_run_id)
        assert child.node_attempts[0].error_code == code.value
        view = LocalWorkbenchService(engine).get_run(run.run_id)["stages"][0]
        assert view["error_code"] == code.value
        assert view["error"] == attempt.error_detail


def test_registered_child_pipeline_identity_matches_executing_workflow(tmp_path, monkeypatch):
    store, image, profile = fixture_engine(tmp_path)
    with WorkbenchRepository(store, tmp_path / "workbench") as repo:
        engine = WorkbenchEngine(repo, {"fake": profile})
        identities = []
        original = engine._execute

        def execute(run, plan, stage_id):
            child = repo.load(run.workbench.stage_states[stage_id].current().child_run_id)
            identities.append((stage_id, child.pipeline_name, child.pipeline_version))
            return original(run, plan, stage_id)

        monkeypatch.setattr(engine, "_execute", execute)
        run = engine.create(image, "fake", {}, "create")
        engine.drain()
        run = repo.load(run.run_id)
        engine.preview(run.run_id, run.workbench.state_revision, "p0", False, False)
        run = repo.load(run.run_id)
        engine.decision(run.run_id, run.workbench.state_revision, "confirm", "tester")
        engine.drain()
        assert repo.load(run.run_id).status == "succeeded"
        assert identities == [
            ("propose", "instance_proposals", "1"),
            ("select", "instance_selection", "1"),
            ("generate", profile.shape_plan.pipeline_name, profile.shape_plan.pipeline_version),
        ]
