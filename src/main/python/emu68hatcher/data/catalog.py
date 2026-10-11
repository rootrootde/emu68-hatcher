"""Complete catalogs and build-local catalog selection."""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from emu68hatcher.data.catalog_graph import validate_dependency_graph
from emu68hatcher.data.package_schema import ADFRule, Bundle, Package, SourceType

DATA_DIR = Path(__file__).parent


def validate_relative_path(value: str) -> str:
    if (
        value.startswith(("/", "\\"))
        or "\\" in value
        or ":" in value
        or ".." in value.split("/")
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError(f"unsafe catalog path: {value!r}")
    return value


class CatalogData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    packages: dict[str, Package]
    bundles: dict[str, Bundle]
    adf_rules: dict[str, list[ADFRule]]

    @model_validator(mode="after")
    def validate_contents(self):
        if not self.packages or not self.adf_rules:
            raise ValueError("catalog must contain packages and ADF rules")
        for key, package in self.packages.items():
            if key != package.name:
                raise ValueError(f"package index differs from name: {key}")
            if package.bundle and package.bundle not in self.bundles:
                raise ValueError(f"unknown bundle: {package.bundle}")
            for rule in [*package.install, *package.relocate]:
                validate_relative_path(rule.source)
                validate_relative_path(rule.dest)
                if getattr(rule, "rename", None):
                    validate_relative_path(rule.rename)
            for script in package.scripts:
                validate_relative_path(script.target)
                script.content.encode("iso-8859-1")
                script.name.encode("iso-8859-1")
                if any(c in script.name for c in "\r\n"):
                    raise ValueError("script name must occupy one line")
            for menu in [
                *package.menu_entries,
                *([package.menu_entry] if package.menu_entry else []),
            ]:
                menu.model_dump_json().encode("iso-8859-1")
            download = package.download
            if download:
                if download.filename:
                    validate_relative_path(download.filename)
                    if "/" in download.filename or download.filename in {".", ".."}:
                        raise ValueError("download filename must be a plain filename")
                if download.path:
                    validate_relative_path(download.path)
                if download.source != SourceType.LOCAL:
                    if not download.hash or not re.fullmatch(r"[0-9a-fA-F]{32}", download.hash):
                        raise ValueError(f"{key}: remote download needs an MD5 hash")
                    if download.source == SourceType.GITHUB and not download.filename:
                        raise ValueError(f"{key}: remote download needs a filename")
                    if download.source == SourceType.GITHUB and not re.fullmatch(
                        r"[\w][\w.-]*/[\w][\w.-]*", download.repo or ""
                    ):
                        raise ValueError(f"{key}: invalid GitHub repository")
                for url in (download.url, download.backup_url):
                    if url:
                        from pydantic import AnyHttpUrl, TypeAdapter

                        TypeAdapter(AnyHttpUrl).validate_python(url)
        for key, bundle in self.bundles.items():
            if key != bundle.id:
                raise ValueError(f"bundle index differs from id: {key}")
        for version, rules in self.adf_rules.items():
            if not re.fullmatch(r"\d+(?:\.\d+)+", version):
                raise ValueError(f"invalid ADF version: {version}")
            for rule in rules:
                validate_relative_path(rule.source)
                validate_relative_path("" if rule.dest == "/" else rule.dest)
                if rule.rename:
                    validate_relative_path(rule.rename)
                if rule.package and rule.package not in self.packages:
                    raise ValueError(f"unknown ADF package: {rule.package}")
        validate_dependency_graph(list(self.packages.values()))
        return self


@dataclass(frozen=True)
class CatalogSnapshot:
    id: str
    revision: int
    source_commit: str
    _content: bytes = field(repr=False)

    @classmethod
    def create(cls, data: CatalogData, *, id="bundled", revision=0, source_commit="bundled"):
        return cls(id, revision, source_commit, data.model_dump_json(by_alias=True).encode())

    def data(self) -> CatalogData:
        return CatalogData.model_validate_json(self._content)

    def packages(self) -> list[Package]:
        return [Package.model_validate(p) for p in json.loads(self._content)["packages"].values()]

    def package(self, name: str) -> Package | None:
        raw = json.loads(self._content)["packages"].get(name.lower())
        return Package.model_validate(raw) if raw else None

    def bundles(self) -> dict[str, Bundle]:
        return {
            k: Bundle.model_validate(v) for k, v in json.loads(self._content)["bundles"].items()
        }

    def adf_rules(self) -> dict[str, list[ADFRule]]:
        return {
            k: [ADFRule.model_validate(r) for r in v]
            for k, v in json.loads(self._content)["adf_rules"].items()
        }


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def read_catalog_yaml(path: Path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)


def load_catalog_source(
    packages_dir: Path = DATA_DIR / "packages", reference_dir: Path | None = None
) -> CatalogData:
    reference_dir = reference_dir or packages_dir.parent / "reference"
    packages = {}
    errors = []
    for path in sorted(packages_dir.glob("*.yaml")):
        try:
            package = Package.model_validate(read_catalog_yaml(path))
            if package.name.lower() in packages:
                raise ValueError(f"duplicate package: {package.name}")
            packages[package.name] = package
        except (OSError, ValueError, yaml.YAMLError) as error:
            errors.append(f"{path.name}: {error}")
    if errors:
        raise ValueError("invalid package files:\n  " + "\n  ".join(errors))
    bundles = read_catalog_yaml(reference_dir / "bundles.yaml")
    adf_rules = read_catalog_yaml(reference_dir / "adf_rules.yaml")
    if not isinstance(bundles, dict) or not isinstance(adf_rules, dict):
        raise ValueError("bundles and ADF rules must be mappings")
    if any(not isinstance(value, dict) for value in bundles.values()):
        raise ValueError("each bundle must be a mapping")
    if any(not isinstance(key, str) for key in adf_rules):
        raise ValueError("ADF version keys must be quoted strings")
    return CatalogData(
        packages=packages,
        bundles={k: Bundle.model_validate({"id": k, **v}) for k, v in bundles.items()},
        adf_rules=adf_rules,
    )


@cache
def bundled_catalog() -> CatalogSnapshot:
    return CatalogSnapshot.create(load_catalog_source())


def clear_bundled_catalog_cache():
    bundled_catalog.cache_clear()


_catalog_context: ContextVar[CatalogSnapshot | None] = ContextVar("package_catalog", default=None)


@contextmanager
def use_catalog(snapshot: CatalogSnapshot):
    token = _catalog_context.set(snapshot)
    try:
        yield snapshot
    finally:
        _catalog_context.reset(token)


def get_catalog_snapshot() -> CatalogSnapshot:
    snapshot = _catalog_context.get()
    if snapshot is not None:
        return snapshot
    from emu68hatcher.data.update_manifest import get_active_selection

    return get_active_selection().catalog or bundled_catalog()
