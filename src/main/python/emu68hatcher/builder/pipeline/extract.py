"""extract stage - unpack downloaded and bundled archives into the working tree"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.host.archive import extract_archive
from emu68hatcher.builder.state import BuildStage, DownloadedArtifacts, ExtractedArtifacts

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow


def _extract_progress(workflow: BuildWorkflow, label: str):
    """progress callback reporting 'Extracting <label>: <filename>' to the workflow"""

    def cb(filename: str, current: int, total: int) -> None:
        workflow._update_state(message=f"Extracting {label}: {filename}")

    return cb


def stage_extract(
    workflow: BuildWorkflow,
    downloaded: DownloadedArtifacts,
) -> ExtractedArtifacts:
    """extract downloaded archives"""
    workflow._update_state(BuildStage.EXTRACT, 0.0)
    workflow._milestone("Extracting archives")
    extracted_paths = dict(downloaded.extracted_paths)
    _extract_downloaded(workflow, downloaded, extracted_paths)
    _extract_local_archives(workflow, downloaded, extracted_paths)
    if workflow.config.rgb2rtg.enabled:
        from emu68hatcher.builder.rgb2rtg import extract_rgb2rtg

        workflow._check_cancelled()
        extracted_paths["rgb2rtg"] = extract_rgb2rtg(
            workflow.config.rgb2rtg.archive, downloaded.workspace.extracted_dir / "rgb2rtg"
        )
        workflow._check_cancelled()
    return ExtractedArtifacts(downloaded=downloaded, extracted_paths=extracted_paths)


def _extract_downloaded(
    workflow: BuildWorkflow,
    downloaded: DownloadedArtifacts,
    extracted_paths: dict[str, Path],
) -> None:
    """extract archives pulled by the download stage into extracted_dir"""
    import re

    from emu68hatcher.builder.host.archive import ARCHIVE_EXTENSIONS

    archive_extensions = ARCHIVE_EXTENSIONS
    # safe package names - no slashes, dots-only, or empties (used as path components)
    _safe_pkg = re.compile(r"^[\w][\w.+-]*$")

    if not downloaded.downloaded_files:
        workflow._update_state(progress=100.0)
        workflow._milestone("No archives to extract")
        return

    total = len(downloaded.downloaded_files)
    completed = 0
    extracted_count = 0

    for package_name, archive_path in downloaded.downloaded_files.items():
        workflow._check_cancelled()
        if not _safe_pkg.match(package_name):
            raise BuildError(f"refusing unsafe package name from YAML: {package_name!r}")

        # mirror download-manager extractions into extracted_dir (symlink, copy on windows w/o privilege)
        if package_name in extracted_paths:
            dm_path = extracted_paths[package_name]
            if not dm_path.exists():
                if package_name in downloaded.required_artifacts:
                    raise BuildError(f"Required artifact {package_name} was not extracted")
                workflow.logger.warning(f"Extracted path is missing for {package_name}: {dm_path}")
                completed += 1
                continue
            std_path = downloaded.workspace.extracted_dir / package_name
            if dm_path.is_dir() and dm_path != std_path and not std_path.exists():
                std_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    std_path.symlink_to(dm_path, target_is_directory=True)
                except (OSError, NotImplementedError):
                    # WinError 1314 (no symlink privilege) or similar - fall back to copying
                    shutil.copytree(dm_path, std_path)
            workflow.logger.info(f"Already extracted: {package_name}")
            extracted_count += 1
            completed += 1
            continue

        workflow._update_state(progress=(completed / total) * 80)
        workflow._milestone(f"Extracting {package_name}")

        output_dir = downloaded.workspace.extracted_dir / package_name

        if archive_path.suffix.lower() not in archive_extensions:
            # raw binary, not an archive - just copy it through
            output_dir.mkdir(parents=True, exist_ok=True)
            dest_file = output_dir / archive_path.name
            shutil.copy2(archive_path, dest_file)
            extracted_paths[package_name] = output_dir
            extracted_count += 1
            workflow.logger.info(f"Copied raw file {package_name}: {archive_path.name}")
            completed += 1
            continue

        result = extract_archive(
            archive_path, output_dir, progress_callback=_extract_progress(workflow, package_name)
        )

        if result.success:
            extracted_paths[package_name] = result.output_dir
            extracted_count += 1
            workflow.logger.info(
                f"Extracted {package_name}: {result.files_extracted} files to {result.output_dir}"
            )
        else:
            message = f"Failed to extract {package_name}: {result.error}"
            if package_name in downloaded.required_artifacts:
                raise BuildError(message)
            workflow.logger.warning(message)

        completed += 1

    workflow._update_state(progress=80.0)
    workflow._milestone(f"Extracted {extracted_count} of {total} downloaded packages")


def _extract_local_archives(
    workflow: BuildWorkflow,
    downloaded: DownloadedArtifacts,
    extracted_paths: dict[str, Path],
) -> None:
    """extract bundled local-source package archives the download stage skipped"""
    from emu68hatcher.builder.host.archive import ARCHIVE_EXTENSIONS
    from emu68hatcher.builder.pipeline._selection import get_resolution
    from emu68hatcher.data.package_loader import (
        get_local_packages_dir,
        get_package_by_name,
    )
    from emu68hatcher.data.package_schema import SourceType

    archive_extensions = ARCHIVE_EXTENSIONS
    local_packages_dir = get_local_packages_dir()

    # full resolved set (same as stage_download) so requires-pulled local archives extract too
    all_package_names = get_resolution(workflow).install_order

    local_extracted = 0

    # picasso96 uses a user-supplied archive when configured, overriding the aminet download
    if (
        downloaded.workspace.validated.picasso96_archive_path is not None
        and "picasso96" in all_package_names
        and "picasso96" not in extracted_paths
    ):
        p96_out = downloaded.workspace.extracted_dir / "picasso96"
        workflow._milestone("Extracting Picasso96 (user archive)")
        if _stage_user_picasso96(workflow, downloaded, p96_out):
            extracted_paths["picasso96"] = p96_out
            local_extracted += 1
        elif "picasso96" in downloaded.required_packages:
            raise BuildError("Failed to extract required Picasso96 archive")

    for pkg_name in all_package_names:
        if pkg_name in extracted_paths:
            continue  # already extracted from download
        pkg = get_package_by_name(pkg_name)
        if not pkg or not pkg.download or pkg.download.source != SourceType.LOCAL:
            continue

        # roadshow gets a user-supplied archive when configured; everything else uses the bundled file
        if pkg_name == "roadshow" and downloaded.workspace.validated.roadshow_archive_path:
            output_dir = downloaded.workspace.extracted_dir / pkg_name
            workflow._milestone("Extracting Roadshow (user archive)")
            if _stage_user_roadshow(workflow, downloaded, output_dir):
                extracted_paths[pkg_name] = output_dir
                local_extracted += 1
            elif pkg_name in downloaded.required_packages:
                raise BuildError(f"Failed to extract required {pkg_name} archive")
            continue

        if not pkg.download.path:
            continue
        archive_path = local_packages_dir / pkg.download.path
        if not archive_path.exists():
            if pkg_name in downloaded.required_packages:
                raise BuildError(f"Required local source is missing for {pkg_name}: {archive_path}")
            continue
        if archive_path.suffix.lower() not in archive_extensions:
            continue
        output_dir = downloaded.workspace.extracted_dir / pkg_name
        workflow._milestone(f"Extracting {pkg_name} (local)")

        result = extract_archive(
            archive_path, output_dir, progress_callback=_extract_progress(workflow, pkg_name)
        )
        if result.success:
            extracted_paths[pkg_name] = output_dir
            local_extracted += 1
            workflow.logger.info(
                f"Extracted local archive {pkg_name}: {result.files_extracted} files"
            )
        else:
            message = f"Failed to extract local archive {pkg_name}: {result.error}"
            if pkg_name in downloaded.required_packages:
                raise BuildError(message)
            workflow.logger.warning(message)

    workflow._update_state(progress=100.0)
    workflow._milestone(
        "Extraction complete" + (f" ({local_extracted} local)" if local_extracted else "")
    )


def _stage_user_picasso96(
    workflow: BuildWorkflow,
    downloaded: DownloadedArtifacts,
    output_dir: Path,
) -> bool:
    """extract the user's Picasso96 .lha into output_dir; returns True on success"""
    src = downloaded.workspace.validated.picasso96_archive_path
    if src is None:
        return False
    output_dir.mkdir(parents=True, exist_ok=True)
    result = extract_archive(
        src, output_dir, progress_callback=_extract_progress(workflow, "Picasso96")
    )
    if not result.success:
        workflow.logger.error(f"Failed to extract Picasso96 archive: {result.error}")
        return False
    workflow.logger.info(f"Extracted user Picasso96 archive: {result.files_extracted} files")
    return True


