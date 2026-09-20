import json

import pytest
from test_comfy_profile import profile

from assets_generator.cli import _execute, _parser
from assets_generator.comfy_profile import ComfyImageProfile
from assets_generator.comfy_submission import ComfySubmissionUnknown
from assets_generator.remote_service_store import RemoteServiceStore


def test_cli_validate_start_uncertain_resume_and_status(tmp_path, monkeypatch, capsys):
    profile_path = tmp_path / "profile.json"
    raw = profile()
    raw["image_targets"] = {}
    profile_path.write_text(json.dumps(raw))
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps({"images": {}, "parameters": {}}))
    directory = tmp_path / "jobs"
    store_path = tmp_path / "store"
    parser = _parser()
    calls = []

    def start(*args):
        calls.append("start")
        raise ComfySubmissionUnknown("ack lost")

    def finish(*args):
        calls.append("finish")
        owner, req = args[1:3]
        return owner.transition(
            req,
            expected="running",
            state="failed",
            error={"code": "FIXTURE_FAILURE", "detail": "fixture only"},
        )

    monkeypatch.setattr(ComfyImageProfile, "start", start)
    monkeypatch.setattr(ComfyImageProfile, "finish", finish)

    def run(action, extra=()):
        args = parser.parse_args(
            [
                "comfy-image",
                action,
                "--profile",
                str(profile_path),
                "--directory",
                str(directory),
                "--store",
                str(store_path),
                "--key",
                "one",
                *extra,
            ]
        )
        return _execute(parser, args)

    assert run("validate") == 0
    assert not directory.exists()
    assert not json.loads(capsys.readouterr().out)["deployment_verified"]
    assert run("start", ("--request", str(request_path))) == 3
    assert json.loads(capsys.readouterr().out)["state"] == "uncertain"
    assert run("status") == 0
    capsys.readouterr()
    assert calls == ["start"]
    with pytest.raises(ValueError, match="state conflict"):
        run("start", ("--request", str(request_path)))
    assert run("resume") == 0
    assert calls == ["start", "finish"]
    owner = RemoteServiceStore(
        directory / "service.sqlite", ComfyImageProfile.load(profile_path).identity
    )
    try:
        assert owner.lookup(owner.request_for("one")).state == "failed"
    finally:
        owner.close()


def test_cli_resume_missing_database_does_not_recreate(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile()))
    parser = _parser()
    directory = tmp_path / "missing"
    args = parser.parse_args(
        [
            "comfy-image",
            "resume",
            "--profile",
            str(path),
            "--directory",
            str(directory),
            "--key",
            "one",
        ]
    )
    with pytest.raises(ValueError, match="refusing to recreate"):
        _execute(parser, args)
    assert not directory.exists()
