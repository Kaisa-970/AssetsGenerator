"""Canonicalize a native shape using the existing Core implementation."""

from __future__ import annotations

from pathlib import Path

from .contracts import ContractError
from .dag_adapters import AdapterSpec, NodeExecutionContext, NodeExecutionResult
from .models import ArtifactRef, BackendNativeFrame, StructuredValue
from .operators import canonicalize_glb
from .serialization import sha256_bytes
from .spatial import AXIS_TIE_EPSILON, CANONICALIZATION_RULE_VERSION, validate_mesh_native_frame


class CanonicalizeAdapter:
    @property
    def spec(self) -> AdapterSpec:
        fixed = {
            "rule_version": CANONICALIZATION_RULE_VERSION,
            "axis_tie_epsilon": AXIS_TIE_EPSILON,
            "yaw_tie_break": "dominant_native_component_positive",
            "origin_rule": "aabb_floor_center",
            "relative_scale_rule": "largest_aabb_extent_equals_one",
            "spatial_source_digest": sha256_bytes(
                Path(__file__).with_name("spatial.py").read_bytes()
            ),
            "operator_source_digest": sha256_bytes(
                Path(__file__).with_name("operators.py").read_bytes()
            ),
        }
        return AdapterSpec(
            "canonicalize_shape",
            "1",
            ("canonicalize@1",),
            parameter_schema={
                "type": "object",
                "properties": {
                    key: {
                        "type": "number" if isinstance(value, float) else "string",
                        "enum": [value],
                    }
                    for key, value in fixed.items()
                },
            },
            defaults=fixed,
        )

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        mesh, native = context.inputs.get("mesh"), context.inputs.get("native_frame")
        if not isinstance(mesh, ArtifactRef) or not isinstance(native, StructuredValue):
            raise ContractError("canonicalize requires mesh and native frame")
        if context.inputs.get("components"):
            raise ContractError("shape canonicalize does not yet support component maps")
        frame = BackendNativeFrame(**native.value)
        validate_mesh_native_frame(
            context.store.get_manifest(mesh.artifact_id).identity.identity_metadata, frame
        )
        result = canonicalize_glb(context.store, mesh, frame)
        return NodeExecutionResult(
            {
                "mesh": result.mesh,
                "canonical_frame": result.frame,
                "transform": result.transform,
                "spatial_info": result.spatial_info,
                "components": [],
            }
        )
