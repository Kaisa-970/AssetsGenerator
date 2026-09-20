import sys

import pytest

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import ContractError, OperatorSpec, PortSpec
from assets_generator.dag_adapters import AdapterRegistry, AdapterSpec, NodeExecutionResult
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.pipeline import PipelineDefinition, compile_pipeline
from assets_generator.process_admission import admit_compute
from assets_generator.workbench_models import ProcessObservation
from assets_generator.worker import ProcessJobRequest


class ProcessCopy:
    spec = AdapterSpec("process_copy", "1", ("copy@1",), execution_kind="process")

    def execute(self, context):
        for index in range(2):
            job = context.worker.run(
                ProcessJobRequest(
                    [sys.executable, "-c", "print('ok')"], context.store.root, 10, str(index)
                )
            )
            assert job.stdout.strip() == "ok"
        return NodeExecutionResult({"image": context.inputs["image"]})


def setup(tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    registry = AdapterRegistry()
    registry.register(ProcessCopy())
    port = PortSpec(("rgb_image",))
    plan = registry.bind_plan(
        compile_pipeline(
            PipelineDefinition(
                "process",
                "1",
                {"source": port},
                {
                    "A": {
                        "operator": "copy@1",
                        "adapter": "process_copy@1",
                        "inputs": {"image": "pipeline.inputs.source"},
                    }
                },
            ),
            {"copy@1": OperatorSpec("copy", "1", {"image": port}, {"image": port})},
            require_explicit_joins=True,
        )
    )
    source = store.persist_bytes(
        b"fixture", kind="rgb_image", schema_name="raster_image", schema_version="1.0"
    )
    return store, registry, plan, source


def test_process_adapter_keeps_each_command_evidence(tmp_path):
    store, registry, plan, source = setup(tmp_path)
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"source": source}).run_id)
        assert run.status == "succeeded"
        attempts = repo.load(run.run_id).dag.node_states["A"].attempts
        workers = attempts[0].worker_executions
        assert len(workers) == 2
        assert workers[0].job_id != workers[1].job_id
        assert all(
            w.launch_phase == "exit_observed" and w.last_probe.result == "exited" for w in workers
        )
        assert engine.recover(run.run_id).status == "succeeded"
        assert len(repo.load(run.run_id).dag.node_states["A"].attempts) == 1


class Probe:
    def __init__(self, result):
        self.result = result

    def observe(self, identity):
        return ProcessObservation("now", self.result, identity)


def test_other_dag_authorized_worker_blocks_without_new_attempt(tmp_path):
    store, registry, plan, source = setup(tmp_path)
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        old = engine.drain(engine.create(plan, {"source": source}).run_id)
        # Emulate a pre-exit snapshot after a service crash.
        worker = old.dag.node_states["A"].current().worker_executions[-1]
        worker.launch_phase = "release_authorized"
        worker.last_probe = None
        repo.commit(old)
        engine.probe = Probe("alive")
        run = engine.drain(engine.create(plan, {"source": source}).run_id)
        assert run.status == "recovery_blocked"
        assert run.dag.node_states["A"].attempts == []
        assert old.run_id in run.dag.node_states["A"].dispatch_block_reason
        with pytest.raises(ContractError, match="admission blocked"):
            admit_compute(repo, engine.probe)
        assert engine.recover(run.run_id).status == "recovery_blocked"
        assert repo.load(run.run_id).dag.node_states["A"].dispatch_block_reason
        engine.probe = Probe("exited")
        # Real worker lifecycle uses the real probe after admission was retested.
        admit_compute(repo, engine.probe)
        # A verified empty group stays terminal even after PID reuse.
        admit_compute(repo, Probe("unknown"))
        from assets_generator.workbench_process import LinuxProcessProbe

        engine.probe = LinuxProcessProbe()
        assert engine.drain(run.run_id).status == "succeeded"


@pytest.mark.parametrize("result", ["alive", "unknown"])
def test_dag_worker_blocks_workbench_admission(tmp_path, result):
    store, registry, plan, source = setup(tmp_path)
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        original = engine.drain(engine.create(plan, {"source": source}).run_id)
        worker = original.dag.node_states["A"].current().worker_executions[-1]
        worker.launch_phase = "release_authorized"
        worker.last_probe = None
        # WorkbenchEngine uses exactly the same admission function, including DAGs.
        from types import SimpleNamespace

        from assets_generator.workbench_engine import WorkbenchEngine

        with pytest.raises(ContractError, match="admission blocked"):
            repo.commit(original)
            WorkbenchEngine._admit_compute(
                SimpleNamespace(repository=repo, probe=Probe(result)), "another", "propose", 1
            )


