"""configure ToolsDaemon and native Workbench Tools menus"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.staging.files import (
    read_info_tooltypes,
    resolve_source_path,
    write_info_tooltypes,
)
from emu68hatcher.builder.staging.scripts.injector import write_amiga_script
from emu68hatcher.builder.staging.toolsdaemon import patch_toolsdaemon
from emu68hatcher.config.schema import NetworkStack
from emu68hatcher.data.package_loader import get_package_by_name

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow

_MENU_INDENT = "        "
# lower priorities keep ToolsPrefs before AddItem after AutoArrangeIcons (0)
_TOOLSDAEMON_STARTPRI = -1
_ADDITEM_STARTPRI = -2


@dataclass(frozen=True)
class _MenuLauncher:
    menu: str
    title: str
    command: str
    wb_launch: bool = False
    selected_icons: bool = False
    stack: int = 8192


# Hatcher-Prefs serves every stack: one window to connect, disconnect and edit
# the settings, so the Network menu has one entry
_HATCHER_PREFS = "SYS:C/Hatcher-Prefs"
_HATCHER_PREFS_STACK = 65536

# System->Prefs submenu launchers.
# requires: package name(s) that must be installed for the editor to exist; empty = stock editor.
# p96_modern: editor exists only in the user-supplied modern Picasso96 archive, not the aminet one.
# wb_launch: Workbench mode keeps the icon stack and tooltypes for editors that need them.
_PREFS_ENTRIES: tuple[dict, ...] = (
    {"title": "AHI", "exe": "Prefs/AHI", "requires": ("ahi",), "wb_launch": True},
    {"title": "DefaultIcons", "exe": "Prefs/DefaultIcons"},
    {"title": "Font", "exe": "Prefs/Font"},
    {"title": "IControl", "exe": "Prefs/IControl"},
    {"title": "Input", "exe": "Prefs/Input"},
    {"title": "Locale", "exe": "Prefs/Locale"},
    {
        "title": "MagicMenu",
        "exe": "Prefs/MagicMenuPrefs",
        "requires": ("magicmenu",),
        "wb_launch": True,
    },
    {"title": "MUI", "exe": "Programs/MUI/MUI", "requires": ("mui38", "mui5"), "wb_launch": True},
    {"title": "Overscan", "exe": "Prefs/Overscan"},
    {
        "title": "P96Prefs",
        "exe": "Prefs/P96Prefs",
        "requires": ("picasso96",),
        "p96_modern": True,
        "wb_launch": True,
    },
    {"title": "Palette", "exe": "Prefs/Palette"},
    {
        "title": "Picasso96Mode",
        "exe": "Prefs/Picasso96Mode",
        "requires": ("picasso96",),
        "wb_launch": True,
    },
    {"title": "ScreenMode", "exe": "Prefs/ScreenMode"},
    {"title": "Trident", "exe": "Prefs/Trident", "requires": ("poseidon",), "wb_launch": True},
    {"title": "WBPattern", "exe": "Prefs/WBPattern"},
    {"title": "Workbench", "exe": "Prefs/Workbench"},
)


def _collect_app_entries(all_packages: list[str]) -> list[_MenuLauncher]:
    """Collect launchers declared by installed packages."""
    entries: list[_MenuLauncher] = []
    for name in all_packages:
        pkg = get_package_by_name(name)
        if not pkg:
            continue
        menu_entries = ([pkg.menu_entry] if pkg.menu_entry else []) + pkg.menu_entries
        for me in menu_entries:
            command = f"SYS:{me.path}" if me.wb_launch else f"Run >NIL: SYS:{me.path}"
            entries.append(
                _MenuLauncher(
                    menu=me.menu,
                    title=me.title,
                    command=command,
                    wb_launch=me.wb_launch,
                    selected_icons=me.selected_icons,
                )
            )
    return entries


def _network_entries(network_stack: NetworkStack | None) -> list[_MenuLauncher]:
    if network_stack is None:
        return []
    return [
        _MenuLauncher("Network", "Network Settings", _HATCHER_PREFS, stack=_HATCHER_PREFS_STACK)
    ]


def _prefs_entries(all_packages: list[str], p96_modern: bool) -> list[_MenuLauncher]:
    entries: list[_MenuLauncher] = []
    installed = set(all_packages)
    for entry in _PREFS_ENTRIES:
        requires = entry.get("requires")
        if requires and not (installed & set(requires)):
            continue
        if entry.get("p96_modern") and not p96_modern:
            continue
        wb_launch = bool(entry.get("wb_launch"))
        path = f"SYS:{entry['exe']}"
        entries.append(
            _MenuLauncher(
                menu="System",
                title=entry["title"],
                command=path if wb_launch else f"Run >NIL: {path}",
                wb_launch=wb_launch,
            )
        )
    return entries


def _append_launcher(lines: list[str], entry: _MenuLauncher, keyword: str = "ITEM") -> None:
    lines.append(f"{_MENU_INDENT}{keyword} {entry.title}")
    selected = " []" if entry.selected_icons else ""
    if entry.wb_launch:
        lines.append(f"{_MENU_INDENT}(WB) {entry.command}{selected}")
    else:
        lines.append(f"{_MENU_INDENT}(CLI) {entry.stack} {entry.command}{selected}")


def _build_toolsdaemon_menu(
    network_stack: NetworkStack | None,
    all_packages: list[str],
    p96_modern: bool = False,
    kickstart_version: str | None = None,
    rgb2rtg: bool = False,
    ags: bool = False,
) -> list[str]:
    """Build the complete ToolsDaemon.menu file."""
    network_actions = _network_entries(network_stack)
    package_entries = _collect_app_entries(all_packages)
    package_menus: dict[str, list[_MenuLauncher]] = {}
    for entry in sorted(package_entries, key=lambda item: (item.menu.lower(), item.title.lower())):
        package_menus.setdefault(entry.menu, []).append(entry)

    lines: list[str] = []
    package_menus.pop("Tools", None)

    network_apps = package_menus.pop("Network", [])
    if network_actions or network_apps:
        lines.append("TITLE Network")
        for entry in network_actions:
            _append_launcher(lines, entry)
        if network_actions and network_apps:
            lines.append(f"{_MENU_INDENT}ITEMBAR")
        for entry in network_apps:
            _append_launcher(lines, entry)

    apps = package_menus.pop("Apps", [])
    if apps:
        lines.append("TITLE Apps")
        for entry in apps:
            _append_launcher(lines, entry)

    games = package_menus.pop("Games", [])
    wrapper = "whdloadwrapper" in all_packages
    if ags or games or wrapper:
        lines.append("TITLE Games")
    if ags:
        # AGS_Drive: comes from S:User-Startup. WBRun opens the AGS project through its
        # IconX icon. iGame is a CLI start on purpose: started from Workbench, the shell
        # it spawns for WHDLoad has no console, and the WHDLoad wrapper chain hangs in it.
        # iGame finds its lists and prefs through PROGDIR:, so no cd is needed. Without
        # Run, ToolsDaemon's CLI start never brings iGame up.
        _append_launcher(lines, _MenuLauncher("Games", "AGS", "C:WBRun AGS_Drive:AGS"))
        _append_launcher(
            lines,
            _MenuLauncher("Games", "iGame", "Run >NIL: AGS_Drive:IGame/iGame", stack=65536),
        )
    for entry in games:
        _append_launcher(lines, entry)
    if wrapper:
        _append_launcher(
            lines,
            _MenuLauncher("Games", "WHDLoad wrapper on/off", "C:Execute S:WHDLoadWrapper-Switch"),
        )

    system_apps = package_menus.pop("System", [])
    lines.append("TITLE System")
    for entry in system_apps:
        _append_launcher(lines, entry)
    if system_apps:
        lines.append(f"{_MENU_INDENT}ITEMBAR")

    if rgb2rtg:
        lines.append(f"{_MENU_INDENT}ITEM RGB2RTG")
        for title, command in (
            ("Init...", "ASK"),
            ("Settings...", "IMAGE"),
            ("Sound...", "HDMISOUND=ASK"),
            ("About...", "ABOUT"),
        ):
            _append_launcher(
                lines, _MenuLauncher("System", title, f"C:rgb2rtg {command}"), keyword="SUB"
            )
        lines.append(f"{_MENU_INDENT}ITEMBAR")

    lines.append(f"{_MENU_INDENT}ITEM Prefs")
    for entry in _prefs_entries(all_packages, p96_modern):
        _append_launcher(lines, entry, keyword="SUB")
    lines.append(f"{_MENU_INDENT}SUBBAR")
    open_prefs = (
        "C:WBRun SYS:Prefs" if kickstart_version == "3.9" else "SYS:Rexxc/rx >NIL: S:Win SYS:Prefs"
    )
    _append_launcher(
        lines,
        _MenuLauncher("System", "Open Prefs", open_prefs),
        keyword="SUB",
    )
    lines.append(f"{_MENU_INDENT}ITEMBAR")
    _append_launcher(lines, _MenuLauncher("System", "Reboot", "C:Reboot"))

    for menu in sorted(package_menus, key=str.lower):
        lines.append(f"TITLE {menu}")
        for entry in package_menus[menu]:
            _append_launcher(lines, entry)

    lines.append("END")
    return lines


def _configure_native_tools_menu(
    boot_staging: Path,
    s_dir: Path,
    all_packages: list[str],
) -> int:
    """add package launchers to Workbench's native Tools menu."""
    entries = sorted(
        (entry for entry in _collect_app_entries(all_packages) if entry.menu == "Tools"),
        key=lambda entry: entry.title.lower(),
    )
    if not entries:
        return 0

    info_path = resolve_source_path(boot_staging, "WBStartup/AddItem.info")
    if info_path is None or not info_path.is_file():
        raise BuildError("Native Tools menu entries require WBStartup/AddItem.info")

    tooltypes = [
        "DONOTWAIT",
        f"STARTPRI={_ADDITEM_STARTPRI}",
        "ADDITEM=----------------, ,NIL:",
    ]
    helper_dir = s_dir / "AppMenu"
    for index, entry in enumerate(entries, start=1):
        if "," in entry.title:
            raise BuildError(f"Tools menu title contains a comma: {entry.title}")

        launch = f"C:WBRun {entry.command}" if entry.wb_launch else entry.command
        if entry.selected_icons:
            helper_name = f"Tool{index}.rexx"
            write_amiga_script(
                helper_dir / helper_name,
                [
                    "/* Workbench Tools launcher */",
                    "OPTIONS RESULTS",
                    "PARSE ARG commandline",
                    "commandline = STRIP(commandline, 'B')",
                    "IF LEFT(commandline, 1) = '\"' THEN PARSE VAR commandline '\"' target '\"'",
                    "ELSE PARSE VAR commandline target .",
                    f"launch = '{launch}'",
                    "IF target ~= 'SYS:' THEN launch = launch || ' \"' || target || '\"'",
                    "ADDRESS COMMAND launch",
                    "EXIT RC",
                ],
            )
            launch = f'SYS:Rexxc/rx >NIL: S:AppMenu/{helper_name} "%s"'

        if "," in launch:
            raise BuildError(f"Tools menu command contains a comma: {entry.title}")
        tooltypes.append(f"ADDITEM={entry.title},{launch},NIL:")

    write_info_tooltypes(info_path, tooltypes)
    return len(entries)


