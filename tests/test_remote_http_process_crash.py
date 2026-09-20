"""A killed HTTP listener must not erase an independently executing job."""

import json
import selectors
import subprocess
import sys
import threading
from contextlib import closing

import pytest
from test_dag_remote_execution import setup

from assets_generator.contracts import ContractError
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.remote_protocol import RemoteIdentity
from assets_generator.remote_service_store import RemoteServiceStore
from assets_generator.remote_service_worker import ServiceOutput, execute_service_job

SERVER = """
import json, sys
from pathlib import Path
from assets_generator.remote_protocol import RemoteIdentity
from assets_generator.remote_service_store import RemoteServiceStore
from assets_generator.remote_service_http import create_remote_server
store = RemoteServiceStore(Path(sys.argv[1]), RemoteIdentity('service', 'sha256:'+'c'*64))
server = create_remote_server(store, port=int(sys.argv[2]))
print(json.dumps({'port': server.server_port}), flush=True)
server.serve_forever()
"""


def test_listener_sigkill_keeps_active_job_and_parent_attempt(tmp_path):
    database = tmp_path / "service.sqlite"
    identity = RemoteIdentity("service", "sha256:" + "c" * 64)
    processes = []
    started, release = threading.Event(), threading.Event()
    results, calls = [], []
    worker = None

    def launch(port=0):
        process = subprocess.Popen(
            [sys.executable, "-u", "-c", SERVER, str(database), str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        processes.append(process)
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=10), "listener did not start"
        return process, json.loads(process.stdout.readline())["port"]

    def handler(request, service):
        calls.append(request.submission_key)
        started.set()
        assert release.wait(15), "test did not release independent executor"
        descriptor = json.loads(request.payload_json)["input_blobs"]["image"]
        return {
            "mesh": ServiceOutput(
                service.get_blob(descriptor["identity"]["blob_digest"]), "image/png"
            )
        }

    try:
        listener, port = launch()
        store, registry, local, plan, source, _ = setup(tmp_path, f"http://127.0.0.1:{port}")
        with closing(RemoteServiceStore(database, identity)) as service:
            with DagRepository(store, tmp_path / "core") as repo:
                engine = DagEngine(repo, registry)
                run = engine.drain(engine.create(plan, {"source": source}).run_id)
                binding = run.dag.node_states["B"].current().remote_binding
                request = service.request_for(binding.submission_key)

                def execute():
                    with closing(RemoteServiceStore(database, identity)) as worker_store:
                        results.append(execute_service_job(worker_store, request, handler))

                worker = threading.Thread(target=execute)
                worker.start()
                assert started.wait(10)
                assert service.lookup(request).state == "running"
                listener.kill()
                assert listener.wait(10) == -9

                offline = engine.drain(run.run_id)
                attempt = offline.dag.node_states["B"].current()
                assert offline.dag.node_states["B"].status == "running"
                assert attempt.error_code == "remote_transport_unknown"
                assert attempt.remote_binding == binding
                assert len(offline.dag.node_states["B"].attempts) == 1
                with pytest.raises(ContractError):
                    engine.retry(run.run_id, "B", offline.dag.revision)
                assert service.lookup(request).state == "running"
                assert calls == [request.submission_key]

                listener, _ = launch(port)
                resumed = engine.drain(run.run_id)
                assert resumed.dag.node_states["B"].current().remote_binding == binding
                assert len(resumed.dag.node_states["B"].attempts) == 1
                release.set()
                worker.join(10)
                assert not worker.is_alive()
                assert results[0].state == "succeeded"
                completed = engine.drain(run.run_id)
                assert completed.status == "succeeded"
                assert all(len(state.attempts) == 1 for state in completed.dag.node_states.values())
                assert calls == [request.submission_key]
                assert [node for node, _ in local.calls] == ["A", "C", "D"]
                assert len(service.list_jobs()["jobs"]) == 1
                listener.kill()
                listener.wait(10)
                assert engine.recover(run.run_id).dag.node_states == completed.dag.node_states
    finally:
        release.set()
        if worker is not None:
            worker.join(10)
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
