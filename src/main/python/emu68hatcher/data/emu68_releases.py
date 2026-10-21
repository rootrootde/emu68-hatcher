"""Emu68 release table loaded from reference/emu68_releases.yaml"""

import re
from functools import cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from emu68hatcher.data.data_manager import load_yaml_data

_KERNEL_KEYS = ("modern", "classic", "pistorm16")
_ASSET_KINDS = {"zip": "zips", "raw": "extras"}
_DIALECT = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
_INT32_MAX = 2147483647


class _TableAsset(BaseModel):
    role: str = Field(min_length=1)
    kind: Literal["zip", "raw"]
    filename: str
    url: str | None = None
    size: int | None = None
    sha256: str | None = None


class _TableRelease(BaseModel):
    version: str
    tag: str
    channel: Literal["stable", "alpha", "beta"]
    dialect: str = Field(pattern=_DIALECT.pattern)
    assets: list[_TableAsset]
    kernels: dict[str, str]

    @model_validator(mode="after")
    def _consistent(self):
        if self.tag != f"v{self.version}":
            raise ValueError(f"tag does not match {self.version}")
        if set(self.kernels) != set(_KERNEL_KEYS):
            raise ValueError(f"{self.version} needs kernels {_KERNEL_KEYS}")
        return self


class _Emu68Table(BaseModel):
    """emu68_releases.yaml as written; url, size and sha256 may still be unpinned"""

    schema_version: Literal[1]
    repository: str
    releases: list[_TableRelease] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_versions(self):
        versions = [release.version for release in self.releases]
        if len(versions) != len(set(versions)):
            raise ValueError("duplicate release version")
        return self


@cache
def load_emu68_releases() -> _Emu68Table:
    """validated emu68_releases.yaml; raises ValueError on a malformed table"""
    return _Emu68Table.model_validate(load_yaml_data("emu68_releases"))


def download_table() -> dict[str, dict]:
    """version -> {"tag", "zips": [asset], "extras"?: [...]}; url/size/sha256 may be None"""
    table = {}
    for release in load_emu68_releases().releases:
        entry: dict = {"tag": release.tag}
        for asset in release.assets:
            entry.setdefault(_ASSET_KINDS[asset.kind], []).append(
                {
                    "role": asset.role,
                    "filename": asset.filename,
                    "url": asset.url,
                    "size": asset.size,
                    "sha256": asset.sha256,
                }
            )
        table[release.version] = entry
    return table


def kernel_table() -> dict[str, dict[str, str]]:
    """version -> {"modern", "classic", "pistorm16"} kernel file names"""
    return {
        release.version: {key: release.kernels[key] for key in _KERNEL_KEYS}
        for release in load_emu68_releases().releases
    }


class Emu68Asset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    kind: Literal["zip", "raw"]
    filename: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    url: str | None = Field(default=None, pattern=r"^https://github\.com/[^\s]+$")
    size: int | None = Field(default=None, gt=0, le=_INT32_MAX)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _zip_is_pinned(self):
        # the Manager verifies every zip it installs; a raw file may stay unpinned
        if self.kind == "zip" and not (self.url and self.size and self.sha256):
            raise ValueError(f"zip asset {self.filename} needs url, size and sha256")
        return self


class Emu68Release(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1)
    tag: str = Field(min_length=2)
    channel: Literal["stable", "alpha", "beta"]
    prerelease: bool
    dialect: str = Field(pattern=_DIALECT.pattern)
    assets: list[Emu68Asset] = Field(min_length=1)
    kernels: dict[str, str]

    @model_validator(mode="after")
    def _consistent(self):
        if self.tag != f"v{self.version}":
            raise ValueError(f"tag does not match {self.version}")
        if self.prerelease != (self.channel != "stable"):
            raise ValueError(f"{self.version}: prerelease flag contradicts the channel")
        if set(self.kernels) != set(_KERNEL_KEYS):
            raise ValueError(f"{self.version} needs kernels {_KERNEL_KEYS}")
        return self


class Emu68ReleaseIndex(BaseModel):
    """the signed emu68-releases-v1.json payload Emu68 Manager reads"""

    model_config = ConfigDict(extra="forbid")

    format: Literal["emu68-releases-v1"]
    schema_version: Literal[1]
    revision: int = Field(ge=1, le=_INT32_MAX)
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    repository: str = Field(pattern=r"^[\w][\w.-]*/[\w][\w.-]*$")
    releases: list[Emu68Release] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_versions(self):
        versions = [release.version for release in self.releases]
        if len(versions) != len(set(versions)):
            raise ValueError("duplicate release version")
        return self


def _asset_payload(release: _TableRelease, asset: _TableAsset, repository: str) -> dict:
    expected = f"https://github.com/{repository}/releases/download/{release.tag}/{asset.filename}"
    if asset.url is not None and asset.url != expected:
        raise ValueError(
            f"{release.version}: url for {asset.filename} is not the release asset url"
        )
    return asset.model_dump()


def release_index_payload(revision: int, commit: str) -> dict:
    """the emu68-releases-v1 payload for the table as it is, validated as the Manager reads it"""
    table = load_emu68_releases()
    payload = {
        "format": "emu68-releases-v1",
        "schema_version": 1,
        "revision": revision,
        "source_commit": commit,
        "repository": table.repository,
        "releases": [
            {
                "version": release.version,
                "tag": release.tag,
                "channel": release.channel,
                "prerelease": release.channel != "stable",
                "dialect": release.dialect,
                "assets": [_asset_payload(release, a, table.repository) for a in release.assets],
                "kernels": dict(release.kernels),
            }
            for release in table.releases
        ],
    }
    return Emu68ReleaseIndex.model_validate(payload).model_dump(mode="json")
