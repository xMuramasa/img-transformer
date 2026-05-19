"""PDF-safe image optimization pipeline.

Outputs are intentionally restricted to JPEG and PNG for maximum
compatibility with @react-pdf/renderer.
"""

from __future__ import annotations

import json
import logging
import warnings
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Iterable

from PIL import Image, ImageFile, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError

logger = logging.getLogger(__name__)

# Defensive upper bound to avoid decompression bombs and memory spikes.
DEFAULT_MAX_PIXELS = 40_000_000

# Hard cap on analysis work; we classify using a downscaled preview.
ANALYSIS_MAX_SIDE = 512

_BASE_SUPPORTED_FORMATS: frozenset[str] = frozenset(
    {"JPEG", "JPG", "PNG", "WEBP", "TIFF", "BMP", "GIF"}
)


class ImageOptimizationError(ValueError):
    """Base error for PDF optimization failures."""


class UnsupportedImageFormatError(ImageOptimizationError):
    """Raised when input format cannot be handled safely."""


class CorruptedImageError(ImageOptimizationError):
    """Raised when input cannot be decoded as an image."""


class OversizedImageError(ImageOptimizationError):
    """Raised when image dimensions exceed configured safety limits."""


@dataclass(frozen=True)
class OptimizationResult:
    """Metadata returned after optimizing one image."""

    input_path: str
    output_path: str
    original_size: int
    optimized_size: int
    reduction_percentage: float
    original_dimensions: tuple[int, int]
    final_dimensions: tuple[int, int]
    selected_output_format: str
    processing_duration_ms: float


@dataclass(frozen=True)
class ClassificationDecision:
    target_format: str
    has_alpha: bool
    alpha_coverage: float
    unique_colors: int
    entropy: float
    flat_area_ratio: float
    screenshot_like: bool
    reasons: tuple[str, ...]


def _json_log(event: str, **payload: object) -> None:
    logger.info("%s %s", event, json.dumps(payload, sort_keys=True, default=str))


def _has_avif_decoder() -> bool:
    return ".avif" in Image.registered_extensions()


def _supported_input_formats() -> frozenset[str]:
    if _has_avif_decoder():
        return frozenset((*_BASE_SUPPORTED_FORMATS, "AVIF"))
    return _BASE_SUPPORTED_FORMATS


def _normalize_input_format(image: Image.Image, input_path: Path) -> str:
    fmt = (image.format or "").upper()
    ext = input_path.suffix.lower()
    if ext == ".svg" or fmt == "SVG":
        raise UnsupportedImageFormatError(
            "SVG input is not supported in this pipeline. Rasterize SVG before optimization."
        )

    if not fmt:
        raise CorruptedImageError("Input file does not contain a detectable image format.")

    if fmt == "AVIF" and not _has_avif_decoder():
        raise UnsupportedImageFormatError(
            "AVIF support is unavailable in this Pillow build. Install AVIF-capable codecs."
        )

    supported = _supported_input_formats()
    if fmt not in supported:
        allowed = ", ".join(sorted(supported))
        raise UnsupportedImageFormatError(
            f"Unsupported input format {fmt!r}. Supported formats: {allowed}."
        )
    return fmt


def _is_effectively_transparent(img: Image.Image) -> bool:
    if img.mode in {"RGBA", "LA"}:
        alpha = img.getchannel("A")
        lo, hi = alpha.getextrema()
        return lo < 255 and hi <= 255
    if img.mode == "P" and "transparency" in img.info:
        return True
    return False


def _analysis_preview(img: Image.Image) -> Image.Image:
    preview = img.convert("RGBA" if _is_effectively_transparent(img) else "RGB")
    preview.thumbnail((ANALYSIS_MAX_SIDE, ANALYSIS_MAX_SIDE), Image.Resampling.LANCZOS)
    return preview


def _count_unique_colors(preview: Image.Image, max_colors: int = 16384) -> int:
    rgb = preview.convert("RGB")
    colors = rgb.getcolors(maxcolors=max_colors)
    if colors is None:
        return max_colors + 1
    return len(colors)


def _flat_area_ratio(preview: Image.Image) -> float:
    edge_map = preview.convert("L").filter(ImageFilter.FIND_EDGES)
    mean_edge = ImageStat.Stat(edge_map).mean[0] / 255.0
    return max(0.0, min(1.0, 1.0 - mean_edge))


def _alpha_coverage(preview: Image.Image) -> float:
    if preview.mode not in {"RGBA", "LA"}:
        return 0.0
    alpha = preview.getchannel("A")
    hist = alpha.histogram()
    total = sum(hist)
    if total == 0:
        return 0.0
    opaque = hist[255]
    return max(0.0, min(1.0, 1.0 - (opaque / total)))


