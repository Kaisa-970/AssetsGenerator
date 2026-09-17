import json
import sys
from pathlib import Path

import pytest

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.cli import main
from assets_generator.models import StructuredValue


def invoke(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["assets-generator", *map(str, args)])
    assert main() == 0
    return json.loads(capsys.readouterr().out)


def test_inspect_verifies_artifact_and_run(tmp_path, monkeypatch, capsys):
    store = LocalArtifactStore(tmp_path / "store")
    ref = store.record_build_run(
        "run_test", StructuredValue("build_run", "BuildRun", "1.0", {"status": "failed"})
    )
    result = invoke(monkeypatch, capsys, "inspect", "--store", store.root, "--run", "run_test")
    assert result["value"]["status"] == "failed"
    assert result["manifest"]["artifact_id"] == ref.artifact_id
    store.blob_path(ref).write_bytes(b"corrupted")
    monkeypatch.setattr(
        sys,
        "argv",
        ["assets-generator", "inspect", "--store", str(store.root), "--artifact", ref.artifact_id],
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "verification failed" in capsys.readouterr().err


def test_inspect_rejects_run_path_traversal(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        ["assets-generator", "inspect", "--store", str(tmp_path), "--run", "../outside"],
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "invalid run ID" in capsys.readouterr().err


def test_multi_view_cli_binds_explicit_environments(tmp_path, monkeypatch, capsys):
    from assets_generator import multi_view_workflow

    def build(**kwargs):
        plan = kwargs["resolved_plan"]
        da3 = plan.backend_for("estimate_geometry", "geometry_frontend@1").implementation
        tsdf = plan.backend_for("reconstruct", "reconstruction@1").implementation
        assert da3.python == Path("/env/da3/bin/python")
        assert da3.process_res == 518
        assert tsdf.python == Path("/env/open3d/bin/python")
        assert tsdf.voxel_size_ratio == 0.02
        assert tsdf.up_axis == "-Z"
        assert kwargs["export_appearance_mode"] == "preserve_mesh"
        return {"run_id": "run_test"}

    monkeypatch.setattr(multi_view_workflow, "build_multi_view_asset", build)
    result = invoke(
        monkeypatch,
        capsys,
        "build-multi-view",
        "--observations",
        "sha256:" + "a" * 64,
        "--store",
        tmp_path,
        "--output",
        tmp_path / "output",
        "--da3-python",
        "/env/da3/bin/python",
        "--da3-repo",
        "/repo/da3",
        "--da3-model",
        "/models/da3",
        "--open3d-python",
        "/env/open3d/bin/python",
        "--da3-process-res",
        "518",
        "--voxel-size-ratio",
        "0.02",
        "--up-axis=-Z",
    )
    assert result["run_id"] == "run_test"


def test_candidate_cli_runs_existing_workflow(tmp_path, monkeypatch, capsys):
    from test_completion import setup_candidate

    from assets_generator import cli

    store, obs, original, plan = setup_candidate(tmp_path)
    monkeypatch.setattr(cli, "resolve_plan", lambda *args, **kwargs: plan)
    reference = tmp_path / "observations-ref.json"
    reference.write_text(json.dumps({"artifact_id": obs.artifact_id}))
    result = invoke(
        monkeypatch,
        capsys,
        "build-candidate",
        "--observations",
        reference,
        "--reconstruction-release",
        original.release_manifest.artifact_id,
        "--view-id",
        "front",
        "--store",
        store.root,
        "--output",
        tmp_path / "candidate",
    )
    from assets_generator.models import ArtifactRef

    candidate = store.read_structured(ArtifactRef(**result["manifest"]))
    assert candidate["selected_view_id"] == "front"
    assert candidate["fusion"] == "not_performed"


def test_review_cli_closes_server_on_interrupt(tmp_path, monkeypatch, capsys):
    from assets_generator import alignment_review

    seen = {}

    class Server:
        server_port = 12345

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            seen["closed"] = True

    monkeypatch.setattr(alignment_review, "AlignmentReviewSession", lambda *args: args)
    monkeypatch.setattr(alignment_review, "create_review_server", lambda *args: Server())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "assets-generator",
            "review-candidate",
            "--candidate",
            "sha256:" + "a" * 64,
            "--store",
            str(tmp_path),
            "--output",
            str(tmp_path / "review"),
        ],
    )
    assert main() == 0
    assert seen["closed"]
    assert "http://127.0.0.1:12345/" in capsys.readouterr().out


def test_instance_review_cli_closes_server_on_interrupt(tmp_path, monkeypatch, capsys):
    from assets_generator import instance_review

    seen = {}

    class Server:
        server_port = 23456

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            seen["closed"] = True

    monkeypatch.setattr(instance_review, "InstanceReviewSession", lambda *args: args)
    monkeypatch.setattr(instance_review, "create_instance_review_server", lambda *args: Server())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "assets-generator",
            "review-instances",
            "--proposals",
            "sha256:" + "a" * 64,
            "--store",
            str(tmp_path),
            "--output",
            str(tmp_path / "selection"),
            "--port",
            "0",
        ],
    )
    assert main() == 0
    assert seen["closed"]
    assert "http://127.0.0.1:23456/" in capsys.readouterr().out


def test_scene_layout_review_cli_closes_server_on_interrupt(tmp_path, monkeypatch, capsys):
    from assets_generator import scene_layout_review

    seen = {}

    class Server:
        server_port = 34567

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            seen["closed"] = True

    monkeypatch.setattr(scene_layout_review, "SceneLayoutReviewSession", lambda *args: args)
    monkeypatch.setattr(
        scene_layout_review, "create_scene_layout_review_server", lambda *args: Server()
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "assets-generator",
            "review-scene-layout",
            "--manifest",
            str(tmp_path / "draft.json"),
            "--store",
            str(tmp_path / "store"),
            "--output",
            str(tmp_path / "scene"),
            "--port",
            "0",
        ],
    )
    assert main() == 0
    assert seen["closed"]
    assert "http://127.0.0.1:34567/" in capsys.readouterr().out
