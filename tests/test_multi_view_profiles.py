"""Local profile validation/identity, without importing model environments."""

from dataclasses import FrozenInstanceError, asdict

import pytest

from assets_generator import multi_view_profiles as profiles
from assets_generator.workbench_profiles import _CHECKS


def _config():
    return dict(da3_python="python", da3_repo="repo", da3_model="model", open3d_python="python")


@pytest.mark.parametrize(
    "changes",
    [
        {"unknown": 1},
        {"da3_python": ""},
        {"da3_repo": None},
        {"process_res": True},
        {"process_res": 13},
        {"process_res": 392.0},
        {"da3_timeout": float("nan")},
        {"open3d_timeout": float("inf")},
        {"voxel_size_ratio": 0},
        {"sdf_trunc_ratio": -1},
        {"depth_trunc_ratio": False},
        {"depth_trunc_ratio": "3"},
        {"voxel_size_ratio": 0.05, "sdf_trunc_ratio": 0.04},
        {"up_axis": "Y"},
        {"up_axis": []},
    ],
)
def test_invalid_configuration_precedes_resource_access(monkeypatch, changes):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid configuration must not access resources")

    monkeypatch.setattr(profiles, "_path", forbidden)
    with pytest.raises(ValueError):
        profiles.load_multi_view_profile({**_config(), **changes})
    assert _CHECKS.get() is None


def test_config_roundtrip_defaults_and_frozen():
    config = profiles.MultiViewProfileConfig.from_mapping(_config())
    assert profiles.MultiViewProfileConfig.from_mapping(asdict(config)) == config
    assert config.process_res == 392
    assert config.up_axis == "-Y"
    with pytest.raises(FrozenInstanceError):
        config.process_res = 512
    with pytest.raises(ValueError, match="requires"):
        profiles.load_multi_view_profile({"da3_python": "python"})


def test_real_profile_binding_identity_and_resource_guard(tmp_path, monkeypatch):
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o755)
    venv_python = tmp_path / "venv-python"
    venv_python.symlink_to(python)
    repo = tmp_path / "repo"
    repo.mkdir()
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}")
    (model / "model.safetensors").write_bytes(b"fixture-model")
    monkeypatch.setattr(profiles, "backend_source_identity", lambda path: {"digest": "fixture"})
    environments = []

    def environment(path, packages):
        environments.append((path, packages))
        return {"fixture": str(path)}

    monkeypatch.setattr(profiles, "_environment_identity", environment)
    config = profiles.MultiViewProfileConfig(
        str(venv_python),
        str(repo),
        str(model),
        str(venv_python),
        process_res=518,
        voxel_size_ratio=0.02,
        sdf_trunc_ratio=0.08,
        up_axis="+Z",
    )
    result = profiles.load_multi_view_profile(config)
    assert not result.test_only
    geometry = result.plan.backend_for("estimate_geometry", "geometry_frontend@1").implementation
    reconstruction = result.plan.backend_for("reconstruct", "reconstruction@1").implementation
    assert geometry.python == venv_python
    assert geometry.process_res == 518
    assert reconstruction.voxel_size_ratio == 0.02
    assert reconstruction.up_axis == "+Z"
    assert result.identity["geometry"]["runner_digest"].startswith("sha256:")
    assert result.identity["reconstruction"]["adapter_digest"].startswith("sha256:")
    assert len(environments) == 2
    assert environments[0][0] == venv_python
    assert _CHECKS.get() is None
    assert result.check is not None
    result.check()
    (model / "model.safetensors").write_bytes(b"changed-model")
    with pytest.raises(ValueError, match="resources changed"):
        result.check()


def test_failed_load_restores_guard_context(tmp_path):
    outer = []
    token = _CHECKS.set(outer)
    try:
        with pytest.raises(ValueError, match="does not exist"):
            profiles.load_multi_view_profile({**_config(), "da3_python": str(tmp_path / "missing")})
        assert _CHECKS.get() is outer
    finally:
        _CHECKS.reset(token)


def test_demo_profile_wrapper_uses_same_defaults_and_loader(monkeypatch):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "profile_demo", Path("examples/dag_multi_view_demo.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    args = module.parser().parse_args(
        [
            "start",
            "--store",
            "store",
            "--directory",
            "directory",
            "--da3-python",
            "python",
            "--da3-repo",
            "repo",
            "--da3-model",
            "model",
            "--open3d-python",
            "python",
        ]
    )
    captured = []
    sentinel = object()

    def loader(config):
        captured.append(config)
        return sentinel

    monkeypatch.setattr(module, "load_multi_view_profile", loader)
    assert module.profile(args) is sentinel
    assert captured == [profiles.MultiViewProfileConfig.from_mapping(_config())]
