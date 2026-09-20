"""DAG integration against the durable service, without an in-memory job fixture."""

import io
import json

from PIL import Image, ImageOps
from test_dag_remote_execution import setup
from test_remote_service_http import serve

from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.remote_protocol import RemoteIdentity
from assets_generator.remote_service_worker import ServiceOutput, execute_service_job


def test_diamond_with_durable_service_restart_and_offline_recovery(tmp_path):
    database = tmp_path / "service.sqlite"
    directory = tmp_path / "core"
    calls = []
    identity = RemoteIdentity("service", "sha256:" + "c" * 64)

    def invert(request, service):
        calls.append(request.submission_key)
        descriptor = json.loads(request.payload_json)["input_blobs"]["image"]
        data = service.get_blob(descriptor["identity"]["blob_digest"])
        with Image.open(io.BytesIO(data)) as image:
            buffer = io.BytesIO()
            ImageOps.invert(image.convert("RGB")).save(buffer, format="PNG")
        return {"mesh": ServiceOutput(buffer.getvalue(), "image/png")}

    with serve(database, identity=identity) as (service, client, port):
        store, registry, local, plan, source, _ = setup(tmp_path, client.endpoint)
        with DagRepository(store, directory) as repo:
            engine = DagEngine(repo, registry)
            run = engine.drain(engine.create(plan, {"source": source}).run_id)
            assert run.dag.node_states["B"].status == "running"
            assert run.dag.node_states["C"].status == "succeeded"
            assert run.dag.node_states["D"].status == "pending"
            binding = run.dag.node_states["B"].current().remote_binding
            request = service.request_for(binding.submission_key)
            assert request == binding.request()
            assert service.lookup(request).state == "queued"
    # Reopen BOTH the actual HTTP service/storage and the Core repository.
    with serve(database, port, identity) as (service, client, _):
        assert service.lookup(request).state == "queued"
        with DagRepository(store, directory) as repo:
            engine = DagEngine(repo, registry)
            resumed = engine.drain(run.run_id)
            assert resumed.dag.node_states["B"].current().remote_binding == binding
            assert len(resumed.dag.node_states["B"].attempts) == 1
            assert execute_service_job(service, request, invert).state == "succeeded"
    # Restart after terminal commit, before the Core has observed that terminal.
    with serve(database, port, identity) as (_, client, _):
        with DagRepository(store, directory) as repo:
            completed = DagEngine(repo, registry).drain(run.run_id)
            assert completed.status == "succeeded"
            assert all(len(node.attempts) == 1 for node in completed.dag.node_states.values())
            output = completed.dag.node_states["B"].current().outputs["image"]
            with Image.open(store.blob_path(output)) as image:
                assert image.getpixel((0, 0)) == (0, 255, 255)
            assert output != source
            assert calls == [binding.submission_key]
            assert [node for node, _ in local.calls] == ["A", "C", "D"]
            repo.verify_reference_closure(completed.dag.node_states["B"].current().remote_result)
    # No HTTP listener exists; completed evidence must remain locally verifiable.
    with DagRepository(store, directory) as repo:
        restored = DagEngine(repo, registry).recover(run.run_id)
        assert restored.dag.node_states == completed.dag.node_states
