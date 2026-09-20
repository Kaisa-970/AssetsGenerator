"""Run a CPU-only YAML diamond without model downloads or production state."""

from __future__ import annotations

import argparse
from pathlib import Path

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.dag_adapters import (
    AdapterRegistry,
    AdapterSpec,
    NodeExecutionContext,
    NodeExecutionResult,
)
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.models import ArtifactRef
from assets_generator.pipeline import compile_pipeline, load_operator_specs, load_pipeline


class Copy:
    spec = AdapterSpec("demo_copy", "1", ("demo_copy@1",))

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        print(f"execute {context.node_id}")
        return NodeExecutionResult({"result": context.inputs["source"]})


class Join:
    spec = AdapterSpec("demo_join", "1", ("demo_join@1",))

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        print(f"execute {context.node_id}")
        left, right = context.inputs["left"], context.inputs["right"]
        assert isinstance(left, ArtifactRef) and isinstance(right, ArtifactRef)
        print(f"shared input: {left == right}")
        payload = context.store.blob_path(left).read_bytes() + b" + "
        payload += context.store.blob_path(right).read_bytes()
        result = context.store.persist_bytes(
            payload, kind="quality_evidence", schema_name="DemoText", schema_version="1"
        )
        return NodeExecutionResult({"result": result})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--recover", metavar="RUN_ID")
    args = parser.parse_args()
    base = Path(__file__).parent
    registry = AdapterRegistry()
    registry.register(Copy())
    registry.register(Join())
    static = compile_pipeline(
        load_pipeline(base / "dag-diamond.yaml"),
        load_operator_specs(base / "dag-operators.yaml"),
        require_explicit_joins=True,
    )
    plan = registry.bind_plan(static)
    store = LocalArtifactStore(args.directory / "store")
    with DagRepository(store, args.directory / "runs") as repository:
        engine = DagEngine(repository, registry)
        if args.recover:
            run = engine.recover(args.recover)
        else:
            source = store.persist_bytes(
                b"hello", kind="quality_evidence", schema_name="DemoText", schema_version="1"
            )
            run = engine.create(plan, {"source": source})
            run = engine.drain(run.run_id)
        print(f"{run.run_id}: {run.status}")
        if run.dag is not None:
            for key, node in run.dag.node_states.items():
                print(f"  {key}: {node.status}, attempts={len(node.attempts)}")


if __name__ == "__main__":
    main()
