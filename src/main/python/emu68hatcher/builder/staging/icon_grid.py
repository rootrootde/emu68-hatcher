"""alphabetical icon grids for every drawer with icons, positions byte-patched in place"""

import logging
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# package drawers are gridded too: install rules merge several archives into
# one drawer (hippoplayer + hipposupport), so even vendor layouts interleave.
# Env-Archive is runtime state, left alone.
_SKIP_SUBTREES = ("prefs/env-archive",)

_MARGIN_X = 10
_MARGIN_Y = 4
_GAP_X = 8  # horizontal gap between columns (workbench clean-up density)
_ROW_PAD_Y = 18  # label line + gap below the image
_TOPAZ_CHAR_W = 8
_MAX_INNER_W = 520  # width budget for resizable drawer windows
# the SYS: window size lives in the volume disk.info (left alone), so the
# root grid has to fit the stock window
_ROOT_INNER_W = 420
_WINDOW_X = 80
_WINDOW_Y = 50
_EMBOSS = 3  # 3D frame workbench draws around the bitmap (IControl default)
_TARGET_ASPECT = 2.0  # preferred window shape, width:height (iTidy's default)

_CONTAINER_TYPES = (2, 5)  # WBDRAWER, WBGARBAGE

# first boot collapses these variants onto one canonical icon (FirstBoot/
# Pi4vsPi3_Pistorm renames the matching HDToolBox and deletes the rest,
# AUXRename activates _AUX) - variants share the canonical grid slot so the
# survivor sits alphabetically right and the deletions leave no hole
_SLOT_ALIASES = {
    "hdtoolboxpi3": "hdtoolbox",
    "hdtoolboxpi4": "hdtoolbox",
    "_aux": "aux",
}

# gone after first boot (FirstBootWB self-deletes, Startup-Sequence_UAEGFX
# removes the monitor icon for the other platform) - placed at the end of
# their drawer so the hole lands at the grid tail
_SORT_LAST = {"firstbootwb", "uaegfx", "videocore"}


@dataclass
class _Icon:
    path: Path
    do_type: int
    width: int
    height: int
    png: bool = False


# PowerIcons/OS4-style icons are plain PNG files; amiga attributes live in
# icOn chunks as 4-byte-tag entries - scalars carry a 4-byte value, string
# tags (default tool, tooltypes) run to a NUL
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_PNG_STRING_TAGS = {0x8000100A, 0x8000100B}
_PNG_TAG_X = 0x80001001
_PNG_TAG_Y = 0x80001002
_PNG_TAG_TYPE = 0x8000100F


def _png_chunks(data: bytes):
    """yield (offset, total_len, type, body) for every chunk across both images"""
    pos = 0
    while pos + 8 <= len(data):
        if data[pos : pos + 8] == _PNG_MAGIC:
            pos += 8
            continue
        length, ctype = struct.unpack(">I4s", data[pos : pos + 8])
        if pos + 12 + length > len(data):
            return  # truncated chunk
        yield pos, 12 + length, ctype, data[pos + 8 : pos + 8 + length]
        pos += 12 + length


def _png_icon_entries(body: bytes) -> list[tuple[int, bytes]] | None:
    """icOn tag entries, or None when the layout doesn't parse cleanly"""
    entries = []
    i = 0
    while i + 4 <= len(body):
        tag = int.from_bytes(body[i : i + 4], "big")
        if tag in _PNG_STRING_TAGS:
            end = body.find(b"\x00", i + 4)
            if end == -1:
                return None
            entries.append((tag, body[i : end + 1]))
            i = end + 1
        else:
            if i + 8 > len(body):
                return None
            entries.append((tag, body[i : i + 8]))
            i += 8
    return entries if i == len(body) else None


def _png_chunk(ctype: bytes, body: bytes) -> bytes:
    return (
        struct.pack(">I", len(body))
        + ctype
        + body
        + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF)
    )


