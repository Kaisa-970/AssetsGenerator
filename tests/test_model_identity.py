from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from assets_generator.backends.model_identity import snapshot_digest, snapshot_state
from assets_generator.operators import BiRefNetSegmentationBackend


def test_model_digest_tracks_weights_but_ignores_runtime_files(tmp_path):
    weights = tmp_path / "weights.bin"
    weights.write_bytes(b"first")
    before = snapshot_digest(tmp_path)
    state = snapshot_state(tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "run.txt").write_text("runtime")
    assert snapshot_digest(tmp_path) == before
    assert snapshot_state(tmp_path) == state
    weights.write_bytes(b"other")
    assert snapshot_digest(tmp_path) != before


def test_remote_identity_cache_tracks_resolved_snapshot(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    weight = snapshot / "weights.bin"
    weight.write_bytes(b"first")
    backend = BiRefNetSegmentationBackend(Path(sys.executable))
    calls = []

    def probe(request):
        calls.append(request)
        Path(request.command[-1]).write_text(
            json.dumps(
                {
                    "model_digest": snapshot_digest(snapshot),
                    "snapshot_path": str(snapshot),
                    "snapshot_state": snapshot_state(snapshot),
                }
            )
        )

    monkeypatch.setattr(backend.worker, "run", probe)
    first = backend._model_cache_identity()
    assert backend._model_cache_identity() == first
    assert len(calls) == 1
    stamp = weight.stat()
    weight.write_bytes(b"other")
    os.utime(weight, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert backend._model_cache_identity() != first
    assert len(calls) == 2
    backend.MODEL_REVISION = "changed"
    backend._model_cache_identity()
    assert len(calls) == 3


def test_local_runner_identity_does_not_import_huggingface(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    (model / "weights.bin").write_bytes(b"weights")
    request = tmp_path / "request.json"
    response = tmp_path / "response.json"
    request.write_text(json.dumps({"action": "identity", "model": str(model)}))
    runner = Path(__file__).parents[1] / "src/assets_generator/backends/birefnet_runner.py"
    result = subprocess.run(
        [sys.executable, "-S", str(runner), str(request), str(response)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert json.loads(response.read_text())["model_digest"].startswith("sha256:")
