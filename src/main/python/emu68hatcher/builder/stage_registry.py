"""Ordered build-stage registry."""

from collections.abc import Callable
from dataclasses import dataclass

from emu68hatcher.builder.pipeline import (
    stage_configure,
    stage_create_image,
    stage_download,
    stage_extract,
    stage_finalize,
    stage_flash,
    stage_import_ags,
    stage_install_extras,
    stage_install_packages,
    stage_install_workbench,
    stage_setup_workspace,
    stage_validate,
)
from emu68hatcher.builder.state import BuildStage


@dataclass(frozen=True)
class StageDefinition:
    stage: BuildStage
    label: str
    function: Callable
    include_in_progress: bool = True


PIPELINE_STAGES = (
    StageDefinition(BuildStage.VALIDATE, "Validating", stage_validate),
    StageDefinition(BuildStage.SETUP_WORKSPACE, "Setting up workspace", stage_setup_workspace),
    StageDefinition(BuildStage.DOWNLOAD, "Downloading", stage_download),
    StageDefinition(BuildStage.EXTRACT, "Extracting", stage_extract),
    StageDefinition(BuildStage.CREATE_IMAGE, "Creating Image", stage_create_image),
    StageDefinition(BuildStage.INSTALL_WORKBENCH, "Installing Workbench", stage_install_workbench),
    StageDefinition(BuildStage.INSTALL_PACKAGES, "Installing Packages", stage_install_packages),
    StageDefinition(BuildStage.CONFIGURE, "Configuring", stage_configure),
    StageDefinition(BuildStage.IMPORT_AGS, "Importing AGS", stage_import_ags),
    StageDefinition(BuildStage.INSTALL_EXTRAS, "Mirroring Extras", stage_install_extras),
    StageDefinition(BuildStage.FINALIZE, "Finalizing", stage_finalize),
    StageDefinition(BuildStage.FLASH, "Flashing to SD card", stage_flash),
)

STAGE_LABELS = {
    BuildStage.INIT.value: "Initializing",
    **{definition.stage.value: definition.label for definition in PIPELINE_STAGES},
    BuildStage.COMPLETE.value: "Complete",
    BuildStage.FAILED.value: "Failed",
}
PROGRESS_STAGE_ORDER = tuple(
    definition.stage.value for definition in PIPELINE_STAGES if definition.include_in_progress
)
