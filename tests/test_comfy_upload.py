import io
import json
import threading
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.comfy_http import ComfyClient
from assets_generator.comfy_submission import ComfySubmissionUnknown
from assets_generator.comfy_upload import upload_image


@pytest.mark.parametrize(
    "failure", [None, "renamed", "different_bytes", "redirect", "corrupt_local"]
)
def test_upload_validates_input_receipt_and_readback(tmp_path, failure):
    data = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(data, format="PNG")
    encoded = data.getvalue()
    store = LocalArtifactStore(tmp_path / "store")
    ref = store.persist_bytes(
        encoded,
        kind="rgb_image",
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png", "channel_layout": "RGB"},
    )
    calls = []
    uploaded = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            calls.append(self.path)
            assert self.path == "/upload/image"
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            message = BytesParser(policy=default).parsebytes(
                ("Content-Type: " + self.headers["Content-Type"] + "\r\n\r\n").encode() + raw
            )
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                uploaded[name] = part.get_payload(decode=True)
                if name == "image":
                    uploaded["filename"] = part.get_filename()
            if failure == "redirect":
                self.send_response(307)
                self.send_header("Location", "/unexpected")
                self.end_headers()
                return
            receipt = {
                "name": uploaded["filename"] if failure != "renamed" else "other.png",
                "subfolder": "assets-generator",
                "type": "input",
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(receipt).encode())

        def do_GET(self):
            calls.append(self.path)
            assert urlsplit(self.path).path == "/view"
            assert parse_qs(urlsplit(self.path).query)["filename"] == [uploaded["filename"]]
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(b"changed" if failure == "different_bytes" else encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        client = ComfyClient(f"http://127.0.0.1:{server.server_port}")
        if failure == "corrupt_local":
            store.blob_path(ref).write_bytes(b"changed")
            with pytest.raises(ValueError, match="missing or corrupt"):
                upload_image(client, store, ref)
            assert calls == []
        elif failure:
            with pytest.raises(ComfySubmissionUnknown):
                upload_image(client, store, ref)
            assert calls.count("/upload/image") == 1
        else:
            result = upload_image(client, store, ref)
            assert result["artifact_id"] == ref.artifact_id
            assert result["verification"] == "exact-byte-readback@1"
            assert uploaded["image"] == encoded
            assert uploaded["overwrite"] == b"false"
            assert uploaded["type"] == b"input"
            assert len(calls) == 2
        assert "/prompt" not in calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
