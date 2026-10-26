"""Logging configuration, applied once by the composition root.

Lines read ``time level logger message`` with UTC timestamps; messages carry ``key=value``
pairs (source ids, outcomes, statuses, counts), never personal data or raw rows.
"""

import logging
import time
from typing import Final

_FORMAT: Final = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DATE_FORMAT: Final = "%Y-%m-%dT%H:%M:%SZ"
_QUIET: Final = ("httpx2", "httpcore2", "psycopg")
"""Libraries whose per-request INFO lines repeat what our own log lines already say."""


def configure_logging(level: str) -> None:
    """Send every log record to stderr in the project format at ``level``."""
    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)
    formatter.converter = time.gmtime
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in _QUIET:
        logging.getLogger(name).setLevel(max(logging.WARNING, root.level))
