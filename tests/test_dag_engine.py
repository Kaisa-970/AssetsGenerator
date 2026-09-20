from pathlib import Path

import pytest

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.compiled_plan import thaw
from assets_generator.contracts import ContractError, OperatorSpec, PortSpec, RelationSpec
from assets_generator.dag_adapters import AdapterRegistry, AdapterSpec, NodeExecutionResult
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository, EvidenceError
from assets_generator.models import StructuredValue
from assets_generator.pipeline import PipelineDefinition, compile_pipeline


class CopyAdapter:
    spec = AdapterSpec("copy", "1", ("copy@1", "join@1"))

    def __init__(self):
        self.calls = []
        self.fail = set()

    def execute(self, context):
        self.calls.append((context.node_id, dict(context.inputs)))
        if context.node_id in self.fail:
            raise ContractError("injected failure")
        return NodeExecutionResult({"image": next(iter(context.inputs.values()))})


class HumanAdapter:
    spec = AdapterSpec("human", "1", ("copy@1",), execution_kind="human")

    def execute(self, context):
        if context.decision is not None:
            decision = context.store.read_structured(context.decision)
            if decision["payload"] != {"accept": True}:
                raise ContractError("accept required")
            return NodeExecutionResult({"image": context.inputs["image"]})
        return NodeExecutionResult(
            wait_request=context.store.persist_structured(
                StructuredValue(
                    "dag_human_request",
                    "DagHumanInputRequest",
                    "1.0",
                    {
                        "run_id": context.run_id,
                        "node_id": context.node_id,
                        "input_digest": context.input_digest,
                        "attempt_id": context.attempt_id,
                    },
                )
            )
        )


def setup(tmp_path: Path, *, human=False):
    store = LocalArtifactStore(tmp_path / "store")
    adapter = CopyAdapter()
    registry = AdapterRegistry()
    registry.register(adapter)
    registry.register(HumanAdapter())
    port = PortSpec(("rgb_image",))
    specs = {
        "copy@1": OperatorSpec("copy", "1", {"image": port}, {"image": port}),
        "join@1": OperatorSpec(
            "join",
            "1",
            {"left": port, "right": port},
            {"image": port},
            (RelationSpec("independent_inputs@1", ("left", "right")),),
        ),
    }
    nodes = {
        "A": {
            "operator": "copy@1",
            "adapter": "copy@1",
            "inputs": {"image": "pipeline.inputs.source"},
        },
        "B": {
            "operator": "copy@1",
            "adapter": "human@1" if human else "copy@1",
            "inputs": {"image": "A.outputs.image"},
        },
        "C": {"operator": "copy@1", "adapter": "copy@1", "inputs": {"image": "A.outputs.image"}},
        "D": {
            "operator": "join@1",
            "adapter": "copy@1",
            "inputs": {"left": "B.outputs.image", "right": "C.outputs.image"},
        },
    }
    plan = registry.bind_plan(
        compile_pipeline(
            PipelineDefinition("diamond", "1", {"source": port}, nodes),
            specs,
            require_explicit_joins=True,
        )
    )
    source = store.persist_bytes(
        b"small-fixture",
        kind="rgb_image",
        schema_name="raster_image",
        schema_version="1.0",
        identity_metadata={"media_type": "application/octet-stream"},
    )
    return store, adapter, registry, plan, source


def test_diamond_once_exact_fanout_join_and_recovery(tmp_path):
    store, adapter, registry, plan, source = setup(tmp_path)
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.create(plan, {"source": source})
        run = engine.drain(run.run_id)
        assert run.status == "succeeded"
        assert [name for name, _ in adapter.calls] == ["A", "B", "C", "D"]
        assert adapter.calls[1][1] == adapter.calls[2][1] == {"image": source}
        assert adapter.calls[3][1] == {"left": source, "right": source}
    with DagRepository(store, tmp_path / "repo") as repo:
        recovered = DagEngine(repo, registry).drain(run.run_id)
        assert recovered.status == "succeeded"
        assert len(adapter.calls) == 4
        assert recovered.dag.node_states["D"].current().provenance["image"]


