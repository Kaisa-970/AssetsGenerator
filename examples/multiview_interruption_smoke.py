"""Explicit Linux/GPU smoke: kill this harness's DAG controller during DA3.

Run with --config (existing multi-view profile JSON), --images (two or more
local images), and --output (a new evidence directory outside the repository).
No packages/models are installed. Signals target only this run's controller.
The model process is allowed to exit naturally; retry is explicit and serial.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import time
from pathlib import Path

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import ContractError
from assets_generator.dag_adapters import AdapterRegistry
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.dag_profiles import register_multi_view_profiles
from assets_generator.models import ArtifactRef, ObservationView
from assets_generator.multi_view_profiles import load_multi_view_profile
from assets_generator.multi_view_relations import register_multi_view_relations
from assets_generator.observations import make_observation_bundle, observation_bundle_value
from assets_generator.pipeline import compile_pipeline, load_operator_specs, load_pipeline
from assets_generator.relations import default_relation_registry
from assets_generator.serialization import to_primitive
from assets_generator.workbench_process import LinuxProcessProbe
from assets_generator.workflow import _import_image


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--images", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=1800)
    args = parser.parse_args()
    if len(args.images) < 2:
        parser.error("at least two images required")
    root = args.output.expanduser().absolute()
    root.mkdir(parents=True, exist_ok=False)

    def snapshot(name, value):
        (root / name).write_text(json.dumps(to_primitive(value), indent=2))

    os.environ["PYTHONUNBUFFERED"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    raw = json.loads(args.config.read_text())
    config = raw["profiles"][raw["default_profile"]]
    started = time.monotonic()
    profile = load_multi_view_profile(config)
    snapshot("resources.json", {"config": config, "load_seconds": time.monotonic() - started})
    adapters = AdapterRegistry()
    register_multi_view_profiles(adapters, {"local-multiview": profile}, "local-multiview")
    relations = default_relation_registry()
    register_multi_view_relations(relations)
    base = Path(__file__).resolve().parent
    plan = adapters.bind_plan(
        compile_pipeline(
            load_pipeline(base / "dag-multi-view-asset.yaml"),
            load_operator_specs(base / "dag-multi-view-operators.yaml"),
            relation_registry=relations,
            require_explicit_joins=True,
        ),
        relation_registry=relations,
    )
    store = LocalArtifactStore(root / "store")
    views = [
        ObservationView(str(index), _import_image(store, path, "rgb_image"))
        for index, path in enumerate(args.images)
    ]
    observations = store.persist_structured(
        observation_bundle_value(make_observation_bundle(views, store))
    )
    directory = root / "service"
    with DagRepository(store, directory) as repo:
        run = DagEngine(repo, adapters, relations).create(plan, {"observations": observations})
    rid = run.run_id
    snapshot("created.json", run)
    print(f"Created {rid}; starting DA3", flush=True)
    pid = os.fork()
    if pid == 0:
        try:
            with DagRepository(store, directory) as repo:
                result = DagEngine(repo, adapters, relations).drain(rid)
                snapshot("unexpected-completed.json", result)
        except BaseException as error:
            (root / "controller-error.txt").write_text(repr(error))
            os._exit(1)
        os._exit(0)
    reaped = False
    worker = None
    deadline = time.monotonic() + args.timeout
    probe = LinuxProcessProbe()
    reader = DagRepository(store, directory)
    try:
        while time.monotonic() < deadline:
            current = reader.load(rid)
            state = current.dag.node_states["geometry"]
            if state.attempts and state.current().worker_executions:
                worker = state.current().worker_executions[-1]
                if worker.launch_phase == "release_authorized":
                    observation = probe.observe(worker.identity())
                    progress = b""
                    for member in observation.group_members:
                        try:
                            proc = Path(f"/proc/{member.pid}")
                            if b"da3_runner.py" not in (proc / "cmdline").read_bytes():
                                continue
                            for fd in (1, 2):
                                progress += (proc / f"fd/{fd}").read_bytes()
                        except (FileNotFoundError, ProcessLookupError):
                            continue
                    if (
                        b"Processed Images Done" in progress
                        and b"Model Forward Pass Done" not in progress
                    ):
                        # This is the real upstream inference call after model load.
                        # Preserve exact logs: do not claim a particular GPU kernel was interrupted.
                        (root / "inference-progress.txt").write_bytes(progress)
                        snapshot("before-kill.json", current)
                        snapshot(
                            "kill.json",
                            {"controller_pid": pid, "worker": worker, "observation": observation},
                        )
                        os.kill(pid, signal.SIGKILL)
                        os.waitpid(pid, 0)
                        reaped = True
                        print("Controller killed after DA3 input processing", flush=True)
                        break
            if state.status in {"succeeded", "failed"}:
                raise RuntimeError("DA3 ended before verified interruption; no acceptance")
            time.sleep(0.02)
        else:
            raise TimeoutError("No verified DA3 inference progress")
        with DagRepository(store, directory) as repo:
            engine = DagEngine(repo, adapters, relations)
            recovered = engine.recover(rid)
            snapshot("alive-recovered.json", recovered)
            assert recovered.status == "recovery_blocked", recovered.status
            try:
                engine.retry(rid, "geometry", recovered.dag.revision)
            except ContractError as error:
                (root / "blocked-retry.txt").write_text(str(error))
            else:
                raise AssertionError("Retry admitted while old worker alive")
            blocked = repo.load(rid)
            snapshot("blocked-retry.json", blocked)
            assert len(blocked.dag.node_states["geometry"].attempts) == 1
            assert len(blocked.dag.node_states["geometry"].current().worker_executions) == 1
            print("Live worker prevents duplicate dispatch", flush=True)
            while time.monotonic() < deadline:
                observation = probe.observe(worker.identity())
                if observation.result == "exited":
                    break
                time.sleep(0.5)
            else:
                raise TimeoutError("Original worker did not exit; no retry dispatched")
            snapshot("exit-observation.json", observation)
            recovered = engine.recover(rid)
            snapshot("exited-recovered.json", recovered)
            assert recovered.status == "interrupted", recovered.status
            assert len(recovered.dag.node_states["geometry"].attempts) == 1
            assert recovered.dag.node_states["geometry"].dispatch_block_reason is None
            result = engine.retry(rid, "geometry", recovered.dag.revision)
            snapshot("retried.json", result)
            assert result.status == "succeeded", result.status
            counts = {key: len(state.attempts) for key, state in result.dag.node_states.items()}
            assert counts == {"geometry": 2, "reconstruction": 1, "release": 1}, counts
            for state in result.dag.node_states.values():
                for value in state.current().outputs.values():
                    for ref in value if isinstance(value, list) else [value]:
                        if isinstance(ref, ArtifactRef):
                            repo.verify_reference_closure(ref)
            restored = engine.recover(rid)
            snapshot("restored.json", restored)
            assert to_primitive(restored.dag.node_states) == to_primitive(result.dag.node_states)
            snapshot(
                "validation.json",
                {
                    "run_id": rid,
                    "status": result.status,
                    "attempts": counts,
                    "closures_verified": True,
                    "repeated_recovery_unchanged": True,
                },
            )
            print(
                "Explicit retry published; evidence verified; repeated recovery unchanged",
                flush=True,
            )
    finally:
        if not reaped:
            # Clean up only the controller created by this harness, never arbitrary GPU jobs.
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)
        if worker is not None:
            snapshot("final-worker-observation.json", probe.observe(worker.identity()))


if __name__ == "__main__":
    main()
