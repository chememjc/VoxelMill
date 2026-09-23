"""Per-stage wall-time breakdown for CLI reports.

``StageTimer`` is a re-entrant context manager: nested stages accumulate
exclusively (parent excludes child wall time) so a flat table can sum to
roughly the overall run. Skipped stages are simply absent; nothing raises.
"""
from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from typing import Any, Iterator, TextIO


class StageTimer:
    """Accumulate named stage durations with ``time.monotonic()``."""

    def __init__(self):
        self._totals: dict[str, float] = {}
        self._stack: list[dict] = []

    @contextmanager
    def stage(self, name: str) -> Iterator['StageTimer']:
        frame: dict[str, Any] = {'name': str(name), 'start': time.monotonic(), 'child': 0.0}
        self._stack.append(frame)
        try:
            yield self
        finally:
            end = time.monotonic()
            frame = self._stack.pop()
            elapsed = end - frame['start']
            exclusive = max(0.0, elapsed - frame['child'])
            key = frame['name']
            self._totals[key] = self._totals.get(key, 0.0) + exclusive
            if self._stack:
                # Parent exclusive time excludes this child's full wall span.
                self._stack[-1]['child'] += elapsed

    def add(self, name: str, seconds: float) -> None:
        """Accumulate an externally measured duration (skipped if non-finite)."""
        value = float(seconds)
        if value < 0 or value != value:  # NaN check
            return
        key = str(name)
        self._totals[key] = self._totals.get(key, 0.0) + value

    def as_dict(self) -> dict[str, float]:
        return {name: float(seconds) for name, seconds in self._totals.items()}


def format_timing_table(timing: dict, total: float | None = None,
                        *, file: TextIO | None = None) -> str:
    """Compact stage / seconds / percent table. Returns the text; may also write it."""
    rows = []
    for name, seconds in timing.items():
        if not isinstance(seconds, (int, float)):
            continue
        rows.append((str(name), float(seconds)))
    if total is None or not (total == total) or total < 0:
        total = sum(seconds for _, seconds in rows) or 0.0
    lines = ['stage                  seconds    %']
    for name, seconds in rows:
        pct = (100.0 * seconds / total) if total > 0 else 0.0
        lines.append(f'{name:<20} {seconds:8.3f} {pct:6.1f}')
    lines.append(f'{"total":<20} {float(total):8.3f}  100.0')
    text = '\n'.join(lines)
    if file is not None:
        print(text, file=file)
    return text


def print_timing_table(timing: dict, total: float | None = None,
                       *, file: TextIO | None = None) -> None:
    format_timing_table(timing, total, file=file if file is not None else sys.stderr)
