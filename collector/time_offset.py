"""Broker server time -> UTC.

MT5 reports tick times in the broker's server time (an "epoch" shifted by the server's
UTC offset, which changes with DST). The offset is estimated as

    round((latest_tick_time - utc_now) / 3600)  hours

while the market is active, and re-checked periodically. A stale tick would give a
wrong answer, so the estimate is rejected (None) unless the difference is within
`max_residual_hours` of a whole number of hours, i.e. the tick is fresh.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

MS_PER_HOUR = 3_600_000
MAX_ABS_OFFSET_H = 14


def estimate_offset_hours(latest_server_ms: int, utc_now_ms: int,
                          max_residual_hours: float = 0.1) -> Optional[int]:
    diff_h = (latest_server_ms - utc_now_ms) / MS_PER_HOUR
    offset = round(diff_h)
    if abs(diff_h - offset) > max_residual_hours:
        return None  # not a whole-hour difference -> tick is stale/ambiguous
    if abs(offset) > MAX_ABS_OFFSET_H:
        return None
    return int(offset)


def server_to_utc_ms(server_ms: int, offset_hours: int) -> int:
    return server_ms - offset_hours * MS_PER_HOUR


def utc_to_server_ms(utc_ms: int, offset_hours: int) -> int:
    return utc_ms + offset_hours * MS_PER_HOUR


@dataclass
class OffsetTracker:
    """Holds the current offset and decides when to re-check it."""
    recheck_seconds: int = 3600
    offset_hours: Optional[int] = None
    last_check_ms: Optional[int] = None

    def due(self, utc_now_ms: int) -> bool:
        return (self.offset_hours is None or self.last_check_ms is None
                or utc_now_ms - self.last_check_ms >= self.recheck_seconds * 1000)

    def update(self, latest_server_ms: int, utc_now_ms: int,
               market_is_open: bool) -> Tuple[bool, Optional[int], Optional[int]]:
        """Try to refresh the offset. Returns (changed, old, new).

        Does nothing when the market is closed or the estimate is ambiguous; the
        previous offset is kept and the check will be retried on the next call.
        """
        if not market_is_open:
            return False, self.offset_hours, self.offset_hours
        est = estimate_offset_hours(latest_server_ms, utc_now_ms)
        if est is None:
            return False, self.offset_hours, self.offset_hours
        old = self.offset_hours
        self.offset_hours = est
        self.last_check_ms = utc_now_ms
        return (old is not None and old != est), old, est
