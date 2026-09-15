from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .artifact_store import LocalArtifactStore
from .models import ARTIFACT_KINDS, STRUCTURED_KINDS, PortValue, StructuredValue

Cardinality = Literal["one", "zero_or_one", "many"]
Carrier = Literal["artifact_ref", "structured"]


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
    values = [] if value is None else value if isinstance(value, list) else [value]
    if spec.cardinality == "one" and len(values) != 1:
        raise ContractError(f"{operator}.{port_name} requires exactly one value")
    if spec.cardinality == "zero_or_one" and len(values) > 1:
        raise ContractError(f"{operator}.{port_name} accepts at most one value")
    for item in values:
        descriptor = describe_value(item, store)
        prefix = f"{operator}.{port_name}"
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
