"""CPU-only previews, result collection, and resumable manifest execution."""

from __future__ import annotations

import argparse
import base64
import html
import io
import json
import subprocess
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import quote

from PIL import Image

from .backends.model_identity import snapshot_digest
from .serialization import sha256_bytes


def execution_configuration(args: argparse.Namespace) -> dict[str, Any]:
    backend = getattr(args, "shape_backend", "trellis2")
    model = (
        getattr(args, "model", None)
        or {
            "trellis2": "microsoft/TRELLIS.2-4B",
            "triposr": "stabilityai/TripoSR",
        }[backend]
    )
    local_model = Path(model).expanduser()
    return {
        "shape_backend": backend,
        "model": str(local_model.resolve()) if local_model.is_dir() else model,
        "local_model_digest": snapshot_digest(local_model) if local_model.is_dir() else None,
        "python": str(Path(args.python).expanduser().resolve()),
        "repo": str(Path(args.repo).expanduser().resolve()),
        "seed": 42,
        "pipeline_type": "512",
    }


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_matching_report(
    path: Path,
    image_digest: str,
    mask_digest: str,
    configuration: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text())
        if not isinstance(loaded, dict):
            return None
        report: dict[str, Any] = loaded
        if report["image_digest"] != image_digest or report["mask_digest"] != mask_digest:
            return None
        if configuration is not None and report.get("execution_configuration") != configuration:
            return None
        if not isinstance(report["cases"], list):
            return None
        return report
    except (OSError, ValueError, KeyError, TypeError):
        return None


def matching_report(path: Path, image: Path, mask: Path, mode: str) -> bool:
    report = load_matching_report(
        path, sha256_bytes(image.read_bytes()), sha256_bytes(mask.read_bytes())
    )
    expected = {mode} if mode != "both" else {"provided", "automatic"}
    return bool(report and expected <= {row["mode"] for row in report["cases"]})


