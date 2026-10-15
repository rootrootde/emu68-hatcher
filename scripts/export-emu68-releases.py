#!/usr/bin/env python3
"""Export data/reference/emu68_releases.yaml as an unsigned emu68-releases-v1.json payload.

The output is the canonical payload (sorted keys, compact, UTF-8) plus a newline.
Signing is a separate publisher step: sign-update-manifest.py --format releases.
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "src/main/python/emu68hatcher/data/reference/emu68_releases.yaml"
sys.path.insert(0, str(ROOT / "src/main/python"))

from emu68hatcher.data.emu68_releases import release_index_payload  # noqa: E402
from emu68hatcher.data.manifest_signing import canonical_payload  # noqa: E402

_INT32_MAX = 2147483647


def _git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path)
    parser.add_argument("--revision", type=int, required=True)
    args = parser.parse_args()
    if not 1 <= args.revision <= _INT32_MAX:
        parser.error("revision must be 1..2147483647")

    # the commit label must describe the bytes actually exported
    if subprocess.run(["git", "-C", str(ROOT), "diff", "--quiet", "HEAD", "--", SOURCE]).returncode:
        parser.error(f"{SOURCE} has uncommitted changes")
    commit = _git("rev-parse", "HEAD")

    content = canonical_payload(release_index_payload(args.revision, commit)) + b"\n"
    # never replace an earlier export; a publisher may already have signed it
    try:
        with args.output.open("xb") as file:
            file.write(content)
    except FileExistsError:
        parser.error(f"{args.output} already exists")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
