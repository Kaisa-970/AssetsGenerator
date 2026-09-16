"""Run a local, explicitly selected smoke dataset with fresh artifact stores."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from .artifact_store import LocalArtifactStore
from .backend_registry import BackendRegistry, ShapeBackend, resolve_plan
from .errors import classify_error
from .models import ArtifactRef
from .operators import BiRefNetSegmentationBackend, Trellis2Backend, TripoSRBackend
from .pipeline import load_default_operator_specs, load_default_pipeline
from .serialization import sha256_bytes
from .workflow import build_image_asset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--image", type=Path)
    inputs.add_argument("--manifest", type=Path)
    parser.add_argument("--mode", choices=["both", "provided", "automatic"], default="both")
    parser.add_argument("--mask", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--preview-only", action="store_true")
    parser.add_argument("--python", type=Path)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--shape-backend", choices=["trellis2", "triposr"], default="trellis2")
    parser.add_argument("--model")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--pipeline-type", choices=["512", "1024", "1024_cascade", "1536_cascade"], default="512"
    )
    parser.add_argument("--backend-timeout", type=float, default=1800.0)
    parser.add_argument("--triposr-timeout", type=float, default=900.0)
    parser.add_argument("--triposr-chunk-size", type=int, default=8192)
    parser.add_argument("--triposr-mc-resolution", type=int, default=256)
    parser.add_argument("--triposr-foreground-ratio", type=float, default=0.85)
    args = parser.parse_args()
    if args.manifest:
        from .benchmark_review import run_manifest

        return run_manifest(args)
    if not args.python or not args.repo:
        parser.error("execution requires --python and --repo")
    from .benchmark_review import execution_configuration

    configuration = execution_configuration(args)
    registry = BackendRegistry()
    implementation: ShapeBackend
    if args.shape_backend == "triposr":
        implementation = TripoSRBackend(
            args.python,
            args.repo,
            configuration["model"],
            timeout_seconds=args.triposr_timeout,
            chunk_size=args.triposr_chunk_size,
            mc_resolution=args.triposr_mc_resolution,
            foreground_ratio=args.triposr_foreground_ratio,
        )
    else:
        implementation = Trellis2Backend(
            args.python,
            args.repo,
            configuration["model"],
            timeout_seconds=args.backend_timeout,
        )
    registry.register(
        name=args.shape_backend,
        operator="shape_generation@1",
        backend_version="1.0.0",
        implementation=implementation,
    )
    plan = resolve_plan(
        load_default_pipeline(),
        registry,
        operator_specs=load_default_operator_specs(),
        backend_overrides={"generate_shape": args.shape_backend},
    )
    args.output.mkdir(parents=True, exist_ok=False)
    rows: list[dict[str, Any]] = []
    hardware = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    source_digest = configuration["core_source_digest"]
    modes = ["provided", "automatic"] if args.mask else ["automatic"]
    if args.mode != "both":
        modes = [args.mode]
    if "provided" in modes and not args.mask:
        parser.error("provided mode requires --mask")
    for mode in modes:
        started = time.monotonic()
        store_path = args.output / mode / "store"
        row: dict[str, Any] = {
            "mode": mode,
            "seed": args.seed,
            "pipeline_type": args.pipeline_type,
        }
        try:
            result = build_image_asset(
                image_path=args.image,
                mask_path=args.mask if mode == "provided" else None,
                store_path=store_path,
                output_path=args.output / mode / "release",
                resolved_plan=plan,
                segmentation_backend=BiRefNetSegmentationBackend(args.python),
                seed=args.seed,
                pipeline_type=args.pipeline_type,
            )
            store = LocalArtifactStore(store_path)
            release = json.loads((result.output_directory / "release.json").read_text())
            row["release_digests_valid"] = all(
                store.verify_digest(ArtifactRef(ref["artifact_id"]))
                for ref in release["files"].values()
            )
            row["quality"] = json.loads(
                (result.output_directory / "qa/quality-report.json").read_text()
            )
            run = store.get_build_run(result.run_id)
            row["node_attempts"] = run["node_attempts"]
            row["provenance"] = [
                json.loads(path.read_text())
                for path in sorted((result.output_directory / "provenance").glob("*.json"))
            ]
            row["run_id"] = result.run_id
            row["status"] = "succeeded"
        except Exception as error:
            row.update(status="failed", error_code=classify_error(error).value, error=str(error))
            if (store_path / "runs").is_dir():
                store = LocalArtifactStore(store_path)
                row["build_runs"] = [
                    store.get_build_run(path.stem) for path in (store_path / "runs").glob("*.json")
                ]
        row["elapsed_seconds"] = time.monotonic() - started
        rows.append(row)
        (args.output / "report.json").write_text(
            json.dumps(
                {
                    "scope": "single-object smoke baseline; not representative benchmark",
                    "image_digest": sha256_bytes(args.image.read_bytes()),
                    "mask_digest": sha256_bytes(args.mask.read_bytes()) if args.mask else None,
                    "hardware": hardware,
                    "execution_configuration": configuration,
                    "source_digest": source_digest,
                    "cases": rows,
                    "manual_review": "pending",
                    "render_back": "skipped: no camera registration",
                },
                indent=2,
            )
        )
    return int(any(row["status"] == "failed" for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
