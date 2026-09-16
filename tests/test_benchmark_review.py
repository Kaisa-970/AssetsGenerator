import argparse
import json

from PIL import Image

from assets_generator.benchmark_review import (
    collect,
    execution_configuration,
    matching_report,
    run_manifest,
)
from assets_generator.serialization import sha256_bytes


def test_resume_checks_content_and_mode(tmp_path):
    image, mask, report = (tmp_path / name for name in ("image", "mask", "report.json"))
    image.write_bytes(b"one")
    mask.write_bytes(b"mask")
    report.write_text(
        json.dumps(
            {
                "image_digest": sha256_bytes(b"one"),
                "mask_digest": sha256_bytes(b"mask"),
                "cases": [{"mode": "provided", "status": "failed"}],
            }
        )
    )
    assert matching_report(report, image, mask, "provided")
    assert not matching_report(report, image, mask, "both")
    image.write_bytes(b"two")
    assert not matching_report(report, image, mask, "provided")


def test_preview_never_launches_gpu_and_selection_keeps_indices(tmp_path, monkeypatch):
    for name in ("image.png", "mask.png"):
        Image.new("L", (4, 4), 255).save(tmp_path / name)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "cases": [
                    {"id": name, "image": "image.png", "mask": "mask.png"} for name in ("a", "b")
                ]
            }
        )
    )
    output = tmp_path / "out"
    args = argparse.Namespace(
        manifest=manifest,
        output=output,
        case_id=["b"],
        limit=1,
        resume=False,
        preview_only=True,
        python="python",
        repo="repo",
        mode="provided",
    )
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return argparse.Namespace(returncode=0)

    monkeypatch.setattr("assets_generator.benchmark_review.subprocess.run", run)
    run_manifest(args)
    assert not calls
    assert "target mask" in (output / "review.html").read_text()
    args.preview_only = False
    args.resume = True
    run_manifest(args)
    assert len(calls) == 1
    assert str(output / "case-001") in calls[0]
    (output / "STOP").touch()
    run_manifest(args)
    assert len(calls) == 1


def test_collect_ignores_reports_and_models_for_replaced_input(tmp_path):
    image = tmp_path / "image.png"
    mask = tmp_path / "mask.png"
    Image.new("RGB", (4, 4), "red").save(image)
    Image.new("L", (4, 4), 255).save(mask)
    manifest = tmp_path / "manifest.json"
    cases = [{"id": "current", "image": image.name, "mask": mask.name}]
    manifest.write_text(json.dumps({"cases": cases}))
    old = tmp_path / "out/case-000/provided/release/geometry"
    old.mkdir(parents=True)
    (old / "visual.glb").write_bytes(b"stale")
    (tmp_path / "out/case-000/report.json").write_text(
        json.dumps(
            {
                "image_digest": sha256_bytes(b"old image"),
                "mask_digest": sha256_bytes(mask.read_bytes()),
                "cases": [{"mode": "provided", "status": "succeeded"}],
            }
        )
    )

    collect(cases, manifest, tmp_path / "out")

    summary = json.loads((tmp_path / "out/review-summary.json").read_text())
    assert summary[0]["status"] == "not_run"
    assert "visual.glb" not in (tmp_path / "out/review.html").read_text()


def test_resume_returns_failure_from_latest_matching_report(tmp_path, monkeypatch):
    for name in ("image.png", "mask.png"):
        Image.new("L", (4, 4), 255).save(tmp_path / name)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"cases": [{"id": "a", "image": "image.png", "mask": "mask.png"}]})
    )
    output = tmp_path / "out"
    case = output / "case-000"
    case.mkdir(parents=True)
    (case / "report.json").write_text(
        json.dumps(
            {
                "image_digest": sha256_bytes((tmp_path / "image.png").read_bytes()),
                "mask_digest": sha256_bytes((tmp_path / "mask.png").read_bytes()),
                "cases": [{"mode": "provided", "status": "failed"}],
            }
        )
    )
    monkeypatch.setattr(
        "assets_generator.benchmark_review.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    args = argparse.Namespace(
        manifest=manifest,
        output=output,
        case_id=[],
        limit=None,
        resume=True,
        preview_only=False,
        python="python",
        repo="repo",
        mode="provided",
    )

    report_path = case / "report.json"
    report = json.loads(report_path.read_text())
    report["execution_configuration"] = execution_configuration(args)
    report_path.write_text(json.dumps(report))
    assert run_manifest(args) == 1


