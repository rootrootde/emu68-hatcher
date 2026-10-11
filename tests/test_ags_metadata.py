from pathlib import Path

import pytest
from emu68hatcher.builder.ags_metadata import (
    copy_uaefsdb_entry,
    iter_staged_entries,
    rename_uaefsdb_entry,
)
from emu68hatcher.builder.errors import BuildError


def _record(
    amiga_name: str, host_name: str, bits: int = 0, comment: str = "", active: int = 1
) -> bytes:
    raw = bytearray(600)
    raw[0] = active
    raw[1:5] = bits.to_bytes(4, "big")
    raw[5 : 5 + len(amiga_name)] = amiga_name.encode("iso-8859-1")
    raw[262 : 262 + len(host_name)] = host_name.encode("iso-8859-1")
    raw[519 : 519 + len(comment)] = comment.encode("iso-8859-1")
    return bytes(raw)


def _database(parent: Path, *records: bytes) -> None:
    (parent / "_UAEFSDB.___").write_bytes(b"".join(records))


def test_scan_reads_later_records_and_defaults(tmp_path: Path):
    (tmp_path / "A1200_Script").write_bytes(b"script")
    (tmp_path / "A1200_Script_NC").write_bytes(b"nc")
    (tmp_path / "ordinary").write_bytes(b"plain")
    (tmp_path / "_UAEFSDB.___.bak").write_bytes(b"backup")
    _database(
        tmp_path,
        _record("A1200_Script", "A1200_Script", 2),
        _record("A1200_Script_NC", "A1200_Script_NC", 2, "script comment"),
    )

    entries = {entry.path: entry for entry in iter_staged_entries(tmp_path)}

    assert set(entries) == {"A1200_Script", "A1200_Script_NC", "ordinary", "_UAEFSDB.___.bak"}
    assert entries["A1200_Script_NC"].size == 2
    assert entries["A1200_Script_NC"].protection_bits == 2
    assert entries["A1200_Script_NC"].comment == "script comment"
    assert entries["ordinary"].protection_bits == 0


def test_scan_maps_each_directory_and_ignores_inactive_records(tmp_path: Path):
    encoded = tmp_path / "HostDir"
    encoded.mkdir()
    (encoded / "__uae_odd").write_bytes(b"data")
    _database(
        tmp_path,
        _record("AmigaDir", "HostDir", 64),
        _record("unused", "missing", active=0),
    )
    _database(encoded, _record("#?+-\\", "__uae_odd", 66, "mapped"))

    entries = {entry.path: entry for entry in iter_staged_entries(tmp_path)}

    assert entries["AmigaDir"].is_dir
    assert entries["AmigaDir"].size == 0
    assert entries["AmigaDir"].protection_bits == 64
    assert entries["AmigaDir/#?+-\\"].protection_bits == 66
    assert entries["AmigaDir/#?+-\\"].comment == "mapped"


@pytest.mark.parametrize(
    "records, message",
    [
        ((_record("stale", "missing"),), "missing entry"),
        ((_record("one", "file"), _record("two", "file")), "duplicate records"),
        ((_record("../escape", "file"),), "unsafe name"),
        ((_record("file", "file", active=2),), "invalid record"),
    ],
)
def test_scan_rejects_bad_database(tmp_path: Path, records: tuple[bytes, ...], message: str):
    (tmp_path / "file").write_bytes(b"data")
    _database(tmp_path, *records)

    with pytest.raises(BuildError, match=message):
        list(iter_staged_entries(tmp_path))


def test_scan_rejects_symlink(tmp_path: Path):
    (tmp_path / "file").write_bytes(b"data")
    (tmp_path / "link").symlink_to(tmp_path / "file")

    with pytest.raises(BuildError, match="non-file entry"):
        list(iter_staged_entries(tmp_path))


def test_copy_and_rename_allow_ordinary_files(tmp_path: Path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "ordinary").write_bytes(b"data")
    (target / "ordinary").write_bytes(b"data")
    _database(source, _record("other", "other", 2))
    _database(target, _record("ordinary", "ordinary", 2))

    rename_uaefsdb_entry(source, "ordinary", "renamed")
    copy_uaefsdb_entry(source / "ordinary", target / "ordinary")

    (entry,) = list(iter_staged_entries(target))
    assert entry.path == "ordinary"
    assert entry.protection_bits == 0
