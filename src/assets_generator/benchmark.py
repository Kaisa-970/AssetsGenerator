"""Run a local, explicitly selected smoke dataset with fresh artifact stores."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from .artifact_store import LocalArtifactStore
from .errors import classify_error
from .models import ArtifactRef
from .operators import BiRefNetSegmentationBackend, Trellis2Backend
from .serialization import sha256_bytes
from .workflow import build_image_asset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--mask", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    rows: list[dict[str, Any]] = []
    hardware = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    source_digest = sha256_bytes(
        b"".join(
            path.relative_to(Path(__file__).parent).as_posix().encode() + path.read_bytes()
            for path in sorted(Path(__file__).parent.rglob("*.py"))
        )
    )
    for mode in ["provided", "automatic"] if args.mask else ["automatic"]:
        started = time.monotonic()
        store_path = args.output / mode / "store"
        row: dict[str, Any] = {"mode": mode, "seed": 42, "pipeline_type": "512"}
        try:
            result = build_image_asset(
                image_path=args.image,
                mask_path=args.mask if mode == "provided" else None,
                store_path=store_path,
                output_path=args.output / mode / "release",
                backend=Trellis2Backend(args.python, args.repo),
                segmentation_backend=BiRefNetSegmentationBackend(args.python),
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
