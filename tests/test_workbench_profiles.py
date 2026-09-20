from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from assets_generator import workbench_profiles as profiles


def _config(tmp_path, monkeypatch):
    interpreter = tmp_path / "venv/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(sys.executable)
    checkpoint = tmp_path / "sam.pth"
    checkpoint.write_bytes(b"sam fixture")
    repo = tmp_path / "repo"
    repo.mkdir()
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.yaml").write_text("model: fixture")
    (model / "model.ckpt").write_bytes(b"weights")
    evidence = tmp_path / "frame.json"
    evidence.write_text("{}")
    monkeypatch.setattr(
        profiles,
        "_environment_identity",
        lambda python, packages: {"environment_digest": "sha256:env", "packages": list(packages)},
    )
    monkeypatch.setattr(
        profiles,
        "backend_environment_identity",
        lambda python: {"environment_digest": "sha256:triposr"},
    )
    monkeypatch.setattr(
        profiles,
        "backend_source_identity",
        lambda repo: {
            "path": str(repo),
            "source_digest": "sha256:source",
            "revision": "abc",
            "dirty": False,
        },
    )
    monkeypatch.setattr(
        profiles.TripoSRBackend,
        "_frame_validation_identity",
        lambda self: {"evidence_digest": profiles._digest(self.frame_validation)},
    )
    return {
        "sam": {"python": str(interpreter), "checkpoint": str(checkpoint), "device": "cpu"},
        "profiles": {
            "local-triposr": {
                "backend": "triposr",
                "python": str(interpreter),
                "repo": str(repo),
                "model": str(model),
                "frame_validation": str(evidence),
            }
        },
    }


def test_load_profile_preserves_venv_and_binds_content(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch)
    profile = profiles.load_profiles(config)["local-triposr"]
    assert not profile.test_only
    assert str(profile.proposer.python) == config["sam"]["python"]
    assert (
        str(
            profile.shape_plan.backend_for(
                "generate_shape", "shape_generation@1"
            ).implementation.python
        )
        == config["sam"]["python"]
    )
    assert "path" not in profile.shape_identity["source"]
    before = profile.shape_identity["model"]
    Path(config["profiles"]["local-triposr"]["model"], "model.ckpt").write_bytes(b"changed")
    after = profiles.load_profiles(config)["local-triposr"]
    assert before != after.shape_identity["model"]


@pytest.mark.parametrize(
    "mutation", ["remote", "missing_weights", "bool_size", "nan_ratio", "unknown"]
)
def test_invalid_profiles_rejected(tmp_path, monkeypatch, mutation):
    config = _config(tmp_path, monkeypatch)
    shape = config["profiles"]["local-triposr"]
    if mutation == "remote":
        shape["model"] = "remote-owner/remote-model"
    elif mutation == "missing_weights":
        Path(shape["model"], "model.ckpt").unlink()
    elif mutation == "bool_size":
        shape["mc_resolution"] = True
    elif mutation == "nan_ratio":
        shape["foreground_ratio"] = float("nan")
    else:
        shape["unknown"] = 1
    with pytest.raises(ValueError):
        profiles.load_profiles(config)


def test_sam_parameter_change_updates_identity(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch)
    before = profiles.load_profiles(config)["local-triposr"]
    config["sam"]["points_per_side"] = 8
    after = profiles.load_profiles(config)["local-triposr"]
    assert before.proposal_identity != after.proposal_identity


def test_trellis_requires_local_conditioner_and_weights(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch)
    shape = config["profiles"]["local-triposr"]
    shape["backend"] = "trellis2"
    del shape["frame_validation"]
    model = Path(shape["model"])
    conditioning = tmp_path / "dino"
    conditioning.mkdir()
    (conditioning / "config.json").write_text("{}")
    (conditioning / "model.safetensors").write_bytes(b"dino fixture")
    pipeline = {
        "args": {
            "models": {"flow": "flow"},
            "image_cond_model": {
                "name": "DinoV3FeatureExtractor",
                "args": {"model_name": str(conditioning)},
            },
        }
    }
    (model / "pipeline.json").write_text(json.dumps(pipeline))
    (model / "flow.json").write_text("{}")
    (model / "flow.safetensors").write_bytes(b"flow fixture")
    result = profiles.load_profiles(config)["local-triposr"]
    assert result.shape_identity["model"]["conditioning_model_digest"].startswith("sha256:")
    broken = copy.deepcopy(pipeline)
    broken["args"]["image_cond_model"]["args"]["model_name"] = "remote/dino"
    (model / "pipeline.json").write_text(json.dumps(broken))
    monkeypatch.setattr(
        profiles, "_cached_snapshot", lambda *args: (_ for _ in ()).throw(ValueError("not cached"))
    )
    with pytest.raises(ValueError, match="not cached"):
        profiles.load_profiles(config)


