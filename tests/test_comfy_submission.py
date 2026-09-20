import pytest

from assets_generator.comfy_submission import ComfySubmissionJournal, ComfySubmissionUnknown

PROMPT = {"1": {"class_type": "Fixture", "inputs": {"seed": 42}}}
DEPLOYMENT = {"endpoint": "http://localhost:8188", "revision": "fixture"}


def test_fixed_prompt_and_deployment_survive_reopen_and_bind_key(tmp_path):
    path = tmp_path / "journal.sqlite"
    journal = ComfySubmissionJournal(path)
    before = journal.prepare("one", deployment=DEPLOYMENT, prompt=PROMPT)
    journal.close()
    journal = ComfySubmissionJournal(path)
    try:
        assert journal.prepare("one", deployment=DEPLOYMENT, prompt=PROMPT) == before
        with pytest.raises(ValueError, match="identity conflict"):
            journal.prepare("one", deployment={**DEPLOYMENT, "revision": "other"}, prompt=PROMPT)
        assert journal.read("one") == before
        with pytest.raises(ValueError, match="missing"):
            journal.submit_once("missing", lambda _: pytest.fail("unexpected network"))
    finally:
        journal.close()


@pytest.mark.parametrize("failure", ["lost_response", "crash", "wrong_id"])
def test_uncertain_post_is_never_repeated_after_reopen(tmp_path, failure):
    path = tmp_path / "journal.sqlite"
    journal = ComfySubmissionJournal(path)
    original = journal.prepare("one", deployment=DEPLOYMENT, prompt=PROMPT)
    calls = []

    def post(body):
        calls.append(body)
        other = ComfySubmissionJournal(path)
        try:
            assert other.read("one")["phase"] == "sending"
            with pytest.raises(ComfySubmissionUnknown):
                other.submit_once("one", lambda _: pytest.fail("duplicate submission"))
        finally:
            other.close()
        if failure == "lost_response":
            raise OSError("upstream accepted but connection closed")
        if failure == "crash":
            raise KeyboardInterrupt()
        return {"prompt_id": "unexpected"}

    try:
        with pytest.raises(KeyboardInterrupt if failure == "crash" else ComfySubmissionUnknown):
            journal.submit_once("one", post)
    finally:
        journal.close()
    journal = ComfySubmissionJournal(path)
    try:
        assert journal.prepare("one", deployment=DEPLOYMENT, prompt=PROMPT)["phase"] == "sending"
        with pytest.raises(ComfySubmissionUnknown):
            journal.submit_once("one", post)
        assert calls == [{"prompt_id": original["prompt_id"], "prompt": PROMPT}]
    finally:
        journal.close()


def test_acknowledged_is_not_posted_twice(tmp_path):
    journal = ComfySubmissionJournal(tmp_path / "journal.sqlite")
    try:
        journal.prepare("one", deployment=DEPLOYMENT, prompt=PROMPT)
        result = journal.submit_once(
            "one", lambda body: {"prompt_id": body["prompt_id"], "node_errors": {}}
        )
        assert result["phase"] == "acknowledged"
        assert journal.submit_once("one", lambda _: pytest.fail("replayed")) == result
    finally:
        journal.close()


def test_observation_is_fixed_offline_and_missing_evidence_not_repaired(tmp_path):
    from assets_generator.comfy_http import ComfyClient

    path = tmp_path / "journal.sqlite"
    journal = ComfySubmissionJournal(path)
    record = journal.prepare("one", deployment=DEPLOYMENT, prompt=PROMPT)
    journal.submit_once("one", lambda body: {"prompt_id": body["prompt_id"]})
    history = {
        record["prompt_id"]: {
            "prompt": [0, record["prompt_id"], PROMPT, {}, ["1"]],
            "outputs": {"1": {}},
            "status": {"status_str": "success", "completed": True, "messages": []},
        }
    }
    original = journal.record_history("one", history)
    journal.close()
    journal = ComfySubmissionJournal(path)
    try:
        client = ComfyClient(DEPLOYMENT["endpoint"])
        client._json = lambda *args: pytest.fail("offline observation must not query")
        assert client.observe(journal, "one") == original
        assert journal.record_history("one", history) == original
        changed = {record["prompt_id"]: {**history[record["prompt_id"]], "outputs": {}}}
        with pytest.raises(ValueError, match="conflict"):
            journal.record_history("one", changed)
        assert journal.submit_once("one", lambda _: pytest.fail("replayed"))["phase"] == "observed"
        journal.db.execute("DELETE FROM observations")
        with pytest.raises(ValueError, match="missing or corrupt"):
            client.observe(journal, "one")
        with pytest.raises(ValueError, match="missing or corrupt"):
            journal.record_history("one", history)
        assert journal.db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    finally:
        journal.close()


def test_observation_and_phase_commit_atomically(tmp_path):
    import sqlite3

    journal = ComfySubmissionJournal(tmp_path / "journal.sqlite")
    try:
        record = journal.prepare("one", deployment=DEPLOYMENT, prompt=PROMPT)
        journal.submit_once("one", lambda body: {"prompt_id": body["prompt_id"]})
        history = {
            record["prompt_id"]: {
                "prompt": [0, record["prompt_id"], PROMPT],
                "outputs": {},
                "status": {"status_str": "error", "completed": False, "messages": []},
            }
        }
        journal.db.execute(
            "CREATE TRIGGER fail_phase BEFORE UPDATE ON prompts "
            "WHEN NEW.phase='observed' BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            journal.record_history("one", history)
        assert journal.read("one")["phase"] == "acknowledged"
        assert journal.observation("one") is None
        journal.db.execute("DROP TRIGGER fail_phase")
        assert journal.record_history("one", history)["state"] == "failed"
    finally:
        journal.close()
