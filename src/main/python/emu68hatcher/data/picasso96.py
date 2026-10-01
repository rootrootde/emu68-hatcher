"""Read and write Picasso96 P96S settings."""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

SETTINGS_SOURCE = Path(__file__).parent / "reference" / "picasso96.json"
LEGACY_BOARD_TYPE = 14
BGRA_MODE_BITS = 0x303

# BOOL occupies a word in the file, despite the SDK's in-memory layout.
_LAYOUTS = {
    "STHD": (">IHh30s", "board_type local_ordering last_selected name"),
    "BDNM": (">32s", "name"),
    "MNTR": (">32sIIHHI", "name hsync_min hsync_max vsync_min vsync_max flags"),
    "RSHD": (">IHHhhH22s", "display_id width height active last_selected flags name"),
    "MIHD": (
        ">hhHHBBHHHHBBHHHHBBI",
        "open_count active width height depth flags hor_total hor_blank_size "
        "hor_sync_start hor_sync_size hor_sync_skew hor_enable_skew ver_total "
        "ver_blank_size ver_sync_start ver_sync_size clock clock_divide pixel_clock",
    ),
}


def decode_settings(data: bytes) -> dict:
    if len(data) < 12 or data[:4] != b"FORM" or data[8:12] != b"P96S":
        raise ValueError("Not an IFF P96S settings file")
    if int.from_bytes(data[4:8], "big") + 8 != len(data):
        raise ValueError("P96S FORM size does not match the file length")
    chunks = []
    offset = 12
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError("Truncated P96S chunk header")
        tag = data[offset : offset + 4].decode("ascii")
        size = int.from_bytes(data[offset + 4 : offset + 8], "big")
        end = offset + 8 + size
        if end + (size & 1) > len(data):
            raise ValueError(f"Truncated P96S {tag} chunk")
        payload = data[offset + 8 : end]
        chunk = {"id": tag}
        if tag in _LAYOUTS:
            fmt, fields = _LAYOUTS[tag]
            if len(payload) != struct.calcsize(fmt):
                raise ValueError(f"Unexpected P96S {tag} chunk size: {size}")
            for field, value in zip(fields.split(), struct.unpack(fmt, payload), strict=True):
                if isinstance(value, bytes):
                    value = value.rstrip(b"\0").decode("latin-1")
                elif field == "display_id":
                    value = f"0x{value:08x}"
                chunk[field] = value
        elif tag == "ANNO":
            chunk["text"] = payload.decode("latin-1")
        else:
            chunk["hex"] = payload.hex()
        if size & 1 and data[end]:
            chunk["padding"] = data[end]
        chunks.append(chunk)
        offset = end + (size & 1)
    return {"chunks": chunks}


def encode_settings(settings: dict) -> bytes:
    if not isinstance(settings, dict) or not isinstance(settings.get("chunks"), list):
        raise ValueError("P96S JSON must contain a chunks list")
    if settings.keys() - {"defaults", "chunks"}:
        raise ValueError("Unknown P96S JSON fields")
    body = bytearray(b"P96S")
    defaults = settings.get("defaults", {})
    if not isinstance(defaults, dict) or any(not isinstance(v, dict) for v in defaults.values()):
        raise ValueError("P96S defaults must map chunk IDs to fields")
    for entry in settings["chunks"]:
        if not isinstance(entry, dict):
            raise ValueError("P96S chunks must be objects")
        tag = entry["id"]
        if not isinstance(tag, str) or len(tag) != 4:
            raise ValueError(f"Invalid P96S chunk ID: {tag!r}")
        chunk = defaults.get(tag, {}) | entry
        if tag in _LAYOUTS:
            fmt, field_names = _LAYOUTS[tag]
            fields = field_names.split()
            unknown = chunk.keys() - {*fields, "id", "padding"}
            if unknown:
                raise ValueError(f"Unknown P96S {tag} fields: {sorted(unknown)}")
            values = []
            for field in fields:
                value = chunk[field]
                if field == "name":
                    if not isinstance(value, str):
                        raise ValueError(f"P96S {tag} name must be text")
                    value = value.encode("latin-1")
                    limit = {"STHD": 30, "BDNM": 32, "MNTR": 32, "RSHD": 22}[tag]
                    if len(value) > limit:
                        raise ValueError(f"P96S {tag} name exceeds {limit} bytes")
                elif field == "display_id" and isinstance(value, str):
                    value = int(value, 0)
                values.append(value)
            try:
                payload = struct.pack(fmt, *values)
            except struct.error as exc:
                raise ValueError(f"Invalid P96S {tag} fields: {exc}") from exc
        elif tag == "ANNO":
            payload = chunk["text"].encode("latin-1")
        else:
            payload = bytes.fromhex(chunk["hex"])
        body.extend(tag.encode("ascii"))
        body.extend(struct.pack(">I", len(payload)))
        body.extend(payload)
        if len(payload) & 1:
            body.append(chunk.get("padding", 0))
    return b"FORM" + struct.pack(">I", len(body)) + body


def load_default_settings() -> bytes:
    return encode_settings(json.loads(SETTINGS_SOURCE.read_text(encoding="utf-8")))


def workbench_mode_ids(data: bytes) -> dict[tuple[int, int], int]:
    """Return active BGRA mode IDs for the legacy VideoCore board."""
    return _mode_ids(data, 32, BGRA_MODE_BITS)


def clut_mode_ids(data: bytes) -> dict[tuple[int, int], int]:
    """Return active 8-bit mode IDs for the legacy VideoCore board."""
    return _mode_ids(data, 8, 0)


def _mode_ids(data: bytes, depth: int, format_bits: int) -> dict[tuple[int, int], int]:
    chunks = decode_settings(data)["chunks"]
    boards = [chunk for chunk in chunks if chunk["id"] == "STHD"]
    names = [chunk["name"] for chunk in chunks if chunk["id"] == "BDNM"]
    if len(boards) != 1 or boards[0]["board_type"] != LEGACY_BOARD_TYPE or names != ["VideoCore"]:
        raise ValueError("Expected one VideoCore board with legacy BoardType 14")
    modes = {}
    seen_ids = set()
    resolution = None
    for chunk in chunks:
        if chunk["id"] == "RSHD":
            resolution = chunk
            display_id = int(chunk["display_id"], 0)
            if display_id in seen_ids or display_id & 0xFFFF != 0x1000:
                raise ValueError(f"Invalid or duplicate P96S display ID: {chunk['display_id']}")
            seen_ids.add(display_id)
        elif chunk["id"] == "MIHD":
            if resolution is None:
                raise ValueError("P96S mode has no resolution header")
            size = (resolution["width"], resolution["height"])
            if size != (chunk["width"], chunk["height"]):
                raise ValueError("P96S mode dimensions do not match its resolution")
            if (
                resolution["active"]
                and resolution["flags"] & 2
                and chunk["active"]
                and chunk["depth"] == depth
            ):
                if size in modes:
                    raise ValueError(f"Duplicate P96S {depth}-bit mode: {size}")
                modes[size] = int(resolution["display_id"], 0) | format_bits
    if not modes:
        raise ValueError(f"P96S settings contain no active {depth}-bit modes")
    return modes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("inspect", "generate"))
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        if args.operation == "inspect":
            text = json.dumps(
                decode_settings(args.source.read_bytes()), indent=2, ensure_ascii=True
            )
            args.destination.write_text(text + "\n", encoding="utf-8")
        else:
            settings = json.loads(args.source.read_text(encoding="utf-8"))
            args.destination.write_bytes(encode_settings(settings))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
