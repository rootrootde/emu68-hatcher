"""configure Amiga system stage - orchestrates script, boot, and prefs phases"""

from __future__ import annotations

from typing import TYPE_CHECKING

from emu68hatcher.builder.pipeline.configure_boot import configure_boot_partition
from emu68hatcher.builder.pipeline.configure_menus import configure_menus
from emu68hatcher.builder.pipeline.configure_prefs import (
    configure_preferences,
    stage_whdload_kickstarts,
)
from emu68hatcher.builder.pipeline.configure_scripts import configure_scripts
from emu68hatcher.builder.pipeline.relocate import apply_relocations
from emu68hatcher.builder.staging.files import resolve_staging_path
from emu68hatcher.builder.state import BuildStage, CreatedImage
from emu68hatcher.utils.paths import ensure_dir

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow


def stage_configure(workflow: BuildWorkflow, image: CreatedImage) -> CreatedImage:
    """configure stage: scripts (injections, startup, FirstBoot), boot partition (Emu68/ROM/config), prefs"""
    workflow._update_state(BuildStage.CONFIGURE, 0.0)
    workflow._milestone("Configuring system")

    boot_device = workflow.config.boot_device
    boot_staging = image.workspace.staging_dir / boot_device
    # case-insensitive lookups: on linux a literal join beside an ADF-made drawer of
    # another casing leaves two sibling directories, which finalize refuses
    s_dir = ensure_dir(resolve_staging_path(boot_staging, "S"))
    prefs_dir = ensure_dir(resolve_staging_path(boot_staging, "Prefs"))
    env_archive = ensure_dir(resolve_staging_path(prefs_dir, "Env-Archive"))
    ensure_dir(resolve_staging_path(boot_staging, "Devs/DOSDrivers"))

    all_packages = _collect_enabled_packages(workflow, image)

    # phase 1: Script configuration (0-40%)
    workflow._check_cancelled()
    configure_scripts(
        workflow,
        boot_staging,
        s_dir,
        all_packages,
        image.workspace.validated.user_archives,
    )
    workflow._check_cancelled()
    configure_menus(
        workflow,
        boot_staging,
        s_dir,
        all_packages,
        image.extracted.extracted_paths,
        image.workspace.validated.user_archives,
    )

    # relocate stock OS files per enabled packages (e.g. commodity -> WBStartup)
    workflow._check_cancelled()
    moved = apply_relocations(workflow, boot_staging, all_packages)
    if moved:
        workflow.logger.info(f"Relocated {moved} staged file(s)")

    from emu68hatcher.builder.pipeline.configure_games import configure_whdload_wrapper

    workflow._check_cancelled()
    configure_whdload_wrapper(workflow, boot_staging, all_packages)

    # phase 2: Boot partition setup (40-70%)
    workflow._check_cancelled()
    configure_boot_partition(workflow, image)

    if "whdload" in all_packages:
        workflow._check_cancelled()
        stage_whdload_kickstarts(workflow, image, boot_staging)

    # phase 3: System preferences (70-100%)
    workflow._check_cancelled()
    configure_preferences(workflow, image, boot_staging, prefs_dir, env_archive)

    workflow._update_state(progress=100.0)
    workflow._milestone("System configured")
    return image


def _collect_enabled_packages(workflow: BuildWorkflow, image: CreatedImage) -> list[str]:
    """installed package names in install order, without optional ones whose download failed"""
    from emu68hatcher.builder.pipeline._selection import get_resolution

    skipped = image.extracted.downloaded.skipped_packages
    return [name for name in get_resolution(workflow).install_order if name not in skipped]
