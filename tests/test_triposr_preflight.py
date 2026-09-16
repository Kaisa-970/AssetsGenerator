from __future__ import annotations

import json
import subprocess
from pathlib import Path

from assets_generator.backends import triposr_preflight


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "TripoSR"
    (repo / "tsr").mkdir(parents=True)
    (repo / "run.py").write_text("")
    (repo / "tsr" / "system.py").write_text("")
    return repo


def _model(tmp_path: Path) -> Path:
    model = tmp_path / "model"
    model.mkdir()
    (model / "model.ckpt").write_bytes(b"fixture")
    (model / "config.yaml").write_text("fixture")
    return model


def test_preflight_passes_for_complete_read_only_probe(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    model = _model(tmp_path)
    python = tmp_path / "python"
    python.write_text("")
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(command)
        if "nvidia-smi" in command[0]:
            return subprocess.CompletedProcess(command, 0, "GPU, 570.0, 8192\n", "")
        payload = {
            "python": str(python),
            "python_version": "3.10.0",
            "imports": {"torchmcubes": {"ok": True}},
            "torch": {
                "version": "2.8.0",
                "cuda_version": "12.8",
                "cuda_available": True,
                "device_name": "GPU",
                "device_capability": [12, 0],
                "compiled_arches": ["sm_120"],
            },
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(triposr_preflight.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(triposr_preflight.subprocess, "run", run)

    report = triposr_preflight.run_preflight(python, repo, str(model))

    assert report.ready is True
    assert len(calls) == 2
    assert all("run.py" not in command for command in calls)
    python_call = next(command for command in calls if "nvidia-smi" not in command[0])
    assert "-B" in python_call


def test_preflight_rejects_remote_model_and_unsupported_device(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    python = tmp_path / "python"
    python.write_text("")

    def run(command, **kwargs):
        if "nvidia-smi" in command[0]:
            return subprocess.CompletedProcess(command, 0, "GPU, 570.0, 8192\n", "")
        payload = {
            "python": str(python),
            "python_version": "3.10.0",
            "imports": {"torchmcubes": {"ok": False, "error": "ImportError"}},
            "torch": {
                "version": "2.5.0",
                "cuda_version": "12.1",
                "cuda_available": True,
                "device_name": "GPU",
                "device_capability": [12, 0],
                "compiled_arches": ["sm_90"],
            },
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(triposr_preflight.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(triposr_preflight.subprocess, "run", run)

    report = triposr_preflight.run_preflight(python, repo, "stabilityai/TripoSR")
    statuses = {check.name: check.status for check in report.checks}

    assert report.ready is False
    assert statuses["model_snapshot"] == "fail"
    assert statuses["required_imports"] == "fail"
    assert statuses["torch_cuda"] == "fail"


def test_main_emits_json_and_nonzero_for_missing_paths(capsys):
    result = triposr_preflight.main(
        [
            "--python",
            "/missing/python",
            "--repo",
            "/missing/repo",
            "--model",
            "/missing/model",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 1
    assert payload["ready"] is False
