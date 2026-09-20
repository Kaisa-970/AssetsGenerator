"""Exercise the shipped YAML demo through independent Python processes."""

import os
import subprocess
import sys
from pathlib import Path


def test_yaml_diamond_runs_and_recovers_without_reexecution(tmp_path):
    script = Path("examples/dag_diamond_demo.py").absolute()
    env = {**os.environ, "PYTHONPATH": str(Path("src").absolute())}
    cmd = [sys.executable, str(script), "--directory", str(tmp_path / "demo")]
    first = subprocess.run(cmd, env=env, check=True, capture_output=True, text=True, timeout=30)
    assert [line for line in first.stdout.splitlines() if line.startswith("execute")] == [
        "execute A",
        "execute B",
        "execute C",
        "execute D",
    ]
    assert "shared input: True" in first.stdout
    summary = next(line for line in first.stdout.splitlines() if line.startswith("dag_"))
    run_id, status = summary.split(": ")
    assert status == "succeeded"
    resumed = subprocess.run(
        [*cmd, "--recover", run_id], env=env, check=True, capture_output=True, text=True, timeout=30
    )
    assert "execute " not in resumed.stdout
    assert f"{run_id}: succeeded" in resumed.stdout
    assert resumed.stdout.count("attempts=1") == 4
