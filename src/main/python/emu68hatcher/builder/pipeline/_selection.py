"""Shared package selection for the build pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING

from emu68hatcher.builder.errors import BuildError
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
    if config.ags_import is not None and config.ags_import.enabled:
        choices["whdload"] = True
    resolution = resolve_choices(
        choices,
        kickstart_version,
        emu68_version,
        config.network_stack,
        config.display.workbench_theme,
    )
    if resolution.unsatisfiable:
        details = "; ".join(
            f"{token} (required by {', '.join(names)})"
            for token, names in sorted(resolution.unsatisfiable.items())
        )
        raise BuildError(f"Missing package requirements: {details}. Change the software selection.")
    return resolution
