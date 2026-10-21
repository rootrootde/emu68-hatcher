#!/usr/bin/env python3
"""Build the update payloads: one signed catalog for the latest released version."""

import argparse
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

from emu68hatcher import __version__
from emu68hatcher.data.catalog import DATA_DIR, load_catalog_source
from emu68hatcher.data.catalog_manifest import (
    CATALOG_SCHEMA_VERSION,
    CatalogRelease,
    catalog_id,
    validate_client_catalog,
)
from emu68hatcher.data.manifest_models import UpdateManifestV1, UpdateManifestV2
from emu68hatcher.data.manifest_signing import MAX_MANIFEST_BYTES, verify_manifest_bytes

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


def build_catalog_manifest(
    previous,
    *,
    revision,
    source_commit,
    release,
    packages_dir=DATA_DIR / "packages",
    reference_dir=None,
):
    version = release["version"]
    if version != __version__:
        raise ValueError(
            f"checkout version {__version__} is not the latest published release {version}; "
            "publish from that release's branch"
        )
    if previous and revision <= previous.revision:
        raise ValueError("publication revision must increase")
    # the catalog revision counts the publications for one version; the Amiga tools read it
    old = None
    if previous:
        old = next((c for c in previous.catalogs if c.id == catalog_id(version)), None)
    data = load_catalog_source(packages_dir, reference_dir)
    catalog = CatalogRelease(
        id=catalog_id(version),
        min_hatcher_version=version,
        catalog_schema_version=CATALOG_SCHEMA_VERSION,
        revision=old.revision + 1 if old else 1,
        source_commit=source_commit,
        **data.model_dump(mode="json", by_alias=True),
    )
    validate_client_catalog(catalog.snapshot())
    return UpdateManifestV2(
        schema_version=2, revision=revision, hatcher=release, catalogs=[catalog]
    )


def build_legacy_manifest(previous, *, revision, release):
    # 1.0 clients read the release block; their download overrides stay as last published
    packages = previous.packages if previous else {}
    return UpdateManifestV1(schema_version=1, revision=revision, hatcher=release, packages=packages)


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
    if signed_size > MAX_MANIFEST_BYTES:
        raise ValueError("generated manifest exceeds client size limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--packages-dir", type=Path, default=DATA_DIR / "packages")
    parser.add_argument("--reference-dir", type=Path)
    parser.add_argument("--previous-v2", type=Path)
    parser.add_argument("--previous-v1", type=Path)
    parser.add_argument("--legacy-output", type=Path, required=True)
    parser.add_argument("--release-json", type=Path, required=True)
    parser.add_argument("--asset-dir", type=Path, required=True)
    parser.add_argument("--revision", type=int)
    args = parser.parse_args()

    previous = _previous(args.previous_v2, 2)
    legacy_previous = _previous(args.previous_v1, 1)
    floor = max(
        previous.revision + 1 if previous else 1,
        legacy_previous.revision + 1 if legacy_previous else 1,
    )
    revision = args.revision if args.revision is not None else max(int(time.time()), floor)
    if revision < floor:
        raise ValueError("publication revision must increase")
    release = _release_payload(_release_from_json(args.release_json), args.asset_dir)
    source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    manifest = build_catalog_manifest(
        previous,
        revision=revision,
        source_commit=source_commit,
        release=release,
        packages_dir=args.packages_dir,
        reference_dir=args.reference_dir,
    )
    legacy = build_legacy_manifest(legacy_previous, revision=revision, release=release)
    _write_payload(args.output, manifest)
    _write_payload(args.legacy_output, legacy)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
