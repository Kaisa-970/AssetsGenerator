"""Exercise the executable entry point with contract Backends, never GPU models."""

import importlib.util
import json
import sys
from pathlib import Path

from test_multi_view_workflow import (
    ContractGeometryFrontend,
    ContractReconstruction,
    _observations,
    _plan,
)

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.dag_multi_view import MultiViewProfile


def test_demo_start_and_resume_use_registered_relations(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("demo", Path("examples/dag_multi_view_demo.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    profile = MultiViewProfile(
        _plan(ContractGeometryFrontend(), ContractReconstruction()),
        {"fixture": "demo"},
        test_only=True,
    )
    monkeypatch.setattr(module, "profile", lambda args: profile)
    store = LocalArtifactStore(tmp_path / "store")
    observations = _observations(tmp_path, store)
    common = [
        "--store",
        str(tmp_path / "store"),
        "--directory",
        str(tmp_path / "service"),
        "--da3-python",
        "unused",
        "--da3-repo",
        "unused",
        "--da3-model",
        "unused",
        "--open3d-python",
        "unused",
    ]
    monkeypatch.setattr(
        sys, "argv", ["demo", "start", "--observations", observations.artifact_id, *common]
    )
    module.main()
    output = capsys.readouterr().out
    prefix, payload = output.split("\n", 1)
    run = json.loads(payload)
    assert run["status"] == "succeeded"
    assert prefix == "run_id=" + run["run_id"]
    monkeypatch.setattr(sys, "argv", ["demo", "resume", "--run", run["run_id"], *common])
    module.main()
    restored = json.loads(capsys.readouterr().out)
    assert restored["status"] == "succeeded"
    for name in run["dag"]["node_states"]:
        assert (
            restored["dag"]["node_states"][name]["attempts"]
            == run["dag"]["node_states"][name]["attempts"]
        )
