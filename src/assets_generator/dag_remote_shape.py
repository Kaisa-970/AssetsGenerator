"""Explicit service binding for the existing shape_generation@1 Operator."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contracts import ContractError
from .dag_adapters import AdapterSpec, NodeExecutionContext, NodeExecutionResult
from .dag_remote_adapter import RemoteNodeAdapter
from .models import ArtifactRef
from .remote_http import RemoteJobClient
from .remote_protocol import RemoteIdentity, RemoteJob, RemoteOutput
from .remote_shape_output import import_shape_output


class RemoteShapeAdapter(RemoteNodeAdapter):
    def __init__(self, endpoint: str, identity: RemoteIdentity):
        fixed = {
            "remote_endpoint": RemoteJobClient(endpoint).endpoint,
            "service_id": identity.service_id,
            "backend_digest": identity.backend_digest,
        }
        self._spec = AdapterSpec(
            "remote_shape",
            "1",
            ("shape_generation@1",),
            execution_kind="remote",
            parameter_schema={
                "type": "object",
                "properties": {
                    **{key: {"type": "string", "enum": [value]} for key, value in fixed.items()},
                    "seed": {"type": "integer", "minimum": 0, "maximum": 2**32 - 1},
                    "pipeline_type": {
                        "type": "string",
                        "enum": ["512", "1024", "1024_cascade", "1536_cascade"],
                    },
                },
            },
            defaults={**fixed, "seed": 42, "pipeline_type": "512"},
        )

    @property
    def spec(self) -> AdapterSpec:
        return self._spec

    def input_blobs(self, context: NodeExecutionContext) -> Mapping[str, ArtifactRef]:
        image = context.inputs.get("image")
        if not isinstance(image, ArtifactRef):
            raise ContractError("remote shape requires an image ArtifactRef")
        return {"rgba": image}

    def prepare_payload(self, context: NodeExecutionContext) -> dict[str, Any]:
        return {
            "operation": "shape_generation@1",
            "parameters": {
                "seed": context.parameters["seed"],
                "pipeline_type": context.parameters["pipeline_type"],
            },
        }

    def import_result(
        self, context: NodeExecutionContext, job: RemoteJob, blobs: Mapping[str, bytes]
    ) -> NodeExecutionResult:
        for name, media in {
            "mesh": "model/gltf-binary",
            "shape_metadata": "application/json",
        }.items():
            if RemoteOutput.from_job(job, name).media_type != media:
                raise ContractError("remote shape output media mismatch")
        output = import_shape_output(context.store, blobs)
        # Preserve the bounded Backend declaration on a new immutable mesh identity.
        # The wire mesh identity remains validated by import_shape_output above.
        mesh = output.mesh
        mode = output.backend_metadata.get("postprocess_mode")
        if mode in ("textured_glb", "geometry_fallback_no_texture"):
            identity = context.store.get_manifest(mesh.artifact_id).identity
            mesh = context.store.persist_bytes(
                blobs["mesh"],
                kind=identity.kind,
                schema_name=identity.schema_name,
                schema_version=identity.schema_version,
                identity_metadata={**identity.identity_metadata, "postprocess_mode": mode},
            )
        return NodeExecutionResult(
            {"mesh": mesh, "material": output.material, "native_frame": output.native_frame}
        )
