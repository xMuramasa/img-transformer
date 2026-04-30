"""Single-image conversion and resizing."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from PIL import Image

from .background import remove_background

# Pillow format names (canonical, uppercase).
ALLOWED_FORMATS: frozenset[str] = frozenset(
    {"WEBP", "PNG", "JPEG", "GIF", "BMP", "TIFF"}
)

# Formats that can encode an alpha channel; required when removing backgrounds.
ALPHA_CAPABLE_FORMATS: frozenset[str] = frozenset({"WEBP", "PNG", "GIF", "TIFF"})

# File extensions we recognize as image inputs (lowercase, no dot).
IMAGE_EXTS: frozenset[str] = frozenset(
    {"png", "jpg", "jpeg", "gif", "bmp", "webp", "tiff", "tif"}
)

# User-facing aliases → canonical Pillow format names.
_FORMAT_ALIASES: dict[str, str] = {
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "png": "PNG",
    "webp": "WEBP",
    "gif": "GIF",
    "bmp": "BMP",
    "tif": "TIFF",
    "tiff": "TIFF",
}


def normalize_format(fmt: str) -> str:
    """Return the canonical Pillow format name for *fmt*.

    Raises:
        ValueError: if *fmt* is not in the allow-list.
    """
    if not fmt:
        raise ValueError("Target format is required.")
    canonical = _FORMAT_ALIASES.get(fmt.strip().lower())
    if canonical is None or canonical not in ALLOWED_FORMATS:
        allowed = ", ".join(sorted(ALLOWED_FORMATS))
        raise ValueError(
            f"Unsupported target format {fmt!r}. Allowed: {allowed}."
        )
    return canonical


def extension_for(fmt: str) -> str:
    """Return the file extension (without dot) used for *fmt*."""
    canonical = normalize_format(fmt)
    return "jpg" if canonical == "JPEG" else canonical.lower()


def _compute_size(
    original: tuple[int, int],
    width: int | None,
    height: int | None,
) -> tuple[int, int] | None:
    """Compute the target size, preserving aspect ratio when only one
    dimension is supplied.

    Returns ``None`` when no resize is requested.
    """
    if width is None and height is None:
        return None
    orig_w, orig_h = original
    if orig_w <= 0 or orig_h <= 0:
        raise ValueError("Source image has invalid dimensions.")

    if width is not None and height is not None:
        new_w, new_h = width, height
    elif width is not None:
        new_w = width
        new_h = max(1, round(orig_h * (width / orig_w)))
    else:  # height is not None
        assert height is not None
        new_h = height
        new_w = max(1, round(orig_w * (height / orig_h)))

    if new_w <= 0 or new_h <= 0:
        raise ValueError("Target dimensions must be positive.")
    return new_w, new_h


def _prepare_for_format(img: Image.Image, fmt: str) -> Image.Image:
    """Convert *img*'s mode if needed for the target format.

    JPEG and BMP cannot encode alpha; flatten to RGB. Palette images are
    promoted so resize and re-encode behave predictably.
    """
    if fmt in {"JPEG", "BMP"}:
        if img.mode in {"RGBA", "LA"}:
            background = Image.new("RGB", img.size, (255, 255, 255))
            alpha = img.split()[-1]
            background.paste(img.convert("RGBA"), mask=alpha)
            return background
        if img.mode != "RGB":
            return img.convert("RGB")
        return img
    if img.mode == "P":
        return img.convert("RGBA")
    return img


def convert_image(
    src: BinaryIO | str | Path | bytes,
    *,
    target_format: str,
    width: int | None = None,
    height: int | None = None,
    remove_bg: bool = False,
) -> bytes:
    """Convert *src* to *target_format*, optionally resizing.

    Args:
        src: Source image — a path, a file-like object, or raw bytes.
        target_format: Target format (e.g., ``"webp"``, ``"PNG"``, ``"jpg"``).
        width: Target width in pixels. If only one of width/height is given,
            the other is derived to preserve aspect ratio.
        height: Target height in pixels.
        remove_bg: If True, remove the background before resizing/encoding.
            Requires the ``bgremove`` extra and an alpha-capable target format.

    Returns:
        The encoded image as bytes.

    Raises:
        ValueError: on unsupported format or invalid dimensions.
        PIL.UnidentifiedImageError: when *src* is not a recognized image.
    """
    fmt = normalize_format(target_format)

    if remove_bg and fmt not in ALPHA_CAPABLE_FORMATS:
        allowed = ", ".join(sorted(ALPHA_CAPABLE_FORMATS))
        raise ValueError(
            f"remove_bg requires an alpha-capable target format ({allowed}); "
            f"got {fmt}."
        )

    # Materialize raw bytes once so background removal (which needs bytes)
    # and Pillow can both consume the same input.
    if isinstance(src, bytes):
        raw = src
    elif isinstance(src, (str, Path)):
        raw = Path(src).read_bytes()
    else:
        raw = src.read()

    if remove_bg:
        img = remove_background(raw)
        try:
            new_size = _compute_size(img.size, width, height)
            if new_size is not None:
                img = img.resize(new_size, Image.LANCZOS)
            img = _prepare_for_format(img, fmt)
            buf = BytesIO()
            img.save(buf, format=fmt)
            return buf.getvalue()
        finally:
            img.close()

    with Image.open(BytesIO(raw)) as img:
        img.load()
        new_size = _compute_size(img.size, width, height)
        if new_size is not None:
            img = img.resize(new_size, Image.LANCZOS)
        img = _prepare_for_format(img, fmt)

        buf = BytesIO()
        img.save(buf, format=fmt)
        return buf.getvalue()
