"""Hardware-specific Workbench configuration."""

from __future__ import annotations

import shutil
import struct
from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.state import CreatedImage
from emu68hatcher.utils.paths import ensure_dir

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow

VIDEOCORE_TOOLTYPES = [
    "BOARDTYPE=Videocore",
    "SETTINGSFILE=SYS:DEVS/Picasso96Settings",
    "(BORDERBLANK=Yes)",
    "(BorderBlank=System)",
    "(BIGSPRITE=Yes)",
    "SOFTSPRITE=Yes",
    "IGNOREMASK=Yes",
    "(VC4_INTEGER_SCALING=1)",
    "(VC4_KERNEL=0)",
    "VC4_KERNEL=1",
    "VC4_KERNEL_B=1",
    "VC4_KERNEL_C=500",
    "VC4_PHASE=120",
    "VC4_SCALER=3",
    "(VC4_SPRITE_OPACITY=118)",
    "(VC4_SWITCH_METHOD=SEL)",
    "(VC4_SWITCH_INVERT=YES)",
    "(VC4_SWITCH_METHOD=CSI)",
    "(VC4_SWITCH_METHOD=CTS)",
    "VC4_LEGACY_ID",  # matches BoardType 14 in the generated P96 settings
]
UAEGFX_TOOLTYPES = [
    "BOARDTYPE=uaegfx",
    "SETTINGSFILE=SYS:DEVS/Picasso96Settings",
    "SOFTSPRITE=Yes",
    "IGNOREMASK=Yes",
]


def stage_whdload_kickstarts(
    workflow: BuildWorkflow,
    image: CreatedImage,
    boot_staging: Path,
) -> None:
    from emu68hatcher.data.rom_detection import WHDLOAD_ROM_NAMES, find_whdload_kickstarts

    directories = [Path(path) for path in workflow.config.asset_directories if Path(path).exists()]
    matched: dict[str, Path] = {}
    for directory in directories:
        for name, source in find_whdload_kickstarts(directory).items():
            matched.setdefault(name, source)
    validated = image.workspace.validated
    boot_name = validated.resolved_rom_info.get("whdload_name")
    if boot_name and boot_name not in matched:
        matched[boot_name] = validated.resolved_rom_path

    kickstarts_dir = ensure_dir(boot_staging / "Devs" / "Kickstarts")
    for name, source in matched.items():
        shutil.copy2(source, kickstarts_dir / name)
    found = sorted(matched)
    missing = [name for name in WHDLOAD_ROM_NAMES if name not in matched]
    workflow.logger.info(
        f"Staged {len(found)} WHDLoad ROM(s) to DEVS:Kickstarts/: "
        f"{', '.join(found) if found else '(none)'}"
    )
    if missing:
        workflow.logger.info(f"Missing WHDLoad ROMs (optional): {', '.join(missing)}")


def configure_hardware(
    workflow: BuildWorkflow,
    image: CreatedImage,
    boot_staging: Path,
) -> None:
    _configure_videocore_tooltypes(workflow, boot_staging)
    _override_videocore_card(workflow, image, boot_staging)
    _configure_hdtoolbox_tooltypes(workflow, boot_staging)
    _seed_poseidon_config(workflow, boot_staging)


def _override_videocore_card(
    workflow: BuildWorkflow,
    image: CreatedImage,
    boot_staging: Path,
) -> None:
    source = image.extracted.downloaded.downloaded_files.get("emu68_videocore")
    if not source or not source.exists():
        return
    from emu68hatcher.builder.staging.files import resolve_staging_path

    dest = resolve_staging_path(boot_staging, "Libs/Picasso96/VideoCore.card")
    if not dest.parent.exists():
        workflow.logger.warning(f"VideoCore.card destination not found: {dest.parent}")
        return
    shutil.copy2(source, dest)
    workflow.logger.info(f"Overrode VideoCore.card with {source.name}")


