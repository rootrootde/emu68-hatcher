"""Output target validation and build-long resource acquisition."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.errors import BuildError
from emu68hatcher.config.schema import OutputType

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow


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


def _validate_target_disk(info, required_size: int) -> None:
    if info.is_system_disk:
        raise BuildError(f"refusing to use system disk {info.device}")
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
            cancel_check=lambda: workflow._cancelled,
        )
        if getattr(result, "cancelled", False):
            workflow.logger.warning("hst-imager settings push cancelled by user")
        elif result.returncode == 0:
            workflow.logger.info("hst-imager settings pushed (AllPhysicalDrives=True)")
        else:
            workflow.logger.warning(
                f"could not push hst-imager settings: {result.stderr or result.stdout}"
            )
    except (subprocess.SubprocessError, OSError) as e:
        workflow.logger.warning(f"could not push hst-imager settings: {e}")
