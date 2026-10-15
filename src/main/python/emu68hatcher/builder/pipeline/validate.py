"""Asset and media validation stage."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.pipeline.validate_archives import (
    validate_picasso96_archive,
    validate_roadshow_archive,
)
from emu68hatcher.builder.pipeline.validate_output import validate_output_target
from emu68hatcher.builder.state import BuildStage, ValidatedInputs
from emu68hatcher.config.schema import NetworkStack
from emu68hatcher.data.install_media import (
    check_install_media_complete,
    get_required_install_media,
    scan_install_media_by_hash,
)
from emu68hatcher.data.rom_detection import (
    identify_kickstart,
    pick_boot_rom,
    scan_for_kickstart_roms,
)

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow


def stage_validate(workflow: BuildWorkflow, _previous=None) -> ValidatedInputs:
    workflow._update_state(BuildStage.VALIDATE, 0.0, "Validating configuration...")
    from emu68hatcher.builder.rgb2rtg import validate_rgb2rtg

    validate_rgb2rtg(workflow)
    existing_dirs = _existing_asset_directories(workflow)
    kickstart_version = workflow.config.kickstart.version.value
    rom_path, rom_info, whdload_roms = _resolve_rom(workflow, existing_dirs, kickstart_version)
    if workflow.config.rgb2rtg.enabled and rom_info.get("model") != "A1200":
        raise BuildError("RGB2RTG requires an A1200 ROM and A1200/PiStorm32-lite hardware")
    found_media = _resolve_media(workflow, existing_dirs, kickstart_version)
    _check_icon_set_adf(workflow, found_media, kickstart_version)
    _check_optional_package_adfs(workflow, found_media, kickstart_version)
    if workflow.config.network_stack == NetworkStack.MIAMIDX:
        from emu68hatcher.builder.pipeline.configure_network import (
            validate_miamidx_key_directory,
        )

        keys = validate_miamidx_key_directory(workflow.config.miamidx_key_directory)
        if keys:
            workflow.logger.info("MiamiDX registration keys accepted")

    ags_plan = None
    if workflow.config.ags_import is not None and workflow.config.ags_import.enabled:
        from emu68hatcher.builder.ags_validation import validate_ags_import

        ags_plan = validate_ags_import(workflow)
    validate_output_target(workflow)
    if ags_plan is not None:
        from emu68hatcher.builder.ags_validation import check_elevated_source_access

        check_elevated_source_access(workflow, ags_plan)
    roadshow_archive = (
        workflow.config.roadshow_archive
        if workflow.config.network_stack == NetworkStack.ROADSHOW
        else None
    )
    roadshow_path, roadshow_kind = validate_roadshow_archive(roadshow_archive)
    picasso96_path = validate_picasso96_archive(workflow.config.display.picasso96_archive)
    if roadshow_path:
        workflow.logger.info(f"Roadshow archive accepted ({roadshow_kind}): {roadshow_path}")
    if picasso96_path:
        workflow.logger.info(f"Picasso96 archive accepted: {picasso96_path}")

    workflow._update_state(progress=100.0)
    workflow._milestone("Configuration validated")
    return ValidatedInputs(
        resolved_rom_path=rom_path,
        resolved_rom_info=rom_info,
        resolved_install_media=tuple(found_media),
        roadshow_archive_path=roadshow_path,
        roadshow_archive_kind=roadshow_kind,
        picasso96_archive_path=picasso96_path,
        ags_plan=ags_plan,
        whdload_roms=whdload_roms,
    )


def _existing_asset_directories(workflow: BuildWorkflow) -> list[Path]:
    configured = [Path(path) for path in workflow.config.asset_directories]
    existing = [path for path in configured if path.exists() and path.is_dir()]
    if not configured:
        raise BuildError("No asset directories configured (ROMs / ADFs)")
    if not existing:
        raise BuildError(
            "None of the configured asset directories exist: "
            + ", ".join(str(path) for path in configured)
        )
    return existing


def _resolve_rom(
    workflow: BuildWorkflow,
    directories: list[Path],
    kickstart_version: str,
) -> tuple[Path, dict, dict[str, Path]]:
    workflow._update_state(progress=20.0)
    workflow._milestone(f"Scanning {len(directories)} asset directories for Kickstart ROM")
    found, truncated = scan_for_kickstart_roms(directories)
    whdload_roms: dict[str, Path] = {}
    for rom in found:
        name = rom.get("whdload_name")
        if name and not rom.get("excluded"):
            whdload_roms.setdefault(name, rom["path"])

    rom_path = pick_boot_rom(found, kickstart_version)
    if rom_path is not None:
        workflow.logger.info(f"Auto-detected Kickstart ROM: {rom_path}")
        return rom_path, identify_kickstart(rom_path), whdload_roms

    cap_hint = (
        " A scanned directory hit the 5000-file limit, so the ROM may have been skipped."
        if truncated
        else ""
    )
    locations = ", ".join(str(path) for path in directories)
    if found:
        versions = ", ".join(sorted({rom["version"] for rom in found}))
        raise BuildError(
            f"No Kickstart {kickstart_version} ROM found in {locations}. "
            f"Found versions: {versions}.{cap_hint}"
        )
    raise BuildError(f"No valid Kickstart ROMs found in {locations}.{cap_hint}")


def _resolve_media(
    workflow: BuildWorkflow,
    directories: list[Path],
    kickstart_version: str,
) -> list:
    workflow._update_state(progress=40.0)
    workflow._milestone("Scanning for install media (ADFs/ISOs)")
    found, truncated = scan_install_media_by_hash(directories)
    _, missing = check_install_media_complete(found, kickstart_version)
    cap_hint = " (scan limit reached)" if truncated else ""
    if found:
        workflow.logger.info(f"Found {len(found)} install media files")
        if missing:
            workflow.logger.warning(
                f"Missing install media for {kickstart_version}: {missing}{cap_hint}"
            )
    else:
        required = get_required_install_media(kickstart_version)
        workflow.logger.warning(
            f"No install media found. Required for {kickstart_version}: {required}{cap_hint}"
        )
    return found


def _check_optional_package_adfs(
    workflow: BuildWorkflow,
    found_media: list,
    kickstart_version: str,
) -> None:
    from emu68hatcher.builder.pipeline._selection import get_resolution
    from emu68hatcher.data.package_loader import get_adf_rules_for_version
    from emu68hatcher.data.package_selection import missing_optional_adfs

    missing = missing_optional_adfs(
        get_adf_rules_for_version(kickstart_version),
        get_resolution(workflow).selected,
        workflow.config.icon_set,
        {media.adf_name for media in found_media},
        set(get_required_install_media(kickstart_version)),
    )
    if not missing:
        return
    lines = [f"  {adf}  (required by: {', '.join(packages)})" for adf, packages in missing.items()]
    raise BuildError(
        "Enabled package(s) need install media that wasn't found:\n"
        + "\n".join(lines)
        + "\n\nAdd the missing ADFs, or untick the packages on the Software page or the "
        "languages under System > AmigaOS & files > Languages."
    )


def _check_icon_set_adf(
    workflow: BuildWorkflow,
    found_media: list,
    kickstart_version: str,
) -> None:
    from emu68hatcher.data.icon_sets import format_adf_name, get_icon_set_extra_adf

    required_adf = get_icon_set_extra_adf(workflow.config.icon_set, kickstart_version)
    if required_adf is None or required_adf in {media.adf_name for media in found_media}:
        return
    label = format_adf_name(required_adf)
    raise BuildError(
        f"The selected icon set '{workflow.config.icon_set}' requires a recognized "
        f"{label} ADF. Add the ADF to the Amiga Files tab or select another icon set."
    )
