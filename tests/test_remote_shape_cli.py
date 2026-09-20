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
    assert cli.main(["list", *options[:-2], "--limit", "1"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "jobs": [{"job_id": "same-key", "state": "queued", "error": None}],
        "next_before": None,
    }
    assert cli.main(["inspect", *options]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "queued"
    assert cli.main(["execute", *options]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "succeeded"
    assert cli.main(["abandon-exited", *options]) == 1
    assert "only running" in capsys.readouterr().err
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


def test_drain_loads_once_and_stops_at_limit(tmp_path, monkeypatch, capsys):
    from assets_generator.remote_protocol import RemoteRequest

    config = tmp_path / "config.json"
    config.write_text("{}")
    loads = []

    def load(*args, **kwargs):
        loads.append(True)
        return {"test": object()}

    monkeypatch.setattr(cli, "load_shape_profiles", load)
    monkeypatch.setattr(cli, "shape_handler_from_profile", lambda *args, **kwargs: Handler())
    path = tmp_path / "db"
    store = RemoteServiceStore(path, Handler.identity)
    for key in ("a", "b", "c"):
        store.submit(RemoteRequest.create(Handler.identity, key, {}))
    try:
        assert (
            cli.main(
                [
                    "drain",
                    "--config",
                    str(config),
                    "--profile",
                    "test",
                    "--service-id",
                    "test",
                    "--database",
                    str(path),
                    "--workspace",
                    str(tmp_path),
                    "--max-jobs",
                    "2",
                ]
            )
            == 0
        )
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert [row["job_id"] for row in rows] == ["a", "b"]
        assert loads == [True]
        assert store.lookup(RemoteRequest.create(Handler.identity, "c", {})).state == "queued"
    finally:
        store.close()
