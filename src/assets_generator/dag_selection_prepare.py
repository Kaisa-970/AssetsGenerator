"""Explicit confirmed selection -> prepared RGBA conversion for modular shape DAGs."""

from pathlib import Path

from .contracts import ContractError
from .dag_adapters import AdapterSpec, NodeExecutionContext, NodeExecutionResult
from .models import ArtifactRef
from .operators import prepare_observation
from .serialization import sha256_bytes
from .workbench_binding import verify_imported_binding


class SelectionPrepareAdapter:
    @property
    def spec(self) -> AdapterSpec:
        fixed = {
            name + "_digest": sha256_bytes(Path(__file__).with_name(name + ".py").read_bytes())
            for name in ("operators", "workbench_binding")
        }
        return AdapterSpec(
            "selection_prepare",
            "1",
            ("selection_prepare@1",),
            parameter_schema={
                "type": "object",
                "properties": {
                    key: {"type": "string", "enum": [value]} for key, value in fixed.items()
                },
            },
            defaults=fixed,
        )

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        binding = context.inputs.get("binding")
        if not isinstance(binding, ArtifactRef):
            raise ContractError("selection preparation requires a binding Artifact")
        raw = context.store.read_structured(binding)
        image, mask = ArtifactRef(**raw["image"]), ArtifactRef(**raw["final_mask"])
        verification = verify_imported_binding(context.store, binding, image, mask)
        prepared = prepare_observation(context.store, image, mask)
        observation = context.store.persist_structured(prepared.bundle)
        return NodeExecutionResult(
            {"rgba": prepared.rgba, "observations": observation, "verification": verification}
        )
