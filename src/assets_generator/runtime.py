from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import TypeVar

from .artifact_store import LocalArtifactStore
from .contracts import (
    OperatorSpec,
    validate_operator_inputs,
    validate_operator_outputs,
    validate_port_value,
)
from .errors import classify_error
from .models import ArtifactRef, NodeAttempt, PortValue
from .observations import observation_bundle_from_artifact
from .pipeline import PipelineDefinition

T = TypeVar("T")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Phase1Runtime:
    def __init__(
        self,
        store: LocalArtifactStore,
        pipeline: PipelineDefinition,
        specs: dict[str, OperatorSpec],
    ) -> None:
        self.store = store
        self.pipeline = pipeline
        self.specs = specs
        self.attempts: list[NodeAttempt] = []

    def validate_pipeline_inputs(self, inputs: Mapping[str, PortValue | list[PortValue]]) -> None:
        unknown = set(inputs) - set(self.pipeline.inputs)
        if unknown:
            raise ValueError(f"pipeline received unknown inputs: {sorted(unknown)}")
        for name, spec in self.pipeline.inputs.items():
            value = inputs.get(name)
            validate_port_value(
                operator=self.pipeline.name,
                port_name=name,
                spec=spec,
                value=value,
                store=self.store,
            )
            if isinstance(value, ArtifactRef) and "observation_bundle" in spec.kinds:
                observation_bundle_from_artifact(value, self.store)

    def run_node(
        self,
        node_id: str,
        inputs: dict[str, PortValue | list[PortValue]],
        execute: Callable[[], tuple[T, dict[str, PortValue | list[PortValue]]]],
        *,
        backend: str | None = None,
        execution_mode: str | Callable[[T], str] = "executed",
        validate_result: Callable[[T], None] | None = None,
    ) -> T:
        node = self.pipeline.nodes[node_id]
        operator_key = str(node["operator"])
        spec = self.specs[operator_key]
        attempt = NodeAttempt(
            node_id=node_id,
            attempt=1,
            operator=operator_key,
            backend=backend,
            status="running",
            execution_mode=execution_mode if isinstance(execution_mode, str) else "executed",
            started_at=utc_now(),
            finished_at=None,
            error_code=None,
        )
        self.attempts.append(attempt)
        try:
            validate_operator_inputs(spec, inputs, self.store)
            result, outputs = execute()
            validate_operator_outputs(spec, outputs, self.store)
            if validate_result is not None:
                validate_result(result)
            if callable(execution_mode):
                attempt.execution_mode = execution_mode(result)
            attempt.outputs = outputs
            attempt.status = "succeeded"
            return result
        except Exception as error:
            attempt.status = "failed"
            attempt.error_code = classify_error(error).value
            raise
        finally:
            attempt.finished_at = utc_now()
