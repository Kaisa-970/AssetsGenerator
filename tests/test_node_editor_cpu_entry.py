"""The installed CLI CPU path needs no model configuration or environment."""

import io
import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

import pytest
from PIL import Image

from assets_generator import node_editor


@pytest.mark.parametrize(
    "option", ["config", "proposal_config", "multi_view_config", "remote_config", "comfy_config"]
)
def test_model_configuration_without_store_fails_before_read(tmp_path, option):
    with pytest.raises(ValueError, match="requires --store"):
        node_editor.serve_editor(tmp_path / "editor", 0, None, [], **{option: tmp_path / "missing"})


def test_explicit_templates_preserved_and_static_default(tmp_path, monkeypatch):
    seen = []

    class Server:
        server_port = 0

        def serve_forever(self):
            pass

        def server_close(self):
            pass

    def create(editor, port):
        seen.append(editor)
        return Server()

    monkeypatch.setattr(node_editor, "create_editor_server", create)
    supplied = Path("examples/cpu-image-resize.yaml")
    node_editor.serve_editor(tmp_path / "first", 0, None, [supplied], store=tmp_path / "store")
    assert [item["id"] for item in seen[0].templates] == [supplied.stem]
    assert seen[0].execution_profile == "CPU · 未配置模型"
    node_editor.serve_editor(tmp_path / "second", 0, None, [])
    assert seen[1].execution is None
    assert [item["id"] for item in seen[1].templates] == ["cpu-image-editor"]


def test_cli_cpu_upload_execute_and_read_output(tmp_path):
    environment = {**os.environ, "PYTHONPATH": str(Path("src").absolute())}
    with (tmp_path / "stderr.log").open("w+") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "assets_generator.cli",
                "node-editor",
                "--directory",
                str(tmp_path / "editor"),
                "--store",
                str(tmp_path / "store"),
                "--port",
                "0",
            ],
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
            env=environment,
        )
        try:
            assert process.stdout is not None
            assert select.select([process.stdout], [], [], 30)[0], "CLI did not start"
            line = process.stdout.readline()
            assert line.startswith("http://127.0.0.1:"), line
            root = line.split()[0].rstrip("/")

            def request(path, value=None, data=None):
                payload = json.dumps(value).encode() if value is not None else data
                headers = {
                    "Content-Type": "application/json"
                    if value is not None
                    else "application/octet-stream"
                }
                with urlopen(
                    Request(root + path, data=payload, headers=headers), timeout=30
                ) as response:
                    return response.read()

            catalog = json.loads(request("/api/catalog"))
            assert catalog["execution_enabled"]
            assert catalog["execution_profile"] == "CPU · 未配置模型"
            assert {item["name"] for item in catalog["adapters"]} == {
                "encode_png",
                "resize_image",
                "apply_binary_mask",
            }
            assert catalog["backends"] == []
            assert len(catalog["templates"]) == 1
            graph = catalog["templates"][0]["pipeline"]
            compiled = json.loads(request("/api/compile", {"pipeline": graph}))
            assert compiled["ok"] and compiled["execution_ready"]
            buffer = io.BytesIO()
            Image.new("RGB", (32, 24), (15, 60, 125)).save(buffer, format="PNG")
            uploaded = json.loads(request("/api/inputs/image", data=buffer.getvalue()))
            assert json.loads(request("/api/runs"))["runs"] == []
            started = json.loads(
                request("/api/runs", {"pipeline": graph, "image_ref": uploaded["image_ref"]})
            )
            run_id = started["run"]["run_id"]
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                result = json.loads(request("/api/runs/" + run_id))
                if result["run"]["status"] == "succeeded":
                    break
                assert result["run"]["status"] not in {
                    "failed",
                    "interrupted",
                    "recovery_blocked",
                }, result
                time.sleep(0.05)
            else:
                pytest.fail("CPU run did not finish")
            assert len(result["run"]["dag"]["node_states"]["encode"]["attempts"]) == 1
            output = request(result["outputs"][0]["url"])
            with Image.open(io.BytesIO(output)) as image:
                assert image.size == (32, 24)
                assert image.getpixel((0, 0)) == (15, 60, 125)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
