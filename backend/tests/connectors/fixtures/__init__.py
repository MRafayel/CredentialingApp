"""Recorded source shapes with made-up people and identifiers, one file per connector."""

from pathlib import Path

FIXTURES = Path(__file__).parent


def fixture_bytes(name: str) -> bytes:
    """The raw bytes of a fixture file, exactly as a source would send them."""
    return (FIXTURES / name).read_bytes()