def test_environment_probe_uses_unresolved_python_and_offline_env(tmp_path, monkeypatch):
    interpreter = tmp_path / "python"
    interpreter.symlink_to(sys.executable)
    captured = {}

    def run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return type("Result", (), {"stdout": '{"environment_digest":"sha256:env"}'})()

    monkeypatch.setattr(profiles.subprocess, "run", run)
    profiles._environment_identity(interpreter, ("torch",))
    assert captured["command"][0] == str(interpreter)
    assert captured["env"]["HF_HUB_OFFLINE"] == "1"


def test_conditioning_hf_cache_resolution_is_offline(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    seen = {}

    def run(command, **kwargs):
        seen.update(command=command, **kwargs)
        return type("Result", (), {"stdout": str(snapshot)})()

    monkeypatch.setattr(profiles.subprocess, "run", run)
    assert profiles._cached_snapshot(Path(sys.executable), "facebook/dinov3") == snapshot
    assert "try_to_load_from_cache" in seen["command"][3]
    assert "revision='main'" in seen["command"][3]
    assert seen["env"]["HF_HUB_OFFLINE"] == "1"


def test_external_checkpoint_resolution_is_local_only(tmp_path, monkeypatch):
    config = tmp_path / "checkpoint.json"
    weights = tmp_path / "checkpoint.safetensors"
    config.write_text("{}")
    weights.write_bytes(b"small fixture")
    seen = {}

    def run(command, **kwargs):
        seen.update(command=command, **kwargs)
        return type("Result", (), {"stdout": json.dumps([str(config), str(weights)])})()

    monkeypatch.setattr(profiles.subprocess, "run", run)
    assert profiles._cached_checkpoint(
        Path(sys.executable), "microsoft/TRELLIS-image-large/ckpts/decoder"
    ) == [config, weights]
    assert "local_files_only=True" in seen["command"][3]
    assert seen["command"][-2:] == ["microsoft/TRELLIS-image-large", "ckpts/decoder"]


def test_editable_sam_source_bytes_affect_environment_identity(tmp_path, monkeypatch):
    package = tmp_path / "segment_anything"
    package.mkdir()
    (package / "__init__.py").write_text("raise RuntimeError('must not import model')")
    source = package / "predictor.py"
    source.write_text("VERSION = 1")
    info = tmp_path / "segment_anything-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Name: segment_anything\nVersion: 1.0\n")
    (info / "RECORD").write_text("editable.pth,,\nsegment_anything-1.0.dist-info/METADATA,,\n")
    (tmp_path / "editable.pth").write_text(str(tmp_path))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    first = profiles._environment_identity(Path(sys.executable), ("segment_anything",))
    source.write_text("VERSION = 2")
    second = profiles._environment_identity(Path(sys.executable), ("segment_anything",))
    assert first["environment_digest"] != second["environment_digest"]


@pytest.mark.parametrize(
    "bad", [{}, {"args": []}, {"args": {"models": {"x": "x"}, "image_cond_model": []}}]
)
def test_invalid_trellis_pipeline_is_value_error(tmp_path, bad):
    (tmp_path / "pipeline.json").write_text(json.dumps(bad))
    (tmp_path / "x.json").write_text("{}")
    (tmp_path / "x.safetensors").write_bytes(b"fixture")
    with pytest.raises(ValueError):
        profiles._model_identity("trellis2", tmp_path, Path(sys.executable))


@pytest.mark.parametrize("change", ["checkpoint", "model_add", "model_delete", "symlink"])
def test_profile_guard_rejects_changed_resources(tmp_path, monkeypatch, change):
    config = _config(tmp_path, monkeypatch)
    profile = profiles.load_profiles(config)["local-triposr"]
    profile.identity_check()
    model = Path(config["profiles"]["local-triposr"]["model"])
    if change == "checkpoint":
        Path(config["sam"]["checkpoint"]).write_bytes(b"changed")
    elif change == "model_add":
        (model / "new.json").write_text("{}")
    elif change == "model_delete":
        (model / "model.ckpt").unlink()
    else:
        interpreter = Path(config["sam"]["python"])
        interpreter.unlink()
        interpreter.symlink_to("/bin/true")
    with pytest.raises(ValueError, match="resources changed"):
        profile.identity_check()


def test_digest_rejects_mutation_during_hash(tmp_path, monkeypatch):
    path = tmp_path / "weights"
    path.write_bytes(b"before")
    original = profiles._signature
    calls = 0

    def signature(value):
        nonlocal calls
        calls += 1
        if calls == 2:
            path.write_bytes(b"after")
        return original(value)

    monkeypatch.setattr(profiles, "_signature", signature)
    with pytest.raises(ValueError, match="resources changed"):
        profiles._digest(path)


