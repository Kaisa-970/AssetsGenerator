"""Durable reverse index of artifact evidence and its node/port owners."""

from __future__ import annotations

from typing import Any

from .dag_models import DagEvidenceConsumer, DagState
from .serialization import to_primitive
from .workbench_persistence import WorkbenchRepository, _references


def index_evidence(state: DagState, repository: WorkbenchRepository) -> None:
    # Keep previously discovered nested links even if their container is now
    # unreadable. Entries are diagnostic links, never ArtifactRef exemptions.
    def add(value: Any, consumer: DagEvidenceConsumer) -> None:
        pending = _references(to_primitive(value))
        seen: set[str] = set()
        while pending:
            ref = pending.pop()
            if ref.artifact_id in seen:
                continue
            seen.add(ref.artifact_id)
            entries = state.evidence_consumers.setdefault(ref.artifact_id, [])
            if consumer not in entries:
                entries.append(consumer)
            try:
                if repository.store.verify_digest(ref):
                    manifest = repository.store.get_manifest(ref.artifact_id)
                    if manifest.identity.identity_metadata.get("media_type") == "application/json":
                        pending.extend(
                            _references(
                                repository.store.read_structured(ref),
                                schema_name=manifest.identity.schema_name,
                            )
                        )
            except (OSError, ValueError, KeyError):
                pass

    for name, value in state.named_actual_inputs.items():
        add(value, DagEvidenceConsumer(None, None, "pipeline_input", name))
    for node in state.node_states.values():
        for attempt in node.attempts:
            for role in ("resolved_inputs", "outputs", "provenance"):
                for port, value in getattr(attempt, role).items():
                    add(value, DagEvidenceConsumer(node.node_id, attempt.attempt, role, port))
            for role in ("request", "decision", "draft", "child_result"):
                add(
                    getattr(attempt, role),
                    DagEvidenceConsumer(
                        node.node_id,
                        attempt.attempt,
                        role,
                        None,
                    ),
                )
    for key, receipt in state.receipts.items():
        add(
            receipt,
            DagEvidenceConsumer(receipt.get("node_id"), receipt.get("attempt"), "receipt", key),
        )


def node_evidence(state: DagState, node_id: str) -> set[str]:
    return {
        identity
        for identity, consumers in state.evidence_consumers.items()
        if any(consumer.node_id == node_id for consumer in consumers)
    }
