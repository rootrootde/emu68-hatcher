"""One signed catalog per app version, carried by the update manifest."""

from fnmatch import fnmatchcase
from functools import lru_cache
from typing import Any

from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field, model_validator

from emu68hatcher import __version__
from emu68hatcher.config.network_models import NetworkStack
from emu68hatcher.data.catalog import CatalogData, CatalogSnapshot, load_catalog_source
from emu68hatcher.data.package_loader import get_local_packages_dir


def catalog_schema_number(version: str) -> int:
    # one number per app version: a client parses only the catalog made for it and keeps
    # every other one opaque, so new package fields never break an older client's manifest
    parsed = Version(version)
    return parsed.major * 1_000_000 + parsed.minor * 1_000 + parsed.micro


def catalog_id(version: str) -> str:
    return "hatcher-" + version.replace(".", "-")


CATALOG_SCHEMA_VERSION = catalog_schema_number(__version__)


class CatalogTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # min/max stay for clients before 1.3.0, which pick a catalog by version range;
    # this client wants the exact version
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    min_hatcher_version: str
    max_hatcher_version_exclusive: str | None = None
    catalog_schema_version: int = Field(ge=1)

    def is_for(self, version: str) -> bool:
        return (
            self.min_hatcher_version == version
            and self.catalog_schema_version == catalog_schema_number(version)
        )


class CatalogRelease(CatalogTarget):
    revision: int = Field(ge=1)
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    # a catalog for another app version stays opaque: its package fields may be unknown here
    packages: dict[str, Any]
    bundles: dict[str, Any]
    adf_rules: dict[str, Any]

    @model_validator(mode="after")
    def validate_own_catalog(self):
        if self.catalog_schema_version == CATALOG_SCHEMA_VERSION:
            self.data()
        return self

    def data(self) -> CatalogData:
        if self.catalog_schema_version != CATALOG_SCHEMA_VERSION:
            raise ValueError(f"catalog is for Hatcher {self.min_hatcher_version}")
        return CatalogData(packages=self.packages, bundles=self.bundles, adf_rules=self.adf_rules)

    def snapshot(self) -> CatalogSnapshot:
        return CatalogSnapshot.create(
            self.data(), id=self.id, revision=self.revision, source_commit=self.source_commit
        )


@lru_cache(maxsize=8)
def validate_client_catalog(snapshot: CatalogSnapshot) -> None:
    from emu68hatcher.data.package_resolver import resolve

    data = snapshot.data()
    baseline = load_catalog_source()
    engine_ids = {p.name for p in baseline.packages.values() if p.mandatory}
    engine_ids.update(stack.value.lower() for stack in NetworkStack)
    # Keep the font availability check required by existing catalog releases.
    engine_ids.update({"fonts_apparent", "fonts_magicwb"})
    missing = engine_ids - data.packages.keys()
    if missing:
        raise ValueError(f"catalog omits engine packages: {sorted(missing)}")
    for name, package in baseline.packages.items():
        if package.mandatory and not data.packages[name].mandatory:
            raise ValueError(f"catalog makes an engine package optional: {name}")
    local_root = get_local_packages_dir()
    for package in data.packages.values():
        download = package.download
        if download and download.source.value == "local":
            path = download.path
            if path and not (local_root / path).is_file():
                raise ValueError(f"local archive is unavailable: {path}")
            if not path:
                for rule in package.install:
                    pattern = rule.source
                    parts = pattern.split("/")
                    matches = [local_root]
                    for part in parts:
                        matches = [
                            child
                            for parent in matches
                            if parent.is_dir()
                            for child in parent.iterdir()
                            if fnmatchcase(child.name.lower(), part.lower())
                            and child.resolve().is_relative_to(local_root.resolve())
                        ]
                    if not matches:
                        raise ValueError(f"local source is unavailable: {package.name}: {pattern}")
    known_icons = {r.icon_set for rules in baseline.adf_rules.values() for r in rules if r.icon_set}
    known_adfs = {r.adf for rules in baseline.adf_rules.values() for r in rules}
    for rules in data.adf_rules.values():
        for rule in rules:
            if rule.adf not in known_adfs or (rule.icon_set and rule.icon_set not in known_icons):
                raise ValueError(f"unsupported ADF or icon set: {rule.adf}, {rule.icon_set}")
    versions = set(baseline.adf_rules)
    emu_versions = {v for p in baseline.packages.values() for v in p.emu68_versions or []} or {None}
    for version in sorted(versions):
        for emu_version in emu_versions:
            available = [
                p
                for p in data.packages.values()
                if p.matches_version(version) and p.matches_emu68(emu_version)
            ]
            available_names = {p.name for p in available}
            required_names = {
                p.name
                for p in baseline.packages.values()
                if p.mandatory and p.matches_version(version) and p.matches_emu68(emu_version)
            }
            if not required_names <= available_names:
                raise ValueError(
                    f"{version}/{emu_version}: engine packages are incompatible: "
                    f"{sorted(required_names - available_names)}"
                )
            for package in available:
                result = resolve({package.name}, version, emu_version, packages=available)
                if result.unsatisfiable:
                    raise ValueError(
                        f"{version}/{emu_version}: {package.name}: {result.unsatisfiable}"
                    )
