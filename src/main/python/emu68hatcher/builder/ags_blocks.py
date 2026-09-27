"""Validate AGS partition boundaries and completed block copies."""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

from .errors import BuildError


@dataclass(frozen=True, slots=True)
class AGSBlockPartition:
    index: int
    device: str
    low_cylinder: int
    high_cylinder: int
    size: int
    offset: int
    block_size: int
    dos_type: bytes
    flags: int


def read_source_blocks(path: Path) -> tuple[AGSBlockPartition, ...]:
    size = path.stat().st_size
    with path.open("rb") as stream:

        def block(offset: int, expected: bytes) -> bytes:
            if offset < 0 or offset + 512 > size:
                raise BuildError("AGS RDB block lies outside the source image")
            stream.seek(offset)
            data = stream.read(512)
            count = struct.unpack_from(">I", data, 4)[0]
            if data[:4] != expected or not 1 <= count <= 128:
                raise BuildError("AGS RDB block is invalid")
            if sum(struct.unpack_from(f">{count}I", data)) & 0xFFFFFFFF:
                raise BuildError("AGS RDB checksum is invalid")
            return data

        stream.seek(0)
        header = stream.read(16 * 512)
        offsets = [i for i in range(0, len(header), 512) if header[i : i + 4] == b"RDSK"]
        if len(offsets) != 1:
            raise BuildError("AGS needs a single direct RDB in the source image")
        rdb = block(offsets[0], b"RDSK")

        def value(data, offset):
            return struct.unpack_from(">I", data, offset)[0]

        block_size = value(rdb, 16)
        sectors, heads = value(rdb, 68), value(rdb, 72)
        if (heads, sectors, block_size) != (16, 63, 512):
            raise BuildError("AGS block copies require 16 heads, 63 sectors and 512-byte blocks")
        cylinder_size = heads * sectors * block_size
        pointer = value(rdb, 28)
        seen = set()
        result = []
        while pointer != 0xFFFFFFFF:
            if pointer in seen or len(seen) >= 128:
                raise BuildError("AGS RDB partition chain is invalid")
            seen.add(pointer)
            part = block(pointer * block_size, b"PART")
            name_length = part[36]
            if not 1 <= name_length <= 31:
                raise BuildError("AGS partition device name is invalid")
            device = part[37 : 37 + name_length].decode("iso-8859-1")
            low, high = value(part, 164), value(part, 168)
            length = (high - low + 1) * cylinder_size
            offset = low * cylinder_size
            if low < value(rdb, 136) or high < low or offset + length > size:
                raise BuildError(f"AGS partition {device} extends outside the source image")
            if (value(part, 140), value(part, 148)) != (heads, sectors):
                raise BuildError(f"AGS partition {device} has incompatible geometry")
            filesystem_block = value(part, 132) * 4 * value(part, 144)
            if filesystem_block != 512:
                raise BuildError(f"AGS partition {device} needs unsupported filesystem blocks")
            if any(
                offset < previous.offset + previous.size and previous.offset < offset + length
                for previous in result
            ):
                raise BuildError("AGS source partitions overlap")
            result.append(
                AGSBlockPartition(
                    len(result) + 1,
                    device,
                    low,
                    high,
                    length,
                    offset,
                    filesystem_block,
                    part[192:196],
                    value(part, 20),
                )
            )
            pointer = value(part, 16)
    return tuple(result)


def verify_copy_result(result, expected_bytes: int) -> None:
    if not result.success:
        raise BuildError(f"AGS partition copy failed: {result.error}")
    output = result.stdout
    completed = re.findall(r"Copied '[^']+' \((\d+) bytes\)", output)
    if " ERR]" in output or completed != [str(expected_bytes)]:
        raise BuildError(f"AGS partition copy is incomplete; expected {expected_bytes} bytes")
    destination = output.split("Destination partition number ", 1)
    if len(destination) != 2:
        raise BuildError("AGS partition copy did not report its destination")
    sizes = re.findall(r"- Size '[^']+' \((\d+) bytes\)", destination[1])
    if sizes != [str(expected_bytes)]:
        raise BuildError("AGS destination partition was shortened or has an unexpected size")


