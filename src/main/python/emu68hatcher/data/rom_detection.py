"""Kickstart ROM detection - checksum lookup first, header parse as fallback"""

import hashlib
import re
from collections.abc import Callable
from pathlib import Path

from emu68hatcher.data.install_media import walk_files_capped

# valid Kickstart ROM file sizes
KICKSTART_ROM_SIZES: tuple[int, ...] = (262144, 524288, 1048576)

# Cloanto encrypted dumps prepend an 11-byte AMIROMTYPE1 header
_ENCRYPTED_HEADER_SIZE = 11
_IDENTIFIABLE_SIZES = frozenset(KICKSTART_ROM_SIZES) | {
    size + _ENCRYPTED_HEADER_SIZE for size in KICKSTART_ROM_SIZES
}


def _version_sort_key(v: str) -> tuple:
    """parse '3.2.3' / '1.3' / 'AROS' into a sortable tuple (unparseable suffixes sort first)"""
    parts: list[int] = []
    for chunk in v.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            return tuple(parts)  # stop at the first non-int chunk
    return tuple(parts)


KICKSTART_CHECKSUMS: dict[str, dict] = {
    "93b2a0f1af4a6ac373d6da2cd4f76c3b": {
        "version": "3.2.2.1",
        "friendly_name": "Kickstart 3.2.2 A1200 (47.111)",
        "revision": "47.111",
        "model": "A1200",
        "fat32_name": "A1200.47.111.rom",
        "whdload_name": "kick47111.A1200",
        "sequence": 1,
    },
    "cad62a102848e13bf04d8a3b0f8be6ab": {
        "version": "3.2",
        "friendly_name": "Kickstart 3.2 A1200 (47.96)",
        "revision": "47.96",
        "model": "A1200",
        "fat32_name": "A1200.47.96.rom",
        "whdload_name": "kick4796.A1200",
        "sequence": 1,
    },
    "646773759326fbac3b2311fd8c8793ee": {
        "version": "3.1",
        "friendly_name": "Kickstart 3.1 A1200 (40.068)",
        "revision": "40.068",
        "model": "A1200",
        "fat32_name": "A1200.40.068.rom",
        "whdload_name": "kick40068.A1200",
        "sequence": 1,
    },
    "43efffafb382528355bb4cdde9fa9ce7": {
        "version": "3.1",
        "friendly_name": "Kickstart 3.1 A1200 Encrypted ROM",
        "model": "A1200",
        "sequence": 4,
        "excluded": True,
        "exclude_message": "Emu68 Imager does not support encrypted AmigaForever ROMs. You will need to either find an unencrypted version or decrypt the ROM. The file found was:",
    },
    "1c953f4337b450b24b108df19fccb788": {
        "version": "3.1",
        "friendly_name": "Kickstart 3.x A1200 Cloanto (45.064)",
        "revision": "45.064",
        "model": "A1200",
        "fat32_name": "A1200.45.064.rom",
        "whdload_name": "kick45064.A1200",
        "sequence": 3,
    },
    "14cda40cc6e39b468195ebc03f335ce1": {
        "version": "3.1",
        "friendly_name": "Kickstart 3.x A1200 Cloanto (45.066)",
        "revision": "45.066",
        "model": "A1200",
        "fat32_name": "A1200.45.066.rom",
        "whdload_name": "kick45066.A1200",
        "sequence": 2,
    },
    "63b9a74484faf5dc74469dbbfa0ade5b": {
        "version": "3.2.3",
        "friendly_name": "Kickstart 3.2.3 A1200 (47.115)",
        "revision": "47.115",
        "model": "A1200",
        "fat32_name": "A1200.47.115.rom",
        "whdload_name": "kick47115.A1200",
        "sequence": 1,
    },
}


