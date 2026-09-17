import json

import pytest
from test_alignment import fixture

from assets_generator.alignment_review import AlignmentReviewSession
from assets_generator.composition import region_scene
from assets_generator.contracts import ContractError
from assets_generator.models import ArtifactRef
from assets_generator.operators import _load_scene


def selected_session(tmp_path):
    store, candidate, transform = fixture(tmp_path)
    session = AlignmentReviewSession(store.root, candidate, tmp_path / "output")
    saved = session.save({"matrix": transform.matrix})
    review = session.review(
        {
            "alignment_id": saved["alignment"]["artifact_id"],
            "decision": "accepted",
            "reviewer": "test",
        }
    )
    session = AlignmentReviewSession(store.root, candidate, tmp_path / "new-output")
    assert len(session.history()["alignments"]) == 1
    restored = session.restore({"alignment_id": saved["alignment"]["artifact_id"]})
    assert restored["matrix"] == transform.matrix
    request = {
        "alignment_id": saved["alignment"]["artifact_id"],
        "accepted_review_id": review["review"]["artifact_id"],
        "reviewer": "selector",
    }
    selection = session.select(request)
    return store, session, selection, request


def test_restore_select_region_publish_roundtrip(tmp_path):
    store, session, selected, _ = selected_session(tmp_path)
    regions = {
        "generated": {"mode": "inside", "minimum": [2, 3, 4], "maximum": [6, 7, 8]},
        "reconstructed": {"mode": "all"},
    }
    data = {"selection_id": selected["selection"]["artifact_id"], "regions": regions}
    preview = session.compose(data, preview=True)
    result = session.compose(data)
    manifest = store.read_structured(ArtifactRef(**result["composition"]))
    assert manifest["status"] == "unfused_component_composition"
    assert store.get_build_run(manifest["run_id"])["status"] == "succeeded"
    region = store.read_structured(ArtifactRef(**manifest["regions"]))
    assert region["rules"] == regions
    assert {c["source"] for c in region["components"]} == {"generated", "reconstructed"}
    assert sum(len(c["source_face_indices"]) for c in region["components"]) == preview["faces"]
    scene = _load_scene(store.blob_path(ArtifactRef(**manifest["glb"])).read_bytes())
    assert sum(len(g.faces) for g in scene.geometry.values()) == preview["faces"]
    assert {g.visual.kind for g in scene.geometry.values()} == {"texture", "vertex"}
    assert any(
        g.visual.material.baseColorTexture is not None
        for g in scene.geometry.values()
        if g.visual.kind == "texture"
    )
    from pathlib import Path

    assert (
        json.loads((Path(result["output_directory"]) / "composition.json").read_text()) == manifest
    )
    assert (
        json.loads((Path(result["output_directory"]) / "run.json").read_text())["status"]
        == "succeeded"
    )


def test_conflicts_and_stale_selection(tmp_path):
    store, session, selected, request = selected_session(tmp_path)
    session.review(
        {"alignment_id": request["alignment_id"], "decision": "rejected", "reviewer": "other"}
    )
    data = {
        "selection_id": selected["selection"]["artifact_id"],
        "regions": {"generated": {"mode": "all"}, "reconstructed": {"mode": "all"}},
    }
    with pytest.raises(ContractError, match="changed"):
        session.compose(data)
    with pytest.raises(ContractError, match="resolution"):
        session.select(request)
    selection = session.select(
        {**request, "resolution_note": "Explicitly choosing first inspection for this experiment"}
    )
    assert store.read_structured(ArtifactRef(**selection["selection"]))["resolution_note"]


@pytest.mark.parametrize(
    "rule",
    [
        {"mode": "inside", "minimum": [0, 0, 0], "maximum": [0, 1, 1]},
        {"mode": "other"},
        {"mode": "none"},
    ],
)
def test_invalid_or_empty_regions(tmp_path, rule):
    store, session, selected, _ = selected_session(tmp_path)
    with pytest.raises((ContractError, ValueError)):
        region_scene(
            store,
            ArtifactRef(**selected["selection"]),
            {"generated": rule, "reconstructed": {"mode": "none"}},
        )


def test_composition_publish_failure_recorded(tmp_path, monkeypatch):
    from pathlib import Path

    store, session, selected, _ = selected_session(tmp_path)

    def fail(*args):
        raise OSError("cannot publish")

    monkeypatch.setattr(Path, "rename", fail)
    with pytest.raises(OSError, match="cannot publish"):
        session.compose(
            {
                "selection_id": selected["selection"]["artifact_id"],
                "regions": {"generated": {"mode": "all"}, "reconstructed": {"mode": "all"}},
            }
        )
    runs = [store.get_build_run(p.stem) for p in (store.root / "runs").glob("run_*.json")]
    run = next(r for r in runs if r["pipeline_name"] == "manual_region_composition")
    assert run["status"] == "failed"
    assert run["node_attempts"][-1]["error_code"] == "release_failed"
    assert not list(session.output_root.glob("composition-*"))