def test_failure_continues_independent_branch_and_retry_preserves_ancestors(tmp_path):
    store, adapter, registry, plan, source = setup(tmp_path)
    adapter.fail.add("B")
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"source": source}).run_id)
        assert run.status == "failed"
        assert [name for name, _ in adapter.calls] == ["A", "B", "C"]
        assert run.dag.node_states["D"].status == "blocked"
        adapter.fail.clear()
        run = engine.retry(run.run_id, "B", run.dag.revision)
        assert run.status == "succeeded"
        assert [name for name, _ in adapter.calls] == ["A", "B", "C", "B", "D"]
        assert len(run.dag.node_states["B"].attempts) == 2


def test_human_wait_restart_decision_idempotency(tmp_path):
    store, adapter, registry, plan, source = setup(tmp_path, human=True)
    with DagRepository(store, tmp_path / "repo") as repo:
        run = DagEngine(repo, registry).drain(
            DagEngine(repo, registry).create(plan, {"source": source}).run_id
        )
        request = run.dag.node_states["B"].current().request
        assert run.status == "waiting_for_input"
        assert [name for name, _ in adapter.calls] == ["A", "C"]
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.recover(run.run_id)
        assert run.dag.node_states["B"].current().request == request
        revision = run.dag.revision
        kwargs = dict(
            expected_revision=revision,
            idempotency_key="decision1",
            reviewer="test",
            payload={"accept": True},
        )
        run = engine.decide(run.run_id, "B", **kwargs)
        assert run.status == "succeeded"
        assert engine.decide(run.run_id, "B", **kwargs).dag.revision == run.dag.revision
        with pytest.raises(ContractError, match="idempotency"):
            engine.decide(run.run_id, "B", **{**kwargs, "payload": {"accept": False}})
        assert [name for name, _ in adapter.calls] == ["A", "C", "D"]


def test_corrupt_output_blocks_durably_preserves_success_attempt(tmp_path):
    store, adapter, registry, plan, source = setup(tmp_path)
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"source": source}).run_id)
        store.blob_path(source).write_bytes(b"corrupt")
        run = engine.recover(run.run_id)
        assert run.status == "recovery_blocked"
        assert run.dag.node_states["A"].current().status == "succeeded"
        assert run.dag.node_states["A"].status == "recovery_blocked"
        assert repo.load(run.run_id).dag.invalid_evidence
        assert len(adapter.calls) == 4


def test_input_lost_before_dispatch_is_durable_block(tmp_path):
    store, adapter, registry, plan, source = setup(tmp_path)
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.create(plan, {"source": source})
        store.blob_path(source).unlink()
        run = engine.drain(run.run_id)
        assert run.status == "recovery_blocked"
        assert not adapter.calls
        assert run.dag.node_states["A"].status == "recovery_blocked"
        assert not run.dag.node_states["A"].attempts
        assert repo.load(run.run_id).status == "recovery_blocked"


def test_retry_invalid_inputs_is_durable_and_does_not_reset_descendants(tmp_path):
    store, adapter, registry, plan, source = setup(tmp_path)
    adapter.fail.add("A")
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"source": source}).run_id)
        assert run.status == "failed"
        # Make the failed node's retry input unavailable.  The retry command
        # must persist the resulting recovery block before reporting failure;
        # it must not reset the downstream blocked branch in memory only.
        store.blob_path(source).unlink()
        with pytest.raises(EvidenceError):
            engine.retry(run.run_id, "A", run.dag.revision)
        persisted = repo.load(run.run_id)
        assert persisted.status == "recovery_blocked"
        assert persisted.dag.node_states["A"].status == "recovery_blocked"
        assert persisted.dag.node_states["A"].recovery_blocked_reason.startswith(
            "retry_input_invalid:"
        )
        assert persisted.dag.node_states["B"].status == "blocked"


def test_decided_crash_retry_preserves_original_decision(tmp_path, monkeypatch):
    store, adapter, registry, plan, source = setup(tmp_path, human=True)
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"source": source}).run_id)
        execute = engine._execute

        def crash(*args):
            raise KeyboardInterrupt("service stopped after decision commit")

        monkeypatch.setattr(engine, "_execute", crash)
        with pytest.raises(KeyboardInterrupt):
            engine.decide(
                run.run_id,
                "B",
                expected_revision=run.dag.revision,
                idempotency_key="key",
                reviewer="test",
                payload={"accept": True},
            )
        monkeypatch.setattr(engine, "_execute", execute)
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.recover(run.run_id)
        previous = run.dag.node_states["B"].current()
        assert previous.decision is not None
        assert run.status == "interrupted"
        run = engine.retry(run.run_id, "B", run.dag.revision)
        assert run.status == "succeeded"
        assert run.dag.node_states["B"].current().decision == previous.decision
        assert run.dag.node_states["B"].current().request == previous.request
        assert len(run.dag.node_states["B"].attempts) == 2
        assert [name for name, _ in adapter.calls] == ["A", "C", "D"]


