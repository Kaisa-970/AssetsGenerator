#!/usr/bin/env python3
"""Durable remote_jobs@1 facade for one pinned local Qwen deployment.

No automatic resubmission to the non-idempotent upstream API is permitted.
A stranded running job blocks claims until an administrator reconciles it.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import io
import json
import os
import re
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode()


def sha(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def decode(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    value = json.loads(
        data,
        object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")),
    )
    canonical(value)
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("invalid identifier")
    return value


def digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
        raise ValueError("invalid digest")
    return value


def durable(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + str(threading.get_ident()))
    with temporary.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Conflict(ValueError):
    pass


class DeploymentInvalid(RuntimeError):
    pass


class InvalidUpstreamResult(ValueError):
    """A complete response arrived, but cannot satisfy the output contract."""


AUDIT_CONTEXT = threading.local()


def process_identity(pid):
    if type(pid) is not int or pid <= 0:
        raise DeploymentInvalid("invalid upstream PID")
    proc = Path("/proc") / str(pid)
    stat = (proc / "stat").read_text()
    fields = stat[stat.rindex(")") + 2 :].split()
    return {
        "pid": pid,
        "starttime": fields[19],
        "executable": str((proc / "exe").resolve(strict=True)),
        "cmdline_digest": sha((proc / "cmdline").read_bytes()),
    }


class Store:
    def __init__(self, root, manifest):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lockfile = (self.root / "owner.lock").open("a+")
        try:
            fcntl.flock(self.lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lockfile.close()
            raise
        self.manifest = manifest
        self.service_id = identifier(manifest["service_id"])
        self.backend = sha(canonical(manifest))
        self.lock = threading.RLock()
        self.verify_deployment()
        self.db = sqlite3.connect(self.root / "jobs.sqlite", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS deployment (identity TEXT NOT NULL)")
        rows = self.db.execute("SELECT identity FROM deployment").fetchall()
        if rows and rows[0]["identity"] != self.backend:
            raise DeploymentInvalid("state directory belongs to another deployment")
        if not rows:
            self.db.execute("INSERT INTO deployment VALUES (?)", (self.backend,))
        self.db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            submission_key TEXT PRIMARY KEY, request_digest TEXT NOT NULL,
            job_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
            result TEXT, error TEXT)""")
        self.db.commit()
        self.stopped_reason = (
            "Persisted running job requires reconciliation"
            if self.db.execute('SELECT 1 FROM jobs WHERE state="running"').fetchone()
            else None
        )

    def verify_deployment(self):
        expected_process = self.manifest.get("upstream_process")
        if expected_process is not None:
            try:
                actual = process_identity(expected_process["pid"])
            except (OSError, KeyError, ValueError) as error:
                raise DeploymentInvalid("upstream process unavailable") from error
            if actual != expected_process:
                raise DeploymentInvalid("upstream process identity changed")
            if actual["executable"] != self.manifest["roles"]["model_executable"]:
                raise DeploymentInvalid("upstream executable differs from manifest")
        files = self.manifest.get("files")
        if not isinstance(files, dict) or not files:
            raise DeploymentInvalid("deployment manifest requires pinned files")
        for path, expected in files.items():
            digest(expected)
            hasher = hashlib.sha256()
            try:
                with open(path, "rb") as stream:
                    while block := stream.read(8 * 1024 * 1024):
                        hasher.update(block)
            except OSError as error:
                raise DeploymentInvalid("deployment file unavailable") from error
            if "sha256:" + hasher.hexdigest() != expected:
                raise DeploymentInvalid("deployment file changed: " + path)
        own_path = str(Path(__file__).resolve())
        if own_path not in files:
            raise DeploymentInvalid("manifest must pin bridge implementation")

    def close(self):
        self.db.close()
        self.lockfile.close()

    def blob(self, expected):
        digest(expected)
        data = (self.root / "blobs" / expected[7:]).read_bytes()
        if sha(data) != expected:
            raise ValueError("stored Blob digest mismatch")
        return data

    def put_blob(self, expected, data):
        digest(expected)
        if not data or len(data) > 128 * 1024 * 1024 or sha(data) != expected:
            raise ValueError("invalid Blob content")
        with self.lock:
            path = self.root / "blobs" / expected[7:]
            if path.exists():
                if path.read_bytes() != data:
                    raise ValueError("existing Blob content mismatch")
            else:
                durable(path, data)

    def wire(self, row):
        return dict(
            protocol_version="1",
            service_id=self.service_id,
            backend_digest=self.backend,
            submission_key=row["submission_key"],
            request_digest=row["request_digest"],
            job_id=row["job_id"],
            state=row["state"],
            result=decode(row["result"]) if row["result"] else None,
            error=decode(row["error"]) if row["error"] else None,
        )

    def lookup(self, key, by_key=False):
        identifier(key)
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM jobs WHERE " + ("submission_key" if by_key else "job_id") + "=?",
                (key,),
            ).fetchone()
            return self.wire(row) if row else None

    def submit(self, request):
        if not isinstance(request, dict) or set(request) != {
            "service_id",
            "backend_digest",
            "submission_key",
            "request_digest",
            "payload",
        }:
            raise ValueError("invalid request envelope")
        if request["service_id"] != self.service_id or request["backend_digest"] != self.backend:
            raise Conflict("deployment identity mismatch")
        key = identifier(request["submission_key"])
        payload = request["payload"]
        if not isinstance(payload, dict):
            raise ValueError("payload must be object")
        expected = sha(
            canonical({key: request[key] for key in ("service_id", "backend_digest", "payload")})
        )
        if digest(request["request_digest"]) != expected:
            raise ValueError("request digest mismatch")
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM jobs WHERE submission_key=?", (key,)).fetchone()
            if row:
                if row["request_digest"] != expected:
                    raise Conflict("submission key conflict")
                return self.wire(row)
            job_id = "qwen_" + hashlib.sha256(key.encode()).hexdigest()[:32]
            self.db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?)",
                (key, expected, job_id, canonical(payload), "queued", None, None),
            )
            return self.wire(
                self.db.execute("SELECT * FROM jobs WHERE submission_key=?", (key,)).fetchone()
            )

    def finish(self, key, *, result=None, error=None):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE jobs SET state=?, result=?, error=? "
                'WHERE submission_key=? AND state="running"',
                (
                    "succeeded" if result else "failed",
                    canonical(result) if result else None,
                    canonical(error) if error else None,
                    key,
                ),
            )

    def claim(self):
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if (
                self.stopped_reason
                or self.db.execute('SELECT 1 FROM jobs WHERE state="running"').fetchone()
            ):
                return None
            row = self.db.execute(
                'SELECT * FROM jobs WHERE state="queued" ORDER BY rowid LIMIT 1'
            ).fetchone()
            if row:
                self.db.execute(
                    'UPDATE jobs SET state="running" WHERE submission_key=?',
                    (row["submission_key"],),
                )
            return row

    def input_data(self, payload, name, kind, schema, media):
        item = payload["input_blobs"][name]
        if not isinstance(item, dict) or set(item) != {"artifact_id", "identity"}:
            raise ValueError("invalid upload identity")
        identity = item["identity"]
        if not isinstance(identity, dict) or set(identity) != {
            "kind",
            "schema_name",
            "schema_version",
            "blob_digest",
            "identity_metadata",
        }:
            raise ValueError("invalid Artifact identity")
        if (identity["kind"], identity["schema_name"], identity["schema_version"]) != (
            kind,
            schema,
            "1.0",
        ):
            raise ValueError("input contract mismatch")
        if (
            not isinstance(identity["identity_metadata"], dict)
            or identity["identity_metadata"].get("media_type") != media
        ):
            raise ValueError("input media type mismatch")
        if digest(item["artifact_id"]) != sha(canonical(identity)) or payload["inputs"][name] != {
            "artifact_id": item["artifact_id"]
        }:
            raise ValueError("input Artifact binding mismatch")
        return self.blob(identity["blob_digest"])

    def intent(self, payload):
        required = {"capability_id", "operation", "inputs", "input_blobs", "parameters"}
        if not required.issubset(payload) or set(payload) - required - {
            "input_digest",
            "binding_digest",
        }:
            raise ValueError("invalid payload fields")
        capability = payload["capability_id"]
        if capability not in ("text_to_image", "image_to_image"):
            raise ValueError("unknown capability")
        names = {"prompt"} if capability == "text_to_image" else {"prompt", "source_image"}
        if set(payload["inputs"]) != names or set(payload["input_blobs"]) != names:
            raise ValueError("input ports mismatch")
        prompt = self.input_data(payload, "prompt", "text", "plain_text", "text/plain").decode(
            "utf-8"
        )
        if (
            not 1 <= len(prompt) <= 3000
            or not prompt.strip()
            or "\x00" in prompt
            or "<sd_cpp_extra_args>" in prompt
            or "</sd_cpp_extra_args>" in prompt
        ):
            raise ValueError("invalid prompt")
        parameters = payload["parameters"]
        if not isinstance(parameters, dict) or set(parameters) != {
            "width",
            "height",
            "steps",
            "seed",
        }:
            raise ValueError("invalid parameter fields")
        if any(type(parameters[key]) is not int for key in parameters):
            raise ValueError("parameters must be integers")
        if (
            parameters["width"] not in (512, 768, 1024)
            or parameters["height"] not in (512, 768, 1024)
            or not 1 <= parameters["steps"] <= 40
            or not -1 <= parameters["seed"] <= 2147483647
        ):
            raise ValueError("parameter out of range")
        source = None
        if capability == "image_to_image":
            source = self.input_data(payload, "source_image", "rgb_image", "png", "image/png")
            if len(source) > 8 * 1024 * 1024:
                raise ValueError("source image exceeds limit")
            with Image.open(io.BytesIO(source)) as image:
                image.load()
                if (
                    image.format != "PNG"
                    or image.mode != "RGB"
                    or "transparency" in image.info
                    or image.getexif().get(274, 1) != 1
                ):
                    raise ValueError("source must be RGB PNG without transparency or orientation")
        return prompt, parameters, source

    def execute_next(self, infer):
        row = self.claim()
        if row is None:
            return False
        key = row["submission_key"]
        try:
            intent = self.intent(decode(row["payload"]))
        except Exception:
            self.finish(
                key,
                error={
                    "code": "INVALID_INPUT",
                    "detail": "Input contract or parameter validation failed",
                },
            )
            return True
        try:
            self.verify_deployment()
        except Exception as error:
            self.stopped_reason = str(error)
            return False
        # A receipt is archived before decoding; uncertain transport remains running.
        try:
            AUDIT_CONTEXT.directory = self.root / "upstream-responses" / row["job_id"]
            png = infer(*intent)
            durable(AUDIT_CONTEXT.directory / "decoded-output.png", png)
            self.verify_deployment()
            try:
                png = rgb_output(png, intent[1])
            except Exception as error:
                raise InvalidUpstreamResult(
                    "upstream PNG content violates output contract"
                ) from error
            self.verify_deployment()
            blob_digest = sha(png)
            self.put_blob(blob_digest, png)
            self.finish(
                key,
                result={
                    "outputs": [
                        {
                            "output_id": "image",
                            "blob_digest": blob_digest,
                            "byte_length": len(png),
                            "media_type": "image/png",
                        }
                    ]
                },
            )
            return True
        except InvalidUpstreamResult as error:
            self.finish(key, error={"code": "INVALID_UPSTREAM_RESULT", "detail": str(error)})
            return True
        except Exception as error:
            self.stopped_reason = (
                "Upstream outcome unknown; explicit reconciliation required: "
                + type(error).__name__
            )
            return False


