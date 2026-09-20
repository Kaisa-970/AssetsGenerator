"""Remote dispatch keeps uncertain jobs on their original durable attempt."""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING

from .compiled_plan import CompiledNode, digest, thaw
from .contracts import ContractError
from .dag_adapters import BoundDagPlan, NodeExecutionContext
from .dag_provenance import persist_node_provenance
from .dag_remote_adapter import RemoteNodeAdapter
from .dag_remote_submission import DagRemoteSubmission
from .models import ArtifactRef, BuildRun
from .remote_binding import RemoteAttemptBinding
from .remote_http import RemoteJobClient, RemoteTransportUnknown
from .remote_protocol import RemoteIdentity, RemoteJob, RemoteOutput, RemoteRequest
from .runtime import utc_now
from .serialization import canonical_json_bytes, to_primitive
from .workbench_persistence import _references

if TYPE_CHECKING:
    from .dag_engine import DagEngine


def _bridge(engine: DagEngine, binding: RemoteAttemptBinding) -> DagRemoteSubmission:
    return DagRemoteSubmission(engine.repository, RemoteJobClient(binding.endpoint))


def validate_remote_success(engine: DagEngine, run: BuildRun, node: CompiledNode) -> None:
    assert run.dag is not None
    binding = run.dag.node_states[node.node_id].current().remote_binding
    if binding is None or _bridge(engine, binding).pinned_result(binding).state != "succeeded":
        raise ContractError("remote success requires pinned successful job")


def validate_remote_retry(engine: DagEngine, run: BuildRun, node_id: str) -> None:
    assert run.dag is not None
    state = run.dag.node_states[node_id]
    if not state.attempts or state.current().remote_binding is None:
        return
    attempt = state.current()
    binding = attempt.remote_binding
    assert binding is not None
    job = _bridge(engine, binding).pinned_result(binding)
    if job.state != "failed" and attempt.status != "succeeded":
        raise ContractError("remote attempt is active, unknown or awaiting result import")


