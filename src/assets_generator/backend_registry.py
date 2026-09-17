from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, cast

from .artifact_store import LocalArtifactStore
from .contracts import ContractError, OperatorSpec
from .models import ArtifactRef, StructuredValue
from .operators import GeometryFrontendOutput, ReconstructionOutput, ShapeOutput
from .pipeline import PipelineDefinition
from .serialization import canonical_json_bytes, sha256_bytes, to_primitive


class ShapeBackend(Protocol):
    def generate(
        self,
        store: LocalArtifactStore,
        rgba: ArtifactRef,
        *,
        seed: int = 42,
        pipeline_type: str = "512",
    ) -> ShapeOutput: ...


class GeometryFrontendBackend(Protocol):
    def estimate(
        self, store: LocalArtifactStore, observations: ArtifactRef
    ) -> GeometryFrontendOutput: ...


class ReconstructionBackend(Protocol):
    def reconstruct(
        self,
        store: LocalArtifactStore,
        observations: ArtifactRef,
        cameras: list[StructuredValue],
        depths: list[ArtifactRef],
        points: ArtifactRef,
    ) -> ReconstructionOutput: ...


@dataclass(frozen=True)
class BackendRegistration:
    name: str
    operator: str
    backend_version: str
    implementation: object


class BackendRegistry:
    def __init__(self) -> None:
        self._registrations: dict[str, BackendRegistration] = {}

    def register(
        self,
        *,
        name: str,
        operator: str,
        backend_version: str,
        implementation: object,
    ) -> None:
        if name in self._registrations:
            raise ContractError(f"duplicate backend registration: {name}")
        self._registrations[name] = BackendRegistration(
            name, operator, backend_version, implementation
        )

    def resolve(self, name: str, operator: str) -> BackendRegistration:
        try:
            registration = self._registrations[name]
        except KeyError as error:
            raise ContractError(f"backend is not registered: {name}") from error
        if registration.operator != operator:
            raise ContractError(
                f"backend {name} implements {registration.operator}, not {operator}"
            )
        return registration


@dataclass(frozen=True)
class ResolvedBackend:
    node_id: str
    operator: str
    name: str
    backend_version: str
    implementation: object

    def shape_backend(self) -> ShapeBackend:
        if self.operator != "shape_generation@1":
            raise ContractError(f"node {self.node_id} is not a shape generation node")
        return cast(ShapeBackend, self.implementation)

    def geometry_frontend_backend(self) -> GeometryFrontendBackend:
        if self.operator != "geometry_frontend@1":
            raise ContractError(f"node {self.node_id} is not a geometry frontend node")
        return cast(GeometryFrontendBackend, self.implementation)

    def reconstruction_backend(self) -> ReconstructionBackend:
        if self.operator != "reconstruction@1":
            raise ContractError(f"node {self.node_id} is not a reconstruction node")
        return cast(ReconstructionBackend, self.implementation)


@dataclass(frozen=True)
class ResolvedPlan:
    pipeline_name: str
    pipeline_version: str
    contract_digest: str
    backends: Mapping[str, ResolvedBackend]

    def __post_init__(self) -> None:
        backends = dict(self.backends)
        for node_id, binding in backends.items():
            if not node_id or node_id != binding.node_id:
                raise ContractError("resolved backend mapping key must match binding node_id")
            if not binding.operator or not binding.name or not binding.backend_version:
                raise ContractError("resolved backend binding fields must not be empty")
        object.__setattr__(self, "backends", MappingProxyType(backends))

    def backend_for(self, node_id: str, operator: str) -> ResolvedBackend:
        try:
            binding = self.backends[node_id]
        except KeyError as error:
            raise ContractError(f"node has no resolved backend: {node_id}") from error
        if binding.operator != operator:
            raise ContractError(
                f"resolved backend for {node_id} implements {binding.operator}, not {operator}"
            )
        return binding


def resolve_plan(
    pipeline: PipelineDefinition,
    registry: BackendRegistry,
    *,
    operator_specs: Mapping[str, OperatorSpec],
    backend_overrides: dict[str, str] | None = None,
) -> ResolvedPlan:
    overrides = backend_overrides or {}
    unknown_nodes = set(overrides) - set(pipeline.nodes)
    if unknown_nodes:
        raise ContractError(f"backend overrides reference unknown nodes: {sorted(unknown_nodes)}")
    unsupported_overrides = {
        node_id for node_id in overrides if pipeline.nodes[node_id].get("backend") is None
    }
    if unsupported_overrides:
        raise ContractError(
            "backend overrides require nodes with declared backends: "
            f"{sorted(unsupported_overrides)}"
        )
    bindings: dict[str, ResolvedBackend] = {}
    for node_id, node in pipeline.nodes.items():
        backend_name = overrides.get(node_id, node.get("backend"))
        if backend_name is None:
            continue
        operator = str(node["operator"])
        registration = registry.resolve(str(backend_name), operator)
        bindings[node_id] = ResolvedBackend(
            node_id,
            operator,
            registration.name,
            registration.backend_version,
            registration.implementation,
        )
    digest = resolve_plan_contract_digest(pipeline, operator_specs)
    return ResolvedPlan(pipeline.name, pipeline.version, digest, bindings)


def resolve_plan_contract_digest(
    pipeline: PipelineDefinition, operator_specs: Mapping[str, OperatorSpec]
) -> str:
    referenced = {str(node["operator"]) for node in pipeline.nodes.values()}
    missing = referenced - set(operator_specs)
    if missing:
        raise ContractError(f"resolved plan is missing operator specs: {sorted(missing)}")
    contract = {
        "pipeline": to_primitive(pipeline),
        "operator_specs": to_primitive({key: operator_specs[key] for key in sorted(referenced)}),
    }
    return sha256_bytes(canonical_json_bytes(contract))