_EXTRA_KICKSTART_CHECKSUMS: dict[str, dict] = {
    "85ad74194e87c08904327de1a9443b7a": {
        "version": "1.2",
        "friendly_name": "Kickstart 1.2 A500 (33.180)",
        "revision": "33.180",
        "model": "A500",
        "fat32_name": "A500.33.180.rom",
        "whdload_name": "kick33180.A500",
        "sequence": 1,
    },
    "82a21c1890cae844b3df741f2762d48d": {
        "version": "1.3",
        "friendly_name": "Kickstart 1.3 A500 (34.005)",
        "revision": "34.005",
        "model": "A500",
        "fat32_name": "A500.34.005.rom",
        "whdload_name": "kick34005.A500",
        "sequence": 1,
    },
    "1fa1f93d3d7b51271dd1356b8b2b45a9": {
        "version": "1.0",
        "friendly_name": "Kickstart 1.0 A1000 (31.034)",
        "revision": "31.034",
        "model": "A1000",
        "fat32_name": "A1000.31.034.rom",
        "whdload_name": "kick31034.A1000",
        "sequence": 1,
    },
    "e40a5dfb3d017ba8779faba30cbd1c8e": {
        "version": "3.1",
        "friendly_name": "Kickstart 3.1 A600 (40.063)",
        "revision": "40.063",
        "model": "A600",
        "fat32_name": "A600.40.063.rom",
        "whdload_name": "kick40063.A600",
        "sequence": 1,
    },
    "9bdedde6a4f33555b4a270c8ca53297d": {
        "version": "3.1",
        "friendly_name": "Kickstart 3.1 A4000 (40.068)",
        "revision": "40.068",
        "model": "A4000",
        "fat32_name": "A4000.40.068.rom",
        "whdload_name": "kick40068.A4000",
        "sequence": 1,
    },
}
KICKSTART_CHECKSUMS.update(_EXTRA_KICKSTART_CHECKSUMS)

_WHDLOAD_NAMES = frozenset(
    {
        "kick33180.A500",
        "kick34005.A500",
        "kick40063.A600",
        "kick40068.A1200",
        "kick40068.A4000",
        "kick31034.A1000",
    }
)
for _info in KICKSTART_CHECKSUMS.values():
    if _info.get("whdload_name") and _info["whdload_name"] not in _WHDLOAD_NAMES:
        del _info["whdload_name"]


def _detect_kickstart_from_header(data: bytes) -> dict | None:
    """fallback ID by scanning version strings in the ROM bytes - when checksum isn't in DB"""
    file_size = len(data)

    # valid: 256KB, 512KB, or 1MB (extended)
    if file_size not in KICKSTART_ROM_SIZES:
        return None

    version_patterns = [
        rb"Kickstart\s*(\d+)\.(\d+)",
        rb"exec\s+(\d+)\.(\d+)",
        rb"V(\d+)\.(\d+)",
    ]

    major, minor = None, None
    for pattern in version_patterns:
        match = re.search(pattern, data)
        if match:
            major = int(match.group(1))
            minor = int(match.group(2))
            break

    version_map = {
        30: "1.0",
        31: "1.1",
        32: "1.1",
        33: "1.2",
        34: "1.3",
        35: "1.3",
        36: "2.0",
        37: "2.04",
        38: "2.1",
        39: "3.0",
        40: "3.1",
        41: "3.1",
        42: "3.1",
        43: "3.1",
        44: "3.1",
        45: "3.1",
        46: "3.1",  # 3.1.4
        47: "3.2",
    }

    if major and major in version_map:
        version = version_map[major]
        revision = f"{major}.{minor}" if minor else str(major)

        # only match prefixed strings (b"A1200") - bare digits collide with addresses/years
        model = "Unknown"
        if b"A1200" in data:
            model = "A1200"
        elif b"A4000" in data:
            model = "A4000"
        elif b"A3000" in data:
            model = "A3000"
        elif b"A600" in data:
            model = "A600"
        elif b"A500" in data:
            model = "A500"
        elif b"CD32" in data:
            model = "CD32"
        elif b"AROS" in data:
            model = "AROS"

        return {
            "version": version,
            "revision": revision,
            "model": model,
            "size": file_size,
        }

    if b"AROS" in data:
        return {
            "version": "3.1",  # AROS is generally 3.1 compatible
            "revision": "AROS",
            "model": "AROS",
            "size": file_size,
        }

    return None


