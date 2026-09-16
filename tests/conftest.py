from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from assets_generator.backends.source_identity import backend_source_identity


@pytest.fixture
def triposr_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., tuple[Path, Path]]:
    monkeypatch.setattr(
        "assets_generator.operators.backend_environment_identity", lambda python: {"fixture": True}
    )

    def create(*, python: Path | None = None) -> tuple[Path, Path]:
        repo = tmp_path / "triposr-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "fixture@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Fixture"], cwd=repo, check=True)
        (repo / "fixture.py").write_text("FRAME = '+Z'\n", encoding="utf-8")
        subprocess.run(["git", "add", "fixture.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)

        backend_python = (python or Path(sys.executable)).expanduser().absolute()
        evidence = tmp_path / "triposr-frame-validation.json"
        backend_dir = Path(__file__).parents[1] / "src/assets_generator/backends"

        def digest(name: str) -> str:
            return f"sha256:{hashlib.sha256((backend_dir / name).read_bytes()).hexdigest()}"

        evidence.write_text(
            json.dumps(
                {
                    "rule": "triposr-marching-cubes-glb-roundtrip-v1",
                    "status": "pass",
                    "backend_python": str(backend_python),
                    "triposr_repo": str(repo.resolve()),
                    "backend_source": backend_source_identity(repo),
                    "backend_environment": {"fixture": True},
                    "fixture_digest": digest("triposr_frame_fixture.py"),
                    "validator_digest": digest("triposr_frame_validation.py"),
                }
            ),
            encoding="utf-8",
        )
        return repo, evidence

    return create
