import json
from pathlib import Path

import pytest
from PIL import Image
from test_scene_extraction import setup

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import ContractError
from assets_generator.instance_proposals import propose_instances, select_instance_proposals
from assets_generator.models import ArtifactRef, StructuredValue
from assets_generator.scene_extraction import extract_scene_objects


class FakeProposer:
    def propose(self, store, image):
        masks = []
        for index, box in enumerate([(0, 0, 4, 6), (4, 0, 8, 6)]):
            p = store.root / f"mask-{index}.png"
            m = Image.new("L", (8, 6), 0)
            for x in range(box[0], box[2]):
                for y in range(box[1], box[3]):
                    m.putpixel((x, y), 255)
            m.save(p)
            from assets_generator.workflow import _import_image

            ref = _import_image(store, p, "binary_mask")
            masks.append(ref)
        return StructuredValue(
            "instance_proposals",
            "InstanceProposals",
            "1.0",
            {
                "schema_version": "1.0",
                "image": {"artifact_id": image.artifact_id},
                "status": "unreviewed_proposals",
                "backend_metadata": {"checkpoint_digest": "sha256:" + "0" * 64},
                "proposals": [
                    {
                        "proposal_id": f"p{i}",
                        "mask": {"artifact_id": r.artifact_id},
                        "label": "unknown",
                        "area": 24,
                        "bbox": [i * 4, 0, 4, 6],
                        "predicted_iou": 0.9,
                        "stability_score": 0.95,
                    }
                    for i, r in enumerate(masks)
                ],
            },
        )


def test_propose_select_and_extract_handoff(tmp_path):
    (tmp_path / "extract").mkdir()
    image = tmp_path / "scene.png"
    Image.new("RGB", (8, 6), "red").save(image)
    proposal = propose_instances(
        image_path=image,
        store_path=tmp_path / "store",
        output_path=tmp_path / "proposals",
        backend=FakeProposer(),
    )
    store = LocalArtifactStore(tmp_path / "store")
    proposal_value = store.read_structured(ArtifactRef(**proposal["proposals"]))
    assert json.loads((tmp_path / "proposals/proposals.json").read_text()) == proposal_value
    mask_provenance_ids = [p["provenance"]["artifact_id"] for p in proposal_value["proposals"]]
    assert len(mask_provenance_ids) == len(set(mask_provenance_ids)) == 2
    for row, provenance_id in zip(proposal_value["proposals"], mask_provenance_ids, strict=True):
        provenance = json.loads(store.blob_path(ArtifactRef(provenance_id)).read_text())
        assert provenance["output_artifact_id"] == row["mask"]["artifact_id"]
        assert provenance["source"] == "estimated"
    proposal_run = store.get_build_run(proposal["run_id"])
    assert proposal_run["status"] == "succeeded"
    assert [node["status"] for node in proposal_run["node_attempts"]] == [
        "succeeded",
        "succeeded",
    ]
    selected = select_instance_proposals(
        proposals=ArtifactRef(**proposal["proposals"]),
        proposal_ids=["p1", "p0"],
        reviewer="tester",
        store_path=tmp_path / "store",
        output_path=tmp_path / "selected",
    )
    raw = json.loads(Path(selected["manifest"]).read_text())
    assert raw["instance_selection"] == selected["selection"]
    selection_run = store.get_build_run(selected["run_id"])
    assert selection_run["status"] == "succeeded"
    assert [node["status"] for node in selection_run["node_attempts"]] == [
        "succeeded",
        "succeeded",
    ]
    selection_provenance = json.loads((tmp_path / "selected/provenance.json").read_text())
    assert selection_provenance["source"] == "user"
    assert selection_provenance["output_artifact_id"] == selected["selection"]["artifact_id"]
    args = setup(tmp_path / "extract")
    args["manifest_path"] = Path(selected["manifest"])
    args["store_path"] = tmp_path / "store"
    args["output_path"] = tmp_path / "extracted"
    result = extract_scene_objects(**args)
    request = store.read_structured(
        ArtifactRef(**store.read_structured(result.manifest)["request"])
    )
    assert request["segmentation_source"] == "selected_model_proposals"
    assert request["instance_selection"] == selected["selection"]
    assert [o["mask"] for o in request["objects"]] == [
        p["mask"]
        for p in reversed(store.read_structured(ArtifactRef(**proposal["proposals"]))["proposals"])
    ]
    assert set(result.releases) == {"object_001", "object_002"}


