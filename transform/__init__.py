"""Pure-Python image transformation core.

No web framework dependencies — usable from FastAPI, a CLI, or tests.
"""

from .image import (
    ALLOWED_FORMATS,
    ALPHA_CAPABLE_FORMATS,
    IMAGE_EXTS,
    convert_image,
    normalize_format,
)
from .archive import ConversionReport, convert_many, convert_zip
from .background import BackgroundRemovalUnavailable, remove_background
from .pdf_optimizer import (
    CorruptedImageError,
    ImageOptimizationError,
    OptimizationResult,
    OversizedImageError,
    UnsupportedImageFormatError,
    optimize_image_for_pdf,
    optimize_many_for_pdf,
)

__all__ = [
    "IMAGE_EXTS",
    "ALLOWED_FORMATS",
    "ALPHA_CAPABLE_FORMATS",
    "BackgroundRemovalUnavailable",
    "ConversionReport",
    "CorruptedImageError",
    "ImageOptimizationError",
    "convert_image",
    "convert_many",
    "convert_zip",
    "normalize_format",
    "OptimizationResult",
    "optimize_image_for_pdf",
    "optimize_many_for_pdf",
    "OversizedImageError",
    "remove_background",
    "UnsupportedImageFormatError",
]
