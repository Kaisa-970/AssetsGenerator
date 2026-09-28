"""Real editor HTTP -> real remote HTTP -> CPU shape -> published asset."""

import io
import json
import sys
import threading
import time
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

import pytest
import trimesh
import yaml
from PIL import Image
from test_remote_http import request
from test_remote_service_http import serve
from test_remote_shape_service import Backend

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.dag_adapters import AdapterRegistry
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_image_mask import ApplyBinaryMaskAdapter
from assets_generator.dag_persistence import DagRepository
from assets_generator.dag_remote_profiles import register_remote_shape_profiles
from assets_generator.node_editor import DraftEditor, create_editor_server
from assets_generator.node_editor_execution import NodeEditorExecution
from assets_generator.remote_service_process import ServiceProcessWorker
from assets_generator.remote_service_worker import execute_service_job
from assets_generator.remote_shape_service import ShapeServiceHandler
from assets_generator.worker import ProcessJobRequest


@pytest.mark.parametrize("abandon_first", [False, True])
@pytest.mark.parametrize("masked_input", [False, True])
def test_remote_asset_chain_through_editor_http(tmp_path, abandon_first, masked_input, monkeypatch):
    with serve(tmp_path / "service.sqlite") as (remote, client, _):
        identity = request().identity
        registry = AdapterRegistry()
        registry.register(ApplyBinaryMaskAdapter())
        register_remote_shape_profiles(
            registry,
            {
                "default_profile": "cpu",
                "profiles": {
                    "cpu": {
                        "endpoint": client.endpoint,
                        "service_id": identity.service_id,
                        "backend_digest": identity.backend_digest,
                    }
                },
            },
        )
        with DagRepository(LocalArtifactStore(tmp_path / "store"), tmp_path / "core") as repo:
            execution = NodeEditorExecution(DagEngine(repo, registry))
            dispatched = []
            original_dispatch = execution._dispatch

            def record_dispatch(run_id, command):
                dispatched.append(run_id)
                return original_dispatch(run_id, command)

            monkeypatch.setattr(execution, "_dispatch", record_dispatch)
            editor = DraftEditor(tmp_path / "drafts", execution=execution)
            server = create_editor_server(editor, 0)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"

            def get(path):
                with urlopen(base + path, timeout=10) as response:
                    return json.load(response)

            def post(path, body, binary=False):
                data = body if binary else json.dumps(body).encode()
                with urlopen(
                    Request(
                        base + path,
                        data,
                        {
                            "Content-Type": "application/octet-stream"
                            if binary
                            else "application/json"
                        },
                        method="POST",
                    ),
                    timeout=10,
                ) as response:
                    return json.load(response)

            def wait_for(run_id, predicate):
                deadline = time.monotonic() + 30
                while True:
                    snapshot = get(f"/api/runs/{run_id}")
                    if predicate(snapshot):
                        return snapshot
                    assert time.monotonic() < deadline
                    time.sleep(0.05)

            def settled(run_id):
                return wait_for(run_id, lambda snapshot: not snapshot["busy"])

            def queued(run_id, previous_key=None):
                def is_queued(snapshot):
                    states = snapshot["run"]["dag"]["node_states"]
                    attempts = states["shape"]["attempts"]
                    if not attempts:
                        return False
                    binding = attempts[-1].get("remote_binding")
                    if not binding or binding["submission_key"] == previous_key:
                        return False
                    remote_request = remote.request_for(binding["submission_key"])
                    return (
                        remote_request is not None
                        and remote.lookup(remote_request).state == "queued"
                    )

                return wait_for(run_id, is_queued)

            try:
                graph = yaml.safe_load(
                    Path(
                        "examples/remote-mask-shape.yaml"
                        if masked_input
                        else "pipelines/remote_shape_asset_v1.yaml"
                    ).read_text()
                )
                graph["nodes"]["shape"]["backend"] = "cpu"
                assert post("/api/compile", {"pipeline": graph})["execution_ready"]
                data = io.BytesIO()
                Image.new("RGBA", (2, 2), (255, 20, 10, 120)).save(data, format="PNG")
                uploaded = post("/api/inputs/rgba", data.getvalue(), binary=True)
                assert get("/api/runs")["runs"] == []
                creation = {
                    "pipeline": graph,
                    "image_ref": uploaded["image_ref"],
                    "idempotency_key": "browser-intent-one",
                }
                if masked_input:
                    rgb_data = io.BytesIO()
                    Image.new("RGB", (2, 2), (255, 20, 10)).save(rgb_data, format="PNG")
                    mask_data = io.BytesIO()
                    Image.frombytes("L", (2, 2), bytes([255, 0, 0, 255])).save(
                        mask_data, format="PNG"
                    )
                    rgb = post("/api/inputs/image", rgb_data.getvalue(), binary=True)
                    mask = post("/api/inputs/mask", mask_data.getvalue(), binary=True)
                    del creation["image_ref"]
                    creation["input_refs"] = {"image": rgb["image_ref"], "mask": mask["mask_ref"]}
                started = post("/api/runs", creation)
                run_id = started["run"]["run_id"]
                waiting = queued(run_id)
                assert waiting["run"]["status"] == "running"
                exported = get(f"/api/runs/{run_id}/draft")
                assert exported["source_run_id"] == run_id
                assert exported["source_plan_id"] == waiting["run"]["dag"]["plan_id"]
                original_plan = get(f"/api/runs/{run_id}/plan")
                cloned = exported["pipeline"]
                assert cloned["inputs"]["image"]["kind"] == (
                    "rgb_image" if masked_input else "rgba_image"
                )
                assert cloned["nodes"]["shape"]["backend"] == "cpu"
                assert (
                    cloned["nodes"]["shape"]["parameters"]
                    == original_plan["bindings"]["shape"]["parameters"]
                )
                assert post("/api/compile", {"pipeline": cloned})["execution_ready"]
                assert get(f"/api/runs/{run_id}")["run"]["run_id"] == run_id
                assert dispatched == [run_id]
                assert len(get("/api/runs")["runs"]) == 1
                attempt = repo.load(run_id).dag.node_states["shape"].current()
                req = remote.request_for(attempt.remote_binding.submission_key)
                assert remote.lookup(req).state == "queued"
                assert post("/api/runs", creation)["run"]["run_id"] == run_id
                handler = ShapeServiceHandler(
                    identity, tmp_path / "work", Backend, lambda: identity
                )
                if abandon_first:
                    original_key = req.submission_key
                    remote.transition(req, expected="queued", state="running")
                    ServiceProcessWorker(remote, req).run(
                        ProcessJobRequest(
                            [sys.executable, "-c", "pass"],
                            tmp_path,
                            10,
                            "interrupted-result",
                        )
                    )
                    # Process exit was saved, but no terminal result was published.
                    assert remote.abandon_exited_job(req).state == "failed"
                    # The explicitly started command owns remote result collection;
                    # GET observes it without creating a resume/dispatch command.
                    failed = settled(run_id)
                    assert failed["run"]["status"] == "failed"
                    old = repo.load(run_id).dag.node_states["shape"].current()
                    assert old.error_code == "SERVICE_RESULT_ABANDONED"
                    assert old.remote_result is not None
                    repo.verify_reference_closure(old.remote_result)
                    post(
                        f"/api/runs/{run_id}/retry",
                        {
                            "node_id": "shape",
                            "expected_revision": failed["run"]["dag"]["revision"],
                        },
                    )
                    waiting = queued(run_id, previous_key=original_key)
                    current = repo.load(run_id).dag.node_states["shape"].current()
                    assert current.remote_binding.submission_key != original_key
                    req = remote.request_for(current.remote_binding.submission_key)
                    assert remote.lookup(req).state == "queued"
                assert execute_service_job(remote, req, handler).state == "succeeded"
                # Start/retry already authorized the continuation loop. It collects
                # this result and runs downstream without a second resume request.
                completed = settled(run_id)
                assert completed["run"]["status"] == "succeeded"
                assert dispatched == [run_id] * (2 if abandon_first else 1)
                jobs_after_completion = remote.list_jobs()["jobs"]
                assert len(jobs_after_completion) == (2 if abandon_first else 1)
                assert all(
                    len(s["attempts"]) == (2 if abandon_first and key == "shape" else 1)
                    for key, s in completed["run"]["dag"]["node_states"].items()
                )
                with urlopen(
                    base + f"/api/runs/{run_id}/outputs/publish/glb", timeout=10
                ) as response:
                    assert response.headers["Content-Type"] == "model/gltf-binary"
                    scene = trimesh.load(
                        io.BytesIO(response.read()), file_type="glb", force="scene"
                    )
                    assert sum(len(g.faces) for g in scene.geometry.values()) > 0
                report_path = f"/api/runs/{run_id}/outputs/quality/report"
                assert any(output["url"] == report_path for output in completed["outputs"])
                reference = get(f"/api/runs/{run_id}/references/quality/report")
                assert (
                    reference["reference"]
                    == completed["run"]["dag"]["node_states"]["quality"]["attempts"][0]["outputs"][
                        "report"
                    ]
                )
                assert reference["source_run_id"] == run_id
                assert reference["kind"] == "quality_report"
                assert reference["frame_id"] is None
                assert reference["unit"] is None
                mesh_reference = get(f"/api/runs/{run_id}/references/canonical/mesh")
                mesh_identity = repo.store.get_manifest(
                    mesh_reference["reference"]["artifact_id"]
                ).identity
                assert mesh_reference["frame_id"] == mesh_identity.identity_metadata["frame_id"]
                assert mesh_reference["unit"] == mesh_identity.identity_metadata["unit"]
                report = get(report_path)
                assert report["profile"] == "geometry-v1"
                assert report["overall_status"] in {"pass", "warn"}
                for node in ("shape", "canonical"):
                    assert any(
                        output["node_id"] == node and output["kind"] == "triangle_mesh"
                        for output in completed["outputs"]
                    )
                    facts = get(f"/api/runs/{run_id}/appearance/{node}/mesh")
                    assert facts["primitive_count"] > 0
                    with urlopen(base + f"/api/runs/{run_id}/outputs/{node}/mesh") as response:
                        assert response.read(4) == b"glTF"

                if abandon_first:
                    history = repo.load(run_id).dag.node_states["shape"].attempts
                    assert history[0] == old
                    assert remote.lookup(remote.request_for(original_key)).state == "failed"
                    assert remote.list_jobs()["jobs"] == [
                        {"job_id": req.submission_key, "state": "succeeded", "error": None},
                        {
                            "job_id": original_key,
                            "state": "failed",
                            "error": {
                                "code": "SERVICE_RESULT_ABANDONED",
                                "detail": (
                                    "Operator explicitly abandoned unpublished result "
                                    "after confirmed process exit"
                                ),
                            },
                        },
                    ]
                release = get(f"/api/runs/{run_id}/outputs/publish/release")
                assert release["export_profile"] == "gltf2-v1"
                archive_url = base + f"/api/runs/{run_id}/archives/publish/release"
                with urlopen(archive_url) as response:
                    assert response.headers["Content-Type"] == "application/zip"
                    archived_bytes = response.read()
                with zipfile.ZipFile(io.BytesIO(archived_bytes)) as archive:
                    assert set(archive.namelist()) == {
                        "asset.json",
                        "release.json",
                        *release["files"],
                    }
                    assert json.loads(archive.read("release.json")) == release
                    for name, reference in release["files"].items():
                        from assets_generator.models import ArtifactRef

                        assert (
                            archive.read(name)
                            == repo.store.blob_path(ArtifactRef(**reference)).read_bytes()
                        )
                assert get(f"/api/runs/{run_id}")["run"]["dag"] == completed["run"]["dag"]
                assert post("/api/runs", creation)["run"]["dag"] == completed["run"]["dag"]
                # Output/status GETs and idempotent creation never dispatch a new
                # command or submit another remote inference job.
                assert dispatched == [run_id] * (2 if abandon_first else 1)
                assert remote.list_jobs()["jobs"] == jobs_after_completion
                from urllib.error import HTTPError

                missing = repo.store.blob_path(ArtifactRef(**next(iter(release["files"].values()))))
                missing.unlink()
                with pytest.raises(HTTPError) as rejected:
                    urlopen(archive_url)
                assert rejected.value.code == 400
                assert not missing.exists()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                execution.close()
