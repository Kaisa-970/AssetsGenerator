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
