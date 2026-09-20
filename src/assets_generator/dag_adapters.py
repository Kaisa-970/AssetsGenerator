"""Trusted CPU/human/gated-process adapters with immutable execution bindings.

HTTP and arbitrary unresolved Backend bindings remain unsupported. Process
adapters receive a worker with durable authorization and cross-run admission.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

from .artifact_store import LocalArtifactStore
from .compiled_plan import CompiledPlan, digest, freeze, thaw
from .contracts import ContractError
from .models import ArtifactRef, PortValue
from .serialization import canonical_json_bytes, sha256_bytes
from .workbench_context import ChildRunContext
from .worker import ProcessJobRequest, WorkerJob

_SCHEMA_KEYS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "minimum",
        "maximum",
    }
)
_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}


def _check_schema(schema: Mapping[str, Any]) -> None:
    if not isinstance(schema, Mapping) or set(schema) - _SCHEMA_KEYS:
        raise ContractError("unsupported adapter parameter schema keyword")
    kind = schema.get("type")
    if kind not in _TYPES:
        raise ContractError("adapter parameter schema requires a supported type")
    allowed = {"type", "enum"}
    if kind == "object":
        allowed |= {"properties", "required", "additionalProperties"}
        props = schema.get("properties", {})
        required = schema.get("required", ())
        if not isinstance(props, Mapping) or not isinstance(required, (tuple, list)):
            raise ContractError("invalid object parameter schema")
        if any(not isinstance(key, str) or key not in props for key in required):
            raise ContractError("required parameters must name declared properties")
        if type(schema.get("additionalProperties", False)) is not bool:
            raise ContractError("additionalProperties must be boolean")
        for subschema in props.values():
            _check_schema(subschema)
    elif kind == "array":
        allowed.add("items")
        _check_schema(schema.get("items", {}))
    elif kind in {"integer", "number"}:
        allowed |= {"minimum", "maximum"}
        for key in ("minimum", "maximum"):
            if key in schema and type(schema[key]) not in (int, float):
                raise ContractError("parameter bounds must be numbers")
        if schema.get("minimum", float("-inf")) > schema.get("maximum", float("inf")):
            raise ContractError("parameter minimum exceeds maximum")
    if set(schema) - allowed:
        raise ContractError("parameter schema keywords do not apply to its type")
    if "enum" in schema and (not isinstance(schema["enum"], (list, tuple)) or not schema["enum"]):
        raise ContractError("parameter enum must be a nonempty sequence")
    freeze(schema)


def _validate(value: Any, schema: Mapping[str, Any], path: str) -> None:
    kind = schema["type"]
    valid = {
        "object": isinstance(value, Mapping),
        "array": isinstance(value, (tuple, list)),
        "string": type(value) is str,
        "integer": type(value) is int,
        "number": type(value) in (int, float),
        "boolean": type(value) is bool,
        "null": value is None,
    }[kind]
    if not valid:
        raise ContractError(f"{path} requires {kind}")
    if "enum" in schema and not any(digest(value) == digest(option) for option in schema["enum"]):
        raise ContractError(f"{path} is outside enum")
    if kind == "object":
        props = schema.get("properties", {})
        if set(schema.get("required", ())) - set(value):
            raise ContractError(f"{path} is missing required parameters")
        if not schema.get("additionalProperties", False) and set(value) - set(props):
            raise ContractError(f"{path} contains unknown parameters")
        for key, item in value.items():
            if key in props:
                _validate(item, props[key], f"{path}.{key}")
    elif kind == "array":
        for index, item in enumerate(value):
            _validate(item, schema["items"], f"{path}[{index}]")
    elif kind in {"integer", "number"}:
        if "minimum" in schema and value < schema["minimum"]:
            raise ContractError(f"{path} is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ContractError(f"{path} exceeds maximum")


@dataclass(frozen=True)
class AdapterSpec:
    name: str
    version: str
    operators: tuple[str, ...]
    parameter_schema: Mapping[str, Any] = field(default_factory=lambda: {"type": "object"})
    defaults: Mapping[str, Any] = field(default_factory=dict)
    execution_kind: str = "cpu"
    uses_child_run: bool = False

    def __post_init__(self) -> None:
        if any(not isinstance(v, str) or not v or "@" in v for v in (self.name, self.version)):
            raise ContractError("adapter requires name and version")
        if not self.operators or any(
            not isinstance(v, str) or len(v.split("@")) != 2 or not all(v.split("@"))
            for v in self.operators
        ):
            raise ContractError("adapter requires versioned operators")
        if self.execution_kind not in {"cpu", "human", "process"}:
            raise ContractError("supports CPU, human and gated process adapters only")
        if type(self.uses_child_run) is not bool:
            raise ContractError("uses_child_run must be boolean")
        _check_schema(self.parameter_schema)
        if self.parameter_schema["type"] != "object":
            raise ContractError("adapter parameter schema must describe object")
        object.__setattr__(self, "operators", tuple(sorted(set(self.operators))))
        object.__setattr__(self, "parameter_schema", freeze(self.parameter_schema))
        object.__setattr__(self, "defaults", freeze(self.defaults))
        if not isinstance(self.defaults, Mapping):
            raise ContractError("adapter defaults must be an object")
        partial = {**thaw(self.parameter_schema), "required": []}
        _validate(self.defaults, partial, "defaults")

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "operators": list(self.operators),
            "parameter_schema": thaw(self.parameter_schema),
            "defaults": thaw(self.defaults),
            "execution_kind": self.execution_kind,
            **({"uses_child_run": True} if self.uses_child_run else {}),
        }

    def normalize_parameters(self, parameters: Mapping[str, Any]) -> Mapping[str, Any]:
        result: Mapping[str, Any] = freeze({**thaw(self.defaults), **thaw(parameters)})
        _validate(result, self.parameter_schema, "parameters")
        return result


class ProcessWorker(Protocol):
    def run(self, request: ProcessJobRequest) -> WorkerJob: ...


@dataclass(frozen=True)
class NodeExecutionContext:
    run_id: str
    node_id: str
    inputs: Mapping[str, PortValue | list[PortValue]]
    parameters: Mapping[str, Any]
    store: LocalArtifactStore
    decision: ArtifactRef | None = None
    attempt_id: str = ""
    input_digest: str = ""
    worker: ProcessWorker | None = None
    child_context: ChildRunContext | None = None
    output_path: Path | None = None


@dataclass(frozen=True)
class NodeExecutionResult:
    outputs: Mapping[str, PortValue | list[PortValue]] = field(default_factory=dict)
    wait_request: ArtifactRef | None = None

    def __post_init__(self) -> None:
        if self.wait_request is not None and self.outputs:
            raise ContractError("human wait cannot also complete outputs")
        if self.wait_request is not None and not isinstance(self.wait_request, ArtifactRef):
            raise ContractError("human request must be an ArtifactRef")


class NodeAdapter(Protocol):
    @property
    def spec(self) -> AdapterSpec: ...
    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult: ...


def adapter_implementation_digest(adapter: NodeAdapter) -> str:
    """Pin actual trusted Python module bytes and class, without absolute paths.

    Transitive dependency identity is not inferred. Adapters must not hide model
    execution/configuration in mutable instance state; use declared parameters.
    """
    cls = type(adapter)
    source = inspect.getsourcefile(cls)
    if source is None:
        raise ContractError("adapter implementation requires inspectable source")
    try:
        content = Path(source).read_bytes()
    except OSError as error:
        raise ContractError("adapter implementation source is unavailable") from error
    return digest(
        {"module": cls.__module__, "class": cls.__qualname__, "source": sha256_bytes(content)}
    )


@dataclass(frozen=True)
class BoundAdapter:
    adapter: str
    spec: Mapping[str, Any]
    spec_digest: str
    implementation_digest: str
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "spec", freeze(self.spec))
        object.__setattr__(self, "parameters", freeze(self.parameters))

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "spec": thaw(self.spec),
            "spec_digest": self.spec_digest,
            "implementation_digest": self.implementation_digest,
            "parameters": thaw(self.parameters),
        }


@dataclass(frozen=True)
class BoundDagPlan:
    plan_id: str
    static_plan: CompiledPlan
    bindings: Mapping[str, BoundAdapter]
    schema_version: str = "bound_dag_plan@1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "bindings", MappingProxyType(dict(sorted(self.bindings.items()))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "schema_version": self.schema_version,
            "static_plan": self.static_plan.to_dict(),
            "bindings": {key: value.to_dict() for key, value in self.bindings.items()},
        }

    def to_json(self) -> str:
        return canonical_json_bytes(self.to_dict()).decode()

    @classmethod
    def from_dict(
        cls, raw: Mapping[str, Any], *, registry: AdapterRegistry, relation_registry: Any = None
    ) -> BoundDagPlan:
        try:
            freeze(raw)
            static = CompiledPlan.from_dict(raw["static_plan"], relation_registry=relation_registry)
            result = registry.bind_plan(static, relation_registry=relation_registry)
            if canonical_json_bytes(result.to_dict()) != canonical_json_bytes(thaw(raw)):
                raise ContractError("bound DAG plan does not match registered execution bindings")
            return result
        except (KeyError, TypeError, AttributeError) as error:
            raise ContractError("invalid bound DAG plan") from error

    @classmethod
    def from_json(
        cls, value: str, *, registry: AdapterRegistry, relation_registry: Any = None
    ) -> BoundDagPlan:
        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, item in pairs:
                if key in result:
                    raise ContractError(f"duplicate bound plan field: {key}")
                result[key] = item
            return result

        try:
            raw = json.loads(value, object_pairs_hook=unique)
        except (TypeError, ValueError) as error:
            raise ContractError("invalid bound DAG JSON") from error
        return cls.from_dict(raw, registry=registry, relation_registry=relation_registry)


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, NodeAdapter] = {}

    def register(self, adapter: NodeAdapter) -> None:
        spec = adapter.spec
        if not isinstance(spec, AdapterSpec) or not callable(getattr(adapter, "execute", None)):
            raise ContractError("adapter requires AdapterSpec and execute")
        if spec.key in self._adapters:
            raise ContractError(f"duplicate adapter: {spec.key}")
        adapter_implementation_digest(adapter)
        self._adapters[spec.key] = adapter

    def resolve(self, binding: BoundAdapter) -> NodeAdapter:
        adapter = self._adapters.get(binding.adapter)
        if adapter is None:
            raise ContractError(f"missing adapter: {binding.adapter}")
        spec = adapter.spec.to_dict()
        if (
            canonical_json_bytes(spec) != canonical_json_bytes(thaw(binding.spec))
            or digest(spec) != binding.spec_digest
            or adapter_implementation_digest(adapter) != binding.implementation_digest
        ):
            raise ContractError(f"adapter identity changed: {binding.adapter}")
        adapter.spec.normalize_parameters(binding.parameters)
        return adapter

    def bind_plan(self, plan: CompiledPlan, *, relation_registry: Any = None) -> BoundDagPlan:
        # Recompile even in-memory instances; frozen dataclasses are not authority.
        plan = CompiledPlan.from_dict(plan.to_dict(), relation_registry=relation_registry)
        if not plan.require_explicit_joins:
            raise ContractError("DAG execution requires explicit join relation compilation")
        bindings: dict[str, BoundAdapter] = {}
        for node in plan.nodes:
            if node.backend is not None:
                raise ContractError(f"unresolved Backend not supported in A2: {node.backend}")
            choices = [
                a
                for a in self._adapters.values()
                if node.operator in a.spec.operators
                and (node.adapter is None or a.spec.key == node.adapter)
            ]
            if len(choices) != 1:
                raise ContractError(f"node {node.node_id} requires exactly one compatible adapter")
            adapter = choices[0]
            spec = adapter.spec.to_dict()
            bindings[node.node_id] = BoundAdapter(
                adapter.spec.key,
                spec,
                digest(spec),
                adapter_implementation_digest(adapter),
                adapter.spec.normalize_parameters(node.parameters),
            )
        provisional = BoundDagPlan("", plan, bindings)
        body = provisional.to_dict()
        del body["plan_id"]
        return BoundDagPlan(digest(body), plan, bindings)
