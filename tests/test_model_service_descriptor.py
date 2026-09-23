"""Discovery is bounded read-only data; adapters keep authoritative mesh ports."""

from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import ContractError
from assets_generator.dag_adapters import NodeExecutionContext
from assets_generator.model_service_descriptor import (
    DiscoveredShapeAdapter,
    detect_service,
    shape_service_descriptor,
    validate_descriptor,
)
from assets_generator.remote_http import RemoteTransportUnknown
from assets_generator.remote_protocol import RemoteIdentity
from assets_generator.remote_service_http import create_remote_server
from assets_generator.remote_service_store import RemoteServiceStore
from assets_generator.serialization import canonical_json_bytes, sha256_bytes


@pytest.fixture
def descriptor():
    return {
        "schema_version": "model_service@1",
        "display_name": "Example Mesh",
        "service_id": "example",
        "backend_digest": "sha256:" + "a" * 64,
        "operator": "shape_generation@1",
        "transport": "remote_jobs@1",
        "frame_id": "model-native",
        "up_axis": "+Y",
        "unit": "relative_unit",
        "parameter_schema": {
            "type": "object",
            "properties": {
                "steps": {"type": "integer", "minimum": 1, "maximum": 100},
                "quality": {"type": "string", "enum": ["fast", "best"]},
            },
            "required": ["steps"],
        },
        "defaults": {"steps": 12, "quality": "fast"},
    }


@contextmanager
def listening(server):
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_round_trip_and_copy(descriptor):
    normalized = validate_descriptor(descriptor)
    assert normalized == descriptor
    descriptor["defaults"]["steps"] = 99
    assert normalized["defaults"]["steps"] == 12


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "model_service@2"),
        ("operator", "other@1"),
        ("transport", "python@1"),
        ("service_id", "../unsafe"),
        ("backend_digest", "bad"),
        ("display_name", ""),
        ("display_name", "a" * 129),
        ("defaults", {"steps": 0}),
        ("defaults", {"undeclared": True}),
        ("parameter_schema", {"type": "string"}),
    ],
)
def test_reject_invalid_descriptor(descriptor, field, value):
    descriptor[field] = value
    with pytest.raises(ValueError):
        validate_descriptor(descriptor)


def test_reject_unknown_fields_open_parameters_and_reserved(descriptor):
    for key in ("remote_endpoint", "service_id", "backend_digest"):
        changed = deepcopy(descriptor)
        changed["parameter_schema"]["properties"][key] = {"type": "string"}
        with pytest.raises(ContractError, match="reserved"):
            validate_descriptor(changed)
    descriptor["parameter_schema"]["additionalProperties"] = True
    with pytest.raises(ContractError, match="closed"):
        validate_descriptor(descriptor)
    descriptor["unknown"] = True
    with pytest.raises(ContractError, match="unknown fields"):
        validate_descriptor(descriptor)


def test_actual_http_discovery_without_job_and_immutable_descriptor(tmp_path, descriptor):
    identity = RemoteIdentity(descriptor["service_id"], descriptor["backend_digest"])
    store = RemoteServiceStore(tmp_path / "service.sqlite", identity)
    expected = deepcopy(descriptor)
    server = create_remote_server(store, descriptor=descriptor)
    descriptor["defaults"]["steps"] = 90
    try:
        with listening(server) as endpoint:
            found = detect_service(endpoint + "/")
            assert found == {
                "endpoint": endpoint,
                "descriptor": expected,
                "descriptor_digest": sha256_bytes(canonical_json_bytes(expected)),
            }
            assert store.list_jobs()["jobs"] == []
    finally:
        store.close()


def test_server_refuses_mismatched_identity(tmp_path, descriptor):
    store = RemoteServiceStore(
        tmp_path / "service.sqlite", RemoteIdentity("other", "sha256:" + "a" * 64)
    )
    try:
        with pytest.raises(ValueError, match="store identity"):
            create_remote_server(store, descriptor=descriptor)
    finally:
        store.close()


@pytest.mark.parametrize(
    "status,media,body",
    [
        (302, "application/json", b"{}"),
        (200, "text/html", b"{}"),
        (200, "application/json", b" " * (64 * 1024 + 1)),
        (200, "application/json", b'{"x":1,"x":2}'),
        (404, "application/json", b"{}"),
    ],
)
def test_detection_rejects_bad_transport(status, media, body):
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(status)
            self.send_header("Content-Type", media)
            if status == 302:
                self.send_header("Location", "/redirected")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with listening(ThreadingHTTPServer(("127.0.0.1", 0), Handler)) as endpoint:
        with pytest.raises(RemoteTransportUnknown):
            detect_service(endpoint)
    assert paths == ["/v1/service-descriptor"]


