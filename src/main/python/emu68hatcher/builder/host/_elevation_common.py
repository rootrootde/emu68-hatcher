"""Shared elevation data, command wrapping, and process execution."""

import logging
import os
import queue
import re
import shlex
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from emu68hatcher.utils.host_tools import get_hst_imager_env
from emu68hatcher.utils.paths import DOTNET_BUNDLE_ENV_VAR, get_dotnet_bundle_dir
from emu68hatcher.utils.platform import OperatingSystem, get_platform_info

logger = logging.getLogger(__name__)


class ElevationDenied(RuntimeError):
    """Interactive elevation was denied or unavailable."""


@dataclass
class ElevationToken:
    method: str
    helper: object | None = None
    askpass_path: Path | None = None
    sudo_keepalive_stop: threading.Event | None = field(default=None, repr=False)
    sudo_keepalive_thread: threading.Thread | None = field(default=None, repr=False)


def ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _dotnet_env_prefix() -> str:
    return f"{DOTNET_BUNDLE_ENV_VAR}={shlex.quote(str(get_dotnet_bundle_dir()))}"


def wrap_for_elevation(cmd: list[str], token: ElevationToken | None) -> list[str]:
    if token is None or token.method == "noop":
        return cmd
    if token.method == "sudo":
        return _wrap_sudo(cmd)
    return cmd


_DEVICE_RE = re.compile(r"^(/dev/(?:r?disk|sd|mmcblk)\d+)")


def _wrap_sudo(cmd: list[str]) -> list[str]:
    base_device = None
    for arg in cmd:
        match = _DEVICE_RE.match(arg)
        if match:
            base_device = match.group(1).replace("/dev/rdisk", "/dev/disk")
            break
    command = " ".join(shlex.quote(arg) for arg in cmd)
    if base_device and get_platform_info().os == OperatingSystem.MACOS:
        inner = (
            f"/usr/sbin/diskutil unmountDisk force {shlex.quote(base_device)} "
            f">/dev/null 2>&1 ; true ; {_dotnet_env_prefix()} exec {command}"
        )
        return ["sudo", "-n", "/bin/bash", "-c", inner]
    return ["sudo", "-n", "/bin/sh", "-c", f"{_dotnet_env_prefix()} exec {command}"]


def run_sudo_validate(askpass: Path) -> None:
    env = get_hst_imager_env()
    env["SUDO_ASKPASS"] = str(askpass)
    subprocess.run(
        ["sudo", "-A", "-v"],
        check=True,
        env=env,
        timeout=300,
        capture_output=True,
        text=True,
    )


def refresh_sudo_timestamp(token: ElevationToken | None) -> bool:
    if token is None or token.method != "sudo":
        return True
    try:
        if token.askpass_path is not None:
            run_sudo_validate(token.askpass_path)
        else:
            subprocess.run(
                ["sudo", "-n", "-v"],
                check=True,
                timeout=30,
                capture_output=True,
                text=True,
            )
    except (subprocess.CalledProcessError, subprocess.SubprocessError, OSError) as error:
        detail = getattr(error, "stderr", None) or getattr(error, "stdout", None) or error
        logger.warning(f"sudo timestamp refresh failed: {detail}")
        return False
    return True


def start_sudo_keepalive(token: ElevationToken) -> None:
    if token.method != "sudo" or token.sudo_keepalive_thread is not None:
        return

    stop = threading.Event()

    def keepalive() -> None:
        while not stop.wait(60.0):
            try:
                result = subprocess.run(
                    ["sudo", "-n", "-v"],
                    timeout=30,
                    capture_output=True,
                    text=True,
                )
            except (subprocess.SubprocessError, OSError) as error:
                logger.warning(f"sudo keepalive stopped: {error}")
                return
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip() or f"rc={result.returncode}"
                logger.warning(f"sudo keepalive stopped: {detail}")
                return

    thread = threading.Thread(target=keepalive, name="sudo-keepalive", daemon=True)
    token.sudo_keepalive_stop = stop
    token.sudo_keepalive_thread = thread
    thread.start()


def stop_sudo_keepalive(token: ElevationToken) -> None:
    stop = token.sudo_keepalive_stop
    thread = token.sudo_keepalive_thread
    token.sudo_keepalive_stop = None
    token.sudo_keepalive_thread = None
    if stop is not None:
        stop.set()
    if thread is not None:
        thread.join(timeout=2)


@dataclass
class ElevatedResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    cancelled: bool = False
    timed_out: bool = False
    stop_failed: bool = False


