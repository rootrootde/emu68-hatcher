"""elevated worker program - written to a temp file and run by the elevated interpreter"""

# worker script gets written to a temp file at spawn time.
# Popen + poll so a cancel sentinel file can kill the in-flight subprocess.
# stdout/stderr stream through reader threads -> chunk files; consumer drains them live.
WORKER_SCRIPT = '''
"""worker - reads cmd-N.json from ipc_dir, writes .result.json + chunked .out/.err files"""
import json, os, subprocess, sys, threading, time
from pathlib import Path


_trace_fp = None


def trace(msg):
    if _trace_fp is None:
        return
    try:
        _trace_fp.write(f"{time.time():.3f} {msg}\\n")
        _trace_fp.flush()
    except OSError:
        pass


def _grant_user_read(path):
    """windows-only: drop integrity to Medium so the non-elevated parent can read worker output"""
    if sys.platform != "win32":
        return
    try:
        subprocess.run(
            ["icacls", str(path), "/q",
             "/grant", "*S-1-5-11:(R)",
             "/setintegritylevel", "Medium"],
            capture_output=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _stream_reader(pipe, ipc_dir, seq, stream, full_buf):
    """drain pipe in chunks; flush each complete-line batch as cmd-N.<stream>.NNNNNN; touch .done at EOF"""
    chunk_seq = 0
    buf = bytearray()

    def _flush(payload):
        nonlocal chunk_seq
        if not payload:
            return
        chunk_seq += 1
        name = f"cmd-{seq}.{stream}.{chunk_seq:06d}"
        tmp = ipc_dir / (name + ".tmp")
        final = ipc_dir / name
        try:
            tmp.write_bytes(payload)
            tmp.replace(final)
            _grant_user_read(final)
        except OSError as e:
            trace(f"seq={seq} chunk write failed {name}: {e}")

    try:
        while True:
            data = pipe.read(65536)
            if not data:
                break
            buf.extend(data)
            full_buf.extend(data)
            # hst-imager ends live progress updates with a carriage return
            end = max(buf.rfind(b"\\n"), buf.rfind(b"\\r"))
            if end < 0:
                continue
            _flush(bytes(buf[:end + 1]))
            del buf[:end + 1]
    except OSError as e:
        trace(f"seq={seq} stream {stream} read error: {e}")
    finally:
        if buf:
            _flush(bytes(buf))
        try:
            pipe.close()
        except OSError:
            pass
        done = ipc_dir / f"cmd-{seq}.{stream}.done"
        try:
            done.touch()
            _grant_user_read(done)
        except OSError as e:
            trace(f"seq={seq} done sentinel {stream} failed: {e}")


def run_one(argv, timeout, ipc_dir, cancel_file, seq):
    cancelled = False
    timed_out = False
    rc = -2
    stdout_buf = bytearray()
    stderr_buf = bytearray()
    trace(f"seq={seq} run_one start argv0={argv[0] if argv else ''!r}")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # pin .NET single-file extraction inside ipc_dir; root context $HOME (/var/root) may be unwritable
    env = os.environ.copy()
    dotnet_dir = Path(ipc_dir) / "dotnet"
    try:
        dotnet_dir.mkdir(parents=True, exist_ok=True)
        env["DOTNET_BUNDLE_EXTRACT_BASE_DIR"] = str(dotnet_dir)
    except OSError as e:
        trace(f"seq={seq} dotnet dir setup failed: {e}")
    trace(f"seq={seq} popen begin")
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            creationflags=creation_flags,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as e:
        trace(f"seq={seq} popen failed: {e}")
        # still emit empty .done sentinels so the consumer can finalize
        for stream in ("out", "err"):
            try:
                (ipc_dir / f"cmd-{seq}.{stream}.done").touch()
                _grant_user_read(ipc_dir / f"cmd-{seq}.{stream}.done")
            except OSError:
                pass
        return -2, "", str(e), False
    trace(f"seq={seq} popen ok pid={proc.pid}")

    t_out = threading.Thread(
        target=_stream_reader,
        args=(proc.stdout, ipc_dir, seq, "out", stdout_buf),
        daemon=True,
    )
    t_err = threading.Thread(
        target=_stream_reader,
        args=(proc.stderr, ipc_dir, seq, "err", stderr_buf),
        daemon=True,
    )
    t_out.start()
    t_err.start()

    deadline = time.time() + timeout if timeout else None
    poll_count = 0
    while proc.poll() is None:
        poll_count += 1
        if cancel_file.exists():
            cancelled = True
            break
        if deadline and time.time() > deadline:
            timed_out = True
            break
        time.sleep(0.1)
    trace(f"seq={seq} poll loop done polls={poll_count} cancelled={cancelled} timed_out={timed_out}")
    if cancelled or timed_out:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        trace(f"seq={seq} kill+wait done")
    if cancelled:
        rc = -3
    elif timed_out:
        rc = -1
    else:
        rc = proc.returncode

    # let reader threads finish draining + write their .done sentinels
    t_out.join(timeout=10)
    t_err.join(timeout=10)
    out = stdout_buf.decode("utf-8", errors="replace")
    err = stderr_buf.decode("utf-8", errors="replace")
    trace(f"seq={seq} streams closed stdout={len(out)}b stderr={len(err)}b rc={rc}")
    if timed_out and not err:
        err = f"timeout after {timeout}s"
    if cancelled and not err:
        err = "cancelled by user"
    trace(f"seq={seq} run_one done")
    return rc, out, err, cancelled


def main(ipc_dir):
    global _trace_fp
    ipc_dir.mkdir(parents=True, exist_ok=True)
    try:
        _trace_fp = open(ipc_dir / "_trace.log", "w", encoding="utf-8")
        _grant_user_read(ipc_dir / "_trace.log")
    except OSError:
        _trace_fp = None
    trace(f"worker started pid={__import__('os').getpid()}")
    (ipc_dir / "ready").touch()
    _grant_user_read(ipc_dir / "ready")
    cancel_file = ipc_dir / "cancel"
    while True:
        if (ipc_dir / "quit").exists():
            break
        for cmd_file in sorted(ipc_dir.glob("cmd-*.json")):
            if cmd_file.name.endswith(".tmp") or cmd_file.name.endswith(".result.json"):
                continue
            try:
                spec = json.loads(cmd_file.read_text())
                argv = spec["argv"]
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                continue
            timeout = spec.get("timeout")
            seq = cmd_file.stem.split("-", 1)[-1]
            trace(f"seq={seq} picked up cmd file")
            rc, out, err, cancelled = run_one(argv, timeout, ipc_dir, cancel_file, seq)
            if cancel_file.exists():
                try:
                    cancel_file.unlink()
                except OSError:
                    pass
            payload = {"rc": rc, "stdout": out, "stderr": err, "cancelled": cancelled}
            result = cmd_file.with_suffix(".result.json")
            tmp = result.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload))
            tmp.rename(result)
            _grant_user_read(result)
            cmd_file.unlink(missing_ok=True)
            trace(f"seq={seq} result file written")
        time.sleep(0.1)
    trace("worker exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
'''
