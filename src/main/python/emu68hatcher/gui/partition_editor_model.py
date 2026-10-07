"""Mutable partition editor state independent of Qt widgets."""

from pathlib import Path
from uuid import uuid4

from emu68hatcher.config.constants import (
    CYLINDER_SIZE,
    MAX_AMIGA_PARTITIONS,
    MIN_AMIGA_PARTITION_SIZE,
    PFS3_MAX_PARTITION_SIZE,
)
from emu68hatcher.config.partition_helpers import (
    build_partition_config,
    calculate_free_space,
    calculate_id76_size,
    calculate_usable_amiga_space,
    create_default_partition_layout,
    next_device_name,
    next_volume_name,
    round_to_cylinder,
    round_to_mbr_sector,
    validate_partition_layout,
)
from emu68hatcher.config.partition_models import (
    AmigaPartition,
    Filesystem,
    MBRPartition,
    PartitionConfig,
)


class PartitionEditorModel:
    def __init__(self, disk_size_gb: int = 64):
        self.disk_size = 0
        self.boot_size = 0
        self.partitions: list[AmigaPartition] = []
        self.partition_ids: list[str] = []
        self.selected_id: str | None = None
        self.reset(disk_size_gb=disk_size_gb)

    def load(self, config: PartitionConfig) -> None:
        identities = {}
        for part, identity in zip(self.partitions, self.partition_ids, strict=False):
            identities.setdefault(self._identity_key(part), []).append(identity)
        self.disk_size = config.disk_size
        self.boot_size = 0
        self.partitions = []
        for mbr in config.layout:
            if mbr.type == "fat32":
                self.boot_size = mbr.size
            elif mbr.type == "id76" and mbr.amiga_partitions:
                self.partitions = [part.model_copy(deep=True) for part in mbr.amiga_partitions]
        self.partition_ids = []
        for part in self.partitions:
            previous = identities.get(self._identity_key(part), [])
            self.partition_ids.append(previous.pop(0) if previous else uuid4().hex)
        if self.selected_id not in self.partition_ids:
            self.selected_id = None

    @staticmethod
    def _identity_key(part):
        return (
            ("ags", part.ags_reservation.role)
            if part.ags_reservation
            else ("manual", part.device.casefold())
        )

    def reset(
        self,
        *,
        disk_size_gb: int | None = None,
        disk_size_bytes: int | None = None,
        preserve_extra_directories: bool = False,
    ) -> None:
        reservations = [
            part.model_copy(deep=True) for part in self.partitions if part.ags_reservation
        ]
        retained_extras = [
            part.model_copy(deep=True)
            for part in self.partitions
            if preserve_extra_directories
            and part.extra_content_directory
            and not part.bootable
            and not part.ags_reservation
        ]
        boot_extra = (
            next((part.extra_content_directory for part in self.partitions if part.bootable), None)
            if preserve_extra_directories
            else None
        )
        extras = (
            {part.device: part.extra_content_directory for part in self.partitions}
            if preserve_extra_directories
            else {}
        )
        layout = create_default_partition_layout(
            disk_size_gb=disk_size_gb or 8,
            disk_size_bytes=disk_size_bytes,
        )
        self.load(layout)
        reservations.extend(retained_extras)
        if reservations:
            reserved_devices = {part.device.upper() for part in reservations}
            used_devices = reserved_devices | {
                part.device.upper()
                for part in self.partitions
                if part.device.upper() not in reserved_devices
            }
            for part in self.partitions:
                if part.device.upper() in reserved_devices:
                    part.device = next_device_name(list(used_devices))
                    used_devices.add(part.device.upper())
            self.partitions.extend(reservations)
            self.partition_ids.extend(uuid4().hex for _ in reservations)
        for part in self.partitions:
            if extras.get(part.device):
                part.extra_content_directory = extras[part.device]
            if part.bootable and boot_extra is not None:
                part.extra_content_directory = boot_extra

    def change_disk_size(self, disk_size: int) -> bool:
        self.disk_size = disk_size
        return self.free_space < 0

    def set_boot_size_mb(self, size_mb: int) -> None:
        self.boot_size = round_to_mbr_sector(size_mb * 1024 * 1024)

    @property
    def id76_size(self) -> int:
        return calculate_id76_size(self.disk_size, self.boot_size)

    @property
    def usable_space(self) -> int:
        return calculate_usable_amiga_space(self.id76_size)

    @property
    def allocated_space(self) -> int:
        return sum(part.size for part in self.partitions)

    @property
    def free_space(self) -> int:
        return calculate_free_space(self.id76_size, self.partitions)

    @property
    def can_add(self) -> bool:
        return (
            len(self.partitions) < MAX_AMIGA_PARTITIONS
            and self.free_space >= MIN_AMIGA_PARTITION_SIZE
        )

    def add_partition(self) -> bool:
        if not self.can_add:
            return False
        size = round_to_cylinder(self.free_space)
        if size < MIN_AMIGA_PARTITION_SIZE:
            return False
        self.partition_ids.append(uuid4().hex)
        self.partitions.append(
            AmigaPartition(
                device=next_device_name([part.device for part in self.partitions]),
                volume=next_volume_name([part.volume for part in self.partitions]),
                filesystem=Filesystem.PFS3,
                size=size,
            )
        )
        return True

    def remove_partition(self, row: int) -> bool:
        if not 0 <= row < len(self.partitions) or len(self.partitions) <= 1:
            return False
        if self.partitions[row].ags_reservation:
            return False
        self.partitions.pop(row)
        removed_id = self.partition_ids.pop(row)
        if self.selected_id == removed_id:
            self.selected_id = None
        return True

    def set_partition_size_mb(self, row: int, size_mb: int) -> None:
        part = self.partitions[row]
        if part.ags_reservation:
            return
        new_size = max(
            round_to_cylinder(size_mb * 1024 * 1024),
            self.minimum_size(row),
        )
        maximum = round_to_cylinder(self.free_space + part.size)
        if part.filesystem == Filesystem.PFS3:
            maximum = min(maximum, round_to_cylinder(PFS3_MAX_PARTITION_SIZE))
        if maximum >= self.minimum_size(row):
            part.size = min(new_size, maximum)

    def minimum_size(self, row: int) -> int:
        part = self.partitions[row]
        minimum = (
            part.ags_reservation.minimum_size if part.ags_reservation else MIN_AMIGA_PARTITION_SIZE
        )
        return ((minimum + CYLINDER_SIZE - 1) // CYLINDER_SIZE) * CYLINDER_SIZE

    def set_device(self, row: int, value: str) -> None:
        self.partitions[row].device = value.strip().upper()

    def set_volume(self, row: int, value: str) -> None:
        if self.partitions[row].ags_reservation:
            return
        self.partitions[row].volume = value.strip()

    def set_filesystem(self, row: int, value: str) -> None:
        if self.partitions[row].ags_reservation:
            return
        self.partitions[row].filesystem = Filesystem(value)

    def resize_pair(self, left: int, left_size: int, right: int, right_size: int) -> None:
        if not 0 <= left < len(self.partitions):
            return
        left_part = self.partitions[left]
        if left_part.ags_reservation:
            return
        if left_size < self.minimum_size(left) or left_size % CYLINDER_SIZE:
            return
        if left_part.filesystem == Filesystem.PFS3 and left_size > PFS3_MAX_PARTITION_SIZE:
            return
        if 0 <= right < len(self.partitions):
            right_part = self.partitions[right]
            if right_part.ags_reservation:
                return
            if right_size < self.minimum_size(right) or right_size % CYLINDER_SIZE:
                return
            if right_part.filesystem == Filesystem.PFS3 and right_size > PFS3_MAX_PARTITION_SIZE:
                return
            if left_size + right_size != left_part.size + right_part.size:
                return
            right_part.size = right_size
        elif left_size > left_part.size + max(0, self.free_space):
            return
        left_part.size = left_size

    def set_bootable(self, row: int, bootable: bool) -> None:
        if self.partitions[row].ags_reservation:
            return
        if bootable:
            for index, part in enumerate(self.partitions):
                part.bootable = index == row
        else:
            self.partitions[row].bootable = False

    def set_extra_directory(self, row: int, path: Path | None) -> None:
        if self.partitions[row].ags_reservation:
            return
        self.partitions[row].extra_content_directory = path

    @property
    def errors(self) -> list[str]:
        return validate_partition_layout(self.disk_size, self.boot_size, self.partitions)

    def to_config(self) -> PartitionConfig:
        return build_partition_config(self.disk_size, self.boot_size, self.partitions)

    def to_layout_draft(self) -> PartitionConfig:
        """Return current editor values for planning, including invalid layouts."""
        return PartitionConfig.model_construct(
            disk_size=self.disk_size,
            layout=[
                MBRPartition.model_construct(type="fat32", name="EMU68BOOT", size=self.boot_size),
                MBRPartition.model_construct(
                    type="id76",
                    name="AMIGA",
                    size=self.id76_size,
                    amiga_partitions=[part.model_copy(deep=True) for part in self.partitions],
                ),
            ],
        )
