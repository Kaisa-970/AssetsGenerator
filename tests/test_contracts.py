from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.backend_registry import BackendRegistry, resolve_plan
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


def test_pipeline_compile_rejects_cycles() -> None:
    specs = {
        "step@1": OperatorSpec(
            "step", "1", {"input": PortSpec(("rgb_image",))}, {"output": PortSpec(("rgb_image",))}
        )
    }
    pipeline = PipelineDefinition(
        "cyclic",
        "1",
        {"image": PortSpec(("rgb_image",))},
        {
            "a": {"operator": "step@1", "inputs": {"input": "b.outputs.output"}},
            "b": {"operator": "step@1", "inputs": {"input": "a.outputs.output"}},
        },
    )
    with pytest.raises(ContractError, match="contains cycle"):
        compile_pipeline(pipeline, specs)


def test_resolved_plan_binds_pipeline_backend_and_allows_override() -> None:
    pipeline = load_default_pipeline()
    specs = load_default_operator_specs()
    registry = BackendRegistry()
    trellis = object()
    alternate = object()
    registry.register(
        name="trellis2",
        operator="shape_generation@1",
        backend_version="1.0",
        implementation=trellis,
    )
    registry.register(
        name="alternate",
        operator="shape_generation@1",
        backend_version="2.0",
        implementation=alternate,
    )

    default = resolve_plan(pipeline, registry, operator_specs=specs)
    overridden = resolve_plan(
        pipeline,
        registry,
        operator_specs=specs,
        backend_overrides={"generate_shape": "alternate"},
    )

    assert default.backend_for("generate_shape", "shape_generation@1").implementation is trellis
    assert (
        overridden.backend_for("generate_shape", "shape_generation@1").implementation is alternate
    )
    with pytest.raises(TypeError):
        overridden.backends["generate_shape"] = default.backends["generate_shape"]  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        overridden.pipeline_version = "changed"  # type: ignore[misc]


def test_resolved_plan_rejects_backend_for_wrong_operator() -> None:
    registry = BackendRegistry()
    registry.register(
        name="trellis2",
        operator="segmentation@1",
        backend_version="1.0",
        implementation=object(),
    )

    with pytest.raises(ContractError, match="implements segmentation@1"):
        resolve_plan(
            load_default_pipeline(), registry, operator_specs=load_default_operator_specs()
        )


def test_backend_registry_rejects_duplicate_names() -> None:
    registry = BackendRegistry()
    registration = {
        "name": "trellis2",
        "operator": "shape_generation@1",
        "backend_version": "1.0",
        "implementation": object(),
    }
    registry.register(**registration)

    with pytest.raises(ContractError, match="duplicate backend registration"):
        registry.register(**registration)


def test_resolved_plan_rejects_unknown_override_node() -> None:
    with pytest.raises(ContractError, match="unknown nodes"):
        resolve_plan(
            load_default_pipeline(),
            BackendRegistry(),
            operator_specs=load_default_operator_specs(),
            backend_overrides={"missing": "trellis2"},
        )


def test_resolved_plan_requires_registered_default_backend() -> None:
    with pytest.raises(ContractError, match="backend is not registered: trellis2"):
        resolve_plan(
            load_default_pipeline(),
            BackendRegistry(),
            operator_specs=load_default_operator_specs(),
        )


def test_resolved_plan_rejects_binding_for_unsupported_node() -> None:
    pipeline = load_default_pipeline()
    pipeline.nodes["resolve_mask"]["backend"] = "segmentation"
    registry = BackendRegistry()
    registry.register(
        name="trellis2",
        operator="shape_generation@1",
        backend_version="1.0",
        implementation=object(),
    )
    registry.register(
        name="segmentation",
        operator="segmentation@1",
        backend_version="1.0",
        implementation=object(),
    )

    with pytest.raises(ContractError, match="not supported for node: resolve_mask"):
        resolve_plan(pipeline, registry, operator_specs=load_default_operator_specs())


def test_directly_constructed_resolved_plan_copies_bindings() -> None:
    from assets_generator.backend_registry import ResolvedBackend, ResolvedPlan

    bindings = {
        "generate_shape": ResolvedBackend(
            "generate_shape", "shape_generation@1", "test", "1", object()
        )
    }
    plan = ResolvedPlan("pipeline", "1", "sha256:test", bindings)
    bindings.clear()

    assert "generate_shape" in plan.backends
    with pytest.raises(TypeError):
        plan.backends["other"] = plan.backends["generate_shape"]  # type: ignore[index]
