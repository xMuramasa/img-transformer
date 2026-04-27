# syntax=docker/dockerfile:1.7
FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install runtime deps first for better layer caching.
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
      "fastapi>=0.110" \
      "uvicorn[standard]>=0.27" \
      "pillow>=10.0" \
      "python-multipart>=0.0.9" \
      "pydantic>=2.6"

COPY app ./app
COPY transform ./transform

# Drop privileges.
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
