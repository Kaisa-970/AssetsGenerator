import argparse
import json

from PIL import Image

from assets_generator.benchmark_review import collect, matching_report, run_manifest
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

    assert run_manifest(args) == 0
