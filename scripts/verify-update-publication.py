#!/usr/bin/env python3
"""Compare the signed publication files with this checkout's catalog."""

import argparse
from pathlib import Path

from emu68hatcher import __version__
from emu68hatcher.data.catalog import load_catalog_source
from emu68hatcher.data.catalog_manifest import validate_client_catalog
from emu68hatcher.data.update_manifest import verify_manifest_bytes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    legacy = verify_manifest_bytes((args.directory / "manifest.json").read_bytes())
    manifest = verify_manifest_bytes((args.directory / "manifest-v2.json").read_bytes())
    if legacy.schema_version != 1 or manifest.schema_version != 2:
        raise ValueError("publication endpoints have incorrect schemas")
    if legacy.hatcher != manifest.hatcher or legacy.revision != manifest.revision:
        raise ValueError("publication release metadata differs")
    if len(manifest.catalogs) != 1:
        raise ValueError("publication carries more than one catalog")
    catalog = manifest.catalogs[0]
    if not catalog.is_for(__version__):
        raise ValueError(
            f"published catalog is for Hatcher {catalog.min_hatcher_version}, "
            f"this checkout is {__version__}"
        )
    if catalog.source_commit != args.source_commit:
        raise ValueError("published catalog comes from a different commit")
    if catalog.data() != load_catalog_source():
        raise ValueError("published catalog differs from source YAML")
    validate_client_catalog(catalog.snapshot())
    print(f"verified {catalog.id}, revision {catalog.revision}, source {catalog.source_commit}")


if __name__ == "__main__":
    main()
