from pathlib import Path
from urllib.parse import unquote

import pytest
from emu68hatcher.builder.ags_metadata import copy_uaefsdb_entry, iter_staged_entries
from emu68hatcher.builder.errors import BuildCancelledError, BuildError
from emu68hatcher.builder.staging.hst_metadata import _encoded_name, prepare_workbench_metadata


def write_record(amiga: str, host: str, bits: int, comment: str = "") -> bytes:
    raw = bytearray(600)
    raw[0] = 1
    raw[1:5] = bits.to_bytes(4, "big")
    for offset, value in (
        (5, amiga.encode("latin-1")),
        (262, host.encode("utf-8")),
        (519, comment.encode("latin-1")),
    ):
        raw[offset : offset + len(value)] = value
    return bytes(raw)


@pytest.mark.parametrize(
    "name", ["#?+-\\", "CON", "NUL.txt", "50%", "name.uaem", "trailing.", "ä:"]
)
def test_transfer_names_decode_exactly(name: str):
    encoded = _encoded_name(name)
    assert unquote(encoded, encoding="latin-1") == name
    assert not any(char in encoded for char in '\\/:*?"<>|')
    assert not encoded.lower().endswith(".uaem")


def test_transfer_keeps_later_flags_comments_and_directory_mapping(tmp_path: Path):
    (tmp_path / "first").write_bytes(b"one")
    (tmp_path / "second").write_bytes(b"two")
    nested = tmp_path / "__uae_dir"
    nested.mkdir()
    (nested / "__uae_file").write_bytes(b"odd")
    (tmp_path / "_UAEFSDB.___").write_bytes(
        write_record("first", "first", 2)
        + write_record("second", "second", 66, "für später")
        + write_record("D:ä", "__uae_dir", 64)
    )
    (nested / "_UAEFSDB.___").write_bytes(write_record("#?+-\\", "__uae_file", 2))
    assert len(list(iter_staged_entries(tmp_path))) == 4

    prepare_workbench_metadata(tmp_path)

    assert (tmp_path / "first.uaem").read_bytes().startswith(b"----rw-d ")
    assert (tmp_path / "second.uaem").read_bytes().startswith(b"-s--rw-d ")
    assert (tmp_path / "second.uaem").read_bytes().endswith("für später\n".encode("latin-1"))
    assert (tmp_path / "D%3a%e4" / "%23%3f+-%5c").read_bytes() == b"odd"
    assert not list(tmp_path.rglob("_UAEFSDB.___"))


def test_transfer_cancel_does_not_modify_staging(tmp_path: Path):
    (tmp_path / "source").write_bytes(b"original")
    with pytest.raises(BuildCancelledError):
        prepare_workbench_metadata(tmp_path, lambda: True)
    assert list(tmp_path.iterdir()) == [tmp_path / "source"]


def test_transfer_rejects_unicode_before_renaming_any_files(tmp_path: Path):
    (tmp_path / "50%").write_bytes(b"original")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "game\U0001f3ae").write_bytes(b"game")
    with pytest.raises(BuildError, match="Amiga character set"):
        prepare_workbench_metadata(tmp_path)
    assert (tmp_path / "50%").read_bytes() == b"original"
    assert (nested / "game\U0001f3ae").read_bytes() == b"game"
    assert not list(tmp_path.rglob("*.uaem"))


def test_transport_host_name_does_not_alias_another_amiga_name(tmp_path: Path):
    (tmp_path / "question").write_bytes(b"question")
    (tmp_path / "%3f.bsh").write_bytes(b"literal")
    (tmp_path / "_UAEFSDB.___").write_bytes(write_record("?.bsh", "question", 2))
    prepare_workbench_metadata(tmp_path)
    assert (tmp_path / "%3f%2ebsh").read_bytes() == b"question"
    assert (tmp_path / "%253f.bsh").read_bytes() == b"literal"
    names = {item.name for item in tmp_path.iterdir() if not item.name.endswith(".uaem")}
    assert "%3f.bsh" not in names
    assert {unquote(name, encoding="latin-1") for name in names} == {"?.bsh", "%3f.bsh"}


def test_transfer_ignores_only_obsolete_records_after_workbench_extraction(tmp_path: Path):
    (tmp_path / "Asl").write_bytes(b"decompressed")
    (tmp_path / "_UAEFSDB.___").write_bytes(write_record("Asl.Z", "Asl.Z", 2))
    prepare_workbench_metadata(tmp_path, allow_stale_metadata=True)
    assert (tmp_path / "Asl").read_bytes() == b"decompressed"
    assert not (tmp_path / "Asl.Z.uaem").exists()


def test_boot_transfer_merges_mapped_catalog_directories(tmp_path: Path):
    mapped = tmp_path / "__uae_francais"
    literal = tmp_path / "Français"
    mapped.mkdir()
    literal.mkdir()
    (mapped / "sys.catalog").write_bytes(b"system")
    (literal / "vnc.catalog").write_bytes(b"vnc")
    (tmp_path / "_UAEFSDB.___").write_bytes(write_record("français", mapped.name, 0))

    prepare_workbench_metadata(tmp_path, allow_stale_metadata=True)

    directories = [item for item in tmp_path.iterdir() if item.is_dir()]
    assert len(directories) == 1
    assert (directories[0] / "sys.catalog").read_bytes() == b"system"
    assert (directories[0] / "vnc.catalog").read_bytes() == b"vnc"
    assert unquote(directories[0].name, encoding="latin-1").casefold() == "français"


def test_copy_metadata_keeps_utf8_host_name_and_mapped_amiga_name(tmp_path: Path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    for parent in (source, target):
        (parent / "café").write_bytes(b"file")
    (source / "_UAEFSDB.___").write_bytes(write_record("Amigaä", "café", 66, "comment"))
    copy_uaefsdb_entry(source / "café", target / "café")
    (entry,) = tuple(iter_staged_entries(target))
    assert (entry.path, entry.protection_bits, entry.comment) == ("Amigaä", 66, "comment")
