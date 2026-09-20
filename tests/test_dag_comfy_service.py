"""Real Core/service HTTP with injected Comfy transport, no model inference."""

import io
import json
from pathlib import Path

from PIL import Image
from test_comfy_profile import profile
from test_remote_service_http import serve

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.comfy_executor import execute_next_image
from assets_generator.comfy_http import ComfyClient
from assets_generator.comfy_profile import ComfyImageProfile
from assets_generator.dag_adapters import AdapterRegistry
from assets_generator.dag_comfy_image import ComfyImageAdapter
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.pipeline import compile_pipeline, load_default_operator_specs, load_pipeline


def test_two_comfy_nodes_execute_and_recover_with_original_artifacts(tmp_path, monkeypatch):
    import assets_generator.comfy_upload as upload_module

    configured = ComfyImageProfile(json.dumps(profile()).encode())
    core = LocalArtifactStore(tmp_path / "core-store")
    service_store = LocalArtifactStore(tmp_path / "service-store")
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(stream, format="PNG")
    image = core.persist_bytes(
        stream.getvalue(),
        kind="rgb_image",
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png", "channel_layout": "RGB"},
    )
    prompts, uploads = {}, []

    def upload(client, store, ref):
        uploads.append(ref)
        blob = store.get_manifest(ref.artifact_id).identity.blob_digest
        name = "asset-" + blob[7:] + ".png"
        return {
            "artifact_id": ref.artifact_id,
            "blob_digest": blob,
            "endpoint": client.endpoint,
            "filename": name,
            "subfolder": "assets-generator",
            "type": "input",
            "workflow_value": "assets-generator/" + name,
            "verification": "exact-byte-readback@1",
        }

    def transport(self, path, body=None):
        if body is not None:
            assert body["prompt_id"] not in prompts
            prompts[body["prompt_id"]] = body["prompt"]
            return {"prompt_id": body["prompt_id"]}
        pid = path.rsplit("/", 1)[1]
        return {
            pid: {
                "prompt": [0, pid, prompts[pid]],
                "status": {"status_str": "success", "completed": True, "messages": []},
                "outputs": {
                    "3": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}
                },
            }
        }

    def download(self, *args, **kwargs):
        data = io.BytesIO()
        Image.new("RGB", (2, 2), "blue" if len(prompts) == 1 else "green").save(data, format="PNG")
        return data.getvalue()

    monkeypatch.setattr(upload_module, "upload_image", upload)
    monkeypatch.setattr(ComfyClient, "_json", transport)
    monkeypatch.setattr(ComfyClient, "download_image", download)
    with serve(tmp_path / "service.sqlite", identity=configured.identity) as (service, client, _):
        registry = AdapterRegistry()
        adapter = ComfyImageAdapter(client.endpoint, configured)
        registry.register(adapter)
        registry.register_backend("comfy_first", adapter)
        registry.register_backend("comfy_second", adapter)
        plan = registry.bind_plan(
            compile_pipeline(
                load_pipeline(Path("pipelines/comfy_image_chain_v1.yaml")),
                load_default_operator_specs(),
                require_explicit_joins=True,
            )
        )
        with DagRepository(core, tmp_path / "core") as repo:
            engine = DagEngine(repo, registry)
            run = engine.drain(engine.create(plan, {"image": image}).run_id)
            for node in ("first", "second"):
                assert run.dag.node_states[node].status == "running"
                job = execute_next_image(
                    configured, service, tmp_path / "journal.sqlite", service_store
                )
                assert job.state == "succeeded"
                run = engine.drain(run.run_id)
            assert run.status == "succeeded", run
            first = run.dag.node_states["first"].current()
            second = run.dag.node_states["second"].current()
            assert uploads == [image, first.outputs["image"]]
            assert second.resolved_inputs["image"] == first.outputs["image"]
            assert len(prompts) == 2
            for state in run.dag.node_states.values():
                assert len(state.attempts) == 1
                repo.verify_reference_closure(state.current().outputs["evidence"])
            restored = engine.drain(run.run_id)
            assert restored.status == "succeeded"
            assert len(prompts) == 2
            assert all(len(state.attempts) == 1 for state in restored.dag.node_states.values())
        with DagRepository(core, tmp_path / "core") as reopened:
            engine = DagEngine(reopened, registry)
            restored = engine.drain(run.run_id)
            assert restored.status == "succeeded"
            assert len(prompts) == len(uploads) == 2
            for state in restored.dag.node_states.values():
                assert len(state.attempts) == 1
                reopened.verify_reference_closure(state.current().outputs["evidence"])
