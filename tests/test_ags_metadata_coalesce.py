from pathlib import Path

import pytest
from emu68hatcher.builder.ags_metadata import (
    coalesce_staged_directories,
    iter_staged_entries,
)
from emu68hatcher.builder.errors import BuildCancelledError, BuildError


def _record(amiga: str, host: str, bits: int = 0, comment: str = "") -> bytes:
    raw = bytearray(600)
    raw[0] = 1
    raw[1:5] = bits.to_bytes(4, "big")
    for offset, data in (
        (5, amiga.encode("iso-8859-1")),
        (262, host.encode("utf-8")),
        (519, comment.encode("iso-8859-1")),
    ):
        raw[offset : offset + len(data)] = data
    return bytes(raw)


def _french_pair(root: Path) -> tuple[Path, Path]:
    catalog = root / "Locale" / "Catalogs"
    catalog.mkdir(parents=True)
    alias = catalog / "__uae___fran_ais"
    literal = catalog / "Français"
    alias.mkdir()
    literal.mkdir()
    (catalog / "_UAEFSDB.___").write_bytes(_record("français", alias.name))
    return alias, literal


def test_merge_disjoint_boot_directories_keeps_contents_and_metadata(tmp_path: Path):
    alias, literal = _french_pair(tmp_path)
    (alias / "Sys").mkdir()
    (alias / "Sys" / "sys.catalog").write_bytes(b"system")
    (alias / "Sys" / "_UAEFSDB.___").write_bytes(_record("sys.catalog", "sys.catalog", 2, "core"))
    (literal / "VNC").mkdir()
    (literal / "VNC" / "vnc.catalog").write_bytes(b"vnc")
    (literal / "VNC" / "_UAEFSDB.___").write_bytes(_record("vnc.catalog", "vnc.catalog", 66, "app"))
    (alias / "_UAEFSDB.___").write_bytes(_record("Sys", "Sys", 0))

    coalesce_staged_directories(tmp_path)

    assert not alias.exists()
    assert (literal / "Sys" / "sys.catalog").read_bytes() == b"system"
    assert (literal / "VNC" / "vnc.catalog").read_bytes() == b"vnc"
    entries = {
        entry.path: entry for entry in iter_staged_entries(tmp_path, allow_stale_metadata=True)
    }
    assert entries["Locale/Catalogs/français/Sys/sys.catalog"].protection_bits == 2
    assert entries["Locale/Catalogs/français/Sys/sys.catalog"].comment == "core"
    assert entries["Locale/Catalogs/français/VNC/vnc.catalog"].protection_bits == 66
    assert entries["Locale/Catalogs/français/VNC/vnc.catalog"].comment == "app"


def test_nested_merge_preserves_literal_directory_name(tmp_path: Path):
    alias, literal = _french_pair(tmp_path)
    encoded = literal / "__uae___sys"
    encoded.mkdir()
    (literal / "_UAEFSDB.___").write_bytes(_record("Sys", encoded.name))
    (encoded / "one").write_bytes(b"one")
    source = alias / "Sys"
    source.mkdir()
    (source / "two").write_bytes(b"two")

    coalesce_staged_directories(tmp_path)

    assert not encoded.exists()
    assert (literal / "Sys" / "one").read_bytes() == b"one"
    assert (literal / "Sys" / "two").read_bytes() == b"two"
    assert (
        len([entry for entry in iter_staged_entries(tmp_path) if entry.path.endswith("/Sys")]) == 1
    )


@pytest.mark.parametrize("conflict", ["contents", "metadata"])
def test_conflicts_fail_before_mutation(tmp_path: Path, conflict: str):
    alias, literal = _french_pair(tmp_path)
    (alias / "same").write_bytes(b"left")
    (literal / "same").write_bytes(b"right" if conflict == "contents" else b"left")
    if conflict == "metadata":
        (alias / "_UAEFSDB.___").write_bytes(_record("same", "same", 2))

    with pytest.raises(BuildError, match="different file contents|conflicting Amiga metadata"):
        coalesce_staged_directories(tmp_path)

    assert alias.exists() and literal.exists()
    assert (alias / "same").read_bytes() == b"left"


def test_symlink_and_cancel_leave_staging_unchanged(tmp_path: Path):
    alias, literal = _french_pair(tmp_path)
    (alias / "link").symlink_to(literal)
    with pytest.raises(BuildError, match="symbolic link"):
        coalesce_staged_directories(tmp_path)
    (alias / "link").unlink()
    with pytest.raises(BuildCancelledError):
        coalesce_staged_directories(tmp_path, lambda: True)
    assert alias.exists() and literal.exists()


def test_boot_overlay_case_change_updates_metadata_host_only(tmp_path: Path):
    startup = tmp_path / "Startup-Sequence"
    startup.write_bytes(b"boot")
    (tmp_path / "_UAEFSDB.___").write_bytes(_record("Startup-sequence", "Startup-sequence", 66))

    coalesce_staged_directories(tmp_path)

    entries = list(iter_staged_entries(tmp_path))
    assert len(entries) == 1
    assert entries[0].path == "Startup-sequence"
    assert entries[0].protection_bits == 66