class ChildCopy:
    spec = AdapterSpec("child_copy", "1", ("copy@1",), uses_child_run=True)

    def execute(self, context):
        from assets_generator.models import BuildRun
        from assets_generator.runtime import utc_now

        child = BuildRun(
            context.child_context.registration.child_run_id,
            "real_child_pipeline",
            "2",
            "running",
            {},
            [],
            utc_now(),
            None,
        )
        context.child_context.begin(child)
        child.status = "succeeded"
        context.child_context.persist(child)
        return NodeExecutionResult({"image": context.inputs["image"]})


@pytest.mark.parametrize("index_damage", [None, "missing", "corrupt"])
@pytest.mark.parametrize("pinned", [True, False])
@pytest.mark.parametrize("damaged_evidence", [False, True])
def test_child_workflow_registration_and_result_roundtrip(
    tmp_path, monkeypatch, index_damage, pinned, damaged_evidence
):
    store, registry, _, source = setup(tmp_path)
    registry.register(ChildCopy())
    port = PortSpec(("rgb_image",))
    plan = registry.bind_plan(
        compile_pipeline(
            PipelineDefinition(
                "child",
                "1",
                {"source": port},
                {
                    "A": {
                        "operator": "copy@1",
                        "adapter": "child_copy@1",
                        "inputs": {"image": "pipeline.inputs.source"},
                    }
                },
            ),
            {"copy@1": OperatorSpec("copy", "1", {"image": port}, {"image": port})},
            require_explicit_joins=True,
        )
    )
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.create(plan, {"source": source})
        if pinned:
            run = engine.drain(run.run_id)
            assert run.status == "succeeded"
        else:
            save = engine._save

            def crash(current):
                if current.dag.node_states["A"].status == "succeeded":
                    raise KeyboardInterrupt("parent result not saved")
                save(current)

            monkeypatch.setattr(engine, "_save", crash)
            with pytest.raises(KeyboardInterrupt):
                engine.drain(run.run_id)
            run = repo.load(run.run_id)
        attempt = run.dag.node_states["A"].current()
        assert attempt.child_registration == attempt.child_reservation
        child = repo.load(attempt.child_registration.child_run_id)
        assert child.pipeline_name == "real_child_pipeline"
        assert child.parent_run_id == run.run_id
        if pinned:
            assert store.read_structured(attempt.child_result)["status"] == "succeeded"
        else:
            assert attempt.child_result is None
        index = store.root / "runs" / f"{child.run_id}.json"
        if index_damage == "missing":
            index.unlink()
        elif index_damage == "corrupt":
            index.write_text("{broken json")
        if damaged_evidence:
            store.blob_path(source).unlink()
        engine = DagEngine(repo, registry)
        expected = (
            "recovery_blocked"
            if damaged_evidence or (index_damage and not pinned)
            else "succeeded"
            if pinned
            else "interrupted"
        )
        for _ in range(2):
            restored = engine.recover(run.run_id)
            assert restored.status == expected
            durable = repo.load(run.run_id)
            assert durable.status == expected
            assert len(durable.dag.node_states["A"].attempts) == 1
            if index_damage and not pinned and not damaged_evidence:
                assert "child run index missing/corrupt" in (
                    durable.dag.node_states["A"].recovery_blocked_reason
                )
            if damaged_evidence:
                assert source.artifact_id in durable.dag.invalid_evidence
            if index_damage == "missing":
                assert not index.exists()
            elif index_damage == "corrupt":
                assert index.read_text() == "{broken json"


def test_process_authorization_crash_retains_attempt_and_blocks_retry(tmp_path, monkeypatch):
    store, registry, plan, source = setup(tmp_path)
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.create(plan, {"source": source})
        original_save = engine._save

        def save_then_crash(current):
            original_save(current)
            node = current.dag.node_states["A"]
            if node.attempts and any(
                w.launch_phase == "release_authorized" for w in node.current().worker_executions
            ):
                raise KeyboardInterrupt("service death after durable authorization")

        monkeypatch.setattr(engine, "_save", save_then_crash)
        with pytest.raises(KeyboardInterrupt):
            engine.drain(run.run_id)
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry, probe=Probe("unknown"))
        recovered = engine.recover(run.run_id)
        assert recovered.status == "recovery_blocked"
        assert recovered.dag.node_states["A"].current().status == "interrupted"
        with pytest.raises(ContractError, match="admission blocked"):
            engine.retry(run.run_id, "A", recovered.dag.revision)
        blocked = repo.load(run.run_id)
        assert len(blocked.dag.node_states["A"].attempts) == 1
        engine.probe = Probe("exited")
        admit_compute(repo, engine.probe)
        from assets_generator.workbench_process import LinuxProcessProbe

        engine.probe = LinuxProcessProbe()
        restored = engine.retry(run.run_id, "A", blocked.dag.revision)
        assert restored.status == "succeeded"
        assert len(restored.dag.node_states["A"].attempts) == 2
