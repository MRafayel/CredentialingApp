"""The HTTP API end to end against the local database, rolled back after each test."""

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import httpx2
import pytest
from psycopg import AsyncConnection
from psycopg.rows import TupleRow

from credentialing.api.app import create_app
from credentialing.bootstrap import api_container, build_services
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import EntityKind, Outcome
from credentialing.domain.models import FetchResult, SourceSnapshot
from credentialing.infrastructure.config import Settings
from credentialing.infrastructure.database import ConnectionSource
from tests.fakes import FixedClock, ScriptedConnector, capabilities
from tests.synthetic import NOW, exclusion, make_npi

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SOURCE = "test_federal_list"
OWNER_NPI = make_npi("100000011")


@pytest.fixture
async def organization_id(db_connection: AsyncConnection[TupleRow]) -> UUID:
    organization_id = uuid4()
    await db_connection.execute(
        t"insert into organizations (id, name) values ({organization_id}, 'Testorg Clinic')"
    )
    return organization_id


@pytest.fixture
async def client(database: ConnectionSource) -> AsyncIterator[httpx2.AsyncClient]:
    snapshot = SourceSnapshot(
        id=uuid4(),
        source_id=SOURCE,
        fetched_at=NOW,
        outcome=Outcome.COMPLETED,
        source_url="https://example.test/list.csv",
        content_hash="ab" * 32,
    )
    listed = exclusion(source_id=SOURCE, snapshot_id=snapshot.id, last="Deltaperson", first="Avery")
    business = exclusion(
        source_id=SOURCE,
        snapshot_id=snapshot.id,
        entity_kind=EntityKind.ORGANIZATION,
        last=None,
        organization_name="Sample Echo Clinic",
        related_parties=("Echoperson, Blake",),
        npi=make_npi("100000012"),
        related_npis=(OWNER_NPI,),
    )
    registry = ConnectorRegistry()
    registry.register(
        ScriptedConnector(
            capabilities(SOURCE, display_name="Test Federal List"),
            FetchResult(snapshot=snapshot, records=(listed, business)),
        )
    )
    services = build_services(database, registry, FixedClock(NOW))
    await services.sync_source.run(SOURCE)
    app = create_app(expose_docs=False)
    app.state.container = api_container(services, Settings(environment="local"))
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_register_check_and_read_history(
    client: httpx2.AsyncClient, organization_id: UUID
) -> None:
    headers = {"X-Organization-Id": str(organization_id)}
    body = {
        "entity_kind": "individual",
        "person_name": {"last": "Deltaperson", "first": "Avery"},
        "licenses": [{"number": "123456", "state": "NY"}],
    }
    provider = (await client.post("/providers", json=body, headers=headers)).json()

    checked = await client.post(
        f"/providers/{provider['id']}/verifications/exclusions", headers=headers
    )

    assert checked.status_code == 200
    verdict = checked.json()
    assert verdict["verdict"] == "needs_review"
    assert verdict["review_in"] == [SOURCE]
    assert verdict["listed_in"] == verdict["flagged_in"] == verdict["unchecked"] == []
    assert verdict["sources_checked"] == 1
    assert verdict["oldest_list_updated_at"] == NOW.isoformat().replace("+00:00", "Z")
    (result,) = verdict["results"]
    assert verdict["last_checked_at"] == result["created_at"]
    assert result["source_id"] == SOURCE
    assert result["outcome"] == "completed"
    assert result["finding"] == "ambiguous"
    assert result["match"]["level"] == "potential"
    assert result["match"]["relation"] == "party"
    assert result["freshness"] in {"live", "cached"}
    assert result["provenance"]["source_url"] == f"https://example.test/{SOURCE}"
    (record,) = result["matched_records"]
    assert record["person_name"]["last"] == "Deltaperson"
    assert "date_of_birth" not in record
    assert "address" not in record

    history = await client.get(f"/providers/{provider['id']}/verifications", headers=headers)
    assert history.status_code == 200
    assert history.json() == [result]

    read = await client.get(f"/providers/{provider['id']}/verdict", headers=headers)
    assert read.status_code == 200
    assert read.json() == verdict


async def test_a_related_party_is_flagged(
    client: httpx2.AsyncClient, organization_id: UUID
) -> None:
    headers = {"X-Organization-Id": str(organization_id)}
    body = {
        "entity_kind": "individual",
        "person_name": {"last": "Echoperson", "first": "Blake"},
        "npi": OWNER_NPI,
    }
    provider = (await client.post("/providers", json=body, headers=headers)).json()

    checked = await client.post(
        f"/providers/{provider['id']}/verifications/exclusions", headers=headers
    )

    verdict = checked.json()
    assert verdict["verdict"] == "flagged"
    assert verdict["flagged_in"] == [SOURCE]
    assert verdict["listed_in"] == verdict["review_in"] == []
    (result,) = verdict["results"]
    assert result["finding"] == "related_party"
    assert result["match"]["level"] == "high"
    assert result["match"]["relation"] == "related_party"
    assert result["summary"].startswith("Flagged in Test Federal List: named with a listed party")


async def test_unknown_provider_is_404(client: httpx2.AsyncClient, organization_id: UUID) -> None:
    headers = {"X-Organization-Id": str(organization_id)}

    response = await client.post(f"/providers/{uuid4()}/verifications/exclusions", headers=headers)

    assert response.status_code == 404


async def test_exclusion_lookup_reads_the_stored_list(
    client: httpx2.AsyncClient, organization_id: UUID
) -> None:
    headers = {"X-Organization-Id": str(organization_id)}

    response = await client.get(
        "/exclusions/search", params={"q": "deltaperson av"}, headers=headers
    )

    assert response.status_code == 200
    body = response.json()
    ours = [record for record in body["records"] if record["source_id"] == SOURCE]
    assert [record["person_name"]["last"] for record in ours] == ["Deltaperson"]
    assert "date_of_birth" not in ours[0]
    (source,) = body["sources"]
    assert source["source_id"] == SOURCE
    assert source["fetched_at"] == NOW.isoformat().replace("+00:00", "Z")
