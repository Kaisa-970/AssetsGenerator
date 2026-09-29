"""Local draft editor with owned image or multi-view DAG execution."""

from __future__ import annotations

import ipaddress
import json
import mimetypes
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import yaml

from .contracts import ContractError
from .dag_adapters import AdapterRegistry, NodeBindingError
from .multi_view_relations import register_multi_view_relations
from .node_editor_execution import (
    NodeEditorExecution,
    PreflightChanged,
    validate_editor_execution,
    validate_editor_reference_inputs,
)
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
        execution: NodeEditorExecution | None = None,
        execution_profile: str | None = None,
    ):
        self.directory = directory.absolute()
        self.execution = execution
        self.execution_profile = execution_profile
        self.adapters = execution.engine.registry if execution else adapters
        if execution is not None:
            self.specs = execution.specs
            self.relations = execution.relations
            if operators:
                declared = load_operator_specs(operators)
                if any(self.specs.get(key) != value for key, value in declared.items()):
                    raise ContractError("editor operator contracts differ from execution service")
        else:
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
        from .node_editor_services import ModelServices

        self.model_services = ModelServices(self.directory)
        if self.execution:
            self.execution.install_model_registry(self.model_services.restore)
            from .model_service_descriptor import dynamic_operator_spec, select_capability

            for entry in self.model_services.entries:
                selected = select_capability(entry["descriptor"], entry.get("capability_id"))
                if "inputs" in selected and "outputs" in selected:
                    spec = dynamic_operator_spec(selected)
                    self.specs[f"{spec.name}@{spec.version}"] = spec
            self.adapters = self.execution.engine.registry

    def catalog(self) -> dict[str, Any]:
        templates = []
        for template in self.templates:
            result = self.compile(template["pipeline"])
            bindings = (result.get("bound_plan") or {}).get("bindings", {})
            kinds = {item["spec"]["execution_kind"] for item in bindings.values()}
            level = (
                "remote"
                if "remote" in kinds
                else "local"
                if "process" in kinds
                else "cpu"
                if bindings
                else "unconfigured"
            )
            reason = result.get("execution_reason")
            if not result.get("ok"):
                reason = "; ".join(item["message"] for item in result.get("diagnostics", []))
            elif not result.get("execution_ready") and not reason:
                reason = "当前服务仅支持编辑，请配置执行入口"
            templates.append(
                {
                    **template,
                    "execution_ready": result.get("execution_ready", False),
                    "execution_reason": reason,
                    "execution_level": level,
                    "service_status": "未检查远程可达性" if level == "remote" else None,
                }
            )
        return {
            "operators": {k: to_primitive(v) for k, v in self.specs.items()},
            "adapters": self.adapters.catalog() if self.adapters else [],
            "backends": self.adapters.backend_catalog() if self.adapters else [],
            "templates": templates,
            "model_services": self.model_services.summaries(),
            "execution_enabled": self.execution is not None,
            "execution_profile": (
                "已配置模型服务"
                if self.execution_profile == "CPU · 未配置模型" and self.model_services.summaries()
                else self.execution_profile
            ),
        }

    def detect_model_service(self, body: Any) -> dict[str, Any]:
        if not self.execution:
            raise ContractError("请使用 --store 启用执行服务后添加模型")
        if not isinstance(body, dict) or set(body) != {"endpoint"}:
            raise ContractError("服务检测需要 endpoint")
        from .model_service_adapters import discovered_service_adapter

        detection = self.model_services.detect(body["endpoint"])
        descriptor = detection["descriptor"]
        availability = {}
        for capability in descriptor.get("capabilities", [descriptor]):
            capability_id = capability.get("capability_id", capability["operator"])
            try:
                discovered_service_adapter(
                    detection["endpoint"],
                    descriptor,
                    capability_id if descriptor.get("capabilities") else None,
                )
            except (ValueError, TypeError) as exc:
                availability[capability_id] = {"installable": False, "reason": str(exc)}
            else:
                availability[capability_id] = {"installable": True, "reason": None}
        return {**detection, "capability_availability": availability}

    def add_model_service(self, body: Any) -> dict[str, Any]:
        if not self.execution:
            raise ContractError("请使用 --store 启用执行服务后添加模型")
        if (
            not isinstance(body, dict)
            or set(body) - {"endpoint", "descriptor_digest", "capability_id"}
            or not {"endpoint", "descriptor_digest"}.issubset(body)
        ):
            raise ContractError("请先检测服务再确认添加")
        with self.lock:
            entry = self.model_services.add(
                body["endpoint"],
                body["descriptor_digest"],
                self.execution,
                body.get("capability_id"),
            )
            self.adapters = self.execution.engine.registry
            return {
                "backend": entry["backend"],
                "display_name": entry["descriptor"]["display_name"],
                "catalog": self.catalog(),
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
            execution_reason = None
            if self.execution and bound:
                try:
                    if len(plan.inputs) > 1:
                        validate_editor_reference_inputs(plan)
                    else:
                        validate_editor_execution(plan)
                except ContractError as error:
                    execution_reason = str(error)
            return {
                "ok": True,
                "plan": plan.to_dict(),
                "diagnostics": [],
                "scope": "bound_execution" if self.execution else "static_contracts_only",
                "execution_ready": self.execution is not None
                and bound is not None
                and execution_reason is None,
                "execution_reason": execution_reason,
                "bound_plan": bound.to_dict() if bound else None,
            }
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            message = str(error)
            diagnostic: dict[str, Any] = {"message": message}
            if isinstance(error, NodeBindingError):
                diagnostic["node_id"] = error.node_id
                return {"ok": False, "diagnostics": [diagnostic]}
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


def create_editor_server(
    editor: DraftEditor, port: int = 8767, *, host: str = "127.0.0.1"
) -> ThreadingHTTPServer:
    address = ipaddress.IPv4Address(host)
    if address.is_unspecified or address.is_multicast:
        raise ValueError("Use a specific local IPv4 address, not a wildcard or multicast address")
    bind_host = str(address)

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
                f"{bind_host}:{cast(ThreadingHTTPServer, self.server).server_port}",
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
                elif path == "/api/runs" and editor.execution:
                    self.respond(200, {"runs": editor.execution.list_runs()})
                elif path.startswith("/api/runs/") and editor.execution:
                    parts = path.removeprefix("/api/runs/").split("/")
                    if len(parts) == 3 and parts[1] in {
                        "snapshot-input-reference",
                        "snapshot-input-image",
                    }:
                        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                        if (
                            set(query) != {"snapshot"}
                            or len(query["snapshot"]) != 1
                            or not query["snapshot"][0]
                        ):
                            raise ValueError("snapshot input requires one snapshot identity")
                        snapshot = {"artifact_id": query["snapshot"][0]}
                        if parts[1] == "snapshot-input-reference":
                            self.respond(
                                200,
                                editor.execution.snapshot_input_reference(
                                    parts[0], parts[2], snapshot
                                ),
                            )
                        else:
                            output = editor.execution.snapshot_input_image(
                                parts[0], parts[2], snapshot
                            )
                            self.send(200, output.data, output.media_type)
                    elif len(parts) == 4 and parts[1] in {"snapshot-output", "snapshot-reference"}:
                        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                        if (
                            set(query) != {"snapshot"}
                            or len(query["snapshot"]) != 1
                            or not query["snapshot"][0]
                        ):
                            raise ValueError("snapshot output requires one snapshot identity")
                        snapshot = {"artifact_id": query["snapshot"][0]}
                        if parts[1] == "snapshot-reference":
                            self.respond(
                                200,
                                editor.execution.snapshot_reference(
                                    parts[0], parts[2], parts[3], snapshot
                                ),
                            )
                        else:
                            output = editor.execution.snapshot_output(
                                parts[0], parts[2], parts[3], snapshot
                            )
                            self.send(200, output.data, output.media_type)
                    elif len(parts) == 3 and parts[1] == "continue-extraction":
                        query = parse_qs(urlsplit(self.path).query)
                        if set(query) != {"snapshot"} or len(query["snapshot"]) != 1:
                            raise ValueError("continue extraction requires one snapshot identity")
                        self.respond(
                            200,
                            editor.execution.continue_extraction(
                                parts[0], parts[2], {"artifact_id": query["snapshot"][0]}
                            ),
                        )
                    elif len(parts) == 3 and parts[1] == "decisions":
                        self.respond(200, editor.execution.decision_summary(parts[0], parts[2]))
                    elif len(parts) == 4 and parts[1] == "archives":
                        output = editor.execution.release_archive(parts[0], parts[2], parts[3])
                        self.send(200, output.data, output.media_type)
                    elif len(parts) == 4 and parts[1] == "references":
                        self.respond(
                            200, editor.execution.output_reference(parts[0], parts[2], parts[3])
                        )
                    elif len(parts) == 4 and parts[1] == "appearance":
                        self.respond(200, editor.execution.appearance(parts[0], parts[2], parts[3]))
                    elif len(parts) == 4 and parts[1] == "outputs":
                        output = editor.execution.output(parts[0], parts[2], parts[3])
                        self.send(200, output.data, output.media_type)
                    elif len(parts) == 2 and parts[1] == "input-references":
                        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                        if (
                            set(query) != {"snapshot"}
                            or len(query["snapshot"]) != 1
                            or not query["snapshot"][0]
                        ):
                            raise ValueError("input references require one snapshot identity")
                        self.respond(
                            200,
                            editor.execution.input_references(
                                parts[0], {"artifact_id": query["snapshot"][0]}
                            ),
                        )
                    elif len(parts) == 2 and parts[1] == "draft":
                        self.respond(200, editor.execution.draft_from_run(parts[0]))
                    elif len(parts) == 2 and parts[1] == "plan":
                        self.respond(200, editor.execution.plan(parts[0]))
                    elif len(parts) == 1:
                        self.respond(200, editor.execution.snapshot(parts[0]))
                    else:
                        raise ValueError("invalid run route")
                elif path == "/api/inputs/text" and editor.execution:
                    query = parse_qs(urlsplit(self.path).query)
                    if set(query) != {"artifact_id"} or len(query["artifact_id"]) != 1:
                        raise ValueError("text preview requires one Artifact ID")
                    self.respond(200, editor.execution.read_text_input(query["artifact_id"][0]))
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
            except (ValueError, OSError, KeyError, RuntimeError) as error:
                self.respond(400, {"error": str(error)})

        def mutate(self) -> None:
            if not self.allowed(True):
                self.respond(403, {"error": "invalid origin"})
                return
            try:
                path = urlsplit(self.path).path
                if (
                    self.command == "POST"
                    and path
                    in {
                        "/api/inputs/image",
                        "/api/inputs/rgba",
                        "/api/inputs/mask",
                        "/api/inputs/text",
                    }
                    and editor.execution
                ):
                    if self.headers.get("Content-Type") != "application/octet-stream":
                        raise ValueError("application/octet-stream required")
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 20 * 1024 * 1024:
                        raise ValueError("image upload must be at most 20 MiB")
                    self.connection.settimeout(10)
                    data = self.rfile.read(length)
                    if len(data) != length:
                        raise ValueError("incomplete image upload")
                    self.respond(
                        201,
                        editor.execution.upload_text(data)
                        if path == "/api/inputs/text"
                        else editor.execution.upload_mask(data)
                        if path == "/api/inputs/mask"
                        else editor.execution.upload_image(data, rgba=path == "/api/inputs/rgba"),
                    )
                    return
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("application/json required")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2 * 1024 * 1024:
                    raise ValueError("invalid body size")
                self.connection.settimeout(10)
                body = json.loads(self.rfile.read(length))
                path = urlsplit(self.path).path
                if self.command == "POST" and path == "/api/model-services/detect":
                    self.respond(200, editor.detect_model_service(body))
                elif self.command == "POST" and path == "/api/model-services":
                    self.respond(201, editor.add_model_service(body))
                elif self.command == "POST" and path == "/api/compile":
                    self.respond(200, editor.compile(body.get("pipeline")))
                elif (
                    self.command == "POST"
                    and path == "/api/inputs/observations"
                    and editor.execution
                ):
                    if not isinstance(body, dict) or set(body) != {"images"}:
                        raise ValueError("observations import requires images")
                    self.respond(201, editor.execution.import_observations(body["images"]))
                elif self.command == "POST" and path == "/api/preflight" and editor.execution:
                    if not isinstance(body, dict) or set(body) - {"reuse_source"} != {
                        "pipeline",
                        "input_refs",
                    }:
                        raise ValueError("preflight requires pipeline and prepared input_refs")
                    self.respond(
                        200,
                        editor.execution.preflight(
                            body["pipeline"],
                            input_refs=body["input_refs"],
                            reuse_source=body.get("reuse_source"),
                        ),
                    )
                elif self.command == "POST" and path == "/api/prepare-inputs" and editor.execution:
                    if not isinstance(body, dict) or set(body) not in (
                        {"pipeline", "image_path"},
                        {"pipeline", "image_ref"},
                        {"pipeline", "observations_ref"},
                        {"pipeline", "input_refs"},
                    ):
                        raise ValueError(
                            "input preparation requires pipeline and exactly one input source"
                        )
                    self.respond(
                        201,
                        editor.execution.prepare_inputs(
                            body["pipeline"],
                            **{key: value for key, value in body.items() if key != "pipeline"},
                        ),
                    )
                elif self.command == "POST" and path == "/api/runs" and editor.execution:
                    if not isinstance(body, dict) or set(body) - {
                        "idempotency_key",
                        "reuse_source",
                        "preflight_digest",
                    } not in (
                        {"pipeline", "image_path"},
                        {"pipeline", "image_ref"},
                        {"pipeline", "observations_ref"},
                        {"pipeline", "input_refs"},
                    ):
                        raise ValueError("run requires pipeline and exactly one input source")
                    options: dict[str, Any] = {}
                    if "idempotency_key" in body:
                        if not isinstance(body["idempotency_key"], str):
                            raise ValueError("idempotency_key must be text")
                        options["idempotency_key"] = body["idempotency_key"]
                    if "preflight_digest" in body:
                        options["preflight_digest"] = body["preflight_digest"]
                    if "reuse_source" in body:
                        options["reuse_source"] = body["reuse_source"]
                    if "input_refs" in body:
                        value = editor.execution.start(
                            body["pipeline"], input_refs=body["input_refs"], **options
                        )
                    elif "observations_ref" in body:
                        value = editor.execution.start(
                            body["pipeline"], observations_ref=body["observations_ref"], **options
                        )
                    elif "image_ref" in body:
                        value = editor.execution.start(
                            body["pipeline"], image_ref=body["image_ref"], **options
                        )
                    else:
                        value = editor.execution.start(
                            body["pipeline"], body["image_path"], **options
                        )
                    self.respond(202, value)
                elif self.command == "POST" and path.startswith("/api/runs/") and editor.execution:
                    parts = path.removeprefix("/api/runs/").split("/")
                    if len(parts) != 2 or not isinstance(body, dict):
                        raise ValueError("invalid run action")
                    run_id, action = parts
                    if "node_id" in body and (
                        not isinstance(body["node_id"], str) or not body["node_id"]
                    ):
                        raise ValueError("node_id must be nonempty text")
                    if action == "resume" and set(body) == {"expected_revision"}:
                        value = editor.execution.resume(run_id, body["expected_revision"])
                    elif action == "retry" and set(body) == {"node_id", "expected_revision"}:
                        value = editor.execution.retry(
                            run_id, body["node_id"], body["expected_revision"]
                        )
                    elif action == "reuse-decision" and set(body) in (
                        {"node_id", "expected_revision", "source_run_id"},
                        {
                            "node_id",
                            "expected_revision",
                            "source_run_id",
                            "source_snapshot",
                            "confirm",
                            "idempotency_key",
                            "reviewer",
                        },
                    ):
                        value = editor.execution.reuse_decision(run_id, body)
                    elif action == "review" and set(body) == {"node_id"}:
                        value = editor.execution.review(run_id, body["node_id"])
                    else:
                        raise ValueError("unknown action or invalid fields")
                    self.respond(202, value)
                elif self.command == "PUT" and path.startswith("/api/drafts/"):
                    self.respond(200, editor.save(path.removeprefix("/api/drafts/"), body))
                else:
                    self.respond(404, {"error": "unknown route"})
            except PreflightChanged as error:
                self.respond(
                    409,
                    {"error": str(error), "code": "preflight_changed", "preflight": error.report},
                )
            except (
                ValueError,
                OSError,
                AttributeError,
                KeyError,
                RuntimeError,
                TypeError,
            ) as error:
                self.respond(400, {"error": str(error)})

        do_POST = mutate
        do_PUT = mutate

    return ThreadingHTTPServer((bind_host, port), Handler)


def serve_editor(
    directory: Path,
    port: int,
    operators: Path | None,
    templates: list[Path],
    *,
    config: Path | None = None,
    store: Path | None = None,
    profile: str | None = None,
    multi_view_config: Path | None = None,
    remote_config: Path | None = None,
    comfy_config: Path | None = None,
    proposal_config: Path | None = None,
    host: str = "127.0.0.1",
) -> None:
    from contextlib import ExitStack
    from importlib.resources import as_file

    with ExitStack() as stack:
        if not templates:
            templates = [
                stack.enter_context(
                    as_file(files("assets_generator.resources").joinpath("cpu-image-editor.yaml"))
                )
            ]
        execution = None
        editor = DraftEditor(directory, operators, templates)
        if any(
            value is not None
            for value in (
                config,
                store,
                profile,
                multi_view_config,
                remote_config,
                proposal_config,
                comfy_config,
            )
        ):
            if store is None:
                raise ValueError("execution requires --store")
            if proposal_config is not None and config is not None:
                raise ValueError("choose either image config or proposal config")
            if (config is None and proposal_config is None) != (profile is None):
                raise ValueError(
                    "local execution requires --profile with --config or --proposal-config"
                )
            from .artifact_store import LocalArtifactStore
            from .dag_engine import DagEngine
            from .dag_persistence import DagRepository
            from .dag_profiles import image_adapter_registry
            from .serialization import read_json
            from .workbench_profiles import load_profiles

            registry = AdapterRegistry()
            if config is not None and profile is not None:
                profiles = load_profiles(
                    read_json(config),
                    progress=lambda message: print(message, file=sys.stderr, flush=True),
                )
                registry = image_adapter_registry(profiles, profile)
            if proposal_config is not None:
                from .dag_profiles import proposal_adapter_registry
                from .workbench_profiles import load_proposal_profiles

                proposal_profiles = load_proposal_profiles(
                    read_json(proposal_config),
                    progress=lambda message: print(message, file=sys.stderr, flush=True),
                )
                assert profile is not None
                registry = proposal_adapter_registry(proposal_profiles, profile)
            if multi_view_config is not None:
                from .dag_profiles import register_multi_view_profiles
                from .multi_view_profiles import load_multi_view_profile

                raw = read_json(multi_view_config)
                if set(raw) != {"default_profile", "profiles"} or not isinstance(
                    raw["profiles"], dict
                ):
                    raise ValueError("multi-view config requires default_profile and profiles")
                configured = {
                    name: load_multi_view_profile(value) for name, value in raw["profiles"].items()
                }
                register_multi_view_profiles(registry, configured, raw["default_profile"])
            if remote_config is not None:
                from .dag_remote_profiles import register_remote_shape_profiles

                register_remote_shape_profiles(registry, read_json(remote_config))
            if comfy_config is not None:
                from .dag_comfy_profiles import register_comfy_profiles

                register_comfy_profiles(registry, read_json(comfy_config), base=comfy_config.parent)
            # Comfy profiles already register these shared CPU adapters. Custom
            # Operator catalogs only expose utilities whose contracts they contain.
            if comfy_config is None:
                from .dag_image_encoding import EncodePngAdapter
                from .dag_image_mask import ApplyBinaryMaskAdapter
                from .dag_image_resize import ResizeImageAdapter

                for utility in (EncodePngAdapter(), ResizeImageAdapter(), ApplyBinaryMaskAdapter()):
                    if utility.spec.operators[0] in editor.specs:
                        registry.register(utility)
            repository = DagRepository(LocalArtifactStore(store), directory / "runtime")
            stack.enter_context(repository)
            execution = NodeEditorExecution(
                DagEngine(repository, registry, editor.relations),
                specs=editor.specs,
                relations=editor.relations,
            )
            stack.callback(execution.close)
            editor = DraftEditor(
                directory,
                operators,
                templates,
                execution=execution,
                execution_profile=(
                    "CPU · 未配置模型"
                    if all(
                        value is None
                        for value in (
                            config,
                            proposal_config,
                            multi_view_config,
                            remote_config,
                            comfy_config,
                        )
                    )
                    else profile
                ),
            )
        server = (
            create_editor_server(editor, port)
            if host == "127.0.0.1"
            else create_editor_server(editor, port, host=host)
        )
        stack.callback(server.server_close)
        print(f"http://{host}:{server.server_port}/ (Ctrl+C 关闭)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
