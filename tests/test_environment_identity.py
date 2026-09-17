from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from assets_generator.backends.environment_identity import (
    current_environment_identity,
    open3d_environment_identity,
)


def test_open3d_identity_detects_same_version_content_changes(tmp_path, monkeypatch):
    for name in ("open3d", "numpy", "Pillow", "trimesh"):
        package = tmp_path / name
        package.mkdir()
        (package / "__init__.py").write_text("__version__ = '0.19.0'\n")
        metadata = tmp_path / f"{name}-0.19.0.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text(f"Name: {name}\nVersion: 0.19.0\n")
        (metadata / "INSTALLER").write_text("pip\n")
        entries = [f"{name}/__init__.py", f"{metadata.name}/METADATA"]
        if name == "open3d":
            (package / "cpu").mkdir()
            (package / "cpu/pybind.so").write_bytes(b"fake extension")
            (package / "_build_config.py").write_text("BUILD_CUDA_MODULE = False\n")
            entries.extend(["open3d/cpu/pybind.so", "open3d/_build_config.py"])
        (metadata / "RECORD").write_text("\n".join(f"{path},," for path in entries))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    executable = tmp_path / "venv/bin/python"
    executable.parent.mkdir(parents=True)
    executable.symlink_to(sys.executable)
    initial = open3d_environment_identity(executable)
    assert initial["configured_python"] == str(executable)
    assert initial["python_executable"] == str(executable)
    assert initial == open3d_environment_identity(executable)
    for relative in ["open3d/cpu/pybind.so", "open3d/_build_config.py", "numpy/__init__.py"]:
        path = tmp_path / relative
        path.write_bytes(path.read_bytes() + b"\n# local modification\n")
        changed = open3d_environment_identity(executable)
        assert changed["open3d_version"] == initial["open3d_version"]
        assert changed["environment_digest"] != initial["environment_digest"]
        initial = changed


def test_open3d_identity_fails_closed_for_missing_interpreter(tmp_path):
    with pytest.raises(ValueError, match="cannot identify Open3D environment"):
        open3d_environment_identity(Path(tmp_path / "missing-python"))


def test_environment_identity_tracks_torchmcubes_extension(tmp_path, monkeypatch) -> None:
    package = tmp_path / "torchmcubes"
    package.mkdir()
    initializer = package / "__init__.py"
    initializer.write_text("VERSION = 1\n", encoding="utf-8")
    extension = tmp_path / "torchmcubes_module.so"
    extension.write_bytes(b"compiled extension")
    torch_extension = tmp_path / "torch_core.so"
    torch_extension.write_bytes(b"torch core")
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            __version__="test",
            version=SimpleNamespace(cuda="test"),
            _C=SimpleNamespace(__file__=str(torch_extension)),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "torchmcubes",
        SimpleNamespace(__file__=str(initializer), __version__="test"),
    )
    monkeypatch.setitem(
        sys.modules,
        "torchmcubes_module",
        SimpleNamespace(__file__=str(extension)),
    )

    identity = current_environment_identity()

    assert identity["torchmcubes_extension"] == str(extension)
    assert identity["torchmcubes_extension_digest"] == (
        f"sha256:{hashlib.sha256(extension.read_bytes()).hexdigest()}"
    )
    assert identity["torch_extension_digest"] == (
        f"sha256:{hashlib.sha256(torch_extension.read_bytes()).hexdigest()}"
    )
