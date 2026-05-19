from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageChops

from transform import (
    CorruptedImageError,
    OptimizationResult,
    OversizedImageError,
    UnsupportedImageFormatError,
    optimize_image_for_pdf,
    optimize_many_for_pdf,
)


def _make_photo_like(path: Path, size: tuple[int, int] = (2200, 1400)) -> None:
    red = Image.linear_gradient("L").resize(size)
    green = Image.radial_gradient("L").resize(size)
    blue = Image.effect_noise(size, 90)
    img = Image.merge("RGB", (red, green, blue))
    img.save(path, format="PNG")


def _make_ui_like(path: Path, size: tuple[int, int] = (1200, 800)) -> None:
    img = Image.new("RGB", size, (245, 245, 245))
    # Deliberately keep a limited palette and flat regions.
    for y in range(0, size[1], 50):
        for x in range(size[0]):
            img.putpixel((x, y), (40, 40, 40))
    for x in range(0, size[0], 120):
        for y in range(size[1]):
            img.putpixel((x, y), (55, 120, 180))
    img.save(path, format="PNG")


def _make_transparent_photo(path: Path, size: tuple[int, int] = (1500, 900)) -> None:
    rgba = Image.effect_noise(size, 90).convert("RGBA")
    rgba.putpixel((0, 0), (255, 255, 255, 0))
    rgba.save(path, format="PNG")


def test_photo_prefers_jpeg_and_resizes(tmp_path: Path):
    src = tmp_path / "photo.png"
    out = tmp_path / "photo-out.png"
    _make_photo_like(src)

    result = optimize_image_for_pdf(str(src), str(out), max_width=1400, jpeg_quality=68)

    assert isinstance(result, OptimizationResult)
    assert result.selected_output_format == "jpg"
    assert result.final_dimensions[0] == 1400
    assert result.optimized_size < result.original_size

    with Image.open(result.output_path) as image:
        image.load()
        assert image.format == "JPEG"
        assert image.mode == "RGB"


def test_png_quantization_for_ui_assets(tmp_path: Path):
    src = tmp_path / "ui.png"
    out = tmp_path / "ui-out.jpg"
    _make_ui_like(src)

    result = optimize_image_for_pdf(str(src), str(out), png_colors=32)

    assert result.selected_output_format == "png"
    with Image.open(result.output_path) as image:
        image.load()
        assert image.format == "PNG"
        assert image.mode in {"P", "PA"}
        assert image.getcolors(maxcolors=256) is not None
        assert len(image.getcolors(maxcolors=256)) <= 32


def test_transparent_png_minor_alpha_flattens_to_jpeg(tmp_path: Path):
    src = tmp_path / "transparent-photo.png"
    out = tmp_path / "transparent-photo-out.png"
    _make_transparent_photo(src)

    result = optimize_image_for_pdf(str(src), str(out), jpeg_quality=70)

    assert result.selected_output_format == "jpg"
    with Image.open(result.output_path) as image:
        image.load()
        assert image.format == "JPEG"
        assert image.mode == "RGB"


def test_screenshot_ui_classification_goes_png(tmp_path: Path):
    src = tmp_path / "screenshot.bmp"
    out = tmp_path / "screenshot-out.jpg"
    _make_ui_like(src)

    result = optimize_image_for_pdf(str(src), str(out), png_colors=48)

    assert result.selected_output_format == "png"
    assert result.output_path.endswith(".png")


def test_optional_grayscale_mode(tmp_path: Path):
    src = tmp_path / "scan.tiff"
    out = tmp_path / "scan-out.jpg"
    _make_photo_like(src, size=(900, 700))

    result = optimize_image_for_pdf(str(src), str(out), grayscale=True)

    with Image.open(result.output_path) as image:
        image.load()
        assert image.mode in {"L", "P", "RGB"}
        # A grayscale JPEG often decodes as mode L; RGB fallback is tolerated.
        if image.mode == "RGB":
            r, g, b = image.split()
            assert ImageChops.difference(r, g).getbbox() is None
            assert ImageChops.difference(g, b).getbbox() is None


def test_corrupted_input_raises(tmp_path: Path):
    src = tmp_path / "bad.png"
    src.write_bytes(b"this is not an image")

    with pytest.raises(CorruptedImageError):
        optimize_image_for_pdf(str(src), str(tmp_path / "bad-out.jpg"))


def test_large_image_guard_raises(tmp_path: Path):
    src = tmp_path / "large.png"
    Image.new("RGB", (300, 300), "white").save(src, format="PNG")

    with pytest.raises(OversizedImageError):
        optimize_image_for_pdf(
            str(src),
            str(tmp_path / "large-out.jpg"),
            max_pixels=10_000,
        )


def test_batch_processing(tmp_path: Path):
    src1 = tmp_path / "img1.png"
    src2 = tmp_path / "img2.png"
    out1 = tmp_path / "img1-out"
    out2 = tmp_path / "img2-out"
    _make_photo_like(src1, size=(1100, 700))
    _make_ui_like(src2, size=(800, 500))

    results = optimize_many_for_pdf(
        [(str(src1), str(out1)), (str(src2), str(out2))],
        jpeg_quality=67,
        png_colors=48,
    )

    assert len(results) == 2
    assert all(isinstance(r, OptimizationResult) for r in results)
    assert {r.selected_output_format for r in results} == {"jpg", "png"}


def test_orientation_exif_is_applied(tmp_path: Path):
    src = tmp_path / "oriented.jpg"
    out = tmp_path / "oriented-out.jpg"
    base = Image.new("RGB", (80, 40), "white")
    exif = Image.Exif()
    exif[274] = 6
    base.save(src, format="JPEG", exif=exif)

    result = optimize_image_for_pdf(str(src), str(out), max_width=400)

    assert result.final_dimensions == (40, 80)


def test_metadata_removed_from_output(tmp_path: Path):
    src = tmp_path / "meta.jpg"
    out = tmp_path / "meta-out.jpg"
    img = Image.new("RGB", (200, 120), "gray")
    exif = Image.Exif()
    exif[305] = "img-transformer-tests"
    img.save(src, format="JPEG", exif=exif)

    result = optimize_image_for_pdf(str(src), str(out), max_width=300)

    with Image.open(result.output_path) as optimized:
        optimized.load()
        assert len(optimized.getexif()) == 0
        assert "icc_profile" not in optimized.info


def test_gif_uses_first_frame_only(tmp_path: Path):
    src = tmp_path / "animated.gif"
    out = tmp_path / "animated-out"

    frame1 = Image.new("RGB", (100, 60), "red")
    frame2 = Image.new("RGB", (100, 60), "blue")
    frame1.save(
        src,
        format="GIF",
        save_all=True,
        append_images=[frame2],
        loop=0,
        duration=80,
    )

    result = optimize_image_for_pdf(str(src), str(out), max_width=100)
    with Image.open(result.output_path) as optimized:
        optimized.load()
        pixel = optimized.convert("RGB").getpixel((0, 0))
        assert pixel[0] > pixel[2]


def test_svg_is_rejected(tmp_path: Path):
    src = tmp_path / "vector.svg"
    src.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"></svg>')

    with pytest.raises(UnsupportedImageFormatError):
        optimize_image_for_pdf(str(src), str(tmp_path / "vector-out.jpg"))
