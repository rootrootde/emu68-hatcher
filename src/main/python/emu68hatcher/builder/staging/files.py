"""Amiga file staging"""

import logging
import shutil
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from emu68hatcher.data.catalog import validate_relative_path
from emu68hatcher.utils.paths import ensure_dir

logger = logging.getLogger(__name__)


# --- Amiga .info file tooltype modification ---


def read_info_tooltypes(info_path: Path) -> list[str]:
    """read tooltypes from an Amiga .info file"""
    data = info_path.read_bytes()
    _, tooltypes, _ = _parse_info_to_tooltypes(data)
    return tooltypes


def write_info_tooltypes(info_path: Path, tooltypes: list[str]) -> None:
    """replace all tooltypes in an Amiga .info file"""
    data = info_path.read_bytes()
    tt_start, _, tt_end = _parse_info_to_tooltypes(data)

    # tooltypes-present flag in the .info header (any non-zero value works)
    header = bytearray(data[:78])
    if tooltypes:
        struct.pack_into(">I", header, 54, 1)
    else:
        struct.pack_into(">I", header, 54, 0)

    # tooltypes section: DWORD ptr_array_size = (n+1)*4, then n x (DWORD len + null-terminated bytes)
    ptr_array_size = (len(tooltypes) + 1) * 4
    new_tt = struct.pack(">I", ptr_array_size)
    for tt in tooltypes:
        tt_bytes = tt.encode("iso-8859-1") + b"\x00"
        new_tt += struct.pack(">I", len(tt_bytes)) + tt_bytes

    new_data = bytes(header) + data[78:tt_start] + new_tt + data[tt_end:]
    info_path.write_bytes(new_data)
    logger.info(f"Wrote {len(tooltypes)} tooltypes to {info_path.name}")


