"""configure startup scripts"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.staging.files import resolve_staging_path
from emu68hatcher.builder.staging.scripts.generator import generate_shell_startup
from emu68hatcher.builder.staging.scripts.injector import (
    apply_package_scripts,
    apply_standard_injections,
    write_amiga_script,
)
from emu68hatcher.config.schema import KickstartVersion
from emu68hatcher.data.package_loader import get_local_packages_dir

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow


def configure_scripts(
    workflow: BuildWorkflow,
    boot_staging: Path,
    s_dir: Path,
    all_packages: list[str],
    user_archives: frozenset[str],
) -> None:
    """inject into or generate Startup-Sequence, User-Startup, Shell-Startup, FirstBoot"""
    workflow._update_state(progress=10.0)
    workflow._milestone("Injecting into Startup-Sequence")
    startup_path = resolve_staging_path(s_dir, "Startup-Sequence")

    if startup_path.exists():
        local_packages_dir = get_local_packages_dir()
        content_base = local_packages_dir / "System"

        injection_results = apply_standard_injections(
            staging_dir=boot_staging,
            content_base_path=content_base,
            use_remlib=workflow.config.kickstart.version != KickstartVersion.V3_9,
        )
        failures = [result.error for result in injection_results if result.error]
        if failures:
            details = "; ".join(failures)
            raise BuildError(f"Startup-Sequence update failed: {details}")
        injection_count = sum(result.changed for result in injection_results)
        workflow.logger.info(f"Applied {injection_count} script injections to Startup-Sequence")
    else:
        raise BuildError(
            "No Startup-Sequence found in staging. "
            "ADF extraction is required - check that install media (ADF files) are configured."
        )

    workflow._update_state(progress=20.0)
    workflow._milestone("Setting up User-Startup")
    user_startup_path = resolve_staging_path(s_dir, "User-Startup")
    if not user_startup_path.exists():
        write_amiga_script(user_startup_path, ["; User-Startup", "; Emu68 Hatcher"])

    # user-supplied archives switch when_user_archive-gated script variants (e.g. the full
    # Roadshow auto-runs Network-Startup; the demo timer starts when bsdsocket.library opens)
    script_count = apply_package_scripts(boot_staging, all_packages, user_archives)
    workflow.logger.info(f"Applied {script_count} package script blocks to User-Startup")

    workflow._update_state(progress=30.0)
    workflow._milestone("Generating Shell-Startup")
    shell_startup = generate_shell_startup()
    write_amiga_script(resolve_staging_path(s_dir, "Shell-Startup"), shell_startup.splitlines())
    workflow.logger.info(f"Generated Shell-Startup ({len(shell_startup)} bytes)")

    workflow._update_state(progress=40.0)
