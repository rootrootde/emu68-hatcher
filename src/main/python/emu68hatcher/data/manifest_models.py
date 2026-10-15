"""Update manifest schema models."""

from __future__ import annotations

import re
from typing import Any, Literal

from packaging.version import InvalidVersion, Version
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from emu68hatcher import __version__
from emu68hatcher.data.catalog_manifest import CatalogRelease

_SAFE_FILENAME_RE = re.compile(r"^[^/\\\x00-\x1f]+$")


class HatcherArtifact(BaseModel):
    """Installer offered for one host platform."""

    model_config = ConfigDict(extra="forbid")

    url: AnyHttpUrl
    filename: str
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    size: int | None = Field(default=None, gt=0)

    @field_validator("filename")
    @classmethod
    def _validate_filename(cls, value: str) -> str:
        if not _SAFE_FILENAME_RE.fullmatch(value) or value in {".", ".."}:
            raise ValueError("filename must be a plain file name")
        return value


class HatcherRelease(BaseModel):
    """Latest published application release."""

    model_config = ConfigDict(extra="forbid")

    version: str
    release_url: AnyHttpUrl
    artifacts: dict[str, HatcherArtifact] = Field(default_factory=dict)

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        try:
            Version(value)
        except InvalidVersion as error:
            raise ValueError("invalid application version") from error
        return value

    @field_validator("artifacts")
    @classmethod
    def _validate_platform_keys(
        cls, value: dict[str, HatcherArtifact]
    ) -> dict[str, HatcherArtifact]:
        allowed = {
            f"{os_name}-{arch}"
            for os_name in ("macos", "windows", "linux")
            for arch in ("x64", "arm64")
        }
        unknown = value.keys() - allowed
        if unknown:
            raise ValueError(f"unknown artifact platforms: {sorted(unknown)}")
        return value


class UpdateManifest(BaseModel):
    """schema 1: release metadata plus the frozen download overrides of 1.0 clients"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    revision: int = Field(ge=1)
    hatcher: HatcherRelease
    packages: dict[str, Any] = Field(default_factory=dict)


UpdateManifestV1 = UpdateManifest


class UpdateManifestV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2]
    revision: int = Field(ge=1)
    hatcher: HatcherRelease
    catalogs: list[CatalogRelease] = Field(min_length=1)

    def select_catalog(self, version: str = __version__) -> CatalogRelease | None:
        return next((c for c in self.catalogs if c.is_for(version)), None)


def parse_manifest_payload(payload: dict) -> UpdateManifestV1 | UpdateManifestV2:
    if payload.get("schema_version") == 2:
        return UpdateManifestV2.model_validate(payload)
    return UpdateManifestV1.model_validate(payload)


class ManifestSignature(BaseModel):
    """Detached signature stored beside the payload."""

    model_config = ConfigDict(extra="forbid")

    algorithm: Literal["ed25519"]
    key_id: str = Field(pattern=r"^[a-z0-9_-]+$")
    value: str


class SignedManifest(BaseModel):
    """Signed JSON envelope."""

    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any]
    signature: ManifestSignature
