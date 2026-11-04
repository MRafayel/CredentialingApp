"""The contract every registered connector passes. A new connector joins by being registered
in ``bootstrap.build_registry`` and adding its fixture to ``CONNECTOR_FIXTURES``."""

from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from typing import Final

import pytest

from credentialing.bootstrap import build_registry
from credentialing.connectors.framework.base import BulkListConnectorBase
from credentialing.connectors.framework.http import FetchedDocument, FetchFailure, Response
from credentialing.domain.enums import ChangeSignal, Outcome
from credentialing.domain.models import ConnectorCapabilities
from credentialing.domain.ports import BulkListConnector
from tests.connectors.fixtures import (
    builders,
    fixture_bytes,
    il_builders,
    ma_masshealth_builders,
    mi_builders,
    mn_builders,
    nv_builders,
    oh_builders,
)
from tests.connectors.fixtures.co_builders import co_hcpf
from tests.connectors.fixtures.de_builders import de_dmap
from tests.connectors.fixtures.ks_builders import ks_kmap
from tests.connectors.fixtures.nh_builders import nh_dhhs
from tests.connectors.fixtures.wv_builders import wv_bms
from tests.connectors.fixtures.wy_builders import wy_wdh
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW

pytestmark = pytest.mark.anyio

CONNECTOR_FIXTURES: Final[dict[str, Callable[[], bytes]]] = {
    "oig_leie": partial(fixture_bytes, "oig_leie.csv"),
    "ny_omig": partial(fixture_bytes, "ny_omig.txt"),
    "nc_medicaid": builders.nc_medicaid,
    "md_medicaid": builders.md_medicaid,
    "vt_medicaid": builders.vt_medicaid,
    "nd_medicaid": builders.nd_medicaid,
    "wa_hca": builders.wa_hca,
    "ia_medicaid": builders.ia_medicaid,
    "in_fssa": builders.in_fssa,
    "pa_medicheck": partial(fixture_bytes, "pa_medicheck.csv"),
    "nj_treasury": partial(fixture_bytes, "nj_treasury.txt"),
    "mt_dphhs": partial(fixture_bytes, "mt_dphhs.html"),
    "mo_mmac": builders.mo_mmac,
    "ga_dch_oig": builders.ga_dch_oig,
    "ca_dhcs": partial(fixture_bytes, "ca_dhcs.csv"),
    "mn_dhs_individuals": mn_builders.mn_dhs_individuals,
    "mn_dhs_organizations": mn_builders.mn_dhs_organizations,
    "de_dmap": de_dmap,
    "nv_omig": nv_builders.nv_omig,
    "wv_bms": wv_bms,
    "wy_wdh": wy_wdh,
    "co_hcpf": co_hcpf,
    "ks_kmap": ks_kmap,
    "ma_masshealth": ma_masshealth_builders.ma_masshealth,
    "mi_mdhhs": mi_builders.mi_mdhhs,
    "nh_dhhs": nh_dhhs,
    "oh_medicaid": oh_builders.oh_medicaid,
    "il_hfs": il_builders.il_hfs,
}
"""Each source's synthetic document: a recorded file, or a workbook built at test time."""

LOCATOR_FIXTURES: Final[dict[str, Callable[[bytes], bytes]]] = {
    "mo_mmac": lambda _body: fixture_bytes("mo_mmac_landing.html"),
    "ga_dch_oig": lambda _body: fixture_bytes("ga_dch_oig_landing.html"),
    "ca_dhcs": builders.ca_dhcs_resource_show,
    "nv_omig": lambda _body: fixture_bytes("nv_omig_landing.html"),
    "wv_bms": lambda _body: fixture_bytes("wv_bms_landing.html"),
    "wy_wdh": lambda _body: fixture_bytes("wy_wdh_landing.html"),
    "co_hcpf": lambda _body: fixture_bytes("co_hcpf_landing.html"),
    "ma_masshealth": lambda _body: fixture_bytes("ma_masshealth_landing.html"),
    "mi_mdhhs": lambda _body: fixture_bytes("mi_mdhhs_landing.html"),
    "oh_medicaid": lambda _body: fixture_bytes("oh_medicaid_landing.html"),
    "il_hfs": lambda _body: il_builders.il_hfs_page(),
}
"""For a source whose file URL changes, the landing page or API answer that locates the
document (given the document, so an API answer can carry its hash)."""

SESSION_FIXTURES: Final[dict[str, Callable[[bytes], tuple[Response, ...]]]] = {
    "il_hfs": il_builders.export_answers,
}
"""For a source whose file is behind a token flow, the session's answers ending in the
document."""

_RESOLVED: Final = (ChangeSignal.LINK_DISCOVERY, ChangeSignal.API_METADATA)

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

    if connector.capabilities.change_signal in _RESOLVED:
        locator = connector.landing_url or connector.metadata_url
        assert connector.capabilities.download_url is None
        assert locator is not None
        assert locator.startswith("https://")
        assert source_id in LOCATOR_FIXTURES
        return
    assert connector.capabilities.download_url == connector.download_url
    assert connector.download_url.startswith("https://")


@pytest.mark.parametrize("source_id", _SOURCE_IDS)
async def test_the_fixture_parses_into_records_of_this_snapshot(source_id: str) -> None:
    body = CONNECTOR_FIXTURES[source_id]()
    locate = LOCATOR_FIXTURES.get(source_id)
    located = (_document(locate(body)),) if locate else ()
    session = SESSION_FIXTURES.get(source_id)
    answers = session(body) if session else ()
    fetcher = ScriptedFetcher(*located, _document(body), session_answers=answers)

    result = await _connector(source_id, fetcher).fetch(None)

    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.records
    assert result.snapshot.row_count == len(result.records)
    provider_type_fields = _connector(
        source_id, ScriptedFetcher()
    ).capabilities.provider_type_fields
    for record in result.records:
        assert record.source_id == source_id
        assert record.snapshot_id == result.snapshot.id
        assert record.raw
        assert set(provider_type_fields) <= set(record.raw)
        assert record.npi not in record.related_npis
        assert len(set(record.related_npis)) == len(record.related_npis)


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
