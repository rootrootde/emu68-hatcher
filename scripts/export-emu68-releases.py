#!/usr/bin/env python3
"""Export data/reference/emu68_releases.yaml as an unsigned emu68-releases-v1.json payload.

The output is the canonical payload (sorted keys, compact, UTF-8) plus a newline.
Signing is a separate publisher step: sign canonical_payload(payload) with the
updates key and wrap it as {"payload": ..., "signature": {...}} the same way
sign-update-manifest.py does for update manifests.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "src/main/python/emu68hatcher/data/reference/emu68_releases.yaml"
sys.path.insert(0, str(ROOT / "src/main/python"))

from emu68hatcher.data.emu68_releases import load_emu68_releases  # noqa: E402
from emu68hatcher.data.update_manifest import canonical_payload  # noqa: E402

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CHANNELS = {"stable", "alpha", "beta"}
_INT32_MAX = 2147483647


def _git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def _asset(release: dict, asset: dict, repository: str) -> dict:
    filename = asset["filename"]
    if not _SAFE_NAME.fullmatch(filename):
        raise ValueError(f"unsafe asset filename: {filename!r}")
    url = asset.get("url")
    expected = f"https://github.com/{repository}/releases/download/{release['tag']}/{filename}"
    if url is not None and url != expected:
        raise ValueError(f"{release['version']}: url for {filename} is not the release asset url")
    size = asset.get("size")
    if size is not None and (type(size) is not int or not 0 < size <= _INT32_MAX):
        raise ValueError(f"{release['version']}: bad size for {filename}")
    sha256 = asset.get("sha256")
    if sha256 is not None and not _SHA256.fullmatch(sha256):
        raise ValueError(f"{release['version']}: bad sha256 for {filename}")
    return {
        "role": asset["role"],
        "kind": asset["kind"],
        "filename": filename,
        "url": url,
        "size": size,
        "sha256": sha256,
    }


def build_payload(revision: int, commit: str) -> dict:
    table = load_emu68_releases()
    repository = table["repository"]
    releases = []
    for release in table["releases"]:
        if release.get("channel") not in _CHANNELS:
            raise ValueError(f"{release['version']}: unknown channel")
        releases.append(
            {
                "version": release["version"],
                "tag": release["tag"],
                "channel": release["channel"],
                "prerelease": release["channel"] != "stable",
                "assets": [_asset(release, a, repository) for a in release["assets"]],
                "kernels": dict(release["kernels"]),
            }
        )
    return {
        "format": "emu68-releases-v1",
        "schema_version": 1,
        "revision": revision,
        "source_commit": commit,
        "repository": repository,
        "releases": releases,
    }


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

    content = canonical_payload(build_payload(args.revision, commit)) + b"\n"
    # never replace an earlier export; a publisher may already have signed it
    try:
        with args.output.open("xb") as file:
            file.write(content)
    except FileExistsError:
        parser.error(f"{args.output} already exists")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
