"""Calling-window evaluation: a clock check, not a trigger (SD-22).

The worker is running continuously anyway; it simply declines to dial
outside the window. Times are evaluated in the contact's local time,
sourced from the client account (SD-21).
"""

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class TimeBand:
    start: time
    end: time

    def contains(self, t: time) -> bool:
        return self.start <= t < self.end


@dataclass(frozen=True)
class CallingWindow:
    window: TimeBand
    days: frozenset[int]  # ISO weekday, 1=Monday .. 7=Sunday
    excluded_bands: tuple[TimeBand, ...] = ()
    timezone: str = "Africa/Lagos"


def is_open(window: CallingWindow, now_utc: datetime) -> bool:
    local = now_utc.astimezone(ZoneInfo(window.timezone))
    if local.isoweekday() not in window.days:
        return False
    t = local.time()
    if not window.window.contains(t):
        return False
    return all(not band.contains(t) for band in window.excluded_bands)
