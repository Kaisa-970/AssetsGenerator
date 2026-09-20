from dataclasses import replace
from pathlib import Path

import pytest

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import ContractError, OperatorSpec, PortSpec, RelationSpec
from assets_generator.models import StructuredValue
from assets_generator.relations import (
    IndependentInputsValidator,
    RelationValidatorRegistry,
    RelationValidatorSpec,
    RuntimeRelationContext,
    StaticRelationContext,
    default_relation_registry,
)
from assets_generator.serialization import sha256_bytes


def operator(*relations):
    port = PortSpec(("pbr_material",), carriers=("structured",))
    return OperatorSpec("join", "1", {"a": port, "b": port}, {}, relations)


def test_independent_sources_explicitly_allowed_without_lineage_claim(tmp_path):
    registry = default_relation_registry()
    spec = operator(RelationSpec("independent_inputs@1", ("a", "b")))
    compiled = registry.validate_static(
        spec,
        {"a": "left.outputs.result", "b": "right.outputs.result"},
        require_explicit_joins=True,
    )
    registry.validate_runtime(
        spec,
        {
            "a": StructuredValue("pbr_material", "pbr", "1", {"source": "left"}),
            "b": StructuredValue("pbr_material", "pbr", "1", {"source": "right"}),
        },
        LocalArtifactStore(tmp_path),
        resolved_relations=compiled,
    )
    assert compiled[0].validator == "independent_inputs@1"
    assert compiled[0].digest == registry.resolve("independent_inputs@1").spec.digest


def test_join_policy_is_explicit_and_legacy_operators_still_compile():
    registry = default_relation_registry()
    bindings = {"a": "left.outputs.result", "b": "right.outputs.result"}
    assert registry.validate_static(operator(), bindings) == ()
    with pytest.raises(ContractError, match="explicit relation"):
        registry.validate_static(operator(), bindings, require_explicit_joins=True)


@pytest.mark.parametrize(
    "relation,match",
    [
        (RelationSpec("missing@1", ("a", "b")), "unknown relation validator"),
        (RelationSpec("independent_inputs@1", ("a", "missing")), "unknown inputs"),
    ],
)
def test_bad_declarations_rejected(relation, match):
    with pytest.raises(ContractError, match=match):
        default_relation_registry().resolve_operator(operator(relation))


def test_duplicate_registration_and_reversed_declarations_rejected():
    registry = default_relation_registry()
    with pytest.raises(ContractError, match="duplicate relation validator"):
        registry.register(IndependentInputsValidator())
    with pytest.raises(ContractError, match="duplicate relation"):
        registry.resolve_operator(
            operator(
                RelationSpec("independent_inputs@1", ("a", "b")),
                RelationSpec("independent_inputs@1", ("b", "a")),
            )
        )


@pytest.mark.parametrize(
    "validator,inputs",
    [
        ("unversioned", ("a", "b")),
        ("x@", ("a", "b")),
        ("x@1", ("a", "a")),
        ("x@1", ("a",)),
        ("x@1", "ab"),
        (None, ("a", "b")),
        ("x@1", None),
        ("x@1", {"a": 1, "b": 2}),
    ],
)
def test_relation_shape_rejected(validator, inputs):
    with pytest.raises(ContractError):
        RelationSpec(validator, inputs)


class MatchingValues:
    spec = RelationValidatorSpec("matching_test", "1", sha256_bytes(b"matching test v1"))

    def __init__(self):
        self.static_calls = 0
        self.runtime_calls = 0

    def validate_static(self, context: StaticRelationContext):
        self.static_calls += 1
        assert set(context.bindings) == {"a", "b"}
        with pytest.raises(TypeError):
            context.bindings["a"] = "other"

    def validate_runtime(self, context: RuntimeRelationContext):
        self.runtime_calls += 1
        a, b = context.values["a"], context.values["b"]
        if a != b:
            raise ContractError("runtime relation mismatch")


def test_runtime_checks_real_data_and_pinned_identity(tmp_path):
    validator = MatchingValues()
    registry = RelationValidatorRegistry()
    registry.register(validator)
    spec = operator(RelationSpec("matching_test@1", ("a", "b")))
    compiled = registry.validate_static(spec, {"a": "x", "b": "y"})
    assert validator.static_calls == 1
    assert validator.runtime_calls == 0
    values = {
        "a": StructuredValue("pbr_material", "pbr", "1", {"source": "a"}),
        "b": StructuredValue("pbr_material", "pbr", "1", {"source": "b"}),
    }
    store = LocalArtifactStore(tmp_path)
    with pytest.raises(ContractError, match="runtime relation mismatch"):
        registry.validate_runtime(spec, values, store, resolved_relations=compiled)
    assert validator.runtime_calls == 1
    with pytest.raises(ContractError, match="differs from compiled plan"):
        registry.validate_runtime(
            spec,
            values,
            store,
            resolved_relations=(replace(compiled[0], digest=sha256_bytes(b"stale contract")),),
        )
    assert validator.runtime_calls == 1
    with pytest.raises(ContractError, match="requires exactly one"):
        registry.validate_runtime(spec, {"a": values["a"]}, store, resolved_relations=compiled)
    assert validator.runtime_calls == 1


def test_validator_identity_cannot_change_after_registration():
    validator = MatchingValues()
    registry = RelationValidatorRegistry()
    registry.register(validator)
    validator.spec = replace(validator.spec, implementation_digest=sha256_bytes(b"changed"))
    with pytest.raises(ContractError, match="identity changed"):
        registry.resolve("matching_test@1")


def test_validator_implementation_identity_changes_contract_digest():
    first = RelationValidatorSpec("check", "1", sha256_bytes(b"first"))
    assert first.digest != replace(first, implementation_digest=sha256_bytes(b"second")).digest


def test_builtin_validator_identity_uses_actual_module_content():
    import assets_generator.relations as module

    assert IndependentInputsValidator.spec.implementation_digest == sha256_bytes(
        Path(module.__file__).read_bytes()
    )
