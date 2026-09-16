import pytest

from assets_generator.cli import _parser, _shape_registry
from assets_generator.contracts import ContractError
from assets_generator.operators import TripoSRBackend


def test_shape_backend_only_overrides_pipeline_when_explicit() -> None:
    parser = _parser()
    default = parser.parse_args(["build", "--image", "image.png", "--output", "output"])
    overridden = parser.parse_args(
        [
            "build",
            "--image",
            "image.png",
            "--output",
            "output",
            "--shape-backend",
            "triposr",
        ]
    )

    assert default.shape_backend is None
    assert overridden.shape_backend == "triposr"


def test_shape_registry_defaults_to_trellis_only() -> None:
    args = _parser().parse_args(["build", "--image", "image.png", "--output", "output"])

    registry = _shape_registry(args)

    assert registry.resolve("trellis2", "shape_generation@1").name == "trellis2"
    with pytest.raises(ContractError, match="backend is not registered: triposr"):
        registry.resolve("triposr", "shape_generation@1")


def test_shape_registry_requires_complete_triposr_configuration() -> None:
    args = _parser().parse_args(
        [
            "build",
            "--image",
            "image.png",
            "--output",
            "output",
            "--shape-backend",
            "triposr",
            "--triposr-python",
            "/env/bin/python",
        ]
    )

    with pytest.raises(ValueError, match="requires --triposr-python and --triposr-repo"):
        _shape_registry(args)


def test_shape_registry_registers_triposr_when_configured() -> None:
    args = _parser().parse_args(
        [
            "build",
            "--image",
            "image.png",
            "--output",
            "output",
            "--shape-backend",
            "triposr",
            "--triposr-python",
            "/env/bin/python",
            "--triposr-repo",
            "/repo/TripoSR",
            "--triposr-chunk-size",
            "4096",
            "--triposr-mc-resolution",
            "128",
        ]
    )

    registration = _shape_registry(args).resolve("triposr", "shape_generation@1")

    assert registration.name == "triposr"
    assert isinstance(registration.implementation, TripoSRBackend)
    assert registration.implementation.chunk_size == 4096
    assert registration.implementation.mc_resolution == 128
