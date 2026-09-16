from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from .contracts import ContractError, OperatorSpec, PortSpec, effective_output_spec


@dataclass(frozen=True)
class PipelineDefinition:
    name: str
    version: str
    inputs: dict[str, PortSpec]
    nodes: dict[str, dict[str, Any]]


def _port_spec(raw: dict[str, Any]) -> PortSpec:
    kinds = raw.get("kinds") or [raw["kind"]]
    carriers = tuple(raw.get("carriers", ["artifact_ref", "structured"]))
    return PortSpec(
        tuple(kinds),
        raw.get("cardinality", "one"),
        carriers,
        raw.get("schema_name"),
        str(raw["schema_version"]) if "schema_version" in raw else None,
        raw.get("requires_frame", False),
        raw.get("requires_unit", False),
        raw.get("persist", False),
    )


def load_operator_specs(path: Path) -> dict[str, OperatorSpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _operator_specs_from_raw(raw)


def _operator_specs_from_raw(raw: dict[str, Any]) -> dict[str, OperatorSpec]:
    specs: dict[str, OperatorSpec] = {}
    for item in raw["operators"]:
        spec = OperatorSpec(
            name=item["name"],
            version=str(item["version"]),
            inputs={name: _port_spec(value) for name, value in item.get("inputs", {}).items()},
            outputs={name: _port_spec(value) for name, value in item.get("outputs", {}).items()},
        )
        key = f"{spec.name}@{spec.version}"
        if key in specs:
            raise ContractError(f"duplicate operator spec: {key}")
        specs[key] = spec
    return specs


def load_pipeline(path: Path) -> PipelineDefinition:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _pipeline_from_raw(raw)


def _pipeline_from_raw(raw: dict[str, Any]) -> PipelineDefinition:
    return PipelineDefinition(
        name=raw["pipeline"],
        version=str(raw["version"]),
        inputs={name: _port_spec(value) for name, value in raw["inputs"].items()},
        nodes=raw["nodes"],
    )


def load_default_operator_specs() -> dict[str, OperatorSpec]:
    resource = files("assets_generator.resources").joinpath("operators-v1.yaml")
    raw = yaml.safe_load(resource.read_text(encoding="utf-8"))
    return _operator_specs_from_raw(raw)


def load_default_pipeline() -> PipelineDefinition:
    resource = files("assets_generator.resources").joinpath("image_asset_v2.yaml")
    raw = yaml.safe_load(resource.read_text(encoding="utf-8"))
    return _pipeline_from_raw(raw)


def compile_pipeline(pipeline: PipelineDefinition, specs: dict[str, OperatorSpec]) -> None:
    deps: dict[str, set[str]] = {node_id: set() for node_id in pipeline.nodes}
    for node_id, node in pipeline.nodes.items():
        for reference in dict(node.get("inputs", {})).values():
            key = str(reference).removesuffix("?")
            if key.startswith("pipeline.inputs."):
                continue
            source_node = key.split(".outputs.", 1)[0]
            if source_node not in pipeline.nodes:
                raise ContractError(f"{node_id} references unknown node {source_node}")
            deps[node_id].add(source_node)
    ready = [node_id for node_id, values in deps.items() if not values]
    order: list[str] = []
    while ready:
        node_id = ready.pop(0)
        order.append(node_id)
        for candidate, values in deps.items():
            if node_id in values:
                values.remove(node_id)
                if not values:
                    ready.append(candidate)
    if len(order) != len(pipeline.nodes):
        raise ContractError(
            f"pipeline contains cycle involving nodes: {sorted(set(pipeline.nodes) - set(order))}"
        )
    available: dict[str, PortSpec] = {
        f"pipeline.inputs.{name}": spec for name, spec in pipeline.inputs.items()
    }
    for node_id in order:
        node = pipeline.nodes[node_id]
        operator_key = str(node["operator"])
        if operator_key not in specs:
            raise ContractError(f"{node_id} references unknown operator {operator_key}")
        operator = specs[operator_key]
        bindings = node.get("inputs", {})
        unknown_bindings = set(bindings) - set(operator.inputs)
        if unknown_bindings:
            raise ContractError(f"{node_id} has unknown input bindings: {sorted(unknown_bindings)}")
        for port_name, port in operator.inputs.items():
            reference = bindings.get(port_name)
            if reference is None:
                if port.cardinality == "one":
                    raise ContractError(f"{node_id}.{port_name} is not bound")
                continue
            optional = str(reference).endswith("?")
            key = str(reference).removesuffix("?")
            if key not in available:
                raise ContractError(f"{node_id}.{port_name} references unavailable {key}")
            source_spec = available[key]
            if not set(source_spec.kinds) <= set(port.kinds):
                raise ContractError(f"{node_id}.{port_name} kind mismatch")
            if not set(source_spec.carriers) <= set(port.carriers):
                raise ContractError(f"{node_id}.{port_name} carrier mismatch")
            if port.schema_name is not None and source_spec.schema_name != port.schema_name:
                raise ContractError(f"{node_id}.{port_name} schema mismatch")
            if (
                port.schema_version is not None
                and source_spec.schema_version != port.schema_version
            ):
                raise ContractError(f"{node_id}.{port_name} schema version mismatch")
            if optional and port.cardinality == "one":
                raise ContractError(f"{node_id}.{port_name} cannot use an optional reference")
        for output_name, output in operator.outputs.items():
            available[f"{node_id}.outputs.{output_name}"] = effective_output_spec(output)
