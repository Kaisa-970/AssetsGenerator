import io
import json
from copy import deepcopy
from dataclasses import replace

import numpy as np
import pytest
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import ContractError, OperatorSpec, PortSpec, RelationSpec
from assets_generator.models import StructuredValue
from assets_generator.multi_view_relations import register_multi_view_relations
from assets_generator.observation_import import import_observation_manifest
from assets_generator.relations import default_relation_registry
from assets_generator.serialization import to_primitive


def spec():
    return OperatorSpec(
        "dag_reconstruction",
        "1",
        {
            "observations": PortSpec(("observation_bundle",), carriers=("artifact_ref",)),
            "cameras": PortSpec(
                ("camera_record",), cardinality="one_or_more", carriers=("structured",)
            ),
            "depths": PortSpec(
                ("depth_map",),
                cardinality="zero_or_more",
                carriers=("artifact_ref",),
                requires_frame=True,
                requires_unit=True,
            ),
            "points": PortSpec(
                ("point_cloud",),
                carriers=("artifact_ref",),
                requires_frame=True,
                requires_unit=True,
            ),
            "geometry_evidence": PortSpec(("quality_evidence",), carriers=("artifact_ref",)),
        },
        {},
        (
            RelationSpec(
                "multi_view_reconstruction@1",
                ("observations", "cameras", "depths", "points", "geometry_evidence"),
            ),
        ),
    )


def bind_evidence(store, values):
    values["geometry_evidence"] = store.persist_structured(
        StructuredValue(
            "quality_evidence",
            "GeometryFrontendEvidence",
            "1.0",
            {
                **{
                    key: to_primitive(values[key])
                    for key in ("observations", "cameras", "depths", "points")
                },
                "backend_metadata": {"backend_version": "test"},
            },
        )
    )


