import json

import pytest
from test_remote_http import request
from test_remote_http import server as _server_fixture

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.remote_http import RemoteTransportUnknown
from assets_generator.remote_submission import RemoteSubmission
from assets_generator.workbench_persistence import DurableIO, WorkbenchRepository


@pytest.fixture
def http_server():
    yield from _server_fixture.__wrapped__()


def test_response_loss_restart_recovers_original_job(tmp_path, http_server):
    state, client = http_server
    store = LocalArtifactStore(tmp_path / "store")
    directory = tmp_path / "journal"
    with WorkbenchRepository(store, directory) as repo:
        journal = RemoteSubmission(repo, client)
        state["drop"] = True
        with pytest.raises(RemoteTransportUnknown):
            journal.submit(request())
        assert state["submissions"] == 1
        assert json.loads(journal._path(request()).read_text())["phase"] == "authorized"
    with WorkbenchRepository(store, directory) as repo:
        journal = RemoteSubmission(repo, client)
        assert journal.recover(request()).job_id == "job-one"
        assert journal.submit(request()).job_id == "job-one"
        assert state["submissions"] == 1
        before = journal._path(request()).read_bytes()
        state["jobs"]["same-key"]["job_id"] = "different"
        with pytest.raises(RemoteTransportUnknown):
            journal.recover(request())
        assert journal._path(request()).read_bytes() == before
        with pytest.raises(ValueError, match="identity"):
            journal.submit(request({"seed": 123}))


def test_durable_authorization_precedes_post_and_no_lookup_resubmit(
    tmp_path, http_server, monkeypatch
):
    state, client = http_server
    store = LocalArtifactStore(tmp_path / "store")
    directory = tmp_path / "journal"
    original = client.submit
    with WorkbenchRepository(store, directory) as repo:
        journal = RemoteSubmission(repo, client)

        def crash(req):
            assert json.loads(journal._path(req).read_text())["phase"] == "authorized"
            raise RuntimeError("crash before POST")

        monkeypatch.setattr(client, "submit", crash)
        with pytest.raises(RuntimeError):
            journal.submit(request())
    monkeypatch.setattr(client, "submit", original)
    with WorkbenchRepository(store, directory) as repo:
        journal = RemoteSubmission(repo, client)
        assert journal.recover(request()) is None
        with pytest.raises(RemoteTransportUnknown, match="no automatic"):
            journal.submit(request())
        assert state["submissions"] == 0


def test_terminal_result_cannot_change(tmp_path, http_server):
    state, client = http_server
    with WorkbenchRepository(LocalArtifactStore(tmp_path / "store"), tmp_path / "journal") as repo:
        journal = RemoteSubmission(repo, client)
        journal.submit(request())
        state["jobs"]["same-key"].update(state="succeeded", result={"mesh": "digest-a"})
        journal.recover(request())
        before = journal._path(request()).read_bytes()
        state["jobs"]["same-key"]["result"] = {"mesh": "digest-b"}
        with pytest.raises(ValueError, match="terminal"):
            journal.recover(request())
        assert journal._path(request()).read_bytes() == before


def test_failed_durable_write_never_sends(tmp_path, http_server):
    state, client = http_server

    def fail(operation, path):
        if operation == "publish" and path.parent.name == "remote_submissions":
            raise OSError("simulated storage failure")

    with WorkbenchRepository(
        LocalArtifactStore(tmp_path / "store"), tmp_path / "journal", io=DurableIO(fail)
    ) as repo:
        journal = RemoteSubmission(repo, client)
        with pytest.raises(OSError):
            journal.submit(request())
        with pytest.raises(RuntimeError, match="healthy"):
            journal.submit(request())
        assert state["submissions"] == 0
