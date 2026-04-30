"""Pydantic models / parsing helpers for API requests."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from transform import ALPHA_CAPABLE_FORMATS, normalize_format

TargetFormat = Literal["webp", "png", "jpeg", "jpg", "gif", "bmp", "tiff", "tif"]


class ConvertOptions(BaseModel):
    """Options shared by every conversion endpoint."""

    target_format: str = Field(..., description="Target image format.")
    width: int | None = Field(None, ge=1, le=20_000)
    height: int | None = Field(None, ge=1, le=20_000)
    remove_bg: bool = Field(False, description="Remove image background.")

    @field_validator("target_format")
    @classmethod
    def _validate_format(cls, v: str) -> str:
        # Raises ValueError on unsupported format.
        return normalize_format(v)

    def model_post_init(self, __context: object) -> None:  # noqa: D401
        if self.remove_bg and self.target_format not in ALPHA_CAPABLE_FORMATS:
            allowed = ", ".join(sorted(ALPHA_CAPABLE_FORMATS))
            raise ValueError(
                f"remove_bg requires an alpha-capable target format ({allowed})."
            )
