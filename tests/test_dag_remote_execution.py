import io

import pytest
from PIL import Image
from test_dag_engine import CopyAdapter
from test_remote_http import server as _server_fixture

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import OperatorSpec, PortSpec, RelationSpec
from assets_generator.dag_adapters import AdapterRegistry, AdapterSpec, NodeExecutionResult
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.dag_remote_adapter import RemoteNodeAdapter
from assets_generator.pipeline import PipelineDefinition, compile_pipeline
from assets_generator.serialization import sha256_bytes


@pytest.fixture
def http_server():
    yield from _server_fixture.__wrapped__()


class ImageAdapter(RemoteNodeAdapter):
    def __init__(self, endpoint):
        values = {
            "remote_endpoint": endpoint,
            "service_id": "service",
            "backend_digest": "sha256:" + "c" * 64,
        }
        self._spec = AdapterSpec(
            "remote_image",
            "1",
            ("copy@1",),
            execution_kind="remote",
            parameter_schema={
                "type": "object",
                "properties": {k: {"type": "string", "enum": [v]} for k, v in values.items()},
            },
            defaults=values,
        )

    @property
    def spec(self):
        return self._spec

    def input_blobs(self, context):
        return {"image": context.inputs["image"]}

    def prepare_payload(self, context):
        return {"operation": "image_fixture"}

    def import_result(self, context, job, blobs):
        data = blobs["mesh"]
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            assert image.format == "PNG" and image.size == (2, 2)
        ref = context.store.persist_bytes(
            data,
            kind="rgb_image",
            schema_name="raster_image",
            schema_version="1.0",
            identity_metadata={"media_type": "image/png"},
        )
        return NodeExecutionResult({"image": ref})


def setup(tmp_path, endpoint):
    store = LocalArtifactStore(tmp_path / "store")
    registry = AdapterRegistry()
    local = CopyAdapter()
    registry.register(local)
    registry.register(ImageAdapter(endpoint))
    port = PortSpec(("rgb_image",))
    specs = {
        "copy@1": OperatorSpec("copy", "1", {"image": port}, {"image": port}),
        "join@1": OperatorSpec(
            "join",
            "1",
            {"left": port, "right": port},
            {"image": port},
            (RelationSpec("independent_inputs@1", ("left", "right")),),
        ),
    }
    nodes = {
        "A": {
            "operator": "copy@1",
            "adapter": "copy@1",
            "inputs": {"image": "pipeline.inputs.source"},
        },
        "B": {
            "operator": "copy@1",
            "adapter": "remote_image@1",
            "inputs": {"image": "A.outputs.image"},
        },
        "C": {"operator": "copy@1", "adapter": "copy@1", "inputs": {"image": "A.outputs.image"}},
        "D": {
            "operator": "join@1",
            "adapter": "copy@1",
            "inputs": {"left": "B.outputs.image", "right": "C.outputs.image"},
        },
    }
    plan = registry.bind_plan(
        compile_pipeline(
            PipelineDefinition("remote_diamond", "1", {"source": port}, nodes),
            specs,
            require_explicit_joins=True,
        )
    )
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
    data = buffer.getvalue()
    source = store.persist_bytes(
        data,
        kind="rgb_image",
        schema_name="raster_image",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png"},
    )
    return store, registry, local, plan, source, data


@pytest.mark.parametrize("drop", [False, True])
def test_remote_diamond_wait_reopen_and_import(tmp_path, http_server, drop):
    state, client = http_server
    store, registry, local, plan, source, data = setup(tmp_path, client.endpoint)
    state["drop"] = drop
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"source": source}).run_id)
        assert run.dag.node_states["B"].status == "running"
        assert run.dag.node_states["C"].status == "succeeded"
        assert run.dag.node_states["D"].status == "pending"
        with pytest.raises(ValueError):
            engine.retry(run.run_id, "B", run.dag.revision)
        assert state["submissions"] == 1
        assert state["uploads"] == [data]
    job = next(iter(state["jobs"].values()))
    state["blob"] = data
    state["media"] = "image/png"
    job.update(
        state="succeeded",
        result={
            "outputs": [
                {
                    "output_id": "mesh",
                    "blob_digest": sha256_bytes(data),
                    "byte_length": len(data),
                    "media_type": "image/png",
                }
            ]
        },
    )
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(run.run_id)
        assert run.status == "succeeded"
        assert all(len(n.attempts) == 1 for n in run.dag.node_states.values())
        assert [name for name, _ in local.calls] == ["A", "C", "D"]
        assert state["submissions"] == 1
        before = run.dag.node_states
        state["mode"] = "error"
        assert engine.recover(run.run_id).dag.node_states == before


