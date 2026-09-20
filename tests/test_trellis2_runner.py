from __future__ import annotations

from pathlib import Path

from assets_generator.backends import trellis2_runner


class _Array:
    def __init__(self, value):
        self.value = value

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.value


class _Mesh:
    vertices = _Array("vertices")
    faces = _Array("faces")


def test_cuda_postprocess_error_is_eligible_for_geometry_fallback():
    assert trellis2_runner._is_geometry_fallback_error(RuntimeError("CuMesh failed"))
    assert trellis2_runner._is_geometry_fallback_error(
        RuntimeError("CUDA error: invalid configuration argument")
    )
    assert not trellis2_runner._is_geometry_fallback_error(RuntimeError("bad input"))


def test_geometry_fallback_exports_mesh_without_texture(monkeypatch, tmp_path):
    calls = {}

    class _Trimesh:
        def __init__(self, *, vertices, faces, process):
            calls["mesh"] = (vertices, faces, process)

        def export(self, path, *, file_type):
            calls["export"] = (Path(path), file_type)

    monkeypatch.setattr(trellis2_runner.trimesh, "Trimesh", _Trimesh)
    output = tmp_path / "fallback.glb"
    trellis2_runner._export_geometry_fallback(_Mesh(), str(output))

    assert calls["mesh"] == ("vertices", "faces", False)
    assert calls["export"] == (output, "glb")
