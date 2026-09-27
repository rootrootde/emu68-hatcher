"""Stage small additions for the AGS portable launcher."""

from __future__ import annotations

import hashlib
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from emu68hatcher.builder.ags_source import validate_source_identity
from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.staging.files import resolve_staging_path
from emu68hatcher.builder.state import BuildStage


def stage_import_ags(workflow, image):
    workflow._update_state(BuildStage.IMPORT_AGS, 0.0)
    plan = image.workspace.validated.ags_plan
    if plan is None:
        workflow._update_state(progress=100.0)
        return image
    from emu68hatcher.builder.ags_scripts import prepare_ags_launcher

    started = time.monotonic()
    validate_source_identity(plan.inventory)
    staging = image.workspace.staging_dir
    boot = resolve_staging_path(staging, workflow.config.boot_device)
    from emu68hatcher.builder.ags_source import run_hst_to_file

    with tempfile.TemporaryDirectory(prefix="ags-portable-") as temporary:
        for name in ("Ex", "kgiconload", "WBLoad", "WBRun"):
            destination = resolve_staging_path(boot, f"C/{name}")
            if destination.is_file():
                continue
            source = (
                f"{plan.inventory.source_path.as_posix()}/rdb/{plan.inventory.whdload.index}"
                f"/AGS2/OS/C/{name}"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            run_hst_to_file(
                ["fs", "copy", source, str(destination.parent), "--uaemetadata", "UaeFsDb"],
                Path(temporary) / f"helper-{name}.log",
                lambda: workflow._cancelled,
            )
            if not resolve_staging_path(boot, f"C/{name}").is_file():
                raise BuildError(f"AGS source is missing helper {name}")
    launcher = prepare_ags_launcher(plan, boot, cancel_check=lambda: workflow._cancelled)
    validate_source_identity(plan.inventory)
    workflow._update_state(progress=100.0)
    workflow._milestone(f"AGS portable launcher prepared in {time.monotonic() - started:.2f}s")
    return replace(image, ags_launcher=launcher)


def verify_ags_staging(workflow, image) -> None:
    plan = image.workspace.validated.ags_plan
    if plan is None:
        return
    launcher = image.ags_launcher
    if launcher is None:
        raise BuildError("AGS portable launcher preparation is missing")
    for device, relative, digest in launcher.verified_files:
        if device != "__boot__":
            continue
        device = workflow.config.boot_device
        path = resolve_staging_path(image.workspace.staging_dir, f"{device}/{relative}")
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise BuildError(f"AGS staged portable file differs: {device}:{relative}")
    boot = resolve_staging_path(image.workspace.staging_dir, workflow.config.boot_device)
    for relative in ("C/WHDLoad", "C/IconX", "S/WHDLoad.prefs"):
        if not resolve_staging_path(boot, relative).is_file():
            raise BuildError(f"AGS requires {relative} in the staged Workbench")


def verify_ags_target(workflow, image) -> None:
    plan = image.workspace.validated.ags_plan
    if plan is None:
        return
    from emu68hatcher.builder.ags_blocks import verify_target_partitions
    from emu68hatcher.builder.host.hst_commands import HSTCommand, HSTCommandLine, hst_path
    from emu68hatcher.builder.host.hst_runner import HSTRunner
    from emu68hatcher.builder.pipeline.finalize import _build_device_map

    runner = HSTRunner(cancel_check=lambda: workflow._cancelled)
    verify_target_partitions(workflow, runner, plan, image.image_path)
    mapping = _build_device_map(workflow)
    with tempfile.TemporaryDirectory(prefix="ags-verify-") as temporary:
        for number, (device, relative, digest) in enumerate(image.ags_launcher.verified_files):
            workflow._check_cancelled()
            device = workflow.config.boot_device if device == "__boot__" else device
            destination = Path(temporary) / str(number)
            destination.mkdir()
            source = hst_path(
                image.image_path, "mbr", mapping[device], "rdb", device, *relative.split("/")
            )
            result = runner.run_command(
                HSTCommandLine(
                    HSTCommand.FS_COPY, [source, str(destination), "--uaemetadata", "None"]
                ),
                elevation=workflow.state.elevation,
            )
            copied = destination / Path(relative).name
            if (
                not result.success
                or not copied.is_file()
                or hashlib.sha256(copied.read_bytes()).hexdigest() != digest
            ):
                raise BuildError(f"AGS target portable file differs: {device}:{relative}")
