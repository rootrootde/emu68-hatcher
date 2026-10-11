"""Write the build receipts and the installation record into the boot staging tree.

Runs at the start of finalize: after configure and install_extras, so the hashes
describe the bytes that are copied into the image.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from emu68hatcher import __version__
from emu68hatcher.builder.staging.files import resolve_source_path, resolve_staging_path
from emu68hatcher.builder.staging.receipts import (
    RECEIPTS_FILE,
    build_receipts,
    find_block,
    sha256_file,
    write_json,
)
from emu68hatcher.config.defaults import EMU68_BOOT_PARTITION_NAME
from emu68hatcher.data.package_loader import get_local_packages_dir

if TYPE_CHECKING:
    from emu68hatcher.builder.state import CreatedImage
    from emu68hatcher.builder.workflow import BuildWorkflow

INSTALLATION_FILE = "Emu68-Hatcher/Installation.json"
INSTALLATION_FORMAT = "hatcher-installation-1"
_SETTINGS_TOOLS = ("hatcher_prefs", "hatcher_packages", "emu68_manager")


def _user_archives(workflow: BuildWorkflow) -> set[str]:
    # same rule as configure_scripts' when_user_archive selection
    archives = set()
    if workflow.config.roadshow_archive is not None:
        archives.add("roadshow")
    if workflow.config.display.picasso96_archive is not None:
        archives.add("picasso96")
    return archives


def _startup_time(boot_root, licensed: bool) -> str:
    """which boot-time clock hook the Roadshow block carries, as hatcher-prefs names it"""
    user_startup = resolve_source_path(boot_root, "S/User-Startup")
    block = find_block(user_startup.read_bytes(), "Roadshow TCP/IP") if user_startup else None
    if block is None or not licensed:
        return "none"
    if b"NetworkConfig.rexx SYNCTIME" in block:
        return "networkconfig-synctime"
    if b"Hatcher-Prefs startup-time" in block:
        return "hatcher-prefs-opt-in"
    return "none"


def _boot_files(staging_dir) -> dict[str, str]:
    boot = staging_dir / EMU68_BOOT_PARTITION_NAME
    return {
        name: sha256_file(boot / name)
        for name in ("config.txt", "cmdline.txt")
        if (boot / name).is_file()
    }


def write_build_records(workflow: BuildWorkflow, image: CreatedImage) -> None:
    from emu68hatcher.builder.pipeline._selection import get_resolution

    boot_device = workflow.config.boot_device
    boot_root = image.workspace.staging_dir / boot_device
    if not boot_root.is_dir():
        workflow.logger.warning("No boot staging tree; skipping build receipts")
        return
    resolution = get_resolution(workflow)
    catalog = {
        "id": workflow.catalog.id,
        "revision": workflow.catalog.revision,
        "source_commit": workflow.catalog.source_commit,
    }
    packages = {p.name: p for p in workflow.catalog.packages()}
    user_archives = _user_archives(workflow)
    log = workflow.write_log
    if log is None:
        from emu68hatcher.builder.staging.receipts import StagingWriteLog

        log = StagingWriteLog(boot_root)
    for name in (RECEIPTS_FILE, INSTALLATION_FILE):
        existing = resolve_source_path(boot_root, name)
        if existing is not None:
            workflow.logger.warning(f"Replacing {name} from the extra content with the build's")

    receipts = build_receipts(
        log=log,
        install_order=resolution.install_order,
        packages=packages,
        requested=resolution.requested,
        user_archives=user_archives,
        local_root=get_local_packages_dir(),
        catalog=catalog,
        builder_version=__version__,
    )
    write_json(resolve_staging_path(boot_root, RECEIPTS_FILE), receipts)
    for skipped in receipts["not_recorded"]:
        workflow.logger.debug(f"No receipt for {skipped['id']}: {skipped['reason']}")
    workflow.logger.info(
        f"Wrote {len(receipts['packages'])} package receipts to SYS:{RECEIPTS_FILE}"
    )

    selected = set(resolution.install_order)
    stack = workflow.config.network_stack
    licensed = "roadshow" in user_archives and "roadshow" in selected
    installation = {
        "format": INSTALLATION_FORMAT,
        "authority": "build observation; the files on the card are authoritative",
        "builder": {"name": "Emu68 Hatcher", "version": __version__},
        "built": datetime.now(timezone.utc).date().isoformat(),
        "kickstart": workflow.config.kickstart.version.value,
        "emu68_version": workflow.config.emu68_version.value,
        "system_device": boot_device,
        "boot_partition": EMU68_BOOT_PARTITION_NAME,
        # hashes of the generated boot files, so a settings tool can tell user edits apart
        "boot_files": _boot_files(image.workspace.staging_dir),
        "network_stack": stack.value.lower() if stack else None,
        "roadshow_licensed": licensed,
        "startup_time": _startup_time(boot_root, licensed),
        "tools": sorted(t for t in _SETTINGS_TOOLS if t in selected and t in packages),
        "catalog": catalog,
        "receipts": RECEIPTS_FILE,
    }
    write_json(resolve_staging_path(boot_root, INSTALLATION_FILE), installation)