@pytest.mark.parametrize("tamper", ["image", "mask_order", "mask_content"])
def test_extraction_rejects_selection_handoff_tamper(tmp_path, tamper):
    image = tmp_path / "scene.png"
    Image.new("RGB", (8, 6), "red").save(image)
    proposal = propose_instances(
        image_path=image,
        store_path=tmp_path / "store",
        output_path=tmp_path / "proposals",
        backend=FakeProposer(),
    )
    selected = select_instance_proposals(
        proposals=ArtifactRef(**proposal["proposals"]),
        proposal_ids=["p0", "p1"],
        reviewer="tester",
        store_path=tmp_path / "store",
        output_path=tmp_path / "selected",
    )
    manifest = Path(selected["manifest"])
    raw = json.loads(manifest.read_text())
    if tamper == "image":
        Image.new("RGB", (8, 6), "blue").save(manifest.parent / "other.png")
        raw["image"] = "other.png"
    elif tamper == "mask_order":
        raw["objects"].reverse()
    else:
        Image.new("L", (8, 6), 255).save(manifest.parent / raw["objects"][0]["mask"])
    manifest.write_text(json.dumps(raw))
    extract_root = tmp_path / "extract"
    extract_root.mkdir()
    args = setup(extract_root)
    args.update(
        manifest_path=manifest,
        store_path=tmp_path / "store",
        output_path=tmp_path / "extracted",
    )
    with pytest.raises(ContractError, match="selection does not match"):
        extract_scene_objects(**args)
    assert not args["output_path"].exists()


def test_selection_rejects_unknown_duplicates_and_empty(tmp_path):
    image = tmp_path / "scene.png"
    Image.new("RGB", (8, 6), "red").save(image)
    proposal = propose_instances(
        image_path=image,
        store_path=tmp_path / "store",
        output_path=tmp_path / "proposals",
        backend=FakeProposer(),
    )
    for ids in [[], ["missing"], ["p0", "p0"]]:
        output = tmp_path / ("x" + str(ids))
        with pytest.raises(ContractError):
            select_instance_proposals(
                proposals=ArtifactRef(**proposal["proposals"]),
                proposal_ids=ids,
                reviewer="tester",
                store_path=tmp_path / "store",
                output_path=output,
            )
        assert not output.exists()


def test_rejected_proposals_are_retained_as_selection_evidence(tmp_path):
    image = tmp_path / "scene.png"
    Image.new("RGB", (8, 6), "red").save(image)
    proposal = propose_instances(
        image_path=image,
        store_path=tmp_path / "store",
        output_path=tmp_path / "proposals",
        backend=FakeProposer(),
    )
    store = LocalArtifactStore(tmp_path / "store")
    ref = ArtifactRef(**proposal["proposals"])
    raw = store.read_structured(ref)
    chosen = raw["proposals"][1]["proposal_id"]
    result = select_instance_proposals(
        proposals=ref,
        proposal_ids=[chosen],
        reviewer="tester",
        store_path=store.root,
        output_path=tmp_path / "selection",
    )
    selection = store.read_structured(ArtifactRef(**result["selection"]))
    assert selection["selected_ids"] == [chosen]
    assert selection["unselected_ids"] == [raw["proposals"][0]["proposal_id"]]
    manifest = json.loads(Path(result["manifest"]).read_text())
    assert len(manifest["objects"]) == 1
    assert (
        Path(result["manifest"]).parent / manifest["objects"][0]["mask"]
    ).read_bytes() == store.blob_path(ArtifactRef(**raw["proposals"][1]["mask"])).read_bytes()


def test_proposal_publish_failure_records_run(tmp_path, monkeypatch):
    image = tmp_path / "scene.png"
    Image.new("RGB", (8, 6), "red").save(image)

    def fail(*args):
        raise OSError("publication failed")

    monkeypatch.setattr("assets_generator.instance_proposals.publish_staged_release", fail)
    with pytest.raises(OSError, match="publication failed"):
        propose_instances(
            image_path=image,
            store_path=tmp_path / "store",
            output_path=tmp_path / "proposals",
            backend=FakeProposer(),
        )
    store = LocalArtifactStore(tmp_path / "store")
    runs = [store.get_build_run(path.stem) for path in (store.root / "runs").glob("*.json")]
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    assert runs[0]["node_attempts"][-1]["error_code"] == "release_failed"
    assert not (tmp_path / "proposals").exists()


def test_selection_publish_failure_records_run(tmp_path, monkeypatch):
    image = tmp_path / "scene.png"
    Image.new("RGB", (8, 6), "red").save(image)
    proposal = propose_instances(
        image_path=image,
        store_path=tmp_path / "store",
        output_path=tmp_path / "proposals",
        backend=FakeProposer(),
    )

    def fail(*args):
        raise OSError("publication failed")

    monkeypatch.setattr("assets_generator.instance_proposals.publish_staged_release", fail)
    with pytest.raises(OSError, match="publication failed"):
        select_instance_proposals(
            proposals=ArtifactRef(**proposal["proposals"]),
            proposal_ids=["p0"],
            reviewer="tester",
            store_path=tmp_path / "store",
            output_path=tmp_path / "selection",
        )
    store = LocalArtifactStore(tmp_path / "store")
    runs = [store.get_build_run(path.stem) for path in (store.root / "runs").glob("*.json")]
    selection_runs = [run for run in runs if run["pipeline_name"] == "instance_selection"]
    assert len(selection_runs) == 1
    assert selection_runs[0]["status"] == "failed"
    assert selection_runs[0]["node_attempts"][-1]["error_code"] == "release_failed"
    assert not (tmp_path / "selection").exists()
