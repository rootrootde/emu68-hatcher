"""Partition configuration models."""

from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from emu68hatcher.config.ags_models import AGSPartitionReservation
from emu68hatcher.config.constants import (
    CYLINDER_SIZE,
    DEFAULT_BOOT_DEVICE,
    FFS_MAX_PARTITION_SIZE,
    MAX_AMIGA_PARTITIONS,
    MBR_OVERHEAD,
    MBR_SECTOR_SIZE,
    MIN_AMIGA_PARTITION_SIZE,
    MIN_BOOT_PARTITION_SIZE,
    PFS3_MAX_PARTITION_SIZE,
    RDB_OVERHEAD,
)


class Filesystem(str, Enum):
    PFS3 = "PFS3"
    FFS = "FFS"


class AmigaPartition(BaseModel):
    device: str = Field(pattern=r"^[A-Z]{2,3}\d+$")
    volume: str = Field(min_length=1, max_length=31)
    filesystem: Filesystem = Filesystem.PFS3
    size: int = Field(gt=0)
    bootable: bool = False
    priority: int = Field(ge=-128, le=127, default=0)
    buffers: int = Field(ge=1, le=600, default=30)
    max_transfer: int = 0x1FE00
    mask: int = 0x7FFFFFFE
    no_mount: bool = False
    extra_content_directory: Path | None = None
    ags_reservation: AGSPartitionReservation | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("extra_content_directory", mode="before")
    @classmethod
    def _convert_extra_content_directory(cls, value):
        if value is None or value == "":
            return None
        return Path(value) if isinstance(value, str) else value


class MBRPartition(BaseModel):
    type: Literal["fat32", "id76"]
    name: str
    size: int = Field(gt=0)
    amiga_partitions: list[AmigaPartition] | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_amiga_partitions(self):
        if self.type == "id76" and not self.amiga_partitions:
            raise ValueError("ID76 partition must have at least one Amiga partition")
        if self.type == "fat32" and self.amiga_partitions:
            raise ValueError("FAT32 partition cannot have Amiga partitions")
        return self


class PartitionConfig(BaseModel):
    disk_size: int = Field(gt=0)
    layout: list[MBRPartition] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    def iter_amiga_partitions(self):
        for mbr_part in self.layout:
            if mbr_part.amiga_partitions:
                yield from mbr_part.amiga_partitions

    @property
    def bootable_device(self) -> str | None:
        for amiga_part in self.iter_amiga_partitions():
            if amiga_part.bootable:
                return amiga_part.device
        return None

    @property
    def bootable_device_or_default(self) -> str:
        return self.bootable_device or DEFAULT_BOOT_DEVICE

    @model_validator(mode="after")
    def validate_partition_sizes(self):
        self.layout = [MBRPartition.model_validate(part.model_dump()) for part in self.layout]
        if len(self.layout) != 2 or [part.type for part in self.layout] != ["fat32", "id76"]:
            raise ValueError("Partition layout must contain FAT32 followed by one ID76 container")

        boot, id76 = self.layout
        if boot.size < MIN_BOOT_PARTITION_SIZE:
            raise ValueError(
                f"Boot partition must be at least {MIN_BOOT_PARTITION_SIZE // (1024 * 1024)} MB"
            )
        if boot.size % MBR_SECTOR_SIZE:
            raise ValueError("Boot partition size must be MBR sector aligned (512 bytes)")
        if id76.size % MBR_SECTOR_SIZE:
            raise ValueError("ID76 partition size must be MBR sector aligned (512 bytes)")

        total = sum(part.size for part in self.layout)
        if total + MBR_OVERHEAD > self.disk_size:
            raise ValueError(
                f"Total partition size ({total}) + overhead ({MBR_OVERHEAD}) "
                f"exceeds disk size ({self.disk_size})"
            )

        devices: list[str] = []
        volumes: list[str] = []
        ags_roles: list[str] = []
        bootable_count = 0
        for mbr in self.layout:
            if mbr.type != "id76" or not mbr.amiga_partitions:
                continue
            usable = mbr.size - RDB_OVERHEAD
            if len(mbr.amiga_partitions) > MAX_AMIGA_PARTITIONS:
                raise ValueError(f"Maximum {MAX_AMIGA_PARTITIONS} Amiga partitions allowed")
            inner_total = sum(part.size for part in mbr.amiga_partitions)
            if inner_total > usable:
                raise ValueError(
                    f"Amiga partitions in {mbr.name!r} exceed RDB usable space by "
                    f"{inner_total - usable} bytes"
                )
            for part in mbr.amiga_partitions:
                _validate_amiga_partition(part)
                devices.append(part.device.upper())
                volumes.append(part.volume.lower())
                if part.ags_reservation:
                    ags_roles.append(part.ags_reservation.role)
                bootable_count += int(part.bootable)

        if len(devices) != len(set(devices)):
            raise ValueError("Amiga device names must be unique (case-insensitive)")
        if len(volumes) != len(set(volumes)):
            raise ValueError("Amiga volume names must be unique (case-insensitive)")
        if len(ags_roles) != len(set(ags_roles)):
            raise ValueError("AGS partition roles must be unique")
        if devices and bootable_count == 0:
            raise ValueError("Exactly one Amiga partition must be bootable")
        if bootable_count > 1:
            raise ValueError("Only one Amiga partition can be bootable")
        return self

    def _uses(self, filesystem: Filesystem) -> bool:
        return any(part.filesystem == filesystem for part in self.iter_amiga_partitions())

    @property
    def uses_pfs3(self) -> bool:
        return self._uses(Filesystem.PFS3)

    @property
    def uses_ffs(self) -> bool:
        return self._uses(Filesystem.FFS)


def _validate_amiga_partition(part: AmigaPartition) -> None:
    if part.ags_reservation is not None:
        if part.filesystem != Filesystem.PFS3 or part.bootable or part.no_mount:
            raise ValueError(f"{part.device}: AGS partitions must be mounted, non-bootable PFS3")
        if part.extra_content_directory is not None:
            raise ValueError(f"{part.device}: AGS partitions cannot contain extra host files")
        if part.size != part.ags_reservation.minimum_size:
            raise ValueError(f"{part.device}: AGS partition must match its fixed source size")
    if part.size < MIN_AMIGA_PARTITION_SIZE:
        raise ValueError(
            f"{part.device}: size must be at least {MIN_AMIGA_PARTITION_SIZE // (1024 * 1024)} MB"
        )
    if part.size % CYLINDER_SIZE:
        raise ValueError(f"{part.device}: size must be cylinder aligned ({CYLINDER_SIZE} bytes)")
    limits = {
        Filesystem.PFS3: PFS3_MAX_PARTITION_SIZE,
        Filesystem.FFS: FFS_MAX_PARTITION_SIZE,
    }
    limit = limits[part.filesystem]
    if part.size > limit:
        raise ValueError(
            f"{part.device}: {part.filesystem.value} partition cannot exceed "
            f"{limit // (1024**3)} GB"
        )
