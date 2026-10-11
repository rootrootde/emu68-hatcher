import os
import threading

import pytest
from emu68hatcher.builder.host._elevated_worker_src import WORKER_SCRIPT


def _worker_functions():
    namespace = {"__name__": "worker_test"}
    exec(compile(WORKER_SCRIPT, "elevated_worker.py", "exec"), namespace)
    return namespace


@pytest.mark.parametrize("separator", [b"\r", b"\n", b"\r\n"])
def test_progress_reaches_chunk_before_pipe_closes(tmp_path, separator):
    worker = _worker_functions()
    emitted = threading.Event()
    worker["_grant_user_read"] = lambda path: emitted.set()
    read_fd, write_fd = os.pipe()
    reader = os.fdopen(read_fd, "rb", buffering=0)
    output = bytearray()
    thread = threading.Thread(
        target=worker["_stream_reader"],
        args=(reader, tmp_path, 1, "out", output),
        daemon=True,
    )
    progress = b"12.3% [4 MB/s] [8 MB / 64 MB] [0h:00m:02s / 0h:00m:16s]" + separator
    thread.start()
    try:
        os.write(write_fd, progress)
        assert emitted.wait(2), "progress remained buffered until EOF"
        assert (tmp_path / "cmd-1.out.000001").read_bytes() == progress
        assert not (tmp_path / "cmd-1.out.done").exists()
        assert thread.is_alive()
    finally:
        os.close(write_fd)
        thread.join(2)
    assert not thread.is_alive()
    assert output == progress


def test_progress_chunks_preserve_partial_lines_and_utf8(tmp_path):
    worker = _worker_functions()
    worker["_grant_user_read"] = lambda path: None
    fragments = [b"first\r", b"\nnext caf\xc3", b"\xa9\rpartial", b" line\nlast", b""]

    class Pipe:
        def read(self, _size):
            return fragments.pop(0)

        def close(self):
            pass

    output = bytearray()
    worker["_stream_reader"](Pipe(), tmp_path, 1, "out", output)
    chunks = [path.read_bytes() for path in sorted(tmp_path.glob("cmd-1.out.0*"))]
    assert b"".join(chunks) == output == b"first\r\nnext caf\xc3\xa9\rpartial line\nlast"
    assert [line for chunk in chunks for line in chunk.decode("utf-8").splitlines() if line] == [
        "first",
        "next café",
        "partial line",
        "last",
    ]
    assert (tmp_path / "cmd-1.out.done").exists()
