"""macOS and Linux elevation acquisition."""

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from emu68hatcher.builder.host._elevation_common import (
    ElevationDenied,
    ElevationToken,
    run_sudo_validate,
    start_sudo_keepalive,
)

logger = logging.getLogger(__name__)

_MACOS_ASKPASS_SCRIPT = (
    "#!/bin/sh\n"
    "/usr/bin/osascript "
    '-e \'display dialog "Emu68 Hatcher needs admin access to write to the SD card." '
    'default answer "" with hidden answer with title "Emu68 Hatcher" with icon caution\' '
    "-e 'text returned of result' 2>/dev/null\n"
)


def remove_askpass(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        logger.warning(f"could not remove askpass helper {path}: {error}")


def _write_askpass(script: str) -> Path:
    import tempfile

    descriptor, path = tempfile.mkstemp(prefix="emu68hatcher-askpass-", suffix=".sh")
    try:
        os.write(descriptor, script.encode())
    finally:
        os.close(descriptor)
    os.chmod(path, 0o700)
    return Path(path)


def acquire_macos() -> ElevationToken:
    have_tty = hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
    if have_tty:
        askpass = _write_askpass(_MACOS_ASKPASS_SCRIPT)
        try:
            run_sudo_validate(askpass)
        except (subprocess.CalledProcessError, subprocess.SubprocessError, OSError) as error:
            remove_askpass(askpass)
            detail = getattr(error, "stderr", None) or getattr(error, "stdout", None) or error
            raise ElevationDenied(f"admin prompt cancelled or denied: {detail}") from error
        token = ElevationToken("sudo", askpass_path=askpass)
        start_sudo_keepalive(token)
        return token

    from emu68hatcher.builder.host.elevated_helper import ElevatedHelper

    helper = ElevatedHelper()
    if helper.spawn():
        return ElevationToken("osascript-helper", helper=helper)
    raise ElevationDenied(
        "macos admin prompt cancelled or osascript helper failed to start. "
        "launch the app from Terminal and try again."
    )


def acquire_linux() -> ElevationToken:
    have_tty = sys.stdin.isatty() if hasattr(sys.stdin, "isatty") else False
    if have_tty and shutil.which("sudo"):
        try:
            subprocess.run(["sudo", "-v"], check=True, timeout=120)
            token = ElevationToken("sudo")
            start_sudo_keepalive(token)
            return token
        except (subprocess.CalledProcessError, subprocess.SubprocessError, OSError) as error:
            logger.info(f"sudo unavailable ({error}); trying pkexec")
    if not shutil.which("pkexec"):
        raise ElevationDenied(
            "pkexec not found. Run Emu68 Hatcher from a terminal, or install polkit, "
            "and start the build again."
        )
    from emu68hatcher.builder.host.elevated_helper import ElevatedHelper

    helper = ElevatedHelper()
    if helper.spawn():
        return ElevationToken("pkexec-helper", helper=helper)
    raise ElevationDenied(
        "the pkexec helper failed to start. Run Emu68 Hatcher from a terminal, "
        "or install polkit, and start the build again."
    )
