"""Disk layout constants."""

from typing import Final

COMMON_DISK_SIZES: Final[list[int]] = [4, 8, 16, 32, 64, 128, 256, 512]
MIN_BOOT_PARTITION_SIZE: Final[int] = 128 * 1024 * 1024
MBR_OVERHEAD: Final[int] = 1048576 + 50688
RDB_OVERHEAD: Final[int] = 16 * 63 * 512 * 2
FFS_MAX_PARTITION_SIZE: Final[int] = 4 * 1024 * 1024 * 1024
PFS3_MAX_PARTITION_SIZE: Final[int] = 101 * 1024 * 1024 * 1024
CYLINDER_SIZE: Final[int] = 16 * 63 * 512
MBR_SECTOR_SIZE: Final[int] = 512
MIN_AMIGA_PARTITION_SIZE: Final[int] = 10 * 1024 * 1024
MAX_AMIGA_PARTITIONS: Final[int] = 10
DEFAULT_BOOT_DEVICE: Final[str] = "SDH0"
EMU68_BOOT_PARTITION_NAME: Final[str] = "EMU68BOOT"
