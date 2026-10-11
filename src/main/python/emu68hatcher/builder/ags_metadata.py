"""UAE filesystem database records for copied Amiga files."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.errors import BuildCancelledError, BuildError

if TYPE_CHECKING:
    from emu68hatcher.builder.ags_source import AGSEntry

_DB_NAME = "_UAEFSDB.___"
_RECORD_SIZE = 600
_AMIGA_NAME = slice(5, 261)
_HOST_NAME = slice(262, 518)
_COMMENT = slice(519, 600)


@dataclass(frozen=True, slots=True)
class _Record:
    amiga_name: str
    host_name: str
    protection_bits: int
    comment: str


def _check_name(name: str, directory: Path) -> str:
    if not name or name in (".", "..") or "/" in name or "\0" in name:
        raise BuildError(f"AGS metadata has an unsafe name in {directory}: {name!r}")
    return name


def _field_name(record: bytes, field: slice, directory: Path) -> str:
    try:
        value = (
            record[field]
            .split(b"\0", 1)[0]
            .decode("utf-8" if field == _HOST_NAME else "iso-8859-1")
        )
    except UnicodeDecodeError as exc:
        raise BuildError(f"AGS metadata has an invalid host name in {directory}") from exc
    return _check_name(value, directory)


def _records_by_host(parent: Path) -> dict[str, _Record]:
    path = parent / _DB_NAME
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {}
    if not stat.S_ISREG(info.st_mode):
        raise BuildError(f"AGS metadata is not a regular file in {parent}")
    data = path.read_bytes()
    if len(data) % _RECORD_SIZE:
        raise BuildError(f"AGS metadata is damaged in {parent}")
    records: dict[str, _Record] = {}
    for offset in range(0, len(data), _RECORD_SIZE):
        raw = data[offset : offset + _RECORD_SIZE]
        if raw[0] == 0:
            continue
        if raw[0] != 1:
            raise BuildError(f"AGS metadata has an invalid record in {parent}")
        amiga_name = _field_name(raw, _AMIGA_NAME, parent)
        host_name = _field_name(raw, _HOST_NAME, parent)
        protection_bits = int.from_bytes(raw[1:5], "big")
        if protection_bits > 255:
            raise BuildError(f"AGS metadata has invalid protection bits for {host_name}")
        comment = raw[_COMMENT].split(b"\0", 1)[0].decode("iso-8859-1")
        key = host_name.casefold()
        if key in records:
            raise BuildError(f"AGS metadata has duplicate records for {host_name}")
        records[key] = _Record(amiga_name, host_name, protection_bits, comment)
    return records


def iter_staged_entries(
    root: Path,
    cancel_check: Callable[[], bool] | None = None,
    *,
    allow_stale_metadata: bool = False,
) -> Iterator[AGSEntry]:
    from emu68hatcher.builder.ags_source import AGSEntry

    if not root.is_dir() or root.is_symlink():
        raise BuildError(f"AGS staging root is not a directory: {root}")
    stack = [(root, "")]
    count = 0
    while stack:
        parent, prefix = stack.pop()
        records = _records_by_host(parent)
        seen_host: set[str] = set()
        seen_amiga: set[str] = set()
        directories = []
        try:
            with os.scandir(parent) as children:
                for child in children:
                    if count % 4096 == 0 and cancel_check and cancel_check():
                        raise BuildCancelledError("Build was cancelled by user")
                    count += 1
                    if child.name.casefold() == _DB_NAME.casefold():
                        if child.name != _DB_NAME or child.is_symlink():
                            raise BuildError(
                                f"AGS staging has an invalid metadata file: {child.path}"
                            )
                        continue
                    host_name = _check_name(child.name, parent)
                    host_key = host_name.casefold()
                    if host_key in seen_host:
                        raise BuildError(f"AGS staging has colliding host names: {child.path}")
                    seen_host.add(host_key)
                    record = records.pop(host_key, None)
                    if record and record.host_name != host_name:
                        raise BuildError(f"AGS metadata host name differs in case: {child.path}")
                    amiga_name = record.amiga_name if record else host_name
                    amiga_key = amiga_name.casefold()
                    if amiga_key in seen_amiga:
                        raise BuildError(f"AGS staging has colliding Amiga names: {child.path}")
                    seen_amiga.add(amiga_key)
                    info = child.stat(follow_symlinks=False)
                    is_dir = stat.S_ISDIR(info.st_mode)
                    if not is_dir and not stat.S_ISREG(info.st_mode):
                        raise BuildError(f"AGS staging has a non-file entry: {child.path}")
                    relative = f"{prefix}/{amiga_name}" if prefix else amiga_name
                    yield AGSEntry(
                        relative,
                        0 if is_dir else info.st_size,
                        is_dir,
                        record.protection_bits if record else 0,
                        record.comment if record else "",
                    )
                    if is_dir:
                        directories.append((Path(child.path), relative))
        except OSError as exc:
            raise BuildError(f"Cannot scan AGS staging in {parent}: {exc}") from exc
        if records and not allow_stale_metadata:
            missing = next(iter(records.values())).host_name
            raise BuildError(f"AGS metadata refers to a missing entry in {parent}: {missing}")
        stack.extend(reversed(directories))


def _name_bytes(name: str, *, host: bool = False) -> bytes:
    try:
        encoded = name.encode("utf-8" if host else "iso-8859-1")
    except UnicodeEncodeError as exc:
        raise BuildError(f"AGS metadata name cannot be encoded: {name}") from exc
    if not encoded or len(encoded) > 255 or b"\0" in encoded:
        raise BuildError(f"AGS metadata name is invalid: {name}")
    return encoded + bytes(256 - len(encoded))


def _read_records(parent: Path) -> list[bytearray]:
    path = parent / _DB_NAME
    try:
        info = path.lstat()
    except FileNotFoundError:
        return []
    if not stat.S_ISREG(info.st_mode):
        raise BuildError(f"AGS metadata is not a regular file in {parent}")
    data = path.read_bytes()
    if len(data) % _RECORD_SIZE:
        raise BuildError(f"AGS metadata is damaged in {parent}")
    return [bytearray(data[i : i + _RECORD_SIZE]) for i in range(0, len(data), _RECORD_SIZE)]


def _record_name(record: bytearray, field: slice) -> bytes:
    return bytes(record[field]).split(b"\0", 1)[0]


def _find_record(records: list[bytearray], name: str) -> bytearray | None:
    encoded = _name_bytes(name, host=True).split(b"\0", 1)[0]
    matches = [
        record
        for record in records
        if record[0] == 1 and _record_name(record, _HOST_NAME) == encoded
    ]
    if len(matches) > 1:
        raise BuildError(f"AGS metadata has {len(matches)} records for {name}")
    return matches[0] if matches else None


def _write_records(parent: Path, records: list[bytearray]) -> None:
    target = parent / _DB_NAME
    descriptor, temp_name = tempfile.mkstemp(prefix="._uaefsdb-", dir=parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            for record in records:
                stream.write(record)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def rename_uaefsdb_entry(parent: Path, old_name: str, new_name: str) -> None:
    records = _read_records(parent)
    record = _find_record(records, old_name)
    if record is None:
        return
    encoded = _name_bytes(new_name, host=True).split(b"\0", 1)[0]
    if any(
        other[0] == 1 and _record_name(other, _HOST_NAME).lower() == encoded.lower()
        for other in records
        if other is not record
    ):
        raise BuildError(f"AGS metadata already has an entry for {new_name}")
    record[_AMIGA_NAME] = _name_bytes(new_name)
    record[_HOST_NAME] = _name_bytes(new_name, host=True)
    _write_records(parent, records)


def copy_uaefsdb_entry(source_file: Path, dest_file: Path) -> None:
    source_records = _read_records(source_file.parent)
    source_record = _find_record(source_records, source_file.name)
    target = dest_file.parent / _DB_NAME
    records = _read_records(dest_file.parent) if target.exists() else []
    old = _find_record(records, dest_file.name)
    if source_record is None and old is None:
        return
    if old is not None:
        records.remove(old)
    if source_record is not None:
        record = bytearray(source_record)
        if dest_file.name != source_file.name:
            record[_AMIGA_NAME] = _name_bytes(dest_file.name)
        record[_HOST_NAME] = _name_bytes(dest_file.name, host=True)
        records.append(record)
    _write_records(dest_file.parent, records)


@dataclass(frozen=True, slots=True)
class _StagedChild:
    path: Path
    amiga_name: str
    is_dir: bool
    record: _Record | None

    @property
    def metadata(self) -> tuple[int, str]:
        if self.record is None:
            return 0, ""
        return self.record.protection_bits, self.record.comment


def _staged_children(
    parent: Path, cancel_check: Callable[[], bool] | None
) -> tuple[_StagedChild, ...]:
    records = _records_by_host(parent)
    result = []
    seen_host: set[str] = set()
    with os.scandir(parent) as children:
        for child in children:
            if cancel_check and cancel_check():
                raise BuildCancelledError("Build was cancelled by user")
            if child.name.casefold() == _DB_NAME.casefold():
                if child.name != _DB_NAME or child.is_symlink():
                    raise BuildError(f"Invalid staging metadata file: {child.path}")
                continue
            name = _check_name(child.name, parent)
            key = name.casefold()
            if key in seen_host:
                raise BuildError(f"Staging has colliding host names in {parent}: {name}")
            seen_host.add(key)
            info = child.stat(follow_symlinks=False)
            is_dir = stat.S_ISDIR(info.st_mode)
            if not is_dir and not stat.S_ISREG(info.st_mode):
                raise BuildError(f"Staging has a symbolic link or special file: {child.path}")
            record = records.get(key)
            result.append(
                _StagedChild(
                    Path(child.path), record.amiga_name if record else name, is_dir, record
                )
            )
    return tuple(result)


def _same_file(left: Path, right: Path, cancel_check: Callable[[], bool] | None) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as left_stream, right.open("rb") as right_stream:
        while True:
            if cancel_check and cancel_check():
                raise BuildCancelledError("Build was cancelled by user")
            block = left_stream.read(1024 * 1024)
            if block != right_stream.read(1024 * 1024):
                return False
            if not block:
                return True


def _logical_groups(children: tuple[_StagedChild, ...]) -> dict[str, list[_StagedChild]]:
    groups: dict[str, list[_StagedChild]] = {}
    for child in children:
        groups.setdefault(child.amiga_name.casefold(), []).append(child)
    return groups


def _check_merge_group(group: list[_StagedChild], cancel_check: Callable[[], bool] | None) -> None:
    first = group[0]
    for other in group[1:]:
        if first.is_dir != other.is_dir:
            raise BuildError(f"Staging has a file/directory collision at {other.path}")
        if first.metadata != other.metadata:
            raise BuildError(f"Staging has conflicting Amiga metadata at {other.path}")
        if not first.is_dir and not _same_file(first.path, other.path, cancel_check):
            raise BuildError(f"Staging has different file contents at {other.path}")
    if first.is_dir:
        children = tuple(
            child for directory in group for child in _staged_children(directory.path, cancel_check)
        )
        for subgroup in _logical_groups(children).values():
            if len(subgroup) > 1:
                _check_merge_group(subgroup, cancel_check)


def _preflight_directory_merges(root: Path, cancel_check: Callable[[], bool] | None) -> None:
    stack = [root]
    while stack:
        if cancel_check and cancel_check():
            raise BuildCancelledError("Build was cancelled by user")
        parent = stack.pop()
        children = _staged_children(parent, cancel_check)
        for group in _logical_groups(children).values():
            if len(group) > 1:
                if not all(child.is_dir for child in group):
                    raise BuildError(f"Staging has duplicate Amiga files in {parent}")
                _check_merge_group(group, cancel_check)
        stack.extend(child.path for child in children if child.is_dir)


def _preferred_directory(group: list[_StagedChild]) -> _StagedChild:
    return min(
        group,
        key=lambda child: (
            child.path.name.casefold() != child.amiga_name.casefold(),
            child.path.name.casefold(),
            child.path.name,
        ),
    )


def _raw_record(parent: Path, name: str) -> bytearray | None:
    return _find_record(_read_records(parent), name)


def _set_record_host(record: bytearray, host_name: str) -> bytearray:
    result = bytearray(record)
    result[_HOST_NAME] = _name_bytes(host_name, host=True)
    return result


def _move_record(
    source_parent: Path, source_name: str, target_parent: Path, target_name: str
) -> None:
    source = _raw_record(source_parent, source_name)
    records = _read_records(target_parent)
    old = _find_record(records, target_name)
    if old is not None:
        records.remove(old)
    if source is not None:
        records.append(_set_record_host(source, target_name))
    if source is not None or old is not None:
        _write_records(target_parent, records)


def _rename_record_host(parent: Path, old_name: str, new_name: str) -> None:
    records = _read_records(parent)
    record = _find_record(records, old_name)
    if record is None:
        return
    if _find_record(records, new_name) is not None:
        raise BuildError(f"Staging metadata already names {new_name} in {parent}")
    record[_HOST_NAME] = _name_bytes(new_name, host=True)
    _write_records(parent, records)


def _retain_directory_record(parent: Path, canonical: _StagedChild, alias: _StagedChild) -> None:
    records = _read_records(parent)
    kept = _find_record(records, canonical.path.name)
    removed = _find_record(records, alias.path.name)
    if kept is None and removed is not None:
        records.append(_set_record_host(removed, canonical.path.name))
    if removed is not None:
        records.remove(removed)
    if kept is None and removed is None:
        return
    _write_records(parent, records)


def _normalize_record_host_case(parent: Path, children: tuple[_StagedChild, ...]) -> None:
    mismatched = {
        child.path.name.casefold(): child.path.name
        for child in children
        if child.record is not None and child.record.host_name != child.path.name
    }
    if not mismatched:
        return
    records = _read_records(parent)
    for record in records:
        if record[0] != 1:
            continue
        host = _record_name(record, _HOST_NAME).decode("utf-8")
        actual = mismatched.get(host.casefold())
        if actual is not None:
            record[_HOST_NAME] = _name_bytes(actual, host=True)
    _write_records(parent, records)


def _merge_directory(source: Path, target: Path, cancel_check: Callable[[], bool] | None) -> None:
    target_groups = _logical_groups(_staged_children(target, cancel_check))
    for child in _staged_children(source, cancel_check):
        if cancel_check and cancel_check():
            raise BuildCancelledError("Build was cancelled by user")
        matches = target_groups.get(child.amiga_name.casefold(), [])
        if not matches:
            destination = target / child.path.name
            if destination.exists():
                raise BuildError(f"Staging merge would overwrite {destination}")
            _move_record(source, child.path.name, target, destination.name)
            child.path.rename(destination)
            target_groups[child.amiga_name.casefold()] = [
                _StagedChild(destination, child.amiga_name, child.is_dir, child.record)
            ]
            continue
        existing = _preferred_directory(matches) if child.is_dir else matches[0]
        if child.is_dir:
            if (
                child.path.name.casefold() == child.amiga_name.casefold()
                and existing.path.name.casefold() != existing.amiga_name.casefold()
            ):
                renamed = target / child.path.name
                if renamed.exists():
                    raise BuildError(f"Staging merge would overwrite {renamed}")
                _rename_record_host(target, existing.path.name, renamed.name)
                existing.path.rename(renamed)
                existing = _StagedChild(renamed, existing.amiga_name, True, existing.record)
                target_groups[child.amiga_name.casefold()] = [existing]
            _merge_directory(child.path, existing.path, cancel_check)
        else:
            if not _same_file(child.path, existing.path, cancel_check):
                raise BuildError(f"Staging file changed during merge: {child.path}")
            child.path.unlink()
    database = source / _DB_NAME
    if database.exists():
        database.unlink()
    source.rmdir()


def _coalesce_directory(root: Path, cancel_check: Callable[[], bool] | None) -> None:
    children = _staged_children(root, cancel_check)
    _normalize_record_host_case(root, children)
    for group in _logical_groups(children).values():
        if len(group) <= 1:
            continue
        canonical = _preferred_directory(group)
        for alias in group:
            if alias.path == canonical.path:
                continue
            if cancel_check and cancel_check():
                raise BuildCancelledError("Build was cancelled by user")
            _retain_directory_record(root, canonical, alias)
            _merge_directory(alias.path, canonical.path, cancel_check)
    for child in _staged_children(root, cancel_check):
        if child.is_dir:
            _coalesce_directory(child.path, cancel_check)


def coalesce_staged_directories(root: Path, cancel_check: Callable[[], bool] | None = None) -> None:
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise BuildError(f"Staging root is not a directory: {root}")
    _preflight_directory_merges(root, cancel_check)
    _coalesce_directory(root, cancel_check)
