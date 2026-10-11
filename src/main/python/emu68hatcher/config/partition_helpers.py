"""partition layout helpers - validation, alignment, auto-naming"""

import re

from emu68hatcher.config.constants import (
    CYLINDER_SIZE,
    DEFAULT_BOOT_DEVICE,
    MBR_OVERHEAD,
    MBR_SECTOR_SIZE,
    PFS3_MAX_PARTITION_SIZE,
    RDB_OVERHEAD,
)
from emu68hatcher.config.partition_models import (
    AmigaPartition,
    Filesystem,
    MBRPartition,
    PartitionConfig,
)

PFS3_MAX_CREATE: int = PFS3_MAX_PARTITION_SIZE
_MIN_CONTENT_HEADROOM = 1024 * 1024


def round_to_cylinder(size: int) -> int:
    """round down to cylinder boundary (516,096 bytes)"""
    return (size // CYLINDER_SIZE) * CYLINDER_SIZE


def round_to_mbr_sector(size: int) -> int:
    """round down to MBR sector boundary (512 bytes)"""
    return (size // MBR_SECTOR_SIZE) * MBR_SECTOR_SIZE


def usable_partition_content_size(size: int) -> int:
    headroom = max(_MIN_CONTENT_HEADROOM, size // 50)
    return max(0, size - headroom)


def disk_size_for_gb(gb: int) -> int:
    """convert GB to usable bytes (95% of decimal GB for SD card safety)"""
    return int(gb * 1_000_000_000 * 0.95)


def calculate_boot_default(disk_size: int) -> int:
    """default EMU68BOOT size matching upstream imager: min(disk/15, 1GB), MBR-aligned"""
    return round_to_mbr_sector(min(disk_size // 15, 1024 * 1024 * 1024))


def calculate_id76_size(disk_size: int, boot_size: int) -> int:
    """calculate ID76 (Amiga RDB container) size from remaining space"""
    return round_to_mbr_sector(disk_size - MBR_OVERHEAD - boot_size)


def calculate_usable_amiga_space(id76_size: int) -> int:
    """usable space for Amiga partitions within ID76 container"""
    return id76_size - RDB_OVERHEAD


def calculate_free_space(id76_size: int, amiga_partitions: list[AmigaPartition]) -> int:
    """free space remaining for new Amiga partitions"""
    usable = calculate_usable_amiga_space(id76_size)
    allocated = sum(p.size for p in amiga_partitions)
    return usable - allocated


def next_device_name(existing: list[str], prefix: str = "SDH") -> str:
    """find the lowest unused device name (e.g., SDH0, SDH1, ...)"""
    used = set()
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    for name in existing:
        m = pattern.match(name)
        if m:
            used.add(int(m.group(1)))
    i = 0
    while i in used:
        i += 1
    return f"{prefix}{i}"


def next_volume_name(existing: list[str]) -> str:
    """find the next unused volume name: Work, Work_1, Work_2, ..."""
    used = {n.lower() for n in existing}
    if "work" not in used:
        return "Work"
    i = 1
    while f"work_{i}" in used:
        i += 1
    return f"Work_{i}"


def validate_partition_layout(
    disk_size: int,
    boot_size: int,
    amiga_partitions: list[AmigaPartition],
) -> list[str]:
    """validate a partition layout. returns list of error strings (empty = valid)"""
    from pydantic import ValidationError

    try:
        build_partition_config(disk_size, boot_size, amiga_partitions)
    except ValidationError as e:
        return [str(error["msg"]) for error in e.errors()]
    return []


def build_partition_config(
    disk_size_bytes: int,
    boot_size: int,
    amiga_partitions: list[AmigaPartition],
) -> PartitionConfig:
    """assemble a PartitionConfig from editor state"""
    id76_size = calculate_id76_size(disk_size_bytes, boot_size)

    validated_parts = [AmigaPartition.model_validate(p.model_dump()) for p in amiga_partitions]
    return PartitionConfig(
        disk_size=disk_size_bytes,
        layout=[
            MBRPartition(type="fat32", name="EMU68BOOT", size=boot_size),
            MBRPartition(
                type="id76",
                name="AMIGA",
                size=id76_size,
                amiga_partitions=validated_parts,
            ),
        ],
    )


def create_default_partition_layout(
    disk_size_gb: int = 8, disk_size_bytes: int | None = None
) -> PartitionConfig:
    """Workbench with unallocated space for additional partitions."""
    disk_size = disk_size_bytes if disk_size_bytes is not None else disk_size_for_gb(disk_size_gb)
    boot_size = calculate_boot_default(disk_size)
    return build_partition_config(
        disk_size,
        boot_size,
        [
            AmigaPartition(
                device=DEFAULT_BOOT_DEVICE,
                volume="Workbench",
                filesystem=Filesystem.PFS3,
                size=round_to_cylinder(disk_size // 15),
                bootable=True,
                priority=0,
            )
        ],
    )