def identify_kickstart(path: Path) -> dict | None:
    """ID Kickstart ROM - checksum lookup first, header parse as fallback"""
    if not path.exists():
        return None
    try:
        if path.stat().st_size not in _IDENTIFIABLE_SIZES:
            return None
    except OSError:
        return None

    data = path.read_bytes()
    md5 = hashlib.md5(data).hexdigest()

    info = KICKSTART_CHECKSUMS.get(md5)
    if info:
        return {**info, "size": len(data)}

    return _detect_kickstart_from_header(data)


def scan_for_kickstart_roms(
    directories: Path | list[Path] | tuple[Path, ...],
    max_files: int = 5000,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[list[dict], bool]:
    """scan one or more dirs for Kickstart ROMs (cap at max_files per dir); returns (results, truncated)"""
    dirs = [directories] if isinstance(directories, Path) else list(directories)

    results = []
    rom_extensions = {".rom", ".bin", ".kick", ".a500", ".a600", ".a1200", ".a4000"}
    truncated = False
    seen_paths: set[Path] = set()

    for directory in dirs:
        if cancel_check and cancel_check():
            break
        if not directory.exists() or not directory.is_dir():
            continue

        seen_count = 0
        for path, ext in walk_files_capped(directory, max_files, cancel_check):
            seen_count += 1
            if path in seen_paths:
                continue
            seen_paths.add(path)
            try:
                size = path.stat().st_size
            except OSError:
                continue

            if ext in rom_extensions or size in KICKSTART_ROM_SIZES:
                if cancel_check and cancel_check():
                    break
                info = identify_kickstart(path)
                if info:
                    entry = {
                        "path": path,
                        "version": info["version"],
                        # revision / model are best-effort; upstream hash table only fills them for known A1200 dumps
                        "revision": info.get("revision", ""),
                        "model": info.get("model", "Unknown"),
                        "size": info["size"],
                    }
                    if info.get("whdload_name"):
                        entry["whdload_name"] = info["whdload_name"]
                    if info.get("excluded"):
                        entry["excluded"] = True
                        entry["exclude_message"] = info.get("exclude_message", "")
                    results.append(entry)
        if seen_count >= max_files:
            truncated = True

    # newer ROMs first - parse the version string so "3.10" sorts above "3.2"
    results.sort(key=lambda x: _version_sort_key(x["version"]), reverse=True)
    return results, truncated


# whdload ROM names for devs:kickstarts/; derived from the hash table so syncs are picked up
WHDLOAD_ROM_NAMES: tuple[str, ...] = tuple(
    sorted(
        {
            info["whdload_name"]
            for info in KICKSTART_CHECKSUMS.values()
            if info.get("whdload_name") and not info.get("excluded")
        }
    )
)


def find_whdload_kickstarts(directory: Path) -> dict[str, Path]:
    """find ROMs matching known WHDLoad names -> {whdload_name: rom_path}, first dump wins on dupes"""
    roms, _ = scan_for_kickstart_roms(directory)
    matched: dict[str, Path] = {}
    for rom in roms:
        name = rom.get("whdload_name")
        if not name or rom.get("excluded"):
            continue
        matched.setdefault(name, rom["path"])
    return matched


# 3.9 has no dedicated ROM - it runs on the 3.1 40.068 ROM plus a soft ROM update
ROM_VERSION_ALIAS = {"3.9": "3.1"}


def pick_boot_rom(found_roms: list[dict], version: str) -> Path | None:
    """boot ROM for a version from scan results: usable match, A1200 first"""
    version = ROM_VERSION_ALIAS.get(version, version)
    matching = [r for r in found_roms if r["version"] == version and not r.get("excluded")]
    if not matching:
        return None
    return next((r["path"] for r in matching if "1200" in r["model"]), matching[0]["path"])
