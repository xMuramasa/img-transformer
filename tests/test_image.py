from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from tests._helpers import make_png, make_rgba_png
from transform import convert_image, normalize_format


def _open(data: bytes) -> Image.Image:
    img = Image.open(BytesIO(data))
    img.load()
    return img


def test_normalize_format_aliases():
    assert normalize_format("jpg") == "JPEG"
    assert normalize_format("JPEG") == "JPEG"
    assert normalize_format("webp") == "WEBP"


def test_normalize_format_rejects_unknown():
    with pytest.raises(ValueError):
        normalize_format("heic")


def test_convert_png_to_webp_no_resize():
    out = convert_image(make_png((50, 40)), target_format="webp")
    img = _open(out)
    assert img.format == "WEBP"
    assert img.size == (50, 40)


def test_resize_preserves_aspect_when_only_width():
    out = convert_image(make_png((100, 50)), target_format="png", width=20)
    img = _open(out)
    assert img.size == (20, 10)


def test_resize_preserves_aspect_when_only_height():
    out = convert_image(make_png((100, 50)), target_format="png", height=10)
    img = _open(out)
    assert img.size == (20, 10)


def test_resize_explicit_both_dims():
    out = convert_image(make_png((100, 50)), target_format="png", width=33, height=44)
    img = _open(out)
    assert img.size == (33, 44)


def test_rgba_png_to_jpeg_flattens_alpha():
    out = convert_image(make_rgba_png(), target_format="jpeg")
    img = _open(out)
    assert img.format == "JPEG"
    assert img.mode == "RGB"


def test_invalid_format_raises():
    with pytest.raises(ValueError):
        convert_image(make_png(), target_format="bogus")


def test_remove_bg_requires_alpha_format():
    with pytest.raises(ValueError, match="alpha-capable"):
        convert_image(make_png(), target_format="jpeg", remove_bg=True)


def test_remove_bg_missing_dep_raises_runtime():
    """When the optional 'bgremove' extra is not installed, asking to remove
    the background surfaces a clear error rather than silently succeeding."""
    pytest.importorskip  # noqa: B018 — sentinel for readers
    try:
        import rembg  # noqa: F401
    except ImportError:
        from transform.background import BackgroundRemovalUnavailable
        with pytest.raises(BackgroundRemovalUnavailable):
            convert_image(make_png(), target_format="png", remove_bg=True)


def test_remove_bg_returns_rgba_when_available():
    pytest.importorskip("rembg")
    out = convert_image(make_png((64, 64)), target_format="png", remove_bg=True)
    img = _open(out)
    assert img.format == "PNG"
    assert img.mode in {"RGBA", "LA"}
    assert img.size == (64, 64)