@pytest.mark.parametrize("after_artifact", [False, True])
def test_decision_crash_after_artifact_before_receipt_commit_is_idempotent(
    tmp_path, monkeypatch, after_artifact
):
    store, adapter, registry, plan, source = setup(tmp_path, human=True)
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"source": source}).run_id)
        original_persist = store.persist_structured

        published = []
        tripped = False

        def persist_then_crash(value):
            nonlocal tripped
            if value.kind != "dag_human_decision":
                return original_persist(value)
            if not tripped:
                tripped = True
                if after_artifact:
                    published.append(original_persist(value))
                raise RuntimeError("stopped around decision artifact publication")
            reference = original_persist(value)
            published.append(reference)
            return reference

        monkeypatch.setattr(store, "persist_structured", persist_then_crash)
        with pytest.raises(RuntimeError):
            engine.decide(
                run.run_id,
                "B",
                expected_revision=run.dag.revision,
                idempotency_key="prepared-key",
                reviewer="test",
                payload={"accept": True},
            )

    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        recovered = engine.recover(run.run_id)
        decision = recovered.dag.node_states["B"].current().decision
        assert decision is not None
        assert set(published) == {decision}
        assert recovered.status == "interrupted"
        assert recovered.dag.receipts["prepared-key"]["status"] == "committed"
        # Repeating the same request must reuse the durable receipt and decision.
        again = engine.decide(
            run.run_id,
            "B",
            expected_revision=recovered.dag.revision,
            idempotency_key="prepared-key",
            reviewer="test",
            payload={"accept": True},
        )
        assert again.dag.node_states["B"].current().decision == decision
        assert again.dag.revision == recovered.dag.revision
        assert [name for name, _ in adapter.calls] == ["A", "C"]
        completed = engine.retry(run.run_id, "B", again.dag.revision)
        assert completed.status == "succeeded"
        assert completed.dag.node_states["B"].current().decision == decision
        assert [name for name, _ in adapter.calls] == ["A", "C", "D"]
        engine.decide(
            run.run_id,
            "B",
            expected_revision=completed.dag.revision,
            idempotency_key="prepared-key",
            reviewer="test",
            payload={"accept": True},
        )
        assert [name for name, _ in adapter.calls] == ["A", "C", "D"]


@pytest.mark.parametrize(
    "field", ["resolved_inputs", "adapter", "binding_digest", "parameters_digest"]
)
def test_valid_output_cannot_restore_mismatched_input_or_binding(tmp_path, field):
    store, adapter, registry, plan, source = setup(tmp_path)
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"source": source}).run_id)
        attempt = run.dag.node_states["A"].current()
        if field == "resolved_inputs":
            other = store.persist_bytes(
                b"another",
                kind="rgb_image",
                schema_name="raster_image",
                schema_version="1.0",
                identity_metadata={"media_type": "application/octet-stream"},
            )
            attempt.resolved_inputs = {"image": other}
        else:
            setattr(attempt, field, "invalid")
        # Inject a forged historical snapshot bypassing normal CAS guards.
        repo.commit(run)
        recovered = engine.recover(run.run_id)
        assert recovered.status == "recovery_blocked"
        assert "recovery_input_mismatch" in recovered.dag.node_states["A"].recovery_blocked_reason
        assert recovered.dag.node_states["A"].current().status == "succeeded"
        assert len(adapter.calls) == 4


def test_corrupt_provenance_is_not_recreated_by_recovery(tmp_path):
    store, adapter, registry, plan, source = setup(tmp_path)
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"source": source}).run_id)
        reference = run.dag.node_states["A"].current().provenance["image"][0]
        store.blob_path(reference).unlink()
        recovered = engine.recover(run.run_id)
        assert recovered.status == "recovery_blocked"
        assert not store.blob_path(reference).exists()
        assert "recovery_output_invalid" in recovered.dag.node_states["A"].recovery_blocked_reason


