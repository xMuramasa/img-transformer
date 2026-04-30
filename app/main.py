"""FastAPI application entry-point."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import UnidentifiedImageError
from pydantic import ValidationError

from app.schemas import ConvertOptions
from transform import convert_image, convert_many, convert_zip
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