def verify_target_partitions(workflow, runner, plan, image_path) -> None:
    import json

    from .host.hst_commands import HSTCommand, HSTCommandLine, hst_path

    for mbr_index, mbr in enumerate(workflow.config.partitions.layout, 1):
        if mbr.type != "id76":
            continue
        rdb = hst_path(image_path, "mbr", mbr_index)
        info = runner.run_command(
            HSTCommandLine(HSTCommand.RDB_INFO, [rdb]), elevation=workflow.state.elevation
        )
        if not info.success or " ERR]" in info.stdout:
            raise BuildError("Cannot verify AGS target RDB")
        table = info.stdout.split("Partitions:", 1)
        if len(table) != 2:
            raise BuildError("AGS target RDB partition table is missing")
        rows = []
        for line in table[1].split("Partition table overview:", 1)[0].split("\n"):
            fields = [field.strip() for field in line.split("|")]
            if len(fields) == 15 and fields[0].isdigit():
                rows.append(fields)
        if len(rows) != len(mbr.amiga_partitions):
            raise BuildError("AGS target partition count differs from the layout")
        next_cylinder = 2
        for part, fields in zip(mbr.amiga_partitions, rows, strict=True):
            low, high = int(fields[3]), int(fields[4])
            if (
                fields[1] != part.device
                or low != next_cylinder
                or (high - low + 1) * 516096 != part.size
            ):
                raise BuildError(f"AGS target geometry differs for {part.device}")
            next_cylinder = high + 1
            if part.ags_reservation and (
                not fields[9].startswith("0x50465303")
                or fields[12:14] != ["False", "False"]
                or fields[7] != "512"
            ):
                raise BuildError(f"AGS target filesystem flags differ for {part.device}")
        listing = runner.run_command(
            HSTCommandLine(
                HSTCommand.FS_DIR,
                [hst_path(image_path, "mbr", mbr_index, "rdb"), "--format", "Json"],
            ),
            elevation=workflow.state.elevation,
        )
        try:
            entries = json.loads(listing.stdout[listing.stdout.index("{") :])["entries"]
        except (ValueError, KeyError) as error:
            raise BuildError("Cannot verify AGS target volume names") from error
        expected = [(part.device, part.volume, part.size) for part in mbr.amiga_partitions]
        actual = [
            (entry["properties"]["Device Name"], entry["properties"]["Volume Name"], entry["size"])
            for entry in entries
        ]
        if not listing.success or actual != expected:
            raise BuildError("AGS target volumes differ from the applied layout")


def copy_creation_script(workflow, runner, script, plan, image_path) -> None:
    import time

    from .ags_source import validate_source_identity
    from .host.disk_writer import _PERCENT_PROGRESS_RE
    from .host.hst_commands import HSTCommand

    total = sum(component.partition.size for component in plan.inventory.components)
    completed = 0
    for command in script.commands:
        workflow._check_cancelled()
        is_copy = command.command == HSTCommand.RDB_PART_COPY
        expected = 0
        if is_copy:
            validate_source_identity(plan.inventory)
            source_index = int(command.args[1])
            expected = next(p.size for p in plan.inventory.partitions if p.index == source_index)
            workflow._milestone(command.description)
        workflow._update_state(
            progress=95 * completed / total,
            message=f"{command.description}; {completed:,}/{total:,} bytes",
        )
        start = time.monotonic()

        def on_line(
            _stream, line, is_copy=is_copy, expected=expected, completed=completed, command=command
        ):
            if not is_copy:
                return
            match = re.search(
                r"(?:copying|writing|reading|processed)\s*[:\-]?\s*(\d+)\s*(?:/|of)\s*(\d+)",
                line,
                re.IGNORECASE,
            )
            percent_match = _PERCENT_PROGRESS_RE.fullmatch(line)
            if percent_match:
                percent = float(percent_match["percent"].replace(",", "."))
                if not 0 <= percent <= 100:
                    return
                done = int(expected * percent / 100)
            elif match and int(match[2]) == expected:
                done = min(expected, int(match[1]))
            else:
                return
            workflow._update_state(
                progress=95 * (completed + done) / total,
                message=f"{command.description}; {done:,}/{expected:,} bytes",
            )

        result = runner.run_command(
            command,
            timeout=max(300, expected / 262144),
            elevation=workflow.state.elevation,
            on_line=on_line,
        )
        workflow._check_cancelled()
        if is_copy:
            validate_source_identity(plan.inventory)
            verify_copy_result(result, expected)
            completed += expected
            elapsed = time.monotonic() - start
            workflow._milestone(
                f"AGS copied {expected:,} bytes in {elapsed:.2f}s "
                f"({expected / max(elapsed, 0.001) / 1024**2:.1f} MiB/s)"
            )
        elif not result.success or " ERR]" in result.stdout:
            raise BuildError(f"Image creation failed: {result.error or result.stdout}")
    validate_source_identity(plan.inventory)
    verify_target_partitions(workflow, runner, plan, image_path)
