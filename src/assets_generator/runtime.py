from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import TypeVar

from .artifact_store import LocalArtifactStore
from .contracts import (
    OperatorSpec,
    validate_operator_inputs,
    validate_operator_outputs,
    validate_port_value,
)
from .models import NodeAttempt, PortValue
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

    def validate_pipeline_inputs(self, inputs: dict[str, PortValue]) -> None:
        unknown = set(inputs) - set(self.pipeline.inputs)
        if unknown:
            raise ValueError(f"pipeline received unknown inputs: {sorted(unknown)}")
        for name, spec in self.pipeline.inputs.items():
            validate_port_value(
                operator=self.pipeline.name,
                port_name=name,
                spec=spec,
                value=inputs.get(name),
                store=self.store,
            )

    def run_node(
        self,
        node_id: str,
        inputs: dict[str, PortValue | list[PortValue]],
        execute: Callable[[], tuple[T, dict[str, PortValue | list[PortValue]]]],
        *,
        backend: str | None = None,
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
            execution_mode="executed",
            started_at=utc_now(),
            finished_at=None,
            error_code=None,
        )
        self.attempts.append(attempt)
        try:
            validate_operator_inputs(spec, inputs, self.store)
            result, outputs = execute()
            validate_operator_outputs(spec, outputs, self.store)
            attempt.outputs = outputs
            attempt.status = "succeeded"
            return result
        except Exception as error:
            attempt.status = "failed"
            attempt.error_code = type(error).__name__
            raise
        finally:
            attempt.finished_at = utc_now()
