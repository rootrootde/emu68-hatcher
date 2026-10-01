"""Ordered preference configuration."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.pipeline.configure_hardware import (
    configure_hardware,
    stage_whdload_kickstarts,
)
from emu68hatcher.builder.pipeline.configure_icons import configure_icons
from emu68hatcher.builder.pipeline.configure_network import (
    configure_network,
    generate_wireless_prefs,
)
from emu68hatcher.builder.staging.files import resolve_source_path
from emu68hatcher.builder.staging.themes import install_workbench_theme
from emu68hatcher.builder.state import CreatedImage
from emu68hatcher.config.display_models import (
    WORKBENCH_RTG_MODE_BY_NAME,
    WorkbenchScreenMode,
)
from emu68hatcher.utils.paths import ensure_dir

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow

__all__ = ["configure_preferences", "stage_whdload_kickstarts"]


def configure_preferences(
    workflow: BuildWorkflow,
    image: CreatedImage,
    boot_staging: Path,
    prefs_dir: Path,
    env_archive: Path,
) -> None:
    from emu68hatcher.builder.staging.prefs import (
        configure_picasso96_settings,
        configure_workbench_screen_mode,
        enable_workbench_backdrop,
        install_default_prefs,
    )

    workflow._update_state(progress=70.0)
    workflow._milestone("Configuring Amiga preferences")
    install_default_prefs(prefs_dir)
    workflow.logger.info("Configured Amiga preferences (wbpattern + env vars)")

    try:
        enable_workbench_backdrop(prefs_dir)
    except (OSError, ValueError) as exc:
        workflow.logger.warning(f"Could not enable Workbench backdrop: {exc}")
    else:
        workflow.logger.info("Enabled Workbench backdrop")

    workbench_mode = workflow.config.display.workbench_mode
    mode = (
        WORKBENCH_RTG_MODE_BY_NAME[workbench_mode]
        if workbench_mode != WorkbenchScreenMode.NATIVE
        else None
    )
    configure_picasso96_settings(boot_staging, mode)
    workflow.logger.info("Generated Picasso96Settings (VideoCore legacy BoardType 14)")
    if mode is not None:
        configure_workbench_screen_mode(prefs_dir, mode)
        for relative in ("WBStartup/FirstBootWB", "WBStartup/FirstBootWB.info"):
            wizard_file = resolve_source_path(boot_staging, relative)
            if wizard_file and wizard_file.is_file():
                wizard_file.unlink()
        workflow.logger.info(
            f"Configured Workbench for VideoCore {mode.width}x{mode.height}, 32-bit BGRA"
        )

    from emu68hatcher.builder.pipeline._selection import get_resolution

    if workflow.config.wifi and "emu68_wifi" in get_resolution(workflow).selected:
        workflow._update_state(progress=80.0)
        workflow._milestone("Configuring WiFi")
        sys_dir = ensure_dir(env_archive / "Sys")
        (sys_dir / "wireless.prefs").write_text(
            generate_wireless_prefs(
                workflow.config.wifi.ssid,
                workflow.config.wifi.password,
            ),
            encoding="iso-8859-1",
            newline="\n",
        )
        workflow.logger.info("Generated wireless.prefs")
    if workflow.config.network_stack is not None:
        configure_network(workflow, boot_staging)

    workflow._update_state(progress=85.0)
    workflow._milestone("Configuring hardware")
    configure_hardware(workflow, image, boot_staging)

    theme = workflow.config.display.workbench_theme
    theme_paths = install_workbench_theme(boot_staging, theme)
    if theme_paths:
        workflow.logger.info(
            f"Installed Workbench theme '{theme.value}' ({len(theme_paths)} files)"
        )

    workflow._update_state(progress=90.0)
    workflow._milestone("Generating drawer icons")
    configure_icons(workflow, image, boot_staging)

    workflow._update_state(progress=97.0)
    workflow._milestone("Arranging icons")
    from emu68hatcher.builder.staging.icon_grid import arrange_icons

    arranged = arrange_icons(boot_staging)
    if arranged:
        workflow.logger.info(f"Arranged {arranged} icons into alphabetical drawer grids")
