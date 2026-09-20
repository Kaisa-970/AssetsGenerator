"""Serve an executable image graph without model environments or downloads."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from pathlib import Path

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.dag_adapters import AdapterRegistry
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_image_encoding import EncodePngAdapter
from assets_generator.dag_persistence import DagRepository
from assets_generator.node_editor import DraftEditor, create_editor_server
from assets_generator.node_editor_execution import NodeEditorExecution


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    registry = AdapterRegistry()
    registry.register(EncodePngAdapter())
    with ExitStack() as stack:
        repository = stack.enter_context(
            DagRepository(LocalArtifactStore(args.directory / "store"), args.directory / "runtime")
        )
        execution = NodeEditorExecution(DagEngine(repository, registry))
        stack.callback(execution.close)
        editor = DraftEditor(
            args.directory / "drafts",
            templates=[Path(__file__).with_name("cpu-image-editor.yaml")],
            execution=execution,
        )
        server = create_editor_server(editor, args.port)
        stack.callback(server.server_close)
        print(f"http://127.0.0.1:{server.server_port}/ (Ctrl+C 关闭)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
