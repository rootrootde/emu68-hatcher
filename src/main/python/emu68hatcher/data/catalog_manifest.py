"""Versioned catalog ranges carried by signed manifests."""

from fnmatch import fnmatchcase
from functools import lru_cache
from typing import Any

from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field, model_validator

from emu68hatcher.data.catalog import (
    DATA_DIR,
    CatalogData,
    CatalogSnapshot,
    load_catalog_source,
    use_catalog,
)


class CatalogTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    min_hatcher_version: str
    max_hatcher_version_exclusive: str | None = None
    catalog_schema_version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_range(self):
        lower = Version(self.min_hatcher_version)
        if self.max_hatcher_version_exclusive is not None:
            if Version(self.max_hatcher_version_exclusive) <= lower:
                raise ValueError("catalog version range is empty")
        return self

    def matches(self, version: str) -> bool:
        current = Version(version)
        return current >= Version(self.min_hatcher_version) and (
            self.max_hatcher_version_exclusive is None
            or current < Version(self.max_hatcher_version_exclusive)
        )


class CatalogRelease(CatalogTarget):
    revision: int = Field(ge=1)
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    # unknown future schemas remain opaque until a matching engine understands them.
    packages: dict[str, Any]
    bundles: dict[str, Any]
    adf_rules: dict[str, Any]

    @model_validator(mode="after")
    def validate_known_schema(self):
        if self.catalog_schema_version == 1:
            self.data()
        return self

    def data(self) -> CatalogData:
        if self.catalog_schema_version != 1:
            raise ValueError(f"unsupported catalog schema: {self.catalog_schema_version}")
        return CatalogData(packages=self.packages, bundles=self.bundles, adf_rules=self.adf_rules)

    def snapshot(self) -> CatalogSnapshot:
        return CatalogSnapshot.create(
            self.data(), id=self.id, revision=self.revision, source_commit=self.source_commit
        )


def validate_ranges(catalogs: list[CatalogRelease]) -> None:
    ids = [c.id for c in catalogs]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate catalog id")
    ordered = sorted(catalogs, key=lambda c: Version(c.min_hatcher_version))
    for left, right in zip(ordered, ordered[1:], strict=False):
        if left.max_hatcher_version_exclusive is None or (
            Version(left.max_hatcher_version_exclusive) > Version(right.min_hatcher_version)
        ):
            raise ValueError(f"overlapping catalog ranges: {left.id}, {right.id}")


@lru_cache(maxsize=8)
def validate_client_catalog(snapshot: CatalogSnapshot) -> None:
    from emu68hatcher.data.package_resolver import resolve
    from emu68hatcher.data.themes import load_workbench_themes

    data = snapshot.data()
    baseline = load_catalog_source()
    engine_ids = {p.name for p in baseline.packages.values() if p.mandatory}
    engine_ids.update({"roadshow", "amitcp_ng", "miamidx"})
    for theme in load_workbench_themes().values():
        engine_ids.update(theme.required_packages)
    missing = engine_ids - data.packages.keys()
    if missing:
        raise ValueError(f"catalog omits engine packages: {sorted(missing)}")
    for name, package in baseline.packages.items():
        if package.mandatory and not data.packages[name].mandatory:
            raise ValueError(f"catalog makes an engine package optional: {name}")
    local_root = DATA_DIR / "local_packages"
    for package in data.packages.values():
        download = package.download
        if download and download.source.value == "local":
            path = download.path
            if path and not (DATA_DIR / "local_packages" / path).is_file():
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
    with use_catalog(snapshot):
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
                    result = resolve(
                        {package.name}, set(), version, emu_version, packages=available
                    )
                    if result.unsatisfiable:
                        raise ValueError(
                            f"{version}/{emu_version}: {package.name}: {result.unsatisfiable}"
                        )