def configure_menus(
    workflow: BuildWorkflow,
    boot_staging: Path,
    s_dir: Path,
    all_packages: list[str],
    extracted_paths: dict[str, Path],
    user_archives: frozenset[str],
) -> None:
    """configure custom menus and native Tools entries."""
    if "toolsdaemon" not in all_packages:
        return
    workflow._milestone("Installing ToolsDaemon 2.2 menus")
    patched = patch_toolsdaemon(boot_staging, extracted_paths)
    workflow.logger.info(f"Patched ToolsDaemon 2.2 files: {', '.join(patched)}")

    toolsdaemon_info = resolve_source_path(boot_staging, "WBStartup/ToolsDaemon.info")
    if toolsdaemon_info is None:
        toolsdaemon_info = boot_staging / "WBStartup" / "ToolsDaemon.info"
    try:
        existing = read_info_tooltypes(toolsdaemon_info)
    except (ValueError, OSError) as exc:
        workflow.logger.warning(
            f"Cannot read {toolsdaemon_info.name} ({exc}); STARTPRI/WAIT tooltypes not set"
        )
    else:
        tooltypes = [
            entry for entry in existing if not entry.upper().startswith(("STARTPRI=", "WAIT="))
        ]
        write_info_tooltypes(
            toolsdaemon_info,
            [*tooltypes, f"STARTPRI={_TOOLSDAEMON_STARTPRI}", "WAIT=1"],
        )

    p96_modern = "picasso96" in user_archives
    ags = bool(workflow.config.ags_import and workflow.config.ags_import.enabled)
    lines = _build_toolsdaemon_menu(
        workflow.config.network_stack,
        all_packages,
        p96_modern,
        workflow.config.kickstart.version.value,
        workflow.config.rgb2rtg.enabled,
        ags,
    )
    write_amiga_script(s_dir / "ToolsDaemon.menu", lines)
    native_tools = _configure_native_tools_menu(boot_staging, s_dir, all_packages)

    removed = 0
    for relative_path in (
        "WBStartup/MenuTools",
        "WBStartup/MenuTools.info",
        "WBStartup/Disabled/MenuTools",
        "WBStartup/Disabled/MenuTools.info",
    ):
        path = resolve_source_path(boot_staging, relative_path)
        if path is not None and path.is_file():
            path.unlink()
            removed += 1
    workflow.logger.info(
        f"Generated S:ToolsDaemon.menu ({len(lines)} lines), {native_tools} native Tools entries; "
        f"removed {removed} native MenuTools files"
    )
