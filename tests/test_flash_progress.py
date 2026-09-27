import logging
from collections import deque
from unittest.mock import Mock

import pytest
from emu68hatcher.builder.host.disk_writer import _handle_progress_line


@pytest.mark.parametrize("verify", [True, False])
def test_hst_percent_progress_includes_speed_and_time(verify):
    line = "97.9% [1 GB/s] [1 GB / 1 GB] [0h:00m:01s / 0h:00m:01s]    \r"
    callback = Mock()
    recent = deque(maxlen=30)

    _handle_progress_line(line, set(), callback, recent, verify=verify)

    phase = "Writing and verifying" if verify else "Writing"
    callback.assert_called_once_with(
        97.9, f"{phase}: 97.9% [1 GB/s] [1 GB / 1 GB] [0h:00m:01s / 0h:00m:01s]"
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
            )
    assert [call.args[0] for call in callback.call_args_list] == [1.0, 2.0, 10.0, 11.0, 100.0]
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

    _handle_progress_line(line, set(), callback, recent)

    callback.assert_not_called()
    assert list(recent) == [line]


def test_byte_counter_progress_still_works():
    callback = Mock()

    _handle_progress_line(
        "[INF] Writing: 1,024 / 4,096 bytes (25 %)", set(), callback, deque(maxlen=30)
    )

    callback.assert_called_once_with(25.0, "Writing: 25.0%")
