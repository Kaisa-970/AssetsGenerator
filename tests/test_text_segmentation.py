import io
from pathlib import Path

import pytest
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.dag_adapters import AdapterRegistry, NodeExecutionContext
from assets_generator.dag_image_mask import ApplyBinaryMaskAdapter
from assets_generator.dag_remote_profiles import register_remote_shape_profiles
from assets_generator.dag_text_segmentation import SelectTextMaskAdapter
from assets_generator.models import StructuredValue
from assets_generator.pipeline import compile_pipeline, load_default_operator_specs, load_pipeline
from assets_generator.sam3_text_contract import parameters, validate_mask


def test_text_asset_template_binds():
    registry = AdapterRegistry()
    register_remote_shape_profiles(
        registry,
        {
            "default_profile": "sam3d",
            "profiles": {
                "sam3d": {
                    "endpoint": "http://127.0.0.1:8772",
                    "service_id": "sam3d",
                    "backend_digest": "sha256:" + "a" * 64,
                    "operator": "masked_shape_generation@1",
                    "upstream_digest": "sha256:" + "b" * 64,
                },
                "sam3": {
                    "endpoint": "http://127.0.0.1:8773",
                    "service_id": "sam3",
                    "backend_digest": "sha256:" + "c" * 64,
                    "operator": "text_segmentation@1",
                },
            },
        },
    )
    plan = registry.bind_plan(
        compile_pipeline(
            load_pipeline(Path("pipelines/sam3_text_to_asset_v2.yaml")),
            load_default_operator_specs(),
            require_explicit_joins=True,
        )
    )
    assert set(plan.static_plan.inputs) == {"image", "text"}
    nodes = {node.node_id: node for node in plan.static_plan.nodes}
    assert nodes["segment"].operator == "text_segmentation@2"
    assert nodes["segment"].inputs["text"].port == "text"
    assert nodes["shape"].inputs["mask"].node_id == "segment"
    assert nodes["publish"].inputs["asset"].node_id == "assemble"
    assert "prompt" not in plan.bindings["segment"].parameters
    assert all(binding.spec["execution_kind"] != "human" for binding in plan.bindings.values())

    legacy = registry.bind_plan(
        compile_pipeline(
            load_pipeline(Path("pipelines/sam3_text_to_asset_v1.yaml")),
            load_default_operator_specs(),
            require_explicit_joins=True,
        )
    )
    assert set(legacy.static_plan.inputs) == {"image"}
    assert legacy.static_plan.pipeline_version == "1"
    assert plan.static_plan.pipeline_version == "2"
    assert legacy.bindings["segment"].parameters["prompt"] == "robot"
    assert "bake_filter" not in legacy.bindings["shape"].parameters


def test_auto_extract_template_routes_union_mask_without_selection_node():
    registry = AdapterRegistry()
    registry.register(ApplyBinaryMaskAdapter())
    register_remote_shape_profiles(
        registry,
        {
            "default_profile": "sam3",
            "profiles": {
                "sam3": {
                    "endpoint": "http://127.0.0.1:8773",
                    "service_id": "sam3",
                    "backend_digest": "sha256:" + "c" * 64,
                    "operator": "text_segmentation@1",
                }
            },
        },
    )
    plan = registry.bind_plan(
        compile_pipeline(
            load_pipeline(Path("pipelines/sam3_text_auto_extract_v1.yaml")),
            load_default_operator_specs(),
            require_explicit_joins=True,
        )
    )
    nodes = {node.node_id: node for node in plan.static_plan.nodes}
    assert nodes["segment"].operator == "text_segmentation@2"
    binding = nodes["extract"].inputs["mask"]
    assert (binding.node_id, binding.port) == ("segment", "mask")
    assert "select" not in plan.static_plan.nodes


@pytest.mark.parametrize("count", [0, 1, 2])
def test_selection_never_silently_unions_masks(tmp_path, count):
    store = LocalArtifactStore(tmp_path)

    def persist(mode, value, kind):
        out = io.BytesIO()
        Image.new(mode, (4, 4), value).save(out, format="PNG")
        return store.persist_bytes(
            out.getvalue(), kind=kind, schema_name="png", schema_version="1.0"
        )

    image = persist("RGB", "red", "rgb_image")
    mask = persist("L", 255, "binary_mask")
    bundle = store.persist_structured(
        StructuredValue(
            "remote_job_result",
            "TextMaskCandidates",
            "1.0",
            {
                "image": {"artifact_id": image.artifact_id},
                "candidates": [{"mask": {"artifact_id": mask.artifact_id}}] * count,
            },
        )
    )
    context = NodeExecutionContext(
        "run", "select", {"candidates": bundle}, {"candidate_index": -1}, store
    )
    if count == 1:
        assert SelectTextMaskAdapter().execute(context).outputs["mask"] == mask
    else:
        with pytest.raises(ValueError, match="exactly one"):
            SelectTextMaskAdapter().execute(context)


