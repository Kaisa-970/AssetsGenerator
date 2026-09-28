"""Small image-to-mesh service SDK; run inside the model's own environment.

The trusted callback runs synchronously in this dedicated service process, never
inside the editor/Core process. Unknown interrupted jobs are not automatically retried.
"""

from __future__ import annotations

import json
import logging
import tempfile
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .artifact_store import LocalArtifactStore
from .compiled_plan import thaw
from .dag_adapters import AdapterSpec
from .model_service_descriptor import validate_descriptor
from .models import BackendNativeFrame, PBRMaterial, StructuredValue
from .operators import ShapeOutput
from .remote_protocol import RemoteIdentity, RemoteRequest
from .remote_service_http import create_remote_server
from .remote_service_store import RemoteServiceStore
from .remote_service_worker import ServiceOutput, execute_next_service_job
from .remote_shape_input import import_shape_rgba
from .remote_shape_output import export_shape_output
from .serialization import canonical_json_bytes, sha256_bytes, to_primitive
from .spatial import validate_backend_native_frame

Inference = Callable[[Path, Mapping[str, Any]], bytes]
_LOG = logging.getLogger(__name__)


class ShapeModelService:
    """Provide metadata, a synchronous infer(rgba_png_path, parameters) callback, and GLB.

    deployment must identify the actual model weights/code/environment (digests or
    immutable revisions). The wrapper additionally binds schema, defaults and frame.
    Callback-created daemon/escaped processes are outside this SDK's contract.
    """

    def __init__(
        self,
        *,
        service_id: str,
        display_name: str,
        deployment: Mapping[str, Any],
        frame: BackendNativeFrame,
        infer: Inference,
        directory: Path,
        parameter_schema: Mapping[str, Any] | None = None,
        defaults: Mapping[str, Any] | None = None,
        capabilities: Mapping[str, Mapping[str, Any]] | None = None,
        port: int = 0,
    ) -> None:
        if not deployment:
            raise ValueError("deployment must identify model weights, code and environment")
        validate_backend_native_frame(frame)
        schema = dict(parameter_schema or {"type": "object", "properties": {}})
        values = dict(defaults or {})
        raw_capabilities = capabilities or {
            "shape_generation@1": {"parameter_schema": schema, "defaults": values}
        }
        if not raw_capabilities:
            raise ValueError("capabilities must contain at least one capability")
        self.parameters = {}
        capability_descriptors = []
        for capability_id, raw in raw_capabilities.items():
            if not isinstance(capability_id, str) or not capability_id.strip():
                raise ValueError("capability IDs must be nonempty strings")
            capability_schema = dict(raw.get("parameter_schema", schema))
            capability_defaults = dict(raw.get("defaults", values))
            capability_frame = raw.get("frame_id", frame.frame_id)
            capability_up_axis = raw.get("up_axis", frame.up_axis)
            capability_unit = raw.get("unit", frame.unit)
            if (capability_frame, capability_up_axis, capability_unit) != (
                frame.frame_id,
                frame.up_axis,
                frame.unit,
            ):
                raise ValueError(
                    "all shape capabilities must share the service output frame and unit"
                )
            self.parameters[capability_id] = AdapterSpec(
                "shape_model_service",
                "1",
                ("shape_generation@1",),
                parameter_schema=capability_schema,
                defaults=capability_defaults,
            )
            capability_descriptors.append(
                {
                    "capability_id": capability_id,
                    "display_name": raw.get("display_name", capability_id),
                    "operator": "shape_generation@1",
                    "transport": "remote_jobs@1",
                    "frame_id": capability_frame,
                    "up_axis": capability_up_axis,
                    "unit": capability_unit,
                    "parameter_schema": capability_schema,
                    "defaults": capability_defaults,
                }
            )
        first = capability_descriptors[0]
        self.identity = RemoteIdentity(
            service_id,
            sha256_bytes(
                canonical_json_bytes(
                    {
                        "deployment": dict(deployment),
                        "frame": to_primitive(frame),
                        "capabilities": capability_descriptors,
                        "wrapper_digest": sha256_bytes(Path(__file__).read_bytes()),
                    }
                )
            ),
        )
        self.descriptor = validate_descriptor(
            {
                "schema_version": "model_service@1",
                "display_name": display_name,
                "service_id": service_id,
                "backend_digest": self.identity.backend_digest,
                "operator": first["operator"],
                "transport": first["transport"],
                "frame_id": first["frame_id"],
                "up_axis": first["up_axis"],
                "unit": first["unit"],
                "parameter_schema": first["parameter_schema"],
                "defaults": first["defaults"],
                "capabilities": capability_descriptors,
            }
        )
        self.frame, self.infer = frame, infer
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.store = RemoteServiceStore(directory / "jobs.sqlite", self.identity)
        self.server = create_remote_server(self.store, port=port, descriptor=self.descriptor)
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self.last_worker_error: str | None = None

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def handle(
        self, request: RemoteRequest, service: RemoteServiceStore
    ) -> dict[str, ServiceOutput]:
        if request.identity != self.identity or service.identity != self.identity:
            raise ValueError("model deployment identity mismatch")
        payload = json.loads(request.payload_json)
        allowed = {
            "operation",
            "parameters",
            "input_blobs",
            "input_digest",
            "binding_digest",
            "capability_id",
        }
        if set(payload) - allowed or set(payload) - {"capability_id"} != {
            "operation",
            "parameters",
            "input_blobs",
            "input_digest",
            "binding_digest",
        }:
            raise ValueError("invalid shape request")
        if payload["operation"] != "shape_generation@1":
            raise ValueError("invalid shape request")
        capability_id = payload.get("capability_id") or "shape_generation@1"
        if capability_id == "shape_generation":
            capability_id = "shape_generation@1"
        if capability_id not in self.parameters:
            raise ValueError("invalid shape request")
        parameters = thaw(
            self.parameters[capability_id].normalize_parameters(payload["parameters"])
        )
        with tempfile.TemporaryDirectory(prefix="inference-", dir=self.directory) as temporary:
            store = LocalArtifactStore(Path(temporary) / "store")
            rgba = import_shape_rgba(request, service, store)
            data = self.infer(store.blob_path(rgba), parameters)
            if not isinstance(data, bytes):
                raise ValueError("inference must return self-contained GLB bytes")
            metadata = {
                "frame_id": self.frame.frame_id,
                "unit": self.frame.unit,
                "up_axis": self.frame.up_axis,
                "media_type": "model/gltf-binary",
            }
            if self.frame.forward_axis is not None:
                metadata["forward_axis"] = self.frame.forward_axis
            mesh = store.persist_bytes(
                data,
                kind="triangle_mesh",
                schema_name="glTF",
                schema_version="2.0",
                identity_metadata=metadata,
            )
            output = ShapeOutput(
                mesh,
                StructuredValue(
                    "pbr_material", "PBRMaterial", "1.0", to_primitive(PBRMaterial([1.0] * 4))
                ),
                StructuredValue(
                    "backend_native_frame", "BackendNativeFrame", "1.0", to_primitive(self.frame)
                ),
                {
                    "service_id": self.identity.service_id,
                    "backend_digest": self.identity.backend_digest,
                    "parameters": parameters,
                    "appearance": "preserve_mesh",
                },
            )
            return export_shape_output(store, output)

    def start_worker(self) -> None:
        """Serial queue admission is persisted; a prior running job blocks dispatch."""
        if self._worker is not None:
            raise RuntimeError("worker already started")

        def loop() -> None:
            previous_error = None
            while not self._stop.is_set():
                try:
                    job = execute_next_service_job(self.store, self.handle)
                    self.last_worker_error = None
                    previous_error = None
                    if job is not None:
                        continue
                except Exception as error:
                    # Do not release claims or re-submit on storage/unknown failures.
                    self.last_worker_error = str(error)
                    if previous_error != self.last_worker_error:
                        _LOG.error("model queue stopped: %s", error)
                    previous_error = self.last_worker_error
                self._stop.wait(0.2)

        self._worker = threading.Thread(target=loop, name="shape-model-worker", daemon=True)
        self._worker.start()

    def close(self) -> None:
        """Call after stopping the HTTP serving thread; never close storage mid-inference."""
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=10)
            if self._worker.is_alive():
                raise RuntimeError(
                    "inference still running; job remains running, stop process explicitly"
                )
        self.server.server_close()
        self.store.close()

    def serve(self) -> None:
        self.start_worker()
        print(self.endpoint, flush=True)
        try:
            self.server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self.close()
