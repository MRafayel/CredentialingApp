"""Row accounting: turn source rows into records without letting one bad row sink the file.

A row that cannot form a valid record is skipped and counted; an unreadable value in an
optional column (a garbled date) is counted and left empty, because dropping a listed party
over a bad date would turn into a false "no record". Too many skipped rows, or none usable,
reject the whole file: a mostly unreadable file must not replace the current list. A reader
that cannot split a line into the source's columns (a stray fragment) passes an
:class:`UnreadableRow` in its place, which is counted the same way. Rows that repeat one
record identity can be merged into one record with :func:`merge_duplicates`.
"""

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Final

from pydantic import ValidationError

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.values import clean, parse_date
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
class UnreadableRow:
    """A line a reader could not split into the source's columns; ``reason`` names no values."""

    reason: str


@dataclass(frozen=True, slots=True)
class ParsedList:
    """The records parsed from one document, with what was skipped, merged or unreadable.

    ``rows`` counts source rows; ``merged`` the rows folded into another row's record.
    """

    records: tuple[ExclusionRecord, ...]
    rows: int
    skipped: int = 0
    unreadable: Mapping[str, int] = field(default_factory=dict)
    merged: int = 0

    def note(self) -> str:
        """A one-line summary for the snapshot note: counts and column names only."""
        note = f"{self.rows} rows, {len(self.records)} records, {self.skipped} skipped"
        if self.merged:
            note += f"; {self.merged} merged"
        if self.unreadable:
            columns = ", ".join(
                f"{name} {count}" for name, count in sorted(self.unreadable.items())
            )
            note += f"; unreadable values: {columns}"
        return note


type RowMapper = Callable[[Mapping[str, str], RowIssues], ExclusionRecord]


def build_records(
    rows: Iterable[Mapping[str, str] | UnreadableRow], build: RowMapper
) -> ParsedList:
    """Map every row with ``build``, skipping rows that raise a validation or parse error.

    An :class:`UnreadableRow` from the reader is counted as a row and skipped.

    Errors raised while reading ``rows`` themselves (a broken file) are not caught.

    Raises:
        ParseError: No row was usable, or more were skipped than the thresholds allow.
    """
    issues = RowIssues()
    records: list[ExclusionRecord] = []
    count = skipped = 0
    for row in rows:
        count += 1
        if isinstance(row, UnreadableRow):
            skipped += 1
            continue
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


def merge_duplicates(
    records: Iterable[ExclusionRecord], *, joined: Iterable[str], separator: str = "; "
) -> tuple[ExclusionRecord, ...]:
    """Merge records that share one identity into one record, keeping first-seen order.

    Identity is the record hash, so rows that differ in any identifying field are never merged;
    the ``joined`` raw columns keep every distinct value, in order, joined with ``separator``.
    Placeholders ("N/A") and spelling that differs only in whitespace are not repeated.
    """
    groups: dict[str, list[ExclusionRecord]] = {}
    for record in records:
        groups.setdefault(record.record_hash, []).append(record)
    columns = tuple(joined)
    return tuple(
        group[0] if len(group) == 1 else _merge(group, columns, separator)
        for group in groups.values()
    )


def _merge(
    group: Sequence[ExclusionRecord], joined: Sequence[str], separator: str
) -> ExclusionRecord:
    first = group[0]
    raw = dict(first.raw)
    for column in joined:
        values = _distinct(record.raw.get(column) for record in group)
        if column in raw and values:
            raw[column] = separator.join(values)
    return ExclusionRecord.create(
        id=first.id,
        source_id=first.source_id,
        snapshot_id=first.snapshot_id,
        entity_kind=first.entity_kind,
        person_name=first.person_name,
        organization_name=first.organization_name,
        aliases=_union(record.aliases for record in group),
        related_parties=_union(record.related_parties for record in group),
        npi=first.npi,
        related_npis=_union(record.related_npis for record in group),
        state_provider_id=_first(record.state_provider_id for record in group),
        license=_first(record.license for record in group),
        date_of_birth=_first(record.date_of_birth for record in group),
        address=_first(record.address for record in group),
        action=first.action,
        action_raw=_first(record.action_raw for record in group),
        effective_date=first.effective_date,
        end_date=_first(record.end_date for record in group),
        reason=_first(record.reason for record in group),
        raw=raw,
        valid_from=first.valid_from,
    )


def _distinct(values: Iterable[str | None]) -> tuple[str, ...]:
    distinct: dict[str, str] = {}
    for value in values:
        key = clean(value)
        if value is not None and key is not None:
            distinct.setdefault(key, value)
    return tuple(distinct.values())


def _union(values: Iterable[tuple[str, ...]]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for items in values for item in items))


def _first[T](values: Iterable[T | None]) -> T | None:
    return next((value for value in values if value is not None), None)
