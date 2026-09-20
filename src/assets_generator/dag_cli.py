"""Local YAML single-image DAG command entry, reusing installed model profiles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .artifact_store import LocalArtifactStore
from .dag_adapters import AdapterRegistry
from .dag_engine import DagEngine
from .dag_image_adapters import DagImageBuildAdapter, DagMaskSelectionAdapter, DagProposalAdapter
from .dag_persistence import DagRepository
from .pipeline import compile_pipeline, load_default_operator_specs, load_pipeline
from .serialization import read_json, to_primitive
from .workbench_profiles import load_profiles
from .workflow import _import_image


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    command = subparsers.add_parser("dag-image", help="Run/resume a YAML single-image DAG")
    command.add_argument("action", choices=("start", "resume", "decide", "retry", "review"))
    for name in ("config", "store", "directory"):
        command.add_argument(f"--{name}", type=Path, required=True)
    command.add_argument("--profile", required=True)
    command.add_argument("--pipeline", type=Path)
    command.add_argument("--image", type=Path)
    command.add_argument("--run")
    command.add_argument("--node")
    command.add_argument("--expected-revision", type=int)
    command.add_argument("--decision", type=Path)
    command.add_argument("--key")
    command.add_argument("--reviewer")
    command.add_argument("--port", type=int, default=8765)


def execute(args: argparse.Namespace) -> int:
    profiles = load_profiles(read_json(args.config))
    if args.profile not in profiles:
        raise ValueError(f"unknown profile: {args.profile}")
    profile = profiles[args.profile]
    registry = AdapterRegistry()
    for adapter in (
        DagProposalAdapter(profile),
        DagMaskSelectionAdapter(),
        DagImageBuildAdapter(profile),
    ):
        registry.register(adapter)
    store = LocalArtifactStore(args.store)
    repo = DagRepository(store, args.directory)
    with repo:
        engine = DagEngine(repo, registry)
        if args.action == "start":
            if args.pipeline is None or args.image is None:
                raise ValueError("start requires --pipeline and --image")
            plan = registry.bind_plan(
                compile_pipeline(
                    load_pipeline(args.pipeline),
                    load_default_operator_specs(),
                    require_explicit_joins=True,
                )
            )
            image = _import_image(store, args.image, "rgb_image")
            run = engine.drain(engine.create(plan, {"image": image}).run_id)
        else:
            if not args.run:
                raise ValueError("action requires --run")
            if args.action == "review":
                if not args.node:
                    raise ValueError("review requires --node")
                from .dag_review import DagMaskReviewService, create_dag_review_server

                service = DagMaskReviewService(engine, args.run, args.node)
                server = create_dag_review_server(service, args.port)
                print(f"http://127.0.0.1:{server.server_port}/ (Ctrl+C 关闭)", flush=True)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
                finally:
                    server.server_close()
                    service.close()
                return 0
            if args.action == "resume":
                run = engine.drain(args.run)
            else:
                if args.node is None or args.expected_revision is None:
                    raise ValueError("retry/decide require --node and --expected-revision")
                if args.action == "retry":
                    run = engine.retry(args.run, args.node, args.expected_revision)
                else:
                    if not args.key or not args.reviewer or args.decision is None:
                        raise ValueError("decide requires --key, --reviewer and --decision JSON")
                    run = engine.decide(
                        args.run,
                        args.node,
                        expected_revision=args.expected_revision,
                        idempotency_key=args.key,
                        reviewer=args.reviewer,
                        payload=read_json(args.decision),
                    )
        print(json.dumps(to_primitive(run), indent=2))
    return 0
