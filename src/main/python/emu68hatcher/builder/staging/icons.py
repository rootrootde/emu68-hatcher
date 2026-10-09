"""Amiga drawer-icon generation - ensure user-visible folders get a '.info' so Workbench shows them"""

import logging
import shutil
import tempfile
from pathlib import Path

from emu68hatcher.data.package_loader import get_local_packages_dir
from emu68hatcher.utils.host_tools import find_hst_imager, run_hst_extract

logger = logging.getLogger(__name__)


# roots that get auto-generated drawer icons; system dirs (C/, S/, Libs/, Devs/) stay icon-less
_ICON_ROOTS: tuple[str, ...] = ("Programs", "Prefs", "Emu68-Hatcher")

# runtime-state subdirs to leave alone even when under an icon root
_ICON_SKIP: tuple[str, ...] = ("Prefs/Env-Archive",)


def _drawer_template() -> Path:
    """path to the bundled generic '_drawer.info' template"""
    return get_local_packages_dir() / "System" / "_drawer.info"


def ensure_dirs_for_orphan_drawer_icons(boot_staging: Path) -> int:
    """create empty dirs for drawer-type .info files whose target dir is missing"""
    created = 0
    for info_path in boot_staging.rglob("*.info"):
        # disk.info is the volume icon; _drawer.info represents the parent dir itself
        if info_path.name in ("disk.info", "_drawer.info"):
            continue
        try:
            data = info_path.read_bytes()
        except OSError:
            continue
        # DiskObject struct: magic 0xE310 at offset 0, do_Type byte at offset 48 (2 = WB_DRAWER)
        if len(data) < 49 or data[0:2] != b"\xe3\x10" or data[48] != 2:
            continue
        drawer = info_path.with_suffix("")
        if drawer.exists():
            continue
        drawer.mkdir(parents=True, exist_ok=True)
        created += 1
        logger.info(f"Created missing drawer for orphan icon: {drawer.relative_to(boot_staging)}")
    return created


def _iter_drawer_dirs(boot_staging: Path):
    """yield every directory under the icon roots that should have an icon"""
    for root_name in _ICON_ROOTS:
        root = boot_staging / root_name
        if not root.exists():
            continue
        yield root
        for sub in root.rglob("*"):
            if not sub.is_dir():
                continue
            rel = sub.relative_to(boot_staging).as_posix()
            if any(rel == s or rel.startswith(s + "/") for s in _ICON_SKIP):
                continue
            yield sub


def ensure_drawer_icons(boot_staging: Path) -> int:
    """drop a '<name>.info' next to every folder under the icon roots that lacks one"""
    template = _drawer_template()
    if not template.exists():
        logger.warning("drawer template %s not found; skipping icon generation", template)
        return 0

    created = 0
    for d in _iter_drawer_dirs(boot_staging):
        info = d.with_name(d.name + ".info")
        if info.exists():
            continue
        shutil.copy2(template, info)
        created += 1
        logger.debug("created drawer icon %s", info.relative_to(boot_staging))
    return created


def extract_icon_from_adf(
    adf_path: Path,
    file_in_adf: str,
    dest: Path,
    hst_imager: Path | None = None,
) -> bool:
    """extract a single .info file from an ADF to 'dest' via hst-imager fs extract"""
    if hst_imager is None:
        hst_imager = find_hst_imager()
    if not hst_imager:
        logger.warning("hst-imager not found; cannot extract icon from ADF")
        return False
    if not adf_path.exists():
        logger.warning(f"ADF {adf_path} not found")
        return False

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        result = run_hst_extract(
            hst_imager,
            f"{adf_path.as_posix()}/{file_in_adf}",
            tmp_dir.as_posix() + "/",
            uaemetadata="None",
        )
        if result.returncode != 0:
            logger.warning(
                f"hst-imager failed to extract {adf_path.name}/{file_in_adf}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
            return False

        extracted = [p for p in tmp_dir.rglob("*.info") if p.is_file()]
        if not extracted:
            logger.warning(f"No .info file produced by extracting {file_in_adf}")
            return False

        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(extracted[0], dest)
        return True


def apply_icon_set_drawer(
    boot_staging: Path,
    adf_path: Path,
    file_in_adf: str,
    hst_imager: Path | None = None,
) -> int:
    """swap auto-generated drawer icons for the iconset version, matching by byte-equality against the bundled template (so tool icons are left alone)"""
    template = _drawer_template()
    if not template.exists():
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        iconset_drawer = Path(tmp) / "drawer.info"
        if not extract_icon_from_adf(adf_path, file_in_adf, iconset_drawer, hst_imager):
            return 0

        # only swap in a real drawer icon (do_Type 2 + drawer data); a tool/project
        # icon here would make every folder un-openable ("Unable to open your tool")
        icon_bytes = iconset_drawer.read_bytes()
        do_type = icon_bytes[48] if len(icon_bytes) > 48 else 0
        has_drawer_data = len(icon_bytes) >= 70 and icon_bytes[66:70] != b"\x00\x00\x00\x00"
        if do_type != 2 or not has_drawer_data:
            logger.warning(
                f"iconset drawer {file_in_adf} is not a valid WBDRAWER icon "
                f"(do_Type={do_type}); keeping the generic drawer icons"
            )
            return 0

        template_bytes = template.read_bytes()
        replaced = 0
        for d in _iter_drawer_dirs(boot_staging):
            info = d.with_name(d.name + ".info")
            if not info.exists() or not info.is_file():
                continue
            if info.read_bytes() != template_bytes:
                continue
            shutil.copy2(iconset_drawer, info)
            replaced += 1
        return replaced
