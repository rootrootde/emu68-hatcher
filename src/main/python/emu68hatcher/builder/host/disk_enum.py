"""Cross-platform removable-disk facade."""

import json
import logging
import plistlib
import subprocess

from emu68hatcher.builder.host.disk_info import (
    DiskInfo,
    DiskOperationResult,
    normalise_device,
)
from emu68hatcher.utils.platform import OperatingSystem, get_platform_info

logger = logging.getLogger(__name__)

__all__ = [
    "DiskInfo",
    "DiskOperationResult",
    "eject_disk",
    "find_disk",
    "list_removable_disks",
    "online_disk",
    "unmount_disk",
]


def list_removable_disks(*, raise_on_error: bool = False) -> list[DiskInfo]:
    try:
        platform = get_platform_info().os
        if platform == OperatingSystem.LINUX:
            from emu68hatcher.builder.host._disk_linux import list_disks
        elif platform == OperatingSystem.MACOS:
            from emu68hatcher.builder.host._disk_macos import list_disks
        elif platform == OperatingSystem.WINDOWS:
            from emu68hatcher.builder.host._disk_windows import list_disks
        else:
            return []
        return list_disks()
    except (
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
        plistlib.InvalidFileException,
    ):
        logger.exception("disk enumeration failed")
        if raise_on_error:
            raise
        return []


def find_disk(device: str, *, raise_on_error: bool = False) -> DiskInfo | None:
    normalized = normalise_device(device)
    disks = list_removable_disks(raise_on_error=raise_on_error)
    return next((disk for disk in disks if normalise_device(disk.device) == normalized), None)


def unmount_disk(
    info: DiskInfo,
    logger: logging.Logger | None = None,
    elevation: object | None = None,
) -> DiskOperationResult:
    log = logger or globals()["logger"]
    platform = get_platform_info().os
    log.info(f"unmounting {info.device}: {info.mounted_partitions}")
    if platform == OperatingSystem.MACOS:
        from emu68hatcher.builder.host._disk_macos import unmount

        return unmount(info)
    if platform == OperatingSystem.LINUX:
        from emu68hatcher.builder.host._disk_linux import unmount

        return unmount(info, log, elevation)
    if platform == OperatingSystem.WINDOWS:
        from emu68hatcher.builder.host._disk_windows import set_offline

        return set_offline(info, log, elevation, offline=True)
    return DiskOperationResult(False, f"unmount is unsupported on {platform.value}")


def online_disk(
    info: DiskInfo,
    logger: logging.Logger | None = None,
    elevation: object | None = None,
) -> DiskOperationResult:
    if get_platform_info().os != OperatingSystem.WINDOWS:
        return DiskOperationResult(True)
    from emu68hatcher.builder.host._disk_windows import set_offline

    return set_offline(info, logger or globals()["logger"], elevation, offline=False)


def eject_disk(device: str, logger: logging.Logger | None = None) -> tuple[bool, str]:
    log = logger or globals()["logger"]
    platform = get_platform_info().os
    log.info(f"ejecting {device}")
    if platform == OperatingSystem.MACOS:
        from emu68hatcher.builder.host._disk_macos import eject

        return eject(device)
    if platform == OperatingSystem.LINUX:
        from emu68hatcher.builder.host._disk_linux import eject

        return eject(device, log)
    return False, "eject not supported on this platform"