def test_confirmed_failure_retains_error_and_allows_explicit_retry(tmp_path, http_server):
    state, client = http_server
    store, registry, local, plan, source, _ = setup(tmp_path, client.endpoint)
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"source": source}).run_id)
        next(iter(state["jobs"].values())).update(
            state="failed", error={"code": "BACKEND_TIMEOUT", "detail": "model deadline"}
        )
        run = engine.drain(run.run_id)
        attempt = run.dag.node_states["B"].current()
        assert attempt.error_code == "BACKEND_TIMEOUT"
        assert attempt.error_detail == "model deadline"
        assert run.dag.node_states["D"].status == "blocked"
        assert attempt.remote_result is not None
        run = engine.retry(run.run_id, "B", run.dag.revision)
        assert len(run.dag.node_states["B"].attempts) == 2
        assert state["submissions"] == 2
        assert [name for name, _ in local.calls] == ["A", "C"]


def test_invalid_result_blocks_without_new_submission(tmp_path, http_server):
    state, client = http_server
    store, registry, _, plan, source, _ = setup(tmp_path, client.endpoint)
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"source": source}).run_id)
        next(iter(state["jobs"].values())).update(state="succeeded", result={"outputs": []})
        run = engine.drain(run.run_id)
        assert run.dag.node_states["B"].status == "recovery_blocked"
        with pytest.raises(ValueError):
            engine.retry(run.run_id, "B", run.dag.revision)
        assert state["submissions"] == 1
        assert len(repo.load(run.run_id).dag.node_states["B"].attempts) == 1


def test_corrupt_completed_remote_output_never_reimports(tmp_path, http_server):
    state, client = http_server
    store, registry, _, plan, source, _ = setup(tmp_path, client.endpoint)
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "blue").save(buffer, format="PNG")
    data = buffer.getvalue()
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"source": source}).run_id)
        state.update(blob=data, media="image/png")
        next(iter(state["jobs"].values())).update(
            state="succeeded",
            result={
                "outputs": [
                    {
                        "output_id": "mesh",
                        "blob_digest": sha256_bytes(data),
                        "byte_length": len(data),
                        "media_type": "image/png",
                    }
                ]
            },
        )
        run = engine.drain(run.run_id)
        assert run.status == "succeeded"
        output = run.dag.node_states["B"].current().outputs["image"]
        store.blob_path(output).unlink()
        downloads = state["downloads"]
        for _ in range(2):
            run = engine.recover(run.run_id)
            assert run.dag.node_states["B"].status == "recovery_blocked"
            assert not store.blob_path(output).exists()
            assert state["downloads"] == downloads


def test_upload_receipt_failure_never_submits_or_reuploads_on_recovery(tmp_path, http_server):
    state, client = http_server
    store, registry, _, plan, source, data = setup(tmp_path, client.endpoint)
    state["bad_receipt"] = True
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"source": source}).run_id)
        assert run.dag.node_states["B"].status == "running"
        assert run.dag.node_states["C"].status == "succeeded"
        assert state["submissions"] == 0
        assert state["uploads"] == [data]
        engine.recover(run.run_id)
        assert state["submissions"] == 0
        assert state["uploads"] == [data]


@pytest.mark.parametrize("crash_point", ["before_import", "before_parent_save"])
def test_actual_remote_transform_recovers_import_crash_same_job(
    tmp_path, http_server, monkeypatch, crash_point
):
    state, client = http_server
    state["transform_image"] = True
    store, registry, _, plan, source, data = setup(tmp_path, client.endpoint)
    directory = tmp_path / "repo"

    class SimulatedCrash(BaseException):
        pass

    with DagRepository(store, directory) as repo:
        engine = DagEngine(repo, registry)
        run = engine.create(plan, {"source": source})
        if crash_point == "before_import":
            original = ImageAdapter.import_result

            def interrupt(*args, **kwargs):
                raise SimulatedCrash()

            monkeypatch.setattr(ImageAdapter, "import_result", interrupt)
        else:
            original_save = engine._save

            def interrupt_save(record):
                if record.dag.node_states["B"].status == "succeeded":
                    raise SimulatedCrash()
                original_save(record)

            monkeypatch.setattr(engine, "_save", interrupt_save)
        with pytest.raises(SimulatedCrash):
            engine.drain(run.run_id)
        saved = repo.load(run.run_id).dag.node_states["B"].current()
        assert saved.remote_result is not None
        assert saved.status == "running"
        if crash_point == "before_import":
            monkeypatch.setattr(ImageAdapter, "import_result", original)
    with DagRepository(store, directory) as repo:
        engine = DagEngine(repo, registry)
        completed = engine.drain(run.run_id)
        assert completed.status == "succeeded"
        attempt = completed.dag.node_states["B"].current()
        assert attempt.attempt == 1
        assert attempt.remote_binding == saved.remote_binding
        assert state["submissions"] == 1
        assert state["uploads"] == [data]
        with Image.open(store.blob_path(attempt.outputs["image"])) as output:
            assert output.getpixel((0, 0)) == (0, 255, 255)
        assert attempt.outputs["image"] != source
        provenance = store.read_structured(attempt.provenance["image"][0])
        assert attempt.remote_result.artifact_id in provenance["derived_from_artifact_ids"]
