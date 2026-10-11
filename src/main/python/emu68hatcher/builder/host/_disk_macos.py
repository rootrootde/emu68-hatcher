"""macOS removable-disk operations."""

import plistlib
import subprocess

from emu68hatcher.builder.host.disk_info import DiskInfo, DiskOperationResult


def list_disks() -> list[DiskInfo]:
    listing = subprocess.run(
        ["diskutil", "list", "-plist", "physical"],
        capture_output=True,
        timeout=10,
    )
    if listing.returncode != 0:
        return []
    disks = []
    for whole in plistlib.loads(listing.stdout).get("AllDisksAndPartitions", []):
        identifier = whole.get("DeviceIdentifier")
        if not identifier:
            continue
        info = _disk_info(identifier)
        if info is None or not (info.get("RemovableMedia") or info.get("Ejectable")):
            continue
        mounted = [
            part["MountPoint"]
            for part in whole.get("Partitions", []) or []
            if part.get("MountPoint")
        ]
        size = int(info.get("TotalSize") or whole.get("Size") or 0)
        if not size:
            continue
        name = (info.get("MediaName") or info.get("IORegistryEntryName") or identifier).strip()
        disks.append(
            DiskInfo(
                device=f"/dev/{identifier}",
                name=name or identifier,
                size_bytes=size,
                is_removable=True,
                is_system_disk=bool(info.get("SystemImage"))
                or any(mount in ("/", "/System/Volumes/Data") for mount in mounted),
                mounted_partitions=mounted,
                is_read_only=info.get("WritableMedia") is False,
            )
        )
    return disks


def _disk_info(identifier: str) -> dict | None:
    result = subprocess.run(
        ["diskutil", "info", "-plist", identifier],
        capture_output=True,
        timeout=10,
    )
    return plistlib.loads(result.stdout) if result.returncode == 0 else None


def unmount(info: DiskInfo) -> DiskOperationResult:
    result = subprocess.run(
        ["diskutil", "unmountDisk", info.device],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode == 0:
        return DiskOperationResult(True)
    detail = result.stderr.strip() or result.stdout.strip()
    return DiskOperationResult(
        False,
        f"diskutil unmountDisk {info.device} failed (rc={result.returncode}): {detail}",
    )


def eject(device: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["diskutil", "eject", device],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode == 0:
        return True, f"Ejected {device}"
    return False, result.stderr.strip() or result.stdout.strip() or "eject failed"