def test_declared_parameters_reach_payload_and_fixed_identity_cannot_change(tmp_path, descriptor):
    adapter = DiscoveredShapeAdapter("http://127.0.0.1:8780", descriptor)
    parameters = adapter.spec.normalize_parameters({"steps": 24, "quality": "best"})
    context = NodeExecutionContext(
        "run", "shape", {}, parameters, LocalArtifactStore(tmp_path / "store")
    )
    assert adapter.prepare_payload(context) == {
        "operation": "shape_generation@1",
        "parameters": {"steps": 24, "quality": "best"},
    }
    assert adapter.spec.operators == ("shape_generation@1",)
    assert adapter.spec.defaults["service_id"] == "example"
    with pytest.raises(ContractError):
        adapter.spec.normalize_parameters({"service_id": "other"})
    with pytest.raises(ContractError):
        adapter.spec.normalize_parameters({"seed": 42})


def test_existing_shape_service_descriptor_matches_handler():
    raw = shape_service_descriptor(RemoteIdentity("shape", "sha256:" + "b" * 64), "My model")
    assert raw["defaults"] == {"seed": 42, "pipeline_type": "512"}
    assert set(raw["parameter_schema"]["properties"]) == {"seed", "pipeline_type"}


def test_verified_frame_and_ineffective_parameters_are_explicit(tmp_path):
    from assets_generator.models import BackendNativeFrame

    identity = RemoteIdentity("triposr", "sha256:" + "c" * 64)
    frame = BackendNativeFrame(
        "triposr_glb_native", "right", "+Z", None, "unknown", "relative_unit"
    )
    raw = shape_service_descriptor(identity, "TripoSR", frame=frame, fixed_parameters=True)
    assert (raw["frame_id"], raw["up_axis"], raw["unit"]) == (
        "triposr_glb_native",
        "+Z",
        "relative_unit",
    )
    adapter = DiscoveredShapeAdapter("http://127.0.0.1:8780", raw)
    parameters = adapter.spec.normalize_parameters({})
    context = NodeExecutionContext(
        "run", "shape", {}, parameters, LocalArtifactStore(tmp_path / "store")
    )
    assert adapter.prepare_payload(context)["parameters"] == {"seed": 42, "pipeline_type": "512"}
    for changed in ({"seed": 43}, {"pipeline_type": "1024"}):
        with pytest.raises(ContractError):
            adapter.spec.normalize_parameters(changed)
    with pytest.raises(ValueError, match="up axis"):
        shape_service_descriptor(
            identity,
            "Invalid",
            frame=BackendNativeFrame(
                "native", "right", "invalid", None, "unknown", "relative_unit"
            ),
        )


@pytest.mark.parametrize("backend,axis", [("triposr", "+Z"), ("trellis2", "+Y")])
def test_cli_serve_discovers_verified_profile_frame(tmp_path, monkeypatch, backend, axis):
    from types import SimpleNamespace

    from assets_generator import remote_shape_cli as cli

    identity = RemoteIdentity("service", "sha256:" + "d" * 64)
    monkeypatch.setattr(
        cli,
        "load_shape_profiles",
        lambda *_args, **_kwargs: {"model": SimpleNamespace(shape_identity={"backend": backend})},
    )
    monkeypatch.setattr(
        cli,
        "shape_handler_from_profile",
        lambda *_args, **_kwargs: SimpleNamespace(identity=identity),
    )
    captured = []

    class Server:
        server_port = 8780

        def serve_forever(self):
            pass

        def server_close(self):
            pass

    def create(store, *, port, descriptor):
        captured.append(descriptor)
        return Server()

    monkeypatch.setattr(cli, "create_remote_server", create)
    config = tmp_path / "config.json"
    config.write_text("{}")
    assert (
        cli.main(
            [
                "serve",
                "--config",
                str(config),
                "--profile",
                "model",
                "--service-id",
                "service",
                "--database",
                str(tmp_path / "jobs.sqlite"),
                "--workspace",
                str(tmp_path / "work"),
            ]
        )
        == 0
    )
    raw = captured[0]
    assert (raw["frame_id"], raw["up_axis"], raw["unit"]) == (
        f"{backend}_glb_native",
        axis,
        "relative_unit",
    )
    adapter = DiscoveredShapeAdapter("http://127.0.0.1:8780", raw)
    if backend == "trellis2":
        assert adapter.spec.normalize_parameters({"seed": 7, "pipeline_type": "1024"})["seed"] == 7
    else:
        with pytest.raises(ContractError):
            adapter.spec.normalize_parameters({"seed": 7})
