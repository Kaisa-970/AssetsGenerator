from __future__ import annotations

import json

import pytest
from PIL import Image
from test_multi_view_workflow import (
    ContractGeometryFrontend,
    ContractReconstruction,
    _observations,
    _plan,
)
from test_workflow import ContractBackend

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.backend_registry import BackendRegistry, resolve_plan
from assets_generator.completion import build_completion_candidate
from assets_generator.contracts import ContractError
from assets_generator.models import ArtifactRef
from assets_generator.multi_view_workflow import build_multi_view_asset
from assets_generator.observation_import import import_observation_manifest
from assets_generator.observations import observation_bundle_from_artifact
from assets_generator.pipeline import load_default_operator_specs, load_default_pipeline


def setup_candidate(tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    _observations(tmp_path, store)
    Image.new("L", (8, 6), 255).save(tmp_path / "mask.png")
    manifest = tmp_path / "observations.json"
    data = json.loads(manifest.read_text())
    for v in data["views"]:
        v["mask"] = "mask.png"
    manifest.write_text(json.dumps(data))
    obs = import_observation_manifest(manifest, store)
    result = build_multi_view_asset(
        observations=obs,
        store_path=store.root,
        output_path=tmp_path / "original",
        resolved_plan=_plan(ContractGeometryFrontend(), ContractReconstruction()),
    )
    registry = BackendRegistry()
    registry.register(
        name="trellis2",
        operator="shape_generation@1",
        backend_version="test",
        implementation=ContractBackend(),
    )
    plan = resolve_plan(
        load_default_pipeline(), registry, operator_specs=load_default_operator_specs()
    )
    return store, obs, result, plan


def test_candidate_preserves_reconstruction_and_records_generation(tmp_path):
    store, obs, original, plan = setup_candidate(tmp_path)
    bundle = observation_bundle_from_artifact(obs, store)
    assert bundle.views[0].mask is not None
    result = build_completion_candidate(
        observations=obs,
        reconstruction_release=original.release_manifest,
        view_id=bundle.views[0].view_id,
        store_path=store.root,
        output_path=tmp_path / "candidate",
        resolved_plan=plan,
    )
    raw = json.loads((result.output_directory / "candidate.json").read_text())
    assert raw == store.read_structured(result.manifest)
    assert raw["geometry_conditioning"] is False
    assert raw["generated"]["source"] == "generated"
    assert raw["reconstructed"]["source"] == "reconstructed"
    assert (result.output_directory / "reconstructed/geometry/visual.glb").read_bytes() == (
        tmp_path / "original/geometry/visual.glb"
    ).read_bytes()
    generated = json.loads((result.output_directory / "generated/release.json").read_text())
    for name, ref in generated["files"].items():
        assert (result.output_directory / "generated" / name).read_bytes() == store.blob_path(
            ArtifactRef(**ref)
        ).read_bytes()
    records = [
        json.loads(p.read_text())
        for p in (result.output_directory / "generated/provenance").glob("*.json")
    ]
    assert any(r["source"] == "generated" for r in records)


def test_unknown_view_does_not_publish_candidate(tmp_path):
    store, obs, original, plan = setup_candidate(tmp_path)
    with pytest.raises(ContractError, match="view_id"):
        build_completion_candidate(
            observations=obs,
            reconstruction_release=original.release_manifest,
            view_id="missing",
            store_path=store.root,
            output_path=tmp_path / "candidate",
            resolved_plan=plan,
        )
    assert not (tmp_path / "candidate").exists()


def test_generation_failure_leaves_no_partial_candidate(tmp_path):
    store, obs, original, _ = setup_candidate(tmp_path)

    class BrokenBackend:
        def generate(self, *args, **kwargs):
            raise RuntimeError("generation failed")

    registry = BackendRegistry()
    registry.register(
        name="trellis2",
        operator="shape_generation@1",
        backend_version="test",
        implementation=BrokenBackend(),
    )
    plan = resolve_plan(
        load_default_pipeline(), registry, operator_specs=load_default_operator_specs()
    )
    with pytest.raises(Exception, match="generation failed"):
        build_completion_candidate(
            observations=obs,
            reconstruction_release=original.release_manifest,
            view_id="front",
            store_path=store.root,
            output_path=tmp_path / "candidate",
            resolved_plan=plan,
        )
    assert not (tmp_path / "candidate").exists()
    assert not list(tmp_path.glob(".candidate-*"))
    assert store.verify_digest(original.release_manifest)


def test_candidate_rejects_release_path_traversal(tmp_path):
    from assets_generator.models import SCHEMA_VERSION, StructuredValue

    store, obs, original, plan = setup_candidate(tmp_path)
    raw = store.read_structured(original.release_manifest)
    raw["files"]["../escape"] = raw["files"]["geometry/visual.glb"]
    bad = store.persist_structured(
        StructuredValue("asset_release", "AssetRelease", SCHEMA_VERSION, raw)
    )
    with pytest.raises(ContractError, match="unsafe"):
        build_completion_candidate(
            observations=obs,
            reconstruction_release=bad,
            view_id="front",
            store_path=store.root,
            output_path=tmp_path / "candidate",
            resolved_plan=plan,
        )
    assert not (tmp_path / "candidate").exists()
