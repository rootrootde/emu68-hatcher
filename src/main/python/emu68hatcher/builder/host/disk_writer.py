"""flash an image, optionally verifying it in a separate pass before remounting"""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from emu68hatcher.builder.errors import BuildCancelledError, BuildError
from emu68hatcher.builder.host._flash_process import run_local_flash
from emu68hatcher.builder.host._flash_progress import FlashProgress
from emu68hatcher.builder.host.elevation import (
    ElevationToken,
    refresh_elevation,
    wrap_for_elevation,
)
from emu68hatcher.utils.host_tools import find_hst_imager, get_hst_imager_env

logger = logging.getLogger(__name__)


_PERCENT_PROGRESS_RE = re.compile(
    r"^\s*(?P<percent>\d{1,3}(?:[.,]\d+)?)%\s+"
    r"(?P<details>\[[^\]\r\n]+\]\s+\[[^\]\r\n]+\]\s+\[[^\]\r\n]+\])\s*$"
)
_PROGRESS_RE = re.compile(
    r"(?P<phase>writing|verifying|reading)\s*[:\-]?\s*"
    r"(?P<done>\d[\d_,.]*)\s*(?:/|of)\s*(?P<total>\d[\d_,.]*)",
    re.IGNORECASE,
)


def _handle_progress_line(
    line: str,
    phase_seen: set[str],
    progress_callback: Callable[[float, str], None] | None,
    recent: deque[str],
    *,
    statistics: FlashProgress,
    verify: bool = True,
) -> None:
    """emit progress on a hst-imager progress line, else buffer it and debug-log"""
    if verify and line.endswith("Post-write verification complete"):
        phase_seen.add("verified")
        return
    if verify and line.endswith("Verifying written data"):
        phase_seen.add("verifying")
        logger.info("flash: verification pass started")
        if progress_callback:
            progress_callback(50.0, "Verifying written data…")
        return
    if verify and line.endswith("Flushing written data"):
        if progress_callback:
            progress_callback(50.0, "Flushing written data…")
        return
    percent_match = _PERCENT_PROGRESS_RE.fullmatch(line)
    if percent_match:
        pct = float(percent_match.group("percent").replace(",", "."))
        if 0.0 <= pct <= 100.0:
            phase = "Verifying" if "verifying" in phase_seen else "Writing"
            message = statistics.format(phase, pct, percent_match.group("details"))
            milestone = f"{phase}:{int(pct) // 10}"
            if milestone not in phase_seen:
                phase_seen.add(milestone)
                logger.info("flash: %.1f%%; %s", pct, message.replace("\n", "; "))
            if progress_callback:
                overall = (50.0 if phase == "Verifying" else 0.0) + pct / 2 if verify else pct
                progress_callback(overall, message)
            return
    m = _PROGRESS_RE.search(line)
    if not m:
        recent.append(line)
        logger.debug(f"hst-imager: {line}")
        return
    phase = m.group("phase").lower()
    if phase not in phase_seen:
        phase_seen.add(phase)
        logger.info(f"flash: {phase} pass started")
    if progress_callback:
        total = _parse_int(m.group("total"))
        if total > 0:
            pct = max(0.0, min(100.0, 100.0 * _parse_int(m.group("done")) / total))
            overall = (50.0 if phase == "verifying" else 0.0) + pct / 2 if verify else pct
            progress_callback(overall, f"{phase.capitalize()}: {pct:.1f}%")


def _raise_flash_failure(
    rc: int, duration: float, recent: deque[str], *, verifying: bool = False
) -> None:
    """log the tail buffer and raise BuildError with the most likely cause line"""
    for ln in recent:
        logger.error(f"hst-imager: {ln}")
    # first non-empty .NET-ish exception line is usually the human-readable cause
    cause = next(
        (ln for ln in recent if "Exception" in ln or "Error" in ln or "denied" in ln.lower()),
        recent[-1] if recent else "",
    )
    operation = "verification" if verifying else "flash"
    raise BuildError(f"hst-imager {operation} failed (rc={rc}) after {duration:.1f}s: {cause}")


