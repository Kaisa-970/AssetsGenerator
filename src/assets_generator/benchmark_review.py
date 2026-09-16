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

from .serialization import sha256_bytes


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def matching_report(path: Path, image: Path, mask: Path, mode: str) -> bool:
    try:
        report = json.loads(path.read_text())
        expected = {mode} if mode != "both" else {"provided", "automatic"}
        return bool(
            report["image_digest"] == sha256_bytes(image.read_bytes())
            and report["mask_digest"] == sha256_bytes(mask.read_bytes())
            and expected <= {row["mode"] for row in report["cases"]}
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def thumbnail(path: Path) -> str:
    with Image.open(path) as source:
        image = source.convert("RGB")
        image.thumbnail((280, 210))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def collect(cases: list[dict[str, Any]], manifest: Path, output: Path) -> None:
    rows = []
    cards = []
    for index, case in enumerate(cases):
        reports = sorted((output / f"case-{index:03d}").glob("**/report.json"))
        row: dict[str, Any] = {"id": case["id"], "status": "not_run", "attempts": []}
        for report_path in reports:
            try:
                report = json.loads(report_path.read_text())
                row["attempts"].append({"report": str(report_path), "results": report["cases"]})
                row["status"] = ", ".join(r["status"] for r in report["cases"])
            except (OSError, ValueError, KeyError):
                row["status"] = "invalid_report"
        rows.append(row)
        image = manifest.parent / case["image"]
        mask = manifest.parent / case["mask"]
        metrics = []
        for attempt in row["attempts"]:
            for result in attempt["results"]:
                peaks = [
                    p.get("parameters", {}).get("peak_cuda_memory_mb")
                    for p in result.get("provenance", [])
                ]
                metrics.append(
                    {
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
            for p in sorted((output / f"case-{index:03d}").glob("**/geometry/visual.glb"))
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
    failed = False
    for index, case in selected:
        if (args.output / "STOP").exists():
            break
        image = (args.manifest.parent / case["image"]).resolve()
        mask = (args.manifest.parent / case["mask"]).resolve()
        destination = args.output / f"case-{index:03d}"
        reports = list(destination.glob("**/report.json"))
        if args.resume and any(matching_report(p, image, mask, args.mode) for p in reports):
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
        ]
        with (args.output / f"case-{index:03d}.log").open("a") as log:
            result = subprocess.run(command, stdout=log, stderr=log, check=False)
        failed |= result.returncode != 0
        collect(cases, args.manifest, args.output)
    collect(cases, args.manifest, args.output)
    return int(failed)
