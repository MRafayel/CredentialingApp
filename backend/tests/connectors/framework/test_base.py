import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from credentialing.connectors.framework.base import BulkListConnectorBase, Metadata
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.http import (
    FetchedDocument,
    FetchFailure,
    FetchOutcome,
    NotModified,
)
from credentialing.connectors.framework.rows import (
    MAX_SKIPPED_ROWS,
    ParsedList,
    RowIssues,
    build_records,
)
from credentialing.domain.enums import ChangeSignal, EntityKind, ExclusionAction, Outcome
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


_LANDING = "https://example.test/providers/sanctions/"
_API = "https://data.example.test/api/3/action/resource_show?id=r1"
_PAGE = b'<html><body><a href="/uploads/list-2026-10-01.xlsx">List</a></body></html>'
_FILE_URL = "https://example.test/uploads/list-2026-10-01.xlsx"


def _page(body: bytes = _PAGE, *, url: str = _LANDING) -> FetchedDocument:
    return FetchedDocument(
        body=body,
        content_hash="aa" * 32,
        etag=None,
        last_modified=None,
        content_type="text/html; charset=utf-8",
        final_url=url,
        byte_size=len(body),
        fetched_at=NOW,
        status=200,
    )


def _completed(url: str, *, etag: str | None = None) -> SourceSnapshot:
    return SourceSnapshot(
        id=uuid4(),
        source_id="test_exclusions",
        fetched_at=datetime(2026, 10, 1, tzinfo=UTC),
        outcome=Outcome.COMPLETED,
        source_url=url,
        content_hash="ab" * 32,
        etag=etag,
        last_modified=datetime(2026, 9, 30, tzinfo=UTC),
        byte_size=100,
        row_count=7,
    )


class _LinkConnector(_Connector):
    landing_url = _LANDING

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return CAPABILITIES.model_copy(
            update={
                "change_signal": ChangeSignal.LINK_DISCOVERY,
                "download_url": None,
                "crawl_delay": timedelta(seconds=3),
            }
        )

    def find_link(self, html: str) -> str:
        found = re.search(r'href="([^"]+\.xlsx)"', html)
        if found is None:
            raise ParseError("no workbook link")
        return found[1]


async def test_link_discovery_downloads_the_link_found_on_the_landing_page() -> None:
    fetcher = ScriptedFetcher(_page(), _document())

    result = await _LinkConnector(fetcher).fetch(None)

    assert fetcher.requests == [
        (_LANDING, ChangeSignal.CONTENT_HASH),
        (_FILE_URL, ChangeSignal.CONTENT_HASH),
    ]
    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.snapshot.source_url == _FILE_URL
    assert result.snapshot.etag == '"v2"'
    assert result.records is not None


async def test_an_unchanged_link_is_one_request_and_keeps_the_validators() -> None:
    previous = _completed(_FILE_URL, etag='"v1"')
    fetcher = ScriptedFetcher(_page())

    result = await _LinkConnector(fetcher).fetch(previous)

    assert len(fetcher.requests) == 1
    assert result.records is None
    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.snapshot.note == "unchanged (link unchanged)"
    assert result.snapshot.source_url == _FILE_URL
    assert (result.snapshot.etag, result.snapshot.row_count) == ('"v1"', 7)


async def test_a_new_link_is_downloaded_without_the_old_files_validators() -> None:
    previous = _completed("https://example.test/uploads/list-2026-09-01.xlsx", etag='"v1"')
    fetcher = ScriptedFetcher(_page(), _document())

    result = await _LinkConnector(fetcher).fetch(previous)

    assert fetcher.requests[1] == (_FILE_URL, ChangeSignal.CONTENT_HASH)
    assert result.records is not None


async def test_the_same_link_after_a_failed_fetch_is_downloaded_again() -> None:
    previous = _completed(_FILE_URL).model_copy(update={"outcome": Outcome.FAILED})
    fetcher = ScriptedFetcher(_page(), _document())

    result = await _LinkConnector(fetcher).fetch(previous)

    assert fetcher.requests[1] == (_FILE_URL, ChangeSignal.CONDITIONAL_GET)
    assert result.records is not None


async def test_a_landing_page_without_the_link_is_failed_not_empty() -> None:
    result = await _LinkConnector(ScriptedFetcher(_page(b"<html>moved</html>"))).fetch(None)

    assert result.snapshot.outcome is Outcome.FAILED
    assert result.snapshot.note == "no download link on the landing page: no workbook link"
    assert result.snapshot.source_url == _LANDING
    assert result.records is None


@pytest.mark.parametrize("outcome", [Outcome.BLOCKED, Outcome.SOURCE_UNAVAILABLE])
async def test_a_landing_page_failure_keeps_its_outcome(outcome: Outcome) -> None:
    failure = FetchFailure(outcome=outcome, status=None, note="synthetic", fetched_at=NOW)

    result = await _LinkConnector(ScriptedFetcher(failure)).fetch(_completed(_FILE_URL))

    assert result.snapshot.outcome is outcome
    assert result.records is None


