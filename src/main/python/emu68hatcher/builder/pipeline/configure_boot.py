"""configure phase 2: copy Emu68 boot files, Kickstart ROM, and generate config.txt/cmdline.txt"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.staging.scripts.generator import generate_boot_partition_files
from emu68hatcher.builder.state import CreatedImage
from emu68hatcher.config.defaults import EMU68_BOOT_PARTITION_NAME
from emu68hatcher.utils.paths import ensure_dir

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow


def configure_boot_partition(workflow: BuildWorkflow, image: CreatedImage) -> None:
    """copy Emu68 boot files, ROM, and generate config.txt/cmdline.txt."""
    emu68_boot_staging = image.workspace.staging_dir / EMU68_BOOT_PARTITION_NAME
    ensure_dir(emu68_boot_staging)

    workflow._update_state(progress=45.0)
    workflow._milestone("Copying Emu68 boot files")
    _copy_emu68_boot_files(workflow, image, emu68_boot_staging)

    workflow._update_state(progress=55.0)
    workflow._milestone("Copying Kickstart ROM to boot partition")
    rom_filename = _copy_kickstart_rom(workflow, image, emu68_boot_staging)

    workflow._update_state(progress=60.0)
    workflow._milestone("Generating Emu68 boot config")
    _generate_boot_config(workflow, image, rom_filename)

    # marker file so anyone inspecting the SD card knows which tool built it
    _write_hatcher_marker(emu68_boot_staging)


def _copy_emu68_boot_files(
    workflow: BuildWorkflow,
    image: CreatedImage,
    emu68_boot_staging: Path,
) -> None:
    """copy Emu68 boot files from all variant archives to EMU68BOOT staging"""
    emu68_extracted = image.extracted.extracted_paths.get("emu68_boot")

    if not emu68_extracted or not emu68_extracted.exists():
        raise BuildError("Required Emu68 primary boot archive was not extracted")

    # primary variant (pistorm32lite): copy everything except config.txt
    boot_files_copied = 0
    for item in emu68_extracted.iterdir():
        if item.name.lower() == "config.txt":
            workflow.logger.debug("Skipping config.txt from archive (generated separately)")
            continue
        dest = emu68_boot_staging / item.name
        if item.is_file():
            shutil.copy2(item, dest)
            boot_files_copied += 1
            workflow.logger.debug(f"Copied boot file: {item.name}")
        elif item.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest)
            boot_files_copied += 1

    workflow.logger.info(f"Copied {boot_files_copied} Emu68 boot files from primary variant")
    if boot_files_copied == 0:
        raise BuildError("Emu68 primary boot archive contained no boot files")

    secondary_names = sorted(
        name
        for name in image.extracted.downloaded.required_boot_artifacts
        if name.startswith("emu68_boot_")
    )
    for secondary_name in secondary_names:
        secondary_dir = image.extracted.extracted_paths.get(secondary_name)
        if not secondary_dir or not secondary_dir.exists():
            raise BuildError(f"Required Emu68 kernel archive was not extracted: {secondary_name}")
        kernels_copied = 0
        for item in secondary_dir.iterdir():
            if item.is_file() and item.name.lower().startswith("emu68-"):
                dest = emu68_boot_staging / item.name
                shutil.copy2(item, dest)
                boot_files_copied += 1
                kernels_copied += 1
                workflow.logger.info(f"Copied kernel from secondary variant: {item.name}")
        overlays_src = secondary_dir / "overlays"
        if overlays_src.is_dir():
            overlays_dst = emu68_boot_staging / "overlays"
            overlays_dst.mkdir(exist_ok=True)
            for ov in overlays_src.iterdir():
                if ov.is_file():
                    shutil.copy2(ov, overlays_dst / ov.name)
                    boot_files_copied += 1
                    workflow.logger.info(f"Copied overlay: {ov.name}")
        if kernels_copied == 0:
            raise BuildError(f"Emu68 archive {secondary_name} contained no kernel")

    workflow.logger.info(f"Total Emu68 boot files copied: {boot_files_copied}")

    stealth_fw = (
        Path(__file__).parent.parent.parent / "data" / "boot_files" / "ps32lite-stealth-firmware.gz"
    )
    if stealth_fw.exists():
        shutil.copy2(stealth_fw, emu68_boot_staging / "ps32lite-stealth-firmware.gz")
        workflow.logger.info("Copied ps32lite-stealth-firmware.gz for stealth mode")


def _copy_kickstart_rom(
    workflow: BuildWorkflow,
    image: CreatedImage,
    emu68_boot_staging: Path,
) -> str:
    """copy Kickstart ROM to boot partition. returns the ROM filename used"""
    rom_filename = "kick.rom"
    validated = image.workspace.validated
    if validated.resolved_rom_info.get("fat32_name"):
        rom_filename = validated.resolved_rom_info["fat32_name"]

    if validated.resolved_rom_path.exists():
        rom_dest = emu68_boot_staging / rom_filename
        shutil.copy2(validated.resolved_rom_path, rom_dest)
        workflow.logger.info(f"Copied Kickstart ROM to EMU68BOOT as {rom_filename}")
    else:
        workflow.logger.warning("No Kickstart ROM found - boot partition will be incomplete")

    return rom_filename


def _generate_boot_config(
    workflow: BuildWorkflow,
    image: CreatedImage,
    rom_filename: str,
) -> None:
    """generate config.txt and cmdline.txt for Emu68 boot partition"""
    screen_mode = "1280*720-50"  # default to 720p50
    custom_cvt = ""

    if workflow.config.display:
        if workflow.config.display.hdmi_mode:
            screen_mode = workflow.config.display.hdmi_mode
        if screen_mode == "Custom" and workflow.config.display.custom:
            custom_cvt = workflow.config.display.custom.to_cvt_string()

    # otg_mode=1 only when the poseidon package is in the build - it flips the
    # pi4/cm4 usb-c controller into host mode for xhci.device unit 0
    from emu68hatcher.builder.pipeline._selection import get_resolution

    selected = get_resolution(workflow).selected

    generate_boot_partition_files(
        image.workspace.staging_dir,
        screen_mode=screen_mode,
        custom_cvt=custom_cvt,
        rom_filename=rom_filename,
        emu68_version=workflow.config.emu68_version.value,
        usb_otg="poseidon" in selected,
        boot_settings=workflow.config.emu68_boot,
        rgb2rtg_video=workflow.config.rgb2rtg.video if workflow.config.rgb2rtg.enabled else None,
    )
    workflow.logger.info(
        f"Generated config.txt (HDMI mode: {screen_mode}, ROM: {rom_filename}) and cmdline.txt"
    )


def _write_hatcher_marker(emu68_boot_staging: Path) -> None:
    """drop a txt file on the FAT32 boot partition identifying the build tool"""
    from datetime import datetime, timezone

    from emu68hatcher import __version__

    app_name = "Emu68 Hatcher"
    version = __version__

    text = (
        f"This SD card image was built with {app_name}.\n"
        "\n"
        f"{app_name} version:  {version}\n"
        f"built at:             {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
        "homepage:             https://rootrootde.github.io/emu68hatcher/\n"
    )
    (emu68_boot_staging / "BUILT-WITH-EMU68HATCHER.txt").write_text(
        text, encoding="utf-8", newline="\n"
    )
