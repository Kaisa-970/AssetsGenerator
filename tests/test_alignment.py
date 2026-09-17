from __future__ import annotations

import json

import numpy as np
import pytest
import trimesh
from PIL import Image

from assets_generator.alignment import align_completion_candidate, candidate_frame
from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import ContractError
from assets_generator.models import SpatialTransform, StructuredValue
from assets_generator.operators import _load_scene, _scene_vertices


def fixture(tmp_path, unit="relative_unit"):
    store = LocalArtifactStore(tmp_path / "store")
    scene = trimesh.Scene()
    textured = trimesh.creation.box()
    textured.visual = trimesh.visual.texture.TextureVisuals(
        uv=np.zeros((len(textured.vertices), 2)),
        material=trimesh.visual.material.PBRMaterial(
            baseColorTexture=Image.new("RGB", (2, 2), "red")
        ),
    )
    colored = trimesh.creation.icosphere(subdivisions=0)
    colored.visual.vertex_colors = [20, 40, 80, 255]
    scene.add_geometry(textured, node_name="texture")
    pose = np.eye(4)
    pose[0, 3] = 3
    scene.add_geometry(colored, node_name="colors", transform=pose)
    refs = []
    for label in ["generated", "reconstructed"]:
        mesh = store.persist_bytes(
            scene.export(file_type="glb"),
            kind="gltf_asset",
            schema_name="glTF",
            schema_version="2.0",
            identity_metadata={
                "frame_id": "gltf_export",
                "unit": unit,
                "up_axis": "+Y",
                "forward_axis": "+Z",
            },
        )
        refs.append(
            store.persist_structured(
                StructuredValue(
                    "asset_release",
                    "AssetRelease",
                    "1.0",
                    {
                        "export_profile": "gltf2-v1",
                        "files": {"geometry/visual.glb": {"artifact_id": mesh.artifact_id}},
                        "fixture_label": label,
                    },
                )
            )
        )
    raw = {
        "policy": "independent-generation-candidate-v1",
        "alignment": "not_performed",
        "generated": {"release": {"artifact_id": refs[0].artifact_id}},
        "reconstructed": {"release": {"artifact_id": refs[1].artifact_id}},
    }
    candidate = store.persist_structured(
        StructuredValue("completion_candidate", "CompletionCandidate", "1.0", raw)
    )
    matrix = np.array([[0, -2, 0, 4], [2, 0, 0, 5], [0, 0, 2, 6], [0, 0, 0, 1]], dtype=float)
    transform = SpatialTransform(
        candidate_frame(refs[0]), candidate_frame(refs[1]), matrix.tolist()
    )
    return store, candidate, transform


def test_alignment_geometry_appearance_provenance_roundtrip(tmp_path):
    store, candidate, transform = fixture(tmp_path)
    result = align_completion_candidate(
        candidate=candidate,
        transform=transform,
        store_path=store.root,
        output_path=tmp_path / "aligned",
    )
    output = result.output_directory
    before = _load_scene((output / "generated-original.glb").read_bytes())
    after = _load_scene((output / "aligned.glb").read_bytes())
    expected = trimesh.transform_points(_scene_vertices(before), transform.matrix)
    actual = _scene_vertices(after)
    assert np.allclose(actual[np.lexsort(actual.T)], expected[np.lexsort(expected.T)])
    assert len(after.geometry) == 2
    for original, aligned in zip(before.geometry.values(), after.geometry.values(), strict=True):
        assert original.visual.kind == aligned.visual.kind
        if original.visual.kind == "texture":
            assert np.array_equal(
                original.visual.material.baseColorTexture, aligned.visual.material.baseColorTexture
            )
            assert np.array_equal(original.visual.uv, aligned.visual.uv)
        else:
            assert np.array_equal(original.visual.vertex_colors, aligned.visual.vertex_colors)
    raw = store.read_structured(result.manifest)
    assert raw == json.loads((output / "alignment.json").read_text())
    assert store.read_structured(result.transform)["matrix"] == transform.matrix
    assert raw["fusion"] == "not_performed"
    assert raw["review_status"] == "pending"
    record = store.read_structured(result.provenance)
    assert candidate.artifact_id in record["derived_from_artifact_ids"]
    assert result.transform.artifact_id in record["derived_from_artifact_ids"]
    meta = store.get_manifest(result.aligned.artifact_id).identity.identity_metadata
    assert meta["frame_id"] == transform.target_frame_id
    assert meta["unit"] == "relative_unit"
    assert len(_load_scene((output / "overlay.glb").read_bytes()).geometry) == 4
    assert store.verify_digest(candidate)
    with pytest.raises(FileExistsError):
        align_completion_candidate(
            candidate=candidate, transform=transform, store_path=store.root, output_path=output
        )


