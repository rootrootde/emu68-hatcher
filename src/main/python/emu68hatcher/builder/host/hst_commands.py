"""hst-imager command generation - blank image, MBR+RDB partition, format, fs copy"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from emu68hatcher.config.schema import (
    AmigaPartition,
    BuildConfig,
    Filesystem,
    MBRPartition,
)


class HSTCommand(str, Enum):
    """hst-imager subcommands used by the build pipeline"""

    # image + partition operations
    BLANK = "blank"
    MBR_INIT = "mbr init"
    MBR_PART_ADD = "mbr part add"
    MBR_PART_FORMAT = "mbr part format"
    RDB_INIT = "rdb init"
    RDB_PART_ADD = "rdb part add"
    RDB_PART_COPY = "rdb part copy"
    RDB_INFO = "rdb info"
    RDB_PART_FORMAT = "rdb part format"
    RDB_FS_ADD = "rdb fs add"

    # filesystem operations
    FS_DIR = "fs dir"
    FS_COPY = "fs copy"


@dataclass
class HSTCommandLine:
    """one hst-imager command"""

    command: HSTCommand
    args: list[str] = field(default_factory=list)
    description: str = ""

    def to_args(self) -> list[str]:
        """argv form"""
        return self.command.value.split() + self.args

    def to_string(self) -> str:
        """display form"""
        return f"hst-imager {self.command.value} {' '.join(self.args)}"


@dataclass
class HSTScript:
    """ordered list of HSTCommandLines"""

    commands: list[HSTCommandLine] = field(default_factory=list)
    description: str = ""


##############################################
# filesystem handler paths (PFS3, FFS, etc.) #
##############################################

# dostype: 4 ASCII chars, last is interpreted as a hex digit ("PFS3" → PFS\x03)
FILESYSTEM_HANDLERS = {
    Filesystem.PFS3: {
        "name": "pfs3aio",
        "dostype": "PFS3",
        "handler_file": "pfs3aio",
    },
    Filesystem.FFS: {
        "name": "FastFileSystem",
        "dostype": "DOS3",
        "handler_file": "FastFileSystem",
    },
}


######################
# command Generators #
######################


def hst_path(image_path: Path | str, *parts: str | int) -> str:
    """hst-imager target path; windows physical drives need backslashes (mixing breaks fs copy)"""
    s = str(image_path)
    if s.startswith("\\\\.\\"):
        sep, base = "\\", s
    else:
        sep, base = "/", s if isinstance(image_path, str) else image_path.as_posix()
    if not parts:
        return base
    return base + sep + sep.join(str(p) for p in parts)


def generate_blank_image_command(
    output_path: Path,
    size_bytes: int,
) -> HSTCommandLine:
    """blank disk image"""
    return HSTCommandLine(
        command=HSTCommand.BLANK,
        args=[str(output_path), str(size_bytes)],
        description=f"Create blank {size_bytes // (1024**3)}GB image",
    )


def generate_mbr_init_command(image_path: Path) -> HSTCommandLine:
    """init MBR on image"""
    return HSTCommandLine(
        command=HSTCommand.MBR_INIT,
        args=[str(image_path)],
        description="Initialize MBR partition table",
    )


def generate_mbr_partition_commands(
    image_path: Path,
    partitions: list[MBRPartition],
) -> list[HSTCommandLine]:
    """MBR partition create + format commands"""
    commands = []

    # first partition at sector 2048 to match the reference imager
    MBR_FIRST_PARTITION_START_SECTOR = 2048
    current_sector = MBR_FIRST_PARTITION_START_SECTOR

    for i, part in enumerate(partitions):
        if part.type == "fat32":
            part_type = "0xb"  # FAT32 - reference imager uses 0xb, not 0x0C
        else:
            part_type = "0x76"  # amiga RDB (ID76)

        size_sectors = part.size // 512  # 512-byte sectors

        commands.append(
            HSTCommandLine(
                command=HSTCommand.MBR_PART_ADD,
                args=[
                    str(image_path),
                    part_type,
                    str(part.size),
                    "--start-sector",
                    str(current_sector),
                ],
                description=f"Add MBR partition {i + 1}: {part.name} ({part.type})",
            )
        )

        current_sector += size_sectors

        if part.type == "fat32":
            commands.append(
                HSTCommandLine(
                    command=HSTCommand.MBR_PART_FORMAT,
                    args=[
                        str(image_path),
                        str(i + 1),  # partition number (1-based)
                        part.name,
                    ],
                    description=f"Format {part.name} as FAT32",
                )
            )

    return commands


def generate_rdb_init_command(
    image_path: Path,
    partition_number: int,
) -> HSTCommandLine:
    """init RDB on ID76 partition"""
    rdb_path = hst_path(image_path, "mbr", partition_number)
    return HSTCommandLine(
        command=HSTCommand.RDB_INIT,
        args=[rdb_path],
        description=f"Initialize RDB in MBR partition {partition_number}",
    )


def _fs_info(fs: Filesystem) -> dict:
    """filesystem handler descriptor, defaulting to PFS3 for unknown filesystems"""
    return FILESYSTEM_HANDLERS.get(fs, FILESYSTEM_HANDLERS[Filesystem.PFS3])


def generate_rdb_filesystem_command(
    image_path: Path,
    partition_number: int,
    filesystem: Filesystem,
    handler_path: Path | None = None,
) -> HSTCommandLine:
    """add filesystem handler to RDB"""
    fs_info = _fs_info(filesystem)

    rdb_path = hst_path(image_path, "mbr", partition_number)

    # rdb fs add "path" "handler_path" DOSTYPE
    if handler_path and fs_info["handler_file"]:
        args = [rdb_path, str(handler_path), fs_info["dostype"]]
    else:
        # built-in filesystems still need a handler path - caller must supply
        args = [rdb_path, fs_info["name"], fs_info["dostype"]]

    return HSTCommandLine(
        command=HSTCommand.RDB_FS_ADD,
        args=args,
        description=f"Add {filesystem.value} filesystem handler",
    )


def generate_rdb_partition_commands(
    image_path: Path,
    mbr_partition_number: int,
    amiga_partitions: list[AmigaPartition],
    ags_plan=None,
) -> list[HSTCommandLine]:
    """create + format the amiga RDB partitions"""
    commands = []

    rdb_path = hst_path(image_path, "mbr", mbr_partition_number)

    for i, part in enumerate(amiga_partitions):
        if part.ags_reservation:
            if ags_plan is None:
                raise ValueError("AGS partition copy requires a validated build plan")
            source = ags_plan.inventory.component(part.ags_reservation.role).partition
            if source.size != part.size:
                raise ValueError("AGS partition size differs from its source")
            commands.append(
                HSTCommandLine(
                    HSTCommand.RDB_PART_COPY,
                    [
                        str(ags_plan.inventory.source_path),
                        str(source.index),
                        rdb_path,
                        "--name",
                        part.device,
                        "--dos-type",
                        "PFS3",
                        "--verbose",
                    ],
                    f"Copy AGS {source.volume} to {part.device} ({source.size:,} bytes)",
                )
            )
            continue
        fs_info = _fs_info(part.filesystem)

        # rdb part add "path/mbr/N" DEVICE DOSTYPE SIZE --buffers ... --bootable --boot-priority
        args = [
            rdb_path,
            part.device,
            fs_info["dostype"],
            str(part.size),
            "--buffers",
            str(part.buffers),
            "--max-transfer",
            hex(part.max_transfer),
            "--mask",
            hex(part.mask),
        ]

        if part.no_mount:
            args.append("--no-mount")

        if part.bootable:
            args.extend(["--bootable", "--boot-priority", str(part.priority)])

        commands.append(
            HSTCommandLine(
                command=HSTCommand.RDB_PART_ADD,
                args=args,
                description=f"Add Amiga partition {part.device}: {part.volume}",
            )
        )

        rdb_partition_number = i + 1  # rdb partition numbers are 1-based
        commands.append(
            HSTCommandLine(
                command=HSTCommand.RDB_PART_FORMAT,
                args=[
                    rdb_path,
                    str(rdb_partition_number),
                    part.volume,
                ],
                description=f"Format {part.device} as {part.filesystem.value}",
            )
        )

    return commands


def generate_disk_creation_script(
    config: BuildConfig,
    output_path: Path,
    fs_handler_paths: dict[Filesystem, Path] | None = None,
    skip_blank: bool = False,
    ags_plan=None,
) -> HSTScript:
    """full disk-creation script. skip_blank=True for DEVICE mode or pre-allocated sparse IMG."""
    if config.partitions is None:
        raise ValueError("Partition configuration is required")

    fs_handler_paths = fs_handler_paths or {}
    script = HSTScript(description="Emu68 Hatcher - Disk Creation")

    # blank image - skipped for DEVICE and sparse pre-alloc
    if not skip_blank:
        script.commands.append(
            generate_blank_image_command(output_path, config.partitions.disk_size)
        )

    script.commands.append(generate_mbr_init_command(output_path))
    script.commands.extend(generate_mbr_partition_commands(output_path, config.partitions.layout))

    for i, mbr_part in enumerate(config.partitions.layout):
        if mbr_part.type == "id76" and mbr_part.amiga_partitions:
            mbr_num = i + 1  # 1-based

            script.commands.append(generate_rdb_init_command(output_path, mbr_num))

            # one fs handler entry per distinct filesystem
            filesystems_added = set()
            for amiga_part in mbr_part.amiga_partitions:
                fs = amiga_part.filesystem
                if fs in filesystems_added:
                    continue
                fs_info = FILESYSTEM_HANDLERS.get(fs)
                handler_path = fs_handler_paths.get(fs)
                if fs_info and fs_info.get("handler_file") and handler_path:
                    script.commands.append(
                        generate_rdb_filesystem_command(
                            output_path,
                            mbr_num,
                            fs,
                            handler_path,
                        )
                    )
                    filesystems_added.add(fs)

            script.commands.extend(
                generate_rdb_partition_commands(
                    output_path,
                    mbr_num,
                    mbr_part.amiga_partitions,
                    ags_plan=ags_plan,
                )
            )

    return script
