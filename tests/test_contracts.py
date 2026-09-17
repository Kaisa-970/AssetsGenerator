from __future__ import annotations

import re
from dataclasses import FrozenInstanceError

import pytest

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.backend_registry import (
    BackendRegistry,
    ResolvedBackend,
    ResolvedPlan,
    resolve_plan,
)
from assets_generator.contracts import (
    ContractError,
    OperatorSpec,
    PortSpec,
    validate_operator_inputs,
    validate_port_value,
)
from assets_generator.models import STRUCTURED_KINDS
from assets_generator.pipeline import (
    PipelineDefinition,
    compile_pipeline,
    load_default_operator_specs,
    load_default_pipeline,
    load_operator_specs,
    load_pipeline,
)
from assets_generator.runtime import Phase1Runtime


def test_phase1_pipeline_compiles() -> None:
    specs = load_operator_specs(__import__("pathlib").Path("pipelines/operators-v1.yaml"))
    pipeline = load_pipeline(__import__("pathlib").Path("pipelines/image_asset_v1.yaml"))
    compile_pipeline(pipeline, specs)


def test_packaged_and_repository_pipeline_contracts_match() -> None:
    from importlib.resources import files

    repository_specs = load_operator_specs(
        __import__("pathlib").Path("pipelines/operators-v1.yaml")
    )
    repository_pipeline = load_pipeline(__import__("pathlib").Path("pipelines/image_asset_v2.yaml"))

    assert load_default_operator_specs() == repository_specs
    assert load_default_pipeline() == repository_pipeline
    assert (
        files("assets_generator.resources").joinpath("operators-v1.yaml").read_bytes()
        == __import__("pathlib").Path("pipelines/operators-v1.yaml").read_bytes()
    )
    assert (
        files("assets_generator.resources").joinpath("multi_view_asset_v1.yaml").read_bytes()
        == __import__("pathlib").Path("pipelines/multi_view_asset_v1.yaml").read_bytes()
    )


@pytest.mark.parametrize(
    "name",
    ["operators-v1.yaml", "image_asset_v1.yaml", "image_asset_v2.yaml", "multi_view_asset_v1.yaml"],
)
def test_all_packaged_yaml_mirrors_match(name) -> None:
    from importlib.resources import files
    from pathlib import Path

    assert (
        files("assets_generator.resources").joinpath(name).read_bytes()
        == Path("pipelines", name).read_bytes()
    )


@pytest.mark.parametrize("cardinality", ["one", "one_or_more"])
def test_optional_reference_cannot_bind_required_port(cardinality) -> None:
    specs = {
        "consumer@1": OperatorSpec(
            "consumer", "1", {"value": PortSpec(("rgb_image",), cardinality)}, {}
        )
    }
    pipeline = PipelineDefinition(
        "invalid",
        "1",
        {"value": PortSpec(("rgb_image",), cardinality)},
        {"consumer": {"operator": "consumer@1", "inputs": {"value": "pipeline.inputs.value?"}}},
    )
    with pytest.raises(ContractError, match="cardinality mismatch"):
        compile_pipeline(pipeline, specs)


def test_documented_structured_kinds_match_runtime_contract() -> None:
    document = (
        __import__("pathlib").Path("docs/design/pipeline-contract.md").read_text(encoding="utf-8")
    )
    match = re.search(r"结构化 kind：\n\n```text\n(?P<kinds>.*?)\n```", document, re.DOTALL)

    assert match is not None
    assert frozenset(match.group("kinds").splitlines()) == STRUCTURED_KINDS


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


def test_port_validation_rejects_tampered_artifact_blob(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    image = store.persist_bytes(
        b"image",
        kind="rgb_image",
        schema_name="image",
        schema_version="1.0",
    )
    store.blob_path(image).write_bytes(b"tampered")

    with pytest.raises(ContractError, match="artifact has invalid digest"):
        validate_port_value(
            operator="consumer",
            port_name="image",
            spec=PortSpec(("rgb_image",)),
            value=image,
            store=store,
        )


@pytest.mark.parametrize("value", [{"artifact_id": "sha256:bad"}, [None]])
def test_port_validation_rejects_unknown_carrier_objects(tmp_path, value) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    spec = PortSpec(
        ("rgb_image",),
        cardinality="one" if isinstance(value, dict) else "zero_or_more",
    )

    with pytest.raises(ContractError, match="unsupported carrier type"):
        validate_port_value(
            operator="backend",
            port_name="output",
            spec=spec,
            value=value,
            store=store,
        )


def test_collection_cardinality_requires_list_and_nonempty_one_or_more(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    image = store.persist_bytes(
        b"image", kind="rgb_image", schema_name="image", schema_version="1.0"
    )
    spec = PortSpec(("rgb_image",), cardinality="one_or_more")

    with pytest.raises(ContractError, match="requires a list"):
        validate_port_value(
            operator="multi", port_name="images", spec=spec, value=image, store=store
        )
    with pytest.raises(ContractError, match="at least one"):
        validate_port_value(operator="multi", port_name="images", spec=spec, value=[], store=store)
    validate_port_value(operator="multi", port_name="images", spec=spec, value=[image], store=store)
    for cardinality in ("zero_or_more", "many"):
        collection = PortSpec(("depth_map",), cardinality=cardinality)
        with pytest.raises(ContractError, match="requires a list"):
            validate_port_value(
                operator="multi",
                port_name="depths",
                spec=collection,
                value=None,
                store=store,
            )
        validate_port_value(
            operator="multi", port_name="depths", spec=collection, value=[], store=store
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"cardinality": "one_or_mroe"}, "unknown port cardinality"),
        ({"carriers": ("artifact",)}, "unknown port carriers"),
        ({"carriers": ()}, "at least one carrier"),
    ],
)
def test_port_spec_rejects_invalid_contract_enums(kwargs, message) -> None:
    with pytest.raises(ContractError, match=message):
        PortSpec(("rgb_image",), **kwargs)


