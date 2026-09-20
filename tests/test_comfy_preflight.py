import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_comfy_profile import profile

from assets_generator.cli import _execute, _parser
from assets_generator.comfy_preflight import preflight
from assets_generator.comfy_profile import ComfyImageProfile


@pytest.mark.parametrize("failure", [None, "missing", "output", "transport"])
def test_preflight_uses_only_get_and_reports_limited_evidence(tmp_path, capsys, failure):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.command, self.path))
            name = self.path.rsplit("/", 1)[-1]
            value = {name: {"output_node": name == "SaveImage" and failure != "output"}}
            if failure == "missing" and name == "Fixture":
                value = {}
            body = json.dumps(value).encode()
            self.send_response(503 if failure == "transport" else 200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        raw = profile()
        raw["endpoint"] = f"http://127.0.0.1:{server.server_port}"
        path = tmp_path / "profile.json"
        path.write_text(json.dumps(raw))
        parser = _parser()
        assert _execute(
            parser, parser.parse_args(["comfy-image", "preflight", "--profile", str(path)])
        ) == (0 if failure is None else 1)
        result = json.loads(capsys.readouterr().out)
        assert result["ok"] == (failure is None)
        assert result["deployment_verified"] is False
        assert result["inference_submitted"] is False
        assert result["unchecked"]
        assert requests == [
            ("GET", "/object_info/Fixture"),
            ("GET", "/object_info/LoadImage"),
            ("GET", "/object_info/SaveImage"),
        ]
        assert list(tmp_path.iterdir()) == [path]
        if failure is None:
            assert preflight(ComfyImageProfile.load(path)) == result
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
