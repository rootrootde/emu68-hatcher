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
    find_kickstart_for_version,
    identify_kickstart,
    scan_for_kickstart_roms,
)

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow


def stage_validate(workflow: BuildWorkflow, _previous=None) -> ValidatedInputs:
    workflow._update_state(BuildStage.VALIDATE, 0.0, "Validating configuration...")
    existing_dirs = _existing_asset_directories(workflow)
    kickstart_version = workflow.config.kickstart.version.value
    rom_path, rom_info = _resolve_rom(workflow, existing_dirs, kickstart_version)
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

    ags_inventory = None
    if workflow.config.ags_import is not None:
        from emu68hatcher.builder.ags_validation import validate_ags_import

        ags_inventory = validate_ags_import(workflow)
    validate_output_target(workflow)
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
        ags_inventory=ags_inventory,
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
) -> tuple[Path, dict]:
    workflow._update_state(progress=20.0)
    workflow._milestone(f"Scanning {len(directories)} asset directories for Kickstart ROM")
    rom_path = find_kickstart_for_version(directories, kickstart_version)
    if rom_path:
        workflow.logger.info(f"Auto-detected Kickstart ROM: {rom_path}")
        return rom_path, identify_kickstart(rom_path)

    found, truncated = scan_for_kickstart_roms(directories)
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

    enabled = get_resolution(workflow).selected
    core_required = set(get_required_install_media(kickstart_version))
    found_names = {media.adf_name for media in found_media}
    missing: dict[str, list[str]] = {}
    for rule in get_adf_rules_for_version(kickstart_version):
        if rule.mandatory or not rule.package:
            continue
        if rule.package.lower() not in enabled:
            continue
        if rule.adf in core_required or rule.adf in found_names:
            continue
        missing.setdefault(rule.adf, []).append(rule.package)
    if not missing:
        return
    lines = [
        f"  {adf}  (required by: {', '.join(sorted(set(packages)))})"
        for adf, packages in sorted(missing.items())
    ]
    raise BuildError(
        "Enabled package(s) need install media that wasn't found:\n"
        + "\n".join(lines)
        + "\n\nAdd the missing ADFs or disable the packages in the Software tab."
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
