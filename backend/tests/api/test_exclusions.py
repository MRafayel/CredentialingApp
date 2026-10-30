"""The exclusion lookup over in-memory storage: fast, no database."""

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import date, timedelta
from uuid import uuid4

import httpx2
import pytest

from credentialing.application.lookup_exclusions import LookupExclusions
from credentialing.application.record_profile import RecordProfileQuery
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import EntityKind, ExclusionAction, Outcome
from credentialing.domain.models import Address, License, SourceSnapshot
from credentialing.domain.policies import DEFAULT_FRESHNESS
from tests.api.test_providers import HEADERS, _client, _container
from tests.fakes import (
    FixedClock,
    InMemoryRecords,
    InMemorySnapshots,
    ScriptedConnector,
    capabilities,
)
from tests.synthetic import NOW, exclusion, make_npi

pytestmark = pytest.mark.anyio

SOURCE = "fake_state"
NPI = make_npi("100000001")
OWNER_NPI = make_npi("100000002")


@pytest.fixture
async def client() -> AsyncIterator[httpx2.AsyncClient]:
    registry = ConnectorRegistry()
    registry.register(
        ScriptedConnector(
            capabilities(SOURCE, frozenset({"NY"}), display_name="Fake New York List").model_copy(
                update={"provider_type_fields": ("PROVIDER_TYPE",)}
            )
        )
    )
    snapshots, records = InMemorySnapshots(), InMemoryRecords()
    snapshot = SourceSnapshot(
        id=uuid4(),
        source_id=SOURCE,
        fetched_at=NOW - timedelta(hours=2),
        outcome=Outcome.COMPLETED,
        source_url="https://example.test/fake_state/list.csv",
    )
    await snapshots.add(snapshot)
    listed = exclusion(
        source_id=SOURCE,
        snapshot_id=snapshot.id,
        entity_kind=EntityKind.ORGANIZATION,
        last=None,
        organization_name="7 STAR SAMPLEVILLE PHARMACY AND DELI INC",
        npi=NPI,
        related_npis=(OWNER_NPI,),
        related_parties=("Exampleowner, Jordan",),
        license=License(number="00012345", state="NY"),
        action=ExclusionAction.TERMINATION,
        raw={"PROVIDER_TYPE": "Pharmacy"},
    ).model_copy(update={"address": Address(line1="1 Sample Way", city="Sampleville", state="NY")})
    owner = exclusion(
        source_id=SOURCE,
        snapshot_id=snapshot.id,
        last="Exampleowner",
        first="Jordan",
        npi=OWNER_NPI,
        date_of_birth=date(1970, 5, 17),
        raw={"PROVIDER_TYPE": "Pharmacist"},
    )
    removed = exclusion(source_id=SOURCE, snapshot_id=snapshot.id, last="Sampleremoved")
    await records.replace_current(
        SOURCE, snapshot.id, [listed, owner, removed], NOW - timedelta(days=7), source_changed=True
    )
    await records.replace_current(SOURCE, snapshot.id, [listed, owner], NOW, source_changed=True)
    container = replace(
        _container(),
        lookup_exclusions=LookupExclusions(records, snapshots, registry, FixedClock(NOW)),
        record_profile=RecordProfileQuery(
            records, snapshots, registry, DEFAULT_FRESHNESS, FixedClock(NOW)
        ),
    )
    async for client in _client(container):
        yield client


