"""Inspect supported AGS images without changing their contents."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from emu68hatcher.builder.errors import BuildCancelledError, BuildError
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
class AGSInventory:
    source_path: Path
    identity: tuple[int, int, int, int]
    profile: str
    version: str
    partitions: tuple[AGSPartition, ...]
    whdload: AGSPartition
    entries: tuple[AGSEntry, ...]
    content_bytes: int
    file_count: int
    required_bytes: int
    warnings: tuple[str, ...]


def _check_cancel(cancel_check: CancelCheck) -> None:
    if cancel_check and cancel_check():
        raise BuildCancelledError("Build was cancelled by user")


def source_identity(source_path: Path) -> tuple[int, int, int, int]:
    path_text = str(source_path)
    if path_text.startswith(("\\\\", "//")):
        raise BuildError("AGS source must be a local image file; UNC paths are unsupported")
    try:
        stat = source_path.stat()
    except OSError as exc:
        raise BuildError(f"Cannot read AGS source image {source_path}: {exc}") from exc
    if not source_path.is_file():
        raise BuildError(f"AGS source is not a regular image file: {source_path}")
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


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
            detail = output.read_text(encoding="utf-8", errors="replace")[-1000:].strip()
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
                    str(properties["Device Name"]),
                    str(properties["Volume Name"]),
                    int(raw["size"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BuildError("AGS image has an invalid RDB partition") from exc
    if not result:
        raise BuildError("AGS image has no supported direct RDB partitions")
    return tuple(result)


def _check_script_hashes(
    source_path: Path, partition: AGSPartition, cancel_check: CancelCheck
) -> None:
    from emu68hatcher.builder.ags_scripts import V30_SCRIPT_HASHES

    with tempfile.TemporaryDirectory(prefix="ags-scripts-") as temp:
        root = Path(temp)
        for position, (relative, expected) in enumerate(V30_SCRIPT_HASHES.items()):
            _check_cancel(cancel_check)
            target = root / str(position)
            target.mkdir()
            output = root / f"copy-{position}.log"
            source = f"{source_path.as_posix()}/rdb/{partition.index}/{relative}"
            run_hst_to_file(
                ["fs", "copy", source, str(target), "--uaemetadata", "None"],
                output,
                cancel_check,
            )
            extracted = target / Path(relative).name
            if (
                not extracted.is_file()
                or hashlib.sha256(extracted.read_bytes()).hexdigest() != expected
            ):
                raise BuildError(f"AGS script profile is unsupported: {relative} differs from v30")


def inspect_ags_source(source_path: Path, cancel_check: CancelCheck = None) -> AGSInventory:
    if str(source_path).startswith(("\\\\", "//")):
        raise BuildError("AGS source must be a local image file; UNC paths are unsupported")
    source_path = Path(source_path).expanduser().resolve()
    identity = source_identity(source_path)
    with tempfile.TemporaryDirectory(prefix="ags-inspect-") as temp:
        root = Path(temp)
        partition_json = root / "partitions.json"
        run_hst_to_file(
            ["fs", "dir", f"{source_path.as_posix()}/rdb", "--format", "Json"],
            partition_json,
            cancel_check,
        )
        partitions = _partitions(partition_json)
        candidates = [part for part in partitions if part.volume.casefold() == "whdload"]
        if len(candidates) != 1:
            raise BuildError("AGS image needs one WHDLoad RDB volume")
        whdload = candidates[0]
        listing = root / "whdload.json"
        run_hst_to_file(
            [
                "fs",
                "dir",
                f"{source_path.as_posix()}/rdb/{whdload.index}",
                "--recursive",
                "--format",
                "Json",
                "--uaemetadata",
                "UaeFsDb",
            ],
            listing,
            cancel_check,
        )
        entries = []
        seen = set()
        content_bytes = 0
        file_count = 0
        for raw in iter_hst_entries(listing):
            if len(entries) % 4096 == 0:
                _check_cancel(cancel_check)
            entry = _entry(raw)
            key = entry.path.lower()
            if key in seen:
                raise BuildError(f"AGS source has colliding names: {entry.path}")
            seen.add(key)
            entries.append(entry)
            if not entry.is_dir:
                file_count += 1
                content_bytes += entry.size
        markers = {
            "ags2/ags2",
            "ags2/ags2menu",
            "ags2/scripts/start_ags",
            "ags2/scripts/start_ags.info",
            "ags2/scripts/ags-stuff",
            "game",
            "demo",
            "beta",
            "magazine",
        }
        if not markers <= seen:
            missing = ", ".join(sorted(markers - seen))
            raise BuildError(f"AGS WHDLoad volume is missing required content: {missing}")
        _check_script_hashes(source_path, whdload, cancel_check)
    if source_identity(source_path) != identity:
        raise BuildError("AGS source image changed during inspection")
    required = ((content_bytes + len(entries) * 8192) * 11 + 9) // 10 + 512 * _MIB
    return AGSInventory(
        source_path,
        identity,
        "v30",
        "3.0",
        partitions,
        whdload,
        tuple(entries),
        content_bytes,
        file_count,
        required,
        ("Only the v30 WHDLoad volume is supported; Games and emulators are excluded.",),
    )
