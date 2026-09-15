from __future__ import annotations

import json
from dataclasses import replace

import pytest

from assets_generator.artifact_store import ArtifactStoreError, LocalArtifactStore, create_manifest
from assets_generator.models import ArtifactAnnotations, StructuredValue


def test_blob_and_artifact_identity_are_stable_and_location_independent(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    first = store.persist_bytes(
        b"mesh",
        kind="triangle_mesh",
        schema_name="test_mesh",
        schema_version="1.0",
        identity_metadata={"frame_id": "native", "unit": "relative_unit"},
        annotations=ArtifactAnnotations(labels={"name": "first"}),
    )
    second = store.persist_bytes(
        b"mesh",
        kind="triangle_mesh",
        schema_name="test_mesh",
        schema_version="1.0",
        identity_metadata={"frame_id": "native", "unit": "relative_unit"},
        annotations=ArtifactAnnotations(labels={"name": "second"}),
    )
    changed_frame = store.persist_bytes(
        b"mesh",
        kind="triangle_mesh",
        schema_name="test_mesh",
        schema_version="1.0",
        identity_metadata={"frame_id": "canonical", "unit": "relative_unit"},
    )

    assert first == second
    assert first != changed_frame
    assert store.verify_digest(first)
    assert store.resolve_blob(store.get_manifest(first.artifact_id).identity.blob_digest)


def test_structured_value_round_trip(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    value = StructuredValue(
        "quality_report",
        "QualityReport",
        "1.0",
        {"profile": "geometry-v1", "checks": [], "overall_status": "pass"},
    )
    reference = store.persist_structured(value)

    assert store.read_structured(reference) == value.value
    manifest = store.get_manifest(reference.artifact_id)
    assert manifest.identity.kind == "quality_report"
    assert manifest.identity.schema_name == "QualityReport"


def test_manifest_annotations_do_not_change_artifact_identity(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    with store.transaction() as transaction:
        blob = transaction.put_blob(b"content")
        first = create_manifest(
            blob,
            kind="quality_evidence",
            schema_name="text",
            schema_version="1.0",
        )
        second = replace(first, annotations=ArtifactAnnotations(created_at="later"))
        transaction.put_manifest(first)
        transaction.put_manifest(second)
        transaction.commit()
    assert first.artifact_id == second.artifact_id


def test_manifest_identity_tampering_is_rejected(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    reference = store.persist_bytes(
        b"content",
        kind="quality_evidence",
        schema_name="text",
        schema_version="1.0",
    )
    digest = reference.artifact_id.split(":", 1)[1]
    path = store.manifests_dir / digest[:2] / f"{digest}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["identity"]["schema_version"] = "tampered"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ArtifactStoreError, match="identity mismatch"):
        store.get_manifest(reference.artifact_id)
    assert not store.verify_digest(reference)


def test_cache_rejects_missing_or_tampered_artifacts(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    reference = store.persist_bytes(
        b"content",
        kind="quality_evidence",
        schema_name="text",
        schema_version="1.0",
    )
    key = "sha256:" + "a" * 64
    store.put_cache(key, {"artifact_ids": [reference.artifact_id], "value": "valid"})
    assert store.get_cache(key) == {
        "artifact_ids": [reference.artifact_id],
        "cache_key": key,
        "value": "valid",
    }

    store.blob_path(reference).unlink()

    assert store.get_cache(key) is None