def test_draft_survives_restart_without_confirming(tmp_path):
    store, adapter, registry, plan, source = setup(tmp_path, human=True)
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"source": source}).run_id)
        run = engine.save_draft(
            run.run_id,
            "B",
            expected_revision=run.dag.revision,
            payload={"choice": "unconfirmed", "options": [True, False]},
        )
    with DagRepository(store, tmp_path / "repo") as repo:
        run = DagEngine(repo, registry).recover(run.run_id)
        assert run.status == "waiting_for_input"
        assert run.dag.node_states["B"].current().draft["choice"] == "unconfirmed"
        assert run.dag.node_states["B"].current().decision is None
        assert [name for name, _ in adapter.calls] == ["A", "C"]


def test_waiting_takes_precedence_over_independent_failure(tmp_path):
    store, adapter, registry, plan, source = setup(tmp_path, human=True)
    adapter.fail.add("C")
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"source": source}).run_id)
        assert run.status == "waiting_for_input"
        assert run.dag.node_states["C"].status == "failed"
        run = engine.decide(
            run.run_id,
            "B",
            expected_revision=run.dag.revision,
            idempotency_key="done",
            reviewer="test",
            payload={"accept": True},
        )
        assert run.status == "failed"
        assert run.dag.node_states["D"].status == "blocked"


class FreshAdapter(CopyAdapter):
    def execute(self, context):
        if context.node_id == "A":
            return NodeExecutionResult(
                {
                    "image": context.store.persist_bytes(
                        context.attempt_id.encode(),
                        kind="rgb_image",
                        schema_name="raster_image",
                        schema_version="1.0",
                    )
                }
            )
        return super().execute(context)


def test_changed_upstream_invalidates_decision_and_waits_again(tmp_path):
    store, _, _, original, source = setup(tmp_path, human=True)
    registry = AdapterRegistry()
    registry.register(FreshAdapter())
    registry.register(HumanAdapter())
    plan = registry.bind_plan(original.static_plan)
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"source": source}).run_id)
        run = engine.decide(
            run.run_id,
            "B",
            expected_revision=run.dag.revision,
            idempotency_key="first",
            reviewer="test",
            payload={"accept": True},
        )
        original_decision = run.dag.node_states["B"].current().decision
        store.blob_path(run.dag.node_states["A"].current().provenance["image"][0]).unlink()
        run = engine.recover(run.run_id)
        run = engine.retry(run.run_id, "A", run.dag.revision)
        assert run.status == "waiting_for_input"
        assert run.dag.node_states["B"].current().decision is None
        assert run.dag.node_states["B"].attempts[0].decision == original_decision
        assert run.dag.node_states["C"].status == "succeeded"
        assert run.dag.node_states["D"].status != "succeeded"
        revision = run.dag.revision
        run = engine.decide(
            run.run_id,
            "B",
            expected_revision=revision,
            idempotency_key="first",
            reviewer="test",
            payload={"accept": True},
        )
        assert run.dag.revision == revision
        assert run.status == "waiting_for_input"
        assert run.dag.node_states["B"].current().decision is None
        assert run.dag.node_states["D"].status != "succeeded"


def test_invalid_old_decision_retry_creates_new_request(tmp_path):
    store, adapter, registry, plan, source = setup(tmp_path, human=True)
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"source": source}).run_id)
        run = engine.decide(
            run.run_id,
            "B",
            expected_revision=run.dag.revision,
            idempotency_key="first",
            reviewer="test",
            payload={"accept": True},
        )
        decision = run.dag.node_states["B"].current().decision
        store.blob_path(decision).unlink()
        run = engine.recover(run.run_id)
        run = engine.retry(run.run_id, "B", run.dag.revision)
        assert run.status == "waiting_for_input"
        assert run.dag.node_states["B"].current().decision is None
        assert run.dag.node_states["B"].attempts[0].decision == decision


