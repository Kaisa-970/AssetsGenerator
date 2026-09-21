import io
import json
from copy import deepcopy

import pytest
import trimesh
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.remote_protocol import RemoteRequest
from assets_generator.sam3d_bridge import Sam3DBridge
from assets_generator.sam3d_http import Sam3DLookup, Sam3DReceipt, Sam3DUnknown
from assets_generator.sam3d_service_store import Sam3DServiceStore
from assets_generator.serialization import canonical_json_bytes, sha256_bytes


class Upstream:
    endpoint = "http://127.0.0.1:7861"

    def __init__(self, deployment):
        self.deployment = deployment
        self.posts = 0
        self.downloads = 0
        self.drop = False
        self.status = "running"
        self.options = None
        self.job = None

    def capabilities(self):
        return {
            "protocol_version": "1.1",
            "deployment": self.deployment,
            "idempotency": {"persistent": True, "lookup_by_key": True},
        }

    def submit_once(self, image, mask, options, *, submission_key, backend_digest):
        self.posts += 1
        self.image, self.mask, self.options = image, mask, options
        request = {
            "schema": "sam3d-request@1",
            "image": sha256_bytes(image),
            "mask": sha256_bytes(mask),
            "points": None,
            "options": options,
        }
        self.request = request
        self.job = Sam3DReceipt(
            "job-1", submission_key, sha256_bytes(canonical_json_bytes(request)), backend_digest
        )
        if self.drop:
            raise Sam3DUnknown("response lost")
        return "job-1"

    def lookup(self, key, *, request_digest, backend_digest, expected_job_id=None):
        if self.job is None:
            return Sam3DLookup("not_found")
        assert key == self.job.submission_key
        assert request_digest == self.job.request_digest
        assert backend_digest == self.job.backend_digest
        if expected_job_id is not None:
            assert expected_job_id == self.job.job_id
        return Sam3DLookup("registered", self.job, self.status)

    def query(self, job_id):
        if self.status != "completed":
            return {
                "id": job_id,
                "status": self.status,
                "request_digest": self.job.request_digest,
                "deployment": self.deployment,
                "submission_key": self.job.submission_key,
            }
        return deepcopy(self.result)

    def complete(self):
        mesh = trimesh.creation.box()
        mesh.visual.vertex_colors = [20, 50, 100, 255]
        parameters = {**self.options, "mesh_status": "completed"}
        self.files = {
            "model.glb": mesh.export(file_type="glb"),
            "mask.png": self.mask,
            "parameters.json": canonical_json_bytes(parameters),
        }
        descriptors = {
            name: {"sha256": sha256_bytes(data), "byte_length": len(data)}
            for name, data in self.files.items()
        }
        evidence = {
            "schema": "sam3d-result@1",
            "request": self.request,
            "request_digest": self.job.request_digest,
            "deployment": self.deployment,
            "actual_parameters": parameters,
            "files": deepcopy(descriptors),
            "spatial": self.deployment["spatial"],
        }
        self.files["evidence.json"] = canonical_json_bytes(evidence)
        descriptors["evidence.json"] = {
            "sha256": sha256_bytes(self.files["evidence.json"]),
            "byte_length": len(self.files["evidence.json"]),
        }
        self.result = {
            "id": "job-1",
            "status": "completed",
            "file_descriptors": descriptors,
            "deployment": self.deployment,
            "request_digest": self.job.request_digest,
            "submission_key": self.job.submission_key,
        }
        self.status = "completed"

    def download(self, job_id, name):
        self.downloads += 1
        return self.files[name]


def png(mode, value):
    buffer = io.BytesIO()
    Image.new(mode, (4, 4), value).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def setup(tmp_path):
    deployment = {
        "backend_digest": "sha256:" + "a" * 64,
        "spatial": {
            "frame_id": "sam3d_glb",
            "handedness": "right",
            "up_axis": "+Y",
            "forward_axis": None,
            "unit": "relative",
            "metric_scale_verified": False,
            "mesh_row_vector_transform": [[1, 0, 0], [0, 0, -1], [0, 1, 0]],
        },
    }
    client = Upstream(deployment)
    bridge = Sam3DBridge(client, deployment)
    identity = bridge.identity("sam3d-bridge")
    owner = Sam3DServiceStore(tmp_path / "service.sqlite", identity)
    owner.initialize_bridge()
    image, mask = png("RGB", "red"), png("L", 255)
    for data in (image, mask):
        owner.put_blob(data, sha256_bytes(data))
    request = RemoteRequest.create(
        identity,
        "original-key",
        {
            "operation": "masked_shape_generation@1",
            "image_digest": sha256_bytes(image),
            "mask_digest": sha256_bytes(mask),
            "parameters": {},
            "backend_digest": deployment["backend_digest"],
        },
    )
    owner.submit(request)
    artifacts = LocalArtifactStore(tmp_path / "staging-artifacts")
    yield owner, request, client, bridge, artifacts
    owner.close()


