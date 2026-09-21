"""Two real HTTP listeners, CPU mesh substitute; no model/GPU acceptance claim."""

import io
import json
import socket
import threading
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest
import trimesh
from test_sam3d_bridge import setup as bridge_setup  # noqa: F401

from assets_generator.dag_adapters import AdapterRegistry
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.dag_remote_profiles import register_remote_shape_profiles
from assets_generator.models import ArtifactRef
from assets_generator.pipeline import compile_pipeline, load_default_operator_specs, load_pipeline
from assets_generator.remote_service_http import create_remote_server
from assets_generator.sam3d_bridge import Sam3DBridge
from assets_generator.sam3d_http import Sam3DClient, Sam3DUnknown
from assets_generator.sam3d_service_store import Sam3DServiceStore
from assets_generator.serialization import canonical_json_bytes


@contextmanager
def listening(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize("drop_response", [False, True])
@pytest.mark.parametrize("appearance", ["vertex", "texture"])
def test_http_masked_shape_release_and_restart(bridge_setup, tmp_path, drop_response, appearance):  # noqa: F811
    unused, _, substitute, _, store = bridge_setup
    substitute.drop = False

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, data, media="application/json", code=200):
            if not isinstance(data, bytes):
                data = canonical_json_bytes(data)
            self.send_response(code)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def receipt(self):
            r = substitute.job
            return {
                "id": r.job_id,
                "submission_key": r.submission_key,
                "request_digest": r.request_digest,
                "backend_digest": r.backend_digest,
                "status_url": "/api/v1/jobs/job-1",
                "status": substitute.status,
            }

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            parsed = BytesParser(policy=policy.default).parsebytes(
                ("Content-Type: " + self.headers["Content-Type"] + "\r\n\r\n").encode() + body
            )
            fields = {
                p.get_param("name", header="content-disposition"): p.get_payload(decode=True)
                for p in parsed.iter_parts()
            }
            substitute.submit_once(
                fields["image"],
                fields["mask"],
                json.loads(fields["options"]),
                submission_key=fields["submission_key"].decode(),
                backend_digest=fields["backend_digest"].decode(),
            )
            if drop_response:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
            else:
                self.send(self.receipt(), code=202)

        def do_GET(self):
            if self.path == "/api/v1/capabilities":
                self.send(substitute.capabilities())
            elif "/by-key/" in self.path:
                self.send(self.receipt())
            elif "/files/" in self.path:
                self.send(substitute.files[self.path.rsplit("/", 1)[1]], "application/octet-stream")
            else:
                status = substitute.query("job-1")
                if substitute.status == "completed":
                    status["files"] = {n: "/api/v1/jobs/job-1/files/" + n for n in substitute.files}
                self.send(status)

    with listening(ThreadingHTTPServer(("127.0.0.1", 0), Handler)) as upstream:
        bridge = Sam3DBridge(Sam3DClient(upstream, "test-secret"), substitute.deployment)
        identity = bridge.identity("sam3d-bridge")
        service_path = tmp_path / "bridge.sqlite"
        owner = Sam3DServiceStore(service_path, identity)
        owner.initialize_bridge()
        with listening(create_remote_server(owner)) as endpoint:
            registry = AdapterRegistry()
            register_remote_shape_profiles(
                registry,
                {
                    "default_profile": "sam3d",
                    "profiles": {
                        "sam3d": {
                            "operator": "masked_shape_generation@1",
                            "endpoint": endpoint,
                            "service_id": identity.service_id,
                            "backend_digest": identity.backend_digest,
                            "upstream_digest": bridge.backend_digest,
                        }
                    },
                },
            )
            plan = registry.bind_plan(
                compile_pipeline(
                    load_pipeline(Path("pipelines/sam3d_masked_shape_v1.yaml")),
                    load_default_operator_specs(),
                    require_explicit_joins=True,
                )
            )
            inputs = {}
            from test_sam3d_bridge import png

            for name, kind, mode, color in (
                ("image", "rgb_image", "RGB", "red"),
                ("mask", "binary_mask", "L", 255),
            ):
                inputs[name] = store.persist_bytes(
                    png(mode, color),
                    kind=kind,
                    schema_name="png",
                    schema_version="1.0",
                    identity_metadata={"media_type": "image/png"},
                )
            repo_path = tmp_path / "dag"
            with DagRepository(store, repo_path) as repo:
                engine = DagEngine(repo, registry)
                run = engine.drain(engine.create(plan, inputs).run_id)
                attempt = run.dag.node_states["shape"].current()
                assert attempt.status == "running"
                request = owner.request_for(attempt.remote_binding.submission_key)
                if drop_response:
                    with pytest.raises(Sam3DUnknown):
                        bridge.start_next(owner, store)
                else:
                    assert bridge.start_next(owner, store).state == "running"
            # Reopen both durable controllers before completion; no second POST.
            reopened = Sam3DServiceStore(service_path, identity)
            substitute.complete()
            # Use an asymmetric box, so an unintended rotation is observable.
            mesh = trimesh.creation.box(extents=[1, 2, 3])
            if appearance == "vertex":
                mesh.visual.vertex_colors = [20, 50, 100, 255]
            else:
                from PIL import Image

                mesh.visual = trimesh.visual.texture.TextureVisuals(
                    uv=np.zeros((len(mesh.vertices), 2)),
                    material=trimesh.visual.material.PBRMaterial(
                        baseColorTexture=Image.new("RGB", (2, 2), (20, 50, 100))
                    ),
                )
            from assets_generator.serialization import sha256_bytes

            substitute.files["model.glb"] = mesh.export(file_type="glb")
            descriptor = {
                "sha256": sha256_bytes(substitute.files["model.glb"]),
                "byte_length": len(substitute.files["model.glb"]),
            }
            evidence = json.loads(substitute.files["evidence.json"])
            evidence["files"]["model.glb"] = descriptor
            substitute.files["evidence.json"] = canonical_json_bytes(evidence)
            substitute.result["file_descriptors"]["model.glb"] = descriptor
            substitute.result["file_descriptors"]["evidence.json"] = {
                "sha256": sha256_bytes(substitute.files["evidence.json"]),
                "byte_length": len(substitute.files["evidence.json"]),
            }
            assert bridge.recover(reopened, request, store).state == "succeeded"
            with DagRepository(store, repo_path) as repo:
                engine = DagEngine(repo, registry)
                completed = engine.recover(run.run_id)
                completed = engine.drain(run.run_id)
                assert completed.status == "succeeded", completed
                assert substitute.posts == 1
                assert all(len(s.attempts) == 1 for s in completed.dag.node_states.values())
                published = completed.dag.node_states["publish"].current()
                repo.verify_reference_closure(published.outputs["release"])
                release = store.read_structured(published.outputs["release"])
                assembly_refs = [
                    ArtifactRef(**v)
                    for k, v in release["files"].items()
                    if k.startswith("provenance/assembly-")
                ]
                assert len(assembly_refs) == 1
                provenance = store.read_structured(assembly_refs[0])
                shape = completed.dag.node_states["shape"].current()
                for key in ("sam3d_evidence", "actual_mask"):
                    assert shape.outputs[key].artifact_id in provenance["derived_from_artifact_ids"]
                native_bytes = store.blob_path(shape.outputs["mesh"]).read_bytes()
                assert (
                    native_bytes == substitute.files["model.glb"]
                )  # No duplicate native rotation.
                exported = trimesh.load(
                    io.BytesIO(store.blob_path(published.outputs["glb"]).read_bytes()),
                    file_type="glb",
                    force="scene",
                )
                assert all(g.visual.kind == appearance for g in exported.geometry.values())
                for geometry in exported.geometry.values():
                    if appearance == "vertex":
                        assert np.all(geometry.visual.vertex_colors == [20, 50, 100, 255])
                    else:
                        assert np.all(
                            np.asarray(geometry.visual.material.baseColorTexture) == [20, 50, 100]
                        )
                canonical = completed.dag.node_states["canonical"].current()
                native_scene = trimesh.load(
                    io.BytesIO(native_bytes), file_type="glb", force="scene"
                )
                from assets_generator.mesh_io import scene_vertices

                vertices = scene_vertices(native_scene)
                matrix = np.asarray(canonical.outputs["transform"].value["matrix"])
                expected = trimesh.transform_points(vertices, matrix)
                expected = expected[:, [1, 2, 0]]  # canonical Z-up -> GLB Y-up once
                actual_vertices = scene_vertices(exported)
                assert np.allclose(
                    np.sort(expected, axis=0), np.sort(actual_vertices, axis=0), atol=1e-6
                )
                # A different valid evidence Artifact must not be silently attached to this mesh.
                from assets_generator.contracts import ContractError
                from assets_generator.dag_asset_assembly import validate_shape_asset_inputs

                wrong = store.persist_bytes(
                    b"{}",
                    kind="remote_job_result",
                    schema_name="Sam3DResult",
                    schema_version="1.0",
                    identity_metadata={"media_type": "application/json"},
                )
                assembled = completed.dag.node_states["assemble"].current()
                with pytest.raises(ContractError, match="lineage"):
                    validate_shape_asset_inputs(
                        store, {**assembled.resolved_inputs, "sam3d_evidence": wrong}, run.run_id
                    )
                restored = engine.recover(run.run_id)
                assert restored.dag.node_states == completed.dag.node_states
                assert substitute.posts == 1
            reopened.close()
        owner.close()