def _write_png_position(path: Path, x: int, y: int) -> bool:
    """set the position tags in a PNG icon; False when the file doesn't parse"""
    data = path.read_bytes()
    chunks = list(_png_chunks(data))
    icons = [(off, total, body) for off, total, ctype, body in chunks if ctype == b"icOn"]
    parsed = {off: _png_icon_entries(body) for off, _, body in icons}
    if icons and any(p is None for p in parsed.values()):
        return False

    pos_entries = struct.pack(">II", _PNG_TAG_X, x) + struct.pack(">II", _PNG_TAG_Y, y)
    out = bytearray()
    pos = 0
    # peterk icon.library reads metadata from the first image only
    iends = [off for off, _, ctype, _ in chunks if ctype == b"IEND"]
    if not iends:
        return False
    first_iend = min(iends)
    first_icon = next((off for off, _, _ in icons if off < first_iend), None)
    for off, total, ctype, body in chunks:
        out += data[pos:off]  # signatures between images

        if ctype == b"icOn":
            body = b"".join(raw for tag, raw in parsed[off] if tag not in (_PNG_TAG_X, _PNG_TAG_Y))
            if off == first_icon:
                body += pos_entries
            out += _png_chunk(ctype, body)
        elif ctype == b"IEND" and off == first_iend and first_icon is None:
            out += _png_chunk(b"icOn", pos_entries)
            out += data[off : off + total]
        else:
            out += data[off : off + total]
        pos = off + total
    out += data[pos:]
    path.write_bytes(bytes(out))
    return True


def _color_icon_size(data: bytes) -> tuple[int, int, bool] | None:
    """ColorIcon (FORM ICON, FACE chunk) real size + frameless flag"""
    pos = data.find(b"FORM")
    while pos != -1:
        if data[pos + 8 : pos + 12] == b"ICON":
            face = data.find(b"FACE", pos)
            # MagicWB-era icons (MUI 3.8, MagicMenu) end in a degenerate FACE
            # claiming 256x256 with no image data - only trust FACE when an
            # IMAG chunk backs it, otherwise the planar size is the real one
            if face != -1 and len(data) >= face + 11 and data.find(b"IMAG", face) != -1:
                return data[face + 8] + 1, data[face + 9] + 1, bool(data[face + 10] & 1)
            return None
        pos = data.find(b"FORM", pos + 4)
    return None


def _read_icon(path: Path) -> _Icon | None:
    """parse the DiskObject header; None for anything that isn't an icon"""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    try:
        return _parse_icon(path, data)
    except (ValueError, struct.error, IndexError) as exc:
        logger.warning(f"icon {path}: unreadable ({exc}); not placed")
        return None


def _parse_icon(path: Path, data: bytes) -> _Icon | None:
    if data[:8] == _PNG_MAGIC and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        do_type = 3
        for _, _, ctype, body in _png_chunks(data):
            if ctype == b"icOn":
                for tag, raw in _png_icon_entries(body) or []:
                    if tag == _PNG_TAG_TYPE and len(raw) == 8:
                        do_type = int.from_bytes(raw[4:8], "big")
        # png icons render frameless
        return _Icon(path, do_type, width + _EMBOSS, height + _EMBOSS, png=True)
    # magic 0xE310, Gadget width/height at 12/14, do_Type at 48
    if len(data) < 78 or data[0:2] != b"\xe3\x10":
        return None
    width, height = struct.unpack(">HH", data[12:16])
    framed = True
    # the gadget size can be a stub (seen: 8x8) - the rendered bitmap is in
    # the ColorIcon IFF tail or, for NewIcons, encoded in the first IM1=
    # tooltype (transparency char, then width/height as char - 0x21)
    color = _color_icon_size(data)
    if color:
        width, height = max(width, color[0]), max(height, color[1])
        framed = not color[2]
    else:
        im1 = data.find(b"IM1=")
        if im1 != -1 and len(data) > im1 + 7:
            width = max(width, data[im1 + 5] - 0x21)
            height = max(height, data[im1 + 6] - 0x21)
    # workbench adds the emboss frame once, plus a border when framed
    pad = _EMBOSS * 2 if framed else _EMBOSS
    return _Icon(path, data[48], width + pad, height + pad)