def test_response_lost_reopen_lookup_publish_and_offline_recovery(setup, tmp_path):
    owner, request, client, bridge, artifacts = setup
    client.drop = True
    with pytest.raises(Sam3DUnknown):
        bridge.start_next(owner, artifacts)
    assert owner.lookup(request).state == "running"
    with pytest.raises(ValueError):
        bridge.start_next(owner, artifacts)
    reopened = Sam3DServiceStore(tmp_path / "service.sqlite", owner.identity)
    assert bridge.recover(reopened, request, artifacts).state == "running"
    client.complete()
    result = bridge.recover(reopened, request, artifacts)
    assert result.state == "succeeded" and client.posts == 1
    downloads = client.downloads
    client.job = None
    assert bridge.recover(reopened, request, artifacts) == result
    assert client.downloads == downloads
    blobs = {
        item["output_id"]: reopened.get_blob(item["blob_digest"])
        for item in json.loads(result.result_json)["outputs"]
    }
    mesh = trimesh.load(io.BytesIO(blobs["mesh"]), file_type="glb", force="scene")
    assert {geometry.visual.kind for geometry in mesh.geometry.values()} == {"vertex"}
    native = json.loads(blobs["shape_metadata"])["native_frame"]["value"]
    assert native["unit"] == "relative_unit" and native["up_axis"] == "+Y"
    reopened.close()


def test_missing_ownership_never_recreated(setup):
    owner, request, client, bridge, artifacts = setup
    bridge.start_next(owner, artifacts)
    owner.db.execute("DELETE FROM sam3d_jobs")
    with pytest.raises(ValueError, match="ownership missing"):
        bridge.recover(owner, request, artifacts)
    assert client.posts == 1


def test_missing_success_blob_never_downloaded_again(setup):
    owner, request, client, bridge, artifacts = setup
    bridge.start_next(owner, artifacts)
    client.complete()
    result = bridge.recover(owner, request, artifacts)
    digest = json.loads(result.result_json)["outputs"][0]["blob_digest"]
    owner.db.execute("DELETE FROM blobs WHERE digest=?", (digest,))
    downloads = client.downloads
    with pytest.raises(ValueError, match="missing/corrupt"):
        bridge.recover(owner, request, artifacts)
    assert client.downloads == downloads


def test_terminal_descriptors_fixed_before_download(setup):
    owner, request, client, bridge, artifacts = setup
    bridge.start_next(owner, artifacts)
    client.complete()
    client.files["model.glb"] += b"tampered"
    with pytest.raises(Sam3DUnknown, match="changed"):
        bridge.recover(owner, request, artifacts)
    assert owner.bridge_record(request)["completion"] is not None
    assert owner.lookup(request).state == "running"
    assert client.posts == 1


@pytest.mark.parametrize("field", ["mask", "parameters", "deployment", "spatial"])
def test_semantic_result_mismatch_rejected_even_with_matching_file_digests(setup, field):
    owner, request, client, bridge, artifacts = setup
    bridge.start_next(owner, artifacts)
    client.complete()
    evidence = json.loads(client.files["evidence.json"])
    if field == "mask":
        client.files["mask.png"] = png("L", 0)
    elif field == "parameters":
        parameters = {**client.options, "seed": 111, "mesh_status": "completed"}
        client.files["parameters.json"] = canonical_json_bytes(parameters)
        evidence["actual_parameters"] = parameters
    elif field == "deployment":
        evidence["deployment"] = {"backend_digest": "wrong"}
    else:
        evidence["spatial"] = {"unit": "meter"}
    for name in ("model.glb", "mask.png", "parameters.json"):
        descriptor = {
            "sha256": sha256_bytes(client.files[name]),
            "byte_length": len(client.files[name]),
        }
        evidence["files"][name] = descriptor
        client.result["file_descriptors"][name] = descriptor
    client.files["evidence.json"] = canonical_json_bytes(evidence)
    client.result["file_descriptors"]["evidence.json"] = {
        "sha256": sha256_bytes(client.files["evidence.json"]),
        "byte_length": len(client.files["evidence.json"]),
    }
    with pytest.raises(ValueError):
        bridge.recover(owner, request, artifacts)
    assert owner.lookup(request).state == "running"


