from pathlib import Path

import pytest
from test_dag_process import setup as dag_setup
from test_scene_extraction import setup as scene_setup
from test_workbench_worker import ProcessProposer

from assets_generator.contracts import ContractError
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.process_admission import admit_compute
from assets_generator.workbench_engine import BackendProfile, WorkbenchEngine
from assets_generator.workbench_models import ProcessObservation
from assets_generator.workflow import _import_image


class Probe:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def observe(self, identity):
        self.calls += 1
        return ProcessObservation("now", self.result, identity)


def workbench_orphan(tmp_path, repo):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    args = scene_setup(fixture)
    image = _import_image(repo.store, fixture / "scene.png", "rgb_image")
    profile = BackendProfile(
        "cpu",
        ProcessProposer(tmp_path / "marker"),
        args["resolved_plan"],
        {"model": "cpu"},
        {"model": "fake"},
    )
    engine = WorkbenchEngine(repo, {"cpu": profile})
    run = engine.create(image, "cpu", {}, "create")
    engine.drain()
    run = repo.load(run.run_id)
    worker = run.workbench.stage_states["propose"].current().worker_execution
    # Recreate a crash snapshot with authorization but no observed exit.
    worker.launch_phase = "release_authorized"
    worker.last_probe = None
    worker.exit_code = None
    repo.commit(run)
    return run


@pytest.mark.parametrize("result", ["alive", "unknown"])
def test_workbench_orphan_blocks_dag_without_dispatch(tmp_path, result):
    store, registry, plan, source = dag_setup(tmp_path)
    with DagRepository(store, tmp_path / "repo") as repo:
        old = workbench_orphan(tmp_path, repo)
        engine = DagEngine(repo, registry)
        engine.probe = Probe(result)
        run = engine.drain(engine.create(plan, {"source": source}).run_id)
        assert run.status == "recovery_blocked"
        assert run.dag.node_states["A"].attempts == []
        assert old.run_id in run.dag.node_states["A"].dispatch_block_reason
        assert engine.recover(run.run_id).status == "recovery_blocked"


def test_terminal_ledger_write_failure_does_not_admit(tmp_path, monkeypatch):
    store, _, _, _ = dag_setup(tmp_path)
    with DagRepository(store, tmp_path / "repo") as repo:
        workbench_orphan(tmp_path, repo)
        original = repo.io.write

        def write(path, *args, **kwargs):
            if Path(path).parent.name == "process_exits":
                raise OSError("injected terminal ledger write failure")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(repo.io, "write", write)
        with pytest.raises(ContractError, match="terminal ledger write failure"):
            admit_compute(repo, Probe("exited"))
        assert not list((repo.directory / "process_exits").glob("*.json"))
        with pytest.raises(RuntimeError, match="healthy commit"):
            repo._ready()


def test_terminal_ledger_survives_restart_and_prevents_pid_reuse_probe(tmp_path):
    store, _, _, _ = dag_setup(tmp_path)
    with DagRepository(store, tmp_path / "repo") as repo:
        old = workbench_orphan(tmp_path, repo)
        probe = Probe("exited")
        admit_compute(repo, probe)
        assert probe.calls == 1
        assert len(list((repo.directory / "process_exits").glob("*.json"))) == 1
        # The immutable parent can still hold pre-exit history; terminal evidence
        # is kept separately and must override future observations of that PID.
        assert (
            repo.load(old.run_id)
            .workbench.stage_states["propose"]
            .current()
            .worker_execution.launch_phase
            == "release_authorized"
        )
    with DagRepository(store, tmp_path / "repo") as repo:
        reused = Probe("unknown")
        admit_compute(repo, reused)
        assert reused.calls == 0
