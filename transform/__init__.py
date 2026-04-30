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

__all__ = [
    "IMAGE_EXTS",
    "ALLOWED_FORMATS",
    "ALPHA_CAPABLE_FORMATS",
    "BackgroundRemovalUnavailable",
    "ConversionReport",
    "convert_image",
    "convert_many",
    "convert_zip",
    "normalize_format",
    "remove_background",
]
