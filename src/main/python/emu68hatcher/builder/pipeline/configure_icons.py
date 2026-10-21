"""Workbench drawer icon configuration."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.state import CreatedImage

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow


def configure_icons(
    workflow: BuildWorkflow,
    image: CreatedImage,
    boot_staging: Path,
) -> None:
    from emu68hatcher.builder.staging.icons import (
        ensure_dirs_for_orphan_drawer_icons,
        ensure_drawer_icons,
    )

    created = ensure_drawer_icons(boot_staging)
    if created:
        workflow.logger.info(f"Created {created} drawer icons")
    fixed = ensure_dirs_for_orphan_drawer_icons(boot_staging)
    if fixed:
        workflow.logger.info(f"Created {fixed} missing drawers for orphan icons")
    _install_icon_set(workflow, image, boot_staging)


def _install_icon_set(
    workflow: BuildWorkflow,
    image: CreatedImage,
    boot_staging: Path,
) -> None:
    from emu68hatcher.data.icon_sets import get_icon_sets_for_version

    name = workflow.config.icon_set
    version = workflow.config.kickstart.version.value
    try:
        rows = get_icon_sets_for_version(version)
        selected = next((row for row in rows if row.get("name") == name), None)
        if selected is None:
            selected = next((row for row in rows if row.get("default")), None)
        if selected is None:
            workflow.logger.info(f"No icon set for KS {version}; keeping generic drawer icons")
            return
        _apply_new_folder(workflow, image, boot_staging, selected.get("new_folder_icon", {}))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        workflow.logger.exception("Failed to install icon set")


def _apply_new_folder(
    workflow: BuildWorkflow,
    image: CreatedImage,
    boot_staging: Path,
    config: dict,
) -> None:
    from emu68hatcher.builder.staging.icons import apply_icon_set_drawer
    from emu68hatcher.utils.host_tools import localize_for_hst

    source = config.get("source")
    filename = config.get("file")
    if not source or not filename:
        return
    media = image.workspace.validated.resolved_install_media
    adf_path = next((item.path for item in media if item.adf_name == source), None)
    if not adf_path:
        workflow.logger.warning(
            f"Icon set source ADF '{source}' not available; keeping bundled drawer"
        )
        return
    try:
        adf_path = localize_for_hst(
            adf_path,
            image.workspace.extracted_dir / "network_media",
        )
    except OSError:
        workflow.logger.warning(
            f"Could not copy icon set ADF '{source}' from network path; keeping bundled drawer"
        )
        return
    count = apply_icon_set_drawer(boot_staging, adf_path, filename)
    if count:
        workflow.logger.info(f"Applied icon-set drawer from {source}/{filename} to {count} folders")
