"""API behaviour over in-memory use cases: fast, no database."""

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import httpx2
import pytest

from credentialing.api.app import create_app
from credentialing.api.container import AppContainer
from credentialing.api.schemas import AttentionFilter
from credentialing.application.check_exclusions import CheckExclusions
from credentialing.application.import_providers import ImportProviders
from credentialing.application.list_providers import ListProviders
from credentialing.application.lookup_exclusions import LookupExclusions
from credentialing.application.npi_lookup import NpiLookup
from credentialing.application.npi_suggestions import SuggestNpis
from credentialing.application.provider_status import (
    MarkViewed,
    ProviderStatusKeeper,
    ProviderStatusQuery,
    ProviderStatusSummary,
)
from credentialing.application.provider_verdict import ProviderVerdictQuery
from credentialing.application.recheck_providers import RecheckProviders
from credentialing.application.record_profile import RecordProfileQuery
from credentialing.application.register_provider import RegisterProvider
from credentialing.application.set_provider_npi import SetProviderNpi
from credentialing.application.source_status import SourceStatusReport
from credentialing.application.verification_history import VerificationHistory
from credentialing.application.verify_npi import VerifyNpi
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import Attention, AttentionScope, DatasetLoadMode, Outcome
from credentialing.domain.models import Address, DatasetLoad, NpiAnswer, NpiRecord, SourceSnapshot
from credentialing.domain.policies import DEFAULT_FRESHNESS, MONITORING_INTERVAL
from tests.fakes import (
    API_SOURCE,
    FixedClock,
    InMemoryMonitoringRuns,
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
    keeper = ProviderStatusKeeper(providers, verifications, registry)
    verify_npi = VerifyNpi(
        providers,
        npi_lookup,
        verifications,
        clock,
        source_id=API_SOURCE,
        source_url="https://example.test/registry",
        status=keeper,
    )
    check_exclusions = CheckExclusions(
        ConnectorRegistry(),
        providers,
        records,
        snapshots,
        verifications,
        DEFAULT_FRESHNESS,
        clock,
        status=keeper,
    )
    recheck = RecheckProviders(
        providers,
        check_exclusions,
        verify_npi,
        keeper,
        InMemoryMonitoringRuns(),
        clock,
        verifications=verifications,
    )
    return AppContainer(
        register_provider=RegisterProvider(providers, clock),
        import_providers=ImportProviders(providers, recheck, clock),
        list_providers=ListProviders(providers, keeper),
        source_status=SourceStatusReport(registry, snapshots, records, DEFAULT_FRESHNESS, clock),
        check_exclusions=check_exclusions,
        lookup_exclusions=LookupExclusions(records, snapshots, registry, clock),
        record_profile=RecordProfileQuery(records, snapshots, registry, DEFAULT_FRESHNESS, clock),
        verification_history=VerificationHistory(providers, verifications, records),
        provider_verdict=ProviderVerdictQuery(providers, verifications),
        provider_status=ProviderStatusQuery(providers, keeper),
        provider_status_summary=ProviderStatusSummary(providers, keeper),
        mark_viewed=MarkViewed(providers, keeper, clock),
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


async def test_an_npi_already_on_the_roster_is_409_naming_its_holder(
    client: httpx2.AsyncClient,
) -> None:
    npi = make_npi("100000001")
    holder = await _add(client, "Testperson", npi=npi)

    response = await client.post(
        "/providers",
        json={
            "entity_kind": "individual",
            "person_name": {"last": "Sampleperson", "first": "Blake"},
            "npi": npi,
        },
        headers=HEADERS,
    )
    roster = await client.get("/providers", headers=HEADERS)

    assert response.status_code == 409
    assert response.json() == {
        "detail": "a provider with this NPI is already on the roster",
        "provider_id": holder,
    }
    assert npi not in response.text
    assert roster.json()["total"] == 1


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
    assert [entry["id"] for entry in history.json()["items"]] == [verification["id"]]
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
    assert history.json()["items"] == []


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


async def test_confirming_an_npi_another_provider_holds_is_409_and_sets_nothing() -> None:
    async for client in _client(_npi_container(_registered())):
        holder = await _add(client, "Testperson", npi=SUGGESTED)
        provider_id = await _add(client, "Sampleperson")
        response = await client.put(
            f"/providers/{provider_id}/npi", json={"npi": SUGGESTED}, headers=HEADERS
        )
        after = await client.get(f"/providers/{provider_id}", headers=HEADERS)
        history = await client.get(f"/providers/{provider_id}/verifications", headers=HEADERS)

    assert response.status_code == 409
    assert response.json() == {
        "detail": "a provider with this NPI is already on the roster",
        "provider_id": holder,
    }
    assert after.json()["npi"] is None
    assert history.json()["items"] == []


# --- Monitoring -----------------------------------------------------------------------------


async def _register(
    client: httpx2.AsyncClient, last: str, *, role: str | None = None, npi: str | None = None
) -> dict[str, object]:
    body: dict[str, object] = {
        "entity_kind": "individual",
        "person_name": {"last": last, "first": "Avery"},
    }
    if role is not None:
        body["role"] = role
    if npi is not None:
        body["npi"] = npi
    response = await client.post("/providers", json=body, headers=HEADERS)
    assert response.status_code == 201
    payload: dict[str, object] = response.json()
    return payload


async def test_a_provider_is_registered_with_its_roster_role(client: httpx2.AsyncClient) -> None:
    practitioner = await _register(client, "Practiceperson")
    worker = await _register(client, "Workperson", role="workforce")
    refused = await client.post(
        "/providers",
        json={"entity_kind": "individual", "person_name": {"last": "X", "first": "Y"}, "role": "x"},
        headers=HEADERS,
    )

    assert practitioner["role"] == "practitioner"
    assert worker["role"] == "workforce"
    assert refused.status_code == 422


async def test_the_roster_carries_each_status_and_filters_by_attention(
    client: httpx2.AsyncClient,
) -> None:
    missing = await _register(client, "Missingperson")
    worker = await _register(client, "Workperson", role="workforce")

    listed = (await client.get("/providers", headers=HEADERS)).json()
    filtered = (
        await client.get("/providers", params={"attention": "not_checked"}, headers=HEADERS)
    ).json()
    refused = await client.get("/providers", params={"attention": "bogus"}, headers=HEADERS)

    assert [item["id"] for item in listed["items"]] == [missing["id"], worker["id"]]
    status = listed["items"][0]["status"]
    assert status == {
        "verdict": "not_checked",
        "identity": "npi_missing",
        "changed": False,
        "changed_at": None,
        "attention": "npi_missing",
        "last_checked_at": None,
        "next_check_at": None,
        "listed_in": [],
        "flagged_in": [],
        "review_in": [],
        "unchecked": [],
    }
    assert listed["items"][1]["status"]["identity"] == "not_expected"
    assert [item["id"] for item in filtered["items"]] == [worker["id"]]
    assert filtered["total"] == 1
    assert refused.status_code == 422


async def test_the_roster_filters_by_any_attention_or_none(client: httpx2.AsyncClient) -> None:
    missing = await _register(client, "Missingperson")
    worker = await _register(client, "Workperson", role="workforce")
    other = {"X-Organization-Id": str(uuid4())}

    some = (await client.get("/providers", params={"attention": "any"}, headers=HEADERS)).json()
    none = (await client.get("/providers", params={"attention": "none"}, headers=HEADERS)).json()
    theirs = (await client.get("/providers", params={"attention": "any"}, headers=other)).json()

    # Missing NPI ranks before not checked: the attention order holds within "any".
    assert [item["id"] for item in some["items"]] == [missing["id"], worker["id"]]
    assert some["total"] == 2
    assert (none["items"], none["total"]) == ([], 0)
    assert theirs["total"] == 0


def test_the_attention_filter_is_every_reason_plus_any_and_none() -> None:
    values = {item.value for item in AttentionFilter}

    assert values == {*(reason.value for reason in Attention), "any", "none"}
    assert AttentionFilter.ANY.selection() is AttentionScope.ANY
    assert AttentionFilter.NONE.selection() is AttentionScope.NONE
    assert AttentionFilter.LISTED.selection() is Attention.LISTED


async def test_the_summary_counts_the_callers_roster_only(client: httpx2.AsyncClient) -> None:
    await _register(client, "Missingperson")
    await _register(client, "Workperson", role="vendor")

    summary = (await client.get("/providers/summary", headers=HEADERS)).json()
    other = await client.get("/providers/summary", headers={"X-Organization-Id": str(uuid4())})

    assert summary["total"] == 2
    assert summary["counts"]["npi_missing"] == 1
    assert summary["counts"]["not_checked"] == 1
    assert summary["counts"]["none"] == 0
    assert set(summary["counts"]) == {"none", *(reason.value for reason in Attention)}
    assert summary["recheck_interval_days"] == MONITORING_INTERVAL.days == 15
    assert other.json()["total"] == 0


async def test_a_providers_status_and_seen_are_scoped_to_its_organization(
    client: httpx2.AsyncClient,
) -> None:
    created = await _register(client, "Statusperson", npi=make_npi("100000001"))
    other = {"X-Organization-Id": str(uuid4())}

    status = await client.get(f"/providers/{created['id']}/status", headers=HEADERS)
    seen = await client.post(f"/providers/{created['id']}/seen", headers=HEADERS)

    assert status.status_code == 200
    assert status.json()["identity"] == "npi_unverified"
    assert status.json()["attention"] == "not_checked"
    assert seen.status_code == 204
    assert seen.content == b""
    for response in (
        await client.get(f"/providers/{created['id']}/status", headers=other),
        await client.post(f"/providers/{created['id']}/seen", headers=other),
        await client.get(f"/providers/{uuid4()}/status", headers=HEADERS),
        await client.post(f"/providers/{uuid4()}/seen", headers=HEADERS),
    ):
        assert response.status_code == 404


async def test_an_npi_verification_carries_its_change_and_moves_the_status(
    client: httpx2.AsyncClient,
) -> None:
    created = await _register(client, "Statusperson", npi=make_npi("100000001"))

    verified = await client.post(f"/providers/{created['id']}/verifications/npi", headers=HEADERS)
    status = (await client.get(f"/providers/{created['id']}/status", headers=HEADERS)).json()

    assert verified.json()["changed_from"] is None
    assert status["identity"] == "npi_uncertain"
    assert status["attention"] == "npi_uncertain"


IMPORT_TEXT = (
    "Last Name\tFirst Name\tNPI\tDOB\tDepartment\n"
    f"Testperson\tAvery\t{make_npi('100000001')}\t1970-02-03\tCardiology\n"
    "Sampleperson\tBlake\t\tsomeday\tFront desk\n"
    "\tCasey\t\t\t\n"
)


async def test_an_import_preview_shows_every_row_and_stores_nothing(
    client: httpx2.AsyncClient,
) -> None:
    response = await client.post("/providers/import", json={"text": IMPORT_TEXT}, headers=HEADERS)
    roster = (await client.get("/providers", headers=HEADERS)).json()

    assert response.status_code == 200
    payload = response.json()
    assert payload["columns"] == ["last", "first", "npi", "date_of_birth"]
    assert payload["ignored_columns"] == ["Department"]
    assert (payload["importable"], payload["with_warnings"], payload["rejected"]) == (2, 1, 1)
    assert (payload["dry_run"], payload["imported"], payload["recheck"]) == (True, [], None)
    assert payload["no_header"] is False
    first, second, third = payload["rows"]
    assert first == {
        "line": 1,
        "display": "Testperson, Avery",
        "entity_kind": "individual",
        "npi": make_npi("100000001"),
        "role": "practitioner",
        "has_date_of_birth": True,
        "name_split": False,
        "importable": True,
        "problems": [],
        "duplicate_of_line": None,
        "existing_provider_id": None,
        "values": {
            "last": "Testperson",
            "first": "Avery",
            "npi": make_npi("100000001"),
            "date_of_birth": "1970-02-03",
        },
    }
    assert third["values"] == {"last": "", "first": "Casey", "npi": "", "date_of_birth": ""}
    assert second["problems"] == [
        {"code": "date_of_birth_unreadable", "severity": "warning", "detail": None}
    ]
    assert (second["importable"], second["role"]) == (True, "workforce")
    assert third["importable"] is False
    assert third["problems"][0]["code"] == "name_missing"
    assert roster["total"] == 0


async def test_an_import_commit_stores_the_rows_for_the_caller_only(
    client: httpx2.AsyncClient,
) -> None:
    other = {"X-Organization-Id": str(uuid4())}

    response = await client.post(
        "/providers/import", json={"text": IMPORT_TEXT, "dry_run": False}, headers=HEADERS
    )
    again = await client.post("/providers/import", json={"text": IMPORT_TEXT}, headers=HEADERS)
    theirs = await client.post("/providers/import", json={"text": IMPORT_TEXT}, headers=other)

    payload = response.json()
    assert response.status_code == 200
    assert payload["dry_run"] is False
    assert len(payload["imported"]) == 2
    recheck = payload["recheck"]
    assert (recheck["providers"], recheck["failures"]) == (2, 0)
    roster = (await client.get("/providers", headers=HEADERS)).json()
    assert {item["id"] for item in roster["items"]} == set(payload["imported"])
    assert (await client.get("/providers", headers=other)).json()["total"] == 0
    # A second import of the same text finds them on the caller's roster, not on another's.
    assert [row["existing_provider_id"] for row in again.json()["rows"][:2]] == payload["imported"]
    assert [row["existing_provider_id"] for row in theirs.json()["rows"]] == [None, None, None]


async def test_an_unrecognized_text_reports_no_header(client: httpx2.AsyncClient) -> None:
    response = await client.post(
        "/providers/import", json={"text": "Testperson,Avery\n"}, headers=HEADERS
    )

    assert response.status_code == 200
    assert (response.json()["no_header"], response.json()["rows"]) == (True, [])


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({"text": "Last,First\n" + "x" * (2 * 1024 * 1024)}, "larger than 2 MB"),
        ({"text": "Last,First\n" + "Testperson,Avery\n" * 5_001}, "more than 5,000 rows"),
        ({"text": "Last,First\nTestperson," + "y" * 200_000}, "could not be read"),
    ],
)
async def test_an_import_over_the_limits_is_422(
    client: httpx2.AsyncClient, body: dict[str, object], detail: str
) -> None:
    response = await client.post("/providers/import", json=body, headers=HEADERS)

    assert response.status_code == 422
    assert detail in response.json()["detail"]


@pytest.mark.parametrize("body", [{}, {"text": 3}, {"text": "x", "dry_run": "maybe"}, {"rows": []}])
async def test_an_unreadable_import_body_is_422(
    client: httpx2.AsyncClient, body: dict[str, object]
) -> None:
    response = await client.post("/providers/import", json=body, headers=HEADERS)

    assert response.status_code == 422


async def test_an_import_needs_the_organization_header(client: httpx2.AsyncClient) -> None:
    response = await client.post("/providers/import", json={"text": IMPORT_TEXT})

    assert response.status_code == 401
