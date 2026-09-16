from assets_generator.cli import _parser


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
            "alternate",
        ]
    )

    assert default.shape_backend is None
    assert overridden.shape_backend == "alternate"
