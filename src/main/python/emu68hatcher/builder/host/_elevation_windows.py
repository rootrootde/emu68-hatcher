"""Windows elevation acquisition."""

import logging
import shutil

from emu68hatcher.builder.host._elevation_common import ElevationDenied, ElevationToken

logger = logging.getLogger(__name__)


def acquire_windows() -> ElevationToken:
    if not shutil.which("powershell"):
        raise ElevationDenied("powershell not found on PATH")
    from emu68hatcher.builder.host.elevated_helper import ElevatedHelper

    for attempt in (1, 2):
        helper = ElevatedHelper()
        if helper.spawn():
            return ElevationToken(method="runas-helper", helper=helper)
        logger.warning(f"elevated helper spawn attempt {attempt} failed")
    raise ElevationDenied(
        "Windows administrator approval was not granted. Click Yes on the "
        "User Account Control prompt when it appears, then start the build again."
    )
