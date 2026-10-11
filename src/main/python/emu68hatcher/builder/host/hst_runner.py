"""hst-imager command exec - progress, capture, errors, timeout"""

import logging
import shlex
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from emu68hatcher.builder.errors import BuildCancelledError
from emu68hatcher.builder.host.elevation import ElevationToken, run_elevated
from emu68hatcher.builder.host.hst_commands import (
    HSTCommandLine,
    HSTScript,
)
from emu68hatcher.utils.host_tools import find_hst_imager

_logger = logging.getLogger(__name__)


class CommandStatus(str, Enum):
    """command run status"""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"


@dataclass
class CommandResult:
    """one hst-imager command's result"""

    command: HSTCommandLine
    status: CommandStatus
    return_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.status == CommandStatus.COMPLETED and self.return_code == 0


@dataclass
class ScriptResult:
    """one hst script's combined result"""

    script: HSTScript
    results: list[CommandResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return all(r.success for r in self.results)

    @property
    def failed_commands(self) -> list[CommandResult]:
        return [r for r in self.results if not r.success]


# progress callback type
HstProgressCallback = Callable[[int, int, str, CommandStatus], None]


class HSTRunner:
    """runs hst-imager commands with progress callbacks"""

    # binary path is logged once per process - buildlog gets noisy otherwise
    _logged_binary: bool = False

    def __init__(
        self,
        hst_imager_path: Path | None = None,
        timeout: float = 300.0,
        dry_run: bool = False,
        cancel_check: Callable[[], bool] | None = None,
    ):
        self._hst_imager = hst_imager_path
        self.timeout = timeout
        self.dry_run = dry_run
        self._cancel_check = cancel_check

    @property
    def hst_imager(self) -> Path:
        """hst-imager binary path"""
        if self._hst_imager is None:
            self._hst_imager = find_hst_imager()
        if self._hst_imager is None:
            raise RuntimeError(
                "HST Imager not found. Open the Start tab and download the required tools."
            )
        return self._hst_imager

    def is_available(self) -> bool:
        """hst-imager binary is on disk"""
        try:
            return self.hst_imager.exists()
        except RuntimeError:
            return False

    def run_command(
        self,
        command: HSTCommandLine,
        timeout: float | None = None,
        elevation: ElevationToken | None = None,
        on_line: Callable[[str, str], None] | None = None,
    ) -> CommandResult:
        """run one hst-imager command synchronously"""
        if self.dry_run:
            return CommandResult(
                command=command,
                status=CommandStatus.COMPLETED,
                stdout=f"[DRY RUN] Would execute: {command.to_string()}",
            )

        cmd_timeout = timeout or self.timeout
        start_time = time.time()

        try:
            args = [str(self.hst_imager)] + command.to_args()

            if not HSTRunner._logged_binary:
                _logger.info(f"hst-imager: binary: {self.hst_imager}")
                HSTRunner._logged_binary = True
            # log just the subcommand - elevation wrapping is noise
            _logger.info(f"hst-imager: $ {shlex.join(['hst-imager', *command.to_args()])}")

            result = run_elevated(
                args,
                elevation,
                timeout=cmd_timeout,
                cancel_check=self._cancel_check,
                on_line=on_line,
            )

            duration = time.time() - start_time

            if getattr(result, "cancelled", False):
                _logger.info(f"hst-imager: cancelled after {duration:.2f}s")
                raise BuildCancelledError("Build was cancelled by user")

            if result.returncode == 0:
                _logger.info(f"hst-imager: rc=0 in {duration:.2f}s")
                return CommandResult(
                    command=command,
                    status=CommandStatus.COMPLETED,
                    return_code=result.returncode,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    duration=duration,
                )
            else:
                # hst-imager errors land on stdout as "[timestamp ERR] message", not stderr
                error_detail = None
                if result.stdout:
                    for line in result.stdout.split("\n"):
                        if " ERR]" in line:
                            error_detail = line.split(" ERR]", 1)[-1].strip()
                            break
                if not error_detail:
                    error_detail = (
                        result.stderr.strip() if result.stderr else f"Exit code {result.returncode}"
                    )
                # log stdout/stderr at INFO - buildlog needs failures even when caller swallows them
                _logger.info(
                    f"hst-imager: rc={result.returncode} in {duration:.2f}s; error={error_detail!r}"
                )
                if result.stdout:
                    _logger.info(f"hst-imager: stdout (500 chars): {result.stdout[:500]!r}")
                if result.stderr:
                    _logger.info(f"hst-imager: stderr (500 chars): {result.stderr[:500]!r}")
                return CommandResult(
                    command=command,
                    status=CommandStatus.FAILED,
                    return_code=result.returncode,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    duration=duration,
                    error=error_detail,
                )

        except subprocess.TimeoutExpired:
            return CommandResult(
                command=command,
                status=CommandStatus.TIMEOUT,
                duration=cmd_timeout,
                error=f"Command timed out after {cmd_timeout}s",
            )
        except BuildCancelledError:
            raise
        except Exception as e:
            return CommandResult(
                command=command,
                status=CommandStatus.FAILED,
                duration=time.time() - start_time,
                error=str(e),
            )

    def run_script(
        self,
        script: HSTScript,
        progress_callback: HstProgressCallback | None = None,
        stop_on_error: bool = True,
        elevation: ElevationToken | None = None,
    ) -> ScriptResult:
        """run a script synchronously"""
        result = ScriptResult(script=script)
        total = len(script.commands)

        for i, command in enumerate(script.commands):
            if progress_callback:
                progress_callback(
                    i + 1,
                    total,
                    command.description or command.to_string(),
                    CommandStatus.RUNNING,
                )

            cmd_result = self.run_command(command, elevation=elevation)
            result.results.append(cmd_result)

            if progress_callback:
                progress_callback(
                    i + 1,
                    total,
                    command.description or command.to_string(),
                    cmd_result.status,
                )

            if not cmd_result.success and stop_on_error:
                for remaining in script.commands[i + 1 :]:
                    result.results.append(
                        CommandResult(
                            command=remaining,
                            status=CommandStatus.SKIPPED,
                        )
                    )
                break

        return result
