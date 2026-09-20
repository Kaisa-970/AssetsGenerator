"""Real HTTP protocol fixture, not an installed ComfyUI or model acceptance."""

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
from assets_generator.comfy_import import import_image
from assets_generator.comfy_submission import ComfySubmissionJournal, ComfySubmissionUnknown
from assets_generator.comfy_upload import upload_image
from assets_generator.comfy_workflow import ComfyWorkflow
from assets_generator.dag_adapters import AdapterSpec


@pytest.mark.parametrize("lose_ack", [False, True])
def test_image_chain_recovers_without_reupload_resubmit_or_redownload(tmp_path, lose_ack):
    def png(color):
        output = io.BytesIO()
        Image.new("RGB", (3, 2), color).save(output, format="PNG")
        return output.getvalue()

    input_bytes, output_bytes = png("red"), png("blue")
    calls, uploaded, history = [], {}, {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, data, media="application/json"):
            self.send_response(200)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            calls.append(("POST", self.path))
            data = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/upload/image":
                message = BytesParser(policy=default).parsebytes(
                    ("Content-Type: " + self.headers["Content-Type"] + "\r\n\r\n").encode() + data
                )
                for part in message.iter_parts():
                    if part.get_param("name", header="content-disposition") == "image":
                        uploaded[part.get_filename()] = part.get_payload(decode=True)
                self.reply(
                    json.dumps(
                        {
                            "name": next(iter(uploaded)),
                            "subfolder": "assets-generator",
                            "type": "input",
                        }
                    ).encode()
                )
            elif self.path == "/prompt":
                body = json.loads(data)
                prompt = body["prompt"]
                assert prompt["1"]["inputs"]["image"] == "assets-generator/" + next(iter(uploaded))
                assert prompt["2"]["inputs"]["seed"] == 7
                prompt_id = body["prompt_id"]
                history[prompt_id] = {
                    "prompt": [0, prompt_id, prompt, {}, ["3"]],
                    "status": {"status_str": "success", "completed": True, "messages": []},
                    "outputs": {
                        "3": {
                            "images": [{"filename": "out.png", "subfolder": "", "type": "output"}]
                        }
                    },
                }
                if lose_ack:
                    self.close_connection = True
                else:
                    self.reply(json.dumps({"prompt_id": prompt_id, "node_errors": {}}).encode())
            else:
                self.send_error(404)

        def do_GET(self):
            calls.append(("GET", self.path))
            path = urlsplit(self.path)
            if path.path.startswith("/history/"):
                key = path.path.rsplit("/", 1)[1]
                self.reply(json.dumps({key: history[key]}).encode())
            elif path.path == "/view":
                query = parse_qs(path.query)
                data = (
                    uploaded[query["filename"][0]] if query["type"] == ["input"] else output_bytes
                )
                self.reply(data, "image/png")
            else:
                self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    store = LocalArtifactStore(tmp_path / "store")
    journal = ComfySubmissionJournal(tmp_path / "journal.sqlite")
    client = ComfyClient(f"http://127.0.0.1:{server.server_port}")
    try:
        source = store.persist_bytes(
            input_bytes,
            kind="rgb_image",
            schema_name="png",
            schema_version="1.0",
            identity_metadata={"media_type": "image/png", "channel_layout": "RGB"},
        )
        upload = upload_image(client, store, source)
        workflow = ComfyWorkflow(
            {
                "1": {"class_type": "LoadImage", "inputs": {"image": "placeholder"}},
                "2": {"class_type": "FixtureTransform", "inputs": {"image": ["1", 0], "seed": 42}},
                "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
            },
            AdapterSpec(
                "fixture",
                "1",
                ("fixture@1",),
                {
                    "type": "object",
                    "properties": {"seed": {"type": "integer"}},
                    "required": ["seed"],
                },
            ),
            {"seed": ("2", "seed")},
            image_targets={"image": ("1", "image")},
        )
        bound = workflow.bind({"seed": 7}, images={"image": upload}, endpoint=client.endpoint)
        journal.prepare(
            "one",
            deployment={
                "endpoint": client.endpoint,
                "input": upload,
                "workflow_digest": bound["workflow_digest"],
                "mapping_digest": bound["mapping_digest"],
                "internal_identity": "unverified",
            },
            prompt=bound["prompt"],
        )
        if lose_ack:
            with pytest.raises(ComfySubmissionUnknown):
                client.submit(journal, "one")
        else:
            client.submit(journal, "one")
        journal.close()
        journal = ComfySubmissionJournal(tmp_path / "journal.sqlite")
        observed = client.observe(journal, "one")
        result = import_image(client, journal, store, "one", node="3", index=0, mode="RGB")
        assert store.blob_path(result).read_bytes() == output_bytes
        assert source != result
        assert calls.count(("POST", "/upload/image")) == 1
        assert calls.count(("POST", "/prompt")) == 1
    finally:
        journal.close()
        server.shutdown()
        server.server_close()
        thread.join()
    before = list(calls)
    journal = ComfySubmissionJournal(tmp_path / "journal.sqlite")
    try:
        assert client.observe(journal, "one") == observed
        assert import_image(client, journal, store, "one", node="3", index=0, mode="RGB") == result
        assert calls == before
        store.blob_path(result).unlink()
        with pytest.raises(ValueError, match="missing or corrupt"):
            import_image(client, journal, store, "one", node="3", index=0, mode="RGB")
        assert not store.blob_path(result).exists()
    finally:
        journal.close()
