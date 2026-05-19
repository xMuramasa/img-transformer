from __future__ import annotations

import json
import zipfile
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from tests._helpers import make_png

client = TestClient(app)


def _make_photo_like(size: tuple[int, int] = (1800, 1100)) -> bytes:
    red = Image.linear_gradient("L").resize(size)
    green = Image.radial_gradient("L").resize(size)
    blue = Image.effect_noise(size, 90)
    img = Image.merge("RGB", (red, green, blue))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_convert_image_endpoint():
    r = client.post(
        "/api/convert/image",
        data={"target_format": "webp", "width": "20"},
        files={"file": ("a.png", make_png((100, 50)), "image/png")},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/webp")
    img = Image.open(BytesIO(r.content))
    img.load()
    assert img.format == "WEBP"
    assert img.size == (20, 10)


def test_convert_image_rejects_bad_format():
    r = client.post(
        "/api/convert/image",
        data={"target_format": "heic"},
        files={"file": ("a.png", make_png(), "image/png")},
    )
    assert r.status_code == 400


def test_convert_image_rejects_non_image():
    r = client.post(
        "/api/convert/image",
        data={"target_format": "png"},
        files={"file": ("a.png", b"not an image", "image/png")},
    )
    assert r.status_code == 400


def test_convert_files_endpoint():
    r = client.post(
        "/api/convert/files",
        data={"target_format": "png"},
        files=[
            ("files", ("a.png", make_png(), "image/png")),
            ("files", ("b.png", make_png(), "image/png")),
        ],
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    report = json.loads(r.headers["X-Conversion-Report"])
    assert sorted(report["converted"]) == ["a.png", "b.png"]
    with zipfile.ZipFile(BytesIO(r.content)) as z:
        assert sorted(z.namelist()) == ["a.png", "b.png"]


def test_convert_zip_endpoint():
    inner = BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("img.png", make_png())
    r = client.post(
        "/api/convert/zip",
        data={"target_format": "webp"},
        files={"file": ("in.zip", inner.getvalue(), "application/zip")},
    )
    assert r.status_code == 200
    with zipfile.ZipFile(BytesIO(r.content)) as z:
        assert z.namelist() == ["img.webp"]


def test_optimize_pdf_endpoint_success():
    r = client.post(
        "/api/optimize/pdf",
        data={"max_width": "1200", "jpeg_quality": "68", "png_colors": "64"},
        files={"file": ("photo.png", _make_photo_like(), "image/png")},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")
    assert "X-Optimization-Result" in r.headers
    payload = json.loads(r.headers["X-Optimization-Result"])
    assert payload["final_dimensions"][0] <= 1200
    assert payload["selected_output_format"] in {"jpg", "png"}


def test_optimize_pdf_files_endpoint_success():
    r = client.post(
        "/api/optimize/pdf/files",
        data={"max_width": "1200", "jpeg_quality": "68", "png_colors": "64"},
        files=[
            ("files", ("photo-a.png", _make_photo_like(), "image/png")),
            ("files", ("photo-b.png", _make_photo_like((1600, 900)), "image/png")),
        ],
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"

    report = json.loads(r.headers["X-Conversion-Report"])
    assert sorted(report["converted"]) == ["photo-a.png", "photo-b.png"]
    assert report["failed"] == []
    assert report["skipped"] == []

    with zipfile.ZipFile(BytesIO(r.content)) as z:
        names = sorted(z.namelist())
        assert len(names) == 2
        assert {name.rsplit(".", 1)[0] for name in names} == {"photo-a", "photo-b"}
        assert {name.rsplit(".", 1)[1] for name in names} <= {"jpg", "png"}


def test_optimize_pdf_endpoint_rejects_invalid_input():
    r = client.post(
        "/api/optimize/pdf",
        files={"file": ("not-image.png", b"bad-image-bytes", "image/png")},
    )
    assert r.status_code == 400
