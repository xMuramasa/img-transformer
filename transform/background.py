"""Background removal via rembg (optional dependency).

The heavy ONNX runtime + model is imported lazily so the rest of the app
keeps a small footprint when the ``bgremove`` extra is not installed.
"""

from __future__ import annotations

from io import BytesIO
from threading import Lock

from PIL import Image

_session = None
_session_lock = Lock()

# Default model — small, general purpose, ~170 MB.
DEFAULT_MODEL = "u2net"


class BackgroundRemovalUnavailable(RuntimeError):
    """Raised when rembg / onnxruntime is not installed."""


def _get_session(model_name: str = DEFAULT_MODEL):
    """Return a process-wide rembg session, creating it on first use."""
    global _session
    if _session is not None:
        return _session
    with _session_lock:
        if _session is not None:
            return _session
        try:
            from rembg import new_session  # type: ignore[import-not-found]
        except ImportError as e:
            raise BackgroundRemovalUnavailable(
                "Background removal requires the 'bgremove' extra: "
                "uv sync --extra bgremove"
            ) from e
        _session = new_session(model_name)
        return _session


def remove_background(data: bytes) -> Image.Image:
    """Remove the background from *data* and return an RGBA Pillow image.

    Raises:
        BackgroundRemovalUnavailable: if the optional dependency is missing.
    """
    try:
        from rembg import remove  # type: ignore[import-not-found]
    except ImportError as e:
        raise BackgroundRemovalUnavailable(
            "Background removal requires the 'bgremove' extra: "
            "uv sync --extra bgremove"
        ) from e

    session = _get_session()
    out_bytes = remove(data, session=session)
    img = Image.open(BytesIO(out_bytes))
    img.load()
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    return img
