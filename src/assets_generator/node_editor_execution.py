"""Single-writer asynchronous execution for the local DAG canvas.

The caller owns the repository context; close this service before releasing it.
GET projections never run recovery or wait for the engine's command lock.
"""

from __future__ import annotations

import io
import re
import threading
import uuid
import zipfile
from collections.abc import Callable
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from PIL import Image

from .compiled_plan import CompiledPlan, digest, thaw
from .contracts import ContractError, OperatorSpec, validate_port_value
from .dag_adapters import BoundDagPlan
from .dag_engine import DagEngine
from .dag_models import PortMap
from .dag_preflight import analyze_execution
from .dag_review import DagMaskReviewService, create_dag_review_server
from .models import ArtifactRef, PortValue, StructuredValue
from .node_editor_actions import project_run_actions
from .pipeline import (
    _operator_specs_from_raw,
    _pipeline_from_raw,
    _port_spec,
    compile_pipeline,
    load_default_operator_specs,
)
from .relations import RelationValidatorRegistry
from .serialization import cache_key, canonical_json_bytes, read_json, sha256_bytes, to_primitive
from .workbench_http import OutputPayload
from .workbench_persistence import CreationReceipt


def validate_editor_inputs(plan: CompiledPlan) -> str:
    """Return the supported input name after validating the entry contract."""
    if set(plan.inputs) == {"image"}:
        name = "image"
        kinds = tuple(plan.inputs[name].contract["kinds"])
        if kinds not in {("rgb_image",), ("rgba_image",)}:
            raise ContractError("canvas image input must have rgb_image or rgba_image kind")
        kind = kinds[0]
    elif set(plan.inputs) == {"observations"}:
        name, kind = "observations", "observation_bundle"
    else:
        raise ContractError(
            "canvas execution requires exactly one input named image or observations"
        )
    contract = plan.inputs[name].contract
    if tuple(contract["kinds"]) != (kind,):
        raise ContractError(f"canvas {name} input must have {kind} kind")
    if "artifact_ref" not in contract["carriers"] or contract["cardinality"] not in {
        "one",
        "zero_or_one",
    }:
        raise ContractError(f"canvas {name} input must accept a scalar ArtifactRef")
    fixed_schema = {
        "rgba_image": ("png", "1.0"),
        "observation_bundle": ("ObservationBundle", "1.0"),
    }.get(kind)
    if fixed_schema is not None:
        for field, expected in zip(("schema_name", "schema_version"), fixed_schema, strict=True):
            declared = contract.get(field)
            if declared is not None and declared != expected:
                raise ContractError(
                    f"canvas {name} input requires {field}={expected}; got {declared}"
                )
    return name


def validate_editor_execution(plan: CompiledPlan) -> str:
    """Shared creation gate: supported input contract and executable graph."""
    name = validate_editor_inputs(plan)
    if not plan.nodes:
        raise ContractError("canvas execution requires at least one processing node")
    return name


def validate_editor_reference_inputs(plan: CompiledPlan) -> None:
    """The named-reference endpoint accepts complete scalar ArtifactRef mappings."""
    if not plan.nodes:
        raise ContractError("canvas execution requires at least one processing node")
    for name, port in plan.inputs.items():
        contract = port.contract
        if "artifact_ref" not in contract["carriers"] or contract["cardinality"] not in {
            "one",
            "zero_or_one",
        }:
            raise ContractError(f"canvas {name} input must accept a scalar ArtifactRef")


class PreflightChanged(ContractError):
    def __init__(self, report: dict[str, Any]):
        super().__init__("execution preflight changed; inspect and confirm the new explanation")
        self.report = report


