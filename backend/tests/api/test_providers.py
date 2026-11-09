"""API behaviour over in-memory use cases: fast, no database."""

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import httpx2
import pytest

from credentialing.api.app import create_app
from credentialing.api.container import AppContainer
from credentialing.application.check_exclusions import CheckExclusions
from credentialing.application.list_providers import ListProviders
from credentialing.application.lookup_exclusions import LookupExclusions
from credentialing.application.npi_lookup import NpiLookup
from credentialing.application.npi_suggestions import SuggestNpis
from credentialing.application.provider_verdict import ProviderVerdictQuery
from credentialing.application.record_profile import RecordProfileQuery
from credentialing.application.register_provider import RegisterProvider
from credentialing.application.set_provider_npi import SetProviderNpi
from credentialing.application.source_status import SourceStatusReport
from credentialing.application.verification_history import VerificationHistory
from credentialing.application.verify_npi import VerifyNpi
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import DatasetLoadMode, Outcome
from credentialing.domain.models import Address, DatasetLoad, NpiAnswer, NpiRecord, SourceSnapshot
from credentialing.domain.policies import DEFAULT_FRESHNESS
from tests.fakes import (
    API_SOURCE,
    FixedClock,
    InMemoryNpiLinks,
    InMemoryNpiRegistry,
    InMemoryProviders,
    InMemoryRecords,
    InMemorySnapshots,
    InMemoryVerifications,
    ScriptedConnector,
    ScriptedDatasetConnector,
    ScriptedNpiRegistryConnector,
    api_failure,
    api_no_result,
    capabilities,
    dataset_capabilities,
)
from tests.synthetic import NOW, make_npi, npi_person

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
            last_modified=NOW - timedelta(days=9),
        )
    )
    npi_registry = InMemoryNpiRegistry()
    npi_lookup = NpiLookup(npi_registry, registry, snapshots, DEFAULT_FRESHNESS, clock)
    verify_npi = VerifyNpi(
        providers,
        npi_lookup,
        verifications,
        clock,
        source_id=API_SOURCE,
        source_url="https://example.test/registry",
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
        lookup_exclusions=LookupExclusions(records, snapshots, registry, clock),
        record_profile=RecordProfileQuery(records, snapshots, registry, DEFAULT_FRESHNESS, clock),
        verification_history=VerificationHistory(providers, verifications, records),
        provider_verdict=ProviderVerdictQuery(providers, verifications),
        npi_lookup=npi_lookup,
        verify_npi=verify_npi,
        suggest_npis=SuggestNpis(
            providers, InMemoryNpiLinks(records, npi_registry), npi_lookup, clock
        ),
        set_provider_npi=SetProviderNpi(providers, npi_lookup, verify_npi, clock),
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
    fetched, stated = NOW - timedelta(hours=2), NOW - timedelta(days=9)
    assert loaded["latest_completed"]["fetched_at"] == fetched.isoformat().replace("+00:00", "Z")
    assert loaded["data_as_of"] == stated.isoformat().replace("+00:00", "Z")
    assert never["health"] == "never_fetched"
    assert (never["latest"], never["freshness"], never["next_due"]) == (None, None, None)


async def test_sources_need_the_organization_header(client: httpx2.AsyncClient) -> None:
    response = await client.get("/sources")

    assert response.status_code == 401


# --- NPI suggestions and confirming an NPI ---------------------------------------------------

SUGGESTED = make_npi("190000011")
STREET = "1 Sample Way"
WRONG_CHECK_DIGIT = SUGGESTED[:9] + str((int(SUGGESTED[9]) + 1) % 10)


def _npi_container(*mirror: NpiRecord, answers: tuple[NpiAnswer, ...] = ()) -> AppContainer:
    """The base container over a loaded mirror holding ``mirror`` and, with ``answers``, a
    live registry answering them."""
    base = _container()
    clock = FixedClock(NOW)
    directory = ConnectorRegistry()
    directory.register(ScriptedDatasetConnector(dataset_capabilities("nppes_monthly")))
    if answers:
        directory.register(ScriptedNpiRegistryConnector(*answers))
    snapshots = InMemorySnapshots()
    snapshots.stored.append(
        SourceSnapshot(
            id=uuid4(),
            source_id="nppes_monthly",
            fetched_at=NOW - timedelta(days=2),
            outcome=Outcome.COMPLETED,
            source_url="https://example.test/nppes/monthly.zip",
            load=DatasetLoad(mode=DatasetLoadMode.FULL, scanned=1, added=1),
        )
    )
    registry = InMemoryNpiRegistry(*mirror)
    lookup = NpiLookup(registry, directory, snapshots, DEFAULT_FRESHNESS, clock)
    verifications = InMemoryVerifications()
    verify = VerifyNpi(
        base.providers,
        lookup,
        verifications,
        clock,
        source_id=API_SOURCE,
        source_url="https://example.test/registry",
    )
    return replace(
        base,
        npi_lookup=lookup,
        verify_npi=verify,
        verification_history=VerificationHistory(base.providers, verifications, InMemoryRecords()),
        suggest_npis=SuggestNpis(
            base.providers, InMemoryNpiLinks(InMemoryRecords(), registry), lookup, clock
        ),
        set_provider_npi=SetProviderNpi(base.providers, lookup, verify, clock),
    )


def _registered() -> NpiRecord:
    place = Address(line1=STREET, city="Sampleville", state="NY", postal_code="99901")
    return npi_person(SUGGESTED, practice=place, mailing=place)


async def test_npi_suggestions_come_with_the_mirror_and_never_a_street() -> None:
    async for client in _client(_npi_container(_registered())):
        provider_id = await _add(client, "Testperson")
        response = await client.get(f"/providers/{provider_id}/npi-suggestions", headers=HEADERS)
        after = await client.get(f"/providers/{provider_id}", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert (body["provider_id"], body["found"], body["note"]) == (provider_id, 1, None)
    assert body["mirror"]["provenance"]["source_id"] == "nppes_monthly"
    assert body["mirror"]["freshness"] == "cached"
    (suggestion,) = body["suggestions"]
    assert (suggestion["npi"], suggestion["level"]) == (SUGGESTED, "potential")
    assert suggestion["registry"]["npi"] == SUGGESTED
    assert suggestion["registry"]["practice_city"] == "Sampleville"
    assert STREET not in response.text
    assert after.json()["npi"] is None


async def test_npi_suggestions_for_an_unknown_provider_are_404() -> None:
    async for client in _client(_npi_container()):
        response = await client.get(f"/providers/{uuid4()}/npi-suggestions", headers=HEADERS)

    assert response.status_code == 404


async def test_confirming_an_npi_sets_it_once_and_appends_the_verification() -> None:
    async for client in _client(_npi_container(_registered())):
        provider_id = await _add(client, "Testperson")
        response = await client.put(
            f"/providers/{provider_id}/npi", json={"npi": SUGGESTED}, headers=HEADERS
        )
        again = await client.put(
            f"/providers/{provider_id}/npi", json={"npi": SUGGESTED}, headers=HEADERS
        )
        history = await client.get(f"/providers/{provider_id}/verifications", headers=HEADERS)
        suggestions = await client.get(f"/providers/{provider_id}/npi-suggestions", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["provider"]["npi"] == SUGGESTED
    verification = body["verification"]
    assert verification["credential_type"] == "npi"
    assert verification["details"]["npi_set_by"] == "user"
    assert verification["details"]["npi_status"] == "active"
    assert verification["match"]["level"] == "exact"
    assert [entry["id"] for entry in history.json()] == [verification["id"]]
    assert again.status_code == 409
    assert again.json()["detail"] == "the provider already has an NPI; changing it is not offered"
    assert suggestions.json()["suggestions"] == []
    assert suggestions.json()["note"].startswith("the provider already has an NPI")


@pytest.mark.parametrize("npi", ["123", "12345678901", WRONG_CHECK_DIGIT])
async def test_confirming_a_malformed_npi_is_422(npi: str) -> None:
    async for client in _client(_npi_container(_registered())):
        provider_id = await _add(client, "Testperson")
        response = await client.put(
            f"/providers/{provider_id}/npi", json={"npi": npi}, headers=HEADERS
        )

    assert response.status_code == 422


async def test_confirming_an_npi_nppes_does_not_know_is_422_and_sets_nothing() -> None:
    container = _npi_container(answers=(api_no_result(fetched_at=NOW),))
    async for client in _client(container):
        provider_id = await _add(client, "Testperson")
        response = await client.put(
            f"/providers/{provider_id}/npi", json={"npi": SUGGESTED}, headers=HEADERS
        )
        after = await client.get(f"/providers/{provider_id}", headers=HEADERS)

    assert response.status_code == 422
    assert response.json()["detail"] == f"NPI {SUGGESTED} is not in NPPES"
    assert after.json()["npi"] is None


async def test_confirming_an_npi_nppes_could_not_check_is_503_and_sets_nothing() -> None:
    container = _npi_container(answers=(api_failure(Outcome.SOURCE_UNAVAILABLE, fetched_at=NOW),))
    async for client in _client(container):
        provider_id = await _add(client, "Testperson")
        response = await client.put(
            f"/providers/{provider_id}/npi", json={"npi": SUGGESTED}, headers=HEADERS
        )
        after = await client.get(f"/providers/{provider_id}", headers=HEADERS)
        history = await client.get(f"/providers/{provider_id}/verifications", headers=HEADERS)

    assert response.status_code == 503
    assert response.json()["detail"] == "NPPES could not be checked: HTTP 503"
    assert after.json()["npi"] is None
    assert history.json() == []


async def test_confirming_an_npi_for_another_organizations_provider_is_404() -> None:
    async for client in _client(_npi_container(_registered())):
        provider_id = await _add(client, "Testperson")
        response = await client.put(
            f"/providers/{provider_id}/npi",
            json={"npi": SUGGESTED},
            headers={"X-Organization-Id": str(uuid4())},
        )
        after = await client.get(f"/providers/{provider_id}", headers=HEADERS)

    assert response.status_code == 404
    assert after.json()["npi"] is None
