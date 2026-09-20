import json
from unittest.mock import Mock

import pytest
from test_comfy_profile import profile

from assets_generator.cli import _execute, _parser
from assets_generator.comfy_profile import ComfyImageProfile
from assets_generator.remote_service_store import RemoteServiceStore


def test_service_commands_separate_listener_and_executor(tmp_path, monkeypatch, capsys):
    import assets_generator.comfy_service_cli as module

    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile()))
    directory = tmp_path / "jobs"
    parser = _parser()

    def args(action):
        return parser.parse_args(
            [
                "comfy-service",
                action,
                "--profile",
                str(path),
                "--directory",
                str(directory),
                "--store",
                str(tmp_path / "store"),
            ]
        )

    listener = Mock(server_port=8771)
    listener.serve_forever.side_effect = KeyboardInterrupt
    monkeypatch.setattr(module, "create_remote_server", lambda *a, **kw: listener)
    calls = []
    monkeypatch.setattr(module, "execute_next_image", lambda *a: calls.append("execute"))
    assert _execute(parser, args("serve")) == 0
    listener.server_close.assert_called_once()
    assert calls == []
    capsys.readouterr()
    assert _execute(parser, args("list")) == 0
    assert json.loads(capsys.readouterr().out)["jobs"] == []
    assert _execute(parser, args("execute-next")) == 0
    assert calls == ["execute"]
    with pytest.raises(ValueError, match="--key"):
        _execute(parser, args("recover"))
    owner = RemoteServiceStore(directory / "service.sqlite", ComfyImageProfile.load(path).identity)
    owner.close()


def test_service_nonserve_missing_database_is_not_recreated(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile()))
    directory = tmp_path / "missing"
    parser = _parser()
    args = parser.parse_args(
        [
            "comfy-service",
            "execute-next",
            "--profile",
            str(path),
            "--directory",
            str(directory),
            "--store",
            str(tmp_path / "store"),
        ]
    )
    with pytest.raises(ValueError, match="refusing to recreate"):
        _execute(parser, args)
    assert not directory.exists()
