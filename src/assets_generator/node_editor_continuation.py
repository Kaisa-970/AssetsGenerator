"""Read-only continuation drafts from pinned, verified segmentation evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .compiled_plan import thaw
from .contracts import ContractError
from .models import ArtifactRef
from .serialization import to_primitive
from .workbench_models import read_build_run

if TYPE_CHECKING:
    from .node_editor_execution import NodeEditorExecution


def continue_extraction(
    service: NodeEditorExecution, run_id: str, node_id: str, snapshot_ref: dict[str, Any]
) -> dict[str, Any]:
    """Return a new draft only when segmentation and all ancestors are reusable."""
    service._owned(run_id)
    result: dict[str, Any] = {
        "eligible": False,
        "reason": None,
        "pipeline": None,
        "input_refs": None,
        "reuse_source": snapshot_ref,
        "preflight": None,
        "source_run_id": run_id,
        "source_node_id": node_id,
    }
    try:
        if not isinstance(snapshot_ref, dict) or set(snapshot_ref) != {"artifact_id"}:
            raise ContractError("an exact source snapshot is required")
        snapshot = ArtifactRef(**snapshot_ref)
        store = service.engine.store
        if not store.verify_digest(snapshot):
            raise ContractError("source snapshot is missing or corrupt")
        identity = store.get_manifest(snapshot.artifact_id).identity
        if (identity.kind, identity.schema_name, identity.schema_version) != (
            "build_run",
            "BuildRun",
            "1.0",
        ):
            raise ContractError("source must be a BuildRun snapshot")
        run = read_build_run(store.read_structured(snapshot))
        if run.run_id != run_id or run.dag is None:
            raise ContractError("source snapshot does not belong to the selected run")
        plan = service.engine._plan(run)
        nodes = {node.node_id: node for node in plan.static_plan.nodes}
        if node_id not in nodes or nodes[node_id].operator != "text_segmentation@1":
            raise ContractError("continue extraction requires a text_segmentation node")
        state = run.dag.node_states[node_id]
        if state.status != "succeeded" or not state.attempts:
            raise ContractError("segmentation must have completed successfully")
        original_image = state.current().resolved_inputs.get("image")
        if not isinstance(original_image, ArtifactRef):
            raise ContractError("segmentation must bind its exact original image")
        # Validate the recorded image against the selected snapshot's graph inputs,
        # not the latest mutable run index or a guessed pipeline input name.
        if service.engine._inputs(run, nodes[node_id]).get("image") != original_image:
            raise ContractError("segmentation image differs from the snapshot binding")
        keep: set[str] = set()

        def visit(key: str) -> None:
            if key in keep:
                return
            keep.add(key)
            for parent in plan.static_plan.dependencies[key]:
                visit(parent)

        visit(node_id)
        if any(plan.bindings[key].spec["execution_kind"] == "human" for key in keep):
            raise ContractError(
                "human decision inheritance is not supported; confirm a new decision explicitly"
            )
        declared_inputs: set[str] = set()
        draft_nodes: dict[str, Any] = {}
        for node in plan.static_plan.nodes:
            if node.node_id not in keep:
                continue
            binding = plan.bindings[node.node_id]
            draft_nodes[node.node_id] = {
                "operator": node.operator,
                "adapter": binding.adapter,
                "inputs": {key: item.reference for key, item in node.inputs.items()},
                "parameters": thaw(binding.parameters),
                **({"backend": binding.backend} if binding.backend is not None else {}),
            }
            declared_inputs.update(
                item.port for item in node.inputs.values() if item.source == "pipeline_input"
            )
        extract = "extract_foreground"
        suffix = 2
        while extract in nodes:
            extract = f"extract_foreground_{suffix}"
            suffix += 1
        draft_nodes[extract] = {
            "operator": "apply_binary_mask@1",
            "adapter": "apply_binary_mask@1",
            "inputs": {
                "image": nodes[node_id].inputs["image"].reference,
                "mask": f"{node_id}.outputs.mask",
            },
            "parameters": {},
        }
        pipeline = {
            "pipeline": plan.static_plan.pipeline_name,
            "version": plan.static_plan.pipeline_version,
            "inputs": {
                name: thaw(plan.static_plan.inputs[name].contract)
                for name in sorted(declared_inputs)
            },
            "nodes": draft_nodes,
        }
        inputs = {
            name: to_primitive(run.dag.named_actual_inputs[name])
            for name in sorted(declared_inputs)
        }
        report = service.preflight(pipeline, input_refs=inputs, reuse_source=snapshot_ref)
        result.update(
            pipeline=pipeline,
            input_refs=inputs,
            preflight=report,
            extract_node_id=extract,
            original_image=to_primitive(original_image),
        )
        if not report["execution_ready"] or any(
            report["nodes"].get(key, {}).get("status") != "reuse" for key in keep
        ):
            raise ContractError(
                "source segmentation or required ancestors cannot be reused; inspect preflight"
            )
        if report["nodes"].get(extract, {}).get("status") != "execute":
            raise ContractError("extraction must be the only new execution")
        result["eligible"] = True
    except (ValueError, OSError, KeyError, TypeError, StopIteration) as error:
        result["reason"] = str(error)
    return result
