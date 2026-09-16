from __future__ import annotations

import json
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
    weights.write_bytes(b"other-content")
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
    weight.write_bytes(b"other-content")
    assert backend._model_cache_identity() != first
    assert len(calls) == 2
    backend.MODEL_REVISION = "changed"
    backend._model_cache_identity()
    assert len(calls) == 3