def test_operator_yaml_rejects_invalid_cardinality_and_carrier(tmp_path) -> None:
    path = tmp_path / "operators.yaml"
    path.write_text(
        """
operators:
  - name: invalid
    version: 1
    inputs:
      images: {kind: rgb_image, cardinality: one_or_mroe, carriers: [artifact]}
""",
        encoding="utf-8",
    )

    with pytest.raises(ContractError, match="unknown port cardinality"):
        load_operator_specs(path)


def test_pipeline_compile_rejects_scalar_collection_cardinality_mismatch() -> None:
    specs = {
        "consumer@1": OperatorSpec(
            "consumer", "1", {"images": PortSpec(("rgb_image",), "one_or_more")}, {}
        )
    }
    pipeline = PipelineDefinition(
        "invalid",
        "1",
        {"image": PortSpec(("rgb_image",))},
        {"consumer": {"operator": "consumer@1", "inputs": {"images": "pipeline.inputs.image"}}},
    )

    with pytest.raises(ContractError, match="cardinality mismatch"):
        compile_pipeline(pipeline, specs)


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


def test_resolved_plan_rejects_backend_mapping_node_mismatch() -> None:
    binding = ResolvedBackend(
        node_id="reconstruct",
        operator="reconstruction@1",
        name="reconstruction",
        backend_version="test",
        implementation=object(),
    )

    with pytest.raises(ContractError, match="mapping key must match"):
        ResolvedPlan("pipeline", "1", "sha256:contract", {"estimate_geometry": binding})


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


def test_resolved_plan_rejects_override_for_core_node() -> None:
    registry = BackendRegistry()
    registry.register(
        name="external_canonicalize",
        operator="canonicalize@1",
        backend_version="test",
        implementation=object(),
    )

    with pytest.raises(ContractError, match="nodes with declared backends"):
        resolve_plan(
            load_default_pipeline(),
            registry,
            operator_specs=load_default_operator_specs(),
            backend_overrides={"canonicalize": "external_canonicalize"},
        )


def test_resolved_plan_requires_registered_default_backend() -> None:
    with pytest.raises(ContractError, match="backend is not registered: trellis2"):
        resolve_plan(
            load_default_pipeline(),
            BackendRegistry(),
            operator_specs=load_default_operator_specs(),
        )


def test_resolved_plan_supports_multiple_backend_operator_types() -> None:
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

    plan = resolve_plan(pipeline, registry, operator_specs=load_default_operator_specs())

    assert plan.backend_for("resolve_mask", "segmentation@1").name == "segmentation"
    assert plan.backend_for("generate_shape", "shape_generation@1").name == "trellis2"


def test_phase6_multi_view_pipeline_compiles_and_resolves_two_backends() -> None:
    from pathlib import Path

    specs = load_operator_specs(Path("pipelines/operators-v1.yaml"))
    pipeline = load_pipeline(Path("pipelines/multi_view_asset_v1.yaml"))
    compile_pipeline(pipeline, specs)
    registry = BackendRegistry()
    registry.register(
        name="geometry_frontend",
        operator="geometry_frontend@1",
        backend_version="test",
        implementation=object(),
    )
    registry.register(
        name="reconstruction",
        operator="reconstruction@1",
        backend_version="test",
        implementation=object(),
    )

    plan = resolve_plan(pipeline, registry, operator_specs=specs)

    assert set(plan.backends) == {"estimate_geometry", "reconstruct"}


def test_phase6_runtime_rejects_malformed_observation_bundle(tmp_path) -> None:
    from pathlib import Path

    store = LocalArtifactStore(tmp_path / "store")
    reference = store.persist_bytes(
        b"{}",
        kind="observation_bundle",
        schema_name="ObservationBundle",
        schema_version="1.0",
    )
    pipeline = load_pipeline(Path("pipelines/multi_view_asset_v1.yaml"))
    runtime = Phase1Runtime(
        store, pipeline, load_operator_specs(Path("pipelines/operators-v1.yaml"))
    )

    with pytest.raises(ContractError, match="views must be a list"):
        runtime.validate_pipeline_inputs({"observations": reference})


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
