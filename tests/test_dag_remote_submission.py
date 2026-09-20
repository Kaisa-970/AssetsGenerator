from dataclasses import replace

import pytest
from test_dag_persistence import run_record
from test_remote_binding import binding
from test_remote_http import server as _server_fixture

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.dag_models import DagAttempt
from assets_generator.dag_persistence import DagRepository
from assets_generator.dag_remote_submission import DagRemoteSubmission
from assets_generator.remote_http import RemoteTransportUnknown


@pytest.fixture
def http_server():
    yield from _server_fixture.__wrapped__()


def setup(repo, client):
    owned = replace(binding(), endpoint=client.endpoint)
    run = run_record(repo.store)
    node = run.dag.node_states["a"]
    node.status = "running"
    node.attempts.append(
        DagAttempt(1, input_digest=owned.input_digest, binding_digest=owned.binding_digest)
    )
    repo.create(run)
    return owned


def test_no_network_without_persisted_owner_and_recover_after_restart(tmp_path, http_server):
    state, client = http_server
    store = LocalArtifactStore(tmp_path / "store")
    directory = tmp_path / "service"
    with DagRepository(store, directory) as repo:
        owned = setup(repo, client)
        bridge = DagRemoteSubmission(repo, client)
        with pytest.raises(ValueError, match="persisted"):
            bridge.submit(owned)
        with pytest.raises(ValueError, match="revision"):
            bridge.prepare(owned, expected_revision=-1)
        assert state["submissions"] == 0
        bridge.prepare(owned, expected_revision=0)
        assert repo.load(owned.run_id).dag.node_states["a"].current().remote_binding == owned
        assert state["submissions"] == 0
        state["drop"] = True
        with pytest.raises(RemoteTransportUnknown):
            bridge.submit(owned)
    with DagRepository(store, directory) as repo:
        bridge = DagRemoteSubmission(repo, client)
        assert bridge.recover(owned).job_id == "job-one"
        assert bridge.submit(owned).job_id == "job-one"
        assert state["submissions"] == 1


def test_missing_both_journal_files_cannot_recreate_bound_submission(tmp_path, http_server):
    state, client = http_server
    with DagRepository(LocalArtifactStore(tmp_path / "store"), tmp_path / "service") as repo:
        owned = setup(repo, client)
        bridge = DagRemoteSubmission(repo, client)
        bridge.prepare(owned, expected_revision=0)
        bridge.submit(owned)
        bridge.journal._path(owned.request()).unlink()
        bridge.journal._reservation(owned.request()).unlink()
        with pytest.raises(OSError):
            bridge.submit(owned)
        with pytest.raises(OSError):
            bridge.prepare(owned, expected_revision=repo.load(owned.run_id).dag.revision)
        assert state["submissions"] == 1


def test_foreign_directory_and_stopped_attempt_cannot_submit(tmp_path, http_server):
    state, client = http_server
    store = LocalArtifactStore(tmp_path / "store")
    with DagRepository(store, tmp_path / "service") as repo:
        owned = setup(repo, client)
        bridge = DagRemoteSubmission(repo, client)
        bridge.prepare(owned, expected_revision=0)
        run = repo.load(owned.run_id)
        node = run.dag.node_states["a"]
        node.status = node.current().status = "interrupted"
        repo.save(run, expected_revision=run.dag.revision)
        with pytest.raises(ValueError, match="running"):
            bridge.submit(owned)
    with DagRepository(store, tmp_path / "foreign") as repo:
        with pytest.raises(ValueError, match="another service"):
            DagRemoteSubmission(repo, client).submit(owned)
    assert state["submissions"] == 0


def test_parent_save_failure_keeps_prepared_request_unsent(tmp_path, http_server, monkeypatch):
    state, client = http_server
    store = LocalArtifactStore(tmp_path / "store")
    directory = tmp_path / "service"
    with DagRepository(store, directory) as repo:
        owned = setup(repo, client)
        bridge = DagRemoteSubmission(repo, client)
        original = repo.save

        def fail(*args, **kwargs):
            raise OSError("parent snapshot unavailable")

        monkeypatch.setattr(repo, "save", fail)
        with pytest.raises(OSError):
            bridge.prepare(owned, expected_revision=0)
        monkeypatch.setattr(repo, "save", original)
        with pytest.raises(ValueError, match="persisted"):
            bridge.submit(owned)
        assert state["submissions"] == 0
    with DagRepository(store, directory) as repo:
        bridge = DagRemoteSubmission(repo, client)
        bridge.prepare(owned, expected_revision=0)
        assert bridge.submit(owned).job_id == "job-one"
        assert state["submissions"] == 1


