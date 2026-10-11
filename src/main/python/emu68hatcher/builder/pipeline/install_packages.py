"""install packages stage"""

from __future__ import annotations

from typing import TYPE_CHECKING

from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.staging.packages import PackageInstaller
from emu68hatcher.builder.state import BuildStage, CreatedImage
from emu68hatcher.data.package_loader import get_local_packages_dir

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow


def stage_install_packages(
    workflow: BuildWorkflow,
    image: CreatedImage,
) -> CreatedImage:
    """install selected packages to the disk image using YAML rules"""
    workflow._update_state(BuildStage.INSTALL_PACKAGES, 0.0)
    workflow._milestone("Installing packages")

    extracted = image.extracted
    downloaded = extracted.downloaded
    workspace = downloaded.workspace
    if not extracted.extracted_paths and not downloaded.downloaded_files:
        workflow._update_state(progress=100.0)
        workflow._milestone("No packages to install")
        return image

    local_packages_dir = get_local_packages_dir()

    installer = PackageInstaller(
        staging_dir=workspace.staging_dir,
        extracted_packages_dir=workspace.extracted_dir,
        extracted_paths=extracted.extracted_paths,
        local_packages_dir=local_packages_dir if local_packages_dir.exists() else None,
        boot_device=workflow.config.boot_device,
    )

    from emu68hatcher.builder.pipeline._selection import get_resolution

    resolution = get_resolution(workflow)
    all_packages = resolution.install_order  # dep-before-dependent; independent order preserved
    if resolution.unsatisfiable:
        details = ", ".join(
            f"{token} (required by {', '.join(sorted(reqs))})"
            for token, reqs in sorted(resolution.unsatisfiable.items())
        )
        raise BuildError(f"Unsatisfied package requirements: {details}")
    for name, reason in resolution.dropped.items():
        workflow.logger.info(f"dropped {name}: {reason}")

    missing_sources = sorted(
        name
        for name in downloaded.required_packages
        if name in resolution.selected and not installer.has_package_source(name)
    )
    if missing_sources:
        raise BuildError("Required package source is missing: " + ", ".join(missing_sources))

    workflow.logger.info(f"Installing {len(all_packages)} packages using YAML rules")

    total = len(all_packages)
    completed = 0
    files_installed = 0

    for package_name in all_packages:
        workflow._check_cancelled()

        workflow._update_state(
            progress=(completed / total) * 100 if total > 0 else 0,
        )
        workflow._milestone(f"Installing {package_name}")

        count = installer.install_package(package_name)
        files_installed += count

        if count > 0:
            workflow.logger.info(f"Installed {count} files for {package_name}")
        else:
            workflow.logger.debug(f"No files installed for {package_name} (may use ADF/CD source)")

        completed += 1

    workflow._update_state(progress=100.0)
    workflow._milestone(f"Installed {total} packages ({files_installed} files)")
    return image
