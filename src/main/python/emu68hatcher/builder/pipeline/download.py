"""download and workspace setup stages"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.host.download_catalog import (
    downloadable_mandatory_names,
    get_emu68_boot_files,
    get_package_downloads,
    get_required_startup_files,
)
from emu68hatcher.builder.host.downloads import DownloadManager
from emu68hatcher.builder.staging.files import prepare_staging_directory
from emu68hatcher.builder.state import (
    BuildStage,
    DownloadedArtifacts,
    ValidatedInputs,
    Workspace,
)
from emu68hatcher.config.defaults import EMU68_BOOT_PARTITION_NAME
from emu68hatcher.data.themes import get_workbench_theme
from emu68hatcher.utils.paths import ensure_dir, make_temp_workdir

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow
    from emu68hatcher.data.package_resolver import Resolution


def stage_setup_workspace(workflow: BuildWorkflow, validated: ValidatedInputs) -> Workspace:
    """set up working directories"""
    workflow._update_state(BuildStage.SETUP_WORKSPACE, 0.0)
    workflow._milestone("Setting up workspace")

    work_dir = make_temp_workdir()
    staging_dir = ensure_dir(work_dir / "staging")
    downloads_dir = ensure_dir(work_dir / "downloads")
    extracted_dir = ensure_dir(work_dir / "extracted")
    workbench_dir = ensure_dir(work_dir / "workbench")

    devices = [EMU68_BOOT_PARTITION_NAME]
    boot_device = None
    if workflow.config.partitions:
        devices.extend(p.device for p in workflow.config.partitions.iter_amiga_partitions())
        boot_device = workflow.config.partitions.bootable_device

    prepare_staging_directory(staging_dir, devices, boot_device=boot_device)

    workflow._update_state(progress=100.0)
    workflow._milestone("Workspace ready")
    return Workspace(
        validated=validated,
        work_dir=work_dir,
        staging_dir=staging_dir,
        downloads_dir=downloads_dir,
        extracted_dir=extracted_dir,
        workbench_dir=workbench_dir,
    )


def _download_pfs3aio_if_needed(
    workflow: BuildWorkflow,
    manager: DownloadManager,
    artifacts: DownloadedArtifacts,
) -> Path | None:
    if not workflow.config.partitions or not workflow.config.partitions.uses_pfs3:
        return None

    workflow._update_state(progress=2.0)
    workflow._milestone("Downloading PFS3AIO filesystem handler")
    startup_files = get_required_startup_files()

    for item in startup_files:
        if item.name == "pfs3aio":
            artifacts.required_artifacts.add(item.name)
            result = manager.download(item)
            if result.success and result.extracted_path:
                artifacts.downloaded_files["pfs3aio"] = result.path
                artifacts.extracted_paths["pfs3aio"] = result.extracted_path
                workflow.logger.info(f"PFS3AIO handler ready: {result.extracted_path}")
            else:
                raise BuildError(f"Failed to download PFS3AIO filesystem handler: {result.error}")
            return result.extracted_path

    raise BuildError("PFS3AIO not found in startup files configuration")


def _extract_ffs_handler_if_needed(
    workflow: BuildWorkflow,
    workspace: Workspace,
) -> Path | None:
    """extract L/FastFileSystem from Install ADF if any partition uses FFS (Intl needs a registered RDB handler)"""
    if not workflow.config.partitions or not workflow.config.partitions.uses_ffs:
        return None

    from emu68hatcher.utils.host_tools import find_hst_imager, localize_for_hst, run_hst_extract

    # match KS and WB on parsed version tuples (string startswith would treat "3.1" as a prefix of "3.10")
    ks_version = workflow.config.kickstart.version.value
    ks_parts = tuple(ks_version.split("."))

    def _versions_match(wbv: str) -> bool:
        if not wbv:
            return False
        wb_parts = tuple(wbv.split("."))
        prefix_len = min(len(ks_parts), len(wb_parts))
        return ks_parts[:prefix_len] == wb_parts[:prefix_len]

    install_adf: Path | None = None
    for m in workspace.validated.resolved_install_media:
        if "install" not in m.adf_name.lower():
            continue
        if _versions_match(m.workbench_version or ""):
            install_adf = m.path
            break

    # filename fallback (sorted for determinism across filesystems / repeated runs)
    if install_adf is None:
        for d in (Path(p) for p in workflow.config.asset_directories):
            if not d.exists():
                continue
            candidates = sorted(p for p in d.rglob("*.adf") if p.name.lower().startswith("install"))
            if candidates:
                install_adf = candidates[0]
                break

    if install_adf is None or not install_adf.exists():
        dirs_str = ", ".join(str(d) for d in workflow.config.asset_directories) or "(none)"
        raise BuildError(
            "FFS partition selected but no Install ADF found to extract "
            f"L/FastFileSystem from. Asset directories: {dirs_str}. "
            "Place an Install3.x.adf in one of them."
        )

    hst_imager = find_hst_imager()
    if not hst_imager:
        raise BuildError("hst-imager not available; cannot extract FFS handler")

    try:
        install_adf = localize_for_hst(install_adf, workspace.extracted_dir / "network_media")
    except OSError as e:
        raise BuildError(f"Cannot copy {install_adf.name} from network path: {e}") from e

    scratch = ensure_dir(workspace.extracted_dir / "ffs_handler")
    workflow._update_state(progress=3.0)
    workflow._milestone("Extracting FFS handler from Install ADF")
    result = run_hst_extract(
        hst_imager,
        f"{install_adf.as_posix()}/L/FastFileSystem",
        scratch.as_posix() + "/",
        uaemetadata="UaeFsDb",
    )
    handler = scratch / "FastFileSystem"
    if result.returncode != 0 or not handler.exists():
        raise BuildError(
            f"Failed to extract L/FastFileSystem from {install_adf.name}: "
            f"{result.stderr.strip() or result.stdout.strip() or 'handler not found'}"
        )
    workflow.logger.info(f"FFS handler ready: {handler}")
    return handler


def _download_boot_files(
    workflow: BuildWorkflow,
    manager: DownloadManager,
    artifacts: DownloadedArtifacts,
) -> None:
    workflow._update_state(progress=5.0)
    workflow._milestone(f"Downloading Emu68 {workflow.config.emu68_version.value} boot files")
    items = get_emu68_boot_files(version=workflow.config.emu68_version.value)
    if not items:
        raise BuildError(
            f"No boot files are defined for Emu68 {workflow.config.emu68_version.value}"
        )
    workflow.logger.info(f"Downloading {len(items)} Emu68 boot file variant(s) from GitHub...")
    for item in items:
        if not item.optional:
            artifacts.required_artifacts.add(item.name)
            artifacts.required_boot_artifacts.add(item.name)
        result = manager.download(item)
        if result.success:
            artifacts.downloaded_files[item.name] = result.path
            if result.extracted_path:
                artifacts.extracted_paths[item.name] = result.extracted_path
            workflow.logger.info(f"Downloaded Emu68 variant: {item.name} -> {result.path}")
        elif item.optional:
            workflow.logger.warning(
                f"Optional Emu68 variant failed (non-fatal): {item.name} - {result.error}"
            )
        else:
            raise BuildError(f"Failed to download required {item.name}: {result.error}")


def _failure_kind(failures: list[str]) -> str:
    detail = " ".join(failures).lower()
    if "hash mismatch" in detail:
        return "hash mismatch"
    if any(token in detail for token in ("getaddrinfo", "name or service", "nodename nor")):
        return "DNS lookup failed"
    if "not yet valid" in detail or "has expired" in detail:
        return "TLS certificate not yet valid / expired"
    if "certificate" in detail or "ssl:" in detail:
        return "TLS handshake failed"
    return "download failed"


def _record_package_results(
    workflow: BuildWorkflow,
    artifacts: DownloadedArtifacts,
    results: dict,
    mandatory_names: set[str],
) -> None:
    mandatory = {name.lower() for name in mandatory_names}
    mandatory_failures: list[str] = []
    optional_failures: list[str] = []
    for name, result in results.items():
        if result.success:
            if result.path:
                artifacts.downloaded_files[name] = result.path
            if result.extracted_path:
                artifacts.extracted_paths[name] = result.extracted_path
            workflow.logger.info(f"Downloaded {name}: {result.path}")
        elif name.lower() in mandatory:
            mandatory_failures.append(f"{name}: {result.error}")
            workflow.logger.error(f"Mandatory package failed: {name}: {result.error}")
        else:
            optional_failures.append(f"{name}: {result.error}")
            workflow.logger.warning(f"Optional package failed: {name}: {result.error}")
    if optional_failures:
        names = ", ".join(failure.split(":", 1)[0] for failure in optional_failures)
        workflow.logger.warning(f"{len(optional_failures)} optional package(s) skipped: {names}")
    if mandatory_failures:
        detail = "\n  - ".join(mandatory_failures)
        raise BuildError(f"{_failure_kind(mandatory_failures)}:\n  - {detail}")


def _download_packages(
    workflow: BuildWorkflow,
    workspace: Workspace,
    manager: DownloadManager,
    artifacts: DownloadedArtifacts,
    resolution: Resolution,
) -> None:
    kickstart_version = workflow.config.kickstart.version.value
    emu68_version = workflow.config.emu68_version.value
    package_names = resolution.install_order
    download_names = package_names
    if workspace.validated.picasso96_archive_path is not None:
        download_names = [name for name in package_names if name != "picasso96"]
    mandatory = set(downloadable_mandatory_names(kickstart_version, emu68_version))
    mandatory.update(_required_selection_packages(workflow, resolution))
    artifacts.required_artifacts.update(mandatory)
    workflow.logger.info(f"Total packages to process: {len(package_names)}")
    if not package_names:
        return
    items = get_package_downloads(download_names)
    if not items:
        return

    def progress_callback(name: str, current: int, total: int) -> None:
        workflow._check_cancelled()
        workflow._update_state(progress=20 + (current / total) * 80 if total > 0 else 20)
        workflow._log(f"Working on {name}")

    def file_progress(name: str, downloaded: int, total: int) -> None:
        if total > 0:
            workflow._update_state(
                message=(
                    f"Downloading {name} - {downloaded / (1024 * 1024):.1f}/"
                    f"{total / (1024 * 1024):.1f} MB"
                )
            )

    workflow.logger.info(f"Downloading {len(items)} packages...")
    results = manager.download_all(items, progress_callback, file_progress=file_progress)
    _record_package_results(workflow, artifacts, results, mandatory)
    downloaded = {name.lower() for name in results}
    for package_name in package_names:
        if package_name.lower() not in downloaded:
            workflow.logger.info(
                f"Package {package_name} not configured for download (may be local or built-in)"
            )


def _required_selection_packages(
    workflow: BuildWorkflow,
    resolution: Resolution,
) -> set[str]:
    theme = get_workbench_theme(workflow.config.display.workbench_theme)
    required = {name.lower() for name in theme.required_packages} if theme else set()
    from emu68hatcher.data.package_loader import get_mandatory_packages

    required.update(
        p.name
        for p in get_mandatory_packages(
            workflow.config.kickstart.version.value, workflow.config.emu68_version.value
        )
    )
    required.update(resolution.required_by)
    if workflow.config.network_stack is not None:
        required.add(workflow.config.network_stack.value.lower())
    if not required:
        return set()

    from emu68hatcher.data.package_loader import get_package_by_name

    order = resolution.install_order
    providers: dict[str, str] = {}
    for name in order:
        package = get_package_by_name(name)
        if package is None:
            continue
        providers[name] = name
        providers.update({token.lower(): name for token in package.provides})

    pending = list(required)
    while pending:
        name = pending.pop()
        package = get_package_by_name(name)
        if package is None:
            continue
        requirements = package.requires + (
            [package.archive_package] if package.archive_package else []
        )
        for requirement in requirements:
            provider = providers.get(requirement.lower())
            if provider is not None and provider not in required:
                required.add(provider)
                pending.append(provider)
    return required


def stage_download(workflow: BuildWorkflow, workspace: Workspace) -> DownloadedArtifacts:
    """download network resources and record the required inputs."""
    workflow._update_state(BuildStage.DOWNLOAD, 0.0)
    workflow._milestone("Preparing downloads")

    manager = DownloadManager(
        work_dir=workspace.downloads_dir,
        max_retries=5,
        timeout=120.0,
        cancel_callback=lambda: workflow._cancelled,
    )

    from emu68hatcher.builder.pipeline._selection import get_resolution
    from emu68hatcher.data.package_loader import get_mandatory_packages

    resolution = get_resolution(workflow)
    mandatory_packages = get_mandatory_packages(
        workflow.config.kickstart.version.value,
        workflow.config.emu68_version.value,
    )
    required_packages = {
        package.name for package in mandatory_packages if package.name in resolution.selected
    }
    required_packages.update(_required_selection_packages(workflow, resolution))
    artifacts = DownloadedArtifacts(
        workspace=workspace,
        required_packages=required_packages,
    )

    pfs3_handler = _download_pfs3aio_if_needed(workflow, manager, artifacts)
    ffs_handler = _extract_ffs_handler_if_needed(workflow, workspace)
    _download_boot_files(workflow, manager, artifacts)
    _download_packages(workflow, workspace, manager, artifacts, resolution)

    workflow._update_state(progress=100.0)
    workflow._milestone(f"Downloaded {len(artifacts.downloaded_files)} items")
    return replace(
        artifacts,
        pfs3_handler_path=pfs3_handler,
        ffs_handler_path=ffs_handler,
    )
