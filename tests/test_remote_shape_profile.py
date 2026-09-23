from dataclasses import replace
from pathlib import Path

import pytest
from test_workbench_profiles import _config

from assets_generator.errors import DeploymentIdentityError
from assets_generator.remote_protocol import RemoteRequest
from assets_generator.remote_service_process import ServiceProcessWorker
from assets_generator.remote_service_store import RemoteServiceStore
from assets_generator.remote_shape_profile import shape_handler_from_profile
from assets_generator.workbench_profiles import load_profiles


def test_profile_service_identity_and_worker_injection(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch)
    profile = load_profiles(config)["local-triposr"]
    handler = shape_handler_from_profile(profile, service_id="shape", workspace=tmp_path / "work")
    assert handler.verify_identity() == handler.identity
    backend = profile.shape_plan.backend_for("generate_shape", "shape_generation@1").implementation
    original_worker = backend.worker
    store = RemoteServiceStore(tmp_path / "service.sqlite", handler.identity)
    try:
        worker = ServiceProcessWorker(store, RemoteRequest.create(handler.identity, "job", {}))
        copied = handler.factory(worker)
        assert copied is not backend
        assert copied.worker is worker
        assert backend.worker is original_worker
        backend.mc_resolution += 1
        with pytest.raises(DeploymentIdentityError, match="resources changed"):
            handler.verify_identity()
    finally:
        store.close()


def test_profile_changes_rejected_before_factory(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch)
    profile = load_profiles(config)["local-triposr"]
    with pytest.raises(ValueError, match="verified real"):
        shape_handler_from_profile(
            replace(profile, test_only=True), service_id="shape", workspace=tmp_path
        )
    handler = shape_handler_from_profile(profile, service_id="shape", workspace=tmp_path)
    Path(config["profiles"]["local-triposr"]["model"], "model.ckpt").write_bytes(b"changed")
    with pytest.raises(DeploymentIdentityError, match="changed"):
        handler.verify_identity()


def test_shape_only_loader_never_constructs_sam(tmp_path, monkeypatch):
    from assets_generator import workbench_profiles

    config = _config(tmp_path, monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError("SAM must not be loaded")

    monkeypatch.setattr(workbench_profiles, "SAMInstanceProposer", forbidden)
    profile = workbench_profiles.load_shape_profiles({"profiles": config["profiles"]})[
        "local-triposr"
    ]
    handler = shape_handler_from_profile(profile, service_id="shape", workspace=tmp_path)
    assert handler.verify_identity() == handler.identity
    assert not hasattr(profile, "proposer")
    with pytest.raises(ValueError):
        workbench_profiles.load_shape_profiles(config)


def test_profile_rejects_backend_response_identity_mismatch(tmp_path, monkeypatch):
    from test_remote_shape_output import fixture

    from assets_generator.artifact_store import LocalArtifactStore

    profile = load_profiles(_config(tmp_path, monkeypatch))["local-triposr"]
    handler = shape_handler_from_profile(profile, service_id="shape", workspace=tmp_path)
    output = fixture(LocalArtifactStore(tmp_path / "store"))
    expected = {
        "backend": "triposr",
        "model_digest": profile.shape_identity["model"]["snapshot_digest"],
    }
    valid = replace(output, backend_metadata=expected)
    handler.validate_output_identity(valid)
    for metadata in (
        {},
        {**expected, "backend": "trellis2"},
        {**expected, "model_digest": "other"},
    ):
        with pytest.raises(ValueError, match="model identity"):
            handler.validate_output_identity(replace(output, backend_metadata=metadata))
