import argparse
import json

from PIL import Image

from assets_generator.benchmark_review import matching_report, run_manifest
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
