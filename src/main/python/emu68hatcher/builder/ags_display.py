"""Set AGS theme modes for the imported AGA profiles."""

from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path

from emu68hatcher.data.picasso96 import clut_mode_ids, load_default_settings

from .ags_source import _check_cancel, _entry, iter_hst_entries, run_hst_to_file
from .errors import BuildError
from .staging.files import resolve_staging_path


def set_theme_mode(source: bytes, mode_id: int) -> bytes:
    pattern = rb"(?mi)^([ \t]*mode[ \t]*=[ \t]*)\$[0-9a-f]+(?=[ \t\r]*(?:#|$))"
    result, count = re.subn(
        pattern, lambda match: match[1] + f"${mode_id:08X}".encode("ascii"), source
    )
    if count != 1:
        raise BuildError("AGS theme must contain exactly one hexadecimal mode setting")
    return result


def theme_switch_script(mode_id: int, *, rtg: bool) -> bytes:
    native_id = "$29000"
    rtg_id = f"${mode_id:08X}"
    old_ids = (native_id, "$00029000") if rtg else (rtg_id,)
    new_id = rtg_id if rtg else native_id
    label = "RTG" if rtg else "AGA"
    lines = ["FailAt 10", f'Echo "Setting AGS themes to {label}..."', "CD AGS:Themes"]
    for old_id in old_ids:
        command = (
            f'AGS:OS/C/SearchReplace FROM "%N" TO "%N" SEARCH "{old_id}" REPLACE "{new_id}" INPLACE'
        )
        lines += [
            'List >T:AGS-ThemeChange FILES PAT #?.conf LFORMAT "'
            + command.replace('"', '*"')
            + '"',
            "Execute T:AGS-ThemeChange",
            "AGS:OS/C/SearchReplace FROM AGS:AGS2.conf TO AGS:AGS2.conf "
            f'SEARCH "{old_id}" REPLACE "{new_id}" INPLACE',
        ]
    for name, before, after in (
        ("RTG", "run" if rtg else "rub", "rub" if rtg else "run"),
        ("AGA", "rub" if rtg else "run", "run" if rtg else "rub"),
    ):
        stem = f"AGS:Themes.ags/- Use {name} Screen -"
        lines += [
            f'If EXISTS "{stem}.{before}"',
            f' If NOT EXISTS "{stem}.{after}"',
            f'  Rename "{stem}.{before}" TO "{stem}.{after}"',
            " EndIf",
            "EndIf",
        ]
    lines += ["Delete >NIL: T:AGS-ThemeChange", "CD AGS:", 'Echo "Process complete!"']
    return ("\n".join(lines) + "\n").encode("iso-8859-1")


def prepare_ags_display(plan, staging: Path, *, rtg: bool, cancel_check=None):
    try:
        mode_id = clut_mode_ids(load_default_settings())[640, 256]
    except (OSError, ValueError, KeyError) as exc:
        raise BuildError(f"AGS requires an active VideoCore 640x256, 8-bit mode: {exc}") from exc
    device = plan.target("whdload").device
    root = resolve_staging_path(staging, f"{device}/AGS2")
    source_root = f"{plan.inventory.source_path.as_posix()}/rdb/{plan.inventory.whdload.index}/AGS2"
    verified = []

    def record(path: Path):
        relative = "AGS2/" + path.relative_to(root).as_posix()
        verified.append((device, relative, hashlib.sha256(path.read_bytes()).hexdigest()))

    if rtg:
        with tempfile.TemporaryDirectory(prefix="ags-themes-") as temporary:
            temporary = Path(temporary)
            listing = temporary / "themes.json"
            run_hst_to_file(
                ["fs", "dir", source_root + "/Themes", "--format", "Json"],
                listing,
                cancel_check,
            )
            entries = [_entry(raw) for raw in iter_hst_entries(listing)]
            themes = [e for e in entries if not e.is_dir and e.path.lower().endswith(".conf")]
            if not themes or not any(e.path.lower() == "default.conf" for e in themes):
                raise BuildError("AGS source is missing its default theme configuration")
            themes_root = resolve_staging_path(root, "Themes")
            themes_root.mkdir(parents=True, exist_ok=True)
            for relative, destination in (("Themes/*.conf", themes_root), ("AGS2.conf", root)):
                run_hst_to_file(
                    [
                        "fs",
                        "copy",
                        source_root + "/" + relative,
                        str(destination),
                        "--uaemetadata",
                        "UaeMetafile",
                        "--force",
                    ],
                    temporary / "copy.log",
                    cancel_check,
                )
            files = [(resolve_staging_path(root, "AGS2.conf"), None)] + [
                (resolve_staging_path(themes_root, entry.path), entry.size) for entry in themes
            ]
            for path, size in files:
                _check_cancel(cancel_check)
                if not path.is_file() or (size is not None and path.stat().st_size != size):
                    raise BuildError(
                        f"AGS theme configuration was not copied completely: {path.name}"
                    )
                try:
                    path.write_bytes(set_theme_mode(path.read_bytes(), mode_id))
                except BuildError as exc:
                    raise BuildError(f"Cannot configure AGS theme {path.name}: {exc}") from exc
                record(path)

    for name, use_rtg in (("Themes_AGAtoRTG", True), ("Themes_RTGtoAGA", False)):
        _check_cancel(cancel_check)
        destination = resolve_staging_path(root, "Scripts/" + name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(theme_switch_script(mode_id, rtg=use_rtg))
        record(destination)
    return tuple(verified)
