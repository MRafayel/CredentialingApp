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
from credentialing.domain.enums import Outcome
from credentialing.domain.models import FetchResult, SourceSnapshot
from credentialing.infrastructure.config import Settings
from credentialing.infrastructure.database import ConnectionSource
from tests.fakes import FixedClock, ScriptedConnector, capabilities
from tests.synthetic import NOW, exclusion

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SOURCE = "test_federal_list"


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
    registry = ConnectorRegistry()
    registry.register(
        ScriptedConnector(
            capabilities(SOURCE, display_name="Test Federal List"),
            FetchResult(snapshot=snapshot, records=(listed,)),
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
    (result,) = checked.json()
    assert result["source_id"] == SOURCE
    assert result["outcome"] == "completed"
    assert result["finding"] == "ambiguous"
    assert result["match"]["level"] == "potential"
    assert result["freshness"] in {"live", "cached"}
    assert result["provenance"]["source_url"] == f"https://example.test/{SOURCE}"
    (record,) = result["matched_records"]
    assert record["person_name"]["last"] == "Deltaperson"
    assert "date_of_birth" not in record
    assert "address" not in record

    history = await client.get(f"/providers/{provider['id']}/verifications", headers=headers)
    assert history.status_code == 200
    assert history.json() == [result]


async def test_unknown_provider_is_404(client: httpx2.AsyncClient, organization_id: UUID) -> None:
    headers = {"X-Organization-Id": str(organization_id)}

    response = await client.post(f"/providers/{uuid4()}/verifications/exclusions", headers=headers)

    assert response.status_code == 404
