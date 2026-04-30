# img-transformer

A small FastAPI web app to convert and resize images. Supports three input
modes:

- **Single image** — convert one file (e.g., PNG → WebP).
- **Multiple files** — upload many images, get a zip of converted ones.
- **Zip archive** — upload a `.zip`, get a converted `.zip` back.

Originally a Jupyter notebook (`img_to_webp.ipynb`); now a production-shaped app.

## Project layout

```
app/                 FastAPI surface
  main.py            routes + static mount
  schemas.py         request validation
  static/index.html  drag-and-drop UI (vanilla JS)
transform/           pure-Python core (no web deps)
  image.py           single-image convert + resize
  archive.py         zip / multi-file conversion
tests/               pytest suite
```

## Run locally

Requires [uv](https://docs.astral.sh/uv/) and Python 3.14+.

```bash
uv sync --extra dev
uv run uvicorn app.main:app --reload
```

Then open <http://localhost:8000>.

## Tests

```bash
uv run pytest
```

## Docker

```bash
docker build -t img-transformer .
docker run --rm -p 8000:8000 img-transformer
```

## API

All endpoints accept `multipart/form-data` with form fields
`target_format` (one of `webp`, `png`, `jpeg`, `gif`, `bmp`, `tiff`),
optional `width` / `height` integers, and optional `remove_bg` (boolean).

| Method | Path                  | File field | Response                  |
| ------ | --------------------- | ---------- | ------------------------- |
| POST   | `/api/convert/image`  | `file`     | converted image           |
| POST   | `/api/convert/files`  | `files[]`  | zip of converted images   |
| POST   | `/api/convert/zip`    | `file`     | zip of converted images   |
| GET    | `/healthz`            | —          | `{"status": "ok"}`        |

Bulk endpoints include a JSON `X-Conversion-Report` header summarizing
converted / skipped / failed entries.

### Background removal (optional)

Install the extra to enable the `remove_bg` flag (uses [`rembg`](https://github.com/danielgatis/rembg)
with the U2Net ONNX model, ~170 MB downloaded on first use):

```bash
uv sync --extra bgremove
```

When `remove_bg=true`, the target format must be alpha-capable
(`webp`, `png`, `gif`, `tiff`). Without the extra installed, the endpoints
return `503` with a clear message.

### Limits

- 50 MB per single image
- 200 MB combined for multi-file uploads
- 200 MB per zip upload

### Security notes

- Zip-slip protection: entries with absolute paths or `..` segments are rejected.
- Target format is allow-listed; arbitrary Pillow format names are not accepted.
- Uploads are processed in-memory and never persisted to disk.

## Credits

Built by Martin Salinas with help from GitHub Copilot.
