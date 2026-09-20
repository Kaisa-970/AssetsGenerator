import json
from pathlib import Path

from test_dag_image_adapters import fixture_engine

from assets_generator.cli import _execute, _parser


def main(argv):
    parser = _parser()
    return _execute(parser, parser.parse_args(argv))


def test_cli_yaml_start_resume_decide(tmp_path, monkeypatch, capsys):
    (tmp_path / "fixture").mkdir()
    _, _, profile = fixture_engine(tmp_path / "fixture")
    monkeypatch.setattr("assets_generator.dag_cli.load_profiles", lambda config: {"cpu": profile})
    config = tmp_path / "config.json"
    config.write_text("{}")
    from PIL import Image

    photo = tmp_path / "photo.png"
    Image.new("RGB", (8, 6), "red").save(photo)
    common = [
        "--config",
        str(config),
        "--profile",
        "cpu",
        "--store",
        str(tmp_path / "store"),
        "--directory",
        str(tmp_path / "dag"),
    ]
    assert (
        main(
            [
                "dag-image",
                "start",
                *common,
                "--pipeline",
                str(Path("examples/dag-image-asset.yaml")),
                "--image",
                str(photo),
            ]
        )
        == 0
    )
    run = json.loads(capsys.readouterr().out)
    assert run["status"] == "waiting_for_input"
    assert main(["dag-image", "resume", *common, "--run", run["run_id"]]) == 0
    resumed = json.loads(capsys.readouterr().out)
    assert len(resumed["dag"]["node_states"]["candidates"]["attempts"]) == 1
    decision = tmp_path / "decision.json"
    decision.write_text(json.dumps({"proposal_id": "p0", "invert": False, "keep_largest": True}))
    assert (
        main(
            [
                "dag-image",
                "decide",
                *common,
                "--run",
                run["run_id"],
                "--node",
                "choose_object",
                "--expected-revision",
                str(resumed["dag"]["revision"]),
                "--key",
                "test",
                "--reviewer",
                "CPU fixture",
                "--decision",
                str(decision),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "succeeded"
