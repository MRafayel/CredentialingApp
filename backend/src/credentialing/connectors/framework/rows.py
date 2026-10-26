"""Row accounting: turn source rows into records without letting one bad row sink the file.

A row that cannot form a valid record is skipped and counted; an unreadable value in an
optional column (a garbled date) is counted and left empty, because dropping a listed party
over a bad date would turn into a false "no record". Too many skipped rows, or none usable,
reject the whole file: a mostly unreadable file must not replace the current list.
"""

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Final

from pydantic import ValidationError

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.values import parse_date
from credentialing.domain.models import ExclusionRecord

MAX_SKIPPED_ROWS: Final = 10
"""Skipped rows always tolerated, whatever the file size."""

MAX_SKIPPED_SHARE: Final = 0.01
"""Share of skipped rows beyond which a large file is rejected as a whole."""


class RowIssues:
    """Counts unreadable values by column while rows are mapped; never stores the values."""

    def __init__(self) -> None:
        self.unreadable: Counter[str] = Counter()

    def date(self, column: str, text: str | None, formats: Sequence[str]) -> date | None:
        """Parse a date cell; an unreadable one is counted under ``column`` and becomes None."""
        try:
            return parse_date(text, formats)
        except ParseError:
            self.unreadable[column] += 1
            return None


@dataclass(frozen=True, slots=True)
class ParsedList:
    """The records parsed from one document, with what was skipped or unreadable."""

    records: tuple[ExclusionRecord, ...]
    rows: int
    skipped: int = 0
    unreadable: Mapping[str, int] = field(default_factory=dict)

    def note(self) -> str:
        """A one-line summary for the snapshot note: counts and column names only."""
        note = f"{self.rows} rows, {len(self.records)} records, {self.skipped} skipped"
        if self.unreadable:
            columns = ", ".join(
                f"{name} {count}" for name, count in sorted(self.unreadable.items())
            )
            note += f"; unreadable values: {columns}"
        return note


type RowMapper = Callable[[Mapping[str, str], RowIssues], ExclusionRecord]


def build_records(rows: Iterable[Mapping[str, str]], build: RowMapper) -> ParsedList:
    """Map every row with ``build``, skipping rows that raise a validation or parse error.

    Errors raised while reading ``rows`` themselves (a broken file) are not caught.

    Raises:
        ParseError: No row was usable, or more were skipped than the thresholds allow.
    """
    issues = RowIssues()
    records: list[ExclusionRecord] = []
    count = skipped = 0
    for row in rows:
        count += 1
        try:
            records.append(build(row, issues))
        except ValidationError, ParseError:
            skipped += 1
    if not records:
        raise ParseError(f"no usable rows ({count} read)")
    if skipped > max(MAX_SKIPPED_ROWS, int(count * MAX_SKIPPED_SHARE)):
        raise ParseError(f"{skipped} of {count} rows unusable")
    return ParsedList(
        records=tuple(records), rows=count, skipped=skipped, unreadable=dict(issues.unreadable)
    )
