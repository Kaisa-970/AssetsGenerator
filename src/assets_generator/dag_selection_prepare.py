"""Explicit confirmed selection -> prepared RGBA conversion for modular shape DAGs."""

from pathlib import Path

from .contracts import ContractError
from .dag_adapters import AdapterSpec, NodeExecutionContext, NodeExecutionResult
from .dag_image_adapters import _ComparisonStore
from .models import ArtifactRef
from .operators import prepare_observation
from .serialization import sha256_bytes
from .workbench_binding import create_selection_binding, verify_imported_binding
from .workbench_models import MaskDraft, _decode
from .workbench_persistence import _references


class SelectionPrepareAdapter:
    @property
    def spec(self) -> AdapterSpec:
        fixed = {
            name + "_digest": sha256_bytes(Path(__file__).with_name(name + ".py").read_bytes())
            for name in (
                "operators",
                "workbench_binding",
                "instance_proposals",
                "dag_image_adapters",
            )
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
        # Verify the full existing evidence first. Recomputing an expected mask
        # must never restore a missing blob and hide a broken selection chain.
        pending, checked = [binding], set()
        while pending:
            ref = pending.pop()
            if ref.artifact_id in checked:
                continue
            checked.add(ref.artifact_id)
            if not context.store.verify_digest(ref):
                raise ContractError("selection evidence is missing or corrupt")
            identity = context.store.get_manifest(ref.artifact_id).identity
            if identity.identity_metadata.get("media_type") == "application/json":
                pending.extend(
                    _references(
                        context.store.read_structured(ref), schema_name=identity.schema_name
                    )
                )
        raw = context.store.read_structured(binding)
        expected = create_selection_binding(
            _ComparisonStore(context.store),
            original_proposals=ArtifactRef(**raw["original_proposals"]),
            selection=ArtifactRef(**raw["selection"]),
            draft=_decode(MaskDraft, raw["draft"]),
        )
        if expected != binding:
            raise ContractError("selection binding differs from confirmed selection evidence")
        image, mask = ArtifactRef(**raw["image"]), ArtifactRef(**raw["final_mask"])
        verification = verify_imported_binding(context.store, binding, image, mask)
        prepared = prepare_observation(context.store, image, mask)
        observation = context.store.persist_structured(prepared.bundle)
        return NodeExecutionResult(
            {"rgba": prepared.rgba, "observations": observation, "verification": verification}
        )
