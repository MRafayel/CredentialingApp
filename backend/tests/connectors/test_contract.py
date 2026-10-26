"""The contract every registered connector passes. A new connector joins by being registered
in ``bootstrap.build_registry`` and adding its fixture to ``CONNECTOR_FIXTURES``."""

from datetime import UTC, datetime
from typing import Final

import pytest

from credentialing.bootstrap import build_registry
from credentialing.connectors.framework.base import BulkListConnectorBase
from credentialing.connectors.framework.http import FetchedDocument, FetchFailure
from credentialing.domain.enums import Outcome
from credentialing.domain.models import ConnectorCapabilities
from credentialing.domain.ports import BulkListConnector
from tests.connectors.fixtures import fixture_bytes
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW

pytestmark = pytest.mark.anyio

CONNECTOR_FIXTURES: Final = {
    "oig_leie": "oig_leie.csv",
    "ny_omig": "ny_omig.txt",
}

_REGISTERED = build_registry(ScriptedFetcher()).bulk_connectors()
_SOURCE_IDS = [connector.capabilities.source_id for connector in _REGISTERED]


def _connector(source_id: str, fetcher: ScriptedFetcher) -> BulkListConnector:
    connector = build_registry(fetcher).get(source_id)
    assert isinstance(connector, BulkListConnectorBase)
    return connector


def _document(body: bytes) -> FetchedDocument:
    return FetchedDocument(
        body=body,
        content_hash="ef" * 32,
        etag=None,
        last_modified=datetime(2026, 10, 9, tzinfo=UTC),
        content_type="text/plain",
        final_url="https://example.test/list",
        byte_size=len(body),
        fetched_at=NOW,
        status=200,
    )


def test_every_registered_connector_has_a_fixture() -> None:
    assert sorted(_SOURCE_IDS) == sorted(CONNECTOR_FIXTURES)
    assert len(set(_SOURCE_IDS)) == len(_SOURCE_IDS)


@pytest.mark.parametrize("source_id", _SOURCE_IDS)
def test_capabilities_validate(source_id: str) -> None:
    capabilities = _connector(source_id, ScriptedFetcher()).capabilities

    assert ConnectorCapabilities.model_validate(capabilities.model_dump()) == capabilities
    assert capabilities.source_url.startswith("https://")
    assert capabilities.display_name


@pytest.mark.parametrize("source_id", _SOURCE_IDS)
def test_capabilities_report_the_url_the_connector_downloads(source_id: str) -> None:
    connector = _connector(source_id, ScriptedFetcher())
    assert isinstance(connector, BulkListConnectorBase)

    assert connector.capabilities.download_url == connector.download_url
    assert connector.download_url.startswith("https://")


@pytest.mark.parametrize("source_id", _SOURCE_IDS)
async def test_the_fixture_parses_into_records_of_this_snapshot(source_id: str) -> None:
    body = fixture_bytes(CONNECTOR_FIXTURES[source_id])

    result = await _connector(source_id, ScriptedFetcher(_document(body))).fetch(None)

    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.records
    assert result.snapshot.row_count == len(result.records)
    for record in result.records:
        assert record.source_id == source_id
        assert record.snapshot_id == result.snapshot.id
        assert record.raw


@pytest.mark.parametrize("source_id", _SOURCE_IDS)
@pytest.mark.parametrize("outcome", [Outcome.BLOCKED, Outcome.SOURCE_UNAVAILABLE, Outcome.FAILED])
async def test_a_failed_fetch_never_yields_records(source_id: str, outcome: Outcome) -> None:
    failure = FetchFailure(outcome=outcome, status=None, note="synthetic", fetched_at=NOW)

    result = await _connector(source_id, ScriptedFetcher(failure)).fetch(None)

    assert result.snapshot.outcome is outcome
    assert result.records is None


@pytest.mark.parametrize("source_id", _SOURCE_IDS)
async def test_an_unexpected_document_is_failed_not_empty(source_id: str) -> None:
    page = b"<html><body>Service notice</body></html>"

    result = await _connector(source_id, ScriptedFetcher(_document(page))).fetch(None)

    assert result.snapshot.outcome is Outcome.FAILED
    assert result.records is None
