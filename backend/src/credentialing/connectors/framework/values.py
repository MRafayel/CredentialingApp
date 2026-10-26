"""Parsing of single cell values, shared by every connector.

Sources write "no value" in many ways ("", "N/A", "0", "00000000"); these helpers turn all of
them into ``None`` so a placeholder never reaches a domain model as data.
"""

import re
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Final

from credentialing.connectors.framework.errors import ParseError
from credentialing.domain.enums import EntityKind
from credentialing.domain.types import validate_npi

PLACEHOLDERS: Final = frozenset({"", "N/A", "NA"})
"""Cell values that mean "no value" in free-text columns (compared case-insensitively)."""

DATE_PLACEHOLDERS: Final = frozenset({"", "0", "00000000", "N/A", "NA", "00/00/0000"})
"""Cell values that mean "no date"; OIG writes ``0`` for an absent reinstatement date."""

_WHITESPACE: Final = re.compile(r"\s+")
_STATE: Final = re.compile(r"^[A-Z]{2}$")


def collapse(text: str | None) -> str | None:
    """Trim and collapse internal whitespace; empty becomes ``None``.

    Used for names, where placeholder words are not stripped: "NA" and "NULL" are real
    surnames.
    """
    if text is None:
        return None
    collapsed = _WHITESPACE.sub(" ", text).strip()
    return collapsed or None


def clean(text: str | None) -> str | None:
    """Collapse whitespace and turn placeholders such as "N/A" into ``None``."""
    collapsed = collapse(text)
    if collapsed is None or collapsed.upper() in PLACEHOLDERS:
        return None
    return collapsed


def parse_date(text: str | None, formats: Sequence[str]) -> date | None:
    """Parse a date in the first matching ``strptime`` format; placeholders become ``None``.

    Raises:
        ParseError: The text is neither a placeholder nor a date in one of ``formats``.
    """
    value = (text or "").strip()
    if value.upper() in DATE_PLACEHOLDERS:
        return None
    for date_format in formats:
        try:
            return datetime.strptime(value, date_format).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    raise ParseError(f"not a date in {', '.join(formats)}")


def parse_npi(text: str | None) -> str | None:
    """Return the NPI when the cell holds exactly ten digits with a valid check digit.

    Anything else (OIG's ``0`` placeholder, a nine-digit value, a typo) becomes ``None`` and the
    original stays in the record's raw row. A wrong NPI is worse than none: matching treats
    two different NPIs as proof of a different person.
    """
    value = (text or "").strip()
    return value if validate_npi(value) else None


def parse_state(text: str | None) -> str | None:
    """A two-letter state code, upper-cased, or ``None`` when the cell holds anything else."""
    value = (text or "").strip().upper()
    return value if _STATE.match(value) else None


def parse_us_zip(text: str | None) -> str | None:
    """Normalize a U.S. ZIP code, restoring leading zeros that spreadsheet exports drop.

    ``2134`` becomes ``02134`` and nine digits become ``ZIP+4``; all zeros means no ZIP.
    Anything that is not digits is kept as cleaned text.
    """
    value = clean(text)
    if value is None:
        return None
    digits = value.replace("-", "")
    if not digits.isdigit():
        return value
    if set(digits) == {"0"}:
        return None
    if len(digits) == 9:
        return f"{digits[:5]}-{digits[5:]}"
    if 3 <= len(digits) <= 5:
        return digits.zfill(5)
    return value


def entity_kind_from(business_name: str | None, last_name: str | None) -> EntityKind:
    """Person or organization for sources with separate business and person name columns."""
    if business_name:
        return EntityKind.ORGANIZATION
    if last_name:
        return EntityKind.INDIVIDUAL
    return EntityKind.UNKNOWN
