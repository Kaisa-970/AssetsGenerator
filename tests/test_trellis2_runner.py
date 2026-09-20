from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import trimesh
from PIL import Image

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


@pytest.mark.parametrize(
    "failure", [None, "CUDA error: invalid configuration argument", "bad input"]
)
def test_runner_postprocess_response_and_loadable_glb(tmp_path, monkeypatch, failure):
    geometry = trimesh.creation.box()

    class Tensor(_Array):
        @property
        def shape(self):
            return self.value.shape

    mesh = SimpleNamespace(
        vertices=Tensor(geometry.vertices.copy()),
        faces=Tensor(geometry.faces.copy()),
        attrs=None,
        coords=None,
        layout=None,
        voxel_size=0.01,
        simplify=lambda count: None,
    )
    pipeline = SimpleNamespace(cuda=lambda: None, run=lambda *args, **kwargs: [mesh])
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(
                is_available=lambda: True,
                reset_peak_memory_stats=lambda: None,
                max_memory_allocated=lambda: 1024,
            )
        ),
    )
    monkeypatch.setitem(sys.modules, "trellis2", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules,
        "trellis2.pipelines",
        SimpleNamespace(
            Trellis2ImageTo3DPipeline=SimpleNamespace(from_pretrained=lambda model: pipeline)
        ),
    )

    def to_glb(**kwargs):
        if failure:
            raise RuntimeError(failure)
        return geometry

    monkeypatch.setitem(
        sys.modules, "o_voxel", SimpleNamespace(postprocess=SimpleNamespace(to_glb=to_glb))
    )
    monkeypatch.setattr(
        trellis2_runner.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="test")
    )
    monkeypatch.setattr(trellis2_runner, "snapshot_digest", lambda path: "sha256:" + "a" * 64)
    # main prepends the Backend repo to sys.path; isolate this mutation from other tests.
    monkeypatch.setattr(sys, "path", list(sys.path))
    image = tmp_path / "input.png"
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(image)
    output, response, request = (
        tmp_path / name for name in ("output.glb", "response.json", "request.json")
    )
    request.write_text(
        json.dumps(
            {
                "repo": str(tmp_path),
                "model": str(tmp_path),
                "input_image": str(image),
                "output_glb": str(output),
                "seed": 42,
                "pipeline_type": "512",
                "decimation_target": 100,
                "texture_size": 32,
            }
        )
    )
    monkeypatch.setattr(sys, "argv", ["runner", str(request), str(response)])
    if failure == "bad input":
        with pytest.raises(RuntimeError, match="bad input"):
            trellis2_runner.main()
        assert not response.exists()
        assert not output.exists()
        return
    assert trellis2_runner.main() == 0
    metadata = json.loads(response.read_text())
    assert metadata["postprocess_mode"] == (
        "geometry_fallback_no_texture" if failure else "textured_glb"
    )
    loaded = trimesh.load(output, force="mesh")
    assert len(loaded.faces) == metadata["face_count"] == len(geometry.faces)
    assert np.allclose(loaded.bounds, geometry.bounds)
    if failure:
        assert loaded.visual.kind != "texture"
