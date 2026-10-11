"""Disk enumeration data types."""

from dataclasses import dataclass, field


@dataclass
class DiskInfo:
    device: str
    name: str
    size_bytes: int
    is_removable: bool
    is_system_disk: bool
    mounted_partitions: list[str] = field(default_factory=list)
    is_read_only: bool = False

    @property
    def size_human(self) -> str:
        value = self.size_bytes
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024:
                return f"{value:.1f} {unit}" if unit != "B" else f"{value} B"
            value /= 1024
        return f"{value:.1f} PB"

    @property
    def display_label(self) -> str:
        label = f"{self.name} ({self.size_human}) - {self.device}"
        return f"{label} [write-protected]" if self.is_read_only else label


@dataclass(frozen=True)
class DiskOperationResult:
    success: bool
    error: str = ""


def normalise_device(value: str) -> str:
    return str(value).strip().rstrip("\\/").lower().replace("/dev/r", "/dev/")
