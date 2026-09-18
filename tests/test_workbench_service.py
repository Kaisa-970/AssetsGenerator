"""Real HTTP transport through durable workbench execution, using CPU test backends."""

from __future__ import annotations

import io
import json
import threading
import time
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path

from PIL import Image
from test_instance_proposals import FakeProposer
from test_scene_extraction import setup

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.models import ArtifactRef
from assets_generator.workbench_engine import BackendProfile, WorkbenchEngine
from assets_generator.workbench_http import create_workbench_server
from assets_generator.workbench_persistence import WorkbenchRepository
from assets_generator.workbench_service import LocalWorkbenchService


class CountingProposer(FakeProposer):
    def __init__(self):
        self._calls = [0]

    @property
    def calls(self):
        return self._calls[0]

    def propose(self, store, image):
        self._calls[0] += 1
        return super().propose(store, image)


@contextmanager
def running_http(service):
    server = create_workbench_server(service, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    headers = {}

    def request(method, path, body=None):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    session = json.loads(request("GET", "/session")[1])
    headers.update(
        {
            "Origin": f"http://127.0.0.1:{server.server_port}",
            "X-Workbench-Token": session["token"],
            "Content-Type": "application/json",
        }
    )
    try:
        yield request
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
        service.close()


def poll(request, run_id, expected):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        status, data = request("GET", f"/runs/{run_id}")
        assert status == 200, data
        view = json.loads(data)
        assert view["status"] not in {"failed", "interrupted"}, view
        assert not view.get("background_error"), view
        if view["status"] == expected:
            return view
        time.sleep(0.05)
    raise AssertionError(f"Run did not reach {expected}: {view}")


def test_http_upload_wait_restart_confirm_and_download(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    args = setup(fixture)
    proposer = CountingProposer()
    profile = BackendProfile(
        "fake", proposer, args["resolved_plan"], {"model": "fake-sam"}, {"model": "fake-shape"}
    )
    store = LocalArtifactStore(tmp_path / "store")
    workbench = tmp_path / "workbench"
    stream = io.BytesIO()
    Image.new("L", (8, 6), 160).save(stream, "PNG")

    with WorkbenchRepository(store, workbench) as repo:
        engine = WorkbenchEngine(repo, {"fake": profile})
        with running_http(LocalWorkbenchService(engine)) as request:
            status, data = request("POST", "/inputs", stream.getvalue())
            assert status == 201, data
            imported = json.loads(data)["image"]
            status, data = request(
                "POST",
                "/runs",
                json.dumps(
                    {
                        "image": imported,
                        "shape_profile": "fake",
                        "parameters": {},
                        "idempotency_key": "http-create",
                    }
                ),
            )
            assert status == 202, data
            run_id = json.loads(data)["run_id"]
            waiting = poll(request, run_id, "waiting_for_input")
            assert proposer.calls == 1
            assert len(waiting["review"]["items"]) == 2
            status, source = request("GET", waiting["image_url"])
            assert status == 200
            with Image.open(io.BytesIO(source)) as image:
                assert image.mode == "RGB"
                assert image.size == (8, 6)
            status, data = request(
                "POST",
                f"/runs/{run_id}/mask-preview",
                json.dumps(
                    {
                        "expected_revision": waiting["revision"],
                        "proposal_id": "p0",
                        "invert": True,
                        "keep_largest": True,
                    }
                ),
            )
            assert status == 202, data
            preview = json.loads(data)
            status, preview_bytes = request("GET", preview["review"]["draft"]["mask_url"])
            assert status == 200
            before = repo.load(run_id)
            proposed = before.workbench.stage_states["propose"].current().outputs.copy()
            request_ref = before.workbench.stage_states["select"].request_ref

    # Lose all service/engine in-memory queues and reopen the durable repository.
    with WorkbenchRepository(store, workbench) as repo:
        engine = WorkbenchEngine(repo, {"fake": profile})
        recovered = engine.recover(run_id)
        assert recovered.status == "waiting_for_input"
        assert recovered.workbench.stage_states["select"].request_ref == request_ref
        assert recovered.workbench.stage_states["propose"].current().outputs == proposed
        with running_http(LocalWorkbenchService(engine)) as request:
            waiting = poll(request, run_id, "waiting_for_input")
            assert proposer.calls == 1
            assert waiting["review"]["draft"] == preview["review"]["draft"]
            assert request("GET", waiting["review"]["draft"]["mask_url"]) == (200, preview_bytes)
            status, listed = request("GET", "/runs")
            assert status == 200
            assert {"run_id": run_id, "status": "waiting_for_input"} in json.loads(listed)["runs"]
            confirmation = json.dumps(
                {
                    "expected_revision": waiting["revision"],
                    "idempotency_key": "http-confirm",
                    "reviewer": "integration-test",
                }
            )
            status, data = request("POST", f"/runs/{run_id}/decision", confirmation)
            assert status == 202, data
            finished = poll(request, run_id, "succeeded")
            assert proposer.calls == 1
            assert finished["review"]["confirmed"] is True
            assert all(stage["status"] == "succeeded" for stage in finished["stages"])
            outputs = {row["key"]: row["url"] for row in finished["outputs"]}
            assert set(outputs) == {"glb", "release", "asset", "qa"}
            downloaded = {}
            for key, url in outputs.items():
                status, downloaded[key] = request("GET", url)
                assert status == 200
            release = json.loads(downloaded["release"])
            assert downloaded["glb"][:4] == b"glTF"
            assert (
                downloaded["glb"]
                == store.blob_path(
                    ArtifactRef(**release["files"]["geometry/visual.glb"])
                ).read_bytes()
            )
            assert json.loads(downloaded["asset"])
            assert json.loads(downloaded["qa"])
            # A lost HTTP confirmation response must not create another child or inference.
            child_ids = [
                s.current().child_run_id for s in repo.load(run_id).workbench.stage_states.values()
            ]
            status, data = request("POST", f"/runs/{run_id}/decision", confirmation)
            assert status == 202, data
            assert json.loads(data)["status"] == "succeeded"
            assert child_ids == [
                s.current().child_run_id for s in repo.load(run_id).workbench.stage_states.values()
            ]
            assert proposer.calls == 1


def test_recovery_quarantines_one_run_and_keeps_other_runs_available(tmp_path):
    from test_workbench_engine import fixture_engine

    from assets_generator.contracts import ContractError

    store, image, profile = fixture_engine(tmp_path)
    with WorkbenchRepository(store, tmp_path / "workbench") as repo:
        engine = WorkbenchEngine(repo, {"fake": profile})
        bad = engine.create(image, "fake", {}, "bad")
        good = engine.create(image, "fake", {}, "good")
        engine.drain()
        original_recover = engine.recover

        def recover(run_id):
            if run_id == bad.run_id:
                raise ContractError("published artifact corrupt")
            return original_recover(run_id)

        engine.recover = recover
        service = LocalWorkbenchService(engine)
        service.recover_runs()
        with running_http(service) as request:
            status, data = request("GET", "/runs")
            assert status == 200
            rows = {row["run_id"]: row for row in json.loads(data)["runs"]}
            assert rows[bad.run_id]["status"] == "recovery_blocked"
            assert "published artifact corrupt" in rows[bad.run_id]["recovery_error"]
            assert rows[good.run_id]["status"] == "waiting_for_input"
            status, data = request("GET", f"/runs/{bad.run_id}")
            assert status == 200
            assert json.loads(data)["outputs"] == []
            status, data = request(
                "POST",
                f"/runs/{bad.run_id}/retry",
                json.dumps({"expected_revision": 0, "idempotency_key": "retry"}),
            )
            assert status == 409
            assert "recovery blocked" in json.loads(data)["error"]
            assert request("GET", f"/runs/{good.run_id}")[0] == 200
