"""Trusted shape handler composing existing isolated Backends and remote boundaries."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from pathlib import Path

from .artifact_store import LocalArtifactStore
from .backend_registry import ShapeBackend
from .operators import ShapeOutput
from .remote_protocol import RemoteIdentity, RemoteRequest
from .remote_service_process import ServiceProcessWorker
from .remote_service_store import RemoteServiceStore
from .remote_service_worker import ServiceOutput
from .remote_shape_input import import_shape_rgba
from .remote_shape_output import export_shape_output


class ShapeServiceHandler:
    """Factory and identity verifier are trusted deployment configuration, never wire code.

    The factory must inject the supplied worker into an existing independent-
    process Backend. Identity verification runs before and after inference.
    """

    def __init__(
        self,
        identity: RemoteIdentity,
        workspace: Path,
        factory: Callable[[ServiceProcessWorker], ShapeBackend],
        verify_identity: Callable[[], RemoteIdentity],
        validate_output_identity: Callable[[ShapeOutput], None] | None = None,
    ):
        self.identity = identity
        self.workspace = workspace
        self.factory = factory
        self.verify_identity = verify_identity
        self.validate_output_identity = validate_output_identity

    def __call__(
        self, request: RemoteRequest, service: RemoteServiceStore
    ) -> dict[str, ServiceOutput]:
        if request.identity != self.identity or service.identity != self.identity:
            raise ValueError("shape handler service identity mismatch")
        payload = json.loads(request.payload_json)
        if (
            set(payload)
            != {"operation", "input_blobs", "input_digest", "binding_digest", "parameters"}
            or payload["operation"] != "shape_generation@1"
        ):
            raise ValueError("invalid shape service request")
        parameters = payload["parameters"]
        if not isinstance(parameters, dict) or set(parameters) != {"seed", "pipeline_type"}:
            raise ValueError("shape parameters require seed and pipeline_type")
        if type(parameters["seed"]) is not int or not 0 <= parameters["seed"] < 2**32:
            raise ValueError("invalid shape seed")
        if parameters["pipeline_type"] not in ("512", "1024", "1024_cascade", "1536_cascade"):
            raise ValueError("unsupported shape pipeline_type")
        if self.verify_identity() != self.identity:
            raise ValueError("shape deployment identity changed")
        self.workspace.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="shape-service-", dir=self.workspace) as temporary:
            store = LocalArtifactStore(Path(temporary) / "store")
            rgba = import_shape_rgba(request, service, store)
            backend = self.factory(ServiceProcessWorker(service, request))
            output = backend.generate(store, rgba, **parameters)
            if self.verify_identity() != self.identity:
                raise ValueError("shape deployment identity changed during inference")
            if self.validate_output_identity is not None:
                self.validate_output_identity(output)
            raw_worker = service.worker_record(request)
            if raw_worker is None:
                raise ValueError("shape Backend did not register its process")
            worker = json.loads(raw_worker)
            if worker["launch_phase"] != "exit_observed" or worker["exit_code"] != 0:
                raise ValueError("shape Backend lacks successful process evidence")
            return export_shape_output(store, output)
