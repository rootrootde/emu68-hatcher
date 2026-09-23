"""AGS source, destination and capacity checks."""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.staging.tree_copy import measure_contained_tree
from emu68hatcher.config.partition_helpers import usable_partition_content_size
from emu68hatcher.config.schema import OutputType

if TYPE_CHECKING:
    from emu68hatcher.builder.ags_source import AGSInventory
    from emu68hatcher.builder.workflow import BuildWorkflow

_GIB = 1024**3
AGS_ASSIGNS = frozenset(
    {
        "whdload",
        "whd_games",
        "whd_demos",
        "ags_drive",
        "ags",
        "agsos",
        "scripts",
        "admin",
        "whdsaves",
    }
)


def _existing_parent(path: Path) -> Path:
    while not path.exists():
        if path.parent == path:
            raise BuildError(f"Cannot find a filesystem for {path}")
        path = path.parent
    return path


def _workspace_filesystem() -> Path:
    from emu68hatcher.utils.paths import get_cache_dir

    base = get_cache_dir()
    if os.name == "nt" and base.drive.startswith("\\\\"):
        base = Path(tempfile.gettempdir())
    return _existing_parent(base)


def _macos_source_devices(source: Path) -> set[str]:
    mount = source.parent
    while not mount.is_mount() and mount.parent != mount:
        mount = mount.parent
    try:
        result = subprocess.run(
            ["diskutil", "info", "-plist", str(mount)], capture_output=True, timeout=10
        )
        if result.returncode:
            raise ValueError("diskutil could not identify the source volume")
        info = plistlib.loads(result.stdout)
        if info.get("FilesystemType") == "apfs":
            stores = [entry["APFSPhysicalStore"] for entry in info["APFSPhysicalStores"]]
        else:
            stores = [info["ParentWholeDisk"]]
        devices = set()
        for store in stores:
            match = re.fullmatch(r"(disk\d+)(?:s\d+)*", store)
            if match is None:
                raise ValueError("unrecognized physical store")
            devices.add(f"/dev/{match[1]}")
        if not devices:
            raise ValueError("no physical store found")
        return devices
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as error:
        raise BuildError(
            "Cannot identify the disk containing the AGS source. "
            "Build an image without flashing, or move the source to a local disk."
        ) from error


def check_source_destination(workflow: BuildWorkflow) -> None:
    config = workflow.config
    selection = config.ags_import
    assert selection is not None
    source = selection.source_image.resolve()
    output = config.output
    if output is None:
        raise BuildError("Output configuration not specified")
    if output.type == OutputType.IMG:
        destination = Path(output.path).resolve()
        if source == destination or (
            source.exists() and destination.exists() and source.samefile(destination)
        ):
            raise BuildError("AGS source and output image must be different files")

    devices = [output.flash_target] if output.flash_target else []
    if output.type == OutputType.DEVICE:
        devices.append(str(output.path))
    if devices:
        from emu68hatcher.builder.host.disk_enum import find_disk
        from emu68hatcher.utils.platform import OperatingSystem, get_platform_info

        if get_platform_info().os == OperatingSystem.MACOS:
            source_devices = _macos_source_devices(source)
            if source_devices.intersection(devices):
                raise BuildError("AGS source is stored on the selected output or flash disk")
        for device in devices:
            info = find_disk(device)
            if info is None:
                raise BuildError(f"Cannot inspect AGS output disk {device}")
            for mount in info.mounted_partitions:
                if source.is_relative_to(Path(mount).resolve()):
                    raise BuildError(f"AGS source is stored on the output disk {device}")


def validate_ags_import(workflow: BuildWorkflow) -> AGSInventory:
    from emu68hatcher.builder.ags_source import inspect_ags_source

    selection = workflow.config.ags_import
    assert selection is not None
    check_source_destination(workflow)
    partitions = workflow.config.partitions
    if partitions is None:
        raise BuildError("AGS import requires a partition layout")
    target = None
    for part in partitions.iter_amiga_partitions():
        if {part.device.lower(), part.volume.lower()} & AGS_ASSIGNS:
            raise BuildError(f"AGS assign conflicts with partition {part.device}: {part.volume}")
        if part.device == selection.content_device:
            target = part
    if target is None or target.no_mount:
        raise BuildError(
            f"AGS content device {selection.content_device} must be a mounted partition"
        )

    workflow._milestone("Inspecting AGS source image")
    inventory = inspect_ags_source(selection.source_image, cancel_check=lambda: workflow._cancelled)
    baseline = _GIB if target.bootable else 0
    extras_bytes = 0
    target_extras = 0
    for part in partitions.iter_amiga_partitions():
        workflow._check_cancelled()
        extra = part.extra_content_directory
        if extra is not None and extra.is_dir():
            try:
                usage = measure_contained_tree(extra, cancel_check=lambda: workflow._cancelled)
            except InterruptedError:
                workflow._check_cancelled()
                raise
            except OSError as error:
                raise BuildError(
                    f"Cannot inspect AGS extra content on {part.device}: {error}"
                ) from error
            extras_bytes += usage.estimated_bytes
            if part.device == target.device:
                target_extras = usage.estimated_bytes
    needed = inventory.required_bytes + baseline + target_extras
    if needed > usable_partition_content_size(target.size):
        raise BuildError(
            f"AGS and other content need at least {needed / _GIB:.1f} GiB "
            f"on {target.device}; configured size is {target.size / _GIB:.1f} GiB"
        )
    check_host_capacity(workflow, inventory, extras_bytes)
    return inventory


def check_host_capacity(
    workflow: BuildWorkflow, inventory: AGSInventory, extras_bytes: int = 0
) -> None:
    output = workflow.config.output
    partitions = workflow.config.partitions
    assert output is not None and partitions is not None
    staging_fs = _workspace_filesystem()
    # packages, extracted media and the new Workbench also need temporary space
    staging_bytes = inventory.required_bytes + extras_bytes + 2 * _GIB
    requirements = {staging_fs.stat().st_dev: (staging_fs, staging_bytes)}
    if output.type == OutputType.IMG:
        output_fs = _existing_parent(Path(output.path).resolve().parent)
        image_bytes = (
            inventory.required_bytes + extras_bytes + _GIB
            if output.sparse
            else partitions.disk_size
        )
        device = output_fs.stat().st_dev
        previous = requirements.get(device, (output_fs, 0))
        requirements[device] = (output_fs, previous[1] + image_bytes)
        # macOS may build in its workspace and move the image after flashing
        if output.flash_target and device != staging_fs.stat().st_dev:
            requirements[staging_fs.stat().st_dev] = (staging_fs, staging_bytes + image_bytes)
    for path, required in requirements.values():
        free = shutil.disk_usage(path).free
        if free < required:
            raise BuildError(
                f"AGS build needs about {required / _GIB:.1f} GiB free on {path}; "
                f"only {free / _GIB:.1f} GiB is available (staging and output included)"
            )
