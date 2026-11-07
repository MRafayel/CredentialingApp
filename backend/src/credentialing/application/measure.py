"""Measuring what a use case costs: wall-clock phases and the process's CPU time, the same way
for every run that reports them (dataset loads, NPI linking)."""

import resource
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta


def cpu_seconds() -> float:
    """User plus system CPU time of this process (every thread) so far."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


@contextmanager
def phase(phases: dict[str, timedelta], name: str) -> Iterator[None]:
    """Add the wall-clock time of the block to ``phases[name]``; phases keep the order they
    first ran in."""
    started = time.perf_counter()
    try:
        yield
    finally:
        spent = timedelta(seconds=time.perf_counter() - started)
        phases[name] = phases.get(name, timedelta(0)) + spent
