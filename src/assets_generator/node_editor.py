"""Local draft editor: static compilation only, never dispatch models."""

from __future__ import annotations

import json
import mimetypes
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import yaml

from .contracts import ContractError
from .dag_adapters import AdapterRegistry
from .multi_view_relations import register_multi_view_relations
from .pipeline import (
    _pipeline_from_raw,
    compile_pipeline,
    load_default_operator_specs,
    load_operator_specs,
)
from .relations import default_relation_registry
from .serialization import canonical_json_bytes, to_primitive
from .workbench_persistence import DurableIO


class DraftEditor:
    def __init__(
        self,
        directory: Path,
        operators: Path | None = None,
        templates: list[Path] | None = None,
        adapters: AdapterRegistry | None = None,
    ):
        self.directory = directory.absolute()
        self.adapters = adapters
        self.specs = load_default_operator_specs()
        if operators:
            self.specs.update(load_operator_specs(operators))
        self.relations = default_relation_registry()
        register_multi_view_relations(self.relations)
        self.templates = [
            {"id": p.stem, "label": p.stem, "pipeline": yaml.safe_load(p.read_text())}
            for p in templates or []
        ]
        self.io = DurableIO()
        self.lock = threading.RLock()

    def catalog(self) -> dict[str, Any]:
        return {
            "operators": {k: to_primitive(v) for k, v in self.specs.items()},
            "adapters": self.adapters.catalog() if self.adapters else [],
            "templates": self.templates,
            "execution_enabled": False,
        }

    def compile(self, raw: Any) -> dict[str, Any]:
        try:
            if not isinstance(raw, dict):
                raise ContractError("pipeline must be an object")
            if set(raw) - {"pipeline", "version", "inputs", "nodes"}:
                raise ContractError("unknown pipeline fields")
            plan = compile_pipeline(
                _pipeline_from_raw(raw),
                self.specs,
                relation_registry=self.relations,
                require_explicit_joins=True,
            )
            bound = (
                self.adapters.bind_plan(plan, relation_registry=self.relations)
                if self.adapters
                else None
            )
            return {
                "ok": True,
                "plan": plan.to_dict(),
                "diagnostics": [],
                "scope": "static_contracts_only",
                "execution_ready": False,
                "bound_plan": bound.to_dict() if bound else None,
            }
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            message = str(error)
            diagnostic: dict[str, Any] = {"message": message}
            location = re.match(r"^([\w-]+)\.([\w-]+)\s", message)
            if location:
                diagnostic.update(node_id=location[1], port=location[2])
            # Contract errors already include node/port names where known. Never
            # invent a location for an ambiguous global diagnostic.
            if isinstance(raw, dict) and isinstance(raw.get("nodes"), dict):
                matches = [
                    name
                    for name in raw["nodes"]
                    if isinstance(name, str)
                    and re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", message)
                ]
                if len(matches) == 1 and "node_id" not in diagnostic:
                    diagnostic["node_id"] = matches[0]
                    match = re.search(re.escape(matches[0]) + r"\.([\w-]+)", message)
                    if match:
                        diagnostic["port"] = match[1]
            return {"ok": False, "diagnostics": [diagnostic]}

    def path(self, name: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name):
            raise ValueError("invalid draft name")
        path = self.directory / f"{name}.json"
        if path.is_symlink():
            raise ValueError("draft cannot be a symbolic link")
        return path

    def save(self, name: str, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict) or set(body) != {"pipeline", "layout"}:
            raise ValueError("draft requires pipeline and layout")
        if not isinstance(body["pipeline"], dict) or not isinstance(body["layout"], dict):
            raise ValueError("pipeline and layout must be objects")
        with self.lock:
            path = self.path(name)
            self.io.mkdir(self.directory)
            self.io.write(path, canonical_json_bytes(body))
        return {"saved": name, "validation": self.compile(body["pipeline"])}


def create_editor_server(editor: DraftEditor, port: int = 8767) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, data: bytes, media: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def respond(self, status: int, value: Any) -> None:
            self.send(status, canonical_json_bytes(value))

        def allowed(self, mutation: bool = False) -> bool:
            host = self.headers.get("Host")
            allowed = {
                f"127.0.0.1:{cast(ThreadingHTTPServer, self.server).server_port}",
                f"localhost:{cast(ThreadingHTTPServer, self.server).server_port}",
            }
            if host not in allowed:
                return False
            return not mutation or self.headers.get("Origin") in {None, f"http://{host}"}

        def do_GET(self) -> None:
            if not self.allowed():
                self.respond(403, {"error": "invalid Host"})
                return
            path = urlsplit(self.path).path
            try:
                if path == "/api/catalog":
                    self.respond(200, editor.catalog())
                elif path == "/api/drafts":
                    self.respond(
                        200,
                        {
                            "drafts": sorted(
                                p.stem
                                for p in editor.directory.glob("*.json")
                                if not p.is_symlink()
                            )
                        },
                    )
                elif path.startswith("/api/drafts/"):
                    self.respond(
                        200, json.loads(editor.path(path.removeprefix("/api/drafts/")).read_text())
                    )
                else:
                    relative = "index.html" if path == "/" else path.lstrip("/")
                    if ".." in Path(relative).parts or "\\" in relative or "%" in relative:
                        raise ValueError("invalid static path")
                    resource = (
                        files("assets_generator.resources")
                        .joinpath("node-editor")
                        .joinpath(relative)
                    )
                    self.send(
                        200,
                        resource.read_bytes(),
                        mimetypes.guess_type(relative)[0] or "application/octet-stream",
                    )
            except FileNotFoundError:
                self.respond(
                    404, {"error": "not found; build frontend with npm run build if missing"}
                )
            except (ValueError, OSError) as error:
                self.respond(400, {"error": str(error)})

        def mutate(self) -> None:
            if not self.allowed(True):
                self.respond(403, {"error": "invalid origin"})
                return
            try:
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("application/json required")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2 * 1024 * 1024:
                    raise ValueError("invalid body size")
                self.connection.settimeout(10)
                body = json.loads(self.rfile.read(length))
                path = urlsplit(self.path).path
                if self.command == "POST" and path == "/api/compile":
                    self.respond(200, editor.compile(body.get("pipeline")))
                elif self.command == "PUT" and path.startswith("/api/drafts/"):
                    self.respond(200, editor.save(path.removeprefix("/api/drafts/"), body))
                else:
                    self.respond(404, {"error": "unknown route"})
            except (ValueError, OSError, AttributeError) as error:
                self.respond(400, {"error": str(error)})

        do_POST = mutate
        do_PUT = mutate

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve_editor(directory: Path, port: int, operators: Path | None, templates: list[Path]) -> None:
    server = create_editor_server(DraftEditor(directory, operators, templates), port)
    print(f"http://127.0.0.1:{server.server_port}/ (Ctrl+C 关闭)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
