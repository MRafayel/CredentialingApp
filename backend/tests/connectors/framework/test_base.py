from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from credentialing.connectors.framework.base import BulkListConnectorBase
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.http import FetchedDocument, FetchFailure, NotModified
from credentialing.connectors.framework.rows import (
    MAX_SKIPPED_ROWS,
    ParsedList,
    RowIssues,
    build_records,
)
from credentialing.domain.enums import EntityKind, ExclusionAction, Outcome
from credentialing.domain.models import (
    ConnectorCapabilities,
    ExclusionRecord,
    PersonName,
    SourceSnapshot,
)
from tests.fakes import ScriptedFetcher, capabilities
from tests.synthetic import NOW

pytestmark = pytest.mark.anyio

CAPABILITIES = capabilities("test_exclusions")


def _document(body: bytes = b"rows") -> FetchedDocument:
    return FetchedDocument(
        body=body,
        content_hash="cd" * 32,
        etag='"v2"',
        last_modified=datetime(2026, 10, 9, tzinfo=UTC),
        content_type="text/csv",
        final_url="https://example.test/final.csv",
        byte_size=len(body),
        fetched_at=NOW,
        status=200,
    )


def _record(snapshot: SourceSnapshot, last: str) -> ExclusionRecord:
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=snapshot.source_id,
        snapshot_id=snapshot.id,
        entity_kind=EntityKind.INDIVIDUAL,
        person_name=PersonName(last=last, first="Avery"),
        action=ExclusionAction.EXCLUSION,
        raw={"NAME": last},
        valid_from=snapshot.fetched_at,
    )


class _Connector(BulkListConnectorBase):
    download_url = "https://example.test/list.csv"

    def __init__(self, fetcher: ScriptedFetcher, *, fail: Exception | None = None) -> None:
        super().__init__(fetcher)
        self._fail = fail

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return CAPABILITIES

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        if self._fail is not None:
            raise self._fail
        return ParsedList(records=(_record(snapshot, "Testperson"),), rows=2, skipped=1)


async def test_a_new_document_is_parsed_into_records_of_its_snapshot() -> None:
    result = await _Connector(ScriptedFetcher(_document())).fetch(None)

    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.snapshot.source_url == "https://example.test/final.csv"
    assert result.snapshot.row_count == 1
    assert result.snapshot.note == "2 rows, 1 records, 1 skipped"
    assert result.records is not None
    assert [record.snapshot_id for record in result.records] == [result.snapshot.id]


@pytest.mark.parametrize(
    ("error", "note"),
    [
        (ParseError("unexpected header: got A; expected B"), "unexpected header"),
        (KeyError("LASTNAME"), "KeyError"),
    ],
)
async def test_a_parse_error_is_a_failed_snapshot_without_records_or_validators(
    error: Exception, note: str
) -> None:
    result = await _Connector(ScriptedFetcher(_document()), fail=error).fetch(None)

    assert result.snapshot.outcome is Outcome.FAILED
    assert result.records is None
    assert result.snapshot.note is not None
    assert note in result.snapshot.note
    assert result.snapshot.content_hash is None
    assert result.snapshot.etag is None
    assert result.snapshot.last_modified is None


async def test_an_unchanged_source_keeps_the_previous_validators() -> None:
    previous = SourceSnapshot(
        id=uuid4(),
        source_id="test_exclusions",
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        outcome=Outcome.COMPLETED,
        source_url="https://example.test/list.csv",
        content_hash="ab" * 32,
        etag='"v1"',
        last_modified=datetime(2025, 12, 31, tzinfo=UTC),
        byte_size=100,
        row_count=7,
    )
    unchanged = NotModified(
        reason="HTTP 304", etag=None, last_modified=None, fetched_at=NOW, status=304
    )

    result = await _Connector(ScriptedFetcher(unchanged)).fetch(previous)

    assert result.records is None
    snapshot = result.snapshot
    assert snapshot.outcome is Outcome.COMPLETED
    assert snapshot.id != previous.id
    assert snapshot.fetched_at == NOW
    assert snapshot.note == "unchanged (HTTP 304)"
    assert (snapshot.content_hash, snapshot.etag, snapshot.last_modified) == (
        previous.content_hash,
        previous.etag,
        previous.last_modified,
    )
    assert (snapshot.byte_size, snapshot.row_count) == (100, 7)


async def test_a_failed_fetch_yields_its_outcome_and_no_records() -> None:
    failure = FetchFailure(outcome=Outcome.BLOCKED, status=403, note="HTTP 403", fetched_at=NOW)

    result = await _Connector(ScriptedFetcher(failure)).fetch(None)

    assert result.snapshot.outcome is Outcome.BLOCKED
    assert result.snapshot.note == "HTTP 403"
    assert result.records is None


def _snapshot() -> SourceSnapshot:
    return SourceSnapshot(
        id=uuid4(),
        source_id="test_exclusions",
        fetched_at=NOW,
        outcome=Outcome.COMPLETED,
        source_url="https://example.test/list.csv",
    )


def test_build_records_skips_invalid_rows_and_counts_unreadable_dates() -> None:
    snapshot = _snapshot()

    def build(row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
        issues.date("DATE", row["DATE"], ("%Y%m%d",))
        if not row["NAME"]:
            raise ParseError("no name")
        return _record(snapshot, row["NAME"])

    rows = [
        {"NAME": "Testperson", "DATE": "20200101"},
        {"NAME": "", "DATE": "20200101"},
        {"NAME": "Sampleton", "DATE": "garbage"},
    ]
    parsed = build_records(rows, build)

    assert len(parsed.records) == 2
    assert parsed.note() == "3 rows, 2 records, 1 skipped; unreadable values: DATE 1"


def test_build_records_rejects_a_file_without_usable_rows() -> None:
    def build(row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
        raise ParseError("no name")

    with pytest.raises(ParseError, match="no usable rows"):
        build_records([{"NAME": ""}], build)


def test_build_records_rejects_a_file_with_too_many_bad_rows() -> None:
    snapshot = _snapshot()

    def build(row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
        if not row["NAME"]:
            raise ParseError("no name")
        return _record(snapshot, row["NAME"])

    rows = [{"NAME": ""}] * (MAX_SKIPPED_ROWS + 1) + [{"NAME": "Testperson"}]
    with pytest.raises(ParseError, match="rows unusable"):
        build_records(rows, build)
