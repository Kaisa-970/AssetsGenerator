"""CPU protocol fixture: prompt-dependent colour tiles, NOT a generative model.

PYTHONPATH=src python examples/generic_model_service_cpu.py \
    --directory /tmp/generic-cpu-service --port 8782

Add the printed URL in the model-service panel. The descriptor supplies the
text-to-image ports; the Core needs no text-to-image Python Adapter.
"""

from __future__ import annotations

import argparse
import io
import json
import threading
from pathlib import Path

from PIL import Image
from PIL import __version__ as pillow_version

from assets_generator.model_service_descriptor import validate_descriptor
from assets_generator.remote_protocol import RemoteIdentity, RemoteRequest
from assets_generator.remote_service_http import create_remote_server
from assets_generator.remote_service_store import RemoteServiceStore
from assets_generator.remote_service_worker import ServiceOutput, execute_next_service_job
from assets_generator.serialization import canonical_json_bytes, sha256_bytes


def cpu_descriptor() -> dict:
    """Pin fixture code and rendering environment, not an invented model identity."""
    return validate_descriptor(
        {
            "schema_version": "model_service@1",
            "display_name": "CPU 文字转图片示例（非真实模型）",
            "service_id": "generic-cpu-image",
            "backend_digest": sha256_bytes(
                canonical_json_bytes(
                    {
                        "fixture": "prompt-colour-tile-v1",
                        "code": sha256_bytes(Path(__file__).read_bytes()),
                        "pillow": pillow_version,
                    }
                )
            ),
            "capabilities": [
                {
                    "capability_id": "text_to_image",
                    "display_name": "文字转图片（CPU 协议示例）",
                    "transport": "remote_jobs@1",
                    "inputs": {
                        "prompt": {
                            "kind": "text",
                            "carrier": "artifact_ref",
                            "schema_name": "plain_text",
                            "schema_version": "1.0",
                            "media_type": "text/plain",
                        }
                    },
                    "outputs": {
                        "image": {
                            "kind": "rgb_image",
                            "carrier": "artifact_ref",
                            "schema_name": "png",
                            "schema_version": "1.0",
                            "media_type": "image/png",
                        }
                    },
                    "parameter_schema": {
                        "type": "object",
                        "properties": {
                            "width": {"type": "integer", "minimum": 1, "maximum": 512},
                            "height": {"type": "integer", "minimum": 1, "maximum": 512},
                            "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647},
                        },
                        "required": ["width", "height", "seed"],
                        "additionalProperties": False,
                    },
                    "defaults": {"width": 128, "height": 96, "seed": 0},
                }
            ],
        }
    )


def render_tile(request: RemoteRequest, service: RemoteServiceStore) -> dict[str, ServiceOutput]:
    """Verify the uploaded Artifact, then deterministically render a small RGB PNG."""
    payload = json.loads(request.payload_json)
    if payload.get("capability_id") != "text_to_image":
        raise ValueError("unknown fixture capability")
    if set(payload.get("inputs", {})) != {"prompt"} or set(payload.get("input_blobs", {})) != {
        "prompt"
    }:
        raise ValueError("fixture requires exactly one prompt input")
    upload = payload["input_blobs"]["prompt"]
    identity = upload["identity"]
    if (
        set(upload) != {"artifact_id", "identity"}
        or set(identity)
        != {"kind", "schema_name", "schema_version", "blob_digest", "identity_metadata"}
        or (identity["kind"], identity["schema_name"], identity["schema_version"])
        != ("text", "plain_text", "1.0")
        or identity["identity_metadata"].get("media_type") != "text/plain"
        or sha256_bytes(canonical_json_bytes(identity)) != upload["artifact_id"]
        or payload["inputs"]["prompt"] != {"artifact_id": upload["artifact_id"]}
    ):
        raise ValueError("prompt Artifact contract or identity mismatch")
    data = service.get_blob(identity["blob_digest"])
    if len(data) > 65536:
        raise ValueError("prompt exceeds fixture limit")
    text = data.decode("utf-8")
    if not text.strip() or "\x00" in text:
        raise ValueError("prompt must contain nonempty UTF-8 text")
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict) or set(parameters) != {"width", "height", "seed"}:
        raise ValueError("invalid fixture parameters")
    for key, limit in (("width", 512), ("height", 512), ("seed", 2147483647)):
        if (
            type(parameters[key]) is not int
            or not (0 if key == "seed" else 1) <= parameters[key] <= limit
        ):
            raise ValueError("invalid fixture parameter: " + key)
    digest = sha256_bytes(canonical_json_bytes({"prompt": text, "seed": parameters["seed"]}))
    colour = tuple(bytes.fromhex(digest.split(":")[1][:6]))
    output = io.BytesIO()
    Image.new("RGB", (parameters["width"], parameters["height"]), colour).save(output, format="PNG")
    return {"image": ServiceOutput(output.getvalue(), "image/png")}


class CpuGenericService:
    def __init__(self, directory: Path, port: int = 0):
        self.descriptor = cpu_descriptor()
        self.store = RemoteServiceStore(
            directory / "service.sqlite",
            RemoteIdentity(self.descriptor["service_id"], self.descriptor["backend_digest"]),
        )
        self.server = create_remote_server(self.store, port=port, descriptor=self.descriptor)
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"

    def execute_next(self):
        return execute_next_service_job(self.store, render_tile)

    def serve(self) -> None:
        stop = threading.Event()

        def work():
            while not stop.is_set():
                try:
                    self.execute_next()
                except Exception as error:
                    # Unknown running state stays durable and blocks all further claims.
                    print(f"CPU worker stopped: {error}", flush=True)
                    return
                stop.wait(0.1)

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        print(self.endpoint, flush=True)
        try:
            self.server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            worker.join()
            self.server.server_close()
            self.store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8782)
    args = parser.parse_args()
    CpuGenericService(args.directory, args.port).serve()


if __name__ == "__main__":
    main()
