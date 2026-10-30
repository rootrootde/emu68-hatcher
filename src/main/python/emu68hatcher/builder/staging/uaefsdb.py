"""UAE filesystem database records for copied Amiga files."""

from __future__ import annotations

import os
import stat
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.staging.files import resolve_staging_path

DB_NAME = "_UAEFSDB.___"
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
        raise BuildError(f"Amiga file metadata has an unsafe name in {directory}: {name!r}")
    return name


_REPLACEMENTS = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "−": "-",
        "€": "EUR",
    }
)


def _plain_letter(char: str) -> str:
    # accents the Amiga character set lacks are dropped, ligatures split
    kept = "".join(
        c
        for c in unicodedata.normalize("NFKD", char)
        if ord(c) <= 255 and c not in "/:\0" and unicodedata.category(c) != "Mn"
    )
    return kept or "_"


def amiga_name_for_host(name: str) -> str:
    """Amiga name for a staged entry that has no UaeFsDb record."""
    if any("\udc80" <= c <= "\udcff" for c in name):
        # undecodable bytes: Amiga archives write Latin-1 names
        name = os.fsencode(name).decode("iso-8859-1")
    name = unicodedata.normalize("NFC", name)
    if all(ord(c) <= 255 for c in name):
        return name
    name = name.translate(_REPLACEMENTS)
    return "".join(c if ord(c) <= 255 else _plain_letter(c) for c in name)


def _field_name(record: bytes, field: slice, directory: Path) -> str:
    try:
        value = (
            record[field]
            .split(b"\0", 1)[0]
            .decode("utf-8" if field == _HOST_NAME else "iso-8859-1")
        )
    except UnicodeDecodeError as exc:
        raise BuildError(f"Amiga file metadata has an invalid host name in {directory}") from exc
    return _check_name(value, directory)


def records_by_host(parent: Path) -> dict[str, _Record]:
    path = parent / DB_NAME
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {}
    if not stat.S_ISREG(info.st_mode):
        raise BuildError(f"Amiga file metadata is not a regular file in {parent}")
    data = path.read_bytes()
    if len(data) % _RECORD_SIZE:
        raise BuildError(f"Amiga file metadata is damaged in {parent}")
    records: dict[str, _Record] = {}
    for offset in range(0, len(data), _RECORD_SIZE):
        raw = data[offset : offset + _RECORD_SIZE]
        if raw[0] == 0:
            continue
        if raw[0] != 1:
            raise BuildError(f"Amiga file metadata has an invalid record in {parent}")
        amiga_name = _field_name(raw, _AMIGA_NAME, parent)
        host_name = _field_name(raw, _HOST_NAME, parent)
        protection_bits = int.from_bytes(raw[1:5], "big")
        if protection_bits > 255:
            raise BuildError(f"Amiga file metadata has invalid protection bits for {host_name}")
        comment = raw[_COMMENT].split(b"\0", 1)[0].decode("iso-8859-1")
        key = host_name.casefold()
        if key in records:
            raise BuildError(f"Amiga file metadata has duplicate records in {parent}: {host_name}")
        records[key] = _Record(amiga_name, host_name, protection_bits, comment)
    return records


def _name_bytes(name: str, *, host: bool = False) -> bytes:
    try:
        encoded = name.encode("utf-8" if host else "iso-8859-1")
    except UnicodeEncodeError as exc:
        raise BuildError(f"Amiga file metadata name cannot be encoded: {name}") from exc
    if not encoded or len(encoded) > 255 or b"\0" in encoded:
        raise BuildError(f"Amiga file metadata name is invalid: {name}")
    return encoded + bytes(256 - len(encoded))


def _read_records(parent: Path) -> list[bytearray]:
    path = parent / DB_NAME
    try:
        info = path.lstat()
    except FileNotFoundError:
        return []
    if not stat.S_ISREG(info.st_mode):
        raise BuildError(f"Amiga file metadata is not a regular file in {parent}")
    data = path.read_bytes()
    if len(data) % _RECORD_SIZE:
        raise BuildError(f"Amiga file metadata is damaged in {parent}")
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
        raise BuildError(f"Amiga file metadata has {len(matches)} records for {name}")
    return matches[0] if matches else None


def _write_records(parent: Path, records: list[bytearray]) -> None:
    target = parent / DB_NAME
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


def drop_superseded_records(root: Path) -> None:
    # HST appends a new record when an overwrite differs only in case, the last one is current
    for database in root.rglob(DB_NAME):
        records = _read_records(database.parent)
        latest: dict[str, bytearray] = {}
        for record in records:
            if record[0] == 1:
                host = _record_name(record, _HOST_NAME).decode("utf-8", "replace")
                latest[host.casefold()] = record
        current = {id(record) for record in latest.values()}
        kept = [record for record in records if record[0] != 1 or id(record) in current]
        if len(kept) != len(records):
            _write_records(database.parent, kept)


def import_records(database: Path, target_parent: Path) -> None:
    """Merge a foreign UaeFsDb into the staging records of target_parent, present files only."""
    data = database.read_bytes()
    if len(data) % _RECORD_SIZE:
        raise BuildError(f"Amiga file metadata is damaged: {database}")
    imported: dict[str, bytearray] = {}
    for offset in range(0, len(data), _RECORD_SIZE):
        raw = bytearray(data[offset : offset + _RECORD_SIZE])
        if raw[0] == 0:
            continue
        if raw[0] != 1 or int.from_bytes(raw[1:5], "big") > 255:
            raise BuildError(f"Amiga file metadata has an invalid record: {database}")
        _field_name(raw, _AMIGA_NAME, database.parent)
        target = resolve_staging_path(target_parent, _field_name(raw, _HOST_NAME, database.parent))
        # stale records for deleted files are common in emulator folders
        if not target.exists():
            continue
        raw[_HOST_NAME] = _name_bytes(target.name, host=True)
        imported[target.name.casefold()] = raw
    if not imported:
        return
    kept = [
        record
        for record in _read_records(target_parent)
        if record[0] != 1
        or _record_name(record, _HOST_NAME).decode("utf-8", "replace").casefold() not in imported
    ]
    _write_records(target_parent, kept + list(imported.values()))
