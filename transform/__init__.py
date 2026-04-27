"""Pure-Python image transformation core.

No web framework dependencies — usable from FastAPI, a CLI, or tests.
"""

from .image import IMAGE_EXTS, ALLOWED_FORMATS, convert_image, normalize_format
from .archive import ConversionReport, convert_many, convert_zip

__all__ = [
    "IMAGE_EXTS",
    "ALLOWED_FORMATS",
    "ConversionReport",
    "convert_image",
    "convert_many",
    "convert_zip",
    "normalize_format",
]
