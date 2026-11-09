"""The NPI mirror routes and the profile's NPI links over in-memory storage: fast, no database."""

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import date, timedelta
from uuid import uuid4

import httpx2
import pytest

from credentialing.application.link_npis import LinkNpis
from credentialing.application.npi_lookup import NpiLookup
from credentialing.application.record_profile import RecordProfileQuery
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import DatasetLoadMode, EntityKind, NpiStatus, Outcome
from credentialing.domain.models import (
    Address,
    AuthorizedOfficial,
    DatasetLoad,
    NpiAnswer,
    NpiTaxonomy,
    PersonName,
    SourceSnapshot,
)
from credentialing.domain.policies import DEFAULT_FRESHNESS
from tests.api.test_providers import HEADERS, _client, _container
from tests.fakes import (
    FixedClock,
    InMemoryNpiLinks,
    InMemoryNpiRegistry,
    InMemoryRecords,
    InMemorySnapshots,
    ScriptedConnector,
    ScriptedDatasetConnector,
    ScriptedNpiRegistryConnector,
    api_failure,
    api_found,
    api_no_result,
    capabilities,
    dataset_capabilities,
)
from tests.synthetic import NOW, exclusion, make_npi, npi_organization, npi_person

pytestmark = pytest.mark.anyio

MONTHLY = "fake_monthly"
LIST = "fake_state"
PERSON_NPI = make_npi("140000021")
ORGANIZATION_NPI = make_npi("140000022")
STREET = "100 Sample Way"
MAILING = "PO Box 9"
PLACE = Address(line1=STREET, city="SAMPLEVILLE", state="NY", postal_code="999010000")


def _mirror() -> InMemoryNpiRegistry:
    person = npi_person(
        PERSON_NPI,
        practice=PLACE,
        mailing=Address(line1=MAILING, city="SAMPLEVILLE", state="NY", postal_code="99901"),
        licenses=(("SAMPLE-1", "NY"),),
    ).model_copy(update={"credential": "M.D.", "enumeration_date": date(2007, 5, 23)})
    business = npi_organization(
        ORGANIZATION_NPI, other_names=("SAMPLEVILLE CLINIC",), practice=PLACE
    ).model_copy(
        update={
            "authorized_official": AuthorizedOfficial(
                name=PersonName(last="EXAMPLE", first="JORDAN"), title="ADMINISTRATOR"
            ),
            "taxonomies": (NpiTaxonomy(code="261QM1300X", primary=True),),
        }
    )
    return InMemoryNpiRegistry(person, business)


class _World:
    def __init__(self, *answers: NpiAnswer) -> None:
        self.directory = ConnectorRegistry()
        self.directory.register(ScriptedConnector(capabilities(LIST, frozenset({"NY"}))))
        self.directory.register(ScriptedDatasetConnector(dataset_capabilities(MONTHLY)))
        self.live = ScriptedNpiRegistryConnector(*answers)
        if answers:
            self.directory.register(self.live)
        self.snapshots = InMemorySnapshots()
        self.records = InMemoryRecords()
        self.registry = _mirror()
        self.links = InMemoryNpiLinks(self.records, self.registry)
        clock = FixedClock(NOW)
        self.container = replace(
            _container(),
            npi_lookup=NpiLookup(
                self.registry, self.directory, self.snapshots, DEFAULT_FRESHNESS, clock
            ),
            record_profile=RecordProfileQuery(
                self.records,
                self.snapshots,
                self.directory,
                DEFAULT_FRESHNESS,
                clock,
                npi_links=self.links,
                npi_registry=self.registry,
            ),
        )

    async def load_mirror(self) -> SourceSnapshot:
        snapshot = SourceSnapshot(
            id=uuid4(),
            source_id=MONTHLY,
            fetched_at=NOW - timedelta(days=1),
            outcome=Outcome.COMPLETED,
            source_url="https://example.test/nppes/monthly.zip",
            last_modified=NOW - timedelta(days=4),
            load=DatasetLoad(mode=DatasetLoadMode.FULL, scanned=2, added=2),
        )
        await self.snapshots.add(snapshot)
        return snapshot


@pytest.fixture
async def world() -> _World:
    world = _World()
    await world.load_mirror()
    return world


@pytest.fixture
async def client(world: _World) -> AsyncIterator[httpx2.AsyncClient]:
    async for client in _client(world.container):
        yield client


