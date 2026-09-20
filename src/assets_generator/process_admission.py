"""Shared compute admission across all locally owned orchestration runs."""

from __future__ import annotations

from functools import partial

from .contracts import ContractError
from .serialization import cache_key, canonical_json_bytes, read_json, to_primitive
from .workbench_models import WorkerExecution
from .workbench_persistence import ProcessProbe, WorkbenchRepository


def admit_compute(
    repository: WorkbenchRepository,
    probe: ProcessProbe,
    *,
    exclude_attempt: tuple[str, str, int] | None = None,
) -> None:
    directory = str(repository.directory.resolve())
    for marker in (repository.store.root / "parent_run_owners").glob("*.json"):
        try:
            owner = read_json(marker)
            if owner.get("workbench_directory") != directory:
                continue
            run = repository.load(marker.stem)
            workers: list[tuple[str, int, WorkerExecution]] = []
            if run.workbench is not None:
                workers = [
                    (stage.stage_id, attempt.attempt, attempt.worker_execution)
                    for stage in run.workbench.stage_states.values()
                    for attempt in stage.attempts
                    if attempt.worker_execution is not None
                ]
            elif run.dag is not None:
                workers = [
                    (node.node_id, attempt.attempt, worker)
                    for node in run.dag.node_states.values()
                    for attempt in node.attempts
                    for worker in attempt.worker_executions
                ]
            else:
                raise ValueError("owned run has no process evidence")
            for node_id, attempt, worker in workers:
                if (run.run_id, node_id, attempt) == exclude_attempt:
                    continue
                if worker.launch_phase in {"prepared", "identity_recorded"}:
                    continue
                if (
                    worker.launch_phase == "exit_observed"
                    and worker.last_probe is not None
                    and worker.last_probe.result == "exited"
                ):
                    continue
                identity = worker.identity()
                terminal = (
                    repository.directory
                    / "process_exits"
                    / (
                        cache_key(
                            {
                                "run": run.run_id,
                                "node": node_id,
                                "attempt": attempt,
                                "job": worker.job_id,
                                "identity": identity,
                            }
                        ).split(":", 1)[1]
                        + ".json"
                    )
                )
                if terminal.exists():
                    evidence = read_json(terminal)
                    if (
                        evidence.get("identity") != to_primitive(identity)
                        or evidence.get("result") != "exited"
                    ):
                        raise ValueError("invalid terminal process evidence")
                    continue
                observation = probe.observe(identity)
                if observation.result == "exited":
                    payload = canonical_json_bytes(
                        {
                            "identity": to_primitive(identity),
                            "result": "exited",
                            "observation": to_primitive(observation),
                        }
                    )
                    repository._mutate(partial(repository.io.write, terminal, payload))
                if observation.result != "exited":
                    raise ContractError(
                        f"compute admission blocked by {run.run_id}/{node_id}: "
                        f"{observation.result}; {observation.reason or 'process group unverified'}"
                    )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ContractError(
                f"cannot verify process evidence for {marker.stem}: {error}"
            ) from error
