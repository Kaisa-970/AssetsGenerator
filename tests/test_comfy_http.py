import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from assets_generator.comfy_http import ComfyClient
from assets_generator.comfy_submission import ComfySubmissionJournal, ComfySubmissionUnknown


@pytest.mark.parametrize("mode", ["ok", "lost", "redirect", "oversized", "duplicate_json"])
def test_comfy_submission_uses_one_http_post_across_restart(tmp_path, mode):
    posts, queries = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            posts.append((self.path, body))
            if mode == "lost":
                self.close_connection = True
                return
            if mode == "redirect":
                self.send_response(307)
                self.send_header("Location", "/prompt-again")
                self.end_headers()
                return
            if mode == "oversized":
                data = b" " * 2048
            elif mode == "duplicate_json":
                data = b'{"prompt_id":"a","prompt_id":"b"}'
            else:
                data = json.dumps({"prompt_id": body["prompt_id"], "node_errors": {}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            queries.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")  # Missing history is uncertainty, never permission to repost.

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    path = tmp_path / "journal.sqlite"
    journal = ComfySubmissionJournal(path)
    client = ComfyClient(f"http://127.0.0.1:{server.server_port}", max_response_bytes=1024)
    try:
        record = journal.prepare(
            "one",
            deployment={"endpoint": client.endpoint, "revision": "fixture"},
            prompt={"1": {"class_type": "Fixture", "inputs": {}}},
        )
        if mode == "ok":
            assert client.submit(journal, "one")["phase"] == "acknowledged"
        else:
            with pytest.raises(ComfySubmissionUnknown):
                client.submit(journal, "one")
        journal.close()
        journal = ComfySubmissionJournal(path)
        assert client.history(journal, "one") == {}
        if mode == "ok":
            assert client.submit(journal, "one")["phase"] == "acknowledged"
        else:
            with pytest.raises(ComfySubmissionUnknown):
                client.submit(journal, "one")
        assert len(posts) == 1
        assert posts[0][0] == "/prompt"
        assert posts[0][1]["prompt_id"] == record["prompt_id"]
        assert queries == ["/history/" + record["prompt_id"]]
        with pytest.raises(ComfySubmissionUnknown, match="history missing"):
            client.observe(journal, "one")
        assert len(posts) == 1
        with pytest.raises(ValueError, match="endpoint differs"):
            ComfyClient("http://127.0.0.1:1").submit(journal, "one")
    finally:
        journal.close()
        server.shutdown()
        server.server_close()
        thread.join()


def test_fixed_image_download_uses_exact_descriptor_and_validates_bytes(tmp_path):
    import io
    from urllib.parse import parse_qs, urlsplit

    from PIL import Image

    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(stream, format="PNG")
    data = stream.getvalue()
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            paths.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    journal = ComfySubmissionJournal(tmp_path / "journal.sqlite")
    try:
        client = ComfyClient(f"http://127.0.0.1:{server.server_port}")
        prompt = {"1": {"class_type": "Fixture", "inputs": {}}}
        record = journal.prepare("one", deployment={"endpoint": client.endpoint}, prompt=prompt)
        journal.submit_once("one", lambda body: {"prompt_id": body["prompt_id"]})
        journal.record_history(
            "one",
            {
                record["prompt_id"]: {
                    "prompt": [0, record["prompt_id"], prompt],
                    "outputs": {
                        "1": {
                            "images": [
                                {"filename": "a & b.png", "subfolder": "job", "type": "output"}
                            ]
                        }
                    },
                    "status": {"status_str": "success", "completed": True, "messages": []},
                }
            },
        )
        assert client.download_image(journal, "one", node="1", index=0, mode="RGB") == data
        assert parse_qs(urlsplit(paths[0]).query)["filename"] == ["a & b.png"]
        assert urlsplit(paths[0]).path == "/view"
        with pytest.raises(ComfySubmissionUnknown):
            client.download_image(journal, "one", node="1", index=0, mode="RGBA")
        with pytest.raises(ComfySubmissionUnknown):
            client.download_image(journal, "one", node="1", index=0, mode="RGB", max_bytes=4)
    finally:
        journal.close()
        server.shutdown()
        server.server_close()
        thread.join()
