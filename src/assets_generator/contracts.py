from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .artifact_store import LocalArtifactStore
from .models import ARTIFACT_KINDS, STRUCTURED_KINDS, ArtifactRef, PortValue, StructuredValue

Cardinality = Literal["one", "zero_or_one", "one_or_more", "zero_or_more", "many"]
Carrier = Literal["artifact_ref", "structured"]

_CARDINALITIES = frozenset({"one", "zero_or_one", "one_or_more", "zero_or_more", "many"})
_CARRIERS = frozenset({"artifact_ref", "structured"})


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class PortSpec:
    kinds: tuple[str, ...]
    cardinality: Cardinality = "one"
    carriers: tuple[Carrier, ...] = ("artifact_ref", "structured")
    schema_name: str | None = None
    schema_version: str | None = None
    requires_frame: bool = False
    requires_unit: bool = False
    persist: bool = False

    def __post_init__(self) -> None:
        if self.cardinality not in _CARDINALITIES:
            raise ContractError(f"unknown port cardinality: {self.cardinality}")
        unknown_carriers = set(self.carriers) - _CARRIERS
        if unknown_carriers:
            raise ContractError(f"unknown port carriers: {sorted(unknown_carriers)}")
        if not self.carriers:
            raise ContractError("port requires at least one carrier")
        known = ARTIFACT_KINDS | STRUCTURED_KINDS
        unknown = set(self.kinds) - known
        if unknown:
            raise ContractError(f"unknown port kinds: {sorted(unknown)}")


@dataclass(frozen=True)
class OperatorSpec:
    name: str
    version: str
    inputs: dict[str, PortSpec]
    outputs: dict[str, PortSpec]


@dataclass(frozen=True)
class ValueDescriptor:
    carrier: Carrier
    kind: str
    schema_name: str
    schema_version: str
    identity_metadata: dict[str, object] = field(default_factory=dict)


def describe_value(value: PortValue, store: LocalArtifactStore) -> ValueDescriptor:
    if isinstance(value, StructuredValue):
        return ValueDescriptor("structured", value.kind, value.schema_name, value.schema_version)
    if not isinstance(value, ArtifactRef):
        raise ContractError(f"port value has unsupported carrier type: {type(value).__name__}")
    if not store.verify_digest(value):
        raise ContractError(f"artifact has invalid digest: {value.artifact_id}")
    manifest = store.get_manifest(value.artifact_id)
    identity = manifest.identity
    return ValueDescriptor(
        "artifact_ref",
        identity.kind,
        identity.schema_name,
        identity.schema_version,
        identity.identity_metadata,
    )


def validate_port_value(
    *,
    operator: str,
    port_name: str,
    spec: PortSpec,
    value: PortValue | list[PortValue] | None,
    store: LocalArtifactStore,
) -> None:
    prefix = f"{operator}.{port_name}"
    plural = spec.cardinality in {"one_or_more", "zero_or_more", "many"}
    if plural:
        if not isinstance(value, list):
            raise ContractError(f"{prefix} requires a list value")
        else:
            values = value
        if spec.cardinality == "one_or_more" and not values:
            raise ContractError(f"{prefix} requires at least one value")
    else:
        if isinstance(value, list):
            raise ContractError(f"{prefix} rejects a list value")
        if spec.cardinality == "one" and value is None:
            raise ContractError(f"{prefix} requires exactly one value")
        values = [] if value is None else [value]
    for item in values:
        descriptor = describe_value(item, store)
        if descriptor.carrier not in spec.carriers:
            raise ContractError(f"{prefix} rejects carrier {descriptor.carrier}")
        if descriptor.kind not in spec.kinds:
            raise ContractError(f"{prefix} rejects kind {descriptor.kind}")
        if spec.schema_name is not None and descriptor.schema_name != spec.schema_name:
            raise ContractError(f"{prefix} rejects schema {descriptor.schema_name}")
        if spec.schema_version is not None and descriptor.schema_version != spec.schema_version:
            raise ContractError(f"{prefix} rejects schema version {descriptor.schema_version}")
        if spec.requires_frame and not descriptor.identity_metadata.get("frame_id"):
            raise ContractError(f"{prefix} requires frame_id")
        if spec.requires_unit and not descriptor.identity_metadata.get("unit"):
            raise ContractError(f"{prefix} requires unit")


def cardinality_compatible(source: Cardinality, target: Cardinality, *, optional: bool) -> bool:
    source_plural = source in {"one_or_more", "zero_or_more", "many"}
    target_plural = target in {"one_or_more", "zero_or_more", "many"}
    if source_plural != target_plural:
        return False
    ranges = {
        "one": (1, 1),
        "zero_or_one": (0, 1),
        "one_or_more": (1, None),
        "zero_or_more": (0, None),
        "many": (0, None),
    }
    source_min, source_max = ranges[source]
    target_min, target_max = ranges[target]
    if optional:
        source_min = 0
    if source_min < target_min:
        return False
    if target_max is not None and (source_max is None or source_max > target_max):
        return False
    return True


def validate_operator_inputs(
    spec: OperatorSpec,
    inputs: dict[str, PortValue | list[PortValue]],
    store: LocalArtifactStore,
) -> None:
    unknown = set(inputs) - set(spec.inputs)
    if unknown:
        raise ContractError(f"{spec.name} received unknown ports: {sorted(unknown)}")
    for port_name, port_spec in spec.inputs.items():
        validate_port_value(
            operator=spec.name,
            port_name=port_name,
            spec=port_spec,
            value=inputs.get(port_name),
            store=store,
        )


def effective_output_spec(spec: PortSpec) -> PortSpec:
    if not spec.persist:
        return spec
    return PortSpec(
        spec.kinds,
        spec.cardinality,
        ("artifact_ref",),
        spec.schema_name,
        spec.schema_version,
        spec.requires_frame,
        spec.requires_unit,
        True,
    )


def validate_operator_outputs(
    spec: OperatorSpec,
    outputs: dict[str, PortValue | list[PortValue]],
    store: LocalArtifactStore,
) -> None:
    unknown = set(outputs) - set(spec.outputs)
    if unknown:
        raise ContractError(f"{spec.name} produced unknown ports: {sorted(unknown)}")
    for port_name, port_spec in spec.outputs.items():
        validate_port_value(
            operator=spec.name,
            port_name=port_name,
            spec=effective_output_spec(port_spec),
            value=outputs.get(port_name),
            store=store,
        )
