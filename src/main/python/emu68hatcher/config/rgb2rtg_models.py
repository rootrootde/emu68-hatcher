"""RGB2RTG build settings."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


class RGB2RTGConfig(BaseModel):
    enabled: bool = False
    archive: Path | None = None
    video: Literal["pal", "ntsc"] = "pal"

    model_config = ConfigDict(extra="forbid")

    @field_validator("archive", mode="before")
    @classmethod
    def convert_archive(cls, value):
        return Path(value) if value else None