def test_invalid_prompt_and_empty_mask_rejected():
    with pytest.raises(ValueError):
        parameters({"prompt": " ", "confidence": 0.5})
    buffer = io.BytesIO()
    Image.new("L", (2, 2), 0).save(buffer, format="PNG")
    with pytest.raises(ValueError, match="nonempty"):
        validate_mask(buffer.getvalue(), (2, 2))


@pytest.mark.parametrize("count", [0, 1, 2])
def test_import_checks_request_identity_and_persists_candidates(tmp_path, count):
    from assets_generator.dag_text_segmentation import RemoteTextSegmentationAdapter
    from assets_generator.models import ArtifactRef
    from assets_generator.remote_protocol import RemoteIdentity, RemoteJob, RemoteRequest
    from assets_generator.serialization import canonical_json_bytes, sha256_bytes, to_primitive

    store = LocalArtifactStore(tmp_path)
    image_bytes = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(image_bytes, format="PNG")
    image = store.persist_bytes(
        image_bytes.getvalue(), kind="rgb_image", schema_name="png", schema_version="1.0"
    )
    identity = RemoteIdentity("sam3", "sha256:" + "a" * 64)
    adapter = RemoteTextSegmentationAdapter("http://127.0.0.1:8773", identity)
    context = NodeExecutionContext(
        "run",
        "segment",
        {"image": image},
        adapter.spec.normalize_parameters({"prompt": "robot"}),
        store,
        input_digest="sha256:" + "b" * 64,
        binding_digest="sha256:" + "c" * 64,
    )
    payload = adapter.prepare_payload(context)
    payload.update(
        input_digest=context.input_digest,
        binding_digest=context.binding_digest,
        input_blobs={
            "image": {
                "artifact_id": image.artifact_id,
                "identity": to_primitive(store.get_manifest(image.artifact_id).identity),
            }
        },
    )
    request = RemoteRequest.create(identity, "job", payload)
    mask = io.BytesIO()
    Image.new("L", (4, 4), 255).save(mask, format="PNG")
    evidence = {
        "schema": "Sam3TextCandidates@1",
        "image_artifact_id": image.artifact_id,
        "image_digest": sha256_bytes(image_bytes.getvalue()),
        "parameters": payload["parameters"],
        "backend_digest": identity.backend_digest,
        "request_digest": request.request_digest,
        "candidates": [
            {
                "output_id": "mask_0",
                "blob_digest": sha256_bytes(mask.getvalue()),
                "score": 0.9,
                "box": [0, 0, 4, 4],
            }
        ],
    }
    evidence["candidates"] = [
        {**evidence["candidates"][0], "output_id": f"mask_{i}"} for i in range(count)
    ]
    blobs = {
        "evidence": canonical_json_bytes(evidence),
        **{f"mask_{i}": mask.getvalue() for i in range(count)},
    }
    wire = {k: v for k, v in request.to_dict().items() if k != "payload"}
    wire.update(
        protocol_version="1",
        job_id="job",
        state="succeeded",
        error=None,
        result={
            "outputs": [
                {
                    "output_id": n,
                    "blob_digest": sha256_bytes(data),
                    "byte_length": len(data),
                    "media_type": "application/json" if n == "evidence" else "image/png",
                }
                for n, data in blobs.items()
            ]
        },
    )
    job = RemoteJob.parse(wire, request)
    if count == 0:
        with pytest.raises(ValueError, match="没有找到"):
            adapter.import_result(context, job, blobs)
        return
    result = adapter.import_result(context, job, blobs)
    ref = result.outputs["candidates"]
    assert isinstance(ref, ArtifactRef)
    assert len(store.read_structured(ref)["candidates"]) == count
    output_mask = result.outputs["mask"]
    with Image.open(store.blob_path(output_mask)) as merged:
        assert merged.mode == "L"
        assert merged.getextrema() == (255, 255)
    from assets_generator.dag_persistence import DagRepository

    with DagRepository(store, tmp_path / "dag") as repo:
        repo.verify_reference_closure(output_mask)
    evidence["request_digest"] = "sha256:" + "f" * 64
    with pytest.raises(ValueError, match="evidence mismatch"):
        adapter.import_result(context, job, {**blobs, "evidence": canonical_json_bytes(evidence)})


