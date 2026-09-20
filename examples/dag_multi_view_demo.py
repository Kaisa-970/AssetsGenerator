"""Run the DA3 -> Open3D -> release YAML DAG with explicit local resources.

Use PYTHONPATH=src python examples/dag_multi_view_demo.py --help. Resource
identities are recomputed on resume/retry; incompatible resources fail closed.
No models or Python environments are downloaded or installed by this script.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import fields
from pathlib import Path

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.dag_adapters import AdapterRegistry
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_multi_view import MultiViewProfile
from assets_generator.dag_persistence import DagRepository
from assets_generator.dag_profiles import register_multi_view_profiles
from assets_generator.models import ArtifactRef
from assets_generator.multi_view_profiles import MultiViewProfileConfig, load_multi_view_profile
from assets_generator.multi_view_relations import register_multi_view_relations
from assets_generator.pipeline import (
    compile_pipeline,
    load_operator_specs,
    load_pipeline,
)
from assets_generator.relations import default_relation_registry
from assets_generator.serialization import to_primitive


def profile(args: argparse.Namespace) -> MultiViewProfile:
    return load_multi_view_profile(
        MultiViewProfileConfig(
            **{field.name: getattr(args, field.name) for field in fields(MultiViewProfileConfig)}
        )
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=("start", "resume", "retry"))
    result.add_argument("--store", required=True, type=Path)
    result.add_argument("--directory", required=True, type=Path)
    result.add_argument("--observations", help="existing ObservationBundle Artifact ID (start)")
    result.add_argument("--run", help="existing parent DAG run ID (resume/retry)")
    result.add_argument("--node", help="failed/interrupted node ID (retry)")
    result.add_argument("--expected-revision", type=int, help="current run revision (retry)")
    for name in ("da3-python", "da3-repo", "da3-model", "open3d-python"):
        result.add_argument(f"--{name}", required=True)
    result.add_argument("--process-res", type=int, default=392)
    result.add_argument("--da3-timeout", type=float, default=1800.0)
    result.add_argument("--open3d-timeout", type=float, default=1800.0)
    result.add_argument("--voxel-size-ratio", type=float, default=0.01)
    result.add_argument("--sdf-trunc-ratio", type=float, default=0.04)
    result.add_argument("--depth-trunc-ratio", type=float, default=3.0)
    result.add_argument("--up-axis", choices=("+X", "-X", "+Y", "-Y", "+Z", "-Z"), default="-Y")
    return result


def main() -> None:
    cli = parser()
    args = cli.parse_args()
    if args.command == "start":
        if not args.observations or args.run or args.node or args.expected_revision is not None:
            cli.error("start requires --observations and rejects retry/resume options")
    elif not args.run or args.observations:
        cli.error("resume/retry requires --run and rejects --observations")
    if args.command == "retry" and (not args.node or args.expected_revision is None):
        cli.error("retry requires --node and --expected-revision")
    if args.command == "resume" and (args.node or args.expected_revision is not None):
        cli.error("resume rejects retry options")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    local_profile = profile(args)
    adapters = AdapterRegistry()
    register_multi_view_profiles(adapters, {"local-multiview": local_profile}, "local-multiview")
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
    store = LocalArtifactStore(args.store)
    repository = DagRepository(store, args.directory)
    with repository:
        engine = DagEngine(repository, adapters, relations)
        if args.command == "start":
            run = engine.create(plan, {"observations": ArtifactRef(args.observations)})
            print(f"run_id={run.run_id}", flush=True)
            run = engine.drain(run.run_id)
        elif args.command == "resume":
            run = engine.drain(args.run)
        else:
            run = engine.retry(args.run, args.node, args.expected_revision)
        print(json.dumps(to_primitive(run), indent=2))


if __name__ == "__main__":
    main()
