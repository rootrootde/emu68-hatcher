import logging
from collections import deque
from unittest.mock import Mock

import pytest
from emu68hatcher.builder.host._flash_progress import FlashProgress
from emu68hatcher.builder.host.disk_writer import _handle_progress_line


@pytest.mark.parametrize("verify", [True, False])
def test_hst_percent_progress_formats_sizes_and_time(verify):
    line = "97.9% [1 GB/s] [1 GB / 1 GB] [0h:00m:01s / 0h:00m:01s]    \r"
    callback = Mock()
    recent = deque(maxlen=30)

    _handle_progress_line(
        line, set(), callback, recent, statistics=FlashProgress(1024**3), verify=verify
    )

    callback.assert_called_once_with(
        48.95 if verify else 97.9,
        "Writing: 0.98 / 1.00 GiB\nCalculating speed | Elapsed 0:01",
    )
    assert not recent


def test_progress_logs_milestones_but_reports_every_update(caplog):
    callback = Mock()
    seen = set()
    with caplog.at_level(logging.INFO):
        for pct in (1.0, 2.0, 10.0, 11.0, 100.0):
            _handle_progress_line(
                f"{pct}% [9 MB/s] [1 GB / 2 GB] [0h:01m:00s / 0h:02m:00s]",
                seen,
                callback,
                deque(maxlen=30),
                statistics=FlashProgress(2 * 1024**3),
            )
    assert [call.args[0] for call in callback.call_args_list] == [0.5, 1.0, 5.0, 5.5, 50.0]
    assert len(caplog.records) == 3


@pytest.mark.parametrize(
    "line",
    [
        "[INF] image contains 50% free space",
        "120.0% [9 MB/s] [1 GB / 2 GB] [0h:01m:00s / 0h:02m:00s]",
    ],
)
def test_other_output_is_retained_for_errors(line):
    callback = Mock()
    recent = deque(maxlen=30)

    _handle_progress_line(line, set(), callback, recent, statistics=FlashProgress(1024**3))

    callback.assert_not_called()
    assert list(recent) == [line]


def test_byte_counter_progress_still_works():
    callback = Mock()

    _handle_progress_line(
        "[INF] Writing: 1,024 / 4,096 bytes (25 %)",
        set(),
        callback,
        deque(maxlen=30),
        statistics=FlashProgress(4096),
    )

    callback.assert_called_once_with(12.5, "Writing: 25.0%")


def test_startup_spike_leaves_recent_speed_and_eta():
    size = 60 * 1024**3
    statistics = FlashProgress(size)
    callback = Mock()
    seen = set()
    for elapsed in range(26):
        done = 0 if elapsed == 0 else 8 * 1024**3 + (elapsed - 1) * 40 * 1024**2
        percent = done / size * 100
        line = f"{percent:.1f}% [1 GB/s] [8 GB / 60 GB] [0h:00m:{elapsed:02}s / 0h:01m:00s]"
        _handle_progress_line(line, seen, callback, deque(maxlen=30), statistics=statistics)
    message = callback.call_args.args[1]
    assert "Processing 39.9 MiB/s" in message
    assert "Elapsed 0:25" in message
    assert "~21:49 left" in message
    assert "1 GB/s" not in message


def test_rate_uses_tool_elapsed_time_and_handles_duplicate_seconds():
    statistics = FlashProgress(1024**3)
    statistics.format("Writing", 0, "[1 GB/s] [0 GB / 1 GB] [0h:00m:00s / 0h:00m:01s]")
    statistics.format("Writing", 10, "[1 GB/s] [0 GB / 1 GB] [0h:00m:01s / 0h:00m:01s]")
    statistics.format("Writing", 15, "[1 GB/s] [0 GB / 1 GB] [0h:00m:01s / 0h:00m:01s]")
    message = statistics.format("Writing", 50, "[1 GB/s] [0 GB / 1 GB] [0h:00m:10s / 0h:00m:01s]")
    assert "Processing 51.2 MiB/s | Elapsed 0:10 | ~0:10 left" in message
    assert len(statistics._samples) == 3


def test_no_eta_when_processing_stalls():
    statistics = FlashProgress(1024**3)
    for elapsed in (0, 10, 20, 30):
        message = statistics.format(
            "Writing", 50, f"[1 GB/s] [0 GB / 1 GB] [0h:00m:{elapsed:02}s / 0h:00m:01s]"
        )
    assert "Processing 0.0 MiB/s" in message
    assert "left" not in message


@pytest.mark.parametrize("helper", [True, False])
@pytest.mark.parametrize("verify", [True, False])
def test_flash_reuses_recent_speed_tracker_for_both_process_paths(
    monkeypatch, tmp_path, helper, verify
):
    from types import SimpleNamespace

    from emu68hatcher.builder.host import disk_writer

    image = tmp_path / "image.img"
    with image.open("wb") as output:
        output.truncate(60 * 1024**3)
    callback = Mock()
    monkeypatch.setattr(disk_writer, "find_hst_imager", lambda: "hst-imager")
    monkeypatch.setattr(disk_writer, "check_flash_verification_support", lambda: None)
    monkeypatch.setattr(disk_writer, "refresh_elevation", lambda token: True)
    monkeypatch.setattr(disk_writer, "wrap_for_elevation", lambda args, token: args)

    def run(args, **kwargs):
        assert args[args.index("--verify") + 1] == "false"
        assert ("--verify-after" in args) is verify
        assert args[args.index("--force") + 1] == "false"
        for line in (
            "1.7% [1 GB/s] [1 GB / 59.5 GB] [0h:00m:00s / 0h:00m:58s]",
            "10.0% [196.1 MB/s] [5.9 GB / 59.5 GB] [0h:00m:30s / 0h:05m:09s]",
            "20.0% [63.2 MB/s] [11.9 GB / 59.5 GB] [0h:03m:12s / 0h:16m:04s]",
            "[INF] Verifying written data",
            "100.0% [63.2 MB/s] [59.5 GB / 59.5 GB] [0h:10m:00s / 0h:10m:00s]",
            "[INF] Post-write verification complete",
        ):
            if not verify and ("Verifying" in line or "verification" in line):
                continue
            if helper:
                kwargs["on_line"]("stdout", line)
            else:
                kwargs["on_line"](line)
        return SimpleNamespace(returncode=0, cancelled=False, timed_out=False, stop_failed=False)

    monkeypatch.setattr(disk_writer, "run_local_flash", run)
    elevation = (
        SimpleNamespace(method="test-helper", helper=SimpleNamespace(run=run)) if helper else None
    )
    disk_writer.flash_image_to_disk(
        image, "test-device", verify=verify, elevation=elevation, progress_callback=callback
    )
    reports = [call.args for call in callback.call_args_list]
    assert "Calculating speed" in reports[1][1]
    assert "Processing 37.9 MiB/s | Elapsed 3:12 | ~21:36 left" in reports[3][1]
    completion = "Flash and verification complete" if verify else "Flash complete (not verified)"
    assert reports[-1] == (100.0, completion)
