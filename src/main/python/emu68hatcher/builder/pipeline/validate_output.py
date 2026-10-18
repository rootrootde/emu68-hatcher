"""Output target validation and build-long resource acquisition."""

from __future__ import annotations

import errno
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.errors import BuildError
from emu68hatcher.config.schema import OutputType

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow

_WRITE_PROTECTED = (
    "{device} reports itself read-only. Either the lock switch on the side of the SD card "
    "or its adapter is set to LOCK, or on Windows the disk has its read-only attribute set. "
    "Slide the switch away from LOCK and reinsert the card, or clear the attribute in "
    "diskpart (select disk <number>, then attributes disk clear readonly), and try again."
)
_FAT32_MAX_FILE = 2**32 - 1


def validate_output_target(workflow: BuildWorkflow) -> None:
    from emu68hatcher.builder.host.disk_enum import find_disk

    output = workflow.config.output
    partitions = workflow.config.partitions
    if output is None:
        raise BuildError("Output configuration not specified")
    if partitions is None:
        raise BuildError("Partition configuration not specified")

    required_size = partitions.disk_size
    if output.type == OutputType.IMG:
        out_path = Path(output.path)
        out_str = str(out_path)
        if out_str.startswith("\\\\") and not out_str.startswith("\\\\.\\"):
            raise BuildError(
                "Output image is on a network share (UNC path). hst-imager cannot write the "
                "partition layout there - pick a local folder for the .img file."
            )
        if not out_path.parent.exists():
            raise BuildError(f"Output directory not found: {out_path.parent}")
        _check_file_size_limit(out_path.parent, required_size)
        if output.flash_target:
            if output.verify_after_flash:
                from emu68hatcher.builder.host.disk_writer import check_flash_verification_support

                check_flash_verification_support()
            info = find_disk(output.flash_target)
            if info is None:
                raise BuildError(
                    f"Flash target {output.flash_target} not found among removable disks. "
                    "Insert the SD card and refresh, or pick a different target."
                )
            _validate_target_disk(info, required_size)
            _acquire_for_workflow(workflow)
            _claim_macos_disk(workflow, output.flash_target)
        return

    info = find_disk(str(output.path))
    if info is None:
        raise BuildError(
            f"Target device {output.path} not found among removable disks. "
            "Insert the SD card and refresh, or pick a different device."
        )
    _validate_target_disk(info, required_size)
    _acquire_for_workflow(workflow)
    _claim_macos_disk(workflow, str(output.path))


def _check_file_size_limit(folder: Path, required_size: int) -> None:
    """FAT32 holds files up to 4 GiB; probe the folder before downloads, not in create_image"""
    if required_size <= _FAT32_MAX_FILE:
        return
    probe = folder / ".emu68hatcher-size-probe"
    try:
        with open(probe, "wb") as handle:
            handle.truncate(_FAT32_MAX_FILE + 1)
    except OSError as error:
        # a full disk is not the size limit; create_image reports that one
        if error.errno == errno.ENOSPC:
            return
        raise BuildError(
            f"{folder} cannot hold the {required_size / 1024**3:.1f} GiB image ({error}). "
            "The volume is most likely FAT32, which stores files up to 4 GiB only. "
            "Choose a folder on an APFS, NTFS, ext4 or exFAT volume."
        ) from error
    finally:
        probe.unlink(missing_ok=True)


def _validate_target_disk(info, required_size: int) -> None:
    if info.is_system_disk:
        raise BuildError(f"refusing to use system disk {info.device}")
    if info.is_read_only:
        raise BuildError(_WRITE_PROTECTED.format(device=info.device))
    if info.size_bytes < required_size:
        raise BuildError(
            f"target {info.device} is {info.size_bytes:,} bytes "
            f"({info.size_human}); configured disk_size is {required_size:,} bytes. "
            "Pick a larger card or shrink the layout."
        )


def _claim_macos_disk(workflow: BuildWorkflow, device: str) -> None:
    from emu68hatcher.utils.platform import OperatingSystem, get_platform_info

    if get_platform_info().os != OperatingSystem.MACOS:
        return
    from emu68hatcher.builder.host.disk_claim import claim_macos_disk

    claim = claim_macos_disk(device)
    if claim is None:
        workflow.logger.warning(
            f"could not claim {device} via DiskArbitration; the 'disk not readable' "
            "dialog may pop mid-build (click Ignore if it does)"
        )
        return
    workflow.state.disk_claim = claim
    workflow.logger.info(f"DiskArbitration claim held on {device}")


def _acquire_for_workflow(workflow: BuildWorkflow) -> None:
    from emu68hatcher.builder.host.elevation import (
        ElevationDenied,
        acquire_elevation,
        run_elevated,
    )
    from emu68hatcher.utils.host_tools import find_hst_imager

    if workflow.state.elevation is not None:
        return
    try:
        workflow.state.elevation = acquire_elevation()
        workflow.logger.info(f"acquired elevation token via {workflow.state.elevation.method}")
    except ElevationDenied as e:
        raise BuildError(f"admin access required to write to a physical disk: {e}") from e

    hst = find_hst_imager()
    if not hst:
        return
    command = [
        str(hst),
        "settings",
        "update",
        "--all-physical-drives",
        "--skip-unused-sectors",
        "--sparse-files",
    ]
    try:
        result = run_elevated(
            command,
            workflow.state.elevation,
            timeout=30,
            cancel_check=workflow.is_cancelled,
        )
        if result.cancelled:
            workflow.logger.warning("hst-imager settings push cancelled by user")
        elif result.returncode == 0:
            workflow.logger.info("hst-imager settings pushed (AllPhysicalDrives=True)")
        else:
            workflow.logger.warning(
                f"could not push hst-imager settings: {result.stderr or result.stdout}"
            )
    except (subprocess.SubprocessError, OSError) as e:
        workflow.logger.warning(f"could not push hst-imager settings: {e}")
