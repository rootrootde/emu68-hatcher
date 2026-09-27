"""Inspect supported AGS images without changing their contents."""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from emu68hatcher.builder.errors import BuildCancelledError, BuildError
from emu68hatcher.config.ags_models import AGSRole
from emu68hatcher.utils.host_tools import find_hst_imager, get_hst_imager_env

CancelCheck = Callable[[], bool] | None
_MIB = 1024 * 1024


@dataclass(frozen=True, slots=True)
class AGSPartition:
    index: int
    device: str
    volume: str
    size: int


@dataclass(frozen=True, slots=True)
class AGSEntry:
    path: str
    size: int
    is_dir: bool
    protection_bits: int
    comment: str


@dataclass(frozen=True, slots=True)
class AGSComponentInventory:
    role: AGSRole
    partition: AGSPartition
    source_subpath: str
    entries: tuple[AGSEntry, ...]
    content_bytes: int
    file_count: int
    directory_count: int


@dataclass(frozen=True, slots=True)
class AGSInventory:
    source_path: Path
    identity: tuple[int, int, int, int, int]
    profile: str
    version: str
    partitions: tuple[AGSPartition, ...]
    components: tuple[AGSComponentInventory, ...]
    marker_hashes: Mapping[str, str]
    warnings: tuple[str, ...]
    source_scripts: Mapping[str, bytes] = field(default_factory=lambda: MappingProxyType({}))

    def component(self, role: AGSRole) -> AGSComponentInventory:
        for component in self.components:
            if component.role == role:
                return component
        raise KeyError(role)

    @property
    def whdload(self) -> AGSPartition:
        return self.component("whdload").partition

    @property
    def entries(self) -> tuple[AGSEntry, ...]:
        return self.component("whdload").entries

    @property
    def content_bytes(self) -> int:
        return sum(component.content_bytes for component in self.components)

    @property
    def file_count(self) -> int:
        return sum(component.file_count for component in self.components)

    @property
    def required_bytes(self) -> int:
        from emu68hatcher.builder.ags_requirements import calculate_ags_requirements

        return sum(r.minimum_partition_bytes for r in calculate_ags_requirements(self.components))


def _check_cancel(cancel_check: CancelCheck) -> None:
    if cancel_check and cancel_check():
        raise BuildCancelledError("Build was cancelled by user")


def source_identity(source_path: Path) -> tuple[int, int, int, int, int]:
    path_text = str(source_path)
    if path_text.startswith(("\\\\", "//")):
        raise BuildError("AGS source must be a local image file; UNC paths are unsupported")
    try:
        stat = source_path.stat()
    except OSError as exc:
        raise BuildError(f"Cannot read AGS source image {source_path}: {exc}") from exc
    if not source_path.is_file():
        raise BuildError(f"AGS source is not a regular image file: {source_path}")
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def validate_source_identity(inventory: AGSInventory) -> None:
    if source_identity(inventory.source_path) != inventory.identity:
        raise BuildError("AGS source image changed after inspection; inspect it again")


def run_hst_to_file(args: list[str], output: Path, cancel_check: CancelCheck = None) -> None:
    binary = find_hst_imager()
    if binary is None or not binary.is_file():
        raise BuildError("HST Imager is needed to inspect the AGS image")
    _check_cancel(cancel_check)
    error_path = output.with_name(output.name + ".err")
    with output.open("wb") as out_stream, error_path.open("wb") as error_stream:
        try:
            process = subprocess.Popen(
                [str(binary), *args],
                stdout=out_stream,
                stderr=error_stream,
                env=get_hst_imager_env(),
            )
        except OSError as exc:
            raise BuildError(f"Cannot start HST Imager: {exc}") from exc
        try:
            while process.poll() is None:
                _check_cancel(cancel_check)
                time.sleep(0.2)
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
    if process.returncode:
        detail = error_path.read_text(encoding="utf-8", errors="replace").strip()
        if not detail:
            lines = output.read_text(encoding="utf-8", errors="replace").splitlines()
            errors = [
                line for line in lines if "[ERR]" in line or " ERR]" in line or "Exception:" in line
            ]
            detail = "\n".join(errors or lines[-12:])[:2000].strip()
        raise BuildError(f"Cannot read AGS image: {detail or f'HST exit {process.returncode}'}")
    _check_cancel(cancel_check)


