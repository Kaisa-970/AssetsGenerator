import io
import json
import threading
from http.client import HTTPConnection

import pytest
from PIL import Image

from assets_generator.workbench_http import (
    OutputPayload,
    create_workbench_server,
    normalize_upload,
)


class Service:
    def __init__(self):
        self.commands = []
        self.images = []

    def catalog(self):
        return {"backends": [{"id": "fake", "label": "Test backend"}]}

    def import_image(self, data):
        self.images.append(data)
        return {"image": {"artifact_id": "test"}}

    def list_runs(self):
        return [{"run_id": "run_one", "status": "waiting_for_input"}]

    def create_run(self, body):
        self.commands.append(body)
        return self.get_run("run_one")

    def get_run(self, run_id):
        if run_id != "run_one":
            raise KeyError(run_id)
        return {"run_id": run_id, "revision": 2}

    def command(self, run_id, action, body):
        if body.get("expected_revision") != 2:
            raise RuntimeError("stale revision")
        self.commands.append((run_id, action, body))
        return self.get_run(run_id)

    def output(self, run_id, key):
        if (run_id, key) != ("run_one", "image"):
            raise KeyError(key)
        return OutputPayload(b"image", "image/png")


@pytest.fixture
def server():
    service = Service()
    server = create_workbench_server(service, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    token = json.loads(request("GET", "/session")[1])["token"]
    headers = {
        "Origin": f"http://127.0.0.1:{server.server_port}",
        "X-Workbench-Token": token,
        "Content-Type": "application/json",
    }
    try:
        yield service, server, request, headers
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_loopback_host_origin_and_session_are_required(server):
    service, http, request, headers = server
    assert http.server_address[0] == "127.0.0.1"
    assert request("GET", "/")[0] == 200
    assert request("GET", "/session", headers={"Host": f"localhost:{http.server_port}"})[0] == 200
    assert request("GET", "/session", headers={"Host": "evil.example"})[0] == 403
    for replacement in ({"Origin": "https://evil.example"}, {"X-Workbench-Token": "wrong"}):
        assert request("POST", "/runs", "{}", {**headers, **replacement})[0] == 403
    assert not service.commands
    assert request("POST", "/runs", "{}", headers)[0] == 202
    assert len(service.commands) == 1


def test_routes_cannot_read_arbitrary_paths_and_stale_commands_conflict(server):
    service, _, request, headers = server
    assert request("GET", "/runs/run_one/outputs/image") == (200, b"image")
    for path in ("/etc/passwd", "/runs/../outputs/image", "/runs/run_one/outputs/%2e%2e"):
        assert request("GET", path)[0] == 404
    assert request("GET", "/runs/run_missing")[0] == 404
    assert request("POST", "/runs/run_one/decision", '{"expected_revision":1}', headers)[0] == 409
    assert not service.commands
    assert request("POST", "/runs/run_one/decision", '{"expected_revision":2}', headers)[0] == 202
    assert len(service.commands) == 1


def test_upload_is_decoded_and_normalized_before_import(server):
    service, _, request, headers = server
    stream = io.BytesIO()
    Image.new("L", (5, 7), 200).save(stream, "PNG")
    assert request("POST", "/inputs", stream.getvalue(), headers)[0] == 201
    with Image.open(io.BytesIO(service.images[0])) as result:
        assert result.mode == "RGB"
        assert result.size == (5, 7)
    assert request("POST", "/inputs", b"not an image", headers)[0] == 400
    assert len(service.images) == 1
    assert request("POST", "/runs", "[]", headers)[0] == 400
    assert request("POST", "/runs", "{}", {**headers, "Content-Length": "999999"})[0] == 400


def test_upload_limits_pixels_before_loading(monkeypatch):
    stream = io.BytesIO()
    Image.new("RGB", (5, 7)).save(stream, "PNG")
    monkeypatch.setattr("assets_generator.workbench_http.MAX_IMAGE_PIXELS", 20)
    with pytest.raises(ValueError, match="pixels"):
        normalize_upload(stream.getvalue())


def test_upload_applies_camera_orientation_and_removes_metadata():
    stream = io.BytesIO()
    source = Image.new("RGB", (5, 7))
    exif = Image.Exif()
    exif[274] = 6
    source.save(stream, "JPEG", exif=exif)
    with Image.open(io.BytesIO(normalize_upload(stream.getvalue()))) as result:
        assert result.size == (7, 5)
        assert not result.getexif()