def requested_results(report: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    expected = {mode} if mode != "both" else {"provided", "automatic"}
    results = [row for row in report["cases"] if row.get("mode") in expected]
    return results if expected <= {row.get("mode") for row in results} else []


def thumbnail(path: Path) -> str:
    with Image.open(path) as source:
        image = source.convert("RGB")
        image.thumbnail((280, 210))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def collect(
    cases: list[dict[str, Any]],
    manifest: Path,
    output: Path,
    configuration: dict[str, Any] | None = None,
) -> None:
    rows = []
    cards = []
    for index, case in enumerate(cases):
        image = manifest.parent / case["image"]
        mask = manifest.parent / case["mask"]
        image_digest = sha256_bytes(image.read_bytes())
        mask_digest = sha256_bytes(mask.read_bytes())
        reports = []
        for report_path in sorted((output / f"case-{index:03d}").glob("**/report.json")):
            report = load_matching_report(report_path, image_digest, mask_digest, configuration)
            if report is not None:
                reports.append((report_path, report))
        row: dict[str, Any] = {"id": case["id"], "status": "not_run", "attempts": []}
        for report_path, report in reports:
            row["attempts"].append(
                {
                    "report": str(report_path),
                    "results": report["cases"],
                    "execution_configuration": report.get("execution_configuration"),
                }
            )
            row["status"] = ", ".join(r["status"] for r in report["cases"])
        rows.append(row)
        metrics = []
        for attempt in row["attempts"]:
            for result in attempt["results"]:
                peaks = [
                    p.get("parameters", {}).get("peak_cuda_memory_mb")
                    for p in result.get("provenance", [])
                ]
                metrics.append(
                    {
                        "execution_configuration": attempt["execution_configuration"],
                        "mode": result["mode"],
                        "status": result["status"],
                        "seconds": round(result.get("elapsed_seconds", 0), 1),
                        "peak_cuda_allocated_mb": peaks,
                        "QA": result.get("quality", {}).get("overall_status"),
                        "error": result.get("error"),
                    }
                )
        links = "".join(
            '<div class="model-panel">'
            f'<button data-model="{html.escape(quote(p.relative_to(output).as_posix()))}" '
            f'data-label="{html.escape(case["id"])}">查看模型</button>'
            f'<a href="{html.escape(quote(p.relative_to(output).as_posix()))}" download>'
            "下载 GLB</a>"
            f"<span>{html.escape(str(p.relative_to(output)))}</span>"
            '<div class="model-status" role="status"></div></div>'
            for report_path, _ in reports
            for p in sorted(report_path.parent.glob("*/release/geometry/visual.glb"))
        )
        metadata = {
            key: case[key]
            for key in (
                "category",
                "image_size",
                "bbox_size",
                "foreground_fraction",
                "touches_image_boundary",
                "selection_reason",
                "review_status",
            )
            if key in case
        }
        cards.append(
            f"<article><h2>{html.escape(case['id'])}</h2>"
            f'<img alt="original" src="{thumbnail(image)}"> '
            f'<img alt="target mask" src="{thumbnail(mask)}">'
            f"<p>{html.escape(row['status'])}</p>{links}"
            f"<pre>{html.escape(json.dumps(metadata, ensure_ascii=False, indent=2))}</pre>"
            f"<pre>{html.escape(json.dumps(metrics, indent=2))}</pre></article>"
        )
    write_json(output / "review-summary.json", rows)
    (output / "review.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Benchmark review</title>'
        "<style>body{font:16px sans-serif;margin:24px}article{border-top:1px solid #aaa;"
        "padding:16px}pre{white-space:pre-wrap}img{vertical-align:top}</style>"
        "<h1>Original / target mask</h1><p>Visual review pending. "
        "CUDA memory is allocator peak, not total device memory.</p>"
        + files("assets_generator.resources").joinpath("review-viewer.html").read_text()
        + "".join(cards),
        encoding="utf-8",
    )


def run_manifest(args: argparse.Namespace) -> int:
    cases = json.loads(args.manifest.read_text())["cases"]
    ids = [c["id"] for c in cases]
    if len(set(ids)) != len(ids) or set(args.case_id) - set(ids):
        raise ValueError("duplicate or unknown case IDs")
    if args.limit is not None and args.limit <= 0:
        raise ValueError("limit must be positive")
    selected = [(i, c) for i, c in enumerate(cases) if not args.case_id or c["id"] in args.case_id]
    if args.limit:
        selected = selected[: args.limit]
    args.output.mkdir(parents=True, exist_ok=args.resume or args.preview_only)
    if args.preview_only:
        collect(cases, args.manifest, args.output)
        return 0
    if not args.python or not args.repo:
        raise ValueError("execution requires --python and --repo")
    configuration = execution_configuration(args)
    failed = False
    for index, case in selected:
        if (args.output / "STOP").exists():
            break
        image = (args.manifest.parent / case["image"]).resolve()
        mask = (args.manifest.parent / case["mask"]).resolve()
        image_digest = sha256_bytes(image.read_bytes())
        mask_digest = sha256_bytes(mask.read_bytes())
        destination = args.output / f"case-{index:03d}"
        reports = sorted(destination.glob("**/report.json"))
        if args.resume:
            matching = [
                report
                for path in reports
                if (
                    report := load_matching_report(
                        path,
                        image_digest,
                        mask_digest,
                        configuration,
                    )
                )
                and requested_results(report, args.mode)
            ]
            if matching:
                results = requested_results(matching[-1], args.mode)
                failed |= any(row.get("status") == "failed" for row in results)
                continue
        if destination.exists():
            number = 1
            while (destination / f"retry-{number:03d}").exists():
                number += 1
            destination = destination / f"retry-{number:03d}"
        command = [
            sys.executable,
            "-m",
            "assets_generator.benchmark",
            "--image",
            str(image),
            "--mask",
            str(mask),
            "--output",
            str(destination),
            "--python",
            str(args.python),
            "--repo",
            str(args.repo),
            "--mode",
            args.mode,
            "--shape-backend",
            configuration["shape_backend"],
            "--model",
            configuration["model"],
        ]
        with (args.output / f"case-{index:03d}.log").open("a") as log:
            result = subprocess.run(command, stdout=log, stderr=log, check=False)
        failed |= result.returncode != 0
        collect(cases, args.manifest, args.output, configuration)
    collect(cases, args.manifest, args.output, configuration)
    return int(failed)
