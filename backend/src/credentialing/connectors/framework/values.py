"""Parsing of single cell values, shared by every connector.

Sources write "no value" in many ways ("", "N/A", "0", "00000000"); these helpers turn all of
them into ``None`` so a placeholder never reaches a domain model as data.
"""

import re
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Final

from credentialing.connectors.framework.errors import ParseError
from credentialing.domain.enums import EntityKind, ExclusionAction
from credentialing.domain.models import Address
from credentialing.domain.types import validate_npi

PLACEHOLDERS: Final = frozenset({"", "N/A", "NA"})
"""Cell values that mean "no value" in free-text columns (compared case-insensitively)."""

DATE_PLACEHOLDERS: Final = frozenset({"", "0", "00000000", "N/A", "NA", "00/00/0000"})
"""Cell values that mean "no date"; OIG writes ``0`` for an absent reinstatement date."""

OPEN_ENDED_AFTER_YEAR: Final = 2100
"""End dates after this year are placeholders for "open-ended" (Pennsylvania writes 2299,
Vermont 2382-12-31), never real dates."""

_WHITESPACE: Final = re.compile(r"\s+")
_STATE: Final = re.compile(r"^[A-Z]{2}$")
_COMPACT_DATE: Final = "%Y%m%d"
_EIGHT_DIGITS: Final = re.compile(r"^[0-9]{8}$")
_TEN_DIGITS: Final = re.compile(r"(?<![0-9])[0-9]{10}(?![0-9])")
_IDENTIFIER_SEPARATORS: Final = re.compile(r"[\n\r;,]+")
_TRAILING_ZIP: Final = re.compile(r"[\s,]+([0-9]{5}(?:-?[0-9]{4})?)$")
_TRAILING_STATE: Final = re.compile(r"(?:^|[\s,]+)([A-Za-z]{2})\.?$")


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

    ``%Y%m%d`` (OIG's and spreadsheet integers such as ``20140301``) only accepts exactly eight
    digits, because ``strptime`` would otherwise read ``2014031`` as a date.

    Raises:
        ParseError: The text is neither a placeholder nor a date in one of ``formats``.
    """
    value = (text or "").strip()
    if value.upper() in DATE_PLACEHOLDERS:
        return None
    for date_format in formats:
        if date_format == _COMPACT_DATE and not _EIGHT_DIGITS.match(value):
            continue
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


def open_ended(end_date: date | None) -> date | None:
    """An end date, or ``None`` when it is a far-future placeholder for "no end"."""
    if end_date is not None and end_date.year > OPEN_ENDED_AFTER_YEAR:
        return None
    return end_date


def split_identifiers(text: str | None) -> tuple[str, ...]:
    """The identifiers in one cell, split on newlines, semicolons and commas, trimmed."""
    if not text:
        return ()
    return tuple(part for part in (p.strip() for p in _IDENTIFIER_SEPARATORS.split(text)) if part)


def parse_npi_list(text: str | None) -> tuple[str, ...]:
    """Every valid NPI in a cell, in order and without repeats.

    Some lists put two NPIs in one cell, typically a person's and their business's, in no
    reliable order. A connector stores the first as the record's ``npi`` and the others as
    ``related_npis``, never in ``aliases``, which would be matched as names; matching treats a
    related NPI as needing a confirming name.
    """
    found: list[str] = []
    for candidate in _TEN_DIGITS.findall(text or ""):
        if validate_npi(candidate) and candidate not in found:
            found.append(candidate)
    return tuple(found)


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


def parse_city_state_zip(text: str | None) -> Address | None:
    """Split a "CITY, ST 12345" cell into an address, best effort.

    The ZIP code, the state or the city may be missing ("Glen Burnie 21060", "Indianapolis,
    IN", "MD 21201"). A cell with neither a state nor a ZIP code is kept whole as the address
    line, so nothing is lost or misread as a city.
    """
    value = clean(text)
    if value is None:
        return None
    rest, postal_code, state = value, None, None
    if found := _TRAILING_ZIP.search(rest):
        postal_code, rest = parse_us_zip(found[1]), rest[: found.start()]
    if found := _TRAILING_STATE.search(rest):
        state, rest = found[1].upper(), rest[: found.start()]
    if state is None and postal_code is None:
        return Address(line1=value)
    return Address(city=rest.strip(" ,") or None, state=state, postal_code=postal_code)


def classify_action(
    text: str | None,
    keywords: Sequence[tuple[str, ExclusionAction]],
    *,
    default: ExclusionAction = ExclusionAction.SANCTION,
) -> ExclusionAction:
    """The action of the first keyword found in a free-text action cell, ignoring case.

    Order matters: the first match wins, so put the most specific keywords first. The text
    itself is kept by the connector as ``action_raw``.
    """
    folded = (text or "").casefold()
    for keyword, action in keywords:
        if keyword.casefold() in folded:
            return action
    return default


def entity_kind_from(business_name: str | None, last_name: str | None) -> EntityKind:
    """Person or organization for sources with separate business and person name columns."""
    if business_name:
        return EntityKind.ORGANIZATION
    if last_name:
        return EntityKind.INDIVIDUAL
    return EntityKind.UNKNOWN
