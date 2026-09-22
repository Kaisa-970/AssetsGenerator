"""Owned immutable output projections for comparison and downstream selection."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .contracts import ContractError
from .models import ArtifactRef, BuildRun
from .serialization import to_primitive
from .workbench_http import OutputPayload
from .workbench_models import read_build_run

if TYPE_CHECKING:
    from .node_editor_execution import NodeEditorExecution


def snapshot_run(service: NodeEditorExecution, run_id: str, snapshot: dict[str, Any]) -> BuildRun:
    service._owned(run_id)
    if not isinstance(snapshot, dict) or set(snapshot) != {"artifact_id"}:
        raise ContractError("an exact source snapshot is required")
    ref = ArtifactRef(**snapshot)
    store = service.engine.store
    service.engine.repository.verify_reference_closure(ref)
    identity = store.get_manifest(ref.artifact_id).identity
    if (identity.kind, identity.schema_name, identity.schema_version) != (
        "build_run",
        "BuildRun",
        "1.0",
    ):
        raise ContractError("source must be a BuildRun snapshot")
    run = read_build_run(store.read_structured(ref))
    if run.run_id != run_id or run.dag is None:
        raise ContractError("source snapshot does not belong to the selected run")
    return run


def snapshot_reference(
    service: NodeEditorExecution,
    run_id: str,
    node_id: str,
    port: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    run = snapshot_run(service, run_id, snapshot)
    assert run.dag is not None
    state = run.dag.node_states.get(node_id)
    if (
        state is None
        or state.status != "succeeded"
        or not state.attempts
        or state.current().status != "succeeded"
    ):
        raise ContractError("snapshot output requires a successful node")
    reference = service._resolve_output(state, port)
    service.engine.repository.verify_reference_closure(reference)
    identity = service.engine.store.get_manifest(reference.artifact_id).identity
    return {
        "source_run_id": run_id,
        "node_id": node_id,
        "port": port,
        "reference": to_primitive(reference),
        "kind": identity.kind,
        "schema_name": identity.schema_name,
        "schema_version": identity.schema_version,
        "source_snapshot": dict(snapshot),
    }


def snapshot_output(
    service: NodeEditorExecution,
    run_id: str,
    node_id: str,
    port: str,
    snapshot: dict[str, Any],
) -> OutputPayload:
    record = snapshot_reference(service, run_id, node_id, port, snapshot)
    ref = ArtifactRef(**record["reference"])
    if not service._viewable_output(ref):
        raise ContractError("unsupported output artifact kind")
    store = service.engine.store
    media = store.get_manifest(ref.artifact_id).identity.identity_metadata.get(
        "media_type", "application/octet-stream"
    )
    return OutputPayload(store.blob_path(ref).read_bytes(), str(media))