def test_publication_transaction_rollback_does_not_leave_partial_blobs(setup, monkeypatch):
    owner, request, client, bridge, artifacts = setup
    bridge.start_next(owner, artifacts)
    client.complete()
    before = owner.db.execute("SELECT digest FROM blobs ORDER BY digest").fetchall()
    original = owner.put_blob
    calls = 0

    def fail(data, digest):
        nonlocal calls
        original(data, digest)
        calls += 1
        if calls == 2:
            raise OSError("publication interrupted")

    monkeypatch.setattr(owner, "put_blob", fail)
    with pytest.raises(OSError):
        bridge.recover(owner, request, artifacts)
    assert owner.db.execute("SELECT digest FROM blobs ORDER BY digest").fetchall() == before
    monkeypatch.setattr(owner, "put_blob", original)
    assert bridge.recover(owner, request, artifacts).state == "succeeded"
    assert client.posts == 1


def test_crash_after_authorization_before_post_never_submits_on_recovery(setup, monkeypatch):
    owner, request, client, bridge, artifacts = setup

    def crash(*args, **kwargs):
        raise OSError("before POST")

    monkeypatch.setattr(client, "submit_once", crash)
    with pytest.raises(OSError):
        bridge.start_next(owner, artifacts)
    assert owner.bridge_record(request)["receipt"] is None
    with pytest.raises(Sam3DUnknown, match="missing/expired"):
        bridge.recover(owner, request, artifacts)
    assert owner.lookup(request).state == "running" and client.posts == 0


def test_upstream_failure_keeps_evidence_and_never_downloads(setup):
    owner, request, client, bridge, artifacts = setup
    bridge.start_next(owner, artifacts)
    client.status = "failed"
    result = bridge.recover(owner, request, artifacts)
    assert result.state == "failed"
    assert owner.bridge_record(request)["completion"]["status"]["status"] == "failed"
    assert client.downloads == 0 and client.posts == 1
    assert bridge.recover(owner, request, artifacts) == result


def test_changed_profile_rejected_before_claim(setup):
    owner, request, client, bridge, artifacts = setup
    changed = deepcopy(client.deployment)
    changed["other"] = "deployment changed"
    other = Sam3DBridge(Upstream(changed), changed)
    with pytest.raises(ValueError, match="identity mismatch"):
        other.start_next(owner, artifacts)
    assert client.posts == 0
    assert owner.lookup(request).state == "queued"


def test_expired_original_never_submitted_again(setup, monkeypatch):
    owner, request, client, bridge, artifacts = setup
    bridge.start_next(owner, artifacts)
    monkeypatch.setattr(
        client, "lookup", lambda *a, **kw: Sam3DLookup("expired_or_missing", client.job)
    )
    with pytest.raises(Sam3DUnknown, match="missing/expired"):
        bridge.recover(owner, request, artifacts)
    assert client.posts == 1 and client.downloads == 0


def test_service_cli_initialization_and_missing_database(setup, tmp_path, monkeypatch, capsys):
    import sys

    from assets_generator.sam3d_service_cli import main

    owner, request, client, bridge, artifacts = setup
    deployment = tmp_path / "deployment.json"
    deployment.write_bytes(canonical_json_bytes(client.deployment))
    directory = tmp_path / "cli-service"
    common = [
        "--deployment",
        str(deployment),
        "--endpoint",
        client.endpoint,
        "--directory",
        str(directory),
    ]
    monkeypatch.setattr(sys, "argv", ["sam3d-service", "init", *common])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["backend_digest"] == owner.identity.backend_digest
    monkeypatch.setattr(sys, "argv", ["sam3d-service", "list", *common])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["jobs"] == []
    (directory / "service.sqlite").unlink()
    with pytest.raises(SystemExit):
        main()
    assert not (directory / "service.sqlite").exists()


