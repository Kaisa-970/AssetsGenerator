from __future__ import annotations

import io
from dataclasses import replace

import pytest
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import ContractError
from assets_generator.models import CameraRecord, Confidence, ObservationView, SpatialTransform
from assets_generator.observations import (
    make_observation_bundle,
    observation_bundle_from_value,
    observation_bundle_value,
    validate_observation_bundle,
)


def _image(store: LocalArtifactStore, size=(8, 6)):
    output = io.BytesIO()
    Image.new("RGB", size, "red").save(output, format="PNG")
    return store.persist_bytes(
        output.getvalue(), kind="rgb_image", schema_name="png", schema_version="1.0"
    )


def _mask(store: LocalArtifactStore, size=(8, 6)):
    output = io.BytesIO()
    Image.new("L", size, 255).save(output, format="PNG")
    return store.persist_bytes(
        output.getvalue(), kind="binary_mask", schema_name="png", schema_version="1.0"
    )


def _raster(store: LocalArtifactStore, kind: str, mode: str, value) -> object:
    output = io.BytesIO()
    Image.new(mode, (8, 6), value).save(output, format="PNG")
    return store.persist_bytes(
        output.getvalue(),
        kind=kind,
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"frame_id": "camera_001", "unit": "millimeter"}
        if kind == "depth_map"
        else {},
    )


def _depth(store: LocalArtifactStore, size=(8, 6), *, spatial=True):
    output = io.BytesIO()
    Image.new("I;16", size, 1000).save(output, format="PNG")
    return store.persist_bytes(
        output.getvalue(),
        kind="depth_map",
        schema_name="png-depth-u16",
        schema_version="1.0",
        identity_metadata={"frame_id": "camera_001", "unit": "millimeter"} if spatial else {},
    )


