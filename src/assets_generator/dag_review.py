"""Single-node DAG mask review using the existing workbench review UI."""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any, cast

from .compiled_plan import digest
from .contracts import ContractError
from .dag_engine import DagEngine
from .dag_models import DagAttempt, DagNodeState
from .instance_proposals import prepare_selection_mask
from .models import ArtifactRef, BuildRun
from .serialization import canonical_json_bytes, to_primitive
from .workbench_http import OutputPayload
from .workbench_models import MaskDraft


class DagMaskReviewService:
    """Own one existing waiting node, never create a run or fabricate approval."""

    def __init__(self, engine: DagEngine, run_id: str, node_id: str):
        self.engine, self.run_id, self.node_id = engine, run_id, node_id
        self._thread: threading.Thread | None = None
        self._submission: tuple[str, str] | None = None
        self._error: str | None = None
        self._lock = threading.Lock()
        self._closed = False
        self.engine.recover(run_id)
        run, _, attempt, _, _ = self._records()
        plan = self.engine._plan(run)
        if plan.bindings[node_id].adapter != "image_mask_selection@1":
            raise ContractError("review requires the image_mask_selection adapter")
        node = next(item for item in plan.static_plan.nodes if item.node_id == node_id)
        self.engine._human_evidence(run, plan, node, attempt)

    def _records(self) -> tuple[BuildRun, DagNodeState, DagAttempt, dict[str, Any], dict[str, Any]]:
        run = self.engine.repository.load(self.run_id)
        if run.dag is None:
            raise ContractError("not a DAG run")
        state = run.dag.node_states[self.node_id]
        attempt = state.current()
        if attempt.request is None:
            raise ContractError("node has no human request")
        self.engine.repository.verify_reference_closure(attempt.request)
        request = self.engine.store.read_structured(attempt.request)
        if (
            request.get("decision_contract") != "single-proposal-mask-edit@1"
            or request.get("run_id") != self.run_id
            or request.get("node_id") != self.node_id
            or request.get("proposals") != to_primitive(attempt.resolved_inputs.get("proposals"))
        ):
            raise ContractError("node is not an exact mask review request")
        proposals = ArtifactRef(**request["proposals"])
        self.engine.repository.verify_reference_closure(proposals)
        raw = self.engine.store.read_structured(proposals)
        return run, state, attempt, request, raw

    def get_run(self) -> dict[str, Any]:
        run, state, attempt, _, raw = self._records()
        base = f"/runs/{self.run_id}/outputs/"
        review: dict[str, Any] = {
            "confirmed": attempt.decision is not None,
            "request": to_primitive(attempt.request),
            "node_id": self.node_id,
            "items": [
                {
                    "proposal_id": item["proposal_id"],
                    "area": item["area"],
                    "mask_url": base + f"mask_{index}",
                }
                for index, item in enumerate(raw["proposals"])
            ],
        }
        assert run.dag is not None
        review["decision_key"] = next(
            (
                key
                for key, receipt in run.dag.receipts.items()
                if attempt.decision is not None
                and receipt.get("node_id") == self.node_id
                and receipt.get("decision") == to_primitive(attempt.decision)
            ),
            None,
        )
        if attempt.draft:
            review["draft"] = {**attempt.draft, "mask_url": base + "preview"}
        assert run.dag is not None
        return {
            "run_id": self.run_id,
            "revision": run.dag.revision,
            "status": run.status,
            "image_url": base + "image",
            "review": review,
            "stages": [{"stage_id": self.node_id, "status": state.status}],
            "outputs": [
                {"key": key, "label": key, "url": base + key} for key in self._outputs(run)
            ],
            "qa": [],
            "background_error": self._error,
            "submission_pending": bool(self._thread and self._thread.is_alive()),
        }

    def preview(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._closed or (self._thread and self._thread.is_alive()):
                raise ContractError("a decision is already being processed")
            if set(body) != {"expected_revision", "proposal_id", "invert", "keep_largest"}:
                raise ContractError("invalid preview fields")
            if (
                type(body["expected_revision"]) is not int
                or type(body["invert"]) is not bool
                or type(body["keep_largest"]) is not bool
            ):
                raise ContractError("invalid preview field types")
            run, state, attempt, _, raw = self._records()
            assert run.dag is not None
            if state.status != "waiting_for_input" or run.dag.revision != body["expected_revision"]:
                raise ContractError("stale or nonwaiting mask review")
            indexed = {item["proposal_id"]: item for item in raw["proposals"]}
            if body["proposal_id"] not in indexed:
                raise ContractError("unknown proposal")
            final, _, _ = prepare_selection_mask(
                self.engine.store,
                ArtifactRef(**raw["image"]),
                ArtifactRef(**indexed[body["proposal_id"]]["mask"]),
                invert=body["invert"],
                keep_largest=body["keep_largest"],
            )
            draft = {
                **to_primitive(
                    MaskDraft(body["proposal_id"], final, body["invert"], body["keep_largest"])
                ),
                "request": to_primitive(attempt.request),
            }
            draft["preview_signature"] = digest(draft)
            self.engine.save_draft(
                self.run_id,
                self.node_id,
                expected_revision=body["expected_revision"],
                payload=draft,
            )
            return self.get_run()

    def decision(self, body: dict[str, Any]) -> dict[str, Any]:
        required = {
            "expected_revision",
            "idempotency_key",
            "reviewer",
            "request",
            "preview_signature",
            "final_mask",
        }
        if (
            set(body) != required
            or not isinstance(body["reviewer"], str)
            or not body["reviewer"].strip()
        ):
            raise ContractError("decision requires explicit reviewer and loaded preview evidence")
        if (
            type(body["expected_revision"]) is not int
            or not isinstance(body["idempotency_key"], str)
            or not body["idempotency_key"]
        ):
            raise ContractError("invalid decision revision/key")
        fingerprint = digest(
            {key: value for key, value in body.items() if key != "expected_revision"}
        )
        with self._lock:
            if self._closed:
                raise RuntimeError("review service is closing")
            if self._thread and self._thread.is_alive():
                if self._submission != (body["idempotency_key"], fingerprint):
                    raise ContractError("another decision is already being processed")
                return self.get_run()
            run, state, attempt, _, raw = self._records()
            assert run.dag is not None
            draft = attempt.draft
            if (
                not draft
                or body["request"] != to_primitive(attempt.request)
                or body["request"] != draft.get("request")
                or body["preview_signature"] != draft.get("preview_signature")
                or body["final_mask"] != draft.get("final_mask")
                or digest(
                    {key: value for key, value in draft.items() if key != "preview_signature"}
                )
                != body["preview_signature"]
            ):
                raise ContractError("decision does not match the saved preview/request")
            payload = {key: draft[key] for key in ("proposal_id", "invert", "keep_largest")}
            receipt = run.dag.receipts.get(body["idempotency_key"])
            if receipt is not None:
                expected = digest(
                    {"node_id": self.node_id, "reviewer": body["reviewer"], "payload": payload}
                )
                if receipt["request_digest"] != expected:
                    raise ContractError("decision idempotency conflict")
                if receipt.get("status", "committed") == "committed":
                    return self.get_run()
            elif (
                state.status != "waiting_for_input" or run.dag.revision != body["expected_revision"]
            ):
                raise ContractError("stale or nonwaiting mask review")
            indexed = {item["proposal_id"]: item for item in raw["proposals"]}
            final, _, _ = prepare_selection_mask(
                self.engine.store,
                ArtifactRef(**raw["image"]),
                ArtifactRef(**indexed[payload["proposal_id"]]["mask"]),
                invert=payload["invert"],
                keep_largest=payload["keep_largest"],
            )
            if to_primitive(final) != body["final_mask"]:
                raise ContractError("preview differs from current canonical mask")
            self._submission = body["idempotency_key"], fingerprint
            self._error = None

            def submit() -> None:
                try:
                    self.engine.decide(
                        self.run_id,
                        self.node_id,
                        expected_revision=body["expected_revision"],
                        idempotency_key=body["idempotency_key"],
                        reviewer=body["reviewer"],
                        payload=payload,
                    )
                except Exception as error:
                    self._error = str(error)

            self._thread = threading.Thread(target=submit, daemon=True)
            self._thread.start()
            return self.get_run()

    def _outputs(self, run: BuildRun) -> dict[str, ArtifactRef]:
        assert run.dag is not None
        result: dict[str, ArtifactRef] = {}
        for state in run.dag.node_states.values():
            if state.status == "succeeded":
                for key, value in state.current().outputs.items():
                    if key in {"glb", "release", "asset", "qa"} and isinstance(value, ArtifactRef):
                        # Qualified IDs avoid collisions between multiple generators.
                        result[f"{state.node_id}--{key}"] = value
        return result

    def output(self, key: str) -> OutputPayload:
        run, _, attempt, _, raw = self._records()
        outputs = self._outputs(run)
        if key in outputs:
            ref = outputs[key]
        elif key == "image":
            ref = ArtifactRef(**raw["image"])
        elif key == "preview" and attempt.draft:
            ref = ArtifactRef(**attempt.draft["final_mask"])
        elif key.startswith("mask_") and key[5:].isdigit():
            ref = ArtifactRef(**raw["proposals"][int(key[5:])]["mask"])
        else:
            raise KeyError(key)
        self.engine.repository.verify_reference_closure(ref)
        media = self.engine.store.get_manifest(ref.artifact_id).identity.identity_metadata.get(
            "media_type", "application/octet-stream"
        )
        return OutputPayload(self.engine.store.blob_path(ref).read_bytes(), str(media))

    def close(self) -> None:
        self._closed = True
        if self._thread:
            self._thread.join()


def create_dag_review_server(
    service: DagMaskReviewService, port: int = 8765
) -> ThreadingHTTPServer:
    token = secrets.token_urlsafe(32)
    base = f"/runs/{service.run_id}"

    class Handler(BaseHTTPRequestHandler):
        def origin(self) -> str | None:
            host = self.headers.get("Host")
            if host not in {
                f"127.0.0.1:{cast(ThreadingHTTPServer, self.server).server_port}",
                f"localhost:{cast(ThreadingHTTPServer, self.server).server_port}",
            }:
                return None
            return f"http://{host}"

        def reply(self, status: int, data: bytes, media: str = "application/json") -> None:
            self.send_response(status)
            for key, value in {
                "Content-Type": media,
                "Content-Length": str(len(data)),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            }.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if not self.origin():
                self.reply(403, b"{}")
                return
            try:
                if self.path == "/":
                    self.reply(
                        200,
                        files("assets_generator.resources")
                        .joinpath("node-workbench.html")
                        .read_bytes(),
                        "text/html; charset=utf-8",
                    )
                elif self.path == "/session":
                    self.reply(
                        200,
                        canonical_json_bytes(
                            {
                                "token": token,
                                "catalog": {
                                    "backends": [],
                                    "review_only": True,
                                    "run_id": service.run_id,
                                    "node_id": service.node_id,
                                },
                            }
                        ),
                    )
                elif self.path == "/runs":
                    self.reply(200, canonical_json_bytes({"runs": [service.get_run()]}))
                elif self.path == base:
                    self.reply(200, canonical_json_bytes(service.get_run()))
                elif self.path.startswith(base + "/outputs/"):
                    result = service.output(self.path.removeprefix(base + "/outputs/"))
                    self.reply(200, result.data, result.media_type)
                else:
                    self.reply(404, b"{}")
            except (ValueError, KeyError, IndexError, OSError) as error:
                self.reply(400, canonical_json_bytes({"error": str(error)}))

        def do_POST(self) -> None:
            if (
                not self.origin()
                or self.headers.get("Origin") != self.origin()
                or not secrets.compare_digest(self.headers.get("X-Workbench-Token", ""), token)
            ):
                self.reply(403, b"{}")
                return
            try:
                lengths = self.headers.get_all("Content-Length", [])
                if (
                    self.headers.get("Transfer-Encoding")
                    or len(lengths) != 1
                    or self.headers.get_content_type() != "application/json"
                    or not 0 < int(lengths[0]) <= 65536
                ):
                    raise ContractError("invalid JSON request length/type")
                body = json.loads(self.rfile.read(int(lengths[0])))
                if not isinstance(body, dict):
                    raise ContractError("expected object")
                if self.path == base + "/mask-preview":
                    result = service.preview(body)
                elif self.path == base + "/decision":
                    result = service.decision(body)
                else:
                    self.reply(404, b"{}")
                    return
                self.reply(202, canonical_json_bytes(result))
            except (ValueError, KeyError, TypeError, RuntimeError) as error:
                self.reply(409, canonical_json_bytes({"error": str(error)}))

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