def rgb_output(png, parameters):
    """qwen_rgba_over_white_v1: preserve RGB; composite straight-alpha RGBA on white."""
    with Image.open(io.BytesIO(png)) as image:
        image.load()
        if image.format != "PNG" or image.size != (parameters["width"], parameters["height"]):
            raise ValueError("upstream image format or dimensions mismatch")
        if image.mode == "RGBA":
            image = Image.alpha_composite(
                Image.new("RGBA", image.size, (255, 255, 255, 255)), image
            )
        elif image.mode != "RGB" or "transparency" in image.info:
            raise ValueError("unsupported output mode")
        output = io.BytesIO()
        image.convert("RGB").save(output, format="PNG")
        return output.getvalue()


def port(kind, schema, media):
    return dict(
        kind=kind,
        carrier="artifact_ref",
        schema_name=schema,
        schema_version="1.0",
        media_type=media,
    )


def descriptor(store):
    caps = []
    for name, label in [("text_to_image", "文生图"), ("image_to_image", "图生图")]:
        inputs = {"prompt": port("text", "plain_text", "text/plain")}
        if name == "image_to_image":
            inputs["source_image"] = port("rgb_image", "png", "image/png")
        caps.append(
            dict(
                capability_id=name,
                display_name="Qwen Image 2.1 " + label,
                transport="remote_jobs@1",
                inputs=inputs,
                outputs={"image": port("rgb_image", "png", "image/png")},
                parameter_schema={
                    "type": "object",
                    "properties": {
                        "width": {"type": "integer", "enum": [512, 768, 1024]},
                        "height": {"type": "integer", "enum": [512, 768, 1024]},
                        "steps": {"type": "integer", "minimum": 1, "maximum": 40},
                        "seed": {"type": "integer", "minimum": -1, "maximum": 2147483647},
                    },
                    "required": ["width", "height", "steps", "seed"],
                    "additionalProperties": False,
                },
                defaults={"width": 1024, "height": 1024, "steps": 20, "seed": 0},
            )
        )
        if name == "image_to_image":
            caps[-1]["relations"] = [
                {"validator": "independent_inputs@1", "inputs": ["prompt", "source_image"]}
            ]
    return dict(
        schema_version="model_service@1",
        display_name="Qwen Image 2.1",
        service_id=store.service_id,
        backend_digest=store.backend,
        capabilities=caps,
    )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def infer(prompt, parameters, source):
    prompt += (
        " <sd_cpp_extra_args>"
        + json.dumps(
            {"seed": parameters["seed"], "sample_params": {"sample_steps": parameters["steps"]}}
        )
        + "</sd_cpp_extra_args>"
    )
    fields = {
        "prompt": prompt,
        "size": f"{parameters['width']}x{parameters['height']}",
        "output_format": "png",
    }
    if source is None:
        request = urllib.request.Request(
            "http://127.0.0.1:18080/v1/images/generations",
            canonical({**fields, "n": 1}),
            {"Content-Type": "application/json"},
        )
    else:
        boundary = "qwen-" + os.urandom(16).hex()
        body = b""
        for key, value in fields.items():
            body += (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
            ).encode()
        body += (
            (
                f"--{boundary}\r\nContent-Disposition: form-data; "
                'name="image"; filename="input.png"\r\nContent-Type: image/png\r\n\r\n'
            ).encode()
            + source
            + f"\r\n--{boundary}--\r\n".encode()
        )
        request = urllib.request.Request(
            "http://127.0.0.1:18080/v1/images/edits",
            body,
            {"Content-Type": "multipart/form-data; boundary=" + boundary},
        )
    with urllib.request.build_opener(NoRedirect).open(request, timeout=900) as response:
        raw = response.read(32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024:
            raise ValueError("upstream response exceeds limit; completion unknown")
        audit = getattr(AUDIT_CONTEXT, "directory", None)
        if audit is not None:
            durable(audit / "response-body.json", raw)
            durable(
                audit / "response-metadata.json",
                canonical(
                    {
                        "status": response.status,
                        "media_type": response.headers.get_content_type(),
                        "byte_length": len(raw),
                        "blob_digest": sha(raw),
                        "output_transform": "qwen_rgba_over_white_v1",
                    }
                ),
            )
        if response.headers.get_content_type() != "application/json":
            raise InvalidUpstreamResult("upstream response media type is not JSON")
        try:
            value = decode(raw)
            return base64.b64decode(value["data"][0]["b64_json"], validate=True)
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise InvalidUpstreamResult("upstream JSON or image encoding invalid") from error


def server(store, host, port_number):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_json(self, status, obj):
            self.send_data(status, canonical(obj), "application/json")

        def send_data(self, status, body, media):
            self.send_response(status)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def body(self, limit):
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("transfer encoding unsupported")
            length = self.headers.get("Content-Length", "")
            if not re.fullmatch(r"[0-9]+", length) or not 1 <= int(length) <= limit:
                raise ValueError("invalid request size")
            self.connection.settimeout(30)
            data = self.rfile.read(int(length))
            if len(data) != int(length):
                raise ValueError("incomplete request")
            return data

        def do_GET(self):
            try:
                if self.path == "/v1/service-descriptor":
                    return self.send_json(200, descriptor(store))
                if self.path == "/health":
                    return self.send_json(
                        200, {"ok": not bool(store.stopped_reason), "detail": store.stopped_reason}
                    )
                match = re.fullmatch(r"/v1/jobs/(?:by-key/)?([A-Za-z0-9_-]{1,128})", self.path)
                if match:
                    row = store.lookup(match[1], "/by-key/" in self.path)
                    return self.send_json(200 if row else 404, row or {})
                match = re.fullmatch(r"/v1/jobs/([A-Za-z0-9_-]{1,128})/outputs/image", self.path)
                if match:
                    row = store.lookup(match[1])
                    if row and row["state"] == "succeeded":
                        out = row["result"]["outputs"][0]
                        data = store.blob(out["blob_digest"])
                        if len(data) != out["byte_length"]:
                            raise ValueError("output size mismatch")
                        return self.send_data(200, data, "image/png")
                self.send_json(404, {})
            except Exception:
                self.send_json(400, {"error": "Invalid request or unavailable evidence"})

        def do_PUT(self):
            try:
                match = re.fullmatch(r"/v1/blobs/([a-f0-9]{64})", self.path)
                if not match:
                    return self.send_json(404, {})
                if (
                    self.headers.get("X-Service-Id") != store.service_id
                    or self.headers.get("X-Backend-Digest") != store.backend
                ):
                    return self.send_json(409, {"error": "deployment identity mismatch"})
                data = self.body(128 * 1024 * 1024)
                expected = "sha256:" + match[1]
                store.put_blob(expected, data)
                self.send_json(
                    201,
                    dict(
                        protocol_version="1",
                        service_id=store.service_id,
                        backend_digest=store.backend,
                        blob_digest=expected,
                        byte_length=len(data),
                    ),
                )
            except Exception:
                self.send_json(400, {"error": "Invalid Blob upload"})

        def do_POST(self):
            try:
                if self.path != "/v1/jobs":
                    return self.send_json(404, {})
                self.send_json(202, store.submit(decode(self.body(2 * 1024 * 1024))))
            except Conflict:
                self.send_json(409, {"error": "request identity or submission key conflict"})
            except Exception:
                self.send_json(400, {"error": "Invalid job request"})

    return ThreadingHTTPServer((host, port_number), Handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18082)
    args = parser.parse_args()
    manifest = decode(args.manifest.read_bytes())
    if not isinstance(manifest.get("upstream_process"), dict):
        raise DeploymentInvalid("manifest requires verified upstream process")
    roles = manifest.get("roles", {})
    if set(roles) != {
        "bridge",
        "model_executable",
        "model_launch",
        "diffusion_weights",
        "text_weights",
        "vision_weights",
        "vae_weights",
    } or any(path not in manifest.get("files", {}) for path in roles.values()):
        raise DeploymentInvalid("manifest requires bridge, executable, launch and all weight roles")
    store = Store(args.directory, manifest)
    http = server(store, args.host, args.port)
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            try:
                store.execute_next(infer)
            except Exception as error:
                store.stopped_reason = "Worker stopped: " + type(error).__name__
                return
            if store.stopped_reason:
                print(store.stopped_reason, flush=True)
                return
            stop.wait(0.5)

    threading.Thread(target=worker, daemon=True).start()
    print(f"Qwen bridge http://{args.host}:{args.port} backend={store.backend}", flush=True)
    try:
        http.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        http.server_close()


if __name__ == "__main__":
    main()