def execute_remote(
    engine: DagEngine, run: BuildRun, plan: BoundDagPlan, node: CompiledNode, *, recover: bool
) -> None:
    assert run.dag is not None
    state = run.dag.node_states[node.node_id]
    attempt = state.current()
    bound = plan.bindings[node.node_id]
    try:
        inputs = engine._inputs(run, node)
        engine._validate_inputs(node, inputs)
        if (
            canonical_json_bytes(inputs) != canonical_json_bytes(attempt.resolved_inputs)
            or attempt.input_digest != engine._input_digest(plan, node, inputs)
            or attempt.binding_digest != digest(bound.to_dict())
            or attempt.operator != node.operator
            or attempt.adapter != bound.adapter
            or attempt.parameters_digest != digest(bound.parameters)
        ):
            raise ContractError("remote attempt differs from fixed plan/inputs")
        adapter = engine.registry.resolve(bound)
        if not isinstance(adapter, RemoteNodeAdapter):
            raise ContractError("remote adapter required")
        context = NodeExecutionContext(
            run.run_id,
            node.node_id,
            copy.deepcopy(inputs),
            bound.parameters,
            engine.store,
            attempt_id=f"{run.run_id}/{node.node_id}/{attempt.attempt}",
            input_digest=attempt.input_digest,
        )
        client = RemoteJobClient(str(bound.parameters["remote_endpoint"]))
        bridge = DagRemoteSubmission(engine.repository, client)
        uploads = adapter.input_blobs(context)
        available = {ref.artifact_id for ref in _references(to_primitive(inputs))}
        descriptors = {}
        total_input = 0
        for name, ref in uploads.items():
            if not isinstance(name, str) or not name or not isinstance(ref, ArtifactRef):
                raise ContractError("remote upload requires named ArtifactRefs")
            if ref.artifact_id not in available:
                raise ContractError("remote upload must belong to resolved inputs")
            engine.repository.verify_reference_closure(ref)
            manifest = engine.store.get_manifest(ref.artifact_id)
            total_input += engine.store.blob_path(ref).stat().st_size
            if total_input > 128 * 1024 * 1024:
                raise ContractError("remote inputs exceed aggregate byte limit")
            descriptors[name] = {
                "artifact_id": ref.artifact_id,
                "identity": to_primitive(manifest.identity),
            }
        payload = adapter.prepare_payload(context)
        if "input_blobs" in payload:
            raise ContractError("input_blobs is reserved for validated upload descriptors")
        payload["input_blobs"] = descriptors
        payload.update(input_digest=attempt.input_digest, binding_digest=attempt.binding_digest)
        request = RemoteRequest.create(
            RemoteIdentity(
                str(bound.parameters["service_id"]), str(bound.parameters["backend_digest"])
            ),
            RemoteAttemptBinding.key_for(run.run_id, node.node_id, attempt.attempt),
            payload,
        )
        binding = RemoteAttemptBinding.create(
            run_id=run.run_id,
            node_id=node.node_id,
            attempt=attempt.attempt,
            input_digest=attempt.input_digest,
            binding_digest=attempt.binding_digest,
            endpoint=client.endpoint,
            request=request,
        )
        if attempt.remote_binding is None:
            if recover:
                raise ContractError("remote preparation interrupted; explicit resolution required")
            bridge.prepare(binding, expected_revision=run.dag.revision)
            refreshed = engine.repository.load(run.run_id)
            assert refreshed.dag is not None
            run.dag = refreshed.dag
            state = run.dag.node_states[node.node_id]
            attempt = state.current()
        elif attempt.remote_binding != binding:
            raise ContractError("remote request changed since preparation")
        job: RemoteJob | None
        if attempt.remote_result is not None:
            job = bridge.pinned_result(binding)
        else:
            if not recover:
                for ref in uploads.values():
                    manifest = engine.store.get_manifest(ref.artifact_id)
                    client.upload_blob(
                        request.identity,
                        engine.store.blob_path(ref).read_bytes(),
                        manifest.identity.blob_digest,
                    )
            job = bridge.recover(binding) if recover else bridge.submit(binding)
            if job is None or job.state in {"queued", "running"}:
                attempt.status = state.status = "running"
                state.recovery_blocked_reason = None
                attempt.error_code = (
                    "remote_transport_unknown" if job is None else f"remote_{job.state}"
                )
                attempt.error_detail = "remote job not found" if job is None else None
                engine._aggregate(run, plan)
                engine._save(run)
                return
            bridge.pin_result(binding, expected_revision=run.dag.revision)
            refreshed = engine.repository.load(run.run_id)
            assert refreshed.dag is not None
            run.dag = refreshed.dag
            state = run.dag.node_states[node.node_id]
            attempt = state.current()
        state.recovery_blocked_reason = None
        if job.state == "failed":
            error = json.loads(job.error_json or b"{}")
            attempt.status = state.status = "failed"
            attempt.error_code, attempt.error_detail = error["code"], error["detail"]
            attempt.finished_at = utc_now()
        else:
            raw = json.loads(job.result_json or b"{}")
            if set(raw) != {"outputs"} or not isinstance(raw["outputs"], list):
                raise ContractError("remote result requires output descriptors")
            blobs = {}
            total = 0
            for item in raw["outputs"]:
                output = RemoteOutput.from_job(job, item["output_id"])
                total += output.byte_length
                if total > 128 * 1024 * 1024:
                    raise ContractError("remote result exceeds aggregate byte limit")
                blobs[output.output_id] = bridge.download(binding, output.output_id)
            result = adapter.import_result(context, job, blobs)
            if result.wait_request is not None:
                raise ContractError("remote import cannot create human wait")
            outputs = copy.deepcopy(dict(result.outputs))
            engine._validate_outputs(node, outputs)
            attempt.provenance = persist_node_provenance(
                engine.store,
                run_id=run.run_id,
                node_id=node.node_id,
                attempt=attempt.attempt,
                operator=node.operator,
                adapter_identity=bound.to_dict(),
                parameters=thaw(bound.parameters),
                inputs=attempt.resolved_inputs,
                outputs=outputs,
                execution_evidence=attempt.remote_result,
            )
            attempt.outputs = outputs
            attempt.status = state.status = "succeeded"
            attempt.error_code = attempt.error_detail = None
            attempt.finished_at = utc_now()
    except RemoteTransportUnknown as error:
        attempt.status = state.status = "running"
        state.recovery_blocked_reason = None
        attempt.error_code = "remote_transport_unknown"
        attempt.error_detail = str(error)
    except Exception as error:
        engine._block(run, state, "remote_recovery_invalid", error)
    engine._aggregate(run, plan)
    engine._save(run)
