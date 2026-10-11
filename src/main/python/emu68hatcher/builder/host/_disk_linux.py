"""Linux removable-disk operations."""

import json
import logging
import shutil
import subprocess

from emu68hatcher.builder.host.disk_info import DiskInfo, DiskOperationResult


def _mountpoints(device: dict):
    for mount in device.get("mountpoints") or [device.get("mountpoint")]:
        if mount:
            yield mount
    for child in device.get("children", []) or []:
        yield from _mountpoints(child)


def list_disks() -> list[DiskInfo]:
    result = subprocess.run(
        ["lsblk", "-J", "-b", "-o", "NAME,SIZE,TYPE,MOUNTPOINT,MOUNTPOINTS,RM,RO,MODEL,VENDOR"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return []
    disks = []
    for device in json.loads(result.stdout).get("blockdevices", []):
        if device.get("type") != "disk" or not device.get("rm"):
            continue
        size = int(device.get("size") or 0)
        if not size:
            continue
        mounted = list(dict.fromkeys(_mountpoints(device)))
        name = (
            " ".join(
                filter(None, [device.get("vendor", "").strip(), device.get("model", "").strip()])
            )
            or device["name"]
        )
        disks.append(
            DiskInfo(
                device=f"/dev/{device['name']}",
                name=name,
                size_bytes=size,
                is_removable=True,
                is_system_disk=any(mount in ("/", "/boot", "/boot/efi") for mount in mounted),
                mounted_partitions=mounted,
                is_read_only=bool(device.get("ro")),
            )
        )
    return disks


def unmount(
    info: DiskInfo,
    logger: logging.Logger,
    elevation: object | None,
) -> DiskOperationResult:
    failures = []
    for mountpoint in info.mounted_partitions:
        success = False
        if shutil.which("udisksctl"):
            find = subprocess.run(
                ["findmnt", "-no", "SOURCE", mountpoint],
                capture_output=True,
                text=True,
                timeout=5,
            )
            part_device = find.stdout.strip()
            if part_device:
                result = subprocess.run(
                    ["udisksctl", "unmount", "-b", part_device],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                success = result.returncode == 0
        if success:
            continue
        command = ["umount", mountpoint]
        if elevation is not None:
            from emu68hatcher.builder.host.elevation import wrap_for_elevation

            command = wrap_for_elevation(command, elevation)
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            failures.append(
                f"umount {mountpoint} failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
    return DiskOperationResult(not failures, "; ".join(failures))


def eject(device: str, logger: logging.Logger) -> tuple[bool, str]:
    if shutil.which("udisksctl"):
        result = subprocess.run(
            ["udisksctl", "power-off", "-b", device],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            return True, f"Powered off {device}"
        logger.info(f"udisksctl power-off {device} failed: {result.stderr.strip()}")
    if shutil.which("eject"):
        result = subprocess.run(["eject", device], capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            return True, f"Ejected {device}"
        return False, result.stderr.strip() or f"eject failed (rc={result.returncode})"
    return False, "no udisksctl or eject command available"
