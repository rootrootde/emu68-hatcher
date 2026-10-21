"""relocate stock OS files already on SYS: (e.g. move a commodity into WBStartup)"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.staging.files import resolve_source_path, resolve_staging_path
from emu68hatcher.data.package_loader import get_package_by_name
from emu68hatcher.utils.paths import ensure_dir

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow


def apply_relocations(workflow: BuildWorkflow, boot_staging: Path, all_packages: list[str]) -> int:
    """move staged files per enabled packages' relocate rules; returns files moved"""
    moved = 0
    for name in sorted(all_packages):
        pkg = get_package_by_name(name)
        if not pkg or not pkg.relocate:
            continue
        for rule in pkg.relocate:
            moved += _relocate_one(workflow, boot_staging, rule.source, rule.dest)
    return moved


def _relocate_one(workflow: BuildWorkflow, boot_staging: Path, source: str, dest: str) -> int:
    src = resolve_source_path(boot_staging, source)
    if src is None or not src.exists():
        workflow.logger.info(f"relocate: {source} not present, skipping")
        return 0

    dest_dir = resolve_staging_path(boot_staging, dest.strip("/"))
    ensure_dir(dest_dir)

    count = 0
    # move the file and, when present, its sibling .info - the icon travels with the file
    for rel in (source, source + ".info"):
        item = resolve_source_path(boot_staging, rel)
        if item is None or not item.exists():
            continue
        target = resolve_staging_path(dest_dir, item.name)
        if target.exists():
            target.unlink()
        shutil.move(str(item), str(target))
        workflow.logger.info(f"relocate: moved {rel} -> {dest}")
        count += 1

    if count and "wbstartup" in dest.lower():
        _ensure_wbstartup_autorun(workflow, dest_dir, Path(source).name)
    return count


def _ensure_wbstartup_autorun(workflow: BuildWorkflow, dest_dir: Path, exe_name: str) -> None:
    """WBStartup commodities only auto-launch with a TOOL icon carrying DONOTWAIT; ensure one"""
    from emu68hatcher.builder.staging.files import read_info_tooltypes, write_info_tooltypes
    from emu68hatcher.data.package_loader import get_local_packages_dir

    info = resolve_staging_path(dest_dir, exe_name + ".info")
    if not info.exists():
        # os 3.9 ships commodities without icons, so nothing got moved; give it the
        # bundled tool icon (already WBTOOL + DONOTWAIT)
        template = get_local_packages_dir() / "System" / "_tool.info"
        if not template.exists():
            workflow.logger.warning(f"no WBStartup icon for {exe_name} and no _tool.info template")
            return
        shutil.copy2(template, info)
        workflow.logger.info(f"relocate: gave {exe_name} a WBStartup icon (DONOTWAIT)")
        return
    # an icon came across (3.1/3.2): make sure DONOTWAIT is set so WB backgrounds it
    try:
        tooltypes = read_info_tooltypes(info)
    except (ValueError, OSError) as exc:
        workflow.logger.warning(f"relocate: cannot read {info.name} ({exc}); DONOTWAIT not checked")
        return
    if not any(t.upper().startswith("DONOTWAIT") for t in tooltypes):
        write_info_tooltypes(info, [*tooltypes, "DONOTWAIT"])
        workflow.logger.info(f"relocate: added DONOTWAIT to {exe_name}.info")
