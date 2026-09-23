"""Adapt the AGS v30 WHDLoad menu for a staged Workbench."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path

from emu68hatcher.data.package_loader import get_local_packages_dir

from .ags_metadata import copy_uaefsdb_entry, rename_uaefsdb_entry
from .errors import BuildCancelledError, BuildError
from .staging.files import resolve_source_path, resolve_staging_path

V30_SCRIPT_HASHES: dict[str, str] = {
    "AGS2/Scripts/Start_AGS": "b88265909e2c57fbfa93a89c2bdd46aec764be956d6e7f1055419b1e9a5339a9",
    "AGS2/Scripts/Default.run": "29e0f0d7f595d44640a5bcb339635a1370c01eb35d66a7b1fa17f74bee75f283",
    "AGS2/Scripts/AGS-Stuff": "920ee1f6d09edd321b32bcc6568eaa6ade13cd77dcc45acce95272633c62c04d",
    "AGS2/Scripts/Check_Drives": "fb32d97ebcfb4b70f9229dbf55d8fcb2144f077cd7655d536f49e7033bacf032",
    "AGS2/Scripts/Start_AGS.info": "4a0345116b53587039fe2a42111b7f04e7d197ce45964fc9a5a8e123c803b67f",
    "AGS2/Scripts/Speed_Reset": "0de95c958dbce0ff737b4fc7c9ab676dce7443c1874844d63da3818f6467e93a",
    "AGS2/Scripts/Splash": "e597c46efa132e31afa2532ee2490f417e0a45350f2122f9c7f66bffb8472fd4",
    "AGS2/Scripts/Splash_Real": "90d4b8421511d6816d6d2468def0e39c0f30ff156770fd29afe3e25318b339a7",
    "AGS2/Scripts/Disclaimer": "eb5e9bb2c0f197ff978002ecbc83378f739c9826186aa63226476f339dcedd61",
    "AGS2/Scripts/Expert_Mode": "d28f2e57536ed532ca82347d9b8b2e93f207e419a4761fb8794f9d8dc2a929b2",
    "AGS2/Scripts/File_Log": "c179b1ddc9ddb619cdca774cd1ed997927b9821917ad9b16ad925ac7279fd585",
    "AGS2/Scripts/Play_Module": "75b98a5f7256460964b18a3f4ba8032b40f84407b64c140649cd77d8aa237f7c",
}

_DEVICE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,29}\Z")
_ASSIGNS = (
    "WHDLoad",
    "WHD_Games",
    "WHD_Demos",
    "AGS_Drive",
    "AGS",
    "Scripts",
    "Admin",
    "AGSOS",
    "WHDSaves",
)
_VARIABLES = ("Expert", "Play", "HW", "PiStorm")
_MENU_COMMANDS = {
    "bastyplayer",
    "ex",
    "echo",
    "if",
    "setenv",
    "skip",
    "endif",
    "cd",
    "kgiconload",
    "lab",
    "unset",
    "ags:",
}
_MENU_SCRIPTS = {
    "Scripts:Splash",
    "Scripts:Disclaimer",
    "Scripts:Expert_Mode",
    "Scripts:File_Log",
    "Scripts:Play_Module",
}
_RUN_PATH = re.compile(r'(?i)^cd\s+"(WHD_Games|WHD_Demos):(?:Game|Demo|Beta|Magazine)/[^\"]+"$')
_ICON_START = re.compile(r"(?i)^kgiconload\s+([^\"<>|]+\.info)$")
_X_VARIABLE = re.compile(r"(?i)^setenv\s+X(Fave|Path|File|Folder|Info|Type|Subfolder)\s+[^<>]+$")
_X_UNSET = re.compile(r"(?i)^unset\s+X(Fave|Path|File|Folder|Info|Type|Subfolder)$")
_SAVE_PATH = re.compile(rb"(?im)^\s*SavePath\s*=")


def _check_cancel(cancel_check: Callable[[], bool] | None) -> None:
    if cancel_check and cancel_check():
        raise BuildCancelledError("AGS import cancelled")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_v30(whdload_root: Path, profile: str) -> Path:
    if profile != "v30":
        raise BuildError(f"AGS script profile {profile!r} is not supported")
    for relative, expected in V30_SCRIPT_HASHES.items():
        path = whdload_root / relative
        if not path.is_file() or _sha256(path) != expected:
            raise BuildError(f"AGS v30 script does not match the supported source: {relative}")
    ags = whdload_root / "AGS2"
    for relative in ("ags2", "ags2menu", "AGS2.conf", "OS/C/kgiconload", "OS/C/BastyPlayer"):
        if not (ags / relative).is_file():
            raise BuildError(f"AGS v30 is missing {relative}")
    for name in ("Game", "Demo", "Beta", "Magazine"):
        if not (whdload_root / name).is_dir():
            raise BuildError(f"AGS WHDLoad content is missing {name}/")
    icon = ags / "Scripts" / "Start_AGS.info"
    data = icon.read_bytes()
    if len(data) < 50 or data[:2] != b"\xe3\x10" or data[48] != 4 or b"c:iconx" not in data.lower():
        raise BuildError("AGS v30 Start_AGS.info is not an IconX project icon")
    return ags


def _visible_menu(name: str) -> bool:
    return (
        name.startswith("+  Games - ")
        or name.startswith("-  Demos - ")
        or name == "-  Disk Magazines.ags"
    )


def _check_run(path: Path, whdload_root: Path, checked_targets: dict[str, Path]) -> None:
    text = path.read_bytes().decode("iso-8859-1")
    commands = []
    target_dir: Path | None = None
    icon_name: str | None = None
    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        command = line.split(None, 1)[0].lower()
        if command not in _MENU_COMMANDS:
            raise BuildError(f"AGS menu has an unsupported command: {path}")
        if command == "ex":
            parts = line.split(None, 2)
            if len(parts) < 2 or parts[1].lower() not in {
                allowed.lower() for allowed in _MENU_SCRIPTS
            }:
                raise BuildError(f"AGS menu calls an unsupported script: {path}")
            if len(parts) == 3 and parts[1].lower() != "scripts:file_log":
                raise BuildError(f"AGS menu passes an unexpected script argument: {path}")
            if len(parts) == 3 and not (
                parts[2].startswith('"') and parts[2].endswith('"') and parts[2].count('"') == 2
            ):
                raise BuildError(f"AGS menu has an unsupported log argument: {path}")
        if command == "cd":
            match = _RUN_PATH.fullmatch(line)
            if match is None:
                raise BuildError(f"AGS menu has an unsupported game path: {path}")
            target = line.split(":", 1)[1].removesuffix('"')
            key = target.casefold()
            if key not in checked_targets:
                found = resolve_source_path(whdload_root, target)
                if found is None or not found.is_dir():
                    raise BuildError(f"AGS menu game path is missing: {path}")
                checked_targets[key] = found
            target_dir = checked_targets[key]
        if command == "setenv" and not _X_VARIABLE.fullmatch(line):
            raise BuildError(f"AGS menu changes a system variable: {path}")
        if command == "unset" and not _X_UNSET.fullmatch(line):
            raise BuildError(f"AGS menu removes a system variable: {path}")
        if command == "if" and line.casefold() != 'if $expert eq "1"':
            raise BuildError(f"AGS menu has an unsupported condition: {path}")
        if command in {"skip", "lab"} and line.casefold() != f"{command} clean-up":
            raise BuildError(f"AGS menu has an unsupported jump: {path}")
        if command == "endif" and line.casefold() != "endif":
            raise BuildError(f"AGS menu has an unsupported condition end: {path}")
        if command == "bastyplayer" and line.casefold() != "bastyplayer >nil: x":
            raise BuildError(f"AGS menu has an unsupported player command: {path}")
        if command == "echo" and not (
            line.lower().startswith('echo "') and line.endswith('"') and line.count('"') == 2
        ):
            raise BuildError(f"AGS menu has an unsupported echo command: {path}")
        if command == "kgiconload":
            icon_match = _ICON_START.fullmatch(line)
            if icon_match is None:
                raise BuildError(f"AGS menu has an unsupported icon command: {path}")
            icon_name = icon_match[1]
        if command == "ags:" and line.casefold() != "ags:":
            raise BuildError(f"AGS menu has an unsupported command: {path}")
        commands.append(command)
    if commands.count("kgiconload") != 1 or commands.count("cd") != 1:
        raise BuildError(f"AGS menu is not a WHDLoad icon starter: {path}")
    if (
        target_dir is None
        or icon_name is None
        or resolve_source_path(target_dir, icon_name) is None
    ):
        raise BuildError(f"AGS menu game icon is missing: {path}")


def _check_menus(ags: Path, whdload_root: Path, cancel_check: Callable[[], bool] | None) -> None:
    roots = [
        p for p in ags.iterdir() if p.is_dir() and p.name.endswith(".ags") and _visible_menu(p.name)
    ]
    if len(roots) != 20:
        raise BuildError("AGS v30 WHDLoad menu categories do not match the supported layout")
    checked_targets: dict[str, Path] = {}
    run_count = 0
    for root in roots:
        for base, dirs, files in os.walk(root):
            dirs.sort()
            for name in files:
                if name.lower().endswith(".run"):
                    _check_cancel(cancel_check)
                    _check_run(Path(base) / name, whdload_root, checked_targets)
                    run_count += 1
    if run_count != 21_796:
        raise BuildError(f"AGS v30 WHDLoad menu has {run_count} starters; expected 21,796")


def _hide_other_menus(ags: Path, cancel_check: Callable[[], bool] | None) -> None:
    for entry in sorted(ags.iterdir(), key=lambda p: p.name.casefold()):
        _check_cancel(cancel_check)
        if entry.is_dir() and entry.name.endswith(".ags") and not _visible_menu(entry.name):
            hidden = entry.with_suffix(".agz")
            if hidden.exists():
                raise BuildError(f"AGS hidden menu already exists: {hidden.name}")
            entry.rename(hidden)
            rename_uaefsdb_entry(ags, entry.name, hidden.name)
        elif entry.is_file() and entry.name.endswith(".run"):
            hidden = entry.with_suffix(".rub")
            if hidden.exists():
                raise BuildError(f"AGS hidden starter already exists: {hidden.name}")
            entry.rename(hidden)
            rename_uaefsdb_entry(ags, entry.name, hidden.name)


def _copy_missing_tree(
    source: Path, destination: Path, cancel_check: Callable[[], bool] | None
) -> None:
    if not source.is_dir():
        raise BuildError(f"AGS v30 is missing {source.name}/")
    if not destination.exists():
        destination.mkdir(parents=True)
        copy_uaefsdb_entry(source, destination)
    for base, dirs, files in os.walk(source):
        dirs.sort()
        relative = Path(base).relative_to(source)
        target_dir = (
            resolve_staging_path(destination, relative.as_posix())
            if relative.parts
            else destination
        )
        if not target_dir.exists():
            target_dir.mkdir(parents=True)
            copy_uaefsdb_entry(Path(base), target_dir)
        for name in sorted(files):
            _check_cancel(cancel_check)
            if name.casefold() == "_uaefsdb.___":
                continue
            source_file = Path(base) / name
            target_file = resolve_staging_path(target_dir, name)
            if target_file.exists():
                continue
            shutil.copy2(source_file, target_file)
            copy_uaefsdb_entry(source_file, target_file)


def _add_drawer_icon(drawer: Path, template: Path) -> None:
    icon = resolve_staging_path(drawer.parent, f"{drawer.name}.info")
    if icon.exists():
        return
    shutil.copy2(template, icon)


def _copy_helpers(ags: Path, target: Path) -> None:
    source = ags / "OS" / "C"
    if not target.exists():
        target.mkdir()
        copy_uaefsdb_entry(source, target)
    for name in ("Ex", "BastyPlayer", "kgiconload"):
        original = resolve_source_path(source, name)
        if original is None:
            raise BuildError(f"AGS v30 is missing OS/C/{name}")
        staged = resolve_staging_path(target, name)
        if staged.exists():
            if _sha256(staged) != _sha256(original):
                raise BuildError(f"AGS helper already exists with different content: {staged}")
            continue
        shutil.copy2(original, staged)
        copy_uaefsdb_entry(original, staged)


def _write_launcher(launcher: Path, icon: Path, content_device: str) -> None:
    root = f"{content_device}:AGS/WHDLoad"
    aliases = {
        "WHDLoad": root,
        "WHD_Games": root,
        "WHD_Demos": root,
        "AGS_Drive": root,
        "AGS": "WHDLoad:AGS2",
        "Scripts": "AGS:Scripts",
        "Admin": "Scripts:Admin",
        "AGSOS": "AGS:OS",
        "WHDSaves": f"{content_device}:AGS/WHDSaves",
    }
    lines = ["FailAt 21"]
    for name in _ASSIGNS:
        lines.extend(
            [
                f"Assign >NIL: {name}: EXISTS",
                "If NOT WARN",
                f' Echo "AGS needs the {name}: assign free"',
                " Quit 20",
                "EndIf",
            ]
        )
    for name in _VARIABLES:
        lines.extend(
            [
                f"If EXISTS ENV:{name}",
                f' Echo "AGS needs ENV:{name} free"',
                " Quit 20",
                "EndIf",
            ]
        )
    for name, target in aliases.items():
        lines.extend(
            [
                f'Assign {name}: "{target}"',
                "If WARN",
                f' Echo "Cannot assign {name}:"',
                " Skip Cleanup",
                "EndIf",
            ]
        )
    lines.extend(
        [
            "Path SYS:Emu68-Hatcher/AGS/C ADD",
            "If WARN",
            " Skip Cleanup",
            "EndIf",
            "SetEnv Expert 0",
            "SetEnv Play 0",
            "SetEnv HW Real",
            "SetEnv PiStorm 1",
            "CD AGS:",
            "If WARN",
            " Skip Cleanup",
            "EndIf",
            "AGS:ags2",
            "LAB Cleanup",
            "UnSetEnv PiStorm",
            "UnSetEnv HW",
            "UnSetEnv Play",
            "UnSetEnv Expert",
        ]
    )
    lines.extend(f"Assign {name}:" for name in reversed(_ASSIGNS))
    data = ("\n".join(lines) + "\n").encode("iso-8859-1")
    if launcher.exists() and launcher.read_bytes() != data:
        raise BuildError(f"AGS launcher already exists with different content: {launcher}")
    if not launcher.exists():
        launcher.write_bytes(data)
        launcher.chmod(0o755)
        copy_uaefsdb_entry(icon.with_suffix(""), launcher)


def _add_save_path(prefs: Path, content_device: str) -> None:
    if not prefs.is_file():
        raise BuildError("WHDLoad package did not stage S/WHDLoad.prefs")
    data = prefs.read_bytes()
    save_path = f"SavePath={content_device}:AGS/WHDSaves".encode("iso-8859-1")
    lines = data.split(b"\n")
    found = False
    for index, line in enumerate(lines):
        if line.lstrip().startswith(b";") or not _SAVE_PATH.search(line):
            continue
        found = True
        value = line.split(b"=", 1)[1].split(b";", 1)[0].strip()
        if value.lower() == b"whdsaves:":
            lines[index] = save_path
            prefs.write_bytes(b"\n".join(lines))
        break
    if not found:
        with prefs.open("ab") as stream:
            if data and not data.endswith(b"\n"):
                stream.write(b"\n")
            stream.write(save_path + b"\n")


def adapt_ags(
    whdload_root: Path,
    boot_root: Path,
    content_device: str,
    profile: str,
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    """Stage the v30 WHDLoad menu and Workbench starter."""
    _check_cancel(cancel_check)
    if not _DEVICE.fullmatch(content_device):
        raise BuildError(f"Invalid AGS content device name: {content_device!r}")
    if content_device.casefold() in {name.casefold() for name in _ASSIGNS} | {
        "sys",
        "s",
        "c",
        "devs",
        "libs",
        "env",
        "envarc",
        "ram",
        "t",
    }:
        raise BuildError(f"AGS content device conflicts with a system assign: {content_device}")
    ags = _require_v30(whdload_root, profile)
    _check_menus(ags, whdload_root, cancel_check)
    launcher_dir = resolve_staging_path(boot_root, "Emu68-Hatcher/AGS")
    launcher = launcher_dir / "Start_AGS"
    icon = ags / "Scripts" / "Start_AGS.info"
    staged_icon = launcher_dir / "Start_AGS.info"
    if staged_icon.exists() and staged_icon.read_bytes() != icon.read_bytes():
        raise BuildError(f"AGS launcher icon already exists with different content: {staged_icon}")
    prefs = resolve_source_path(boot_root, "S/WHDLoad.prefs")
    if prefs is None or not prefs.is_file():
        raise BuildError("WHDLoad package did not stage S/WHDLoad.prefs")
    if resolve_source_path(boot_root, "C/WHDLoad") is None:
        raise BuildError("WHDLoad package did not stage C/WHDLoad")
    if resolve_source_path(boot_root, "C/IconX") is None:
        raise BuildError("Workbench did not stage C/IconX")
    for name in ("Default.run", "Speed_Reset", "Expert_Mode", "File_Log", "Play_Module"):
        (ags / "Scripts" / name).write_bytes(b'Echo >NIL: ""\n')
    _hide_other_menus(ags, cancel_check)
    _copy_missing_tree(ags / "OS" / "Libs", resolve_staging_path(boot_root, "Libs"), cancel_check)
    _copy_missing_tree(
        ags / "OS" / "Devs" / "Kickstarts",
        resolve_staging_path(boot_root, "Devs/Kickstarts"),
        cancel_check,
    )
    _copy_missing_tree(ags / "OS" / "Fonts", resolve_staging_path(boot_root, "Fonts"), cancel_check)
    resolve_staging_path(whdload_root.parent, "WHDSaves").mkdir(exist_ok=True)
    launcher_dir.mkdir(parents=True, exist_ok=True)
    _copy_helpers(ags, launcher_dir / "C")
    template = get_local_packages_dir() / "System" / "_drawer.info"
    if not template.is_file() or template.read_bytes()[48:49] != b"\x02":
        raise BuildError("Workbench drawer icon template is missing or invalid")
    _add_drawer_icon(launcher_dir.parent, template)
    _add_drawer_icon(launcher_dir, template)
    if not staged_icon.exists():
        shutil.copy2(icon, staged_icon)
        copy_uaefsdb_entry(icon, staged_icon)
    _write_launcher(launcher, icon, content_device)
    _add_save_path(prefs, content_device)