@pytest.fixture
def inputs(tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    Image.new("RGB", (8, 6), "red").save(tmp_path / "front.png")
    path = tmp_path / "observations.json"
    path.write_text(json.dumps({"views": [{"view_id": "front", "image": "front.png"}]}))
    observations = import_observation_manifest(path, store)
    camera = StructuredValue(
        "camera_record",
        "CameraRecord",
        "1.0",
        {
            "camera_id": "front_camera",
            "image_view_id": "front",
            "model": "pinhole",
            "width": 8,
            "height": 6,
            "fx": 10.0,
            "fy": 10.0,
            "cx": 4.0,
            "cy": 3.0,
            "distortion": [],
            "camera_frame_id": "front_camera",
            "source": "estimated",
            "T_world_camera": {
                "source_frame_id": "front_camera",
                "target_frame_id": "world",
                "matrix": np.eye(4).tolist(),
            },
        },
    )
    stream = io.BytesIO()
    Image.fromarray(np.ones((6, 8), dtype=np.float32)).save(stream, format="TIFF")
    depth = store.persist_bytes(
        stream.getvalue(),
        kind="depth_map",
        schema_name="DepthMap",
        schema_version="1.0",
        identity_metadata={"view_id": "front", "frame_id": "front_camera", "unit": "relative_unit"},
    )
    points = store.persist_bytes(
        b"points",
        kind="point_cloud",
        schema_name="Points",
        schema_version="1.0",
        identity_metadata={"frame_id": "world", "unit": "relative_unit"},
    )
    values = {
        "observations": observations,
        "cameras": [camera],
        "depths": [depth],
        "points": points,
    }
    bind_evidence(store, values)
    return store, values


def validate(store, values, operator=None):
    registry = default_relation_registry()
    register_multi_view_relations(registry)
    operator = operator or spec()
    relations = registry.validate_static(
        operator,
        {key: f"source.outputs.{key}" for key in operator.inputs},
        require_explicit_joins=True,
    )
    registry.validate_runtime(operator, values, store, resolved_relations=relations)


def replace_metadata(store, ref, **changes):
    identity = store.get_manifest(ref.artifact_id).identity
    return store.persist_bytes(
        store.blob_path(ref).read_bytes(),
        kind=identity.kind,
        schema_name=identity.schema_name,
        schema_version=identity.schema_version,
        identity_metadata={**identity.identity_metadata, **changes},
    )


def test_valid_spatial_join_and_empty_optional_depths(inputs):
    store, values = inputs
    validate(store, values)
    values["depths"] = []
    bind_evidence(store, values)
    validate(store, values)


@pytest.mark.parametrize(
    "field,change,message",
    [
        ("depths", {"frame_id": "wrong_camera"}, "camera frame"),
        ("depths", {"unit": "meter"}, "common unit"),
        ("depths", {"view_id": "other"}, "unknown view"),
        ("points", {"frame_id": "other_world"}, "world frame"),
        ("points", {"unit": "unknown"}, "common unit"),
    ],
)
def test_spatial_mismatch_rejected_even_when_evidence_agrees(inputs, field, change, message):
    store, values = inputs
    if field == "depths":
        values[field] = [replace_metadata(store, values[field][0], **change)]
    else:
        values[field] = replace_metadata(store, values[field], **change)
    bind_evidence(store, values)
    with pytest.raises(ContractError, match=message):
        validate(store, values)


@pytest.mark.parametrize("field", ["observations", "cameras", "depths", "points"])
def test_swapped_inputs_rejected_by_fixed_evidence(inputs, field):
    store, values = inputs
    if field == "cameras":
        camera = values[field][0]
        values[field] = [replace(camera, value={**camera.value, "fx": 11.0})]
    elif field == "depths":
        values[field] = []
    else:
        values[field] = replace_metadata(store, values[field], source_variant="other")
    with pytest.raises(ContractError, match="fixed geometry evidence"):
        validate(store, values)


def test_depths_and_cameras_cannot_duplicate_views(inputs):
    store, values = inputs
    for field, message in (
        ("cameras", "duplicate camera"),
        ("depths", "duplicate geometry frontend depth"),
    ):
        changed = deepcopy(values)
        changed[field] *= 2
        bind_evidence(store, changed)
        with pytest.raises(ContractError, match=message):
            validate(store, changed)


@pytest.mark.parametrize("field", ["points", "depths", "geometry_evidence"])
def test_missing_evidence_rejected_without_repair(inputs, field):
    store, values = inputs
    reference = values[field][0] if field == "depths" else values[field]
    path = store.blob_path(reference)
    path.unlink()
    with pytest.raises(ContractError, match="invalid digest"):
        validate(store, values)
    assert not path.exists()


def test_relation_does_not_change_existing_default_identity():
    registry = default_relation_registry()
    before = registry.resolve("independent_inputs@1").spec
    register_multi_view_relations(registry)
    assert registry.resolve("independent_inputs@1").spec == before
    with pytest.raises(ContractError, match="unknown relation"):
        default_relation_registry().resolve("multi_view_reconstruction@1")


def test_relation_requires_explicit_evidence_binding(inputs):
    registry = default_relation_registry()
    register_multi_view_relations(registry)
    with pytest.raises(ContractError, match="all reconstruction bindings"):
        registry.validate_static(
            spec(),
            {key: "node.outputs.value" for key in spec().inputs if key != "geometry_evidence"},
        )


def release_spec():
    inputs = {
        "observations": spec().inputs["observations"],
        "geometry_evidence": spec().inputs["geometry_evidence"],
        "reconstruction_evidence": spec().inputs["geometry_evidence"],
    }
    return OperatorSpec(
        "dag_multi_view_release",
        "1",
        inputs,
        {},
        (RelationSpec("multi_view_release@1", tuple(inputs)),),
    )


def release_values(store, values, **overrides):
    reconstruction = store.persist_structured(
        StructuredValue(
            "quality_evidence",
            "ReconstructionEvidence",
            "1.0",
            {
                "observations": to_primitive(values["observations"]),
                "geometry_evidence": to_primitive(values["geometry_evidence"]),
                **overrides,
            },
        )
    )
    return {key: values[key] for key in ("observations", "geometry_evidence")} | {
        "reconstruction_evidence": reconstruction
    }


def test_release_join_validates_fixed_chain(inputs):
    store, values = inputs
    validate(store, release_values(store, values), release_spec())


@pytest.mark.parametrize("field", ["observations", "geometry_evidence"])
def test_release_rejects_crossed_lineage(inputs, field):
    store, values = inputs
    changed = release_values(store, values, **{field: {"artifact_id": "sha256:" + "f" * 64}})
    with pytest.raises(ContractError, match="mismatch"):
        validate(store, changed, release_spec())


def test_release_rejects_missing_nested_geometry(inputs):
    store, values = inputs
    outputs = release_values(store, values)
    store.blob_path(values["points"]).unlink()
    with pytest.raises(ContractError, match="invalid digest"):
        validate(store, outputs, release_spec())


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"width": 9}, "dimensions"),
        ({"image_view_id": "unknown"}, "unknown view"),
        ({"T_world_camera": None}, "transforms into one common world"),
    ],
)
def test_invalid_camera_relations(inputs, changes, message):
    store, values = inputs
    camera = values["cameras"][0]
    values["cameras"] = [replace(camera, value={**camera.value, **changes})]
    bind_evidence(store, values)
    with pytest.raises(ContractError, match=message):
        validate(store, values)


def test_unknown_unit_rejected_without_depths(inputs):
    store, values = inputs
    values["depths"] = []
    values["points"] = replace_metadata(store, values["points"], unit="unknown")
    bind_evidence(store, values)
    with pytest.raises(ContractError, match="meter or relative_unit"):
        validate(store, values)
