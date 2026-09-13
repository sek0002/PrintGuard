"""Validated, locally measured model tuning presets."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ModelRecommendation(BaseModel):
    """Stores slider settings with their evaluation limits."""

    model_config = ConfigDict(extra="forbid", strict=True)

    sensitivity: float = Field(ge=0.2, le=5.0)
    threshold: float = Field(ge=0.05, le=1.0)
    consecutive: int | None = Field(default=None, ge=1, le=30)
    recommended: bool
    summary: str = Field(min_length=1, max_length=2000)
    evaluated_at: str
