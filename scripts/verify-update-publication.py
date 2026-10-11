#!/usr/bin/env python3
"""Compare signed publication files with the chosen source catalog."""

import argparse
from pathlib import Path

from emu68hatcher.data.catalog import load_catalog_source, read_catalog_yaml
from emu68hatcher.data.catalog_manifest import validate_client_catalog
from emu68hatcher.data.update_manifest import verify_manifest_bytes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--target", type=Path, default=Path("updates/catalog-target.yaml"))
    args = parser.parse_args()
    legacy = verify_manifest_bytes((args.directory / "manifest.json").read_bytes())
    manifest = verify_manifest_bytes((args.directory / "manifest-v2.json").read_bytes())
    if legacy.schema_version != 1 or manifest.schema_version != 2:
        raise ValueError("publication endpoints have incorrect schemas")
    if legacy.hatcher != manifest.hatcher or legacy.revision != manifest.revision:
        raise ValueError("publication release metadata differs")
    target_config = read_catalog_yaml(args.target)
    target = target_config["target"]
    catalog = next(c for c in manifest.catalogs if c.id == target["id"])
    if catalog.source_commit != args.source_commit:
        raise ValueError("published catalog comes from a different commit")
    if catalog.data() != load_catalog_source():
        raise ValueError("published catalog differs from source YAML")
    for name in target_config.get("legacy_hash_updates", []):
        if legacy.packages[name].hash != catalog.packages[name]["download"]["hash"]:
            raise ValueError(f"legacy download hash differs from source YAML: {name}")
    validate_client_catalog(catalog.snapshot())
    print(f"verified {catalog.id}, revision {catalog.revision}, source {catalog.source_commit}")


if __name__ == "__main__":
    main()