def _pick_columns(n: int, cell_w: int, row_h: int, max_cols: int) -> int:
    """column count whose grid shape lands closest to _TARGET_ASPECT"""
    max_cols = max(1, min(n, max_cols))
    best, best_diff = max_cols, None
    # min 2 columns - a 1xN tower is never the best-looking answer (iTidy)
    for cols in range(min(2, max_cols), max_cols + 1):
        rows = -(-n // cols)
        diff = abs((cols * cell_w) / (rows * row_h) - _TARGET_ASPECT)
        if best_diff is None or diff < best_diff:
            best, best_diff = cols, diff
    return best


def _slot(icon: _Icon) -> str:
    stem = icon.path.stem.lower()
    return _SLOT_ALIASES.get(stem, stem)


def _drawer_icons(drawer: Path) -> list[_Icon]:
    """visible icons of a drawer, containers first, each group alphabetical"""
    icons = []
    for child in drawer.iterdir():
        # disk.info is the volume icon on the backdrop, not part of the window
        if not child.is_file() or child.suffix.lower() != ".info":
            continue
        if child.name.lower() == "disk.info":
            continue
        icon = _read_icon(child)
        if icon:
            icons.append(icon)
    icons.sort(key=lambda i: (i.do_type not in _CONTAINER_TYPES, _slot(i) in _SORT_LAST, _slot(i)))
    return icons


def _write_classic_position(path: Path, x: int, y: int) -> None:
    data = bytearray(path.read_bytes())
    struct.pack_into(">ii", data, 58, x, y)
    path.write_bytes(bytes(data))


def _write_classic_window(path: Path, dx: int, dy: int, w: int, h: int) -> None:
    """patch the DrawerData NewWindow; present when the pointer at 66 is set"""
    data = bytearray(path.read_bytes())
    if len(data) < 134 or data[66:70] == b"\x00\x00\x00\x00":
        return
    struct.pack_into(">hhhh", data, 78, dx, dy, w, h)
    path.write_bytes(bytes(data))


_DDFLAGS_SHOWALL = 2

_ICON_ERRORS = (ValueError, struct.error, IndexError, OSError)


def _write_drawer_showall(path: Path) -> bool:
    """set dd_Flags to show-all-files; DrawerData2 sits after the classic data"""
    from emu68hatcher.builder.staging.files import _parse_info_to_tooltypes

    data = path.read_bytes()
    try:
        _, _, end = _parse_info_to_tooltypes(data)
    except ValueError:
        return False
    if int.from_bytes(data[70:74], "big"):
        return False  # do_ToolWindow set - layout unknown, leave alone
    out = bytearray(data)
    # Gadget UserData (offset 44) holds the icon revision; revision 1 means a
    # 6-byte DrawerData2 (dd_Flags long, dd_ViewModes word) follows the
    # classic data. rev-0 icons get one inserted (an appended ColorIcon FORM
    # just shifts - readers locate it by scanning)
    has_dd2 = (
        int.from_bytes(data[44:48], "big") & 1
        and len(data) >= end + 6
        and data[end : end + 4] != b"FORM"
    )
    if has_dd2:
        struct.pack_into(">I", out, end, _DDFLAGS_SHOWALL)
    else:
        struct.pack_into(">I", out, 44, 1)
        out[end:end] = struct.pack(">IH", _DDFLAGS_SHOWALL, 0)
    path.write_bytes(bytes(out))
    return True


def arrange_icons(boot_staging: Path) -> int:
    """grid-position every icon in the shared drawers; returns icons placed"""
    drawers = sorted(
        {p.parent for p in boot_staging.rglob("*") if p.is_file() and p.suffix.lower() == ".info"}
    )

    count = 0
    for drawer in drawers:
        rel = drawer.relative_to(boot_staging).as_posix().lower()
        if any(rel == s or rel.startswith(s + "/") for s in _SKIP_SUBTREES):
            continue
        icons = _drawer_icons(drawer)
        if not icons:
            continue

        # aliased variants stack on one slot, so the grid is sized in slots
        slots: dict[str, int] = {}
        for icon in icons:
            slots.setdefault(_slot(icon), len(slots))
        n_slots = len(slots)

        # per-slot cell = image or label, whichever is wider (topaz 8 is
        # 8px per char); a stack of aliased variants sizes its slot
        cell_w: dict[int, int] = {}
        cell_h: dict[int, int] = {}
        for icon in icons:
            idx = slots[_slot(icon)]
            w = max(icon.width, len(icon.path.stem) * _TOPAZ_CHAR_W)
            cell_w[idx] = max(cell_w.get(idx, 0), w)
            cell_h[idx] = max(cell_h.get(idx, 0), icon.height)

        inner_w = _ROOT_INNER_W if rel == "." else _MAX_INNER_W
        avg_w = sum(cell_w.values()) // n_slots + _GAP_X
        avg_h = sum(cell_h.values()) // n_slots + _ROW_PAD_Y
        if rel == ".":
            # the stock SYS: window can't be resized - fill its width
            cols = n_slots
        else:
            cols = _pick_columns(n_slots, avg_w, avg_h, max(1, inner_w // avg_w))

        # column width = widest cell in the column, row height = tallest cell
        # in the row (how workbench's own clean-up packs); shrink the column
        # count until the total width fits
        while True:
            col_w = [max(cell_w[i] for i in range(c, n_slots, cols)) for c in range(cols)]
            total = 2 * _MARGIN_X + sum(col_w) + _GAP_X * (cols - 1)
            if total <= inner_w or cols <= 1:
                break
            cols -= 1

        col_x = [_MARGIN_X]
        for w in col_w[:-1]:
            col_x.append(col_x[-1] + w + _GAP_X)
        row_h = [
            max(cell_h[i] for i in range(r * cols, min((r + 1) * cols, n_slots)))
            for r in range(-(-n_slots // cols))
        ]
        row_y = [_MARGIN_Y]
        for h in row_h[:-1]:
            row_y.append(row_y[-1] + h + _ROW_PAD_Y)

        for icon in icons:
            idx = slots[_slot(icon)]
            c, r = idx % cols, idx // cols
            x = col_x[c] + (col_w[c] - icon.width) // 2
            # bottom-aligned so the labels of a row share one baseline
            y = row_y[r] + (row_h[r] - icon.height)
            try:
                if icon.png:
                    if not _write_png_position(icon.path, x, y):
                        logger.warning(
                            f"png icon {icon.path.name}: PNG chunks unreadable, not placed"
                        )
                        continue
                else:
                    _write_classic_position(icon.path, x, y)
            except _ICON_ERRORS as exc:
                logger.warning(f"icon {icon.path}: unreadable ({exc}); not placed")
                continue
            count += 1

        # size the window to show the grid; the drawer's own icon holds it.
        # the SYS: window geometry lives in the volume disk.info, left alone.
        if rel != ".":
            own = drawer.parent / f"{drawer.name}.info"
            own_icon = _read_icon(own) if own.exists() else None
            if own_icon and own_icon.do_type in _CONTAINER_TYPES and not own_icon.png:
                w = min(col_x[-1] + col_w[-1] + _MARGIN_X + 24, 560)
                h = min(row_y[-1] + row_h[-1] + _ROW_PAD_Y + 30, 200)
                try:
                    _write_classic_window(own, _WINDOW_X, _WINDOW_Y, w, h)
                except _ICON_ERRORS as exc:
                    logger.warning(f"icon {own}: window size not written ({exc})")

    # package drawers holding only icon-less files (roadshow docs) open as an
    # empty window - flip their icon to show all files. Programs/ only, so
    # stock drawers like Storage/Keymaps keep the OS behaviour.
    gridded = set(drawers)
    for d in sorted({p.parent for p in boot_staging.rglob("*") if p.is_file()}):
        rel = d.relative_to(boot_staging).as_posix().lower()
        if d in gridded or not rel.startswith("programs/"):
            continue
        own = d.parent / f"{d.name}.info"
        own_icon = _read_icon(own) if own.exists() else None
        if own_icon and own_icon.do_type in _CONTAINER_TYPES and not own_icon.png:
            try:
                _write_drawer_showall(own)
            except _ICON_ERRORS as exc:
                logger.warning(f"icon {own}: show-all-files not set ({exc})")

    return count
