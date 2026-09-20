import io
import json
from dataclasses import replace

import pytest
from PIL import Image
from test_comfy_profile import profile

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.comfy_profile import ComfyImageProfile
from assets_generator.contracts import ContractError
from assets_generator.dag_adapters import NodeExecutionContext
from assets_generator.dag_comfy_image import ComfyImageAdapter
from assets_generator.remote_binding import RemoteAttemptBinding
from assets_generator.remote_protocol import RemoteJob
from assets_generator.serialization import canonical_json_bytes, sha256_bytes


@pytest.mark.parametrize(
    "damage", [None, "input", "profile", "parameters", "output", "transport", "attempt", "binding"]
)
def test_import_checks_composite_boundary_before_store_writes(tmp_path, damage):
    configured = ComfyImageProfile(json.dumps(profile()).encode())
    adapter = ComfyImageAdapter("http://127.0.0.1:8771", configured)
    store = LocalArtifactStore(tmp_path / "store")

    def png(color):
        stream = io.BytesIO()
        Image.new("RGB", (2, 2), color).save(stream, format="PNG")
        return stream.getvalue()

    source = store.persist_bytes(
        png("red"),
        kind="rgb_image",
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png", "channel_layout": "RGB"},
    )
    context = NodeExecutionContext(
        "run",
        "transform",
        {"image": source},
        adapter.spec.normalize_parameters({}),
        store,
        input_digest="sha256:" + "1" * 64,
        attempt_id="run/transform/1",
        binding_digest="sha256:" + "2" * 64,
    )
    blob = store.get_manifest(source.artifact_id).identity.blob_digest
    filename = "asset-" + blob[7:] + ".png"
    receipt = {
        "artifact_id": source.artifact_id,
        "blob_digest": blob,
        "endpoint": profile()["endpoint"],
        "filename": filename,
        "subfolder": "assets-generator",
        "type": "input",
        "workflow_value": "assets-generator/" + filename,
        "verification": "exact-byte-readback@1",
    }
    bound = configured.workflow().bind(
        {"seed": 1}, images={"image": receipt}, endpoint=profile()["endpoint"]
    )
    image = png("blue")
    output_id = sha256_bytes(
        canonical_json_bytes(
            {
                "kind": "rgb_image",
                "schema_name": "png",
                "schema_version": "1.0",
                "blob_digest": sha256_bytes(image),
                "identity_metadata": {"media_type": "image/png", "channel_layout": "RGB"},
            }
        )
    )
    evidence = {
        "provenance_scope": "composite_boundary_only",
        "inputs": {"image": {"artifact_id": source.artifact_id}},
        "outputs": {"image": {"artifact_id": output_id}},
        "submission": {"submission_key": RemoteAttemptBinding.key_for("run", "transform", 1)},
        "output_mapping": profile()["output"],
        "workflow": bound,
        "deployment_claims": {
            "endpoint": profile()["endpoint"],
            "workflow": bound,
            "claims": {
                "profile_digest": configured.identity.backend_digest,
                "dag_input_digest": context.input_digest,
                "dag_binding_digest": context.binding_digest,
            },
        },
        "internal_verification": {
            "comfy_revision": "unverified",
            "custom_nodes": "unverified",
            "models": "unverified",
        },
    }
    if damage == "input":
        evidence["inputs"]["image"]["artifact_id"] = "other"
    if damage == "profile":
        evidence["deployment_claims"]["claims"]["profile_digest"] = "other"
    if damage == "parameters":
        evidence["workflow"]["parameters"]["seed"] = 2
    if damage == "output":
        evidence["outputs"]["image"]["artifact_id"] = "other"
    if damage == "binding":
        evidence["deployment_claims"]["claims"]["dag_binding_digest"] = "other"
    blobs = {"image": image, "evidence": canonical_json_bytes(evidence)}
    job = RemoteJob(
        RemoteAttemptBinding.key_for("run", "transform", 2 if damage == "attempt" else 1),
        "succeeded",
        canonical_json_bytes(
            {
                "outputs": [
                    {
                        "output_id": key,
                        "blob_digest": sha256_bytes(data),
                        "byte_length": len(data),
                        "media_type": "image/png" if key == "image" else "application/json",
                    }
                    for key, data in blobs.items()
                ]
            }
        ),
        None,
    )
    if damage == "transport":
        blobs["image"] = png("green")
    before = set(store.manifests_dir.rglob("*.json"))
    if damage:
        with pytest.raises(ContractError):
            adapter.import_result(context, job, blobs)
        assert set(store.manifests_dir.rglob("*.json")) == before
    else:
        result = adapter.import_result(context, job, blobs)
        assert result.outputs["image"].artifact_id == output_id
        assert store.read_structured(result.outputs["evidence"]) == evidence
        assert adapter.prepare_payload(context) == {
            "operation": "image_transform@1",
            "parameters": {"seed": 1},
        }
        assert adapter.input_blobs(context) == {"image": source}
        with pytest.raises(ContractError):
            adapter.input_blobs(replace(context, inputs={}))
