"""apply the AmigaOS 3.9 BoingBag updates (soft ROM update + fixes) onto the boot tree"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.staging.files import resolve_staging_path
from emu68hatcher.builder.staging.packages import _merge_tree
from emu68hatcher.utils.host_tools import find_7z, run_7z

logger = logging.getLogger(__name__)

_INNER_ARCHIVE = "AmigaOS-Update"
_EXTRACT_TIMEOUT = 600

# the curated per-item install list the original imager applies from each
# boingbag's password-locked AmigaOS-Update payload (from its ListofPackagestoInstall
# sheet, 3.9 ArchiveinArchive rows), applied bb1 then bb2. the .BB39-2 files are
# renamed to their real names; setpatch soft-loads Devs/AmigaOS ROM Update at boot.
# each item: (path within the payload, dest subdir under SYS:, new name or None)
_BOINGBAGS = (
    {
        "package": "boingbag_os39_bb1",
        "password": "93ABDF11",
        "items": (
            ("C", "", None),
            ("Classes", "", None),
            ("Fonts", "", None),
            ("L", "", None),
            ("Libs", "", None),
            ("Prefs", "", None),
            ("S", "", None),
            ("Storage", "", None),
            ("System", "", None),
            ("Tools", "", None),
            ("Utilities", "", None),
            ("WBStartup", "", None),
            ("Devs/paulaaudio.device", "Devs", None),
            ("Devs/serial.device", "Devs", None),
        ),
    },
    {
        "package": "boingbag_os39_bb2",
        "password": "3FB6986B-B0AD6339-4FF3254B",
        "items": (
            ("C", "", None),
            ("Classes", "", None),
            ("L", "", None),
            ("Libs", "", None),
            ("Prefs", "", None),
            ("RexxC", "", None),
            ("S", "", None),
            ("System", "", None),
            ("Tools", "", None),
            ("Utilities", "", None),
            ("WBStartup", "", None),
            ("Devs/AmigaOS ROM Update.BB39-2", "Devs", "AmigaOS ROM Update"),
            ("Devs/NSDPatch.cfg.BB39-2", "Devs", "NSDPatch.cfg"),
            ("Devs/paulaaudio.device", "Devs", None),
            ("Devs/serial.device", "Devs", None),
        ),
    },
)


def apply_boingbags(
    extracted_paths: dict,
    boot_staging: Path,
    *,
    cancel_check: Callable[[], None] | None = None,
) -> None:
    """apply BoingBag 1 then 2 onto boot_staging from the downloaded, extracted archives"""
    seven_z = find_7z()
    if not seven_z:
        raise BuildError("7z not found; cannot apply the AmigaOS 3.9 BoingBags")

    for bb in _BOINGBAGS:
        if cancel_check:
            cancel_check()
        bb_dir = extracted_paths.get(bb["package"])
        if not bb_dir:
            raise BuildError(f"{bb['package']} was not downloaded; cannot apply the 3.9 update")
        _apply_one(seven_z, Path(bb_dir), bb["password"], bb["items"], boot_staging)


def _apply_one(seven_z, bb_dir: Path, password: str, items, boot_staging: Path) -> None:
    inner = next((p for p in bb_dir.rglob(_INNER_ARCHIVE) if p.is_file()), None)
    if inner is None:
        raise BuildError(f"{_INNER_ARCHIVE} not found under {bb_dir}")

    with tempfile.TemporaryDirectory() as td:
        payload = Path(td)
        try:
            r = run_7z(
                seven_z,
                ["x", "-y", f"-p{password}", f"-o{td}", str(inner)],
                timeout=_EXTRACT_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise BuildError(
                f"BoingBag payload extraction timed out after {_EXTRACT_TIMEOUT} s; "
                f"7z did not finish unpacking {inner}. Check that the BoingBag download "
                "is complete and the disk is not full, then build again."
            ) from exc
        if r.returncode != 0:
            raise BuildError(
                f"failed to open BoingBag payload: {(r.stderr or r.stdout).strip()[:300]}"
            )
        for src_rel, dest_sub, new_name in items:
            _apply_item(payload, boot_staging, src_rel, dest_sub, new_name)


def _apply_item(
    payload: Path, boot_staging: Path, src_rel: str, dest_sub: str, new_name: str | None
) -> None:
    src = resolve_staging_path(payload, src_rel)
    if not src.exists():
        logger.warning(f"boingbag item not in payload, skipping: {src_rel}")
        return
    parent = resolve_staging_path(boot_staging, dest_sub) if dest_sub else boot_staging
    target = resolve_staging_path(parent, new_name or src.name)
    if src.is_dir():
        _merge_tree(src, target)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
