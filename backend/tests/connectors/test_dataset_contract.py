"""The contract every registered dataset connector passes. A new one joins by being registered
in ``bootstrap.build_registry`` and adding its file and landing page to the fixtures here."""

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest

from credentialing.bootstrap import build_registry
from credentialing.connectors.framework.dataset import DatasetConnectorBase
from credentialing.connectors.framework.http import FetchedDocument, FetchFailure
from credentialing.domain.enums import ChangeSignal, Outcome
from credentialing.domain.models import ConnectorCapabilities
from tests.connectors.fixtures import nppes_builders
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW

pytestmark = pytest.mark.anyio

DATASET_FIXTURES: Final[dict[str, Callable[[], bytes]]] = {
    "nppes_monthly": nppes_builders.nppes_zip,
    "nppes_weekly": lambda: nppes_builders.nppes_zip(member=nppes_builders.WEEKLY_MEMBER),
    "nppes_deactivations": nppes_builders.deactivation_zip,
}
"""Each dataset source's synthetic file."""

LANDING_FIXTURES: Final[dict[str, Callable[[], bytes]]] = {
    "nppes_monthly": nppes_builders.files_page,
    "nppes_weekly": nppes_builders.files_page,
    "nppes_deactivations": nppes_builders.files_page,
}
"""For a source whose file URL changes, the landing page that links it."""

_REGISTERED = build_registry(ScriptedFetcher()).dataset_connectors()
_SOURCE_IDS = [connector.capabilities.source_id for connector in _REGISTERED]


def _connector(source_id: str, fetcher: ScriptedFetcher, data_dir: Path) -> DatasetConnectorBase:
    connector = build_registry(fetcher, dataset_dir=data_dir).get(source_id)
    assert isinstance(connector, DatasetConnectorBase)
    return connector


def _files(folder: Path) -> list[Path]:
    return [path for path in folder.rglob("*") if path.is_file()]


def _page(body: bytes) -> FetchedDocument:
    return FetchedDocument(
        body=body,
        content_hash=hashlib.sha256(body).hexdigest(),
        etag=None,
        last_modified=None,
        content_type="text/html",
        final_url="https://example.test/files.html",
        byte_size=len(body),
        fetched_at=NOW,
        status=200,
    )


def test_every_registered_dataset_connector_has_a_fixture() -> None:
    assert sorted(_SOURCE_IDS) == sorted(DATASET_FIXTURES)


@pytest.mark.parametrize("source_id", _SOURCE_IDS)
def test_capabilities_validate_and_name_no_fixed_file(source_id: str, tmp_path: Path) -> None:
    connector = _connector(source_id, ScriptedFetcher(), tmp_path)
    capabilities = connector.capabilities

    assert ConnectorCapabilities.model_validate(capabilities.model_dump()) == capabilities
    assert capabilities.source_url.startswith("https://")
    assert capabilities.display_name
    if capabilities.change_signal is ChangeSignal.LINK_DISCOVERY:
        assert capabilities.download_url is None
        assert connector.landing_url is not None
        assert connector.landing_url.startswith("https://")
        assert source_id in LANDING_FIXTURES


@pytest.mark.parametrize("source_id", _SOURCE_IDS)
async def test_the_fixture_opens_and_both_passes_agree(source_id: str, tmp_path: Path) -> None:
    landing = LANDING_FIXTURES.get(source_id)
    located = (_page(landing()),) if landing else ()
    fetcher = ScriptedFetcher(*located, files=[DATASET_FIXTURES[source_id]()])

    fetched = await _connector(source_id, fetcher, tmp_path).fetch_dataset(None)

    assert fetched.snapshot.outcome is Outcome.COMPLETED
    assert fetched.dataset is not None
    scanned = list(fetched.dataset.scan())
    records = list(fetched.dataset.records(None))
    assert scanned
    assert [(r.npi, r.line_hash) for r in records] == scanned
    assert all(record.snapshot_id == fetched.snapshot.id for record in records)
    fetched.dataset.close()
    assert _files(tmp_path) == []


@pytest.mark.parametrize("source_id", _SOURCE_IDS)
@pytest.mark.parametrize("outcome", [Outcome.BLOCKED, Outcome.SOURCE_UNAVAILABLE, Outcome.FAILED])
async def test_a_failed_fetch_never_yields_a_dataset(
    source_id: str, outcome: Outcome, tmp_path: Path
) -> None:
    failure = FetchFailure(outcome=outcome, status=None, note="synthetic", fetched_at=NOW)

    fetched = await _connector(source_id, ScriptedFetcher(failure), tmp_path).fetch_dataset(None)

    assert fetched.snapshot.outcome is outcome
    assert fetched.dataset is None


@pytest.mark.parametrize("source_id", _SOURCE_IDS)
async def test_an_unexpected_file_is_failed_and_deleted(source_id: str, tmp_path: Path) -> None:
    landing = LANDING_FIXTURES.get(source_id)
    located = (_page(landing()),) if landing else ()
    fetcher = ScriptedFetcher(*located, files=[b"<html><body>Service notice</body></html>"])

    fetched = await _connector(source_id, fetcher, tmp_path).fetch_dataset(None)

    assert fetched.snapshot.outcome is Outcome.FAILED
    assert fetched.snapshot.note is not None
    assert fetched.snapshot.note.startswith("unreadable file: ")
    assert fetched.dataset is None
    assert _files(tmp_path) == []
