from __future__ import annotations

import zipfile
from io import BytesIO

from PIL import Image

from tests._helpers import make_png
from transform import convert_many, convert_zip


def _build_zip(entries: dict[str, bytes]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def _list_zip(data: bytes) -> list[str]:
    with zipfile.ZipFile(BytesIO(data)) as z:
        return sorted(z.namelist())


def test_convert_zip_basic():
    src = _build_zip({
        "a.png": make_png((10, 10)),
        "sub/b.png": make_png((20, 20)),
        "notes.txt": b"hello",
    })
    out, report = convert_zip(src, target_format="webp")
    names = _list_zip(out)
    assert names == ["a.webp", "sub/b.webp"]
    assert sorted(report.converted) == ["a.png", "sub/b.png"]
    assert report.skipped == ["notes.txt"]
    assert report.failed == []


def test_convert_zip_rejects_zip_slip():
    src = _build_zip({"../evil.png": make_png(), "ok.png": make_png()})
    out, report = convert_zip(src, target_format="png")
    assert _list_zip(out) == ["ok.png"]
    assert any(name == "../evil.png" for name, _ in report.failed)


def test_convert_zip_handles_corrupt_image():
    src = _build_zip({"good.png": make_png(), "bad.png": b"not really a png"})
    out, report = convert_zip(src, target_format="png")
    assert _list_zip(out) == ["good.png"]
    assert any(name == "bad.png" for name, _ in report.failed)


def test_convert_zip_with_resize():
    src = _build_zip({"a.png": make_png((100, 50))})
    out, _ = convert_zip(src, target_format="png", width=10)
    with zipfile.ZipFile(BytesIO(out)) as z:
        with z.open("a.png") as f:
            img = Image.open(f)
            img.load()
    assert img.size == (10, 5)


def test_convert_many_pairs():
    files = [
        ("one.png", make_png((8, 4))),
        ("two.jpg", make_png((8, 4))),
        ("ignore.txt", b"nope"),
    ]
    out, report = convert_many(files, target_format="webp")
    assert sorted(_list_zip(out)) == ["one.webp", "two.webp"]
    assert report.skipped == ["ignore.txt"]
