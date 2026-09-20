"""Versioned cross-input contracts; no scheduler or model-specific joins.

Static checks inspect bindings and port declarations only. Runtime checks must run
again on verified values before dispatch. The independent-inputs validator is an
explicit semantic declaration, not evidence of shared frame, source or lineage.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from .artifact_store import LocalArtifactStore
from .contracts import ContractError, OperatorSpec, PortSpec, validate_operator_inputs
from .models import PortValue
from .serialization import canonical_json_bytes, sha256_bytes


@dataclass(frozen=True)
class RelationValidatorSpec:
    """A trusted registration's pinned implementation identity.

    Custom registrations must supply their own reproducible implementation
    digest; the registry cannot prove arbitrary Python code matches that claim.
    """

    name: str
    version: str
    implementation_digest: str

    def __post_init__(self) -> None:
        if not self.name or not self.version or "@" in self.name or "@" in self.version:
            raise ContractError("relation validator requires name and version")
        digest = self.implementation_digest
        if (
            not digest.startswith("sha256:")
            or len(digest) != 71
            or any(c not in "0123456789abcdef" for c in digest[7:])
        ):
            raise ContractError("relation implementation_digest must be a SHA-256 digest")

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_json_bytes(self))


@dataclass(frozen=True)
class ResolvedRelation:
    name: str
    version: str
    digest: str
    inputs: tuple[str, ...]

    @property
    def validator(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(frozen=True)
class StaticRelationContext:
    operator: str
    inputs: tuple[str, ...]
    ports: Mapping[str, PortSpec]
    bindings: Mapping[str, str]


@dataclass(frozen=True)
class RuntimeRelationContext:
    operator: str
    inputs: tuple[str, ...]
    values: Mapping[str, PortValue | tuple[PortValue, ...] | None]
    store: LocalArtifactStore


class RelationValidator(Protocol):
    @property
    def spec(self) -> RelationValidatorSpec: ...

    def validate_static(self, context: StaticRelationContext) -> None: ...

    def validate_runtime(self, context: RuntimeRelationContext) -> None: ...


class RelationValidatorRegistry:
    def __init__(self) -> None:
        self._validators: dict[str, RelationValidator] = {}
        self._specs: dict[str, RelationValidatorSpec] = {}

    def register(self, validator: RelationValidator) -> None:
        spec = validator.spec
        if spec.key in self._validators:
            raise ContractError(f"duplicate relation validator: {spec.key}")
        self._validators[spec.key] = validator
        self._specs[spec.key] = spec

    def resolve(self, key: str) -> RelationValidator:
        try:
            validator = self._validators[key]
        except KeyError as exc:
            raise ContractError(f"unknown relation validator: {key}") from exc
        if validator.spec != self._specs[key]:
            raise ContractError(f"relation validator identity changed: {key}")
        return validator

    def resolve_operator(self, operator: OperatorSpec) -> tuple[ResolvedRelation, ...]:
        resolved: list[ResolvedRelation] = []
        seen: set[tuple[str, frozenset[str]]] = set()
        for relation in operator.relations:
            unknown = set(relation.inputs) - set(operator.inputs)
            if unknown:
                raise ContractError(
                    f"{operator.name} relation has unknown inputs: {sorted(unknown)}"
                )
            identity = (relation.validator, frozenset(relation.inputs))
            if identity in seen:
                raise ContractError(f"{operator.name} has duplicate relation: {relation.validator}")
            seen.add(identity)
            spec = self.resolve(relation.validator).spec
            resolved.append(ResolvedRelation(spec.name, spec.version, spec.digest, relation.inputs))
        return tuple(resolved)

    def validate_static(
        self,
        operator: OperatorSpec,
        bindings: Mapping[str, str],
        *,
        require_explicit_joins: bool = False,
    ) -> tuple[ResolvedRelation, ...]:
        unknown = set(bindings) - set(operator.inputs)
        if unknown:
            raise ContractError(f"{operator.name} has unknown input bindings: {sorted(unknown)}")
        resolved = self.resolve_operator(operator)
        # Conservative v1: a join needs a declaration covering its whole bound
        # input set. Pairwise declarations alone cannot prove arbitrary joins.
        if require_explicit_joins and len(bindings) > 1:
            if not any(set(bindings) <= set(relation.inputs) for relation in resolved):
                raise ContractError(f"{operator.name} join requires an explicit relation")
        for relation in resolved:
            context = StaticRelationContext(
                f"{operator.name}@{operator.version}",
                relation.inputs,
                MappingProxyType({name: operator.inputs[name] for name in relation.inputs}),
                MappingProxyType(
                    {name: bindings[name] for name in relation.inputs if name in bindings}
                ),
            )
            self.resolve(relation.validator).validate_static(context)
        return resolved

    def validate_runtime(
        self,
        operator: OperatorSpec,
        values: dict[str, PortValue | list[PortValue]],
        store: LocalArtifactStore,
        *,
        resolved_relations: tuple[ResolvedRelation, ...],
    ) -> None:
        """Validate actual values and pinned validator identities before dispatch.

        This is an integration entry point for A2/A3, not a claim that existing
        workflow camera/depth checks have already migrated to this registry.
        """
        if self.resolve_operator(operator) != resolved_relations:
            raise ContractError(f"{operator.name} relation identity differs from compiled plan")
        validate_operator_inputs(operator, values, store)
        for relation in resolved_relations:
            selected: dict[str, PortValue | tuple[PortValue, ...] | None] = {}
            for name in relation.inputs:
                value = values.get(name)
                selected[name] = tuple(value) if isinstance(value, list) else value
            self.resolve(relation.validator).validate_runtime(
                RuntimeRelationContext(
                    f"{operator.name}@{operator.version}",
                    relation.inputs,
                    MappingProxyType(selected),
                    store,
                )
            )


class IndependentInputsValidator:
    """The Operator explicitly permits unrelated sources for these inputs."""

    spec = RelationValidatorSpec(
        "independent_inputs",
        "1",
        sha256_bytes(Path(__file__).read_bytes()),
    )

    def validate_static(self, context: StaticRelationContext) -> None:
        pass

    def validate_runtime(self, context: RuntimeRelationContext) -> None:
        pass


def default_relation_registry() -> RelationValidatorRegistry:
    registry = RelationValidatorRegistry()
    registry.register(IndependentInputsValidator())
    registry.register(NativeMeshFrameValidator())
    return registry


class NativeMeshFrameValidator:
    spec = RelationValidatorSpec(
        "native_mesh_frame", "1", sha256_bytes(Path(__file__).read_bytes())
    )

    def validate_static(self, context: StaticRelationContext) -> None:
        if set(context.inputs) != {"mesh", "native_frame"}:
            raise ContractError("native mesh relation requires mesh and native_frame")

    def validate_runtime(self, context: RuntimeRelationContext) -> None:
        from .models import ArtifactRef, BackendNativeFrame, StructuredValue
        from .spatial import validate_mesh_native_frame

        mesh = context.values.get("mesh")
        frame = context.values.get("native_frame")
        if not isinstance(mesh, ArtifactRef) or not isinstance(frame, StructuredValue):
            raise ContractError("native mesh relation requires mesh and structured frame")
        validate_mesh_native_frame(
            context.store.get_manifest(mesh.artifact_id).identity.identity_metadata,
            BackendNativeFrame(**frame.value),
        )
