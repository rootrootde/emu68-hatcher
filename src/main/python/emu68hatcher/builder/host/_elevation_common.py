"""Shared elevation data, command wrapping, and process execution."""

import logging
import re
import shlex
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
    os: OperatingSystem
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
    if token.method == "pkexec":
        inner = f"{_dotnet_env_prefix()} exec " + " ".join(shlex.quote(arg) for arg in cmd)
        return ["pkexec", "/bin/sh", "-c", inner]
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
    stdout: str
    stderr: str
    cancelled: bool = False


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
) -> object:
    if token is not None and token.method.endswith("-helper") and token.helper is not None:
        return token.helper.run(cmd, timeout=timeout, cancel_check=cancel_check, on_line=on_line)
    if token is not None:
        refresh_sudo_timestamp(token)
    wrapped = wrap_for_elevation(cmd, token)
    if cancel_check is not None:
        return _run_cancellable(wrapped, timeout, encoding, errors, cancel_check, on_line)
    result = subprocess.run(
        wrapped,
        capture_output=capture_output,
        text=text,
        timeout=timeout,
        encoding=encoding,
        errors=errors,
        env=get_hst_imager_env(),
    )
    if on_line is not None:
        for stream, output in (("out", result.stdout or ""), ("err", result.stderr or "")):
            for line in output.splitlines():
                try:
                    on_line(stream, line)
                except Exception:
                    pass
    return result


def _terminate(process: subprocess.Popen) -> None:
    try:
        process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
    except OSError:
        pass


def _run_cancellable(
    wrapped: list[str],
    timeout: float | None,
    encoding: str,
    errors: str,
    cancel_check: Callable[[], bool],
    on_line: Callable[[str, str], None] | None,
) -> ElevatedResult:
    process = subprocess.Popen(
        wrapped,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding=encoding,
        errors=errors,
        env=get_hst_imager_env(),
    )
    stdout: list[str] = []
    stderr: list[str] = []

    def drain(pipe, sink: list[str], stream: str) -> None:
        try:
            for line in pipe:
                sink.append(line)
                if on_line is not None:
                    try:
                        on_line(stream, line.rstrip("\n"))
                    except Exception:
                        pass
        finally:
            pipe.close()

    stdout_thread = threading.Thread(
        target=drain,
        args=(process.stdout, stdout, "out"),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=drain,
        args=(process.stderr, stderr, "err"),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    deadline = time.time() + timeout if timeout else None
    cancelled = False
    while process.poll() is None:
        if cancel_check():
            cancelled = True
            _terminate(process)
            break
        if deadline is not None and time.time() > deadline:
            _terminate(process)
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)
            raise subprocess.TimeoutExpired(wrapped, timeout)
        time.sleep(0.1)
    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)
    return ElevatedResult(
        returncode=process.returncode if process.returncode is not None else -1,
        stdout="".join(stdout),
        stderr="".join(stderr),
        cancelled=cancelled,
    )
