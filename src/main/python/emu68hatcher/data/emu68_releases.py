"""Emu68 release table loaded from reference/emu68_releases.yaml"""

import re
from functools import cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from emu68hatcher.data.data_manager import load_yaml_data

_KERNEL_KEYS = ("modern", "classic", "pistorm16")
_ASSET_KINDS = {"zip": "zips", "raw": "extras"}
_CHANNELS = ("stable", "alpha", "beta")
_DIALECT = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
_INT32_MAX = 2147483647


@cache
def load_emu68_releases() -> dict:
    """parsed emu68_releases.yaml; raises ValueError on a malformed table"""
    data = load_yaml_data("emu68_releases")
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("emu68_releases.yaml: unsupported schema_version")
    seen = set()
    for release in data.get("releases") or []:
        version = release.get("version")
        if not isinstance(version, str) or version in seen:
            raise ValueError(f"emu68_releases.yaml: bad or duplicate version {version!r}")
        seen.add(version)
        if release.get("tag") != f"v{version}":
            raise ValueError(f"emu68_releases.yaml: tag does not match {version}")
        if release.get("channel") not in _CHANNELS:
            raise ValueError(f"emu68_releases.yaml: {version} has an unknown channel")
        dialect = release.get("dialect")
        if not isinstance(dialect, str) or not _DIALECT.fullmatch(dialect):
            raise ValueError(f"emu68_releases.yaml: {version} needs a dialect")
        if set(release.get("kernels") or {}) != set(_KERNEL_KEYS):
            raise ValueError(f"emu68_releases.yaml: {version} needs kernels {_KERNEL_KEYS}")
        for asset in release.get("assets") or []:
            if asset.get("kind") not in _ASSET_KINDS or not asset.get("role"):
                raise ValueError(f"emu68_releases.yaml: bad asset in {version}")
    if not seen:
        raise ValueError("emu68_releases.yaml: no releases")
    return data


def download_table() -> dict[str, dict]:
    """version -> {"tag", "zips": [asset], "extras"?: [...]}; url/size/sha256 may be None"""
    table = {}
    for release in load_emu68_releases()["releases"]:
        entry: dict = {"tag": release["tag"]}
        for asset in release["assets"]:
            key = _ASSET_KINDS[asset["kind"]]
            entry.setdefault(key, []).append(
                {
                    "role": asset["role"],
                    "filename": asset["filename"],
                    "url": asset.get("url"),
                    "size": asset.get("size"),
                    "sha256": asset.get("sha256"),
                }
            )
        table[release["version"]] = entry
    return table


def kernel_table() -> dict[str, dict[str, str]]:
    """version -> {"modern", "classic", "pistorm16"} kernel file names"""
    return {
        release["version"]: {key: release["kernels"][key] for key in _KERNEL_KEYS}
        for release in load_emu68_releases()["releases"]
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


def _asset_payload(release: dict, asset: dict, repository: str) -> dict:
    filename = asset["filename"]
    url = asset.get("url")
    expected = f"https://github.com/{repository}/releases/download/{release['tag']}/{filename}"
    if url is not None and url != expected:
        raise ValueError(f"{release['version']}: url for {filename} is not the release asset url")
    return {
        "role": asset["role"],
        "kind": asset["kind"],
        "filename": filename,
        "url": url,
        "size": asset.get("size"),
        "sha256": asset.get("sha256"),
    }


def release_index_payload(revision: int, commit: str) -> dict:
    """the emu68-releases-v1 payload for the table as it is, validated as the Manager reads it"""
    table = load_emu68_releases()
    repository = table["repository"]
    payload = {
        "format": "emu68-releases-v1",
        "schema_version": 1,
        "revision": revision,
        "source_commit": commit,
        "repository": repository,
        "releases": [
            {
                "version": release["version"],
                "tag": release["tag"],
                "channel": release["channel"],
                "prerelease": release["channel"] != "stable",
                "dialect": release["dialect"],
                "assets": [_asset_payload(release, a, repository) for a in release["assets"]],
                "kernels": dict(release["kernels"]),
            }
            for release in table["releases"]
        ],
    }
    return Emu68ReleaseIndex.model_validate(payload).model_dump(mode="json")