def iter_hst_entries(path: Path) -> Iterator[dict]:
    decoder = json.JSONDecoder()
    with path.open("r", encoding="utf-8") as stream:
        buffer = stream.read(65536)
        marker = buffer.find('"entries"')
        if marker < 0:
            raise BuildError("HST returned no AGS directory entries")
        offset = buffer.find("[", marker + len('"entries"'))
        if offset < 0:
            raise BuildError("HST returned an invalid AGS directory listing")
        offset += 1
        while True:
            while True:
                if offset >= len(buffer):
                    chunk = stream.read(65536)
                    if not chunk:
                        raise BuildError("HST AGS directory listing ended early")
                    buffer = buffer[offset:] + chunk
                    offset = 0
                if buffer[offset] in " \t\r\n,":
                    offset += 1
                    continue
                break
            if buffer[offset] == "]":
                return
            while True:
                try:
                    entry, end = decoder.raw_decode(buffer, offset)
                    break
                except json.JSONDecodeError as exc:
                    chunk = stream.read(65536)
                    if not chunk:
                        raise BuildError("HST returned invalid AGS directory JSON") from exc
                    buffer = buffer[offset:] + chunk
                    offset = 0
            if not isinstance(entry, dict):
                raise BuildError("HST returned an invalid AGS directory entry")
            yield entry
            offset = end
            if offset > 65536:
                buffer = buffer[offset:]
                offset = 0


def _entry_path(raw: dict) -> str:
    components = raw.get("relativePathComponents")
    if not isinstance(components, list) or not components:
        raise BuildError("AGS image contains an entry without a relative path")
    if any(
        not isinstance(part, str) or not part or part in (".", "..") or "/" in part or "\0" in part
        for part in components
    ):
        raise BuildError(f"AGS image contains an unsafe path: {components!r}")
    return "/".join(components)


