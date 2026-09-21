"""Shared legacy workflow output identities and durable execution evidence.

Identity formulas and attempt=1 semantics are preserved. Generic DAG attempts
have their own identities; this module does not replace their state machine.
"""

from __future__ import annotations

from typing import Any

from .artifact_store import LocalArtifactStore
from .models import SCHEMA_VERSION, ArtifactRef, BuildRun, ProvenanceRecord, StructuredValue
from .serialization import canonical_json_bytes, sha256_bytes, to_primitive


def output_id(run_id: str, node_id: str, port_name: str, element_id: str | None = None) -> str:
    if element_id is not None:
        if not element_id:
            raise ValueError("output element_id must be non-empty")
        return sha256_bytes(
            canonical_json_bytes(["output-element-v1", run_id, node_id, 1, port_name, element_id])
        )
    return sha256_bytes(f"{run_id}:{node_id}:1:{port_name}".encode())


def persist_provenance(
    store: LocalArtifactStore,
    *,
    run_id: str,
    node_id: str,
    port_name: str,
    artifact: ArtifactRef,
    derived_from: list[ArtifactRef],
    operator: str,
    backend: str,
    backend_version: str,
    parameters: dict[str, Any],
    seed: int | None,
    source: str,
    model_digest: str | None = None,
    container_digest: str | None = None,
    element_id: str | None = None,
) -> ArtifactRef:
    identity = output_id(run_id, node_id, port_name, element_id)
    record = ProvenanceRecord(
        provenance_id=sha256_bytes(f"provenance:{identity}".encode()),
        output_id=identity,
        output_artifact_id=artifact.artifact_id,
        derived_from_artifact_ids=[item.artifact_id for item in derived_from],
        operator=operator,
        operator_version="1",
        backend=backend,
        backend_version=backend_version,
        model_digest=model_digest,
        container_digest=container_digest,
        parameters=parameters,
        seed=seed,
        run_id=run_id,
        node_id=node_id,
        attempt=1,
        source=source,
    )
    return store.persist_bytes(
        canonical_json_bytes(record),
        kind="provenance_record",
        schema_name="ProvenanceRecord",
        schema_version=SCHEMA_VERSION,
        identity_metadata={"media_type": "application/json"},
    )


def persist_build_run(store: LocalArtifactStore, run: BuildRun) -> ArtifactRef:
    return store.record_build_run(
        run.run_id,
        StructuredValue("build_run", "BuildRun", SCHEMA_VERSION, to_primitive(run)),
    )
