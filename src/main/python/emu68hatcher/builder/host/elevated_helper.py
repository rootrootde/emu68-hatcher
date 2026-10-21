"""elevated helper - one long-lived worker, file IPC for hst-imager (UAC, osascript, pkexec)"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from emu68hatcher.builder.host._elevated_worker_src import WORKER_SCRIPT
from emu68hatcher.builder.host._elevation_common import ElevatedResult, ElevationDenied
from emu68hatcher.utils.platform import OperatingSystem, get_platform_info, is_root

logger = logging.getLogger(__name__)

_PKEXEC_EXIT_REASONS = {
    126: "the authentication dialog was dismissed",
    127: "authentication failed or no polkit agent is running",
}


class ElevatedHelper:
    """one elevated worker subprocess; commands go in/out via JSON files"""

    POLL = 0.2
    READY_TIMEOUT = 30.0
    AUTH_TIMEOUT = 300.0
    HEARTBEAT_SECONDS = 10.0  # interval for "still running" log line

    def __init__(self) -> None:
        self.ipc_dir: Path | None = None
        self.worker_script: Path | None = None
        self._seq = 0
        self._any_failed = False
        self._process: subprocess.Popen | None = None

    def spawn(self) -> bool:
        """one auth prompt, start the worker; True when ready"""
        if is_root():
            return False  # already admin
        info = get_platform_info()
        if info.os == OperatingSystem.WINDOWS:
            return self._spawn_windows()
        if info.os == OperatingSystem.MACOS:
            return self._spawn_macos()
        if info.os == OperatingSystem.LINUX:
            return self._spawn_linux()
        return False

    def _spawn_windows(self) -> bool:
        self.ipc_dir = Path(tempfile.mkdtemp(prefix="emu68hatcher-helper-"))
        self.worker_script = Path(tempfile.mkstemp(prefix="emu68hatcher-worker-", suffix=".py")[1])
        self.worker_script.write_text(WORKER_SCRIPT)

        from emu68hatcher.builder.host._elevation_common import ps_quote

        ps_args = ", ".join(ps_quote(str(p)) for p in (self.worker_script, self.ipc_dir))
        ps = (
            f"$p = Start-Process -FilePath {ps_quote(sys.executable)} "
            f"-ArgumentList @({ps_args}) -Verb RunAs -PassThru -WindowStyle Hidden; "
            "if ($p) { exit 0 } else { exit 1 }"
        )
        try:
            # Start-Process blocks while the UAC prompt is open; windows auto-denies an
            # unanswered prompt after ~2 min, so 300s lets it resolve instead of killing
            # powershell mid-prompt
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            logger.warning("elevated helper UAC prompt was not answered")
            self._cleanup_files()
            return False
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning(f"elevated helper Start-Process failed: {e}")
            self._cleanup_files()
            return False
        if r.returncode != 0:
            logger.warning(
                f"elevated helper spawn rc={r.returncode}: {r.stderr.strip() or r.stdout.strip()}"
            )
            self._cleanup_files()
            return False
        return self._wait_ready()

    def _spawn_macos(self) -> bool:
        import shlex

        self.ipc_dir = Path(tempfile.mkdtemp(prefix="emu68hatcher-helper-"))
        self.worker_script = Path(tempfile.mkstemp(prefix="emu68hatcher-worker-", suffix=".py")[1])
        self.worker_script.write_text(WORKER_SCRIPT)

        spawn_log = self.ipc_dir / "_spawn.log"
        # frozen builds re-launch their own exe as the worker (main.py dispatch)
        py = sys.executable if getattr(sys, "frozen", False) else "/usr/bin/python3"
        logger.info(f"elevated helper using python: {py}")

        # no nohup - it chokes without a controlling tty inside osascripts shell context
        inner = (
            f"{shlex.quote(py)} {shlex.quote(str(self.worker_script))} "
            f"{shlex.quote(str(self.ipc_dir))} > {shlex.quote(str(spawn_log))} 2>&1 &"
        )
        inner_esc = inner.replace("\\", "\\\\").replace('"', '\\"')
        osa = f'do shell script "{inner_esc}" with administrator privileges'

        try:
            r = subprocess.run(
                ["osascript", "-e", osa],
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning(f"elevated helper osascript failed: {e}")
            self._cleanup_files()
            return False
        if r.returncode != 0:
            logger.warning(
                f"elevated helper spawn rc={r.returncode}: {r.stderr.strip() or r.stdout.strip()}"
            )
            self._cleanup_files()
            return False
        ok = self._wait_ready(cleanup_on_fail=False)
        if not ok:
            if spawn_log.exists():
                try:
                    tail = spawn_log.read_text(errors="replace").strip()
                    if tail:
                        logger.warning(f"elevated worker spawn log:\n{tail}")
                    else:
                        logger.warning("elevated worker spawn log is empty")
                except OSError as e:
                    logger.warning(f"could not read spawn log: {e}")
            else:
                logger.warning(f"spawn log not created at {spawn_log}")
            self._cleanup_files()
        return ok

    def _spawn_linux(self) -> bool:
        pkexec = shutil.which("pkexec")
        if pkexec is None:
            logger.warning("elevated helper needs pkexec, which is not on PATH")
            return False
        if getattr(sys, "frozen", False):
            py = sys.executable
        elif Path("/usr/bin/python3").is_file():
            py = "/usr/bin/python3"
        else:
            py = shutil.which("python3")
        if not py:
            logger.warning("elevated helper found no python3 to run the worker")
            return False
        logger.info(f"elevated helper using python: {py}")

        self.ipc_dir = Path(tempfile.mkdtemp(prefix="emu68hatcher-helper-"))
        self.worker_script = Path(tempfile.mkstemp(prefix="emu68hatcher-worker-", suffix=".py")[1])
        self.worker_script.write_text(WORKER_SCRIPT)
        spawn_log = self.ipc_dir / "_spawn.log"
        try:
            with open(spawn_log, "wb") as log:
                # pkexec execs the worker in place, so this handle becomes the root worker
                self._process = subprocess.Popen(
                    [pkexec, py, str(self.worker_script), str(self.ipc_dir)],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        except OSError as e:
            logger.warning(f"elevated helper pkexec failed: {e}")
            self._cleanup_files()
            return False
        return self._wait_pkexec_ready(spawn_log)

    def _wait_pkexec_ready(self, spawn_log: Path) -> bool:
        """wait through the polkit dialog; fail fast once pkexec has exited"""
        assert self.ipc_dir is not None and self._process is not None
        ready_file = self.ipc_dir / "ready"
        deadline = time.time() + self.AUTH_TIMEOUT + self.READY_TIMEOUT
        while time.time() < deadline:
            if ready_file.exists():
                logger.info(f"elevated helper ready at {self.ipc_dir}")
                return True
            rc = self._process.poll()
            if rc is not None:
                tail = ""
                try:
                    tail = spawn_log.read_text(errors="replace").strip()
                except OSError:
                    pass
                if tail:
                    logger.warning(f"elevated worker spawn log:\n{tail}")
                self._process = None
                self._cleanup_files()
                reason = _PKEXEC_EXIT_REASONS.get(rc, "the elevated worker did not start")
                raise ElevationDenied(f"pkexec exited with code {rc}: {reason}")
            time.sleep(self.POLL)
        logger.warning("elevated helper was not authorised or ready in time")
        try:
            (self.ipc_dir / "quit").touch()
        except OSError:
            pass
        self._stop_process()
        self._cleanup_files()
        return False

    def _stop_process(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            process.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            process.terminate()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired) as e:
            # a root worker ignores signals from the user; the quit sentinel is the real stop
            logger.warning(f"elevated worker pid {process.pid} did not stop: {e}")

    def _wait_ready(self, cleanup_on_fail: bool = True) -> bool:
        assert self.ipc_dir is not None
        ready_file = self.ipc_dir / "ready"
        deadline = time.time() + self.READY_TIMEOUT
        while time.time() < deadline:
            if ready_file.exists():
                logger.info(f"elevated helper ready at {self.ipc_dir}")
                return True
            time.sleep(self.POLL)
        logger.warning("elevated helper did not signal ready within 30s")
        if cleanup_on_fail:
            self._cleanup_files()
        return False

    def run(
        self,
        argv: list[str],
        timeout: float | None = None,
        cancel_check: Callable[[], bool] | None = None,
        on_line: Callable[[str, str], None] | None = None,
    ) -> ElevatedResult:
        """send cmd to worker; on_line(stream, line) fires per stdout/stderr line as the subprocess writes"""
        if self.ipc_dir is None:
            raise RuntimeError("ElevatedHelper.run called before spawn() succeeded")
        try:
            result = self._exchange(argv, timeout, cancel_check, on_line)
        except Exception:
            self._any_failed = True
            raise
        if result.returncode != 0 or result.cancelled:
            self._any_failed = True
        return result

    def _exchange(
        self,
        argv: list[str],
        timeout: float | None,
        cancel_check: Callable[[], bool] | None,
        on_line: Callable[[str, str], None] | None,
    ) -> ElevatedResult:
        """write one cmd file and wait for its result"""
        assert self.ipc_dir is not None
        self._seq += 1
        cmd_seq = self._seq
        cmd_path = self.ipc_dir / f"cmd-{cmd_seq}.json"
        result_path = self.ipc_dir / f"cmd-{cmd_seq}.result.json"
        cancel_path = self.ipc_dir / f"cancel-{cmd_seq}"

        tmp = cmd_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"argv": argv, "timeout": timeout}))
        tmp.rename(cmd_path)

        # per-stream state: (last consumed chunk seq, .done sentinel observed)
        stream_state = {"out": [0, False], "err": [0, False]}

        # extra slack on top of per-command timeout to absorb IPC overhead
        wait_until = time.time() + (timeout or 600) + 30
        start = time.time()
        last_heartbeat = start
        cancel_signaled = False
        parse_failures = 0
        while time.time() < wait_until:
            if on_line:
                for stream, state in stream_state.items():
                    state[0], state[1] = self._drain_chunks(
                        cmd_seq, stream, state[0], state[1], on_line
                    )

            if result_path.exists():
                try:
                    data = json.loads(result_path.read_text())
                except (OSError, json.JSONDecodeError) as e:
                    parse_failures += 1
                    if parse_failures >= 5:
                        result_path.unlink(missing_ok=True)
                        self._cleanup_chunks(cmd_seq)
                        raise RuntimeError(
                            f"elevated worker wrote unreadable result file: {e}"
                        ) from e
                    time.sleep(self.POLL)
                    continue
                # drain any chunks the worker flushed after the .done sentinel
                if on_line:
                    for stream, state in stream_state.items():
                        state[0], state[1] = self._drain_chunks(
                            cmd_seq, stream, state[0], state[1], on_line
                        )
                result_path.unlink(missing_ok=True)
                self._cleanup_chunks(cmd_seq)
                return ElevatedResult(
                    returncode=data["rc"],
                    stdout=data.get("stdout", ""),
                    stderr=data.get("stderr", ""),
                    cancelled=data.get("cancelled", False),
                )

            if cancel_check and not cancel_signaled and cancel_check():
                logger.info("cancellation requested - signalling elevated worker")
                try:
                    cancel_path.touch()
                except OSError as e:
                    logger.warning(f"could not write cancel sentinel: {e}")
                cancel_signaled = True
                # bump the wait so the worker has time to kill its child + write result
                wait_until = max(wait_until, time.time() + 10)

            now = time.time()
            elapsed = now - start
            if (
                elapsed >= self.HEARTBEAT_SECONDS
                and (now - last_heartbeat) >= self.HEARTBEAT_SECONDS
            ):
                logger.info(f"hst-imager: still running... ({elapsed:.0f}s elapsed)")
                last_heartbeat = now

            time.sleep(self.POLL)

        self._cleanup_chunks(cmd_seq)
        raise subprocess.TimeoutExpired(argv, timeout or 600)

    def _drain_chunks(
        self,
        cmd_seq: int,
        stream: str,
        last_seq: int,
        done_seen: bool,
        on_line: Callable[[str, str], None],
    ) -> tuple[int, bool]:
        """consume cmd-N.<stream>.<NNN> in order, emit lines via on_line, unlink each chunk after read"""
        assert self.ipc_dir is not None
        while True:
            nxt = self.ipc_dir / f"cmd-{cmd_seq}.{stream}.{last_seq + 1:06d}"
            if not nxt.exists():
                break
            try:
                content = nxt.read_text(encoding="utf-8", errors="replace")
            except OSError:
                break
            for line in content.splitlines():
                try:
                    on_line(stream, line)
                except Exception:
                    pass  # one bad callback shouldnt break the stream
            nxt.unlink(missing_ok=True)
            last_seq += 1
        if not done_seen:
            done = self.ipc_dir / f"cmd-{cmd_seq}.{stream}.done"
            if done.exists():
                done.unlink(missing_ok=True)
                done_seen = True
        return last_seq, done_seen

    def _cleanup_chunks(self, cmd_seq: int) -> None:
        """drop any straggler chunk/sentinel files for cmd_seq; called after result is consumed"""
        if self.ipc_dir is None:
            return
        for pattern in (f"cmd-{cmd_seq}.out.*", f"cmd-{cmd_seq}.err.*"):
            for path in self.ipc_dir.glob(pattern):
                path.unlink(missing_ok=True)
        (self.ipc_dir / f"cancel-{cmd_seq}").unlink(missing_ok=True)

    def shutdown(self) -> None:
        if self.ipc_dir is None:
            return
        try:
            (self.ipc_dir / "quit").touch()
            # let the worker notice and exit
            time.sleep(0.5)
        except OSError:
            pass
        self._stop_process()
        # the IPC dir and its _trace.log survive only when some command failed
        trace = self.ipc_dir / "_trace.log"
        if self._any_failed and trace.exists():
            logger.info(f"elevated worker trace log: {trace}")
        self._cleanup_files(keep_ipc_dir=self._any_failed)

    def _cleanup_files(self, keep_ipc_dir: bool = False) -> None:
        if self.ipc_dir is not None and not keep_ipc_dir:
            shutil.rmtree(self.ipc_dir, ignore_errors=True)
            self.ipc_dir = None
        if self.worker_script is not None:
            try:
                self.worker_script.unlink(missing_ok=True)
            except OSError:
                pass
            self.worker_script = None