async def test_search_returns_records_with_their_lists(client: httpx2.AsyncClient) -> None:
    response = await client.get(
        "/exclusions/search", params={"q": "star sampleville pharm"}, headers=HEADERS
    )

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "star sampleville pharm"
    (record,) = body["records"]
    assert record["organization_name"] == "7 STAR SAMPLEVILLE PHARMACY AND DELI INC"
    assert record["npi"] == NPI
    assert record["action"] == "termination"
    assert record["license"] == {"number": "00012345", "state": "NY", "kind": None}
    assert record["place"] == {"city": "Sampleville", "state": "NY"}
    assert record["raw_provider_type"] == "Pharmacy"
    assert record["status"] == "active"
    assert "date_of_birth" not in record
    assert "address" not in record
    assert "1 Sample Way" not in response.text
    assert body["sources"] == [
        {
            "source_id": SOURCE,
            "display_name": "Fake New York List",
            "jurisdictions": ["NY"],
            "list_last_updated": None,
            "fetched_at": (NOW - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
        }
    ]


async def test_search_finds_a_record_removed_from_its_list_with_that_status(
    client: httpx2.AsyncClient,
) -> None:
    body = (
        await client.get("/exclusions/search", params={"q": "sampleremoved"}, headers=HEADERS)
    ).json()

    (record,) = body["records"]
    assert record["person_name"]["last"] == "Sampleremoved"
    assert record["status"] == "removed"


async def test_search_narrowed_to_a_state(client: httpx2.AsyncClient) -> None:
    own = await client.get(
        "/exclusions/search", params={"q": "sampleville", "state": "NY"}, headers=HEADERS
    )
    elsewhere = await client.get(
        "/exclusions/search", params={"q": "sampleville", "state": "wa"}, headers=HEADERS
    )

    assert (own.status_code, elsewhere.status_code) == (200, 200)
    assert own.json()["state"] == "NY"
    assert [record["npi"] for record in own.json()["records"]] == [NPI]
    assert elsewhere.json()["state"] == "WA"
    assert elsewhere.json()["records"] == []
    assert [source["source_id"] for source in elsewhere.json()["sources"]] == [SOURCE]


async def test_search_without_a_state_echoes_null(client: httpx2.AsyncClient) -> None:
    body = (await client.get("/exclusions/search", params={"q": NPI}, headers=HEADERS)).json()

    assert body["state"] is None


@pytest.mark.parametrize("state", ["xyz", "N", "N1", ""])
async def test_search_rejects_a_state_that_is_not_two_letters(
    client: httpx2.AsyncClient, state: str
) -> None:
    response = await client.get(
        "/exclusions/search", params={"q": "sampleville", "state": state}, headers=HEADERS
    )

    assert response.status_code == 422


async def test_search_by_npi(client: httpx2.AsyncClient) -> None:
    body = (await client.get("/exclusions/search", params={"q": NPI}, headers=HEADERS)).json()

    assert [record["npi"] for record in body["records"]] == [NPI]
    assert body["records"][0]["related_npis"] == [OWNER_NPI]
    assert body["records"][0]["related_parties"] == ["Exampleowner, Jordan"]


async def test_a_related_npi_finds_the_row_that_names_it_after_its_own(
    client: httpx2.AsyncClient,
) -> None:
    body = (await client.get("/exclusions/search", params={"q": OWNER_NPI}, headers=HEADERS)).json()

    assert [record["npi"] for record in body["records"]] == [OWNER_NPI, NPI]


async def _pharmacy_id(client: httpx2.AsyncClient) -> str:
    body = (await client.get("/exclusions/search", params={"q": NPI}, headers=HEADERS)).json()
    return str(body["records"][0]["id"])


async def test_a_record_profile(client: httpx2.AsyncClient) -> None:
    record_id = await _pharmacy_id(client)

    response = await client.get(f"/exclusions/records/{record_id}", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["record"]["id"] == record_id
    assert body["record"]["source_name"] == "Fake New York List"
    assert body["record"]["status"] == "active"
    assert body["record"]["raw_provider_type"] == "Pharmacy"
    assert body["record"]["place"] == {"city": "Sampleville", "state": "NY"}
    assert body["source"]["source_url"] == f"https://example.test/{SOURCE}"
    assert body["source"]["freshness"] == "cached"
    assert [v["action"] for v in body["versions"]] == ["termination"]
    (version,) = body["versions"]
    assert version["rewritten"] is False
    assert version["name"] == "7 STAR SAMPLEVILLE PHARMACY AND DELI INC"
    assert (version["npi"], version["license_number"], version["license_state"]) == (
        NPI,
        "00012345",
        "NY",
    )
    assert body["rewritten_by"] is None
    (owner,) = body["related"]
    assert owner["link"] == "listed_as"
    assert owner["via_npi"] == OWNER_NPI
    assert owner["raw_provider_type"] == "Pharmacist"
    assert body["same_party"] == body["same_name"] == []
    for hidden in ('"date_of_birth"', '"address"', '"raw"', "1 Sample Way", "1970-05-17"):
        assert hidden not in response.text


async def test_a_record_profile_across_a_rewrite_by_the_list() -> None:
    registry = ConnectorRegistry()
    registry.register(ScriptedConnector(capabilities(SOURCE, frozenset({"NY"}))))
    snapshots, records = InMemorySnapshots(), InMemoryRecords()
    npi = make_npi("100000003")
    before = exclusion(
        source_id=SOURCE,
        last="Samplespeling",
        npi=npi,
        date_of_birth=date(1970, 5, 17),
        valid_from=NOW - timedelta(days=7),
    )
    after = exclusion(
        source_id=SOURCE, last="Samplespelling", npi=npi, date_of_birth=date(1970, 5, 17)
    )
    for record in (before, after):
        await records.replace_current(
            SOURCE, record.snapshot_id, [record], record.valid_from, source_changed=True
        )
    container = replace(
        _container(),
        record_profile=RecordProfileQuery(
            records, snapshots, registry, DEFAULT_FRESHNESS, FixedClock(NOW)
        ),
    )

    async for client in _client(container):
        old = await client.get(f"/exclusions/records/{before.id}", headers=HEADERS)
        new = await client.get(f"/exclusions/records/{after.id}", headers=HEADERS)

    assert old.status_code == new.status_code == 200
    assert old.json()["rewritten_by"] == str(after.id)
    assert old.json()["record"]["status"] == "active"
    assert new.json()["rewritten_by"] is None
    for response in (old, new):
        versions = response.json()["versions"]
        assert [(v["rewritten"], v["name"], v["npi"]) for v in versions] == [
            (False, "Samplespeling, Avery", npi),
            (True, "Samplespelling, Avery", npi),
        ]
        assert "1970-05-17" not in response.text


async def test_an_unknown_record_profile_is_404(client: httpx2.AsyncClient) -> None:
    response = await client.get(f"/exclusions/records/{uuid4()}", headers=HEADERS)

    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


async def test_a_record_profile_needs_the_organization_header(
    client: httpx2.AsyncClient,
) -> None:
    response = await client.get(f"/exclusions/records/{uuid4()}")

    assert response.status_code == 401


async def test_no_match_still_names_the_lists_searched(client: httpx2.AsyncClient) -> None:
    body = (
        await client.get("/exclusions/search", params={"q": "Nobodyperson"}, headers=HEADERS)
    ).json()

    assert body["records"] == []
    assert [source["source_id"] for source in body["sources"]] == [SOURCE]


@pytest.mark.parametrize("q", ["", " ", "x"])
async def test_a_short_query_searches_nothing(client: httpx2.AsyncClient, q: str) -> None:
    body = (await client.get("/exclusions/search", params={"q": q}, headers=HEADERS)).json()

    assert body == {"query": q.strip(), "state": None, "records": [], "sources": []}


@pytest.mark.parametrize("query", ["limit=0", "limit=51", f"q={'a' * 201}"])
async def test_out_of_range_parameters_are_422(client: httpx2.AsyncClient, query: str) -> None:
    response = await client.get(f"/exclusions/search?{query}", headers=HEADERS)

    assert response.status_code == 422


async def test_the_organization_header_is_required(client: httpx2.AsyncClient) -> None:
    response = await client.get("/exclusions/search", params={"q": NPI})

    assert response.status_code == 401
