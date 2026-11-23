"""macOS removable-disk operations."""

import os
import plistlib
import re
import subprocess

from emu68hatcher.builder.host.disk_info import DiskInfo, DiskOperationResult


def list_disks() -> list[DiskInfo]:
    # not `diskutil list`: it stalls 5 s and hides a card diskarbitrationd can't finish probing
    mounts: dict[str, list[str]] = {}
    table = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=10)
    for line in table.stdout.splitlines():
        match = re.match(r"/dev/(disk\d+)s\d+ on (.+) \(", line)
        if match:
            mounts.setdefault(match[1], []).append(match[2])
    disks = []
    for identifier in sorted(
        (name for name in os.listdir("/dev") if re.fullmatch(r"disk\d+", name)),
        key=lambda name: int(name[4:]),
    ):
        info = _disk_info(identifier)
        if info is None or info.get("VirtualOrPhysical") != "Physical":
            continue
        if not (info.get("RemovableMedia") or info.get("Ejectable")):
            continue
        mounted = mounts.get(identifier, [])
        size = int(info.get("TotalSize") or info.get("Size") or 0)
        if not size:
            continue
        name = (info.get("MediaName") or info.get("IORegistryEntryName") or identifier).strip()
        disks.append(
            DiskInfo(
                device=f"/dev/{identifier}",
                name=name or identifier,
                size_bytes=size,
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
    # a card without partitions has nothing mounted, and unmountDisk hangs on it
    identifier = info.device.removeprefix("/dev/")
    if not any(name.startswith(f"{identifier}s") for name in os.listdir("/dev")):
        return DiskOperationResult(True)
    try:
        result = subprocess.run(
            ["diskutil", "unmountDisk", info.device],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return DiskOperationResult(
            False,
            f"diskutil unmountDisk {info.device} did not answer in 30 s; "
            "eject the card, reinsert it and try again",
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
