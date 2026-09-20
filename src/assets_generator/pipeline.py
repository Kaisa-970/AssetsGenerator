from __future__ import annotations

import heapq
import re
from dataclasses import dataclass, replace
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from .compiled_plan import CompiledBinding, CompiledInputSpec, CompiledNode, CompiledPlan, digest
from .contracts import (
    ContractError,
    OperatorSpec,
    PortSpec,
    RelationSpec,
    cardinality_compatible,
    effective_output_spec,
)
from .relations import RelationValidatorRegistry, default_relation_registry
from .serialization import to_primitive


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
        str(raw["schema_version"]) if raw.get("schema_version") is not None else None,
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
            relations=tuple(
                RelationSpec(relation["validator"], relation["inputs"])
                for relation in item.get("relations", ())
            ),
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


def load_multi_view_pipeline() -> PipelineDefinition:
    resource = files("assets_generator.resources").joinpath("multi_view_asset_v1.yaml")
    raw = yaml.safe_load(resource.read_text(encoding="utf-8"))
    return _pipeline_from_raw(raw)


def compile_pipeline(
    pipeline: PipelineDefinition,
    specs: dict[str, OperatorSpec],
    *,
    relation_registry: RelationValidatorRegistry | None = None,
    require_explicit_joins: bool = False,
) -> CompiledPlan:
    """Compile static evidence. Adapter/Backend requests remain unresolved in A1."""
    if not isinstance(pipeline.name, str) or not pipeline.name:
        raise ContractError("pipeline name must be nonempty")
    if not isinstance(pipeline.version, str) or not pipeline.version:
        raise ContractError("pipeline version must be nonempty")
    registry = relation_registry if relation_registry is not None else default_relation_registry()
    identifier = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*\Z")
    for input_name in pipeline.inputs:
        if not identifier.fullmatch(input_name):
            raise ContractError(f"invalid pipeline input name: {input_name}")
    normalized: dict[str, dict[str, CompiledBinding]] = {}
    for node_id, node in pipeline.nodes.items():
        if not identifier.fullmatch(node_id) or node_id == "pipeline":
            raise ContractError(f"invalid node ID: {node_id}")
        unknown = set(node) - {
            "operator",
            "inputs",
            "parameters",
            "adapter",
            "backend",
            "label",
            "ui_layout",
        }
        if unknown:
            raise ContractError(f"{node_id} has unknown fields: {sorted(unknown)}")
        for field in ("operator", "adapter", "backend"):
            if field == "operator" or field in node:
                if not isinstance(node.get(field), str) or not node[field]:
                    raise ContractError(f"{node_id}.{field} must be nonempty text")
        if not isinstance(node.get("inputs", {}), dict) or not isinstance(
            node.get("parameters", {}), dict
        ):
            raise ContractError(f"{node_id} inputs and parameters must be mappings")
        normalized[node_id] = {}
        for port, reference in node.get("inputs", {}).items():
            if not isinstance(reference, str):
                raise ContractError(f"{node_id}.{port} binding must be a reference string")
            match = re.fullmatch(
                r"(?:(pipeline)\.inputs|([A-Za-z_][A-Za-z0-9_-]*)\.outputs)\.([A-Za-z_][A-Za-z0-9_-]*)(\?)?",
                reference,
            )
            if match is None:
                raise ContractError(f"{node_id}.{port} has invalid reference: {reference}")
            normalized[node_id][port] = CompiledBinding(
                "pipeline_input" if match[1] else "node_output", match[3], match[2], bool(match[4])
            )
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
    dependencies = {node: tuple(sorted(values)) for node, values in deps.items()}
    ready = sorted(node_id for node_id, values in deps.items() if not values)
    order: list[str] = []
    while ready:
        node_id = heapq.heappop(ready)
        order.append(node_id)
        for candidate, values in deps.items():
            if node_id in values:
                values.remove(node_id)
                if not values:
                    heapq.heappush(ready, candidate)
    if len(order) != len(pipeline.nodes):
        raise ContractError(
            f"pipeline contains cycle involving nodes: {sorted(set(pipeline.nodes) - set(order))}"
        )
    available: dict[str, PortSpec] = {
        f"pipeline.inputs.{name}": spec for name, spec in pipeline.inputs.items()
    }
    compiled_nodes: list[CompiledNode] = []
    contract_digests: dict[str, str] = {}
    for node_id in order:
        node = pipeline.nodes[node_id]
        operator_key = str(node["operator"])
        if operator_key not in specs:
            raise ContractError(f"{node_id} references unknown operator {operator_key}")
        operator = specs[operator_key]
        if operator_key != f"{operator.name}@{operator.version}":
            raise ContractError(f"{node_id} operator registry key disagrees with spec identity")
        bindings = node.get("inputs", {})
        unknown_bindings = set(bindings) - set(operator.inputs)
        if unknown_bindings:
            raise ContractError(f"{node_id} has unknown input bindings: {sorted(unknown_bindings)}")
        for port_name, port in operator.inputs.items():
            reference = bindings.get(port_name)
            if reference is None:
                if port.cardinality in {"one", "one_or_more"}:
                    raise ContractError(f"{node_id}.{port_name} is not bound")
                continue
            optional = str(reference).endswith("?")
            key = str(reference).removesuffix("?")
            if key not in available:
                raise ContractError(f"{node_id}.{port_name} references unavailable {key}")
            source_spec = available[key]
            if not cardinality_compatible(
                source_spec.cardinality, port.cardinality, optional=optional
            ):
                raise ContractError(f"{node_id}.{port_name} cardinality mismatch")
            if port.requires_frame and not source_spec.requires_frame:
                raise ContractError(f"{node_id}.{port_name} source does not guarantee frame")
            if port.requires_unit and not source_spec.requires_unit:
                raise ContractError(f"{node_id}.{port_name} source does not guarantee unit")
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
        for output_name, output in operator.outputs.items():
            available[f"{node_id}.outputs.{output_name}"] = effective_output_spec(output)

        relations = registry.validate_static(
            operator, bindings, require_explicit_joins=require_explicit_joins
        )
        for port in (*operator.inputs.values(), *operator.outputs.values()):
            CompiledInputSpec.from_port(port)
        contract = to_primitive(operator)
        contract_digest = digest(contract)
        contract_digests[operator_key] = contract_digest
        compiled_nodes.append(
            CompiledNode(
                node_id,
                operator_key,
                contract,
                contract_digest,
                node.get("adapter"),
                node.get("backend"),
                normalized[node_id],
                node.get("parameters", {}),
                tuple(to_primitive(value) for value in relations),
            )
        )
    inputs = {name: CompiledInputSpec.from_port(port) for name, port in pipeline.inputs.items()}
    contract_digests["pipeline.inputs"] = digest(
        {name: value.contract for name, value in inputs.items()}
    )
    dependents = {
        node: tuple(sorted(other for other, values in dependencies.items() if node in values))
        for node in pipeline.nodes
    }
    plan = CompiledPlan(
        "",
        pipeline.name,
        pipeline.version,
        inputs,
        tuple(compiled_nodes),
        tuple(order),
        dependencies,
        dependents,
        contract_digests,
        require_explicit_joins,
    )
    identity = plan.to_dict()
    del identity["plan_id"]
    return replace(plan, plan_id=digest(identity))