def _classify_image(preview: Image.Image, has_alpha: bool) -> ClassificationDecision:
    unique_colors = _count_unique_colors(preview)
    entropy = preview.convert("L").entropy()
    flat_ratio = _flat_area_ratio(preview)
    alpha_coverage = _alpha_coverage(preview)

    screenshot_like = (
        unique_colors <= 2048
        and flat_ratio >= 0.78
        and entropy <= 6.8
    )

    reasons: list[str] = []
    if has_alpha and alpha_coverage >= 0.02:
        reasons.append("meaningful transparency detected")
        return ClassificationDecision(
            target_format="PNG",
            has_alpha=True,
            alpha_coverage=alpha_coverage,
            unique_colors=unique_colors,
            entropy=entropy,
            flat_area_ratio=flat_ratio,
            screenshot_like=screenshot_like,
            reasons=tuple(reasons),
        )
    if has_alpha and alpha_coverage < 0.02:
        reasons.append("minor transparency flattened for JPEG compatibility")

    if unique_colors <= 80:
        reasons.append("small palette")
    if flat_ratio >= 0.84:
        reasons.append("large flat-color regions")
    if screenshot_like:
        reasons.append("ui/screenshot-like structure")

    use_png = (
        screenshot_like
        or (flat_ratio >= 0.88 and unique_colors <= 2048)
        or (unique_colors <= 180 and entropy <= 6.2)
        or unique_colors <= 64
    )
    target_format = "PNG" if use_png else "JPEG"
    if not reasons:
        reasons.append("photographic/high-complexity content")

    return ClassificationDecision(
        target_format=target_format,
        has_alpha=has_alpha,
        alpha_coverage=alpha_coverage,
        unique_colors=unique_colors,
        entropy=entropy,
        flat_area_ratio=flat_ratio,
        screenshot_like=screenshot_like,
        reasons=tuple(reasons),
    )


def _resize_dimensions(original: tuple[int, int], max_width: int) -> tuple[int, int]:
    src_w, src_h = original
    if max_width <= 0:
        raise ImageOptimizationError("max_width must be greater than zero.")
    if src_w <= max_width:
        return original
    ratio = max_width / src_w
    return max_width, max(1, int(round(src_h * ratio)))


def _flatten_alpha_to_white(img: Image.Image) -> Image.Image:
    if img.mode not in {"RGBA", "LA", "P"}:
        return img.convert("RGB") if img.mode != "RGB" else img
    rgba = img.convert("RGBA")
    background = Image.new("RGB", rgba.size, (255, 255, 255))
    background.paste(rgba, mask=rgba.getchannel("A"))
    return background


def _encode_jpeg(img: Image.Image, output_path: Path, jpeg_quality: int) -> None:
    rgb = _flatten_alpha_to_white(img)
    rgb.save(
        output_path,
        format="JPEG",
        quality=jpeg_quality,
        optimize=True,
        progressive=True,
        subsampling="4:2:0",
    )


def _encode_png(img: Image.Image, output_path: Path, png_colors: int) -> None:
    if png_colors < 2 or png_colors > 256:
        raise ImageOptimizationError("png_colors must be in the range [2, 256].")

    if img.mode in {"RGBA", "LA"} or _is_effectively_transparent(img):
        quantized = img.convert("RGBA").quantize(
            colors=png_colors,
            method=Image.Quantize.FASTOCTREE,
            dither=Image.Dither.NONE,
        )
        quantized.save(
            output_path,
            format="PNG",
            optimize=True,
            compress_level=9,
        )
        return

    rgb = img.convert("RGB") if img.mode != "RGB" else img
    quantized = rgb.quantize(
        colors=png_colors,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    )
    quantized.save(
        output_path,
        format="PNG",
        optimize=True,
        compress_level=9,
    )


def _normalized_output_path(output_path: Path, selected: str) -> Path:
    ext = ".jpg" if selected == "JPEG" else ".png"
    if output_path.suffix.lower() == ext:
        return output_path
    if output_path.suffix:
        return output_path.with_suffix(ext)
    return output_path.with_name(f"{output_path.name}{ext}")


