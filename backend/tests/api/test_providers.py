"""API behaviour over in-memory use cases: fast, no database."""

from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID, uuid4

import httpx2
import pytest

from credentialing.api.app import create_app
from credentialing.api.container import AppContainer
from credentialing.application.check_exclusions import CheckExclusions
from credentialing.application.list_providers import ListProviders
from credentialing.application.lookup_exclusions import LookupExclusions
from credentialing.application.provider_verdict import ProviderVerdictQuery
from credentialing.application.record_profile import RecordProfileQuery
from credentialing.application.register_provider import RegisterProvider
from credentialing.application.source_status import SourceStatusReport
from credentialing.application.verification_history import VerificationHistory
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import Outcome
from credentialing.domain.models import SourceSnapshot
from credentialing.domain.policies import DEFAULT_FRESHNESS
from tests.fakes import (
    FixedClock,
    InMemoryProviders,
    InMemoryRecords,
    InMemorySnapshots,
    InMemoryVerifications,
    ScriptedConnector,
    capabilities,
)
from tests.synthetic import NOW, make_npi

pytestmark = pytest.mark.anyio

ORGANIZATION = UUID("00000000-0000-0000-0000-000000000001")
HEADERS = {"X-Organization-Id": str(ORGANIZATION)}


def _container(*, header_enabled: bool = True) -> AppContainer:
    clock = FixedClock(NOW)
    providers, records, snapshots, verifications = (
        InMemoryProviders(),
        InMemoryRecords(),
        InMemorySnapshots(),
        InMemoryVerifications(),
    )
    registry = ConnectorRegistry()
    registry.register(ScriptedConnector(capabilities("fake_federal")))
    registry.register(ScriptedConnector(capabilities("fake_state", frozenset({"NY"}))))
    snapshots.stored.append(
        SourceSnapshot(
            id=uuid4(),
            source_id="fake_federal",
            fetched_at=NOW - timedelta(hours=2),
            outcome=Outcome.COMPLETED,
            source_url="https://example.test/fake_federal/list.csv",
        )
    )
    return AppContainer(
        register_provider=RegisterProvider(providers, clock),
        list_providers=ListProviders(providers),
        source_status=SourceStatusReport(registry, snapshots, records, DEFAULT_FRESHNESS, clock),
        check_exclusions=CheckExclusions(
            ConnectorRegistry(),
            providers,
            records,
            snapshots,
            verifications,
            DEFAULT_FRESHNESS,
            clock,
        ),
        lookup_exclusions=LookupExclusions(records, snapshots, registry),
        record_profile=RecordProfileQuery(records, snapshots, registry, DEFAULT_FRESHNESS, clock),
        verification_history=VerificationHistory(providers, verifications, records),
        provider_verdict=ProviderVerdictQuery(providers, verifications),
        providers=providers,
        organization_header_enabled=header_enabled,
    )


async def _client(container: AppContainer) -> AsyncIterator[httpx2.AsyncClient]:
    app = create_app(expose_docs=False)
    app.state.container = container
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def client() -> AsyncIterator[httpx2.AsyncClient]:
    async for client in _client(_container()):
        yield client


async def test_register_then_get(client: httpx2.AsyncClient) -> None:
    body = {
        "entity_kind": "individual",
        "person_name": {"last": "Testperson", "first": "Avery"},
        "npi": make_npi("100000001"),
        "date_of_birth": "1970-01-01",
        "licenses": [{"number": "123456", "state": "NY"}],
    }

    created = await client.post("/providers", json=body, headers=HEADERS)

    assert created.status_code == 201
    payload = created.json()
    assert payload["person_name"]["last"] == "Testperson"
    assert payload["has_date_of_birth"] is True
    assert "date_of_birth" not in payload
    fetched = await client.get(f"/providers/{payload['id']}", headers=HEADERS)
    assert fetched.status_code == 200
    assert fetched.json() == payload


async def test_a_new_provider_is_not_checked(client: httpx2.AsyncClient) -> None:
    body = {"entity_kind": "individual", "person_name": {"last": "Testperson", "first": "Avery"}}
    created = (await client.post("/providers", json=body, headers=HEADERS)).json()

    verdict = await client.get(f"/providers/{created['id']}/verdict", headers=HEADERS)
    checked = await client.post(
        f"/providers/{created['id']}/verifications/exclusions", headers=HEADERS
    )

    assert verdict.status_code == 200
    expected = {
        "verdict": "not_checked",
        "sources_checked": 0,
        "listed_in": [],
        "flagged_in": [],
        "review_in": [],
        "unchecked": [],
        "oldest_list_updated_at": None,
        "last_checked_at": None,
        "results": [],
    }
    assert verdict.json() == expected
    # This container registers no list for checks, so a run has nothing to answer.
    assert checked.json() == expected


