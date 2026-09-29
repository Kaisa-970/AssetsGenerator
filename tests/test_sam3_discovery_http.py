"""SAM3's listener advertises a pinned capability without dispatching a model."""

import threading

from assets_generator.model_service_descriptor import detect_service
from assets_generator.remote_protocol import RemoteIdentity
from assets_generator.remote_service_http import create_remote_server
from assets_generator.remote_service_store import RemoteServiceStore
from assets_generator.sam3_text_cli import service_descriptor


def test_sam3_descriptor_http_preserves_existing_job_identity(tmp_path):
    identity = RemoteIdentity("sam3", "sha256:" + "a" * 64)
    owner = RemoteServiceStore(tmp_path / "jobs.sqlite", identity)
    server = create_remote_server(owner, descriptor=service_descriptor(identity))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = detect_service(f"http://127.0.0.1:{server.server_port}")
        descriptor = result["descriptor"]
        assert descriptor["backend_digest"] == identity.backend_digest
        capability = descriptor["capabilities"][0]
        assert capability["operator"] == "text_segmentation@2"
        assert capability["transport"] == "sam3_text_jobs@1"
        assert set(capability["parameter_schema"]["properties"]) == {"confidence"}
        assert capability["defaults"] == {"confidence": 0.5}
        assert owner.claim_next_queued() is None
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        owner.close()
