"""local flash process control"""

from __future__ import annotations

from collections.abc import Callable

from emu68hatcher.builder.host._elevation_common import ElevatedResult, run_process


def run_local_flash(
    cmd: list[str],
    *,
    timeout: float | None,
    cancel_check: Callable[[], bool] | None,
    on_line: Callable[[str], None],
) -> ElevatedResult:
    """run a flash command while polling cancel and timeout"""
    return run_process(
        cmd,
        timeout=timeout,
        cancel_check=cancel_check,
        on_line=lambda _stream, line: on_line(line),
        merge_stderr=True,
    )
