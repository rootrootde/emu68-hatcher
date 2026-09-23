"""UAE filesystem database records for copied Amiga files."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from emu68hatcher.builder.errors import BuildError

_DB_NAME = "_UAEFSDB.___"
_RECORD_SIZE = 600
_AMIGA_NAME = slice(5, 261)
_HOST_NAME = slice(262, 518)


def _name_bytes(name: str) -> bytes:
    try:
        encoded = name.encode("iso-8859-1")
    except UnicodeEncodeError as exc:
        raise BuildError(f"AGS metadata name cannot be encoded: {name}") from exc
    if not encoded or len(encoded) > 255 or b"\0" in encoded:
        raise BuildError(f"AGS metadata name is invalid: {name}")
    return encoded + bytes(256 - len(encoded))


def _read_records(parent: Path) -> list[bytearray]:
    path = parent / _DB_NAME
    if not path.is_file():
        raise BuildError(f"AGS metadata is missing in {parent}")
    data = path.read_bytes()
    if len(data) % _RECORD_SIZE:
        raise BuildError(f"AGS metadata is damaged in {parent}")
    return [bytearray(data[i : i + _RECORD_SIZE]) for i in range(0, len(data), _RECORD_SIZE)]


def _record_name(record: bytearray, field: slice) -> bytes:
    return bytes(record[field]).split(b"\0", 1)[0]


def _find_record(records: list[bytearray], name: str) -> bytearray:
    encoded = _name_bytes(name).split(b"\0", 1)[0]
    matches = [record for record in records if _record_name(record, _HOST_NAME) == encoded]
    if len(matches) != 1:
        raise BuildError(f"AGS metadata has {len(matches)} records for {name}")
    return matches[0]


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
    if any(
        _record_name(other, _HOST_NAME).lower() == new_name.lower().encode("iso-8859-1")
        for other in records
        if other is not record
    ):
        raise BuildError(f"AGS metadata already has an entry for {new_name}")
    record[_AMIGA_NAME] = _name_bytes(new_name)
    record[_HOST_NAME] = _name_bytes(new_name)
    _write_records(parent, records)


def copy_uaefsdb_entry(source_file: Path, dest_file: Path) -> None:
    source_records = _read_records(source_file.parent)
    record = bytearray(_find_record(source_records, source_file.name))
    dest_name = _name_bytes(dest_file.name)
    record[_AMIGA_NAME] = dest_name
    record[_HOST_NAME] = dest_name
    target = dest_file.parent / _DB_NAME
    records = _read_records(dest_file.parent) if target.exists() else []
    old = [
        entry
        for entry in records
        if _record_name(entry, _HOST_NAME) == dest_file.name.encode("iso-8859-1")
    ]
    if len(old) > 1:
        raise BuildError(f"AGS metadata has duplicate records for {dest_file.name}")
    if old:
        records[records.index(old[0])] = record
    else:
        records.append(record)
    _write_records(dest_file.parent, records)