async def test_another_organization_cannot_read_the_provider(client: httpx2.AsyncClient) -> None:
    body = {"entity_kind": "organization", "organization_name": "Sampleville Care Group"}
    created = (await client.post("/providers", json=body, headers=HEADERS)).json()

    other = {"X-Organization-Id": str(uuid4())}
    response = await client.get(f"/providers/{created['id']}", headers=other)

    assert response.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"entity_kind": "individual"},
        {"entity_kind": "organization", "organization_name": "X", "npi": "1234567890"},
        {"entity_kind": "individual", "person_name": {"last": "", "first": "A"}},
        {"entity_kind": "robot", "organization_name": "X"},
        {"entity_kind": "organization", "organization_name": "X", "ssn": "000-00-0000"},
    ],
)
async def test_invalid_bodies_are_422(client: httpx2.AsyncClient, body: dict[str, object]) -> None:
    response = await client.post("/providers", json=body, headers=HEADERS)

    assert response.status_code == 422


async def test_unknown_provider_is_404(client: httpx2.AsyncClient) -> None:
    for response in (
        await client.get(f"/providers/{uuid4()}", headers=HEADERS),
        await client.post(f"/providers/{uuid4()}/verifications/exclusions", headers=HEADERS),
        await client.get(f"/providers/{uuid4()}/verifications", headers=HEADERS),
        await client.get(f"/providers/{uuid4()}/verdict", headers=HEADERS),
    ):
        assert response.status_code == 404


async def test_the_organization_header_is_required(client: httpx2.AsyncClient) -> None:
    response = await client.get(f"/providers/{uuid4()}")

    assert response.status_code == 401


async def test_outside_local_development_the_header_is_refused() -> None:
    async for client in _client(_container(header_enabled=False)):
        response = await client.get(f"/providers/{uuid4()}", headers=HEADERS)

        assert response.status_code == 401
        assert "not configured" in response.json()["detail"]


async def _add(client: httpx2.AsyncClient, last: str, *, npi: str | None = None) -> str:
    body: dict[str, object] = {
        "entity_kind": "individual",
        "person_name": {"last": last, "first": "Avery"},
    }
    if npi:
        body["npi"] = npi
    response = await client.post("/providers", json=body, headers=HEADERS)
    assert response.status_code == 201
    return str(response.json()["id"])


async def test_list_pages_through_the_roster(client: httpx2.AsyncClient) -> None:
    for index in range(3):
        await _add(client, f"Pageperson{index}")

    first = (await client.get("/providers?page=1&page_size=2", headers=HEADERS)).json()
    second = (await client.get("/providers?page=2&page_size=2", headers=HEADERS)).json()

    assert (first["total"], first["page"], first["page_size"]) == (3, 1, 2)
    assert len(first["items"]) == 2
    assert len(second["items"]) == 1
    names = {item["person_name"]["last"] for item in first["items"] + second["items"]}
    assert names == {"Pageperson0", "Pageperson1", "Pageperson2"}


async def test_list_searches_names_and_exact_npi(client: httpx2.AsyncClient) -> None:
    npi = make_npi("100000001")
    await _add(client, "Searchperson", npi=npi)
    await _add(client, "Otherperson")

    by_name = (await client.get("/providers", params={"q": "  searCH "}, headers=HEADERS)).json()
    by_npi = (await client.get("/providers", params={"q": npi}, headers=HEADERS)).json()
    partial_npi = (await client.get("/providers", params={"q": npi[:5]}, headers=HEADERS)).json()

    assert [item["person_name"]["last"] for item in by_name["items"]] == ["Searchperson"]
    assert [item["npi"] for item in by_npi["items"]] == [npi]
    assert partial_npi["total"] == 0


async def test_list_is_scoped_by_organization(client: httpx2.AsyncClient) -> None:
    await _add(client, "Scopedperson")

    other = await client.get("/providers", headers={"X-Organization-Id": str(uuid4())})

    assert other.json() == {"items": [], "total": 0, "page": 1, "page_size": 10}


@pytest.mark.parametrize("query", ["page=0", "page_size=0", "page_size=51"])
async def test_list_rejects_out_of_range_paging(client: httpx2.AsyncClient, query: str) -> None:
    response = await client.get(f"/providers?{query}", headers=HEADERS)

    assert response.status_code == 422


async def test_sources_report_every_registered_source(client: httpx2.AsyncClient) -> None:
    response = await client.get("/sources", headers=HEADERS)

    assert response.status_code == 200
    by_id = {item["source_id"]: item for item in response.json()}
    assert set(by_id) == {"fake_federal", "fake_state"}
    loaded, never = by_id["fake_federal"], by_id["fake_state"]
    assert loaded["health"] == "ok"
    assert loaded["jurisdictions"] == ["US"]
    assert loaded["poll_interval"] == "P7D"
    assert loaded["latest_completed"]["outcome"] == "completed"
    assert loaded["data_as_of"] == (NOW - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    assert never["health"] == "never_fetched"
    assert (never["latest"], never["freshness"], never["next_due"]) == (None, None, None)


async def test_sources_need_the_organization_header(client: httpx2.AsyncClient) -> None:
    response = await client.get("/sources")

    assert response.status_code == 401
