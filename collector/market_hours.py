"""Spot-FX weekly schedule: open Sunday 17:00 New York -> close Friday 17:00 New York.

Used only to decide whether silence is a 'gap' (market open) or normal idling (closed).
Holidays (e.g. Dec 25, Jan 1) are not modelled: a closure then will log a 'gap',
which is accurate - no ticks arrived while the schedule said the market was open.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def market_open(utc_ms: int) -> bool:
    ny = datetime.fromtimestamp(utc_ms / 1000.0, tz=timezone.utc).astimezone(NY)
    wd, hour = ny.weekday(), ny.hour  # Mon=0 ... Sun=6
    if wd == 5:                 # Saturday: closed
        return False
    if wd == 4 and hour >= 17:  # Friday after 17:00: closed
        return False
    if wd == 6 and hour < 17:   # Sunday before 17:00: closed
        return False
    return True
