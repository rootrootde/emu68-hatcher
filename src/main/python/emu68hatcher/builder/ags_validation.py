"""AGS source, destination and capacity checks."""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.ags_requirements import AGSRoleRequirement, calculate_ags_requirements
from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.staging.tree_copy import measure_contained_tree
from emu68hatcher.config.ags_layout import (
    AGSResolvedTarget,
    resolve_ags_targets,
    validate_ags_layout,
)
from emu68hatcher.config.constants import MBR_OVERHEAD, RDB_OVERHEAD
from emu68hatcher.config.partition_helpers import usable_partition_content_size
from emu68hatcher.config.schema import OutputType

if TYPE_CHECKING:
    from emu68hatcher.builder.ags_scripts import AGSScriptPlan
    from emu68hatcher.builder.ags_source import AGSInventory
    from emu68hatcher.builder.workflow import BuildWorkflow
    from emu68hatcher.config.partition_models import PartitionConfig

_GIB = 1024**3


@dataclass(frozen=True, slots=True)
class AGSImportPlan:
    inventory: AGSInventory
    targets: tuple[AGSResolvedTarget, ...]
    requirements: tuple[AGSRoleRequirement, ...]
    script_plan: AGSScriptPlan

    def target(self, role: str) -> AGSResolvedTarget:
        for target in self.targets:
            if target.role == role:
                return target
        raise KeyError(role)


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


def check_elevated_source_access(workflow: BuildWorkflow, plan: AGSImportPlan) -> None:
    """Check source access through the build's elevated HST process."""
    from emu68hatcher.builder.host.hst_commands import HSTCommand, HSTCommandLine, hst_path
    from emu68hatcher.builder.host.hst_runner import HSTRunner

    if workflow.state.elevation is None:
        return
    workflow._check_cancelled()
    result = HSTRunner(cancel_check=lambda: workflow._cancelled).run_command(
        HSTCommandLine(HSTCommand.RDB_INFO, [hst_path(plan.inventory.source_path)]),
        elevation=workflow.state.elevation,
    )
    workflow._check_cancelled()
    if not result.success or " ERR]" in result.stdout or "Partitions:" not in result.stdout:
        raise BuildError(
            f"The elevated build process cannot read the AGS source image "
            f"{plan.inventory.source_path}. Move it to a folder accessible to the "
            "administrator process (on macOS, outside Downloads, Documents and Desktop), "
            f"then select and inspect it again. Details: {result.error or result.stdout}"
        )


def validate_ags_import(workflow: BuildWorkflow) -> AGSImportPlan:
    from emu68hatcher.builder.ags_scripts import prepare_ags_script_plan
    from emu68hatcher.builder.ags_source import inspect_ags_source

    selection = workflow.config.ags_import
    assert selection is not None and selection.enabled
    early_errors = validate_ags_layout(workflow.config)
    if early_errors:
        raise BuildError("AGS layout: " + "; ".join(early_errors))
    check_source_destination(workflow)
    partitions = workflow.config.partitions
    assert partitions is not None

    workflow._milestone("Inspecting AGS source image")
    selected = selection.components.selected_roles()
    inventory = inspect_ags_source(
        selection.source_image,
        selected,
        cancel_check=lambda: workflow._cancelled,
    )
    requirements = calculate_ags_requirements(inventory.components)
    current_errors = validate_ags_layout(
        workflow.config,
        {requirement.role: requirement for requirement in requirements},
    )
    if current_errors:
        raise BuildError("AGS layout: " + "; ".join(current_errors))
    targets = tuple(
        AGSResolvedTarget(
            target.role,
            target.device,
            target.volume,
            target.content_root,
            target.partition.model_copy(deep=True),
        )
        for target in resolve_ags_targets(workflow.config)
    )
    script_plan = prepare_ags_script_plan(inventory.profile, selected, inventory.source_scripts)
    plan = AGSImportPlan(inventory, targets, requirements, script_plan)
    extras_bytes = 0
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
    check_host_capacity(workflow, plan, extras_bytes)
    return plan


def _sparse_image_bytes(partitions: PartitionConfig, plan: AGSImportPlan, extras_bytes: int) -> int:
    copied_bytes = sum(requirement.minimum_partition_bytes for requirement in plan.requirements)
    normal = [part for part in partitions.iter_amiga_partitions() if not part.ags_reservation]
    content_capacity = sum(usable_partition_content_size(part.size) for part in normal)
    metadata_bytes = sum(part.size for part in normal) - content_capacity
    # ordinary files are staged; only AGS copies write every source partition block
    content_bytes = min(content_capacity, 2 * _GIB + extras_bytes)
    layout_bytes = MBR_OVERHEAD + sum(
        part.size if part.type == "fat32" else RDB_OVERHEAD for part in partitions.layout
    )
    return min(partitions.disk_size, copied_bytes + content_bytes + metadata_bytes + layout_bytes)


def check_host_capacity(
    workflow: BuildWorkflow, plan: AGSImportPlan, extras_bytes: int = 0
) -> None:
    output = workflow.config.output
    partitions = workflow.config.partitions
    assert output is not None and partitions is not None
    staging_fs = _workspace_filesystem()
    # packages, extracted media and the new Workbench also need temporary space
    ags_bytes = sum(requirement.estimated_host_bytes for requirement in plan.requirements)
    staging_bytes = ags_bytes + extras_bytes + 2 * _GIB
    requirements = {staging_fs.stat().st_dev: (staging_fs, staging_bytes)}
    if output.type == OutputType.IMG:
        output_fs = _existing_parent(Path(output.path).resolve().parent)
        image_bytes = partitions.disk_size
        if output.sparse:
            image_bytes = _sparse_image_bytes(partitions, plan, extras_bytes)
        copied_bytes = sum(requirement.minimum_partition_bytes for requirement in plan.requirements)
        workflow.logger.info(
            f"AGS space estimate: sparse={output.sparse}, "
            f"logical image={partitions.disk_size / _GIB:.2f} GiB, "
            f"full AGS copies={copied_bytes / _GIB:.2f} GiB, "
            f"output allocation={image_bytes / _GIB:.2f} GiB, "
            f"staging allowance={staging_bytes / _GIB:.2f} GiB"
        )
        device = output_fs.stat().st_dev
        previous = requirements.get(device, (output_fs, 0))
        destination_bytes = image_bytes
        # macOS may build in its workspace and move the image after flashing
        if output.flash_target and device != staging_fs.stat().st_dev:
            requirements[staging_fs.stat().st_dev] = (staging_fs, staging_bytes + image_bytes)
            # a cross-filesystem move can expand sparse holes at the destination
            destination_bytes = partitions.disk_size
        requirements[device] = (output_fs, previous[1] + destination_bytes)
    for path, required in requirements.values():
        free = shutil.disk_usage(path).free
        if free < required:
            raise BuildError(
                f"AGS build needs about {required / _GIB:.1f} GiB free on {path}; "
                f"only {free / _GIB:.1f} GiB is available (staging and output included)"
            )
