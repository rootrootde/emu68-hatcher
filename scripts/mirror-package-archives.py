#!/usr/bin/env python3
"""Download pinned package archives that the archive mirror does not have yet."""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "main" / "python"))

from emu68hatcher.builder.host.download_catalog import package_download_item  # noqa: E402
from emu68hatcher.builder.host.downloads import DownloadManager, archive_asset_name  # noqa: E402
from emu68hatcher.data.catalog import load_catalog_source  # noqa: E402
from emu68hatcher.data.package_schema import SourceType  # noqa: E402

# github turns other characters in asset names into dots, which would break the derived url
_SAFE_NAME = re.compile(r"[A-Za-z0-9._-]+")


def _read_names(path: Path) -> set[str]:
    lines = (line.split("#", 1)[0].strip() for line in path.read_text().splitlines())
    return {line for line in lines if line}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packages-dir", type=Path, action="append", required=True)
    parser.add_argument("--allowlist", type=Path, default=ROOT / "scripts" / "archive-mirror.txt")
    parser.add_argument("--existing", type=Path, required=True, help="asset names, one per line")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    logger = logging.getLogger("emu68hatcher")
    allowed = _read_names(args.allowlist)
    existing = _read_names(args.existing)
    args.out.mkdir(parents=True, exist_ok=True)

    items = {}
    known = set()
    for packages_dir in args.packages_dir:
        for pkg in load_catalog_source(packages_dir).packages.values():
            if not pkg.download or pkg.download.source == SourceType.LOCAL:
                continue
            known.add(pkg.name)
            if pkg.name not in allowed or not pkg.download.hash:
                continue
            item, reason = package_download_item(pkg, logger)
            if item is None:
                print(f"{pkg.name}: not queued: {reason}")
                continue
            items[archive_asset_name(item.expected_hash, item.filename)] = item

    unknown = allowed - known
    if unknown:
        print(f"allowlist names no remote package: {', '.join(sorted(unknown))}")
        return 2
    for name in sorted(known - allowed):
        print(f"{name}: not in the allowlist, not mirrored")

    with tempfile.TemporaryDirectory() as work:
        manager = DownloadManager(work_dir=Path(work), max_retries=3, timeout=120.0)
        for asset, item in sorted(items.items()):
            if asset in existing:
                continue
            if not _SAFE_NAME.fullmatch(asset):
                print(f"{item.name}: file name {item.filename!r} is not a valid asset name")
                continue
            result = manager.download(replace(item, extract=False))
            if not result.success:
                print(f"{item.name}: not mirrored: {result.error}")
                continue
            shutil.copyfile(result.path, args.out / asset)
            print(f"{item.name}: mirrored as {asset}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
