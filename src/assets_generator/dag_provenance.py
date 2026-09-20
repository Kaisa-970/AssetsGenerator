"""Execution evidence for CPU DAG adapters; no inferred geometry source claims."""

from __future__ import annotations

from typing import Any

from .artifact_store import LocalArtifactStore
from .contracts import ContractError
from .models import ArtifactRef, PortValue, ProvenanceRecord
from .serialization import cache_key, canonical_json_bytes, to_primitive


def expected_node_provenance_records(
    store: LocalArtifactStore,
    *,
    run_id: str,
    node_id: str,
    attempt: int,
    operator: str,
    adapter_identity: dict[str, Any],
    parameters: dict[str, Any],
    inputs: dict[str, PortValue | list[PortValue]],
    outputs: dict[str, PortValue | list[PortValue]],
    decision_ref: ArtifactRef | None = None,
    execution_evidence: ArtifactRef | None = None,
) -> dict[str, list[ProvenanceRecord]]:
    """Give each output occurrence its own identity, even for equal ArtifactRefs.

    Scalar StructuredValues remain in recorded input evidence. Their independent
    persistence is governed by the Operator port's persist contract, not this helper.
    """
    parts = operator.split("@")
    if len(parts) != 2 or not all(parts) or type(attempt) is not int or attempt < 1:
        raise ContractError("DAG provenance requires versioned operator and positive attempt")
    if not run_id or not node_id:
        raise ContractError("DAG provenance requires run and node instance identity")
    parents: list[ArtifactRef] = []
    for value in inputs.values():
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, ArtifactRef) and item not in parents:
                parents.append(item)
    if decision_ref is not None and decision_ref not in parents:
        parents.append(decision_ref)
    if execution_evidence is not None:
        if not store.verify_digest(execution_evidence):
            raise ContractError("DAG execution evidence is missing or corrupt")
        if execution_evidence not in parents:
            parents.append(execution_evidence)
    records: dict[str, list[ProvenanceRecord]] = {}
    for port, value in outputs.items():
        records[port] = []
        values = value if isinstance(value, list) else [value]
        for index, item in enumerate(values):
            if not isinstance(item, ArtifactRef):
                continue
            if not store.verify_digest(item):
                raise ContractError("DAG provenance output is missing or corrupt")
            output_id = cache_key(
                {
                    "schema": "dag-execution-output@1",
                    "run_id": run_id,
                    "node_id": node_id,
                    "attempt": attempt,
                    "port": port,
                    "element": index if isinstance(value, list) else None,
                }
            )
            record = ProvenanceRecord(
                provenance_id=cache_key({"provenance_of": output_id}),
                output_id=output_id,
                output_artifact_id=item.artifact_id,
                derived_from_artifact_ids=[ref.artifact_id for ref in parents],
                operator=parts[0],
                operator_version=parts[1],
                backend="core_adapter",
                backend_version=str(
                    adapter_identity.get("spec", adapter_identity).get("version", "unverified")
                ),
                model_digest=None,
                container_digest=None,
                parameters={
                    "node_parameters": to_primitive(parameters),
                    "adapter_identity": to_primitive(adapter_identity),
                    "resolved_inputs": to_primitive(inputs),
                },
                seed=None,
                run_id=run_id,
                node_id=node_id,
                attempt=attempt,
                source="derived",
            )
            records[port].append(record)
    return records


def persist_node_provenance(
    store: LocalArtifactStore,
    *,
    run_id: str,
    node_id: str,
    attempt: int,
    operator: str,
    adapter_identity: dict[str, Any],
    parameters: dict[str, Any],
    inputs: dict[str, PortValue | list[PortValue]],
    outputs: dict[str, PortValue | list[PortValue]],
    decision_ref: ArtifactRef | None = None,
    execution_evidence: ArtifactRef | None = None,
) -> dict[str, list[ArtifactRef]]:
    records = expected_node_provenance_records(
        store,
        run_id=run_id,
        node_id=node_id,
        attempt=attempt,
        operator=operator,
        adapter_identity=adapter_identity,
        parameters=parameters,
        inputs=inputs,
        outputs=outputs,
        decision_ref=decision_ref,
        execution_evidence=execution_evidence,
    )
    return {
        port: [
            store.persist_bytes(
                canonical_json_bytes(record),
                kind="provenance_record",
                schema_name="ProvenanceRecord",
                schema_version="1.0",
                identity_metadata={"media_type": "application/json"},
            )
            for record in values
        ]
        for port, values in records.items()
    }
