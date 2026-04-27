"""Shared test helpers."""

from __future__ import annotations

from io import BytesIO

from PIL import Image


def make_png(size: tuple[int, int] = (40, 30), color: str = "red") -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def make_rgba_png(size: tuple[int, int] = (20, 20)) -> bytes:
    buf = BytesIO()
    Image.new("RGBA", size, (10, 20, 30, 128)).save(buf, format="PNG")
    return buf.getvalue()
