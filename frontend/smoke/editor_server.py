"""CPU-only real HTTP/UI smoke server; run from repository root with tests on PYTHONPATH."""

import argparse
import json
from contextlib import ExitStack
from pathlib import Path

from test_dag_image_adapters import image_plan
from test_workbench_engine import fixture_engine

from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.node_editor import DraftEditor, create_editor_server
from assets_generator.node_editor_execution import NodeEditorExecution

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--remote-submit", action="store_true")
args = parser.parse_args()
args.root.mkdir(parents=True, exist_ok=True)
store, _, profile = fixture_engine(args.root)
registry, _ = image_plan(profile)
with ExitStack() as stack:
    if args.remote_submit:
        from test_remote_service_http import serve

        from assets_generator.dag_remote_profiles import register_remote_shape_profiles

        remote, client, _ = stack.enter_context(serve(args.root / "remote.sqlite"))
        register_remote_shape_profiles(
            registry,
            {
                "default_profile": "cpu",
                "profiles": {
                    "cpu": {
                        "endpoint": client.endpoint,
                        "service_id": remote.identity.service_id,
                        "backend_digest": remote.identity.backend_digest,
                    }
                },
            },
        )
    repo = stack.enter_context(DagRepository(store, args.root / "runtime"))
    service = NodeEditorExecution(DagEngine(repo, registry))
    editor = DraftEditor(
        args.root / "drafts",
        templates=[
            Path(
                "pipelines/remote_selected_image_asset_v1.yaml"
                if args.remote_submit
                else "examples/dag-image-asset.yaml"
            )
        ],
        execution=service,
        execution_profile="fake-cpu-validation",
    )
    server = create_editor_server(editor, 0)
    (args.root / "browser-config.json").write_text(
        json.dumps(
            {
                "template": "remote_selected_image_asset_v1"
                if args.remote_submit
                else "dag-image-asset",
                "url": f"http://127.0.0.1:{server.server_port}",
                "image": str((args.root / "fixture/scene.png").absolute()),
                "root": str(args.root.absolute()),
            }
        )
    )
    print(f"http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.close()
