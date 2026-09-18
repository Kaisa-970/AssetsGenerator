from __future__ import annotations

import pytest

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import ContractError, validate_operator_outputs
from assets_generator.models import StructuredValue
from assets_generator.pipeline import load_default_operator_specs


def test_usd_export_requires_spatial_usd_and_explicit_texture_collection(tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    spec = load_default_operator_specs()["usd_export@1"]
    report = store.persist_structured(StructuredValue("quality_report", "QualityReport", "1.0", {}))
    usd = store.persist_bytes(
        b"#usda 1.0\n",
        kind="usd_asset",
        schema_name="OpenUSD",
        schema_version="1.0",
        identity_metadata={"frame_id": "asset_canonical", "unit": "meter"},
    )
    validate_operator_outputs(spec, {"usd": usd, "report": report, "textures": []}, store)
    without_space = store.persist_bytes(
        b"#usda 1.0\n", kind="usd_asset", schema_name="OpenUSD", schema_version="1.0"
    )
    with pytest.raises(ContractError, match="frame_id"):
        validate_operator_outputs(spec, {"usd": without_space, "report": report}, store)
    glb = store.persist_bytes(
        b"glb",
        kind="gltf_asset",
        schema_name="glTF",
        schema_version="2.0",
        identity_metadata={"frame_id": "gltf_export", "unit": "meter"},
    )
    with pytest.raises(ContractError, match="kind"):
        validate_operator_outputs(spec, {"usd": glb, "report": report}, store)
