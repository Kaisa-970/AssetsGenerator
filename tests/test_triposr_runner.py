from __future__ import annotations

import os

import numpy as np
import pytest
from PIL import Image

from assets_generator.backends.triposr_runner import configure_runtime_environment, prepare_image


def test_runtime_uses_request_local_numba_cache(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("NUMBA_CACHE_DIR", raising=False)

    configure_runtime_environment(tmp_path)

    assert os.environ["NUMBA_CACHE_DIR"] == str(tmp_path / "numba-cache")


def test_runtime_preserves_explicit_numba_cache(tmp_path, monkeypatch) -> None:
    configured = tmp_path / "configured-cache"
    monkeypatch.setenv("NUMBA_CACHE_DIR", str(configured))

    configure_runtime_environment(tmp_path / "request")

    assert os.environ["NUMBA_CACHE_DIR"] == str(configured)


def test_preprocessing_preserves_alpha_until_resize_then_composites_gray() -> None:
    pixels = np.array([[[200, 40, 60, 255], [255, 0, 0, 0], [200, 40, 60, 128]]], dtype=np.uint8)
    source = Image.fromarray(pixels, mode="RGBA")
    calls = []

    def resize(image: Image.Image, ratio: float) -> Image.Image:
        calls.append(ratio)
        assert image.mode == "RGBA"
        np.testing.assert_array_equal(np.asarray(image), pixels)
        padded = np.pad(np.asarray(image), ((1, 1), (0, 0), (0, 0)))
        return Image.fromarray(padded, mode="RGBA")

    prepared = prepare_image(source, 0.85, resize)

    assert calls == [0.85]
    assert prepared.mode == "RGB"
    assert prepared.size == (3, 3)
    actual = np.asarray(prepared)
    np.testing.assert_array_equal(actual[0], [[127, 127, 127]] * 3)
    np.testing.assert_array_equal(actual[1, 0], [200, 40, 60])
    np.testing.assert_array_equal(actual[1, 1], [127, 127, 127])
    np.testing.assert_array_equal(actual[1, 2], [163, 83, 93])
    np.testing.assert_array_equal(np.asarray(source), pixels)


def test_opaque_rgba_input_keeps_foreground_colors() -> None:
    source = Image.new("RGBA", (2, 2), (40, 80, 160, 255))
    prepared = prepare_image(source, 1.0, lambda image, ratio: image)
    assert prepared.mode == "RGB"
    np.testing.assert_array_equal(np.asarray(prepared), np.asarray(source)[:, :, :3])


def test_preprocessing_rejects_empty_alpha_before_upstream_resize() -> None:
    source = Image.new("RGBA", (2, 2), (40, 80, 160, 0))

    with pytest.raises(ValueError, match="no foreground pixels"):
        prepare_image(source, 0.85, lambda image, ratio: pytest.fail("must not resize"))


def test_preprocessing_rejects_empty_alpha_after_resize() -> None:
    source = Image.new("RGBA", (2, 2), (40, 80, 160, 255))

    with pytest.raises(ValueError, match="resized input alpha"):
        prepare_image(source, 0.85, lambda image, ratio: Image.new("RGBA", image.size))
