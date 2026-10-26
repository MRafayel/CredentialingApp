"""Short relative times for terminal output: "35 min ago", "in 6 d", "overdue 3 h".

Pure functions of a moment and "now", so the wording is tested without a clock. Every unit is
rounded down, so "in 6 d" means at least six whole days.
"""

from datetime import datetime, timedelta
from typing import Final

_MINUTE: Final = timedelta(minutes=1)
_HOUR: Final = timedelta(hours=1)
_DAY: Final = timedelta(days=1)


def span(delta: timedelta) -> str:
    """A non-negative duration in its largest whole unit: "45 s", "35 min", "3 h", "7 d"."""
    if delta < timedelta(0):
        raise ValueError("a span cannot be negative")
    if delta >= _DAY:
        return f"{delta // _DAY} d"
    if delta >= _HOUR:
        return f"{delta // _HOUR} h"
    if delta >= _MINUTE:
        return f"{delta // _MINUTE} min"
    return f"{int(delta.total_seconds())} s"


def ago(moment: datetime, now: datetime) -> str:
    """How long before ``now`` ``moment`` was; a moment in the future reads "just now"."""
    elapsed = now - moment
    if elapsed < _MINUTE:
        return "just now"
    if _DAY <= elapsed < 2 * _DAY:
        return "yesterday"
    return f"{span(elapsed)} ago"


def until(moment: datetime, now: datetime) -> str:
    """How long after ``now`` ``moment`` is due, or how long overdue it already is."""
    remaining = moment - now
    if abs(remaining) < _MINUTE:
        return "due now"
    if remaining > timedelta(0):
        return f"in {span(remaining)}"
    return f"overdue {span(-remaining)}"
