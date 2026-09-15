from __future__ import annotations

import pytest

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import (
    ContractError,
    OperatorSpec,
    PortSpec,
    validate_operator_inputs,
)
from assets_generator.pipeline import (
    PipelineDefinition,
    compile_pipeline,
    load_default_operator_specs,
    load_default_pipeline,
    load_operator_specs,
    load_pipeline,
)


def test_phase1_pipeline_compiles() -> None:
    specs = load_operator_specs(__import__("pathlib").Path("pipelines/operators-v1.yaml"))
    pipeline = load_pipeline(__import__("pathlib").Path("pipelines/image_asset_v1.yaml"))
    compile_pipeline(pipeline, specs)


def test_packaged_and_repository_pipeline_contracts_match() -> None:
    repository_specs = load_operator_specs(
        __import__("pathlib").Path("pipelines/operators-v1.yaml")
    )
    repository_pipeline = load_pipeline(__import__("pathlib").Path("pipelines/image_asset_v2.yaml"))

    assert load_default_operator_specs() == repository_specs
    assert load_default_pipeline() == repository_pipeline


def test_spatial_artifact_requires_frame_and_unit(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    mesh = store.persist_bytes(
        b"mesh",
        kind="triangle_mesh",
        schema_name="mesh",
        schema_version="1.0",
    )
    spec = OperatorSpec(
        "canonicalize",
        "1",
        {"mesh": PortSpec(("triangle_mesh",), requires_frame=True, requires_unit=True)},
        {},
    )
    with pytest.raises(ContractError, match="requires frame_id"):
        validate_operator_inputs(spec, {"mesh": mesh}, store)


def test_exact_kind_matching_rejects_implicit_conversion(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    image = store.persist_bytes(
        b"image",
        kind="rgb_image",
        schema_name="image",
        schema_version="1.0",
    )
    spec = OperatorSpec("shape", "1", {"image": PortSpec(("rgba_image",))}, {})
    with pytest.raises(ContractError, match="rejects kind rgb_image"):
        validate_operator_inputs(spec, {"image": image}, store)


def test_pipeline_compile_rejects_persisted_value_as_structured_input() -> None:
    specs = {
        "producer@1": OperatorSpec(
            "producer",
            "1",
            {},
            {"value": PortSpec(("quality_report",), carriers=("structured",), persist=True)},
        ),
        "consumer@1": OperatorSpec(
            "consumer",
            "1",
            {"value": PortSpec(("quality_report",), carriers=("structured",))},
            {},
        ),
    }
    pipeline = PipelineDefinition(
        "invalid",
        "1",
        {},
        {
            "producer": {"operator": "producer@1", "inputs": {}},
            "consumer": {
                "operator": "consumer@1",
                "inputs": {"value": "producer.outputs.value"},
            },
        },
    )

    with pytest.raises(ContractError, match="carrier mismatch"):
        compile_pipeline(pipeline, specs)
