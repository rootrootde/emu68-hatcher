"""Shared package selection for the build pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.errors import BuildError
from emu68hatcher.config.schema import Emu68Version, Unit0Mode, WorkbenchScreenMode
from emu68hatcher.data.package_resolver import Resolution
from emu68hatcher.data.package_selection import resolve_choices

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow
    from emu68hatcher.config.schema import BuildConfig


def get_resolution(workflow: BuildWorkflow) -> Resolution:
    if workflow._resolution is None:
        workflow._resolution = resolve_selection(
            workflow.config,
            workflow.config.kickstart.version.value,
            workflow.config.emu68_version.value,
        )
    return workflow._resolution


def resolve_selection(
    config: BuildConfig, kickstart_version: str, emu68_version: str | None
) -> Resolution:
    choices = {p.name.lower(): p.enabled for p in config.packages}
    resolution = resolve_choices(
        choices,
        kickstart_version,
        emu68_version,
        config.network_stack,
        ags_enabled=config.ags_import is not None and config.ags_import.enabled,
        rgb2rtg_enabled=config.rgb2rtg.enabled,
    )
    if resolution.unsatisfiable:
        details = "; ".join(
            f"{token} (required by {', '.join(names)})"
            for token, names in sorted(resolution.unsatisfiable.items())
        )
        raise BuildError(f"Missing package requirements: {details}. Change the software selection.")
    return resolution


def rgb2rtg_problems(
    emu68_version: str,
    workbench_mode: str,
    framethrower: bool,
    emmc_unit0: str | None,
    extra_lines: list[str],
    archive: Path | None,
) -> list[tuple[str, str]]:
    """RGB2RTG requirement failures as (setting, message) in validate_rgb2rtg order."""
    problems = []
    if emu68_version != Emu68Version.V1_1_0_BETA_1.value:
        problems.append(("emu68_version", "RGB2RTG v0.75 requires the Emu68 1.1.0-beta.1 baseline"))
    if workbench_mode == WorkbenchScreenMode.NATIVE.value:
        problems.append(("workbench_mode", "RGB2RTG requires a VideoCore Workbench screen mode"))
    if framethrower:
        problems.append(
            ("framethrower", "Disable Framethrower/C790 capture before selecting RGB2RTG")
        )
    if emmc_unit0 is not None and emmc_unit0 != Unit0Mode.READ_WRITE.value:
        problems.append(
            ("emmc_unit0", "RGB2RTG on Pi 4 requires read/write eMMC unit 0 boot access")
        )
    for line in extra_lines:
        key = line.split("=", 1)[0].lower()
        if (
            key.startswith("hdmi_")
            or key in {"max_framebuffer_width", "max_framebuffer_height", "disable_overscan"}
            or (
                key == "dtoverlay"
                and any(token in line.lower() for token in ("unicam", "pal", "ntsc"))
            )
        ):
            problems.append(
                ("extra_lines", f"Remove the additional RGB2RTG display override: {line}")
            )
    if archive is None or not Path(archive).expanduser().is_file():
        problems.append(("archive", "Select the local RGB2RTG_A1200_v0.75.7z release archive"))
    return problems