def optimize_image_for_pdf(
    input_path: str,
    output_path: str,
    *,
    max_width: int = 1400,
    jpeg_quality: int = 68,
    png_colors: int = 64,
    grayscale: bool = False,
    max_pixels: int = DEFAULT_MAX_PIXELS,
) -> OptimizationResult:
    """Optimize an image for reliable embedding in PDF renderers.

    Input formats are normalized to JPEG or PNG outputs based on content
    classification heuristics.
    """
    in_path = Path(input_path)
    if not in_path.is_file():
        raise ImageOptimizationError(f"Input file not found: {input_path}")
    if in_path.suffix.lower() == ".svg":
        raise UnsupportedImageFormatError(
            "SVG input is not supported in this pipeline. Rasterize SVG before optimization."
        )
    if jpeg_quality < 30 or jpeg_quality > 95:
        raise ImageOptimizationError("jpeg_quality must be in the range [30, 95].")

    out_target = Path(output_path)
    out_target.parent.mkdir(parents=True, exist_ok=True)

    started = perf_counter()
    original_size = in_path.stat().st_size

    ImageFile.LOAD_TRUNCATED_IMAGES = False

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(in_path) as opened:
                input_format = _normalize_input_format(opened, in_path)
                original_dimensions = opened.size
                if original_dimensions[0] * original_dimensions[1] > max_pixels:
                    raise OversizedImageError(
                        f"Image dimensions {original_dimensions} exceed max_pixels={max_pixels}."
                    )

                if getattr(opened, "is_animated", False):
                    if input_format == "GIF":
                        opened.seek(0)
                    else:
                        raise UnsupportedImageFormatError(
                            f"Animated {input_format} is unsupported; provide a static frame."
                        )

                image = ImageOps.exif_transpose(opened)
                image.load()

    except FileNotFoundError as e:
        raise ImageOptimizationError(f"Input file not found: {input_path}") from e
    except UnidentifiedImageError as e:
        raise CorruptedImageError("Input is not a valid image file.") from e
    except Image.DecompressionBombError as e:
        raise OversizedImageError("Image exceeds Pillow decompression bomb limit.") from e
    except Image.DecompressionBombWarning as e:
        raise OversizedImageError("Image triggered decompression bomb protection.") from e

    has_alpha = _is_effectively_transparent(image)
    preview = _analysis_preview(image)
    decision = _classify_image(preview, has_alpha=has_alpha)

    resized_dimensions = _resize_dimensions(image.size, max_width=max_width)
    if resized_dimensions != image.size:
        image = image.resize(resized_dimensions, Image.Resampling.LANCZOS)

    if grayscale:
        image = ImageOps.grayscale(image)

    normalized_out = _normalized_output_path(out_target, decision.target_format)
    if decision.target_format == "JPEG":
        _encode_jpeg(image, normalized_out, jpeg_quality=jpeg_quality)
    else:
        _encode_png(image, normalized_out, png_colors=png_colors)

    optimized_size = normalized_out.stat().st_size
    reduction = (1.0 - (optimized_size / original_size)) * 100.0 if original_size else 0.0
    duration_ms = (perf_counter() - started) * 1000.0

    _json_log(
        "pdf_image_optimized",
        input_path=str(in_path),
        input_format=input_format,
        output_path=str(normalized_out),
        output_format=decision.target_format,
        original_size=original_size,
        optimized_size=optimized_size,
        reduction_percentage=round(reduction, 2),
        original_dimensions=original_dimensions,
        final_dimensions=image.size,
        decision={
            "has_alpha": decision.has_alpha,
            "alpha_coverage": round(decision.alpha_coverage, 4),
            "unique_colors": decision.unique_colors,
            "entropy": round(decision.entropy, 3),
            "flat_area_ratio": round(decision.flat_area_ratio, 3),
            "screenshot_like": decision.screenshot_like,
            "reasons": list(decision.reasons),
        },
        grayscale=grayscale,
        duration_ms=round(duration_ms, 2),
    )

    return OptimizationResult(
        input_path=str(in_path),
        output_path=str(normalized_out),
        original_size=original_size,
        optimized_size=optimized_size,
        reduction_percentage=reduction,
        original_dimensions=original_dimensions,
        final_dimensions=image.size,
        selected_output_format="jpg" if decision.target_format == "JPEG" else "png",
        processing_duration_ms=duration_ms,
    )


def optimize_many_for_pdf(
    items: Iterable[tuple[str, str]],
    *,
    max_width: int = 1400,
    jpeg_quality: int = 68,
    png_colors: int = 64,
    grayscale: bool = False,
    max_pixels: int = DEFAULT_MAX_PIXELS,
) -> list[OptimizationResult]:
    """Batch-optimized wrapper around :func:`optimize_image_for_pdf`."""
    results: list[OptimizationResult] = []
    for input_path, output_path in items:
        results.append(
            optimize_image_for_pdf(
                input_path,
                output_path,
                max_width=max_width,
                jpeg_quality=jpeg_quality,
                png_colors=png_colors,
                grayscale=grayscale,
                max_pixels=max_pixels,
            )
        )
    return results
