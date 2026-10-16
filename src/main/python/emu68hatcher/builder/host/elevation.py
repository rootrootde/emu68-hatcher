"""Public elevation facade."""

import logging

from emu68hatcher.builder.host._elevation_common import (
    ElevationDenied,
    ElevationToken,
    refresh_sudo_timestamp,
    run_elevated,
    stop_sudo_keepalive,
    wrap_for_elevation,
)
from emu68hatcher.utils.platform import OperatingSystem, get_platform_info, is_root

__all__ = [
    "ElevationDenied",
    "ElevationToken",
    "acquire_elevation",
    "cleanup_elevation",
    "refresh_elevation",
    "run_elevated",
    "wrap_for_elevation",
]

logger = logging.getLogger(__name__)


def cleanup_elevation(token: ElevationToken | None) -> None:
    if token is None:
        return
    stop_sudo_keepalive(token)
    if token.helper is not None:
        try:
            token.helper.shutdown()
        except Exception:
            logger.exception("error shutting down elevated helper")
        finally:
            token.helper = None
    if token.askpass_path is not None:
        from emu68hatcher.builder.host._elevation_unix import remove_askpass

        remove_askpass(token.askpass_path)
        token.askpass_path = None


def refresh_elevation(token: ElevationToken | None) -> bool:
    return refresh_sudo_timestamp(token)


def acquire_elevation() -> ElevationToken:
    """Acquire one token for all elevated calls in a build."""
    platform = get_platform_info().os
    if is_root():
        logger.info("already root - skipping interactive elevation")
        return ElevationToken(method="noop")
    if platform == OperatingSystem.MACOS:
        from emu68hatcher.builder.host._elevation_unix import acquire_macos

        return acquire_macos()
    if platform == OperatingSystem.LINUX:
        from emu68hatcher.builder.host._elevation_unix import acquire_linux

        return acquire_linux()
    if platform == OperatingSystem.WINDOWS:
        from emu68hatcher.builder.host._elevation_windows import acquire_windows

        return acquire_windows()
    raise ElevationDenied(f"unsupported OS: {platform}")
