"""Windows removable-disk operations."""

import json
import logging
import os
import re
import subprocess

from emu68hatcher.builder.host.disk_info import DiskInfo, DiskOperationResult

_PS_GETDISK = r"""
$disks = Get-Disk | Where-Object { $_.BusType -in @('USB','SD','MMC') -or $_.IsBoot -eq $false } | Sort-Object Number
$out = @()
foreach ($d in $disks) {
    $partitions = Get-Partition -DiskNumber $d.Number -ErrorAction SilentlyContinue | ForEach-Object { @{ DriveLetter = if ($_.DriveLetter) { "$($_.DriveLetter):\" } else { $null }; Type = $_.Type } }
    $out += @{ Number = $d.Number; FriendlyName = $d.FriendlyName; Size = [int64]$d.Size; BusType = "$($d.BusType)"; IsBoot = [bool]$d.IsBoot; IsSystem = [bool]$d.IsSystem; IsReadOnly = [bool]$d.IsReadOnly; Partitions = $partitions }
}
$out | ConvertTo-Json -Depth 4 -Compress
"""


def list_disks() -> list[DiskInfo]:
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            _PS_GETDISK,
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    data = json.loads(result.stdout)
    if isinstance(data, dict):
        data = [data]
    system_drive = os.environ.get("SystemDrive", "C:").rstrip("\\")
    disks = []
    for item in data:
        if (item.get("BusType") or "").upper() not in ("USB", "SD", "MMC"):
            continue
        if item.get("IsBoot") or item.get("IsSystem"):
            continue
        size = int(item.get("Size") or 0)
        if not size:
            continue
        partitions = item.get("Partitions") or []
        if not isinstance(partitions, list):
            partitions = [partitions]
        mounted = [
            part.get("DriveLetter") for part in partitions if part and part.get("DriveLetter")
        ]
        disks.append(
            DiskInfo(
                device=f"\\\\.\\PhysicalDrive{item['Number']}",
                name=(item.get("FriendlyName") or f"Disk {item['Number']}").strip(),
                size_bytes=size,
                is_removable=True,
                is_system_disk=any(
                    mount.rstrip("\\").upper().rstrip(":") == system_drive.rstrip(":").upper()
                    for mount in mounted
                ),
                mounted_partitions=mounted,
                is_read_only=bool(item.get("IsReadOnly")),
            )
        )
    return disks


def set_offline(
    info: DiskInfo,
    logger: logging.Logger,
    elevation: object | None,
    *,
    offline: bool,
) -> DiskOperationResult:
    match = re.search(r"PhysicalDrive(\d+)", info.device, re.IGNORECASE)
    if not match:
        return DiskOperationResult(False, f"invalid Windows disk path: {info.device}")
    number = match.group(1)
    flag = "$true" if offline else "$false"
    script = (
        f"Set-Disk -Number {number} -IsOffline {flag}; Set-Disk -Number {number} -IsReadOnly $false"
    )
    command = ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
    logger.info(f"set-disk {number} IsOffline={offline}")
    try:
        if elevation is not None:
            from emu68hatcher.builder.host.elevation import run_elevated

            result = run_elevated(command, elevation, timeout=30)
        else:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return DiskOperationResult(False, f"set-disk failed: {e}")
    if result.returncode == 0:
        return DiskOperationResult(True)
    detail = (result.stderr or result.stdout or "").strip()
    return DiskOperationResult(
        False,
        f"set-disk {number} IsOffline={offline} failed (rc={result.returncode}): {detail}",
    )
