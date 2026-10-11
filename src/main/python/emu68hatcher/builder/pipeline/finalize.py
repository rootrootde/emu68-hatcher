"""finalize stage - copy staged files into the image"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.state import BuildStage, CreatedImage
from emu68hatcher.config.defaults import EMU68_BOOT_PARTITION_NAME

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow


def stage_finalize(workflow: BuildWorkflow, image: CreatedImage) -> CreatedImage:
    """copy staged files into the image"""
    from emu68hatcher.config.schema import OutputType

    workflow._update_state(BuildStage.FINALIZE, 0.0)
    workflow._milestone("Finalizing")
    if workflow.config.ags_import is not None and workflow.config.ags_import.enabled:
        from emu68hatcher.builder.pipeline.import_ags import verify_ags_staging

        verify_ags_staging(workflow, image)

    # after install_extras, before the copy: the receipts describe the image's bytes
    from emu68hatcher.builder.pipeline.build_records import write_build_records

    write_build_records(workflow, image)

    output = workflow.config.output
    assert output is not None
    # windows physical-drive paths lie in exists() once the disk is offline; in DEVICE mode
    # the path is meaningful regardless of fs state
    if output.type != OutputType.DEVICE and not Path(image.image_path).exists():
        raise BuildError("Disk image not found")

    workflow._update_state(progress=10.0)
    workflow._milestone("Copying staged files to image")
    _copy_staged_files_to_image(workflow, image)

    from emu68hatcher.builder.pipeline.import_ags import verify_ags_target

    verify_ags_target(workflow, image)
    workflow._update_state(progress=90.0)
    workflow._milestone("Cleaning up")
    if image.workspace.work_dir.exists():
        # keep the image file, clean up work dirs
        for subdir in ["staging", "downloads", "extracted", "workbench"]:
            cleanup_path = image.workspace.work_dir / subdir
            if cleanup_path.exists():
                shutil.rmtree(cleanup_path, ignore_errors=True)

    workflow._update_state(progress=100.0)
    workflow._milestone("Build complete")
    return image


def _ensure_device_unmounted(workflow: BuildWorkflow) -> None:
    """re-unmount before fs copy - windows auto-mounts the new fat32 and locks the raw disk"""
    from emu68hatcher.config.schema import OutputType

    if workflow.config.output is None or workflow.config.output.type != OutputType.DEVICE:
        return
    from emu68hatcher.builder.host.disk_enum import find_disk, unmount_disk

    info = find_disk(str(workflow.config.output.path))
    if info is None:
        raise BuildError(f"target {workflow.config.output.path} is no longer present")
    result = unmount_disk(info, workflow.logger, elevation=workflow.state.elevation)
    if not result.success:
        raise BuildError(f"cannot prepare target {workflow.config.output.path}: {result.error}")


def _copy_staged_files_to_image(workflow: BuildWorkflow, image: CreatedImage) -> None:
    from emu68hatcher.builder.host.hst_runner import HSTRunner
    from emu68hatcher.config.schema import OutputType

    if (
        workflow.config.output is not None
        and workflow.config.output.type != OutputType.DEVICE
        and not Path(image.image_path).exists()
    ):
        workflow.logger.warning("No image file found, skipping file copy")
        return

    runner = HSTRunner(cancel_check=lambda: workflow._cancelled)

    if not runner.is_available():
        workflow.logger.warning("HST Imager not available, skipping file copy")
        return

    image_path = image.image_path
    posix = image_path.as_posix() if isinstance(image_path, Path) else image_path
    workflow.logger.info(f"finalize: image path: raw={image_path!s} posix={posix}")
    device_to_mbr = _build_device_map(workflow)
    if EMU68_BOOT_PARTITION_NAME not in device_to_mbr:
        raise BuildError("partition layout has no FAT32 boot partition")
    workflow.logger.info(f"finalize: device->MBR mapping: {device_to_mbr}")
    _ensure_device_unmounted(workflow)

    devices_copied = 0
    devices_failed = 0
    for device_dir in image.workspace.staging_dir.iterdir():
        if not device_dir.is_dir():
            continue
        workflow._check_cancelled()
        from emu68hatcher.builder.staging.hst_metadata import prepare_workbench_metadata

        reserved = image.workspace.validated.ags_plan
        is_ags_overlay = bool(
            reserved and any(t.device == device_dir.name for t in reserved.targets)
        )
        if not is_ags_overlay and device_dir.name != EMU68_BOOT_PARTITION_NAME:
            prepare_workbench_metadata(
                device_dir,
                cancel_check=lambda: workflow._cancelled,
                allow_stale_metadata=device_dir.name == workflow.config.boot_device,
            )
        file_count, total_bytes = _staging_inventory(device_dir)
        if file_count == 0:
            workflow.logger.info(f"Skipping empty staging directory: {device_dir.name}")
            continue
        copied = _copy_staging_device(
            workflow,
            image,
            runner,
            device_dir,
            device_to_mbr,
            file_count,
            total_bytes,
            use_metafiles=device_dir.name != EMU68_BOOT_PARTITION_NAME,
        )
        if copied:
            devices_copied += 1
        else:
            devices_failed += 1

    workflow.logger.info(f"Copied files to {devices_copied} partitions ({devices_failed} failed)")
    if devices_failed:
        raise BuildError(
            f"{devices_failed} partition(s) failed to copy - the image is not bootable"
        )


def _build_device_map(workflow: BuildWorkflow) -> dict[str, int]:
    mapping: dict[str, int] = {}
    if not workflow.config.partitions:
        return mapping
    for index, mbr_part in enumerate(workflow.config.partitions.layout, start=1):
        if mbr_part.type == "fat32":
            mapping[EMU68_BOOT_PARTITION_NAME] = index
        elif mbr_part.type == "id76" and mbr_part.amiga_partitions:
            for amiga_part in mbr_part.amiga_partitions:
                mapping[amiga_part.device] = index
    return mapping


def _staging_inventory(device_dir: Path) -> tuple[int, int]:
    file_count = 0
    total_bytes = 0
    for path in device_dir.rglob("*"):
        if path.is_file():
            file_count += 1
            total_bytes += path.stat().st_size
    return file_count, total_bytes


def _copy_staging_device(
    workflow: BuildWorkflow,
    image: CreatedImage,
    runner,
    device_dir: Path,
    device_to_mbr: dict[str, int],
    file_count: int,
    total_bytes: int,
    *,
    use_metafiles: bool = False,
) -> bool:
    from emu68hatcher.builder.host.hst_commands import HSTCommand, HSTCommandLine, hst_path

    device_name = device_dir.name
    mbr_num = device_to_mbr.get(device_name)
    if mbr_num is None:
        raise BuildError(f"staging device {device_name} is absent from the partition layout")
    if device_name == EMU68_BOOT_PARTITION_NAME:
        destination = hst_path(image.image_path, "mbr", mbr_num)
    else:
        destination = hst_path(image.image_path, "mbr", mbr_num, "rdb", device_name)
    args = [
        f"{device_dir.as_posix()}/*",
        destination,
        "--makedir",
        "TRUE",
        "--recursive",
        "TRUE",
        "--force",
        "TRUE",
    ]
    if device_name != EMU68_BOOT_PARTITION_NAME:
        args.extend(["--uaemetadata", "UaeMetafile" if use_metafiles else "UaeFsDb"])
    command = HSTCommandLine(
        command=HSTCommand.FS_COPY,
        args=args,
        description=f"Copy files to {device_name}",
    )
    workflow.logger.info(f"finalize: {device_name} dest: {destination!r}")
    workflow.logger.info(f"Running: {command.to_string()}")
    copy_timeout = max(300.0, total_bytes / 1_048_576)
    start_time = time.time()
    result = runner.run_command(
        command,
        timeout=copy_timeout,
        elevation=workflow.state.elevation,
    )
    duration_ms = int((time.time() - start_time) * 1000)
    if result.success:
        workflow.logger.info(
            f"Copied {file_count} files ({total_bytes:,} bytes) to {device_name} in {duration_ms}ms"
        )
        return True
    workflow.logger.error(f"Failed to copy files to {device_name}: {result.error}")
    if result.stdout:
        workflow.logger.error(f"stdout: {result.stdout}")
    if result.stderr:
        workflow.logger.error(f"stderr: {result.stderr}")
    return False
