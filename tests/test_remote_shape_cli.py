import json

import pytest
from test_remote_http import request

from assets_generator import remote_shape_cli as cli
from assets_generator.remote_service_store import RemoteServiceStore
from assets_generator.remote_service_worker import ServiceOutput


class Handler:
    identity = request().identity

    def __call__(self, req, store):
        return {"result": ServiceOutput(b"test", "application/octet-stream")}


def test_explicit_execute_and_inspect(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.json"
    config.write_text('{"profiles":{}}')
    monkeypatch.setattr(cli, "load_shape_profiles", lambda *_args, **_kwargs: {"test": object()})
    monkeypatch.setattr(cli, "shape_handler_from_profile", lambda *_args, **_kwargs: Handler())
    database = tmp_path / "service.sqlite"
    store = RemoteServiceStore(database, request().identity)
    store.submit(request())
    store.close()
    options = [
        "--config",
        str(config),
        "--profile",
        "test",
        "--service-id",
        "test",
        "--database",
        str(database),
        "--workspace",
        str(tmp_path / "work"),
        "--job",
        "same-key",
    ]
    assert cli.main(["inspect", *options]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "queued"
    assert cli.main(["execute", *options]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "succeeded"
    assert cli.main(["execute", *options]) == 1
    assert "state conflict" in capsys.readouterr().err
    assert cli.main(["inspect", *options]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "succeeded"


def test_execute_requires_job_before_loading_resources(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(
            [
                "execute",
                "--config",
                "missing",
                "--profile",
                "test",
                "--service-id",
                "test",
                "--database",
                str(tmp_path / "db"),
                "--workspace",
                str(tmp_path),
            ]
        )
