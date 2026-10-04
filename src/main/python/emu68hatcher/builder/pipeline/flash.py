"""flash stage - write built .img to a physical disk (IMG + flash mode only)"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.state import BuildStage, CreatedImage
from emu68hatcher.config.schema import OutputType

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow


def stage_flash(workflow: BuildWorkflow, image: CreatedImage) -> CreatedImage:
    """flash .img to flash_target; no-op for DEVICE mode or IMG mode without flash_target"""
    output = workflow.config.output
    if output is None or output.type != OutputType.IMG or not output.flash_target:
        return image

    workflow._update_state(BuildStage.FLASH, 0.0)
    workflow._milestone(f"Flashing image to {output.flash_target}")

    image_path = Path(image.image_path)
    if not image_path.exists():
        raise BuildError("image not found - cannot flash")

    from emu68hatcher.builder.host.disk_enum import find_disk
    from emu68hatcher.builder.host.disk_writer import flash_image_to_disk

    info = find_disk(output.flash_target)
    if info is None:
        raise BuildError(f"target {output.flash_target} is no longer present or not removable")
    if info.is_system_disk:
        raise BuildError(f"refusing to flash to system disk {output.flash_target}")

    from emu68hatcher.builder.host.disk_enum import unmount_disk

    result = unmount_disk(info, workflow.logger, elevation=workflow.state.elevation)
    if not result.success:
        raise BuildError(f"cannot prepare target {output.flash_target}: {result.error}")

    def progress_cb(pct: float, msg: str) -> None:
        workflow._update_state(progress=pct, message=msg)

    def cancel_predicate() -> bool:
        return workflow._cancelled

    # size-derived cap, same 1 MB/s floor finalize uses for fs copy. without it the timeout
    # defaults to None, which the elevated helper caps at 630s - a multi-GB write+verify blows
    # past that, aborting the build while the root worker keeps writing to the card
    image_bytes = image_path.stat().st_size
    flash_timeout = max(600.0, image_bytes / 1_048_576)

    flash_image_to_disk(
        image_path,
        output.flash_target,
        verify=output.verify_after_flash,
        skip_unused_sectors=True,  # huge saving on sparse images
        elevation=workflow.state.elevation,
        progress_callback=progress_cb,
        cancel_predicate=cancel_predicate,
        timeout=flash_timeout,
    )

    workflow._update_state(progress=100.0)
    verification = "verified written blocks" if output.verify_after_flash else "not verified"
    workflow._milestone(f"Flashed to {output.flash_target} ({verification})")
    return image
