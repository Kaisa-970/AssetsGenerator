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
from test_remote_http import request

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.comfy_http import ComfyClient
from assets_generator.comfy_result import publish_image_result
from assets_generator.comfy_service import finish_owned_image, start_owned_image
from assets_generator.comfy_submission import ComfySubmissionUnknown
from assets_generator.comfy_workflow import ComfyWorkflow
from assets_generator.dag_adapters import AdapterSpec
from assets_generator.models import ArtifactRef
from assets_generator.remote_protocol import RemoteRequest
from assets_generator.remote_service_store import RemoteServiceStore


@pytest.mark.parametrize("commit_crash", ["none", "before", "after"])
@pytest.mark.parametrize("lose_ack", [False, True])
def test_image_chain_recovers_without_reupload_resubmit_or_redownload(
    tmp_path, lose_ack, commit_crash, monkeypatch
):
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
    journal_path = tmp_path / "journal.sqlite"
    req = RemoteRequest.create(request().identity, "one", {})
    owner_path = tmp_path / "owner.sqlite"
    owner = RemoteServiceStore(owner_path, req.identity)
    owner.submit(req)
    owner.transition(req, expected="queued", state="running")
    client = ComfyClient(f"http://127.0.0.1:{server.server_port}")
    try:
        source = store.persist_bytes(
            input_bytes,
            kind="rgb_image",
            schema_name="png",
            schema_version="1.0",
            identity_metadata={"media_type": "image/png", "channel_layout": "RGB"},
        )
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

        def start():
            return start_owned_image(
                owner,
                req,
                journal_path,
                client,
                store,
                workflow,
                images={"image": source},
                parameters={"seed": 7},
                deployment_claims={"fixture": True},
            )

        if lose_ack:
            with pytest.raises(ComfySubmissionUnknown):
                start()
        else:
            start()
        owner.close()
        owner = RemoteServiceStore(owner_path, req.identity)
        persist = store.persist_structured

        def interrupted(value):
            if commit_crash == "after":
                persist(value)
            raise OSError("injected result commit crash")

        if commit_crash != "none":
            monkeypatch.setattr(store, "persist_structured", interrupted)
            with pytest.raises(ComfySubmissionUnknown):
                finish_owned_image(
                    owner, req, journal_path, client, store, node="3", index=0, mode="RGB"
                )
        else:
            fixed = finish_owned_image(
                owner, req, journal_path, client, store, node="3", index=0, mode="RGB"
            )
        owner.close()
        owner = RemoteServiceStore(owner_path, req.identity)
        monkeypatch.setattr(store, "persist_structured", lambda *_: pytest.fail("repaired result"))
        if commit_crash == "before":
            with pytest.raises(ComfySubmissionUnknown):
                finish_owned_image(
                    owner, req, journal_path, client, store, node="3", index=0, mode="RGB"
                )
            assert owner.lookup(req).state == "running"
        else:
            fixed = finish_owned_image(
                owner, req, journal_path, client, store, node="3", index=0, mode="RGB"
            )
            loaded = store.read_structured(fixed)
            result = ArtifactRef(loaded["outputs"]["image"]["artifact_id"])
            assert result != source
            assert store.blob_path(result).read_bytes() == output_bytes
            assert loaded["inputs"]["image"] == {"artifact_id": source.artifact_id}
            assert set(loaded["internal_verification"].values()) == {"unverified"}
            from assets_generator.workbench_persistence import _references

            assert set(_references(loaded)) == {source, result}
            terminal = publish_image_result(owner, req, store)
            assert terminal.state == "succeeded"
        assert calls.count(("POST", "/upload/image")) == 1
        assert calls.count(("POST", "/prompt")) == 1
        assert sum(method == "GET" and "type=output" in path for method, path in calls) == 1
    finally:
        owner.close()
        server.shutdown()
        server.server_close()
        thread.join()

    before = list(calls)
    # Completed results must remain usable after the internal journal disappears.
    if commit_crash != "before":
        journal_path.unlink()
        journal_path.with_name(journal_path.name + ".identity.json").unlink()
    owner = RemoteServiceStore(owner_path, req.identity)
    try:
        if commit_crash == "before":
            with pytest.raises(ComfySubmissionUnknown):
                finish_owned_image(
                    owner, req, journal_path, client, store, node="3", index=0, mode="RGB"
                )
        else:
            assert (
                finish_owned_image(
                    owner, req, journal_path, client, store, node="3", index=0, mode="RGB"
                )
                == fixed
            )
            with pytest.raises(ComfySubmissionUnknown):
                finish_owned_image(
                    owner, req, journal_path, client, store, node="different", index=0, mode="RGB"
                )
            assert publish_image_result(owner, req, store) == terminal
            assert owner.download(req, "image")[1] == output_bytes
            assert owner.download(req, "evidence")[1] == store.blob_path(fixed).read_bytes()
            store.blob_path(result).unlink()
            with pytest.raises(ComfySubmissionUnknown):
                publish_image_result(owner, req, store)
            assert not store.blob_path(result).exists()
        assert calls == before
    finally:
        owner.close()
