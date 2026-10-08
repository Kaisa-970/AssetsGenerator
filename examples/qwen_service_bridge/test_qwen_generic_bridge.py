import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import qwen_generic_bridge as bridge
from PIL import Image


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        own = str(Path(bridge.__file__).resolve())
        self.manifest = {
            "service_id": "qwen-test",
            "files": {own: bridge.sha(Path(own).read_bytes())},
        }
        self.store = bridge.Store(self.path, self.manifest)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: self.store.close())

    def artifact(self, data, kind="text", schema="plain_text", media="text/plain"):
        blob = bridge.sha(data)
        self.store.put_blob(blob, data)
        identity = dict(
            kind=kind,
            schema_name=schema,
            schema_version="1.0",
            blob_digest=blob,
            identity_metadata={"media_type": media},
        )
        return {"artifact_id": bridge.sha(bridge.canonical(identity)), "identity": identity}

    def request(self, key="test", image=False):
        prompt = self.artifact(b"a chair")
        blobs = {"prompt": prompt}
        if image:
            out = io.BytesIO()
            Image.new("RGB", (10, 10), "red").save(out, format="PNG")
            blobs["source_image"] = self.artifact(out.getvalue(), "rgb_image", "png", "image/png")
        payload = dict(
            capability_id="image_to_image" if image else "text_to_image",
            operation="dynamic",
            inputs={key: {"artifact_id": value["artifact_id"]} for key, value in blobs.items()},
            input_blobs=blobs,
            parameters={"width": 512, "height": 512, "steps": 1, "seed": 0},
        )
        return self.envelope(payload, key)

    def envelope(self, payload, key):
        value = dict(
            service_id=self.store.service_id, backend_digest=self.store.backend, payload=payload
        )
        return {
            **value,
            "submission_key": key,
            "request_digest": bridge.sha(bridge.canonical(value)),
        }

    @staticmethod
    def result(prompt, parameters, image):
        out = io.BytesIO()
        Image.new("RGBA", (parameters["width"], parameters["height"]), (1, 2, 3, 255)).save(
            out, format="PNG"
        )
        return out.getvalue()

    def test_two_capabilities_and_image_request(self):
        self.assertEqual(len(bridge.descriptor(self.store)["capabilities"]), 2)
        request = self.request(image=True)
        job = self.store.submit(request)
        called = []

        def infer(*args):
            called.append(args)
            return self.result(*args)

        self.assertTrue(self.store.execute_next(infer))
        self.assertIsInstance(called[0][2], bytes)
        final = self.store.lookup(job["job_id"])
        self.assertEqual(final["state"], "succeeded")
        with Image.open(
            io.BytesIO(self.store.blob(final["result"]["outputs"][0]["blob_digest"]))
        ) as image:
            self.assertEqual(image.mode, "RGB")

    def test_idempotency_and_digest_conflict(self):
        request = self.request()
        first = self.store.submit(request)
        self.assertEqual(self.store.submit(request), first)
        request["payload"]["parameters"]["seed"] = 10
        with self.assertRaises(ValueError):
            self.store.submit(request)
        conflicting = self.envelope(request["payload"], "test")
        with self.assertRaises(bridge.Conflict):
            self.store.submit(conflicting)
        self.store.execute_next(self.result)
        self.assertEqual(self.store.submit(self.request())["state"], "succeeded")

    def test_concurrent_submission_and_claim_execute_once(self):
        request = self.request()
        threads = [threading.Thread(target=lambda: self.store.submit(request)) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        count = []

        def infer(*args):
            count.append(1)
            return self.result(*args)

        threads = [
            threading.Thread(target=lambda: self.store.execute_next(infer)) for _ in range(10)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(count), 1)

    def test_unknown_blocks_and_restart_never_redispatches(self):
        self.store.submit(self.request())
        self.store.submit(self.request("next"))

        def unknown(*args):
            raise TimeoutError()

        self.assertFalse(self.store.execute_next(unknown))
        self.assertEqual(self.store.lookup("test", True)["state"], "running")
        self.assertEqual(self.store.lookup("next", True)["state"], "queued")
        self.store.close()
        self.store = bridge.Store(self.path, self.manifest)
        self.assertFalse(self.store.execute_next(lambda *args: self.fail("redispatched")))

    def test_invalid_input_failed_does_not_block_next(self):
        request = self.request()
        request["payload"]["inputs"]["prompt"]["artifact_id"] = "sha256:" + "0" * 64
        self.store.submit(self.envelope(request["payload"], "test"))
        self.store.submit(self.request("next"))
        self.store.execute_next(lambda *args: self.fail("invalid input dispatched"))
        self.assertEqual(self.store.lookup("test", True)["state"], "failed")
        self.store.execute_next(self.result)
        self.assertEqual(self.store.lookup("next", True)["state"], "succeeded")

    def test_blob_corruption_and_traversal_rejected(self):
        request = self.request()
        blob = request["payload"]["input_blobs"]["prompt"]["identity"]["blob_digest"]
        (self.path / "blobs" / blob[7:]).write_bytes(b"wrong")
        self.store.submit(request)
        self.store.execute_next(lambda *args: self.fail("corrupt dispatched"))
        self.assertEqual(self.store.lookup("test", True)["state"], "failed")
        with self.assertRaises(ValueError):
            self.store.blob("sha256:../../etc/passwd")

    def test_process_lock(self):
        with self.assertRaises(BlockingIOError):
            bridge.Store(self.path, self.manifest)

    def test_deployment_change_stops_next_claim(self):
        test_file = self.path / "pinned"
        test_file.write_bytes(b"original")
        self.manifest["files"][str(test_file)] = bridge.sha(b"original")
        self.store.submit(self.request())
        self.store.submit(self.request("next"))
        test_file.write_bytes(b"changed")
        self.store.execute_next(lambda *args: self.fail("changed deployment dispatched"))
        self.assertEqual(self.store.lookup("test", True)["state"], "running")
        self.assertEqual(self.store.lookup("next", True)["state"], "queued")

    def test_http_upload_identity_receipt_and_result(self):
        http = bridge.server(self.store, "127.0.0.1", 0)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        base = f"http://127.0.0.1:{http.server_port}"
        request = self.request()
        blob = b"upload"
        path = base + "/v1/blobs/" + bridge.sha(blob)[7:]
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(urllib.request.Request(path, blob, method="PUT"))
        self.assertEqual(caught.exception.code, 409)
        headers = {"X-Service-Id": self.store.service_id, "X-Backend-Digest": self.store.backend}
        with urllib.request.urlopen(
            urllib.request.Request(path, blob, headers, method="PUT")
        ) as response:
            self.assertEqual(json.load(response)["blob_digest"], bridge.sha(blob))
        with urllib.request.urlopen(
            urllib.request.Request(
                base + "/v1/jobs", bridge.canonical(request), {"Content-Type": "application/json"}
            )
        ) as response:
            job = json.load(response)
        self.store.execute_next(self.result)
        with urllib.request.urlopen(base + "/v1/jobs/by-key/test") as response:
            self.assertEqual(json.load(response)["state"], "succeeded")
        with urllib.request.urlopen(
            base + "/v1/jobs/" + job["job_id"] + "/outputs/image"
        ) as response:
            with Image.open(io.BytesIO(response.read())) as image:
                self.assertEqual(image.mode, "RGB")

    def test_real_core_adapter_http_contract(self):
        from assets_generator.artifact_store import LocalArtifactStore
        from assets_generator.dag_adapters import NodeExecutionContext
        from assets_generator.model_service_adapters import GenericRemoteCapabilityAdapter
        from assets_generator.remote_http import RemoteJobClient
        from assets_generator.remote_protocol import RemoteIdentity, RemoteRequest
        from assets_generator.serialization import to_primitive

        http = bridge.server(self.store, "127.0.0.1", 0)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        endpoint = f"http://127.0.0.1:{http.server_port}"
        client = RemoteJobClient(endpoint)
        adapter = GenericRemoteCapabilityAdapter(
            endpoint, client.service_descriptor(), "text_to_image"
        )
        store = LocalArtifactStore(self.path / "artifacts")
        prompt = store.persist_bytes(
            "红色椅子".encode(),
            kind="text",
            schema_name="plain_text",
            schema_version="1.0",
            identity_metadata={"media_type": "text/plain", "label": "中文"},
        )
        context = NodeExecutionContext(
            "run",
            "generate",
            {"prompt": prompt},
            {"width": 512, "height": 512, "steps": 1, "seed": 0},
            store,
        )
        payload = adapter.prepare_payload(context)
        payload["input_blobs"] = {}
        identity = RemoteIdentity(self.store.service_id, self.store.backend)
        for name, ref in adapter.input_blobs(context).items():
            manifest = store.get_manifest(ref.artifact_id)
            payload["input_blobs"][name] = {
                "artifact_id": ref.artifact_id,
                "identity": to_primitive(manifest.identity),
            }
            client.upload_blob(
                identity, store.blob_path(ref).read_bytes(), manifest.identity.blob_digest
            )
        payload.update(input_digest="sha256:" + "1" * 64, binding_digest="sha256:" + "2" * 64)
        request = RemoteRequest.create(identity, "core-test", payload)
        job = client.submit(request)
        self.store.execute_next(self.result)
        job = client.query(request, job.job_id)
        self.assertEqual(job.state, "succeeded")
        blobs = {"image": client.download(request, job.job_id, "image", expected_job=job)}
        self.assertIn("image", adapter.import_result(context, job, blobs).outputs)

    def test_upstream_process_identity_detects_replacement(self):
        current = bridge.process_identity(os.getpid())
        self.store.manifest["upstream_process"] = {**current, "starttime": "wrong"}
        with self.assertRaises(bridge.DeploymentInvalid):
            self.store.verify_deployment()

    def test_rgba_white_composite_has_explicit_pixel_semantics(self):
        output = io.BytesIO()
        image = Image.new("RGBA", (3, 1))
        image.putdata([(255, 0, 0, 0), (255, 0, 0, 128), (255, 0, 0, 255)])
        image.save(output, format="PNG")
        result = bridge.rgb_output(output.getvalue(), {"width": 3, "height": 1})
        with Image.open(io.BytesIO(result)) as image:
            self.assertEqual(image.mode, "RGB")
            self.assertEqual(list(image.getdata()), [(255, 255, 255), (255, 127, 127), (255, 0, 0)])

    def test_complete_invalid_response_fails_without_blocking_queue(self):
        self.store.submit(self.request())
        self.store.submit(self.request("next"))
        self.store.execute_next(lambda *args: b"not a PNG")
        self.assertEqual(self.store.lookup("test", True)["state"], "failed")
        self.assertTrue(list((self.path / "upstream-responses").glob("*/decoded-output.png")))
        self.store.execute_next(self.result)
        self.assertEqual(self.store.lookup("next", True)["state"], "succeeded")

    def test_duplicate_json_fields_rejected(self):
        with self.assertRaises(ValueError):
            bridge.decode(b'{"a":1,"a":2}')
        with self.assertRaises(ValueError):
            bridge.decode(b'{"a":NaN}')


if __name__ == "__main__":
    unittest.main()
