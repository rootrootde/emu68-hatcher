"""Install Workbench files from ADFs."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.pipeline.adf_extract import extract_adfs_with_rules
from emu68hatcher.builder.pipeline.adf_mapping import filter_needed_media
from emu68hatcher.builder.staging.boingbag import apply_boingbags
from emu68hatcher.builder.staging.files import stage_workbench_files
from emu68hatcher.builder.state import BuildStage, CreatedImage
from emu68hatcher.data.install_media import scan_install_media_by_hash

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow


def stage_install_workbench(
    workflow: BuildWorkflow,
    image: CreatedImage,
) -> CreatedImage:
    workspace = image.workspace
    workflow._update_state(BuildStage.INSTALL_WORKBENCH, 0.0, "Installing Workbench...")
    adf_paths, description = _select_adfs(workflow, image)
    if not adf_paths:
        raise BuildError(
            "No Workbench disks detected. Add a directory containing your Workbench "
            "ADF files to the Amiga Files tab."
        )

    workflow._update_state(progress=10.0)
    workflow._milestone(f"Extracting {description}")
    total_files, errors = extract_adfs_with_rules(workflow, workspace, adf_paths)
    if errors:
        workflow.logger.warning(f"Some ADF extractions failed: {'; '.join(errors[:5])}")
    if total_files == 0:
        raise BuildError(
            "ADF extraction produced no files - hst-imager could not read the Workbench ADFs."
        )

    workflow._update_state(progress=50.0)
    if workflow.config.kickstart.version.value.startswith("3.2"):
        _decompress_z_files(workflow, workspace.workbench_dir)

    workflow._update_state(progress=70.0)
    workflow._milestone("Copying Workbench files to staging")
    files_staged = stage_workbench_files(
        workspace.workbench_dir, workspace.staging_dir / workflow.config.boot_device
    )
    workflow.logger.info(f"Staged {files_staged} Workbench files to {workflow.config.boot_device}")

    # BoingBags include older libraries and tools than the add-on packages.
    if workflow.config.kickstart.version.value == "3.9":
        workflow._update_state(progress=90.0)
        workflow._milestone("Installing AmigaOS 3.9 BoingBag 1 + 2")
        apply_boingbags(
            image.extracted.extracted_paths,
            workspace.staging_dir / workflow.config.boot_device,
        )

    workflow._update_state(progress=100.0)
    workflow._milestone(f"Workbench installed ({files_staged} files)")
    return image


def _select_adfs(
    workflow: BuildWorkflow,
    image: CreatedImage,
) -> tuple[list[Path], str]:
    version = workflow.config.kickstart.version.value
    media = image.workspace.validated.resolved_install_media
    if media:
        paths, names = filter_needed_media(media, version)
        if paths:
            workflow.logger.info(
                f"Identified {len(paths)} ADFs for KS {version}: {', '.join(names)}"
            )
            return paths, f"{len(paths)} ADFs for KS {version}"

    workflow.logger.info("No pre-resolved media, attempting direct ADF scan...")
    directories = [Path(path) for path in workflow.config.asset_directories if Path(path).exists()]
    found, _ = scan_install_media_by_hash(directories)
    paths, _ = filter_needed_media(found, version)
    return paths, f"{len(paths)} ADFs"


def _decompress_z_files(workflow: BuildWorkflow, directory: Path) -> None:
    from emu68hatcher.utils.host_tools import find_7z, run_7z

    z_files = list(directory.rglob("*.Z"))
    if not z_files:
        return
    seven_z = find_7z()
    if not seven_z:
        workflow.logger.warning(f"7-Zip not found, cannot decompress {len(z_files)} .Z files")
        return

    workflow._milestone(f"Decompressing {len(z_files)} .Z files")
    by_dir: dict[Path, list[Path]] = {}
    for z_file in z_files:
        by_dir.setdefault(z_file.parent, []).append(z_file)
    done = 0
    failed = 0
    for parent, files in sorted(by_dir.items()):
        workflow._check_cancelled()
        try:
            result = run_7z(seven_z, ["e", "*.Z", "-y"], cwd=parent, timeout=120)
        except (OSError, subprocess.SubprocessError) as e:
            workflow.logger.warning(f"7-Zip failed in {parent.name}: {e}")
            failed += len(files)
            done += len(files)
            continue
        if result.returncode != 0:
            output = (result.stderr or result.stdout or "").strip()
            workflow.logger.warning(f"7-Zip reported errors in {parent.name}: {output[:200]}")
        for z_file in files:
            if z_file.with_suffix("").exists():
                z_file.unlink()
            else:
                failed += 1
        done += len(files)
        workflow._update_state(
            progress=50.0 + 20.0 * done / len(z_files),
            message=f"Decompressing .Z files ({done}/{len(z_files)})",
        )
    if failed:
        workflow.logger.warning(f"{failed} .Z files could not be decompressed")
    workflow.logger.info(f"Decompressed {len(z_files) - failed} .Z files")
