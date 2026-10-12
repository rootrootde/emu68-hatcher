"""WHDLoad wrapper state at build time."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.staging.files import resolve_staging_path

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow


# the wrapper's EmuControl settings disturb the RGB2RTG picture, so RGB2RTG builds start with
# the plain WHDLoad in C:; S:WHDLoadWrapper-Switch (Games menu) swaps the two at any time
def configure_whdload_wrapper(workflow: BuildWorkflow, boot_staging: Path, all_packages) -> None:
    if "whdloadwrapper" not in all_packages or not workflow.config.rgb2rtg.enabled:
        return
    plain = resolve_staging_path(boot_staging, "C/WHDLoad.ori")
    if not plain.is_file():
        workflow.logger.warning("C/WHDLoad.ori is not staged; the WHDLoad wrapper stays on")
        return
    shutil.copyfile(plain, resolve_staging_path(boot_staging, "C/WHDLoad"))
    workflow.logger.info(
        "RGB2RTG: WHDLoad wrapper installed but off, C:WHDLoad is the plain WHDLoad"
    )
