from __future__ import annotations

import hashlib
import sys
from types import SimpleNamespace

from assets_generator.backends.environment_identity import current_environment_identity


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
