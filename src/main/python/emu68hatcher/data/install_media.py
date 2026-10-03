"""hash-based install media identification (ADFs, ISOs) via install_media_hashes.yaml"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# dirs pruned from tree-scans so they don't eat the max_files budget
SCAN_SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".tox", ".mypy_cache"}


def _raise_walk_error(error: OSError) -> None:
    raise error


def walk_files_capped(
    directory: Path,
    max_files: int,
    cancel_check: Callable[[], bool] | None = None,
):
    """yield (path, ext) pairs under 'directory', pruning common skip-dirs, capped at 'max_files'"""
    seen = 0
    for dirpath, dirnames, filenames in os.walk(directory, onerror=_raise_walk_error):
        if cancel_check and cancel_check():
            return
        dirnames[:] = [d for d in dirnames if d not in SCAN_SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            if cancel_check and cancel_check():
                return
            seen += 1
            if seen > max_files:
                return
            yield Path(dirpath) / fname, os.path.splitext(fname)[1].lower()


@dataclass
class IdentifiedInstallMedia:
    """install media file identified by hash"""

    path: Path
    friendly_name: str
    adf_name: str  # internal name from YAML
    workbench_version: str  # e.g., "3.1", "3.2"


def scan_install_media_by_hash(
    directories: Path | list[Path] | tuple[Path, ...],
    max_files: int = 5000,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[list["IdentifiedInstallMedia"], bool]:
    """scan one or more dirs for install media, MD5-identify via install_media_hashes.yaml"""
    from emu68hatcher.data.data_manager import lookup_install_media
    from emu68hatcher.utils.hashing import calculate_hash

    dirs = [directories] if isinstance(directories, Path) else list(directories)

    media_extensions = {".adf", ".iso", ".lha"}
    candidates: list[Path] = []
    seen_paths: set[Path] = set()
    truncated = False

    for directory in dirs:
        if cancel_check and cancel_check():
            break
        if not directory.exists() or not directory.is_dir():
            continue

        seen_count = 0
        for path, ext in walk_files_capped(directory, max_files, cancel_check):
            seen_count += 1
            if ext not in media_extensions:
                continue
            if path in seen_paths:
                continue
            try:
                # ADF must be standard floppy size (880KB)
                if ext == ".adf" and path.stat().st_size != 901120:
                    continue
            except OSError:
                continue
            seen_paths.add(path)
            candidates.append(path)
        if seen_count >= max_files:
            truncated = True

    identified = []
    for path in candidates:
        if cancel_check and cancel_check():
            break
        try:
            md5 = calculate_hash(path)
            info = lookup_install_media(md5)

            if info:
                identified.append(
                    IdentifiedInstallMedia(
                        path=path,
                        friendly_name=info.friendly_name,
                        adf_name=info.adf_name,
                        workbench_version=info.workbench_version,
                    )
                )
        except Exception:
            continue

    return identified, truncated


def get_required_install_media(workbench_version: str) -> list[str]:
    """required ADF_Names for a Workbench version (locale disks excluded)"""
    requirements = {
        "3.1": [
            "Workbench3_1",
            "Extras3_1",
            "Storage3_1",
            "Fonts3_1",
            "Install3_1",
        ],
        "3.2": [
            "Workbench3_2",
            "Extras3_2",
            "Storage3_2",
            "Fonts3_2",
            "Install3_2",
            "Classes3_2",
            "DiskDoctor3_2",
            "Backdrops3_2",
        ],
        "3.2.2.1": [
            # base 3.2 disks
            "Workbench3_2",
            "Extras3_2",
            "Storage3_2",
            "Fonts3_2",
            "Install3_2",
            "Classes3_2",
            "Backdrops3_2",
            # 3.2.2.x update disks
            "Update3_2_2",
            "Update3_2_2_1",
            "Classes3_2_2",
            "DiskDoctor3_2_2",
        ],
        "3.2.3": [
            # base 3.2 disks
            "Workbench3_2",
            "Extras3_2",
            "Storage3_2",
            "Fonts3_2",
            "Install3_2",
            "Classes3_2",
            "Backdrops3_2",
            # 3.2.3 update disks
            "Update3_2_3",
            "Extras3_2_3",
            "Classes3_2_3",
            "DiskDoctor3_2_3",
        ],
        "3.9": [
            # 3.9 installs from a single CD volume, not floppies
            "AmigaOS3_9",
        ],
    }
    if workbench_version not in requirements:
        # better to fail loud than silently substitute 3.1 disks
        raise KeyError(
            f"unknown workbench version {workbench_version!r}; known: {sorted(requirements)}"
        )
    return requirements[workbench_version]


def check_install_media_complete(
    found_media: list[IdentifiedInstallMedia],
    workbench_version: str,
) -> tuple[bool, list[str]]:
    """check if all required install media for a version is present"""
    required = get_required_install_media(workbench_version)

    # use all found adf_names regardless of version - 3.2.3 needs base 3.2 disks tagged "3.2"
    found_adf_names = {m.adf_name for m in found_media}

    missing = [r for r in required if r not in found_adf_names]

    return len(missing) == 0, missing