async def test_a_failed_download_of_the_found_link_keeps_its_outcome() -> None:
    failure = FetchFailure(outcome=Outcome.FAILED, status=404, note="HTTP 404", fetched_at=NOW)

    result = await _LinkConnector(ScriptedFetcher(_page(), failure)).fetch(None)

    assert result.snapshot.outcome is Outcome.FAILED
    assert result.snapshot.source_url == _FILE_URL


async def test_the_crawl_delay_reaches_the_fetcher() -> None:
    delays: list[timedelta] = []

    class _Recording(ScriptedFetcher):
        async def fetch(
            self,
            url: str,
            *,
            previous: SourceSnapshot | None,
            change_signal: ChangeSignal,
            crawl_delay: timedelta = timedelta(0),
        ) -> FetchOutcome:
            delays.append(crawl_delay)
            return await super().fetch(url, previous=previous, change_signal=change_signal)

    await _LinkConnector(_Recording(_page(), _document())).fetch(None)

    assert delays == [timedelta(seconds=3), timedelta(seconds=3)]


_BODY = b"rows"
_MD5 = hashlib.md5(_BODY, usedforsecurity=False).hexdigest()


class _ApiConnector(_Connector):
    metadata_url = _API

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return CAPABILITIES.model_copy(
            update={"change_signal": ChangeSignal.API_METADATA, "download_url": None}
        )

    def read_metadata(self, body: bytes) -> Metadata:
        answer = json.loads(body)
        return Metadata(
            url=answer["url"],
            hash=answer["hash"],
            last_modified=datetime.fromisoformat(answer["last_modified"]).replace(tzinfo=UTC),
        )


def _answer(md5: str = _MD5, url: str = "https://example.test/download/list.csv") -> bytes:
    return json.dumps(
        {"url": url, "hash": md5.upper(), "last_modified": "2026-10-12T17:41:37"}
    ).encode()


def _api_page(body: bytes) -> FetchedDocument:
    return _page(body, url=_API)


async def test_api_metadata_downloads_the_described_file_and_keeps_its_hash() -> None:
    fetcher = ScriptedFetcher(_api_page(_answer()), _document(_BODY))

    result = await _ApiConnector(fetcher).fetch(None)

    assert fetcher.requests[1] == (
        "https://example.test/download/list.csv",
        ChangeSignal.CONTENT_HASH,
    )
    snapshot = result.snapshot
    assert snapshot.outcome is Outcome.COMPLETED
    assert snapshot.source_url == "https://example.test/download/list.csv"
    assert snapshot.etag == _MD5
    assert snapshot.last_modified == datetime(2026, 10, 12, 17, 41, 37, tzinfo=UTC)
    assert result.records is not None


async def test_an_unchanged_api_hash_is_one_request() -> None:
    previous = _completed("https://example.test/download/list.csv", etag=_MD5)
    fetcher = ScriptedFetcher(_api_page(_answer()))

    result = await _ApiConnector(fetcher).fetch(previous)

    assert len(fetcher.requests) == 1
    assert result.records is None
    assert result.snapshot.note == "unchanged (metadata unchanged)"
    assert result.snapshot.etag == _MD5
    assert result.snapshot.source_url == previous.source_url


async def test_a_new_api_hash_downloads_again() -> None:
    previous = _completed("https://example.test/download/list.csv", etag="0" * 32)
    fetcher = ScriptedFetcher(_api_page(_answer()), _document(_BODY))

    result = await _ApiConnector(fetcher).fetch(previous)

    assert len(fetcher.requests) == 2
    assert result.records is not None
    assert result.snapshot.etag == _MD5


async def test_a_body_that_does_not_match_the_api_hash_is_failed() -> None:
    fetcher = ScriptedFetcher(_api_page(_answer("f" * 32)), _document(_BODY))

    result = await _ApiConnector(fetcher).fetch(None)

    assert result.snapshot.outcome is Outcome.FAILED
    assert result.snapshot.note is not None
    assert result.snapshot.note.startswith("hash mismatch")
    assert result.snapshot.etag is None
    assert result.records is None


async def test_an_unreadable_api_answer_is_failed() -> None:
    result = await _ApiConnector(ScriptedFetcher(_api_page(b"<html>down</html>"))).fetch(None)

    assert result.snapshot.outcome is Outcome.FAILED
    assert result.snapshot.note == "metadata unreadable: JSONDecodeError"
    assert result.snapshot.source_url == _API


async def test_a_parse_failure_after_api_metadata_drops_the_kept_hash() -> None:
    fetcher = ScriptedFetcher(_api_page(_answer()), _document(_BODY))

    result = await _ApiConnector(fetcher, fail=ParseError("unexpected header")).fetch(None)

    assert result.snapshot.outcome is Outcome.FAILED
    assert result.snapshot.etag is None