def run_elevated(
    cmd: list[str],
    token: ElevationToken | None,
    *,
    capture_output: bool = True,
    text: bool = True,
    timeout: float | None = None,
    encoding: str = "utf-8",
    errors: str = "replace",
    cancel_check: Callable[[], bool] | None = None,
    on_line: Callable[[str, str], None] | None = None,
) -> ElevatedResult:
    if token is not None and token.method.endswith("-helper") and token.helper is not None:
        return token.helper.run(cmd, timeout=timeout, cancel_check=cancel_check, on_line=on_line)
    if token is not None:
        refresh_sudo_timestamp(token)
    wrapped = wrap_for_elevation(cmd, token)
    if cancel_check is not None:
        result = run_process(
            wrapped,
            timeout=timeout,
            cancel_check=cancel_check,
            on_line=on_line,
            encoding=encoding,
            errors=errors,
        )
        if result.timed_out:
            raise subprocess.TimeoutExpired(wrapped, timeout)
        return result
    completed = subprocess.run(
        wrapped,
        capture_output=capture_output,
        text=text,
        timeout=timeout,
        encoding=encoding,
        errors=errors,
        env=get_hst_imager_env(),
    )
    if on_line is not None:
        for stream, output in (("out", completed.stdout or ""), ("err", completed.stderr or "")):
            for line in output.splitlines():
                try:
                    on_line(stream, line)
                except Exception:
                    pass
    return ElevatedResult(completed.returncode, completed.stdout or "", completed.stderr or "")


def _stop(proc: subprocess.Popen, isolated_group: bool) -> bool:
    try:
        if os.name == "nt":
            proc.terminate()
        elif isolated_group:
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=5)
        return True
    except (OSError, ProcessLookupError):
        return proc.poll() is not None
    except subprocess.TimeoutExpired:
        if os.name != "nt" and not isolated_group:
            try:
                proc.send_signal(signal.SIGHUP)
                proc.wait(timeout=5)
                return True
            except (OSError, subprocess.TimeoutExpired):
                return proc.poll() is not None
        try:
            if os.name == "nt":
                proc.kill()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
            return True
        except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
            return proc.poll() is not None


def run_process(
    cmd: list[str],
    *,
    timeout: float | None = None,
    cancel_check: Callable[[], bool] | None = None,
    on_line: Callable[[str, str], None] | None = None,
    merge_stderr: bool = False,
    isolated_group: bool | None = None,
    encoding: str = "utf-8",
    errors: str = "replace",
) -> ElevatedResult:
    """run cmd, polling cancel and timeout; on_line fires on the calling thread"""
    if isolated_group is None:
        # macos sudo timestamps are tty-bound; setsid makes a fresh sudo -n fail
        isolated_group = os.name != "nt" and Path(cmd[0]).name != "sudo"
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        text=True,
        encoding=encoding,
        errors=errors,
        bufsize=1,
        env=get_hst_imager_env(),
        start_new_session=isolated_group and os.name != "nt",
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
    )
    lines: queue.Queue[tuple[str, str] | None] = queue.Queue()
    collected: dict[str, list[str]] = {"out": [], "err": []}

    def read(pipe, stream: str) -> None:
        try:
            for line in pipe:
                collected[stream].append(line)
                lines.put((stream, line.rstrip("\n")))
        finally:
            lines.put(None)

    pipes = [(proc.stdout, "out")] if merge_stderr else [(proc.stdout, "out"), (proc.stderr, "err")]
    readers = [threading.Thread(target=read, args=pipe, daemon=True) for pipe in pipes]
    for reader in readers:
        reader.start()

    def dispatch(item: tuple[str, str]) -> None:
        if on_line is not None:
            try:
                on_line(*item)
            except Exception:
                pass

    deadline = time.monotonic() + timeout if timeout else None
    exited_at: float | None = None
    eof = 0
    cancelled = timed_out = False
    stopped = True
    while True:
        try:
            item = lines.get(timeout=0.1)
            if item is None:
                eof += 1
            else:
                dispatch(item)
        except queue.Empty:
            pass
        if proc.poll() is not None:
            if eof == len(readers) and lines.empty():
                break
            # a grandchild holding the pipe open must not stall the caller forever
            exited_at = exited_at or time.monotonic()
            if time.monotonic() - exited_at > 5:
                break
        if cancel_check is not None and cancel_check():
            cancelled = True
            stopped = _stop(proc, isolated_group)
            break
        if deadline is not None and time.monotonic() >= deadline:
            timed_out = True
            stopped = _stop(proc, isolated_group)
            break
    for reader in readers:
        reader.join(timeout=1)
    while True:
        try:
            item = lines.get_nowait()
        except queue.Empty:
            break
        if item is not None:
            dispatch(item)
    return ElevatedResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout="".join(collected["out"]),
        stderr="".join(collected["err"]),
        cancelled=cancelled,
        timed_out=timed_out,
        stop_failed=not stopped,
    )
