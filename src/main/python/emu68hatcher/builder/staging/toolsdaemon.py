"""Apply ToolsDaemon binary patches."""

from __future__ import annotations

from pathlib import Path

from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.staging.files import resolve_source_path

_PATCH_TARGETS = (
    ("WBStartup/ToolsDaemon", "ToolsDaemon.pch"),
    ("L/ToolsDaemon-handler", "ToolsDaemon-handler.pch"),
    ("Prefs/ToolsPrefs", "ToolsPrefs.pch"),
)


def patch_toolsdaemon(
    boot_staging: Path,
    extracted_paths: dict[str, Path],
) -> tuple[str, ...]:
    """Patch the three installed ToolsDaemon 2.1a files to 2.2."""
    patch_root = extracted_paths.get("toolsdaemon_patch")
    if patch_root is None or not patch_root.is_dir():
        raise BuildError("ToolsDaemon 2.2 patch files are missing. Check the package download.")

    patched: list[tuple[Path, bytes]] = []
    for relative_target, patch_name in _PATCH_TARGETS:
        target = resolve_source_path(boot_staging, relative_target)
        if target is None or not target.is_file():
            raise BuildError(f"ToolsDaemon base file is missing: SYS:{relative_target}")
        patch_file = _find_patch_file(patch_root, patch_name)
        patched.append((target, _apply_patch(target.read_bytes(), patch_file.read_bytes())))

    for target, data in patched:
        target.write_bytes(data)

    return tuple(relative_target for relative_target, _patch_name in _PATCH_TARGETS)


def _find_patch_file(root: Path, name: str) -> Path:
    matches = [path for path in root.rglob(name) if path.is_file()]
    if len(matches) != 1:
        raise BuildError(f"ToolsDaemon patch archive has no unique {name} file")
    return matches[0]


def _apply_patch(original: bytes, patch_data: bytes) -> bytes:
    chunks = _read_patch_chunks(patch_data)
    input_sum, input_length = _file_metadata(chunks, b"INPF")
    output_sum, output_length = _file_metadata(chunks, b"OUTF")

    if len(original) != input_length or _checksum(original) != input_sum:
        raise BuildError("ToolsDaemon 2.2 patch does not match the installed 2.1a file")

    sequence = chunks.get(b"PSEQ")
    if sequence is None:
        raise BuildError("ToolsDaemon patch has no PSEQ data")
    output = _decode_sequence(original, sequence)

    if len(output) != output_length or _checksum(output) != output_sum:
        raise BuildError("ToolsDaemon 2.2 patch produced an invalid output file")
    return output


def _read_patch_chunks(data: bytes) -> dict[bytes, bytes]:
    if len(data) < 12 or data[:4] != b"FORM" or data[8:12] != b"PTCH":
        raise BuildError("ToolsDaemon patch is not an IFF PTCH file")

    form_end = 8 + int.from_bytes(data[4:8], "big")
    if form_end > len(data):
        raise BuildError("ToolsDaemon patch is truncated")

    chunks: dict[bytes, bytes] = {}
    offset = 12
    while offset < form_end:
        if offset + 8 > form_end:
            raise BuildError("ToolsDaemon patch has a truncated chunk header")
        chunk_id = data[offset : offset + 4]
        chunk_size = int.from_bytes(data[offset + 4 : offset + 8], "big")
        chunk_start = offset + 8
        chunk_end = chunk_start + chunk_size
        if chunk_end > form_end:
            raise BuildError("ToolsDaemon patch has a truncated chunk")
        if chunk_id in chunks:
            raise BuildError(f"ToolsDaemon patch repeats the {chunk_id.decode('ascii')} chunk")
        chunks[chunk_id] = data[chunk_start:chunk_end]
        offset = chunk_end + (chunk_size & 1)
    return chunks


def _file_metadata(chunks: dict[bytes, bytes], chunk_id: bytes) -> tuple[int, int]:
    chunk = chunks.get(chunk_id)
    if chunk is None or len(chunk) < 8:
        raise BuildError(f"ToolsDaemon patch has no valid {chunk_id.decode('ascii')} chunk")
    return int.from_bytes(chunk[:4], "big"), int.from_bytes(chunk[4:8], "big")


def _checksum(data: bytes) -> int:
    return sum(data) & 0xFFFFFFFF


def _decode_sequence(original: bytes, sequence: bytes) -> bytes:
    source_offset = 0
    patch_offset = 0
    output = bytearray()

    def source_bytes(length: int) -> bytes:
        nonlocal source_offset
        end = source_offset + length
        if end > len(original):
            raise BuildError("ToolsDaemon patch reads beyond the input file")
        value = original[source_offset:end]
        source_offset = end
        return value

    def patch_bytes(length: int) -> bytes:
        nonlocal patch_offset
        end = patch_offset + length
        if end > len(sequence):
            raise BuildError("ToolsDaemon patch has incomplete PSEQ data")
        value = sequence[patch_offset:end]
        patch_offset = end
        return value

    while patch_offset < len(sequence):
        opcode = sequence[patch_offset]
        patch_offset += 1
        if opcode == 0:
            break

        if opcode in (0x49, 0x52, 0x53, 0x55):
            length = int.from_bytes(patch_bytes(2), "big")
            if opcode == 0x49:
                output.extend(patch_bytes(length))
            elif opcode == 0x52:
                source_bytes(length)
                output.extend(patch_bytes(length))
            elif opcode == 0x53:
                source_bytes(length)
            else:
                output.extend(source_bytes(length))
            continue

        if opcode in (0x69, 0x72, 0x73, 0x75):
            length = patch_bytes(1)[0]
            if opcode == 0x69:
                output.extend(patch_bytes(length))
            elif opcode == 0x72:
                source_bytes(length)
                output.extend(patch_bytes(length))
            elif opcode == 0x73:
                source_bytes(length)
            else:
                output.extend(source_bytes(length))
            continue

        if opcode > 0xC0:
            length = opcode - 0xC0
            source_bytes(length)
            output.extend(patch_bytes(length))
        elif opcode >= 0xA0:
            old_value = source_bytes(1)[0]
            output.append((old_value + opcode - 0xB0) & 0xFF)
        elif opcode > 0x80:
            output.extend(source_bytes(opcode - 0x80))
        else:
            raise BuildError(f"ToolsDaemon patch uses unknown PSEQ opcode 0x{opcode:02x}")

    return bytes(output)
