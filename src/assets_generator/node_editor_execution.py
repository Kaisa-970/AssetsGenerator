"""Single-writer asynchronous execution for the local DAG canvas.

The caller owns the repository context; close this service before releasing it.
GET projections never run recovery or wait for the engine's command lock.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .compiled_plan import CompiledPlan, thaw
from .contracts import ContractError, OperatorSpec, validate_port_value
from .dag_engine import DagEngine
from .dag_review import DagMaskReviewService, create_dag_review_server
from .models import ArtifactRef
from .pipeline import _pipeline_from_raw, _port_spec, compile_pipeline, load_default_operator_specs
from .relations import RelationValidatorRegistry
from .serialization import read_json, to_primitive
from .workbench_http import OutputPayload
from .workflow import _import_image


def validate_editor_inputs(plan: CompiledPlan) -> None:
    """Check the input shape supported by the canvas start endpoint."""
    if set(plan.inputs) != {"image"}:
        raise ContractError("canvas execution currently requires exactly one input named image")
    contract = plan.inputs["image"].contract
    if tuple(contract["kinds"]) != ("rgb_image",):
        raise ContractError("canvas image input must have rgb_image kind")
    if "artifact_ref" not in contract["carriers"] or contract["cardinality"] not in {
        "one",
        "zero_or_one",
    }:
        raise ContractError("canvas image input must accept a scalar ArtifactRef")


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
        self.relations = relations or engine.relations
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._active_run: str | None = None
        self._errors: dict[str, str] = {}
        self._closed = False
        self._review: tuple[DagMaskReviewService, ThreadingHTTPServer, threading.Thread] | None = (
            None
        )

    def _owned(self, run_id: str) -> None:
        if not re.fullmatch(r"dag_[A-Za-z0-9_-]{1,120}", run_id):
            raise ContractError("invalid DAG run ID")
        owner = read_json(self.engine.store.root / "parent_run_owners" / f"{run_id}.json")
        if owner.get("workbench_directory") != str(self.engine.repository.directory.resolve()):
            raise ContractError("run belongs to another editor directory")

    def plan(self, run_id: str) -> dict[str, Any]:
        """Read the verified persisted plan; never recover or dispatch a run."""
        self._owned(run_id)
        run = self.engine.repository.load(run_id)
        return self.engine._plan(run).to_dict()

    def snapshot(self, run_id: str) -> dict[str, Any]:
        self._owned(run_id)
        run = self.engine.repository.load(run_id)
        if run.dag is None:
            raise ContractError("not a DAG run")
        review = self._review
        review_busy = bool(
            review
            and review[0].run_id == run_id
            and review[0]._thread
            and review[0]._thread.is_alive()
        )
        return {
            "run": to_primitive(run),
            "outputs": [
                {
                    "node_id": state.node_id,
                    "port": port,
                    "url": f"/api/runs/{run_id}/outputs/{state.node_id}/{port}",
                }
                for state in run.dag.node_states.values()
                if state.status == "succeeded"
                for port, ref in state.current().outputs.items()
                if port in {"glb", "release", "asset", "qa"} and isinstance(ref, ArtifactRef)
            ],
            "busy": bool(self._active_run == run_id and self._worker and self._worker.is_alive())
            or review_busy,
            "error": self._errors.get(run_id)
            or (review[0]._error if review and review[0].run_id == run_id else None),
        }

    def output(self, run_id: str, node_id: str, port: str) -> OutputPayload:
        self._owned(run_id)
        run = self.engine.repository.load(run_id)
        if run.dag is None or port not in {"glb", "release", "asset", "qa"}:
            raise ContractError("unsupported output")
        state = run.dag.node_states[node_id]
        if state.status != "succeeded":
            raise ContractError("output requires a successful node")
        ref = state.current().outputs[port]
        if not isinstance(ref, ArtifactRef):
            raise ContractError("output is not an artifact")
        self.engine.repository.verify_reference_closure(ref)
        media = self.engine.store.get_manifest(ref.artifact_id).identity.identity_metadata.get(
            "media_type", "application/octet-stream"
        )
        return OutputPayload(self.engine.store.blob_path(ref).read_bytes(), str(media))

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
        self._errors.pop(run_id, None)
        self._active_run = run_id

        def execute() -> None:
            try:
                command()
            except Exception as error:
                self._errors[run_id] = f"{type(error).__name__}: {error}"

        self._worker = threading.Thread(target=execute, daemon=True)
        self._worker.start()

    def start(self, raw: dict[str, Any], image_path: str) -> dict[str, Any]:
        with self._lock:
            self._idle()
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
            validate_editor_inputs(plan.static_plan)
            contract = plan.static_plan.inputs["image"].contract
            if not isinstance(image_path, str) or not image_path.strip():
                raise ContractError("image path is required")
            image = _import_image(self.engine.store, Path(image_path).expanduser(), "rgb_image")
            validate_port_value(
                operator=plan.static_plan.pipeline_name,
                port_name="image",
                spec=_port_spec(thaw(contract)),
                value=image,
                store=self.engine.store,
            )
            self._stop_review()
            run = self.engine.create(plan, {"image": image})
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
            self._stop_review(wait=True)
            if self._worker:
                self._worker.join()