def test_environment_guard_rejects_new_package_source(tmp_path, monkeypatch):
    package = tmp_path / "segment_anything"
    package.mkdir()
    (package / "__init__.py").write_text("# fixture")
    info = tmp_path / "segment_anything-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Name: segment_anything\nVersion: 1.0\n")
    (info / "RECORD").write_text("segment_anything-1.0.dist-info/METADATA,,\n")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    checks = []
    token = profiles._CHECKS.set(checks)
    try:
        identity = profiles._environment_identity(Path(sys.executable), ("segment_anything",))
    finally:
        profiles._CHECKS.reset(token)
    assert not any(key.startswith("_resource") for key in identity)
    (package / "new.py").write_text("NEW = True")
    with pytest.raises(ValueError, match="resources changed"):
        for check in checks:
            check()


def test_progress_does_not_change_identity_and_resets_after_failure(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch)
    baseline = profiles.load_profiles(config)["local-triposr"]
    events = []
    loaded = profiles.load_profiles(config, progress=events.append)["local-triposr"]
    assert loaded.shape_identity == baseline.shape_identity
    assert loaded.proposal_identity == baseline.proposal_identity
    assert any("model weights" in event and "核验完成" in event for event in events)
    assert any("SAM environment" in event for event in events)
    count = len(events)
    profiles.load_profiles(config)
    assert len(events) == count
    monkeypatch.setattr(
        profiles, "_model_identity", lambda *args: (_ for _ in ()).throw(ValueError("injected"))
    )
    with pytest.raises(ValueError, match="injected"):
        profiles.load_profiles(config, progress=events.append)
    assert "核验失败" in events[-1]
    assert profiles._PROGRESS.get() is None


def test_proposal_only_loader_never_loads_shape_and_preserves_resource_guards(
    tmp_path, monkeypatch
):
    config = _config(tmp_path, monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError("shape resources must not be inspected")

    monkeypatch.setattr(profiles, "_load_shape_profiles", forbidden)
    progress = []
    loaded = profiles.load_proposal_profiles(
        {"profiles": {"sam": config["sam"]}}, progress=progress.append
    )
    profile = loaded["sam"]
    assert profile.name == "sam" and not profile.test_only
    assert str(profile.proposer.python) == config["sam"]["python"]
    assert not hasattr(profile, "shape_plan")
    assert profile.proposal_identity["checkpoint_digest"].startswith("sha256:")
    assert profile.identity_check is not None
    profile.identity_check()
    assert any("SAM checkpoint" in message for message in progress)
    Path(config["sam"]["checkpoint"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="resources changed"):
        profile.identity_check()
    assert profiles._CHECKS.get() is None and profiles._PROGRESS.get() is None


def test_proposal_profile_guards_are_independent_and_failures_restore_context(
    tmp_path, monkeypatch
):
    config = _config(tmp_path, monkeypatch)
    second_checkpoint = tmp_path / "second.pth"
    second_checkpoint.write_bytes(b"second")
    loaded = profiles.load_proposal_profiles(
        {
            "profiles": {
                "first": config["sam"],
                "second": {**config["sam"], "checkpoint": str(second_checkpoint)},
            }
        }
    )
    second_checkpoint.write_bytes(b"changed")
    loaded["first"].identity_check()
    with pytest.raises(ValueError, match="resources changed"):
        loaded["second"].identity_check()
    for raw in (
        {"profiles": {}},
        {"profiles": {"": config["sam"]}},
        {"profiles": {"broken": {"python": "/missing", "checkpoint": "/missing"}}},
    ):
        with pytest.raises(ValueError):
            profiles.load_proposal_profiles(raw)
        assert profiles._CHECKS.get() is None and profiles._PROGRESS.get() is None


@pytest.mark.parametrize("field", ["python", "checkpoint", "parameters", "timeout", "identity"])
@pytest.mark.parametrize("proposal_only", [False, True])
def test_sam_execution_configuration_drift_is_rejected(tmp_path, monkeypatch, field, proposal_only):
    config = _config(tmp_path, monkeypatch)
    if proposal_only:
        loaded = profiles.load_proposal_profiles({"profiles": {"sam": config["sam"]}})["sam"]
    else:
        loaded = profiles.load_profiles(config)["local-triposr"]
    loaded.identity_check()
    if field == "python":
        loaded.proposer.python = Path("/another/environment/python")
    elif field == "checkpoint":
        loaded.proposer.checkpoint = Path("/another/checkpoint.pth")
    elif field == "parameters":
        loaded.proposer.parameters["points_per_side"] = 7
    elif field == "timeout":
        loaded.proposer.timeout_seconds += 1
    else:
        loaded.proposal_identity["checkpoint_digest"] = "sha256:" + "f" * 64
    with pytest.raises(ValueError, match="resources changed"):
        loaded.identity_check()


def test_sam_worker_injection_does_not_change_deployment_identity(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch)
    loaded = profiles.load_proposal_profiles({"profiles": {"sam": config["sam"]}})["sam"]
    loaded.proposer.worker = object()
    loaded.identity_check()