def test_success_with_missing_terminal_evidence_is_not_accepted(setup):
    owner, request, client, bridge, artifacts = setup
    bridge.start_next(owner, artifacts)
    client.complete()
    bridge.recover(owner, request, artifacts)
    owner.db.execute("UPDATE sam3d_jobs SET completion=NULL")
    downloads = client.downloads
    with pytest.raises(ValueError, match="terminal ownership"):
        bridge.recover(owner, request, artifacts)
    assert client.downloads == downloads and client.posts == 1


@pytest.mark.parametrize("fault", ["payload", "mask", "parameters"])
def test_invalid_request_is_terminal_and_next_valid_request_runs(setup, fault):
    owner, request, client, bridge, artifacts = setup
    payload = json.loads(request.payload_json)
    if fault == "payload":
        payload.pop("operation")
    elif fault == "mask":
        data = png("L", 0)
        owner.put_blob(data, sha256_bytes(data))
        payload["mask_digest"] = sha256_bytes(data)
    else:
        payload["parameters"] = {"seed": "bad"}
    # Replace the fixture's not-yet-executed queued input with an invalid request.
    owner.db.execute("DELETE FROM jobs")
    invalid = RemoteRequest.create(owner.identity, "invalid", payload)
    owner.submit(invalid)
    owner.submit(request)
    failed = bridge.start_next(owner, artifacts)
    assert failed.state == "failed" and client.posts == 0
    assert json.loads(failed.error_json)["code"] == "SAM3D_INVALID_INPUT"
    assert bridge.recover(owner, invalid, artifacts) == failed
    assert bridge.start_next(owner, artifacts).state == "running"
    assert client.posts == 1


def test_crash_between_claim_and_authorization_rolls_back(setup, monkeypatch, tmp_path):
    owner, request, client, bridge, artifacts = setup
    original = bridge._intent

    def crash(*args):
        # Claim is visible inside the transaction only.
        assert owner.lookup(request).state == "running"
        raise KeyboardInterrupt()

    monkeypatch.setattr(bridge, "_intent", crash)
    with pytest.raises(KeyboardInterrupt):
        bridge.start_next(owner, artifacts)
    reopened = Sam3DServiceStore(tmp_path / "service.sqlite", owner.identity)
    assert reopened.lookup(request).state == "queued"
    assert reopened.db.execute("SELECT COUNT(*) FROM sam3d_jobs").fetchone()[0] == 0
    monkeypatch.setattr(bridge, "_intent", original)
    assert bridge.start_next(reopened, artifacts).state == "running"
    assert client.posts == 1
    reopened.close()


def test_explicit_rejection_releases_queue_but_conflict_does_not(setup, monkeypatch):
    from assets_generator.sam3d_http import Sam3DConflict, Sam3DRejected

    owner, request, client, bridge, artifacts = setup
    original = client.submit_once

    def reject(*args, **kwargs):
        raise Sam3DRejected("HTTP 429")

    monkeypatch.setattr(client, "submit_once", reject)
    failed = bridge.start_next(owner, artifacts)
    assert failed.state == "failed"
    assert json.loads(failed.error_json)["code"] == "SAM3D_REJECTED"
    assert bridge.recover(owner, request, artifacts) == failed
    other = RemoteRequest.create(owner.identity, "second", json.loads(request.payload_json))
    owner.submit(other)
    monkeypatch.setattr(client, "submit_once", original)
    assert bridge.start_next(owner, artifacts).state == "running"
    client.status = "failed"
    bridge.recover(owner, other, artifacts)
    third = RemoteRequest.create(owner.identity, "third", json.loads(request.payload_json))
    owner.submit(third)

    def conflict(*args, **kwargs):
        raise Sam3DConflict("conflict")

    monkeypatch.setattr(client, "submit_once", conflict)
    with pytest.raises(Sam3DUnknown, match="conflict"):
        bridge.start_next(owner, artifacts)
    assert owner.lookup(third).state == "running"
    with pytest.raises(ValueError, match="blocked"):
        bridge.start_next(owner, artifacts)
