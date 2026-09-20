"""Geometry QA for canonicalized DAG shapes; no material or rendering claims."""

from pathlib import Path

from .contracts import ContractError
from .dag_adapters import AdapterSpec, NodeExecutionContext, NodeExecutionResult
from .models import ArtifactRef, StructuredValue
from .operators import validate_geometry
from .serialization import sha256_bytes, to_primitive


class GeometryValidationAdapter:
    @property
    def spec(self) -> AdapterSpec:
        source = sha256_bytes(Path(__file__).with_name("operators.py").read_bytes())
        return AdapterSpec(
            "geometry_validation",
            "1",
            ("geometry_validation@1",),
            parameter_schema={
                "type": "object",
                "properties": {"operator_source_digest": {"type": "string", "enum": [source]}},
            },
            defaults={"operator_source_digest": source},
        )

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        mesh, source = context.inputs.get("mesh"), context.inputs.get("source_mesh")
        if not isinstance(mesh, ArtifactRef) or not isinstance(source, ArtifactRef):
            raise ContractError("geometry validation requires mesh and source_mesh")
        report = validate_geometry(
            context.store,
            mesh,
            derived_from=source,
            run_id=context.run_id,
            canonical_node_id=None,
        )
        reference = context.store.persist_structured(
            StructuredValue("quality_report", "QualityReport", "1.0", to_primitive(report))
        )
        return NodeExecutionResult({"report": reference})
