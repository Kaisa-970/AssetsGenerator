from contextlib import contextmanager

import pytest

from assets_generator import workbench_app


def test_startup_recovers_before_resume_and_closes_on_dispatch_failure(tmp_path, monkeypatch):
    events = []
    config = tmp_path / "config.json"
    config.write_text("{}")

    @contextmanager
    def repository(*args):
        events.append("lock")
        try:
            yield object()
        finally:
            events.append("unlock")

    class Service:
        def __init__(self, engine):
            pass

        def recover_runs(self):
            events.append("recover")

        def resume(self):
            events.append("resume")
            raise RuntimeError("cannot start worker")

        def close(self):
            events.append("service_close")

    class Server:
        def server_close(self):
            events.append("server_close")

    monkeypatch.setattr(workbench_app, "WorkbenchRepository", repository)
    monkeypatch.setattr(workbench_app, "WorkbenchEngine", lambda *args, **kwargs: object())
    monkeypatch.setattr(workbench_app, "LocalWorkbenchService", Service)
    monkeypatch.setattr(workbench_app, "create_workbench_server", lambda *args: Server())
    monkeypatch.setattr("assets_generator.workbench_profiles.load_profiles", lambda config: {})
    with pytest.raises(RuntimeError, match="cannot start worker"):
        workbench_app.serve_workbench(
            config_path=config, store_path=tmp_path / "store", directory=tmp_path / "workbench"
        )
    assert events == ["lock", "recover", "resume", "server_close", "service_close", "unlock"]