def _stage_user_roadshow(
    workflow: BuildWorkflow,
    downloaded: DownloadedArtifacts,
    output_dir: Path,
) -> bool:
    src = downloaded.workspace.validated.roadshow_archive_path
    kind = downloaded.workspace.validated.roadshow_archive_kind
    if src is None or kind is None:
        return False

    output_dir.mkdir(parents=True, exist_ok=True)

    if kind == "dir_full":
        return _mirror_tree(src, output_dir, workflow)
    if kind == "dir_inner":
        wrapped = output_dir / "Roadshow-1.15"
        wrapped.mkdir(parents=True, exist_ok=True)
        return _mirror_tree(src, wrapped, workflow)

    on_extract = _extract_progress(workflow, "Roadshow")

    if kind == "inner_full":
        result = extract_archive(src, output_dir, progress_callback=on_extract)
        if not result.success:
            workflow.logger.error(f"Failed to extract Roadshow archive: {result.error}")
            return False
        workflow.logger.info(f"Extracted user Roadshow archive: {result.files_extracted} files")
        return True

    if kind == "outer":
        scratch = output_dir.parent / f"{output_dir.name}_outer"
        if scratch.exists():
            shutil.rmtree(scratch)
        scratch.mkdir(parents=True, exist_ok=True)
        outer = extract_archive(src, scratch, progress_callback=on_extract)
        if not outer.success:
            workflow.logger.error(f"Failed to extract outer Roadshow envelope: {outer.error}")
            return False

        inner = _pick_inner_roadshow(scratch)
        if inner is None:
            workflow.logger.error(
                "Outer Roadshow archive did not contain Roadshow-1.15.lha (or compatible)"
            )
            return False

        result = extract_archive(inner, output_dir, progress_callback=on_extract)
        shutil.rmtree(scratch, ignore_errors=True)
        if not result.success:
            workflow.logger.error(f"Failed to extract inner Roadshow archive: {result.error}")
            return False
        workflow.logger.info(
            f"Extracted user Roadshow archive ({inner.name}): {result.files_extracted} files"
        )
        return True

    workflow.logger.error(f"Unknown Roadshow archive kind: {kind}")
    return False


def _pick_inner_roadshow(scratch: Path) -> Path | None:
    """find the full-release inner LHA (Roadshow-1.15.lha or compatible) inside a scratch dir"""
    candidates = sorted(scratch.rglob("Roadshow-1.*.lha"))
    for c in candidates:
        if "Update" in c.name or "Compact" in c.name or "Demo" in c.name:
            continue
        return c
    return None


def _mirror_tree(src: Path, dest: Path, workflow: BuildWorkflow) -> bool:
    """copy src tree into dest (dest may exist); returns True on success"""
    try:
        for item in src.iterdir():
            target = dest / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
        workflow.logger.info(f"Mirrored Roadshow directory {src} -> {dest}")
        return True
    except OSError as e:
        workflow.logger.error(f"Failed to mirror Roadshow directory {src}: {e}")
        return False