def test_history_skips_legacy_alignment_without_run(tmp_path):
    from assets_generator.models import StructuredValue

    store, session, _, request = selected_session(tmp_path)
    raw = store.read_structured(ArtifactRef(request["alignment_id"]))
    del raw["run_id"]
    store.persist_structured(
        StructuredValue("candidate_alignment", "CandidateAlignment", "1.0", raw)
    )
    assert len(session.history()["alignments"]) == 1


def test_standard_release_coordinates_evidence_and_components(tmp_path):
    from pathlib import Path

    import numpy as np

    from assets_generator.operators import _scene_vertices

    store, session, selected, _ = selected_session(tmp_path)
    result = session.compose(
        {
            "selection_id": selected["selection"]["artifact_id"],
            "regions": {"generated": {"mode": "all"}, "reconstructed": {"mode": "all"}},
        }
    )
    release = store.read_structured(ArtifactRef(**result["release"]))
    asset = store.read_structured(ArtifactRef(**release["asset_definition"]))
    assert {c["source"] for c in asset["component_provenance"]} == {"generated", "reconstructed"}
    assert len(asset["geometry"]["visual_meshes"]) == len(asset["component_provenance"])
    assert asset["spatial"]["unit"] == "relative_unit"
    output = Path(result["output_directory"])
    for name, raw in release["files"].items():
        ref = ArtifactRef(**raw)
        assert store.verify_digest(ref)
        assert (output / name).read_bytes() == store.blob_path(ref).read_bytes()
    a = _scene_vertices(_load_scene((output / "visual.glb").read_bytes()))
    b = _scene_vertices(_load_scene((output / "geometry/visual.glb").read_bytes()))
    assert np.allclose(np.unique(a, axis=0), np.unique(b, axis=0))
    assert (output / "evidence/transform.json").exists()
    assert list((output / "evidence").glob("review-*.json"))
    qa = json.loads((output / "qa/quality-report.json").read_text())
    assert qa["overall_status"] == "warn"
    assert any(
        c["check_id"] == "simulation_readiness" and c["status"] == "warn" for c in qa["checks"]
    )
    records_by_id = {}
    output_ids = set()
    from assets_generator.workflow import _output_id

    for c in asset["component_provenance"]:
        record = json.loads((output / f"provenance/{c['component_id']}.json").read_text())
        assert record["provenance_id"] not in records_by_id
        assert record["output_id"] not in output_ids
        records_by_id[record["provenance_id"]] = record
        output_ids.add(record["output_id"])
        assert record["parameters"]["output_element_id"] == c["component_id"]
        assert record["output_id"] == _output_id(
            record["run_id"], record["node_id"], "components", c["component_id"]
        )
        assert record["provenance_id"] in c["provenance_ids"]
        assert record["output_artifact_id"] == c["artifact"]["artifact_id"]
        run = store.get_build_run(record["run_id"])
        assert any(n["node_id"] == record["node_id"] for n in run["node_attempts"])
    assert len(records_by_id) == len(output_ids) == 4
    for c in asset["component_provenance"]:
        assert (
            records_by_id[c["provenance_ids"][0]]["output_artifact_id"]
            == c["artifact"]["artifact_id"]
        )


def test_release_rejects_wrong_aligned_unit(tmp_path):
    from assets_generator.models import StructuredValue

    store, session, selected, request = selected_session(tmp_path)
    raw = store.read_structured(ArtifactRef(request["alignment_id"]))
    raw["target_unit"] = "meter"
    bad = store.persist_structured(
        StructuredValue("candidate_alignment", "CandidateAlignment", "1.0", raw)
    )
    session.restore({"alignment_id": bad.artifact_id})
    accepted = session.review(
        {"alignment_id": bad.artifact_id, "decision": "accepted", "reviewer": "tester"}
    )
    new_selection = session.select(
        {
            "alignment_id": bad.artifact_id,
            "accepted_review_id": accepted["review"]["artifact_id"],
            "reviewer": "tester",
        }
    )
    with pytest.raises(ContractError, match="frame/unit"):
        session.compose(
            {
                "selection_id": new_selection["selection"]["artifact_id"],
                "regions": {"generated": {"mode": "all"}, "reconstructed": {"mode": "all"}},
            }
        )
    assert not list(session.output_root.glob("composition-*"))


def test_output_element_identity_is_stable_and_unambiguous():
    from assets_generator.serialization import sha256_bytes
    from assets_generator.workflow import _output_id

    assert _output_id("run", "node", "components", "generated_0") == _output_id(
        "run", "node", "components", "generated_0"
    )
    assert _output_id("run", "node", "components", "generated_0") != _output_id(
        "run", "node", "components", "generated_1"
    )
    assert _output_id("run", "node", "a:b", "c") != _output_id("run", "node", "a", "b:c")
    assert _output_id("run", "node", "mesh") == sha256_bytes(b"run:node:1:mesh")
    with pytest.raises(ValueError, match="non-empty"):
        _output_id("run", "node", "components", "")
