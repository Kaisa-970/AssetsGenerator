from __future__ import annotations

import json

import pytest
from PIL import Image
from test_workflow import ContractBackend

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.backend_registry import BackendRegistry, resolve_plan
from assets_generator.contracts import ContractError
from assets_generator.models import ArtifactRef
from assets_generator.pipeline import load_default_operator_specs, load_default_pipeline
from assets_generator.scene_extraction import extract_scene_objects


def setup(tmp_path, backend=None):
    Image.new("RGB", (8, 6), "red").save(tmp_path / "scene.png")
    Image.new("L", (8, 6), 255).save(tmp_path / "mask.png")
    manifest = tmp_path / "input.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "image": "scene.png",
                "objects": [{"object_id": name, "mask": "mask.png"} for name in ["chair", "table"]],
            }
        )
    )
    registry = BackendRegistry()
    registry.register(
        name="trellis2",
        operator="shape_generation@1",
        backend_version="test",
        implementation=backend or ContractBackend(),
    )
    plan = resolve_plan(
        load_default_pipeline(), registry, operator_specs=load_default_operator_specs()
    )
    return dict(
        manifest_path=manifest,
        store_path=tmp_path / "store",
        output_path=tmp_path / "output",
        resolved_plan=plan,
    )


def test_extraction_roundtrip_and_child_releases(tmp_path):
    args = setup(tmp_path)
    result = extract_scene_objects(**args)
    store = LocalArtifactStore(args["store_path"])
    raw = store.read_structured(result.manifest)
    assert raw == json.loads((result.output_directory / "extraction.json").read_text())
    assert raw["pose_estimation"] == "not_performed"
    assert raw["scene_assembly"] == "not_performed"
    assert set(result.releases) == {"chair", "table"}
    request = store.read_structured(ArtifactRef(**raw["request"]))
    assert request["segmentation_source"] == "user"
    assert store.verify_digest(ArtifactRef(**request["image"]))
    for obj in raw["objects"]:
        assert store.verify_digest(ArtifactRef(**obj["mask"]))
        assert store.get_build_run(obj["run_id"])["status"] == "succeeded"
        release = store.read_structured(ArtifactRef(**obj["release"]))
        for name, ref in release["files"].items():
            assert (result.output_directory / obj["directory"] / name).read_bytes() == (
                store.blob_path(ArtifactRef(**ref)).read_bytes()
            )
    assert store.get_build_run(result.run_id)["status"] == "succeeded"
    with pytest.raises(FileExistsError):
        extract_scene_objects(**args)


@pytest.mark.parametrize("identifier", ["chair", "../escape", "", "a/b"])
def test_duplicate_or_unsafe_object_ids_rejected(tmp_path, identifier):
    args = setup(tmp_path)
    raw = json.loads(args["manifest_path"].read_text())
    raw["objects"][1]["object_id"] = identifier
    args["manifest_path"].write_text(json.dumps(raw))
    with pytest.raises(ContractError, match="object_id"):
        extract_scene_objects(**args)
    assert not args["output_path"].exists()


def test_invalid_later_mask_fails_before_generation(tmp_path):
    class NeverBackend:
        def generate(self, *args, **kwargs):
            pytest.fail("all masks must be validated before model execution")

    args = setup(tmp_path, NeverBackend())
    Image.new("L", (8, 6), 0).save(tmp_path / "empty.png")
    raw = json.loads(args["manifest_path"].read_text())
    raw["objects"][1]["mask"] = "empty.png"
    args["manifest_path"].write_text(json.dumps(raw))
    with pytest.raises(Exception, match="foreground"):
        extract_scene_objects(**args)
    assert not args["output_path"].exists()


def test_later_failure_preserves_runs_but_no_partial_package(tmp_path):
    class FailSecond(ContractBackend):
        calls = 0

        def generate(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("second object failed")
            return super().generate(*args, **kwargs)

    args = setup(tmp_path, FailSecond())
    with pytest.raises(Exception, match="second object failed"):
        extract_scene_objects(**args)
    assert not args["output_path"].exists()
    store = LocalArtifactStore(args["store_path"])
    runs = [store.get_build_run(path.stem) for path in (store.root / "runs").glob("*.json")]
    parent = next(run for run in runs if run["pipeline_name"] == "scene_extraction")
    children = [run for run in runs if run is not parent]
    assert parent["status"] == "failed"
    assert [node["status"] for node in parent["node_attempts"]] == ["succeeded", "failed"]
    assert sorted(run["status"] for run in children) == ["failed", "succeeded"]


def test_failed_child_linkage_is_exact_across_batches(tmp_path):
    from test_workflow import FailingBackend

    args = setup(tmp_path, FailingBackend())
    store = LocalArtifactStore(args["store_path"])
    for _ in range(2):
        with pytest.raises(Exception, match="backend exploded"):
            extract_scene_objects(**args)
    runs = [store.get_build_run(path.stem) for path in (store.root / "runs").glob("*.json")]
    parents = [run for run in runs if run["pipeline_name"] == "scene_extraction"]
    child_ids = set()
    assert len(parents) == 2
    for parent in parents:
        attempt = parent["node_attempts"][0]
        assert attempt["status"] == "failed"
        reference = ArtifactRef(**attempt["outputs"]["child_run"])
        assert store.verify_digest(reference)
        child = store.read_structured(reference)
        assert child["status"] == "failed"
        assert child == store.get_build_run(child["run_id"])
        child_ids.add(child["run_id"])
    assert len(child_ids) == 2
    assert child_ids == {
        run["run_id"] for run in runs if run["pipeline_name"] != "scene_extraction"
    }


def test_explicit_child_run_id_cannot_overwrite_existing_or_escape(tmp_path):
    from assets_generator.workflow import build_image_asset

    args = setup(tmp_path)
    kwargs = dict(
        image_path=tmp_path / "scene.png",
        mask_path=tmp_path / "mask.png",
        store_path=args["store_path"],
        output_path=args["output_path"],
        resolved_plan=args["resolved_plan"],
    )
    with pytest.raises(ContractError, match="invalid explicit"):
        build_image_asset(**kwargs, run_id="../outside")
    result = build_image_asset(**kwargs, run_id="run_explicit")
    assert result.run_id == "run_explicit"
    store = LocalArtifactStore(args["store_path"])
    original = store.get_build_run(result.run_id)
    with pytest.raises(ContractError, match="already exists"):
        build_image_asset(**kwargs, run_id="run_explicit")
    assert store.get_build_run(result.run_id) == original
