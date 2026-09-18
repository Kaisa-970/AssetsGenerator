"""Service lifetime and CLI composition for the local workbench."""

from __future__ import annotations

import json
from pathlib import Path

from .artifact_store import LocalArtifactStore
from .workbench_engine import WorkbenchEngine
from .workbench_http import create_workbench_server
from .workbench_persistence import WorkbenchRepository
from .workbench_process import LinuxProcessProbe
from .workbench_service import LocalWorkbenchService


def serve_workbench(
    *, config_path: Path, store_path: Path, directory: Path, port: int = 8765
) -> None:
    from .workbench_profiles import load_profiles

    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    config = json.loads(config_path.expanduser().read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("workbench configuration must be a JSON object")
    store = LocalArtifactStore(store_path.expanduser().absolute())
    with WorkbenchRepository(store, directory.expanduser().absolute()) as repository:
        profiles = load_profiles(config)
        engine = WorkbenchEngine(repository, profiles, probe=LinuxProcessProbe())
        service = LocalWorkbenchService(engine)
        service.recover_runs()
        server = create_workbench_server(service, port)
        try:
            # Recovery can queue Core work; it never implicitly retries interrupted inference.
            service.resume()
            print(f"http://127.0.0.1:{server.server_port}/ (Ctrl+C 关闭)", flush=True)
            server.serve_forever()
        except KeyboardInterrupt:
            print("正在关闭工作台，等待已开始的任务保存结果…", flush=True)
        finally:
            server.server_close()
            service.close()
