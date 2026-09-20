from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.dag_provenance import persist_node_provenance
from assets_generator.models import StructuredValue


def test_output_occurrences_and_attempts_have_distinct_provenance(tmp_path):
    store = LocalArtifactStore(tmp_path)
    ref = store.persist_bytes(
        b"tiny", kind="quality_evidence", schema_name="test", schema_version="1"
    )
    common = dict(
        run_id="run_test",
        operator="copy@2",
        adapter_identity={"spec": {"name": "copy", "version": "3"}},
        parameters={},
        inputs={"source": ref},
        outputs={"items": [ref, ref]},
    )
    all_records = []
    for node, attempt in [("left", 1), ("right", 1), ("left", 2)]:
        refs = persist_node_provenance(store, node_id=node, attempt=attempt, **common)
        records = [store.read_structured(item) for item in refs["items"]]
        for record in records:
            assert record["attempt"] == attempt
            assert record["operator_version"] == "2"
            assert record["backend_version"] == "3"
            assert record["node_id"] == node
            assert record["output_artifact_id"] == ref.artifact_id
            assert record["derived_from_artifact_ids"] == [ref.artifact_id]
        all_records.extend(records)
    assert len({r["provenance_id"] for r in all_records}) == 6
    assert len({r["output_id"] for r in all_records}) == 6


def test_decision_and_structured_input_are_preserved(tmp_path):
    store = LocalArtifactStore(tmp_path)
    ref = store.persist_bytes(
        b"output", kind="quality_evidence", schema_name="test", schema_version="1"
    )
    decision = store.persist_structured(StructuredValue("quality_report", "DecisionTest", "1", {}))
    structured = StructuredValue("semantic_info", "SemanticInfo", "1", {"label": "test"})
    refs = persist_node_provenance(
        store,
        run_id="run_test",
        node_id="review",
        attempt=1,
        operator="review@1",
        adapter_identity={"version": "1"},
        parameters={},
        inputs={"info": structured},
        outputs={"output": ref},
        decision_ref=decision,
    )
    record = store.read_structured(refs["output"][0])
    assert record["derived_from_artifact_ids"] == [decision.artifact_id]
    assert record["parameters"]["resolved_inputs"]["info"]["value"] == {"label": "test"}


def test_remote_execution_evidence_is_a_provenance_parent(tmp_path):
    import pytest

    from assets_generator.contracts import ContractError

    store = LocalArtifactStore(tmp_path)
    output = store.persist_bytes(
        b"output", kind="quality_evidence", schema_name="test", schema_version="1"
    )
    evidence = store.persist_structured(
        StructuredValue("remote_job_result", "RemoteJobResult", "1.0", {"job": "test"})
    )
    args = dict(
        run_id="run_test",
        node_id="remote",
        attempt=1,
        operator="copy@1",
        adapter_identity={"version": "1"},
        parameters={},
        inputs={},
        outputs={"output": output},
        execution_evidence=evidence,
    )
    refs = persist_node_provenance(store, **args)
    record = store.read_structured(refs["output"][0])
    assert record["derived_from_artifact_ids"] == [evidence.artifact_id]
    store.blob_path(evidence).unlink()
    with pytest.raises(ContractError, match="execution evidence"):
        persist_node_provenance(store, **args)