def _camera(view_id: str = "view_001") -> CameraRecord:
    return CameraRecord(
        "camera_001",
        view_id,
        "pinhole",
        8,
        6,
        10.0,
        10.0,
        4.0,
        3.0,
        [],
        "camera_001",
        SpatialTransform(
            "camera_001",
            "observation_world",
            [
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
        ),
        "provided",
    )


def test_multi_view_rgbd_bundle_has_stable_identity_and_round_trips(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    views = [
        ObservationView("view_000", _image(store), mask=_mask(store)),
        ObservationView("view_001", _image(store), depth=_depth(store), camera=_camera()),
    ]

    first = make_observation_bundle(views, store)
    second = make_observation_bundle(views, store)
    value = observation_bundle_value(first)

    assert first.observation_id == second.observation_id
    assert observation_bundle_from_value(value, store) == first
    assert store.read_structured(store.persist_structured(value))["observation_id"] == (
        first.observation_id
    )


def test_observation_identity_normalizes_numeric_camera_values(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    image = _image(store)
    floating = replace(_camera("view_000"), T_world_camera=None)
    integral = replace(
        floating,
        fx=10,  # type: ignore[arg-type]
        fy=10,  # type: ignore[arg-type]
        cx=4,  # type: ignore[arg-type]
        cy=3,  # type: ignore[arg-type]
    )

    first = make_observation_bundle([ObservationView("view_000", image, camera=floating)], store)
    second = make_observation_bundle([ObservationView("view_000", image, camera=integral)], store)

    assert first == second
    assert second.views[0].camera is not None
    assert isinstance(second.views[0].camera.fx, float)


def test_observation_bundle_rejects_tampered_artifact_blob(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    image = _image(store)
    store.blob_path(image).write_bytes(b"tampered")

    with pytest.raises(ContractError, match="invalid artifact digest"):
        make_observation_bundle([ObservationView("view_000", image)], store)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda view, store: replace(view, view_id="view_000"), "view_id values must be unique"),
        (lambda view, store: replace(view, mask=_mask(store, (4, 4))), "mask dimensions differ"),
        (
            lambda view, store: replace(view, depth=_depth(store, spatial=False)),
            "requires frame_id",
        ),
        (lambda view, store: replace(view, camera=_camera("other")), "references view other"),
        (
            lambda view, store: replace(view, camera=replace(_camera(), fx=float("nan"))),
            "non-finite calibration",
        ),
    ],
)
def test_observation_bundle_rejects_invalid_alignment(tmp_path, change, message) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    first = ObservationView("view_000", _image(store))
    second = ObservationView("view_001", _image(store), camera=_camera())

    with pytest.raises(ContractError, match=message):
        validate_observation_bundle(
            make_observation_bundle([first, second], store).__class__(
                "observation_test", [first, change(second, store)]
            ),
            store,
        )


@pytest.mark.parametrize(
    ("payload_change", "message"),
    [
        (lambda payload: {**payload, "views": "bad"}, "views must be a list"),
        (lambda payload: {**payload, "views": [None]}, "views\\[0\\] must be an object"),
        (lambda payload: {**payload, "observation_id": "observation_tampered"}, "does not match"),
    ],
)
def test_observation_bundle_from_value_rejects_malformed_payload(
    tmp_path, payload_change, message
) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    bundle = make_observation_bundle([ObservationView("view_000", _image(store))], store)
    value = observation_bundle_value(bundle)

    with pytest.raises(ContractError, match=message):
        observation_bundle_from_value(replace(value, value=payload_change(value.value)), store)


def test_observation_bundle_from_value_rejects_wrong_schema_version(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    bundle = make_observation_bundle([ObservationView("view_000", _image(store))], store)

    with pytest.raises(ContractError, match="expected ObservationBundle"):
        observation_bundle_from_value(
            replace(observation_bundle_value(bundle), schema_version="2.0"), store
        )


def test_observation_bundle_rejects_unsupported_camera_model(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    camera = replace(_camera("view_000"), model="fisheye")  # type: ignore[arg-type]

    with pytest.raises(ContractError, match="unsupported model"):
        make_observation_bundle([ObservationView("view_000", _image(store), camera=camera)], store)


@pytest.mark.parametrize("source", ["registered", "nonsense"])
def test_observation_bundle_rejects_invalid_camera_source(tmp_path, source) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    camera = replace(_camera("view_000"), source=source)

    with pytest.raises(ContractError, match="invalid source"):
        make_observation_bundle([ObservationView("view_000", _image(store), camera=camera)], store)


@pytest.mark.parametrize(
    ("view", "message"),
    [
        (
            lambda store: ObservationView("view_000", _raster(store, "rgb_image", "L", 128)),
            "rgb_image must use RGB mode",
        ),
        (
            lambda store: ObservationView(
                "view_000", _image(store), mask=_raster(store, "binary_mask", "L", 0)
            ),
            "must contain foreground pixels",
        ),
        (
            lambda store: ObservationView(
                "view_000", _image(store), mask=_raster(store, "binary_mask", "L", 128)
            ),
            "must contain only 0 and 255",
        ),
        (
            lambda store: ObservationView(
                "view_000", _image(store), depth=_raster(store, "depth_map", "RGB", "red")
            ),
            "single-channel numeric raster",
        ),
    ],
)
def test_observation_bundle_rejects_invalid_raster_semantics(tmp_path, view, message) -> None:
    store = LocalArtifactStore(tmp_path / "store")

    with pytest.raises(ContractError, match=message):
        make_observation_bundle([view(store)], store)


def test_observation_bundle_rejects_duplicate_camera_ids(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    first = replace(
        _camera("front"),
        camera_id="shared",
        camera_frame_id="front_camera",
        T_world_camera=replace(_camera("front").T_world_camera, source_frame_id="front_camera"),
    )
    second = replace(
        _camera("side"),
        camera_id="shared",
        camera_frame_id="side_camera",
        T_world_camera=replace(_camera("side").T_world_camera, source_frame_id="side_camera"),
    )

    with pytest.raises(ContractError, match="camera_id values must be unique"):
        make_observation_bundle(
            [
                ObservationView("front", _image(store), camera=first),
                ObservationView("side", _image(store), camera=second),
            ],
            store,
        )


def test_observation_bundle_rejects_non_rigid_camera_transform(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    camera = replace(
        _camera("view_000"),
        T_world_camera=SpatialTransform(
            "camera_001",
            "observation_world",
            [
                [2.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
        ),
    )

    with pytest.raises(ContractError, match="transform must be rigid"):
        make_observation_bundle([ObservationView("view_000", _image(store), camera=camera)], store)


def test_observation_bundle_rejects_non_string_confidence_method(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    camera = replace(
        _camera("view_000"),
        confidence=Confidence(0.8, 1, "v1", None, []),  # type: ignore[arg-type]
    )

    with pytest.raises(ContractError, match="requires method and method_version"):
        make_observation_bundle([ObservationView("view_000", _image(store), camera=camera)], store)
