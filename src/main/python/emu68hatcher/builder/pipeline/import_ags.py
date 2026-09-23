"""Import the supported AGS WHDLoad volume into staging."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.ags_source import (
    AGSInventory,
    _entry,
    iter_hst_entries,
    run_hst_to_file,
    validate_source_identity,
)
from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.staging.files import ci_match_child, resolve_staging_path
from emu68hatcher.builder.state import BuildStage, CreatedImage
from emu68hatcher.config.partition_helpers import usable_partition_content_size

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow


def _staging_paths(workflow: BuildWorkflow, image: CreatedImage) -> tuple[Path, Path, Path]:
    selection = workflow.config.ags_import
    assert selection is not None
    staging = image.workspace.staging_dir
    content = resolve_staging_path(staging, selection.content_device)
    boot = resolve_staging_path(staging, workflow.config.boot_device)
    ags = resolve_staging_path(content, "AGS")
    return boot, content, ags


def _host_listing(root: Path, output: Path, workflow: BuildWorkflow) -> dict[str, object]:
    run_hst_to_file(
        ["fs", "dir", str(root), "--recursive", "--format", "Json", "--uaemetadata", "UaeFsDb"],
        output,
        lambda: workflow._cancelled,
    )
    entries = {}
    for raw in iter_hst_entries(output):
        entry = _entry(raw, host_defaults=True)
        key = entry.path.lower()
        if key in entries:
            raise BuildError(f"AGS staging contains colliding names: {entry.path}")
        entries[key] = entry
    return entries


def _verify_copy(
    workflow: BuildWorkflow, inventory: AGSInventory, root: Path, output: Path
) -> None:
    copied = _host_listing(root, output, workflow)
    if len(copied) != len(inventory.entries):
        raise BuildError(
            f"AGS copy is incomplete: {len(copied)} of {len(inventory.entries)} entries found"
        )
    for index, expected in enumerate(inventory.entries):
        if index % 4096 == 0:
            workflow._check_cancelled()
        actual = copied.pop(expected.path.lower(), None)
        if actual is None or actual != expected:
            raise BuildError(f"AGS copy differs from source at {expected.path}")
    if copied:
        raise BuildError("AGS copy contains unexpected files")


def _staging_usage(root: Path, workflow: BuildWorkflow) -> tuple[int, int]:
    total_bytes = 0
    entry_count = 0
    for path in root.rglob("*"):
        if entry_count % 4096 == 0:
            workflow._check_cancelled()
        if path.is_symlink():
            raise BuildError(f"AGS staging contains a symbolic link: {path}")
        entry_count += 1
        if path.is_file():
            total_bytes += path.stat().st_size
    return total_bytes, entry_count


def _check_target_capacity(workflow: BuildWorkflow, content_dir: Path, boot_dir: Path) -> None:
    selection = workflow.config.ags_import
    partitions = workflow.config.partitions
    assert selection is not None and partitions is not None
    target = next(
        (
            part
            for part in partitions.iter_amiga_partitions()
            if part.device == selection.content_device
        ),
        None,
    )
    if target is None:
        raise BuildError(f"AGS target device {selection.content_device} is missing")
    bytes_used, entries = _staging_usage(content_dir, workflow)
    required = ((bytes_used + entries * 8192) * 11 + 9) // 10 + 512 * 1024**2
    if required > usable_partition_content_size(target.size):
        raise BuildError(
            f"AGS staging needs about {required / 1024**3:.1f} GiB on {target.device}; "
            f"partition size is {target.size / 1024**3:.1f} GiB"
        )
    if workflow.config.boot_device != selection.content_device:
        boot = next(
            (
                part
                for part in partitions.iter_amiga_partitions()
                if part.device == workflow.config.boot_device
            ),
            None,
        )
        if boot is None:
            raise BuildError(f"AGS boot device {workflow.config.boot_device} is missing")
        boot_bytes, boot_entries = _staging_usage(boot_dir, workflow)
        boot_required = ((boot_bytes + boot_entries * 8192) * 11 + 9) // 10 + 128 * 1024**2
        if boot_required > usable_partition_content_size(boot.size):
            raise BuildError(
                f"AGS boot staging needs about {boot_required / 1024**3:.1f} GiB "
                f"on {boot.device}; partition size is {boot.size / 1024**3:.1f} GiB"
            )


def _check_before_copy(
    workflow: BuildWorkflow, inventory: AGSInventory, content_dir: Path, ags_dir: Path
) -> None:
    selection = workflow.config.ags_import
    partitions = workflow.config.partitions
    assert selection is not None and partitions is not None
    target = next(
        (
            part
            for part in partitions.iter_amiga_partitions()
            if part.device == selection.content_device
        ),
        None,
    )
    if target is None:
        raise BuildError(f"AGS target device {selection.content_device} is missing")
    existing_bytes, existing_entries = _staging_usage(content_dir, workflow)
    needed = inventory.required_bytes + ((existing_bytes + existing_entries * 8192) * 11 + 9) // 10
    if needed > usable_partition_content_size(target.size):
        raise BuildError(
            f"AGS needs about {needed / 1024**3:.1f} GiB on {target.device}; "
            f"partition size is {target.size / 1024**3:.1f} GiB"
        )
    free = shutil.disk_usage(ags_dir).free
    if free < inventory.required_bytes:
        raise BuildError(
            f"AGS import needs about {inventory.required_bytes / 1024**3:.1f} GiB free "
            f"for staging; only {free / 1024**3:.1f} GiB remains"
        )


def stage_import_ags(workflow: BuildWorkflow, image: CreatedImage) -> CreatedImage:
    workflow._update_state(BuildStage.IMPORT_AGS, 0.0)
    inventory = image.workspace.validated.ags_inventory
    selection = workflow.config.ags_import
    if selection is None:
        workflow._update_state(progress=100.0)
        return image
    if inventory is None or selection.scope != "whdload" or inventory.profile != "v30":
        raise BuildError("Only the AGS v30 WHDLoad import is supported")
    validate_source_identity(inventory)
    boot_dir, content_dir, ags_dir = _staging_paths(workflow, image)
    if ci_match_child(ags_dir, "WHDLoad") is not None:
        raise BuildError(f"AGS/WHDLoad already exists on {selection.content_device}")
    workflow._milestone(f"Importing AGS v30 WHDLoad to {selection.content_device}")
    ags_dir.mkdir(parents=True, exist_ok=True)
    boot_dir.mkdir(parents=True, exist_ok=True)
    _check_before_copy(workflow, inventory, content_dir, ags_dir)
    with tempfile.TemporaryDirectory(prefix=".WHDLoad-import-", dir=ags_dir) as temporary:
        staged = Path(temporary)
        with tempfile.TemporaryDirectory(prefix="ags-import-log-") as log_dir:
            log = Path(log_dir)
            source = f"{inventory.source_path.as_posix()}/rdb/{inventory.whdload.index}/*"
            run_hst_to_file(
                [
                    "fs",
                    "copy",
                    source,
                    str(staged),
                    "--recursive",
                    "TRUE",
                    "--makedir",
                    "TRUE",
                    "--uaemetadata",
                    "UaeFsDb",
                ],
                log / "copy.log",
                lambda: workflow._cancelled,
            )
            workflow._update_state(progress=75.0)
            validate_source_identity(inventory)
            workflow._milestone("Checking AGS files and Amiga metadata")
            _verify_copy(workflow, inventory, staged, log / "copied.json")
            from emu68hatcher.builder.ags_scripts import adapt_ags

            adapt_ags(
                staged,
                boot_dir,
                selection.content_device,
                inventory.profile,
                cancel_check=lambda: workflow._cancelled,
            )
            workflow._check_cancelled()
            _check_target_capacity(workflow, content_dir, boot_dir)
            os.rename(staged, ags_dir / "WHDLoad")
    workflow._update_state(progress=100.0)
    workflow._milestone(f"Imported {inventory.file_count:,} AGS files")
    return image


def verify_ags_staging(workflow: BuildWorkflow, image: CreatedImage) -> None:
    inventory = image.workspace.validated.ags_inventory
    if inventory is None:
        return
    boot_dir, content_dir, ags_dir = _staging_paths(workflow, image)
    imported = resolve_staging_path(ags_dir, "WHDLoad")
    required_files = (
        resolve_staging_path(boot_dir, "Emu68-Hatcher/AGS/Start_AGS"),
        resolve_staging_path(boot_dir, "Emu68-Hatcher/AGS/Start_AGS.info"),
        resolve_staging_path(boot_dir, "Emu68-Hatcher/AGS/C/Ex"),
        resolve_staging_path(boot_dir, "Emu68-Hatcher/AGS/C/BastyPlayer"),
        resolve_staging_path(boot_dir, "Emu68-Hatcher/AGS/C/kgiconload"),
        resolve_staging_path(imported, "AGS2/ags2"),
        resolve_staging_path(imported, "AGS2/ags2menu"),
        resolve_staging_path(imported, "AGS2/AGS2.conf"),
        resolve_staging_path(boot_dir, "C/WHDLoad"),
        resolve_staging_path(boot_dir, "C/IconX"),
        resolve_staging_path(boot_dir, "S/WHDLoad.prefs"),
    )
    for path in required_files:
        if not path.is_file() or path.stat().st_size == 0:
            raise BuildError(f"Required AGS launcher file is missing: {path}")
    launcher = required_files[0].read_bytes()
    icon = required_files[1].read_bytes()
    if b"AGS:ags2" not in launcher or b"\r\n" in launcher:
        raise BuildError("AGS launcher is not a valid IconX script")
    if len(icon) < 78 or icon[:2] != b"\xe3\x10" or icon[48] != 4 or b"iconx" not in icon.lower():
        raise BuildError("AGS launcher icon is not an IconX project icon")
    for index in (2, 3, 4, 5, 6, 8, 9):
        if required_files[index].read_bytes()[:4] != b"\0\0\x03\xf3":
            raise BuildError(f"AGS requires an Amiga executable: {required_files[index]}")
    for folder in ("Game", "Demo", "Beta", "Magazine"):
        path = resolve_staging_path(imported, folder)
        if not path.is_dir():
            raise BuildError(f"Required AGS content is missing: {path}")
    if not resolve_staging_path(ags_dir, "WHDSaves").is_dir():
        raise BuildError("AGS save directory is missing")
    _check_target_capacity(workflow, content_dir, boot_dir)