def test_download_requires_durable_success_and_rejects_changed_result(tmp_path, http_server):
    from assets_generator.serialization import sha256_bytes

    state, client = http_server
    store = LocalArtifactStore(tmp_path / "store")
    directory = tmp_path / "service"
    with DagRepository(store, directory) as repo:
        owned = setup(repo, client)
        bridge = DagRemoteSubmission(repo, client)
        bridge.prepare(owned, expected_revision=0)
        bridge.submit(owned)
        job = state["jobs"][owned.submission_key]
        job.update(
            state="succeeded",
            result={
                "outputs": [
                    {
                        "output_id": "mesh",
                        "blob_digest": sha256_bytes(b"test output"),
                        "byte_length": len(b"test output"),
                        "media_type": "model/gltf-binary",
                    }
                ]
            },
        )
        # A success existing only on the server must first be observed durably.
        with pytest.raises(ValueError, match="persisted successful"):
            bridge.download(owned, "mesh")
        assert state.get("downloads", 0) == 0
        bridge.recover(owned)
    with DagRepository(store, directory) as repo:
        bridge = DagRemoteSubmission(repo, client)
        before = bridge.journal._path(owned.request()).read_bytes()
        assert bridge.download(owned, "mesh") == b"test output"
        assert state["downloads"] == 1
        assert bridge.journal._path(owned.request()).read_bytes() == before
        state["blob"] = b"other bytes"
        job["result"]["outputs"][0]["blob_digest"] = sha256_bytes(state["blob"])
        with pytest.raises(ValueError, match="pinned"):
            bridge.download(owned, "mesh")
        assert state["downloads"] == 1
        assert bridge.journal._path(owned.request()).read_bytes() == before
        bridge.journal._path(owned.request()).unlink()
        with pytest.raises(OSError):
            bridge.download(owned, "mesh")
        assert state["downloads"] == 1


@pytest.mark.parametrize("terminal", ["succeeded", "failed"])
def test_terminal_evidence_survives_restart_without_service_or_journal(
    tmp_path, http_server, monkeypatch, terminal
):
    state, client = http_server
    store = LocalArtifactStore(tmp_path / "store")
    directory = tmp_path / "service"
    with DagRepository(store, directory) as repo:
        owned = setup(repo, client)
        bridge = DagRemoteSubmission(repo, client)
        bridge.prepare(owned, expected_revision=0)
        bridge.submit(owned)
        with pytest.raises(ValueError, match="terminal"):
            bridge.pin_result(owned, expected_revision=repo.load(owned.run_id).dag.revision)
        state["jobs"][owned.submission_key].update(
            state=terminal,
            result={"outputs": []} if terminal == "succeeded" else None,
            error={"code": "BACKEND_TIMEOUT", "detail": "deadline exceeded"}
            if terminal == "failed"
            else None,
        )
        observed = bridge.recover(owned)
        reference = bridge.pin_result(owned, expected_revision=repo.load(owned.run_id).dag.revision)
        bridge.journal._path(owned.request()).unlink()
        bridge.journal._reservation(owned.request()).unlink()

    def no_network(*args, **kwargs):
        raise AssertionError("offline evidence validation must not use HTTP")

    monkeypatch.setattr(client, "query", no_network)
    monkeypatch.setattr(client, "lookup", no_network)
    with DagRepository(store, directory) as repo:
        bridge = DagRemoteSubmission(repo, client)
        run = repo.load(owned.run_id)
        assert run.dag.node_states["a"].current().remote_result == reference
        assert bridge.pinned_result(owned) == observed
        assert bridge.pin_result(owned, expected_revision=run.dag.revision) == reference
        assert repo.load(owned.run_id).dag.revision == run.dag.revision
        run.dag.node_states["a"].current().remote_result = None
        with pytest.raises(ValueError, match="immutable"):
            repo.save(run, expected_revision=run.dag.revision)
        store.blob_path(reference).unlink()
        with pytest.raises(ValueError, match="missing/corrupt"):
            bridge.pin_result(owned, expected_revision=run.dag.revision)
        assert not store.blob_path(reference).exists()
        assert state["submissions"] == 1


def test_result_without_binding_rejected():
    from assets_generator.models import ArtifactRef

    with pytest.raises(ValueError, match="requires remote binding"):
        DagAttempt(1, remote_result=ArtifactRef("sha256:" + "a" * 64))
