"""Real editor HTTP -> real remote HTTP -> CPU shape -> published asset."""

import io
import json
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

import trimesh
import yaml
from PIL import Image
from test_remote_http import request
from test_remote_service_http import serve
from test_remote_shape_service import Backend

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.dag_adapters import AdapterRegistry
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.dag_remote_profiles import register_remote_shape_profiles
from assets_generator.node_editor import DraftEditor, create_editor_server
from assets_generator.node_editor_execution import NodeEditorExecution
from assets_generator.remote_service_worker import execute_service_job
from assets_generator.remote_shape_service import ShapeServiceHandler


def test_remote_asset_chain_through_editor_http(tmp_path):
    with serve(tmp_path / "service.sqlite") as (remote, client, _):
        identity = request().identity
        registry = AdapterRegistry()
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

            def settled(run_id):
                deadline = time.monotonic() + 30
                while True:
                    snapshot = get(f"/api/runs/{run_id}")
                    if not snapshot["busy"]:
                        return snapshot
                    assert time.monotonic() < deadline
                    time.sleep(0.05)

            try:
                graph = yaml.safe_load(Path("pipelines/remote_shape_asset_v1.yaml").read_text())
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
                started = post("/api/runs", creation)
                run_id = started["run"]["run_id"]
                waiting = settled(run_id)
                assert waiting["run"]["status"] == "running"
                attempt = repo.load(run_id).dag.node_states["shape"].current()
                req = remote.request_for(attempt.remote_binding.submission_key)
                assert remote.lookup(req).state == "queued"
                assert post("/api/runs", creation)["run"]["run_id"] == run_id
                handler = ShapeServiceHandler(
                    identity, tmp_path / "work", Backend, lambda: identity
                )
                assert execute_service_job(remote, req, handler).state == "succeeded"
                # Polling alone cannot consume the remote result or execute downstream nodes.
                unchanged = get(f"/api/runs/{run_id}")
                assert unchanged["run"]["dag"] == waiting["run"]["dag"]
                post(
                    f"/api/runs/{run_id}/resume",
                    {"expected_revision": waiting["run"]["dag"]["revision"]},
                )
                completed = settled(run_id)
                assert completed["run"]["status"] == "succeeded"
                assert all(
                    len(s["attempts"]) == 1 for s in completed["run"]["dag"]["node_states"].values()
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
                report = get(report_path)
                assert report["profile"] == "geometry-v1"
                assert report["overall_status"] in {"pass", "warn"}
                assert not any(output["node_id"] == "shape" for output in completed["outputs"])
                release = get(f"/api/runs/{run_id}/outputs/publish/release")
                assert release["export_profile"] == "gltf2-v1"
                assert post("/api/runs", creation)["run"]["dag"] == completed["run"]["dag"]
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                execution.close()