def check_flash_verification_support() -> None:
    """reject older tools before a build starts writing to the target"""
    hst = find_hst_imager()
    if not hst:
        raise BuildError("hst-imager binary not found")
    try:
        result = subprocess.run(
            [str(hst), "write", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
            env=get_hst_imager_env(),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise BuildError(f"Could not check hst-imager verification support: {error}") from error
    if result.returncode != 0 or "--verify-after" not in result.stdout:
        raise BuildError(
            "This hst-imager does not support verification after writing. "
            "Install the updated Hatcher build of hst-imager, or uncheck 'Verify after writing'."
        )


def flash_image_to_disk(
    image_path: Path,
    target_device: str,
    *,
    verify: bool = True,
    skip_unused_sectors: bool = True,
    force: bool = False,
    elevation: ElevationToken | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
    cancel_predicate: Callable[[], bool] | None = None,
    timeout: float | None = None,
) -> None:
    """write image_path to target_device; BuildCancelledError on cancel, BuildError on failure"""
    image_path = Path(image_path)
    if not image_path.exists():
        raise BuildError(f"image not found: {image_path}")

    hst = find_hst_imager()
    if not hst:
        raise BuildError("hst-imager binary not found")

    if verify:
        check_flash_verification_support()
    # Explicit values override saved tool settings, including Verify=True.
    args = [
        str(hst),
        "--verbose",
        "write",
        str(image_path),
        str(target_device),
        "--verify",
        "false",
        "--force",
        str(force).lower(),
        "--skip-unused-sectors",
        str(skip_unused_sectors).lower(),
    ]
    if verify:
        args.append("--verify-after")

    statistics = FlashProgress(image_path.stat().st_size)

    # helper IPC streams stdout/stderr via on_line so the GUI progress bar moves while writing
    if (
        elevation is not None
        and elevation.method.endswith("-helper")
        and elevation.helper is not None
    ):
        logger.info(f"flash: $ {shlex.join(args)}")
        if progress_callback:
            progress_callback(0.0, f"Flashing to {target_device}, this can take a few minutes…")
        start = time.time()
        phase_seen: set[str] = set()
        recent: deque[str] = deque(maxlen=30)

        def on_line(stream: str, line: str) -> None:
            ln = line.rstrip()
            if not ln:
                return
            _handle_progress_line(
                ln, phase_seen, progress_callback, recent, statistics=statistics, verify=verify
            )

        result = elevation.helper.run(
            args, timeout=timeout, cancel_check=cancel_predicate, on_line=on_line
        )
        duration = time.time() - start
        if result.cancelled:
            raise BuildCancelledError("flash cancelled by user")
        if result.returncode != 0:
            _raise_flash_failure(
                result.returncode, duration, recent, verifying="verifying" in phase_seen
            )
        if verify and "verified" not in phase_seen:
            raise BuildError("hst-imager did not confirm completion of post-write verification")
        logger.info(f"flash: done in {duration:.1f}s; verified={verify}")
        if progress_callback:
            progress_callback(
                100.0,
                "Flash and verification complete" if verify else "Flash complete (not verified)",
            )
        return

    if not refresh_elevation(elevation):
        raise BuildError("admin access could not be renewed before flashing")
    cmd = wrap_for_elevation(args, elevation)

    logger.info(f"flash: $ {shlex.join(cmd)}")
    if progress_callback:
        progress_callback(0.0, f"Flashing to {target_device}, this can take a few minutes…")

    start = time.time()
    phase_seen: set[str] = set()
    recent: deque[str] = deque(maxlen=30)

    def on_local_line(line: str) -> None:
        line = line.rstrip()
        if line:
            _handle_progress_line(
                line, phase_seen, progress_callback, recent, statistics=statistics, verify=verify
            )

    try:
        result = run_local_flash(
            cmd,
            timeout=timeout,
            cancel_check=cancel_predicate,
            on_line=on_local_line,
        )
    except OSError as e:
        raise BuildError(f"failed to launch hst-imager: {e}") from e

    duration = time.time() - start
    if result.stop_failed:
        raise BuildError("flash process did not stop after cancel or timeout")
    if result.cancelled:
        raise BuildCancelledError("flash cancelled by user")
    if result.timed_out:
        raise BuildError(f"flash timed out after {timeout}s")

    if result.returncode != 0:
        _raise_flash_failure(
            result.returncode, duration, recent, verifying="verifying" in phase_seen
        )

    if verify and "verified" not in phase_seen:
        raise BuildError("hst-imager did not confirm completion of post-write verification")
    logger.info(f"flash: done in {duration:.1f}s; verified={verify}")
    if progress_callback:
        progress_callback(
            100.0, "Flash and verification complete" if verify else "Flash complete (not verified)"
        )


def _parse_int(s: str) -> int:
    return int(s.replace("_", "").replace(",", "").replace(".", ""))