class NodeEditorExecution:
    def __init__(
        self,
        engine: DagEngine,
        *,
        specs: dict[str, OperatorSpec] | None = None,
        relations: RelationValidatorRegistry | None = None,
    ) -> None:
        engine.repository._ready()
        self.engine = engine
        self.specs = specs if specs is not None else load_default_operator_specs()
        if relations is not None and relations is not engine.relations:
            raise ContractError("editor execution must share the engine relation registry")
        self.relations = engine.relations
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._active_run: str | None = None
        self._errors: dict[str, str] = {}
        self._closed = False
        self._stop_execution = threading.Event()
        self._review: tuple[DagMaskReviewService, ThreadingHTTPServer, threading.Thread] | None = (
            None
        )

    def install_model_registry(self, update: Callable[[Any], None]) -> None:
        """Serialize explicit service installation with execution commands."""
        with self._lock:
            self._idle()
            registry = self.engine.registry.copy()
            update(registry)
            self.engine.registry = registry

    def _owned(self, run_id: str) -> None:
        if not re.fullmatch(r"dag_[A-Za-z0-9_-]{1,120}", run_id):
            raise ContractError("invalid DAG run ID")
        owner = read_json(self.engine.store.root / "parent_run_owners" / f"{run_id}.json")
        if owner.get("workbench_directory") != str(self.engine.repository.directory.resolve()):
            raise ContractError("run belongs to another editor directory")

    def input_references(self, run_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Return verified named pipeline input references from one immutable run snapshot."""
        self._owned(run_id)
        expected = read_json(self.engine.store.root / "runs" / f"{run_id}.json")
        if not isinstance(snapshot, dict) or snapshot.get("artifact_id") != expected.get(
            "artifact_id"
        ):
            raise ContractError("input source snapshot does not match the persisted run")
        run = self.engine.repository.load(run_id)
        if run.dag is None or not isinstance(run.dag.named_actual_inputs, dict):
            raise ContractError("run has no named actual inputs")
        values: dict[str, Any] = {}
        for name, ref in run.dag.named_actual_inputs.items():
            if not isinstance(ref, ArtifactRef):
                raise ContractError(f"input {name} is not an Artifact reference")
            manifest = self.engine.store.get_manifest(ref.artifact_id)
            if not self.engine.store.verify_digest(ref):
                raise ContractError(f"input {name} failed Artifact digest verification")
            values[name] = {
                "artifact_id": ref.artifact_id,
                "identity": to_primitive(manifest.identity),
            }
        return {"run_id": run_id, "snapshot_ref": snapshot, "inputs": values}

    def plan(self, run_id: str) -> dict[str, Any]:
        """Read the verified persisted plan; never recover or dispatch a run."""
        self._owned(run_id)
        run = self.engine.repository.load(run_id)
        from .node_editor_snapshot import persisted_plan

        return persisted_plan(self, run)

    def draft_from_run(self, run_id: str) -> dict[str, Any]:
        """Project verified execution configuration into a new, unexecuted draft."""
        plan = self.plan(run_id)
        static = plan["static_plan"]
        nodes = {}
        for node in static["nodes"]:
            bound = plan["bindings"][node["node_id"]]
            inputs = {}
            for name, binding in node["inputs"].items():
                prefix = (
                    "pipeline.inputs"
                    if binding["source"] == "pipeline_input"
                    else f"{binding['node_id']}.outputs"
                )
                inputs[name] = f"{prefix}.{binding['port']}" + ("?" if binding["optional"] else "")
            nodes[node["node_id"]] = {
                "operator": node["operator"],
                "adapter": bound["adapter"],
                "inputs": inputs,
                "parameters": bound["parameters"],
                **({"backend": bound["backend"]} if bound.get("backend") else {}),
            }
        pipeline_inputs = {}
        for name, contract in static["inputs"].items():
            port = dict(contract)
            # The canvas uses kind for its supported single-kind pipeline inputs.
            if len(port["kinds"]) == 1:
                port["kind"] = port.pop("kinds")[0]
            pipeline_inputs[name] = port
        return {
            "source_run_id": run_id,
            "source_plan_id": plan["plan_id"],
            "pipeline": {
                "pipeline": static["pipeline_name"],
                "version": static["pipeline_version"],
                "inputs": pipeline_inputs,
                "nodes": nodes,
            },
        }

    def _viewable_output(self, ref: object) -> bool:
        if not isinstance(ref, ArtifactRef):
            return False
        return self.engine.store.get_manifest(ref.artifact_id).identity.kind in {
            "gltf_asset",
            "triangle_mesh",
            "asset_release",
            "asset_definition",
            "quality_report",
            "rgb_image",
            "rgba_image",
            "binary_mask",
        }

    def _run_busy(self, run_id: str) -> bool:
        review = self._review
        return bool(
            (self._active_run == run_id and self._worker and self._worker.is_alive())
            or (
                review
                and review[0].run_id == run_id
                and review[0]._thread
                and review[0]._thread.is_alive()
            )
        )

    def snapshot(self, run_id: str) -> dict[str, Any]:
        self._owned(run_id)
        # A worker can publish its final snapshot and exit while this read is in
        # flight. Never label the earlier snapshot idle; let the next poll reload.
        busy_before = self._run_busy(run_id)
        for _ in range(3):
            snapshot_ref = read_json(self.engine.store.root / "runs" / f"{run_id}.json")
            run = self.engine.repository.load(run_id)
            if snapshot_ref == read_json(self.engine.store.root / "runs" / f"{run_id}.json"):
                break
        else:
            raise ContractError("run changed during snapshot read; refresh")
        if run.dag is None:
            raise ContractError("not a DAG run")
        review = self._review
        return {
            "run": to_primitive(run),
            "snapshot_ref": snapshot_ref,
            "actions": project_run_actions(
                run,
                command_busy=bool(
                    self._closed
                    or (self._worker and self._worker.is_alive())
                    or (review and review[0]._thread and review[0]._thread.is_alive())
                ),
            ),
            "outputs": self._output_entries(run),
            "busy": busy_before or self._run_busy(run_id),
            "error": self._errors.get(run_id)
            or (review[0]._error if review and review[0].run_id == run_id else None),
        }

    def _output_entries(self, run: Any) -> list[dict[str, str]]:
        entries = []
        for state in run.dag.node_states.values():
            if state.status != "succeeded":
                continue
            for port, ref in state.current().outputs.items():
                if not isinstance(ref, ArtifactRef):
                    continue
                identity = self.engine.store.get_manifest(ref.artifact_id).identity
                if self._viewable_output(ref):
                    entries.append(
                        {
                            "node_id": state.node_id,
                            "port": port,
                            "kind": identity.kind,
                            "url": f"/api/runs/{run.run_id}/outputs/{state.node_id}/{port}",
                        }
                    )
                elif (identity.kind, identity.schema_name, identity.schema_version) == (
                    "remote_job_result",
                    "TextMaskCandidates",
                    "1.0",
                ):
                    # Resolve only references owned by this node's candidate bundle.
                    self.engine.repository.verify_reference_closure(ref)
                    items = self.engine.store.read_structured(ref)["candidates"]
                    for index in range(len(items)):
                        child_port = f"{port}~{index}"
                        entries.append(
                            {
                                "node_id": state.node_id,
                                "port": child_port,
                                "kind": "binary_mask",
                                "url": (
                                    f"/api/runs/{run.run_id}/outputs/{state.node_id}/{child_port}"
                                ),
                            }
                        )
        return entries

    def _resolve_output(self, state: Any, port: str) -> ArtifactRef:
        if port in state.current().outputs:
            ref = state.current().outputs[port]
        elif "~" in port:
            parent, raw_index = port.rsplit("~", 1)
            if not raw_index.isdecimal() or str(int(raw_index)) != raw_index:
                raise ContractError("invalid candidate index")
            bundle = state.current().outputs.get(parent)
            if not isinstance(bundle, ArtifactRef):
                raise ContractError("candidate bundle missing")
            self.engine.repository.verify_reference_closure(bundle)
            identity = self.engine.store.get_manifest(bundle.artifact_id).identity
            if (identity.kind, identity.schema_name, identity.schema_version) != (
                "remote_job_result",
                "TextMaskCandidates",
                "1.0",
            ):
                raise ContractError("unsupported candidate bundle")
            items = self.engine.store.read_structured(bundle)["candidates"]
            if int(raw_index) >= len(items):
                raise ContractError("candidate index out of range")
            ref = ArtifactRef(**items[int(raw_index)]["mask"])
            if self.engine.store.get_manifest(ref.artifact_id).identity.kind != "binary_mask":
                raise ContractError("candidate is not a mask")
        else:
            raise ContractError("output port missing")
        if not isinstance(ref, ArtifactRef):
            raise ContractError("output requires an ArtifactRef")
        return ref

    def output_reference(self, run_id: str, node_id: str, port: str) -> dict[str, Any]:
        """Verify an owned output before offering its exact identity for a new run."""
        self._owned(run_id)
        run = self.engine.repository.load(run_id)
        if run.dag is None or run.dag.node_states[node_id].status != "succeeded":
            raise ContractError("output reference requires a successful node")
        reference = self._resolve_output(run.dag.node_states[node_id], port)
        if not isinstance(reference, ArtifactRef):
            raise ContractError("output reference requires a scalar ArtifactRef")
        self.engine.repository.verify_reference_closure(reference)
        identity = self.engine.store.get_manifest(reference.artifact_id).identity
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
        }

    def snapshot_reference(
        self, run_id: str, node_id: str, port: str, snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        from .node_editor_snapshot import snapshot_reference

        return snapshot_reference(self, run_id, node_id, port, snapshot)

    def snapshot_output(
        self, run_id: str, node_id: str, port: str, snapshot: dict[str, Any]
    ) -> OutputPayload:
        from .node_editor_snapshot import snapshot_output

        return snapshot_output(self, run_id, node_id, port, snapshot)

    def _bind_candidate_input(self, value: dict[str, Any], inputs: dict[str, Any]) -> ArtifactRef:
        source = value["source"]
        if not isinstance(source, dict) or set(source) not in (
            {"run_id", "node_id", "port"},
            {"run_id", "node_id", "port", "snapshot"},
        ):
            raise ContractError("invalid candidate source")
        if "snapshot" in source:
            resolved = self.snapshot_reference(
                source["run_id"], source["node_id"], source["port"], source["snapshot"]
            )
            from .node_editor_snapshot import snapshot_run

            run = snapshot_run(self, source["run_id"], source["snapshot"])
        else:
            resolved = self.output_reference(source["run_id"], source["node_id"], source["port"])
            run = self.engine.repository.load(source["run_id"])
        if (
            resolved["reference"] != {"artifact_id": value["artifact_id"]}
            or "~" not in source["port"]
        ):
            raise ContractError("candidate source does not match mask")
        if run.dag is None:
            raise ContractError("candidate run missing DAG")
        parent = source["port"].rsplit("~", 1)[0]
        bundle = run.dag.node_states[source["node_id"]].current().outputs[parent]
        if not isinstance(bundle, ArtifactRef):
            raise ContractError("candidate bundle missing")
        raw = self.engine.store.read_structured(bundle)
        image = ArtifactRef(**raw["image"])
        if inputs.get("image") != image:
            raise ContractError("candidate mask must be used with its original image")
        mask = ArtifactRef(value["artifact_id"])
        binding = self.engine.store.persist_structured(
            StructuredValue(
                "quality_evidence",
                "TextMaskSelection",
                "1.0",
                {
                    "source": source,
                    "bundle": to_primitive(bundle),
                    "mask": to_primitive(mask),
                    "image": to_primitive(image),
                },
            )
        )
        identity = self.engine.store.get_manifest(mask.artifact_id).identity
        return self.engine.store.persist_bytes(
            self.engine.store.blob_path(mask).read_bytes(),
            kind=identity.kind,
            schema_name=identity.schema_name,
            schema_version=identity.schema_version,
            identity_metadata={
                **identity.identity_metadata,
                "selection_binding": to_primitive(binding),
            },
        )

    def continue_extraction(
        self, run_id: str, node_id: str, snapshot_ref: dict[str, Any]
    ) -> dict[str, Any]:
        from .node_editor_continuation import continue_extraction

        return continue_extraction(self, run_id, node_id, snapshot_ref)

    def decision_summary(self, run_id: str, node_id: str) -> dict[str, Any]:
        """Read a verified human decision without claiming authentication or QA approval."""
        self._owned(run_id)
        run = self.engine.repository.load(run_id)
        if run.dag is None or node_id not in run.dag.node_states:
            raise ContractError("human node missing")
        state = run.dag.node_states[node_id]
        if not state.attempts or state.current().decision is None:
            return {"recorded": False, "run_id": run_id, "node_id": node_id}
        attempt = state.current()
        plan = self.engine._plan(run)
        node = next(item for item in plan.static_plan.nodes if item.node_id == node_id)
        self.engine._human_evidence(run, plan, node, attempt)
        assert attempt.decision is not None
        self.engine.repository.verify_reference_closure(attempt.decision)
        decision = self.engine.store.read_structured(attempt.decision)
        return {
            "recorded": True,
            "run_id": run_id,
            "node_id": node_id,
            "decision_ref": to_primitive(attempt.decision),
            "reviewer": decision["reviewer"],
            "reuse": decision.get("reuse"),
            "confirmed_at": decision.get("confirmed_at"),
            "payload": decision["payload"],
            "node_finished_at": attempt.finished_at,
            "decision_time": None,
            "identity_verified": False,
        }

    def output(self, run_id: str, node_id: str, port: str) -> OutputPayload:
        self._owned(run_id)
        run = self.engine.repository.load(run_id)
        if run.dag is None:
            raise ContractError("unsupported output")
        state = run.dag.node_states[node_id]
        if state.status != "succeeded":
            raise ContractError("output requires a successful node")
        ref = self._resolve_output(state, port)
        if not isinstance(ref, ArtifactRef) or not self._viewable_output(ref):
            raise ContractError("unsupported output artifact kind")
        self.engine.repository.verify_reference_closure(ref)
        media = self.engine.store.get_manifest(ref.artifact_id).identity.identity_metadata.get(
            "media_type", "application/octet-stream"
        )
        return OutputPayload(self.engine.store.blob_path(ref).read_bytes(), str(media))

    def appearance(self, run_id: str, node_id: str, port: str) -> dict[str, Any]:
        from .mesh_appearance import describe_mesh_appearance

        self._owned(run_id)
        run = self.engine.repository.load(run_id)
        assert run.dag is not None
        state = run.dag.node_states[node_id]
        if state.status != "succeeded":
            raise ContractError("appearance requires successful node")
        ref = self._resolve_output(state, port)
        if not isinstance(ref, ArtifactRef):
            raise ContractError("appearance requires mesh artifact")
        identity = self.engine.store.get_manifest(ref.artifact_id).identity
        if identity.kind not in {"triangle_mesh", "gltf_asset"}:
            raise ContractError("appearance requires GLB mesh")
        self.engine.repository.verify_reference_closure(ref)
        return describe_mesh_appearance(
            self.engine.store.blob_path(ref).read_bytes(), identity.identity_metadata
        )

    def release_archive(self, run_id: str, node_id: str, port: str) -> OutputPayload:
        """Download declared release files; never re-export or regenerate evidence."""
        from .release_io import release_files as _release_files

        self._owned(run_id)
        run = self.engine.repository.load(run_id)
        if run.dag is None or run.dag.node_states[node_id].status != "succeeded":
            raise ContractError("archive requires a successful release node")
        ref = run.dag.node_states[node_id].current().outputs[port]
        store = self.engine.store
        if (
            not isinstance(ref, ArtifactRef)
            or store.get_manifest(ref.artifact_id).identity.kind != "asset_release"
        ):
            raise ContractError("archive requires an AssetRelease")
        self.engine.repository.verify_reference_closure(ref)
        release = store.read_structured(ref)
        files = _release_files(store, ref)
        files["asset.json"] = ArtifactRef(**release["asset_definition"])
        files["release.json"] = ref
        # Bound the in-memory download independently of compression ratio.
        limit = 512 * 1024 * 1024
        if sum(store.blob_path(item).stat().st_size for item in files.values()) > limit:
            raise ContractError("release archive exceeds 512 MiB download limit")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, item in sorted(files.items()):
                archive.writestr(zipfile.ZipInfo(name), store.blob_path(item).read_bytes())
        return OutputPayload(buffer.getvalue(), "application/zip")

    def list_runs(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in sorted((self.engine.store.root / "parent_run_owners").glob("dag_*.json")):
            try:
                self._owned(path.stem)
            except (OSError, ValueError, KeyError):
                continue
            try:
                run = self.engine.repository.load(path.stem)
                if run.dag is not None:
                    rows.append({"run_id": run.run_id, "status": run.status})
            except (OSError, ValueError, KeyError) as error:
                rows.append(
                    {"run_id": path.stem, "status": "recovery_blocked", "error": str(error)}
                )
        return rows

    def _stop_review(
        self, *, wait: bool = False, validate: Callable[[], Any] | None = None
    ) -> None:
        if self._review is None:
            if validate is not None:
                validate()
            return
        service, server, thread = self._review
        # Coordinate with decision() itself: no decision can pass its admission
        # check after closing, even if a request raced with a canvas command.
        with service._lock:
            if not wait and service._thread and service._thread.is_alive():
                raise ContractError("a human decision is still executing")
            if validate is not None:
                validate()
            service._closed = True
        server.shutdown()
        server.server_close()
        thread.join()
        service.close()
        self._review = None

    def _idle(self) -> None:
        if self._closed:
            raise RuntimeError("editor execution service is closing")
        if self._worker and self._worker.is_alive():
            raise ContractError("another editor command is still executing")
        review = self._review
        if review is not None:
            with review[0]._lock:
                if review[0]._thread and review[0]._thread.is_alive():
                    raise ContractError("a human decision is still executing")

    def _dispatch(self, run_id: str, command: Callable[[], Any]) -> None:
        # All routes (including resume/retry after a pre-marker crash) must
        # establish creation evidence before any execution can occur.
        repository = self.engine.repository
        marker = repository.directory / "created_runs" / f"{run_id}.json"
        if not marker.exists():
            repository._mutate(
                lambda: repository.io.write(
                    marker,
                    canonical_json_bytes({"run_id": run_id}),
                    exclusive=True,
                )
            )
        self._errors.pop(run_id, None)
        self._active_run = run_id

        def execute() -> None:
            try:
                command()
                # Only an explicitly dispatched command owns this continuation.
                # Read-only snapshots and server startup never create this loop.
                while not self._stop_execution.is_set():
                    run = repository.load(run_id)
                    if run.dag is None or run.status in {"succeeded", "failed"}:
                        break
                    states = list(run.dag.node_states.values())
                    if any(
                        state.status
                        in {"waiting_for_input", "recovery_blocked", "interrupted", "failed"}
                        for state in states
                    ):
                        break
                    remote_pending = any(
                        state.status == "running"
                        and state.attempts
                        and state.current().remote_binding is not None
                        for state in states
                    )
                    if not remote_pending or self._stop_execution.wait(2):
                        break
                    self.engine.drain(run_id)
            except Exception as error:
                self._errors[run_id] = f"{type(error).__name__}: {error}"

        self._worker = threading.Thread(target=execute, daemon=True)
        self._worker.start()

    def upload_image(self, data: bytes, *, rgba: bool = False) -> dict[str, Any]:
        """Import a decoded image without creating a run or dispatching inference."""
        if not 0 < len(data) <= 20 * 1024 * 1024:
            raise ContractError("image upload must be at most 20 MiB")
        try:
            decoded = Image.open(io.BytesIO(data))
        except Image.DecompressionBombError as error:
            raise ContractError("upload must be at most 25 megapixels") from error
        with decoded as image:
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                raise ContractError("upload must be PNG, JPEG or WebP")
            if image.width * image.height > 25_000_000 or getattr(image, "n_frames", 1) != 1:
                raise ContractError("upload must be a single image of at most 25 megapixels")
            image.load()
            if rgba and (image.format != "PNG" or image.mode != "RGBA"):
                raise ContractError("prepared RGBA input must be an RGBA PNG")
            if rgba and image.getchannel("A").getextrema()[1] == 0:
                raise ContractError("prepared RGBA input contains no foreground")
            metadata = {
                "media_type": Image.MIME[image.format],
                "width": image.width,
                "height": image.height,
                "channel_layout": image.mode,
            }
        with self._lock:
            if self._closed:
                raise ContractError("editor execution service is closing")
            ref = self.engine.store.persist_bytes(
                data,
                kind="rgba_image" if rgba else "rgb_image",
                schema_name="png" if rgba else "raster_image",
                schema_version="1.0",
                identity_metadata={"media_type": "image/png", "channel_layout": "RGBA"}
                if rgba
                else metadata,
            )
        return {"image_ref": to_primitive(ref)}

    def read_text_input(self, artifact_id: str) -> dict[str, str]:
        """Read only verified, bounded text; never accept a filesystem path."""
        ref = ArtifactRef(artifact_id)
        store = self.engine.store
        identity = store.get_manifest(ref.artifact_id).identity
        if (identity.kind, identity.schema_name, identity.schema_version) != (
            "text",
            "plain_text",
            "1.0",
        ):
            raise ContractError("input is not a plain text Artifact")
        path = store.blob_path(ref)
        if not 0 < path.stat().st_size <= 65536:
            raise ContractError("text input must be at most 64 KiB")
        data = path.read_bytes()
        if sha256_bytes(data) != identity.blob_digest:
            raise ContractError("text input digest mismatch")
        text = data.decode("utf-8")
        if not text.strip():
            raise ContractError("text input must be nonempty")
        return {"artifact_id": artifact_id, "text": text}

    def upload_text(self, data: bytes) -> dict[str, Any]:
        if not 0 < len(data) <= 65536:
            raise ContractError("text input must be at most 64 KiB")
        if not data.decode("utf-8").strip():
            raise ContractError("text input must be nonempty UTF-8")
        with self._lock:
            if self._closed:
                raise ContractError("editor execution service is closing")
            ref = self.engine.store.persist_bytes(
                data,
                kind="text",
                schema_name="plain_text",
                schema_version="1.0",
                identity_metadata={"media_type": "text/plain; charset=utf-8"},
            )
        return {"text_ref": to_primitive(ref)}

    def upload_mask(self, data: bytes) -> dict[str, Any]:
        """Import a strict binary PNG mask without creating a run."""
        if not 0 < len(data) <= 20 * 1024 * 1024:
            raise ContractError("mask upload must be at most 20 MiB")
        try:
            decoded = Image.open(io.BytesIO(data))
        except Image.DecompressionBombError as error:
            raise ContractError("mask upload must be at most 25 megapixels") from error
        with decoded as mask:
            if mask.format != "PNG" or mask.width * mask.height > 25_000_000:
                raise ContractError("mask upload must be a PNG of at most 25 megapixels")
            if getattr(mask, "n_frames", 1) != 1:
                raise ContractError("mask upload must be a single frame")
            mask.load()
            if mask.mode not in {"1", "L"} or "transparency" in mask.info:
                raise ContractError("mask upload must be a grayscale PNG without transparency")
            values = set(mask.convert("L").tobytes())
            if not values <= {0, 255} or 255 not in values:
                raise ContractError("mask upload must contain only 0/255 values and foreground")
            width, height = mask.size
            channel_layout = mask.mode
        with self._lock:
            if self._closed:
                raise ContractError("editor execution service is closing")
            ref = self.engine.store.persist_bytes(
                data,
                kind="binary_mask",
                schema_name="png",
                schema_version="1.0",
                identity_metadata={
                    "media_type": "image/png",
                    "channel_layout": channel_layout,
                    "width": width,
                    "height": height,
                },
            )
        return {"mask_ref": to_primitive(ref)}

    def import_observations(self, images: list[dict[str, Any]]) -> dict[str, Any]:
        """Assemble explicit ordered RGB references, without estimating cameras."""
        from .models import ObservationView
        from .observations import make_observation_bundle, observation_bundle_value

        if not isinstance(images, list) or not 2 <= len(images) <= 32:
            raise ContractError("select between 2 and 32 RGB images")
        refs = []
        for item in images:
            if (
                not isinstance(item, dict)
                or set(item) != {"artifact_id"}
                or not isinstance(item["artifact_id"], str)
            ):
                raise ContractError("images must contain ArtifactRef objects")
            refs.append(ArtifactRef(**item))
        if len({ref.artifact_id for ref in refs}) != len(refs):
            raise ContractError("observation images must be distinct")
        with self._lock:
            if self._closed:
                raise ContractError("editor execution service is closing")
            bundle = make_observation_bundle(
                [ObservationView(f"view_{index:03d}", ref) for index, ref in enumerate(refs)],
                self.engine.store,
            )
            reference = self.engine.store.persist_structured(observation_bundle_value(bundle))
            return {
                "observations_ref": to_primitive(reference),
                "views": to_primitive(bundle.views),
            }

    def _compile_execution(self, raw: dict[str, Any]) -> BoundDagPlan:
        if not isinstance(raw, dict) or set(raw) - {"pipeline", "version", "inputs", "nodes"}:
            raise ContractError("invalid pipeline object")
        plan = self.engine.registry.bind_plan(
            compile_pipeline(
                _pipeline_from_raw(raw),
                self.specs,
                relation_registry=self.relations,
                require_explicit_joins=True,
            ),
            relation_registry=self.relations,
        )
        return plan

    def _resolve_inputs(
        self,
        plan: BoundDagPlan,
        image_path: str | None = None,
        *,
        image_ref: dict[str, Any] | None = None,
        observations_ref: dict[str, Any] | None = None,
        input_refs: dict[str, dict[str, Any]] | None = None,
    ) -> PortMap:
        actual_inputs: dict[str, PortValue | list[PortValue]]
        if input_refs is not None:
            if any(value is not None for value in (image_path, image_ref, observations_ref)):
                raise ContractError("multi-input refs cannot be combined with image sources")
            validate_editor_reference_inputs(plan.static_plan)
            name = None
            if not isinstance(input_refs, dict) or set(input_refs) != set(plan.static_plan.inputs):
                raise ContractError(
                    "multi-input run requires one ArtifactRef for every pipeline input"
                )
            if any(
                not isinstance(value, dict)
                or set(value) not in ({"artifact_id"}, {"artifact_id", "source"})
                for value in input_refs.values()
            ):
                raise ContractError("multi-input inputs must be ArtifactRef objects")
            actual_inputs = {
                key: ArtifactRef(value["artifact_id"]) for key, value in input_refs.items()
            }
            for key, supplied in input_refs.items():
                if "source" in supplied:
                    actual_inputs[key] = self._bind_candidate_input(supplied, actual_inputs)
        else:
            name = validate_editor_execution(plan.static_plan)
            if sum(v is not None for v in (image_path, image_ref, observations_ref)) != 1:
                raise ContractError("provide exactly one input source")
            if (name == "observations") != (observations_ref is not None):
                raise ContractError("input source does not match pipeline input")
            reference = observations_ref if name == "observations" else image_ref
            if reference is not None:
                if not isinstance(reference, dict) or set(reference) != {"artifact_id"}:
                    raise ContractError("input reference must be an ArtifactRef")
                image = ArtifactRef(**reference)
            else:
                if not isinstance(image_path, str) or not image_path.strip():
                    raise ContractError("image path is required")
                source_path = Path(image_path).expanduser()
                if source_path.stat().st_size > 20 * 1024 * 1024:
                    raise ContractError("image upload must be at most 20 MiB")
                contract = plan.static_plan.inputs[name].contract
                image = ArtifactRef(
                    **self.upload_image(
                        source_path.read_bytes(),
                        rgba=tuple(contract["kinds"]) == ("rgba_image",),
                    )["image_ref"]
                )
            actual_inputs = {name: image}
        return actual_inputs

    def _validate_actual_inputs(self, plan: BoundDagPlan, actual_inputs: PortMap) -> None:
        for input_name, value in actual_inputs.items():
            assert isinstance(value, ArtifactRef)
            contract = plan.static_plan.inputs[input_name].contract
            validate_port_value(
                operator=plan.static_plan.pipeline_name,
                port_name=input_name,
                spec=_port_spec(thaw(contract)),
                value=value,
                store=self.engine.store,
            )
            identity = self.engine.store.get_manifest(value.artifact_id).identity
            self.engine.repository.verify_reference_closure(value)
            if identity.kind == "observation_bundle":
                from .observations import observation_bundle_from_artifact

                observation_bundle_from_artifact(value, self.engine.store)
            if identity.kind == "rgba_image":
                if (
                    identity.schema_name,
                    identity.schema_version,
                    identity.identity_metadata,
                ) != ("png", "1.0", {"media_type": "image/png", "channel_layout": "RGBA"}):
                    raise ContractError("prepared RGBA Artifact has an invalid identity")
                blob = self.engine.store.blob_path(value)
                if blob.stat().st_size > 20 * 1024 * 1024:
                    raise ContractError("image upload must be at most 20 MiB")
                with Image.open(blob) as prepared:
                    if (
                        prepared.format != "PNG"
                        or prepared.mode != "RGBA"
                        or prepared.width * prepared.height > 25_000_000
                        or getattr(prepared, "n_frames", 1) != 1
                    ):
                        raise ContractError("prepared RGBA input must be a bounded RGBA PNG")
                    prepared.load()
                    if prepared.getchannel("A").getextrema()[1] == 0:
                        raise ContractError("prepared RGBA input contains no foreground")

    def prepare_inputs(
        self,
        raw: dict[str, Any],
        image_path: str | None = None,
        *,
        image_ref: dict[str, Any] | None = None,
        observations_ref: dict[str, Any] | None = None,
        input_refs: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Explicit input import/selection binding; never create or dispatch a run."""
        with self._lock:
            if self._closed:
                raise ContractError("editor execution service is closing")
            plan = self._compile_execution(raw)
            actual = self._resolve_inputs(
                plan,
                image_path,
                image_ref=image_ref,
                observations_ref=observations_ref,
                input_refs=input_refs,
            )
            self._validate_actual_inputs(plan, actual)
            return {"input_refs": to_primitive(actual)}

    def _exact_refs(self, input_refs: dict[str, dict[str, Any]]) -> PortMap:
        if not isinstance(input_refs, dict) or any(
            not isinstance(value, dict) or set(value) != {"artifact_id"}
            for value in input_refs.values()
        ):
            raise ContractError("preflight requires prepared exact input_refs")
        return {key: ArtifactRef(**value) for key, value in input_refs.items()}

    def _preflight_report(
        self,
        plan: BoundDagPlan,
        actual: PortMap,
        reuse_ref: ArtifactRef | None,
    ) -> dict[str, Any]:
        report = analyze_execution(self.engine, plan, actual, reuse_ref)
        try:
            validate_editor_reference_inputs(plan.static_plan)
            if set(actual) != set(plan.static_plan.inputs):
                raise ContractError("preflight requires every named input")
            self._validate_actual_inputs(plan, actual)
            if reuse_ref is not None:
                source = self.engine.store.read_structured(reuse_ref)
                self._owned(source["run_id"])
        except (ValueError, OSError, KeyError, TypeError) as error:
            report["execution_ready"] = False
            report["entry_error"] = str(error)
            for row in report["nodes"].values():
                row.update(status="blocked", reason="entry_input_invalid", detail=str(error))
        report.pop("digest", None)
        report["digest"] = digest(report)
        return report

    def preflight(
        self,
        raw: dict[str, Any],
        *,
        input_refs: dict[str, dict[str, Any]],
        reuse_source: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Read-only explanation over already prepared immutable inputs."""
        actual = self._exact_refs(input_refs)
        if reuse_source is not None and (
            not isinstance(reuse_source, dict) or set(reuse_source) != {"artifact_id"}
        ):
            raise ContractError("reuse source must be an immutable snapshot reference")
        try:
            plan = self._compile_execution(raw)
        except (ValueError, OSError, KeyError, TypeError, AttributeError) as error:
            report: dict[str, Any] = {
                "schema_version": "execution_preflight@1",
                "plan_id": None,
                "pipeline": raw,
                "inputs": to_primitive(actual),
                "reuse_source": reuse_source,
                "nodes": {},
                "execution_ready": False,
                "error": {"reason": "plan_invalid", "detail": str(error)},
            }
            report["digest"] = digest(report)
            return report
        return self._preflight_report(
            plan,
            actual,
            ArtifactRef(**reuse_source) if reuse_source is not None else None,
        )

    def _replay_prepared_creation(
        self,
        raw: dict[str, Any],
        input_refs: dict[str, dict[str, Any]],
        reuse_source: dict[str, Any] | None,
        key: str,
    ) -> dict[str, Any] | None:
        """Validate replay against persisted configuration, independent of live adapters.

        Any ambiguity raises a normal error, preserving the caller's unresolved
        creation request. It must never become a preflight-changed rejection.
        """
        repository = self.engine.repository
        receipt = repository.creation_receipt("node-editor:" + key)
        if receipt is None:
            return None
        index = self.engine.store.root / "runs" / f"{receipt.run_id}.json"
        marker = repository.directory / "created_runs" / f"{receipt.run_id}.json"
        if not index.exists():
            if marker.exists():
                raise ContractError("created run index is missing; refusing to recreate it")
            return None
        self._owned(receipt.run_id)
        existing = repository.load(receipt.run_id)
        if existing.dag is None:
            raise ContractError("creation receipt references a non-DAG run")
        reference = existing.dag.plan
        if not self.engine.store.verify_digest(reference):
            raise ContractError("created run plan evidence is missing or corrupt")
        stored = self.engine.store.read_structured(reference)
        identity = self.engine.store.get_manifest(reference.artifact_id).identity
        if (identity.kind, identity.schema_name, identity.schema_version) != (
            "dag_plan",
            "BoundDagPlan",
            "1.0",
        ):
            raise ContractError("invalid created run plan contract")
        static = stored["static_plan"]
        specs = _operator_specs_from_raw(
            {
                "operators": list(
                    {
                        node["operator"]: node["operator_contract"] for node in static["nodes"]
                    }.values()
                )
            }
        )
        if not isinstance(raw, dict) or set(raw) - {"pipeline", "version", "inputs", "nodes"}:
            raise ContractError("invalid pipeline object")
        requested = compile_pipeline(
            _pipeline_from_raw(raw),
            specs,
            relation_registry=self.relations,
            require_explicit_joins=True,
        )
        actual = self._exact_refs(input_refs)
        if reuse_source is not None and (
            not isinstance(reuse_source, dict) or set(reuse_source) != {"artifact_id"}
        ):
            raise ContractError("reuse source must be an immutable snapshot reference")
        request_digest = cache_key(
            {
                "plan": stored,
                "inputs": to_primitive(actual),
                **({"reuse_source": reuse_source} if reuse_source is not None else {}),
            }
        )
        if (
            canonical_json_bytes(requested.to_dict()) != canonical_json_bytes(static)
            or existing.dag.named_actual_inputs != actual
            or existing.dag.plan_id != stored["plan_id"]
            or receipt.request_digest != request_digest
        ):
            raise ContractError("creation idempotency key conflict")
        if not marker.exists():
            repository._mutate(
                lambda: repository.io.write(
                    marker, canonical_json_bytes({"run_id": receipt.run_id}), exclusive=True
                )
            )
        return self.snapshot(receipt.run_id)

    def start(
        self,
        raw: dict[str, Any],
        image_path: str | None = None,
        *,
        image_ref: dict[str, Any] | None = None,
        observations_ref: dict[str, Any] | None = None,
        input_refs: dict[str, dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
        reuse_source: dict[str, Any] | None = None,
        preflight_digest: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if idempotency_key is not None and (
                not isinstance(idempotency_key, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", idempotency_key)
            ):
                raise ContractError("invalid creation idempotency key")
            if self._closed:
                raise ContractError("editor execution service is closing")
            if idempotency_key is not None and input_refs is not None:
                if any(value is not None for value in (image_path, image_ref, observations_ref)):
                    raise ContractError("prepared refs cannot be combined with image sources")
                # Candidate source preparation is a legacy start feature, not an
                # exact-reference replay. Checked requests never carry it.
                if preflight_digest is not None or all(
                    isinstance(value, dict) and set(value) == {"artifact_id"}
                    for value in input_refs.values()
                ):
                    replay = self._replay_prepared_creation(
                        raw, input_refs, reuse_source, idempotency_key
                    )
                    if replay is not None:
                        return replay
            try:
                plan = self._compile_execution(raw)
            except (ValueError, OSError, KeyError, TypeError, AttributeError):
                if preflight_digest is not None and input_refs is not None:
                    raise PreflightChanged(
                        self.preflight(raw, input_refs=input_refs, reuse_source=reuse_source)
                    ) from None
                raise
            if preflight_digest is not None:
                if not isinstance(preflight_digest, str) or not preflight_digest:
                    raise ContractError("preflight_digest must be nonempty text")
                if input_refs is None or any(
                    value is not None for value in (image_path, image_ref, observations_ref)
                ):
                    raise ContractError("checked creation requires prepared input_refs")
                actual_inputs = self._exact_refs(input_refs)
            else:
                actual_inputs = self._resolve_inputs(
                    plan,
                    image_path,
                    image_ref=image_ref,
                    observations_ref=observations_ref,
                    input_refs=input_refs,
                )
                self._validate_actual_inputs(plan, actual_inputs)
            reuse_ref = None
            if reuse_source is not None:
                if not isinstance(reuse_source, dict) or set(reuse_source) != {"artifact_id"}:
                    raise ContractError("reuse source must be an immutable snapshot reference")
                reuse_ref = ArtifactRef(**reuse_source)
            run_id = None
            marker = None
            repository = self.engine.repository
            if idempotency_key is not None:
                if repository.creation_receipt("node-editor:" + idempotency_key) is None:
                    self._idle()
                requested_receipt = CreationReceipt(
                    "node-editor:" + idempotency_key,
                    cache_key(
                        {
                            "plan": plan.to_dict(),
                            "inputs": to_primitive(actual_inputs),
                            **({"reuse_source": to_primitive(reuse_ref)} if reuse_ref else {}),
                        }
                    ),
                    f"dag_{uuid.uuid4().hex}",
                )
                receipt = repository.creation_receipt("node-editor:" + idempotency_key)
                if (
                    receipt is not None
                    and receipt.request_digest != requested_receipt.request_digest
                ):
                    raise ContractError("creation idempotency key conflict")
                receipt = receipt or requested_receipt
                run_id = receipt.run_id
                marker = repository.directory / "created_runs" / f"{run_id}.json"
                index = self.engine.store.root / "runs" / f"{run_id}.json"
                if index.exists():
                    existing = repository.load(run_id)
                    if (
                        existing.dag is None
                        or existing.dag.plan_id != plan.plan_id
                        or existing.dag.named_actual_inputs != actual_inputs
                    ):
                        raise ContractError("creation receipt does not match persisted run")
                    if not marker.exists():
                        repository._mutate(
                            lambda: repository.io.write(
                                marker,
                                canonical_json_bytes({"run_id": run_id}),
                                exclusive=True,
                            )
                        )
                    # No resume/dispatch on repeated POST, even after restart.
                    return self.snapshot(run_id)
                if marker.exists():
                    raise ContractError("created run index is missing; refusing to recreate it")
            if preflight_digest is not None:
                report = self._preflight_report(plan, actual_inputs, reuse_ref)
                if report["digest"] != preflight_digest or not report["execution_ready"]:
                    raise PreflightChanged(report)
            elif reuse_ref is not None:
                self.engine.repository.verify_reference_closure(reuse_ref)
                source = self.engine.store.read_structured(reuse_ref)
                self._owned(source["run_id"])
                if source.get("dag") is None:
                    raise ContractError("reuse source must contain DAG state")
            self._idle()
            if idempotency_key is not None:
                repository.reserve_creation(requested_receipt)
            self._stop_review()
            run = self.engine.create(
                plan,
                actual_inputs,
                run_id=run_id,
                **({"reuse_source": reuse_ref} if reuse_ref else {}),
            )
            if marker is not None:
                repository._mutate(
                    lambda: repository.io.write(
                        marker,
                        canonical_json_bytes({"run_id": run.run_id}),
                        exclusive=True,
                    )
                )
            self._dispatch(run.run_id, lambda: self.engine.drain(run.run_id))
            return self.snapshot(run.run_id)

    def _revision(self, run_id: str, expected_revision: int) -> None:
        self._owned(run_id)
        run = self.engine.repository.load(run_id)
        if (
            run.dag is None
            or type(expected_revision) is not int
            or run.dag.revision != expected_revision
        ):
            raise ContractError("DAG revision conflict")

    def resume(self, run_id: str, expected_revision: int) -> dict[str, Any]:
        with self._lock:
            self._idle()
            self._revision(run_id, expected_revision)
            self._stop_review(validate=lambda: self._revision(run_id, expected_revision))
            self._dispatch(run_id, lambda: self.engine.drain(run_id))
            return self.snapshot(run_id)

    def retry(self, run_id: str, node_id: str, expected_revision: int) -> dict[str, Any]:
        with self._lock:
            self._idle()
            self._revision(run_id, expected_revision)
            run = self.engine.repository.load(run_id)
            assert run.dag is not None
            if node_id not in run.dag.node_states or run.dag.node_states[node_id].status not in {
                "failed",
                "interrupted",
                "recovery_blocked",
            }:
                raise ContractError("node is not retryable")
            self._stop_review(validate=lambda: self._revision(run_id, expected_revision))
            self._dispatch(run_id, lambda: self.engine.retry(run_id, node_id, expected_revision))
            return self.snapshot(run_id)

    def reuse_decision(self, run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        from .dag_decision_reuse import decision_proposal

        with self._lock:
            self._idle()
            if "confirm" in body and body["confirm"] is not True:
                raise ContractError("confirmation must be explicit true")
            self._owned(run_id)
            target = self.engine.repository.load(run_id)
            assert target.dag is not None
            if body.get("confirm") is True:
                if not isinstance(body.get("reviewer"), str) or not body["reviewer"].strip():
                    raise ContractError("confirmation requires reviewer")
                if not isinstance(body.get("idempotency_key"), str) or not body["idempotency_key"]:
                    raise ContractError("confirmation requires idempotency key")
                receipt = target.dag.receipts.get(body["idempotency_key"])
                if receipt is not None:
                    reuse = receipt.get("reuse", {})
                    if (
                        receipt["node_id"] != body["node_id"]
                        or receipt["reviewer"] != body["reviewer"]
                        or reuse.get("source_snapshot") != body["source_snapshot"]
                        or reuse.get("source_run_id") != body["source_run_id"]
                    ):
                        raise ContractError("decision idempotency conflict")
                    if receipt.get("status") == "prepared":
                        self._stop_review()
                        self._dispatch(
                            run_id,
                            lambda: self.engine.decide(
                                run_id,
                                body["node_id"],
                                expected_revision=body["expected_revision"],
                                idempotency_key=body["idempotency_key"],
                                reviewer=body["reviewer"],
                                payload=receipt["payload"],
                                reuse_source=ArtifactRef(**body["source_snapshot"]),
                            ),
                        )
                    return self.snapshot(run_id)
            self._revision(run_id, body["expected_revision"])
            source_id = body["source_run_id"]
            self._owned(source_id)
            source = (
                ArtifactRef(**body["source_snapshot"])
                if "source_snapshot" in body
                else ArtifactRef(**read_json(self.engine.store.root / "runs" / f"{source_id}.json"))
            )
            target = self.engine.repository.load(run_id)
            proposal = decision_proposal(self.engine, target, body["node_id"], source)
            if proposal["source_run_id"] != source_id:
                raise ContractError("source snapshot belongs to another run")
            if body.get("confirm") is not True:
                return proposal
            self._stop_review(validate=lambda: self._revision(run_id, body["expected_revision"]))
            self._dispatch(
                run_id,
                lambda: self.engine.decide(
                    run_id,
                    body["node_id"],
                    expected_revision=body["expected_revision"],
                    idempotency_key=body["idempotency_key"],
                    reviewer=body["reviewer"],
                    payload=proposal["payload"],
                    reuse_source=source,
                ),
            )
            return self.snapshot(run_id)

    def review(self, run_id: str, node_id: str) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise RuntimeError("editor execution service is closing")
            if self._review and (self._review[0].run_id, self._review[0].node_id) == (
                run_id,
                node_id,
            ):
                return {"url": f"http://127.0.0.1:{self._review[1].server_port}/"}
            self._idle()
            self._owned(run_id)

            def validate_review() -> None:
                run = self.engine.repository.load(run_id)
                if run.dag is None or node_id not in run.dag.node_states:
                    raise ContractError("unknown review node")
                state = run.dag.node_states[node_id]
                if not state.attempts or state.current().request is None:
                    raise ContractError("node has no human request")
                plan = self.engine._plan(run)
                if plan.bindings[node_id].adapter != "image_mask_selection@1":
                    raise ContractError("review requires the image_mask_selection adapter")
                node = next(item for item in plan.static_plan.nodes if item.node_id == node_id)
                self.engine._human_evidence(run, plan, node, state.current())
                request_ref = state.current().request
                assert request_ref is not None
                request = self.engine.store.read_structured(request_ref)
                if request.get("decision_contract") != "single-proposal-mask-edit@1" or request.get(
                    "proposals"
                ) != to_primitive(state.current().resolved_inputs.get("proposals")):
                    raise ContractError("node is not an exact mask review request")
                proposals = ArtifactRef(**request["proposals"])
                self.engine.repository.verify_reference_closure(proposals)

            self._stop_review(validate=validate_review)
            service = DagMaskReviewService(self.engine, run_id, node_id)
            server = create_dag_review_server(service, 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            self._review = service, server, thread
            thread.start()
            return {"url": f"http://127.0.0.1:{server.server_port}/"}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._stop_execution.set()
            self._stop_review(wait=True)
            if self._worker:
                self._worker.join()