def _parse_info_to_tooltypes(data: bytes) -> tuple[int, list[str], int]:
    """parse an Amiga .info file to find the tooltypes section"""
    size = len(data)
    if size < 78:
        raise ValueError(f"File too small for .info header ({size} bytes)")

    magic = struct.unpack(">H", data[0:2])[0]
    if magic != 0xE310:
        raise ValueError(f"Not an Amiga .info file (magic=0x{magic:04X})")

    def be_long(off: int) -> int:
        return struct.unpack(">I", data[off : off + 4])[0]

    has_first_image = be_long(22) != 0
    has_second_image = be_long(26) != 0
    has_default_tool = be_long(50) != 0
    has_tooltypes = be_long(54) != 0
    has_drawer_data = be_long(66) != 0

    offset = 78  # after DiskObject header

    # drawer/disk/garbage icons have a 56-byte OldDrawerData block before images
    if has_drawer_data:
        offset += 56

    # skip first image (struct Image = 20 bytes + pixel data)
    if has_first_image:
        offset = _skip_image(data, offset)

    # skip second image
    if has_second_image:
        offset = _skip_image(data, offset)

    # skip DefaultTool string
    if has_default_tool and offset + 4 <= size:
        dt_len = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4 + dt_len

    # tooltypes section: DWORD ptr_array_size = (n+1)*4, then n x (DWORD len + null-terminated bytes)
    tt_start = offset
    tooltypes: list[str] = []
    if has_tooltypes and offset + 4 <= size:
        ptr_array_size = struct.unpack(">I", data[offset : offset + 4])[0]
        num_entries = (ptr_array_size // 4) - 1 if ptr_array_size >= 4 else 0
        offset += 4
        for _ in range(num_entries):
            if offset + 4 > size:
                break
            tt_len = struct.unpack(">I", data[offset : offset + 4])[0]
            if offset + 4 + tt_len > size:
                break
            raw = data[offset + 4 : offset + 4 + tt_len]
            # strip null terminator
            tt_str = raw.rstrip(b"\x00").decode("iso-8859-1")
            tooltypes.append(tt_str)
            offset += 4 + tt_len

    return tt_start, tooltypes, offset


def _skip_image(data: bytes, offset: int) -> int:
    """skip an Amiga Image structure + pixel data, return new offset"""
    # bounds-check so a truncated icon raises ValueError (caught upstream) not struct.error
    if offset + 10 > len(data):
        raise ValueError("truncated .info: image header runs past end of file")
    width = struct.unpack(">H", data[offset + 4 : offset + 6])[0]
    height = struct.unpack(">H", data[offset + 6 : offset + 8])[0]
    depth = struct.unpack(">H", data[offset + 8 : offset + 10])[0]
    offset += 20  # image struct size
    # pixel data: word-aligned width * height * depth
    word_width = (width + 15) // 16
    offset += word_width * 2 * height * depth
    return offset


@dataclass
class AmigaFile:
    """represents a file to be installed on Amiga"""

    source_path: Path
    dest_path: str  # amiga-style path (e.g., "C/Dir")
    device: str = "DH0"
    icon_path: Path | None = None

    @property
    def has_icon(self) -> bool:
        """check if file has an associated icon"""
        return self.icon_path is not None and self.icon_path.exists()


def ci_match_child(parent: Path, name: str) -> str | None:
    """case-insensitive lookup of 'name' under 'parent', returning actual on-disk name or None"""
    if (parent / name).exists():
        return name
    if not parent.is_dir():
        return None
    name_lower = name.lower()
    try:
        for entry in parent.iterdir():
            if entry.name.lower() == name_lower:
                return entry.name
    except OSError:
        pass
    return None


def resolve_staging_path(base: Path, rel_path: str) -> Path:
    """case-insensitively resolve rel_path under base; missing components are appended as-is"""
    validate_relative_path(rel_path)
    result = base
    for part in Path(rel_path).parts:
        matched = ci_match_child(result, part) if result.is_dir() else None
        result = result / (matched if matched else part)
    require_contained_path(base, result)
    return result


def require_contained_path(base: Path, path: Path) -> Path:
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError(f"path leaves its source or staging directory: {path}")
    return path


def resolve_source_path(base: Path, rel_path: str) -> Path | None:
    """Resolve every component case-insensitively, returning None on a miss."""
    validate_relative_path(rel_path)
    current = base
    for part in rel_path.split("/"):
        if not part:
            continue
        matched = ci_match_child(current, part)
        if matched is None:
            return None
        current /= matched
        require_contained_path(base, current)
    return current


@dataclass
class FileMapping:
    """mapping of source files to destinations on Amiga"""

    files: list[AmigaFile] = field(default_factory=list)

    def add(self, source: Path, dest: str, device: str = "DH0") -> None:
        """add a file mapping"""
        # check for associated icon
        icon_path = Path(str(source) + ".info")
        if not icon_path.exists():
            icon_path = None

        self.files.append(
            AmigaFile(
                source_path=source,
                dest_path=dest,
                device=device,
                icon_path=icon_path,
            )
        )

    def add_directory(
        self,
        source_dir: Path,
        dest_dir: str,
        device: str = "DH0",
        recursive: bool = True,
        filter_func: Callable[[Path], bool] | None = None,
    ) -> None:
        """add all files from a directory"""
        if not source_dir.exists():
            return

        pattern = "**/*" if recursive else "*"

        # case-fold dedupe so Linux ext4 matches PFS3 semantics (newest mtime wins on collision)
        by_ci_path: dict[str, Path] = {}
        for source in source_dir.glob(pattern):
            if not source.is_file():
                continue
            key = str(source.relative_to(source_dir)).lower()
            existing = by_ci_path.get(key)
            if existing is None or source.stat().st_mtime > existing.stat().st_mtime:
                by_ci_path[key] = source

        for source in by_ci_path.values():
            if source.suffix.lower() == ".info":
                base_path = source.with_suffix("")
                if base_path.exists() and base_path.is_file():
                    continue

            if filter_func and not filter_func(source):
                continue

            rel_path = source.relative_to(source_dir)
            amiga_rel_path = unix_to_amiga_path(str(rel_path))
            if dest_dir:
                dest_path = f"{dest_dir}/{amiga_rel_path}"
            else:
                dest_path = amiga_rel_path

            self.add(source, dest_path, device)


def unix_to_amiga_path(path: str) -> str:
    """Unix path -> Amiga-style (already Amiga if it contains colon)"""
    if ":" in path:
        return path

    path = path.replace("\\", "/")

    if path.startswith("./"):
        path = path[2:]

    path = path.lstrip("/")

    return path


def prepare_staging_directory(
    staging_dir: Path,
    devices: list[str],
    boot_device: str | None = None,
) -> dict[str, Path]:
    """prepare per-device staging dirs. Amiga scaffold dirs (C/S/L/...) only on the boot partition"""
    device_dirs = {}

    AMIGA_SUBDIRS = ["C", "S", "L", "Libs", "Devs", "Prefs", "Fonts", "T"]

    NON_AMIGA_DEVICES = {"EMU68BOOT", "BOOT", "FAT32"}

    for device in devices:
        device_dir = ensure_dir(staging_dir / device)
        device_dirs[device] = device_dir

        # scaffold only on the boot partition; Work/Data etc start empty so they
        # keep boot-only system drawers off data volumes
        if (
            device.upper() not in NON_AMIGA_DEVICES
            and boot_device is not None
            and device == boot_device
        ):
            for subdir in AMIGA_SUBDIRS:
                ensure_dir(device_dir / subdir)

    return device_dirs


def stage_files(
    mapping: FileMapping,
    staging_dir: Path,
) -> int:
    """copy files into the staging tree"""
    count = 0

    for amiga_file in mapping.files:
        if not amiga_file.source_path.exists():
            continue

        device_dir = staging_dir / amiga_file.device
        dest = resolve_staging_path(device_dir, amiga_file.dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        # main file
        shutil.copy2(amiga_file.source_path, dest)
        count += 1

        # copy icon if present
        if amiga_file.has_icon:
            icon_dest = Path(str(dest) + ".info")
            shutil.copy2(amiga_file.icon_path, icon_dest)
            count += 1

    return count
