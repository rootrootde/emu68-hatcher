"""path utils - runtime data dir resolved via $HATCHER_HOME, OS data dir (frozen), or repo-local hatcher_data/"""

import os
import shutil
import sys
import tempfile
from functools import cache
from pathlib import Path

from emu68hatcher.utils.platform import OperatingSystem, get_platform_info

# .NET single-file extraction target override - bypasses default ~/.net which is unwritable under
# root context ($HOME=/var/root) and under macOS Tahoe sandbox/translocation for user context
DOTNET_BUNDLE_ENV_VAR = "DOTNET_BUNDLE_EXTRACT_BASE_DIR"


def _user_data_dir() -> Path:
    """return the OS-standard per-user data directory for this app"""
    app_name = "Emu68 Hatcher"
    os_name = get_platform_info().os
    if os_name == OperatingSystem.MACOS:
        return Path.home() / "Library" / "Application Support" / app_name
    if os_name == OperatingSystem.WINDOWS:
        # LOCALAPPDATA, not Roaming APPDATA (caches shouldn't sync across domain machines)
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / app_name
        return Path.home() / "AppData" / "Local" / app_name
    # linux / other Unix: XDG spec
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "emu68hatcher"
    return Path.home() / ".local" / "share" / "emu68hatcher"


def _ensure(path: Path) -> Path:
    """mkdir -p once and return"""
    path.mkdir(parents=True, exist_ok=True)
    return path


@cache
def _get_home() -> Path:
    """base dir for all runtime data"""
    home = os.environ.get("HATCHER_HOME")
    if home:
        p = Path(home).expanduser().resolve()
    elif getattr(sys, "frozen", False):
        p = _user_data_dir()
    else:
        # source checkout: store under repo root (5 parents up from this file)
        p = Path(__file__).resolve().parents[5] / "hatcher_data"
    return _ensure(p)


def get_app_log_path() -> Path:
    """'<home>/hatcher.log'; reset_runtime_data leaves it alone"""
    return _get_home() / "hatcher.log"


def get_extra_packages_dir() -> Path:
    """'<home>/packages': the user's own package files, not created"""
    return _get_home() / "packages"


@cache
def get_cache_dir() -> Path:
    """return '<home>/cache'"""
    return _ensure(_get_home() / "cache")


@cache
def get_downloads_dir() -> Path:
    """dir for downloaded packages"""
    return _ensure(get_cache_dir() / "downloads")


@cache
def get_dotnet_bundle_dir() -> Path:
    """target for .NET single-file extraction (hst-imager bundle cache)"""
    return _ensure(get_cache_dir() / "dotnet-bundle")


def make_temp_workdir() -> Path:
    """create a fresh local work directory"""
    temp_base = get_cache_dir() / "temp"
    if os.name == "nt" and temp_base.drive.startswith("\\\\"):
        return Path(tempfile.mkdtemp(prefix="emu68hatcher-"))
    temp_base = _ensure(temp_base)
    return Path(tempfile.mkdtemp(dir=temp_base))


@cache
def get_tools_dir() -> Path:
    """return '<home>/tools/<platform>'"""
    info = get_platform_info()
    return _ensure(_get_home() / "tools" / info.platform_string)


def ensure_dir(path: Path) -> Path:
    """mkdir -p (helper for tmp dirs)"""
    return _ensure(path)


def reset_runtime_data() -> list[Path]:
    """delete cache and downloaded tools plus stray temp files; returns paths that resisted removal"""
    failures: list[Path] = []
    home = _get_home()
    for target in (home / "cache", home / "tools"):
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            if target.exists():
                failures.append(target)
    # elevation worker scripts, ipc dirs and askpass helpers land in the OS temp dir
    for stray in Path(tempfile.gettempdir()).glob("emu68hatcher-*"):
        try:
            if stray.is_dir():
                shutil.rmtree(stray, ignore_errors=True)
            else:
                stray.unlink(missing_ok=True)
        except OSError:
            pass
    # the cached path getters ran _ensure on first call; restore what they promise exists
    for d in (
        get_cache_dir(),
        get_downloads_dir(),
        get_dotnet_bundle_dir(),
        get_tools_dir(),
    ):
        d.mkdir(parents=True, exist_ok=True)
    return failures
