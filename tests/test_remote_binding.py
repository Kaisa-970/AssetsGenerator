import json
from dataclasses import replace

import pytest
from test_dag_persistence import run_record

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.dag_models import DagAttempt
from assets_generator.dag_persistence import DagRepository
from assets_generator.remote_binding import RemoteAttemptBinding
from assets_generator.remote_protocol import RemoteIdentity, RemoteRequest
from assets_generator.serialization import canonical_json_bytes
from assets_generator.workbench_models import read_build_run


def binding():
    input_digest, binding_digest = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    request = RemoteRequest.create(
        RemoteIdentity("service", "sha256:" + "c" * 64),
        RemoteAttemptBinding.key_for("dag_test", "a", 1),
        {"input_digest": input_digest, "binding_digest": binding_digest},
    )
    return RemoteAttemptBinding.create(
        run_id="dag_test",
        node_id="a",
        attempt=1,
        input_digest=input_digest,
        binding_digest=binding_digest,
        endpoint="http://localhost:9000",
        request=request,
    )


def test_owned_binding_roundtrip_and_immutable_active_attempt(tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    run = run_record(store)
    owned = binding()
    node = run.dag.node_states["a"]
    node.status = "running"
    node.attempts.append(
        DagAttempt(
            1,
            input_digest=owned.input_digest,
            binding_digest=owned.binding_digest,
            remote_binding=owned,
        )
    )
    with DagRepository(store, tmp_path / "service") as repo:
        repo.create(run)
        assert repo.load(run.run_id) == run
        assert read_build_run(json.loads(canonical_json_bytes(run))) == run
        for changed in (None, replace(owned, endpoint="http://localhost:9001")):
            loaded = repo.load(run.run_id)
            loaded.dag.node_states["a"].current().remote_binding = changed
            with pytest.raises(ValueError, match="immutable"):
                repo.save(loaded, expected_revision=loaded.dag.revision)
        loaded = repo.load(run.run_id)
        loaded.dag.node_states["a"].current().status = "interrupted"
        loaded.dag.node_states["a"].status = "interrupted"
        repo.save(loaded, expected_revision=loaded.dag.revision)
        assert repo.load(run.run_id).dag.node_states["a"].current().remote_binding == owned


def test_legacy_attempt_wire_shape_unchanged():
    raw = json.loads(canonical_json_bytes(DagAttempt(1)))
    assert "remote_binding" not in raw


@pytest.mark.parametrize(
    "changes",
    [
        {"attempt": 2},
        {"node_id": "another"},
        {"run_id": "dag_other"},
        {"request_digest": "sha256:" + "d" * 64},
        {"input_digest": "sha256:" + "e" * 64},
        {"endpoint": "https://user:secret@example.com"},
    ],
)
def test_owner_and_request_tampering_rejected(changes):
    with pytest.raises(ValueError):
        replace(binding(), **changes)


def test_remote_attempt_is_not_adopted_by_local_scheduler(tmp_path):
    from assets_generator.contracts import ContractError
    from assets_generator.dag_adapters import AdapterRegistry
    from assets_generator.dag_engine import DagEngine

    store = LocalArtifactStore(tmp_path / "store")
    run = run_record(store)
    owned = binding()
    node = run.dag.node_states["a"]
    node.status = "running"
    node.attempts.append(
        DagAttempt(
            1,
            input_digest=owned.input_digest,
            binding_digest=owned.binding_digest,
            remote_binding=owned,
        )
    )
    with DagRepository(store, tmp_path / "service") as repo:
        repo.create(run)
        before = canonical_json_bytes(repo.load(run.run_id))
        with pytest.raises(ContractError, match="not enabled"):
            DagEngine(repo, AdapterRegistry()).drain(run.run_id)
        assert canonical_json_bytes(repo.load(run.run_id)) == before