@pytest.mark.parametrize(
    "matrix",
    [
        np.diag([-1, 1, 1, 1]),
        np.diag([1, 2, 1, 1]),
        np.diag([0, 0, 0, 1]),
        np.full((4, 4), np.nan),
        np.eye(3),
        [[1, 0.1, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
    ],
)
def test_reject_invalid_transform(tmp_path, matrix):
    store, candidate, transform = fixture(tmp_path)
    transform = SpatialTransform(
        transform.source_frame_id, transform.target_frame_id, np.asarray(matrix).tolist()
    )
    with pytest.raises(ContractError, match="alignment requires"):
        align_completion_candidate(
            candidate=candidate,
            transform=transform,
            store_path=store.root,
            output_path=tmp_path / "out",
        )
    assert not (tmp_path / "out").exists()


def test_frame_unit_and_port_validation(tmp_path):
    store, candidate, transform = fixture(tmp_path, unit="unknown")
    with pytest.raises(ContractError, match="unit"):
        align_completion_candidate(
            candidate=candidate,
            transform=transform,
            store_path=store.root,
            output_path=tmp_path / "out",
        )
    wrong = SpatialTransform("gltf_export", "gltf_export", transform.matrix)
    with pytest.raises(ContractError, match="frame"):
        align_completion_candidate(
            candidate=candidate,
            transform=wrong,
            store_path=store.root,
            output_path=tmp_path / "out",
        )
    wrong_kind = store.persist_structured(
        StructuredValue("quality_report", "QualityReport", "1.0", {})
    )
    with pytest.raises(ContractError, match="rejects kind"):
        align_completion_candidate(
            candidate=wrong_kind,
            transform=transform,
            store_path=store.root,
            output_path=tmp_path / "out",
        )


def test_preview_failure_is_atomic(tmp_path, monkeypatch):
    store, candidate, transform = fixture(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("preview failed")

    monkeypatch.setattr("assets_generator.alignment.files", fail)
    with pytest.raises(RuntimeError, match="preview failed"):
        align_completion_candidate(
            candidate=candidate,
            transform=transform,
            store_path=store.root,
            output_path=tmp_path / "out",
        )
    assert not (tmp_path / "out").exists()
    assert not list(tmp_path.glob(".out-*"))
    assert store.verify_digest(candidate)


def test_provenance_resolves_successful_build_run(tmp_path):
    store, candidate, transform = fixture(tmp_path)
    result = align_completion_candidate(
        candidate=candidate,
        transform=transform,
        store_path=store.root,
        output_path=tmp_path / "out",
    )
    provenance = store.read_structured(result.provenance)
    run = store.get_build_run(provenance["run_id"])
    assert run == json.loads((result.output_directory / "run.json").read_text())
    assert run["status"] == "succeeded"
    assert run["finished_at"]
    assert run["inputs"]["candidate"]["artifact_id"] == candidate.artifact_id
    assert run["inputs"]["transform"]["value"]["matrix"] == transform.matrix
    assert [node["status"] for node in run["node_attempts"]] == ["succeeded", "succeeded"]
    node = run["node_attempts"][0]
    assert node["node_id"] == provenance["node_id"]
    assert node["attempt"] == provenance["attempt"]
    assert node["outputs"]["aligned"]["artifact_id"] == provenance["output_artifact_id"]


@pytest.mark.parametrize("failure", ["preview", "rename", "contract"])
def test_failed_alignment_run_is_queryable(tmp_path, monkeypatch, failure):
    from pathlib import Path

    store, candidate, transform = fixture(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("publication failed")

    if failure == "preview":
        monkeypatch.setattr("assets_generator.alignment.files", fail)
    elif failure == "rename":
        monkeypatch.setattr(Path, "rename", fail)
    else:
        transform = SpatialTransform("wrong", "wrong", transform.matrix)
    with pytest.raises((RuntimeError, ContractError)):
        align_completion_candidate(
            candidate=candidate,
            transform=transform,
            store_path=store.root,
            output_path=tmp_path / "out",
        )
    run_files = list((store.root / "runs").glob("run_*.json"))
    assert len(run_files) == 1
    run = store.get_build_run(run_files[0].stem)
    assert run["status"] == "failed"
    assert run["finished_at"]
    attempt = run["node_attempts"][-1]
    assert attempt["status"] == "failed"
    assert attempt["finished_at"]
    assert attempt["error_code"] == (
        "contract_error" if failure == "contract" else "release_failed"
    )
    if failure != "contract":
        from assets_generator.models import ArtifactRef

        provenance = store.read_structured(ArtifactRef(**attempt["outputs"]["provenance"]))
        assert provenance["run_id"] == run["run_id"]
        assert run["node_attempts"][0]["status"] == "succeeded"
    assert not (tmp_path / "out").exists()
    assert not list(tmp_path.glob(".out-*"))
