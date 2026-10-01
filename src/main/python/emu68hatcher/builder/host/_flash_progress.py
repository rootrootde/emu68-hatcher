from __future__ import annotations

import re
from collections import deque

_ELAPSED_RE = re.compile(r"\[(\d+)h:(\d+)m:(\d+)s\s*/")
_RATE_WINDOW = 20
_RATE_WARMUP = 5


def _format_duration(seconds: float) -> str:
    hours, remainder = divmod(round(seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


class FlashProgress:
    def __init__(self, image_bytes: int):
        self.image_bytes = image_bytes
        self._samples: deque[tuple[int, float]] = deque()

    def format(self, phase: str, percent: float, details: str) -> str:
        done = self.image_bytes * percent / 100
        divisor, unit = 1, "B"
        for candidate, label in (
            (1024**4, "TiB"),
            (1024**3, "GiB"),
            (1024**2, "MiB"),
            (1024, "KiB"),
        ):
            if self.image_bytes >= candidate:
                divisor, unit = candidate, label
                break
        message = f"{phase}: {done / divisor:.2f} / {self.image_bytes / divisor:.2f} {unit}"
        elapsed_match = _ELAPSED_RE.search(details)
        if not elapsed_match:
            return message
        hours, minutes, seconds = map(int, elapsed_match.groups())
        elapsed = hours * 3600 + minutes * 60 + seconds
        rate = self._recent_rate(elapsed, done)
        timing = f"Elapsed {_format_duration(elapsed)}"
        if percent >= 100:
            return f"{message}\n{timing}"
        if rate is None:
            return f"{message}\nCalculating speed | {timing}"
        speed = f"Processing {rate / 1024**2:.1f} MiB/s"
        if rate > 0:
            timing += f" | ~{_format_duration((self.image_bytes - done) / rate)} left"
        return f"{message}\n{speed} | {timing}"

    def _recent_rate(self, elapsed: int, done: float) -> float | None:
        # use the tool clock so buffered output cannot inflate the rate
        if self._samples and (elapsed < self._samples[-1][0] or done < self._samples[-1][1]):
            self._samples.clear()
        if self._samples and elapsed == self._samples[-1][0]:
            self._samples.pop()
        self._samples.append((elapsed, done))
        cutoff = elapsed - _RATE_WINDOW
        while len(self._samples) > 2 and self._samples[1][0] <= cutoff:
            self._samples.popleft()
        start, start_done = self._samples[0]
        if elapsed - start < _RATE_WARMUP:
            return None
        if start < cutoff:
            next_time, next_done = self._samples[1]
            start_done += (next_done - start_done) * (cutoff - start) / (next_time - start)
            start = cutoff
        return (done - start_done) / (elapsed - start)