def test_resume_uses_latest_matching_retry_status(tmp_path, monkeypatch):
    for name in ("image.png", "mask.png"):
        Image.new("L", (4, 4), 255).save(tmp_path / name)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"cases": [{"id": "a", "image": "image.png", "mask": "mask.png"}]})
    )
    output = tmp_path / "out"
    digests = {
        "image_digest": sha256_bytes((tmp_path / "image.png").read_bytes()),
        "mask_digest": sha256_bytes((tmp_path / "mask.png").read_bytes()),
    }
    attempts = ((output / "case-000", "failed"), (output / "case-000/retry-001", "succeeded"))
    for directory, status in attempts:
        directory.mkdir(parents=True)
        (directory / "report.json").write_text(
            json.dumps({**digests, "cases": [{"mode": "provided", "status": status}]})
        )
    monkeypatch.setattr(
        "assets_generator.benchmark_review.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    args = argparse.Namespace(
        manifest=manifest,
        output=output,
        case_id=[],
        limit=None,
        resume=True,
        preview_only=False,
        python="python",
        repo="repo",
        mode="provided",
    )

    for directory, _ in attempts:
        report_path = directory / "report.json"
        report = json.loads(report_path.read_text())
        report["execution_configuration"] = execution_configuration(args)
        report_path.write_text(json.dumps(report))
    assert run_manifest(args) == 0


def test_resume_rejects_legacy_or_different_backend_configuration(tmp_path, monkeypatch):
    for name in ("image.png", "mask.png"):
        Image.new("L", (4, 4), 255).save(tmp_path / name)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"cases": [{"id": "a", "image": "image.png", "mask": "mask.png"}]})
    )
    output = tmp_path / "out"
    case = output / "case-000"
    case.mkdir(parents=True)
    args = argparse.Namespace(
        manifest=manifest,
        output=output,
        case_id=[],
        limit=None,
        resume=True,
        preview_only=False,
        python="python",
        repo="repo",
        mode="provided",
        shape_backend="triposr",
        model="stabilityai/TripoSR",
    )
    report = {
        "image_digest": sha256_bytes((tmp_path / "image.png").read_bytes()),
        "mask_digest": sha256_bytes((tmp_path / "mask.png").read_bytes()),
        "cases": [{"mode": "provided", "status": "succeeded"}],
    }
    calls = []
    monkeypatch.setattr(
        "assets_generator.benchmark_review.subprocess.run",
        lambda command, **kwargs: calls.append(command) or argparse.Namespace(returncode=0),
    )
    for old_configuration in (None, {**execution_configuration(args), "shape_backend": "trellis2"}):
        report["execution_configuration"] = old_configuration
        (case / "report.json").write_text(json.dumps(report))
        assert run_manifest(args) == 0
        command = calls[-1]
        assert command[command.index("--shape-backend") + 1] == "triposr"
        assert command[command.index("--model") + 1] == "stabilityai/TripoSR"
        assert json.loads((output / "review-summary.json").read_text())[0]["status"] == "not_run"
    assert len(calls) == 2
    report["execution_configuration"] = execution_configuration(args)
    (case / "report.json").write_text(json.dumps(report))
    assert run_manifest(args) == 0
    assert len(calls) == 2
    args.model = "other/model"
    assert run_manifest(args) == 0
    assert len(calls) == 3


def test_execution_configuration_tracks_local_weights(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    weights = model / "weights.bin"
    weights.write_bytes(b"one")
    args = argparse.Namespace(
        shape_backend="triposr", model=str(model), python="python", repo="repo"
    )
    before = execution_configuration(args)
    weights.write_bytes(b"two")
    assert execution_configuration(args) != before


def test_benchmark_binds_selected_backend_and_records_configuration(tmp_path, monkeypatch):
    import sys

    from assets_generator import benchmark

    image, mask = tmp_path / "image.png", tmp_path / "mask.png"
    Image.new("RGB", (4, 4), "red").save(image)
    Image.new("L", (4, 4), 255).save(mask)
    output = tmp_path / "out"
    implementation = object()
    monkeypatch.setattr(benchmark, "TripoSRBackend", lambda *args: implementation)
    monkeypatch.setattr(
        benchmark.subprocess, "run", lambda *args, **kwargs: argparse.Namespace(stdout="fixture")
    )

    def build(**kwargs):
        binding = kwargs["resolved_plan"].backend_for("generate_shape", "shape_generation@1")
        assert binding.name == "triposr"
        assert binding.implementation is implementation
        assert kwargs["mask_path"] == mask
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(benchmark, "build_image_asset", build)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark",
            "--image",
            str(image),
            "--mask",
            str(mask),
            "--output",
            str(output),
            "--mode",
            "provided",
            "--shape-backend",
            "triposr",
            "--model",
            "custom/model",
            "--python",
            "/fixture/python",
            "--repo",
            "/fixture/repo",
        ],
    )
    assert benchmark.main() == 1
    report = json.loads((output / "report.json").read_text())
    assert report["execution_configuration"]["shape_backend"] == "triposr"
    assert report["execution_configuration"]["model"] == "custom/model"
    assert report["cases"][0]["status"] == "failed"
