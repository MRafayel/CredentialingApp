"""The system clock adapter."""

from datetime import UTC, datetime


class SystemClock:
    """The real clock: timezone-aware UTC now."""

    def now(self) -> datetime:
        """Return the current UTC time."""
        return datetime.now(UTC)
