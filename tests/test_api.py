from __future__ import annotations

import json
import zipfile
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from tests._helpers import make_png

client = TestClient(app)


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