def test_failed_attempt_corrupt_partial_evidence_blocks_durably(tmp_path):
    store, adapter, registry, plan, source = setup(tmp_path)
    adapter.fail.add("B")
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"source": source}).run_id)
        reference = store.persist_bytes(
            b"partial", kind="rgb_image", schema_name="raster_image", schema_version="1.0"
        )
        run.dag.node_states["B"].current().outputs = {"image": reference}
        repo.commit(run)
        store.blob_path(reference).unlink()
        run = engine.recover(run.run_id)
        assert run.status == "recovery_blocked"
        assert run.dag.node_states["B"].current().status == "failed"
        assert repo.load(run.run_id).status == "recovery_blocked"
        adapter.fail.clear()
        run = engine.retry(run.run_id, "B", run.dag.revision)
        assert run.status == "succeeded"
        assert reference.artifact_id in run.dag.invalid_evidence


def test_older_history_block_requires_repair_or_explicit_retry(tmp_path):
    store, adapter, registry, plan, source = setup(tmp_path)
    adapter.fail.add("B")
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"source": source}).run_id)
        historical = store.persist_bytes(
            b"older-partial", kind="rgb_image", schema_name="raster_image", schema_version="1.0"
        )
        run.dag.node_states["B"].current().outputs = {"image": historical}
        repo.commit(run)
        adapter.fail.clear()
        run = engine.retry(run.run_id, "B", run.dag.revision)
        assert run.status == "succeeded"
        blob = store.blob_path(historical)
        data = blob.read_bytes()
        blob.unlink()
        run = engine.recover(run.run_id)
        assert run.status == "recovery_blocked"
        assert run.dag.node_states["A"].status == "succeeded"
        assert run.dag.node_states["B"].status == "recovery_blocked"
        consumers = repo.load(run.run_id).dag.evidence_consumers[historical.artifact_id]
        assert any(c.node_id == "B" and c.attempt == 1 and c.port == "image" for c in consumers)
        assert all(c.node_id != "A" for c in consumers)
        run = engine.recover(run.run_id)
        assert run.status == "recovery_blocked"
        blob.write_bytes(data)
        assert engine.recover(run.run_id).status == "succeeded"


def test_failed_upstream_retry_preserves_blocked_descendant_history(tmp_path):
    store, adapter, registry, plan, source = setup(tmp_path)
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"source": source}).run_id)
        store.blob_path(run.dag.node_states["A"].current().provenance["image"][0]).unlink()
        run = engine.recover(run.run_id)
        adapter.fail.add("A")
        run = engine.retry(run.run_id, "A", run.dag.revision)
        assert run.status == "failed"
        persisted = repo.load(run.run_id)
        for name in ("B", "C", "D"):
            assert persisted.dag.node_states[name].status == "blocked"
            assert persisted.dag.node_states[name].current().status == "succeeded"
            assert len(persisted.dag.node_states[name].attempts) == 1


def test_unused_input_corruption_blocks_run_not_arbitrary_node(tmp_path):
    store, adapter, registry, original, source = setup(tmp_path)
    # Recompile from a definition to include an intentionally unused input.
    from assets_generator.pipeline import _operator_specs_from_raw

    static = original.static_plan
    specs = _operator_specs_from_raw(
        {"operators": [thaw(n.operator_contract) for n in static.nodes if n.node_id in {"A", "D"}]}
    )
    definition = PipelineDefinition(
        "unused",
        "1",
        {
            "source": PortSpec(("rgb_image",)),
            "unused": PortSpec(("rgb_image",)),
        },
        {
            "A": {
                "operator": "copy@1",
                "adapter": "copy@1",
                "inputs": {"image": "pipeline.inputs.source"},
            }
        },
    )
    plan = registry.bind_plan(compile_pipeline(definition, specs, require_explicit_joins=True))
    extra = store.persist_bytes(
        b"unused", kind="rgb_image", schema_name="raster_image", schema_version="1.0"
    )
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"source": source, "unused": extra}).run_id)
        blob = store.blob_path(extra)
        data = blob.read_bytes()
        blob.unlink()
        run = engine.recover(run.run_id)
        assert run.status == "recovery_blocked"
        assert run.dag.node_states["A"].status == "succeeded"
        assert extra.artifact_id in repo.load(run.run_id).dag.unassigned_evidence_blocks
        blob.write_bytes(data)
        assert engine.recover(run.run_id).status == "succeeded"
