"""Emu68 release table loaded from reference/emu68_releases.yaml"""

from functools import cache

from emu68hatcher.data.data_manager import load_yaml_data

_KERNEL_KEYS = ("modern", "classic", "pistorm16")
_ASSET_KINDS = {"zip": "zips", "raw": "extras"}


@cache
def load_emu68_releases() -> dict:
    """parsed emu68_releases.yaml; raises ValueError on a malformed table"""
    data = load_yaml_data("emu68_releases")
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("emu68_releases.yaml: unsupported schema_version")
    seen = set()
    for release in data.get("releases") or []:
        version = release.get("version")
        if not isinstance(version, str) or version in seen:
            raise ValueError(f"emu68_releases.yaml: bad or duplicate version {version!r}")
        seen.add(version)
        if release.get("tag") != f"v{version}":
            raise ValueError(f"emu68_releases.yaml: tag does not match {version}")
        if set(release.get("kernels") or {}) != set(_KERNEL_KEYS):
            raise ValueError(f"emu68_releases.yaml: {version} needs kernels {_KERNEL_KEYS}")
        for asset in release.get("assets") or []:
            if asset.get("kind") not in _ASSET_KINDS or not asset.get("role"):
                raise ValueError(f"emu68_releases.yaml: bad asset in {version}")
    if not seen:
        raise ValueError("emu68_releases.yaml: no releases")
    return data


def download_table() -> dict[str, dict]:
    """version -> {"tag", "zips": [(role, filename)], "extras"?: [...]}"""
    table = {}
    for release in load_emu68_releases()["releases"]:
        entry: dict = {"tag": release["tag"]}
        for asset in release["assets"]:
            key = _ASSET_KINDS[asset["kind"]]
            entry.setdefault(key, []).append((asset["role"], asset["filename"]))
        table[release["version"]] = entry
    return table


def kernel_table() -> dict[str, dict[str, str]]:
    """version -> {"modern", "classic", "pistorm16"} kernel file names"""
    return {
        release["version"]: {key: release["kernels"][key] for key in _KERNEL_KEYS}
        for release in load_emu68_releases()["releases"]
    }