def _entry(raw: dict, *, host_defaults: bool = False) -> AGSEntry:
    path = _entry_path(raw)
    kind = raw.get("type")
    size = raw.get("size")
    properties = raw.get("properties")
    if kind not in (0, 1) or not isinstance(size, int) or size < 0:
        raise BuildError(f"AGS image contains an invalid entry: {path}")
    if not isinstance(properties, dict):
        raise BuildError(f"AGS image has no metadata for {path}")
    try:
        protection = int(
            properties.get("$ProtectionBits", "0")
            if host_defaults
            else properties["$ProtectionBits"]
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise BuildError(f"AGS image has no protection bits for {path}") from exc
    comment = properties.get("Comment", "")
    if not isinstance(comment, str):
        raise BuildError(f"AGS image has an invalid comment for {path}")
    return AGSEntry(path, size, kind == 0, protection, comment)


def _partitions(path: Path) -> tuple[AGSPartition, ...]:
    result = []
    for raw in iter_hst_entries(path):
        properties = raw.get("properties", {})
        try:
            result.append(
                AGSPartition(
                    int(raw["name"]),
                    str(properties["Device Name"]).strip(),
                    str(properties["Volume Name"]).strip(),
                    int(raw["size"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BuildError("AGS image has an invalid RDB partition") from exc
    if not result:
        raise BuildError("AGS image has no supported direct RDB partitions")
    return tuple(result)


def _volume(partitions: tuple[AGSPartition, ...], name: str) -> AGSPartition:
    matches = [part for part in partitions if part.volume.casefold() == name.casefold()]
    if len(matches) != 1:
        raise BuildError(f"AGS image needs exactly one {name} RDB volume")
    return matches[0]


def inspect_ags_partitions(
    source_path: Path, cancel_check: CancelCheck = None
) -> tuple[AGSPartition, ...]:
    with tempfile.TemporaryDirectory(prefix="ags-partitions-") as temp:
        output = Path(temp) / "partitions.json"
        run_hst_to_file(
            ["fs", "dir", f"{source_path.as_posix()}/rdb", "--format", "Json"],
            output,
            cancel_check,
        )
        return _partitions(output)


def read_ags_scripts(
    source_path: Path,
    partition: AGSPartition,
    paths: tuple[str, ...],
    cancel_check: CancelCheck = None,
) -> Mapping[str, bytes]:
    scripts: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory(prefix="ags-scripts-") as temp:
        root = Path(temp)
        for position, relative in enumerate(paths):
            _check_cancel(cancel_check)
            target = root / str(position)
            target.mkdir()
            source = f"{source_path.as_posix()}/rdb/{partition.index}/{relative}"
            run_hst_to_file(
                ["fs", "copy", source, str(target), "--uaemetadata", "None"],
                root / f"copy-{position}.log",
                cancel_check,
            )
            extracted = target / Path(relative).name
            if not extracted.is_file():
                raise BuildError(f"AGS source is missing script {relative}")
            scripts[relative] = extracted.read_bytes()
    return MappingProxyType(scripts)


def inspect_ags_component(
    source_path: Path,
    partitions: tuple[AGSPartition, ...],
    role: AGSRole,
    cancel_check: CancelCheck = None,
) -> AGSComponentInventory:
    from .ags_blocks import read_source_blocks

    volume = {"whdload": "WHDLoad", "games": "Games", "work": "Work", "media": "Media"}[role]
    partition = _volume(partitions, volume)
    _check_cancel(cancel_check)
    blocks = read_source_blocks(source_path)
    block = blocks[partition.index - 1]
    if block.size != partition.size or block.device != partition.device:
        raise BuildError(f"AGS {volume} filesystem and RDB geometry disagree")
    if block.dos_type not in (b"PDS\x03", b"PFS\x03") or block.flags != 0:
        raise BuildError(f"AGS {volume} requires mounted, non-bootable PDS3/PFS3")
    required = {
        "whdload": {"ags2", "game", "demo", "magazine"},
        "games": {"premium"},
        "work": {"emulators"},
        "media": {"st-00"},
    }[role]
    with tempfile.TemporaryDirectory(prefix="ags-markers-") as temporary:
        listing = Path(temporary) / "markers.json"
        source = f"{source_path.as_posix()}/rdb/{partition.index}"
        run_hst_to_file(["fs", "dir", source, "--format", "Json"], listing, cancel_check)
        names = {entry["name"].casefold() for entry in iter_hst_entries(listing)}
        if required - names:
            raise BuildError(f"AGS {volume} is missing: {', '.join(sorted(required - names))}")
        if role == "whdload":
            run_hst_to_file(
                ["fs", "dir", source + "/AGS2", "--format", "Json"], listing, cancel_check
            )
            names = {entry["name"].casefold() for entry in iter_hst_entries(listing)}
            required_menu = {"ags2", "ags2menu", "ags2.conf", "os", "scripts"}
            if required_menu - names:
                raise BuildError("AGS source is missing portable menu files")
    return AGSComponentInventory(role, partition, "", (), partition.size, 0, 0)


def inspect_ags_source(
    source_path: Path,
    roles: tuple[AGSRole, ...] | list[AGSRole] = ("whdload",),
    cancel_check: CancelCheck = None,
    refresh: bool = False,
) -> AGSInventory:
    from emu68hatcher.builder.ags_inspection import inspect_ags_source as inspect_cached

    return inspect_cached(source_path, roles, cancel_check=cancel_check, refresh=refresh)
