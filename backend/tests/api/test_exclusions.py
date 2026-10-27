"""The exclusion lookup over in-memory storage: fast, no database."""

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import httpx2
import pytest

from credentialing.application.lookup_exclusions import LookupExclusions
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import EntityKind, ExclusionAction, Outcome
from credentialing.domain.models import Address, License, SourceSnapshot
from tests.api.test_providers import HEADERS, _client, _container
from tests.fakes import InMemoryRecords, InMemorySnapshots, ScriptedConnector, capabilities
from tests.synthetic import NOW, exclusion, make_npi

pytestmark = pytest.mark.anyio

SOURCE = "fake_state"
NPI = make_npi("100000001")


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
        license=License(number="00012345", state="NY"),
        action=ExclusionAction.TERMINATION,
        raw={"PROVIDER_TYPE": "Pharmacy"},
    ).model_copy(update={"address": Address(line1="1 Sample Way", city="Sampleville", state="NY")})
    await records.replace_current(SOURCE, snapshot.id, [listed], NOW)
    container = replace(
        _container(), lookup_exclusions=LookupExclusions(records, snapshots, registry)
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
    assert "date_of_birth" not in record
    assert "address" not in record
    assert "1 Sample Way" not in response.text
    assert body["sources"] == [
        {
            "source_id": SOURCE,
            "display_name": "Fake New York List",
            "list_last_updated": None,
            "fetched_at": (NOW - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
        }
    ]


async def test_search_by_npi(client: httpx2.AsyncClient) -> None:
    body = (await client.get("/exclusions/search", params={"q": NPI}, headers=HEADERS)).json()

    assert [record["npi"] for record in body["records"]] == [NPI]


async def test_no_match_still_names_the_lists_searched(client: httpx2.AsyncClient) -> None:
    body = (
        await client.get("/exclusions/search", params={"q": "Nobodyperson"}, headers=HEADERS)
    ).json()

    assert body["records"] == []
    assert [source["source_id"] for source in body["sources"]] == [SOURCE]


@pytest.mark.parametrize("q", ["", " ", "x"])
async def test_a_short_query_searches_nothing(client: httpx2.AsyncClient, q: str) -> None:
    body = (await client.get("/exclusions/search", params={"q": q}, headers=HEADERS)).json()

    assert body == {"query": q.strip(), "records": [], "sources": []}


@pytest.mark.parametrize("query", ["limit=0", "limit=51", f"q={'a' * 201}"])
async def test_out_of_range_parameters_are_422(client: httpx2.AsyncClient, query: str) -> None:
    response = await client.get(f"/exclusions/search?{query}", headers=HEADERS)

    assert response.status_code == 422


async def test_the_organization_header_is_required(client: httpx2.AsyncClient) -> None:
    response = await client.get("/exclusions/search", params={"q": NPI})

    assert response.status_code == 401
