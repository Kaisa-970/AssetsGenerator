"""Immutable static compilation evidence; not a dispatchable execution plan.

Adapter and Backend requests are deliberately unresolved in schema 1. Existing
ResolvedPlan remains the legacy workflow's runtime binding until milestone A2.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .contracts import ContractError, PortSpec
from .serialization import canonical_json_bytes, sha256_bytes, to_primitive

SCHEMA_VERSION = "compiled_plan_static@1"


def freeze(value: Any) -> Any:
    """Copy JSON values recursively, rejecting opaque/mutable foreign objects."""
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ContractError("compiled plan mapping keys must be strings")
        return MappingProxyType({key: freeze(value[key]) for key in sorted(value)})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ContractError(f"compiled plan requires finite JSON values, got {type(value).__name__}")


def thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    return value


def digest(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(thaw(value)))


@dataclass(frozen=True)
class CompiledInputSpec:
    """Deeply immutable projection, including optional/persistence semantics."""

    contract: Mapping[str, Any]

    def __post_init__(self) -> None:
        for key in ("requires_frame", "requires_unit", "persist"):
            if type(self.contract.get(key)) is not bool:
                raise ContractError(f"compiled port {key} must be boolean")
        for key in ("kinds", "carriers"):
            values = self.contract.get(key)
            if (
                not isinstance(values, (list, tuple))
                or not values
                or any(not isinstance(value, str) or not value for value in values)
            ):
                raise ContractError(f"compiled port {key} must be a nonempty string sequence")
        for key in ("schema_name", "schema_version"):
            value = self.contract.get(key)
            if value is not None and (not isinstance(value, str) or not value):
                raise ContractError(f"compiled port {key} must be nonempty text or null")
        for key in ("frame_id", "unit", "media_type"):
            value = self.contract.get(key)
            if value is not None and (not isinstance(value, str) or not value):
                raise ContractError(f"compiled port {key} must be nonempty text or null")
        object.__setattr__(self, "contract", freeze(self.contract))

    @classmethod
    def from_port(cls, port: PortSpec) -> CompiledInputSpec:
        return cls(to_primitive(port))


@dataclass(frozen=True)
class CompiledBinding:
    source: str
    port: str
    node_id: str | None = None
    optional: bool = False

    @property
    def reference(self) -> str:
        prefix = "pipeline.inputs" if self.source == "pipeline_input" else f"{self.node_id}.outputs"
        return f"{prefix}.{self.port}" + ("?" if self.optional else "")


@dataclass(frozen=True)
class CompiledNode:
    node_id: str
    operator: str
    operator_contract: Mapping[str, Any]
    operator_contract_digest: str
    adapter: str | None
    backend: str | None
    inputs: Mapping[str, CompiledBinding]
    parameters: Mapping[str, Any]
    relations: tuple[Mapping[str, Any], ...] = ()
    resolution_status: str = "unresolved"

    def __post_init__(self) -> None:
        object.__setattr__(self, "operator_contract", freeze(self.operator_contract))
        object.__setattr__(self, "parameters", freeze(self.parameters))
        object.__setattr__(self, "inputs", MappingProxyType(dict(sorted(self.inputs.items()))))
        object.__setattr__(self, "relations", tuple(freeze(item) for item in self.relations))

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "operator": self.operator,
            "operator_contract": thaw(self.operator_contract),
            "operator_contract_digest": self.operator_contract_digest,
            "adapter": self.adapter,
            "backend": self.backend,
            "inputs": {key: to_primitive(value) for key, value in self.inputs.items()},
            "parameters": thaw(self.parameters),
            "relations": thaw(self.relations),
            "resolution_status": self.resolution_status,
        }


@dataclass(frozen=True)
class CompiledPlan:
    plan_id: str
    pipeline_name: str
    pipeline_version: str
    inputs: Mapping[str, CompiledInputSpec]
    nodes: tuple[CompiledNode, ...]
    topological_order: tuple[str, ...]
    dependencies: Mapping[str, tuple[str, ...]]
    dependents: Mapping[str, tuple[str, ...]]
    contract_digests: Mapping[str, str]
    require_explicit_joins: bool = False
    schema_version: str = SCHEMA_VERSION
    resolution_status: str = "unresolved"

    def __post_init__(self) -> None:
        object.__setattr__(self, "inputs", MappingProxyType(dict(sorted(self.inputs.items()))))
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "topological_order", tuple(self.topological_order))
        for name in ("dependencies", "dependents", "contract_digests"):
            object.__setattr__(self, name, freeze(getattr(self, name)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "pipeline_name": self.pipeline_name,
            "pipeline_version": self.pipeline_version,
            "inputs": {key: thaw(value.contract) for key, value in self.inputs.items()},
            "nodes": [node.to_dict() for node in self.nodes],
            "topological_order": list(self.topological_order),
            "dependencies": thaw(self.dependencies),
            "dependents": thaw(self.dependents),
            "contract_digests": thaw(self.contract_digests),
            "require_explicit_joins": self.require_explicit_joins,
            "schema_version": self.schema_version,
            "resolution_status": self.resolution_status,
        }

    def to_json(self) -> str:
        return canonical_json_bytes(self.to_dict()).decode("utf-8")

    @classmethod
    def from_json(cls, value: str, *, relation_registry: Any = None) -> CompiledPlan:
        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, item in pairs:
                if key in result:
                    raise ContractError(f"duplicate compiled plan field: {key}")
                result[key] = item
            return result

        try:
            raw = json.loads(value, object_pairs_hook=unique)
        except (TypeError, ValueError) as error:
            raise ContractError(f"invalid compiled plan JSON: {error}") from error
        return cls.from_dict(raw, relation_registry=relation_registry)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, relation_registry: Any = None) -> CompiledPlan:
        # Recompile stored declarations, then compare every derived field. A digest
        # alone cannot prove graph consistency, even when recalculated by a caller.
        from .pipeline import (
            PipelineDefinition,
            _operator_specs_from_raw,
            _port_spec,
            compile_pipeline,
        )

        try:
            freeze(raw)
            if raw["schema_version"] != SCHEMA_VERSION:
                raise ContractError("unsupported compiled plan schema")
            if type(raw["require_explicit_joins"]) is not bool:
                raise ContractError("require_explicit_joins must be boolean")
            nodes: dict[str, dict[str, Any]] = {}
            contracts: dict[str, Any] = {}
            for node in raw["nodes"]:
                node_id = node["node_id"]
                if node_id in nodes:
                    raise ContractError(f"duplicate compiled node: {node_id}")
                contract = node["operator_contract"]
                key = node["operator"]
                if key in contracts and contracts[key] != contract:
                    raise ContractError(f"conflicting operator contract: {key}")
                contracts[key] = contract
                bindings = {
                    port: CompiledBinding(**binding).reference
                    for port, binding in node["inputs"].items()
                }
                definition = {"operator": key, "inputs": bindings, "parameters": node["parameters"]}
                for field in ("adapter", "backend"):
                    if node[field] is not None:
                        definition[field] = node[field]
                nodes[node_id] = definition
            specs = _operator_specs_from_raw({"operators": list(contracts.values())})
            pipeline = PipelineDefinition(
                raw["pipeline_name"],
                raw["pipeline_version"],
                {key: _port_spec(value) for key, value in raw["inputs"].items()},
                nodes,
            )
            result = compile_pipeline(
                pipeline,
                specs,
                relation_registry=relation_registry,
                require_explicit_joins=raw["require_explicit_joins"],
            )
            if canonical_json_bytes(raw) != canonical_json_bytes(result.to_dict()):
                raise ContractError("compiled plan identity or derived fields do not match")
            return result
        except (KeyError, TypeError, AttributeError, ValueError) as error:
            if isinstance(error, ContractError):
                raise
            raise ContractError(f"invalid compiled plan: {error}") from error
