import io
from pathlib import Path

import pytest
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.dag_adapters import AdapterRegistry, NodeExecutionContext
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
            load_pipeline(Path("pipelines/sam3_text_to_asset_v1.yaml")),
            load_default_operator_specs(),
            require_explicit_joins=True,
        )
    )
    assert plan.bindings["segment"].parameters["prompt"] == "robot"


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


def test_import_checks_request_identity_and_persists_candidates(tmp_path):
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
    blobs = {"evidence": canonical_json_bytes(evidence), "mask_0": mask.getvalue()}
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
    result = adapter.import_result(context, job, blobs)
    ref = result.outputs["candidates"]
    assert isinstance(ref, ArtifactRef)
    assert len(store.read_structured(ref)["candidates"]) == 1
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
