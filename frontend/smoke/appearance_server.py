"""CPU mesh fixtures over real remote-shape and editor HTTP; no model inference.

Run with PYTHONPATH=src:tests and a new --root outside the repository.
"""

import argparse
import io
import json
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import trimesh
import yaml
from PIL import Image
from test_remote_service_http import serve
from test_remote_shape_service import Backend

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.dag_adapters import AdapterRegistry
from assets_generator.dag_asset_assembly import ShapeAssetAssemblyAdapter
from assets_generator.dag_asset_export import AssetExportAdapter
from assets_generator.dag_canonicalize import CanonicalizeAdapter
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_geometry_validation import GeometryValidationAdapter
from assets_generator.dag_persistence import DagRepository
from assets_generator.dag_remote_shape import RemoteShapeAdapter
from assets_generator.node_editor import DraftEditor, create_editor_server
from assets_generator.node_editor_execution import NodeEditorExecution
from assets_generator.remote_service_worker import execute_service_job
from assets_generator.remote_shape_service import ShapeServiceHandler


class AppearanceBackend(Backend):
    mode = "none"

    def generate(self, store, rgba, **parameters):
        original = super().generate(store, rgba, **parameters)
        mesh = trimesh.creation.box()
        if self.mode == "vertex":
            mesh.visual.vertex_colors = [255, 30, 20, 255]
        elif self.mode == "texture":
            mesh.visual = trimesh.visual.TextureVisuals(
                uv=np.zeros((len(mesh.vertices), 2)),
                image=Image.new("RGB", (2, 2), "red"),
            )
        metadata = store.get_manifest(original.mesh.artifact_id).identity.identity_metadata
        reference = store.persist_bytes(
            mesh.export(file_type="glb"),
            kind="triangle_mesh",
            schema_name="glTF",
            schema_version="2.0",
            identity_metadata=metadata,
        )
        return replace(
            original,
            mesh=reference,
            backend_metadata={
                "cpu_fixture": True,
                **(
                    {"postprocess_mode": "geometry_fallback_no_texture"}
                    if self.mode == "none"
                    else {}
                ),
            },
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=False)
    store = LocalArtifactStore(args.root / "store")
    data = io.BytesIO()
    Image.new("RGBA", (2, 2), "red").save(data, format="PNG")
    image = store.persist_bytes(
        data.getvalue(),
        kind="rgba_image",
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png", "channel_layout": "RGBA"},
    )
    with serve(args.root / "remote.sqlite") as (remote, client, _):
        registry = AdapterRegistry()
        for adapter in [
            RemoteShapeAdapter(client.endpoint, remote.identity),
            CanonicalizeAdapter(),
            GeometryValidationAdapter(),
            ShapeAssetAssemblyAdapter(),
            AssetExportAdapter(),
        ]:
            registry.register(adapter)
        with DagRepository(store, args.root / "runtime") as repo:
            service = NodeEditorExecution(DagEngine(repo, registry))
            graph = yaml.safe_load(Path("pipelines/remote_shape_asset_v1.yaml").read_text())
            runs = {}
            for mode in ["none", "vertex", "texture"]:
                AppearanceBackend.mode = mode
                created = service.start(graph, image_ref={"artifact_id": image.artifact_id})
                run_id = created["run"]["run_id"]
                deadline = time.monotonic() + 30
                while True:
                    state = repo.load(run_id).dag.node_states["shape"]
                    if state.attempts and state.current().remote_binding is not None:
                        attempt = state.current()
                        break
                    assert time.monotonic() < deadline
                    time.sleep(0.05)
                request = remote.request_for(attempt.remote_binding.submission_key)
                while request is None:
                    assert time.monotonic() < deadline
                    time.sleep(0.05)
                    request = remote.request_for(attempt.remote_binding.submission_key)
                handler = ShapeServiceHandler(
                    remote.identity,
                    args.root / "remote-work",
                    AppearanceBackend,
                    lambda: remote.identity,
                )
                assert execute_service_job(remote, request, handler).state == "succeeded"
                service._worker.join(30)
                assert not service._worker.is_alive()
                run = repo.load(run_id)
                assert run.status == "succeeded", run
                runs[mode] = run_id
            server = create_editor_server(
                DraftEditor(
                    args.root / "drafts",
                    execution=service,
                    templates=[Path("pipelines/remote_shape_asset_v1.yaml")],
                    execution_profile="CPU appearance fixture; no model",
                ),
                0,
            )
            config = {
                "root": str(args.root.resolve()),
                "url": f"http://127.0.0.1:{server.server_port}",
                "runs": runs,
                "cpu_fixture": True,
            }
            (args.root / "browser-config.json").write_text(json.dumps(config, indent=2))
            print(json.dumps(config), flush=True)
            try:
                server.serve_forever()
            finally:
                server.server_close()
                service.close()


if __name__ == "__main__":
    main()