@pytest.mark.parametrize("mode", ["RGB", "L"])
@pytest.mark.parametrize("metadata", ["transparency", "orientation"])
def test_transparency_and_orientation_rejected(mode, metadata):
    from assets_generator.sam3_text_contract import image_size

    buffer = io.BytesIO()
    image = Image.new(mode, (2, 2), 255)
    options = {}
    if metadata == "transparency":
        options["transparency"] = (255, 255, 255) if mode == "RGB" else 0
    else:
        exif = Image.Exif()
        exif[274] = 6
        options["exif"] = exif
    image.save(buffer, format="PNG", **options)
    with pytest.raises(ValueError):
        if mode == "RGB":
            image_size(buffer.getvalue())
        else:
            validate_mask(buffer.getvalue(), (2, 2))


def test_idle_worker_does_not_hash_and_changed_deployment_never_executes(tmp_path, monkeypatch):
    from assets_generator import sam3_text_identity
    from assets_generator.remote_protocol import RemoteIdentity, RemoteRequest
    from assets_generator.remote_service_store import RemoteServiceStore
    from assets_generator.sam3_text_cli import execute_verified_job

    identity = RemoteIdentity("sam3", "sha256:" + "a" * 64)
    store = RemoteServiceStore(tmp_path / "service.sqlite", identity)
    hashes = []
    calls = []

    def changed(_):
        hashes.append(1)
        return RemoteIdentity("sam3", "sha256:" + "b" * 64)

    monkeypatch.setattr(sam3_text_identity, "deployment_identity", changed)
    try:

        def handler(*args):
            calls.append(1)
            return {}

        assert execute_verified_job(store, handler, {}) is None
        assert not hashes
        store.submit(RemoteRequest.create(identity, "job", {}))
        assert execute_verified_job(store, handler, {}).state == "failed"
        assert hashes == [1]
        assert not calls
    finally:
        store.close()


def test_text_port_payload_uses_verified_text_artifact(tmp_path):
    import threading
    from types import SimpleNamespace

    from assets_generator.dag_text_segmentation import RemoteTextInputSegmentationAdapter
    from assets_generator.node_editor_execution import NodeEditorExecution
    from assets_generator.remote_protocol import RemoteIdentity

    store = LocalArtifactStore(tmp_path)
    service = SimpleNamespace(
        engine=SimpleNamespace(store=store), _lock=threading.RLock(), _closed=False
    )
    from assets_generator.models import ArtifactRef

    ref = ArtifactRef(**NodeEditorExecution.upload_text(service, b"chair")["text_ref"])
    assert store.verify_digest(ref)
    adapter = RemoteTextInputSegmentationAdapter(
        "http://127.0.0.1:8773", RemoteIdentity("sam3", "sha256:" + "a" * 64)
    )
    context = NodeExecutionContext(
        "run", "segment", {"text": ref}, adapter.spec.normalize_parameters({}), store
    )
    assert adapter.prepare_payload(context)["parameters"]["prompt"] == "chair"
    assert "prompt" not in adapter.spec.defaults
    for bad in (b"", b"   ", b"\xff"):
        with pytest.raises(ValueError):
            NodeEditorExecution.upload_text(service, bad)
    store.blob_path(ref).unlink()
    with pytest.raises(ValueError, match="intact text"):
        adapter.prepare_payload(context)


def test_read_bound_text_checks_content_and_contract(tmp_path):
    import threading
    from types import SimpleNamespace

    from assets_generator.models import ArtifactRef
    from assets_generator.node_editor_execution import NodeEditorExecution

    store = LocalArtifactStore(tmp_path)
    service = SimpleNamespace(
        engine=SimpleNamespace(store=store), _lock=threading.RLock(), _closed=False
    )
    ref = NodeEditorExecution.upload_text(service, b"table")["text_ref"]
    assert NodeEditorExecution.read_text_input(service, ref["artifact_id"])["text"] == "table"
    wrong = store.persist_bytes(
        b"table", kind="binary_mask", schema_name="png", schema_version="1.0"
    )
    with pytest.raises(ValueError, match="plain text"):
        NodeEditorExecution.read_text_input(service, wrong.artifact_id)
    store.blob_path(ArtifactRef(**ref)).write_bytes(b"chair")
    with pytest.raises(ValueError, match="digest mismatch"):
        NodeEditorExecution.read_text_input(service, ref["artifact_id"])
