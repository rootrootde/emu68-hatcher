"""AGS selection and partition reservations."""

from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

AGSRole = Literal["whdload", "games", "work", "media"]
AGS_ROLES: tuple[AGSRole, ...] = get_args(AGSRole)


class AGSComponents(BaseModel):
    model_config = ConfigDict(extra="forbid")

    whdload: Literal[True] = True
    games: bool = True
    work: bool = True
    media: bool = False

    def selected_roles(self) -> tuple[AGSRole, ...]:
        return tuple(role for role in AGS_ROLES if getattr(self, role))


class AGSImportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_image: Path
    enabled: bool = True
    components: AGSComponents = Field(default_factory=AGSComponents)
    allocation_state: Literal["pending", "ready"] = "pending"
    migration_notice: str | None = None
    legacy_content_device: str | None = Field(default=None, pattern=r"^[A-Z]{2,3}\d+$")

    @field_validator("source_image", mode="before")
    @classmethod
    def _local_image(cls, value):
        if not str(value).strip() or str(value).startswith(("\\\\", "//")):
            raise ValueError("AGS requires a local image file; UNC sources are not supported")
        return value


class AGSPartitionReservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: AGSRole
    minimum_size: int = Field(gt=0)
