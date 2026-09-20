"""Bind an already validated local shape profile to a gated service handler."""

from __future__ import annotations

from copy import copy
from pathlib import Path

from .backend_registry import ShapeBackend
from .compiled_plan import digest
from .operators import Trellis2Backend, TripoSRBackend
from .remote_protocol import RemoteIdentity
from .remote_service_process import ServiceProcessWorker
from .remote_shape_service import ShapeServiceHandler
from .serialization import canonical_json_bytes
from .workbench_engine import BackendProfile


def shape_handler_from_profile(
    profile: BackendProfile, *, service_id: str, workspace: Path
) -> ShapeServiceHandler:
    """Reuse profile resource guards; caller must use the real profile loader first."""
    if profile.test_only or profile.identity_check is None:
        raise ValueError("shape service requires a verified real profile")
    resolved = profile.shape_plan.backend_for("generate_shape", "shape_generation@1")
    implementation = resolved.implementation
    if not isinstance(implementation, (Trellis2Backend, TripoSRBackend)):
        raise ValueError("unsupported real shape implementation")
    expected = canonical_json_bytes(profile.shape_identity)
    expected_config = canonical_json_bytes(
        {key: value for key, value in vars(implementation).items() if key != "worker"}
    )
    identity = RemoteIdentity(
        service_id,
        digest(
            {
                "schema": "shape-service-profile@1",
                "shape_identity": profile.shape_identity,
                "operator": "shape_generation@1",
                "backend": resolved.name,
                "backend_version": resolved.backend_version,
            }
        ),
    )

    def verify() -> RemoteIdentity:
        assert profile.identity_check is not None
        profile.identity_check()
        if canonical_json_bytes(profile.shape_identity) != expected:
            raise ValueError("shape profile identity changed")
        if (
            canonical_json_bytes(
                {key: value for key, value in vars(implementation).items() if key != "worker"}
            )
            != expected_config
        ):
            raise ValueError("shape Backend configuration changed")
        return identity

    def factory(worker: ServiceProcessWorker) -> ShapeBackend:
        verify()
        backend = copy(implementation)
        backend.worker = worker  # type: ignore[assignment]
        return backend

    verify()
    return ShapeServiceHandler(identity, workspace, factory, verify)
