"""CPU browser smoke with real editor/gateway and injected upstream Comfy transport."""

import argparse
import io
import json
import threading
from pathlib import Path

import pytest
from PIL import Image
from test_comfy_profile import profile
from test_remote_service_http import serve

import assets_generator.comfy_upload as upload_module
from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.comfy_executor import execute_next_image
from assets_generator.comfy_http import ComfyClient
from assets_generator.comfy_profile import ComfyImageProfile
from assets_generator.dag_adapters import AdapterRegistry
from assets_generator.dag_comfy_image import ComfyImageAdapter
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_image_encoding import EncodePngAdapter
from assets_generator.dag_persistence import DagRepository
from assets_generator.node_editor import DraftEditor, create_editor_server
from assets_generator.node_editor_execution import NodeEditorExecution

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
args = parser.parse_args()
args.root.mkdir(parents=True, exist_ok=True)
Image.new("RGB", (8, 8), "red").save(args.root / "input.png")
configured = ComfyImageProfile(json.dumps(profile()).encode())
with pytest.MonkeyPatch.context() as monkeypatch:
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
    with serve(args.root / "service.sqlite", identity=configured.identity) as (service, client, _):
        registry = AdapterRegistry()
        registry.register(EncodePngAdapter())
        adapter = ComfyImageAdapter(client.endpoint, configured)
        registry.register(adapter)
        for name in ("comfy_first", "comfy_second"):
            registry.register_backend(name, adapter)
        with DagRepository(
            LocalArtifactStore(args.root / "core-store"), args.root / "runtime"
        ) as repo:
            execution = NodeEditorExecution(DagEngine(repo, registry))
            editor = DraftEditor(
                args.root / "drafts",
                templates=[Path("pipelines/comfy_image_chain_v1.yaml")],
                execution=execution,
            )
            server = create_editor_server(editor, 0)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            (args.root / "browser-config.json").write_text(
                json.dumps(
                    {
                        "url": f"http://127.0.0.1:{server.server_port}",
                        "root": str(args.root.absolute()),
                        "image": str((args.root / "input.png").absolute()),
                    }
                )
            )
            print("ready", flush=True)
            try:
                for line in __import__("sys").stdin:
                    if line.strip() == "quit":
                        break
                    if line.strip() == "execute":
                        job = execute_next_image(
                            configured,
                            service,
                            args.root / "journal.sqlite",
                            LocalArtifactStore(args.root / "service-store"),
                        )
                        print(
                            json.dumps(
                                {"state": job.state if job else None, "prompts": len(prompts)}
                            ),
                            flush=True,
                        )
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                execution.close()
