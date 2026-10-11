#!/usr/bin/env python3
"""Add the latest release artifacts to a manifest payload."""

import argparse
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

from emu68hatcher.data.catalog import DATA_DIR, load_catalog_source, read_catalog_yaml
from emu68hatcher.data.catalog_manifest import (
    CatalogRelease,
    CatalogTarget,
    validate_client_catalog,
)
from emu68hatcher.data.update_manifest import (
    _MAX_MANIFEST_BYTES,
    UpdateManifestV1,
    UpdateManifestV2,
    verify_manifest_bytes,
)
from pydantic import BaseModel, ConfigDict, Field

_ARTIFACT_RE = re.compile(
    r"^emu68hatcher-(?P<version>.+)-(?P<platform>"
    r"(?:windows|linux|macos)-(?:x64|arm64))\.(?:exe|deb|dmg)$"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _release_from_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data.get("release"), dict):
        return data["release"]
    return data


def _release_payload(release: dict, asset_dir: Path) -> dict:
    tag = release.get("tag_name", "")
    version = tag.removeprefix("v")
    release_url = release.get("html_url")
    if not version or not release_url:
        raise ValueError("release JSON has no tag_name or html_url")

    urls = {
        asset.get("name"): asset.get("browser_download_url")
        for asset in release.get("assets", [])
        if asset.get("name") and asset.get("browser_download_url")
    }
    artifacts = {}
    for path in sorted(asset_dir.iterdir()):
        if not path.is_file():
            continue
        match = _ARTIFACT_RE.fullmatch(path.name)
        if not match or match.group("version") != version:
            continue
        url = urls.get(path.name)
        if not url:
            raise ValueError(f"release JSON has no URL for {path.name}")
        artifacts[match.group("platform")] = {
            "url": url,
            "filename": path.name,
            "sha256": _sha256(path),
            "size": path.stat().st_size,
        }
    if not artifacts:
        raise ValueError(f"no installer artifacts found for {tag}")
    return {
        "version": version,
        "release_url": release_url,
        "artifacts": artifacts,
    }


class PublishTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: CatalogTarget
    close_ranges: dict[str, str] = Field(default_factory=dict)
    legacy_hash_updates: list[str] = Field(default_factory=list)


def refresh_legacy_hashes(legacy: UpdateManifestV1, catalog, names: list[str]) -> UpdateManifestV1:
    fields = legacy.model_dump(mode="json", exclude_none=True)
    for name in names:
        old = fields["packages"].get(name)
        package = catalog.packages.get(name)
        if old is None or package is None or package.download is None:
            raise ValueError(f"unknown legacy download override: {name}")
        current = package.download.model_dump(mode="json", exclude_none=True)
        if any(current.get(key) != value for key, value in old.items() if key != "hash"):
            raise ValueError(f"legacy download source changed: {name}")
        old["hash"] = current["hash"]
    return UpdateManifestV1.model_validate(fields)


def build_catalog_manifest(
    target_config,
    previous,
    *,
    revision,
    source_commit,
    release,
    packages_dir=DATA_DIR / "packages",
    reference_dir=None,
):
    config = PublishTarget.model_validate(target_config)
    if config.target.catalog_schema_version != 1:
        raise ValueError("generator supports catalog schema 1 only")
    catalogs = []
    previous_revision = previous.revision if previous else 0
    if revision <= previous_revision:
        raise ValueError("publication revision must increase")
    previous_catalogs = {c.id: c for c in previous.catalogs} if previous else {}
    unknown = config.close_ranges.keys() - previous_catalogs.keys()
    if unknown:
        raise ValueError(f"cannot close unknown ranges: {sorted(unknown)}")
    for old in previous_catalogs.values():
        if old.id == config.target.id:
            continue
        if old.id in config.close_ranges:
            fields = old.model_dump(mode="json")
            fields["max_hatcher_version_exclusive"] = config.close_ranges[old.id]
            if fields["max_hatcher_version_exclusive"] != old.max_hatcher_version_exclusive:
                fields["revision"] = old.revision + 1
            old = CatalogRelease.model_validate(fields)
        catalogs.append(old)
    data = load_catalog_source(packages_dir, reference_dir)
    old = previous_catalogs.get(config.target.id)
    if old and old.min_hatcher_version != config.target.min_hatcher_version:
        raise ValueError("a new lower version bound needs a new catalog id")
    catalog = CatalogRelease(
        **config.target.model_dump(),
        revision=(old.revision + 1 if old else 1),
        source_commit=source_commit,
        **data.model_dump(mode="json", by_alias=True),
    )
    validate_client_catalog(catalog.snapshot())
    catalogs.append(catalog)
    return UpdateManifestV2(
        schema_version=2,
        revision=revision,
        hatcher=release,
        catalogs=sorted(catalogs, key=lambda c: c.id),
    )


def _previous(path, expected_schema):
    if path is None:
        return None
    manifest = verify_manifest_bytes(path.read_bytes())
    if manifest.schema_version != expected_schema:
        raise ValueError(f"expected schema {expected_schema}: {path}")
    return manifest


def _write_payload(path, manifest):
    content = (
        json.dumps(
            manifest.model_dump(mode="json", by_alias=True),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    )
    envelope = {
        "payload": manifest.model_dump(mode="json", by_alias=True),
        "signature": {"algorithm": "ed25519", "key_id": "updates-2026", "value": "A" * 88},
    }
    signed_size = len((json.dumps(envelope, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    if signed_size > _MAX_MANIFEST_BYTES:
        raise ValueError("generated manifest exceeds client size limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--target", type=Path, default=Path("updates/catalog-target.yaml"))
    parser.add_argument("--packages-dir", type=Path, default=DATA_DIR / "packages")
    parser.add_argument("--reference-dir", type=Path)
    parser.add_argument("--previous-v2", type=Path)
    parser.add_argument("--previous-v1", type=Path)
    parser.add_argument(
        "--legacy-source", type=Path, default=Path("updates/legacy-manifest-source.json")
    )
    parser.add_argument("--legacy-output", type=Path, required=True)
    parser.add_argument("--release-json", type=Path, required=True)
    parser.add_argument("--asset-dir", type=Path, required=True)
    parser.add_argument("--revision", type=int)
    args = parser.parse_args()

    previous = _previous(args.previous_v2, 2)
    legacy = _previous(args.previous_v1, 1) or UpdateManifestV1.model_validate(
        json.loads(args.legacy_source.read_text(encoding="utf-8"))
    )
    revision = (
        args.revision
        if args.revision is not None
        else max(int(time.time()), (previous.revision + 1 if previous else 1), legacy.revision + 1)
    )
    if revision <= legacy.revision:
        raise ValueError("legacy publication revision must increase")
    release = _release_payload(_release_from_json(args.release_json), args.asset_dir)
    source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    target_config = read_catalog_yaml(args.target)
    manifest = build_catalog_manifest(
        target_config,
        previous,
        revision=revision,
        source_commit=source_commit,
        release=release,
        packages_dir=args.packages_dir,
        reference_dir=args.reference_dir,
    )
    target = PublishTarget.model_validate(target_config)
    legacy = refresh_legacy_hashes(
        legacy,
        load_catalog_source(args.packages_dir, args.reference_dir),
        target.legacy_hash_updates,
    )
    legacy_fields = legacy.model_dump(mode="json")
    legacy_fields.update(revision=revision, hatcher=release)
    legacy = UpdateManifestV1.model_validate(legacy_fields)
    _write_payload(args.output, manifest)
    _write_payload(args.legacy_output, legacy)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