async def test_an_npi_with_its_mirror_provenance_and_no_address(
    client: httpx2.AsyncClient,
) -> None:
    response = await client.get(f"/npi/{PERSON_NPI}", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert (body["npi"], body["status"], body["live"]) == (PERSON_NPI, "active", None)
    record = body["record"]
    assert record["npi"] == PERSON_NPI
    assert record["entity_kind"] == "individual"
    assert record["status"] == "active"
    assert record["person_name"]["last"] == "TESTPERSON"
    assert record["credential"] == "M.D."
    assert record["primary_taxonomy"] == "207Q00000X"
    assert (record["practice_city"], record["practice_state"]) == ("SAMPLEVILLE", "NY")
    assert record["taxonomies"] == [
        {
            "code": "207Q00000X",
            "primary": True,
            "license_number": "SAMPLE-1",
            "license_state": "NY",
            "group": None,
        }
    ]
    assert record["in_file"] is True
    assert body["mirror"]["provenance"]["source_id"] == MONTHLY
    assert body["mirror"]["provenance"]["method"] == "bulk_dataset"
    assert body["mirror"]["provenance"]["source_updated_at"].startswith("2026-01-11")
    assert body["mirror"]["freshness"] == "cached"
    for hidden in (STREET, MAILING, "99901", "mailing_address", "practice_address"):
        assert hidden not in response.text


async def test_an_organization_shows_its_official_and_other_names(
    client: httpx2.AsyncClient,
) -> None:
    record = (await client.get(f"/npi/{ORGANIZATION_NPI}", headers=HEADERS)).json()["record"]

    assert record["organization_name"] == "SAMPLEVILLE CARE GROUP LLC"
    assert record["other_names"] == ["SAMPLEVILLE CLINIC"]
    assert record["authorized_official"] == {
        "name": {"last": "EXAMPLE", "first": "JORDAN", "middle": None, "suffix": None},
        "title": "ADMINISTRATOR",
    }


async def test_an_npi_nobody_could_check_is_200_unknown_and_a_malformed_one_422(
    client: httpx2.AsyncClient,
) -> None:
    unknown = await client.get(f"/npi/{make_npi('140000099')}", headers=HEADERS)
    malformed = await client.get("/npi/12345", headers=HEADERS)

    assert unknown.status_code == 200
    assert unknown.json() == {
        "npi": make_npi("140000099"),
        "status": "unknown",
        "record": None,
        "mirror": unknown.json()["mirror"],
        "live": None,
    }
    assert malformed.status_code == 422


async def test_a_live_lookup_has_its_own_block_beside_the_mirror() -> None:
    answer = api_found(
        npi_person(PERSON_NPI).model_copy(
            update={"last_updated": date(2026, 1, 14), "line_hash": "api:" + "e" * 64}
        ),
        fetched_at=NOW,
    )
    world = _World(answer)
    await world.load_mirror()

    async for client in _client(world.container):
        response = await client.get(f"/npi/{PERSON_NPI}?live=true", headers=HEADERS)

    body = response.json()
    assert (response.status_code, body["status"]) == (200, "active")
    assert body["record"]["last_updated"] == "2026-01-14"
    assert body["live"]["outcome"] == "completed"
    assert body["live"]["found"] is True
    assert body["live"]["freshness"] == "live"
    assert body["live"]["provenance"]["source_id"] == "nppes_api"
    assert body["live"]["provenance"]["method"] == "official_api"
    assert body["mirror"]["freshness"] == "cached"
    assert world.live.lookups == [PERSON_NPI]
    for hidden in (STREET, MAILING):
        assert hidden not in response.text


async def test_not_found_live_and_a_failed_request_are_both_200_with_their_status() -> None:
    missing = make_npi("140000098")
    not_found = _World(api_no_result(fetched_at=NOW))
    failed = _World(api_failure(Outcome.SOURCE_UNAVAILABLE, fetched_at=NOW))
    for world in (not_found, failed):
        await world.load_mirror()

    async for client in _client(not_found.container):
        gone = (await client.get(f"/npi/{missing}", headers=HEADERS)).json()
    async for client in _client(failed.container):
        down = await client.get(f"/npi/{missing}", headers=HEADERS)

    assert (gone["status"], gone["live"]["found"]) == ("not_found", False)
    assert down.status_code == 200
    assert down.json()["status"] == "unknown"
    assert down.json()["live"]["outcome"] == "source_unavailable"
    assert down.json()["live"]["found"] is None


async def test_the_npi_routes_need_the_organization_header(client: httpx2.AsyncClient) -> None:
    assert (await client.get(f"/npi/{PERSON_NPI}")).status_code == 401
    assert (await client.get("/npi/search", params={"q": "Testperson"})).status_code == 401


async def test_a_search_returns_compact_records_and_the_mirror(client: httpx2.AsyncClient) -> None:
    response = await client.get(
        "/npi/search", params={"q": "Testperson Avery", "limit": 5}, headers=HEADERS
    )

    assert response.status_code == 200
    body = response.json()
    assert body["query_text"] == "Testperson Avery"
    assert (body["query"], body["live"]) == (None, None)
    assert [record["npi"] for record in body["records"]] == [PERSON_NPI]
    assert "taxonomies" not in body["records"][0]
    assert body["mirror"]["provenance"]["source_id"] == MONTHLY
    assert STREET not in response.text


@pytest.mark.parametrize("query", ["limit=0", "limit=201", f"q={'a' * 201}", "state=NEW"])
async def test_out_of_range_search_parameters_are_422(
    client: httpx2.AsyncClient, query: str
) -> None:
    assert (await client.get(f"/npi/search?{query}", headers=HEADERS)).status_code == 422


async def test_a_live_search_sends_the_structured_query_and_ignores_q() -> None:
    world = _World(api_found(npi_person(make_npi("140000031")), fetched_at=NOW))
    await world.load_mirror()

    async for client in _client(world.container):
        response = await client.get(
            "/npi/search",
            params={
                "live": "true",
                "q": "ignored",
                "last_name": "Testperson",
                "entity_kind": "individual",
                "state": "NY",
            },
            headers=HEADERS,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["query"]["last_name"] == "Testperson"
    assert body["query"]["entity_kind"] == "individual"
    assert body["query_text"] == ""
    assert [record["npi"] for record in body["records"]] == [make_npi("140000031")]
    assert body["live"]["found"] is True
    ((query, limit),) = world.live.searches
    assert (query.last_name, query.state, limit) == ("Testperson", "NY", 50)


@pytest.mark.parametrize(
    "params",
    [
        {"first_name": "Avery"},
        {"last_name": "T*"},
        {"last_name": "Testperson", "state": "ny"},
        {"organization_name": "Care", "entity_kind": "unknown"},
    ],
)
async def test_a_live_query_the_registry_would_refuse_is_422(params: dict[str, str]) -> None:
    world = _World(api_no_result(fetched_at=NOW))

    async for client in _client(world.container):
        response = await client.get(
            "/npi/search", params={"live": "true", **params}, headers=HEADERS
        )

    assert response.status_code == 422
    assert world.live.searches == []


async def test_a_failed_live_search_is_200_without_records() -> None:
    world = _World(api_failure(Outcome.BLOCKED, fetched_at=NOW, note="HTTP 429"))

    async for client in _client(world.container):
        response = await client.get(
            "/npi/search", params={"live": "true", "last_name": "Testperson"}, headers=HEADERS
        )

    assert response.status_code == 200
    assert response.json()["records"] == []
    assert response.json()["live"]["outcome"] == "blocked"


async def test_a_record_profile_lists_its_npi_links(world: _World) -> None:
    listed = exclusion(source_id=LIST).model_copy(
        update={"address": Address(line1="1 Listed Way", city="Sampleville", state="NY")}
    )
    snapshot = SourceSnapshot(
        id=uuid4(),
        source_id=LIST,
        fetched_at=NOW,
        outcome=Outcome.COMPLETED,
        source_url="https://example.test/list.csv",
    )
    await world.snapshots.add(snapshot)
    listed = listed.model_copy(update={"snapshot_id": snapshot.id})
    await world.records.replace_current(LIST, snapshot.id, [listed], NOW, source_changed=True)
    await LinkNpis(world.directory, world.snapshots, world.links, FixedClock(NOW)).run(
        source_id=None, trigger="cli"
    )

    async for client in _client(world.container):
        response = await client.get(f"/exclusions/records/{listed.id}", headers=HEADERS)

    assert response.status_code == 200
    (link,) = response.json()["npi_links"]
    assert link["npi"] == PERSON_NPI
    assert link["level"] == "potential"
    assert {"field": "address", "matched": True, "detail": "same city"} in link["evidence"]
    assert link["candidates"] == 1
    assert link["registry"]["npi"] == PERSON_NPI
    assert link["registry"]["entity_kind"] == EntityKind.INDIVIDUAL.value
    assert link["registry"]["status"] == NpiStatus.ACTIVE.value
    for hidden in (STREET, MAILING, "1 Listed Way"):
        assert hidden not in response.text