def _configure_videocore_tooltypes(workflow: BuildWorkflow, boot_staging: Path) -> None:
    from emu68hatcher.builder.staging.files import (
        read_info_tooltypes,
        resolve_source_path,
        write_info_tooltypes,
    )

    monitors = resolve_source_path(boot_staging, "Devs/Monitors")
    storage = resolve_source_path(boot_staging, "Storage/Monitors")
    for name, tooltypes, directories in (
        ("Videocore.info", VIDEOCORE_TOOLTYPES, (monitors, storage)),
        ("uaegfx.info", UAEGFX_TOOLTYPES, (storage, monitors)),
    ):
        info = next(
            (
                found
                for directory in directories
                if directory is not None and (found := resolve_source_path(directory, name))
            ),
            None,
        )
        if not info:
            workflow.logger.debug(f"{name} not found, skipping tooltype configuration")
            continue
        try:
            if workflow.config.rgb2rtg.enabled and name == "Videocore.info":
                required = {"boardtype", "settingsfile", "vc4_legacy_id"}
                existing = [
                    entry
                    for entry in read_info_tooltypes(info)
                    if entry.strip(" ()").split("=", 1)[0].lower() not in required
                ]
                keys = {entry.split("=", 1)[0].lower() for entry in existing}
                tooltypes = existing + [
                    entry for entry in tooltypes if entry.split("=", 1)[0].lower() not in keys
                ]
            write_info_tooltypes(info, tooltypes)
            workflow.logger.info(f"Configured {name} tooltypes (BOARDTYPE set)")
        except (OSError, ValueError):
            workflow.logger.exception(f"Failed to set {name} tooltypes")


def _configure_hdtoolbox_tooltypes(workflow: BuildWorkflow, boot_staging: Path) -> None:
    from emu68hatcher.builder.staging.files import read_info_tooltypes, write_info_tooltypes

    targets = (
        ("HDToolBoxPi3.info", "brcm-sdhc.device", "brcm-emmc.device"),
        ("HDToolBoxPi4.info", "brcm-emmc.device", "brcm-sdhc.device"),
    )
    for name, active, alternate in targets:
        path = boot_staging / "Tools" / name
        if not path.exists():
            continue
        try:
            patched = [
                f"SCSI_DEVICE_NAME={active}"
                if entry.strip() == "SCSI_DEVICE_NAME=scsi.device"
                else entry
                for entry in read_info_tooltypes(path)
            ]
            patched += ["(SCSI_DEVICE_NAME=scsi.device)", f"(SCSI_DEVICE_NAME={alternate})"]
            write_info_tooltypes(path, patched)
            workflow.logger.info(f"Patched {name}: SCSI_DEVICE_NAME={active}")
        except (OSError, ValueError):
            workflow.logger.exception(f"Failed to patch {name}")


def _seed_poseidon_config(workflow: BuildWorkflow, boot_staging: Path) -> None:
    target = boot_staging / "Prefs" / "Env-Archive" / "PsdStackloader"
    seed = Path(__file__).parent.parent.parent / "data" / "reference" / "poseidon.psdc"
    if not target.exists() or not seed.exists():
        return
    form = seed.read_bytes()
    data = target.read_bytes()
    offset = data.find(b"FORM")
    if (
        offset < 12
        or data[offset + 8 : offset + 12] != b"PSDC"
        or struct.unpack(">I", data[:4])[0] != 0x3F3
    ):
        workflow.logger.warning("PsdStackloader layout not recognised; Poseidon config not seeded")
        return
    table_size = struct.unpack(">I", data[8:12])[0]
    old_longs = struct.unpack(">I", data[offset - 4 : offset])[0]
    size_offset = 20 + 4 * (table_size - 1)
    if struct.unpack(">I", data[size_offset : size_offset + 4])[0] != old_longs:
        workflow.logger.warning("PsdStackloader hunk table mismatch; Poseidon config not seeded")
        return
    padded = form + b"\x00" * (-len(form) % 4)
    new_longs = len(padded) // 4
    patched = bytearray(data)
    patched[size_offset : size_offset + 4] = struct.pack(">I", new_longs)
    patched[offset - 4 : offset] = struct.pack(">I", new_longs)
    patched[offset : offset + old_longs * 4] = padded
    target.write_bytes(bytes(patched))
    workflow.logger.info("Seeded Poseidon config (xhci units preconfigured)")
