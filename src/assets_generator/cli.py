from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import (
    compile_pipeline,
    load_default_operator_specs,
    load_default_pipeline,
    load_operator_specs,
    load_pipeline,
)
from .serialization import to_primitive


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="assets-generator")
    subparsers = parser.add_subparsers(dest="command", required=True)
    compile_command = subparsers.add_parser("compile-pipeline")
    compile_command.add_argument("--pipeline", type=Path)
    compile_command.add_argument("--operators", type=Path)

    build = subparsers.add_parser("build")
    build.add_argument("--image", type=Path, required=True)
    build.add_argument("--mask", type=Path, required=True)
    build.add_argument("--store", type=Path, default=Path("artifact-store"))
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--name")
    build.add_argument("--seed", type=int, default=42)
    build.add_argument(
        "--pipeline-type", choices=["512", "1024", "1024_cascade", "1536_cascade"], default="512"
    )
    build.add_argument(
        "--trellis-repo", type=Path, default=Path("/home/ypkwsl/Workspace/TRELLIS.2")
    )
    build.add_argument(
        "--trellis-python",
        type=Path,
        default=Path("/home/ypkwsl/DevTools/miniconda3/envs/TRELLTS/bin/python"),
    )
    build.add_argument("--trellis-model", default="microsoft/TRELLIS.2-4B")
    build.add_argument("--backend-timeout", type=float, default=1800.0)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "compile-pipeline":
        specs = (
            load_operator_specs(args.operators) if args.operators else load_default_operator_specs()
        )
        pipeline = load_pipeline(args.pipeline) if args.pipeline else load_default_pipeline()
        compile_pipeline(pipeline, specs)
        print(f"{pipeline.name}@{pipeline.version}: valid")
        return 0
    from .operators import Trellis2Backend
    from .workflow import build_image_asset

    backend = Trellis2Backend(
        args.trellis_python,
        args.trellis_repo,
        args.trellis_model,
        timeout_seconds=args.backend_timeout,
    )
    result = build_image_asset(
        image_path=args.image,
        mask_path=args.mask,
        store_path=args.store,
        output_path=args.output,
        backend=backend,
        seed=args.seed,
        pipeline_type=args.pipeline_type,
        asset_name=args.name,
    )
    print(json.dumps(to_primitive(result), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
