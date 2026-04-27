"""Bulk image conversion: zip-in/zip-out and multi-file → zip."""

from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import PurePosixPath
from typing import BinaryIO, Iterable

from PIL import UnidentifiedImageError

from .image import IMAGE_EXTS, convert_image, extension_for, normalize_format


@dataclass
class ConversionReport:
    """Summary of a bulk conversion run."""

    converted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "converted": list(self.converted),
            "skipped": list(self.skipped),
            "failed": [{"name": n, "error": e} for n, e in self.failed],
        }


def _is_image_name(name: str) -> bool:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return ext in IMAGE_EXTS


def _safe_member_name(name: str) -> str | None:
    """Return a sanitized member name, or ``None`` if the entry is unsafe.

    Rejects absolute paths, drive letters, and any path containing ``..``
    segments (zip-slip protection).
    """
    if not name or name.endswith("/"):
        return None
    # Normalize separators; zip spec uses forward slashes.
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):
        return None
    parts = PurePosixPath(normalized).parts
    if any(p in {"..", ""} for p in parts):
        return None
    return posixpath.join(*parts)


def _output_name(member_name: str, target_ext: str) -> str:
    """Replace *member_name*'s extension with *target_ext*, preserving dirs."""
    base, _, _ = member_name.rpartition(".")
    stem = base if base else member_name
    return f"{stem}.{target_ext}"


def convert_zip(
    src_zip: BinaryIO | bytes,
    *,
    target_format: str,
    width: int | None = None,
    height: int | None = None,
) -> tuple[bytes, ConversionReport]:
    """Convert every image inside *src_zip* and return a new zip.

    Non-image entries are skipped. Unsafe entries (zip-slip) are skipped and
    recorded as ``failed``. The output zip preserves the input directory
    layout but with new file extensions.
    """
    fmt = normalize_format(target_format)
    target_ext = extension_for(fmt)
    report = ConversionReport()

    if isinstance(src_zip, bytes):
        src_zip = BytesIO(src_zip)

    out_buf = BytesIO()
    with (
        zipfile.ZipFile(src_zip, "r") as zin,
        zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as zout,
    ):
        for info in zin.infolist():
            if info.is_dir():
                continue
            name = info.filename
            safe = _safe_member_name(name)
            if safe is None:
                report.failed.append((name, "unsafe path"))
                continue
            if not _is_image_name(safe):
                report.skipped.append(safe)
                continue
            try:
                with zin.open(info, "r") as member:
                    data = member.read()
                converted = convert_image(
                    data,
                    target_format=fmt,
                    width=width,
                    height=height,
                )
            except UnidentifiedImageError:
                report.failed.append((safe, "not a recognizable image"))
                continue
            except Exception as e:  # noqa: BLE001 — surface error to caller
                report.failed.append((safe, str(e)))
                continue

            zout.writestr(_output_name(safe, target_ext), converted)
            report.converted.append(safe)

    return out_buf.getvalue(), report


def convert_many(
    files: Iterable[tuple[str, BinaryIO | bytes]],
    *,
    target_format: str,
    width: int | None = None,
    height: int | None = None,
) -> tuple[bytes, ConversionReport]:
    """Convert an iterable of ``(filename, data)`` pairs into a single zip."""
    fmt = normalize_format(target_format)
    target_ext = extension_for(fmt)
    report = ConversionReport()

    out_buf = BytesIO()
    with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in files:
            safe = _safe_member_name(name) or name.rsplit("/", 1)[-1]
            if not _is_image_name(safe):
                report.skipped.append(safe)
                continue
            try:
                converted = convert_image(
                    data,
                    target_format=fmt,
                    width=width,
                    height=height,
                )
            except UnidentifiedImageError:
                report.failed.append((safe, "not a recognizable image"))
                continue
            except Exception as e:  # noqa: BLE001
                report.failed.append((safe, str(e)))
                continue

            zout.writestr(_output_name(safe, target_ext), converted)
            report.converted.append(safe)

    return out_buf.getvalue(), report
