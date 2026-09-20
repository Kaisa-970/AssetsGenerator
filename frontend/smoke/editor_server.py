"""CPU-only real HTTP/UI smoke server; run from repository root with tests on PYTHONPATH."""

import argparse
import json
from pathlib import Path

from test_dag_image_adapters import image_plan
from test_workbench_engine import fixture_engine

from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.node_editor import DraftEditor, create_editor_server
from assets_generator.node_editor_execution import NodeEditorExecution

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
args = parser.parse_args()
args.root.mkdir(parents=True, exist_ok=True)
store, _, profile = fixture_engine(args.root)
registry, _ = image_plan(profile)
with DagRepository(store, args.root / "runtime") as repo:
    service = NodeEditorExecution(DagEngine(repo, registry))
    editor = DraftEditor(
        args.root / "drafts",
        templates=[Path("examples/dag-image-asset.yaml")],
        execution=service,
        execution_profile="fake-cpu-validation",
    )
    server = create_editor_server(editor, 0)
    (args.root / "browser-config.json").write_text(
        json.dumps(
            {
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
