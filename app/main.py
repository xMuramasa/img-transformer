"""FastAPI application entry-point."""

from __future__ import annotations

from dataclasses import asdict
import json
import tempfile
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import UnidentifiedImageError
from pydantic import ValidationError

from app.schemas import ConvertOptions, PdfOptimizeOptions
from transform import (
    CorruptedImageError,
    ImageOptimizationError,
    OversizedImageError,
    UnsupportedImageFormatError,
    convert_image,
    convert_many,
    convert_zip,
    optimize_image_for_pdf,
)
from transform.background import BackgroundRemovalUnavailable
from transform.image import extension_for

# --- Limits --------------------------------------------------------------
MAX_IMAGE_BYTES = 50 * 1024 * 1024   # 50 MB per single image
MAX_FILES_TOTAL = 200 * 1024 * 1024  # 200 MB combined for multi-file
MAX_ZIP_BYTES = 200 * 1024 * 1024    # 200 MB zip upload

app = FastAPI(title="img-transformer", version="0.1.0")

_STATIC_DIR = Path(__file__).parent / "static"


# --- Helpers -------------------------------------------------------------
def _parse_options(
    target_format: str,
    width: int | None,
    height: int | None,
    remove_bg: bool = False,
) -> ConvertOptions:
    try:
        return ConvertOptions(
            target_format=target_format,
            width=width,
            height=height,
            remove_bg=remove_bg,
        )
    except (ValidationError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _parse_pdf_options(
    max_width: int,
    jpeg_quality: int,
    png_colors: int,
    grayscale: bool,
    max_pixels: int,
) -> PdfOptimizeOptions:
    try:
        return PdfOptimizeOptions(
            max_width=max_width,
            jpeg_quality=jpeg_quality,
            png_colors=png_colors,
            grayscale=grayscale,
            max_pixels=max_pixels,
        )
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


async def _read_capped(upload: UploadFile, cap: int) -> bytes:
    """Read an UploadFile fully, raising 413 if it exceeds *cap* bytes."""
    buf = BytesIO()
    remaining = cap
    while True:
        chunk = await upload.read(64 * 1024)
        if not chunk:
            break
        remaining -= len(chunk)
        if remaining < 0:
            raise HTTPException(status_code=413, detail="Upload too large.")
        buf.write(chunk)
    return buf.getvalue()


def _attachment(name: str) -> dict[str, str]:
    safe = name.replace('"', "")
    return {"Content-Disposition": f'attachment; filename="{safe}"'}


# --- Routes --------------------------------------------------------------
@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/convert/image")
async def api_convert_image(
    file: UploadFile = File(...),
    target_format: str = Form(...),
    width: int | None = Form(None),
    height: int | None = Form(None),
    remove_bg: bool = Form(False),
):
    opts = _parse_options(target_format, width, height, remove_bg)
    data = await _read_capped(file, MAX_IMAGE_BYTES)
    try:
        out = await run_in_threadpool(
            convert_image,
            data,
            target_format=opts.target_format,
            width=opts.width,
            height=opts.height,
            remove_bg=opts.remove_bg,
        )
    except UnidentifiedImageError:
        raise HTTPException(status_code=400, detail="File is not a recognizable image.")
    except BackgroundRemovalUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    stem = Path(file.filename or "image").stem or "image"
    out_name = f"{stem}.{extension_for(opts.target_format)}"
    media_type = f"image/{extension_for(opts.target_format)}"
    return StreamingResponse(
        BytesIO(out),
        media_type=media_type,
        headers=_attachment(out_name),
    )


@app.post("/api/optimize/pdf")
async def api_optimize_pdf_image(
    file: UploadFile = File(...),
    max_width: int = Form(1400),
    jpeg_quality: int = Form(68),
    png_colors: int = Form(64),
    grayscale: bool = Form(False),
    max_pixels: int = Form(40_000_000),
):
    opts = _parse_pdf_options(max_width, jpeg_quality, png_colors, grayscale, max_pixels)
    data = await _read_capped(file, MAX_IMAGE_BYTES)
    original_name = file.filename or "image"
    stem = Path(original_name).stem or "image"

    try:
        with tempfile.TemporaryDirectory(prefix="img-transformer-") as tmp:
            tmp_path = Path(tmp)
            src_path = tmp_path / original_name
            out_path = tmp_path / f"{stem}.optimized"
            src_path.write_bytes(data)

            result = await run_in_threadpool(
                optimize_image_for_pdf,
                str(src_path),
                str(out_path),
                max_width=opts.max_width,
                jpeg_quality=opts.jpeg_quality,
                png_colors=opts.png_colors,
                grayscale=opts.grayscale,
                max_pixels=opts.max_pixels,
            )
            output_bytes = Path(result.output_path).read_bytes()

    except OversizedImageError as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    except (UnsupportedImageFormatError, CorruptedImageError, ImageOptimizationError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    out_ext = result.selected_output_format
    media_type = "image/jpeg" if out_ext == "jpg" else "image/png"
    out_name = f"{stem}.{out_ext}"
    result_payload = asdict(result)
    result_payload["output_path"] = out_name
    result_payload["input_path"] = original_name

    return StreamingResponse(
        BytesIO(output_bytes),
        media_type=media_type,
        headers={
            **_attachment(out_name),
            "X-Optimization-Result": json.dumps(result_payload),
        },
    )


@app.post("/api/convert/files")
async def api_convert_files(
    files: list[UploadFile] = File(...),
    target_format: str = Form(...),
    width: int | None = Form(None),
    height: int | None = Form(None),
    remove_bg: bool = Form(False),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")
    opts = _parse_options(target_format, width, height, remove_bg)

    payloads: list[tuple[str, bytes]] = []
    total = 0
    for f in files:
        data = await f.read()
        total += len(data)
        if total > MAX_FILES_TOTAL:
            raise HTTPException(status_code=413, detail="Combined upload too large.")
        payloads.append((f.filename or "image", data))

    try:
        zip_bytes, report = await run_in_threadpool(
            convert_many,
            payloads,
            target_format=opts.target_format,
            width=opts.width,
            height=opts.height,
            remove_bg=opts.remove_bg,
        )
    except BackgroundRemovalUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return StreamingResponse(
        BytesIO(zip_bytes),
        media_type="application/zip",
        headers={
            **_attachment("converted.zip"),
            "X-Conversion-Report": json.dumps(report.as_dict()),
        },
    )


@app.post("/api/convert/zip")
async def api_convert_zip(
    file: UploadFile = File(...),
    target_format: str = Form(...),
    width: int | None = Form(None),
    height: int | None = Form(None),
    remove_bg: bool = Form(False),
):
    opts = _parse_options(target_format, width, height, remove_bg)
    data = await _read_capped(file, MAX_ZIP_BYTES)
    try:
        zip_bytes, report = await run_in_threadpool(
            convert_zip,
            data,
            target_format=opts.target_format,
            width=opts.width,
            height=opts.height,
            remove_bg=opts.remove_bg,
        )
    except BackgroundRemovalUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid zip: {e}") from e

    stem = Path(file.filename or "archive").stem or "archive"
    out_name = f"{stem}_converted.zip"
    return StreamingResponse(
        BytesIO(zip_bytes),
        media_type="application/zip",
        headers={
            **_attachment(out_name),
            "X-Conversion-Report": json.dumps(report.as_dict()),
        },
    )


# --- Static frontend (mounted last so /api/* keeps priority) -------------
if _STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
else:  # pragma: no cover — defensive fallback during early dev
    @app.get("/", response_class=HTMLResponse)
    def _root() -> str:
        return "<h1>img-transformer</h1><p>Static UI not built.</p>"
