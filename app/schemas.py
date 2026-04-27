"""Pydantic models / parsing helpers for API requests."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from transform import normalize_format

TargetFormat = Literal["webp", "png", "jpeg", "jpg", "gif", "bmp", "tiff", "tif"]


class ConvertOptions(BaseModel):
    """Options shared by every conversion endpoint."""

    target_format: str = Field(..., description="Target image format.")
    width: int | None = Field(None, ge=1, le=20_000)
    height: int | None = Field(None, ge=1, le=20_000)

    @field_validator("target_format")
    @classmethod
    def _validate_format(cls, v: str) -> str:
        # Raises ValueError on unsupported format.
        return normalize_format(v)
