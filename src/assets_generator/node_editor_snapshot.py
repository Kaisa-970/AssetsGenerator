"""Owned immutable output projections for comparison and downstream selection."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .compiled_plan import digest
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


def persisted_plan(service: NodeEditorExecution, run: BuildRun) -> dict[str, Any]:
    """Read immutable historical bindings without resolving today's implementations."""
    if run.dag is None:
        raise ContractError("historical plan requires a DAG run")
    store = service.engine.store
    service.engine.repository.verify_reference_closure(run.dag.plan)
    identity = store.get_manifest(run.dag.plan.artifact_id).identity
    if (identity.kind, identity.schema_name, identity.schema_version) != (
        "dag_plan",
        "BoundDagPlan",
        "1.0",
    ):
        raise ContractError("invalid plan artifact contract")
    raw = store.read_structured(run.dag.plan)
    try:
        body = {key: value for key, value in raw.items() if key != "plan_id"}
        static = raw["static_plan"]
        if (
            raw["schema_version"] != "bound_dag_plan@1"
            or digest(body) != raw["plan_id"]
            or raw["plan_id"] != run.dag.plan_id
            or set(raw["bindings"]) != set(run.dag.node_states)
            or static["pipeline_name"] != run.pipeline_name
            or static["pipeline_version"] != run.pipeline_version
        ):
            raise ContractError("historical run plan mismatch")
    except (KeyError, TypeError, AttributeError) as error:
        raise ContractError("invalid historical plan") from error
    return raw


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
    binding = persisted_plan(service, run)["bindings"][node_id]
    return {
        "source_run_id": run_id,
        "node_id": node_id,
        "port": port,
        "reference": to_primitive(reference),
        "kind": identity.kind,
        "schema_name": identity.schema_name,
        "schema_version": identity.schema_version,
        "frame_id": identity.identity_metadata.get("frame_id"),
        "unit": identity.identity_metadata.get("unit"),
        "source_snapshot": dict(snapshot),
        "execution": {
            "adapter": binding["adapter"],
            "backend": binding.get("backend"),
            "parameters": binding["parameters"],
            "implementation_digest": binding["implementation_digest"],
        },
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


def snapshot_input_reference(
    service: NodeEditorExecution, run_id: str, name: str, snapshot: dict[str, Any]
) -> dict[str, Any]:
    """Project a named original input from its verified immutable run, without rebinding."""
    run = snapshot_run(service, run_id, snapshot)
    assert run.dag is not None
    ref = run.dag.named_actual_inputs.get(name)
    if not isinstance(ref, ArtifactRef):
        raise ContractError("snapshot input requires a named scalar Artifact reference")
    service.engine.repository.verify_reference_closure(ref)
    identity = service.engine.store.get_manifest(ref.artifact_id).identity
    return {
        "source_run_id": run_id,
        "input_name": name,
        "reference": to_primitive(ref),
        "kind": identity.kind,
        "source_snapshot": dict(snapshot),
    }


def snapshot_input_image(
    service: NodeEditorExecution, run_id: str, name: str, snapshot: dict[str, Any]
) -> OutputPayload:
    record = snapshot_input_reference(service, run_id, name, snapshot)
    if record["kind"] not in {"rgb_image", "rgba_image", "binary_mask"}:
        raise ContractError("snapshot input is not a previewable image")
    ref = ArtifactRef(**record["reference"])
    store = service.engine.store
    media = store.get_manifest(ref.artifact_id).identity.identity_metadata.get("media_type")
    if media not in {"image/png", "image/jpeg", "image/webp"}:
        raise ContractError("snapshot input has unsupported image media type")
    return OutputPayload(store.blob_path(ref).read_bytes(), media)
